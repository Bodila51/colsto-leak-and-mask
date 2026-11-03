# flyover-check: tests

Everything except the two real-recording sections was produced by `uv run scripts/selftest.py` on 2026-11-03, on a Windows 11 PC
(Python 3.12, ffmpeg 8.1, uv 0.13). The recordings are **synthetic**, built by `scripts/make_test_audio.py` with a fixed
seed, so every part of them is known. The test files go in `data/skilltest/`, which is git-ignored. Result file: `data/skilltest/results.json`.

**56 of 56 checks pass.** The two real recordings that were tried (the last two sections) have no list of true flyover
times, so they show behaviour, not accuracy.

## What the synthetic recordings contain

| Part | How it is made |
|---|---|
| Plane | A tone with 8 harmonics whose pitch follows a true Doppler curve (rest pitch 140 Hz, 75 m/s, closest approach 300 m at 45 s, so it slides 179 to 115 Hz); loudness falls with distance (1/r). Peak level about -38 dBFS for the base tone. |
| Speech-like sound | Voiced syllables (glottal pulse train through vowel formants) whose pitch glides up to about +-30 % inside each syllable, plus hissy consonants; 4-30 s and 50-82 s, about -26 dBFS. It deliberately crosses the plane's harmonics. |
| Mains hum | 50 Hz plus 5 harmonics, with a slow wander of +-0.04 Hz like a real grid. |
| Background | Pink noise (street-like), -42 dBFS; optional wind-like roar, sirens. |
| File | 48 kHz stereo AAC in mp4 with a video track, GPS (`+42.6977+023.3219`), QuickTime location key and creation time. |

Other files: no plane (speech and hum only), wind roar, two overlapping planes, a turning plane (its pitch also wobbles up and
down), sirens, and a 10-minute file (the main file repeated 7 times).

## 1. check finds the tone

| Number | Value |
|---|---|
| Planes found in the main file | 1 (true: 1). Voices and hum are not called planes. |
| Detected range | 0:19-1:02, closest approach (0:45) inside it |
| Confidence | high (0.99), 8 harmonics found |
| Mean error of the traced pitch against the true pitch | 0.43 Hz |
| Mains hum | 50 Hz grid found, 34 dB suppressed later by the notches |
| Two overlapping planes | 2 found, trace errors 0.46, 0.14 Hz, overlap flagged: True |
| Turning plane | found as `irregular glide`: [['irregular glide', 0.84]] |
| Wind-like roar | not called a plane; the loud stretch is flagged as masking noise: [[10.24, 40.704]] |
| No plane (speech + hum) | 0 planes |
| Sirens (hi-lo at 8-18 s, wail at 24-36 s) | not called planes; both noticed as sirens (low-confidence hint): ['two-tone', 'sweep'] |

## 2. safe copy removes it, and the second check finds nothing

| Number | Value |
|---|---|
| Plane tone alone, notched along the traces found in the real mix | **37.1 dB** quieter (the plane is 5.1 % of the main file's energy together with hum) |
| Two overlapping planes | 26.0 dB |
| Turning plane (partial: its faint tails cannot be extrapolated) | 13.6 dB; safe copy verdict SAFE (its own second check found nothing) |
| Mains hum | 34.1 dB |
| Second check on the safe copy of the main file | `SAFE`: 0 planes, no hum, no location or time tags |
| Video stream | unchanged: `ffmpeg -c copy -f md5` of the video packets is identical before and after |
| Audio length | unchanged |

## 3. the speech is kept

| Number | Value |
|---|---|
| Energy the notches take from sound that has no plane in it, 300-3400 Hz | -13.2 dB, that is 4.8 % of the energy of speech plus noise in that band (voiced harmonics that pass through a notch for a moment) |
| Same, all frequencies | -11.9 dB (6.5 %) |
| Overall loudness change of the main file (plane and hum removed) | -0.82 dB |
| Spectrogram difference outside the notched bands, per cell, 300-3400 Hz | 0.046 dB mean absolute change |
| Same, all bands | 0.010 dB; energy outside the notches changes by -0.0077 dB |
| The notches cover | 2.31 % of the spectrogram |
| With no plane in the file, only the hum notch acts: speech-band energy change | < 0.001 dB |

The numbers in `safe_copy.py`'s own output come from the same function (`change_report`). They are measured before the final AAC encode.

## 3b. jet fan-tone stacks (high search band)

A synthetic jet: a stack of 4 fan tones (1.3 / 2.6 / 3.9 / 5.2 kHz at rest, sliding to 1.6 / 3.3 / 4.9 / 6.5 kHz on approach and down to 1.08 kHz and its harmonics
after passing), 75 m/s, closest approach 220 m, with the same speech, hum and noise. It is found in the high band
(`high`, high, harmonics [1, 2, 3, 4], pitch 1633 to 1080 Hz, traced pitch error 0.7 Hz).
The fan tones are 56.1 dB quieter after the notches (they are 1.2 % of each harmonic's frequency wide each side);
the notches take -19.0 dB (1.3 %) of the energy of plane-free sound in 300-3400 Hz. Safe copy verdict: SAFE.

## 4. decoy mode

The plane tone is replaced by a made-up tone that keeps its loudness but follows a different Doppler curve. The check still hears a
plane, as it should. The fake pitch differs from the real one by 17.1 Hz on average over the overlap;
the median pitch is -13 % of the real one; the steepest slide goes from 30.0 Hz/s (real) to 10.1 Hz/s (fake).
Fake parameters used: {"0": {"speed_ms": 73.7038667429799, "closest_m": 327.60787994707476, "closest_time_shift_s": -9.112414618531817}}. Overall level change: -0.37 dB.

## 5. speed

A 10-minute mp4 (AAC, 48 kHz stereo, the 90 s main file repeated 7 times, so 7 planes): `check.py` including decoding, analysis, the spectrogram PNG and the JSON took
**21.4 s** (analysis alone 26.3 s). The safe copy of the 90 s main file took 25 s
(it re-decodes, notches, encodes, and re-checks). Timings were taken while other programs were using the same PC.

## 6. other tested behaviour

- Links: when yt-dlp prints `CERTIFICATE_VERIFY_FAILED` the call is retried once with `--compat-options no-certifi`; `--no-check-certificates` is never used (tested with a stand-in yt-dlp).
- locate: refuses to run without a consent word and asks; a public challenge must be named; stops when no plane is audible; says what ADS-B data is needed; writes `case.json` with `audio`, `utc_start` (from the file's `creation_time`, with a warning) and `bbox`; prints the install steps when the solver is missing; with a stand-in solver that returns a one-plane result it says there are two possible places and gives the mirror point and the distance between them.
- Mono 44.1 kHz audio-only (`.m4a`) input goes through `safe_copy.py` and the check on the output finds nothing.

## All checks

| Result | Test | Detail |
|---|---|---|
| pass | main: exactly one plane found (voices and hum not called planes) | found 1 |
| pass | main: closest approach (t=45 s) lies inside the detected range | 19.1-61.8 s |
| pass | main: traced pitch within 2 Hz of the true pitch on average | mean error 0.43 Hz |
| pass | main: confidence is high | high (0.99) |
| pass | main: at least 6 harmonics found | 8 |
| pass | main: 50 Hz mains hum found | grid 50.0, mains 49.996 Hz |
| pass | main: GPS and creation time tags reported | 3 location tags, 2 time tags |
| pass | main: GPS parsed to the right coordinates |  |
| pass | main: plane tone suppressed by at least 30 dB | 37.1 dB |
| pass | main: hum suppressed by at least 25 dB | 34.1 dB |
| pass | main: notches take less than 10 % of the energy of plane-free sound in the 300-3400 Hz band | -13.2 dB (4.8 %) |
| pass | main: overall loudness change under 1 dB | -0.82 dB (plane + hum are 5.1 % of the energy) |
| pass | main: speech band spectrogram changes < 0.1 dB outside the notches | 0.046 dB |
| pass | main: safe copy says SAFE | SAFE  |
| pass | main: second check finds no plane | 0 left |
| pass | main: second check finds no hum |  |
| pass | main: no location/time tags left |  |
| pass | main: video stream unchanged (packet checksum equal) |  |
| pass | main: audio length unchanged |  |
| pass | decoy: the check still hears a plane (expected) | 1 |
| pass | decoy: fake pitch curve differs from the real one by > 4 Hz on average | 17.1 Hz |
| pass | decoy: overall pitch stays in the same range (median within 20 %) | ratio 0.870 |
| pass | no_plane: no plane reported | 0 |
| pass | no_plane: with nothing to remove, only the hum notch acts (speech band energy change < 0.01 dB) | -0.0013 dB |
| pass | roar: broadband roar is not called a plane | 0 |
| pass | roar: the loud stretch is flagged as masking noise | [(10, 41)] |
| pass | two_planes: two planes found | 2 |
| pass | two_planes: each true plane matched with trace error < 3 Hz | 0.46, 0.14 |
| pass | two_planes: overlap is flagged |  |
| pass | two_planes: tones suppressed by at least 25 dB | 26.0 dB |
| pass | turning: still found as a flyover, flagged as an irregular glide | [('irregular glide', 0.84)] |
| pass | turning: tone suppressed by at least 10 dB (partial: a turning plane's faint tails cannot be extrapolated) | 13.6 dB |
| pass | turning: the safe copy's verdict matches what its own second check found | SAFE |
| pass | siren: not called a plane | 0 |
| pass | siren: both sirens noticed (low-confidence hint) | ['sweep', 'two-tone'] |
| pass | jet: exactly one flyover found | 1 |
| pass | jet: found in the high (fan-tone) search band | high |
| pass | jet: at least 3 harmonics found | [1, 2, 3, 4] |
| pass | jet: traced pitch within 1 % of the true pitch on average (1.6 kHz base tone) | 0.7 Hz |
| pass | jet: fan tones suppressed by at least 30 dB | 56.1 dB |
| pass | jet: notches take less than 10 % of the energy of plane-free sound in 300-3400 Hz | -19.0 dB |
| pass | jet: safe copy says SAFE and the second check finds no flyover | SAFE  |
| pass | links: CERTIFICATE_VERIFY_FAILED is retried once with --compat-options no-certifi | 2 calls |
| pass | links: --no-check-certificates is never used |  |
| pass | locate: refuses to run without a consent word and asks |  |
| pass | locate: a public challenge must be named |  |
| pass | locate: no plane in the audio -> stops |  |
| pass | locate: no ADS-B -> says what is needed |  |
| pass | locate: case.json has audio, utc_start (from the file's creation_time) and bbox | 2026-11-02T14:03:10Z |
| pass | locate: says the start time came from the file's tag and may be off |  |
| pass | locate: warns the box is smaller than 40 x 40 km |  |
| pass | locate: solver not installed -> install steps are shown |  |
| pass | locate: one plane -> says two possible places and gives the mirror point |  |
| pass | long: file is 10 minutes | 600.0 s |
| pass | long: check.py (with spectrogram PNG) finishes in under 60 s | 21.4 s (analysis alone 26.3 s) |
| pass | long: the 6 whole repeated planes and the start of the 7th (cut at 10:00) are all found | 7 found |

## A real jet recording (the repo's jet test clip, not published)

The repo's own test clip of jets at an airport (8.4 minutes, mono AAC, with a voice-over; it is git-ignored and nothing cut from it is committed)
shows about five passes, each a loud wide-band roar with a few thin sliding lines before and after the closest point (fan tones near 1.4 / 2.8 / 4.2 kHz,
about 5 to 8 seconds of them are clear). `check.py` takes about 20 s on it and reports:

| Time | Confidence | Pitch | Harmonics |
|---|---|---|---|
| 3:23-3:30 | low (0.52) | 2643 to 2428 Hz | 2 |
| 5:32-5:38 | low (0.37) | 1229 to 1198 Hz | 2 |
| 7:24-7:30 | medium (0.63) | 1456 to 1322 Hz | 5 |

That is three of the five passes (near 3:30, 5:40 and 7:32) and not the other two, which show only a few seconds of faint line before the roar and are below the detector's bar.
Only the 7:24 detection was compared by eye with the spectrogram, where it matches the thin 1.4 kHz line; the other two were not checked that way. `safe_copy.py` on the clip: 0.077 % of the energy removed
(-31.1 dB), overall level change -0.004 dB, mean spectrogram change outside the notches 0.043 dB
(0.083 dB in the 300-3400 Hz speech band), notches covering 0.28 % of the spectrogram; verdict **SAFE**:
the check on the copy finds no flyover. What that does and does not mean: the thin lines the detector can see are notched out; the wide-band roar of the jets is untouched,
and the real lines wander by about 1 % of their pitch, so the notch is wide (4-40 Hz) and only partly effective on the lowest harmonic of the faintest passes. This is a detector-level result, not proof
that a method which integrates over the whole roar could not still recover something.

## One real recording (no ground truth)

`check.py` was run on colsto's public challenge audio (`youtu.be/4CxVxuMVkMc`, 1 hour, Opus 48 kHz stereo), already stored in
`data/colsto/` by the repo's own pipeline. The check took about 35 s with the propeller band only and 200 s with both bands on this PC while other programs were running. A first version of the detector
reported 53 flyovers in that hour, most of them single-harmonic wobbles, steady machine tones and beeps. It was tightened (a flyover must be
at most 4 minutes long, slide at least 3 %, follow a Doppler-like curve or carry at least 3 harmonics at exact ratios, and not pulse on and off).
The result with only the propeller band was 13 flyovers; with the fan-tone band added it is 22 (the extra ones are tonal sounds at 0.4-3 kHz that were not checked against the audio, so expect some false positives in the high band on a recording like this one):

| Time | Confidence | Pitch | Harmonics | Kind |
|---|---|---|---|---|
| 0:20-0:37 | medium (0.57) | 233 to 220 Hz | 1 | plane |
| 1:58-2:04 | low (0.55) | 131 to 115 Hz | 2 | plane |
| 4:25-4:37 | medium (0.66) | 1992 to 1618 Hz | 1 | plane |
| 7:59-8:09 | medium (0.58) | 79 to 63 Hz | 4 | irregular glide |
| 8:12-8:21 | low (0.46) | 75 to 75 Hz | 4 | irregular glide |
| 12:50-13:29 | medium (0.74) | 98 to 89 Hz | 2 | plane |
| 16:00-16:08 | low (0.49) | 65 to 57 Hz | 2 | irregular glide |
| 16:12-17:26 | medium (0.66) | 135 to 129 Hz | 2 | plane |
| 23:17-23:36 | high (0.78) | 92 to 78 Hz | 2 | plane |
| 25:45-25:56 | low (0.46) | 127 to 122 Hz | 5 | irregular glide |
| 25:59-26:22 | high (0.77) | 74 to 55 Hz | 2 | plane |
| 27:40-28:10 | medium (0.66) | 123 to 117 Hz | 2 | irregular glide |
| 34:47-36:26 | high (0.89) | 162 to 110 Hz | 3 | plane |
| 36:26-36:35 | low (0.42) | 93 to 90 Hz | 2 | plane |
| 43:28-43:58 | medium (0.69) | 329 to 329 Hz | 4 | irregular glide |
| 43:28-44:08 | medium (0.71) | 416 to 391 Hz | 1 | plane |
| 44:46-45:11 | high (0.83) | 63 to 53 Hz | 3 | plane |
| 49:00-49:09 | low (0.52) | 105 to 98 Hz | 2 | irregular glide |
| 50:02-51:41 | medium (0.70) | 89 to 80 Hz | 5 | irregular glide |
| 50:42-51:32 | high (0.80) | 87 to 63 Hz | 2 | plane |
| 51:02-53:51 | medium (0.71) | 75 to 68 Hz | 2 | irregular glide |
| 53:09-53:23 | medium (0.62) | 81 to 71 Hz | 3 | irregular glide |

There is no list of true flyover times for this recording, so these are not scored. A look at the spectrogram around 34:47 shows a
clear plane with sliding harmonics, which matches the detection. This tuning on one recording is a reason to treat the confidence
labels as rough, not as probabilities.

## Not tested

- Real planes with known ground truth (only the one unscored recording above).
- Jets, helicopters, strong wind over a quiet plane, and recordings from phones with noise suppression.
- A real `yt-dlp` download and a real live-stream capture (only the certificate retry logic, with a stand-in).
- The real solver: the stand-in returned a canned answer. The solver is heavy, so it was not run here.
- `--fetch-adsb-now` (network).
- Stereo files where the channels hear the plane very differently, and files with more than one audio track.
