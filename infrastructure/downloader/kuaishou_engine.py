"""
infrastructure/downloader/kuaishou_engine.py
============================================
Kuaishou downloader — bypasses yt-dlp entirely.

Root cause of previous failure
-------------------------------
Kuaishou GraphQL (/graphql visionVideoDetail) returns photo=null when called
without a valid `did` (device-ID) cookie in the session. The engine was calling
the API cookie-less, so every response came back with photo=null.

Extraction strategy (tried in order)
--------------------------------------
A. HTML page scrape — GET kuaishou.com/short-video/<id> → parse __NEXT_DATA__
   JSON embedded by the Next.js SSR renderer. Works for public videos with zero
   auth. This is the primary strategy.

B. GraphQL with cookie — POST /graphql with the user's Kuaishou cookie file
   (Netscape .txt or DPAPI .enc). Used when strategy A fails (e.g. private videos).

C. kwai.com API — POST to the international Kuaishou feed API. Alternative
   endpoint structure that sometimes works when www.kuaishou.com is rate-limited.

Short-URL resolution
--------------------
v.kuaishou.com/* short-links are resolved via HTTP redirect following.
Timeout increased to 30s (was 20s) to handle slow CN redirect chains.

URL patterns supported
----------------------
  v.kuaishou.com/<code>
  www.kuaishou.com/short-video/<id>
  www.kuaishou.com/f/<code>
  m.kuaishou.com/short-video/<id>
  www.kuaishou.com/video/<id>

Dependencies
------------
- requests (already in requirements.txt)
- curl_cffi (optional — Chrome TLS impersonation; strongly recommended)

Security
--------
- shell=False on all subprocess calls
- No user data logged (CDN URLs truncated to 80 chars)
- Cookie file validated via CWE-22 guard in yt_dlp_engine._resolve_cookie
- Strategy E (CDP) launches the user's real Brave/Chrome profile with an
  unauthenticated --remote-debugging-port. For the lifetime of that run, any
  local process able to reach the port can drive the whole profile — every
  site's cookies/sessions, not only Kuaishou's — and --disable-gpu-sandbox
  also narrows the renderer sandbox. This is inherent to the CDP-intercept
  approach (shared with facebook_story_engine), not a bug to fix here; kept
  as a deliberate trade-off since it only runs locally and briefly.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading as _threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from utils.i18n import t

logger = logging.getLogger(__name__)


def _sanitise_cookie_header(cookie_str: str) -> str:
    """SEC-KS-02: Return a redacted version of a cookie string safe to log.

    Replaces each cookie value with <redacted> so that cookie data cannot
    appear in debug log files even when log level is DEBUG.

    Example:
        "did=abc123; userId=xyz"  ->  "did=<redacted>; userId=<redacted>"
    """
    if not cookie_str:
        return ""
    parts = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, _ = part.partition("=")
            parts.append(f"{k.strip()}=<redacted>")
        else:
            parts.append(part)
    return "; ".join(parts)


# Serialize CDP browser launches — only one Brave instance at a time.
# Two concurrent requests (desktop + remote API) launching Brave simultaneously
# causes ECONNREFUSED on the second instance because the first holds the profile lock.
_CDP_LOCK = _threading.Lock()

# ── URL patterns ──────────────────────────────────────────────────────────────

_KUAISHOU_RE = re.compile(
    r"(?:"
    r"v\.kuaishou\.com/"
    r"|(?:www|m)\.kuaishou\.com/(?:short-video|f|video)/"
    r")",
    re.I,
)

_PHOTO_ID_RE = re.compile(
    r"kuaishou\.com/(?:short-video|video)/([A-Za-z0-9_-]+)",
    re.I,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_RESOLVE_TIMEOUT = 15  # seconds — HEAD almost always fails from non-CN; 15s is enough
_API_TIMEOUT = 25  # seconds — GraphQL / page fetch
_DL_TIMEOUT = 30  # seconds — CDN connect timeout

# Total wall-clock budget for extract_info_kuaishou (strategies A-D + CDP).
# Must stay comfortably under api/server.py's 180s /api/analyse deadline so
# the client never times out while strategy E is still running.
_EXTRACT_BUDGET_S = 160.0

_GQL_URL = "https://www.kuaishou.com/graphql"

_GQL_QUERY = (
    # BUG-KS-05 FIX: API schema declares $type as String, not Int.
    # Passing Int literal caused HTTP 400 "Variable '$type' of type 'Int' used
    # in position expecting type 'String'" on every call, making strategy B
    # permanently dead. Fix: declare as String and pass string value "0".
    "query visionVideoDetail($photoId: String, $type: String) {"
    "  visionVideoDetail(photoId: $photoId, type: $type) {"
    "    photo { id caption duration coverUrl photoUrl videoResource }"
    "    author { name }"
    "    status"
    "  }"
    "}"
)

_KWAI_FEED_URL = "https://www.kwai.com/rest/infra/wd/photo/query_photo_info"

_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.kuaishou.com/",
}

_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://www.kuaishou.com",
    "Referer": "https://www.kuaishou.com/",
    "X-Kpf": "PC_WEB",
}

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.S,
)

# ── Public helpers ────────────────────────────────────────────────────────────


def is_kuaishou_url(url: str) -> bool:
    return bool(_KUAISHOU_RE.search(url))


# ── Session factory ───────────────────────────────────────────────────────────


def _get_impersonate_string() -> str:
    """Return the curl_cffi impersonate string, pinned to a specific Chrome
    version so it matches the hardcoded UA in _PAGE_HEADERS/_API_HEADERS.

    BrowserTypeLiteral is a typing.Literal, not a dict — the previous probe
    checked isinstance(target_map, dict) against it, which is always False,
    so this always fell through to the bare "chrome" alias (whatever
    curl_cffi's current DEFAULT_CHROME happens to be — chrome146 as of
    curl_cffi 0.16, up from chrome136). Pin explicitly instead of relying on
    that alias, so a curl_cffi upgrade can't silently move the TLS
    fingerprint out from under the UA strings below.
    """
    try:
        import typing

        from curl_cffi.requests.impersonate import BrowserTypeLiteral  # noqa: PLC0415

        if "chrome131" in typing.get_args(BrowserTypeLiteral):
            return "chrome131"
    except Exception:
        pass
    return "chrome"


def _make_session() -> Any:
    # Cookies are attached per-request via an explicit "Cookie" header (see each
    # strategy / download call site) rather than a session-level cookie jar.
    # A jar entry needs a single fixed `domain=`, but requests here span multiple
    # hosts (www.kuaishou.com, kwai.com, assorted CDN hosts) — a jar would either
    # under-send (wrong domain) or over-send (leak across unrelated hosts).
    try:
        from curl_cffi import requests as cffi_req  # noqa: PLC0415

        _imp = _get_impersonate_string()
        session: Any = cffi_req.Session(impersonate=_imp)  # type: ignore[arg-type]
        logger.debug("Kuaishou: curl_cffi session (Chrome impersonation)")
        return session
    except ImportError:
        pass

    import requests as req  # noqa: PLC0415

    session = req.Session()
    logger.debug("Kuaishou: requests session (no TLS impersonation)")
    return session


# ── Short-URL resolution ──────────────────────────────────────────────────────


def _resolve_short_url(url: str, session) -> str:
    if "v.kuaishou.com" not in url.lower():
        return url

    for method in ("HEAD", "GET"):
        try:
            fn = session.head if method == "HEAD" else session.get
            kwargs: dict = {"allow_redirects": True, "timeout": _RESOLVE_TIMEOUT}
            if method == "GET":
                kwargs["headers"] = {**_PAGE_HEADERS, "Accept": "text/html,*/*"}
            resp = fn(url, **kwargs)
            final = str(resp.url)
            if final and final != url and "kuaishou.com" in final:
                logger.debug(
                    "Kuaishou: short URL resolved (%s): %s -> %s",
                    method,
                    url,
                    final[:80],
                )
                return final
        except Exception as exc:
            logger.debug("Kuaishou: short URL resolve %s failed: %s", method, exc)

    logger.warning(
        "Kuaishou: could not resolve short URL %s — photo_id will be wrong (short code, not real ID)",
        url,
    )
    return url


def _extract_photo_id(url: str) -> Optional[str]:
    m = _PHOTO_ID_RE.search(url)
    if m:
        return m.group(1)
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return parts[-1] if parts else None


# ── Strategy A: HTML __NEXT_DATA__ scrape ────────────────────────────────────


def _strategy_html(session, photo_id: str, cookie_str: str = "") -> tuple | None:
    page_url = f"https://www.kuaishou.com/short-video/{photo_id}"
    headers = dict(_PAGE_HEADERS)
    if cookie_str:
        headers["Cookie"] = cookie_str
    try:
        resp = session.get(page_url, headers=headers, timeout=_API_TIMEOUT)
    except Exception as exc:
        logger.debug("Kuaishou strategy A: GET failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug("Kuaishou strategy A: HTTP %d", resp.status_code)
        return None

    html = resp.text
    logger.debug("Kuaishou strategy A: HTML len=%d", len(html))
    m = _NEXT_DATA_RE.search(html)
    if m:
        try:
            nd = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("Kuaishou strategy A: __NEXT_DATA__ JSON parse failed: %s", exc)
            nd = {}

        pp = nd.get("props", {}).get("pageProps", {})
        candidates = [
            pp.get("initialState", {}).get("singlePhotoPage", {}).get("photo"),
            pp.get("photoDetail", {}).get("photo"),
            pp.get("photo"),
        ]
        photo = next((c for c in candidates if c), None)
        if photo:
            author = pp.get("initialState", {}).get("singlePhotoPage", {}).get("author") or {}
            return photo, author

        logger.debug(
            "Kuaishou strategy A: photo not in __NEXT_DATA__; pageProps keys: %s; initialState keys: %s",
            list(pp.keys()),
            list((pp.get("initialState") or {}).keys())[:10],
        )
    else:
        logger.debug("Kuaishou strategy A: __NEXT_DATA__ tag not found in HTML")

    # Fallback: window.__INITIAL_STATE__
    m2 = re.search(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});\s*(?:</script>|window\.)",
        html,
        re.S,
    )
    if m2:
        try:
            state = json.loads(m2.group(1))
            photo = state.get("singlePhotoPage", {}).get("photo") or state.get("photoDetail", {}).get("photo")
            if photo:
                author = state.get("singlePhotoPage", {}).get("author") or {}
                return photo, author
        except (json.JSONDecodeError, ValueError):
            pass

    logger.debug("Kuaishou strategy A: no usable data found in page HTML")

    # CSR fallback: scan raw HTML for CDN video URLs directly embedded in JS.
    # BUG-KS-03 FIX: The previous `max(..., key=len)` picked the longest URL
    # which could be a static placeholder/example URL embedded in the JS bundle
    # (e.g. framework demo assets, error page assets) that is longer than the
    # real CDN URL. We now apply two filters before selecting:
    #   1. Exclude URLs whose path contains known JS-framework or error-page
    #      segments that Kuaishou embeds in their Next.js bundle.
    #   2. Exclude URLs that appear inside a `<script src=` or `import(...)` call
    #      (they are JS assets, not video CDN links).
    # After filtering we still prefer the longest remaining URL as a proxy for
    # highest-quality (highest-bitrate) variant.
    cdn_re = re.compile(
        r'"(https://[^"]*\.(?:kuaishou|ksapisrv|ali-ec|bd-api)[^"]*\.mp4[^"]*)"',
        re.I,
    )
    cdn_matches = cdn_re.findall(html)
    if cdn_matches:
        _JS_JUNK_RE = re.compile(
            r"/(?:_next|static|assets|chunks|webpack|node_modules|vendor|"
            r"placeholder|sample|demo|example|error|404|500|favicon)/",
            re.I,
        )
        filtered = [u for u in cdn_matches if not _JS_JUNK_RE.search(u)]
        # Fall back to unfiltered list only if all matches were excluded
        candidates = filtered if filtered else cdn_matches
        best = max(candidates, key=len)
        logger.debug(
            "Kuaishou strategy A: CDN URL found via HTML scan (%d matches, %d after filter): %s",
            len(cdn_matches),
            len(candidates),
            best[:80],
        )
        synthetic_photo = {"id": photo_id, "caption": "", "duration": 0, "coverUrl": "", "photoUrl": best}
        return synthetic_photo, {}

    return None


# ── Strategy B: GraphQL with cookie ──────────────────────────────────────────


def _warmup_session(session) -> None:
    """GET kuaishou.com homepage to receive did/userId Set-Cookie headers.
    Silently ignores failures — warm up is best-effort.
    """
    try:
        session.get(
            "https://www.kuaishou.com/",
            headers=_PAGE_HEADERS,
            timeout=10,
            allow_redirects=True,
        )
        logger.debug("Kuaishou: session warm-up complete")
    except Exception as exc:
        logger.debug("Kuaishou: session warm-up failed (non-fatal): %s", exc)


def _strategy_gql(session, photo_id: str, cookie_str: str = "") -> tuple | None:
    _warmup_session(session)
    payload = {
        "operationName": "visionVideoDetail",
        "variables": {"photoId": photo_id, "type": "0"},
        "query": _GQL_QUERY,
    }
    headers = dict(_API_HEADERS)
    if cookie_str:
        headers["Cookie"] = cookie_str
    try:
        resp = session.post(_GQL_URL, json=payload, headers=headers, timeout=_API_TIMEOUT)
    except Exception as exc:
        logger.debug("Kuaishou strategy B: POST failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug(
            "Kuaishou strategy B: HTTP %d body=%s",
            resp.status_code,
            resp.text[:120],
        )
        return None

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None

    vvd = data.get("data", {}).get("visionVideoDetail") or {}
    photo = vvd.get("photo")
    if not photo:
        logger.debug(
            "Kuaishou strategy B: photo=null (did cookie missing or invalid); status=%s errors=%s",
            vvd.get("status"),
            data.get("errors"),
        )
        return None

    return photo, vvd.get("author") or {}


# ── Strategy C: kwai.com international API ────────────────────────────────────


def _strategy_kwai(session, photo_id: str, cookie_str: str = "") -> tuple | None:
    payload = {"photoId": photo_id, "pageSource": "PROFILE"}
    kwai_headers = {
        **_API_HEADERS,
        "Origin": "https://www.kwai.com",
        "Referer": f"https://www.kwai.com/short-video/{photo_id}",
    }
    if cookie_str:
        kwai_headers["Cookie"] = cookie_str
    try:
        resp = session.post(_KWAI_FEED_URL, json=payload, headers=kwai_headers, timeout=_API_TIMEOUT)
    except Exception as exc:
        logger.debug("Kuaishou strategy C: POST failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug("Kuaishou strategy C: HTTP %d", resp.status_code)
        return None

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None

    photo = data.get("photo") or data.get("data", {}).get("photo")
    if not photo:
        logger.debug("Kuaishou strategy C: photo not in response")
        return None

    author = data.get("author") or data.get("data", {}).get("author") or {}
    return photo, author


# ── Strategy D: www.kuaishou.com REST info API ───────────────────────────────

_SHORT_VIDEO_API_URL = "https://www.kuaishou.com/api/short-video/info"


def _strategy_mobile(session, photo_id: str, cookie_str: str = "") -> tuple | None:
    """kuaishou.com REST info API — GET-based, separate from GraphQL WAF rules."""
    headers = {
        **_API_HEADERS,
        "Referer": f"https://www.kuaishou.com/short-video/{photo_id}",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    try:
        resp = session.get(
            _SHORT_VIDEO_API_URL,
            params={"photoId": photo_id},
            headers=headers,
            timeout=_API_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("Kuaishou strategy D: GET failed (%s)", exc)
        return None

    if resp.status_code != 200:
        logger.debug("Kuaishou strategy D: HTTP %d", resp.status_code)
        return None

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return None

    photo = data.get("photo") or data.get("data", {}).get("photo") or data.get("result", {}).get("photo")
    if not photo:
        logger.debug("Kuaishou strategy D: photo not in response; keys=%s", list(data.keys())[:8])
        return None

    author = (
        data.get("author") or data.get("data", {}).get("author") or data.get("result", {}).get("author") or {}
    )
    return photo, author


def _pick_best_video_url(photo: dict) -> Optional[str]:
    # photoUrl — direct MP4 (replaces deprecated mainMvUrls)
    photo_url = (photo.get("photoUrl") or "").strip()
    if photo_url.startswith("http"):
        logger.debug("Kuaishou: photoUrl CDN: %s", photo_url[:80])
        return photo_url

    # mainMvUrls — kept for backward compatibility with cached/old responses
    for entry in photo.get("mainMvUrls") or []:
        url = (entry.get("url") or "").strip()
        if url.startswith("http"):
            logger.debug("Kuaishou: mainMvUrls CDN: %s", url[:80])
            return url

    # NOTE: hlsPlayUrl (m3u8 playlist) is intentionally not used — download()
    # streams the URL as a raw byte copy, which cannot mux an HLS playlist
    # into a valid MP4. Using it here made _is_valid_mp4 fail downstream and
    # reported a misleading "CDN URL expired" error.

    # videoResource — GQL returns this as opaque scalar JSON (dict or JSON string)
    try:
        vr = photo.get("videoResource")
        if isinstance(vr, str):
            import json as _json  # noqa: PLC0415

            vr = _json.loads(vr)
        if isinstance(vr, dict):
            best_url: Optional[str] = None
            best_bitrate = -1
            h264 = vr.get("h264") or {}
            for adaptation in h264.get("adaptationSet") or []:
                for rep in adaptation.get("representation") or []:
                    bitrate = rep.get("avgBitrate") or 0
                    url = (rep.get("url") or "").strip()
                    if url.startswith("http") and bitrate > best_bitrate:
                        best_bitrate = bitrate
                        best_url = url
            if best_url:
                logger.debug(
                    "Kuaishou: videoResource h264 (bitrate=%d): %s",
                    best_bitrate,
                    best_url[:80],
                )
                return best_url
    except (KeyError, TypeError, ValueError):
        pass

    return None


# ── Cookie loader ─────────────────────────────────────────────────────────────


def _load_cookie_str(config: ConfigManager) -> str:
    try:
        from http.cookiejar import MozillaCookieJar  # noqa: PLC0415

        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_path = _resolve_cookie("https://www.kuaishou.com/", config)
        if not cookie_path:
            return ""

        usable, is_temp = _prepare_cookie_for_use(cookie_path)
        jar = MozillaCookieJar()
        try:
            jar.load(usable, ignore_discard=True, ignore_expires=True)
        finally:
            if is_temp:
                Path(usable).unlink(missing_ok=True)

        parts = [
            f"{c.name}={c.value}" for c in jar if "kuaishou" in (c.domain or "") or "kwai" in (c.domain or "")
        ]
        cookie_result = "; ".join(parts)
        # SEC-KS-02: never log raw cookie values — log only redacted key names
        logger.debug(
            "Kuaishou: loaded %d cookie(s): %s",
            len(parts),
            _sanitise_cookie_header(cookie_result),
        )
        return cookie_result
    except Exception as exc:
        logger.debug("Kuaishou: cookie load failed (non-fatal): %s", exc)
        return ""


# ── Strategy E: Playwright CDP intercept ─────────────────────────────────────
#
# Root cause of strategies A-D failing:
#   www.kuaishou.com serves a CSR shell page (no embedded data).
#   All API endpoints (GQL, REST, kwai.com) are behind WAF or DNS-blocked from
#   non-CN IPs, or have removed the queried fields from their schema.
#   yt-dlp has no Kuaishou extractor in recent versions.
#
# This strategy opens the user's Brave/Chrome via CDP, navigates to the
# Kuaishou video page, and intercepts the CDN MP4 URL from actual network
# traffic — bypassing WAF, schema changes, and IP blocks entirely.
#
# Kuaishou CDN URL patterns (as of 2026):
#   https://*.ksapisrv.com/.../*.mp4...
#   https://*.kuaishou.com/.../*.mp4...
#   https://ali*.ks-cdn.com/...
#   https://tx*.ks-cdn.com/...
#   https://*.kwaicdn.com/...
#
# The page JS player issues a plain XHR/fetch for the MP4 — page.on("request")
# catches it reliably. No DASH, no Service Worker complexity.

_KS_CDN_RE = re.compile(
    r"(?:"
    r"ksapisrv\.com/[^?#]*\.mp4"
    r"|kuaishou\.com/[^?#]*\.mp4"
    r"|ks-cdn\.com/[^?#]*\.mp4"
    r"|kwaicdn\.com/[^?#]*\.mp4"
    r"|alicdn\.com/[^?#]*\.mp4"
    r"|txmov2\.a\.yximgs\.com/[^?#]*\.mp4"
    r"|ali-safety-video\.acfun\.cn/[^?#]*\.mp4"
    r")",
    re.I,
)

# Broader CDN pattern for response MIME intercept (no .mp4 extension required)
_KS_CDN_HOST_RE = re.compile(
    r"(?:ksapisrv|ks-cdn|kwaicdn|yximgs|alicdn)"
    r"\.(?:com|cn)/",
    re.I,
)

_KS_PRE_PAGE_JS = (
    "(function(){"
    "if(window.__omni_ks_installed)return;"
    "window.__omni_ks_installed=true;"
    "window.__omni_ks_url=null;"
    "function _cap(u){"
    " if(!u||typeof u!=='string')return;"
    " var l=u.toLowerCase();"
    " if(window.__omni_ks_url)return;"
    " if(l.indexOf('.mp4')===-1)return;"
    " if(l.indexOf('ksapisrv')!==-1||l.indexOf('ks-cdn')!==-1"
    "  ||l.indexOf('kwaicdn')!==-1||l.indexOf('kuaishou')!==-1){"
    "  window.__omni_ks_url=u;"
    " }"
    "}"
    "var _f=window.fetch;"
    "window.fetch=function(i,o){_cap(typeof i==='string'?i:(i&&i.url));return _f.apply(this,arguments);};"
    "var _x=XMLHttpRequest.prototype.open;"
    "XMLHttpRequest.prototype.open=function(m,u){_cap(u);return _x.apply(this,arguments);};"
    "})()"
)

_KS_POLL_JS = (
    "(function(){"
    "if(window.__omni_ks_url)return window.__omni_ks_url;"
    "try{"
    " var e=performance.getEntriesByType('resource');"
    " for(var i=0;i<e.length;i++){"
    "  var u=e[i].name,l=u.toLowerCase();"
    "  if(l.indexOf('.mp4')!==-1&&("
    "   l.indexOf('ksapisrv')!==-1||l.indexOf('ks-cdn')!==-1"
    "   ||l.indexOf('kwaicdn')!==-1||l.indexOf('kuaishou')!==-1"
    "  ))return u;"
    " }"
    "}catch(e){}"
    "var vs=document.querySelectorAll('video');"
    "for(var j=0;j<vs.length;j++){"
    " var src=vs[j].currentSrc||vs[j].src||'';"
    " if(src&&src.indexOf('http')===0)return src;"
    "}"
    "return '';"
    "})()"
)


def _is_ks_cdn_url(url: str) -> bool:
    return bool(_KS_CDN_RE.search(url))


def _inject_cookies_cdp(ctx, config: Optional[ConfigManager]) -> None:
    """Inject Kuaishou cookies from the .enc cookie file into a Playwright browser context.

    Without cookies the player shows a login wall and never fires the CDN request.
    Silently skips if config is None, no cookie file configured, or any error occurs.
    """
    if not config:
        return
    try:
        from http.cookiejar import MozillaCookieJar  # noqa: PLC0415

        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_path = _resolve_cookie("https://www.kuaishou.com/", config)
        if not cookie_path:
            return

        usable, is_temp = _prepare_cookie_for_use(cookie_path)
        jar = MozillaCookieJar()
        try:
            jar.load(usable, ignore_discard=True, ignore_expires=True)
        finally:
            if is_temp:
                Path(usable).unlink(missing_ok=True)

        cdp_cookies = []
        for c in jar:
            if "kuaishou" not in (c.domain or "") and "kwai" not in (c.domain or ""):
                continue
            entry: dict = {
                "name": c.name,
                "value": c.value or "",
                "domain": c.domain or ".kuaishou.com",
                "path": c.path or "/",
            }
            # expires: 0 means session cookie in CDP
            if c.expires:
                entry["expires"] = float(c.expires)
            if c.secure:
                entry["secure"] = True
            cdp_cookies.append(entry)

        if cdp_cookies:
            ctx.add_cookies(cdp_cookies)
            logger.debug("Kuaishou CDP: injected %d cookies into browser context", len(cdp_cookies))
        else:
            logger.debug("Kuaishou CDP: cookie file loaded but no kuaishou/kwai cookies found")
    except Exception as exc:
        logger.debug("Kuaishou CDP: cookie inject failed (non-fatal): %s", exc)


def _cdp_caption_from_url(cdn_url: str, page_url: str) -> str:
    """Build a human-readable title from what CDP gives us (no caption from API).

    BUG-KS-04 FIX: The previous implementation only matched the /upic/YYYY/MM/DD/
    path pattern. Kuaishou CDN servers in some regions use different path schemas
    (e.g. /bs2/newsnap-enc/, /uc/YYYYMMDDHHMMSS/, or no date segment at all).
    We now try multiple date-extraction patterns in order of specificity, and fall
    back to a local timestamp so the filename is never just kuaishou_<code>.
    """
    # Pattern 1: /upic/YYYY/MM/DD/ -- most common CN CDN
    dm = re.search(r"/upic/(\d{4})/(\d{2})/(\d{2})/", cdn_url)
    if dm:
        date_str = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
    else:
        # Pattern 2: /uc/YYYYMMDDHHMMSS or /YYYYMMDD embedded in path segment
        dm2 = re.search(r"/(?:uc/)?(\d{4})(\d{2})(\d{2})\d{0,6}(?:/|_|\.)", cdn_url)
        if dm2:
            date_str = f"{dm2.group(1)}-{dm2.group(2)}-{dm2.group(3)}"
        else:
            # Fallback: use current local date so filename always has a date component
            from datetime import date as _date  # noqa: PLC0415

            date_str = _date.today().isoformat()

    # Prefer real photo_id from resolved URL, fall back to short code from original
    pm = re.search(r"kuaishou\.com/(?:short-video|video)/([A-Za-z0-9_-]+)", page_url)
    if not pm:
        pm = re.search(r"v\.kuaishou\.com/([A-Za-z0-9_-]+)", page_url)
    code = pm.group(1) if pm else "video"

    return f"kuaishou_{code}_{date_str}"


def _strategy_cdp(
    page_url: str,
    config: Optional[ConfigManager],
    on_progress: Optional[Callable] = None,
    timeout: float = 45.0,
    cancel_event: Optional[_threading.Event] = None,
) -> tuple | None:
    """Open user's Brave/Chrome via CDP, navigate to Kuaishou page, intercept CDN URL.

    Uses the real browser profile (not isolated) so the browser has its actual
    version string and passes Kuaishou's UA check. Only the one new tab created
    here is closed after capture — other existing tabs are left untouched.

    Serialized via _CDP_LOCK: only one Brave instance at a time. Concurrent calls
    queue here rather than launching two browsers simultaneously (which causes
    ECONNREFUSED on the second instance due to the profile directory lock).
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415, F401
    except ImportError:
        logger.debug("Kuaishou strategy E: playwright not installed — skip")
        return None

    logger.debug("Kuaishou strategy E: waiting for CDP lock...")
    with _CDP_LOCK:
        if cancel_event and cancel_event.is_set():
            logger.debug("Kuaishou strategy E: cancelled while waiting for CDP lock")
            return None
        logger.debug("Kuaishou strategy E: CDP lock acquired")
        return _strategy_cdp_locked(page_url, config, on_progress, timeout, cancel_event)


def _strategy_cdp_locked(
    page_url: str,
    config: Optional[ConfigManager],
    on_progress: Optional[Callable] = None,
    timeout: float = 45.0,
    cancel_event: Optional[_threading.Event] = None,
) -> tuple | None:
    """Inner implementation — called only while _CDP_LOCK is held."""
    from playwright.sync_api import TimeoutError as PWTimeout  # noqa: PLC0415
    from playwright.sync_api import sync_playwright

    # Single absolute deadline used by every phase below.
    _abs_deadline = time.monotonic() + timeout

    _final_page_url = page_url  # updated to real URL after browser redirect

    import os
    import socket as _socket_mod

    def _prog(pct: int, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, "", msg)
            except Exception:
                pass

    # BUG-KS-LINUX FIX: facebook_story_engine._find_browser_exe raises
    # "chỉ hỗ trợ Windows và macOS" on any other platform, but the except
    # clauses below swallowed that and always reported "browser not found" —
    # telling a Linux user to install Brave/Chrome would never fix anything,
    # since strategy E's CDP profile handling only supports win32/darwin.
    if sys.platform not in ("win32", "darwin"):
        raise RuntimeError(t("err.ks_strategy_e_platform"))

    # ── Locate browser + real profile dir ────────────────────────────────────
    try:
        from infrastructure.downloader.facebook_story_engine import (  # noqa: PLC0415
            _find_browser_exe,
        )

        exe = _find_browser_exe("brave")
        browser_name = "brave"
    except Exception:
        try:
            from infrastructure.downloader.facebook_story_engine import (  # noqa: PLC0415
                _find_browser_exe,
            )

            exe = _find_browser_exe("chrome")
            browser_name = "chrome"
        except Exception as exc:
            # BUG-KS-02 FIX: surface a user-readable error instead of silently
            # returning None. When strategies A-D all fail, the user sees the
            # generic "all methods failed" message with no hint about installing
            # a browser. Raise RuntimeError so the caller can display it.
            logger.debug("Kuaishou strategy E: no browser found (%s)", exc)
            raise RuntimeError(t("err.ks_no_browser")) from exc

    # ── Resolve real profile base dir ─────────────────────────────────────────
    # sys.platform is guaranteed win32/darwin here (checked above).
    if sys.platform == "win32":
        local_app = Path(os.environ.get("LOCALAPPDATA", ""))
        if browser_name == "brave":
            profile_base = local_app / "BraveSoftware/Brave-Browser/User Data"
        else:
            profile_base = local_app / "Google/Chrome/User Data"
    else:
        if browser_name == "brave":
            profile_base = Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser"
        else:
            profile_base = Path.home() / "Library/Application Support/Google/Chrome"

    if profile_base.exists():
        try:
            from infrastructure.downloader.facebook_story_engine import (  # noqa: PLC0415
                _clear_crashed_flag,
            )

            _clear_crashed_flag(profile_base)
        except Exception:
            pass

    # ── Free port ─────────────────────────────────────────────────────────────
    with _socket_mod.socket() as _s:
        _s.bind(("127.0.0.1", 0))
        port = _s.getsockname()[1]

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--restore-last-session=false",
        "--no-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        # Avoid GPU pipeline stall on low-end iGPU (e.g. Intel HD 620).
        # CDP sessions do not need hardware rendering.
        "--disable-gpu-sandbox",
        "--disable-software-rasterizer",
    ]
    if profile_base.exists():
        cmd.append(f"--user-data-dir={profile_base}")

    _prog(5, t("progress.ks_browser_start"))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logger.debug("Kuaishou strategy E: browser pid=%d port=%d", proc.pid, port)

    cdn_url: Optional[str] = None

    try:
        with sync_playwright() as pw:
            _prog(8, t("progress.ks_cdp_connect"))
            cdp_browser = None
            # Allow up to 30s for CDP connect but never past the absolute deadline
            # minus 20s buffer for navigation + intercept (was 30s — too tight
            # on slow machines where login redirect + player init takes 60-90s).
            _cdp_connect_deadline = min(
                time.monotonic() + 30.0,
                _abs_deadline - 20.0,
            )
            last_exc = None
            while time.monotonic() < _cdp_connect_deadline:
                try:
                    cdp_browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=3_000)
                    break
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.8)

            if cdp_browser is None:
                logger.debug("Kuaishou strategy E: CDP connect failed: %s", last_exc)
                return None

            ctx = cdp_browser.contexts[0]

            # BUG-KS-CDP-01 FIX: _inject_cookies_cdp was defined but never
            # called. Without cookies the Kuaishou player shows a login wall
            # and never fires the CDN request, causing strategy E to always
            # time out on accounts that require auth to view content.
            _inject_cookies_cdp(ctx, config)

            # Open exactly one new tab — track it so we close only this tab later.
            page = ctx.new_page()

            # Mutable guard: callbacks check this before accepting a CDN URL.
            # Set to the real target pid after goto + redirect resolution.
            # Empty string = "not yet known, accept any" (pre-navigation phase).
            # This prevents CDN URLs from auto-advanced videos being captured
            # before or during navigate-back (the URL is fired for the wrong video).
            _accept_pid: list[str] = [""]

            def _pid_ok() -> bool:
                """Return True if current page URL matches target, or target not yet known."""
                guard = _accept_pid[0]
                if not guard:
                    return True
                try:
                    cur = page.url
                    cur_pid = _extract_photo_id(cur)
                    return cur_pid == guard
                except Exception:
                    return True

            # Layer A: Playwright request intercept
            def _on_request(request) -> None:
                nonlocal cdn_url
                if not cdn_url and _is_ks_cdn_url(request.url):
                    if not _pid_ok():
                        logger.debug(
                            "Kuaishou CDP[A]: skipping CDN URL (wrong page pid): %s",
                            request.url[:80],
                        )
                        return
                    logger.debug("Kuaishou CDP[A]: caught %s", request.url[:80])
                    cdn_url = request.url

            page.on("request", _on_request)

            # Layer B: response MIME
            def _on_response(response) -> None:
                nonlocal cdn_url
                if cdn_url:
                    return
                ct = response.headers.get("content-type", "").lower()
                if ct.startswith("video/") and "mjpeg" not in ct:
                    if _is_ks_cdn_url(response.url):
                        if not _pid_ok():
                            return
                        logger.debug("Kuaishou CDP[B]: video MIME=%s url=%s", ct, response.url[:80])
                        cdn_url = response.url

            page.on("response", _on_response)

            # Layer C: CDP Network domain
            try:
                cdp_session = ctx.new_cdp_session(page)
                cdp_session.send("Network.enable")

                def _on_cdp_request(params: dict) -> None:
                    nonlocal cdn_url
                    if cdn_url:
                        return
                    u = params.get("request", {}).get("url", "")
                    if u and _is_ks_cdn_url(u):
                        if not _pid_ok():
                            logger.debug(
                                "Kuaishou CDP[C]: skipping CDN URL (wrong page pid): %s",
                                u[:80],
                            )
                            return
                        logger.debug("Kuaishou CDP[C]: Network domain caught %s", u[:80])
                        cdn_url = u

                cdp_session.on("Network.requestWillBeSent", _on_cdp_request)
            except Exception as exc:
                logger.debug("Kuaishou CDP[C]: Network domain unavailable: %s", exc)

            # Layer D: responseReceived — catches video/* MIME even without .mp4 in URL
            try:

                def _on_cdp_response(params: dict) -> None:
                    nonlocal cdn_url
                    if cdn_url:
                        return
                    mime = params.get("response", {}).get("mimeType", "")
                    u = params.get("response", {}).get("url", "")
                    if mime.startswith("video/") and "mjpeg" not in mime:
                        if _is_ks_cdn_url(u) or _KS_CDN_HOST_RE.search(u):
                            if not _pid_ok():
                                return
                            logger.debug(
                                "Kuaishou CDP[D]: responseReceived MIME=%s url=%s",
                                mime,
                                u[:80],
                            )
                            cdn_url = u

                cdp_session.on("Network.responseReceived", _on_cdp_response)
            except Exception:
                pass

            # Pre-page JS: intercept fetch/XHR before Kuaishou JS loads
            page.add_init_script(_KS_PRE_PAGE_JS)

            _prog(12, t("progress.ks_open_page"))

            # Build canonical URL for navigation.
            # BUG-KS-06 FIX: When server-side short URL resolution fails (non-CN IP
            # timeout), _resolve_short_url returns the original v.kuaishou.com URL
            # unchanged, so "v.kuaishou.com" is still in page_url. The old else
            # branch passed the short URL directly to CDP -> browser navigated to
            # https://v.kuaishou.com/<code> which works but wastes ~90s on extra
            # redirect + auto-advance recovery. The browser CAN follow v.kuaishou.com
            # redirects natively (unlike server-side curl_cffi on non-CN IP), so we
            # always build a canonical URL from the extracted photo_id regardless of
            # whether the short URL was resolved. If photo_id IS the short code (real
            # ID unknown), https://www.kuaishou.com/short-video/<code> is still valid
            # -- kuaishou.com redirects it to the real video page.
            _pid = _extract_photo_id(page_url)
            if _pid:
                _nav_url = f"https://www.kuaishou.com/short-video/{_pid}"
            else:
                _nav_url = page_url.split("?")[0]
            logger.debug("Kuaishou CDP: navigating to %s", _nav_url)

            try:
                _remaining = _abs_deadline - time.monotonic()
                page.goto(
                    _nav_url,
                    wait_until="domcontentloaded",
                    timeout=min(max(_remaining * 0.35, 8.0), 40.0) * 1_000,
                )
            except PWTimeout:
                pass
            except Exception as exc:
                logger.debug("Kuaishou CDP: goto warning (non-fatal): %s", exc)

            # BUG-KS-08 FIX: When page_url contains a short code (server-side
            # resolution failed), _nav_url is https://www.kuaishou.com/short-video/<code>.
            # The browser follows the redirect to the real video URL, so page.url
            # now contains the real photo_id. Using _nav_url as the source for
            # _target_pid meant the short code was used for auto-advance detection,
            # causing every real-id URL seen in the poll loop to be misidentified as
            # auto-advance, triggering an infinite re-navigate loop that prevented
            # the video from ever playing within the 120s timeout.
            # Fix: read the post-redirect URL from the browser and use that for
            # both _target_pid and _nav_url (so re-navigate after real auto-advance
            # also goes to the correct canonical URL).
            try:
                _redirected_url = page.url
                _redirected_pid = _extract_photo_id(_redirected_url)
                # BUG-KS-CDP-02 FIX: When goto raises ERR_TIMED_OUT the browser
                # may not have followed the redirect yet and page.url returns
                # "about:blank" or the original nav URL unchanged.
                # _extract_photo_id("about:blank") = "blank" -- a garbage pid.
                # Guard: only accept a redirected pid that looks like a real
                # Kuaishou photo id (alphanumeric, len >= 6, not reserved words).
                _RESERVED = {"blank", "about", "null", "undefined", "short-video", "video", "f"}
                _pid_valid = (
                    _redirected_pid
                    and _redirected_pid not in _RESERVED
                    and len(_redirected_pid) >= 6
                    and _redirected_pid != _pid
                )
                if _pid_valid:
                    logger.debug(
                        "Kuaishou CDP: short URL redirected %s -> real pid=%s",
                        _pid,
                        _redirected_pid,
                    )
                    _nav_url = f"https://www.kuaishou.com/short-video/{_redirected_pid}"
                    _pid = _redirected_pid
                else:
                    # BUG-KS-CDP-03 FIX: page.url is "about:blank" -- goto ERR_TIMED_OUT
                    # before any redirect happened (kuaishou.com unreachable from this IP).
                    # Polling 150s will find nothing. Retry navigate once with remaining
                    # time; if still blank, return None immediately so user gets a fast
                    # failure instead of a 150s freeze per attempt.
                    _is_blank = (
                        not _redirected_pid or _redirected_pid in _RESERVED or len(_redirected_pid) < 6
                    )
                    if _is_blank and "kuaishou.com" not in (_redirected_url or ""):
                        logger.debug(
                            "Kuaishou CDP: page.url=%r after goto -- kuaishou.com unreachable; "
                            "retrying navigate once with remaining time",
                            _redirected_url,
                        )
                        _retry_remaining = _abs_deadline - time.monotonic() - 10.0
                        if _retry_remaining > 8.0:
                            try:
                                page.goto(
                                    _nav_url,
                                    wait_until="domcontentloaded",
                                    timeout=min(_retry_remaining, 40.0) * 1_000,
                                )
                            except Exception as _retry_exc:
                                logger.debug("Kuaishou CDP: retry goto also failed: %s", _retry_exc)
                            _retry_url = page.url
                            _retry_pid = _extract_photo_id(_retry_url)
                            if (
                                not _retry_pid or _retry_pid in _RESERVED or len(_retry_pid) < 6
                            ) and "kuaishou.com" not in (_retry_url or ""):
                                logger.debug(
                                    "Kuaishou CDP: still blank after retry (%r) -- "
                                    "kuaishou.com unreachable, aborting strategy E early",
                                    _retry_url,
                                )
                                return None
                            # Retry succeeded -- update nav URL with real pid if resolved
                            _retry_valid_pid = _extract_photo_id(_retry_url)
                            if (
                                _retry_valid_pid
                                and _retry_valid_pid not in _RESERVED
                                and len(_retry_valid_pid) >= 6
                                and _retry_valid_pid != _pid
                            ):
                                _nav_url = f"https://www.kuaishou.com/short-video/{_retry_valid_pid}"
                                _pid = _retry_valid_pid
                                logger.debug("Kuaishou CDP: retry redirect -> real pid=%s", _retry_valid_pid)
                        else:
                            logger.debug(
                                "Kuaishou CDP: insufficient time for retry (%.1fs) -- "
                                "aborting strategy E early",
                                _retry_remaining,
                            )
                            return None
                    else:
                        logger.debug(
                            "Kuaishou CDP: short URL redirected %s -> real pid=%s "
                            "(ignored -- not a valid pid)",
                            _pid,
                            _redirected_pid,
                        )
            except Exception:
                pass

            # photo_id of the video we actually want — used to detect auto-advance.
            _target_pid = _extract_photo_id(_nav_url) or ""

            # Arm the CDN callback guard now that we know the real target pid.
            # Callbacks will reject CDN URLs fired while the page shows a different video.
            if _target_pid:
                _accept_pid[0] = _target_pid

            # Click video element to trigger play — Kuaishou player needs a gesture.
            _CLICK_JS = (
                "(function(){"
                "var v=document.querySelector('video');"
                "if(v){try{v.play();}catch(e){}try{v.click();}catch(e){}}"
                "var p=document.querySelector('.player-container,.video-player,.ksPlayerWrapper');"
                "if(p){try{p.click();}catch(e){}}"
                "})()"
            )
            # Click phase: at most 20s but must leave >=15s for poll before deadline.
            _click_deadline = min(
                time.monotonic() + 20.0,
                _abs_deadline - 15.0,
            )
            _clicked = False
            while time.monotonic() < _click_deadline and not cdn_url:
                if cancel_event and cancel_event.is_set():
                    logger.debug("Kuaishou CDP: cancelled during click phase")
                    return None
                if not _clicked:
                    try:
                        page.evaluate(_CLICK_JS)
                        _clicked = True
                        logger.debug("Kuaishou CDP: click gesture sent")
                    except Exception:
                        pass
                time.sleep(1.0)
                if cdn_url:
                    break
                # re-click every 5s in case player reloaded
                try:
                    page.evaluate(_CLICK_JS)
                except Exception:
                    pass

            # Poll loop — runs until absolute deadline minus 5s cleanup buffer.
            loop_deadline = _abs_deadline - 5.0
            last_poll = 0.0
            while time.monotonic() < loop_deadline:
                if cdn_url:
                    break
                if cancel_event and cancel_event.is_set():
                    logger.debug("Kuaishou CDP: cancelled during poll loop")
                    return None
                now = time.monotonic()
                if now - last_poll >= 1.5:
                    last_poll = now
                    try:
                        val = page.evaluate(_KS_POLL_JS)
                        if (
                            val
                            and val.startswith("http")
                            and (_is_ks_cdn_url(val) or _KS_CDN_HOST_RE.search(val))
                        ):
                            logger.debug("Kuaishou CDP[JS]: poll caught %s", val[:80])
                            cdn_url = val
                    except Exception:
                        pass
                    # Check if Kuaishou auto-advanced to a different video (feed
                    # swipe behavior). If so, navigate back to the target video.
                    # Do NOT follow the new URL — it is a different video.
                    if not cdn_url:
                        try:
                            cur_url = page.url
                            if "kuaishou.com/short-video/" in cur_url:
                                _cur_pid = _extract_photo_id(cur_url)
                                if _cur_pid and _cur_pid != _target_pid:
                                    logger.debug(
                                        "Kuaishou CDP: auto-advance to %s detected — "
                                        "navigating back to target %s",
                                        _cur_pid,
                                        _target_pid,
                                    )
                                    try:
                                        page.goto(
                                            _nav_url,
                                            wait_until="domcontentloaded",
                                            timeout=15_000,
                                        )
                                        page.evaluate(_CLICK_JS)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                time.sleep(0.4)

            # Capture final page URL — after browser redirect this contains
            # the real photo_id, even when short URL resolution failed server-side.
            try:
                _final_page_url = page.url
            except Exception:
                _final_page_url = page_url

            # Close the tab we opened. close() sends CDP Target.closeTarget which
            # is reliable; we also sweep any remaining kuaishou tabs in case the
            # player auto-opened a second one or a previous run leaked a tab.
            try:
                page.close()
                page.wait_for_event("close", timeout=3_000)
            except Exception:
                pass

            # Sweep: close any leftover kuaishou tabs (leaked from previous runs
            # or auto-opened by the player). Only touch tabs whose URL contains
            # kuaishou.com — never close the user's own tabs.
            try:
                for _p in list(ctx.pages):
                    try:
                        _u = _p.url
                    except Exception:
                        continue
                    if "kuaishou.com" in _u.lower() or "kwai.com" in _u.lower():
                        try:
                            _p.close()
                        except Exception:
                            pass
            except Exception:
                pass

            # Disconnect from CDP without closing the browser — the browser
            # belongs to the user. connect_over_cdp().close() disconnects only.
            try:
                cdp_browser.close()
            except Exception:
                pass

    except Exception as exc:
        logger.debug("Kuaishou strategy E: CDP session error: %s", exc)
        return None
    finally:
        # proc is the launcher Popen'd above. When Brave was already running,
        # Chromium's single-instance IPC forwards the new-tab request to the
        # existing instance and the launcher exits immediately (returncode != 0).
        # In that case terminate()/kill() are no-ops on a dead pid, which is fine.
        # When Brave was NOT running, proc IS the browser — terminate it so the
        # next invocation gets a fresh instance without a stale profile lock.
        _already_exited = proc.poll() is not None
        if not _already_exited:
            # Browser was freshly spawned by us — shut it down.
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
            time.sleep(0.5)  # extra buffer for profile lock release on Windows
        else:
            # Launcher already exited (existing Brave instance reused).
            # The browser stays alive — tabs were closed via CDP above.
            logger.debug("Kuaishou strategy E: launcher already exited (existing browser reused)")

    if not cdn_url:
        logger.debug("Kuaishou strategy E: no CDN URL captured within %.0fs", timeout)
        return None

    # Prefer real photo_id from the final browser URL (after redirect) over
    # the short code that was used to navigate (when server-side resolve failed).
    _real_pid = _extract_photo_id(_final_page_url) or _extract_photo_id(page_url) or ""
    caption = _cdp_caption_from_url(cdn_url, _final_page_url)

    photo = {
        "id": _real_pid,
        "caption": caption,
        "duration": 0,
        "coverUrl": "",
        "photoUrl": cdn_url,
    }
    author: dict = {}
    logger.info(
        "Kuaishou strategy E (CDP): captured CDN URL %s (real_pid=%s)",
        cdn_url[:80],
        _real_pid,
    )
    return photo, author


# ── Public extraction entry point ─────────────────────────────────────────────


def extract_info_kuaishou(
    url: str,
    config: Optional[ConfigManager] = None,
    cancel_event: Optional[_threading.Event] = None,
) -> MediaInfo:
    """Extract Kuaishou video metadata. Raises RuntimeError on failure.

    Tries strategies in order:
      A. HTML __NEXT_DATA__ scrape + CDN URL regex scan (no auth required)
      B. GraphQL visionVideoDetail with cookie (photoUrl / hlsPlayUrl fields)
      C. kwai.com international feed API
      D. kuaishou.com REST info API
      E. yt-dlp built-in extractor (last resort)
    """

    def _cancelled() -> bool:
        return bool(cancel_event and cancel_event.is_set())

    _start = time.monotonic()
    cookie_str = _load_cookie_str(config) if config else ""
    session = _make_session()

    try:
        resolved = _resolve_short_url(url, session)
        photo_id = _extract_photo_id(resolved)
        if not photo_id:
            raise RuntimeError(t("err.ks_no_photo_id", url=resolved))
        logger.debug("Kuaishou: photo_id=%s (resolved from %s)", photo_id, url)

        result: tuple | None = None

        if _cancelled():
            raise RuntimeError(t("err.ks_cancelled"))
        logger.debug("Kuaishou: trying strategy A (HTML __NEXT_DATA__)")
        result = _strategy_html(session, photo_id, cookie_str)

        if result is None:
            if _cancelled():
                raise RuntimeError(t("err.ks_cancelled"))
            logger.debug("Kuaishou: trying strategy B (GraphQL + cookie)")
            result = _strategy_gql(session, photo_id, cookie_str)

        if result is None:
            if _cancelled():
                raise RuntimeError(t("err.ks_cancelled"))
            logger.debug("Kuaishou: trying strategy C (kwai.com API)")
            result = _strategy_kwai(session, photo_id, cookie_str)

        if result is None:
            if _cancelled():
                raise RuntimeError(t("err.ks_cancelled"))
            logger.debug("Kuaishou: trying strategy D (m.kuaishou.com mobile API)")
            result = _strategy_mobile(session, photo_id, cookie_str)

        if result is None:
            if _cancelled():
                raise RuntimeError(t("err.ks_cancelled"))
            logger.debug("Kuaishou: trying strategy E (CDP browser intercept)")
            # BUG-KS-BUDGET FIX: strategies A-D plus short-URL resolution can
            # burn well over 100s before strategy E even starts. A fixed 150s
            # CDP timeout on top of that overran api/server.py's 180s analyse
            # deadline (client sees a timeout while the browser keeps running).
            # Give strategy E only what's left of the shared budget.
            _elapsed = time.monotonic() - _start
            _cdp_timeout = max(20.0, _EXTRACT_BUDGET_S - _elapsed)
            result = _strategy_cdp(resolved, config, timeout=_cdp_timeout, cancel_event=cancel_event)

        if result is None:
            raise RuntimeError(t("err.ks_all_strategies_failed"))

        photo, author = result

        uploader = (author.get("name") or "").strip()
        duration = int(photo.get("duration") or 0) // 1000  # ms -> s
        thumbnail = photo.get("coverUrl") or ""

        video_url = _pick_best_video_url(photo)
        if not video_url:
            raise RuntimeError(t("err.ks_no_video_url"))

        # Prefer the real photo_id returned by the strategy (e.g. CDP real_pid)
        # over the short code that was used for navigation. When server-side
        # short URL resolution times out on non-CN IP, photo_id stays as the
        # short code (e.g. "JZQ58vpT") but photo["id"] contains the real ID
        # resolved by the browser redirect (e.g. "3x78s79ptbs94km"). Using the
        # short code as video_id causes the re-extract in download() to navigate
        # to short-video/<short_code> which ERR_TIMESOUTs again and fails.
        _result_id = (photo.get("id") or "").strip()
        _effective_id = (
            _result_id
            if _result_id and _result_id != photo_id and len(_result_id) > len(photo_id)
            else photo_id
        )

        title = _clean_caption((photo.get("caption") or "").strip())

        logger.info(
            "Kuaishou: extracted — title=%r uploader=%r duration=%ds",
            title[:50],
            uploader[:30],
            duration,
        )

        return MediaInfo(
            url=video_url,
            title=title,
            uploader=uploader,
            duration=duration,
            thumbnail=thumbnail,
            platform="Kuaishou",
            formats=[{"format_id": "best", "url": video_url}],
            is_live=False,
            was_live=False,
            video_id=_effective_id,
            source_engine="kuaishou",
        )
    finally:
        session.close()


# ── download() helpers ────────────────────────────────────────────────────────


def _looks_like_photo_id(value: str) -> bool:
    """True if `value` looks like a bare Kuaishou photo id, not a URL/CDN host."""
    return bool(
        value
        and re.match(r"^[A-Za-z0-9_-]{6,}$", value)
        and "kuaishou.com" not in value
        and "ksapisrv.com" not in value
        and "kwaicdn.com" not in value
    )


def _build_output_path(output_dir: Path, media_info: MediaInfo) -> tuple[Path, Path]:
    """Return (filename, part_path) for media_info.

    BUG-KS-09: filename pattern unified with yt-dlp platforms:
      <title> [<photo_id[:30]>].mp4
    ID bracket makes every file uniquely identifiable regardless of CDN host,
    matching the [%(id).30B] convention yt-dlp uses for TikTok, YouTube, etc.
    """
    photo_id = media_info.video_id or ""
    id_bracket = f" [{photo_id[:30]}]" if photo_id else ""
    title_part = _sanitise_filename(media_info.title or "kuaishou")
    # Mirror yt-dlp trim_file_name=180: cap stem so total path < MAX_PATH.
    title_part = title_part[: 180 - len(id_bracket)]
    safe_title = f"{title_part}{id_bracket}"
    filename = output_dir / f"{safe_title}.mp4"
    stem = filename.stem
    counter = 1
    while filename.exists():
        filename = output_dir / f"{stem} ({counter}).mp4"
        counter += 1
    return filename, filename.with_suffix(".part")


def _stream_to_part_file(resp, part_path: Path, task: DownloadTask, on_progress) -> None:
    """Stream resp's body to part_path, updating task progress/speed/eta.

    Respects task cancellation/pause. Shared by the initial download attempt
    and the post-expiry retry attempt so both get identical progress reporting
    and cancellation handling.
    """
    total_bytes = int(resp.headers.get("Content-Length", 0) or 0)
    downloaded = 0
    chunk_size = 1024 * 256  # 256 KB
    speed_window: list[tuple[float, int]] = []

    with open(part_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if task.is_cancellation_requested:
                break
            task.wait_if_paused()

            fh.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()

            speed_window.append((now, len(chunk)))
            speed_window[:] = [(t, b) for t, b in speed_window if now - t <= 3.0]
            window_bytes = sum(b for _, b in speed_window)
            window_sec = now - speed_window[0][0] if len(speed_window) > 1 else 1.0
            speed_bps = window_bytes / max(window_sec, 0.001)

            progress_pct = (downloaded / total_bytes * 100.0) if total_bytes else 0.0
            eta_s = ""
            if total_bytes and speed_bps > 0:
                secs = int((total_bytes - downloaded) / speed_bps)
                mm, ss = divmod(secs, 60)
                eta_s = f"{mm:02d}:{ss:02d}"

            with task._lock:
                task.progress = min(progress_pct, 99.0)
                task.downloaded_bytes = downloaded
                task.total_bytes = total_bytes
                task.speed = _fmt_speed(speed_bps)
                task.eta = eta_s

            if on_progress:
                on_progress(task)


def _handle_cancel(task: DownloadTask, part_path: Path) -> bool:
    """If cancellation was requested, clean up part_path and mark task CANCELLED.

    Returns True when the caller should stop (cancellation was handled).
    """
    if not task.is_cancellation_requested:
        return False
    part_path.unlink(missing_ok=True)
    with task._lock:
        task.status = DownloadStatus.CANCELLED
    return True


# ── Engine class ──────────────────────────────────────────────────────────────


class KuaishouEngine:
    """
    Downloads Kuaishou videos directly from CDN, bypassing yt-dlp.

    extract_info() — fetch metadata (no download)
    download()     — download video to task.output_dir
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def extract_info(
        self,
        url: str,
        cancel_event: Optional[_threading.Event] = None,
    ) -> MediaInfo:
        return extract_info_kuaishou(url, self._config, cancel_event=cancel_event)

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        """Download Kuaishou video. Mutates task in-place; raises RuntimeError on failure."""
        with task._lock:
            task.status = DownloadStatus.DOWNLOADING

        media_info = task.media_info
        if media_info is None:
            raise RuntimeError("KuaishouEngine.download: task.media_info is None")

        # Same threading.Event the task's own cancel button sets — reusing it
        # (instead of ignoring cancellation here) lets a re-extract triggered
        # mid-download abort promptly instead of running the full CDP timeout.
        cancel_event = task._cancel_event
        cdn_url = media_info.url
        cookie_str = _load_cookie_str(self._config)

        # CDN URLs expire within minutes. Decide whether to re-extract:
        # - Desktop flow: task.url is a kuaishou.com page URL; CDN URL in
        #   media_info is fresh → skip re-extract, probe first.
        # - Remote API flow: iPhone echoes back the CDN URL as body.url
        #   (task.url = CDN URL, not page URL). By download time the URL may
        #   have expired → probe first; re-extract only if probe fails.
        #
        # BUG-KS-PROBE FIX: A network timeout during probe does NOT mean the
        # CDN URL has expired — it means the probe itself timed out (e.g. CN CDN
        # reachability from non-CN IP is intermittent). Triggering re-extract on
        # timeout caused a cascading failure: re-extract → short URL resolve
        # timeout (15s×2) → strategy B photo=null → strategy E CDP 150s timeout
        # → total failure even though the original CDN URL was still valid.
        #
        # Rule: re-extract ONLY when the server explicitly rejects the URL
        # (HTTP 403/404/410 or body is not MP4 magic bytes). Network errors and
        # timeouts → proceed with the existing CDN URL and let the full download
        # attempt fail naturally (which gives a user-visible retry option).
        _need_reextract = not cdn_url or not cdn_url.startswith("http")
        if cdn_url and cdn_url.startswith("http") and not _need_reextract:
            # Probe with Range GET bytes=0-11 to check MP4 magic bytes.
            # HEAD is unreliable — Kuaishou CDN returns HTTP 200 with an HTML
            # error page body when the signed URL has expired.
            # Short timeout (8s): if CDN is unreachable we skip re-extract and
            # attempt the download directly — better than 150s CDP fallback.
            try:
                _probe_sess = _make_session()
                _probe_headers = {
                    **_PAGE_HEADERS,
                    "Referer": "https://www.kuaishou.com/",
                    "Range": "bytes=0-11",
                }
                if cookie_str:
                    _probe_headers["Cookie"] = cookie_str
                # BUG-KS-PROBE-MEM FIX: stream=True + reading a single bounded
                # chunk means at most `chunk_size` bytes ever come over the
                # wire, even when the CDN ignores Range and returns HTTP 200
                # with the full video body. The previous `.content[:12]` read
                # (no stream=True) buffered the entire response into memory
                # first — multiple MB per probe on a server that ignores Range.
                _probe_resp = _probe_sess.get(
                    cdn_url,
                    headers=_probe_headers,
                    timeout=8,
                    stream=True,
                    allow_redirects=True,
                )
                _probe_bytes = b""
                for _chunk in _probe_resp.iter_content(chunk_size=12):
                    _probe_bytes = _chunk
                    break
                _probe_resp.close()
                _probe_sess.close()
                _probe_is_mp4 = len(_probe_bytes) >= 8 and _probe_bytes[4:8] in (
                    b"ftyp",
                    b"moov",
                    b"mdat",
                    b"wide",
                )
                if _probe_resp.status_code not in (200, 206) or not _probe_is_mp4:
                    logger.debug(
                        "Kuaishou CDN probe: HTTP %d mp4=%s — CDN URL expired, re-extracting",
                        _probe_resp.status_code,
                        _probe_is_mp4,
                    )
                    _need_reextract = True
                else:
                    logger.debug(
                        "Kuaishou CDN probe: HTTP %d mp4=%s — CDN URL still valid",
                        _probe_resp.status_code,
                        _probe_is_mp4,
                    )
            except Exception as exc:
                # Network timeout or connection error — NOT a URL expiry signal.
                # Proceed with the existing CDN URL; do not trigger re-extract.
                logger.debug(
                    "Kuaishou CDN probe failed (%s) — skipping probe, attempting download with existing URL",
                    exc,
                )

        if _need_reextract:
            # Always prefer canonical URL so _resolve_short_url is skipped entirely.
            # Short URL HEAD+GET each timeout at 15 s on non-CN IP — if video_id
            # is already known from the analyse step, constructing the canonical
            # URL directly avoids 30+ s of pointless timeout churn.
            #
            # BUG-KS-01 FIX: The previous check `^3[A-Za-z0-9_-]{10,}$` only
            # accepted IDs starting with '3', which is a historical accident of
            # Kuaishou's current ID generation, not a schema guarantee. A broader
            # validity check (alphanumeric + _ + -, length >= 6) is used instead,
            # with task.url as a second source so short codes in CDN URLs don't
            # accidentally become the reextract target.
            _vid_id = media_info.video_id or ""
            _is_valid_id = bool(
                _vid_id
                and re.match(r"^[A-Za-z0-9_-]{6,}$", _vid_id)
                # Exclude raw CDN URLs that may have been stored as video_id
                and "kuaishou.com" not in _vid_id
                and "ksapisrv.com" not in _vid_id
                and "kwaicdn.com" not in _vid_id
            )
            if _is_valid_id:
                _reextract_url = f"https://www.kuaishou.com/short-video/{_vid_id}"
            else:
                # Prefer photo_id extracted from the original task URL (page URL)
                # over any CDN URL that might be stored in task.url (Remote API flow).
                _ks_m = re.search(
                    r"kuaishou\.com/(?:short-video|video)/([A-Za-z0-9_-]+)",
                    task.url,
                    re.I,
                )
                if _ks_m:
                    _reextract_url = f"https://www.kuaishou.com/short-video/{_ks_m.group(1)}"
                elif "v.kuaishou.com" in task.url:
                    # Short URL — avoid server-side resolution (times out on non-CN IP).
                    # Extract the short code and build a kuaishou.com canonical URL
                    # so _resolve_short_url is skipped (it HEAD+GET timeouts 15s each).
                    _short_code_m = re.search(r"v\.kuaishou\.com/([A-Za-z0-9_-]+)", task.url, re.I)
                    if _short_code_m:
                        _reextract_url = f"https://www.kuaishou.com/short-video/{_short_code_m.group(1)}"
                    else:
                        _reextract_url = task.url
                else:
                    # task.url is a CDN URL (Remote API flow with no page URL).
                    # BUG-KS-RE-01 FIX: Previously fell back to task.url (= CDN URL)
                    # which passed to extract_info_kuaishou -> _extract_photo_id
                    # returned a CDN path segment, not a photo id -> all strategies fail.
                    # Fix: try to salvage the photo id from media_info.video_id even
                    # if it looked invalid above, or from task.media_info directly.
                    # If video_id contains the real id (len >= 6, no CDN host), use it.
                    # Otherwise there is genuinely nothing to work with — raise early
                    # with a clear message instead of letting 150s CDP run on a CDN URL.
                    _salvage_id = (media_info.video_id or "").strip() if media_info else ""
                    if (
                        _salvage_id
                        and len(_salvage_id) >= 6
                        and "." not in _salvage_id
                        and "/" not in _salvage_id
                    ):
                        _reextract_url = f"https://www.kuaishou.com/short-video/{_salvage_id}"
                        logger.info(
                            "Kuaishou: Remote API re-extract — salvaged video_id=%s from media_info",
                            _salvage_id,
                        )
                    else:
                        raise RuntimeError(t("err.ks_no_page_url", video_id=_vid_id))
            logger.info(
                "Kuaishou: re-extracting fresh CDN URL for task %s via %s",
                task.id,
                _reextract_url[:80],
            )
            media_info = extract_info_kuaishou(_reextract_url, self._config, cancel_event=cancel_event)
            cdn_url = media_info.url

        # ── Output path ───────────────────────────────────────────────────
        output_dir = Path(task.output_dir) if task.output_dir else self._config.download_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        filename, part_path = _build_output_path(output_dir, media_info)

        with task._lock:
            task.filename = str(filename)

        # ── Stream download with progress ─────────────────────────────────
        _CDN_HEADERS = {
            "User-Agent": _PAGE_HEADERS["User-Agent"],
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.kuaishou.com/",
        }
        # Cookie is attached explicitly so CDN requests carry the kuaishou
        # session cookies. kwaicdn.com signed URLs are tied to the session
        # that issued them; downloading without the same cookies causes the
        # CDN to return an HTML error page (HTTP 200, <!DOCTYPE html> body)
        # instead of the MP4.
        if cookie_str:
            _CDN_HEADERS["Cookie"] = cookie_str
        session = _make_session()
        try:
            resp = session.get(
                cdn_url,
                headers=_CDN_HEADERS,
                stream=True,
                timeout=_DL_TIMEOUT,
            )
            if resp.status_code != 200:
                ct = resp.headers.get("content-type", "")
                logger.debug(
                    "Kuaishou CDN: HTTP %d content-type=%r url=%s",
                    resp.status_code,
                    ct,
                    cdn_url[:100],
                )
                raise RuntimeError(t("err.ks_cdn_http", code=resp.status_code))

            # Early content-type check: if CDN returns text/html the URL has
            # expired (signed URL invalidated). Re-extract immediately and
            # restart the download with the fresh CDN URL — do not raise here,
            # as the retry loop in download_manager would reuse the same stale
            # task.media_info on the next attempt anyway.
            _resp_ct = resp.headers.get("content-type", "").lower()
            if "text/html" in _resp_ct or "application/xhtml" in _resp_ct:
                logger.debug(
                    "Kuaishou CDN: content-type=%r — URL expired, re-extracting inline",
                    _resp_ct,
                )
                resp.close()
                session.close()
                _vid_id = media_info.video_id or ""
                _reextract_inline_url: str
                if _vid_id and re.match(r"^[A-Za-z0-9_-]{6,}$", _vid_id) and "." not in _vid_id:
                    _reextract_inline_url = f"https://www.kuaishou.com/short-video/{_vid_id}"
                elif re.search(r"kuaishou\.com/(?:short-video|video)/([A-Za-z0-9_-]+)", task.url, re.I):
                    _m = re.search(r"kuaishou\.com/(?:short-video|video)/([A-Za-z0-9_-]+)", task.url, re.I)
                    _reextract_inline_url = f"https://www.kuaishou.com/short-video/{_m.group(1)}"  # type: ignore[union-attr]
                elif "v.kuaishou.com" in task.url:
                    _sc = re.search(r"v\.kuaishou\.com/([A-Za-z0-9_-]+)", task.url, re.I)
                    _reextract_inline_url = (
                        f"https://www.kuaishou.com/short-video/{_sc.group(1)}"  # type: ignore[union-attr]
                        if _sc
                        else task.url
                    )
                else:
                    # task.url is a CDN URL (Remote API) — cannot navigate to it.
                    # Raise so download_manager shows a clear error instead of
                    # running 150s CDP on a CDN URL that will never yield a page.
                    raise RuntimeError(t("err.ks_cdn_html_no_page_url"))
                logger.info("Kuaishou: inline re-extract via %s", _reextract_inline_url[:80])
                media_info = extract_info_kuaishou(
                    _reextract_inline_url, self._config, cancel_event=cancel_event
                )
                cdn_url = media_info.url
                # BUG-KS-FN FIX: recompute filename/part_path — the re-extract
                # may have returned a different title/id than the original
                # media_info the output path above was built from, otherwise
                # the finished file keeps the stale pre-re-extract name.
                filename, part_path = _build_output_path(output_dir, media_info)
                with task._lock:
                    task.media_info = media_info
                    task.filename = str(filename)
                cookie_str = _load_cookie_str(self._config)
                _CDN_HEADERS = dict(_CDN_HEADERS)
                if cookie_str:
                    _CDN_HEADERS["Cookie"] = cookie_str
                else:
                    _CDN_HEADERS.pop("Cookie", None)
                session = _make_session()
                resp = session.get(
                    cdn_url,
                    headers=_CDN_HEADERS,
                    stream=True,
                    timeout=_DL_TIMEOUT,
                )
                if resp.status_code != 200:
                    raise RuntimeError(t("err.ks_cdn_http_after_reextract", code=resp.status_code))
                _resp_ct = resp.headers.get("content-type", "").lower()
                if "text/html" in _resp_ct or "application/xhtml" in _resp_ct:
                    raise RuntimeError(t("err.ks_cdn_html_after_reextract"))

            try:
                _stream_to_part_file(resp, part_path, task, on_progress)
            except Exception:
                # BUG-KS-PART-LEAK FIX: a mid-stream network error left the
                # partial file on disk indefinitely — only cancellation and
                # invalid-MP4 cleaned it up. Clean up here too, then re-raise
                # so the caller still sees the failure.
                part_path.unlink(missing_ok=True)
                raise
        finally:
            session.close()

        if _handle_cancel(task, part_path):
            return

        if not _is_valid_mp4(part_path):
            # Log first 200 bytes to diagnose what CDN actually returned
            try:
                with open(part_path, "rb") as _f:
                    _head = _f.read(200)
                logger.debug(
                    "Kuaishou: invalid MP4 — first bytes: %r (size=%d)",
                    _head[:80],
                    part_path.stat().st_size,
                )
            except Exception:
                pass
            part_path.unlink(missing_ok=True)

            # CDN URLs expire quickly (~minutes). If video_id is known, rebuild
            # the canonical URL and re-extract a fresh CDN URL, then retry once.
            #
            # BUG-KS-VIDID FIX: the previous guard `vid_id and vid_id != task.url`
            # compared a bare photo id against a full URL — always true when
            # vid_id was non-empty, including when vid_id was itself a stray
            # CDN URL. Use the same id-shaped check as the initial re-extract.
            vid_id = media_info.video_id if media_info else ""
            if _looks_like_photo_id(vid_id):
                logger.info("Kuaishou: CDN URL expired, re-extracting via video_id=%s", vid_id)
                try:
                    canonical = f"https://www.kuaishou.com/short-video/{vid_id}"
                    media_info = extract_info_kuaishou(canonical, self._config, cancel_event=cancel_event)
                    cdn_url = media_info.url
                    if not cdn_url or not cdn_url.startswith("http"):
                        raise RuntimeError("re-extract returned no CDN URL")

                    # BUG-KS-FN FIX: rebuild filename/part_path from the fresh
                    # media_info via the same helper the main path uses — the
                    # previous ad-hoc rebuild dropped the [id] bracket and the
                    # 180-char cap that BUG-KS-09 added.
                    filename, part_path = _build_output_path(output_dir, media_info)
                    with task._lock:
                        task.filename = str(filename)

                    _retry_headers = {**_CDN_HEADERS, "Accept": "*/*"}
                    if cookie_str:
                        _retry_headers["Cookie"] = cookie_str
                    _retry_sess = _make_session()
                    try:
                        resp2 = _retry_sess.get(
                            cdn_url,
                            headers=_retry_headers,
                            stream=True,
                            timeout=_DL_TIMEOUT,
                        )
                        if resp2.status_code == 200:
                            # BUG-KS-RETRY-PROGRESS FIX: reuse the same streaming
                            # helper as the main attempt so this retry also
                            # respects cancellation/pause and reports progress —
                            # the previous inline loop did neither.
                            _stream_to_part_file(resp2, part_path, task, on_progress)
                            resp2.close()
                            if _handle_cancel(task, part_path):
                                return
                            if _is_valid_mp4(part_path):
                                part_path.rename(filename)
                                with task._lock:
                                    task.status = DownloadStatus.COMPLETED
                                    task.progress = 100.0
                                    task.filename = str(filename)
                                logger.info(
                                    "Kuaishou: download complete (re-extracted) — %s (%.1f MB)",
                                    filename.name,
                                    filename.stat().st_size / 1_048_576,
                                )
                                if on_progress:
                                    on_progress(task)
                                return
                            part_path.unlink(missing_ok=True)
                        else:
                            resp2.close()
                    finally:
                        _retry_sess.close()
                except Exception as exc:
                    logger.debug("Kuaishou: re-extract retry failed: %s", exc)
                    part_path.unlink(missing_ok=True)

            raise RuntimeError(t("err.ks_bad_file"))

        part_path.rename(filename)

        with task._lock:
            task.status = DownloadStatus.COMPLETED
            task.progress = 100.0
            task.filename = str(filename)

        logger.info(
            "Kuaishou: download complete — %s (%.1f MB)",
            filename.name,
            filename.stat().st_size / 1_048_576,
        )

        if on_progress:
            on_progress(task)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _clean_caption(caption: str) -> str:
    """Turn a raw Kuaishou caption into a usable filename stem.

    BUG-KS-07 FIX: Raw captions contain hashtags (#tag), @mentions with
    internal user-ID suffixes (@name(O3xID)), and can be arbitrarily long
    (full caption text). After _sanitise_filename these produce filenames like:
      '???? ??????? @???????(O3xrgtux2ehryffe) @????(O3xddgkd5fav5if9).mp4'
    which Taildrop then strips to just '(O3xrgtux2ehryffe)_(O3xddgkd5fav5if9).mp4'.

    Strategy:
    1. Strip @mention(...) parenthesised ID suffixes, keep the display name.
    2. Strip standalone hashtags (#word).
    3. Collapse runs of whitespace / punctuation.
    4. Truncate to 60 chars so paths stay short on Windows (MAX_PATH = 260).
    5. If the remaining text has no ASCII content (pure Arabic/CJK/etc.), fall
       back to the generic "kuaishou" stem immediately — Taildrop's NFKD+ascii
       encode would reduce it to empty anyway, producing 'file.mp4' on iOS.
    6. Fall back to the generic "kuaishou" stem if nothing meaningful remains.
       (The photo-id bracket that makes the filename unique is appended by the
       output-path builder, not here — see BUG-KS-09.)
    """
    if not caption:
        return "kuaishou"

    # Remove @name(InternalID) -> keep display name only
    text = re.sub(r"@([^(\s#@]+)\([^)]*\)", r"\1", caption)
    # Remove bare @mentions with no parens
    text = re.sub(r"@\S+", "", text)
    # Remove hashtags
    text = re.sub(r"#\S+", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate
    text = text[:60].strip()

    if not text:
        return "kuaishou"

    # BUG-KS-08 FIX: Taildrop strips all non-ASCII chars via NFKD+ascii encode.
    # If the caption is pure CJK/non-ASCII with only punctuation surviving
    # (e.g. a comma or parenthesis), the previous .strip("-_ ") check left those
    # punctuation chars in _ascii_preview, making it truthy and bypassing the
    # fallback — the file was saved as "??????????.mp4" which Taildrop reduced to
    # ",.mp4" on iOS.  Fix: require at least one alphanumeric ASCII character.
    import unicodedata as _ud

    _ascii_preview = _ud.normalize("NFKD", text).encode("ascii", errors="ignore").decode("ascii")
    if not re.search(r"[A-Za-z0-9]", _ascii_preview):
        return "kuaishou"

    return text


def _sanitise_filename(name: str) -> str:
    for ch in r'<>:"/\|?*':
        name = name.replace(ch, "_")
    name = name.strip(". ")
    return name or "kuaishou_video"


def _fmt_speed(bps: float) -> str:
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.1f} MiB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KiB/s"
    return f"{bps:.0f} B/s"


def _is_valid_mp4(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 10_240:
        return False
    try:
        with open(path, "rb") as f:
            header = f.read(12)
        return (
            header[4:8] == b"ftyp"
            or header[0:4] in (b"ftyp", b"moov", b"mdat")
            or header[4:8] in (b"moov", b"mdat", b"wide")
        )
    except OSError:
        return False
