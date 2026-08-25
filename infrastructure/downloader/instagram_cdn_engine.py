"""
infrastructure/downloader/instagram_cdn_engine.py
Anonymous streaming download for a pre-signed Instagram/Facebook CDN URL
pasted directly by the user -- IDM parity. The URL already carries its own
signature (oh=/oe=), so no Instagram API call and no session cookie are
needed: just the same anonymous GET/Range a browser-originated CDN link
already works with.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from utils.instagram_http import is_ig_cdn_host

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


def is_ig_cdn_url(url: str) -> bool:
    """True for a pasted, pre-signed Instagram/Facebook CDN media URL.

    Validates the URL's actual authority (via urlparse), not just a
    substring match -- a string like
    "https://attacker.example/?u=https://real.fbcdn.net/x?oh=1&oe=1" must
    not pass, since is_ig_cdn_url() gates an unauthenticated fetch that
    later uses this exact string as the literal request URL.
    """
    if not is_ig_cdn_host(url):
        return False
    query = parse_qs(urlparse(url).query)
    return "oh" in query and "oe" in query


# Extensions an Instagram/Facebook CDN link actually serves.  is_ig_cdn_url()
# accepts any signed media URL, photos included, so the container must come
# from the URL instead of being hardcoded to .mp4 -- a JPEG written as .mp4
# opens in no player and in no image viewer.
_CDN_EXTS = frozenset(
    {".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
)


def _ext_for(url: str) -> str:
    """Return the media extension carried by *url*'s path, or ".mp4"."""
    ext = Path(urlparse(url).path).suffix.lower()
    return ext if ext in _CDN_EXTS else ".mp4"


def download_ig_cdn_url(
    url: str,
    output_dir: Path,
    filename_hint: str,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> Path:
    """Stream-download a pre-signed CDN URL straight to *output_dir*.

    Sends no cookies at all -- User-Agent + Referer only, matching IDM's
    anonymous fetch of the same URL. Raises RuntimeError with the engine
    message-prefix contract (``not found:`` / ``blocked:``) on failure so
    download_manager._HARD_ERROR_KEYWORDS classifies an expired oe=
    signature as a hard error rather than retrying it.
    """
    import requests

    headers = {
        "User-Agent": _UA,
        "Referer": "https://www.instagram.com/",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{filename_hint}{_ext_for(url)}"
    stem = dest.stem
    counter = 1
    while dest.exists():
        dest = output_dir / f"{stem} ({counter}){dest.suffix}"
        counter += 1
    part = dest.with_suffix(dest.suffix + ".part")

    with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
        if resp.status_code == 404:
            raise RuntimeError(
                "not found: CDN link đã hết hạn (chữ ký oe= hết hạn). Dán link mới từ trình duyệt."
            )
        if resp.status_code == 403:
            raise RuntimeError("blocked: CDN link bị từ chối truy cập (403).")
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        speed_win: list[tuple[float, int]] = []

        try:
            with part.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    speed_win.append((now, len(chunk)))
                    speed_win = [(t, b) for t, b in speed_win if now - t <= 3.0]
                    bps = (
                        sum(b for _, b in speed_win) / max(now - speed_win[0][0], 0.001) if speed_win else 0.0
                    )
                    pct = int(downloaded * 100 / total) if total else 0
                    if on_progress:
                        try:
                            on_progress(pct, f"{bps / 1024:.0f} KB/s")
                        except Exception:
                            pass
        except Exception:
            # Connection dropped mid-stream: don't leave a partial .part file
            # or a task stuck retrying against a hard-error keyword that
            # won't match a generic transient exception message.
            part.unlink(missing_ok=True)
            raise

    if part.stat().st_size < 1024:
        part.unlink(missing_ok=True)
        raise RuntimeError("not found: File tải về quá nhỏ -- CDN link có thể đã hết hạn.")

    part.rename(dest)
    logger.info("ig_cdn: download complete -- %s", dest.name)
    return dest
