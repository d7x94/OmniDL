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
            shell32 = ctypes.windll.shell32

            # ILCreateFromPathW converts the path string to a PIDL.
            # This is immune to # / [ ] / Unicode / long-path issues that
            # plague the explorer.exe /select, command-line approach.
            parent_path = str(path.resolve().parent)
            # Two PIDLs are needed:
            #   parent_pidl — the folder to open (absolute)
            #   file_pidl   — used to extract the relative (child) PIDL
            # ILFindLastID returns a pointer INTO file_pidl (no new allocation).
            # SHOpenFolderAndSelectItems(folder, cidl=1, [relative_pidl], 0)
            # is the correct idiom: cidl=1 + relative child PIDL selects the
            # file inside the opened folder.
            # Using cidl=0 opens the folder without selecting any file.
            # Using an absolute PIDL as an apidl entry is wrong — apidl must
            # contain relative (child) PIDLs only (MSDN requirement).
            parent_pidl = shell32.ILCreateFromPathW(parent_path)
            file_pidl = shell32.ILCreateFromPathW(abs_path)
            if not parent_pidl or not file_pidl:
                shell32.ILFree(parent_pidl)
                shell32.ILFree(file_pidl)
                return False
            try:
                # ILFindLastID: extract the last SHITEMID from file_pidl.
                # This gives us the relative (child) PIDL needed by apidl.
                # The returned pointer is INTO file_pidl — do NOT free it.
                rel_pidl = shell32.ILFindLastID(file_pidl)
                ItemArray = ctypes.c_void_p * 1
                items = ItemArray(rel_pidl)
                hr = shell32.SHOpenFolderAndSelectItems(
                    parent_pidl, 1, items, 0,
                )
                return hr == 0  # S_OK
            finally:
                shell32.ILFree(parent_pidl)
                shell32.ILFree(file_pidl)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path.resolve())], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(path.resolve().parent)], close_fds=True)
        return True
    except Exception as exc:
        logger.debug("reveal_in_explorer failed: %s", exc)
        return False


def open_folder(path: Path) -> None:
    """Open a folder in the OS file manager.

    """
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["explorer", str(path.resolve())],
                close_fds=True,
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path.resolve())], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(path.resolve())], close_fds=True)
    except Exception as exc:
        logger.debug("open_folder failed for %s: %s", path, exc)
