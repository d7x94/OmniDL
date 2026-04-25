"""
infrastructure/downloader/instagram_live_engine.py

Direct Instagram Live recorder using Instagram's internal API + bundled FFmpeg.
Used as the primary engine for Instagram Live URLs — replaces yt-dlp for live
streams because yt-dlp's Instagram extractor fails to resolve live HLS streams.

Flow:
  1. Extract username from URL
  2. Load Instagram cookies (Netscape .txt or DPAPI .enc)
  3. Query Instagram profile API → get broadcast_id
  4. Query Instagram broadcast info API → get HLS playback URL
  5. Launch FFmpeg subprocess → record HLS stream to .ts file
  6. Report progress (downloaded bytes / speed) via on_progress callback

No new dependencies: uses requests (already in requirements.txt) and the
bundled FFmpeg binary via locate_ffmpeg().
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# Instagram internal API — same app-ID and endpoints used by instagram_live_checker.py
_IG_APP_ID       = "936619743392459"
_PROFILE_API     = "https://i.instagram.com/api/v1/users/web_profile_info/"
_BROADCAST_API   = "https://i.instagram.com/api/v1/live/{broadcast_id}/get_info/"
_REQUEST_TIMEOUT = 15  # seconds

# Windows flag: hide the FFmpeg console window in GUI builds.
_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Matches both Instagram live URL formats:
#   /username/live/   (classic format)
#   /live/shortcode/  (2024+ format)
_INSTAGRAM_LIVE_RE = re.compile(
    r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I
)
# Extracts username from the classic /username/live/ format
_USER_LIVE_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]+)/live", re.I
)


def is_instagram_live_url(url: str) -> bool:
    """Return True if *url* is an Instagram Live URL (either format)."""
    return bool(_INSTAGRAM_LIVE_RE.search(url))


class InstagramLiveEngine:
    """
    Records an Instagram Live stream to a .ts file.

    Uses Instagram's internal web API to obtain the HLS stream URL, then
    launches FFmpeg to perform the actual recording.  No yt-dlp involvement.
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    # ── Public interface (mirrors YtDlpEngine.download signature) ────────────

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """
        Record the Instagram Live stream referenced by *task.url*.

        Mutates task.status / progress / filename in-place (same contract as
        YtDlpEngine.download).  Raises RuntimeError on unrecoverable failure.
        """
        # Lazy import — requests is always installed but we defer it to avoid
        # loading the module on every app startup before it is needed.
        import requests  # noqa: PLC0415

        url = task.url

        # ── Cookie resolution (same logic as YtDlpEngine) ───────────────────
        # Import from yt_dlp_engine — both engines live in the same package and
        # share these pure helper functions.
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_path = _resolve_cookie(url, self._config)
        if not cookie_path:
            raise RuntimeError(
                "Instagram Live cần cookie file để xác thực.\n"
                "Cấu hình trong Settings → Network → Cookie file (Instagram)."
            )

        usable_cookie, is_temp = _prepare_cookie_for_use(cookie_path)
        try:
            self._record(task, url, usable_cookie, on_progress, requests)
        finally:
            if is_temp:
                try:
                    Path(usable_cookie).unlink(missing_ok=True)
                except Exception:
                    pass

    # ── Internal implementation ───────────────────────────────────────────────

    def _record(
        self,
        task: DownloadTask,
        url: str,
        cookie_file: str,
        on_progress: Optional[Callable[[DownloadTask], None]],
        requests,  # passed in to avoid re-import
    ) -> None:
        # ── Step 1: load cookies ─────────────────────────────────────────────
        jar = MozillaCookieJar()
        try:
            jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
        except Exception as exc:
            raise RuntimeError(
                f"Không đọc được cookie file Instagram: {exc}"
            ) from exc

        ig_cookies = {c.name: c.value for c in jar if "instagram.com" in c.domain}
        if "sessionid" not in ig_cookies:
            raise RuntimeError(
                "login: Cookie file thiếu sessionid Instagram.\n"
                "Export lại cookie sau khi đăng nhập tại instagram.com."
            )

        csrftoken = ig_cookies.get("csrftoken", "")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "X-IG-App-ID": _IG_APP_ID,
            "X-CSRFToken": csrftoken,
            "X-IG-WWW-Claim": "0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.instagram.com",
        }
        proxies = (
            {"http": self._config.proxy, "https": self._config.proxy}
            if self._config.proxy
            else None
        )

        # ── Step 2: extract username ─────────────────────────────────────────
        username = self._extract_username(url, task)

        # ── Step 3: get broadcast_id + HLS URL from Instagram API ───────────
        broadcast_id, hls_url = self._get_broadcast_info(
            username, headers, jar, proxies, requests
        )

        if not hls_url:
            raise RuntimeError(
                f"@{username} hiện không có live stream nào đang phát.\n"
                "Kiểm tra lại URL hoặc thử lại sau vài giây."
            )

        # ── Step 4: build output path ────────────────────────────────────────
        output_dir = (
            Path(task.output_dir) if task.output_dir else self._config.download_dir
        ).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        rec_ts  = time.strftime("%Y-%m-%d %H-%M")
        short_id = (broadcast_id or "live")[:12]
        raw_name = f"{username} - [LIVE] {rec_ts} [{short_id}].ts"
        # Sanitise characters illegal on NTFS
        for ch in r'<>:"/\|?*':
            raw_name = raw_name.replace(ch, "_")
        output_path = output_dir / raw_name

        with task._lock:
            task.filename = str(output_path)

        # ── Step 5: locate bundled FFmpeg ────────────────────────────────────
        from utils.ffmpeg_locator import locate_ffmpeg  # noqa: PLC0415
        loc = locate_ffmpeg()
        if loc is None:
            raise RuntimeError(
                "Không tìm thấy FFmpeg.\n"
                "Đặt ffmpeg.exe vào thư mục resources/ffmpeg/ hoặc cài FFmpeg lên PATH."
            )
        ffmpeg_bin = str(Path(loc.ffmpeg_bin))

        # ── Step 6: format Cookie header for FFmpeg ──────────────────────────
        # FFmpeg passes -headers to every HTTP request made by the HLS demuxer,
        # so each segment request includes the Instagram session cookie.
        cookie_header_value = "; ".join(
            f"{c.name}={c.value}"
            for c in jar
            if "instagram.com" in c.domain
        )
        headers_arg = (
            f"Cookie: {cookie_header_value}\r\n"
            f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
            f"X-IG-App-ID: {_IG_APP_ID}\r\n"
            f"Referer: https://www.instagram.com/\r\n"
            f"Origin: https://www.instagram.com\r\n"
        )

        # ── Step 7: launch FFmpeg ────────────────────────────────────────────
        # -c copy: stream-copy all tracks without re-encoding (fast, lossless)
        # -f mpegts: MPEG-TS container (same as yt-dlp live recording)
        # shell=False: required by OMNIDL_STABILITY_RULES
        cmd = [
            ffmpeg_bin,
            "-y",
            "-headers", headers_arg,
            "-i", hls_url,
            "-c", "copy",
            "-f", "mpegts",
            str(output_path),
        ]

        logger.info(
            "InstagramLiveEngine: starting FFmpeg | user=@%s | broadcast=%s | out=%s",
            username, broadcast_id, output_path,
        )

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=_WIN_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Không khởi động được FFmpeg ({ffmpeg_bin}).\n"
                "Kiểm tra lại bundle hoặc cài FFmpeg vào PATH."
            ) from exc

        # ── Step 8: poll progress / handle cancel ────────────────────────────
        _prev_size = 0
        _prev_time = time.monotonic()

        try:
            while True:
                if task.is_cancellation_requested:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    # Raise DownloadError so _run_task treats it as cancellation
                    import yt_dlp  # noqa: PLC0415
                    raise yt_dlp.utils.DownloadError("Cancelled by user")

                ret = proc.poll()
                if ret is not None:
                    break  # FFmpeg exited (stream ended or error)

                # Update task progress every 2 s
                now = time.monotonic()
                elapsed = max(now - _prev_time, 0.001)
                cur_size = output_path.stat().st_size if output_path.exists() else 0
                delta    = cur_size - _prev_size
                speed_bs = delta / elapsed if elapsed > 0 else 0

                with task._lock:
                    task.status          = DownloadStatus.DOWNLOADING
                    task.downloaded_bytes = cur_size
                    task.total_bytes      = 0       # unknown for live
                    task.progress         = 50.0    # indeterminate
                    task.speed            = _fmt_speed(speed_bs)
                    task.eta              = ""

                if on_progress:
                    on_progress(task)

                _prev_size = cur_size
                _prev_time = now
                time.sleep(2)

        finally:
            # Best-effort kill if we exit the loop abnormally
            try:
                proc.kill()
            except Exception:
                pass

        if ret != 0:
            stderr_bytes = b""
            if proc.stderr:
                try:
                    stderr_bytes = proc.stderr.read()
                except Exception:
                    pass
            stderr_txt = stderr_bytes.decode("utf-8", errors="replace")
            logger.error(
                "InstagramLiveEngine: FFmpeg exited %d | stderr: %s",
                ret, stderr_txt[-600:],
            )
            raise RuntimeError(
                f"FFmpeg kết thúc với mã lỗi {ret}.\n"
                "Kiểm tra omnidl_run.log để biết chi tiết."
            )

        logger.info(
            "InstagramLiveEngine: recording complete → %s", output_path
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_username(self, url: str, task: "Optional[DownloadTask]" = None) -> str:
        m = _USER_LIVE_RE.search(url)
        if m:
            return m.group(1).lower()
        # /live/shortcode/ format — fallback to uploader stored in task.media_info
        if task is not None and task.media_info:
            uploader = (task.media_info.uploader or "").strip()
            if uploader:
                return uploader.lower().lstrip("@")
        raise RuntimeError(
            "Không thể trích xuất username từ URL Instagram Live.\n"
            "Dùng định dạng: https://www.instagram.com/username/live/"
        )

    def _get_broadcast_info(
        self,
        username: str,
        headers: dict,
        jar: MozillaCookieJar,
        proxies: Optional[dict],
        requests,
    ) -> tuple[str, Optional[str]]:
        """
        Query Instagram's internal API to get (broadcast_id, hls_url).

        Returns ("", None) if the user is not currently live.
        Raises RuntimeError on auth / network failure.
        """
        # ── Phase A: profile API → broadcast_id ─────────────────────────────
        try:
            resp = requests.get(
                _PROFILE_API,
                params={"username": username},
                headers=headers,
                cookies=jar,
                proxies=proxies,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"Lỗi kết nối Instagram API: {exc}") from exc
        except requests.exceptions.Timeout:
            raise RuntimeError("Instagram API hết thời gian chờ. Thử lại sau.") from None
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Lỗi HTTP khi gọi Instagram API: {exc}") from exc

        if resp.status_code == 401:
            raise RuntimeError(
                "login: Cookie Instagram hết hạn hoặc không hợp lệ.\n"
                "Refresh cookie trong Settings → Network."
            )
        if resp.status_code == 403:
            raise RuntimeError("login: Instagram từ chối truy cập (403). Refresh cookie.")
        if resp.status_code == 429:
            raise RuntimeError(
                "blocked: Rate limit Instagram. Chờ 5–10 phút rồi thử lại."
            )
        if not resp.ok:
            raise RuntimeError(
                f"Instagram API trả về HTTP {resp.status_code} cho @{username}."
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise RuntimeError(
                "Instagram trả về phản hồi không hợp lệ (non-JSON)."
            ) from exc

        user = (
            data.get("data", {}).get("user")
            or data.get("user")
            or {}
        )
        if not user:
            logger.debug(
                "InstagramLiveEngine: empty user data for @%s. keys=%s",
                username, list(data.keys()),
            )
            return "", None

        broadcast_id = (
            str(user.get("live_broadcast_id") or "")
            or str((user.get("broadcast") or {}).get("id") or "")
        ).strip()

        if not broadcast_id:
            logger.debug("InstagramLiveEngine: @%s is not live (no broadcast_id)", username)
            return "", None

        # ── Phase B: broadcast info API → HLS URL ───────────────────────────
        broadcast_url = _BROADCAST_API.format(broadcast_id=broadcast_id)
        try:
            resp2 = requests.get(
                broadcast_url,
                headers=headers,
                cookies=jar,
                proxies=proxies,
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Lỗi lấy broadcast info: {exc}") from exc

        if not resp2.ok:
            raise RuntimeError(
                f"Broadcast info API trả về HTTP {resp2.status_code} "
                f"(broadcast_id={broadcast_id})."
            )

        try:
            bdata = resp2.json()
        except ValueError as exc:
            raise RuntimeError("Broadcast info: phản hồi không hợp lệ.") from exc

        broadcast = bdata.get("broadcast") or {}
        # Prefer progressive HLS over DASH for FFmpeg compatibility
        hls_url = (
            broadcast.get("playback_url")
            or broadcast.get("dash_abr_playback_url")
            or broadcast.get("dash_live_master_template_url")
        )

        logger.debug(
            "InstagramLiveEngine: @%s | broadcast_id=%s | hls_url=%s",
            username,
            broadcast_id,
            (hls_url[:80] + "...") if hls_url and len(hls_url) > 80 else hls_url,
        )

        return broadcast_id, hls_url


# ── Formatting helpers (mirrors yt_dlp_engine._fmt_speed) ─────────────────────

def _fmt_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} KB/s"
    return f"{bps:.0f} B/s"
