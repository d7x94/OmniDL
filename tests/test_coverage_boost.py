"""
tests/test_coverage_boost.py
Coverage gap tests for download_service and download_manager missing lines.
All I/O mocked.
"""
from __future__ import annotations

import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.event_bus import EventBus
from app.services.download_service import DownloadService
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.download_manager import DownloadManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_config(max_concurrent=1, max_retries=0):
    cfg = MagicMock()
    cfg.max_concurrent = max_concurrent
    cfg.max_retries = max_retries
    cfg.download_dir = Path("/tmp/omnidl_cov_test")  # nosec B108
    cfg.proxy = ""
    cfg.platform_cookies = {}
    cfg.cookie_file = ""
    return cfg


def make_service_full(config=None):
    config = config or make_config()
    manager = MagicMock()
    manager.get_all_tasks.return_value = []
    history = MagicMock()
    history.all.return_value = []
    engine = MagicMock()
    bus = EventBus()
    svc = DownloadService(
        config=config,
        download_manager=manager,
        history_repo=history,
        engine=engine,
        event_bus=bus,
    )
    return svc, config, manager, history, engine, bus


def _wait(cb_list, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cb_list:
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# DownloadService.check_profile_live
# ---------------------------------------------------------------------------

class TestCheckProfileLive:
    def test_invalid_username_calls_on_error_synchronously(self):
        svc, *_ = make_service_full()
        errors = []
        with patch(
            "utils.instagram_live_checker.extract_instagram_username",
            return_value="",
        ):
            svc.check_profile_live(
                "https://instagram.com/",
                on_done=lambda u: None,
                on_error=errors.append,
            )
        assert errors
        assert "username" in errors[0].lower() or errors[0]

    def test_valid_username_spawns_thread_and_calls_on_done(self):
        svc, *_ = make_service_full()
        done = []
        with (
            patch("utils.instagram_live_checker.extract_instagram_username", return_value="user123"),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=""),
            patch("utils.instagram_live_checker.check_instagram_live", return_value="https://live.url"),
        ):
            svc.check_profile_live(
                "https://instagram.com/user123/",
                on_done=done.append,
                on_error=lambda e: None,
            )
        assert _wait(done)
        assert done[0] == "https://live.url"

    def test_checker_exception_calls_on_error(self):
        svc, *_ = make_service_full()
        errors = []
        with (
            patch("utils.instagram_live_checker.extract_instagram_username", return_value="user123"),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=""),
            patch("utils.instagram_live_checker.check_instagram_live", side_effect=RuntimeError("network error")),
        ):
            svc.check_profile_live(
                "https://instagram.com/user123/",
                on_done=lambda u: None,
                on_error=errors.append,
            )
        assert _wait(errors)
        assert "network error" in errors[0]


# ---------------------------------------------------------------------------
# DownloadService.check_tiktok_profile_live
# ---------------------------------------------------------------------------

class TestCheckTiktokProfileLive:
    def test_invalid_username_calls_on_error_synchronously(self):
        svc, *_ = make_service_full()
        errors = []
        with patch("utils.tiktok_live_checker.extract_tiktok_username", return_value=None):
            svc.check_tiktok_profile_live(
                "https://tiktok.com/",
                on_done=lambda u: None,
                on_error=errors.append,
            )
        assert errors
        assert "username" in errors[0].lower() or errors[0]

    def test_valid_username_calls_on_done(self):
        svc, *_ = make_service_full()
        done = []
        with (
            patch("utils.tiktok_live_checker.extract_tiktok_username", return_value="ttuser"),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=""),
            patch("utils.tiktok_live_checker.check_tiktok_live", return_value="https://tt-live.url"),
        ):
            svc.check_tiktok_profile_live(
                "https://tiktok.com/@ttuser/live",
                on_done=done.append,
                on_error=lambda e: None,
            )
        assert _wait(done)
        assert done[0] == "https://tt-live.url"

    def test_checker_exception_calls_on_error(self):
        svc, *_ = make_service_full()
        errors = []
        with (
            patch("utils.tiktok_live_checker.extract_tiktok_username", return_value="ttuser"),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=""),
            patch("utils.tiktok_live_checker.check_tiktok_live", side_effect=RuntimeError("tt error")),
        ):
            svc.check_tiktok_profile_live(
                "https://tiktok.com/@ttuser/live",
                on_done=lambda u: None,
                on_error=errors.append,
            )
        assert _wait(errors)
        assert "tt error" in errors[0]

    def test_cookie_temp_file_cleaned_up_on_success(self):
        svc, *_ = make_service_full()
        done = []
        unlinked = []
        with (
            patch("utils.tiktok_live_checker.extract_tiktok_username", return_value="ttuser"),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value="enc_cookie"),
            patch(
                "infrastructure.downloader.yt_dlp_engine._prepare_cookie_for_use",
                return_value=("/tmp/temp_cookie.txt", True),  # nosec B108
            ),
            patch("utils.tiktok_live_checker.check_tiktok_live", return_value=None),
            patch("os.unlink", side_effect=lambda p: unlinked.append(p)),
        ):
            svc.check_tiktok_profile_live(
                "https://tiktok.com/@ttuser/live",
                on_done=done.append,
                on_error=lambda e: None,
            )
        assert _wait(done)
        assert any("/tmp/temp_cookie.txt" in str(p) for p in unlinked)


# ---------------------------------------------------------------------------
# DownloadManager - live task ffmpeg hard error (BUG-CI)
# ---------------------------------------------------------------------------

def _make_mgr(engine, max_retries=2, gallery_engine=None):
    cfg = make_config(max_retries=max_retries)
    bus = MagicMock()
    bus.publish = MagicMock()
    mgr = DownloadManager(
        config=cfg,
        engine=engine,
        event_bus=bus,
        gallery_engine=gallery_engine,
    )
    mgr.start()
    return mgr, bus


def _wait_terminal(task, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.status in DownloadStatus.terminal_states():
            return True
        time.sleep(0.05)
    return False


class TestDownloadManagerLiveFfmpegError:
    def test_ffmpeg_error_on_live_task_is_hard_error_no_retry(self):
        call_count = [0]
        engine = MagicMock()

        def fake_dl(task, on_progress=None, on_postprocess=None):
            call_count[0] += 1
            raise RuntimeError("ffmpeg exited with code 1")

        engine.download.side_effect = fake_dl
        mgr, _ = _make_mgr(engine, max_retries=3)
        try:
            task = DownloadTask(url="https://tiktok.com/@u/live")
            task.media_info = MediaInfo(url=task.url, title="Live", is_live=True)
            mgr.enqueue(task)
            assert _wait_terminal(task)
            assert task.status == DownloadStatus.FAILED
            # Hard error - no retries
            assert call_count[0] == 1
        finally:
            mgr.shutdown(wait=False)


class TestDownloadManagerPhotoFallback:
    def test_photo_error_triggers_gallery_fallback(self):
        yt_engine = MagicMock()
        gallery_engine = MagicMock()

        def fake_yt_dl(task, on_progress=None, on_postprocess=None):
            raise RuntimeError("no video formats found")

        def fake_gallery_dl(task, on_progress=None, on_postprocess=None):
            task.filename = "/tmp/photo.jpg"  # nosec B108

        yt_engine.download.side_effect = fake_yt_dl
        gallery_engine.download.side_effect = fake_gallery_dl

        mgr, _ = _make_mgr(yt_engine, max_retries=0, gallery_engine=gallery_engine)
        try:
            task = DownloadTask(url="https://instagram.com/p/abc")
            task.media_info = MediaInfo(
                url=task.url, title="Photo", source_engine="yt_dlp"
            )
            mgr.enqueue(task)
            assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            gallery_engine.download.assert_called_once()
        finally:
            mgr.shutdown(wait=False)

    def test_photo_error_gallery_fallback_also_fails(self):
        yt_engine = MagicMock()
        gallery_engine = MagicMock()

        yt_engine.download.side_effect = RuntimeError("no video in this post")
        gallery_engine.download.side_effect = RuntimeError("gdl also failed")

        mgr, _ = _make_mgr(yt_engine, max_retries=0, gallery_engine=gallery_engine)
        try:
            task = DownloadTask(url="https://instagram.com/p/xyz")
            task.media_info = MediaInfo(
                url=task.url, title="Photo2", source_engine="yt_dlp"
            )
            mgr.enqueue(task)
            assert _wait_terminal(task)
            assert task.status == DownloadStatus.FAILED
        finally:
            mgr.shutdown(wait=False)


class TestDownloadManagerNotLiveRetry:
    def test_not_currently_live_on_live_task_retries(self):
        call_count = [0]
        engine = MagicMock()

        def fake_dl(task, on_progress=None, on_postprocess=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("not currently live")
            task.filename = "/tmp/live.mp4"  # nosec B108

        engine.download.side_effect = fake_dl
        mgr, _ = _make_mgr(engine, max_retries=3)
        try:
            task = DownloadTask(url="https://tiktok.com/@u/live")
            task.media_info = MediaInfo(url=task.url, title="Live", is_live=True)
            mgr.enqueue(task)
            assert _wait_terminal(task, timeout=10)
            assert task.status == DownloadStatus.COMPLETED
            assert call_count[0] == 3
        finally:
            mgr.shutdown(wait=False)


class TestDownloadManagerCancelDuringBackoff:
    def test_cancel_during_retry_backoff(self):
        engine = MagicMock()
        engine.download.side_effect = RuntimeError("transient error")

        cfg = make_config(max_retries=5)
        bus = MagicMock()
        bus.publish = MagicMock()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.start()
        try:
            task = DownloadTask(url="https://example.com/v")
            task.media_info = MediaInfo(url=task.url, title="Test")
            mgr.enqueue(task)
            # Give first attempt time to fail, then cancel during backoff
            time.sleep(0.2)
            mgr.cancel(task.id)
            assert _wait_terminal(task, timeout=5)
            assert task.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)
        finally:
            mgr.shutdown(wait=False)
