# Questions people asked — draft

Plain-words answers with the real numbers. The row named after each answer is in [`post/numbers.md`](../post/numbers.md) and gives the file it comes from.
A question we have not measured says so.

## How often does it work?

**1 blind recording in 6.** The recordings come from 4 public webcams (three of them are the same Midway camera). Miami was located 749 m from the spot the owner names (map position of a restaurant, ±40 m).
The other five missed: Brighton 7.0 km (and 17.6 km on a second run), Southampton 20.6 km, Midway 21.7 to 27.6 km on all three recordings. Every run that gave a point, except Miami's two, was 7 km or worse (N20–N24).
It is a small sample and the solver changed along the way (N07).

## Was Miami luck?

Partly, and the files say so. The answer is the sum over 74 aircraft; the top three give only 8.8 % of the score. The second-best peak, 2.9 km away, scored 0.6 % lower than the winner (34.377 against 34.583). An earlier version gave 1,872 m, and the first, detector-first solver found no flyover at all (N10–N14).
What we can say is that two runs with different code both landed within 2 km on Miami and nowhere near on the other recordings.

## What counts as "blind"? What is the "true" position?

Blind: the solver got only the audio, the UTC start time, a 40×40 km box that contains the camera but has a randomly offset centre, and ADS-B data logged around the box centre. The true positions were sealed by SHA-256 before solving, each case's runs were committed before its truth was opened, and the hashes match (N01–N03, N05).
The true position is not always owner-published coordinates. For Miami, Brighton and Southampton it is the map position of the building the owner's page names (a restaurant, an airport terminal, a restaurant), good to 15–40 m. Only the Midway cases have owner-published coordinates (N06).

## What is the shortest recording that works?

**Not measured.** We have not trimmed a recording to see where it stops working. Miami used a 66-minute recording and 74 aircraft. In colsto's challenge the two flyovers used each show a sliding tone for about two minutes (`results/colsto_dopploc/rank1.png`).
Length alone did not decide the Midway clips: the clean tone near each jet pass there is 5 to 8 seconds long (N47).

## How many planes do you need?

One plane leaves two places, one on each side of its path; nothing in the sound tells them apart. More planes on different lines pick one. Planes on the same line (arrivals and departures on one runway) do not.
Miami's answer sums 74 aircraft (N12, N14); colsto's challenge used two (N30). Error against the number of planes is **not measured**.

## Must the recording time be known, or can it be searched?

It has to be known to within a minute or so: flight data records where planes were at each second. The solvers scan a clock offset, which absorbs a stream delay or a small error in the start time (ridge v1.1: −10 to +60 s; v1.3: −10 to +90 s; doppler-locate's score: ±8 s).
On Miami the scan chose 38.5 s (N61). Searching over the time itself (try every hour of a day) is not part of any solver here.

## How was the camera's clock lined up with the flight data?

- **Webcam streams (A, B, C):** the recorder kept each HLS segment's `#EXT-X-PROGRAM-DATE-TIME`, a UTC stamp. The first segment's stamp is `utc_start` (N60).
- **Screen recordings (E, F, G):** the file's own start time as wall-clock UTC.
- **Either way the delay between the camera and the stamp is unknown, so the solver scans it** and keeps one value for all planes in a recording. For Miami that value was 38.5 s.

## Sound is slow. How do you handle that?

Sound travels about 343 m/s. A plane 2 km away is heard about 6 s late, and in 6 s a plane at about 100 m/s (360 km/h) has moved about 600 m. What you hear at time *t* left the plane earlier, from somewhere else.
The model works out, for each moment of audio, when the sound left the plane and where the plane was then, and compares that. The speed of sound comes from weather data in doppler-locate's search (NOAA GFS by default) and a fixed 340.3 m/s in the ridge solver (`solver_ridge/ridge.py`); the ground height comes from a terrain model.

## What if two planes overlap?

Each plane is a thin line that slides slowly, with copies at fixed ratios above it. Two planes at once show as two sets of lines. On a synthetic file with two overlapping planes the skill found both, flagged the overlap, and its safe copy took each tone down by 26.0 dB (N44). The ridge solver scores every aircraft separately and sums them, so overlap is not a special case there. We have no count of overlaps on real recordings.

## What did you do about gaps in the flight data?

- **Live webcams:** we polled adsb.lol every few seconds around the search box. About 45 % of the polls got HTTP 429 ("too many requests") and returned nothing (N62). The gaps are filled with each plane's full-day trace from the adsb.lol map, fetched once per plane at one request a second, merged per plane and de-duplicated by time; a track is split after 90 s without a position. Which planes to fetch was decided by "was inside the box's poll circle", never by distance to the camera.
- **colsto's challenge:** no live feed exists for 2026-09-08, so the adsb.lol day archive was used (ingest: 53.4 million points in 227 s; `docs/windows-notes.md`).
- Webcam error with live polls versus the day archive: **not measured**.

## How precise is it?

- **Miami:** 749 m from the reference point (±40 m), at best of two runs that gave a point (1,872 m and 749 m), with a near-tied runner-up 2.9 km away (N12, N13).
- **The other five blind recordings:** 7.0 to 27.6 km (N20–N24).
- **colsto's challenge:** a town match (Oyster Bay hamlet, 925 m from its centre) when doppler-locate's author measured the flyovers, and a miss (Connecticut, 137 km; the right pair 5th of 517) when doppler-locate's detector measured them. Both used a 300 km box chosen knowing the answer, and the camera's coordinates are unpublished, so neither is an error in metres (N30–N34).
- What limits it: timing (a few seconds of error is hundreds of metres for a plane at 100 m/s), the planes' reported positions (a fix every few seconds, interpolated), the weather, and how close the plane passed.

## Why do fixed outdoor cameras work best?

The method assumes the microphone stands still while the plane passes, so a moving camera breaks it, and indoors or behind noise suppression the steady tone is the first thing lost.
But fixed and outdoors is not enough. All our cameras are fixed and outdoors, and five of six recordings missed. What differed on the one that worked, from the files and not as a rule: Miami's strongest aircraft passed 2.1 km and 3.2 km away (N14), with a steady engine tone, over a full hour of busy traffic;
Midway's jets pass 90–150 m up, so the tone is short and weak under broadband roar; Southampton had very little traffic; Brighton had light aircraft and helicopters (N25).
One webcam we recorded had silent audio and was dropped for that reason; it was never packaged or solved, so it is not a hidden miss (N04).

## How do you break it?

With the safe copy (`flyover-check`): it finds each plane tone, cuts it out with a notch a few Hz wide that follows the sliding pitch (voices move too fast to be caught), cuts mains hum, strips GPS and creation-time tags, and checks the result again.
- **On the Miami audio:** the check found 15 tones (7 confident, 8 low-confidence). After three passes it cut 13; its own re-check still hears 2 (0:01–0:56 medium, 25:47 low), so it reports **NOT FULLY SAFE**. Energy removed 0.191 % (−27.2 dB), speech band changed 0.010 dB (N49, N50).
- **Whether the locator still finds Miami on that copy is not known yet:** the run is in progress (N51). We will report it whichever way it lands.
- Synthetic plane: **37.1 dB** quieter, second check clean, video stream byte-identical; synthetic jet fan-tone stack 56.1 dB (N41, N43). On the real Midway clip the check found 3 of about 5 jet passes and the copy re-checks clean, which means only that the detector hears nothing (N47, N48).
- `--decoy` replaces each tone with a fake one on a different Doppler curve; the check still hears a plane (N45).
- It does not cover the picture and signs, speech, wide-band jet noise, or anything a stronger method could dig out of faint leftovers. We have not run music or noise laid over the top.
