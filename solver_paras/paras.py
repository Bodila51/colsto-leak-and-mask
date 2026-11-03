"""Paras-style known-camera check (NOT blind): hand-picked aircraft, revealed camera.

This mirrors the structure of Paras Chopra's viral replication. The camera position is known and
each aircraft is picked by hand. For every aircraft:

1. A latency scan. At the known camera, the emission-time-corrected Doppler curve c/(c + dr/dte)
   over a window around closest approach (CPA) is scored on the whitened log-frequency
   spectrogram (max over the emitted f0, with harmonics), for stream latency L = 0..60 s.
2. The spectrogram, with the predicted curve at the best L.
3. A curve-fit heat map on a +-6 km grid at 100 m around the camera, at that L. The window is fixed
   in receive time around the camera's CPA, so this is a fit check, not a search.

Per-case settings live in CASES. In case_E (jet departures, high band) one latency is shared by all
aircraft. In case_B (low-frequency stack) each aircraft gets its own scan and a verdict, and the
Doppler curve is compared with a flat line on the same frames. This tells a pass apart from a
steady or throttling source (an engine running on the apron).

Overhead passes leave the two mirror bands (left and right of the track) nearly merged on the
track line. Along-track position trades off against L, which is fixed here from the known camera.
Geometry, terrain, ADS-B parsing and the spectrogram are reused from solver_ridge/ridge.py.

    python solver_paras/paras.py cases/case_E --out results/paras_setup_E
    python solver_paras/paras.py cases/case_B --out results/paras_setup_B
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numba as nb
import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver_ridge"))
from ridge import (C_AIR, KT, MIC_AGL, Terrain, interp_track, load_tracks, to_ll, to_xy,  # noqa: E402
                   track_heights, whitened_spectrogram)

# Owner-published (revealed) cameras and the aircraft picked by hand.
CASES = {
    "case_E": dict(  # StreamTime LIVE Midway: six 13C departures straight over the camera
        camera=(41.77844, -87.74278),
        picks=["ac13d6", "abaeb2", "aa9b42", "accb50", "ac33a3", "accf07"],
        cpa_window=None, despike=False, event=None,
        clock_l=28.0,  # burned-in stream clock 08:29:39 CST at recording t = 290 s
        voice=(430.0, 474.0),  # synthetic voice mixed in (audio s)
        extend=True, shared_l=True, overview=None,
        yticks=[1000, 1500, 2000, 3000, 4000, 6000],
        params=dict(fmin=800.0, fmax=7900.0, f0min=1000.0, f0max=6000.0, harmonics=3, win=0.15, hop=0.025,
                    dlog=0.005, ridge=31, half=10.0, min_frac=0.6)),
    "case_B": dict(  # Shoreham (Brighton City) ATC tower webcam
        camera=(50.8335, -0.29075),
        picks=["407ee4", "402b2f", "407063"],  # AW109 G-TJPK, RF6 G-BONT, MD500 G-CODA
        # the passes named in the brief (real s after utc_start): ~13:32, ~13:36, ~13:48
        cpa_window={"407ee4": (0.0, 400.0), "402b2f": (200.0, 600.0), "407063": (900.0, 1300.0)},
        despike=True,  # live polls with a stale seen_pos jump (e.g. 407ee4 at 169 s: 156 m/s vs gs 57)
        event=(38.0, 130.0),  # the harmonic stack in the audio (s), read off overview.png
        event_note=("The clear stack (70 Hz x1..x6, audio 38-130 s) starts with an upward sweep at 38-43 s "
                    "(an engine spooling up), stays steady, then slides ~10 % down and fades by ~130 s. For any "
                    "L in 0-60 s it ends (real time <= 130 s) before G-TJPK's first ADS-B report (141 s), and "
                    "G-BONT (from 384 s) and G-CODA (to 1113 s) are further away in time: no candidate's "
                    "ADS-B-covered track can explain it. An engine on the ground (the apron aircraft, or the "
                    "A109 before lift-off, below ADS-B coverage) fits the onset better than a pass."),
        clock_l=None, voice=None, extend=False, shared_l=False, overview=(0.0, 300.0),
        yticks=[50, 70, 100, 140, 200, 300, 420],
        params=dict(fmin=40.0, fmax=500.0, f0min=40.0, f0max=150.0, harmonics=6, win=1.0, hop=0.125,
                    dlog=0.01, ridge=15, half=40.0, min_frac=0.3)),
}


# ---------------------------------------------------------------- track

def despike(tr, tol=1.5, slack=10.0):
    """Drop reports whose implied speed from the previous kept report exceeds tol x the reported
    ground speed + slack m/s (positions with a wrong timestamp)."""
    x, y = to_xy(tr["lat"], tr["lon"], tr["lat"][0], tr["lon"][0])
    keep = np.ones(len(x), bool)
    j = 0
    for i in range(1, len(x)):
        dt = tr["t"][i] - tr["t"][j]
        gs = np.nanmax([tr["gs"][i], tr["gs"][j], 0.0]) * KT
        if dt > 0 and math.hypot(x[i] - x[j], y[i] - y[j]) / dt > tol * gs + slack:
            keep[i] = False
        else:
            j = i
    return {k: (v[keep] if isinstance(v, np.ndarray) else v) for k, v in tr.items()}, int((~keep).sum())


def extend_back(tr, h, terrain, lat0, lon0, span=60.0, dt=1.0, fit_s=6.0):
    """ADS-B coverage of these departures starts only 0-9 s before the camera (on the runway they are
    below the receivers). Extend the track back with the ground velocity and climb of the first fit_s
    of reports, clamped to the runway (DEM + 3 m). Acceleration on the roll is ignored."""
    x, y = to_xy(tr["lat"], tr["lon"], lat0, lon0)
    t = tr["t"]
    f = t <= t[0] + fit_s
    f[:3] = True
    vx, vy, climb = (np.polyfit(t[f] - t[0], v[f], 1)[0] for v in (x, y, h))
    ts = t[0] - dt * np.arange(int(span / dt), 0, -1)
    xs, ys = x[0] + vx * (ts - t[0]), y[0] + vy * (ts - t[0])
    lat, lon = to_ll(xs, ys, lat0, lon0)
    hs = np.maximum(h[0] + climb * (ts - t[0]), terrain.elevation(lat, lon) + 3.0)
    n = len(ts)
    new = dict(tr, t=np.r_[ts, t], lat=np.r_[lat, tr["lat"]], lon=np.r_[lon, tr["lon"]],
               ground=np.r_[np.zeros(n, bool), tr["ground"]], baro=np.r_[np.full(n, np.nan), tr["baro"]],
               geom=np.r_[np.full(n, np.nan), tr["geom"]], gs=np.r_[np.full(n, math.hypot(vx, vy) / KT), tr["gs"]],
               trk=np.r_[np.full(n, math.degrees(math.atan2(vx, vy)) % 360), tr["trk"]])
    return new, np.r_[hs, h], float(climb)


# ---------------------------------------------------------------- kernel

@nb.njit(parallel=True, cache=True)
def fit_kernel(gx, gy, gz, te, ax, ay, az, c, tq, rows, Wp, pad, dlog, hk, k0lo, k0hi, nmin, flat,
               s_out, k0_out, n_out):
    """For each point: Doppler log-ratio at the receive times tq (frames rows), the mean whitened
    energy along it for every emitted bin k0 (+ harmonic offsets hk), max over k0. With flat, the
    same frames are scored along ratio 1 (no Doppler)."""
    G = gx.shape[0]
    N = te.shape[0]
    Q = tq.shape[0]
    K = Wp.shape[1] - 2 * pad
    H = hk.shape[0]
    for g in nb.prange(G):
        r = np.empty(N)
        for n in range(N):
            r[n] = math.sqrt((ax[n] - gx[g]) ** 2 + (ay[n] - gy[g]) ** 2 + (az[n] - gz[g]) ** 2)
        tr = np.empty(N)
        lr = np.empty(N)
        for n in range(N):
            tr[n] = te[n] + r[n] / c
            if 0 < n < N - 1:
                vr = (r[n + 1] - r[n - 1]) / (te[n + 1] - te[n - 1])
                lr[n] = math.log(c / (c + vr))
            else:
                lr[n] = np.nan
        S = np.zeros(K + 2 * pad)
        cnt = 0
        n = 1
        for q in range(Q):
            t = tq[q]
            while n < N - 2 and not (tr[n + 1] > t):
                n += 1
            if not (tr[n] <= t < tr[n + 1]):
                continue
            a0 = lr[n]
            a1 = lr[n + 1]
            if not (np.isfinite(a0) and np.isfinite(a1)):
                continue
            w = (t - tr[n]) / (tr[n + 1] - tr[n])
            k = int(round(((1 - w) * a0 + w * a1) / dlog))
            if k > pad:
                k = pad
            if k < -pad:
                k = -pad
            if flat:
                k = 0
            b = pad + k
            row = rows[q]
            for kk in range(K):
                S[kk] += Wp[row, b + kk]
            cnt += 1
        n_out[g] = cnt
        if cnt < nmin:
            s_out[g] = np.nan
            k0_out[g] = -1
            continue
        best = -1e30
        bk = -1
        for k0 in range(k0lo, k0hi + 1):
            s = S[k0]
            m = 1
            for hi in range(1, H):
                if k0 + hk[hi] < K:
                    s += S[k0 + hk[hi]]
                    m += 1
            v = s / (cnt * math.sqrt(m))
            if v > best:
                best = v
                bk = k0
        s_out[g] = best
        k0_out[g] = bk


# ---------------------------------------------------------------- setup

class Setup:
    def __init__(self, case_dir, a, cfg):
        self.a = a
        self.cfg = cfg
        cs = json.loads((Path(case_dir) / "case.json").read_text())
        self.t0 = datetime.fromisoformat(cs["utc_start"].replace("Z", "+00:00")).timestamp()
        x, fs = sf.read(ROOT / cs["audio"], dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        self.dur = len(x) / fs
        hop = int(round(a.hop * fs))
        self.hop_s = hop / fs
        self.W, self.tau, self.fk = whitened_spectrogram(x, fs, a.fmin, a.fmax, int(round(a.win * fs)),
                                                         hop, a.dlog)
        del x
        if a.ridge > 1:  # keep narrow tonal ridges only: subtract the local mean over +-ridge/2 log bins
            self.W = (self.W - uniform_filter1d(self.W, a.ridge, axis=1, mode="nearest")).astype(np.float32)
        self.T, self.K = self.W.shape
        self.pad = int(math.ceil(math.log(1.45) / a.dlog))
        self.Wp = np.zeros((self.T, self.K + 2 * self.pad), np.float32)
        self.Wp[:, self.pad:self.pad + self.K] = self.W
        self.hk = np.array([int(round(math.log(m) / a.dlog)) for m in range(1, a.harmonics + 1)], np.int64)
        self.k0lo = int(round(math.log(a.f0min / a.fmin) / a.dlog))
        self.k0hi = min(self.K - 1, int(round(math.log(a.f0max / a.fmin) / a.dlog)))
        v = cfg["voice"]
        self.clean = np.ones(self.T, bool) if v is None else ~((self.tau >= v[0]) & (self.tau <= v[1]))

        self.lat0, self.lon0 = cfg["camera"]
        self.terrain = Terrain()
        self.geoid_n = self.terrain.geoid(self.lat0, self.lon0)
        self.cam_z = float(self.terrain.elevation(np.array([self.lat0]), np.array([self.lon0]))[0]) + MIC_AGL
        tracks = load_tracks(ROOT / cs["adsb"], self.t0 - 300, self.t0 + self.dur + 300)
        offs = [np.median(tr["geom"][m] - tr["baro"][m]) for tr in tracks.values()
                for m in [np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])] if m.any()]
        goff = float(np.median(offs)) if offs else 0.0
        self.ac = []
        for hx in cfg["picks"]:
            tr = tracks[hx]
            keep = (tr["t"] >= self.t0 - 60) & (tr["t"] <= self.t0 + self.dur + 60)
            if cfg["extend"]:
                keep &= ~tr["ground"]
            tr = {k: (v[keep] if isinstance(v, np.ndarray) else v) for k, v in tr.items()}
            dropped = 0
            if cfg["despike"]:
                tr, dropped = despike(tr)
            h = track_heights(tr, self.terrain, self.geoid_n, goff)
            rx, ry = to_xy(tr["lat"], tr["lon"], self.lat0, self.lon0)
            rt = tr["t"]
            climb = np.nan
            if cfg["extend"]:
                tr, h, climb = extend_back(tr, h, self.terrain, self.lat0, self.lon0)
            te = np.arange(self.t0 - 120, self.t0 + self.dur + 60, 0.02)
            X, Y, Z = interp_track(tr, h, te, self.lat0, self.lon0)
            r = np.sqrt(X ** 2 + Y ** 2 + (Z - self.cam_z) ** 2)
            w = (cfg["cpa_window"] or {}).get(hx)
            rw = r if w is None else np.where((te >= self.t0 + w[0]) & (te <= self.t0 + w[1]), r, np.nan)
            i = int(np.nanargmin(rw))
            # emission time span that feeds receive times cpa_tr +- win (any point within ~9 km)
            cpa_tr = te[i] + r[i] / C_AIR
            sel = (te >= cpa_tr - a.half - 30) & (te <= cpa_tr + a.half + 1)
            j = max(0, i - 50), min(len(te), i + 51)
            vx, vy = np.gradient(X[j[0]:j[1]], te[j[0]:j[1]]), np.gradient(Y[j[0]:j[1]], te[j[0]:j[1]])
            hd = math.atan2(np.nanmean(vx), np.nanmean(vy))  # track direction at CPA, from north
            lateral = X[i] * math.cos(hd) - Y[i] * math.sin(hd)  # camera-to-track offset (signed)
            self.ac.append(dict(
                hex=hx, flight=tr["flight"], type=tr["type"], te=te[sel], X=X[sel], Y=Y[sel], Z=Z[sel],
                rx=rx, ry=ry, rt=rt, cpa_te=float(te[i]), cpa_tr=float(cpa_tr), cpa_r=float(r[i]),
                cpa_hor=float(math.hypot(X[i], Y[i])), cpa_agl=float(Z[i] - self.cam_z + MIC_AGL),
                track_deg=float(math.degrees(hd) % 360), lateral=float(abs(lateral)),
                first_report_s=float(rt[0] - te[i]), last_report_s=float(rt[-1] - te[i]), climb=climb,
                dropped=dropped))

    def frames(self, ac, L, keep_voice=False):
        """Audio rows and receive times for the window cpa_tr +- half at latency L (voice frames dropped)."""
        tq_all = self.tau + self.t0 - L
        rows = np.nonzero((tq_all >= ac["cpa_tr"] - self.a.half) & (tq_all <= ac["cpa_tr"] + self.a.half)
                          & (self.clean | keep_voice))[0]
        return rows, tq_all[rows]

    def fit(self, ac, gx, gy, gz, L, keep_voice=False, flat=False):
        rows, tq = self.frames(ac, L, keep_voice)
        G = len(gx)
        s = np.full(G, np.nan)
        k0 = np.full(G, -1, np.int64)
        n = np.zeros(G, np.int64)
        nmin = int(round(self.a.min_frac * 2 * self.a.half / self.hop_s))
        if len(rows) >= nmin:
            fit_kernel(gx, gy, gz, ac["te"], ac["X"], ac["Y"], ac["Z"], C_AIR, tq, rows.astype(np.int64),
                       self.Wp, self.pad, self.a.dlog, self.hk, self.k0lo, self.k0hi, nmin, flat, s, k0, n)
        return s, k0, n

    def curve(self, ac, L):
        """Predicted log-ratio at the camera on the audio frames of the window (for plotting)."""
        r = np.sqrt(ac["X"] ** 2 + ac["Y"] ** 2 + (ac["Z"] - self.cam_z) ** 2)
        tr = ac["te"] + r / C_AIR
        lr = np.log(C_AIR / (C_AIR + np.gradient(r, ac["te"])))
        t_audio = tr - self.t0 + L
        return t_audio, lr


# ---------------------------------------------------------------- plots

def label(ac):
    return f"{ac['flight'] or ac['hex']} ({ac['type']})"


def plot_scan(S, Ls, used, skipped, bestL, out):
    """bestL: one shared latency (float) or a per-hex dict."""
    clock = S.cfg["clock_l"]
    n = len(S.ac)
    shared = not isinstance(bestL, dict)
    fig, axs = plt.subplots(n + shared, 1, figsize=(9, 1.6 * (n + shared) + 0.4), sharex=True, squeeze=False)
    axs = axs[:, 0]
    for ax, ac in zip(axs, S.ac):
        col = "tab:blue" if ac["hex"] in used else "0.6"
        ax.plot(Ls, ac["scan"], color=col, lw=1.2, label="Doppler curve")
        if "scan_flat" in ac:
            ax.plot(Ls, ac["scan_flat"], color="0.5", lw=0.9, ls="--", label="flat line, same frames")
        L = bestL if shared else bestL.get(ac["hex"])
        if L is not None:
            ax.axvline(L, color="tab:red", lw=1, label=None if shared else f"best L = {L:.2f} s")
        if clock is not None:
            ax.axvline(clock, color="k", ls=":", lw=1)
        note = "" if ac["hex"] in used else f"  [skipped: {skipped[ac['hex']]}]"
        ax.set_title(label(ac) + note, fontsize=9, loc="left")
        ax.set_ylabel("fit")
        if not shared:
            ax.legend(fontsize=7, loc="upper right")
    if shared:
        tot = np.nansum([ac["scan"] for ac in S.ac if ac["hex"] in used], axis=0)
        axs[-1].plot(Ls, tot, color="k", lw=1.5)
        axs[-1].axvline(bestL, color="tab:red", lw=1, label=f"best shared L = {bestL:.2f} s")
        if clock is not None:
            axs[-1].axvline(clock, color="k", ls=":", lw=1, label=f"burned-in clock L ~ {clock:.0f} s")
        axs[-1].set_title("sum over used aircraft", fontsize=9, loc="left")
        axs[-1].legend(fontsize=8, loc="upper right")
    axs[-1].set_xlabel("stream latency L (s)")
    fig.suptitle("Latency scan at the KNOWN camera (not blind, hand-picked aircraft)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def set_hz_axis(S, ax):
    ax.set_yscale("log")
    ax.set_ylim(S.fk[0], S.fk[-1])
    ticks = S.cfg["yticks"]
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{t / 1000:g}k" if t >= 1000 else f"{t:g}" for t in ticks])
    ax.minorticks_off()


def plot_spec(S, ac, L, k0, out, extra=""):
    t_c = ac["cpa_tr"] - S.t0 + L
    sel = (S.tau >= t_c - S.a.half - 5) & (S.tau <= t_c + S.a.half + 5)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.pcolormesh(S.tau[sel], S.fk, S.W[sel].T, shading="nearest", cmap="magma", vmin=-1, vmax=3, rasterized=True)
    t_a, lr = S.curve(ac, L)
    keep = (t_a >= t_c - S.a.half) & (t_a <= t_c + S.a.half)
    if k0 >= 0:
        f0 = S.fk[0] * math.exp(k0 * S.a.dlog)
        styles = {1: "-", 2: "--", 3: ":"}
        for h in range(1, S.a.harmonics + 1):
            ax.plot(t_a[keep], h * f0 * np.exp(lr[keep]), color="cyan", lw=1.3 if h <= 3 else 0.9,
                    ls=styles.get(h, "-."), label=f"x{h}" if h > 1 else f"predicted, f0 = {f0:.0f} Hz")
    if S.cfg["voice"] is not None:
        ax.axvspan(*S.cfg["voice"], color="w", alpha=0.25, lw=0)
    ax.set_xlim(S.tau[sel][0], S.tau[sel][-1])
    set_hz_axis(S, ax)
    ax.set_xlabel("audio time (s)")
    ax.set_ylabel("Hz (whitened, ridge-filtered)")
    ax.legend(fontsize=8, loc="upper right", ncol=2 if S.a.harmonics > 3 else 1)
    ax.set_title(f"{label(ac)}: closest {ac['cpa_r']:.0f} m slant, {ac['cpa_hor']:.0f} m horizontal, "
                 f"{ac['cpa_agl']:.0f} m AGL   L = {L:.2f} s{extra}", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def plot_overview(S, span, bestL, out):
    """The spectrogram over span (audio s), with each aircraft's scored window at its best L."""
    sel = (S.tau >= span[0]) & (S.tau <= span[1])
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.pcolormesh(S.tau[sel], S.fk, S.W[sel].T, shading="nearest", cmap="magma", vmin=-1, vmax=3, rasterized=True)
    for ac, col in zip(S.ac, ("cyan", "lime", "yellow", "w")):
        L = bestL.get(ac["hex"])
        if L is None:
            continue
        rows, _ = S.frames(ac, L)
        t_a, lr = S.curve(ac, L)
        ok = np.isfinite(lr)
        if not ok.any() or not len(rows):
            continue
        lo, hi = max(S.tau[rows[0]], np.nanmin(t_a[ok])), S.tau[rows[-1]]
        if hi < span[0] or lo > span[1]:
            continue
        ax.axvspan(lo, hi, color=col, alpha=0.15, lw=0)
        ax.text(max(lo, span[0]) + 1, S.fk[-1] * 0.9, f"{ac['flight']} window, L = {L:.2f} s",
                color=col, fontsize=8, va="top")
    ax.set_xlim(span)
    set_hz_axis(S, ax)
    ax.set_xlabel("audio time (s)")
    ax.set_ylabel("Hz (whitened, ridge-filtered)")
    ax.set_title(f"Audio {span[0]:.0f}-{span[1]:.0f} s with each aircraft's scored window at its best L "
                 f"(ADS-B-covered part only)", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def plot_heat(S, H, xs, ys, acs, title, out):
    fig, ax = plt.subplots(figsize=(6.6, 6))
    ext = [xs[0] / 1e3, xs[-1] / 1e3, ys[0] / 1e3, ys[-1] / 1e3]
    im = ax.imshow(H, origin="lower", extent=ext, cmap="viridis", interpolation="nearest")
    fig.colorbar(im, ax=ax, shrink=0.8, label="curve fit (whitened energy)")
    for ac in acs:
        m = (np.abs(ac["rx"]) < 8e3) & (np.abs(ac["ry"]) < 8e3)
        ax.plot(ac["rx"][m] / 1e3, ac["ry"][m] / 1e3, color="w", lw=0.8, alpha=0.8)
    iy, ix = np.unravel_index(np.nanargmax(H), H.shape)
    ax.plot(xs[ix] / 1e3, ys[iy] / 1e3, "x", color="orange", ms=11, mew=2.5,
            label=f"peak ({math.hypot(xs[ix], ys[iy]):.0f} m from camera)")
    ax.plot(0, 0, "*", color="red", ms=16, mec="k", label="true camera")
    ax.set_xlim(ext[0], ext[1])
    ax.set_ylim(ext[2], ext[3])
    ax.set_xlabel("east (km)")
    ax.set_ylabel("north (km)")
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- main

def heat_record(S, ac, H, s0, k0, L, xs, ys, Ls):
    iy, ix = np.unravel_index(np.nanargmax(H), H.shape)
    err = math.hypot(xs[ix], ys[iy])
    plat, plon = to_ll(xs[ix], ys[iy], S.lat0, S.lon0)
    return dict(hex=ac["hex"], flight=ac["flight"] or ac["hex"], type=ac["type"], cpa_audio_s=ac["cpa_tr"] - S.t0 + L,
                cpa_slant_m=ac["cpa_r"], cpa_horizontal_m=ac["cpa_hor"], cpa_agl_m=ac["cpa_agl"],
                track_deg=ac["track_deg"], camera_track_offset_m=ac["lateral"],
                first_adsb_report_rel_cpa_s=ac["first_report_s"], climb_m_s=ac["climb"],
                mirror_separation_m=2 * ac["lateral"], f0_hz=float(S.fk[0] * math.exp(k0 * S.a.dlog)),
                fit_at_camera=float(s0), fit_peak=float(H[iy, ix]),
                peak=dict(lat=float(plat), lon=float(plon), x_m=float(xs[ix]), y_m=float(ys[iy])),
                peak_error_m=round(err, 1), scan_best_L_s=float(Ls[np.nanargmax(ac["scan"])]))


def grid_record(S, ac, cs):
    m = (np.abs(ac["rx"]) < 8e3) & (np.abs(ac["ry"]) < 8e3)
    return dict(hex=ac["hex"], flight=cs, heat=f"heat_{cs}.npy", track_x_m=np.round(ac["rx"][m], 1).tolist(),
                track_y_m=np.round(ac["ry"][m], 1).tolist(), track_t=np.round(ac["rt"][m] - S.t0, 2).tolist())


def combined(S, heats, acs, L, xs, ys, GX, GY, out, what):
    C = np.nansum(heats, axis=0).astype(np.float32)
    np.save(out / "heat_combined.npy", C)
    iy, ix = np.unravel_index(np.nanargmax(C), C.shape)
    err = math.hypot(xs[ix], ys[iy])
    plat, plon = to_ll(xs[ix], ys[iy], S.lat0, S.lon0)
    plot_heat(S, C, xs, ys, acs, f"Sum over {len(heats)} hand-picked {what}, L = {L:.2f} s\n"
              f"not blind: known camera, hand-picked aircraft", out / "heat_combined.png")
    far = np.where(np.hypot(GX, GY) > 1000, C, -np.inf)  # best point more than 1 km from the camera
    jy, jx = np.unravel_index(np.argmax(far), C.shape)
    print(f"combined peak {err:.0f} m from camera")
    return dict(peak=dict(lat=float(plat), lon=float(plon), x_m=float(xs[ix]), y_m=float(ys[iy])),
                peak_error_m=round(err, 1), aircraft=[ac["flight"] for ac in acs],
                fit_peak=float(C[iy, ix]), fit_median=float(np.nanmedian(C)),
                runner_up_beyond_1km=dict(x_m=float(xs[jx]), y_m=float(ys[jy]), fit=float(C[jy, jx]),
                                          dist_m=round(math.hypot(xs[jx], ys[jy]), 1),
                                          ratio_to_peak=round(float(C[jy, jx] / C[iy, ix]), 3)))


def run_shared(S, a, Ls, cam, grid_pts, out, summary, grid):
    """case_E: one latency for all aircraft, skipping passes outside the audio or under the voice."""
    clock, voice = S.cfg["clock_l"], S.cfg["voice"]
    skipped = {}
    for ac in S.ac:
        lo, hi = ac["cpa_tr"] - S.t0 + clock - a.half, ac["cpa_tr"] - S.t0 + clock + a.half
        if hi > S.dur or lo < 0:
            skipped[ac["hex"]] = f"window {lo:.0f}-{hi:.0f} s is past the end of the audio ({S.dur:.0f} s)"
        elif voice is not None and lo < voice[1] and hi > voice[0]:
            skipped[ac["hex"]] = f"window {lo:.0f}-{hi:.0f} s falls in the voice ({voice[0]:.0f}-{voice[1]:.0f} s)"
    used = [ac["hex"] for ac in S.ac if ac["hex"] not in skipped]
    tot = np.nansum([ac["scan"] for ac in S.ac if ac["hex"] in used], axis=0)
    bestL = float(Ls[int(np.nanargmax(tot))])
    print(f"shared L = {bestL:.2f} s (burned-in clock ~{clock:.0f} s)")
    plot_scan(S, Ls, used, skipped, bestL, out / "latency_scan.png")
    summary.update(L_s=bestL, L_clock_s=clock)
    grid["L_s"] = bestL
    xs, ys, GX, GY, GZ = grid_pts
    heats = []
    for ac in S.ac:
        cs = ac["flight"] or ac["hex"]
        if ac["hex"] not in used:
            summary["skipped"].append(dict(hex=ac["hex"], flight=cs, reason=skipped[ac["hex"]]))
            if "voice" in skipped[ac["hex"]]:  # show why: the pass under the voice, voice frames kept
                _, k0, _ = S.fit(ac, *cam, bestL, keep_voice=True)
                plot_spec(S, ac, bestL, int(k0[0]), out / f"spec_{cs}_skipped.png")
            continue
        s0, k0, _ = S.fit(ac, *cam, bestL)
        plot_spec(S, ac, bestL, int(k0[0]), out / f"spec_{cs}.png")
        H = S.fit(ac, GX.ravel(), GY.ravel(), GZ, bestL)[0].reshape(GX.shape).astype(np.float32)
        np.save(out / f"heat_{cs}.npy", H)
        heats.append(H)
        plot_heat(S, H, xs, ys, [ac], f"{label(ac)}: curve fit at L = {bestL:.2f} s\n"
                  f"not blind: known camera, hand-picked aircraft", out / f"heat_{cs}.png")
        rec = heat_record(S, ac, H, s0[0], int(k0[0]), bestL, xs, ys, Ls)
        summary["aircraft"].append(rec)
        grid["aircraft"].append(grid_record(S, ac, cs))
        print(f"{cs:8s} peak {rec['peak_error_m']:6.0f} m from camera (fit {rec['fit_peak']:.2f} vs {s0[0]:.2f} at camera)")
    summary["combined"] = combined(S, heats, [ac for ac in S.ac if ac["hex"] in used], bestL, xs, ys, GX, GY, out,
                                   "departures")
    grid["combined"] = "heat_combined.npy"
    summary["track_note"] = ("ADS-B coverage of each departure starts 0-9 s before the camera; earlier the track "
                             "is extrapolated back along the first 6 s of reports (ground velocity and climb), "
                             "clamped to the runway (DEM + 3 m). Takeoff-roll acceleration is ignored.")
    summary["history_note"] = ("A first run without the ridge filter (--ridge 0) gave L = 33.75 s and peaks 0.1-8.5 km "
                               "off (combined 3.6 km): broadband pass noise outscored the tones. The filter was "
                               "added after looking at that run; L is still chosen only by the Doppler fit.")
    summary["mirror_note"] = ("Overhead passes: the camera is within tens of metres of each track line, so the "
                              "left/right mirror bands nearly merge on the track line and cannot be told apart.")


VERDICT_RULES = ("fits only if all hold: (1) the latency scan has a distinct peak, z = (max - median) / (1.4826 MAD) "
                 ">= 4; (2) at that L the Doppler curve beats a flat line on the same frames by >= 15 %; (3) the "
                 "predicted Doppler swing in the scored frames is >= 2 log bins (else the shape is untestable); "
                 "(4) the camera scores >= 0.9 of the heat-map peak; (5) the fit reaches >= 0.5 of what the "
                 "recording's own clear stack (cfg event) scores along a flat line, i.e. there is a ridge at all. "
                 "Rule 5 was added after seeing G-BONT pass rules 1-4 with no visible ridge under its curve.")


def event_reference(S, ev):
    """Fit of the clear event (the stack) along a flat line, same harmonic sum: what a real ridge scores."""
    m = (S.tau >= ev[0]) & (S.tau <= ev[1])
    w = S.W[m].mean(axis=0)
    best = -1e30
    for k0 in range(S.k0lo, S.k0hi + 1):
        idx = [k0 + h for h in S.hk if k0 + h < S.K]
        best = max(best, w[idx].sum() / math.sqrt(len(idx)))
    return float(best)


def run_each(S, a, Ls, cam, grid_pts, out, summary, grid):
    """case_B: every aircraft gets its own scan, best L and a verdict; a combined map only if >= 2 fit."""
    xs, ys, GX, GY, GZ = grid_pts
    bestL, skipped = {}, {}
    for ac in S.ac:
        ac["scan_flat"] = np.array([S.fit(ac, *cam, L, flat=True)[0][0] for L in Ls])
        if np.isfinite(ac["scan"]).any():
            bestL[ac["hex"]] = float(Ls[int(np.nanargmax(ac["scan"]))])
        else:
            skipped[ac["hex"]] = "no ADS-B-covered frames inside the audio at any L"
    used = list(bestL)
    plot_scan(S, Ls, used, skipped, bestL, out / "latency_scan.png")
    if S.cfg["overview"] is not None:
        plot_overview(S, S.cfg["overview"], bestL, out / "overview.png")
    heats, fit_acs = {}, []
    ref = event_reference(S, S.cfg["event"]) if S.cfg["event"] is not None else None
    summary["event_reference_fit"] = ref
    for ac in S.ac:
        cs = ac["flight"] or ac["hex"]
        if ac["hex"] not in used:
            summary["skipped"].append(dict(hex=ac["hex"], flight=cs, reason=skipped[ac["hex"]]))
            continue
        L = bestL[ac["hex"]]
        s0, k0, n0 = S.fit(ac, *cam, L)
        sf0 = S.fit(ac, *cam, L, flat=True)[0][0]
        rows, _ = S.frames(ac, L)
        t_a, lr = S.curve(ac, L)
        inwin = np.isfinite(lr) & (t_a >= S.tau[rows[0]]) & (t_a <= S.tau[rows[-1]])
        swing = float(np.ptp(lr[inwin]) / a.dlog) if inwin.any() else 0.0
        sc = ac["scan"][np.isfinite(ac["scan"])]
        z = float((sc.max() - np.median(sc)) / (1.4826 * np.median(np.abs(sc - np.median(sc))) + 1e-9))
        plot_spec(S, ac, L, int(k0[0]), out / f"spec_{cs}.png", extra=f"\nDoppler fit {s0[0]:.2f} vs flat {sf0:.2f}")
        H = S.fit(ac, GX.ravel(), GY.ravel(), GZ, L)[0].reshape(GX.shape).astype(np.float32)
        np.save(out / f"heat_{cs}.npy", H)
        heats[ac["hex"]] = H
        plot_heat(S, H, xs, ys, [ac], f"{label(ac)}: curve fit at its best L = {L:.2f} s\n"
                  f"not blind: known camera, hand-picked aircraft", out / f"heat_{cs}.png")
        rec = heat_record(S, ac, H, s0[0], int(k0[0]), L, xs, ys, Ls)
        checks = dict(scan_peak_z=round(z, 2), fit_flat_same_frames=float(sf0),
                      doppler_over_flat=round(float(s0[0] / sf0), 3) if sf0 > 0 else None,
                      predicted_swing_bins=round(swing, 1), scored_frames=int(n0[0]),
                      window_frames=int(round(2 * a.half / S.hop_s)),
                      camera_over_peak=round(float(s0[0] / rec["fit_peak"]), 3))
        fails = []
        if z < 4:
            fails.append(f"latency scan has no distinct peak (z = {z:.1f})")
        if not (sf0 > 0 and s0[0] >= 1.15 * sf0):
            fails.append(f"Doppler curve does not beat a flat line on the same frames ({s0[0]:.2f} vs {sf0:.2f})")
        if swing < 2:
            fails.append(f"predicted Doppler swing is only {swing:.1f} log bins (shape untestable)")
        if checks["camera_over_peak"] < 0.9:
            fails.append(f"camera scores {checks['camera_over_peak']:.2f} of the heat-map peak")
        if ref is not None:
            checks["fit_over_event_reference"] = round(float(s0[0] / ref), 3)
            if s0[0] < 0.5 * ref:
                fails.append(f"no ridge: fit {s0[0]:.2f} is {s0[0] / ref:.2f} of the clear stack's {ref:.2f}")
        rec.update(L_s=L, checks=checks, verdict="fits" if not fails else "does not fit", reasons=fails,
                   adsb_reports_rel_cpa_s=[ac["first_report_s"], ac["last_report_s"]],
                   despiked_reports=ac["dropped"])
        ev = S.cfg["event"]
        if ev is not None:  # can the covered track (receive time + any L in the scan) reach the stack at all?
            span = (ac["rt"][0] - S.t0 + a.lmin, ac["rt"][-1] - S.t0 + ac["cpa_r"] / C_AIR + a.lmax)
            w0 = ac["cpa_tr"] - S.t0 - a.half
            rec["event_check"] = dict(
                event_audio_s=list(ev), adsb_audio_span_any_L_s=[round(span[0], 1), round(span[1], 1)],
                scored_window_audio_s=[round(max(w0, ac["rt"][0] - S.t0) + L, 1), round(w0 + 2 * a.half + L, 1)],
                window_can_reach_event=bool(any(ac["cpa_tr"] - S.t0 - a.half + Lx < ev[1]
                                                and ac["cpa_tr"] - S.t0 + a.half + Lx > ev[0]
                                                and ac["rt"][0] - S.t0 + Lx < ev[1] for Lx in Ls)))
        if not fails:
            fit_acs.append(ac)
        summary["aircraft"].append(rec)
        grid["aircraft"].append(dict(grid_record(S, ac, cs), L_s=L))
        print(f"{cs:8s} L {L:.2f}  fit {s0[0]:.3f} flat {sf0:.3f}  z {z:.1f}  swing {swing:.1f} bins  peak "
              f"{rec['peak_error_m']:.0f} m (camera {checks['camera_over_peak']:.2f} of peak)  -> {rec['verdict']} {fails}")
    if len(fit_acs) >= 2:
        tot = np.nansum([ac["scan"] for ac in fit_acs], axis=0)
        Lc = float(Ls[int(np.nanargmax(tot))])
        hs = [S.fit(ac, GX.ravel(), GY.ravel(), GZ, Lc)[0].reshape(GX.shape) for ac in fit_acs]
        summary["combined"] = dict(combined(S, hs, fit_acs, Lc, xs, ys, GX, GY, out, "aircraft"), L_s=Lc)
        grid["combined"] = "heat_combined.npy"
    else:
        summary["combined"] = f"not made: {len(fit_acs)} aircraft fit (needs 2)"
    summary["verdict_rules"] = VERDICT_RULES
    if S.cfg.get("event_note"):
        summary["event_note"] = S.cfg["event_note"]
    summary["verdict"] = ("fits: " + ", ".join(ac["flight"] for ac in fit_acs)) if fit_acs else "does not fit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fmin", type=float)
    ap.add_argument("--fmax", type=float)
    ap.add_argument("--f0min", type=float)
    ap.add_argument("--f0max", type=float)
    ap.add_argument("--harmonics", type=int, help="score x1..xN")
    ap.add_argument("--win", type=float)
    ap.add_argument("--hop", type=float)
    ap.add_argument("--dlog", type=float)
    ap.add_argument("--ridge", type=int, help="local-mean width in log bins (0 = off)")
    ap.add_argument("--half", type=float, help="window around CPA receive time, +-s")
    ap.add_argument("--min-frac", type=float, help="min fraction of window frames scored")
    ap.add_argument("--lmin", type=float, default=0.0)
    ap.add_argument("--lmax", type=float, default=60.0)
    ap.add_argument("--lstep", type=float, default=0.25)
    ap.add_argument("--extent", type=float, default=6000.0)
    ap.add_argument("--step", type=float, default=100.0)
    a = ap.parse_args()
    name = json.loads((Path(a.case_dir) / "case.json").read_text())["case"]
    cfg = CASES[name]
    for k, v in cfg["params"].items():
        if getattr(a, k) is None:
            setattr(a, k, v)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    S = Setup(a.case_dir, a, cfg)
    print(f"spectrogram {S.W.shape}, camera ground {S.cam_z - MIC_AGL:.1f} m (DEM)  ({time.time() - t_start:.1f}s)")

    # 1. latency scans at the known camera
    Ls = np.arange(a.lmin, a.lmax + 1e-9, a.lstep)
    cam = (np.zeros(1), np.zeros(1), np.full(1, S.cam_z))
    for ac in S.ac:
        ac["scan"] = np.array([S.fit(ac, *cam, L)[0][0] for L in Ls])
        mx = f"scan max {np.nanmax(ac['scan']):.2f} at L {Ls[np.nanargmax(ac['scan'])]:.2f}" \
            if np.isfinite(ac["scan"]).any() else "no scored frames"
        print(f"{ac['flight']:8s} {ac['type']:5s} CPA {ac['cpa_tr'] - S.t0:6.1f}s  {ac['cpa_r']:.0f} m slant  "
              f"track {ac['track_deg']:.0f}  lateral {ac['lateral']:.0f} m  first report {ac['first_report_s']:+.1f}s "
              f"climb {ac['climb']:.1f} m/s  {mx}")

    # 2-3. spectrogram and heat map per aircraft
    xs = np.arange(-a.extent, a.extent + 1e-6, a.step)
    ys = xs.copy()
    GX, GY = np.meshgrid(xs, ys)
    lat, lon = to_ll(GX.ravel(), GY.ravel(), S.lat0, S.lon0)
    GZ = S.terrain.elevation(lat, lon) + MIC_AGL
    summary = dict(note="NOT blind: known (revealed) camera, hand-picked aircraft, Paras-style setup",
                   camera=dict(lat=cfg["camera"][0], lon=cfg["camera"][1], z_m=S.cam_z), params=vars(a),
                   aircraft=[], skipped=[])
    grid = dict(lat0=S.lat0, lon0=S.lon0, x_m=xs.tolist(), y_m=ys.tolist(), shape=list(GX.shape),
                layout="heat[iy, ix], y north from -extent, x east from -extent (origin lower)", aircraft=[])
    run = run_shared if cfg["shared_l"] else run_each
    run(S, a, Ls, cam, (xs, ys, GX, GY, GZ), out, summary, grid)
    grid["camera_xy_m"] = [0.0, 0.0]
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "grid.json").write_text(json.dumps(grid))
    print(f"done  ({time.time() - t_start:.1f}s)")


if __name__ == "__main__":
    main()
