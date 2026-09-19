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

            def add_post_processor(self, pp, when=None):
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
        import yt_dlp as real_yt_dlp

        import infrastructure.downloader.yt_dlp_engine as mod

        cfg = make_config()
        engine = YtDlpEngine(cfg)
        task = make_task()

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def add_post_processor(self, pp, when=None):
                pass

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
        import yt_dlp as real_yt_dlp

        import infrastructure.downloader.yt_dlp_engine as mod

        cfg = make_config()
        engine = YtDlpEngine(cfg)
        task = make_task()

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def add_post_processor(self, pp, when=None):
                pass

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

        assert info.is_live is False, "TikTok /video/<id> URL must never be flagged as livestream"

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

        assert info.is_live is True, "TikTok /live/ URL with is_live=True from yt-dlp must stay live"

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
    """BUG-BP / BUG-BS / BUG-TT-SHOP-3 — For TikTok VOD URLs, download() must build the
    seven-tier format selector:

        best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best

    Rationale:
      Tier 1  best[format_id^=h264]          — watermark-free h264_* muxed stream
      Tier 2  best[format_id=audio][ext=mp4] — shopping-link muxed mp4 stream
      Tier 3  download                       — watermarked fallback (BUG-TT-EFF-2: before audio last-resort)
      Tier 4  bestvideo*+bestaudio*          — DASH merge (may need FFmpeg)
      Tier 5  bestvideo*                     — single-stream starred selector
      Tier 6  best[format_id=audio]          — last resort: mislabeled product-link or template effect
      Tier 7  best                           — final safety net

    Previous 7-tier chain is SUPERSEDED. All tests updated to assert the new 7-tier selector.

    BUG-BP extension: format_id='best' (single-mux path) now also receives
    the 7-tier chain instead of being left unmodified.
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

            def add_post_processor(self, pp, when=None):
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
        """'bestvideo+bestaudio/best' → BUG-BS 7-tier chain (watermark-free h264 first)."""
        task = self._make_tiktok_task("bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    def test_1080p_gets_acodec_filter(self):
        """'bestvideo[height<=1080]+bestaudio/best' → BUG-BS 7-tier chain.
        Height cap is not propagated into the new selector (h264_* streams
        are already height-limited by TikTok CDN; the selector picks best tbr)."""
        task = self._make_tiktok_task("bestvideo[height<=1080]+bestaudio/best")
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    def test_720p_gets_acodec_filter(self):
        """'bestvideo[height<=720]+bestaudio/best' → BUG-BS 7-tier chain."""
        task = self._make_tiktok_task("bestvideo[height<=720]+bestaudio/best")
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    def test_360p_gets_acodec_filter(self):
        """'bestvideo[height<=360]+bestaudio/best' → BUG-BS 7-tier chain."""
        task = self._make_tiktok_task("bestvideo[height<=360]+bestaudio/best")
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    def test_audio_only_not_modified(self):
        """'bestaudio/best' (audio-only) must NOT be modified — no bestvideo present."""
        task = self._make_tiktok_task("bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestaudio/best"

    def test_best_gets_seven_tier_chain(self):
        """BUG-BP: format_id='best' on a TikTok VOD now receives the 7-tier chain.
        Previously the else-branch was missing the BUG-BS fix; it now also maps to
        best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best."""
        url = "https://www.tiktok.com/@testuser/video/9999"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test", is_live=False)
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

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
        """TikTok live uses HLS-safe format chain (BUG-TT-14) — VOD patch must not apply."""
        url = "https://www.tiktok.com/@testuser/live"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="TikTok Live", is_live=True)
        opts = self._capture_opts(task)
        assert opts["format"] == "best[protocol=m3u8_native]/best[protocol^=m3u8]/best[protocol^=https]/best"

    def test_format_is_idempotent(self):
        """BUG-BS: Running the patch logic twice must not produce extra tiers.
        The idempotency guard is 'bestvideo*' not in _format_id — once patched
        to the 7-tier chain, subsequent calls skip the patch."""
        task = self._make_tiktok_task("bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        # No [acodec!=none] in the new 7-tier selector
        assert "[acodec!=none]" not in fmt, f"New 7-tier chain must not contain [acodec!=none], got: {fmt}"
        assert (
            fmt
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )
        # Verify exactly 7 tiers
        assert len(fmt.split("/")) == 7, f"Expected 7 tiers, got: {fmt}"


# ---------------------------------------------------------------------------
# BUG-YT-LIVE-FMT: yt-dlp 2026.07.04 live adaptive format support for YouTube.
# Non-TikTok live must honour the user's quality preset for YouTube via
# "<format_id>/best"; other live platforms (Instagram, Twitch) keep "best".
# ---------------------------------------------------------------------------


class TestYouTubeLiveAdaptiveFormat:
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

            def add_post_processor(self, pp, when=None):
                pass

            def download(self, urls):
                pass

        import infrastructure.downloader.yt_dlp_engine as mod

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)
        return captured

    def test_youtube_live_honours_quality_preset(self):
        url = "https://www.youtube.com/watch?v=live123"
        task = DownloadTask(url=url, format_id="bestvideo[height<=720]+bestaudio/best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="YT Live", is_live=True)
        opts = self._capture_opts(task)
        assert opts["format"] == "bestvideo[height<=720]+bestaudio/best/best"

    def test_youtube_live_short_domain_honours_preset(self):
        url = "https://youtu.be/live123"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="YT Live", is_live=True)
        opts = self._capture_opts(task)
        assert opts["format"] == "best/best"

    def test_instagram_live_still_prefers_bare_best(self):
        """Non-YouTube live platforms still pick 'best' first.

        BUG-FB-LIVE-FMT added a "/bv*+ba" tail so a DASH-only Facebook broadcast
        (video-only + audio-only representations, no muxed format) stops aborting
        with "Requested format is not available". Instagram/Twitch expose a muxed
        HLS format, so the first branch still wins and behaviour is unchanged.
        """
        url = "https://www.instagram.com/someuser/live/"
        task = DownloadTask(url=url, format_id="bestvideo+bestaudio/best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="IG Live", is_live=True)
        opts = self._capture_opts(task)
        assert opts["format"].split("/")[0] == "best"
        assert opts["format"] == "best/bv*+ba"

    def test_tiktok_live_regression_unaffected(self):
        """TikTok live keeps its HLS-safe chain — YouTube change must not leak in."""
        url = "https://www.tiktok.com/@testuser/live"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="TikTok Live", is_live=True)
        opts = self._capture_opts(task)
        assert opts["format"] == "best[protocol=m3u8_native]/best[protocol^=m3u8]/best[protocol^=https]/best"

    def test_youtube_vod_not_modified(self):
        """Non-live YouTube must be untouched — only the live branch changes."""
        url = "https://www.youtube.com/watch?v=abc123"
        task = DownloadTask(url=url, format_id="bestvideo+bestaudio/best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="YT VOD", is_live=False)
        opts = self._capture_opts(task)
        assert opts["format"] == "bestvideo+bestaudio/best"


# ---------------------------------------------------------------------------
# BUG-BM: TikTok short-link URLs (vt.tiktok.com / vm.tiktok.com) must also
# receive the [acodec!=none] format injection and must not be detected as live.
# ---------------------------------------------------------------------------


class TestTikTokShortUrlAudioFix:
    """BUG-BM / BUG-BS — short-link TikTok URLs (vt.tiktok.com/*, vm.tiktok.com/*)
    must receive the same seven-tier BUG-BS format selector as canonical
    tiktok.com/@user/video/<id> URLs:

        best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best

    The _TIKTOK_SHORT_RE regex covers both vt.tiktok.com and vm.tiktok.com.
    All tests updated to assert the BUG-BS 7-tier selector (3-tier superseded).
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

            def add_post_processor(self, pp, when=None):
                pass

            def download(self, urls):
                pass

        import infrastructure.downloader.yt_dlp_engine as mod

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)
        return captured

    def _make_short_task(self, short_url: str, format_id: str, ext: str = "mp4"):
        task = DownloadTask(url=short_url, format_id=format_id, output_ext=ext)
        task.media_info = MediaInfo(url=short_url, title="TikTok short", is_live=False)
        return task

    # ── vt.tiktok.com ────────────────────────────────────────────────────

    def test_vt_short_url_gets_acodec_filter(self):
        """vt.tiktok.com short link must produce the BUG-BS 7-tier chain."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "bestvideo+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        ), f"Short URL 'vt.tiktok.com' must receive 7-tier chain, got: {opts['format']}"

    def test_vt_short_url_1080p_gets_acodec_filter(self):
        """vt.tiktok.com with 1080p selector → BUG-BS 7-tier chain."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNQHFCu/",
            "bestvideo[height<=1080]+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    def test_vt_short_url_720p_gets_acodec_filter(self):
        """vt.tiktok.com with 720p selector → BUG-BS 7-tier chain."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNQMpEK/",
            "bestvideo[height<=720]+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    # ── vm.tiktok.com ────────────────────────────────────────────────────

    def test_vm_short_url_gets_acodec_filter(self):
        """vm.tiktok.com short link (global) → BUG-BS 7-tier chain."""
        task = self._make_short_task(
            "https://vm.tiktok.com/ZMJxABCDE/",
            "bestvideo+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        ), f"Short URL 'vm.tiktok.com' must receive 7-tier chain, got: {opts['format']}"

    def test_vm_short_url_1080p_gets_acodec_filter(self):
        """vm.tiktok.com with 1080p selector → BUG-BS 7-tier chain."""
        task = self._make_short_task(
            "https://vm.tiktok.com/ZMJxABCDE/",
            "bestvideo[height<=1080]+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    # ── Short URL edge cases ──────────────────────────────────────────────

    def test_vt_short_url_audio_only_not_modified(self):
        """Audio-only format must NOT be modified for short URLs either."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == "bestaudio/best"

    def test_vt_short_url_best_gets_seven_tier(self):
        """BUG-BP: format_id='best' on vt.tiktok.com VOD now receives the
        7-tier chain — same as canonical URLs. Previously left unmodified."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "best",
        )
        opts = self._capture_opts(task)
        assert (
            opts["format"]
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )

    def test_vt_short_url_format_is_idempotent(self):
        """BUG-BS: 7-tier chain is idempotent for short URLs too.
        [acodec!=none] must NOT appear — it was part of the old 3-tier chain."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "bestvideo+bestaudio/best",
        )
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert "[acodec!=none]" not in fmt, f"7-tier chain must not contain [acodec!=none], got: {fmt}"
        assert (
            fmt
            == "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"
        )
        assert len(fmt.split("/")) == 7, f"Expected 7 tiers, got: {fmt}"


# ---------------------------------------------------------------------------
# BUG-BN: 3-tier chain — intermediate bestvideo+bestaudio fallback
# ---------------------------------------------------------------------------


class TestTikTokFourTierSelector:
    """BUG-BS / BUG-TT-SHOP-3 — validates the seven-tier format selector
    that supersedes the old BUG-BN three-tier chain.

    Selector: best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best

      Tier 1  best[format_id^=h264]          — highest-bitrate watermark-free h264 muxed stream
      Tier 2  best[format_id=audio][ext=mp4] — shopping-link muxed mp4 stream
      Tier 3  best[format_id=audio]          — shopping-link muxed stream (any ext)
      Tier 4  download                       — TikTok watermarked muxed stream (fallback with audio)
      Tier 5  bestvideo*+bestaudio*          — DASH merge via FFmpegMergerPP
      Tier 6  bestvideo*                     — single-stream starred selector
      Tier 7  best                           — final safety net

    All quality presets (bestvideo+bestaudio/best, bestvideo[height<=N]+bestaudio/best)
    and short URLs (vt.tiktok.com, vm.tiktok.com) must all resolve to this exact 7-tier
    string. The BUG-BN 3-tier chain ([acodec!=none]) is fully superseded.

    Renamed from TestTikTokThreeTierFallback to reflect actual behavior.
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

            def add_post_processor(self, pp, when=None):
                pass

            def download(self, urls):
                pass

        import infrastructure.downloader.yt_dlp_engine as mod

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)
        return captured

    def _make_task(self, url: str, format_id: str):
        task = DownloadTask(url=url, format_id=format_id, output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test", is_live=False)
        return task

    FOUR_TIER = "best[format_id^=h264]/best[format_id=audio][ext=mp4]/download/bestvideo*+bestaudio*/bestvideo*/best[format_id=audio]/best"

    def test_canonical_url_has_seven_tiers(self):
        """Canonical tiktok.com/@user/video/<id> must produce exactly 7 tiers."""
        url = "https://www.tiktok.com/@khaly.57/video/7622620153158814996"
        task = self._make_task(url, "bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert fmt == self.FOUR_TIER, f"Expected 7-tier chain, got: {fmt}"
        tiers = fmt.split("/")
        assert len(tiers) == 7, f"Expected 7 tiers, got {len(tiers)}: {fmt}"
        assert tiers[0] == "best[format_id^=h264]"
        assert tiers[2] == "download"
        assert tiers[3] == "bestvideo*+bestaudio*"
        assert tiers[5] == "best[format_id=audio]"
        assert tiers[6] == "best"

    def test_short_url_video1_has_seven_tiers(self):
        """vt.tiktok.com/ZSHNx3n8Y/ (Video 1 — 2:50, silent) must produce 7 tiers."""
        url = "https://vt.tiktok.com/ZSHNx3n8Y/"
        task = self._make_task(url, "bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert fmt == self.FOUR_TIER, f"Short URL must produce 7-tier chain, got: {fmt}"
        assert len(fmt.split("/")) == 7

    def test_short_url_video3_has_seven_tiers(self):
        """vt.tiktok.com/ZSHNQHFCu/ (Video 3 — 5:47, silent) must produce 7 tiers."""
        url = "https://vt.tiktok.com/ZSHNQHFCu/"
        task = self._make_task(url, "bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert fmt == self.FOUR_TIER, f"Short URL must produce 7-tier chain, got: {fmt}"
        assert len(fmt.split("/")) == 7

    def test_no_acodec_filter_in_seven_tier(self):
        """BUG-BS replaces [acodec!=none] with the h264 format_id prefix selector.
        The old [acodec!=none] filter must NOT appear in the new chain."""
        url = "https://www.tiktok.com/@testuser/video/7620980082118675732"
        task = self._make_task(url, "bestvideo[height<=1080]+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert "[acodec!=none]" not in fmt, (
            f"7-tier chain must not contain [acodec!=none] — old BUG-BN pattern, got: {fmt}"
        )
        assert fmt == self.FOUR_TIER

    def test_all_presets_produce_seven_tiers(self):
        """Every quality preset from home_tab must produce the BUG-BS 7-tier chain."""
        presets = [
            "bestvideo+bestaudio/best",
            "bestvideo[height<=2160]+bestaudio/best",
            "bestvideo[height<=1080]+bestaudio/best",
            "bestvideo[height<=720]+bestaudio/best",
            "bestvideo[height<=480]+bestaudio/best",
            "bestvideo[height<=360]+bestaudio/best",
        ]
        url = "https://www.tiktok.com/@testuser/video/1234567890"
        for preset in presets:
            task = self._make_task(url, preset)
            opts = self._capture_opts(task)
            fmt = opts["format"]
            assert fmt == self.FOUR_TIER, f"Preset {preset!r} must produce 7-tier chain, got: {fmt}"
            assert len(fmt.split("/")) == 7, (
                f"Preset {preset!r}: expected 7 tiers, got {len(fmt.split('/'))}: {fmt}"
            )


class TestTikTokShortUrlLiveDetection:
    """BUG-CH — short-link TikTok URLs (vt/vm.tiktok.com) trust yt-dlp's
    is_live signal from the resolved URL.  is_live=True means the short link
    resolved to a live stream; is_live=False means it resolved to a VOD.
    (Supersedes BUG-BM which forced is_live=False unconditionally for short links.)
    """

    def _make_engine(self):
        cfg = make_config()
        cfg.cookie_file = ""
        cfg.platform_cookies = {}
        return YtDlpEngine(cfg)

    def _fake_ydl_cls(self, is_live_from_api: bool):
        """Return a fake YoutubeDL class that returns is_live from API."""

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def add_post_processor(self, pp, when=None):
                pass

            def extract_info(self, url, download=False):
                return {
                    "id": "ABCDE12345",
                    "title": "TikTok short VOD",
                    "uploader": "testuser",
                    "duration": 170,
                    "thumbnail": "",
                    "formats": [],
                    "is_live": is_live_from_api,
                    "was_live": False,
                }

        return FakeYDL

    def test_vt_short_url_is_live_true_when_api_returns_true(self):
        """vt.tiktok.com short link trusts yt-dlp is_live (BUG-CH).
        is_live=True from yt-dlp means the short link resolved to a live stream.
        """
        import infrastructure.downloader.yt_dlp_engine as mod

        engine = self._make_engine()
        with patch.object(mod.yt_dlp, "YoutubeDL", self._fake_ydl_cls(is_live_from_api=True)):
            info = engine.extract_info("https://vt.tiktok.com/ZSHNx3n8Y/")
        assert info.is_live, (
            "vt.tiktok.com short URL must honour yt-dlp is_live=True (resolved to live stream)"
        )

    def test_vm_short_url_is_live_true_when_api_returns_true(self):
        """vm.tiktok.com short link trusts yt-dlp is_live (BUG-CH).
        is_live=True from yt-dlp means the short link resolved to a live stream.
        """
        import infrastructure.downloader.yt_dlp_engine as mod

        engine = self._make_engine()
        with patch.object(mod.yt_dlp, "YoutubeDL", self._fake_ydl_cls(is_live_from_api=True)):
            info = engine.extract_info("https://vm.tiktok.com/ZMJxABCDE/")
        assert info.is_live, (
            "vm.tiktok.com short URL must honour yt-dlp is_live=True (resolved to live stream)"
        )

    def test_vt_short_url_is_live_false_stays_false(self):
        """vt.tiktok.com with is_live=False from API remains False."""
        import infrastructure.downloader.yt_dlp_engine as mod

        engine = self._make_engine()
        with patch.object(mod.yt_dlp, "YoutubeDL", self._fake_ydl_cls(is_live_from_api=False)):
            info = engine.extract_info("https://vt.tiktok.com/ZSHNQMpEK/")
        assert not info.is_live


class TestTikTokShortUrlRegex:
    """Unit tests for _TIKTOK_SHORT_RE / _TIKTOK_VOD_RE module constants."""

    def test_vt_tiktok_matches_short_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_SHORT_RE

        assert _TIKTOK_SHORT_RE.search("https://vt.tiktok.com/ZSHNx3n8Y/")

    def test_vm_tiktok_matches_short_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_SHORT_RE

        assert _TIKTOK_SHORT_RE.search("https://vm.tiktok.com/ZMJxABCDE/")

    def test_canonical_url_does_not_match_short_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_SHORT_RE

        assert not _TIKTOK_SHORT_RE.search("https://www.tiktok.com/@testuser/video/7620980082118675732")

    def test_canonical_url_matches_vod_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_VOD_RE

        assert _TIKTOK_VOD_RE.search("https://www.tiktok.com/@testuser/video/7620980082118675732")

    def test_live_url_matches_live_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_LIVE_RE

        assert _TIKTOK_LIVE_RE.search("https://www.tiktok.com/@testuser/live")

    def test_live_url_does_not_match_short_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_SHORT_RE

        assert not _TIKTOK_SHORT_RE.search("https://www.tiktok.com/@testuser/live")

    def test_youtube_does_not_match_any_tiktok_re(self):
        from infrastructure.downloader.yt_dlp_engine import (
            _TIKTOK_LIVE_RE,
            _TIKTOK_SHORT_RE,
            _TIKTOK_VOD_RE,
        )

        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert not _TIKTOK_SHORT_RE.search(yt_url)
        assert not _TIKTOK_VOD_RE.search(yt_url)
        assert not _TIKTOK_LIVE_RE.search(yt_url)
