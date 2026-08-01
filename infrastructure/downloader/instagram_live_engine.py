"""
infrastructure/downloader/instagram_live_engine.py

Instagram Live recorder using CDP (Playwright) + bundled FFmpeg.

Flow:
  1. Extract username from URL
  2. Launch Brave/Chrome via CDP, navigate to the live URL (browser already
     has Instagram session -- no cookie file needed for HLS URL discovery)
  3. Intercept HLS .m3u8 requests from Instagram CDN via CDP Network domain
  4. Pass captured HLS URL to FFmpeg with session cookies for segment auth
  5. Report progress via on_progress callback

Why CDP instead of Instagram API:
  Instagram's internal API (get_info / heartbeat) returns the HLS playback URL
  only to the broadcaster's own mobile session. Viewer sessions (even with valid
  web sessionid cookie) get HTTP 500/404 on both endpoints. The browser however
  receives the HLS URL as part of page initialisation data and fetches it
  immediately when the live page loads -- CDP Network interception captures it.

Platform support:
  Windows, macOS (same as facebook_story_engine).
  Linux: not supported (raises RuntimeError).
"""

from __future__ import annotations

import logging
import re
import shutil
import socket as _socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.config.config_manager import ConfigManager
from utils.helpers import sanitise_filename as _sanitise_filename

logger = logging.getLogger(__name__)

_IG_APP_ID = "936619743392459"
_REQUEST_TIMEOUT = 15

_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_INSTAGRAM_LIVE_RE = re.compile(r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I)
_USER_LIVE_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)/live", re.I)
_BROADCAST_ID_FROM_URL_RE = re.compile(r"instagram\.com/(?:[^/]+/)?live/(\d+)", re.I)

# Instagram Live streams come from cdninstagram.com or Instagram's edge CDN.
# 2025+: Instagram migrated from HLS-only to DASH (MPD) for some live streams.
# We capture both HLS (.m3u8) and DASH (.mpd) and pass the URL to FFmpeg.
# FFmpeg handles both formats via -i <url> -c copy.
_IG_HLS_RE = re.compile(
    r"(?:cdninstagram\.com|scontent[^/]*\.instagram\.com|live-upload\.instagram\.com"
    r"|[^/]*\.fbcdn\.net)"
    r".*?\.(?:m3u8|mpd)",
    re.I,
)
_IG_HLS_PATH_RE = re.compile(
    r"instagram\.com/.*?\.(?:m3u8|mpd)",
    re.I,
)

# 2025+: Instagram live streams may take longer to initialize in CDP mode.
# Increased to 120s: temp profile = no cache, Instagram React SPA needs 20-40s
# to bootstrap the live player and issue the first CDN request.
_CDP_HLS_WAIT_S = 120.0
# Resume re-capture uses a shorter window than the initial 120s: the page is no
# longer cold-starting from the user's perspective and, if no fresh stream URL
# appears within this window, the live has almost certainly ended.
_RESUME_CDP_WAIT_S = 45.0
# Instagram live DASH .mpd is a snapshot manifest whose segment window goes stale
# after a few minutes; re-capture a fresh URL and resume into a new part file,
# then concat. Mirrors the TikTok BUG-TT-17 re-extract loop. A high cap allows
# multi-hour streams (~one part per stall) while still terminating eventually.
_MAX_RESUME_ATTEMPTS = 60
# Drop parts smaller than this — a sub-256KB file is FFmpeg writing only the
# container header before the URL stalled, not real footage worth concatenating.
_MIN_PART_BYTES = 256 * 1024
# Give up after this many consecutive empty parts: the URL is bad or the stream
# is not really live, so re-capturing again would only spin.
_MAX_EMPTY_PARTS = 3
# Serialize CDP browser launches to prevent profile lock conflicts
_CDP_LOCK: "threading.Lock | None" = None


def _get_cdp_lock() -> "threading.Lock":
    global _CDP_LOCK
    if _CDP_LOCK is None:
        import threading

        _CDP_LOCK = threading.Lock()
    return _CDP_LOCK


def is_instagram_live_url(url: str) -> bool:
    """Return True if *url* is an Instagram Live URL (either format)."""
    return bool(_INSTAGRAM_LIVE_RE.search(url))


def _is_ig_hls_url(url: str) -> bool:
    return bool(_IG_HLS_RE.search(url)) or bool(_IG_HLS_PATH_RE.search(url))


def _free_port() -> int:
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _browser_candidates(browser: str) -> list[str]:
    if sys.platform == "win32":
        if browser == "brave":
            return [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ]
        return [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    if sys.platform == "darwin":
        home = Path.home()
        if browser == "brave":
            return [
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                str(home / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ]
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    raise RuntimeError("Instagram Live CDP chi ho tro Windows va macOS.\nLinux chua duoc ho tro.")


def _find_browser_exe(browser: str) -> str:
    browser = browser.lower()
    # Fall back to the other Chromium browser when the configured one is not
    # installed — a wrong cookies_browser setting should degrade, not hard-fail.
    for name in (browser, "chrome" if browser == "brave" else "brave"):
        exe = next((p for p in _browser_candidates(name) if Path(p).exists()), None)
        if exe:
            return exe
    raise RuntimeError(
        f"Khong tim thay {browser.title()} (hoac Brave/Chrome thay the). "
        "Hay cai dat trinh duyet truoc.\n"
        "Dam bao da dang nhap Instagram trong trinh duyet do."
    )


class _ProfileLockConflict(RuntimeError):
    """Raised internally when a persistent CDP profile is locked by a stale process."""


def _cdp_intercept_hls(
    live_url: str,
    browser: str,
    timeout: float,
    cookie_file: Optional[str] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    profile_dir: Optional[str] = None,
) -> tuple[Optional[str], dict]:
    """
    Launch browser, navigate to live_url, intercept HLS .m3u8 URL via CDP.
    Returns the HLS URL string, or None if not found within timeout
    (also None when *cancel_check* returns True mid-wait).

    profile_dir, when given, is a stable user-data-dir reused across
    recordings so device cookies (datr/mid/ig_did) persist and the browser
    fingerprint stays constant instead of looking like a new device signing
    in on every recording. Falls back to a fresh temp profile, with a
    warning, if the persistent profile is locked by a stale process.
    """
    try:
        return _cdp_intercept_hls_impl(live_url, browser, timeout, cookie_file, cancel_check, profile_dir)
    except _ProfileLockConflict:
        logger.warning("CDP: persistent profile locked (stale process?) -- retrying with a temp profile")
        return _cdp_intercept_hls_impl(live_url, browser, timeout, cookie_file, cancel_check, None)


def _cdp_intercept_hls_impl(
    live_url: str,
    browser: str,
    timeout: float,
    cookie_file: Optional[str],
    cancel_check: Optional[Callable[[], bool]],
    profile_dir: Optional[str],
) -> tuple[Optional[str], dict]:
    _cancelled = cancel_check or (lambda: False)
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright  # noqa: I001
        from playwright._impl._api_structures import SetCookieParam  # noqa: PLC0415
    except ImportError as err:
        raise RuntimeError("Thieu thu vien Playwright.\nChay: pip install playwright") from err

    exe = _find_browser_exe(browser)
    port = _free_port()

    # A persistent profile_dir keeps the device fingerprint stable across
    # recordings. Without one (or if it's unusable), fall back to a temp
    # user-data-dir so the browser always spawns as a fresh process with the
    # CDP port active -- without this, if Brave is already running it forwards
    # the launch to the existing instance (which has no debug port) and the
    # new process exits immediately -> ECONNREFUSED on every attempt.
    if profile_dir is None:
        _is_temp_profile = True
        tmp_profile = tempfile.mkdtemp(prefix="omnidl_cdp_")
    else:
        _is_temp_profile = False
        tmp_profile = profile_dir
        Path(tmp_profile).mkdir(parents=True, exist_ok=True)

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={tmp_profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--restore-last-session=false",
        "--no-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-sync",
        "--disable-gpu",
        # Push the default browser window off-screen so only the CDP context
        # window (created via new_context) is visible to the user.
        "--window-position=-32000,-32000",
        "--window-size=1280,720",
    ]

    with _get_cdp_lock():
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_WIN_NO_WINDOW,
        )
    logger.info("CDP: launching %s on port %d (pid=%d)", browser, port, proc.pid)

    hls_url: Optional[str] = None
    _captured_browser_headers: dict = {}

    try:
        with sync_playwright() as pw:
            cdp_browser = None
            deadline = time.monotonic() + 30.0
            last_exc = None

            while time.monotonic() < deadline:
                if _cancelled():
                    return None, {}
                if proc.poll() is not None:
                    if not _is_temp_profile:
                        # Persistent profile likely locked by a stale/zombie
                        # process from a previous crashed run -- the caller
                        # retries once with a temp profile instead of failing
                        # the recording outright.
                        raise _ProfileLockConflict(
                            f"Persistent CDP profile locked (exit code {proc.returncode})"
                        )
                    raise RuntimeError(
                        "Khong ket noi duoc CDP: trinh duyet thoat ngay sau khi khoi dong "
                        f"(exit code {proc.returncode}).\n"
                        "Dong hoan toan trinh duyet (ke ca System Tray) roi thu lai."
                    )
                try:
                    cdp_browser = pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{port}",
                        timeout=3_000,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.8)

            if cdp_browser is None:
                raise RuntimeError(
                    "Khong ket noi duoc CDP.\n"
                    "Dong hoan toan trinh duyet (ke ca System Tray) roi thu lai.\n"
                    f"(chi tiet: {last_exc})"
                )

            logger.info("CDP: connected on port %d", port)

            # Build Instagram cookies BEFORE creating context so they are
            # present on the very first navigation request.
            # connect_over_cdp contexts[0] is a default context on a fresh
            # profile -- adding cookies after the fact is too late because
            # Instagram's login redirect fires before add_cookies() returns.
            # Solution: always create a new_context() and pass cookies via
            # storage_state so the browser sends them on the first request.
            playwright_cookies: list[SetCookieParam] = []
            if cookie_file:
                try:
                    from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
                        _prepare_cookie_for_use,
                    )

                    usable_ck, is_temp_ck = _prepare_cookie_for_use(cookie_file)
                    try:
                        jar = MozillaCookieJar()
                        jar.load(usable_ck, ignore_discard=True, ignore_expires=True)
                        # BUG-IG-ANTIBOT: preserve what the jar actually holds
                        # instead of forcing sameSite=None + a far-future expiry
                        # on every cookie -- that pattern, repeated on every
                        # recording, is itself a non-browser tell. Session
                        # cookies (no expires in the jar) stay session cookies
                        # (Playwright's expires=-1 sentinel); sameSite is left
                        # unset so Playwright applies its own browser-matching
                        # default instead of a hardcoded value.
                        playwright_cookies = [
                            {
                                "name": c.name,
                                "value": c.value or "",
                                # Playwright requires domain WITH leading dot for
                                # subdomain matching; instagram.com cookies must
                                # also match www.instagram.com.
                                "domain": (c.domain if c.domain.startswith(".") else f".{c.domain}")
                                if c.domain
                                else ".instagram.com",
                                "path": c.path or "/",
                                "secure": bool(c.secure),
                                "httpOnly": False,
                                "expires": c.expires if (c.expires and c.expires > 0) else -1,
                            }
                            for c in jar
                            if "instagram.com" in (c.domain or "")
                        ]
                    except Exception as _ck_exc:
                        logger.debug("CDP: cookie build failed (non-fatal): %s", _ck_exc)
                    finally:
                        if is_temp_ck:
                            try:
                                Path(usable_ck).unlink(missing_ok=True)
                            except Exception:
                                pass
                except Exception as _ci_exc:
                    logger.debug("CDP: cookie inject skipped: %s", _ci_exc)

            # Always use a fresh new_context with cookies pre-loaded.
            # Do NOT use cdp_browser.contexts[0] -- on connect_over_cdp the
            # default context is shared with the browser process and cookies
            # added to it after the fact are not sent on the first request.
            ctx = cdp_browser.new_context()
            if playwright_cookies:
                ctx.add_cookies(playwright_cookies)
                has_session = any(c["name"] == "sessionid" for c in playwright_cookies)
                logger.info(
                    "CDP: injected %d Instagram cookies into browser context (sessionid=%s)",
                    len(playwright_cookies),
                    "yes" if has_session else "NO - login will fail",
                )

            page = ctx.new_page()

            # Context-level route: ONLY intercept .m3u8/.mpd requests.
            # Routing "**/*" (all requests) throttles the entire page because
            # every JS/CSS/image asset must round-trip through this Python
            # callback before the browser continues. With Instagram's SPA
            # (~5 MB cold JS load) that adds 10-30s to page startup.
            # Scoping to stream URL patterns eliminates the throttle while
            # still catching Service Worker-initiated CDN fetches that
            # page.on("request") misses.
            def _on_ctx_route(route) -> None:
                nonlocal hls_url
                u = route.request.url
                if not hls_url:
                    logger.info("CDP[SW]: stream URL via context route: %.120s", u)
                    hls_url = u
                try:
                    route.continue_()
                except Exception:
                    pass

            for _stream_pattern in ("**/*.m3u8", "**/*.m3u8?*", "**/*.mpd", "**/*.mpd?*"):
                try:
                    ctx.route(_stream_pattern, _on_ctx_route)
                except Exception as exc:
                    logger.debug("CDP[SW]: route(%s) failed (%s)", _stream_pattern, exc)
            logger.debug("CDP[SW]: context-level stream routes installed")

            # Layer A: Playwright request intercept (page-level)
            # Log ALL instagram/cdninstagram requests to help diagnose
            def _on_request(request) -> None:
                nonlocal hls_url, _captured_browser_headers
                u = request.url
                if ".m3u8" in u or ".mpd" in u:
                    logger.info("CDP[A]: stream URL request: %s", u)
                    if not hls_url:
                        hls_url = u
                        try:
                            _captured_browser_headers = dict(request.all_headers())
                        except Exception:
                            pass
                elif "instagram.com" in u and any(
                    x in u for x in ("/live/", "live-hls", "dash-hls", "playback")
                ):
                    logger.debug("CDP[A]: IG live-related request: %.120s", u)

            page.on("request", _on_request)

            # Layer B: response MIME-type match
            def _on_response(response) -> None:
                nonlocal hls_url
                if hls_url:
                    return
                u = response.url
                ct = response.headers.get("content-type", "").lower()
                if (
                    "application/vnd.apple.mpegurl" in ct
                    or "application/x-mpegurl" in ct
                    or "application/dash+xml" in ct
                ):
                    logger.info("CDP[B]: stream via MIME (%s): %.120s", ct, u)
                    hls_url = u
                elif (".m3u8" in u or ".mpd" in u) and (
                    "instagram.com" in u or "cdninstagram.com" in u or "fbcdn.net" in u
                ):
                    logger.info("CDP[B]: stream via URL pattern: %.120s", u)
                    hls_url = u

            page.on("response", _on_response)

            # Layer C: CDP Network domain (catches ALL browser requests)
            try:
                cdp_session = ctx.new_cdp_session(page)
                cdp_session.send("Network.enable")

                def _on_cdp_request(params: dict) -> None:
                    nonlocal hls_url, _captured_browser_headers
                    req = params.get("request", {})
                    u = req.get("url", "")
                    if not u:
                        return
                    if ".m3u8" in u or ".mpd" in u:
                        logger.info("CDP[C]: stream URL via Network domain: %s", u)
                        if not hls_url:
                            hls_url = u
                            # Capture the exact headers the browser sent for this request
                            # so FFmpeg can authenticate with the same credentials.
                            _captured_browser_headers = dict(req.get("headers", {}))
                    elif "instagram.com" in u and any(
                        x in u for x in ("/live/", "live-hls", "dash-hls", "playback")
                    ):
                        logger.debug("CDP[C]: IG live-related: %.120s", u)

                def _on_cdp_response(params: dict) -> None:
                    nonlocal hls_url
                    if hls_url:
                        return
                    headers = params.get("response", {}).get("headers", {})
                    ct = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
                    u = params.get("response", {}).get("url", "")
                    if (
                        "application/dash+xml" in ct
                        or "application/vnd.apple.mpegurl" in ct
                        or "application/x-mpegurl" in ct
                    ):
                        logger.info(
                            "CDP[C]: stream via responseReceived MIME (%s): %.120s",
                            ct,
                            u,
                        )
                        hls_url = u

                cdp_session.on("Network.requestWillBeSent", _on_cdp_request)
                cdp_session.on("Network.responseReceived", _on_cdp_response)
                logger.debug("CDP[C]: Network domain enabled")
            except Exception as exc:
                logger.debug("CDP[C]: Network domain unavailable (%s)", exc)

            # Navigate - poll deadline starts AFTER navigation completes
            logger.info("CDP: navigating to %s", live_url[:100])
            try:
                page.goto(live_url, wait_until="domcontentloaded", timeout=20_000)
                logger.debug("CDP: landed on %s", page.url[:120])
            except PWTimeout:
                logger.debug("CDP: domcontentloaded timeout (non-fatal, continuing poll)")
            except Exception as exc:
                logger.debug("page.goto warning (non-fatal): %s", exc)

            # Dismiss the Instagram "tap to play" interstitial.
            # The live page renders a black overlay requiring a user gesture
            # before the video player starts and HLS segments are fetched.
            # Instagram's React SPA takes 15-40s to mount the live player --
            # injecting at 1.5s always misses the overlay (DOM not ready yet).
            # Instead, re-inject the gesture every 5s inside the poll loop
            # until the stream URL is found.
            _GESTURE_JS = (
                "(function(){"
                "try{"
                "var dlg=document.querySelector('[role=\"dialog\"]');"
                "if(dlg){"
                "var btns=dlg.querySelectorAll('[role=\"button\"],button');"
                "if(btns.length>0){"
                "btns[btns.length-1].dispatchEvent("
                "new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));"
                "}}"
                "}catch(e){}"
                "var playSelectors=['[data-visualcompletion=\"media-vc-image\"]',"
                "'._aatk','._aatn','._ab8w','._aagu','._aagv'];"
                "for(var s=0;s<playSelectors.length;s++){"
                "var ov=document.querySelector(playSelectors[s]);"
                "if(ov){try{"
                "var oe={bubbles:true,cancelable:true,view:window};"
                "ov.dispatchEvent(new MouseEvent('mousedown',oe));"
                "ov.dispatchEvent(new MouseEvent('mouseup',oe));"
                "ov.dispatchEvent(new MouseEvent('click',oe));"
                "}catch(e){} break;}"
                "}"
                "try{"
                "var cx=window.innerWidth/2,cy=window.innerHeight/2;"
                "var el=document.elementFromPoint(cx,cy);"
                "if(el){var ev={bubbles:true,cancelable:true,view:window,clientX:cx,clientY:cy};"
                "el.dispatchEvent(new MouseEvent('mousedown',ev));"
                "el.dispatchEvent(new MouseEvent('mouseup',ev));"
                "el.dispatchEvent(new MouseEvent('click',ev));}"
                "}catch(e){}"
                "var vs=document.querySelectorAll('video');"
                "for(var i=0;i<vs.length;i++){"
                "(function(v){"
                "try{"
                "var opts={bubbles:true,cancelable:true,view:window};"
                "v.dispatchEvent(new MouseEvent('mousedown',opts));"
                "v.dispatchEvent(new MouseEvent('mouseup',opts));"
                "v.dispatchEvent(new MouseEvent('click',opts));"
                "}catch(e){}"
                "v.muted=false;"
                "var p=v.paused?v.play():Promise.resolve();"
                "if(p&&p.then){"
                "p.catch(function(){"
                "v.muted=true;"
                "var p2=v.play();"
                "if(p2&&p2.then){p2.then(function(){"
                "setTimeout(function(){v.muted=false;},150);}).catch(function(){});}"
                "});"
                "}"
                "})(vs[i]);"
                "}"
                "})()"
            )

            time.sleep(2.0)
            try:
                page.evaluate(_GESTURE_JS)
                logger.debug("CDP: injected tap-to-play gesture (initial)")
            except Exception as exc:
                logger.debug("CDP: tap-to-play inject failed (non-fatal): %s", exc)

            # Poll loop - deadline starts now (after navigation + gesture)
            loop_deadline = time.monotonic() + timeout
            _last_gesture_t = time.monotonic()
            _GESTURE_INTERVAL = 5.0  # re-inject gesture every 5s until URL found
            _poll_tick = 0
            while time.monotonic() < loop_deadline:
                if hls_url:
                    break
                if _cancelled():
                    return None, {}

                # Re-inject gesture every 5s -- Instagram SPA may render the
                # live player at any point during the 120s window.
                now = time.monotonic()
                if now - _last_gesture_t >= _GESTURE_INTERVAL:
                    _last_gesture_t = now
                    try:
                        page.evaluate(_GESTURE_JS)
                        logger.debug("CDP: re-injected tap-to-play gesture")
                    except Exception:
                        pass

                _poll_tick += 1
                try:
                    # Layer D: performance resource entries (.m3u8 / .mpd)
                    val = page.evaluate(
                        "(function(){"
                        "try{"
                        "var e=performance.getEntriesByType('resource');"
                        "for(var i=0;i<e.length;i++){"
                        "var u=e[i].name;"
                        "if(u&&(u.indexOf('.m3u8')!==-1||u.indexOf('.mpd')!==-1))"
                        "return u;"
                        "}"
                        "}catch(ex){}"
                        "return '';"
                        "})()"
                    )
                    if val and (".m3u8" in val or ".mpd" in val):
                        logger.info("CDP[D]: stream URL via performance API: %.120s", val)
                        hls_url = val
                        break
                except Exception:
                    pass

                # Layer E: scan JS globals every 5 ticks (~2.5s)
                # Instagram embeds playback_url / dash_abr_playback_url inside
                # multiple window globals depending on IG version/rollout.
                if not hls_url and _poll_tick % 5 == 0:
                    try:
                        val = page.evaluate(
                            "(function(){"
                            "try{"
                            # Search multiple IG data globals for stream URLs
                            "var globs=['__additionalData','__initialData',"
                            "'__reactData','__initialDataLoaded','__bbox'];"
                            "for(var gi=0;gi<globs.length;gi++){"
                            " try{"
                            "  var src=JSON.stringify(window[globs[gi]]||{});"
                            '  var m=src.match(/"(https:[^"]+\\.(?:m3u8|mpd)[^"]*)"/i);'
                            "  if(m)return decodeURIComponent(m[1].replace(/\\\\\\\\/g,'/'));"
                            " }catch(ex2){}"
                            "}"
                            # Scan script[type=application/json] tags (SSR data)
                            "var scripts=document.querySelectorAll('script[type=\"application/json\"]');"
                            "for(var si=0;si<scripts.length;si++){"
                            " try{"
                            '  var re2=/"(https:[^"]+\\.(?:m3u8|mpd)[^"]*)"/i;'
                            "  var m2=scripts[si].textContent.match(re2);"
                            "  if(m2)return decodeURIComponent(m2[1].replace(/\\\\\\\\/g,'/'));"
                            " }catch(ex3){}"
                            "}"
                            # Fallback: video element src / currentSrc
                            "var v=document.querySelector('video');"
                            "if(v&&v.src&&(v.src.indexOf('.m3u8')!==-1||v.src.indexOf('.mpd')!==-1))"
                            "return v.src;"
                            "var cs=v?v.currentSrc:'';"
                            "if(cs&&(cs.indexOf('.m3u8')!==-1||cs.indexOf('.mpd')!==-1))"
                            "return v.currentSrc;"
                            "}catch(ex){}"
                            "return '';"
                            "})()"
                        )
                        if val and ("http" in val) and (".m3u8" in val or ".mpd" in val):
                            logger.info("CDP[E]: stream URL via JS globals: %.120s", val)
                            hls_url = val
                            break
                    except Exception:
                        pass

                time.sleep(0.5)

            if not hls_url:
                logger.warning(
                    "CDP: no stream URL captured in %.0fs after navigation. "
                    "Instagram may require login or is showing app-only wall.",
                    timeout,
                )

    finally:
        # Kill the browser process tree. On Windows, proc.terminate() only
        # signals the parent; GPU/renderer child processes keep running and
        # hold the debug port, causing ECONNREFUSED on the next launch.
        # taskkill /F /T kills the entire process tree.
        try:
            if sys.platform == "win32":
                subprocess.run(  # noqa: S603
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_WIN_NO_WINDOW,
                    check=False,
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=3)
                    except Exception:
                        pass
        except Exception:
            pass
        # Clean up temp profile dir -- a persistent profile is deliberately
        # left in place so device cookies (datr/mid/ig_did) survive to the
        # next recording.
        if _is_temp_profile:
            try:
                shutil.rmtree(tmp_profile, ignore_errors=True)
            except Exception:
                pass
        # Give OS time to release the debug port before next retry attempts
        # to bind a new Brave instance (prevents ECONNREFUSED on reuse).
        time.sleep(1.5)

    return hls_url, _captured_browser_headers


class InstagramLiveEngine:
    """
    Records an Instagram Live stream to a .ts file.

    Primary method: CDP (Playwright) to intercept HLS URL from browser.
    Fallback: Instagram profile API (works only when API returns HLS URL).
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config

    def download(
        self,
        task: DownloadTask,
        on_progress: Optional[Callable[[DownloadTask], None]] = None,
        on_postprocess: Optional[Callable[[DownloadTask], None]] = None,
    ) -> None:
        url = task.url
        username = self._extract_username(url, task)

        # Initial capture (full 120s window): CDP intercept then API fallback.
        hls_url, _browser_headers = self._capture_stream_url(task, url, username, _CDP_HLS_WAIT_S)

        if not hls_url:
            # "not currently live" marker: download_manager skips retries and the
            # live monitor tab resets the item to WAITING instead of ERROR.
            raise RuntimeError(
                f"not currently live: @{username} hien khong co live stream nao dang phat, "
                "hoac khong the lay duoc HLS URL.\n\n"
                "Hay dam bao:\n"
                "- Da dang nhap Instagram trong Brave/Chrome va luu cookies\n"
                "- Stream dang phat tai thoi diem tai\n"
                "- Da trich xuat cookies Instagram trong Settings"
            )

        # Build output path. DASH (.mpd) -> matroska (.mkv) because .ts cannot
        # hold every codec DASH may use; HLS (.m3u8) -> mpegts (.ts).
        output_dir = (Path(task.output_dir) if task.output_dir else self._config.download_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        bid_match = _BROADCAST_ID_FROM_URL_RE.search(url)
        broadcast_id = bid_match.group(1)[:12] if bid_match else "live"
        rec_ts = time.strftime("%Y-%m-%d %H-%M")
        is_dash = bool(hls_url and ".mpd" in hls_url.lower())
        ext = ".mkv" if is_dash else ".ts"
        raw_name = _sanitise_filename(f"{username} - [LIVE] {rec_ts} [{broadcast_id}]", max_len=180) + ext
        output_path = output_dir / raw_name

        with task._lock:
            task.filename = str(output_path)

        # Locate FFmpeg
        from utils.ffmpeg_locator import locate_ffmpeg  # noqa: PLC0415

        loc = locate_ffmpeg()
        if loc is None:
            raise RuntimeError(
                "Khong tim thay FFmpeg.\n"
                "Dat ffmpeg.exe vao thu muc resources/ffmpeg/ hoac cai FFmpeg len PATH."
            )
        ffmpeg_bin = str(Path(loc.ffmpeg_bin))

        # Re-capture + resume loop. Instagram's live DASH .mpd is a snapshot whose
        # segment window goes stale after a few minutes, stalling FFmpeg even
        # though the stream is still live. On a stall (or clean EOF) we re-capture
        # a fresh URL and resume into a new .partN file, then concat at the end.
        # The loop stops when re-capture yields no URL (stream genuinely ended),
        # mirroring the TikTok BUG-TT-17 mechanism in yt_dlp_engine.
        parts: list[Path] = []
        total_bytes = 0
        attempt = 0
        empties = 0
        cancelled = False
        ffmpeg_err = False

        while attempt <= _MAX_RESUME_ATTEMPTS:
            part_path = Path(f"{output_path}.part{attempt}")
            reason, n = self._record_segment(
                task,
                hls_url,
                _browser_headers,
                url,
                part_path,
                is_dash,
                ffmpeg_bin,
                total_bytes,
                on_progress,
            )
            if n > _MIN_PART_BYTES:
                parts.append(part_path)
                total_bytes += n
                empties = 0
            else:
                part_path.unlink(missing_ok=True)
                empties += 1

            if reason == "cancelled":
                cancelled = True
                break
            if reason == "ffmpeg_error" and not parts:
                ffmpeg_err = True
                break
            if empties >= _MAX_EMPTY_PARTS:
                break

            logger.info(
                "BUG-IG-RESUME: FFmpeg %s after %s recorded (attempt %d) -- re-capturing stream URL",
                reason,
                _fmt_bytes(n),
                attempt,
            )
            if task.is_cancellation_requested:
                cancelled = True
                break
            try:
                hls_url, _browser_headers = self._capture_stream_url(task, url, username, _RESUME_CDP_WAIT_S)
            except Exception:
                # DownloadError on mid-capture cancellation, or any capture error:
                # finalize what we have rather than discarding the recording.
                if task.is_cancellation_requested:
                    cancelled = True
                    break
                hls_url = None
            if not hls_url:
                logger.info(
                    "BUG-IG-RESUME: re-capture returned no URL -- stream ended, finalizing %d part(s)",
                    len(parts),
                )
                break
            attempt += 1

        # Finalize: nothing usable -> raise; one part -> rename; many -> concat.
        if not parts:
            if ffmpeg_err:
                raise RuntimeError(
                    "FFmpeg ket thuc voi loi va khong ghi duoc du lieu.\n"
                    "Kiem tra omnidl_run.log de biet chi tiet."
                )
            raise RuntimeError(
                "FFmpeg khong ghi duoc du lieu tu stream (stream da ket thuc hoac URL het han).\n"
                "Thu lai ngay khi stream dang phat."
            )

        if len(parts) == 1:
            parts[0].replace(output_path)
        else:
            logger.info("BUG-IG-RESUME: concatenating %d parts -> %s", len(parts), output_path)
            _concat_parts(ffmpeg_bin, parts, output_path)
            for p in parts:
                p.unlink(missing_ok=True)

        with task._lock:
            task.filename = str(output_path)

        if cancelled:
            import yt_dlp  # noqa: PLC0415

            raise yt_dlp.utils.DownloadError("Cancelled by user")

        logger.info("InstagramLiveEngine: recording complete -> %s", output_path)

    def _capture_stream_url(
        self,
        task: DownloadTask,
        url: str,
        username: str,
        timeout: float,
    ) -> tuple[Optional[str], dict]:
        """Capture a live stream URL: CDP intercept (Windows/macOS) then API
        fallback. Returns (url_or_None, browser_headers). Raises DownloadError
        when the task is cancelled mid-capture."""
        hls_url: Optional[str] = None
        browser_headers: dict = {}
        if sys.platform in ("win32", "darwin"):
            browser = getattr(self._config, "cookies_browser", "brave") or "brave"
            try:
                from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
                    _resolve_cookie,
                )

                _profile_dir = str(self._config.config_path.parent / "cdp_profiles" / "instagram")
                hls_url, browser_headers = _cdp_intercept_hls(
                    url,
                    browser,
                    timeout,
                    cookie_file=_resolve_cookie(url, self._config),
                    cancel_check=lambda: task.is_cancellation_requested,
                    profile_dir=_profile_dir,
                )
            except RuntimeError as exc:
                logger.warning("CDP HLS intercept failed: %s -- trying API fallback", exc)
            except Exception as exc:
                logger.warning("CDP HLS intercept error: %s -- trying API fallback", exc)

        if task.is_cancellation_requested:
            import yt_dlp  # noqa: PLC0415

            raise yt_dlp.utils.DownloadError("Cancelled by user")

        if not hls_url:
            hls_url = self._api_hls_fallback(
                url, username, cancel_check=lambda: task.is_cancellation_requested
            )
            if task.is_cancellation_requested:
                import yt_dlp  # noqa: PLC0415

                raise yt_dlp.utils.DownloadError("Cancelled by user")

        return hls_url, browser_headers

    def _record_segment(
        self,
        task: DownloadTask,
        hls_url: str,
        browser_headers: dict,
        url: str,
        output_path: Path,
        is_dash: bool,
        ffmpeg_bin: str,
        bytes_base: int,
        on_progress: Optional[Callable[[DownloadTask], None]],
    ) -> tuple[str, int]:
        """Record one FFmpeg segment to *output_path*.

        Returns ``(reason, bytes_written)`` where ``reason`` is one of:
          "clean"        -- FFmpeg exited 0 (input/manifest ended)
          "stall"        -- no bytes written within the watchdog window
          "ffmpeg_error" -- FFmpeg exited non-zero
          "cancelled"    -- user requested cancellation
        Never raises on stall/EOF so the caller can re-capture and resume.
        Raising FileNotFoundError->RuntimeError on Popen is preserved.
        """
        # Prefer browser-captured headers (the exact request the browser used to
        # fetch the stream URL); fall back to cookie-file headers.
        if browser_headers:
            from utils.instagram_http import is_ig_cdn_host, strip_cdn_headers  # noqa: PLC0415

            _effective_headers = browser_headers
            if is_ig_cdn_host(hls_url):
                _effective_headers = strip_cdn_headers(browser_headers)
                _dropped = sorted(set(browser_headers) - set(_effective_headers))
                if _dropped:
                    logger.info("BUG-IG-CDN: dropping %s before CDN segment fetch", _dropped)
            _SKIP_HEADERS = {"accept-encoding", "connection", "host", "content-length"}
            headers_arg = "".join(
                f"{k}: {v}\r\n" for k, v in _effective_headers.items() if k.lower() not in _SKIP_HEADERS
            )
            logger.debug("FFmpeg: using %d browser-captured headers", len(_effective_headers))
        else:
            headers_arg = self._build_ffmpeg_headers(hls_url)

        if is_dash:
            container_args = ["-f", "matroska"]
            input_args = [
                "-reconnect",
                "1",
                "-reconnect_on_network_error",
                "1",
                "-reconnect_delay_max",
                "5",
                "-timeout",
                "10000000",
                "-allowed_extensions",
                "ALL",
                "-protocol_whitelist",
                "https,tls,tcp,http,crypto,data",
                "-headers",
                headers_arg,
                "-i",
                hls_url,
            ]
            # DASH MPD exposes multiple video representations; without -map FFmpeg
            # parses the MPD but writes nothing. Audio is optional (0:a:0?).
            map_args = ["-map", "0:v:0", "-map", "0:a:0?"]
        else:
            container_args = ["-f", "mpegts"]
            input_args = [
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-timeout",
                "10000000",
                "-protocol_whitelist",
                "https,tls,tcp,http,crypto,data",
                "-headers",
                headers_arg,
                "-i",
                hls_url,
            ]
            map_args = []

        cmd = [
            ffmpeg_bin,
            "-y",
            # Without -loglevel warning, FFmpeg's per-segment stats fill the
            # stderr pipe buffer (~64KB) on long recordings; once full, FFmpeg
            # blocks on write and the stall watchdog kills a healthy recording.
            "-hide_banner",
            "-loglevel",
            "warning",
            *input_args,
            *map_args,
            "-c",
            "copy",
            *container_args,
            str(output_path),
        ]
        logger.info("InstagramLiveEngine: FFmpeg | out=%s", output_path)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=_WIN_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Khong khoi dong duoc FFmpeg ({ffmpeg_bin}).\nKiem tra lai bundle hoac cai FFmpeg vao PATH."
            ) from exc

        # Drain stderr continuously so the pipe never fills and blocks FFmpeg.
        _stderr_lines: deque[str] = deque(maxlen=40)

        def _drain_stderr(pipe, lines: "deque[str]") -> None:
            try:
                for raw in pipe:
                    lines.append(raw.decode("utf-8", errors="replace").rstrip())
            except Exception:
                pass

        if proc.stderr:
            threading.Thread(
                target=_drain_stderr,
                args=(proc.stderr, _stderr_lines),
                daemon=True,
            ).start()

        _prev_size = 0
        _prev_time = time.monotonic()
        _last_growth_t = time.monotonic()
        # DASH needs time to parse MPD, fetch init segment, then media segments;
        # 90s grace covers slow CDN. Tightens to 30s once data is flowing.
        _STALL_TIMEOUT = 90.0 if is_dash else 30.0
        _data_ever_written = False
        _cancelled = False
        ret: Optional[int] = None

        try:
            while True:
                if task.is_cancellation_requested:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    _cancelled = True
                    break

                ret = proc.poll()
                if ret is not None:
                    break

                now = time.monotonic()
                elapsed = max(now - _prev_time, 0.001)
                cur_size = output_path.stat().st_size if output_path.exists() else 0
                delta = cur_size - _prev_size
                speed_bs = delta / elapsed if elapsed > 0 else 0

                if delta > 0:
                    _last_growth_t = now
                    if not _data_ever_written:
                        _data_ever_written = True
                        _STALL_TIMEOUT = 30.0
                elif now - _last_growth_t > _STALL_TIMEOUT:
                    _stall_msg = "\n".join(_stderr_lines)[-600:] or "(no stderr output)"
                    logger.warning(
                        "InstagramLiveEngine: FFmpeg stalled"
                        " (no bytes written in %.0fs) -- terminating | stderr: %s",
                        _STALL_TIMEOUT,
                        _stall_msg,
                    )
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    break

                with task._lock:
                    task.status = DownloadStatus.DOWNLOADING
                    task.downloaded_bytes = bytes_base + cur_size
                    task.total_bytes = 0
                    task.progress = 50.0
                    task.speed = _fmt_speed(speed_bs)
                    task.eta = ""

                if on_progress:
                    on_progress(task)

                _prev_size = cur_size
                _prev_time = now
                time.sleep(2)

        finally:
            try:
                proc.kill()
            except Exception:
                pass

        final_size = output_path.stat().st_size if output_path.exists() else 0
        if _cancelled:
            return "cancelled", final_size
        if ret is not None and ret != 0:
            logger.error(
                "InstagramLiveEngine: FFmpeg exited %d | stderr: %s",
                ret,
                "\n".join(_stderr_lines)[-600:],
            )
            return "ffmpeg_error", final_size
        if ret is None:
            return "stall", final_size
        return "clean", final_size

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_username(self, url: str, task: Optional[DownloadTask] = None) -> str:
        m = _USER_LIVE_RE.search(url)
        if m:
            return m.group(1).lower()
        if task is not None and task.media_info:
            uploader = (task.media_info.uploader or "").strip()
            if uploader:
                return uploader.lower().lstrip("@")
        if _INSTAGRAM_LIVE_RE.search(url):
            # /live/<id> form carries no username; the broadcast id in the URL
            # still distinguishes the output filename.
            return "instagram"
        raise RuntimeError(
            "unsupported url: Khong the trich xuat username tu URL Instagram Live.\n"
            "Dung dinh dang: https://www.instagram.com/username/live/"
        )

    def _api_hls_fallback(
        self,
        url: str,
        username: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_path = _resolve_cookie(url, self._config)
        if not cookie_path:
            return None

        usable, is_temp = _prepare_cookie_for_use(cookie_path)
        try:
            return self._api_get_hls(url, username, usable, cancel_check=cancel_check)
        except Exception as exc:
            logger.debug("API fallback failed: %s", exc)
            return None
        finally:
            if is_temp:
                try:
                    Path(usable).unlink(missing_ok=True)
                except Exception:
                    pass

    def _api_get_hls(
        self,
        url: str,
        username: str,
        cookie_file: str,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        from utils.instagram_http import (  # noqa: PLC0415
            WWW_API_BASE,
            build_web_headers,
            get_shared_session,
            record_www_claim,
        )

        _cancelled = cancel_check or (lambda: False)
        jar = MozillaCookieJar()
        try:
            jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
        except Exception:
            return None

        ig_cookies = {c.name: c.value for c in jar if "instagram.com" in c.domain}
        if "sessionid" not in ig_cookies:
            return None

        # BUG-IG-ANTIBOT: one browser-shaped identity on the www host for every
        # call this engine makes — no mobile UA, no private mobile API host,
        # and no viewer-heartbeat call (that endpoint registers the account
        # as an active live viewer, a participation action, not a read).
        session = get_shared_session(jar)
        web_headers = build_web_headers(
            referer=f"https://www.instagram.com/{username}/",
            csrftoken=ig_cookies.get("csrftoken") or "",
        )
        proxies = {"http": self._config.proxy, "https": self._config.proxy} if self._config.proxy else None

        bid_match = _BROADCAST_ID_FROM_URL_RE.search(url)
        broadcast_id = bid_match.group(1) if bid_match else ""

        if not broadcast_id:
            if _cancelled():
                return None
            try:
                resp = session.get(
                    f"{WWW_API_BASE}/users/web_profile_info/",
                    params={"username": username},
                    headers=web_headers,
                    cookies=jar,
                    proxies=proxies,
                    timeout=_REQUEST_TIMEOUT,
                )
                record_www_claim(resp.headers)
                if resp.ok:
                    data = resp.json()
                    user = data.get("data", {}).get("user") or data.get("user") or {}
                    broadcast_id = (
                        str(user.get("live_broadcast_id") or "")
                        or str((user.get("broadcast") or {}).get("id") or "")
                        or str((user.get("active_live_info") or {}).get("broadcast_id") or "")
                    ).strip()
            except Exception:
                return None

        if not broadcast_id:
            return None

        if _cancelled():
            return None
        try:
            r = session.get(
                f"{WWW_API_BASE}/live/{broadcast_id}/get_info/",
                headers=web_headers,
                cookies=jar,
                proxies=proxies,
                timeout=_REQUEST_TIMEOUT,
            )
            record_www_claim(r.headers)
            if r.ok:
                d = r.json()
                broadcast = d.get("broadcast") or d
                hls = (
                    broadcast.get("playback_url")
                    or broadcast.get("dash_abr_playback_url")
                    or broadcast.get("dash_live_master_template_url")
                )
                if hls and hls.startswith("http"):
                    logger.debug("API fallback: HLS from get_info")
                    return hls
        except Exception:
            pass

        return None

    def _build_ffmpeg_headers(self, target_url: str) -> str:
        from utils.instagram_http import is_ig_cdn_host  # noqa: PLC0415

        # CDN host (fbcdn.net / cdninstagram.com): the pre-signed URL carries
        # its own auth (oh=/oe=) — a browser never sends instagram.com cookies
        # cross-site to the CDN, so neither should we. UA + Referer + Origin
        # only, matching root cause #1 (BUG-IG-CDN).
        if is_ig_cdn_host(target_url):
            return (
                "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36\r\n"
                "Referer: https://www.instagram.com/\r\n"
                "Origin: https://www.instagram.com\r\n"
            )

        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_header = ""
        cookie_path = _resolve_cookie(target_url, self._config)
        if cookie_path:
            usable, is_temp = _prepare_cookie_for_use(cookie_path)
            try:
                jar = MozillaCookieJar()
                jar.load(usable, ignore_discard=True, ignore_expires=True)
                cookie_header = "; ".join(f"{c.name}={c.value}" for c in jar if "instagram.com" in c.domain)
            except Exception:
                pass
            finally:
                if is_temp:
                    try:
                        Path(usable).unlink(missing_ok=True)
                    except Exception:
                        pass

        parts = (
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
            f"X-IG-App-ID: {_IG_APP_ID}\r\n"
            "Referer: https://www.instagram.com/\r\n"
            "Origin: https://www.instagram.com\r\n"
        )
        if cookie_header:
            parts += f"Cookie: {cookie_header}\r\n"
        return parts


def _concat_parts(ffmpeg_bin: str, parts: "list[Path]", output: Path) -> None:
    """Concatenate resume segments into *output* via the FFmpeg concat demuxer
    (-c copy). Works for .mkv (DASH) and .ts (HLS); binary append is not safe for
    matroska. Mirrors the concat command in ffmpeg_convert_service._concat."""

    def _escape(p: Path) -> str:
        return p.as_posix().replace("'", "'\\''")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        delete=False,
        dir=str(output.parent),
        encoding="utf-8",
    ) as lf:
        for p in parts:
            lf.write(f"file '{_escape(p)}'\n")
        list_file = Path(lf.name)

    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output),
        ]
        result = subprocess.run(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
            creationflags=_WIN_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg concat that bai (ma loi {result.returncode}): {err}")
    finally:
        list_file.unlink(missing_ok=True)


# ── Formatting helpers ─────────────────────────────────────────────────────────


def _fmt_bytes(n: int) -> str:
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MiB"
    if n >= 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n} B"


def _fmt_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} KB/s"
    return f"{bps:.0f} B/s"
