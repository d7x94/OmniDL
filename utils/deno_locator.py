"""
utils/deno_locator.py
Locate the bundled Deno binary at runtime.

Mirrors the design of ffmpeg_locator.py exactly:

  • PyInstaller frozen build  → sys._MEIPASS/deno/deno[.exe]
  • Source / CI staging area  → resources/deno/deno[.exe]
  • Developer machine         → shutil.which("deno")

Deno is required by yt-dlp to solve YouTube's JavaScript n-challenge
(encrypted nonce in stream URLs). Without it, yt-dlp cannot obtain valid
download URLs for many YouTube videos.

The resolved path is passed to yt-dlp via the JS_RUNTIMES / js_runtimes
option (or as a PATH prefix) so yt-dlp finds deno without a system install.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DENO_NAMES: tuple[str, ...] = ("deno.exe", "deno")


def _find_deno_in(directory: Path) -> Optional[Path]:
    if not directory.is_dir():
        return None
    for name in _DENO_NAMES:
        p = directory / name
        if p.is_file():
            return p.resolve()
    return None


@lru_cache(maxsize=1)
def locate_deno() -> Optional[Path]:
    """Return the absolute path to the deno binary, or None.

    Search order (same priority as ffmpeg_locator):
      1. sys._MEIPASS/deno/          — PyInstaller frozen bundle
      2. <project_root>/resources/deno/ — source-mode / CI staging
      3. System PATH (shutil.which)
    """
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)

    # 1. Frozen bundle
    if meipass is not None:
        p = _find_deno_in(Path(meipass) / "deno")
        if p:
            logger.info("Using bundled Deno (PyInstaller): %s", p)
            return p
        logger.error(
            "FROZEN APP: bundled Deno not found in %s/deno/. "
            "Rebuild with Deno staged in resources/deno/.",
            meipass,
        )
        # Fall through to system PATH

    # 2. Source-mode / CI staging
    if meipass is None:
        project_root = Path(__file__).parent.parent
        p = _find_deno_in(project_root / "resources" / "deno")
        if p:
            logger.info("Using source-mode Deno from resources/deno: %s", p)
            return p

    # 3. System PATH
    which = shutil.which("deno")
    if which:
        logger.info("Using system Deno from PATH: %s", which)
        return Path(which).resolve()

    logger.warning(
        "Deno not found (bundle, resources/deno, or PATH). "
        "YouTube JS challenge solving will be unavailable. "
        "Install Deno: https://deno.land"
    )
    return None


def get_deno_path() -> Optional[str]:
    """Return deno binary path string, or None if not found."""
    p = locate_deno()
    return str(p) if p else None


def get_deno_env() -> dict[str, str]:
    """Return env vars to prepend bundled Deno to PATH for subprocess calls.

    yt-dlp spawns deno as a subprocess by searching PATH.
    This helper returns a modified env dict with the deno directory
    prepended, so yt-dlp finds the bundled binary without a system install.
    """
    p = locate_deno()
    if p is None:
        return {}
    deno_dir = str(p.parent)
    current_path = os.environ.get("PATH", "")
    return {"PATH": deno_dir + os.pathsep + current_path}
