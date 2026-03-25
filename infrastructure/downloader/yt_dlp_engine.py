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

import os
import yt_dlp

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from utils.ffmpeg_locator import get_ffmpeg_path

logger = logging.getLogger(__name__)


def _validate_cookie_path(config: "ConfigManager") -> str | None:
    """Resolve and validate the configured cookie_file path (CWE-22).

    Returns the resolved absolute path string (plaintext or .enc) when the
    file exists and is safely contained within the OmniDL data directory.
    Returns None in all other cases.

    DPAPI-encrypted files (.enc) are validated here but NOT decrypted — the
    caller must call _prepare_cookie_for_use() to get a usable path.

    Security rationale
    ──────────────────
    The allowed root is restricted to *config_path.parent* only (the OmniDL
    data directory).  Path.parents is used instead of str.startswith() to
    prevent the sibling-directory bypass.
    """
    cookie_file = config.cookie_file.strip()
    if not cookie_file:
        return None
    cp = Path(cookie_file).resolve()
    safe_root = config.config_path.parent.resolve()
    is_safe = cp == safe_root or safe_root in cp.parents

    if not is_safe:
        logger.warning(
            "cookie_file rejected — not inside a safe directory: %s",
            cookie_file,
        )
        return None

    if cp.is_file():
        logger.info("Using cookie file: %s", cp)
        return str(cp)

    # Auto-fallback: config stores .txt but encrypt_cookie_file renamed to .enc
    if cp.suffix == ".txt":
        enc_cp = cp.with_suffix(".enc")
        if enc_cp.is_file():
            logger.info("Cookie file auto-upgraded .txt → .enc: %s", enc_cp.name)
            return str(enc_cp)

    logger.warning(
        "cookie_file rejected — not found on disk: %s",
        cookie_file,
    )
    return None


def _prepare_cookie_for_use(cookie_path: str) -> "tuple[str, bool]":
    """Decrypt .enc cookie to a temp file if needed.

    Returns (usable_path, is_temp).
    If is_temp=True, the caller MUST delete the file after use.
    If is_temp=False, the path is the original file — do not delete.
    """
    from infrastructure.downloader.cookie_storage import decrypt_to_tempfile, is_encrypted
    p = Path(cookie_path)
    if is_encrypted(p):
        try:
            tmp = decrypt_to_tempfile(p)
            return str(tmp), True
        except Exception as exc:
            logger.warning("Failed to decrypt cookie file %s: %s", p.name, exc)
            return cookie_path, False  # fallback: pass enc path (will fail in yt-dlp, but safe)
    return cookie_path, False

# ── Per-platform cookie resolution ───────────────────────────────────────────

# Maps registered hostname suffixes to ConfigManager platform keys.
# Intentionally uses exact hostname matching (via urlparse) rather than
# substring regex to prevent the subdomain-spoofing attack:
#   malicious.tiktok.com.evil → hostname does NOT end with ".tiktok.com"
#   www.tiktok.com            → hostname ends with ".tiktok.com" ✅
_COOKIE_PLATFORM_MAP: list[tuple[str, str]] = [
    ("youtube.com",   "youtube"),   # age-restricted content requires Google account cookies
    ("youtu.be",      "youtube"),
    ("tiktok.com",    "tiktok"),
    ("instagram.com", "instagram"),
    ("facebook.com",  "facebook"),
    ("fb.watch",      "facebook"),
    ("twitter.com",   "twitter"),
    ("x.com",         "twitter"),
    ("threads.net",   "threads"),
    ("threads.com",   "threads"),   # new domain (2024+)
]


def _resolve_cookie(url: str, config: "ConfigManager") -> str | None:
    """Return the validated cookie file path for *url*, or None.

    Resolution order (first non-empty validated path wins):
      1. config.platform_cookies[platform_key]   — per-platform (most specific)
      2. config.cookie_file                       — global fallback
      3. None                                     — no cookie configured

    Platforms with no entry in _COOKIE_PLATFORM_MAP (Twitch, Vimeo, Dailymotion …)
    skip step 1 and go straight to the global fallback.  This means YouTube
    downloads never accidentally receive an Instagram session cookie.

    Security: every candidate path is validated by _validate_cookie_path_raw()
    (same CWE-22 logic as _validate_cookie_path()) before being returned.
    """
    from urllib.parse import urlparse as _urlparse

    # ── Step 1: detect platform from URL hostname ─────────────────────────
    platform_key: str | None = None
    try:
        hostname = (_urlparse(url).hostname or "").lower()
    except Exception:
        hostname = ""

    for domain, key in _COOKIE_PLATFORM_MAP:
        if hostname == domain or hostname.endswith("." + domain):
            platform_key = key
            break

    # ── Step 2: per-platform cookie ───────────────────────────────────────
    if platform_key:
        candidate = config.get_cookie_for_platform(platform_key).strip()
        validated = _validate_cookie_path_raw(candidate, config)
        if validated:
            logger.info(
                "Using %s cookie: %s", platform_key, validated
            )
            return validated

    # ── Step 3: global cookie fallback ───────────────────────────────────
    return _validate_cookie_path(config)


def _validate_cookie_path_raw(cookie_file: str, config: "ConfigManager") -> str | None:
    """Validate a raw cookie file path string (CWE-22).

    Accepts both .txt (plaintext) and .enc (DPAPI-encrypted) files.
    Auto-fallback: if config stores .txt but only .enc exists on disk
    (encrypt_cookie_file renamed it after the path was saved), silently
    use the .enc file — prevents the "rejected — not inside safe directory"
    false-positive that occurs when the .txt no longer exists.

    Returns the resolved absolute path string, or None if invalid.
    """
    if not cookie_file:
        return None
    cp = Path(cookie_file).resolve()
    safe_root = config.config_path.parent.resolve()
    is_safe = cp == safe_root or safe_root in cp.parents

    if not is_safe:
        logger.warning(
            "platform cookie_file rejected — not inside safe directory: %s",
            cookie_file,
        )
        return None

    if cp.is_file():
        return str(cp)

    # Auto-fallback: .txt stored in config but .enc exists on disk
    # (encrypt_cookie_file renamed .txt → .enc after config was saved)
    if cp.suffix == ".txt":
        enc_cp = cp.with_suffix(".enc")
        if enc_cp.is_file():
            logger.debug(
                "Platform cookie auto-upgraded .txt → .enc: %s", enc_cp.name
            )
            return str(enc_cp)

    logger.warning(
        "platform cookie_file rejected — file not found on disk: %s",
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
    (re.compile(r"threads\.(net|com)", re.I), "Threads"),
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
    # Instagram photo — no video stream in post
    if "no video in this post" in msg_l or "no video formats found" in msg_l:
        return (
            "Bài đăng này chỉ có ảnh, không có video.\n"
            "OmniDL sẽ thử tải ảnh với format='best'.\n"
            "Nếu vẫn lỗi, hãy đảm bảo đang dùng cookie Instagram "
            "(không phải Facebook) và yt-dlp phiên bản mới nhất."
        )
    # yt-dlp internal extractor bug (e.g. KeyError('=') on base64 shortcodes)
    if "extractor error" in msg_l or "keyerror" in msg_l:
        return (
            "yt-dlp gặp lỗi nội bộ khi phân tích URL này.\n"
            "Hãy cập nhật yt-dlp lên phiên bản mới nhất:\n"
            "Settings → Cập nhật yt-dlp, hoặc chạy: pip install -U yt-dlp"
        )
    # Instagram-specific errors
    if "checkpoint" in msg_l or "challenge_required" in msg_l:
        return (
            "Instagram yêu cầu xác minh tài khoản.\n"
            "1. Mở Instagram trên trình duyệt, hoàn tất xác minh.\n"
            "2. Export cookies mới (dùng tiện ích 'Get cookies.txt LOCALLY').\n"
            "3. Cập nhật cookie file trong Settings → Network → Cookie file.\n"
            "Lưu ý: Cookie Instagram thường hết hạn sau 1–2 tuần."
        )
    if ("rate" in msg_l and ("limit" in msg_l or "429" in msg_l or "too many" in msg_l)
            or "429" in msg_l or "too many requests" in msg_l):
        return (
            "Rate limit reached — too many requests in a short time.\n"
            "Wait 5–10 minutes and try again. "
            "Enabling browser cookies in Settings may help."
        )
    # Facebook-specific errors
    if "content not available" in msg_l or "this content isn" in msg_l:
        return (
            "This Facebook content is not available. "
            "It may require login or be restricted to a specific region."
        )
    # Geographic / copyright restrictions
    if "geo" in msg_l or "region" in msg_l or "country" in msg_l:
        return (
            "This content is geo-restricted and not available in your region.\n"
            "Try enabling a VPN or proxy in Settings → Network → Proxy URL."
        )
    if "your ip" in msg_l and "blocked" in msg_l:
        return (
            "IP của bạn bị TikTok/nền tảng chặn truy cập bài đăng này.\n"
            "Nguyên nhân thường gặp:\n"
            "  • IP bị đưa vào danh sách đen do quá nhiều request (rate-limit tạm thời)\n"
            "  • ISP/VPS/datacenter IP bị chặn theo chính sách địa lý\n"
            "Giải pháp:\n"
            "  1. Bật proxy/VPN trong Settings → Network → Proxy URL\n"
            "     (ví dụ: socks5://127.0.0.1:1080 nếu dùng local proxy)\n"
            "  2. Chờ 5–15 phút rồi thử lại (nếu là rate-limit tạm thời)\n"
            "  3. Refresh cookie TikTok: Settings → Per-Platform Cookies → TikTok"
        )
    if "copyright" in msg_l:
        return "This content has been blocked due to a copyright claim."
    if "blocked" in msg_l:
        return (
            "This content is blocked or access was denied.\n"
            "Try enabling a VPN or proxy in Settings → Network → Proxy URL."
        )
    # Account issues
    if "suspended" in msg_l or ("account" in msg_l and "disabled" in msg_l):
        return "The account that posted this content has been suspended."
    if "members only" in msg_l or "subscriber" in msg_l:
        return (
            "This content is for members/subscribers only.\n"
            "Make sure you are logged in via cookies in Settings."
        )
    # TikTok / platform deleted or unavailable video
    if "currently not available" in msg_l or "video does not exist" in msg_l \
            or "this video is not available" in msg_l:
        return (
            "Video này không còn tồn tại hoặc đã bị xóa.\n"
            "Kiểm tra lại URL — nếu link rút gọn (vt.tiktok.com), "
            "thử mở trong trình duyệt để lấy link đầy đủ."
        )
    return msg[:200]


# Profile / channel / playlist URL patterns — these return multiple items.
# When a URL matches, extract_info() uses extract_flat="in_playlist" to
# collect entry URLs without triggering per-video extractors (fast, safe).
# Platforms supported: TikTok, YouTube, Twitter/X, Instagram, Threads.
#
# Design rule: match ONLY unambiguous profile/channel/playlist URLs.
# Single-video URLs (e.g. /video/ID, /watch?v=, /status/) must NOT match
# so noplaylist=True continues to work correctly for them.
_PROFILE_URL_RE = re.compile(
    r'(?:'
    r'tiktok\.com/@[^/?#]+/?(?:[?#].*)?$'                              # TikTok @user
    r'|youtube\.com/(?:@[^/?#]+|c/[^/?#]+|channel/[^/?#]+|user/[^/?#]+)/?(?:[?#].*)?$'  # YT channel
    r'|youtube\.com/playlist\?'                                         # YT playlist
    r'|twitter\.com/(?!.*?/status/)[^/?#]+/?(?:[?#].*)?$'              # Twitter @user (not tweets)
    r'|x\.com/(?!.*?/status/)[^/?#]+/?(?:[?#].*)?$'                    # X @user (not tweets)
    r'|instagram\.com/(?!p/|reel/|tv/|live/|stories/|explore/|accounts/)[^/?#]+/?(?:[?#].*)?$'  # IG profile
    r'|threads\.(net|com)/@[^/?#]+/?(?:[?#].*)?$'                      # Threads @user
    r')',
    re.I,
)


# Known URL patterns that yt-dlp cannot handle, with actionable messages.
# Checked before calling yt-dlp to give a better UX than a generic error.
# Patterns that are ALWAYS blocked (no cookie can help)
_ALWAYS_BLOCKED: list[tuple[re.Pattern, str]] = [
    (
        # threads.com — Meta's new domain (2024+). Neither yt-dlp nor gallery-dl
        # has an extractor for this domain yet. threads.net posts also unsupported.
        re.compile(r"threads\.(com|net)/.*/(post|p)/", re.I),
        "Threads posts chưa được yt-dlp hỗ trợ.\n\n"
        "Cách tải video Threads:\n"
        "• Mở post trong trình duyệt → nhấn ... → Lưu\n"
        "• Hoặc dùng tiện ích 'Video Downloader' trên trình duyệt.",
    ),
]

# Patterns that require cookies — only blocked if no cookie is configured
_NEEDS_COOKIES: list[tuple[re.Pattern, str]] = [
    (
        # Instagram Stories — both /stories/ path and reel-style archive URLs
        re.compile(r"instagram\.com/stories/", re.I),
        "Instagram Stories require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
    (
        # Instagram Live — old format (/username/live/) AND new 2024+ format (/live/shortcode/)
        re.compile(r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I),
        "Instagram Live streams require login cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
    (
        # Facebook Live — facebook.com/live/ path
        re.compile(r"facebook\.com/live/", re.I),
        "Facebook Live streams require cookies.\n"
        "Set up a cookie file in Settings → Network → Cookie file.",
    ),
    (
        # Facebook Stories — covers /stories/, story.php, permalink story, share/r/
        re.compile(
            r"facebook\.com/(?:"
            r"stories/"
            r"|story\.php"
            r"|permalink\.php[^#]*story_fbid"
            r"|share/[rs]/"
            r"|.*[?&]view_single"
            r")",
            re.I,
        ),
        "Facebook Stories không thể tải tự động.\n\n"
        "Cách tải Story Facebook:\n"
        "• Mở Story trong trình duyệt → nhấn ... → Lưu video\n"
        "• Hoặc dùng tiện ích 'Video Downloader' trên trình duyệt.",
    ),
]



def _check_unsupported_url(url: str, has_cookies: bool = False) -> str | None:
    """Return a user-friendly message if URL is blocked, else None.
    
    has_cookies=True means a cookie file or browser cookies are configured,
    so cookie-required URLs (Stories, Live) are allowed through to yt-dlp.
    """
    for pattern, message in _ALWAYS_BLOCKED:
        if pattern.search(url):
            return message
    if not has_cookies:
        for pattern, message in _NEEDS_COOKIES:
            if pattern.search(url):
                return message
    return None


# DEF-015: module-level constant — avoids re-allocating on every download() call
_MEDIA_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v",
    ".mp3", ".m4a", ".opus", ".aac", ".flac", ".wav",
    ".ts",   # MPEG-TS live recordings — needed so pp_hook captures task.filename
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
            self._config.cookie_file.strip()
            or self._config.use_cookies
            or any(self._config.platform_cookies.values())
        )
        early_msg = _check_unsupported_url(url, has_cookies=has_cookies)
        if early_msg:
            raise RuntimeError(early_msg)

        # ── Profile / channel / playlist fast-path ────────────────────────
        # When URL is a profile or channel page (TikTok @user, YouTube channel,
        # Twitter/X @user, Instagram profile, Threads @user, YouTube playlist),
        # use extract_flat="in_playlist" to collect entry URLs WITHOUT triggering
        # per-video extractors.
        #
        # WHY: The two-pass approach (noplaylist=True then noplaylist=False with
        # ignoreerrors) caused "No video formats found!" and "status code 100004"
        # errors to appear in logs because yt-dlp attempted to fully extract each
        # entry even when we only needed the URL.  extract_flat="in_playlist"
        # returns only {_type, url, id, title} per entry — no extractor called,
        # no per-video errors, extremely fast (1 API call vs N calls).
        #
        # SAFETY: _PROFILE_URL_RE is conservative — only unambiguous profile URLs
        # match.  Single-video URLs (/video/ID, /watch?v=, /status/) do NOT match,
        # so noplaylist=True continues to protect against accidental playlist
        # expansion for videos that are embedded inside playlists.

        opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,   # DEF-007: prevent hang on stalled server
            # FIX-FINAL: JS challenge solver for YouTube n-challenge
            "remote_components": "ejs:github",
        }
        # Deno PATH is injected once at startup (main.py) — not per-call.
        # os.environ.update() from worker threads is not thread-safe on CPython.
        _ffmpeg_dir = get_ffmpeg_path()
        if _ffmpeg_dir:
            opts["ffmpeg_location"] = _ffmpeg_dir

        if self._config.proxy:
            opts["proxy"] = self._config.proxy
        # Cookie resolution: per-platform first, global fallback second.
        # _resolve_cookie() uses urlparse hostname matching (not regex substring)
        # to prevent subdomain-spoofing. CWE-22 guard applied inside.
        _cookie_path = _resolve_cookie(url, self._config)
        _cookie_temp_ei: str | None = None   # temp file to clean up after extract
        if _cookie_path:
            _usable, _is_temp = _prepare_cookie_for_use(_cookie_path)
            opts["cookiefile"] = _usable
            if _is_temp:
                _cookie_temp_ei = _usable
        if not opts.get("cookiefile") and self._config.use_cookies:
            opts["cookiesfrombrowser"] = (self._config.cookies_browser,)

        # Profile / channel fast-path (after opts are built so cookie/proxy
        # are included in the flat playlist fetch).
        if _PROFILE_URL_RE.search(url):
            # Clean temp cookie before early return (playlist path)
            if _cookie_temp_ei:
                try:
                    Path(_cookie_temp_ei).unlink(missing_ok=True)
                except Exception:
                    pass
            return self._extract_playlist_flat(url, opts)

        # Retry up to 2 times on transient errors (rate limit, network blip).
        last_exc: Exception | None = None
        info = None
        # Regex for Instagram photo/reel/TV shortcode extraction from URL.
        # Used to build a synthetic MediaInfo when yt-dlp raises "no video in
        # this post" — photo posts have no video stream but ARE downloadable
        # with format="best".  The shortcode is extracted for video_id so
        # the filename template is still meaningful.
        _ig_photo_re = re.compile(
            r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.I
        )
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                break   # success
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                msg_l = msg.lower()

                # FIX-A: Instagram photo posts raise one of two errors during
                # extract_info depending on the yt-dlp version and whether
                # cookies are present:
                #   • Without cookies: "There is no video in this post"
                #   • With valid cookies: "No video formats found!"
                # Both mean the same thing — the post contains only images.
                # We intercept both and return synthetic MediaInfo(formats=[],
                # duration=0) so BUG Z photo detection in home_tab activates.
                # The download() call then uses format="best" to fetch the image.
                _is_photo_error = (
                    "no video in this post" in msg_l
                    or "no video formats found" in msg_l
                )
                if _is_photo_error and _ig_photo_re.search(url):
                    m = _ig_photo_re.search(url)
                    shortcode = m.group(1) if m else ""
                    logger.info(
                        "Instagram photo detected (no video stream) — "
                        "returning synthetic MediaInfo for photo path: %s",
                        shortcode,
                    )
                    # Clean temp cookie before early return (photo path)
                    if _cookie_temp_ei:
                        try:
                            Path(_cookie_temp_ei).unlink(missing_ok=True)
                        except Exception:
                            pass
                    return MediaInfo(
                        url=url,
                        title=shortcode or "Instagram Photo",
                        uploader="",
                        duration=0,
                        thumbnail="",
                        platform="Instagram",
                        formats=[],
                        is_live=False,
                        was_live=False,
                        video_id=shortcode,
                        # Signal DownloadManager to use GalleryDlEngine.
                        # yt-dlp cannot download image-only posts — routing
                        # here avoids 4 pointless retries through yt-dlp.
                        source_engine="gallery_dl",
                    )

                # Don't retry hard errors (private, removed, unsupported,
                # or Instagram auth challenges that retrying cannot resolve).
                _hard = (
                    "private", "removed", "unsupported url",
                    "not found", "404", "login",
                    "checkpoint", "challenge_required",   # FIX-3: Instagram auth
                    "no video in this post",              # FIX-B: photo (no cookies)
                    "no video formats found",             # FIX-B: photo (with cookies)
                    "extractor error",                    # FIX-B: yt-dlp internal bug
                    "currently not available",            # TikTok deleted video
                    "video does not exist",               # TikTok removed video
                    "this video is not available",        # TikTok region/deleted
                    "unavailable",                        # generic platform unavailable
                )
                if any(k in msg_l for k in _hard):
                    raise RuntimeError(_friendly_error(msg)) from exc
                # NOTE: With remote_components=ejs:github, most YouTube errors
                # are resolved automatically. Retries here handle transient issues.
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)   # 1s, 2s back-off
            except Exception as exc:
                msg = str(exc)
                # FIX-B: KeyError('=') manifests as a generic Exception with
                # "extractor error" in the string representation.  Don't retry.
                if "extractor error" in msg.lower() or "keyerror" in msg.lower():
                    raise RuntimeError(_friendly_error(msg)) from exc
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

        # Single-video path — profile URLs were already handled above by
        # _extract_playlist_flat() and returned early.  At this point info
        # is always a single-video dict (not a playlist).
        # FIX-2: Force is_live=True for Instagram Live URLs even when yt-dlp
        # returns is_live=False (race condition during stream preparation).
        # The regex mirrors _instagram_live_re in download() — both patterns
        # must be kept in sync (BUG Y invariant).
        _ig_live_re = re.compile(
            r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I
        )
        is_live_resolved = bool(info.get("is_live")) or bool(_ig_live_re.search(url))

        # Clean up decrypted temp cookie file now that extraction is complete
        if _cookie_temp_ei:
            try:
                Path(_cookie_temp_ei).unlink(missing_ok=True)
            except Exception:
                pass

        return MediaInfo(
            url=url,
            title=info.get("title") or "Unknown",
            uploader=info.get("uploader") or info.get("channel") or "",
            duration=int(info.get("duration") or 0),
            thumbnail=info.get("thumbnail") or "",
            platform=_detect_platform(url),
            formats=info.get("formats") or [],
            is_live=is_live_resolved,
            was_live=bool(info.get("was_live")),
            video_id=info.get("id") or "",
            # playlist_entries always empty here — profile URLs returned early
        )

    # ── Playlist / channel fast extraction ───────────────────────────────

    def _extract_playlist_flat(
        self, url: str, base_opts: "dict[str, object]"
    ) -> "MediaInfo":
        """
        Collect entry URLs from a profile/channel/playlist URL using
        extract_flat="in_playlist".

        extract_flat tells yt-dlp to return ONLY basic metadata (url, id,
        title) for each entry WITHOUT calling per-video extractors.  This
        means:
          • No "No video formats found!" errors for unavailable videos
          • No "status code 100004" errors for geo-restricted entries
          • Extremely fast — single HTTP request instead of N requests
          • Output entries are always {"_type": "url", "url": "<webpage_url>"}
            so entry["url"] IS the canonical webpage URL, no preference
            logic needed

        Supports: TikTok @user, YouTube channel/@handle/playlist,
                  Twitter/X @user, Instagram profile, Threads @user.

        Raises RuntimeError on hard failures (auth, empty playlist, network).
        """
        opts_flat: dict[str, object] = dict(base_opts)
        opts_flat["noplaylist"]   = False
        opts_flat["extract_flat"] = "in_playlist"
        # ignoreerrors silences per-entry warnings that can appear even with
        # extract_flat (e.g. private entries in a mixed public/private feed).
        opts_flat["ignoreerrors"] = True

        logger.info(
            "Profile/playlist flat-extract: %s", url
        )
        try:
            with yt_dlp.YoutubeDL(opts_flat) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(_friendly_error(str(exc))) from exc
        except Exception as exc:
            raise RuntimeError(
                f"Không thể lấy danh sách từ URL này: {exc}"
            ) from exc

        if not info:
            raise RuntimeError(
                "Không nhận được dữ liệu từ URL. "
                "Kiểm tra lại URL hoặc thêm cookie file trong Settings."
            )

        # Flatten nested playlist (e.g. YouTube channel has a playlist of
        # playlists) — we only want leaf-level video entries.
        raw_entries: list[dict] = []

        def _collect(node: "dict") -> None:
            for entry in node.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("_type") == "playlist":
                    _collect(entry)           # recurse one level
                else:
                    raw_entries.append(entry)

        if info.get("_type") == "playlist":
            _collect(info)
        else:
            # URL returned a single item (unusual for profile URL but handle it)
            raw_entries.append(info)

        # Extract webpage URLs — with extract_flat, entry["url"] is always
        # the canonical video page URL (not a CDN stream URL).
        entry_urls: list[str] = []
        for entry in raw_entries:
            u = entry.get("url") or entry.get("webpage_url") or ""
            if u.startswith("http"):
                entry_urls.append(u)

        logger.info(
            "Flat-extract: %d URLs from '%s'",
            len(entry_urls),
            (info.get("title") or info.get("uploader") or url)[:60],
        )

        if not entry_urls:
            raise RuntimeError(
                "Playlist/profile không có video nào khả dụng.\n"
                "Có thể tài khoản private hoặc cần cookie file."
            )

        # Use playlist-level title/uploader for display
        playlist_title = (
            info.get("title")
            or info.get("uploader")
            or info.get("channel")
            or ""
        )
        # Use first entry's thumbnail as preview (may be empty — acceptable)
        first = raw_entries[0] if raw_entries else {}

        return MediaInfo(
            url=url,
            title=playlist_title or "Unknown",
            uploader=info.get("uploader") or info.get("channel") or "",
            duration=0,                         # no single duration for a playlist
            thumbnail=first.get("thumbnail") or "",
            platform=_detect_platform(url),
            formats=[],                          # no format picker for playlists
            is_live=False,
            was_live=False,
            video_id=info.get("id") or "",
            playlist_entries=entry_urls,
            playlist_title=playlist_title,
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
        # Always resolve to an absolute path before passing to yt-dlp.
        # If output_dir is relative (e.g. the user typed a relative path into
        # Settings, or the app was launched from a different CWD), yt-dlp will
        # write files relative to the process CWD.  On Windows + PyInstaller
        # the CWD is the EXE directory, not the downloads folder, so the file
        # lands in the wrong place AND info_dict["filepath"] is relative,
        # causing task.filename to resolve to EXE-dir/video.mp4.
        # .resolve() makes the path absolute before yt-dlp ever sees it.
        output_dir = (
            Path(task.output_dir) if task.output_dir else self._config.download_dir
        ).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Livestream detection ──────────────────────────────────────────
        # yt-dlp may not always set is_live=True for TikTok live during
        # extract_info (race between go-live and extraction timing).
        # We also check duration==0 + TikTok URL as a strong secondary signal.
        # Same race condition applies to Instagram live streams.
        # Instagram live regex covers both the old (/username/live/) and
        # new 2024+ (/live/shortcode/) URL formats.
        # NOTE: Instagram photo posts (source_engine="gallery_dl") are routed
        # to GalleryDlEngine by DownloadManager before reaching this method —
        # this code never runs for photos.
        _tiktok_live_re = re.compile(r"tiktok\.com/@[^/]+/live", re.I)
        _instagram_live_re = re.compile(
            r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I
        )
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
                    f" %(title).80B [%(id).12B].ts"
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
            # FIX-FINAL: Enable remote JS challenge solver (ejs:github).
            # YouTube uses n-challenge (encrypted nonce) to validate stream URLs.
            # Without solving it, all formats appear unavailable or return garbage.
            # yt-dlp downloads the solver script from GitHub on first use (~1s),
            # then caches it. This is the same as --remote-components ejs:github.
            "allow_unplayable_formats": False,
            "remote_components": "ejs:github",
            # Use bundled Deno if available (injected via PATH env override below)
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
            "progress_hooks": [self._make_progress_hook(task, on_progress, is_live)],
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
        # Cookie resolution: per-platform first, global fallback second.
        # _resolve_cookie() applies CWE-22 guard via _validate_cookie_path_raw().
        # If cookie is DPAPI-encrypted (.enc), decrypt to temp file for this download.
        _cookie_path = _resolve_cookie(task.url, self._config)
        _cookie_temp_dl: str | None = None  # temp file to clean up in finally
        if _cookie_path:
            _usable, _is_temp = _prepare_cookie_for_use(_cookie_path)
            opts["cookiefile"] = _usable
            if _is_temp:
                _cookie_temp_dl = _usable
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

        _original_pp_hook = opts.get("postprocessor_hooks", [None])[0]

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

        # Deno PATH is injected once at startup (main.py) — not per-call.
        # os.environ.update() from worker threads is not thread-safe on CPython.

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
                        if task.filename and f.stem in task.filename:
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

        # Always clean up the decrypted temp cookie file, even on error
        if _cookie_temp_dl:
            try:
                Path(_cookie_temp_dl).unlink(missing_ok=True)
                logger.debug("Cleaned up temp cookie file: %s", _cookie_temp_dl)
            except Exception:
                pass

    # ── Internal helpers ──────────────────────────────────────────────────

    def _make_progress_hook(
        self,
        task: DownloadTask,
        callback: Optional[Callable[[DownloadTask], None]],
        is_live: bool = False,
    ) -> Callable[[dict], None]:
        def hook(d: dict[str, Any]) -> None:
            # Respect pause / cancel
            task.wait_if_paused()
            if task.is_cancellation_requested:
                raise yt_dlp.utils.DownloadError("Cancelled by user")

            status = d.get("status", "")
            if status == "downloading":
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
                # FIX-1: Live streams never have total_bytes (open-ended HLS).
                # Show bytes recorded so user knows the download is active.
                # Condition is guarded by is_live so VODs are never affected —
                # even VODs that transiently report total_bytes=0 in the first
                # few hook calls will not show this message.
                elif is_live and task.total_bytes == 0 and task.downloaded_bytes > 0:
                    task.eta = f"⏺ {_fmt_bytes(task.downloaded_bytes)} đã ghi"
                _fname = d.get("filename")
                if _fname and Path(_fname).is_absolute():
                    task.filename = _fname
                if callback:
                    callback(task)

            elif status == "finished":
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
            # NOTE: "no_check_certificates" intentionally excluded —
            # disabling TLS verification exposes all downloads to MITM attacks.
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


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count used for live recording progress display."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GiB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n} B"


def _fmt_eta(eta: int | float) -> str:
    s = int(eta)
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"