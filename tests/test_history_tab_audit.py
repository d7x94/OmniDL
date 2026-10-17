"""Regression guards for the History tab / history storage audit (Aug 2026).

Each test pins one defect found while auditing every button on the desktop
History tab and the /api/history endpoints, so none of them can silently
come back.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from domain.models.download_task import DownloadTask
from infrastructure.storage.history_repository import HistoryRepository

# ── H-01 / H-02: on-disk ordering ────────────────────────────────────────────


def _add(repo: HistoryRepository, i: int) -> None:
    task = DownloadTask(url=f"https://example.com/{i}", output_dir="/tmp")
    task.finished_at = 1_700_000_000 + i
    repo.add(task)


def test_history_stays_newest_first_after_restart(tmp_path):
    """add() prepends in memory but appends to disk.

    Reading the JSONL back top-to-bottom used to yield OLDEST-first, so the
    History tab listed the oldest downloads at the top after every restart.
    """
    path = tmp_path / "history.jsonl"
    repo = HistoryRepository(path, limit=500)
    for i in range(5):
        _add(repo, i)

    in_session = [e["url"] for e in repo.all()]
    after_restart = [e["url"] for e in HistoryRepository(path, limit=500).all()]

    assert in_session[0].endswith("/4"), "add() must keep newest first"
    assert after_restart == in_session, "restart must not flip history order"


def test_history_trim_keeps_newest_not_oldest(tmp_path):
    """The over-limit trim keeps the FIRST *limit* entries.

    With an oldest-first load that erased the NEWEST records from disk
    permanently — the exact opposite of "prune oldest first".
    """
    path = tmp_path / "history.jsonl"
    repo = HistoryRepository(path, limit=500)
    for i in range(6):
        _add(repo, i)

    trimmed = HistoryRepository(path, limit=2).all()

    assert [e["url"] for e in trimmed] == [
        "https://example.com/5",
        "https://example.com/4",
    ]
    # The trim rewrites the file, so the loss would have been permanent.
    assert [e["url"] for e in HistoryRepository(path, limit=500).all()] == [
        "https://example.com/5",
        "https://example.com/4",
    ]


# ── H-03 / H-14: /api/history ────────────────────────────────────────────────


def test_api_history_does_not_reverse_an_already_newest_first_list():
    src = Path("api/server.py").read_text(encoding="utf-8")
    assert "list(reversed(service.get_history()))" not in src, (
        "HistoryRepository.all() is newest-first; reversing served page 1 "
        "of the web history as the oldest downloads"
    )


def test_api_history_search_survives_a_null_title():
    """A stored null title/filename used to make the q= filter raise a 500."""
    src = Path("api/server.py").read_text(encoding="utf-8")
    assert 'ql in (x.get("title") or "").lower()' in src
    assert 'ql in (x.get("filename") or "").lower()' in src
    assert 'ql in (x.get("url") or "").lower()' in src


# ── Desktop tab: needs a real QApplication ───────────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    _QT = QApplication.instance() is not None or QApplication([]) is not None
except Exception:  # pragma: no cover - headless CI without a real PySide6
    _QT = False

pytestmark_qt = pytest.mark.skipif(not _QT, reason="requires a real PySide6 build")


class _FakeToolbar:
    def __init__(self) -> None:
        self.url = None

    def set_url(self, url: str) -> None:
        self.url = url


class _FakeService:
    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries

    def get_history(self) -> list[dict]:
        return [dict(e) for e in self.entries]

    def search_history(self, q: str) -> list[dict]:
        return [dict(e) for e in self.entries if q.lower() in (e.get("title") or "").lower()]

    def delete_history_entry(self, task_id: str) -> None:
        self.entries = [e for e in self.entries if e["id"] != task_id]

    def clear_history(self) -> None:
        self.entries = []


class _FakeApp:
    def __init__(self, entries: list[dict]) -> None:
        self.service = _FakeService(entries)
        self._toolbar = _FakeToolbar()
        self.navigated: list[str] = []

    def get_toolbar(self):
        return self._toolbar

    def navigate_to(self, key: str) -> None:
        self.navigated.append(key)


def _entry(i: int, **over) -> dict:
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


def _flush() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _labels(tab) -> list[str]:
    return [x.text() for x in tab._scroll_content.findChildren(QLabel)]


def _buttons(tab, text: str) -> list:
    return [b for b in tab._scroll_content.findChildren(QPushButton) if b.text() == text]


@pytestmark_qt
def test_deleting_the_last_row_shows_the_empty_placeholder():
    """Deleting rows one by one used to leave a blank scroll area."""
    from ui.tabs.history_tab import HistoryTab

    app = _FakeApp([_entry(i) for i in range(3)])
    tab = HistoryTab(app)
    tab.refresh()

    for _ in range(3):
        _buttons(tab, "Delete")[0].click()
    _flush()

    assert tab._entries == []
    assert any("history" in lbl.lower() or lbl for lbl in _labels(tab)), (
        "an empty-state label must be shown once the last row is deleted"
    )
    assert _labels(tab), "the tab must not be left completely blank"


@pytestmark_qt
def test_folder_button_follows_a_rename(tmp_path, monkeypatch):
    """The Folder lambda captured the filename at build time.

    After Rename it pointed at a path that no longer existed, so Folder fell
    back to opening the parent directory instead of highlighting the file.
    """
    import ui.tabs.history_tab as H

    old = tmp_path / "old.mp4"
    old.write_bytes(b"x")
    new = tmp_path / "new.mp4"

    app = _FakeApp([_entry(0, filename=str(old), output_dir=str(tmp_path))])

    def _rename(task_id, name):
        old.rename(new)
        return str(new)

    app.service.rename_download = _rename

    tab = H.HistoryTab(app)
    tab.refresh()

    monkeypatch.setattr(H.QInputDialog, "getText", staticmethod(lambda *a, **k: ("new.mp4", True)))
    _buttons(tab, "Rename")[0].click()

    revealed = {}
    monkeypatch.setattr(H, "reveal_in_explorer", lambda p: revealed.setdefault("path", Path(p)) or True)
    monkeypatch.setattr(H, "open_folder", lambda p: revealed.setdefault("folder", Path(p)))
    _buttons(tab, "Folder")[0].click()

    assert revealed.get("path") == new.resolve(), (
        "Folder must reveal the renamed file, not fall back to the parent dir"
    )


@pytestmark_qt
def test_relative_filename_resolves_against_output_dir(tmp_path):
    """Path(raw).resolve() anchored a relative filename to the process CWD."""
    from ui.tabs.history_tab import HistoryTab

    (tmp_path / "clip.mp4").write_bytes(b"x")
    app = _FakeApp([_entry(0, filename="clip.mp4", output_dir=str(tmp_path))])
    tab = HistoryTab(app)
    tab.refresh()

    assert tab._entry_path("t0") == (tmp_path / "clip.mp4").resolve()


@pytestmark_qt
def test_entry_path_is_none_without_a_file():
    from ui.tabs.history_tab import HistoryTab

    app = _FakeApp([_entry(0)])
    tab = HistoryTab(app)
    tab.refresh()

    assert tab._entry_path("t0") is None
    assert tab._entry_path("missing") is None
