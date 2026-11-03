"""Record YouTube live HLS audio with exact UTC per segment, and log live ADS-B around each search box.

Idea and segment-timing approach from Bortlesboat/aircraft-doppler-realworld-test (MIT).

    python recorder/record.py --minutes 90 --backfill-min 180

Reads data/sealed/cams.json (camera URLs, case ids, search boxes). Writes, per case:
    data/raw/<case>/seg/<media-seq>.ts      raw HLS segments (format 91, HE-AAC audio)
    data/raw/<case>/index.csv               seq,utc_start_iso,duration_s,file
    data/raw/adsb/<case>.jsonl              {"t": poll_unix, "src": ..., "ac": [...]}
ADS-B is polled around the box centre (never the camera), so the logs reveal nothing beyond the box.
"""
import argparse, csv, json, os, subprocess, sys, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
UA = {"User-Agent": "flyover-leak-recorder/0.1 (research; github.com/Bodila51/flyover-leak)"}
# airplanes.live answered HTTP 403 to this client on 2026-11-03, so only adsb.lol is polled live;
# gaps are filled later from the adsb.lol daily history.
ADSB = {
    "adsblol": "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}",
}
stop = threading.Event()


def log(*a):
    print(datetime.now(timezone.utc).strftime("%H:%M:%S"), *a, flush=True)


def get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.status, r.read()


def hls_url(watch_url):
    out = subprocess.run(["yt-dlp", "--compat-options", "no-certifi", "-g", "-f", "91", watch_url],
                         capture_output=True, text=True, timeout=120)
    url = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    if not url.startswith("http"):
        raise RuntimeError(out.stderr.strip()[-300:])
    return url


def parse_playlist(text):
    """Return [(seq, utc_datetime, duration, uri)]. PDT tags may be sparse: carry time forward by EXTINF."""
    seq, t, dur, segs = 0, None, None, []
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            t = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
        elif line.startswith("#EXTINF:"):
            dur = float(line.split(":", 1)[1].split(",")[0])
        elif line and not line.startswith("#"):
            segs.append((seq, t, dur, line))
            if t is not None and dur is not None:
                t = t + timedelta(seconds=dur)
            seq += 1
    return segs


def record_cam(cam, minutes, backfill_min):
    case = cam["case"]
    segdir = os.path.join(RAW, case, "seg")
    os.makedirs(segdir, exist_ok=True)
    idx_path = os.path.join(RAW, case, "index.csv")
    have = set()
    if os.path.exists(idx_path):
        have = {int(r[0]) for r in csv.reader(open(idx_path)) if r and r[0].isdigit()}
    idx = open(idx_path, "a", newline="")
    w = csv.writer(idx)
    if not have:
        w.writerow(["seq", "utc_start", "duration_s", "file"])
    end = time.time() + minutes * 60
    url, url_t, first = None, 0, True
    pool = ThreadPoolExecutor(6)

    def fetch(seg):
        seq, t, dur, uri = seg
        path = os.path.join(segdir, f"{seq}.ts")
        for _ in range(3):
            try:
                _, data = get(uri, timeout=30)
                open(path, "wb").write(data)
                return seg, path
            except Exception:
                time.sleep(2)
        return seg, None

    while time.time() < end and not stop.is_set():
        try:
            if url is None or time.time() - url_t > 1500:
                url, url_t = hls_url(cam["url"]), time.time()
            _, body = get(url)
            segs = [s for s in parse_playlist(body.decode()) if s[0] not in have and s[1] is not None]
            if first:
                cutoff = datetime.now(timezone.utc) - timedelta(minutes=backfill_min)
                segs = [s for s in segs if s[1] >= cutoff]
                log(case, f"backfill {len(segs)} segments from {segs[0][1].isoformat() if segs else '-'}")
                first = False
            for seg, path in pool.map(fetch, segs):
                if path:
                    w.writerow([seg[0], seg[1].isoformat(), seg[2], os.path.basename(path)])
                    have.add(seg[0])
            idx.flush()
        except Exception as e:
            log(case, "error:", repr(e)[:200])
            url = None
            time.sleep(10)
        time.sleep(8)
    idx.close()
    log(case, f"recording done, {len(have)} segments")


def poll_adsb(cams, minutes, period):
    """Poll each source for each box about every `period` seconds, staggered, within documented limits
    (airplanes.live: 1 request/s; adsb.lol: dynamic)."""
    end = time.time() + minutes * 60
    files = {c["case"]: open(os.path.join(RAW, "adsb", f"{c['case']}.jsonl"), "a") for c in cams}
    stats = {}
    jobs = [(c, src) for c in cams for src in ADSB]
    gap = period / len(jobs)
    while time.time() < end and not stop.is_set():
        for c, src in jobs:
            t0 = time.time()
            lat, lon = c["box_center"]
            try:
                status, body = get(ADSB[src].format(lat=lat, lon=lon, nm=25), timeout=10)
                ac = json.loads(body).get("ac", [])
                files[c["case"]].write(json.dumps({"t": round(t0, 3), "src": src, "ac": ac}) + "\n")
                files[c["case"]].flush()
                key = (src, "ok")
            except Exception as e:
                key = (src, getattr(e, "code", type(e).__name__))
            stats[key] = stats.get(key, 0) + 1
            time.sleep(max(0.0, gap - (time.time() - t0)))
        if int(time.time()) % 300 < period:
            log("adsb", {f"{k[0]}:{k[1]}": v for k, v in sorted(stats.items(), key=str)})
    for f in files.values():
        f.close()
    log("adsb done", stats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=90)
    ap.add_argument("--backfill-min", type=float, default=180)
    ap.add_argument("--adsb-period", type=float, default=6.0)
    ap.add_argument("--cases", nargs="*")
    a = ap.parse_args()
    cams = json.load(open(os.path.join(ROOT, "data", "sealed", "cams.json")))["cams"]
    if a.cases:
        cams = [c for c in cams if c["case"] in a.cases]
    threads = [threading.Thread(target=poll_adsb, args=(cams, a.minutes, a.adsb_period), daemon=True)]
    threads += [threading.Thread(target=record_cam, args=(c, a.minutes, a.backfill_min), daemon=True) for c in cams]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    sys.exit(main())
