"""
utils/naming.py
Centralized filename builder for all download output paths.

Keeps full Unicode (Vietnamese, CJK, emoji); strips only filesystem-illegal chars.
Used by taildrop_service.py and yt_dlp_engine.py (live rename).
"""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path as _Path
from pathlib import PurePath

# Filesystem-illegal characters.  Control characters (\x01-\x1f) are illegal on
# Windows and produce unopenable files elsewhere, so they are stripped too.
_ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f]')

# Unicode TAG characters (U+E0000-U+E007F) are the invisible payload of emoji
# flag sequences such as the England flag in "Морган Ерболат 🏴󠁧󠁢󠁥󠁮".  They are
# legal on disk but Taildrop peers answer "400 Bad Request: invalid filename"
# for them, and the ASCII retry then dropped the whole Cyrillic name segment.
# Deleted (not replaced with "_") so the visible flag emoji survives intact.
_INVISIBLE_TAGS = re.compile(r"[\U000e0000-\U000e007f]")

# Letters that carry no combining mark, so NFKD alone cannot transliterate them.
_ASCII_MAP = {
    "\u0111": "d", "\u0110": "D",   # đ Đ  (Vietnamese)
    "\u00f0": "d", "\u00d0": "D",   # ð Ð
    "\u00f8": "o", "\u00d8": "O",   # ø Ø
    "\u00df": "ss",                 # ß
    "\u00e6": "ae", "\u00c6": "AE",
    "\u0153": "oe", "\u0152": "OE",
    "\u0142": "l", "\u0141": "L",   # ł Ł
    "\u00fe": "th", "\u00de": "Th",
    "\u0131": "i",                  # ı
}

_WS_RUN = re.compile(r"\s{2,}")

# Synthetic title patterns produced by yt-dlp or download_service
_SYNTHETIC_TITLE_RE = re.compile(r"(?i)^tiktok-live video\b")
_TRAILING_TS_RE = re.compile(r"\s*\d{4}-\d{2}-\d{2}[ _]\d{2}[_:]\d{2}\s*$")


def sanitise_for_filesystem(text: str) -> str:
    return _ILLEGAL.sub("_", _INVISIBLE_TAGS.sub("", text))


def to_ascii_filename(name: str, fallback: str = "file") -> str:
    """Best-effort ASCII transliteration of a filename, keeping the extension.

    Used only as a *fallback* for peers that reject non-ASCII names (some
    Taildrop receivers answer "400 Bad Request: invalid filename").  Unlike a
    plain ``encode("ascii", "ignore")`` this keeps the information that has an
    ASCII equivalent:

        "cậu ấy thật đáng yêu"  -> "cau ay that dang yeu"   (was "cu y tht ng yu")
        "ALEE ❗️ - 2026-08-28"  -> "ALEE - 2026-08-28"      (was "ALEE  - 2026-08-28")
        "家有兩兄妹 - 2026-06-10" -> "2026-06-10"             (was "- 2026-06-10")

    Scripts with no ASCII equivalent (CJK, Arabic, emoji) still cannot be
    represented, but the leftover separators are cleaned up so the result never
    starts with a dangling " - " and is never empty.
    """
    path = PurePath(name)
    stem, suffix = path.stem, path.suffix

    for src, dst in _ASCII_MAP.items():
        stem = stem.replace(src, dst)
    stem = unicodedata.normalize("NFKD", stem)
    stem = "".join(ch for ch in stem if not unicodedata.combining(ch))
    stem = stem.encode("ascii", "ignore").decode("ascii")

    # Drop segments that transliteration emptied out ("家有兩兄妹 - date" -> "date").
    segments = [seg.strip() for seg in stem.split(" - ")]
    stem = " - ".join(seg for seg in segments if seg)
    stem = _WS_RUN.sub(" ", stem).strip()

    suffix = suffix.encode("ascii", "ignore").decode("ascii")
    return (stem or fallback) + suffix


def _clean_title(title: str, uploader: str) -> str:
    if not title:
        return ""
    if _SYNTHETIC_TITLE_RE.match(title):
        return ""
    # Guard on `uploader`: "".lower() is a prefix of every string, so an empty
    # uploader would silently discard every title.
    if uploader and title.lstrip("@").lower().startswith(uploader.lower()):
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
        # A directory output (gallery-dl albums) is named after the account only —
        # it carries no date/title/id, so rebuild from media_info instead.
        if existing and not _Path(existing).is_dir():
            _p = _Path(existing)
            _use_ext = ext or _p.suffix.lstrip(".")
            _stem = _p.stem + (suffix or "")
            return f"{_stem}.{_use_ext}" if _use_ext else _stem

    mi = getattr(task, "media_info", None)
    uploader = getattr(mi, "uploader", "") or "Unknown"
    title = getattr(mi, "title", "") or ""
    video_id = (getattr(mi, "video_id", "") or "")[:30]

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
