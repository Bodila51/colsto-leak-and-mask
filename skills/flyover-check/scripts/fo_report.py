"""Plain-words report, JSON and spectrogram picture for flyover-check."""
from __future__ import annotations

import os

import numpy as np

import fo_media
import fo_detect
from fo_detect import mmss


def check_path(path: str, min_dur: float = 5.0) -> dict:
    """Run the whole check on one local media file. Returns dict(meta, media, analysis)."""
    info = fo_media.probe(path)
    media = fo_media.media_summary(info)
    if media["n_audio"] == 0:
        raise RuntimeError("this file has no audio track, so there is nothing to check for plane tones")
    meta = fo_media.metadata_leaks(info)
    x16 = fo_media.decode_mono(path, fo_detect.HIGH_BAND["sr"])
    from scipy import signal as _sig
    x = _sig.resample_poly(x16, 1, 2).astype(np.float32)           # 8 kHz copy for the propeller-range search
    res = fo_detect.analyze(x, fo_detect.SR, min_dur=min_dur, x_hi=x16)
    return {"path": path, "meta": meta, "media": media, "analysis": res}


# ----------------------------------------------------------------------------
# words
# ----------------------------------------------------------------------------

def _norm_time(v: str) -> str:
    try:
        from datetime import datetime, timezone
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return v.strip()


def _plural(n, one, many):
    return one if n == 1 else many


def _times_list(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def grid_region(hz: float) -> str:
    if abs(hz - 50) < 3:
        return ("50 Hz mains: Europe, most of Africa and Asia, Australia, much of South America "
                "(not the USA/Canada)")
    return "60 Hz mains: USA, Canada, Mexico, parts of South America and East Asia (e.g. Korea, Taiwan)"


def summarize(r: dict, name: str | None = None) -> dict:
    """Return dict(headline, leaks: [lines], notes: [lines], limits: [lines], verdict)."""
    res = r["analysis"]
    meta = r["meta"]
    fl = res.get("flyovers", [])
    leaks, notes = [], []
    head = []

    # planes
    if fl:
        strong = [g for g in fl if g["label"] in ("high", "medium")]
        weak = [g for g in fl if g["label"] == "low"]

        def times(gs):
            t = [mmss(g["start_s"]) for g in gs]
            return _times_list(t[:8]) + (f" and {len(t) - 8} more" if len(t) > 8 else "")

        if strong:
            n = len(strong)
            s_ = (f"{n} {_plural(n, 'plane', 'planes')} audible at {times(strong)}. "
                  "Someone with public flight data and the date could use "
                  f"{_plural(n, 'it', 'them')} to find where this was filmed.")
            if weak:
                s_ += f" ({len(weak)} more low-confidence {_plural(len(weak), 'candidate', 'candidates')} at {times(weak)}.)"
        else:
            n = len(weak)
            s_ = (f"No clear plane tone, but {n} low-confidence {_plural(n, 'candidate', 'candidates')} at "
                  f"{times(weak)}. They may be planes or other sounds.")
        head.append(s_)
        for i, g in enumerate(fl, 1):
            line = (f"Plane {i}: {mmss(g['start_s'])}-{mmss(g['end_s'])}, confidence {g['label']} "
                    f"({g['confidence']:.2f}). Tone slides from {g['f_start']:.0f} to {g['f_end']:.0f} Hz "
                    f"with {len(g['harmonics'])} {_plural(len(g['harmonics']), 'harmonic', 'harmonics')}.")
            if g.get("band") == "high":
                line += f" These are high-pitched fan tones (a stack near {g['f_median']:.0f} Hz), typical of a jet."
            if g["kind"] != "plane":
                line += " The pitch does not follow one clean Doppler curve, so this may be a turning plane or not a plane."
            leaks.append(line)
        if any(g["overlaps"] for g in fl):
            notes.append("Some planes overlap in time. Overlapping planes are harder to separate, so their "
                         "time ranges and counts are less certain.")
        if len(strong) == 1:
            notes.append("One plane alone leaves two possible places, one on each side of its path.")
    elif res.get("too_short"):
        head.append("The audio is shorter than 3 seconds: too short to look for plane tones, hum or sirens.")
    else:
        head.append("No plane tones found.")

    # metadata
    if meta["location"] or meta["data_streams"]:
        bits = []
        for it in meta["location"]:
            if "lat" in it:
                bits.append(f"{it['lat']:.5f}, {it['lon']:.5f}")
        if bits:
            head.append("Your file also contains GPS coordinates (" + "; ".join(sorted(set(bits))) + ").")
        else:
            head.append("Your file also contains location tags.")
        for it in meta["location"]:
            leaks.append(f"Location tag: {it['key']} = {it['value']} ({it['where']}).")
        for ds in meta["data_streams"]:
            leaks.append(f"A data track that usually carries GPS/telemetry is inside the file "
                         f"(stream {ds['index']}: {ds['what']}).")
    if meta["times"]:
        uniq = {}
        for t in meta["times"]:
            uniq.setdefault(_norm_time(t["value"]), t["value"])
        tv = "; ".join(list(uniq.values())[:3])[:140]
        head.append(f"It also records when it was made ({tv}).")
        seen = set()
        for t in meta["times"]:
            key = (t["key"].lower(), _norm_time(t["value"]))
            if key in seen:
                continue
            seen.add(key)
            leaks.append(f"Time tag: {t['key']} = {t['value']} ({t['where']}).")
    if meta["device"]:
        dv = "; ".join(sorted({f"{d['key']}={d['value']}" for d in meta["device"]
                               if "encoder" not in d["key"].lower()}))[:160]
        if dv:
            notes.append(f"Device/software tags: {dv}. These name the phone or camera, not the place.")

    # hum
    hum = res.get("hum")
    if hum:
        ks = ", ".join(f"{h['freq_hz']:.0f}" for h in hum["harmonics"][:5])
        s = (f"Mains hum is in the audio (lines near {ks} Hz, up to "
             f"{max(h['above_floor_db'] for h in hum['harmonics']):.0f} dB above the background). "
             f"That is a {hum['grid_hz']:.0f} Hz grid, which narrows the region ({grid_region(hum['grid_hz'])}).")
        head.append(s)
        w = hum.get("wander")
        if w:
            leaks.append(f"Hum frequency wanders between {w['min_hz']:.3f} and {w['max_hz']:.3f} Hz. "
                         "Analysts can compare that wobble with grid frequency logs to estimate date and "
                         "time, but only where such logs exist and only with a reasonably long recording.")
        else:
            leaks.append("Hum is present, but the recording is too short to measure its wobble over time.")

    # sirens
    sirens = res.get("sirens", [])
    if sirens:
        for sr_ in sirens:
            leaks.append(f"Possible emergency siren at {mmss(sr_['start_s'])}-{mmss(sr_['end_s'])}: "
                         f"{sr_['note']} (low confidence). Siren styles differ between countries and cities, "
                         "so this can give at most a rough region, and it can be wrong.")
        head.append("A possible siren is audible; that only gives a rough region.")

    # masking and ignored things
    roar = res.get("roar", [])
    if roar:
        notes.append("Loud, wide-band low-frequency noise (wind, traffic or engines) at "
                     + _times_list(f"{mmss(a)}-{mmss(b)}" for a, b in roar[:4])
                     + ". It can hide plane tones from this detector, so a plane may still be there.")
    if res.get("voice_zones"):
        notes.append("Voice-like sounds around "
                     + _times_list(f"{mmss(a)}-{mmss(b)}" for a, b in res["voice_zones"][:4])
                     + " were ignored: voices change pitch too fast to be a plane.")
    if res.get("steady_tones"):
        st = res["steady_tones"][0]
        notes.append(f"A steady tone near {st['freq_hz']:.0f} Hz ({mmss(st['start_s'])}-{mmss(st['end_s'])}) "
                     "does not slide, so it is not treated as a plane.")
    if res.get("n_weak"):
        notes.append(f"{res['n_weak']} very faint candidate(s) were too weak to report.")

    found = bool(fl or meta["location"] or meta["data_streams"] or hum or sirens or meta["times"])
    if res.get("too_short") and not (fl or meta["location"] or meta["data_streams"] or meta["times"]):
        verdict = "TOO SHORT TO JUDGE"
    elif fl or meta["location"] or meta["data_streams"]:
        verdict = "LEAKS FOUND"
    elif hum or sirens or meta["times"]:
        verdict = "SMALL HINTS FOUND"
    else:
        verdict = "NOTHING FOUND BY THIS CHECK"
    limits = [
        "This check only hears planes that make clean tones: propeller and piston tones (50-420 Hz base tone) and "
        "jet fan tones (a base tone up to about 3 kHz, harmonics up to 7.5 kHz) that show as thin sliding lines. "
        "A jet that is only a wide-band roar, or a plane hidden under wind or traffic, will not be found.",
        "A clean result is not a guarantee: it does not look at the picture, speech, signs or anything "
        "outside the audio and file tags it reads.",
    ]
    if not found and not res.get("too_short"):
        head.append("This check found nothing that points to a place or time.")
    return {"verdict": verdict, "headline": " ".join(head), "headline_lines": head,
            "leaks": leaks, "notes": notes, "limits": limits}


def print_summary(r: dict, s: dict, png: str | None, json_path: str | None, fixes: bool = True) -> None:
    m = r["media"]
    print(f"flyover-check: {os.path.basename(r['path'])}")
    dur = m["duration_s"]
    print(f"  length {mmss(dur) if dur else '?'}, audio {m['audio_codec']} {m['sample_rate']} Hz x{m['channels']}"
          + (f", video {m['video_codec']}" if m["has_video"] else ", no video"))
    print(f"\nRESULT: {s['verdict']}")
    for ln in s["headline_lines"]:
        print("  " + ln)
    if s["leaks"]:
        print("\nDetails")
        for l in s["leaks"]:
            print("  - " + l)
    if s["notes"]:
        print("\nNotes")
        for l in s["notes"]:
            print("  - " + l)
    print("\nLimits")
    for l in s["limits"]:
        print("  - " + l)
    if png:
        print(f"\nSpectrogram with each flyover marked: {png}")
    if json_path:
        print(f"Machine-readable result: {json_path}")
    if fixes and s["verdict"] in ("LEAKS FOUND", "SMALL HINTS FOUND"):
        print("\nTo fix: run safe_copy.py on this file. It removes the plane tones and hum from the sound "
              "and strips the location/time tags, then checks its own work.")


def to_json(r: dict, s: dict) -> dict:
    res = r["analysis"]
    fo = []
    for g in res.get("flyovers", []):
        fo.append({
            "start_s": round(g["start_s"], 2), "end_s": round(g["end_s"], 2),
            "start": mmss(g["start_s"]), "end": mmss(g["end_s"]),
            "confidence": round(g["confidence"], 3), "label": g["label"], "kind": g["kind"], "band": g.get("band", "low"),
            "f_start_hz": round(g["f_start"], 1), "f_end_hz": round(g["f_end"], 1),
            "f_median_hz": round(g["f_median"], 1),
            "harmonics": [h[0] for h in g["harmonics"]], "reasons": g["reasons"],
            "overlaps_with": [j + 1 for j in g["overlaps"]],
            "trace": {"t_s": [round(float(v), 3) for v in (g["idx"] * res["_spec"]["dt"])[::8]],
                      "f1_hz": [round(float(v), 2) for v in g["f1"][::8]]},
        })
    hum = res.get("hum")
    return {
        "file": r["path"], "verdict": s["verdict"], "summary": s["headline"],
        "media": r["media"], "flyovers": fo,
        "metadata": r["meta"],
        "hum": ({k: v for k, v in hum.items()} if hum else None),
        "sirens": res.get("sirens", []), "wind_or_traffic": res.get("roar", []),
        "voice_like": res.get("voice_zones", []), "notes": s["notes"], "limits": s["limits"],
    }


# ----------------------------------------------------------------------------
# picture
# ----------------------------------------------------------------------------

def _panel(ax, spec, groups, res, title_extra, mmss_fmt, fig, colors, first_index):
    S, times, freqs, dt = spec["S"], spec["times"], spec["freqs"], spec["dt"]
    fmax = 1000.0
    if groups:
        fmax = max(g["f1"].max() * max(h[0] for h in g["harmonics"]) for g in groups) * 1.12
        fmax = float(np.clip(fmax, 600, 7500))
    sel = freqs <= fmax
    block = S[:, sel]
    vmin, vmax = np.percentile(block, 8), np.percentile(block, 99.7)
    im = ax.imshow(block.T, origin="lower", aspect="auto", cmap="magma", vmin=vmin, vmax=vmax,
                   extent=[times[0], times[-1] + dt, freqs[0], freqs[sel][-1]], interpolation="nearest")
    for n_, g in enumerate(groups):
        i = first_index[id(g)]
        c = colors[i % len(colors)]
        ax.axvspan(g["start_s"], g["end_s"], color=c, alpha=0.10, lw=0)
        ax.axvline(g["start_s"], color=c, lw=0.8, alpha=0.8)
        ax.axvline(g["end_s"], color=c, lw=0.8, alpha=0.8)
        tt = g["idx"] * g["dt"]
        for (k, _, _) in g["harmonics"]:
            if (g["f1"] * k).max() < fmax:
                ax.plot(tt, g["f1"] * k, color=c, lw=1.6 if k == 1 else 1.0, ls="--" if k == 1 else "-",
                        alpha=1.0 if k == 1 else 0.55)
        y = fmax * (0.95 - 0.07 * (n_ % 3))
        ax.text(g["start_s"] + 0.01 * (times[-1]), y,
                f"Plane {i + 1}  {mmss(g['start_s'])}-{mmss(g['end_s'])}  {g['label']}",
                color="black", fontsize=9, va="top", ha="left", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25", fc=c, ec="none", alpha=0.9))
    if title_extra == "low":
        hum = res.get("hum")
        if hum:
            for h in hum["notch"][:8]:
                if h["freq_hz"] < fmax:
                    ax.axhline(h["freq_hz"], color="white", lw=0.6, ls="-", alpha=0.28)
            ax.text(times[-1] * 0.995, hum["notch"][0]["freq_hz"] if hum["notch"] else hum["grid_hz"],
                    f" {hum['grid_hz']:.0f} Hz hum", color="white", fontsize=8, ha="right", va="bottom", alpha=0.9)
        for s_ in res.get("sirens", []):
            ax.axvspan(s_["start_s"], s_["end_s"], ymax=0.12, color="#b0bec5", alpha=0.55, lw=0)
            ax.text(s_["start_s"], fmax * 0.02, "siren?", color="white", fontsize=8, va="bottom")
        for (a_, b_) in res.get("roar", []):
            ax.axvspan(a_, b_, ymin=0.0, ymax=0.04, color="#90caf9", alpha=0.8, lw=0)
    ax.set_xlim(times[0], times[-1] + dt)
    ax.set_ylim(freqs[0], freqs[sel][-1])
    ax.xaxis.set_major_formatter(mmss_fmt)
    ax.set_ylabel("frequency (Hz)")
    cb = fig.colorbar(im, ax=ax, pad=0.01, fraction=0.025)
    cb.set_label("level (dB)")


def render_png(r: dict, path: str, title: str = "") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    res = r["analysis"]
    if "_spec" not in res:      # too short to analyse: nothing to draw
        return
    fl = res["flyovers"]
    index = {id(g): i for i, g in enumerate(fl)}
    low = [g for g in fl if g.get("band") != "high"]
    high = [g for g in fl if g.get("band") == "high"]
    panels = []
    if low or not high:
        panels.append((res["_spec"], low, "low"))
    if high and "_spec_hi" in res:
        panels.append((res["_spec_hi"], high, "high"))
    fmt = FuncFormatter(lambda v, _: mmss(v))
    colors = ["#00e5ff", "#7CFC00", "#ffffff", "#ff80ab", "#ffd54f"]
    fig, axes = plt.subplots(len(panels), 1, figsize=(13, 5.6 * len(panels) / (1 if len(panels) == 1 else 1.4)),
                             dpi=110, squeeze=False)
    for ax, (spec, groups, name) in zip(axes[:, 0], panels):
        _panel(ax, spec, groups, res, name, fmt, fig, colors, index)
    axes[-1, 0].set_xlabel("time (min:sec)")
    n = len(fl)
    axes[0, 0].set_title((title + "  -  " if title else "") +
                         (f"{n} plane{'s' if n != 1 else ''} marked" if n else "no plane tones found"),
                         fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
