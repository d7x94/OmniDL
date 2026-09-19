"""
utils/tiktok_live_checker.py
Check whether a TikTok user is currently broadcasting a live stream.

Uses TikTok's internal web API — the same endpoint yt-dlp uses when
checking live status for a user profile URL.

No new dependencies: uses `requests` (already in requirements.txt).
curl_cffi is used for TLS impersonation when available (already in pyproject.toml).

Public interface
────────────────
check_tiktok_live(username, proxy="", cookie_file="") -> Optional[str]
    Returns the live URL if the user is currently live, None otherwise.
    Raises RuntimeError on hard errors (network, rate-limit, not found).
    Pass cookie_file (decrypted Netscape .txt) for authenticated scrape --
    required since TikTok bot-detection (2026-04) strips liveRoomInfo for
    unauthenticated requests.

is_tiktok_profile_url(url) -> bool
    Returns True if url looks like a TikTok profile page (not a live/video URL).

extract_tiktok_username(url) -> Optional[str]
    Extracts the username from a TikTok profile URL.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Optional

from utils.i18n import t

logger = logging.getLogger(__name__)

# TikTok internal web API for live status.
# webcast/room/check_alive is the lightest endpoint -- returns live status
# without downloading the full user profile JSON.
_LIVE_CHECK_API = "https://www.tiktok.com/api/live/detail/"
_WEBCAST_API = "https://webcast.tiktok.com/webcast/room/check_alive/"
_REQUEST_TIMEOUT = 15  # seconds

# Cache successful room_id extractions to survive IP rate-limit windows.
# When all 4 detection passes fail (TikTok serving minimal HTML), a cached
# room_id lets us call check_alive directly -- which uses a lighter endpoint
# that isn't affected by the same HTML-scraping rate limit.
_ROOM_ID_CACHE: dict[str, tuple[str, float]] = {}  # username -> (room_id, ts)
_ROOM_ID_CACHE_TTL = 5400.0  # 90 minutes (covers typical live session duration)

# BUG-TT-ENDEDROOM FIX: rooms webcast/room/info reported as finished.
# After a broadcast ends TikTok keeps serving the old roomId in the live
# page's SIGI_STATE, and check_alive keeps answering alive=True for it, so
# pass-1/pass-2 announced "LIVE" on every poll and the caller then paid two
# more webcast calls to reject it -- @tomluoc211 room 7679856087437429522 ran
# that loop for 9.5 hours after the stream finished.  Remembering the verdict
# lets those passes bail out before check_alive.
# A restarted broadcast can reuse the same roomId, so the mark is TTL-bounded
# and is cleared the moment room/info reports status=2 again.  Pass-4 does not
# consult this set, so a genuine restart is still detected immediately.
_ENDED_ROOM_IDS: dict[str, float] = {}  # room_id -> ts
_ENDED_ROOM_TTL = 1800.0  # 30 minutes
_ENDED_ROOM_LOCK = threading.Lock()


def _mark_room_ended(room_id: str) -> None:
    if not room_id:
        return
    now = time.monotonic()
    with _ENDED_ROOM_LOCK:
        for rid, ts in list(_ENDED_ROOM_IDS.items()):
            if now - ts >= _ENDED_ROOM_TTL:
                del _ENDED_ROOM_IDS[rid]
        _ENDED_ROOM_IDS[room_id] = now


def _clear_room_ended(room_id: str) -> None:
    with _ENDED_ROOM_LOCK:
        _ENDED_ROOM_IDS.pop(room_id, None)


def _room_recently_ended(room_id: str) -> bool:
    with _ENDED_ROOM_LOCK:
        ts = _ENDED_ROOM_IDS.get(room_id)
        if ts is None:
            return False
        if time.monotonic() - ts >= _ENDED_ROOM_TTL:
            del _ENDED_ROOM_IDS[room_id]
            return False
        return True


# Profile URL pattern -- matches /@username but NOT /live/, /video/, /tag/, etc.
# TikTok usernames: letters, digits, underscores, dots (1-24 chars).
# Accepts both trailing slash and bare query string: /@user, /@user/, /@user?lang=en
# The mobile host m.tiktok.com serves the same @user paths and is what the
# TikTok app puts on the clipboard, so both hosts must match.
_PROFILE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/"
    r"@([A-Za-z0-9_.]{1,24})"
    r"(?:/|\?[^/]*)?$",
    re.I,
)

# Known live URL patterns.
# Capture group 1 = username so extract_tiktok_username_from_live_url() can
# extract it without a second regex.
# BUG-TT-05 FIX: added (?:/|\?|#|$) terminator so URLs with query strings
# (e.g. @user/live?lang=vi) and fragment anchors are correctly recognised as
# live URLs instead of falling through to _PROFILE_RE which doesn't match /live.
_LIVE_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?tiktok\.com/@([A-Za-z0-9_.]{1,24})/live(?:/|\?|#|$)",
    re.I,
)

# Regex to extract any 10+ digit roomId from raw page HTML.
# BUG-TT-08 FIX: when JSON structure parsing fails, brute-force scan the raw HTML
# for any numeric roomId pattern. TikTok room IDs are always 10+ digit integers.
_ROOM_ID_RE = re.compile(r'"roomId"\s*:\s*"(\d{10,})"')

# BUG-TT-09 FIX: pass-0 webcast API using sec_user_id from share URL query string.
# TikTok share links embed sec_user_id which can be used to call the webcast API
# directly, bypassing page scraping entirely. This is more reliable than HTML parsing
# because it doesn't depend on TikTok's bot-detection-influenced page rendering.
_WEBCAST_ROOM_LIST_API = "https://webcast.tiktok.com/webcast/room/list/"
_SEC_USER_ID_RE = re.compile(r"[?&]sec_user_id=([A-Za-z0-9_~%-]+)", re.I)

# BUG-TT-09: TikTok app API for user live status - lighter than page scrape.
# Uses the same aid=1988 (TikTok web) parameter.
_TIKTOK_USER_LIVE_API = "https://www.tiktok.com/api/live/detail/"

# BUG-TT-08 FIX: impersonation UA -- mirrors yt-dlp TikTokLiveIE impersonate=True.
# curl_cffi is already in pyproject.toml (curl-cffi>=0.15.0).
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)


def _get_chrome_impersonate_target() -> "Any":
    """Return the curl_cffi-native impersonate string for Chrome.

    curl_cffi.requests.Session/head/get(impersonate=...) requires a plain
    string like "chrome" or "chrome131" -- NOT an ImpersonateTarget object.
    ImpersonateTarget is yt-dlp's internal type; passing it to curl_cffi
    causes 'ImpersonateTarget' object has no attribute 'encode' at request time.

    Strategy:
    1. Probe CurlCFFIRH._SUPPORTED_IMPERSONATE_TARGET_MAP for a chrome146 key
       (matches _CHROME_UA above) so the result is a deliberate, deterministic
       pick rather than "whichever chrome key the map lists first" -- that
       order shifts with every curl_cffi release (0.16 added chrome142/145/146
       ahead of the older entries), which would otherwise silently move the
       TLS fingerprint away from what _CHROME_UA claims.
    2. Fall back to any chrome key if chrome146 is not listed (older curl_cffi).
    3. Fall back to "chrome" if map lookup fails or module not available.
    """
    try:
        from yt_dlp.networking._curlcffi import CurlCFFIRH as _RH  # noqa: PLC0415
        from yt_dlp.networking.impersonate import ImpersonateTarget as _IT  # noqa: PLC0415

        _map = getattr(_RH, "_SUPPORTED_IMPERSONATE_TARGET_MAP", {})
        chrome_key = next(
            (k for k, v in _map.items() if getattr(k, "client", None) == "chrome" and v == "chrome146"),
            None,
        ) or next(
            (k for k in _map if getattr(k, "client", None) == "chrome"),
            None,
        )
        if chrome_key is None:
            # Try bare unversioned key (curl_cffi < 0.15)
            bare = _IT.from_str("chrome")
            if bare in _map:
                chrome_key = bare
        if chrome_key is not None:
            # Return the curl_cffi-native string value from the map
            val = _map.get(chrome_key)
            if isinstance(val, str) and val:
                return val
    except Exception:  # noqa: BLE001
        pass
    return "chrome"  # safe fallback for curl_cffi without yt_dlp integration


def _get_impersonate_session(jar: "Optional[Any]" = None) -> "Any":
    """Return a curl_cffi Session with Chrome TLS impersonation if available,
    else fall back to a plain requests.Session.

    BUG-TT-08 FIX: TikTok bot-detection uses TLS fingerprinting in addition to
    IP/cookie checks. Using a plain requests.Session triggers bot-detection even
    with valid cookies, causing TikTok to omit roomId from the page JSON.
    BUG-TT-10 FIX: curl_cffi >= 0.15 uses ImpersonateTarget objects, not plain
    strings. Use _get_chrome_impersonate_target() to select the right form.
    """
    try:
        from curl_cffi import requests as _cffi_req  # noqa: PLC0415

        _target = _get_chrome_impersonate_target()
        cffi_session: Any = _cffi_req.Session(impersonate=_target)  # type: ignore[assignment]
        if jar:
            cffi_session.cookies.update(jar)
        return cffi_session
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: curl_cffi unavailable (%s), using requests", exc)
        import requests as _req  # noqa: PLC0415

        req_session = _req.Session()
        if jar:
            req_session.cookies.update(jar)
        return req_session


def extract_tiktok_username_from_live_url(url: str) -> Optional[str]:
    """Extract the username from a canonical TikTok /live URL.

    Returns None if the URL is not a recognised /live URL.
    Does NOT resolve short links -- call _resolve_short_link first if needed.
    """
    m = _LIVE_URL_RE.match(url.strip())
    return m.group(1).lower() if m else None


# BUG-CH FIX: TikTok short-link domains (vt.tiktok.com, vm.tiktok.com).
# User pastes a share link like https://vt.tiktok.com/ZS9N8sGVN33Go-yNEKU/
# which redirects to the canonical /@username URL.
# is_tiktok_profile_url / extract_tiktok_username must resolve these first
# so Live Monitor recognises them as profile URLs instead of falling through
# to analyse_url() which then fails with "not currently live".
_SHORT_LINK_RE = re.compile(
    r"^https?://(?:vt|vm)\.tiktok\.com/",
    re.I,
)


def _resolve_short_link(url: str, proxy: str = "") -> str:
    """Follow HTTP redirects on a TikTok short link and return the final URL.

    Returns *url* unchanged if the redirect does not land on a tiktok.com URL,
    or on any network error (fail-safe: caller still gets the original URL).
    Tries HEAD first (cheap); falls back to GET with stream=True if HEAD
    resolves to a non-canonical URL (e.g. /?_r=1) because TikTok drops
    HEAD redirect chains for some short-live-link paths.
    """
    import requests  # noqa: PLC0415

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {"User-Agent": _CHROME_UA}

    def _is_canonical(resolved: str) -> bool:
        return "tiktok.com/@" in resolved

    try:
        resp = requests.head(
            url,
            headers=headers,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        final = resp.url
        if _is_canonical(final):
            logger.debug("tiktok_live_checker: resolved %s -> %s", url, final)
            return final
        logger.debug(
            "tiktok_live_checker: HEAD resolved to non-canonical %s, retrying with GET",
            final,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: HEAD failed for %s: %s", url, exc)

    try:
        resp = requests.get(
            url,
            headers=headers,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        resp.close()
        final = resp.url
        if "tiktok.com" in final:
            logger.debug("tiktok_live_checker: GET resolved %s -> %s", url, final)
            return final
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: GET resolve failed for %s: %s", url, exc)

    return url


def is_tiktok_profile_url(url: str, proxy: str = "") -> bool:
    """Return True if *url* is a TikTok profile page (not a live/video URL).

    Automatically resolves vt/vm.tiktok.com short links before matching.
    """
    stripped = url.strip()
    if _SHORT_LINK_RE.match(stripped):
        stripped = _resolve_short_link(stripped, proxy=proxy)
    if _LIVE_URL_RE.match(stripped):
        return False
    return bool(_PROFILE_RE.match(stripped))


def extract_tiktok_username(url: str, proxy: str = "") -> Optional[str]:
    """Extract the username from a TikTok profile URL.

    Automatically resolves vt/vm.tiktok.com short links before matching.
    Returns None if the URL is not a recognisable profile URL.
    """
    stripped = url.strip()
    if _SHORT_LINK_RE.match(stripped):
        stripped = _resolve_short_link(stripped, proxy=proxy)
    m = _PROFILE_RE.match(stripped)
    return m.group(1).lower() if m else None


def check_tiktok_live(
    username: str,
    proxy: str = "",
    cookie_file: str = "",
) -> Optional[str]:
    """
    Check whether *username* is currently broadcasting a TikTok LIVE.

    BUG-TT-07 FIX: cookie_file (decrypted Netscape .txt) should be supplied
    when available -- TikTok bot-detection now strips liveRoomInfo from the
    profile page for unauthenticated requests, causing false "not live" results.

    BUG-TT-08 FIX: curl_cffi impersonation added to bypass TLS fingerprinting.

    Parameters
    ----------
    username:    TikTok username (without @)
    proxy:       Optional proxy URL e.g. "http://127.0.0.1:8080"
    cookie_file: Path to decrypted Netscape-format TikTok cookie file (optional)

    Returns
    -------
    str:  Live URL (``https://www.tiktok.com/@{username}/live``) if live
    None: User is not currently live

    Raises
    ------
    RuntimeError: On network error, rate-limit, or unrecognised API response.
    """
    result = _check_tiktok_live_with_room_id(username, proxy=proxy, cookie_file=cookie_file)
    if result is None:
        return None
    live_url, _room_id = result
    return live_url


def _load_cookie_jar(cookie_file: str) -> "Optional[Any]":
    """Load a Netscape-format cookie file into an http.cookiejar.CookieJar.

    Returns None on any error (missing file, parse failure).
    The returned jar is compatible with requests.Session.cookies.
    """
    if not cookie_file:
        return None
    from pathlib import Path as _Path  # noqa: PLC0415

    p = _Path(cookie_file)
    if not p.is_file():
        return None
    try:
        import http.cookiejar as _cj  # noqa: PLC0415

        jar = _cj.MozillaCookieJar()
        jar.load(str(p), ignore_discard=True, ignore_expires=True)
        return jar
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: failed to load cookie jar %s: %s", p.name, exc)
        return None


def _fetch_tiktok_profile_page(username: str, proxy: str = "", cookie_file: str = "") -> Optional[str]:
    """Fetch TikTok profile page HTML. Returns page text or None on error.

    BUG-TT-08 FIX: use curl_cffi impersonation to bypass TLS fingerprinting.
    TikTok bot-detection now uses both IP reputation AND TLS fingerprinting.
    A plain requests.Session triggers bot-detection even with valid cookies,
    causing TikTok to omit roomId from the response JSON.
    curl_cffi with impersonate='chrome124' mirrors yt-dlp's impersonate=True.
    """
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Google Chrome";v="146", "Chromium";v="146", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
    jar = _load_cookie_jar(cookie_file)
    if jar:
        logger.debug("tiktok_live_checker: fetching @%s with %d cookies", username, len(list(jar)))
    else:
        logger.debug(
            "tiktok_live_checker: fetching @%s without cookies -- liveRoomInfo may be absent",
            username,
        )
    profile_url = f"https://www.tiktok.com/@{username}"
    session = _get_impersonate_session(jar)
    try:
        resp = session.get(
            profile_url,
            headers=headers,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:
        exc_s = str(exc)
        if "connection" in exc_s.lower() or "connect" in exc_s.lower():
            raise RuntimeError(t("err.network", err=exc)) from exc
        if "timeout" in exc_s.lower():
            raise RuntimeError(t("err.tiktok_api_timeout")) from None
        raise RuntimeError(t("err.http", err=exc)) from exc
    finally:
        # Each unclosed curl_cffi Session pins a libcurl easy handle whose
        # native memory CPython's GC thresholds cannot see. The response body
        # is already buffered here (stream=False), so closing now is safe.
        session.close()

    if resp.status_code == 404:
        raise RuntimeError(f"not found: Tai khoan @{username} khong tim thay tren TikTok.")
    if resp.status_code == 429:
        raise RuntimeError("blocked: TikTok dang rate-limit tam thoi.\nCho 5-10 phut roi thu lai.")
    if resp.status_code not in (200, 301, 302):
        logger.debug(
            "tiktok_live_checker: unexpected status %s for @%s",
            resp.status_code,
            username,
        )
        return None
    return resp.text


def _fetch_tiktok_live_page(username: str, proxy: str = "", cookie_file: str = "") -> "Optional[str]":
    """Fetch TikTok live page HTML (/@username/live). Returns page text or None.

    BUG-TT-07 FIX: pass-2 fetch mirrors yt-dlp TikTokLiveIE pass-2.
    BUG-TT-08 FIX: curl_cffi impersonation added.
    """
    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.tiktok.com/@{username}",
    }
    jar = _load_cookie_jar(cookie_file)
    live_url = f"https://www.tiktok.com/@{username}/live"
    session = _get_impersonate_session(jar)
    try:
        resp = session.get(
            live_url,
            headers=headers,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: live page fetch failed for @%s: %s", username, exc)
        return None
    finally:
        session.close()
    if resp.status_code not in (200, 301, 302):
        logger.debug(
            "tiktok_live_checker: live page status %s for @%s",
            resp.status_code,
            username,
        )
        return None
    return resp.text


def _extract_json_blob(page_text: str, script_id: str) -> "Optional[dict]":
    """Extract and parse a JSON blob from a <script id="..."> tag."""
    import json as _json  # noqa: PLC0415

    m = re.search(
        r'<script[^>]+id="' + re.escape(script_id) + r'"[^>]*>(.*?)</script>',
        page_text,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return _json.loads(m.group(1))
    except (ValueError, _json.JSONDecodeError):
        return None


def _room_id_from_profile_page(page_text: str, username: str) -> "tuple[Optional[str], bool]":
    """Extract room_id from a TikTok profile page (/@username).

    BUG-TT-07 FIX: mirrors yt-dlp TikTokLiveIE pass-1 exactly.
    yt-dlp reads __UNIVERSAL_DATA_FOR_REHYDRATION__ -> __DEFAULT_SCOPE__ ->
    webapp.user-detail -> userInfo -> user -> roomId.

    BUG-TT-08 FIX: validate roomId is a non-zero integer string (TikTok returns
    "0" or "" for users who are not live -- these are not valid room IDs).
    Also add raw-HTML regex scan as final fallback.

    Returns (room_id, status_ended).
    status_ended=True means stream is confirmed ended (status 4/5) -- caller
    should NOT fall through to pass-2 live page scraping.
    """

    def _valid_room_id(v: Any) -> Optional[str]:
        """Return room_id string if v is a valid non-zero numeric room ID."""
        if not v:
            return None
        s = str(v).strip()
        if not s.isdigit() or int(s) == 0:
            return None
        return s

    # Primary: __UNIVERSAL_DATA_FOR_REHYDRATION__ (current TikTok, mirrors yt-dlp pass 1)
    data = _extract_json_blob(page_text, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
    _status_ended = False  # True if we parsed liveRoomInfo with status 4/5
    if data:
        scope = data.get("__DEFAULT_SCOPE__", {})
        user_info = scope.get("webapp.user-detail", {}).get("userInfo", {})
        room_id = _valid_room_id(user_info.get("user", {}).get("roomId"))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via UNIVERSAL_DATA_FOR_REHYDRATION: %s",
                username,
                room_id,
            )
            return room_id, False
        # Also check liveRoomInfo path (legacy / some regions)
        live_room = user_info.get("liveRoomInfo")
        if live_room:
            room_id = _valid_room_id(live_room.get("roomId") or live_room.get("id"))
            status = live_room.get("status")
            if room_id and (status == 2 or status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s liveRoomInfo via UNIVERSAL_DATA_FOR_REHYDRATION"
                    " status=%s roomId=%s",
                    username,
                    status,
                    room_id,
                )
                return room_id, False
            if room_id and status in (4, 5):
                _status_ended = True

    # Fallback: __NEXT_DATA__ (older TikTok page format, still used in some regions)
    data = _extract_json_blob(page_text, "__NEXT_DATA__")
    if data:
        user_info = data.get("props", {}).get("pageProps", {}).get("userInfo", {})
        room_id = _valid_room_id(user_info.get("user", {}).get("roomId"))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via __NEXT_DATA__ user.roomId: %s",
                username,
                room_id,
            )
            return room_id, False
        live_room = user_info.get("liveRoomInfo")
        if live_room:
            room_id = _valid_room_id(live_room.get("roomId") or live_room.get("id"))
            status = live_room.get("status")
            if room_id and (status == 2 or status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s liveRoomInfo via __NEXT_DATA__ status=%s roomId=%s",
                    username,
                    status,
                    room_id,
                )
                return room_id, False
            if room_id and status in (4, 5):
                _status_ended = True

    # BUG-TT-08 FIX pass-3: raw regex scan on entire page HTML.
    # When TikTok changes the script tag structure, JSON path parsing fails but
    # the raw "roomId":"<digits>" pattern is still present in the HTML source.
    # Skip if we already parsed a valid liveRoomInfo with status=4/5 (stream ended).
    if not _status_ended:
        for m in _ROOM_ID_RE.finditer(page_text):
            room_id = _valid_room_id(m.group(1))
            if room_id:
                logger.debug(
                    "tiktok_live_checker: @%s roomId via raw HTML scan: %s",
                    username,
                    room_id,
                )
                return room_id, False

    return None, _status_ended


def _room_id_from_live_page(page_text: str, username: str) -> "Optional[str]":
    """Extract room_id from a TikTok live page (/@username/live).

    BUG-TT-07 FIX: mirrors yt-dlp TikTokLiveIE pass-2 exactly.
    yt-dlp reads SIGI_STATE/sigi-persisted-data -> LiveRoom.liveRoomUserInfo.user.roomId
    or UserModule.users.*.roomId.

    BUG-TT-08 FIX: validate roomId, add raw HTML scan fallback.
    """

    def _valid_room_id(v: Any) -> Optional[str]:
        if not v:
            return None
        s = str(v).strip()
        if not s.isdigit() or int(s) == 0:
            return None
        return s

    sigi = _extract_json_blob(page_text, "SIGI_STATE") or _extract_json_blob(page_text, "sigi-persisted-data")
    if sigi:
        room_id = _valid_room_id(
            sigi.get("LiveRoom", {}).get("liveRoomUserInfo", {}).get("user", {}).get("roomId")
        )
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via SIGI_STATE LiveRoom: %s",
                username,
                room_id,
            )
            return room_id
        users = sigi.get("UserModule", {}).get("users", {})
        for user_data in users.values():
            room_id = _valid_room_id(user_data.get("roomId"))
            if room_id:
                logger.debug(
                    "tiktok_live_checker: @%s roomId via SIGI_STATE UserModule: %s",
                    username,
                    room_id,
                )
                return room_id

    # BUG-TT-26B ALIGN: live page migrated from SIGI_STATE to URD format.
    # _fetch_hls_from_live_page already checks this path; backport here so
    # Pass-2 room_id detection is consistent with HLS extraction.
    urd = _extract_json_blob(page_text, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if urd:
        scope = urd.get("__DEFAULT_SCOPE__", {})
        lr_info = scope.get("webapp.user-detail", {}).get("userInfo", {}).get("liveRoomInfo") or {}
        room_id = _valid_room_id(lr_info.get("roomId") or lr_info.get("id"))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via live page URD liveRoomInfo: %s",
                username,
                room_id,
            )
            return room_id

    # BUG-TT-08 FIX: raw regex fallback on live page too
    for m in _ROOM_ID_RE.finditer(page_text):
        room_id = _valid_room_id(m.group(1))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via live page raw HTML scan: %s",
                username,
                room_id,
            )
            return room_id

    return None


def _verify_room_alive(
    room_id: str,
    username: str,
    proxy: str = "",
    cookie_file: str = "",
) -> bool:
    """Call webcast/room/check_alive to confirm the room is currently live.

    BUG-TT-08 FIX: after extracting room_id from HTML, verify it is actually
    live via the webcast API. This catches cases where room_id is present in
    the page but the stream has already ended.

    Returns True if the API confirms live, False on any failure or non-live status.
    On API errors, returns True (optimistic -- caller already has a room_id,
    let yt-dlp attempt the download and fail gracefully if stream ended).
    """
    import json as _json  # noqa: PLC0415

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "application/json, */*",
        "Referer": f"https://www.tiktok.com/@{username}/live",
        "Origin": "https://www.tiktok.com",
    }
    jar = _load_cookie_jar(cookie_file)
    session = _get_impersonate_session(jar)
    try:
        resp = session.get(
            _WEBCAST_API,
            params={"aid": "1988", "room_id": room_id},
            headers=headers,
            proxies=proxies,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug(
                "tiktok_live_checker: check_alive status %s for room %s -- assuming live",
                resp.status_code,
                room_id,
            )
            return True
        data = _json.loads(resp.text)
        # check_alive returns {"data": [{"room_id": "...", "alive": true/false}]}
        # BUG-TT-CHECKALIVE-DICT FIX: API sometimes returns a dict instead of a
        # list for the "data" field.  alive_list[0] on a dict raises KeyError(0)
        # (str == "0"), which the except handler caught and treated as "assuming
        # live" -- silently masking a confirmed-not-live result.
        alive_list = data.get("data") or []
        if isinstance(alive_list, dict):
            alive_list = [alive_list]
        if not alive_list:
            logger.debug(
                "tiktok_live_checker: check_alive empty response for room %s -- assuming live",
                room_id,
            )
            return True
        alive = alive_list[0].get("alive", True)
        logger.debug("tiktok_live_checker: check_alive room %s alive=%s", room_id, alive)
        return bool(alive)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "tiktok_live_checker: check_alive failed for room %s: %s -- assuming live",
            room_id,
            exc,
        )
        return True
    finally:
        session.close()


def _fetch_hls_from_webcast_room_info(
    room_id: str,
    username: str,
    proxy: str = "",
    cookie_file: str = "",
    exclude_bases: "frozenset[str]" = frozenset(),
) -> "Optional[tuple[str, str]]":
    """Call webcast/room/info/ with Chrome impersonation to get the HLS stream URL.

    BUG-TT-25 FIX: yt-dlp TikTokLiveIE calls room/info without signing (no
    X-Bogus/msToken). TikTok returns status=4 for unsigned requests even when
    the stream is active. This function calls the same endpoint via curl_cffi
    TLS impersonation, which TikTok treats as an authenticated browser request.

    Returns (hls_url, room_id) if status=2 (live), None otherwise.
    """
    import json as _json  # noqa: PLC0415

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "application/json, */*",
        "Referer": f"https://www.tiktok.com/@{username}/live",
        "Origin": "https://www.tiktok.com",
    }
    jar = _load_cookie_jar(cookie_file)
    session = _get_impersonate_session(jar)
    try:
        resp = session.get(
            "https://webcast.tiktok.com/webcast/room/info/",
            params={"aid": "1988", "room_id": room_id},
            headers=headers,
            proxies=proxies,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "tiktok_live_checker: room/info HTTP %s for room %s",
                resp.status_code,
                room_id,
            )
            return None
        data = _json.loads(resp.text)
        _raw_data = data.get("data")
        if not _raw_data or not isinstance(_raw_data, dict):
            logger.debug(
                "BUG-TT-25: room/info empty envelope (blocked/rate-limited) for room %s",
                room_id,
            )
            return None
        room_data = _raw_data
        # BUG-TT-ROOMINFO-NESTED FIX: TikTok sometimes nests room data under
        # a "room" key: {"data": {"room": {"status": 2, "stream_url": {...}}}}
        # instead of the flat {"data": {"status": 2, ...}} structure.
        _nested = room_data.get("room")
        if isinstance(_nested, dict):
            room_data = _nested
        status = room_data.get("status")
        if status != 2:
            if status in (4, 5):
                _mark_room_ended(room_id)
                logger.debug(
                    "BUG-TT-25: room/info status=%s (ended) for room %s",
                    status,
                    room_id,
                )
            else:
                logger.debug(
                    "tiktok_live_checker: room/info status=%s for room %s (not live)",
                    status,
                    room_id,
                )
            return None
        # status=2: the room is broadcasting again -- a restarted stream may
        # reuse the roomId that was marked ended earlier.
        _clear_room_ended(room_id)
        stream_url = room_data.get("stream_url") or {}
        # Collect all CDN variants: primary first, then hls_pull_url_map entries.
        # Different quality variants (HD/SD/LD) may be on different CDN nodes —
        # when the primary CDN stalls, a lower-quality variant may still serve data.
        _hls_map = stream_url.get("hls_pull_url_map") or {}
        _candidates: list[str] = []
        _primary = stream_url.get("hls_pull_url", "")
        if _primary:
            _candidates.append(_primary)
        for _u in _hls_map.values():
            if isinstance(_u, str) and _u and _u not in _candidates:
                _candidates.append(_u)
        # Hoist FLV candidates so they are available in both the no-HLS branch
        # and the HLS-all-excluded fallback (BUG-TT-HLS404 FIX) further down.
        # BUG-TT-PRELIVE FIX: TikTok's stream_url.flv_pull_url is a
        # {quality: url} dict (no flv_pull_url_map key), so iterate its values.
        # Tolerate the legacy string form too.
        _flv_raw = stream_url.get("flv_pull_url")
        _flv_candidates: list[str] = []
        if isinstance(_flv_raw, dict):
            for _u in _flv_raw.values():
                if isinstance(_u, str) and _u and _u not in _flv_candidates:
                    _flv_candidates.append(_u)
        elif isinstance(_flv_raw, str) and _flv_raw:
            _flv_candidates.append(_flv_raw)
        for _u in (stream_url.get("flv_pull_url_map") or {}).values():
            if isinstance(_u, str) and _u and _u not in _flv_candidates:
                _flv_candidates.append(_u)
        if not _candidates:
            _su_keys = list(stream_url.keys())
            logger.debug(
                "tiktok_live_checker: room/info no HLS URL for room %s (stream_url keys: %s)",
                room_id,
                _su_keys,
            )
            # BUG-TT-FLV FIX: some streamers/regions serve FLV instead of HLS.
            # room/info returns status=2 (live) but hls_pull_url is absent while
            # flv_pull_url is populated. The BUG-TT-16 download path already routes
            # .flv URLs to FFmpeg (yt_dlp_engine.py BUG-TT-FLV-CURL), so returning
            # the FLV URL here is sufficient to unblock the download.
            for _url in _flv_candidates:
                if _url.split("?")[0] not in exclude_bases:
                    logger.info(
                        "tiktok_live_checker: room/info FLV fallback for @%s room %s",
                        username,
                        room_id,
                    )
                    return _url, room_id
            return None
        # Return first URL whose CDN base is not in the caller's exclude list.
        for _url in _candidates:
            if _url.split("?")[0] not in exclude_bases:
                logger.info(
                    "tiktok_live_checker: room/info @%s room %s -> HLS URL obtained",
                    username,
                    room_id,
                )
                return _url, room_id
        # BUG-TT-HLS404 FIX: every HLS variant is on an excluded (persistent-404) CDN
        # base, but the room is live (status=2). FLV pull infra (pull-flv-*) is separate
        # from HLS (pull-hls-*) and frequently serves when the HLS playlist 404s. Try FLV
        # before returning a known-dead HLS URL (mirrors BUG-TT-24 in yt_dlp_engine).
        for _url in _flv_candidates:
            if _url.split("?")[0] not in exclude_bases:
                logger.info(
                    "tiktok_live_checker: room/info HLS exhausted — FLV fallback for @%s room %s",
                    username,
                    room_id,
                )
                return _url, room_id
        # All known bases excluded — return primary anyway as last resort.
        logger.debug(
            "tiktok_live_checker: room/info all %d HLS variants excluded for room %s — returning primary",
            len(_candidates),
            room_id,
        )
        return _candidates[0], room_id
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: room/info failed for room %s: %s", room_id, exc)
        return None
    finally:
        session.close()


def _fetch_hls_from_live_page(
    username: str,
    proxy: str = "",
    cookie_file: str = "",
) -> "Optional[tuple[str, str]]":
    """Extract HLS URL from the live page (no webcast API needed).

    BUG-TT-26 FIX: when webcast.tiktok.com is unreachable or returns non-live
    status, fall back to fetching www.tiktok.com/@username/live with Chrome
    impersonation and extracting the stream URL from the page.

    BUG-TT-26B FIX: TikTok migrated live page data from SIGI_STATE to
    UNIVERSAL_DATA_FOR_REHYDRATION. Tries SIGI_STATE first (legacy), then
    URD, then __NEXT_DATA__ (older regions).

    Returns (hls_url, room_id) or None.
    """
    page_text = _fetch_tiktok_live_page(username, proxy=proxy, cookie_file=cookie_file)
    if not page_text:
        return None

    def _hls_from_live_room(lr: dict) -> "Optional[tuple[str, str]]":
        rid = str(lr.get("roomId") or lr.get("id") or "")
        if not rid or not rid.isdigit() or int(rid) == 0:
            return None
        su = lr.get("streamUrl") or lr.get("stream_url") or {}
        # BUG-TT-FLV FIX: fall back to FLV if HLS is absent in the page JSON.
        _flv = su.get("flv_pull_url")
        url = (
            su.get("hls_pull_url")
            or next(iter((su.get("hls_pull_url_map") or {}).values()), "")
            or (next(iter(_flv.values()), "") if isinstance(_flv, dict) else (_flv or ""))
            or next(iter((su.get("flv_pull_url_map") or {}).values()), "")
        )
        return (url, rid) if isinstance(url, str) and url else None

    # Path 1: SIGI_STATE (legacy, still used in some regions)
    sigi = _extract_json_blob(page_text, "SIGI_STATE") or _extract_json_blob(page_text, "sigi-persisted-data")
    if sigi:
        live_room = sigi.get("LiveRoom", {}).get("liveRoomUserInfo", {}).get("liveRoom", {})
        result = _hls_from_live_room(live_room) if live_room else None
        if result:
            logger.info(
                "tiktok_live_checker: BUG-TT-26 live page HLS URL for @%s room %s",
                username,
                result[1],
            )
            return result

    # BUG-TT-26B FIX: TikTok live page now puts room data in UNIVERSAL_DATA_FOR_REHYDRATION.
    # Same JSON blob that pass-1 profile scrape reads successfully for roomId detection.
    urd = _extract_json_blob(page_text, "__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if urd:
        scope = urd.get("__DEFAULT_SCOPE__", {})
        lr_info = scope.get("webapp.user-detail", {}).get("userInfo", {}).get("liveRoomInfo") or {}
        result = _hls_from_live_room(lr_info) if lr_info else None
        if result:
            logger.info(
                "tiktok_live_checker: BUG-TT-26B live page URD HLS URL for @%s room %s",
                username,
                result[1],
            )
            return result

    # Path 3: __NEXT_DATA__ (older TikTok page format, some regions)
    next_data = _extract_json_blob(page_text, "__NEXT_DATA__")
    if next_data:
        lr_info = (
            next_data.get("props", {}).get("pageProps", {}).get("userInfo", {}).get("liveRoomInfo") or {}
        )
        result = _hls_from_live_room(lr_info) if lr_info else None
        if result:
            logger.info(
                "tiktok_live_checker: BUG-TT-26B live page NEXT_DATA HLS URL for @%s room %s",
                username,
                result[1],
            )
            return result

    logger.debug("tiktok_live_checker: BUG-TT-26 no HLS URL in SIGI_STATE/URD for @%s", username)
    if cookie_file:
        logger.debug("tiktok_live_checker: BUG-TT-26NC retrying without cookies for @%s", username)
        return _fetch_hls_from_live_page(username, proxy=proxy, cookie_file="")
    return None


_dispatcher: "Optional[Any]" = None
_health_daemon: "Optional[Any]" = None


def _get_dispatcher() -> "Any":
    global _dispatcher, _health_daemon  # noqa: PLW0603
    if _dispatcher is None:
        from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher
        from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
        from utils.tiktok_detection.strategies import (
            Pass0WebcastApi,
            Pass1ProfilePage,
            Pass2LivePage,
            Pass3UserApi,
            Pass4ApiLiveRoom,
        )

        strategies = [
            Pass4ApiLiveRoom(),
            Pass0WebcastApi(),
            Pass1ProfilePage(),
            Pass2LivePage(),
            Pass3UserApi(),
        ]
        registry = StrategyHealthRegistry()
        _dispatcher = LiveDetectionDispatcher(strategies, registry)
        _health_daemon = HealthDaemon(strategies, registry)
        _health_daemon.start()
    return _dispatcher


def get_health_daemon() -> "Optional[Any]":
    """Return the health daemon singleton, or None if dispatcher not yet created."""
    return _health_daemon


def _check_tiktok_live_with_room_id(
    username: str,
    proxy: str = "",
    cookie_file: str = "",
    share_url: str = "",
) -> "Optional[tuple[str, str]]":
    """Internal: returns (live_url, room_id) if live, None if not live.

    Delegates to LiveDetectionDispatcher which runs Pass-0/1/2 in parallel.
    Public signature unchanged for backward compatibility.
    """
    from utils.tiktok_detection.context import LiveCheckContext

    ctx = LiveCheckContext(
        username=username,
        proxy=proxy,
        cookie_file=cookie_file,
        share_url=share_url,
    )
    # A hard error (429 / 404 / network) is held, not raised yet: the whole
    # point of _ROOM_ID_CACHE is to answer during a rate-limit window via
    # check_alive, which does not share the HTML-scraping limit. Raising here
    # would skip that path exactly when it applies. Re-raised below if the
    # cache cannot answer either.
    _hard_error: "Optional[RuntimeError]" = None
    try:
        result = _get_dispatcher().check(ctx)
    except RuntimeError as exc:
        _hard_error = exc
        result = None

    if result is not None:
        if _verify_room_alive(result[1], username, proxy=proxy, cookie_file=cookie_file):
            # BUG-TT-PRELIVE FIX: TikTok pre-populates SIGI_STATE.LiveRoom.roomId and
            # check_alive returns alive=True for scheduled streams before they start.
            # Require room/info status=2 + HLS URL as the authoritative "broadcasting" signal.
            hls_check = _fetch_hls_from_webcast_room_info(
                result[1], username, proxy=proxy, cookie_file=cookie_file
            )
            if hls_check is None:
                logger.debug(
                    "tiktok_live_checker: @%s roomId=%s alive but room/info status!=2 -- %s",
                    username,
                    result[1],
                    "stream ended" if _room_recently_ended(result[1]) else "scheduled, not live yet",
                )
                return None
            _ROOM_ID_CACHE[username] = (result[1], time.monotonic())
            return result
        # roomId found but stream already ended -- fall through to cached/None path

    # All passes failed (IP rate-limit serving minimal HTML).
    # If we have a recent cached room_id, verify via check_alive -- that
    # endpoint is not affected by the same HTML-scraping rate limit.
    cached = _ROOM_ID_CACHE.get(username)
    if cached:
        cached_room_id, ts = cached
        if time.monotonic() - ts < _ROOM_ID_CACHE_TTL:
            if _verify_room_alive(cached_room_id, username, proxy=proxy, cookie_file=cookie_file):
                # BUG-TT-PRELIVE FIX: also gate cached path on room/info status=2.
                hls_check = _fetch_hls_from_webcast_room_info(
                    cached_room_id, username, proxy=proxy, cookie_file=cookie_file
                )
                if hls_check is None:
                    logger.debug(
                        "tiktok_live_checker: @%s cached roomId=%s alive but room/info status!=2 -- evicting",
                        username,
                        cached_room_id,
                    )
                    _ROOM_ID_CACHE.pop(username, None)
                    return None
                logger.info(
                    "tiktok_live_checker: @%s LIVE via cached roomId=%s (detection blocked)",
                    username,
                    cached_room_id,
                )
                return (f"https://www.tiktok.com/@{username}/live", cached_room_id)
            # check_alive confirmed not live -- evict stale cache entry.
            # This is authoritative, so it also settles a held rate-limit error.
            _ROOM_ID_CACHE.pop(username, None)
            return None
        else:
            # TTL expired: previously this fell straight through to `return None`
            # and left the entry pinned for the process lifetime.
            _ROOM_ID_CACHE.pop(username, None)

    if _hard_error is not None:
        raise _hard_error
    return None


def _extract_live_room_id(data: dict, username: str) -> "Optional[str]":
    """Compatibility shim for any callers that pass a pre-parsed dict.

    Tries the UNIVERSAL_DATA_FOR_REHYDRATION paths first, then deep-walk.
    """

    def _valid(v: Any) -> Optional[str]:
        if not v:
            return None
        s = str(v).strip()
        return s if s.isdigit() and int(s) != 0 else None

    try:
        scope = data.get("__DEFAULT_SCOPE__", {})
        room_id = _valid(
            scope.get("webapp.user-detail", {}).get("userInfo", {}).get("user", {}).get("roomId")
        )
        if room_id:
            return room_id
        live_room = scope.get("webapp.user-detail", {}).get("userInfo", {}).get("liveRoomInfo")
        if live_room:
            status = live_room.get("status")
            room_id = _valid(live_room.get("roomId") or live_room.get("id"))
            if room_id and (status == 2 or status not in (4, 5)):
                return room_id
    except (AttributeError, TypeError):
        pass
    try:
        stack = [data]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("status") == 2:
                room_id = _valid(node.get("roomId"))
                if room_id:
                    return room_id
            stack.extend(node.values())
    except (AttributeError, TypeError, RecursionError):
        pass
    return None


def _extract_live_status(data: dict, username: str) -> bool:
    """Compatibility wrapper -- returns bool. Use _extract_live_room_id for new code."""
    return _extract_live_room_id(data, username) is not None
