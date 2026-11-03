"""Direct curve-on-ridge Doppler locator (no detector).

For every grid point g, aircraft a and shared stream latency L, the emission-time-corrected
Doppler ratio c/(c+v_r) predicts a curve in a log-frequency spectrogram. The score is the best
(over the unknown emitted frequency f0, with x2/x3 harmonics) mean whitened energy along that
curve, minus the same aircraft scored with ratio == 1 (a flat line over the same frames).
Positive excess over the per-aircraft grid median is summed over aircraft; the answer is the
argmax over (g, L). Inputs are only cases/<case>/case.json and adsb.jsonl.

v1.1: track samples count only when airborne (not "ground", gs >= 50 kt, >= 30 m above the DEM
ground), and a (g, a, L) curve is scored only if its predicted Doppler ratio swings by at least
~0.5 % across the scored frames, so flat curves carry no weight.

v1.2: latency step 0.5 s and STFT hop <= 0.1 s when the band reaches above 1 kHz; a dense 100 m
sub-grid within 1.5 km of every airborne track sample below 600 m AGL inside the bbox; refinement of
the top 25 peaks plus every local maximum within 30 % of the best.

v1.3: each curve is scored only over max(8 s, 4 d_cpa / v) centred on its predicted CPA receive
time; airborne needs >= 50 m above ground; latency scan -10..+90 s, flagged when the best L is at a bound.

    python solver_ridge/ridge.py cases/case_E --out results/case_E_ridge
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numba as nb
import numpy as np
import soundfile as sf
from scipy.interpolate import CubicHermiteSpline
from scipy.ndimage import binary_dilation, maximum_filter1d

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "data" / "vendor" / "doppler-locate"
FT = 0.3048
KT = 0.514444
R_EARTH = 6371008.8
C_AIR = 331.3 * math.sqrt(1 + 15.0 / 273.15)  # ~15 C air, 340.3 m/s
SENT = -32768
MIC_AGL = 5.0  # assumed microphone height above the DEM terrain, m


# ---------------------------------------------------------------- spectrogram

def whitened_spectrogram(x, fs, fmin, fmax, nperseg, hop, dlog):
    """Log-frequency, whitened spectrogram W[frame, bin] and the frame centre times (s)."""
    win = np.hanning(nperseg).astype(np.float32)
    n = 1 + (len(x) - nperseg) // hop
    flin = np.fft.rfftfreq(nperseg, 1 / fs)
    K = int(round(math.log(fmax / fmin) / dlog)) + 1
    fk = fmin * np.exp(dlog * np.arange(K))
    lo = fk * math.exp(-dlog / 2)
    hi = fk * math.exp(dlog / 2)
    out = np.empty((n, K), np.float32)
    for c0 in range(0, n, 2000):  # chunks keep long files in memory
        c1 = min(n, c0 + 2000)
        idx = np.arange(nperseg)[None, :] + hop * np.arange(c0, c1)[:, None]
        P = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        lp = 10 * np.log10(P + 1e-12).astype(np.float32)
        for k in range(K):
            sel = np.nonzero((flin >= lo[k]) & (flin < hi[k]))[0]
            if len(sel):
                out[c0:c1, k] = lp[:, sel].max(axis=1)
            else:  # bin narrower than the FFT resolution: interpolate
                j = np.searchsorted(flin, fk[k]) - 1
                w = (fk[k] - flin[j]) / (flin[j + 1] - flin[j])
                out[c0:c1, k] = (1 - w) * lp[:, j] + w * lp[:, j + 1]
    out -= np.median(out, axis=0, keepdims=True)  # per-frequency median over time
    med = np.median(out, axis=1, keepdims=True)
    mad = np.median(np.abs(out - med), axis=1, keepdims=True) * 1.4826 + 1e-6
    W = np.clip((out - med) / mad, -3, 6)  # per-frame normalisation
    W = maximum_filter1d(W, 3, axis=1)  # +-1 bin tolerance for model error
    W -= W.mean()
    tau = (np.arange(n) * hop + nperseg / 2) / fs
    return W.astype(np.float32), tau, fk


# ---------------------------------------------------------------- geometry / terrain

def to_xy(lat, lon, lat0, lon0):
    x = np.radians(np.asarray(lon) - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = np.radians(np.asarray(lat) - lat0) * R_EARTH
    return x, y


def to_ll(x, y, lat0, lon0):
    lat = lat0 + np.degrees(np.asarray(y) / R_EARTH)
    lon = lon0 + np.degrees(np.asarray(x) / (R_EARTH * math.cos(math.radians(lat0))))
    return lat, lon


class Terrain:
    """Copernicus GLO-90 heights (EGM2008 ~ orthometric) and the EGM96 geoid, from the
    doppler-locate cache. Falls back to a constant ground when unavailable."""

    def __init__(self):
        self.ok = False
        self.note = ""
        try:
            sys.path.insert(0, str(VENDOR / "src"))
            from dopploc.dem import DEM
            self.dem = DEM(VENDOR / "data" / "cache" / "dem", res=90)
            self.ok = True
            self.note = "Copernicus GLO-90 DEM"
        except Exception as e:  # pragma: no cover
            self.note = f"DEM unavailable ({e}); constant ground"

    def elevation(self, lat, lon):
        lat = np.asarray(lat, float)
        if not self.ok:
            return np.zeros(lat.shape)
        return self.dem.elevation(lat, np.asarray(lon, float))

    def geoid(self, lat, lon):
        try:
            import rasterio
            with rasterio.open(VENDOR / "data" / "cache" / "us_nga_egm96_15.tif") as ds:
                return float(next(ds.sample([(lon, lat)]))[0])
        except Exception:
            return 0.0


# ---------------------------------------------------------------- ADS-B

def load_tracks(path, t_lo, t_hi):
    """Per hex: sorted, de-duplicated position reports (position time = t - seen_pos)."""
    raw = {}
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            for a in d["ac"]:
                lat, lon = a.get("lat"), a.get("lon")
                if lat is None or lon is None:
                    continue
                tp = d["t"] - float(a.get("seen_pos") or 0.0)
                if not (t_lo <= tp <= t_hi):
                    continue
                ab = a.get("alt_baro")
                ground = ab == "ground"
                raw.setdefault(a["hex"], []).append((
                    tp, lat, lon, ground,
                    np.nan if (ground or ab is None) else float(ab),
                    np.nan if a.get("alt_geom") is None else float(a["alt_geom"]),
                    np.nan if a.get("gs") is None else float(a["gs"]),
                    np.nan if a.get("track") is None else float(a["track"]),
                    (a.get("flight") or "").strip(), a.get("t") or ""))
    tracks = {}
    for hx, rows in raw.items():
        rows.sort(key=lambda r: r[0])
        keep = []
        for r in rows:
            if keep and (abs(r[0] - keep[-1][0]) < 0.05 or (r[1] == keep[-1][1] and r[2] == keep[-1][2])):
                continue
            keep.append(r)
        if len(keep) < 2:
            continue
        a = np.array([r[:8] for r in keep], float)
        flights = [r[8] for r in keep if r[8]]
        types = [r[9] for r in keep if r[9]]
        tracks[hx] = dict(t=a[:, 0], lat=a[:, 1], lon=a[:, 2], ground=a[:, 3] > 0, baro=a[:, 4],
                          geom=a[:, 5], gs=a[:, 6], trk=a[:, 7],
                          flight=max(set(flights), key=flights.count) if flights else "",
                          type=max(set(types), key=types.count) if types else "")
    return tracks


def track_heights(tr, terrain, geoid_n, global_off):
    """Orthometric height (m) of each report: alt_geom (HAE) - N; baro + per-hex geom-baro
    offset when geom is missing; DEM + 3 m on the ground."""
    h = np.full(len(tr["t"]), np.nan)
    both = np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])
    off = np.median(tr["geom"][both] - tr["baro"][both]) if both.any() else global_off
    g = np.isfinite(tr["geom"]) & ~tr["ground"]
    h[g] = tr["geom"][g] * FT - geoid_n
    b = ~g & np.isfinite(tr["baro"]) & ~tr["ground"]
    h[b] = (tr["baro"][b] + off) * FT - geoid_n
    gr = tr["ground"] | ~np.isfinite(h)
    if gr.any():
        h[gr] = terrain.elevation(tr["lat"][gr], tr["lon"][gr]) + 3.0
    return h


def airborne_reports(tr, h, terrain, min_gs_kt=50.0, min_agl=50.0):
    """v1.1: a report counts as airborne only if not 'ground', gs >= 50 kt (finite-difference speed
    when gs is missing) and >= 50 m (v1.3; 30 m in v1.1) above the local DEM ground."""
    x, y = to_xy(tr["lat"], tr["lon"], tr["lat"][0], tr["lon"][0])
    fd = np.hypot(np.gradient(x, tr["t"]), np.gradient(y, tr["t"])) / KT
    gs = np.where(np.isfinite(tr["gs"]), tr["gs"], fd)
    agl = h - terrain.elevation(tr["lat"], tr["lon"])
    return ~tr["ground"] & (gs >= min_gs_kt) & np.isfinite(h) & (agl >= min_agl)


def interp_track(tr, h, te, lat0, lon0, max_gap=45.0, air=None):
    """Cubic Hermite x/y with the reported ground velocity, linear height; NaN across gaps
    and (v1.1) on any segment that touches a non-airborne report."""
    x, y = to_xy(tr["lat"], tr["lon"], lat0, lon0)
    t = tr["t"]
    vx = tr["gs"] * KT * np.sin(np.radians(tr["trk"]))
    vy = tr["gs"] * KT * np.cos(np.radians(tr["trk"]))
    fdx = np.gradient(x, t)
    fdy = np.gradient(y, t)
    bad = ~np.isfinite(vx) | ~np.isfinite(vy)
    vx[bad], vy[bad] = fdx[bad], fdy[bad]
    X = CubicHermiteSpline(t, x, vx)(te)
    Y = CubicHermiteSpline(t, y, vy)(te)
    Z = np.interp(te, t, h)
    seg = np.searchsorted(t, te) - 1
    ok = (seg >= 0) & (seg < len(t) - 1)
    segc = np.clip(seg, 0, len(t) - 2)
    ok &= (t[segc + 1] - t[segc]) <= max_gap
    if air is not None:
        ok &= air[segc] & air[segc + 1]
    X[~ok] = np.nan
    Y[~ok] = np.nan
    Z[~ok] = np.nan
    return X, Y, Z


# ---------------------------------------------------------------- numba kernels

@nb.njit(parallel=True, cache=True)
def curves_kernel(gx, gy, gz, te, ax, ay, az, c, tj0, hop, nj, dlog, rmax, pad, cpa_min, cpa_k,
                  d_out, j0_out, j1_out, rmin_out, temin_out):
    G = gx.shape[0]
    N = te.shape[0]
    dt = te[1] - te[0]
    for g in nb.prange(G):
        r = np.empty(N)
        rmin = 1e30
        temin = np.nan
        nc = -1
        for n in range(N):
            if np.isfinite(ax[n]):
                r[n] = math.sqrt((ax[n] - gx[g]) ** 2 + (ay[n] - gy[g]) ** 2 + (az[n] - gz[g]) ** 2)
                if r[n] < rmin:
                    rmin = r[n]
                    temin = te[n]
                    nc = n
            else:
                r[n] = np.nan
        # v1.3: score only max(cpa_min, cpa_k * d_cpa / v) centred on the CPA receive time
        half = 1e30
        if 0 < nc < N - 1 and np.isfinite(ax[nc - 1]) and np.isfinite(ax[nc + 1]):
            v = math.sqrt((ax[nc + 1] - ax[nc - 1]) ** 2 + (ay[nc + 1] - ay[nc - 1]) ** 2
                          + (az[nc + 1] - az[nc - 1]) ** 2) / (2 * dt)
            if v > 1.0:
                half = 0.5 * max(cpa_min, cpa_k * rmin / v)
        tcpa = temin + rmin / c
        rmin_out[g] = rmin
        temin_out[g] = temin
        j0_out[g] = -1
        j1_out[g] = -1
        for j in range(nj):
            d_out[g, j] = SENT
        if rmin > rmax:
            continue
        tr = np.full(N, np.nan)
        lr = np.full(N, np.nan)
        ok = np.zeros(N, np.bool_)
        for n in range(1, N - 1):
            tr[n] = te[n] + r[n] / c
            if np.isfinite(r[n - 1]) and np.isfinite(r[n + 1]) and r[n] <= rmax:
                vr = (r[n + 1] - r[n - 1]) / (2 * dt)
                lr[n] = math.log(c / (c + vr))
                ok[n] = True
        # map receive times onto the real-time frame grid
        n = 1
        for j in range(nj):
            t = tj0 + j * hop
            while n < N - 2 and not (np.isfinite(tr[n + 1]) and tr[n + 1] > t):
                n += 1
            if abs(t - tcpa) > half:
                continue
            if not (ok[n] and ok[n + 1]):
                continue
            if not (tr[n] <= t < tr[n + 1]):
                continue
            w = (t - tr[n]) / (tr[n + 1] - tr[n])
            v = (1 - w) * lr[n] + w * lr[n + 1]
            k = int(round(v / dlog))
            if k > pad:
                k = pad
            if k < -pad:
                k = -pad
            d_out[g, j] = k
            if j0_out[g] < 0:
                j0_out[g] = j
            j1_out[g] = j


@nb.njit(parallel=True, cache=True)
def score_kernel(Wp, Cc, pad, d, j0a, j1a, rowofs, h2, h3, k0lo, k0hi, nmin, flat, min_swing,
                 exc_out, cur_out, k0_out):
    G = d.shape[0]
    T = Wp.shape[0]
    K = Cc.shape[1]
    nL = rowofs.shape[0]
    for g in nb.prange(G):
        j0 = j0a[g]
        j1 = j1a[g]
        S = np.empty(K, np.float32)  # float32 accumulators: speed only
        Nn = np.empty(K)
        for li in range(nL):
            exc_out[g, li] = np.nan
            cur_out[g, li] = np.nan
            k0_out[g, li] = -1
            if j0 < 0:
                continue
            o = rowofs[li]
            for k in range(K):
                S[k] = 0.0
            n = 0
            rlo = T
            rhi = -1
            dlo = 1 << 20
            dhi = -(1 << 20)
            for j in range(j0, j1 + 1):
                row = j + o
                if row < 0 or row >= T:
                    continue
                dj = d[g, j]
                if dj == SENT:
                    continue
                if dj < dlo:
                    dlo = dj
                if dj > dhi:
                    dhi = dj
                if flat:
                    dj = 0
                n += 1
                if row < rlo:
                    rlo = row
                if row > rhi:
                    rhi = row
                b = pad + dj
                for k in range(K):
                    S[k] += Wp[row, b + k]
            if n < nmin:
                continue
            if dhi - dlo < min_swing:  # v1.1: flat curves (no Doppler swing in the scored frames) carry no weight
                continue
            nn = rhi - rlo + 1
            for k in range(K):
                Nn[k] = Cc[rhi + 1, k] - Cc[rlo, k]
            best = -1e30
            bk = -1
            bestn = -1e30
            for k0 in range(k0lo, k0hi + 1):
                s = S[k0]
                z = Nn[k0]
                m = 1
                if k0 + h2 < K:
                    s += S[k0 + h2]
                    z += Nn[k0 + h2]
                    m += 1
                if h3 > 0 and k0 + h3 < K:
                    s += S[k0 + h3]
                    z += Nn[k0 + h3]
                    m += 1
                v = s / (n * math.sqrt(m))
                vn = z / (nn * math.sqrt(m))
                if v > best:
                    best = v
                    bk = k0
                if vn > bestn:
                    bestn = vn
            exc_out[g, li] = best - bestn
            cur_out[g, li] = best
            k0_out[g, li] = bk


# ---------------------------------------------------------------- solver

class Problem:
    def __init__(self, case_dir, a):
        t_start = time.time()
        self.case_dir = Path(case_dir)
        self.case = json.loads((self.case_dir / "case.json").read_text())
        self.name = self.case.get("case", self.case_dir.name)
        self.a = a
        cs = self.case
        self.t0 = datetime.fromisoformat(cs["utc_start"].replace("Z", "+00:00")).timestamp()
        b = cs["bbox"]  # [lat_min, lon_min, lat_max, lon_max]
        self.bbox = b
        self.lat0 = (b[0] + b[2]) / 2
        self.lon0 = (b[1] + b[3]) / 2
        x, fs = sf.read(ROOT / cs["audio"], dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        self.fs = fs
        self.dur = len(x) / fs
        hop_s = a.hop if a.fmax <= 1000 else min(a.hop, 0.1)  # v1.2: fast jet sweeps need <= 0.1 s
        hop = int(round(hop_s * fs))
        nper = int(round(a.win * fs))
        self.W, self.tau, self.fk = whitened_spectrogram(x, fs, a.fmin, a.fmax, nper, hop, a.dlog)
        self.hop_s = hop / fs
        self.T, self.K = self.W.shape
        self.pad = int(math.ceil(math.log(1.45) / a.dlog))
        self.Wp = np.zeros((self.T, self.K + 2 * self.pad), np.float32)
        self.Wp[:, self.pad:self.pad + self.K] = self.W
        self.Cc = np.zeros((self.T + 1, self.K), np.float64)
        self.Cc[1:] = np.cumsum(self.W, axis=0)
        self.h2 = int(round(math.log(2) / a.dlog)) if a.harmonics >= 2 else 10 ** 6
        self.h3 = int(round(math.log(3) / a.dlog)) if a.harmonics >= 3 else -1
        self.k0lo = max(0, int(round(math.log(a.f0min / a.fmin) / a.dlog)))
        self.k0hi = min(self.K - 1, int(round(math.log(a.f0max / a.fmin) / a.dlog)))
        # latency grid (frames); real-time frame j <-> audio row j + (L - Lmax)/hop
        self.Lstep_f = max(1, int(round(a.lstep / self.hop_s)))
        self.Lmax_f = int(math.ceil(a.lmax / self.hop_s))
        self.Lmin_f = int(math.floor(a.lmin / self.hop_s))
        self.nj = self.T + (self.Lmax_f - self.Lmin_f)
        self.tj0 = self.t0 + self.tau[0] - self.Lmax_f * self.hop_s
        print(f"spectrogram {self.W.shape} hop {self.hop_s:.3f}s  ({time.time() - t_start:.1f}s)", flush=True)

        # aircraft
        self.terrain = Terrain()
        self.geoid_n = self.terrain.geoid(self.lat0, self.lon0)
        t_lo = self.t0 + self.tau[0] - a.lmax - 120
        t_hi = self.t0 + self.dur - a.lmin + 60
        tracks = load_tracks(ROOT / cs.get("adsb", str(self.case_dir / "adsb.jsonl")), t_lo, t_hi)
        offs = [np.median(tr["geom"][m] - tr["baro"][m]) for tr in tracks.values()
                for m in [np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])] if m.any()]
        goff = float(np.median(offs)) if offs else 0.0
        self.te = np.arange(self.tj0 - 30.0, self.tj0 + self.nj * self.hop_s + 2.0, min(0.25, self.hop_s))
        # audio span in real time (any latency)
        w_lo = self.t0 - a.lmax - 20
        w_hi = self.t0 + self.dur - a.lmin
        xb, yb = to_xy(np.array([b[0], b[2]]), np.array([b[1], b[3]]), self.lat0, self.lon0)
        self.xb, self.yb = xb, yb
        self.ac = []
        for hx, tr in tracks.items():
            inwin = (tr["t"] >= w_lo - 30) & (tr["t"] <= w_hi + 30)
            if not inwin.any():
                continue
            h = track_heights(tr, self.terrain, self.geoid_n, goff)
            air = airborne_reports(tr, h, self.terrain)
            X, Y, Z = interp_track(tr, h, self.te, self.lat0, self.lon0, air=air)
            sel = (self.te >= w_lo) & (self.te <= w_hi) & np.isfinite(X)
            if not sel.any():
                continue
            near = ((X[sel] > xb[0] - a.near_km * 1e3) & (X[sel] < xb[1] + a.near_km * 1e3)
                    & (Y[sel] > yb[0] - a.near_km * 1e3) & (Y[sel] < yb[1] + a.near_km * 1e3))
            pts = (tr["t"] >= w_lo) & (tr["t"] <= w_hi)
            airborne = pts & air
            if not near.any() or not airborne.any():
                continue
            terr = float(np.median(self.terrain.elevation(tr["lat"][airborne], tr["lon"][airborne])))
            if np.nanmin(h[airborne]) - terr > a.max_alt:
                continue
            self.ac.append(dict(hex=hx, flight=tr["flight"], type=tr["type"], X=X, Y=Y, Z=Z,
                                lat=tr["lat"], lon=tr["lon"], t=tr["t"]))
        print(f"{len(self.ac)} candidate aircraft (geoid N {self.geoid_n:.1f} m, geom-baro {goff:.0f} ft)  "
              f"({time.time() - t_start:.1f}s)", flush=True)

    def grid_heights(self, gx, gy):
        lat, lon = to_ll(gx, gy, self.lat0, self.lon0)
        return self.terrain.elevation(lat, lon) + MIC_AGL

    def score(self, gx, gy, gz, rowofs, flat=False, med=None, chunk=3000):
        """exc, cur, k0 (A, G, nL) plus rmin, temin (A, G) for the given grid and latencies.
        With med (A, 1, nL) given, returns only the summed positive excess (G, nL)."""
        if len(gx) > chunk:
            parts = [self.score(gx[i:i + chunk], gy[i:i + chunk], gz[i:i + chunk], rowofs, flat, med, chunk)
                     for i in range(0, len(gx), chunk)]
            if med is not None:
                return np.concatenate(parts, axis=0)
            return tuple(np.concatenate([q[k] for q in parts], axis=1) for k in range(5))
        A, G, nL = len(self.ac), len(gx), len(rowofs)
        if med is not None:
            tot = np.zeros((G, nL), np.float32)
            A_store = 0
        else:
            A_store = A
        exc = np.full((A_store, G, nL), np.nan, np.float32)
        cur = np.full((A_store, G, nL), np.nan, np.float32)
        k0 = np.full((A_store, G, nL), -1, np.int32)
        rmin = np.full((A, G), np.nan)
        temin = np.full((A, G), np.nan)
        d = np.empty((G, self.nj), np.int16)
        j0 = np.empty(G, np.int64)
        j1 = np.empty(G, np.int64)
        nmin = int(round(self.a.min_window / self.hop_s))
        self.min_swing_bins = max(1, int(round(math.log(1 + self.a.min_swing) / self.a.dlog)))
        for i, ac in enumerate(self.ac):
            rm = np.empty(G)
            tm = np.empty(G)
            curves_kernel(gx, gy, gz, self.te, ac["X"], ac["Y"], ac["Z"], C_AIR, self.tj0, self.hop_s,
                          self.nj, self.a.dlog, self.a.rmax, self.pad, self.a.cpa_min, self.a.cpa_k,
                          d, j0, j1, rm, tm)
            rmin[i], temin[i] = rm, tm
            e = np.empty((G, nL), np.float32)
            c = np.empty((G, nL), np.float32)
            kk = np.empty((G, nL), np.int32)
            score_kernel(self.Wp, self.Cc, self.pad, d, j0, j1, rowofs, self.h2, self.h3,
                         self.k0lo, self.k0hi, nmin, flat, self.min_swing_bins, e, c, kk)
            if med is not None:
                tot += np.nan_to_num(np.maximum(e - med[i], 0.0))
            else:
                exc[i], cur[i], k0[i] = e, c, kk
        if med is not None:
            return tot
        return exc, cur, k0, rmin, temin

    def dense_points(self):
        """v1.2: 100 m lattice points within 1.5 km of airborne track samples below 600 m AGL in the bbox."""
        a = self.a
        xs = np.arange(self.xb[0], self.xb[1] + 1e-6, a.dense)
        ys = np.arange(self.yb[0], self.yb[1] + 1e-6, a.dense)
        mask = np.zeros((len(ys), len(xs)), bool)
        s = (self.te >= self.t0 - a.lmax - 20) & (self.te <= self.t0 + self.dur - a.lmin)
        step = max(1, int(round(1.0 / (self.te[1] - self.te[0]))))
        for ac in self.ac:
            X, Y, Z = ac["X"][s][::step], ac["Y"][s][::step], ac["Z"][s][::step]
            ok = (np.isfinite(X) & (X >= self.xb[0]) & (X <= self.xb[1])
                  & (Y >= self.yb[0]) & (Y <= self.yb[1]))
            if not ok.any():
                continue
            X, Y, Z = X[ok], Y[ok], Z[ok]
            lat, lon = to_ll(X, Y, self.lat0, self.lon0)
            low = (Z - self.terrain.elevation(lat, lon)) < a.dense_agl
            ix = np.clip(np.round((X[low] - xs[0]) / a.dense).astype(int), 0, len(xs) - 1)
            iy = np.clip(np.round((Y[low] - ys[0]) / a.dense).astype(int), 0, len(ys) - 1)
            mask[iy, ix] = True
        r = int(round(a.dense_radius / a.dense))
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        mask = binary_dilation(mask, structure=(xx ** 2 + yy ** 2) <= r * r)
        GX, GY = np.meshgrid(xs, ys)
        return GX[mask], GY[mask]

    def curve_for(self, ac, gx, gy, gz, L_s):
        """Predicted log-ratio curve (bins) for one point at latency L, on audio frames."""
        d = np.empty((1, self.nj), np.int16)
        j0 = np.empty(1, np.int64)
        j1 = np.empty(1, np.int64)
        rm = np.empty(1)
        tm = np.empty(1)
        curves_kernel(np.array([gx]), np.array([gy]), np.array([gz]), self.te, ac["X"], ac["Y"], ac["Z"],
                      C_AIR, self.tj0, self.hop_s, self.nj, self.a.dlog, self.a.rmax, self.pad,
                      self.a.cpa_min, self.a.cpa_k, d, j0, j1, rm, tm)
        o = int(round((L_s / self.hop_s))) - self.Lmax_f
        rows = np.arange(self.nj) + o
        ok = (d[0] != SENT) & (rows >= 0) & (rows < self.T)
        return rows[ok], d[0][ok].astype(float)


def combine(exc):
    """Positive excess over each aircraft's grid median (per latency), summed over aircraft."""
    med = np.nanmedian(exc, axis=1, keepdims=True)
    med = np.where(np.isfinite(med), med, 0.0)
    contrib = np.nan_to_num(np.maximum(exc - med, 0.0))
    return contrib, med


def local_maxima(H, gx, gy, k, sep):
    order = np.argsort(-H)
    picks = []
    for i in order:
        if not np.isfinite(H[i]) or H[i] <= 0:
            break
        if all(math.hypot(gx[i] - gx[j], gy[i] - gy[j]) >= sep for j in picks):
            picks.append(i)
        if len(picks) >= k:
            break
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fmin", type=float, default=300.0)
    ap.add_argument("--fmax", type=float, default=6000.0)
    ap.add_argument("--f0min", type=float, default=300.0)
    ap.add_argument("--f0max", type=float, default=6000.0)
    ap.add_argument("--harmonics", type=int, default=3)
    ap.add_argument("--win", type=float, default=0.256, help="STFT window, s")
    ap.add_argument("--hop", type=float, default=0.25, help="STFT hop, s")
    ap.add_argument("--dlog", type=float, default=0.005)
    ap.add_argument("--lmin", type=float, default=-10.0)
    ap.add_argument("--lmax", type=float, default=90.0)
    ap.add_argument("--lstep", type=float, default=0.5)
    ap.add_argument("--grid", type=float, default=450.0)
    ap.add_argument("--fine", type=float, default=75.0)
    ap.add_argument("--fine_half", type=float, default=1200.0)
    ap.add_argument("--rmax", type=float, default=5000.0)
    ap.add_argument("--near_km", type=float, default=8.0)
    ap.add_argument("--max_alt", type=float, default=4000.0)
    ap.add_argument("--min_window", type=float, default=6.0, help="v1.3: below the 8 s CPA window")
    ap.add_argument("--cpa_min", type=float, default=8.0, help="v1.3: min scored window around CPA, s")
    ap.add_argument("--cpa_k", type=float, default=4.0, help="v1.3: scored window = cpa_k * d_cpa / v")
    ap.add_argument("--min_swing", type=float, default=0.005, help="v1.1: min Doppler ratio swing in the scored frames")
    ap.add_argument("--peaks", type=int, default=25)
    ap.add_argument("--near_best", type=float, default=0.7, help="also refine local maxima >= this x best")
    ap.add_argument("--max_refine", type=int, default=100)
    ap.add_argument("--dense", type=float, default=100.0)
    ap.add_argument("--dense_radius", type=float, default=1500.0)
    ap.add_argument("--dense_agl", type=float, default=600.0)
    a = ap.parse_args()
    t_start = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    P = Problem(a.case_dir, a)
    if not P.ac:
        raise SystemExit("no candidate aircraft")

    # ---- coarse grid over the bbox
    xs = np.arange(P.xb[0], P.xb[1] + 1e-6, a.grid)
    ys = np.arange(P.yb[0], P.yb[1] + 1e-6, a.grid)
    GX, GY = np.meshgrid(xs, ys)  # [iy, ix]
    gx, gy = GX.ravel(), GY.ravel()
    gz = P.grid_heights(gx, gy)
    Ls_f = np.arange(P.Lmin_f, P.Lmax_f + 1, P.Lstep_f)
    rowofs = (Ls_f - P.Lmax_f).astype(np.int64)
    Ls = Ls_f * P.hop_s
    print(f"coarse grid {len(ys)}x{len(xs)} = {len(gx)} pts, {len(Ls)} latencies", flush=True)
    exc, cur, k0, rmin, temin = P.score(gx, gy, gz, rowofs)
    contrib, med = combine(exc)
    tot = contrib.sum(axis=0)  # (G, nL)
    print(f"coarse done ({time.time() - t_start:.1f}s)", flush=True)

    # ---- dense sub-grid near low airborne passes, scored with the coarse grid's null
    heat_all = tot.max(axis=1)
    dx, dy = P.dense_points()
    print(f"dense sub-grid {len(dx)} pts", flush=True)
    if len(dx):
        dz = P.grid_heights(dx, dy)
        dtot = P.score(dx, dy, dz, rowofs, med=med)
    else:
        dz, dtot = np.zeros(0), np.zeros((0, len(Ls)), np.float32)
    dlat, dlon = to_ll(dx, dy, P.lat0, P.lon0)
    np.savez_compressed(out / "dense.npz", lat=dlat, lon=dlon, heat=dtot.max(axis=1) if len(dx) else dx,
                        latency_s=Ls[np.argmax(dtot, axis=1)] if len(dx) else dx)
    print(f"dense done ({time.time() - t_start:.1f}s)", flush=True)

    # ---- refine: top peaks plus every local maximum within 30 % of the best (coarse + dense pool)
    px_ = np.r_[gx, dx]
    py_ = np.r_[gy, dy]
    ptot = np.r_[tot, dtot]
    pheat = ptot.max(axis=1)
    pk = local_maxima(pheat, px_, py_, a.peaks, 2000.0)
    near = [i for i in local_maxima(pheat, px_, py_, 10 ** 6, 1000.0) if pheat[i] >= a.near_best * pheat.max()]
    peaks = []
    for i in sorted(set(pk) | set(near), key=lambda q: -pheat[q]):
        if all(math.hypot(px_[i] - px_[j], py_[i] - py_[j]) >= 500 for j in peaks):
            peaks.append(i)
    if len(peaks) > a.max_refine:
        print(f"  {len(peaks)} peaks, refining the best {a.max_refine}", flush=True)
        peaks = peaks[:a.max_refine]
    print(f"refining {len(peaks)} peaks", flush=True)
    fine_step = max(1, int(round(0.25 / P.hop_s)))

    def refine(cx, cy, Lc_f, half=a.fine_half):
        fx = np.arange(cx - half, cx + half + 1e-6, a.fine)
        fy = np.arange(cy - half, cy + half + 1e-6, a.fine)
        FX, FY = np.meshgrid(fx, fy)
        fxx, fyy = FX.ravel(), FY.ravel()
        fzz = P.grid_heights(fxx, fyy)
        hw = int(round(3.0 / P.hop_s))  # +-3 s around the region's coarse latency
        lf = np.arange(max(P.Lmin_f, Lc_f - hw), min(P.Lmax_f, Lc_f + hw) + 1, fine_step)
        e, c_, kk, rm, tm = P.score(fxx, fyy, fzz, (lf - P.Lmax_f).astype(np.int64))
        # null statistics from the coarse grid (grid median per aircraft, at the nearest coarse L)
        li = np.clip(np.round((lf - P.Lmin_f) / P.Lstep_f).astype(int), 0, len(Ls) - 1)
        m = med[:, 0, li][:, None, :]
        cb = np.nan_to_num(np.maximum(e - m, 0.0))
        t = cb.sum(axis=0)
        return dict(x=fxx, y=fyy, z=fzz, Lf=lf, exc=e, k0=kk, contrib=cb, tot=t, rmin=rm, temin=tm)

    regions = []
    for p in peaks:
        Lc = Ls_f[int(np.argmax(ptot[p]))]
        r = refine(px_[p], py_[p], Lc)
        regions.append(r)
        i, l = np.unravel_index(np.argmax(r["tot"]), r["tot"].shape)
        print(f"  peak {pheat[p]:.3f} -> fine {r['tot'][i, l]:.3f} at L={r['Lf'][l] * P.hop_s:.2f}s", flush=True)
    best_r = max(range(len(regions)), key=lambda q: np.max(regions[q]["tot"]))
    R = regions[best_r]
    bi, bl = np.unravel_index(np.argmax(R["tot"]), R["tot"].shape)
    bx, by, bz = R["x"][bi], R["y"][bi], R["z"][bi]
    bL = R["Lf"][bl] * P.hop_s
    best_score = float(R["tot"][bi, bl])
    blat, blon = to_ll(bx, by, P.lat0, P.lon0)
    contrib_best = R["contrib"][:, bi, bl]
    order = np.argsort(-contrib_best)
    top = [int(i) for i in order if contrib_best[i] > 0]
    print(f"best {float(blat):.5f},{float(blon):.5f} score {best_score:.3f} L {bL:.2f}s "
          f"top {[P.ac[i]['flight'] or P.ac[i]['hex'] for i in top[:5]]}", flush=True)

    # ---- mirror across the main track (top aircraft, at its closest approach)
    mirror = None
    two_band = None  # None = mirror could not be evaluated
    if top:
        ac = P.ac[top[0]]
        tcpa = R["temin"][top[0], bi]
        n = int(np.argmin(np.abs(P.te - tcpa)))
        fin = [k for k in range(max(0, n - 8), min(len(P.te), n + 9)) if np.isfinite(ac["X"][k])]
        n0, n1 = (fin[0], fin[-1]) if fin else (n, n)
        px, py = ac["X"][n], ac["Y"][n]
        ux, uy = ac["X"][n1] - ac["X"][n0], ac["Y"][n1] - ac["Y"][n0]
        nrm = math.hypot(ux, uy)
        if np.isfinite(nrm) and nrm > 0:
            ux, uy = ux / nrm, uy / nrm
            vx, vy = bx - px, by - py
            along = vx * ux + vy * uy
            mx, my = px + 2 * along * ux - vx, py + 2 * along * uy - vy
            Rm = refine(mx, my, R["Lf"][bl], half=600.0)
            mi, ml = np.unravel_index(np.argmax(Rm["tot"]), Rm["tot"].shape)
            mlat, mlon = to_ll(Rm["x"][mi], Rm["y"][mi], P.lat0, P.lon0)
            ms = float(Rm["tot"][mi, ml])
            mirror = dict(lat=round(float(mlat), 6), lon=round(float(mlon), 6), score=round(ms, 4),
                          latency_s=round(float(Rm["Lf"][ml] * P.hop_s), 2),
                          reflected_lat=round(float(to_ll(mx, my, P.lat0, P.lon0)[0]), 6),
                          reflected_lon=round(float(to_ll(mx, my, P.lat0, P.lon0)[1]), 6),
                          distance_from_answer_m=round(math.hypot(Rm["x"][mi] - bx, Rm["y"][mi] - by), 1),
                          ratio_to_top=round(ms / best_score, 4) if best_score > 0 else None)
            two_band = bool(best_score > 0 and ms >= 0.9 * best_score
                            and math.hypot(Rm["x"][mi] - bx, Rm["y"][mi] - by) > 300)

    # ---- runner-up: best other region / coarse local max >= 2 km away
    cands = []
    for q, r in enumerate(regions):
        i, l = np.unravel_index(np.argmax(r["tot"]), r["tot"].shape)
        if math.hypot(r["x"][i] - bx, r["y"][i] - by) >= 2000:
            cands.append((float(r["tot"][i, l]), r["x"][i], r["y"][i], r["Lf"][l] * P.hop_s))
    for p in local_maxima(heat_all, gx, gy, 20, 1000.0):
        if math.hypot(gx[p] - bx, gy[p] - by) >= 2000:
            cands.append((float(heat_all[p]), gx[p], gy[p], Ls[int(np.argmax(tot[p]))]))
    runner = None
    if cands:
        s, x_, y_, l_ = max(cands)
        la, lo = to_ll(x_, y_, P.lat0, P.lon0)
        runner = dict(lat=round(float(la), 6), lon=round(float(lo), 6), score=round(s, 4),
                      latency_s=round(float(l_), 2), distance_from_answer_m=round(math.hypot(x_ - bx, y_ - by), 1))

    # ---- per-aircraft details at the answer
    used = []
    for i in top:
        ac = P.ac[i]
        kk = int(R["k0"][i, bi, bl])
        n = int(np.argmin(np.abs(P.te - R["temin"][i, bi])))
        hd = math.hypot(ac["X"][n] - bx, ac["Y"][n] - by)
        used.append(dict(hex=ac["hex"], callsign=ac["flight"], type=ac["type"],
                         closest_slant_m=round(float(R["rmin"][i, bi]), 1),
                         closest_horizontal_m=round(float(hd), 1),
                         height_above_point_m=round(float(ac["Z"][n] - bz), 1),
                         cpa_audio_s=round(float(R["temin"][i, bi] - P.t0 + bL), 1),
                         excess=round(float(R["exc"][i, bi, bl]), 4),
                         contribution=round(float(contrib_best[i]), 4),
                         f0_hz=round(float(P.fk[kk]), 1) if kk >= 0 else None))

    # ---- latency scan at the answer (coarse latencies) and over the whole grid
    one = P.score(np.array([bx]), np.array([by]), np.array([bz]), rowofs)
    m_all = med[:, 0, :]
    scan_pt = np.nan_to_num(np.maximum(one[0][:, 0, :] - m_all, 0)).sum(axis=0)
    scan_grid = tot.max(axis=0)

    null = float(np.median(heat_all[heat_all > 0])) if (heat_all > 0).any() else 0.0
    Lb = int(np.argmin(np.abs(Ls - bL)))
    heat_1 = contrib[top[0], :, Lb] if top else np.zeros(len(gx))
    heat_2 = contrib[top[:2], :, Lb].sum(axis=0) if top else np.zeros(len(gx))
    shape = GX.shape
    np.save(out / "heat_1.npy", heat_1.reshape(shape).astype(np.float32))
    np.save(out / "heat_2.npy", heat_2.reshape(shape).astype(np.float32))
    np.save(out / "heat_all.npy", heat_all.reshape(shape).astype(np.float32))
    lat_ax = to_ll(np.zeros_like(ys), ys, P.lat0, P.lon0)[0]
    lon_ax = to_ll(xs, np.zeros_like(xs), P.lat0, P.lon0)[1]
    (out / "grid.json").write_text(json.dumps(dict(
        lat=[round(float(v), 6) for v in lat_ax], lon=[round(float(v), 6) for v in lon_ax],
        shape=list(shape), index="heat[i_lat, i_lon]", spacing_m=a.grid,
        heat_1=f"top aircraft at the answer latency {bL:.2f} s",
        heat_2=f"top two aircraft at the answer latency {bL:.2f} s",
        heat_all="all aircraft, max over latency")))

    # ---- curves for the top aircraft
    curves = []
    for i in top[:max(2, min(4, len(top)))]:
        ac = P.ac[i]
        rows, dbin = P.curve_for(ac, bx, by, bz, bL)
        kk = int(R["k0"][i, bi, bl])
        f0 = P.fk[kk]
        harm = {}
        for h in (1, 2, 3)[:a.harmonics]:
            f = f0 * h * np.exp(dbin * a.dlog)
            if (f >= a.fmin).any() and (f <= a.fmax).any():
                harm[f"x{h}"] = [round(float(v), 2) for v in f]
        curves.append(dict(hex=ac["hex"], callsign=ac["flight"], type=ac["type"], f0_hz=round(float(f0), 1),
                           t_s=[round(float(v), 3) for v in P.tau[rows]], f_hz=harm))
    (out / "curves.json").write_text(json.dumps(dict(
        note="t_s = audio seconds since utc_start at the answer point and latency; f_hz per harmonic",
        latency_s=round(bL, 2), curves=curves[:2], extra=curves[2:])))

    runtime = time.time() - t_start
    ans = dict(
        case=P.name, version="1.3", method="ridge: emission-time Doppler curve on whitened log-f spectrogram, "
        "max over f0 (x1,x2,x3), minus flat-line null, airborne track segments only, min Doppler swing, positive excess over per-aircraft grid median, "
        "summed over aircraft, argmax over grid and shared latency",
        lat=round(float(blat), 6), lon=round(float(blon), 6), score=round(best_score, 4),
        null_grid_median=round(null, 4), score_over_null=round(best_score / null, 2) if null > 0 else None,
        latency_s=round(float(bL), 2),
        latency_at_bound=bool(min(abs(bL - Ls[0]), abs(bL - Ls[-1])) < 0.5 * P.Lstep_f * P.hop_s),
        two_band=two_band, mirror=mirror, runner_up=runner,
        aircraft=used, n_candidates=len(P.ac),
        params=dict(vars(a), c_m_s=round(C_AIR, 2), mic_agl_m=MIC_AGL, geoid_n_m=round(P.geoid_n, 2),
                    heights=f"{P.terrain.note}; aircraft alt_geom (HAE) minus geoid; baro + per-hex geom-baro "
                            "offset when geom missing; ground reports at DEM + 3 m; no wind"),
        runtime_s=round(runtime, 1))
    (out / "answer.json").write_text(json.dumps(ans, indent=1))
    print(json.dumps({k: ans[k] for k in ("lat", "lon", "score", "null_grid_median", "latency_s", "latency_at_bound",
                                          "two_band")}), flush=True)

    plots(P, out, a, xs, ys, heat_1, heat_2, heat_all, shape, top, bx, by, bL, mirror, curves,
          Ls, scan_pt, scan_grid, R, bi, bl)
    print(f"done in {time.time() - t_start:.1f}s", flush=True)


def plots(P, out, a, xs, ys, heat_1, heat_2, heat_all, shape, top, bx, by, bL, mirror, curves,
          Ls, scan_pt, scan_grid, R, bi, bl):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lat_ax = to_ll(np.zeros_like(ys), ys, P.lat0, P.lon0)[0]
    lon_ax = to_ll(xs, np.zeros_like(xs), P.lat0, P.lon0)[1]
    blat, blon = to_ll(bx, by, P.lat0, P.lon0)
    ext = [lon_ax[0], lon_ax[-1], lat_ax[0], lat_ax[-1]]
    t_lo = P.t0 - a.lmax - 20
    t_hi = P.t0 + P.dur - a.lmin
    for name, H, title in (("heatmap_1plane", heat_1, "best single aircraft"),
                           ("heatmap_2planes", heat_2, "top two aircraft"),
                           ("heatmap_all", heat_all, "all aircraft (max over latency)")):
        fig, ax = plt.subplots(figsize=(8, 7.5))
        im = ax.imshow(H.reshape(shape), origin="lower", extent=ext, cmap="magma", aspect="auto")
        fig.colorbar(im, ax=ax, label="positive excess (whitened energy)")
        for i, ac in enumerate(P.ac):
            s = (P.te >= t_lo) & (P.te <= t_hi)
            la, lo = to_ll(ac["X"][s], ac["Y"][s], P.lat0, P.lon0)
            hi = i in top[:2]
            ax.plot(lo, la, color="cyan" if hi else "white", lw=1.4 if hi else 0.5, alpha=0.9 if hi else 0.35)
            if hi:
                ok = np.isfinite(la)
                if ok.any():
                    k = np.nonzero(ok)[0][len(np.nonzero(ok)[0]) // 2]
                    ax.text(lo[k], la[k], ac["flight"] or ac["hex"], color="cyan", fontsize=8)
        ax.plot(blon, blat, "x", color="lime", ms=10, mew=2, label="argmax")
        if mirror:
            ax.plot(mirror["lon"], mirror["lat"], "+", color="yellow", ms=10, mew=2, label="mirror")
        ax.set_xlim(ext[0], ext[1])
        ax.set_ylim(ext[2], ext[3])
        ax.set_xlabel("lon")
        ax.set_ylabel("lat")
        ax.set_title(f"{P.name} ridge: {title}, L={bL:.2f}s")
        ax.legend(loc="lower right", fontsize=8)
        fig.tight_layout()
        fig.savefig(out / f"{name}.png", dpi=110)
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(Ls, scan_pt, label="at the answer point")
    ax.plot(Ls, scan_grid, label="max over grid", alpha=0.7)
    ax.axvline(bL, color="k", ls=":", label=f"best L = {bL:.2f} s")
    ax.set_xlabel("stream latency L (s)")
    ax.set_ylabel("summed positive excess")
    ax.legend()
    ax.set_title(f"{P.name} latency scan")
    fig.tight_layout()
    fig.savefig(out / "latency_scan.png", dpi=110)
    plt.close(fig)

    for cv in curves:
        tt = np.array(cv["t_s"])
        if not len(tt):
            continue
        lo_t, hi_t = max(0, tt.min() - 15), min(P.dur, tt.max() + 15)
        rs = (P.tau >= lo_t) & (P.tau <= hi_t)
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.pcolormesh(P.tau[rs], P.fk, P.W[rs].T, cmap="gray_r", vmin=-1, vmax=4, shading="auto")
        for h, f in cv["f_hz"].items():
            ax.plot(tt, f, color="cyan", lw=1.0, alpha=0.9)
        ax.set_yscale("log")
        ax.set_ylim(a.fmin, a.fmax)
        ax.set_xlabel("audio time since utc_start (s)")
        ax.set_ylabel("Hz")
        ax.set_title(f"{cv['callsign'] or cv['hex']} ({cv['type']})  f0 {cv['f0_hz']} Hz, L={bL:.2f}s, "
                     "predicted curve at argmax (cyan)")
        fig.tight_layout()
        safe = "".join(ch for ch in (cv["callsign"] or cv["hex"]) if ch.isalnum())
        fig.savefig(out / f"spectrogram_{safe}.png", dpi=110)
        plt.close(fig)


if __name__ == "__main__":
    main()
