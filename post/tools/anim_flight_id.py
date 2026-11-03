"""Animated 'which flight was it?' panel for the Midway clip, synced to post/midway_tracked.mp4 (recording 441-461 s).

    python post/tools/anim_flight_id.py --out post/midway_sound_panel.mp4

All data is computed here from the screen recording audio and the ADS-B log, with the same tone tracker and
pass model as results/flight_id_E (solver_paras/flight_id.py). Known location; the flight is what is identified.
"""
import argparse, json, math, os, subprocess, sys
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
from scipy.optimize import least_squares
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "solver_paras"))
import flight_id as FI

CAM = (41.77844, -87.74278)
T0 = datetime(2026, 11, 3, 14, 25, 17, tzinfo=timezone.utc).timestamp()
LAT = 28.0
C = 343.0
BG, FG, ACC, DIM, HI = "#07090c", "#e8edf2", "#3ee6ff", "#5b6673", "#7dff9a"


def compute(audio, adsb):
    cfg = SimpleNamespace(win=0.256, nfft=16384, hop=0.05, f0min=900.0, f0max=1800.0, fstep=1.0, harmonics=4,
                          max_step=0.015, jump_pen=0.05)
    x, sr = FI.read_audio(audio)
    t, fz, z, P, df = FI.tone_track(x, sr, 425.0, 480.0, cfg)
    keep = FI.pass_span(t, fz, z, 1.5, 1.0)
    ts, fs = t[keep], fz[keep]
    best = None
    for v0 in (40.0, 70.0, 100.0):
        for d0 in (100.0, 300.0, 1000.0):
            p0 = [math.sqrt(fs.max() * fs.min()), v0, d0, ts[np.argmin(np.gradient(fs, ts))]]
            r = least_squares(lambda p: FI.pass_model(p, ts) - fs, p0,
                              bounds=([500, 5, 10, ts.min() - 60], [3000, 250, 8000, ts.max() + 60]))
            if best is None or r.cost < best.cost:
                best = r
    f0, v, d, tce = best.x
    tc = tce + d / C
    cpa = T0 + tc - LAT
    pts = {}
    for line in open(adsb):
        dd = json.loads(line)
        for ac in dd["ac"]:
            if "lat" not in ac or ac.get("alt_baro") in (None, "ground"):
                continue
            tp = dd["t"] - (ac.get("seen_pos") or 0)
            if abs(tp - cpa) > 600:
                continue
            pts.setdefault(ac["hex"], []).append((tp, ac["lat"], ac["lon"], ac.get("alt_baro"), ac.get("gs"),
                                                  (ac.get("flight") or "").strip(), ac.get("t") or "?"))
    rows = []
    for h, p in pts.items():
        bb = None
        for tp, la, lo, alt, gs, fl, ty in p:
            rr = math.hypot((la - CAM[0]) * 111320, (lo - CAM[1]) * 111320 * math.cos(math.radians(CAM[0])))
            if bb is None or rr < bb[0]:
                bb = (rr, tp, alt, gs, fl, ty)
        if bb[0] > 10000:
            continue
        fl = bb[4] or next((q[5] for q in p if q[5]), h)
        dt = bb[1] - cpa
        m = abs(dt) / 10 + abs(bb[0] - d) / 500 + abs((bb[3] or 0) - v * 1.94384) / 30
        rows.append(dict(m=m, fl=fl, ty=bb[5], r=bb[0], gs=bb[3] or 0, dt=dt))
    rows.sort(key=lambda r: r["m"])
    # spectrogram for display: recording 441-461 s, 500-7000 Hz, from P (whitened, 425-480 s)
    fr = np.arange(P.shape[1]) * df
    sel = (fr >= 500) & (fr <= 7000)
    return dict(t=t, fz=fz, keep=keep, P=P[:, sel], fr=fr[sel], v=v, d=d, tc=tc, cpa=cpa, rows=rows, rms=float(np.sqrt(np.mean(best.fun ** 2))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default=os.path.join(ROOT, "data", "screenrec", "mdw_163340.mp4"))
    ap.add_argument("--adsb", default=os.path.join(ROOT, "cases", "case_E", "adsb.jsonl"))
    ap.add_argument("--start", type=float, default=441.0); ap.add_argument("--dur", type=float, default=20.0)
    ap.add_argument("--fps", type=int, default=30); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = compute(a.audio, a.adsb)
    t, fz, keep, P, fr = D["t"], D["fz"], D["keep"], D["P"], D["fr"]
    win = (t >= a.start) & (t <= a.start + a.dur)
    Pw, tw = P[win], t[win]
    rows = D["rows"]; n = len(rows); best = rows[0]

    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": FG, "axes.labelcolor": FG,
                         "xtick.color": DIM, "ytick.color": DIM, "axes.edgecolor": DIM})
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG)
    fig.text(0.03, 0.955, "Which flight made this sound?", fontsize=30, weight="bold", color=FG)
    fig.text(0.03, 0.918, "Midway (Chicago) webcam, known location. The sound alone gives the pass; public flight data says which plane.",
             fontsize=15, color=DIM)
    axS = fig.add_axes([0.05, 0.47, 0.90, 0.40], facecolor=BG)
    axT = fig.add_axes([0.05, 0.06, 0.36, 0.33], facecolor=BG); axT.axis("off")
    axB = fig.add_axes([0.52, 0.07, 0.43, 0.32], facecolor=BG)
    vmax = np.percentile(Pw, 99.5); vmin = np.percentile(Pw, 40)
    im = axS.imshow(np.full_like(Pw.T, vmin), origin="lower", aspect="auto", cmap="magma", vmin=vmin, vmax=vmax,
                    extent=[0, a.dur, fr[0], fr[-1]])
    axS.set_ylabel("Hz"); axS.set_xlabel("seconds")
    axS.set_title("1 · the sound: the jet's fan tone slides down as it passes over the camera (Doppler)", loc="left", color=FG, fontsize=15)
    play = axS.axvline(0, color=FG, lw=1.2)
    dots, = axS.plot([], [], ".", color=ACC, ms=5)
    harm = [axS.plot([], [], ".", color=ACC, ms=3, alpha=0.6)[0] for _ in range(3)]
    axB.set_title(f"3 · every aircraft within 10 km (n = {n}): how well its pass matches the sound", loc="left", color=FG, fontsize=13)
    show = rows[:12]
    ys = np.arange(len(show))[::-1]
    bars = axB.barh(ys, [0] * len(show), color=[HI if i == 0 else "#3a4450" for i in range(len(show))])
    axB.set_yticks(ys); axB.set_yticklabels([f"{r['fl']} {r['ty']}" for r in show], fontsize=10, color=FG)
    mmax = max(r["m"] for r in show) * 1.1
    axB.set_xlim(0, mmax); axB.set_xlabel("mismatch (time, distance, speed) — lower is better", color=DIM)
    for sp in ("top", "right"):
        axB.spines[sp].set_visible(False)
    rows_txt = [("", "from the sound", "flight data"),
                ("speed", f"{D['v']*1.94384:.0f} kt", f"{best['gs']:.0f} kt"),
                ("closest pass", f"{D['d']:.0f} m", f"{best['r']:.0f} m off, ~130 m up"),
                ("moment", datetime.fromtimestamp(D["cpa"], timezone.utc).strftime("%H:%M:%S UTC"), f"{best['dt']:+.1f} s"),
                ("aircraft", "jet fan tone", f"{best['fl']} · B737 MAX 8")]
    axT.set_title("2 · the pass, from the sound alone vs flight data", loc="left", color=FG, fontsize=13)
    txt = [[axT.text(c * 0.36, 0.85 - r * 0.19, "", fontsize=14, color=(DIM if r == 0 else FG), transform=axT.transAxes)
            for c in range(3)] for r in range(len(rows_txt))]
    verdict = fig.text(0.03, 0.43, "", fontsize=19, color=HI, weight="bold")
    mark = axB.text(show[0]["m"], ys[0], "", va="center", color=HI, fontsize=12, weight="bold")
    note = fig.text(0.03, 0.015, "known location (owner-published); what the sound identified is the flight  ·  data: adsb.lol (ODbL)  ·  stream: StreamTime LIVE  ·  method: colsto",
                    fontsize=11, color=DIM)
    nfr = int(a.dur * a.fps)
    tmp = os.path.join(ROOT, "data", "look", "anim_frames"); os.makedirs(tmp, exist_ok=True)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "1920x1080", "-r", str(a.fps),
                            "-i", "-", "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                            "-vf", "scale=out_range=tv", a.out], stdin=subprocess.PIPE)
    tone_t = t[keep]; tone_f = fz[keep]
    for k in range(nfr):
        tt = k / a.fps
        cur = a.start + tt
        shown = np.where(tw <= cur, 1, 0)[:, None]
        im.set_data((np.where(shown, Pw, vmin)).T)
        play.set_xdata([tt, tt])
        m = tone_t <= cur
        dots.set_data(tone_t[m] - a.start, tone_f[m])
        for h, ln in zip((2, 3, 4), harm):
            ln.set_data(tone_t[m] - a.start, tone_f[m] * h)
        # table after the tone ends (~15 s), bars after that
        if tt >= 14.5:
            nrow = min(len(rows_txt), 1 + int((tt - 14.5) / 0.35))
            for r in range(nrow):
                for c in range(3):
                    txt[r][c].set_text(rows_txt[r][c])
        if tt >= 15.5:
            frac = min(1.0, (tt - 15.5) / 2.0)
            for b, r in zip(bars, show):
                b.set_width(r["m"] * frac)
        if tt >= 17.8:
            verdict.set_text(f"1 of {n} aircraft matches the sound: {best['fl']} (Boeing 737 MAX 8), {best['r']:.0f} m from the camera, {best['dt']:+.1f} s. Next best is {rows[1]['m']/best['m']:.0f}x worse.")
            mark.set_text("  <- match")
        fig.canvas.draw()
        enc.stdin.write(np.asarray(fig.canvas.buffer_rgba()).tobytes())
    enc.stdin.close(); enc.wait()
    print("wrote", a.out, "best", best["fl"], "n", n)


if __name__ == "__main__":
    main()
