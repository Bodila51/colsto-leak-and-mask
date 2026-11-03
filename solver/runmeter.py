"""Run a command and record wall time and peak memory of its whole process tree.

    python runmeter.py OUT.json -- command arg ...

Peak RAM is the largest sum over the tree of (a) the resident working set and (b) the private
(committed) bytes, polled every 0.5 s; it also records the lowest free system RAM seen.
"""
import json
import subprocess
import sys
import threading
import time

import psutil


def main():
    out = sys.argv[1]
    cmd = sys.argv[sys.argv.index("--") + 1:]
    t0 = time.time()
    p = subprocess.Popen(cmd)
    root = psutil.Process(p.pid)
    peak = {"rss": 0, "private": 0, "free_min": 1 << 62, "procs": 0}
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            try:
                procs = [root] + root.children(recursive=True)
                rss = priv = n = 0
                for q in procs:
                    try:
                        mi = q.memory_info()
                        rss += mi.rss
                        priv += getattr(mi, "private", mi.rss)
                        n += 1
                    except psutil.Error:
                        pass
                peak["rss"] = max(peak["rss"], rss)
                peak["private"] = max(peak["private"], priv)
                peak["procs"] = max(peak["procs"], n)
                peak["free_min"] = min(peak["free_min"], psutil.virtual_memory().available)
            except psutil.Error:
                pass
            stop.wait(0.5)

    th = threading.Thread(target=poll, daemon=True)
    th.start()
    rc = p.wait()
    stop.set()
    th.join()
    res = {"cmd": cmd, "returncode": rc, "wall_s": round(time.time() - t0, 1),
           "peak_rss_gb": round(peak["rss"] / 2**30, 2), "peak_private_gb": round(peak["private"] / 2**30, 2),
           "min_free_ram_gb": round(peak["free_min"] / 2**30, 2), "max_processes": peak["procs"]}
    with open(out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps(res))
    sys.exit(rc)


if __name__ == "__main__":
    main()
