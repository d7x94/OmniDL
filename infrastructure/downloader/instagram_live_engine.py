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
import socket as _socket
import subprocess
import sys
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

# Instagram Live HLS URLs come from cdninstagram.com or Instagram's edge CDN
# and always end in .m3u8
_IG_HLS_RE = re.compile(
    r"(?:cdninstagram\.com|scontent[^/]*\.instagram\.com|live-upload\.instagram\.com)"
    r".*?\.m3u8",
    re.I,
)
_IG_HLS_PATH_RE = re.compile(
    r"instagram\.com/.*?\.m3u8",
    re.I,
)

_CDP_HLS_WAIT_S = 30.0
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


def _cdp_intercept_hls(live_url: str, browser: str, timeout: float) -> Optional[str]:
    """
    Launch browser, navigate to live_url, intercept HLS .m3u8 URL via CDP.
    Returns the HLS URL string, or None if not found within timeout.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright  # noqa: I001
    except ImportError as err:
        raise RuntimeError(
            "Thieu thu vien Playwright.\nChay: pip install playwright"
        ) from err

    import os

    exe  = _find_browser_exe(browser)
    port = _free_port()

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=Translate",
        "--restore-last-session=false",
        "--no-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
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

                ctx  = cdp_browser.contexts[0]
                page = ctx.new_page()

                # Layer A: Playwright request intercept
                # Log ALL instagram/cdninstagram requests to help diagnose
                def _on_request(request) -> None:
                    nonlocal hls_url
                    u = request.url
                    if ".m3u8" in u:
                        logger.info("CDP[A]: .m3u8 request: %.120s", u)
                        if not hls_url:
                            hls_url = u
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
                    ):
                        logger.info("CDP[B]: HLS via MIME (%s): %.120s", ct, u)
                        hls_url = u
                    elif ".m3u8" in u and "instagram.com" in u:
                        logger.info("CDP[B]: HLS via URL pattern: %.120s", u)
                        hls_url = u

                page.on("response", _on_response)

                # Layer C: CDP Network domain (catches ALL browser requests)
                try:
                    cdp_session = ctx.new_cdp_session(page)
                    cdp_session.send("Network.enable")

                    def _on_cdp_request(params: dict) -> None:
                        nonlocal hls_url
                        u = params.get("request", {}).get("url", "")
                        if not u:
                            return
                        if ".m3u8" in u:
                            logger.info("CDP[C]: .m3u8 via Network domain: %.120s", u)
                            if not hls_url:
                                hls_url = u
                        elif "instagram.com" in u and any(
                            x in u for x in ("/live/", "live-hls", "dash-hls", "playback")
                        ):
                            logger.debug("CDP[C]: IG live-related: %.120s", u)

                    cdp_session.on("Network.requestWillBeSent", _on_cdp_request)
                    logger.debug("CDP[C]: Network domain enabled")
                except Exception as exc:
                    logger.debug("CDP[C]: Network domain unavailable (%s)", exc)

                # Navigate - poll deadline starts AFTER navigation completes
                logger.info("CDP: navigating to %s", live_url[:100])
                try:
                    page.goto(live_url, wait_until="domcontentloaded", timeout=30_000)
                except PWTimeout:
                    logger.debug("CDP: domcontentloaded timeout (non-fatal, continuing poll)")
                except Exception as exc:
                    logger.debug("page.goto warning (non-fatal): %s", exc)

                # Dismiss the Instagram "tap to play" interstitial.
                # The live page renders a black overlay requiring a user gesture
                # before the video player starts and HLS segments are fetched.
                # Inject synthetic mouse events on the overlay and video element
                # to satisfy the autoplay policy (same pattern as facebook_story_engine).
                time.sleep(1.5)
                try:
                    page.evaluate(
                        "(function(){"
                        "var overlaySelectors=['[data-visualcompletion=\"media-vc-image\"]',"
                        "'[role=\"button\"]','._aatk','._aatn','._ab8w'];"
                        "for(var s=0;s<overlaySelectors.length;s++){"
                        " var ov=document.querySelector(overlaySelectors[s]);"
                        " if(ov){try{"
                        "  var oe={bubbles:true,cancelable:true,view:window};"
                        "  ov.dispatchEvent(new MouseEvent('mousedown',oe));"
                        "  ov.dispatchEvent(new MouseEvent('mouseup',oe));"
                        "  ov.dispatchEvent(new MouseEvent('click',oe));"
                        " }catch(e){} break;}"
                        "}"
                        "var vs=document.querySelectorAll('video');"
                        "for(var i=0;i<vs.length;i++){"
                        " (function(v){"
                        "  try{"
                        "   var opts={bubbles:true,cancelable:true,view:window};"
                        "   v.dispatchEvent(new MouseEvent('mousedown',opts));"
                        "   v.dispatchEvent(new MouseEvent('mouseup',opts));"
                        "   v.dispatchEvent(new MouseEvent('click',opts));"
                        "  }catch(e){}"
                        "  v.muted=false;"
                        "  var p=v.paused?v.play():Promise.resolve();"
                        "  if(p&&p.then){"
                        "   p.catch(function(){"
                        "    v.muted=true;"
                        "    var p2=v.play();"
                        "    if(p2&&p2.then){p2.then(function(){"
                        "    setTimeout(function(){v.muted=false;},150);}).catch(function(){});}"
                        "   });"
                        "  }"
                        " })(vs[i]);"
                        "}"
                        "})()"
                    )
                    logger.debug("CDP: injected tap-to-play gesture")
                except Exception as exc:
                    logger.debug("CDP: tap-to-play inject failed (non-fatal): %s", exc)

                # Poll loop - deadline starts now (after navigation + gesture)
                loop_deadline = time.monotonic() + timeout
                while time.monotonic() < loop_deadline:
                    if hls_url:
                        break
                    try:
                        val = page.evaluate(
                            "(function(){"
                            "try{"
                            "var e=performance.getEntriesByType('resource');"
                            "for(var i=0;i<e.length;i++){"
                            "var u=e[i].name;"
                            "if(u&&u.indexOf('.m3u8')!==-1)"
                            "return u;"
                            "}"
                            "}catch(ex){}"
                            "return '';"
                            "})()"
                        )
                        if val and ".m3u8" in val:
                            logger.info("CDP[D]: HLS via performance API: %.120s", val)
                            hls_url = val
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)

                if not hls_url:
                    logger.warning(
                        "CDP: no .m3u8 URL captured in %.0fs after navigation. "
                        "Instagram may require login or is showing app-only wall.",
                        timeout,
                    )

        finally:
            try:
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
            # Give OS time to release the debug port before next retry attempts
            # to bind a new Brave instance (prevents ECONNREFUSED on reuse).
            time.sleep(1.5)

    return hls_url


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
        if sys.platform in ("win32", "darwin"):
            browser = getattr(self._config, "browser", "brave") or "brave"
            try:
                hls_url = _cdp_intercept_hls(url, browser, _CDP_HLS_WAIT_S)
            except RuntimeError as exc:
                logger.warning("CDP HLS intercept failed: %s -- trying API fallback", exc)
            except Exception as exc:
                logger.warning("CDP HLS intercept error: %s -- trying API fallback", exc)

        # Fallback: API
        if not hls_url:
            hls_url = self._api_hls_fallback(url, username)

        if not hls_url:
            raise RuntimeError(
                f"@{username} hien khong co live stream nao dang phat, "
                "hoac khong the lay duoc HLS URL.\n\n"
                "Hay dam bao:\n"
                "- Da dang nhap Instagram trong Brave/Chrome\n"
                "- Stream dang phat tai thoi diem tai\n"
                "- Dong hoan toan Brave/Chrome truoc khi thu lai"
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

        # Build headers for FFmpeg HLS segment auth
        headers_arg = self._build_ffmpeg_headers(url)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-headers", headers_arg,
            "-i", hls_url,
            "-c", "copy",
            "-f", "mpegts",
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

        if ret != 0:
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
