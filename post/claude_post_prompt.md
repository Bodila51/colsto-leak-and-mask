Write X posts for me (EN, plus an RU translation of each). Maximum hook, but every claim must be true. Mark anything that is a stretch.

## Context

- YouTuber colsto (video, 2026-11-02, "I discovered a sound that leaks your exact location (and it's everywhere)") showed how it works. A passing plane's engine tone slides down in pitch (Doppler). From the shape of the slide you get the speed, the closest distance and the moment it passed. Matched against public flight data (ADS-B), that tells you which plane it was and where the microphone was. He published no code.
- Paras Chopra repeated it with Claude Code on a Southampton webcam, a known camera with one ATR 72. He published no code.
- Another post: someone recorded laptop-mic audio at home (known location). Claude found the plane tone, and exactly one flight in the flight data matched.

## What I did (repo: github.com/Bodila51/colsto-leak-and-mask, MIT, everything open)

1. **Midway (Chicago), the main demo.**
   - Material: my own screen recording of the public StreamTime LIVE stream at Midway airport, 2026-11-03, about 14:32 UTC. A Boeing 737 MAX 8 takes off and climbs right over the camera, and its fan tone slides 1,395 → 1,107 Hz.
   - From the sound alone: speed 139 kt, closest 120 m, closest pass at 14:32:20 UTC. The stream's delay (28 s) was read from the clock burned into the video.
   - Public ADS-B (adsb.lol) has 35 aircraft within 10 km during ±10 min. Exactly one matches: SWA4113, a Boeing 737 MAX 8, 143 kt, 13 m from the camera horizontally (about 130 m up), 0.1 s from the moment the sound gives. The next best (SWA1076) is 65 s off, and its score is 19× worse.
   - Important: the camera location was KNOWN. The sound identified WHICH plane it was.
2. **Southampton, the same test as Paras.**
   - Setup: a ferry webcam at Town Quay, known camera, one hand-picked plane.
   - A TBM 700 (N850BG) passed about 3 km away and 4 km up. Its predicted Doppler curve lies on the real engine tone (fit 0.80 vs 0.40 for a flat tone). On the ±6 km map there are two mirror bands, and the camera sits on the brighter one.
   - The "which plane" test also gives N850BG (0.84 Hz vs 2.13 Hz for the runner-up, 2.5×). That gap is not rock-solid.
3. **colsto's challenge.**
   - With the flyover events as measured by the author of the open doppler-locate project, rank 1 is inside Oyster Bay, NY (colsto's answer), 925 m from the town centre. The planes it gives are a Pilatus PC-12 N22NY and a King Air 350 N93CQ.
   - With fully automatic detection it's a miss: 137 km off, and the right pair came 5th of 517.
   - The search box was chosen knowing it is Long Island.
4. **Blind tests** (the answer sealed by SHA-256 before solving; the program got only the sound, the time and a 40×40 km box).
   - 6 recordings from 4 cameras. **None was located by sound.**
   - Miami's 749 m looked like a hit, but a control run (the same sound shifted by 30 min) landed in nearly the same place. So it was not the sound, and this is marked "not valid" in the repo.
   - A synthetic test missed too, by 3-6 km.
5. **A Claude Code skill**, `npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check`.
   - It checks a video for plane noise that can give away the place; it found the takeoff in the Midway recording.
   - The "safe copy" cuts out the tones it hears, BUT it does not yet hide the location from our own search. That was checked on Miami.

## The post video (three windows)

1. My Midway recording with a green box on the plane: "TARGET LOCKED · SWA4113".
2. A spectrogram drawing live: the tone slides down, then "sound only: 139 kt · 120 m" and "match 1 of 35: SWA4113".
3. A terminal with the real run of the script (`post/tools/live_flight_id.py`).

## Rules

- Don't write "Claude Code discovered": colsto discovered it. "found / identified / matched" is fine.
- The skill is called /flyover-check, not /colsto.
- Don't claim that it finds a location from scratch, or that the safe copy hides you. The demo is: "known location → the sound identified the exact plane".
- Don't use "hacked", "reverse engineered", "tested/untested".
- Don't criticise colsto, Paras or anyone else; credit them: colsto (method), doppler-locate (open engine), adsb.lol (data).
- Honest misses are part of the hook: "I tried it blind: 0 of 6".

## Give me

- 3 hook variants for a single post (≤280 characters each, with the repo link);
- one 5-6 tweet thread;
- each in EN and RU;
- after each variant, a line saying which claim relies on which fact above.
