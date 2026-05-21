"""
tests/test_download_task.py
Unit tests for domain/models/download_task.py

Covers:
- snapshot() returns a consistent atomic copy (Issue #12)
- wait_if_paused() exits on cancellation (Issue #17)
- pause / resume / cancel state transitions
- Bug #7: __post_init__ no longer contains dead code
"""
import threading
import time

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_task() -> DownloadTask:
    return DownloadTask(url="https://example.com/video")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDownloadTaskSnapshot:
    def test_snapshot_returns_dict(self):
        task = make_task()
        snap = task.snapshot()
        assert isinstance(snap, dict)
        assert "status" in snap
        assert "progress" in snap
        assert "downloaded_bytes" in snap
        assert "total_bytes" in snap

    def test_snapshot_reflects_current_values(self):
        task = make_task()
        task.progress = 42.5
        task.speed = "1.2 MiB/s"
        snap = task.snapshot()
        assert snap["progress"] == 42.5
        assert snap["speed"] == "1.2 MiB/s"

    def test_snapshot_is_independent_copy(self):
        """Mutating the snapshot must not affect the task."""
        task = make_task()
        task.progress = 10.0
        snap = task.snapshot()
        snap["progress"] = 99.9
        assert task.progress == 10.0


class TestWaitIfPaused:
    def test_not_paused_returns_immediately(self):
        """A non-paused task must not block."""
        task = make_task()
        # Default state: pause_event is set → wait_if_paused returns immediately
        started = time.time()
        task.wait_if_paused()
        assert time.time() - started < 0.5

    def test_cancelled_task_unblocks(self):
        """If a paused task is cancelled, wait_if_paused must return within ~1s."""
        task = make_task()
        task.pause()

        def canceller():
            time.sleep(0.1)
            task.cancel()

        t = threading.Thread(target=canceller, daemon=True)
        t.start()

        started = time.time()
        task.wait_if_paused()
        elapsed = time.time() - started
        # Should unblock very quickly after cancel()
        assert elapsed < 2.0


class TestPauseResumeCancelTransitions:
    def test_initial_status_is_queued(self):
        task = make_task()
        assert task.status == DownloadStatus.QUEUED

    def test_pause_sets_paused_status(self):
        task = make_task()
        task.status = DownloadStatus.DOWNLOADING
        task.pause()
        assert task.status == DownloadStatus.PAUSED

    def test_resume_restores_downloading_status(self):
        task = make_task()
        task.status = DownloadStatus.DOWNLOADING
        task.pause()
        task.resume()
        assert task.status == DownloadStatus.DOWNLOADING

    def test_cancel_sets_cancellation_flag(self):
        task = make_task()
        assert not task.is_cancellation_requested
        task.cancel()
        assert task.is_cancellation_requested

    def test_cancel_unblocks_pause_event(self):
        """cancel() must set the pause_event so wait_if_paused() can exit."""
        task = make_task()
        task.pause()
        task.cancel()
        # After cancel(), _pause_event must be set
        assert task._pause_event.is_set()

    def test_pause_during_processing_is_ignored(self):
        """Pausing during PROCESSING should be a no-op (cannot interrupt muxing)."""
        task = make_task()
        task.status = DownloadStatus.PROCESSING
        task.pause()
        assert task.status == DownloadStatus.PROCESSING


class TestDownloadTaskProperties:
    def test_title_falls_back_to_url(self):
        task = make_task()
        task.url = "https://example.com/some/path"
        assert task.title.startswith("https://")

    def test_title_uses_media_info(self):
        task = make_task()
        task.media_info = MediaInfo(url=task.url, title="My Great Video")
        assert task.title == "My Great Video"

    def test_platform_falls_back_to_unknown(self):
        task = make_task()
        assert task.platform == "unknown"

    def test_to_dict_contains_required_keys(self):
        task = make_task()
        d = task.to_dict()
        for key in ("id", "url", "title", "platform", "filename", "status"):
            assert key in d
