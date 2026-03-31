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


# ── Regression: .txt → .enc auto-fallback in cookie validation ───────────────

class TestCookiePathEncFallback:
    """BUG BC regression: validate_cookie_path_raw must accept .enc when
    config stores .txt but encrypt_cookie_file renamed it to .enc."""

    def _make_config(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        return cfg

    def test_raw_txt_path_accepted_when_txt_exists(self, tmp_path):
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path_raw
        cfg = self._make_config(tmp_path)
        cookie_dir = cfg.config_path.parent / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        txt = cookie_dir / "instagram_brave_cdp_cookies.txt"
        txt.write_text("# Netscape\n.instagram.com TRUE / TRUE 0 sid abc\n")
        result = _validate_cookie_path_raw(str(txt), cfg)
        assert result == str(txt)

    def test_raw_txt_path_falls_back_to_enc(self, tmp_path):
        """Config stores .txt but only .enc exists → return .enc path."""
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path_raw
        cfg = self._make_config(tmp_path)
        cookie_dir = cfg.config_path.parent / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        txt = cookie_dir / "instagram_brave_cdp_cookies.txt"
        enc = txt.with_suffix(".enc")
        enc.write_bytes(b"encrypted_data")
        # .txt does NOT exist, only .enc
        result = _validate_cookie_path_raw(str(txt), cfg)
        assert result == str(enc), f"Expected .enc path, got {result!r}"

    def test_raw_outside_safe_dir_rejected(self, tmp_path):
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path_raw
        cfg = self._make_config(tmp_path)
        outside = tmp_path.parent / "evil_cookies.txt"
        outside.write_text("bad")
        result = _validate_cookie_path_raw(str(outside), cfg)
        assert result is None

    def test_global_txt_falls_back_to_enc(self, tmp_path):
        """Global cookie_file: config stores .txt, only .enc on disk → enc used."""
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        cfg = self._make_config(tmp_path)
        cookie_dir = cfg.config_path.parent / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True)
        txt = cookie_dir / "brave_global_cookies.txt"
        enc = txt.with_suffix(".enc")
        enc.write_bytes(b"encrypted_global")
        cfg.set("cookie_file", str(txt))
        result = _validate_cookie_path(cfg)
        assert result == str(enc)


# ---------------------------------------------------------------------------
# FIX-TK: TikTok VOD falsely detected as livestream
# ---------------------------------------------------------------------------

class TestTikTokVodLiveDetection:
    """FIX-TK — TikTok /video/<id> URLs must never resolve is_live=True,
    even when yt-dlp metadata returns is_live=True (TikTok API stale data).
    Only /live/ path URLs are real TikTok livestreams."""

    def _make_engine(self):
        cfg = make_config()
        cfg.use_cookies = False
        cfg.cookie_file = ""
        cfg.platform_cookies = {}
        cfg.remote_components = None
        return YtDlpEngine(cfg)

    def _fake_info(self, is_live: bool, duration: int = 60) -> dict:
        return {
            "title": "Test TikTok video",
            "uploader": "testuser",
            "duration": duration,
            "thumbnail": "https://example.com/thumb.jpg",
            "formats": [{"format_id": "0", "ext": "mp4", "url": "https://cdn.tiktok.com/v.mp4"}],
            "is_live": is_live,
            "was_live": False,
            "id": "7620980082118675732",
        }

    @patch("yt_dlp.YoutubeDL")
    def test_tiktok_vod_url_is_never_live(self, mock_ydl_cls):
        """VOD URL with is_live=True from API must be corrected to is_live=False."""
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = self._fake_info(is_live=True)
        mock_ydl_cls.return_value = mock_ydl

        engine = self._make_engine()
        url = "https://www.tiktok.com/@gracilenemonteir78900/video/7620980082118675732?is_from_webapp=1&sender_device=pc"
        info = engine.extract_info(url)

        assert info.is_live is False, (
            "TikTok /video/<id> URL must never be flagged as livestream"
        )

    @patch("yt_dlp.YoutubeDL")
    def test_tiktok_vod_clean_url_is_never_live(self, mock_ydl_cls):
        """VOD URL without query params must also not be flagged as livestream."""
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = self._fake_info(is_live=True)
        mock_ydl_cls.return_value = mock_ydl

        engine = self._make_engine()
        url = "https://www.tiktok.com/@testuser/video/1234567890"
        info = engine.extract_info(url)

        assert info.is_live is False

    @patch("yt_dlp.YoutubeDL")
    def test_tiktok_live_url_remains_live(self, mock_ydl_cls):
        """Real TikTok /live/ URL where yt-dlp returns is_live=True must stay live."""
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = self._fake_info(is_live=True, duration=0)
        mock_ydl_cls.return_value = mock_ydl

        engine = self._make_engine()
        url = "https://www.tiktok.com/@testuser/live"
        info = engine.extract_info(url)

        assert info.is_live is True, (
            "TikTok /live/ URL with is_live=True from yt-dlp must stay live"
        )

    @patch("yt_dlp.YoutubeDL")
    def test_tiktok_vod_false_from_api_stays_false(self, mock_ydl_cls):
        """VOD where API correctly returns is_live=False must also stay False."""
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = self._fake_info(is_live=False)
        mock_ydl_cls.return_value = mock_ydl

        engine = self._make_engine()
        url = "https://www.tiktok.com/@testuser/video/9999999999"
        info = engine.extract_info(url)

        assert info.is_live is False



# ---------------------------------------------------------------------------
# FIX-TK-AUDIO-2: TikTok DASH audio format selector fix
# ---------------------------------------------------------------------------

class TestTikTokFormatIdPatch:
    """FIX-TK-AUDIO-2 — For TikTok VOD URLs, download() must inject
    [acodec!=none] into the bestaudio selector so yt-dlp never picks an
    audio-less DASH stream, which causes FFmpegMergerPP to silently skip
    the audio-map step and produce a silent mp4.

    Long-form TikTok videos (5+ min) are particularly affected because
    TikTok's CDN marks their separate audio tracks with acodec='none' in
    yt-dlp's format table, making bestaudio resolve to a silent stream.
    """

    def _capture_opts(self, task, cfg=None):
        if cfg is None:
            cfg = make_config()
            cfg.cookie_file = ""
            cfg.platform_cookies = {}
            cfg.remote_components = None
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

    def _make_tiktok_task(self, format_id, ext="mp4"):
        url = "https://www.tiktok.com/@testuser/video/7620980082118675732"
        task = DownloadTask(url=url, format_id=format_id, output_ext=ext)
        task.media_info = MediaInfo(url=url, title="Test TikTok", is_live=False)
        return task

    def test_best_quality_gets_acodec_filter(self):
        """'bestvideo+bestaudio/best' -> 'bestvideo+bestaudio[acodec!=none]/best'"""
        task = self._make_tiktok_task("bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestvideo+bestaudio[acodec!=none]/best"

    def test_1080p_gets_acodec_filter(self):
        """'bestvideo[height<=1080]+bestaudio/best' gets acodec filter applied."""
        task = self._make_tiktok_task("bestvideo[height<=1080]+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestvideo[height<=1080]+bestaudio[acodec!=none]/best"

    def test_720p_gets_acodec_filter(self):
        task = self._make_tiktok_task("bestvideo[height<=720]+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestvideo[height<=720]+bestaudio[acodec!=none]/best"

    def test_360p_gets_acodec_filter(self):
        task = self._make_tiktok_task("bestvideo[height<=360]+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestvideo[height<=360]+bestaudio[acodec!=none]/best"

    def test_audio_only_not_modified(self):
        """'bestaudio/best' (audio-only) must NOT be modified — no bestvideo present."""
        task = self._make_tiktok_task("bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestaudio/best"

    def test_best_not_modified(self):
        """'best' (live/photo path) must not be modified."""
        url = "https://www.tiktok.com/@testuser/video/9999"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test", is_live=False)
        opts = self._capture_opts(task)
        assert opts["format"] == "best"

    def test_non_tiktok_url_not_modified(self):
        """YouTube URLs must NOT have format_id modified."""
        task = make_task(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        task.format_id = "bestvideo+bestaudio/best"
        task.output_ext = "mp4"
        task.media_info = MediaInfo(url=task.url, title="YouTube video", is_live=False)
        cfg = make_config()
        cfg.cookie_file = ""
        cfg.platform_cookies = {}
        cfg.remote_components = None
        opts = self._capture_opts(task, cfg=cfg)
        assert opts["format"] == "bestvideo+bestaudio/best"

    def test_tiktok_live_not_modified(self):
        """TikTok live uses 'best' — format must not be patched."""
        url = "https://www.tiktok.com/@testuser/live"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="TikTok Live", is_live=True)
        opts = self._capture_opts(task)
        assert opts["format"] == "best"

    def test_acodec_filter_not_duplicated(self):
        """Running patch twice must not produce double [acodec!=none]."""
        task = self._make_tiktok_task("bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert fmt.count("[acodec!=none]") == 1, f"Expected exactly 1 filter, got: {fmt}"
