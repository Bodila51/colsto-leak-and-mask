#!/usr/bin/env python
"""Live-webcam location solver: Doppler slides of passing aircraft + public ADS-B polls -> a point.

    python solver/solve.py cases/<case> [--out results/<case>]

Run it with the doppler-locate virtual environment (see docs/windows-notes.md):
    data/vendor/doppler-locate/.venv/Scripts/python.exe solver/solve.py cases/<case>

Inputs (cases/<case>/):
  case.json   {"audio": FLAC path, "utc_start": ISO UTC of the first sample, "bbox": [lat_min, lon_min,
               lat_max, lon_max] (at least 40 x 40 km)}
  adsb.jsonl  one line per poll: {"t": unix s, "src": "adsblol"|"airplaneslive", "ac": [raw /v2/point ...]}

Outputs (results/<case>/): answer.json, metrics.json, heatmap_1plane.png, heatmap_2planes.png,
heatmap_all.png, spectrogram_<n>_<callsign>.png (one per flyover used), latency_scan.png, events.json.

Method (physics as in doppler-locate, MIT, github.com/digitalis-p/doppler-locate; its detector, track
resampling, forward model with emission-time solver, brightness scoring kernel and DEM cache are
imported, not copied):
  f = f0 c / (c + v_r) evaluated at the emission time t_e, t_recv = t_e + r(t_e) / c, r the slant range
  to the microphone with the DEM ground height. The point is the argmax of
      T(cell, latency) = sum over flyovers of max(best_flight_score(cell, latency), null)
  with ONE latency shared by all flyovers. Latency = utc_start - true UTC of the first sample (the stream
  label runs ahead of the sound by this much), scanned over [-10, +60] s. Nothing is picked by hand.

The point written to answer.json is the computed argmax. A miss is a result.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from numba import njit, prange
from scipy.ndimage import maximum_filter1d

REPO = Path(__file__).resolve().parents[1]
VENDOR = Path(os.environ.get("DOPPLOC_HOME", REPO / "data" / "vendor" / "doppler-locate"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dopploc import audio, detect, geo, physics, score, tracks  # noqa: E402
from dopploc.dem import DEM  # noqa: E402

import adsb_poll  # noqa: E402

# ----------------------------------------------------------------------------- frozen settings
OBS_HEIGHT_M = 2.0
LAT_RANGE = (-10.0, 60.0)       # stream latency scan range (s)
TOL_S = 8.0                     # tolerance of each flyover's fitted CPA time (straight-line fits are biased)
HALF_WIDTH_S = 60.0             # spectrogram clip around each flyover
F0_FRAC = 0.08                  # f0 sweep +-8 % (straight-line f0 is biased by descent / deceleration)
D_LO, D_HI = 0.75, 1.30         # accepted CPA slant distance = fitted range x (lo, hi)
V_LO, V_HI = 0.85, 1.15         # accepted ground speed = fitted range x (lo, hi), +-5 m/s
MAX_EVENTS = 8
MIN_CONF = ("high", "medium")
COARSE = dict(model_dt=0.25, frame_step=4, dlam=0.5, f0_step_hz=0.5)
FINE = dict(model_dt=0.1, frame_step=1, dlam=0.2, f0_step_hz=0.2, f0_frac=0.06)
N_PEAKS = 4
FINE_STEP_M = 50.0
FINE_HALF_CELLS = 2.5           # fine lattice half-width around a coarse peak, in coarse cells
LAM_ZOOM = 4.0                  # fine stage scans latency +-4 s around the coarse peak
USED_PCT = 90.0                 # a flyover counts as used if its score is above this percentile of its candidates


def log(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------------------- local grid
class Grid:
    """Cells on a local azimuthal-equidistant lattice (metres) centred on the bbox, inside the bbox."""

    def __init__(self, bbox, step_m):
        from pyproj import Transformer
        lat0, lon0, lat1, lon1 = bbox
        self.bbox = bbox
        self.clat, self.clon = (lat0 + lat1) / 2, (lon0 + lon1) / 2
        crs = f"+proj=aeqd +lat_0={self.clat} +lon_0={self.clon} +datum=WGS84 +units=m +no_defs"
        self.fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self.inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        cx, cy = self.fwd.transform(np.array([lon0, lon1, lon0, lon1]), np.array([lat0, lat0, lat1, lat1]))
        self.step = step_m
        xs = np.arange(math.floor(min(cx) / step_m) * step_m + step_m / 2, max(cx), step_m)
        ys = np.arange(math.floor(min(cy) / step_m) * step_m + step_m / 2, max(cy), step_m)
        self.xs, self.ys = xs, ys
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        lon, lat = self.inv.transform(X.ravel(), Y.ravel())
        inside = (lat >= lat0) & (lat <= lat1) & (lon >= lon0) & (lon <= lon1)
        self.shape = X.shape
        self.flat = np.flatnonzero(inside)
        self.x, self.y = X.ravel()[inside], Y.ravel()[inside]
        self.lat, self.lon = lat[inside], lon[inside]
        self.n = len(self.x)

    def to_map(self, v):
        """Cell values -> 2D array (nx, ny) with NaN outside the bbox."""
        out = np.full(self.shape[0] * self.shape[1], np.nan)
        out[self.flat] = v
        return out.reshape(self.shape)


def lattice_around(grid: Grid, x0, y0, half, step):
    """Fine cells around (x0, y0), inside the bbox."""
    o = np.arange(-half, half + 1e-6, step)
    X, Y = np.meshgrid(x0 + o, y0 + o, indexing="ij")
    X, Y = X.ravel(), Y.ravel()
    lon, lat = grid.inv.transform(X, Y)
    la0, lo0, la1, lo1 = grid.bbox
    ok = (lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1)
    return X[ok], Y[ok], lat[ok], lon[ok]


# ----------------------------------------------------------------------------- numba helpers
@njit(parallel=True, cache=True)
def _cpa_cells(obs, t0, dt, P, c):
    """Closest point of approach of one sampled track to every observer. NaN when the CPA is at an end."""
    n = obs.shape[0]
    m = P.shape[0]
    tau = np.full(n, np.nan)
    dd = np.full(n, np.nan)
    tdel = np.full(n, np.nan)
    for i in prange(n):
        a, d, t = physics.track_cpa(obs[i], t0, dt, P, c)
        if a > t0 + dt and a < t0 + (m - 2) * dt:
            tau[i] = a
            dd[i] = d
            tdel[i] = t
    return tau, dd, tdel


# ----------------------------------------------------------------------------- events
@dataclass
class Ev:
    name: str
    t_e: float                 # audio time of the delayed CPA (s)
    f0: float
    harms: np.ndarray
    speed: tuple
    slant: tuple
    z: float
    confidence: str
    fband: tuple = (0.0, 0.0)
    tau: np.ndarray = None     # frame times (audio s)
    S0: np.ndarray = None      # whitened brightness (nbin, nt)
    B: np.ndarray = None       # S0 with +-1 bin frequency tolerance
    fb0: float = 0.0
    dfb: float = 0.0
    flights: list = field(default_factory=list)   # candidate Flight objects (index = F value)
    window: tuple = (0.0, 0.0)  # accepted delayed-CPA UTC window
    null: float = 0.0


@dataclass
class Flight:
    fid: str
    hexid: str
    callsign: str | None
    type: str | None
    engine: str
    sm: tracks.Sampled
    lat: np.ndarray
    lon: np.ndarray


def prepare_spec(ev: Ev, audio_path):
    """Whitened brightness clip around a flyover (as dopploc score.prepare_event)."""
    hmax = int(max(ev.harms))
    hmin = int(min(ev.harms))
    ev.fband = (max(15.0, 0.6 * ev.f0 * hmin), 1.6 * ev.f0 * hmax)
    sr = 4000 if ev.fband[1] < 1800 else 16000
    sp = audio.brightness(audio_path, ev.t_e - HALF_WIDTH_S, ev.t_e + HALF_WIDTH_S, sr=sr, win_s=1.0, hop_s=0.25,
                          fmin=ev.fband[0], fmax=ev.fband[1], line_s=20.0)
    ev.tau = sp.t.astype(float)
    ev.S0 = np.ascontiguousarray(sp.S, dtype=np.float64)
    ev.B = np.ascontiguousarray(maximum_filter1d(sp.S, size=3, axis=0), dtype=np.float64)
    ev.fb0, ev.dfb = float(sp.f[0]), float(sp.df)
    ev.sp = sp


def events_from_detection(res, max_events, c=343.0, fallback_low=True):
    """High / medium confidence aircraft flyovers (best detector score first, at most ``max_events``). Fallback:
    with fewer than two of those, up to two refined low-confidence aircraft detections are added (their
    speed / distance ranges are profiled here); they carry ``confidence = "low"`` into the outputs."""
    cand = [f for f in res.flyovers if f.confidence in MIN_CONF and f.kind == "aircraft" and f.refined
            and f.ranges is not None]
    cand.sort(key=lambda f: -f.z)
    cand = cand[:max_events]
    added = 0
    if fallback_low and len(cand) < 2:
        low = [f for f in res.flyovers if f.confidence == "low" and f.kind == "aircraft" and f.refined
               and hasattr(f, "_trace")]
        low.sort(key=lambda f: -f.z)
        for fo in low:
            if len(cand) >= 2 or added >= 2:
                break
            try:
                rng = detect._ranges_job((*fo._trace, c))
            except Exception:  # noqa: BLE001
                rng = None
            if rng:
                fo.ranges = rng
                cand.append(fo)
                added += 1
    evs = []
    for fo in cand:
        e = fo.event("x")
        if e is None:
            continue
        evs.append(Ev(name="", t_e=float(e["audio_cpa"]), f0=float(e["f0"]), harms=np.asarray(e["harmonics"], float),
                      speed=tuple(e["speed_ms"]), slant=tuple(e["slant_m"]), z=float(fo.z),
                      confidence=fo.confidence))
    evs.sort(key=lambda e: e.t_e)
    for i, e in enumerate(evs):
        e.name = f"F{i + 1}"
    return evs, added


# ----------------------------------------------------------------------------- atmosphere
class Atm:
    """GFS effective wind / sound speed per (flight, time) at the bbox centre; still air outside CONUS."""

    def __init__(self, mode, day, cache, clat, clon, c, elev_c):
        self.c = c
        self.clat, self.clon, self.elev = clat, clon, elev_c
        self.A = None
        self.mode = "none"
        if mode == "gfs":
            from dopploc import atmos
            la0, la1, lo0, lo1 = atmos.CROP
            if la0 <= clat <= la1 and lo0 <= clon % 360 <= lo1:
                try:
                    self.A = atmos.Atmosphere(day, cache, log=lambda *_: None)
                    self.mode = "gfs"
                except Exception as e:  # network / eccodes problem: fall back to still air, say so
                    log(f"  GFS unavailable ({e}); still air c = {c:g}")
            else:
                log("  bbox outside the GFS crop (CONUS); still air")

    def row(self, t_utc, h_ac):
        if self.A is None:
            return physics.still_air(self.c)
        ce, ua, va, um, vm = self.A.effective(np.array([self.clat]), np.array([self.clon]), np.array([t_utc]),
                                              np.array([self.elev + OBS_HEIGHT_M]), np.array([h_ac]))
        z = np.zeros(1)
        ws = geo.enu_to_ecef_vec(np.array([self.clat]), np.array([self.clon]), ua, va, z)[0]
        wm = geo.enu_to_ecef_vec(np.array([self.clat]), np.array([self.clon]), um, vm, z)[0]
        return np.r_[ce[0], ws, ce[0], wm]


# ----------------------------------------------------------------------------- candidate flights
def collect_flights(trs, ev: Ev, utc0, bbox, c, exclude_engines):
    la0, lo0, la1, lo1 = bbox
    dmax = ev.slant[1] * D_HI
    mlat = dmax / 111e3 + 0.05
    mlon = mlat / max(0.2, math.cos(math.radians((la0 + la1) / 2)))
    lo_t = utc0 + ev.t_e - LAT_RANGE[1] - TOL_S
    hi_t = utc0 + ev.t_e - LAT_RANGE[0] + TOL_S
    ev.window = (lo_t, hi_t)
    out = []
    for tr in trs:
        if tr.engine in exclude_engines:
            continue
        if tr.t[-1] < lo_t - dmax / c - 5 or tr.t[0] > hi_t:
            continue
        if (tr.lat.max() < la0 - mlat or tr.lat.min() > la1 + mlat or tr.lon.max() < lo0 - mlon
                or tr.lon.min() > lo1 + mlon):
            continue
        # resample the part that can matter: emission up to dmax/c before the delayed CPA, +-75 s of curve
        sm = tracks.resample(tr, 0.5, t_range=(lo_t - dmax / c - 80.0, hi_t + 80.0))
        if sm is None or len(sm.gs) < 8:
            continue
        hexid = tr.icao
        out.append(Flight(tr.fid, hexid, tr.callsign, tr.type, tr.engine, sm, tr.lat, tr.lon))
    ev.flights = out


# ----------------------------------------------------------------------------- scoring core
def sweep_f0(ev: Ev, step_hz, frac=F0_FRAC):
    span = ev.f0 * frac
    return np.arange(ev.f0 - span, ev.f0 + span + 1e-9, step_hz)


def score_event_cells(ev: Ev, utc0, obs, cell_ids, ncell, lam_grid, cfg, atm: Atm, c, mode, lam_center=None,
                      deltas=None, f0_grid=None, d_band=True):
    """Score every (cell, flight) for one flyover.

    mode "scan": per-row delta sweep (+-TOL_S) around the flyover's own measured CPA time; the implied
      latency lam = utc0 + t_e - t_cpa_utc + delta is binned on ``lam_grid`` (max over flights and f0).
    mode "abs": a shared absolute latency grid (``deltas`` are latencies): used by the fine stage.
    Returns S (ncell, L) float32 (-inf where no candidate), F (ncell, L) int16 (flight index),
    and the list of per-row best scores (for the null level).
    """
    L = len(lam_grid)
    dlam = lam_grid[1] - lam_grid[0] if L > 1 else 1.0
    S = np.full((ncell, L), -np.inf, np.float32)
    F = np.full((ncell, L), -1, np.int16)
    if not ev.flights:
        return S, F, np.zeros(0)
    fs = cfg["frame_step"]
    tau = np.ascontiguousarray(ev.tau[::fs])
    B = np.ascontiguousarray(ev.B[:, ::fs])
    f0s = f0_grid if f0_grid is not None else sweep_f0(ev, cfg["f0_step_hz"], cfg.get("f0_frac", F0_FRAC))
    hw = HALF_WIDTH_S
    ext = hw + TOL_S + 2 * LAM_ZOOM + 6.0       # curve half-extent covering every shift that can be scored
    u_grid = np.arange(-ext, ext + 1e-9, cfg["model_dt"])
    u0, du = u_grid[0], u_grid[1] - u_grid[0]
    if deltas is None:
        deltas = np.arange(-TOL_S, TOL_S + 1e-9, cfg["dlam"])
    row_best = []
    dmin, dmax = ev.slant[0] * D_LO, ev.slant[1] * D_HI
    vmin, vmax = ev.speed[0] * V_LO - 5.0, ev.speed[1] * V_HI + 5.0
    tw = ev.window
    for fi, fl in enumerate(ev.flights):
        sm = fl.sm
        step = 4
        P = np.ascontiguousarray(sm.P[::step])
        tau_c, dd, tdel = _cpa_cells(obs, sm.t0, sm.dt * step, P, c)
        ok = np.isfinite(tdel) & (dd >= dmin) & (dd <= dmax) & (tdel >= tw[0]) & (tdel <= tw[1])
        if mode == "abs" and lam_center is not None:
            lam_ij = utc0 + ev.t_e - tdel
            ok &= np.abs(lam_ij - lam_center) <= TOL_S + LAM_ZOOM + 1.0
        if not ok.any():
            continue
        idx = np.flatnonzero(ok)
        gs = np.interp(tau_c[idx], sm.t, sm.gs)
        keep = (gs >= vmin) & (gs <= vmax)
        idx = idx[keep]
        if not len(idx):
            continue
        t_mid = float(np.mean(tdel[idx]))
        h_ac = float(np.interp(t_mid, sm.t, sm.h))
        arow = atm.row(t_mid, h_ac)
        for s0 in range(0, len(idx), 4096):
            sel = idx[s0:s0 + 4096]
            tc, dc, R = score.model_ratios(obs[sel], sm, tdel[sel], u_grid, c, arow, win=90.0)
            good = np.isfinite(tc)
            if not good.any():
                continue
            sel, tc, dc, R = sel[good], tc[good], dc[good], R[good]
            if mode == "scan":
                base = np.full(len(sel), ev.t_e)
                Ssc, _ = score.score_brightness(R, u0, du, base, deltas, f0s, ev.harms, tau, B, ev.fb0, ev.dfb)
                lam = (utc0 + ev.t_e - tc)[:, None] + deltas[None, :]
            else:
                base = tc - utc0
                Ssc, _ = score.score_brightness(R, u0, du, base, deltas, f0s, ev.harms, tau, B, ev.fb0, ev.dfb)
                lam = np.broadcast_to(deltas[None, :], Ssc.shape)
            row_best.append(Ssc.max(axis=1))
            b = np.rint((lam - lam_grid[0]) / dlam).astype(np.int64)
            okb = (b >= 0) & (b < L)
            if mode == "abs":   # same acceptance as the scan: curve within +-TOL_S of the measured CPA time
                okb &= np.abs(lam - (utc0 + ev.t_e - tc)[:, None]) <= TOL_S
            cid = np.broadcast_to(cell_ids[sel][:, None], b.shape)
            flat = (cid * L + b)[okb]
            vals = Ssc[okb].astype(np.float32)
            Sf = S.reshape(-1)
            np.maximum.at(Sf, flat, vals)
            hit = Sf[flat] == vals
            F.reshape(-1)[flat[hit]] = fi
    return S, F, (np.concatenate(row_best) if row_best else np.zeros(0))


def null_level(S):
    v = S[np.isfinite(S)]
    return float(np.median(v)) if v.size else 0.0


def total_map(Slist, nulls):
    """T = sum_e max(S_e, null_e), with null_e where an event has no candidate."""
    T = 0.0
    for S, n in zip(Slist, nulls):
        T = T + np.where(np.isfinite(S), np.maximum(S, n), n)
    return T


def pick_peaks(grid: Grid, Tcell, n, radius_m):
    order = np.argsort(-Tcell)
    out = []
    for i in order:
        if all(math.hypot(grid.x[i] - grid.x[j], grid.y[i] - grid.y[j]) >= radius_m for j in out):
            out.append(int(i))
            if len(out) >= n:
                break
    return out


# ----------------------------------------------------------------------------- main solve
def solve(case_dir: Path, out_dir: Path, args):
    t_start = time.time()
    stage = {}
    case = json.loads((case_dir / "case.json").read_text())
    audio_path = Path(case["audio"])
    if not audio_path.is_absolute():
        audio_path = (REPO / audio_path) if (REPO / audio_path).exists() else (case_dir / audio_path)
    utc0 = datetime.fromisoformat(case["utc_start"].replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    bbox = [float(x) for x in case["bbox"]]
    cache = Path(os.environ.get("DOPPLOC_CACHE", VENDOR / "data" / "cache"))
    types_path = VENDOR / "data" / "ref" / "icao_aircraft_types2.js.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    c = args.c
    day = datetime.fromtimestamp(utc0, timezone.utc).strftime("%Y-%m-%d")
    clat0, clon0 = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    km_ns = (bbox[2] - bbox[0]) * 111.2
    km_ew = (bbox[3] - bbox[1]) * 111.2 * math.cos(math.radians(clat0))
    if min(km_ns, km_ew) < 39.0:
        log(f"warning: bbox is {km_ns:.0f} x {km_ew:.0f} km; the method wants at least 40 x 40 km")

    metrics = {"case": case_dir.name, "audio": audio_path.name, "utc_start": case["utc_start"], "bbox": bbox,
               "bbox_km": [round(km_ns, 1), round(km_ew, 1)], "settings": {
                   "latency_range_s": LAT_RANGE, "cpa_tol_s": TOL_S, "f0_frac": F0_FRAC, "max_events": args.max_events,
                   "coarse": COARSE, "fine": FINE, "atmosphere": args.atmosphere, "c": c,
                   "exclude_engines": sorted(args.exclude_engines)}}

    # 1. ADS-B polls -> tracks
    t0 = time.time()
    log("ADS-B polls")
    trs, ainfo = adsb_poll.build_tracks(case_dir / "adsb.jsonl", cache, types_path, log=log)
    metrics["adsb"] = ainfo
    stage["adsb_s"] = round(time.time() - t0, 1)

    # 2. flyovers in the audio
    t0 = time.time()
    import pickle
    dur = detect.duration(audio_path)
    metrics["audio_duration_s"] = round(dur, 1)
    pk = out_dir / "detect" / "result.pkl"
    if args.reuse_detect and pk.exists():
        res = pickle.loads(pk.read_bytes())
        log(f"flyover detection reused from {pk.name}")
    else:
        log(f"detecting flyovers in {dur / 60:.1f} min of audio")
        res = detect.detect(audio_path, 0.0, None, detect.Params(), log=log)
        detect.save(res, out_dir / "detect", log=lambda *_: None)
        pk.write_bytes(pickle.dumps(res))
    evs, n_low = events_from_detection(res, args.max_events, c, fallback_low=not args.no_fallback_low)
    metrics["flyovers_detected"] = {"total": len(res.flyovers),
                                    "high_medium": sum(f.confidence in MIN_CONF for f in res.flyovers),
                                    "low_confidence_added": n_low, "used_as_events": len(evs)}
    stage["detect_s"] = round(time.time() - t0, 1)
    answer = {"case": case_dir.name, "status": "ok", "method": "argmax of the summed per-flyover scores, one "
              "shared latency", "bbox": bbox, "utc_start": case["utc_start"]}
    if not evs or not trs:
        answer.update(status="no_flyovers" if not evs else "no_adsb", lat=None, lon=None, score=None,
                      latency_s=None, flights_used=[], two_band=None)
        metrics["stages_s"] = stage
        metrics["wall_s"] = round(time.time() - t_start, 1)
        (out_dir / "answer.json").write_text(json.dumps(answer, indent=1))
        (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
        log(f"no point: {answer['status']}")
        return answer, metrics

    # 3. grid, DEM, spectrograms, candidate flights
    t0 = time.time()
    area_km2 = km_ns * km_ew
    step = max(200.0, math.ceil(math.sqrt(area_km2 * 1e6 / 60000.0) / 50.0) * 50.0)
    grid = Grid(bbox, step)
    tiles = {(int(math.floor(a)), int(math.floor(b))) for a in (bbox[0], bbox[2]) for b in (bbox[1], bbox[3])}
    ntiles = (int(math.floor(bbox[2])) - int(math.floor(bbox[0])) + 1) * (int(math.floor(bbox[3])) - int(math.floor(bbox[1])) + 1)
    dem_res = 30 if ntiles <= 6 else 90
    dem = DEM(cache / "dem", res=dem_res)
    elev = dem.elevation(grid.lat, grid.lon)
    obs = geo.lla_to_ecef(grid.lat, grid.lon, elev + OBS_HEIGHT_M)
    atm = Atm(args.atmosphere, day, cache, grid.clat, grid.clon, c, float(np.median(elev)))
    metrics["grid"] = {"step_m": step, "cells": grid.n, "dem_res_m": dem_res, "atmosphere": atm.mode}
    log(f"grid: {grid.n:,} cells at {step:g} m, DEM GLO-{dem_res}, atmosphere {atm.mode}")
    for e in evs:
        prepare_spec(e, audio_path)
        collect_flights(trs, e, utc0, bbox, c, args.exclude_engines)
        log(f"  {e.name}: t={e.t_e:.1f}s f0={e.f0:.1f}Hz harm={e.harms.astype(int).tolist()} "
            f"v={e.speed[0]:.0f}-{e.speed[1]:.0f} d={e.slant[0]:.0f}-{e.slant[1]:.0f}m ({e.confidence}) "
            f"-> {len(e.flights)} candidate flights")
    stage["prep_s"] = round(time.time() - t0, 1)

    # 4. coarse scan: every cell x flight x latency
    t0 = time.time()
    lam_grid = np.arange(LAT_RANGE[0], LAT_RANGE[1] + 1e-9, COARSE["dlam"])
    cell_ids = np.arange(grid.n)
    Sl, Fl = [], []
    row_stats = {}
    for e in evs:
        te = time.time()
        S, F, rb = score_event_cells(e, utc0, obs, cell_ids, grid.n, lam_grid, COARSE, atm, c, "scan")
        e.null = null_level(S)
        Sl.append(S)
        Fl.append(F)
        row_stats[e.name] = {"candidate_rows": int(len(rb)), "null": round(e.null, 4),
                             "p90": round(float(np.percentile(rb, USED_PCT)), 4) if len(rb) else None,
                             "secs": round(time.time() - te, 1)}
        log(f"  {e.name}: {len(rb):,} candidate rows, null {e.null:.3f}, {time.time() - te:.0f} s")
    nulls = [e.null for e in evs]
    T = total_map(Sl, nulls)                     # (cells, L)
    Tcell = T.max(axis=1)
    peaks = pick_peaks(grid, Tcell, N_PEAKS, max(1500.0, 6 * step))
    metrics["coarse"] = {"events": row_stats, "peaks": [
        {"lat": float(grid.lat[i]), "lon": float(grid.lon[i]), "total": float(Tcell[i]),
         "latency_s": float(lam_grid[int(np.argmax(T[i]))])} for i in peaks]}
    stage["coarse_s"] = round(time.time() - t0, 1)

    # 5. fine stage around each peak (shared latency, finer grid, f0, model step)
    t0 = time.time()
    best = None
    fine_maps = []
    for pk in peaks:
        lam_p = float(lam_grid[int(np.argmax(T[pk]))])
        fx, fy, flat_, flon_ = lattice_around(grid, grid.x[pk], grid.y[pk], FINE_HALF_CELLS * step, FINE_STEP_M)
        if len(fx) == 0:
            continue
        fel = dem.elevation(flat_, flon_)
        fobs = geo.lla_to_ecef(flat_, flon_, fel + OBS_HEIGHT_M)
        lam_f = np.arange(lam_p - LAM_ZOOM, lam_p + LAM_ZOOM + 1e-9, FINE["dlam"])
        cid = np.arange(len(fx))
        Sf_l, fsets = [], []
        near = np.flatnonzero(np.hypot(grid.x - grid.x[pk], grid.y - grid.y[pk]) <= (FINE_HALF_CELLS + 1.0) * step)
        li = int(np.argmin(np.abs(lam_grid - lam_p)))
        lo, hi = max(0, li - 8), min(len(lam_grid), li + 9)
        for k, e in enumerate(evs):
            # candidate flights for this event near the peak: the ones the coarse scan chose around it
            fset = np.unique(Fl[k][np.ix_(near, np.arange(lo, hi))])
            fset = fset[fset >= 0]
            saved = e.flights
            e.flights = [saved[j] for j in fset]
            S, _, _ = score_event_cells(e, utc0, fobs, cid, len(fx), lam_f, FINE, atm, c, "abs", lam_center=lam_p,
                                        deltas=lam_f)
            e.flights = saved
            Sf_l.append(S)
            fsets.append(fset)
        Tf = total_map(Sf_l, nulls)
        i, j = np.unravel_index(int(np.argmax(Tf)), Tf.shape)
        cand = {"total": float(Tf[i, j]), "lat": float(flat_[i]), "lon": float(flon_[i]), "x": float(fx[i]),
                "y": float(fy[i]), "elev": float(fel[i]), "lam": float(lam_f[j]), "peak": pk}
        fine_maps.append(dict(cand))
        cand["fsets"] = fsets
        if best is None or cand["total"] > best["total"]:
            best = cand
    stage["fine_s"] = round(time.time() - t0, 1)
    metrics["fine_peaks"] = fine_maps
    if best is None:
        answer.update(status="no_candidates", lat=None, lon=None, score=None, latency_s=None, flights_used=[],
                      two_band=None)
        metrics["stages_s"] = stage
        (out_dir / "answer.json").write_text(json.dumps(answer, indent=1))
        (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
        return answer, metrics

    # 6. exact evaluation at the argmax: latency and f0 refined, best flight per flyover
    t0 = time.time()
    lat_b, lon_b = best["lat"], best["lon"]
    obs_b = geo.lla_to_ecef(np.array([lat_b]), np.array([lon_b]), np.array([best["elev"] + OBS_HEIGHT_M]))
    lam_x = np.arange(best["lam"] - 1.0, best["lam"] + 1.0 + 1e-9, 0.05)
    best_by_event = {}
    for k, e in enumerate(evs):
        for fi in best["fsets"][k]:
            fi = int(fi)
            saved = e.flights
            e.flights = [saved[fi]]
            S, _, _ = score_event_cells(e, utc0, obs_b, np.array([0]), 1, lam_x, dict(FINE, dlam=0.05), atm, c,
                                        "abs", lam_center=best["lam"], deltas=lam_x,
                                        f0_grid=sweep_f0(e, 0.05, 0.05))
            e.flights = saved
            s_here = S[0]
            if np.isfinite(s_here).any():
                cur = best_by_event.get(k)
                if cur is None or np.max(s_here) > np.max(cur[1]):
                    best_by_event[k] = (fi, s_here)
    # one shared latency: maximise the sum over events of each event's best flight
    tot = np.zeros(len(lam_x))
    for k, e in enumerate(evs):
        if k in best_by_event:
            tot += np.maximum(best_by_event[k][1], e.null)
        else:
            tot += e.null
    jx = int(np.argmax(tot))
    lam_star = float(lam_x[jx])
    total_star = float(tot[jx])
    stage["exact_s"] = round(time.time() - t0, 1)

    # which flyovers count as used: the coarse-stage score of the chosen flight at the answer cell and latency
    # is above the 90th percentile of all of that flyover's candidate rows
    li = int(np.argmin(np.abs(lam_grid - lam_star)))
    icell = int(np.argmin(np.hypot(grid.x - best["x"], grid.y - best["y"])))
    used = set()
    for k, e in enumerate(evs):
        if k not in best_by_event:
            continue
        sc_c = float(np.max(Sl[k][max(0, icell - 0):icell + 1, max(0, li - 2):li + 3]))
        p90 = row_stats[e.name]["p90"]
        if p90 is not None and np.isfinite(sc_c) and sc_c >= p90:
            used.add(k)
    # per-flyover details
    flights_used = []
    details = {}
    for k, e in enumerate(evs):
        if k not in best_by_event:
            details[e.name] = None
            continue
        fi, s = best_by_event[k]
        fl = e.flights[fi]
        sc = float(s[jx])
        t_exp = utc0 + e.t_e - lam_star
        tc, dc, R = score.model_ratios(obs_b, fl.sm, np.array([t_exp]), np.arange(-90, 90.01, 0.1), c,
                                       atm.row(t_exp, float(np.interp(t_exp, fl.sm.t, fl.sm.h))))
        tcpa_utc = float(tc[0])
        gs_c = float(np.interp(tcpa_utc - dc[0] / c, fl.sm.t, fl.sm.gs))
        rec = {"flyover": e.name, "hex": fl.hexid, "callsign": fl.callsign, "type": fl.type, "engine": fl.engine,
               "fid": fl.fid, "score": round(sc, 4), "null": round(e.null, 4), "used": k in used,
               "audio_cpa_measured_s": round(e.t_e, 2), "cpa_distance_m": round(float(dc[0]), 1),
               "ground_speed_ms": round(gs_c, 1),
               "cpa_utc": datetime.fromtimestamp(tcpa_utc, timezone.utc).isoformat(timespec="seconds"),
               "confidence": e.confidence, "detector_score": round(e.z, 1)}
        details[e.name] = rec
        flights_used.append(rec)
    used_names = [d["flyover"] for d in flights_used if d["used"]]

    # 7. the coarse maps at the final latency: strongest flyover alone, two strongest, every used flyover
    order = sorted([k for k in used], key=lambda k: -(details[evs[k].name]["score"] - evs[k].null))
    if not order and best_by_event:   # nothing cleared the bar: still draw the single best flyover
        order = [max(best_by_event, key=lambda k: details[evs[k].name]["score"] - evs[k].null)]

    def emap(k):
        s = Sl[k][:, li]
        return np.where(np.isfinite(s), np.maximum(s, evs[k].null), evs[k].null)

    def full(k):   # (cells, latencies): the flyover's score, or its null where it has no candidate
        s = Sl[k]
        return np.where(np.isfinite(s), np.maximum(s, evs[k].null), evs[k].null)

    def lam_max(ks):   # best summed score over ONE shared latency, per cell
        return sum(full(k) for k in ks).max(axis=1) if ks else np.zeros(grid.n)

    M1 = lam_max(order[:1])
    M2 = lam_max(order[:2])
    Mall = lam_max(order)
    two_band, why, mirror = assess_two_band(grid, evs, flights_used, details, used_names, Mall, best, step, c)

    answer.update({
        "lat": round(lat_b, 6), "lon": round(lon_b, 6), "elevation_m": round(best["elev"], 1),
        "score": round(total_star, 4), "score_null": round(float(sum(e.null for e in evs)), 4),
        "latency_s": round(lam_star, 2),
        "latency_note": "utc_start - true UTC of the first sample; true UTC of audio time t = utc_start + t - latency",
        "n_flyovers_detected": metrics["flyovers_detected"]["high_medium"], "n_flyovers_scored": len(evs),
        "n_flyovers_used": len(used_names), "flights_used": flights_used, "two_band": bool(two_band),
        "two_band_reason": why, "mirror_point": mirror,
        "fine_total": round(best["total"], 4),
        "runner_up": next(({"lat": round(m["lat"], 6), "lon": round(m["lon"], 6), "fine_total": round(m["total"], 4),
                            "margin": round(best["total"] - m["total"], 4)}
                           for m in sorted(fine_maps, key=lambda m: -m["total"]) if m["peak"] != best["peak"]), None),
        "search": {"step_m": step, "cells": grid.n, "bbox_km": metrics["bbox_km"],
                   "coarse_peaks": metrics["coarse"]["peaks"]}})
    # 8. outputs
    t0 = time.time()
    make_outputs(out_dir, grid, evs, details, order, M1, M2, Mall, best, lam_star, lam_x, tot, answer, atm, c,
                 utc0, obs_b, best_by_event, trs)
    stage["plots_s"] = round(time.time() - t0, 1)
    metrics["stages_s"] = stage
    metrics["wall_s"] = round(time.time() - t_start, 1)
    try:
        import psutil
        metrics["peak_rss_gb_main_process_end"] = round(psutil.Process().memory_info().rss / 2**30, 2)
    except Exception:
        pass
    (out_dir / "answer.json").write_text(json.dumps(answer, indent=1))
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1))
    (out_dir / "events.json").write_text(json.dumps([{
        "name": e.name, "audio_cpa_s": e.t_e, "f0_hz": e.f0, "harmonics": e.harms.astype(int).tolist(),
        "speed_ms": e.speed, "slant_m": e.slant, "confidence": e.confidence, "detector_score": e.z,
        "n_candidate_flights": len(e.flights)} for e in evs], indent=1))
    log(f"answer: {lat_b:.5f}, {lon_b:.5f}  score {total_star:.3f}  latency {lam_star:.2f} s  "
        f"used {len(used_names)}/{len(evs)} flyovers  two_band={two_band}  ({time.time() - t_start:.0f} s)")
    return answer, metrics


# ----------------------------------------------------------------------------- two-band
def _line_of(fl: Flight, t_cpa):
    """Local straight line (heading deg mod 180, point xy) of a flight at its CPA, in lat/lon."""
    sm = fl.sm
    i = int(np.clip(np.searchsorted(sm.t, t_cpa), 1, len(sm.t) - 2))
    j0, j1 = max(0, i - 20), min(len(sm.t) - 1, i + 20)
    la0, lo0, la1, lo1 = sm.lat[j0], sm.lon[j0], sm.lat[j1], sm.lon[j1]
    dn = (la1 - la0) * 111.2e3
    de = (lo1 - lo0) * 111.2e3 * math.cos(math.radians(la0))
    return math.degrees(math.atan2(de, dn)) % 180.0, (sm.lat[i], sm.lon[i])


def assess_two_band(grid, evs, flights_used, details, used_names, Mall, best, step, c):
    """Two-band flag.

    A single flyover (or several on the same line) cannot tell the two sides of the track apart: the score is
    one band on each side. True when (a) fewer than two flyovers are used, or (b) all used tracks run
    parallel (<= 8 deg) and within 10 % of the CPA distance of one line, or (c) the all-flyover map has a second
    peak >= max(3 km, 4 cells) away within 5 % of the dynamic range of the best peak.
    """
    reasons = []
    mirror = None
    used = [d for d in flights_used if d["used"]]
    flights = {}
    for e in evs:
        d = details.get(e.name)
        if d:
            flights[e.name] = next(f for f in e.flights if f.fid == d["fid"])
    lines = []
    for d in used:
        fl = flights[d["flyover"]]
        t_c = datetime.fromisoformat(d["cpa_utc"]).timestamp() - d["cpa_distance_m"] / c
        lines.append(_line_of(fl, t_c) + (d["cpa_distance_m"],))
    if len(used) < 2:
        reasons.append("fewer than two flyovers used: one track leaves two mirror bands")
    else:
        h0, p0, d0 = lines[0]
        same = True
        for h, p, dd in lines[1:]:
            dh = abs(h - h0)
            dh = min(dh, 180 - dh)
            # perpendicular offset of the second line's point from the first line
            a = math.radians(h0)
            de = (p[1] - p0[1]) * 111.2e3 * math.cos(math.radians(p0[0]))
            dn = (p[0] - p0[0]) * 111.2e3
            off = abs(de * math.cos(a) - dn * math.sin(a))
            if dh > 8.0 or off > 0.1 * max(d0, dd):
                same = False
        if same:
            reasons.append("all used tracks run along one line: the two sides stay indistinguishable")
    # (c) second peak in the all-flyover map
    keep = np.hypot(grid.x - best["x"], grid.y - best["y"]) >= max(3000.0, 4 * step)
    if keep.any():
        top = float(np.max(Mall))
        lo = float(np.median(Mall))
        sec = float(np.max(Mall[keep]))
        if top > lo and (top - sec) <= 0.05 * (top - lo):
            reasons.append(f"second peak {top - sec:.3f} below the best (<= 5 % of the dynamic range)")
    if reasons and lines:
        h, p, dd = lines[0]
        # mirror image of the answer across the first used track
        a = math.radians(h)
        de = (best["lon"] - p[1]) * 111.2e3 * math.cos(math.radians(p[0]))
        dn = (best["lat"] - p[0]) * 111.2e3
        along = de * math.sin(a) + dn * math.cos(a)
        perp = de * math.cos(a) - dn * math.sin(a)
        me = along * math.sin(a) - perp * math.cos(a)
        mn = along * math.cos(a) + perp * math.sin(a)
        mirror = {"lat": round(p[0] + mn / 111.2e3, 6),
                  "lon": round(p[1] + me / (111.2e3 * math.cos(math.radians(p[0]))), 6)}
    return bool(reasons), "; ".join(reasons) if reasons else "tracks on different lines and no second peak", mirror


# ----------------------------------------------------------------------------- plots
def make_outputs(out_dir, grid, evs, details, order, M1, M2, Mall, best, lam_star, lam_x, tot, answer, atm, c,
                 utc0, obs_b, best_by_event, trs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    la0, lo0, la1, lo1 = grid.bbox
    cl = answer["lat"]
    cos = math.cos(math.radians(grid.clat))

    def names(ks):
        return ", ".join(f"{details[evs[k].name]['callsign'] or details[evs[k].name]['hex']}" for k in ks)

    # tracks of the used flights, for the overlay
    def track_xy(fl):
        m = (fl.lat >= la0 - .05) & (fl.lat <= la1 + .05) & (fl.lon >= lo0 - .05) & (fl.lon <= lo1 + .05)
        return fl.lon[m], fl.lat[m]

    flights = {}
    for e in evs:
        d = details.get(e.name)
        if d:
            flights[e.name] = next(f for f in e.flights if f.fid == d["fid"])

    def heat(M, ks, title, fname):
        fig, axs = plt.subplots(1, 2, figsize=(12.5, 6.0), dpi=130, gridspec_kw={"width_ratios": [1.15, 1]})
        Z = grid.to_map(M)                      # (nx, ny), NaN outside the bbox
        lo, hi = float(np.nanmin(Z)), float(np.nanmax(Z))
        ax_, ay_ = grid.fwd.transform(answer["lon"], answer["lat"])
        for ai, ax in enumerate(axs):
            im = ax.pcolormesh(grid.xs / 1e3, grid.ys / 1e3, Z.T, shading="nearest", cmap="magma", vmin=lo, vmax=hi,
                               rasterized=True)
            for k in ks:
                fl = flights[evs[k].name]
                tx, ty = track_xy(fl)
                if len(tx):
                    px, py = grid.fwd.transform(tx, ty)
                    ax.plot(px / 1e3, py / 1e3, "-", color="white", lw=0.9, alpha=0.85)
                    if ai == 0:
                        m = len(px) // 2
                        ax.annotate(details[evs[k].name]["callsign"] or fl.hexid, (px[m] / 1e3, py[m] / 1e3),
                                    color="white", fontsize=7, ha="center", va="bottom")
            ax.plot(ax_ / 1e3, ay_ / 1e3, "o", mfc="none", mec="cyan", ms=12 if ai == 0 else 16, mew=1.6,
                    label="argmax")
            if ai == 0:
                ax.set_xlim(grid.xs.min() / 1e3, grid.xs.max() / 1e3)
                ax.set_ylim(grid.ys.min() / 1e3, grid.ys.max() / 1e3)
                ax.set_title(f"whole bbox ({grid.n:,} cells, {grid.step:g} m)", fontsize=9)
            else:
                ax.set_xlim(ax_ / 1e3 - 4, ax_ / 1e3 + 4)
                ax.set_ylim(ay_ / 1e3 - 4, ay_ / 1e3 + 4)
                ax.set_title("zoom: 8 x 8 km around the argmax", fontsize=9)
            ax.set_aspect("equal")
            ax.set_xlabel("east of bbox centre (km)")
            if ai == 0:
                ax.set_ylabel("north of bbox centre (km)")
        cb = fig.colorbar(im, ax=axs, shrink=0.8)
        cb.set_label("best summed score over one shared latency")
        axs[0].legend(loc="upper right", fontsize=7)
        fig.suptitle(title, fontsize=11)
        fig.savefig(out_dir / fname, bbox_inches="tight")
        plt.close(fig)

    if order:
        heat(M1, order[:1], f"1 plane ({names(order[:1])}): one track, two bands", "heatmap_1plane.png")
        heat(M2, order[:2], f"2 planes ({names(order[:2])})", "heatmap_2planes.png")
        heat(Mall, order, f"all {len(order)} planes ({names(order)})", "heatmap_all.png")

    # latency scan
    fig, ax = plt.subplots(figsize=(6.5, 3.4), dpi=130)
    ax.plot(lam_x, tot, color="#1f77b4")
    ax.axvline(lam_star, color="crimson", lw=1)
    ax.set_xlabel("latency (s)")
    ax.set_ylabel("summed score at the argmax cell")
    ax.set_title(f"latency scan near the optimum: {lam_star:.2f} s", fontsize=10)
    fig.tight_layout()
    fig.savefig(out_dir / "latency_scan.png")
    plt.close(fig)

    # one spectrogram per flyover, with the fitted curve and callsign
    for n, k in enumerate(order, 1):
        e = evs[k]
        d = details[e.name]
        fl = flights[e.name]
        fi, s = best_by_event[k]
        u = np.arange(-70.0, 70.0 + 1e-9, 0.1)
        tc, dc, R = score.model_ratios(obs_b, fl.sm, np.array([utc0 + e.t_e - lam_star]), u, c,
                                       atm.row(utc0 + e.t_e - lam_star, float(np.mean(fl.sm.h))))
        t_audio = (tc[0] - utc0) + lam_star + u
        # f0 at the optimum: the sweep value that maximises the brightness under the curve
        f0s = sweep_f0(e, 0.05, 0.05)
        Sx, A = score.score_brightness(R.astype(np.float64), u[0], u[1] - u[0], np.array([tc[0] - utc0]),
                                       np.array([lam_star]), f0s, e.harms, e.tau, e.B, e.fb0, e.dfb)
        f0b = float(f0s[int(A[0, 0])])
        sp = e.sp
        fig, (a0, ax) = plt.subplots(2, 1, figsize=(10, 7.4), dpi=130, sharex=True)
        for a_ in (a0, ax):
            a_.pcolormesh(sp.t, sp.f, e.S0, shading="auto", cmap="magma", vmin=0, vmax=4, rasterized=True)
            a_.set_xlim(sp.t[0], sp.t[-1])
            a_.set_ylim(sp.f[0], sp.f[-1])
            a_.set_ylabel("Hz")
        cols = ["#00e5ff", "#7CFC00", "#ffd400", "#ff77ff"]
        for hi_, h in enumerate(e.harms):
            ax.plot(t_audio, h * f0b * R[0], color=cols[hi_ % 4], lw=0.8, alpha=0.6, ls="--",
                    label=f"harmonic {int(h)}  ({h * f0b:.1f} Hz at rest)")
        ax.set_xlabel("audio time (s)")
        who = d["callsign"] or d["hex"]
        a0.set_title("whitened spectrogram, no overlay", fontsize=9)
        ax.set_title(f"{e.name}: {who}  {d['type'] or ''}  CPA {d['cpa_distance_m']:.0f} m, {d['ground_speed_ms']:.0f} m/s, "
                     f"score {d['score']:.2f} (null {d['null']:.2f}), latency {lam_star:.1f} s", fontsize=9)
        ax.legend(loc="upper right", fontsize=6)
        fig.tight_layout()
        safe = "".join(ch for ch in str(who) if ch.isalnum()) or "x"
        fig.savefig(out_dir / f"spectrogram_{n}_{safe}.png")
        plt.close(fig)


# ----------------------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", type=Path, help="cases/<case> (holds case.json and adsb.jsonl)")
    ap.add_argument("--out", type=Path, help="default results/<case>")
    ap.add_argument("--atmosphere", choices=("gfs", "none"), default="gfs",
                    help="GFS winds / temperature aloft (CONUS only) or still air")
    ap.add_argument("--c", type=float, default=343.0, help="speed of sound for still air / travel time")
    ap.add_argument("--max-events", type=int, default=MAX_EVENTS)
    ap.add_argument("--reuse-detect", action="store_true", help="reuse <out>/detect/result.pkl if present")
    ap.add_argument("--no-fallback-low", action="store_true",
                    help="never add low-confidence flyovers (default: add up to 2 when fewer than 2 high/medium)")
    ap.add_argument("--exclude-engines", default="", help="comma list of ICAO engine letters to drop, e.g. J,R")
    a = ap.parse_args(argv)
    a.exclude_engines = {x for x in a.exclude_engines.split(",") if x}
    case_dir = a.case.resolve()
    out = (a.out or (REPO / "results" / case_dir.name)).resolve()
    solve(case_dir, out, a)


if __name__ == "__main__":
    main()
