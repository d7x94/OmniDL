English | [Tiếng Việt](README.vi.md) | [简体中文](README.zh-CN.md)

# OmniDL v20.3.8

A desktop media downloader for YouTube, TikTok, Instagram, Twitter/X, Facebook, and 1000+ sites — built with Python, PySide6, yt-dlp, and gallery-dl.

## Features

- **Download** video, audio, and images from 1000+ platforms via yt-dlp and gallery-dl
- **Batch download** — paste multiple URLs; per-platform delays avoid rate-limiting
- **Special downloads** — Facebook Story, Instagram Live via CDP (Chrome DevTools Protocol, the browser debugging interface Playwright drives)
- **Facebook photos, albums and posts** — photo posts, albums and mixed photo+video feed posts download through gallery-dl and yt-dlp together, each post landing in its own folder; suggested/sponsored videos Facebook injects into a post page are filtered out so a photo post is never delivered as somebody else's advert
- **Live stream monitor** — auto-record when a stream goes live; watch Instagram profiles (needs cookie), TikTok profiles (no cookie needed), or Facebook pages/profiles (needs cookie)
- **Convert** downloaded files to MP4, MP3, MKV, AVI with FFmpeg. Hardware encoders (NVENC/QSV/AMF/VideoToolbox) are probed automatically, with a CPU fallback always available
- **Video editor** — preview, trim, rotate, mute downloaded files
- **Archive** — compress files to 7z/ZIP (optional password), extract existing archives
- **Documents** — convert Markdown ↔ PDF, HTML ↔ PDF and Office ↔ PDF (desktop tab + Remote API); see [Document conversion](#document-conversion)
- **TikTok account pool** — add several TikTok cookies, one per browser profile; each is health-checked before it is accepted, and can be renamed or refreshed in place
- **Multi-language UI** — English, Vietnamese and Chinese, in both the desktop app and the PWA (Progressive Web App — the Remote UI installed like a native app); engine and download errors are translated too
- **Cookie management** per platform — browser import, yt-dlp extraction, or CDP extraction (Brave/Chrome 127+); encrypted at rest; orphan cleanup on method switch
- **Taildrop file transfer** — send files to iPhone or any Tailscale node, multi-device
- **Post-download actions** — convert, send via Taildrop, or delete directly from the queue
- **Remote API** (optional) — control OmniDL from iPhone or any browser on the network via a built-in PWA

## System Requirements

- Python 3.11 or later
- Windows, macOS, or Linux
- FFmpeg is bundled in release builds; a source checkout needs FFmpeg on `PATH` for Convert/Editor/Documents features
- [Tailscale](https://tailscale.com) — only if using the Remote API from outside your own machine

## Installation

```bash
uv sync --extra dev
```

With Remote API support:

```bash
uv sync --extra api --extra dev
```

## Quick Start

### Desktop

```bash
uv run python main.py
```

### Remote UI / Web API

1. Open **Settings → Remote API** in the desktop app and turn it on. OmniDL generates an API token and stores it in the system keyring (never in a config file).
2. The server starts on `http://0.0.0.0:8765`. Every request needs the `X-API-Token` header.
3. Open `http://<computer-name-or-IP>:8765` in a browser on the same network and enter the token.

Keep the API token secret — anyone with it can control OmniDL and read your download history. Don't expose port 8765 to the public internet; use [Tailscale](#remote-control-from-iphone-remote-api--taildrop) to reach it securely from outside your LAN.

## Basic Configuration

Settings are edited from the desktop **Settings** tab (download folder, max concurrent downloads, retries, theme, language, cookie files, Remote API, Taildrop). They are stored in `config.json` inside the [Data Directory](#data-directory) — the file is written automatically by the app and is not meant to be hand-edited.

Key settings:

| Setting | Where | Purpose |
|---|---|---|
| Download folder | Settings → File Location | Where finished downloads are saved |
| Max concurrent downloads | Settings → Download Behaviour | How many downloads run in parallel |
| UI language | Settings → Appearance | `en` / `vi` / `zh` |
| Cookie files | Settings → Network | Per-platform cookies for login-gated content |
| Remote API | Settings → Remote API | Enable/disable the remote-control server and token |

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| Safari/browser can't load `http://my-laptop:8765` | Tailscale off, or devices on different accounts | Check the Tailscale app, confirm same tailnet |
| "Unauthorized" in the Remote App | Wrong or missing token | Settings → Remote API → copy token, re-enter it |
| iPhone missing from Detect nodes | iPhone's Tailscale is off or offline | Open Tailscale on iPhone, confirm connected |
| Taildrop fails with `400 Bad Request` | Unusual characters in filename | OmniDL sends the full Unicode name first and retries once with an ASCII transliteration; if it still fails, update Tailscale on iPhone |
| Taildrop fails with `502 Bad Gateway` | iPhone unreachable (Tailscale asleep or offline) | Open Tailscale on the iPhone, then send the file again; OmniDL does not retry this error automatically |
| File sent but no notification | iOS notifications disabled for Tailscale | iPhone Settings → Notifications → Tailscale → allow |
| Remote App slow / SSE (Server-Sent Events, used for live progress) drops | Unstable Tailscale connection | Switch to relay mode in Tailscale settings |

## Directory Structure

```
domain/          Pure business models (DownloadTask, MediaInfo, enums). No external deps.
app/             Use-cases, EventBus, DownloadService. Orchestration only.
infrastructure/  yt-dlp engine, download manager, account_pool, config, history.
ui/              PySide6 tabs and widgets. Consumes app/service layer only.
utils/           Pure helpers — ffmpeg_locator, logger, live checkers, tiktok_detection/.
tests/           pytest unit tests. Mock-only — no real network or subprocess.
```

Dependency direction: `ui -> app -> domain`, `infrastructure -> domain`, `utils` usable everywhere.

## Development and Testing

```bash
uv sync --extra dev
uv run python main.py
uv run pytest --cov=. --cov-report=term-missing
```

Coverage threshold: `fail_under = 80` — must not be lowered.

CI runs on every push/PR: `ruff check → mypy → pytest` (Python 3.11/3.12/3.13) plus a parallel `bandit -ll → pip-audit` security job.

### Building

Release builds run automatically in CI (`.github/workflows/build.yml`) when a `v*.*.*` tag is pushed: PyInstaller builds for Windows and macOS, plus a Linux tarball, all published to a GitHub Release.

To build locally (Python 3.13):

```bash
uv sync --extra build --extra dev
# Windows: dist\OmniDL\OmniDL.exe
# macOS:   dist/OmniDL/OmniDL
```

Distribute the entire `dist/OmniDL/` folder, not just the executable.

## License

[MIT](LICENSE)

---

## Remote control from iPhone (Remote API + Taildrop)

Control OmniDL from an iPhone, and send finished downloads straight to it, over Tailscale. The two features work independently but pair well together.

**Requirements**

- Tailscale installed on both computer and iPhone — [tailscale.com](https://tailscale.com)
- Both devices signed into the same Tailscale account (same tailnet)
- Remote API extra installed: `uv sync --extra api --extra dev`

### 1. Connect both devices with Tailscale

Install Tailscale on both devices, sign in with the same account, and confirm the computer shows up in the iPhone's Tailscale app. Note the computer's MagicDNS name (Tailscale's automatic hostname for a device, e.g. `my-laptop`) — it stays stable even if the IP changes.

### 2. Enable the Remote API in OmniDL

Open **Settings → Remote API** and turn it on. OmniDL generates a random API token and stores it in the system keyring (Windows Credential Manager / macOS Keychain, never in a config file). The server starts on `http://0.0.0.0:8765`, listening on all interfaces including Tailscale.

Every request needs the `X-API-Token` header. Don't expose port 8765 to the public internet without Tailscale.

### 3. Open the app on iPhone

In Safari, go to `http://<computer-name>:8765` (e.g. `http://my-laptop:8765`) and enter the API token when asked. Use Share → "Add to Home Screen" to install it like a native app.

If it won't connect: confirm Tailscale is on for both devices, or try the Tailscale IP instead of the name (e.g. `http://100.64.0.5:8765`).

| Feature | What it does |
|---|---|
| Paste URL + Analyse | Analyze a link, pick format/quality |
| Download | Start a download, watch progress live |
| Queue | View all tasks, pause/resume/cancel |
| Convert | Convert a downloaded file to an iPhone-friendly MP4 |
| Preview | View video/image in-browser |
| Send (Taildrop) | Send the file to the iPhone over Tailscale |
| Delete | Remove the file from the computer |

### 4. Set up Taildrop to receive files

Taildrop sends files peer-to-peer between Tailscale devices — no cloud involved.

In **Settings → Taildrop**, enable it, click **Detect nodes** to find online Tailscale devices, and check your iPhone in the list (you can select more than one). Choose a send mode:

- **Ask** (default) — files are sent only when you press "Send" manually
- **Always** — every finished download is sent automatically

On the iPhone, a Taildrop notification appears for each incoming file; tap it, choose **Save**, and it lands in the Files app. File names with emoji or special characters are sanitized automatically before sending.

You can also trigger a manual send from the desktop Queue tab (**Send** button) or from the Remote App queue.

### Typical flow

```
iPhone (Safari/PWA)                     Computer (OmniDL)
──────────────────────────────────────────────────────────
1. Open Remote App, paste a TikTok link
2. Tap Analyse                     →   Analyzes the URL
3. Pick MP4, tap Download          →   Starts downloading
4. Watch live progress             ←   SSE events
5. Tap Convert                     →   FFmpeg converts to iPhone MP4
6. Tap Send                        →   Tailscale sends the file
7. Notification appears            ←   File arrives
8. Tap Save in Files app
9. Tap Delete in Remote App        →   File removed from the computer
```

### Start automatically on Windows login

Create a shortcut to `OmniDL.exe`, press `Win + R`, type `shell:startup`, and drop the shortcut into that folder. OmniDL will launch with Windows and the Remote API / Taildrop will be ready if they were enabled before.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| pyside6 | >=6.7 | GUI framework |
| yt-dlp | >=2026.7.4 | Download engine (video / live) |
| gallery-dl | >=1.32.12 | Image/gallery download engine (Instagram photos, Facebook photos/albums, Twitter images) |
| requests | >=2.31.0 | HTTP client |
| packaging | >=23.0 | Version utilities |
| playwright | >=1.40 | Facebook Story + Instagram Live CDP via `connect_over_cdp()` |
| cryptography | >=41.0.0 | Cookie at-rest encryption (macOS Fernet/AES-128-CBC) |
| keyring | >=24.0.0 | macOS Keychain key storage; Brave/Chrome 127+ App-Bound cookie decrypt |
| Pillow | >=10.3.0 | Thumbnail decoding/resizing, frame effects |
| platformdirs | >=4.0.0 | Platform-appropriate data directory resolution |
| PySocks | >=1.7.1 | SOCKS proxy support for yt-dlp/gallery-dl |
| curl-cffi | >=0.16.0 | Chrome TLS impersonation for TikTok/Kuaishou requests |
| loguru | >=0.7.3 | Application logging |
| ffmpeg-python | >=0.2.0 | FFmpeg command construction |
| py7zr | >=1.1.3 | 7z archive compression/extraction |
| pyzipper | >=0.4.0 | Password-protected ZIP compression/extraction |
| weasyprint | >=69.0 | HTML → PDF rendering for the Documents tab |
| markdown | >=3.6 | Markdown → HTML for the Documents tab |
| pypdf | >=4.2.0 | PDF text extraction (PDF → Markdown/HTML) |

**Optional — Remote API** (needed only when `api_enabled=True`, via `uv sync --extra api`):

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.111.0 | ASGI web framework for the remote-control API |
| uvicorn[standard] | >=0.29.0 | ASGI server — runs as a daemon thread alongside the Qt event loop |

Dev/build deps live in the `dev` and `build` optional groups in `pyproject.toml` (`uv sync --extra dev`).

## Document conversion

The **Documents** tab (and `POST /api/docs/convert`) converts between document
formats. Seven routes are supported:

| From | To | Back-end |
|---|---|---|
| Markdown (`.md`, `.markdown`, …) | PDF | `markdown` + WeasyPrint |
| Markdown | HTML | `markdown` |
| HTML (`.html`, `.htm`, `.xhtml`) | PDF | WeasyPrint |
| PDF | Markdown | `pypdf` |
| PDF | HTML | `pypdf` |
| PDF | DOCX | LibreOffice |
| Office (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.rtf`, `.csv`, …) | PDF | LibreOffice |

Two back-ends are **optional**, and OmniDL degrades gracefully without them —
`GET /api/docs/capabilities` reports what the host can do, the desktop tab greys
out the rest, and the API answers `503` with a plain-language reason:

- **LibreOffice** — required for every Office route. Not bundled (it is a ~700 MB
  suite); install it from [libreoffice.org](https://www.libreoffice.org/) and the
  tab's **Re-check** button picks it up without restarting OmniDL.
- **Pango / GTK** — WeasyPrint loads Pango, HarfBuzz and fontconfig at import time.
  Linux and macOS package managers ship these; on **Windows** install the
  [GTK for Windows Runtime](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer)
  to enable Markdown/HTML → PDF.

**Scanned PDFs are not supported.** PDF → Markdown/HTML extracts the text layer;
a PDF with no selectable text fails with a clear message rather than producing an
empty file. There is no OCR.

**Security.** A document can reference remote or arbitrary local resources through
`<img>`, `<link>`, `@import` or a CSS `url()`. The renderer allows only `data:`
URIs and files inside the source document's own folder; anything else is refused,
logged, and skipped (the page still renders without it). Over the Remote API both
`source_path` and `out_dir` are confined to `download_dir`, sources over 200 MiB
are rejected, and concurrency is bounded to 2.

## Data Directory

| OS | Path |
|---|---|
| Windows | `%APPDATA%\OmniDL\` |
| macOS | `~/Library/Application Support/OmniDL/` |
| Linux | `~/.local/share/OmniDL/` |

Cookie files live in `<data_dir>/cookies/` and are encrypted at rest (DPAPI, Windows' built-in per-user encryption, on Windows; Fernet on macOS).
Logs go to the platform log directory (`%LOCALAPPDATA%\OmniDL\Logs\` on Windows,
`~/Library/Logs/OmniDL/` on macOS, `~/.local/state/OmniDL/log/` on Linux):
`omnidl.log` at INFO always, plus `omnidl_debug.log` at DEBUG when *Verbose logging*
is on in Settings. Both rotate at 5 MB with 3 files kept.

## Remote API Endpoints

When `api_enabled=True`, the server runs at `http://0.0.0.0:8765`. Every request needs the `X-API-Token` header.

| Method | Path | Description |
|---|---|---|
| GET | `/api/ping` | Health check — returns the app version and the `facebook_story` capability flag (Story capture needs a local Brave/Chrome, so it is `true` only on Windows and macOS) |
| POST | `/api/analyse` | Analyze a URL, return MediaInfo |
| GET | `/api/analyse/stream` | Analyze a URL over SSE (live progress) |
| POST | `/api/clipboard/analyse` | Analyze the URL currently on the clipboard |
| POST | `/api/download` | Start a download |
| POST | `/api/download/batch` | Enqueue up to 500 downloads in one call (Batch tab) |
| GET | `/api/queue` | List all tasks |
| GET | `/api/queue/{task_id}` | Get one task's status |
| POST | `/api/queue/{task_id}/pause` | Pause a task |
| POST | `/api/queue/{task_id}/resume` | Resume a task |
| POST | `/api/queue/{task_id}/cancel` | Cancel a task |
| DELETE | `/api/queue/items` | Remove tasks from the queue by id |
| DELETE | `/api/queue/finished` | Clear completed/failed tasks from the queue |
| POST | `/api/queue/{task_id}/transfer` | Send a file via Taildrop |
| POST | `/api/queue/{task_id}/rename` | Rename the output file of a completed task |
| DELETE | `/api/queue/{task_id}/file` | Delete a file from disk |
| GET | `/api/queue/{task_id}/file` | Stream/preview a file (Range requests) |
| GET | `/api/queue/{task_id}/fileinfo` | File metadata (name, size, existence) |
| GET | `/api/convert/encoders` | List available GPU/CPU encoders |
| GET | `/api/convert/codecs` | List output codecs + subtitle support this FFmpeg build has |
| GET | `/api/convert/concurrency` | Current parallel-conversion limit and the server maximum |
| POST | `/api/convert/concurrency` | Set how many conversions run at once (1-8, persisted) |
| POST | `/api/queue/{task_id}/convert` | Start a convert job |
| POST | `/api/queue/{task_id}/subtitles` | Generate subtitles (.srt) only, no re-encode |
| GET | `/api/convert/{job_id}` | Get a convert job's status |
| POST | `/api/convert/{job_id}/cancel` | Cancel a convert job |
| GET | `/api/convert/{job_id}/file` | Stream a converted file (iOS Safari); `?kind=srt` for the subtitle sidecar |
| DELETE | `/api/convert/{job_id}/file` | Delete a converted file |
| GET | `/api/history/stats` | Download history stats |
| GET | `/api/history` | Download history |
| DELETE | `/api/history/{task_id}` | Delete one history entry |
| GET | `/api/files/browse` | Browse files/folders on the computer |
| POST | `/api/files/convert` | Convert an arbitrary file (outside the queue) |
| POST | `/api/files/convert/batch` | Queue up to 100 files with one set of settings |
| POST | `/api/files/subtitles` | Generate subtitles (.srt) for an arbitrary file |
| DELETE | `/api/files/delete` | Delete a file by path |
| GET | `/api/files/serve` | Stream a file by absolute path |
| POST | `/api/files/transfer` | Send a file by path via Taildrop |
| GET | `/api/nodes` | List configured Taildrop nodes |
| POST | `/api/archive/compress` | Compress files to 7z/ZIP (optional password) |
| POST | `/api/archive/extract` | Extract an archive |
| POST | `/api/archive/contents` | Preview an archive's contents before extracting |
| GET | `/api/docs/capabilities` | Report which document-conversion back-ends the host has |
| POST | `/api/docs/convert` | Convert a document (Markdown/HTML/Office ↔ PDF) |
| GET | `/api/monitor` | List watched live streams |
| POST | `/api/monitor` | Add a profile/URL to the live monitor (TikTok, Instagram, Facebook page/profile, or a direct live URL) |
| POST | `/api/monitor/interval` | Set the live-check interval |
| DELETE | `/api/monitor/{item_id}` | Stop watching |
| POST | `/api/monitor/{item_id}/cancel` | Stop recording, keep watching |
| POST | `/api/monitor/{item_id}/pause` | Pause live checks for one link |
| POST | `/api/monitor/{item_id}/resume` | Resume live checks for one link |
| POST | `/api/monitor/{item_id}/check-now` | Check live status immediately |
| POST | `/api/monitor/pause` | Pause the whole live monitor |
| POST | `/api/monitor/resume` | Resume the whole live monitor |
| GET | `/api/settings/language` | Current UI language + supported languages |
| POST | `/api/settings/language` | Set the UI language (`en` / `vi` / `zh`) |
| GET | `/api/events` | SSE stream — real-time progress/status |
| GET | `/` | PWA (iPhone web app) |

## Changelog

Full version history, including per-bug-fix notes, lives in [`CHANGELOG.md`](CHANGELOG.md).
