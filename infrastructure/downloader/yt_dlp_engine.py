"""
infrastructure/downloader/yt_dlp_engine.py
Thin wrapper around yt-dlp: metadata extraction + download execution.
"""
from __future__ import annotations

import logging
import re
import shlex
import time
from pathlib import Path
from typing import Any, Callable, Optional

import yt_dlp

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from utils.ffmpeg_locator import get_ffmpeg_path

logger = logging.getLogger(__name__)


def _validate_cookie_path(config: "ConfigManager") -> str | None:
    """Resolve and validate the configured cookie_file path (CWE-22).

    Returns the resolved absolute path string when the file exists and is
    safely contained within the OmniDL data directory.  Returns None in all
    other cases (absent, empty, outside the allowed root, or not a file).

    Security rationale
    ──────────────────
    The allowed root is restricted to *config_path.parent* only (the OmniDL
    data directory).  The previous implementation also allowed Path.home() as
    a valid root, which permitted any file under the user's home directory —
    including ~/.ssh/id_rsa or ~/.gnupg/secring.gpg — to be silently forwarded
    as ``cookiefile`` to yt-dlp, which transmits it to the remote server.

    Path.parents is used instead of str.startswith() to prevent the sibling-
    directory bypass: /home/user_evil/cookies.txt passes a startswith check
    against /home/user but fails the exact-ancestor check via Path.parents.
    """
    cookie_file = config.cookie_file.strip()
    if not cookie_file:
        return None
    cp = Path(cookie_file).resolve()
    # Accept files inside the OmniDL data directory OR anywhere under the
    # user's home directory.  Path.parents is used (not str.startswith) to
    # prevent the sibling-directory bypass (CWE-22).
    safe_roots = (
        config.config_path.parent.resolve(),
    )
    is_safe = any(cp == root or root in cp.parents for root in safe_roots)
    if cp.is_file() and is_safe:
        logger.info("Using cookie file: %s", cp)
        return str(cp)
    logger.warning(
        "cookie_file rejected — not inside a safe directory: %s",
        cookie_file,
    )
    return None

# Map URL patterns to friendly platform names
_PLATFORM_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"youtu\.?be", re.I), "YouTube"),
    (re.compile(r"tiktok\.com", re.I), "TikTok"),
    (re.compile(r"instagram\.com", re.I), "Instagram"),
    (re.compile(r"twitter\.com|x\.com", re.I), "Twitter/X"),
    (re.compile(r"facebook\.com|fb\.watch", re.I), "Facebook"),
    (re.compile(r"twitch\.tv", re.I), "Twitch"),
    (re.compile(r"threads\.net", re.I), "Threads"),
    (re.compile(r"vimeo\.com", re.I), "Vimeo"),
    (re.compile(r"dailymotion\.com", re.I), "Dailymotion"),
]


def _detect_platform(url: str) -> str:
    for pattern, name in _PLATFORM_MAP:
        if pattern.search(url):
            return name
    return "Web"


def _friendly_error(msg: str) -> str:
    msg_l = msg.lower()
    if "private" in msg_l:
        return "Content is private. Try enabling cookies in Settings."
    if "not found" in msg_l or "404" in msg_l:
        return "URL not found or content was removed."
    if "unsupported url" in msg_l:
        return "This platform is not supported by yt-dlp."
    if "not start" in msg_l and "live" in msg_l:
        return "Live stream has not started yet."
    if "ended" in msg_l and "live" in msg_l:
        return "Live stream has ended."
    return msg[:200]


# Known URL patterns that yt-dlp cannot handle, with actionable messages.
# Checked before calling yt-dlp to give a better UX than a generic error.
# Patterns that require cookies — only blocked if no cookie is configured
_NEEDS_COOKIES: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"instagram\.com/stories/", re.I),
        "Instagram Stories require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
    (
        re.compile(r"instagram\.com/[^/]+/live(?:/|$)", re.I),
        "Instagram Live streams require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
    (
        re.compile(r"facebook\.com/live/", re.I),
        "Facebook Live streams require cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
    (
        # Matches /stories/ paths AND ?view_single=1 story viewer URLs
        # e.g. facebook.com/stories/XYZ or story.php?...&view_single=1
        re.compile(r"facebook\.com/(?:stories/|.*[?&]view_single)", re.I),
        "Facebook Stories require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
]


def _check_unsupported_url(url: str, has_cookies: bool = False) -> str | None:
    """Return a user-friendly message if URL is blocked, else None.
    
    has_cookies=True means a cookie file or browser cookies are configured,
    so cookie-required URLs (Stories, Live) are allowed through to yt-dlp.
    """
    if not has_cookies:
        for pattern, message in _NEEDS_COOKIES:
            if pattern.search(url):
                return message
    return None


# DEF-015: module-level constant — avoids re-allocating on every download() call
_MEDIA_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v",
    ".mp3", ".m4a", ".opus", ".aac", ".flac", ".wav",
})


class YtDlpEngine:
    """
    Handles:
    • metadata extraction (extract_info)
    • download execution (download)
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    # ── Metadata extraction ───────────────────────────────────────────────

    def extract_info(self, url: str) -> MediaInfo:
        """
        Fetch video metadata without downloading.
        Raises RuntimeError on failure.
        """
        # Reject known-unsupported URL patterns before calling yt-dlp so the
        # user gets an actionable message rather than a generic yt-dlp error.
        # has_cookies allows Stories and Live URLs through when cookies are
        # configured by the user (intent check).  Security validation of the
        # actual cookie path happens in _validate_cookie_path() below.
        has_cookies = bool(
            self._config.cookie_file.strip() or self._config.use_cookies
        )
        early_msg = _check_unsupported_url(url, has_cookies=has_cookies)
        if early_msg:
            raise RuntimeError(early_msg)

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,   # DEF-007: prevent hang on stalled server
        }
        _ffmpeg_dir = get_ffmpeg_path()
        if _ffmpeg_dir:
            opts["ffmpeg_location"] = _ffmpeg_dir

        if self._config.proxy:
            opts["proxy"] = self._config.proxy
        # Cookie-file validation delegated to _validate_cookie_path() (CWE-22).
        # See the helper's docstring for the security rationale.
        _cookie_path = _validate_cookie_path(self._config)
        if _cookie_path:
            opts["cookiefile"] = _cookie_path
        if not opts.get("cookiefile") and self._config.use_cookies:
            opts["cookiesfrombrowser"] = (self._config.cookies_browser,)

        # Retry up to 2 times on transient errors (rate limit, network blip).
        last_exc: Exception | None = None
        info = None
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                break   # success
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                # Don't retry hard errors (private, removed, unsupported)
                _hard = (
                    "private", "removed", "unsupported url",
                    "not found", "404", "login",
                )
                if any(k in msg.lower() for k in _hard):
                    raise RuntimeError(_friendly_error(msg)) from exc
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)   # 1s, 2s back-off
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if info is None:
            msg = str(last_exc) if last_exc else "No response from server"
            if any(k in msg.lower() for k in ("rate", "429", "too many")):
                platform = _detect_platform(url)
                msg = (f"{platform} rate limit reached. "
                       "Wait 2-3 minutes and try again. "
                       "Tip: enable browser cookies in Settings -> Network.")
            raise RuntimeError(_friendly_error(msg)) from last_exc

        # Handle playlist — take first entry
        if info.get("_type") == "playlist":
            entries = info.get("entries", [])
            if not entries:
                raise RuntimeError("Playlist is empty.")
            info = entries[0]

        return MediaInfo(
            url=url,
            title=info.get("title") or "Unknown",
            uploader=info.get("uploader") or info.get("channel") or "",
            duration=int(info.get("duration") or 0),
            thumbnail=info.get("thumbnail") or "",
            platform=_detect_platform(url),
            formats=info.get("formats") or [],
            is_live=bool(info.get("is_live")),
            was_live=bool(info.get("was_live")),
            # saved so download() needs no 2nd network call
            video_id=info.get("id") or "",
        )

    # ── Download execution ────────────────────────────────────────────────

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """
        Execute the download for *task*.
        Mutates task.status / progress / filename in-place.
        Raises RuntimeError on failure.
        """
        output_dir = (
            Path(task.output_dir) if task.output_dir else self._config.download_dir
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Livestream detection ──────────────────────────────────────────
        # yt-dlp may not always set is_live=True for TikTok live during
        # extract_info (race between go-live and extraction timing).
        # We also check duration==0 + TikTok URL as a strong secondary signal.
        # Same race condition applies to Instagram live streams.
        _tiktok_live_re = re.compile(r"tiktok\.com/@[^/]+/live", re.I)
        _instagram_live_re = re.compile(r"instagram\.com/[^/]+/live(?:/|$)", re.I)
        is_live = bool(
            (task.media_info and task.media_info.is_live)
            or (task.media_info and task.media_info.duration == 0
                and _tiktok_live_re.search(task.url))
            or (task.media_info and task.media_info.duration == 0
                and _instagram_live_re.search(task.url))
        )
        if is_live:
            logger.info(
                "Task %s detected as livestream — using HLS-safe options", task.id
            )

        # ── Filename template (all fixes applied) ────────────────────────
        #
        # VOD template breakdown:
        #   %(uploader,channel|Unknown).50B          uploader capped at 50 bytes
        #   %(upload_date>%Y-%m-%d - ,...|)s         ISO date + " - " separator
        #                                             embedded so separator only
        #                                             appears when a date exists;
        #                                             falls back to empty string
        #   %(title).100B                            title capped at 100 bytes
        #   [%(id).12B]                              first 12 chars of video ID
        #                                            (enough for uniqueness on
        #                                            all platforms; avoids the
        #                                            32+ char Facebook IDs)
        #
        # LIVE template uses a local recording timestamp instead of upload_date
        # (which is unavailable mid-stream) and prefixes [LIVE] for clarity.
        #
        # windowsfilenames=True (set in opts below) replaces all characters
        # illegal on NTFS/FAT32 (:, <, >, ", |, ?, *) so the file can be
        # written on Windows without yt-dlp raising a PermissionError.
        #
        # trim_file_name=180 hard-caps the stem at 180 bytes, keeping the
        # total path safely under the Windows MAX_PATH limit of 260 chars
        # even with a long download directory.

        if is_live:
            rec_ts = time.strftime("%Y-%m-%d %H-%M")
            outtmpl = str(
                output_dir
                / (
                    f"%(uploader,channel|Unknown).50B"
                    f" - [LIVE] {rec_ts}"
                    f" %(title).80B [%(id).12B].%(ext)s"
                )
            )
        else:
            outtmpl = str(
                output_dir
                / (
                    "%(uploader,channel|Unknown).50B"
                    " - %(upload_date>%Y-%m-%d - ,release_date>%Y-%m-%d - |)s"
                    "%(title).100B [%(id).12B].%(ext)s"
                )
            )

        opts: dict[str, Any] = {
            # Livestreams serve a single HLS/DASH mux — yt-dlp cannot split
            # them into separate video+audio tracks.  'best' picks the highest-
            # quality combined stream and skips the ffmpeg merge step entirely.
            "format": "best" if is_live else task.format_id,
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "retries": self._config.max_retries,
            # fragment_retries=0 for live streams so that a DownloadError raised
            # inside the progress hook propagates immediately.  With max_retries,
            # yt-dlp retried each HLS fragment individually, making Stop/Cancel
            # take 90+ seconds on some streams.
            "fragment_retries": 0 if is_live else self._config.max_retries,
            # Exponential backoff between retries (sleep_interval doubles up to
            # max_sleep_interval) prevents hammering CDNs on HTTP 429 / 503.
            "sleep_interval": 2,
            "max_sleep_interval": 30,
            "sleep_interval_requests": 1,
            "concurrent_fragment_downloads": 4,
            "writethumbnail": False,
            "embedthumbnail": False,
            "addmetadata": False if is_live else self._config.embed_metadata,
            "progress_hooks": [self._make_progress_hook(task, on_progress)],
            "postprocessor_hooks": [self._make_pp_hook(task, on_postprocess)],
            # noplaylist must match extract_info() — without it, pasting a
            # playlist URL would show the first video's metadata but silently
            # download the entire playlist.
            "noplaylist": True,
            # socket_timeout prevents a stalled server from holding a worker
            # thread for the yt-dlp default of 30 s per read.  With
            # max_concurrent=3, three simultaneous stalls fully block capacity.
            "socket_timeout": 30,
            # continuedl resumes partial files instead of restarting from zero
            # after a cancel/restart — critical for large (2+ GB) downloads.
            "continuedl": True,
            "overwrites": False,
            # Replace NTFS-illegal characters (:, <, >, ", |, ?, *) so files
            # can always be written on Windows without PermissionError.
            "windowsfilenames": True,
            # Hard-cap the filename stem so total path stays under MAX_PATH.
            # yt-dlp trims from the middle (preserving start + end) so the
            # video ID bracket at the end is never cut off.
            "trim_file_name": 180,
        }

        # ── Bundled FFmpeg ────────────────────────────────────────────────
        # Point yt-dlp at the bundled FFmpeg directory so post-processing
        # (stream merging, thumbnail embedding, metadata) works without
        # requiring the user to install FFmpeg on their machine.
        # get_ffmpeg_path() returns None when no bundle is present (dev
        # mode without resources/ffmpeg/), in which case yt-dlp falls
        # back to searching the system PATH.
        _ffmpeg_dir = get_ffmpeg_path()
        if _ffmpeg_dir:
            opts["ffmpeg_location"] = _ffmpeg_dir

        # HLS livestream options.  hls_use_mpegts writes MPEG-TS segments as
        # they arrive rather than building an MP4 index — without it, TikTok
        # live streams fail mid-download or produce unplayable files.
        # live_from_start=False records from now (not stream start).
        # socket_timeout is tightened for live so the cancel check fires within
        # 10 s rather than 30 s.
        if is_live:
            opts["hls_use_mpegts"] = True
            opts["live_from_start"] = False
            opts["socket_timeout"] = 10   # faster cancel response for live

        # merge_output_format tells yt-dlp to invoke ffmpeg to remux/merge the
        # downloaded streams.  For livestreams the HLS segments are already a
        # single muxed container — adding a merge step causes ffmpeg to crash
        # (Windows exit code 3419392776).  Only set it for non-live downloads.
        if not is_live:
            opts["merge_output_format"] = task.output_ext

        # Proper thumbnail embedding via postprocessors.
        # Skip for livestreams — there is no single output file to embed into
        # while the stream is ongoing; ffmpeg will crash trying.
        if self._config.embed_thumbnail and not is_live:
            opts["writethumbnail"] = True
            opts["postprocessors"] = [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ]

        if self._config.proxy:
            opts["proxy"] = self._config.proxy
        # Cookie-file validation delegated to _validate_cookie_path() (CWE-22).
        # Centralising the check in the helper ensures both extract_info() and
        # download() enforce identical security constraints and cannot diverge.
        _cookie_path = _validate_cookie_path(self._config)
        if _cookie_path:
            opts["cookiefile"] = _cookie_path
        if not opts.get("cookiefile") and self._config.use_cookies:
            opts["cookiesfrombrowser"] = (self._config.cookies_browser,)

        # Extra user-supplied yt-dlp args
        self._apply_extra_args(opts)

        # Capture the final output path from yt-dlp's postprocessor hook rather
        # than predicting it.  Prediction approaches fail because our filename
        # sanitisation differs from yt-dlp's, upload_date / uploader selection
        # is non-deterministic, and a size scan picks the wrong file when
        # thumbnails or multiple streams exist in the output directory.
        # The pp_hook fires with status="finished" after FFmpeg merge completes;
        # at that point info_dict["filepath"] holds the exact final path.

        _final_filepath: list[str] = []   # mutable closure cell

        _hooks = opts.get("postprocessor_hooks") or []
        _original_pp_hook = _hooks[0] if _hooks else None

        def _capturing_pp_hook(d: dict) -> None:
            # Capture filepath after EVERY postprocessor finishes — the last
            # "finished" event is always the final merged output.
            if d.get("status") == "finished":
                _info = d.get("info_dict") or {}
                fp = (
                    _info.get("filepath")
                    or _info.get("__real_download_filename")
                    or ""
                )
                if fp:
                    p = Path(fp)
                    if p.suffix.lower() in _MEDIA_EXTS and not fp.endswith(".part"):
                        _final_filepath.clear()
                        # Resolve to absolute path — on some Windows + PyInstaller
                        # environments yt-dlp may return a relative filepath in
                        # info_dict, which would cause open_folder / history to
                        # target the wrong directory.
                        resolved = str(p.resolve())
                        _final_filepath.append(resolved)
                        # BUG-1 FIX: write task.filename synchronously here so
                        # the Queue "Open" button always opens the correct folder
                        # even if the post-ydl.download() resolution block is
                        # skipped or fails (e.g. pp_hook fires without a
                        # subsequent size-scan fallback succeeding).
                        with task._lock:
                            task.filename = resolved
            # Also run the original pp hook (progress + postprocess callbacks)
            if _original_pp_hook:
                _original_pp_hook(d)

        opts["postprocessor_hooks"] = [_capturing_pp_hook]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([task.url])
        except yt_dlp.utils.DownloadError as exc:
            # Check the task's own cancellation flag rather than parsing the
            # error string — reliable across yt-dlp versions and locales.
            if task.is_cancellation_requested:
                # Remove any partial .part files left by yt-dlp so the download
                # directory does not accumulate stale fragment files.
                try:
                    for f in output_dir.glob("*.part"):
                        f.unlink(missing_ok=True)
                        logger.debug("Cleaned up partial file: %s", f)
                except OSError as cleanup_exc:
                    logger.warning("Part-file cleanup failed: %s", cleanup_exc)
                raise  # let _run_task handle the CANCELLED transition
            raise RuntimeError(_friendly_error(str(exc))) from exc
        except Exception as exc:
            if task.is_cancellation_requested:
                raise yt_dlp.utils.DownloadError("Cancelled by user") from exc
            raise RuntimeError(str(exc)) from exc

        # ── Resolve final filename ─────────────────────────────────────────
        if _final_filepath:
            # Best case: pp_hook told us exactly where the merged file is
            p = Path(_final_filepath[0])
            if p.is_file() and p.stat().st_size > 1_000:
                task.filename = str(p)
                logger.info("Filename from pp_hook: %s", task.filename)
            else:
                task.filename = _final_filepath[0]   # keep path even if verify fails
                logger.warning("pp_hook path not found on disk: %s", task.filename)
        elif task.filename and Path(task.filename).is_file():
            # Progress hook captured it and it still exists (single-format, no merge)
            logger.debug("Keeping progress-hook filename: %s", task.filename)
        else:
            # Last resort: largest VIDEO file in output_dir (never pick thumbnails)
            try:
                candidates = [
                    f for f in output_dir.iterdir()
                    if f.suffix.lower() in _MEDIA_EXTS
                    and not f.name.endswith(".part")
                    and not f.name.endswith(".ytdl")
                    and f.stat().st_size > 50_000      # >50KB — skip thumbnails
                ]
                if candidates:
                    task.filename = str(max(candidates, key=lambda f: f.stat().st_size))
                    logger.info("Filename via size scan: %s", task.filename)
                else:
                    logger.warning("No media file found in %s", output_dir)
            except Exception as e:
                logger.warning("Size scan failed: %s", e)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _make_progress_hook(
        self,
        task: DownloadTask,
        callback: Optional[Callable[[DownloadTask], None]],
    ) -> Callable[[dict], None]:
        def hook(d: dict[str, Any]) -> None:
            # Respect pause / cancel
            task.wait_if_paused()
            if task.is_cancellation_requested:
                raise yt_dlp.utils.DownloadError("Cancelled by user")

            status = d.get("status", "")
            if status == "downloading":
                with task._lock:
                    task.status = DownloadStatus.DOWNLOADING
                    task.downloaded_bytes = d.get("downloaded_bytes") or 0
                    task.total_bytes = (
                        d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    )
                    if task.total_bytes > 0:
                        task.progress = min(
                            99.0, task.downloaded_bytes / task.total_bytes * 100
                        )
                    speed = d.get("speed")
                    if speed:
                        task.speed = _fmt_speed(speed)
                    eta = d.get("eta")
                    if eta is not None:
                        task.eta = _fmt_eta(eta)
                    _fname = d.get("filename")
                    if _fname and Path(_fname).is_absolute():
                        task.filename = _fname
                if callback:
                    callback(task)

            elif status == "finished":
                with task._lock:
                    task.status = DownloadStatus.PROCESSING
                    task.progress = 99.5
                    task.speed = ""
                    task.eta = ""
                    _fname = d.get("filename")
                    if _fname and Path(_fname).is_absolute():
                        task.filename = _fname
                if callback:
                    callback(task)

        return hook

    def _make_pp_hook(
        self,
        task: DownloadTask,
        callback: Optional[Callable[[DownloadTask], None]],
    ) -> Callable[[dict], None]:
        def hook(d: dict[str, Any]) -> None:
            if d.get("status") == "started":
                task.status = DownloadStatus.PROCESSING
                task.eta = "Processing…"
                if callback:
                    callback(task)
            elif d.get("status") == "finished":
                task.eta = ""
                if callback:
                    callback(task)

        return hook

    def _apply_extra_args(self, opts: dict[str, Any]) -> None:
        # Extra args are passed through a strict allowlist (CWE-78: OS Command
        # Injection).  Without filtering, options such as --exec,
        # --exec-before-download, and --postprocessor-args would allow arbitrary
        # command execution from user-supplied config.
        _SAFE_EXTRA_OPTS: frozenset[str] = frozenset({
            "format",
            "subtitleslangs",
            "writesubtitles",
            "writethumbnail",
            "noplaylist",
            "playliststart",
            "playlistend",
            "ratelimit",
            "sleep_interval",
            "max_sleep_interval",
            "geo_bypass",
            "geo_bypass_country",
            "write_all_thumbnails",
            "write_description",
            "write_info_json",
            "age_limit",
            "user_agent",
        })

        raw = self._config.extra_args.strip()
        if not raw:
            return
        try:
            tokens = shlex.split(raw)
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                # Handle both long (--format) and short (-f) flag forms.
                if tok.startswith("--"):
                    key = tok[2:].replace("-", "_")
                elif tok.startswith("-") and len(tok) == 2:
                    key = tok[1:]   # short flag, e.g. -x → "x"
                else:
                    i += 1
                    continue

                if key not in _SAFE_EXTRA_OPTS:
                    logger.warning(
                        "Blocked unsafe extra_arg key '%s' — "
                        "not in allowlist.  "
                        "Remove it from Settings → Extra yt-dlp args.",
                        key,
                    )
                    # consume optional value token so the index advances correctly
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        i += 2
                    else:
                        i += 1
                    continue

                if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    opts[key] = tokens[i + 1]
                    i += 2
                else:
                    opts[key] = True
                    i += 1
        except Exception as exc:
            logger.warning("Failed to parse extra_args: %s", exc)


def _fmt_speed(speed: float) -> str:
    if speed >= 1024 ** 2:
        return f"{speed / 1024 ** 2:.1f} MiB/s"
    if speed >= 1024:
        return f"{speed / 1024:.0f} KiB/s"
    return f"{speed:.0f} B/s"


def _fmt_eta(eta: int | float) -> str:
    s = int(eta)
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
