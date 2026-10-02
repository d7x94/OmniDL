"""Tests for the v20.3.14 button audit fixes (fix/audit-buttons-20.3.14).

Each class targets one fix from the audit. Qt-free stubs, same pattern as
test_live_monitor_audit.py — methods called unbound with a SimpleNamespace
standing in for self.
"""

from __future__ import annotations

import types
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fix 1 — live_monitor_tab: _remove_item / _clear_all must keep_partial=True
# before cancelling a task, same as _cancel_item already does.
# ---------------------------------------------------------------------------


class _StubTask:
    def __init__(self):
        self.keep_partial = False


class _StubService:
    def __init__(self):
        self.cancelled = []
        self.tasks = {}

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def cancel_download(self, task_id):
        self.cancelled.append(task_id)


def _make_monitor_tab_stub():
    tab = types.SimpleNamespace()
    tab._app = types.SimpleNamespace(service=_StubService())
    tab._checking_item = None
    tab._items = []
    tab._detach_row = lambda item: None
    tab._update_empty_state = lambda: None
    tab._update_status = lambda: None
    return tab


def _recording_item(task_id="t1"):
    from ui.tabs.live_monitor_tab import _MonitorItem, _MonitorState

    item = _MonitorItem(url="https://www.tiktok.com/@u/live")
    item.task_id = task_id
    item.state = _MonitorState.RECORDING
    return item


class TestLiveMonitorKeepPartialOnDiscard:
    def test_remove_item_keeps_partial_file(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        tab = _make_monitor_tab_stub()
        item = _recording_item()
        task = _StubTask()
        tab._app.service.tasks["t1"] = task
        tab._items = [item]

        LiveMonitorTab._remove_item(tab, item)

        assert task.keep_partial is True
        assert tab._app.service.cancelled == ["t1"]

    def test_clear_all_keeps_partial_file(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        tab = _make_monitor_tab_stub()
        item = _recording_item()
        task = _StubTask()
        tab._app.service.tasks["t1"] = task
        tab._items = [item]

        LiveMonitorTab._clear_all(tab)

        assert task.keep_partial is True
        assert tab._app.service.cancelled == ["t1"]


# ---------------------------------------------------------------------------
# Fix 2 — live_monitor_tab: _force_check_now must reset item.url back to
# watch_url (BUG-MON-URL), same as _cancel_item already does.
# ---------------------------------------------------------------------------


class TestForceCheckNowResetsUrl:
    def test_error_row_url_reset_to_watch_url(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem, _MonitorState

        item = _MonitorItem(
            url="https://www.tiktok.com/@u/video/123",
            watch_url="https://www.tiktok.com/@u",
        )
        item.state = _MonitorState.ERROR

        tab = types.SimpleNamespace()
        tab._refresh_item_ui = lambda item: None
        tab._checking_item = None
        tab._paused = False
        tab._trigger_check = lambda item: None
        tab._items = [item]
        tab._app = types.SimpleNamespace(toast=lambda *a, **k: None)

        LiveMonitorTab._force_check_now(tab, item)

        assert item.url == "https://www.tiktok.com/@u"
        assert item.state == _MonitorState.WAITING


# ---------------------------------------------------------------------------
# Fix 3 — toolbar: _start_analyse must not launch a second analyse while one
# is already running (Enter / Paste bypass the disabled Analyse button).
# ---------------------------------------------------------------------------


class TestToolbarStartAnalyseGuard:
    def _tb(self, calls):
        tb = types.SimpleNamespace()
        tb._analysing = True
        tb._current_cancel = "old-event"
        tb._current_analysing_url = "https://example.com/v"
        tb._analyse_token = 1
        tb._analyse_btn = types.SimpleNamespace(setText=lambda v: None, setEnabled=lambda v: None)
        tb._stop_btn = types.SimpleNamespace(show=lambda: None)
        tb._set_status = lambda *a: None
        tb._spinner_timer = types.SimpleNamespace(start=lambda: None)
        tb._app = types.SimpleNamespace(
            service=types.SimpleNamespace(analyse_url=lambda **kw: calls.append(kw)),
            navigate_to=lambda *a: None,
            get_tab=lambda *a: None,
        )
        return tb

    def test_same_url_while_analysing_is_a_noop(self):
        from ui.components.toolbar import Toolbar

        calls = []
        tb = self._tb(calls)
        tb.get_url = lambda: "https://example.com/v"

        Toolbar._start_analyse(tb)

        assert calls == []
        assert tb._current_cancel == "old-event"
        assert tb._analyse_token == 1

    def test_different_url_while_analysing_cancels_and_restarts(self):
        from ui.components.toolbar import Toolbar

        calls = []
        tb = self._tb(calls)
        tb.get_url = lambda: "https://example.com/other"
        cancelled = []
        tb._current_cancel = types.SimpleNamespace(set=lambda: cancelled.append(True))

        Toolbar._start_analyse(tb)

        assert cancelled == [True]
        assert tb._current_analysing_url == "https://example.com/other"
        assert tb._analyse_token == 2
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# Fix 4 — toolbar: _cancel_analyse must clear HomeTab's loading state, or
# Stop leaves HomeTab stuck showing the loading view.
# ---------------------------------------------------------------------------


class TestToolbarCancelAnalyseClearsHome:
    def test_cancel_analyse_calls_home_clear_result(self):
        from ui.components.toolbar import Toolbar

        home = types.SimpleNamespace(cleared=False)
        home.clear_result = lambda: setattr(home, "cleared", True)

        tb = types.SimpleNamespace()
        tb._current_cancel = None
        tb._analyse_token = 0
        tb._set_status = lambda *a: None
        tb._reset_btn = lambda: None
        tb._app = types.SimpleNamespace(get_tab=lambda name: home if name == "home" else None)

        Toolbar._cancel_analyse(tb)

        assert home.cleared is True


# ---------------------------------------------------------------------------
# Fix 5 — queue_tab: after a successful rename, _on_rename must drop the
# DownloadItemWidget's stale _completed_path snapshot so Open/Preview/
# Convert/Edit/Send pick up the renamed file.
# ---------------------------------------------------------------------------


class TestQueueTabRenameRefreshesCompletedPath:
    def test_on_rename_clears_stale_completed_path(self):
        from ui.tabs.queue_tab import QueueTab

        old_path = "/downloads/old_name.mp4"
        new_path = "/downloads/new_name.mp4"

        widget = types.SimpleNamespace()
        widget._completed_path = old_path
        refreshed_with = []
        widget.refresh = lambda task: refreshed_with.append((widget._completed_path, task.filename))

        task = types.SimpleNamespace(filename=new_path)
        service = types.SimpleNamespace(
            rename_download=lambda tid, name: new_path,
            get_task=lambda tid: task,
        )

        qt = types.SimpleNamespace()
        qt._app = types.SimpleNamespace(service=service)
        qt._widgets = {"t1": widget}

        with patch(
            "ui.tabs.queue_tab.QInputDialog.getText",
            return_value=("new_name.mp4", True),
        ):
            QueueTab._on_rename(qt, "t1", old_path)

        assert refreshed_with == [("", new_path)]


# ---------------------------------------------------------------------------
# Fix 6 — batch_tab: _retry_errors must also move any still-ANALYSING row
# back to PENDING, or a stale callback leaves it stuck forever.
# ---------------------------------------------------------------------------


class TestBatchRetryErrorsResetsAnalysing:
    def test_retry_resets_analysing_row_to_pending(self):
        from ui.tabs.batch_tab import BatchTab, _ItemState

        analysing_item = types.SimpleNamespace(state=_ItemState.ANALYSING)
        error_item = types.SimpleNamespace(state=_ItemState.ERROR, error_msg="boom", checked=False)

        bt = types.SimpleNamespace()
        bt._items = [analysing_item, error_item]
        bt._batch_token = 0
        bt._stop_seq_timer = lambda: None
        bt._seq_queue = types.SimpleNamespace(clear=lambda: None)
        bt._refresh_item_ui = lambda item: None
        bt._analysing_count = 1
        bt._retry_btn = types.SimpleNamespace(setEnabled=lambda v: None, setText=lambda v: None)
        bt._analyse_btn = types.SimpleNamespace(setEnabled=lambda v: None, setText=lambda v: None)
        bt._cancel_btn = types.SimpleNamespace(setVisible=lambda v: None)
        bt._queue_all_btn = types.SimpleNamespace(setEnabled=lambda v: None)
        bt._set_status = lambda *a: None
        bt._analyse_next = lambda token: None

        BatchTab._retry_errors(bt)

        assert analysing_item.state == _ItemState.PENDING
        assert error_item.state == _ItemState.PENDING


# ---------------------------------------------------------------------------
# Fix 7 — batch_tab: _update_queue_btn_count must not re-enable Queue All
# while an analyse pass or a sequential run is in progress.
# ---------------------------------------------------------------------------


class TestBatchQueueAllStaysDisabledDuringRun:
    def test_disabled_while_analysing(self):
        from ui.tabs.batch_tab import BatchTab, _ItemState

        ready_item = types.SimpleNamespace(state=_ItemState.READY, checked=True)
        bt = types.SimpleNamespace()
        bt._items = [ready_item]
        bt._analysing_count = 1
        bt._seq_queue = []
        calls = {"enabled": []}
        bt._queue_all_btn = types.SimpleNamespace(
            setEnabled=lambda v: calls["enabled"].append(v),
            setText=lambda v: None,
        )

        BatchTab._update_queue_btn_count(bt)

        assert calls["enabled"] == []

    def test_disabled_during_sequential_run(self):
        from ui.tabs.batch_tab import BatchTab, _ItemState

        ready_item = types.SimpleNamespace(state=_ItemState.READY, checked=True)
        bt = types.SimpleNamespace()
        bt._items = [ready_item]
        bt._analysing_count = 0
        bt._seq_queue = [ready_item]
        calls = {"enabled": []}
        bt._queue_all_btn = types.SimpleNamespace(
            setEnabled=lambda v: calls["enabled"].append(v),
            setText=lambda v: None,
        )

        BatchTab._update_queue_btn_count(bt)

        assert calls["enabled"] == []

    def test_enabled_when_idle(self):
        from ui.tabs.batch_tab import BatchTab, _ItemState

        ready_item = types.SimpleNamespace(state=_ItemState.READY, checked=True)
        bt = types.SimpleNamespace()
        bt._items = [ready_item]
        bt._analysing_count = 0
        bt._seq_queue = []
        calls = {"enabled": []}
        bt._queue_all_btn = types.SimpleNamespace(
            setEnabled=lambda v: calls["enabled"].append(v),
            setText=lambda v: None,
        )

        BatchTab._update_queue_btn_count(bt)

        assert calls["enabled"] == [True]


# ---------------------------------------------------------------------------
# Fix 8 — home_tab: refresh() must keep showing the user's custom output
# folder instead of overwriting the label with the default download dir.
# ---------------------------------------------------------------------------


class TestHomeTabRefreshKeepsCustomDir:
    def test_refresh_shows_custom_dir_when_set(self):
        from pathlib import Path

        from ui.tabs.home_tab import HomeTab

        ht = types.SimpleNamespace()
        ht._custom_output_dir = Path("/custom/pick")
        ht._app = types.SimpleNamespace(
            service=types.SimpleNamespace(get_download_dir=lambda: Path("/default/downloads"))
        )
        ht._short_path = lambda p: str(p)
        shown = []
        ht._folder_lbl = types.SimpleNamespace(setText=lambda v: shown.append(v))

        HomeTab.refresh(ht)

        assert shown == ["/custom/pick"]

    def test_refresh_shows_default_dir_when_no_custom(self):
        from pathlib import Path

        from ui.tabs.home_tab import HomeTab

        ht = types.SimpleNamespace()
        ht._custom_output_dir = None
        ht._app = types.SimpleNamespace(
            service=types.SimpleNamespace(get_download_dir=lambda: Path("/default/downloads"))
        )
        ht._short_path = lambda p: str(p)
        shown = []
        ht._folder_lbl = types.SimpleNamespace(setText=lambda v: shown.append(v))

        HomeTab.refresh(ht)

        assert shown == ["/default/downloads"]


# ---------------------------------------------------------------------------
# Fix 7 (021026 audit) — queue_tab: Clear must not drop a task that still
# has an active (PENDING/CONVERTING) remote-convert job, or the FFmpeg job
# is orphaned with no queue entry left to show/cancel it.
# ---------------------------------------------------------------------------


class TestQueueClearExcludesActiveConvertTasks:
    def test_clear_finished_passes_exclude_ids(self):
        from ui.tabs.queue_tab import QueueTab

        calls = []
        service = types.SimpleNamespace(
            clear_finished=lambda exclude_ids=None: calls.append(("finished", exclude_ids)),
        )
        qt = types.SimpleNamespace()
        qt._app = types.SimpleNamespace(service=service)
        qt._select_mode = False
        qt._selected_ids = set()

        with patch(
            "api.server.tasks_with_active_convert",
            return_value=frozenset({"t-converting"}),
        ):
            QueueTab._clear_finished(qt)

        assert calls == [("finished", frozenset({"t-converting"}))]

    def test_clear_selected_skips_active_convert_task(self):
        from ui.tabs.queue_tab import QueueTab

        calls = []
        service = types.SimpleNamespace(
            clear_specific=lambda ids: calls.append(ids),
        )
        qt = types.SimpleNamespace()
        qt._app = types.SimpleNamespace(service=service)
        qt._select_mode = True
        qt._selected_ids = {"t-done", "t-converting"}
        qt._update_clear_btn_label = lambda: None

        with patch(
            "api.server.tasks_with_active_convert",
            return_value=frozenset({"t-converting"}),
        ):
            QueueTab._clear_finished(qt)

        assert calls == [["t-done"]]

    def test_tasks_with_active_convert_empty_when_api_not_running(self):
        from api.server import tasks_with_active_convert

        assert tasks_with_active_convert() == frozenset()
