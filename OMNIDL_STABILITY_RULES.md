# OmniDL Stability Rules
Version: 15.1
Status: ACTIVE
Scope: Entire OmniDL codebase — AI-assisted development
Last updated: 2026-03-21 (toolbar _ui_queue migration; Editor + Threads removal; coverage omit fix; deno_locator protection added)

---

## DOCUMENT AUTHORITY

Priority order (highest → lowest):

1. Convert Architecture v2
2. **This document** — defines HOW changes may occur, what must not be broken
3. `PROJECT_MAP.md` — describes WHAT exists and where
4. Task-specific prompts

This document is a **regression prevention contract**.
All bugs listed in Section 16 are CONFIRMED FIXED and verified in code.
AI tools must treat all rules here as STRICT constraints.

---

## SECTION 1 — CORE STABILITY PRINCIPLES

1. The architecture is considered stable. See `PROJECT_MAP.md` for the full map.
2. Fixes must be **minimal and localized** — touch only what the bug requires.
3. Large refactors are forbidden unless explicitly requested by the user.
4. UI must never perform blocking operations.
5. Worker threads must never mutate UI state directly.
6. FFmpeg and yt-dlp execution flows must remain unchanged.
7. Deterministic behavior has priority over feature expansion.
8. **Python version policy:** Local dev runs 3.14.3 (catches forward-compat bugs early).
   CI gate runs 3.11–3.13. EXE is built with 3.13. Any fix for a 3.14-specific bug
   MUST be verified to not break 3.11–3.13 behavior before merge.

---

## SECTION 2 — PROTECTED SYSTEMS

The following systems must not be modified without explicit approval:

- `ConvertQueue` scheduling logic and semaphore concurrency
- FFmpeg watchdog monitoring (`_run_ffmpeg`)
- GPU encoder detection pipeline (2-phase detection + cache)
- UI ↔ Service separation (tabs never import infrastructure)
- `.part` temporary output workflow
- Cancel mechanism (`cancel_event`, `_kill_proc`, `ConversionCancelledError`)
- Download retry loop in `DownloadManager._run_task` (Fixed6)
- `_HARD_ERROR_KEYWORDS` list in `DownloadManager`
- `_on_close` dual-check for active downloads AND conversions (Fixed7)
- `_analysing` flag try/except guard in `Toolbar._start_analyse` (Fixed7)
- `ThumbnailService` routing via `ServiceFacade.fetch_thumbnail` (Fixed7)
- `_PALETTES` registry and `_CTK_BASE` mapping in `tokens.py` (Fixed11)
- Quality card grid layout in `ConvertTab._build()` (Fixed12)
- Pack order in `ConvertTab._build()`: bottom bar before scroll (Fixed12)
- `GalleryDlEngine` routing via `source_engine` in `DownloadManager._run_task`
- `_validate_cookie_path` in `yt_dlp_engine.py` (CWE-22 path traversal guard)
- `cookie_storage.py` — DPAPI encrypt/decrypt pipeline; `encrypt_cookie_file`, `decrypt_to_tempfile`, `cleanup_stale_cookies`, `cleanup_leftover_temp_files`
- `encrypt_cookie_file()` — writes `.enc` BEFORE deleting `.txt` (atomicity); falls back gracefully on non-Windows or DPAPI error
- `decrypt_to_tempfile()` — caller MUST delete returned temp file in `finally`; raises RuntimeError on decryption failure
- `_prepare_cookie_for_use()` in `yt_dlp_engine.py` — decrypt gate; returns `(usable_path, is_temp)` pair
- `extract_browser_cookies()` in `cookie_extractor.py` — yt-dlp extraction logic; temp-file-first pattern; platform domain filter
- `extract_via_cdp()` in `cookie_extractor.py` — CDP extraction pipeline; must use real profile dir, not temp; must call `Network.enable` before `Network.getAllCookies`; must terminate proc in `finally`
- `_cdp_get_page_ws_url()` — uses `/json/list` page target, NOT `/json/version` browser endpoint; page target required for `Network.*` domain
- `_cdp_get_all_cookies()` — must call `Network.enable` (id=1) before `Network.getAllCookies` (id=2); without enable, Chrome returns `[]`
- `_is_browser_running()` — uses `tasklist` list-form (no `shell=True`); returns False on non-Windows; safe default
- `_find_browser_profile()` — returns real User Data dir; must NOT be used when browser is running (file lock)
- `_PLATFORM_DOMAINS` in `cookie_extractor.py` — includes `youtube` with `google.com` (auth); do NOT remove `google.com`
- `_COOKIE_PLATFORM_MAP` in `yt_dlp_engine.py` — now includes `youtube.com` and `youtu.be`; Twitch/Vimeo still NOT included
- `_PLATFORM_ANALYSIS_DELAY` in `batch_tab.py` — per-platform delays; instagram=2.5s, tiktok=2.0s, facebook/twitter=1.5s, youtube=0.8s
- `_get_analysis_delay(url)` in `batch_tab.py` — uses urlparse hostname; called from `_delayed_analyse()` daemon thread
- `_retry_errors()` in `batch_tab.py` — resets ERROR items to PENDING; preserves READY/QUEUED items; fires new batch token
- `_url_lbl` in `DownloadItemWidget` — shown on FAILED status; uses `getattr(self, "_url_lbl", None)` guard (tests use `__new__`)
- `_validate_cookie_path_raw()` auto-fallback — if `.txt` absent but `.enc` exists: silently uses `.enc` (encrypt renamed it)
- `_validate_cookie_path()` (global) same auto-fallback — `.txt` config → `.enc` on disk → accepted
- `cryptography>=41.0.0` — required for macOS Fernet encryption; do NOT remove from requirements
- `_friendly_error` in `yt_dlp_engine.py` — `msg[:200]` fallback AND standalone `"429"`/`"too many requests"` check
- `self._powered_lbl` in `main_window.py` — version label `v16.0.0` stored for theme refresh (NOT the old "Powered by yt-dlp" badge removed in BUG AR)
- `LiveMonitorTab._monitor_token` stale-callback guard — increment on remove/clear only
- `LiveMonitorTab._checking` sequential lock — must be reset BEFORE the token check in both callbacks
- `FfmpegConvertService._build_cmd()` profile: MUST be `-profile:v main -level:v 4.1` — NOT `high`/`4.0` (BUG AX)
- `YtDlpEngine` live outtmpl: MUST use hardcoded `.ts` extension — NOT `%(ext)s` (BUG AX)
- `ConvertTab._ui_queue` + `_poll_ui_queue()`: the ONLY safe bridge for bg→UI thread communication
  Background threads MUST use `self._ui_queue.put(fn)` — NEVER `self.after()` or `self.winfo_exists()` (BUG AY)
- Same `_ui_queue` pattern required in ALL 7 affected classes: `ConvertTab`, `LiveMonitorTab`,
  `BatchTab`, `DownloadItemWidget`, `StatusBar`, `SettingsTab`, `Toolbar` (BUG AY/AZ/BA + v16.3.0)
- `QueueTab.DownloadItemWidget` MUST receive `on_convert=` param — enables → MP4 button (Fix)
- `_apply_available_encoders()` and `_refresh_status()` MUST have `winfo_exists()` guard at top
- `_PROFILE_URL_RE` in `yt_dlp_engine.py` — conservative profile/channel URL regex; single-video URLs must NOT match
- `_resolve_cookie()` in `yt_dlp_engine.py` — per-platform cookie resolution using urlparse (not regex substring)
- `_validate_cookie_path_raw()` — CWE-22 guard for per-platform paths; same `Path.parents` logic as `_validate_cookie_path()`
- `_COOKIE_PLATFORM_MAP` — exact hostname → platform key mapping (urlparse-based, not regex)
- `has_cookies` check in `extract_info()` — must include `any(config.platform_cookies.values())`

**Minimum dependency versions (from `requirements.txt`):**
- `gallery-dl>=1.27.0` — lower versions may lack `--dump-json` or `-q` flag support

**Deno PATH injection (`utils/deno_locator.py`):**
- `locate_deno()` — 3-step resolution: PyInstaller bundle → `resources/deno/` → `shutil.which`
- `get_deno_env()` — returns modified env dict with deno directory prepended to PATH
- `extract_info()` and `download()` in `yt_dlp_engine.py` MUST call `get_deno_env()` and pass result to subprocess environment; removing this breaks YouTube throttle-bypass on bundled builds
- Do NOT hardcode a deno path — the 3-step resolution order handles frozen, source, and system installs

**Exception — Surgical Fix Rule:**
A protected system MAY be modified ONLY when required to fix an explicitly listed bug,
the architecture behavior remains unchanged, and the modification is minimal and localized.

---

## SECTION 3 — THREAD SAFETY RULES

**UI thread** owns:
- All CTk widget calls
- `ConvertTab._active_count`, `_jobs`, `_cards`
- `DownloadTask` field reads via `snapshot()`
- `SettingsTab._clear_all_data()` — UI thread only
- `LiveMonitorTab._clear_all()` and `_remove_item()` — UI thread only

**Worker threads** do:
- Encoding and subprocess monitoring
- Progress parsing and field mutation (under `task._lock`)
- Calling callbacks ONLY — never touching UI state directly
- `GalleryDlEngine.download()` subprocess (drains stderr via daemon thread)
- `DownloadService.check_profile_live()` Instagram API call — daemon thread, same pattern as `analyse_url()`

**Rules:**
- `after(0, ...)` on the **UI thread** is safe for deferred UI calls. NEVER call `self.after()` from a background/worker thread — use `_ui_queue.put()` instead (BUG AY)
- `_active_count` in ConvertTab must only be decremented inside `_finish_job()` on the UI thread
- `DownloadTask.snapshot()` must be used by UI poll loop (atomic multi-field read)
- `LiveMonitorTab` background callbacks MUST dispatch via `self._ui_queue.put()` — NOT `self.after(0, ...)` directly (BUG AY)
- `Toolbar` analyse callbacks (`_safe_done`, `_safe_error`) MUST dispatch via `self._ui_queue.put()` — NOT `self.after(0, ...)` (v16.3.0)

---

## SECTION 4 — FFmpeg EXECUTION SAFETY

- Subprocess execution must **NEVER** use `shell=True`
- stdout and stderr must be drained by dedicated daemon threads
- Stalled processes must be terminated by the watchdog (30s silence threshold)
- On POSIX: `start_new_session=True` + `os.killpg` to kill full process group
- On Windows: `proc.kill()` only
- `stderr_thread.join(timeout=5.0)` and `stdout_thread.join(timeout=5.0)` — both required

**gallery-dl subprocess follows same rules:**
- `shell=False` (Popen list form)
- `stderr` drained via daemon thread (`_drain_stderr`) to prevent deadlock
- `proc.kill()` on cancel; `stderr_thread.join(timeout=5.0)` required

---

## SECTION 5 — TEMPORARY FILE SAFETY (CONVERT)

- Resume encoding NOT supported (timestamp discontinuity)
- Stale `.part` files MUST be deleted before each new encode
- `_fresh_encode` MUST have `except Exception: temp_output.unlink(missing_ok=True); raise`
- `scan_folder_for_media` must exclude `.part.mp4` files

---

## SECTION 6 — ENCODER DETECTION STABILITY

- `_use_cache = ffmpeg_bin is None` must gate ALL `_encoder_cache_set()` calls
- Both cache read AND write skipped when `ffmpeg_bin is not None`

---

## SECTION 7 — DOWNLOAD RETRY RULES (Fixed6)

**Location:** `DownloadManager._run_task()` and `DownloadManager._HARD_ERROR_KEYWORDS`

- `max_attempts = max(1, config.max_retries + 1)` — always at least 1 attempt
- Backoff: `min(2 ** (attempt - 1), 30)` seconds (1s, 2s, 4s, capped at 30s)
- Progress/speed/eta MUST be reset to 0/"" before each retry
- `DOWNLOAD_PROGRESS` event MUST be published after progress reset
- Cancellation MUST be checked before each attempt AND after each sleep
- Hard errors MUST skip retry and break immediately
- Routing to `GalleryDlEngine` happens inside `_run_task` — retry rules apply equally

**Current `_HARD_ERROR_KEYWORDS` (Fixed12-InstagramLive — final):**
```
"private", "removed", "not found", "404", "login",
"unsupported url", "cancelled by user", "age", "unavailable",
"this video is unavailable",
"checkpoint", "challenge_required",
"content not available", "this content isn", "This content isn't available",
"geo-restricted", "not available in your country",
"copyright", "blocked", "suspended", "members only", "subscribers only",
"no video in this post", "no video formats found",
"extractor error",
```
Do NOT remove keywords. Adding new keywords is permitted.

**gallery-dl error prefix convention:**
Every `GalleryDlEngine._friendly_error()` return starts with an English keyword:
`"login: …"`, `"not found: …"`, `"blocked: …"`, `"unsupported url: …"`, `"private: …"`.
Do NOT translate to pure Vietnamese — breaks keyword matching.

**Test rule:** Failing engine mocks must use `max_retries=0` or a hard-error message.

---

## SECTION 8 — ERROR HANDLING GUARANTEES

- Errors must never crash the UI
- `ConversionCancelledError` caught separately; must NOT trigger GPU→CPU retry
- Hard download errors must NOT be retried (Section 7)
- `_friendly_error()` in `yt_dlp_engine.py` — `msg[:200]` fallback MUST remain as final catch-all
- `_friendly_error()` 429 check: condition is `"429" in msg_l or "too many requests" in msg_l` as standalone OR combined with `"rate"` — both branches required so `"HTTP Error 429: Too Many Requests"` (no "rate" keyword) is still caught

**Live Monitor error handling:**
- `check_instagram_live()` raises only `RuntimeError` for all errors — there is NO separate
  `TransientError` class. `DownloadService.check_profile_live()` catches all `Exception` and
  forwards the message string to `on_error()`.
- `LiveMonitorTab._on_check_error()` classifies by keyword substring check on the error message:
  - Keywords `"private"`, `"not found"`, `"login"`, `"checkpoint"`, `"removed"`, `"404"` →
    `_MonitorState.ERROR` — no retry
  - No keyword match (timeout, rate limit, connection error) → `_MonitorState.WAITING` — retried
    on the next poll cycle
- Hard error messages from `check_instagram_live()` are prefixed with English keywords
  (e.g. `"login: Cookie…"`, `"not found: Tài khoản…"`) so the keyword check fires correctly

---

## SECTION 9 — CANCEL MECHANISM RULES (CONVERT)

- `ConvertQueue.submit()` creates `threading.Event` per job, returns `cancel_event.set`
- Cancel event flows: `_run` → `_convert_sync` → `_try_encode_with_fallback` → `_fresh_encode` → `_run_ffmpeg`
- `_run_ffmpeg` watchdog checks `cancel_event.is_set()` every 1 second
- `_kill_proc`: `os.killpg` on POSIX, `proc.kill()` on Windows
- `FileCard._cancel_btn` shown when state is QUEUED or CONVERTING

**gallery-dl cancel:** `task.is_cancellation_requested` → `proc.kill()`. No `cancel_event`.

**Live Monitor cancel:** `_cancel_item(item)` → `service.cancel_download(item.task_id)` →
resets item to WAITING with `last_check=0.0` for immediate re-check.

---

**Python 3.14+ threading constraint (BUG AY — confirmed crash on 3.14.3):**
- Python 3.14 enforces strict main-thread-only access for ALL Tkinter APIs,
  including `self.after()`, `self.winfo_exists()`, and `widget.configure()`.
  Bug confirmed on local venv running Python 3.14.3 — previous versions were lenient
  due to GIL timing; 3.14 is not.
- The ONLY thread-safe Tkinter call is `queue.Queue.put()` from a background thread.
- All background threads in `ConvertTab` post callables to `self._ui_queue`
  (a `queue.Queue`). `_poll_ui_queue()` drains it every 50 ms on the UI thread.
- This pattern must be used in any future background thread added to any UI class.
- All 7 affected files now fixed: `convert_tab.py`, `live_monitor_tab.py`,
  `batch_tab.py`, `download_item_widget.py`, `status_bar.py`, `settings_tab.py`,
  `toolbar.py` — see BUG AY/AZ/BA + v16.3.0.
- CI matrix currently covers 3.11–3.13; 3.14 not yet added but fix is forward-compatible.


## SECTION 10 — UI WIDGET SAFETY RULES

- Never use `winfo_children()[N]` — always use stored dict references
- `_custom_quality` must be `tk.StringVar(value="23")` — NOT `tk.IntVar`
- `_validate_custom_quality()` bound to `<FocusOut>` and `<Return>`
- Quality card row (`q_row`) MUST use **grid** — NOT `pack(side="left")`
- Bottom action bar (`bar`) MUST be packed `side="bottom"` BEFORE `_scroll`

**Pause button for gallery-dl tasks:**
- `is_gallery_dl = (task.media_info is not None and getattr(task.media_info, "source_engine", "yt_dlp") == "gallery_dl")`
- `task.media_info is not None` guard MUST remain — prevents AttributeError

**Title bar (BUG AR — permanent):**
- `_ytdlp_badge` REMOVED from title bar — do NOT restore
- OLD `_powered_lbl` ("Powered by yt-dlp" badge) REMOVED from sidebar — do NOT restore
- NEW `self._powered_lbl` = version label `v16.0.0` in sidebar bottom strip — REQUIRED for theme refresh, do NOT remove
- Engine versions visible in Settings → YT-DLP ENGINE / GALLERY-DL ENGINE

**Live Monitor widget safety:**
- Every widget `configure()` must be guarded with `winfo_exists()`
- `_on_theme()` must update all item rows in loop
- `_empty_lbl` toggles between `pack()` / `pack_forget()` based on `len(self._items)`
- `open_folder_btn` shown ONLY when `state == ENDED and item.filename` non-empty (BUG AW)
- `item.filename` stored from `snap.get("filename")` when `status == COMPLETED` (BUG AW)

---

## SECTION 11 — LOGGING SAFETY RULES

- Console handler wraps `sys.stdout` with `io.TextIOWrapper(buffer, errors="replace")` ONLY when
  `hasattr(sys.stdout, "buffer") and hasattr(sys.stdout.buffer, "raw")` — i.e. real OS file only.
  In pytest/test environments `sys.stdout.buffer` has no `.raw` attribute → falls through to raw `sys.stdout`.
  This prevents pytest capture from hitting "I/O operation on closed file" at teardown.
- File handler: `encoding="utf-8"` — do NOT change
- `RotatingFileHandler`: `maxBytes=5*1024*1024`, `backupCount=3` — do NOT lower
- Test cleanup: `RotatingFileHandler.close()` MUST be called before `removeHandler()` in test teardown

---

## SECTION 12 — COOKIE FILE SECURITY RULES

- `_validate_cookie_path` uses `Path.parents` — NOT `str.startswith`
- `_browse_cookie_file` must `shutil.copy2` to `config_path.parent / "cookies" / filename`
- Config saves the DESTINATION path — never source path
- `GalleryDlEngine._base_cmd()` and `check_instagram_live()` both read `config.cookie_file`
- `check_instagram_live()` uses `http.cookiejar.MozillaCookieJar` — no separate credential storage

**Built-in cookie extractor (`cookie_extractor.py`) security (BUG BB — final):**

*Method 1 — yt-dlp extraction (`extract_browser_cookies`):*
- Writes to `{browser}_global_cookies.txt` or `{platform_key}_{browser}_cookies.txt` in safe dir
- Temp-file-first: writes `_tmp_<n>` then renames — never corrupts existing file on failure
- `tmp_path` always cleaned up (`unlink(missing_ok=True)`) in success and failure paths
- Platform filter: `cookie.domain.lstrip(".")` exact or suffix match — NOT substring
- `_PLATFORM_DOMAINS["threads"]` includes `instagram.com` — Threads auth is Instagram-backed
- Works for Firefox, Opera, Edge, Chrome/Brave < 127. Fails on Brave/Chrome 127+ with DPAPI error.

*Method 2 — CDP extraction (`extract_via_cdp`):*
- Browser-running guard: `_is_browser_running()` MUST be called first; True → return error (file lock)
- Real profile: `_find_browser_profile()` → real `User Data` dir; NEVER a temp/empty dir (no cookies)
- Page target: `_cdp_get_page_ws_url()` uses `/json/list` (page type) — NOT `/json/version` (browser endpoint)
  `Network.*` only works on page targets; browser endpoint always returns `[]`
- `Network.enable` MUST fire (id=1) before `Network.getAllCookies` (id=2) — without it returns `[]`
- Output files: `{browser}_cdp_cookies.txt` / `{platform_key}_{browser}_cdp_cookies.txt` — distinct from yt-dlp names
- `proc.terminate()` + `proc.wait(timeout=5)` + `proc.kill()` fallback in `finally` — no zombie processes
- `_is_browser_running()`: `tasklist` list-form — NEVER `shell=True`; returns False on non-Windows
- CDP port: 9223 default — unprivileged, avoids clashing with user's 9222 session

*SettingsTab UI (BUG BB — final):*
- `🔄 Lấy cookies từ trình duyệt` → `_extract_global_cookies()` (yt-dlp)
- `🦁 Brave/Chrome 127+ (CDP)` → `_extract_global_cdp()` (CDP)
- `🔄` per platform row → `_extract_platform_cookie()` (yt-dlp)
- `🦁` per platform row → `_extract_platform_cdp()` (CDP)
- `_pc_extract_btns` list contains BOTH 🔄 and 🦁 buttons — ALL disabled during any extraction
- Shared `_pc_extract_status` shows progress/result for all platform rows
- `_extract_global_hint` — warning label explaining which button to use
- Keyring row: `🔧 Cài keyring` button + `_keyring_status` label in YT-DLP ENGINE section
- `_on_theme()` MUST refresh all: `_pc_extract_btns`, `_pc_extract_status`, `_extract_global_btn`,
  `_extract_global_status`, `_extract_global_hint`, `_extract_cdp_btn`, `_keyring_btn`, `_keyring_status`

**Cookie security hardening (BUG BC):**
- `encrypt_cookie_file(txt_path)` — DPAPI-encrypts `.txt` → `.enc`; deletes plaintext AFTER enc written (atomicity)
- `decrypt_to_tempfile(enc_path)` — decrypts to `omnidl_dec_*.txt` in same dir; caller MUST delete in `finally`
- `_prepare_cookie_for_use(path)` in `yt_dlp_engine.py` — checks `is_encrypted()`; returns `(usable, is_temp)` pair
- Temp file cleanup: MUST happen after `extract_info()` AND after `download()` in unconditional block
- `_find_free_port()` in `cookie_extractor.py` — random port each CDP call; `port=None` default triggers it
- `--remote-debugging-address=127.0.0.1` in CDP launch command — explicit localhost-only bind
- Confirmation dialogs for global extraction (`_extract_global_cookies`, `_extract_global_cdp`) — UI thread only
- `cleanup_stale_cookies(dir, 30)` called at startup in `main.py` — auto-deletes `.txt`/`.enc` > 30 days
- `cleanup_leftover_temp_files(dir)` called at startup in `main.py` — removes `omnidl_dec_*` crash leftovers
- Non-Windows fallback: `encrypt_cookie_file` is a no-op; `decrypt_to_tempfile` returns `.txt` path as-is
- On DPAPI failure: `encrypt_cookie_file` falls back gracefully to plaintext (logged, never crashes)

**AI MUST NOT (cookie extractor + storage):**
- Call `extract_browser_cookies()` or `extract_via_cdp()` from the UI thread
- Skip temp-file pattern in `extract_browser_cookies()` — corrupts existing file on failure
- Use `/json/version` endpoint for `Network.getAllCookies` — must use `/json/list` page target
- Call `Network.getAllCookies` without first calling `Network.enable` — returns empty list
- Launch CDP with temp/empty `--user-data-dir` — no cookies in empty profile
- Launch CDP without checking `_is_browser_running()` — file lock corrupts profile
- Use `shell=True` in `_is_browser_running()` — command injection risk
- Skip `proc.terminate()`/`proc.kill()` in CDP finally block — zombie process leak
- Use old `_cdp_get_ws_url()` (browser endpoint) — must use `_cdp_get_page_ws_url()`
- Use substring domain matching in `_PLATFORM_DOMAINS` filter
- Add YouTube, Twitch, or Vimeo to `_PLATFORM_DOMAINS`
- Delete `.txt` in `encrypt_cookie_file` BEFORE `.enc` is written — data loss on failure
- Use hardcoded `port=9223` for CDP — must use `_find_free_port()` (`port=None` default)
- Bypass `_macos_get_or_create_key()` and hardcode a key — key must come from Keychain
- Remove `cryptography` from requirements — Fernet requires it on macOS
- Remove `--remote-debugging-address=127.0.0.1` from CDP launch — reopens network binding risk
- Skip decrypted temp file deletion in `yt_dlp_engine.py` — plaintext accumulates on disk
- Remove confirmation dialogs from global extraction — users must acknowledge cookie scope
- Skip `cleanup_stale_cookies` / `cleanup_leftover_temp_files` in `main.py` startup
- `_resolve_cookie(url, config)` uses `urlparse().hostname` — exact match, NOT regex substring
  Prevents subdomain spoofing: `malicious.tiktok.com.evil` does NOT end with `.tiktok.com`
- `_validate_cookie_path_raw(path, config)` applies same `safe_root in cp.parents` CWE-22 guard
  as `_validate_cookie_path()` — every per-platform path is validated before use
- Per-platform cookie filename MUST be prefixed: `f"{platform_key}_{src.name}"` to prevent
  collision when two platforms share the same source filename (e.g. both named `cookies.txt`)
- `isinstance(val, dict)` guard in `platform_cookies` property — corrupt config value
  (e.g. `null`, string) degrades to `{}` and falls back to global `cookie_file` safely
- `_COOKIE_PLATFORM_MAP` uses exact hostname tuples — YouTube, Twitch, Vimeo intentionally
  NOT included so their downloads never receive an Instagram/TikTok session cookie
- `set_cookie_for_platform(key, path)` is thread-safe — delegates to `config.set()` which
  holds `self._lock`

---

## SECTION 13 — TESTABILITY REQUIREMENTS

- All subprocess and yt-dlp calls must be mockable
- No real network calls in any test
- Mock functions for `_fresh_encode`/`_run_ffmpeg` must accept `cancel_event=None`
- Mock functions for `FfmpegConvertService._run` MUST accept `**kwargs` (or explicit `encode_settings=None, cancel_event=None`) — `ConvertQueue._worker` passes these kwargs
- `test_resume_skipped_when_no_partial` and similar tests MUST patch `_locate_ffmpeg_bin` as a staticmethod to prevent ConversionError before `_fresh_encode` is reached
- `fake_popen` in FFmpeg tests MUST create `.part.mp4` not `.mp4` — production code writes to temp path and renames
- Failing engine mocks must use `max_retries=0` or a hard-error message
- `GalleryDlEngine` tests must mock `subprocess.Popen`
- `check_instagram_live()` tests must mock `requests.get` — no real Instagram API calls
- `test_logger_uses_rotating_file_handler`: MUST call `h.close()` before `removeHandler(h)` in `finally` block — prevents cascade "I/O on closed file" errors

---

## SECTION 14 — AI MODIFICATION RULES

**Before modifying any code:**
1. Required by listed bug or explicit user request? If NO → do not modify.
2. Alters architecture? If YES → reject.
3. Smaller fix possible? If YES → use it.
4. Violates any rule here? If YES → reject.

**AI MUST NOT:**
- Merge UI and service layers
- Rewrite queue, semaphore, or retry logic
- Use `winfo_children()` positional indexing
- Use `tk.IntVar` for any entry that may transiently hold empty string
- Remove `errors="replace"` from console log handler
- Save cookie source path to config instead of safe-dir copy
- Remove keywords from `_HARD_ERROR_KEYWORDS`
- Call `_encoder_cache_set()` when `ffmpeg_bin is not None`
- Decrement `_active_count` anywhere except `_finish_job()`
- Remove the convert-active check from `_on_close()`
- Remove `try/except Exception: self._reset_btn()` from `_start_analyse()`
- Save proxy without validating scheme in `_on_proxy_focusout()`
- Remove the original `publish(event, **kwargs)` from EventBus
- Allow `HomeTab` to instantiate `ThumbnailService()` directly
- Allow `start_download()` to create duplicate tasks for active URLs
- Name a class `MediaInfo` in `ffmpeg_convert_service.py` — must be `FfmpegMediaInfo`
- Recreate `_resume_encode`
- Lower `fail_under` below 80 in `setup.cfg`
- Remove `main.py`, `gallery_dl_engine.py`, or `instagram_live_checker.py` from `[coverage:run] omit` — these require external resources (UI/subprocess/network) and cannot be tested headlessly
- Use ASCII transliteration in `_PRESETS` labels
- Import from `infrastructure/` in `BatchTab` or `LiveMonitorTab`
- Raise `MAX_BATCH_URLS = 50` without rate-limit analysis
- Change batch analysis to parallel
- Skip `is_valid_url()` in BatchTab
- Use bare `open()` without `errors="replace"` for .txt import
- Remove `_concurrent_hint` label from `SettingsTab`
- Recreate `ThreadPoolExecutor` dynamically for `max_concurrent`
- Revert quality card from grid to `pack(side="left")`
- Pack `_scroll` before `_bar` in `ConvertTab._build()`
- Increase `wraplength` above 130 in quality cards
- Use `--no-progress` in `GalleryDlEngine._base_cmd()`
- Translate gallery-dl errors to pure Vietnamese
- Set `source_engine` to non-`"gallery_dl"` in IG photo synthetic MediaInfo
- Remove `task.media_info is not None` guard from pause button logic
- Run `_clear_all_data()` in background thread
- Allow `_clear_all_data()` during active downloads/conversions
- Add `allow_unplayable_formats` back to `yt_dlp_engine.download()`
- Restore `_ytdlp_badge` to title bar (BUG AR)
- Restore the old "Powered by yt-dlp" `_powered_lbl` badge to sidebar (BUG AR) — the NEW `self._powered_lbl` (version label) is DIFFERENT and must remain
- Import `check_instagram_live()` directly in `LiveMonitorTab` — must go through `service.check_profile_live()`
- Set `_checking=False` AFTER the token check — order matters for remove-during-check safety
- Increment `_monitor_token` anywhere except `_remove_item()` and `_clear_all()` in `LiveMonitorTab`
- Change `-profile:v main` back to `high` or `-level:v 4.1` back to `4.0` — breaks iPhone playback (BUG AX)
- Use `%(ext)s` in live outtmpl — must be hardcoded `.ts` (BUG AX)
- Remove `".ts"` from `_MEDIA_EXTS` — required for live recording `task.filename` capture (BUG AX)
- Hide or omit `open_folder_btn` after recording ENDED — it must appear when `item.filename` is set (BUG AW)
- Call `self.after()` or `self.winfo_exists()` from a background thread in `ConvertTab` — use `self._ui_queue.put()` (BUG AY)
- Remove `_poll_ui_queue()` or `_ui_queue` from `ConvertTab` — they are the Python 3.14 thread-safety mechanism
- Add new background thread to `ConvertTab` without using `self._ui_queue.put()` for all UI callbacks
- Add new background thread to ANY UI class without `_ui_queue` pattern (BUG AY/AZ/BA)
- Call `self.after(0, ...)` from `_safe_done` or `_safe_error` in `toolbar.py` — use `_ui_queue.put()` (v16.3.0)
- Remove `_drain_ui_queue()` or `_ui_queue` from `toolbar.py` — required for Python 3.14 (v16.3.0)
- Create `DownloadItemWidget` in `QueueTab` without `on_convert=` parameter — hides → MP4 button
- Remove `winfo_exists()` guard from `_apply_available_encoders()` or `_refresh_status()` in `ConvertTab`
- Remove `winfo_exists()` guards from `LiveMonitorTab._on_theme()`
- Call `check_profile_live()` in parallel — must be sequential via `_checking` flag
- Remove cookie requirement guard from `_add_url()` for Instagram profile URLs
- Use regex substring matching in `_resolve_cookie()` for platform detection — must use `urlparse` hostname
- Add new platform to `_COOKIE_PLATFORM_MAP` without using exact hostname (not substring/regex)
- Call `_validate_cookie_path()` directly for per-platform paths — must use `_validate_cookie_path_raw()`
- Omit `any(config.platform_cookies.values())` from `has_cookies` check in `extract_info()`
- Widen `_PROFILE_URL_RE` to match single-video URLs (e.g. `/video/ID`, `/watch?v=`) — breaks `noplaylist=True` protection
- Use `extract_flat` approach for single-video URLs — only valid for confirmed profile/channel/playlist URLs
- Remove `ignoreerrors=True` from `_extract_playlist_flat()` opts — causes errors on unavailable entries
- Save per-platform cookie without `platform_key_` filename prefix — causes collision between platforms
- Reintroduce the two-pass `opts_pl` / `noplaylist=False` block in `extract_info()` — use `_extract_playlist_flat()` instead (BUG AV)

- Call `extract_browser_cookies()` or `extract_via_cdp()` from the UI thread — daemon thread only
- Skip temp-file pattern in `extract_browser_cookies()` — corrupts existing good file on failure
- Add YouTube/Twitch/Vimeo to `_PLATFORM_DOMAINS` in `cookie_extractor.py`
- Use substring domain matching in platform cookie filter — exact or `.`-suffix only
- Remove disable-all-extract-buttons guard — prevents tmp-file/profile lock race
- Use `/json/version` (browser endpoint) for CDP cookie extraction — must use `/json/list` page target
- Omit `Network.enable` before `Network.getAllCookies` in CDP — returns empty cookies
- Launch CDP with temp/empty `--user-data-dir` — no cookies in empty profile
- Use `shell=True` in `_is_browser_running()` — command injection risk
- Skip `proc.terminate()`/`proc.kill()` in CDP finally block — zombie process
- Restore old `_cdp_get_ws_url()` function — replaced by `_cdp_get_page_ws_url()`
- Use hardcoded `port=9223` for CDP instead of `_find_free_port()` — predictable attack window
- Remove `--remote-debugging-address=127.0.0.1` from CDP cmd — network exposure
- Delete cookie plaintext before encrypted version is written — data loss on disk failure
- Skip `_prepare_cookie_for_use()` in `yt_dlp_engine.py` — passes .enc to yt-dlp which cannot read it
- Skip temp file cleanup in `yt_dlp_engine.extract_info()` or `download()` — plaintext on disk
- Remove confirmation dialogs from `_extract_global_cookies` / `_extract_global_cdp`
- Remove `cleanup_stale_cookies` or `cleanup_leftover_temp_files` from `main.py`
- Remove `google.com` from `_PLATFORM_DOMAINS["youtube"]` — breaks age-restriction auth
- Add Twitch, Vimeo, or Dailymotion to `_COOKIE_PLATFORM_MAP` — they don't require per-platform cookies
- Set a fixed delay for all platforms in BatchUX — must use `_get_analysis_delay()` per-platform lookup
- Call `analyse_url()` directly in `_analyse_next()` without `_delayed_analyse()` daemon thread
- Remove `_retry_btn` from `_on_batch_complete()` when errors > 0 — users must be able to retry
- Access `self._url_lbl` without `getattr(self, "_url_lbl", None)` guard — breaks tests using `__new__`
- Ignore `.enc` fallback in `_validate_cookie_path_raw()` — causes "rejected" warning when .txt was encrypted
- When uncertain → prefer NO CHANGE.

---

## SECTION 15 — THEME SYSTEM RULES (Fixed11)

**Architecture:**
- `_PALETTES` — single source of truth for all 10 palettes
- `_CTK_BASE` — maps theme name to `"dark"` or `"light"` for CTk
- `THEME_NAMES` — ordered list; used by Settings OptionMenu
- `T.ctk_base` — correct CTk base; always passed to `ctk.set_appearance_mode()`
- `T.is_dark` — True when `ctk_base == "dark"`

**10 built-in themes:** dark, midnight, ocean, forest, rose, amber, nord (dark);
light, solarized, lavender (light).

**`set_mode()` contract:** Unknown names fall back to `"dark"`. Fires callbacks on change.

**AI MUST NOT:**
- Call `ctk.set_appearance_mode()` with raw theme name
- Add palette without entry in both `_PALETTES` and `_CTK_BASE`
- Remove or reorder `THEME_NAMES`
- Rename `_DARK` or `_LIGHT`
- Use `if mode == "dark"` branching — use `_PALETTES[mode]` lookup
- Add theme without verifying all 34 token keys present

---

## SECTION 16 — FIXED BUG REGISTRY

All bugs below are CONFIRMED FIXED. AI must NOT revert or replace these.

---

### BUG A — Unsafe Resume Encoding
**File:** `ffmpeg_convert_service.py`
**Invariants:** `_resume_encode` deleted permanently. Stale `.part` deleted before encode.

---

### BUG B — Encoder Cache Pollution
**File:** `ffmpeg_convert_service.py`
**Invariants:** `_use_cache = ffmpeg_bin is None` gates all `_encoder_cache_set()` calls.

---

### BUG C — UI Thread Mutation of `_active_count`
**File:** `convert_tab.py`
**Invariants:** `_active_count -= 1` ONLY inside `_finish_job`.

---

### BUG D — Duration Parsing Precision
**File:** `ffmpeg_convert_service.py`
**Invariants:** `int(frac) / (10 ** len(frac))` — NOT `/ 100.0`.

---

### BUG E — Custom Quality Range Mismatch
**File:** `convert_tab.py`
**Invariants:** `max(16, min(35, custom_val))`; label "Giá trị (16–35)".

---

### BUG F — Fragile Widget Index Access (Quality Cards)
**File:** `convert_tab.py`
**Invariants:** `_quality_main_labels.get(k)` — never `winfo_children()`.

---

### BUG G — Unsafe Convert Cancellation
**Files:** `ffmpeg_convert_service.py`, `convert_tab.py`
**Invariants:** Full cancel chain. `submit()` returns `Callable[[], None]`.

---

### BUG H — `.part` File Leak on Encode Failure
**File:** `ffmpeg_convert_service.py`
**Invariants:** `except Exception: temp_output.unlink(missing_ok=True); raise`.

---

### BUG I — `.part.mp4` Files in Folder Scan
**File:** `ffmpeg_convert_service.py`
**Invariants:** `not p.name.endswith(".part.mp4")` filter.

---

### BUG J — UnicodeEncodeError in Console Logging
**File:** `utils/logger.py`
**Invariants:** `errors="replace"` wrapper; `hasattr` guard.

---

### BUG K — TclError on Custom Quality Entry
**File:** `convert_tab.py`
**Invariants:** `_custom_quality` is `tk.StringVar`; restores `"23"` on error.

---

### BUG L — Cookie File Rejected (Outside Safe Directory)
**File:** `settings_tab.py`
**Invariants:** `shutil.copy2` to safe dir; config saves destination path.

---

### BUG M — No Automatic Retry on Download Failure
**File:** `download_manager.py`
**Invariants:** `max(1, retries+1)` attempts; exponential backoff; hard errors no retry.

---

### BUG N — Fragile Widget Index Access (Speed Cards)
**File:** `convert_tab.py`
**Invariants:** `_speed_main_labels.get(k)` — never `winfo_children()`.

---

### BUG O — Cancel Button Missing from Theme Refresh
**File:** `convert_tab.py`
**Invariants:** `_cancel_btn.configure(...)` in `FileCard._on_theme()`.

---

### BUG P — Custom Quality Silent Clamp
**File:** `convert_tab.py`
**Invariants:** `_validate_custom_quality()` on FocusOut + Return; border flash.

---

### BUG Q — `_on_close` Did Not Warn About Active Conversions
**File:** `main_window.py`
**Invariants:** Checks BOTH `active_dl` AND `active_cv`. Both mentioned in warning.

---

### BUG R — `_analysing` Flag Could Get Permanently Stuck
**File:** `toolbar.py`
**Invariants:** `try/except Exception: self._reset_btn()` wraps `_start_analyse()` body.

---

### BUG S — Config Numeric Values Had No Range Validation
**File:** `config_manager.py`
**Invariants:** `max_concurrent`[1-10], `max_retries`[0-10], `history_limit`[10-5000]. Read-only clamp.

---

### BUG T — Duplicate URL Created Multiple Download Tasks
**File:** `download_service.py`
**Invariants:** `active_states()` check in `start_download()`. COMPLETED allows re-download.

---

### BUG U — Proxy URL Had No Validation Before Saving
**File:** `settings_tab.py`
**Invariants:** `_on_proxy_focusout` — inline scheme check, NOT `is_valid_url()`.

---

### BUG V — Typed EventBus Had No IDE Support
**File:** `event_bus.py`
**Invariants:** Original `publish()` unchanged. Typed wrappers additive.

---

### BUG W — ThumbnailService Instantiated Directly in HomeTab
**Files:** `download_service.py`, `main_window.py`, `home_tab.py`
**Invariants:** Only via `DownloadService._thumbnail_svc`. HomeTab must NOT instantiate directly.

---

### BUG X — Facebook Story Regex Missed 4 URL Formats
**File:** `yt_dlp_engine.py`
**Invariants:** Regex covers stories/, story.php, permalink.php?story_fbid=, share/r/, share/s/, view_single.

---

### BUG Y — Instagram Live Regex Missed 2024+ URL Format
**File:** `yt_dlp_engine.py`
**Invariants:** Pattern `r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)"`. **3 locations** in sync.

---

### BUG Z — Instagram Photos Show Empty Quality Picker
**File:** `home_tab.py`
**Invariants:** `is_photo` detection; `_q_sec.pack_forget()`; `hasattr` guard.

---

### BUG AA — Hard Error Keywords Missed Platform-Specific Unretryable Errors
**File:** `download_manager.py`
**Invariants:** Never remove keywords. Case-insensitive check.

---

### BUG AB — `_friendly_error` Showed Raw yt-dlp Errors
**File:** `yt_dlp_engine.py`
**Invariants:** `msg[:200]` fallback must remain as final catch-all.

---

### BUG AC — Test Docstring Incorrectly Described Facebook Stories
**Files:** `test_yt_dlp_engine_extra.py`, `test_e2e.py`
**Invariants:** Test name `test_facebook_stories_blocked_without_cookies`. Docstring clarifies cookies help.

---

### BUG AD — `MediaInfo` Name Collision
**Files:** `ffmpeg_convert_service.py`, `convert_tab.py`, `test_ffmpeg_convert_service.py`
**Invariants:** `FfmpegMediaInfo` in ffmpeg module — never `MediaInfo`.

---

### BUG AE — `_resume_encode` Dead Code
**File:** `ffmpeg_convert_service.py`
**Invariants:** Method must NOT exist. Do NOT reconnect.

---

### BUG AF — `build.yml` Missing ruff + mypy Gates
**File:** `.github/workflows/build.yml`
**Invariants:** Order: install → ruff → mypy → pytest.

---

### BUG AG — Coverage Threshold Too Low
**File:** `setup.cfg`
**Invariants:** `fail_under = 80`.

---

### BUG AH — Vietnamese Labels Without Diacritics
**File:** `ffmpeg_convert_service.py`
**Invariants:** Full diacritics in all `_PRESETS` labels.

---

### BUG AI — No Batch Download Capability
**Files:** `batch_tab.py` (new), `main_window.py`
**Invariants:** ServiceFacade only. MAX_BATCH_URLS=50. Sequential `_analyse_next()`. `_batch_token` guards.

---

### BUG AJ — `max_concurrent` Change Not Applied While Running
**File:** `settings_tab.py`
**Invariants:** Pool created once. `_concurrent_hint` in `_build()` + `_on_theme()`.

---

### BUG AK — Convert Tab Layout Breaks on Resize
**File:** `convert_tab.py`
**Invariants:** `q_row` uses grid + `uniform="qual_card"`. `bar.pack(side="bottom")` before `_scroll`.

---

### BUG AL — Instagram Photos Not Downloaded
**Files:** `yt_dlp_engine.py`, `download_task.py`, `download_manager.py`, `download_service.py`,
`gallery_dl_engine.py` (new)
**Invariants:** Synthetic MediaInfo sets `source_engine="gallery_dl"`. `-q` in `_base_cmd()`.
`_should_fallback_to_gallery_dl()` in `download_service.py`.

---

### BUG AM — Instagram Live `is_live` Race Condition
**File:** `yt_dlp_engine.py`
**Invariants:** `is_live_resolved = bool(info.get("is_live")) or bool(_ig_live_re.search(url))`.
3 regex locations in sync.

---

### BUG AN — gallery-dl Crashed with `--no-progress`
**File:** `gallery_dl_engine.py`
**Invariants:** `"-q"` — NEVER `"--no-progress"`.

---

### BUG AO — gallery-dl Hard Errors Retried
**File:** `gallery_dl_engine.py`
**Invariants:** Every `_friendly_error()` return starts with English keyword.

---

### BUG AP — Pause Button Misleading for gallery-dl Tasks
**File:** `download_item_widget.py`
**Invariants:** `is_gallery_dl` check. `task.media_info is not None` guard.

---

### BUG AQ — No User-Initiated Data Reset Feature
**File:** `settings_tab.py`
**Invariants:** Active task guard. `askyesno` confirmation. UI thread only. Navigate to home.

---

### BUG AR — yt-dlp Version Badge and "Powered by" Label
**File:** `ui/main_window.py`

Title bar showed `_ytdlp_badge` (stale after external pip update). Sidebar showed
`"Powered by yt-dlp"` — misleading as OmniDL now supports multiple engines.

**Fix:** Both widgets removed permanently. Engine versions visible in SettingsTab.
The version label `v16.0.0` in the sidebar bottom strip is stored as `self._powered_lbl`
for theme refresh — this is a DIFFERENT widget, not the "Powered by yt-dlp" badge.

**Invariants:**
- `_ytdlp_badge` must NOT exist in `main_window.py`
- Old "Powered by yt-dlp" badge must NOT be restored — OmniDL is a multi-engine app
- `self._powered_lbl` (version label `v16.0.0`) MUST exist in `_build_sidebar()` for theme refresh
- `_on_theme()` must NOT reference `_ytdlp_badge`; it MAY reference `self._powered_lbl` for color updates

---

### BUG AS — No Live Stream Monitor or Instagram Profile Watcher
**Files:** `ui/tabs/live_monitor_tab.py` (new), `utils/instagram_live_checker.py` (new),
`app/services/download_service.py`, `ui/main_window.py`

No way to monitor live stream URLs or auto-record when an Instagram user goes live.
Users had to manually paste the live URL after the stream started.

**Fix:**

**Part 1 — Live Monitor Tab:** New `LiveMonitorTab` in the DOWNLOADS sidebar section.
Monitors a list of URLs and auto-records when live detected. Supports:
- Direct live URLs (all yt-dlp supported platforms: YouTube, Twitch, TikTok, Facebook, etc.)
- Instagram profile URLs (profile watch mode via `check_instagram_live()`)

**Part 2 — Instagram Profile Watcher:** When `instagram.com/username/` is pasted,
the tab enters profile watch mode. Periodically calls `check_instagram_live()` which
hits `https://i.instagram.com/api/v1/users/web_profile_info/?username=USERNAME`,
checks `live_broadcast_id` in the JSON response, returns the live URL when broadcasting.
Cookie file is required (sessionid cookie must be present).

**State machine per `_MonitorItem`:**
```
WAITING   → polling every interval (default 30s, min 15s)
CHECKING  → one check in flight (analyse_url or check_instagram_live)
LIVE      → stream detected, starting download
RECORDING → DownloadTask active, progress shown inline
ENDED     → task COMPLETED, file saved
ERROR     → hard error (auth/not found/private) — no retry
```

**Architecture invariants:**
- `LiveMonitorTab` uses ONLY ServiceFacade — never imports `infrastructure/` directly
- All callbacks via `self.after(0, ...)` — no direct UI mutation from threads
- `_monitor_token` guards ALL callbacks — incremented ONLY on remove/clear
- `_checking` flag: sequential lock — only 1 check in flight at a time
- `_checking=False` MUST be set **BEFORE** the token check in both done/error callbacks
  (prevents stuck lock if item removed while check was in flight)
- `winfo_ismapped()` guard in poll loop — does NOT poll when tab hidden
- `winfo_exists()` guard on every widget `configure()` call in `_on_theme()`
- `MAX_MONITOR_URLS = 20` hard cap — prevents API spam
- `MIN_CHECK_INTERVAL_S = 15` — user cannot set below 15s
- `_POLL_MS = 5000` ms poll cadence
- Profile watch mode requires cookie file — blocked at `_add_url()` if not set
- `check_instagram_live()` uses stdlib `http.cookiejar.MozillaCookieJar` — no new deps
- Cookie banner shown when cookie file age > `_COOKIE_WARN_DAYS = 7` days
- On LIVE detected for profile watch: calls `service.analyse_url(live_url)` for full MediaInfo;
  falls back to minimal MediaInfo if analyse fails (stream confirmed live via API)
- `output_ext="ts"` for live recording (compatible with `hls_use_mpegts=True`)
- `DownloadService.check_profile_live()` spawns daemon thread — same pattern as `analyse_url()`
- `ServiceFacade.check_profile_live(url, on_done, on_error)` proxy to DownloadService
- `_MonitorItem.is_profile_watch = True` and `username` set when URL is Instagram profile
- Regular live URL (`is_profile_watch=False`): routed via `service.analyse_url()` — existing yt-dlp path
- Hard errors from Instagram API (`login:`, `not found:`, `private:`) → `ERROR` state — no retry
- Transient errors (timeout, connection, 429) → `WAITING` state — retried next poll cycle
- **No `TransientError` class** — `check_instagram_live()` raises only `RuntimeError` for all errors;
  classification (hard vs transient) is done by keyword check in `_on_check_error()`

---

---

### BUG AT — Profile/Channel URLs Silently Downloaded All Videos
**Files:** `infrastructure/downloader/yt_dlp_engine.py`, `domain/models/download_task.py`,
`ui/tabs/home_tab.py`, `ui/tabs/batch_tab.py`

Pasting a profile URL (e.g. `tiktok.com/@user`, `youtube.com/@channel`) into the Download
tab caused yt-dlp to silently download ALL videos in the channel as 1 task — no per-video
control, no history entries per video, no cancel granularity.

**Fix (3 parts):**

**Part 1 — `_PROFILE_URL_RE` + `_extract_playlist_flat()`:**
Module-level `_PROFILE_URL_RE` regex detects unambiguous profile/channel/playlist URLs.
When matched, `extract_info()` calls `_extract_playlist_flat(url, opts)` instead of the
normal single-video path. `_extract_playlist_flat` uses `extract_flat="in_playlist"` which
returns only `{url, id, title}` per entry WITHOUT calling per-video extractors → no
`"No video formats found"` errors, no `"status code 100004"` errors, fast (1 API call).

**Part 2 — `MediaInfo.playlist_entries`:**
`playlist_entries: list = field(default_factory=list)` added to `MediaInfo`.
`_extract_playlist_flat` returns `MediaInfo(playlist_entries=[url1, url2, ...])`.
Single-video path: `playlist_entries` stays `[]` — **no existing behaviour changed**.

**Part 3 — `HomeTab` redirect + `BatchTab.load_playlist()`:**
`on_analysis_done()` checks `info.playlist_entries` — when non-empty, navigates to BatchTab
and calls `batch_tab.load_playlist(urls, playlist_title)` (deferred 50ms via `after(50, ...)`).
`load_playlist()` clears existing batch, populates textarea, auto-starts analysis.
Single-video path is in the `else` branch — completely unchanged.

**Supported platforms (via `_PROFILE_URL_RE`):**
TikTok `@user`, YouTube `@channel`/`/c/`/`/channel/`/`/user/`/`/playlist?`,
Twitter/X `@user` (not `/status/`), Instagram profile, Threads `@user`.

**Invariants:**
- `_PROFILE_URL_RE` must NOT match single-video URLs (`/video/ID`, `/watch?v=`, `/status/`)
- `_extract_playlist_flat` opts: `noplaylist=False`, `extract_flat="in_playlist"`, `ignoreerrors=True`
- `ignoreerrors=True` is REQUIRED — skips deleted/geo-restricted/unavailable entries silently
- `playlist_entries` empty list for single-video MediaInfo — `field(default_factory=list)` only
- `HomeTab.on_analysis_done()` checks `info.playlist_entries` BEFORE `_populate_card()`
- `BatchTab.load_playlist()` respects `MAX_BATCH_URLS = 50` cap, shows truncation toast
- `after(50, ...)` defer in HomeTab prevents CTk layout glitch on first BatchTab visit
- `get_tab("batch") is not None` guard before `load_playlist()` call
- `_extract_playlist_flat` passes `base_opts` (with cookie/proxy already built) — not empty opts

---

### BUG AU — Single Cookie File Applied to All Platforms
**Files:** `infrastructure/config/config_manager.py`, `infrastructure/downloader/yt_dlp_engine.py`,
`infrastructure/downloader/gallery_dl_engine.py`, `app/services/download_service.py`,
`ui/tabs/settings_tab.py`

One cookie file was shared across all platforms. TikTok cookie sent to Instagram downloads,
Instagram cookie sent to TikTok CDN — suboptimal at best, potential session leak at worst.
Users had to swap the cookie file manually when switching platforms.

**Fix (5 layers):**

**Layer 1 — `ConfigManager`:** Added `platform_cookies: dict` to `_DEFAULTS` (default `{}`).
Added `platform_cookies` property (with `isinstance` guard), `get_cookie_for_platform(key)`,
`set_cookie_for_platform(key, path)` methods. Thread-safe via `config.set()`.

**Layer 2 — `_resolve_cookie(url, config)`:** New module-level function in `yt_dlp_engine.py`.
Uses `urllib.parse.urlparse().hostname` for exact matching (NOT regex substring — prevents
subdomain spoofing: `malicious.tiktok.com.evil` → hostname does not end with `.tiktok.com`).
Resolution order: (1) `platform_cookies[key]` validated by `_validate_cookie_path_raw()` →
(2) global `config.cookie_file` via `_validate_cookie_path()` → (3) `None`.
YouTube/Twitch/Vimeo skip step 1 entirely — no cookie leakage across platforms.

**Layer 3 — `yt_dlp_engine.py` callsites:** Both `extract_info()` and `download()` now call
`_resolve_cookie(url, config)` instead of `_validate_cookie_path(config)`. `has_cookies`
check updated to include `any(config.platform_cookies.values())`.

**Layer 4 — `gallery_dl_engine.py`:** `_base_cmd(url="")` accepts URL, calls `_resolve_cookie()`
when provided. Callsites pass `url=url` / `url=task.url`.

**Layer 5 — `settings_tab.py`:** New section `🍪 PER-PLATFORM COOKIES` (8th section) with
5 platform rows (TikTok, Instagram, Facebook, Twitter/X, Threads). Each row: label + path
label + 📂 Browse + 🗑 Clear. `_browse_platform_cookie(key, lbl)` copies to safe dir with
`platform_key_` prefix. `_on_theme()` updated with `_card_cookies`, `_pc_lbls`,
`_pc_browse_btns`, `_pc_clear_btns` lists.

**Security invariants:**
- `_resolve_cookie` uses `urlparse().hostname` — exact domain match ONLY
- `hostname == domain or hostname.endswith("." + domain)` is the ONLY valid check
- `_validate_cookie_path_raw()` applies `safe_root in cp.parents` (same CWE-22 guard)
- Per-platform cookie filename MUST be prefixed: `f"{platform_key}_{src.name}"`
- `isinstance(val, dict)` guard in `platform_cookies` property prevents crash on corrupt config
- `has_cookies` in `extract_info()` MUST include `any(config.platform_cookies.values())`
  to allow IG stories/live through `_NEEDS_COOKIES` when only per-platform cookie is set
- `_COOKIE_PLATFORM_MAP` covers: tiktok.com, instagram.com, facebook.com, fb.watch,
  twitter.com, x.com, threads.net — YouTube/Twitch/Vimeo intentionally NOT included
- `_validate_cookie_path()` (global fallback) unchanged — not removed, not bypassed

---

### BUG AV — TikTok Profile Two-Pass Approach Caused Per-Video Errors
**File:** `infrastructure/downloader/yt_dlp_engine.py`

The original BUG AT fix used a two-pass approach: Pass 1 with `noplaylist=True`, then if
`_type=playlist` detected, Pass 2 with `noplaylist=False` and `ignoreerrors=True`.
yt-dlp with `noplaylist=False` still called per-video extractors during `extract_info()`,
causing `"No video formats found!"` and `"status code 100004"` for deleted/restricted videos
to appear in stderr even with `ignoreerrors=True`. Pass 2 also had lazy `None` entries
that caused `playlist_entry_urls` to be empty, falling back to the old behavior.

**Fix:** Replaced two-pass with `_PROFILE_URL_RE` early detection + `_extract_playlist_flat()`
using `extract_flat="in_playlist"`. With this flag yt-dlp returns only `{url, id, title}`
per entry — **no per-video extractor called** → no errors, no None entries, very fast.

**Invariants:**
- `_extract_playlist_flat` opts MUST have `"extract_flat": "in_playlist"` — no two-pass
- `"ignoreerrors": True` MUST be set — skips private/deleted entries without aborting
- `entry["url"]` with `extract_flat` IS the webpage URL (not CDN) — no `webpage_url` preference needed
- The old two-pass block (`opts_pl`, `noplaylist=False` in a dict copy) must NOT be reintroduced
- Nested playlist flattening via `_collect()` helper handles YouTube channels (playlist-of-playlists)


---

### BUG AW — Live Recording Missing Open-Folder Button
**File:** `ui/tabs/live_monitor_tab.py`

After a live stream recording finished (state `ENDED`), the Live Monitor row had no
way to open the download folder. `_MonitorItem` had no `filename` field and no
`open_folder_btn` — the recorded `.ts` file was inaccessible from the UI.

**Fix:**
- `_MonitorItem`: added `filename: str = ""` and `open_folder_btn: Optional[CTkButton]`
- `_rebuild_item_ui`: builds 📂 button (hidden initially)
- `_refresh_recording_items`: on `COMPLETED`, stores `snap.get("filename")` → `item.filename`
- `_refresh_item_ui`: shows 📂 when `ENDED + item.filename`; hides otherwise
- `_open_folder_for_item(item)`: calls `reveal_in_explorer(p)` → fallback `open_folder(p.parent)`
- `_on_theme()`: refreshes `open_folder_btn` colors (`T.success_bg`, `T.success_text`)
- Imports added: `reveal_in_explorer`, `open_folder` from `utils.helpers`

**Invariants:**
- `item.filename` set ONLY from `snap.get("filename", "")` in `_refresh_recording_items()`
- `open_folder_btn` shown ONLY when `state == ENDED and item.filename` — both required
- `open_folder_btn` NOT packed in `_rebuild_item_ui` — visibility via `_refresh_item_ui` only
- `_open_folder_for_item` calls `.resolve()` before path ops (yt-dlp may return relative path)
- `_on_theme()` must refresh `open_folder_btn` in item loop

---

### BUG AX — Live Recording Unplayable on iPhone (Wrong Container + H.264 Profile)
**Files:** `infrastructure/downloader/yt_dlp_engine.py`,
`app/services/ffmpeg_convert_service.py`

Two bugs combined to produce an MP4 that iPhones cannot play natively (VLC required):

**Bug 1 — Live outtmpl used `%(ext)s`:**
yt-dlp sometimes reports `ext=mp4` for TikTok HLS streams while writing MPEG-TS content
(`hls_use_mpegts=True`). Result: file named `.mp4` containing MPEG-TS data — container
mismatch that defeats iPhone's QuickTime decoder. Also, `.ts` not being in `_MEDIA_EXTS`
meant `pp_hook` never captured `task.filename` for live recordings.

**Fix 1:** Hardcode `.ts` in live outtmpl: `f" %(title).80B [%(id).12B].ts"`.
Add `".ts"` to `_MEDIA_EXTS`.

**Bug 2 — H.264 High Profile:**
`_build_cmd()` used `-profile:v high -level:v 4.0`. High Profile uses B-frames and
advanced coding that Apple's Photos/QuickTime app cannot always handle for MPEG-TS source.

**Fix 2:** All 3 encode paths changed to `-profile:v main -level:v 4.1`.
`Main Profile Level 4.1` supports 1080p@30fps, compatible with iPhone 4S+ (iOS 5+).
File size ~5-8% larger but quality indistinguishable at CRF 18-28.

**Invariants:**
- Live outtmpl MUST use `.ts` explicitly — NEVER `%(ext)s`
- `".ts"` MUST remain in `_MEDIA_EXTS`
- `_build_cpu_flags()` (both paths): `"-profile:v", "main"` and `"-level:v", "4.1"` — exact strings
- `_build_gpu_flags()`: `flags += ["-profile:v", "main", "-level:v", "4.1"]` when `supports_profile_level`
- Do NOT revert to `"-profile:v", "high"` or `"-level:v", "4.0"`


---

### BUG AY — Python 3.14 Thread-Safety: `self.after()` Not Callable from Background Threads
**File:** `ui/tabs/convert_tab.py`

`ConvertTab` uses three background threads (`_detect_encoders_async`,
`_scan_folder_async`, `_probe_info_async`) and four nested callbacks
(`on_start`, `on_progress`, `on_done`, `on_error`) that fire from worker threads.
All 7 called `self.after(0, lambda: ...)` to marshal results back to the UI thread.

Python 3.14 enforces strict main-thread-only access for ALL Tkinter APIs. Previous
versions tolerated this due to GIL timing — 3.14 raises:
`RuntimeError: main thread is not in main loop`
immediately on `self.after()`, `self.winfo_exists()`, or any Tkinter call from a
non-main thread. The first crash occurred in `_detect_encoders_async` at startup
(thread `omnidl-detect-encoders`), preventing the app from opening.

**Fix:** Introduce a `queue.Queue`-based UI callback pump in `ConvertTab`:

1. `import queue` added to imports.
2. `self._ui_queue: queue.Queue = queue.Queue()` added to `__init__`.
3. `_poll_ui_queue()` method added to `ConvertTab` (NOT `FileCard`) — runs on UI
   thread every 50 ms, drains all pending callables, reschedules itself while widget
   exists. Catches per-callback exceptions so one bad callback doesn't stop the pump.
4. `self._poll_ui_queue()` called from `__init__` after `self._build()`.
5. All 7 background thread functions: replaced `self.after(0, lambda: fn())`
   with `self._ui_queue.put(lambda: fn())`. All winfo_exists guards removed from
   these functions — the poller's own `winfo_exists` guard handles widget lifetime.

**Critical mistake caught:** First insertion placed `_poll_ui_queue` inside `FileCard`
(the first class in the file) instead of `ConvertTab`. Caused `AttributeError:
'ConvertTab' object has no attribute '_poll_ui_queue'` at startup. Fixed by
explicitly searching for the ConvertTab-specific `# ── Build` separator.

**Scope — all 7 files fixed:**
Same fix applied to the other affected files:

| File | Callbacks fixed | Pump method | Interval |
|---|---|---|---|
| `convert_tab.py` | 7 (`_detect_encoders_async`, `_scan_folder_async`, `_probe_info_async`, `on_start/progress/done/error`) | `_poll_ui_queue()` | 50 ms |
| `live_monitor_tab.py` | 6 (`on_done`, `on_error` ×3 pairs) | `_poll()` (queue drain integrated) | 5000 ms (_POLL_MS) |
| `batch_tab.py` | 4 (`on_done`, `on_error`, `_on_batch_complete`, `_refresh_item_ui`) | `_drain_ui_queue()` | 100 ms |
| `download_item_widget.py` | 3 (`_on_progress`, `_on_done`, `_on_error`) | `_drain_ui_queue()` | 50 ms |
| `status_bar.py` | 1 (`_do_net_check`) | `_drain_ui_queue()` | 100 ms |
| `settings_tab.py` | 10 (`_worker` ×2: yt-dlp update + gallery-dl update) | `_drain_ui_queue()` | 150 ms |
| `toolbar.py` | 2 (`_safe_done`, `_safe_error` from `analyse_url` background thread) | `_drain_ui_queue()` | 50 ms |

`live_monitor_tab` integrates queue draining directly into the existing `_poll()` loop
rather than adding a separate poller — consistent with its 5-second poll cadence.

**Invariants:**
- `_ui_queue` MUST be initialized before `threading.Thread(target=_detect_encoders_async).start()`
- `_poll_ui_queue()` MUST be called from `__init__` after `self._build()`
- `_poll_ui_queue()` MUST be defined in `ConvertTab`, NOT in `FileCard`
- ALL background thread callbacks in `ConvertTab` MUST use `self._ui_queue.put(fn)` — no exceptions
- `_poll_ui_queue()` MUST reschedule via `self.after(50, self._poll_ui_queue)` — NOT `after(0,...)`
- 50 ms poll interval: fast enough to be imperceptible, slow enough to not waste CPU
- `except Exception` per-callback prevents one bad callback from stopping the pump


---

### BUG AZ — Python 3.14 Thread-Safety: `SettingsTab` Update Workers
**File:** `ui/tabs/settings_tab.py`

`_update_ytdlp()` and `_update_gallery_dl()` each spawn a `threading.Thread(target=_worker)`.
The nested `_worker` functions called `self.after(0, lambda: widget.configure(...))` directly —
same Python 3.14 crash as BUG AY. Crash would occur when user clicks "Update yt-dlp" or
"Update gallery-dl" buttons in Settings.

**Fix:** Added `_ui_queue: queue.Queue` to `SettingsTab.__init__`, added `_drain_ui_queue()`
method (150 ms poll), replaced all 10 `self.after(0, lambda:...)` calls in both `_worker`
functions with `self._ui_queue.put(lambda:...)`.

**Invariants:**
- `_update_ytdlp._worker` MUST use `self._ui_queue.put()` — 5 callbacks
- `_update_gallery_dl._worker` MUST use `self._ui_queue.put()` — 5 callbacks
- `_drain_ui_queue()` must be called from `__init__` after `self._build()`

---

### BUG BA — Python 3.14 Thread-Safety: `StatusBar` Network Check
**File:** `ui/components/status_bar.py`

`StatusBar._check_net()` spawns `threading.Thread(target=self._do_net_check)` every 15 s.
`_do_net_check()` called `if self.winfo_exists(): self.after(0, ...)` — both calls are
Tkinter APIs not safe from non-main threads on Python 3.14. Crash appeared at startup
(thread `Thread-3: _do_net_check`) approximately 15 seconds after launch.

**Fix:** Added `_ui_queue: queue.Queue` to `StatusBar.__init__`, added `_drain_ui_queue()`
(100 ms poll), replaced the problematic pattern with
`self._ui_queue.put(lambda result=ok: self._update_net(result))`.

**Invariants:**
- `_do_net_check` MUST NOT call `self.winfo_exists()` or `self.after()` — use `_ui_queue.put()`
- `_drain_ui_queue()` must be called from `__init__`

---

### BUG BB — No Built-in Cookie Extractor (Dependency on Third-Party Extension)
**Files:** `infrastructure/downloader/cookie_extractor.py` (new),
`ui/tabs/settings_tab.py`

Users had to install the browser extension "Get cookies.txt LOCALLY" (a third-party
tool) to export cookies before pasting them into OmniDL. This created a trust boundary:
the extension could read and potentially exfiltrate all browser cookies.

**Fix (2 layers):**

**Layer 1 — `cookie_extractor.py`:** New module wrapping yt-dlp's
`cookiesfrombrowser` option. `extract_browser_cookies(browser, output_path,
platform_key=None)` reads cookies from the local browser using yt-dlp's own
DPAPI/keyring decryption, writes a Netscape-format `.txt` file inside the safe
directory, and returns `(count, error_message)`. Platform filter (`platform_key`)
uses `http.cookiejar.MozillaCookieJar` to keep only the relevant domains — no
cross-platform session leakage. Writes to a sibling `.tmp` file first — never
corrupts an existing good cookie file on failure.

**Layer 2 — `settings_tab.py`:**
- **Global extract:** Button `🔄 Lấy cookies từ trình duyệt` added below the browser
  dropdown in `🔒 NETWORK & AUTHENTICATION`. Calls `_extract_global_cookies()` in a
  daemon thread; saves result as global `cookie_file`.
- **Per-platform extract:** Button `🔄` added to each platform row in
  `🍪 PER-PLATFORM COOKIES`. Calls `_extract_platform_cookie(key, lbl)` in a daemon
  thread; saves platform-filtered result via `set_cookie_for_platform()`.
- Shared status label `_pc_extract_status` below platform rows shows progress/result.
- All UI updates via `self._ui_queue.put()` — Python 3.14 safe (BUG AY/AZ pattern).
- All extract buttons disabled during extraction to prevent tmp-file race condition.

**Security invariants:**
- Cookies never leave the local machine — no third-party service involved
- Output always inside `config_path.parent/cookies/` — CWE-22 guard accepts it
- Platform filter: exact domain or `.`-suffix match only (not substring)
- Temp-file-first write pattern — failure never corrupts existing file
- `_PLATFORM_DOMAINS["threads"]` includes `instagram.com` (Threads auth is Instagram-backed)
- YouTube/Twitch/Vimeo intentionally NOT in `_PLATFORM_DOMAINS`

**Thread invariants:**
- All 8 worker functions (`_extract_global_cookies`, `_extract_global_cdp`,
  `_extract_platform_cookie`, `_extract_platform_cdp`, `_install_keyring`,
  yt-dlp update ×2, gallery-dl update ×1) spawn daemon threads
- ALL UI callbacks use `self._ui_queue.put()` — NEVER `self.after()` (BUG AY/AZ)
- All `_pc_extract_btns` (🔄 + 🦁) disabled at worker start; re-enabled at end via `_ui_queue.put()`
- `_on_theme()` MUST refresh all new refs: `_pc_extract_btns`, `_pc_extract_status`,
  `_extract_global_btn`, `_extract_global_status`, `_extract_global_hint`,
  `_extract_cdp_btn`, `_keyring_btn`, `_keyring_status`

**CDP technical invariants (final — after 8 hotfixes):**
- `_cdp_get_page_ws_url(port)` uses `/json/list` → picks first target with `type=="page"`
  If no page exists, calls `PUT /json/new?about:blank` and retries up to 5 times
- `_cdp_get_all_cookies(sock)` sends `Network.enable` (id=1) first, waits for ack,
  then sends `Network.getAllCookies` (id=2) — both steps mandatory
- `extract_via_cdp()` checks `_is_browser_running(browser)` before doing anything;
  returns user-friendly error if True (profile locked)
- `extract_via_cdp()` uses `_find_browser_profile(browser)` for `--user-data-dir`;
  points at real User Data dir, not a temp directory
- CDP subprocess terminated in `finally` block always — even on `_is_browser_running=True`
  return path (no proc launched in that case, guard is `if proc is not None`)

---

### BUG BC — Cookie Files Stored as Plaintext on Disk
**Files:** `infrastructure/downloader/cookie_storage.py` (new),
`infrastructure/downloader/cookie_extractor.py`,
`infrastructure/downloader/yt_dlp_engine.py`,
`ui/tabs/settings_tab.py`, `main.py`

After BUG BB, extracted cookies were stored as Netscape plaintext in
`%APPDATA%\OmniDL\cookies\`.  Any process running as the same Windows user
could read all session tokens.  Four additional risks were identified and fixed.

**Fix (5 layers):**

**Layer 1 — DPAPI at-rest encryption (`cookie_storage.py`):**
New module. `encrypt_cookie_file(txt_path)` encrypts to `.enc` using Windows
`CryptProtectData` — key bound to current user account.  Atomic: `.enc` written
before `.txt` deleted.  `decrypt_to_tempfile(enc_path)` decrypts to `omnidl_dec_*`
temp file; caller must delete in `finally`.  Graceful fallback on non-Windows.

**Layer 2 — Decrypt gate in `yt_dlp_engine.py`:**
`_prepare_cookie_for_use(path)` checks `is_encrypted()`, decrypts if needed,
returns `(usable_path, is_temp)`.  Temp files cleaned up unconditionally after
`extract_info()` and `download()`.

**Layer 3 — CDP hardening in `cookie_extractor.py`:**
`_find_free_port()` selects a random unprivileged port each call — attacker
cannot predict which port to probe during the ~10-second extraction window.
`--remote-debugging-address=127.0.0.1` added to CDP launch command — explicit
localhost-only bind.

**Layer 4 — Confirmation dialogs in `settings_tab.py`:**
Global extraction (`_extract_global_cookies`, `_extract_global_cdp`) shows
`askyesno` dialog explaining full-browser scope and recommending per-platform
buttons instead.

**Layer 5 — Startup cleanup in `main.py`:**
`cleanup_leftover_temp_files()` removes `omnidl_dec_*` files from crashed sessions.
`cleanup_stale_cookies(30)` auto-deletes `.txt`/`.enc` files older than 30 days.

**Security invariants:**
- `.enc` file written before `.txt` deleted — no data loss on disk failure
- Temp file (`omnidl_dec_*`) deleted in `finally` — never persists after download
- CDP port is random per-call — unpredictable during 10-second window
- `--remote-debugging-address=127.0.0.1` — no accidental LAN exposure
- DPAPI fallback: plaintext used silently on non-Windows or DPAPI failure (logged)
- Confirmation dialog: UI thread only (`askyesno` blocks until user responds)
- Stale cleanup: 30-day threshold; skips `_tmp_*` and `omnidl_dec_*` prefixes

---

### BUG BD — Batch Rate-Limiting and No Retry for Failed URLs
**Files:** `ui/tabs/batch_tab.py`, `ui/components/download_item_widget.py`

Three UX problems in batch mode: (1) No per-platform delay → Instagram/TikTok rate-limit
immediately on sequential analysis. (2) Failed batch items don't show which URL failed.
(3) Failed Queue items don't show their URL — user can't identify which link to retry.

**Fix (3 parts):**

**Part 1 — Per-platform analysis delay:**
`_PLATFORM_ANALYSIS_DELAY` dict maps hostname suffix → seconds.
`_get_analysis_delay(url)` uses `urlparse().hostname` for exact matching.
`_delayed_analyse()` daemon thread sleeps the delay before calling `analyse_url()`.
First item in batch: no delay. Subsequent items: platform delay applied.
Delay runs in a daemon thread — UI stays responsive during sleep.

**Part 2 — Error visibility in batch results:**
ERROR state row now shows `URL · error_preview` instead of error alone.
User can see exactly which link failed without cross-referencing the textarea.

**Part 3 — Retry button + Queue URL label:**
`_retry_btn` (🔄 Thử lại X lỗi) appears after `_on_batch_complete()` when errors > 0.
`_retry_errors()` resets ERROR items to PENDING, fires new batch_token, re-runs analysis.
READY/QUEUED items are preserved — only failed URLs are retried.
`_url_lbl` in `DownloadItemWidget`: shown below title on FAILED status using `getattr` guard.

**Invariants:**
- `_PLATFORM_ANALYSIS_DELAY`: instagram/threads=2.5s, tiktok=2.0s, facebook/twitter/x=1.5s, youtube=0.8s
- `_get_analysis_delay()`: uses `urlparse().hostname` (not regex substring)
- `_delayed_analyse()`: daemon thread; checks token before calling `analyse_url()`
- `_retry_btn`: shown only when `errors > 0` in `_on_batch_complete()`; disabled in `_clear_all()`
- `_retry_errors()`: fires new `_batch_token`; preserves non-ERROR items
- `_url_lbl`: initialized to `None` in `__init__` before `_build()`; all access via `getattr(self, "_url_lbl", None)`

---

### BUG BE — Cookie File Rejected After Encryption (.txt → .enc Rename)
**File:** `infrastructure/downloader/yt_dlp_engine.py`

After BUG BC encrypted cookie files (.txt → .enc), settings_tab saved the `.txt` path
to config before `encrypt_cookie_file()` renamed it. Subsequent downloads called
`_validate_cookie_path_raw()` which checked `cp.is_file()` on the `.txt` path —
file no longer existed → `"rejected — not inside safe directory"` warning → no cookie
→ Instagram/TikTok returned rate-limit/login-required errors.

**Fix:** Both `_validate_cookie_path_raw()` and `_validate_cookie_path()` now auto-fallback:
if `.txt` path is absent but `.enc` with same stem exists → silently use `.enc`.
This is a 1-line check that fires only when `.txt` missing AND `.enc` present —
the CWE-22 `safe_root in cp.parents` check still fires FIRST.

**Invariants:**
- `safe_root in cp.parents` check ALWAYS fires before the `.enc` fallback
- Fallback only triggers when `cp.suffix == ".txt"` and `.enc` sibling exists
- Both `_validate_cookie_path()` and `_validate_cookie_path_raw()` have identical fallback
- 4 regression tests in `test_yt_dlp_engine.py::TestCookiePathEncFallback`

---

### BUG BF — YouTube Age-Restricted Content Not Supported Per-Platform
**Files:** `infrastructure/downloader/yt_dlp_engine.py`,
`infrastructure/downloader/cookie_extractor.py`, `ui/tabs/settings_tab.py`

YouTube was intentionally excluded from `_COOKIE_PLATFORM_MAP` and `_PLATFORM_DOMAINS`
to prevent unwanted cookie leakage. However, age-restricted YouTube content requires
both `youtube.com` session cookies AND `google.com` auth cookies (account age-verify).
Without per-platform support, users had to use the global cookie file.

**Fix (3 files, ~10 lines):**
- `_COOKIE_PLATFORM_MAP`: added `("youtube.com", "youtube")` and `("youtu.be", "youtube")`
- `_PLATFORM_DOMAINS["youtube"]`: `("youtube.com", "youtu.be", "google.com")`
  `google.com` is required — YouTube delegates age-verification to Google Account auth.
- `settings_tab.py`: added YouTube row to `_PC_PLATFORMS` list (first in list)

**Invariants:**
- `google.com` MUST remain in `_PLATFORM_DOMAINS["youtube"]` — without it, age-gate blocks
- Twitch, Vimeo, Dailymotion still NOT in `_COOKIE_PLATFORM_MAP` — they don't need per-platform
- YouTube cookie file contains only `youtube.com`, `youtu.be`, `google.com` cookies — no other domains

---

### BUG BG — Decrypted Temp Cookie Leaked on Early-Return Paths in extract_info()
**File:** `infrastructure/downloader/yt_dlp_engine.py`

`extract_info()` creates a decrypted temp file (`omnidl_dec_*.txt`) when cookie is `.enc`.
The cleanup at the end of the function was missed on two early-return paths:
(1) Profile/playlist URL (`_PROFILE_URL_RE.search(url)` → `return self._extract_playlist_flat()`)
(2) Instagram photo detection (`return MediaInfo(source_engine="gallery_dl")`)
These paths returned before reaching the cleanup block → temp file persisted on disk.

**Fix:** Added explicit cleanup (try/unlink/except) immediately before each early return.
Pattern mirrors the existing cleanup at the function's normal exit point.

**Invariants:**
- Every `return` path in `extract_info()` MUST clean up `_cookie_temp_ei` before returning
- Cleanup uses `try/except Exception: pass` — never crashes on cleanup failure
- 3 return paths: playlist early-return (L459), photo early-return (~L502), normal exit (~L565)

---

### BUG BH — Global Cookie Section UI Misleading (Looked Like Primary Option)
**File:** `ui/tabs/settings_tab.py`

The global cookie extraction buttons (🔄/🦁) appeared before the per-platform table
without clear indication that they collect ALL browser cookies (Google, email, banking...).
Users naturally used global first, which created unnecessarily broad cookie files.

**Fix:** Restructured `🔒 NETWORK & AUTHENTICATION` section:
- Section renamed to "🌐 Cookie fallback — cho YouTube, Twitch, Vimeo..."
- Warning label added: "⚠ File này chứa toàn bộ cookies (Google, email, banking...)"
- Button labels clarified: "🔄 Firefox / Edge / Opera" and "🦁 Brave / Chrome 127+"
- `🍪 PER-PLATFORM COOKIES` section title: added "✅ Khuyến nghị — bảo mật hơn"
- Per-platform description updated: explains why per-platform is safer

**Invariants:**
- Global section still fully functional — required for YouTube, Twitch, Vimeo
- `_extract_global_btn`, `_extract_cdp_btn` behaviour unchanged — only label text changed
- `_on_theme()` refs unchanged — same widget variables

---

### Minor Fixes (no BUG entry — defensive code quality)

**Fix: `_apply_available_encoders()` winfo guard** (`ui/tabs/convert_tab.py`)
Added `if not self.winfo_exists(): return` at method start. Prevents TclError if
ConvertTab is destroyed during the 1-2 s encoder detection window at startup.
The existing `try/except` around `_encoder_menu.configure()` returns early but
`_encoder_status_lbl.configure()` at the end was not protected.

**Fix: `_refresh_status()` winfo guard** (`ui/tabs/convert_tab.py`)
Added `if not self.winfo_exists(): return` at method start. Prevents TclError if
ConvertTab is destroyed while FFmpeg is encoding (close app during active conversion).
`_finish_card()` already had a card-level guard but `_refresh_status()` accessed
`self._status_lbl` without checking if the parent widget still exists.

**Fix: `QueueTab` wires `on_convert`** (`ui/tabs/queue_tab.py`)
`DownloadItemWidget` was created without `on_convert=` parameter. Added
`on_convert=lambda p, **kw: self._app.convert_to_mp4(p, **kw)`.
Result: → MP4 button now appears in Queue tab for any non-MP4 completed file.

**Fix: LiveMonitor → MP4 button (`_start_mp4_convert`)** (`ui/tabs/live_monitor_tab.py`)
`_start_mp4_convert(item)` quick-converts `.ts` → `.mp4` using CPU libx264 main profile,
CRF 23. All 3 callbacks (`_on_progress`, `_on_done`, `_on_error`) use `self._ui_queue.put()`
(Python 3.14 safe). Progress shown in button text (0%→99%). On completion `item.filename`
updated to the new `.mp4`. Double-click guard via `mp4_converting` flag.

**Fix: LiveMonitor ⚙ Send-to-Convert button** (`ui/tabs/live_monitor_tab.py`)
Added `send_to_conv_btn` (⚙) to `_MonitorItem` alongside → MP4. Calls
`_send_to_convert_tab(item)` which: checks file exists, gets `convert_tab` via
`get_tab("convert")`, calls `convert_tab._add_file(path)`, navigates to Convert Tab.
Allows user to use GPU encoder, custom CRF, presets — vs the quick-convert CPU default.
Button shows whenever `.ts` file is ready (even while → MP4 is converting).


## QUICK REFERENCE — What Must Never Change

| Component | Rule |
|---|---|
| `_resume_encode` | Deleted permanently (BUG AE) |
| `_use_cache` | Gates ALL `_encoder_cache_set()` calls |
| `_active_count` | Only in `_finish_job` on UI thread |
| Duration fraction | `int(frac) / (10 ** len(frac))` |
| Quality clamp | `max(16, min(35, custom_val))` |
| `_quality_main_labels` | Never `winfo_children()[N]` |
| `_speed_main_labels` | Never `winfo_children()[N]` |
| Cancel chain | `cancel_event` through all 5 levels |
| `_kill_proc` | `os.killpg` POSIX / `proc.kill()` Windows |
| `start_new_session` | POSIX only |
| `ConversionCancelledError` | Subclass, caught separately, never retried |
| `.part` cleanup | `except Exception: unlink; raise` |
| Scan filter | `not p.name.endswith(".part.mp4")` |
| Log handler | `errors="replace"` + `hasattr` guard |
| `_custom_quality` | `tk.StringVar` — never `tk.IntVar` |
| Cookie path | Copy to safe dir — never save source path |
| Retry loop | `max(1, retries+1)` attempts, exponential backoff |
| `_HARD_ERROR_KEYWORDS` | Never remove — only add |
| Thread join timeouts | Both `stderr` and `stdout` join have `timeout=5.0` |
| `_cancel_btn` in `_on_theme` | Warning token colors |
| `_validate_custom_quality` | FocusOut + Return, border flash |
| `_on_close` dual check | BOTH downloads AND `ConvertTab._active_count` |
| `_start_analyse` try/except | `except Exception: self._reset_btn()` always present |
| Config clamps | `max_concurrent`[1-10], `max_retries`[0-10], `history_limit`[10-5000] |
| Duplicate URL guard | `active_states()` in `start_download()` |
| Proxy validation | Inline scheme check — NOT `is_valid_url()` |
| EventBus `publish()` | Original method unchanged |
| ThumbnailService | Only via `DownloadService._thumbnail_svc` |
| Facebook Story regex | stories/, story.php, permalink.php?story_fbid=, share/r/, share/s/, view_single |
| Instagram Live regex | Both `/username/live/` AND `/live/shortcode/` — **3 locations** |
| `_q_sec` photo hide | `hasattr` guard + `pack_forget()` / `pack()` |
| `_friendly_error` fallback | `msg[:200]` final catch-all |
| `FfmpegMediaInfo` name | Never `MediaInfo` in ffmpeg module |
| build.yml gate order | ruff → mypy → pytest |
| Coverage threshold | `fail_under = 80` |
| Coverage omit | `ui/*`, `tests/*`, `build/*`, `main.py`, `gallery_dl_engine.py`, `instagram_live_checker.py`, `facebook_story_engine.py` |
| `_parse_seconds()` | Pure function in `ffmpeg_convert_service.py` — must remain importable for tests |
| ConversionError message | Full Unicode: `"ffmpeg thoát với lỗi {returncode}"` — NOT ASCII transliteration |
| `_friendly_error` 429 | Standalone `"429" in msg_l or "too many requests" in msg_l` branch MUST remain |
| Logger TextIOWrapper | Wrap only when `hasattr(buffer, "raw")` — NOT in pytest/test environments |
| `_PRESETS` labels | Full Vietnamese diacritics |
| `BatchTab._batch_token` | Guards ALL analyse callbacks |
| `MAX_BATCH_URLS` | Hard cap at 50 |
| Batch analysis | Sequential via `_analyse_next()` |
| BatchTab / LiveMonitorTab infra | ServiceFacade only |
| `.txt` import encoding | `Path.read_text(errors="replace")` |
| `_concurrent_hint` label | In `_build()` + `_on_theme()` |
| `max_concurrent` pool | Created once at startup |
| `_PALETTES` / `_CTK_BASE` | Add new theme to BOTH dicts |
| `ctk.set_appearance_mode()` | Always `T.ctk_base` |
| `set_mode()` fallback | Unknown → `"dark"` |
| `_DARK` / `_LIGHT` names | Never rename |
| `THEME_NAMES` order | Fixed list of 10 |
| Quality card layout | grid `uniform="qual_card"` — never pack(side="left") |
| Bottom bar pack order | `bar.pack(side="bottom")` BEFORE `_scroll.pack(expand=True)` |
| Quality card wraplength | ≤ 130 |
| `source_engine="gallery_dl"` | Synthetic MediaInfo for IG photos |
| `GalleryDlEngine._base_cmd()` | `"-q"` — never `"--no-progress"` |
| gallery-dl error prefixes | English keyword prefix in every `_friendly_error()` return |
| Pause button gallery-dl | `is_gallery_dl` + `task.media_info is not None` guard |
| `_clear_all_data()` guard | Blocked during downloads/conversions |
| `_clear_all_data()` thread | UI thread only |
| `_card_data` in `_on_theme` | All 4 Data & Privacy widgets theme-aware |
| `_ytdlp_badge` | Removed permanently (BUG AR) — do NOT restore |
| Old "Powered by yt-dlp" badge | Removed permanently (BUG AR) — do NOT restore |
| `self._powered_lbl` | Version label `v16.0.0` in sidebar — MUST remain for theme refresh |
| `_monitor_token` | Incremented on remove/clear only; guards ALL callbacks |
| `_checking` reset order | `False` BEFORE token check in done/error callbacks |
| `MAX_MONITOR_URLS` | Hard cap at 20 |
| `MIN_CHECK_INTERVAL_S` | 15s minimum |
| LiveMonitorTab infra | ServiceFacade only |
| `check_profile_live()` | Sequential via `_checking` — never parallel |
| `hls_use_mpegts=True` | Live download opt — MUST remain set for live streams |
| Live recording ext | `output_ext="ts"` |
| Cookie warn threshold | `_COOKIE_WARN_DAYS = 7` |
| `_PROFILE_URL_RE` | Conservative profile regex — single-video URLs must NOT match |
| `_extract_playlist_flat` opts | `extract_flat="in_playlist"`, `noplaylist=False`, `ignoreerrors=True` |
| `playlist_entries` default | `field(default_factory=list)` — never bare `[]` |
| `load_playlist` cap | Respects `MAX_BATCH_URLS = 50` |
| HomeTab playlist defer | `after(50, ...)` before `load_playlist()` |
| `_resolve_cookie` detection | `urlparse().hostname` exact match — NOT regex substring |
| `_COOKIE_PLATFORM_MAP` | Exact hostname list — YouTube/Twitch NOT included |
| `_validate_cookie_path_raw` | Same `Path.parents` CWE-22 guard as `_validate_cookie_path` |
| `has_cookies` check | Must include `any(config.platform_cookies.values())` |
| Per-platform filename prefix | `f"{platform_key}_{src.name}"` — prevents collision |
| `platform_cookies` guard | `isinstance(val, dict)` — corrupt config → `{}` fallback |
| `_base_cmd(url="")` | gallery-dl passes URL for per-platform resolution |
| `_pc_lbls/_pc_browse_btns/_pc_clear_btns` | All 3 lists in `_on_theme()` |
| `cookie_storage.py` | DPAPI encrypt/decrypt; `encrypt_cookie_file` atomic; temp cleanup in `finally` (BUG BC) |
| `encrypt_cookie_file` | `.enc` written BEFORE `.txt` deleted — data-loss safe (BUG BC) |
| `decrypt_to_tempfile` | Caller MUST delete returned temp in `finally` (BUG BC) |
| `_prepare_cookie_for_use` | Decrypt gate in `yt_dlp_engine.py`; `(path, is_temp)` pair (BUG BC) |
| CDP port | `_find_free_port()` random each call — NOT hardcoded 9223 (BUG BC) |
| `--remote-debugging-address` | `127.0.0.1` explicit in CDP cmd — no LAN exposure (BUG BC) |
| Global extraction dialogs | `askyesno` confirmation in `_extract_global_cookies` + `_extract_global_cdp` (BUG BC) |
| Startup cookie cleanup | `cleanup_stale_cookies(30)` + `cleanup_leftover_temp_files()` in `main.py` (BUG BC) |
| `_PLATFORM_ANALYSIS_DELAY` | Per-platform batch delay dict; instagram=2.5s, tiktok=2.0s (BUG BD) |
| `_get_analysis_delay()` | urlparse hostname lookup — NOT regex (BUG BD) |
| `_delayed_analyse()` | Daemon thread with delay before `analyse_url()` (BUG BD) |
| `_retry_btn` | Shown when errors > 0 in `_on_batch_complete()`; `_retry_errors()` preserves READY/QUEUED (BUG BD) |
| `_url_lbl` in DownloadItemWidget | `getattr(self, "_url_lbl", None)` guard — `__new__` safe (BUG BD) |
| `.enc` auto-fallback | `_validate_cookie_path_raw` + global: `.txt` absent → try `.enc` (BUG BE) |
| `_PLATFORM_DOMAINS["youtube"]` | Includes `google.com` for age-verify auth — do NOT remove (BUG BF) |
| `_COOKIE_PLATFORM_MAP` youtube | `youtube.com` + `youtu.be` entries; Twitch/Vimeo NOT included (BUG BF) |
| `extract_info()` temp cleanup | ALL 3 return paths clean `_cookie_temp_ei` before returning (BUG BG) |
| Global cookie section label | "Cookie fallback" — NOT primary option; per-platform preferred (BUG BH) |
| `cookie_extractor.py` | Two extraction methods: yt-dlp + CDP; each with distinct output names (BUG BB) |
| `extract_browser_cookies()` | Temp-file-first; platform filter exact/suffix; yt-dlp method (BUG BB) |
| `extract_via_cdp()` | CDP method; real profile; `_is_browser_running` guard; `Network.enable` first (BUG BB) |
| `_cdp_get_page_ws_url()` | `/json/list` page target — NOT `/json/version` browser endpoint (BUG BB) |
| `Network.enable` before `getAllCookies` | Mandatory — omitting returns empty cookie list (BUG BB) |
| `_is_browser_running()` | tasklist list-form; no shell=True; False on non-Windows (BUG BB) |
| `_find_browser_profile()` | Real User Data dir; NEVER temp/empty dir (BUG BB) |
| CDP proc cleanup | `terminate()` + `wait(5)` + `kill()` fallback in finally block (BUG BB) |
| CDP port 9223 | Unprivileged; avoids clash with user's 9222 session (BUG BB) |
| `_PLATFORM_DOMAINS` | Exact/suffix domain match; YouTube/Twitch NOT included (BUG BB) |
| `_pc_extract_btns` | Contains BOTH 🔄 and 🦁 buttons; all disabled during any extraction (BUG BB) |
| `_extract_cdp_btn` | 🦁 global CDP button; in _on_theme(); daemon thread (BUG BB) |
| `_keyring_btn` / `_keyring_status` | In YT-DLP ENGINE section; _on_theme() required (BUG BB) |
| Extract worker threads | All UI callbacks via `_ui_queue.put()` — NEVER `self.after()` (BUG BB) |
| `open_folder_btn` in LiveMonitor | Shown when ENDED + filename set (BUG AW) |
| `item.filename` | From `snap.get("filename")` on COMPLETED only (BUG AW) |
| Live outtmpl `.ts` | Hardcoded, NOT `%(ext)s` (BUG AX) |
| `".ts"` in `_MEDIA_EXTS` | Required for live `task.filename` capture (BUG AX) |
| `-profile:v main` | NOT `high` — iPhone compatibility (BUG AX) |
| `-level:v 4.1` | NOT `4.0` (BUG AX) |
| `-movflags +faststart` | Always in `_build_cmd()` — required for iPhone playback |
| `ConvertTab._ui_queue` | `queue.Queue` — bg threads post here (BUG AY) |
| `_poll_ui_queue()` | Must be in `ConvertTab`, called after `_build()` (BUG AY) |
| bg threads → `_ui_queue.put()` | NEVER `self.after()` from non-main thread (BUG AY) |
| `_poll_ui_queue` reschedule | `after(50,...)` NOT `after(0,...)` |
| All 7 UI classes | MUST have `_ui_queue` + pump (BUG AY/AZ/BA + v16.3.0) |
| `toolbar._safe_done/_safe_error` | `_ui_queue.put()` — NOT `self.after(0,...)` (v16.3.0) |
| `settings_tab._worker` | 10 `_ui_queue.put()` calls — no `self.after(0,...)` (BUG AZ) |
| `status_bar._do_net_check` | `_ui_queue.put()` — no `winfo_exists()` (BUG BA) |
| `QueueTab on_convert=` | MUST be wired — enables → MP4 in Queue tab |
| `_apply_available_encoders` | winfo guard at top — prevents TclError on startup close |
| `_refresh_status` | winfo guard at top — prevents TclError on close-during-encode |
| `send_to_conv_btn` in LiveMonitor | ⚙ button — shown with `.ts` file, navigates to Convert Tab |
| `_start_mp4_convert` in LiveMonitor | → MP4 quick-convert; all callbacks via `_ui_queue.put()` |

---

*End of OMNIDL_STABILITY_RULES.md v15.1*
