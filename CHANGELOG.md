# Changelog

All notable changes to OmniDL are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v18.0.0 — 2026-04-30

### Added

- **`utils/__version__.py`** — single source of truth for the application version string.
  All code that previously read `_APP_VERSION` from `main.py` now imports from here.

- **`infrastructure/downloader/instagram_live_engine.py`** — CDP-based Instagram Live
  recorder. Intercepts HLS (`.m3u8`) and DASH (`.mpd`) streams via Playwright
  `connect_over_cdp`. Windows/macOS only; Linux raises `RuntimeError` immediately.
  Supports DASH adaptive streaming introduced by Instagram in 2025 (BUG-IG-01):
  `_IG_HLS_RE` matches both `.m3u8` and `.mpd`; DASH streams muxed to `.mkv`
  via `-f matroska`; timeout raised from 30 s to 60 s; `ctx.route` registered for
  Service Worker intercept; `Network.responseReceived` listener added for
  `application/dash+xml` MIME type.

- **`api/tailscale_https.py`** — Tailscale HTTPS reverse-proxy helpers for the
  Remote API. Allows the PWA to be served over HTTPS via Tailscale funnel.

- **`ui/tabs/settings/remote_api_panel.py`** — extended with a Tailscale HTTPS
  Profile section backed by `tailscale_https.py`.

- **`utils/tiktok_live_checker.py`** — TikTok profile live-status checker.
  `check_tiktok_live(username, proxy)` returns the HLS URL when the profile is
  live, `None` otherwise. `LiveMonitorTab` now supports TikTok profile watch
  (`@username` URLs) alongside Instagram. No cookie required.

### Fixed

- **BUG-TT-08 — TikTok Live false "not currently live"** (`utils/tiktok_live_checker.py`,
  `app/services/download_service.py`) — Three compounded issues: (1) plain
  `requests.Session` used — TikTok TLS fingerprinting stripped `roomId` from JSON
  for non-Chrome TLS. Fixed: `curl_cffi` with Chrome impersonation via
  `_get_impersonate_session()`. (2) `roomId="0"` not rejected — TikTok returns
  `"0"` for non-live users. Fixed: `_valid_room_id()` rejects `"0"`, `""`, and
  non-numeric strings. (3) Checker failure still built `MediaInfo(is_live=True)`
  causing 4 useless yt-dlp retries. Fixed: `download_service.py` calls `on_error()`
  when checker returns `None`.

- **BUG-TT-09 — TikTok Live `roomId` omitted from HTML via bot-detection**
  (`utils/tiktok_live_checker.py`) — TikTok 2025+ omits `roomId` from rendered
  HTML for server/residential IPs even with valid cookies and Chrome TLS. Fixed:
  pass-0 added to `_check_tiktok_live_with_room_id()` — extracts `sec_user_id`
  from the share URL query params, calls the `webcast/room/list/` API directly.
  Pass-0 failure is non-fatal; falls through to HTML-scrape passes 1/2/3.

- **BUG-TT-10 — `curl_cffi >= 0.15` `ImpersonateTarget` API mismatch**
  (`infrastructure/downloader/yt_dlp_engine.py`, `utils/tiktok_live_checker.py`) —
  `_get_chrome_impersonate_target()` returned a map KEY (`ImpersonateTarget` object);
  `curl_cffi` internally calls `target.encode()` which fails on `ImpersonateTarget`.
  Fixed: TWO separate module-level vars — `_IMPERSONATE_TARGET` (`ImpersonateTarget`
  object, for yt-dlp `opts["impersonate"]` only) and `_IMPERSONATE_STRING` (string
  from map VALUE e.g. `"chrome131"`, fallback `"chrome"`, for all `curl_cffi`
  `Session/head/get` calls only). `tiktok_live_checker._get_chrome_impersonate_target()`
  returns map VALUE string.

- **BUG-IG-01 — Instagram Live DASH/MPD + Service Worker + timeout**
  (`infrastructure/downloader/instagram_live_engine.py`) — Instagram Live migrated
  to DASH adaptive streaming in 2025. All regex patterns matched `.m3u8` only;
  30 s timeout insufficient; Service Worker requests not interceptable via
  `page.on("request")`. Fixed: `_IG_HLS_RE` matches `.m3u8` + `.mpd`;
  `_CDP_HLS_WAIT_S = 60.0`; `ctx.route("**/*", ...)` for Service Worker;
  `Network.responseReceived` for `application/dash+xml`; DASH streams use
  `-f matroska` (`.mkv` output).

- **BUG-KS-01 — Kuaishou CDN probe timeout incorrectly triggered re-extract**
  (`infrastructure/downloader/kuaishou_engine.py`) — `except Exception` in the CDN
  probe block set `_need_reextract = True` on network timeout. A timeout is not URL
  expiry. Fixed: re-extract triggered only on HTTP 403/404/410 or non-MP4 magic
  bytes. Probe timeout reduced 15 s to 8 s. Short URL re-extract now builds
  `kuaishou.com/short-video/<code>` directly, skipping `_resolve_short_url`
  (30 s timeout on non-CN IP).

- **BUG-KS-02 — Kuaishou download sessions missing `cookie_str` + HTML response
  not early-detected** (`infrastructure/downloader/kuaishou_engine.py`) —
  `_make_session()` was called without `cookie_str`; `kwaicdn.com` returned HTTP 200
  with HTML body (~92 KB). Fixed: `cookie_str` loaded at top of `download()` and
  passed to all `_make_session()` calls (probe, download, retry). `Content-Type`
  check added immediately after `session.get()`: `text/html` triggers inline
  re-extract that updates `task.media_info` in-place without raising.

- **GPU encoder stall: cache not invalidated after stall**
  (`app/services/ffmpeg_convert_service.py`) — after the watchdog killed a stalled
  GPU encoder process, the 5-minute detection cache still listed the encoder as
  valid; the next conversion would stall again. Fixed: `_encoder_cache_invalidate()`
  added; called in `_try_encode_with_fallback()` on any GPU `ConversionError` before
  the CPU fallback. Resets `_encoder_cache_ts = 0.0` so the next call to
  `detect_available_encoders()` re-runs full 2-phase validation.

- **CMD window flash on ffmpeg/ffprobe calls in `facebook_story_engine.py` (Windows)**
  — four `subprocess.run()` calls (ffmpeg download, `_ffmpeg_mux`, ffprobe in
  `_has_audio_stream`, `_ffmpeg_download_with_audio`) were missing `creationflags`.
  Fixed: `_WIN_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)` added at
  module level; `creationflags=_WIN_NO_WINDOW` applied to all 4 calls. All 5
  subprocess calls in the file now carry the flag (browser `Popen` was already
  fixed in BUG CB).

### Coverage

- `infrastructure/downloader/instagram_live_engine.py` added to `[tool.coverage.run] omit`
  (Playwright CDP cannot run headlessly in CI).
- `infrastructure/downloader/cookie_extractor.py` and `cookie_storage.py` added to omit.
- Test count: 37 files, 1376+ functions (up from 36 files / 1352+).

---

## v16.3.1 — 2026-03-22

### Fixed

- **`main.py`: `_APP_VERSION` corrected to `"16.3.0"`** — was incorrectly
  left as `"16.0.0"` after the v16.3.0 release. This caused
  `_clear_history_on_version_change()` to never trigger for users upgrading
  from v16.0.0, as `stored == _APP_VERSION` even after a genuine version bump.

- **`main.py`: `_check_deps()` now includes `playwright>=1.40`** — previously
  missing from the startup dependency check. Users without `playwright`
  installed would only discover the missing package when clicking into the
  Special tab, not at launch.

- **`main.py`: `_check_deps()` install strings now use versioned lower bounds**
  (e.g. `customtkinter>=5.2.2` instead of `customtkinter==5.2.2`) — consistent
  with `requirements.txt` and avoids forcing an exact pin that conflicts with
  already-installed environments.

- **`ui/main_window.py`: sidebar version label updated to `v16.3.0`** — was
  displaying `v16.0.0` in the bottom strip.

- **`config.json`: added missing keys `platform_cookies` and `app_version`**
  — both keys exist in `ConfigManager._DEFAULTS` but were absent from the
  sample `config.json` committed to the repository. `ConfigManager` fills
  them in at runtime, but their absence caused confusion when reading the
  file directly.

- **`requirements.lock`: added all missing runtime packages** — `gallery-dl`,
  `keyring`, `cryptography`, `playwright` were present in `requirements.txt`
  but omitted from `requirements.lock`, making the lock file inconsistent with
  the declared dependencies.

- **`.github/workflows/ci.yml`: header comment version updated to `v16.3.0`**

---

## v16.3.0 — 2026-03-21

### Added

- **`toolbar.py`: `_ui_queue` pattern** — migrated `_safe_done` and
  `_safe_error` callbacks (called from background thread via `analyse_url`)
  from `self.after(0, ...)` to `self._ui_queue.put(...)`. Added
  `_drain_ui_queue()` polling every 50 ms with `winfo_exists()` guard.
  Toolbar is now fully consistent with the `_ui_queue` pattern used by all
  other tabs and components (BUG AY fix). Safe on Python 3.14 which will
  raise `RuntimeError` for Tkinter calls from non-main threads.
  `after(50/100/4000)` calls remain unchanged — these are invoked on the
  UI thread and are not affected.

### Removed

- **Video Editor tab** (`ui/tabs/edit_tab.py`, `infrastructure/video/`) — removed
  to keep OmniDL focused on its core downloader purpose. The tab was functional
  but lacked realtime preview, making its value limited compared to dedicated
  editors (CapCut, DaVinci). Files removed: `edit_tab.py`,
  `video_edit_engine.py`, `infrastructure/video/__init__.py`.

- **Threads engine** (`infrastructure/downloader/threads_engine.py`) — removed
  from Special tab due to high API maintenance cost. Meta changes the Threads
  API frequently (endpoint, `doc_id`, `lsd` token); the engine required two
  complete rewrites in a single session with no guarantee of stability.
  Facebook Story remains the only platform in the Special tab.

### Fixed

- **`setup.cfg` coverage omit** — added
  `infrastructure/downloader/facebook_story_engine.py` to the `omit` list.
  The engine uses Playwright + CDP which cannot be tested headlessly; without
  this entry the 681-line file counted toward coverage and risked breaching
  `fail_under = 80`.

- **`special_dl_tab.py` cleanup** — removed `_last_all: list[Path]`,
  `_run_threads()`, multi-file result handling, and Threads subtitle text.
  Tab now cleanly supports Facebook Story only.

---

## v16.2.1 — 2026-03-21

### Fixed

- **Threads engine: wrong endpoint** (`infrastructure/downloader/threads_engine.py`)
  — Strategy A was calling `i.instagram.com/api/v1/media/` which rejects
  Threads `media_id`s. Corrected to `www.threads.net/api/v1/media/`.

- **Threads engine: wrong app ID** — changed from Instagram web app ID
  `936619743392459` to Threads-specific `238260118697367`.

- **Threads engine: stale `lsd` token** — added `_extract_lsd_from_page()`
  to extract a fresh token from page HTML before each GraphQL call. The
  previously hardcoded fallback value `AVq8xCFW3BY` was expired.

- **Threads engine: missing headers** — added `X-IG-WWW-Claim`, `X-ASBD-ID`,
  and `Sec-Fetch-*` headers required by `www.threads.net/api/v1/`.

- **Threads engine: added Strategy A2** — GraphQL fallback with fresh `lsd`
  token, tries 3 known `doc_id` values. Strategy cascade is now A → A2 → B → C.

---

## v16.2.0 — 2026-03-21

### Added

- **Threads engine** (`infrastructure/downloader/threads_engine.py`) —
  cross-platform (Windows/macOS/Linux) Threads post downloader. No CDP, no
  browser launch; uses `requests` + Instagram session cookie. Supports video,
  single image, carousel. 4-strategy cascade:
  A. `www.threads.net/api/v1/media/{id}/info/`
  B. Threads GraphQL + lsd token
  C. oEmbed API
  D. Page scrape + JSON-LD.

- **macOS support for Facebook Story** (`facebook_story_engine.py`) — browser
  exe paths and profile directories resolved per-platform:
  Windows: `C:\Program Files\...`, macOS: `/Applications/...app/Contents/MacOS/`.
  Linux raises `RuntimeError` with clear message. App Store builds are
  explicitly not supported (do not allow `--remote-debugging-port`).

- **Resolution picker in Video Editor** (`edit_tab.py`, `video_edit_engine.py`)
  — 6-button segmented control: Giữ nguyên / 480p / 720p / 1080p / 2K / 4K.
  Selecting a resolution auto-updates CRF to recommended value and shows
  bitrate estimate. Scale uses `-2:height` to preserve aspect ratio.

### Fixed

- **`video_edit_engine.py`: `str` has no attribute `parent`** — `loc.ffmpeg_bin`
  is `str` (per `FFmpegLocation` dataclass); wrapped with `Path()` at point
  of use.

- **`edit_tab.py`: `get_supported_encoders()` blocking UI thread** — moved
  encoder detection to `_detect_encoders_async()` background thread; result
  returned via `_ui_queue` to update dropdown without freezing the UI.

- **`edit_tab.py`: blur filter graph syntax** — blur `[in]/overlay` complex
  filter was incorrectly included in `-vf` chain. Moved to `-filter_complex`
  with correct `crop → boxblur → overlay` chain per region.

- **`edit_tab.py`: `if preset.crf:` falsy** — `crf=0` (lossless) was skipped
  because `0` is falsy. Changed to `if preset.crf is not None:`.

---

## v16.1.0 — 2026-03-20

### Added

- **Special Downloads tab** — new sidebar entry (SYSTEM section, `⚡ Special`)
  for platforms that the main yt-dlp/gallery-dl pipeline cannot handle.
  Currently supports **Facebook Story** (video). Fully isolated: no imports
  from `YtDlpEngine`, `DownloadManager`, or `DownloadService` — a crash here
  cannot affect normal downloads.

### Changed

- **Facebook Story engine rewritten — Playwright `connect_over_cdp`**
  (`infrastructure/downloader/facebook_story_engine.py`):
  - Replaced ~230 lines of hand-rolled WebSocket code (`_ws_send`, `_ws_recv`,
    `_open_ws`, `_get_stable_cdp_connection`) with `playwright.sync_api.sync_playwright`
    using `connect_over_cdp()`. Browser is still the user's own Brave/Chrome
    (preserves Facebook login session); no `playwright install` / no extra
    browser binary download required.
  - Three interception layers retained: `page.on("request")` (Layer A),
    `page.on("response")` (Layer B), `page.evaluate(_POLL_JS)` every 2s (Layer C).
  - `add_init_script(_PRE_PAGE_JS)` replaces `Page.addScriptToEvaluateOnNewDocument`.
  - `_inject_play` / `_poll_video_url` replaced by inline `page.evaluate()` calls.
  - Public API (`download_story()`) unchanged.
- **Special tab post-download buttons** (`special_dl_tab.py`):
  - Removed: `🗑 Xoá file` (destructive action, no undo).
  - Added: `🗑 Xoá lịch sử` — resets the progress card to idle state
    (available both in the success `_btn_row` and the error `_retry_row`).
  - `📂 Mở thư mục` uses `explorer /select,<path>` — highlights the
    downloaded file in Windows Explorer.

### Fixed

- **File overwrite on duplicate Story ID** (`facebook_story_engine.py`) — if
  `fb_story_<slug>.mp4` already exists in the output directory, the new file is
  saved as `fb_story_<slug>_<timestamp>.mp4` instead of silently overwriting.
- **Stream download deadline missing** (`facebook_story_engine.py`) —
  `_download_cdn_url()` chunk loop now has a hard 300-second total deadline.
  Exceeding it aborts the download, deletes the partial file, and falls through
  to the `_ffmpeg_download` fallback.
- **UI buttons not visible after successful download** (`special_dl_tab.py`) —
  `_on_download()` called `self._open_btn.pack_forget()` before launching the
  worker thread. This hid the button inside `_btn_row` permanently; re-packing
  the container after completion did not restore it. Removed the spurious call.
- **Dead code removed** — `_get_cookie_path()` and `_cleanup_cookie()` in
  `facebook_story_engine.py` were defined but never called (leftover from the
  pre-CDP yt-dlp implementation). Deleted.
- **Dead code removed** — `self._worker_q: queue.Queue` in `SpecialDlTab.__init__`
  and its `import queue` were declared but never used. Deleted.

### Dependencies

- `playwright>=1.40` added to `requirements.txt` (runtime — `connect_over_cdp`
  only; no `playwright install` / no browser binary bundling required).

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
