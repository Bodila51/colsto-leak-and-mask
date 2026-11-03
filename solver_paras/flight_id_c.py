"""Which flight was it? The camera location is KNOWN; the flight is what gets identified (case_C).

1. Audio only. In a segment around one pass, a whitened STFT (60-400 Hz) and a Viterbi path give the
   dominant narrow line. A straight, level, constant-speed pass with the emission-time correction,
   f(t) = f0 c / (c + v_r(te)), t = te + r(te)/c, is fitted to that track alone: f0, speed, closest
   (slant) distance and CPA time. No ADS-B is used.
2. ADS-B. Every aircraft within --radius (horizontal) of the camera within +-10 min of the pass gets
   its predicted Doppler curve at the camera, from its real track (emission time, DEM camera height).
   Only f0 (closed form) and the clock offset L (one range for all) are fitted, and the RMS error in Hz
   against the extracted track is reported.
3. flight_id.png (spectrogram + track, best curve with x2, audio vs ADS-B table, error bars) and
   summary.json.

(solver_paras/flight_id.py is the case_E version, written by another session.)

    python solver_paras/flight_id_c.py cases/case_C --out results/flight_id_C --seg 460 860
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver_ridge"))
sys.path.insert(0, str(ROOT / "solver_paras"))
from ridge import C_AIR, KT, MIC_AGL, Terrain, interp_track, load_tracks, track_heights  # noqa: E402
from paras import CASES, despike  # noqa: E402

NOTE = "known location; the flight is what is identified"


# ---------------------------------------------------------------- audio

def stft_band(x, fs, t_lo, win, hop, pad, fmin, fmax):
    """dB power in fmin..fmax (zero-padded FFT), frame centre times (audio s), frequencies."""
    n, h = int(round(win * fs)), int(round(hop * fs))
    nfft = n * pad
    f = np.fft.rfftfreq(nfft, 1 / fs)
    band = np.nonzero((f >= fmin) & (f <= fmax))[0]
    nf = 1 + (len(x) - n) // h
    w = np.hanning(n).astype(np.float32)
    D = np.empty((nf, len(band)), np.float32)
    for c0 in range(0, nf, 100):
        c1 = min(nf, c0 + 100)
        idx = np.arange(n)[None, :] + h * np.arange(c0, c1)[:, None]
        P = np.abs(np.fft.rfft(x[idx] * w, n=nfft, axis=1)[:, band]) ** 2
        D[c0:c1] = 10 * np.log10(P + 1e-12)
    return D, t_lo + (np.arange(nf) * h + n / 2) / fs, f[band]


def whiten(D, ridge_hz, df):
    """Steady lines out (per-frequency median over time), narrow lines kept (local mean over
    ridge_hz subtracted), then each frame in robust units."""
    S = D - np.median(D, axis=0, keepdims=True)
    S = S - uniform_filter1d(S, max(3, int(round(ridge_hz / df))), axis=1, mode="nearest")
    med = np.median(S, axis=1, keepdims=True)
    return (S - med) / (1.4826 * np.median(np.abs(S - med), axis=1, keepdims=True) + 1e-6)


def viterbi(S, jmax, pen):
    """Path k[t] maximising sum S[t, k] - pen |k[t] - k[t-1]| with |jump| <= jmax bins."""
    T, K = S.shape
    acc = S[0].astype(np.float64)
    back = np.zeros((T, K), np.int16)
    for t in range(1, T):
        best = np.full(K, -np.inf)
        arg = np.zeros(K, np.int16)
        for j in range(-jmax, jmax + 1):
            cand = np.full(K, -np.inf)
            if j >= 0:
                cand[:K - j] = acc[j:] - pen * abs(j)
            else:
                cand[-j:] = acc[:K + j] - pen * abs(j)
            m = cand > best
            best[m], arg[m] = cand[m], j
        acc = best + S[t]
        back[t] = arg
    k = np.empty(T, np.int64)
    k[-1] = int(np.argmax(acc))
    for t in range(T - 1, 0, -1):
        k[t - 1] = k[t] + back[t, k[t]]
    return k


def pass_model(p, t):
    """Received frequency at receive times t for a straight level pass: f0, speed v (m/s), closest
    slant distance d (m), CPA emission time tc (same clock as t)."""
    f0, v, d, tc = p
    te = t - d / C_AIR
    for _ in range(30):  # t = te + r(te)/c, a contraction since v < c
        te = t - np.sqrt(d ** 2 + (v * (te - tc)) ** 2) / C_AIR
    r = np.sqrt(d ** 2 + (v * (te - tc)) ** 2)
    return f0 * C_AIR / (C_AIR + v ** 2 * (te - tc) / r)


# ---------------------------------------------------------------- ADS-B

def predicted(tr, h, cam_z, lat0, lon0, te, max_gap):
    """Receive times (real clock) and Doppler ratio at the camera for emission times te."""
    X, Y, Z = interp_track(tr, h, te, lat0, lon0, max_gap=max_gap)
    r = np.sqrt(X ** 2 + Y ** 2 + (Z - cam_z) ** 2)
    ratio = C_AIR / (C_AIR + np.gradient(r, te))
    return te + r / C_AIR, ratio, X, Y, Z, r


def fit_flight(t_rx, ratio, tau, fobs, t0, Ls, min_cover):
    """For each L: f0 by least squares (closed form), RMS in Hz on the covered frames; best L."""
    ok = np.isfinite(t_rx) & np.isfinite(ratio)
    best = dict(rms=np.inf)
    if ok.sum() < 10:
        return best
    tr, rt = t_rx[ok], ratio[ok]
    o = np.argsort(tr)
    tr, rt = tr[o], rt[o]
    for L in Ls:
        q = t0 + tau - L  # real receive time of each audio frame
        g = np.interp(q, tr, rt, left=np.nan, right=np.nan)
        gap = np.interp(q, tr[1:], np.diff(tr), left=np.inf, right=np.inf) > 2.0  # inside a track gap
        g[gap] = np.nan
        m = np.isfinite(g)
        if m.mean() < min_cover:
            continue
        f0 = float(np.sum(fobs[m] * g[m]) / np.sum(g[m] ** 2))
        rms = float(np.sqrt(np.mean((fobs[m] - f0 * g[m]) ** 2)))
        if rms < best["rms"]:
            best = dict(rms=rms, L=float(L), f0=f0, cover=float(m.mean()))
    return best


def extract(S, k, tau, f, sal, nsig):
    """The tone: the run of the path around its salience maximum that stays >= median + nsig sigma
    (the path picks maxima, so noise sits at a positive level); sub-bin by a parabola on S."""
    med = float(np.median(sal))
    thr = med + nsig * 1.4826 * float(np.median(np.abs(sal - med)))
    i0 = i1 = int(np.argmax(sal))
    while i0 > 0 and sal[i0 - 1] >= thr:
        i0 -= 1
    while i1 < len(sal) - 1 and sal[i1 + 1] >= thr:
        i1 += 1
    rows = np.arange(i0, i1 + 1)
    kk = k[rows].astype(float)
    inner = (k[rows] > 0) & (k[rows] < S.shape[1] - 1)
    y0, y1, y2 = (S[rows[inner], k[rows[inner]] + j] for j in (-1, 0, 1))
    den = y0 - 2 * y1 + y2
    kk[inner] += np.where(den < 0, 0.5 * (y0 - y2) / np.where(den < 0, den, -1), 0.0).clip(-0.5, 0.5)
    return tau[rows], f[0] + kk * (f[1] - f[0])


def fit_audio(t_trk, f_trk, a, t0):
    """Level-pass fit to the track alone, from a few starts (speed and distance trade off)."""
    fmid = math.sqrt(f_trk.max() * f_trk.min())
    tmid = t_trk[np.argmin(np.abs(f_trk - fmid))]
    lo, hi = [a.fmin / 2, 10.0, 50.0, t_trk[0] - 120], [a.fmax * 2, 330.0, 30000.0, t_trk[-1] + 120]
    res = min((least_squares(lambda p: pass_model(p, t_trk) - f_trk, [fmid, v0, d0, tmid], bounds=(lo, hi),
                             x_scale=[10, 20, 1000, 5])
               for v0 in (50.0, 100.0, 150.0, 220.0) for d0 in (1000.0, 3000.0, 8000.0)), key=lambda r: r.cost)
    f0a, va, da, tca = res.x
    neff = len(t_trk) * a.hop / a.win  # overlapping frames: about one independent frame per window length
    cov = np.linalg.pinv(res.jac.T @ res.jac) * np.sum(res.fun ** 2) / max(1.0, neff - 4) * (len(t_trk) / neff)
    sd = np.sqrt(np.clip(np.diag(cov), 0, None))
    return res, dict(
        tone_audio_s=[round(float(t_trk[0]), 2), round(float(t_trk[-1]), 2)],
        visible_s=round(float(t_trk[-1] - t_trk[0]), 1),
        f_start_hz=round(float(f_trk[0]), 2), f_end_hz=round(float(f_trk[-1]), 2),
        f0_hz=round(float(f0a), 2), f0_sd_hz=round(float(sd[0]), 2),
        speed_m_s=round(float(va), 1), speed_kt=round(float(va / KT), 1), speed_sd_kt=round(float(sd[1] / KT), 1),
        closest_slant_km=round(float(da / 1e3), 3), closest_sd_km=round(float(sd[2] / 1e3), 3),
        at_bound=bool(np.any(np.isclose(res.x, lo)) or np.any(np.isclose(res.x, hi))),
        cpa_emission_audio_s=round(float(tca), 2), cpa_heard_audio_s=round(float(tca + da / C_AIR), 2),
        cpa_heard_stream_utc=datetime.fromtimestamp(t0 + tca + da / C_AIR, timezone.utc).strftime("%H:%M:%S"),
        rms_hz=round(float(np.sqrt(np.mean(res.fun ** 2))), 3), frames=int(len(t_trk)),
        sd_note="1 sigma from the Jacobian, residual scaled to ~1 independent frame per window")


def rank(cands, t_trk, f_trk, t0, Ls, min_cover):
    """Fit every candidate (f0 + L) to the track; sorted by RMS, unscored last."""
    out = []
    for rec, t_rx, ratio in cands:
        rec = dict(rec)
        fit = fit_flight(t_rx, ratio, t_trk, f_trk, t0, Ls, min_cover)
        if np.isfinite(fit["rms"]):
            rec.update(rms_hz=round(fit["rms"], 3), L_s=round(fit["L"], 2), f0_hz=round(fit["f0"], 2),
                       cover=round(fit["cover"], 3),
                       L_at_bound=bool(np.isclose(fit["L"], Ls[0]) or np.isclose(fit["L"], Ls[-1])))
        else:
            rec.update(rms_hz=None, reason=f"ADS-B covers < {min_cover:.0%} of the tone frames at every L")
        out.append(rec)
    return sorted(out, key=lambda r: (r["rms_hz"] is None, r["rms_hz"] or 0))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seg", type=float, nargs=2, default=(460.0, 860.0), help="audio segment searched, s")
    ap.add_argument("--fmin", type=float, default=60.0)
    ap.add_argument("--fmax", type=float, default=400.0)
    ap.add_argument("--win", type=float, default=1.0)
    ap.add_argument("--hop", type=float, default=0.1)
    ap.add_argument("--pad", type=int, default=4, help="FFT zero padding factor")
    ap.add_argument("--ridge-hz", type=float, default=8.0)
    ap.add_argument("--jmax", type=int, default=3, help="max path jump per hop, bins")
    ap.add_argument("--pen", type=float, default=0.3, help="path penalty per bin of jump")
    ap.add_argument("--smooth", type=float, default=8.0, help="salience smoothing along the path, s")
    ap.add_argument("--nsig", type=float, default=1.0, help="visible: smoothed salience >= median + nsig sigma")
    ap.add_argument("--nsig-scan", default="2,1.5,1,0.5", help="thresholds re-run for the sensitivity record")
    ap.add_argument("--radius", type=float, default=10000.0)
    ap.add_argument("--window", type=float, default=600.0, help="+-s around the pass")
    ap.add_argument("--lmin", type=float, default=0.0)
    ap.add_argument("--lmax", type=float, default=60.0)
    ap.add_argument("--lstep", type=float, default=0.05)
    ap.add_argument("--min-cover", type=float, default=0.8, help="min fraction of track frames with ADS-B")
    ap.add_argument("--max-gap", type=float, default=60.0)
    ap.add_argument("--title", help="place name for the figure title")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cs = json.loads((Path(a.case_dir) / "case.json").read_text())
    cfg = CASES[cs["case"]]
    lat0, lon0 = cfg["camera"]
    t0 = datetime.fromisoformat(cs["utc_start"].replace("Z", "+00:00")).timestamp()

    # 1. audio: dominant narrow line and the level-pass fit
    fs = sf.info(str(ROOT / cs["audio"])).samplerate
    x, _ = sf.read(str(ROOT / cs["audio"]), start=int(a.seg[0] * fs), stop=int(a.seg[1] * fs), dtype="float32",
                   always_2d=True)
    x = x.mean(axis=1)
    D, tau, f = stft_band(x, fs, a.seg[0], a.win, a.hop, a.pad, a.fmin, a.fmax)
    df = f[1] - f[0]
    S = whiten(D, a.ridge_hz, df)
    k = viterbi(S, a.jmax, a.pen)
    sal = uniform_filter1d(S[np.arange(len(k)), k], max(1, int(round(a.smooth / a.hop))))
    t_trk, f_trk = extract(S, k, tau, f, sal, a.nsig)
    print(f"tone {t_trk[0]:.1f}-{t_trk[-1]:.1f} s ({t_trk[-1] - t_trk[0]:.1f} s), {f_trk[0]:.1f} -> {f_trk[-1]:.1f} Hz")
    res, audio = fit_audio(t_trk, f_trk, a, t0)
    f0a, va, da, tca = res.x
    print(f"audio fit: f0 {f0a:.1f} Hz  v {va / KT:.0f} kt  d {da / 1e3:.2f} km  CPA heard {audio['cpa_heard_audio_s']:.1f} s  "
          f"rms {audio['rms_hz']:.2f} Hz")

    # 2. ADS-B: every aircraft within radius in +-window of the pass, f0 + L only
    terrain = Terrain()
    geoid_n = terrain.geoid(lat0, lon0)
    cam_z = float(terrain.elevation(np.array([lat0]), np.array([lon0]))[0]) + MIC_AGL
    t_pass = t0 + tca  # pass time on the stream clock; real time is earlier by L
    w_lo, w_hi = t_pass - a.lmax - a.window, t_pass - a.lmin + a.window
    tracks = load_tracks(ROOT / cs["adsb"], w_lo - 900, w_hi + 900)
    offs = [np.median(tr["geom"][m] - tr["baro"][m]) for tr in tracks.values()
            for m in [np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])] if m.any()]
    goff = float(np.median(offs)) if offs else 0.0
    Ls = np.arange(a.lmin, a.lmax + 1e-9, a.lstep)
    te = np.arange(t0 + a.seg[0] - a.lmax - 200, t0 + a.seg[1] + 5, 0.05)
    tw = np.arange(w_lo, w_hi, 0.5)
    cands = []  # (static record, receive times, Doppler ratio)
    for hx, tr in tracks.items():
        tr, dropped = despike(tr)
        if len(tr["t"]) < 2:
            continue
        h = track_heights(tr, terrain, geoid_n, goff)
        Xw, Yw, Zw = interp_track(tr, h, tw, lat0, lon0, max_gap=a.max_gap)
        dw = np.hypot(Xw, Yw)
        if not (np.isfinite(dw) & (dw <= a.radius)).any():
            continue
        iw = int(np.nanargmin(dw))
        t_rx, ratio, X, Y, Z, r = predicted(tr, h, cam_z, lat0, lon0, te, a.max_gap)
        near = (np.abs(tw - tw[iw]) <= 60) & np.isfinite(Zw)
        climb = np.polyfit(tw[near] - tw[iw], Zw[near], 1)[0] if near.sum() > 3 else np.nan
        gs = tr["gs"][np.abs(tr["t"] - tw[iw]) <= 60]
        rec = dict(hex=hx, flight=tr["flight"] or hx, type=cfg.get("types", {}).get(hx) or tr["type"] or "?",
                   closest_horizontal_m=round(float(dw[iw])),
                   closest_slant_m=round(float(math.hypot(dw[iw], Zw[iw] - cam_z))),
                   closest_agl_m=round(float(Zw[iw] - cam_z + MIC_AGL)),
                   closest_utc=datetime.fromtimestamp(tw[iw], timezone.utc).strftime("%H:%M:%S"),
                   gs_kt=round(float(np.nanmedian(gs)), 0) if np.isfinite(gs).any() else None,
                   climb_m_s=round(float(climb), 2) if np.isfinite(climb) else None,
                   within_radius_s=round(float((np.isfinite(dw) & (dw <= a.radius)).sum() * 0.5), 1),
                   despiked_reports=dropped)
        cands.append((rec, t_rx, ratio))
    flights = rank(cands, t_trk, f_trk, t0, Ls, a.min_cover)
    for rec in flights:
        print(f"{rec['flight']:8s} {rec['type']:6s} {rec['closest_horizontal_m']:6d} m  rms {rec['rms_hz']}  L {rec.get('L_s')}")
    scored = [r for r in flights if r["rms_hz"] is not None]
    best, second = scored[0], scored[1] if len(scored) > 1 else None
    ratio2 = second["rms_hz"] / best["rms_hz"] if second else None

    sens = []  # the same chain for other visibility thresholds
    for ns in sorted({float(v) for v in a.nsig_scan.split(",")} | {a.nsig}, reverse=True):
        tt, ff = extract(S, k, tau, f, sal, ns)
        _, au = fit_audio(tt, ff, a, t0)
        sc = [r for r in rank(cands, tt, ff, t0, Ls, a.min_cover) if r["rms_hz"] is not None]
        sens.append(dict(nsig=ns, tone_audio_s=au["tone_audio_s"], audio_speed_kt=au["speed_kt"],
                         audio_closest_slant_km=au["closest_slant_km"], audio_at_bound=au["at_bound"],
                         best=sc[0]["flight"], best_rms_hz=sc[0]["rms_hz"],
                         second=sc[1]["flight"] if len(sc) > 1 else None,
                         second_rms_hz=sc[1]["rms_hz"] if len(sc) > 1 else None,
                         ratio=round(sc[1]["rms_hz"] / sc[0]["rms_hz"], 2) if len(sc) > 1 else None))
        print(f"nsig {ns}: {sens[-1]}")

    # best flight: full curve, phase, CPA from ADS-B
    trb = despike(tracks[best["hex"]])[0]
    hb = track_heights(trb, terrain, geoid_n, goff)
    t_rx, ratio, X, Y, Z, r = predicted(trb, hb, cam_z, lat0, lon0, te, a.max_gap)
    ib = int(np.nanargmin(r))
    cl = best["climb_m_s"]
    phase = "level" if cl is None or abs(cl) < 1.5 else ("climb" if cl > 0 else "descent")
    v3 = np.sqrt(np.gradient(X, te) ** 2 + np.gradient(Y, te) ** 2 + np.gradient(Z, te) ** 2)
    adsb = dict(flight=best["flight"], type=best["type"], speed_kt_ground=best["gs_kt"],
                speed_kt_3d_at_cpa=round(float(np.nanmedian(v3[max(0, ib - 200):ib + 200]) / KT), 1),
                closest_slant_km=round(float(r[ib] / 1e3), 3),
                closest_horizontal_km=round(best["closest_horizontal_m"] / 1e3, 3),
                altitude_agl_m=best["closest_agl_m"], phase=f"{phase} ({cl:+.1f} m/s)" if cl is not None else phase,
                cpa_heard_audio_s=round(float(t_rx[ib] - t0 + best["L_s"]), 2), L_s=best["L_s"], f0_hz=best["f0_hz"],
                rms_hz=best["rms_hz"], within_radius_s=best["within_radius_s"])
    sep = (f"best {best['flight']} {best['rms_hz']:.2f} Hz vs second {second['flight']} {second['rms_hz']:.2f} Hz "
           f"(x{ratio2:.1f})") if second else "only one aircraft scored"
    clear = ratio2 is not None and ratio2 >= 2.0
    verdict = f"{best['flight']} ({best['type']}) is identified; {sep}." if clear else f"NOT clearly separated: {sep}."
    print(verdict)

    # 3. figure
    fig = plt.figure(figsize=(15, 10))
    gsp = fig.add_gridspec(2, 2, hspace=0.32, wspace=0.18)
    m = (tau >= t_trk[0] - 15) & (tau <= t_trk[-1] + 15)
    ax1 = fig.add_subplot(gsp[0, 0])
    ax1.pcolormesh(tau[m], f, S[m].T, shading="nearest", cmap="magma", vmin=-1, vmax=5, rasterized=True)
    ax1.plot(t_trk, f_trk, color="lime", lw=1.0, label="extracted tone (audio only)")
    ax1.set_ylabel("Hz (whitened)")
    ax1.set_xlabel("audio time (s)")
    ax1.set_title("1  Audio: the dominant narrow line, tracked", loc="left", fontsize=11)
    ax1.legend(fontsize=8, loc="upper right")
    ax2 = fig.add_subplot(gsp[0, 1])
    ax2.pcolormesh(tau[m], f, S[m].T, shading="nearest", cmap="gray_r", vmin=-1, vmax=6, rasterized=True)
    ta = t_rx - t0 + best["L_s"]
    k2 = (ta >= tau[m][0]) & (ta <= tau[m][-1])
    ax2.plot(t_trk, f_trk, ".", color="lime", ms=2.5, label="extracted tone")
    ax2.plot(ta[k2], best["f0_hz"] * ratio[k2], color="tab:blue", lw=1.6,
             label=f"{best['flight']} predicted, f0 {best['f0_hz']:.1f} Hz")
    ax2.plot(ta[k2], 2 * best["f0_hz"] * ratio[k2], color="tab:blue", lw=1.2, ls="--", label="x2")
    ax2.plot(t_trk, pass_model(res.x, t_trk), color="tab:orange", lw=1.2, ls=":", label="audio-only level-pass fit")
    ax2.set_xlim(tau[m][0], tau[m][-1])
    ax2.set_ylim(a.fmin, a.fmax)
    ax2.set_xlabel("audio time (s)")
    ax2.set_title(f"2  Tone vs {best['flight']}'s ADS-B Doppler curve (L = {best['L_s']:.2f} s)", loc="left", fontsize=11)
    ax2.legend(fontsize=8, loc="upper right")
    ax3 = fig.add_subplot(gsp[1, 0])
    ax3.axis("off")
    cells = [["source / type", "audio: one tonal pass", f"{best['flight']}  {best['type']}"],
             ["speed", f"{audio['speed_kt']:.0f} +- {audio['speed_sd_kt']:.0f} kt",
              f"{best['gs_kt']:.0f} kt ground ({adsb['speed_kt_3d_at_cpa']:.0f} kt 3D)"],
             ["closest pass", f"{audio['closest_slant_km']:.2f} +- {audio['closest_sd_km']:.2f} km slant",
              f"{adsb['closest_slant_km']:.2f} km slant ({adsb['closest_horizontal_km']:.2f} hor, "
              f"{adsb['altitude_agl_m'] / 1e3:.1f} up)"],
             ["CPA heard, audio s", f"{audio['cpa_heard_audio_s']:.1f}",
              f"{adsb['cpa_heard_audio_s']:.1f} (at L {best['L_s']:.2f} s)"],
             ["phase", "level pass assumed", adsb["phase"]],
             ["visible", f"tone {audio['visible_s']:.0f} s",
              f"{adsb['within_radius_s']:.0f} s within {a.radius / 1e3:.0f} km"],
             ["f0", f"{audio['f0_hz']:.1f} Hz", f"{best['f0_hz']:.1f} Hz"]]
    tb = ax3.table(cellText=cells, colLabels=["", "from the audio", "from ADS-B"], loc="center", cellLoc="left",
                   colWidths=[0.17, 0.33, 0.50])
    tb.auto_set_font_size(False)
    tb.set_fontsize(9)
    tb.scale(1, 1.9)
    ax3.set_title("3  The audio alone vs the best flight's ADS-B", loc="left", fontsize=11)
    ax4 = fig.add_subplot(gsp[1, 1])
    vals = [r["rms_hz"] for r in scored]
    ax4.barh(range(len(scored)), vals, color=["tab:orange"] + ["0.65"] * (len(scored) - 1))
    ax4.set_yticks(range(len(scored)))
    ax4.set_yticklabels([f"{r['flight']} ({r['type']})" for r in scored], fontsize=8)
    ax4.invert_yaxis()
    for i, v in enumerate(vals):
        ax4.text(v, i, f" {v:.1f}", va="center", fontsize=7.5)
    ax4.set_xlabel("RMS error vs the extracted tone (Hz), only f0 and L fitted")
    ax4.set_title(f"4  Every aircraft within {a.radius / 1e3:.0f} km, +-{a.window / 60:.0f} min: fit error",
                  loc="left", fontsize=11)
    rs = [r["ratio"] for r in sens if r["ratio"]]
    levels = ", ".join(f"{r['nsig']:g}" for r in sens)
    bests = "/".join(sorted({r["best"] for r in sens}))
    ax4.text(0.98, 0.10, f"tone-extent thresholds {levels} sigma: best is {bests} in all, "
             f"second/best x{min(rs):.1f}-x{max(rs):.1f}", transform=ax4.transAxes, ha="right", fontsize=8, color="0.25")
    if len(flights) > len(scored):
        ax4.text(0.98, 0.02, f"{len(flights) - len(scored)} more had no ADS-B over the tone", transform=ax4.transAxes,
                 ha="right", fontsize=8, color="0.4")
    fig.suptitle(f"Which flight was it? {a.title or cs['case']}: {verdict}", fontsize=12)
    fig.text(0.5, 0.005, NOTE, ha="center", fontsize=9, color="0.3", style="italic")
    fig.savefig(out / "flight_id.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    summary = dict(note=NOTE, case=cs["case"], camera=dict(lat=lat0, lon=lon0, z_m=cam_z), params=vars(a),
                   audio_only=audio, best_adsb=adsb, second=second["flight"] if second else None,
                   second_over_best_rms=round(ratio2, 2) if ratio2 else None, clearly_separated=bool(clear),
                   separation_rule="clearly separated if the second-best RMS is >= 2x the best",
                   verdict=verdict, threshold_sensitivity=sens,
                   separation_note=(f"second/best over the {len(sens)} thresholds: x{min(rs):.2f}-x{max(rs):.2f}; >= 2x at "
                                    f"{sum(r >= 2 for r in rs)} of {len(rs)}"
                                    + ("; the second's best L sits at the scan bound" if second and second.get("L_at_bound")
                                       else "")),
                   flights=flights,
                   extracted_track=dict(t_audio_s=np.round(t_trk, 2).tolist(), f_hz=np.round(f_trk, 2).tolist()))
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print("done")


if __name__ == "__main__":
    main()
