# colsto-leak-and-mask

A plane flying past a microphone can give away where the recording was made. This repo shows it with blind runs on public webcams and on colsto's public challenge. It also gives you a Claude Code skill that checks your own videos before you post them and makes a safe copy.

The method is colsto's ([video](https://www.youtube.com/watch?v=UytYrhYLOKI)). This repo is an independent replication and is not affiliated with colsto, who released no code.

> Status: work in progress. Results are added as runs finish. Every number below comes from a run in this repo, with the file it came from.

## What leaks, and why

1. A propeller plane makes a steady hum, roughly 70-300 Hz, plus harmonics.
2. As the plane passes, that hum slides down in pitch (the Doppler effect). The shape of the slide gives the plane's speed, how close it came and the second it was closest.
3. Public flight-tracking data (ADS-B) records where every transponder-equipped plane was, every second.
4. Find the place on the ground where that plane, at that moment, would have produced exactly that slide. That is where the microphone was.
5. One plane fits equally well on both sides of its path, so you get two bands. A second plane on a different line picks one.

```
   pitch                              flight path (ADS-B)
   120 Hz ─────╮                      ─────────●──────────►
               ╰──╮                            ¦  closest approach
                  ╰────── 90 Hz        mic? ×  ¦  × mic?   (mirror pair)
   ─────────────────────► time                 ¦
```

## Results

| Case | Search area | Planes used | True location | Found | Error | Flights |
|---|---|---|---|---|---|---|
| _runs in progress_ | | | | | | |

How the blind runs work, so you don't have to trust us:

1. Before any solving, a separate agent wrote each camera's location, from the owner's own public page, into a sealed file. Only its SHA-256 was committed ([`truth/SEALED.sha256`](truth/SEALED.sha256)).
2. The solver got only anonymous audio, the UTC start time from the stream and a 40×40 km box. The box contains the camera but its centre was offset at random. ADS-B was logged around the box centre, not the camera.
3. The solver code was committed before any case was opened. Its answer is the computed best point, committed before the truth was revealed.
4. Then the truth file was committed. Anyone can check that its hash matches the seal, and that the commit order is seal → answer → truth.

## Try it on your own video

```bash
npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check
```

Then, in Claude Code:

- "check my video `clip.mp4`": tells you in plain words whether planes, mains hum or file metadata could place the recording.
- "make a safe copy of `clip.mp4`": removes only the narrow plane tones and mains hum, strips GPS and date tags, and checks again. It sounds the same to a listener.
- "find where `my_recording.mp4` was made": only for your own recordings, public challenges, or recordings whose owner agreed. It needs the date and time and a country or area.

## Limits

- It needs an audible plane, the date and time of the recording, and public flight data for that moment.
- It doesn't work on silent or heavily noise-suppressed audio, on planes missing from ADS-B, or in places with no traffic overhead.
- adsb.lol history starts in 2023. A day's archive is published the morning after (about 03:30 UTC).
- Propeller planes work best. Jets mostly only work near airports, where they are low and close.
- Planes on one line (for example, arrivals and departures on one runway) leave the two-band ambiguity in place.
- The picture can still give a place away (signs, buildings). The safe copy only covers sound and file metadata.

## Responsible use

Use this on your own recordings, on public challenges like colsto's, and on public webcams whose owners publish their location. Don't use it to find a person. The skill's "find" mode refuses other recordings. The point of publishing it is the check and the safe copy: people should know what their videos give away and be able to remove it.

## Credits

- **colsto**, for the method and the challenge ([video](https://www.youtube.com/watch?v=UytYrhYLOKI), [raw audio](https://youtu.be/4CxVxuMVkMc)).
- [digitalis-p/doppler-locate](https://github.com/digitalis-p/doppler-locate) (MIT): the location engine used here.
- [Bortlesboat/aircraft-doppler-realworld-test](https://github.com/Bortlesboat/aircraft-doppler-realworld-test) (MIT): the idea of recording YouTube live HLS with `#EXT-X-PROGRAM-DATE-TIME` while logging ADS-B.
- Also see [mojomast/hermes-aircraft-doppler-geolocation](https://github.com/mojomast/hermes-aircraft-doppler-geolocation), [1337isnot1337/DOPPLERGEIST](https://github.com/1337isnot1337/DOPPLERGEIST), [Espaye/aircraft-doppler](https://github.com/Espaye/aircraft-doppler), [Goprogabriel/echotrace](https://github.com/Goprogabriel/echotrace).
- Data: [adsb.lol](https://www.adsb.lol/) (ODbL 1.0), Copernicus DEM GLO-30 ("produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"), NOAA GFS.

## Licence

MIT, see [LICENSE](LICENSE).
