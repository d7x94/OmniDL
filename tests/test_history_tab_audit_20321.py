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


class _FakeTokens:
    def __init__(self, color):
        self.color = color
        self.callbacks = []

    def register(self, cb):
        self.callbacks.append(cb)

    def unregister(self, cb):
        pass

    def __getattr__(self, name):
        return self.color


def test_theme_switch_restyles_history_tab(monkeypatch):
    import ui.tabs.history_tab as H

    fake = _FakeTokens("#AAAAAA")
    monkeypatch.setattr(H, "T", fake)
    tab = H.HistoryTab(_App([_entry(0)]))
    tab.refresh()
    assert "#AAAAAA" in tab._clear_btn.styleSheet()

    fake.color = "#BBBBBB"
    for cb in fake.callbacks:
        cb()
    assert "#BBBBBB" in tab._clear_btn.styleSheet()
    card = tab._items_layout.itemAt(0).widget()
    assert "#BBBBBB" in card.styleSheet()


def test_rename_hidden_for_relative_dir_under_output_dir(tmp_path, monkeypatch):
    from ui.tabs.history_tab import HistoryTab

    (tmp_path / "gallery").mkdir()
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    tab = HistoryTab(_App([_entry(0, filename="gallery", output_dir=str(tmp_path))]))
    tab.refresh()
    assert not _btns(tab, "Rename")
