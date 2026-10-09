"""Regression guards for the tab-history audit (v20.3.21)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QPushButton

    _QT = QApplication.instance() is not None or QApplication([]) is not None
except Exception:  # pragma: no cover
    _QT = False

pytestmark = pytest.mark.skipif(not _QT, reason="requires a real PySide6 build")


class _Svc:
    def __init__(self, entries):
        self.entries = entries

    def get_history(self):
        return [dict(e) for e in self.entries]

    def search_history(self, q):
        return self.get_history()

    def delete_history_entry(self, task_id):
        self.entries = [e for e in self.entries if e["id"] != task_id]


class _App:
    def __init__(self, entries):
        self.service = _Svc(entries)


def _entry(i, **over):
    base = {
        "id": f"t{i}",
        "url": f"https://example.com/{i}",
        "title": f"Video {i}",
        "platform": "youtube",
        "filename": "",
        "output_dir": "/tmp",
        "status": "COMPLETED",
        "downloaded_bytes": 1024,
        "finished_at": 1_700_000_000 + i,
        "is_live": False,
    }
    base.update(over)
    return base


def _btns(tab, prefix):
    return [b for b in tab._scroll_content.findChildren(QPushButton) if b.text().startswith(prefix)]


def test_theme_switch_restyles_history_tab():
    from ui.tabs.history_tab import HistoryTab
    from ui.themes.tokens import T

    start = T.mode
    other = "light" if start != "light" else "dark"
    tab = HistoryTab(_App([_entry(0)]))
    tab.refresh()
    try:
        T.set_mode(other)
        assert T.error_bg in tab._clear_btn.styleSheet()
        card = tab._items_layout.itemAt(0).widget()
        assert T.surface in card.styleSheet()
    finally:
        T.set_mode(start)
