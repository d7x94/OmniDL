"""
utils/soffice_locator.py
Locate the LibreOffice ``soffice`` binary used for Office <-> PDF conversion.

Mirrors the design of ffmpeg_locator.py / deno_locator.py:

  • System PATH                     -> shutil.which("soffice" / "libreoffice")
  • Well-known per-platform install directories

LibreOffice is NOT bundled with OmniDL (it is a ~700 MB suite). Office
conversions are therefore an optional capability: when the binary is absent
the document-convert service raises DocConvertToolMissingError and both the
desktop tab and the Remote API report the feature as unavailable instead of
failing with an opaque traceback.
"""

from __future__ import annotations

import logging
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PATH_NAMES: tuple[str, ...] = ("soffice", "libreoffice", "soffice.exe")

_WINDOWS_CANDIDATES: tuple[str, ...] = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

_MACOS_CANDIDATES: tuple[str, ...] = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/local/bin/soffice",
)

_LINUX_CANDIDATES: tuple[str, ...] = (
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
    "/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
)


def _platform_candidates() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return _WINDOWS_CANDIDATES
    if sys.platform == "darwin":
        return _MACOS_CANDIDATES
    return _LINUX_CANDIDATES


@lru_cache(maxsize=1)
def locate_soffice() -> Optional[Path]:
    """Return the absolute path to the LibreOffice binary, or None.

    Search order:
      1. System PATH (``soffice``, then ``libreoffice``)
      2. Well-known install directories for the current platform

    The result is cached for the process lifetime; call
    :func:`reset_soffice_cache` after the user installs LibreOffice without
    restarting OmniDL.
    """
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            logger.info("Using LibreOffice from PATH: %s", found)
            return Path(found).resolve()

    for candidate in _platform_candidates():
        p = Path(candidate)
        if p.is_file():
            logger.info("Using LibreOffice from well-known location: %s", p)
            return p.resolve()

    logger.info(
        "LibreOffice not found (PATH or well-known locations). "
        "Office <-> PDF conversion will be unavailable until it is installed."
    )
    return None


def reset_soffice_cache() -> None:
    """Clear the cached lookup so a freshly installed LibreOffice is picked up."""
    locate_soffice.cache_clear()


def has_soffice() -> bool:
    return locate_soffice() is not None
