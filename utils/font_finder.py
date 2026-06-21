"""Cross-platform font path discovery for PIL and ffmpeg drawtext."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

_LINUX_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_WINDOWS_FONTS = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/verdana.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
]


@lru_cache(maxsize=1)
def find_font_path() -> str | None:
    """Return first existing font path, preferring native platform fonts."""
    candidates = _WINDOWS_FONTS + _LINUX_FONTS if sys.platform == "win32" else _LINUX_FONTS + _WINDOWS_FONTS
    for p in candidates:
        if Path(p).exists():
            return p
    return None
