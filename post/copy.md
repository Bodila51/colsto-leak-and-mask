# copy.md — X post drafts (EN + RU), with the Miami blind result

Every number comes from a row in [`numbers.md`](numbers.md) (N12 = row N12). `[BRACKETS]` = no result yet.

## What the post rests on now

1. **Miami, blind: 749 m** (N12). One recording in six; the other five missed by 7 to 28 km (N24).
2. **The skill** (N40–N50): it finds the tones and cuts them, but on the same Miami audio its own re-check still hears 2 of 15 tones (NOT FULLY SAFE), and the locator on the copy is pending (N51).
3. **colsto's challenge**, both runs: a town match with the author's events, a 137 km miss with doppler-locate's detector (N30–N34).

### Your hook, judged again

> "Claude Code can now find the sound that leaks your exact location (and it's everywhere)", and "Claude Code can now find this sound in your video and remove it"

**Not true as written, so I did not use it.** Why:

- **"Claude Code can now find the sound"**: colsto found it. What Claude Code does here is run our skill and solver. Same family as the rule against "Claude Code discovered".
- **"Exact location"**: 749 m, on one blind recording of six (4 cameras). The other five missed by 7.0 to 27.6 km (N24). Miami's best and second-best peaks scored within 0.6 % of each other, 2.9 km apart (N13). The "truth" is a restaurant's map position, ±40 m (N06). colsto's challenge is a town match, and a miss when doppler-locate's detector measures the events (N30–N31).
- **"(and it's everywhere)"**: it needs an audible plane, the time and flight data. Light traffic (Southampton) and jets right over the camera (Midway) failed (N25).
- **"…and remove it"**: the skill cut 13 of 15 Miami tones; its re-check still hears 2, and whether the locator still finds the camera on the copy is not known yet (N50, N51).

**Closest true versions** (used below): 
- *Miami:* "A script matching passing planes' engine pitch to flight data put the camera 749 m from the spot its owner names. 1 hit in 6 blind recordings. The other 5 missed by 7-28 km."
- *Skill:* "Add one skill and Claude Code can check your video for the engine tone that lets a passing plane give away where you filmed, and cut it out. On 66 min of Miami audio it found 15 tones; after the cut its own re-check still hears 2."

### Stretches to watch

1. **"1 of 6" is recordings, not cameras**: A, B, C, plus three recordings (E, F, G) of the same Midway camera. Say "6 blind recordings from 4 cameras" or "1 in 6 recordings" (N04, N24).
2. **"749 m from the real camera"** is 749 m from a named building's map position (OSM, ±40 m): the owner's page says the camera is at the 94th Aero Squadron restaurant by runway 9/27. Not owner-published coordinates, which only the Midway cases have (N06). The drafts say "the spot its owner names". The README line about "the owner's own public page" needs the same fix.
3. **The near tie** (N13) must be somewhere the reader sees it: thread post 3 and reply C. A bare "749 m" would overstate how sure the solver was.
4. **Say all of A's runs**: detector-first found nothing; ridge v1 1,872 m; ridge v1.1 749 m. v1.1 was committed (17:18) before any Miami answer (17:38), and A's truth stayed sealed until all its runs were in (N02, N07). The solver changed across cases; every run is in the thread or the README table.
5. **"Blind"** means audio + UTC start + a 40 km box with a random centre + ADS-B (N05). Not "only the audio".
6. **Safe copy**: never write "safe", "removes it" or "hides the camera" for Miami. Re-check says NOT FULLY SAFE (N50). The locator on the copy is pending (N51); the gated variant V5 is for when it lands, whichever way.
7. **colsto**: "doppler-locate's detector", not "our detection" (N34). Never quote the town match without the miss. Both runs use a box chosen knowing the answer. The right pair came 5th of 517.
8. **The Miami video** (`post/miami_vertical.mp4`) is real data, but it shows one slide that is nearly flat (a tone at about 4,500 Hz, "pitch falling slowly"), titles it "The pitch slide matches a real aircraft", never shows the near-tied runner-up, and says "Add more aircraft" without saying 74 aircraft carry the answer (N17). Add one on-screen line for each. **The colsto video** was built before run 2 and does not say "events by doppler-locate's author", "box chosen knowing the answer", or "the automatic run missed" (N71). `post/still_0*.png` and `post_vertical.mp4` are retired.
9. **No generalisation**: Miami (jets, a few km away) worked; Midway (jets, 90–150 m up) did not; colsto's propeller planes gave a town match only with the author's events. "Propeller planes work best" and "jets overhead beat it" are not shown; the drafts say what each clip did.
10. **Credits**: colsto (method, challenge); digitalis-p/doppler-locate (engine; the colsto result is its pipeline); Paras Chopra (replication, `[LINK_PARAS]` missing); Bortlesboat (HLS + ADS-B idea). Say nothing about what colsto did or did not publish. Do not describe anyone's replication as unconvincing. The skill is **flyover-check**; the repo name has "colsto", the skill never.
11. RU drafts avoid gendered first-person verbs.

Length: every post fits 280 characters (a link counts as 23). Recount if you edit.

Recommended order: the thread first (it carries every caveat), then V1, then V2, V3 and V4 on later days. V5 only if N51 exists.

---

## V1 — The Miami blind run

**EN**

```
66 min of webcam audio, no picture. A script matching passing planes' engine pitch to flight data put the camera 749 m from the spot its owner names. Blind: truth sealed by hash first.

1 hit in 6 blind recordings. The other 5 missed by 7-28 km.

github.com/Bodila51/colsto-leak-and-mask
```

**RU**

```
66 минут звука вебкамеры, без картинки. Скрипт сопоставил тон пролетающих самолётов с данными о рейсах и поставил камеру в 749 м от места, названного владельцем.

Вслепую, истина запечатана хэшем: 1 попадание из 6 записей. Остальные 5 мимо на 7-28 км.

github.com/Bodila51/colsto-leak-and-mask
```

| Claim | Needs | File |
|---|---|---|
| 66 min, no picture; solver got audio, start time, box, flight data | N05, N15 | `cases/case_A/case.json` |
| 749 m from the spot its owner names | N12, N16, N06 | `results/case_A_evaluation.json`, `truth/case_A.json` |
| Blind, truth sealed by hash first | N01–N03 | `truth/SEALED.sha256`; commits `1000f05` → `38fb65e`, `7b03594` → `c103bf5` |
| 1 hit in 6 recordings; the other 5 missed by 7–28 km | N24 | `results/case_*_evaluation.json` |

Stretch watch: 1, 2, 3, 4, 5.

---

## V2 — The skill (the closest true version of your hook)

**EN**

```
Add one skill and Claude Code can check your video for the engine tone that lets a passing plane give away where you filmed, and cut it out.

On 66 min of Miami webcam audio it found 15 tones. After the cut, its own re-check still hears 2.

github.com/Bodila51/colsto-leak-and-mask
```

**RU**

```
Добавьте один скилл, и Claude Code проверит видео на тон двигателя, по которому пролетевший самолёт может выдать место съёмки, и вырежет его.

На 66 минутах звука майамской вебкамеры скилл нашёл 15 тонов. После выреза его же проверка всё ещё слышит 2.

github.com/Bodila51/colsto-leak-and-mask
```

| Claim | Needs | File |
|---|---|---|
| A passing plane "can" give away the place | method, N12 | `README.md` "What leaks, and why" |
| 15 tones found in 66 min; 2 still heard after the cut | N49, N50 | `results/case_A_safe/audio.safe-copy.json`, `safe_copy_report.json` |
| "Cut it out" | N41, N50 | `skills/flyover-check/TESTS.md` §2 (37.1 dB on a synthetic plane) |

Stretch watch: 6. Add the install line from reply B.

---

## V3 — All six, with the near tie

**EN**

```
I ran a plane-sound locator blind on 6 recordings from 4 public webcams. 1 hit: Miami, 749 m. 5 misses, 7-28 km: Brighton, Southampton, Midway x3.

Miami was a near tie: the runner-up peak, 2.9 km away, scored within 0.6%.

github.com/Bodila51/colsto-leak-and-mask
```

**RU**

```
Слепой прогон локатора по звуку самолётов: 6 записей с 4 публичных вебкамер. 1 попадание: Майами, 749 м. 5 промахов, 7-28 км: Брайтон, Саутгемптон, Midway x3.

Майами был почти ничьей: второй пик в 2,9 км набрал на 0,6% меньше.

github.com/Bodila51/colsto-leak-and-mask
```

| Claim | Needs | File |
|---|---|---|
| 6 recordings, 4 cameras; 1 hit; 5 misses at 7–28 km | N04, N20–N24 | `results/case_*_evaluation.json` |
| Runner-up 2.9 km away, 0.6 % lower | N13 | `results/case_A_ridge_v11/answer.json` → `runner_up` |

Stretch watch: 1, 4, 5.

---

## V4 — colsto's challenge, both runs

**EN**

```
colsto's challenge, two runs on my PC. Events from doppler-locate's author: Oyster Bay, 925 m from the hamlet centre. Events from its detector: Connecticut, 137 km off; the right pair came 5th of 517.

Both in a 300 km box chosen knowing the answer.

github.com/Bodila51/colsto-leak-and-mask
```

**RU**

```
Челлендж colsto, два прогона на моём ПК. События от автора doppler-locate: Oyster Bay, 925 м от центра посёлка. События от её детектора: Коннектикут, 137 км мимо; верная пара пятая из 517.

Оба в квадрате 300 км, выбранном при известном ответе.

github.com/Bodila51/colsto-leak-and-mask
```

| Claim | Needs | File |
|---|---|---|
| Run 1: Oyster Bay, 925 m from the centre, author's events | N30 | `results/colsto_dopploc/metrics.json` |
| Run 2: Connecticut, 137 km; right pair 5th of 517 | N31, N32 | `results/colsto_dopploc_e2e/metrics.json` |
| Box chosen knowing the answer | N34 | `metrics.json` → `regional_box_note` |

Stretch watch: 7.

---

## V5 — GATED: the Miami safe copy through the locator (only when N51 exists)

Write the result whichever way it lands. Fill `[MIAMI_SAFE_RESULT]` from `results/case_A_safe/ridge_v11/answer.json` (a point and its error, or "no point").

**EN**

```
Same locator, same box and start time, on the safe copy of the Miami audio: [MIAMI_SAFE_RESULT].

The skill's own re-check calls that copy NOT FULLY SAFE: 2 of the 15 tones are still audible.

github.com/Bodila51/colsto-leak-and-mask
```

**RU**

```
Тот же локатор, тот же квадрат и время старта, на безопасной копии майамского звука: [MIAMI_SAFE_RESULT].

Проверка самого скилла называет эту копию NOT FULLY SAFE: 2 из 15 тонов ещё слышны.

github.com/Bodila51/colsto-leak-and-mask
```

If the locator no longer finds Miami (error above 5 km, say, fixed before looking), this becomes the "found → safe copy → not found" post, with the NOT FULLY SAFE line kept. If it still finds Miami, post that.

---

## Replies under a single post (A: limits, B: credits + install, C: how sure Miami was)

**A — EN**

```
Limits: it needs an audible plane, the recording time and public flight data. Silent or heavily noise-suppressed audio defeats it; light traffic and jets 90-150 m overhead did too. One plane leaves two mirror places. The safe copy covers sound and file tags, not the picture.
```

**A — RU**

```
Ограничения: нужны слышимый самолёт, время записи и открытые данные о рейсах. Тихий звук и сильное шумоподавление ломают метод; редкий трафик и джеты в 90-150 м над камерой тоже. Один самолёт даёт два зеркальных места. Безопасная копия закрывает звук и теги, не картинку.
```

**B — EN**

```
Method: colsto (youtu.be/UytYrhYLOKI). Thanks to Paras Chopra for his replication [LINK_PARAS], digitalis-p/doppler-locate, Bortlesboat.

npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check
```

**B — RU**

```
Метод — colsto (youtu.be/UytYrhYLOKI). Спасибо Paras Chopra за повторение [LINK_PARAS], digitalis-p/doppler-locate, Bortlesboat.

npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check
```

**C — EN**

```
How sure was Miami? Not very: 74 aircraft add up to the answer, no single plane carries it, and the runner-up peak, 2.9 km away, scored within 0.6%. The reference point is a restaurant's map position, ±40 m. Earlier runs: 1,872 m, and nothing.
```

**C — RU**

```
Насколько уверен был Майами? Не очень: ответ складывается из 74 самолётов, ни один не тянет его в одиночку, а второй пик в 2,9 км набрал на 0,6% меньше. Опорная точка: положение ресторана на карте, ±40 м. Прежние прогоны: 1872 м и «ничего».
```

Needs: N13, N14, N16, N10, N11.

---

## Thread (6 posts)

Images: post 1 gets `post/miami_vertical.mp4` (after the on-screen fixes in stretch 8), post 3 `post/miami_still_04_found.png`, post 4 `results/colsto_dopploc_e2e/rank1.png` or the colsto stills with the caveats added.

### EN

**1/**
```
A plane flying past a webcam can give away where the camera is. colsto showed how.

I ran his method blind on 6 recordings from 4 public webcams. One landed 749 m from the camera. The other five missed by 7-28 km. What worked, what didn't:
```

**2/**
```
The idea: a passing engine's pitch slides (Doppler), and public flight data says which plane it was. The solver got only the audio, the UTC start and a 40 km box with a random centre. The true positions were sealed by SHA-256 before any solving.
```

**3/**
```
Miami (jets, 66 min): 749 m from the restaurant the owner names (map position, ±40 m). 74 aircraft add up to the answer; no single plane carries it. The runner-up peak, 2.9 km away, scored within 0.6%. Earlier runs: 1,872 m, and nothing found.
```

**4/**
```
Misses: Brighton 7.0 and 17.6 km, Southampton 20.6 km, Midway x3 21.7-27.6 km. Probably light traffic, or jets 90-150 m up that are mostly roar. colsto's challenge: a town match with the author's events, 137 km off with doppler-locate's detector. Box chosen knowing the answer.
```

**5/**
```
The skill checks a video for plane tones, mains hum, GPS and date tags, and cuts them out. Miami audio: 15 tones found, 2 still heard after the cut; speech band moved 0.010 dB. Synthetic plane: 37 dB quieter. Does the locator still find Miami on the copy? Not known yet.
```

**6/**
```
Check yours (own, public challenges, or owner's OK):
npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check

Needs an audible plane, the time, flight data.
Credit: colsto; doppler-locate; Paras Chopra [LINK_PARAS]; Bortlesboat.
github.com/Bodila51/colsto-leak-and-mask
```

### RU

**1/**
```
Пролетевший мимо вебкамеры самолёт может выдать, где камера. Показал это colsto.

Метод прогнан вслепую на 6 записях с 4 публичных вебкамер. Одна попала в 749 м от камеры. Остальные пять промахнулись на 7-28 км. Что вышло, а что нет:
```

**2/**
```
Идея: тон двигателя пролетающего самолёта съезжает (Доплер), а открытые данные о рейсах говорят, какой это самолёт. Решатель получил только звук, UTC-время старта и квадрат 40 км со случайным центром. Истинные позиции запечатаны SHA-256 до решения.
```

**3/**
```
Майами (джеты, 66 мин): 749 м от ресторана, который называет владелец (позиция на карте, ±40 м). Ответ складывается из 74 самолётов, ни один не тянет его в одиночку. Второй пик в 2,9 км набрал на 0,6% меньше. Прежние прогоны: 1872 м и «ничего не найдено».
```

**4/**
```
Промахи: Брайтон 7,0 и 17,6 км, Саутгемптон 20,6 км, Midway x3 21,7-27,6 км. Вероятно, редкий трафик или джеты в 90-150 м, где в основном рёв. Челлендж colsto: совпадение по городу с событиями автора, 137 км мимо с детектором doppler-locate. Квадрат выбран при известном ответе.
```

**5/**
```
Скилл проверяет видео на тоны самолётов, сетевой гул, GPS и дату и вырезает их. Майами: найдено 15 тонов, после выреза слышны 2; речевая полоса сдвинулась на 0,010 дБ. Синтетический самолёт: на 37 дБ тише. Находит ли локатор Майами на копии? Пока неизвестно.
```

**6/**
```
Проверьте (свои записи, публичные челленджи, с согласия владельца):
npx skills add Bodila51/colsto-leak-and-mask --skill flyover-check

Нужны слышимый самолёт, время, данные о рейсах.
Спасибо: colsto; doppler-locate; Paras Chopra [LINK_PARAS]; Bortlesboat.
```

### Thread claims → what proves them

| Post | Claim | Needs | File |
|---|---|---|---|
| 1 | 6 recordings, 4 cameras; 749 m; the other five missed by 7–28 km | N04, N12, N24 | `results/case_*_evaluation.json` |
| 2 | method; inputs; truth sealed before solving | N01–N03, N05 | `README.md` "How a blind run works"; `truth/SEALED.sha256` |
| 3 | 749 m from the owner-named restaurant (±40 m); 74 aircraft; near tie; earlier runs 1,872 m and nothing | N10–N14, N16 | `results/case_A_evaluation.json`, `results/case_A_ridge_v11/answer.json` |
| 4 | the misses; probable reasons; colsto runs; box | N20–N25, N30–N34 | `results/case_B/C/E/F/G_evaluation.json`, `results/colsto_dopploc*/metrics.json` |
| 5 | skill numbers; 15 found, 2 remain; 0.010 dB; 37 dB; locator on copy unknown | N41, N49–N51 | `results/case_A_safe/*`, `TESTS.md`. If N51 lands before posting, replace the last sentence with the result |
| 6 | install; needs; use rule; credits | N53 | `README.md`; add an install log |

---

## Images and alt text

**Real, ready (data in `post/anim_data_miami.json`, not a preview).** Apply stretch 8 first.

### Miami video (`post/miami_vertical.mp4`)

**EN**
> Vertical video, about 20 seconds, an excerpt of a 66-minute recording. A spectrogram of webcam audio, 3,000 to 6,000 Hz, shows a thin bright line near 4,500 Hz, an aircraft engine tone; a cyan dashed curve, the tone predicted for Embraer 175 ENY3714 (closest approach 2,070 m), follows it. A map of a 40 by 40 km box over Miami shows one flight path and a soft glow; with 74 aircraft the glow concentrates, and a pin "found" sits 749 metres from a star "real camera", the restaurant's map position.

**RU**
> Вертикальное видео, около 20 секунд, фрагмент записи в 66 минут. Спектрограмма звука вебкамеры, 3000-6000 Гц: тонкая яркая линия около 4500 Гц, тон двигателя самолёта; голубая пунктирная кривая, предсказанный тон для Embraer 175 ENY3714 (ближайшее расстояние 2070 м), идёт вдоль неё. Карта квадрата 40 на 40 км над Майами: одна трасса и мягкое свечение; при 74 самолётах свечение сжимается, метка «found» в 749 метрах от звезды «real camera», положения ресторана на карте.

### Still 1 — `post/miami_still_01_spectrogram.png` ("Look at the sound")

**EN**
> Spectrogram of 20 seconds of webcam audio, 3,000 to 6,000 Hz, time left to right. A thin bright line near 4,500 Hz is labelled "aircraft engine tone, pitch falling slowly". A white marker sits at 6.5 seconds. The broad glow below 3,500 Hz is background noise.

**RU**
> Спектрограмма 20 секунд звука вебкамеры, 3000-6000 Гц, время слева направо. Тонкая яркая линия около 4500 Гц с подписью «aircraft engine tone, pitch falling slowly». Белая отметка на 6,5 секунде. Широкое свечение ниже 3500 Гц, фоновый шум.

### Still 2 — `post/miami_still_02_match.png` ("The pitch slide matches a real aircraft")

**EN**
> The same spectrogram. A cyan dashed curve, the tone predicted for the matched aircraft, wobbles around the bright line near 4,500 Hz and ends just below it. A badge reads "Matched aircraft, Embraer 175, ENY3714, closest approach 2,070 m".

**RU**
> Та же спектрограмма. Голубая пунктирная кривая, предсказанный тон подобранного самолёта, колеблется вокруг яркой линии около 4500 Гц и в конце уходит чуть ниже. Плашка: «Matched aircraft, Embraer 175, ENY3714, closest approach 2,070 m».

### Still 3 — `post/miami_still_03_one_aircraft.png` ("Where could the microphone be?")

**EN**
> A map of a 40 by 40 km box over Miami with white flight paths and a soft blue glow west of the centre, near Doral and Hialeah. Text: "1 aircraft, 2 possible places. Distance known, side unknown. Closest approach 2,070 m".

**RU**
> Карта квадрата 40 на 40 км над Майами: белые трассы и мягкое голубое свечение западнее центра, у Дорала и Хайалиа. Текст: «1 aircraft, 2 possible places. Distance known, side unknown. Closest approach 2,070 m».

### Still 4 — `post/miami_still_04_found.png` ("Add more aircraft")

**EN**
> A zoomed map, scale bar 1 km. A bright blue glow, a pin labelled "found" about three-quarters of a kilometre west of a star labelled "real camera", joined by a dashed line, with flight paths above. Text: "found blind: 749 m from the real camera. 40×40 km search box".

**RU**
> Карта крупным планом, масштаб 1 км. Яркое голубое свечение, метка «found» примерно в трёх четвертях километра западнее звезды «real camera», между ними пунктир, выше трассы. Текст: «found blind: 749 m from the real camera. 40×40 km search box».

### colsto results, with caveats (the stills as built lack them)

`post/colsto_still_02_match.png` — **EN alt:** "Spectrogram of colsto's challenge audio, 80 to 450 Hz. Three cyan dashed curves, marked 1x, 2x and 3x, lie on a tone sliding from about 125 to 105 Hz and its copies; a flat tone near 180 Hz is not matched. Badge: Pilatus PC-12 N22NY, closest approach 6,129 m."

`post/colsto_still_04_found.png` — **EN alt:** "A map of Oyster Bay with the hamlet outline dashed and a pin "found" inside it. Text: found, inside Oyster Bay (colsto's answer), camera coordinates not published, town match." **Add on screen or in the post:** events by doppler-locate's author; box chosen knowing the answer; the automatic run missed (N34, N71).

`results/colsto_dopploc_e2e/rank1.png`, `scatter.png`: describe from the image after viewing it (not described here).

---

## Before you post

1. Pick the thread, or V1 with replies A, B, C. V2–V4 later. V5 only when N51 exists.
2. Every number matches its row in `numbers.md`. Fill `[LINK_PARAS]`; nothing else in brackets should be left in a post.
3. Fix the README: "location from the owner's own public page" (true for E, F, G; A, B, C are named buildings' map positions), "1 of 6 cameras" (recordings), and the safe-copy line "It sounds the same to a listener" (a goal; the measured speech-band change on Miami is 0.010 dB).
4. Add on-screen lines to the Miami video (stretch 8) and the caveats to the colsto stills.
5. Add an install log (N53).
