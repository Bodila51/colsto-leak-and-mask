#!/usr/bin/env python
"""Midway triptych: the stream clip (top left), a live spectrogram of its audio with SWA4113's predicted Doppler curve
(top right) and a replay of the real flyover-check run plus the aircraft's ADS-B reports (bottom).

    data\\vendor\\doppler-locate\\.venv\\Scripts\\python.exe post/anim/midway_triptych.py [--check]

Writes (LOCAL ONLY: the owner's screen recording of the StreamTime LIVE Midway stream and its audio; *.mp4 is git-ignored)
  post/midway_triptych.mp4            1920x1080: clip 960x540 | spectrogram 960x540 (livespec.html) / terminal 1920x540
  post/midway_triptych_vertical.mp4   1080x1350: clip 1080x608, spectrogram 1080x500, terminal 1080x242
--check writes QA frames at 0/7/10/13/19.9 s to build/qa_mid instead.
Known camera, not blind: the curve is midway_curve.predict() (paras.py geometry at the published camera, L = 28 s).
Terminal lines: the check command and its real stdout (data/look/midway_check/check_stdout.txt), one added note, the
ADS-B reports of SWA4113 in cases/case_E/adsb.jsonl around closest approach, the Doppler relation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ANIM = Path(__file__).resolve().parent
POST = ANIM.parent
ROOT = POST.parent
BUILD = ANIM / "build"
CLIP = POST / "midway_demo_clip.mp4"
CHECK = ROOT / "data" / "look" / "midway_check" / "check_stdout.txt"
FPS, NFRAMES = 30, 600
TYPES = {"B38M": "Boeing 737 MAX 8"}
QA_T = "0,7,10,13,19.9"
sys.path.insert(0, str(ANIM))


def run(cmd, **kw):
    print("[midway] $", " ".join(str(c) for c in cmd)[:180], flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def adsb_rows(hexid, t_cpa, before=6.0, after=30.0):
    """Position reports of one aircraft around its closest approach (time of position = poll time - seen_pos)."""
    rows = {}
    with open(ROOT / "cases" / "case_E" / "adsb.jsonl", encoding="utf-8") as f:
        for line in f:
            if hexid not in line:
                continue
            r = json.loads(line)
            for a in r["ac"]:
                if a.get("hex") == hexid and a.get("lat") is not None:
                    t = round(r["t"] - (a.get("seen_pos") or 0.0), 1)
                    if -before <= t - t_cpa <= after and (a["lat"], a["lon"]) not in [(v["lat"], v["lon"]) for v in rows.values()]:
                        rows[t] = a
    return sorted(rows.items())


def terminal(cur):
    lines = [dict(t=0.15, kind="cmd", text="uv run skills/flyover-check/scripts/check.py midway_demo_clip.mp4", type_s=0.9)]
    out = [l.rstrip() for l in CHECK.read_text(encoding="utf-8").splitlines() if l.strip()]
    t = 1.3
    for l in out:
        lines.append(dict(t=round(t, 2), kind="res" if l.startswith(("RESULT", "flyover-check")) else "log", text=l))
        t += 0.28
        if l.strip().startswith("- Plane 2"):   # the one added line: both detections are this aircraft (the curve fits both)
            lines.append(dict(t=round(t, 2), kind="note", text="note: plane 1 and plane 2 are the same aircraft before and after it passed overhead"))
            t += 0.28
    rows = adsb_rows(cur["hex"], cur["cpa_emit_utc"])
    tc = cur["cpa_clip_s"]                     # the report nearest the closest approach shows up when the clip reaches it
    k = min(range(len(rows)), key=lambda i: abs(rows[i][0] - cur["cpa_emit_utc"]))
    lines.append(dict(t=round(max(t, tc - 0.4 * k - 0.8), 2), kind="head",
                      text=f"cases/case_E/adsb.jsonl · {cur['flight']} ({cur['hex']}) around closest approach · UTC, lat, lon, altitude, ground speed"))
    for i, (ts, a) in enumerate(rows):
        hh = datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S.%f")[:-5]
        alt = f"alt {a['alt_baro']:,} ft baro" if isinstance(a.get("alt_baro"), (int, float)) else f"alt {a.get('alt_baro')}"
        if isinstance(a.get("alt_geom"), (int, float)):
            alt += f" / {a['alt_geom']:,} ft geom"
        gs = f"gs {a['gs']:.1f} kt" if a.get("gs") is not None else "gs  n/a"
        lines.append(dict(t=round(tc + 0.4 * (i - k) if i <= k else tc + 0.12 * (i - k), 2), kind="hi" if i == k else "log",
                          text=f"{hh}Z  {a['lat']:.6f}  {a['lon']:.6f}  {alt:<34} {gs}"))
    lines.append(dict(t=round(tc + 0.12 * (len(rows) - k) + 0.5, 2), kind="hi", text="doppler: f = f0·c/(c+v_r) → tone falls as it passes"))
    return dict(title="flyover-leak · flyover-check skill · Midway demo clip",
                label="replay of the real check output (it ran in 11.0 s)", caption="", caption_sub="",
                video_tag="STREAM · owner's screen recording · its own audio",
                layout=dict(overlayv=dict(video=dict(x=0, y=0, w=1080, h=608), divs=[[0, 608, 1080, 608], [0, 1108, 1080, 1108]])),
                lines=lines)


def main():
    t0 = time.time()
    from midway_curve import predict
    import build
    cur = predict()
    BUILD.mkdir(exist_ok=True)
    spec = dict(f_min_hz=500.0, f_max_hz=7500.0, window_s=0.15)
    img, _, _ = build.spectrogram(CLIP, spec)
    Image.fromarray(img).save(BUILD / "mid_spec.png")
    n = int(round(cur["cpa_slant_m"], -1))
    (BUILD / "livespec.json").write_text(json.dumps(dict(
        spec="build/mid_spec.png", meta=dict(n_cols=img.shape[1], hop_s=build.HOP / build.SR, **{k: spec[k] for k in ("f_min_hz", "f_max_hz")}),
        curves=cur["curves"], cpa_t=cur["cpa_clip_s"], f0_hz=cur["f0_hz"],
        step="LIVE · AUDIO OF THIS CLIP · 500-7500 Hz", title="Its pitch falls as it passes overhead",
        label=f"predicted from public flight data: {cur['flight']} · {TYPES.get(cur['type'], cur['type'])} · ~{n} m overhead",
        badge="known camera — this is a demo of the effect, not a blind find",
        credits="stream: StreamTime LIVE (Midway) · data: adsb.lol (ODbL) · method: colsto",
        curve_note=f"shape: paras.py at the published camera, L = {cur['L_s']} s; f0 = {cur['f0_hz']} Hz fitted to the clip (x1-x3)",
        cpa=dict(slant_m=cur["cpa_slant_m"], horizontal_m=cur["cpa_horizontal_m"], agl_m=cur["cpa_agl_m"])), ensure_ascii=False), encoding="utf-8")
    (BUILD / "midway_terminal.json").write_text(json.dumps(terminal(cur), ensure_ascii=False), encoding="utf-8")
    print(f"[midway] curve f0 {cur['f0_hz']} Hz, CPA {cur['cpa_clip_s']} s into the clip, {cur['cpa_slant_m']} m slant ({time.time() - t0:.0f}s)")

    cap = ANIM / "tools" / "cap.js"
    q = ["--query", "data=midway_terminal"]
    if "--check" in sys.argv:
        for lay, page, extra in (("panel", "livespec.html", []), ("panelv", "livespec.html", []), ("term", "terminal.html", q),
                                 ("termv", "terminal.html", q), ("overlay", "terminal.html", q), ("overlayv", "terminal.html", q)):
            run(["node", cap, "--page", page, "--layout", lay, *extra, "--out", BUILD / "qa_mid" / lay, "--times", QA_T, "--workers", "5"])
        return
    fr = {k: BUILD / f"frames_mid_{k}" for k in ("panel", "panelv", "term", "termv")}
    for d in list(fr.values()) + [BUILD / "ov_mid", BUILD / "ovv_mid"]:
        shutil.rmtree(d, ignore_errors=True)
    jobs = [["node", cap, "--page", "livespec.html", "--layout", "panel", "--out", fr["panel"]],
            ["node", cap, "--page", "livespec.html", "--layout", "panelv", "--out", fr["panelv"]],
            ["node", cap, "--page", "terminal.html", "--layout", "term", *q, "--out", fr["term"]],
            ["node", cap, "--page", "terminal.html", "--layout", "termv", *q, "--out", fr["termv"]]]
    procs = [subprocess.Popen([str(c) for c in j + ["--from", "0", "--to", str(NFRAMES), "--workers", "4"]]) for j in jobs]
    if any(p.wait() for p in procs):
        sys.exit("frame capture failed")
    run(["node", cap, "--page", "terminal.html", "--layout", "overlay", *q, "--out", BUILD / "ov_mid", "--times", "0", "--workers", "1"])
    run(["node", cap, "--page", "terminal.html", "--layout", "overlayv", *q, "--out", BUILD / "ovv_mid", "--times", "0", "--workers", "1"])
    t1 = time.time()
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
           "-color_trc", "bt709", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-t", "20", "-r", str(FPS)]
    seq = lambda d: ["-framerate", str(FPS), "-start_number", "0", "-i", d / "f%04d.jpg"]  # noqa: E731
    tv = "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x05070a:s=1920x1080:r={FPS}:d=20", "-i", CLIP,
         *seq(fr["panel"]), *seq(fr["term"]), "-i", BUILD / "ov_mid" / "t0.png",
         "-filter_complex", "[1:v]scale=960:540:flags=lanczos,setsar=1[w];[0:v][w]overlay=0:0:eof_action=repeat[a];[a][2:v]overlay=960:0[b];"
                            f"[b][3:v]overlay=0:540[c];[c][4:v]overlay=0:0,{tv}[v]",
         "-map", "[v]", "-map", "1:a", *enc, POST / "midway_triptych.mp4"])
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x05070a:s=1080x1350:r={FPS}:d=20", "-i", CLIP,
         *seq(fr["panelv"]), *seq(fr["termv"]), "-i", BUILD / "ovv_mid" / "t0.png",
         "-filter_complex", "[1:v]scale=1080:608:flags=lanczos,setsar=1[w];[0:v][w]overlay=0:0:eof_action=repeat[a];[a][2:v]overlay=0:608[b];"
                            f"[b][3:v]overlay=0:1108[c];[c][4:v]overlay=0:0,{tv}[v]",
         "-map", "[v]", "-map", "1:a", *enc, POST / "midway_triptych_vertical.mp4"])
    for d in fr.values():
        shutil.rmtree(d, ignore_errors=True)
    print(f"[midway] done: capture {t1 - t0:.0f}s, encode {time.time() - t1:.0f}s -> post/midway_triptych.mp4, post/midway_triptych_vertical.mp4")


if __name__ == "__main__":
    main()
