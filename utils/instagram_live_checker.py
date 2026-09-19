"""
utils/instagram_live_checker.py
Check whether an Instagram user is currently broadcasting a live stream.

Uses Instagram's internal web API — the same endpoint that yt-dlp and
gallery-dl use when authenticated via a Netscape cookie file.

Requests go through curl_cffi Chrome TLS impersonation (shared with
utils/tiktok_live_checker.py) to avoid plain-requests bot detection,
falling back to a plain requests.Session if curl_cffi is unavailable.

Public interface
────────────────
check_instagram_live(username, cookie_file, proxy="", deep=False) -> Optional[str]
    Returns the live URL if the user is currently live, None otherwise.
    Raises RuntimeError on hard errors (auth, bad cookie, network).

is_instagram_profile_url(url) -> bool
    Returns True if url looks like a profile page (not a live/post URL).

extract_instagram_username(url) -> Optional[str]
    Extracts the username from a profile URL.
"""

from __future__ import annotations

import logging
import re
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Optional

from utils.i18n import t
from utils.instagram_http import (
    WWW_API_BASE,
    build_web_headers,
    get_shared_session,
    record_www_claim,
)

logger = logging.getLogger(__name__)

# Instagram web API — the same host/app-id a real browser tab uses.
_PROFILE_API = f"{WWW_API_BASE}/users/web_profile_info/"
_STORY_API = f"{WWW_API_BASE}/feed/user/{{user_id}}/story/"
_REQUEST_TIMEOUT = 15  # seconds

# Profile URL pattern — matches /username/ but NOT /p/, /reel/, /live/, /stories/
_PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/"
    r"(?!p/|reel/|tv/|live/|stories/|explore/|accounts/)"
    r"([A-Za-z0-9._]+)/?(?:\?.*)?$",
    re.I,
)


def is_instagram_profile_url(url: str) -> bool:
    """Return True if *url* is an Instagram profile page (not a live/post URL)."""
    return bool(_PROFILE_RE.match(url.strip()))


def extract_instagram_username(url: str) -> Optional[str]:
    """Extract the username from an Instagram profile URL.

    Returns None if the URL is not a recognisable profile URL.
    """
    m = _PROFILE_RE.match(url.strip())
    return m.group(1).lower() if m else None


def _check_story_broadcast(
    session: "Any",
    user: dict,
    username: str,
    headers: dict,
    proxies: Optional[dict],
    jar: "Any" = None,
) -> bool:
    """Deep fallback: check the story-feed API for an active broadcast.

    Never raises — any failure just means "couldn't confirm live via story
    feed", not a hard error. Used when the profile API's live fields come
    back falsy but the profile API response may be degraded/stripped.
    """
    user_id = user.get("id") or user.get("pk")
    if not user_id:
        return False
    try:
        resp = session.get(
            _STORY_API.format(user_id=user_id),
            headers=headers,
            cookies=jar,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
        )
        record_www_claim(resp.headers)
        data = resp.json()
        return bool(data.get("broadcast"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("instagram_live_checker: story broadcast check failed for @%s: %s", username, exc)
        return False


def check_instagram_live(
    username: str,
    cookie_file: str,
    proxy: str = "",
    deep: bool = False,
) -> Optional[str]:
    """
    Check whether *username* is currently broadcasting an Instagram Live.

    Parameters
    ----------
    username:    Instagram username (without @)
    cookie_file: Path to a Netscape-format .txt cookie file (Instagram cookies)
    proxy:       Optional proxy URL e.g. "http://127.0.0.1:8080"
    deep:        If True and the profile API shows no live fields, also probe
                 the story-feed API for an active broadcast (catches degraded/
                 stripped profile responses). Extra request, so opt-in.

    Returns
    -------
    str:  Live URL (``https://www.instagram.com/{username}/live/``) if live
    None: User is not currently live

    Raises
    ------
    RuntimeError: On authentication failure, network error, or missing cookie
    """
    if not cookie_file or not Path(cookie_file).is_file():
        raise RuntimeError("login: " + t("err.ig_cookie_required"))

    # Load cookies from Netscape file using stdlib MozillaCookieJar
    jar = MozillaCookieJar()
    try:
        jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise RuntimeError("login: " + t("err.cookie_unreadable", err=exc)) from exc

    # Verify we have the required sessionid cookie
    session_cookies = {c.name: c.value for c in jar if "instagram.com" in c.domain}
    if "sessionid" not in session_cookies:
        raise RuntimeError("login: " + t("err.ig_cookie_no_sessionid"))

    # Extract CSRF token from cookies — required by Instagram's internal API
    # since late 2023. Without it the API returns empty user data or 403.
    csrftoken = session_cookies.get("csrftoken") or ""

    # BUG-IG-ANTIBOT: one shared header-builder with instagram_live_engine so
    # the two callers cannot drift into two different, script-shaped clients.
    headers = build_web_headers(referer=f"https://www.instagram.com/{username}/", csrftoken=csrftoken)

    proxies = {"http": proxy, "https": proxy} if proxy else None

    # BUG-IG FIX: plain requests.Session triggers Instagram bot detection via
    # TLS fingerprinting, causing a 429 death spiral even with valid cookies.
    # curl_cffi Chrome impersonation (shared with tiktok_live_checker) mirrors
    # yt-dlp's impersonate=True and avoids the fingerprint-based block.
    # BUG-IG-ANTIBOT: one long-lived shared session per process instead of a
    # fresh TLS handshake on every poll — kept alive across calls, not closed
    # in the finally below.
    session = get_shared_session(jar)
    try:
        # cookies=jar is passed explicitly (not just relied on via the shared
        # session's own mutable cookie store) so a concurrent recording-worker
        # thread mutating that same shared jar can't race this request's
        # cookies -- see get_shared_session()'s docstring.
        resp = session.get(
            _PROFILE_API,
            params={"username": username},
            headers=headers,
            cookies=jar,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
        )
    except Exception as exc:
        # curl_cffi exceptions are not requests.exceptions subclasses —
        # match by message like tiktok_live_checker._fetch_tiktok_profile_page.
        exc_s = str(exc)
        if "connection" in exc_s.lower() or "connect" in exc_s.lower():
            raise RuntimeError(t("err.network", err=exc)) from exc
        if "timeout" in exc_s.lower():
            raise RuntimeError(t("err.ig_api_timeout")) from None
        raise RuntimeError(t("err.http", err=exc)) from exc

    record_www_claim(resp.headers)

    if resp.status_code != 200:
        logger.warning(
            "instagram_live_checker: HTTP %s for @%s (retry-after=%s) body=%r",
            resp.status_code,
            username,
            resp.headers.get("Retry-After"),
            resp.text[:200],
        )

    # Handle auth errors
    if resp.status_code == 401:
        raise RuntimeError("login: " + t("err.ig_cookie_invalid"))
    if resp.status_code == 404:
        raise RuntimeError("not found: " + t("err.ig_account_not_found", username=username))
    if resp.status_code == 429:
        raise RuntimeError("blocked: " + t("err.ig_rate_limited"))
    if resp.status_code == 403:
        raise RuntimeError("login: " + t("err.ig_forbidden"))

    try:
        data = resp.json()
    except ValueError as exc:
        logger.debug(
            "instagram_live_checker: non-JSON response for %s (status %s): %s…",
            username,
            resp.status_code,
            resp.text[:80],
        )
        raise RuntimeError(t("err.ig_bad_response")) from exc

    # --- Parse live status from response -----------------------------------
    # The API returns: {"data": {"user": { ...fields... }}}
    # Live-related fields (Instagram changes these periodically):
    #   is_live:             bool — currently broadcasting
    #   live_broadcast_id:   str  — broadcast ID when live
    #   has_active_broadcast:bool — alternative field name
    user = data.get("data", {}).get("user") or data.get("user") or {}

    if not user:
        logger.warning(
            "instagram_live_checker: empty user data for @%s (status %s). Response keys: %s",
            username,
            resp.status_code,
            list(data.keys()),
        )
        # Not an error — could mean private account or non-existent user
        return None

    # Instagram changes live-status field names periodically.
    # Check all known variants to maximise compatibility.
    is_live = bool(
        user.get("is_live")
        or user.get("has_active_broadcast")
        or user.get("live_broadcast_id")
        # Newer API variants (2024+)
        or user.get("live_broadcast_status") == "active"
        or (user.get("broadcast_count") or 0) > 0
        or bool(user.get("active_live_info"))
    )

    logger.debug(
        "instagram_live_checker: @%s is_live=%s "
        "(is_live=%r, has_active_broadcast=%r, live_broadcast_id=%r, "
        "broadcast_count=%r, active_live_info=%r, live_broadcast_status=%r, "
        "response_keys=%s)",
        username,
        is_live,
        user.get("is_live"),
        user.get("has_active_broadcast"),
        user.get("live_broadcast_id"),
        user.get("broadcast_count"),
        bool(user.get("active_live_info")),
        user.get("live_broadcast_status"),
        list(user.keys())[:15],  # log first 15 keys — helps diagnose future API changes
    )

    if not is_live and deep:
        is_live = _check_story_broadcast(session, user, username, headers, proxies, jar=jar)

    if is_live:
        live_url = f"https://www.instagram.com/{username}/live/"
        logger.info("instagram_live_checker: @%s is LIVE → %s", username, live_url)
        return live_url

    return None
