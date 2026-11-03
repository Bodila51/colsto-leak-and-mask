"""Detection for flyover-check: plane flyovers, mains hum, sirens, masking noise.

Idea in one paragraph. A passing plane makes a steady tone (roughly 70-300 Hz, plus
harmonics) whose pitch slides down slowly as it passes (Doppler). On a spectrogram that
is a thin, smooth, slowly sloping line, with copies at 2x, 3x, 4x ... the frequency.
Wind and traffic are wide smears, not lines. Voices also make lines, but they move fast
and break up. So we look for lines that are thin, long, smooth, slowly sliding, and have
harmonics at constant ratios.
"""
from __future__ import annotations

import os

import numpy as np
from scipy import ndimage, signal

SR = 8000           # analysis sample rate (Hz)
NFFT = 4096         # 0.512 s window, 1.95 Hz bins
HOP = 1024          # 0.128 s step
FMIN_S = 30.0       # lowest frequency kept in the spectrogram
FMAX_S = 3000.0     # highest frequency kept
PLANE_BAND = (45.0, 420.0)   # fundamental of a plane tone must sit here
# Two search bands. LOW is where propeller/piston tones live (base tone 45-420 Hz, harmonics up to 3 kHz).
# HIGH is where turbofan fan-tone stacks live (a base tone of about 0.45-3 kHz with harmonics up to 7.5 kHz),
# analysed with a shorter window because those lines slide fast in absolute Hz.
LOW_BAND = dict(name="low", sr=8000, nfft=4096, hop=1024, fmin=30.0, fmax=3000.0, plane=(45.0, 420.0), cand=(40.0, 1800.0))
HIGH_BAND = dict(name="high", sr=16000, nfft=2048, hop=512, fmin=300.0, fmax=7500.0, plane=(450.0, 3200.0), cand=(450.0, 7500.0),
                 peak_db=8.5, max_miss_s=0.3, gate_rel=0.006, min_cov=0.35)
PEAK_DB = 7.0       # a spectral peak must stand this far above its neighbourhood
HARM_DB = 8.0       # a harmonic must reach this to count as present


def mmss(t: float) -> str:
    t = max(0.0, float(t))
    m, s = divmod(int(round(t)), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ----------------------------------------------------------------------------
# spectrogram
# ----------------------------------------------------------------------------

def compute_spec(x: np.ndarray, sr: int = SR, nfft: int = NFFT, hop: int = HOP,
                 fmin: float = FMIN_S, fmax: float = FMAX_S):
    """Return S (frames x bins, dB of a sinusoid's amplitude), times (s), freqs (Hz)."""
    NFFT_, HOP_ = nfft, hop
    x = np.asarray(x, dtype=np.float32)
    pad = NFFT_ // 2
    tail = (-(len(x) + 2 * pad - NFFT_)) % HOP_
    xp = np.pad(x, (pad, pad + tail))
    n = 1 + (len(xp) - NFFT_) // HOP_
    win = signal.windows.hann(NFFT_, sym=False).astype(np.float32)
    k0 = int(fmin * NFFT_ / sr)
    k1 = int(fmax * NFFT_ / sr) + 1
    norm = 2.0 / win.sum()
    S = np.empty((n, k1 - k0), np.float32)
    view = np.lib.stride_tricks.sliding_window_view(xp, NFFT_)[::HOP_]
    for a in range(0, n, 256):
        X = np.fft.rfft(view[a:a + 256] * win, axis=1)[:, k0:k1]
        S[a:a + 256] = 20.0 * np.log10(np.abs(X) * norm + 1e-9)
    freqs = np.arange(k0, k1) * sr / NFFT_
    times = np.arange(n) * HOP_ / sr
    return S, times, freqs


def peakiness(S: np.ndarray):
    """P = how far each cell stands above the local noise floor (dB).
    P2 = P with long-lasting steady lines (hum, fans) taken out, so only things that move remain."""
    sub = S[::4]
    floor_sub = ndimage.median_filter(sub, size=(1, 41), mode="nearest")
    floor = np.repeat(floor_sub, 4, axis=0)[: S.shape[0]]
    P = S - floor
    if P.shape[0] >= 100:
        base = np.maximum(np.median(P, axis=0), 0.0)
        P2 = P - base[None, :]
    else:
        P2 = P
    return P.astype(np.float32), P2.astype(np.float32)


# ----------------------------------------------------------------------------
# ridge tracking
# ----------------------------------------------------------------------------

def pick_peaks(P: np.ndarray, freqs: np.ndarray, thr: float = PEAK_DB, maxk: int = 12):
    T = P.shape[0]
    df = freqs[1] - freqs[0]
    core = P[:, 1:-1]
    m = (core > P[:, :-2]) & (core >= P[:, 2:]) & (core >= thr)
    rows, cols = np.nonzero(m)
    cols = cols + 1
    b = P[rows, cols]
    a = P[rows, cols - 1]
    c = P[rows, cols + 1]
    den = a - 2 * b + c
    delta = np.where(den < -1e-6, 0.5 * (a - c) / np.where(den < -1e-6, den, -1.0), 0.0)
    fhz = freqs[0] + (cols + np.clip(delta, -0.5, 0.5)) * df
    bounds = np.searchsorted(rows, np.arange(T + 1))
    peaks = []
    for t in range(T):
        lo, hi = bounds[t], bounds[t + 1]
        if hi == lo:
            peaks.append((np.empty(0), np.empty(0)))
            continue
        f, p = fhz[lo:hi], b[lo:hi]
        if len(f) > maxk:
            o = np.argsort(p)[-maxk:]
            f, p = f[o], p[o]
        peaks.append((f, p))
    return peaks


def link_tracks(peaks, dt: float, df: float, max_miss: int | None = None, min_pts: int | None = None,
                gate_rel: float = 0.012):
    """Join peaks in consecutive frames into tracks, predicting each next frequency from the slope."""
    max_miss = max_miss or max(2, round(1.0 / dt))
    min_pts = min_pts or max(5, round(1.28 / dt))
    active = []   # dicts
    done = []
    for ti, (pf, pp) in enumerate(peaks):
        used_p = np.zeros(len(pf), bool)
        if active and len(pf):
            lastf = np.array([a["f"][-1] for a in active])
            slope = np.array([a["slope"] for a in active])
            gap = np.array([ti - a["t"][-1] for a in active], float)
            pred = lastf + slope * gap * dt
            gate = 1.6 * df + (gate_rel + gate_rel / 2 * (gap - 1)) * lastf
            cost = np.abs(pf[None, :] - pred[:, None]) / gate[:, None]
            cand = np.argwhere(cost <= 1.0)
            if len(cand):
                order = np.argsort(cost[cand[:, 0], cand[:, 1]])
                used_a = set()
                for o in order:
                    ai, pi = cand[o]
                    if ai in used_a or used_p[pi]:
                        continue
                    used_a.add(ai)
                    used_p[pi] = True
                    a = active[ai]
                    inst = (pf[pi] - a["f"][-1]) / ((ti - a["t"][-1]) * dt)
                    al = min(0.3, dt / 0.4)       # same smoothing time whatever the frame rate
                    a["slope"] = (1 - al) * a["slope"] + al * inst
                    a["t"].append(ti)
                    a["f"].append(float(pf[pi]))
                    a["p"].append(float(pp[pi]))
        for pi in np.nonzero(~used_p)[0]:
            active.append({"t": [ti], "f": [float(pf[pi])], "p": [float(pp[pi])], "slope": 0.0})
        still = []
        for a in active:
            if ti - a["t"][-1] > max_miss:
                if len(a["t"]) >= min_pts:
                    done.append(a)
            else:
                still.append(a)
        active = still
    for a in active:
        if len(a["t"]) >= min_pts:
            done.append(a)
    return done



def stitch_tracks(raw: list[dict], dt: float, df: float, max_gap: int | None = None, min_len: int | None = None):
    """Join pieces of the same line that broke (crossing another line, a brief dropout):
    the next piece must start within ~1.8 s and continue the pitch and slope of the last."""
    import bisect
    max_gap = max_gap or max(4, round(1.8 / dt))
    min_len = min_len or max(8, round(1.5 / dt))
    n_edge = max(4, round(1.3 / dt))
    lead = max(2, round(0.5 / dt))
    items = sorted([r for r in raw if len(r["t"]) >= min_len], key=lambda r: r["t"][0])
    short = [r for r in raw if len(r["t"]) < min_len]
    starts = [r["t"][0] for r in items]
    used = [False] * len(items)
    out = []

    def edge_slope(tl, fl, head):
        n = min(n_edge, len(tl))
        if n < 4:
            return 0.0
        tt = np.array(tl[:n] if head else tl[-n:], float) * dt
        ff = np.array(fl[:n] if head else fl[-n:], float)
        return float(np.polyfit(tt, ff, 1)[0])

    for i, A in enumerate(items):
        if used[i]:
            continue
        used[i] = True
        cur = {"t": list(A["t"]), "f": list(A["f"]), "p": list(A["p"]), "slope": 0.0}
        while True:
            ta, fa = cur["t"][-1], cur["f"][-1]
            sa = edge_slope(cur["t"], cur["f"], False)
            best = None
            for j in range(bisect.bisect_left(starts, ta - lead), len(items)):
                if starts[j] > ta + max_gap:
                    break
                if used[j]:
                    continue
                B = items[j]
                gap = (B["t"][0] - ta) * dt
                pred = fa + sa * gap
                tol = 2.5 * df + 0.02 * fa + abs(sa) * 0.3
                err = abs(B["f"][0] - pred)
                sb = edge_slope(B["t"], B["f"], True)
                if err <= tol and abs(sb - sa) <= 0.06 * fa + 1.0:
                    c = err / tol
                    if best is None or c < best[0]:
                        best = (c, j)
            if best is None:
                break
            j = best[1]
            used[j] = True
            B = items[j]
            keep = [k for k, tt in enumerate(B["t"]) if tt > ta]
            cur["t"] += [B["t"][k] for k in keep]
            cur["f"] += [B["f"][k] for k in keep]
            cur["p"] += [B["p"][k] for k in keep]
        out.append(cur)
    return out + short


def _odd(n: int) -> int:
    n = int(n)
    return n if n % 2 else n + 1


def track_features(tr: dict, dt: float) -> dict:
    ti = np.array(tr["t"])
    fi = np.array(tr["f"])
    pi = np.array(tr["p"])
    grid = np.arange(ti[0], ti[-1] + 1)
    fg = np.interp(grid, ti, fi)
    pg = np.interp(grid, ti, pi)
    w = _odd(max(5, round(2.0 / dt)))
    if len(fg) > w:
        fm = ndimage.median_filter(fg, size=5, mode="nearest")
        fs = signal.savgol_filter(fm, w, 2)
        rate = signal.savgol_filter(np.log(np.maximum(fs, 1.0)), w, 2, deriv=1, delta=dt)
    else:
        fs = fg.copy()
        rate = np.gradient(np.log(np.maximum(fs, 1.0)), dt)
    arate = np.abs(rate)
    moving = arate > 0.002
    down = float(np.mean(rate[moving] < 0)) if moving.any() else 0.5
    rough = float(np.sqrt(np.mean((fg - fs) ** 2)))
    dur = (ti[-1] - ti[0]) * dt
    # how fast does the raw trace move, over ~0.13 s steps? (jitter in a fine-grained spectrogram would otherwise
    # look like fast gliding; voices glide fast over that step, planes do not)
    lag = max(1, round(0.128 / dt))
    lf = dict(zip(ti.tolist(), np.log(np.maximum(fi, 1.0))))
    rr = np.array([abs(lf[t + lag] - lf[t]) / (lag * dt) for t in lf if (t + lag) in lf])
    raw_p90 = float(np.percentile(rr, 90)) if len(rr) >= 5 else 0.0
    n_gaps = int(np.sum(np.diff(ti) > max(3, round(0.4 / dt))))                 # dropouts longer than ~0.4 s (beeps, pulsing)
    return {
        "raw_p90": raw_p90, "gap_rate": float(n_gaps / max((ti[-1] - ti[0]) * dt, 1.0)),
        "i0": int(ti[0]), "i1": int(ti[-1]), "grid": grid, "f": fs, "p": pg,
        "dur": float(dur), "cov": float(len(ti) / len(grid)),
        "fmed": float(np.median(fs)), "pmean": float(pi.mean()),
        "span_ln": float(np.log(fs.max() / max(fs.min(), 1.0))),
        "rate_med": float(np.median(arate)), "rate_p90": float(np.percentile(arate, 90)),
        "down": down, "rough": rough, "n": int(len(ti)),
    }


# ----------------------------------------------------------------------------
# harmonic grouping and scoring
# ----------------------------------------------------------------------------

def _common(a: dict, b: dict, min_frames: int = 10):
    lo, hi = max(a["i0"], b["i0"]), min(a["i1"], b["i1"])
    if hi - lo < min_frames:
        return None
    fa = a["f"][lo - a["i0"]: hi - a["i0"] + 1]
    fb = b["f"][lo - b["i0"]: hi - b["i0"] + 1]
    return lo, hi, fa, fb


def probe_harmonic(P2: np.ndarray, freqs: np.ndarray, idx: np.ndarray, ftarget: np.ndarray, k: int):
    """How strongly does the spectrogram show a line near ftarget(t)? Returns (frac, mean_db, best_f)."""
    df = freqs[1] - freqs[0]
    W = 2 + k // 2
    cb = np.round((ftarget - freqs[0]) / df).astype(int)
    ok = (cb - W >= 0) & (cb + W < P2.shape[1])
    if ok.sum() < 5:
        return 0.0, 0.0, None
    offs = np.arange(-W, W + 1)
    bins = np.clip(cb[:, None] + offs[None, :], 0, P2.shape[1] - 1)
    vals = P2[idx[:, None], bins]
    am = vals.argmax(axis=1)
    best = vals[np.arange(len(idx)), am]
    bestf = freqs[bins[np.arange(len(idx)), am]]
    hit = (best >= HARM_DB) & ok
    frac = float(hit.mean())
    mean = float(best[hit].mean()) if hit.any() else 0.0
    return frac, mean, np.where(hit, bestf, np.nan)


def group_flyovers(tracks_f: list[dict], P2: np.ndarray, freqs: np.ndarray, dt: float, min_dur: float,
                   band: dict = LOW_BAND):
    """Merge tracks that are harmonics of one source and score each source as a possible plane."""
    cand = [t for t in tracks_f
            if t["dur"] >= 3.0 and t["rate_p90"] <= 0.30 and t["rate_med"] <= 0.15
            and t["span_ln"] >= 0.02 and t["cov"] >= band.get("min_cov", 0.6) and t["pmean"] >= 7.5 and t["raw_p90"] <= 0.10 and t["gap_rate"] <= 0.4
            and t["rough"] <= 1.0 + 0.01 * t["fmed"] and band["cand"][0] <= t["fmed"] <= band["cand"][1]]
    cand.sort(key=lambda t: t["fmed"])
    taken = set()
    groups = []
    for i, head in enumerate(cand):
        if i in taken:
            continue
        members = [(1, head)]
        taken.add(i)
        for j in range(i + 1, len(cand)):
            if j in taken:
                continue
            c = _common(head, cand[j], max(5, round(1.3 / dt)))
            if c is None:
                continue
            lo, hi, fa, fb = c
            q = fb / np.maximum(fa, 1.0)
            k = int(round(np.median(q)))
            if 2 <= k <= 14 and abs(np.median(q) / k - 1) < 0.012 and np.std(q / k) < 0.012:
                members.append((k, cand[j]))
                taken.add(j)
        groups.append(members)

    out = []
    for members in groups:
        # fundamental trace on the union of frames, from all member harmonics (f_k / k)
        i0 = min(t["i0"] for _, t in members)
        i1 = max(t["i1"] for _, t in members)
        n = i1 - i0 + 1
        sw = np.zeros(n)
        sf = np.zeros(n)
        for k, t in members:
            w = np.maximum(t["p"], 1.0) * (1.0 if k == 1 else 0.7)
            sl = slice(t["i0"] - i0, t["i1"] - i0 + 1)
            sw[sl] += w
            sf[sl] += w * t["f"] / k
        have = sw > 0
        f1 = np.full(n, np.nan)
        f1[have] = sf[have] / sw[have]
        idx = np.arange(i0, i1 + 1)
        f1 = np.interp(idx, idx[have], f1[have])
        win = _odd(max(5, round(2.0 / dt)))
        if n > win:
            f1 = signal.savgol_filter(f1, win, 2)
        # is the lowest member really the fundamental? look for a line at f/2, f/3
        sub = 1
        for s in (3, 2):
            frac, mean, _ = probe_harmonic(P2, freqs, idx, f1 / s, 1)
            if frac >= 0.35 and mean >= 9.0 and band['plane'][0] * 0.9 <= np.median(f1) / s:
                sub = s
                break
        if sub > 1:
            f1 = f1 / sub
        fmed = float(np.median(f1))
        if not (band['plane'][0] <= fmed <= band['plane'][1]):
            continue
        # which harmonics are really there?
        supp = []
        kmax = int(min(band['fmax'] / max(f1.max(), 1.0), 40))
        prev = 0
        for k in range(1, kmax + 1):
            frac, mean, _ = probe_harmonic(P2, freqs, idx, f1 * k, k)
            if frac >= 0.35 and mean >= 9.0:
                if k - prev > 3:       # a lone line far above the others is a coincidence
                    break
                supp.append((k, round(frac, 2), round(mean, 1)))
                prev = k
            elif k - prev > 3:
                break
        if not supp:
            continue
        pm = float(np.mean([m for _, _, m in supp[:3]]))
        dur = (i1 - i0) * dt
        rough = float(np.median([t["rough"] / max(t["fmed"], 1) for _, t in members]))
        out.append({
            "i0": int(i0), "i1": int(i1), "idx": idx, "f1": f1, "harmonics": supp, "rough": rough,
            "dt": float(dt), "band": band["name"],
            "start_s": float(i0 * dt), "end_s": float(i1 * dt), "dur_s": float(dur),
            "f_start": float(f1[0]), "f_end": float(f1[-1]), "f_median": fmed, "prom_db": pm,
        })
    return drop_harmonic_duplicates(merge_groups(out, dt), dt)



def merge_groups(groups: list[dict], dt: float, max_gap_s: float = 25.0) -> list[dict]:
    """One plane often shows up as two groups (its line broke where it crossed another). Merge groups
    that overlap with the same pitch, or that follow each other with the same pitch and harmonics."""
    gs = sorted(groups, key=lambda g: g["start_s"])
    changed = True
    while changed:
        changed = False
        for i in range(len(gs)):
            for j in range(i + 1, len(gs)):
                A, B = gs[i], gs[j]
                lo, hi = max(A["i0"], B["i0"]), min(A["i1"], B["i1"])
                ha, hb = {h[0] for h in A["harmonics"]}, {h[0] for h in B["harmonics"]}
                share = len(ha & hb) / max(1, min(len(ha), len(hb)))
                same = False
                if hi - lo >= 5:
                    fa = A["f1"][lo - A["i0"]: hi - A["i0"] + 1]
                    fb = B["f1"][lo - B["i0"]: hi - B["i0"] + 1]
                    same = float(np.mean(np.abs(np.log(fa / fb)))) <= 0.03 and share >= 0.5
                else:
                    gap = B["start_s"] - A["end_s"]
                    if 0 <= gap <= max_gap_s and share >= 0.6:
                        same = abs(np.log(B["f_start"] / A["f_end"])) <= 0.06
                if not same:
                    continue
                i0, i1 = min(A["i0"], B["i0"]), max(A["i1"], B["i1"])
                idx = np.arange(i0, i1 + 1)
                f = np.full(len(idx), np.nan)
                w = np.zeros(len(idx))
                acc = np.zeros(len(idx))
                for G in (A, B):
                    sl = slice(G["i0"] - i0, G["i1"] - i0 + 1)
                    acc[sl] += G["f1"]
                    w[sl] += 1
                have = w > 0
                f[have] = acc[have] / w[have]
                f = np.interp(idx, idx[have], f[have])
                hs = {}
                for G in (A, B):
                    for h in G["harmonics"]:
                        if h[0] not in hs or h[2] > hs[h[0]][2]:
                            hs[h[0]] = h
                M = dict(A if A["prom_db"] >= B["prom_db"] else B)
                M.update({"i0": i0, "i1": i1, "idx": idx, "f1": f, "harmonics": [hs[k] for k in sorted(hs)],
                          "start_s": float(i0 * dt), "end_s": float(i1 * dt), "dur_s": float((i1 - i0) * dt),
                          "f_start": float(f[0]), "f_end": float(f[-1]), "f_median": float(np.median(f)),
                          "prom_db": float(max(A["prom_db"], B["prom_db"])), "rough": min(A["rough"], B["rough"])})
                gs[i] = M
                del gs[j]
                changed = True
                break
            if changed:
                break
    return gs


def finalize_groups(groups: list[dict], dt: float, min_dur: float):
    """Judge each merged group. Real flyovers are bounded in length, slide by a visible amount, follow a
    Doppler-like curve (or carry a rich family of harmonics), and mostly fall in pitch. Anything else that
    merely hums at a changing pitch (machinery, wind, a drifting generator) is dropped here."""
    kept, n_weak = [], 0
    for g in groups:
        f1, idx = g["f1"], g["idx"]
        n = len(f1)
        dur = g["dur_s"]
        if dur < min_dur or dur > 240.0:
            continue
        span_ln = float(np.log(f1.max() / f1.min()))
        if span_ln < 0.03:
            continue
        win = _odd(max(5, round(2.0 / dt)))
        rate = signal.savgol_filter(np.log(f1), win, 2, deriv=1, delta=dt) if n > win else np.gradient(np.log(f1), dt)
        moving = np.abs(rate) > 0.002
        down = float(np.mean(rate[moving] < 0)) if moving.any() else 0.5
        nh = len(g["harmonics"])
        pm = g["prom_db"]
        fit, rms = fit_doppler(idx * dt, f1)
        fit_ok = bool(fit is not None and rms < 0.025 and 10 <= fit[1] <= 340 and fit[2] >= 10)
        irregular = not (fit_ok and g["f_end"] <= g["f_start"] * 1.02)      # a plane's pitch ends no higher than it began
        if os.environ.get("FLYOVER_DEBUG"):
            print(f"  [group {g['start_s']:.0f}-{g['end_s']:.0f}s f={g['f_start']:.0f}->{g['f_end']:.0f} nh={nh} span={span_ln:.3f} "
                  f"pm={pm:.1f} down={down:.2f} fit_ok={fit_ok} rms={rms} dur={dur:.0f}]")
        if irregular and not (nh >= 3 and span_ln >= 0.06 and pm >= 12 and dur >= 12 and down >= 0.5):
            continue
        if nh == 1 and (pm < 12 or dur < 8 or down < 0.85):
            continue
        d = float(np.clip((dur - 4.0) / 16.0, 0, 1))
        s_ = float(np.clip((pm - 8.0) / 12.0, 0, 1))
        h = 0.35 if nh == 1 else 0.7 if nh == 2 else 1.0
        sl = float(np.clip((span_ln - 0.03) / 0.10, 0, 1))
        sm = float(1 - np.clip(g["rough"] / 0.01, 0, 1))
        mono = 1.0 if (fit_ok and g["f_end"] <= g["f_start"] * 1.02) else float(np.clip((down - 0.5) / 0.5, 0, 1))
        conf = 0.25 * d + 0.20 * s_ + 0.20 * h + 0.15 * sl + 0.10 * sm + 0.10 * mono
        reasons = []
        kind = "plane"
        if not fit_ok or down < 0.75:
            kind = "irregular glide"
            conf *= 0.85
            reasons.append("the pitch does not follow a single clean Doppler curve (turning plane, changing engine "
                           "power, or a different source)")
        else:
            reasons.append(f"pitch follows a Doppler curve (about {fit[1]:.0f} m/s, closest about {fit[2]:.0f} m)")
        reasons.append("only the base tone is visible, no harmonics" if nh == 1 else f"{nh} harmonics at fixed ratios")
        reasons.append(f"pitch slides {g['f_start']:.0f} -> {g['f_end']:.0f} Hz over {dur:.0f} s")
        if dur < 10:
            reasons.append("short")
        low_t = 0.33 if g.get("band") == "high" else 0.4      # fan-tone lines are only clear for a few seconds
        label = "high" if conf >= 0.75 else "medium" if conf >= 0.55 else "low" if conf >= low_t else None
        if label is None:
            n_weak += 1
            continue
        g.update({"span_ln": span_ln, "down_frac": down, "confidence": float(conf), "label": label,
                  "kind": kind, "reasons": reasons, "doppler_fit": None if fit is None else [float(v) for v in fit]})
        kept.append(g)
    kept.sort(key=lambda g: g["start_s"])
    for a in kept:
        a["overlaps"] = []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            ov = min(kept[i]["end_s"], kept[j]["end_s"]) - max(kept[i]["start_s"], kept[j]["start_s"])
            if ov >= 3.0:
                kept[i]["overlaps"].append(j)
                kept[j]["overlaps"].append(i)
    return kept, n_weak


def drop_harmonic_duplicates(groups: list[dict], dt: float | None = None) -> list[dict]:
    """A group whose 'base tone' is really a whole multiple of another group's base tone (same time, fixed
    ratio 2, 3, ...) is that group's own harmonic, not a second plane. Works across the two search bands."""
    gs = sorted(groups, key=lambda g: g["f_median"])
    drop = set()
    for i, A in enumerate(gs):
        for j in range(i + 1, len(gs)):
            if j in drop or i in drop:
                continue
            B = gs[j]
            lo, hi = max(A["start_s"], B["start_s"]), min(A["end_s"], B["end_s"])
            if hi - lo < 1.0:
                continue
            tt = np.arange(lo, hi, 0.25)
            fa = np.interp(tt, A["idx"] * A["dt"], A["f1"])
            fb = np.interp(tt, B["idx"] * B["dt"], B["f1"])
            q = fb / fa
            k = int(round(float(np.median(q))))
            if k >= 2 and abs(np.median(q) / k - 1) <= 0.03 and np.std(q / k) <= 0.03 \
                    and B["start_s"] >= A["start_s"] - 4 and B["end_s"] <= A["end_s"] + 4:
                drop.add(j)
    return [g for j, g in enumerate(gs) if j not in drop]


def mark_overlaps(groups: list[dict]) -> list[dict]:
    groups = sorted(groups, key=lambda g: g["start_s"])
    for g in groups:
        g["overlaps"] = []
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            ov = min(groups[i]["end_s"], groups[j]["end_s"]) - max(groups[i]["start_s"], groups[j]["start_s"])
            if ov >= 3.0:
                groups[i]["overlaps"].append(j)
                groups[j]["overlaps"].append(i)
    return groups


def doppler_model(t, p):
    """Pitch of a source passing at constant speed v (m/s), closest distance d (m), closest at time tc."""
    fr, v, d, tc = p
    c = 343.0
    r = np.sqrt(d * d + (v * (t - tc)) ** 2)
    return fr * c / (c - v * v * (tc - t) / r)


def fit_doppler(tg: np.ndarray, f: np.ndarray):
    """Robust fit of a straight-line flyover to the traced pitch. Returns (params, relative rms of inliers)
    or (None, None). Outliers (a trace pulled off by a voice, say) do not drag the fit."""
    from scipy.optimize import least_squares
    if len(tg) < 20:
        return None, None
    k0 = int(np.argmax(np.abs(np.gradient(f))))
    best = None
    lo = [0.4 * f.min(), 15, 20, tg[0] - 20]
    hi = [1.6 * f.max(), 200, 6000, tg[-1] + 20]
    for v0, d0 in ((70, 300), (120, 600), (50, 150)):
        p0 = [float(np.sqrt(f[0] * f[-1])), v0, d0, float(tg[k0])]
        try:
            r_ = least_squares(lambda q: (doppler_model(tg, q) - f) / f, p0, bounds=(lo, hi),
                               loss="soft_l1", f_scale=0.01)
        except Exception:
            continue
        if best is None or r_.cost < best.cost:
            best = r_
    if best is None:
        return None, None
    res = (doppler_model(tg, best.x) - f) / f
    inl = np.abs(res) <= max(3.0 * 1.4826 * np.median(np.abs(res)), 0.005)
    if inl.mean() < 0.6:
        return None, None
    return best.x, float(np.sqrt(np.mean(res[inl] ** 2)))


def extend_trace(g: dict, dt: float, total_s: float, ext_s: float = 30.0):
    """Pitch to notch along: the traced part, repaired where the trace was pulled off course, and extended
    ext_s beyond both ends. A plane in straight flight follows a Doppler curve, so we fit one to the trace
    (ignoring outliers). If it does not fit (turning plane), we keep the measured trace and let its slope fade."""
    tg = g["idx"] * dt
    f = g["f1"]
    if tg[-1] - tg[0] < 15.0:
        ext_s = min(ext_s, 15.0)        # a short stretch pins the curve down poorly; do not extrapolate far
    te = np.arange(max(0.0, tg[0] - ext_s), min(total_s, tg[-1] + ext_s) + dt / 2, dt)
    fit, rms = fit_doppler(tg, f)
    good = fit is not None and rms < 0.012
    fe = np.empty_like(te)
    inside = (te >= tg[0]) & (te <= tg[-1])
    fe[inside] = np.interp(te[inside], tg, f)
    if good:
        m = doppler_model(te, fit)
        sig = 1.4826 * np.median(np.abs(doppler_model(tg, fit) - f))
        bad = np.abs(fe - m) > np.maximum(3.5 * sig, 0.005 * m)
        fe[~inside | bad] = m[~inside | bad]
    else:
        for side, (mask, edge_t, edge_f) in enumerate(((te < tg[0], tg[0], f[0]), (te > tg[-1], tg[-1], f[-1]))):
            if not mask.any():
                continue
            n = min(len(f), 40)
            sl = np.polyfit(tg[:n], f[:n], 1)[0] if side == 0 else np.polyfit(tg[-n:], f[-n:], 1)[0]
            dtm = np.abs(te[mask] - edge_t)
            tau = 8.0
            fe[mask] = edge_f + np.sign(te[mask] - edge_t) * sl * tau * (1 - np.exp(-dtm / tau))
    # whatever the fit says, the pitch before the traced part cannot be wildly higher, nor after it wildly lower
    before = te < tg[0]
    after = te > tg[-1]
    fe[before] = np.minimum(fe[before], f[0] * 1.35)
    fe[after] = np.maximum(fe[after], f[-1] / 1.35)
    return te, np.maximum(fe, 20.0), bool(good)


# ----------------------------------------------------------------------------
# voices, roar
# ----------------------------------------------------------------------------

def _merge_intervals(iv, gap):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def find_voice_zones(tracks_f, dt):
    """Best effort: stretches full of short, broken, fast-moving lines in the voice range."""
    iv = []
    n = 0
    for t in tracks_f:
        if 0.5 <= t["dur"] <= 8.0 and 70 <= t["fmed"] <= 600 and (t["raw_p90"] > 0.08 or t["cov"] < 0.55):
            iv.append((t["i0"] * dt, t["i1"] * dt))
            n += 1
    zones = _merge_intervals(iv, 2.0)
    return [(a_, b_) for a_, b_ in zones if b_ - a_ >= 2.0 and n >= 6], n


def find_roar(S, freqs, dt):
    """Stretches where loud, flat (noise-like) sound fills 50-400 Hz: wind, traffic, engines."""
    sel = (freqs >= 50) & (freqs <= 400)
    pw = 10 ** (S[:, sel] / 10.0)
    lb = 10 * np.log10(pw.mean(axis=1) + 1e-12)
    lmean = S[:, sel].mean(axis=1)
    flat = lmean - lb          # ~ -2.5 dB for pure noise, very negative for a few strong lines
    ref = np.percentile(lb, 10)
    loud = (lb >= ref + 12) & (flat >= -5.0)
    k = ndimage.binary_closing(loud, np.ones(8, bool))
    lab, n = ndimage.label(k)
    zones = []
    for i in range(1, n + 1):
        ix = np.nonzero(lab == i)[0]
        a, b = ix[0] * dt, ix[-1] * dt
        if b - a >= 3.0:
            zones.append((float(a), float(b)))
    return zones


# ----------------------------------------------------------------------------
# mains hum
# ----------------------------------------------------------------------------

def _welch_peak(f, pdb, fc, half=0.7):
    """Prominence (dB above neighbours) and interpolated frequency of the PSD peak near fc."""
    win = (f >= fc - half) & (f <= fc + half)
    nb = ((f >= fc - 15) & (f < fc - 3)) | ((f > fc + 3) & (f <= fc + 15))
    if not win.any() or nb.sum() < 6:
        return None
    ix = np.nonzero(win)[0]
    i = ix[int(np.argmax(pdb[ix]))]
    fr = float(f[i])
    if 0 < i < len(f) - 1:
        a_, b_, c_ = pdb[i - 1], pdb[i], pdb[i + 1]
        den = a_ - 2 * b_ + c_
        if den < 0:
            fr += 0.5 * (a_ - c_) / den * (f[1] - f[0])
    return fr, float(pdb[i] - np.median(pdb[nb]))


def find_hum(x: np.ndarray, sr: int = SR):
    """Look for mains hum: narrow steady lines at 50 or 60 Hz and their multiples."""
    if len(x) < sr * 4:
        return None
    nper = 16384 if len(x) >= 16384 * 2 else 8192
    f, pxx = signal.welch(x, sr, nperseg=nper, noverlap=nper // 2, window="hann", detrend=False)
    pdb = 10 * np.log10(pxx + 1e-20)
    best = None
    for grid in (50.0, 60.0):
        present = []
        for k in range(1, 15):
            fc = grid * k
            if fc > 900:
                break
            if abs(fc - 300) < 1 or abs(fc - 600) < 1:
                continue    # shared by both grids; tells us nothing
            r = _welch_peak(f, pdb, fc)
            if r and r[1] >= 10.0:
                present.append((k, r[0], r[1]))
        score = sum(p for _, _, p in present)
        if present and (len(present) >= 2 or present[0][2] >= 20.0):
            if best is None or score > best["score"]:
                best = {"grid_hz": grid, "score": float(score),
                        "harmonics": [{"k": k, "freq_hz": fq, "above_floor_db": pr} for k, fq, pr in present]}
    if best is None:
        return None
    top = max(best["harmonics"], key=lambda h: h["above_floor_db"] + 0.02 * h["k"])
    mains = top["freq_hz"] / top["k"]
    wander = enf_trace(x, sr, top["freq_hz"], top["k"])
    if wander:
        mains = wander["median_hz"]
    best["wander"] = wander
    best["mains_hz_median"] = float(mains)
    best["spread_hz"] = float((wander or {}).get("std_hz", 0.02))
    # notch the first eight multiples whether or not each stands out (the frequency is known precisely and a
    # narrow notch costs almost nothing), and any higher one that does stand out
    notch = []
    for k in range(1, 16):
        if mains * k > 900:
            break
        r = _welch_peak(f, pdb, mains * k, half=0.9)
        prom = r[1] if r else 0.0
        if k <= 8 or prom >= 6.0:
            notch.append({"k": k, "freq_hz": float(mains * k), "above_floor_db": float(prom)})
    best["notch"] = notch
    return best


def enf_trace(x, sr, fc, k):
    n = 32768
    if len(x) < n * 2:
        return None
    hop = sr  # 1 s
    nfr = 1 + (len(x) - n) // hop
    win = signal.windows.hann(n, sym=False)
    lo = int((fc - 4) * n / sr)
    hi = int((fc + 4) * n / sr) + 2
    view = np.lib.stride_tricks.sliding_window_view(x, n)[::hop]
    out = np.full(nfr, np.nan)
    for a in range(0, nfr, 128):
        X = np.abs(np.fft.rfft(view[a:a + 128] * win, axis=1)[:, lo:hi])
        i = X.argmax(axis=1)
        for r, ii in enumerate(i):
            if 1 <= ii < X.shape[1] - 1:
                al, be, ga = np.log(X[r, ii - 1] + 1e-12), np.log(X[r, ii] + 1e-12), np.log(X[r, ii + 1] + 1e-12)
                den = al - 2 * be + ga
                d = 0.5 * (al - ga) / den if den < 0 else 0.0
                out[a + r] = (lo + ii + d) * sr / n / k
    out = out[np.isfinite(out)]
    if len(out) < 5:
        return None
    return {"min_hz": float(out.min()), "max_hz": float(out.max()),
            "std_hz": float(out.std()), "median_hz": float(np.median(out)), "n": int(len(out))}


# ----------------------------------------------------------------------------
# sirens (best effort, rough region hint only)
# ----------------------------------------------------------------------------

def _stft_band(x, sr, nfft, hop, fmin, fmax):
    x = np.asarray(x, np.float32)
    n = 1 + max(0, (len(x) - nfft) // hop)
    win = signal.windows.hann(nfft, sym=False).astype(np.float32)
    k0, k1 = int(fmin * nfft / sr), int(fmax * nfft / sr) + 1
    S = np.empty((n, k1 - k0), np.float32)
    view = np.lib.stride_tricks.sliding_window_view(x, nfft)[::hop]
    for a_ in range(0, n, 512):
        X = np.fft.rfft(view[a_:a_ + 512] * win, axis=1)[:, k0:k1]
        S[a_:a_ + 512] = 20.0 * np.log10(np.abs(X) * (2.0 / win.sum()) + 1e-9)
    return S, (np.arange(n) * hop + nfft / 2) / sr, np.arange(k0, k1) * sr / nfft


def find_sirens(x, flyovers, sr=SR):
    """Best-effort siren hint: a long, strong, clean tone in 300-1800 Hz that either alternates between
    two pitches (hi-lo) or sweeps up and down again and again (wail / yelp). Uses a shorter window than
    the plane search because sirens glide fast."""
    if len(x) < sr * 4:
        return []
    S, times, freqs = _stft_band(x, sr, 1024, 512, 250, 2000)
    dt = float(times[1] - times[0])
    df = float(freqs[1] - freqs[0])
    floor = np.repeat(ndimage.median_filter(S[::4], size=(1, 31), mode="nearest"), 4, axis=0)[: S.shape[0]]
    P = S - floor
    if P.shape[0] >= 200:
        P = P - np.maximum(np.median(P, axis=0), 0.0)[None, :]
    band = np.nonzero(freqs >= 300)[0]
    P = P[:, band]
    fr_axis = freqs[band]
    for g in flyovers:                                  # do not mistake plane harmonics for a siren
        tt = g.get("ext_t", g["idx"] * (HOP / sr))
        ff = g.get("ext_f1", g["f1"])
        rows = np.nonzero((times >= tt[0]) & (times <= tt[-1]))[0]
        if not len(rows):
            continue
        base = np.interp(times[rows], tt, ff)
        for k, _, _ in g["harmonics"]:
            c = np.round((base * k - fr_axis[0]) / df).astype(int)
            for off in range(-5, 6):
                cc = c + off
                okk = (cc >= 0) & (cc < P.shape[1])
                P[rows[okk], cc[okk]] = 0.0
    mx = P.max(axis=1)
    strong = P >= np.maximum(14.0, mx - 10.0)[:, None]
    left_ok = np.c_[np.ones((P.shape[0], 1), bool), P[:, 1:] >= P[:, :-1]]
    right_ok = np.c_[P[:, :-1] >= P[:, 1:], np.ones((P.shape[0], 1), bool)]
    isl = strong & left_ok & right_ok
    ib = np.where(isl.any(axis=1), isl.argmax(axis=1), P.argmax(axis=1))
    pm = P[np.arange(len(ib)), ib]
    fr = fr_axis[ib]
    tonal = (mx >= 14.0) & (pm >= 12.0)
    gap_ok = max(int(0.3 / dt), 2)
    runs, start, last = [], None, None
    for t in range(len(tonal)):
        if tonal[t]:
            if start is None:
                start = t
            elif t - last > gap_ok or abs(fr[t] - fr[last]) > 350:
                runs.append((start, last))
                start = t
            last = t
        elif start is not None and t - last > gap_ok:
            runs.append((start, last))
            start = None
    if start is not None:
        runs.append((start, last))
    found = []
    for a_, b_ in runs:
        dur = (b_ - a_) * dt
        if dur < 3.5 or tonal[a_:b_ + 1].mean() < 0.75:
            continue
        seg = ndimage.median_filter(fr[a_:b_ + 1].copy(), size=5, mode="nearest")
        kind, note = _classify_siren(seg, dt)
        if kind:
            found.append({"start_s": float(times[a_]), "end_s": float(times[b_]), "kind": kind,
                          "note": note, "f_lo": float(seg.min()), "f_hi": float(seg.max())})
    return found


def _classify_siren(seg, dt):
    lo, hi = np.percentile(seg, 10), np.percentile(seg, 90)
    if lo <= 0:
        return None, ""
    # two alternating tones (hi-lo)
    mid = np.sqrt(lo * hi)
    lab = seg > mid
    lab = ndimage.median_filter(lab.astype(int), size=5, mode="nearest").astype(bool)
    change = np.nonzero(np.diff(lab.astype(int)))[0]
    if len(change) >= 4 and 1.12 <= hi / lo <= 1.7:
        lens = np.diff(np.r_[0, change, len(seg)]) * dt
        lens = lens[1:-1] if len(lens) > 2 else lens
        a, b = seg[lab], seg[~lab]
        if len(a) and len(b) and np.std(a) / np.mean(a) < 0.05 and np.std(b) / np.mean(b) < 0.05 \
                and 0.25 <= np.median(lens) <= 2.5:
            return "two-tone", "two alternating tones (hi-lo)"
    # smooth repeating sweep (wail / yelp)
    if seg.max() / max(seg.min(), 1) >= 1.35:
        d = seg - seg.mean()
        ac = np.correlate(d, d, "full")[len(d) - 1:]
        ac = ac / max(ac[0], 1e-9)
        lo_l, hi_l = max(int(0.6 / dt), 2), min(int(10 / dt), len(ac) - 1)
        if hi_l > lo_l + 4:
            pks, _ = signal.find_peaks(ac[lo_l:hi_l], prominence=0.1)
            if len(pks):
                pk = lo_l + int(pks[np.argmax(ac[lo_l:hi_l][pks])])
                cycles = len(seg) / pk
                jumps = np.mean(np.abs(np.diff(seg)) > 100)
                if ac[pk] >= 0.45 and cycles >= 2 and jumps < 0.05:
                    return "sweep", f"rising and falling sweep, one cycle about every {pk * dt:.1f} s (wail/yelp style)"
    return None, ""


# ----------------------------------------------------------------------------
# main entry
# ----------------------------------------------------------------------------

def _analyze_band(x, sr, band, min_dur, dur_total):
    S, times, freqs = compute_spec(x, sr, band["nfft"], band["hop"], band["fmin"], band["fmax"])
    P, P2 = peakiness(S)
    dt = float(times[1] - times[0])
    df = float(freqs[1] - freqs[0])
    peaks = pick_peaks(P2, freqs, thr=band.get("peak_db", PEAK_DB))
    miss = round(band["max_miss_s"] / dt) if "max_miss_s" in band else None
    raw = stitch_tracks(link_tracks(peaks, dt, df, max_miss=miss, gate_rel=band.get("gate_rel", 0.012)), dt, df)
    feats = [track_features(t, dt) for t in raw]
    groups = group_flyovers(feats, P2, freqs, dt, min_dur, band)
    keep, n_weak = finalize_groups(groups, dt, min_dur)
    for g in keep:
        g["ext_t"], g["ext_f1"], g["ext_fit"] = extend_trace(g, dt, dur_total)
    return {"keep": keep, "n_weak": n_weak, "feats": feats, "S": S, "times": times, "freqs": freqs, "dt": dt, "df": df}


def analyze(x: np.ndarray, sr: int = SR, min_dur: float = 5.0, x_hi: np.ndarray | None = None) -> dict:
    """Full audio analysis. x is mono at 8 kHz (low band: propeller tones, hum, sirens, voices, roar);
    x_hi, if given, is the same audio mono at 16 kHz (high band: turbofan fan-tone stacks up to 7.5 kHz).
    Returns a dict with the findings (JSON-friendly except the keys starting with '_', which hold arrays)."""
    dur_total = len(x) / sr
    res = {"duration_s": float(dur_total), "flyovers": [], "hum": None, "sirens": [],
           "roar": [], "voice_zones": [], "n_voice_tracks": 0, "n_weak": 0, "steady_tones": []}
    if len(x) < sr * 3:
        res["too_short"] = True
        return res
    lo = _analyze_band(x, sr, LOW_BAND, min_dur, dur_total)
    keep, n_weak = list(lo["keep"]), lo["n_weak"]
    hi = None
    if x_hi is not None and len(x_hi) >= HIGH_BAND["sr"] * 3:
        hi = _analyze_band(x_hi, HIGH_BAND["sr"], HIGH_BAND, min_dur, dur_total)
        keep += hi["keep"]
        n_weak += hi["n_weak"]
    keep = mark_overlaps(drop_harmonic_duplicates(keep))
    res["n_weak"] = n_weak
    res["flyovers"] = keep
    dt, feats = lo["dt"], lo["feats"]
    # steady tones: long, but they do not slide (fans, hum leftovers, alarms)
    steady = [t for t in feats if t["dur"] >= 8 and t["span_ln"] < 0.02 and t["pmean"] >= 9]
    steady = sorted(steady, key=lambda t: -t["dur"])[:5]
    res["steady_tones"] = [{"freq_hz": t["fmed"], "start_s": t["i0"] * dt, "end_s": t["i1"] * dt} for t in steady]
    zones, nv = find_voice_zones(feats, dt)
    res["voice_zones"], res["n_voice_tracks"] = zones, nv
    res["roar"] = find_roar(lo["S"], lo["freqs"], dt)
    res["hum"] = find_hum(x, sr)
    res["sirens"] = find_sirens(x, keep, sr)
    res["_spec"] = {"S": lo["S"], "times": lo["times"], "freqs": lo["freqs"], "dt": dt, "df": lo["df"]}
    if hi is not None:
        res["_spec_hi"] = {"S": hi["S"], "times": hi["times"], "freqs": hi["freqs"], "dt": hi["dt"], "df": hi["df"]}
    return res
