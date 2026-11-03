"""Live-scrolling spectrogram of the Midway clip, like a real-time analyzer: new sound enters on the right.

    python post/tools/anim_scroll.py --out post/midway_spectrogram_live.mp4

Synced to post/midway_tracked.mp4 (recording 441-461 s). The tone dots are the real tone track of
solver_paras/flight_id.py; the end cards are the numbers of results/flight_id_E/live_run.txt.
"""
import argparse, math, os, subprocess, sys
from types import SimpleNamespace

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "solver_paras"))
import flight_id as FI

BG, FG, ACC, DIM, HI = "#000000", "#f2f5f8", "#3ee6ff", "#6b7682", "#7dff9a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default=os.path.join(ROOT, "data", "screenrec", "mdw_163340.mp4"))
    ap.add_argument("--start", type=float, default=441.0); ap.add_argument("--dur", type=float, default=20.0)
    ap.add_argument("--view", type=float, default=8.0, help="seconds visible on screen")
    ap.add_argument("--fps", type=int, default=30); ap.add_argument("--out", required=True)
    a = ap.parse_args()

    x, sr = FI.read_audio(a.audio)
    hop = sr // a.fps                     # one spectrogram column per video frame
    nper, nfft = int(0.256 * sr), 8192
    t_lo = a.start - a.view
    n_cols = int((a.dur + a.view) * a.fps)
    win = np.hanning(nper).astype(np.float32)
    f = np.fft.rfftfreq(nfft, 1 / sr)
    sel = (f >= 500) & (f <= 7000)
    cols = np.empty((n_cols, sel.sum()), np.float32)
    for k in range(n_cols):
        c = int((t_lo + k / a.fps) * sr)
        seg = x[c - nper // 2: c + nper // 2] * win
        cols[k] = 10 * np.log10(np.abs(np.fft.rfft(seg, nfft))[sel] ** 2 + 1e-12)
    cols -= np.median(cols, axis=0, keepdims=True)
    vmin, vmax = np.percentile(cols, 35), np.percentile(cols, 99.6)

    cfg = SimpleNamespace(win=0.256, nfft=16384, hop=0.05, f0min=900.0, f0max=1800.0, fstep=1.0, harmonics=4,
                          max_step=0.015, jump_pen=0.05)
    t, fz, z, P, df = FI.tone_track(x, sr, 425.0, 480.0, cfg)
    keep = FI.pass_span(t, fz, z, 1.5, 1.0)
    tt_tone, ff_tone = t[keep], fz[keep]

    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.0, 0.22, 1.0, 0.78]); ax.set_facecolor(BG); ax.axis("off")
    nv = int(a.view * a.fps)
    im = ax.imshow(np.full((sel.sum(), nv), vmin), origin="lower", aspect="auto", cmap="magma", vmin=vmin, vmax=vmax,
                   extent=[-a.view, 0, f[sel][0], f[sel][-1]], interpolation="bilinear")
    dots = [ax.plot([], [], "o", color=ACC, ms=3.2, alpha=a_)[0] for a_ in (1.0, 0.75, 0.6, 0.5)]
    ax.set_xlim(-a.view, 0)
    ax.text(-a.view + 0.08, 6750, "7 kHz", color=DIM, fontsize=12, va="top")
    ax.text(-a.view + 0.08, 650, "0.5 kHz", color=DIM, fontsize=12, va="bottom")
    c1 = fig.text(0.03, 0.12, "", fontsize=30, color=FG, family="DejaVu Sans Mono")
    c2 = fig.text(0.03, 0.045, "", fontsize=30, color=HI, family="DejaVu Sans Mono", weight="bold")
    live = fig.text(0.975, 0.965, "● LIVE", fontsize=16, color="#ff4d4d", ha="right", va="top", family="DejaVu Sans Mono")

    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "1920x1080",
                            "-r", str(a.fps), "-i", "-", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
                            "-pix_fmt", "yuv420p", "-vf", "scale=out_range=tv", a.out], stdin=subprocess.PIPE)
    for k in range(int(a.dur * a.fps)):
        now = a.start + k / a.fps
        im.set_data(cols[k: k + nv].T)
        m = (tt_tone <= now) & (tt_tone >= now - a.view)
        for h, d in zip((1, 2, 3, 4), dots):
            d.set_data(tt_tone[m] - now, ff_tone[m] * h)
        sec = k / a.fps
        if sec >= 15.0:
            c1.set_text("sound only:  139 kt · closest 120 m · 14:32:20 UTC")
        if sec >= 16.8:
            c2.set_text("match 1 of 35:  SWA4113 · Boeing 737 MAX 8 · 13 m · +0.1 s")
        live.set_alpha(1.0 if int(sec * 2) % 2 == 0 else 0.35)
        fig.canvas.draw()
        enc.stdin.write(np.asarray(fig.canvas.buffer_rgba()).tobytes())
    enc.stdin.close(); enc.wait()
    print("wrote", a.out)


if __name__ == "__main__":
    main()
