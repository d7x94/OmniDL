# OmniDL v16

A desktop video downloader supporting YouTube, TikTok, Instagram, Twitter/X, Facebook, and 1000+ sites — built with Python, CustomTkinter, and yt-dlp.

## Architecture

```
domain/          Pure business models (DownloadTask, MediaInfo, enums). No external deps.
app/             Use-cases, EventBus, DownloadService. Orchestration only.
infrastructure/  yt-dlp engine, download manager, config, history. Side effects here.
ui/              CustomTkinter tabs and widgets. Consumes app/service layer.
utils/           Pure helpers — no omnidl imports.
tests/           pytest unit tests. Mock-only.
```

## Setup (Development)

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Running

```bash
python main.py
```

## Testing

```bash
pytest --cov=. --cov-report=term-missing
```

## Building (Windows EXE)

```bash
cd build
build_windows.bat
# Output: dist\OmniDL\OmniDL.exe
```

Distribute the entire `dist\OmniDL\` folder, not just the `.exe`.

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| customtkinter | ==5.2.2 | GUI framework |
| yt-dlp | >=2024.1.1 | Download engine |
| Pillow | >=10.2.0 | Thumbnail rendering |
| requests | >=2.31.0 | HTTP client |
| packaging | >=23.0 | Version utilities |

Dev/build deps live in `requirements-dev.txt` (pytest, pytest-cov, pyinstaller).
