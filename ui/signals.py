"""Centralized Qt signals for cross-thread UI updates.

Usage from worker threads:
    from ui.signals import ui_bridge
    ui_bridge.post(lambda: widget.setText("hello"))
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal


class _UIBridge(QObject):
    """Routes callables from background threads to the Qt main thread event loop."""

    _call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._exec, Qt.ConnectionType.QueuedConnection)

    def _exec(self, fn) -> None:
        try:
            fn()
        except Exception:
            pass

    def post(self, fn) -> None:
        """Schedule *fn* to run on the main thread (safe to call from any thread)."""
        self._call.emit(fn)


ui_bridge = _UIBridge()


class Signals(QObject):
    """Global Qt signals for UI events and updates."""

    notification_added = Signal(str, str, str)  # type (success/error/info), title, detail
    file_dropped = Signal(str)  # url/path that was dropped onto the window
    badge_updated = Signal(str, int)  # tab_key, count (0 = remove badge)


signals = Signals()
