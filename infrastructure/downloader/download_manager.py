"""
infrastructure/downloader/download_manager.py
ThreadPoolExecutor-based concurrent download manager.
Emits events via EventBus; never touches the UI directly.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from app.event_bus import bus as global_bus, EventBus
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Orchestrates the full download lifecycle:

    1. Receives DownloadTask objects via enqueue()
    2. Submits them to a ThreadPoolExecutor (max_workers = config.max_concurrent)
    3. Publishes EventBus events on progress / completion / failure
    """

    def __init__(
        self,
        config: ConfigManager,
        engine: YtDlpEngine,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._config = config
        self._bus = event_bus or global_bus
        # A single shared engine instance is injected from main.py so that
        # extract_info() (DownloadService) and download() (DownloadManager)
        # use the same object.  Config changes and any engine-level state are
        # therefore consistent across both call sites.
        self._engine = engine
        self._lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}
        self._futures: dict[str, Future] = {}
        self._executor: Optional[ThreadPoolExecutor] = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._executor = ThreadPoolExecutor(
            max_workers=self._config.max_concurrent,
            thread_name_prefix="omnidl-dl",
        )
        logger.info(
            "DownloadManager started (max_concurrent=%d)",
            self._config.max_concurrent,
        )

    def shutdown(self, wait: bool = True) -> None:
        self._running = False
        # Cancel all queued tasks
        with self._lock:
            for task in self._tasks.values():
                if task.status in DownloadStatus.active_states():
                    task.cancel()
        if self._executor:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        logger.info("DownloadManager shut down")

    # ── Public API ────────────────────────────────────────────────────────

    def enqueue(self, task: DownloadTask) -> None:
        """Submit a task to the executor."""
        # The running check and both dict insertions must happen inside a single
        # lock acquisition to prevent two races:
        #   1. shutdown() running between the check and submit(), causing an
        #      unhandled RuntimeError from ThreadPoolExecutor.
        #   2. get_all_tasks() seeing a task whose future does not exist yet if
        #      the two dict insertions were guarded by separate lock calls.
        with self._lock:
            if not self._running or not self._executor:
                raise RuntimeError("DownloadManager is not running.")
            self._tasks[task.id] = task
            future = self._executor.submit(self._run_task, task)
            self._futures[task.id] = future
        future.add_done_callback(lambda f: self._on_future_done(task.id, f))
        logger.info("Enqueued task %s — %s", task.id, task.title)

    def pause(self, task_id: str) -> None:
        task = self._get_task(task_id)
        if task:
            task.pause()
            self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)

    def resume(self, task_id: str) -> None:
        task = self._get_task(task_id)
        if task:
            task.resume()
            self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)

    def cancel(self, task_id: str) -> None:
        task = self._get_task(task_id)
        if task:
            task.cancel()

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        return self._get_task(task_id)

    def get_all_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())

    def clear_terminal(self) -> None:
        """Remove completed / failed / cancelled tasks from tracking."""
        with self._lock:
            terminal = DownloadStatus.terminal_states()
            to_del = [
                tid for tid, t in self._tasks.items()
                if t.status in terminal
            ]
            for tid in to_del:
                del self._tasks[tid]
                self._futures.pop(tid, None)

    # ── Internal ──────────────────────────────────────────────────────────

    def _get_task(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def _run_task(self, task: DownloadTask) -> None:
        # Guard the initial state transition inside the task lock so that a
        # concurrent snapshot() call on the UI poll thread never observes an
        # uninitialised started_at alongside DOWNLOADING status.
        with task._lock:
            task.started_at = time.time()
            task.status = DownloadStatus.DOWNLOADING
        self._bus.publish(EventBus.DOWNLOAD_STARTED, task=task)
        logger.info("Download started: %s", task.id)

        try:
            self._engine.download(
                task,
                on_progress=self._on_progress,
                on_postprocess=self._on_progress,
            )

            # Wrap every terminal-state write in the task lock so the UI poll
            # thread's snapshot() call never reads a half-updated task (e.g.
            # status=COMPLETED with progress still at 0.0).
            if task.is_cancellation_requested:
                with task._lock:
                    task.status = DownloadStatus.CANCELLED
                    task.finished_at = time.time()
                logger.info("Task cancelled: %s", task.id)
                self._bus.publish(EventBus.DOWNLOAD_CANCELLED, task=task)
            else:
                with task._lock:
                    task.status = DownloadStatus.COMPLETED
                    task.progress = 100.0
                    task.finished_at = time.time()
                logger.info("Task completed: %s → %s", task.id, task.filename)
                self._bus.publish(EventBus.DOWNLOAD_COMPLETED, task=task)

        except Exception as exc:
            if task.is_cancellation_requested:
                with task._lock:
                    task.status = DownloadStatus.CANCELLED
                    task.finished_at = time.time()
                self._bus.publish(EventBus.DOWNLOAD_CANCELLED, task=task)
            else:
                with task._lock:
                    task.status = DownloadStatus.FAILED
                    task.error_msg = str(exc)
                    task.finished_at = time.time()
                logger.error("Task failed %s: %s", task.id, exc)
                self._bus.publish(EventBus.DOWNLOAD_FAILED, task=task)

    def _on_progress(self, task: DownloadTask) -> None:
        self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)

    def _on_future_done(self, task_id: str, future: Future) -> None:
        exc = future.exception()
        if exc:
            # logger.exception() already captures the traceback via exc_info;
            # passing exc_info= separately would emit a duplicate stack trace.
            logger.error(
                "Unhandled exception in task %s", task_id, exc_info=exc
            )
