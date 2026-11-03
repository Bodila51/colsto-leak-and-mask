#!/usr/bin/env python
"""Write post/anim/preview_data.json: demo values only, so the pipeline can be tested before real results exist.

    python post/anim/make_preview.py

The tone curves are a smooth fit of the real ridge (1.4 / 2.8 / 4.2 kHz slide, 14-20 s) in the current monitor1_recording.mp4 so that
the demo looks plausible. Everything
else (aircraft, distances, map, heat) is invented and the page stamps "PREVIEW - not real data" on every frame.
"""
import json
import math
from pathlib import Path

import numpy as np

import build as B

ANIM = Path(__file__).resolve().parent


def main():
    x = B.decode(B.POST / "monitor1_recording.mp4")
    sp = dict(f_min_hz=0.0, f_max_hz=4500.0, window_s=0.3, view_s=20)
    D, fr = B.stft_db(x, sp)
    E = B.line_enhance(D, fr[1] - fr[0], 150.0)
    t = np.arange(E.shape[1]) * B.HOP / B.SR
    # the engine tone in this clip: a fundamental near 1.4 kHz that slides down from ~14.4 s, plus its 2nd and 3rd harmonic.
    # Follow the fundamental with a robust cubic over its strong points; the harmonics are exact multiples (demo values).
    sel = (fr > 1100) & (fr < 1800)
    pk = fr[sel][np.argmax(E[sel], axis=0)]
    val = E[sel].max(axis=0)
    t0, t1 = 14.3, 19.9
    win = (t >= t0) & (t <= t1)
    c = np.polyfit([t0, t1], [1400.0, 1250.0], 1)
    for _ in range(8):
        w = win & (val > np.percentile(val[win], 35)) & (np.abs(np.polyval(c, t) - pk) < 60)
        c = np.polyfit(t[w], pk[w], 3)
    print("ridge points used:", int(w.sum()), "of", int(win.sum()), " f1 at", t0, t1, "=", np.polyval(c, [t0, t1]).round(0))
    tt = np.linspace(t0, t1, 57)
    f1 = np.minimum.accumulate(np.polyval(c, tt))
    curves = [dict(harmonic=h, f0_hz=round(float(f1[0]), 1), t_s=[round(float(v), 3) for v in tt], f_hz=[round(float(h * v), 1) for v in f1]) for h in (1, 2, 3)]

    # fake map around the synthetic test case box (Tennessee), 40 x 40 km
    bbox = [35.95152, -86.37686, 36.3472, -85.88684]
    lat_c, lon_c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    kx = 111.32 * math.cos(math.radians(lat_c))
    n = 110
    lat = np.linspace(bbox[0], bbox[2], n)
    lon = np.linspace(bbox[1], bbox[3], n)
    LON, LAT = np.meshgrid(lon, lat)
    X = (LON - lon_c) * kx * 1000
    Y = (LAT - lat_c) * 111.32 * 1000

    def track(p0, p1, k=40):  # metres from centre -> lat/lon
        xs, ys = np.linspace(p0[0], p1[0], k), np.linspace(p0[1], p1[1], k)
        return dict(lat=[round(lat_c + yv / 111320, 5) for yv in ys], lon=[round(lon_c + xv / (kx * 1000), 5) for xv in xs])

    def dist_to_line(p0, p1):
        d = np.array(p1) - np.array(p0)
        return np.abs(d[0] * (Y - p0[1]) - d[1] * (X - p0[0])) / math.hypot(*d)

    a0, a1 = (-19500, 4200), (19500, 6800)
    b0, b1 = (-5200, -19500), (-1800, 19500)
    dA, dB = 3100.0, 4300.0
    sig = 420.0
    hA = np.exp(-0.5 * ((dist_to_line(a0, a1) - dA) / sig) ** 2)
    heat1 = hA
    hB = np.exp(-0.5 * ((dist_to_line(b0, b1) - dB) / sig) ** 2)
    cross = np.sqrt(hA * hB)  # the two pairs of bands cross in four places; the demo keeps one
    best = np.unravel_index(np.argmax(cross), cross.shape)
    px, py = X[best], Y[best]
    heat2 = 0.14 * np.maximum(hA, hB) + np.exp(-0.5 * (((X - px) ** 2 + (Y - py) ** 2) / 650.0 ** 2))
    found = dict(lat=round(float(lat[best[0]]), 5), lon=round(float(lon[best[1]]), 5))
    true = dict(lat=round(found["lat"] + 90 / 111320, 5), lon=round(found["lon"] - 60 / (kx * 1000), 5))
    err = math.hypot(90, 60)
    r2 = lambda m: [[round(float(v), 3) for v in row] for row in m]  # noqa: E731
    out = dict(
        preview=True,
        note="PREVIEW demo values: aircraft, distances, map and heat are invented. Tone curves follow the real ridge.",
        clip=dict(utc_start="2026-11-03T13:31:14.000Z", duration_s=20.0),
        spectrogram=sp,
        callout=dict(tip_t_s=15.4, tip_f_hz=round(float(2 * np.interp(15.4, tt, f1)), 1), from_s=0.35, to_s=7.6, sub="slides down as it passes"),
        safe_audio="post/anim/build/preview_safe.wav",
        curves=curves,
        aircraft=[
            dict(id="A", label="DEMO-1 · B738 · N000AA", closest_m=int(dA), track=track(a0, a1)),
            dict(id="B", label="DEMO-2 · B738 · DEMO02", closest_m=int(dB), track=track(b0, b1)),
        ],
        map=dict(bbox=bbox, lat=[round(float(v), 5) for v in lat], lon=[round(float(v), 5) for v in lon],
                 heat_1=r2(heat1), heat_2=r2(heat2), planes_used=2,
                 found=found, true=true, error_m=round(err), found_in_safe=False),
    )
    (ANIM / "preview_data.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print("wrote preview_data.json", (ANIM / "preview_data.json").stat().st_size // 1024, "KB; found", found, "err", round(err))


if __name__ == "__main__":
    main()
