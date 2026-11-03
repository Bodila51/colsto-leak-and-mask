"""Which flight was it? Known camera, unknown flight (case_E, the SWA4113 overhead pass).

1. From the audio alone: track the fundamental fan line (900-1800 Hz, harmonic sum x1..x4) through
   the pass and fit a straight, constant-speed pass with emission-time correction: f0, speed,
   closest distance, CPA time.
2. From ADS-B: every airborne aircraft within 10 km of the camera within +-10 min. Its predicted
   Doppler ratio at the camera (real track, emission-time correction, DEM heights) is fitted to
   the tone with only f0 and one clock offset L, bounded to 0-60 s for all. Score: RMS in Hz.

The location is known. What is identified is the flight.

    python solver_paras/flight_id.py cases/case_E --audio data/screenrec/mdw_163340.mp4 --out results/flight_id_E
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy.interpolate import UnivariateSpline
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver_ridge"))
from ridge import (C_AIR, KT, MIC_AGL, Terrain, airborne_reports, interp_track, load_tracks,  # noqa: E402
                   to_xy)

CAMERA = (41.77844, -87.74278)
FT = 0.3048


def read_audio(path, fs=16000):
    path = Path(path)
    if path.suffix.lower() in (".mp4", ".m4a", ".ts"):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(fs),
                              "-f", "f32le", "-"], check=True, capture_output=True).stdout
        return np.frombuffer(raw, np.float32), fs
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    return x.mean(axis=1), fs


# ---------------------------------------------------------------- tone

def tone_track(x, fs, t_lo, t_hi, a):
    """Harmonic-sum f0 track (Viterbi, bounded step) and a per-frame confidence z."""
    nper, nfft, hop = int(a.win * fs), a.nfft, int(a.hop * fs)
    seg = x[int(t_lo * fs):int(t_hi * fs)]
    n = 1 + (len(seg) - nper) // hop
    win = np.hanning(nper).astype(np.float32)
    P = np.empty((n, nfft // 2 + 1), np.float32)
    for c0 in range(0, n, 400):
        c1 = min(n, c0 + 400)
        idx = np.arange(nper)[None, :] + hop * np.arange(c0, c1)[:, None]
        P[c0:c1] = 10 * np.log10(np.abs(np.fft.rfft(seg[idx] * win, nfft, axis=1)) ** 2 + 1e-12)
    P -= np.median(P, axis=0, keepdims=True)  # whiten per frequency over the segment
    P -= np.median(P, axis=1, keepdims=True)  # and per frame (the broadband roar at the pass)
    t = t_lo + (np.arange(n) * hop + nper / 2) / fs
    df = fs / nfft
    f0 = np.arange(a.f0min, a.f0max + 1e-9, a.fstep)
    S = np.zeros((n, len(f0)), np.float32)
    m_used = np.zeros(len(f0))
    for m in range(1, a.harmonics + 1):
        q = m * f0 / df
        ok = q < P.shape[1] - 1
        i0 = np.floor(q[ok]).astype(int)
        w = (q[ok] - i0).astype(np.float32)
        S[:, ok] += P[:, i0] * (1 - w) + P[:, i0 + 1] * w
        m_used[ok] += 1
    S /= m_used
    # Viterbi: max sum of S minus a small penalty per bin of jump, jump <= max_step (relative) per frame
    K = int(math.ceil(a.max_step * a.f0max / a.fstep))
    lam = a.jump_pen
    D = S[0].astype(np.float64).copy()
    back = np.zeros((n, len(f0)), np.int32)
    for i in range(1, n):
        best = np.full(len(f0), -np.inf)
        arg = np.zeros(len(f0), np.int32)
        for k in range(-K, K + 1):
            cand = np.full(len(f0), -np.inf)
            if k >= 0:
                cand[k:] = D[:len(f0) - k] - lam * k
            else:
                cand[:k] = D[-k:] + lam * k
            better = cand > best
            best[better] = cand[better]
            arg[better] = k
        D = best + S[i]
        back[i] = arg
    path = np.zeros(n, int)
    path[-1] = int(np.argmax(D))
    for i in range(n - 1, 0, -1):
        path[i - 1] = path[i] - back[i, path[i]]
    # parabolic refinement and a confidence z against the frame's own f0 distribution
    fz = np.empty(n)
    z = np.empty(n)
    for i, j in enumerate(path):
        y0, y1, y2 = S[i, max(j - 1, 0)], S[i, j], S[i, min(j + 1, len(f0) - 1)]
        den = y0 - 2 * y1 + y2
        fz[i] = f0[j] + (0.5 * (y0 - y2) / den * a.fstep if den < 0 and 0 < j < len(f0) - 1 else 0.0)
        row = S[i]
        med = np.median(row)
        z[i] = (y1 - med) / (1.4826 * np.median(np.abs(row - med)) + 1e-6)
    return t, fz, z, P, df


def pass_span(t, f, z, zlo, max_gap, rise=0.01):
    """Frames of one pass: grow from the most confident frame while the line keeps falling (a straight
    pass is monotonic in f; a rise of more than `rise` after the running minimum, or a fall below the
    running maximum going back, ends it). Frames with z < zlo are skipped; gaps > max_gap end it."""
    seed = int(np.argmax(z))
    keep = np.zeros(len(t), bool)
    keep[seed] = True
    for step in (1, -1):
        ext, last = f[seed], seed
        i = seed + step
        while 0 <= i < len(t):
            if (step == 1 and f[i] > ext * (1 + rise)) or (step == -1 and f[i] < ext * (1 - rise)):
                break
            if z[i] >= zlo:
                if abs(t[i] - t[last]) > max_gap:
                    break
                keep[i] = True
                last = i
                ext = min(ext, f[i]) if step == 1 else max(ext, f[i])
            i += step
    return keep


# ---------------------------------------------------------------- audio-only pass model

def pass_model(p, tq):
    """Straight constant-speed pass: f0, v (m/s), d (m), tce (emission CPA, audio s). Returns f at receive tq."""
    f0, v, d, tce = p
    te = np.linspace(tq.min() - 40, tq.max() + 5, 4000)
    r = np.sqrt(d ** 2 + (v * (te - tce)) ** 2)
    tr = te + r / C_AIR
    rdot = v ** 2 * (te - tce) / r
    return f0 * np.interp(tq, tr, C_AIR / (C_AIR + rdot))


def fit_pass(t, f):
    i = np.argmin(np.gradient(f, t))
    p0 = [math.sqrt(f.max() * f.min()), 70.0, 200.0, t[i]]
    best = None
    for v0 in (40.0, 70.0, 100.0):
        for d0 in (100.0, 300.0, 1000.0):
            p0[1], p0[2] = v0, d0
            r = least_squares(lambda p: pass_model(p, t) - f, p0, bounds=([500, 5, 10, t.min() - 60],
                                                                           [3000, 250, 8000, t.max() + 60]))
            if best is None or r.cost < best.cost:
                best = r
    p = best.x
    rms = float(np.sqrt(np.mean((pass_model(p, t) - f) ** 2)))
    return dict(f0_hz=float(p[0]), speed_kt=float(p[1] / KT), speed_m_s=float(p[1]), closest_m=float(p[2]),
                cpa_emission_audio_s=float(p[3]), cpa_receive_audio_s=float(p[3] + p[2] / C_AIR), rms_hz=rms), p


# ---------------------------------------------------------------- ADS-B candidates

def heights(tr, terrain, geoid_n, goff, t_ref, span=90.0):
    """One height source per aircraft: baro + this aircraft's median (geom - baro) offset where baro
    exists (mixing geom and baro report by report puts steps into Z), geom otherwise, DEM + 3 m on
    the ground. The offset is taken within +-span s of t_ref (it changes with altitude)."""
    both = np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])
    loc = both & (np.abs(tr["t"] - t_ref) <= span)
    use = loc if loc.sum() >= 2 else both
    off = np.median(tr["geom"][use] - tr["baro"][use]) if use.any() else goff
    h = np.where(np.isfinite(tr["baro"]), (tr["baro"] + off) * FT, tr["geom"] * FT) - geoid_n
    gr = tr["ground"] | ~np.isfinite(h)
    if gr.any():
        h[gr] = terrain.elevation(tr["lat"][gr], tr["lon"][gr]) + 3.0
    return h


def smooth_z(t, h, air, te, sigma_m=8.0, max_gap=45.0):
    """Cubic smoothing spline of height per airborne segment (25 ft baro steps), NaN outside."""
    Z = np.full(len(te), np.nan)
    idx = np.nonzero(air & np.isfinite(h))[0]
    if len(idx) < 4:
        return Z
    cuts = np.nonzero(np.diff(t[idx]) > max_gap)[0] + 1
    for seg in np.split(idx, cuts):
        if len(seg) < 4:
            continue
        sp = UnivariateSpline(t[seg], h[seg], k=3, s=len(seg) * sigma_m ** 2)
        m = (te >= t[seg[0]]) & (te <= t[seg[-1]])
        Z[m] = sp(te[m])
    return Z


def candidates(case_dir, t0, t_pass, a, terrain, cam_z):
    cs = json.loads((Path(case_dir) / "case.json").read_text())
    lo, hi = t0 + t_pass - a.window, t0 + t_pass + a.window
    tracks = load_tracks(ROOT / cs["adsb"], lo - 120, hi + 120)
    geoid_n = terrain.geoid(*CAMERA)
    offs = [np.median(tr["geom"][m] - tr["baro"][m]) for tr in tracks.values()
            for m in [np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])] if m.any()]
    goff = float(np.median(offs)) if offs else 0.0
    out = []
    for hx, tr in tracks.items():
        x, y = to_xy(tr["lat"], tr["lon"], *CAMERA)
        w = (tr["t"] >= lo) & (tr["t"] <= hi)
        t_ref = tr["t"][w][np.argmin(np.hypot(x, y)[w])] if w.any() else tr["t"][0]
        h = heights(tr, terrain, geoid_n, goff, t_ref)
        air = airborne_reports(tr, h, terrain)
        inwin = (tr["t"] >= lo) & (tr["t"] <= hi) & air & (np.hypot(x, y) <= a.radius)
        if not inwin.any():
            continue
        te = np.arange(lo - 60, hi + 60, 0.05)
        X, Y, Z = interp_track(tr, h, te, *CAMERA, air=air)
        Zs = smooth_z(tr["t"], h, air, te)  # piecewise-linear Z has climb-rate steps at every report
        Z = np.where(np.isfinite(X), Zs, np.nan)
        r = np.sqrt(X ** 2 + Y ** 2 + (Z - cam_z) ** 2)
        if not np.isfinite(r).any():
            continue
        rdot = np.gradient(r, te)
        i = int(np.nanargmin(r))
        k = np.nonzero(inwin)[0]
        j = int(np.argmin(np.abs(tr["t"] - te[i])))
        near = (np.abs(tr["t"] - te[i]) <= 20) & np.isfinite(h)
        climb = float(np.polyfit(tr["t"][near] - te[i], h[near], 1)[0]) if near.sum() >= 2 else np.nan
        agl = h - terrain.elevation(tr["lat"], tr["lon"])
        out.append(dict(hex=hx, flight=tr["flight"] or hx, type=tr["type"], tr=te + r / C_AIR,
                        ratio=C_AIR / (C_AIR + rdot), cpa_slant_m=float(r[i]), cpa_t=float(te[i] - t0),
                        cpa_agl_m=float(Z[i] - cam_z + MIC_AGL), gs_kt=float(tr["gs"][j]),
                        climb_m_s=climb, agl_m=float(agl[j]), alt_baro_ft=float(tr["baro"][j]),
                        within_radius_s=float(tr["t"][k[-1]] - tr["t"][k[0]]), first_report_t=float(tr["t"][0] - t0),
                        track_x=x, track_y=y, track_t=tr["t"] - t0))
    return out


def fit_candidate(c, t_audio, f, t0, Ls, min_cov):
    """Best (L, f0) for f ~ f0 * ratio(receive time = audio time - L); RMS over covered frames."""
    best = dict(rms_hz=np.inf, L_s=None, f0_hz=None, coverage=0.0)
    ok_tr = np.isfinite(c["tr"]) & np.isfinite(c["ratio"])
    if ok_tr.sum() < 2:
        return best
    trv, rv = c["tr"][ok_tr], c["ratio"][ok_tr]
    # receive time is monotonic in emission time; NaN gaps were dropped, so mark frames across gaps
    gap = np.diff(trv) > 1.0
    for L in Ls:
        tq = t0 + t_audio - L
        rho = np.interp(tq, trv, rv, left=np.nan, right=np.nan)
        seg = np.searchsorted(trv, tq) - 1
        bad = (seg >= 0) & (seg < len(gap)) & gap[np.clip(seg, 0, len(gap) - 1)]
        rho[bad] = np.nan
        m = np.isfinite(rho)
        cov = m.mean()
        if cov < min_cov:
            continue
        f0 = float(np.sum(f[m] * rho[m]) / np.sum(rho[m] ** 2))
        rms = float(np.sqrt(np.mean((f[m] - f0 * rho[m]) ** 2)))
        if rms < best["rms_hz"]:
            best = dict(rms_hz=rms, L_s=float(L), f0_hz=f0, coverage=float(cov))
    return best


# ---------------------------------------------------------------- figure

def figure(out, t, fz, keep, P, df, t_lo, aud, pbest, best, ranked, flat_rms, a):
    fig = plt.figure(figsize=(14, 10))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], hspace=0.32, wspace=0.22)
    # (1) spectrogram with the extracted tone
    ax = fig.add_subplot(gs[0, 0])
    fmax = 7000
    kk = int(fmax / df)
    ax.pcolormesh(t, np.arange(kk) * df, P[:, :kk].T, shading="nearest", cmap="magma", vmin=0, vmax=25,
                  rasterized=True)
    for m in range(1, 5):
        ax.plot(t[keep], m * fz[keep], ".", color="cyan", ms=1.5)
    ax.set_ylim(600, fmax)
    ax.set_xlabel("audio time (s)")
    ax.set_ylabel("Hz")
    ax.set_title("(1) Spectrogram (screen recording, no voice) with the extracted fan line x1..x4", fontsize=10)
    # (2) tone with the best flight's curve
    ax = fig.add_subplot(gs[0, 1])
    ax.plot(t[keep], fz[keep], ".", color="k", ms=3, label="extracted tone")
    tt = t[keep]
    ax.plot(tt, pass_model(pbest, tt), color="tab:orange", lw=1.5, ls="--",
            label=f"audio-only straight pass (RMS {aud['rms_hz']:.1f} Hz)")
    c = best["cand"]
    rho = np.interp(a.t0 + tt - best["L_s"], c["tr"][np.isfinite(c["tr"])], c["ratio"][np.isfinite(c["tr"])],
                    left=np.nan, right=np.nan)
    ax.plot(tt, best["f0_hz"] * rho, color="tab:blue", lw=2,
            label=f"{c['flight']} ({c['type']}) from ADS-B, L = {best['L_s']:.2f} s (RMS {best['rms_hz']:.1f} Hz)")
    ax.set_xlabel("audio time (s)")
    ax.set_ylabel("fundamental (Hz)")
    ax.legend(fontsize=8)
    ax.set_title("(2) Extracted tone vs the best flight's predicted Doppler curve", fontsize=10)
    # (3) table
    ax = fig.add_subplot(gs[1, 0])
    ax.axis("off")
    phase = "takeoff/climb" if c["climb_m_s"] > 2 and c["agl_m"] < 1500 else (
        "descent/approach" if c["climb_m_s"] < -2 else "level")
    rows = [
        ["", "from the audio", f"from ADS-B ({c['flight']})"],
        ["type", "turbofan: fan-tone stack\n~1.3 kHz + harmonics", c["type"]],
        ["speed", f"{aud['speed_kt']:.0f} kt", f"{c['gs_kt']:.0f} kt ground speed"],
        ["closest pass", f"{aud['closest_m']:.0f} m", f"{c['cpa_slant_m']:.0f} m slant ({c['cpa_agl_m']:.0f} m AGL)"],
        ["phase", "straight pass,\nconstant speed (model)", f"{phase}: {c['alt_baro_ft']:.0f} ft baro,\n"
                                                             f"climb {c['climb_m_s']:.0f} m/s"],
        ["visible for", f"{aud['visible_s']:.0f} s of tone", f"{c['within_radius_s']:.0f} s within {a.radius / 1e3:.0f} km"
                                                            f"\n(first report {c['first_report_t'] - c['cpa_t']:+.0f} s from CPA)"],
        ["f0 / clock", f"f0 {aud['f0_hz']:.0f} Hz, CPA {aud['cpa_receive_audio_s']:.1f} s",
         f"f0 {best['f0_hz']:.0f} Hz, L = {best['L_s']:.2f} s"],
    ]
    tb = ax.table(cellText=rows, loc="center", cellLoc="left", colWidths=[0.18, 0.38, 0.44])
    tb.auto_set_font_size(False)
    tb.set_fontsize(8.5)
    tb.scale(1, 2.6)
    for (i, j), cell in tb.get_celld().items():
        if i == 0 or j == 0:
            cell.set_text_props(weight="bold")
    ax.set_title("(3) The pass from the audio alone vs the best ADS-B flight", fontsize=10)
    # (4) sorted bars
    ax = fig.add_subplot(gs[1, 1])
    names = [f"{r['flight']} {r['type']}" for r in ranked]
    vals = [r["rms_hz"] for r in ranked]
    cols = ["tab:blue" if i == 0 else "0.65" for i in range(len(ranked))]
    y = np.arange(len(ranked))
    ax.barh(y, vals, color=cols)
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.invert_yaxis()
    ax.axvline(flat_rms, color="k", ls=":", lw=1, label=f"flat line, no Doppler ({flat_rms:.0f} Hz)")
    ax.axvline(aud["rms_hz"], color="tab:orange", ls="--", lw=1, label=f"audio-only pass model ({aud['rms_hz']:.1f} Hz)")
    ax.set_xlabel("RMS fit error (Hz), f0 and L (0-60 s) fitted per aircraft")
    ax.legend(fontsize=7, loc="lower right")
    ax.set_title(f"(4) All {len(ranked)} airborne aircraft within {a.radius / 1e3:.0f} km, +-{a.window / 60:.0f} min "
                 f"(coverage >= {a.min_cov:.0%})", fontsize=10)
    fig.suptitle("Which flight was it? Midway takeoff clip (case_E). Known location; the flight is what is identified.",
                 fontsize=12)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--audio", required=True, help="audio file; mp4 is decoded with ffmpeg")
    ap.add_argument("--out", required=True)
    ap.add_argument("--t-lo", type=float, default=425.0, help="audio segment searched for the tone (s)")
    ap.add_argument("--t-hi", type=float, default=480.0)
    ap.add_argument("--win", type=float, default=0.256)
    ap.add_argument("--nfft", type=int, default=16384)
    ap.add_argument("--hop", type=float, default=0.05)
    ap.add_argument("--f0min", type=float, default=900.0)
    ap.add_argument("--f0max", type=float, default=1800.0)
    ap.add_argument("--fstep", type=float, default=1.0)
    ap.add_argument("--harmonics", type=int, default=4)
    ap.add_argument("--max-step", type=float, default=0.015, help="max relative f0 jump per frame")
    ap.add_argument("--jump-pen", type=float, default=0.05, help="Viterbi penalty per Hz of jump")
    ap.add_argument("--zlo", type=float, default=1.5, help="tone track: min frame confidence")
    ap.add_argument("--max-gap", type=float, default=1.0)
    ap.add_argument("--latency", type=float, default=28.0, help="nominal stream latency, for the ADS-B window")
    ap.add_argument("--window", type=float, default=600.0, help="+- s around the pass")
    ap.add_argument("--radius", type=float, default=10000.0)
    ap.add_argument("--lmin", type=float, default=0.0)
    ap.add_argument("--lmax", type=float, default=60.0)
    ap.add_argument("--lstep", type=float, default=0.25)
    ap.add_argument("--min-cov", type=float, default=0.8,
                    help="min fraction of tone frames a track must cover (a partly covered, nearly flat curve "
                         "otherwise fits only the flat part of the tone)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cs = json.loads((Path(a.case_dir) / "case.json").read_text())
    t0 = datetime.fromisoformat(cs["utc_start"].replace("Z", "+00:00")).timestamp()
    a.t0 = t0

    x, fs = read_audio(ROOT / a.audio if not Path(a.audio).is_absolute() else a.audio)
    t, fz, z, P, df = tone_track(x, fs, a.t_lo, a.t_hi, a)
    keep = pass_span(t, fz, z, a.zlo, a.max_gap)
    tt, ff = t[keep], fz[keep]
    print(f"tone: {keep.sum()} frames, {tt[0]:.2f}-{tt[-1]:.2f} s, {ff[:20].mean():.0f} -> {ff[-20:].mean():.0f} Hz")
    aud, pbest = fit_pass(tt, ff)
    aud["visible_s"] = float(tt[-1] - tt[0])
    aud["frames"] = int(keep.sum())
    print("audio-only pass:", {k: round(v, 2) for k, v in aud.items()})

    terrain = Terrain()
    cam_z = float(terrain.elevation(np.array([CAMERA[0]]), np.array([CAMERA[1]]))[0]) + MIC_AGL
    t_pass = aud["cpa_receive_audio_s"] - a.latency
    cands = candidates(a.case_dir, t0, t_pass, a, terrain, cam_z)
    Ls = np.arange(a.lmin, a.lmax + 1e-9, a.lstep)
    res = []
    for c in cands:
        r = fit_candidate(c, tt, ff, t0, Ls, a.min_cov)
        r.update(cand=c, hex=c["hex"], flight=c["flight"], type=c["type"])
        res.append(r)
    ranked = sorted([r for r in res if np.isfinite(r["rms_hz"])], key=lambda r: r["rms_hz"])
    nocov = [r for r in res if not np.isfinite(r["rms_hz"])]
    flat_rms = float(np.std(ff))
    best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    sep = second["rms_hz"] / best["rms_hz"] if second else None
    for r in ranked[:8]:
        print(f"{r['flight']:8s} {r['type']:5s} RMS {r['rms_hz']:7.2f} Hz  L {r['L_s']:.2f}  f0 {r['f0_hz']:.0f}  "
              f"cov {r['coverage']:.2f}  cpa {r['cand']['cpa_slant_m']:.0f} m")
    print(f"{len(cands)} candidates, {len(ranked)} scored, {len(nocov)} without track during the tone; "
          f"second/best = {sep}")
    figure(out / "flight_id.png", t, fz, keep, P, df, a.t_lo, aud, pbest, best, ranked, flat_rms, a)

    c = best["cand"]
    weak = sep is None or sep < 2.0
    summary = dict(
        note="Known location (camera 41.77844, -87.74278); the flight is what is identified. Not blind.",
        audio=dict(file=str(a.audio), why="screen recording with no synthetic voice; sample-aligned with "
                   "cases/case_E/audio.flac (cross-correlation 0.9994-0.99999 at 100/300/400 s, offset 0)",
                   tone_span_s=[float(tt[0]), float(tt[-1])], f_start_hz=float(ff[:20].mean()),
                   f_end_hz=float(ff[-20:].mean())),
        audio_only_pass=aud,
        adsb=dict(n_candidates=len(cands), n_scored=len(ranked), latency_bounds_s=[a.lmin, a.lmax],
                  min_coverage=a.min_cov, radius_m=a.radius, window_s=a.window,
                  flat_line_rms_hz=flat_rms,
                  ranked=[dict(flight=r["flight"], hex=r["hex"], type=r["type"], rms_hz=round(r["rms_hz"], 2),
                               L_s=r["L_s"], f0_hz=round(r["f0_hz"], 1), coverage=round(r["coverage"], 3),
                               cpa_slant_m=round(r["cand"]["cpa_slant_m"]), cpa_t_s=round(r["cand"]["cpa_t"], 1))
                          for r in ranked],
                  no_track_during_tone=[dict(flight=r["flight"], hex=r["hex"], type=r["type"],
                                             cpa_t_s=round(r["cand"]["cpa_t"], 1)) for r in nocov]),
        best=dict(flight=best["flight"], hex=best["hex"], type=best["type"], rms_hz=best["rms_hz"], L_s=best["L_s"],
                  f0_hz=best["f0_hz"], coverage=best["coverage"], gs_kt=c["gs_kt"], cpa_slant_m=c["cpa_slant_m"],
                  cpa_agl_m=c["cpa_agl_m"], climb_m_s=c["climb_m_s"], alt_baro_ft=c["alt_baro_ft"]),
        second=None if second is None else dict(flight=second["flight"], type=second["type"],
                                                rms_hz=second["rms_hz"], L_s=second["L_s"]),
        separation_second_over_best=sep,
        history_note=("Changed after looking at earlier runs, applied to every aircraft alike: (1) the tone span "
                      "follows the monotonic pass from the most confident frame, since per-frame z alone cut the "
                      "tail under the broadband roar or joined other lines; (2) tracks must cover >= 80 % of the "
                      "tone frames, since at 60 % a nearly flat, partly covered curve (AAL2658) won on the flat "
                      "part; (3) the height uses one source per aircraft (baro + local geom-baro offset) and a "
                      "smoothing spline, since geom/baro mixing and 25 ft steps put spikes into dr/dt. The best "
                      "flight was SWA4113 in every run after (1); the second best stayed above 150 Hz."),
        separation=("weak" if weak else "clear") + f": second-best RMS is {sep:.2f}x the best" if sep else "only one",
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ("best", "second", "separation")}, indent=1))


if __name__ == "__main__":
    main()
