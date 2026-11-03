"""Paras-style known-camera check (NOT blind): hand-picked aircraft, revealed camera.

This mirrors the structure of Paras Chopra's viral replication. The camera position is known and
each aircraft is picked by hand. For every aircraft:

1. A latency scan. At the known camera, the emission-time-corrected Doppler curve c/(c + dr/dte)
   over a short window around closest approach (CPA) is scored on the whitened log-frequency
   spectrogram (max over the emitted f0, with x2/x3 harmonics), for stream latency L = 0..60 s.
2. The spectrogram, with the predicted curve at the best shared L.
3. A curve-fit heat map on a +-6 km grid at 100 m around the camera, at that L. The window is fixed
   in receive time around the camera's CPA, so this is a fit check, not a search.

Overhead passes leave the two mirror bands (left and right of the track) nearly merged on the
track line. Along-track position trades off against L, which is fixed here from the known camera.
Geometry, terrain, ADS-B parsing and the spectrogram are reused from solver_ridge/ridge.py.

    python solver_paras/paras.py cases/case_E --out results/paras_setup_E
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

# case_E: owner-published camera (revealed), and the six 13C departures picked by hand.
CAMERA = (41.77844, -87.74278)
PICKS = ["ac13d6", "abaeb2", "aa9b42", "accb50", "ac33a3", "accf07"]
CLOCK_L = 28.0  # burned-in stream clock 08:29:39 CST at recording t = 290 s
VOICE = (430.0, 474.0)  # synthetic voice mixed in (audio s)


# ---------------------------------------------------------------- track

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
def fit_kernel(gx, gy, gz, te, ax, ay, az, c, tq, rows, Wp, pad, dlog, h2, h3, k0lo, k0hi, nmin,
               s_out, k0_out, n_out):
    """For each point: Doppler log-ratio at the receive times tq (frames rows), the mean whitened
    energy along it for every emitted bin k0 (+x2, +x3), max over k0."""
    G = gx.shape[0]
    N = te.shape[0]
    Q = tq.shape[0]
    K = Wp.shape[1] - 2 * pad
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
            if k0 + h2 < K:
                s += S[k0 + h2]
                m += 1
            if h3 > 0 and k0 + h3 < K:
                s += S[k0 + h3]
                m += 1
            v = s / (cnt * math.sqrt(m))
            if v > best:
                best = v
                bk = k0
        s_out[g] = best
        k0_out[g] = bk


# ---------------------------------------------------------------- setup

class Setup:
    def __init__(self, case_dir, a):
        self.a = a
        cs = json.loads((Path(case_dir) / "case.json").read_text())
        self.t0 = datetime.fromisoformat(cs["utc_start"].replace("Z", "+00:00")).timestamp()
        x, fs = sf.read(ROOT / cs["audio"], dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        self.dur = len(x) / fs
        hop = int(round(a.hop * fs))
        self.hop_s = hop / fs
        self.W, self.tau, self.fk = whitened_spectrogram(x, fs, a.fmin, a.fmax, int(round(a.win * fs)),
                                                         hop, a.dlog)
        if a.ridge > 1:  # keep narrow tonal ridges only: subtract the local mean over +-ridge/2 log bins
            self.W = (self.W - uniform_filter1d(self.W, a.ridge, axis=1, mode="nearest")).astype(np.float32)
        self.T, self.K = self.W.shape
        self.pad = int(math.ceil(math.log(1.45) / a.dlog))
        self.Wp = np.zeros((self.T, self.K + 2 * self.pad), np.float32)
        self.Wp[:, self.pad:self.pad + self.K] = self.W
        self.h2 = int(round(math.log(2) / a.dlog))
        self.h3 = int(round(math.log(3) / a.dlog))
        self.k0lo = int(round(math.log(a.f0min / a.fmin) / a.dlog))
        self.k0hi = min(self.K - 1, int(round(math.log(a.f0max / a.fmin) / a.dlog)))
        self.clean = ~((self.tau >= VOICE[0]) & (self.tau <= VOICE[1]))

        self.lat0, self.lon0 = CAMERA
        self.terrain = Terrain()
        self.geoid_n = self.terrain.geoid(self.lat0, self.lon0)
        self.cam_z = float(self.terrain.elevation(np.array([self.lat0]), np.array([self.lon0]))[0]) + MIC_AGL
        tracks = load_tracks(ROOT / cs["adsb"], self.t0 - 300, self.t0 + self.dur + 300)
        offs = [np.median(tr["geom"][m] - tr["baro"][m]) for tr in tracks.values()
                for m in [np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])] if m.any()]
        goff = float(np.median(offs)) if offs else 0.0
        self.ac = []
        for hx in PICKS:
            tr = tracks[hx]
            keep = (tr["t"] >= self.t0 - 60) & (tr["t"] <= self.t0 + self.dur + 60) & ~tr["ground"]
            tr = {k: (v[keep] if isinstance(v, np.ndarray) else v) for k, v in tr.items()}
            h = track_heights(tr, self.terrain, self.geoid_n, goff)
            rx, ry = to_xy(tr["lat"], tr["lon"], self.lat0, self.lon0)
            rt = tr["t"]
            tr, h, climb = extend_back(tr, h, self.terrain, self.lat0, self.lon0)
            te = np.arange(self.t0 - 120, self.t0 + self.dur + 60, 0.02)
            X, Y, Z = interp_track(tr, h, te, self.lat0, self.lon0)
            r = np.sqrt(X ** 2 + Y ** 2 + (Z - self.cam_z) ** 2)
            i = int(np.nanargmin(r))
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
                first_report_s=float(rt[0] - te[i]), climb=climb))

    def frames(self, ac, L, keep_voice=False):
        """Audio rows and receive times for the window cpa_tr +- half at latency L (voice frames dropped)."""
        tq_all = self.tau + self.t0 - L
        rows = np.nonzero((tq_all >= ac["cpa_tr"] - self.a.half) & (tq_all <= ac["cpa_tr"] + self.a.half)
                          & (self.clean | keep_voice))[0]
        return rows, tq_all[rows]

    def fit(self, ac, gx, gy, gz, L, keep_voice=False):
        rows, tq = self.frames(ac, L, keep_voice)
        G = len(gx)
        s = np.full(G, np.nan)
        k0 = np.full(G, -1, np.int64)
        n = np.zeros(G, np.int64)
        nmin = int(round(self.a.min_frac * 2 * self.a.half / self.hop_s))
        if len(rows) >= nmin:
            fit_kernel(gx, gy, gz, ac["te"], ac["X"], ac["Y"], ac["Z"], C_AIR, tq, rows.astype(np.int64),
                       self.Wp, self.pad, self.a.dlog, self.h2, self.h3, self.k0lo, self.k0hi, nmin, s, k0, n)
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
    n = len(S.ac)
    fig, axs = plt.subplots(n + 1, 1, figsize=(9, 1.6 * (n + 1)), sharex=True)
    for ax, ac in zip(axs, S.ac):
        sc = ac["scan"]
        col = "tab:blue" if ac["hex"] in used else "0.6"
        ax.plot(Ls, sc, color=col, lw=1.2)
        ax.axvline(bestL, color="tab:red", lw=1)
        ax.axvline(CLOCK_L, color="k", ls=":", lw=1)
        note = "" if ac["hex"] in used else f"  [skipped: {skipped[ac['hex']]}]"
        ax.set_title(label(ac) + note, fontsize=9, loc="left")
        ax.set_ylabel("fit")
    tot = np.nansum([ac["scan"] for ac in S.ac if ac["hex"] in used], axis=0)
    axs[-1].plot(Ls, tot, color="k", lw=1.5)
    axs[-1].axvline(bestL, color="tab:red", lw=1, label=f"best shared L = {bestL:.2f} s")
    axs[-1].axvline(CLOCK_L, color="k", ls=":", lw=1, label=f"burned-in clock L ~ {CLOCK_L:.0f} s")
    axs[-1].set_title("sum over used aircraft", fontsize=9, loc="left")
    axs[-1].set_xlabel("stream latency L (s)")
    axs[-1].legend(fontsize=8, loc="upper right")
    fig.suptitle("Latency scan at the KNOWN camera (not blind, hand-picked aircraft)", fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


def plot_spec(S, ac, L, k0, out):
    t_c = ac["cpa_tr"] - S.t0 + L
    sel = (S.tau >= t_c - S.a.half - 5) & (S.tau <= t_c + S.a.half + 5)
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.pcolormesh(S.tau[sel], S.fk, S.W[sel].T, shading="nearest", cmap="magma", vmin=-1, vmax=3, rasterized=True)
    ax.set_yscale("log")
    ax.set_ylim(S.fk[0], S.fk[-1])
    t_a, lr = S.curve(ac, L)
    keep = (t_a >= t_c - S.a.half) & (t_a <= t_c + S.a.half)
    if k0 >= 0:
        f0 = S.fk[0] * math.exp(k0 * S.a.dlog)
        for h, ls in ((1, "-"), (2, "--"), (3, ":")):
            ax.plot(t_a[keep], h * f0 * np.exp(lr[keep]), color="cyan", lw=1.3, ls=ls,
                    label=f"x{h}" if h > 1 else f"predicted, f0 = {f0:.0f} Hz")
    ax.axvspan(*VOICE, color="w", alpha=0.25, lw=0)
    ax.set_xlim(S.tau[sel][0], S.tau[sel][-1])
    ax.set_yticks([1000, 1500, 2000, 3000, 4000, 6000])
    ax.set_yticklabels(["1k", "1.5k", "2k", "3k", "4k", "6k"])
    ax.set_xlabel("audio time (s)")
    ax.set_ylabel("Hz (whitened, ridge-filtered)")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_title(f"{label(ac)}: closest {ac['cpa_r']:.0f} m slant, {ac['cpa_hor']:.0f} m horizontal, "
                 f"{ac['cpa_agl']:.0f} m AGL   L = {L:.2f} s", fontsize=10)
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fmin", type=float, default=800.0)
    ap.add_argument("--fmax", type=float, default=7900.0)
    ap.add_argument("--f0min", type=float, default=1000.0)
    ap.add_argument("--f0max", type=float, default=6000.0)
    ap.add_argument("--win", type=float, default=0.15)
    ap.add_argument("--hop", type=float, default=0.025)
    ap.add_argument("--dlog", type=float, default=0.005)
    ap.add_argument("--ridge", type=int, default=31, help="local-mean width in log bins (0 = off)")
    ap.add_argument("--half", type=float, default=10.0, help="window around CPA receive time, +-s")
    ap.add_argument("--min-frac", type=float, default=0.6, help="min fraction of window frames scored")
    ap.add_argument("--lmin", type=float, default=0.0)
    ap.add_argument("--lmax", type=float, default=60.0)
    ap.add_argument("--lstep", type=float, default=0.25)
    ap.add_argument("--extent", type=float, default=6000.0)
    ap.add_argument("--step", type=float, default=100.0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    S = Setup(a.case_dir, a)
    print(f"spectrogram {S.W.shape}, camera ground {S.cam_z - MIC_AGL:.1f} m (DEM)  ({time.time() - t_start:.1f}s)")

    # 1. latency scans at the known camera
    Ls = np.arange(a.lmin, a.lmax + 1e-9, a.lstep)
    cam = (np.zeros(1), np.zeros(1), np.full(1, S.cam_z))
    skipped = {}
    for ac in S.ac:
        ac["scan"] = np.array([S.fit(ac, *cam, L)[0][0] for L in Ls])
        lo, hi = ac["cpa_tr"] - S.t0 + CLOCK_L - a.half, ac["cpa_tr"] - S.t0 + CLOCK_L + a.half
        if hi > S.dur or lo < 0:
            skipped[ac["hex"]] = f"window {lo:.0f}-{hi:.0f} s is past the end of the audio ({S.dur:.0f} s)"
        elif lo < VOICE[1] and hi > VOICE[0]:
            skipped[ac["hex"]] = f"window {lo:.0f}-{hi:.0f} s falls in the voice ({VOICE[0]:.0f}-{VOICE[1]:.0f} s)"
        print(f"{ac['flight']:8s} {ac['type']:5s} CPA {ac['cpa_tr'] - S.t0:6.1f}s  {ac['cpa_r']:.0f} m slant  "
              f"track {ac['track_deg']:.0f}  lateral {ac['lateral']:.0f} m  first report {ac['first_report_s']:+.1f}s "
              f"climb {ac['climb']:.1f} m/s  scan max {np.nanmax(ac['scan']):.2f} "
              f"at L {Ls[np.nanargmax(ac['scan'])]:.2f}  {skipped.get(ac['hex'], '')}")
    used = [ac["hex"] for ac in S.ac if ac["hex"] not in skipped]
    tot = np.nansum([ac["scan"] for ac in S.ac if ac["hex"] in used], axis=0)
    bestL = float(Ls[int(np.nanargmax(tot))])
    print(f"shared L = {bestL:.2f} s (burned-in clock ~{CLOCK_L:.0f} s)  ({time.time() - t_start:.1f}s)")
    plot_scan(S, Ls, used, skipped, bestL, out / "latency_scan.png")

    # 2-3. spectrogram and heat map per used aircraft at the shared L
    xs = np.arange(-a.extent, a.extent + 1e-6, a.step)
    ys = xs.copy()
    GX, GY = np.meshgrid(xs, ys)
    lat, lon = to_ll(GX.ravel(), GY.ravel(), S.lat0, S.lon0)
    GZ = S.terrain.elevation(lat, lon) + MIC_AGL
    summary = dict(note="NOT blind: known (revealed) camera, hand-picked aircraft, Paras-style setup",
                   camera=dict(lat=CAMERA[0], lon=CAMERA[1], z_m=S.cam_z), L_s=bestL, L_clock_s=CLOCK_L,
                   params=vars(a), aircraft=[], skipped=[])
    heats = []
    grid = dict(lat0=S.lat0, lon0=S.lon0, x_m=xs.tolist(), y_m=ys.tolist(), shape=list(GX.shape),
                layout="heat[iy, ix], y north from -extent, x east from -extent (origin lower)",
                L_s=bestL, aircraft=[])
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
        H, _, _ = S.fit(ac, GX.ravel(), GY.ravel(), GZ, bestL)
        H = H.reshape(GX.shape).astype(np.float32)
        np.save(out / f"heat_{cs}.npy", H)
        heats.append(H)
        iy, ix = np.unravel_index(np.nanargmax(H), H.shape)
        err = math.hypot(xs[ix], ys[iy])
        plat, plon = to_ll(xs[ix], ys[iy], S.lat0, S.lon0)
        plot_heat(S, H, xs, ys, [ac], f"{label(ac)}: curve fit at L = {bestL:.2f} s\n"
                  f"not blind: known camera, hand-picked aircraft", out / f"heat_{cs}.png")
        rec = dict(hex=ac["hex"], flight=cs, type=ac["type"], cpa_audio_s=ac["cpa_tr"] - S.t0 + bestL,
                   cpa_slant_m=ac["cpa_r"], cpa_horizontal_m=ac["cpa_hor"], cpa_agl_m=ac["cpa_agl"],
                   track_deg=ac["track_deg"], camera_track_offset_m=ac["lateral"],
                   first_adsb_report_rel_cpa_s=ac["first_report_s"], climb_m_s=ac["climb"],
                   mirror_separation_m=2 * ac["lateral"], f0_hz=float(S.fk[0] * math.exp(k0[0] * a.dlog)),
                   fit_at_camera=float(s0[0]), fit_peak=float(H[iy, ix]),
                   peak=dict(lat=float(plat), lon=float(plon), x_m=float(xs[ix]), y_m=float(ys[iy])),
                   peak_error_m=round(err, 1), scan_best_L_s=float(Ls[np.nanargmax(ac["scan"])]))
        summary["aircraft"].append(rec)
        m = (np.abs(ac["rx"]) < 8e3) & (np.abs(ac["ry"]) < 8e3)
        grid["aircraft"].append(dict(hex=ac["hex"], flight=cs, heat=f"heat_{cs}.npy",
                                     track_x_m=np.round(ac["rx"][m], 1).tolist(),
                                     track_y_m=np.round(ac["ry"][m], 1).tolist(),
                                     track_t=np.round(ac["rt"][m] - S.t0, 2).tolist()))
        print(f"{cs:8s} peak {err:6.0f} m from camera (fit {H[iy, ix]:.2f} vs {s0[0]:.2f} at camera)  "
              f"({time.time() - t_start:.1f}s)")

    C = np.nansum(heats, axis=0).astype(np.float32)
    np.save(out / "heat_combined.npy", C)
    iy, ix = np.unravel_index(np.nanargmax(C), C.shape)
    err = math.hypot(xs[ix], ys[iy])
    plat, plon = to_ll(xs[ix], ys[iy], S.lat0, S.lon0)
    plot_heat(S, C, xs, ys, [ac for ac in S.ac if ac["hex"] in used],
              f"Sum over {len(heats)} hand-picked departures, L = {bestL:.2f} s\n"
              f"not blind: known camera, hand-picked aircraft", out / "heat_combined.png")
    summary["combined"] = dict(peak=dict(lat=float(plat), lon=float(plon), x_m=float(xs[ix]), y_m=float(ys[iy])),
                               peak_error_m=round(err, 1), aircraft=[r["flight"] for r in summary["aircraft"]])
    far = np.where(np.hypot(GX, GY) > 1000, C, -np.inf)  # best point more than 1 km from the camera
    jy, jx = np.unravel_index(np.argmax(far), C.shape)
    summary["combined"].update(fit_peak=float(C[iy, ix]), fit_median=float(np.nanmedian(C)),
                               runner_up_beyond_1km=dict(x_m=float(xs[jx]), y_m=float(ys[jy]), fit=float(C[jy, jx]),
                                                         dist_m=round(math.hypot(xs[jx], ys[jy]), 1),
                                                         ratio_to_peak=round(float(C[jy, jx] / C[iy, ix]), 3)))
    summary["track_note"] = ("ADS-B coverage of each departure starts 0-9 s before the camera; earlier the track "
                             "is extrapolated back along the first 6 s of reports (ground velocity and climb), "
                             "clamped to the runway (DEM + 3 m). Takeoff-roll acceleration is ignored.")
    summary["history_note"] = ("A first run without the ridge filter (--ridge 0) gave L = 33.75 s and peaks 0.1-8.5 km "
                               "off (combined 3.6 km): broadband pass noise outscored the tones. The filter was "
                               "added after looking at that run; L is still chosen only by the Doppler fit.")
    summary["mirror_note"] = ("Overhead passes: the camera is within tens of metres of each track line, so the "
                              "left/right mirror bands nearly merge on the track line and cannot be told apart.")
    grid["combined"] = "heat_combined.npy"
    grid["camera_xy_m"] = [0.0, 0.0]
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "grid.json").write_text(json.dumps(grid))
    print(f"combined peak {err:.0f} m from camera  ({time.time() - t_start:.1f}s)")


if __name__ == "__main__":
    main()
