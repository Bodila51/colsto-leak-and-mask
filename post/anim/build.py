#!/usr/bin/env python
"""Build the assets of the post animation from one data file.

    python post/anim/build.py [--data post/anim_data.json | --preview]

Reads the real monitor-1 audio and the data file, writes post/anim/build/:
  data.json          the data used (the page loads this one)
  spec_before.png    spectrogram of post/monitor1_recording.mp4 (0-450 Hz, magma)
  spec_after.png     spectrogram of the safe-copy audio, same scale
  spec_meta.json     time and frequency axes of the two PNGs
  map_vectors.json   OpenStreetMap vectors for the search box (Natural Earth coastline if Overpass is down)
  preview_safe.wav   preview only: the monitor-1 audio with the preview tone curves cut out

Needs: python 3.12, numpy, pillow, matplotlib (magma table), ffmpeg on PATH. No other packages.
Map data: (c) OpenStreetMap contributors, ODbL, fetched from the public Overpass API. No tile servers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image

ANIM = Path(__file__).resolve().parent
POST = ANIM.parent
ROOT = POST.parent
BUILD = ANIM / "build"

SR = 16000
CLIP_S = 20.0
HOP = 200                                    # 0.0125 s
DEFAULT_SPEC = dict(f_min_hz=0.0, f_max_hz=450.0, window_s=1.0)   # colsto style; override with data["spectrogram"]
UA = "flyover-leak-post/1.0 (github.com/Bodila51/colsto-leak-and-mask)"


def log(*a):
    print("[build]", *a, flush=True)


# ------------------------------------------------------------------------------------------ audio
def decode(path: Path, ss: float | None = None) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype="<f4").astype(np.float64)
    n = int(CLIP_S * SR)
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)), mode="reflect")
    return x[:n]


def spec_cfg(data: dict | None) -> dict:
    c = dict(DEFAULT_SPEC)
    c.update((data or {}).get("spectrogram", {}))
    return c


def stft_db(x: np.ndarray, spec: dict | None = None):
    """Power spectrogram in dB, rows = f_min..f_max (about 1 Hz-wide window bins for the default band)."""
    c = dict(DEFAULT_SPEC, **(spec or {}))
    nper = 1 << int(round(math.log2(c["window_s"] * SR)))
    nfft = nper * 4
    while (c["f_max_hz"] - c["f_min_hz"]) / (SR / nfft) > 1100 and nfft > nper:
        nfft //= 2
    win = np.hanning(nper)
    xp = np.pad(x, (nper // 2, nper // 2), mode="reflect")
    n_cols = int(round(len(x) / HOP))
    fr = np.fft.rfftfreq(nfft, 1 / SR)
    keep = (fr >= c["f_min_hz"]) & (fr <= c["f_max_hz"])
    cols = np.empty((int(keep.sum()), n_cols))
    for j in range(n_cols):
        seg = xp[j * HOP: j * HOP + nper]
        cols[:, j] = np.abs(np.fft.rfft(seg * win, nfft)[keep]) ** 2
    return 10 * np.log10(cols + 1e-12), fr[keep]


def line_enhance(D: np.ndarray, df: float, width_hz: float = 30.0) -> np.ndarray:
    """D minus its median over +-width/2: narrow steady tones stand out, broad voice blobs sink."""
    k = int(round(width_hz / df)) | 1
    pad = np.pad(D, ((k // 2, k // 2), (0, 0)), mode="edge")
    out = np.empty_like(D)
    for a in range(0, D.shape[1], 200):  # column blocks keep the temporary arrays small
        out[:, a:a + 200] = D[:, a:a + 200] - np.median(sliding_window_view(pad[:, a:a + 200], k, axis=0), axis=-1)
    return out


def _gauss(s: float) -> np.ndarray:
    r = int(3 * s) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / s) ** 2)
    return k / k.sum()


def blur(a: np.ndarray, sigma: float, axis: int) -> np.ndarray:
    k = _gauss(sigma)
    r = len(k) // 2
    pad = np.pad(a, [(r, r) if i == axis else (0, 0) for i in range(2)], mode="edge")
    return np.tensordot(sliding_window_view(pad, len(k), axis=axis), k, axes=([-1], [0]))


def spectrogram(path: Path, spec: dict | None = None, ref=None):
    c = dict(DEFAULT_SPEC, **(spec or {}))
    x = decode(path)
    D, fr = stft_db(x, c)
    E = line_enhance(D, fr[1] - fr[0], max(30.0, (c["f_max_hz"] - c["f_min_hz"]) / 30))
    if ref is None:  # constants come from the "before" audio; the "after" image reuses them
        e = c.get("e_range_db") or ([6.0, 26.0] if c["f_max_hz"] <= 600 else np.percentile(E, [90, 99.9]).tolist())
        ref = dict(e=e, d=np.percentile(D, [30, 99.8]).tolist())  # e: dB above the local median
    En = np.clip((E - ref["e"][0]) / (ref["e"][1] - ref["e"][0]), 0, 1)
    Dn = np.clip((D - ref["d"][0]) / (ref["d"][1] - ref["d"][0]), 0, 1)
    b = np.clip(0.9 * En ** 0.9 + 0.2 * Dn ** 1.8, 0, 1)
    b = np.clip(b + 0.9 * blur(blur(b, 2.5, 0), 2.0, 1), 0, 1)[::-1]  # a little bloom; top row = 450 Hz
    import matplotlib
    lut = (matplotlib.colormaps["magma"](np.linspace(0, 1, 256))[:, :3] * 255).astype(np.uint8)
    img = lut[np.round(b * 255).astype(np.uint8)]
    return img, ref, fr


# ------------------------------------------------------------------------------------ preview safe copy
def notch_curves(x: np.ndarray, curves: list[dict], half_hz: float = 3.4, n: int = 16384) -> np.ndarray:
    """STFT mask that cuts a band around each tone curve (what a safe copy does to the plane tone)."""
    hop = n // 4
    win = np.sqrt(np.hanning(n + 1)[:-1])
    pad = n
    xp = np.pad(x, (pad, pad), mode="reflect")
    out = np.zeros_like(xp)
    norm = np.zeros_like(xp)
    fr = np.fft.rfftfreq(n, 1 / SR)
    for s in range(0, len(xp) - n, hop):
        t = (s + n / 2 - pad) / SR
        F = np.fft.rfft(xp[s:s + n] * win)
        m = np.ones_like(fr)
        for c in curves:
            if c["t_s"][0] - 0.5 <= t <= c["t_s"][-1] + 0.5:
                f = float(np.interp(t, c["t_s"], c["f_hz"]))
                d = np.abs(fr - f)
                m *= np.where(d < half_hz, 0.0, np.where(d < half_hz * 1.8, 0.5 - 0.5 * np.cos(np.pi * (d - half_hz) / (half_hz * 0.8)), 1.0))
        out[s:s + n] += np.fft.irfft(F * m, n) * win
        norm[s:s + n] += win ** 2
    y = (out / np.maximum(norm, 1e-6))[pad:-pad]
    return y


def write_wav(path: Path, x: np.ndarray):
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    hdr = b"RIFF" + (36 + pcm.nbytes).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") \
        + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + SR.to_bytes(4, "little") + (SR * 2).to_bytes(4, "little") \
        + (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + b"data" + pcm.nbytes.to_bytes(4, "little")
    path.write_bytes(hdr + pcm.tobytes())


# ------------------------------------------------------------------------------------------- map
def dp(points, tol_deg):
    """Douglas-Peucker, iterative."""
    if len(points) < 3:
        return points
    pts = np.asarray(points)
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        d = q - p
        L = math.hypot(*d)
        seg = pts[a + 1:b]
        dist = np.hypot(*(seg - p).T) if L == 0 else np.abs(d[0] * (seg[:, 1] - p[1]) - d[1] * (seg[:, 0] - p[0])) / L
        i = int(np.argmax(dist))
        if dist[i] > tol_deg:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return pts[keep].tolist()


def stitch(segments):
    """Join way pieces end to end into rings (for multipolygon outers)."""
    segs = [list(s) for s in segments if len(s) > 1]
    rings = []
    while segs:
        ring = segs.pop()
        grown = True
        while grown and ring[0] != ring[-1]:
            grown = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                grown = True
                break
        rings.append(ring)
    return rings


OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]


def overpass(bbox):
    s, w, n, e = bbox
    bb = f"({s},{w},{n},{e})"
    q = f"""[out:json][timeout:120];
(
 way["natural"="coastline"]{bb};
 way["highway"~"^(motorway|trunk|primary)$"]{bb};
 way["aeroway"="runway"]{bb};
 way["natural"="water"]["name"]{bb};
 relation["natural"="water"]["name"]{bb};
 node["place"~"^(city|town)$"]["name"]{bb};
);
out geom;"""
    cache = BUILD / f"overpass_{hashlib.md5(q.encode()).hexdigest()[:10]}.json"
    if cache.exists():
        log("overpass: cached", cache.name)
        return json.loads(cache.read_text(encoding="utf-8"))
    last = None
    for rnd in range(2):
        for url in OVERPASS:
            try:
                log("overpass:", url)
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": UA})
                d = json.loads(urllib.request.urlopen(req, timeout=200).read())
                if d.get("elements"):
                    cache.write_text(json.dumps(d), encoding="utf-8")
                    return d
            except Exception as ex:  # noqa: BLE001
                last = ex
                log("  failed:", ex)
        time.sleep(5)
    raise RuntimeError(f"overpass failed: {last}")


def vectors_from_overpass(d, bbox):
    tol = 12 / 111320.0
    lines, polys, places = [], [], []
    for el in d["elements"]:
        t = el.get("tags", {})
        if el["type"] == "node":
            places.append(dict(name=t["name"], kind=t["place"], lat=el["lat"], lon=el["lon"]))
        elif el["type"] == "way":
            pts = [[p["lat"], p["lon"]] for p in el.get("geometry", [])]
            if len(pts) < 2:
                continue
            if t.get("natural") == "coastline":
                lines.append(dict(k="coast", p=dp(pts, tol)))
            elif t.get("natural") == "water":
                if pts[0] == pts[-1]:
                    polys.append(dict(k="water", p=dp(pts, tol * 2)))
                else:
                    lines.append(dict(k="waterline", p=dp(pts, tol)))
            elif t.get("aeroway") == "runway":
                lines.append(dict(k="runway", p=pts))
            elif t.get("highway") in ("motorway", "trunk"):
                lines.append(dict(k="major", p=dp(pts, tol)))
            elif t.get("highway") == "primary":
                lines.append(dict(k="primary", p=dp(pts, tol)))
        elif el["type"] == "relation":
            outers = [[[p["lat"], p["lon"]] for p in m["geometry"]] for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
            inners = [[[p["lat"], p["lon"]] for p in m["geometry"]] for m in el.get("members", []) if m.get("role") == "inner" and m.get("geometry")]
            for ring in stitch(outers) + stitch(inners):
                if len(ring) > 3 and ring[0] == ring[-1]:
                    polys.append(dict(k="water", p=dp(ring, tol * 2)))
    r5 = lambda pts: [[round(a, 5), round(b, 5)] for a, b in pts]  # noqa: E731
    for it in lines + polys:
        it["p"] = r5(it["p"])
    # keep a handful of the biggest places, away from the box edge
    s, w, n, e = bbox
    mx, my = (e - w) * 0.1, (n - s) * 0.1
    places = [p for p in places if w + mx < p["lon"] < e - mx and s + my < p["lat"] < n - my]
    places.sort(key=lambda p: (p["kind"] != "city", p["name"]))
    return dict(source="OpenStreetMap via Overpass API", lines=lines, polys=polys, places=places[:6])


def vectors_from_natural_earth(bbox):
    url = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson"
    cache = BUILD / "ne_10m_coastline.geojson"
    if not cache.exists():
        log("natural earth:", url)
        cache.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=300).read())
    g = json.loads(cache.read_text(encoding="utf-8"))
    s, w, n, e = bbox
    lines = []
    for f in g["features"]:
        geo = f["geometry"]
        parts = geo["coordinates"] if geo["type"] == "MultiLineString" else [geo["coordinates"]]
        for part in parts:
            run = []
            for lon, lat in part:
                if s - 0.2 <= lat <= n + 0.2 and w - 0.2 <= lon <= e + 0.2:
                    run.append([round(lat, 5), round(lon, 5)])
                elif run:
                    if len(run) > 1:
                        lines.append(dict(k="coast", p=run))
                    run = []
            if len(run) > 1:
                lines.append(dict(k="coast", p=run))
    return dict(source="Natural Earth 10m coastline (public domain)", lines=lines, polys=[], places=[])


def clip_runs(pts, bbox):
    """Liang-Barsky: the parts of a polyline (lat, lon) inside bbox, as runs that start/end on the box edge where it is crossed."""
    s, w, n, e = bbox
    runs, cur = [], []
    for (la0, lo0), (la1, lo1) in zip(pts[:-1], pts[1:]):
        dx, dy = lo1 - lo0, la1 - la0
        t0, t1, ok = 0.0, 1.0, True
        for p, q in ((-dx, lo0 - w), (dx, e - lo0), (-dy, la0 - s), (dy, n - la0)):
            if p == 0:
                if q < 0:
                    ok = False
                    break
            else:
                r = q / p
                if p < 0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
        if not ok or t0 > t1:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
            continue
        a, b = [la0 + t0 * dy, lo0 + t0 * dx], [la0 + t1 * dy, lo0 + t1 * dx]
        if not cur:
            cur = [a]
        cur.append(b)
        if t1 < 1.0:
            runs.append(cur)
            cur = []
    if len(cur) > 1:
        runs.append(cur)
    return runs


def sea_polygons(coast_lines, bbox):
    """Fill the water side of OSM coastlines (water is on the right of the way direction) up to the box edge."""
    s, w, n, e = bbox
    W, H = e - w, n - s
    key = lambda p: (round(p[0], 5), round(p[1], 5))  # noqa: E731
    # join ways end to start (coastline ways all run the same way)
    ways = [list(l["p"]) for l in coast_lines]
    starts = {key(wy[0]): i for i, wy in enumerate(ways)}
    used, chains = set(), []
    for i, wy in enumerate(ways):
        if i in used:
            continue
        # walk back to the true start of this chain
        j = i
        seen = {j}
        ends = {key(w2[-1]): k for k, w2 in enumerate(ways)}
        while key(ways[j][0]) in ends and ends[key(ways[j][0])] not in seen:
            j = ends[key(ways[j][0])]
            seen.add(j)
        chain, cur = [], j
        while cur is not None and cur not in used:
            used.add(cur)
            chain += ways[cur] if not chain else ways[cur][1:]
            cur = starts.get(key(ways[cur][-1]))
        chains.append(chain)

    def perim(p):
        la, lo = p
        eps = 1e-6
        if abs(la - n) < eps:
            return lo - w
        if abs(lo - e) < eps:
            return W + (n - la)
        if abs(la - s) < eps:
            return W + H + (e - lo)
        if abs(lo - w) < eps:
            return 2 * W + H + (la - s)
        return None

    corners = [(0.0, [n, w]), (W, [n, e]), (W + H, [s, e]), (2 * W + H, [s, w])]
    P = 2 * (W + H)
    out = []
    for ch in chains:
        for run in clip_runs(ch, bbox):
            a, b = perim(run[0]), perim(run[-1])
            if a is None or b is None:
                continue
            s0, s1 = b, a
            if s1 <= s0:
                s1 += P
            ring = list(run)
            for off in (0.0, P):
                for cs, cp in corners:
                    if s0 < cs + off < s1:
                        ring.append(cp)
            ring.append(run[0])
            out.append(dict(k="sea", p=[[round(x, 5), round(y, 5)] for x, y in dp(ring, 12 / 111320.0)]))
    return out


def build_map(bbox, force_ne=False):
    if not force_ne:
        try:
            v = vectors_from_overpass(overpass(bbox), bbox)
            v["polys"] += sea_polygons([l for l in v["lines"] if l["k"] == "coast"], bbox)
            log(f"map: {len(v['lines'])} lines, {len(v['polys'])} polygons, {len(v['places'])} places from OpenStreetMap")
            return v
        except Exception as ex:  # noqa: BLE001
            log("overpass unavailable, falling back to Natural Earth:", ex)
    v = vectors_from_natural_earth(bbox)
    v["polys"] += sea_polygons(v["lines"], bbox)
    log(f"map: {len(v['lines'])} coastline pieces from Natural Earth")
    return v


# ------------------------------------------------------------------------------------------ validation
def validate(d: dict):
    """Fail early, with a message that names the field, if the data file does not follow post/anim/DATA.md."""
    def need(cond, msg):
        if not cond:
            sys.exit(f"data error: {msg} (see post/anim/DATA.md)")
    need(isinstance(d.get("clip", {}).get("utc_start"), str), "clip.utc_start missing")
    need(d.get("safe_audio"), "safe_audio missing")
    need(d.get("curves"), "curves missing")
    for c in d["curves"]:
        need(len(c["t_s"]) == len(c["f_hz"]) >= 2, f"curve {c.get('harmonic')}: t_s and f_hz must have the same length >= 2")
    need(len(d.get("aircraft", [])) >= 1, "aircraft[0] missing")
    for i, a in enumerate(d["aircraft"]):
        need(a.get("label") and a.get("closest_m") is not None, f"aircraft[{i}] needs label and closest_m")
        need(len(a["track"]["lat"]) == len(a["track"]["lon"]) >= 2, f"aircraft[{i}].track.lat/lon")
    m = d.get("map", {})
    for k in ("bbox", "lat", "lon", "found", "true", "error_m"):
        need(k in m, f"map.{k} missing")
    need("heat_1" in m and ("heat_2" in m or "heat_all" in m), "map.heat_1 and map.heat_2 (or heat_all) needed")
    nr, nc = len(m["lat"]), len(m["lon"])
    for k in ("heat_1", "heat_2", "heat_all"):
        if k in m:
            need(len(m[k]) == nr and all(len(r) == nc for r in m[k]), f"map.{k} must be len(lat) x len(lon) = {nr} x {nc}")
    if m.get("safe", {}).get("heat") is not None:
        need(len(m["safe"]["heat"]) == nr, "map.safe.heat must be len(lat) x len(lon)")
    la1, lo1, la2, lo2 = m["found"]["lat"], m["found"]["lon"], m["true"]["lat"], m["true"]["lon"]
    h = 2 * 6371000 * math.asin(math.sqrt(math.sin(math.radians(la2 - la1) / 2) ** 2 + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(math.radians(lo2 - lo1) / 2) ** 2))
    if abs(h - m["error_m"]) > max(25, 0.05 * m["error_m"]):
        log(f"WARNING: map.error_m = {m['error_m']} but found-to-true distance is {h:.0f} m")


# ------------------------------------------------------------------------------------------- main
def resolve(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--data", default=str(POST / "anim_data.json"))
    g.add_argument("--preview", action="store_true")
    ap.add_argument("--force-natural-earth", action="store_true")
    a = ap.parse_args()
    data_path = ANIM / "preview_data.json" if a.preview else resolve(a.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    validate(data)
    BUILD.mkdir(exist_ok=True)
    log("data:", data_path, "(PREVIEW)" if data.get("preview") else "(final)")

    # spectrograms
    t0 = time.time()
    sp = spec_cfg(data)
    img_b, ref, fr = spectrogram(POST / "monitor1_recording.mp4", sp)
    Image.fromarray(img_b).save(BUILD / "spec_before.png", optimize=False)
    safe = resolve(data["safe_audio"])
    if data.get("preview"):
        x = decode(POST / "monitor1_recording.mp4")
        wide = sp["f_max_hz"] > 600
        write_wav(safe, notch_curves(x, data["curves"], half_hz=34.0 if wide else 3.4, n=4096 if wide else 16384))
        log("preview safe copy ->", safe)
    if not safe.exists():
        sys.exit(f"safe-copy audio not found: {safe}")
    img_a, _, _ = spectrogram(safe, sp, ref)
    Image.fromarray(img_a).save(BUILD / "spec_after.png", optimize=False)
    meta = dict(n_cols=img_b.shape[1], n_rows=img_b.shape[0], hop_s=HOP / SR, t_start_s=0.0, f_max_hz=sp["f_max_hz"], f_min_hz=sp["f_min_hz"],
                window_s=sp["window_s"], e_range_db=ref["e"],
                col_time_note="column j is centred on j*hop_s seconds of the clip; top row = f_max_hz")
    (BUILD / "spec_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    log(f"spectrograms {img_b.shape[1]}x{img_b.shape[0]} in {time.time() - t0:.1f}s")

    # map
    bbox = data["map"]["bbox"]
    (BUILD / "map_vectors.json").write_text(json.dumps(build_map(bbox, a.force_natural_earth)), encoding="utf-8")

    shutil.copyfile(data_path, BUILD / "data.json")
    log("done")


if __name__ == "__main__":
    main()
