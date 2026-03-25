# OmniDL — Project Map
Version: v16.3.0 (current)
Last updated: 2026-03-21 (v16.3.0: toolbar _ui_queue migration; Editor + Threads removed; coverage omit fix; Quick Stats corrected; deno_locator.py added; line counts corrected for yt_dlp_engine/helpers/convert_tab/special_dl_tab)
Purpose: Reference document for AI-assisted development sessions.
         Read this file FIRST before modifying any code.

---

## Quick stats

| Metric | Value |
|---|---|
| Python files (source) | 53 |
| Source lines (excl. tests) | ~15,791 |
| Test files | 27 |
| Test functions | 915+ |
| Python target | 3.11–3.13 (CI + build EXE); 3.14.3 (local dev) |
| UI framework | CustomTkinter |
| Download engines | yt-dlp + gallery-dl |
| Convert engine | FFmpeg (subprocess) |
| Live checker | Instagram web API (requests + stdlib) |

---

## Architecture — 5 layers

```
domain/          Pure data models. Zero I/O, zero framework deps.
app/             Business logic: services + use cases + event bus.
infrastructure/  External systems: yt-dlp, gallery-dl, config file, history storage.
ui/              All CustomTkinter UI code. Never imported by lower layers.
utils/           Shared utilities used by any layer.
```

**Dependency rule:** Each layer may only import from layers below it.
`ui` → `app` → `domain`. `infrastructure` → `domain`.
`utils` is imported by everyone. `ui` never imports `infrastructure` directly.

**Multi-engine architecture:**
- `YtDlpEngine` — all video + live stream downloads
- `GalleryDlEngine` — image/gallery downloads (Instagram photos, Twitter images)
- `instagram_live_checker` (`utils/`) — Instagram profile live detection via web API

Routing between engines is determined by `MediaInfo.source_engine` field set during
`extract_info()`. `DownloadManager` reads this field and calls the correct engine.
`LiveMonitorTab` uses `service.check_profile_live()` for Instagram profiles and
`service.analyse_url()` for all other live URL types.

**Cookie validation auto-fallback (BUG BE):**
- `_validate_cookie_path_raw()` and `_validate_cookie_path()`: if `.txt` absent but `.enc` exists → use `.enc`
- Fixes "cookie_file rejected" false-positive when `encrypt_cookie_file()` renamed `.txt` → `.enc` after config saved
- CWE-22 `safe_root in cp.parents` check still fires FIRST — fallback only within safe directory

**YouTube per-platform (BUG BF):**
- `_COOKIE_PLATFORM_MAP` now includes `youtube.com` and `youtu.be` → `"youtube"` key
- Enables per-platform YouTube cookie for age-restricted content

**Temp cookie leak fix (BUG BG):**
- `extract_info()`: 3 return paths all clean `_cookie_temp_ei` before returning
  (1) playlist early-return before ydl loop, (2) Instagram photo early-return, (3) normal exit
- Uses inline `try/unlink/except` pattern — same as existing cleanup at normal exit

**Per-platform cookie routing:** `_resolve_cookie(url, config)` in `yt_dlp_engine.py`
uses `urlparse().hostname` to detect platform from URL, then resolves the correct cookie
via `config.platform_cookies[key]` → fallback `config.cookie_file` → `None`.
Applies to `YtDlpEngine.extract_info()`, `YtDlpEngine.download()`, and
`GalleryDlEngine._base_cmd(url)`.

**Profile/channel detection:** `_PROFILE_URL_RE` matches unambiguous profile URLs.
When matched, `_extract_playlist_flat()` uses `extract_flat="in_playlist"` to collect
entry URLs without per-video extraction. Result: `MediaInfo(playlist_entries=[...])` →
`HomeTab` redirects to `BatchTab.load_playlist()`.

---

## Startup sequence (`main.py`)

```
1. _get_data_dir()      → platform data dir
   Windows : %APPDATA%\OmniDL\
   macOS   : ~/Library/Application Support/OmniDL/
   Linux   : ~/.local/share/OmniDL/

2. _get_log_dir()       → platform log dir

3. setup_logging(LOG_DIR)

4. _check_deps()        → verify customtkinter, yt_dlp, gallery_dl, PIL, requests, platformdirs
5. _migrate_legacy_data()   → one-time copy from old APP_BINARY_DIR location

6. ConfigManager(DATA_DIR / "config.json")
7. HistoryRepository(DATA_DIR / "download_history.jsonl", limit=500)
8. _clear_history_on_version_change(config, history)

9.  YtDlpEngine(config)
10. GalleryDlEngine(config)
11. DownloadManager(config, engine=engine, gallery_engine=gallery_engine) → start()
12. DownloadService(config, manager, history, engine, gallery_engine=gallery_engine)

13. T.set_mode(config.theme) → ctk.set_appearance_mode(T.ctk_base)
14. MainWindow(service=service, config=config) → mainloop()
15. On close: manager.shutdown(), service.close(), config.save()
```

**Title bar (BUG AR):** `_ytdlp_badge` and the old "Powered by yt-dlp" badge permanently removed.
`self._powered_lbl` (version label `v16.0.0` in sidebar bottom strip) is kept for theme refresh — this is a DIFFERENT widget.
Engine versions are in Settings → YT-DLP ENGINE / GALLERY-DL ENGINE.

---

## File-by-file reference

### `main.py` (227 lines)
Entry point. Constructs full object graph.
Key functions: `_get_data_dir()`, `_check_deps()`, `_migrate_legacy_data()`,
`_clear_history_on_version_change()`, `main()`.

---

### `domain/enums/download_status.py` (26 lines)
```python
QUEUED, DOWNLOADING, PROCESSING, PAUSED, COMPLETED, FAILED, CANCELLED
active_states()   → {QUEUED, DOWNLOADING, PROCESSING}
terminal_states() → {COMPLETED, FAILED, CANCELLED}
```

---

### `domain/models/download_task.py` (206 lines)
Core domain entity. Pure Python.

**`MediaInfo` fields:**
```
url, title, uploader, duration, thumbnail, platform
formats: list[dict]   # [] for image-only / live posts
is_live: bool
was_live: bool
video_id: str
source_engine: str = "yt_dlp"   # "yt_dlp" | "gallery_dl"
```
`source_engine="gallery_dl"` → `DownloadManager` routes to `GalleryDlEngine`.

**`DownloadTask`** — `_lock: threading.RLock`, `snapshot()` for atomic multi-field read,
`wait_if_paused()` (yt-dlp only), `is_cancellation_requested`.

---

### `app/event_bus.py` (102 lines)
Pub/sub singleton `bus`. Events: `download.*`, `analysis.*`.
Typed convenience publishers (Fixed7) are additive — original `publish()` unchanged.

---

### `app/services/download_service.py` (312 lines)
**The only object the UI talks to for downloads.**

Public API:
```python
analyse_url(url, on_done, on_error)          # background thread
check_profile_live(url, on_done, on_error)   # NEW — Instagram profile live check
start_download(url, media_info, format_id, output_ext, output_dir=None) → DownloadTask
pause_download / resume_download / cancel_download
clear_finished / get_task / get_all_tasks
get_history / search_history / clear_history
convert_to_mp4(...) → cancel_fn
fetch_thumbnail(url, width, height, on_done, on_error)
close()
```

**`check_profile_live(url, on_done, on_error)`:**
Spawns daemon thread → extracts username from URL → calls
`utils/instagram_live_checker.check_instagram_live(username, cookie_file, proxy)` →
calls `on_done(live_url_or_None)` or `on_error(message)`.
Same threading pattern as `analyse_url()`.
Cookie: uses `_resolve_cookie("https://www.instagram.com/", config)` →
Instagram-specific cookie when available, else global fallback.

**Auto-routing in `analyse_url()`:** When yt-dlp raises a photo error on a supported
image platform, `_should_fallback_to_gallery_dl()` triggers silent retry via `GalleryDlEngine`.

---

### `app/services/thumbnail_service.py` (219 lines)
Owned by `DownloadService._thumbnail_svc`. SSRF prevention. HomeTab must NOT instantiate directly.

---

### `infrastructure/config/config_manager.py` (328 lines)
Thread-safe JSON config. Defaults, range clamping, `reset_to_defaults()`.

**`reset_to_defaults()`:** Called by version change and `SettingsTab._clear_all_data()`.

**Per-platform cookie API:**
```python
platform_cookies -> dict[str, str]
    # Returns mapping platform_key → path. isinstance guard: corrupt value → {}.
    # Keys: "tiktok", "instagram", "facebook", "twitter", "threads"

get_cookie_for_platform(key: str) -> str
    # Returns path or "" — never None

set_cookie_for_platform(key: str, path: str) -> None
    # Thread-safe via config.set(). Empty path → removes key from dict.
```

**Config key added:** `"platform_cookies": {}` (default empty dict).
Backward compat: existing `"cookie_file"` is the global fallback — not removed.

---

### `infrastructure/downloader/cookie_storage.py` (305 lines)
At-rest encryption for cookie files — Windows DPAPI + macOS Keychain/Fernet (BUG BC).

**`encrypt_cookie_file(txt_path) → Path`**
- Windows: `CryptProtectData` (DPAPI) — key derived from Windows login credentials
- macOS: `_macos_get_or_create_key()` + `Fernet.encrypt()` — AES-128-CBC + HMAC-SHA256
- Linux: no-op — returns `.txt` path unchanged (plaintext)
- Atomic: `.enc` fully written BEFORE `.txt` deleted — no data loss on disk failure
- Falls back silently to plaintext on DPAPI/Fernet failure (logs warning, never crashes)

**`_macos_get_or_create_key() → bytes` (macOS internal):**
- Reads 32-byte key from macOS Keychain (`service="OmniDL"`, `account="cookie_encryption_key_v1"`)
- First call: generates `os.urandom(32)`, stores base64url in Keychain via `keyring`
- Subsequent calls: retrieves existing key — same key used for all future encryptions
- Raises `RuntimeError` if Keychain write denied (user must grant Keychain access)
- Corrupted entry triggers automatic key regeneration

**`decrypt_to_tempfile(enc_path) → Path`**
- Windows: `CryptUnprotectData` (DPAPI)
- macOS: `_macos_get_or_create_key()` + `Fernet.decrypt()`
- Returns `enc_path` unchanged if not `.enc` (plaintext passthrough — no temp created)
- Raises `RuntimeError` on: wrong user account / corrupted file / lost Keychain key
- Caller MUST delete returned path in `finally` block

**`cleanup_stale_cookies(safe_dir, max_age_days=30) → int`**
- Deletes `.txt` and `.enc` files older than `max_age_days`
- Skips `_tmp_*` and `omnidl_dec_*` prefixes (managed separately)
- Called from `main.py` at startup

**`cleanup_leftover_temp_files(safe_dir)`**
- Deletes `omnidl_dec_*.txt` files from any previous crashed session
- Called from `main.py` at startup before `cleanup_stale_cookies`

---

### `infrastructure/downloader/cookie_extractor.py` (696 lines)
Two-method cookie extraction module — no third-party extension required.

**Method 1 — yt-dlp: `extract_browser_cookies(browser, output_path, platform_key=None)`**
- Uses yt-dlp `cookiesfrombrowser` option (DPAPI/keyring decryption)
- Temp-file-first pattern: writes `_tmp_<n>`, renames on success — never corrupts existing file
- Platform filter via `http.cookiejar.MozillaCookieJar` + `_PLATFORM_DOMAINS`
- Works for: Firefox, Opera, Edge, Chrome/Brave < 127
- Fails on Brave/Chrome 127+ (App-Bound Encryption) — use CDP method
- `_friendly_extract_error(msg)` translates yt-dlp errors to Vietnamese (7 error patterns)

**Method 2 — CDP: `extract_via_cdp(output_path, platform_key, browser, port=9223)`**
- Chrome DevTools Protocol — browser decrypts its own cookies, bypasses App-Bound Encryption
- Works on ALL Brave/Chrome versions including 127+. Uses Python stdlib only (no deps).
- Flow:
  1. `_is_browser_running(browser)` → if True, return error (profile file-lock)
  2. `_find_browser_profile(browser)` → real User Data dir (NOT temp dir)
  3. Launch `brave.exe --remote-debugging-port=9223 --user-data-dir=<real profile>`
  4. `_cdp_wait_ready(port, timeout=20)` — polls `/json/version` until CDP ready
  5. `_cdp_get_page_ws_url(port)` — `/json/list` picks first `type=="page"` target
     (NOT `/json/version` browser endpoint — Network.* only works on page targets)
  6. WebSocket connect: `_cdp_ws_connect`, `_cdp_ws_send`, `_cdp_ws_recv` (stdlib only)
  7. `_cdp_get_all_cookies(sock)`:
     - Step A: `Network.enable` (id=1) — mandatory before getAllCookies or returns `[]`
     - Step B: `Network.getAllCookies` (id=2) — actual cookie fetch
  8. Platform filter (same `_PLATFORM_DOMAINS` logic as yt-dlp method)
  9. `_cdp_cookies_to_netscape(cookies)` → Netscape format string
  10. Write output file
  11. `finally`: `proc.terminate()` + `proc.wait(5)` + `proc.kill()` fallback
- `_friendly_cdp_error(msg)`: 4 error patterns → Vietnamese user messages
- `_is_browser_running(browser)`: `tasklist /FI "IMAGENAME eq brave.exe"` list-form,
  no `shell=True`, returns False on non-Windows (safe default)

**`_PLATFORM_DOMAINS`** — platform key → domain tuple (exact/suffix filter):
`tiktok → tiktok.com`, `instagram → instagram.com`,
`facebook → facebook.com / fb.com`, `twitter → twitter.com / x.com`,
`threads → threads.net / instagram.com` (Threads auth is Instagram-backed).
YouTube: `("youtube.com", "youtu.be", "google.com")` — `google.com` required for age-verify auth (BUG BF).
Twitch/Vimeo/Dailymotion intentionally NOT included.

**Output filenames** (all 4 distinct, no collision):
- yt-dlp global: `{browser}_global_cookies.txt`
- CDP global: `{browser}_cdp_cookies.txt`
- yt-dlp per-platform: `{platform_key}_{browser}_cookies.txt`
- CDP per-platform: `{platform_key}_{browser}_cdp_cookies.txt`

---

### `infrastructure/downloader/yt_dlp_engine.py` (1221 lines)
**`extract_info(url) → MediaInfo`**
- Cookie path validation (CWE-22)
- Intercepts IG photo errors → synthetic `MediaInfo(source_engine="gallery_dl")`
- Forces `is_live=True` for IG Live URLs (race condition fix, BUG AM)
- 3-attempt retry with 1s/2s backoff; hard errors not retried

**`_friendly_error(msg)`:**
- `msg[:200]` fallback as final catch-all — MUST remain
- 429 / rate-limit check: `("rate" in msg_l and ...) or "429" in msg_l or "too many requests" in msg_l`
  The standalone `"429"` branch handles `"HTTP Error 429: Too Many Requests"` which has no `"rate"` keyword.

**`download(task, ...)`**
- `is_live` detection: `media_info.is_live` OR TikTok/IG URL pattern + `duration==0`
- Live opts: `hls_use_mpegts=True`, `live_from_start=False`, `socket_timeout=10`
- Live outtmpl uses hardcoded `.ts` extension (NOT `%(ext)s`) — prevents MPEG-TS content
  being named `.mp4` which iPhones cannot play via Photos/QuickTime (BUG AX)
- `".ts"` in `_MEDIA_EXTS` — required for `pp_hook` to capture `task.filename` (BUG AX)
- Live ETA: `"⏺ X MiB đã ghi"` when `total_bytes==0`
- No `allow_unplayable_formats` — gallery-dl handles photos

**Instagram Live regex (3 sync locations):**
`r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)"`
in `_NEEDS_COOKIES`, `_instagram_live_re` in `download()`, `_ig_live_re` in `extract_info()`.

**Profile/channel detection:**
- `_PROFILE_URL_RE` — module-level regex matching unambiguous profile/channel/playlist URLs.
  Matched early in `extract_info()` (after opts built with cookie/proxy) → calls `_extract_playlist_flat()`.
  Platforms: TikTok `@user`, YouTube channel/playlist, Twitter/X `@user`, Instagram profile, Threads `@user`.
  Single-video URLs (`/video/ID`, `/watch?v=`, `/status/`) intentionally NOT matched.
- `_extract_playlist_flat(url, base_opts)` — collects entry URLs via `extract_flat="in_playlist"`.
  No per-video extractors called → no errors for deleted/geo-restricted entries.
  Returns `MediaInfo(playlist_entries=[...], playlist_title=...)`.
  Nested playlist flattening via `_collect()` helper (handles YouTube channel-of-playlists).

**Per-platform cookie routing:**
- `_COOKIE_PLATFORM_MAP` — exact hostname → key: tiktok.com, instagram.com, facebook.com,
  fb.watch, twitter.com, x.com, threads.net. YouTube/Twitch NOT included.
- `_resolve_cookie(url, config)` — uses `urlparse().hostname` (NOT regex substring).
  Order: `platform_cookies[key]` validated → `config.cookie_file` → `None`.
  Subdomain spoofing blocked: `malicious.tiktok.com.evil` hostname does not end with `.tiktok.com`.
- `_validate_cookie_path_raw(path, config)` — CWE-22 guard (`Path.parents`) for per-platform paths.
- `has_cookies` check includes `any(config.platform_cookies.values())` so per-platform
  cookies allow IG stories/live through `_NEEDS_COOKIES` even without global `cookie_file`.

---

### `infrastructure/downloader/gallery_dl_engine.py` (412 lines)
Wraps gallery-dl CLI. Activated when `source_engine=="gallery_dl"`.

- `_base_cmd(url="")`: `gallery-dl -q [--cookies FILE] [--proxy URL]` — NEVER `--no-progress`
  Accepts `url` for per-platform cookie resolution. Callsites pass `url=url` (extract_info)
  and `url=task.url` (download). Falls back to global `_validate_cookie_path()` if no URL.
- `extract_info()`: `--dump-json --no-download` → parses JSON → returns `MediaInfo`
- `download()`: Popen, stdout progress (file-count), cancel via `task.is_cancellation_requested`
- `_friendly_error()`: every message prefixed with English keyword matching `_HARD_ERROR_KEYWORDS`

Supported platforms: instagram.com, twitter.com, x.com, pinterest.*, pixiv.net, deviantart.com

---

### `infrastructure/downloader/download_manager.py` (299 lines)
`ThreadPoolExecutor(max_workers=config.max_concurrent)`.

**Engine routing in `_run_task()`:**
```python
use_gallery = (
    self._gallery_engine is not None
    and task.media_info is not None
    and getattr(task.media_info, "source_engine", "yt_dlp") == "gallery_dl"
)
```

**`_HARD_ERROR_KEYWORDS`:** 24 keywords. Never remove. See Section 7.

---

### `infrastructure/storage/history_repository.py` (191 lines)
JSONL-backed history. Thread-safe. `clear()` is safe during active downloads.

---

### `utils/logger.py` (97 lines)
`RotatingFileHandler` (5 MB, 3 backups), `encoding="utf-8"`.
Console handler wraps `sys.stdout` with `io.TextIOWrapper(errors="replace")` ONLY when
`hasattr(sys.stdout.buffer, "raw")` — skipped in pytest to prevent capture teardown crash.

---

### `utils/ffmpeg_locator.py` (254 lines)
Finds FFmpeg: bundled → macOS bundle → PATH.

---

### `utils/helpers.py` (206 lines)
`fmt_bytes`, `fmt_duration`, `is_valid_url`, `sanitise_filename`, `safe_path`,
`reveal_in_explorer`, `open_folder`, `open_file`.

---

### `utils/instagram_live_checker.py` (216 lines) — NEW
Instagram profile live-status checker. No new dependencies (uses `requests` already in requirements).

**Public API:**
```python
is_instagram_profile_url(url: str) -> bool
    # True for instagram.com/username/ — False for /live/, /p/, /reel/, etc.

extract_instagram_username(url: str) -> Optional[str]
    # Extracts username from profile URL

check_instagram_live(username: str, cookie_file: str, proxy: str = "") -> Optional[str]
    # Returns live URL if broadcasting, None if not live
    # Raises RuntimeError for ALL errors (both hard and transient)
    # Hard errors prefixed with keywords: "login: …", "not found: …", "private: …"
    # Transient errors (timeout, 429): plain message, no keyword prefix
```

**API endpoint:** `https://i.instagram.com/api/v1/users/web_profile_info/?username=USERNAME`

**Headers:** `X-IG-App-ID: 936619743392459` (public Instagram web app ID)

**Cookie parsing:** `http.cookiejar.MozillaCookieJar` — reads the same Netscape file as yt-dlp.
Requires `sessionid` cookie. Returns `None` (not live) if user is private and not broadcasting.

**Live detection:** Checks `live_broadcast_id`, `is_live`, `has_active_broadcast` fields
in the `data.user` JSON response.

**Error handling:** Only `RuntimeError` is raised — there is NO `TransientError` class.
`DownloadService.check_profile_live()` catches all `Exception` and forwards the message string
to `on_error()`. `LiveMonitorTab._on_check_error()` classifies as hard (→ `ERROR` state) or
transient (→ `WAITING` state) by keyword substring check on the message.

---

### `utils/deno_locator.py` (107 lines) — NEW
Locates the bundled Deno binary at runtime. Deno is required by yt-dlp to solve
YouTube's JavaScript n-challenge (anti-throttle); without it, YouTube speeds are throttled.

**Resolution order:**
1. `sys._MEIPASS/deno/` — PyInstaller frozen bundle
2. `<project_root>/resources/deno/` — source mode / CI staging area
3. `shutil.which("deno")` — system PATH fallback

**Public API:**
```python
locate_deno() -> Optional[Path]
    # Returns absolute path to deno binary, or None if not found.

get_deno_path() -> Optional[str]
    # Returns deno binary path as string, or None.

get_deno_env() -> dict[str, str]
    # Returns modified os.environ with deno directory prepended to PATH.
    # Pass to subprocess env= so yt-dlp finds deno without a system install.
```

**Usage in `yt_dlp_engine.py`:** `get_deno_env()` is called inside both
`extract_info()` and `download()` to inject the deno directory into the
subprocess environment before spawning yt-dlp.

---

### `app/services/ffmpeg_convert_service.py` (1106 lines)
`FfmpegConvertService`, `ConvertQueue`, `FfmpegMediaInfo` (renamed, BUG AD).
`_resume_encode` permanently deleted (BUG AE).
`_parse_seconds(m)` — pure function converting `HH:MM:SS.cs` regex match to float seconds; importable by tests.

**H.264 profile (BUG AX):** All encode paths use `-profile:v main -level:v 4.1` (changed
from `high`/`4.0`). Main Profile is required for iPhone Photos/QuickTime compatibility.
Applies to `_build_cpu_flags()` (both legacy and encode_settings paths) and `_build_gpu_flags()`.
**MP4 output always includes:** `-pix_fmt yuv420p`, `-movflags +faststart` (moov atom first — enables
immediate playback on iPhone/AirDrop), `-c:a aac -b:a 128k -ar 44100`.

**URL label on FAILED status (BUG BD):**
- `_url_lbl`: `ctk.CTkLabel` initialized to `None` in `__init__` before `_build()`
- Shown below title on `DownloadStatus.FAILED` — displays `🔗 <url>` so user can identify failed link
- Access via `getattr(self, "_url_lbl", None)` — safe when tests use `__new__` without calling `_build()`
- `pack_forget()` on all non-FAILED states

**Python 3.14 thread-safety (BUG AY):**** `ConvertTab` uses a `queue.Queue`-based callback
pump instead of `self.after(0,...)` from background threads:
- `self._ui_queue: queue.Queue` — initialized in `__init__` before thread spawn
- `_poll_ui_queue()` — drains queue every 50 ms on UI thread; in `ConvertTab` (not `FileCard`)
- 7 background functions use `self._ui_queue.put(fn)`: `_detect_encoders_async`,
  `_scan_folder_async`, `_probe_info_async`, `on_start`, `on_progress`, `on_done`, `on_error`
- Same `_ui_queue` pattern applied to all 7 affected classes:
  `live_monitor_tab` (6 puts, `_poll()` drain), `batch_tab` (4 puts, 100 ms),
  `download_item_widget` (3 puts, 50 ms), `status_bar` (1 put, 100 ms),
  `settings_tab` (10 puts across 2 update workers, 150 ms),
  `toolbar` (2 puts: `_safe_done`/`_safe_error`, 50 ms — v16.3.0)
- Pattern required for any future background thread in any UI class
- `_apply_available_encoders()`: `winfo_exists()` guard added — prevents TclError on early app close
- `_refresh_status()`: `winfo_exists()` guard added — prevents TclError during close-while-encoding

---

### `ui/main_window.py` (484 lines)
9 tabs. Custom title bar. `_ytdlp_badge` and old "Powered by yt-dlp" badge permanently removed (BUG AR).
`self._powered_lbl` = version label `v16.0.0` in sidebar bottom strip — stored for theme refresh, NOT the removed badge.

**NAV_ITEMS:**
```python
("home",         "⬇",  "Download",    "DOWNLOADS"),
("queue",        "≡",  "Queue",        "DOWNLOADS"),
("batch",        "☰",  "Batch",        "DOWNLOADS"),
("live_monitor", "🔴", "Live Monitor", "DOWNLOADS"),
("convert",      "🍎", "Convert",      "TOOLS"),
("history",      "⏱",  "History",      "LIBRARY"),
("settings",     "⚙",  "Settings",     "SYSTEM"),
("special_dl",   "⚡", "Special",       "SYSTEM"),
```

**ServiceFacade new method:**
```python
check_profile_live(url, on_done, on_error)  # Instagram profile live check
```

---

### `ui/tabs/home_tab.py` (602 lines)
URL analysis and download. `_thumb_token` guard. Photo/image detection hides quality picker.

**Playlist redirect (BUG AT):**
`on_analysis_done()` checks `info.playlist_entries` before `_populate_card()`.
When non-empty: resets HomeTab to welcome, navigates to BatchTab,
calls `batch_tab.load_playlist(urls, title)` via `after(50, ...)`.
`get_tab("batch") is not None` guard prevents crash if BatchTab unavailable.
Single-video path in `else` branch — unchanged.

---

### `ui/tabs/queue_tab.py` (141 lines)
Polls `service.get_all_tasks()` every 500ms.
`on_convert=lambda p, **kw: self._app.convert_to_mp4(p, **kw)` wired to `DownloadItemWidget`
— enables → MP4 button for non-MP4 completed files in Queue tab.

---

### `ui/tabs/batch_tab.py` (899 lines)
MAX_BATCH_URLS=50. Sequential `_analyse_next()`. `_batch_token` guards. ServiceFacade only.

**`load_playlist(urls, playlist_title="")`** (public method, BUG AT):
Called by HomeTab when profile/channel URL is analysed. Clears existing batch,
populates textarea with `urls[:MAX_BATCH_URLS]`, shows truncation toast if capped,
auto-starts analysis. Must be called on UI thread (HomeTab uses `after(50, ...)`).

**Per-platform analysis delay (BUG BI):**
- `_PLATFORM_ANALYSIS_DELAY`: hostname → seconds dict (instagram=2.5, tiktok=2.0, facebook/twitter=1.5, youtube=0.8)
- `_get_analysis_delay(url)`: `urlparse().hostname` lookup — NOT regex substring
- `_delayed_analyse()`: daemon thread sleeping delay before `analyse_url()` — first item has no delay; UI stays responsive
- `_retry_btn`: appears after `_on_batch_complete()` when `errors > 0`; shows count ("🔄 Thử lại 3 lỗi")
- `_retry_errors()`: resets ERROR items to PENDING; fires new `_batch_token`; preserves READY/QUEUED items
- ERROR state row: shows `URL · error_preview` so user knows which link failed
---

### `ui/tabs/live_monitor_tab.py` (1163 lines) — NEW
Live Stream Monitor tab. Monitors multiple URLs and auto-records when streams go live.

**Key constants:**
```python
MAX_MONITOR_URLS     = 20   # hard cap
MIN_CHECK_INTERVAL_S = 15   # seconds
DEFAULT_CHECK_INTERVAL = 30 # seconds
_POLL_MS             = 5000 # poll cadence
_COOKIE_WARN_DAYS    = 7    # days before warning
```

**`_MonitorItem` dataclass fields:**
```
url, state, media_info, task_id, error_msg
last_check: float              # time.time() of last check
is_profile_watch: bool         # True for instagram.com/username/ URLs
username: str                  # e.g. "baki_babyboy"
filename: str = ""             # captured from snap["filename"] on COMPLETED (BUG AW)
row_frame, state_lbl, title_lbl, platform_lbl,
progress_lbl, cancel_btn, open_folder_btn,         # 📂 shown when ENDED + filename set
remove_btn, cookie_warn                             # UI widgets
```

**`_open_folder_for_item(item)`** (BUG AW):
Reveals the recorded `.ts` file in Explorer/Finder using `reveal_in_explorer()`.
Falls back to `open_folder(p.parent)` if file not immediately visible.

**`_start_mp4_convert(item)`** (BUG AX):
Quick-convert .ts → .mp4 using CPU libx264 main profile, CRF 23. All callbacks use
`_ui_queue.put()`. Progress shown as % in button text. `item.filename` updated to new .mp4.

**`_send_to_convert_tab(item)`** (new):
Sends .ts file to Convert Tab for user-controlled encoding (GPU, preset, CRF).
Calls `convert_tab._add_file(path)` then `navigate_to("convert")`.
Button ⚙ shown alongside → MP4 — both visible when `.ts` file ready.

**`_MonitorState` state machine:**
```
WAITING   → polling every interval
CHECKING  → one check in flight
LIVE      → stream detected, starting download
RECORDING → DownloadTask active
ENDED     → COMPLETED
ERROR     → hard error, no retry
```

**Two check paths in `_trigger_check(item)`:**
1. Profile watch (`is_profile_watch=True`):
   `service.check_profile_live(url)` → Instagram API → `_on_profile_check_done()`
   On live detected: call `service.analyse_url(live_url)` for full MediaInfo →
   `_on_live_url_analysed()` → `_start_recording()`
   If analyse fails: `_on_live_url_analyse_fallback()` builds minimal MediaInfo → `_start_recording()`

2. Direct live URL:
   `service.analyse_url(url)` → yt-dlp `extract_info()` → `_on_check_done()`
   `is_live=True` → `_start_recording()`; `is_live=False` → back to WAITING

**Sequential analysis (`_checking` flag):**
Only 1 check in flight at a time — prevents rate limiting on Instagram/TikTok.
`_checking=False` set BEFORE token check in both `_on_check_done` and `_on_check_error`.

**`_monitor_token` stale-callback guard:**
Incremented on `_remove_item()` and `_clear_all()`. Every callback checks
`token != self._monitor_token` (after setting `_checking=False`) and returns early
if stale — prevents UI updates for removed items.

**Cookie warning:**
`_cookie_age_days()` reads `Path(config.cookie_file).stat().st_mtime`.
Banner + per-row warning shown when age > 7 days. Updated every poll cycle.

**ServiceFacade methods used:**
`analyse_url`, `check_profile_live`, `start_download`, `get_task`, `cancel_download`

**Does NOT use:**
`infrastructure/*`, `YtDlpEngine`, `GalleryDlEngine`, `check_instagram_live` directly.

---

### `ui/tabs/convert_tab.py` (1170 lines)
Quality cards use grid layout (BUG AK). `_custom_quality: tk.StringVar`.

---

### `ui/tabs/history_tab.py` (174 lines)
`refresh()` called on every navigation. Search via `service.search_history()`.

---

### `ui/tabs/settings_tab.py` (1661 lines)
**8 sections:**
```
📁 DOWNLOAD LOCATION
⚙  DOWNLOAD BEHAVIOUR
🔒 NETWORK & AUTHENTICATION
🍪 PER-PLATFORM COOKIES         ← BUG AU + BB
🎨 APPEARANCE
🔧 YT-DLP ENGINE                ← keyring button added (BUG BB)
🖼 GALLERY-DL ENGINE
🗑 DATA & PRIVACY
```

**Global cookie section demoted (BUG BH):**
- Section renamed: "🌐 Cookie fallback — cho YouTube, Twitch, Vimeo..." (NOT primary option)
- Warning label: "⚠ File này chứa toàn bộ cookies (Google, email, banking...)"
- Button labels clarified: "🔄 Firefox / Edge / Opera" | "🦁 Brave / Chrome 127+"
- `🍪 PER-PLATFORM COOKIES` title: added "✅ Khuyến nghị — bảo mật hơn"
- Per-platform description: explains why per-platform is safer (no cross-domain leakage)

**Built-in cookie extractor — final (BUG BB):****

`🔒 NETWORK & AUTHENTICATION` section (global cookies):
- `🔄 Lấy cookies từ trình duyệt` → `_extract_global_cookies()` (yt-dlp method)
- `🦁 Brave/Chrome 127+ (CDP)` → `_extract_global_cdp()` (CDP method)
- `_extract_global_hint`: warning label explaining which button for which browser
- `_extract_global_status`: shared status label (progress / ✓ count / ❌ error)
- Both buttons are daemon threads; all UI via `_ui_queue.put()`

`🍪 PER-PLATFORM COOKIES` section (per-platform):
- Each platform row: label + path label + 🦁 CDP + 🔄 yt-dlp + 📂 Browse + 🗑 Clear
- `_extract_platform_cdp(key, lbl)` → CDP filtered extraction
- `_extract_platform_cookie(key, lbl)` → yt-dlp filtered extraction
- `_pc_extract_btns` list contains BOTH 🦁 and 🔄 for all 5 platforms (10 buttons total)
- ALL buttons disabled during any extraction → prevents profile lock race
- `_pc_extract_status`: shared label below platform rows

`🔧 YT-DLP ENGINE` section:
- `🔧 Cài keyring` button → `_install_keyring()` (daemon thread, `_ui_queue.put()`)
- `_keyring_status`: shows `✓ keyring X.X` or `⚠ Chưa cài` on startup
- `_keyring_installed_text()`: static helper checking importability of `keyring` package

**Widget refs — all registered in `_on_theme()`:**
`_card_cookies`, `_pc_lbls`, `_pc_browse_btns`, `_pc_clear_btns`, `_pc_extract_btns`,
`_pc_extract_status`, `_extract_global_btn`, `_extract_cdp_btn`, `_extract_global_status`,
`_extract_global_hint`, `_keyring_btn`, `_keyring_status`

**Per-platform cookie UI (BUG AU):**
- `_browse_platform_cookie(key, lbl)`: copies to `cookies/platform_key_filename.txt` (safe dir)
- `_clear_platform_cookie(key, lbl)`: calls `config.set_cookie_for_platform(key, "")`

**Python 3.14 thread-safety (BUG AZ):** All 8 worker functions use `_ui_queue.put()`.
`_drain_ui_queue()` polls at 150 ms.

`_clear_all_data()`: active task guard + confirmation + `clear_history()` + `reset_to_defaults()`
+ navigate home. UI thread only.

---

### `ui/tabs/special_dl_tab.py` (470 lines)
Special Downloads tab — handles platforms the main yt-dlp/gallery-dl pipeline
cannot process. Placed in SYSTEM section of sidebar.

**Isolation contract:**
- Zero imports from `YtDlpEngine`, `DownloadManager`, `DownloadService`, `EventBus`.
- Only shared state: `config` object (read-only: `config.download_dir`).
- Errors here CANNOT affect normal downloads.
- Files downloaded here do NOT appear in `HistoryRepository` / History tab.

**Supported platforms:** `facebook_story` (CDP/Playwright) only. Threads engine removed in v16.3.0 due to high API maintenance cost (see CHANGELOG).

**UI flow:**
```
Platform dropdown → URL entry → browser selector (brave/chrome) → ⬇ Tải về
    → _worker thread → _run_facebook_story() → download_story()
    → success: _btn_row shown (📂 Mở thư mục | 🗑 Xoá lịch sử)
    → error:   _retry_row shown (🔄 Thử lại | 🗑 Xoá lịch sử)
```

**Key widgets:**
- `_dl_btn` — disabled while `_running=True` (prevents double-submit)
- `_btn_row` — shown only on success; `📂 Mở thư mục` uses `explorer /select,<path>`
- `_retry_row` — shown only on error
- `_progress` — CTkProgressBar (0–1 float)
- `_last_dest: Path | None` — holds path of last successfully downloaded file

**Dead code removed (v16.1.0):**
- `self._worker_q` and `import queue` — declared but never used (re-added correctly with `_ui_queue`).
- `_delete_file()` method — removed; button replaced with `🗑 Xoá lịch sử`.

**Bug fixed (v16.1.0):**
- `_on_download()` no longer calls `self._open_btn.pack_forget()` before the
  worker thread starts — this was hiding the button inside `_btn_row` permanently.

**Python 3.14 thread-safety (v16.1.0):**
- `self._ui_queue: queue.Queue` — drained every 50 ms via `_drain_ui_queue()`
- All 7 `self.after(0, ...)` calls in `_worker` and `_on_progress` migrated to `self._ui_queue.put()`
- `winfo_exists()` guard in `_drain_ui_queue()` prevents `TclError` on app close
- Consistent with `_ui_queue` pattern used by all other tabs (BUG AY fix)

---

### `infrastructure/downloader/facebook_story_engine.py` (681 lines)
Facebook Story downloader. Called exclusively by `SpecialDlTab`. Zero coupling
to `DownloadManager` or `DownloadService`.

**Architecture (8 steps):**
1. Browser launch — `subprocess.Popen` (user's Brave/Chrome, `--remote-debugging-port`)
2. CDP connection — `playwright.sync_api.connect_over_cdp()` (no `playwright install`)
3. Pre-page inject — `page.add_init_script(_PRE_PAGE_JS)` patches fetch/XHR
4. Navigate — `page.goto(story_url, wait_until="domcontentloaded")`
5. Intercept — 3 layers (see below), deadline-based
6. URL cleaning — strip `bytestart`/`byteend`/`range` params → full video URL
7. Download — requests stream (300s deadline) → ffmpeg fallback
8. Validate — MP4 magic bytes + min 100 KB

**Three interception layers:**
```
Layer A  page.on("request")   — outgoing request URL matches _FB_VIDEO_RE
Layer B  page.on("response")  — response Content-Type starts with "video/"
Layer C  page.evaluate(_POLL_JS) every 2s — checks window.__omni_urls,
         performance.getEntriesByType, video.currentSrc
```

**Why `connect_over_cdp` (not `playwright launch`):**
- Uses user's existing browser → preserves Facebook login session (cookies)
- No `playwright install` → no extra ~150 MB browser binary
- Playwright handles WS stability (replaces ~230 lines of raw WS code)

**Key constants:**
```python
_FB_VIDEO_RE   # regex matching fbcdn.net video paths
_FB_THUMB_RE   # regex for thumbnail paths (excluded)
_PRE_PAGE_JS   # fetch/XHR patch injected before Facebook JS loads
_POLL_JS       # JS evaluating 3 detection strategies
_PLAY_JS       # JS calling video.play() to dismiss tap-to-play overlays
```

**Public API:**
```python
download_story(url, config, browser="brave", on_progress=None, timeout=60.0) -> Path
is_facebook_story_url(url) -> bool
```

**`on_progress` callback signature:** `(pct: int, speed: str, status: str) -> None`

**Filename collision (v16.1.0):** if `fb_story_<slug>.mp4` exists →
saves as `fb_story_<slug>_<timestamp>.mp4`.

**Stream deadline (v16.1.0):** `_download_cdn_url()` chunk loop aborts after
300 seconds; partial file deleted; falls through to `_ffmpeg_download`.

**Cookie note:** `platform_cookies["facebook"]` from Settings tab is NOT used.
Authentication is provided by the user's browser profile (via `contexts[0]`).
`_get_cookie_path()` / `_cleanup_cookie()` were removed (dead code, v16.1.0).

**Security:**
- CDP port bound to `127.0.0.1` only; open ~45s during download
- CDN URL logged at 80 chars max (no user-identifiable data in fbcdn.net URLs)
- Browser crash flag cleared after `proc.terminate()` (prevents Brave restore dialog)

---

### `ui/components/status_bar.py` (264 lines)
Bottom status bar. Polls download tasks every 800 ms (active) / adaptive. Network
connectivity check via `_do_net_check()` background thread every 15 s.

**Python 3.14 thread-safety (BUG BA):** `_do_net_check()` posted result to UI via
`self.after(0,...)` — crash on Python 3.14. Fixed with `_drain_ui_queue()` (100 ms).
`_do_net_check()` now uses `self._ui_queue.put(lambda result=ok: self._update_net(result))`.

### `ui/components/download_item_widget.py` (445 lines)
`refresh()` called every 500ms. Pause button disabled for `source_engine="gallery_dl"` tasks.

---

### `ui/components/toolbar.py` (302 lines)
`_start_analyse()` has `try/except Exception: self._reset_btn()` guard (BUG R).

**Python 3.14 thread-safety (v16.3.0):**
- `self._ui_queue: queue.Queue` — initialized in `__init__`
- `_drain_ui_queue()` polls every 50 ms; `winfo_exists()` guard prevents `TclError` on app close
- `_safe_done` and `_safe_error` callbacks (invoked from background thread via `analyse_url`)
  migrated from `self.after(0, ...)` → `self._ui_queue.put(...)` (BUG AY pattern)
- `after(50/100/4000)` calls remain — these run on the UI thread and are safe

---

### `ui/themes/tokens.py` (529 lines)
`_ThemeManager` singleton `T`. 10 themes. 34 tokens per palette.

---

## Data flows

### Video download (yt-dlp)
```
Toolbar → analyse_url → YtDlpEngine.extract_info → MediaInfo(source_engine="yt_dlp")
→ start_download → DownloadManager._run_task → YtDlpEngine.download
→ progress_hook → task.wait_if_paused()
→ COMPLETED → HistoryRepository.add
```

### Profile / channel / playlist
```
Toolbar → analyse_url → YtDlpEngine.extract_info
  → _PROFILE_URL_RE.search(url)? YES
  → _extract_playlist_flat(url, opts)
    → yt-dlp extract_flat="in_playlist" (1 API call, no per-video extraction)
    → `ignoreerrors=True` skips deleted/geo-restricted entries silently
    → _collect() flattens nested playlists
    → entry["url"] collected for each valid entry
  → MediaInfo(playlist_entries=[url1, url2, ...], playlist_title="...")
→ HomeTab.on_analysis_done()
  → info.playlist_entries non-empty
  → navigate_to("batch")
  → after(50, batch_tab.load_playlist(urls, title))
→ BatchTab.load_playlist()
  → clear existing batch
  → populate textarea (capped at MAX_BATCH_URLS=50)
  → _start_batch_analyse() → sequential analyse_url per entry
  → user reviews, queues, downloads individually
```

### Image download (gallery-dl)
```
Toolbar → analyse_url → YtDlpEngine raises photo error
→ _should_fallback_to_gallery_dl() → GalleryDlEngine.extract_info
→ MediaInfo(source_engine="gallery_dl", formats=[])
→ HomeTab: is_photo detected → hides quality picker
→ start_download → DownloadManager._run_task → GalleryDlEngine.download
→ stdout: one path per file → progress by file count
→ COMPLETED → HistoryRepository.add
```

### Live Monitor — direct URL
```
LiveMonitorTab._trigger_check → service.analyse_url(live_url)
→ YtDlpEngine.extract_info → MediaInfo(is_live=True)
→ _on_check_done → is_live=True → _start_recording
→ service.start_download(output_ext="ts") → DownloadTask
→ _refresh_recording_items polls task every 5s
→ COMPLETED → _MonitorState.ENDED
```

### Live Monitor — Instagram profile watch
```
LiveMonitorTab._trigger_check → service.check_profile_live(profile_url)
→ DownloadService spawns thread → check_instagram_live(username, cookie)
→ GET https://i.instagram.com/api/v1/users/web_profile_info/?username=...
→ parse live_broadcast_id from JSON
→ on_done(live_url) if live, on_done(None) if not live
→ _on_profile_check_done → live_url present?
    YES: service.analyse_url(live_url) → MediaInfo → _start_recording
    NO:  back to WAITING
```

### Facebook Story (Special tab)
```
SpecialDlTab._on_download
→ threading.Thread → _worker → _run_facebook_story()
→ facebook_story_engine.download_story(url, config, browser)
    → _cdp_intercept(url, browser, timeout, on_progress)
        → subprocess.Popen(brave/chrome, --remote-debugging-port=N)
        → playwright.connect_over_cdp("http://127.0.0.1:N")
        → page.add_init_script(_PRE_PAGE_JS)   # patch fetch/XHR
        → page.goto(story_url)
        → Layer A: page.on("request")  → video URL?
        → Layer B: page.on("response") → video MIME?
        → Layer C: page.evaluate(_POLL_JS) every 2s
        → proc.terminate() in finally
    → _download_cdn_url(cdn_url, dest)   # requests stream, 300s deadline
    → _ffmpeg_download(cdn_url, dest)    # fallback if requests fails
    → _validate_mp4(dest)
→ SpecialDlTab._btn_row shown (📂 Mở thư mục | 🗑 Xoá lịch sử)
```
Note: result NOT written to HistoryRepository.

---

### Convert
```
ConvertTab → ConvertQueue.submit → cancel_fn
→ FfmpegConvertService._run → _fresh_encode → _run_ffmpeg
→ .part → atomic rename → final output
→ _finish_job on UI thread
```

---

## Thread model summary

| Thread | Owns | Must NOT |
|---|---|---|
| UI (main) | All CTk calls, `_active_count`, `_jobs`, `_clear_all_data()`, `LiveMonitorTab._remove_item()` | block, subprocess |
| DownloadManager workers | DownloadTask mutations (under `_lock`) | touch UI directly |
| ConvertQueue workers | FFmpeg subprocess | touch UI directly |
| Analysis threads | `extract_info()` (yt-dlp or gallery-dl) | touch UI directly |
| gallery-dl workers | gallery-dl subprocess + stderr drain | touch UI directly |
| Profile check threads | `check_instagram_live()` HTTP call | touch UI directly |
| ffprobe threads | `FfmpegMediaInfo` | touch UI directly |
| Encoder detection | `detect_available_encoders()` | touch UI directly |
| **Special tab worker** | **`download_story()` — CDP + requests/ffmpeg** | **touch UI directly via `_ui_queue.put()` — migrated (v16.1.0)** |
| **Toolbar analyse callbacks** | **`_safe_done` / `_safe_error` via `analyse_url`** | **`_ui_queue.put()` — migrated (v16.3.0)** |

Cross-thread UI: background threads MUST use `_ui_queue.put(fn)` — never `widget.after()` from a worker thread (BUG AY). `after(0, ...)` is only safe when called from the UI thread itself.

---

## Security model

| Threat | Mitigation |
|---|---|
| Command injection | `shell=False` on all subprocess calls |
| Path traversal (CWE-22) | Cookie path via `Path.parents`, not `str.startswith` |
| SSRF (thumbnail) | RFC-1918 / loopback / link-local blocked |
| Supply chain (yt-dlp) | SHA-256 verified against PyPI JSON API |
| Supply chain (gallery-dl) | Same SHA-256 verification |
| Instagram API SSRF | `check_instagram_live()` only calls fixed Instagram domain |
| URL scheme | `is_valid_url()` — http/https only |
| Extra args injection | `_SAFE_EXTRA_OPTS` allowlist |
| Cookie forwarding | File must reside inside `config_path.parent/` |
| Instagram cookie scope | `check_instagram_live()` sends only `sessionid`, `csrftoken`, `ds_user_id`, `mid` |

---

## Configuration keys (complete)

| Key | Default | Notes |
|---|---|---|
| `download_dir` | ~/Downloads/OmniDL | |
| `theme` | "dark" | |
| `max_concurrent` | 3 | clamped [1-10] |
| `max_retries` | 3 | clamped [0-10] |
| `proxy` | "" | validated on save |
| `use_cookies` | False | |
| `cookies_browser` | "chrome" | |
| `cookie_file` | "" | global fallback — shared by all engines |
| `platform_cookies` | {} | per-platform paths: tiktok, instagram, facebook, twitter, threads |
| `embed_thumbnail` | True | |
| `embed_metadata` | True | |
| `default_quality` | "bestvideo+bestaudio/best" | |
| `default_format` | "mp4" | |
| `history_limit` | 500 | clamped [10-5000] |
| `extra_args` | "" | allowlisted keys only |

---

## Test suite structure

| File | Covers |
|---|---|
| `test_download_task.py` | DownloadTask domain model |
| `test_download_manager.py` | Retry, routing, gallery-dl |
| `test_download_service.py` | Orchestration, gallery fallback |
| `test_yt_dlp_engine.py` | YtDlpEngine |
| `test_yt_dlp_engine_extra.py` | `TestInstagramLive`, `TestInstagramPhoto`, edge cases |
| `test_ffmpeg_convert_service.py` | Convert, watchdog, encoder cache |
| `test_ffmpeg_locator.py` | FFmpeg discovery |
| `test_config_manager.py` | Config read/write |
| `test_config_manager_extra.py` | Config edge cases |
| `test_history_repository.py` | JSONL history |
| `test_history_repository_extra.py` | History edge cases |
| `test_event_bus.py` | EventBus pub/sub |
| `test_helpers.py` | fmt_bytes, is_valid_url, sanitise_filename |
| `test_cookie_extractor.py` | Platform domains, CDP helpers, extract flows |
| `test_cookie_storage.py` | DPAPI/Fernet encrypt/decrypt, stale cleanup |
| `test_facebook_story_engine.py` | Pure functions: `is_facebook_story_url`, `_normalize_url`, `_is_fb_video_url`, `_full_video_url`, `_validate_mp4`, `_clear_crashed_flag` |
| `test_logger_and_manager_extra.py` | Logger + download manager extras |
| `test_queue_open_folder_fix.py` | Open folder path fix |
| `test_queue_open_path.py` | Queue open path |
| `test_session10_fixes.py` | Session-10 regression |
| `test_e2e.py` | Full stack integration |
| `test_facebook_story_engine.py` | `is_facebook_story_url`, `_normalize_url`, `_is_fb_video_url`, `_full_video_url`, `_validate_mp4`, `_clear_crashed_flag` |
| `test_audit_fixes.py` | Regression: known bugs |
| `test_elite_audit_fixes.py` | Additional regression |
| `test_generator_elite.py` | Generative/property-based |
| `test_patch_fixes.py` | Patch regression |
| `test_repair_fixes.py` | Repair regression |

**Total: 27 test files, 915+ test functions (843 passed, 1 skipped as of Fixed13-BugBH)**
`test_cookie_extractor.py` — 34 tests: `TestPlatformDomains` (4), `TestFriendlyExtractError` (10), `TestExtractBrowserCookies` (8), `TestCdpHelpers` (5), `TestExtractViaCdp` (7)
`test_cookie_storage.py` — 32 tests: `TestIsEncrypted` (3), `TestEncryptCookieFile` (5), `TestDecryptToTempfile` (5), `TestCleanupStaleCookies` (5), `TestCleanupLeftoverTempFiles` (3), `TestMacosKeychain` (4), `TestMacosEncryptDecrypt` (6), `TestRoundTrip` (1)

**Mock rule:** `subprocess.Popen`, `yt_dlp.YoutubeDL`, `requests.get` — all must be mockable.
`check_instagram_live()` tests must mock `requests.get` — no real API calls.

---

## Requirements

**`requirements.txt`:**
```
customtkinter>=5.2.2      # unpinned from ==5.2.2; older exact pin had Canvas bugs on Python 3.13
yt-dlp>=2025.1.1          # lower-bound raised; 2024.1.1 is 14+ months stale
gallery-dl>=1.27.0        # image/gallery downloader — fallback for IG photos, Twitter images
Pillow>=10.3.0            # >=10.3.0 patches CVE-2024-28219 (ImageMath buffer overflow)
requests>=2.31.0
packaging>=23.0
platformdirs>=4.0.0       # SEC-3: platform-appropriate user-data directories
PySocks>=1.7.1            # required by yt-dlp for SOCKS4/5 proxy support
keyring>=24.0.0           # yt-dlp: decrypt Brave/Chrome 127+ App-Bound cookies;
                          # OmniDL: store macOS Keychain encryption key (BUG BC)
cryptography>=41.0.0      # Fernet (AES-128-CBC + HMAC-SHA256) for macOS cookie at-rest encryption
playwright>=1.40          # Facebook Story CDP via connect_over_cdp() — no playwright install needed
```

**`requirements-dev.txt`:**
```
pytest>=8.0.0
pytest-cov>=5.0.0
pyinstaller==6.19.0
types-requests>=2.31.0
```

---

## CI Pipeline (`.github/workflows/ci.yml`)

Two parallel jobs. Gate order within `test`: **ruff → mypy → pytest**.

**Job 1 — `test`** (matrix: Python `["3.11", "3.12", "3.13"]`):

```
pip install ruff mypy types-requests
ruff check . --select=E,F,B,I --exclude=tests/,build/,venv_build/
mypy domain/ app/ infrastructure/ utils/ --ignore-missing-imports --no-strict-optional
pytest --ignore=tests/test_e2e.py --ignore=tests/test_logger_and_manager_extra.py
       --cov=. --cov-report=term-missing --cov-report=xml:coverage.xml
```

**Job 2 — `security`** (Python 3.13, runs in parallel with `test`):

```
pip install bandit pip-audit
bandit -r . -x tests/,build/,venv_build/ -ll   # MEDIUM+ severity fails build
pip-audit --requirement requirements.txt        # HIGH/CRITICAL CVE fails build
```

Both jobs upload artifact reports (7-day retention). `concurrency` group cancels redundant runs on the same branch.

**Python version policy:**

| Environment | Version | Purpose |
|---|---|---|
| Local dev venv | 3.14.3 | Catch forward-compat bugs early (BUG AY confirmed here) |
| CI matrix | 3.11, 3.12, 3.13 | Gate — must pass before merge |
| EXE build (`build.yml`) | 3.13 | Stable PyInstaller support |

⚠ **Known gap:** Local (3.14) ≠ CI (3.11–3.13) ≠ build (3.13). Bugs found locally on 3.14
may not be caught by CI until 3.14 is added to the matrix (planned when 3.14 reaches stable,
~Oct 2026). All 3.14-specific fixes must be verified to not break 3.11–3.13 behavior.

**Coverage omit** (`setup.cfg [coverage:run]`):
`ui/*`, `tests/*`, `build/*`, `main.py`,
`infrastructure/downloader/gallery_dl_engine.py`, `utils/instagram_live_checker.py`
— these require external resources (UI/subprocess/network) and cannot be tested headlessly.

✅ **`infrastructure/downloader/facebook_story_engine.py`** — now covered by
`tests/test_facebook_story_engine.py` (76 tests across 7 functions:
`is_facebook_story_url`, `_normalize_url`, `_is_fb_video_url`, `_full_video_url`,
`_validate_mp4`, `_clear_crashed_flag`, `_find_browser_exe`).
`_find_browser_exe` covers Windows, macOS (/Applications + ~/Applications), not-found, Linux rejection.
`fail_under = 80` is no longer at risk from this module.

Note: `ui/tabs/special_dl_tab.py` is covered by `ui/*` omit — no impact on coverage threshold.

**`fail_under = 80`** — must NOT be lowered.

---


## Known limitations (not bugs, by design)

| Limitation | Reason |
|---|---|
| Pause does NOT work during PROCESSING (FFmpeg merge) | FFmpeg subprocess cannot be paused |
| Pause does NOT work for gallery-dl downloads | Subprocess-based; button disabled |
| Instagram carousel downloads full set via gallery-dl | `noplaylist=True` with yt-dlp gives 1 image; gallery-dl gives all |
| Live Monitor: interval setting not persisted | Ephemeral; acceptable for live monitoring sessions |
| Live Monitor: `.ts` files show Convert button | `output_ext="ts"` triggers the Convert button — useful, not a bug |
| `max_concurrent` change requires restart | `ThreadPoolExecutor` cannot resize; warning label shown |
| Live Monitor: profile watch requires fresh cookie | Instagram API rejects stale sessions; cookie warning shown |
| Live Monitor: profile watch Instagram only | Other platforms don't expose live status via public JSON API |
| Instagram cookie quality warning | Old cookie session → lower resolution from Instagram API |
| Batch analysis sequential | Rate-limit protection; parallel would trigger Instagram/TikTok blocks |
| Playlist capped at 50 | `MAX_BATCH_URLS = 50` — prevents rate limit hammering; user sees truncation toast |
| Profile `_extract_playlist_flat` slow for large channels | 500+ video channels still take 20-60s; no progress feedback during analysis |
| Per-platform cookie not used by `use_cookies` browser mode | Browser cookie extraction is global only — per-platform only applies to `.txt` files |
| Python 3.14 `self.after()` | Fixed in all **7** files (BUG AY/AZ/BA + v16.3.0): `convert_tab` (`_poll_ui_queue` 50ms), `live_monitor_tab` (in `_poll()`), `batch_tab` (100ms), `download_item_widget` (50ms), `status_bar` (100ms, BUG BA), `settings_tab` (150ms, BUG AZ), `toolbar` (50ms, v16.3.0). Bug confirmed on Python 3.14.3 local — 3.14 enforces strict main-thread-only Tkinter access; `_ui_queue` pattern is the permanent fix. CI matrix covers 3.11–3.13; 3.14 not yet added. |
| Special tab downloads not in History | Intentional isolation; HistoryRepository not touched by Special tab |
| Facebook Story: CDP port open ~45s | Trade-off for CDP method; bound to 127.0.0.1 only |
| Facebook Story: requires user login in Brave/Chrome | Session lives in browser profile; no cookie file used |
| Facebook Story: macOS + Windows | Brave/Chrome paths resolved per-platform; Linux not supported |

---

*End of PROJECT_MAP.md — v16.3.0*
