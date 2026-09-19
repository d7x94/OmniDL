"""
tests/test_coverage_boost.py
Coverage gap tests for download_service and download_manager missing lines.
All I/O mocked.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_valid_username_calls_on_done(self):
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

    def test_valid_username_calls_on_done(self):
        svc, *_ = make_service_full()
        done = []
        with (
            patch("utils.tiktok_live_checker.extract_tiktok_username", return_value="ttuser"),
            patch("app.services.download_service._resolve_cookie", return_value=""),
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
            patch("app.services.download_service._resolve_cookie", return_value=""),
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
        """Lines 544-550: finally block unlinks temp cookie file."""
        svc, *_ = make_service_full()
        done = []
        unlinked = []

        # The finally block does: import os as _os; _os.unlink(_tt_cookie_txt)
        # The local import binds to the real `os` module, so patch "os.unlink".

        def capturing_unlink(p):
            unlinked.append(p)

        with (
            patch("utils.tiktok_live_checker.extract_tiktok_username", return_value="ttuser"),
            patch("app.services.download_service._resolve_cookie", return_value="enc"),
            patch(
                "app.services.download_service._prepare_cookie_for_use",
                return_value=("/tmp/temp_tt_cookie.txt", True),  # nosec B108
            ),
            patch("utils.tiktok_live_checker.check_tiktok_live", return_value=None),
            patch("os.unlink", side_effect=capturing_unlink),
        ):
            svc.check_tiktok_profile_live(
                "https://tiktok.com/@ttuser/live",
                on_done=done.append,
                on_error=lambda e: None,
            )
        assert _wait(done)
        assert "/tmp/temp_tt_cookie.txt" in unlinked  # nosec B108

    def test_cookie_temp_file_cleaned_up_on_exception(self):
        """Lines 544-550: finally runs even when checker raises."""
        svc, *_ = make_service_full()
        errors = []
        unlinked = []

        with (
            patch("utils.tiktok_live_checker.extract_tiktok_username", return_value="ttuser"),
            patch("app.services.download_service._resolve_cookie", return_value="enc"),
            patch(
                "app.services.download_service._prepare_cookie_for_use",
                return_value=("/tmp/temp_tt_cookie2.txt", True),  # nosec B108
            ),
            patch("utils.tiktok_live_checker.check_tiktok_live", side_effect=RuntimeError("fail")),
            patch("os.unlink", side_effect=lambda p: unlinked.append(p)),
        ):
            svc.check_tiktok_profile_live(
                "https://tiktok.com/@ttuser/live",
                on_done=lambda u: None,
                on_error=errors.append,
            )
        assert _wait(errors)
        # on_error fires inside `except`, the unlink inside the `finally` that
        # follows it — waiting only on `errors` reads `unlinked` too early.
        assert _wait(unlinked)
        assert "/tmp/temp_tt_cookie2.txt" in unlinked  # nosec B108


# ---------------------------------------------------------------------------
# DownloadManager helpers
# ---------------------------------------------------------------------------

def _make_mgr(engine, max_retries=2, gallery_engine=None,
              story_engine_enabled=False, instagram_live_engine=None,
              kuaishou_engine=None):
    cfg = make_config(max_retries=max_retries)
    bus = MagicMock()
    bus.publish = MagicMock()
    mgr = DownloadManager(
        config=cfg,
        engine=engine,
        event_bus=bus,
        gallery_engine=gallery_engine,
        story_engine_enabled=story_engine_enabled,
        instagram_live_engine=instagram_live_engine,
        kuaishou_engine=kuaishou_engine,
    )
    mgr.start()
    return mgr, bus, cfg


def _wait_terminal(task, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.status in DownloadStatus.terminal_states():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# DownloadManager - live task ffmpeg hard error (BUG-CI)
# ---------------------------------------------------------------------------

class TestDownloadManagerLiveFfmpegError:
    def test_ffmpeg_error_on_live_task_is_hard_error_no_retry(self):
        call_count = [0]
        engine = MagicMock()

        def fake_dl(task, on_progress=None, on_postprocess=None):
            call_count[0] += 1
            raise RuntimeError("ffmpeg exited with code 1")

        engine.download.side_effect = fake_dl
        mgr, *_ = _make_mgr(engine, max_retries=3)
        try:
            task = DownloadTask(url="https://tiktok.com/@u/live")
            task.media_info = MediaInfo(url=task.url, title="Live", is_live=True)
            mgr.enqueue(task)
            assert _wait_terminal(task)
            assert task.status == DownloadStatus.FAILED
            assert call_count[0] == 1
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# DownloadManager - photo fallback (BUG-BU)
# ---------------------------------------------------------------------------

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

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, gallery_engine=gallery_engine)
        try:
            task = DownloadTask(url="https://instagram.com/p/abc")
            task.media_info = MediaInfo(url=task.url, title="Photo", source_engine="yt_dlp")
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

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, gallery_engine=gallery_engine)
        try:
            task = DownloadTask(url="https://instagram.com/p/xyz")
            task.media_info = MediaInfo(url=task.url, title="Photo2", source_engine="yt_dlp")
            mgr.enqueue(task)
            assert _wait_terminal(task)
            assert task.status == DownloadStatus.FAILED
        finally:
            mgr.shutdown(wait=False)

    def test_photo_fallback_orphan_cleanup_runs(self, tmp_path):
        """Lines 507-538: orphan cleanup deletes yt-dlp partial files after gallery-dl succeeds."""
        yt_engine = MagicMock()
        gallery_engine = MagicMock()

        orphan = tmp_path / "partial_ytdlp.mp4"
        orphan.write_bytes(b"fake")

        def fake_yt_dl(task, on_progress=None, on_postprocess=None):
            raise RuntimeError("no video formats found")

        def fake_gallery_dl(task, on_progress=None, on_postprocess=None):
            result = tmp_path / "slug" / "photo.jpg"
            result.parent.mkdir(exist_ok=True)
            result.write_bytes(b"photo")
            task.filename = str(result)
            task.gallery_dl_files = [str(result)]

        yt_engine.download.side_effect = fake_yt_dl
        gallery_engine.download.side_effect = fake_gallery_dl

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, gallery_engine=gallery_engine)
        try:
            task = DownloadTask(url="https://instagram.com/p/abc", output_dir=str(tmp_path))
            task.media_info = MediaInfo(url=task.url, title="Photo", source_engine="yt_dlp")
            mgr.enqueue(task)
            assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            assert not orphan.exists()
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# DownloadManager - not-live retry (BUG-CI transient)
# ---------------------------------------------------------------------------

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
        mgr, *_ = _make_mgr(engine, max_retries=3)
        try:
            task = DownloadTask(url="https://tiktok.com/@u/live")
            task.media_info = MediaInfo(url=task.url, title="Live", is_live=True)
            mgr.enqueue(task)
            assert _wait_terminal(task, timeout=10)
            assert task.status == DownloadStatus.COMPLETED
            assert call_count[0] == 3
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# DownloadManager - Kuaishou routing (lines 288-302)
# ---------------------------------------------------------------------------

class TestDownloadManagerKuaishouRouting:
    def test_kuaishou_url_routes_to_kuaishou_engine(self):
        yt_engine = MagicMock()
        ks_engine = MagicMock()

        def fake_ks_dl(task, on_progress=None, on_postprocess=None):
            task.filename = "/tmp/ks_video.mp4"  # nosec B108

        ks_engine.download.side_effect = fake_ks_dl

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, kuaishou_engine=ks_engine)
        try:
            task = DownloadTask(url="https://v.kuaishou.com/abc123")
            task.media_info = MediaInfo(url=task.url, title="KS video")
            with patch(
                "infrastructure.downloader.kuaishou_engine.is_kuaishou_url",
                return_value=True,
            ):
                mgr.enqueue(task)
                assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            ks_engine.download.assert_called_once()
            yt_engine.download.assert_not_called()
        finally:
            mgr.shutdown(wait=False)

    def test_kuaishou_source_engine_routes_to_kuaishou_engine(self):
        yt_engine = MagicMock()
        ks_engine = MagicMock()

        def fake_ks_dl(task, on_progress=None, on_postprocess=None):
            task.filename = "/tmp/ks_cdn.mp4"  # nosec B108

        ks_engine.download.side_effect = fake_ks_dl

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, kuaishou_engine=ks_engine)
        try:
            task = DownloadTask(url="https://cdn.kuaishoudelivery.com/video.mp4")
            task.media_info = MediaInfo(url=task.url, title="KS cdn", source_engine="kuaishou")
            with patch(
                "infrastructure.downloader.kuaishou_engine.is_kuaishou_url",
                return_value=False,
            ):
                mgr.enqueue(task)
                assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            ks_engine.download.assert_called_once()
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# DownloadManager - Facebook Story routing (lines 310-332)
# ---------------------------------------------------------------------------

class TestDownloadManagerFacebookStoryRouting:
    def test_facebook_story_url_routes_to_story_engine(self, tmp_path):
        yt_engine = MagicMock()
        result_file = tmp_path / "story.mp4"
        result_file.write_bytes(b"fake")

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, story_engine_enabled=True)
        try:
            task = DownloadTask(url="https://www.facebook.com/stories/123456")
            task.media_info = MediaInfo(url=task.url, title="FB Story")
            with (
                patch(
                    "infrastructure.downloader.facebook_story_engine.is_facebook_story_url",
                    return_value=True,
                ),
                patch(
                    "infrastructure.downloader.facebook_story_engine.download_story",
                    return_value=result_file,
                ),
            ):
                mgr.enqueue(task)
                assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            assert str(result_file) in task.filename
            yt_engine.download.assert_not_called()
        finally:
            mgr.shutdown(wait=False)


# ---------------------------------------------------------------------------
# DownloadManager - Instagram Live routing (lines 341-355)
# ---------------------------------------------------------------------------

class TestDownloadManagerInstagramLiveRouting:
    def test_instagram_live_url_routes_to_ig_live_engine(self):
        yt_engine = MagicMock()
        ig_engine = MagicMock()

        def fake_ig_dl(task, on_progress=None, on_postprocess=None):
            task.filename = "/tmp/ig_live.mp4"  # nosec B108

        ig_engine.download.side_effect = fake_ig_dl

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, instagram_live_engine=ig_engine)
        try:
            task = DownloadTask(url="https://www.instagram.com/user/live/")
            task.media_info = MediaInfo(url=task.url, title="IG Live")
            with patch(
                "infrastructure.downloader.instagram_live_engine.is_instagram_live_url",
                return_value=True,
            ):
                mgr.enqueue(task)
                assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            ig_engine.download.assert_called_once()
            yt_engine.download.assert_not_called()
        finally:
            mgr.shutdown(wait=False)

    def test_instagram_live_source_engine_routes_to_ig_live_engine(self):
        yt_engine = MagicMock()
        ig_engine = MagicMock()

        def fake_ig_dl(task, on_progress=None, on_postprocess=None):
            task.filename = "/tmp/ig_live2.mp4"  # nosec B108

        ig_engine.download.side_effect = fake_ig_dl

        mgr, *_ = _make_mgr(yt_engine, max_retries=0, instagram_live_engine=ig_engine)
        try:
            task = DownloadTask(url="https://example.com/stream.m3u8")
            task.media_info = MediaInfo(url=task.url, title="IG Live via source_engine",
                                        source_engine="instagram_live")
            with patch(
                "infrastructure.downloader.instagram_live_engine.is_instagram_live_url",
                return_value=False,
            ):
                mgr.enqueue(task)
                assert _wait_terminal(task)
            assert task.status == DownloadStatus.COMPLETED
            ig_engine.download.assert_called_once()
        finally:
            mgr.shutdown(wait=False)
