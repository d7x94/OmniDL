"""
app/event_bus.py
Thread-safe publish-subscribe event bus.
UI subscribes; infrastructure publishes.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from pathlib import Path

    from domain.models.conversion_job import ConversionJob
    from domain.models.download_task import DownloadTask, MediaInfo

logger = logging.getLogger(__name__)

Handler = Callable[..., None]


class EventBus:
    """
    Lightweight synchronous event bus.

    Events are delivered on the publisher's thread.
    The UI must marshal callbacks to the main thread via after().

    Typed convenience methods (publish_download_started etc.) are provided
    for IDE auto-complete and static analysis.  The underlying publish()
    method with **kwargs remains fully supported for backward compatibility.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        # RLock instead of Lock: a handler that calls subscribe() or
        # unsubscribe() on the same thread during publish() would deadlock
        # with a plain Lock.  RLock allows the same thread to re-acquire
        # without blocking, making the bus safe for re-entrant use.
        self._lock = threading.RLock()

    # ── Subscription ─────────────────────────────────────────────────────

    def subscribe(self, event: str, handler: Handler) -> None:
        with self._lock:
            self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        with self._lock:
            handlers = self._handlers.get(event, [])
            if handler in handlers:
                handlers.remove(handler)

    # ── Publishing ───────────────────────────────────────────────────────

    def publish(self, event: str, **kwargs: Any) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event, []))
        for h in handlers:
            try:
                h(**kwargs)
            except Exception:
                logger.exception("EventBus handler error for event=%s", event)

    # ── Typed convenience publishers ─────────────────────────────────────
    # These are thin wrappers that make call-sites self-documenting and
    # allow IDE type-checking to catch wrong payload keys at write time.
    # Runtime behaviour is identical to calling publish() directly.

    def publish_download_started(self, task: "DownloadTask") -> None:
        self.publish(self.DOWNLOAD_STARTED, task=task)

    def publish_download_progress(self, task: "DownloadTask") -> None:
        self.publish(self.DOWNLOAD_PROGRESS, task=task)

    def publish_download_completed(self, task: "DownloadTask") -> None:
        self.publish(self.DOWNLOAD_COMPLETED, task=task)

    def publish_download_failed(self, task: "DownloadTask") -> None:
        self.publish(self.DOWNLOAD_FAILED, task=task)

    def publish_download_cancelled(self, task: "DownloadTask") -> None:
        self.publish(self.DOWNLOAD_CANCELLED, task=task)

    def publish_analysis_done(self, info: "MediaInfo") -> None:
        self.publish(self.ANALYSIS_DONE, info=info)

    def publish_analysis_failed(self, error: str) -> None:
        self.publish(self.ANALYSIS_FAILED, error=error)

    def publish_taildrop_completed(
        self, task: "DownloadTask", dest_node: str
    ) -> None:
        self.publish(self.TAILDROP_COMPLETED, task=task, dest_node=dest_node)

    def publish_taildrop_failed(
        self, task: "DownloadTask", dest_node: str, error: str
    ) -> None:
        self.publish(self.TAILDROP_FAILED, task=task, dest_node=dest_node, error=error)

    def publish_convert_taildrop_completed(
        self, out_path: "Path", dest_node: str
    ) -> None:
        """Fired when a converted file is successfully sent to a Tailscale peer."""
        self.publish(self.CONVERT_TAILDROP_COMPLETED, out_path=out_path, dest_node=dest_node)

    def publish_convert_taildrop_failed(
        self, out_path: "Path", dest_node: str, error: str
    ) -> None:
        """Fired when Taildrop transfer of a converted file fails."""
        self.publish(self.CONVERT_TAILDROP_FAILED, out_path=out_path, dest_node=dest_node, error=error)

    # ── Convert event publishers ──────────────────────────────────────────

    def publish_convert_started(self, job: "ConversionJob") -> None:
        self.publish(self.CONVERT_STARTED, job=job)

    def publish_convert_progress(self, job: "ConversionJob") -> None:
        self.publish(self.CONVERT_PROGRESS, job=job)

    def publish_convert_completed(self, job: "ConversionJob") -> None:
        self.publish(self.CONVERT_COMPLETED, job=job)

    def publish_convert_failed(self, job: "ConversionJob") -> None:
        self.publish(self.CONVERT_FAILED, job=job)

    def publish_convert_cancelled(self, job: "ConversionJob") -> None:
        self.publish(self.CONVERT_CANCELLED, job=job)

    # ── Well-known event name constants ──────────────────────────────────

    DOWNLOAD_STARTED   = "download.started"
    DOWNLOAD_PROGRESS  = "download.progress"
    DOWNLOAD_COMPLETED = "download.completed"
    DOWNLOAD_FAILED    = "download.failed"
    DOWNLOAD_CANCELLED = "download.cancelled"
    ANALYSIS_DONE      = "analysis.done"
    ANALYSIS_FAILED    = "analysis.failed"
    TAILDROP_COMPLETED = "taildrop.completed"   # kwargs: task, dest_node
    TAILDROP_FAILED    = "taildrop.failed"       # kwargs: task, dest_node, error
    CONVERT_TAILDROP_COMPLETED = "convert.taildrop.completed"  # kwargs: out_path, dest_node
    CONVERT_TAILDROP_FAILED    = "convert.taildrop.failed"     # kwargs: out_path, dest_node, error
    CONVERT_STARTED    = "convert.started"       # kwargs: job (ConversionJob)
    CONVERT_PROGRESS   = "convert.progress"      # kwargs: job
    CONVERT_COMPLETED  = "convert.completed"     # kwargs: job
    CONVERT_FAILED     = "convert.failed"        # kwargs: job
    CONVERT_CANCELLED  = "convert.cancelled"     # kwargs: job


# ── Module-level singleton ────────────────────────────────────────────────
bus = EventBus()
