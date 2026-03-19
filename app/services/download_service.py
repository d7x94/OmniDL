"""
app/services/download_service.py
Application service — the only entry point the UI is allowed to call.
Orchestrates use-cases, wires infrastructure, never touches CTk widgets.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from app.event_bus import EventBus
from app.event_bus import bus as global_bus
from app.services.ffmpeg_convert_service import FfmpegConvertService
from app.services.thumbnail_service import ThumbnailService
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
from infrastructure.storage.history_repository import HistoryRepository
from utils.helpers import is_valid_url

if TYPE_CHECKING:
    from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine

logger = logging.getLogger(__name__)

# yt-dlp errors that mean the post is image-only.
# When these occur on a supported image platform, we fall back to gallery-dl.
_PHOTO_ERRORS = (
    "no video in this post",
    "no video formats found",
    "no formats found",
)


def _should_fallback_to_gallery_dl(url: str, error_msg: str) -> bool:
    """
    Return True when a yt-dlp failure should be retried with gallery-dl.
    Only triggers for photo-specific errors on supported image platforms.
    Auth / rate-limit errors won't be helped by gallery-dl — don't fall back.
    """
    from infrastructure.downloader.gallery_dl_engine import is_gallery_dl_url
    if not is_gallery_dl_url(url):
        return False
    return any(k in error_msg.lower() for k in _PHOTO_ERRORS)


class DownloadService:
    """
    Facade for the UI layer.
    Thread-safe; all heavy work is dispatched to background threads.
    """

    def __init__(
        self,
        config: ConfigManager,
        download_manager: DownloadManager,
        history_repo: HistoryRepository,
        engine: YtDlpEngine,
        event_bus: Optional[EventBus] = None,
        gallery_engine: Optional[GalleryDlEngine] = None,
    ) -> None:
        self._config = config
        self._manager = download_manager
        self._history = history_repo
        self._engine = engine
        self._bus = event_bus or global_bus
        # Optional gallery-dl engine for image platform fallback.
        # Typed as object to avoid circular imports; duck-typed at call site.
        self._gallery_engine = gallery_engine

        # DEF-005: single-threaded executor so history writes survive shutdown
        self._history_executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="omnidl-history"
        )

        self._converter = FfmpegConvertService()
        self._thumbnail_svc = ThumbnailService()

        # Wire completion → history save (DEF-018: one handler for all terminal states)
        self._bus.subscribe(EventBus.DOWNLOAD_COMPLETED, self._save_to_history)
        self._bus.subscribe(EventBus.DOWNLOAD_FAILED, self._save_to_history)
        self._bus.subscribe(EventBus.DOWNLOAD_CANCELLED, self._save_to_history)

    # ── Analysis (async) ──────────────────────────────────────────────────

    def analyse_url(
        self,
        url: str,
        on_done: Callable[[MediaInfo], None],
        on_error: Callable[[str], None],
    ) -> None:
        """
        Fetch metadata for *url* in a daemon thread.
        Calls *on_done* or *on_error* on completion (still background thread —
        UI must use .after() to marshal to main thread).
        """
        if not is_valid_url(url):
            on_error("Invalid URL — must start with http:// or https://")
            return

        def _worker() -> None:
            try:
                info = self._engine.extract_info(url)
                self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                on_done(info)
            except Exception as exc:
                err = str(exc)
                # Auto-routing: when yt-dlp finds no video formats on a
                # supported image platform, retry silently with gallery-dl.
                # The user never sees this fallback happen — they just get
                # the correct MediaInfo (source_engine="gallery_dl").
                if self._gallery_engine and _should_fallback_to_gallery_dl(url, err):
                    logger.info(
                        "yt-dlp returned no video formats for %s — "
                        "falling back to gallery-dl",
                        url,
                    )
                    try:
                        info = self._gallery_engine.extract_info(url)
                        self._bus.publish(EventBus.ANALYSIS_DONE, info=info)
                        on_done(info)
                        return
                    except Exception as gdl_exc:
                        # gallery-dl also failed — report its error (more specific)
                        err = str(gdl_exc)
                self._bus.publish(EventBus.ANALYSIS_FAILED, error=err)
                on_error(err)

        threading.Thread(target=_worker, daemon=True, name="omnidl-analyse").start()

    # ── Download lifecycle ────────────────────────────────────────────────

    def start_download(
        self,
        url: str,
        media_info: MediaInfo,
        format_id: str,
        output_ext: str,
        output_dir: Optional[Path] = None,
    ) -> DownloadTask:
        """Create a DownloadTask and submit it to the manager.

        If the same URL is already QUEUED, DOWNLOADING, or PROCESSING the
        existing task is returned immediately — no duplicate is created.
        Re-downloading a COMPLETED/FAILED/CANCELLED URL always starts a new job.
        """
        # Duplicate guard: only block active (not terminal) duplicates.
        for existing in self._manager.get_all_tasks():
            if (existing.url == url
                    and existing.status in DownloadStatus.active_states()):
                logger.info(
                    "Duplicate URL ignored — task %s already active: %s",
                    existing.id, url,
                )
                return existing

        resolved_dir = output_dir or self._config.download_dir
        resolved_dir.mkdir(parents=True, exist_ok=True)

        task = DownloadTask(
            url=url,
            media_info=media_info,
            format_id=format_id,
            output_ext=output_ext,
            output_dir=str(resolved_dir),
        )
        self._manager.enqueue(task)
        return task

    def pause_download(self, task_id: str) -> None:
        self._manager.pause(task_id)

    def resume_download(self, task_id: str) -> None:
        self._manager.resume(task_id)

    def cancel_download(self, task_id: str) -> None:
        self._manager.cancel(task_id)

    def clear_finished(self) -> None:
        self._manager.clear_terminal()

    # ── Query ─────────────────────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Return a single task by ID, or None if not found."""
        return self._manager.get_task(task_id)

    def get_all_tasks(self) -> list[DownloadTask]:
        return self._manager.get_all_tasks()

    def get_history(self) -> list[dict]:
        return self._history.all()

    def search_history(self, query: str) -> list[dict]:
        return self._history.search(query)

    def clear_history(self) -> None:
        self._history.clear()

    def convert_to_mp4(
        self,
        source: Path,
        on_progress: Optional[Callable[[float], None]] = None,
        on_done: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Convert *source* to MP4/H.264/AAC in a background thread.

        Callbacks fire on the worker thread — UI callers must marshal to
        the main thread via ``widget.after(0, ...)``.
        """
        self._converter.convert(
            source=source,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
        )

    def fetch_thumbnail(
        self,
        url: str,
        width: int,
        height: int,
        on_done: Callable,
        on_error: Callable[[str], None],
    ) -> None:
        """Fetch and resize a thumbnail in a background thread.

        Delegates to ThumbnailService which validates the URL against
        SSRF patterns (RFC-1918, loopback, link-local) before fetching.
        Callbacks fire on the worker thread — callers must use after() to
        marshal widget updates to the UI thread.
        """
        self._thumbnail_svc.fetch_async(
            url=url,
            width=width,
            height=height,
            on_done=on_done,
            on_error=on_error,
        )

    def check_profile_live(
        self,
        url: str,
        on_done: "Callable[[Optional[str]], None]",
        on_error: "Callable[[str], None]",
    ) -> None:
        """Check if an Instagram profile URL is currently live.

        Spawns a daemon thread (same pattern as analyse_url).
        Calls on_done(live_url_or_None) or on_error(message).
        Callers must use after() to marshal UI updates.
        """
        import threading

        from utils.instagram_live_checker import (
            check_instagram_live,
            extract_instagram_username,
        )

        username = extract_instagram_username(url)
        if not username:
            on_error("Không thể lấy username từ URL.")
            return

        # Use Instagram-specific cookie when available — instagram_live_checker
        # requires a valid sessionid from Instagram (not from another platform).
        from infrastructure.downloader.yt_dlp_engine import _resolve_cookie
        cookie_file = (
            _resolve_cookie("https://www.instagram.com/", self._config) or ""
        )
        proxy       = self._config.proxy

        def _worker() -> None:
            try:
                live_url = check_instagram_live(
                    username=username,
                    cookie_file=cookie_file,
                    proxy=proxy,
                )
                on_done(live_url)
            except Exception as exc:
                on_error(str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Flush pending history writes and release resources (DEF-005).

        Call this AFTER manager.shutdown(wait=True) to ensure all
        completion events have already been published before the
        executor is shut down.
        """
        self._history_executor.shutdown(wait=True)

    # ── Internal ──────────────────────────────────────────────────────────

    def _save_to_history(self, task: DownloadTask) -> None:
        """Submit a history-write job (DEF-005, DEF-018).

        Replaces three identical daemon-thread handlers with one method
        backed by a non-daemon ThreadPoolExecutor so writes complete
        before process exit.
        """
        self._history_executor.submit(self._history.add, task)
