"""
app/event_bus.py
Thread-safe publish-subscribe event bus.
UI subscribes; infrastructure publishes.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

Handler = Callable[..., None]


class EventBus:
    """
    Lightweight synchronous event bus.

    Events are delivered on the publisher's thread.
    The UI must marshal callbacks to the main thread via after().
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
