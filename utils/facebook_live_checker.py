"""
utils/facebook_live_checker.py
Check whether a Facebook page / profile is currently broadcasting a live video.

Facebook has no public live-status API, so this scrapes the two surfaces a
logged-in browser tab uses:

  1. ``facebook.com/{user}/live/`` — redirects straight to the active broadcast
     when the page is live.
  2. the profile / page HTML — carries ``"is_live_streaming":true`` (and
     variants) inside the embedded JSON blobs when a broadcast is running.

Requests go through curl_cffi Chrome TLS impersonation (shared with
utils/tiktok_live_checker.py) because plain-requests traffic is served a
logged-out shell even with valid cookies.

Public interface
────────────────
check_facebook_live(username, cookie_file, proxy="") -> Optional[str]
    Returns a yt-dlp-downloadable live URL if the user is live, None otherwise.
    Raises RuntimeError on hard errors (auth, bad cookie, network).

is_facebook_profile_url(url) -> bool
    True when url looks like a profile / page (or its /live tab), not a post.

extract_facebook_username(url) -> Optional[str]
    Extracts the page slug or numeric profile id from a profile URL.
"""

from __future__ import annotations

import logging
import re
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Optional

from utils.i18n import t

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 20  # seconds

# Path segments that are Facebook features, never usernames.  Anything in this
# set must not be treated as a profile slug.
_RESERVED = (
    "watch|reel|reels|story\\.php|stories|media|groups|events|marketplace|photo|photo\\.php"
    "|share|sharer|pages|permalink\\.php|video\\.php|videos|gaming|login|help|settings"
    "|business|ads|messages|notifications|bookmarks|search|hashtag|profile\\.php|p"
)

# facebook.com/<slug>            → profile / page
# facebook.com/<slug>/live       → the page's live tab
# facebook.com/people/<name>/<id>
_PROFILE_RE = re.compile(
    r"^https?://(?:[\w-]+\.)?facebook\.com/"
    rf"(?!(?:{_RESERVED})(?:/|\?|$))"
    r"(?:people/[^/?#]+/)?"
    r"([A-Za-z0-9.\-]+)"
    r"/?(?:live/?)?(?:\?.*)?$",
    re.I,
)

# facebook.com/profile.php?id=1234567890  (optionally &sk=live)
_PROFILE_ID_RE = re.compile(
    r"^https?://(?:[\w-]+\.)?facebook\.com/profile\.php\?(?:[^#]*&)?id=(\d+)",
    re.I,
)

# Live markers inside the embedded JSON of a live page.
_LIVE_MARKERS = (
    re.compile(r'"is_live_streaming"\s*:\s*true', re.I),
    re.compile(r'"broadcast_status"\s*:\s*"(?:LIVE|ACTIVE|LIVE_NOW)"', re.I),
    re.compile(r'"live_status"\s*:\s*"(?:LIVE|IS_LIVE|LIVE_NOW)"', re.I),
    re.compile(r'"is_live"\s*:\s*true', re.I),
)

# Video id, tried in order of reliability.
_VIDEO_ID_RES = (
    re.compile(r'"video_id"\s*:\s*"(\d{6,})"'),
    re.compile(r'"broadcast_id"\s*:\s*"(\d{6,})"'),
    re.compile(r"/videos/(\d{6,})"),
    re.compile(r"[?&]v=(\d{6,})"),
    re.compile(r'"post_id"\s*:\s*"(\d{6,})"'),
)

_LOGIN_WALL_RE = re.compile(r"facebook\.com/(?:login|checkpoint)", re.I)


def is_facebook_profile_url(url: str) -> bool:
    """Return True if *url* is a Facebook profile / page URL (not a post)."""
    url = url.strip()
    return bool(_PROFILE_ID_RE.match(url) or _PROFILE_RE.match(url))


def extract_facebook_username(url: str) -> Optional[str]:
    """Return the page slug or numeric profile id from a Facebook profile URL."""
    url = url.strip()
    m = _PROFILE_ID_RE.match(url)
    if m:
        return m.group(1)
    m = _PROFILE_RE.match(url)
    return m.group(1) if m else None


def _load_facebook_cookies(cookie_file: str) -> "MozillaCookieJar":
    """Load a Netscape cookie file and verify it carries a Facebook session."""
    if not cookie_file or not Path(cookie_file).is_file():
        raise RuntimeError("login: " + t("err.fb_cookie_required"))

    jar = MozillaCookieJar()
    try:
        jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise RuntimeError("login: " + t("err.cookie_unreadable", err=exc)) from exc

    names = {c.name for c in jar if "facebook.com" in c.domain}
    # c_user is the logged-in account id; xs is the session token.  Facebook
    # serves a logged-out shell (no live markers at all) without both.
    if "c_user" not in names or "xs" not in names:
        raise RuntimeError("login: " + t("err.fb_cookie_no_session"))
    return jar


def _find_video_id(text: str) -> str:
    for rx in _VIDEO_ID_RES:
        m = rx.search(text)
        if m:
            return m.group(1)
    return ""


def _get(session: "Any", url: str, headers: dict, proxies: Optional[dict], jar: "Any") -> "Any":
    try:
        return session.get(
            url,
            headers=headers,
            cookies=jar,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:
        # curl_cffi exceptions are not requests.exceptions subclasses — match
        # by message, same as tiktok_live_checker._fetch_tiktok_profile_page.
        exc_s = str(exc).lower()
        if "timeout" in exc_s:
            raise RuntimeError(t("err.fb_api_timeout")) from None
        if "connect" in exc_s:
            raise RuntimeError(t("err.network", err=exc)) from exc
        raise RuntimeError(t("err.http", err=exc)) from exc


def check_facebook_live(
    username: str,
    cookie_file: str,
    proxy: str = "",
) -> Optional[str]:
    """
    Check whether *username* is currently broadcasting a Facebook Live.

    Parameters
    ----------
    username:    Page slug (``fb.com/<slug>``) or numeric profile id
    cookie_file: Path to a Netscape-format .txt cookie file with Facebook cookies
    proxy:       Optional proxy URL e.g. "http://127.0.0.1:8080"

    Returns
    -------
    str:  Live video URL when the page is broadcasting
    None: Not currently live

    Raises
    ------
    RuntimeError: On authentication failure, rate limit, or network error.
                  The message is prefixed ("login: ", "blocked: ", "not found: ")
                  so LiveMonitorService can classify it without parsing text.
    """
    jar = _load_facebook_cookies(cookie_file)

    from utils.tiktok_live_checker import _CHROME_UA, _get_impersonate_session

    headers = {
        "User-Agent": _CHROME_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    session = _get_impersonate_session(jar)

    # Every unclosed curl_cffi Session pins a libcurl easy handle whose native
    # memory CPython's GC thresholds cannot see.  LiveMonitorService calls this
    # once per interval per watched page, so leaking one per check grows without
    # bound.  Same finally-close as tiktok_live_checker._fetch_tiktok_profile_page;
    # both response bodies are fully buffered before the close.
    try:
        # Probe the /live tab first: when the page is broadcasting Facebook
        # redirects it to the live video itself, which gives us the id for free.
        resp = _get(session, f"https://www.facebook.com/{username}/live/", headers, proxies, jar)

        status = getattr(resp, "status_code", 0)
        if status == 404:
            raise RuntimeError("not found: " + t("err.fb_page_not_found", username=username))
        if status == 429:
            raise RuntimeError("blocked: 429 " + t("err.fb_rate_limited"))
        if status in (401, 403):
            raise RuntimeError("login: " + t("err.fb_cookie_invalid"))

        final_url = str(getattr(resp, "url", "") or "")
        if _LOGIN_WALL_RE.search(final_url):
            raise RuntimeError("login: " + t("err.fb_cookie_invalid"))

        html = resp.text or ""
        is_live = any(rx.search(html) for rx in _LIVE_MARKERS)
        video_id = ""

        if is_live:
            # The redirect target is the most trustworthy id source when present.
            video_id = _find_video_id(final_url) or _find_video_id(html)
        else:
            # /live/ can render the page's past-broadcast list instead of redirecting.
            # Fall back to the profile page, whose header JSON carries the marker.
            resp2 = _get(session, f"https://www.facebook.com/{username}", headers, proxies, jar)
            if getattr(resp2, "status_code", 0) == 429:
                raise RuntimeError("blocked: 429 " + t("err.fb_rate_limited"))
            html2 = resp2.text or ""
            if _LOGIN_WALL_RE.search(str(getattr(resp2, "url", "") or "")):
                raise RuntimeError("login: " + t("err.fb_cookie_invalid"))
            if any(rx.search(html2) for rx in _LIVE_MARKERS):
                is_live = True
                video_id = _find_video_id(html2)
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001 — close must never mask the real error
            pass

    logger.debug(
        "facebook_live_checker: %s is_live=%s video_id=%s (final_url=%s, html=%d bytes)",
        username,
        is_live,
        video_id or "-",
        final_url[:120],
        len(html),
    )

    if not is_live:
        return None

    if not video_id:
        # A live marker with no resolvable id cannot be handed to yt-dlp —
        # report "not live" rather than enqueueing a task that must fail.
        logger.info("facebook_live_checker: %s is live but no video id found", username)
        return None

    # /<user>/videos/<id> is the form yt-dlp's FacebookIE._VALID_URL matches.
    live_url = f"https://www.facebook.com/{username}/videos/{video_id}"
    logger.info("facebook_live_checker: %s is LIVE → %s", username, live_url)
    return live_url
