"""
infrastructure/downloader/yt_dlp_engine.py
Thin wrapper around yt-dlp: metadata extraction + download execution.
"""
from __future__ import annotations

import logging
import re
import shlex
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

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
    _IMPERSONATE_STRING = None
except Exception as _curl_load_err:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "curl_cffi/impersonate unavailable (%s) — "
        "TLS impersonation disabled; Kuaishou and TikTok live may fail",
        _curl_load_err,
    )
    _CURL_CFFI_AVAILABLE = False
    _IMPERSONATE_TARGET = None
    _IMPERSONATE_STRING = None

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
    ("kuaishou.com",  "kuaishou"),
    ("kwai.com",      "kuaishou"),
    ("v.kuaishou.com","kuaishou"),
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
_TIKTOK_VOD_RE   = re.compile(r"tiktok\.com/@[^/]+/video/\d+", re.I)
_TIKTOK_SHORT_RE = re.compile(r"(?:vt|vm)\.tiktok\.com/", re.I)
# BUG-TT-06 FIX: also match m.tiktok.com/share/live/<room_id> — the mobile
# share URL form that yt-dlp accepts directly without a profile-page scrape.
_TIKTOK_LIVE_RE  = re.compile(
    r"(?:tiktok\.com/@[^/]+/live|m\.tiktok\.com/share/live/\d+)", re.I
)

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
    if ("rate" in msg_l and ("limit" in msg_l or "429" in msg_l or "too many" in msg_l)
            or "429" in msg_l or "too many requests" in msg_l):
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

# DEF-015: module-level constant — avoids re-allocating on every _apply_extra_args() call.
# Allowlist for user-supplied extra yt-dlp args (CWE-78: OS Command Injection guard).
# Options such as --exec, --exec-before-download, --postprocessor-args are intentionally
# excluded — they allow arbitrary command execution from user-supplied config.
# NOTE: "no_check_certificates" intentionally excluded —
# disabling TLS verification exposes all downloads to MITM attacks.
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
            impersonate=_IMPERSONATE_STRING,
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
            impersonate=_IMPERSONATE_STRING,
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
    "merging formats", "destination:", "requested format",
    "ffmpeg", "format_id", "vcodec", "acodec", "sorted",
    "selected", "tiktok", "downloading", "fallback", "not available",
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
            "socket_timeout": 20,   # DEF-007: prevent hang on stalled server
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
                    "ssl routines",                       # BUG-CC: TLS fingerprint rejection (Kuaishou)
                    "tls connect error",                  # BUG-CC: curl TLS failure
                    "curl: (35)",                         # BUG-CC: curl SSL connect error code
                    "is not available",                   # BUG-CD: impersonate target missing in EXE
                    "not currently live",                 # TikTok/IG channel is offline — not an error
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
        _tiktok_live_re = _TIKTOK_LIVE_RE
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
            else:
                _live_outtmpl_dir = output_dir
            outtmpl = str(
                _live_outtmpl_dir
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
        _is_tiktok_vod = (
            _TIKTOK_VOD_RE.search(task.url) or _TIKTOK_SHORT_RE.search(task.url)
        )

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
                _format_id = "best[format_id^=h264]/download/bestvideo*+bestaudio*/best"
            elif "bestaudio" in _format_id:
                pass  # audio-only selector — leave unchanged, no video needed
            else:
                _format_id = "best[format_id^=h264]/download/bestvideo*+bestaudio*/best"

        opts: dict[str, Any] = {
            "format": "best" if is_live else _format_id,
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
            "logger": _DiagLogger() if (_is_tiktok_vod and not is_live) else None,
            "ignoreerrors": False,
            "retries": self._config.max_retries,
            # BUG-TT-03 FIX: fragment_retries=0 caused entire live recordings to
            # abort on a single transient network glitch (Wi-Fi blip, DNS hiccup).
            # Set to 3 for live streams — enough to survive brief interruptions
            # without making Stop/Cancel unresponsive. yt-dlp's per-fragment
            # backoff (sleep_interval) keeps retry churn low.
            "fragment_retries": 3 if is_live else self._config.max_retries,
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
        if is_live:
            opts["hls_use_mpegts"]              = True
            opts["live_from_start"]             = False
            opts["socket_timeout"]              = 10   # faster cancel response
            opts["concurrent_fragment_downloads"] = 1  # no parallel HLS writes
            opts["keep_fragments"]              = False
            # Route temp segment files away from the (potentially Unicode)
            # download directory.  tempfile.gettempdir() always returns an
            # ASCII-safe path on all supported platforms.
            _live_tmp = Path(tempfile.gettempdir()) / "omnidl_live"
            _live_tmp.mkdir(parents=True, exist_ok=True)
            opts["paths"] = {"temp": str(_live_tmp)}
            # BUG-BW FIX: restrictfilenames removed — superseded by redirecting
            # outtmpl to the ASCII temp dir above.  restrictfilenames only
            # replaced file-system-illegal chars, not Unicode, so the named
            # pipe path could still contain Vietnamese/emoji and crash ffmpeg
            # with STATUS_PIPE_NOT_AVAILABLE (0xCBAE0008).

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
                "[BUG-BQ diag] TikTok VOD starting | task=%s | "
                "original_format_id=%r | effective_format=%r",
                task.id[:8],
                task.format_id,
                opts.get("format"),
            )
            # BUG-BR FORMAT AUDIT: dump every format from extract_info so we
            # can see whether TikTok provides separate audio streams and how
            # they are labeled.  This is read-only — no effect on download.
            _all_formats = (
                task.media_info.formats if task.media_info else []
            )
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
            _is_hls_expired = (
                is_live
                and _TIKTOK_LIVE_RE.search(task.url)
                and "ffmpeg exited with code" in _exc_str.lower()
                and not task.is_cancellation_requested
            )
            if _is_hls_expired:
                logger.info(
                    "BUG-TT-02: TikTok live HLS expired for task %s — re-extracting",
                    task.id,
                )
                try:
                    _fresh_info = self.extract_info(task.url)
                    if _fresh_info and _fresh_info.is_live:
                        task.media_info = _fresh_info
                        logger.info(
                            "BUG-TT-02: fresh HLS URL obtained, retrying download"
                        )
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            ydl.download([task.url])
                        # If retry succeeded fall through to filename resolution
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
                raise RuntimeError(_friendly_error(_exc_str)) from exc
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
                    logger.warning(
                        "Failed to move live recording to output dir: %s", _mv_exc
                    )

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