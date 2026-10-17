"""Regression tests for the Queue + Batch audit (desktop UI and web API).

Each test pins one bug found during the audit so it cannot come back:

Queue / core
  Q-01  DownloadTask.pause() must refuse terminal + PROCESSING states.
  Q-02  DELETE /api/queue/items must skip tasks with a running convert job.
  Q-03  Dropping tasks must publish EventBus.DOWNLOAD_REMOVED.
  Q-04  Desktop select mode must reach tasks that finish while it is on.
  Q-05  Leaving select mode must clear the checkbox tick.
  Q-06/07  Web queue: Pause hidden for PROCESSING and for gallery-dl.

Batch
  B-01  The analysis spinner must restart after the analysing row is removed.
  B-02  "Select all" must not tick ERROR rows.
  B-03  retranslate() must rebuild the status line.
  B-04  A batch where every submission fails must say so.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo

_INDEX_HTML = Path(__file__).resolve().parents[1] / "api" / "static" / "index.html"


def _task(status: DownloadStatus) -> DownloadTask:
    t = DownloadTask(url="https://example.com/v", media_info=None, format_id="best", output_ext="mp4")
    t.status = status
    return t


# ── Q-01: pause / resume state machine ────────────────────────────────────────


@pytest.mark.parametrize("status", sorted(DownloadStatus.terminal_states(), key=lambda s: s.name))
def test_pause_refuses_terminal_states(status):
    """A finished task must stay finished.

    Pausing one used to move it out of terminal_states(), so "clear finished"
    could never remove it and a following resume() flipped it back to
    DOWNLOADING — a ghost job stuck in the queue badge forever.
    """
    task = _task(status)
    assert task.pause() is False
    assert task.status is status


def test_pause_refuses_processing():
    task = _task(DownloadStatus.PROCESSING)
    assert task.pause() is False
    assert task.status is DownloadStatus.PROCESSING


@pytest.mark.parametrize("status", [DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING])
def test_pause_accepts_active_states(status):
    task = _task(status)
    assert task.pause() is True
    assert task.status is DownloadStatus.PAUSED


def test_resume_only_acts_on_paused():
    task = _task(DownloadStatus.COMPLETED)
    assert task.resume() is False
    assert task.status is DownloadStatus.COMPLETED

    task = _task(DownloadStatus.PAUSED)
    assert task.resume() is True
    assert task.status is DownloadStatus.DOWNLOADING


def test_completed_task_cannot_be_resurrected():
    """The full exploit path: pause then resume a completed download."""
    task = _task(DownloadStatus.COMPLETED)
    task.pause()
    task.resume()
    assert task.status is DownloadStatus.COMPLETED
    assert task.status in DownloadStatus.terminal_states()


# ── Q-03: removal events ──────────────────────────────────────────────────────


def _manager():
    from infrastructure.downloader.download_manager import DownloadManager

    cfg = MagicMock()
    cfg.max_concurrent = 2
    cfg.download_dir = Path("/tmp")
    bus = MagicMock()
    mgr = DownloadManager(config=cfg, engine=MagicMock(), event_bus=bus)
    return mgr, bus


def test_clear_terminal_publishes_removed_ids():
    from app.event_bus import EventBus

    mgr, bus = _manager()
    done = _task(DownloadStatus.COMPLETED)
    running = _task(DownloadStatus.DOWNLOADING)
    mgr._tasks = {done.id: done, running.id: running}

    removed = mgr.clear_terminal()

    assert removed == [done.id]
    published = [c for c in bus.publish.call_args_list if c[0][0] == EventBus.DOWNLOAD_REMOVED]
    assert published, "clearing tasks must announce them so remote clients forget them"
    assert published[-1][1]["ids"] == [done.id]


def test_clear_specific_publishes_only_what_it_removed():
    from app.event_bus import EventBus

    mgr, bus = _manager()
    done = _task(DownloadStatus.COMPLETED)
    running = _task(DownloadStatus.DOWNLOADING)
    mgr._tasks = {done.id: done, running.id: running}

    # An active task is not removable, so it must not be announced either.
    removed = mgr.clear_specific([done.id, running.id, "does-not-exist"])

    assert removed == [done.id]
    assert running.id in mgr._tasks
    published = [c for c in bus.publish.call_args_list if c[0][0] == EventBus.DOWNLOAD_REMOVED]
    assert published[-1][1]["ids"] == [done.id]


def test_no_removal_event_when_nothing_was_removed():
    from app.event_bus import EventBus

    mgr, bus = _manager()
    mgr._tasks = {}
    mgr.clear_terminal()
    assert not [c for c in bus.publish.call_args_list if c[0][0] == EventBus.DOWNLOAD_REMOVED]


def test_purge_returns_ids_for_the_caller_to_announce():
    """_purge_old_tasks runs under the manager lock, so it reports rather than
    publishes — enqueue() broadcasts once the lock is released."""
    import infrastructure.downloader.download_manager as dm

    mgr, _bus = _manager()
    mgr._tasks = {}
    for _ in range(dm.MAX_TASKS):
        t = _task(DownloadStatus.COMPLETED)
        mgr._tasks[t.id] = t

    purged = mgr._purge_old_tasks()

    assert purged, "registry over MAX_TASKS must shed old terminal tasks"
    assert all(tid not in mgr._tasks for tid in purged)


# ── Q-01/Q-02: API surface ────────────────────────────────────────────────────


def _endpoint(app, path: str, method: str = "GET"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _api(tasks, remote_convert=None):
    """Build the real FastAPI app over an in-memory task store."""
    import api.server as srv

    store = {t.id: t for t in tasks}
    service = SimpleNamespace(
        get_all_tasks=lambda: list(store.values()),
        get_history=lambda: [],
        get_task=store.get,
        pause_download=lambda tid: store[tid].pause(),
        resume_download=lambda tid: store[tid].resume(),
        clear_specific=lambda ids: [
            i for i in ids if store[i].status in DownloadStatus.terminal_states()
        ],
        clear_finished=lambda exclude_ids=None: [],
    )
    config = SimpleNamespace(api_token="", download_dir=Path("/tmp"))
    app = srv.create_app(service, config, remote_convert=remote_convert)
    return app, store


def test_api_pause_endpoint_rejects_terminal_tasks():
    done = _task(DownloadStatus.COMPLETED)
    app, _ = _api([done])
    pause = _endpoint(app, "/api/queue/{task_id}/pause", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(pause(task_id=done.id, _=None))

    assert exc.value.status_code == 409
    assert done.status is DownloadStatus.COMPLETED


def test_api_resume_endpoint_rejects_unpaused_tasks():
    done = _task(DownloadStatus.COMPLETED)
    app, _ = _api([done])
    resume = _endpoint(app, "/api/queue/{task_id}/resume", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(resume(task_id=done.id, _=None))

    assert exc.value.status_code == 409
    assert done.status is DownloadStatus.COMPLETED


def test_api_pause_resume_still_works_on_a_running_task():
    live = _task(DownloadStatus.DOWNLOADING)
    app, _ = _api([live])
    pause = _endpoint(app, "/api/queue/{task_id}/pause", "POST")
    resume = _endpoint(app, "/api/queue/{task_id}/resume", "POST")

    assert asyncio.run(pause(task_id=live.id, _=None)).action == "paused"
    assert live.status is DownloadStatus.PAUSED
    assert asyncio.run(resume(task_id=live.id, _=None)).action == "resumed"
    assert live.status is DownloadStatus.DOWNLOADING


def test_clear_selected_excludes_active_convert_jobs():
    """DELETE /api/queue/items must honour the same exclusion as
    DELETE /api/queue/finished — clearing a task mid-conversion orphans the
    FFmpeg job with no queue entry left to show or cancel it."""
    from api.models import ClearItemsRequest
    from domain.models.conversion_job import ConversionStatus

    converting = _task(DownloadStatus.COMPLETED)
    plain = _task(DownloadStatus.COMPLETED)
    remote = MagicMock()
    remote.get_all_jobs.return_value = [
        SimpleNamespace(source_task_id=converting.id, status=ConversionStatus.CONVERTING)
    ]

    app, _ = _api([converting, plain], remote_convert=remote)
    clear = _endpoint(app, "/api/queue/items", "DELETE")

    out = asyncio.run(clear(body=ClearItemsRequest(ids=[converting.id, plain.id]), _=None))

    assert out["removed_ids"] == [plain.id]
    assert out["excluded_ids"] == [converting.id]
    assert out["excluded_count"] == 1


def test_clear_selected_removes_everything_when_nothing_is_converting():
    from api.models import ClearItemsRequest

    a = _task(DownloadStatus.COMPLETED)
    b = _task(DownloadStatus.FAILED)
    app, _ = _api([a, b])
    clear = _endpoint(app, "/api/queue/items", "DELETE")

    out = asyncio.run(clear(body=ClearItemsRequest(ids=[a.id, b.id]), _=None))

    assert sorted(out["removed_ids"]) == sorted([a.id, b.id])
    assert out["excluded_count"] == 0


def test_task_response_exposes_source_engine():
    from api.models import TaskResponse

    assert "source_engine" in TaskResponse.model_fields
    assert TaskResponse.model_fields["source_engine"].default == "yt_dlp"


def test_task_dict_reports_source_engine():
    from api.server import _task_to_dict

    task = _task(DownloadStatus.DOWNLOADING)
    task.media_info = MediaInfo(
        title="x", url="https://example.com/v", platform="instagram", source_engine="gallery_dl"
    )
    assert _task_to_dict(task)["source_engine"] == "gallery_dl"

    plain = _task(DownloadStatus.DOWNLOADING)
    assert _task_to_dict(plain)["source_engine"] == "yt_dlp"


# ── Q-04 / Q-05: desktop select mode ──────────────────────────────────────────


def test_queue_poll_reapplies_select_mode():
    """A task that finishes while select mode is on had no checkbox when the
    mode was switched on (only terminal tasks get one), and nothing else ever
    gave it one."""
    src = (Path(__file__).resolve().parents[1] / "ui" / "tabs" / "queue_tab.py").read_text(encoding="utf-8")
    refresh_branch = src.split("else:\n                w = self._widgets[task.id]", 1)[1][:400]
    assert "set_select_mode(True, self._on_item_select)" in refresh_branch


def test_leaving_select_mode_unticks_the_checkbox():
    src = (Path(__file__).resolve().parents[1] / "ui" / "components" / "download_item_widget.py").read_text(
        encoding="utf-8"
    )
    off_branch = src.split("def set_select_mode", 1)[1].split("else:", 1)[1]
    assert "setChecked(False)" in off_branch
    assert "blockSignals(True)" in off_branch


def test_select_mode_connects_the_toggle_once():
    """set_select_mode is now called on every poll; reconnecting each time
    would fire the owner's handler once per elapsed poll."""
    src = (Path(__file__).resolve().parents[1] / "ui" / "components" / "download_item_widget.py").read_text(
        encoding="utf-8"
    )
    body = src.split("def set_select_mode", 1)[1].split("def retranslate", 1)[0]
    assert body.count("toggled.connect") == 1


# ── Q-06 / Q-07 / W-*: web queue rendering ────────────────────────────────────


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def test_web_hides_pause_for_processing_and_gallery_dl():
    html = _html()
    assert "const canPause = t.status === 'DOWNLOADING' && t.source_engine !== 'gallery_dl';" in html
    controls = html.split("let controls = ''", 1)[1][:600]
    assert "canPause" in controls


def test_web_handles_the_removed_event():
    html = _html()
    assert "evtSrc.addEventListener('removed'" in html


def test_web_progress_does_not_rebuild_the_whole_list():
    html = _html()
    assert "'progress'" not in html.split("const liveEvents =", 1)[1].split("]", 1)[0]
    assert "function _patchQueueItem(t)" in html


def test_web_queue_has_no_hardcoded_english_left():
    """The strings themselves still live in the en catalogue — what must be
    gone is the call sites that assigned them directly."""
    html = _html()
    for call_site in (
        "btn.textContent = 'Cancelling…'",
        "btn.textContent = '⏳ Sending…'",
        "btn.textContent = '📡 Queued…'",
        "toast(r.detail || 'Transfer queued')",
        "'Cleared finished tasks')",
    ):
        assert call_site not in html, f"{call_site} must go through T()"


def test_web_status_labels_exist_in_all_three_languages():
    """The badge no longer prints the raw enum name, so every DownloadStatus
    member needs a catalogue entry in every language."""
    html = _html()
    for status in DownloadStatus:
        key = f"'q.st.{status.name}':"
        assert html.count(key) == 3, f"{key} missing from one of vi/en/zh"


def test_partial_saved_draws_a_finished_progress_bar():
    html = _html()
    fill_map = re.search(r"const fillCls = \{[^}]*\}", html).group(0)
    assert "PARTIAL_SAVED:'done'" in fill_map


# ── Batch tab ─────────────────────────────────────────────────────────────────


def _batch_stub():
    from ui.tabs.batch_tab import BatchTab

    tab = MagicMock(spec=BatchTab)
    tab._items = []
    tab._batch_token = 1
    tab._analysing_count = 0
    tab._spinner_idx = 0
    tab._spinner_token = None
    tab._status_fn = None
    tab._app = MagicMock()
    return tab


def test_analyse_next_restarts_the_spinner():
    """Removing the analysing row drops the count to 0 and _tick_spinner exits.
    Nothing restarted it, so every later row froze on a static "…"."""
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._items = [_BatchItem(url="https://example.com/a", state=_ItemState.PENDING)]

    BatchTab._analyse_next(tab, tab._batch_token)

    assert tab._analysing_count == 1
    tab._tick_spinner.assert_called_once_with(tab._batch_token)


def test_analyse_next_does_not_stack_spinner_loops():
    """B-05: the restart guard used to be `_analysing_count == 1`.

    The count dips to 0 and back to 1 between two rows, so every row started a
    fresh 100 ms QTimer chain while the previous one was still pending — a
    500-URL batch ended up with 500 overlapping chains and an ever-faster
    spinner.  The guard is now the batch token the running chain owns.
    """
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._spinner_token = tab._batch_token  # a chain for this batch already ticks
    tab._items = [_BatchItem(url="https://example.com/a", state=_ItemState.PENDING)]

    BatchTab._analyse_next(tab, tab._batch_token)

    tab._tick_spinner.assert_not_called()


def test_spinner_chain_from_a_cancelled_batch_does_not_disable_the_new_one():
    """A stale chain must only clear the flag when it is the one that set it."""
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._batch_token = 2
    tab._spinner_token = 2  # the live chain
    BatchTab._tick_spinner(tab, 1)  # a chain left over from the cancelled batch
    assert tab._spinner_token == 2


def test_select_all_skips_rows_that_cannot_be_queued():
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    ready = _BatchItem(url="https://example.com/a", state=_ItemState.READY, checked=False)
    bad = _BatchItem(url="https://example.com/b", state=_ItemState.ERROR, checked=False)
    queued = _BatchItem(url="https://example.com/c", state=_ItemState.QUEUED, checked=False)
    tab._items = [ready, bad, queued]

    BatchTab._on_select_all_toggled(tab, True)

    assert ready.checked is True
    assert bad.checked is False, "an ERROR row has a disabled checkbox and can never be queued"
    assert queued.checked is False


def test_status_line_is_rebuilt_on_language_change():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    lang = {"v": "vi"}
    BatchTab._set_status(tab, lambda: (f"status-{lang['v']}", "#fff"))
    tab._render_status.assert_called_once()

    # _set_status stores the builder, so replaying it picks up the new language.
    assert tab._status_fn() == ("status-vi", "#fff")
    lang["v"] = "en"
    assert tab._status_fn() == ("status-en", "#fff")


def test_render_status_clears_when_no_builder():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_stub()
    tab._status_lbl = MagicMock()
    tab._status_fn = None
    BatchTab._render_status(tab)
    tab._status_lbl.setText.assert_called_once_with("")


def test_retranslate_replays_the_status_line():
    src = (Path(__file__).resolve().parents[1] / "ui" / "tabs" / "batch_tab.py").read_text(encoding="utf-8")
    body = src.split("def retranslate", 1)[1]
    assert "self._render_status()" in body


def test_batch_reports_when_every_submission_fails():
    """Previously `if queued:` swallowed the all-failed case: the button still
    read "Add N videos" and the status line still claimed N were ready."""
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_stub()
    tab._quality_combo = MagicMock()
    tab._quality_combo.currentText.return_value = "Best"
    tab._quality_map = {"Best": "bestvideo+bestaudio/best"}
    tab._format_combo = MagicMock()
    tab._format_combo.currentText.return_value = "mp4"
    tab._sequential_chk = MagicMock()
    tab._sequential_chk.isChecked.return_value = False
    tab._app = MagicMock()
    tab._submit_one.return_value = None  # every submission fails
    tab._items = [
        _BatchItem(
            url="https://example.com/a",
            state=_ItemState.READY,
            checked=True,
            media_info=MediaInfo(title="a", url="https://example.com/a", platform="youtube"),
        )
    ]

    BatchTab._queue_all(tab)

    assert tab._app.toast.called
    assert tab._app.toast.call_args[0][1] == "error"
    assert tab._set_status.called
    tab._app.navigate_to.assert_not_called()


def test_batch_queue_all_failed_key_exists_in_every_language():
    from utils.translations import CATALOG

    for lang, table in CATALOG.items():
        assert "batch.queue_all_failed" in table, f"missing in {lang}"
