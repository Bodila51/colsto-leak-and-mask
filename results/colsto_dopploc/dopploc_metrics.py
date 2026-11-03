#!/usr/bin/env python
"""Collect the result of doppler-locate's own pipeline (dopploc search + score) into results/colsto_dopploc.

    dopploc_metrics.py OUT_DIR(vendor out/colsto_dopploc) RESULT_DIR SEARCH_METER.json SCORE_METER.json
"""
import json
import shutil
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent))
from make_metrics import dist_m, dist_to_ring_m, poly_centroid, poly_contains  # noqa: E402

out, res, m_search, m_score = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
res.mkdir(parents=True, exist_ok=True)
ADSB = Path("data/vendor/doppler-locate/data/adsb/2026-09-08")
best = pl.read_csv(out / "best_per_pair.csv")
feat = json.loads((res / "oyster_bay_hamlet.geojson").read_text())
coords = feat["geometry"]["coordinates"]
clat, clon = poly_centroid(coords[0])
town_p = res / "town_of_oyster_bay.geojson"
town = json.loads(town_p.read_text())["geometry"]["coordinates"] if town_p.exists() else None
ac = pl.read_parquet(ADSB / "aircraft.parquet")


def flight_info(fid):
    icao, t = fid.split(":")
    r = ac.filter(pl.col("icao") == icao)
    typ = r["type"][0] if len(r) else None
    reg = r["reg"][0] if len(r) and "reg" in r.columns else None
    cs = (pl.scan_parquet(str(ADSB / "points-*.parquet")).filter(pl.col("icao") == icao)
          .filter(pl.col("t").is_between(float(t) - 3600, float(t) + 14400)).select("callsign").collect())
    names = [c for c in cs["callsign"].to_list() if c]
    cs1 = max(set(names), key=names.count) if names else None
    return dict(fid=fid, icao=icao, type=typ, reg=reg, callsign=cs1)


def row_info(i, r):
    lat, lon = r["lat"], r["lon"]
    inside = poly_contains(coords, lon, lat)
    d = dict(rank=i + 1, lat=lat, lon=lon, score=round(r["score"], 4), S1=round(r["S1"], 4), S2=round(r["S2"], 4),
             event1_flight=flight_info(r["f1"]), event2_flight=flight_info(r["f2"]),
             t1m_utc_delayed_cpa=r["t1m"], t2m_utc_delayed_cpa=r["t2m"], d1m=round(r["d1m"]), d2m=round(r["d2m"]),
             clock_shift_delta_s=r["delta"], f0_event1_hz=r["f01"], f0_event2_hz=r["f02"],
             inside_osm_hamlet_boundary=bool(inside),
             distance_to_hamlet_centroid_m=round(dist_m(lat, lon, clat, clon), 1),
             distance_to_hamlet_boundary_m=0.0 if inside else round(dist_to_ring_m(coords[0], lat, lon), 1))
    if town:
        d["inside_town_of_oyster_bay_municipality"] = bool(poly_contains(town, lon, lat))
    return d


top = [row_info(i, r) for i, r in enumerate(best.head(5).iter_rows(named=True))]
ms, mc = json.loads(m_search.read_text()), json.loads(m_score.read_text())
counts = json.loads((out / "search_counts.json").read_text())
scored = pl.read_parquet(out / "scored.parquet")
# best point per flight pair: how many pairs, how high does the nearest-to-hamlet pair rank
best_d = [dist_m(r["lat"], r["lon"], clat, clon) for r in best.iter_rows(named=True)]
inside_ranks = [i + 1 for i, r in enumerate(best.iter_rows(named=True)) if poly_contains(coords, r["lon"], r["lat"])]
answer = dict(
    case="colsto-dopploc-NYC300", method="doppler-locate's own pipeline: dopploc search + dopploc score (cases/oyster.toml events)",
    rank1=top[0], top5=top, n_flight_pairs=len(best), n_scored_points=len(scored),
    ranks_of_pairs_whose_best_point_is_inside_hamlet=inside_ranks[:20],
    closest_pair_to_hamlet_centroid=dict(rank=int(min(range(len(best_d)), key=best_d.__getitem__)) + 1, distance_m=round(min(best_d), 1)),
)
(res / "answer.json").write_text(json.dumps(answer, indent=1))
metrics = dict(
    case="colsto-dopploc-NYC300", pipeline="doppler-locate (MIT) documented flow: dopploc search, dopploc score; events as measured in its cases/oyster.toml",
    regional_box_note="300 x 300 km regional box centred on New York City (40.71, -74.01); not the continental US and not blind: the box was chosen knowing the answer is on Long Island.",
    camera_coordinates="camera coordinates not published; only the town (hamlet Oyster Bay, NY) is known",
    rank1_point=dict(lat=top[0]["lat"], lon=top[0]["lon"]),
    town_match_inside_osm_hamlet_boundary=top[0]["inside_osm_hamlet_boundary"],
    distance_to_hamlet_centroid_m=top[0]["distance_to_hamlet_centroid_m"],
    distance_to_hamlet_boundary_m=top[0]["distance_to_hamlet_boundary_m"],
    inside_town_of_oyster_bay_municipality=top[0].get("inside_town_of_oyster_bay_municipality"),
    hamlet=dict(name="Oyster Bay, NY (hamlet / census place)", osm="relation 174432 (Nominatim polygon_geojson=1)",
                centroid_lat=round(clat, 6), centroid_lon=round(clon, 6), polygon_file="oyster_bay_hamlet.geojson"),
    rank1_score=top[0]["score"], rank2_score=top[1]["score"] if len(top) > 1 else None,
    flights_used=dict(event1=top[0]["event1_flight"], event2=top[0]["event2_flight"]),
    n_flight_pairs=len(best), n_scored_points=len(scored), search_counts=counts,
    runtime_s=dict(search=round(ms["wall_s"], 1), score=round(mc["wall_s"], 1), total=round(ms["wall_s"] + mc["wall_s"], 1)),
    peak_private_gb=dict(search=ms["peak_private_gb"], score=mc["peak_private_gb"]),
    settings="[search] bbox -75.789,39.361,-72.231,42.059, gpu=false, workers=3, offset_tol_s=8; [score] dt_sweep -8..8 step 0.1; "
             "atmosphere gfs (default); numba CPU backend; BelowNormal priority; another solver was running at the same time",
    osm_attribution="Hamlet and town polygons (c) OpenStreetMap contributors, ODbL, via Nominatim.",
)
(res / "metrics.json").write_text(json.dumps(metrics, indent=1))
# files
best.head(20).write_csv(res / "best_per_pair_top20.csv")
for f in ("summary.md", "scatter.png", "case.toml", "search_counts.json"):
    if (out / f).exists():
        shutil.copy(out / f, res / f)
for i in range(1, 6):
    if (out / f"rank{i}.png").exists():
        shutil.copy(out / f"rank{i}.png", res / f"rank{i}.png")
for nm, p in (("search", "data/logs/dopploc_search.log"), ("score", "data/logs/dopploc_score.log")):
    shutil.copy(p, res / f"{nm}.log")
print(json.dumps({k: metrics[k] for k in ("rank1_point", "town_match_inside_osm_hamlet_boundary", "distance_to_hamlet_centroid_m",
                                           "rank1_score", "rank2_score", "runtime_s")}))
print(json.dumps(metrics["flights_used"]))
