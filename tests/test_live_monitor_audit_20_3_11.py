"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - live monitor."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.services.live_monitor_service import LiveMonitorService, MonitorItem
from domain.models.download_task import MediaInfo

_WATCH = "https://www.facebook.com/somepage"
_LIVE = "https://www.facebook.com/somepage/videos/1234567890"


def _fb_watch():
    service = MagicMock()
    svc = LiveMonitorService(service, MagicMock(), broadcast=lambda ev, data: None)  # type: ignore[arg-type]
    item = MonitorItem(
        url=_LIVE, is_profile_watch=True, profile_platform="facebook", username="somepage", watch_url=_WATCH
    )
    svc._items.append(item)
    return svc, service, item


# BUG-MON-URL: a profile watch swaps item.url for the live-video URL when the
# page goes live.  Back in WAITING that URL fed the next poll, and a Facebook
# /videos/<id> URL has no username, so the watch failed "no username" 8 times.
def test_profile_watch_gets_its_page_url_back_when_the_live_is_not_live():
    svc, _service, item = _fb_watch()

    svc._on_live_url_analysed(item, MediaInfo(url=_LIVE, title="x", is_live=False), _LIVE)

    assert item.url == _WATCH


def test_profile_watch_gets_its_page_url_back_after_cancel():
    svc, service, item = _fb_watch()
    item.task_id = "task1"

    assert svc.cancel(item.id) is True

    service.cancel_download.assert_called_once_with("task1")
    assert item.url == _WATCH


def _fb_watch_ui():
    import types

    from tests.test_live_monitor_audit import _make_tab_stub
    from ui.tabs.live_monitor_tab import _MonitorItem

    item = _MonitorItem(
        url=_LIVE, is_profile_watch=True, profile_platform="facebook", username="somepage", watch_url=_WATCH
    )
    tab = _make_tab_stub()
    tab._refresh_item_ui = lambda i: None
    tab._items = [item]
    tab._app.service = types.SimpleNamespace(get_task=lambda tid: None, cancel_download=lambda tid: None)
    return tab, item


def test_ui_profile_watch_gets_its_page_url_back_when_the_live_is_not_live():
    from ui.tabs.live_monitor_tab import LiveMonitorTab

    tab, item = _fb_watch_ui()

    LiveMonitorTab._on_live_url_analysed(tab, item, MediaInfo(url=_LIVE, title="x", is_live=False), _LIVE)

    assert item.url == _WATCH


def test_ui_profile_watch_gets_its_page_url_back_after_cancel():
    from ui.tabs.live_monitor_tab import LiveMonitorTab

    tab, item = _fb_watch_ui()
    item.task_id = "task1"

    LiveMonitorTab._cancel_item(tab, item)

    assert item.url == _WATCH
