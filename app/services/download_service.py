"""
app/services/download_service.py
Application service — the only entry point the UI is allowed to call.
Orchestrates use-cases, wires infrastructure, never touches CTk widgets.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from app.event_bus import bus as global_bus, EventBus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
from infrastructure.storage.history_repository import HistoryRepository
from utils.helpers import is_valid_url

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._config = config
        self._manager = download_manager
        self._history = history_repo
        self._engine = engine
        self._bus = event_bus or global_bus

        # Wire completion → history save
        self._bus.subscribe(EventBus.DOWNLOAD_COMPLETED, self._on_completed)
        self._bus.subscribe(EventBus.DOWNLOAD_FAILED, self._on_failed)
        self._bus.subscribe(EventBus.DOWNLOAD_CANCELLED, self._on_cancelled)

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
                self._bus.publish(EventBus.ANALYSIS_FAILED, error=str(exc))
                on_error(str(exc))

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
        """Create a DownloadTask and submit it to the manager."""
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

    # ── Internal ──────────────────────────────────────────────────────────

    def _on_completed(self, task: DownloadTask) -> None:
        # Dispatch the disk write to a daemon thread so the download worker
        # is freed immediately for the next queued item.  history_repo.add()
        # calls json.dumps() + file.write() synchronously and would otherwise
        # hold the worker slot during I/O, especially for large histories.
        threading.Thread(
            target=self._history.add, args=(task,), daemon=True,
            name="omnidl-history-save",
        ).start()

    def _on_failed(self, task: DownloadTask) -> None:
        threading.Thread(
            target=self._history.add, args=(task,), daemon=True,
            name="omnidl-history-save",
        ).start()

    def _on_cancelled(self, task: DownloadTask) -> None:
        threading.Thread(
            target=self._history.add, args=(task,), daemon=True,
            name="omnidl-history-save",
        ).start()
