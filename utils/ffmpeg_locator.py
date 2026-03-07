"""
utils/ffmpeg_locator.py
Locate the bundled FFmpeg binary directory at runtime.

This module resolves the correct FFmpeg location whether OmniDL is:
  • Running as a PyInstaller frozen build  → sys._MEIPASS/ffmpeg/
  • Running from source / CI staging area  → resources/ffmpeg/
  • Neither (developer with system FFmpeg)  → shutil.which() on PATH

The returned ``FFmpegLocation`` carries the *directory* path used by
yt-dlp's ``ffmpeg_location`` option (which accepts a directory), plus
the individual binary paths for diagnostics.

Design notes
────────────
• sys._MEIPASS is the PyInstaller extraction directory.  All files
  declared in the spec's ``datas`` list are unpacked there at start-up.
  We place ffmpeg under a ``ffmpeg/`` sub-folder to avoid polluting the
  root of _MEIPASS with binaries.

• The ``resources/ffmpeg/`` path is used exclusively in CI (populated by
  the GitHub Actions workflow step before PyInstaller runs) and optionally
  by developers who want to test the bundled-FFmpeg path locally.  It is
  intentionally NOT committed to the repository — see .gitignore.

• Returning None rather than raising allows the application to start even
  without a bundled or system FFmpeg.  A WARNING / ERROR is emitted so
  developers and CI are aware when the bundle is missing.

• lru_cache(maxsize=1) ensures the filesystem is probed at most once per
  process; all callers share a single cached result.
"""
from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Binary names probed in order — first match wins.
# On Windows yt-dlp requires the .exe extension; on POSIX the bare name.
_FFMPEG_NAMES: tuple[str, ...] = ("ffmpeg.exe", "ffmpeg")
_FFPROBE_NAMES: tuple[str, ...] = ("ffprobe.exe", "ffprobe")


@dataclass
class FFmpegLocation:
    """Holds the resolved location of an FFmpeg installation.

    Attributes:
        source:     Origin: ``"bundle"`` (PyInstaller), ``"resources"``
                    (source-mode CI staging), or ``"system"`` (PATH).
        directory:  Resolved absolute path to the directory containing the
                    ffmpeg binary.  Passed to yt-dlp's ``ffmpeg_location``.
        ffmpeg_bin: Full absolute path to the ffmpeg binary.
        ffprobe_bin: Full absolute path to the ffprobe binary, or the
                    sentinel string ``"<not found>"`` when ffprobe is absent.
    """

    source: str
    directory: str
    ffmpeg_bin: str
    ffprobe_bin: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_binary(directory: Path, names: tuple[str, ...]) -> Optional[Path]:
    """Return the first regular file matching one of *names* inside *directory*.

    Checks names in order so ``.exe`` variants are preferred over bare names.
    Directories named ``ffmpeg`` are intentionally ignored (is_file check).
    Returns ``None`` when no match is found.
    """
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _probe_directory(directory: Path, source: str) -> Optional[FFmpegLocation]:
    """Probe *directory* for ffmpeg and ffprobe binaries.

    Returns an :class:`FFmpegLocation` when ffmpeg is found.  ffprobe is
    optional — its absence triggers a WARNING but still yields a (degraded)
    result because yt-dlp can operate without it for most tasks.

    Returns ``None`` when *directory* does not exist or contains no ffmpeg.

    Symlinks are resolved via ``Path.resolve()`` before the result is stored
    so that the returned ``directory`` field always contains a canonical path.
    """
    if not directory.is_dir():
        return None

    resolved = directory.resolve()
    ffmpeg_p = _find_binary(resolved, _FFMPEG_NAMES)
    if ffmpeg_p is None:
        return None

    ffprobe_p = _find_binary(resolved, _FFPROBE_NAMES)
    if ffprobe_p is None:
        logger.warning(
            "ffprobe is NOT in %s — thumbnail embedding and some "
            "post-processing features will be unavailable.",
            resolved,
        )
        ffprobe_str = "<not found>"
    else:
        ffprobe_str = str(ffprobe_p)

    return FFmpegLocation(
        source=source,
        directory=str(resolved),
        ffmpeg_bin=str(ffmpeg_p),
        ffprobe_bin=ffprobe_str,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def locate_ffmpeg() -> Optional[FFmpegLocation]:
    """Locate FFmpeg.  Result is cached for the lifetime of the process.

    Search priority
    ───────────────
    1. ``sys._MEIPASS/ffmpeg/``              — PyInstaller frozen build
    2. ``<project_root>/resources/ffmpeg/``  — source-mode / CI staging
    3. System PATH via ``shutil.which``

    Returns an :class:`FFmpegLocation` on success, or ``None`` when FFmpeg
    cannot be found by any method.

    Note (FIX [L6]): when running as a frozen app but the bundle is absent
    the function logs an ERROR *and falls through* to attempt the system PATH
    rather than returning ``None`` immediately.
    """
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)

    # ── 1. PyInstaller frozen bundle ─────────────────────────────────────
    if meipass is not None:
        candidate = Path(meipass) / "ffmpeg"
        result = _probe_directory(candidate, "bundle")
        if result is not None:
            logger.info(
                "Using bundled FFmpeg (PyInstaller): %s", result.directory
            )
            return result
        # FIX [L6]: Log error but fall through to system PATH.
        logger.error(
            "FROZEN APP: bundled FFmpeg not found at %s. "
            "Rebuild with FFmpeg staged in resources/ffmpeg/. "
            "Falling through to system PATH.",
            candidate,
        )
        # Fall through ↓

    # ── 2. Source-mode / CI staging area ─────────────────────────────────
    # Only probed when NOT running as a frozen bundle (meipass is None).
    if meipass is None:
        project_root = Path(__file__).parent.parent
        candidate = project_root / "resources" / "ffmpeg"
        result = _probe_directory(candidate, "resources")
        if result is not None:
            logger.info(
                "Using source-mode FFmpeg from resources/ffmpeg: %s",
                result.directory,
            )
            return result

    # ── 3. System PATH ────────────────────────────────────────────────────
    ffmpeg_which: Optional[str] = shutil.which("ffmpeg")
    ffprobe_which: Optional[str] = shutil.which("ffprobe")

    if ffmpeg_which is None:
        logger.error(
            "FFmpeg not found anywhere (bundle, resources/ffmpeg, or system "
            "PATH). Install FFmpeg via your OS package manager or rebuild "
            "the app bundle."
        )
        return None

    ffmpeg_dir = Path(ffmpeg_which).parent.resolve()

    if ffprobe_which is None:
        logger.warning(
            "ffprobe is NOT on PATH — thumbnail embedding and some "
            "post-processing features will be unavailable."
        )
        ffprobe_str = "<not found>"
    else:
        ffprobe_dir = Path(ffprobe_which).parent.resolve()
        if ffprobe_dir != ffmpeg_dir:
            logger.warning(
                "ffmpeg and ffprobe are in different directories: %s vs %s",
                ffmpeg_dir,
                ffprobe_dir,
            )
        ffprobe_str = ffprobe_which

    logger.info("Using system FFmpeg from PATH: %s", ffmpeg_dir)
    return FFmpegLocation(
        source="system",
        directory=str(ffmpeg_dir),
        ffmpeg_bin=ffmpeg_which,
        ffprobe_bin=ffprobe_str,
    )


def get_ffmpeg_path() -> Optional[str]:
    """Return the directory path string for yt-dlp's ``ffmpeg_location`` option.

    This is the public compatibility wrapper used throughout the codebase.
    Returns ``None`` when FFmpeg cannot be found (yt-dlp will search PATH).
    """
    result = locate_ffmpeg()
    return result.directory if result is not None else None


def get_ffmpeg_diagnostics() -> str:
    """Return a human-readable diagnostic string describing FFmpeg location.

    Useful for the Settings tab "About FFmpeg" panel and for CI log output.
    """
    result = locate_ffmpeg()
    if result is None:
        return (
            "FFmpeg: not found "
            "(install via OS package manager or rebuild the bundle)"
        )

    source_labels: dict[str, str] = {
        "bundle":    "bundled (PyInstaller)",
        "resources": "source-mode resources/ffmpeg/",
        "system":    "system PATH",
    }
    label = source_labels.get(result.source, result.source)
    return (
        f"FFmpeg: {label}\n"
        f"  directory={result.directory}\n"
        f"  ffmpeg={result.ffmpeg_bin}\n"
        f"  ffprobe={result.ffprobe_bin}"
    )
