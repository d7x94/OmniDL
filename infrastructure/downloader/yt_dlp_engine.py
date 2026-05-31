"""
infrastructure/downloader/yt_dlp_engine.py
Thin wrapper around yt-dlp: metadata extraction + download execution.
"""

from __future__ import annotations

import logging
import random
import re
import shlex
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse as _urlparse

import yt_dlp

# BUG-CB FIX: curl_cffi provides libcurl-impersonate TLS fingerprinting.
# Sites like Kuaishou reject Python's default TLS fingerprint with
# SSL RECORD_LAYER_FAILURE. Requires curl-cffi>=0.10.0,<0.15 (yt-dlp constraint).
# opts["impersonate"] must be an ImpersonateTarget object, not a plain string.
# BUG-CC FIX: log non-ImportError failures so curl_cffi load problems are visible
# in debug log (previously silently fell back to _CURL_CFFI_AVAILABLE=False).
# BUG-CD FIX: ImpersonateTarget.from_str("chrome") always succeeds — it just
# constructs an object; it does NOT validate that the backend can serve "chrome".
# In a frozen EXE (PyInstaller), curl_cffi imports OK but the CurlCFFIRH handler
# may have no available targets if native DLLs are not loadable, causing every
# yt-dlp call to fail with "Impersonate target 'chrome' is not available".
# Fix: after creating the target, probe the handler's supported target map.
# If "chrome" is absent, treat as unavailable and set _CURL_CFFI_AVAILABLE=False.
# BUG-CE FIX: curl-cffi>=0.15 is rejected by yt-dlp with an ImportError whose
# message contains "not supported" / "versions". The previous probe silently
# swallowed ALL ImportErrors ("module path differs across versions — skip probe")
# so _CURL_CFFI_AVAILABLE stayed True even though the handler cannot be loaded.
# Fix: re-raise any ImportError whose message indicates a version incompatibility
# so the outer except-Exception handler logs it and sets _CURL_CFFI_AVAILABLE=False.
try:
    import curl_cffi as _curl_cffi  # noqa: F401
    from yt_dlp.networking.impersonate import ImpersonateTarget as _ImpersonateTarget

    _IMPERSONATE_TARGET = _ImpersonateTarget.from_str("chrome")
    # _IMPERSONATE_STRING: the curl_cffi-native string (e.g. "chrome131") used
    # when calling curl_cffi.requests.Session/head/get directly.
    # curl_cffi expects its own string format, NOT ImpersonateTarget objects.
    # _IMPERSONATE_TARGET (ImpersonateTarget object) is for yt-dlp opts only.
    _IMPERSONATE_STRING = "chrome"  # safe default for curl_cffi < 0.15
    # Runtime probe — no network, no I/O. Checks that yt-dlp accepts this curl_cffi
    # version and that the handler lists at least one "chrome" target.
    # BUG-CE2 FIX: curl_cffi >= 0.15 uses versioned ImpersonateTarget keys
    # (e.g. ImpersonateTarget(client='chrome', version='124', os='macos')).
    # The bare ImpersonateTarget.from_str("chrome") (no version/os) does NOT
    # appear in the map, causing a false "not in map" miss and disabling
    # impersonation even though curl_cffi is fully functional.
    # Fix: scan the map for any key with client='chrome' and use it directly.
    # Also extract the map VALUE (curl_cffi string) for direct curl_cffi API calls.
    try:
        from yt_dlp.networking._curlcffi import CurlCFFIRH as _CurlCFFIRH  # type: ignore[import]

        _supported_map = getattr(_CurlCFFIRH, "_SUPPORTED_IMPERSONATE_TARGET_MAP", {})
        # First try exact match (curl_cffi < 0.15 — unversioned keys)
        if _IMPERSONATE_TARGET not in _supported_map:
            # curl_cffi >= 0.15: scan for any chrome target in the map
            _chrome_target = next(
                (k for k in _supported_map if getattr(k, "client", None) == "chrome"),
                None,
            )
            if _chrome_target is None:
                raise RuntimeError(
                    f"CurlCFFIRH does not list 'chrome' as a supported target "
                    f"(available: {list(_supported_map.keys())[:5]})"
                )
            _IMPERSONATE_TARGET = _chrome_target
        # BUG-TT-10 FIX: extract the curl_cffi-native string from map value.
        # curl_cffi.requests.Session/head/get(impersonate=...) requires a string
        # like "chrome131", NOT an ImpersonateTarget object. The map value is
        # the correct curl_cffi string; the key is the yt-dlp ImpersonateTarget.
        _map_val = _supported_map.get(_IMPERSONATE_TARGET)
        if isinstance(_map_val, str) and _map_val:
            _IMPERSONATE_STRING = _map_val
    except ImportError as _probe_err:
        _probe_msg = str(_probe_err).lower()
        if "not supported" in _probe_msg or "versions" in _probe_msg or "only curl" in _probe_msg:
            # yt-dlp explicitly rejected this curl_cffi version — propagate so
            # the outer handler disables impersonation.
            raise
        # yt-dlp internal module path differs across versions but import itself
        # succeeded — skip map probe, trust the outer import succeeded.
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CURL_CFFI_AVAILABLE = False
    _IMPERSONATE_TARGET = None
    _IMPERSONATE_STRING = None  # type: ignore[assignment]
except Exception as _curl_load_err:
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "curl_cffi/impersonate unavailable (%s) — "
        "TLS impersonation disabled; Kuaishou and TikTok live may fail",
        _curl_load_err,
    )
    _CURL_CFFI_AVAILABLE = False
    _IMPERSONATE_TARGET = None
    _IMPERSONATE_STRING = None  # type: ignore[assignment]

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from utils.ffmpeg_locator import get_ffmpeg_path

logger = logging.getLogger(__name__)


class _PlatformRateLimiter:
    """Minimum-interval rate limiter for platform API requests, shared across threads."""

    def __init__(self, min_interval: float = 2.0) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_t: float = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._last_t + self.min_interval - now
            if gap > 0:
                time.sleep(gap)
            self._last_t = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._last_t = 0.0


_TT_RL = _PlatformRateLimiter(2.0)


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
    ("youtube.com", "youtube"),  # age-restricted content requires Google account cookies
    ("youtu.be", "youtube"),
    ("tiktok.com", "tiktok"),
    ("instagram.com", "instagram"),
    ("facebook.com", "facebook"),
    ("fb.watch", "facebook"),
    ("twitter.com", "twitter"),
    ("x.com", "twitter"),
    ("threads.net", "threads"),
    ("threads.com", "threads"),  # new domain (2024+)
    ("kuaishou.com", "kuaishou"),
    ("kwai.com", "kuaishou"),
    ("v.kuaishou.com", "kuaishou"),
]

_IG_RL = _PlatformRateLimiter(1.5)
_FB_RL = _PlatformRateLimiter(1.0)
_TW_RL = _PlatformRateLimiter(0.8)

_PLATFORM_RL_MAP: dict[str, _PlatformRateLimiter] = {
    "tiktok": _TT_RL,
    "instagram": _IG_RL,
    "facebook": _FB_RL,
    "twitter": _TW_RL,
}


def platform_for_url(url: str) -> str | None:
    try:
        hostname = (_urlparse(url).hostname or "").lower()
    except Exception:
        return None
    for domain, key in _COOKIE_PLATFORM_MAP:
        if hostname == domain or hostname.endswith("." + domain):
            return key
    return None


def _get_platform_rl(url: str) -> "_PlatformRateLimiter | None":
    key = platform_for_url(url)
    return _PLATFORM_RL_MAP.get(key) if key else None


def _resolve_cookie(url: str, config: "ConfigManager", override: "str | None" = None) -> "str | None":
    """Return the validated cookie file path for *url*, or None.

    Resolution order (first non-empty validated path wins):
      0. override                                 — pool-assigned account cookie
      1. config.platform_cookies[platform_key]   — per-platform (most specific)
      2. config.cookie_file                       — global fallback
      3. None                                     — no cookie configured

    Platforms with no entry in _COOKIE_PLATFORM_MAP (Twitch, Vimeo, Dailymotion …)
    skip step 1 and go straight to the global fallback.  This means YouTube
    downloads never accidentally receive an Instagram session cookie.

    Security: every candidate path is validated by _validate_cookie_path_raw()
    (same CWE-22 logic as _validate_cookie_path()) before being returned.
    """
    # ── Step 0: pool-assigned account cookie (highest priority) ──────────
    if override:
        validated = _validate_cookie_path_raw(override, config)
        if validated:
            logger.debug("Using pool-assigned cookie override: %s", Path(override).name)
            return validated

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
            logger.info("Using %s cookie: %s", platform_key, validated)
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
            logger.debug("Platform cookie auto-upgraded .txt → .enc: %s", enc_cp.name)
            return str(enc_cp)

    logger.warning(
        "platform cookie_file rejected — file not found on disk: %s",
        cookie_file,
    )
    return None


# BUG-BM FIX: TikTok short-link URL patterns.
#
# vt.tiktok.com/* and vm.tiktok.com/* are share-link redirectors — the
# user pastes them, yt-dlp resolves them internally to the canonical
# tiktok.com/@user/video/<id> URL.  However, task.url is set from the
# ORIGINAL user-supplied URL, so it still holds the short form at download
# time.  Any per-URL logic that uses task.url (format patching, live
# detection) must also recognise the short-link domains.
#
# Pattern intent:
#   _TIKTOK_VOD_RE   — canonical VOD URL: tiktok.com/@user/video/<numeric-id>
#   _TIKTOK_SHORT_RE — short-link domains: vt.tiktok.com/* or vm.tiktok.com/*
#   _TIKTOK_LIVE_RE  — canonical live URL: tiktok.com/@user/live
#
# Combined guard (_TIKTOK_VOD_RE OR _TIKTOK_SHORT_RE) is used wherever
# logic must apply to all TikTok VOD downloads regardless of URL form.
# _TIKTOK_LIVE_RE is unchanged — short live links are extremely rare and
# TikTok does not publish vt.tiktok.com/… for livestreams.
_TIKTOK_VOD_RE = re.compile(r"tiktok\.com/@[^/]+/video/\d+", re.I)
_TIKTOK_SHORT_RE = re.compile(r"(?:vt|vm)\.tiktok\.com/", re.I)
# BUG-TT-06 FIX: also match m.tiktok.com/share/live/<room_id> — the mobile
# share URL form that yt-dlp accepts directly without a profile-page scrape.
_TIKTOK_LIVE_RE = re.compile(r"(?:tiktok\.com/@[^/]+/live|m\.tiktok\.com/share/live/\d+)", re.I)

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


def _build_ffmpeg_cookie_header(cookie_file: str) -> str:
    """Parse a Netscape cookie file and return a 'name=val; ...' string for tiktok.com.

    BUG-TT-20C: TikTok stage CDN nodes require session cookies in HTTP headers
    even when the HLS URL is signed. Used to build the -headers Cookie: argument
    for direct FFmpeg calls.
    Returns empty string on any error so callers can skip -headers gracefully.
    """
    if not cookie_file:
        return ""
    try:
        from pathlib import Path as _P  # noqa: PLC0415

        pairs: list[str] = []
        for line in _P(cookie_file).read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain = parts[0].lstrip(".")
            if "tiktok" not in domain:
                continue
            name, value = parts[5], parts[6]
            if name and value:
                pairs.append(f"{name}={value}")
        return "; ".join(pairs)
    except Exception:  # noqa: BLE001
        return ""


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
    if "not currently live" in msg_l or "channel is not currently live" in msg_l:
        return "channel is not currently live"
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
    if (
        "rate" in msg_l
        and ("limit" in msg_l or "429" in msg_l or "too many" in msg_l)
        or "429" in msg_l
        or "too many requests" in msg_l
    ):
        return (
            "Rate limit reached — too many requests in a short time.\n"
            "Wait 5–10 minutes and try again. "
            "Enabling browser cookies in Settings may help."
        )
    # TLS fingerprint rejection - Kuaishou and similar CDNs (BUG-CC)
    if "ssl routines" in msg_l or "tls connect error" in msg_l or "curl: (35)" in msg_l:
        return (
            "Loi ket noi TLS - server tu choi TLS fingerprint mac dinh.\n"
            "OmniDL dung curl_cffi (Chrome impersonation) de bypass loi nay.\n"
            "Neu loi van xay ra:\n"
            "  1. Kiem tra antivirus/proxy khong intercept HTTPS\n"
            "  2. Thu bat proxy trong Settings -> Network -> Proxy URL\n"
            "  3. Chay: pip install -U curl-cffi"
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
    # ffmpeg exit code on livestream — HLS URL expired or stream ended/unavailable.
    # 3419392776 = 0xCBAE0008 = STATUS_PIPE_NOT_AVAILABLE (Windows named pipe).
    # Also covers non-Windows ffmpeg failures (any non-zero exit from ffmpeg).
    if "ffmpeg exited with code" in msg_l:
        return (
            "Không thể ghi livestream — ffmpeg báo lỗi.\n"
            "Nguyên nhân thường gặp:\n"
            "  • Link livestream đã hết hạn (URL TikTok expire sau ~1–2 phút)\n"
            "    → Sao chép lại link và thử tải ngay lập tức\n"
            "  • Livestream đã kết thúc hoặc bị tạm dừng\n"
            "  • Kết nối mạng không ổn định trong quá trình ghi\n"
            "Nếu lỗi vẫn xảy ra: thử tải lại link hoặc đợi livestream ổn định."
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
    if "not comfortable" in msg_l or "log in for access" in msg_l:
        return (
            "TikTok yêu cầu đăng nhập để tải video này.\n"
            "Cookie pool có thể đã hết hạn hoặc dùng tài khoản khác.\n"
            "Giải pháp: Refresh cookie TikTok: Settings → Per-Platform Cookies → TikTok"
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
    # TikTok API status 10231 — API parameter issue, video still accessible in browser
    if "status code 10231" in msg_l:
        return (
            "TikTok API từ chối request (status 10231) dù video vẫn xem được.\n"
            "Thử:\n"
            "  1. Refresh cookie TikTok: Settings → Per-Platform Cookies → TikTok\n"
            "  2. Bật proxy/VPN trong Settings → Network → Proxy URL"
        )
    # TikTok / platform deleted or unavailable video
    if (
        "currently not available" in msg_l
        or "video does not exist" in msg_l
        or "this video is not available" in msg_l
    ):
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
    r"(?:"
    r"tiktok\.com/@[^/?#]+/?(?:[?#].*)?$"  # TikTok @user
    r"|youtube\.com/(?:@[^/?#]+|c/[^/?#]+|channel/[^/?#]+|user/[^/?#]+)/?(?:[?#].*)?$"  # YT channel
    r"|youtube\.com/playlist\?"  # YT playlist
    r"|twitter\.com/(?!.*?/status/)[^/?#]+/?(?:[?#].*)?$"  # Twitter @user (not tweets)
    r"|x\.com/(?!.*?/status/)[^/?#]+/?(?:[?#].*)?$"  # X @user (not tweets)
    r"|instagram\.com/(?!p/|reel/|tv/|live/|stories/|explore/|accounts/)[^/?#]+/?(?:[?#].*)?$"  # IG profile
    r"|threads\.(net|com)/@[^/?#]+/?(?:[?#].*)?$"  # Threads @user
    r")",
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
        "Instagram Stories require login cookies.\nSet up a cookie file in Settings → Network → Cookie file.",
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
        "Facebook Live streams require cookies.\nSet up a cookie file in Settings → Network → Cookie file.",
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
_MEDIA_EXTS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mkv",
        ".webm",
        ".mov",
        ".avi",
        ".flv",
        ".m4v",
        ".mp3",
        ".m4a",
        ".opus",
        ".aac",
        ".flac",
        ".wav",
        ".ts",  # MPEG-TS live recordings — needed so pp_hook captures task.filename
    }
)

# DEF-015: module-level constant — avoids re-allocating on every _apply_extra_args() call.
# Allowlist for user-supplied extra yt-dlp args (CWE-78: OS Command Injection guard).
# Options such as --exec, --exec-before-download, --postprocessor-args are intentionally
# excluded — they allow arbitrary command execution from user-supplied config.
# NOTE: "no_check_certificates" intentionally excluded —
# disabling TLS verification exposes all downloads to MITM attacks.
_SAFE_EXTRA_OPTS: frozenset[str] = frozenset(
    {
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
    }
)

# BUG-BT: Audio-only output formats that require FFmpegExtractAudio postprocessor
# instead of merge_output_format.  merge_output_format is designed to pick the
# container when MERGING separate video+audio streams; it cannot transcode audio
# to a different codec independently.  Using it with mp3/m4a/flac on YouTube
# (where the native format is webm/opus) causes "Postprocessing: Conversion failed!".
_AUDIO_ONLY_EXTS: frozenset[str] = frozenset({"mp3", "m4a", "aac", "flac", "opus", "wav", "ogg"})

# BUG-CC FIX: Kuaishou short-link pre-resolver.
# v.kuaishou.com/* and www.kuaishou.com/short-video/* are redirect chains.
# yt-dlp's Generic extractor follows them with its own HTTP client, but the
# Kuaishou CDN rejects the TLS connection (WRONG_VERSION_NUMBER) even when
# curl_cffi impersonation is active — the CDN serves a non-TLS response at
# a hop yt-dlp cannot skip.  Pre-resolving with curl_cffi directly (HEAD +
# allow_redirects) gives us the final kwai.com/kuaishou.com canonical URL
# which yt-dlp can then fetch without hitting the problematic redirect chain.
_KUAISHOU_SHORT_RE = re.compile(r"v\.kuaishou\.com/|www\.kuaishou\.com/short-video/", re.I)


def _resolve_kuaishou_url(url: str) -> str:
    """Follow Kuaishou short-link redirects and return the final URL.

    Uses curl_cffi with Chrome impersonation.  Falls back to original URL on
    any failure — the caller (extract_info) will then let yt-dlp try as usual.
    """
    if not _CURL_CFFI_AVAILABLE:
        return url
    try:
        from curl_cffi import requests as _cffi_req

        # BUG-TT-10 FIX: curl_cffi API requires a string (e.g. "chrome131"),
        # NOT an ImpersonateTarget object. Use _IMPERSONATE_STRING here.
        resp = _cffi_req.head(
            url,
            impersonate=_IMPERSONATE_STRING,  # type: ignore[arg-type]
            allow_redirects=True,
            timeout=15,
        )
        final = str(resp.url)
        if final and final != url:
            logger.debug("Kuaishou short URL resolved: %s -> %s", url, final)
            return final
        # HEAD may not follow all redirects on some CDNs — try GET if same URL
        resp2 = _cffi_req.get(
            url,
            impersonate=_IMPERSONATE_STRING,  # type: ignore[arg-type]
            allow_redirects=True,
            timeout=15,
        )
        final2 = str(resp2.url)
        if final2 and final2 != url:
            logger.debug("Kuaishou short URL resolved (GET): %s -> %s", url, final2)
            return final2
    except Exception as exc:
        logger.debug("Kuaishou short URL pre-resolve failed (%s) — using original", exc)
    return url


# BUG-BQ DIAGNOSTIC: yt-dlp logger bridge — captures format selection,
# FFmpegMergerPP activity, and fallback events into omnidl_run.log.
# Read-only: zero effect on download logic or output.
_DIAG_KEYWORDS: tuple[str, ...] = (
    "merging formats",
    "destination:",
    "requested format",
    "ffmpeg",
    "format_id",
    "vcodec",
    "acodec",
    "sorted",
    "selected",
    "tiktok",
    "downloading",
    "fallback",
    "not available",
)


class _DiagLogger:
    def debug(self, msg: str) -> None:
        if any(kw in msg.lower() for kw in _DIAG_KEYWORDS):
            logger.debug("[yt-dlp diag] %s", msg.strip())

    def info(self, msg: str) -> None:
        pass  # progress bar lines — skip

    def warning(self, msg: str) -> None:
        logger.warning("[yt-dlp] %s", msg.strip())

    def error(self, msg: str) -> None:
        logger.error("[yt-dlp] %s", msg.strip())


def _tt29_cookie_sources(
    task_url: str, config: ConfigManager, override: "str | None"
) -> "list[tuple[str, str]]":
    """Return (cookie_path, label) pairs for BUG-TT-29 rotation.

    Sequence: pool → global-tiktok → anon → pool → global-tiktok.
    Consecutive duplicates are removed so identical cookie files don't
    burn retries (e.g. when pool override == per-platform cookie).
    """
    pool = _resolve_cookie(task_url, config, override) or ""
    global_tt = _resolve_cookie(task_url, config, None) or ""
    raw: list[tuple[str, str]] = [
        (pool, "pool"),
        (global_tt, "global"),
        ("", "anon"),
        (pool, "pool"),
        (global_tt, "global"),
    ]
    out: list[tuple[str, str]] = []
    last: object = object()
    for c, lbl in raw:
        if c == last:
            continue
        out.append((c, lbl))
        last = c
    return out


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
        # Route Kuaishou URLs to KuaishouEngine before any yt-dlp processing.
        # yt-dlp has no extractor for v.kuaishou.com and fails with
        # "This platform is not supported" for all Kuaishou short-links.
        from infrastructure.downloader.kuaishou_engine import (  # noqa: PLC0415
            extract_info_kuaishou,
            is_kuaishou_url,
        )

        if is_kuaishou_url(url):
            return extract_info_kuaishou(url, self._config)

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

        # BUG-CC FIX: Pre-resolve Kuaishou short URLs before passing to yt-dlp.
        # yt-dlp's Generic extractor fails with TLS WRONG_VERSION_NUMBER when
        # following v.kuaishou.com redirect chains.  Resolving the final URL
        # here via curl_cffi bypasses the problematic CDN hop entirely.
        if _KUAISHOU_SHORT_RE.search(url):
            url = _resolve_kuaishou_url(url)

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
            "socket_timeout": 20,  # DEF-007: prevent hang on stalled server
            # FIX-FINAL: JS challenge solver for YouTube n-challenge
            # Must be a list — str causes yt-dlp to iterate characters (BUG-BQ).
            "remote_components": ["ejs:github"],
        }
        # BUG-CB FIX: impersonate Chrome TLS fingerprint when curl_cffi is available.
        if _CURL_CFFI_AVAILABLE:
            opts["impersonate"] = _IMPERSONATE_TARGET
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
        _cookie_temp_ei: str | None = None  # temp file to clean up after extract
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

        # Rate-limit per-platform to reduce HTTP 429 risk when analyse and download
        # fire concurrently (shared per-platform limiter with _extract_tiktok_live_hls_url).
        _rl = _get_platform_rl(url)
        if _rl is not None:
            _rl.acquire()

        # Retry up to 2 times on transient errors (rate limit, network blip).
        last_exc: Exception | None = None
        info = None
        _saw_10231 = False  # BUG-TT-10231: track across all attempts (last_exc may be 429)
        # Regex for Instagram photo/reel/TV shortcode extraction from URL.
        # Used to build a synthetic MediaInfo when yt-dlp raises "no video in
        # this post" — photo posts have no video stream but ARE downloadable
        # with format="best".  The shortcode is extracted for video_id so
        # the filename template is still meaningful.
        _ig_photo_re = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.I)
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                break  # success
            except yt_dlp.utils.DownloadError as exc:
                msg = str(exc)
                msg_l = msg.lower()
                if "status code 10231" in msg_l:
                    _saw_10231 = True

                # FIX-A: Instagram photo posts raise one of two errors during
                # extract_info depending on the yt-dlp version and whether
                # cookies are present:
                #   • Without cookies: "There is no video in this post"
                #   • With valid cookies: "No video formats found!"
                # Both mean the same thing — the post contains only images.
                # We intercept both and return synthetic MediaInfo(formats=[],
                # duration=0) so BUG Z photo detection in home_tab activates.
                # The download() call then uses format="best" to fetch the image.
                _is_photo_error = "no video in this post" in msg_l or "no video formats found" in msg_l
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
                    "private",
                    "removed",
                    "unsupported url",
                    "not found",
                    "404",
                    "login",
                    "checkpoint",
                    "challenge_required",  # FIX-3: Instagram auth
                    "no video in this post",  # FIX-B: photo (no cookies)
                    "no video formats found",  # FIX-B: photo (with cookies)
                    "extractor error",  # FIX-B: yt-dlp internal bug
                    "currently not available",  # TikTok deleted video
                    "video does not exist",  # TikTok removed video
                    "this video is not available",  # TikTok region/deleted
                    "unavailable",  # generic platform unavailable
                    "ssl routines",  # BUG-CC: TLS fingerprint rejection (Kuaishou)
                    "tls connect error",  # BUG-CC: curl TLS failure
                    "curl: (35)",  # BUG-CC: curl SSL connect error code
                    "is not available",  # BUG-CD: impersonate target missing in EXE
                    "not currently live",  # TikTok/IG channel is offline — not an error
                )
                if any(k in msg_l for k in _hard):
                    raise RuntimeError(_friendly_error(msg)) from exc
                # NOTE: With remote_components=ejs:github, most YouTube errors
                # are resolved automatically. Retries here handle transient issues.
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)  # 1s, 2s back-off
            except Exception as exc:
                msg = str(exc)
                # FIX-B: KeyError('=') manifests as a generic Exception with
                # "extractor error" in the string representation.  Don't retry.
                if "extractor error" in msg.lower() or "keyerror" in msg.lower():
                    raise RuntimeError(_friendly_error(msg)) from exc
                last_exc = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        if info is None:
            # BUG-TT-10231: TikTok web extraction returns status 10231 for some videos.
            # BUG-TT-CHALLENGE: TikTok also serves an anti-bot JS challenge page that
            # yt-dlp cannot solve, raising "Unexpected response from webpage request".
            # Both are bypassed by routing to the Android mobile API via app_info.
            # Use _saw_10231 (not just last_exc) — last retry may have been a 429.
            # Fix: use app_info (not app_name) to trigger _extract_aweme_app() (Android
            # mobile API). app_name alone is ignored by TikTokIE when app_info is absent:
            # _KNOWN_APP_INFO stays [] → yt-dlp skips app API → web extraction → 10231.
            _tt_web_blocked_ei = _saw_10231 or (
                last_exc
                and (
                    "status code 10231" in str(last_exc).lower()
                    or "unexpected response from webpage request" in str(last_exc).lower()
                )
            )
            if _tt_web_blocked_ei:
                for _fb_app_info in (
                    "/trill/35.1.3/2023501030/1180",
                    "/musical_ly/35.1.3/2023501030/1233",
                    "/aweme/35.1.3/2023501030/1128",
                ):
                    _tt_opts = dict(opts)
                    _tt_opts["extractor_args"] = {"tiktok": {"app_info": [_fb_app_info]}}
                    try:
                        with yt_dlp.YoutubeDL(_tt_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                        logger.debug("BUG-TT-10231: %s retry succeeded for %s", _fb_app_info, url)
                        break
                    except Exception as _tt_exc:
                        logger.debug("BUG-TT-10231: %s retry failed: %s", _fb_app_info, _tt_exc)
                        last_exc = _tt_exc

        if info is None:
            msg = str(last_exc) if last_exc else "No response from server"
            if any(k in msg.lower() for k in ("rate", "429", "too many")):
                platform = _detect_platform(url)
                msg = (
                    f"{platform} rate limit reached. "
                    "Wait 2-3 minutes and try again. "
                    "Tip: enable browser cookies in Settings -> Network."
                )
            raise RuntimeError(_friendly_error(msg)) from last_exc

        # Single-video path — profile URLs were already handled above by
        # _extract_playlist_flat() and returned early.  At this point info
        # is always a single-video dict (not a playlist).
        # FIX-2: Force is_live=True for Instagram Live URLs even when yt-dlp
        # returns is_live=False (race condition during stream preparation).
        # The regex mirrors _instagram_live_re in download() — both patterns
        # must be kept in sync (BUG Y invariant).
        _ig_live_re = re.compile(r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I)
        # FIX-TK / BUG-BM: TikTok canonical VOD URLs (/video/<id>) must never
        # be treated as live — force is_live=False regardless of what yt-dlp
        # returns (race condition during stream preparation can flip is_live).
        #
        # BUG-CH FIX: Short links (vt/vm.tiktok.com/*) were previously also
        # forced to is_live=False, which was correct for VOD short links but
        # broke live stream short links (e.g. a user shares a live via short
        # link).  yt-dlp resolves short links internally and sets is_live=True
        # when the resolved URL is a live stream.  We must honour that signal.
        # Guard: _TIKTOK_VOD_RE (canonical /video/<id>) is unambiguous — only
        # canonical VOD URLs match, not short links or live paths.
        if _TIKTOK_VOD_RE.search(url):
            # BUG-CH invariant: canonical VOD URL => never live, even if
            # yt-dlp race-returns is_live=True during stream preparation.
            is_live_resolved = False
        elif _TIKTOK_SHORT_RE.search(url):
            # BUG-CH FIX: short link — trust yt-dlp's is_live from the
            # resolved URL.  is_live=True means the short link resolved to
            # a live stream; is_live=False means it resolved to a VOD.
            is_live_resolved = bool(info.get("is_live"))
        else:
            is_live_resolved = bool(info.get("is_live")) or bool(_ig_live_re.search(url))

        # Clean up decrypted temp cookie file now that extraction is complete
        if _cookie_temp_ei:
            try:
                Path(_cookie_temp_ei).unlink(missing_ok=True)
            except Exception:
                pass

        # BUG-TT-28 FIX: store the canonical webpage_url (e.g. @user/live) so that
        # BUG-TT-27's _extract_url selection matches _TIKTOK_LIVE_RE at download time.
        # Short links (vm/vt.tiktok.com) resolved by yt-dlp carry the canonical URL
        # in info["webpage_url"] but not in the original `url` parameter.
        _canonical_url = info.get("webpage_url") or url
        _uploader = info.get("uploader") or info.get("uploader_id") or info.get("channel") or ""
        # BUG-TT-28 FIX: yt-dlp's [vm.tiktok] extractor leaves uploader empty for live
        # streams even when webpage_url is the canonical @user/live URL.  Extract the
        # username from that URL so Windows post-download rename doesn't fall back to
        # "Unknown".
        if not _uploader:
            _m_upl = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", _canonical_url, re.I)
            if _m_upl:
                _uploader = _m_upl.group(1)
        # BUG-TT-30 FIX: for TikTok live, yt-dlp sets info["id"] to the room_id.
        # Store it so _extract_tiktok_live_hls_url can use BUG-TT-25/26/29 fallbacks.
        _tt_room_id = ""
        if is_live_resolved and _TIKTOK_LIVE_RE.search(_canonical_url or ""):
            _tt_room_id = info.get("id") or ""
        return MediaInfo(
            url=_canonical_url,
            title=info.get("title") or "Unknown",
            uploader=_uploader,
            uploader_id=info.get("uploader_id", "") or "",
            duration=int(info.get("duration") or 0),
            thumbnail=info.get("thumbnail") or "",
            platform=_detect_platform(url),
            formats=info.get("formats") or [],
            is_live=is_live_resolved,
            was_live=bool(info.get("was_live")),
            video_id=info.get("id") or "",
            tiktok_room_id=_tt_room_id,
            # playlist_entries always empty here — profile URLs returned early
        )

    # ── Playlist / channel fast extraction ───────────────────────────────

    def _extract_playlist_flat(self, url: str, base_opts: "dict[str, object]") -> "MediaInfo":
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
        opts_flat["noplaylist"] = False
        opts_flat["extract_flat"] = "in_playlist"
        # ignoreerrors silences per-entry warnings that can appear even with
        # extract_flat (e.g. private entries in a mixed public/private feed).
        opts_flat["ignoreerrors"] = True

        logger.info("Profile/playlist flat-extract: %s", url)
        try:
            with yt_dlp.YoutubeDL(opts_flat) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(_friendly_error(str(exc))) from exc
        except Exception as exc:
            raise RuntimeError(f"Không thể lấy danh sách từ URL này: {exc}") from exc

        if not info:
            raise RuntimeError(
                "Không nhận được dữ liệu từ URL. Kiểm tra lại URL hoặc thêm cookie file trong Settings."
            )

        # Flatten nested playlist (e.g. YouTube channel has a playlist of
        # playlists) — we only want leaf-level video entries.
        raw_entries: list[dict] = []

        def _collect(node: "dict") -> None:
            for entry in node.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("_type") == "playlist":
                    _collect(entry)  # recurse one level
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
        playlist_title = info.get("title") or info.get("uploader") or info.get("channel") or ""
        # Use first entry's thumbnail as preview (may be empty — acceptable)
        first = raw_entries[0] if raw_entries else {}

        return MediaInfo(
            url=url,
            title=playlist_title or "Unknown",
            uploader=info.get("uploader") or info.get("channel") or "",
            duration=0,  # no single duration for a playlist
            thumbnail=first.get("thumbnail") or "",
            platform=_detect_platform(url),
            formats=[],  # no format picker for playlists
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
        output_dir = (Path(task.output_dir) if task.output_dir else self._config.download_dir).resolve()
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
        _tiktok_live_re = _TIKTOK_LIVE_RE
        _instagram_live_re = re.compile(r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I)
        is_live = bool(
            (task.media_info and task.media_info.is_live)
            or (task.media_info and task.media_info.duration == 0 and _tiktok_live_re.search(task.url))
            or (task.media_info and task.media_info.duration == 0 and _instagram_live_re.search(task.url))
        )
        if is_live:
            logger.info("Task %s detected as livestream — using HLS-safe options", task.id)

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
            # BUG-BW FIX: On Windows, yt-dlp derives the named pipe path from
            # outtmpl.  If outtmpl expands to a Unicode string (Vietnamese
            # uploader name, emoji in title), Windows cannot create the pipe
            # and ffmpeg exits with STATUS_PIPE_NOT_AVAILABLE (0xCBAE0008).
            # restrictfilenames=True sanitises file-system-illegal chars only —
            # it does NOT transliterate Unicode to ASCII, so the crash persists.
            #
            # Fix: on Windows, build outtmpl against the ASCII-safe system temp
            # dir instead of output_dir.  After the download completes the .ts
            # file is moved to output_dir by the post-download block below.
            # Non-Windows paths are unchanged — named pipes are not used there.
            import sys as _sys_outtmpl

            if _sys_outtmpl.platform == "win32":
                _live_outtmpl_dir = Path(tempfile.gettempdir()) / "omnidl_live"
                _live_outtmpl_dir.mkdir(parents=True, exist_ok=True)
                # BUG-BW2 FIX: On Windows, yt-dlp expands outtmpl BEFORE creating
                # the named pipe.  Even though _live_outtmpl_dir is ASCII, if
                # %(uploader) or %(title) expand to Unicode (Arabic, CJK, emoji),
                # the resulting pipe path is Unicode and Windows rejects it with
                # STATUS_PIPE_NOT_AVAILABLE (0xCBAE0008).
                # Fix: on Windows, use ONLY %(id) + a static timestamp in the
                # live outtmpl — both are guaranteed ASCII.  The .ts file is moved
                # to output_dir with a proper name by the post-download block.
                outtmpl = str(_live_outtmpl_dir / f"live_{rec_ts}_%(id).20B.ts")
            else:
                _live_outtmpl_dir = output_dir
                outtmpl = str(
                    _live_outtmpl_dir
                    / (f"%(uploader,channel|Unknown).50B - [LIVE] {rec_ts} %(title).80B [%(id).12B].ts")
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

        # BUG-BO / FIX-TK-AUDIO-3: TikTok long-form VODs (2+ min) use ONLY
        # DASH streams — separate video and audio tracks with no muxed
        # (progressive) stream at all.  TikTok's CDN mislabels ALL audio
        # tracks with acodec='none' and vcodec='none' in yt-dlp's format
        # table, even though the streams carry real AAC/Opus audio data.
        #
        # History of failed fixes:
        #   FIX-TK-AUDIO-2  injected [acodec!=none] into bestaudio → yt-dlp
        #                   found NO matching audio stream, /best fallback
        #                   picked a video-only DASH stream → silent output.
        #   BUG-BN          built a 3-tier chain adding "bestvideo+bestaudio"
        #                   as Tier 2.  Assumption was "FFmpegMergerPP merges
        #                   by stream URL, not by codec label" — INCORRECT.
        #                   yt-dlp's format selector, when BOTH selected
        #                   streams have acodec='none' AND vcodec='none',
        #                   cannot determine which stream carries audio and
        #                   which carries video; the merger is skipped or
        #                   selects the wrong stream → still silent.
        #
        # Root cause: yt-dlp's unstarred "bestvideo" / "bestaudio" selectors
        # require the codec metadata to be populated to distinguish stream
        # roles.  When TikTok mislabels BOTH tracks as acodec=none/vcodec=none
        # the selectors cannot determine which stream is audio and may skip
        # the merge entirely.
        #
        # Correct fix (BUG-BO): use yt-dlp's STARRED selectors:
        #   bestvideo*  = "best format that contains video" — codec-agnostic,
        #                 selects by actual stream content, not metadata label.
        #   bestaudio*  = "best format that contains audio" — same principle.
        #
        # Starred selectors are yt-dlp's recommended approach for DASH
        # platforms with unreliable codec metadata (documented in yt-dlp
        # FORMAT SELECTION guide).  They read the actual stream capabilities
        # rather than trusting the acodec/vcodec metadata fields.
        #
        # Transformation:
        #   bestvideo+bestaudio/best               ->
        #       bestvideo*+bestaudio*/best
        #   bestvideo[height<=1080]+bestaudio/best ->
        #       bestvideo*[height<=1080]+bestaudio*/best
        #   bestaudio/best  (audio-only)           -> unchanged
        #   best            (live/photo)           -> unchanged
        #
        # Guard: already starred → skip (idempotency).
        # BUG-BM invariant preserved: _TIKTOK_SHORT_RE covers vt/vm.tiktok.com.
        _format_id = task.format_id
        _is_tiktok_vod = _TIKTOK_VOD_RE.search(task.url) or _TIKTOK_SHORT_RE.search(task.url)

        # BUG-BT FIX: Detect audio-only output formats early so downstream
        # logic can route to FFmpegExtractAudio instead of merge_output_format.
        _out_ext = task.output_ext.lower().lstrip(".")
        _is_audio_output = _out_ext in _AUDIO_ONLY_EXTS

        # BUG-BT FIX: When the user requests an audio-only output format
        # (mp3, m4a, flac …) but the format_id still pulls both video and audio
        # streams (e.g. "bestvideo+bestaudio/best"), downloading the video
        # stream is wasteful and FFmpeg cannot mux the result into an audio
        # container.  Patch format_id to audio-only so only the audio stream
        # is fetched.  This is safe: if the user explicitly chose "Audio Only"
        # quality in the UI, format_id is already "bestaudio/best" and the
        # guard below is a no-op.
        if _is_audio_output and not is_live and "bestvideo" in _format_id:
            _format_id = "bestaudio/best"
        if not is_live and _is_tiktok_vod and "bestvideo*" not in _format_id:
            # BUG-BP FIX: BUG-BO only patched format_id values containing
            # "bestvideo".  When format_id="best" (the common default), the
            # guard missed it → single mislabeled DASH stream → silent output.
            #
            # BUG-BS FIX (2026-03-31, v2): TikTok exposes three kinds of
            # streams for VODs:
            #
            #   1. format_id="download": progressive muxed MP4, h264+aac,
            #      ALWAYS has audio, BUT carries a visible TikTok watermark.
            #
            #   2. format_id starts with "h264_": watermark-free progressive
            #      muxed MP4, h264+aac, reliable audio (confirmed by community
            #      and yt-dlp source).
            #
            #   3. format_id starts with "bytevc1_": watermark-free, TikTok
            #      proprietary H.265 codec.  Audio metadata says acodec=aac
            #      but the actual CDN stream carries no audio track for many
            #      VODs (confirmed: EmbedThumbnail OFF still silent → stream
            #      itself has no audio).
            #
            # Previous fix (BUG-BS v1) used "download/..." which solved the
            # silent-audio problem but introduced watermark on all TikTok VODs.
            #
            # Correct fix: prefer h264_* formats first (no watermark + audio),
            # fall back to "download" (watermark + audio) only if h264 URLs
            # are expired or geo-blocked, then last-resort starred selectors.
            #
            # Selector: best[format_id^=h264]/download/bestvideo*+bestaudio*/best
            #   best[format_id^=h264] — picks highest-tbr h264_* entry
            #                           (excludes format_id="download" since
            #                            it doesn't start with "h264")
            #   /download             — fallback: watermarked but guaranteed audio
            #   /bestvideo*+bestaudio*/best — last resort
            #
            # NOTE: the previous BUG-BS v1 mistake: only the `else` branch was
            # patched. task.format_id='bestvideo+bestaudio/best' (UI default)
            # contains "bestvideo" → the `if` branch ran → no "download" prefix.
            # Fix: apply the new selector in BOTH the if and else branches.
            if "bestvideo" in _format_id:
                # User picked a video+audio quality — honour their intent but
                # override with the watermark-free h264 chain for TikTok.
                # BUG-TT-SHOP FIX: TikTok videos with shopping links expose only
                # a single muxed stream with format_id="audio" (mislabeled — it
                # IS a video+audio MP4). bestvideo*+bestaudio* fails because there
                # is only 1 stream; bare "best" then picks it but yt-dlp infers
                # ext=mp3 from acodec metadata -> output is audio-only mp3.
                # Fix: insert bestvideo* (single-stream starred selector) before
                # bare best. Starred selector picks "best format containing video"
                # regardless of codec labels and preserves the mp4 container.
                # BUG-TT-SHOP-2 FIX: bestvideo* alone is insufficient when yt-dlp
                # infers ext=mp3 from acodec metadata on the single-stream case.
                # ext= filter added: prefer the stream explicitly labeled mp4/m4v
                # before falling back to codec-agnostic starred selectors.
                # BUG-TT-SHOP-3 FIX: shopping-link stream has ext=mp3 (not mp4),
                # so best[format_id=audio][ext=mp4] does NOT match it.
                # The stream falls through to bestvideo*+bestaudio* which cannot
                # split a single stream into video+audio pair -> yt-dlp picks
                # "audio" stream as audio-only -> output is silent mp4.
                # Fix: add best[format_id=audio] (no ext filter) AFTER the mp4
                # variant so the mislabeled shopping stream is still caught.
                # FFmpegVideoRemuxer (added below for all TikTok VODs) will
                # recontainer it to the correct output_ext regardless of ext label.
                # BUG-TT-EFF-2 FIX: template effect and shopping cart videos expose
                # a genuine audio-only stream with format_id="audio" (vcodec=none).
                # best[format_id=audio] was matching BEFORE /download, so the
                # watermarked video+audio "download" format was never tried.
                # Fix: move /download before best[format_id=audio] (unqualified).
                # best[format_id=audio][ext=mp4] still catches mislabeled muxed
                # shopping streams. best[format_id=audio] is now last resort only.
                _format_id = (
                    "best[format_id^=h264]"
                    "/best[format_id=audio][ext=mp4]"
                    "/download"
                    "/bestvideo*+bestaudio*"
                    "/bestvideo*"
                    "/best[format_id=audio]"
                    "/best"
                )
            elif "bestaudio" in _format_id:
                pass  # audio-only selector — leave unchanged, no video needed
            else:
                _format_id = (
                    "best[format_id^=h264]"
                    "/best[format_id=audio][ext=mp4]"
                    "/download"
                    "/bestvideo*+bestaudio*"
                    "/bestvideo*"
                    "/best[format_id=audio]"
                    "/best"
                )

        opts: dict[str, Any] = {
            # BUG-CF-2 FIX: BUG-CF reverted "best[protocol^=m3u8]/best" arguing
            # hls_prefer_native=True is sufficient. This is incorrect: hls_prefer_native
            # only switches the downloader when HLS is already selected; it does NOT
            # influence format selection. When "best" selects a FLV stream (TikTok live
            # exposes both HLS and FLV), yt-dlp uses FFmpegFD regardless of
            # hls_prefer_native=True, and FFmpegFD uses a Windows named pipe derived
            # from outtmpl -- crashing with 3419392776 (STATUS_PIPE_NOT_AVAILABLE).
            # hls_prefer_native=True only matters AFTER an HLS stream is selected.
            # Fix: re-apply the m3u8 preference for TikTok live URLs; fall back to
            # plain "best" for other live platforms (Instagram, Twitch) which have
            # no FLV/HLS ambiguity issue. Non-TikTok live and VODs are unaffected.
            #
            # BUG-TT-14 FIX: best[protocol^=m3u8] matches both "m3u8" (FFmpegFD) and
            # "m3u8_native" (HlsFD). When TikTok exposes an m3u8_native stream,
            # yt-dlp may prefer m3u8 (FFmpegFD) which uses a Windows named pipe.
            # hls_prefer_native=True switches HlsFD for m3u8 but NOT for m3u8_native
            # (HlsFD is already used). Force m3u8_native first, then m3u8, then
            # any https stream, then best -- never FLV/RTMP which always use FFmpegFD.
            "format": (
                "best[protocol=m3u8_native]/best[protocol^=m3u8]/best[protocol^=https]/best"
                if (is_live and (_TIKTOK_LIVE_RE.search(task.url) or _TIKTOK_SHORT_RE.search(task.url)))
                else ("best" if is_live else _format_id)
            ),
            # FIX-FINAL: JS challenge solver for YouTube n-challenge.
            # BUG-BQ FIX: must be a list — str causes yt-dlp to iterate over
            # individual characters and silently discard the solver.
            "allow_unplayable_formats": False,
            "remote_components": ["ejs:github"],
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            # BUG-CB FIX: impersonate Chrome TLS fingerprint when curl_cffi is available.
            # Required for sites that reject Python's default TLS fingerprint (e.g. Kuaishou).
            **({"impersonate": _IMPERSONATE_TARGET} if _CURL_CFFI_AVAILABLE else {}),
            # BUG-BQ: diagnostic logger — None safely ignored by yt-dlp.
            # Also enable for TikTok live to log which protocol/format is selected.
            "logger": _DiagLogger()
            if (_is_tiktok_vod and not is_live) or (is_live and _TIKTOK_LIVE_RE.search(task.url))
            else None,
            "ignoreerrors": False,
            "retries": self._config.max_retries,
            # BUG-TT-03 FIX: fragment_retries=0 caused entire live recordings to
            # abort on a single transient network glitch (Wi-Fi blip, DNS hiccup).
            # Set to 3 for live streams — enough to survive brief interruptions
            # without making Stop/Cancel unresponsive. yt-dlp's per-fragment
            # backoff (sleep_interval) keeps retry churn low.
            "fragment_retries": 3 if is_live else self._config.max_retries,
            # Jittered backoff between retries prevents bot-like fixed-interval
            # patterns that trigger platform rate-limit detection.
            "sleep_interval": random.uniform(1.0, 3.0),
            "max_sleep_interval": random.uniform(20.0, 40.0),
            "sleep_interval_requests": random.uniform(0.3, 1.2),
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

        # HLS livestream options.
        # live_from_start=False records from now (not stream start).
        # socket_timeout=10 tightened for live so the cancel check fires within
        # 10 s rather than 30 s.
        #
        # FIX-HLS-TEMPDIR (P2c):
        # Root cause of ffmpeg exit code 3419392776 (0xCBAE0008, Windows
        # STATUS_PIPE_NOT_AVAILABLE): yt-dlp writes HLS temp segments into the
        # output directory by default.  If that path contains Unicode characters
        # (e.g. Vietnamese folder names) ffmpeg fails immediately when trying
        # to open the temp file handle.
        #
        # Fix: redirect yt-dlp's "temp" path bucket to the system temp
        # directory (C:\Users\...\AppData\Local\Temp on Windows) which is
        # always ASCII and writeable.  The final .ts file is still written to
        # output_dir once the download completes.
        #
        # Additional live-only guards:
        # • concurrent_fragment_downloads=1  — serialise HLS segment writes to
        #   eliminate read/write races between parallel ffmpeg workers on
        #   Windows NTFS (another common source of the 0xCBAE0008 code).
        # • keep_fragments=False             — clean up every .part/.ytdl
        #   segment file after a completed or cancelled live recording so the
        #   output directory is not littered with partial segments.
        # • hls_prefer_native=True           — force yt-dlp to use its native
        #   Python HLS downloader (HlsFD) instead of FFmpegFD for live streams.
        #   FFmpegFD runs ffmpeg as a long-running subprocess and never calls
        #   progress hooks during the download, making cancel impossible.
        #   HlsFD downloads segments in a Python loop and calls progress hooks
        #   after each segment (~2-4 s), so cancel fires reliably.
        #   hls_use_mpegts was previously used here but switched yt-dlp to
        #   FFmpegFD, which broke cancel. The Unicode/pipe issue it fixed is
        #   already handled by paths["temp"] pointing to an ASCII temp dir.
        if is_live:
            opts["hls_prefer_native"] = True
            opts["live_from_start"] = False
            # BUG-TT-11 FIX: 10s too short for TikTok CDN token rotation (~15s).
            # Segments stalled at boundary -> ffmpeg exit 3419392776. 15s covers
            # rotation window; cancel still fires within one segment (~15s max).
            opts["socket_timeout"] = 15
            # BUG-TT-11 FIX: 3 retries insufficient when CDN rotates tokens mid-stream.
            opts["fragment_retries"] = 5
            opts["concurrent_fragment_downloads"] = 1  # no parallel HLS writes
            opts["keep_fragments"] = False
            # Route temp segment files away from the (potentially Unicode)
            # download directory.  tempfile.gettempdir() always returns an
            # ASCII-safe path on all supported platforms.
            _live_tmp = Path(tempfile.gettempdir()) / "omnidl_live"
            _live_tmp.mkdir(parents=True, exist_ok=True)
            opts["paths"] = {"temp": str(_live_tmp)}

        # merge_output_format tells yt-dlp to invoke ffmpeg to remux/merge the
        # downloaded streams.  For livestreams the HLS segments are already a
        # single muxed container — adding a merge step causes ffmpeg to crash
        # (Windows exit code 3419392776).  Only set it for non-live downloads.
        #
        # BUG-BT FIX: merge_output_format is designed for MERGING separate
        # video+audio streams into a single container.  It cannot transcode
        # audio from one codec to another (e.g. opus → mp3).  For audio-only
        # output formats, FFmpegExtractAudio is the correct postprocessor —
        # it is added below in the thumbnail/metadata block.
        if not is_live and not _is_audio_output:
            opts["merge_output_format"] = task.output_ext

        # Proper thumbnail embedding via postprocessors.
        # Skip for livestreams — there is no single output file to embed into
        # while the stream is ongoing; ffmpeg will crash trying.
        #
        # BUG-BT FIX: For audio-only output formats (mp3, m4a, flac …) the
        # FFmpegExtractAudio postprocessor MUST come first — it performs the
        # codec transcode.  Subsequent postprocessors (Metadata, EmbedThumbnail)
        # then operate on the already-transcoded audio file.
        if _is_audio_output and not is_live:
            _audio_pp: dict = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": _out_ext,
                # "0" = highest VBR quality for mp3/aac; ignored for lossless
                # formats (flac, wav) where quality is not applicable.
                "preferredquality": "0",
            }
            if self._config.embed_thumbnail:
                opts["writethumbnail"] = True
                opts["postprocessors"] = [
                    _audio_pp,
                    {"key": "FFmpegMetadata", "add_metadata": True},
                    {"key": "EmbedThumbnail"},
                ]
                # EmbedThumbnail stream-copies the audio so no re-encode occurs
                # when attaching the thumbnail artwork (ID3 for mp3, covr for m4a).
                opts["postprocessor_args"] = {
                    "EmbedThumbnail+ffmpeg": ["-c", "copy"],
                }
            else:
                pps: list = [_audio_pp]
                if self._config.embed_metadata:
                    pps.append({"key": "FFmpegMetadata", "add_metadata": True})
                opts["postprocessors"] = pps
        elif self._config.embed_thumbnail and not is_live:
            opts["writethumbnail"] = True
            opts["postprocessors"] = [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ]
            # BUG-BQ FIX: TikTok serves H.265/bytevc1 muxed streams where the
            # audio track is AAC but the video codec is ByteDance-proprietary.
            # When EmbedThumbnail invokes FFmpeg to re-mux the file, FFmpeg may
            # silently drop or corrupt the audio track if it attempts to
            # transcode rather than stream-copy — particularly with bytevc1
            # which FFmpeg does not fully recognise as a standard H.265 variant.
            #
            # Fix: pass -c copy to FFmpeg for all postprocessor operations so
            # BOTH video and audio streams are stream-copied as-is, with no
            # transcoding or codec re-interpretation.  The thumbnail is added
            # as a separate attachment stream, which does not require any
            # existing stream to be transcoded.
            #
            # postprocessor_args format: {"key": [ffmpeg_flags...]}
            # "EmbedThumbnail+ffmpeg" targets only the EmbedThumbnail
            # postprocessor's FFmpeg invocation — does not affect merge or
            # any other FFmpeg call.
            opts["postprocessor_args"] = {
                "EmbedThumbnail+ffmpeg": ["-c", "copy"],
            }

        # BUG-TT-SHOP-2 FIX: TikTok shopping-link videos expose a single muxed
        # stream with format_id="audio" and acodec=mp3 (mislabeled metadata).
        # yt-dlp infers ext=mp3 from acodec, so the output lands as .mp3 even
        # though the stream IS a video+audio MP4.
        # merge_output_format only triggers when yt-dlp merges 2+ streams — with
        # a single stream, no merge occurs and the inferred ext=mp3 is kept.
        # Fix: prepend FFmpegVideoRemuxer to the postprocessors list for all
        # TikTok VOD non-audio downloads to force-remux into the correct video
        # container (-c copy, zero re-encode) regardless of what ext yt-dlp infers.
        # This is a no-op when the output is already in the correct container.
        # Must run AFTER the thumbnail block so we prepend to the final pp list,
        # not an intermediate list that gets clobbered by the elif branch above.
        if _is_tiktok_vod and not is_live and not _is_audio_output:
            _remux_pp = {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": task.output_ext.lstrip(".") or "mp4",
            }
            _existing_pps = list(opts.get("postprocessors") or [])
            # FFmpegVideoRemuxer must be FIRST so the container is correct before
            # EmbedThumbnail / FFmpegMetadata operate on the file.
            opts["postprocessors"] = [_remux_pp] + _existing_pps

        if self._config.proxy:
            opts["proxy"] = self._config.proxy
        # Cookie resolution: pool override first, per-platform second, global fallback third.
        # _resolve_cookie() applies CWE-22 guard via _validate_cookie_path_raw().
        # If cookie is DPAPI-encrypted (.enc), decrypt to temp file for this download.
        _task_cookie_override = getattr(task, "_cookie_override", None)
        _cookie_path = _resolve_cookie(task.url, self._config, _task_cookie_override)
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

        _final_filepath: list[str] = []  # mutable closure cell
        # BUG-TT-EFF: track whether yt-dlp selected a video-less stream for a
        # non-audio-output TikTok VOD.  TikTok "template effect" videos (AR/duet
        # effects) only expose a single format_id="audio" stream with vcodec=none
        # — there is no video track available via the API.  The format selector
        # falls through all tiers and lands on this stream, producing an mp4
        # container with audio only.  We detect this in the pp_hook and raise a
        # clear error after download rather than silently delivering a broken file.
        _selected_vcodec: list[str] = []  # populated by pp_hook on first "finished"

        _original_pp_hook = opts.get("postprocessor_hooks", [None])[0]

        def _capturing_pp_hook(d: dict) -> None:
            # Capture filepath after EVERY postprocessor finishes — the last
            # "finished" event is always the final merged output.
            if d.get("status") == "finished":
                _info = d.get("info_dict") or {}
                # BUG-TT-EFF: record vcodec from the first finished event (before
                # any remux may change the info_dict).
                if not _selected_vcodec:
                    _vc = _info.get("vcodec") or ""
                    _selected_vcodec.append(_vc)
                fp = _info.get("filepath") or _info.get("__real_download_filename") or ""
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

                # BUG-BQ DIAGNOSTIC: log stream metadata so we can confirm
                # vcodec/acodec are preserved through every postprocessor step.
                if _is_tiktok_vod and not is_live:
                    _info = d.get("info_dict") or {}
                    _req_fmts = _info.get("requested_formats") or []
                    logger.debug(
                        "[BUG-BQ diag] pp_hook | status=%s | pp=%s | "
                        "format_id=%s | vcodec=%s | acodec=%s | "
                        "width=%s | height=%s | requested_formats_count=%d | "
                        "req_fmt_ids=%s | filepath=%s",
                        d.get("status"),
                        d.get("postprocessor", "?"),
                        _info.get("format_id", "?"),
                        _info.get("vcodec", "?"),
                        _info.get("acodec", "?"),
                        _info.get("width", "?"),
                        _info.get("height", "?"),
                        len(_req_fmts),
                        [f.get("format_id") for f in _req_fmts],
                        _info.get("filepath") or _info.get("__real_download_filename") or "?",
                    )

            # Also run the original pp hook (progress + postprocess callbacks)
            if _original_pp_hook:
                _original_pp_hook(d)

        opts["postprocessor_hooks"] = [_capturing_pp_hook]

        # Deno PATH is injected once at startup (main.py) — not per-call.
        # os.environ.update() from worker threads is not thread-safe on CPython.

        # BUG-BQ / BUG-BR DIAGNOSTIC: log effective format string + full format
        # list before download starts.  The full format list is critical for
        # diagnosing why long-form TikTok VODs download without audio — we need
        # to see EVERY format yt-dlp received from TikTok's API (not just the
        # selected one) to understand whether separate audio-only streams exist.
        if _is_tiktok_vod and not is_live:
            logger.debug(
                "[BUG-BQ diag] TikTok VOD starting | task=%s | original_format_id=%r | effective_format=%r",
                task.id[:8],
                task.format_id,
                opts.get("format"),
            )
            # BUG-BR FORMAT AUDIT: dump every format from extract_info so we
            # can see whether TikTok provides separate audio streams and how
            # they are labeled.  This is read-only — no effect on download.
            _all_formats = task.media_info.formats if task.media_info else []
            if _all_formats:
                logger.debug(
                    "[BUG-BR fmt-audit] %d format(s) available from extract_info:",
                    len(_all_formats),
                )
                for _fmt in _all_formats:
                    logger.debug(
                        "[BUG-BR fmt-audit]  id=%-35s | vcodec=%-10s | acodec=%-10s"
                        " | ext=%-5s | tbr=%-8s | abr=%-8s | vbr=%-8s"
                        " | height=%-5s | protocol=%s",
                        _fmt.get("format_id", "?"),
                        _fmt.get("vcodec", "?"),
                        _fmt.get("acodec", "?"),
                        _fmt.get("ext", "?"),
                        _fmt.get("tbr", "?"),
                        _fmt.get("abr", "?"),
                        _fmt.get("vbr", "?"),
                        _fmt.get("height", "?"),
                        _fmt.get("protocol", "?"),
                    )
            else:
                logger.debug(
                    "[BUG-BR fmt-audit] No formats in task.media_info "
                    "(extract_info may not have returned format list)"
                )

        # BUG-TT-16 FIX: for TikTok live, bypass yt-dlp download and call
        # FFmpeg directly with -reconnect flags. yt-dlp hard-codes FFmpegFD
        # for all is_live=True streams (ignores hls_prefer_native). FFmpegFD
        # has no reconnect logic, so it crashes when TikTok CDN rotates HLS
        # tokens every ~18-25s. Direct FFmpeg with -reconnect_on_http_error
        # handles this transparently.
        _is_tiktok_live_for_direct = is_live and (
            _TIKTOK_LIVE_RE.search(task.url) or _TIKTOK_SHORT_RE.search(task.url)
        )
        _live_vid_id = ""
        _direct_ffmpeg_ok = False
        _hls_confirmed_ended = False
        if _is_tiktok_live_for_direct:
            import sys as _sys_tt16

            _room_id_hint = (task.media_info.tiktok_room_id if task.media_info else "") or ""
            _username_hint = (task.media_info.uploader if task.media_info else "") or ""
            # BUG-TT-27 FIX: task.url may still be the original short link (vt/vm.tiktok.com)
            # when the user pasted a short URL. _extract_tiktok_live_hls_url passes task_url to
            # yt-dlp which then uses the [vm.tiktok] extractor → HTTP 429, and the BUG-TT-25
            # username regex r"tiktok\.com/@user/live" fails on short links so BUG-TT-25/26 are
            # skipped entirely. Use the canonical @user/live URL from media_info when available.
            _extract_url = (
                task.media_info.url
                if task.media_info
                and task.media_info.url
                and _TIKTOK_LIVE_RE.search(task.media_info.url or "")
                else task.url
            )
            _hls_result = self._extract_tiktok_live_hls_url(
                _extract_url,
                room_id=_room_id_hint,
                cookie_override=_task_cookie_override,
                username=_username_hint,
            )
            # () means all fallbacks (BUG-TT-25 + BUG-TT-26) were tried and exhausted
            _hls_confirmed_ended = _hls_result is not None and not _hls_result
            if _hls_result:
                _hls_url, _hls_vid_id, _hls_uploader, _hls_title = _hls_result
                _live_vid_id = _hls_vid_id
                # Enrich task.media_info with live metadata from HLS extraction
                # (analyse-phase info is often incomplete for TikTok live)
                if task.media_info:
                    if _hls_uploader and not task.media_info.uploader:
                        task.media_info.uploader = _hls_uploader
                    _mi_title = task.media_info.title or ""
                    _is_synthetic_mi_title = (
                        not _mi_title
                        or _mi_title == "Unknown"
                        or _mi_title.lower().startswith("tiktok-live video")
                        or (_hls_uploader and _mi_title.lstrip("@").lower().startswith(_hls_uploader.lower()))
                    )
                    if _hls_title and _is_synthetic_mi_title:
                        task.media_info.title = _hls_title
                # Build output path using same ASCII-safe pattern as outtmpl
                if _sys_tt16.platform == "win32":
                    _direct_out_dir = Path(tempfile.gettempdir()) / "omnidl_live"
                    _direct_out_dir.mkdir(parents=True, exist_ok=True)
                else:
                    _direct_out_dir = output_dir
                _direct_out_path = str(_direct_out_dir / f"live_{rec_ts}_{_hls_vid_id[:20]}.ts")
                task.filename = _direct_out_path
                _tt16_cookie = _resolve_cookie(task.url, self._config, _task_cookie_override) or ""
                _dl_method = (
                    "curl_cffi Chrome impersonation"
                    if _CURL_CFFI_AVAILABLE
                    else "direct FFmpeg with reconnect"
                )
                logger.info(
                    "BUG-TT-16: TikTok live using %s (bypassing yt-dlp FFmpegFD) for task %s",
                    _dl_method,
                    task.id,
                )
                # BUG-TT-17 FIX: TikTok HLS URLs are signed (~18-25s TTL).
                # When FFmpeg dies from a 403/404 after token rotation,
                # -reconnect retries the *same expired URL* — always fails.
                # Fix: re-extract a fresh HLS URL after each FFmpeg death,
                # write each segment to a temp file, then binary-append to the
                # main output (MPEG-TS binary concat is spec-valid).
                _MAX_HLS_RETRIES = 20  # ~20 token rotations = long stream
                _tt16_attempt = 0
                _tt16_current_hls = _hls_url
                _tt16_bad_bases: set[str] = set()
                _tt16_bad_hosts: set[str] = set()
                # BUG-TT-STALL2 FIX: track consecutive stalls per CDN base.
                # After _STALL_BASE_THRESHOLD stalls on the same base, add it to
                # bad_bases so _fetch_hls_from_webcast_room_info returns a different
                # quality variant (SD/LD from hls_pull_url_map) instead of retrying
                # the same dead CDN path with a refreshed token indefinitely.
                _tt16_stall_counts: dict[str, int] = {}
                _STALL_BASE_THRESHOLD = 1
                try:
                    while _tt16_attempt <= _MAX_HLS_RETRIES:
                        _seg_path = (
                            _direct_out_path
                            if _tt16_attempt == 0
                            else (_direct_out_path + f".seg{_tt16_attempt}")
                        )
                        try:
                            # BUG-TT-FLV-CURL FIX: FLV URLs (from BUG-TT-24 fallback)
                            # must not be passed to _download_tiktok_live_hls_curl —
                            # it parses the response as M3U8, finds no segments, and
                            # stalls for 60s per attempt, freezing the UI at 556B.
                            # Route FLV to FFmpeg which already handles it via _is_flv_url.
                            _is_flv_current = ".flv" in _tt16_current_hls.lower()
                            if _CURL_CFFI_AVAILABLE and not _is_flv_current:
                                self._download_tiktok_live_hls_curl(
                                    _tt16_current_hls,
                                    _seg_path,
                                    task,
                                    _tt16_cookie,
                                    on_progress,
                                )
                            else:
                                self._download_tiktok_live_direct(
                                    _tt16_current_hls,
                                    _seg_path,
                                    task,
                                    _tt16_cookie,
                                    on_progress,
                                )
                            # FFmpeg exited cleanly — stream ended
                            if _tt16_attempt > 0:
                                # Append segment to main file then delete
                                try:
                                    with open(_direct_out_path, "ab") as _fout, open(_seg_path, "rb") as _fin:
                                        _fout.write(_fin.read())
                                    Path(_seg_path).unlink(missing_ok=True)
                                    logger.info(
                                        "BUG-TT-17: appended segment %d to %s",
                                        _tt16_attempt,
                                        _direct_out_path,
                                    )
                                except OSError as _ap_exc:
                                    logger.warning(
                                        "BUG-TT-17: append seg %d failed: %s",
                                        _tt16_attempt,
                                        _ap_exc,
                                    )
                            # BUG-TT-28 FIX: TikTok CDN ends HLS sessions with
                            # #EXT-X-ENDLIST when the signed URL expires (~9-15 min),
                            # causing FFmpeg to exit cleanly (code 0) even though the
                            # broadcaster is still live. Re-extract to verify.
                            if _tt16_attempt < _MAX_HLS_RETRIES:
                                _tt28_fresh = self._extract_tiktok_live_hls_url(
                                    task.url,
                                    room_id=_room_id_hint,
                                    cookie_override=_task_cookie_override,
                                    username=_username_hint,
                                )
                                if _tt28_fresh:
                                    logger.info(
                                        "BUG-TT-28: FFmpeg exited cleanly but stream"
                                        " still live — re-extracting HLS"
                                        " (attempt %d/%d)",
                                        _tt16_attempt + 1,
                                        _MAX_HLS_RETRIES,
                                    )
                                    _tt16_current_hls = _tt28_fresh[0]
                                    _tt16_attempt += 1
                                    time.sleep(2)
                                    continue
                            _direct_ffmpeg_ok = True
                            break
                        except yt_dlp.utils.DownloadError:
                            raise
                        except RuntimeError as _seg_exc:
                            _seg_size = 0
                            try:
                                _seg_size = Path(_seg_path).stat().st_size
                            except OSError:
                                pass

                            # Append whatever was captured before FFmpeg died
                            # BUG-TT-SMALLSEG FIX: only append segments with real content.
                            # A stall on a new CDN base produces a tiny garbage file (e.g. 556 B).
                            # Appending it makes _main_size non-zero, which bypasses the
                            # BUG-TT-20B/STALL2 retry path that excludes bad CDN bases.
                            # Always clean up the seg file regardless of size.
                            if _tt16_attempt > 0:
                                try:
                                    if _seg_size > 500_000:
                                        with (
                                            open(_direct_out_path, "ab") as _fout,
                                            open(_seg_path, "rb") as _fin,
                                        ):
                                            _fout.write(_fin.read())
                                    Path(_seg_path).unlink(missing_ok=True)
                                except OSError:
                                    pass

                            _main_size = 0
                            try:
                                _main_size = Path(_direct_out_path).stat().st_size
                            except OSError:
                                pass

                            if _main_size > 500_000:
                                # File has real content — stream likely ended or
                                # token expired but enough data was captured
                                if _tt16_attempt >= _MAX_HLS_RETRIES:
                                    logger.info(
                                        "BUG-TT-17: max retries reached, treating %s as completed",
                                        _fmt_bytes(_main_size),
                                    )
                                    _direct_ffmpeg_ok = True
                                    break

                                # Try re-extracting a fresh HLS URL
                                logger.info(
                                    "BUG-TT-17: FFmpeg died after %s recorded "
                                    "(attempt %d/%d) — re-extracting HLS URL",
                                    _fmt_bytes(_main_size),
                                    _tt16_attempt + 1,
                                    _MAX_HLS_RETRIES,
                                )
                                _fresh = self._extract_tiktok_live_hls_url(
                                    task.url,
                                    room_id=_room_id_hint,
                                    cookie_override=_task_cookie_override,
                                    username=_username_hint,
                                )
                                if _fresh:
                                    _tt16_current_hls = _fresh[0]
                                    _tt16_attempt += 1
                                    time.sleep(2)
                                    continue
                                else:
                                    # Can't re-extract — stream likely ended
                                    logger.info(
                                        "BUG-TT-17: HLS re-extract failed — stream ended, %s saved",
                                        _fmt_bytes(_main_size),
                                    )
                                    _direct_ffmpeg_ok = True
                                    break
                            else:
                                # Very small file on first attempt — real failure
                                logger.warning(
                                    "BUG-TT-17: FFmpeg failed with small output (%s) on attempt %d: %s",
                                    _fmt_bytes(_main_size),
                                    _tt16_attempt,
                                    _seg_exc,
                                )
                                if _tt16_attempt == 0:
                                    # BUG-TT-18 FIX: attempt 0 failed with 0B.
                                    # Try re-extracting a fresh HLS URL before
                                    # falling back to yt-dlp.
                                    # BUG-TT-20A FIX: compare base URLs (strip
                                    # query params) — expire/sign always differ
                                    # even on the same CDN path, so the old
                                    # full-URL != check always fired and wasted
                                    # an attempt on an identical 404 path.
                                    # BUG-TT-21 FIX: when re-extract returns the
                                    # same CDN path (_hd 404s consistently), pass
                                    # the bad base as excluded so a lower-quality
                                    # variant (_sd, _ld) on a different CDN path
                                    # can be tried instead.
                                    # BUG-TT-STALL FIX: stall watchdog fires when
                                    # FFmpeg gets no data (not necessarily a CDN
                                    # 404). BUG-TT-25 may return the same CDN
                                    # base with a fresh token that will work.
                                    # Only exclude the base for 404/403 failures,
                                    # not stalls — so BUG-TT-25 URLs aren't
                                    # rejected by the base-exclusion check below.
                                    # BUG-TT-STALL2 FIX: after _STALL_BASE_THRESHOLD
                                    # consecutive stalls on the same CDN base, that
                                    # base is dead — exclude it to force trying an
                                    # alternative quality variant (SD/LD).
                                    _is_stall = "stall watchdog" in str(_seg_exc)
                                    _stall_base0 = _tt16_current_hls.split("?")[0]
                                    if not _is_stall:
                                        _tt16_bad_bases.add(_stall_base0)
                                        _tt16_bad_hosts.add(_urlparse(_tt16_current_hls).netloc)
                                    else:
                                        _tt16_stall_counts[_stall_base0] = (
                                            _tt16_stall_counts.get(_stall_base0, 0) + 1
                                        )
                                        if _tt16_stall_counts[_stall_base0] >= _STALL_BASE_THRESHOLD:
                                            _tt16_bad_bases.add(_stall_base0)
                                            _tt16_bad_hosts.add(_urlparse(_tt16_current_hls).netloc)
                                    _fresh0 = self._extract_tiktok_live_hls_url(
                                        task.url,
                                        _exclude_bases=frozenset(_tt16_bad_bases),
                                        _exclude_hosts=frozenset(_tt16_bad_hosts),
                                        room_id=_room_id_hint,
                                        cookie_override=_task_cookie_override,
                                        username=_username_hint,
                                    )
                                    _base_new = _fresh0[0].split("?")[0] if _fresh0 else ""
                                    if _fresh0 and _base_new not in _tt16_bad_bases:
                                        _tt16_current_hls = _fresh0[0]
                                        try:
                                            Path(_direct_out_path).unlink(missing_ok=True)
                                        except OSError:
                                            pass
                                        _tt16_attempt += 1
                                        time.sleep(2)
                                        continue
                                    raise  # no alternative CDN path available
                                # BUG-TT-20B FIX: subsequent 0B failure — exclude
                                # this base too and try one more CDN path before
                                # falling back to yt-dlp (BUG-TT-22).
                                if _main_size == 0:
                                    _is_stall2 = "stall watchdog" in str(_seg_exc)
                                    _stall_base2 = _tt16_current_hls.split("?")[0]
                                    if not _is_stall2:
                                        _tt16_bad_bases.add(_stall_base2)
                                        _tt16_bad_hosts.add(_urlparse(_tt16_current_hls).netloc)
                                    else:
                                        _tt16_stall_counts[_stall_base2] = (
                                            _tt16_stall_counts.get(_stall_base2, 0) + 1
                                        )
                                        if _tt16_stall_counts[_stall_base2] >= _STALL_BASE_THRESHOLD:
                                            _tt16_bad_bases.add(_stall_base2)
                                            _tt16_bad_hosts.add(_urlparse(_tt16_current_hls).netloc)
                                    _fresh1 = self._extract_tiktok_live_hls_url(
                                        task.url,
                                        _exclude_bases=frozenset(_tt16_bad_bases),
                                        _exclude_hosts=frozenset(_tt16_bad_hosts),
                                        room_id=_room_id_hint,
                                        cookie_override=_task_cookie_override,
                                        username=_username_hint,
                                    )
                                    _base_new2 = _fresh1[0].split("?")[0] if _fresh1 else ""
                                    if _fresh1 and _base_new2 not in _tt16_bad_bases:
                                        _tt16_current_hls = _fresh1[0]
                                        try:
                                            Path(_direct_out_path).unlink(missing_ok=True)
                                        except OSError:
                                            pass
                                        _tt16_attempt += 1
                                        time.sleep(2)
                                        continue
                                    raise
                                _direct_ffmpeg_ok = True
                                break
                        except Exception as _seg_exc:
                            logger.warning(
                                "BUG-TT-17: unexpected error on attempt %d: %s",
                                _tt16_attempt,
                                _seg_exc,
                            )
                            if _tt16_attempt == 0:
                                raise
                            _direct_ffmpeg_ok = True
                            break
                except yt_dlp.utils.DownloadError:
                    raise
                except RuntimeError as _tt16_exc:
                    logger.warning(
                        "BUG-TT-17: direct FFmpeg failed (%s), falling back to yt-dlp",
                        _tt16_exc,
                    )
                except Exception as _tt16_exc:
                    logger.warning(
                        "BUG-TT-17: direct FFmpeg unexpected error (%s), falling back to yt-dlp",
                        _tt16_exc,
                    )
                if _direct_ffmpeg_ok and _tt16_attempt > 0:
                    for _ci in range(1, _tt16_attempt + 2):
                        _seg_clean = Path(_direct_out_path + f".seg{_ci}")
                        try:
                            _seg_clean.unlink(missing_ok=True)
                        except OSError:
                            pass
                    # BUG-TT-23 FIX: _download_tiktok_live_direct overwrites
                    # task.filename with the last segment path (e.g. .seg1).
                    # After that segment is appended+deleted, task.filename points
                    # to a non-existent file → filename resolution falls through to
                    # size scan → picks the largest (old) file in output_dir.
                    # Reset to the main output file which has all segments merged.
                    task.filename = _direct_out_path
            else:
                if _hls_confirmed_ended:
                    logger.debug(
                        "BUG-TT-16: HLS URL extraction confirmed stream ended"
                        " (all fallbacks exhausted) -- falling back to yt-dlp (no BUG-TT-12 retry)"
                    )
                else:
                    logger.debug("BUG-TT-16: HLS URL extraction failed, falling back to yt-dlp")

        if not _direct_ffmpeg_ok:
            # BUG-YTDLP-PROGRESS FIX: yt-dlp's FFmpegFD for live streams may
            # not emit progress hook updates reliably (0 bytes during startup,
            # then sparse). Add a file-size polling thread identical to the
            # direct FFmpeg path so the UI shows "⏺ X MiB đã ghi" instead of
            # silent "0%". Thread reads task.filename (set by progress hook at
            # line 2717-2718 once yt-dlp opens the output file) and polls size.
            _ytdlp_poll_stop = None
            if is_live and on_progress:
                import threading as _th_ytdlp

                _ytdlp_poll_stop = _th_ytdlp.Event()

                def _ytdlp_live_poller(
                    _stop=_ytdlp_poll_stop,
                    _task=task,
                    _cb=on_progress,
                ) -> None:
                    while not _stop.wait(2.0):
                        _f = _task.filename
                        if not _f:
                            continue
                        try:
                            _sz = Path(_f).stat().st_size
                        except OSError:
                            continue
                        if _sz > 0:
                            _task.downloaded_bytes = _sz
                            _task.eta = f"⏺ {_fmt_bytes(_sz)} đã ghi"
                            _cb(_task)

                _th_ytdlp.Thread(target=_ytdlp_live_poller, daemon=True).start()

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

                # BUG-TT-02 FIX: TikTok HLS tokens expire after ~1-2 minutes.
                # When ffmpeg exits with an error on a live stream, re-extract a
                # fresh HLS URL and retry the download exactly once. This handles
                # the case where the user paused/reconnected and the original URL
                # is now stale. Only applies to TikTok live streams (the platform
                # with the shortest-lived HLS tokens); other platforms already
                # handle reconnection internally via yt-dlp's own retry logic.
                _exc_str = str(exc)
                _exc_l = _exc_str.lower()
                # BUG-TT-02 FIX2: also match short links (vt/vm.tiktok.com) --
                # task.url holds the original user-pasted URL which may be a short
                # link even when the stream is a TikTok live.
                _is_tiktok_live_url = _TIKTOK_LIVE_RE.search(task.url) or _TIKTOK_SHORT_RE.search(task.url)
                # BUG-TT-12 FIX: TikTok webcast/room/info API sometimes returns
                # status=4 (not live) even when the stream is active. This is a
                # TikTok API race / CDN cache issue that affects the yt-dlp
                # TikTokLiveIE._call_api path. When "not currently live" or
                # "livestream has ended" occurs at download time (not analyse time,
                # which is already handled by BUG-TT-06 in download_service.py),
                # wait 5s and retry once — the CDN cache usually refreshes within
                # a few seconds.
                # Trigger conditions: TikTok live URL + live task + "not live" error.
                _is_tiktok_api_race = (
                    is_live
                    and _is_tiktok_live_url
                    and (
                        "not currently live" in _exc_l
                        or "channel is not currently live" in _exc_l
                        or "this livestream has ended" in _exc_l
                        or "usernotlive" in _exc_l
                    )
                    and not task.is_cancellation_requested
                    # BUG-TT-HLS-CONFIRMED-ENDED FIX: if HLS extraction already
                    # tried BUG-TT-25 + BUG-TT-26 and both returned None, the
                    # stream is confirmed over — skip the CDN-race retry.
                    and not _hls_confirmed_ended
                    # BUG-TT-12-SCOPE FIX: only retry when detection confirmed a
                    # live room (tiktok_room_id set). Optimistic downloads have no
                    # confirmed live evidence — skip the 35s retry sequence.
                    and bool(task.media_info and task.media_info.tiktok_room_id)
                )
                _is_hls_expired = (
                    is_live
                    and _is_tiktok_live_url
                    and "ffmpeg exited with code" in _exc_l
                    and not task.is_cancellation_requested
                )
                if _is_tiktok_api_race:
                    _bt12_last_exc: "Optional[Exception]" = None
                    for _bt12_delay in (5, 10, 20):
                        logger.info(
                            "BUG-TT-12: TikTok live API returned 'not live' for task %s"
                            " — waiting %ds and retrying (CDN cache race)",
                            task.id,
                            _bt12_delay,
                        )
                        time.sleep(_bt12_delay)
                        try:
                            with yt_dlp.YoutubeDL(opts) as ydl:
                                ydl.download([task.url])
                            _bt12_last_exc = None
                            break
                        except yt_dlp.utils.DownloadError as retry_exc:
                            if task.is_cancellation_requested:
                                raise
                            _bt12_l = str(retry_exc).lower()
                            if (
                                "not currently live" not in _bt12_l
                                and "channel is not currently live" not in _bt12_l
                            ):
                                raise RuntimeError(_friendly_error(str(retry_exc))) from retry_exc
                            _bt12_last_exc = retry_exc
                        except Exception as retry_exc:
                            raise RuntimeError(str(retry_exc)) from retry_exc
                    if _bt12_last_exc is not None:
                        raise RuntimeError(_friendly_error(str(_bt12_last_exc))) from _bt12_last_exc
                elif _is_hls_expired:
                    logger.info(
                        "BUG-TT-02: TikTok live HLS expired for task %s — re-extracting",
                        task.id,
                    )
                    try:
                        _fresh_info = self.extract_info(task.url)
                        _still_live = bool(_fresh_info and _fresh_info.is_live)
                        # BUG-TT-13 FIX: yt-dlp TikTokLiveIE._call_api sends an
                        # unsigned request to webcast.tiktok.com/webcast/room/info
                        # (no X-Bogus/msToken). TikTok returns status=4 for unsigned
                        # requests even when the stream is active, so extract_info
                        # returns is_live=False and BUG-TT-02 gives up prematurely.
                        # Fix: when extract_info says not-live, cross-check via our
                        # custom checker which uses Chrome impersonation and different
                        # endpoints. If checker confirms live, retry download directly
                        # — the HLS playlist expired, not the stream itself.
                        if not _still_live and _is_tiktok_live_url:
                            import re as _re_tt13  # noqa: PLC0415

                            _tt13_m = _re_tt13.compile(
                                r"tiktok\.com/@([A-Za-z0-9_.]+)/live", _re_tt13.I
                            ).search(task.url)
                            if _tt13_m:
                                _tt13_user = _tt13_m.group(1)
                                _tt13_cookie_raw = (
                                    _resolve_cookie(
                                        "https://www.tiktok.com/", self._config, _task_cookie_override
                                    )
                                    or ""
                                )
                                _tt13_cookie_txt, _tt13_is_temp = "", False
                                if _tt13_cookie_raw:
                                    _tt13_cookie_txt, _tt13_is_temp = _prepare_cookie_for_use(
                                        _tt13_cookie_raw
                                    )
                                try:
                                    from utils.tiktok_live_checker import (  # noqa: PLC0415
                                        _check_tiktok_live_with_room_id,
                                    )

                                    _tt13_r = _check_tiktok_live_with_room_id(
                                        _tt13_user,
                                        proxy=self._config.proxy or "",
                                        cookie_file=_tt13_cookie_txt,
                                        share_url=task.url,
                                    )
                                    if _tt13_r:
                                        _still_live = True
                                        logger.info(
                                            "BUG-TT-13: yt-dlp said not-live but"
                                            " checker confirms @%s live"
                                            " — retrying download directly",
                                            _tt13_user,
                                        )
                                except Exception as _tt13_exc:
                                    logger.debug(
                                        "BUG-TT-13: checker failed (%s) — treating as ended",
                                        _tt13_exc,
                                    )
                                finally:
                                    if _tt13_is_temp and _tt13_cookie_txt:
                                        try:
                                            import os as _os13  # noqa: PLC0415

                                            _os13.unlink(_tt13_cookie_txt)
                                        except OSError:
                                            pass
                        if _still_live:
                            if _fresh_info and _fresh_info.is_live:
                                task.media_info = _fresh_info
                            logger.info("BUG-TT-02/13: stream live, retrying download")
                            time.sleep(3)
                            with yt_dlp.YoutubeDL(opts) as ydl:
                                ydl.download([task.url])
                        else:
                            raise RuntimeError(
                                "Livestream đã kết thúc hoặc HLS URL không còn hợp lệ.\n"
                                "Thêm lại link để theo dõi lần phát tiếp theo."
                            ) from exc
                    except yt_dlp.utils.DownloadError as retry_exc:
                        if task.is_cancellation_requested:
                            raise
                        raise RuntimeError(_friendly_error(str(retry_exc))) from retry_exc
                    except RuntimeError:
                        raise
                    except Exception as retry_exc:
                        raise RuntimeError(_friendly_error(str(retry_exc))) from retry_exc
                else:
                    # BUG-TT-10231-DL: 10231 during download — yt-dlp re-runs extract_info
                    # internally in ydl.download(). Use app_info to trigger the Android
                    # mobile API (_extract_aweme_app) which bypasses the web path that
                    # returns 10231. app_name alone has no effect (see BUG-TT-10231).
                    # BUG-TT-CHALLENGE: same Android API fallback for the anti-bot JS
                    # challenge ("Unexpected response from webpage request").
                    _tt_web_blocked = (
                        "status code 10231" in _exc_l or "unexpected response from webpage request" in _exc_l
                    )
                    if _tt_web_blocked and _is_tiktok_vod and not is_live:
                        _10231_dl_ok = False
                        for _fb_app_info in (
                            "/trill/35.1.3/2023501030/1180",
                            "/musical_ly/35.1.3/2023501030/1233",
                            "/aweme/35.1.3/2023501030/1128",
                        ):
                            _fb_opts = dict(opts)
                            _fb_opts["extractor_args"] = {"tiktok": {"app_info": [_fb_app_info]}}
                            try:
                                with yt_dlp.YoutubeDL(_fb_opts) as ydl:
                                    ydl.download([task.url])
                                logger.debug(
                                    "BUG-TT-10231-DL: %s retry succeeded for %s",
                                    _fb_app_info,
                                    task.url,
                                )
                                _10231_dl_ok = True
                                break
                            except Exception as _fb_exc:
                                logger.debug(
                                    "BUG-TT-10231-DL: %s retry failed: %s",
                                    _fb_app_info,
                                    _fb_exc,
                                )
                        if not _10231_dl_ok:
                            raise RuntimeError(_friendly_error(_exc_str)) from exc
                    else:
                        # BUG-TT-SENSITIVE: pool cookie may be stale / wrong account.
                        # Retry with per-platform cookie (no pool override) before failing.
                        _tt_login_req = (
                            "not comfortable for some audiences" in _exc_l or "log in for access" in _exc_l
                        )
                        if _tt_login_req and _is_tiktok_vod and not is_live and _task_cookie_override:
                            _global_cookie = _resolve_cookie(task.url, self._config, None)
                            _global_temp: str | None = None
                            _global_ok = False
                            try:
                                _fb_opts = dict(opts)
                                if _global_cookie:
                                    _gc_usable, _gc_is_temp = _prepare_cookie_for_use(_global_cookie)
                                    _fb_opts["cookiefile"] = _gc_usable
                                    if _gc_is_temp:
                                        _global_temp = _gc_usable
                                else:
                                    _fb_opts.pop("cookiefile", None)
                                logger.debug(
                                    "BUG-TT-SENSITIVE: pool cookie rejected, retrying with"
                                    " per-platform cookie for %s",
                                    task.url,
                                )
                                with yt_dlp.YoutubeDL(_fb_opts) as ydl:
                                    ydl.download([task.url])
                                _global_ok = True
                            except Exception as _sens_exc:
                                logger.debug(
                                    "BUG-TT-SENSITIVE: per-platform cookie retry failed: %s",
                                    _sens_exc,
                                )
                            finally:
                                if _global_temp:
                                    try:
                                        Path(_global_temp).unlink(missing_ok=True)
                                    except OSError:
                                        pass
                            if not _global_ok:
                                raise RuntimeError(_friendly_error(_exc_str)) from exc
                        else:
                            raise RuntimeError(_friendly_error(_exc_str)) from exc
            except Exception as exc:
                if task.is_cancellation_requested:
                    raise yt_dlp.utils.DownloadError("Cancelled by user") from exc
                raise RuntimeError(str(exc)) from exc
            finally:
                if _ytdlp_poll_stop:
                    _ytdlp_poll_stop.set()

        # ── Resolve final filename ─────────────────────────────────────────
        # BUG-TT-EFF: if yt-dlp selected a vcodec=none stream for a non-audio
        # output, the file is an mp4 container with audio only. Raise a clear
        # error so the user knows this video type is unavailable via yt-dlp
        # (TikTok "template effect" videos have no video track in the API).
        # Guard: only for TikTok VOD, non-audio output, non-live.
        if (
            _is_tiktok_vod
            and not is_live
            and not _is_audio_output
            and _selected_vcodec
            and _selected_vcodec[0].lower() in ("none", "")
        ):
            # BUG-TT-PROD FIX: TikTok product/shopping link videos expose
            # format_id="audio" with vcodec=none and acodec=aac (ext=m4a) in
            # yt-dlp metadata, but the actual downloaded file DOES contain a
            # video track — the metadata is mislabeled by TikTok API.
            # Same as BUG-TT-SHOP-2 (acodec=mp3) but with acodec=aac/ext=m4a,
            # so best[format_id=audio][ext=mp4] doesn't catch it.
            # Probe the actual output with FFprobe before deleting. If the file
            # has a video codec, the metadata was wrong — skip the error.
            # Only delete+raise when FFprobe confirms no video (or unavailable).
            _broken = _final_filepath[0] if _final_filepath else (task.filename or "")
            _probe_confirmed_no_video = True  # safe default: trust metadata
            if _broken and Path(_broken).is_file():
                from app.services.ffmpeg_convert_service import probe_media_info as _probe_mi

                _probe_result = _probe_mi(Path(_broken))
                if _probe_result is not None and _probe_result.video_codec:
                    logger.debug(
                        "BUG-TT-PROD: vcodec=none in yt-dlp metadata but FFprobe "
                        "found video_codec=%r in %s — product link video, skipping error",
                        _probe_result.video_codec,
                        _broken,
                    )
                    _probe_confirmed_no_video = False
            if _probe_confirmed_no_video:
                # Delete the audio-only file before retry or final error.
                if _broken:
                    try:
                        Path(_broken).unlink(missing_ok=True)
                        logger.debug("BUG-TT-EFF: deleted audio-only output %s", _broken)
                    except OSError:
                        pass

                # BUG-TT-SHOP-3 FIX: TikTok product/showcase videos sometimes only
                # expose video formats to the musical_ly mobile client (different API
                # aid param). Retry once with app_name=musical_ly + video-only format
                # chain before giving up. Silent on failure — raises below if no video.
                _shop3_got_video = False
                _final_filepath.clear()
                _selected_vcodec.clear()
                _ml_opts = dict(opts)
                _ml_opts["extractor_args"] = {"tiktok": {"app_name": ["musical_ly"]}}
                _ml_opts["format"] = (
                    "best[format_id^=h264]/download/bestvideo*+bestaudio*/bestvideo*/best[vcodec!=none]"
                )
                try:
                    with yt_dlp.YoutubeDL(_ml_opts) as ydl:
                        ydl.download([task.url])
                    if _selected_vcodec and _selected_vcodec[0].lower() not in ("none", ""):
                        _shop3_got_video = True
                        logger.debug(
                            "BUG-TT-SHOP-3: musical_ly retry succeeded, vcodec=%r",
                            _selected_vcodec[0],
                        )
                    elif _final_filepath and Path(_final_filepath[0]).is_file():
                        from app.services.ffmpeg_convert_service import (  # noqa: PLC0415
                            probe_media_info as _probe_shop3,
                        )

                        _r3 = _probe_shop3(Path(_final_filepath[0]))
                        if _r3 and _r3.video_codec:
                            _shop3_got_video = True
                            logger.debug(
                                "BUG-TT-SHOP-3: musical_ly retry — FFprobe found video=%r",
                                _r3.video_codec,
                            )
                except Exception as _shop3_exc:
                    logger.debug("BUG-TT-SHOP-3: musical_ly retry failed: %s", _shop3_exc)

                if not _shop3_got_video:
                    _retry_broken = _final_filepath[0] if _final_filepath else ""
                    if _retry_broken:
                        try:
                            Path(_retry_broken).unlink(missing_ok=True)
                        except OSError:
                            pass
                    raise RuntimeError(
                        "Video này chỉ có âm thanh — không có video track.\n"
                        'TikTok product/showcase và "template effect" / AR effect videos'
                        " không cung cấp video track qua API (chỉ expose audio stream).\n"
                        "Cách tải: mở video trên TikTok app → chia sẻ → Lưu video."
                    )
                # BUG-TT-SHOP-3: musical_ly retry succeeded — fall through to filename resolution
        if _final_filepath:
            # Best case: pp_hook told us exactly where the merged file is
            p = Path(_final_filepath[0])
            if p.is_file() and p.stat().st_size > 1_000:
                task.filename = str(p)
                logger.info("Filename from pp_hook: %s", task.filename)
            else:
                task.filename = _final_filepath[0]  # keep path even if verify fails
                logger.warning("pp_hook path not found on disk: %s", task.filename)
        elif task.filename and Path(task.filename).is_file():
            # Progress hook captured it and it still exists (single-format, no merge)
            logger.debug("Keeping progress-hook filename: %s", task.filename)
        else:
            # Last resort: largest VIDEO file in output_dir (never pick thumbnails)
            try:
                candidates = [
                    f
                    for f in output_dir.iterdir()
                    if f.suffix.lower() in _MEDIA_EXTS
                    and not f.name.endswith(".part")
                    and not f.name.endswith(".ytdl")
                    and f.stat().st_size > 50_000  # >50KB — skip thumbnails
                ]
                if candidates:
                    task.filename = str(max(candidates, key=lambda f: f.stat().st_size))
                    logger.info("Filename via size scan: %s", task.filename)
                else:
                    logger.warning("No media file found in %s", output_dir)
            except Exception as e:
                logger.warning("Size scan failed: %s", e)

        # BUG-BW FIX: Move live recording from ASCII temp dir to output_dir.
        # On Windows, outtmpl was redirected to tempdir to avoid Unicode named
        # pipe paths.  Now that ffmpeg has finished writing, move the .ts file
        # to where the user expects it (output_dir).
        import sys as _sys_mv

        if is_live and _sys_mv.platform == "win32" and task.filename:
            _src = Path(task.filename)
            if _src.is_file() and _src.parent.resolve() != output_dir.resolve():
                try:
                    import shutil as _shutil

                    _dst = output_dir / _src.name
                    _shutil.move(str(_src), str(_dst))
                    task.filename = str(_dst)
                    logger.info("Live recording moved to output dir: %s", task.filename)
                except Exception as _mv_exc:
                    logger.warning("Failed to move live recording to output dir: %s", _mv_exc)

        # BUG-LN-WIN FIX: outtmpl for Windows live uses only timestamp+id to
        # avoid Unicode named-pipe crash (BUG-BW2).  After the file is in
        # output_dir, rename it to a full descriptive name using media_info.
        # Non-Windows and non-live paths are unaffected.
        if is_live and _sys_mv.platform == "win32" and task.filename:
            _cur = Path(task.filename)
            if _cur.is_file() and _cur.parent.resolve() == output_dir.resolve():
                try:
                    from utils.naming import build_filename as _build_fn

                    _mi = task.media_info
                    _uploader = (_mi.uploader if _mi and _mi.uploader else "") or ""
                    if not _uploader:
                        import re as _re_upl  # noqa: PLC0415

                        _upl_url = (_mi.url if _mi and _mi.url else "") or task.url or ""
                        _upl_m = _re_upl.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", _upl_url, _re_upl.I)
                        _uploader = _upl_m.group(1) if _upl_m else "Unknown"
                    _title = _mi.title if _mi and _mi.title else ""
                    _vid_id = (_mi.video_id if _mi and _mi.video_id else _live_vid_id)[:20]
                    _new_name = _build_fn(
                        uploader=_uploader,
                        date_label=f"[LIVE] {rec_ts}",
                        title=_title,
                        video_id=_vid_id,
                        ext="ts",
                    )
                    _new_path = output_dir / _new_name
                    if _new_path != _cur:
                        _cur.rename(_new_path)
                        task.filename = str(_new_path)
                        logger.info("Live recording renamed: %s", task.filename)
                except Exception as _rn_exc:
                    logger.warning("Failed to rename live recording: %s", _rn_exc)

        # Always clean up the decrypted temp cookie file, even on error
        if _cookie_temp_dl:
            try:
                Path(_cookie_temp_dl).unlink(missing_ok=True)
                logger.debug("Cleaned up temp cookie file: %s", _cookie_temp_dl)
            except Exception:
                pass

    # ── TikTok live direct-FFmpeg helpers ─────────────────────────────────
    # BUG-TT-16 FIX: yt-dlp's downloader selection hard-codes FFmpegFD for
    # all is_live=True streams regardless of hls_prefer_native or format
    # selector (see yt_dlp/downloader/__init__.py:
    #   if info_dict.get('is_live'): return FFmpegFD
    # This line is checked BEFORE hls_prefer_native and BEFORE m3u8_native
    # protocol preference, so neither setting has any effect for live streams.
    # FFmpegFD runs FFmpeg as a long-running subprocess reading from the HLS
    # playlist. TikTok CDN rotates HLS tokens every ~18-25s; when a token
    # expires mid-download, FFmpeg receives HTTP 403/404 and exits with
    # code 3419392776 (STATUS_PIPE_NOT_AVAILABLE / generic crash on Windows).
    # FFmpegFD has no reconnect logic — it just dies.
    #
    # Fix: for TikTok live, extract the HLS playlist URL directly from
    # yt-dlp's info_dict (skip_download=True), then call FFmpeg ourselves
    # with -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10.
    # These flags tell FFmpeg to re-request the playlist and resume after
    # any HTTP error, which covers CDN token rotation transparently.
    # Progress is polled by watching the output file size.

    def _extract_tiktok_live_hls_url(
        self,
        task_url: str,
        _exclude_bases: "frozenset[str] | None" = None,
        _exclude_hosts: "frozenset[str] | None" = None,
        room_id: str = "",
        cookie_override: "str | None" = None,
        username: str = "",
    ) -> "tuple[str, str, str, str] | None":
        """Extract (hls_url, video_id, uploader, title) from TikTok live via yt-dlp skip_download.

        Returns None if extraction fails or no suitable HLS format found.
        The returned hls_url is the best m3u8 URL from the format list.
        _exclude_bases: base URLs (path without query params) to skip — used to
        avoid CDN nodes that returned 404 on a previous attempt.
        """
        _cookie_path = _resolve_cookie(task_url, self._config, cookie_override)
        opts_ei: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
        }
        if _CURL_CFFI_AVAILABLE:
            opts_ei["impersonate"] = _IMPERSONATE_TARGET
        if self._config.proxy:
            opts_ei["proxy"] = self._config.proxy
        _ffmpeg_dir = get_ffmpeg_path()
        if _ffmpeg_dir:
            opts_ei["ffmpeg_location"] = _ffmpeg_dir
        _cookie_temp: str | None = None
        if _cookie_path:
            _usable, _is_temp = _prepare_cookie_for_use(_cookie_path)
            opts_ei["cookiefile"] = _usable
            if _is_temp:
                _cookie_temp = _usable
        _TT_RL.acquire()
        try:
            with yt_dlp.YoutubeDL(opts_ei) as ydl:
                info = ydl.extract_info(task_url, download=False)
        except Exception as exc:
            exc_str = str(exc)
            # BUG-TT-25/26/30 FIX: yt-dlp TikTokLiveIE calls room/info without
            # signing (no X-Bogus/msToken) -- TikTok returns status=4 even for
            # active streams. Try our fallbacks when yt-dlp says "not currently live".
            _is_not_live_err = (
                "not currently live" in exc_str.lower() or "channel is not currently live" in exc_str.lower()
            )
            if _is_not_live_err:
                import re as _re_tt25  # noqa: PLC0415

                _m25 = _re_tt25.search(r"tiktok\.com/@([A-Za-z0-9_.]+)/live", task_url, _re_tt25.I)
                _u25 = (_m25.group(1) if _m25 else None) or username
                if _u25:
                    from utils.tiktok_live_checker import (  # noqa: PLC0415
                        _fetch_hls_from_live_page,
                        _fetch_hls_from_webcast_room_info,
                        _verify_room_alive,
                    )

                    if room_id:
                        # BUG-TT-25: signed room/info call (needs room_id)
                        _c25_raw = _resolve_cookie(task_url, self._config, cookie_override) or ""
                        _c25_txt, _c25_is_temp = "", False
                        if _c25_raw:
                            _c25_txt, _c25_is_temp = _prepare_cookie_for_use(_c25_raw)
                        _direct25 = _fetch_hls_from_webcast_room_info(
                            room_id,
                            _u25,
                            proxy=self._config.proxy or "",
                            cookie_file=_c25_txt,
                            exclude_bases=_exclude_bases or frozenset(),
                        )
                        if _c25_is_temp:
                            try:
                                Path(_c25_txt).unlink(missing_ok=True)
                            except OSError:
                                pass
                        if _direct25:
                            _hls25, _rid25 = _direct25
                            logger.info(
                                "BUG-TT-25: room/info direct HLS URL for %s"
                                " (bypassed yt-dlp unsigned room/info call)",
                                task_url[:60],
                            )
                            return _hls25, _rid25, _u25, ""
                    # BUG-TT-26 FIX: live page scrape only needs username — try
                    # even when room_id is empty (e.g. analyse returned 429).
                    _c26_raw = _resolve_cookie(task_url, self._config, cookie_override) or ""
                    _c26_txt, _c26_is_temp = "", False
                    if _c26_raw:
                        _c26_txt, _c26_is_temp = _prepare_cookie_for_use(_c26_raw)
                    try:
                        _direct26 = _fetch_hls_from_live_page(
                            _u25,
                            proxy=self._config.proxy or "",
                            cookie_file=_c26_txt,
                        )
                    finally:
                        if _c26_is_temp:
                            try:
                                Path(_c26_txt).unlink(missing_ok=True)
                            except OSError:
                                pass
                    if _direct26:
                        _hls26, _rid26 = _direct26
                        logger.info(
                            "BUG-TT-26: live page SIGI_STATE HLS URL for %s"
                            " (webcast API bypassed via page scrape)",
                            task_url[:60],
                        )
                        return _hls26, _rid26, _u25, ""
                    # BUG-TT-29 FIX: retry with cookie rotation if room_id known.
                    # Rotates pool → global-tiktok → anon → pool → global-tiktok
                    # (consecutive duplicates collapsed). Backoff covers bot-detection
                    # cooldowns that 3×5s could not recover from.
                    if room_id:
                        _proxy29 = self._config.proxy or ""
                        _sources29 = _tt29_cookie_sources(task_url, self._config, cookie_override)
                        _backoff29 = (8, 15, 25, 40, 60)
                        for _retry29, (_r29_raw, _r29_label) in enumerate(_sources29):
                            if _retry29 == 0:
                                logger.debug(
                                    "BUG-TT-29: room %s attempt 1/%d (cookie=%s)",
                                    room_id,
                                    len(_sources29),
                                    _r29_label,
                                )
                            else:
                                _sleep29 = _backoff29[min(_retry29 - 1, len(_backoff29) - 1)]
                                logger.debug(
                                    "BUG-TT-29: room %s attempt %d/%d (cookie=%s) — retrying in %ds",
                                    room_id,
                                    _retry29 + 1,
                                    len(_sources29),
                                    _r29_label,
                                    _sleep29,
                                )
                                time.sleep(_sleep29)
                            _r29_txt, _r29_temp = "", False
                            if _r29_raw:
                                _r29_txt, _r29_temp = _prepare_cookie_for_use(_r29_raw)
                            try:
                                _r29a = _fetch_hls_from_webcast_room_info(
                                    room_id,
                                    _u25,
                                    proxy=_proxy29,
                                    cookie_file=_r29_txt,
                                    exclude_bases=_exclude_bases or frozenset(),
                                )
                                if _r29a:
                                    logger.info(
                                        "BUG-TT-29: room/info HLS URL found on retry %d for %s",
                                        _retry29 + 1,
                                        task_url[:60],
                                    )
                                    return _r29a[0], _r29a[1], _u25, ""
                                _r29b = _fetch_hls_from_live_page(_u25, proxy=_proxy29, cookie_file=_r29_txt)
                                if _r29b:
                                    logger.info(
                                        "BUG-TT-29: live page HLS URL found on retry %d for %s",
                                        _retry29 + 1,
                                        task_url[:60],
                                    )
                                    return _r29b[0], _r29b[1], _u25, ""
                            finally:
                                if _r29_temp:
                                    try:
                                        Path(_r29_txt).unlink(missing_ok=True)
                                    except OSError:
                                        pass
                        # Final check: only return () if check_alive also says dead.
                        _final29 = _verify_room_alive(room_id, _u25, proxy=_proxy29, cookie_file="")
                        if not _final29:
                            logger.debug(
                                "BUG-TT-29: check_alive confirms room %s ended -- confirmed ended",
                                room_id,
                            )
                            return ()
                        logger.debug(
                            "BUG-TT-29: room %s still alive after retries -- letting yt-dlp try",
                            room_id,
                        )
                        return None
            logger.debug("BUG-TT-16: HLS extract failed: %s", exc)
            return None
        finally:
            if _cookie_temp:
                try:
                    Path(_cookie_temp).unlink(missing_ok=True)
                except OSError:
                    pass
        if not info:
            return None
        formats = info.get("formats") or []
        video_id = info.get("id") or ""
        # BUG-TT-18 FIX: sort m3u8 formats by quality (height/tbr DESC) before
        # selecting. yt-dlp returns formats lowest-first; the first m3u8_native
        # is typically _ld (low definition) whose CDN node may return 404 while
        # the _hd stream works. Prefer highest quality to avoid this.
        _m3u8_fmts = [
            f
            for f in formats
            if f.get("protocol") in ("m3u8_native", "m3u8") and f.get("url", "").startswith("http")
        ]
        _m3u8_fmts.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
        _excl = _exclude_bases or frozenset()
        _excl_h = _exclude_hosts or frozenset()
        hls_url = ""
        for _f in _m3u8_fmts:
            _u = _f["url"]
            if _u.split("?")[0] not in _excl and _urlparse(_u).netloc not in _excl_h:
                hls_url = _u
                break
        if not hls_url and _m3u8_fmts and (_excl or _excl_h):
            # BUG-TT-24 FIX: all HLS CDN paths excluded (persistent 404).
            # FLV CDN infra (pull-flv-*) is separate from HLS (pull-hls-*),
            # so try HTTP-FLV before falling back to an already-excluded HLS path.
            _flv_fmts = [
                f
                for f in formats
                if f.get("url", "").startswith("http")
                and (f.get("ext") == "flv" or ".flv" in f.get("url", ""))
                and f.get("url", "").split("?")[0] not in _excl
                and _urlparse(f.get("url", "")).netloc not in _excl_h
            ]
            if _flv_fmts:
                _flv_fmts.sort(
                    key=lambda f: (f.get("height") or 0, f.get("tbr") or 0),
                    reverse=True,
                )
                hls_url = _flv_fmts[0]["url"]
                logger.debug(
                    "BUG-TT-24: HLS CDN exhausted — using FLV fallback for %s",
                    task_url[:60],
                )
        if not hls_url and _m3u8_fmts:
            # All candidates excluded — last resort: try best HLS anyway
            hls_url = _m3u8_fmts[0]["url"]
        if not hls_url:
            # Fallback: any format with an http(s) url that looks like HLS
            for fmt in formats:
                u = fmt.get("url", "")
                if ".m3u8" in u and u.startswith("http"):
                    if u.split("?")[0] not in _excl and _urlparse(u).netloc not in _excl_h:
                        hls_url = u
                        break
            if not hls_url:
                for fmt in formats:
                    u = fmt.get("url", "")
                    if ".m3u8" in u and u.startswith("http"):
                        hls_url = u
                        break
        if not hls_url:
            logger.debug("BUG-TT-16: no HLS URL found in formats (count=%d)", len(formats))
            return None
        uploader = info.get("uploader") or info.get("uploader_id") or info.get("channel") or ""
        title = info.get("title") or ""
        logger.debug("BUG-TT-16: extracted HLS URL for %s (id=%s)", task_url[:60], video_id)
        return hls_url, video_id, uploader, title

    def _download_tiktok_live_direct(
        self,
        hls_url: str,
        out_path: str,
        task: DownloadTask,
        cookie_path: str,
        on_progress: "Optional[Callable[[DownloadTask], None]]",
    ) -> None:
        """Download TikTok live HLS to out_path using FFmpeg with reconnect flags.

        BUG-TT-16: bypasses yt-dlp's forced FFmpegFD (no reconnect) by calling
        FFmpeg directly with -reconnect flags that handle CDN token rotation.

        Raises RuntimeError on failure. Raises yt_dlp.utils.DownloadError on cancel.
        """
        import subprocess
        import sys as _sys_dl

        _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        ffmpeg_dir = get_ffmpeg_path()
        if ffmpeg_dir:
            ffmpeg_bin = str(Path(ffmpeg_dir) / ("ffmpeg.exe" if _sys_dl.platform == "win32" else "ffmpeg"))
            if not Path(ffmpeg_bin).is_file():
                ffmpeg_bin = "ffmpeg"
        else:
            ffmpeg_bin = "ffmpeg"

        _cookie_temp_direct: str | None = None
        _ffmpeg_cookie_hdr = ""
        if cookie_path:
            _usable, _is_temp = _prepare_cookie_for_use(cookie_path)
            if _is_temp:
                _cookie_temp_direct = _usable
            # BUG-TT-20C FIX: TikTok's stage CDN requires session cookies in
            # HTTP request headers even when the URL is signed (expire+sign).
            # Parse the decrypted cookie file and pass via -headers to FFmpeg.
            _ffmpeg_cookie_hdr = _build_ffmpeg_cookie_header(_usable)

        import threading
        from collections import deque

        # BUG-FLV-PERSIST FIX: FLV streams are continuous HTTP connections —
        # HLS-specific options (-http_persistent, -reconnect_at_eof,
        # -reconnect_on_http_error, -reconnect_max_retries) are invalid for
        # FLV inputs and cause "Option http_persistent not found" → exit code
        # 2880417800. Detect FLV and skip those options.
        _is_flv_url = ".flv" in hls_url.lower()

        cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "10",
        ]
        if not _is_flv_url:
            cmd += [
                "-reconnect_on_http_error",
                "403,404,503",
                "-reconnect_at_eof",
                "1",
                "-reconnect_max_retries",
                "10",
                "-http_persistent",
                "0",
            ]
        cmd += [
            "-user_agent",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        ]
        if _ffmpeg_cookie_hdr:
            cmd += [
                "-headers",
                f"Cookie: {_ffmpeg_cookie_hdr}\r\nReferer: https://www.tiktok.com/\r\n",
            ]
        # -use_wallclock_as_timestamps: timestamp each received packet using
        # actual receive time instead of the stream's encoded wall-clock PTS.
        # TikTok HLS segments carry absolute stream-position timestamps (e.g.
        # 2700s for a stream that started 45 min ago), so without this flag
        # the .ts file reports ~49 min duration even for a 4-min recording.
        cmd += [
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            hls_url,
            "-c",
            "copy",
            "-f",
            "mpegts",
            "-y",
            out_path,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError as err:
            raise RuntimeError("FFmpeg không tìm thấy. Kiểm tra cài đặt FFmpeg.") from err
        finally:
            if _cookie_temp_direct:
                try:
                    Path(_cookie_temp_direct).unlink(missing_ok=True)
                except OSError:
                    pass

        # Real-time stderr reader: kills FFmpeg immediately on 404/403 instead
        # of waiting the full reconnect_delay_max (10s) timeout per failure.
        _stderr_lines: deque[str] = deque(maxlen=20)
        _kill_on_http_err = threading.Event()

        def _read_stderr(pipe: "Any", lines: "deque[str]", ev: threading.Event) -> None:
            try:
                for raw in pipe:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    lines.append(line)
                    if "404 Not Found" in line or "403 Forbidden" in line:
                        ev.set()
            except Exception:
                pass

        _stderr_thread = threading.Thread(
            target=_read_stderr,
            args=(proc.stderr, _stderr_lines, _kill_on_http_err),
            daemon=True,
        )
        _stderr_thread.start()

        task.status = DownloadStatus.DOWNLOADING
        task.filename = out_path
        _last_size = 0
        _stall_seconds = 0
        _STALL_LIMIT_S = 20  # seconds without new data → stream ended

        try:
            while proc.poll() is None:
                # Cancel check
                if task.is_cancellation_requested:
                    proc.kill()
                    raise yt_dlp.utils.DownloadError("Cancelled by user")
                task.wait_if_paused()

                # Early kill on 404/403 — saves ~10s per CDN failure
                if _kill_on_http_err.is_set() and proc.poll() is None:
                    proc.kill()

                # Progress via file size
                try:
                    cur_size = Path(out_path).stat().st_size
                except OSError:
                    cur_size = 0

                elapsed = task.elapsed
                if cur_size > _last_size:
                    task.downloaded_bytes = cur_size
                    task.eta = (
                        f"⏺ {_fmt_bytes(cur_size)} | {elapsed}"
                        if elapsed
                        else f"⏺ {_fmt_bytes(cur_size)} đã ghi"
                    )
                    _last_size = cur_size
                    _stall_seconds = 0
                else:
                    _stall_seconds += 1
                    if elapsed:
                        task.eta = f"⏺ {elapsed}"
                    if _stall_seconds >= _STALL_LIMIT_S:
                        proc.kill()
                        raise RuntimeError(
                            "FFmpeg stall watchdog: không có dữ liệu trong 120s — stream có thể đã kết thúc."
                        )

                if on_progress:
                    on_progress(task)
                time.sleep(1)
        except Exception:
            if proc.poll() is None:
                proc.kill()
            raise

        _stderr_thread.join(timeout=2)
        ret = proc.returncode
        if ret != 0:
            err_msg = "\n".join(_stderr_lines)[-300:]
            raise RuntimeError(f"FFmpeg exited with code {ret}.\n{err_msg or 'Không có thông tin lỗi.'}")

        # Mark progress done
        task.progress = 100.0
        if on_progress:
            on_progress(task)

    def _download_tiktok_live_hls_curl(
        self,
        hls_url: str,
        out_path: str,
        task: DownloadTask,
        cookie_path: str,
        on_progress: "Optional[Callable[[DownloadTask], None]]",
    ) -> None:
        """Download TikTok live HLS using curl_cffi Chrome TLS impersonation.

        BUG-TT-CURLHLS FIX: TikTok's CDN (pull-hls-*.tiktokcdn.com) silently
        blocks FFmpeg's OpenSSL TLS fingerprint — connection hangs with 0 bytes.
        curl_cffi with Chrome impersonation bypasses this.  Primary downloader
        for TikTok live; _download_tiktok_live_direct (FFmpeg) is the fallback.

        Raises RuntimeError with patterns that match the existing _tt16 retry
        logic — "stall watchdog" for no-data, "404 Not Found"/"403 Forbidden"
        for expired URLs — so the caller re-extracts without any code changes.
        """
        from urllib.parse import urljoin

        from utils.tiktok_live_checker import _get_impersonate_session, _load_cookie_jar  # noqa: PLC0415

        _STALL_TIMEOUT_S = 20
        _POLL_INTERVAL_S = 2
        _SEG_TIMEOUT_S = 20

        jar = None
        _cookie_temp_curl: "str | None" = None
        if cookie_path:
            _usable_curl, _is_temp_curl = _prepare_cookie_for_use(cookie_path)
            if _is_temp_curl:
                _cookie_temp_curl = _usable_curl
            jar = _load_cookie_jar(_usable_curl)

        proxies = None
        if self._config.proxy:
            proxies = {"http": self._config.proxy, "https": self._config.proxy}

        session = _get_impersonate_session(jar)
        _curl_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
        }

        seen_segs: "set[str]" = set()
        last_new_seg_ts = time.time()
        total_bytes = 0

        task.status = DownloadStatus.DOWNLOADING
        task.filename = out_path

        try:
            with open(out_path, "wb") as _curl_f:
                while True:
                    if task.is_cancellation_requested:
                        raise yt_dlp.utils.DownloadError("Cancelled by user")
                    task.wait_if_paused()

                    if time.time() - last_new_seg_ts > _STALL_TIMEOUT_S:
                        raise RuntimeError(
                            f"stall watchdog: curl_cffi HLS không có segment mới trong "
                            f"{_STALL_TIMEOUT_S}s — stream có thể đã kết thúc."
                        )

                    try:
                        _m3u8_resp = session.get(
                            hls_url,
                            headers=_curl_headers,
                            proxies=proxies,
                            timeout=_SEG_TIMEOUT_S,
                        )
                    except Exception as _curl_e:
                        raise RuntimeError(f"HLS playlist request failed: {_curl_e}") from _curl_e

                    if _m3u8_resp.status_code == 404:
                        raise RuntimeError("404 Not Found: HLS playlist expired or unavailable")
                    if _m3u8_resp.status_code == 403:
                        raise RuntimeError("403 Forbidden: HLS playlist access denied")
                    if _m3u8_resp.status_code != 200:
                        time.sleep(_POLL_INTERVAL_S)
                        continue

                    _m3u8_text = _m3u8_resp.text
                    _stream_ended = "#EXT-X-ENDLIST" in _m3u8_text

                    _base_url = hls_url.rsplit("/", 1)[0] + "/"
                    for _seg_line in _m3u8_text.splitlines():
                        _seg_line = _seg_line.strip()
                        if not _seg_line or _seg_line.startswith("#"):
                            continue
                        _seg_url = (
                            _seg_line if _seg_line.startswith("http") else urljoin(_base_url, _seg_line)
                        )
                        # Dedup by base path — TikTok signs each segment URL
                        # per-refresh, so same segment returns different ?expire/sign.
                        _seg_key = _seg_url.split("?")[0]
                        if _seg_key in seen_segs:
                            continue
                        seen_segs.add(_seg_key)

                        try:
                            _seg_resp = session.get(
                                _seg_url,
                                headers=_curl_headers,
                                proxies=proxies,
                                timeout=_SEG_TIMEOUT_S,
                            )
                        except Exception:
                            continue

                        if _seg_resp.status_code == 200:
                            _seg_data = _seg_resp.content
                            _curl_f.write(_seg_data)
                            _curl_f.flush()
                            total_bytes += len(_seg_data)
                            last_new_seg_ts = time.time()

                            task.downloaded_bytes = total_bytes
                            _elapsed_curl = task.elapsed
                            task.eta = (
                                f"⏺ {_fmt_bytes(total_bytes)} | {_elapsed_curl}"
                                if _elapsed_curl
                                else f"⏺ {_fmt_bytes(total_bytes)} đã ghi"
                            )
                            if on_progress:
                                on_progress(task)

                    if _stream_ended:
                        break

                    time.sleep(_POLL_INTERVAL_S)
        finally:
            if _cookie_temp_curl:
                try:
                    Path(_cookie_temp_curl).unlink(missing_ok=True)
                except OSError:
                    pass

        task.progress = 100.0
        if on_progress:
            on_progress(task)

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
                task.total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if task.total_bytes > 0:
                    task.progress = min(99.0, task.downloaded_bytes / task.total_bytes * 100)
                speed = d.get("speed")
                if speed:
                    task.speed = _fmt_speed(speed)
                eta = d.get("eta")
                if is_live:
                    # FIX-1: For livestreams, always show elapsed recording time
                    # instead of yt-dlp's eta (which is meaningless for open-ended
                    # streams). Format: "⏺ X MiB | MM:SS" or fallbacks.
                    elapsed = task.elapsed
                    if task.downloaded_bytes > 0 and elapsed:
                        task.eta = f"⏺ {_fmt_bytes(task.downloaded_bytes)} | {elapsed}"
                    elif task.downloaded_bytes > 0:
                        task.eta = f"⏺ {_fmt_bytes(task.downloaded_bytes)} đã ghi"
                    elif elapsed:
                        task.eta = f"⏺ {elapsed}"
                elif eta is not None:
                    task.eta = _fmt_eta(eta)
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
                    key = tok[1:]  # short flag, e.g. -x → "x"
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
    if speed >= 1024**2:
        return f"{speed / 1024**2:.1f} MiB/s"
    if speed >= 1024:
        return f"{speed / 1024:.0f} KiB/s"
    return f"{speed:.0f} B/s"


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count used for live recording progress display."""
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GiB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n} B"


def _fmt_eta(eta: int | float) -> str:
    s = int(eta)
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
