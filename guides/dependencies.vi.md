# Dependencies

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

**Tùy chọn — Remote API** (chỉ cần khi `api_enabled=True`, qua `uv sync --extra api`):

| Package | Version | Purpose |
|---|---|---|
| fastapi | >=0.111.0 | ASGI web framework for the remote-control API |
| uvicorn[standard] | >=0.29.0 | ASGI server — runs as a daemon thread alongside the Qt event loop |

Dependency cho dev nằm trong dependency group `dev`, cho build nằm trong extra `build` của `pyproject.toml` (`uv sync --group dev`, `uv sync --extra build`).
