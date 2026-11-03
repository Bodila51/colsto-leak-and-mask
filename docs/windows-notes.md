# Running doppler-locate on this Windows 11 PC

Machine: Ryzen 7 5700X (16 threads), 16 GB RAM (often 2-6 GB free), RTX 3060 (unused), Python 3.12, no WSL,
no compiler, no CUDA toolkit. Everything runs in CPU mode on numba kernels.

doppler-locate (MIT, github.com/digitalis-p/doppler-locate) is cloned into `data/vendor/doppler-locate`
(`data/` is git-ignored). Its source edits are saved as `solver/windows_patches.diff`
(`git -C data/vendor/doppler-locate diff`); apply them to a fresh clone with `git apply`.

## Install

```
git clone --depth 1 https://github.com/digitalis-p/doppler-locate data/vendor/doppler-locate
cd data/vendor/doppler-locate
python -m uv venv --python 3.12 .venv
python -m uv pip install --system-certs --python .venv/Scripts/python.exe -e ".[test]" truststore psutil
```

Python 3.12 instead of the README's 3.13: the package needs only >= 3.11 and 3.12 is already on the PC.
The `gui` extra is skipped (GTK). `pytest`: 9 passed, 2 skipped (the native-library tests).

## Every fix and why

| # | What broke | Fix |
|---|---|---|
| 1 | `uv` and Python `requests` fail with `invalid peer certificate: UnknownIssuer` (this PC's TLS chain is not in certifi) | `uv ... --system-certs`; `truststore` plus a `sitecustomize.py` in the venv's site-packages (`truststore.inject_into_ssl()`) so `requests` uses the Windows trust store. curl and yt-dlp `--compat-options no-certifi` already worked. `--no-check-certificates` is never used. |
| 2 | `gh` (GitHub CLI) is not installed; `dopploc fetch-adsb` shells out to it | `adsb.fetch_day` now falls back to the GitHub REST API (release assets of `adsblol/globe_history_<year>`) with a resumable `requests` download. Same files as `gh release download`. |
| 3 | `search.py` uses `multiprocessing.get_context("fork")`; Windows has no fork | On `win32` the parent dumps the sample table and tile indices as `.npy` files and each spawned worker memory-maps them (`_dump_state` / `_win_init`). Workers share the OS page cache instead of holding N copies. `ElevationLattice` got a `.path` attribute so workers can reopen it. |
| 4 | `_default_workers()` reads `/proc/meminfo` | falls back to `GlobalMemoryStatusEx` through ctypes (`search._avail_gb`). |
| 5 | `ingest` queued the whole 3.9 GB tar in the parent and kept 20 M rows per output file in RAM | in-flight work is bounded (2048 traces) and files are written every 4 M rows. Ingest of 2026-09-08 (53.4 M points): 227 s, peak private memory 5.4 GB, six workers. |
| 6 | `detect` starts up to 16 spawned workers, each re-importing numpy/scipy/numba; with ~4 GB free the pool was OOM-killed (`BrokenProcessPool`) | pool size = min(6, free RAM / 1.2 GB); `DOPPLOC_WORKERS` overrides. |
| 7 | no native library (`libdopploc_native.so`), no gcc / nvcc | numba fallback, as the README allows (`native.backend() == "numba"`). `[search] gpu = false`. |
| 8 | yt-dlp: `HTTP Error 403` on the audio of youtu.be/4CxVxuMVkMc with the default player client | `yt-dlp --compat-options no-certifi --extractor-args "youtube:player_client=web_embedded" -f bestaudio ...` |

Other Windows details: pass absolute executable paths to `subprocess` from Git Bash (a relative
`.venv/Scripts/x.exe` is not found); set `PYTHONUNBUFFERED=1` when logging to a file.

## Memory

`solver/runmeter.py` wraps a command and records wall time and the peak working set / private bytes of the whole
process tree (and the lowest free RAM seen). Always run heavy steps one at a time on this PC.
