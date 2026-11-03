"""Media helpers for flyover-check: ffmpeg/ffprobe, file metadata leaks, yt-dlp download.

Imported by check.py, safe_copy.py and locate.py (same folder). No third-party
imports except numpy, which every script already declares.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np

INSTALL_HELP = """ffmpeg and ffprobe are required but were not found on PATH.
Install them, then open a new terminal:
  Windows : winget install Gyan.FFmpeg        (or: choco install ffmpeg)
  macOS   : brew install ffmpeg
  Debian/Ubuntu : sudo apt install ffmpeg
  Fedora  : sudo dnf install ffmpeg
Check with:  ffmpeg -version   and   ffprobe -version"""

YTDLP_HELP = """yt-dlp was not found on PATH (needed for links).
  Windows : winget install yt-dlp.yt-dlp        (or: pip install -U yt-dlp)
  macOS   : brew install yt-dlp
  Linux   : pipx install yt-dlp   (or: pip install -U yt-dlp)"""


def need_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        print("Missing tool(s): " + ", ".join(missing), file=sys.stderr)
        print(INSTALL_HELP, file=sys.stderr)
        sys.exit(2)


def is_url(s: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s))


# ----------------------------------------------------------------------------
# probing
# ----------------------------------------------------------------------------

def probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffprobe could not read the file: " + r.stderr.strip()[:300])
    return json.loads(r.stdout or "{}")


def media_summary(info: dict) -> dict:
    streams = info.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition", {}) or {}).get("attached_pic")]
    dur = None
    try:
        dur = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        for s in audio:
            try:
                dur = float(s.get("duration"))
                break
            except (TypeError, ValueError):
                pass
    a0 = audio[0] if audio else None
    return {
        "duration_s": dur,
        "format": info.get("format", {}).get("format_name"),
        "has_video": bool(video),
        "n_audio": len(audio),
        "audio_codec": a0.get("codec_name") if a0 else None,
        "sample_rate": int(a0.get("sample_rate", 0)) if a0 else None,
        "channels": int(a0.get("channels", 0)) if a0 else None,
        "video_codec": video[0].get("codec_name") if video else None,
    }


# ----------------------------------------------------------------------------
# metadata leaks
# ----------------------------------------------------------------------------

_LOC_KEY = re.compile(r"(location|gps|geo|latitude|longitude|xyz|iso6709)", re.I)
_TIME_KEYS = ("creation_time", "creationdate", "encoded_date", "tagged_date", "date_utc",
              "date", "datetime", "recorded", "year")
_DEVICE_KEYS = ("make", "model", "manufacturer", "software", "encoder", "android.version",
                "com.apple.quicktime.software")
_DATA_STREAM = re.compile(r"(gpmd|gopro|camm|mebx|dji|telemetry|gps|nmea|rtmd)", re.I)
_ISO6709 = re.compile(r"^\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def parse_iso6709(s: str):
    m = _ISO6709.match(s or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    # "+DDMM" forms are rare in phone files; only accept plausible degrees.
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    alt = float(m.group(3)) if m.group(3) else None
    return {"lat": lat, "lon": lon, "alt_m": alt}


def metadata_leaks(info: dict) -> dict:
    """Look through container and stream tags for location, time and device info."""
    tags = []
    for k, v in (info.get("format", {}).get("tags") or {}).items():
        tags.append(("file", k, str(v)))
    for s in info.get("streams", []):
        label = f"{s.get('codec_type', '?')} stream {s.get('index')}"
        for k, v in (s.get("tags") or {}).items():
            tags.append((label, k, str(v)))
    location, times, device = [], [], []
    for where, k, v in tags:
        kl = k.lower()
        if _LOC_KEY.search(kl):
            item = {"where": where, "key": k, "value": v}
            pos = parse_iso6709(v)
            if pos is None:
                m = re.findall(r"[-+]?\d{1,3}\.\d{3,}", v)
                if len(m) >= 2:
                    la, lo = float(m[0]), float(m[1])
                    if -90 <= la <= 90 and -180 <= lo <= 180:
                        pos = {"lat": la, "lon": lo, "alt_m": None}
            if pos:
                item.update(pos)
            location.append(item)
        elif any(t in kl for t in _TIME_KEYS) and kl not in ("encoder",):
            if kl == "year" or kl == "date" or "time" in kl or "date" in kl:
                times.append({"where": where, "key": k, "value": v})
        if any(t in kl for t in _DEVICE_KEYS):
            device.append({"where": where, "key": k, "value": v})
    data_streams = []
    for s in info.get("streams", []):
        if s.get("codec_type") in ("data", "subtitle", "attachment") or s.get("codec_type") is None:
            blob = " ".join(str(x) for x in (s.get("codec_tag_string"), s.get("codec_name"),
                                              (s.get("tags") or {}).get("handler_name")))
            if _DATA_STREAM.search(blob):
                data_streams.append({"index": s.get("index"), "what": blob.strip()})
    # remove duplicates of the same time value
    seen, uniq = set(), []
    for t in times:
        key = (t["key"].lower(), t["value"])
        if key not in seen:
            seen.add(key)
            uniq.append(t)
    return {
        "location": location,
        "times": uniq,
        "device": device,
        "data_streams": data_streams,
        "has_location": bool(location) or bool(data_streams),
        "has_time": bool(uniq),
    }


def best_creation_time(meta: dict):
    """Return (datetime in UTC or None, note). Prefers values with a time zone."""
    best = None
    for t in meta.get("times", []):
        k, v = t["key"].lower(), t["value"]
        if "creation" in k or "date" in k or "time" in k:
            try:
                s = v.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(timezone.utc)
                if best is None or "creationdate" in k:
                    best = (dt, k)
            except ValueError:
                continue
    return best


# ----------------------------------------------------------------------------
# audio decode
# ----------------------------------------------------------------------------

def decode_mono(path: str, sr: int = 8000, audio_index: int = 0) -> np.ndarray:
    """Decode the audio to a mono float32 array at `sr` through ffmpeg (video is skipped)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-map", f"0:a:{audio_index}",
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg could not decode the audio: "
                           + r.stderr.decode("utf-8", "replace").strip()[:300])
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def decode_full_to_wav(path: str, wav_path: str, audio_index: int = 0) -> None:
    """Decode the first audio track at its native rate and channel count to float32 WAV."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", path, "-map", f"0:a:{audio_index}",
           "-vn", "-sn", "-dn", "-c:a", "pcm_f32le", wav_path]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not decode the audio: "
                           + r.stderr.decode("utf-8", "replace").strip()[:300])


# ----------------------------------------------------------------------------
# yt-dlp
# ----------------------------------------------------------------------------

def _ytdlp_cmd() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True)
    if r.returncode == 0:
        return [sys.executable, "-m", "yt_dlp"]
    print(YTDLP_HELP, file=sys.stderr)
    sys.exit(2)


def run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    """Run yt-dlp. On CERTIFICATE_VERIFY_FAILED retry once with --compat-options no-certifi.
    Certificate checking is never switched off."""
    base = _ytdlp_cmd()
    r = subprocess.run(base + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 and "CERTIFICATE_VERIFY_FAILED" in (r.stderr or "") + (r.stdout or ""):
        r = subprocess.run(base + ["--compat-options", "no-certifi"] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    return r


def fetch_link(url: str, outdir: str, want_video: bool = False, live_seconds: int = 300) -> dict:
    """Download a link (or record a live stream for `live_seconds`). Returns
    {path, title, is_live, utc_start (datetime|None), utc_note, source_note}.
    Downloaded files are untrusted data: they are only ever passed to ffmpeg/ffprobe."""
    os.makedirs(outdir, exist_ok=True)
    r = run_ytdlp(["--no-playlist", "--no-warnings", "--dump-single-json", url])
    if r.returncode != 0:
        raise RuntimeError("yt-dlp could not read the link: " + (r.stderr or "").strip()[-400:])
    info = json.loads(r.stdout)
    title = info.get("title") or "video"
    is_live = bool(info.get("is_live"))
    out = {"title": title, "is_live": is_live, "utc_start": None, "utc_note": "",
           "webpage_url": info.get("webpage_url") or url}

    if is_live:
        g = run_ytdlp(["--no-playlist", "--no-warnings", "-f", "bestaudio/best", "-g", url])
        lines = [x for x in (g.stdout or "").splitlines() if x.strip()]
        if g.returncode != 0 or not lines:
            raise RuntimeError("yt-dlp could not get the live stream address: "
                               + (g.stderr or "").strip()[-300:])
        wav = os.path.join(outdir, "live_capture.wav")
        started = datetime.now(timezone.utc)
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", lines[0], "-t", str(int(live_seconds)),
               "-vn", "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", wav]
        subprocess.run(cmd, capture_output=True)
        if not os.path.exists(wav) or os.path.getsize(wav) < 1000:
            raise RuntimeError("could not record the live stream")
        out.update(path=wav, utc_start=started,
                   utc_note=("Live capture: start time is this computer's clock when recording began. "
                             "The stream itself runs 5-60 s behind real time, so the true start is "
                             "somewhat earlier."))
        return out

    fmt = ("bv*[height<=720]+ba/b[height<=720]/bv*+ba/b" if want_video else "ba/b")
    args = ["--no-playlist", "--no-warnings", "-f", fmt, "-o", os.path.join(outdir, "source.%(ext)s")]
    if want_video:
        args += ["--merge-output-format", "mp4"]
    args.append(url)
    d = run_ytdlp(args)
    files = [f for f in glob.glob(os.path.join(outdir, "source.*"))
             if not f.endswith((".part", ".ytdl", ".json"))]
    if d.returncode != 0 or not files:
        raise RuntimeError("yt-dlp download failed: " + (d.stderr or "").strip()[-400:])
    files.sort(key=os.path.getmtime, reverse=True)
    out["path"] = files[0]
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts:
        out["utc_start"] = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        out["utc_note"] = ("Time comes from the site (upload/release time), not from when it was "
                           "recorded. For location work this is usually far too late.")
    elif info.get("upload_date"):
        out["utc_note"] = f"Site shows upload date {info['upload_date']} only; not when it was recorded."
    out["source_note"] = ("Videos that went through YouTube and similar sites have their GPS and "
                          "creation-time tags removed by the site, so metadata checks apply to "
                          "your original file, not to the download.")
    return out


# ----------------------------------------------------------------------------
# safe container writing
# ----------------------------------------------------------------------------

_AUDIO_BY_EXT = {
    ".mp4": ("aac", ["-b:a", "256k"]), ".m4v": ("aac", ["-b:a", "256k"]),
    ".mov": ("aac", ["-b:a", "256k"]), ".mkv": ("aac", ["-b:a", "256k"]),
    ".webm": ("libopus", ["-b:a", "192k"]), ".m4a": ("aac", ["-b:a", "256k"]),
    ".mp3": ("libmp3lame", ["-b:a", "256k"]), ".flac": ("flac", []),
    ".wav": ("pcm_s16le", []), ".ogg": ("libopus", ["-b:a", "192k"]), ".opus": ("libopus", ["-b:a", "192k"]),
}


def write_safe_container(src: str, wav: str, dst: str, has_video: bool) -> list[str]:
    """Mux the cleaned audio with the untouched video (-c:v copy) and drop all metadata,
    other streams, chapters and GPS/telemetry tracks. Returns the ffmpeg command used."""
    ext = os.path.splitext(dst)[1].lower()
    acodec, aopts = _AUDIO_BY_EXT.get(ext, ("aac", ["-b:a", "256k"]))
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", wav]
    if has_video:
        cmd += ["-i", src, "-map", "1:v:0", "-map", "0:a:0", "-c:v", "copy"]
    else:
        cmd += ["-map", "0:a:0"]
    cmd += ["-c:a", acodec] + aopts
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1",
            "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1",
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
            "-metadata", "location=", "-metadata", "location-eng=",
            "-metadata", "com.apple.quicktime.location.ISO6709=",
            "-metadata", "com.apple.quicktime.creationdate=",
            "-metadata", "creation_time="]
    if ext in (".mp4", ".m4v", ".mov", ".m4a"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(dst)
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg could not write the safe copy: "
                           + r.stderr.decode("utf-8", "replace").strip()[:400])
    return cmd


def tempdir(prefix: str = "flyover-") -> str:
    return tempfile.mkdtemp(prefix=prefix)


def resolve_input(src: str, workdir: str, want_video: bool = False, live_seconds: int = 300) -> tuple[str, dict]:
    """A local path stays as it is; a link is fetched with yt-dlp. Returns (path, extra-info)."""
    if is_url(src):
        got = fetch_link(src, workdir, want_video=want_video, live_seconds=live_seconds)
        return got["path"], got
    if not os.path.isfile(src):
        raise FileNotFoundError(f"no such file: {src}")
    return src, {}
