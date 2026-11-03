# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "scipy", "soundfile", "matplotlib"]
# ///
"""Self-test for flyover-check on synthetic recordings with known contents.

  uv run selftest.py [--dir data/skilltest] [--quick] [--regen]

It builds recordings from known parts (a Doppler-sliding plane tone with harmonics, speech-like
sound with fast pitch glides, 50 Hz hum, street noise, wind roar, sirens), then checks:
  * check finds the tone, at the right time and pitch, and does not call voices or hum planes
  * safe copy removes the tone; the second check finds nothing; speech is kept
  * a 10-minute file is checked in well under a minute
Numbers go to <dir>/results.json and are printed. Exit code 1 if any test fails.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fo_media  # noqa: E402

RESULTS = []


def ok(name, cond, detail=""):
    RESULTS.append({"test": name, "pass": bool(cond), "detail": detail})
    print(("PASS  " if cond else "FAIL  ") + name + (("  - " + detail) if detail else ""), flush=True)
    return cond


def bandpass(x, sr, lo, hi):
    from scipy import signal
    sos = signal.butter(4, [lo, hi], "bandpass", fs=sr, output="sos")
    return signal.sosfiltfilt(sos, x)


def truth_trace_error(g, plane, dt):
    import numpy as np
    tt = g["idx"] * g.get("dt", dt)
    ft = np.interp(tt, plane["f_trace_t"], plane["f_trace_hz"])
    return float(np.mean(np.abs(g["f1"] - ft)))


def match_planes(found, truth_planes, dt):
    """Pair each true plane with the best detected one by trace error."""
    import numpy as np
    pairs = []
    for i, tp in enumerate(truth_planes):
        best = None
        for j, g in enumerate(found):
            tt = g["idx"] * g.get("dt", dt)
            ok_ = (tt >= tp["f_trace_t"][0]) & (tt <= tp["f_trace_t"][-1])
            if ok_.sum() < 10:
                continue
            ft = np.interp(tt, tp["f_trace_t"], tp["f_trace_hz"])
            err = float(np.mean(np.abs(g["f1"][ok_] - ft[ok_])))
            if best is None or err < best[1]:
                best = (j, err)
        pairs.append(best)
    return pairs


def build_cached(scn, d, regen=False, mp4=True):
    import make_test_audio as mt
    base = os.path.join(d, scn)
    need = [base + ".wav", base + ".truth.json", base + ".stems.npz"] + ([base + ".mp4"] if mp4 else [])
    if regen or not all(os.path.exists(p) for p in need):
        t = time.time()
        mt.build(scn, d, mp4=mp4)
        print(f"  (built {scn} test files in {time.time() - t:.0f} s)", flush=True)
    with open(base + ".truth.json") as fh:
        truth = json.load(fh)
    return base, truth


def truth_metrics(base, truth, res, hum_on=True):
    """Run the exact notch operations and score them against the known parts (left channel only, to save time).
    Plane suppression: the plane tone alone, notched along the traces found in the real mix. Collateral
    damage: what the same notches take from sound that has no plane in it. (The end-to-end test on the real
    mix is the second check finding nothing.)"""
    import numpy as np
    import soundfile as sf
    import fo_remove
    x, sr = sf.read(base + ".wav", dtype="float32", always_2d=True)
    x = x[:, :1]
    st = np.load(base + ".stems.npz")
    dt = res["_spec"]["dt"]
    fl, hum = res["flyovers"], res.get("hum")

    def pipe(sig):
        y, _, _ = fo_remove.remove_flyovers(sig, sr, fl, dt)
        if hum and hum_on:
            y, _ = fo_remove.mains_notch(y, sr, hum)
        return y.astype(np.float64)

    P = st["plane"][:, :1].astype(np.float32)
    H = st["hum"][:, :1].astype(np.float32)
    SN = (st["speech"] + st["noise"] + st["extra"])[:, :1].astype(np.float32)
    out = {}
    y_sn = pipe(SN)
    e_p = float(np.sum(P.astype(np.float64) ** 2))
    if e_p > 0 and fl:
        # the plane tone alone, notched along the traces found in the real mix
        out["plane_suppression_db"] = float(10 * np.log10(e_p / (np.sum(pipe(P) ** 2) + 1e-30)))
    else:
        out["plane_suppression_db"] = None
    if hum and hum_on:
        yh, _ = fo_remove.mains_notch(H, sr, hum)
        out["hum_suppression_db"] = float(10 * np.log10(np.sum(H.astype(np.float64) ** 2) / (np.sum(yh.astype(np.float64) ** 2) + 1e-30)))
    s0 = SN.astype(np.float64)[:, 0]
    d_all = s0 - y_sn[:, 0]
    out["collateral_db_all"] = float(10 * np.log10((np.sum(d_all ** 2) + 1e-30) / (np.sum(s0 ** 2) + 1e-30)))
    bp_s, bp_d = bandpass(s0, sr, 300, 3400), bandpass(d_all, sr, 300, 3400)
    out["collateral_db_speech_band"] = float(10 * np.log10((np.sum(bp_d ** 2) + 1e-30) / (np.sum(bp_s ** 2) + 1e-30)))
    out["plane_energy_share_percent"] = float(100 * e_p / (np.sum((P + H + SN).astype(np.float64) ** 2) + 1e-30))
    y_full = pipe(x)
    out["change"] = fo_remove.change_report(x, y_full.astype(np.float32), sr, fl, dt, hum if hum_on else None)
    return out


def run_main(d, args):
    import numpy as np
    import fo_report
    import safe_copy
    print("\n== main: one plane + speech + 50 Hz hum + noise, mp4 with GPS and creation time ==")
    base, truth = build_cached("main", d, args.regen)
    tp = truth["planes"][0]
    t = time.time()
    r = fo_report.check_path(base + ".mp4")
    t_check = time.time() - t
    res, meta = r["analysis"], r["meta"]
    dt = res["_spec"]["dt"]
    fl = res["flyovers"]
    out = {"check_seconds": t_check}
    ok("main: exactly one plane found (voices and hum not called planes)", len(fl) == 1, f"found {len(fl)}")
    if fl:
        g = fl[0]
        out["detected"] = {"start_s": g["start_s"], "end_s": g["end_s"], "confidence": g["confidence"],
                           "label": g["label"], "harmonics": [h[0] for h in g["harmonics"]]}
        ok("main: closest approach (t=%.0f s) lies inside the detected range" % tp["tc"],
           g["start_s"] <= tp["tc"] <= g["end_s"], f"{g['start_s']:.1f}-{g['end_s']:.1f} s")
        err = truth_trace_error(g, tp, dt) if True else 0
        pairs = match_planes(fl, truth["planes"], dt)
        err = pairs[0][1] if pairs and pairs[0] else 99
        out["pitch_trace_error_hz"] = err
        ok("main: traced pitch within 2 Hz of the true pitch on average", err < 2.0, f"mean error {err:.2f} Hz")
        ok("main: confidence is high", g["label"] == "high", f"{g['label']} ({g['confidence']:.2f})")
        ok("main: at least 6 harmonics found", len(g["harmonics"]) >= 6, f"{len(g['harmonics'])}")
    hum = res.get("hum")
    ok("main: 50 Hz mains hum found", bool(hum) and abs(hum["grid_hz"] - 50) < 1,
       f"grid {hum['grid_hz'] if hum else None}, mains {hum['mains_hz_median']:.3f} Hz" if hum else "none")
    ok("main: GPS and creation time tags reported", bool(meta["location"]) and bool(meta["times"]),
       f"{len(meta['location'])} location tags, {len(meta['times'])} time tags")
    loc = [m for m in meta["location"] if "lat" in m]
    ok("main: GPS parsed to the right coordinates", bool(loc) and abs(loc[0]["lat"] - 42.6977) < 1e-4
       and abs(loc[0]["lon"] - 23.3219) < 1e-4)
    # truth-based numbers on the WAV
    tm = truth_metrics(base, truth, res)
    out["truth"] = tm
    ok("main: plane tone suppressed by at least 30 dB", tm["plane_suppression_db"] >= 30,
       f"{tm['plane_suppression_db']:.1f} dB")
    ok("main: hum suppressed by at least 25 dB", tm["hum_suppression_db"] >= 25, f"{tm['hum_suppression_db']:.1f} dB")
    ok("main: notches take less than 10 % of the energy of plane-free sound in the 300-3400 Hz band",
       tm["collateral_db_speech_band"] < -10, f"{tm['collateral_db_speech_band']:.1f} dB "
       f"({100 * 10 ** (tm['collateral_db_speech_band'] / 10):.1f} %)")
    ok("main: overall loudness change under 1 dB", abs(tm["change"]["level_change_db"]) < 1.0,
       f"{tm['change']['level_change_db']:+.2f} dB (plane + hum are {tm['plane_energy_share_percent']:.1f} % of the energy)")
    ch = tm["change"]
    ok("main: speech band spectrogram changes < 0.1 dB outside the notches",
       ch["speech_band_mean_abs_db"] < 0.1, f"{ch['speech_band_mean_abs_db']:.3f} dB")
    # the real end-to-end command
    dst = os.path.join(d, "main.safe.mp4")
    rd = os.path.join(d, "out")
    t = time.time()
    code = safe_copy.main([base + ".mp4", "--out", dst, "--report-dir", rd, "--seed", "1"])
    out["safe_copy_seconds"] = time.time() - t
    with open(os.path.join(rd, "main.safe-copy.json")) as fh:
        rep = json.load(fh)
    ok("main: safe copy says SAFE", rep["verdict"] == "SAFE", rep["verdict"] + " " + "; ".join(rep["problems"]))
    ok("main: second check finds no plane", len(rep["after"]["flyovers"]) == 0, f"{len(rep['after']['flyovers'])} left")
    ok("main: second check finds no hum", rep["after"]["hum"] is None)
    ok("main: no location/time tags left", not rep["after"]["metadata"]["location"] and not rep["after"]["metadata"]["times"])
    out["safe_copy_report"] = {"audio_change": rep["audio_change"], "after": rep["after"]["summary"]}

    def vmd5(p):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-map", "0:v:0", "-c", "copy", "-f", "md5", "-"],
                              capture_output=True, text=True).stdout.strip()
    ok("main: video stream unchanged (packet checksum equal)", vmd5(base + ".mp4") == vmd5(dst))
    ok("main: audio length unchanged", abs(fo_media.media_summary(fo_media.probe(dst))["duration_s"]
                                           - fo_media.media_summary(fo_media.probe(base + ".mp4"))["duration_s"]) < 0.1)
    return out, base, truth


def run_decoy(d, base, truth, res):
    import numpy as np
    import soundfile as sf
    import fo_detect, fo_remove
    print("\n== decoy: replace the plane tone with a fake Doppler curve ==")
    x, sr = sf.read(base + ".wav", dtype="float32", always_2d=True)
    dt = res["_spec"]["dt"]
    fl, hum = res["flyovers"], res.get("hum")
    y, kept, _ = fo_remove.remove_flyovers(x, sr, fl, dt, keep_tones=True)
    y, _ = fo_remove.mains_notch(y, sr, hum)
    y, fakes = fo_remove.add_decoys(y, sr, kept, fl, dt, np.random.default_rng(3))
    r2 = fo_detect.analyze(fo_remove.to_8k(y.mean(1), sr).astype(np.float32))
    out = {"fakes": fakes}
    ok("decoy: the check still hears a plane (expected)", len(r2["flyovers"]) >= 1, f"{len(r2['flyovers'])}")
    if r2["flyovers"]:
        g2 = max(r2["flyovers"], key=lambda g: g["confidence"])
        g1 = fl[0]
        i0, i1 = max(g1["i0"], g2["i0"]), min(g1["i1"], g2["i1"])
        a = g1["f1"][i0 - g1["i0"]: i1 - g1["i0"] + 1]
        b = g2["f1"][i0 - g2["i0"]: i1 - g2["i0"] + 1]
        diff = float(np.mean(np.abs(a - b)))
        out["mean_pitch_difference_hz"] = diff
        out["median_pitch_ratio"] = float(np.median(b) / np.median(a))
        out["steepest_slide_real_hz_s"] = float(np.max(np.abs(np.gradient(a, dt))))
        out["steepest_slide_fake_hz_s"] = float(np.max(np.abs(np.gradient(b, dt))))
        ok("decoy: fake pitch curve differs from the real one by > 4 Hz on average", diff > 4.0, f"{diff:.1f} Hz")
        ok("decoy: overall pitch stays in the same range (median within 20 %)", abs(out["median_pitch_ratio"] - 1) < 0.20,
           f"ratio {out['median_pitch_ratio']:.3f}")
    e_in = float(np.sum(x ** 2))
    e_out = float(np.sum(y ** 2))
    out["level_change_db"] = 10 * np.log10(e_out / e_in)
    return out


def run_other(d, args):
    import fo_report
    out = {}
    print("\n== no plane: speech + hum only ==")
    base, truth = build_cached("no_plane", d, args.regen)
    r = fo_report.check_path(base + ".mp4")
    out["no_plane"] = {"flyovers": len(r["analysis"]["flyovers"]), "weak": r["analysis"]["n_weak"],
                       "voice_tracks": r["analysis"]["n_voice_tracks"]}
    ok("no_plane: no plane reported", len(r["analysis"]["flyovers"]) == 0, f"{len(r['analysis']['flyovers'])}")
    tm = truth_metrics(base, truth, r["analysis"])
    ok("no_plane: with nothing to remove, only the hum notch acts (speech band energy change < 0.01 dB)",
       abs(tm["change"].get("speech_band_energy_change_db", 0)) < 0.01,
       f"{tm['change'].get('speech_band_energy_change_db', 0):+.4f} dB")

    print("\n== roar: wind/traffic-like broadband noise at 10-40 s + speech ==")
    base, truth = build_cached("roar", d, args.regen)
    r = fo_report.check_path(base + ".mp4")
    res = r["analysis"]
    out["roar"] = {"flyovers": len(res["flyovers"]), "roar_zones": res["roar"]}
    ok("roar: broadband roar is not called a plane", len(res["flyovers"]) == 0, f"{len(res['flyovers'])}")
    ok("roar: the loud stretch is flagged as masking noise",
       any(a < 14 and b > 36 for a, b in res["roar"]), f"{[(round(a), round(b)) for a, b in res['roar']]}")

    print("\n== two overlapping planes ==")
    base, truth = build_cached("two_planes", d, args.regen)
    r = fo_report.check_path(base + ".mp4")
    res = r["analysis"]
    dt = res["_spec"]["dt"]
    pairs = match_planes(res["flyovers"], truth["planes"], dt)
    out["two_planes"] = {"found": len(res["flyovers"]), "trace_errors_hz": [p[1] if p else None for p in pairs],
                         "overlap_flagged": any(g["overlaps"] for g in res["flyovers"])}
    ok("two_planes: two planes found", len(res["flyovers"]) == 2, f"{len(res['flyovers'])}")
    ok("two_planes: each true plane matched with trace error < 3 Hz", all(p and p[1] < 3.0 for p in pairs),
       ", ".join(f"{p[1]:.2f}" if p else "none" for p in pairs))
    ok("two_planes: overlap is flagged", out["two_planes"]["overlap_flagged"])
    tm = truth_metrics(base, truth, res)
    out["two_planes"]["plane_suppression_db"] = tm["plane_suppression_db"]
    out["two_planes"]["collateral_db_speech_band"] = tm["collateral_db_speech_band"]
    ok("two_planes: tones suppressed by at least 25 dB", tm["plane_suppression_db"] >= 25, f"{tm['plane_suppression_db']:.1f} dB")

    print("\n== turning plane (pitch wobbles up and down as well as sliding) ==")
    base, truth = build_cached("turning", d, args.regen)
    r = fo_report.check_path(base + ".mp4")
    res = r["analysis"]
    out["turning"] = {"found": len(res["flyovers"]),
                      "kinds": [(g["kind"], round(g["confidence"], 2)) for g in res["flyovers"]]}
    ok("turning: still found as a flyover, flagged as an irregular glide",
       len(res["flyovers"]) >= 1 and any(g["kind"] != "plane" or g["label"] != "high" for g in res["flyovers"]),
       f"{out['turning']['kinds']}")
    tm = truth_metrics(base, truth, res)
    out["turning"]["plane_suppression_db"] = tm["plane_suppression_db"]
    ok("turning: tone suppressed by at least 10 dB (partial: a turning plane's faint tails cannot be extrapolated)",
       tm["plane_suppression_db"] >= 10, f"{tm['plane_suppression_db']:.1f} dB")
    import safe_copy
    rd = os.path.join(d, "out")
    safe_copy.main([base + ".mp4", "--out", os.path.join(d, "turning.safe.mp4"), "--report-dir", rd, "--seed", "1"])
    with open(os.path.join(rd, "turning.safe-copy.json")) as fh:
        rep = json.load(fh)
    honest = (rep["verdict"] == "SAFE" and not rep["after"]["flyovers"] and not rep["problems"]) or \
             (rep["verdict"] == "NOT FULLY SAFE" and rep["problems"])
    out["turning"]["safe_copy_verdict"] = rep["verdict"]
    ok("turning: the safe copy's verdict matches what its own second check found", honest, rep["verdict"])

    print("\n== sirens: hi-lo at 8-18 s, wail at 24-36 s ==")
    base, truth = build_cached("siren", d, args.regen)
    r = fo_report.check_path(base + ".mp4")
    res = r["analysis"]
    out["siren"] = {"flyovers": len(res["flyovers"]), "sirens": res["sirens"]}
    ok("siren: not called a plane", len(res["flyovers"]) == 0, f"{len(res['flyovers'])}")
    kinds = sorted(s["kind"] for s in res["sirens"])
    ok("siren: both sirens noticed (low-confidence hint)", kinds == ["sweep", "two-tone"], f"{kinds}")
    return out


def run_jet(d, args):
    import numpy as np
    import fo_report
    import safe_copy
    print("\n== jet: a stack of fan tones (1.3 / 2.6 / 3.9 / 5.2 kHz at rest) sliding down, plus speech, hum, noise ==")
    base, truth = build_cached("jet", d, args.regen)
    r = fo_report.check_path(base + ".mp4")
    res = r["analysis"]
    fl = res["flyovers"]
    out = {"found": len(fl)}
    ok("jet: exactly one flyover found", len(fl) == 1, f"{len(fl)}")
    if fl:
        g = fl[0]
        pairs = match_planes(fl, truth["planes"], res["_spec"]["dt"])
        err = pairs[0][1] if pairs and pairs[0] else 999.0
        out.update({"band": g.get("band"), "label": g["label"], "confidence": g["confidence"], "harmonics": [h[0] for h in g["harmonics"]],
                    "f_start_hz": g["f_start"], "f_end_hz": g["f_end"], "pitch_error_hz": err})
        ok("jet: found in the high (fan-tone) search band", g.get("band") == "high", str(g.get("band")))
        ok("jet: at least 3 harmonics found", len(g["harmonics"]) >= 3, f"{[h[0] for h in g['harmonics']]}")
        ok("jet: traced pitch within 1 % of the true pitch on average (1.6 kHz base tone)", err < 16.0, f"{err:.1f} Hz")
    tm = truth_metrics(base, truth, res)
    out["truth"] = tm
    ok("jet: fan tones suppressed by at least 30 dB", tm["plane_suppression_db"] >= 30, f"{tm['plane_suppression_db']:.1f} dB")
    ok("jet: notches take less than 10 % of the energy of plane-free sound in 300-3400 Hz", tm["collateral_db_speech_band"] < -10,
       f"{tm['collateral_db_speech_band']:.1f} dB")
    rd = os.path.join(d, "out")
    safe_copy.main([base + ".mp4", "--out", os.path.join(d, "jet.safe.mp4"), "--report-dir", rd, "--seed", "1"])
    with open(os.path.join(rd, "jet.safe-copy.json")) as fh:
        rep = json.load(fh)
    out["safe_verdict"] = rep["verdict"]
    ok("jet: safe copy says SAFE and the second check finds no flyover", rep["verdict"] == "SAFE" and not rep["after"]["flyovers"],
       rep["verdict"] + " " + "; ".join(rep["problems"]))
    return out


def run_links_and_locate(d, args):
    """No network: yt-dlp is replaced by a stub that fails the way this PC does; the solver by a stub."""
    import io
    import contextlib
    import tempfile
    import shutil
    import locate
    out = {}
    print("\n== links: certificate retry, never unverified ==")
    tmp = tempfile.mkdtemp(prefix="fo-stub-")
    stub = os.path.join(tmp, "stub_ytdlp.py")
    with open(stub, "w") as fh:
        fh.write(
            "import sys\n"
            "a = sys.argv[1:]\n"
            "open(sys.argv[0] + '.log', 'a').write(' '.join(a) + chr(10))\n"
            "if '--no-check-certificates' in a:\n"
            "    sys.exit(9)\n"
            "if '--compat-options' not in a:\n"
            "    sys.stderr.write('ERROR: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')\n"
            "    sys.exit(1)\n"
            "print('{\"title\": \"stub\", \"is_live\": false}')\n")
    real = fo_media._ytdlp_cmd
    fo_media._ytdlp_cmd = lambda: [sys.executable, stub]
    r = fo_media.run_ytdlp(["--dump-single-json", "https://example.invalid/x"])
    fo_media._ytdlp_cmd = real
    log = open(stub + ".log").read().splitlines()
    ok("links: CERTIFICATE_VERIFY_FAILED is retried once with --compat-options no-certifi",
       r.returncode == 0 and len(log) == 2 and "--compat-options no-certifi" in log[1]
       and "--compat-options" not in log[0], f"{len(log)} calls")
    ok("links: --no-check-certificates is never used", not any("--no-check-certificates" in l for l in log))

    print("\n== locate: consent gate, case files, honest two-place report ==")
    base = os.path.join(d, "main")
    fake_repo = os.path.join(tmp, "repo")
    os.makedirs(os.path.join(fake_repo, "solver"))
    with open(os.path.join(fake_repo, "solver", "solve.py"), "w") as fh:
        fh.write("# stub\n")
    cases = os.path.join(tmp, "cases")

    def run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = locate.main(argv)
        return code, buf.getvalue()

    code, txt = run([base + ".mp4"])
    ok("locate: refuses to run without a consent word and asks", code == 3 and "ONLY for" in txt and "own" in txt)
    code, txt = run([base + ".mp4", "--consent", "public-challenge"])
    ok("locate: a public challenge must be named", code == 3)
    code, txt = run([os.path.join(d, "no_plane.mp4"), "--consent", "own", "--bbox", "42,22,43,24",
                     "--cases-dir", cases, "--repo", fake_repo])
    ok("locate: no plane in the audio -> stops", code == 5 and "nothing to locate" in txt)
    code, txt = run([base + ".mp4", "--consent", "own", "--cases-dir", cases, "--repo", fake_repo,
                     "--bbox", "42.2,23.0,42.9,23.8"])
    ok("locate: no ADS-B -> says what is needed", code == 6 and "ADS-B" in txt)
    adsb = os.path.join(tmp, "adsb.jsonl")
    with open(adsb, "w") as fh:
        fh.write('{"t": 1, "src": "adsblol", "ac": []}\n')
    code, txt = run([base + ".mp4", "--consent", "own", "--cases-dir", cases, "--repo", fake_repo, "--case", "t1",
                     "--bbox", "42.5,23.3,42.7,23.5", "--adsb", adsb, "--dry-run"])
    cj = json.load(open(os.path.join(cases, "t1", "case.json")))
    ok("locate: case.json has audio, utc_start (from the file's creation_time) and bbox",
       code == 0 and cj["utc_start"].startswith("2026-11-02T14:03:10") and cj["bbox"] == [42.5, 23.3, 42.7, 23.5]
       and cj["audio"].endswith("audio.flac") and os.path.exists(os.path.join(cases, "t1", "audio.flac")),
       str(cj["utc_start"]))
    ok("locate: says the start time came from the file's tag and may be off", "file tag" in txt)
    ok("locate: warns the box is smaller than 40 x 40 km", "WARNING" in txt and "40 x 40" in txt)
    code, txt = run([base + ".mp4", "--consent", "own", "--cases-dir", cases, "--case", "t2", "--repo", fake_repo,
                     "--bbox", "42,22,43.2,24", "--adsb", adsb])
    ok("locate: solver not installed -> install steps are shown",
       code == 4 and "git clone" in txt and "colsto-leak-and-mask" in txt)
    # a stub solver that answers like the real one for a single plane (two-band result)
    fake = os.path.join(tmp, "fake_solve.py")
    with open(fake, "w") as fh:
        fh.write(
            "import sys, os, json\n"
            "name = os.path.basename(os.path.normpath(sys.argv[-1]))\n"
            "os.makedirs(os.path.join('results', name), exist_ok=True)\n"
            "ans = {'lat': 42.5, 'lon': 23.4, 'n_flyovers_used': 1, 'n_flyovers_detected': 1, 'two_band': True,\n"
            "       'two_band_reason': 'fewer than two flyovers used: one track leaves two mirror bands',\n"
            "       'mirror_point': {'lat': 42.58, 'lon': 23.46},\n"
            "       'flights_used': [{'used': True, 'callsign': 'TEST1', 'type': 'C172', 'cpa_distance_m': 300.0,\n"
            "                         'cpa_utc': '2026-11-02T14:03:40', 'ground_speed_ms': 60.0}]}\n"
            "json.dump(ans, open(os.path.join('results', name, 'answer.json'), 'w'))\n")
    code, txt = run([base + ".mp4", "--consent", "owner-agreed", "--cases-dir", cases, "--case", "t3",
                     "--repo", fake_repo, "--bbox", "42,22,43.2,24", "--adsb", adsb,
                     "--solver-cmd", f"{sys.executable} {fake}"])
    ok("locate: one plane -> says two possible places and gives the mirror point",
       code == 0 and "TWO POSSIBLE PLACES" in txt and "42.58000" in txt and "apart" in txt)
    out["locate_stub_output"] = txt.split("RESULT FROM THE SOLVER")[-1]
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def run_long(d, args):
    import fo_report
    print("\n== speed: 10-minute file ==")
    base = os.path.join(d, "main")
    long_mp4 = os.path.join(d, "long10.mp4")
    if not os.path.exists(long_mp4):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=5",
                        "-stream_loop", "6", "-i", base + ".wav", "-t", "600", "-c:v", "libx264",
                        "-preset", "ultrafast", "-crf", "36", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                        "-shortest", long_mp4], check=True)
    import check as check_cli
    t = time.time()
    r = fo_report.check_path(long_mp4)
    t_analysis = time.time() - t
    t = time.time()
    code = check_cli.main([long_mp4, "--out", os.path.join(d, "out")])
    t_full = time.time() - t
    n = len(r["analysis"]["flyovers"])
    out = {"duration_s": r["media"]["duration_s"], "analysis_seconds": t_analysis,
           "check_cli_seconds_with_png": t_full, "planes_found": n}
    ok("long: file is 10 minutes", abs(r["media"]["duration_s"] - 600) < 1.5, f"{r['media']['duration_s']:.1f} s")
    ok("long: check.py (with spectrogram PNG) finishes in under 60 s", t_full < 60, f"{t_full:.1f} s (analysis alone {t_analysis:.1f} s)")
    ok("long: the 6 whole repeated planes and the start of the 7th (cut at 10:00) are all found", n == 7, f"{n} found")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="data/skilltest")
    ap.add_argument("--regen", action="store_true", help="rebuild the synthetic files")
    ap.add_argument("--quick", action="store_true", help="skip the 10-minute speed test")
    args = ap.parse_args(argv)
    fo_media.need_ffmpeg()
    os.makedirs(args.dir, exist_ok=True)
    allr = {}
    main_out, base, truth = run_main(args.dir, args)
    allr["main"] = main_out
    import fo_report
    r = fo_report.check_path(base + ".mp4")
    allr["decoy"] = run_decoy(args.dir, base, truth, r["analysis"])
    allr["others"] = run_other(args.dir, args)
    allr["jet"] = run_jet(args.dir, args)
    allr["links_and_locate"] = run_links_and_locate(args.dir, args)
    if not args.quick:
        allr["long"] = run_long(args.dir, args)
    n_fail = sum(1 for x in RESULTS if not x["pass"])
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} checks passed")
    allr["checks"] = RESULTS
    with open(os.path.join(args.dir, "results.json"), "w") as fh:
        json.dump(allr, fh, indent=1, default=float)
    print("numbers written to", os.path.join(args.dir, "results.json"))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
