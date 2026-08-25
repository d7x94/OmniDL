"""
infrastructure/downloader/gallery_dl_engine.py
Thin subprocess wrapper around gallery-dl for image/gallery downloads.

Used as a fallback when yt-dlp finds no video formats — e.g. Instagram
photo posts, Twitter image tweets.  Activated automatically via
source_engine="gallery_dl" on MediaInfo when yt-dlp raises a photo error.

Cookie file:  shares the same file configured in Settings → Network.
Output dir:   same download_dir as yt-dlp downloads.
Progress:     file-count based (gallery-dl has no byte-level hook).
Cancel:       proc.kill() when task.is_cancellation_requested is set.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# Suppress console window on Windows for all subprocess calls.
_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Platforms gallery-dl handles better than yt-dlp for image content.
# BUG-FB-PHOTO FIX: facebook.com added so photo-only posts (yt-dlp reports
# "no video in this post") fall back to gallery-dl instead of failing outright.
# Safe: facebook_story_engine's is_facebook_story_url() check runs BEFORE
# this fallback in download_manager routing, so Story/fb.watch URLs are
# never affected; plain video posts never hit this path (fallback only fires
# on a photo-only error, which video posts don't raise).
_SUPPORTED_RE = re.compile(
    r"instagram\.com|twitter\.com|x\.com|pinterest\.|pixiv\.net|deviantart\.com|facebook\.com",
    re.I,
)


def is_gallery_dl_url(url: str) -> bool:
    """True when the URL belongs to a platform gallery-dl supports for images."""
    return bool(_SUPPORTED_RE.search(url))


def _find_executable() -> Optional[str]:
    """
    Locate gallery-dl binary.
    Search order:
      1. Frozen app: sys._MEIPASS/gallery-dl/ (bundled binary)
      2. python -m gallery_dl — works in uv/venv where the gallery-dl script
         wrapper is a uv trampoline that fails with "canonicalize script path"
         on Windows when the path contains spaces or non-ASCII characters.
      3. System PATH (shutil.which)
      4. Scripts dir of the current Python environment (venv / dev)
    Returns None if not found — callers raise RuntimeError with install hint.
    """
    # 1. PyInstaller frozen bundle — gallery-dl binary lives in _MEIPASS/gallery-dl/
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        for name in ("gallery-dl", "gallery-dl.exe"):
            candidate = meipass / "gallery-dl" / name
            if candidate.is_file():
                return str(candidate)
        # Frozen: do not fall through to python -m (no venv in frozen app)
        return None

    # 2. python -m gallery_dl — preferred in uv/venv dev environments.
    # The gallery-dl script entry point on Windows is a uv trampoline (.exe
    # wrapper) that can fail with "uv trampoline failed to canonicalize script
    # path" when the venv path contains spaces or non-ASCII chars.  Running via
    # the Python interpreter directly bypasses the trampoline entirely.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--version"],
            capture_output=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            # Return a sentinel that _exe() / _base_cmd() recognize as
            # "use python -m gallery_dl" invocation.
            return f"{sys.executable}::gallery_dl_module"
    except Exception:
        pass

    # 3. System PATH
    found = shutil.which("gallery-dl")
    if found:
        return found

    # 4. Scripts dir alongside Python executable (venv Scripts / bin)
    scripts = Path(sys.executable).parent
    for name in ("gallery-dl", "gallery-dl.exe", "gallery_dl", "gallery_dl.exe"):
        candidate = scripts / name
        if candidate.is_file():
            return str(candidate)
    return None


def _exe_cmd(exe: str) -> list[str]:
    """Convert the value returned by _find_executable() to a command prefix.

    When exe is the sentinel "python_path::gallery_dl_module", returns
    [python, "-m", "gallery_dl"].  Otherwise returns [exe].
    """
    if exe.endswith("::gallery_dl_module"):
        python = exe[: -len("::gallery_dl_module")]
        return [python, "-m", "gallery_dl"]
    return [exe]


def _friendly_error(msg: str) -> str:
    """Map gallery-dl stderr messages to user-readable Vietnamese/English text.

    Each message intentionally starts with an English keyword that matches
    DownloadManager._HARD_ERROR_KEYWORDS so hard errors are never retried.
    """
    m = msg.lower()
    if "login" in m or "401" in m or "cookie" in m or "authentication" in m:
        return (
            "login: gallery-dl yêu cầu đăng nhập.\n"
            "Kiểm tra cookie file trong Settings → Network → Cookie file.\n"
            "Đảm bảo dùng cookie Instagram (không phải Facebook)."
        )
    if "404" in m or "not found" in m:
        return "not found: URL không tìm thấy hoặc nội dung đã bị xóa."
    if "429" in m or "rate" in m or "too many" in m:
        return "blocked: gallery-dl bị rate limit — Instagram đang chặn tạm thời.\nChờ 5–10 phút rồi thử lại."
    if "gallery-dl" in m and ("not found" in m or "no such" in m):
        return "unsupported url: gallery-dl chưa được cài đặt.\nChạy: pip install gallery-dl"
    if "private" in m:
        return "private: Nội dung này ở chế độ riêng tư —\ncần cookie tài khoản có quyền xem."
    return msg[:300] if msg else "gallery-dl thất bại không rõ nguyên nhân."


# ── BUG-BW helpers ────────────────────────────────────────────────────────────


def _has_audio(video_path: Path, ffmpeg_dir: Optional[str] = None) -> bool:
    """Return True when *video_path* contains at least one audio stream.

    Uses ffprobe (bundled with FFmpeg) with a fast stream-count query.
    Returns True on any error so that callers do NOT attempt a rescue pass
    when the check itself fails (fail-open is safer than false negatives).
    """
    ffprobe = "ffprobe"
    if ffmpeg_dir:
        candidate = Path(ffmpeg_dir) / "ffprobe"
        if not candidate.is_file():
            candidate = Path(ffmpeg_dir) / "ffprobe.exe"
        if candidate.is_file():
            ffprobe = str(candidate)

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_WIN_NO_WINDOW,
        )
        # ffprobe outputs one "audio" line per audio stream; empty = no audio
        return bool(result.stdout.strip())
    except Exception as exc:
        logger.debug("_has_audio: ffprobe check failed for %s — %s", video_path.name, exc)
        return True  # fail-open: assume audio OK, skip rescue pass


def _ytdlp_carousel_videos(
    url: str,
    output_dir: Path,
    dl_start_ts: float,
    cookie_file: Optional[str] = None,
    proxy: Optional[str] = None,
    ffmpeg_dir: Optional[str] = None,
    max_retries: int = 0,
    rescue_dir: Optional[Path] = None,
) -> list[str]:
    """Download all VIDEO items in an Instagram carousel with proper audio.

    Uses yt-dlp with noplaylist=False + ignoreerrors=True so:
    - All video items are fetched with bestvideo+bestaudio merge (audio OK).
    - Image items silently raise "no video formats" and are skipped.

    Returns a list of newly created video file paths.
    Returns [] on any failure so callers can treat it as a no-op.
    """
    import yt_dlp  # noqa: PLC0415 — lazy import, yt-dlp may not always be present

    # BUG-BT: when rescue_dir is provided (the gallery-dl output subdir), place
    # rescued files directly there — no uploader subdir — so they share the same
    # directory as the gallery-dl images and task.filename already points there.
    if rescue_dir is not None:
        outtmpl = str(rescue_dir / "%(title).60B [%(id).12B].%(ext)s")
    else:
        outtmpl = str(
            output_dir / "%(uploader,channel|instagram_rescue)s" / "%(title).60B [%(id).12B].%(ext)s"
        )
    opts: dict[str, object] = {
        # BUG-BX / BUG-BY: Instagram carousel videos may be:
        #   (a) separate DASH streams (video-only + audio-only) → need mux
        #   (b) combined stream with embedded audio (background music posts)
        # The old "bestvideo[acodec=none]+bestaudio[vcodec=none]" selector
        # only matched case (a). For case (b), acodec=none rejects the combined
        # stream, the fallback bestvideo*+bestaudio* picks the combined stream
        # as "best video" but pairs it with a separate audio stream — FFmpeg
        # then muxes them and the result has duplicated/wrong audio or silence.
        # Fix: prefer separate streams for proper mux, but fall back to the
        # combined stream directly (no forced re-mux) when no separate pair
        # exists. bestvideo+bestaudio covers both: yt-dlp uses separate streams
        # when available, and the combined stream when not.
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        # BUG-BW: expand carousel so all video items are fetched
        "noplaylist": False,
        "socket_timeout": 30,
        "retries": max_retries,
        "windowsfilenames": True,
        "trim_file_name": 180,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if proxy:
        opts["proxy"] = proxy
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    # BUG-BT: when rescue_dir is provided, scan only that directory with a tight
    # window (dl_start_ts, no -5s offset) so gallery-dl's just-written silent
    # files (same dir, slightly older mtime) are excluded.  Without this, both
    # the 5 silent gallery-dl files and 5 new yt-dlp files are returned (10
    # total), and the stale paths persist in task.gallery_dl_files after the
    # silent files are deleted on disk.
    if rescue_dir is not None:
        _scan_root = rescue_dir
        rescue_start = dl_start_ts  # tight — gallery-dl files pre-date this
    else:
        _scan_root = output_dir
        rescue_start = dl_start_ts - 5.0

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        logger.warning("BUG-BW rescue: yt-dlp failed for %s — %s", url, exc)
        return []

    # Collect files created during the rescue pass
    _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
    new_files: list[str] = []
    try:
        for f in _scan_root.rglob("*"):
            if (
                f.is_file()
                and f.suffix.lower() in _vid_exts
                and f.stat().st_mtime >= rescue_start
                and f.stat().st_size > 10_000  # skip tiny/corrupt files
            ):
                new_files.append(str(f))
    except Exception as scan_exc:
        logger.warning("BUG-BW rescue: post-rescue scan failed — %s", scan_exc)

    logger.info(
        "BUG-BW rescue: yt-dlp rescued %d video file(s) with audio from %s",
        len(new_files),
        url,
    )
    return new_files


class GalleryDlEngine:
    """
    Wraps gallery-dl CLI as a subprocess.

    Public interface mirrors YtDlpEngine so DownloadManager can route to
    either engine without needing to know the difference:
        extract_info(url) → MediaInfo
        download(task, on_progress, on_postprocess) → None
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    # ── Internal helpers ──────────────────────────────────────────────────

    def _exe(self) -> str:
        """Return gallery-dl path (or sentinel) or raise RuntimeError with install hint."""
        exe = _find_executable()
        if not exe:
            raise RuntimeError(
                "gallery-dl chưa được cài đặt.\nChạy: pip install gallery-dl\nSau đó khởi động lại OmniDL."
            )
        return exe

    def _base_cmd(self, url: str = "") -> "tuple[list[str], str | None]":
        """Build base command with shared options (cookie, proxy, quiet).

        Returns (cmd, cookie_temp_path).  cookie_temp_path is the path of a
        decrypted plaintext temp file that the caller MUST delete after the
        subprocess exits.  It is None when no temp file was created (no cookie
        configured, or cookie was already a plaintext .txt file).

        Gallery-dl cannot parse Windows DPAPI-encrypted .enc cookie files
        directly — _prepare_cookie_for_use() decrypts them to a temp file
        first, mirroring the approach used by yt_dlp_engine.download().

        url is used for per-platform cookie resolution when provided.
        Callers that know the target URL (download, extract_info) should
        pass it so the correct platform cookie is selected automatically.
        Callers without a URL (e.g. version check) may omit it — the global
        cookie_file fallback is used in that case.
        """
        # -q suppresses info/progress output — gallery-dl does not have
        # --no-progress; that flag is yt-dlp only and causes an arg-parse error.
        cmd: list[str] = _exe_cmd(self._exe()) + ["-q"]
        cookie_temp: str | None = None

        # Per-platform cookie resolution — same logic as yt_dlp_engine.
        # Import inline to avoid circular imports at module level.
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
            platform_for_url,
        )

        cookie_path = _resolve_cookie(url, self._config) if url else None
        # Fallback: if no URL or per-platform cookie not found, use global
        if not cookie_path:
            from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path  # noqa: PLC0415

            cookie_path = _validate_cookie_path(self._config)
        if cookie_path:
            # Decrypt DPAPI-encrypted .enc files to a temp plaintext file so
            # gallery-dl can parse them as Netscape cookies.  gallery-dl cannot
            # read the binary .enc format directly.  When is_temp=True the
            # caller MUST delete the returned temp file after the subprocess
            # exits to avoid plaintext cookie files persisting on disk.
            usable, is_temp = _prepare_cookie_for_use(cookie_path)
            if is_temp:
                cookie_temp = usable
            cmd += ["--cookies", usable]
            logger.debug("gallery-dl using cookie file: %s", usable)
        elif self._config.use_cookies and self._config.cookies_browser:
            # Same precedence as yt_dlp_engine: cookie file wins when set,
            # browser cookies only as the fallback. Previously gallery-dl
            # never saw this setting at all, so users who chose "cookies
            # from browser" (no cookie file) got logged-out gallery-dl runs
            # for Instagram/Twitter/Facebook photo posts.
            cmd += ["--cookies-from-browser", self._config.cookies_browser]
            logger.debug("gallery-dl using cookies from browser: %s", self._config.cookies_browser)

        if self._config.proxy:
            cmd += ["--proxy", self._config.proxy]

        # BUG-IG-ANTIBOT: gallery-dl runs bare otherwise — stock UA, no
        # request pacing. Instagram image/gallery posts get a jittered
        # inter-request delay and a full Chrome header profile.
        # "-o browser=chrome" (not "--user-agent") because gallery-dl's own
        # browser profiles pair the UA with matching sec-ch-ua client hints
        # and TLS cipher order (extractor/common.py HEADERS/CIPHERS) —
        # overriding only the UA string left those other headers on
        # gallery-dl's Firefox default, disagreeing with a Chrome UA.
        if url and platform_for_url(url) == "instagram":
            cmd += ["--sleep-request", "6.0-12.0", "-o", "browser=chrome"]

        return cmd, cookie_temp

    # ── Metadata extraction ───────────────────────────────────────────────

    def extract_info(self, url: str) -> MediaInfo:
        """
        Fetch gallery metadata via gallery-dl --dump-json.
        Returns MediaInfo(source_engine="gallery_dl", formats=[], duration=0).
        Raises RuntimeError on failure.
        """
        base_cmd, cookie_temp = self._base_cmd(url=url)
        cmd = base_cmd + ["--dump-json", "--no-download", url]
        logger.debug("gallery-dl extract_info: %s", cmd)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
                creationflags=_WIN_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("gallery-dl hết thời gian khi lấy thông tin URL.") from None
        except FileNotFoundError:
            raise RuntimeError("gallery-dl không tìm thấy.\nCài đặt: pip install gallery-dl") from None
        finally:
            # Always clean up decrypted temp cookie file, even on error.
            if cookie_temp:
                try:
                    Path(cookie_temp).unlink(missing_ok=True)
                    logger.debug(
                        "gallery-dl extract_info: cleaned up temp cookie: %s",
                        cookie_temp,
                    )
                except Exception:
                    pass

        # gallery-dl --dump-json emits one JSON array per line:
        # [1, "url", {metadata}]  → type 1 = image URL
        # [0, {...}]              → type 0 = message / count info
        items: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("["):
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, list) and len(obj) >= 3 and obj[0] == 1:
                    items.append(obj[2])
            except json.JSONDecodeError:
                continue

        if not items:
            stderr = result.stderr.strip()
            if result.returncode != 0 or stderr:
                raise RuntimeError(_friendly_error(stderr or "No items found"))
            raise RuntimeError(
                "gallery-dl không tìm thấy nội dung tại URL này.\nKiểm tra URL hoặc thử refresh cookie."
            )

        first = items[0]
        count = len(items)

        # Extract uploader/username — field name varies by platform
        username = (
            first.get("uploader")
            or first.get("username")
            or (
                first.get("user", {}).get("username", "")
                if isinstance(first.get("user"), dict)
                else str(first.get("user", ""))
            )
            or ""
        )

        # Build a human-readable title
        raw_title = first.get("title") or first.get("description", "")[:80].split("\n")[0] or ""
        if not raw_title:
            raw_title = f"{count} ảnh" if count > 1 else "Instagram Photo"

        post_id = str(first.get("post_id") or first.get("shortcode") or first.get("id", "") or "")

        # Use first image URL as thumbnail preview
        thumbnail = str(first.get("url") or first.get("thumbnail") or "")

        from infrastructure.downloader.yt_dlp_engine import _detect_platform

        platform = _detect_platform(url)

        logger.info(
            "gallery-dl extract_info: %d item(s) | platform=%s | id=%s",
            count,
            platform,
            post_id,
        )

        return MediaInfo(
            url=url,
            title=raw_title,
            uploader=str(username),
            duration=0,
            thumbnail=thumbnail,
            platform=platform,
            formats=[],
            is_live=False,
            was_live=False,
            video_id=post_id,
            source_engine="gallery_dl",
        )

    # ── Download execution ────────────────────────────────────────────────

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """
        Execute gallery-dl download for *task*.
        Updates task.status / progress / eta / filename in-place.
        Raises RuntimeError on failure.
        """
        output_dir = (Path(task.output_dir) if task.output_dir else self._config.download_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # BUG-BT / Issue-3: per-post output isolation for Instagram posts/reels.
        # Without this, all posts from the same account share one directory, so
        # successive downloads accumulate in the same folder and Taildrop can't
        # distinguish which files belong to which post.
        # Folder name: {username}_{YYYYMMDD}_{shortcode[:8]}
        _insta_post_re = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.I)
        _sc_m = _insta_post_re.search(task.url)
        # BUG-BX: track whether we isolated output to a per-post slug folder.
        # When True, the fallback scan can include ALL files in output_dir
        # (no mtime filter needed) because the folder is post-specific.
        _is_post_isolated = bool(_sc_m)
        if _sc_m:
            _shortcode = _sc_m.group(1)[:8]
            _upl = ""
            if task.media_info and task.media_info.uploader:
                _upl = re.sub(r"[^\w.]", "_", task.media_info.uploader)[:32].strip("_")
            _date_str = datetime.date.today().strftime("%Y%m%d")
            _slug = f"{_upl}_{_date_str}_{_shortcode}" if _upl else f"instagram_{_date_str}_{_shortcode}"
            output_dir = (output_dir / _slug).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)

        # ── Instagram carousel: images-only strategy ─────────────────────
        # gallery-dl fetches Instagram video CDN URLs which are video-only
        # DASH streams (no audio).  For carousel posts (/p/ URLs), tell
        # gallery-dl to skip video files entirely — yt-dlp will download
        # them afterwards with proper bestvideo+bestaudio merge.
        _is_ig_carousel = bool(_sc_m) and bool(re.search(r"instagram\.com/p/", task.url, re.I))

        base_cmd, cookie_temp = self._base_cmd(url=task.url)
        if _is_ig_carousel:
            cmd = base_cmd + [
                "--filter",
                "extension in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'avif', 'heic')",
                "-d",
                str(output_dir),
                "--directory",
                ".",
                task.url,
            ]
        else:
            cmd = base_cmd + [
                "-d",
                str(output_dir),
                "--directory",
                ".",
                task.url,
            ]
        logger.info("gallery-dl download: %s → %s", task.url, output_dir)

        # BUG-BV: capture wall-clock time before the subprocess starts so the
        # fallback scan can scope results to files created in THIS session only.
        # We subtract a 5-second buffer to absorb filesystem timestamp rounding
        # and NAS/network-drive clock skew.
        _dl_start_ts: float = time.time() - 5.0

        # ── Subprocess ────────────────────────────────────────────────────
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_WIN_NO_WINDOW,
            )
        except FileNotFoundError:
            # Clean up temp cookie before propagating so plaintext files
            # never linger on disk when the binary is missing.
            if cookie_temp:
                try:
                    Path(cookie_temp).unlink(missing_ok=True)
                except Exception:
                    pass
            raise RuntimeError("gallery-dl không tìm thấy.\nCài đặt: pip install gallery-dl") from None

        task.status = DownloadStatus.DOWNLOADING
        task.progress = 0.0
        task.eta = "⬇ Đang chuẩn bị tải ảnh…"
        if on_progress:
            on_progress(task)

        # Drain stderr in a daemon thread to prevent pipe deadlock
        stderr_lines: deque[str] = deque(maxlen=200)

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip()
                stderr_lines.append(line)
                if line:
                    logger.debug("gallery-dl: %s", line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        # ── Progress tracking via stdout ──────────────────────────────────
        # gallery-dl writes a line to stdout for each completed file.
        # The line is either the file path directly, or "#: /path/file.jpg".
        downloaded_files: list[str] = []
        last_output_dir: Optional[Path] = None

        assert proc.stdout is not None
        for raw_line in proc.stdout:
            if task.is_cancellation_requested:
                proc.kill()
                break

            line = raw_line.rstrip()
            if not line:
                continue

            # File completion line: starts with "#:" or is an absolute/relative path
            filepath_str = ""
            if line.startswith("#:"):
                filepath_str = line[2:].strip()
            elif not line.startswith("["):
                # Plain path — not a log message
                filepath_str = line.strip()

            if filepath_str:
                fp = Path(filepath_str)
                if fp.exists() and fp.is_file():
                    downloaded_files.append(str(fp))
                    last_output_dir = fp.parent
                    task.downloaded_bytes = sum(
                        Path(f).stat().st_size for f in downloaded_files if Path(f).exists()
                    )
                    n = len(downloaded_files)
                    task.eta = f"⬇ {n} file{'s' if n > 1 else ''} đã tải"
                    task.speed = ""
                    # Indeterminate — show pulse at 50% while files arrive
                    task.progress = min(50.0 + n * 5, 95.0)
                    if on_progress:
                        on_progress(task)
                    logger.debug("gallery-dl downloaded: %s", fp.name)

        proc.wait()
        stderr_thread.join(timeout=5.0)

        # ── Cancel handling ───────────────────────────────────────────────
        if task.is_cancellation_requested:
            from yt_dlp.utils import DownloadError

            raise DownloadError("Cancelled by user")

        # ── Error handling ────────────────────────────────────────────────
        # For Instagram carousels with --filter, gallery-dl may exit non-zero
        # when all items are videos (filter excludes everything).  That is OK
        # — yt-dlp will handle the videos below.
        if proc.returncode != 0 and not downloaded_files and not _is_ig_carousel:
            err = "\n".join(ln for ln in stderr_lines if ln and not ln.startswith("[debug]"))
            raise RuntimeError(_friendly_error(err or "gallery-dl exit code non-zero"))

        # Partial success (some files downloaded, process exited non-zero)
        if proc.returncode != 0 and downloaded_files:
            logger.warning(
                "gallery-dl exited with code %d but %d file(s) were downloaded — treating as partial success",
                proc.returncode,
                len(downloaded_files),
            )

        # ── Resolve output path ───────────────────────────────────────────
        # For multi-file downloads, point to the directory so "Open Folder" works.
        # Record the specific files downloaded in this task so TaildropService
        # can zip only them instead of the entire account directory.
        if downloaded_files:
            task.gallery_dl_files = downloaded_files[:]
            if len(downloaded_files) == 1:
                task.filename = downloaded_files[0]
            else:
                # Point to the deepest directory gallery-dl created
                task.filename = str(last_output_dir if last_output_dir else Path(downloaded_files[0]).parent)
        else:
            # BUG-BV FIX: gallery-dl with -q suppresses stdout so downloaded_files
            # is always empty.  Scan the output subtree for ALL media types (images
            # AND videos) written after the download started.  Scoping by mtime
            # prevents picking up files from previous downloads of the same account.
            # Set task.gallery_dl_files so TaildropService zips only this session's
            # files rather than the accumulated account directory.
            try:
                _media_exts = frozenset(
                    {
                        # images
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".gif",
                        ".webp",
                        ".avif",
                        # videos — BUG-BV: previously missing, causing carousel videos
                        # to be excluded from gallery_dl_files and Taildrop sends
                        ".mp4",
                        ".mov",
                        ".webm",
                        ".mkv",
                        ".m4v",
                    }
                )
                candidates = [
                    f
                    for f in output_dir.rglob("*")
                    if f.is_file()
                    and f.suffix.lower() in _media_exts
                    and f.stat().st_size > 1_000
                    # BUG-BX: skip mtime filter when output_dir is a per-post
                    # slug folder (Instagram /p/ URLs).  gallery-dl may set file
                    # mtime from post metadata (e.g. user has mtime:true in their
                    # gallery-dl config), causing images to have historical
                    # timestamps that fail the >= _dl_start_ts check.  Since the
                    # slug folder is post-specific, all files here belong to this
                    # post regardless of their mtime.  For non-isolated dirs
                    # (other platforms), keep the mtime filter to avoid including
                    # files from previous downloads of the same account directory.
                    and (_is_post_isolated or f.stat().st_mtime >= _dl_start_ts)
                ]
                if candidates:
                    # Separate images and videos for logging; both go into gallery_dl_files
                    _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
                    n_imgs = sum(1 for f in candidates if f.suffix.lower() not in _vid_exts)
                    n_vids = sum(1 for f in candidates if f.suffix.lower() in _vid_exts)
                    logger.info(
                        "gallery-dl fallback scan: found %d image(s) + %d video(s) "
                        "in output subtree (mtime ≥ session start)",
                        n_imgs,
                        n_vids,
                    )
                    task.gallery_dl_files = [str(f) for f in sorted(candidates)]
                    if len(candidates) == 1:
                        task.filename = str(candidates[0])
                    else:
                        # Point to the common parent directory
                        _parent = max(candidates, key=lambda f: f.stat().st_mtime).parent
                        task.filename = str(_parent)
                else:
                    logger.warning(
                        "gallery-dl fallback scan: no new media files found in %s (mtime ≥ session start)",
                        output_dir,
                    )
            except Exception as scan_err:
                logger.warning("gallery-dl output scan failed: %s", scan_err)
                task.filename = str(output_dir)

        _gdl_files_attr = getattr(task, "gallery_dl_files", None)
        _total_files = len(_gdl_files_attr) if _gdl_files_attr else len(downloaded_files)
        logger.info(
            "gallery-dl complete: %d file(s) → %s",
            _total_files,
            task.filename,
        )

        # ── Instagram carousel: yt-dlp primary video download ────────────────
        # gallery-dl fetches Instagram video CDN URLs as video-only DASH
        # streams (no audio).  The --filter above tells gallery-dl to skip
        # videos, so we download them here with yt-dlp which performs a
        # proper bestvideo+bestaudio merge via FFmpeg.
        #
        # BUG-IG-MIX: the rescue must run for EVERY /p/ carousel.  A previous
        # version skipped it when all gallery-dl files were images, reasoning
        # that such a post contains no video.  That check can never be false:
        # the --filter above forbids gallery-dl from writing a video file, so
        # a mixed photo+video post looks exactly like a photo-only post and
        # its videos were silently dropped.  A photo-only post costs one extra
        # yt-dlp extraction here (ignoreerrors=True → returns []).
        _current_gdl_files: list[str] = getattr(task, "gallery_dl_files", None) or []

        _vid_exts_set = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})

        if _is_ig_carousel and not task.is_cancellation_requested:
            from utils.ffmpeg_locator import get_ffmpeg_path  # noqa: PLC0415

            _ffmpeg_dir = get_ffmpeg_path()

            # Delete any silent gallery-dl videos that slipped through the
            # filter (e.g. gallery-dl version without --filter support).
            _gdl_videos = [
                Path(f)
                for f in _current_gdl_files
                if Path(f).suffix.lower() in _vid_exts_set and Path(f).is_file()
            ]
            if _gdl_videos:
                for sv in _gdl_videos:
                    try:
                        sv.unlink(missing_ok=True)
                        logger.debug("Deleted gallery-dl silent video: %s", sv.name)
                    except Exception:
                        pass
                _gdl_video_paths = {str(v) for v in _gdl_videos}
                _current_gdl_files = [f for f in _current_gdl_files if f not in _gdl_video_paths]

            task.eta = "⬇ Đang tải video có âm thanh…"
            if on_progress:
                on_progress(task)

            # Determine output directory for yt-dlp videos — same folder as
            # gallery-dl images so Taildrop zips everything together.
            # BUG-IG-MIX: a video-only post gives gallery-dl nothing to write,
            # so there is no image to borrow the folder from.  Fall back to the
            # per-post slug folder instead of None — None makes yt-dlp create a
            # nested "<uploader>/" subdir and splits the post across two dirs.
            _image_files = [Path(f) for f in _current_gdl_files if Path(f).is_file()]
            _video_out_dir: Path | None = (
                _image_files[0].parent if _image_files else (output_dir if _is_post_isolated else None)
            )

            # Prepare cookie — reuse the decrypted temp if still alive.
            _vid_cookie: str | None = None
            _vid_cookie_is_temp: bool = False
            if cookie_temp:
                _vid_cookie = cookie_temp
            else:
                try:
                    from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415,E501
                        _prepare_cookie_for_use,
                        _resolve_cookie,
                    )

                    _rcp = _resolve_cookie(task.url, self._config)
                    if _rcp:
                        _vid_cookie, _vid_cookie_is_temp = _prepare_cookie_for_use(_rcp)
                except Exception:
                    pass

            _vid_dl_start = time.time()
            try:
                video_files = _ytdlp_carousel_videos(
                    url=task.url,
                    output_dir=output_dir,
                    dl_start_ts=_vid_dl_start,
                    cookie_file=_vid_cookie,
                    proxy=self._config.proxy or None,
                    ffmpeg_dir=_ffmpeg_dir,
                    max_retries=0,
                    rescue_dir=_video_out_dir,
                )
            finally:
                if _vid_cookie_is_temp and _vid_cookie:
                    try:
                        Path(_vid_cookie).unlink(missing_ok=True)
                    except Exception:
                        pass

            if video_files:
                task.gallery_dl_files = _current_gdl_files + video_files
                if len(task.gallery_dl_files) > 1:
                    _parent2 = Path(task.gallery_dl_files[-1]).parent
                    task.filename = str(_parent2)
                elif task.gallery_dl_files:
                    task.filename = task.gallery_dl_files[0]
                logger.info(
                    "Instagram carousel: %d image(s) + %d video(s) with audio",
                    len(_current_gdl_files),
                    len(video_files),
                )
            else:
                logger.info(
                    "Instagram carousel: no video items rescued for %s — "
                    "post is image-only, or the video download failed",
                    task.url,
                )

        # Always clean up the decrypted temp cookie file after subprocess exits.
        # On early-exit paths (cancel / error raises above) the atexit handler
        # registered by cookie_storage.decrypt_to_tempfile() provides a safety net.
        if cookie_temp:
            try:
                Path(cookie_temp).unlink(missing_ok=True)
                logger.debug("gallery-dl download: cleaned up temp cookie: %s", cookie_temp)
            except Exception:
                pass
