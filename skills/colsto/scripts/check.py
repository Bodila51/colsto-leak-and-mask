# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "soundfile", "matplotlib"]
# ///
"""flyover-check, mode 1: check my video.

Finds plane flyovers (tonal planes whose pitch slides as they pass), mains hum, possible
sirens and GPS/time tags in the file, writes a spectrogram picture with each flyover marked,
and says in plain words what someone could learn from it.

  uv run check.py VIDEO_OR_AUDIO_OR_LINK [--out DIR] [--json] [--live-seconds 300]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fo_media  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="local file, YouTube (or other yt-dlp) link, or live webcam link")
    ap.add_argument("--out", default="flyover-check-out", help="folder for the picture and JSON (default %(default)s)")
    ap.add_argument("--json", action="store_true", help="also print the full result as JSON")
    ap.add_argument("--min-duration", type=float, default=5.0, help="shortest flyover to report, seconds")
    ap.add_argument("--live-seconds", type=int, default=300, help="how long to record a live link")
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)

    fo_media.need_ffmpeg()
    import fo_report  # imports numpy/scipy; after the ffmpeg check so the message comes first
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    path, extra = fo_media.resolve_input(a.input, os.path.join(a.out, "download"),
                                         want_video=False, live_seconds=a.live_seconds)
    if extra.get("is_live"):
        print(f"Recorded {a.live_seconds} s of the live stream. {extra.get('utc_note', '')}")
    r = fo_report.check_path(path, a.min_duration)
    s = fo_report.summarize(r)
    if extra.get("source_note"):
        s["notes"].append(extra["source_note"])
    stem = os.path.splitext(os.path.basename(path))[0]
    png = None if a.no_png else os.path.join(a.out, f"{stem}.flyovers.png")
    if png:
        fo_report.render_png(r, png, title=os.path.basename(path))
    jp = os.path.join(a.out, f"{stem}.flyover-check.json")
    out = fo_report.to_json(r, s)
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=float)
    fo_report.print_summary(r, s, png, jp)
    print(f"\n(checked in {time.time() - t0:.1f} s)")
    if a.json:
        print(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
