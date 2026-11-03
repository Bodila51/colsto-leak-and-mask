#!/usr/bin/env python
"""Metrics and an error map for a ridge.py result directory.

    make_metrics.py synth  RESULT_DIR TRUTH_JSON INJECTED_LATENCY_S [--extra JSON]
    make_metrics.py colsto RESULT_DIR [--extra JSON]

Reads RESULT_DIR/answer.json, grid.json and heat_all.npy written by solver_ridge/ridge.py and writes
metrics.json and map_*.png next to them. Distances are WGS-84 geodesics (pyproj).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from pyproj import Geod

GEOD = Geod(ellps="WGS84")


def dist_m(lat1, lon1, lat2, lon2):
    return float(GEOD.inv(lon1, lat1, lon2, lat2)[2])


def ring_contains(ring, lon, lat):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def poly_contains(coords, lon, lat):
    if not ring_contains(coords[0], lon, lat):
        return False
    return not any(ring_contains(h, lon, lat) for h in coords[1:])


def poly_centroid(ring):
    """Area-weighted centroid in a local equirectangular plane around the ring's mean."""
    a = np.array(ring, float)
    lat0 = a[:, 1].mean()
    lon0 = a[:, 0].mean()
    k = math.cos(math.radians(lat0))
    x = (a[:, 0] - lon0) * k
    y = a[:, 1] - lat0
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cr = x * y1 - x1 * y
    A = cr.sum() / 2
    cx = ((x + x1) * cr).sum() / (6 * A)
    cy = ((y + y1) * cr).sum() / (6 * A)
    return lat0 + cy, lon0 + cx / k


def dist_to_ring_m(ring, lat, lon):
    """Shortest distance (m) from a point to a polygon ring (planar, local metres)."""
    a = np.array(ring, float)
    k = math.cos(math.radians(lat))
    x = (a[:, 0] - lon) * 111320.0 * k
    y = (a[:, 1] - lat) * 110574.0
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    dx, dy = x1 - x, y1 - y
    L2 = dx * dx + dy * dy + 1e-12
    t = np.clip(-(x * dx + y * dy) / L2, 0, 1)
    return float(np.min(np.hypot(x + t * dx, y + t * dy)))


def load_heat(rd):
    g = json.loads((rd / "grid.json").read_text())
    H = np.load(rd / "heat_all.npy")
    return np.array(g["lat"]), np.array(g["lon"]), H


def synth(rd, truth_path, lat_inj, extra):
    ans = json.loads((rd / "answer.json").read_text())
    tr = json.loads(Path(truth_path).read_text())
    err = dist_m(tr["lat"], tr["lon"], ans["lat"], ans["lon"])
    used = [a["hex"] for a in ans.get("aircraft", [])]
    true_hex = [tr["event1"].split(":")[0], tr["event2"].split(":")[0]]
    heard = [p["fid"].split(":")[0] for p in tr["passes"]]
    m = dict(
        case=ans["case"], kind="synthetic recording rendered from real adsb.lol tracks; the answer is known by construction (not blind)",
        true_mic=dict(lat=round(tr["lat"], 6), lon=round(tr["lon"], 6), elev_m=round(tr["elev"], 1)),
        found=dict(lat=ans["lat"], lon=ans["lon"]),
        error_m=round(err, 1),
        latency_injected_s=lat_inj, latency_found_s=ans["latency_s"],
        latency_error_s=round(ans["latency_s"] - lat_inj, 2),
        score=ans["score"], null_grid_median=ans["null_grid_median"], score_over_null=ans["score_over_null"],
        two_band=ans["two_band"], mirror=ans["mirror"], runner_up=ans["runner_up"],
        n_candidate_aircraft=ans["n_candidates"],
        aircraft_used=[dict(hex=a["hex"], callsign=a["callsign"], type=a["type"], closest_slant_m=a["closest_slant_m"],
                            contribution=a["contribution"], in_truth_passes=a["hex"] in heard) for a in ans.get("aircraft", [])],
        truth_event_flights=dict(event1=tr["event1"], event2=tr["event2"]),
        truth_event_flights_found_among_used=[h in used for h in true_hex],
        truth_audible_passes=[dict(fid=p["fid"], audio_cpa_s=round(p["audio_cpa"], 1), distance_m=round(p["d"]), gs_ms=round(p["gs"], 1),
                                   f0_hz=round(p["f0"], 1)) for p in tr["passes"]],
        runtime_s=ans["runtime_s"], ridge_params=ans["params"],
    )
    m.update(extra)
    (rd / "metrics.json").write_text(json.dumps(m, indent=1))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    lat, lon, H = load_heat(rd)
    fig, ax = plt.subplots(figsize=(8, 7.5))
    im = ax.imshow(H, origin="lower", extent=[lon[0], lon[-1], lat[0], lat[-1]], cmap="magma", aspect="auto")
    fig.colorbar(im, ax=ax, label="positive excess (all aircraft, max over latency)")
    ax.plot(tr["lon"], tr["lat"], "o", mfc="none", mec="white", ms=14, mew=2, label="true microphone")
    ax.plot(ans["lon"], ans["lat"], "x", color="lime", ms=11, mew=2.5, label=f"found ({err:.0f} m away)")
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.set_title(f"{ans['case']}: found vs true, error {err:.0f} m, latency {ans['latency_s']:.1f} s (injected {lat_inj} s)")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(rd / "map_found_vs_true.png", dpi=110)
    plt.close(fig)
    print(json.dumps({k: m[k] for k in ("error_m", "latency_injected_s", "latency_found_s", "truth_event_flights_found_among_used")}))


def colsto(rd, extra):
    ans = json.loads((rd / "answer.json").read_text())
    feat = json.loads((rd / "oyster_bay_hamlet.geojson").read_text())
    coords = feat["geometry"]["coordinates"]
    lat, lon = ans["lat"], ans["lon"]
    inside = poly_contains(coords, lon, lat)
    clat, clon = poly_centroid(coords[0])
    d_cent = dist_m(lat, lon, clat, clon)
    d_edge = 0.0 if inside else dist_to_ring_m(coords[0], lat, lon)
    town_path = rd / "town_of_oyster_bay.geojson"
    town_in = None
    if town_path.exists():
        town_in = poly_contains(json.loads(town_path.read_text())["geometry"]["coordinates"], lon, lat)
    mlat = ans.get("mirror") or {}
    m = dict(
        case=ans["case"],
        kind="colsto's challenge recording (youtu.be/4CxVxuMVkMc, 60 min); NOT blind (see regional_box_note)",
        regional_box_note="The 120 x 120 km box is centred on New York City (40.71, -74.01), not on the answer town. It is a regional "
                          "box chosen knowing the answer is on Long Island, so this is not a blind search. Parameters (band, rmax, "
                          "max_alt) were also set knowing the published event ranges and the propeller band.",
        camera_coordinates="camera coordinates not published; only the town (hamlet Oyster Bay, NY) is known",
        found=dict(lat=lat, lon=lon),
        town_match_inside_osm_hamlet_boundary=bool(inside),
        hamlet=dict(name="Oyster Bay, NY (hamlet / census place)", osm="relation 174432 (Nominatim polygon_geojson=1)",
                    centroid_lat=round(clat, 6), centroid_lon=round(clon, 6),
                    bbox=feat["properties"].get("bbox"), polygon_file="oyster_bay_hamlet.geojson"),
        distance_to_hamlet_centroid_m=round(d_cent, 1),
        distance_to_hamlet_boundary_m=round(d_edge, 1),
        inside_town_of_oyster_bay_municipality=town_in,
        town_osm="relation 10805169 (Town of Oyster Bay, Nassau County), supplementary only",
        latency_found_s=ans["latency_s"], utc_start_label=extra.get("utc_start_label"),
        implied_true_audio_start_utc=extra.get("implied_start"),
        score=ans["score"], null_grid_median=ans["null_grid_median"], score_over_null=ans["score_over_null"],
        two_band=ans["two_band"], mirror=ans["mirror"], runner_up=ans["runner_up"],
        n_candidate_aircraft=ans["n_candidates"],
        flights_used=[dict(hex=a["hex"], callsign=a["callsign"], type=a["type"], closest_slant_m=a["closest_slant_m"],
                           cpa_audio_s=a["cpa_audio_s"], f0_hz=a["f0_hz"], contribution=a["contribution"]) for a in ans.get("aircraft", [])],
        runtime_s=ans["runtime_s"], ridge_params=ans["params"],
        osm_attribution="Hamlet and town polygons (c) OpenStreetMap contributors, ODbL, via Nominatim.",
    )
    m.update({k: v for k, v in extra.items() if k not in ("utc_start_label", "implied_start")})
    (rd / "metrics.json").write_text(json.dumps(m, indent=1))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    la, lo, H = load_heat(rd)
    fig, axs = plt.subplots(1, 2, figsize=(15, 6.8))
    for ax, zoom in zip(axs, (False, True)):
        im = ax.imshow(H, origin="lower", extent=[lo[0], lo[-1], la[0], la[-1]], cmap="magma", aspect="auto")
        ring = np.array(coords[0])
        ax.plot(ring[:, 0], ring[:, 1], color="cyan", lw=1.5 if zoom else 1.2, label="OSM hamlet Oyster Bay")
        ax.plot(clon, clat, "+", color="cyan", ms=10, mew=2, label="hamlet centroid")
        ax.plot(lon, lat, "x", color="lime", ms=11, mew=2.5, label=f"ridge answer ({d_cent / 1000:.1f} km from centroid)")
        if zoom:
            half = max(15000.0, d_cent * 1.3) / 111000.0
            ax.set_xlim(min(clon, lon) - half / math.cos(math.radians(clat)) * 0.3, max(clon, lon) + half / math.cos(math.radians(clat)) * 0.3)
            ax.set_ylim(min(clat, lat) - half * 0.3, max(clat, lat) + half * 0.3)
            ax.set_title("zoom: answer vs hamlet")
        else:
            ax.set_xlim(lo[0], lo[-1])
            ax.set_ylim(la[0], la[-1])
            ax.set_title(f"colsto, 120 km box: all aircraft, max over latency; answer {'INSIDE' if inside else 'outside'} the hamlet")
            fig.colorbar(im, ax=ax, label="positive excess")
        ax.set_xlabel("lon")
        ax.set_ylabel("lat")
        ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(rd / "map_answer_vs_hamlet.png", dpi=110)
    plt.close(fig)
    print(json.dumps({k: m[k] for k in ("town_match_inside_osm_hamlet_boundary", "distance_to_hamlet_centroid_m", "distance_to_hamlet_boundary_m")}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["synth", "colsto"])
    ap.add_argument("result_dir", type=Path)
    ap.add_argument("truth", nargs="?")
    ap.add_argument("latency", nargs="?", type=float)
    ap.add_argument("--extra", default="{}")
    a = ap.parse_args()
    ex = json.loads(a.extra)
    if a.kind == "synth":
        synth(a.result_dir, a.truth, a.latency, ex)
    else:
        colsto(a.result_dir, ex)
