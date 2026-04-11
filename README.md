# OmniDL v17.1.0 (post-release)

A desktop media downloader supporting YouTube, TikTok, Instagram, Twitter/X, Facebook, and 1000+ sites — built with Python, CustomTkinter, yt-dlp, and gallery-dl.

## Features

- **Download** video, audio, and images from 1000+ platforms via yt-dlp and gallery-dl
- **Convert** downloaded files to MP4, MP3, MKV, AVI using FFmpeg (GPU-accelerated where available)
- **Live stream monitor** — auto-record when a stream goes live; Instagram profile watch (cookie-based) and TikTok profile watch (no cookie needed)
- **Batch download** — paste multiple URLs; per-platform analysis delays prevent rate-limiting
- **Remote API** (optional, FastAPI + uvicorn) — mobile/iOS control via PWA at `http://localhost:8765`
- **Per-platform cookie management** — browse, yt-dlp extraction, or CDP extraction (Brave/Chrome 127+); DPAPI/Fernet at-rest encryption; orphan cleanup on method switch
- **Multi-device Taildrop** file transfer via Tailscale CLI
- **Post-download actions** — convert, send via Taildrop, or delete directly from the download queue

## Architecture

```
domain/          Pure business models (DownloadTask, MediaInfo, enums). No external deps.
app/             Use-cases, EventBus, DownloadService. Orchestration only.
infrastructure/  yt-dlp engine, download manager, config, history. Side effects here.
ui/              CustomTkinter tabs and widgets. Consumes app/service layer only.
utils/           Pure helpers — ffmpeg_locator, deno_locator, helpers, logger,
                 instagram_live_checker, tiktok_live_checker. No omnidl imports.
tests/           pytest unit tests. Mock-only — no real network or subprocess.
```

**Layer rule:** `ui` → `app` → `domain`. `infrastructure` → `domain`. `utils` imported by all. `ui` NEVER imports `infrastructure` directly.

## Setup (Development)

```bash
uv sync --extra dev
```

With Remote API support:

```bash
uv sync --extra api --extra dev
```

## Running

```bash
uv run python main.py
```

## Testing

```bash
uv run pytest --cov=. --cov-report=term-missing
```

Coverage threshold: `fail_under = 80` — must not be lowered.

## Building (Windows / macOS EXE)

Builds are automated via the CI/CD pipeline (`.github/workflows/build.yml`).
Pushing a `v*.*.*` tag triggers PyInstaller builds for Windows and macOS and automatically publishes a GitHub Release with both binaries attached.

To build locally (requires Python 3.13 and the dev dependencies):

```bash
uv sync --extra build --extra dev
# Output: dist\OmniDL\OmniDL.exe  (Windows)
#         dist/OmniDL/OmniDL      (macOS bundle via ditto)
```

Distribute the entire `dist\OmniDL\` folder, not just the `.exe`.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| customtkinter | >=5.2.2 | GUI framework |
| yt-dlp | >=2025.1.1 | Download engine (video / live) |
| gallery-dl | >=1.27.0 | Image/gallery download engine (Instagram photos, Twitter images) |
| Pillow | >=10.3.0 | Thumbnail rendering (patches CVE-2024-28219) |
| requests | >=2.31.0 | HTTP client |
| packaging | >=23.0 | Version utilities |
| platformdirs | >=4.0.0 | Platform-appropriate user-data directories (SEC-3) |
| PySocks | >=1.7.1 | SOCKS4/5 proxy support for yt-dlp |
| playwright | >=1.40 | Facebook Story CDP via `connect_over_cdp()` |
| cryptography | >=41.0.0 | Cookie at-rest encryption (macOS Fernet/AES-128-CBC) |
| keyring | >=24.0.0 | macOS Keychain key storage; Brave/Chrome 127+ App-Bound cookie decrypt |

**Optional — Remote API** (only needed when `api_enabled=True`, included via `uv sync --extra api`):

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.111.0 | ASGI web framework for remote-control API |
| uvicorn[standard] | >=0.29.0 | ASGI server — runs as daemon thread alongside Tkinter |

Dev/build deps are in the `dev` and `build` optional groups in `pyproject.toml` (`uv sync --extra dev`).

## Data Directory

| OS | Path |
|---|---|
| Windows | `%APPDATA%\OmniDL\` |
| macOS | `~/Library/Application Support/OmniDL/` |
| Linux | `~/.local/share/OmniDL/` |

Cookie files are stored in `<data_dir>/cookies/` and encrypted at rest (DPAPI on Windows, Fernet on macOS).
Logs are written to `<data_dir>/logs/omnidl_run.log` with 5 MB rotation, 3 backups.

## CI Pipeline

Two parallel GitHub Actions jobs on every push/PR:

**Job 1 — `test`** (Python 3.11, 3.12, 3.13 matrix):
```
ruff check → mypy → pytest (--cov, fail_under=80)
```

**Job 2 — `security`** (Python 3.13):
```
bandit -ll → pip-audit
```

**Build job** (triggered by `v*.*.*` tags only):
```
PyInstaller (Python 3.13) → GitHub Release
```

## Changelog Highlights

### v17.1.0
- Per-platform cookie security hardening: browse handlers encrypt immediately (BUG BM), clear deletes physical files (BUG BN), all 6 acquisition handlers clean up orphaned files on method switch (BUG BO)
- TikTok VOD format selector: 4-tier watermark-free selector applied to both format_id branches (BUG BP/BS); `remote_components` fixed to list (BUG BQ); format audit logging (BUG BR)

### Post-v17.1.0 (current)
- **BUG BT**: Pause/cancel buttons unconditionally hidden on COMPLETED status regardless of `task.filename`
- **BUG BU**: Audio-only output formats (mp3, m4a, flac, aac, opus, wav, ogg) now correctly use `FFmpegExtractAudio` postprocessor instead of `merge_output_format` — fixes "Postprocessing: Conversion failed!" on YouTube/other sites where native format is webm/opus
- **TikTok Live Monitor**: `LiveMonitorTab` now supports TikTok profile watch (`@username` URLs); no cookie required; `utils/tiktok_live_checker.py` added

### v17.0.0
- `PostDownloadActions` component (convert/send/delete bar after successful download)
- `TaildropService.send_file_to_nodes()` multi-device concurrent send
- Multi-device checkbox picker in `TaildropPanel`
- Remote API (FastAPI + uvicorn, optional)

See `CHANGELOG.md` for full version history.
