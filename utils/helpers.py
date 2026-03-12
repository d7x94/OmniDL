"""
utils/helpers.py
Pure utility functions — no dependencies on other omnidl modules.
"""
from __future__ import annotations

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
    Returns True if the OS call was issued, False on failure.

    FIX SEC-2 (HIGH): The previous Windows implementation split
    '/select,' and the path into two separate list elements:
        ['explorer', '/select,', str(path)]
    Explorer does NOT accept them as separate argv entries — it silently
    opens the desktop or does nothing.  The /select, prefix and the path
    must be concatenated into a single argument:
        ['explorer', '/select,C:\\path\\to\\file.mp4']
    This is safe (no shell=True) and works correctly on all tested
    Windows versions.
    """
    try:
        if sys.platform == "win32":
            # Single argument: '/select,<absolute_path>'
            # No shell=True needed — explorer.exe reads its own argv directly.
            # Concatenate /select, and the path into a single argv element.
            # list-form Popen (no shell=True) is safe (no B602); Windows
            # CreateProcess joins the list via list2cmdline which quotes
            # the argument correctly when the path contains spaces.
            subprocess.Popen(
                ["explorer", f"/select,{str(path.resolve())}"],
                close_fds=True,
            )
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
