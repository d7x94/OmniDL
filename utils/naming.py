"""
utils/naming.py
Centralized filename builder for all download output paths.

Keeps full Unicode (Vietnamese, CJK, emoji); strips only filesystem-illegal chars.
Used by taildrop_service.py and yt_dlp_engine.py (live rename).
"""

from __future__ import annotations

import re
import time

_ILLEGAL = re.compile(r'[/\\:*?"<>|\x00]')

# Synthetic title patterns produced by yt-dlp or download_service
_SYNTHETIC_TITLE_RE = re.compile(r"(?i)^tiktok-live video\b")
_TRAILING_TS_RE = re.compile(r"\s*\d{4}-\d{2}-\d{2}[ _]\d{2}[_:]\d{2}\s*$")


def sanitise_for_filesystem(text: str) -> str:
    return _ILLEGAL.sub("_", text)


def _clean_title(title: str, uploader: str) -> str:
    if not title:
        return ""
    if _SYNTHETIC_TITLE_RE.match(title):
        return ""
    if title.lstrip("@").lower().startswith(uploader.lower()):
        return ""
    return _TRAILING_TS_RE.sub("", title).strip()


def _cap_utf8(s: str, max_bytes: int) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def build_filename(
    *,
    uploader: str,
    date_label: str,
    title: str = "",
    video_id: str = "",
    ext: str,
    suffix: str = "",
    uploader_max: int = 50,
    title_max: int = 100,
    total_max: int = 200,
) -> str:
    uploader_safe = _cap_utf8(sanitise_for_filesystem(uploader), uploader_max)
    clean = _clean_title(title, uploader_safe)
    clean_safe = _cap_utf8(sanitise_for_filesystem(clean), title_max) if clean else ""

    stem = f"{uploader_safe} - {date_label}"
    if clean_safe:
        stem += f" - {clean_safe}"
    if video_id:
        stem += f" [{video_id}]"
    if suffix:
        stem += suffix

    stem = _cap_utf8(stem, total_max)
    return f"{stem}.{ext}" if ext else stem


def build_filename_from_task(
    task,
    *,
    is_live: bool = False,
    live_ts: str = "",
    ext: str | None = None,
    suffix: str = "",
) -> str:
    # For completed VOD downloads, task.filename is the authoritative name produced
    # by yt-dlp's output template (correct uploader, upload_date, video_id).
    # Reconstructing from media_info is unreliable: uploader may be empty for short
    # URLs (vt.tiktok.com), upload_date is not stored in MediaInfo, video_id may differ.
    if not is_live:
        existing = getattr(task, "filename", "") or ""
        if existing:
            from pathlib import Path as _Path
            _p = _Path(existing)
            _use_ext = ext or _p.suffix.lstrip(".")
            _stem = _p.stem + (suffix or "")
            return f"{_stem}.{_use_ext}" if _use_ext else _stem

    mi = getattr(task, "media_info", None)
    uploader = getattr(mi, "uploader", "") or "Unknown"
    title = getattr(mi, "title", "") or ""
    video_id = (getattr(mi, "video_id", "") or "")[:20]

    ts = getattr(task, "finished_at", None) or getattr(task, "created_at", 0)

    is_live_task = is_live or getattr(mi, "is_live", False) or getattr(mi, "was_live", False)
    if is_live_task:
        if live_ts:
            date_label = f"[LIVE] {live_ts}"
        else:
            date_label = f"[LIVE] {time.strftime('%Y-%m-%d %H-%M', time.localtime(ts))}"
    else:
        date_label = time.strftime("%Y-%m-%d", time.localtime(ts))

    return build_filename(
        uploader=uploader,
        date_label=date_label,
        title=title,
        video_id=video_id,
        ext=ext or "",
        suffix=suffix,
    )
