English | [Tiếng Việt](README.vi.md) | [简体中文](README.zh-CN.md)

# OmniDL v20.3.29

A desktop media downloader for YouTube, TikTok, Instagram, Twitter/X, Facebook, and 1000+ sites — built with Python, PySide6, yt-dlp, and gallery-dl.

## Features

- **Download** video, audio, and images from 1000+ platforms via yt-dlp and gallery-dl
- **Batch download** — paste multiple URLs; per-platform delays avoid rate-limiting
- **Special downloads** — Facebook Story and Instagram Live (via CDP, the browser debugging interface)
- **Facebook photos, albums and posts** — photo posts, albums and mixed photo+video posts, one folder per post. Suggested and sponsored videos are filtered out
- **Live stream monitor** — auto-record when a stream goes live (Instagram and Facebook need a cookie, TikTok does not)
- **Convert** downloaded files to MP4, MP3, MKV, AVI with FFmpeg. Hardware encoders (NVENC/QSV/AMF/VideoToolbox) are probed automatically, with a CPU fallback always available
- **Video editor** — preview, trim, rotate, mute downloaded files
- **Archive** — compress files to 7z/ZIP (optional password), extract existing archives
- **Documents** — convert Markdown ↔ PDF, HTML ↔ PDF and Office ↔ PDF (desktop tab + Remote API); see [Document conversion](guides/document-conversion.md)
- **TikTok account pool** — use several TikTok cookies, one per browser profile, each health-checked
- **Multi-language UI** — English, Vietnamese and Chinese, in the desktop app and the web app
- **Cookie management** per platform — import from browser, yt-dlp or CDP (Brave/Chrome 127+); encrypted at rest
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
uv sync --group dev
```

With Remote API support:

```bash
uv sync --extra api --group dev
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

Keep the API token secret — anyone with it can control OmniDL and read your download history. Don't expose port 8765 to the public internet; use [Tailscale](guides/remote-iphone.md) to reach it securely from outside your LAN.

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
uv sync --group dev
uv run python main.py
uv run pytest --cov=. --cov-report=term-missing
```

Coverage threshold: `fail_under = 80` — must not be lowered.

CI runs on every push/PR: `ruff check → mypy → pytest` (Python 3.11/3.12/3.13) plus a parallel `bandit -ll → pip-audit` security job.

### Building

Release builds run automatically in CI (`.github/workflows/build.yml`) when a `v*.*.*` tag is pushed: PyInstaller builds for Windows and macOS, plus a Linux tarball, all published to a GitHub Release.

To build locally (Python 3.13):

```bash
uv sync --extra build --group dev
# Windows: dist\OmniDL\OmniDL.exe
# macOS:   dist/OmniDL/OmniDL
```

Distribute the entire `dist/OmniDL/` folder, not just the executable.

## License

[MIT](LICENSE)

## More docs

- [Remote control from iPhone (Remote API + Taildrop)](guides/remote-iphone.md)
- [Document conversion](guides/document-conversion.md)
- [Remote API endpoints](guides/api-endpoints.md)
- [Dependencies](guides/dependencies.md)

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

## Changelog

Full version history, including per-bug-fix notes, lives in [`CHANGELOG.md`](CHANGELOG.md).
