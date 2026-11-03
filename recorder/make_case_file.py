# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile"]
# ///
"""Build a blind case package from a finished video file (e.g. a screen recording of a public live stream).

    uv run recorder/make_case_file.py case_E --file data/clip/case_E_test.mp4 --utc-start 2026-11-03T14:25:17Z

utc_start is the wall-clock UTC of the file's first sample (for a screen recording: the recording's start time).
The stream's own delay behind real time is unknown and is left to the solver's latency scan.
"""
import argparse, json, os, subprocess
from datetime import datetime, timezone

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 16000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--file", required=True)
    ap.add_argument("--utc-start", required=True)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", a.file, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    out = os.path.join(ROOT, "cases", a.case)
    os.makedirs(out, exist_ok=True)
    sf.write(os.path.join(out, "audio.flac"), audio, SR, subtype="PCM_16")
    cams = {c["case"]: c for c in json.load(open(os.path.join(ROOT, "data", "sealed", "cams.json")))["cams"]}
    b = cams[a.case]["bbox"]
    t0 = datetime.fromisoformat(a.utc_start.replace("Z", "+00:00")).timestamp()
    t1 = t0 + len(audio) / SR
    n = 0
    with open(os.path.join(out, "adsb.jsonl"), "w") as f:
        for name in (f"{a.case}.jsonl", f"{a.case}.traces.jsonl"):
            src = os.path.join(ROOT, "data", "raw", "adsb", name)
            if not os.path.exists(src):
                continue
            for line in open(src):
                d = json.loads(line)
                if not (t0 - 600 <= d["t"] <= t1 + 600):
                    continue
                if d["src"] == "adsblol-trace":
                    p0 = d["ac"][0]
                    if not (b[0] - 0.3 <= p0["lat"] <= b[2] + 0.3 and b[1] - 0.45 <= p0["lon"] <= b[3] + 0.45):
                        continue
                f.write(line)
                n += 1
    case = {"case": a.case, "audio": f"cases/{a.case}/audio.flac", "sample_rate": SR,
            "utc_start": datetime.fromtimestamp(t0, timezone.utc).isoformat().replace("+00:00", "Z"),
            "duration_s": round(len(audio) / SR, 2), "gaps_s": [], "bbox": b,
            "bbox_note": "40x40 km search box; contains the microphone; its centre was offset at random",
            "adsb": f"cases/{a.case}/adsb.jsonl", "adsb_polls": n,
            "adsb_sources": "live adsb.lol /v2/point polls around the box centre (src=adsblol) plus each polled aircraft's "
                            "full-day trace from globe.adsb.lol (src=adsblol-trace, seen_pos=0)",
            "clock": "utc_start is the wall-clock start of the file; the stream's delay behind real time is unknown",
            "note": a.note}
    json.dump(case, open(os.path.join(out, "case.json"), "w"), indent=1)
    print(json.dumps({k: case[k] for k in ("case", "utc_start", "duration_s", "adsb_polls")}))


if __name__ == "__main__":
    main()
