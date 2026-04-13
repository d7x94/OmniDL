"""
utils/helpers.py
Pure utility functions — no dependencies on other omnidl modules.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import re
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── Filename sanitisation (BUG-CB) ────────────────────────────────────────

def _ascii_safe(s: str, max_len: int = 32) -> str:
    """Convert string to ASCII-safe filename component.

    1. NFKD normalise (e.g. e with accent -> e + combining accent)
    2. Encode to ASCII, ignoring non-representable chars
    3. Replace runs of non-word chars with single underscore
    4. Strip leading/trailing underscores
    5. Truncate to max_len
    6. Fallback to "unknown" if empty
    """
    if not s:
        return "unknown"
    # NFKD splits composed chars; encode-ignore drops non-ASCII
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Replace non-word chars (except dot) with underscore, collapse runs
    s = re.sub(r"[^\w.]+", "_", s)
    s = s.strip("_")[:max_len].rstrip("_")
    return s or "unknown"


def _extract_url_username(url: str) -> str:
    """Extract username from TikTok/Twitter/Instagram profile/live URLs.

    Patterns:
      tiktok.com/@username/live  ->  username
      tiktok.com/@username       ->  username
      twitter.com/username/...   ->  username
      x.com/username/...         ->  username
      instagram.com/username/live  ->  username

    Returns empty string if no match.
    """
    # TikTok: /@username at path start
    m = re.search(r"tiktok\.com/@([A-Za-z0-9_.]+)", url, re.I)
    if m:
        return m.group(1)
    # Twitter/X: /username (exclude reserved paths)
    m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)", url, re.I)
    if m:
        uname = m.group(1).lower()
        if uname not in ("i", "intent", "search", "explore", "settings", "home"):
            return m.group(1)
    # Instagram: /username/live
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/live", url, re.I)
    if m:
        return m.group(1)
    return ""


def build_download_filename(
    username: str,
    platform: str,
    date_str: str,
    video_id: str,
    ext: str,
    is_live: bool = False,
) -> str:
    """Build ASCII-safe download filename.

    Format: {username}_{Platform}_{YYYYMMDD}_{id10}[_LIVE].{ext}

    All components are sanitised to ASCII. video_id truncated to 10 chars.
    """
    u = _ascii_safe(username, 32)
    p = _ascii_safe(platform, 16)
    d = re.sub(r"[^0-9]", "", date_str)[:8] or "00000000"
    vid = _ascii_safe(video_id, 10)
    e = ext.lstrip(".").lower() or "mp4"
    suffix = "_LIVE" if is_live else ""
    return f"{u}_{p}_{d}_{vid}{suffix}.{e}"


# ── File-size formatting ──────────────────────────────────────────────────

def fmt_bytes(n: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_duration(seconds: int | float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ── URL validation ────────────────────────────────────────────────────────

_URL_RE = re.compile(
    r"^https?://"
    r"(?:[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)$"
)


def is_valid_url(url: str) -> bool:
    """Return True only for http/https URLs with a recognisable host."""
    url = url.strip()
    if not _URL_RE.match(url):
        return False
    try:
        p = urlparse(url)
        return bool(p.scheme in ("http", "https") and p.netloc)
    except Exception:
        return False


# ── Path sanitisation ─────────────────────────────────────────────────────

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitise_filename(name: str, max_len: int = 200) -> str:
    """Strip unsafe characters and path-traversal sequences from a filename."""
    name = _UNSAFE.sub("_", name)              # remove filesystem-unsafe chars
    name = re.sub(r"[.]{2,}", ".", name)       # collapse .. or ... → single dot
    name = name.strip(". ")                   # no leading/trailing dots or spaces
    return name[:max_len] or "download"


def safe_path(base: Path, untrusted: str) -> Path:
    """
    Resolve *untrusted* relative to *base* and raise ValueError if the
    result escapes the base directory (path-traversal prevention, CWE-22).

    Uses exact pathlib ancestor matching — NOT str.startswith().
    The string-prefix approach is vulnerable to a sibling-directory bypass:
    base=/home/user, candidate=/home/user_evil/f → str.startswith passes
    because the string '/home/user_evil' begins with '/home/user'.
    Path.parents performs exact directory-component matching and is immune
    to this class of attack.
    """
    base_r = base.resolve()
    candidate_r = (base_r / untrusted).resolve()
    if not (candidate_r == base_r or base_r in candidate_r.parents):
        raise ValueError(f"Path traversal attempt: {untrusted!r}")
    return candidate_r


# ── OS helpers ────────────────────────────────────────────────────────────

# ── Window handle registry (Windows focus management) ─────────────────────
#
# open_file() monitors the media player and reclaims OmniDL focus when it
# closes, using the _focus_queue → _poll_focus() pattern so the UI thread
# handles window activation (no Windows foreground-lock restrictions apply).
#
# Usage (ui/main_window.py __init__, win32 only, after _build_ui):
#     from utils.helpers import register_app_hwnd
#     register_app_hwnd(ctypes.windll.user32.GetParent(self.winfo_id()))

_app_hwnd: int = 0

_focus_queue: queue.Queue = queue.Queue()

# Track active player-watch threads by process handle to avoid accumulation.
# Multiple open_file() calls while a previous player is still open would
# otherwise spawn unbounded threads each blocking up to 4 hours.
_watch_set: set[int] = set()
_watch_set_lock: threading.Lock = threading.Lock()


def register_app_hwnd(hwnd: int) -> None:
    """Register the main window Win32 HWND for Z-order reclaim after preview."""
    global _app_hwnd
    _app_hwnd = hwnd
    logger.debug("register_app_hwnd: hwnd=%s", hwnd)


def register_main_window(window) -> None:
    """API-compatibility stub — current implementation uses _app_hwnd only."""
    pass



def reveal_in_explorer(path: Path) -> bool:
    """
    Open the parent folder and select/highlight the file.
    Returns True on success, False on failure.

    Windows: uses ctypes SHOpenFolderAndSelectItems — the proper shell API.
    This handles ALL special characters in filenames including #, [, ],
    Unicode, and long paths, unlike the explorer.exe /select, command-line
    which silently breaks on # (treats it as a URL fragment separator).

    macOS:  open -R <path>
    Linux:  xdg-open <parent>
    """
    try:
        if sys.platform == "win32":
            # Use SHOpenFolderAndSelectItems via ctypes — the correct Windows
            # API for "reveal file in Explorer".  explorer.exe /select,<path>
            # breaks silently when the path contains # (Explorer interprets it
            # as a URL fragment), [ ], or certain Unicode characters.
            # SHOpenFolderAndSelectItems has no such limitation.
            abs_path = str(path.resolve())
            parent_path = str(path.resolve().parent)
            shell32 = ctypes.windll.shell32

            # ── Critical: declare return types BEFORE any call ──────────────
            # ctypes default restype is c_int (32-bit).  On 64-bit Windows all
            # PIDL pointers are 64-bit; without c_void_p the top 32 bits are
            # silently truncated, making every pointer invalid.  That causes
            # SHOpenFolderAndSelectItems to return a non-S_OK HRESULT, which
            # makes reveal_in_explorer return False and open_folder() run as a
            # fallback — so the correct folder opens but nothing is selected.
            shell32.ILCreateFromPathW.restype = ctypes.c_void_p
            shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
            shell32.ILFindLastID.restype = ctypes.c_void_p
            shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
            shell32.ILFree.restype = None
            shell32.ILFree.argtypes = [ctypes.c_void_p]
            # SHOpenFolderAndSelectItems(pidlFolder, cidl, apidl, dwFlags)
            # apidl = PCUITEMID_CHILD_ARRAY = pointer to array of child PIDLs
            shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
            shell32.SHOpenFolderAndSelectItems.argtypes = [
                ctypes.c_void_p,   # pidlFolder  (absolute)
                ctypes.c_uint,     # cidl         (count of apidl entries)
                ctypes.c_void_p,   # apidl        (pointer to child-PIDL array)
                ctypes.c_ulong,    # dwFlags
            ]

            # Two absolute PIDLs: one for the parent folder, one for the file.
            # ILFindLastID extracts the last SHITEMID from file_pidl — that is
            # the relative (child) PIDL required by apidl.  The returned pointer
            # points INTO file_pidl memory; do NOT free it separately.
            parent_pidl = shell32.ILCreateFromPathW(parent_path)
            file_pidl = shell32.ILCreateFromPathW(abs_path)
            if not parent_pidl or not file_pidl:
                shell32.ILFree(parent_pidl)
                shell32.ILFree(file_pidl)
                return False
            try:
                rel_pidl = shell32.ILFindLastID(file_pidl)
                apidl = (ctypes.c_void_p * 1)(rel_pidl)
                hr = shell32.SHOpenFolderAndSelectItems(
                    parent_pidl, 1, apidl, 0,
                )
                return hr == 0  # S_OK
            finally:
                shell32.ILFree(parent_pidl)
                shell32.ILFree(file_pidl)
        elif sys.platform == "darwin":
            _p = str(path.resolve())
            subprocess.Popen(["open", "-R", _p], close_fds=True)
        else:
            _p = str(path.resolve().parent)
            subprocess.Popen(["xdg-open", _p], close_fds=True)
        return True
    except Exception as exc:
        logger.debug("reveal_in_explorer failed: %s", exc)
        return False


def open_folder(path: Path) -> None:
    """Open a folder in the OS file manager.

    """
    try:
        if sys.platform == "win32":
            _p = str(path.resolve())
            subprocess.Popen(["explorer", _p], close_fds=True)
        elif sys.platform == "darwin":
            _p = str(path.resolve())
            subprocess.Popen(["open", _p], close_fds=True)
        else:
            _p = str(path.resolve())
            subprocess.Popen(["xdg-open", _p], close_fds=True)
    except Exception as exc:
        logger.debug("open_folder failed for %s: %s", path, exc)


def open_file(path: Path) -> None:
    """Open a file with the OS default application.

    Windows: ShellExecuteExW with SEE_MASK_NOCLOSEPROCESS gets the player
             process handle. A daemon thread blocks on WaitForSingleObject
             until the player process exits (race-free, exact timing), then
             signals _focus_queue with a simple sentinel.

             _poll_focus() on the UI thread receives the sentinel and calls:
               attributes("-topmost", True) — OmniDL above ALL non-topmost
               lift()                       — Tkinter Z-order raise
               BringWindowToTop(hwnd)       — Win32 Z-order raise
               after(600ms): topmost=False  — restore normal Z-order

             Why this works:
               The previous topmost approach failed only because of TIMING:
               topmost was set before startfile (player not yet open, no
               effect). Now WaitForSingleObject gives us the exact moment
               the player exits — we set topmost AFTER the player closes,
               which is the correct and only useful moment.

               BringWindowToTop + topmost require NO foreground permission.
               They affect Z-order only (visual stacking), not focus.
               The user complaint is visual ("bị che" = visually covered),
               not about keyboard focus. This fixes the actual problem.

    Fallback: ShellExecuteExW fails → os.startfile() (no reclaim, same
              as original behaviour).
    macOS: open <path>    Linux: xdg-open <path>
    Silently logs on failure — never raises.
    """
    try:
        if sys.platform == "win32":
            import ctypes as _ctypes
            import ctypes.wintypes as _wt

            _p    = str(path.resolve())
            _hwnd = _app_hwnd

            class _SEI(_ctypes.Structure):
                _fields_ = [
                    ("cbSize",   _wt.DWORD),    ("fMask",    _wt.ULONG),
                    ("hwnd",     _wt.HWND),     ("lpVerb",   _wt.LPCWSTR),
                    ("lpFile",   _wt.LPCWSTR),  ("lpParams", _wt.LPCWSTR),
                    ("lpDir",    _wt.LPCWSTR),  ("nShow",    _ctypes.c_int),
                    ("hInst",    _wt.HINSTANCE),("lpIDList", _ctypes.c_void_p),
                    ("lpClass",  _wt.LPCWSTR),  ("hkey",     _wt.HKEY),
                    ("dwHotKey", _wt.DWORD),    ("hMon",     _wt.HANDLE),
                    ("hProcess", _wt.HANDLE),
                ]

            sei        = _SEI()
            sei.cbSize = _ctypes.sizeof(_SEI)
            sei.fMask  = 0x00000040    # SEE_MASK_NOCLOSEPROCESS
            sei.lpVerb = "open"
            sei.lpFile = _p
            sei.nShow  = 1             # SW_SHOWNORMAL

            _sh = _ctypes.windll.shell32
            _sh.ShellExecuteExW.argtypes = [_ctypes.POINTER(_SEI)]
            _sh.ShellExecuteExW.restype  = _wt.BOOL
            _ok    = _sh.ShellExecuteExW(_ctypes.byref(sei))
            _hproc = sei.hProcess if (_ok and sei.hProcess) else None

            if not _ok:
                # ShellExecuteExW failed entirely (e.g. file type unregistered).
                # Fall back to os.startfile — no focus reclaim in this path.
                import os as _os
                _os.startfile(_p)
            elif _hproc:
                # ShellExecuteExW succeeded and returned a process handle.
                # Spawn a daemon watcher that signals _focus_queue the instant
                # the player process exits — exact timing, no polling.
                # Guard against accumulation: if we are already watching this
                # exact process handle (e.g. two rapid open_file() calls for
                # the same player instance), skip the duplicate thread.
                with _watch_set_lock:
                    if _hproc in _watch_set:
                        pass  # already watching — no new thread needed
                    else:
                        _watch_set.add(_hproc)

                        def _wait(hp=_hproc) -> None:
                            k32 = _ctypes.windll.kernel32
                            k32.WaitForSingleObject(hp, 4 * 3600 * 1000)
                            k32.CloseHandle(hp)
                            with _watch_set_lock:
                                _watch_set.discard(hp)
                            _focus_queue.put_nowait(True)

                        threading.Thread(
                            target=_wait, daemon=True,
                            name="omnidl-player-watch",
                        ).start()
            # else: _ok=True but hProcess=NULL — player was already running
            # and the shell reused the existing process.  The file has been
            # sent to the running player; no focus reclaim needed.

        elif sys.platform == "darwin":
            _p = str(path.resolve())
            subprocess.Popen(["open", _p], close_fds=True)
        else:
            _p = str(path.resolve())
            subprocess.Popen(["xdg-open", _p], close_fds=True)
    except Exception as exc:
        logger.debug("open_file failed for %s: %s", path, exc)

