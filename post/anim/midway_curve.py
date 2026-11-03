#!/usr/bin/env python
"""Predicted Doppler curve of SWA4113 (ac33a3) over the Midway demo clip, at the KNOWN camera (not blind).

The shape comes from public flight data: solver_paras/paras.py's Setup (case_E ADS-B, terrain, emission-time geometry
at the owner-published camera 41.77844, -87.74278) and its curve() at stream latency L = 28 s (the burned-in clock).
One number is fitted to the sound: the emitted fan-tone frequency f0, as paras.py does (max over f0 of the whitened,
ridge-filtered spectrogram along the curve, x1-x3), here on the clip's own audio (case_E has a synthetic voice over
this pass, the clip does not).

    from midway_curve import predict;  predict()  ->  dict(curves=[...], cpa=..., f0_hz=..., ...)
"""
from __future__ import annotations

import math
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solver_paras"))
sys.path.insert(0, str(ROOT / "solver_ridge"))
CLIP = ROOT / "post" / "midway_demo_clip.mp4"
CLIP_AT = 438.0          # clip t = 0 is 438 s into data/screenrec/mdw_163340.mp4 (= case_E audio, wall-clock start 14:25:17Z)
L = 28.0                 # stream latency from the burned-in clock (paras.CLOCK_L)
HEX = "ac33a3"
HARMONICS = (1, 2, 3, 4, 5)


def predict(fmin=800.0, fmax=7900.0):
    import paras
    from ridge import whitened_spectrogram
    from scipy.ndimage import uniform_filter1d
    a = Namespace(fmin=fmin, fmax=fmax, f0min=1000.0, f0max=6000.0, win=0.15, hop=0.025, dlog=0.005, ridge=31, half=10.0, min_frac=0.6)
    old = paras.PICKS
    paras.PICKS = [HEX]
    try:
        S = paras.Setup(ROOT / "cases" / "case_E", a)
    finally:
        paras.PICKS = old
    ac = S.ac[0]
    t_case, lr = S.curve(ac, L)                         # case audio s, log(f / f0)
    t_clip = t_case - CLIP_AT

    # f0: the clip's own audio, same spectrogram and ridge filter as paras.py, x1..x3 along the predicted curve
    fs = 16000
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(CLIP), "-vn", "-ac", "1", "-ar", str(fs), "-f", "f32le", "-"],
                                     capture_output=True, check=True).stdout, dtype="<f4").astype(np.float32)
    W, tau, fk = whitened_spectrogram(x, fs, a.fmin, a.fmax, int(round(a.win * fs)), int(round(a.hop * fs)), a.dlog)
    W = (W - uniform_filter1d(W, a.ridge, axis=1, mode="nearest")).astype(np.float32)
    t_cpa = ac["cpa_tr"] - S.t0 + L - CLIP_AT
    m = (tau >= t_cpa - a.half) & (tau <= t_cpa + a.half)
    lrt = np.interp(tau[m], t_clip, lr)
    best = (-np.inf, None)
    for f0 in np.exp(np.arange(math.log(a.f0min), math.log(a.f0max / 3), a.dlog)):
        s = 0.0
        for h in (1, 2, 3):
            k = np.round(np.log(h * f0 * np.exp(lrt) / fk[0]) / a.dlog).astype(int)
            ok = (k >= 0) & (k < len(fk))
            s += float(W[np.flatnonzero(m)[ok], k[ok]].mean()) if ok.any() else 0.0
        if s > best[0]:
            best = (s, f0)
    f0 = float(best[1])
    keep = (t_clip >= -0.5) & (t_clip <= 20.5)
    tt = t_clip[keep][::5]
    curves = [dict(harmonic=h, t_s=np.round(tt, 3).tolist(), f_hz=np.round(h * f0 * np.exp(lr[keep][::5]), 1).tolist()) for h in HARMONICS]
    return dict(hex=HEX, flight=(ac["flight"] or "").strip(), type=ac["type"], f0_hz=round(f0, 1), score=round(best[0], 4),
                cpa_clip_s=round(float(t_cpa), 2), cpa_emit_utc=float(ac["cpa_te"]), cpa_slant_m=round(ac["cpa_r"], 1),
                cpa_horizontal_m=round(ac["cpa_hor"], 1), cpa_agl_m=round(ac["cpa_agl"], 1), L_s=L, curves=curves)


if __name__ == "__main__":
    r = predict()
    c = r["curves"][0]
    at = lambda s: float(np.interp(s, c["t_s"], c["f_hz"]))  # noqa: E731
    print({k: v for k, v in r.items() if k != "curves"})
    print("x1 at 7, 10, 12.6, 14, 18 s:", [round(at(s)) for s in (7, 10, 12.6, 14, 18)])
