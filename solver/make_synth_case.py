#!/usr/bin/env python
"""Turn a doppler-locate synthetic recording into a solver case (the live-webcam input format).

    python solver/make_synth_case.py VENDOR_SYNTH_DIR cases/synth_seed3 --latency 14 [--seed 1]

VENDOR_SYNTH_DIR holds synthetic.wav and truth.json written by ``dopploc synth``. The ground truth stays
OUTSIDE the case folder (it is read only to place the bbox and to inject a known stream latency); the case
holds just what a live webcam would give: a FLAC, a labelled start time, a bbox and ADS-B poll lines.

* utc_start is the true start plus ``latency`` seconds (the label runs ahead of the sound).
* the bbox is 44 x 44 km and does NOT sit centred on the microphone (offset ~7 km).
* adsb.jsonl: two sources polled every ~5 s (offset against each other, jittered), 15 % of polls
  dropped, every aircraft within 150 km of the bbox centre with its latest fix of the last 45 s and
  ``seen_pos`` = poll time - fix time, as /v2/point returns it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("synth_dir", type=Path)
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--adsb", type=Path, default=Path("data/vendor/doppler-locate/data/adsb/2026-09-08"))
    ap.add_argument("--latency", type=float, required=True, help="injected stream latency (s)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--audio-out", type=Path, help="FLAC path (default data/synth_cases/<case>/audio.flac)")
    ap.add_argument("--traces", action="store_true",
                    help="also write 'adsblol-trace' lines (one aircraft per line, seen_pos 0, 20%% null gs/track) and "
                         "drop 45%% of the live polls, as the real feed does")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    truth = json.loads((a.synth_dir / "truth.json").read_text())
    wav = a.synth_dir / "synthetic.wav"
    name = a.case_dir.name
    flac = a.audio_out or Path("data/synth_cases") / name / "audio.flac"
    flac.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "flac", str(flac)], check=True)
    import soundfile as sf
    dur = sf.info(str(wav)).duration
    t_true0 = float(truth["t_audio0_utc"])
    utc_start = t_true0 + a.latency

    # bbox 44 x 44 km, microphone offset from the centre
    half_lat = 22e3 / 111.2e3
    clat = truth["lat"] + 7e3 / 111.2e3 * rng.choice([-1, 1])
    clon = truth["lon"] + 5e3 / (111.2e3 * np.cos(np.radians(truth["lat"]))) * rng.choice([-1, 1])
    half_lon = half_lat / np.cos(np.radians(clat))
    bbox = [clat - half_lat, clon - half_lon, clat + half_lat, clon + half_lon]

    pts = pl.scan_parquet(str(a.adsb / "points-*.parquet"))
    lo, hi = t_true0 - 900, t_true0 + dur + 900
    r = 150e3
    dlat = r / 111.2e3
    dlon = dlat / np.cos(np.radians(clat))
    df = (pts.filter(pl.col("t").is_between(lo - 60, hi) & pl.col("lat").is_between(clat - dlat, clat + dlat)
                     & pl.col("lon").is_between(clon - dlon, clon + dlon))
          .join(pl.scan_parquet(str(a.adsb / "aircraft.parquet")).select("icao", "type", "reg"), on="icao", how="left")
          .sort("icao", "t").collect())
    print(f"{len(df):,} points of {df['icao'].n_unique()} aircraft near the box")

    def polls(src, phase, interval=5.0, jitter=1.0, drop=0.45 if a.traces else 0.15):
        t = lo + phase
        out = []
        while t < hi:
            tt = t + rng.uniform(-jitter, jitter)
            if rng.random() > drop:
                out.append(tt)
            t += interval
        return out

    poll_t = [(t, "adsblol") for t in polls("adsblol", 0.0)] + [(t, "airplaneslive") for t in polls("airplaneslive", 2.5)]
    poll_t.sort()
    groups = {k[0]: g for k, g in df.group_by("icao")}
    arr = {}
    for ic, g in groups.items():
        arr[ic] = {c: g[c].to_numpy() for c in ("t", "lat", "lon", "alt_baro_ft", "alt_geom_ft", "gs_kt", "track_deg",
                                                "vrate_fpm", "callsign", "type")}
    lines = []
    for T, src in poll_t:
        acs = []
        for ic, g in arr.items():
            i = np.searchsorted(g["t"], T, side="right") - 1
            if i < 0 or T - g["t"][i] > 45.0:
                continue
            o = {"hex": ic, "lat": float(g["lat"][i]), "lon": float(g["lon"][i]),
                 "seen_pos": round(float(T - g["t"][i]), 1)}
            if g["callsign"][i]:
                o["flight"] = str(g["callsign"][i]).ljust(8)
            if g["type"][i]:
                o["t"] = str(g["type"][i])
            for k_in, k_out in (("alt_baro_ft", "alt_baro"), ("alt_geom_ft", "alt_geom"), ("gs_kt", "gs"),
                                ("track_deg", "track"), ("vrate_fpm", "geom_rate")):
                v = g[k_in][i]
                if np.isfinite(v):
                    o[k_out] = round(float(v), 1)
            acs.append(o)
        lines.append(json.dumps({"t": round(float(T), 2), "src": src, "ac": acs}))
    if a.traces:
        tr = df.filter(pl.col("t").is_between(lo, hi)).filter(
            (pl.col("lat").is_between(clat - dlat, clat + dlat)) & pl.col("lon").is_between(clon - dlon, clon + dlon))
        d = {c: tr[c].to_list() for c in ("icao", "t", "lat", "lon", "alt_baro_ft", "alt_geom_ft", "gs_kt", "track_deg",
                                          "vrate_fpm", "callsign", "type")}
        nl = 0
        for i in range(len(tr)):
            o = {"hex": d["icao"][i], "lat": d["lat"][i], "lon": d["lon"][i], "seen_pos": 0}
            if d["callsign"][i]:
                o["flight"] = str(d["callsign"][i])
            if d["type"][i]:
                o["t"] = d["type"][i]
            ab = d["alt_baro_ft"][i]
            o["alt_baro"] = None if ab is None or not np.isfinite(ab) else float(ab)
            ag = d["alt_geom_ft"][i]
            if ag is not None and np.isfinite(ag):
                o["alt_geom"] = float(ag)
            null = rng.random() < 0.2
            gs, tk = d["gs_kt"][i], d["track_deg"][i]
            o["gs"] = None if null or gs is None or not np.isfinite(gs) else float(gs)
            o["track"] = None if null or tk is None or not np.isfinite(tk) else float(tk)
            vr = d["vrate_fpm"][i]
            if vr is not None and np.isfinite(vr):
                o["geom_rate"] = float(vr)
            lines.append(json.dumps({"t": round(float(d["t"][i]), 2), "src": "adsblol-trace", "ac": [o]}))
            nl += 1
        print(f"{nl} trace lines added")
    a.case_dir.mkdir(parents=True, exist_ok=True)
    (a.case_dir / "adsb.jsonl").write_text("\n".join(lines) + "\n")
    case = {"audio": flac.as_posix(),
            "utc_start": datetime.fromtimestamp(utc_start, timezone.utc).isoformat(timespec="milliseconds"),
            "bbox": [round(x, 5) for x in bbox]}
    (a.case_dir / "case.json").write_text(json.dumps(case, indent=1))
    print(f"{len(lines)} polls -> {a.case_dir}; injected latency {a.latency} s (kept out of the case folder)")


if __name__ == "__main__":
    main()
