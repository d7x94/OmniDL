"""
infrastructure/downloader/waaw_engine.py
========================================
waaw.ac video downloader — CDP via Playwright (same pattern as facebook_story_engine).

Anti-bot overview
─────────────────
waaw.ac uses WASM-computed `htoken` and click-event tokens that cannot be replicated
in Python without JS execution. The only viable strategy is to intercept the CDN URL
via three layers while the page runs normally in a real browser:
  Layer A: page.on("request")
  Layer B: page.on("response") — MIME video/*
  Layer C: CDP Network.requestWillBeSent — catches WASM-initiated requests

CDN URL signature: ?serv=1&md5=<hash>&time=<ts>&ip=<b64>&userid=

Platform support: Windows and macOS only (requires local Brave/Chrome).
Linux: raises RuntimeError immediately.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from domain.models.download_task import DownloadTask
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_WAAW_URL_RE = re.compile(r"waaw\.ac/f/[A-Za-z0-9_-]+", re.I)
_STATIC_ASSET_RE = re.compile(r"\.(?:css|js|png|jpe?g|gif|svg|woff2?|ico|ttf)(?:\?|$)", re.I)

# Ad/tracker domains that hijack the play click via an overlay (see log
# analysis). Brave Shields blocks these in a normal session; the CDP-driven
# launch does not.
#
# BUG-WAAW-02: the VAST preroll waterfall (twinrdsyte/megawebify/
# videosprofitnetwork/magsrv/vstserv/yomeno) and the bigboxads/
# videocdnmetrika/netu.php metric feeders are deliberately NOT blocked here —
# those exchanges are what compute player/get_md5.php's `adscore` field.
# Blocking them left `adscore` permanently empty and the player stuck on
# `need_captcha`, so `obf_link` was never released. Only the overlay/
# click-stealer domains below are blocked; do not re-add the waterfall/
# metric hosts without re-verifying adscore still populates.
_AD_BLOCK_URLS = [
    "*/ad/banner/*",
    "*counter.yadro.ru*",
]

# JS-side mirror of _is_waaw_cdn_url()
_JS_MATCH = (
    "(u.indexOf('no_video')===-1&&u.indexOf('//127.0.0.1')===-1&&u.indexOf('//localhost')===-1"
    "&&((u.indexOf('serv=')!==-1&&u.indexOf('md5=')!==-1)||u.indexOf('.m3u8')!==-1))"
)

# Poll JS: scans the native Resource Timing buffer for a matching URL.
# Does NOT patch window.fetch/XMLHttpRequest.prototype.open — waaw.ac's
# anti-bot appears to fingerprint tampered natives (every capture attempt
# against this site fed a honeypot decoy stream regardless of tactic; the
# fetch/XHR patch this replaced was the one JS-visible mutation happening
# before any site script ran). CDP layers (page.on/Network.requestWillBeSent)
# already see every request without touching page globals, so this fallback
# only needs to read the standard, unmodified Performance API.
_POLL_JS = (
    "(function(){"
    " try{"
    "  var es=performance.getEntriesByType('resource');"
    "  for(var i=0;i<es.length;i++){"
    "   var u=es[i].name||'';"
    "   if(" + _JS_MATCH + ")return u;"
    "  }"
    " }catch(e){}"
    " return '';"
    "})()"
)


def is_waaw_url(url: str) -> bool:
    return bool(_WAAW_URL_RE.search(url))


# Host-specific on purpose — must not hijack arbitrary m3u8/mp4 URLs pasted
# from other sites (unlike _is_waaw_cdn_url, used only to filter intercepted
# network traffic already known to originate from a waaw.ac page).
_WAAW_CDN_LINK_RE = re.compile(r"https?://[a-z0-9-]+\.cf[a-z0-9]*cdn\.com/", re.I)


def is_waaw_cdn_link(url: str) -> bool:
    """True for a waaw CDN link pasted directly (e.g. via File Centipede)."""
    if not _WAAW_CDN_LINK_RE.match(url):
        return False
    path = url.split("?", 1)[0]
    return ".m3u8" in path or path.endswith(".mp4")


def _is_waaw_cdn_url(url: str) -> bool:
    # waaw.ac's player loads a decoy placeholder (https://127.0.0.1/no_video.mp4.m3u8)
    # before the real stream is unlocked — never a valid CDN URL.
    if "no_video" in url or "//127.0.0.1" in url or "//localhost" in url:
        return False
    # Query-token style: ?serv=<n>&md5=... (serv number varies per server)
    if "serv=" in url and "md5=" in url:
        return True
    # HLS manifest: the page always plays via HLS.js, so any .m3u8 URL is
    # the CDN manifest regardless of path shape (waaw.ac's CDN URL format
    # changes over time; downstream ffmpeg/MP4-magic validation rejects
    # false positives).
    return ".m3u8" in url


def _un(obf: str) -> str:
    # Port of waaw embed.232.js un(): plaintext if it has a ".", else
    # drop first char and decode remaining 3-char groups as %u0XXX code units.
    if "." in obf:
        return obf
    body = obf[1:]
    return "".join(chr(int("0" + body[i : i + 3], 16)) for i in range(0, len(body), 3))


_MANIFEST_URL_RE = re.compile(r"https?://[^\s\"'\\]+\.m3u8[^\s\"'\\]*")
_OBF_LINK_RE = re.compile(r'"obf_link"\s*:\s*"([^"]+)"')
_DECOY_MARKERS = ("no_video", "//127.0.0.1", "//localhost")
# Real host + path required — rejects junk decodes like "https:" (obf_link
# sentinel "0" decodes to "") that pass the decoy-marker check with nothing
# to check against.
_MANIFEST_SHAPE_RE = re.compile(r"^https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+/.+")


def _valid_manifest(manifest: str) -> Optional[str]:
    if any(bad in manifest for bad in _DECOY_MARKERS):
        return None
    if not _MANIFEST_SHAPE_RE.match(manifest):
        logger.debug("waaw: rejected malformed manifest: %r", manifest[:200])
        return None
    return manifest


def _parse_get_md5_manifest(body: str) -> Optional[str]:
    """Parse a player/get_md5.php response body into a CDN manifest URL.

    Raises if `body` isn't valid JSON and no manifest URL/obf_link can be
    regex-recovered either (caller decides how to handle that — unready/
    partial bodies are expected during polling). Returns None if the body
    has no usable `obf_link` (missing/empty), the response is blocked/pending
    (player JS never builds a manifest from these — obf_link is a sentinel),
    or the built/recovered URL decodes to a decoy host or fails URL-shape
    validation — all of these mean "not a real manifest", not an error."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # Some get_md5.php responses come back HTML-wrapped or with escaped
        # JSON that json.loads() can't parse — regex-scan the raw body for
        # an obf_link value or a literal manifest URL before giving up.
        m = _OBF_LINK_RE.search(body)
        if m:
            regex_manifest = "https:" + _un(m.group(1))
            return _valid_manifest(regex_manifest)
        m = _MANIFEST_URL_RE.search(body)
        if m:
            return _valid_manifest(m.group(0))
        raise
    blocked = data.get("blocked") == "1"
    pending = data.get("pending") == "1"
    obf = data.get("obf_link", "")
    manifest = _valid_manifest("https:" + _un(obf)) if not blocked and not pending and obf else None
    if manifest is None:
        logger.debug(
            "waaw: get_md5 no link — need_captcha=%s adscore=%r blocked=%s pending=%s",
            data.get("need_captcha"),
            data.get("adscore"),
            blocked,
            pending,
        )
    return manifest


_NEED_CAPTCHA_RE = re.compile(r'"need_captcha"\s*:\s*"1"')


def _needs_captcha(body: str) -> bool:
    return bool(_NEED_CAPTCHA_RE.search(body))


def _handle_get_md5_paused(
    cdp_session, request_id: str, captured: list[str], captcha_event: threading.Event
) -> None:
    """Fetch response-stage pause for player/get_md5.php — body is guaranteed
    available here (unlike the Network.getResponseBody poll-loop fallback)."""
    body = ""
    manifest = None
    try:
        body_result = cdp_session.send("Fetch.getResponseBody", {"requestId": request_id})
        body = body_result.get("body", "")
        if body_result.get("base64Encoded"):
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        manifest = _parse_get_md5_manifest(body)
    except Exception as exc:
        logger.debug("waaw: get_md5.php body unusable: %r", str(exc)[:200])
    if manifest is not None:
        if not captured:
            captured.append(manifest)
    elif body:
        logger.debug("waaw: get_md5.php body unusable: %r", body[:200])
        if _needs_captcha(body):
            captcha_event.set()
    # BaseException, not Exception: the browser can be killed by the
    # watchdog (once `captured` is non-empty) between the getResponseBody
    # call above and this continue — the pending CDP send then raises
    # asyncio.CancelledError, which is a BaseException subclass and would
    # otherwise dump a full traceback into the log for an expected race.
    try:
        cdp_session.send("Fetch.continueResponse", {"requestId": request_id})
    except BaseException:
        try:
            cdp_session.send("Fetch.continueRequest", {"requestId": request_id})
        except BaseException:
            pass


def _handle_request_paused(
    cdp_session, params: dict, captured: list[str], captcha_event: threading.Event
) -> None:
    req_url = params.get("request", {}).get("url", "")
    request_id = params["requestId"]
    if "player/get_md5.php" in req_url and "responseStatusCode" in params:
        _handle_get_md5_paused(cdp_session, request_id, captured, captcha_event)
        return
    # An empty text/plain 200 reads as "blocked" to VAST-aware ad-block
    # detectors (see log: VIDEOJS MEDIA_ERR_CUSTOM "doesnot allow AdBlock"
    # fired right after this stub). A standard empty-VAST document is what a
    # real ad exchange returns on a "no fill" — same net effect (no ad
    # plays) without tripping that check.
    body = base64.b64encode(b'<VAST version="3.0"></VAST>').decode("ascii")
    try:
        cdp_session.send(
            "Fetch.fulfillRequest",
            {
                "requestId": request_id,
                "responseCode": 200,
                "responseHeaders": [{"name": "Content-Type", "value": "application/xml"}],
                "body": body,
            },
        )
    except Exception:
        pass


def _trusted_click_waaw(page, logger_: logging.Logger) -> bool:
    """CDP-level trusted click (isTrusted=True) on the video element, unlike
    page.evaluate() dispatched events which anti-bot checks can detect and ignore.

    The player lives inside the /e/<id> embed iframe, not the top-level page,
    so the video element must be located via frame_locator (bounding_box on a
    frame_locator result is already page-relative)."""
    box = None
    try:
        box = page.frame_locator("iframe[src*='/e/']").locator("video").first.bounding_box(timeout=1500)
    except Exception:
        pass
    if box:
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        found = True
    else:
        vp = page.viewport_size or {"width": 1280, "height": 720}
        x, y = vp["width"] / 2, vp["height"] / 2
        found = False
    logger_.debug("waaw: click target video_found=%s x=%.0f y=%.0f", found, x, y)
    try:
        page.mouse.click(x, y)
    except Exception as exc:
        logger_.debug("waaw: mouse click failed: %s", exc)
    frame = next((f for f in page.frames if "/e/" in f.url), None)
    try:
        (frame or page).evaluate(
            "(function(){var v=document.querySelector('video');if(v)try{v.play();}catch(e){}})()"
        )
    except Exception:
        pass
    return found


def _cdp_intercept_waaw(
    url: str,
    timeout: float = 60.0,
    on_progress: Optional[Callable[[int, str, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> str:
    """Launch browser, navigate to waaw.ac URL, return CDN video URL."""
    if sys.platform not in ("win32", "darwin"):
        raise RuntimeError("waaw.ac engine yêu cầu Windows hoặc macOS.")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as err:
        raise RuntimeError("Thiếu thư viện Playwright.\nChạy: pip install playwright") from err

    import os
    import subprocess

    from infrastructure.downloader.facebook_story_engine import (
        _clear_crashed_flag,
        _find_browser_exe,
        _free_port,
    )

    def _prog(pct: int, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, "", msg)
            except Exception:
                pass

    exe = _find_browser_exe("brave")
    port = _free_port()

    if sys.platform == "win32":
        local_app = Path(os.environ.get("LOCALAPPDATA", ""))
        profile_base = local_app / "BraveSoftware/Brave-Browser/User Data"
    else:  # darwin
        profile_base = Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser"

    if profile_base.exists():
        _clear_crashed_flag(profile_base)

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
    ]
    _prog(8, "Đang khởi động trình duyệt...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logger.info("waaw: launching browser on port %d (pid=%d)", port, proc.pid)

    cdn_url: Optional[str] = None

    try:
        with sync_playwright() as pw:
            _prog(10, "Đang kết nối CDP...")
            cdp_browser = None
            deadline = time.monotonic() + 30.0
            last_exc: Optional[Exception] = None

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
                    "Không kết nối được CDP.\n"
                    "Đóng trình duyệt hoàn toàn rồi thử lại.\n"
                    f"(chi tiết: {last_exc})"
                )

            ctx = cdp_browser.contexts[0]
            page = ctx.new_page()

            # Layer C: CDP Network events — catches WASM-initiated requests
            cdp_session = ctx.new_cdp_session(page)
            # Explicit buffer sizes resist eviction of the small get_md5.php
            # bodies by the ~40 decoy .mp666/Frag-* responses that can fire
            # in the same instant (see BUG-WAAW-01 log analysis).
            cdp_session.send(
                "Network.enable", {"maxTotalBufferSize": 100_000_000, "maxResourceBufferSize": 10_000_000}
            )
            captured: list[str] = []
            captcha_event = threading.Event()
            t0 = time.monotonic()
            try:
                cdp_session.send(
                    "Fetch.enable",
                    {
                        "patterns": [
                            *[{"urlPattern": p} for p in _AD_BLOCK_URLS],
                            {"urlPattern": "*/player/get_md5.php*", "requestStage": "Response"},
                        ]
                    },
                )
                cdp_session.on(
                    "Fetch.requestPaused",
                    lambda params: _handle_request_paused(cdp_session, params, captured, captcha_event),
                )
            except Exception as exc:
                logger.warning("waaw: Fetch.enable failed — ad requests will not be stealth-blocked: %s", exc)

            def _on_cdp_request(params: dict) -> None:
                req_url = params.get("request", {}).get("url", "")
                if _is_waaw_cdn_url(req_url) and not captured:
                    captured.append(req_url)

            cdp_session.on("Network.requestWillBeSent", _on_cdp_request)

            # Layer D: player/get_md5.php response body — the JSON obf_link
            # field is the ground-truth manifest URL builder input, immune to
            # CDN host/path format rotation (module docstring / brief). Body
            # is not guaranteed ready inside this callback, so only record
            # the requestId here; the poll loop below fetches it.
            get_md5_request_ids: set[str] = set()
            get_md5_done: set[str] = set()

            def _on_cdp_response(params: dict) -> None:
                resp_url = params.get("response", {}).get("url", "")
                if "player/get_md5.php" in resp_url:
                    get_md5_request_ids.add(params.get("requestId", ""))

            cdp_session.on("Network.responseReceived", _on_cdp_response)

            # Layer A: page.on("request") — also keep a bounded trail of
            # non-static requests (with elapsed-since-goto timestamps) so a
            # timeout failure is diagnosable from omnidl_debug.log instead of
            # blind — in particular, whether ad traffic is a one-shot preroll
            # waterfall or re-fires on every click.
            seen_requests: deque[tuple[float, str]] = deque(maxlen=40)

            def _on_req(req) -> None:
                if _is_waaw_cdn_url(req.url) and not captured:
                    captured.append(req.url)
                if not _STATIC_ASSET_RE.search(req.url):
                    seen_requests.append((time.monotonic() - t0, req.url))

            page.on("request", _on_req)

            # Layer B: page.on("response") — MIME video/* or HLS manifest
            def _on_resp(resp) -> None:
                ct = resp.headers.get("content-type", "").lower()
                is_media = ct.startswith("video/") or "mpegurl" in ct
                if is_media and _is_waaw_cdn_url(resp.url) and not captured:
                    captured.append(resp.url)

            page.on("response", _on_resp)

            console_errors: deque[str] = deque(maxlen=40)

            def _on_console(msg) -> None:
                if msg.type == "error":
                    console_errors.append(msg.text)

            page.on("console", _on_console)

            _prog(12, "Đang mở trang waaw.ac...")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass  # ERR_ABORTED is non-fatal

            deadline_box = [time.monotonic() + timeout]
            last_click = 0.0
            last_src = ""
            video_ever_found = False
            clicking_enabled = True
            captcha_notified = False
            _prog(15, "Đang chờ CDN URL...")

            # page.evaluate() has no timeout param — if waaw.ac's JS thread
            # stalls (ad dialog / anti-bot WASM), the call blocks forever and
            # deadline_box below is never rechecked. Force-kill the browser
            # past a hard ceiling, on cancel, or once a URL is captured (the
            # CDP layer fires on a background thread) so the blocked call
            # raises and the loop can exit.
            watchdog_stop = threading.Event()

            def _hang_watchdog() -> None:
                while not watchdog_stop.wait(1.0):
                    hard_deadline = deadline_box[0] + 20.0
                    if time.monotonic() >= hard_deadline or captured or (cancel_check and cancel_check()):
                        try:
                            proc.kill()
                        except Exception:
                            pass
                        return

            watchdog = threading.Thread(target=_hang_watchdog, daemon=True)
            watchdog.start()

            next_click_gap = random.uniform(4.0, 7.0)

            try:
                while time.monotonic() < deadline_box[0]:
                    if cancel_check and cancel_check():
                        raise RuntimeError("Đã hủy bởi người dùng.")

                    if captcha_event.is_set() and not captcha_notified:
                        captcha_notified = True
                        clicking_enabled = False
                        logger.info("waaw: captcha wall detected — waiting for user to solve it in browser")
                        _prog(
                            18,
                            "Trang yêu cầu captcha — hãy giải captcha trong cửa sổ trình duyệt vừa mở...",
                        )
                        deadline_box[0] = time.monotonic() + 180.0

                    if clicking_enabled and time.monotonic() - last_click >= next_click_gap:
                        if _trusted_click_waaw(page, logger):
                            video_ever_found = True
                        last_click = time.monotonic()
                        next_click_gap = random.uniform(4.0, 7.0)

                    # get_md5_request_ids is mutated from the CDP callback thread —
                    # a set diff/copy racing that mutation can raise "Set changed
                    # size during iteration"; just skip this tick and retry.
                    try:
                        pending_ids = tuple(get_md5_request_ids - get_md5_done)
                    except RuntimeError:
                        pending_ids = ()

                    for req_id in pending_ids:
                        try:
                            body_result = cdp_session.send("Network.getResponseBody", {"requestId": req_id})
                        except Exception:
                            continue  # body not ready yet — retry next iteration
                        get_md5_done.add(req_id)
                        body = body_result.get("body", "")
                        try:
                            manifest = _parse_get_md5_manifest(body)
                        except Exception as exc:
                            logger.debug("waaw: get_md5.php body unusable: %r (%s)", body[:200], exc)
                            continue
                        if manifest is None:
                            logger.debug("waaw: get_md5.php body parsed but had no usable obf_link")
                            if _needs_captcha(body):
                                captcha_event.set()
                            continue
                        if not captured:
                            captured.append(manifest)

                    if captured:
                        cdn_url = captured[0]
                        break

                    try:
                        result = page.evaluate(_POLL_JS)
                        if result and _is_waaw_cdn_url(str(result)):
                            cdn_url = str(result)
                            break
                    except Exception:
                        pass

                    # The player lives in the /e/ embed iframe, not the
                    # top-level page — poll it too.
                    embed_frame = next((f for f in page.frames if "/e/" in f.url), None)
                    if embed_frame is not None:
                        try:
                            result = embed_frame.evaluate(_POLL_JS)
                            if result and _is_waaw_cdn_url(str(result)):
                                cdn_url = str(result)
                                break
                        except Exception:
                            pass

                        try:
                            src = str(
                                embed_frame.evaluate(
                                    "(function(){var v=document.querySelector('video');"
                                    "return v?(v.src||v.currentSrc||''):'';})()"
                                )
                                or ""
                            )
                            if src and src != last_src:
                                logger.debug("waaw: video src -> %s", src[:120])
                                last_src = src
                            if src and _is_waaw_cdn_url(src):
                                cdn_url = src
                                break
                        except Exception:
                            pass

                    time.sleep(0.5)
            finally:
                watchdog_stop.set()

            if not cdn_url and captured:
                cdn_url = captured[0]

            if not cdn_url:
                last_err = console_errors[-1][:200] if console_errors else "none"
                trail = [f"{t:.1f}s {u}" for t, u in seen_requests]
                logger.debug(
                    "waaw: no CDN URL after %ds — %d non-static requests seen: %s",
                    int(timeout),
                    len(seen_requests),
                    trail,
                )
                sample = "; ".join(f"{t:.1f}s {u[:100]}" for t, u in list(seen_requests)[-5:]) or "none"
                if captcha_notified:
                    raise RuntimeError(
                        "Không giải captcha kịp thời gian.\n"
                        "Thử lại và giải captcha trong cửa sổ trình duyệt vừa mở, "
                        "hoặc dán link CDN mới lấy từ công cụ khác (vd: cf*cdn.com .m3u8)."
                    )
                raise RuntimeError(
                    f"Không tìm thấy CDN URL sau {int(timeout)} giây.\n"
                    "waaw.ac có thể đã thay đổi cơ chế bảo vệ.\n"
                    f"(video_found={video_ever_found}, console_errors={len(console_errors)}, "
                    f"last_error={last_err})\n"
                    f"Recent requests: {sample}"
                )
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        if profile_base.exists():
            _clear_crashed_flag(profile_base)

    logger.info("waaw: CDN URL captured: %s...", cdn_url[:80])
    return cdn_url


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# CDN links are IP+time-signed; once expired or hit from a different IP the
# host 404s. Not recoverable in code — only the error message can be honest.
_CDN_EXPIRED_MSG = "Link CDN đã hết hạn hoặc bị khoá theo IP - hãy mở lại trang waaw.ac/f/... để lấy link mới"


def _hls_download(
    cdn_url: str,
    dest: Path,
    on_progress: Optional[Callable[[int, str, str], None]] = None,
) -> None:
    """Download HLS manifest → MP4 via ffmpeg remux. Falls back to the bare
    .mp4 URL (path ends ....mp4.m3u8) when ffmpeg is missing or fails."""
    import subprocess

    from utils.ffmpeg_locator import locate_ffmpeg

    def _mp4_fallback() -> None:
        path, sep, query = cdn_url.partition("?")
        if not path.endswith(".m3u8"):
            raise RuntimeError("Không tải được HLS stream và không có URL MP4 thay thế.")
        _stream_download(path[: -len(".m3u8")] + sep + query, dest, on_progress=on_progress)

    loc = locate_ffmpeg()
    if not loc:
        logger.warning("waaw: ffmpeg not available — trying bare .mp4 URL")
        _mp4_fallback()
        return

    if on_progress:
        try:
            on_progress(50, "", "ffmpeg đang tải HLS stream...")
        except Exception:
            pass

    part = dest.with_name(dest.stem + ".part.mp4")
    cmd = [
        loc.ffmpeg_bin,
        "-y",
        "-user_agent",
        _UA,
        "-referer",
        "https://waaw.ac/",
        "-headers",
        "Origin: https://waaw.ac\r\nAccept-Language: en-US,en;q=0.7\r\n",
        "-protocol_whitelist",
        "https,tls,tcp,http,crypto,data",
        "-i",
        cdn_url,
        "-c",
        "copy",
        "-bsf:a",
        "aac_adtstoasc",
        "-movflags",
        "+faststart",
        str(part),
    ]
    logger.debug("waaw: ffmpeg HLS: %s", cdn_url[:80])

    tail = ""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and part.exists() and part.stat().st_size >= 10_240:
            part.rename(dest)
            return
        tail = result.stderr[-300:].decode("utf-8", errors="replace") if result.stderr else ""
        logger.warning("waaw: ffmpeg rc=%d: %s", result.returncode, tail)
    except subprocess.TimeoutExpired:
        logger.warning("waaw: ffmpeg timeout")
        tail = "timeout"
    finally:
        part.unlink(missing_ok=True)

    if "404 Not Found" in tail:
        raise RuntimeError(_CDN_EXPIRED_MSG)

    logger.info("waaw: ffmpeg failed — trying bare .mp4 URL")
    try:
        _mp4_fallback()
    except Exception as exc:
        raise RuntimeError(
            f"Không tải được HLS stream (ffmpeg: {tail or 'lỗi'}; fallback MP4: {exc})"
        ) from exc


def _stream_download(
    cdn_url: str,
    dest: Path,
    on_progress: Optional[Callable[[int, str, str], None]] = None,
) -> None:
    """Stream-download cdn_url → dest (.part then rename). Validates MP4 magic + size."""
    import requests

    headers = {
        "Referer": "https://waaw.ac/",
        "User-Agent": _UA,
        "Origin": "https://waaw.ac",
        "Accept-Language": "en-US,en;q=0.7",
    }
    part = dest.with_suffix(".part")
    resp = requests.get(cdn_url, headers=headers, stream=True, timeout=30)
    if resp.status_code == 404:
        raise RuntimeError(_CDN_EXPIRED_MSG)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    speed_win: list[tuple[float, int]] = []

    with part.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            now = time.monotonic()
            speed_win.append((now, len(chunk)))
            speed_win = [(t, b) for t, b in speed_win if now - t <= 3.0]
            bps = sum(b for _, b in speed_win) / max(now - speed_win[0][0], 0.001) if speed_win else 0.0
            pct = int(downloaded * 100 / total) if total else 0
            if on_progress:
                try:
                    on_progress(pct, f"{bps / 1024:.0f} KB/s", "")
                except Exception:
                    pass

    if part.stat().st_size < 10_240:
        part.unlink(missing_ok=True)
        raise RuntimeError("File tải về quá nhỏ (< 10 KB) — CDN có thể đã chặn.")
    hdr = part.read_bytes()[:12]
    if hdr[4:8] not in (b"ftyp", b"mdat", b"moov", b"wide", b"free"):
        part.unlink(missing_ok=True)
        raise RuntimeError("File tải về không phải MP4 — CDN URL có thể đã hết hạn.")

    part.rename(dest)


class WaawEngine:
    def __init__(self, config: "ConfigManager") -> None:
        self._config = config

    def download(
        self,
        task: "DownloadTask",
        on_progress: Optional[Callable] = None,
        on_postprocess: Optional[Callable] = None,
    ) -> None:
        """Download waaw.ac video. Mutates task in-place; raises RuntimeError on failure."""
        from domain.enums.download_status import DownloadStatus

        with task._lock:
            task.status = DownloadStatus.DOWNLOADING

        output_dir = Path(task.output_dir) if task.output_dir else self._config.download_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        def _prog(pct: int, speed: str, msg: str) -> None:
            with task._lock:
                task.progress = float(pct)
                task.speed = speed
                task.eta = msg
            if on_progress:
                try:
                    on_progress(task)
                except Exception:
                    pass

        if is_waaw_cdn_link(task.url):
            # A pasted CDN link — no browser capture needed, only ffmpeg/requests.
            cdn_url = task.url
            vid_id = Path(cdn_url.split("?", 1)[0]).name
            for suffix in (".m3u8", ".mp4"):
                if vid_id.endswith(suffix):
                    vid_id = vid_id[: -len(suffix)]
        else:
            if sys.platform not in ("win32", "darwin"):
                raise RuntimeError("waaw.ac engine yêu cầu Windows hoặc macOS.\nLinux chưa được hỗ trợ.")
            m = re.search(r"waaw\.ac/f/([A-Za-z0-9_-]+)", task.url, re.I)
            vid_id = m.group(1) if m else ""
            cdn_url = _cdp_intercept_waaw(
                task.url,
                timeout=120.0,
                on_progress=_prog,
                cancel_check=lambda: task.is_cancellation_requested,
            )

        filename = output_dir / f"waaw_{vid_id}.mp4"
        stem = filename.stem
        counter = 1
        while filename.exists():
            filename = output_dir / f"{stem} ({counter}).mp4"
            counter += 1

        if cdn_url.split("?", 1)[0].endswith(".mp4"):
            _stream_download(cdn_url, filename, on_progress=_prog)
        else:
            _hls_download(cdn_url, filename, on_progress=_prog)

        with task._lock:
            task.status = DownloadStatus.COMPLETED
            task.progress = 100.0
            task.filename = str(filename)

        logger.info("waaw: download complete — %s", filename.name)

        if on_progress:
            try:
                on_progress(task)
            except Exception:
                pass
