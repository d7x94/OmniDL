English | [Tiếng Việt](CHANGELOG.vi.md) | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

All notable changes to OmniDL are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

No unreleased changes yet.

---

## v20.3.21 - 2026-10-09

### Fixed

- History: cards and the Clear button now restyle when the theme changes instead of keeping the old colors
- History: the Rename button check resolves a relative filename against the download folder, so folder (gallery-dl) rows no longer show Rename

---

## v20.3.20 - 2026-10-08

### Fixed

- Main window: the minimum width is now 1290, so the top-bar tabs no longer overlap in Vietnamese at the smallest window size
- Editor: the side panel rows (text overlay options, color and effect sliders) wrap so no control is cut off in a 380 px panel

---

## v20.3.19 - 2026-10-06

### Fixed

- Settings: changing the theme from Settings now updates the top-bar theme button label
- Settings: a TikTok account cookie refresh no longer stays stuck as "refreshing" after it finishes
- Settings: a failed API restart after a Tailscale HTTPS reset rolls HTTPS back and refreshes the token label, like the enable path does
- Settings: Global CDP extract with an unsupported browser shows the error before the confirm dialog instead of after it
- Settings: slider values (e.g. concurrent downloads) save on every change instead of being lost when the app closes within 500 ms
- Settings: Remote API switch, rotate token and Tailscale HTTPS controls are locked while one of them runs, so two actions cannot overlap
- Settings: a language or theme change waits for running Settings workers before rebuilding the page, so a finished worker cannot re-enable a button of a new run

---

## v20.3.18 - 2026-10-05

### Fixed

- Home tab: switching theme now restyles the welcome screen, result card, quality cards and badges instead of leaving unreadable colours from the old theme
- Home tab: a thumbnail still loading for the previous item no longer appears on a result that has no thumbnail
- Home tab: a live result clears the previous status line (photo note or error text)
- Home tab: the Browse and Add to queue tooltips follow the UI language
- Remote API: POST /api/analyse and /api/clipboard/analyse cancel the analyse worker when the client disconnects or the request times out

---

## v20.3.17 - 2026-10-03

### Fixed

- Special Download: a second download no longer leaves Open folder / View buttons dead; the previous result is cleared when a new run starts
- Queue tab's Clear no longer fails when the optional API extra (fastapi) is not installed
- History tab's Clear all no longer removes tasks that still have an active Remote Convert job
- Analyse stream: a client dropping mid-extract no longer evicts the still-running job, so reconnecting does not start a duplicate extract
- Remote API finished-clear reports only real queue tasks as excluded (file-browser converts are no longer counted)
- Remote API file delete also clears the matching history record, so History no longer offers Preview / Send / Convert on a deleted file
- Editor tab's Show/Hide panel labels stay correct when the language is changed while the tab is hidden

---

## v20.3.16 - 2026-10-02

### Fixed

- Analyse button's stale callback (from a superseded request) no longer wipes a newer analysis's cancel button / spinner state
- Settings > Clear Data now also stops a running Remote API server, stops Tailscale Serve, and rebuilds the TikTok account pool instead of leaving them pointed at deleted config
- Remote API toggles (API server, Tailscale HTTPS, rotate token) add a busy-lock and roll the switch back on a failed restart instead of leaving it in a stuck or misleading state
- Editor tab's export and preview no longer race each other; starting an export while a preview is loading is refused instead of corrupting both
- Queue tab's Clear button no longer removes a task that still has an active Remote Convert job running against it
- Home tab surfaces analysis errors via a toast (previously silent in some paths), and its Browse/Add-to-Queue buttons and LIVE badge are translated and tooltipped
- Special Download's Facebook Story preflight check (browser-running detection) no longer blocks the UI thread for up to 5 seconds — it now runs on the worker thread
- TikTok cookie refresh no longer lets a second click start a duplicate extraction for the same account while one is already running
- Post-download multi-file convert (gallery-dl batches) now tracks how many files are still converting instead of flipping the button to "done" after the first one finishes; the custom encoder panel's GPU probe no longer blocks widget construction
- Batch tab's remove-while-analysing no longer races the analysis-count down to -1 and wedging the spinner; Retry is disabled while a batch is running
- Convert tab's file cards now restyle on a theme change instead of staying in whatever theme was active when they were built; a whisper-support probe landing mid-batch no longer re-enables Subtitles early; the file picker's filter text is translated
- Live Monitor no longer imports infrastructure/ directly to check cookie presence (BUG-LM-INFRA-IMPORT); a recording returning to Waiting resets its URL again (BUG-MON-URL); action buttons have tooltips; Force-check-now on an error row re-checks the same guards as Add URL
- Notification panel's title, "Clear all" and the per-entry dismiss tooltip are now translated and update on a language switch
- The analyse-cache's size cap no longer evicts an in-flight SSE job just for being the oldest entry, which broke request deduplication for that URL
- A Windows file lock (AUDIT-02) on 3 more file-mutating routes (queue-file delete's gallery-dl branch, files/delete, files/rename) now answers 409 instead of 500
- `GET /api/queue/{task_id}/file` now refuses to serve HTML/SVG/XML inline, matching `GET /api/files/serve`'s stored-XSS guard
- `GET /api/convert/{job_id}/file` reads job status and filenames from one atomic snapshot instead of separate unguarded attribute reads
- Taildrop transfers that hit an early failure (missing file, no valid target node, or the service shutting down) now report a failure event for every intended node instead of leaving the client's transfer indicator stuck with no terminal signal
- `DELETE /api/history/{task_id}` no longer blocks the event loop while rewriting the history file to disk
- A partially-failed folder delete (`DELETE /api/files/delete`) now still clears the queue/history records for the files it did remove, instead of leaving them all pointed at a mostly-gone path

---

## v20.3.15 - 2026-10-01

### Fixed

- Live Monitor's Clear All and per-row remove no longer delete the partial file of a recording still in progress; they now keep it the same way Stop already did
- Live Monitor's Check Now button resets a row's URL back to the watch page before polling again, so a row that went to an error state no longer gets polled against a stale video URL
- Pasting or pressing Enter with the same URL while an analysis is already running is now a no-op instead of launching a second request; a different URL still cancels the old one and starts over, same as before
- Stopping an analysis in progress now also clears the Home tab's loading view, instead of leaving it stuck
- Renaming a file from the Queue tab now updates Open/Preview/Convert/Edit/Send to the new path instead of the old one
- Batch tab's Retry now also resets a row stuck mid-analysis back to pending, instead of leaving it stuck forever
- Batch tab's Queue All button stays disabled while an analysis or a sequential download run is in progress, instead of re-enabling itself on a checkbox toggle
- Home tab's folder label keeps showing the custom output folder you picked instead of being overwritten by the default download folder on tab refresh

---

## v20.3.14 - 2026-09-30

### Fixed

- "Use cookies from browser" now reads the browser profile picked in Settings > Network for yt-dlp and gallery-dl downloads. Before, yt-dlp read the most recently used profile and gallery-dl its own pick, so a download could run as another account (BUG-COOKIE-PROFILE-DL)
- Facebook Story capture launches the browser on the profile picked in Settings when the same browser is selected, and refuses with a clear message if that profile no longer exists (BUG-COOKIE-PROFILE-DL)
- Cookie files exported by browser extensions keep their `#HttpOnly_` lines: the TikTok account pool no longer rejects a signed-in jar as "not logged in", and TikTok/Facebook Live FFmpeg recordings send the session cookie again (BUG-COOKIE-HTTPONLY)
- Kuaishou downloads attach the session cookie only to Kuaishou hosts. A Remote API download could send it to whatever host its URL named (BUG-KS-COOKIE-HOST)
- A cookie extraction interrupted by a crash no longer leaves the full browser cookie jar (`_tmp_*`) in the cookies folder forever; it is removed at the next start (BUG-COOKIE-TMP-JAR)
- While a cookie extraction runs, Choose and Delete for the same cookie slot are disabled, so the finished extraction cannot overwrite a file just chosen or re-register a slot just cleared (BUG-COOKIE-EXTRACT-RACE)

---

## v20.3.13 - 2026-09-29

### Fixed

- Settings, Cookies per platform, CDP now refuses with a clear message when no browser profile can be resolved, instead of opening the `Default` profile and saving another account's cookies. Pick a profile in the Profile box next to the browser box first. The Global Cookie CDP button, the TikTok account pool and the yt-dlp buttons keep their previous behaviour (BUG-CDP-NO-PROFILE)
- OK.ru per-platform cookie extraction works. OK.ru was offered in Settings but had no cookie domain list, so every extraction, CDP and yt-dlp alike, filtered all cookies away and failed with "no cookies for this platform" (BUG-COOKIE-OKRU)

---

## v20.3.12 - 2026-09-29

### Fixed

- Cookie extraction in Settings, Network now reads the browser profile you pick in the new Profile box, next to the browser box. Per-platform CDP always opened the `Default` profile, so Instagram got the cookies of another account. An unknown profile, a display name instead of a profile folder, or a port already held by another browser session now fails with a clear error. A failed CDP run no longer keeps the old cookie file and reports success (BUG-CDP-PROFILE)

---

## v20.3.11 - 2026-09-29

### Fixed

- Facebook and Instagram photo posts now show their real uploader and title before download, and a gallery-dl login failure is reported as a login error instead of "no content". The metadata probe read a `--dump-json` format that gallery-dl never writes, so it found nothing (BUG-GDL-DUMP)
- X photo links that end in `/photo/N` are downloaded with gallery-dl instead of failing after three retries with "Media #N is not a video" (BUG-X-PHOTO-N)
- X tweets that need a login (NSFW or protected) fail at once with the cookie hint instead of being retried three times (BUG-X-AUTH)
- `t.co` short links now use the X cookie and rate limit (BUG-X-TCO)
- Cancel now stops a pasted Instagram CDN link download straight away (BUG-IG-CDN-CANCEL)
- A network error while streaming a Facebook Story no longer leaves a truncated video and now falls through to the next capture path (BUG-FB-STORY-TRUNC)
- The web UI analyse stream and the clipboard analyse route now refuse Facebook Story and waaw links on hosts without a browser to drive, like `/api/analyse` already did (BUG-API-CDP-GUARD)
- A Facebook profile watch keeps its page URL after Cancel or a live that is already over, so the next poll no longer fails with "no username" (BUG-MON-URL)
- The gallery-dl debug log no longer prints the proxy password or the temporary cookie path (BUG-GDL-LOG-ARGV)
- A gallery-dl error that only contains a word like "generate" is no longer reported as a rate limit (BUG-GDL-RATE-SUBSTR)
- A TikTok task cancelled while waiting for an account slot now ends as cancelled instead of staying queued forever (BUG-TT-POOL-CANCEL)
- The FFmpeg stall message names the real 20 second limit instead of 120 (BUG-TT-STALL-TEXT)
- A Facebook video that fails once with "Cannot parse data" is retried instead of being sent to gallery-dl, when analyse had already found its video formats (BUG-FB-PARSE-VIDEO)
- A `Retry-After` value in an error message is capped at 120 seconds, so one large value no longer parks a worker for hours (BUG-RETRY-AFTER-CAP)
- The Home tab now shows the error when a download cannot be started (for example an unwritable folder) instead of failing silently (BUG-HOME-START-ERROR)

---

## v20.3.10 - 2026-09-28

### Fixed

- When a photo post fell back from yt-dlp to gallery-dl, the cleanup step no longer deletes files that other downloads are writing to the same folder; it now removes only files named after media ids that yt-dlp wrote for this task (BUG-BU-SWEEP)
- A live monitor recording that is cancelled from the queue with its partial file kept (`PARTIAL_SAVED`) no longer leaves the monitor row stuck on "Recording" forever; it ends and the profile watch continues (BUG-MON-PARTIAL)
- Listing a TikTok, Instagram or X profile no longer deletes the decrypted cookie before yt-dlp reads it, so private profiles are listed logged in and no plaintext cookie file is written back (BUG-PROFILE-COOKIE)
- Cancelling a gallery-dl download (Instagram, Facebook or X photos) now stops gallery-dl straight away instead of waiting for it to finish (BUG-GDL-CANCEL)
- A cancelled or failed gallery-dl download no longer leaves the decrypted session cookie file on disk (BUG-GDL-COOKIE-LEAK)
- An Instagram post that gallery-dl cannot download (login required, private, rate limit) is now reported as failed instead of completed with no file (BUG-GDL-IG-SILENT)
- Image-only X posts are now downloaded with gallery-dl instead of failing after three retries with "No video could be found in this tweet" (BUG-X-PHOTO)
- Sending the same TikTok short link to the Remote API twice no longer starts a second recording of the same live (BUG-DUP-CANONICAL)

---

## v20.3.9 - 2026-09-28

### Fixed

- Instagram Live recording no longer logs two asyncio `CancelledError` tracebacks each time the browser closes after capturing the stream URL; stream routes are now removed before Playwright shuts down (BUG-IG-ROUTE-TEARDOWN)
- When FFmpeg fails on an Instagram Live stream, the log now keeps the last 40 error lines whole instead of the last 600 characters, which cut off the line naming the real error (BUG-IG-STDERR-TAIL)

---

## v20.3.7 - 2026-09-20

### Fixed

- Facebook posts with several photos no longer download as an unrelated advert video instead of the real photos (BUG-FB-ADVID)

### Added

- New error message when a Facebook post can only be confirmed as an advert and cannot be downloaded (`err.fb_post_advert_only`)

---

## v20.3.6 - 2026-09-20

### Fixed

- Facebook posts with several photos no longer analysed as the wrong advert video (BUG-FB-ADVID)
- Facebook group posts (`facebook.com/groups/.../posts/...`) are now recognized and can be downloaded

---

## v20.3.5 - 2026-09-20

### Fixed

- Facebook posts with several photos no longer delivered as someone else's advert video (BUG-FB-ADVID)
- A failed Facebook analyse request now appears in the log instead of leaving no trace

---

## v20.3.4 - 2026-09-20

### Fixed

- TikTok: a broadcast that ended was sometimes still reported as blocked/live for up to 30 minutes afterward
- TikTok: a single rate-limit error (HTTP 429) no longer re-enables a detection method that TikTok has actually blocked

---

## v20.3.3 - 2026-09-19

### Fixed

- Hardware encoder detection no longer wastes time probing macOS-only VideoToolbox on Windows and Linux
- Clearer error message when a hardware encoder fails detection

---

## v20.3.2 - 2026-09-17

### Fixed

- Reduced repetitive log messages so real errors and warnings are easier to find in `omnidl.log`
- Fixed a yt-dlp warning that repeated on every TikTok retry
- Taildrop failure messages now show the real error instead of a placeholder warning

---

## v20.3.1 - 2026-09-10

### Fixed

- TikTok: a finished broadcast could still be reported as live on later checks
- TikTok: a network outage was no longer mistaken for a working, healthy detection check
- Facebook photo albums sent via Taildrop no longer named "Unknown"

---

## v20.3.0 - 2026-09-09

### Added

- Recognize more Facebook photo/album/post URL forms

### Fixed

- Facebook posts containing images can now be downloaded (previously failed outright)
- Facebook posts opened via share links (`/share/p/...`) now resolve and download correctly
- Facebook posts mixing photos and a video now save both instead of only the photos

---

## v20.2.1 - 2026-09-09

### Changed

- A download that fails once but succeeds on retry is no longer logged as an error

### Fixed

- TikTok downloads were sometimes re-checked and deleted right after finishing successfully (data-loss bug)
- Facebook photo posts opened via share links no longer fail permanently
- Failed gallery-dl downloads are no longer retried when the failure is not recoverable

---

## v20.2.0 - 2026-09-09

### Fixed

- Facebook Story: a silent video was sometimes wrongly reported as having audio (Windows)
- Facebook Story now saves to the task's chosen folder instead of always the default download folder
- Facebook Live monitoring no longer leaks memory when watching pages for a long time
- Facebook Story download no longer leaves a network connection open after a timeout
- Facebook Story audio track no longer wrongly dropped as missing
- App version reported by `GET /api/ping` corrected

---

## v20.1.0 - 2026-09-09

### Added

- Download Facebook photo posts and albums (desktop app and Remote API)
- Facebook albums now save into their own folder
- Live Monitor can watch Facebook pages and profiles and record when they go live

---

## v20.0.0 - 2026-09-04

### Added

- Convert several files at once, with a configurable parallel-conversion limit
- Remote API: batch file-conversion endpoint
- Engine and download error messages are now translated (English / Vietnamese / Chinese)
- TikTok account pool: one account per browser profile, health-checked before being accepted, with safe rename/refresh and automatic cleanup of removed accounts' cookie files
- Document conversion: Markdown, HTML and Office files to PDF and back (Documents tab + Remote API)
- Archive feature: compress to ZIP/7z (optional password), extract and preview archives
- New downloader for waaw.ac
- Download a pasted, pre-signed Instagram/Facebook CDN link anonymously
- TikTok live detection: added a 4th detection method after anti-bot changes defeated the previous three

### Fixed

- Facebook Story: clearer error when the browser is already open, instead of a vague connection timeout
- Facebook Story: audio is no longer intermittently dropped on slow downloads
- Facebook Story: analysing a link and downloading it no longer behave differently on a Linux server
- Facebook Live broadcasts served only over DASH (not HLS) now record correctly instead of a truncated clip
- An ended TikTok broadcast could be re-detected as live for hours after it finished
- TikTok's rate-limit cooldown is now shared across accounts instead of per account, avoiding wasted requests
- A locked or open output file now reports a clear "file in use" message instead of a server error
- Several cookie/account-pool bugs fixed: fallback to the shared cookie when all accounts are paused, leaked cookie files, stale account state after a pool rebuild
- File names with Vietnamese diacritics, Chinese characters or emoji are now sent to modern devices as-is instead of always being flattened to plain ASCII (BUG-TD-NAME)
- Facebook Live recording added (previously downloaded a short clip instead of the live stream)
- Several smaller fixes: pause/resume state, queue/history sync, Files tab, Live Monitor, and Settings tab
- Archive tab: password-toggle buttons were invisible; "use original name" option now works correctly
- Facebook Story: command-window flash on Windows fully resolved
- Live-recording output file names corrected across platforms
- Fixed a crash on startup in windowed (no console) builds, and a broken "verbose logging" toggle in Settings

### Security

- Document conversion: blocked local-file and network access from untrusted HTML/Markdown content (path confinement, size limit, bounded concurrency)
- File-preview endpoint no longer renders HTML/SVG files inline, preventing stored cross-site scripting

---

## v19.0.0 - 2026-05-31

### Changed

- Desktop UI rewritten in PySide6 (Qt6) with a new visual theme
- TikTok live detection rewritten to run multiple detection strategies concurrently
- Remote App (PWA) UI fully translated into Vietnamese
- Decrypted cookies are now cached in memory to avoid repeated system keychain calls

### Added

- TikTok account pool: use multiple accounts, automatically balanced by load
- Network settings panel: proxy configuration for downloads

### Fixed

- TikTok room ID now passed through the Remote API so fallback strategies work correctly
- Reduced TikTok Live rate-limit errors by pacing requests

---

## v18.0.0 - 2026-04-30

### Added

- Instagram Live recording (Windows/macOS only)
- Remote API access over HTTPS via Tailscale
- TikTok profile live checker (no cookie required)

### Fixed

- TikTok Live falsely reported as not live in several cases (BUG-TT-08/09/10)
- Instagram Live switched to DASH streaming after Instagram changed formats (BUG-IG-01)
- Kuaishou: fixed false re-extraction on temporary network errors and a missing-cookie failure (BUG-KS-01/02)
- Fixed a GPU encoder stall that could repeat after a failed conversion
- Fixed a command-window flash on Windows during Facebook Story downloads

---

## v16.3.1 - 2026-03-22

### Fixed

- Corrected the app version shown in the sidebar and sample config file
- Startup dependency check now includes Playwright

---

## v16.3.0 - 2026-03-21

### Removed

- Video Editor tab (removed to keep the app focused on downloading)
- Threads downloader (removed due to high maintenance cost from frequent API changes)

### Fixed

- Cleaned up leftover code after the removals above

---

## v16.2.1 - 2026-03-21

### Fixed

- Threads downloader: fixed a wrong API endpoint and app ID, and a stale authentication token

---

## v16.2.0 - 2026-03-21

### Added

- Threads downloader (video, image, carousel posts)
- Facebook Story support on macOS
- Resolution picker in the Video Editor (480p to 4K)

### Fixed

- Several Video Editor bugs: a crash, UI freezing during encoder detection, an incorrect blur effect, and a CRF setting bug

---

## v16.1.0 - 2026-03-20

### Added

- Special Downloads tab, starting with Facebook Story

### Changed

- Facebook Story engine rewritten to use the browser's own login session instead of custom connection code

### Fixed

- Duplicate downloads no longer overwrite existing files
- Long downloads now time out safely after 5 minutes instead of hanging
- Post-download buttons no longer stay hidden after a successful download

---

## v16.0.0 - 2026-03-07

### Security

- Fixed a path-traversal vulnerability in cookie file handling
- Removed a shell-injection risk in "reveal in file explorer"
- User data (config, history, logs) moved out of the install directory into a writable per-user location
- Fixed a server-side request forgery (SSRF) risk via thumbnail URLs
- Cookie extraction now validates the browser name against an allowlist
- Custom yt-dlp arguments are now filtered through a strict allowlist
- Updated dependencies, including a security patch for Pillow

### Changed

- Config and history writes are now atomic, reducing the risk of corruption
- History storage switched to an append-only format for reliability
- Live-stream cancel now responds within about 10 seconds instead of up to 90
