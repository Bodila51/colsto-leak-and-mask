---
name: flyover-check
description: Check a video, audio file or link (YouTube, live webcam) for what its sound and file tags give away about where and when it was recorded - plane flyover tones that Doppler-slide as a plane passes, mains hum, sirens, GPS and creation-time metadata. Makes a "safe copy" that sounds the same but no longer leaks those things, and re-checks it. A third mode locates a recording from plane Doppler slides plus public ADS-B flight data, only for the user's own recordings, public challenges or recordings whose owner agreed. Use when someone wants to check a video before posting, remove plane sounds or location tags, or asks where a recording was made.
---

# flyover-check

A plane passing a microphone makes a steady tone (about 70-300 Hz, plus harmonics) whose pitch slides down as it passes. Matched with public flight tracks (ADS-B) and the date, that slide can show where a recording was made. Mains hum and file tags can leak too. This skill checks for them, removes them, and says plainly what it found.

`SKILL_DIR` below means the folder that holds this file. All scripts are in `SKILL_DIR/scripts/`, are plain Python with their dependencies declared at the top (numpy, scipy, soundfile, matplotlib), and run with `uv run` with no setup:

```
uv run SKILL_DIR/scripts/check.py     INPUT
uv run SKILL_DIR/scripts/safe_copy.py INPUT
uv run SKILL_DIR/scripts/locate.py    INPUT --consent ...
```

`INPUT` is a local file, a YouTube (or other yt-dlp) link, or a live webcam link.

## Before the first run

- ffmpeg and ffprobe must be on PATH. The scripts check and print install steps if they are missing (`winget install Gyan.FFmpeg`, `brew install ffmpeg`, `sudo apt install ffmpeg`). Do not continue until they work.
- uv must be installed (`winget install astral-sh.uv`, `brew install uv`, or `pip install uv`). If uv fails with `invalid peer certificate: UnknownIssuer` (a network that inspects TLS), add `--system-certs`: `uv run --system-certs ...`.
- Links need yt-dlp. If yt-dlp fails with `CERTIFICATE_VERIFY_FAILED`, the scripts already retry once with `--compat-options no-certifi`. Never use `--no-check-certificates`.
- A video that was downloaded from YouTube has had its GPS and creation-time tags removed by the site. For a metadata check, use the user's original file, and say so.

## Mode 1 - Check my video

```
uv run SKILL_DIR/scripts/check.py INPUT [--out DIR] [--min-duration 5]
```

It finds plane flyovers in two search bands: propeller and piston tones (a base tone of 45-420 Hz with harmonics up to 3 kHz) and jet fan-tone stacks (a base tone of about 0.45-3 kHz with harmonics up to 7.5 kHz, e.g. 1.4 / 2.8 / 4.2 / 5.6 kHz), each a thin line that slides down slowly with harmonics at fixed ratios. It ignores wide-band roar (wind, traffic), voices (pitch moves too fast), and steady tones that do not slide. It also reports GPS and creation time in the file tags, mains hum at 50/60 Hz and its multiples, and possible sirens. It writes a spectrogram PNG with each flyover marked and a JSON file, and prints a plain-words result. A 10-minute file takes well under a minute.

Report it to the user in plain words, a few lines, for example:

> 2 planes audible at 0:12 and 0:41. Someone with flight data and the date could use them to find where this was filmed. Your file also contains GPS coordinates (42.6977, 23.3219).

Rules for the answer:

- Give each flyover's time range and confidence (high, medium, low) exactly as printed. Say "could", not "will": a plane can only be matched with flight data, the right time and a close enough pass.
- Say "one plane alone leaves two possible places, one on each side of its path" when it is one plane.
- Siren and hum findings give at most a rough region (hum: 50 Hz vs 60 Hz grid; siren style: a country-level hint, often wrong). Say so; never present them as a place.
- Show the PNG path. Offer the safe copy if anything was found.
- If nothing was found, say "this check found nothing", not "your video is safe". Always include the limits line: it does not hear jets that are only a wide-band roar, planes hidden under wind or traffic, and it does not look at the picture, speech or signs.
- Overlapping planes, turning planes ("irregular glide") and low confidence are less certain. Say that.

## Mode 2 - Safe copy

```
uv run SKILL_DIR/scripts/safe_copy.py INPUT [--out FILE] [--decoy] [--keep-hum] [--passes 2]
```

What it does:

1. Runs the check.
2. Removes each plane tone and its harmonics with a narrow notch that follows the traced pitch through time, extended along a fitted Doppler curve beyond the clearly audible part. For propeller tones the notch is a few Hz wide in total, so voices, which move fast, pass through. Jet fan tones sit at 1-7 kHz and wander by about 1 % of their pitch, so their notch is 1.2 % of the harmonic's frequency each side (4-40 Hz). A jet's wide-band roar is not touched: only its thin sliding lines are.
3. Removes mains hum with narrow notches. (`--keep-hum` skips this.)
4. Strips location and creation-time tags, GPS/telemetry tracks, chapters and other tracks. The video stream is copied untouched (`-c:v copy`); the audio is re-encoded (AAC 256k in mp4/mov/mkv, Opus in webm).
5. Runs the check again on the new file.

Read the result:

- `RESULT: SAFE` only if the second check hears no plane tone, no hum and finds no location or time tags. Otherwise it prints `NOT FULLY SAFE` and what remains: report exactly that, and try `--passes 4`.
- Always give the audio-change numbers it prints: energy removed (percent and dB), overall level change, the typical drop inside the notched bands, and the change outside them (including the 300-3400 Hz speech band).
- `--decoy` replaces each plane tone with a made-up one that follows a different, fake Doppler curve (other speed, distance and closest-approach time), keeping the loudness. A plane is still audible, so the check still hears one; the result is called `DECOY COPY`, never "safe". Matching it to flight data would give a wrong answer.
- Say what "safe" does not cover: the picture (its timing and landmarks are unchanged), speech, signs, wide-band jet noise, and anything a stronger method than this one could still pull out of faint leftovers.

## Mode 3 - Find where it was recorded

**Say this first, in these words:** this mode is only for the user's own recordings, public challenges such as colsto's, or recordings whose owner has agreed. It is not for finding where someone else is.

Ask the user which of the three applies (`own`, `public-challenge` with the challenge's name, `owner-agreed`). If none applies, or they will not say, stop and refuse. Do not run `locate.py` without a clear answer. Do not look for ways around it. If the recording looks like a private person's content that the user does not own and has no agreement for, refuse.

```
uv run SKILL_DIR/scripts/locate.py INPUT --consent own|public-challenge|owner-agreed [--challenge-name NAME] \
    (--bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX | --center LAT,LON [--radius-km 25] | --country BG) \
    [--utc-start 2026-11-02T14:03:10Z] [--adsb FILE | --fetch-adsb-now] [--repo PATH]
```

It needs four things; ask for what is missing:

1. **Date and UTC start time** of the first sample, accurate to a few seconds. It reads the file's `creation_time` if present and says that tag is when the file was written (start or end of the recording, sometimes local time stored as UTC). Confirm it with the user.
2. **A search area**: a box of about 40 x 40 km or more around a guess, or a country code (a whole country is slow and coarse).
3. **ADS-B data** for that time: `--adsb FILE` in the solver's `adsb.jsonl` format, or `--fetch-adsb-now` (public adsb.lol, only for recordings made today or in the last hours, because planes that already landed are not listed). For older dates use the adsb.lol daily history as described in the repo README.
4. **The solver**: `solver/solve.py` in the repo `https://github.com/Bodila51/colsto-leak-and-mask`, running in the doppler-locate virtual environment. It is not part of the skill folder. If it is missing, `locate.py` prints the install steps (clone the repo, clone `digitalis-p/doppler-locate` into `data/vendor/doppler-locate`, create the venv; see `docs/windows-notes.md` there) and keeps the case files it wrote. Pass `--repo PATH` if the repo is somewhere else. The solver is heavy: several GB of memory and many minutes.

It writes `cases/<name>/{audio.flac, case.json, adsb.jsonl}`, runs the solver, and prints the best point, the planes used and their closest distance and time.

Be honest about the result:

- **One plane leaves two possible places**, one on each side of its path, mirror images of each other. If the solver says `two_band`, give both points and the distance between them, and say nothing in the sound tells them apart. A second plane on a different line, or any other fact about the scene, can.
- The point is the computed best fit, not a certainty. It is wrong if the start time is off by more than a few seconds, a plane is missing from public data, or the tone was misread. A miss is a result.
- Never present a place as found if the solver returned nothing or flagged ambiguity.

## Files

- `scripts/check.py`, `safe_copy.py`, `locate.py` - the three modes.
- `scripts/fo_media.py` (ffmpeg, metadata, yt-dlp), `fo_detect.py` (flyover, hum, siren detection), `fo_remove.py` (notches, decoys, change measurements), `fo_report.py` (words, PNG, JSON) - shared code.
- `scripts/make_test_audio.py`, `scripts/selftest.py` - synthetic recordings with known contents and the test that checks all of the above (`uv run scripts/selftest.py`). Results are in `TESTS.md`.
