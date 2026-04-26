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
from typing import Any, Optional

logger = logging.getLogger(__name__)

# TikTok internal web API for live status.
# webcast/room/check_alive is the lightest endpoint -- returns live status
# without downloading the full user profile JSON.
_LIVE_CHECK_API  = "https://www.tiktok.com/api/live/detail/"
_WEBCAST_API     = "https://webcast.tiktok.com/webcast/room/check_alive/"
_REQUEST_TIMEOUT = 15  # seconds

# Profile URL pattern -- matches /@username but NOT /live/, /video/, /tag/, etc.
# TikTok usernames: letters, digits, underscores, dots (1-24 chars).
# Accepts both trailing slash and bare query string: /@user, /@user/, /@user?lang=en
_PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?tiktok\.com/"
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
    r"^https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{1,24})/live(?:/|\?|#|$)",
    re.I,
)

# Regex to extract any 10+ digit roomId from raw page HTML.
# BUG-TT-08 FIX: when JSON structure parsing fails, brute-force scan the raw HTML
# for any numeric roomId pattern. TikTok room IDs are always 10+ digit integers.
_ROOM_ID_RE = re.compile(r'"roomId"\s*:\s*"(\d{10,})"')

# BUG-TT-08 FIX: impersonation UA -- mirrors yt-dlp TikTokLiveIE impersonate=True.
# curl_cffi is already in pyproject.toml (curl-cffi>=0.15.0).
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _get_impersonate_session(jar: "Optional[Any]" = None) -> "Any":
    """Return a curl_cffi Session with Chrome TLS impersonation if available,
    else fall back to a plain requests.Session.

    BUG-TT-08 FIX: TikTok bot-detection uses TLS fingerprinting in addition to
    IP/cookie checks. Using a plain requests.Session triggers bot-detection even
    with valid cookies, causing TikTok to omit roomId from the page JSON.
    curl_cffi with impersonate='chrome124' bypasses TLS fingerprinting,
    mirroring exactly what yt-dlp does (impersonate=True on _download_webpage).
    """
    try:
        from curl_cffi import requests as _cffi_req  # noqa: PLC0415
        session = _cffi_req.Session(impersonate="chrome124")
        if jar:
            session.cookies.update(jar)
        return session
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: curl_cffi unavailable (%s), using requests", exc)
        import requests as _req  # noqa: PLC0415
        session = _req.Session()
        if jar:
            session.cookies.update(jar)
        return session


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


def _fetch_tiktok_profile_page(
    username: str, proxy: str = "", cookie_file: str = ""
) -> Optional[str]:
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
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
    jar = _load_cookie_jar(cookie_file)
    if jar:
        logger.debug(
            "tiktok_live_checker: fetching @%s with %d cookies", username, len(list(jar))
        )
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
            raise RuntimeError(f"Loi ket noi mang: {exc}") from exc
        if "timeout" in exc_s.lower():
            raise RuntimeError("TikTok API het thoi gian cho. Thu lai sau.") from None
        raise RuntimeError(f"Loi HTTP: {exc}") from exc

    if resp.status_code == 404:
        raise RuntimeError(
            f"not found: Tai khoan @{username} khong tim thay tren TikTok."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "blocked: TikTok dang rate-limit tam thoi.\n"
            "Cho 5-10 phut roi thu lai."
        )
    if resp.status_code not in (200, 301, 302):
        logger.debug(
            "tiktok_live_checker: unexpected status %s for @%s",
            resp.status_code, username,
        )
        return None
    return resp.text


def _fetch_tiktok_live_page(
    username: str, proxy: str = "", cookie_file: str = ""
) -> "Optional[str]":
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
    if resp.status_code not in (200, 301, 302):
        logger.debug(
            "tiktok_live_checker: live page status %s for @%s",
            resp.status_code, username,
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


def _room_id_from_profile_page(page_text: str, username: str) -> "Optional[str]":
    """Extract room_id from a TikTok profile page (/@username).

    BUG-TT-07 FIX: mirrors yt-dlp TikTokLiveIE pass-1 exactly.
    yt-dlp reads __UNIVERSAL_DATA_FOR_REHYDRATION__ -> __DEFAULT_SCOPE__ ->
    webapp.user-detail -> userInfo -> user -> roomId.

    BUG-TT-08 FIX: validate roomId is a non-zero integer string (TikTok returns
    "0" or "" for users who are not live -- these are not valid room IDs).
    Also add raw-HTML regex scan as final fallback.
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
    if data:
        scope = data.get("__DEFAULT_SCOPE__", {})
        user_info = scope.get("webapp.user-detail", {}).get("userInfo", {})
        room_id = _valid_room_id(user_info.get("user", {}).get("roomId"))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via UNIVERSAL_DATA_FOR_REHYDRATION: %s",
                username, room_id,
            )
            return room_id
        # Also check liveRoomInfo path (legacy / some regions)
        live_room = user_info.get("liveRoomInfo")
        if live_room:
            room_id = _valid_room_id(live_room.get("roomId") or live_room.get("id"))
            status = live_room.get("status")
            if room_id and (status == 2 or status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s liveRoomInfo via UNIVERSAL_DATA_FOR_REHYDRATION"
                    " status=%s roomId=%s", username, status, room_id,
                )
                return room_id

    # Fallback: __NEXT_DATA__ (older TikTok page format, still used in some regions)
    data = _extract_json_blob(page_text, "__NEXT_DATA__")
    if data:
        user_info = (
            data.get("props", {}).get("pageProps", {}).get("userInfo", {})
        )
        room_id = _valid_room_id(user_info.get("user", {}).get("roomId"))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via __NEXT_DATA__ user.roomId: %s",
                username, room_id,
            )
            return room_id
        live_room = user_info.get("liveRoomInfo")
        if live_room:
            room_id = _valid_room_id(live_room.get("roomId") or live_room.get("id"))
            status = live_room.get("status")
            if room_id and (status == 2 or status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s liveRoomInfo via __NEXT_DATA__"
                    " status=%s roomId=%s", username, status, room_id,
                )
                return room_id

    # BUG-TT-08 FIX pass-3: raw regex scan on entire page HTML.
    # When TikTok changes the script tag structure, JSON path parsing fails but
    # the raw "roomId":"<digits>" pattern is still present in the HTML source.
    for m in _ROOM_ID_RE.finditer(page_text):
        room_id = _valid_room_id(m.group(1))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via raw HTML scan: %s",
                username, room_id,
            )
            return room_id

    return None


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

    sigi = (
        _extract_json_blob(page_text, "SIGI_STATE")
        or _extract_json_blob(page_text, "sigi-persisted-data")
    )
    if sigi:
        room_id = _valid_room_id(
            sigi.get("LiveRoom", {})
                .get("liveRoomUserInfo", {})
                .get("user", {})
                .get("roomId")
        )
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via SIGI_STATE LiveRoom: %s",
                username, room_id,
            )
            return room_id
        users = sigi.get("UserModule", {}).get("users", {})
        for user_data in users.values():
            room_id = _valid_room_id(user_data.get("roomId"))
            if room_id:
                logger.debug(
                    "tiktok_live_checker: @%s roomId via SIGI_STATE UserModule: %s",
                    username, room_id,
                )
                return room_id

    # BUG-TT-08 FIX: raw regex fallback on live page too
    for m in _ROOM_ID_RE.finditer(page_text):
        room_id = _valid_room_id(m.group(1))
        if room_id:
            logger.debug(
                "tiktok_live_checker: @%s roomId via live page raw HTML scan: %s",
                username, room_id,
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
                resp.status_code, room_id,
            )
            return True
        data = _json.loads(resp.text)
        # check_alive returns {"data": [{"room_id": "...", "alive": true/false}]}
        alive_list = data.get("data") or []
        if not alive_list:
            logger.debug(
                "tiktok_live_checker: check_alive empty response for room %s -- assuming live",
                room_id,
            )
            return True
        alive = alive_list[0].get("alive", True)
        logger.debug(
            "tiktok_live_checker: check_alive room %s alive=%s", room_id, alive
        )
        return bool(alive)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "tiktok_live_checker: check_alive failed for room %s: %s -- assuming live",
            room_id, exc,
        )
        return True


def _check_tiktok_live_with_room_id(
    username: str,
    proxy: str = "",
    cookie_file: str = "",
) -> "Optional[tuple[str, str]]":
    """Internal: returns (live_url, room_id) if live, None if not live.

    BUG-TT-06 FIX: yt-dlp TikTokLiveIE scrapes the profile page to get
    roomId, then calls webcast.tiktok.com/webcast/room/info.  TikTok now
    frequently returns profile pages where roomId is absent even during an
    active stream (bot-detection / schema change), causing UserNotLive.

    BUG-TT-07 FIX: previous fix used wrong script tag names and wrong JSON
    paths. Correct tags: __UNIVERSAL_DATA_FOR_REHYDRATION__ (profile page)
    and SIGI_STATE/sigi-persisted-data (live page), mirroring yt-dlp exactly.

    BUG-TT-08 FIX: added curl_cffi TLS impersonation, roomId validation
    (reject "0"/empty), raw HTML regex scan as pass-3, and webcast
    check_alive verification after finding a room_id.

    Raises RuntimeError on network errors (propagated from _fetch_tiktok_profile_page).
    """
    # Pass 1: profile page /@username -> __UNIVERSAL_DATA_FOR_REHYDRATION__
    page_text = _fetch_tiktok_profile_page(username, proxy=proxy, cookie_file=cookie_file)
    if page_text is None:
        return None

    room_id = _room_id_from_profile_page(page_text, username)

    # Pass 2: live page /@username/live -> SIGI_STATE (mirrors yt-dlp pass 2)
    if not room_id:
        live_page_text = _fetch_tiktok_live_page(username, proxy=proxy, cookie_file=cookie_file)
        if live_page_text:
            room_id = _room_id_from_live_page(live_page_text, username)

    if not room_id:
        logger.debug(
            "tiktok_live_checker: @%s -- no roomId in profile or live page, not live",
            username,
        )
        return None

    # BUG-TT-08 FIX: verify via webcast API before returning.
    # Optimistic on API failure (returns True) so we don't block valid streams.
    if not _verify_room_alive(room_id, username, proxy=proxy, cookie_file=cookie_file):
        logger.debug(
            "tiktok_live_checker: @%s -- roomId=%s found but check_alive=false, not live",
            username, room_id,
        )
        return None

    live_url = f"https://www.tiktok.com/@{username}/live"
    logger.info(
        "tiktok_live_checker: @%s is LIVE -- roomId=%s -> %s",
        username, room_id, live_url,
    )
    return live_url, str(room_id)


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
            scope.get("webapp.user-detail", {})
                 .get("userInfo", {})
                 .get("user", {})
                 .get("roomId")
        )
        if room_id:
            return room_id
        live_room = (
            scope.get("webapp.user-detail", {})
                 .get("userInfo", {})
                 .get("liveRoomInfo")
        )
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
