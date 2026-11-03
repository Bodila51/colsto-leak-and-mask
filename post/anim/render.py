#!/usr/bin/env python
"""Render every deliverable of the post animation.

    python post/anim/render.py                 # post/anim_data.json if it exists, else the watermarked preview data
    python post/anim/render.py --preview       # force post/anim/preview_data.json
    python post/anim/render.py --data PATH     # any data file (see post/anim/DATA.md)
    python post/anim/render.py --stills-only   # only the five stills (fast check)

Writes
  post/monitor2_analysis.mp4   1920x1080, 20 s, 30 fps, no audio (second monitor; our visuals only)
  post/post_no_footage.mp4     1080x1350, 20 s, no audio: our visuals only (spectrogram, curves, map, numbers). Publishable.
  post/post_vertical.mp4       1080x1350 with the stream footage on top and its audio. LOCAL ONLY: the footage may not be copied.
  post/still_01_spectrogram.png .. still_05_safe.png   1080x1350, our visuals only (publishable)
  data/post_local/still_0N_*_with_footage.png          the same with a stream frame on top. LOCAL ONLY (data/ is git-ignored).
Never commit an mp4 that contains the stream footage (see the root .gitignore).
Needs: python 3.12 with numpy, pillow, matplotlib; node with playwright (post/anim/tools, or the shared copy at
~/Downloads/legends-video/tools); ffmpeg on PATH; the chromium that Playwright installed.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ANIM = Path(__file__).resolve().parent
POST = ANIM.parent
ROOT = POST.parent
BUILD = ANIM / "build"
MON1 = POST / "monitor1_recording.mp4"
FPS, NFRAMES = 30, 600
STILLS = [("01_spectrogram", 6.5), ("02_match", 10.1), ("03_two_places", 13.8), ("04_found", 17.5), ("05_safe", 19.6)]


def run(cmd, **kw):
    print("[render] $", " ".join(str(c) for c in cmd)[:200], flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def cap(layout, out, *extra):
    if out.exists():
        shutil.rmtree(out)
    run(["node", ANIM / "tools" / "cap.js", "--layout", layout, "--out", out, *extra])


def ffmpeg(*args):
    run(["ffmpeg", "-v", "error", "-y", *args])


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
       "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart"]
TV = "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"


def stills(data_name: str):
    times = ",".join(str(t) for _, t in STILLS)
    tall, vert = BUILD / "stills_t", BUILD / "stills_v"
    cap("tall", tall, "--times", times, "--workers", "5")
    cap("vertical", vert, "--times", times, "--workers", "5")
    local = ROOT / "data" / "post_local"
    local.mkdir(parents=True, exist_ok=True)
    for name, t in STILLS:
        key = f"t{str(t).replace('.', '_')}.png"
        Image.open(tall / key).convert("RGB").save(POST / f"still_{name}.png")           # no footage: publishable
        m = BUILD / f"mon_{name}.png"
        ffmpeg("-ss", f"{t:.3f}", "-i", MON1, "-frames:v", "1", "-vf", "scale=1080:608:flags=lanczos", m)
        im = Image.new("RGB", (1080, 1350), (5, 7, 10))
        im.paste(Image.open(m).convert("RGB"), (0, 0))
        im.paste(Image.open(vert / key).convert("RGB"), (0, 608))
        im.save(local / f"still_{name}_with_footage.png")                                 # footage: local only
        print(f"[render] still_{name}.png  (t = {t} s, {data_name})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--data")
    ap.add_argument("--stills-only", action="store_true")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--workers", default="8")
    a = ap.parse_args()
    t0 = time.time()

    if a.preview:
        build_args, name = ["--preview"], "PREVIEW data"
    elif a.data:
        build_args, name = ["--data", a.data], a.data
    elif (POST / "anim_data.json").exists():
        build_args, name = ["--data", str(POST / "anim_data.json")], "post/anim_data.json"
    else:
        build_args, name = ["--preview"], "PREVIEW data (post/anim_data.json not found)"
    print(f"[render] data: {name}", flush=True)
    run([sys.executable, ANIM / "build.py", *build_args])
    t1 = time.time()

    if not a.stills_only:
        fl, fv, ft = BUILD / "frames_l", BUILD / "frames_v", BUILD / "frames_t"
        for d in (fl, fv, ft):
            shutil.rmtree(d, ignore_errors=True)
        # three layouts at once: 8 pages each
        procs = [subprocess.Popen(["node", str(ANIM / "tools" / "cap.js"), "--layout", lay, "--out", str(out), "--from", "0",
                                   "--to", str(NFRAMES), "--workers", a.workers]) for lay, out in (("landscape", fl), ("vertical", fv), ("tall", ft))]
        if any(p.wait() for p in procs):
            sys.exit("frame capture failed")
        ffmpeg("-framerate", str(FPS), "-start_number", "0", "-i", fl / "f%04d.jpg", "-vf", TV, "-r", str(FPS), *ENC,
               POST / "monitor2_analysis.mp4")
        ffmpeg("-framerate", str(FPS), "-start_number", "0", "-i", ft / "f%04d.jpg", "-vf", TV, "-r", str(FPS), *ENC, "-an",
               POST / "post_no_footage.mp4")
        # LOCAL ONLY (stream footage): black 1080x1350 base, the analysis panel at y=608, monitor 1 on top (its own timestamps keep the sync)
        ffmpeg("-f", "lavfi", "-i", f"color=c=0x05070a:s=1080x1350:r={FPS}:d=20", "-framerate", str(FPS), "-start_number", "0",
               "-i", fv / "f%04d.jpg", "-i", MON1,
               "-filter_complex", f"[2:v]scale=1080:608:flags=lanczos,setsar=1[m];[0:v][1:v]overlay=0:608:shortest=0[a];"
                                  f"[a][m]overlay=0:0:eof_action=repeat,{TV}[v]",
               "-map", "[v]", "-map", "2:a", "-t", "20", "-r", str(FPS), *ENC, "-c:a", "aac", "-b:a", "192k",
               POST / "post_vertical.mp4")
        if not a.keep_frames:
            for d in (fl, fv, ft):
                shutil.rmtree(d, ignore_errors=True)
    stills(name)
    t2 = time.time()
    print(f"[render] done: build {t1 - t0:.0f}s, video+stills {t2 - t1:.0f}s, total {t2 - t0:.0f}s  ({name})")


if __name__ == "__main__":
    main()
