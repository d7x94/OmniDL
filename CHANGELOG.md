# Changelog

All notable changes to OmniDL are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v20.3.2 — 2026-09-17

Log audit of `omnidl_debug.log` (session 06:00:47-12:51:46, 23,940 lines) and the
INFO sink `omnidl.log` beside it. No ERROR record or traceback in either file:
all 10 enqueued tasks completed and 9 of 10 Taildrop sends succeeded. Three
logging-layer defects came out of the 9 warnings that remained.

### Fixed — `omnidl.log` was 97% one repeated line

`omnidl.log` held 21,141 records, 20,484 of which were
`Using tiktok cookie: ...\tiktok_brave_cdp_cookies.enc`.

- **`_resolve_cookie()` logged at INFO on every call.** It runs once per
  live-monitor poll — seven watched accounts at a 60 s interval for the ~7 h
  session — so a routine path resolution produced 2,931 INFO records in this
  session alone, while the events the file exists for (10 task completions, 10
  Taildrop sends, 9 warnings) produced a few dozen. The 5 MB x 3 rotation budget
  was spent on the noise and evicted the diagnostics.
- **Fix.** `Using %s cookie` and `Using cookie file` are now DEBUG. The
  resolution is still fully traceable in `omnidl_debug.log`.

### Fixed — yt-dlp warned `Overwriting params from "color" with "no_color"`

Six occurrences, all on the TikTok `BUG-TT-10231-DL` retry chain.

- **yt-dlp keeps the opts dict we hand it.** `YoutubeDL.__init__` does
  `self.params = params` with no copy and then writes
  `params["color"] = "no_color"` into it. The deprecated `"no_color": True`
  OmniDL passed therefore left *both* keys set, so the next `YoutubeDL(...)`
  built from those opts hit yt-dlp's conflict branch.
- **The warning accumulated.** yt-dlp appends it to `params["_warnings"]`, a
  list that `{**opts}` copies by reference and that is never cleared, so each
  further retry appended one more copy and replayed all of them.
- **Fix.** The four extract/download opt builders now pass the non-deprecated
  `"color": "no_color"` and no `"no_color"` key. ANSI escapes stay out of the
  log exactly as before; the mutation and the warning are gone.

### Fixed — a Taildrop failure hid its own cause

At 11:06:41 a send to `iphone-12-pro-max` failed and the log said only
`tailscale exit 1: <ESC>[K# warning: iphone-12-pro-max is reportedly offline;
trying anyway`.

- **Raw CLI output went straight into the message.** `tailscale file cp` draws a
  progress bar, so its stderr carries ANSI escapes and CR overwrites, and it
  prints a `# warning: ... trying anyway` advisory *before* the real error. The
  embedded newline split the log record in two: the real cause,
  `502 Bad Gateway:`, landed on the following physical line with no timestamp,
  no level and no logger name, and the same advisory-only string was what the
  user saw in the failure event.
- **Fix.** `_clean_cli_error()` strips ANSI escapes, folds CR/LF into a single
  line, and drops `#`-prefixed advisories unless they are all tailscale printed.
  The failure now reads `tailscale exit 1: 502 Bad Gateway:`.

### Tests

`tests/test_log_audit_170926.py` (new) guards the INFO demotion and the
`color` option. `TestCliErrorSanitiser` in `tests/test_taildrop_service.py`
replays the exact 11:06:41 stderr. 3,244 passed, 5 skipped.

---

## v20.3.1 — 2026-09-10

### Fixed — a finished TikTok broadcast was reported LIVE on every poll

The 2026-09-10 10:55-11:26 window of `omnidl_debug.log` shows the same account
answered two ways on every 5-minute cycle: `pass-4 status=4 (not live)`
followed by `LIVE via pass-2 roomId=7679022730909469458`, seven cycles in a
row.

- **Pass-4's ended verdict never left pass-4.** `/api-live/user/room/` is the
  canonical live-status endpoint, and it reported `status=4` (ended). The page
  passes meanwhile kept reading the finished broadcast's `roomId` out of
  `SIGI_STATE`, and `webcast/room/check_alive` kept answering `alive=True` for
  it — the exact stale-room case `BUG-TT-ENDEDROOM` already guards through
  `_ENDED_ROOM_IDS`. Pass-4 simply never wrote to that set. It now calls
  `_mark_room_ended()` for the stale `roomId` when the endpoint reports
  `status` 4 or 5, so pass-1 and pass-2 stop announcing the dead room within
  one cycle. Pass-4 still never *reads* the set, so a restart that reuses the
  same `roomId` is detected immediately.

### Fixed — a network outage was recorded as a healthy strategy probe

At 11:11:01 every TikTok request failed with `curl: (7) Failed to connect to
www.tiktok.com:443`. `HealthDaemon` logged `probe pass4_api_live_room ok` and
then `strategy pass3_user_api re-enabled` — a connection failure reset the
failure counter of a pass that had been disabled for being permanently blocked
by bot-detection.

- **Pass-2, pass-3 and pass-4 returned a bare `None` on a network error**, and
  `None` means "this account is not live". They now set the new
  `LiveCheckContext.network_error` flag, and `HealthDaemon._probe_all()`
  records neither a success nor a failure for a probe that never reached
  TikTok. Pass-1 was already correct: it raises `RuntimeError`.
- **A pass-0 cooldown skip counted as a healthy probe.** While the shared
  10013 backoff is active (`BUG-TT-PASS0-GLOBAL`, up to 30 minutes), `check()`
  returns before any request is made. That early return now sets
  `ctx.unavailable`, so a known-blocked endpoint is no longer credited with a
  passing probe every 5 minutes.

### Fixed — a Facebook photo album was sent as "Unknown - ... .zip"

The photo post at 10:54 landed in `facebook_20260910_122123586303380258` and
Taildrop shipped it as `Unknown - 2026-09-10 - Facebook Photo.zip`.

- **The synthetic photo `MediaInfo` had `uploader=""`.** Every later name is
  derived from it: `build_filename_from_task()` falls back to `"Unknown"` and
  the gallery-dl output folder falls back to `facebook_<date>_<id>`. gallery-dl
  runs the download anyway and its `--dump-json` already carries the uploader,
  title and thumbnail, so `yt_dlp_engine.extract_info()` now asks
  `GalleryDlEngine.extract_info()` on the photo path instead of inventing a
  blank author. If that lookup fails the old synthetic `MediaInfo` is still
  returned, so the photo path cannot regress into a hard failure.

---

## v20.3.0 — 2026-09-09

### Fixed — Facebook posts containing images could not be downloaded at all

Analysing any Facebook photo post failed, on the desktop app and through the
Remote API alike. The debug log for the 2026-09-09 17:18-18:05 session shows
eight attempts across five posts, each stopping after the cookie was decrypted
and never reaching a queued task (`omnidl_debug.log` 17:24:29 - 18:01:04).

- **A Facebook photo post is not recognised as a photo post.** `FacebookIE`
  scans the page for `video_data`, finds none on a photo post, and ends at
  `raise ExtractorError('Cannot parse data')`. Every photo fallback in the app
  matched only Instagram's wording — `"There is no video in this post"` and
  `"No video formats found"` — so this error fell through to the generic retry
  path: three yt-dlp attempts, then a hard failure. The gallery-dl route that
  handles these posts was never reached. `extract_info()` now treats
  `Cannot parse data` on a `facebook.com` URL as the photo signal and returns
  the same synthetic `MediaInfo(source_engine="gallery_dl")` it already returned
  for Instagram. `DownloadManager._run_task()` and
  `_should_fallback_to_gallery_dl()` match it too, so a Remote API client that
  does not forward `source_engine` still lands on gallery-dl.
- **gallery-dl cannot open a `/share/p/` link.** It has no extractor for that
  form and exits with `Unsupported URL`. The resolution to the canonical
  `story.php` URL lived inside `yt_dlp_engine.extract_info()` only, so the
  Remote API — which submits the URL the user pasted, not the resolved one —
  handed gallery-dl a link it always rejected. `normalize_gallery_dl_url()`
  now resolves `/share/{p,v,r}/` before its existing `story.php` rewrite.
- **A post that mixes images with a video only ever saved the images.**
  gallery-dl's `FacebookSetExtractor` yields the photo set and skips video
  items. Facebook feed posts now take the same two-engine path Instagram
  carousels already used: gallery-dl saves the images, then a yt-dlp pass with
  `bestvideo+bestaudio/best` fetches the video items into the same folder. The
  same pass covers a photo-with-music post, which Facebook serves as a video.
  When gallery-dl finds no photo set at all, its error is held back until that
  pass has run and only raised if both engines come up empty.

### Added

- `is_facebook_post_url()` in `gallery_dl_engine` — matches `story.php`,
  `permalink.php`, `/<user>/posts/<id>` and `/share/p/`, the URL forms that can
  hold photos, photos with music, or photos plus a video.
- `tests/test_facebook_post_photo_fix.py` — 19 regression tests covering URL
  classification, share-link resolution, the `Cannot parse data` route on all
  three fallbacks, and the two-engine download.

### Notes

- Full suite after the change: 3235 passed, 5 skipped (`python -m pytest tests/ -q`).
- Version bumped 20.2.1 → 20.3.0.
- A photo-only Facebook post now costs one extra yt-dlp extraction (it returns
  no items and is ignored), the same trade-off documented for Instagram
  carousels in v20.1.0.

---

## v20.2.1 — 2026-09-09

### Fixed — debug-log audit (2026-09-09 15:02-15:31 session)

Reading one 30-minute window of `omnidl_debug.log` end to end turned up four
defects. Two were silent: they never produced an error line, so no bug report
would ever have named them.

- **Every TikTok download ran the "audio-only" salvage path (data-loss risk).**
  `_capturing_pp_hook` recorded the video codec from the *first* postprocessor
  event that reported `status="finished"`. That first event comes from
  `_FacebookMetaFixupPP`, which runs at `pre_process` with an info dict that has
  no `vcodec` key at all, so the recorded value was `""` — and the BUG-TT-EFF
  audit treats `""` exactly like `"none"`. Consequence: every finished TikTok VOD
  was re-opened with FFprobe, and when FFprobe was missing or failed
  (`_probe_result is None` leaves `_probe_confirmed_no_video = True`) the
  completed file was **deleted** and the BUG-TT-SHOP-3 client ladder ran for
  nothing. Only the BUG-TT-PROD FFprobe rescue kept this invisible — the log
  shows it firing on three ordinary videos (15:27:22, 15:28:23, 15:28:39), each
  labelled "product link video" though none was a product link. Fixed: an event
  that carries no `vcodec` is no longer treated as a codec reading.
  (`infrastructure/downloader/yt_dlp_engine.py`)

- **`_FacebookMetaFixupPP` was registered on every platform.** The processor
  already returns early unless `extractor_key == "Facebook"`, so it was harmless
  in itself — but registering it is what injected the empty `pre_process` event
  behind the defect above. It is now added only for Facebook URLs.
  (`infrastructure/downloader/yt_dlp_engine.py`)

- **Facebook photo posts from `/share/p/` links failed permanently.** A share
  link resolves to `story.php?story_fbid=X&id=Y`. gallery-dl's `USER_PATTERN`
  excludes `permalink.php` and `photo.php` but not `story.php`, so it captured
  `story.php` as a *profile name*, dispatched to `FacebookUserExtractor`, and
  crashed with `An unexpected error occurred: KeyError - 'set_id'` (task
  7f2b7e61, both attempts). The URL is now rewritten to the
  `/<owner_id>/posts/<story_fbid>` form, which routes to `FacebookSetExtractor`
  — that one parses the post page and falls back to the single-photo path when
  the post holds no photo set. The rewrite applies only when both ids are
  numeric; anything else is passed through untouched.
  (`infrastructure/downloader/gallery_dl_engine.py`)

- **gallery-dl extractor crashes were retried.** `KeyError - 'set_id'` is
  deterministic, so the second attempt burned another 7 s to reproduce it
  exactly. `"an unexpected error occurred:"` joins `"extractor error"` in
  `_HARD_ERROR_KEYWORDS`; transient network failures stay retryable.
  (`infrastructure/downloader/download_manager.py`)

### Changed

- **A recovered retry no longer logs at ERROR.** yt-dlp's own error records are
  demoted to DEBUG whenever DownloadManager still holds a retry, via a new
  `DownloadTask.has_retry_remaining` flag. A Facebook "Cannot parse data" at
  15:23:56 was logged at ERROR even though attempt 2 completed the download at
  15:24:40. The BUG-TT-RETRYNOISE demotion previously covered TikTok VODs only.
  A download that really fails is still logged at ERROR by DownloadManager
  ("Task failed after N attempt(s)"). (`domain/models/download_task.py`,
  `infrastructure/downloader/download_manager.py`,
  `infrastructure/downloader/yt_dlp_engine.py`)

### Tests

`tests/test_log_audit_20260909.py` — 14 tests covering the URL rewrite (and its
pass-through cases), gallery-dl extractor dispatch for both URL forms, the
hard-error classification, and the pp_hook codec capture in all three states
(no event, empty event, real `vcodec="none"`). Full suite: 3216 passed,
5 skipped. Version 20.2.0 → 20.2.1.

---

## v20.2.0 — 2026-09-09

### Fixed — Facebook audit (desktop + Remote API)

A full audit of the Facebook surface (Story engine, photo/album routing, Live
monitoring, and the Remote API endpoints that front them) turned up six defects.

- **Facebook Story: silent files reported as having sound (Windows).**
  `facebook_story_engine._has_audio_stream()` built the ffprobe path as
  `Path(ffmpeg_bin).parent / "ffprobe"`, dropping the `.exe` suffix on Windows —
  the only platform besides macOS where the Story engine runs. The probe raised
  `FileNotFoundError`, the `except` branch returned `True` ("assume audio
  present"), and `_ffmpeg_download_with_audio()` accepted a video-only DASH
  result while logging "Audio captured via ffmpeg DASH demuxer". The helper now
  takes `FFmpegLocation.ffprobe_bin` directly and short-circuits to `True` only
  for the explicit `"<not found>"` sentinel.
- **Facebook Story ignored the per-task download folder.** The Story route in
  `DownloadManager._run_task()` called `download_story(url, config, …)`, which
  always wrote to `config.download_dir`. Every other engine honours
  `DownloadTask.output_dir`, so a Story queued from the Download tab with a
  custom folder — or any task carrying an explicit `output_dir` — landed in the
  default directory instead. `download_story()` gained an `output_dir` parameter
  and the router now forwards `task.output_dir`.
- **Facebook Live monitoring leaked a libcurl handle per check.**
  `utils/facebook_live_checker.check_facebook_live()` never closed its curl_cffi
  session. Each session pins a libcurl easy handle whose native memory CPython's
  GC thresholds cannot see, and `LiveMonitorService` calls this once per interval
  per watched page, indefinitely. The two page fetches are now wrapped in
  `try/finally: session.close()`, matching
  `tiktok_live_checker._fetch_tiktok_profile_page()`.
- **Story download leaked a socket on the stream deadline.**
  `_download_cdn_url()` returned from inside the `iter_content()` loop when the
  300 s deadline fired, leaving the streaming `requests` response open until the
  GC ran. It is now held in a `contextlib.closing()` block.
- **Audio track wrongly rejected as "video-only".** `_derive_audio_url()` was fed
  the raw captured CDN URL, which for an intercepted DASH *segment* still carries
  `bytestart`/`byteend`. `_probe_audio_url()` then added its own
  `Range: bytes=0-8191` header on top of that window, so the CDN could answer
  with a slice the probe read as "no audio track" and the story was muxed
  silently. The candidate is now derived from `_full_video_url()` output.
- **`GET /api/ping` reported the wrong version.** The response hard-coded
  `"version": "1.0.0"` while the app was at 20.1.0. It now reads
  `utils/__version__.py`. This is the same response that advertises the
  `facebook_story` capability, so clients gating on it were reading a stale
  payload.

### Housekeeping

- `_clear_crashed_flag()` no longer leaves an `omnidl_prefs_*.tmp` file behind in
  the user's browser profile when the atomic Preferences write fails.
- New suite: `tests/test_facebook_audit_090926.py` (19 tests).
- Version bumped 20.1.0 → 20.2.0.

---

## v20.1.0 — 2026-09-09

### Added — Facebook photos and albums

- **Facebook photo posts and albums now download** on the desktop app and the
  Remote API. yt-dlp's Facebook extractor matches none of the photo URL forms
  (`/photo/?fbid=`, `/photo.php?fbid=`, `/media/set/?set=`, `/<user>/photos/…`),
  so it raised "Unsupported URL" — a hard error that the existing gallery-dl
  photo fallback never saw. Those URLs are now recognised by
  `gallery_dl_engine.is_facebook_photo_url()` and routed straight to gallery-dl:
  - `DownloadService.analyse_url()` short-circuits to `GalleryDlEngine.extract_info()`,
    so `/api/analyse` and the desktop Home tab both return
    `source_engine="gallery_dl"` instead of failing.
  - `DownloadManager._run_task()` forces the gallery engine for these URLs even
    when the client sent the default `source_engine="yt_dlp"` (the iOS PWA and
    any third-party Remote API client do).
  - `_should_fallback_to_gallery_dl()` also accepts "Unsupported URL" for a
    Facebook photo URL, so any path that still reaches yt-dlp first recovers.
- **Albums get their own folder.** `GalleryDlEngine.download()` isolated output
  only for Instagram posts, so a 60-photo Facebook album emptied into the
  download root. Facebook photo/album downloads now land in
  `<uploader>_<YYYYMMDD>_<fbid|set-id>/`, which also lets Taildrop send the set
  as a unit.
- `GalleryDlEngine.extract_info()` no longer titles every untitled single-item
  gallery "Instagram Photo" — it uses the detected platform.
- `--dump-json` timeout raised from 30 s to 90 s; large albums emit one JSON
  line per photo and were being cut off.

### Added — Facebook live monitoring

- **The Live Monitor watches Facebook pages and profiles** on the desktop tab
  and through the Remote API / PWA (`POST /api/monitor` — no new endpoint
  needed). Paste `facebook.com/<page>`, `facebook.com/<page>/live`,
  `facebook.com/profile.php?id=<id>` or a `/people/<name>/<id>/` link; when the
  page goes live OmniDL records it with the existing Facebook live pipeline.
- New `utils/facebook_live_checker.py` — `is_facebook_profile_url()`,
  `extract_facebook_username()` and `check_facebook_live()`. The checker probes
  the page's `/live/` tab first (Facebook redirects it to the running broadcast)
  and falls back to the profile HTML, matching `"is_live_streaming":true`,
  `"broadcast_status":"LIVE"` and friends. Requests use the same curl_cffi
  Chrome TLS impersonation as the TikTok checker, because plain-requests traffic
  is served a logged-out shell.
- The live URL handed to the recorder is always `/<page>/videos/<id>` — the form
  yt-dlp's `FacebookIE` can resolve. A live marker with no resolvable video id
  reports "not live" instead of queueing a task that could only fail.
- A Facebook cookie is required and checked before the watch is accepted
  (`c_user` + `xs`), the same way the Instagram profile watcher checks its
  cookie: logged-out page HTML carries no live markers at all, so without one
  every check would silently report "not live".
- Rate-limit errors carry `429` in the message so `LiveMonitorService`'s
  exponential backoff triggers in every UI language, not only English.
- New keys in all three languages (`err.fb_*`, `err.profile_watch_needs_fb_cookie`,
  `err.no_username_from_facebook_url`).

---

## v20.0.0 — 2026-06-22 to 2026-09-04

### Added — 2026-09-04 batch conversion

- **Convert several files in one go, with a configurable parallel limit.**
  The desktop Convert tab now has a checkbox on every file card plus a
  "Select all" toggle, so Convert acts on the files you picked instead of
  everything in the list (nothing ticked still means "all pending", so the
  old behaviour is what you get if you never touch a checkbox). A "Parallel"
  spinner (1-8) sets how many FFmpeg processes run at once and is persisted as
  `convert_max_concurrent`.
- **Remote API: `POST /api/files/convert/batch`** queues up to 100 files with
  one set of encode settings and reports partial success — a missing path
  lands in `errors` while the rest still start. `GET`/`POST
  /api/convert/concurrency` read and set the same parallel limit, shared with
  the desktop tab. The web UI grew a "Select many" mode in the Files tab.
- The post-download auto-convert queue in `download_service` read a hard-coded 2
  as well; all three queues now share `convert_max_concurrent`.
- `ConvertQueue.set_max_concurrent()` swaps the semaphore; each worker captures
  the semaphore it will wait on at submit time, so a resize mid-batch cannot
  leave a slot permanently held.

### Fixed — 2026-09-04 Facebook Story reliability and safety

- **Story capture refused to work while the browser was open, without saying so.**
  Chromium is single-instance per profile: launching a second copy forwarded the
  command line to the running browser and dropped `--remote-debugging-port`, so
  CDP never came up, the connect loop burned 30 s, and the failure surfaced as a
  vague connection error. The engine (and the Special tab's new pre-flight check)
  now detects a running Brave/Chrome up-front and says which browser to close.
  `_is_browser_running()` gained a macOS branch (`pgrep -x`).
- **Launch hardening:** the debug endpoint is pinned with an explicit
  `--remote-debugging-address=127.0.0.1`, and `--user-data-dir` is now stated
  rather than inherited — the same directory, but the single-instance rule is
  visible at the call site.
- **The desktop timeout (45 s) was below the 40 s audio wait**, so stories often
  downloaded silent from the Special tab while the same URL worked from the
  queue. Both paths use the 90 s default now.
- **`/api/analyse` accepted Story permalinks that `/api/download` would reject**
  on a Linux server. One `cdp_only_reason()` helper now gates both, and
  `GET /api/ping` reports `facebook_story` so a client can hide the option.
- `_normalize_url` / `_full_video_url` no longer drop repeated query parameters.

### Added — 2026-09-03 multi-language engine errors

- **Engine and download errors are translated.** Download engines used to raise
  hard-coded Vietnamese text, and `download_manager` decided "retry or give up" by
  substring-matching that text — so an English or Chinese user read Vietnamese error
  messages, and translating them would have silently broken the retry and gallery-dl
  fallback paths. Every engine error now carries a stable `err.*` key
  (`_error_key`/`_keyed_exc` in `yt_dlp_engine.py`); retry and fallback classification
  reads the key, and the UI renders the message in the active language
  (`en` / `vi` / `zh`). `tests/test_i18n_engine_messages.py` fails the build if a
  user-facing Vietnamese literal creeps back into the engine layer.

### Added — 2026-08 TikTok Accounts Pool usability

- **One account per browser profile.** `list_browser_profiles()` enumerates the Chrome /
  Edge / Brave profiles on the machine, so a browser can now yield more than one TikTok
  account. The add-account form picks the profile, and the account name is suggested from
  browser + profile.
- **Cookies are health-checked before they are accepted.** `inspect_tiktok_cookie()`
  returns `ok` / `not_logged_in` / `expired` / `unreadable` / `missing` plus a
  session fingerprint. An anonymous jar (only `ttwid`/`tt_csrf_token`) is no longer
  accepted as a login, an expired jar is rejected with a reason, and a jar whose
  fingerprint matches an existing account is reported as a duplicate instead of being
  added twice.
- **Accounts can be renamed and refreshed in place,** and deleting one deletes its
  cookie file instead of leaving it in `cookies/`.

### Fixed — 2026-08-31 logging feature audit

- **Every log line was attributed to the module `logging`.** `logging.currentframe()` is
  `sys._getframe(1)` on CPython 3.11+, i.e. `_InterceptHandler.emit`'s own frame, so the
  frame walk never started and all 119 named lines in `report.log` read
  `[INFO    ] logging - ...`. The handler now walks up from `sys._getframe(0)` until it
  leaves `logging/__init__.py`.
- **`setup_logging()` crashed a windowed build.** `logger.add(sys.stderr)` ran
  unconditionally, but `sys.stderr` is `None` in a PyInstaller `--windowed` build (both
  the Windows and macOS releases) and loguru raises
  `TypeError: Cannot log to objects of type 'NoneType'` — startup aborted before any log
  file existed. The stderr sink is now conditional.
- **The Verbose-logging toggle raised out of the Settings slot.** `logger.remove()` in
  `setup_logging()` drops every sink including an earlier debug sink, but
  `_debug_sink_id` kept the dead id: turning verbose logging off raised `ValueError`, and
  turning it on again was a silent no-op that left `omnidl_debug.log` empty. The id is
  reset on setup and its removal is guarded.
- Turning verbose logging off restored the root level to a hardcoded `INFO` instead of
  the level `setup_logging()` was configured with; `log_dir.mkdir()` ran outside the
  `try/except OSError` that guards the file sink, so an unwritable log directory crashed
  startup instead of degrading to stderr-only logging.

### Fixed — 2026-08-30 debug-log audit

- **An ended TikTok room was re-detected as live forever.** `webcast/room/info` answered
  `status=4` (ended) 641 times for one room, but the live page kept serving that same
  stale `roomId` in `SIGI_STATE` and `check_alive` kept answering `alive=True`, so pass-1
  and pass-2 logged "LIVE" for 9.5 hours after the stream finished. Ended rooms are now
  remembered in a TTL-bounded set (`_mark_room_ended`, 30 min) that pass-1/pass-2 consult
  before paying for a `check_alive` call; the mark is cleared as soon as `room/info`
  reports `status=2` again, and pass-4 never consults it, so a restart that reuses the
  roomId is still detected immediately.
- **Pass-0's 10013 cooldown was keyed per username.** TikTok's `10013` (signature
  required) is a property of the endpoint, not of an account, but the cooldown expired
  after a flat 120s while the monitor polls every ~70s — 1,395 calls with zero successes
  over 33 hours. It is now one shared deadline with exponential backoff (2 min → 30 min),
  reset the moment the endpoint answers anything but 10013.
- **The health daemon counted a blocked endpoint as a working strategy.** A strategy that
  returned `None` because it could not reach its API at all was recorded as a probe
  success, so it was never disabled. `LiveCheckContext.unavailable` now separates "not
  live" from "could not function", and each probe gets its own context.
- **`DELETE /api/convert/{job_id}/file` answered 500 on a locked file.** The queue routes
  already mapped `OSError` to `409`; the convert route was missed.
- **yt-dlp's own ERROR lines were reported for recoverable retries.** The TikTok fallback
  ladders build a `YoutubeDL` per attempt and inherit the diagnostic logger, so every
  attempt that was *expected* to fail wrote an ERROR record — 39 ERROR lines in a window
  where 41/41 downloads completed. Errors are demoted to DEBUG for the duration of a
  ladder (a genuinely failed download is still logged at ERROR by `DownloadManager`), and
  raw ANSI colour escapes are stripped from yt-dlp messages before they reach the log.
- **Windows exit codes were printed unsigned** — "ffmpeg exited with error 4294967256"
  instead of `-40`.

### Fixed — 2026-08-30 Facebook Live follow-ups

- **DASH-only broadcasts (BUG-FB-LIVE-DASH/FMT/HDR).** The live probe only inspected HLS
  formats, so `story.php` and `/<page>/videos/<id>` broadcasts (served as DASH) fell back
  to the VOD path and were captured as truncated clips reported as completed.
  `_facebook_live_manifest_url` now also fetches the DASH manifest and treats
  `MPD@type="dynamic"` as the counterpart of a missing `#EXT-X-ENDLIST`; the recorder
  accepts an `.mpd` URL and drops `-http_persistent` (HLS-demuxer-only, and it aborts
  FFmpeg on a DASH input). The yt-dlp fallback selector gained a `/bv*+ba` tail, because
  a bare `best` means "best *muxed*" and a DASH broadcast has no muxed format — it
  aborted the fallback with "Requested format is not available". The `-headers` builder
  no longer filters cookies to `tiktok` domains, which had dropped the Facebook Referer.

### Fixed — 2026-08 cookie / account-pool audit

- An all-paused account pool hard-failed every TikTok download instead of falling back to
  the global cookie (`has_usable_account()`).
- Extracted cookie jars leaked on rejection or cancel; the 30-day cleanup deleted cookie
  files the config still referenced; the health check missed the `.txt` → `.enc` rename;
  a pool rebuild reset live slot counters (`adopt_state()`); the plaintext cookie cache
  was unbounded and never invalidated (`_COOKIE_CACHE_MAX`, `invalidate_cookie_cache()`).

### Fixed — 2026-08 Taildrop filenames (BUG-TD-NAME)

- Taildrop used to force an ASCII name on **every** send, because older iOS/macOS
  receivers answer `400 Bad Request: invalid filename` for non-ASCII names. That
  flattening deleted Vietnamese diacritics, CJK and emoji outright, so the phone received
  "YUSUKI cu y tht ng yu" or a name starting with a bare " - ". The full Unicode name is
  now attempted first and `to_ascii_filename()` is used only as a retry, so a modern
  receiver keeps the complete name and an old one still gets a readable fallback.
  Transliteration keeps what has an ASCII equivalent (`đ` → `d`, `ø` → `o`, `ß` → `ss`),
  drops segments it emptied out, and never returns an empty stem.
- **Invisible Unicode TAG characters** (U+E0000-U+E007F — the payload of emoji flag
  sequences) are stripped from filenames: they are legal on disk but Taildrop peers
  reject them, and the ASCII retry then dropped the whole name segment around them.
- `build_filename_from_task()` no longer reuses a directory output (gallery-dl albums are
  named after the account only, with no date/title/id) and no longer discards every title
  when `uploader` is empty.

### Testing — 2026-08-30 to 2026-09-03

- New regression suites: `test_log_audit_300826.py`, `test_log_feature_audit_310826.py`,
  `test_cookie_account_audit_2026.py`, `test_tiktok_account_pool_ux.py`,
  `test_i18n_engine_messages.py`, `test_api_file_lock_409.py`, `test_api_doc_convert.py`,
  `test_doc_convert_service.py`, `test_soffice_locator.py`.

### Added — 2026-08-28 document conversion

- **Documents tab + `/api/docs/*` — Markdown ↔ PDF, HTML ↔ PDF, Office ↔ PDF.**
  New `app/services/doc_convert_service.py` with seven routes: `md→pdf`, `md→html`,
  `html→pdf`, `pdf→md`, `pdf→html`, `pdf→docx`, `office→pdf`. Markdown is rendered by
  `markdown`, HTML→PDF by WeasyPrint, PDF text extraction by `pypdf`, and the Office
  routes shell out to a headless LibreOffice located by the new
  `utils/soffice_locator.py`. LibreOffice is optional: `capabilities()` reports which
  back-ends the host has, the desktop tab greys out what is unavailable, and the API
  answers 503 with a plain-language reason instead of an opaque traceback.
  Desktop UI is `ui/tabs/doc_convert_tab.py` (nav key `docs`, batch queue, per-file
  progress, cancel); the PWA gains a matching "Tài liệu" tab; the Remote API gains
  `GET /api/docs/capabilities` and `POST /api/docs/convert`.
- Output files never overwrite: a name collision becomes `name (1).pdf`.

### Security — 2026-08-28 document conversion

- **HTML→PDF resource sandbox.** An HTML or Markdown document can reference
  `file:///etc/passwd` or `http://internal-host/` through `<img>`, `<link>`, `@import`
  or a CSS `url()`, and WeasyPrint would fetch both — local file disclosure plus SSRF
  from whatever machine runs OmniDL, which on the Remote API path is reachable by any
  client. Every render now uses a `URLFetcher` subclass that permits only `data:` URIs
  and `file:` URLs inside the source document's own folder; anything else is refused,
  logged, and skipped (the page still renders, minus that resource). Confinement uses
  exact path-component matching, so `/docs` does not accept `/docs_evil`.
- **`GET /api/files/serve` no longer renders active content inline.** `.html`, `.htm`,
  `.xhtml`, `.svg`, `.xml`, `.xsl`, `.xslt`, `.mhtml` and `.mht` are now sent as
  `application/octet-stream` with `Content-Disposition: attachment`. Served inline they
  execute in the API's own origin, where the PWA keeps the bearer token — a stored XSS
  for any such file in `download_dir`, now including the `.html` the document converter
  can write from an arbitrary Markdown source. Video/audio/image previews are unchanged.
- **`POST /api/docs/convert` input limits.** `source_path` and `out_dir` are both
  confined to `download_dir`; a source over 200 MiB is rejected with 413; concurrent
  conversions are bounded by a semaphore of 2, matching the archive endpoints, so a
  burst of calls cannot fork an unbounded number of `soffice` processes.


### Fixed — 2026-08-26 log review (follow-up)

- **Facebook Live on DASH-only pages (BUG-FB-LIVE-DASH)** — the live probe only inspected
  HLS formats, but `story.php` and `/<page>/videos/<id>` broadcasts are served as DASH
  (`dash-lp-pst-v`, bare representation ids). Every such stream fell through to the VOD
  path again and was captured as a truncated clip reported as a completed download.
  `_facebook_live_hls_url` is now `_facebook_live_manifest_url` and also fetches the DASH
  manifest, treating `MPD@type="dynamic"` as the DASH counterpart of a missing
  `#EXT-X-ENDLIST`. The direct-FFmpeg recorder accepts the `.mpd` URL, dropping
  `-http_persistent` (an HLS-demuxer-only option that aborts FFmpeg on a DASH input) and
  opening the dash demuxer's extension allow-list instead.
- **Locked output file returned HTTP 500** — `DELETE /api/queue/{id}/file` and
  `POST /api/queue/{id}/rename` let Windows' `PermissionError` (file still open in FFmpeg,
  a Taildrop transfer, or a player) escape as an unhandled ASGI exception with a full
  traceback in the debug log. Both now answer `409` with the OS message.

### Fixed — 2026-08-26 audit pass (v19.6.0)

- **Facebook Live recording (BUG-FB-LIVE)** — yt-dlp's `FacebookIE` never reports an
  in-progress broadcast as live, so OmniDL downloaded a short VOD clip instead of the
  stream. Added an HLS `#EXT-X-ENDLIST` probe (`_facebook_live_hls_url`) and a direct-FFmpeg
  recording path shared with TikTok Live. Side fixes surfaced by this: a plaintext session
  cookie left on disk after a hard analyse error (BUG-FB-COOKIE-LEAK), and failed Facebook
  downloads leaving no trace in the debug log (BUG-FB-DIAG).
- **Pause/resume state machine** — `DownloadTask.pause()`/`resume()` now refuse invalid
  state transitions at the domain layer instead of silently no-opping; a paused
  `PROCESSING`/terminal task could previously become un-clearable and later resumable from
  the dead. The REST API now returns `409` instead of a silent success.
- **Task/record lifecycle** — a new `EventBus.DOWNLOAD_REMOVED` event and
  `DownloadService.clear_file_record()` keep Queue widgets, History rows, and the web UI in
  sync when tasks are purged or a file is deleted/renamed elsewhere.
- **Files tab (Remote API)** — `GET /api/files/browse` no longer 404s when `download_dir`
  doesn't exist yet; broken symlinks are listed via an `lstat()` fallback instead of being
  dropped; `DELETE /api/files/delete` deletes a symlink as itself instead of following it;
  `POST /api/files/rename` no longer drops the file extension when the new name omits one.
- **Live Monitor** — `cancel()`/`check_now()` no longer zero `last_check`, which used to
  restart a just-stopped recording within seconds or kill an in-flight check; the
  `MAX_MONITOR_URLS` cap now counts only active watches, not finished history rows (was
  silently blocking new watches after ~20 recordings); progress SSE no longer fires on
  every poll tick while merely recording.
- **Queue / Batch tab** — desktop select-mode checkboxes now reach tasks that finish
  mid-poll and untick on exit; the Pause button is hidden for `gallery_dl` tasks (no pause
  hook); the batch status line survives a language switch; "Select all" skips `ERROR` rows;
  a batch where every submission fails now reports it instead of looking like a no-op.
- **History tab** — download history is now sorted newest-first on load (the on-disk JSONL
  file is append-only/oldest-last); the previous unsorted read showed the oldest downloads
  first and the over-limit trim deleted the newest records instead of the oldest.
- **Web UI / i18n** — editing the URL box after "Analyse" no longer silently redownloads
  the previous video (most visible on Kuaishou); remaining hard-coded English strings in
  the analyse/preview flow and queue status badges are now translated; error toasts get a
  readable multi-line style; the monitor's "paused" tag no longer reuses the Pause button's
  own label; the monitor interval picker now offers 60/180/300/600s, matching the
  server-side range.
- **Settings tab** — the Remote API server now always gets the real `DownloadService`
  (never the desktop facade, which lacked `clear_file_record` and broke Files-tab delete
  after any Tailscale/HTTPS toggle); a TikTok cookie the config layer silently rejected no
  longer shows a false success toast.

### Testing — 2026-08-26

- Added 7 new regression suites for the audit above: `test_bug_fb_live.py`,
  `test_api_files_tab_audit.py`, `test_history_tab_audit.py`, `test_live_monitor_audit2.py`,
  `test_queue_batch_audit.py`, `test_interface_audit_2026.py`,
  `test_settings_tab_audit_2026.py`. Full suite: 2782 passed, 5 skipped.

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
