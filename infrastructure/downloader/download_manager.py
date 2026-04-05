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
from typing import TYPE_CHECKING, Optional

from app.event_bus import EventBus
from app.event_bus import bus as global_bus
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

if TYPE_CHECKING:
    from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Orchestrates the full download lifecycle:

    1. Receives DownloadTask objects via enqueue()
    2. Submits them to a ThreadPoolExecutor (max_workers = config.max_concurrent)
    3. Publishes EventBus events on progress / completion / failure
    4. Routes to GalleryDlEngine when task.media_info.source_engine == "gallery_dl"
    """

    def __init__(
        self,
        config: ConfigManager,
        engine: YtDlpEngine,
        event_bus: Optional[EventBus] = None,
        gallery_engine: Optional[GalleryDlEngine] = None,
        story_engine_enabled: bool = False,
    ) -> None:
        self._config = config
        self._bus = event_bus or global_bus
        self._engine = engine
        # Optional gallery-dl engine — injected from main.py when available.
        # Typed as object to avoid circular imports; duck-typed at call site.
        self._gallery_engine = gallery_engine
        # Facebook Story engine flag — when True, facebook_story_engine is
        # imported lazily on first use (avoids loading Playwright at startup).
        # Injected from main.py; defaults to False so tests and non-CDP builds
        # are unaffected.
        self._story_engine_enabled: bool = story_engine_enabled
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

    # Keywords that identify unrecoverable errors — retrying these wastes time
    # and may trigger platform rate-limiting or account flags.
    _HARD_ERROR_KEYWORDS: tuple[str, ...] = (
        # Generic unrecoverable states
        "private",
        "removed",
        "not found",
        "404",
        "login",
        "unsupported url",
        "cancelled by user",
        "age",              # age-restricted without login
        "unavailable",      # "this video is unavailable"
        # TikTok / platform-specific deleted/unavailable video errors
        "currently not available",  # TikTok deleted video
        "video does not exist",     # TikTok removed video
        "this video is not available",  # TikTok region/deleted
        # Instagram-specific — account/auth issues that retrying cannot fix
        "checkpoint",       # account checkpoint verification required
        "challenge_required",  # two-factor / bot challenge
        "no video in this post",   # photo-only post — retry cannot add video
        "no video formats found",  # photo-only post (with cookies, yt-dlp >= 2024)
        # Vietnamese translations of the two photo-only yt-dlp messages above.
        # _friendly_error() in yt_dlp_engine translates them before raising, so
        # the raw English strings above never appear in the exception message.
        # Without these entries the task retries 4× unnecessarily.
        "bài đăng này chỉ có ảnh",  # "This post only has photos, no video"
        # Facebook-specific
        "content not available",    # post removed or region-blocked
        "this content isn",         # "This content isn't available"
        # Geographic / copyright blocks — retrying changes nothing
        "geo-restricted",
        "not available in your country",
        "copyright",        # copyright claim block
        "blocked",          # region/copyright blocked (from yt-dlp error text)
        # Account-level blocks
        "suspended",        # account suspended
        "members only",     # paywalled content
        "subscribers only",
        # yt-dlp internal bugs — retrying the same broken extractor path
        # never helps; user must update yt-dlp to fix these.
        "extractor error",  # yt-dlp extractor crash (e.g. KeyError on shortcode)
    )

    def _run_task(self, task: DownloadTask) -> None:
        """Execute a download with automatic retry on transient network errors.

        Retries up to ``config.max_retries`` times (default 3) with
        exponential back-off (1 s, 2 s, 4 s, …).  Hard errors — private
        videos, 404s, login-required, cancellation — are never retried.

        All terminal-state writes are wrapped in ``task._lock`` so that a
        concurrent ``snapshot()`` on the UI poll thread never observes a
        half-updated task (e.g. COMPLETED with progress still at 0.0).
        """
        # Guard the initial state transition inside the task lock so that a
        # concurrent snapshot() call on the UI poll thread never observes an
        # uninitialised started_at alongside DOWNLOADING status.
        with task._lock:
            task.started_at = time.time()
            task.status = DownloadStatus.DOWNLOADING
        self._bus.publish(EventBus.DOWNLOAD_STARTED, task=task)
        logger.info("Download started: %s", task.id)

        max_attempts = max(1, self._config.max_retries + 1)
        last_exc: Optional[Exception] = None
        # BUG-BU: set True when yt-dlp hits a photo-only error and gallery-dl
        # hasn't been tried yet (Remote API client omitted source_engine).
        _gallery_fallback_needed: bool = False

        for attempt in range(max_attempts):
            # Check for cancellation before each attempt (including before
            # the very first one, in case cancel() was called while queued).
            if task.is_cancellation_requested:
                break

            if attempt > 0:
                # Exponential back-off: 1 s, 2 s, 4 s, …  capped at 30 s.
                wait_s = min(2 ** (attempt - 1), 30)
                logger.info(
                    "Retrying task %s (attempt %d/%d) in %d s — previous error: %s",
                    task.id, attempt + 1, max_attempts, wait_s, last_exc,
                )
                # Reset visible progress so the UI shows the retry clearly.
                with task._lock:
                    task.progress = 0.0
                    task.speed = ""
                    task.eta = ""
                    task.status = DownloadStatus.DOWNLOADING
                self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)
                time.sleep(wait_s)

                # Re-check cancellation after sleep (user may have cancelled
                # during the back-off wait).
                if task.is_cancellation_requested:
                    break

            try:
                # ── Route: Facebook Story → CDP engine (Playwright) ───────
                # Must be checked BEFORE gallery/yt-dlp routing because Story
                # URLs also match the generic facebook.com domain used below.
                # The import is deferred so Playwright is never loaded on
                # desktop-only startups where story_engine_enabled=False.
                if self._story_engine_enabled:
                    from infrastructure.downloader.facebook_story_engine import (  # noqa: PLC0415
                        download_story,
                        is_facebook_story_url,
                    )
                    if is_facebook_story_url(task.url):
                        def _story_progress(pct: int, speed: str, msg: str) -> None:
                            with task._lock:
                                task.progress = float(pct)
                                task.speed    = speed
                                task.eta      = msg
                            self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)

                        result_path = download_story(
                            url=task.url,
                            config=self._config,
                            browser=getattr(self._config, "cookies_browser", "brave"),
                            on_progress=_story_progress,
                            timeout=90.0,
                        )
                        with task._lock:
                            task.filename = str(result_path)
                        last_exc = None
                        break  # success — skip yt-dlp / gallery routing

                # Route to gallery-dl engine when MediaInfo carries the hint.
                # Falls back to yt-dlp if gallery engine is not wired (e.g. tests).
                use_gallery = (
                    self._gallery_engine is not None
                    and task.media_info is not None
                    and getattr(task.media_info, "source_engine", "yt_dlp")
                    == "gallery_dl"
                )
                active_engine = self._gallery_engine if use_gallery else self._engine

                active_engine.download(
                    task,
                    on_progress=self._on_progress,
                    on_postprocess=self._on_progress,
                )
                last_exc = None
                break  # success — exit retry loop

            except Exception as exc:
                msg = str(exc).lower()

                # BUG-BU: yt-dlp photo-only error on a task submitted via the
                # Remote API without source_engine="gallery_dl" forwarded from
                # /api/analyse.  Detect both the raw English yt-dlp keywords AND
                # the Vietnamese _friendly_error translation (the actual string
                # raised by yt_dlp_engine.download).  Stop yt-dlp retries
                # immediately and flag a single gallery-dl attempt after the loop
                # — avoids 3 pointless yt-dlp retries before the final FAILED.
                _is_photo_error = (
                    "no video in this post" in msg
                    or "no video formats found" in msg
                    or "bài đăng này chỉ có ảnh" in msg
                )
                if (
                    _is_photo_error
                    and self._gallery_engine is not None
                    and task.media_info is not None
                    and getattr(task.media_info, "source_engine", "yt_dlp") == "yt_dlp"
                ):
                    logger.info(
                        "Task %s: yt-dlp photo-only error — switching to gallery-dl "
                        "(BUG-BU: Remote API client did not forward source_engine)",
                        task.id,
                    )
                    task.media_info.source_engine = "gallery_dl"
                    _gallery_fallback_needed = True
                    last_exc = exc
                    break  # stop yt-dlp retries; gallery-dl attempt follows below

                # Hard errors: stop immediately, no retry.
                if any(k in msg for k in self._HARD_ERROR_KEYWORDS):
                    logger.warning(
                        "Hard error for task %s (no retry): %s", task.id, exc
                    )
                    last_exc = exc
                    break

                last_exc = exc
                # Loop continues to next attempt (if any remain).

        # ── BUG-BU: gallery-dl fallback for photo-only posts ─────────────
        # When yt-dlp exhausted retries (or stopped early) with a photo-only
        # error and gallery-dl hasn't been tried, attempt gallery-dl once.
        # This covers the Remote API path where the iOS client sends
        # source_engine="yt_dlp" (default) instead of forwarding "gallery_dl"
        # from /api/analyse.  One attempt is enough — gallery-dl is fast and
        # a second failure is not recoverable without user action (e.g. cookies).
        if (
            _gallery_fallback_needed
            and last_exc is not None
            and not task.is_cancellation_requested
        ):
            logger.info(
                "Task %s: attempting gallery-dl fallback for photo-only post",
                task.id,
            )
            with task._lock:
                task.progress = 0.0
                task.speed = ""
                task.eta = ""
                task.status = DownloadStatus.DOWNLOADING
            self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)
            try:
                assert self._gallery_engine is not None  # guarded by _gallery_fallback_needed
                self._gallery_engine.download(
                    task,
                    on_progress=self._on_progress,
                    on_postprocess=self._on_progress,
                )
                last_exc = None
            except Exception as gdl_exc:
                last_exc = gdl_exc
                logger.warning(
                    "gallery-dl fallback failed for task %s: %s",
                    task.id, gdl_exc,
                )

        # ── Resolve final state ───────────────────────────────────────────
        if task.is_cancellation_requested:
            with task._lock:
                task.status = DownloadStatus.CANCELLED
                task.finished_at = time.time()
            logger.info("Task cancelled: %s", task.id)
            self._bus.publish(EventBus.DOWNLOAD_CANCELLED, task=task)

        elif last_exc is None:
            with task._lock:
                task.status = DownloadStatus.COMPLETED
                task.progress = 100.0
                task.finished_at = time.time()
            logger.info("Task completed: %s → %s", task.id, task.filename)
            self._bus.publish(EventBus.DOWNLOAD_COMPLETED, task=task)

        else:
            with task._lock:
                task.status = DownloadStatus.FAILED
                task.error_msg = str(last_exc)
                task.finished_at = time.time()
            logger.error(
                "Task failed after %d attempt(s) %s: %s",
                max_attempts, task.id, last_exc,
            )
            self._bus.publish(EventBus.DOWNLOAD_FAILED, task=task)

    def _on_progress(self, task: DownloadTask) -> None:
        self._bus.publish(EventBus.DOWNLOAD_PROGRESS, task=task)

    def _on_future_done(self, task_id: str, future: Future) -> None:
        # DEF-008: prune Future reference to prevent memory leak
        with self._lock:
            self._futures.pop(task_id, None)
        # DEF-009: _run_task already logs errors — only surface true escapes here
        exc = future.exception()
        if exc:
            logger.debug(
                "Unhandled exception escaped _run_task for task %s: %s", task_id, exc
            )
