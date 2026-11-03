# numbers.md — every number in the post, where it comes from

Updated with all blind results, the colsto end-to-end run and the Miami safe-copy report. Values are copied from the **source file** named in each row.
`[BRACKETS]` = no result yet; it must not appear in a post. `post/copy.md`, `docs/faq-draft.md` and `docs/readme-sections.md` cite rows by ID (N01 ...).

Commands run from the repo root. `PY` = `data/vendor/doppler-locate/.venv/Scripts/python.exe`. `DOPPLOC` = `data/vendor/doppler-locate/.venv/Scripts/dopploc.exe`.
Cases: A = Miami webcam (PTZtv), B = Brighton City Airport apron cam, C = Southampton Town Quay ferry cam, E / F / G = three recordings of the **same** Midway (Chicago) camera, D = Waupaca (rejected, silent).
So there are **6 blind recordings from 4 cameras**.

## What the results say, in one paragraph

One blind recording was located close: Miami (case A), ridge v1.1, **749 m** from the spot the owner names (a restaurant's map position, ±40 m). The other five blind recordings missed by 7.0 to 27.6 km
(every other run that gave a point was 7.0 km or worse; A's own earlier run was 1,872 m). On Miami the solver's best and second-best peaks scored within 0.6 % of each other, 2.9 km apart, and the answer is the sum over 74 aircraft, not one clean tone.
colsto's challenge: a town match when doppler-locate's author measured the flyovers; a miss (137 km) when doppler-locate's detector measured them; both with a 300 km box chosen knowing the answer.
The skill's Miami safe copy re-checks as NOT FULLY SAFE (2 of 15 tones remain); the locator on that copy is still running.

## 1. Blind-run bookkeeping

| ID | Number | Value | Source file | Command |
|---|---|---|---|---|
| N01 | Seal commits (SHA-256 only) | A, B, C: `1000f05` (15:33); E: `b29eacd` (16:50); F, G: `7f33b39` (17:54) | `truth/SEALED.sha256` | `git log --format='%h %ad %s' --date=iso -- truth/SEALED.sha256` |
| N02 | Order for case A: seal → answers → reveal | `1000f05` (15:33) → `38fb65e` (17:38), `7b03594` (18:24) → `c103bf5` (18:35) | git history | `git log --format='%h %ad %s' --date=iso -- truth/SEALED.sha256 results/case_A_ridge results/case_A_ridge_v11 truth/case_A.json` |
| N03 | Truth matches seal | `truth_hash_ok: true` for A, B, C, E, F, G | `results/case_*_evaluation.json` | outsider: `cd truth && sha256sum --ignore-missing -c SEALED.sha256`; maintainer: `python recorder/seal_truth.py reveal` |
| N04 | Cameras and recordings | 5 cameras recorded; D (Waupaca) dropped for silent audio, never packaged or solved; 4 cameras → 6 blind recordings (E, F, G are one camera) | `TASKS.md`, `results/case_*_evaluation.json` | `ls cases results` |
| N05 | What the solver is given | audio (FLAC, mono 16 kHz), `utc_start`, a 40×40 km box (contains the camera, centre offset at random), ADS-B (live polls + full-day traces) | `cases/case_*/case.json` | `python -c "import json;d=json.load(open('cases/case_A/case.json'));print(d['utc_start'],d['bbox'],d['bbox_note'],d['duration_s'])"` |
| N06 | **What "truth" is** | A: the OSM map position of the 94th Aero Squadron restaurant (the owner's page says the camera is there), ±40 m. B: OSM position of the Shoreham Airport terminal building (tower assumed on it), ±40 m. C: OSM position of the La Regata restaurant at Town Quay, ±15 m. E, F, G: owner-published coordinates, ±30 m, copied by the repo owner, written by the main session (not a separate agent). The README's "location from the owner's own public page" is true of E, F, G only | `truth/case_*.json` → `coord_method`, `uncertainty_m`, `written_by` | `python -c "import json;t=json.load(open('truth/case_A.json'));print(t['coord_method'],t['uncertainty_m'])"` |
| N07 | The solver changed across runs | A: detector-first, ridge v1, v1.1. B: detector-first, v1.1, v1.2. C: v1.1. E: detector-first, v1, v1.1. F, G: v1.3. v1.1 was committed (`6891c89`, 17:18) after the first E miss and before any A answer (`38fb65e`, 17:38). Truth for each case was opened only after all its runs were committed | git history; `solver_ridge/ridge.py` docstring | `git log --format='%h %ad %s' --date=iso -- solver_ridge solver` |

## 2. Miami (case A): the one close blind run

| ID | Number | Value | Source file | Command |
|---|---|---|---|---|
| N10 | Detector-first solver | no flyover detected, no point | `results/case_A_evaluation.json` → `runs[0]` | `PY solver/solve.py cases/case_A --out results/case_A` |
| N11 | Ridge v1 | **1,872 m**, 69 aircraft (README), latency 24.25 s | `results/case_A_evaluation.json` → `runs[1]` | `python solver_ridge/ridge.py cases/case_A --out results/case_A_ridge` (code as of that commit) |
| N12 | **Ridge v1.1** | **749 m**; latency 38.5 s; score 34.58 = 15.86× the grid median; 74 aircraft scored; 163 candidates; runtime 2,627.5 s (43.8 min) | `results/case_A_evaluation.json` → `runs[2]`; `results/case_A_ridge_v11/answer.json` (`lat`, `lon`, `score`, `score_over_null`, `latency_s`, `n_candidates`, `runtime_s`) | v1.1 code is commit `6891c89` (reference checkout `solver_ridge_v11_ref/`, git-ignored): `git worktree add solver_ridge_v11_ref 6891c89`, then `python solver_ridge_v11_ref/solver_ridge/ridge.py cases/case_A --out results/case_A_ridge_v11` |
| N13 | **How sure is the answer?** Runner-up peak | 2,917 m from the answer with score 34.377 vs 34.583: **0.6 % lower**. Mirror point 3,430 m away at 80 % of the score. `two_band: false` | `results/case_A_ridge_v11/answer.json` → `runner_up`, `mirror`, `two_band` | `python -c "import json;a=json.load(open('results/case_A_ridge_v11/answer.json'));print(a['runner_up'],a['mirror'])"` |
| N14 | Where the score comes from | 74 aircraft contribute; the top three (ENY3714 Embraer 175, AAL1901 B38M, AAL2883) give 1.47 + 0.79 + 0.77 = 8.8 % of the 34.58 | `answer.json` → `aircraft[].contribution` | `python -c "import json;c=sorted((x['contribution'] for x in json.load(open('results/case_A_ridge_v11/answer.json'))['aircraft']),reverse=True);print(sum(c[:3])/sum(c))"` |
| N15 | Audio | 66.2 min (3,974.87 s), mono 16 kHz; "mostly jets" (README) | `cases/case_A/case.json` → `duration_s` | as N05 |
| N16 | The reference point | OSM node 5533485322 (94th Aero Squadron restaurant), ±40 m; the owner's text: "alongside MIA runway 9/27" | `truth/case_A.json` | as N06 |
| N17 | The animation's numbers | "found blind: 749 m", Embraer 175 ENY3714 closest approach 2,070 m, 74 aircraft, latency 38.5 s, 40×40 km box | `post/anim_data_miami.json` (`map.error_m`, `map.planes_used`, `aircraft`, `clip.latency_s`) | see the header of `post/anim/miami_data.py` |

## 3. All blind runs (the full table; the post uses every row)

| ID | Number | Value | Source file | Command |
|---|---|---|---|---|
| N20 | B, Brighton | detector-first: no flyover; ridge v1.1: 6,956 m; ridge v1.2: 17,607 m | `results/case_B_evaluation.json` | `python solver_ridge/ridge.py cases/case_B --out results/case_B_ridge_v12` (v1.2 code at `cbc7d2b`) |
| N21 | C, Southampton | ridge v1.1: 20,565 m (one aircraft carried most of the score, README) | `results/case_C_evaluation.json` | as above with `cases/case_C` |
| N22 | E, Midway | detector-first: no flyover; ridge v1: 27,620 m; ridge v1.1: 21,679 m | `results/case_E_evaluation.json` | as in N12 with `cases/case_E` |
| N23 | F, Midway (before the ad) / G (after) | ridge v1.3: 23,400 m / 27,500 m | `results/case_F_evaluation.json`, `results/case_G_evaluation.json` | `python solver_ridge/ridge.py cases/case_F --out results/case_F_ridge_v13` |
| N24 | **Tally** | 6 blind recordings, 4 cameras; 12 runs, 9 gave a point; **1 recording within 1 km (A)**; A's other run 1,872 m; every other run ≥ 7.0 km; the 5 missed recordings by 7.0–27.6 km (best run per recording: 6,956; 20,565; 21,679; 23,400; 27,500 m) | N10–N23 | — |
| N25 | Likely reasons for the misses (README's interpretation) | Southampton: very little traffic; Midway: jets 90–150 m up, roar; Brighton: light aircraft and helicopters | `README.md` blind-run table | — |

## 4. colsto's challenge (one hour of raw audio, "filmed 9/8/26" somewhere in the continental US)

| ID | Number | Value | Source file | Command |
|---|---|---|---|---|
| N30 | Run 1: events as measured by doppler-locate's author (`cases/oyster.toml`) | rank 1 40.8764, −73.5258, **inside the Oyster Bay hamlet, 925 m from its centre**; 48 flight pairs; score 3.662 vs 3.483; PC-12 CST1219 + King Air EGE93; 23.1 min | `results/colsto_dopploc/metrics.json` | `DOPPLOC search out\colsto_dopploc\case.toml`, then `DOPPLOC score out\colsto_dopploc\case.toml --top 5` |
| N31 | Run 2: end to end, doppler-locate's detector run on the audio | rank 1 41.9212, −72.6856 in Connecticut, **136.6 km** from the hamlet centre; 517 pairs; score 5.135 vs 5.010; TBM 900 N217DC + Cessna 400 N65031; 44.2 min (detect 1,468 s + search 535 s + score 650 s) | `results/colsto_dopploc_e2e/metrics.json` | the commands recorded in `results/colsto_dopploc_e2e/detect.log`, `search.log`, `score.log` (`dopploc detect`, then search and score) |
| N32 | In run 2 the right pair | **5th of 517**, score 4.881 vs 5.135 at rank 1 (4.9 % lower), inside the hamlet 1,192 m from its centre | `metrics.json` → `pair_that_ranked_1_in_the_oyster_toml_run` | — |
| N33 | Detection vs the author's measurement | the detector found the same two flyovers (audio times 2,155.35 s vs 2,155.8 s; 3,068.1 s vs 3,072.4 s) but E1 with one harmonic instead of three and E2's tone at 140.7 Hz instead of about 134 Hz (README) | `metrics.json` → `detected_vs_oyster_toml`, `detect_summary` | — |
| N34 | **Caveats that must travel with N30–N32** | (1) 300×300 km box around NYC chosen knowing the answer is on Long Island (the whole-US search does not fit this PC's memory); (2) run 1's events are doppler-locate's author's; run 2's event choice (two highest scores of 8, fixed before search) was "not blind in effect: the same two times had already been seen" in `oyster.toml`; (3) camera coordinates unpublished: a town match, no error in metres; (4) "our own detection" is doppler-locate's `dopploc detect`, not code of ours; (5) the Connecticut pair was rank 2 in run 1 too (41.920, −72.685) | `metrics.json` → `regional_box_note`, `detect_summary.rule_used`, `camera_coordinates`; `results/colsto_dopploc/summary.md` | — |
| N35 | Safe copy of colsto's audio, then run 2's pipeline (the other possible before/after) | [COLSTO_SAFE_RESULT]: not run. Only meaningful if run 2 had worked | — | — |

## 5. The skill (`flyover-check`)

From `skills/flyover-check/TESTS.md` unless stated. Synthetic = built by `scripts/make_test_audio.py` with a fixed seed.

| ID | Number | Value | Source | Command |
|---|---|---|---|---|
| N40 | Self-checks passing | 56 of 56 (synthetic) | `TESTS.md`; `data/skilltest/results.json` (git-ignored) | `uv run skills/flyover-check/scripts/selftest.py` |
| N41 | Synthetic plane tone, after the safe copy | **37.1 dB** quieter; second check `SAFE`; video stream byte-identical; audio length unchanged | `TESTS.md` §2 | same |
| N42 | What the notches take from sound with no plane in it, 300–3400 Hz | 4.8 % of the energy (−13.2 dB); mean spectrogram change outside the notches 0.046 dB; overall loudness −0.82 dB | `TESTS.md` §3 | same |
| N43 | Synthetic jet fan-tone stack | **56.1 dB** quieter; `SAFE` | `TESTS.md` §3b | same |
| N44 | Mains hum / overlapping planes / turning plane (synthetic) | 34.1 dB / 26.0 dB / 13.6 dB (partial) | `TESTS.md` §2 | same |
| N45 | Decoy mode | the check still hears a plane; fake pitch 17.1 Hz off on average | `TESTS.md` §4 | `uv run skills/flyover-check/scripts/safe_copy.py INPUT --decoy` |
| N46 | Speed | a 10-minute file checks in **21.4 s** | `TESTS.md` §5 | `uv run skills/flyover-check/scripts/check.py INPUT` |
| N47 | Real Midway clip, check | **3 of about 5 jet passes** found (3:23 low, 5:32 low, 7:24 medium); the other two are below the bar; only 7:24 compared by eye | `TESTS.md` "A real jet recording" | `uv run skills/flyover-check/scripts/check.py data/clip/case_E_test.mp4` |
| N48 | Real Midway clip, safe copy | 0.077 % of energy removed (−31.1 dB); speech band change 0.083 dB; re-check finds no flyover (`SAFE`, detector-level) | `TESTS.md` same section | `uv run skills/flyover-check/scripts/safe_copy.py data/clip/case_E_test.mp4 --out [FILE]` |
| N49 | **Miami audio, check** | **15 plane tones** in 66 min: 7 confident (8:25, 8:48, 10:13, 11:21, 17:18, 35:52, 58:05) and 8 low-confidence | `results/case_A_safe/audio.safe-copy.json` → `before`; `safe_copy_report.json` | `uv run skills/flyover-check/scripts/safe_copy.py cases/case_A/audio.flac --out cases/case_A_safe/audio.flac` |
| N50 | **Miami audio, safe copy** | 3 passes (518 s); energy removed 0.191 % (−27.2 dB); level −0.011 dB; speech band mean change 0.010 dB; notches cover 0.12 % of the spectrogram. **Re-check: NOT FULLY SAFE**: 2 tones remain (0:01–0:56 medium; 25:47 low) | `results/case_A_safe/safe_copy_report.json`, `audio.safe-copy.json` → `after`, `audio_change` | same; the report suggests `--passes 4` |
| N51 | **The locator on the Miami safe copy** | [MIAMI_SAFE_RESULT]: **pending** (`results/case_A_safe/ridge_v11/` is empty; a run is in progress). Do not claim anything until it exists, and report it whichever way it lands | `results/case_A_safe/ridge_v11/answer.json` (expected) | `python solver_ridge_v11_ref/solver_ridge/ridge.py cases/case_A_safe --out results/case_A_safe/ridge_v11` |
| N52 | colsto's hour through `check.py` | 22 flyovers flagged (13 in the propeller band alone); not scored; expect false positives in the high band | `TESTS.md` "One real recording" | `uv run skills/flyover-check/scripts/check.py data/colsto/recording_4CxVxuMVkMc.webm` |
| N53 | Install | verified (reported by the repo owner's session; no log in the repo: add one) | [LOG_FILE] | `npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check` |
| N54 | Locate mode | now drives doppler-locate's own pipeline (detect, fetch and ingest the adsb.lol day, search, score); **dry run only so far** | commit `ab32d96`; `skills/flyover-check/SKILL.md` Mode 3 | `uv run skills/flyover-check/scripts/locate.py INPUT --consent own … --dry-run` |
| N55 | Not covered by any run yet | real planes with known ground truth in the skill's own checks; helicopters; phone noise suppression; real download and live capture; `--fetch-adsb-now`; a full (not dry) locate run | `TESTS.md`, last section | — |

## 6. Clock, ADS-B, compute

| ID | Number | Value | Source file | Command |
|---|---|---|---|---|
| N60 | Where `utc_start` came from | HLS `#EXT-X-PROGRAM-DATE-TIME` of the first segment (A, B, C); the file's wall-clock start (E, F, G) | `cases/case_*/case.json` → `clock` | `python -c "import json;print(json.load(open('cases/case_A/case.json'))['clock'])"` |
| N61 | Latency found on Miami (v1.1) | 38.5 s, scanned −10…+60 s with step 1.0 s | `results/case_A_ridge_v11/answer.json` → `latency_s`, `params` | as N12 |
| N62 | Share of live adsb.lol polls lost to HTTP 429 | about 45 % (`TASKS.md`); exact [PCT_429] % | `data/raw/rec_adsb.log` | `tail -1 data/raw/rec_adsb.log`; share = 429 / (ok + 429) |
| N63 | Compute | Miami v1.1: 43.8 min; colsto run 1: 23.1 min, peak 4.96 GB; run 2: 44.2 min, peak 5.59 GB; Ryzen 7 5700X, 16 GB, CPU only; another solver was running at the same time | answer.json / metrics.json files above | `PY solver/runmeter.py OUT.json -- command arg ...` |

## 7. Images

| ID | File | Shows | Notes |
|---|---|---|---|
| N70 | `post/miami_still_01_spectrogram.png` … `04_found.png`, `post/miami_vertical.mp4`, `miami_monitor2.mp4`; data `post/anim_data_miami.json` (not a preview) | 20 s of the Miami audio, 3,000–6,000 Hz: a near-flat engine tone at about 4,500 Hz labelled "pitch falling slowly"; the matched Embraer 175 ENY3714 curve (2,070 m); the one-aircraft heat map; the final "found blind: 749 m" | Real data. Not shown: the near-tied runner-up peak (N13) and that 74 aircraft, not one, give the answer (N14); "real camera" is the restaurant's map position (N16). The 1 km scale bar and the 749 m pin–star gap agree |
| N71 | `post/colsto_still_01…04.png`, `colsto_vertical.mp4`; data `post/anim_data_colsto.json` | PC-12 N22NY, closest approach 6,129 m; "found: inside Oyster Bay (colsto's answer) · camera coordinates not published: town match" | Built before run 2: it does not say that the events are the author's, that the box was chosen knowing the answer, or that the automatic run missed (N34). Add that on screen or in the post text |
| N72 | `results/colsto_dopploc/rank1.png`, `scatter.png` (run 1); `results/colsto_dopploc_e2e/rank1.png`, `scatter.png` (run 2) | spectrograms with the predicted curves; score scatter | run 2's pair is the other pair |
| N73 | `post/still_0*.png`, `post/post_vertical.mp4` | the first demo with invented values | **retired: never post** |

## Retired claims (do not use)

- "Found the camera within [X] m on the clip" for Midway, and "safe copy → not found": not true (N22). For Miami the "safe copy → not found" is **pending** (N51) and the check already says NOT FULLY SAFE (N50).
- "Owner-published coordinates" for Miami, Brighton and Southampton: they are map positions of the named building (N06).
- "1 of 6 cameras within 1 km": it is 1 of 6 blind **recordings**, from 4 cameras (N04).
- "Our own detection" for colsto run 2: it is doppler-locate's detector (N34).

## Rules for filling this in

- One command, one file, one value. If a value is typed by hand, say so in the source column.
- A post uses the bad rows as readily as the good ones (N20–N24, N31, N50). Do not swap in a better one.
- Round the way the post rounds (km to 0.1, metres to whole numbers).
