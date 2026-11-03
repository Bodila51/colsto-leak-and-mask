"""Live ADS-B polls (adsb.jsonl) -> flight tracks the doppler-locate forward model can use.

One line per poll::

    {"t": unix_seconds_of_poll, "src": "adsblol" | "airplaneslive", "ac": [raw /v2/point aircraft, ...]}

A position's time is ``t - seen_pos``. The same position is reported by many polls and by both
sources, so points are de-duplicated; gaps are expected and are left as gaps (tracks are split
after 90 s without a position, exactly as doppler-locate does for the daily history).

Lines with ``"src": "adsblol-trace"`` (one aircraft per line, ``seen_pos`` 0) come from each aircraft's full-day
readsb trace: denser near manoeuvres, sparser on straight legs, ``alt_baro`` may be "ground" (dropped) or a
negative number near a runway (kept), ``gs`` / ``track`` may be null (the velocity then comes from finite
differences of the positions). Both sources are merged per airframe and de-duplicated by time.

Uses doppler-locate (MIT, github.com/digitalis-p/doppler-locate): ``dopploc.tracks`` for the
altitude datum, segmentation and Hermite resampling, ``dopploc.adsb`` for the type table.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from dopploc import adsb, geo, tracks


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return np.nan
    return v if np.isfinite(v) else np.nan


def read_polls(path: Path):
    """Raw rows (one per aircraft per poll) -> dict of lists. Returns (rows, n_polls, sources)."""
    cols = {k: [] for k in ("icao", "t", "lat", "lon", "alt_baro_ft", "alt_geom_ft", "gs_kt", "track_deg",
                            "vrate_fpm", "src", "callsign", "type", "reg")}
    n_polls = 0
    sources: dict[str, int] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a half-written last line
            n_polls += 1
            tp = _num(rec.get("t"))
            src = str(rec.get("src", ""))
            sources[src] = sources.get(src, 0) + 1
            if not np.isfinite(tp):
                continue
            for a in rec.get("ac") or ():
                lat, lon = _num(a.get("lat")), _num(a.get("lon"))
                if not (np.isfinite(lat) and np.isfinite(lon)):
                    continue
                ab = a.get("alt_baro")
                if ab == "ground":
                    continue
                seen = _num(a.get("seen_pos"))
                if not np.isfinite(seen):
                    seen = 0.0
                hexid = str(a.get("hex", "")).lower().lstrip("~")
                if not hexid:
                    continue
                rate = _num(a.get("geom_rate"))
                if not np.isfinite(rate):
                    rate = _num(a.get("baro_rate"))
                cols["icao"].append(hexid)
                cols["t"].append(tp - seen)
                cols["lat"].append(lat)
                cols["lon"].append(lon)
                cols["alt_baro_ft"].append(_num(ab))
                cols["alt_geom_ft"].append(_num(a.get("alt_geom")))
                cols["gs_kt"].append(_num(a.get("gs")))
                cols["track_deg"].append(_num(a.get("track")))
                cols["vrate_fpm"].append(rate)
                cols["src"].append(src)
                cols["callsign"].append((a.get("flight") or "").strip() or None)
                cols["type"].append(a.get("t") or None)
                cols["reg"].append(a.get("r") or None)
    return cols, n_polls, sources


def build_tracks(path: Path, cache: Path, types_path: Path | None, min_spacing_s: float = 1.0,
                 max_gap_s: float = 90.0, log=print):
    """Return (tracks, info). ``info`` counts polls, raw rows and kept points for metrics.json."""
    cols, n_polls, sources = read_polls(path)
    n_raw = len(cols["t"])
    if n_raw == 0:
        return [], {"polls": n_polls, "sources": sources, "rows": 0, "points": 0, "aircraft": 0}
    df = pl.DataFrame(cols, schema_overrides={"alt_baro_ft": pl.Float32, "alt_geom_ft": pl.Float32,
                                              "gs_kt": pl.Float32, "track_deg": pl.Float32,
                                              "vrate_fpm": pl.Float32})
    # the same fix arrives in many polls and from both sources: keep one per (airframe, position)
    df = df.with_columns(pl.col("lat").round(5).alias("_la"), pl.col("lon").round(5).alias("_lo"))
    df = df.sort("icao", "t").unique(subset=["icao", "_la", "_lo"], keep="first", maintain_order=True)
    df = df.drop("_la", "_lo").sort("icao", "t")
    # at most one point per min_spacing_s per airframe (the live polls and the full-day trace stamp the same fix a
    # fraction of a second apart); of two near-duplicates keep the one that has speed and track
    t = df["t"].to_numpy()
    ic = df["icao"].to_numpy()
    full = (np.isfinite(df["gs_kt"].to_numpy()) & np.isfinite(df["track_deg"].to_numpy()))
    keep = np.ones(len(df), bool)
    last_i = 0
    for i in range(1, len(df)):
        if ic[i] == ic[last_i] and t[i] - t[last_i] < min_spacing_s:
            if full[i] and not full[last_i]:
                keep[last_i] = False
                keep[i] = True
                last_i = i
            else:
                keep[i] = False
        else:
            last_i = i
    df = df.filter(pl.Series(keep))

    # type table -> engine class (P piston, T turboprop, J jet, ...); unknown -> "?"
    eng = {}
    if types_path is not None and Path(types_path).exists():
        types = adsb.load_types(Path(types_path))
        eng = {k: adsb.engine_class(v[1]) for k, v in types.items()}
    df = df.with_columns(pl.col("type").replace_strict(eng, default="?", return_dtype=pl.String)
                         .fill_null("?").alias("engine"),
                         pl.lit(0).cast(pl.UInt8).alias("flags"))
    geoid = geo.Geoid(cache)
    h = tracks.altitude_msl(df, geoid, "auto")
    trs = tracks.split_tracks(df, h, max_gap_s=max_gap_s)
    info = {"polls": n_polls, "sources": sources, "rows": n_raw, "points": len(df),
            "aircraft": int(df["icao"].n_unique()), "segments": len(trs)}
    log(f"  ADS-B: {n_polls} polls, {n_raw:,} rows -> {len(df):,} points, {info['aircraft']} aircraft, "
        f"{len(trs)} track segments")
    return trs, info
