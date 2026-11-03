#!/usr/bin/env python
"""Build a solver case from an ingested adsb.lol day (doppler-locate parquet) and an audio file.

    python solver/day_to_case.py cases/<case> --audio REC.webm --utc-start ISO --bbox LAT0 LON0 LAT1 LON1 \
        --adsb data/vendor/doppler-locate/data/adsb/2026-09-08 [--duration S]

Writes case.json and adsb.jsonl with one ``"src": "adsblol-trace"`` line per position (the format the live
webcam cases use for full-day traces) for every aircraft in the box +- 50 km during the recording.
Used for the colsto recording, where there is no live feed: the history of the day plays the traces' role.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--audio", type=Path, required=True, help="path written into case.json (any ffmpeg-readable file)")
    ap.add_argument("--utc-start", required=True)
    ap.add_argument("--bbox", type=float, nargs=4, required=True, metavar=("LAT0", "LON0", "LAT1", "LON1"))
    ap.add_argument("--adsb", type=Path, required=True)
    ap.add_argument("--duration", type=float, required=True, help="audio length in seconds")
    a = ap.parse_args()
    t0 = datetime.fromisoformat(a.utc_start.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    la0, lo0, la1, lo1 = a.bbox
    m = 50e3 / 111.2e3
    ml = m / np.cos(np.radians((la0 + la1) / 2))
    pts = pl.scan_parquet(str(a.adsb / "points-*.parquet"))
    df = (pts.filter(pl.col("t").is_between(t0 - 900, t0 + a.duration + 900)
                     & pl.col("lat").is_between(la0 - m, la1 + m) & pl.col("lon").is_between(lo0 - ml, lo1 + ml))
          .join(pl.scan_parquet(str(a.adsb / "aircraft.parquet")).select("icao", "type"), on="icao", how="left")
          .sort("icao", "t").collect())
    d = {c: df[c].to_list() for c in ("icao", "t", "lat", "lon", "alt_baro_ft", "alt_geom_ft", "gs_kt", "track_deg",
                                      "vrate_fpm", "callsign", "type")}
    lines = []
    for i in range(len(df)):
        o = {"hex": d["icao"][i], "lat": d["lat"][i], "lon": d["lon"][i], "seen_pos": 0}
        if d["callsign"][i]:
            o["flight"] = d["callsign"][i]
        if d["type"][i]:
            o["t"] = d["type"][i]
        for k_in, k_out in (("alt_baro_ft", "alt_baro"), ("alt_geom_ft", "alt_geom"), ("gs_kt", "gs"),
                            ("track_deg", "track"), ("vrate_fpm", "geom_rate")):
            v = d[k_in][i]
            o[k_out] = float(v) if v is not None and np.isfinite(v) else None
        lines.append(json.dumps({"t": round(float(d["t"][i]), 2), "src": "adsblol-trace", "ac": [o]}))
    a.case_dir.mkdir(parents=True, exist_ok=True)
    (a.case_dir / "adsb.jsonl").write_text("\n".join(lines) + "\n")
    (a.case_dir / "case.json").write_text(json.dumps({
        "audio": a.audio.as_posix(), "utc_start": a.utc_start, "bbox": [round(x, 5) for x in a.bbox]}, indent=1))
    print(f"{len(lines):,} positions of {df['icao'].n_unique()} aircraft -> {a.case_dir}")


if __name__ == "__main__":
    main()
