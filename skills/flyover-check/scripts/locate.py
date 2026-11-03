# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "soundfile", "matplotlib"]
# ///
"""flyover-check, mode 3: find where it was recorded.

  ONLY for (1) your own recordings, (2) public challenges such as colsto's, or (3) recordings whose owner
  agreed. It is not for finding where a stranger is. Anything else: stop.

  uv run locate.py INPUT --consent own|public-challenge|owner-agreed [--challenge-name NAME]
                   (--utc-start 2026-09-08T14:03:10Z | --date 2026-09-08)
                   (--bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX | --center LAT,LON [--radius-km 100] | --country BG)
                   [--dry-run] [--dopploc-dir PATH] [--workers N]

It drives doppler-locate's own pipeline (github.com/digitalis-p/doppler-locate, MIT):

  1. dopploc detect     find the plane flyovers in your file (no flight data needed)
  2. dopploc fetch-adsb download that UTC day of adsb.lol history (about 3.9 GB, once)
  3. dopploc ingest     cut it down to your region
  4. dopploc search     every pair of real flights that could explain the two flyovers
  5. dopploc score      model the sound each pair would make at each place and rank them

and reports rank 1, the two flights behind it, and how far ahead of rank 2 it is. It needs the date (the
UTC time of the first sample makes it much faster and safer), a region, and two flyovers from planes on
different lines. With one usable flyover it says so and stops. --dry-run prints every command and
changes nothing.
"""
import argparse
import csv
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fo_media  # noqa: E402

RULE = """MODE: find where it was recorded
This mode is ONLY for:
  1. your own recordings,
  2. public challenges (such as colsto's), or
  3. recordings whose owner has agreed.
It is not for locating other people. If the recording is not one of these, stop here."""

ASK = """NOT RUNNING: no consent word was given, so nothing was analysed, downloaded or written.
Which of these is true?
  own               - I made this recording.
  public-challenge  - it is part of a public challenge that invites this (name it with --challenge-name).
  owner-agreed      - the person who recorded it has agreed to have it located.
If none applies, stop: this tool will not be used to find where someone else is.
If one applies, run again with --consent <that word>."""

REPO_URL = "https://github.com/Bodila51/colsto-leak-and-mask"
DOPPLOC_URL = "https://github.com/digitalis-p/doppler-locate"
PATCH_URL = REPO_URL + "/blob/main/solver/windows_patches.diff"
ADSB_FIRST_DAY = date(2023, 1, 1)         # adsb.lol history starts in 2023
ADSB_READY_UTC = (3, 30)                  # a day's history appears the next day at about 03:30 UTC
CONUS = (24.5, -125.0, 49.4, -66.9)       # lat_min, lon_min, lat_max, lon_max (upstream grid is US-only)
HEAVY_SIDE_KM = 320                       # measured: a 300 x 300 km box peaked at about 5 GB
REFUSE_SIDE_KM = 700                      # larger than this needs --allow-large
MAX_PROP_SPEED_MS = 215                   # faster sources are almost always jets, which the search leaves out

# Approximate boxes [lat_min, lon_min, lat_max, lon_max]. Mainland only, generous on purpose.
COUNTRY_BOX = {
    "BG": (41.2, 22.3, 44.3, 28.7), "DE": (47.2, 5.8, 55.1, 15.1), "GB": (49.8, -8.7, 60.9, 1.8),
    "IE": (51.3, -10.7, 55.5, -5.9), "FR": (41.3, -5.2, 51.2, 9.7), "ES": (35.9, -9.4, 43.9, 4.4),
    "PT": (36.9, -9.6, 42.2, -6.1), "IT": (35.4, 6.6, 47.1, 18.6), "NL": (50.7, 3.3, 53.6, 7.3),
    "BE": (49.5, 2.5, 51.6, 6.5), "CH": (45.8, 5.9, 47.9, 10.6), "AT": (46.3, 9.5, 49.1, 17.2),
    "PL": (49.0, 14.1, 54.9, 24.2), "CZ": (48.5, 12.0, 51.1, 18.9), "SK": (47.7, 16.8, 49.7, 22.6),
    "HU": (45.7, 16.1, 48.6, 22.9), "RO": (43.6, 20.2, 48.3, 29.7), "GR": (34.8, 19.3, 41.8, 28.3),
    "RS": (42.2, 18.8, 46.2, 23.0), "HR": (42.3, 13.4, 46.6, 19.5), "SI": (45.4, 13.3, 46.9, 16.6),
    "DK": (54.5, 8.0, 57.8, 15.2), "NO": (57.9, 4.5, 71.2, 31.2), "SE": (55.3, 10.9, 69.1, 24.2),
    "FI": (59.7, 20.5, 70.1, 31.6), "EE": (57.5, 21.8, 59.7, 28.2), "LV": (55.7, 21.0, 58.1, 28.3),
    "LT": (53.9, 20.9, 56.5, 26.9), "UA": (44.3, 22.1, 52.4, 40.2), "TR": (35.8, 26.0, 42.1, 44.8),
    "US": (24.5, -125.0, 49.4, -66.9), "CA": (41.7, -141.0, 60.0, -52.6), "MX": (14.5, -118.4, 32.7, -86.7),
    "BR": (-33.8, -74.0, 5.3, -34.8), "AR": (-55.1, -73.6, -21.8, -53.6), "AU": (-44.0, 112.0, -10.0, 154.0),
    "NZ": (-47.5, 166.0, -34.0, 178.6), "JP": (24.0, 122.9, 45.6, 146.0), "KR": (33.1, 124.6, 38.7, 131.9),
    "IN": (6.7, 68.1, 35.5, 97.4), "ZA": (-34.9, 16.4, -22.1, 33.0),
}

WIN = sys.platform == "win32"

# modules the pipeline imports; checked inside the doppler-locate virtual environment
IMPORT_CHECK = (
    "import importlib.util as u, json\n"
    "need = ['numba','numpy','scipy','soundfile','requests']\n"
    "bad = [m for m in need if u.find_spec(m) is None]\n"
    "for m in ['dopploc.cli','polars','pyarrow','pyproj','rasterio','eccodes','tomlkit']:\n"
    "    try:\n"
    "        __import__(m)\n"
    "    except Exception as e:\n"
    "        bad.append(m + ' (' + type(e).__name__ + ')')\n"
    "print(json.dumps(bad))\n")

# looks up aircraft type / registration / callsign of the flights in the ingested day (run in the venv)
FLIGHT_INFO_PY = r'''
import json, sys
import polars as pl
adsb = sys.argv[1].replace("\\", "/")
out = {}
try:
    ac = pl.read_parquet(adsb + "/aircraft.parquet")
except Exception:
    ac = None
for fid in sys.argv[2:]:
    icao, t = fid.split(":")
    t = float(t)
    typ = reg = cs1 = None
    if ac is not None:
        r = ac.filter(pl.col("icao") == icao)
        if len(r):
            typ = r["type"][0] if "type" in r.columns else None
            reg = r["reg"][0] if "reg" in r.columns else None
    try:
        cs = (pl.scan_parquet(adsb + "/points-*.parquet").filter(pl.col("icao") == icao)
              .filter(pl.col("t").is_between(t - 3600, t + 14400)).select("callsign").collect())
        names = [c for c in cs["callsign"].to_list() if c]
        cs1 = max(set(names), key=names.count) if names else None
    except Exception:
        pass
    out[fid] = {"icao": icao, "type": typ, "reg": reg, "callsign": cs1}
print(json.dumps(out))
'''


def say(*a):
    print(*a, flush=True)


# --------------------------------------------------------------------------------------- small helpers

def km_box(bbox):
    la0, lo0, la1, lo1 = bbox
    return (la1 - la0) * 111.2, (lo1 - lo0) * 111.2 * math.cos(math.radians((la0 + la1) / 2))


def box_around(lat, lon, radius_km):
    dlat = radius_km / 111.2
    dlon = radius_km / (111.2 * math.cos(math.radians(lat)))
    return [round(lat - dlat, 5), round(lon - dlon, 5), round(lat + dlat, 5), round(lon + dlon, 5)]


def haversine_km(la0, lo0, la1, lo1):
    p0, p1 = math.radians(la0), math.radians(la1)
    a = math.sin((p1 - p0) / 2) ** 2 + math.cos(p0) * math.cos(p1) * math.sin(math.radians(lo1 - lo0) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def parse_iso(s):
    d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def posix(p):
    return str(p).replace("\\", "/")


def show_cmd(cmd):
    return subprocess.list2cmdline([str(c) for c in cmd]) if WIN else " ".join(shlex.quote(str(c)) for c in cmd)


def free_ram_gb():
    """Available RAM in GB on Windows, Linux and macOS (psutil if present); None when unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            return next(int(l.split()[1]) for l in f if l.startswith("MemAvailable:")) / 1e6
    except (OSError, StopIteration):
        pass
    if WIN:
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(m)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullAvailPhys / 1e9
        except Exception:
            return None
    if sys.platform == "darwin":
        try:
            txt = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
            ps = int(re.search(r"page size of (\d+) bytes", txt).group(1))
            pages = sum(int(re.search(rf"Pages {k}:\s+(\d+)", txt).group(1)) for k in ("free", "inactive", "speculative"))
            return pages * ps / 1e9
        except Exception:
            return None
    return None


def pick_workers(free_gb, gb_per_worker, cap):
    cpus = os.cpu_count() or 2
    by_cpu = max(1, cpus - 2)
    by_ram = 1 if free_gb is None else max(1, int(free_gb / gb_per_worker))
    return max(1, min(cap, by_cpu, by_ram))


# --------------------------------------------------------------------------------------- doppler-locate install

def is_dopploc_dir(p):
    return bool(p) and os.path.isfile(os.path.join(p, "pyproject.toml")) and os.path.isdir(os.path.join(p, "src", "dopploc"))


def find_dopploc(arg):
    """The doppler-locate clone. A folder you name (--dopploc-dir, $DOPPLOC_DIR) is used or refused, never replaced
    by a different one found elsewhere."""
    here = os.path.dirname(os.path.abspath(__file__))
    named = arg or os.environ.get("DOPPLOC_DIR")
    if named:
        return os.path.abspath(named) if is_dopploc_dir(named) else None
    cands = []
    for up in range(1, 6):                      # inside a clone of the colsto repo: data/vendor/doppler-locate
        cands.append(os.path.join(os.path.abspath(os.path.join(here, *[".."] * up)), "data", "vendor", "doppler-locate"))
    cwd = os.getcwd()
    cands += [os.path.join(cwd, "doppler-locate"), os.path.join(cwd, "data", "vendor", "doppler-locate"), cwd,
              os.path.expanduser("~/doppler-locate")]
    for c in cands:
        if is_dopploc_dir(c):
            return os.path.abspath(c)
    return None


def venv_exe(ddir, name):
    for rel in (("Scripts", name + ".exe"), ("bin", name)):
        p = os.path.join(ddir, ".venv", *rel)
        if os.path.isfile(p):
            return p
    return None


def launcher(ddir, override=None):
    """The command prefix that runs `dopploc`."""
    if override:
        return shlex.split(override.replace("\\", "/") if WIN else override)
    exe = venv_exe(ddir, "dopploc")
    if exe:
        return [exe]
    py = venv_exe(ddir, "python")
    return [py, "-m", "dopploc.cli"] if py else None


def read_text(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def install_problems(ddir, override=None, named=None):
    """Reasons doppler-locate cannot run here; an empty list means it can."""
    if not ddir:
        named = named or os.environ.get("DOPPLOC_DIR")
        if named:
            return [f"{named} is not a doppler-locate folder (no pyproject.toml and src/dopploc in it)"]
        return ["no doppler-locate folder found (looked at data/vendor/doppler-locate in the colsto repo, "
                "./doppler-locate, ./data/vendor/doppler-locate, ~/doppler-locate)"]
    if override:
        return []                                  # testing: a stand-in for the dopploc command
    py = venv_exe(ddir, "python")
    if not py:
        return [f"no virtual environment at {os.path.join(ddir, '.venv')}"]
    probs = []
    try:
        r = subprocess.run([py, "-c", IMPORT_CHECK], capture_output=True, text=True, timeout=180)
        bad = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else ["python could not start"]
        if bad:
            probs.append("missing or broken in the virtual environment: " + ", ".join(bad))
    except Exception as e:
        probs.append(f"the virtual environment's Python did not run ({type(e).__name__})")
    if not launcher(ddir):
        probs.append("the dopploc command is not installed in the virtual environment (run: uv pip install -e .)")
    if WIN and "_win_init" not in read_text(os.path.join(ddir, "src", "dopploc", "search.py")):
        probs.append("this doppler-locate is the Linux original: its search uses fork(), which Windows does not have "
                     "(apply solver/windows_patches.diff from the colsto repo)")
    if not shutil.which("gh") and "api.github.com" not in read_text(os.path.join(ddir, "src", "dopploc", "adsb.py")):
        probs.append("the GitHub CLI (gh) is not installed; fetch-adsb needs it (install it, then run: gh auth login)")
    return probs


def install_text(reasons):
    first = "\n".join("  - " + r for r in reasons)
    return f"""doppler-locate is not ready here:
{first}

Install it (needs git, uv and Python 3.11 or newer):
  git clone {DOPPLOC_URL}
  cd doppler-locate
  uv venv --python 3.12 .venv
  uv pip install -e .
Then run this command again with --dopploc-dir PATH_TO/doppler-locate (or set DOPPLOC_DIR to that folder).

Also needed on PATH: ffmpeg and ffprobe. For links: yt-dlp. To download the flight data: the GitHub CLI (gh),
signed in once with `gh auth login`.
  - Behind a network that inspects TLS, add --system-certs to the uv commands.
  - Windows: the upstream code is Linux-only (fork, /proc, gh). Apply the repo's patch to the fresh clone:
      git apply PATH_TO/colsto-leak-and-mask/solver/windows_patches.diff     ({PATCH_URL})
    and read docs/windows-notes.md in that repo for the other fixes.
  - macOS: if eccodes fails to import, run: brew install eccodes
Do not use scripts/run-capped.sh; this script calls the dopploc commands directly."""


# --------------------------------------------------------------------------------------- date, time, region

def day_problem(day, now=None):
    """Why adsb.lol cannot have this UTC day yet (or ever), or None."""
    now = now or datetime.now(timezone.utc)
    if day < ADSB_FIRST_DAY:
        return (f"{day} is before 2023-01-01. The adsb.lol history starts in 2023, so there is no flight data for "
                "that day and nothing to match the sound against.")
    ready = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1, hours=ADSB_READY_UTC[0],
                                                                                    minutes=ADSB_READY_UTC[1])
    if now < ready:
        return (f"The adsb.lol history for {day} is not out yet. A day appears the day after, at about 03:30 UTC, "
                f"so for this date: after {ready:%Y-%m-%d %H:%M} UTC. Run again then.")
    return None


def parse_bbox(s):
    v = [float(x) for x in s.replace(" ", "").split(",")]
    if len(v) != 4:
        raise ValueError("need four numbers: LAT_MIN,LON_MIN,LAT_MAX,LON_MAX")
    la0, lo0, la1, lo1 = v
    if not (-90 <= la0 < la1 <= 90 and -180 <= lo0 < lo1 <= 180):
        raise ValueError("need LAT_MIN < LAT_MAX within -90..90 and LON_MIN < LON_MAX within -180..180 "
                         "(a box across the 180 degree line is not supported)")
    return v


def inside_conus(bbox):
    return bbox[0] >= CONUS[0] and bbox[1] >= CONUS[1] and bbox[2] <= CONUS[2] and bbox[3] <= CONUS[3]


# --------------------------------------------------------------------------------------- detections -> events

def usable_detections(det):
    out = []
    for i, f in enumerate(det.get("flyovers", []), 1):
        if f.get("kind") == "aircraft" and f.get("confidence") in ("high", "medium") and f.get("refined") and f.get("ranges"):
            out.append((i, f))
    return out


def pick_events(det, forced, max_speed):
    """Two flyovers for the case file, as [(detection number, flyover dict)] in time order."""
    usable = usable_detections(det)
    if forced:
        by_no = {i: f for i, f in enumerate(det.get("flyovers", []), 1) if f.get("refined") and f.get("ranges")}
        chosen = []
        for n in forced:
            if n not in by_no:
                raise ValueError(f"detection {n} has no fitted speed and distance range, so it cannot be used")
            chosen.append((n, by_no[n]))
    else:
        cand = sorted((x for x in usable if x[1]["v"] <= max_speed), key=lambda x: -x[1]["z"])
        chosen = []
        for n, f in cand:
            if all(abs(f["t_cpa"] - g["t_cpa"]) >= 90 for _, g in chosen):
                chosen.append((n, f))
            if len(chosen) == 2:
                break
    return sorted(chosen, key=lambda x: x[1]["t_cpa"])


def event_block(name, f, num):
    r = f["ranges"]
    v0, v1 = r["speed_ms"]
    d0, d1 = r["slant_m"]
    lines = ["[[event]]", f'name = "{name}"      # detection #{num}',
             f"audio_cpa = {f['t_cpa']:.2f}", f"f0 = {f['f0']:.3f}",
             f"harmonics = {json.dumps([int(h) for h in (f.get('harmonics') or [1])])}",
             f"speed_ms = [{max(0.0, v0 - 5.0):.1f}, {v1 + 5.0:.1f}]",
             f"slant_m = [{round(d0 * 0.85)}, {round(d1 * 1.15)}]",
             "half_width_s = 60",
             f"# detected: score {f['z']:.1f}, fit rms {f['rms']:.2f} Hz, {f['confidence']} confidence, "
             f"{f['v']:.0f} m/s, {f['d']:.0f} m"]
    return "\n".join(lines)


def case_toml(name, ddir, audio, day, adsb_dir, out_dir, bbox, workers, lattice, window, utc_note, events_text):
    la0, lo0, la1, lo1 = bbox
    head = [f"# written by flyover-check locate.py. Settings follow results/colsto_dopploc/case.toml.",
            f"name = {json.dumps(name)}", f"root = {json.dumps(posix(ddir))}", f"day = {json.dumps(day.isoformat())}",
            f"adsb = {json.dumps(posix(adsb_dir))}", f"cache = {json.dumps(posix(os.path.join(ddir, 'data', 'cache')))}",
            f"audio = {json.dumps(posix(audio))}", f"out = {json.dumps(posix(out_dir))}", "c = 343.0", "",
            "[search]", "# regional box (lon0, lat0, lon1, lat1)",
            f"bbox = [{lo0}, {la0}, {lo1}, {la1}]", "gpu = false", f"workers = {workers}",
            "offset_tol_s = 8.0   # straight-line midpoints carry a few s of wind / manoeuvre bias",
            f"elevation_lattice = {'true' if lattice else 'false'}"]
    if window:
        head += [f"# {utc_note}", f"utc_window = [{window[0]:.1f}, {window[1]:.1f}]   # {iso(window[0])} .. {iso(window[1])}"]
    head += ["", "[score]", "dt_sweep = [-8.0, 8.0, 0.1]   # absorbs the clock bias of the straight-line midpoints", "",
             "[silence]", "enabled = true", 'mode = "cpa"', "ratio = 1.0", ""]
    return "\n".join(head) + "\n" + events_text + "\n"


# --------------------------------------------------------------------------------------- running steps

def run_step(label, cmd, cwd, env, log_path):
    """Run one command, show its output as it comes and keep a copy in log_path. Returns the exit code."""
    say(f"\n== {label}\n$ {show_cmd(cmd)}")
    t0 = time.time()
    with open(log_path, "a", encoding="utf-8") as lg:
        lg.write(f"$ {show_cmd(cmd)}\n")
        try:
            p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            say(f"could not start: {e}")
            return 127
        for line in p.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            lg.write(line)
        rc = p.wait()
    say(f"-- {label}: exit {rc}, {time.time() - t0:.0f} s (log: {log_path})")
    return rc


def load_json(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def flight_info(ddir, adsb_dir, fids):
    py = venv_exe(ddir, "python")
    if not py or not os.path.isfile(os.path.join(adsb_dir, "aircraft.parquet")):
        return {}
    try:
        r = subprocess.run([py, "-I", "-c", FLIGHT_INFO_PY, adsb_dir] + list(fids), capture_output=True, text=True,
                           timeout=300)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {}


def flight_ident(fid, info):
    i = info.get(fid, {})
    ident = " ".join(x for x in (i.get("callsign"), i.get("type") and f"({i['type']})", i.get("reg") and f"reg {i['reg']}") if x)
    return f"{ident or 'aircraft'} [icao {fid.split(':')[0]}]"


def flight_words(fid, info, d_m, t_utc):
    return f"{flight_ident(fid, info)}, closest to the microphone about {d_m:.0f} m at {iso(t_utc)}"


def report_results(case_dir, ddir, adsb_dir, case_path):
    best_csv = os.path.join(case_dir, "best_per_pair.csv")
    if not os.path.isfile(best_csv):
        say("\nNo ranked result was written. Look at the score log in " + case_dir)
        return False
    with open(best_csv, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        say("\nThe search found no pair of flights that fits both sounds in this box and day. Nothing is found.")
        return False
    rows.sort(key=lambda r: -float(r["score"]))
    top = rows[:5]
    fids = []
    for r in top[:2]:
        fids += [r["f1"], r.get("f2") or ""]
    info = flight_info(ddir, adsb_dir, [f for f in fids if f])
    say("\nRESULT (doppler-locate: search + score)")
    say(f"  Flight pairs that fit both sounds: {len(rows)}. Rank 1 is the best-scoring one.")
    r1 = top[0]
    s1 = float(r1["score"])
    lat, lon = float(r1["lat"]), float(r1["lon"])
    say(f"  RANK 1: {lat:.5f}, {lon:.5f}   score {s1:.3f} (event 1 {float(r1['S1']):.3f}, event 2 {float(r1['S2']):.3f})")
    say(f"    https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=14/{lat:.5f}/{lon:.5f}")
    say("    flight heard in event 1: " + flight_words(r1["f1"], info, float(r1["d1m"]), float(r1["t1m"])))
    if r1.get("f2"):
        say("    flight heard in event 2: " + flight_words(r1["f2"], info, float(r1["d2m"]), float(r1["t2m"])))
    if len(top) > 1:
        r2 = top[1]
        s2 = float(r2["score"])
        la2, lo2 = float(r2["lat"]), float(r2["lon"])
        gap = haversine_km(lat, lon, la2, lo2)
        say(f"  RANK 2: {la2:.5f}, {lo2:.5f}   score {s2:.3f}   -> rank 1 is ahead by {s1 - s2:.3f} ({(s1 - s2) / s1 * 100:.1f} %), "
            f"and rank 2 is {gap:.1f} km from rank 1")
        say("    flights: " + " and ".join(flight_ident(r2[k], info) for k in ("f1", "f2") if r2.get(k)))
        if gap < 3.0:
            say("    Rank 2 is the same neighbourhood: another flight pair points to the same place. That agrees with rank 1.")
        else:
            say("    Rank 2 is a different place. The margin is the only thing that separates them; treat rank 1 as the "
                "lead to check, not as a settled answer.")
    if len(top) > 2:
        say("  Next best:")
        for i, r in enumerate(top[2:], 3):
            say(f"    rank {i}: {float(r['lat']):.5f}, {float(r['lon']):.5f}  score {float(r['score']):.3f}")
    say("  The score is how well a pair of real flights explains both sounds at that place. It is a ranking, not a "
        "probability, and not a proof: a wrong clock, a plane missing from the public data, a misread tone or a jet "
        "can all push the true place down. On colsto's challenge rank 1 was right with 3.66 against 3.48, but that run used a 300 km box chosen knowing the answer.")
    say(f"  Pictures, tables and maps: {case_dir}  (rank1.png, scatter.png, summary.md, best_per_pair.csv)")
    say(f"  Sharper map around rank 1:  dopploc heatmap {case_path} --rank 1   (run it in {ddir})")
    return True


def one_flyover_text(chosen, n_usable, max_speed):
    lines = []
    if not chosen:
        lines.append("No usable plane tone was found: it needs aircraft type, high or medium confidence, a fitted speed and "
                     f"distance, and a speed a propeller plane can fly (under {max_speed:.0f} m/s).")
        if n_usable:
            lines.append(f"{n_usable} good curve(s) were found but all are faster than that, which usually means jets. "
                         "The search leaves jets out, so there is nothing to match (jets near runways mostly fail). "
                         "Check the table, and pass --events A,B if you are sure two of them are propeller planes.")
        lines.append("There is nothing to locate from. Stop here.")
        return "\n".join(lines)
    n, f = chosen[0]
    lines.append(f"Only ONE usable flyover: detection #{n} at {f['t_cpa']:.0f} s into the file "
                 f"({f['v']:.0f} m/s, closest approach about {f['d']:.0f} m, {f['f0']:.0f} Hz). Stopping here on purpose.")
    lines.append("What one plane can and cannot tell you: its pitch slide gives how far its path passed from the "
                 "microphone, but not on which side. Even if you knew which plane it was, that leaves TWO possible "
                 "places, mirror images of each other across its path, and nothing in the sound tells them apart. "
                 "If you do not know the plane either, many places fit.")
    lines.append("doppler-locate's search needs two flyovers from planes on different lines. Its one-flyover mode is "
                 "not validated (upstream could not reproduce it), so this script does not run it.")
    lines.append("What helps: a longer recording, another recording from the same spot, or a second audible plane "
                 "(propeller planes are the best case). A view of the sky or any other fact about the scene can also pick a side.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="local file, YouTube link or live link")
    ap.add_argument("--consent", choices=["own", "public-challenge", "owner-agreed"])
    ap.add_argument("--challenge-name", help="with --consent public-challenge: which challenge")
    ap.add_argument("--utc-start", help="UTC time of the first audio sample, e.g. 2026-09-08T14:03:10Z (best)")
    ap.add_argument("--date", help="UTC date only, e.g. 2026-09-08: searches the whole day (slower, more look-alikes)")
    ap.add_argument("--time-slack-s", type=float, help="how far the start time may be off (default 120 s if you gave "
                    "--utc-start; much wider if it came from the file's tag)")
    ap.add_argument("--bbox", help="LAT_MIN,LON_MIN,LAT_MAX,LON_MAX of the region to search")
    ap.add_argument("--center", help="LAT,LON of a rough guess; with --radius-km builds the box")
    ap.add_argument("--radius-km", type=float, default=100.0)
    ap.add_argument("--country", help="two-letter code; a whole country is RAM-heavy, start regional")
    ap.add_argument("--allow-large", action="store_true", help="allow a box wider than %d km" % REFUSE_SIDE_KM)
    ap.add_argument("--events", help="two detection numbers from the detect table, e.g. 34,46 (default: the two "
                    "strongest propeller-speed flyovers)")
    ap.add_argument("--max-speed", type=float, default=MAX_PROP_SPEED_MS, help="skip faster sources when choosing (m/s)")
    ap.add_argument("--dopploc-dir", help="folder of the doppler-locate clone (default: search common places)")
    ap.add_argument("--out-dir", help="case folder (default <dopploc-dir>/out/<case>)")
    ap.add_argument("--case", help="case name (default locate_<file name>)")
    ap.add_argument("--workers", type=int, help="search processes (default from free RAM, about 1.5 GB each)")
    ap.add_argument("--gpu", action="store_true", help="use the CUDA search (needs doppler-locate's native library)")
    ap.add_argument("--redetect", action="store_true", help="run detect again even if its output exists")
    ap.add_argument("--delete-raw", action="store_true", help="delete the downloaded ~3.9 GB tar after ingest")
    ap.add_argument("--skip-day-check", action="store_true", help="do not stop for a day adsb.lol may not have yet")
    ap.add_argument("--live-seconds", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true", help="print every check and command, change and download nothing")
    ap.add_argument("--dopploc-cmd", help=argparse.SUPPRESS)       # testing: stand-in for the dopploc command
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    # ---- 1. consent gate, before anything else
    say(RULE)
    say("")
    if not a.consent:
        say(ASK)
        return 3
    if a.consent == "public-challenge" and not a.challenge_name:
        say("NOT RUNNING: say which public challenge this is (--challenge-name). Without that, treat it as not allowed.")
        return 3
    say(f"Consent given: {a.consent}" + (f" ({a.challenge_name})" if a.challenge_name else "") + ".")
    if a.dry_run:
        say("DRY RUN: nothing is downloaded, computed or written. The commands below are what would run.")
    say("")

    # ---- 2. is doppler-locate here?
    ddir = find_dopploc(a.dopploc_dir)
    probs = install_problems(ddir, a.dopploc_cmd, a.dopploc_dir)
    if probs:
        say(install_text(probs))
        return 4
    prefix = launcher(ddir, a.dopploc_cmd)
    say(f"doppler-locate: {ddir}  (virtual environment found, imports work)")
    fo_media.need_ffmpeg()

    # ---- 3. the file
    dry_link = a.dry_run and fo_media.is_url(a.input)
    work = fo_media.tempdir("flyover-locate-")
    try:
        extra, meta, dur = {}, {}, None
        if dry_link:
            path = a.input
            say(f"Input: {a.input} (a link: it would be downloaded with yt-dlp; duration and tags unknown in a dry run)")
        else:
            path, extra = fo_media.resolve_input(a.input, os.path.join(work, "download"), want_video=False,
                                                 live_seconds=a.live_seconds)
            info = fo_media.probe(path)
            media = fo_media.media_summary(info)
            if media["n_audio"] == 0:
                say("This file has no audio track, so there is nothing to locate from.")
                return 5
            meta = fo_media.metadata_leaks(info)
            dur = media["duration_s"]
            if not fo_media.is_url(a.input):
                path = os.path.abspath(path)
            say(f"Input: {path}  ({mmss(dur)} of audio)")

        # ---- 4. date and time: from the user or the file's tags, never guessed
        utc0, why, from_tag = None, "", False
        if a.utc_start:
            try:
                utc0, why = parse_iso(a.utc_start), "given by you"
            except ValueError:
                say("--utc-start must look like 2026-09-08T14:03:10Z")
                return 5
        elif extra.get("utc_start"):
            utc0, why, from_tag = extra["utc_start"], extra.get("utc_note", "from the live link"), True
        elif not dry_link and not a.date:
            ct = fo_media.best_creation_time(meta)
            if ct:
                utc0, from_tag = ct[0], True
                why = (f"file tag {ct[1]}. This is when the file was written, which can be the start or the end of the "
                       "recording and may be local time stored as UTC. Check it.")
        day = None
        if a.date:
            try:
                day = date.fromisoformat(a.date)
            except ValueError:
                say("--date must look like 2026-09-08")
                return 5
        if utc0 is not None and day is not None and utc0.date() != day:
            if a.utc_start:
                say(f"--date {day} and --utc-start {utc0.isoformat()} are different UTC days. Fix one of them.")
                return 5
            utc0 = None                                 # the tag disagrees with the date you gave: trust you
        if utc0 is not None:
            day = day or utc0.date()
            say(f"UTC start: {utc0.isoformat()}   ({why})")
        elif day is not None:
            say(f"UTC date: {day}   (given by you; no start time, so the search covers the whole day)")
        else:
            say("No date. The ADS-B history is one file per UTC day, so the date is required, and the time of the first "
                "sample (to a few seconds) makes the search much faster and safer. The file has no usable tag. Give "
                "--utc-start 2026-09-08T14:03:10Z, or --date 2026-09-08 if you only know the day.\n"
                "Note: adsb.lol history appears the day after, at about 03:30 UTC, and starts in 2023.")
            return 5
        if utc0 is None:
            say("WARNING: without a start time every flight of the day is a candidate for the first sound, so there are "
                "more look-alike places. A start time known to a minute or two is worth giving.")
        elif from_tag:
            say("WARNING: this time comes from a tag, not from you. The search window is widened to match; if you know "
                "the real start time, pass --utc-start.")
        if dur and utc0 is not None and (utc0 + timedelta(seconds=dur)).date() != utc0.date():
            say("WARNING: the recording crosses 00:00 UTC. doppler-locate loads one UTC day, so planes after midnight "
                "are not in the data and a flyover there cannot be matched.")

        # ---- 5. is that day published?
        prob = day_problem(day)
        if prob and not a.skip_day_check:
            say("STOP: " + prob)
            return 5
        say(f"ADS-B day: {day} (adsb.lol history appears the day after at about 03:30 UTC and starts in 2023).")

        # ---- 6. region
        bbox = None
        try:
            if a.bbox:
                bbox = parse_bbox(a.bbox)
            elif a.center:
                la, lo = (float(v) for v in a.center.split(","))
                bbox = box_around(la, lo, max(a.radius_km, 20.0))
            elif a.country:
                cc = a.country.upper()
                if cc not in COUNTRY_BOX:
                    say(f"No built-in box for {cc}. Give --bbox or --center.")
                    return 5
                bbox = list(COUNTRY_BOX[cc])
        except ValueError as e:
            say(f"Bad region: {e}")
            return 5
        if bbox is None:
            say("No region. Say roughly where it was recorded: --bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX, or --center LAT,LON "
                "[--radius-km 100], or --country XX. Start with a box of about 100-300 km around your best guess.")
            return 5
        ns, ew = km_box(bbox)
        say(f"Search box: lat {bbox[0]}..{bbox[2]}, lon {bbox[1]}..{bbox[3]}   ({ns:.0f} x {ew:.0f} km)")
        if max(ns, ew) > REFUSE_SIDE_KM and not a.allow_large:
            say(f"STOP: that box is {max(ns, ew):.0f} km on its longest side. A whole country is RAM-heavy (upstream "
                "needed about 26 GB for the continental US; a 300 x 300 km box peaked near 5 GB) and this script does "
                "not guess. Start with a box of about 100-300 km around your best guess, or pass --allow-large if "
                "you know your machine can take it.")
            return 5
        if max(ns, ew) > HEAVY_SIDE_KM:
            say("WARNING: bigger than the 300 x 300 km box that was measured. Expect more RAM, a longer score step and "
                "more look-alike places. A smaller box is better if you have any idea where it is.")
        if min(ns, ew) < 80:
            say("WARNING: under about 80 km wide. Planes are heard from up to ~10 km away and the search only sees planes "
                "inside the box, so a small box can cut off the plane that was heard. 100 km or more is safer.")
        if not inside_conus(bbox):
            say("NOTE: doppler-locate's search grid is the continental-US Albers grid. It is validated only on colsto's "
                "US challenge; outside the US its own README calls this path untested. Treat a result here as a lead.")

        # ---- 7. resources
        free = free_ram_gb()
        w_search = a.workers or pick_workers(free, 1.5, 6)
        w_ingest = pick_workers(free, 1.0, 6)
        w_detect = pick_workers(free, 1.2, 6)
        say("Free RAM: " + (f"{free:.1f} GB" if free is not None else "unknown") +
            f"  ->  detect {w_detect}, ingest {w_ingest}, search {w_search} worker(s); GPU "
            + ("on" if a.gpu else "off") + ".")
        if free is not None and free < 4.0:
            say("WARNING: under 4 GB free. Close other programs; ingest peaked near 5.4 GB and search near 5 GB for a "
                "300 km box on a 16 GB Windows PC.")

        name = a.case or "locate_" + re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(path.split("?")[0]))[0])[:40]
        case_dir = os.path.abspath(a.out_dir) if a.out_dir else os.path.join(ddir, "out", name)
        det_dir = os.path.join(case_dir, "detect")
        case_path = os.path.join(case_dir, "case.toml")
        tag = "%s_%.2f_%.2f_%.2f_%.2f" % (day, bbox[1] - 0.3, bbox[0] - 0.3, bbox[3] + 0.3, bbox[2] + 0.3)
        adsb_dir = os.path.join(ddir, "data", "adsb", tag)           # never the plain data/adsb/<day>: ingest wipes its target
        raw_dir = os.path.join(ddir, "data", "adsb", "raw", str(day))
        ing_bbox = [bbox[1] - 0.3, bbox[0] - 0.3, bbox[3] + 0.3, bbox[2] + 0.3]   # LON0 LAT0 LON1 LAT1, a little wider
        lattice = inside_conus(bbox) and os.path.isfile(os.path.join(ddir, "data", "cache", "elev", "conus_albers_200m.npy"))
        types_ok = os.path.isfile(os.path.join(ddir, "data", "ref", "icao_aircraft_types2.js.gz"))
        ingested = os.path.isfile(os.path.join(adsb_dir, "flyover_locate_ingest.json")) and os.path.isfile(os.path.join(adsb_dir, "aircraft.parquet"))
        if fo_media.is_url(a.input) and not a.dry_run:      # keep the downloaded audio: the case file points at it
            keep = os.path.join(case_dir, "download")
            os.makedirs(keep, exist_ok=True)
            shutil.copy2(path, keep)
            path = os.path.join(keep, os.path.basename(path))
        have_detect = False
        if os.path.isfile(os.path.join(det_dir, "detections.json")) and not a.redetect:
            try:
                old_src = load_json(os.path.join(det_dir, "detections.json")).get("source", "")
                have_detect = os.path.basename(old_src.replace("\\", "/")) == os.path.basename(path)
            except (OSError, ValueError):
                pass
        env = dict(os.environ, PYTHONUNBUFFERED="1", DOPPLOC_WORKERS=str(w_detect))

        steps = []
        if have_detect:
            steps.append(("detect", None, "already done (use --redetect to run it again)"))
        else:
            steps.append(("detect", prefix + ["detect", path, "--out", det_dir],
                          "find the flyovers; a 60-minute file took minutes on a 16-core PC"))
        if not types_ok:
            steps.append(("fetch-types", prefix + ["fetch-types"], "aircraft type table, once"))
        if ingested:
            steps.append(("fetch-adsb", None, f"skipped: {adsb_dir} is already cut for this day and box"))
        else:
            steps.append(("fetch-adsb", prefix + ["fetch-adsb", str(day)],
                          "about 3.9 GB, resumable; the history for that day"))
            steps.append(("ingest", prefix + ["ingest", str(day), "--raw", raw_dir, "--out", adsb_dir, "--bbox"]
                          + ["%.3f" % v for v in ing_bbox] + ["--workers", str(w_ingest)],
                          "cut to your region (4 min and up to 5 GB at peak on the measured PC)"))
        steps.append(("search", prefix + ["search", case_path], "every flight pair that could explain both sounds (3 min measured)"))
        steps.append(("score", prefix + ["score", case_path, "--top", "5"], "model and rank them (20 min measured, about 3 GB)"))

        if a.dry_run:
            say(f"\nWould work in: {ddir}\nCase folder:   {case_dir}\nADS-B cut:     {adsb_dir}")
            for i, (lab, cmd, note) in enumerate(steps, 1):
                say(f"\n{i}. {lab}  - {note}")
                if cmd:
                    say("   " + show_cmd(cmd))
                if lab == "detect":
                    say("   then: pick the two strongest propeller-speed flyovers"
                        + (f" (you chose detections {a.events})" if a.events else "")
                        + "; fewer than two usable -> say so and stop")
                    wnd = ""
                    if utc0 is not None:
                        slack = a.time_slack_s or (max(900.0, (dur or 0) + 300.0) if from_tag else 120.0)
                        wnd = f", utc_window = first flyover's UTC time +-{slack:.0f} s"
                    say(f"   then: write {case_path}: bbox [{bbox[1]}, {bbox[0]}, {bbox[3]}, {bbox[2]}] (lon0 lat0 lon1 lat1), "
                        f"gpu=false, workers={w_search}, elevation_lattice={'true' if lattice else 'false'}{wnd}")
            say("\nThen it prints rank 1, the two flights, and rank 1 against rank 2.")
            return 0

        os.makedirs(case_dir, exist_ok=True)
        log = os.path.join(case_dir, "locate.log")

        def do(lab):
            for l, cmd, note in steps:
                if l == lab:
                    if cmd is None:
                        say(f"\n== {lab}: {note}")
                        return 0
                    return run_step(lab, cmd, ddir, env, log)
            return 0

        def fail(lab, rc):
            say(f"\nSTOPPED at {lab} (exit {rc}). Everything done so far is kept in {case_dir}; run the same command "
                "again to continue (finished steps are reused).")
            return 6

        # ---- 8. detect, then pick two flyovers
        rc = do("detect")
        if rc:
            return fail("detect", rc)
        det = load_json(os.path.join(det_dir, "detections.json"))
        usable = usable_detections(det)
        say(f"\nFlyovers: {len(det.get('flyovers', []))} curves found, {len(usable)} usable (aircraft, high or medium "
            "confidence, fitted). Full table: " + os.path.join(det_dir, "report.md"))
        for n, f in usable:
            say(f"  #{n}: {f['t_cpa']:.0f} s  {f['f0']:.0f} Hz  {f['v']:.0f} m/s  closest {f['d']:.0f} m  score {f['z']:.1f}  ({f['confidence']})")
        forced = None
        if a.events:
            try:
                forced = [int(x) for x in a.events.split(",")]
                if len(forced) != 2 or forced[0] == forced[1]:
                    raise ValueError("give exactly two different numbers")
            except ValueError as e:
                say(f"--events: {e}")
                return 5
        try:
            chosen = pick_events(det, forced, a.max_speed)
        except ValueError as e:
            say(str(e))
            return 5
        if len(chosen) < 2:
            say("\n" + one_flyover_text(chosen, len(usable), a.max_speed))
            return 5
        (n1, f1), (n2, f2) = chosen
        say(f"\nUsing detection #{n1} ({f1['t_cpa']:.0f} s, {f1['v']:.0f} m/s, {f1['d']:.0f} m) and "
            f"#{n2} ({f2['t_cpa']:.0f} s, {f2['v']:.0f} m/s, {f2['d']:.0f} m).")
        if not forced and any(f["v"] > a.max_speed for _, f in usable):
            say(f"(Sources faster than {a.max_speed:.0f} m/s were skipped: they are almost always jets, which the search "
                "leaves out. Pick others with --events A,B.)")
        if f2["t_cpa"] - f1["t_cpa"] < 180:
            say("WARNING: the two flyovers are under 3 minutes apart. If they are the same plane, or two planes on one "
                "line, they cannot tell the two sides apart.")

        # the day the flyovers fall on, when the start time is known
        window, utc_note = None, ""
        if utc0 is not None:
            slack = a.time_slack_s or (max(900.0, (dur or 0) + 300.0) if from_tag else 120.0)
            t1u, t2u = utc0.timestamp() + f1["t_cpa"], utc0.timestamp() + f2["t_cpa"]
            d1u, d2u = datetime.fromtimestamp(t1u, timezone.utc).date(), datetime.fromtimestamp(t2u, timezone.utc).date()
            if d1u != d2u:
                say(f"STOP: the two flyovers are on different UTC days ({d1u} and {d2u}); doppler-locate loads one day.")
                return 5
            if d1u != day:
                say(f"NOTE: the first flyover is on {d1u} UTC, not {day}. Run again with --date {d1u} (or a corrected "
                    "--utc-start): the flight data has to be that day's.")
                return 5
            window = (t1u - slack, t1u + slack)
            utc_note = f"first flyover heard at {iso(t1u)} +-{slack:.0f} s ({why})"
            say(f"Flyover 1 is at {iso(t1u)} UTC and flyover 2 at {iso(t2u)} UTC (start time {why}).")

        events_text = "\n\n".join([event_block("E1", f1, n1), event_block("E2", f2, n2)])
        with open(case_path, "w", encoding="utf-8") as fh:
            fh.write(case_toml(name, ddir, path, day, adsb_dir, case_dir, bbox, w_search, lattice, window,
                               utc_note, events_text))
        if a.gpu:
            with open(case_path, encoding="utf-8") as fh:
                txt = fh.read().replace("gpu = false", "gpu = true")
            with open(case_path, "w", encoding="utf-8") as fh:
                fh.write(txt)
        with open(os.path.join(case_dir, "flyover_locate.json"), "w", encoding="utf-8") as fh:
            json.dump({"consent": a.consent, "challenge": a.challenge_name, "input": a.input, "day": str(day),
                       "utc_start": utc0.isoformat() if utc0 else None, "utc_start_note": why, "bbox_lat_lon": bbox,
                       "events": [n1, n2], "dopploc_dir": ddir}, fh, indent=1)
        say(f"Case written: {case_path}")

        # ---- 9. flight data, then search and score
        if not types_ok:
            rc = do("fetch-types")
            if rc:
                return fail("fetch-types", rc)
        if not ingested:
            for lab in ("fetch-adsb", "ingest"):
                rc = do(lab)
                if rc:
                    return fail(lab, rc)
            with open(os.path.join(adsb_dir, "flyover_locate_ingest.json"), "w", encoding="utf-8") as fh:
                json.dump({"day": str(day), "bbox_lon0_lat0_lon1_lat1": ing_bbox}, fh)
            if a.delete_raw:
                shutil.rmtree(raw_dir, ignore_errors=True)
                say(f"Deleted the downloaded tar in {raw_dir}.")
            else:
                say(f"The downloaded tar (about 3.9 GB) is still in {raw_dir}; delete it when you are done.")
        for lab in ("search", "score"):
            rc = do(lab)
            if rc:
                return fail(lab, rc)
        ok = report_results(case_dir, ddir, adsb_dir, case_path)
        return 0 if ok else 6
    finally:
        shutil.rmtree(work, ignore_errors=True)


def mmss(sec):
    sec = int(round(sec or 0))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


if __name__ == "__main__":
    sys.exit(main())
