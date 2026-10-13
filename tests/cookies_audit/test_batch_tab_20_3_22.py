"""Batch tab audit (v20.3.22): real BatchTab widget, offscreen, mocked service."""

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
