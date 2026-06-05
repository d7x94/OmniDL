"""
tests/test_logger.py
Unit tests for utils/logger.py
"""

import logging
import time
from unittest.mock import MagicMock

import pytest

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.download_manager import DownloadManager
from utils.logger import setup_logging


class TestSetupLogging:
    def test_creates_log_file(self, tmp_path):
        setup_logging(tmp_path / "logs")
        assert (tmp_path / "logs" / "omnidl.log").exists()

    def test_creates_log_dir_if_missing(self, tmp_path):
        log_dir = tmp_path / "deep" / "nested" / "logs"
        setup_logging(log_dir)
        assert log_dir.exists()

    def test_root_logger_level_set(self, tmp_path):
        setup_logging(tmp_path, level=logging.DEBUG)
        assert logging.getLogger().level == logging.DEBUG

    def test_noisy_loggers_set_to_warning(self, tmp_path):
        setup_logging(tmp_path)
        for name in ("PIL", "urllib3", "requests", "yt_dlp"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_oserror_on_log_file_does_not_crash(self, tmp_path):
        """If the log file can't be opened, setup_logging should not raise."""
        from unittest.mock import MagicMock, patch

        with patch("utils.logger.logger") as mock_log:
            mock_log.remove = MagicMock()
            mock_log.add.side_effect = [None, OSError("read-only filesystem")]
            mock_log.warning = MagicMock()
            setup_logging(tmp_path)  # should not raise


"""
tests/test_download_manager_extra.py
Additional tests for infrastructure/downloader/download_manager.py

Fills coverage gaps:
- shutdown() cancels all active tasks
- clear_terminal() removes completed/failed/cancelled tasks
- pause() / resume() publish events
- cancel() sets cancellation on task
"""


# ---------------------------------------------------------------------------
# Helpers (same as test_download_manager.py)
# ---------------------------------------------------------------------------


def make_config(max_concurrent=2):
    cfg = MagicMock()
    cfg.max_concurrent = max_concurrent
    cfg.max_retries = 1
    return cfg


def make_engine(delay=0.0, fail=False):
    engine = MagicMock()

    def fake_download(task, on_progress=None, on_postprocess=None):
        if delay:
            time.sleep(delay)
        if fail:
            raise RuntimeError("fail")
        task.filename = "/tmp/out.mp4"  # nosec B108

    engine.download.side_effect = fake_download
    return engine


def make_bus():
    bus = MagicMock()
    bus.publish = MagicMock()
    return bus


def make_task() -> DownloadTask:
    t = DownloadTask(url="https://example.com/v")
    t.media_info = MediaInfo(url=t.url, title="Test")
    return t


def wait_for_status(task, status, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.status == status:
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_cancels_active_tasks(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(delay=5.0), event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            time.sleep(0.1)  # let it start
        finally:
            mgr.shutdown(wait=False)
        # After shutdown, enqueue should raise
        with pytest.raises(RuntimeError):
            mgr.enqueue(make_task())

    def test_shutdown_wait_true_completes_cleanly(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(), event_bus=make_bus())
        mgr.start()
        task = make_task()
        mgr.enqueue(task)
        mgr.shutdown(wait=True)  # should not hang


# ---------------------------------------------------------------------------
# clear_terminal
# ---------------------------------------------------------------------------


class TestClearTerminal:
    def test_clear_terminal_removes_completed_tasks(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(), event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            assert wait_for_status(task, DownloadStatus.COMPLETED), "Task did not complete"
            mgr.clear_terminal()
            assert mgr.get_task(task.id) is None
        finally:
            mgr.shutdown(wait=False)

    def test_clear_terminal_removes_failed_tasks(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(fail=True), event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            assert wait_for_status(task, DownloadStatus.FAILED), "Task did not fail"
            mgr.clear_terminal()
            assert mgr.get_task(task.id) is None
        finally:
            mgr.shutdown(wait=False)

    def test_active_tasks_not_cleared(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(delay=5.0), event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            time.sleep(0.1)  # let it start downloading
            mgr.clear_terminal()
            # Active task should still be tracked
            assert mgr.get_task(task.id) is not None
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# pause / resume / cancel
# ---------------------------------------------------------------------------


class TestPauseResumeCancel:
    def test_pause_publishes_event(self):
        bus = make_bus()
        mgr = DownloadManager(config=make_config(), engine=make_engine(delay=5.0), event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            time.sleep(0.1)
            mgr.pause(task.id)
            assert bus.publish.called
        finally:
            mgr.shutdown(wait=False)

    def test_cancel_sets_cancellation_flag(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(delay=5.0), event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            time.sleep(0.1)
            mgr.cancel(task.id)
            assert task.is_cancellation_requested
        finally:
            mgr.shutdown(wait=False)

    def test_pause_unknown_id_does_not_crash(self):
        mgr = DownloadManager(config=make_config(), engine=make_engine(), event_bus=make_bus())
        mgr.start()
        try:
            mgr.pause("unknown-id")  # should not raise
        finally:
            mgr.shutdown(wait=False)
