#!/usr/bin/env python
"""Write post/anim_data_colsto.json: the colsto challenge result (results/colsto_dopploc) in the format of DATA.md.

    data\\vendor\\doppler-locate\\.venv\\Scripts\\python.exe post/anim/colsto_data.py

Needs doppler-locate's venv (polars, dopploc) and its outputs in data/vendor/doppler-locate/out/colsto_dopploc:
best_per_pair.csv (rank 1) and heatmap_rank1.parquet, written by

    cd data/vendor/doppler-locate
    .venv/Scripts/dopploc.exe heatmap out/colsto_dopploc/case.toml --rank 1 --half 6000 --step 200

Everything written comes from those files, the ADS-B day the engine used and the hamlet polygon in results/colsto_dopploc:
  curves     doppler-locate's own model curves for event 1 at the rank-1 point (report.overlay_data, the curves of rank1.png)
  aircraft   the two matched flights (ADS-B tracks as the engine reconstructed them), closest distance d1m / d2m
  heat_1     S1 (event-1 score alone) on the heatmap grid, heat_2 = S1 + S2 (both events)
  found      the rank-1 point; no camera point exists, only the hamlet polygon (map.area)
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import polars as pl
from scipy.interpolate import griddata

ROOT = Path(__file__).resolve().parents[2]
DL = ROOT / "data" / "vendor" / "doppler-locate"
RES = ROOT / "results" / "colsto_dopploc"
OUT = ROOT / "post" / "anim_data_colsto.json"
AUDIO = "data/colsto/recording_4CxVxuMVkMc.webm"
BBOX = [40.72, -73.80, 41.02, -73.25]       # western Long Island: Port Washington to Huntington, the Sound to the north
CLIP_LEAD_S = 10.0                          # event-1 CPA (as fitted) sits this far into the 20 s clip
TYPES = {"PC12": "Pilatus PC-12", "B350": "King Air 350"}


def main():
    os.chdir(DL)                             # the case file uses root = "."
    from dopploc import config, report, score
    case = config.load(DL / "out" / "colsto_dopploc" / "case.toml")
    row = pl.read_csv(case.out / "best_per_pair.csv").row(0, named=True)
    ans = json.loads((RES / "answer.json").read_text(encoding="utf-8"))["rank1"]
    assert abs(row["lat"] - ans["lat"]) < 1e-9 and abs(row["lon"] - ans["lon"]) < 1e-9, "rank 1 differs from answer.json"

    # clip: audio time of the fitted event-1 CPA is audio_cpa + delta (report.overlay_data); its UTC is t1m
    a0 = case.events[0].audio_cpa + row["delta"]
    start = round(a0 - CLIP_LEAD_S, 1)
    utc0 = row["t1m"] + (start - a0)
    curves = []
    ov = report.overlay_data(case, row)[0]
    for h, (t, f) in zip(case.events[0].harmonics, ov["curves"]):
        m = (t >= start - 0.5) & (t <= start + 20.5)
        tt, ff = t[m] - start, f[m]
        k = np.arange(0, len(tt), max(1, int(round(0.1 / np.median(np.diff(tt))))))
        curves.append(dict(harmonic=int(h), t_s=np.round(tt[k], 3).tolist(), f_hz=np.round(ff[k], 2).tolist()))

    # tracks: the engine's own reconstruction of the two flights, inside the map box (+ margin)
    flights = score.load_flights(case, [row["f1"], row["f2"]], row["t1"] - 900, row["t2"] + 900, log=lambda *_: None)
    meta = ans
    aircraft = []
    for k, (fid, d, info, tc) in enumerate(((row["f1"], row["d1m"], meta["event1_flight"], row["t1m"]),
                                            (row["f2"], row["d2m"], meta["event2_flight"], row["t2m"]))):
        sm = flights[fid]
        s, w, n, e = BBOX
        mg = 0.08
        m = (sm.lat > s - mg) & (sm.lat < n + mg) & (sm.lon > w - mg) & (sm.lon < e + mg) & (np.abs(sm.t - tc) < 900)
        ii = np.flatnonzero(m)[::4]
        aircraft.append(dict(id="AB"[k], label=f"{TYPES.get(info['type'], info['type'])} · {info['reg']}", closest_m=int(round(d)),
                             fid=fid, callsign=info["callsign"],
                             track=dict(lat=np.round(sm.lat[ii], 5).tolist(), lon=np.round(sm.lon[ii], 5).tolist())))

    # score maps: doppler-locate heatmap around rank 1, resampled onto a regular lat/lon grid
    hm = pl.read_parquet(case.out / "heatmap_rank1.parquet").filter(pl.col("S1").is_finite())
    lon, lat = hm["lon"].to_numpy(), hm["lat"].to_numpy()
    n_ax = 81
    la = np.linspace(lat.min(), lat.max(), n_ax)
    lo = np.linspace(lon.min(), lon.max(), n_ax)
    LO, LA = np.meshgrid(lo, la)

    def grid(v):
        g = griddata((lon, lat), v, (LO, LA), method="linear")
        return [[None if not np.isfinite(x) else round(float(x), 4) for x in r] for r in g]
    s1 = hm["S1"].to_numpy()
    s2 = hm["S2"].to_numpy()
    s2 = np.where(np.isfinite(s2), s2, 0.0)
    pk = hm.sort("score", descending=True).row(0, named=True)

    g = json.loads((RES / "oyster_bay_hamlet.geojson").read_text(encoding="utf-8"))
    ring = [[round(b, 6), round(a, 6)] for a, b in g["geometry"]["coordinates"][0]]
    cen = json.loads((RES / "metrics.json").read_text(encoding="utf-8"))["hamlet"]

    data = {
        "preview": False,
        "source": "results/colsto_dopploc (doppler-locate search + score, rank 1) and its dopploc heatmap around rank 1",
        "audio": {"path": AUDIO, "start_s": start},
        "clip": {"utc_start": dt.datetime.fromtimestamp(utc0, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                 "latency_s": 0.0, "note": "UTC from the engine's fit: t1m at audio_cpa + delta"},
        "safe_audio": None,
        "curves": curves,
        "aircraft": aircraft,
        "map": {
            "bbox": BBOX,
            "lat": np.round(la, 6).tolist(), "lon": np.round(lo, 6).tolist(),
            "heat_1": grid(s1), "heat_2": grid(s1 + s2),
            "heat_note": f"dopploc heatmap --rank 1 --half 6000 --step 200 ({len(hm)} points): heat_1 = S1, heat_2 = S1 + S2; "
                         f"peak {pk['lat']:.5f}, {pk['lon']:.5f} score {pk['score']:.3f}",
            "planes_used": 2,
            "found": {"lat": row["lat"], "lon": row["lon"], "score": round(row["score"], 4)},
            "area": {"name": "Oyster Bay", "rings": [ring], "center": {"lat": cen["centroid_lat"], "lon": cen["centroid_lon"]},
                     "found_text": "inside Oyster Bay", "sub": "(colsto's answer)",
                     "map_label": "Oyster Bay (colsto's answer)", "source": "OSM relation 174432 via Nominatim (ODbL)"},
            "zoom_half_m": 3000,
            "note": "camera coordinates not published: town match",
            "gamma_1": 1.6, "gamma_2": 2.6,
        },
        "spectrogram": {"f_min_hz": 50, "f_max_hz": 450, "window_s": 1.0, "view_s": 12},
        "callout": {"curve": 0},             # the 1st harmonic is the strongest line in this clip
        "second_event_note": f"event 2 is {case.events[1].audio_cpa - case.events[0].audio_cpa:.0f} s later in the recording, not in this clip",
        "credits": "challenge audio: colsto · engine: doppler-locate (MIT) · data: adsb.lol (ODbL) · map © OpenStreetMap contributors",
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: clip audio {start} s, utc {data['clip']['utc_start']}, {len(curves)} curves, heat {n_ax}x{n_ax}, "
          f"peak {pk['lat']:.5f},{pk['lon']:.5f}")


if __name__ == "__main__":
    main()
