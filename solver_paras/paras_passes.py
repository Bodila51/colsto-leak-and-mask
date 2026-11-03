"""CLI front end for paras.py (NOT blind): one Paras-style check per aircraft pass, picked by geometry.

paras.py keeps per-case settings in its CASES table and allows one window per hex. This driver builds
the same settings from the command line instead, so one aircraft can be tested on several passes.
Passes come from --pass hex:lo:hi (real s after utc_start) or from --auto R_M:AGL_M, which keeps every
stretch of the ADS-B track within R_M of the camera and below AGL_M during the audio (geometry only,
no audio involved). Each pass then goes through paras.run_each: latency scan, spectrogram with the
predicted curve, +-6 km heat map, a verdict, and a combined map only if 2+ passes fit with one L.

    python solver_paras/paras_passes.py cases/case_B --out results/paras_setup_B2 --camera 50.8335,-0.29075 \
        --auto 3000:600 --fmin 40 --fmax 600 --f0min 40 --f0max 200 --harmonics 6 --event 38:130
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paras  # noqa: E402

PARAS_SRC = Path(paras.__file__).read_bytes()  # paras.py may be edited by others while this runs
from paras import ROOT, MIC_AGL, Setup, Terrain, load_tracks, to_ll, to_xy, track_heights  # noqa: E402

DEFAULTS = dict(fmin=40.0, fmax=600.0, f0min=40.0, f0max=200.0, harmonics=6, win=1.0, hop=0.125, dlog=0.01,
                ridge=15, half=40.0, min_frac=0.3)
L_CONSISTENT_S = 2.0  # passes "share" a latency if their best L agree within this


class Args(argparse.Namespace):
    """paras.py options this driver does not set read as None (paras falls back to its own defaults)."""

    def __getattr__(self, k):
        return None


def span(s):
    lo, hi = s.split(":")
    return float(lo), float(hi)


def reg(flight):
    """UK callsigns of private aircraft are the registration without the dash."""
    return f"G-{flight[1:]}" if re.fullmatch(r"G[A-Z]{4}", flight or "") else (flight or "")


def auto_passes(case_dir, camera, rmax, aglmax, gap=60.0, sep=30.0):
    """Stretches of each track within rmax (horizontal) and below aglmax during the audio, split at gaps > gap s.
    One pass per local distance minimum (the nearest report within +-sep s), so a stretch that goes abeam and
    later comes back closer gives two passes."""
    cs = json.loads((Path(case_dir) / "case.json").read_text())
    t0 = datetime.fromisoformat(cs["utc_start"].replace("Z", "+00:00")).timestamp()
    terrain = Terrain()
    lat0, lon0 = camera
    ground = float(terrain.elevation(np.array([lat0]), np.array([lon0]))[0])
    tracks = load_tracks(ROOT / cs["adsb"], t0 - 60, t0 + cs["duration_s"] + 60)
    offs = [np.median(tr["geom"][m] - tr["baro"][m]) for tr in tracks.values()
            for m in [np.isfinite(tr["geom"]) & np.isfinite(tr["baro"])] if m.any()]
    goff = float(np.median(offs)) if offs else 0.0
    out = []
    for hx, tr in tracks.items():
        h = track_heights(tr, terrain, terrain.geoid(lat0, lon0), goff)
        x, y = to_xy(tr["lat"], tr["lon"], lat0, lon0)
        d = np.hypot(x, y)
        rel = tr["t"] - t0
        idx = np.nonzero((rel >= 0) & (rel <= cs["duration_s"]) & (d < rmax) & ((h - ground < aglmax) | tr["ground"]))[0]
        if not len(idx):
            continue
        cuts = np.nonzero(np.diff(rel[idx]) > gap)[0]
        for seg in np.split(idx, cuts + 1):
            mins = [k for k in seg if d[k] == d[seg[np.abs(rel[seg] - rel[k]) <= sep]].min()]
            mins = [k for i, k in enumerate(mins) if i == 0 or rel[k] - rel[mins[i - 1]] > sep]
            for k in mins:
                out.append(dict(hex=hx, flight=tr["flight"], type=tr["type"], lo=float(rel[k] - sep),
                                hi=float(rel[k] + sep), stretch_s=[float(rel[seg[0]]), float(rel[seg[-1]])],
                                reports=len(seg), min_hor_m=round(float(d[k]), 1), min_at_s=round(float(rel[k]), 1),
                                agl_at_min_m=round(float(h[k] - ground), 1)))
    return sorted(out, key=lambda p: p["min_at_s"]), t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--camera", required=True, help="lat,lon of the revealed camera")
    ap.add_argument("--pass", dest="passes", action="append", default=[], help="hex:lo:hi, real s after utc_start")
    ap.add_argument("--auto", help="R_M:AGL_M, add every pass within R_M horizontal and below AGL_M")
    ap.add_argument("--sep", type=float, default=30.0, help="auto: +-s for a local distance minimum (and its CPA window)")
    ap.add_argument("--event", type=span, help="lo:hi audio s of a clear stack (verdict rule 5 reference)")
    ap.add_argument("--overview", type=span, help="lo:hi audio s for overview.png")
    ap.add_argument("--no-despike", action="store_true")
    ap.add_argument("--note", action="append", default=[], help="free text copied into summary.json")
    for k, v in DEFAULTS.items():
        ap.add_argument("--" + k.replace("_", "-"), type=type(v), default=v)
    ap.add_argument("--lmin", type=float, default=0.0)
    ap.add_argument("--lmax", type=float, default=60.0)
    ap.add_argument("--lstep", type=float, default=0.25)
    ap.add_argument("--extent", type=float, default=6000.0)
    ap.add_argument("--step", type=float, default=100.0)
    a = Args(**vars(ap.parse_args()))
    camera = tuple(float(v) for v in a.camera.split(","))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    # passes: explicit, then by geometry
    picks = [dict(hex=hx, lo=float(lo), hi=float(hi), how="--pass") for hx, lo, hi in (p.split(":") for p in a.passes)]
    if a.auto:
        rmax, aglmax = span(a.auto)
        found, t0 = auto_passes(a.case_dir, camera, rmax, aglmax, sep=a.sep)
        for p in found:
            print(f"auto: {p['hex']} {p['flight']:7s} {p['type']:5s} stretch {p['stretch_s'][0]:7.1f}-{p['stretch_s'][1]:7.1f} s "
                  f"({p['reports']} reports)  local min {p['min_hor_m']:.0f} m at {p['min_at_s']:.0f} s, "
                  f"{p['agl_at_min_m']:.0f} m AGL")
            picks.append(dict(p, how=f"--auto {a.auto}"))

    # one Setup per pass (paras.Setup takes one window per hex); the spectrogram is computed once
    spec_cache = {}
    whitened = paras.whitened_spectrogram

    def cached(x, fs, *args):
        if args not in spec_cache:
            spec_cache[args] = whitened(x, fs, *args)
        return spec_cache[args]

    paras.whitened_spectrogram = cached
    cfg = dict(camera=camera, picks=[], cpa_window={}, despike=not a.no_despike, event=a.event, clock_l=None,
               voice=None, extend=False, shared_l=False, overview=a.overview,
               yticks=[t for t in (40, 50, 70, 100, 140, 200, 300, 420, 600) if a.fmin <= t <= a.fmax])
    S = None
    for p in picks:
        c = dict(cfg, picks=[p["hex"]], cpa_window={p["hex"]: (p["lo"], p["hi"])})
        s = Setup(a.case_dir, a, c)
        ac = s.ac[0]
        call = ac["flight"] or ac["hex"]
        ac["reg"] = reg(call)
        hhmm = f"{datetime.fromtimestamp(ac['cpa_te'], timezone.utc):%H%M}"
        while S is not None and any(b["flight"] == f"{call}_{hhmm}" for b in S.ac):
            hhmm += "b"
        ac["flight"] = f"{call}_{hhmm}"  # file tag, one per pass
        ac["hex"] = f"{ac['hex']}/{hhmm}"  # run_each keys its latencies by hex
        ac["pick"] = p
        if S is None:
            S = s
            S.ac = []
        S.ac.append(ac)
    S.cfg = cfg
    paras.label = lambda ac: (f"{ac['reg']} ({ac['type']}), pass "
                              f"{datetime.fromtimestamp(ac['cpa_te'], timezone.utc):%H:%M:%S} UTC")
    print(f"spectrogram {S.W.shape}, {len(S.ac)} passes  ({time.time() - t_start:.1f}s)")

    # 1. latency scans at the known camera (as paras.main)
    Ls = np.arange(a.lmin, a.lmax + 1e-9, a.lstep)
    cam = (np.zeros(1), np.zeros(1), np.full(1, S.cam_z))
    for ac in S.ac:
        ac["scan"] = np.array([S.fit(ac, *cam, L)[0][0] for L in Ls])
        mx = f"scan max {np.nanmax(ac['scan']):.2f} at L {Ls[np.nanargmax(ac['scan'])]:.2f}" \
            if np.isfinite(ac["scan"]).any() else "no scored frames"
        print(f"{ac['flight']:11s} {ac['type']:5s} CPA {ac['cpa_tr'] - S.t0:6.1f}s  {ac['cpa_r']:.0f} m slant  "
              f"track {ac['track_deg']:.0f}  lateral {ac['lateral']:.0f} m  reports {ac['first_report_s']:+.1f}"
              f"..{ac['last_report_s']:+.1f}s rel CPA  {mx}")

    # 2-3. spectrogram, heat map and verdict per pass (paras.run_each)
    xs = np.arange(-a.extent, a.extent + 1e-6, a.step)
    ys = xs.copy()
    GX, GY = np.meshgrid(xs, ys)
    lat, lon = to_ll(GX.ravel(), GY.ravel(), S.lat0, S.lon0)
    GZ = S.terrain.elevation(lat, lon) + MIC_AGL
    git = lambda *c: subprocess.run(["git", *c], cwd=ROOT, capture_output=True).stdout
    head = git("rev-parse", "--short", "HEAD").decode().strip()
    summary = dict(note="NOT blind: known (revealed) camera, hand-picked aircraft, Paras-style setup. "
                        "Passes picked by ADS-B geometry only.",
                   camera=dict(lat=camera[0], lon=camera[1], z_m=S.cam_z),
                   params=dict(vars(a), event=list(a.event) if a.event else None),
                   paras_py_sha256=hashlib.sha256(PARAS_SRC).hexdigest(), git_head=head,
                   paras_py_is_head=PARAS_SRC.replace(b"\r\n", b"\n") == git("show", "HEAD:solver_paras/paras.py"),
                   passes_picked=[ac["pick"] for ac in S.ac],
                   aircraft=[], skipped=[])
    grid = dict(lat0=S.lat0, lon0=S.lon0, x_m=xs.tolist(), y_m=ys.tolist(), shape=list(GX.shape),
                layout="heat[iy, ix], y north from -extent, x east from -extent (origin lower)", aircraft=[])
    paras.run_each(S, a, Ls, cam, (xs, ys, GX, GY, GZ), out, summary, grid)

    # one latency-scan figure per pass, next to the all-pass one
    best = {r["hex"]: r["L_s"] for r in summary["aircraft"]}
    acs = S.ac
    for ac in acs:
        S.ac = [ac]
        L = best.get(ac["hex"])
        paras.plot_scan(S, Ls, [ac["hex"]] if L is not None else [], {ac["hex"]: "no scored frames"},
                        {ac["hex"]: L} if L is not None else {}, out / f"scan_{ac['flight']}.png")
    S.ac = acs

    # the combined map needs one latency for the passes that fit
    fits = [r for r in summary["aircraft"] if r["verdict"] == "fits"]
    if len(fits) >= 2:
        Lf = [r["L_s"] for r in fits]
        if max(Lf) - min(Lf) > L_CONSISTENT_S:
            (out / "heat_combined.png").unlink(missing_ok=True)
            (out / "heat_combined.npy").unlink(missing_ok=True)
            grid.pop("combined", None)
            summary["combined"] = (f"not made: the {len(fits)} passes that fit disagree on L "
                                   f"({', '.join(f'{x:.2f}' for x in Lf)} s; needs within {L_CONSISTENT_S:g} s)")
    by_hex = {ac["hex"]: ac for ac in acs}
    for r in summary["aircraft"]:
        ac = by_hex[r["hex"]]
        r["icao"] = r["hex"].split("/")[0]
        r["registration"] = ac["reg"]
        r["camera_vs_peak_fit"] = [r["fit_at_camera"], r["fit_peak"]]
        # the camera reflected across the track line at CPA: where the second (mirror) band crosses
        i = int(np.argmin(np.abs(ac["te"] - ac["cpa_te"])))
        hd = math.radians(ac["track_deg"])
        lat_s = ac["X"][i] * math.cos(hd) - ac["Y"][i] * math.sin(hd)
        mx, my = 2 * lat_s * math.cos(hd), -2 * lat_s * math.sin(hd)
        r["mirror_xy_m"] = [round(mx, 1), round(my, 1)]
        r["peak_to_mirror_m"] = round(math.hypot(r["peak"]["x_m"] - mx, r["peak"]["y_m"] - my), 1)
    for sk in summary["skipped"]:
        ac = by_hex[sk["hex"]]
        nmax = max(int(S.fit(ac, *cam, L)[2][0]) for L in Ls)
        need = int(round(a.min_frac * 2 * a.half / S.hop_s))
        sk.update(registration=ac["reg"], type=ac["type"], verdict="does not fit", cpa_slant_m=ac["cpa_r"],
                  reason=f"untestable: ADS-B covers at most {nmax} of the {need} frames needed "
                         f"(min_frac {a.min_frac} of the +-{a.half:g} s window) at any L in {a.lmin:g}-{a.lmax:g} s",
                  adsb_reports_rel_cpa_s=[ac["first_report_s"], ac["last_report_s"]])
    summary["table"] = [dict(pass_=r["flight"], registration=r["registration"], type=r["type"], L_s=r["L_s"],
                             cpa_slant_m=round(r["cpa_slant_m"]), fit_camera=round(r["fit_at_camera"], 3),
                             fit_peak=round(r["fit_peak"], 3), peak_dist_m=r["peak_error_m"], verdict=r["verdict"])
                        for r in summary["aircraft"]] + \
                       [dict(pass_=s["flight"], registration=s["registration"], type=s["type"], L_s=None,
                             cpa_slant_m=round(s["cpa_slant_m"]), fit_camera=None, fit_peak=None, peak_dist_m=None,
                             verdict=s["verdict"]) for s in summary["skipped"]]
    summary["notes"] = a.note
    summary["command"] = " ".join(["python", "solver_paras/paras_passes.py"] + sys.argv[1:])
    grid["camera_xy_m"] = [0.0, 0.0]
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    (out / "grid.json").write_text(json.dumps(grid))
    print(f"combined: {summary['combined'] if isinstance(summary['combined'], str) else summary['combined']['peak_error_m']}")
    print(f"done  ({time.time() - t_start:.1f}s)")


if __name__ == "__main__":
    main()
