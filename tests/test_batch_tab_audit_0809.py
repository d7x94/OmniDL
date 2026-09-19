"""Regression tests for the Batch tab audit (desktop UI + Web API), 2026-09-08.

Each test pins one defect found during the audit so it cannot come back:

Desktop — ui/tabs/batch_tab.py
  BA-01  One spinner QTimer chain per batch, not one per URL.
  BA-02  Removing a row must also drop it from a running sequential download.
  BA-03  Typing while a pass runs must not re-enable the Analyse button.
  BA-04  A sequential run jumps to the Queue tab once, not once per video.
  BA-05  A QUEUED row's checkbox is disabled — ticking it does nothing.
  BA-06  Stop analysing keeps the finished rows and re-arms the buttons.
  BA-07  The header shows analysis progress (n/total).

Web UI — api/static/index.html
  BW-01  The batch status line is replayed on a language switch.
  BW-02  The Analyse button label follows a language switch mid-pass.
  BW-03  A sequential run switches to the Queue tab once, not once per video.

Web API — api/models.py
  BAPI-01  BatchDownloadItem enforces the same validators as DownloadRequest.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parents[1]
_BATCH_TAB = _ROOT / "ui" / "tabs" / "batch_tab.py"
_INDEX_HTML = _ROOT / "api" / "static" / "index.html"


def _batch_stub():
    from ui.tabs.batch_tab import BatchTab

    tab = MagicMock(spec=BatchTab)
    tab._items = []
    tab._seq_queue = []
    tab._batch_token = 1
    tab._analysing_count = 0
    tab._spinner_idx = 0
    tab._spinner_token = None
    tab._status_fn = None
    tab._app = MagicMock()
    tab._queue_all_btn = MagicMock()
    tab._status_lbl = MagicMock()
    tab._items_layout = MagicMock()
    tab._get_analysis_delay.return_value = 0.0  # the worker thread must not sleep on a Mock
    return tab


# ── BA-01: a single spinner chain per batch ───────────────────────────────────


def test_spinner_chain_is_started_once_per_batch():
    """The count dips to 0 and back to 1 between two rows.  The old guard
    (`_analysing_count == 1`) therefore started a new 100 ms QTimer chain for
    every URL while the previous chain was still pending."""
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._items = [_BatchItem(url=f"https://example.com/{n}", state=_ItemState.PENDING) for n in range(5)]

    # First row: no chain yet, so one must start.
    BatchTab._analyse_next(tab, tab._batch_token)
    assert tab._tick_spinner.call_count == 1

    # Simulate the chain claiming ownership, then walk the rest of the batch.
    tab._spinner_token = tab._batch_token
    for item in tab._items:
        if item.state == _ItemState.ANALYSING:
            item.state = _ItemState.READY
    for _ in range(4):
        BatchTab._analyse_next(tab, tab._batch_token)
        for item in tab._items:
            if item.state == _ItemState.ANALYSING:
                item.state = _ItemState.READY

    assert tab._tick_spinner.call_count == 1, "one chain per batch, not one per URL"


def test_tick_spinner_releases_the_flag_when_the_batch_ends():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._spinner_token = 1
    tab._analysing_count = 0  # pass finished
    BatchTab._tick_spinner(tab, 1)
    assert tab._spinner_token is None


# ── BA-02: removing a row cancels its pending sequential download ─────────────


def test_removing_a_row_drops_it_from_the_sequential_queue():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    keep = _BatchItem(url="https://example.com/a", state=_ItemState.READY)
    drop = _BatchItem(url="https://example.com/b", state=_ItemState.READY)
    tab._items = [keep, drop]
    tab._seq_queue = [keep, drop]

    BatchTab._remove_item(tab, drop)

    assert tab._items == [keep]
    assert tab._seq_queue == [keep], "a deleted row must not still be downloaded"


def test_removing_a_row_that_is_not_queued_is_harmless():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    item = _BatchItem(url="https://example.com/a", state=_ItemState.READY)
    tab._items = [item]
    tab._seq_queue = []

    BatchTab._remove_item(tab, item)

    assert tab._items == []
    assert tab._seq_queue == []


# ── BA-03: the Analyse button stays locked during a pass ──────────────────────


def test_typing_during_a_pass_does_not_re_enable_analyse():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._analysing_count = 1
    tab._url_count_lbl = MagicMock()
    tab._analyse_btn = MagicMock()
    tab._parse_textarea.return_value = ["https://example.com/a"]

    BatchTab._on_text_change(tab)

    tab._analyse_btn.setEnabled.assert_not_called()
    tab._url_count_lbl.setText.assert_called_once()  # the counter still updates


def test_typing_while_idle_still_enables_analyse():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._url_count_lbl = MagicMock()
    tab._analyse_btn = MagicMock()
    tab._parse_textarea.return_value = ["https://example.com/a"]

    BatchTab._on_text_change(tab)

    tab._analyse_btn.setEnabled.assert_called_once_with(True)


def test_empty_textarea_disables_analyse():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._url_count_lbl = MagicMock()
    tab._analyse_btn = MagicMock()
    tab._parse_textarea.return_value = []

    BatchTab._on_text_change(tab)

    tab._analyse_btn.setEnabled.assert_called_once_with(False)


# ── BA-04: one tab switch per sequential run ──────────────────────────────────


def test_sequential_submit_does_not_navigate_per_video():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._seq_queue = [_BatchItem(url="https://example.com/a", state=_ItemState.READY)]
    tab._submit_one.return_value = "task-1"

    BatchTab._submit_next_sequential(tab, "best", "mp4")

    tab._app.navigate_to.assert_not_called()


def test_queue_all_navigates_once_for_the_whole_sequential_run():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._quality_combo = MagicMock()
    tab._quality_combo.currentText.return_value = "Best"
    tab._quality_map = {"Best": "bestvideo+bestaudio/best"}
    tab._format_combo = MagicMock()
    tab._format_combo.currentText.return_value = "mp4"
    tab._sequential_chk = MagicMock()
    tab._sequential_chk.isChecked.return_value = True
    tab._items = [
        _BatchItem(url=f"https://example.com/{n}", state=_ItemState.READY, media_info=object())
        for n in range(3)
    ]

    BatchTab._queue_all(tab)

    assert tab._app.navigate_to.call_count == 1
    tab._app.navigate_to.assert_called_once_with("queue")


# ── BA-05: a queued row cannot be un-ticked ───────────────────────────────────


def test_queued_row_disables_its_checkbox():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    item = _BatchItem(url="https://example.com/a", state=_ItemState.QUEUED)
    item.row_frame = MagicMock()
    item.state_lbl = MagicMock()
    item.title_lbl = MagicMock()
    item.platform_lbl = MagicMock()
    item.check_box = MagicMock()

    BatchTab._refresh_item_ui(_batch_stub(), item)

    item.check_box.setEnabled.assert_called_once_with(False)


# ── BA-06: Stop analysing ─────────────────────────────────────────────────────


def test_cancel_analyse_keeps_finished_rows_and_reopens_the_button():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._analysing_count = 1
    tab._spinner_token = 1
    tab._cancel_btn = MagicMock()
    tab._analyse_btn = MagicMock()
    tab._retry_btn = MagicMock()
    tab._parse_textarea.return_value = ["https://example.com/a"]

    done = _BatchItem(url="https://example.com/a", state=_ItemState.READY)
    running = _BatchItem(url="https://example.com/b", state=_ItemState.ANALYSING)
    waiting = _BatchItem(url="https://example.com/c", state=_ItemState.PENDING)
    tab._items = [done, running, waiting]

    BatchTab._cancel_analyse(tab)

    assert tab._batch_token == 2, "the stale worker callbacks must be invalidated"
    assert tab._analysing_count == 0
    assert tab._spinner_token is None
    assert done.state is _ItemState.READY, "finished rows survive a cancel"
    assert running.state is _ItemState.PENDING
    tab._cancel_btn.setVisible.assert_called_once_with(False)
    tab._analyse_btn.setEnabled.assert_called_once_with(True)


def test_cancel_analyse_is_a_no_op_when_nothing_is_running():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._analysing_count = 0
    tab._cancel_btn = MagicMock()

    BatchTab._cancel_analyse(tab)

    assert tab._batch_token == 1
    tab._cancel_btn.setVisible.assert_not_called()


@pytest.mark.parametrize("lang", ["en", "vi", "zh"])
@pytest.mark.parametrize("key", ["batch.cancel_analyse", "batch.analyse_stopped", "batch.analysing_progress"])
def test_new_batch_keys_exist_in_every_language(lang, key):
    from utils.translations import CATALOG

    assert key in CATALOG[lang]


# ── BA-07: analysis progress in the header ────────────────────────────────────


def test_analyse_next_publishes_progress():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._items = [
        _BatchItem(url="https://example.com/a", state=_ItemState.READY),
        _BatchItem(url="https://example.com/b", state=_ItemState.ERROR),
        _BatchItem(url="https://example.com/c", state=_ItemState.PENDING),
    ]

    BatchTab._analyse_next(tab, tab._batch_token)

    tab._set_status.assert_called_once()
    text, _color = tab._set_status.call_args[0][0]()
    assert "2" in text and "3" in text


# ── BW-01 / BW-02 / BW-03: the web UI ─────────────────────────────────────────


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def test_batch_status_is_replayed_on_a_language_switch():
    html = _html()
    assert "function _renderBatchStatus()" in html
    block = html.split("function applyI18n()", 1)[1].split("\n}", 1)[0]
    assert "_renderBatchStatus" in block, "applyI18n must replay the batch status line"


def test_no_batch_status_call_passes_a_pre_translated_string():
    """setBatchStatus() only takes literals now; anything from T() must go
    through setBatchStatusFn so applyI18n can rebuild it."""
    html = _html()
    for call in re.findall(r"setBatchStatus\((.*?)\);", html):
        assert "T(" not in call, f"translated text passed to setBatchStatus: {call}"


def test_analyse_button_label_follows_a_language_switch():
    html = _html()
    block = html.split("function renderBatchControls()", 1)[1].split("\n}", 1)[0]
    running = block.split("if (_batchAnalyseRunning())", 1)[1].split("} else {", 1)[0]
    assert "batch.analysing_dots" in running
    assert "batch.retrying_dots" in running


def test_sequential_run_switches_tab_once():
    html = _html()
    seq = html.split("async function _batchSeqNext(", 1)[1].split("\n}", 1)[0]
    assert "switchTab(" not in seq, "switching per video throws the user off their tab"
    queue_all = html.split("async function queueBatchAll(", 1)[1].split("\nasync function", 1)[0]
    assert queue_all.count("switchTab('queue'") == 2, "once for sequential, once for bulk"


# ── BAPI-01: the batch endpoint validates like /api/download ──────────────────


def test_batch_item_inherits_the_download_request_validators():
    from api.models import BatchDownloadItem, DownloadRequest

    assert issubclass(BatchDownloadItem, DownloadRequest)


@pytest.mark.parametrize(
    "payload",
    [
        {"url": "file:///etc/passwd"},
        {"url": "not a url"},
        {"url": "https://example.com/v", "output_ext": "exe"},
        {"url": "https://example.com/v", "source_engine": "rm -rf"},
    ],
)
def test_batch_item_rejects_what_the_single_endpoint_rejects(payload):
    from api.models import BatchDownloadRequest

    with pytest.raises(ValidationError):
        BatchDownloadRequest(items=[payload])


def test_batch_item_still_accepts_a_normal_request():
    from api.models import BatchDownloadRequest

    req = BatchDownloadRequest(
        items=[
            {
                "url": "https://www.tiktok.com/@u/video/1",
                "output_ext": "mp4",
                "source_engine": "yt_dlp",
                "is_live": False,
            }
        ]
    )
    assert req.items[0].url == "https://www.tiktok.com/@u/video/1"
