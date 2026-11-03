"""Fill gaps in the live ADS-B log with each aircraft's full-day trace from the adsb.lol map (readsb traces).

    python recorder/fetch_traces.py case_A case_B ...

For every aircraft seen in the live polls of a search box (box centre, 25 nm), downloads
https://globe.adsb.lol/data/traces/<last 2 hex>/trace_full_<hex>.json once, politely (1 request/s), and writes
its points as poll-like lines to data/raw/adsb/<case>.traces.jsonl:
    {"t": unix, "src": "adsblol-trace", "ac": [{"hex", "flight", "t", "lat", "lon", "alt_baro", "alt_geom", "gs", "track", "seen_pos": 0}]}
Aircraft are chosen only by having been inside the box's poll radius, never by distance to the camera.
"""
import json, os, sys, time

import truststore

truststore.inject_into_ssl()
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADSB = os.path.join(ROOT, "data", "raw", "adsb")
UA = {"User-Agent": "flyover-leak-recorder/0.1 (research; github.com/Bodila51/flyover-leak)"}


def main(cases):
    sess = requests.Session()
    sess.headers.update(UA)
    cache = {}
    for case in cases:
        seen = {}
        for line in open(os.path.join(ADSB, f"{case}.jsonl")):
            for a in json.loads(line)["ac"]:
                if "hex" in a and not a["hex"].startswith("~"):
                    seen.setdefault(a["hex"], (a.get("flight") or "").strip())
        n_pts = 0
        with open(os.path.join(ADSB, f"{case}.traces.jsonl"), "w") as out:
            for hexid, flight in sorted(seen.items()):
                if hexid not in cache:
                    try:
                        r = sess.get(f"https://globe.adsb.lol/data/traces/{hexid[-2:]}/trace_full_{hexid}.json", timeout=20)
                        cache[hexid] = r.json() if r.ok else None
                    except Exception:
                        cache[hexid] = None
                    time.sleep(1.0)
                d = cache[hexid]
                if not d:
                    continue
                base = d["timestamp"]
                for p in d.get("trace", []):
                    alt = p[3]
                    det = p[8] if len(p) > 8 and isinstance(p[8], dict) else {}
                    ac = {"hex": hexid, "flight": (det.get("flight") or flight or "").strip(), "t": d.get("t"),
                          "lat": p[1], "lon": p[2], "alt_baro": alt, "alt_geom": p[10] if len(p) > 10 else None,
                          "gs": p[4], "track": p[5], "seen_pos": 0}
                    out.write(json.dumps({"t": round(base + p[0], 3), "src": "adsblol-trace", "ac": [ac]}) + "\n")
                    n_pts += 1
        ok = sum(1 for h in seen if cache.get(h))
        print(f"{case}: {len(seen)} aircraft seen, {ok} traces, {n_pts} points", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
