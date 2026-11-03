"""Overlay a 'target locked' box on the aircraft in a webcam clip (demo visual for the post).

    python post/tools/track_overlay.py SRC OUT --start 438 --dur 20 --crop 48,100,1298,730 \
        --label "SWA4113 · Boeing 737 MAX 8" --tone "1402 -> 1211 Hz"

Detection is simple: the aircraft is the largest dark blob against the sky, not touching the frame edge,
kept near its previous position. Frames are written untouched apart from the drawn overlay; audio is the
source audio of the same span.
"""
import argparse, subprocess, sys

import cv2
import numpy as np

GREEN = (90, 255, 120)
RED = (60, 60, 255)


def brackets(img, x0, y0, x1, y1, col, t=3, L=None):
    L = L or max(12, int(0.25 * min(x1 - x0, y1 - y0)))
    for (ax, ay, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        cv2.line(img, (ax, ay), (ax + dx * L, ay), col, t, cv2.LINE_AA)
        cv2.line(img, (ax, ay), (ax, ay + dy * L), col, t, cv2.LINE_AA)


def tag(img, text, x, y, col, scale=0.6):
    (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.rectangle(img, (x, y - h - 10), (x + w + 12, y), col, -1)
    cv2.putText(img, text, (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, scale, (10, 10, 10), 1, cv2.LINE_AA)


def detect(frame, prev, lost):
    H, W = frame.shape[:2]
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    ground_view = g[int(0.8 * H):].mean() < g[: int(0.4 * H)].mean() - 15   # darker bottom = horizon in view
    ymin = 0 if ground_view else int(0.2 * H)          # looking up: skip the dark dome edge at the top
    ymax = int(0.70 * H) if ground_view else int(0.95 * H)
    sky = g[ymin: max(ymax, ymin + 10)]
    thr = np.median(sky) - 40
    m = (g < thr).astype(np.uint8) * 255
    m[ymax:] = 0
    m[:ymin] = 0
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    best, bs = None, -1e18
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < 25 or a > 0.25 * W * H or x <= 2 or y <= ymin + 2 or x + w >= W - 2 or y + h >= ymax - 2:
            continue
        if prev is not None and lost < 45 and np.hypot(cen[i][0] - prev[0], cen[i][1] - prev[1]) > 0.25 * W:
            continue
        if (prev is None or lost >= 8) and (a < 500 or w < 50):   # (re)acquire only a clearly visible aircraft
            continue
        if w < 1.4 * h * 0.5:  # aircraft blobs are wider than tall (wings), allow head-on views
            continue
        score = a
        if prev is not None:
            d = np.hypot(cen[i][0] - prev[0], cen[i][1] - prev[1])
            score = a - 4.0 * d
        if score > bs:
            bs, best = score, (x, y, x + w, y + h, cen[i][0], cen[i][1])
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--start", type=float, required=True); ap.add_argument("--dur", type=float, default=20)
    ap.add_argument("--crop", default=None, help="x,y,w,h")
    ap.add_argument("--label", default="AIRCRAFT"); ap.add_argument("--tone", default="")
    ap.add_argument("--tone_from", type=float, default=10.0)
    ap.add_argument("--show_from", type=float, default=0.0, help="draw the box only inside this time span")
    ap.add_argument("--show_to", type=float, default=1e9)
    a = ap.parse_args()
    cap = cv2.VideoCapture(a.src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_MSEC, a.start * 1000)
    ok, f = cap.read()
    cx, cy, cw, ch = (map(int, a.crop.split(",")) if a.crop else (0, 0, f.shape[1], f.shape[0]))
    W, H = cw - cw % 2, ch - ch % 2
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                            "-ss", str(a.start), "-t", str(a.dur), "-i", a.src, "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-crf", "17", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000", "-c:a", "aac", "-b:a", "192k",
                            "-shortest", "-map_metadata", "-1", "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
    prev, box, lost, n = None, None, 99, int(round(a.dur * fps))
    hist = []
    for k in range(n):
        if k:
            ok, f = cap.read()
            if not ok:
                break
        fr = f[cy:cy + H, cx:cx + W].copy()
        d = detect(fr, prev, lost)
        if d is not None:
            nb = np.array(d[:4], float)
            box = nb if box is None or lost > 5 else 0.6 * box + 0.4 * nb
            prev, lost = (d[4], d[5]), 0
        else:
            lost += 1
        t = k / fps
        hist = (hist + [d is not None])[-5:]
        if t > a.show_to and a.tone:
            tag(fr, "AIRCRAFT OUT OF VIEW  -  still audible", 14, H - 14, (255, 220, 90))
        if a.show_from <= t <= a.show_to and box is not None and lost <= 8 and sum(hist) >= 3:
            x0, y0, x1, y1 = box.astype(int)
            pad = max(10, int(0.25 * max(x1 - x0, y1 - y0)))
            x0, y0, x1, y1 = max(1, x0 - pad), max(1, y0 - pad), min(W - 2, x1 + pad), min(H - 2, y1 + pad)
            col = GREEN if lost == 0 else RED
            brackets(fr, x0, y0, x1, y1, col)
            tag(fr, f"TARGET LOCKED  {a.label}", x0, max(24, y0 - 4), col)
            if a.tone and t >= a.tone_from:
                tag(fr, f"DOPPLER  tone falling {a.tone}", x0, min(H - 4, y1 + 30), (255, 220, 90))
        cv2.rectangle(fr, (0, 0), (W, 30), (12, 12, 12), -1)
        cv2.putText(fr, f"flyover-check  |  tracking aircraft  |  t = {t:5.2f} s", (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    GREEN, 1, cv2.LINE_AA)
        enc.stdin.write(fr.tobytes())
    enc.stdin.close(); enc.wait()
    print("wrote", a.out)


if __name__ == "__main__":
    sys.exit(main())
