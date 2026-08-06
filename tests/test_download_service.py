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

import pytest

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

    mocks = dict(config=config, manager=manager, history=history, engine=engine, bus=bus)
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
        assert self._wait_history_add(mocks["history"]), "history.add not called on COMPLETED"
        mocks["history"].add.assert_called_with(task)

    def test_failed_event_saves_to_history(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        task = self._make_task()
        mocks["bus"].publish(EventBus.DOWNLOAD_FAILED, task=task)
        assert self._wait_history_add(mocks["history"]), "history.add not called on FAILED"
        mocks["history"].add.assert_called_with(task)

    def test_cancelled_event_saves_to_history(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        task = self._make_task()
        mocks["bus"].publish(EventBus.DOWNLOAD_CANCELLED, task=task)
        assert self._wait_history_add(mocks["history"]), "history.add not called on CANCELLED"
        mocks["history"].add.assert_called_with(task)


# ---------------------------------------------------------------------------
# Branch coverage: _should_fallback_to_gallery_dl, taildrop, duplicate guard
# ---------------------------------------------------------------------------


class TestShouldFallbackToGalleryDl:
    """app/services/download_service._should_fallback_to_gallery_dl coverage."""

    def test_non_gallery_url_returns_false(self):
        from unittest.mock import patch

        from app.services.download_service import _should_fallback_to_gallery_dl

        with patch(
            "infrastructure.downloader.gallery_dl_engine.is_gallery_dl_url",
            return_value=False,
        ):
            assert _should_fallback_to_gallery_dl("https://youtube.com/x", "no video") is False

    def test_gallery_url_with_photo_error_returns_true(self):
        from unittest.mock import patch

        from app.services.download_service import _PHOTO_ERRORS, _should_fallback_to_gallery_dl

        # Use the first known photo-error keyword
        error_kw = next(iter(_PHOTO_ERRORS))
        with patch(
            "infrastructure.downloader.gallery_dl_engine.is_gallery_dl_url",
            return_value=True,
        ):
            assert _should_fallback_to_gallery_dl("https://instagram.com/p/x", error_kw) is True

    def test_gallery_url_without_photo_error_returns_false(self):
        from unittest.mock import patch

        from app.services.download_service import _should_fallback_to_gallery_dl

        with patch(
            "infrastructure.downloader.gallery_dl_engine.is_gallery_dl_url",
            return_value=True,
        ):
            assert _should_fallback_to_gallery_dl("https://instagram.com/p/x", "rate limit") is False


# ---------------------------------------------------------------------------
# BUG-FB-PHOTO: facebook.com added to gallery-dl's supported-platform regex
# so photo-only Facebook posts fall back to gallery-dl instead of failing.
# Uses the real (unmocked) regex to catch regressions in the pattern itself.
# ---------------------------------------------------------------------------


class TestGalleryDlFacebookRouting:
    def test_facebook_photo_post_is_gallery_dl_url(self):
        from infrastructure.downloader.gallery_dl_engine import is_gallery_dl_url

        assert is_gallery_dl_url("https://www.facebook.com/someuser/photos/a.123/456/") is True

    def test_facebook_photo_php_is_gallery_dl_url(self):
        from infrastructure.downloader.gallery_dl_engine import is_gallery_dl_url

        assert is_gallery_dl_url("https://www.facebook.com/photo.php?fbid=123") is True

    def test_facebook_photo_post_falls_back_on_photo_error(self):
        """Real (unmocked) is_gallery_dl_url: FB photo-only error triggers fallback."""
        from app.services.download_service import _should_fallback_to_gallery_dl

        assert (
            _should_fallback_to_gallery_dl(
                "https://www.facebook.com/someuser/photos/a.123/456/",
                "There is no video in this post",
            )
            is True
        )

    def test_facebook_video_error_does_not_fall_back(self):
        """A non-photo error on a Facebook URL must NOT trigger the gallery-dl fallback."""
        from app.services.download_service import _should_fallback_to_gallery_dl

        assert (
            _should_fallback_to_gallery_dl("https://www.facebook.com/watch/?v=123456", "Network timeout")
            is False
        )

    def test_facebook_story_url_not_routed_to_gallery_dl_story_check(self):
        """Story URLs must keep going through facebook_story_engine, not gallery-dl.

        is_gallery_dl_url() now also matches facebook.com generically, but
        download_manager checks is_facebook_story_url() BEFORE gallery/yt-dlp
        routing, so /stories/ URLs are unaffected by this widening.
        """
        from infrastructure.downloader.facebook_story_engine import is_facebook_story_url
        from infrastructure.downloader.gallery_dl_engine import is_gallery_dl_url

        story_url = "https://www.facebook.com/stories/1234567890/"
        assert is_facebook_story_url(story_url) is True
        # gallery_dl_engine's regex matches facebook.com generically too, but
        # routing order (checked in download_manager) gives story precedence.
        assert is_gallery_dl_url(story_url) is True

    def test_facebook_video_post_url_not_a_story(self):
        from infrastructure.downloader.facebook_story_engine import is_facebook_story_url

        assert is_facebook_story_url("https://www.facebook.com/watch/?v=123456") is False


class TestTaildropProperty:
    def test_taildrop_property_returns_service(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        # taildrop is a MagicMock attribute on the config mock
        assert service.taildrop is service._taildrop


class TestDuplicateUrlGuard:
    def test_duplicate_active_url_returns_existing_task(self, tmp_path):
        from domain.enums.download_status import DownloadStatus

        service, mocks = make_service(download_dir=tmp_path)

        existing = MagicMock()
        existing.url = "https://youtube.com/watch?v=dup"
        existing.status = DownloadStatus.DOWNLOADING

        mocks["manager"].get_all_tasks.return_value = [existing]

        result = service.start_download(
            url="https://youtube.com/watch?v=dup",
            media_info=make_media_info(url="https://youtube.com/watch?v=dup"),
            format_id="bestvideo+bestaudio",
            output_ext="mp4",
        )
        # Must return the existing task, not enqueue a new one
        assert result is existing
        mocks["manager"].enqueue.assert_not_called()


class TestFetchThumbnailDelegation:
    def test_fetch_thumbnail_delegates_to_thumbnail_service(self, tmp_path):
        from unittest.mock import patch as _patch

        service, mocks = make_service(download_dir=tmp_path)
        on_done = MagicMock()
        on_error = MagicMock()
        with _patch.object(service._thumbnail_svc, "fetch_async") as mock_fetch:
            service.fetch_thumbnail(
                url="https://img.example.com/thumb.jpg",
                width=120,
                height=90,
                on_done=on_done,
                on_error=on_error,
            )
            mock_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# Gallery-dl fallback in analyse_url (lines 127-138 coverage)
# ---------------------------------------------------------------------------


class TestAnalyseUrlGalleryDlFallback:
    """When yt-dlp fails with a photo-error on a gallery-dl URL, fall back."""

    def _wait_cb(self, cb_list, timeout=3.0):
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if cb_list:
                return True
            time.sleep(0.01)
        return False

    def test_fallback_succeeds_calls_on_done(self, tmp_path):
        from unittest.mock import MagicMock
        from unittest.mock import patch as _patch

        from app.services.download_service import _PHOTO_ERRORS

        gallery_info = make_media_info(url="https://instagram.com/p/x")
        gallery_engine = MagicMock()
        gallery_engine.extract_info.return_value = gallery_info

        photo_err = next(iter(_PHOTO_ERRORS))
        service, mocks = make_service(
            engine_error=photo_err,
            download_dir=tmp_path,
        )
        service._gallery_engine = gallery_engine

        done = []
        errors = []

        with _patch(
            "infrastructure.downloader.gallery_dl_engine.is_gallery_dl_url",
            return_value=True,
        ):
            service.analyse_url(
                "https://instagram.com/p/x",
                on_done=done.append,
                on_error=errors.append,
            )

        assert self._wait_cb(done)
        assert done[0] is gallery_info
        assert not errors

    def test_fallback_fails_calls_on_error(self, tmp_path):
        from unittest.mock import MagicMock
        from unittest.mock import patch as _patch

        from app.services.download_service import _PHOTO_ERRORS

        gallery_engine = MagicMock()
        gallery_engine.extract_info.side_effect = RuntimeError("gdl failed")

        photo_err = next(iter(_PHOTO_ERRORS))
        service, mocks = make_service(
            engine_error=photo_err,
            download_dir=tmp_path,
        )
        service._gallery_engine = gallery_engine

        errors = []

        with _patch(
            "infrastructure.downloader.gallery_dl_engine.is_gallery_dl_url",
            return_value=True,
        ):
            service.analyse_url(
                "https://instagram.com/p/x",
                on_done=lambda i: None,
                on_error=errors.append,
            )

        assert self._wait_cb(errors)
        assert "gdl failed" in errors[0]


# ---------------------------------------------------------------------------
# rename_download
# ---------------------------------------------------------------------------


class TestRenameDownload:
    def _task(self, tmp_path, filename):
        task = DownloadTask(url="https://example.com/video", output_dir=str(tmp_path))
        task.filename = str(filename)
        return task

    def test_renames_file_and_updates_in_memory_task(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        old_file = tmp_path / "old.mp4"
        old_file.write_bytes(b"data")
        task = self._task(tmp_path, old_file)
        mocks["manager"].get_task.return_value = task
        mocks["history"].get_by_id.return_value = None

        new_path = service.rename_download(task.id, "new.mp4")

        assert Path(new_path).name == "new.mp4"
        assert not old_file.exists()
        assert Path(new_path).exists()
        assert task.filename == new_path
        mocks["history"].update_filename.assert_called_once_with(task.id, new_path)

    def test_falls_back_to_history_entry_when_task_gone(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        old_file = tmp_path / "old.mp4"
        old_file.write_bytes(b"data")
        mocks["manager"].get_task.return_value = None
        mocks["history"].get_by_id.return_value = {
            "filename": str(old_file),
            "output_dir": str(tmp_path),
        }

        new_path = service.rename_download("task-1", "renamed.mp4")

        assert Path(new_path).name == "renamed.mp4"
        assert Path(new_path).exists()

    def test_raises_file_not_found_when_no_record(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        mocks["manager"].get_task.return_value = None
        mocks["history"].get_by_id.return_value = None

        with pytest.raises(FileNotFoundError):
            service.rename_download("task-1", "new.mp4")

    def test_raises_file_not_found_when_disk_file_missing(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        task = self._task(tmp_path, tmp_path / "missing.mp4")
        mocks["manager"].get_task.return_value = task
        mocks["history"].get_by_id.return_value = None

        with pytest.raises(FileNotFoundError):
            service.rename_download(task.id, "new.mp4")

    def test_raises_file_exists_when_target_taken(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        old_file = tmp_path / "old.mp4"
        old_file.write_bytes(b"data")
        (tmp_path / "taken.mp4").write_bytes(b"other")
        task = self._task(tmp_path, old_file)
        mocks["manager"].get_task.return_value = task
        mocks["history"].get_by_id.return_value = None

        with pytest.raises(FileExistsError):
            service.rename_download(task.id, "taken.mp4")

    def test_raises_value_error_for_directory(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        gallery_dir = tmp_path / "gallery"
        gallery_dir.mkdir()
        task = self._task(tmp_path, gallery_dir)
        mocks["manager"].get_task.return_value = task
        mocks["history"].get_by_id.return_value = None

        with pytest.raises(ValueError):
            service.rename_download(task.id, "new_name")

    def test_same_name_is_a_noop(self, tmp_path):
        service, mocks = make_service(download_dir=tmp_path)
        old_file = tmp_path / "same.mp4"
        old_file.write_bytes(b"data")
        task = self._task(tmp_path, old_file)
        mocks["manager"].get_task.return_value = task
        mocks["history"].get_by_id.return_value = None

        new_path = service.rename_download(task.id, "same.mp4")

        assert Path(new_path) == old_file
        assert old_file.exists()
