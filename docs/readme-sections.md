<!--
Four sections for README.md, in the order they should appear: "Try it on your own video", "Questions people asked",
"How to run each test", "Limits". Paste them in place of the current "Try it on your own video" and "Limits"; put the two
new ones between them. Real numbers only, each with its file. Written 2026-11-03 from results/case_*_evaluation.json,
results/case_A_ridge_v11/answer.json, results/case_A_safe/*, results/colsto_dopploc*/metrics.json and skills/flyover-check/TESTS.md.
Re-check the numbers if those files change. One result is still pending: the locator on the Miami safe copy
(results/case_A_safe/ridge_v11/ is empty). Add one line to "How do you break it?" and to "Limits" when it exists.
Also fix in the README itself: "the camera's location from the owner's own public page" is true for the Midway cases only (Miami,
Brighton, Southampton use the map position of the building the owner names); "1 of 6 cameras" is 1 of 6 recordings from 4 cameras;
"It sounds the same to a listener" is a goal, not a measurement.
-->

## Try it on your own video

```bash
npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check
```

You need `ffmpeg` and [`uv`](https://docs.astral.sh/uv/) on your PATH. Then, in Claude Code:

- "check my video `clip.mp4`": says in plain words whether plane tones, mains hum or file tags (GPS, creation time) could place the recording. A 10-minute file takes about 21 s. It says "this check found nothing", never "your video is safe". On the 66-minute Miami webcam audio it found 15 plane tones (7 confident, 8 low-confidence).
- "make a safe copy of `clip.mp4`": cuts the narrow plane tones and the mains hum, strips GPS and date tags, keeps the picture byte-for-byte, and checks the copy again. On the Miami audio it cut 13 of the 15 tones in three passes and the re-check still heard 2, so it said **NOT FULLY SAFE**; the speech band changed by 0.010 dB. On a synthetic plane the tone fell by 37.1 dB (jet fan tones: 56.1 dB), and the re-check was clean. Details: [`skills/flyover-check/TESTS.md`](skills/flyover-check/TESTS.md), [`results/case_A_safe/`](results/case_A_safe/).
- "find where `my_recording.mp4` was made": only for your own recordings, public challenges, or recordings whose owner agreed; it asks which one applies and refuses otherwise. It now drives doppler-locate's own pipeline (detect, fetch and ingest the adsb.lol day, search, score); so far only its dry run has been exercised. It needs the date and time and an area, and the solver is heavy (several GB of memory and many minutes).

What "clean" means: the check's detector hears nothing afterwards. It is not proof that nothing can be recovered, and nothing here shows yet that a safe copy keeps a locator from finding the place.

## Questions people asked

**How often does it work?** One blind recording in six. The six recordings come from four public webcams (three of them are the same Midway camera). Miami was located 749 m from the spot the owner names. The other five missed: Brighton 7.0 km (and 17.6 km on a second run), Southampton 20.6 km, Midway 21.7 to 27.6 km on all three recordings. Every run that gave a point, other than Miami's two, was 7 km or worse. It is a small sample and the solver changed along the way (see the table above).

**Was Miami luck?** Partly. The answer is the sum over 74 aircraft; the top three give 8.8 % of the score. The second-best peak, 2.9 km away, scored 0.6 % lower than the winner. An earlier version of the solver gave 1,872 m, and the first, detector-first solver found no flyover ([`results/case_A_evaluation.json`](results/case_A_evaluation.json), [`results/case_A_ridge_v11/answer.json`](results/case_A_ridge_v11/answer.json)).

**What counts as "blind"? What is the true position?** The solver got the audio, the UTC start time, a 40×40 km box that contains the camera but has a randomly offset centre, and ADS-B logged around the box centre. The true positions were sealed by SHA-256 before solving, each case's runs were committed before its truth was opened, and the hashes match. The true position is owner-published coordinates only for the Midway cases; for Miami, Brighton and Southampton it is the map position of the building the owner's page names, good to 15 to 40 m.

**What is the shortest recording that works?** Not measured: we have not trimmed a recording to find where it stops working. Miami used 66 minutes and 74 aircraft. In colsto's challenge the two flyovers used each show a sliding tone for about two minutes ([`results/colsto_dopploc/rank1.png`](results/colsto_dopploc/rank1.png)). On the Midway clips the clean tone near each jet pass is only 5 to 8 seconds long.

**How many planes do you need?** One plane leaves two places, one on each side of its path, mirror images of each other. More planes on different lines pick one. Planes on the same line (arrivals and departures on one runway) do not. Miami's answer sums 74 aircraft; colsto's challenge used two. Error against the number of planes is not measured.

**Must the recording time be known, or can it be searched?** It must be known, to within a minute or so: flight data records where planes were at each second. The solvers scan one clock offset (−10 to +60 s in ridge v1.1, −10 to +90 s in v1.3), which absorbs a stream delay or a small error in the start time; on Miami it picked 38.5 s. Searching over the time itself is not part of any solver here.

**How was the camera's clock lined up with the flight data?** Webcam streams: each HLS segment carries a UTC stamp (`#EXT-X-PROGRAM-DATE-TIME`); the first segment's stamp is the start time. Screen recordings: the file's own start time. In both cases the delay between the camera and that stamp is unknown, so the solver scans it and keeps one value for all planes in a recording.

**Sound is slow. How is that handled?** Sound travels about 343 m/s, so a plane 2 km away is heard about 6 s late, and in 6 s a plane at about 100 m/s has moved about 600 m. The model works out when the sound left the plane and where the plane was then, and compares that. The ground height comes from a terrain model.

**What if two planes overlap?** Each plane is a thin line that slides slowly, with copies at fixed ratios above it. Two planes at once show as two sets of lines. On a synthetic file with two overlapping planes the skill found both, flagged the overlap, and its safe copy took each tone down by 26.0 dB. The ridge solver scores every aircraft separately and sums them. We have no count of overlaps on real recordings.

**What did you do about gaps in the flight data?** Live adsb.lol polls around the search box lost about 45 % of their answers to HTTP 429 ("too many requests"). The gaps are filled with each plane's full-day trace from the adsb.lol map, fetched once per plane at one request a second, merged per plane and de-duplicated by time; a track is split after 90 s without a position. Which planes to fetch was decided by "was inside the box's poll circle", never by distance to the camera. For colsto's challenge, adsb.lol's archive of the day was used instead ([`docs/windows-notes.md`](docs/windows-notes.md)).

**How precise is it?** Miami: 749 m from the reference point (±40 m), as the better of two runs that gave a point. The other five recordings: 7.0 to 27.6 km. colsto's challenge: a town match (Oyster Bay hamlet, 925 m from its centre) when doppler-locate's author measured the flyovers, and a miss (Connecticut, 137 km; the right pair 5th of 517) when doppler-locate's detector measured them; both in a 300 km box chosen knowing the answer, and the camera's coordinates are unpublished, so neither is an error in metres. Timing is the main limit: a few seconds of error is hundreds of metres for a plane at 100 m/s.

**Why do fixed outdoor cameras work best?** The method assumes the microphone stands still while the plane passes, so a moving camera breaks it, and indoors or behind noise suppression the steady tone is the first thing lost. But fixed and outdoors is not enough: all our cameras are, and five of six recordings missed. From the files, not as a rule: Miami's strongest aircraft passed 2.1 and 3.2 km away with a steady tone, over an hour of busy traffic; Midway's jets pass 90 to 150 m up, so the tone is short and weak under broadband roar; Southampton had very little traffic; Brighton had light aircraft and helicopters.

**How do you break it?** With the safe copy (`flyover-check`). It finds each plane tone, cuts it with a notch a few Hz wide that follows the sliding pitch, cuts mains hum, strips GPS and date tags, and checks again. On the Miami audio it cut 13 of 15 tones and the re-check still hears 2, so it says NOT FULLY SAFE. It does not touch the picture, speech, signs or wide-band jet noise. Whether the locator still finds Miami on that copy is not known yet; a run is in progress.

## How to run each test

Set up once, following [`docs/windows-notes.md`](docs/windows-notes.md):

```bash
git clone --depth 1 https://github.com/digitalis-p/doppler-locate data/vendor/doppler-locate
cd data/vendor/doppler-locate
python -m uv venv --python 3.12 .venv
python -m uv pip install --system-certs --python .venv/Scripts/python.exe -e ".[test]" truststore psutil
git apply ../../../solver/windows_patches.diff
```

(`data/` is git-ignored. The patches make doppler-locate run on Windows without a GPU.)

**1. The skill, on synthetic recordings with known contents (56 checks, no network).**

```bash
uv run skills/flyover-check/scripts/selftest.py
```

Add `--quick` for a short run. It writes `data/skilltest/results.json`; compare with [`skills/flyover-check/TESTS.md`](skills/flyover-check/TESTS.md).

**2. The skill, on a file you have.**

```bash
uv run skills/flyover-check/scripts/check.py INPUT
```

```bash
uv run skills/flyover-check/scripts/safe_copy.py INPUT
```

The Miami safe copy came from `uv run skills/flyover-check/scripts/safe_copy.py cases/case_A/audio.flac --out cases/case_A_safe/audio.flac`; its report is in [`results/case_A_safe/`](results/case_A_safe/).

**3. The blind runs (maintainers: the case folders hold audio and ADS-B that are not in git).**

Check the seal first. Only the files present are checked:

```bash
cd truth && sha256sum --ignore-missing -c SEALED.sha256
```

Check the order for a case (here A): seal, then answers, then reveal.

```bash
git log --format='%h %ad %s' --date=iso -- truth/SEALED.sha256 results/case_A_ridge results/case_A_ridge_v11 truth/case_A.json
```

Run a case (here A), once per solver. The detector-first solver:

```bash
data/vendor/doppler-locate/.venv/Scripts/python.exe solver/solve.py cases/case_A --out results/case_A
```

The ridge solver (current code is v1.3; Miami's 749 m is v1.1, commit `6891c89`, kept in a git-ignored worktree):

```bash
git worktree add solver_ridge_v11_ref 6891c89
```

```bash
python solver_ridge_v11_ref/solver_ridge/ridge.py cases/case_A --out results/case_A_ridge_v11
```

`ridge.py` imports numba, numpy, scipy and soundfile; use the doppler-locate venv's python if they are missing. The error is the great-circle distance between the answer and `truth/case_*.json`; the results for every case are in `results/case_*_evaluation.json`. A case folder is made with `uv run recorder/make_case.py case_B` (recorded webcam) or `uv run recorder/make_case_file.py case_E --file data/clip/case_E_test.mp4 --utc-start 2026-11-03T14:25:17Z` (finished video file).

The known-camera check on Midway (not blind, hand-picked aircraft):

```bash
python solver_paras/paras.py cases/case_E --out results/paras_setup_E
```

**4. colsto's challenge with doppler-locate's own pipeline.** Needs about 5 GB of memory. Run 1 (flyover events as measured by doppler-locate's author) took 23 minutes, run 2 (events from `dopploc detect`) 44 minutes, on a Ryzen 7 5700X.

```bash
yt-dlp --compat-options no-certifi --extractor-args "youtube:player_client=web_embedded" -f bestaudio -o data/colsto/recording_4CxVxuMVkMc.webm https://youtu.be/4CxVxuMVkMc
```

From `data/vendor/doppler-locate`, with its venv (`.venv/Scripts/dopploc.exe`):

```bash
dopploc fetch-adsb 2026-09-08
```

```bash
dopploc ingest 2026-09-08
```

Run 1: copy [`results/colsto_dopploc/case.toml`](results/colsto_dopploc/case.toml) to `out/colsto_dopploc/case.toml` there (fix its `audio =` path), then:

```bash
dopploc search out\colsto_dopploc\case.toml
```

```bash
dopploc score out\colsto_dopploc\case.toml --top 5
```

Run 2: detect the events yourself, put them in [`results/colsto_dopploc_e2e/case.toml`](results/colsto_dopploc_e2e/case.toml) (copied to `out/colsto_dopploc_e2e/case.toml`), then search and score as above:

```bash
dopploc detect ..\..\colsto\recording_4CxVxuMVkMc.webm --out out\detect\colsto_e2e
```

The logs, rankings and metrics are in [`results/colsto_dopploc/`](results/colsto_dopploc/) and [`results/colsto_dopploc_e2e/`](results/colsto_dopploc_e2e/) (`search.log`, `score.log`, `detect.log`, `summary.md`, `metrics.json`).

**5. The solver against a known answer (synthetic).** No result is published for this yet.

```bash
dopploc synth cases/synth_base.toml --out out/synth --seed 3
```

```bash
python solver/make_synth_case.py VENDOR_SYNTH_DIR cases/synth_seed3 --latency 14
```

```bash
data/vendor/doppler-locate/.venv/Scripts/python.exe solver/solve.py cases/synth_seed3 --out results/synth_seed3
```

**6. Time and memory of any run.**

```bash
python solver/runmeter.py OUT.json -- command arg ...
```

**7. The install line itself.**

```bash
npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check
```

## Limits

- It needs an audible plane, the date and time of the recording, and public flight data for that moment. adsb.lol history starts in 2023; a day's archive is published the morning after (about 03:30 UTC).
- It does not work on silent or heavily noise-suppressed audio, on planes missing from ADS-B, or in places with no traffic overhead.
- **One blind recording in six was located close.** Miami: 749 m (±40 m reference), with a runner-up peak 2.9 km away that scored within 0.6 %, and an answer that sums 74 aircraft rather than resting on one. The other five missed by 7.0 to 27.6 km. The six recordings are from four cameras, and the solver changed between runs.
- **Light traffic and jets right overhead beat it.** Southampton had very little traffic; Brighton had light aircraft and helicopters; at Midway the jets pass 90 to 150 m up and the sound is mostly broadband roar. What made Miami work is not established: jets a few kilometres away over an hour of busy traffic is the only pattern the files show.
- **colsto's challenge is a town match only when the flyovers are measured by doppler-locate's author.** With doppler-locate's detector on the raw audio, the right pair came 5th of 517 and the top pair was in Connecticut, 137 km away. Both runs use a 300 km box chosen knowing the answer, and the camera's coordinates are unpublished.
- One plane leaves two mirror places; planes on one line (arrivals and departures on one runway) leave the ambiguity in place.
- **The safe copy is not complete on Miami.** It cut 13 of 15 tones and its own re-check hears 2. It covers sound and file tags only; the picture, signs, speech and wide-band jet noise are untouched. "Clean" means the check's detector hears nothing. No run yet shows the safe copy keeping a locator from finding the place; the Miami run is in progress.
- Not covered by any run yet: real planes with known ground truth in the skill's own checks, helicopters, phone noise suppression, a real `yt-dlp` download and live capture, `--fetch-adsb-now`, and a full (not dry) run of `locate.py`.
- The solver is heavy: on colsto's challenge it used about 5 GB of memory and 23 to 44 minutes, and Miami took 44 minutes, on a Ryzen 7 5700X with 16 GB, CPU only.
- The skill's "find" mode refuses recordings that are not yours, a named public challenge, or the owner's.
