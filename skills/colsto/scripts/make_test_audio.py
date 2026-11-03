# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Make synthetic test recordings for flyover-check.

Every file is built from known parts, so a test can say exactly what was in it:
  * a plane: a Doppler-sliding tone with harmonics (rest pitch, speed and distance are chosen)
  * speech-like sound: voiced syllables whose pitch glides fast, plus hissy consonants
  * 50 Hz mains hum with harmonics, with a slow wander like a real grid
  * street-like background noise, optional wind roar, optional siren

Usage:  uv run make_test_audio.py --scenario main --out data/skilltest
Scenarios: main, no_plane, roar, two_planes, turning, siren, long, jet
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess

import numpy as np
import soundfile as sf
from scipy import signal

C_SOUND = 343.0


# ---------------------------------------------------------------- parts

def doppler_f(t, f_rest, v, d, tc):
    r = np.sqrt(d * d + (v * (t - tc)) ** 2)
    v_r = v * v * (tc - t) / r
    return f_rest * C_SOUND / (C_SOUND - v_r), d / r


def plane(sr, dur, f_rest=140.0, v=75.0, d=300.0, tc=45.0, nharm=8, level=0.015, rng=None, turn=0.0):
    rng = rng or np.random.default_rng(1)
    t = np.arange(int(dur * sr)) / sr
    f, amp = doppler_f(t, f_rest, v, d, tc)
    if turn:
        # a turning plane: its pitch also wobbles up and down as it banks and changes power
        f = f * (1 + turn * np.sin(2 * np.pi * 0.045 * t + 0.7) + 0.5 * turn * np.sin(2 * np.pi * 0.11 * t))
    ph = 2 * np.pi * np.cumsum(f) / sr
    out = np.zeros_like(t)
    for k in range(1, nharm + 1):
        out += (level / k ** 0.9) * np.sin(k * ph + rng.uniform(0, 2 * np.pi))
    return (out * amp).astype(np.float64), f


def pink(sr, n, rng, slope=1.0):
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / sr)
    f[0] = f[1]
    X = X / f ** (slope / 2)
    y = np.fft.irfft(X, n)
    return y / y.std()


def hum(sr, dur, f0=50.0, rng=None, level=0.003, wander=0.04):
    rng = rng or np.random.default_rng(2)
    n = int(dur * sr)
    walk = np.cumsum(rng.standard_normal(n // 1000 + 2)) * 0.01
    walk = np.clip(walk - walk.mean(), -3, 3) / 3 * wander
    fw = f0 + np.interp(np.arange(n), np.arange(len(walk)) * 1000, walk)
    ph = 2 * np.pi * np.cumsum(fw) / sr
    amps = [1.0, 1.6, 1.0, 0.5, 0.3, 0.2]
    return sum(level * a * np.sin((k + 1) * ph + rng.uniform(0, 6.28)) for k, a in enumerate(amps))


_VOWELS = [(730, 1090, 2440), (530, 1840, 2480), (270, 2290, 3010), (570, 840, 2410), (300, 870, 2240)]


def _resonator(f, bw, sr):
    r = np.exp(-np.pi * bw / sr)
    th = 2 * np.pi * f / sr
    return [1 - r], [1, -2 * r * np.cos(th), r * r]


def speech(sr, dur, spans, rng, level=0.05, f0_base=(110, 190)):
    out = np.zeros(int(dur * sr))
    for (a, b) in spans:
        t = a
        f0b = rng.choice(f0_base) * rng.uniform(0.9, 1.1)
        n_in = 0
        while t < b - 0.4:
            vd = rng.uniform(0.12, 0.35)
            n = int(vd * sr)
            tt = np.arange(n) / sr
            glide = rng.uniform(-0.6, 0.6)                     # up to ~ +-30 % per syllable
            f0 = f0b * np.exp(glide * (tt / vd - 0.5)) * (1 + 0.015 * np.sin(2 * np.pi * 5.5 * tt))
            ph = 2 * np.pi * np.cumsum(f0) / sr
            src = np.zeros(n)
            for k in range(1, int(4000 / f0b)):
                src += np.sin(k * ph) / k
            seg = src
            for fm in _VOWELS[rng.integers(len(_VOWELS))]:
                bb, aa = _resonator(fm * rng.uniform(0.95, 1.05), 90, sr)
                seg = signal.lfilter(bb, aa, seg)
            seg = seg / (np.sqrt(np.mean(seg ** 2)) + 1e-9)
            seg *= np.hanning(n) ** 0.5
            i0 = int(t * sr)
            out[i0:i0 + n] += seg[:max(0, min(n, len(out) - i0))]
            t += vd
            if rng.random() < 0.6:                              # consonant hiss
                cd = rng.uniform(0.03, 0.12)
                m = int(cd * sr)
                bpb, bpa = signal.butter(2, [2500, 3800], "bandpass", fs=sr)
                hs = signal.lfilter(bpb, bpa, rng.standard_normal(m)) * np.hanning(m)
                i1 = int(t * sr)
                out[i1:i1 + m] += 0.5 * hs[:max(0, min(m, len(out) - i1))] / (hs.std() + 1e-9)
                t += cd
            t += rng.uniform(0.02, 0.1)
            n_in += 1
            if n_in % rng.integers(4, 8) == 0:
                t += rng.uniform(0.3, 0.8)
    out *= level / (np.sqrt(np.mean(out[np.abs(out) > 0] ** 2)) + 1e-9) * 0.6
    return out


def roar(sr, dur, rng, level=0.05, spans=None):
    n = int(dur * sr)
    y = signal.lfilter(*signal.butter(2, 300, "low", fs=sr), rng.standard_normal(n))
    y = y / y.std()
    env = np.zeros(n)
    for a, b in (spans or [(0, dur)]):
        env[int(a * sr):int(b * sr)] = 1
    env = signal.lfilter(*signal.butter(1, 0.5, "low", fs=sr), env)
    return y * env * level


def siren(sr, dur, t0, t1, kind="hilo", level=0.05):
    t = np.arange(int(dur * sr)) / sr
    m = (t >= t0) & (t < t1)
    if kind == "hilo":
        f = np.where(((t - t0) // 0.6).astype(int) % 2 == 0, 440.0, 554.0)
    else:  # wail
        f = 600 + 325 * (1 - np.cos(2 * np.pi * (t - t0) / 6.0))
    ph = 2 * np.pi * np.cumsum(f) / sr
    y = sum((level / k) * np.sin(k * ph) for k in (1, 2, 3))
    return y * m


# ---------------------------------------------------------------- scenarios

def compose(scenario="main", sr=48000, seed=7, dur=None):
    rng = np.random.default_rng(seed)
    truth = {"scenario": scenario, "sr": sr, "planes": [], "hum_hz": 50.0}
    D = dur or {"main": 90.0, "no_plane": 60.0, "roar": 60.0, "two_planes": 100.0,
                "turning": 90.0, "siren": 40.0, "long": 600.0, "jet": 90.0}[scenario]
    truth["duration_s"] = D
    planes = []
    spans = [(4, 30), (50, 82)] if D >= 85 else [(3, 25), (35, D - 5)]
    if scenario == "main":
        planes = [dict(f_rest=140, v=75, d=300, tc=45)]
    elif scenario == "two_planes":
        planes = [dict(f_rest=120, v=70, d=350, tc=35), dict(f_rest=215, v=95, d=400, tc=62)]
    elif scenario == "jet":
        # a turbofan: a stack of fan tones (1.3 / 2.6 / 3.9 / 5.2 kHz at rest) sliding down as it passes
        planes = [dict(f_rest=1300, v=70, d=220, tc=45, nharm=4, level=0.02)]
    elif scenario == "turning":
        planes = [dict(f_rest=150, v=70, d=320, tc=45, turn=0.05)]
    elif scenario == "long":
        planes = [dict(f_rest=135, v=75, d=300, tc=120), dict(f_rest=185, v=85, d=380, tc=300),
                  dict(f_rest=110, v=65, d=280, tc=480)]
        spans = [(10 + 60 * i, 50 + 60 * i) for i in range(0, 9, 2)]
    stems = {}
    stems["noise"] = np.stack([pink(sr, int(D * sr), rng) * 0.008 for _ in range(2)], 1)
    sp = speech(sr, D, spans, rng) if scenario != "siren" else np.zeros(int(D * sr))
    stems["speech"] = np.stack([sp, sp], 1)
    h = hum(sr, D, 50.0, rng)
    stems["hum"] = np.stack([h, h], 1)
    pl = np.zeros((int(D * sr), 2))
    for p in planes:
        sig, f = plane(sr, D, rng=rng, **p)
        pan = rng.uniform(0.6, 1.0)
        pl += np.stack([sig, sig * pan], 1)
        t_ = np.arange(0, D, 0.5)
        truth["planes"].append({**p, "f_trace_t": t_.tolist(),
                                "f_trace_hz": np.interp(t_, np.arange(len(f)) / sr, f).tolist()})
    stems["plane"] = pl
    extra = np.zeros((int(D * sr), 2))
    if scenario == "roar":
        r = roar(sr, D, rng, spans=[(10, 40)])
        extra += np.stack([r, r * 0.8], 1)
        truth["roar"] = [10, 40]
    if scenario == "siren":
        s = siren(sr, D, 8, 18, "hilo") + siren(sr, D, 24, 36, "wail")
        extra += np.stack([s, s], 1)
        truth["sirens"] = [{"t": [8, 18], "kind": "two-tone"}, {"t": [24, 36], "kind": "sweep"}]
    stems["extra"] = extra
    mix = sum(stems.values())
    peak = np.abs(mix).max()
    if peak > 0.95:
        g = 0.95 / peak
        mix *= g
        stems = {k: v * g for k, v in stems.items()}
    truth["speech_spans"] = spans
    return mix.astype(np.float32), {k: v.astype(np.float32) for k, v in stems.items()}, truth


def mux_mp4(wav, mp4, dur, with_meta=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=5",
           "-i", wav, "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    if with_meta:
        cmd += ["-movflags", "use_metadata_tags+faststart",
                "-metadata", "location=+42.6977+023.3219/",
                "-metadata", "com.apple.quicktime.location.ISO6709=+42.6977+023.3219+560.000/",
                "-metadata", "creation_time=2026-11-02T14:03:10Z",
                "-metadata", "com.apple.quicktime.make=Apple",
                "-metadata", "com.apple.quicktime.model=iPhone 15"]
    cmd += ["-shortest", mp4]
    subprocess.run(cmd, check=True, capture_output=True)


def build(scenario, outdir, seed=7, with_meta=True, mp4=True):
    os.makedirs(outdir, exist_ok=True)
    mix, stems, truth = compose(scenario, seed=seed)
    base = os.path.join(outdir, f"{scenario}")
    sf.write(base + ".wav", mix, truth["sr"], subtype="FLOAT")
    np.savez_compressed(base + ".stems.npz", **stems)
    with open(base + ".truth.json", "w") as fh:
        json.dump(truth, fh)
    if mp4:
        mux_mp4(base + ".wav", base + ".mp4", truth["duration_s"], with_meta)
    return base, truth


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="main")
    ap.add_argument("--out", default="data/skilltest")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    b, tr = build(a.scenario, a.out, a.seed)
    print("wrote", b + ".mp4", "and", b + ".wav", "|", len(tr["planes"]), "plane(s)")
