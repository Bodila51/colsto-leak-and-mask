# post/anim_data.json

The one input of the post animation. `python post/anim/render.py` reads `post/anim_data.json` if it exists, otherwise
`post/anim/preview_data.json` (invented demo values, every frame stamped "PREVIEW — not real data").
`--data PATH` and `--preview` override this. `build.py` checks the file and stops with the name of the field that is wrong.

Everything on screen comes from this file, from `post/monitor1_recording.mp4` (the spectrogram is computed from its audio)
and from the safe-copy audio. Nothing is typed into the page.

```jsonc
{
  "preview": false,                       // true stamps the big PREVIEW watermark on every frame. Omit or false for the real run.
  "clip": { "utc_start": "2026-11-03T14:32:29.000Z",     // stream clock at clip t = 0 (first audio sample)
            "latency_s": 28.0 },                          // optional: how far the stream lags real time. The clock top right shows
                                                          //   utc_start - latency_s + t, i.e. the real UTC of the sound. Use the solver's value.
  "safe_audio": "results/case_V/safe.wav", // path from the repo root; any file ffmpeg can read, >= 20 s, same t = 0 as the clip.
                                           // Its spectrogram is the "after" picture (scene 5).

  "curves": [                              // predicted tone, one entry per harmonic of the matched aircraft (aircraft[0]),
    { "harmonic": 1,                       //   at the FOUND location. Drawn cyan, dashed, on the spectrogram.
      "t_s":  [14.3, 14.4, ...],           // clip seconds, ascending, >= 2 points (any spacing; linear between points)
      "f_hz": [1393.0, 1391.2, ...] },     // Hz, same length as t_s
    { "harmonic": 2, "t_s": [...], "f_hz": [...] }
  ],

  "aircraft": [                            // [0] = the matched aircraft of the 8-11 s scene, [1] = the second one (optional)
    { "id": "A",
      "label": "Boeing 737-700 · N943WN",  // shown in the badge: "type · registration or callsign". Keep it under ~30 chars.
      "closest_m": 1234,                   // closest approach to the FOUND point, metres (badge + column text)
      "track": { "lat": [..], "lon": [..] } },   // ADS-B track over the box, >= 2 points, drawn on the map
    { "id": "B", "label": "...", "closest_m": 2345, "track": { "lat": [..], "lon": [..] } }
  ],

  "map": {
    "bbox": [lat_min, lon_min, lat_max, lon_max],   // the search box (map shows exactly this; OSM vectors are fetched for it)
    "lat": [..], "lon": [..],              // regular grid axes, ascending or descending, >= 2 each (resample the solver lattice)
    "heat_1": [[..], ..],                  // len(lat) x len(lon): score of the 1-aircraft run (shows two bands). null = outside box.
    "heat_2": [[..], ..],                  // same shape: the run with 2 or more aircraft (collapses to one peak).
                                           //   "heat_all" is accepted instead of "heat_2". Each map is scaled by its own min and max.
    "planes_used": 2,                      // number of aircraft behind heat_2 ("2 aircraft -> 1 place"; the headline says "second" for 2)
    "found":  { "lat": .., "lon": .. },    // the solver answer (the pin)
    "true":   { "lat": .., "lon": .. },    // the real camera (the star)
    "error_m": 108,                        // great-circle distance found <-> true, metres. The big "found: N m" number.
                                           //   build.py warns if it differs from the coordinates by more than 5 %.
    "gamma_1": 2.2, "gamma_2": 3.0,        // optional: contrast of the heat maps (higher = thinner glow)
    "safe": { "found": false,              // result of the same solver run on the safe copy. false -> "not found".
              "heat": [[..], ..],          //   optional, same shape; drawn grey. error_m only used when found is true ("still found").
              "error_m": null }
  },

  // optional ---------------------------------------------------------------------------------------------------------
  "spectrogram": { "f_min_hz": 0, "f_max_hz": 450,   // band shown. default 0-450 Hz (colsto style) for propellers and helicopters.
                                                     //   The Midway 737 clip: fan lines at about 1.4 / 2.8 / 4.2 / 5.6 kHz, so use 500-6000.
                   "window_s": 1.0,                  // analysis window; use ~0.3 for tones that slide fast. default 1.0
                   "view_s": 12,                     // seconds visible while it scrolls (scene 1). 20 = the whole clip, no scrolling. default 12
                   "e_range_db": [6, 26] },          // brightness range (dB above the local median). default 6-26 for <= 600 Hz, else percentiles
  "callout": { "tip_t_s": 3.0, "tip_f_hz": 210,      // where the "aircraft engine tone" label points (clip s, Hz). default: the 3rd
               "from_s": 0.35, "to_s": 7.5,          //   curve, 1.6 s behind the playhead. window in which it is shown
               "sub": "steady hum + harmonics",      //   small line under it. `"callout": false` hides it
               "curve": 2 }                          //   index into curves that the moving tip follows (default 2 = the 3rd)
}
```

### Cases without stream footage or camera point (the colsto challenge: `post/anim_data_colsto.json`)

```jsonc
{
  "audio": { "path": "data/colsto/recording_4CxVxuMVkMc.webm", "start_s": 2145.1 },  // spectrogram source and its clip t = 0
                                           //   (default: post/monitor1_recording.mp4 from 0). Only the picture is shown, never the sound.
  "safe_audio": null,                      // no safe copy: no scene 5, scene 4 holds to 20 s, 4 stills instead of 5
  "map": {
    // "true" and "error_m" may be left out when "area" is given: the town polygon replaces the star and the distance
    "area": { "name": "Oyster Bay", "rings": [[[lat, lon], ...]], "center": { "lat": .., "lon": .. },
              "found_text": "inside Oyster Bay", "sub": "(colsto's answer)", "map_label": "Oyster Bay (colsto's answer)" },
    "zoom_half_m": 3000,                   // half width of the final zoom (default: 2.6 x error_m)
    "note": "camera coordinates not published: town match"   // shown under the result
  },
  "second_event_note": "...",              // optional line under "2 aircraft -> 1 place"
  "credits": "..."                         // replaces the credit line
}
```

`post/anim/colsto_data.py` writes it (run it with doppler-locate's venv python). What it is made of:

| Field | Source |
|---|---|
| spectrogram | colsto's challenge audio, 2145.1-2165.1 s (event 1, fitted CPA at clip 10 s), 50-450 Hz. Silent outputs: his audio is never in the video. |
| `curves` | doppler-locate `report.overlay_data` at the rank-1 point: its own forward model (ADS-B track, GFS atmosphere, fitted f0 and clock shift), the same curves as `rank1.png`. Harmonics 1-3 of event 1. |
| `clip.utc_start` | the engine's fit: event-1 delayed CPA `t1m` at audio `audio_cpa + delta`. Not a stream clock. |
| `aircraft` | the two flights of rank 1 (PC-12 N22NY for event 1, King Air 350 N93CQ for event 2) as the engine reconstructs them from adsb.lol; `closest_m` = modelled slant distance `d1m` / `d2m`. |
| `heat_1`, `heat_2` | `dopploc heatmap out/colsto_dopploc/case.toml --rank 1 --half 6000 --step 200`: the rank-1 flight pair re-scored on a 61 x 61 grid (12 x 12 km) around rank 1. `heat_1` = S1 (event 1 alone), `heat_2` = S1 + S2. Resampled linearly onto 81 x 81 lat/lon. Outside that 12 km square there is no score, not a low score. |
| `found` | rank 1 of `best_per_pair.csv` (= `results/colsto_dopploc/answer.json`). |
| `area` | `results/colsto_dopploc/oyster_bay_hamlet.geojson` (OSM relation 174432). There is no camera point: colsto did not publish coordinates, so there is no distance, only the town match. |

Event 2 (3072 s) is 917 s after event 1 and is not in the 20 s spectrogram; the map in scene 4 uses it, the picture does not show it.

### The blind Miami result (`post/anim_data_miami.json`, written by `post/anim/miami_data.py`)

Same format with a real camera point: `map.found_label` ("found blind:") replaces "found:", and `map.note`
("40×40 km search box") is shown under the result (landscape) or at the start of the legend line (tall).

| Field | Source |
|---|---|
| spectrogram | `cases/case_A/audio.flac`, case audio 50-70 s, 3000-6000 Hz. ENY3714's first pass runs 0.1-69.9 s in `curves.json`; its closest approach is at 1.6 s, but there its tone does not stand out (power on the predicted curve = the median of shifted copies), at 50-70 s it does (8.3 dB vs 2.0 dB), so the clip is that part of the same pass. Silent outputs, no webcam footage. |
| `curves` | `results/case_A_ridge_v11/curves.json`, ENY3714 x1: the solver's predicted curve at the answer point and latency, already in case audio seconds (snapped to its log-f bins, hence the steps). |
| `clip` | `utc_start` of the case + 50 s, `latency_s` 38.5 from `answer.json` |
| `aircraft` | the top two of `answer.json` (ENY3714 Embraer 175, AAL1901 Boeing 737 MAX 8), `closest_m` = `closest_slant_m`; tracks from `cases/case_A/adsb.jsonl`, ±7 min around each closest approach |
| `heat_1`, `heat_all` | `heat_1.npy` (top aircraft alone) and `heat_all.npy` (all aircraft, max over latency) on `grid.json`. `planes_used` = the 74 aircraft listed in `answer.json` (those with a contribution at the answer; 163 candidates in total). |
| `found`, `true`, `error_m` | `answer.json`; `results/case_A_evaluation.json` (owner-published camera, revealed after the answer, seal hash ok; 749 m) |

No scene 5: `results/case_A_safe/` had no solver run on the safe copy yet when this was made.

## What each picture is made of

| On screen | Source |
|---|---|
| Spectrogram, scene 1-4 (before) | audio of `post/monitor1_recording.mp4`, 0-`f_max_hz`, magma, dB. Brightness = dB above the local median (so steady tones stand out from speech) plus a little of the plain level. Same maths for before and after. |
| Spectrogram, scene 5 (after) | `safe_audio`, with the brightness constants of the "before" picture |
| Cyan dashed lines | `curves` |
| Badge, "closest approach" | `aircraft[0].label`, `aircraft[0].closest_m` |
| Heat map, 1 aircraft | `map.heat_1` and `aircraft[0].track` |
| Heat map, 2 aircraft | `map.heat_2` (or `heat_all`), `aircraft[1].track`, `map.planes_used` |
| Pin / star / "found: N m" | `map.found` / `map.true` / `map.error_m` |
| "not found" | `map.safe.found == false` |
| Clock | `clip.utc_start` + t |
| Roads, coast, runways | OpenStreetMap via the Overpass API for `map.bbox` (cached in `build/`); Natural Earth 10m coastline if Overpass is down |

## Outputs

| File | What | Publishable |
|---|---|---|
| `post/monitor2_analysis.mp4` | 1920×1080, 20 s, silent, our visuals only | yes |
| `post/post_no_footage.mp4` | 1080×1350, 20 s, silent, our visuals only (own tall layout) | yes |
| `post/still_01_spectrogram.png` … `still_05_safe.png` | 1080×1350, our visuals only | yes |
| `post/post_vertical.mp4` | 1080×1350, stream footage on top + its audio | **no, local only** (StreamTime LIVE forbids copying) |
| `data/post_local/still_0N_*_with_footage.png` | stills with a stream frame on top | **no, local only** |

The spectrogram is computed from the stream's audio. It is a derived picture, not a copy of the footage; check that is fine with the
stream's owner if in doubt. Never commit an mp4 or a frame of the footage.

## Things to get right

- `t_s` in `curves` and `track` are in **clip seconds** (0 = first audio sample), not UTC. Convert from the stream clock with the
  solver's latency before writing.
- The 20 s clip may contain only one audible aircraft. If `heat_2` uses a second flyover from elsewhere in the recording, that is
  not visible in the 20 s spectrogram: say so in the post text, or set `planes_used` accordingly.
- The heat grids must be regular in lat/lon. The solver's local metric lattice has to be resampled onto `lat` x `lon`
  (about 100-200 points per side is plenty; the page smooths them).
- `map.error_m` is shown as written. Keep it consistent with `found` and `true`.

## Files

- `render.py`: the whole pipeline (`--preview`, `--data PATH`, `--stills-only`, `--keep-frames`).
- `build.py`: spectrogram PNGs, map vectors, data check -> `build/` (git-ignored).
- `index.html`: the animation, `window.renderAt(t)`, `?layout=landscape|vertical`. No requestAnimationFrame, no Date.now, no Math.random.
- `tools/cap.js`: Playwright capture, 8 pages in parallel (uses `tools/node_modules`, else the shared copy in `~/Downloads/legends-video/tools`).
- `make_preview.py`: regenerates `preview_data.json` (demo values only).
- `fonts/`: Inter and JetBrains Mono, SIL Open Font License 1.1.
