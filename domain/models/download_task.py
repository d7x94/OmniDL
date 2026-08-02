"""
domain/models/download_task.py
Core domain entity.  Pure Python — zero infrastructure deps.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from domain.enums.download_status import DownloadStatus


@dataclass
class MediaInfo:
    """Lightweight metadata returned by the extraction step."""

    url: str
    title: str = "Unknown"
    uploader: str = ""
    uploader_id: str = ""
    duration: int = 0  # seconds
    thumbnail: str = ""
    platform: str = "unknown"
    formats: list[dict] = field(default_factory=list)
    is_live: bool = False
    was_live: bool = False
    video_id: str = ""  # yt-dlp's internal video ID (used for filename)
    tiktok_room_id: str = ""  # BUG-TT-25: room_id from live checker, bypasses yt-dlp unsigned room/info
    # Which engine produced this MediaInfo — routing hint for DownloadManager.
    # "yt_dlp"     → YtDlpEngine.download()   (default, all video platforms)
    # "gallery_dl" → GalleryDlEngine.download() (image/gallery platforms)
    source_engine: str = "yt_dlp"
    # Playlist / channel detection.
    # Non-empty when the analysed URL is a playlist, channel, or user page.
    # Each entry is the direct URL of one video in the playlist.
    # HomeTab redirects to BatchTab when this list is non-empty.
    # Empty list (default) = single-item result — existing behaviour unchanged.
    playlist_entries: list = field(default_factory=list)  # list[str]
    playlist_title: str = ""  # channel/playlist display name


def _set_event() -> threading.Event:
    """Helper: return an already-set threading.Event.

    Defined before DownloadTask so it can be referenced as
    `default_factory=_set_event` in the dataclass body (DEF-019).
    """
    e = threading.Event()
    e.set()
    return e


@dataclass
class DownloadTask:
    """
    Represents a single download job throughout its lifecycle.

    Immutable fields are set at creation; mutable fields are updated
    by the infrastructure layer as the download progresses.
    """

    # ── Identity ─────────────────────────────────────────────────────────
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    url: str = ""
    media_info: Optional[MediaInfo] = None

    # ── Options chosen by the user ────────────────────────────────────────
    format_id: str = "bestvideo+bestaudio/best"
    output_ext: str = "mp4"
    output_dir: str = ""  # resolved absolute path string

    # ── Mutable progress state ────────────────────────────────────────────
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0  # 0–100
    speed: str = ""  # "3.2 MiB/s"
    eta: str = ""  # "01:23"
    downloaded_bytes: int = 0
    total_bytes: int = 0
    filename: str = ""  # final output path
    error_msg: str = ""
    # Files downloaded in this task by GalleryDlEngine (populated only for
    # gallery-dl image downloads).  Used by TaildropService to zip only the
    # newly-downloaded files instead of the entire account directory.
    gallery_dl_files: list = field(default_factory=list)  # list[str]

    # ── Timing ────────────────────────────────────────────────────────────
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    # ── Pool-assigned cookie (not serialised) ────────────────────────────
    # Set by TikTokAccountPool.acquire() before _run_task executes.
    # Overrides _resolve_cookie() so the assigned account's cookie is used
    # for the full lifetime of this download.
    _cookie_override: str | None = field(default=None, compare=False, repr=False)

    # ── Control primitives (not serialised) ──────────────────────────────
    keep_partial: bool = False  # set by UI before cancel on live tasks → engine saves partial file

    _cancel_event: threading.Event = field(default_factory=threading.Event, compare=False, repr=False)
    _pause_event: threading.Event = field(default_factory=_set_event, compare=False, repr=False)
    # An RLock guards coordinated multi-field reads via snapshot().  Python's
    # GIL makes individual attribute assignments atomic, but reading a pair of
    # fields (e.g. downloaded_bytes + total_bytes) is not atomic — the UI poll
    # thread could see an inconsistent snapshot (e.g. "105 MB / 100 MB") without
    # this lock.
    _lock: threading.RLock = field(default_factory=threading.RLock, compare=False, repr=False)

    # ── Convenience properties ────────────────────────────────────────────

    @property
    def title(self) -> str:
        if self.media_info and self.media_info.title:
            return self.media_info.title
        return self.url[:80]

    @property
    def platform(self) -> str:
        if self.media_info:
            return self.media_info.platform
        return "unknown"

    @property
    def elapsed(self) -> str:
        if not self.started_at:
            return ""
        end = self.finished_at or time.time()
        s = int(end - self.started_at)
        m, sec = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    # ── Control ───────────────────────────────────────────────────────────

    def pause(self) -> None:
        with self._lock:  # DEF-004: atomic check-and-mutate
            if self.status == DownloadStatus.PROCESSING:
                return
            self._pause_event.clear()
            self.status = DownloadStatus.PAUSED

    def resume(self) -> None:
        with self._lock:  # DEF-004: atomic check-and-mutate
            self._pause_event.set()
            if self.status == DownloadStatus.PAUSED:
                self.status = DownloadStatus.DOWNLOADING

    def cancel(self) -> None:
        with self._lock:
            self._cancel_event.set()
            self._pause_event.set()  # unblock any waiting hook

    @property
    def is_cancellation_requested(self) -> bool:
        return self._cancel_event.is_set()

    def wait_if_paused(self) -> None:
        """Called inside the download hook — blocks while paused.

        Uses a 1-second timeout loop rather than an indefinite wait so that
        a stuck or mis-set pause event cannot hold a ThreadPoolExecutor worker
        slot permanently.  The loop also checks for cancellation so the thread
        can always exit cleanly regardless of pause state.
        """
        while not self._pause_event.wait(timeout=1.0):
            if self.is_cancellation_requested:
                return

    def snapshot(self) -> dict:
        """Thread-safe atomic read of all display fields.

        The UI poll loop must read multiple fields atomically to avoid seeing
        inconsistent state (e.g. downloaded_bytes advancing past total_bytes
        between two separate reads).  Always call this from the UI thread
        rather than accessing fields directly.
        """
        with self._lock:
            return {
                "status": self.status,
                "progress": self.progress,
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "speed": self.speed,
                "eta": self.eta,
                "filename": self.filename,
                "error_msg": self.error_msg,
            }

    # ── Serialisation (for history) ───────────────────────────────────────

    def to_dict(self) -> dict:
        # Acquire _lock for consistent multi-field read — identical to snapshot().
        # Without the lock, a concurrent progress hook writing filename + status
        # in sequence could produce a dict with status=COMPLETED but filename="".
        with self._lock:
            return {
                "id": self.id,
                "url": self.url,
                "output_dir": self.output_dir,
                "title": self.title,
                "platform": self.platform,
                "filename": self.filename,
                "status": self.status.name,
                "downloaded_bytes": self.downloaded_bytes,
                "total_bytes": self.total_bytes,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "error_msg": self.error_msg,
                "is_live": self.media_info.is_live if self.media_info else False,
            }
