"""
infrastructure/downloader/cookie_extractor.py
Built-in browser cookie extractor — no third-party extension required.

Two extraction methods:

1. yt-dlp browser extraction (`extract_browser_cookies`)
   Reads cookies via yt-dlp's `cookiesfrombrowser` option.
   Works for Firefox, Edge, Opera, Chromium-based browsers on older versions.
   ⚠ Fails on Brave/Chrome 127+ due to App-Bound Encryption (DPAPI bypass).

2. Chrome DevTools Protocol (`extract_via_cdp`)
   Launches the browser with `--remote-debugging-port` and calls
   `Network.getAllCookies` via WebSocket.  The browser decrypts its own
   cookies before handing them over — App-Bound Encryption is irrelevant.
   Works on ALL Brave/Chrome versions.  Uses Python stdlib only (no packages).

Security model
──────────────
• Cookies are read ONLY from the local machine — no third-party service.
• Output file always goes inside config_path.parent/cookies/ (CWE-22 safe).
• Platform-filtered files contain ONLY the relevant platform's domains.
• CDP method: browser process we launch is closed after extraction completes.

Thread safety
─────────────
All public functions are pure (no shared state) and safe to call from a
background thread.  UI updates must go through _ui_queue.put() in the caller.
"""

from __future__ import annotations

import http.cookiejar
import logging
from pathlib import Path

from utils.i18n import t

logger = logging.getLogger(__name__)

# Domains checked when filtering a full browser cookie jar to a
# platform-specific subset.  Filtering uses exact hostname match:
#   cookie.domain.lstrip(".") == d  OR  ends with "." + d
#
# Note: threads.net auth is backed by Instagram sessions — include
# instagram.com so Threads cookies are captured correctly.
_PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    # YouTube: needs both youtube.com (session) and google.com (account/age-verify)
    # Without google.com auth cookies, age-restricted content is blocked.
    "youtube": ("youtube.com", "youtu.be", "google.com"),
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com"),
    "twitter": ("twitter.com", "x.com"),
    "threads": ("threads.net", "threads.com", "instagram.com"),  # threads.com = new domain
    "kuaishou": ("kuaishou.com", "kwai.com"),
}


def extract_browser_cookies(
    browser: str,
    output_path: Path,
    platform_key: str | None = None,
    profile: str | None = None,
) -> tuple[int, str | None]:
    """Extract cookies from *browser* and save to *output_path* (Netscape format).

    Parameters
    ──────────
    browser
        Browser name accepted by yt-dlp's cookiesfrombrowser option:
        "chrome", "firefox", "edge", "safari", "brave", "opera", "chromium".
    output_path
        Destination file path.  Parent directory is created if missing.
        Should already be inside the OmniDL safe directory so that
        _validate_cookie_path_raw() accepts it.
    platform_key
        Optional platform filter key (e.g. "instagram", "tiktok").
        When given, only cookies whose domain matches that platform's
        domains (defined in _PLATFORM_DOMAINS) are written to *output_path*.
        When None, all browser cookies are written (global cookie file).
    profile
        Optional browser profile directory name ("Default", "Profile 1", ...).
        Chromium keeps one cookie store per profile, so this is what lets a
        user register several TikTok logins without logging out in between.
        When None, yt-dlp picks the browser's default profile.

    Returns
    ───────
    (cookie_count, error_message)
        On success: cookie_count > 0, error_message is None.
        On failure: cookie_count == 0, error_message is a user-readable string.
    """
    import yt_dlp

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a sibling temp file first — prevents corrupting an existing
    # good cookie file if extraction fails or the browser is locked mid-read.
    tmp_path = output_path.parent / f"_tmp_{output_path.name}"
    tmp_path.unlink(missing_ok=True)

    try:
        # yt-dlp behaviour when BOTH cookiesfrombrowser AND cookiefile are set:
        #   __init__ → _setup_opener() → loads browser cookies into self.cookiejar
        #   __exit__ → close() → self.cookiejar.save(cookiefile)
        # Result: tmp_path receives a Netscape-format file with browser cookies.
        with yt_dlp.YoutubeDL(
            {
                "cookiesfrombrowser": (browser, profile or None, None, None),
                "cookiefile": str(tmp_path),
                "quiet": True,
                "no_warnings": True,
            }
        ) as _ydl:
            pass  # cookies loaded in __init__, saved in __exit__

        if not tmp_path.exists() or tmp_path.stat().st_size < 20:
            tmp_path.unlink(missing_ok=True)
            return 0, t("cookie.err.read_failed")

        # ── Platform-filtered path ─────────────────────────────────────────
        if platform_key:
            domains = _PLATFORM_DOMAINS.get(platform_key, ())
            src_jar = http.cookiejar.MozillaCookieJar()
            src_jar.load(str(tmp_path), ignore_discard=True, ignore_expires=True)

            out_jar = http.cookiejar.MozillaCookieJar(str(output_path))
            count = 0
            for cookie in src_jar:
                raw_domain = cookie.domain.lstrip(".")
                if any(raw_domain == d or raw_domain.endswith("." + d) for d in domains):
                    out_jar.set_cookie(cookie)
                    count += 1

            tmp_path.unlink(missing_ok=True)

            if count == 0:
                return 0, t("cookie.err.platform_empty", platform=platform_key)

            out_jar.save(ignore_discard=True, ignore_expires=True)
            # Encrypt at rest using DPAPI
            from infrastructure.downloader.cookie_storage import encrypt_cookie_file

            output_path = encrypt_cookie_file(output_path)
            logger.info(
                "Extracted %d %s cookies from %s → %s",
                count,
                platform_key,
                browser,
                output_path,
            )
            return count, None

        # ── Global (unfiltered) path ───────────────────────────────────────
        src_jar = http.cookiejar.MozillaCookieJar()
        src_jar.load(str(tmp_path), ignore_discard=True, ignore_expires=True)
        count = sum(1 for _ in src_jar)

        if count == 0:
            tmp_path.unlink(missing_ok=True)
            return 0, t("cookie.err.browser_empty")

        tmp_path.replace(output_path)
        # Encrypt at rest using DPAPI so plaintext only lives in memory
        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        output_path = encrypt_cookie_file(output_path)
        logger.info(
            "Extracted %d cookies (global) from %s → %s",
            count,
            browser,
            output_path,
        )
        return count, None

    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        msg = str(exc)
        logger.warning("Browser cookie extraction failed (%s): %s", browser, msg)
        return 0, _friendly_extract_error(msg)


def _check_keyring_installed() -> bool:
    """Return True if the `keyring` package is importable (needed for Brave/Chrome 127+)."""
    try:
        import keyring  # noqa: F401

        return True
    except ImportError:
        return False


def _friendly_extract_error(msg: str) -> str:
    """Translate yt-dlp browser-extraction errors to Vietnamese user messages."""
    msg_l = msg.lower()

    # yt-dlp error: "Failed to decrypt with DPAPI" (issue #10927)
    # Brave/Chrome 127+ use App-Bound Encryption (ABE) — the cookie key is
    # cryptographically bound to the browser EXE path via COM IElevator service.
    # No external process can decrypt it — this is a fundamental Windows/Chrome
    # security boundary. keyring does NOT fix this; it addresses a different layer.
    # Only solutions: (1) Firefox (no ABE), (2) manual cookie export from Brave.
    if "failed to decrypt" in msg_l and "dpapi" in msg_l:
        return t("cookie.err.app_bound_encryption")

    # yt-dlp error: "Could not copy Chrome cookie database" (issue #7271)
    # Happens when Brave/Chrome/Chromium-based browser is open and has locked the SQLite file.
    # yt-dlp always says "Chrome" even when reading Brave — do NOT mislead the user.
    if "could not copy" in msg_l and "cookie database" in msg_l:
        return t("cookie.err.brave_locked")

    # yt-dlp error: "could not find <browser> cookies database in <path>"
    # This almost always means the wrong browser is selected in the dropdown.
    # Extract the browser name from the message for a more specific hint.
    if "could not find" in msg_l and "cookies database" in msg_l:
        # Try to extract which browser yt-dlp was looking for
        _detected = None
        for _b in ("chrome", "firefox", "brave", "edge", "opera", "safari", "chromium", "vivaldi"):
            if _b in msg_l:
                _detected = _b
                break
        if _detected:
            return t("cookie.err.db_missing_detected", browser=_detected)
        return t("cookie.err.db_missing")

    if any(k in msg_l for k in ("locked", "database is locked", "unable to open")):
        return t("cookie.err.db_locked")
    if any(k in msg_l for k in ("decrypt", "dpapi", "keyring", "keychain")):
        return t("cookie.err.decrypt_failed")
    if any(k in msg_l for k in ("not found", "no profile", "could not find")):
        return t("cookie.err.profile_missing")
    if "permission" in msg_l or "access denied" in msg_l:
        return t("cookie.err.permission_denied")
    if "unsupported" in msg_l or "not supported" in msg_l:
        return t("cookie.err.browser_unsupported")
    # Generic fallback — cap at 150 chars so it fits in the status label
    return msg[:150]


# ── Chrome DevTools Protocol (CDP) extraction ─────────────────────────────────
#
# Works with ALL Brave/Chrome versions including 127+ (App-Bound Encryption).
# Uses Python stdlib only — no external packages needed.
#
# Flow:
#   1. Locate browser EXE (Brave or Chrome)
#   2. Launch it with --remote-debugging-port=<port> --no-first-run
#   3. Poll http://localhost:<port>/json/version until CDP is ready (≤20 s)
#   4. Open WebSocket, send Network.getAllCookies
#   5. Receive cookies (already decrypted by the browser itself)
#   6. Format Netscape + optional platform filter + save to output_path
#   7. Terminate the launched browser process
# ──────────────────────────────────────────────────────────────────────────────

_BRAVE_EXE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%PROGRAMFILES(X86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
]
_CHROME_EXE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
    r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
]
_EDGE_EXE_CANDIDATES: list[str] = [
    r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
    r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
]
_CHROMIUM_EXE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
    r"%PROGRAMFILES%\Chromium\Application\chrome.exe",
]

_EXE_CANDIDATES: dict[str, list[str]] = {
    "brave": _BRAVE_EXE_CANDIDATES,
    "chrome": _CHROME_EXE_CANDIDATES,
    "edge": _EDGE_EXE_CANDIDATES,
    "chromium": _CHROMIUM_EXE_CANDIDATES,
}

# Process image name used by _is_browser_running(), per family.
_PROCESS_NAMES: dict[str, str] = {
    "brave": "brave.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "chromium": "chrome.exe",
}

# macOS process names as reported by `pgrep -x` (the Mach-O binary inside
# the .app bundle, which is not the same string as the Windows exe name).
_MAC_PROCESS_NAMES: dict[str, str] = {
    "brave": "Brave Browser",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "chromium": "Chromium",
}

_CDP_DEFAULT_PORT = 9223  # avoid clashing with user's own debugging session on 9222

# Known user-data directories per Chromium family on Windows.
_BRAVE_PROFILE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data",
]
_CHROME_PROFILE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\Google\Chrome\User Data",
]
_EDGE_PROFILE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
]
_CHROMIUM_PROFILE_CANDIDATES: list[str] = [
    r"%LOCALAPPDATA%\Chromium\User Data",
]

_PROFILE_CANDIDATES: dict[str, list[str]] = {
    "brave": _BRAVE_PROFILE_CANDIDATES,
    "chrome": _CHROME_PROFILE_CANDIDATES,
    "edge": _EDGE_PROFILE_CANDIDATES,
    "chromium": _CHROMIUM_PROFILE_CANDIDATES,
}


def _browser_family(browser: str) -> str:
    """Map a browser name to its Chromium family key.

    Edge and Chromium used to fall through to the Chrome branch, which made
    OmniDL launch Chrome (and read Chrome's cookies) whenever the user picked
    Edge in the browser selector.
    """
    b = (browser or "").lower()
    for family in ("brave", "edge", "chromium", "chrome"):
        if family in b:
            return family
    return "chrome"


def _find_browser_profile(browser: str) -> "Path | None":
    """Return the user-data-dir for *browser*, or None if not found."""
    import os

    candidates = _PROFILE_CANDIDATES.get(_browser_family(browser), _CHROME_PROFILE_CANDIDATES)
    for template in candidates:
        p = Path(os.path.expandvars(template))
        if p.is_dir():
            return p
    return None


def list_browser_profiles(browser: str) -> "list[tuple[str, str]]":
    """Return [(profile_dir, display_name), ...] for *browser*, best-effort.

    Chromium stores one cookie jar per profile directory ("Default",
    "Profile 1", ...).  Reading `Local State` gives the human names the user
    actually sees in the browser ("Work", "hoa@gmail.com"), so the account
    picker can show something recognisable instead of "Profile 3".

    Returns [] when the browser is not installed / the layout is unexpected;
    callers treat that as "only the default profile is available".
    """
    import json

    user_data = _find_browser_profile(browser)
    if user_data is None:
        return []

    names: dict[str, str] = {}
    try:
        raw = json.loads((user_data / "Local State").read_text(encoding="utf-8", errors="replace"))
        info = raw.get("profile", {}).get("info_cache", {})
        if isinstance(info, dict):
            for dir_name, meta in info.items():
                if not isinstance(meta, dict):
                    continue
                label = str(meta.get("name") or "").strip()
                email = str(meta.get("user_name") or "").strip()
                if label and email and email.lower() not in label.lower():
                    label = f"{label} ({email})"
                names[dir_name] = label or email or dir_name
    except (OSError, ValueError) as exc:
        logger.debug("list_browser_profiles: cannot read Local State for %s — %s", browser, exc)

    found: list[tuple[str, str]] = []
    for child in sorted(user_data.iterdir()) if user_data.is_dir() else []:
        if not child.is_dir():
            continue
        if child.name != "Default" and not child.name.startswith("Profile "):
            continue
        # A profile without a cookie store has never been used — hide it.
        if not (child / "Cookies").is_file() and not (child / "Network" / "Cookies").is_file():
            continue
        found.append((child.name, names.get(child.name, child.name)))

    # "Default" first, then Profile 1, Profile 2, ... in natural order.
    def _order(item: "tuple[str, str]") -> "tuple[int, int, str]":
        name = item[0]
        if name == "Default":
            return (0, 0, name)
        try:
            return (1, int(name.rsplit(" ", 1)[1]), name)
        except (IndexError, ValueError):
            return (2, 0, name)

    return sorted(found, key=_order)


def _is_browser_running(browser: str) -> bool:
    """Return True if the browser is currently running.

    Windows: `tasklist /FI "IMAGENAME eq brave.exe"`.
    macOS:   `pgrep -x "Brave Browser"` — needed by the Facebook Story engine,
             which must refuse to launch a second instance (Chromium forwards
             the command line to the running one and drops
             --remote-debugging-port, so CDP would never come up).
    Returns False on any other platform or if detection fails — safe default.
    """
    import subprocess
    import sys

    if sys.platform == "darwin":
        mac_name = _MAC_PROCESS_NAMES.get(_browser_family(browser))
        if not mac_name:
            return False
        try:
            return (
                subprocess.run(
                    ["pgrep", "-x", mac_name],
                    capture_output=True,
                    timeout=5,
                ).returncode
                == 0
            )
        except Exception:
            return False

    if sys.platform != "win32":
        return False
    exe_name = _PROCESS_NAMES.get(_browser_family(browser), f"{browser.lower()}.exe")
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH", "/FO", "CSV"],
            capture_output=True,
            timeout=5,
        )
        # Parse CSV output: each line is "name","pid","session",...
        # Exact first-field match avoids false positives from similarly named
        # processes (e.g. "unbrave.exe" would not match "brave.exe").
        output = result.stdout.decode(errors="replace")
        return any(
            line.split(",")[0].strip().strip('"').lower() == exe_name.lower()
            for line in output.splitlines()
            if line.strip()
        )
    except Exception:
        return False


def _find_browser_exe(browser: str) -> "Path | None":
    import os

    candidates = _EXE_CANDIDATES.get(_browser_family(browser), _CHROME_EXE_CANDIDATES)
    for template in candidates:
        p = Path(os.path.expandvars(template))
        if p.is_file():
            return p
    return None


def _cdp_wait_ready(port: int, timeout: float = 20.0) -> bool:
    import http.client
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection("localhost", port, timeout=1)
            conn.request("GET", "/json/version")
            if conn.getresponse().status == 200:
                return True
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        time.sleep(0.4)
    return False


def _cdp_get_page_ws_url(port: int) -> str:
    """Return the WebSocket URL of a page target.

    Network.getAllCookies works on a *page* target, not the browser endpoint.
    Fetches /json/list, returns the first page-type target's wsDebuggerUrl.
    If none exists, asks Chrome to create one via /json/new.
    """
    import http.client
    import json
    import time

    for _ in range(5):
        conn = http.client.HTTPConnection("localhost", port, timeout=5)
        try:
            conn.request("GET", "/json/list")
            targets = json.loads(conn.getresponse().read())
        finally:
            try:
                conn.close()
            except Exception:
                pass

        for tgt in targets:
            if tgt.get("type") == "page" and tgt.get("webSocketDebuggerUrl"):
                return tgt["webSocketDebuggerUrl"]

        # No page yet — create one
        try:
            conn2 = http.client.HTTPConnection("localhost", port, timeout=5)
            conn2.request("PUT", "/json/new?about:blank")
            conn2.getresponse().read()
        except Exception:
            pass
        finally:
            try:
                conn2.close()
            except Exception:
                pass
        time.sleep(0.8)

    raise RuntimeError("CDP: no page target available — browser may still be initialising")


def _cdp_ws_connect(port: int, path: str):
    import base64
    import os
    import socket

    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\nHost: localhost:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode()
    sock = socket.create_connection(("localhost", port), timeout=10)
    sock.sendall(handshake)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("WebSocket handshake: connection closed early")
        buf += chunk
    if b"101" not in buf.split(b"\r\n")[0]:
        raise RuntimeError(f"WebSocket upgrade rejected: {buf[:120]!r}")
    return sock


def _cdp_ws_send(sock, message: str) -> None:
    import os
    import struct

    payload = message.encode()
    n = len(payload)
    mask = os.urandom(4)
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(n))
    header = bytearray([0x81])
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    header += mask
    sock.sendall(bytes(header) + masked)


def _cdp_ws_recv(sock) -> str:
    import struct

    def _exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("WebSocket: connection closed mid-frame")
            buf += chunk
        return buf

    h = _exact(2)
    n = h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", _exact(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _exact(8))[0]
    return _exact(n).decode(errors="replace")


def _cdp_get_all_cookies(sock) -> "list[dict]":
    """Get all cookies from a page-level CDP target.

    Must call Network.enable first — without it Chrome returns an empty list
    because the Network domain is not initialised for the page target.

    sock.settimeout(10) is set here explicitly: _cdp_ws_connect() sets
    timeout=10 on create_connection() which applies to the initial recv()
    calls, but CPython may reset the timeout internally after handshake on
    some platforms. Explicitly re-setting it ensures each recv() in the
    30-frame loop is bounded at 10 s, giving a worst-case of ~5 minutes
    instead of indefinite blocking when a browser hangs after receiving the
    command.
    """
    import json
    import socket as _socket

    # Explicit per-recv timeout — guards against a browser that accepts the
    # WebSocket connection but never responds to Network.getAllCookies.
    try:
        sock.settimeout(10)
    except _socket.error:
        pass  # non-fatal — existing timeout from create_connection still applies

    # Step 1: enable the Network domain for this target
    _cdp_ws_send(sock, json.dumps({"id": 1, "method": "Network.enable", "params": {}}))
    for _ in range(30):
        try:
            msg = json.loads(_cdp_ws_recv(sock))
        except Exception:
            continue
        if msg.get("id") == 1:
            break  # Network domain enabled

    # Step 2: request all cookies
    _cdp_ws_send(sock, json.dumps({"id": 2, "method": "Network.getAllCookies", "params": {}}))
    for _ in range(30):
        try:
            msg = json.loads(_cdp_ws_recv(sock))
        except Exception:
            continue
        if msg.get("id") == 2:
            return msg.get("result", {}).get("cookies", [])
    raise RuntimeError("CDP: no response to Network.getAllCookies after 30 frames")


def _cdp_cookies_to_netscape(cookies: "list[dict]") -> str:
    lines = ["# Netscape HTTP Cookie File", "# Exported by OmniDL via CDP", ""]
    for c in cookies:
        domain = c.get("domain", "")
        subdomain_flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expires = max(0, int(c.get("expires", 0)))
        # Strip tab/CR/LF from name and value — these are valid in Set-Cookie
        # headers but break Netscape format which uses \t as field separator
        # and \n as line terminator.
        name = c.get("name", "").replace("\t", " ").replace("\r", "").replace("\n", "")
        value = c.get("value", "").replace("\t", " ").replace("\r", "").replace("\n", "")
        lines.append(f"{domain}\t{subdomain_flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def _find_free_port() -> int:
    """Return an available unprivileged port chosen by the OS (random each call)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def extract_via_cdp(
    output_path: "Path",
    platform_key: "str | None" = None,
    browser: str = "brave",
    port: int | None = None,
    profile: "str | None" = None,
) -> "tuple[int, str | None]":
    """Extract cookies via Chrome DevTools Protocol — bypasses App-Bound Encryption.

    Launches browser with --remote-debugging-port, calls Network.getAllCookies
    (cookies returned already decrypted by the browser), saves Netscape format.
    Works on ALL Brave/Chrome versions including 127+.

    *profile* selects a Chromium profile directory ("Default", "Profile 1", ...)
    so several logins of the same site can be extracted one after another.

    Returns (cookie_count, error_message | None).
    """
    import http.client
    import subprocess

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use random port each call — attacker cannot predict which port to probe
    if port is None:
        port = _find_free_port()

    exe = _find_browser_exe(browser)
    if exe is None:
        # The EXE/profile tables only cover Windows layouts, so on macOS/Linux
        # this branch is always taken — say so instead of claiming the browser
        # is not installed.
        import sys as _sys

        if _sys.platform != "win32":
            return 0, t("cookie.err.cdp_windows_only")
        return 0, t("cookie.err.browser_not_installed", browser=browser.title())

    # Check if port is already responding (e.g. previous OmniDL run left it open)
    port_busy = False
    try:
        c = http.client.HTTPConnection("localhost", port, timeout=1)
        c.request("GET", "/json/version")
        port_busy = c.getresponse().status == 200
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass

    proc = None
    try:
        if not port_busy:
            # ── Check if browser is already running ───────────────────────
            # If running → must close it first so we can launch with the real
            # profile without file-lock conflicts.
            # If not running → launch with the real profile dir so CDP reads
            # the actual cookies (not an empty fresh profile).
            if _is_browser_running(browser):
                return 0, t("cookie.err.browser_running", browser=browser.title())

            # ── Find real profile directory ───────────────────────────────
            profile_dir = _find_browser_profile(browser)
            if profile_dir is None:
                return 0, t("cookie.err.profile_dir_missing", browser=browser.title())

            cmd = [
                str(exe),
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",  # bind to localhost only
                f"--user-data-dir={profile_dir}",  # real profile with actual cookies
                "--no-first-run",
                "--no-default-browser-check",
                # Chromium keeps a separate cookie jar per profile directory.
                # Without this flag every extraction returns the *default*
                # profile's cookies, so a second TikTok account was impossible
                # to register without logging out in the browser first.
                f"--profile-directory={profile or 'Default'}",
                "--disable-extensions-except=",
                "--disable-background-networking",
                "--disable-sync",
                "--window-size=1,1",
                "--window-position=-32000,-32000",
                "about:blank",
            ]
            logger.info(
                "Launching %s (profile=%s) for CDP extraction on port %d",
                browser,
                profile or "Default",
                port,
            )
            # CREATE_NO_WINDOW suppresses the console window that Brave/Chrome
            # would briefly create and show to the user.
            # DETACHED_PROCESS (0x00000008) was previously used but is wrong:
            # it detaches from the console without preventing a new one from
            # appearing, causing a CMD flash during each CDP extraction.
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        if not _cdp_wait_ready(port, timeout=20.0):
            return 0, t("cookie.err.cdp_timeout", browser=browser.title())

        ws_url = _cdp_get_page_ws_url(port)
        ws_path = ws_url.split(f"localhost:{port}", 1)[-1]

        sock = _cdp_ws_connect(port, ws_path)
        try:
            raw_cookies: list[dict] = _cdp_get_all_cookies(sock)
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if not raw_cookies:
            return 0, t("cookie.err.cdp_zero_cookies")

        # Platform filter
        if platform_key:
            domains = _PLATFORM_DOMAINS.get(platform_key, ())
            raw_cookies = [
                c
                for c in raw_cookies
                if any(
                    c.get("domain", "").lstrip(".") == d or c.get("domain", "").lstrip(".").endswith("." + d)
                    for d in domains
                )
            ]
            if not raw_cookies:
                return 0, t(
                    "cookie.err.platform_empty_browser",
                    platform=platform_key.title(),
                    browser=browser.title(),
                )

        output_path.write_text(_cdp_cookies_to_netscape(raw_cookies), encoding="utf-8")
        # Encrypt at rest using DPAPI
        from infrastructure.downloader.cookie_storage import encrypt_cookie_file

        output_path = encrypt_cookie_file(output_path)
        logger.info(
            "CDP: extracted %d cookies from %s (platform=%s) → %s",
            len(raw_cookies),
            browser,
            platform_key or "all",
            output_path,
        )
        return len(raw_cookies), None

    except Exception as exc:
        msg = str(exc)
        logger.warning("CDP extraction failed (%s): %s", browser, msg)
        return 0, _friendly_cdp_error(msg)

    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def _friendly_cdp_error(msg: str) -> str:
    msg_l = msg.lower()
    if "connection refused" in msg_l or "timed out" in msg_l or "timeout" in msg_l:
        return t("cookie.err.cdp_no_connection")
    if "websocket" in msg_l or "handshake" in msg_l or "upgrade" in msg_l:
        return t("cookie.err.cdp_websocket")
    if "0 cookies" in msg_l or "no response" in msg_l:
        return t("cookie.err.cdp_no_cookies")
    return msg[:150]
