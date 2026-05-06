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
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_IG_APP_ID       = "936619743392459"
_PROFILE_API     = "https://i.instagram.com/api/v1/users/web_profile_info/"
_REQUEST_TIMEOUT = 15

_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_INSTAGRAM_LIVE_RE = re.compile(
    r"instagram\.com/(?:[^/]+/live|live/[^/]+)(?:/|$)", re.I
)
_USER_LIVE_RE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]+)/live", re.I
)
_BROADCAST_ID_FROM_URL_RE = re.compile(
    r"instagram\.com/[^/]+/live/(\d+)", re.I
)

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


def _find_browser_exe(browser: str) -> str:
    browser = browser.lower()
    candidates: list[str] = []
    if sys.platform == "win32":
        if browser == "brave":
            candidates = [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            ]
        else:
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
    elif sys.platform == "darwin":
        home = Path.home()
        if browser == "brave":
            candidates = [
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                str(home / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ]
        else:
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                str(home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ]
    else:
        raise RuntimeError(
            "Instagram Live CDP chi ho tro Windows va macOS.\n"
            "Linux chua duoc ho tro."
        )
    exe = next((p for p in candidates if Path(p).exists()), None)
    if not exe:
        raise RuntimeError(
            f"Khong tim thay {browser.title()}. Hay cai dat trinh duyet truoc.\n"
            "Dam bao da dang nhap Instagram trong trinh duyet do."
        )
    return exe


def _cdp_intercept_hls(
    live_url: str, browser: str, timeout: float
) -> tuple[Optional[str], dict]:
    """
    Launch browser, navigate to live_url, intercept HLS .m3u8 URL via CDP.
    Returns the HLS URL string, or None if not found within timeout.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright  # noqa: I001
        from playwright._impl._api_structures import SetCookieParam  # noqa: PLC0415
    except ImportError as err:
        raise RuntimeError(
            "Thieu thu vien Playwright.\nChay: pip install playwright"
        ) from err

    exe  = _find_browser_exe(browser)
    port = _free_port()

    # Use a temp user-data-dir so the browser always spawns as a fresh process
    # with the CDP port active. Without this, if Brave is already running it
    # forwards the launch to the existing instance (which has no debug port) and
    # the new process exits immediately -> ECONNREFUSED on every attempt.
    tmp_profile = tempfile.mkdtemp(prefix="omnidl_cdp_")

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={tmp_profile}",
        "--no-first-run", "--no-default-browser-check",
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
            deadline    = time.monotonic() + 30.0
            last_exc    = None

            while time.monotonic() < deadline:
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
            _cookie_file_for_inject = getattr(_cdp_intercept_hls, "_cookie_file", None)
            if _cookie_file_for_inject:
                try:
                    from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
                        _prepare_cookie_for_use,
                    )
                    usable_ck, is_temp_ck = _prepare_cookie_for_use(_cookie_file_for_inject)
                    try:
                        jar = MozillaCookieJar()
                        jar.load(usable_ck, ignore_discard=True, ignore_expires=True)
                        far_future = int(time.time()) + 86400 * 365
                        playwright_cookies = [
                            {
                                "name": c.name,
                                "value": c.value or "",
                                # Playwright requires domain WITH leading dot for
                                # subdomain matching; instagram.com cookies must
                                # also match www.instagram.com.
                                "domain": (
                                    c.domain if c.domain.startswith(".")
                                    else f".{c.domain}"
                                ) if c.domain else ".instagram.com",
                                "path": c.path or "/",
                                "secure": bool(c.secure),
                                "httpOnly": False,
                                "sameSite": "None",
                                # Ensure cookies are not treated as session-only
                                # (some jar entries have expires=0 which some
                                # Playwright builds interpret as already-expired).
                                "expires": c.expires if (c.expires and c.expires > 0) else far_future,
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
                u  = response.url
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
                    ct = (
                        headers.get("content-type")
                        or headers.get("Content-Type")
                        or ""
                    ).lower()
                    u = params.get("response", {}).get("url", "")
                    if (
                        "application/dash+xml" in ct
                        or "application/vnd.apple.mpegurl" in ct
                        or "application/x-mpegurl" in ct
                    ):
                        logger.info(
                            "CDP[C]: stream via responseReceived MIME (%s): %.120s",
                            ct, u,
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
            loop_deadline   = time.monotonic() + timeout
            _last_gesture_t = time.monotonic()
            _GESTURE_INTERVAL = 5.0  # re-inject gesture every 5s until URL found
            _poll_tick      = 0
            while time.monotonic() < loop_deadline:
                if hls_url:
                    break

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
                            "  var m=src.match(/\"(https:[^\"]+\\.(?:m3u8|mpd)[^\"]*)\"/i);"
                            "  if(m)return decodeURIComponent(m[1].replace(/\\\\\\\\/g,'/'));"
                            " }catch(ex2){}"
                            "}"
                            # Scan script[type=application/json] tags (SSR data)
                            "var scripts=document.querySelectorAll('script[type=\"application/json\"]');"
                            "for(var si=0;si<scripts.length;si++){"
                            " try{"
                            "  var re2=/\"(https:[^\"]+\\.(?:m3u8|mpd)[^\"]*)\"/i;"
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
        # Clean up temp profile dir
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
        url      = task.url
        username = self._extract_username(url, task)

        # Primary: CDP intercept
        hls_url: Optional[str] = None
        _browser_headers: dict = {}
        if sys.platform in ("win32", "darwin"):
            browser = getattr(self._config, "browser", "brave") or "brave"
            try:
                from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
                    _resolve_cookie,
                )
                # Pass cookie file path to _cdp_intercept_hls via function attribute
                # so it can inject Instagram session cookies into the fresh temp profile.
                _cdp_intercept_hls._cookie_file = (  # type: ignore[attr-defined]
                    _resolve_cookie(url, self._config)
                )
                hls_url, _browser_headers = _cdp_intercept_hls(url, browser, _CDP_HLS_WAIT_S)
            except RuntimeError as exc:
                logger.warning("CDP HLS intercept failed: %s -- trying API fallback", exc)
            except Exception as exc:
                logger.warning("CDP HLS intercept error: %s -- trying API fallback", exc)
            finally:
                _cdp_intercept_hls._cookie_file = None  # type: ignore[attr-defined]

        # Fallback: API
        if not hls_url:
            hls_url = self._api_hls_fallback(url, username)

        if not hls_url:
            raise RuntimeError(
                f"@{username} hien khong co live stream nao dang phat, "
                "hoac khong the lay duoc HLS URL.\n\n"
                "Hay dam bao:\n"
                "- Da dang nhap Instagram trong Brave/Chrome va luu cookies\n"
                "- Stream dang phat tai thoi diem tai\n"
                "- Da trich xuat cookies Instagram trong Settings"
            )

        # Build output path
        output_dir = (
            Path(task.output_dir) if task.output_dir else self._config.download_dir
        ).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        bid_match    = _BROADCAST_ID_FROM_URL_RE.search(url)
        broadcast_id = bid_match.group(1)[:12] if bid_match else "live"
        rec_ts       = time.strftime("%Y-%m-%d %H-%M")
        raw_name     = f"{username} - [LIVE] {rec_ts} [{broadcast_id}].ts"
        for ch in r'<>:"/\|?*':
            raw_name = raw_name.replace(ch, "_")
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

        # Build headers for FFmpeg HLS/DASH segment auth.
        # Prefer browser-captured headers (exact same headers browser used to fetch
        # the stream URL, including any session cookies tied to that request).
        # Fall back to cookie-file-based headers if browser headers are empty.
        if _browser_headers:
            _SKIP_HEADERS = {"accept-encoding", "connection", "host", "content-length"}
            headers_arg = "".join(
                f"{k}: {v}\r\n"
                for k, v in _browser_headers.items()
                if k.lower() not in _SKIP_HEADERS
            )
            logger.debug("FFmpeg: using %d browser-captured headers", len(_browser_headers))
        else:
            headers_arg = self._build_ffmpeg_headers(url)

        # DASH MPD requires different container than HLS.
        # HLS -> mpegts (.ts), DASH -> matroska (.mkv) since .ts doesn't support
        # all codecs that DASH may use. Use extension to detect format.
        is_dash = hls_url and ".mpd" in hls_url.lower()
        if is_dash:
            raw_name = raw_name.replace(".ts", ".mkv")
            output_path = output_dir / raw_name
            with task._lock:
                task.filename = str(output_path)
            container_args = ["-f", "matroska"]
        else:
            container_args = ["-f", "mpegts"]

        # DASH MPD needs different demuxer flags than HLS
        if is_dash:
            input_args = [
                "-reconnect", "1",
                "-reconnect_on_network_error", "1",
                "-reconnect_delay_max", "5",
                "-timeout", "10000000",
                "-allowed_extensions", "ALL",
                # Start from near-live instead of startNumber=0.
                # Instagram DASH live uses a sliding window; older segments are
                # purged server-side. Default live_start_index=0 requests a
                # segment that no longer exists -> 404 -> FFmpeg stalls forever.
                # -3 = start 3 segments before the live edge (always in window).
                "-live_start_index", "-3",
                "-headers", headers_arg,
                "-i", hls_url,
            ]
        else:
            input_args = [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-timeout", "10000000",
                "-headers", headers_arg,
                "-i", hls_url,
            ]

        if is_dash:
            # DASH MPD exposes multiple video representations at different bitrates.
            # Without -map, FFmpeg parses the MPD but writes nothing.
            # Instagram live MPDs seen so far have no audio stream (video-only DASH);
            # audio may be muxed into video or delivered via a separate HLS track.
            # Use 0:a:0? (optional) so FFmpeg records video even when audio is absent.
            map_args = ["-map", "0:v:0", "-map", "0:a:0?"]
        else:
            map_args = []

        cmd = [
            ffmpeg_bin,
            "-y",
            *input_args,
            *map_args,
            "-c", "copy",
            *container_args,
            str(output_path),
        ]
        logger.info(
            "InstagramLiveEngine: FFmpeg | user=@%s | broadcast=%s | out=%s",
            username, broadcast_id, output_path,
        )

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
                f"Khong khoi dong duoc FFmpeg ({ffmpeg_bin}).\n"
                "Kiem tra lai bundle hoac cai FFmpeg vao PATH."
            ) from exc

        _prev_size = 0
        _prev_time = time.monotonic()
        _last_growth_t = time.monotonic()
        # DASH streams need time to parse MPD, fetch init segment, then media segments.
        # 90s grace covers slow CDN + optional audio track resolution.
        _STALL_TIMEOUT = 90.0 if is_dash else 30.0
        _data_ever_written = False

        try:
            while True:
                if task.is_cancellation_requested:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    import yt_dlp  # noqa: PLC0415
                    raise yt_dlp.utils.DownloadError("Cancelled by user")

                ret = proc.poll()
                if ret is not None:
                    break

                now      = time.monotonic()
                elapsed  = max(now - _prev_time, 0.001)
                cur_size = output_path.stat().st_size if output_path.exists() else 0
                delta    = cur_size - _prev_size
                speed_bs = delta / elapsed if elapsed > 0 else 0

                if delta > 0:
                    _last_growth_t = now
                    if not _data_ever_written:
                        _data_ever_written = True
                        _STALL_TIMEOUT = 30.0  # tighten to 30s once data is flowing
                elif now - _last_growth_t > _STALL_TIMEOUT:
                    # FFmpeg is running but writing 0 bytes -- stream ended or
                    # CDN URL is inaccessible.  Kill so the task fails cleanly
                    # instead of hanging indefinitely.
                    _stall_stderr = b""
                    if proc.stderr:
                        try:
                            import os as _os
                            try:
                                _stall_stderr = _os.read(proc.stderr.fileno(), 8192)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    _stall_msg = (
                        _stall_stderr.decode("utf-8", errors="replace")[-600:]
                        if _stall_stderr
                        else "(empty - pipe may have data after kill)"
                    )
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
                    # Read remaining stderr after process ends
                    if proc.stderr and not _stall_stderr:
                        try:
                            _stall_stderr = proc.stderr.read(8192)
                            if _stall_stderr:
                                logger.warning(
                                    "InstagramLiveEngine: FFmpeg stderr after kill: %s",
                                    _stall_stderr.decode("utf-8", errors="replace")[-600:],
                                )
                        except Exception:
                            pass
                    break

                with task._lock:
                    task.status           = DownloadStatus.DOWNLOADING
                    task.downloaded_bytes = cur_size
                    task.total_bytes      = 0
                    task.progress         = 50.0
                    task.speed            = _fmt_speed(speed_bs)
                    task.eta              = ""

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

        if ret is not None and ret != 0:
            stderr_bytes = b""
            if proc.stderr:
                try:
                    stderr_bytes = proc.stderr.read()
                except Exception:
                    pass
            logger.error(
                "InstagramLiveEngine: FFmpeg exited %d | stderr: %s",
                ret, stderr_bytes.decode("utf-8", errors="replace")[-600:],
            )
            raise RuntimeError(
                f"FFmpeg ket thuc voi ma loi {ret}.\n"
                "Kiem tra omnidl_run.log de biet chi tiet."
            )
        if ret is None:
            # Stall kill path: ffmpeg was terminated due to no output
            raise RuntimeError(
                "FFmpeg khong ghi duoc du lieu tu stream (stream da ket thuc hoac URL het han).\n"
                "Thu lai ngay khi stream dang phat."
            )

        logger.info("InstagramLiveEngine: recording complete -> %s", output_path)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_username(self, url: str, task: Optional[DownloadTask] = None) -> str:
        m = _USER_LIVE_RE.search(url)
        if m:
            return m.group(1).lower()
        if task is not None and task.media_info:
            uploader = (task.media_info.uploader or "").strip()
            if uploader:
                return uploader.lower().lstrip("@")
        raise RuntimeError(
            "Khong the trich xuat username tu URL Instagram Live.\n"
            "Dung dinh dang: https://www.instagram.com/username/live/"
        )

    def _api_hls_fallback(self, url: str, username: str) -> Optional[str]:
        import requests  # noqa: PLC0415

        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_path = _resolve_cookie(url, self._config)
        if not cookie_path:
            return None

        usable, is_temp = _prepare_cookie_for_use(cookie_path)
        try:
            return self._api_get_hls(url, username, usable, requests)
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
        self, url: str, username: str, cookie_file: str, requests
    ) -> Optional[str]:
        jar = MozillaCookieJar()
        try:
            jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
        except Exception:
            return None

        ig_cookies = {c.name: c.value for c in jar if "instagram.com" in c.domain}
        if "sessionid" not in ig_cookies:
            return None

        web_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "X-IG-App-ID": _IG_APP_ID,
            "X-CSRFToken": ig_cookies.get("csrftoken", ""),
            "X-IG-WWW-Claim": "0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.instagram.com",
        }
        mobile_headers = {
            "User-Agent": (
                "Instagram 319.0.0.34.109 Android (33/13; 420dpi; 1080x2177; "
                "samsung; SM-G991B; o1s; exynos2100; en_US; 534367769)"
            ),
            "X-IG-App-ID": _IG_APP_ID,
            "X-CSRFToken": ig_cookies.get("csrftoken", ""),
            "Accept": "*/*",
            "Accept-Language": "en-US",
        }
        proxies = (
            {"http": self._config.proxy, "https": self._config.proxy}
            if self._config.proxy else None
        )

        bid_match    = _BROADCAST_ID_FROM_URL_RE.search(url)
        broadcast_id = bid_match.group(1) if bid_match else ""

        if not broadcast_id:
            try:
                resp = requests.get(
                    _PROFILE_API,
                    params={"username": username},
                    headers=web_headers,
                    cookies=jar,
                    proxies=proxies,
                    timeout=_REQUEST_TIMEOUT,
                )
                if resp.ok:
                    data = resp.json()
                    user = (
                        data.get("data", {}).get("user")
                        or data.get("user") or {}
                    )
                    broadcast_id = (
                        str(user.get("live_broadcast_id") or "")
                        or str((user.get("broadcast") or {}).get("id") or "")
                        or str((user.get("active_live_info") or {}).get("broadcast_id") or "")
                    ).strip()
            except Exception:
                return None

        if not broadcast_id:
            return None

        for endpoint in (
            f"https://i.instagram.com/api/v1/live/{broadcast_id}/get_info/",
            f"https://i.instagram.com/api/v1/live/{broadcast_id}/heartbeat_and_get_viewer_count/",
        ):
            try:
                r = requests.get(
                    endpoint, headers=mobile_headers, cookies=jar,
                    proxies=proxies, timeout=_REQUEST_TIMEOUT,
                )
                if not r.ok:
                    continue
                d        = r.json()
                broadcast = d.get("broadcast") or d
                hls      = (
                    broadcast.get("playback_url")
                    or broadcast.get("dash_abr_playback_url")
                    or broadcast.get("dash_live_master_template_url")
                )
                if hls and hls.startswith("http"):
                    logger.debug("API fallback: HLS from %s", endpoint)
                    return hls
            except Exception:
                continue

        return None

    def _build_ffmpeg_headers(self, url: str) -> str:
        from infrastructure.downloader.yt_dlp_engine import (  # noqa: PLC0415
            _prepare_cookie_for_use,
            _resolve_cookie,
        )

        cookie_header = ""
        cookie_path   = _resolve_cookie(url, self._config)
        if cookie_path:
            usable, is_temp = _prepare_cookie_for_use(cookie_path)
            try:
                jar = MozillaCookieJar()
                jar.load(usable, ignore_discard=True, ignore_expires=True)
                cookie_header = "; ".join(
                    f"{c.name}={c.value}"
                    for c in jar
                    if "instagram.com" in c.domain
                )
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


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _fmt_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} KB/s"
    return f"{bps:.0f} B/s"
