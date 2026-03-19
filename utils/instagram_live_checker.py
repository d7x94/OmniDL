"""
utils/instagram_live_checker.py
Check whether an Instagram user is currently broadcasting a live stream.

Uses Instagram's internal web API — the same endpoint that yt-dlp and
gallery-dl use when authenticated via a Netscape cookie file.

No new dependencies: uses `requests` (already in requirements.txt) and
parses the Netscape cookie format with stdlib only.

Public interface
────────────────
check_instagram_live(username, cookie_file, proxy="") -> Optional[str]
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
from typing import Optional

logger = logging.getLogger(__name__)

# Instagram internal web API — used by yt-dlp and gallery-dl internally.
# Requires the X-IG-App-ID header (Instagram web app ID, public constant).
_PROFILE_API     = "https://i.instagram.com/api/v1/users/web_profile_info/"
_IG_APP_ID       = "936619743392459"
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


def check_instagram_live(
    username: str,
    cookie_file: str,
    proxy: str = "",
) -> Optional[str]:
    """
    Check whether *username* is currently broadcasting an Instagram Live.

    Parameters
    ----------
    username:    Instagram username (without @)
    cookie_file: Path to a Netscape-format .txt cookie file (Instagram cookies)
    proxy:       Optional proxy URL e.g. "http://127.0.0.1:8080"

    Returns
    -------
    str:  Live URL (``https://www.instagram.com/{username}/live/``) if live
    None: User is not currently live

    Raises
    ------
    RuntimeError: On authentication failure, network error, or missing cookie
    """
    import requests  # already in requirements.txt

    if not cookie_file or not Path(cookie_file).is_file():
        raise RuntimeError(
            "login: Cần cookie file Instagram để kiểm tra live status.\n"
            "Cấu hình trong Settings → Network → Cookie file."
        )

    # Load cookies from Netscape file using stdlib MozillaCookieJar
    jar = MozillaCookieJar()
    try:
        jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    except Exception as exc:
        raise RuntimeError(
            f"login: Không đọc được cookie file: {exc}"
        ) from exc

    # Verify we have the required sessionid cookie
    session_cookies = {c.name: c.value for c in jar if "instagram.com" in c.domain}
    if "sessionid" not in session_cookies:
        raise RuntimeError(
            "login: Cookie file không có sessionid Instagram.\n"
            "Export lại cookie file sau khi đăng nhập Instagram."
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "X-IG-App-ID": _IG_APP_ID,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.instagram.com/{username}/",
        "X-Requested-With": "XMLHttpRequest",
    }

    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        resp = requests.get(
            _PROFILE_API,
            params={"username": username},
            headers=headers,
            cookies=jar,
            proxies=proxies,
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Lỗi kết nối mạng: {exc}") from exc
    except requests.exceptions.Timeout:
        raise RuntimeError("Instagram API hết thời gian chờ. Thử lại sau.")
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Lỗi HTTP: {exc}") from exc

    # Handle auth errors
    if resp.status_code == 401:
        raise RuntimeError(
            "login: Cookie Instagram đã hết hạn hoặc không hợp lệ.\n"
            "Refresh cookie file trong Settings → Network."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "not found: Tài khoản @{username} không tìm thấy.".format(
                username=username
            )
        )
    if resp.status_code == 429:
        raise RuntimeError(
            "blocked: Rate limit — Instagram đang chặn tạm thời.\n"
            "Chờ 5–10 phút rồi thử lại."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            "login: Truy cập bị từ chối (403). Cookie có thể đã hết hạn."
        )

    try:
        data = resp.json()
    except ValueError as exc:
        logger.debug(
            "instagram_live_checker: non-JSON response for %s (status %s): %s…",
            username, resp.status_code, resp.text[:80],
        )
        raise RuntimeError(
            "Instagram trả về phản hồi không hợp lệ. "
            "Thử lại sau hoặc kiểm tra cookie file."
        ) from exc

    # --- Parse live status from response -----------------------------------
    # The API returns: {"data": {"user": { ...fields... }}}
    # Live-related fields (Instagram changes these periodically):
    #   is_live:             bool — currently broadcasting
    #   live_broadcast_id:   str  — broadcast ID when live
    #   has_active_broadcast:bool — alternative field name
    user = (
        data.get("data", {}).get("user")
        or data.get("user")
        or {}
    )

    if not user:
        logger.debug(
            "instagram_live_checker: empty user data for %s. "
            "Response keys: %s",
            username, list(data.keys()),
        )
        # Not an error — could mean private account or non-existent user
        return None

    is_live = bool(
        user.get("is_live")
        or user.get("has_active_broadcast")
        or user.get("live_broadcast_id")
    )

    logger.debug(
        "instagram_live_checker: @%s is_live=%s (fields: is_live=%r, "
        "live_broadcast_id=%r)",
        username,
        is_live,
        user.get("is_live"),
        user.get("live_broadcast_id"),
    )

    if is_live:
        live_url = f"https://www.instagram.com/{username}/live/"
        logger.info("instagram_live_checker: @%s is LIVE → %s", username, live_url)
        return live_url

    return None
