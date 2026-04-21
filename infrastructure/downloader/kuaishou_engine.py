"""
infrastructure/downloader/kuaishou_engine.py
============================================
Kuaishou downloader — bypasses yt-dlp entirely.

Root cause of previous failure
-------------------------------
Kuaishou GraphQL (/graphql visionVideoDetail) returns photo=null when called
without a valid `did` (device-ID) cookie in the session. The engine was calling
the API cookie-less, so every response came back with photo=null.

Extraction strategy (tried in order)
--------------------------------------
A. HTML page scrape — GET kuaishou.com/short-video/<id> → parse __NEXT_DATA__
   JSON embedded by the Next.js SSR renderer. Works for public videos with zero
   auth. This is the primary strategy.

B. GraphQL with cookie — POST /graphql with the user's Kuaishou cookie file
   (Netscape .txt or DPAPI .enc). Used when strategy A fails (e.g. private videos).

C. kwai.com API — POST to the international Kuaishou feed API. Alternative
   endpoint structure that sometimes works when www.kuaishou.com is rate-limited.

Short-URL resolution
--------------------
v.kuaishou.com/* short-links are resolved via HTTP redirect following.
Timeout increased to 30s (was 20s) to handle slow CN redirect chains.

URL patterns supported
----------------------
  v.kuaishou.com/<code>
  www.kuaishou.com/short-video/<id>
  www.kuaishou.com/f/<code>
  m.kuaishou.com/short-video/<id>
  www.kuaishou.com/video/<id>

Dependencies
------------
- requests (already in requirements.txt)
- curl_cffi (optional — Chrome TLS impersonation; strongly recommended)

Security
--------
- shell=False on all subprocess calls
- No user data logged (CDN URLs truncated to 80 chars)
- Cookie file validated via CWE-22 guard in yt_dlp_engine._resolve_cookie
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── URL patterns ──────────────────────────────────────────────────────────────

_KUAISHOU_RE = re.compile(
    r"(?:"
    r"v\.kuaishou\.com/"
    r"|(?:www|m)\.kuaishou\.com/(?:short-video|f|video)/"
    r")",
    re.I,
)

_PHOTO_ID_RE = re.compile(
    r"kuaishou\.com/(?:short-video|video)/([A-Za-z0-9_-]+)",
    re.I,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_RESOLVE_TIMEOUT = 30   # seconds — increased from 20 to handle slow CN redirects
_API_TIMEOUT     = 25   # seconds — GraphQL / page fetch
_DL_TIMEOUT      = 30   # seconds — CDN connect timeout

_GQL_URL = "https://www.kuaishou.com/graphql"

_GQL_QUERY = (
    "query visionVideoDetail($photoId: String, $type: Int) {"
    "  visionVideoDetail(photoId: $photoId, type: $type) {"
    "    photo { id caption duration coverUrl"
    "      mainMvUrls { url }"
    "      videoResource { h264 { adaptationSet { representation { url avgBitrate } } } }"
    "    }"
    "    author { name }"
    "    status"
    "  }"
    "}"
)

_KWAI_FEED_URL = "https://www.kwai.com/rest/infra/wd/photo/query_photo_info"

_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.kuaishou.com/",
}

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://www.kuaishou.com",
    "Referer": "https://www.kuaishou.com/",
}

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.S,
)

# ── Public helpers ────────────────────────────────────────────────────────────


def is_kuaishou_url(url: str) -> bool:
    return bool(_KUAISHOU_RE.search(url))


# ── Session factory ───────────────────────────────────────────────────────────


def _make_session(cookie_str: str = ""):
    try:
        from curl_cffi import requests as cffi_req  # noqa: PLC0415
        session = cffi_req.Session(impersonate="chrome")
        if cookie_str:
            for part in cookie_str.split(";"):
                part = part.strip()
                if "=" in part:
                    k, _, v = part.partition("=")
                    session.cookies.set(k.strip(), v.strip(), domain=".kuaishou.com")
        logger.debug("Kuaishou: curl_cffi session (Chrome impersonation)")
        return session
    except ImportError:
        pass

    import requests as req  # noqa: PLC0415
    session = req.Session()
    if cookie_str:
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, _, v = part.partition("=")
                session.cookies.set(k.strip(), v.strip(), domain=".kuaishou.com")
    logger.debug("Kuaishou: requests session (no TLS impersonation)")
    return session


# ── Short-URL resolution ──────────────────────────────────────────────────────


def _resolve_short_url(url: str, session) -> str:
    if "v.kuaishou.com" not in url.lower():
        return url

    for method in ("HEAD", "GET"):
        try:
            fn = session.head if method == "HEAD" else session.get
            kwargs: dict = {"allow_redirects": True, "timeout": _RESOLVE_TIMEOUT}
            if method == "GET":
                kwargs["headers"] = {**_PAGE_HEADERS, "Accept": "text/html,*/*"}
            resp = fn(url, **kwargs)
            final = str(resp.url)
            if final and final != url and "kuaishou.com" in final:
                logger.debug(
                    "Kuaishou: short URL resolved (%s): %s -> %s",
                    method, url, final[:80],
                )
                return final
        except Exception as exc:
            logger.debug("Kuaishou: short URL resolve %s failed: %s", method, exc)

    logger.warning(
        "Kuaishou: could not resolve short URL %s — "
        "photo_id will be wrong (short code, not real ID)",
        url,
    )
    return url


def _extract_photo_id(url: str) -> Optional[str]:
    m = _PHOTO_ID_RE.search(url)
    if m:
        return m.group(1)
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return parts[-1] if parts else None


# ── Strategy A: HTML __NEXT_DATA__ scrape ────────────────────────────────────


def _strategy_html(session, photo_id: str) -> tuple | None:
    page_url = f"https://www.kuaishou.com/short-video/{photo_id}"
    try:
        resp = session.get(page_url, headers=_PAGE_HEADERS, timeout=_API_TIMEOUT)
    except Exception as exc:
        logger.debug("Kuaishou strategy A: GET failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug("Kuaishou strategy A: HTTP %d", resp.status_code)
        return None

    html = resp.text
    m = _NEXT_DATA_RE.search(html)
    if m:
        try:
            nd = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Kuaishou strategy A: __NEXT_DATA__ JSON parse failed: %s", exc)
            nd = {}

        pp = nd.get("props", {}).get("pageProps", {})
        candidates = [
            pp.get("initialState", {}).get("singlePhotoPage", {}).get("photo"),
            pp.get("photoDetail", {}).get("photo"),
            pp.get("photo"),
        ]
        photo = next((c for c in candidates if c), None)
        if photo:
            author = (
                pp.get("initialState", {}).get("singlePhotoPage", {}).get("author") or {}
            )
            return photo, author

        logger.debug(
            "Kuaishou strategy A: photo not in __NEXT_DATA__; pageProps keys: %s",
            list(pp.keys())[:10],
        )

    # Fallback: window.__INITIAL_STATE__
    m2 = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*(?:</script>|window\.)",
        html, re.S,
    )
    if m2:
        try:
            state = json.loads(m2.group(1))
            photo = (
                state.get("singlePhotoPage", {}).get("photo")
                or state.get("photoDetail", {}).get("photo")
            )
            if photo:
                author = state.get("singlePhotoPage", {}).get("author") or {}
                return photo, author
        except (json.JSONDecodeError, ValueError):
            pass

    logger.debug("Kuaishou strategy A: no usable data found in page HTML")
    return None


# ── Strategy B: GraphQL with cookie ──────────────────────────────────────────


def _strategy_gql(session, photo_id: str) -> tuple | None:
    payload = {
        "operationName": "visionVideoDetail",
        "variables": {"photoId": photo_id, "type": 0},
        "query": _GQL_QUERY,
    }
    try:
        resp = session.post(
            _GQL_URL, json=payload, headers=_API_HEADERS, timeout=_API_TIMEOUT
        )
    except Exception as exc:
        logger.debug("Kuaishou strategy B: POST failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug("Kuaishou strategy B: HTTP %d", resp.status_code)
        return None

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None

    vvd = data.get("data", {}).get("visionVideoDetail") or {}
    photo = vvd.get("photo")
    if not photo:
        logger.debug(
            "Kuaishou strategy B: photo=null (session missing valid did cookie)"
        )
        return None

    return photo, vvd.get("author") or {}


# ── Strategy C: kwai.com international API ────────────────────────────────────


def _strategy_kwai(session, photo_id: str) -> tuple | None:
    payload = {"photoId": photo_id, "pageSource": "PROFILE"}
    kwai_headers = {
        **_API_HEADERS,
        "Origin": "https://www.kwai.com",
        "Referer": f"https://www.kwai.com/short-video/{photo_id}",
    }
    try:
        resp = session.post(
            _KWAI_FEED_URL, json=payload, headers=kwai_headers, timeout=_API_TIMEOUT
        )
    except Exception as exc:
        logger.debug("Kuaishou strategy C: POST failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug("Kuaishou strategy C: HTTP %d", resp.status_code)
        return None

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None

    photo = data.get("photo") or data.get("data", {}).get("photo")
    if not photo:
        logger.debug("Kuaishou strategy C: photo not in response")
        return None

    author = data.get("author") or data.get("data", {}).get("author") or {}
    return photo, author


# ── Video URL extraction ──────────────────────────────────────────────────────


def _pick_best_video_url(photo: dict) -> Optional[str]:
    for entry in (photo.get("mainMvUrls") or []):
        url = (entry.get("url") or "").strip()
        if url.startswith("http"):
            logger.debug("Kuaishou: mainMvUrls CDN: %s", url[:80])
            return url

    try:
        best_url: Optional[str] = None
        best_bitrate = -1
        h264 = photo["videoResource"]["h264"]
        for adaptation in (h264.get("adaptationSet") or []):
            for rep in (adaptation.get("representation") or []):
                bitrate = rep.get("avgBitrate") or 0
                url = (rep.get("url") or "").strip()
                if url.startswith("http") and bitrate > best_bitrate:
                    best_bitrate = bitrate
                    best_url = url
        if best_url:
            logger.debug(
                "Kuaishou: h264 representation (bitrate=%d): %s",
                best_bitrate, best_url[:80],
            )
            return best_url
    except (KeyError, TypeError):
        pass

    return None


# ── Cookie loader ─────────────────────────────────────────────────────────────


def _load_cookie_str(config: ConfigManager) -> str:
    try:
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )
        from http.cookiejar import MozillaCookieJar  # noqa: PLC0415

        cookie_path = _resolve_cookie("https://www.kuaishou.com/", config)
        if not cookie_path:
            return ""

        usable, is_temp = _prepare_cookie_for_use(cookie_path)
        jar = MozillaCookieJar()
        try:
            jar.load(usable, ignore_discard=True, ignore_expires=True)
        finally:
            if is_temp:
                Path(usable).unlink(missing_ok=True)

        parts = [
            f"{c.name}={c.value}"
            for c in jar
            if "kuaishou" in (c.domain or "") or "kwai" in (c.domain or "")
        ]
        return "; ".join(parts)
    except Exception as exc:
        logger.debug("Kuaishou: cookie load failed (non-fatal): %s", exc)
        return ""


# ── Public extraction entry point ─────────────────────────────────────────────


def extract_info_kuaishou(url: str, config: Optional[ConfigManager] = None) -> MediaInfo:
    """Extract Kuaishou video metadata. Raises RuntimeError on failure.

    Tries three strategies in order:
      A. HTML __NEXT_DATA__ scrape (no auth required)
      B. GraphQL with user cookie (requires cookie file configured)
      C. kwai.com international API
    """
    cookie_str = _load_cookie_str(config) if config else ""
    session = _make_session(cookie_str)

    try:
        resolved = _resolve_short_url(url, session)
        photo_id = _extract_photo_id(resolved)
        if not photo_id:
            raise RuntimeError(
                f"Không tách được photo_id từ URL: {resolved}\n"
                "Kiểm tra lại URL Kuaishou."
            )
        logger.debug("Kuaishou: photo_id=%s (resolved from %s)", photo_id, url)

        result: tuple | None = None

        logger.debug("Kuaishou: trying strategy A (HTML __NEXT_DATA__)")
        result = _strategy_html(session, photo_id)

        if result is None and cookie_str:
            logger.debug("Kuaishou: trying strategy B (GraphQL + cookie)")
            result = _strategy_gql(session, photo_id)

        if result is None:
            logger.debug("Kuaishou: trying strategy C (kwai.com API)")
            result = _strategy_kwai(session, photo_id)

        if result is None:
            raise RuntimeError(
                "Kuaishou: tất cả phương thức trích xuất đều thất bại.\n\n"
                "Nguyên nhân có thể:\n"
                "• Video đã bị xóa hoặc là private\n"
                "• Kuaishou chặn request từ IP hiện tại\n"
                "• Cấu trúc trang Kuaishou đã thay đổi\n\n"
                "Thử cấu hình cookie Kuaishou trong Settings → Network để "
                "kích hoạt thêm phương thức trích xuất."
            )

        photo, author = result

        title     = (photo.get("caption") or "").strip() or f"kuaishou_{photo_id}"
        uploader  = (author.get("name") or "").strip()
        duration  = int(photo.get("duration") or 0) // 1000  # ms -> s
        thumbnail = photo.get("coverUrl") or ""

        video_url = _pick_best_video_url(photo)
        if not video_url:
            raise RuntimeError(
                "Kuaishou: không tìm thấy URL video trong dữ liệu trang.\n"
                "Video có thể bị giới hạn khu vực hoặc API đã thay đổi."
            )

        logger.info(
            "Kuaishou: extracted — title=%r uploader=%r duration=%ds",
            title[:50], uploader[:30], duration,
        )

        return MediaInfo(
            url=video_url,
            title=title,
            uploader=uploader,
            duration=duration,
            thumbnail=thumbnail,
            platform="Kuaishou",
            formats=[{"format_id": "best", "url": video_url}],
            is_live=False,
            was_live=False,
            video_id=photo_id,
            source_engine="kuaishou",
        )
    finally:
        session.close()


# ── Engine class ──────────────────────────────────────────────────────────────


class KuaishouEngine:
    """
    Downloads Kuaishou videos directly from CDN, bypassing yt-dlp.

    extract_info() — fetch metadata (no download)
    download()     — download video to task.output_dir
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def extract_info(self, url: str) -> MediaInfo:
        return extract_info_kuaishou(url, self._config)

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """Download Kuaishou video. Mutates task in-place; raises RuntimeError on failure."""
        with task._lock:
            task.status = DownloadStatus.DOWNLOADING

        media_info = task.media_info
        if media_info is None:
            raise RuntimeError("KuaishouEngine.download: task.media_info is None")

        cdn_url = media_info.url
        if not cdn_url or not cdn_url.startswith("http"):
            logger.info("Kuaishou: CDN URL missing, re-extracting for task %s", task.id)
            media_info = extract_info_kuaishou(task.url, self._config)
            cdn_url = media_info.url

        # ── Output path ───────────────────────────────────────────────────
        output_dir = (
            Path(task.output_dir) if task.output_dir else self._config.download_dir
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_title = _sanitise_filename(media_info.title or media_info.video_id or "video")
        filename = output_dir / f"{safe_title}.mp4"
        stem = filename.stem
        counter = 1
        while filename.exists():
            filename = output_dir / f"{stem} ({counter}).mp4"
            counter += 1

        part_path = filename.with_suffix(".part")

        with task._lock:
            task.filename = str(filename)

        # ── Stream download with progress ─────────────────────────────────
        session = _make_session()
        try:
            resp = session.get(
                cdn_url,
                headers={**_API_HEADERS, "Accept": "*/*"},
                stream=True,
                timeout=_DL_TIMEOUT,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Kuaishou CDN trả về HTTP {resp.status_code}. "
                    "URL CDN có thể đã hết hạn — thử lại."
                )

            total_bytes = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            chunk_size = 1024 * 256  # 256 KB
            speed_window: list[tuple[float, int]] = []

            with open(part_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if task.is_cancellation_requested:
                        break
                    task.wait_if_paused()

                    fh.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()

                    speed_window.append((now, len(chunk)))
                    speed_window = [(t, b) for t, b in speed_window if now - t <= 3.0]
                    window_bytes = sum(b for _, b in speed_window)
                    window_sec = (
                        now - speed_window[0][0] if len(speed_window) > 1 else 1.0
                    )
                    speed_bps = window_bytes / max(window_sec, 0.001)

                    progress_pct = (downloaded / total_bytes * 100.0) if total_bytes else 0.0
                    eta_s = ""
                    if total_bytes and speed_bps > 0:
                        secs = int((total_bytes - downloaded) / speed_bps)
                        mm, ss = divmod(secs, 60)
                        eta_s = f"{mm:02d}:{ss:02d}"

                    with task._lock:
                        task.progress = min(progress_pct, 99.0)
                        task.downloaded_bytes = downloaded
                        task.total_bytes = total_bytes
                        task.speed = _fmt_speed(speed_bps)
                        task.eta = eta_s

                    if on_progress:
                        on_progress(task)
        finally:
            session.close()

        if task.is_cancellation_requested:
            part_path.unlink(missing_ok=True)
            with task._lock:
                task.status = DownloadStatus.CANCELLED
            return

        if not _is_valid_mp4(part_path):
            part_path.unlink(missing_ok=True)
            raise RuntimeError(
                "File tải về không hợp lệ (không phải MP4 hoặc quá nhỏ). "
                "URL CDN có thể đã hết hạn — thử lại."
            )

        part_path.rename(filename)

        with task._lock:
            task.status = DownloadStatus.COMPLETED
            task.progress = 100.0
            task.filename = str(filename)

        logger.info(
            "Kuaishou: download complete — %s (%.1f MB)",
            filename.name,
            filename.stat().st_size / 1_048_576,
        )

        if on_progress:
            on_progress(task)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sanitise_filename(name: str) -> str:
    for ch in r'<>:"/\|?*':
        name = name.replace(ch, "_")
    name = name.strip(". ")
    return name or "kuaishou_video"


def _fmt_speed(bps: float) -> str:
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.1f} MiB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KiB/s"
    return f"{bps:.0f} B/s"


def _is_valid_mp4(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 10_240:
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        return (
            header[4:8] == b"ftyp"
            or header[0:4] in (b"ftyp", b"moov", b"mdat")
            or header[4:8] in (b"moov", b"mdat", b"wide")
        )
    except OSError:
        return False
