"""
utils/tiktok_live_checker.py
Check whether a TikTok user is currently broadcasting a live stream.

Uses TikTok's internal web API — the same endpoint yt-dlp uses when
checking live status for a user profile URL.

No new dependencies: uses `requests` (already in requirements.txt).

Public interface
────────────────
check_tiktok_live(username, proxy="") -> Optional[str]
    Returns the live URL if the user is currently live, None otherwise.
    Raises RuntimeError on hard errors (network, rate-limit, not found).

is_tiktok_profile_url(url) -> bool
    Returns True if url looks like a TikTok profile page (not a live/video URL).

extract_tiktok_username(url) -> Optional[str]
    Extracts the username from a TikTok profile URL.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# TikTok internal web API for live status.
# webcast/room/check_alive is the lightest endpoint — returns live status
# without downloading the full user profile JSON.
_LIVE_CHECK_API  = "https://www.tiktok.com/api/live/detail/"
_WEBCAST_API     = "https://webcast.tiktok.com/webcast/room/check_alive/"
_REQUEST_TIMEOUT = 15  # seconds

# Profile URL pattern — matches /@username but NOT /live/, /video/, /tag/, etc.
# TikTok usernames: letters, digits, underscores, dots (1–24 chars).
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


def extract_tiktok_username_from_live_url(url: str) -> Optional[str]:
    """Extract the username from a canonical TikTok /live URL.

    Returns None if the URL is not a recognised /live URL.
    Does NOT resolve short links — call _resolve_short_link first if needed.
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
    import requests

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    def _is_canonical(resolved: str) -> bool:
        """True if resolved URL looks like a proper TikTok content URL."""
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
        # HEAD gave a non-canonical result (e.g. /?_r=1) — retry with GET.
        logger.debug(
            "tiktok_live_checker: HEAD resolved to non-canonical %s, retrying with GET",
            final,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("tiktok_live_checker: HEAD failed for %s: %s", url, exc)

    # GET fallback — stream=True so we don't download the body.
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
) -> Optional[str]:
    """
    Check whether *username* is currently broadcasting a TikTok LIVE.

    Does NOT require cookies — TikTok's live-check API is public for now.
    If the API is gated in the future, the caller will receive a RuntimeError
    with a clear message.

    Parameters
    ----------
    username:  TikTok username (without @)
    proxy:     Optional proxy URL e.g. "http://127.0.0.1:8080"

    Returns
    -------
    str:  Live URL (``https://www.tiktok.com/@{username}/live``) if live
    None: User is not currently live

    Raises
    ------
    RuntimeError: On network error, rate-limit, or unrecognised API response.
    """
    result = _check_tiktok_live_with_room_id(username, proxy=proxy)
    if result is None:
        return None
    live_url, _room_id = result
    return live_url


def _fetch_tiktok_profile_page(username: str, proxy: str = "") -> Optional[str]:
    """Fetch TikTok profile page HTML. Returns page text or None on error."""
    import requests  # already in requirements.txt

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.tiktok.com/@{username}",
        "Origin": "https://www.tiktok.com",
    }
    profile_url = f"https://www.tiktok.com/@{username}"
    try:
        resp = requests.get(
            profile_url,
            headers=headers,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Lỗi kết nối mạng: {exc}") from exc
    except requests.exceptions.Timeout:
        raise RuntimeError("TikTok API hết thời gian chờ. Thử lại sau.") from None
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Lỗi HTTP: {exc}") from exc

    if resp.status_code == 404:
        raise RuntimeError(
            f"not found: Tài khoản @{username} không tìm thấy trên TikTok."
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "blocked: TikTok đang rate-limit tạm thời.\\n"
            "Chờ 5-10 phút rồi thử lại."
        )
    if resp.status_code not in (200, 301, 302):
        logger.debug(
            "tiktok_live_checker: unexpected status %s for @%s",
            resp.status_code, username,
        )
    return resp.text


def _check_tiktok_live_with_room_id(
    username: str,
    proxy: str = "",
) -> "Optional[tuple[str, str]]":
    """Internal: returns (live_url, room_id) if live, None if not live.

    BUG-TT-06 FIX: yt-dlp's TikTokLiveIE scrapes the profile page to get
    roomId, then calls webcast.tiktok.com/webcast/room/info.  TikTok now
    frequently returns profile pages where roomId is absent even during an
    active stream (bot-detection / schema change), causing UserNotLive.

    By extracting room_id ourselves and passing it via the mobile share URL
    (m.tiktok.com/share/live/<room_id>), yt-dlp skips the profile-page scrape
    entirely and calls the webcast API directly with the known room_id.

    Raises RuntimeError on network errors (propagated from _fetch_tiktok_profile_page).
    """
    import json  # noqa: PLC0415

    page_text = _fetch_tiktok_profile_page(username, proxy=proxy)
    if page_text is None:
        return None

    # Quick pre-check — if neither keyword is present, save regex work.
    if "liveRoomInfo" not in page_text and "roomId" not in page_text:
        logger.debug(
            "tiktok_live_checker: @%s — no liveRoomInfo/roomId in page, not live",
            username,
        )
        return None

    # Extract JSON blob — try __NEXT_DATA__ first, then __UNIVERSAL_DATA__.
    next_data_match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        page_text,
        re.DOTALL,
    )
    if not next_data_match:
        next_data_match = re.search(
            r'<script[^>]+id="__UNIVERSAL_DATA__"[^>]*>(.*?)</script>',
            page_text,
            re.DOTALL,
        )

    if not next_data_match:
        logger.debug(
            "tiktok_live_checker: @%s — found live keywords but no data blob, "
            "treating as not live",
            username,
        )
        return None

    try:
        page_data = json.loads(next_data_match.group(1))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.debug(
            "tiktok_live_checker: JSON parse error for @%s: %s",
            username, exc,
        )
        return None

    room_id = _extract_live_room_id(page_data, username)
    if not room_id:
        return None

    live_url = f"https://www.tiktok.com/@{username}/live"
    logger.info(
        "tiktok_live_checker: @%s is LIVE — roomId=%s -> %s",
        username, room_id, live_url,
    )
    return live_url, str(room_id)


def _extract_live_room_id(data: dict, username: str) -> "Optional[str]":
    """Walk known JSON paths and return room_id if user is currently live.

    Returns the room_id string if live (status==2 or room_id present and not
    ended), None otherwise.

    BUG-TT-06: replaces _extract_live_status(bool) so callers can get the
    room_id for use with m.tiktok.com/share/live/<room_id> URLs, bypassing
    yt-dlp's profile-page scrape which TikTok now frequently blocks.
    """
    # Path 1: __NEXT_DATA__ -> props -> pageProps -> userInfo -> liveRoomInfo
    try:
        live_room = (
            data.get("props", {})
                .get("pageProps", {})
                .get("userInfo", {})
                .get("liveRoomInfo")
        )
        if live_room:
            status = live_room.get("status")
            room_id = live_room.get("roomId") or live_room.get("id")
            if room_id and (status == 2 or status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s live via path1 — status=%s roomId=%s",
                    username, status, room_id,
                )
                return str(room_id)
    except (AttributeError, TypeError):
        pass

    # Path 2: __UNIVERSAL_DATA__ -> __DEFAULT_SCOPE__ -> webapp.user-detail
    try:
        scope = data.get("__DEFAULT_SCOPE__", {})
        user_detail = scope.get("webapp.user-detail", {})
        user_info = user_detail.get("userInfo", {})
        live_room = user_info.get("liveRoomInfo")
        if live_room:
            status = live_room.get("status")
            room_id = live_room.get("roomId") or live_room.get("id")
            if room_id and (status == 2 or status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s live via path2 — status=%s roomId=%s",
                    username, status, room_id,
                )
                return str(room_id)
    except (AttributeError, TypeError):
        pass

    # Path 3: deep-walk all dict nodes for status==2 AND roomId in same node.
    try:
        stack = [data]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            if node.get("status") == 2 and node.get("roomId"):
                room_id = node["roomId"]
                logger.debug(
                    "tiktok_live_checker: @%s live via path3 — roomId=%s",
                    username, room_id,
                )
                return str(room_id)
            stack.extend(node.values())
    except (AttributeError, TypeError, RecursionError):
        pass

    logger.debug(
        "tiktok_live_checker: @%s — checked all paths, not live",
        username,
    )
    return None


def _extract_live_status(data: dict, username: str) -> bool:
    """Compatibility wrapper — returns bool. Use _extract_live_room_id for new code."""
    return _extract_live_room_id(data, username) is not None
