# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "soundfile", "matplotlib"]
# ///
"""flyover-check, mode 2: make a safe copy.

The copy sounds the same to a listener, but:
  * each plane tone (and its harmonics) is removed with a few-Hz-wide notch that follows its
    sliding pitch, so voices, which move fast, stay intact
  * mains hum is removed with narrow notches
  * location and creation-time tags (and GPS/telemetry tracks) are stripped; the video stream is
    copied untouched (-c:v copy)
Then the check is run again on the new file. It says SAFE only if no plane tone is detected any more.

  uv run safe_copy.py VIDEO_OR_AUDIO_OR_LINK [--out FILE] [--decoy] [--keep-hum]

--decoy replaces each plane tone with a made-up one that follows a different, fake Doppler curve,
instead of just removing it. A plane is still audible then; the slide just does not match the real flight.
"""
import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fo_media  # noqa: E402


def _short(s):
    return s if len(s) < 160 else s[:157] + "..."


def what_found(r):
    res, meta = r["analysis"], r["meta"]
    from fo_detect import mmss
    bits = []
    fl = res.get("flyovers", [])
    if fl:
        bits.append(f"{len(fl)} plane tone(s) at " + ", ".join(mmss(g["start_s"]) for g in fl))
    if res.get("hum"):
        bits.append(f"{res['hum']['grid_hz']:.0f} Hz mains hum")
    if meta["location"] or meta["data_streams"]:
        bits.append("location tags/tracks")
    if meta["times"]:
        bits.append("creation-time tags")
    return bits or ["nothing"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", help="output file (default: <input>.safe.<ext> next to the input)")
    ap.add_argument("--report-dir", default="flyover-check-out", help="folder for pictures and JSON")
    ap.add_argument("--decoy", action="store_true", help="swap plane tones for fake-Doppler decoys instead of just removing them")
    ap.add_argument("--keep-hum", action="store_true", help="do not remove mains hum")
    ap.add_argument("--passes", type=int, default=2, help="extra clean-up passes if tones remain (default 2)")
    ap.add_argument("--min-duration", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=None, help="random seed for decoys")
    ap.add_argument("--live-seconds", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    fo_media.need_ffmpeg()
    import numpy as np
    import soundfile as sf
    import fo_detect
    import fo_remove
    import fo_report
    from fo_detect import mmss

    t_start = time.time()
    os.makedirs(a.report_dir, exist_ok=True)
    src, extra = fo_media.resolve_input(a.input, os.path.join(a.report_dir, "download"),
                                        want_video=True, live_seconds=a.live_seconds)
    stem, ext = os.path.splitext(os.path.basename(src))
    if a.out:
        dst = a.out
    elif fo_media.is_url(a.input):
        dst = os.path.join(a.report_dir, f"{stem}.safe{ext}")
    else:
        dst = os.path.join(os.path.dirname(os.path.abspath(src)), f"{stem}.safe{ext}")
    if os.path.abspath(dst) == os.path.abspath(src):
        print("Refusing to overwrite the input file.", file=sys.stderr)
        return 2

    print(f"Checking {os.path.basename(src)} ...")
    before = fo_report.check_path(src, a.min_duration)
    res = before["analysis"]
    info = fo_media.probe(src)
    media = before["media"]
    flyovers = res["flyovers"]
    hum = None if a.keep_hum else res.get("hum")
    dt = res["_spec"]["dt"] if "_spec" in res else fo_detect.HOP / fo_detect.SR
    print("  found: " + "; ".join(what_found(before)))

    tmp = fo_media.tempdir()
    try:
        wav_in = os.path.join(tmp, "in.wav")
        fo_media.decode_full_to_wav(src, wav_in)
        x, sr = sf.read(wav_in, dtype="float32", always_2d=True)
        y = x.astype(np.float64)
        rng = np.random.default_rng(a.seed)
        kept, decoys, tone_info = {}, {}, []
        passes_used = 0
        if flyovers:
            print(f"Removing {len(flyovers)} plane tone(s) with notches that follow the pitch ...")
            y, kept, tone_info = fo_remove.remove_flyovers(y, sr, flyovers, dt, keep_tones=a.decoy)
            passes_used = 1
        # more passes if the cleaned sound still shows a plane (this check is on the audio before encoding)
        for p in range(max(a.passes, 0)):
            if not flyovers:
                break
            mono = y.mean(axis=1)
            chk = fo_detect.analyze(fo_remove.to_int(mono, sr, 8000).astype(np.float32), fo_detect.SR, a.min_duration,
                                    x_hi=fo_remove.to_int(mono, sr, 16000).astype(np.float32) if sr >= 16000 else None)
            left = chk["flyovers"]
            if not left:
                break
            print(f"  {len(left)} tone(s) still audible after pass {p + 1}; cleaning again ...")
            y, _, more = fo_remove.remove_flyovers(y, sr, left, chk["_spec"]["dt"], fc_base=2.0)
            tone_info += more
            passes_used += 1
        hum_used = []
        if hum:
            print("Removing mains hum with narrow notches ...")
            y, hum_used = fo_remove.mains_notch(y, sr, hum)
        if a.decoy and flyovers:
            print("Adding decoy tones that follow a fake Doppler curve ...")
            y, decoys = fo_remove.add_decoys(y, sr, kept, flyovers, dt, rng)
        # never make the copy louder than the original was; if the source already peaks above full scale
        # (AAC overshoot), the copy keeps exactly that level
        peak = float(np.abs(y).max())
        limit = max(0.999, float(np.abs(x).max()))
        if peak > limit * 1.0001:
            y = y * (limit / peak)
            print(f"  (peak {peak:.3f} is above the original's {limit:.3f}; scaled by {limit / peak:.4f})")
        wav_out = os.path.join(tmp, "clean.wav")
        sf.write(wav_out, y.astype(np.float32), sr, subtype="FLOAT")
        print("Measuring how much the sound changed ...")
        change = fo_remove.change_report(x, y.astype(np.float32), sr, flyovers, dt, hum)
        print("Writing the safe copy (video stream copied, metadata stripped) ...")
        fo_media.write_safe_container(src, wav_out, dst, media["has_video"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("Re-running the check on the new file ...")
    after = fo_report.check_path(dst, a.min_duration)
    ares = after["analysis"]
    left_fl = ares["flyovers"]
    left_hum = ares.get("hum") if not a.keep_hum else None
    ameta = after["meta"]
    left_meta = bool(ameta["location"] or ameta["data_streams"] or ameta["times"])
    problems = []
    if left_fl and not a.decoy:
        problems.append("plane tone(s) still detected at "
                        + ", ".join(f"{mmss(g['start_s'])}-{mmss(g['end_s'])} ({g['label']})" for g in left_fl))
    if left_hum:
        problems.append(f"{left_hum['grid_hz']:.0f} Hz hum still detected")
    if left_meta:
        problems.append("metadata still has location/time tags: "
                        + "; ".join(f"{t['key']}={_short(t['value'])}" for t in (ameta["location"] + ameta["times"])[:4]))
    if a.decoy:
        verdict = "DECOY COPY" if not problems else "DECOY COPY, WITH LEFTOVERS"
    else:
        verdict = "SAFE" if not problems else "NOT FULLY SAFE"

    pre_png = os.path.join(a.report_dir, f"{stem}.before.png")
    post_png = os.path.join(a.report_dir, f"{stem}.after.png")
    fo_report.render_png(before, pre_png, title="before: " + os.path.basename(src))
    fo_report.render_png(after, post_png, title="after: " + os.path.basename(dst))

    print(f"\nSAFE COPY: {dst}")
    print(f"RESULT: {verdict}")
    print("  before: " + "; ".join(what_found(before)))
    print("  after : " + "; ".join(what_found(after)))
    if verdict == "SAFE":
        print("  The new file has no plane tone this check can hear, no mains hum, and no location or "
              "time tags.")
    elif a.decoy:
        print("  Decoy mode: the plane tones are fake now, so this check still hears planes. That is expected. "
              "Anyone matching the pitch slide to flight data would get a wrong answer, but a plane is still "
              "audible. Decoys: " + "; ".join(
                  f"plane {gi + 1}: fake speed {d['speed_ms']:.0f} m/s, fake closest distance "
                  f"{d['closest_m']:.0f} m, closest-approach moved {d['closest_time_shift_s']:+.1f} s"
                  for gi, d in decoys.items()))
    for p in problems:
        print("  what remains: " + p)
    if problems and not a.decoy:
        print("  Try again with --passes 4, or look at the spectrogram: " + post_png)
    print("\nHow much the audio changed (measured before the final encode)")
    print(f"  - Removed energy: {change['removed_energy_percent']:.3f}% of the total ({change['removed_energy_db']:.1f} dB re input); "
          f"overall level change {change['level_change_db']:+.3f} dB.")
    if "outside_mean_abs_db" in change:
        print(f"  - The notches cover only {change['notched_share_of_spectrum_percent']:.2f}% of the spectrogram, so most of "
              "the removed energy is the plane tones and hum themselves.")
        print(f"  - Outside the notches: mean absolute change {change['outside_mean_abs_db']:.3f} dB per spectrogram cell, "
              f"energy change {change['outside_energy_change_db']:+.4f} dB; speech band 300-3400 Hz: "
              f"mean change {change['speech_band_mean_abs_db']:.3f} dB, energy {change['speech_band_energy_change_db']:+.4f} dB.")
    codec = fo_media._AUDIO_BY_EXT.get(os.path.splitext(dst)[1].lower(), ("aac",))[0]
    print(f"  - The final encode ({codec}) adds its own small, normal loss on top.")
    if media["has_video"]:
        print("  - The video stream was copied bit for bit (-c:v copy). The picture, its timing and any "
              "visible landmarks are unchanged: this tool does not look at them.")
    if len(info.get("streams", [])) > 2:
        print("  - Only the first audio track and the first video track were kept; other tracks, chapters and "
              "data tracks were dropped.")
    print("\nWhat 'safe' means here")
    print("  - This check no longer hears a plane tone, hum or location/time tags. It does not prove that a "
          "better method could not find faint leftovers, and it does not cover the picture, speech, signs or "
          "wide-band jet noise.")
    print(f"\nPictures: {pre_png} (before), {post_png} (after)")
    print(f"(done in {time.time() - t_start:.1f} s; passes used: {passes_used})")

    report = {
        "input": src, "output": dst, "verdict": verdict, "decoy": a.decoy,
        "before": fo_report.to_json(before, fo_report.summarize(before)),
        "after": fo_report.to_json(after, fo_report.summarize(after)),
        "removed": {"harmonics_notched": tone_info, "hum_notches": hum_used},
        "audio_change": change, "decoys": decoys, "problems": problems,
    }
    jp = os.path.join(a.report_dir, f"{stem}.safe-copy.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=float)
    print(f"Machine-readable result: {jp}")
    if a.json:
        print(json.dumps(report, indent=1, default=float))
    return 0 if verdict in ("SAFE", "DECOY COPY") else 1


if __name__ == "__main__":
    sys.exit(main())
