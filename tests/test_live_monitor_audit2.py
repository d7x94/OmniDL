"""Regression tests for the second live-monitor audit (desktop + Remote API).

Each test pins one defect found in that audit:

L1  cancel() must not zero last_check      -> stopped recording restarted in <=5s
L2  check_now() must not zero last_check   -> in-flight check killed by stuck-recovery
L5  the URL cap must count active items    -> profile watch died after ~20 recordings
L6  no monitor_update while still recording-> SSE noise, one re-render per client per poll
L7  desktop platform badge falls back to profile_platform (parity with to_dict())
"""

from __future__ import annotations

import time as _time
from unittest.mock import MagicMock

from app.services import live_monitor_service as lms
from app.services.live_monitor_service import LiveMonitorService, MonitorItem
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import MediaInfo
from tests.test_live_monitor_service import StubService, StubTask


def _make(service: StubService):
    events: list[tuple[str, dict]] = []
    svc = LiveMonitorService(service, MagicMock(), broadcast=lambda ev, d: events.append((ev, d)))  # type: ignore[arg-type]
    return svc, events


# ── L1: cancel keeps the watch but must not re-record immediately ──────────


def test_cancel_does_not_make_item_immediately_due():
    service = StubService()
    svc, _ = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="task9")
    svc._items.append(item)

    before = _time.time()
    assert svc.cancel(item.id) is True
    assert item.last_check >= before  # not 0.0 -> not due for a full interval

    svc._enqueue_next_check()
    assert svc._checking_item is None
    assert item.state == "WAITING"


# ── L2: check_now must not kill an in-flight check ─────────────────────────


def test_check_now_while_checking_does_not_trip_stuck_recovery():
    svc, _ = _make(StubService())
    item = MonitorItem(url="https://x", state="CHECKING", last_check=_time.time())
    svc._items.append(item)
    svc._checking_item = item

    assert svc.check_now(item.id) is True
    assert item.rate_limited_until == 0.0

    svc._recover_stuck_checks()
    assert item.state == "CHECKING"  # check still in flight
    assert item.consecutive_failures == 0
    assert svc._checking_item is item


def test_check_now_while_live_does_not_trip_stuck_recovery():
    svc, _ = _make(StubService())
    item = MonitorItem(url="https://x", state="LIVE", last_check=_time.time())
    svc._items.append(item)
    svc._checking_item = item

    assert svc.check_now(item.id) is True
    svc._recover_stuck_checks()
    assert item.state == "LIVE"
    assert item.consecutive_failures == 0


# ── L5: the cap counts watches, not history ────────────────────────────────


def test_cap_ignores_terminal_items(monkeypatch):
    monkeypatch.setattr(lms, "MAX_MONITOR_URLS", 2)
    svc, _ = _make(StubService())
    svc._items.append(MonitorItem(url="https://a", state="ENDED"))
    svc._items.append(MonitorItem(url="https://b", state="ERROR"))

    svc.add_url("https://www.youtube.com/watch?v=c")
    svc.add_url("https://www.youtube.com/watch?v=d")
    assert svc._active_count() == 2


def test_respawn_survives_a_backlog_of_ended_rows(monkeypatch):
    monkeypatch.setattr(lms, "MAX_MONITOR_URLS", 3)
    service = StubService()
    svc, _ = _make(service)
    for n in range(5):
        svc._items.append(MonitorItem(url=f"https://old{n}", state="ENDED"))

    rec = MonitorItem(
        url="https://www.tiktok.com/@u/live",
        state="RECORDING",
        task_id="t1",
        is_profile_watch=True,
        profile_platform="tiktok",
        username="u",
        watch_url="https://www.tiktok.com/@u",
    )
    svc._items.append(rec)
    service.tasks["t1"] = StubTask("t1", status=DownloadStatus.COMPLETED, filename="/x/y.ts")

    svc._refresh_recording_items()

    assert rec.state == "ENDED"
    assert any(i.state == "WAITING" and i.username == "u" for i in svc._items)


# ── L6: no broadcast while a recording is merely progressing ───────────────


def test_no_monitor_update_while_still_downloading():
    service = StubService()
    svc, events = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="t1")
    svc._items.append(item)
    service.tasks["t1"] = StubTask("t1", status=DownloadStatus.DOWNLOADING)

    svc._refresh_recording_items()

    assert events == []
    assert item.state == "RECORDING"


def test_monitor_update_still_emitted_on_completion():
    service = StubService()
    svc, events = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="t1")
    svc._items.append(item)
    service.tasks["t1"] = StubTask("t1", status=DownloadStatus.COMPLETED, filename="/x/y.ts")

    svc._refresh_recording_items()

    assert [ev for ev, _ in events] == ["monitor_update"]
    assert item.state == "ENDED"
    assert item.filename == "/x/y.ts"


# ── L7 / L5 desktop parity (pure logic, no Qt widgets instantiated) ────────


def test_desktop_platform_badge_falls_back_to_profile_platform():
    from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem

    item = _MonitorItem(
        url="https://www.tiktok.com/@u",
        is_profile_watch=True,
        profile_platform="tiktok",
        username="u",
    )
    item.row_frame = MagicMock()
    item.platform_lbl = MagicMock()
    for attr in (
        "state_lbl",
        "title_lbl",
        "progress_lbl",
        "pause_btn",
        "cancel_btn",
        "check_now_btn",
        "open_folder_btn",
        "send_to_conv_btn",
        "cookie_warn",
    ):
        setattr(item, attr, None)

    tab = MagicMock()
    LiveMonitorTab._refresh_item_ui(tab, item)

    item.platform_lbl.setText.assert_called_once_with("  tiktok  ")
    item.platform_lbl.show.assert_called_once()


def test_desktop_active_count_ignores_terminal_items():
    from ui.tabs.live_monitor_tab import _active_count, _MonitorItem, _MonitorState

    items = [
        _MonitorItem(url="a", state=_MonitorState.ENDED),
        _MonitorItem(url="b", state=_MonitorState.ERROR),
        _MonitorItem(url="c", state=_MonitorState.WAITING),
        _MonitorItem(url="d", state=_MonitorState.RECORDING),
    ]
    assert _active_count(items) == 2


def test_direct_url_item_has_no_platform_badge():
    from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem

    item = _MonitorItem(url="https://example.com/stream")
    item.row_frame = MagicMock()
    item.platform_lbl = MagicMock()
    for attr in (
        "state_lbl",
        "title_lbl",
        "progress_lbl",
        "pause_btn",
        "cancel_btn",
        "check_now_btn",
        "open_folder_btn",
        "send_to_conv_btn",
        "cookie_warn",
    ):
        setattr(item, attr, None)

    LiveMonitorTab._refresh_item_ui(MagicMock(), item)

    item.platform_lbl.hide.assert_called_once()
    item.platform_lbl.setText.assert_not_called()


def test_media_info_platform_still_wins():
    from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem

    item = _MonitorItem(url="https://x", profile_platform="tiktok")
    item.media_info = MediaInfo(url="https://x", platform="instagram", title="Unknown")
    item.row_frame = MagicMock()
    item.platform_lbl = MagicMock()
    for attr in (
        "state_lbl",
        "title_lbl",
        "progress_lbl",
        "pause_btn",
        "cancel_btn",
        "check_now_btn",
        "open_folder_btn",
        "send_to_conv_btn",
        "cookie_warn",
    ):
        setattr(item, attr, None)

    LiveMonitorTab._refresh_item_ui(MagicMock(), item)

    item.platform_lbl.setText.assert_called_once_with("  instagram  ")
