# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile"]
# ///
"""Build a blind case package from a recording: anonymous audio + UTC start + search box + ADS-B.

    uv run recorder/make_case.py case_B [--from 2026-11-03T13:00:00Z] [--to 2026-11-03T14:30:00Z]

Writes cases/<case>/audio.flac (mono 16 kHz, no tags), cases/<case>/case.json, cases/<case>/adsb.jsonl.
Missing HLS segments are filled with silence so sample time stays exact; gaps are listed in case.json.
"""
import argparse, csv, json, os, subprocess, sys, tempfile
from datetime import datetime, timezone

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 16000


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def decode_run(paths):
    """Concatenate a contiguous run of .ts segments and decode to mono float32 at SR."""
    with tempfile.TemporaryDirectory() as tmp:
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            for p in paths:
                f.write(f"file '{p.replace(os.sep, '/')}'\n")
        out = subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                              "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                             capture_output=True, check=True)
    return np.frombuffer(out.stdout, dtype=np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    a = ap.parse_args()
    raw = os.path.join(ROOT, "data", "raw", a.case)
    cams = {c["case"]: c for c in json.load(open(os.path.join(ROOT, "data", "sealed", "cams.json")))["cams"]}
    rows = sorted(({"seq": int(r["seq"]), "t": ts(r["utc_start"]), "d": float(r["duration_s"]),
                    "p": os.path.join(raw, "seg", r["file"])} for r in csv.DictReader(open(os.path.join(raw, "index.csv")))),
                  key=lambda r: r["seq"])
    if a.t0:
        rows = [r for r in rows if r["t"] >= ts(a.t0)]
    if a.t1:
        rows = [r for r in rows if r["t"] + r["d"] <= ts(a.t1)]
    if not rows:
        sys.exit("no segments in range")
    start = rows[0]["t"]
    total = rows[-1]["t"] + rows[-1]["d"] - start
    audio = np.zeros(int(round(total * SR)) + SR, dtype=np.float32)
    runs, cur = [], [rows[0]]
    for r in rows[1:]:
        if r["seq"] == cur[-1]["seq"] + 1:
            cur.append(r)
        else:
            runs.append(cur)
            cur = [r]
    runs.append(cur)
    gaps = []
    for i, run in enumerate(runs):
        x = decode_run([r["p"] for r in run])
        off = int(round((run[0]["t"] - start) * SR))
        audio[off:off + len(x)] = x[: len(audio) - off]
        if i:
            prev = runs[i - 1][-1]
            gaps.append([round(prev["t"] + prev["d"] - start, 2), round(run[0]["t"] - start, 2)])
    audio = audio[: int(round(total * SR))]
    out = os.path.join(ROOT, "cases", a.case)
    os.makedirs(out, exist_ok=True)
    sf.write(os.path.join(out, "audio.flac"), audio, SR, subtype="PCM_16")
    t_end = start + total
    n = 0
    with open(os.path.join(out, "adsb.jsonl"), "w") as f:
        for name in (f"{a.case}.jsonl", f"{a.case}.traces.jsonl"):
            src = os.path.join(ROOT, "data", "raw", "adsb", name)
            if not os.path.exists(src):
                continue
            b = cams[a.case]["bbox"]
            for line in open(src):
                d = json.loads(line)
                if not (start - 600 <= d["t"] <= t_end + 600):
                    continue
                if d["src"] == "adsblol-trace":  # full-day traces: keep only points within 30 km of the box
                    p0 = d["ac"][0]
                    if not (b[0] - 0.3 <= p0["lat"] <= b[2] + 0.3 and b[1] - 0.45 <= p0["lon"] <= b[3] + 0.45):
                        continue
                f.write(line)
                n += 1
    case = {
        "case": a.case,
        "audio": f"cases/{a.case}/audio.flac",
        "sample_rate": SR,
        "utc_start": datetime.fromtimestamp(start, timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_s": round(total, 2),
        "gaps_s": gaps,
        "bbox": cams[a.case]["bbox"],
        "bbox_note": "40x40 km search box; contains the microphone; its centre was offset at random",
        "adsb": f"cases/{a.case}/adsb.jsonl",
        "adsb_polls": n,
        "adsb_sources": "live adsb.lol /v2/point polls around the box centre (src=adsblol) plus each polled aircraft's "
                        "full-day trace from globe.adsb.lol (src=adsblol-trace, seen_pos=0)",
        "clock": "utc_start is #EXT-X-PROGRAM-DATE-TIME of the first HLS segment; camera-to-ingest latency unknown",
    }
    json.dump(case, open(os.path.join(out, "case.json"), "w"), indent=1)
    print(json.dumps({k: case[k] for k in ("case", "utc_start", "duration_s", "adsb_polls")}), "gaps:", len(gaps))


if __name__ == "__main__":
    main()
