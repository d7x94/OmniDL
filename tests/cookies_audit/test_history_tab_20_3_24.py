"""Regression guards for the tab-history audit (v20.3.24)."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

    _QT = QApplication.instance() is not None or QApplication([]) is not None
except Exception:  # pragma: no cover
    _QT = False

from domain.models.download_task import DownloadTask
from infrastructure.storage.history_repository import HistoryRepository


def _entry(i, **over):
    base = {
        "id": f"t{i}",
        "url": f"https://example.com/{i}",
        "title": f"Video {i}",
        "platform": "youtube",
        "filename": "",
        "output_dir": "",
        "status": "COMPLETED",
        "downloaded_bytes": 1024,
        "finished_at": 1_700_000_000 + i,
        "is_live": False,
    }
    base.update(over)
    return base


class _Svc:
    def __init__(self, entries):
        self.entries = entries
        self.cleared_with = "not called"

    def get_history(self):
        return [dict(e) for e in self.entries]

    def search_history(self, q):
        return self.get_history()

    def clear_history(self, exclude_ids=None):
        self.cleared_with = exclude_ids
        self.entries = []


class _App:
    def __init__(self, service):
        self.service = service


@pytest.fixture
def tabs():
    # Parentless tabs left to PySide's exit cleanup crash the interpreter (exit 139).
    made = []
    yield made
    for tab in made:
        tab.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.skipif(not _QT, reason="requires a real PySide6 build")
def test_clear_all_through_service_facade(monkeypatch, tabs):
    # Importing yt_dlp spawns a process (blocked here); stub the two service
    # modules ui.main_window pulls in, and drop the module again on teardown.
    for name, cls in (
        ("app.services.download_service", "DownloadService"),
        ("app.services.taildrop_service", "TaildropService"),
    ):
        mod = types.ModuleType(name)
        setattr(mod, cls, object)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "ui.main_window", raising=False)
    from ui.main_window import ServiceFacade
    from ui.tabs.history_tab import HistoryTab

    svc = _Svc([_entry(0), _entry(1)])
    fake_api = types.ModuleType("api.server")
    fake_api.tasks_with_active_convert = lambda: frozenset({"t-conv"})
    monkeypatch.setitem(sys.modules, "api.server", fake_api)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    tab = HistoryTab(_App(ServiceFacade(svc, None)))
    tabs.append(tab)
    tab.refresh()
    QTest.mouseClick(tab._clear_btn, Qt.MouseButton.LeftButton)

    assert svc.cleared_with == frozenset({"t-conv"})
    assert svc.entries == []

    # Same facade gap: Live Monitor calls check_facebook_profile_live for Facebook profile watches.
    calls = []
    svc.check_facebook_profile_live = lambda **kw: calls.append(kw)
    ServiceFacade(svc, None).check_facebook_profile_live(url="u", on_done=print, on_error=print)
    assert calls == [{"url": "u", "on_done": print, "on_error": print}]


@pytest.mark.skipif(not _QT, reason="requires a real PySide6 build")
def test_partial_saved_badge_and_filter(tabs):
    from ui.tabs.history_tab import HistoryTab
    from utils.i18n import t

    svc = _Svc([_entry(0, status="PARTIAL_SAVED"), _entry(1)])
    tab = HistoryTab(_App(svc))
    tabs.append(tab)
    tab.refresh()
    texts = [lbl.text() for lbl in tab._scroll_content.findChildren(QLabel)]
    assert t("item.status.partial") in texts
    assert "PARTIAL_SAVED" not in texts

    idx = tab._filter_combo.findData("PARTIAL_SAVED")
    assert idx >= 0
    tab._filter_combo.setCurrentIndex(idx)
    tab.refresh()
    assert [e["id"] for e in tab._entries] == ["t0"]


def test_remove_keeps_entry_when_rewrite_fails(tmp_path, monkeypatch):
    path = tmp_path / "download_history.jsonl"
    repo = HistoryRepository(path, limit=10)
    task = DownloadTask(url="https://example.com/a")
    repo.add(task)

    def _fail(self, target):
        raise OSError("disk locked")

    monkeypatch.setattr(Path, "replace", _fail)
    repo.remove(task.id)
    monkeypatch.undo()

    assert [e["id"] for e in repo.all()] == [task.id]
    assert [e["id"] for e in HistoryRepository(path, limit=10).all()] == [task.id]
