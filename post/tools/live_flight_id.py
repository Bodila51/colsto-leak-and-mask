# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Which flight made this sound? A real, step-by-step run meant to be watched in a terminal.

    uv run post/tools/live_flight_id.py            (from the repo root)
    uv run post/tools/live_flight_id.py --pace 0   (no pauses)

Known location (the Midway camera, owner-published), unknown flight. Everything printed is computed now:
  1. read the audio of our screen recording and track the jet's fan tone frame by frame
  2. fit a straight, constant-speed pass to that tone alone -> speed, closest distance, moment of closest pass
  3. read the public ADS-B log and, for every aircraft within 10 km, find where and when it passed closest
  4. compare: which flight passed the camera at that moment, that close, that fast?
The stream's delay behind real time (28 s) is read from the clock burned into the video, not from ADS-B.
"""
import argparse, json, math, os, subprocess, sys, time
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import least_squares

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CAM = (41.77844, -87.74278)                      # owner-published camera coordinates
T0 = datetime(2026, 11, 3, 14, 25, 17, tzinfo=timezone.utc).timestamp()   # recording start, wall clock
LATENCY = 28.0                                   # burned-in stream clock 08:29:39 CST at recording t = 290 s
C = 343.0
PACE = 0.01
G, D, Y, B = "\033[92m", "\033[2m", "\033[93m", "\033[1m"
R = "\033[0m"


def say(s="", pace=None):
    print(s, flush=True)
    time.sleep(PACE if pace is None else pace)


def main():
    global PACE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", default=os.path.join(ROOT, "data", "screenrec", "mdw_163340.mp4"))
    ap.add_argument("--adsb", default=os.path.join(ROOT, "cases", "case_E", "adsb.jsonl"))
    ap.add_argument("--t1", type=float, default=425.0); ap.add_argument("--t2", type=float, default=480.0)
    ap.add_argument("--pace", type=float, default=0.03, help="pause between printed lines, for filming (0 = none)")
    a = ap.parse_args(); PACE = a.pace

    say(f"{B}$ flyover-check: which flight made this sound?{R}", 0.4)
    say(f"{D}known location: Midway camera {CAM[0]:.5f}, {CAM[1]:.5f} (owner-published){R}")
    say(f"{D}audio: {os.path.basename(a.audio)}, {a.t1:.0f}-{a.t2:.0f} s of the recording{R}", 0.3)

    sys.path.insert(0, os.path.join(ROOT, "solver_paras"))
    import flight_id as FI                                   # the same tone tracker and pass model as results/flight_id_E
    from types import SimpleNamespace
    cfg = SimpleNamespace(win=0.256, nfft=16384, hop=0.05, f0min=900.0, f0max=1800.0, fstep=1.0, harmonics=4,
                          max_step=0.015, jump_pen=0.05)
    x, sr = FI.read_audio(a.audio)
    say(f"read {len(x)} samples at {sr} Hz ({len(x)/sr:.0f} s of audio)")
    say(f"STFT: window {cfg.win*1000:.0f} ms, FFT {cfg.nfft}, hop {cfg.hop*1000:.0f} ms, harmonic sum x1..x{cfg.harmonics}", 0.3)
    say(f"{Y}tracking the jet fan tone (900-1800 Hz fundamental) frame by frame{R}", 0.3)
    t, fz, z, P, df = FI.tone_track(x, sr, a.t1, a.t2, cfg)
    keep = FI.pass_span(t, fz, z, 1.5, 1.0)
    for i in range(len(t)):
        if keep[i]:
            say(f"  t={t[i]:7.2f} s   tone {fz[i]:7.1f} Hz   confidence {z[i]:5.1f}   {G}pass{R}")
        elif i % 3 == 0:
            say(f"{D}  t={t[i]:7.2f} s   tone {fz[i]:7.1f} Hz   confidence {z[i]:5.1f}{R}")
    ts, fs = t[keep], fz[keep]
    say(f"{G}tone found: {ts[0]:.2f}-{ts[-1]:.2f} s, {fs[0]:.0f} -> {fs[-1]:.0f} Hz, {len(ts)} frames{R}", 0.5)

    say(f"{Y}fitting a straight, constant-speed pass to the tone alone (no flight data){R}", 0.3)
    best = None
    for v0 in (40.0, 70.0, 100.0):
        for d0 in (100.0, 300.0, 1000.0):
            p0 = [math.sqrt(fs.max() * fs.min()), v0, d0, ts[np.argmin(np.gradient(fs, ts))]]
            r = least_squares(lambda p: FI.pass_model(p, ts) - fs, p0,
                              bounds=([500, 5, 10, ts.min() - 60], [3000, 250, 8000, ts.max() + 60]))
            rr = float(np.sqrt(np.mean(r.fun ** 2)))
            say(f"  start speed {v0*1.94384:5.0f} kt, closest {d0:5.0f} m  ->  {r.x[1]*1.94384:5.0f} kt, {r.x[2]:6.0f} m, rms {rr:6.2f} Hz")
            if best is None or r.cost < best.cost:
                best = r
    f0, v, d, tce = best.x
    tc = tce + d / C
    rms = float(np.sqrt(np.mean(best.fun ** 2)))
    say(f"{G}from the sound alone: speed {v*1.94384:.0f} kt, closest pass {d:.0f} m, at recording t = {tc:.1f} s (rms {rms:.1f} Hz){R}", 0.6)
    cpa_utc = T0 + tc - LATENCY
    say(f"{D}stream delay {LATENCY:.0f} s (from the clock burned into the video) -> real time of closest pass "
        f"{datetime.fromtimestamp(cpa_utc, timezone.utc).strftime('%H:%M:%S')} UTC{R}", 0.5)

    say(f"{Y}reading public ADS-B (adsb.lol) for every aircraft within 10 km, +-10 min{R}", 0.3)
    pts = {}
    n = 0
    with open(a.adsb) as fh:
        for line in fh:
            dd = json.loads(line)
            for ac in dd["ac"]:
                if "lat" not in ac or ac.get("alt_baro") in (None, "ground"):
                    continue
                tp = dd["t"] - (ac.get("seen_pos") or 0)
                if abs(tp - cpa_utc) > 600:
                    continue
                pts.setdefault(ac["hex"], []).append((tp, ac["lat"], ac["lon"], ac.get("alt_baro"), ac.get("gs"),
                                                      (ac.get("flight") or "").strip(), ac.get("t") or "?"))
                n += 1
    say(f"{n} positions from {len(pts)} aircraft")
    rows = []
    for h, p in sorted(pts.items()):
        best = None
        for tp, la, lo, alt, gs, fl, ty in p:
            r = math.hypot((la - CAM[0]) * 111320, (lo - CAM[1]) * 111320 * math.cos(math.radians(CAM[0])))
            if best is None or r < best[0]:
                best = (r, tp, alt, gs, fl, ty)
        if best[0] > 10000:
            continue
        fl = best[4] or next((q[5] for q in p if q[5]), h)
        dt = best[1] - cpa_utc
        mism = abs(dt) / 10 + abs(best[0] - d) / 500 + abs((best[3] or 0) - v * 1.94384) / 30
        rows.append((mism, fl, best[5], best[0], best[2], best[3], dt))
        say(f"  {fl:8s} {best[5]:5s}  closest {best[0]/1000:5.2f} km  alt {best[2]!s:>5} ft  {best[3] or 0:5.0f} kt  "
            f"dt {dt:+7.1f} s   mismatch {mism:6.2f}")
    rows.sort()
    say("", 0.3)
    say(f"{B}ranking (lower = closer match to the sound){R}", 0.3)
    for k, (m, fl, ty, r, alt, gs, dt) in enumerate(rows[:8]):
        col = G if k == 0 else ""
        say(f"{col}  {k+1}. {fl:8s} {ty:5s}  closest {r:5.0f} m  {gs or 0:4.0f} kt  dt {dt:+6.1f} s  mismatch {m:5.2f}{R}", 0.08)
    m1, m2 = rows[0][0], rows[1][0]
    say("", 0.3)
    say(f"{G}{B}match: {rows[0][1]} ({rows[0][2]}) passed {rows[0][3]:.0f} m from the camera, {rows[0][6]:+.1f} s from the moment the sound says."
        f" next best is {m2/m1:.0f}x worse.{R}", 0.5)
    say(f"{D}known location; what the sound identified is the flight.{R}", 0)


if __name__ == "__main__":
    sys.exit(main())
