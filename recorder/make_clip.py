# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "soundfile"]
# ///
"""Make the test clip: a piece of a recorded webcam stream with a voice talking over it.

    uv run recorder/make_clip.py case_X --from 2026-11-03T13:40:00Z --to 2026-11-03T13:52:00Z \
        --voice data/voice/walk-corey.mp3 --voice-at 20 300 --clip-case case_V

Writes data/clip/<clip-case>.mp4 (webcam video + webcam audio + voice, no metadata tags) and a blind case
package cases/<clip-case>/ with its own random search box. The clip's truth (same camera) is written to
data/sealed/truth/<clip-case>.json so it can be sealed before solving.
"""
import argparse, csv, json, math, os, secrets, random, subprocess, tempfile
from datetime import datetime, timezone

import numpy as np
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 16000


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def random_box(lat, lon, airport, rng):
    """40x40 km box containing (lat, lon), centre offset 4-17 km, not within 4 km of the airport."""
    while True:
        dx, dy = rng.uniform(-12, 12), rng.uniform(-12, 12)
        if math.hypot(dx, dy) < 4:
            continue
        clat, clon = lat + dy / 111.32, lon + dx / (111.32 * math.cos(math.radians(lat)))
        ay, ax = (clat - airport[0]) * 111.32, (clon - airport[1]) * 111.32 * math.cos(math.radians(lat))
        if math.hypot(ax, ay) >= 4:
            hl, ho = 20 / 111.32, 20 / (111.32 * math.cos(math.radians(clat)))
            return [round(clat - hl, 5), round(clon - ho, 5), round(clat + hl, 5), round(clon + ho, 5)], [round(dx, 3), round(dy, 3)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case")
    ap.add_argument("--from", dest="t0", required=True)
    ap.add_argument("--to", dest="t1", required=True)
    ap.add_argument("--voice", required=True)
    ap.add_argument("--voice-at", type=float, nargs="+", default=[15.0], help="seconds into the clip")
    ap.add_argument("--voice-gain-db", type=float, default=0.0)
    ap.add_argument("--clip-case", default="case_V")
    a = ap.parse_args()

    raw = os.path.join(ROOT, "data", "raw", a.case)
    rows = sorted((r for r in csv.DictReader(open(os.path.join(raw, "index.csv")))
                   if ts(a.t0) <= ts(r["utc_start"]) and ts(r["utc_start"]) + float(r["duration_s"]) <= ts(a.t1)),
                  key=lambda r: int(r["seq"]))
    seqs = [int(r["seq"]) for r in rows]
    missing = (seqs[-1] - seqs[0] + 1) - len(seqs)
    if missing:
        raise SystemExit(f"{missing} segments missing in that window; pick a gap-free window")
    utc_start = rows[0]["utc_start"]
    os.makedirs(os.path.join(ROOT, "data", "clip"), exist_ok=True)
    out = os.path.join(ROOT, "data", "clip", f"{a.clip_case}.mp4")

    with tempfile.TemporaryDirectory() as tmp:
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            for r in rows:
                f.write(f"file '{os.path.join(raw, 'seg', r['file']).replace(os.sep, '/')}'\n")
        delays = "".join(f"[1:a]adelay={int(t * 1000)}:all=1,volume={a.voice_gain_db}dB[v{i}];" for i, t in enumerate(a.voice_at))
        mix = "".join(f"[v{i}]" for i in range(len(a.voice_at)))
        graph = f"{delays}[0:a]{mix}amix=inputs={1 + len(a.voice_at)}:duration=first:normalize=0[aout]"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", a.voice,
                        "-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
                        "-b:a", "160k", "-map_metadata", "-1", "-movflags", "+faststart", out], check=True)

    # blind case package from the clip itself
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    cdir = os.path.join(ROOT, "cases", a.clip_case)
    os.makedirs(cdir, exist_ok=True)
    sf.write(os.path.join(cdir, "audio.flac"), audio, SR, subtype="PCM_16")
    cams = {c["case"]: c for c in json.load(open(os.path.join(ROOT, "data", "sealed", "cams.json")))["cams"]}
    truth = json.load(open(os.path.join(ROOT, "data", "sealed", "truth", f"{a.case}.json")))
    seed = secrets.randbits(32)
    bbox, off = random_box(truth["lat"], truth["lon"], cams[a.case]["airport"], random.Random(seed))
    t0, dur = ts(utc_start), len(audio) / SR
    n = 0
    with open(os.path.join(cdir, "adsb.jsonl"), "w") as f:
        for name in (f"{a.case}.jsonl", f"{a.case}.traces.jsonl"):
            src = os.path.join(ROOT, "data", "raw", "adsb", name)
            if not os.path.exists(src):
                continue
            b = bbox
            for line in open(src):
                d = json.loads(line)
                if not (t0 - 600 <= d["t"] <= t0 + dur + 600):
                    continue
                if d["src"] == "adsblol-trace":  # full-day traces: keep only points within 30 km of the box
                    p0 = d["ac"][0]
                    if not (b[0] - 0.3 <= p0["lat"] <= b[2] + 0.3 and b[1] - 0.45 <= p0["lon"] <= b[3] + 0.45):
                        continue
                f.write(line)
                n += 1
    case = {"case": a.clip_case, "audio": f"cases/{a.clip_case}/audio.flac", "sample_rate": SR,
            "utc_start": datetime.fromtimestamp(t0, timezone.utc).isoformat().replace("+00:00", "Z"),
            "duration_s": round(dur, 2), "gaps_s": [], "bbox": bbox,
            "bbox_note": "40x40 km search box; contains the microphone; its centre was offset at random",
            "adsb": f"cases/{a.clip_case}/adsb.jsonl", "adsb_polls": n,
            "clock": "utc_start is #EXT-X-PROGRAM-DATE-TIME of the first HLS segment; camera-to-ingest latency unknown",
            "note": "test clip: webcam audio with a synthetic voice talking over it"}
    json.dump(case, open(os.path.join(cdir, "case.json"), "w"), indent=1)
    sealed = dict(truth, case=a.clip_case, clip_of=a.case, clip_window=[a.t0, a.t1], voice=os.path.basename(a.voice),
                  voice_at_s=a.voice_at, box_seed=seed, box_offset_km=off,
                  written_utc=datetime.now(timezone.utc).isoformat())
    json.dump(sealed, open(os.path.join(ROOT, "data", "sealed", "truth", f"{a.clip_case}.json"), "w"), indent=1)
    print(out, json.dumps({k: case[k] for k in ("case", "utc_start", "duration_s", "adsb_polls")}))


if __name__ == "__main__":
    main()
