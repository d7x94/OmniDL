"""
utils/helpers.py
Pure utility functions — no dependencies on other omnidl modules.
"""
from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


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
