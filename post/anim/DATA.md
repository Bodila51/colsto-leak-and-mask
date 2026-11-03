# post/anim_data.json

The one input of the post animation. `python post/anim/render.py` reads `post/anim_data.json` if it exists, otherwise
`post/anim/preview_data.json` (invented demo values, every frame stamped "PREVIEW — not real data").
`--data PATH` and `--preview` override this. `build.py` checks the file and stops with the name of the field that is wrong.

Everything on screen comes from this file, from `post/monitor1_recording.mp4` (the spectrogram is computed from its audio)
and from the safe-copy audio. Nothing is typed into the page.

```jsonc
{
  "preview": false,                       // true stamps the big PREVIEW watermark on every frame. Omit or false for the real run.
  "clip": { "utc_start": "2026-11-03T13:31:14.000Z" },   // UTC of clip t = 0 (first audio sample). Drives the clock top right.
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
  "spectrogram": { "f_min_hz": 0, "f_max_hz": 450,   // band shown. default 0-450 Hz (colsto style). Use e.g. 0-4500 for a jet fan tone.
                   "window_s": 1.0,                  // analysis window; use ~0.3 for tones that slide fast. default 1.0
                   "view_s": 12,                     // seconds visible while it scrolls (scene 1). 20 = the whole clip, no scrolling. default 12
                   "e_range_db": [6, 26] },          // brightness range (dB above the local median). default 6-26 for <= 600 Hz, else percentiles
  "callout": { "tip_t_s": 3.0, "tip_f_hz": 210,      // where the "aircraft engine tone" label points (clip s, Hz). default: the 3rd
               "from_s": 0.35, "to_s": 7.5,          //   curve, 1.6 s behind the playhead. window in which it is shown
               "sub": "steady hum + harmonics" }     //   small line under it. `"callout": false` hides it
}
```

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
