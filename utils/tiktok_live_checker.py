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
_LIVE_URL_RE = re.compile(
    r"^https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{1,24})/live",
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
    import requests  # already in requirements.txt

    proxies = {"http": proxy, "https": proxy} if proxy else None

    # TikTok requires realistic browser headers — bare requests get 0-byte or
    # redirect responses that yt-dlp also works around.
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

    # Strategy: fetch the user's profile page and check for live room ID in
    # the __NEXT_DATA__ JSON blob — the same data yt-dlp uses.
    # Fallback: webcast check_alive API with room_id if we can extract it.

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
            "Chờ 5–10 phút rồi thử lại."
        )
    if resp.status_code not in (200, 301, 302):
        logger.debug(
            "tiktok_live_checker: unexpected status %s for @%s",
            resp.status_code, username,
        )

    page_text = resp.text

    # ── Strategy 1: look for liveRoomInfo or roomId in __NEXT_DATA__ ─────
    # TikTok embeds a JSON blob in a <script id="__NEXT_DATA__"> tag.
    # When a user is live, this blob contains liveRoomInfo with status=2
    # or a roomId field.

    # Quick pre-check — if neither keyword is present, save regex work.
    if "liveRoomInfo" not in page_text and "roomId" not in page_text:
        logger.debug(
            "tiktok_live_checker: @%s — no liveRoomInfo/roomId in page, not live",
            username,
        )
        return None

    # Extract the JSON blob
    next_data_match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        page_text,
        re.DOTALL,
    )
    if not next_data_match:
        # Fallback: look for the JSON in __UNIVERSAL_DATA__ (newer TikTok pages)
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

    import json
    try:
        page_data = json.loads(next_data_match.group(1))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.debug(
            "tiktok_live_checker: JSON parse error for @%s: %s",
            username, exc,
        )
        return None

    # Navigate the nested data structure — TikTok changes this periodically.
    # Known paths (checked in order of reliability):
    is_live = _extract_live_status(page_data, username)

    if is_live:
        live_url = f"https://www.tiktok.com/@{username}/live"
        logger.info("tiktok_live_checker: @%s is LIVE → %s", username, live_url)
        return live_url

    return None


def _extract_live_status(data: dict, username: str) -> bool:
    """Walk known JSON paths to determine if the user is currently live.

    TikTok changes its page schema periodically. This function checks all
    known paths and returns True if any of them indicates an active live.

    Keeping this as a separate function makes it easy to add new paths
    when TikTok changes the schema without touching the main checker.
    """
    # Path 1: __NEXT_DATA__ → props → pageProps → userInfo → liveRoomInfo
    try:
        live_room = (
            data.get("props", {})
                .get("pageProps", {})
                .get("userInfo", {})
                .get("liveRoomInfo")
        )
        if live_room:
            # status=2 means live; status=4 means ended; absence means not live
            status = live_room.get("status")
            room_id = live_room.get("roomId") or live_room.get("id")
            if status == 2 or (room_id and status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s live via path1 — status=%s roomId=%s",
                    username, status, room_id,
                )
                return True
    except (AttributeError, TypeError):
        pass

    # Path 2: __UNIVERSAL_DATA__ → __DEFAULT_SCOPE__ → webapp.user-detail
    try:
        scope = data.get("__DEFAULT_SCOPE__", {})
        user_detail = scope.get("webapp.user-detail", {})
        user_info = user_detail.get("userInfo", {})
        live_room = user_info.get("liveRoomInfo")
        if live_room:
            status = live_room.get("status")
            room_id = live_room.get("roomId") or live_room.get("id")
            if status == 2 or (room_id and status not in (4, 5)):
                logger.debug(
                    "tiktok_live_checker: @%s live via path2 — status=%s roomId=%s",
                    username, status, room_id,
                )
                return True
    except (AttributeError, TypeError):
        pass

    # Path 3: flat search for roomId + status=2 anywhere in the blob
    # (covers future schema changes at the cost of a full JSON string scan)
    try:
        raw = str(data)
        if '"status": 2' in raw or "'status': 2" in raw:
            # Confirm there's also a roomId nearby to avoid false positives
            if "roomId" in raw or "room_id" in raw:
                logger.debug(
                    "tiktok_live_checker: @%s live via path3 (fallback scan)",
                    username,
                )
                return True
    except Exception:
        pass

    logger.debug(
        "tiktok_live_checker: @%s — checked all paths, not live",
        username,
    )
    return False
