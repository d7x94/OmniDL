"""
utils/ffmpeg_locator.py
Locate the bundled FFmpeg binary directory at runtime.

This module resolves the correct FFmpeg location whether OmniDL is:
  • Running as a PyInstaller frozen build  → sys._MEIPASS/ffmpeg/
  • Running from source / CI staging area  → resources/ffmpeg/
  • Neither (developer with system FFmpeg)  → return None, let yt-dlp
                                              search the system PATH

The returned value is a *directory* path (not the binary itself) because
yt-dlp's ``ffmpeg_location`` option accepts a directory containing both
``ffmpeg`` and ``ffprobe``.

Design notes
────────────
• sys._MEIPASS is the PyInstaller extraction directory.  All files
  declared in the spec's ``datas`` list are unpacked there at start-up.
  We place ffmpeg under a ``ffmpeg/`` sub-folder to avoid polluting the
  root of _MEIPASS with binaries.

• The ``resources/ffmpeg/`` path is used exclusively in CI (populated by
  the workflow before the PyInstaller build) and optionally by developers
  who want to test the bundled-FFmpeg path locally.  It is intentionally
  NOT committed to the repository — see .gitignore.

• Returning None rather than raising allows the application to start even
  without a bundled FFmpeg; yt-dlp will use whatever ffmpeg is on PATH.
  A warning is emitted so developers are aware when the bundle is missing.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Binary names to probe — checked in order so the first match wins.
# On Windows yt-dlp requires the .exe extension; on POSIX the bare name.
_FFMPEG_NAMES: tuple[str, ...] = ("ffmpeg.exe", "ffmpeg")


def get_ffmpeg_path() -> str | None:
    """
    Return the path to the directory containing the bundled ffmpeg binary,
    or *None* if no bundle is present.

    Priority order
    ──────────────
    1. ``sys._MEIPASS/ffmpeg/``          — PyInstaller frozen build
    2. ``<project_root>/resources/ffmpeg/`` — source-mode / CI staging
    3. ``None``                           — fall back to system PATH
    """
    # ── 1. PyInstaller frozen build ───────────────────────────────────────
    # sys._MEIPASS is set by PyInstaller after unpacking the bundle.
    # It is NOT present when running from source, so getattr with a default
    # is the idiomatic guard.
    meipass: str | None = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "ffmpeg"
        if _directory_has_ffmpeg(candidate):
            logger.debug("FFmpeg found in PyInstaller bundle: %s", candidate)
            return str(candidate)
        # If we are frozen but the bundle is missing something went wrong
        # during the build — log a clear warning so CI catches it.
        logger.warning(
            "Running as frozen app but bundled FFmpeg not found at: %s. "
            "Post-processing (merging, thumbnail embedding) will use system FFmpeg.",
            candidate,
        )
        return None

    # ── 2. Source-mode / CI staging area ─────────────────────────────────
    # Walk up from this file's parent (utils/) to the project root, then
    # look for resources/ffmpeg/.  This path is populated by the GitHub
    # Actions workflow step before PyInstaller runs, and cleaned up after.
    project_root = Path(__file__).parent.parent
    candidate = project_root / "resources" / "ffmpeg"
    if _directory_has_ffmpeg(candidate):
        logger.debug("FFmpeg found in resources/ffmpeg: %s", candidate)
        return str(candidate)

    # ── 3. Not bundled — let yt-dlp search system PATH ───────────────────
    logger.debug(
        "No bundled FFmpeg found. yt-dlp will search the system PATH. "
        "If you need FFmpeg features, install it via your OS package manager."
    )
    return None


def _directory_has_ffmpeg(directory: Path) -> bool:
    """
    Return True if *directory* exists and contains an ffmpeg binary.

    Checks both the bare name (POSIX) and the .exe variant (Windows) so
    this function works correctly in cross-compilation CI scenarios.
    """
    if not directory.is_dir():
        return False
    return any((directory / name).is_file() for name in _FFMPEG_NAMES)
