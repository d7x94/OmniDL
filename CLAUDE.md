# CLAUDE.md — OmniDL Codebase Guide

This file is the authoritative reference for AI assistants working in this repository.

---

## Project Overview

**OmniDL** is a Python desktop application (v17.1.0) for downloading videos, images, and live streams from hundreds of platforms. It wraps `yt-dlp` (video/live) and `gallery-dl` (images/galleries) behind a CustomTkinter GUI, with an optional FastAPI remote-control server.

---

## Architecture

OmniDL follows **Clean Architecture** with strict layer separation:

```
domain/          ← Pure business models, no external imports
app/             ← Use cases and orchestration services
infrastructure/  ← Side effects: downloaders, storage, config
ui/              ← CustomTkinter GUI (depends on app layer)
api/             ← Optional FastAPI remote-control server
utils/           ← Pure helpers, no omnidl package imports
main.py          ← Entry point: wires everything together
```

**Dependency rule:** Each layer may only import from layers below it. `domain` has zero external dependencies. `utils` has no imports from any other omnidl package.

### Event-driven decoupling

`app/event_bus.py` — a thread-safe pub/sub bus — is the communication backbone between the download engines and the UI. Never bypass it by calling UI methods directly from infrastructure.

---

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point; startup sequence, wires all layers |
| `utils/__version__.py` | Single source of truth for version (`17.1.0`) |
| `domain/models/download_task.py` | Core `DownloadTask` and `MediaInfo` models |
| `domain/enums/download_status.py` | `DownloadStatus` enum (state machine) |
| `app/event_bus.py` | Thread-safe pub-sub (`EventBus`) |
| `app/services/download_service.py` | Main orchestrator: analyse, start, pause, cancel |
| `infrastructure/config/config_manager.py` | JSON-backed, RLock-protected `ConfigManager` |
| `infrastructure/downloader/download_manager.py` | `ThreadPoolExecutor` concurrency orchestrator |
| `infrastructure/downloader/yt_dlp_engine.py` | `YtDlpEngine` — wraps yt-dlp |
| `infrastructure/downloader/gallery_dl_engine.py` | `GalleryDlEngine` — wraps gallery-dl |
| `infrastructure/downloader/facebook_story_engine.py` | `FacebookStoryEngine` — Playwright CDP |
| `infrastructure/storage/history_repository.py` | JSONL-backed append-only history |
| `ui/main_window.py` | Root CustomTkinter window |
| `api/server.py` | FastAPI server with SSE progress and token auth |
| `utils/helpers.py` | URL validation, `safe_path()` (CWE-22 guard) |
| `config.json` | Default/template configuration |

---

## Running the App

```bash
python main.py
```

Data is stored in platform-appropriate directories (`platformdirs`):
- **Windows:** `%APPDATA%\OmniDL\`
- **macOS:** `~/Library/Application Support/OmniDL/`
- **Linux:** `~/.local/share/OmniDL/`

---

## Development Workflow

### Install dependencies

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
# Optional: API server support
pip install -r requirements-api.txt
```

### Run tests

```bash
# All headless tests (excludes tests requiring a display)
pytest --ignore=tests/test_e2e.py --ignore=tests/test_logger_and_manager_extra.py

# With coverage
pytest --cov=. --cov-report=term-missing \
       --ignore=tests/test_e2e.py \
       --ignore=tests/test_logger_and_manager_extra.py

# Single module
pytest tests/test_download_task.py -v
```

Coverage must stay at **80% or above** (`fail_under = 80` in `setup.cfg`). The `ui/`, `api/`, `main.py`, and gallery/facebook engine files are excluded from coverage measurement.

### Lint and type-check

```bash
ruff check . --select=E,F,B,I   # linting
mypy domain/ app/ infrastructure/ utils/   # type checking
```

Both must pass clean before a PR can merge (enforced by CI).

---

## CI/CD

### `ci.yml` (every push and PR)
1. Matrix: Ubuntu, Python 3.11 / 3.12 / 3.13
2. `ruff check` — style/lint
3. `mypy` — type safety (domain, app, infrastructure, utils)
4. `pytest` — unit tests (no E2E)
5. `bandit` SAST — severity ≥ MEDIUM fails the build
6. `pip-audit` CVE scan

### `build.yml` (main branch, version tags, manual dispatch)
1. Test gate (same as ci.yml)
2. PyInstaller build for Windows (ffmpeg downloaded at build time from gyan.dev)
3. PyInstaller build for macOS (ffmpeg from evermeet.cx)
4. GitHub Release created automatically on `v*.*.*` tags

**FFmpeg is never stored in the repository.** It is downloaded by CI during builds and bundled into the PyInstaller output. At runtime, `utils/ffmpeg_locator.get_ffmpeg_path()` resolves it.

---

## Code Conventions

### Imports

```python
from __future__ import annotations  # always first

import stdlib_module               # stdlib block

import third_party_package         # third-party block

from app.services import SomeService    # first-party blocks in layer order:
from domain.models import DownloadTask  #   api, app, domain, infrastructure, ui, utils
```

Enforced by Ruff (`isort` rules). First-party packages are declared in `pyproject.toml`:
`api`, `app`, `domain`, `infrastructure`, `ui`, `utils`.

### Naming

| Kind | Convention | Example |
|------|-----------|---------|
| Classes | PascalCase | `DownloadTask`, `EventBus` |
| Functions/methods | snake_case | `start_download()` |
| Constants | UPPER_CASE | `MAX_WORKERS` |
| Private | leading underscore | `_lock`, `_check_deps()` |

### Type hints

- Full type hints everywhere; enforced by mypy.
- `from __future__ import annotations` for forward references.
- Use `TYPE_CHECKING` blocks to break circular imports.

### Docstrings

```python
"""One-line summary.

Longer explanation if needed:
  - Point 1
  - Point 2
"""
```

### Threading

- `threading.Lock()` / `threading.RLock()` for mutable shared state.
- `ConfigManager` and `EventBus` use `RLock` (re-entrant safe).
- `DownloadManager` uses `ThreadPoolExecutor`; worker count = `config.max_concurrent`.
- Never call UI methods from worker threads — publish events on `EventBus` instead.

### Logging

```python
logger = logging.getLogger(__name__)
```

- One logger per module, named by `__name__`.
- Debug logging is off by default; enabled via `config.debug_logging = True`.
- Noisy third-party loggers (`PIL`, `urllib3`, `yt_dlp`) are silenced at startup.
- `RotatingFileHandler`: 5 MB per file, 3 backups.

### Security rules (do not break these)

- `utils/helpers.py:safe_path()` — **always** use this before writing any user-supplied path (prevents CWE-22 path traversal).
- `api/server.py` — token comparison uses `secrets.compare_digest()` (prevents timing oracles).
- Cookie data is Fernet-encrypted via `infrastructure/downloader/cookie_storage.py`.
- URL scheme and browser-name inputs are allowlisted; do not widen these lists without review.

---

## Configuration Schema (`config.json`)

Key settings an AI assistant may need to reference:

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `download_dir` | str\|null | null | null → OS Downloads folder |
| `max_concurrent` | int | 3 | ThreadPoolExecutor workers |
| `max_retries` | int | 3 | Per-download retry limit |
| `default_quality` | str | `"bestvideo+bestaudio/best"` | yt-dlp format selector |
| `default_format` | str | `"mp4"` | Output container |
| `use_cookies` | bool | false | Enable cookie injection |
| `cookies_browser` | str | `"chrome"` | Allowlisted browser names |
| `api_enabled` | bool | false | Start FastAPI server |
| `api_port` | int | 7799 | API server port |
| `api_token` | str | `""` | Bearer token for API auth |
| `taildrop_enabled` | bool | false | Tailscale file transfer |
| `debug_logging` | bool | false | Verbose log output |

---

## Version Management

The single source of truth is `utils/__version__.py`:

```python
__version__ = "17.1.0"
```

Update **only this file** when bumping the version. CI/build pipelines read from it.

---

## Adding a New Download Engine

1. Create `infrastructure/downloader/my_engine.py` implementing `analyse_url()` and a download method.
2. Instantiate it in `main.py` and pass it to `DownloadService`.
3. Add URL-routing logic in `app/services/download_service.py`.
4. Write tests in `tests/test_my_engine.py` (mock subprocess/network calls).
5. Add the new dependency to `requirements.txt` and update `requirements.lock`.

---

## Adding a New UI Tab

1. Create `ui/tabs/my_tab.py` subclassing `ctk.CTkFrame`.
2. Register it in `ui/main_window.py` tab list.
3. Keep all business logic out of the tab — delegate to `DownloadService` or other app-layer services.

---

## Testing Guidelines

- **Unit tests only** for CI — no network calls, no display required.
- Mock all external processes (`subprocess`, `yt-dlp`, `gallery-dl`) with `unittest.mock`.
- Test file naming: `tests/test_<module_name>.py`.
- E2E tests live in `tests/test_e2e.py` and are always excluded from CI (require a display).
- `tests/conftest.py` configures sys.path and silences background-thread I/O noise.

---

## Common Pitfalls

- **Do not** import `ui` from `domain`, `app`, or `infrastructure`.
- **Do not** call `yt-dlp` or `gallery-dl` directly — always go through the engine classes.
- **Do not** write to `config.json` directly — use `ConfigManager.set()`.
- **Do not** store sensitive data (cookies, tokens) in plain text — use `cookie_storage.py`.
- **Do not** perform blocking operations on the Tkinter main thread — dispatch via `EventBus` or `after()`.
- **Do not** hard-code file paths — use `DATA_DIR` / `LOG_DIR` from `main.py` or `platformdirs`.
