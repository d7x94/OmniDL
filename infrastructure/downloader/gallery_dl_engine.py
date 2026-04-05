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

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# Suppress console window on Windows for all subprocess calls.
_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Platforms gallery-dl handles better than yt-dlp for image content
_SUPPORTED_RE = re.compile(
    r"instagram\.com|twitter\.com|x\.com|pinterest\.|pixiv\.net|deviantart\.com",
    re.I,
)


def is_gallery_dl_url(url: str) -> bool:
    """True when the URL belongs to a platform gallery-dl supports for images."""
    return bool(_SUPPORTED_RE.search(url))


def _find_executable() -> Optional[str]:
    """
    Locate gallery-dl binary.
    Search order:
      1. System PATH (shutil.which)
      2. Scripts dir of the current Python environment (venv / frozen)
    Returns None if not found — callers raise RuntimeError with install hint.
    """
    found = shutil.which("gallery-dl")
    if found:
        return found
    # Same directory as the running python executable (venv Scripts / bin)
    scripts = Path(sys.executable).parent
    for name in ("gallery-dl", "gallery-dl.exe", "gallery_dl", "gallery_dl.exe"):
        candidate = scripts / name
        if candidate.is_file():
            return str(candidate)
    return None


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
        return (
            "blocked: gallery-dl bị rate limit — Instagram đang chặn tạm thời.\n"
            "Chờ 5–10 phút rồi thử lại."
        )
    if "gallery-dl" in m and ("not found" in m or "no such" in m):
        return (
            "unsupported url: gallery-dl chưa được cài đặt.\n"
            "Chạy: pip install gallery-dl"
        )
    if "private" in m:
        return (
            "private: Nội dung này ở chế độ riêng tư —\n"
            "cần cookie tài khoản có quyền xem."
        )
    return msg[:300] if msg else "gallery-dl thất bại không rõ nguyên nhân."


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
        """Return gallery-dl path or raise RuntimeError with install hint."""
        exe = _find_executable()
        if not exe:
            raise RuntimeError(
                "gallery-dl chưa được cài đặt.\n"
                "Chạy: pip install gallery-dl\n"
                "Sau đó khởi động lại OmniDL."
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
        cmd: list[str] = [self._exe(), "-q"]
        cookie_temp: str | None = None

        # Per-platform cookie resolution — same logic as yt_dlp_engine.
        # Import inline to avoid circular imports at module level.
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
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

        if self._config.proxy:
            cmd += ["--proxy", self._config.proxy]

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
            raise RuntimeError(
                "gallery-dl không tìm thấy.\nCài đặt: pip install gallery-dl"
            ) from None
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
                "gallery-dl không tìm thấy nội dung tại URL này.\n"
                "Kiểm tra URL hoặc thử refresh cookie."
            )

        first = items[0]
        count = len(items)

        # Extract uploader/username — field name varies by platform
        username = (
            first.get("uploader")
            or first.get("username")
            or (first.get("user", {}).get("username", "")
                if isinstance(first.get("user"), dict)
                else str(first.get("user", "")))
            or ""
        )

        # Build a human-readable title
        raw_title = (
            first.get("title")
            or first.get("description", "")[:80].split("\n")[0]
            or ""
        )
        if not raw_title:
            raw_title = f"{count} ảnh" if count > 1 else "Instagram Photo"

        post_id = str(
            first.get("post_id")
            or first.get("shortcode")
            or first.get("id", "")
            or ""
        )

        # Use first image URL as thumbnail preview
        thumbnail = str(first.get("url") or first.get("thumbnail") or "")

        from infrastructure.downloader.yt_dlp_engine import _detect_platform
        platform = _detect_platform(url)

        logger.info(
            "gallery-dl extract_info: %d item(s) | platform=%s | id=%s",
            count, platform, post_id,
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
        output_dir = (
            Path(task.output_dir) if task.output_dir else self._config.download_dir
        ).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        base_cmd, cookie_temp = self._base_cmd(url=task.url)
        cmd = base_cmd + [
            "-d", str(output_dir),
            task.url,
        ]
        logger.info("gallery-dl download: %s → %s", task.url, output_dir)

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
            raise RuntimeError(
                "gallery-dl không tìm thấy.\nCài đặt: pip install gallery-dl"
            ) from None

        task.status = DownloadStatus.DOWNLOADING
        task.progress = 0.0
        task.eta = "⬇ Đang chuẩn bị tải ảnh…"
        if on_progress:
            on_progress(task)

        # Drain stderr in a daemon thread to prevent pipe deadlock
        stderr_lines: list[str] = []

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
                        Path(f).stat().st_size
                        for f in downloaded_files
                        if Path(f).exists()
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
        if proc.returncode != 0 and not downloaded_files:
            err = "\n".join(
                ln for ln in stderr_lines if ln and not ln.startswith("[debug]")
            )
            raise RuntimeError(_friendly_error(err or "gallery-dl exit code non-zero"))

        # Partial success (some files downloaded, process exited non-zero)
        if proc.returncode != 0 and downloaded_files:
            logger.warning(
                "gallery-dl exited with code %d but %d file(s) were downloaded — "
                "treating as partial success",
                proc.returncode, len(downloaded_files),
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
                task.filename = str(
                    last_output_dir if last_output_dir else Path(downloaded_files[0]).parent
                )
        else:
            # Fallback: largest image file in output subtree
            try:
                _img_exts = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"})
                candidates = [
                    f for f in output_dir.rglob("*")
                    if f.suffix.lower() in _img_exts
                    and f.stat().st_size > 1_000
                ]
                if candidates:
                    best = max(candidates, key=lambda f: f.stat().st_mtime)
                    task.filename = str(best.parent if len(candidates) > 1 else best)
            except Exception as scan_err:
                logger.warning("gallery-dl output scan failed: %s", scan_err)
                task.filename = str(output_dir)

        logger.info(
            "gallery-dl complete: %d file(s) → %s",
            len(downloaded_files), task.filename,
        )

        # Always clean up the decrypted temp cookie file after subprocess exits.
        # On early-exit paths (cancel / error raises above) the atexit handler
        # registered by cookie_storage.decrypt_to_tempfile() provides a safety net.
        if cookie_temp:
            try:
                Path(cookie_temp).unlink(missing_ok=True)
                logger.debug(
                    "gallery-dl download: cleaned up temp cookie: %s", cookie_temp
                )
            except Exception:
                pass
