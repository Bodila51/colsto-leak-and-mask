#!/usr/bin/env python
"""Write post/anim_data_miami.json: the blind case_A result (Miami airport webcam, ridge solver v1.1) in the format of DATA.md.

    data\\vendor\\doppler-locate\\.venv\\Scripts\\python.exe post/anim/miami_data.py

Everything written comes from these files:
  results/case_A_ridge_v11/answer.json   found point, latency, aircraft (type, closest slant distance)
  results/case_A_ridge_v11/curves.json   the solver's predicted curve of the top aircraft (case audio s = clip s, the clip starts at 0)
  results/case_A_ridge_v11/heat_*.npy    + grid.json: heat_1 = top aircraft alone, heat_all = all aircraft (max over latency)
  results/case_A_evaluation.json         the real camera (owner-published, revealed after the answer) and the error
  cases/case_A/case.json, adsb.jsonl     utc_start, search box, the ADS-B positions of the two aircraft drawn on the map
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "results" / "case_A_ridge_v11"
CASE = ROOT / "cases" / "case_A"
OUT = ROOT / "post" / "anim_data_miami.json"
# 20 s of the top aircraft's first pass (curves.json: 0.1-69.9 s). Its closest approach is 1.6 s into the case audio, but there
# its tone does not stand out (whitened power on the predicted curve = the median of shifted copies); at 50-70 s it does
# (8.3 dB on the curve vs 2.0 dB for the best copy shifted by 2-8 %), so the clip is that part of the same pass.
CLIP = (50.0, 70.0)
TIP_S = 7.5             # the "aircraft engine tone" label points at the curve here (the line is visible from about clip 6 s)
TYPES = {"E75L": "Embraer 175", "B38M": "Boeing 737 MAX 8", "B738": "Boeing 737-800"}


def hav(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def positions(hexes):
    """ADS-B positions per hex: (unix time of the position, lat, lon), sorted, duplicates dropped."""
    out = {h: {} for h in hexes}
    with open(CASE / "adsb.jsonl", encoding="utf-8") as f:
        for line in f:
            if not any(h in line for h in hexes):
                continue
            r = json.loads(line)
            for ac in r.get("ac", []):
                if ac.get("hex") in out and ac.get("lat") is not None:
                    t = round(r["t"] - (ac.get("seen_pos") or 0.0), 1)
                    out[ac["hex"]][t] = (ac["lat"], ac["lon"])
    return {h: sorted((t, la, lo) for t, (la, lo) in d.items()) for h, d in out.items()}


def main():
    ans = json.loads((RES / "answer.json").read_text(encoding="utf-8"))
    ev = json.loads((ROOT / "results" / "case_A_evaluation.json").read_text(encoding="utf-8"))
    run = next(r for r in ev["runs"] if r["answer"].endswith("case_A_ridge_v11/answer.json"))
    case = json.loads((CASE / "case.json").read_text(encoding="utf-8"))
    cj = json.loads((RES / "curves.json").read_text(encoding="utf-8"))
    grid = json.loads((RES / "grid.json").read_text(encoding="utf-8"))
    assert [ans["lat"], ans["lon"]] == run["found"] and ev["truth_hash_ok"]

    # predicted curve of the top aircraft inside the clip (curves.json holds two passes of it; keep the one at the clip)
    c0 = cj["curves"][0]
    assert c0["callsign"] == ans["aircraft"][0]["callsign"]
    t = np.array(c0["t_s"])
    curves = []
    for h, f in c0["f_hz"].items():
        f = np.array(f)
        m = (t >= CLIP[0] - 0.5) & (t <= CLIP[1] + 0.5)
        curves.append(dict(harmonic=int(h[1:]), t_s=np.round(t[m] - CLIP[0], 3).tolist(), f_hz=f[m].tolist()))

    # the two aircraft: badge = top aircraft, second track = the next one in answer.json
    top2 = ans["aircraft"][:2]
    pos = positions([a["hex"] for a in top2])
    from datetime import datetime, timezone
    t0 = datetime.fromisoformat(case["utc_start"].replace("Z", "+00:00")).timestamp()
    utc0 = datetime.fromtimestamp(t0 + CLIP[0], timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    s, w, n, e = case["bbox"]
    aircraft = []
    for k, a in enumerate(top2):
        tc = t0 + a["cpa_audio_s"] - ans["latency_s"]           # real UTC of the closest approach
        pts = [(la, lo) for tt, la, lo in pos[a["hex"]] if abs(tt - tc) < 420 and s - .05 < la < n + .05 and w - .05 < lo < e + .05]
        aircraft.append(dict(id="AB"[k], label=f"{TYPES.get(a['type'], a['type'])} · {a['callsign']}", closest_m=int(round(a["closest_slant_m"])),
                             hex=a["hex"], cpa_audio_s=a["cpa_audio_s"],
                             track=dict(lat=[round(p[0], 5) for p in pts], lon=[round(p[1], 5) for p in pts])))

    def heat(name):
        h = np.load(RES / f"{name}.npy")
        assert h.shape == (len(grid["lat"]), len(grid["lon"]))       # heat[i_lat, i_lon]
        return [[None if not np.isfinite(v) else round(float(v), 4) for v in r] for r in h]

    tlat, tlon = ev["truth"]
    err = hav(ans["lat"], ans["lon"], tlat, tlon)
    assert abs(err - run["error_m"]) < 5, (err, run["error_m"])
    km = round(hav(s, w, s, e) / 1000), round(hav(s, w, n, w) / 1000)
    data = {
        "preview": False,
        "source": "results/case_A_ridge_v11 (ridge solver v1.1, blind) and results/case_A_evaluation.json",
        "audio": {"path": "cases/case_A/audio.flac", "start_s": CLIP[0]},
        "clip": {"utc_start": utc0, "latency_s": ans["latency_s"]},
        "safe_audio": None,
        "curves": curves,
        "aircraft": aircraft,
        "map": {
            "bbox": case["bbox"],
            "lat": grid["lat"], "lon": grid["lon"],
            "heat_1": heat("heat_1"), "heat_all": heat("heat_all"),
            "heat_note": f"heat_1: {grid['heat_1']}; heat_all: {grid['heat_all']} ({ans['n_candidates']} candidate aircraft, "
                         f"{len(ans['aircraft'])} contribute at the answer)",
            "planes_used": len(ans["aircraft"]),
            "found": {"lat": ans["lat"], "lon": ans["lon"]},
            "true": {"lat": tlat, "lon": tlon, "source": ev["camera"] + ", owner-published, revealed after the answer"},
            "error_m": run["error_m"],
            "found_label": "found blind:",
            "note": f"{km[0]}×{km[1]} km search box",
            "gamma_1": 1.3, "gamma_2": 6.0,
        },
        "spectrogram": {"f_min_hz": 3000.0, "f_max_hz": ans["params"]["fmax"], "window_s": 0.5, "view_s": 20},
        "clip_note": f"case audio {CLIP[0]:.0f}-{CLIP[1]:.0f} s: the top aircraft's first pass, where its tone stands out",
        "callout": {"tip_t_s": TIP_S, "tip_f_hz": round(float(np.interp(TIP_S, curves[0]["t_s"], curves[0]["f_hz"])), 1),
                    "from_s": 3.0, "to_s": 7.6, "sub": "pitch falling slowly"},
        "credits": "engine: our ridge solver v1.1 · data: adsb.lol (ODbL) · map © OpenStreetMap contributors",
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: {len(curves)} curve(s) {curves[0]['f_hz'][0]}->{curves[0]['f_hz'][-1]} Hz, "
          f"tracks {[len(a['track']['lat']) for a in aircraft]}, error {err:.0f} m (evaluation {run['error_m']}), box {km}")


if __name__ == "__main__":
    main()
