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
        cfg = make_config()
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
