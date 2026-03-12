"""
tests/test_yt_dlp_engine_extra.py
Additional unit tests for infrastructure/downloader/yt_dlp_engine.py

Fills coverage gaps left by test_yt_dlp_engine.py:
- extract_info: success path, playlist handling, unsupported URL check
- extract_info: friendly error messages for private / 404 / unsupported
- _make_progress_hook: downloading status, finished status, eta/speed fields
- download opts: exponential backoff keys present (D2 fix)
- cookie_file: boundary check (S3 fix) — path inside/outside home dir
- _detect_platform: all known platforms + unknown
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine, _detect_platform


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(
    extra_args="",
    proxy="",
    cookie_file="",
    use_cookies=False,
    cookies_browser="chrome",
    max_retries=3,
    embed_thumbnail=False,
    embed_metadata=False,
    download_dir=None,
):
    cfg = MagicMock()
    cfg.extra_args = extra_args
    cfg.proxy = proxy
    cfg.cookie_file = cookie_file
    cfg.use_cookies = use_cookies
    cfg.cookies_browser = cookies_browser
    cfg.max_retries = max_retries
    cfg.embed_thumbnail = embed_thumbnail
    cfg.embed_metadata = embed_metadata
    cfg.download_dir = download_dir or Path("/tmp")  # nosec B108
    return cfg


def make_task(url="https://youtube.com/watch?v=test") -> DownloadTask:
    task = DownloadTask(url=url, format_id="best", output_ext="mp4")
    task.media_info = MediaInfo(url=url, title="Test Video")
    return task


def fake_ydl_class(info_dict=None, raise_exc=None):
    """Return a fake YoutubeDL class that returns *info_dict* or raises *raise_exc*."""
    class FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            if raise_exc:
                raise raise_exc
            return info_dict

        def download(self, urls):
            if raise_exc:
                raise raise_exc

    return FakeYDL


# ---------------------------------------------------------------------------
# _detect_platform
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    def test_youtube(self):
        assert _detect_platform("https://www.youtube.com/watch?v=abc") == "YouTube"

    def test_youtu_be_short(self):
        assert _detect_platform("https://youtu.be/abc") == "YouTube"

    def test_tiktok(self):
        assert _detect_platform("https://www.tiktok.com/@user/video/1") == "TikTok"

    def test_instagram(self):
        assert _detect_platform("https://www.instagram.com/p/abc/") == "Instagram"

    def test_twitter(self):
        assert _detect_platform("https://twitter.com/user/status/1") == "Twitter/X"

    def test_x_com(self):
        assert _detect_platform("https://x.com/user/status/1") == "Twitter/X"

    def test_facebook(self):
        assert _detect_platform("https://www.facebook.com/video/1") == "Facebook"

    def test_twitch(self):
        assert _detect_platform("https://www.twitch.tv/streamer") == "Twitch"

    def test_vimeo(self):
        assert _detect_platform("https://vimeo.com/12345") == "Vimeo"

    def test_dailymotion(self):
        _url = "https://www.dailymotion.com/video/abc"
        assert _detect_platform(_url) == "Dailymotion"

    def test_unknown_returns_web(self):
        assert _detect_platform("https://somerandomblog.com/video") == "Web"


# ---------------------------------------------------------------------------
# extract_info — success path
# ---------------------------------------------------------------------------

class TestExtractInfoSuccess:
    def test_returns_media_info_object(self):
        info_dict = {
            "title": "My Video",
            "uploader": "TestUser",
            "duration": 120,
            "thumbnail": "https://i.ytimg.com/vi/test/default.jpg",
            "formats": [],
            "is_live": False,
            "was_live": False,
        }
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", fake_ydl_class(info_dict)):
            result = engine.extract_info("https://youtube.com/watch?v=abc")
        assert result.title == "My Video"
        assert result.uploader == "TestUser"
        assert result.duration == 120

    def test_handles_playlist_takes_first_entry(self):
        first_entry = {
            "title": "First Video",
            "uploader": "Chan",
            "duration": 60,
            "thumbnail": "",
            "formats": [],
            "is_live": False,
            "was_live": False,
        }
        playlist_dict = {
            "_type": "playlist",
            "entries": [first_entry],
        }
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", fake_ydl_class(playlist_dict)):
            result = engine.extract_info("https://youtube.com/playlist?list=abc")
        assert result.title == "First Video"

    def test_empty_playlist_raises(self):
        playlist_dict = {"_type": "playlist", "entries": []}
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", fake_ydl_class(playlist_dict)):
            with pytest.raises(RuntimeError, match="empty"):
                engine.extract_info("https://youtube.com/playlist?list=abc")

    def test_none_result_raises(self):
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", fake_ydl_class(None)):
            with pytest.raises(RuntimeError):
                engine.extract_info("https://youtube.com/watch?v=abc")


# ---------------------------------------------------------------------------
# extract_info — error messages
# ---------------------------------------------------------------------------

class TestExtractInfoErrors:
    def _extract_with_error(self, error_msg: str) -> str:
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        exc = yt_dlp.utils.DownloadError(error_msg)
        with patch.object(mod.yt_dlp, "YoutubeDL", fake_ydl_class(raise_exc=exc)):
            with pytest.raises(RuntimeError) as exc_info:
                engine.extract_info("https://youtube.com/watch?v=abc")
        return str(exc_info.value)

    def test_private_video_friendly_message(self):
        msg = self._extract_with_error("This video is private")
        assert "private" in msg.lower()

    def test_404_friendly_message(self):
        msg = self._extract_with_error("404 not found")
        assert "not found" in msg.lower() or "removed" in msg.lower()

    def test_unsupported_url_friendly_message(self):
        msg = self._extract_with_error("Unsupported URL")
        assert "not supported" in msg.lower()

    def test_facebook_stories_always_blocked(self):
        """Facebook Stories are always blocked.

        No cookie can help (yt-dlp limitation).
        """
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        mock_ydl = MagicMock()
        with patch.object(mod.yt_dlp, "YoutubeDL", mock_ydl):
            with pytest.raises(RuntimeError, match="Facebook Stories"):
                engine.extract_info("https://www.facebook.com/stories/user/123")
        mock_ydl.assert_not_called()

    def test_instagram_stories_blocked_without_cookies(self):
        """Instagram Stories are blocked when no cookies are configured."""
        cfg = make_config(use_cookies=False, cookie_file="")
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod
        mock_ydl = MagicMock()
        with patch.object(mod.yt_dlp, "YoutubeDL", mock_ydl):
            with pytest.raises(RuntimeError, match="[Cc]ookie"):
                engine.extract_info("https://www.instagram.com/stories/user/123")
        mock_ydl.assert_not_called()

    def test_instagram_stories_allowed_with_cookie_file(self):
        """Instagram Stories pass through to yt-dlp when a cookie file is configured."""
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            cookie_path = f.name
        try:
            cfg = make_config(cookie_file=cookie_path)
            engine = YtDlpEngine(cfg)
            import infrastructure.downloader.yt_dlp_engine as mod

            info_dict = {
                "title": "Story", "uploader": "user", "duration": 15,
                "thumbnail": "", "formats": [], "is_live": False, "was_live": False,
            }

            class FakeYDL:
                def __init__(self, opts): pass
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def extract_info(self, url, download=False): return info_dict

            with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
                # Should NOT raise — cookie file present → allowed through
                _stories_url = "https://www.instagram.com/stories/user/123"
                result = engine.extract_info(_stories_url)
            assert result.title == "Story"
        finally:
            os.unlink(cookie_path)

    def test_instagram_stories_allowed_with_browser_cookies(self):
        """Instagram Stories pass through to yt-dlp when use_cookies=True."""
        cfg = make_config(use_cookies=True, cookie_file="")
        engine = YtDlpEngine(cfg)
        import infrastructure.downloader.yt_dlp_engine as mod

        info_dict = {
            "title": "Story", "uploader": "user", "duration": 15,
            "thumbnail": "", "formats": [], "is_live": False, "was_live": False,
        }

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False): return info_dict

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            result = engine.extract_info("https://www.instagram.com/stories/user/123")
        assert result.title == "Story"


# ---------------------------------------------------------------------------
# Download opts — D2 fix: exponential backoff
# ---------------------------------------------------------------------------

class TestBackoffOpts:
    def _capture_opts(self, task, cfg):
        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls): pass

        import infrastructure.downloader.yt_dlp_engine as mod
        engine = YtDlpEngine(cfg)
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)
        return captured

    def test_sleep_interval_present(self):
        task = make_task()
        opts = self._capture_opts(task, make_config())
        assert "sleep_interval" in opts
        assert opts["sleep_interval"] >= 1

    def test_max_sleep_interval_present(self):
        task = make_task()
        opts = self._capture_opts(task, make_config())
        assert "max_sleep_interval" in opts
        assert opts["max_sleep_interval"] >= opts["sleep_interval"]

    def test_sleep_interval_requests_present(self):
        task = make_task()
        opts = self._capture_opts(task, make_config())
        assert "sleep_interval_requests" in opts


# ---------------------------------------------------------------------------
# Cookie file boundary check (S3 fix)
# ---------------------------------------------------------------------------

class TestCookieFileBoundary:
    def _run_extract(self, cfg, captured_opts):
        info_dict = {
            "title": "T", "uploader": "U", "duration": 1,
            "thumbnail": "", "formats": [], "is_live": False, "was_live": False,
        }

        class FakeYDL:
            def __init__(self, opts):
                captured_opts.update(opts)
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False):
                return info_dict

        import infrastructure.downloader.yt_dlp_engine as mod
        engine = YtDlpEngine(cfg)
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.extract_info("https://youtube.com/watch?v=abc")

    def _make_real_config(self, config_path, cookie_file=""):
        """Create a real ConfigManager so config_path.parent is a genuine Path."""
        import json
        from infrastructure.config.config_manager import ConfigManager
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({}))
        cfg = ConfigManager(config_path)
        if cookie_file:
            cfg.set("cookie_file", cookie_file)
        cfg.set("use_cookies", False)
        return cfg

    def test_valid_cookie_file_inside_home_is_used(self, tmp_path, monkeypatch):
        # Cookie lives inside the config directory (= safe_root)
        config_dir = tmp_path / "config_dir"
        cookie = config_dir / "cookies.txt"
        config_dir.mkdir(parents=True)
        cookie.write_text("# Netscape HTTP Cookie File\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg = self._make_real_config(config_dir / "config.json",
                                     cookie_file=str(cookie))
        opts = {}
        self._run_extract(cfg, opts)
        assert opts.get("cookiefile") == str(cookie.resolve())

    def test_cookie_file_outside_home_is_rejected(self, tmp_path, monkeypatch):
        # Cookie file exists but is outside the config directory (safe_root)
        config_dir = tmp_path / "home"
        outside = tmp_path / "outside_cookies.txt"
        outside.write_text("# cookies\n")
        monkeypatch.setattr(Path, "home", lambda: config_dir)
        cfg = self._make_real_config(config_dir / "config.json",
                                     cookie_file=str(outside))
        opts = {}
        self._run_extract(cfg, opts)
        # Should NOT be passed to yt-dlp
        assert "cookiefile" not in opts

    def test_nonexistent_cookie_file_is_ignored(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config_dir"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cfg = self._make_real_config(config_dir / "config.json",
                                     cookie_file=str(tmp_path / "nonexistent.txt"))
        opts = {}
        self._run_extract(cfg, opts)
        assert "cookiefile" not in opts


# ---------------------------------------------------------------------------
# Progress hook
# ---------------------------------------------------------------------------

class TestProgressHook:
    def _make_hook(self, task=None):
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        if task is None:
            task = make_task()
        hook = engine._make_progress_hook(task, callback=None)
        return hook, task

    def test_downloading_status_updates_progress(self):
        hook, task = self._make_hook()
        hook({
            "status": "downloading",
            "downloaded_bytes": 500,
            "total_bytes": 1000,
            "speed": None,
            "eta": None,
        })
        assert task.status == DownloadStatus.DOWNLOADING
        assert task.progress == pytest.approx(50.0)

    def test_downloading_caps_progress_at_99(self):
        hook, task = self._make_hook()
        hook({
            "status": "downloading",
            "downloaded_bytes": 1000,
            "total_bytes": 1000,
            "speed": None,
            "eta": None,
        })
        assert task.progress <= 99.0

    def test_downloading_sets_speed(self):
        hook, task = self._make_hook()
        hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 1000,
            "speed": 1024 * 512,  # 512 KiB/s
            "eta": None,
        })
        assert "KiB/s" in task.speed or "MiB/s" in task.speed

    def test_downloading_sets_eta(self):
        hook, task = self._make_hook()
        hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 1000,
            "speed": None,
            "eta": 90,  # 1m30s
        })
        assert task.eta == "01:30"

    def test_finished_sets_processing_status(self):
        hook, task = self._make_hook()
        hook({"status": "finished", "filename": "/tmp/video.mp4"})  # nosec B108
        assert task.status == DownloadStatus.PROCESSING
        assert task.progress == pytest.approx(99.5)

    def test_finished_clears_speed_and_eta(self):
        hook, task = self._make_hook()
        task.speed = "500 KiB/s"
        task.eta = "01:00"
        hook({"status": "finished", "filename": "/tmp/video.mp4"})  # nosec B108
        assert task.speed == ""
        assert task.eta == ""

    def test_cancelled_task_raises_download_error(self):
        hook, task = self._make_hook()
        task.cancel()
        with pytest.raises(yt_dlp.utils.DownloadError):
            hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 100})

    def test_unknown_status_is_ignored(self):
        hook, task = self._make_hook()
        original_status = task.status
        hook({"status": "unknown_future_status"})
        assert task.status == original_status

    def test_callback_called_on_progress(self):
        calls = []
        cfg = make_config()
        engine = YtDlpEngine(cfg)
        task = make_task()
        hook = engine._make_progress_hook(task, callback=lambda t: calls.append(t))
        hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 1000,
            "speed": None,
            "eta": None,
        })
        assert calls == [task]

    def test_no_total_bytes_skips_progress_update(self):
        hook, task = self._make_hook()
        task.progress = 0.0
        hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 0,
            "total_bytes_estimate": 0,
            "speed": None,
            "eta": None,
        })
        assert task.progress == 0.0


# ---------------------------------------------------------------------------
# Final filename resolution after FFmpeg merge
# ---------------------------------------------------------------------------

class TestFinalFilenameResolution:
    """After download, task.filename must point to the real merged file,
    not the intermediate fragment yt-dlp reported during progress hooks."""

    def _run_download(self, tmp_path, create_file_ext=".mp4"):
        """Helper: run engine.download() with a fake yt-dlp that creates a file."""
        cfg = make_config(download_dir=tmp_path)
        engine = YtDlpEngine(cfg)
        task = make_task()
        task.output_dir = str(tmp_path)

        merged_file = tmp_path / f"channel - title{create_file_ext}"

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls):
                # Simulate FFmpeg creating the final merged file.
                # Must be > 50,000 bytes to pass the engine's size-scan threshold.
                merged_file.write_bytes(b"fake merged video content" * 3000)

        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)

        return task, merged_file

    def test_filename_resolved_to_merged_mp4(self, tmp_path):
        """task.filename should point to the .mp4 file after FFmpeg merge."""
        task, merged = self._run_download(tmp_path, ".mp4")
        assert task.filename == str(merged), (
            f"Expected {merged}, got {task.filename}"
        )

    def test_filename_resolved_to_merged_mkv(self, tmp_path):
        """task.filename should point to .mkv when that is the merged output."""
        task, merged = self._run_download(tmp_path, ".mkv")
        assert task.filename == str(merged)

    def test_filename_resolved_to_audio_m4a(self, tmp_path):
        """Audio downloads (.m4a) are also resolved correctly."""
        task, merged = self._run_download(tmp_path, ".m4a")
        assert task.filename == str(merged)

    def test_part_files_excluded_from_resolution(self, tmp_path):
        """Incomplete .part files must not be picked as the final filename."""
        cfg = make_config(download_dir=tmp_path)
        engine = YtDlpEngine(cfg)
        task = make_task()
        task.output_dir = str(tmp_path)

        real_file   = tmp_path / "video.mp4"
        part_file   = tmp_path / "video.mp4.part"

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls):
                real_file.write_bytes(b"real" * 15000)  # >50KB to pass size-scan
                part_file.write_bytes(b"partial")

        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)

        assert ".part" not in task.filename, \
            "task.filename must not point to a .part file"
        assert task.filename == str(real_file)

    def test_empty_output_dir_does_not_crash(self, tmp_path):
        """If no media file is found (edge case), download must not crash."""
        cfg = make_config(download_dir=tmp_path)
        engine = YtDlpEngine(cfg)
        task = make_task()
        task.output_dir = str(tmp_path)

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls):
                pass  # Creates no file at all

        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)  # Must not raise


class TestOutputDirAlwaysAbsolute:
    """
    Regression guard: engine.download() must always resolve output_dir to an
    absolute path before passing it to yt-dlp as the outtmpl base.

    Root cause of the 'Open Folder opens wrong directory' bug on Windows +
    PyInstaller: when the app is launched via a double-clicked EXE the process
    CWD is the EXE directory.  If output_dir / config.download_dir is a relative
    Path (e.g. the user typed "Downloads/OmniDL" into Settings), yt-dlp
    resolves it against the EXE directory and writes files there.  The
    resulting info_dict["filepath"] is also relative, so task.filename ends up
    as EXE-dir/video.mp4 — the wrong place.

    Fix: (Path(task.output_dir) or config.download_dir).resolve() in the
    engine so yt-dlp always receives an absolute outtmpl path.
    """

    def test_opts_outtmpl_is_absolute_for_relative_output_dir(self, tmp_path):
        """
        When task.output_dir is a relative path, the opts["outtmpl"] passed
        to yt-dlp must still be an absolute path.
        """
        import os

        cfg = make_config(download_dir=tmp_path)
        engine = YtDlpEngine(cfg)
        task = make_task()

        # Use a relative output_dir string (no leading slash / drive letter)
        old_cwd = os.getcwd()
        exe_dir = tmp_path / "exe_dir"
        exe_dir.mkdir()
        try:
            os.chdir(exe_dir)   # simulate PyInstaller CWD = EXE dir

            task.output_dir = str(tmp_path)   # absolute — use parent tmp_path
            # Pretend only a relative path is available (edge-case portable install)
            task.output_dir = "downloads"     # relative to CWD = exe_dir

            captured_opts: list[dict] = []

            class CapturingYDL:
                def __init__(self, opts):
                    captured_opts.append(opts)
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def download(self, urls):
                    # Create a dummy file so size-scan doesn't fail
                    dl_dir = (exe_dir / "downloads")
                    dl_dir.mkdir(exist_ok=True)
                    (dl_dir / "video.mp4").write_bytes(b"x" * 60_000)

            import infrastructure.downloader.yt_dlp_engine as mod
            with patch.object(mod.yt_dlp, "YoutubeDL", CapturingYDL):
                try:
                    engine.download(task)
                except Exception:
                    pass   # we only care about opts here

            assert captured_opts, "YoutubeDL was not instantiated"
            outtmpl = captured_opts[0]["outtmpl"]
            assert os.path.isabs(outtmpl), (
                "opts outtmpl must be absolute, got: " + repr(outtmpl) + ". "
                "A relative outtmpl causes yt-dlp to write files to the process CWD "
                "(EXE directory on PyInstaller), not the downloads folder."
            )
        finally:
            os.chdir(old_cwd)

    def test_task_filename_is_absolute_after_download(self, tmp_path):
        """
        After a successful download, task.filename must be an absolute path
        regardless of whether task.output_dir was relative or absolute.
        """
        cfg = make_config(download_dir=tmp_path)
        engine = YtDlpEngine(cfg)
        task = make_task()
        task.output_dir = str(tmp_path)   # absolute

        final_file = tmp_path / "video.mp4"

        class FakeYDL:
            def __init__(self, opts): self.opts = opts
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls):
                # Simulate engine's postprocessor hook firing with the final path
                final_file.write_bytes(b"x" * 60_000)

        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)

        assert task.filename, "task.filename must not be empty after download"
        import os
        assert os.path.isabs(task.filename), (
            f"task.filename must be absolute after download, got: {task.filename!r}"
        )
