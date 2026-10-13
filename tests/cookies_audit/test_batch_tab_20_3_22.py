"""Batch tab audit (v20.3.22): real BatchTab widget, offscreen, mocked service."""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from domain.models.download_task import MediaInfo
from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState
from utils.i18n import t


@pytest.fixture
def tab():
    QApplication.instance() or QApplication([])
    app = MagicMock()
    app.config.default_format = "mp4"
    w = BatchTab(app)
    yield w
    w._clear_all()
    w.deleteLater()


def _load(tab, states):
    """Put rows in the given states and finish the pass, as a real run would."""
    tab._batch_token += 1
    tab._items = []
    for n, st in enumerate(states):
        item = _BatchItem(url=f"https://example.com/{n}", state=st)
        if st == _ItemState.READY:
            item.media_info = MediaInfo(url=item.url, title=f"v{n}", platform="x")
        else:
            item.checked = False
        tab._items.append(item)
    tab._rebuild_results_ui()
    tab._on_batch_complete(tab._batch_token)
    return list(tab._items)


def test_remove_error_rows_disables_retry(tab):
    items = _load(tab, [_ItemState.READY, _ItemState.ERROR, _ItemState.ERROR])
    assert tab._retry_btn.isEnabled()
    QTest.mouseClick(items[1].remove_btn, Qt.MouseButton.LeftButton)
    assert tab._retry_btn.text() == t("batch.retry_count", count=1)
    QTest.mouseClick(items[2].remove_btn, Qt.MouseButton.LeftButton)
    assert not tab._retry_btn.isEnabled()
    assert tab._retry_btn.text() == t("batch.retry_errors")


def test_failed_submission_enables_retry(tab):
    _load(tab, [_ItemState.READY])
    tab._sequential_chk.setChecked(False)
    tab._app.service.start_download.side_effect = RuntimeError("boom")
    assert not tab._retry_btn.isEnabled()
    QTest.mouseClick(tab._queue_all_btn, Qt.MouseButton.LeftButton)
    assert tab._retry_btn.isEnabled()
    assert tab._retry_btn.text() == t("batch.retry_count", count=1)


def test_new_pass_resets_retry_label(tab):
    _load(tab, [_ItemState.ERROR, _ItemState.ERROR])
    tab._text_area.setPlainText("https://example.com/a")
    tab._get_analysis_delay = lambda url: 0.0
    tab._app.service.analyse_url = MagicMock()
    tab._start_batch_analyse()
    assert tab._retry_btn.text() == t("batch.retry_errors")


def test_retried_row_checkbox_matches_model(tab):
    items = _load(tab, [_ItemState.READY, _ItemState.ERROR])
    tab._get_analysis_delay = lambda url: 0.0
    tab._app.service.analyse_url = MagicMock()
    QTest.mouseClick(tab._retry_btn, Qt.MouseButton.LeftButton)
    info = MediaInfo(url=items[1].url, title="ok", platform="x")
    tab._on_item_done(items[1], info, tab._batch_token)
    assert items[1].checked is True
    assert items[1].check_box.isChecked()


def test_sequential_all_failed_reports_error(tab):
    _load(tab, [_ItemState.READY, _ItemState.READY])
    tab._sequential_chk.setChecked(True)
    tab._app.service.start_download.side_effect = RuntimeError("boom")
    QTest.mouseClick(tab._queue_all_btn, Qt.MouseButton.LeftButton)
    while tab._seq_timer is not None and tab._seq_timer.isActive():
        tab._tick_sequential()
    assert tab._status_fn()[0] == t("batch.queue_all_failed", count=2)
    tab._app.toast.assert_any_call(t("batch.queue_all_failed", count=2), "error")


def test_sequential_run_locks_retry_and_analyse(tab):
    _load(tab, [_ItemState.READY, _ItemState.READY, _ItemState.ERROR])
    tab._sequential_chk.setChecked(True)
    tab._app.service.start_download.return_value = MagicMock(id="task-1")
    tab._app.service.get_task.return_value = None
    QTest.mouseClick(tab._queue_all_btn, Qt.MouseButton.LeftButton)
    tab._text_area.setPlainText("https://example.com/new")
    assert not tab._retry_btn.isEnabled()
    assert not tab._analyse_btn.isEnabled()
    while tab._seq_timer.isActive():
        tab._tick_sequential()
    assert tab._retry_btn.isEnabled()
    assert tab._analyse_btn.isEnabled()


def test_last_sequential_item_keeps_queue_all_locked(tab):
    items = _load(tab, [_ItemState.READY, _ItemState.READY])
    items[1].check_box.setChecked(False)
    tab._sequential_chk.setChecked(True)
    tab._app.service.start_download.return_value = MagicMock(id="task-1")
    QTest.mouseClick(tab._queue_all_btn, Qt.MouseButton.LeftButton)
    assert tab._seq_queue == []
    items[1].check_box.setChecked(True)
    assert not tab._queue_all_btn.isEnabled()
    assert tab._queue_all_btn.text() == t("batch.downloading_sequential")


def test_api_batch_enqueue_runs_off_the_event_loop():
    # FastAPI awaits an async handler on the event loop and runs a plain def in
    # its threadpool; 500 sync enqueues (mkdir, task scan) must take the latter.
    import api.server as srv
    from api.models import BatchDownloadItem, BatchDownloadRequest
    from domain.models.download_task import DownloadTask

    def start_download(url, media_info, format_id, output_ext):
        return DownloadTask(url=url, media_info=media_info, format_id=format_id, output_ext=output_ext)

    service = SimpleNamespace(start_download=start_download, get_all_tasks=lambda: [], get_history=lambda: [])
    config = SimpleNamespace(api_token="", download_dir=Path("/tmp"), default_quality="best", default_format="mp4")
    app = srv.create_app(service, config)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/download/batch")
    assert not inspect.iscoroutinefunction(route.endpoint)
    body = BatchDownloadRequest(items=[BatchDownloadItem(url="https://a.com/1", title="One")])
    assert route.endpoint(body=body, _=None).queued == 1


def test_removing_last_analysing_row_returns_to_idle(tab):
    tab._text_area.setPlainText("https://example.com/a")
    tab._get_analysis_delay = lambda url: 0.0
    tab._app.service.analyse_url = MagicMock()
    tab._start_batch_analyse()
    item = tab._items[0]
    assert item.state == _ItemState.ANALYSING
    QTest.mouseClick(item.remove_btn, Qt.MouseButton.LeftButton)
    tab._on_item_done(item, MediaInfo(url=item.url, title="t", platform="x"), tab._batch_token)
    assert tab._status_fn is None
    assert tab._select_all_chk.isHidden()
    assert tab._sequential_chk.isHidden()


def test_retranslate_refreshes_row_tooltip_and_url_count(tab):
    from utils import i18n

    before = i18n.get_language()
    i18n.set_language("en")
    try:
        items = _load(tab, [_ItemState.READY])
        tab._text_area.setPlainText("https://example.com/a\nhttps://example.com/b")
        i18n.set_language("vi")
        tab.retranslate()
        assert items[0].remove_btn.toolTip() == t("batch.remove_item_tooltip")
        assert tab._url_count_lbl.text() == t("batch.url_count", count=2)
    finally:
        i18n.set_language(before)


def test_sequential_failed_count_skips_removed_rows(tab):
    items = _load(tab, [_ItemState.READY, _ItemState.READY, _ItemState.READY])
    tab._sequential_chk.setChecked(True)
    tab._app.service.start_download.side_effect = RuntimeError("boom")
    QTest.mouseClick(tab._queue_all_btn, Qt.MouseButton.LeftButton)
    QTest.mouseClick(items[2].remove_btn, Qt.MouseButton.LeftButton)
    while tab._seq_timer.isActive():
        tab._tick_sequential()
    assert tab._status_fn()[0] == t("batch.queue_all_failed", count=2)


def test_row_ticked_during_run_is_queueable_after_it(tab):
    items = _load(tab, [_ItemState.READY, _ItemState.READY])
    items[1].check_box.setChecked(False)
    tab._sequential_chk.setChecked(True)
    tab._app.service.start_download.return_value = MagicMock(id="task-1")
    tab._app.service.get_task.return_value = None
    QTest.mouseClick(tab._queue_all_btn, Qt.MouseButton.LeftButton)
    items[1].check_box.setChecked(True)
    while tab._seq_timer.isActive():
        tab._tick_sequential()
    assert tab._queue_all_btn.isEnabled()
    assert tab._queue_all_btn.text() == t("batch.queue_add_count", count=1)
