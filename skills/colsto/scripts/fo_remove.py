"""Removal for flyover-check: narrow notches that follow a plane tone, hum notches, decoys,
and measurements of how much the audio changed.

How the plane notch works. The tone's pitch is known over time from the spectrogram trace.
We multiply the audio by exp(-j * phase) where phase follows that pitch. The tone then sits
at 0 Hz, so a very narrow low-pass (a few Hz) isolates exactly that tone, however it slides.
We shift it back and subtract it. Anything that is not within a few Hz of the tone's own
pitch at that moment is untouched, so a voice, whose pitch moves fast, passes through.
The same thing is done for each harmonic.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage, signal

SR8 = 8000
RATE_OUT = 200.0       # sample rate of the slow envelope (Hz)


def to_int(x: np.ndarray, sr: int, sri: int = SR8) -> np.ndarray:
    """Resample to the internal analysis rate sri (8 kHz for propeller-range tones, 16 kHz for fan tones)."""
    if sr == sri:
        return x.astype(np.float64)
    g = int(np.gcd(sr, sri))
    return signal.resample_poly(x.astype(np.float64), sri // g, sr // g)


def to_8k(x: np.ndarray, sr: int) -> np.ndarray:
    return to_int(x, sr, SR8)


def internal_rate(g: dict) -> int:
    return 16000 if g.get("band") == "high" else SR8


def _movavg(v: np.ndarray, k: int) -> np.ndarray:
    """Centered moving average of odd length k (edges repeat the end values)."""
    h = k // 2
    c = np.concatenate([[0.0], np.cumsum(np.concatenate([np.full(h, v[0]), v, np.full(h, v[-1])]))])
    return (c[k:] - c[:-k]) / k


def _boxcar3(v: np.ndarray, D: int) -> np.ndarray:
    """Three cascaded moving averages, then keep every D-th sample: a cheap decimator whose zeros sit at the
    new sample rate (and its multiples), so sound 200 Hz away folds in at about -70 dB."""
    for _ in range(3):
        v = _movavg(v, D + 1)
    return v[::D]


def _demod_lp(x8: np.ndarray, phi: np.ndarray, fc: float, sri: int = SR8):
    """x8 * exp(-j phi), low-passed to +-fc Hz (zero phase) and decimated to 200 Hz. Returns (z, D, rate)."""
    D = int(round(sri / RATE_OUT))
    zr = _boxcar3(x8 * np.cos(phi), D)
    zi = _boxcar3(-x8 * np.sin(phi), D)
    rate = sri / D
    sos = signal.butter(4, fc / (rate / 2), output="sos")
    return signal.sosfiltfilt(sos, zr) + 1j * signal.sosfiltfilt(sos, zi), D, rate


def _offset(x8: np.ndarray, f: np.ndarray, fcut: float, sri: int = SR8):
    """Demodulate x8 along pitch f and read how far the real tone is from it.
    Returns (g, w): offset in Hz per 8 kHz sample, and a 0..1 weight (0 where no tone stands out from
    the sound just beside it)."""
    n = len(x8)
    t = np.arange(n) / sri
    phi = 2 * np.pi * np.cumsum(f) / sri
    z, D, rate = _demod_lp(x8, phi, fcut, sri)
    if len(z) <= 40:
        return np.zeros(n), np.zeros(n)
    # the same narrow band, moved aside by a few bandwidths: what "no tone" looks like right here
    shift = 4.0 * fcut + 4.0
    zn, _, _ = _demod_lp(x8, phi + 2 * np.pi * shift * t, fcut, sri)
    snr = (np.abs(z) ** 2 + 1e-18) / (np.abs(zn) ** 2 + 1e-18)
    snr = ndimage.uniform_filter1d(snr, size=max(int(rate * 0.5), 1))
    w = np.clip((snr - 3.0) / 6.0, 0.0, 1.0)
    psi = np.unwrap(np.angle(z))
    win = int(rate * 1.0) | 1
    if len(psi) > win:
        g = signal.savgol_filter(psi, win, 2, deriv=1, delta=1 / rate) / (2 * np.pi)
    else:
        g = np.gradient(psi, 1 / rate) / (2 * np.pi)
    tz = np.arange(len(z)) * D / sri
    return np.interp(t, tz, np.clip(g, -fcut, fcut)), np.interp(t, tz, w)


def tone_follow(x8: np.ndarray, f8: np.ndarray, fc: float = 3.0, refine: bool = True, sri: int = SR8):
    """Estimate one sliding tone in x8 (8 kHz, mono) whose pitch is roughly f8 (Hz per sample).
    Returns dict with refined pitch f8, phase phi8, complex envelope z (200 Hz grid) and D."""
    f = f8.astype(np.float64).copy()
    if refine:
        g, w = _offset(x8, f, max(fc, 4.0), sri)
        f = f + g * w
    phi = 2 * np.pi * np.cumsum(f) / sri
    z, D, rate = _demod_lp(x8, phi, fc, sri)
    return {"f8": f, "phi8": phi, "z": z, "D": D, "rate": rate, "sri": sri}


def refine_family(x8: np.ndarray, f1: np.ndarray, ks, fc_base: float, sri: int = SR8, fc_fn=None):
    """All harmonics share one base pitch. Walk up the harmonics, each time nudging the shared pitch toward
    what that harmonic shows. Lower harmonics (small errors) pull the pitch close enough that the higher ones,
    whose errors are multiplied by k, still land inside the narrow notch."""
    f0 = f1.astype(np.float64).copy()
    f1 = f0.copy()
    for k in sorted(ks):
        fcut = max((fc_fn or (lambda kk: fc_for(kk, fc_base)))(k), min(3.0 + 0.5 * k, 9.0))
        g, w = _offset(x8, k * f1, fcut, sri)
        f1 = f1 + np.clip(g / k, -fcut / k, fcut / k) * w
    # never let a voice that happens to cross the notch drag the pitch far off the traced one
    lim = np.maximum(1.5, 0.03 * f0)
    return f0 + np.clip(f1 - f0, -lim, lim)


def synth_tone8(res: dict, fade_s: float = 0.4, fade_start: bool = True, fade_end: bool = True) -> np.ndarray:
    """The estimated tone on the 8 kHz grid (same phase function it was measured with, so it cancels exactly)."""
    sri = res.get("sri", SR8)
    n = len(res["phi8"])
    t8 = np.arange(n) / sri
    tz = np.arange(len(res["z"])) * res["D"] / sri
    zr = np.interp(t8, tz, res["z"].real)
    zi = np.interp(t8, tz, res["z"].imag)
    tone = 2.0 * (zr * np.cos(res["phi8"]) - zi * np.sin(res["phi8"]))
    nf = int(fade_s * sri)
    if 0 < nf < n // 2:
        ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
        if fade_start:
            tone[:nf] *= ramp
        if fade_end:
            tone[-nf:] *= ramp[::-1]
    return tone


def from_8k(x8: np.ndarray, sr: int, n_out: int, sri: int = SR8) -> np.ndarray:
    if sr == sri:
        y = x8
    else:
        g = int(np.gcd(sr, sri))
        y = signal.resample_poly(x8, sr // g, sri // g)
    if len(y) >= n_out:
        return y[:n_out]
    return np.pad(y, (0, n_out - len(y)))


def synth_tone(res: dict, sr: int, n_full: int, fade_s: float = 0.4) -> np.ndarray:
    return from_8k(synth_tone8(res, fade_s), sr, n_full, res.get("sri", SR8))


def fc_for(k: int, fc_base: float) -> float:
    """Half-width (Hz) of the notch for harmonic k: a few Hz in total, a little wider for higher harmonics."""
    return float(min(fc_base + 0.15 * k, 5.0))


def group_fc(g: dict, k: int, fc_base: float) -> float:
    """Half-width of the notch for harmonic k of group g. Propeller-range tones: a few Hz in total. Fan tones are
    higher and wander by a fraction of a percent of their pitch (fan speed drifts), so their notch is 0.5 % of the
    harmonic's frequency (3 to 25 Hz each side) - still narrow against the 1-7 kHz range it sits in."""
    if g.get("band") == "high":
        return float(np.clip(0.012 * k * float(np.median(g["f1"])), 4.0, 40.0))
    return fc_for(k, fc_base)


def notch_specs(flyovers: list[dict], fc_base: float = 2.0):
    return [{"group": gi, "k": int(k), "fc": fc_for(k, fc_base)}
            for gi, g in enumerate(flyovers) for (k, _, _) in g["harmonics"]]


def trace_of(g: dict, dt: float):
    """(times, base pitch) to notch along: the traced part plus the Doppler extension on both sides."""
    if "ext_t" in g:
        return g["ext_t"], g["ext_f1"]
    return g["idx"] * g.get("dt", dt), g["f1"]


def remove_flyovers(x: np.ndarray, sr: int, flyovers: list[dict], dt: float, fc_base: float = 2.0,
                    keep_tones: bool = False):
    """x: (n, ch) float array at native rate. Subtracts, channel by channel, every supported harmonic of every
    flyover. Returns (y, kept, info); kept[(group, k)] = per-channel tone estimates (for decoys).
    Propeller-range groups are tracked at 8 kHz, fan-tone groups at 16 kHz (their harmonics reach 7 kHz)."""
    y = x.astype(np.float64).copy()
    n, ch = y.shape
    info, kept = [], {}
    for gi, g in enumerate(flyovers):
        tg_, f1_ = trace_of(g, dt)
        sri = internal_rate(g)
        t0 = max(0.0, float(tg_[0]) - 0.5)
        t1 = min(n / sr, float(tg_[-1]) + 0.5)
        i0, i1 = int(t0 * sr), int(t1 * sr)
        n_seg = i1 - i0
        n8 = int(round((t1 - t0) * sri))
        ts8 = t0 + np.arange(n8) / sri
        top = min(sr / 2 - 200, 0.47 * sri)
        ks = [k for (k, _, _) in g["harmonics"] if k * float(np.max(g["f1"])) * 1.05 < top]
        x8s = []
        for c in range(ch):
            x8 = to_int(y[i0:i1, c], sr, sri)
            x8s.append(x8[:n8] if len(x8) >= n8 else np.pad(x8, (0, n8 - len(x8))))
        # one shared pitch estimate for all channels (they hear the same plane), from their average
        f1_8 = refine_family(np.mean(x8s, axis=0), np.interp(ts8, tg_, f1_), ks, fc_base, sri, lambda kk: group_fc(g, kk, fc_base))
        for c in range(ch):
            total8 = np.zeros(n8)
            for k in ks:
                res = tone_follow(x8s[c], k * f1_8, fc=group_fc(g, k, fc_base), refine=False, sri=sri)
                total8 += synth_tone8(res, fade_start=i0 > 0, fade_end=i1 < n)
                if keep_tones:
                    kept.setdefault((gi, k), []).append({"res": res, "i0": i0, "n": n_seg})
            y[i0:i1, c] -= from_8k(total8, sr, n_seg, sri)
        info += [{"group": gi, "band": g.get("band", "low"), "k": k, "fc_hz": group_fc(g, k, fc_base)} for k in ks]
    return y, kept, info


def mains_notch(x: np.ndarray, sr: int, hum: dict):
    """Zero-phase notches at the mains frequency and its multiples (narrow, 1 Hz wide)."""
    y = x.astype(np.float64).copy()
    mains = hum["mains_hz_median"]
    spread = hum.get("spread_hz", 0.02)
    sos_all = []
    used = []
    for h in hum.get("notch", []):
        f0 = h["k"] * mains
        if f0 >= sr / 2 - 5:
            continue
        bw = float(min(1.0 + 8.0 * spread * h["k"], 4.0))
        b, a = signal.iirnotch(f0, f0 / bw, fs=sr)
        sos_all.append(signal.tf2sos(b, a))
        used.append({"freq_hz": float(f0), "bandwidth_hz": bw})
    if sos_all:
        sos = np.vstack(sos_all)
        for c in range(y.shape[1]):
            y[:, c] = signal.sosfiltfilt(sos, y[:, c])
    return y, used


# ----------------------------------------------------------------------------
# decoy
# ----------------------------------------------------------------------------

def fake_doppler(t: np.ndarray, f_target_med: float, t_loud: float, dur: float, rng):
    """A different but plausible plane pass: other speed, distance and closest-approach time."""
    v = rng.uniform(60, 220)
    d = rng.uniform(150, 900)
    shift = rng.choice([-1, 1]) * rng.uniform(0.12, 0.28) * dur
    tc = t_loud + shift
    r = np.sqrt(d * d + (v * (t - tc)) ** 2)
    v_r = v * v * (tc - t) / r
    f = 343.0 / (343.0 - v_r)
    f = f * (f_target_med / np.median(f))
    return f, {"speed_ms": float(v), "closest_m": float(d), "closest_time_shift_s": float(shift)}


def add_decoys(y: np.ndarray, sr: int, kept: dict, flyovers: list[dict], dt: float, rng):
    """Put a resynthesised tone in place of each removed one, following a fake Doppler curve.
    The loudness profile of the original tone is kept so it sounds the same."""
    out = y.copy()
    n = y.shape[0]
    fakes = {}
    for (gi, k), per_ch in kept.items():
        g = flyovers[gi]
        if gi not in fakes:
            amp_all = sum(np.abs(r["res"]["z"]).mean() for r in per_ch)
            first = kept[(gi, min(kk for (gg, kk) in kept if gg == gi))][0]
            z1 = np.abs(first["res"]["z"])
            tz = (first["i0"] / sr) + np.arange(len(z1)) * first["res"]["D"] / first["res"].get("sri", SR8)
            z1s = ndimage.uniform_filter1d(z1, size=int(first["res"]["rate"] * 3))
            t_loud = float(tz[int(np.argmax(z1s))])
            tg, f1_ = trace_of(g, dt)
            f_fake, info = fake_doppler(tg, float(np.median(f1_)), t_loud, g["dur_s"], rng)
            fakes[gi] = (tg, f_fake, info)
        tg, f_fake, info = fakes[gi]
        for c, r in enumerate(per_ch):
            res = r["res"]
            i0, nseg = r["i0"], r["n"]
            t = i0 / sr + np.arange(nseg) / sr
            f_full = np.interp(t, tg, f_fake * k)
            phase = 2 * np.pi * np.cumsum(f_full) / sr + rng.uniform(0, 2 * np.pi)
            tz = np.arange(len(res["z"])) * res["D"] / res.get("sri", SR8)
            amp = 2.0 * np.abs(res["z"])
            floor = 2.0 * np.percentile(np.abs(res["z"]), 20)
            amp = np.sqrt(np.maximum(amp ** 2 - floor ** 2, 0.0))
            a_full = np.interp(np.arange(nseg) / sr, tz, amp)
            tone = a_full * np.sin(phase)
            nf = int(0.4 * sr)
            if 0 < nf < nseg // 2:
                ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(nf) / nf)
                tone[:nf] *= ramp
                tone[-nf:] *= ramp[::-1]
            out[i0:i0 + nseg, c] += tone
    return out, {gi: v[2] for gi, v in fakes.items()}


# ----------------------------------------------------------------------------
# how much did the audio change?
# ----------------------------------------------------------------------------

def change_report(x_in: np.ndarray, x_out: np.ndarray, sr: int, flyovers: list[dict], dt: float,
                  hum: dict | None, fc_base: float = 2.0) -> dict:
    """Numbers that say how much the audio changed, and whether the change stayed inside the notches."""
    xi = x_in.astype(np.float64)
    xo = x_out.astype(np.float64)
    e_in = float(np.sum(xi ** 2)) + 1e-30
    e_diff = float(np.sum((xi - xo) ** 2)) + 1e-30
    rep = {
        "removed_energy_db": 10 * np.log10(e_diff / e_in),
        "removed_energy_percent": 100.0 * e_diff / e_in,
        "level_change_db": 10 * np.log10((np.sum(xo ** 2) + 1e-30) / e_in),
    }
    mi, mo = xi.mean(axis=1), xo.mean(axis=1)
    nfft, hop = 4096, 4096
    nfr = (len(mi) - nfft) // hop + 1
    if nfr < 3:
        return rep
    win = signal.windows.hann(nfft, sym=False)
    bins = np.fft.rfftfreq(nfft, 1 / sr)
    binw = sr / nfft
    mask = np.zeros((nfr, len(bins)), bool)
    tfr = (np.arange(nfr) * hop + nfft / 2) / sr
    for g in flyovers:
        tg, f1_ = trace_of(g, dt)
        inside = (tfr >= tg[0] - 1.0) & (tfr <= tg[-1] + 1.0)
        if not inside.any():
            continue
        for (k, _, _) in g["harmonics"]:
            fc = group_fc(g, k, fc_base)
            half = fc + 1.5 * binw + 0.05 * k
            cen = np.interp(tfr[inside], tg, f1_ * k)
            rows = np.nonzero(inside)[0]
            for off in np.arange(-int(half / binw) - 1, int(half / binw) + 2):
                cb = np.round(cen / binw).astype(int) + off
                ok = (cb >= 0) & (cb < len(bins)) & (np.abs(bins[np.clip(cb, 0, len(bins) - 1)] - cen) <= half)
                mask[rows[ok], cb[ok]] = True
    if hum:
        for h in hum.get("notch", []):
            f0 = h["k"] * hum["mains_hz_median"]
            bw = min(1.0 + 8.0 * hum.get("spread_hz", 0.02) * h["k"], 4.0)
            mask[:, np.abs(bins - f0) <= bw + 1.5 * binw] = True
    P_in = np.empty((nfr, len(bins)), np.float32)
    P_out = np.empty_like(P_in)
    for a in range(0, nfr, 512):
        sl = slice(a, min(a + 512, nfr))
        idx = (np.arange(sl.start, sl.stop) * hop)[:, None] + np.arange(nfft)[None, :]
        P_in[sl] = np.abs(np.fft.rfft(mi[idx] * win, axis=1)) ** 2
        P_out[sl] = np.abs(np.fft.rfft(mo[idx] * win, axis=1)) ** 2
    # only cells that carry sound (above the quietest quarter) count for the dB comparison
    live = P_in > np.percentile(P_in, 25)
    outside = ~mask
    speech = (bins >= 300) & (bins <= 3400)
    sel = live & outside
    d = 10 * np.log10((P_out[sel] + 1e-20) / (P_in[sel] + 1e-20))
    rep["outside_mean_abs_db"] = float(np.mean(np.abs(d)))
    rep["outside_rms_db"] = float(np.sqrt(np.mean(d ** 2)))
    rep["outside_energy_change_db"] = float(10 * np.log10((P_out[outside].sum() + 1e-20) / (P_in[outside].sum() + 1e-20)))
    ssel = live & outside & speech[None, :]
    ds = 10 * np.log10((P_out[ssel] + 1e-20) / (P_in[ssel] + 1e-20))
    rep["speech_band_mean_abs_db"] = float(np.mean(np.abs(ds)))
    rep["speech_band_energy_change_db"] = float(10 * np.log10(
        (P_out[outside & speech[None, :]].sum() + 1e-20) / (P_in[outside & speech[None, :]].sum() + 1e-20)))
    # typical drop inside the notched cells (median, so loud speech that happens to sit there does not dominate)
    dm = 10 * np.log10((P_in[mask] + 1e-20) / (P_out[mask] + 1e-20))
    rep["inside_notch_median_drop_db"] = float(np.median(dm)) if dm.size else 0.0
    rep["inside_notch_drop_db"] = rep["inside_notch_median_drop_db"]
    rep["notched_share_of_spectrum_percent"] = float(100.0 * mask.mean())
    return rep
