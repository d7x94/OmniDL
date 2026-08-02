# Changelog

All notable changes to OmniDL are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased — 2026-06-22 to 2026-08-02

### Added

- **Archive feature** (`app/services/archive_service.py`, `ui/tabs/archive_tab.py`) — compress files to password-protected `.zip`/`.7z`, extract and preview existing archives. Guards against zip-slip/7z-slip and decompression bombs; stages extraction before committing to disk. Remote API endpoints at `POST /api/archive/{compress,extract,contents}`.
- **`waaw_engine.py`** — new downloader for waaw.ac, reusing the CDP-interception pattern from `facebook_story_engine.py`.
- **`instagram_cdn_engine.py` / `utils/instagram_http.py`** — download a pasted, pre-signed Instagram/Facebook CDN URL anonymously, sharing one client identity with the Instagram live checker/engine.
- **`utils/memtrace.py`** — opt-in memory sampler (`OMNIDL_MEMTRACE=1`), no-op by default.
- **`app/services/live_monitor_service.py`** — headless port of the Live Monitor tab for the Remote API.
- **TikTok live detection, pass 4** (`utils/tiktok_detection/strategies/pass4_api_live_room.py`) — a `/api-live/user/room/` strategy added after passes 0-3 were all defeated by the same anti-bot change at once.

### Fixed

- **Archive tab** — password-toggle buttons were invisible (missing fixed size/stylesheet); "use original name" checkbox now actually disables the archive-name field instead of being ignored.
- **waaw.ac** — CDN-URL capture hardened against obfuscated links and a race where concurrent CDP mutation corrupted request-ID tracking.
- **Linux build** — FFmpeg download switched off johnvansickle.com, which stopped serving a valid tarball.
- Facebook Story's CMD-window flash is fully resolved — all 5 subprocess calls now pass `creationflags=_WIN_NO_WINDOW`.
- Live recording's final output filename corrected across all platforms.

### Testing

- Raised test coverage back above the CI's 80% gate; suppressed a bandit B108 false positive on a `/tmp` test fixture.

---

## v19.0.0 — 2026-05-31

### Changed

- **PySide6 migration** — GUI rewritten from CustomTkinter to PySide6 (Qt6), with a linear design language and QSS dark/light theming.
- **TikTok detection refactor** (`utils/tiktok_detection/`) — 4-pass strategy dispatcher runs concurrently; first successful result wins.
- **Vietnamese localization** — the Remote App (`api/static/index.html`) UI is fully translated to Vietnamese.

### Added

- **TikTok account pool** (`infrastructure/downloader/account_pool.py`) — thread-safe multi-account pool, picks the least-loaded account per download.
- **Network panel** (`ui/tabs/settings/network_panel.py`) — proxy configuration for yt-dlp and gallery-dl.

### Fixed

- **BUG-TT-25/26** — `tiktok_room_id` now forwarded through the Remote API so fallback strategies can use it.
- **Rate limiter** — added a per-request delay to the TikTok Live checker to avoid HTTP 429.

### Performance

- **Cookie cache** — decrypted cookie kept in memory after first read, avoiding repeated Credential Manager calls.

---

## v18.0.0 — 2026-04-30

### Added

- **`utils/__version__.py`** — single source of truth for the app version string.
- **Instagram Live recorder** (`infrastructure/downloader/instagram_live_engine.py`) — CDP-based recorder using Playwright, supports HLS and DASH streams. Windows/macOS only.
- **Tailscale HTTPS proxy** (`api/tailscale_https.py`) — serves the Remote API PWA over HTTPS via Tailscale funnel.
- **TikTok profile live checker** (`utils/tiktok_live_checker.py`) — checks if a TikTok profile is live, no cookie required.

### Fixed

- **BUG-TT-08** — TikTok Live falsely reported "not live": TLS fingerprinting was stripping `roomId` from responses, `roomId="0"` wasn't rejected, and checker failures still triggered pointless yt-dlp retries. Fixed with `curl_cffi` Chrome impersonation, `roomId` validation, and proper error propagation.
- **BUG-TT-09** — TikTok omits `roomId` from HTML for some IPs; added a `webcast/room/list/` API pass before the HTML-scrape fallbacks.
- **BUG-TT-10** — `curl_cffi >= 0.15` API mismatch between yt-dlp's `ImpersonateTarget` object and `curl_cffi`'s expected string; split into two separate variables.
- **BUG-IG-01** — Instagram Live moved to DASH streaming; added `.mpd` matching, Service Worker interception, and a longer timeout.
- **BUG-KS-01** — Kuaishou CDN probe timeouts incorrectly triggered re-extraction; now only real URL-expiry errors (403/404/410) do.
- **BUG-KS-02** — Kuaishou sessions were missing cookies, causing silent HTML responses instead of video; cookies now attached to every session, with HTML responses detected and retried.
- **GPU encoder stall** — the encoder-availability cache wasn't invalidated after a watchdog kill, causing repeat stalls; now invalidated on any GPU conversion error.
- **CMD window flash (Windows)** — `facebook_story_engine.py` subprocess calls were missing `CREATE_NO_WINDOW`; fixed across all calls.

### Coverage

- Playwright-CDP files (`instagram_live_engine.py`, `cookie_extractor.py`, `cookie_storage.py`) excluded from coverage — can't run headlessly in CI.

---

## v16.3.1 — 2026-03-22

### Fixed

- Corrected the hardcoded app version in `main.py` (was stuck at an old value, breaking version-change detection).
- Added `playwright` to the startup dependency check.
- Dependency install strings now use versioned lower bounds instead of exact pins.
- Sidebar version label and `config.json` sample brought back in sync with the actual release.

---

## v16.3.0 — 2026-03-21

### Added

- Toolbar migrated to the `_ui_queue` pattern for thread-safe UI updates, consistent with the rest of the app.

### Removed

- **Video Editor tab** — removed to keep OmniDL focused on downloading; lacked realtime preview compared to dedicated editors.
- **Threads engine** — removed due to high maintenance cost from frequent, undocumented API changes.

### Fixed

- Coverage config updated after removals; dead code in the Special Downloads tab cleaned up.

---

## v16.2.1 — 2026-03-21

### Fixed

- **Threads engine** — wrong API endpoint and app ID corrected, stale `lsd` token refreshed dynamically, missing headers added, and a GraphQL fallback strategy added.

---

## v16.2.0 — 2026-03-21

### Added

- **Threads engine** (`infrastructure/downloader/threads_engine.py`) — downloads Threads posts (video, image, carousel) via a 4-strategy cascade, no browser required.
- **macOS support for Facebook Story**.
- **Resolution picker in Video Editor** — 480p to 4K, with auto CRF and bitrate estimate.

### Fixed

- Minor Video Editor bugs: a `Path` type error, UI thread blocking during encoder detection, an incorrect blur filter graph, and a falsy-zero bug in CRF handling.

---

## v16.1.0 — 2026-03-20

### Added

- **Special Downloads tab** — for platforms outside the main yt-dlp/gallery-dl pipeline. Starts with Facebook Story, fully isolated from the rest of the app.

### Changed

- **Facebook Story engine rewritten** to use Playwright's `connect_over_cdp()` instead of hand-rolled WebSocket code, still using the user's own logged-in browser.

### Fixed

- Duplicate downloads no longer overwrite existing files.
- Long downloads now have a hard 300-second deadline with fallback.
- Post-download buttons no longer stay hidden after a successful download.
- Removed unused dead code left over from the pre-CDP implementation.

### Dependencies

- Added `playwright>=1.40` (runtime only, no browser binary bundling needed).

---

## v16.0.0 — 2026-03-07

### Security

- **Path traversal (cookie files, `safe_path()`)** — replaced a bypassable `str.startswith()` check with proper `Path.parents` containment.
- **Shell injection** — removed `shell=True` from `reveal_in_explorer()`.
- **User data in executable directory** — config/history/logs moved to the platform-appropriate writable directory via `platformdirs`.
- **SSRF via thumbnail URL** — added scheme allowlist, private-IP blocking, and DNS resolution checks.
- **Unsanitised browser name** — cookie extraction now validates against a known-browser allowlist.
- **yt-dlp extra args injection** — user-supplied extra args filtered through a strict allowlist.

### Architecture

- Thumbnail fetching/decoding logic extracted out of the UI layer into `app/services/thumbnail_service.py`.
- One-time migration of old config/history files to the new data directory on first launch.

### Infrastructure

- CI runs pytest (3.11-3.13), ruff, mypy, bandit, and pip-audit on every push/PR.
- Tagged builds (`v*.*.*`) trigger PyInstaller builds for Windows and macOS, published as a GitHub Release.

### Stability

- Config and history writes are now atomic (temp file + rename) and debounced.
- History storage switched to append-only JSONL instead of full-file rewrites.
- Task state transitions are now lock-protected to prevent the UI from reading half-updated state.
- Live-stream cancel now responds within ~10s instead of up to 90s.

### Dependencies

- Raised minimum versions for `yt-dlp`, `Pillow` (security patch), added `platformdirs`; moved `pyinstaller` to dev-only.
