English | [Tiếng Việt](CHANGELOG.vi.md) | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

All notable changes to OmniDL are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

No unreleased changes yet.

---

## v20.3.8 - 2026-09-23

### Fixed

- Remote App: when the phone's connection dropped during analysis and reconnected, the same link was analysed a second time from scratch instead of waiting for the running job
- Taildrop: a transfer that failed for a reason other than the filename (for example `502 Bad Gateway`, iPhone unreachable) no longer re-uploads the whole file under an ASCII name

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
