"""
tests/test_download_manager.py
Unit tests for infrastructure/downloader/download_manager.py

Covers:
- Injected engine is used (Issue #6)
- enqueue() is thread-safe (Issue #15)
- get_task() O(1) lookup
- shutdown() cancels active tasks
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.download_manager import DownloadManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(max_concurrent=2, max_retries=1):
    cfg = MagicMock()
    cfg.max_concurrent = max_concurrent
    cfg.max_retries = max_retries
    return cfg


def make_engine(fail=False, delay=0.0):
    engine = MagicMock()
    def fake_download(task, on_progress=None, on_postprocess=None):
        if delay:
            time.sleep(delay)
        if fail:
            raise RuntimeError("Simulated failure")
        task.filename = "/tmp/fake.mp4"  # nosec B108
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDownloadManagerDI:
    def test_uses_injected_engine(self):
        """Issue #6 — DownloadManager must use the engine passed in, not create one."""
        cfg = make_config()
        engine = make_engine()
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            # Wait for completion
            deadline = time.time() + 5
            while task.status not in DownloadStatus.terminal_states():
                time.sleep(0.05)
                if time.time() > deadline:
                    break
            engine.download.assert_called_once()
        finally:
            mgr.shutdown(wait=False)


class TestEnqueueThreadSafety:
    def test_concurrent_enqueues_all_tracked(self):
        """Issue #15 — all tasks enqueued concurrently should be tracked."""
        cfg = make_config(max_concurrent=8)
        engine = make_engine(delay=0.01)
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            tasks = [make_task() for _ in range(10)]
            threads = [threading.Thread(target=mgr.enqueue, args=(t,)) for t in tasks]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            # All task IDs should be tracked
            tracked_ids = {t.id for t in mgr.get_all_tasks()}
            for task in tasks:
                assert task.id in tracked_ids
        finally:
            mgr.shutdown(wait=False)

    def test_enqueue_after_shutdown_raises(self):
        cfg = make_config()
        engine = make_engine()
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        mgr.shutdown(wait=True)
        with pytest.raises(RuntimeError):
            mgr.enqueue(make_task())


class TestGetTask:
    def test_get_task_returns_correct_task(self):
        cfg = make_config()
        engine = make_engine(delay=0.5)
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            found = mgr.get_task(task.id)
            assert found is task
        finally:
            mgr.shutdown(wait=False)

    def test_get_task_unknown_id_returns_none(self):
        cfg = make_config()
        engine = make_engine()
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            assert mgr.get_task("nonexistent") is None
        finally:
            mgr.shutdown(wait=False)


class TestTaskLifecycle:
    def test_successful_task_becomes_completed(self):
        cfg = make_config()
        engine = make_engine()
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 5
            while task.status != DownloadStatus.COMPLETED:
                time.sleep(0.05)
                assert time.time() < deadline, "Task did not complete in time"
            assert task.progress == 100.0
        finally:
            mgr.shutdown(wait=False)

    def test_failed_task_has_error_msg(self):
        # max_retries=0: task fails immediately without retry sleep,
        # keeping the test fast.  Retry behaviour is tested separately.
        cfg = make_config(max_retries=0)
        engine = make_engine(fail=True)
        bus = make_bus()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 5
            while task.status != DownloadStatus.FAILED:
                time.sleep(0.05)
                assert time.time() < deadline, "Task did not fail in time"
            assert task.error_msg != ""
        finally:
            mgr.shutdown(wait=False)


class TestRetryBehavior:
    """Verify the automatic retry logic introduced in _run_task."""

    def test_transient_error_retried_then_succeeds(self):
        """Engine fails once then succeeds — task ends COMPLETED."""
        call_count = {"n": 0}

        engine = MagicMock()
        def flaky_download(task, on_progress=None, on_postprocess=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Network timeout")   # transient
            task.filename = "/tmp/ok.mp4"  # nosec B108
        engine.download.side_effect = flaky_download

        cfg = make_config(max_retries=2)
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 10
            while task.status not in DownloadStatus.terminal_states():
                time.sleep(0.05)
                assert time.time() < deadline, "Timed out waiting for completion"
            assert task.status == DownloadStatus.COMPLETED
            assert call_count["n"] == 2   # 1 failure + 1 success
        finally:
            mgr.shutdown(wait=False)

    def test_hard_error_not_retried(self):
        """Private-video error must fail immediately — no retry."""
        cfg = make_config(max_retries=3)
        engine = MagicMock()
        engine.download.side_effect = RuntimeError(
            "Content is private. Try enabling cookies."
        )
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 5
            while task.status not in DownloadStatus.terminal_states():
                time.sleep(0.05)
                assert time.time() < deadline, "Timed out"
            assert task.status == DownloadStatus.FAILED
            assert engine.download.call_count == 1   # never retried
        finally:
            mgr.shutdown(wait=False)

    def test_tiktok_not_currently_live_not_retried(self):
        """TikTok 'not currently live' must fail immediately — no retry."""
        cfg = make_config(max_retries=3)
        engine = MagicMock()
        engine.download.side_effect = RuntimeError(
            "ERROR: [tiktok:live] gwh2026: The channel is not currently live"
        )
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 5
            while task.status not in DownloadStatus.terminal_states():
                time.sleep(0.05)
                assert time.time() < deadline, "Timed out"
            assert task.status == DownloadStatus.FAILED
            assert engine.download.call_count == 1   # never retried
        finally:
            mgr.shutdown(wait=False)

    def test_cancelled_during_backoff_stops_cleanly(self):
        """Cancelling while waiting between retries → CANCELLED, not FAILED."""
        cfg = make_config(max_retries=3)
        engine = MagicMock()
        engine.download.side_effect = RuntimeError("Network timeout")

        mgr = DownloadManager(config=cfg, engine=engine, event_bus=make_bus())
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            # Let first attempt fail, then cancel during back-off
            deadline = time.time() + 3
            while engine.download.call_count < 1:
                time.sleep(0.02)
                assert time.time() < deadline, "First attempt never ran"
            task.cancel()
            deadline = time.time() + 5
            while task.status not in DownloadStatus.terminal_states():
                time.sleep(0.05)
                assert time.time() < deadline, "Timed out waiting for cancellation"
            assert task.status == DownloadStatus.CANCELLED
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Branch coverage: _on_progress, _on_future_done with exception
# ---------------------------------------------------------------------------

class TestProgressAndFutureDone:
    def test_on_progress_publishes_event(self):
        """_on_progress must publish DOWNLOAD_PROGRESS event."""
        from app.event_bus import EventBus
        bus = make_bus()
        mgr = DownloadManager(config=make_config(), engine=make_engine(), event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr._on_progress(task)
            bus.publish.assert_called_with(EventBus.DOWNLOAD_PROGRESS, task=task)
        finally:
            mgr.shutdown()

    def test_on_future_done_logs_escaped_exception(self):
        """_on_future_done must log unhandled exceptions that escape _run_task."""
        import concurrent.futures

        bus = make_bus()
        mgr = DownloadManager(config=make_config(), engine=make_engine(), event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            # Create a future that has an exception result
            f = concurrent.futures.Future()
            f.set_exception(RuntimeError("escaped!"))
            # Should not raise — only logs
            mgr._on_future_done(task.id, f)
        finally:
            mgr.shutdown()


class TestCancelBeforeFirstAttempt:
    def test_cancel_while_queued_skips_download(self):
        """Cancelling a task before the first attempt must hit the break at line 213."""
        import time

        from domain.enums.download_status import DownloadStatus

        engine = MagicMock()

        # Make download() block until the event is set, ensuring cancel() fires first
        start_event = threading.Event()

        def slow_download(task, on_progress=None, on_postprocess=None):
            start_event.wait(timeout=5)

        engine.download.side_effect = slow_download

        bus = make_bus()
        cfg = make_config(max_concurrent=1, max_retries=0)
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            # Cancel BEFORE enqueueing so is_cancellation_requested is True
            # when _run_task checks on the first iteration
            task.cancel()
            mgr.enqueue(task)
            # Give the worker thread time to process
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if task.status == DownloadStatus.CANCELLED:
                    break
                time.sleep(0.02)
            # engine.download must NOT have been called
            engine.download.assert_not_called()
        finally:
            start_event.set()
            mgr.shutdown()


class TestPauseResumeCancelGetAll:
    """Cover lines 112-150: pause, resume, cancel, get_all_tasks, clear_terminal."""

    def _make_mgr(self):
        bus = make_bus()
        mgr = DownloadManager(config=make_config(), engine=make_engine(), event_bus=bus)
        mgr.start()
        return mgr, bus

    def test_pause_publishes_progress(self):
        from app.event_bus import EventBus
        mgr, bus = self._make_mgr()
        try:
            task = make_task()
            mgr.enqueue(task)
            mgr.pause(task.id)
            calls = [c for c in bus.publish.call_args_list if c[0][0] == EventBus.DOWNLOAD_PROGRESS]
            assert any(c[1].get("task") is task for c in calls)
        finally:
            mgr.shutdown()

    def test_pause_unknown_id_is_noop(self):
        mgr, _ = self._make_mgr()
        try:
            mgr.pause("nonexistent-id")  # must not raise
        finally:
            mgr.shutdown()

    def test_resume_publishes_progress(self):
        from app.event_bus import EventBus
        mgr, bus = self._make_mgr()
        try:
            task = make_task()
            mgr.enqueue(task)
            mgr.resume(task.id)
            calls = [c for c in bus.publish.call_args_list if c[0][0] == EventBus.DOWNLOAD_PROGRESS]
            assert any(c[1].get("task") is task for c in calls)
        finally:
            mgr.shutdown()

    def test_resume_unknown_id_is_noop(self):
        mgr, _ = self._make_mgr()
        try:
            mgr.resume("nonexistent-id")  # must not raise
        finally:
            mgr.shutdown()

    def test_cancel_sets_cancellation(self):
        mgr, _ = self._make_mgr()
        try:
            task = make_task()
            mgr.enqueue(task)
            mgr.cancel(task.id)
            assert task.is_cancellation_requested
        finally:
            mgr.shutdown()

    def test_cancel_unknown_id_is_noop(self):
        mgr, _ = self._make_mgr()
        try:
            mgr.cancel("nonexistent-id")  # must not raise
        finally:
            mgr.shutdown()

    def test_get_all_tasks_returns_enqueued(self):
        mgr, _ = self._make_mgr()
        try:
            t1 = make_task()
            t2 = make_task()
            mgr.enqueue(t1)
            mgr.enqueue(t2)
            all_tasks = mgr.get_all_tasks()
            ids = {t.id for t in all_tasks}
            assert t1.id in ids and t2.id in ids
        finally:
            mgr.shutdown()

    def test_clear_terminal_removes_completed(self):
        import time

        from domain.enums.download_status import DownloadStatus
        engine = make_engine()
        engine.download.return_value = None  # instant success
        bus = make_bus()
        mgr = DownloadManager(config=make_config(max_retries=0), engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 3.0
            while time.time() < deadline and task.status not in DownloadStatus.terminal_states():
                time.sleep(0.02)
            mgr.clear_terminal()
            assert task.id not in {t.id for t in mgr.get_all_tasks()}
        finally:
            mgr.shutdown()

    def test_clear_terminal_respects_exclude_ids(self):
        import time

        from domain.enums.download_status import DownloadStatus
        engine = make_engine()
        engine.download.return_value = None
        bus = make_bus()
        mgr = DownloadManager(config=make_config(max_retries=0), engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = make_task()
            mgr.enqueue(task)
            deadline = time.time() + 3.0
            while time.time() < deadline and task.status not in DownloadStatus.terminal_states():
                time.sleep(0.02)
            mgr.clear_terminal(exclude_ids=frozenset({task.id}))
            assert task.id in {t.id for t in mgr.get_all_tasks()}
        finally:
            mgr.shutdown()
