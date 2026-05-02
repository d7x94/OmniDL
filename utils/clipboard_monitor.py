"""
utils/clipboard_monitor.py
Clipboard URL monitor -- polls the system clipboard every 1.5 s and fires a
callback when a new HTTP/HTTPS URL is detected.

Rules:
- Only fires when the detected URL differs from the last seen URL.
- Ignores clipboard content that is not a valid http(s) URL.
- Polling stops automatically when stop() is called.
- Thread-safe: all state is protected by a lock; the callback is invoked from
  the polling thread and must be routed through a _ui_queue by the caller.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_URL_TRAILING_JUNK = frozenset(".,;)\"'>]")

_POLL_INTERVAL = 1.5  # seconds


def _extract_url(text: str) -> Optional[str]:
    m = _URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip("".join(_URL_TRAILING_JUNK))


class ClipboardMonitor:
    """
    Polls the clipboard in a daemon thread and calls on_new_url(url) whenever
    a fresh HTTP/HTTPS URL appears.

    The caller is responsible for routing on_new_url into the UI thread via
    _ui_queue.put() -- this class never touches Tkinter directly.
    """

    def __init__(self, get_clipboard: Callable[[], str],
                 on_new_url: Callable[[str], None]) -> None:
        self._get_clipboard = get_clipboard
        self._on_new_url = on_new_url
        self._lock = threading.Lock()
        self._last_url: Optional[str] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True,
                             name="clipboard-monitor")
        self._thread = t
        t.start()
        logger.debug("ClipboardMonitor started")

    def stop(self) -> None:
        with self._lock:
            self._running = False
        logger.debug("ClipboardMonitor stopped")

    def reset_last(self) -> None:
        """Clear last seen URL so the next identical clipboard read fires again."""
        with self._lock:
            self._last_url = None

    def _poll_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                text = self._get_clipboard()
                url = _extract_url(text.strip())
                if url:
                    with self._lock:
                        if url != self._last_url:
                            self._last_url = url
                            fire = True
                        else:
                            fire = False
                    if fire:
                        try:
                            self._on_new_url(url)
                        except Exception as exc:
                            logger.debug("ClipboardMonitor callback error: %s", exc)
            except Exception:
                pass  # clipboard read errors are transient; keep polling
            time.sleep(_POLL_INTERVAL)
