"""
tests/test_download_service.py
Unit tests for app/services/download_service.py

All infrastructure is mocked — no network, no disk, no real threads (where possible).

Covers:
- analyse_url: valid URL dispatches engine, invalid URL calls on_error immediately
- analyse_url: engine error calls on_error
- start_download: creates task with correct fields, uses custom output_dir
- start_download: falls back to config.download_dir when no output_dir given
- pause / resume / cancel: delegate to manager
- clear_finished: delegates to manager
- get_all_tasks / get_history / search_history / clear_history: delegation
- _on_completed / _on_failed / _on_cancelled: trigger history.add via daemon thread
- EventBus subscriptions wired at init
"""
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from app.event_bus import EventBus
from app.services.download_service import DownloadService
from domain.models.download_task import DownloadTask, MediaInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service(
    engine_info=None,
    engine_error=None,
    download_dir=None,
):
    """Return (service, mocks) tuple with all deps mocked."""
    config = MagicMock()
    config.download_dir = download_dir or Path("/tmp/omnidl_test")  # nosec B108

    manager = MagicMock()
    manager.get_all_tasks.return_value = []

    history = MagicMock()
    history.all.return_value = []
    history.search.return_value = []

    engine = MagicMock()
    if engine_info is not None:
        engine.extract_info.return_value = engine_info
    if engine_error is not None:
        engine.extract_info.side_effect = RuntimeError(engine_error)

    bus = EventBus()  # real bus so subscriptions work

    service = DownloadService(
        config=config,
        download_manager=manager,
        history_repo=history,
        engine=engine,
        event_bus=bus,
    )

    mocks = dict(
        config=config, manager=manager, history=history, engine=engine, bus=bus
    )
    return service, mocks


def make_media_info(url="https://youtube.com/watch?v=test", title="Test Video"):
    return MediaInfo(url=url, title=title)


def wait_for(condition, timeout=3.0):
    """Poll *condition* every 10 ms until True or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# analyse_url
# ---------------------------------------------------------------------------

class TestAnalyseUrl:
    def test_invalid_url_calls_on_error_synchronously(self):
        service, _ = make_service()
        errors = []
        service.analyse_url("not-a-url", on_done=lambda i: None, on_error=errors.append)
        assert errors and "Invalid URL" in errors[0]

    def test_valid_url_calls_on_done_with_media_info(self):
        info = make_media_info()
        service, mocks = make_service(engine_info=info)
        results = []
        done = threading.Event()

        def on_done(i):
            results.append(i)
            done.set()

        service.analyse_url(
            "https://youtube.com/watch?v=abc",
            on_done=on_done,
            on_error=lambda e: None,
        )
        assert done.wait(3), "on_done was never called"
        assert results[0] is info

    def test_engine_error_calls_on_error(self):
        service, _ = make_service(engine_error="Video is private")
        errors = []
        done = threading.Event()

        def on_error(e):
            errors.append(e)
            done.set()

        service.analyse_url(
            "https://youtube.com/watch?v=abc",
            on_done=lambda i: None,
            on_error=on_error,
        )
        assert done.wait(3), "on_error was never called"
        assert "private" in errors[0].lower()

    def test_valid_url_publishes_analysis_done_event(self):
        info = make_media_info()
        service, mocks = make_service(engine_info=info)
        events = []
        mocks["bus"].subscribe(EventBus.ANALYSIS_DONE, lambda **kw: events.append(kw))
        done = threading.Event()

        service.analyse_url(
            "https://youtube.com/watch?v=abc",
            on_done=lambda i: done.set(),
            on_error=lambda e: None,
        )
        done.wait(3)
        assert any("info" in e for e in events)

    def test_engine_error_publishes_analysis_failed_event(self):
        service, mocks = make_service(engine_error="Not found")
        events = []
        mocks["bus"].subscribe(EventBus.ANALYSIS_FAILED, lambda **kw: events.append(kw))
        done = threading.Event()

        service.analyse_url(
            "https://youtube.com/watch?v=abc",
            on_done=lambda i: None,
            on_error=lambda e: done.set(),
        )
        done.wait(3)
        assert events


# ---------------------------------------------------------------------------
# start_download
# ---------------------------------------------------------------------------

class TestStartDownload:
    def test_returns_download_task(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        info = make_media_info()
        task = service.start_download(
            url="https://youtube.com/watch?v=abc",
            media_info=info,
            format_id="best",
            output_ext="mp4",
        )
        assert isinstance(task, DownloadTask)

    def test_task_has_correct_url(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        info = make_media_info(url="https://youtube.com/watch?v=abc")
        task = service.start_download(
            url="https://youtube.com/watch?v=abc",
            media_info=info,
            format_id="best",
            output_ext="mp4",
        )
        assert task.url == "https://youtube.com/watch?v=abc"

    def test_task_uses_custom_output_dir(self, tmp_path):
        custom_dir = tmp_path / "custom"
        service, mocks = make_service(download_dir=tmp_path)
        info = make_media_info()
        task = service.start_download(
            url="https://youtube.com/watch?v=abc",
            media_info=info,
            format_id="best",
            output_ext="mp4",
            output_dir=custom_dir,
        )
        assert task.output_dir == str(custom_dir)

    def test_task_falls_back_to_config_download_dir(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        info = make_media_info()
        task = service.start_download(
            url="https://youtube.com/watch?v=abc",
            media_info=info,
            format_id="best",
            output_ext="mp4",
        )
        assert task.output_dir == str(tmp_path)

    def test_enqueue_called_on_manager(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        info = make_media_info()
        task = service.start_download(
            url="https://youtube.com/watch?v=abc",
            media_info=info,
            format_id="best",
            output_ext="mp4",
        )
        mocks["manager"].enqueue.assert_called_once_with(task)

    def test_output_dir_is_created(self, tmp_path):
        new_dir = tmp_path / "new_subdir"
        assert not new_dir.exists()
        service, mocks = make_service(download_dir=new_dir)
        info = make_media_info()
        service.start_download(
            url="https://youtube.com/watch?v=abc",
            media_info=info,
            format_id="best",
            output_ext="mp4",
        )
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# Delegation methods
# ---------------------------------------------------------------------------

class TestDelegation:
    def test_pause_download_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.pause_download("task-1")
        mocks["manager"].pause.assert_called_once_with("task-1")

    def test_resume_download_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.resume_download("task-1")
        mocks["manager"].resume.assert_called_once_with("task-1")

    def test_cancel_download_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.cancel_download("task-1")
        mocks["manager"].cancel.assert_called_once_with("task-1")

    def test_clear_finished_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.clear_finished()
        mocks["manager"].clear_terminal.assert_called_once()

    def test_get_all_tasks_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.get_all_tasks()
        mocks["manager"].get_all_tasks.assert_called_once()

    def test_get_history_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.get_history()
        mocks["history"].all.assert_called_once()

    def test_search_history_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.search_history("Rick Astley")
        mocks["history"].search.assert_called_once_with("Rick Astley")

    def test_clear_history_delegates(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        service.clear_history()
        mocks["history"].clear.assert_called_once()


# ---------------------------------------------------------------------------
# History save via EventBus
# ---------------------------------------------------------------------------

class TestHistorySaveOnEvent:
    """_on_completed / _on_failed / _on_cancelled must call history.add
    (dispatched on a daemon thread — wait for it)."""

    def _make_task(self):
        t = DownloadTask(url="https://example.com/v")
        t.media_info = MediaInfo(url=t.url, title="Test")
        return t

    def _wait_history_add(self, history_mock, timeout=3.0):
        return wait_for(lambda: history_mock.add.called, timeout)

    def test_completed_event_saves_to_history(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        task = self._make_task()
        mocks["bus"].publish(EventBus.DOWNLOAD_COMPLETED, task=task)
        assert self._wait_history_add(mocks["history"]), \
            "history.add not called on COMPLETED"
        mocks["history"].add.assert_called_with(task)

    def test_failed_event_saves_to_history(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        task = self._make_task()
        mocks["bus"].publish(EventBus.DOWNLOAD_FAILED, task=task)
        assert self._wait_history_add(mocks["history"]), \
            "history.add not called on FAILED"
        mocks["history"].add.assert_called_with(task)

    def test_cancelled_event_saves_to_history(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        task = self._make_task()
        mocks["bus"].publish(EventBus.DOWNLOAD_CANCELLED, task=task)
        assert self._wait_history_add(mocks["history"]), \
            "history.add not called on CANCELLED"
        mocks["history"].add.assert_called_with(task)
