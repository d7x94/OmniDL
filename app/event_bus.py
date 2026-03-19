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

    # ── Well-known event name constants ──────────────────────────────────

    DOWNLOAD_STARTED   = "download.started"
    DOWNLOAD_PROGRESS  = "download.progress"
    DOWNLOAD_COMPLETED = "download.completed"
    DOWNLOAD_FAILED    = "download.failed"
    DOWNLOAD_CANCELLED = "download.cancelled"
    ANALYSIS_DONE      = "analysis.done"
    ANALYSIS_FAILED    = "analysis.failed"


# ── Module-level singleton ────────────────────────────────────────────────
bus = EventBus()
