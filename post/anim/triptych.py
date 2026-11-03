#!/usr/bin/env python
"""Miami triptych: the webcam (top left), our analysis (top right) and a replay of the real run log (bottom).

    data\\vendor\\doppler-locate\\.venv\\Scripts\\python.exe post/anim/triptych.py

Writes (LOCAL ONLY: they contain the PTZtv webcam footage and its audio, which may not be copied; *.mp4 is git-ignored)
  post/miami_triptych.mp4            1920x1080: webcam 960x540 | analysis 960x540 (index.html?layout=panel) / terminal 1920x540
  post/miami_triptych_vertical.mp4   1080x1350: webcam 1080x500, analysis 1080x608, terminal 1080x242
Inputs: post/miami_monitor1_recording.mp4 (1280x720, 20 s, its t = 0 is the analysis clip t = 0, stream clock 13:30:21.77Z),
post/anim_data_miami.json, and for the terminal only real files: the commands in post/numbers.md (N12), run.log, answer.json,
case_A_evaluation.json (see terminal_lines).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ANIM = Path(__file__).resolve().parent
POST = ANIM.parent
ROOT = POST.parent
BUILD = ANIM / "build"
RES = ROOT / "results" / "case_A_ridge_v11"
MON1 = POST / "miami_monitor1_recording.mp4"
FPS, NFRAMES = 30, 600
# crops of the 1280x720 webcam frame: below the burned-in text line (top 36 px), above the PTZtv logo (bottom right, y >= 655)
CROP_L = "crop=1092:614:94:36,scale=960:540:flags=lanczos,setsar=1"     # 16:9
CROP_V = "crop=1280:593:0:36,scale=1080:500:flags=lanczos,setsar=1"     # 2.16:1
COMMANDS = ["git worktree add solver_ridge_v11_ref 6891c89",              # post/numbers.md N12: how results/case_A_ridge_v11 was made
            "python solver_ridge_v11_ref/solver_ridge/ridge.py cases/case_A --out results/case_A_ridge_v11"]


def run(cmd, **kw):
    print("[triptych] $", " ".join(str(c) for c in cmd)[:180], flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def terminal_lines():
    """The terminal script: every text is a repo command or comes from a results file; only the times are ours."""
    ans = json.loads((RES / "answer.json").read_text(encoding="utf-8"))
    ev = json.loads((ROOT / "results" / "case_A_evaluation.json").read_text(encoding="utf-8"))
    run_ = next(r for r in ev["runs"] if r["answer"].endswith("case_A_ridge_v11/answer.json"))
    log = [l.rstrip() for l in (RES / "run.log").read_text(encoding="utf-8").splitlines() if l.strip()]
    lines = [dict(t=0.15, kind="cmd", text=COMMANDS[0], type_s=0.6),
             dict(t=1.0, kind="cmd", text=COMMANDS[1], type_s=1.0)]
    # run.log, paced like the real run (spectrogram, candidates, the long coarse grid, peaks, answer)
    lt = [2.2, 2.6, 3.0, 5.0, 5.4, 5.7, 6.0, 6.3, 6.8, 7.2, 7.6]
    for i, l in enumerate(log):
        lines.append(dict(t=lt[min(i, len(lt) - 1)] + 0.25 * max(0, i - len(lt) + 1), kind="log", text=l))
    ac = ans["aircraft"]
    cmax = max(a["contribution"] for a in ac)
    lines.append(dict(t=8.2, kind="head", text=f"results/case_A_ridge_v11/answer.json · {len(ac)} scored aircraft "
                                             f"(callsign, type, closest slant distance, contribution)"))
    t0, t1 = 8.5, 15.0
    for i, a in enumerate(ac):
        lines.append(dict(t=round(t0 + (t1 - t0) * i / max(1, len(ac) - 1), 3), kind="ac", rank=i + 1, bar=round(a["contribution"] / cmax, 4),
                          cells=[f"{i + 1:>2}", a["callsign"] or a["hex"], a["type"] or "?", f"closest {a['closest_slant_m']:>7,.0f} m",
                                 f"contribution {a['contribution']:.3f}"]))
    ru = ans["runner_up"]
    lines += [
        dict(t=15.45, kind="head", text="results/case_A_ridge_v11/answer.json"),
        dict(t=15.7, kind="res", text=f"answer     {ans['lat']:.6f}, {ans['lon']:.6f}   latency {ans['latency_s']} s   "
                                      f"score {ans['score']:.3f} = {ans['score_over_null']}x the grid median"),
        dict(t=15.95, kind="log", text=f"runner-up  {ru['lat']:.6f}, {ru['lon']:.6f}   latency {ru['latency_s']} s   "
                                       f"score {ru['score']:.3f}   {ru['distance_from_answer_m'] / 1000:.1f} km away"),
        dict(t=16.35, kind="hi", text=f"error vs sealed truth: {run_['error_m']:,} m  (results/case_A_evaluation.json)"),
        dict(t=16.75, kind="log", text=f"truth: {ev['camera']}, seal hash ok: {str(ev['truth_hash_ok']).lower()}"),
    ]
    done = next((l for l in log if l.startswith("done in")), "")
    secs = float(done.split()[2].rstrip("s")) if done else None
    top = ac[0]
    return dict(
        title="flyover-leak · ridge solver v1.1 · case_A (blind)",
        label="replay of the real run log" + (f" · {secs:,.0f} s run shown in 20 s" if secs else ""),
        caption=f"matched aircraft {top['callsign']} passed {top['closest_slant_m'] / 1000:.1f} km away, out of frame — the microphone only hears it",
        caption_sub=f"webcam: {ev['camera']} · its own audio",
        video_tag="WEBCAM · same moment as the analysis",
        lines=lines)


def main():
    t0 = time.time()
    if not MON1.exists():
        sys.exit(f"missing {MON1}")
    run([sys.executable, ANIM / "build.py", "--data", POST / "anim_data_miami.json"])
    (BUILD / "terminal.json").write_text(json.dumps(terminal_lines(), ensure_ascii=False), encoding="utf-8")
    cap = ANIM / "tools" / "cap.js"
    fr = {k: BUILD / f"frames_{k}" for k in ("panel", "term", "termv")}
    for d in list(fr.values()) + [BUILD / "ov", BUILD / "ovv"]:
        shutil.rmtree(d, ignore_errors=True)
    jobs = [["node", cap, "--layout", "panel", "--out", fr["panel"], "--from", "0", "--to", str(NFRAMES), "--workers", "6"],
            ["node", cap, "--page", "terminal.html", "--layout", "term", "--out", fr["term"], "--from", "0", "--to", str(NFRAMES), "--workers", "4"],
            ["node", cap, "--page", "terminal.html", "--layout", "termv", "--out", fr["termv"], "--from", "0", "--to", str(NFRAMES), "--workers", "4"]]
    procs = [subprocess.Popen([str(c) for c in j]) for j in jobs]
    if any(p.wait() for p in procs):
        sys.exit("frame capture failed")
    run(["node", cap, "--page", "terminal.html", "--layout", "overlay", "--out", BUILD / "ov", "--times", "0", "--workers", "1"])
    run(["node", cap, "--page", "terminal.html", "--layout", "overlayv", "--out", BUILD / "ovv", "--times", "0", "--workers", "1"])
    t1 = time.time()
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
           "-color_trc", "bt709", "-movflags", "+faststart", "-c:a", "aac", "-b:a", "192k", "-t", "20", "-r", str(FPS)]
    seq = lambda d: ["-framerate", str(FPS), "-start_number", "0", "-i", d / "f%04d.jpg"]  # noqa: E731
    tv = "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x05070a:s=1920x1080:r={FPS}:d=20", "-i", MON1,
         *seq(fr["panel"]), *seq(fr["term"]), "-i", BUILD / "ov" / "t0.png",
         "-filter_complex", f"[1:v]{CROP_L}[w];[0:v][w]overlay=0:0:eof_action=repeat[a];[a][2:v]overlay=960:0[b];[b][3:v]overlay=0:540[c];"
                            f"[c][4:v]overlay=0:0,{tv}[v]",
         "-map", "[v]", "-map", "1:a", *enc, POST / "miami_triptych.mp4"])
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x05070a:s=1080x1350:r={FPS}:d=20", "-i", MON1,
         *seq(fr["panel"]), *seq(fr["termv"]), "-i", BUILD / "ovv" / "t0.png",
         "-filter_complex", f"[1:v]{CROP_V}[w];[2:v]scale=1080:608:flags=lanczos[p];[0:v][w]overlay=0:0:eof_action=repeat[a];[a][p]overlay=0:500[b];"
                            f"[b][3:v]overlay=0:1108[c];[c][4:v]overlay=0:0,{tv}[v]",
         "-map", "[v]", "-map", "1:a", *enc, POST / "miami_triptych_vertical.mp4"])
    for d in fr.values():
        shutil.rmtree(d, ignore_errors=True)
    print(f"[triptych] done: capture {t1 - t0:.0f}s, encode {time.time() - t1:.0f}s -> post/miami_triptych.mp4, post/miami_triptych_vertical.mp4")


if __name__ == "__main__":
    main()
