"""
infrastructure/downloader/facebook_story_engine.py
===================================================
Facebook Story downloader — CDP via Playwright, isolated from the main pipeline.

Architecture
────────────
1. Browser launch  — `subprocess.Popen` (user's Brave/Chrome, `--remote-debugging-port`)
2. CDP connection  — `playwright.sync_api.connect_over_cdp()` (no `playwright install`)
3. Pre-page inject — `page.add_init_script(_PRE_PAGE_JS)` patches fetch/XHR
4. Navigate        — `page.goto(story_url, wait_until="domcontentloaded")`
5. Intercept       — 3 layers (see below), deadline-based
6. URL cleaning    — strip `bytestart`/`byteend`/`range` params → full video URL
7. Download        — requests stream (300s deadline) → ffmpeg fallback
8. Validate        — MP4 magic bytes + min 100 KB

Platform support
────────────────
Windows — Brave/Chrome from C:\\Program Files\\...
macOS   — Brave/Chrome from /Applications/...app/Contents/MacOS/...
          Note: App Store builds do NOT support --remote-debugging-port.
          Users must install from the vendor website (brave.com / google.com/chrome).
Linux   — Not supported (raises RuntimeError immediately).

Why `connect_over_cdp` (not `playwright launch`)
─────────────────────────────────────────────────
- Uses the user's existing browser → preserves Facebook login session (cookies)
- No `playwright install` → no extra ~150 MB browser binary
- Playwright handles WS stability (replaces ~230 lines of raw WS code)

Security
────────
- CDP port bound to `127.0.0.1` only; open ~45s during download
- CDN URL logged at 80 chars max (no user-identifiable data)
- Browser crash flag cleared after `proc.terminate()`

Dependencies
────────────
- playwright >= 1.40 (pip install playwright — no playwright install needed)
"""
from __future__ import annotations

import logging
import re
import socket as _socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_FB_VIDEO_RE = re.compile(
    r"(?:"
    r"/m1/v/t\d+"        # NEW (2024+) DASH:   /m1/v/t6/HASH
    r"|/o1/v/"           # newer DASH:          /o1/v/HASH
    r"|/v/t(?:42|64|66)" # legacy video types
    r"|bytestart="       # any byte-range segment URL
    r")",
    re.I,
)

_FB_THUMB_RE = re.compile(r"/v/t(?:15|39|51)\b", re.I)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Pre-page script — runs BEFORE Facebook JS loads (via add_init_script)
_PRE_PAGE_JS = (
    "(function(){"
    "if(window.__omni_installed)return;"
    "window.__omni_installed=true;"
    "window.__omni_urls=[];"
    "function _cap(u){"
    "  if(!u||typeof u!=='string'||u.indexOf('fbcdn.net')===-1)return;"
    "  var l=u.toLowerCase();"
    "  if(l.indexOf('/m1/v/t')!==-1||l.indexOf('/o1/v/')!==-1"
    "   ||l.indexOf('/v/t42')!==-1||l.indexOf('/v/t64')!==-1"
    "   ||l.indexOf('/v/t66')!==-1||l.indexOf('bytestart=')!==-1)"
    "   window.__omni_urls.push(u);"
    "}"
    "var _f=window.fetch;"
    "window.fetch=function(i,o){_cap(typeof i==='string'?i:(i&&i.url));return _f.apply(this,arguments);};"
    "var _x=XMLHttpRequest.prototype.open;"
    "XMLHttpRequest.prototype.open=function(m,u){_cap(u);return _x.apply(this,arguments);};"
    "})()"
)

# JS to poll the page for an already-resolved video URL
_POLL_JS = (
    "(function(){"
    # 1. Pre-page interceptor result (fetch/XHR patch)
    "if(window.__omni_urls&&window.__omni_urls.length>0)return window.__omni_urls[0];"
    # 2. Performance API (works even for Service Worker cached loads)
    "try{"
    " var e=performance.getEntriesByType('resource');"
    " for(var i=0;i<e.length;i++){"
    "  var u=e[i].name;"
    "  if(!u||u.indexOf('fbcdn.net')===-1)continue;"
    "  var l=u.toLowerCase();"
    "  if(l.indexOf('/m1/v/t')!==-1||l.indexOf('/o1/v/')!==-1"
    "   ||l.indexOf('/v/t42')!==-1||l.indexOf('/v/t64')!==-1"
    "   ||l.indexOf('/v/t66')!==-1||l.indexOf('bytestart=')!==-1)"
    "   return u;"
    " }"
    "}catch(e){}"
    # 3. video.currentSrc
    "var vs=document.querySelectorAll('video');"
    "for(var j=0;j<vs.length;j++){"
    " var src=vs[j].currentSrc||vs[j].src||'';"
    " if(src&&src.indexOf('fbcdn.net')!==-1)return src;"
    "}"
    "return vs.length>0?'VIDEO_FOUND_NO_SRC':'NO_VIDEO';"
    "})()"
)

# JS to call play() on all video elements (dismiss tap-to-play overlays)
_PLAY_JS = (
    "(function(){"
    "var vs=document.querySelectorAll('video');"
    "for(var i=0;i<vs.length;i++){"
    " vs[i].muted=true;"
    " if(vs[i].paused)try{vs[i].play().catch(function(){});}catch(e){}"
    "}"
    "})()"
)


# ── Public helpers ─────────────────────────────────────────────────────────────

def is_facebook_story_url(url: str) -> bool:
    u = url.lower()
    return "facebook.com/stories/" in u or "fb.watch" in u


# ── URL utilities ──────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """Ensure view_single=1 so Facebook opens the single-story viewer."""
    p = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    q["view_single"] = ["1"]
    return urllib.parse.urlunparse(p._replace(
        query=urllib.parse.urlencode({k: v[0] for k, v in q.items()})
    ))


def _is_fb_video_url(url: str) -> bool:
    if "fbcdn.net" not in url:
        return False
    if _FB_THUMB_RE.search(url):
        return False
    return bool(_FB_VIDEO_RE.search(url))


def _full_video_url(cdn_url: str) -> str:
    """Strip bytestart/byteend params that restrict response to one DASH segment."""
    p = urllib.parse.urlparse(cdn_url)
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    q.pop("bytestart", None)
    q.pop("byteend",   None)
    q.pop("range",     None)
    return urllib.parse.urlunparse(p._replace(
        query=urllib.parse.urlencode({k: v[0] for k, v in q.items()})
    ))


def _validate_mp4(path: Path) -> bool:
    """Return True if file is >= 100 KB and has valid MP4 container magic."""
    try:
        if path.stat().st_size < 100_000:
            return False
        hdr = path.read_bytes()[:12]
        return hdr[4:8] in (b"ftyp", b"mdat", b"moov", b"wide", b"free")
    except Exception:
        return False


def _clear_crashed_flag(profile_dir: Path) -> None:
    """Set exit_type=Normal in Brave Preferences so it won't show Restore dialog.

    Called BEFORE launch (prevent dialog on fresh start) AND
    AFTER terminate (undo the Crashed state our kill creates).

    Uses JSON parse → modify → atomic write-back instead of regex so that:
    - Malformed or future-format Preferences files are handled safely.
    - No risk of producing invalid JSON from unescaped values.
    - Atomic write (temp file + rename) prevents corrupt Preferences on crash.
    """
    import json
    import tempfile
    for slot in ["Default", "Profile 1", "Profile 2"]:
        prefs = profile_dir / slot / "Preferences"
        if not prefs.exists():
            continue
        try:
            raw = prefs.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)

            # Patch exit_type under the "profile" or "browser" key (Chromium layout)
            modified = False
            for section_key in ("profile", "browser"):
                section = data.get(section_key)
                if isinstance(section, dict):
                    if section.get("exit_type") != "Normal":
                        section["exit_type"] = "Normal"
                        modified = True
                    if section.get("crashed") is True:
                        section["crashed"] = False
                        modified = True
                    if section.get("session_crash_detected") is True:
                        section["session_crash_detected"] = False
                        modified = True

            if not modified:
                continue  # nothing to change — skip write

            # Atomic write: write to temp file in same dir, then rename
            tmp_fd, tmp_str = tempfile.mkstemp(
                dir=prefs.parent, suffix=".tmp", prefix="omnidl_prefs_"
            )
            try:
                import os as _os
                _os.write(tmp_fd, json.dumps(data, separators=(",", ":")).encode("utf-8"))
            finally:
                _os.close(tmp_fd)
            Path(tmp_str).replace(prefs)
            logger.debug("Cleared crashed flag in %s/%s/Preferences", profile_dir.name, slot)
        except (json.JSONDecodeError, OSError, KeyError):
            # Preferences corrupt or unreadable — skip silently (same as before)
            pass
        except Exception:
            pass


# ── Browser launch helpers ─────────────────────────────────────────────────────

def _find_browser_exe(browser: str) -> str:
    """Return path to Brave or Chrome executable, or raise RuntimeError.

    Supports Windows and macOS.  The .app bundle path on macOS must point to
    the actual Mach-O binary inside Contents/MacOS/ — Playwright needs a
    process it can launch directly, not the .app bundle itself.

    Note: App Store builds of Chrome/Brave do NOT support
    --remote-debugging-port.  Users must install from the vendor website.
    """

    browser = browser.lower()

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
            "Facebook Story chỉ hỗ trợ Windows và macOS.\n"
            "Linux chưa được hỗ trợ."
        )

    exe = next((p for p in candidates if Path(p).exists()), None)
    if not exe:
        raise RuntimeError(
            f"Không tìm thấy {browser.title()}.  Hãy cài đặt trình duyệt trước.\n"
            "Lưu ý: bản tải từ App Store không hỗ trợ CDP — cần bản từ website chính thức."
        )
    return exe


def _free_port() -> int:
    """Return a free local TCP port."""
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── CDP intercept via Playwright ───────────────────────────────────────────────

def _cdp_intercept(
    story_url: str,
    browser: str,
    timeout: float,
    on_progress: Optional[Callable],
) -> Optional[str]:
    """Launch browser, navigate to story_url, return first video CDN URL.

    Uses playwright.connect_over_cdp() — no Playwright browser download needed.
    The browser process is always terminated in a finally block.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright  # noqa: I001
    except ImportError as err:
        raise RuntimeError(
            "Thiếu thư viện Playwright.\n"
            "Chạy: pip install playwright"
        ) from err

    import os

    def _prog(pct: int, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, "", msg)
            except Exception:
                pass

    exe          = _find_browser_exe(browser)
    port         = _free_port()

    if sys.platform == "win32":
        local_app    = Path(os.environ.get("LOCALAPPDATA", ""))
        profile_base = local_app / (
            "BraveSoftware/Brave-Browser/User Data"
            if browser.lower() == "brave"
            else "Google/Chrome/User Data"
        )
    elif sys.platform == "darwin":
        home = Path.home()
        profile_base = home / (
            "Library/Application Support/BraveSoftware/Brave-Browser"
            if browser.lower() == "brave"
            else "Library/Application Support/Google/Chrome"
        )
    else:
        profile_base = Path()   # Linux: not supported, _find_browser_exe raises first

    if profile_base.exists():
        _clear_crashed_flag(profile_base)

    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=Translate",
        "--restore-last-session=false",
        "--no-session-crashed-bubble",
        "--hide-crash-restore-bubble",
    ]
    _prog(8, f"Đang khởi động {browser.title()}...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info("CDP: launching %s on port %d (pid=%d)", browser, port, proc.pid)

    video_url: Optional[str] = None

    try:
        with sync_playwright() as pw:
            # ── Connect via Playwright (handles WS stability internally) ──────
            # Retry up to 15s — Brave takes ~2–4s to start accepting connections
            _prog(10, "Đang kết nối CDP...")
            cdp_browser = None
            deadline = time.monotonic() + 15.0
            last_exc  = None

            while time.monotonic() < deadline:
                try:
                    cdp_browser = pw.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{port}",
                        timeout=3_000,  # ms — per-attempt, not total
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.8)

            if cdp_browser is None:
                raise RuntimeError(
                    "Không kết nối được CDP.\n\n"
                    "Đóng HOÀN TOÀN trình duyệt (kể cả System Tray) rồi thử lại.\n"
                    f"(chi tiết: {last_exc})"
                )

            logger.info("CDP: Playwright connected on port %d", port)

            # ── Create a new page in the existing browser context ─────────────
            # Use the first (default) context so we inherit the user's cookies.
            ctx  = cdp_browser.contexts[0]
            page = ctx.new_page()

            # ── Layer A: intercept outgoing requests ──────────────────────────
            def _on_request(request) -> None:
                nonlocal video_url
                if video_url:
                    return
                if _is_fb_video_url(request.url):
                    logger.info("CDP[A]: video URL caught (%d chars)", len(request.url))
                    video_url = request.url

            page.on("request", _on_request)

            # ── Layer B: MIME-type match on responses ─────────────────────────
            def _on_response(response) -> None:
                nonlocal video_url
                if video_url:
                    return
                ct = response.headers.get("content-type", "").lower()
                if (ct.startswith("video/") and "mjpeg" not in ct
                        and "fbcdn.net" in response.url):
                    logger.info("CDP[B]: video MIME=%s", ct)
                    video_url = response.url

            page.on("response", _on_response)

            # ── Pre-page JS (runs before Facebook JS on every navigation) ─────
            page.add_init_script(_PRE_PAGE_JS)

            # ── Navigate ──────────────────────────────────────────────────────
            story_url_norm = _normalize_url(story_url)
            _prog(12, "Đang mở Story trong trình duyệt...")
            logger.info("CDP: navigating to %s", story_url_norm[:100])

            try:
                page.goto(story_url_norm, wait_until="domcontentloaded",
                          timeout=min(timeout, 20) * 1_000)
            except PWTimeout:
                pass  # partial load is fine — video may already be intercepted
            except Exception as exc:
                logger.debug("page.goto warning (non-fatal): %s", exc)

            # ── Poll loop (Layer C) ────────────────────────────────────────────
            # Wait up to `timeout` seconds for a video URL from any of the
            # three layers.  Every 2s: call play() + poll injected interceptor.
            _prog(15, "Đang chờ video load...")
            loop_deadline = time.monotonic() + timeout
            last_play     = 0.0
            last_poll     = 0.0
            elapsed_pct   = 0

            while time.monotonic() < loop_deadline:
                if video_url:
                    break

                now = time.monotonic()

                # Dismiss tap-to-play overlays
                if now - last_play > 5.0:
                    try:
                        page.evaluate(_PLAY_JS)
                    except Exception:
                        pass
                    last_play = now

                # Layer C: poll injected interceptor + Performance + currentSrc
                if now - last_poll > 2.0:
                    try:
                        val = page.evaluate(_POLL_JS)
                        if val and val not in ("VIDEO_FOUND_NO_SRC", "NO_VIDEO", ""):
                            logger.info("CDP[C]: video via poll (%d chars)", len(val))
                            video_url = str(val)
                            break
                    except Exception as exc:
                        logger.debug("poll error (non-fatal): %s", exc)
                    last_poll = now

                elapsed_pct = min(45, 15 + int(
                    (timeout - (loop_deadline - now)) / timeout * 30
                ))
                _prog(elapsed_pct, "Đang chờ video load...")
                time.sleep(0.4)

        if not video_url:
            logger.warning("CDP: no video URL found within %.0fs", timeout)

        return video_url

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            if profile_base.exists():
                _clear_crashed_flag(profile_base)
                logger.debug("Cleared browser crash flag after CDP session")
        except Exception:
            pass


# ── Download ───────────────────────────────────────────────────────────────────

def _download_cdn_url(
    cdn_url: str,
    dest: Path,
    on_progress: Optional[Callable],
) -> Optional[Path]:
    """Download cdn_url to dest via streaming HTTP GET.

    Strips byte-range params first (avoids DASH init-segment-only response).
    Does a HEAD check — skips if Content-Length < 50 KB.
    """
    import requests

    def _prog(pct: int, speed: str, msg: str) -> None:
        if on_progress:
            try:
                on_progress(pct, speed, msg)
            except Exception:
                pass

    full_url = _full_video_url(cdn_url)
    headers  = {"User-Agent": _UA, "Referer": "https://www.facebook.com/"}

    # HEAD check
    try:
        head = requests.head(full_url, headers=headers, timeout=10,
                             allow_redirects=True)
        cl = int(head.headers.get("content-length", 0))
        if 0 < cl < 50_000:
            logger.warning("HEAD: size=%d < 50 KB (DASH init segment) — skip", cl)
            return None
    except Exception as exc:
        logger.debug("HEAD failed (%s) — proceeding with GET", exc)

    _prog(50, "", "Đang tải video...")
    try:
        resp = requests.get(full_url, headers=headers, stream=True, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("GET failed: %s", exc)
        return None

    total = int(resp.headers.get("content-length", 0))
    done  = 0
    start = time.monotonic()
    # Total deadline: 5 min for large files, but never hang forever
    stream_deadline = start + 300.0
    dest.parent.mkdir(parents=True, exist_ok=True)

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if time.monotonic() > stream_deadline:
                logger.warning("_download_cdn_url: stream deadline exceeded (300s)")
                dest.unlink(missing_ok=True)
                return None
            if chunk:
                f.write(chunk)
                done += len(chunk)
                elapsed = time.monotonic() - start
                speed   = done / elapsed if elapsed > 0.1 else 0
                pct     = (min(95, 50 + int(done / total * 44))
                           if total else 70)
                s_str   = (f"{speed/1048576:.1f} MB/s" if speed > 1_048_576
                           else f"{speed/1024:.0f} KB/s" if speed > 0 else "")
                _prog(pct, s_str, f"Đang tải... {done//1024} KB")

    if _validate_mp4(dest):
        return dest

    logger.warning("GET result not valid MP4 (%d bytes)",
                   dest.stat().st_size if dest.exists() else 0)
    dest.unlink(missing_ok=True)
    return None


def _ffmpeg_download(
    cdn_url: str,
    dest: Path,
    on_progress: Optional[Callable],
) -> Optional[Path]:
    """Use ffmpeg to reassemble DASH segments into a single MP4."""
    from utils.ffmpeg_locator import locate_ffmpeg

    loc = locate_ffmpeg()
    if not loc:
        logger.warning("ffmpeg not available — skipping")
        return None

    full_url = _full_video_url(cdn_url)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if on_progress:
        try:
            on_progress(60, "", "ffmpeg đang xử lý DASH stream...")
        except Exception:
            pass

    cmd = [
        loc.ffmpeg_bin,
        "-y",
        "-user_agent", _UA,
        "-referer", "https://www.facebook.com/",
        "-i", full_url,
        "-c", "copy",
        "-movflags", "+faststart",
        str(dest),
    ]
    logger.debug("ffmpeg: %s ... %s", loc.ffmpeg_bin, full_url[:60])

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timeout")
        dest.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.warning("ffmpeg error: %s", exc)
        dest.unlink(missing_ok=True)
        return None

    if result.returncode == 0 and _validate_mp4(dest):
        logger.info("ffmpeg OK: %s (%d bytes)", dest.name, dest.stat().st_size)
        return dest

    tail = (result.stderr[-300:].decode("utf-8", errors="replace")
            if result.stderr else "")
    logger.warning("ffmpeg rc=%d: %s", result.returncode, tail)
    dest.unlink(missing_ok=True)
    return None


# ── Public entry point ─────────────────────────────────────────────────────────

def download_story(
    url: str,
    config: "ConfigManager",
    browser: str = "brave",
    on_progress: Optional[Callable[[int, str, str], None]] = None,
    timeout: float = 60.0,
) -> Path:
    """Download a Facebook Story video.

    Raises RuntimeError with a Vietnamese user-facing message on failure.
    Public API is identical to the previous CDP implementation.
    """
    if not is_facebook_story_url(url):
        raise RuntimeError(
            "URL không phải Facebook Story.\n"
            "Hãy dán URL dạng facebook.com/stories/..."
        )

    output_dir = Path(config.download_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    m    = re.search(r"/stories/(\d+)", url)
    slug = m.group(1)[:16] if m else str(int(time.time()))
    dest = output_dir / f"fb_story_{slug}.mp4"
    # Avoid silently overwriting a previous download of the same story
    if dest.exists():
        dest = output_dir / f"fb_story_{slug}_{int(time.time())}.mp4"

    # CDP via Playwright: intercept video CDN URL
    cdn_url = _cdp_intercept(url, browser, timeout, on_progress)

    if not cdn_url:
        raise RuntimeError(
            "Không bắt được URL video của Story.\n\n"
            "Có thể do:\n"
            "• Story đã hết hạn (Stories tồn tại 24 giờ)\n"
            "• Bạn chưa đăng nhập Facebook trong Brave/Chrome\n"
            "• Story này chỉ có ảnh (không có video)\n\n"
            "Mở Story trong trình duyệt kiểm tra trước."
        )

    logger.info("Video URL: %s…", cdn_url[:80])
    if on_progress:
        try:
            on_progress(48, "", "Đã bắt được URL — đang tải...")
        except Exception:
            pass

    # Download: requests first (fast), ffmpeg fallback (handles DASH)
    result = _download_cdn_url(cdn_url, dest, on_progress)
    if not result:
        result = _ffmpeg_download(cdn_url, dest, on_progress)

    if not result:
        raise RuntimeError(
            "Bắt được URL video nhưng không tải được file hoàn chỉnh.\n\n"
            "Nguyên nhân thường gặp:\n"
            "• CDN URL đã hết hạn (load quá lâu)\n"
            "• Kết nối mạng không ổn định\n\n"
            "Hãy thử lại ngay sau khi mở Story trong trình duyệt."
        )

    if on_progress:
        try:
            on_progress(100, "", f"✅ Hoàn thành! {result.name}")
        except Exception:
            pass

    logger.info("Facebook Story saved: %s (%d bytes)", result, result.stat().st_size)
    return result
