"""Regression tests for the 2026-09-28 platform download audit (v20.3.10) - TikTok and live monitor."""

from __future__ import annotations


# BUG-MON-PARTIAL: a monitor recording cancelled from the queue (keep_partial)
# ends in PARTIAL_SAVED, which the refresh loop did not treat as terminal --
# the row stayed RECORDING forever and the profile watch never came back.
def _recording_monitor_item():
    from app.services.live_monitor_service import MonitorItem

    return MonitorItem(
        url="https://www.tiktok.com/@u/live",
        state="RECORDING",
        task_id="t1",
        is_profile_watch=True,
        profile_platform="tiktok",
        username="u",
        watch_url="https://www.tiktok.com/@u",
    )


def test_service_monitor_partial_saved_ends_recording():
    from unittest.mock import MagicMock

    from app.services.live_monitor_service import LiveMonitorService
    from domain.enums.download_status import DownloadStatus
    from tests.test_live_monitor_service import StubService, StubTask

    service = StubService()
    svc = LiveMonitorService(service, MagicMock(), broadcast=lambda ev, d: None)  # type: ignore[arg-type]
    rec = _recording_monitor_item()
    svc._items.append(rec)
    service.tasks["t1"] = StubTask("t1", status=DownloadStatus.PARTIAL_SAVED, filename="/x/part.ts")

    svc._refresh_recording_items()

    assert rec.state == "ENDED"
    assert rec.filename == "/x/part.ts"
    assert any(i.state == "WAITING" and i.username == "u" for i in svc._items)


def test_desktop_monitor_partial_saved_ends_recording():
    import types

    from domain.enums.download_status import DownloadStatus
    from tests.test_live_monitor_audit import _make_tab_stub
    from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem, _MonitorState

    item = _MonitorItem(
        url="https://www.tiktok.com/@u/live",
        is_profile_watch=True,
        profile_platform="tiktok",
        username="u",
        watch_url="https://www.tiktok.com/@u",
    )
    item.state = _MonitorState.RECORDING
    item.task_id = "t1"
    tab = _make_tab_stub()
    tab._refresh_item_ui = lambda i: None
    snap = {"status": DownloadStatus.PARTIAL_SAVED, "filename": "/x/part.ts"}
    task = types.SimpleNamespace(snapshot=lambda: snap)
    tab._app.service = types.SimpleNamespace(get_task=lambda tid: task)
    tab._respawn_watch = lambda fin: LiveMonitorTab._respawn_watch(tab, fin)
    tab._items = [item]

    LiveMonitorTab._refresh_recording_items(tab)

    assert item.state == _MonitorState.ENDED
    assert item.filename == "/x/part.ts"
    assert any(i.state == _MonitorState.WAITING for i in tab._items)


# BUG-DUP-CANONICAL: the task is stored under the canonical URL from analyse
# (@user/live) while the duplicate guard compared the short link the client
# sent, so posting the same vt.tiktok.com link twice started a second recording.
def test_duplicate_guard_matches_canonical_task_url(tmp_path):
    from unittest.mock import MagicMock

    from domain.enums.download_status import DownloadStatus
    from domain.models.download_task import MediaInfo
    from tests.test_download_service import make_service

    service, mocks = make_service(download_dir=tmp_path)
    existing = MagicMock()
    existing.url = "https://www.tiktok.com/@someuser/live"
    existing.status = DownloadStatus.DOWNLOADING
    mocks["manager"].get_all_tasks.return_value = [existing]

    result = service.start_download(
        url="https://vt.tiktok.com/ZSabc123/",
        media_info=MediaInfo(url="https://www.tiktok.com/@someuser/live", title="live", is_live=True),
        format_id="best",
        output_ext="mp4",
    )

    assert result is existing
    mocks["manager"].enqueue.assert_not_called()
