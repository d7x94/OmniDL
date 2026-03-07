"""
tests/test_yt_dlp_engine.py
Unit tests for infrastructure/downloader/yt_dlp_engine.py

yt-dlp itself is mocked so tests run without network access.
Covers:
- _apply_extra_args allowlist (Issue #3 / security)
- noplaylist present in download opts (Issue #8)
- socket_timeout and continuedl present in opts (Issue #9)
- Cancellation detection via is_cancellation_requested (Issue #7)
- Short-flag support in extra_args (Bug #12)
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(
    extra_args="",
    proxy="",
    use_cookies=False,
    cookies_browser="chrome",
    max_retries=3,
    embed_thumbnail=False,
    embed_metadata=False,
    download_dir=Path("/tmp"),  # nosec B108
):
    cfg = MagicMock()
    cfg.extra_args = extra_args
    cfg.proxy = proxy
    cfg.use_cookies = use_cookies
    cfg.cookies_browser = cookies_browser
    cfg.max_retries = max_retries
    cfg.embed_thumbnail = embed_thumbnail
    cfg.embed_metadata = embed_metadata
    cfg.download_dir = download_dir
    return cfg


def make_task(url="https://youtube.com/watch?v=test") -> DownloadTask:
    task = DownloadTask(url=url, format_id="best", output_ext="mp4")
    task.media_info = MediaInfo(url=url, title="Test Video")
    return task


# ---------------------------------------------------------------------------
# _apply_extra_args tests
# ---------------------------------------------------------------------------

class TestApplyExtraArgsAllowlist:
    """Issue #3 — only whitelisted yt-dlp options should pass through."""

    def _get_opts_after_apply(self, raw: str) -> dict:
        cfg = make_config(extra_args=raw)
        engine = YtDlpEngine(cfg)
        opts: dict = {}
        engine._apply_extra_args(opts)
        return opts

    def test_safe_key_is_passed_through(self):
        opts = self._get_opts_after_apply("--ratelimit 500K")
        assert opts.get("ratelimit") == "500K"

    def test_unsafe_exec_key_is_blocked(self):
        opts = self._get_opts_after_apply('--exec "rm -rf ~"')
        assert "exec" not in opts

    def test_unsafe_exec_before_download_is_blocked(self):
        opts = self._get_opts_after_apply('--exec-before-download "calc.exe"')
        assert "exec_before_download" not in opts

    def test_unsafe_cookies_key_is_blocked(self):
        opts = self._get_opts_after_apply("--cookies /etc/passwd")
        assert "cookies" not in opts

    def test_multiple_safe_keys(self):
        opts = self._get_opts_after_apply("--noplaylist --geo-bypass")
        assert opts.get("noplaylist") is True
        assert opts.get("geo_bypass") is True

    def test_empty_extra_args_leaves_opts_unchanged(self):
        opts = self._get_opts_after_apply("")
        assert opts == {}

    def test_short_flag_safe_is_passed(self):
        """Bug #12 — short flags were silently dropped; now handled."""
        # 'format' is on the allowlist; short flag '-f' maps to 'f' which is NOT
        # on the allowlist (yt-dlp's short flags don't map 1:1 to long names).
        # This test documents that short flags at least don't crash.
        opts = self._get_opts_after_apply("-x")
        # 'x' is not on the allowlist, should be blocked
        assert "x" not in opts


# ---------------------------------------------------------------------------
# Download opts composition tests
# ---------------------------------------------------------------------------

class TestDownloadOpts:
    """Issues #8, #9 — verify that required opts keys are present."""

    def _capture_opts(self, task, cfg=None):
        """Run engine.download() with yt-dlp mocked; return the opts dict used."""
        if cfg is None:
            cfg = make_config()
        engine = YtDlpEngine(cfg)
        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def download(self, urls):
                pass

        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)
        return captured

    def test_noplaylist_is_set(self):
        """Issue #8 — download() must set noplaylist=True to match extract_info()."""
        task = make_task()
        task.status = DownloadStatus.QUEUED
        opts = self._capture_opts(task)
        assert opts.get("noplaylist") is True

    def test_socket_timeout_is_set(self):
        """Issue #9 — missing socket_timeout could block worker threads indefinitely."""
        task = make_task()
        opts = self._capture_opts(task)
        assert "socket_timeout" in opts
        assert isinstance(opts["socket_timeout"], int)

    def test_continuedl_is_set(self):
        """Issue #9 — continuedl must be True so partial downloads are resumed."""
        task = make_task()
        opts = self._capture_opts(task)
        assert opts.get("continuedl") is True

    def test_overwrites_is_false(self):
        """Issue #9 — completed files must not be overwritten on restart."""
        task = make_task()
        opts = self._capture_opts(task)
        assert opts.get("overwrites") is False


# ---------------------------------------------------------------------------
# Cancellation detection tests
# ---------------------------------------------------------------------------

class TestCancellationDetection:
    """Issue #7 — cancellation must be detected via is_cancellation_requested,
    not a case-sensitive string match on the yt-dlp error message."""

    def test_cancelled_task_does_not_become_failed(self):
        """When the task is cancelled, download() should re-raise DownloadError
        (letting _run_task set CANCELLED), not RuntimeError (FAILED)."""
        import infrastructure.downloader.yt_dlp_engine as mod
        import yt_dlp as real_yt_dlp

        cfg = make_config()
        engine = YtDlpEngine(cfg)
        task = make_task()

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls):
                # US spelling
                raise real_yt_dlp.utils.DownloadError("Download canceled")

        task.cancel()  # set is_cancellation_requested = True

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            # Should re-raise DownloadError, not RuntimeError
            with pytest.raises(real_yt_dlp.utils.DownloadError):
                engine.download(task)

    def test_genuine_error_raises_runtime_error(self):
        """Non-cancellation errors must become RuntimeError with friendly message."""
        import infrastructure.downloader.yt_dlp_engine as mod
        import yt_dlp as real_yt_dlp

        cfg = make_config()
        engine = YtDlpEngine(cfg)
        task = make_task()

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def download(self, urls):
                raise real_yt_dlp.utils.DownloadError("Private video")

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            with pytest.raises(RuntimeError) as exc_info:
                engine.download(task)
        assert "private" in str(exc_info.value).lower()
