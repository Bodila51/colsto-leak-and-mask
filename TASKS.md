# TASKS

## Phase 1: first public push
- [x] Repo skeleton, .gitignore (data/ out of git)
- [x] Tools: uv (python -m uv), gh, ffmpeg, yt-dlp (needs --compat-options no-certifi on this PC)
- [x] Pick 3 webcams with audio and published locations, random case ids, offset 40x40 km boxes (sealed in data/sealed/cams.json)
- [ ] Recording: HLS audio with UTC per segment + live ADS-B (adsb.lol, airplanes.live) around box centres, 90 min + 3 h backfill
- [ ] Truth written by a separate agent into data/sealed/truth/, SHA-256 committed before solving
- [ ] Location engine on Windows (doppler-locate, CPU)
- [ ] Synthetic test, 2 seeds (one noisy)
- [ ] Test A: colsto's challenge
- [ ] Solver for live cases, frozen by commit before cases open
- [ ] Case packages (anonymous audio, UTC start, box, ADS-B) -> blind answers committed
- [ ] Evaluator: truth revealed, error in metres, heat maps 1 / 2 / all planes
- [ ] Test clip: webcam piece + AI voice over it, located blind, then safe copy -> nothing found
- [ ] Skill flyover-check: check, safe copy, locate; installs with npx skills add
- [ ] README with results, credits, limits, responsible use, MIT licence
- [ ] Push (needs empty repo Bodila51/flyover-leak + gh auth login)

## Phase 2
- [ ] Rescore webcams with adsb.lol history (next day ~03:30 UTC)
- [ ] Error vs number of planes chart
- [ ] Post: video 1080x1350, stills, numbers.md
