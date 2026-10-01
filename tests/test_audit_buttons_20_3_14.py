"""Tests for the v20.3.14 button audit fixes (fix/audit-buttons-20.3.14).

Each class targets one fix from the audit. Qt-free stubs, same pattern as
test_live_monitor_audit.py — methods called unbound with a SimpleNamespace
standing in for self.
"""

from __future__ import annotations

import types

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

        tab = types.SimpleNamespace()
        tab._refresh_item_ui = lambda item: None
        tab._checking_item = None
        tab._paused = False
        tab._trigger_check = lambda item: None
        item = _MonitorItem(
            url="https://www.tiktok.com/@u/video/123",
            watch_url="https://www.tiktok.com/@u",
        )
        item.state = _MonitorState.ERROR

        LiveMonitorTab._force_check_now(tab, item)

        assert item.url == "https://www.tiktok.com/@u"
        assert item.state == _MonitorState.WAITING


# ---------------------------------------------------------------------------
# Fix 3 — toolbar: _start_analyse must not launch a second analyse while one
# is already running (Enter / Paste bypass the disabled Analyse button).
# ---------------------------------------------------------------------------


class TestToolbarStartAnalyseGuard:
    def test_second_call_while_analysing_is_a_noop(self):
        from ui.components.toolbar import Toolbar

        calls = []
        tb = types.SimpleNamespace()
        tb._analysing = True
        tb._current_cancel = "old-event"
        tb._analyse_token = 1
        tb.get_url = lambda: "https://example.com/v"
        tb._app = types.SimpleNamespace(
            service=types.SimpleNamespace(analyse_url=lambda **kw: calls.append(kw)),
            navigate_to=lambda *a: None,
            get_tab=lambda *a: None,
        )

        Toolbar._start_analyse(tb)

        assert calls == []
        assert tb._current_cancel == "old-event"
        assert tb._analyse_token == 1


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
