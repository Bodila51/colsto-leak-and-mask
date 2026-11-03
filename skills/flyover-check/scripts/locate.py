# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "soundfile", "matplotlib"]
# ///
"""flyover-check, mode 3: find where it was recorded.

  ONLY for (1) your own recordings, (2) public challenges such as colsto's, or (3) recordings whose owner
  agreed. It is not for finding where a stranger is. Anything else: stop.

  uv run locate.py INPUT --consent own|public-challenge|owner-agreed
                   (--bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX | --country BG | --center LAT,LON [--radius-km 25])
                   [--utc-start 2026-11-02T14:03:10Z] [--adsb FILE | --fetch-adsb-now]

It needs the UTC start time (read from the file's creation_time when present), a search area, public ADS-B
flight data for that time, and the repo's solver (solver/solve.py, built on doppler-locate, MIT).
It writes cases/<name>/{audio.flac, case.json, adsb.jsonl}, runs the solver and reports what came back.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fo_media  # noqa: E402

RULE = """MODE: find where it was recorded
This mode is ONLY for:
  1. your own recordings,
  2. public challenges (such as colsto's), or
  3. recordings whose owner has agreed.
It is not for locating other people. If the recording is not one of these, stop here."""

ASK = """Before anything runs, answer one question: which of these is true?
  own               - I made this recording.
  public-challenge  - it is part of a public challenge that invites this (name it).
  owner-agreed      - the person who recorded it has agreed to have it located.
If none applies, do not continue. Then run again with --consent <that word>."""

REPO_URL = "https://github.com/Bodila51/colsto-leak-and-mask"

INSTALL_SOLVER = f"""The solver is not available here. It lives in the repo, not in the skill folder. To install:
  git clone {REPO_URL}
  cd colsto-leak-and-mask
  git clone --depth 1 https://github.com/digitalis-p/doppler-locate data/vendor/doppler-locate
  cd data/vendor/doppler-locate
  python -m uv venv --python 3.12 .venv
  python -m uv pip install --python .venv/Scripts/python.exe -e ".[test]"     (Windows; on Linux/macOS use .venv/bin/python)
  (on Windows see docs/windows-notes.md in the repo for the patches the solver needs)
Then run this command again from the repo folder, or pass --repo PATH_TO_THE_REPO.
The case files were written so nothing is lost: {{case_dir}}"""

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

UA = {"User-Agent": "flyover-check/0.1 (research; " + REPO_URL + ")"}


def say(*a):
    print(*a, flush=True)


def km_box(bbox):
    la0, lo0, la1, lo1 = bbox
    return (la1 - la0) * 111.2, (lo1 - lo0) * 111.2 * math.cos(math.radians((la0 + la1) / 2))


def box_around(lat, lon, radius_km):
    dlat = radius_km / 111.2
    dlon = radius_km / (111.2 * math.cos(math.radians(lat)))
    return [round(lat - dlat, 5), round(lon - dlon, 5), round(lat + dlat, 5), round(lon + dlon, 5)]


def find_repo(arg):
    cands = []
    if arg:
        cands.append(arg)
    if os.environ.get("FLYOVER_REPO"):
        cands.append(os.environ["FLYOVER_REPO"])
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(1, 6):
        cands.append(os.path.abspath(os.path.join(here, *[".."] * up)))
    cands += [os.getcwd(), os.path.expanduser("~/colsto-leak-and-mask")]
    for c in cands:
        if os.path.isfile(os.path.join(c, "solver", "solve.py")):
            return c
    return None


def find_solver_python(repo, arg):
    if arg:
        return arg
    base = os.path.join(repo, "data", "vendor", "doppler-locate", ".venv")
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = os.path.join(base, *rel)
        if os.path.isfile(p):
            return p
    return None


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:   # system trust store, never unverified
        return json.loads(r.read().decode("utf-8"))


def fetch_adsb_now(bbox, t0_utc, t1_utc, out_path, max_aircraft=150):
    """Public adsb.lol data: planes near the box right now, then each one's full-day trace (readsb).
    Works only for recordings made today (UTC) or in the last few hours: planes that flew past earlier and
    have landed since are not returned. For older dates, build the file from the adsb.lol daily history
    (see the repo README) and pass it with --adsb."""
    la0, lo0, la1, lo1 = bbox
    clat, clon = (la0 + la1) / 2, (lo0 + lo1) / 2
    half = max(km_box(bbox)) / 2 * 1.5
    radius_nm = int(min(250, max(25, half / 1.852)))
    say(f"Asking adsb.lol for aircraft within {radius_nm} nm of the box centre ...")
    snap = http_json(f"https://api.adsb.lol/v2/point/{clat:.4f}/{clon:.4f}/{radius_nm}")
    acs = [a for a in snap.get("ac", []) if a.get("hex") and not a["hex"].startswith("~")]
    now = float(snap.get("now", time.time() * 1000)) / 1000.0
    n_lines = 0
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(json.dumps({"t": now, "src": "adsblol", "ac": snap.get("ac", [])}) + "\n")
        n_lines += 1
        lo_t, hi_t = t0_utc.timestamp() - 600, t1_utc.timestamp() + 600
        for a in acs[:max_aircraft]:
            hx = a["hex"]
            try:
                d = http_json(f"https://globe.adsb.lol/data/traces/{hx[-2:]}/trace_full_{hx}.json", timeout=20)
            except Exception:
                time.sleep(1.0)
                continue
            base = d.get("timestamp", 0)
            for p in d.get("trace", []):
                t = base + p[0]
                if not (lo_t <= t <= hi_t):
                    continue
                if not (la0 - 0.3 <= p[1] <= la1 + 0.3 and lo0 - 0.45 <= p[2] <= lo1 + 0.45):
                    continue
                det = p[8] if len(p) > 8 and isinstance(p[8], dict) else {}
                ac = {"hex": hx, "flight": (det.get("flight") or a.get("flight") or "").strip(), "t": d.get("t"),
                      "lat": p[1], "lon": p[2], "alt_baro": p[3], "alt_geom": p[10] if len(p) > 10 else None,
                      "gs": p[4], "track": p[5], "seen_pos": 0}
                out.write(json.dumps({"t": round(t, 3), "src": "adsblol-trace", "ac": [ac]}) + "\n")
                n_lines += 1
            time.sleep(1.0)       # polite: one request a second
    return len(acs), n_lines


def parse_iso(s):
    d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def report_answer(ans, n_planes_heard):
    say("\nRESULT FROM THE SOLVER")
    lat, lon = ans.get("lat"), ans.get("lon")
    if lat is None:
        say("  The solver did not return a point. Reason given: " + str(ans.get("reason") or ans.get("note") or "none"))
        return
    say(f"  Best point: {lat:.5f}, {lon:.5f}   (https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=14/{lat:.5f}/{lon:.5f})")
    used = ans.get("n_flyovers_used")
    say(f"  Flyovers used: {used} of {ans.get('n_flyovers_detected')} detected; planes this check heard: {n_planes_heard}.")
    for f in ans.get("flights_used", []):
        if f.get("used"):
            say(f"    - {f.get('callsign') or f.get('hex')} ({f.get('type')}), closest to the microphone about "
                f"{f.get('cpa_distance_m'):.0f} m at {f.get('cpa_utc')}, ground speed {f.get('ground_speed_ms'):.0f} m/s")
    if ans.get("two_band"):
        say("  TWO POSSIBLE PLACES. " + str(ans.get("two_band_reason")))
        m = ans.get("mirror_point")
        if m:
            say(f"  The mirror-image place, on the other side of the plane's path: {m['lat']:.5f}, {m['lon']:.5f}")
            d = math.hypot((m["lat"] - lat) * 111.2, (m["lon"] - lon) * 111.2 * math.cos(math.radians(lat)))
            say(f"  The two places are about {d:.1f} km apart. Nothing in the sound tells them apart. "
                "A second plane on a different line, a view of where the plane crossed the sky, or any other "
                "fact about the scene can.")
        else:
            say("  The sound alone cannot tell which side of the plane's path the microphone was on.")
    else:
        say("  The planes used flew on different lines, which is what lets the solver pick one side. "
            "(" + str(ans.get("two_band_reason")) + ")")
    ru = ans.get("runner_up")
    if ru:
        say(f"  Runner-up point: {ru['lat']:.5f}, {ru['lon']:.5f} (score margin {ru['margin']:.3f}).")
    say("  This is the computed best fit, not a certainty. It is wrong if the start time is off by more than a "
        "few seconds, if a plane is missing from public data, or if the plane tone was misread.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="local file, YouTube link or live link")
    ap.add_argument("--consent", choices=["own", "public-challenge", "owner-agreed"])
    ap.add_argument("--challenge-name", help="with --consent public-challenge: which challenge")
    ap.add_argument("--utc-start", help="UTC time of the first audio sample, e.g. 2026-11-02T14:03:10Z")
    ap.add_argument("--bbox", help="LAT_MIN,LON_MIN,LAT_MAX,LON_MAX (the solver wants at least 40 x 40 km)")
    ap.add_argument("--country", help="two-letter code; a very large area, slow and coarse (see warning)")
    ap.add_argument("--center", help="LAT,LON of a rough guess; with --radius-km builds the box")
    ap.add_argument("--radius-km", type=float, default=25.0)
    ap.add_argument("--adsb", help="ADS-B file in the solver's adsb.jsonl format")
    ap.add_argument("--fetch-adsb-now", action="store_true",
                    help="fetch public adsb.lol data (only works for recordings made today/the last hours)")
    ap.add_argument("--repo", help="path of the cloned repo (the folder that holds solver/solve.py)")
    ap.add_argument("--cases-dir", help="where to write the case (default <repo>/cases, or ./flyover-cases)")
    ap.add_argument("--case", help="case name (default own_<file>)")
    ap.add_argument("--solver-python", help="python of the doppler-locate virtual environment")
    ap.add_argument("--solver-cmd", help="override the whole solver command (testing)")
    ap.add_argument("--live-seconds", type=int, default=600)
    ap.add_argument("--allow-low", action="store_true", help="also use low-confidence flyovers")
    ap.add_argument("--dry-run", action="store_true", help="write the case files but do not run the solver")
    a = ap.parse_args(argv)

    say(RULE)
    say("")
    if not a.consent:
        say(ASK)
        return 3
    if a.consent == "public-challenge" and not a.challenge_name:
        say("Say which public challenge this is (--challenge-name). Without that, treat it as not allowed.")
        return 3
    say(f"Consent given: {a.consent}" + (f" ({a.challenge_name})" if a.challenge_name else "") + ".\n")

    fo_media.need_ffmpeg()
    import fo_report
    work = fo_media.tempdir("flyover-locate-")
    try:
        path, extra = fo_media.resolve_input(a.input, os.path.join(work, "download"), want_video=False,
                                             live_seconds=a.live_seconds)
        r = fo_report.check_path(path)
        res = r["analysis"]
        fl = [g for g in res["flyovers"] if a.allow_low or g["label"] in ("high", "medium")]
        say(f"Planes heard in the audio: {len(res['flyovers'])}; usable (medium or high confidence): {len(fl)}.")
        for i, g in enumerate(fl, 1):
            say(f"  plane {i}: {fo_report.mmss(g['start_s'])}-{fo_report.mmss(g['end_s'])} ({g['label']})")
        if not fl:
            say("No usable plane tone, so there is nothing to locate from. Stop here.")
            return 5

        # start time
        utc0, why = None, ""
        if a.utc_start:
            utc0, why = parse_iso(a.utc_start), "given by you"
        elif extra.get("utc_start"):
            utc0, why = extra["utc_start"], extra.get("utc_note", "")
        else:
            ct = fo_media.best_creation_time(r["meta"])
            if ct:
                utc0, why = ct[0], (f"file tag {ct[1]}. This is when the file was written, which can be the start or "
                                    "the end of the recording and may be local time stored as UTC. Check it.")
        if utc0 is None:
            say("No start time. The solver needs the UTC time of the first sample to within a few seconds. "
                "Give --utc-start 2026-11-02T14:03:10Z.")
            return 5
        say(f"UTC start: {utc0.isoformat()}   ({why})")

        # area
        if a.bbox:
            bbox = [float(v) for v in a.bbox.split(",")]
        elif a.center:
            la, lo = (float(v) for v in a.center.split(","))
            bbox = box_around(la, lo, max(a.radius_km, 20.0))
        elif a.country:
            cc = a.country.upper()
            if cc not in COUNTRY_BOX:
                say(f"No built-in box for {cc}. Give --bbox or --center.")
                return 5
            bbox = list(COUNTRY_BOX[cc])
            say("WARNING: a whole-country box is very large. The search will be slow and the answer coarse. "
                "A box of about 40-100 km around your best guess works far better.")
        else:
            say("No search area. Give --bbox LAT_MIN,LON_MIN,LAT_MAX,LON_MAX, --center LAT,LON, or --country XX.")
            return 5
        ns, ew = km_box(bbox)
        say(f"Search box: {bbox}  ({ns:.0f} x {ew:.0f} km)")
        if ns < 40 or ew < 40:
            say("WARNING: the solver wants at least 40 x 40 km; a smaller box may give odd results.")

        # where to write the case
        repo = find_repo(a.repo)
        name = a.case or "own_" + "".join(ch if ch.isalnum() else "_" for ch in os.path.splitext(os.path.basename(path))[0])[:40]
        cases_root = a.cases_dir or (os.path.join(repo, "cases") if repo else os.path.abspath("flyover-cases"))
        case_dir = os.path.join(cases_root, name)
        os.makedirs(case_dir, exist_ok=True)
        flac = os.path.join(case_dir, "audio.flac")
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", path, "-map", "0:a:0", "-vn", "-ac", "1",
                        "-ar", "16000", "-map_metadata", "-1", "-c:a", "flac", flac], check=True)
        dur = r["media"]["duration_s"]
        rel_audio = (os.path.relpath(flac, repo) if repo else flac).replace(os.sep, "/")
        case = {"case": name, "audio": rel_audio, "sample_rate": 16000,
                "utc_start": utc0.isoformat().replace("+00:00", "Z"), "duration_s": round(dur or 0, 2),
                "bbox": bbox, "consent": a.consent, "start_time_note": why}
        with open(os.path.join(case_dir, "case.json"), "w", encoding="utf-8") as fh:
            json.dump(case, fh, indent=1)
        with open(os.path.join(case_dir, "flyovers_heard.json"), "w", encoding="utf-8") as fh:
            json.dump([{"start_s": g["start_s"], "end_s": g["end_s"], "label": g["label"], "f_start_hz": g["f_start"],
                        "f_end_hz": g["f_end"]} for g in fl], fh, indent=1)
        say(f"Case written: {case_dir}")

        # ADS-B
        adsb_dst = os.path.join(case_dir, "adsb.jsonl")
        if a.adsb:
            shutil.copyfile(a.adsb, adsb_dst)
            say(f"ADS-B: copied {a.adsb}")
        elif a.fetch_adsb_now:
            end = utc0 + timedelta(seconds=dur or 0)
            if datetime.now(timezone.utc) - end > timedelta(hours=12):
                say("WARNING: this recording is older than half a day. adsb.lol only lists planes that are in "
                    "the air now, so planes that passed then and have landed since will be missing.")
            try:
                n_ac, n_lines = fetch_adsb_now(bbox, utc0, end, adsb_dst)
                say(f"ADS-B: {n_ac} aircraft near the box now, {n_lines} lines written.")
            except Exception as e:
                say(f"ADS-B fetch failed: {e}")
                return 6
        elif not os.path.exists(adsb_dst):
            say("ADS-B data is needed. Give --adsb FILE (solver's adsb.jsonl format), or --fetch-adsb-now for a "
                "recording from today. For an older date, build adsb.jsonl from the adsb.lol daily history "
                "(see the repo README) and pass it with --adsb.")
            return 6

        if a.dry_run:
            say("Dry run: solver not started.")
            return 0
        if a.solver_cmd:
            cmd = a.solver_cmd.split() + [case_dir]
            cwd = repo or os.getcwd()
        else:
            if not repo:
                say(INSTALL_SOLVER.format(case_dir=case_dir))
                return 4
            py = find_solver_python(repo, a.solver_python)
            if not py:
                say("The solver's Python environment (data/vendor/doppler-locate/.venv) is missing.")
                say(INSTALL_SOLVER.format(case_dir=case_dir))
                return 4
            cmd = [py, os.path.join(repo, "solver", "solve.py"), case_dir]
            cwd = repo
        say("\nRunning the solver (this can take many minutes and several GB of memory) ...")
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        p = subprocess.run(cmd, cwd=cwd, env=env)
        out_dir = os.path.join(cwd, "results", name)
        ans_path = os.path.join(out_dir, "answer.json")
        if p.returncode != 0 or not os.path.exists(ans_path):
            say(f"The solver stopped (exit {p.returncode}) and wrote no answer at {ans_path}.")
            return 7
        with open(ans_path, encoding="utf-8") as fh:
            ans = json.load(fh)
        report_answer(ans, len(fl))
        say(f"\nFull results and heat maps: {out_dir}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
