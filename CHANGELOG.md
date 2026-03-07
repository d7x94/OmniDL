# Changelog

All notable changes to OmniDL are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v16.0.0 — 2026-03-07

### Security

- **SEC-1 (HIGH) — Path traversal via cookie file** — `yt_dlp_engine.py` now
  validates the cookie file path using exact `Path.parents` containment rather
  than a `str.startswith()` prefix check. The prefix check allowed a sibling
  directory (`/home/user_evil/`) to bypass a guard intended for `/home/user/`.
- **SEC-NEW-1 (HIGH) — Path traversal in `safe_path()`** — `utils/helpers.py`
  had the same `str.startswith()` vulnerability. Replaced with the correct
  `base_r in candidate_r.parents` check (identical to the SEC-1 fix).
- **SEC-2 (HIGH) — Shell injection in `reveal_in_explorer()`** — Removed
  `shell=True` from all `subprocess.Popen` calls. Windows `/select,` flag is
  now passed as a single concatenated argument, not split across argv entries.
- **SEC-3 (HIGH) — User data in executable directory** — Config, history, and
  logs are now stored in the platform-appropriate writable directory via
  `platformdirs` (`%APPDATA%\OmniDL` on Windows, `~/Library/Application
  Support/OmniDL` on macOS, `~/.local/share/OmniDL` on Linux). Prevents
  silent write failures when the application is installed under a read-only
  location such as `C:\Program Files`.
- **SEC-4 (HIGH) — SSRF via thumbnail URL** — `ThumbnailService` now enforces
  a three-layer SSRF defence: scheme allowlist (http/https only), IP-literal
  check blocking all RFC-1918 and loopback ranges, and DNS resolution check
  ensuring every resolved address is globally routable. Hostname bypasses
  such as `localhost`, `192.168.1.1.nip.io`, and `metadata.internal` are
  all blocked.
- **SEC-5 (MEDIUM) — Unsanitised browser name for cookie extraction** —
  `cookies_browser` is validated against a `frozenset` of known yt-dlp browser
  names before being passed to yt-dlp. Arbitrary strings are rejected.
- **SEC-6 (MEDIUM) — yt-dlp extra args injection** — User-supplied extra yt-dlp
  arguments are filtered through a strict key allowlist before being applied.
  Dangerous options such as `--exec` and `--postprocessor-args` are silently
  dropped and logged.

### Architecture

- **ThumbnailService extracted to application layer** — All HTTP fetch logic,
  SSRF validation, PIL decoding, and image resizing previously scattered across
  `ui/tabs/home_tab.py` are now isolated in `app/services/thumbnail_service.py`.
  The UI layer no longer imports `requests`, `PIL`, `socket`, or `ipaddress`.
- **Legacy data migration** — `main.py` performs a one-time migration of
  existing `config.json` and `download_history.jsonl` from the old executable
  directory to the new `DATA_DIR` location on first launch after upgrade.

### Infrastructure

- **CI pipeline** (`ci.yml`) — Runs pytest across Python 3.11, 3.12, and 3.13;
  ruff linting; mypy type checking; bandit SAST scan; pip-audit CVE scan.
  All jobs run on every push and pull request.
- **Build & Release pipeline** (`build.yml`) — PyInstaller builds for
  Windows (`windows-latest`) and macOS (`macos-latest`) are gated behind the
  CI test job. Pushing a `v*.*.*` tag automatically creates a GitHub Release
  and attaches both platform binaries as release assets via
  `softprops/action-gh-release`. Pre-release tags (`v1.0.0-beta.1`) are
  published as GitHub pre-releases automatically.
- **PyInstaller** pinned to 6.19.0. All runtime hidden-imports and
  `collect-all` directives verified. macOS packages use `ditto` to preserve
  resource forks. Unsigned binary warning retained pending code-signing
  certificate acquisition.

### Stability

- **Atomic config and history writes** — `ConfigManager` and
  `HistoryRepository` both write to a temporary file then atomically rename
  it over the target, preventing data loss on power failure or disk full.
- **Debounced config saves** — Rapid successive `set()` calls (e.g. slider
  drag events) are coalesced into a single disk write 500 ms after the last
  call.
- **Append-only history store** — `HistoryRepository` switched from a full
  JSON-array rewrite on every completion to an O(1) JSONL append. Full
  rewrites now occur only when the entry limit is exceeded or entries are
  removed. Eliminates write contention under concurrent downloads.
- **Task locking** — `DownloadManager._run_task()` wraps all terminal-state
  transitions (`COMPLETED`, `CANCELLED`, `FAILED`) in the task's `RLock` to
  prevent the UI poll thread from observing half-updated task state.
- **Livestream cancel latency** — `fragment_retries` set to 0 for live streams
  so that cancel requests propagate within the socket timeout window (10 s)
  rather than after all fragment retries (previously up to 90 s).

### Dependencies

- `yt-dlp` minimum raised from `>=2024.1.1` to `>=2025.1.1`.
- `Pillow` minimum raised from `>=10.2.0` to `>=10.3.0` (patches
  CVE-2024-28219, ImageMath buffer overflow).
- `platformdirs>=4.0.0` added as a runtime dependency.
- `pyinstaller==6.19.0` moved from `requirements.txt` to
  `requirements-dev.txt` (not a runtime dependency).
