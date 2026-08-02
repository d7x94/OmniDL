"""
utils/instagram_http.py
Single source of truth for Instagram request identity, shared by
utils/instagram_live_checker.py and infrastructure/downloader/instagram_live_engine.py
so the two callers cannot drift into two different, script-shaped client
fingerprints. Mirrors the browser tab the session cookie actually came from.
"""

from __future__ import annotations

import threading
from typing import Any, Optional
from urllib.parse import urlparse

from utils.tiktok_live_checker import _CHROME_UA, _get_impersonate_session

IG_WEB_APP_ID = "936619743392459"
IG_ASBD_ID = "359341"
WWW_API_BASE = "https://www.instagram.com/api/v1"

_CDN_SUFFIXES = ("fbcdn.net", "cdninstagram.com")
_CDN_EXACT_HOSTS = {"live-upload.instagram.com"}

_www_claim_lock = threading.Lock()
_www_claim = "0"


def record_www_claim(response_headers: Any) -> None:
    """Capture X-IG-Set-WWW-Claim from a response so the next request can echo it.

    Browsers echo back the rotating claim value; a constant "0" over
    thousands of requests marks the client as non-browser. Silently ignores
    responses without the header (defaults stay at "0").
    """
    if not response_headers:
        return
    value = response_headers.get("X-IG-Set-WWW-Claim") or response_headers.get("x-ig-set-www-claim")
    if value:
        global _www_claim  # noqa: PLW0603
        with _www_claim_lock:
            _www_claim = value


def _current_www_claim() -> str:
    with _www_claim_lock:
        return _www_claim


def build_web_headers(referer: str, csrftoken: str) -> dict[str, str]:
    """Return the full browser-shaped header set for an Instagram web API call."""
    return {
        "User-Agent": _CHROME_UA,
        "X-IG-App-ID": IG_WEB_APP_ID,
        "X-ASBD-ID": IG_ASBD_ID,
        "X-CSRFToken": csrftoken,
        "X-IG-WWW-Claim": _current_www_claim(),
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.instagram.com",
        "Referer": referer,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "sec-ch-ua": '"Chromium";v="146", "Not=A?Brand";v="24", "Google Chrome";v="146"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }


_shared_session_lock = threading.Lock()
_shared_session: Optional[Any] = None


def get_shared_session(jar: Optional[Any] = None) -> Any:
    """Return one long-lived impersonated session per process.

    Reuses the same curl_cffi/requests Session across calls (keep-alive TCP,
    natural cookie evolution) instead of the fresh TLS handshake per poll
    that calling _get_impersonate_session() directly on every check gives.
    """
    global _shared_session  # noqa: PLW0603
    with _shared_session_lock:
        if _shared_session is None:
            _shared_session = _get_impersonate_session(jar)
        elif jar is not None:
            try:
                # Clear first -- .update() alone never drops a cookie that
                # existed in a previous jar but not this one (e.g. the user
                # refreshed their cookie export), leaving it stale on the
                # shared session indefinitely.
                _shared_session.cookies.clear()
                _shared_session.cookies.update(jar)
            except Exception:
                pass
        return _shared_session


def close_shared_session() -> None:
    """Release the process-wide session (native libcurl handle) at shutdown."""
    global _shared_session  # noqa: PLW0603
    with _shared_session_lock:
        if _shared_session is not None:
            try:
                _shared_session.close()
            except Exception:
                pass
            _shared_session = None


def is_ig_cdn_host(url: str) -> bool:
    """True for Instagram/Facebook CDN hosts that must never see the session cookie."""
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not hostname:
        return False
    if hostname in _CDN_EXACT_HOSTS:
        return True
    for suffix in _CDN_SUFFIXES:
        if hostname == suffix or hostname.endswith("." + suffix):
            return True
    return hostname.startswith("scontent") and hostname.endswith(".instagram.com")


# Allowlist, not a denylist: fail closed. A future Instagram web client
# header we haven't seen yet (a new auth/identity header) must never reach
# the CDN by default -- only these are known-safe to forward.
_CDN_ALLOWED_HEADER_PREFIXES = ("user-agent", "referer", "origin", "accept", "range")


def strip_cdn_headers(headers: dict) -> dict:
    """Keep only headers safe to forward to the CDN.

    Allowlists User-Agent, Referer, Origin, Accept*, Range; drops everything
    else (Cookie, X-IG-*, X-CSRFToken, X-Requested-With, and any other
    identity/auth header) rather than trying to enumerate every header that
    must never reach the CDN.
    """
    return {k: v for k, v in headers.items() if k.lower().startswith(_CDN_ALLOWED_HEADER_PREFIXES)}
