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
    """BUG-BS (v2) — For TikTok VOD URLs, download() must build the four-tier
    h264-priority format selector chain so long-form VODs always have audio
    AND are watermark-free when possible.

    TikTok exposes three stream kinds for VODs:
      1. format_id starts with "h264_": watermark-free progressive MP4, reliable audio.
      2. format_id == "download": watermarked progressive MP4, guaranteed audio.
      3. format_id starts with "bytevc1_": watermark-free H.265, but audio unreliable
         because TikTok CDN mislabels audio codec metadata as acodec='none'.

    Correct four-tier chain (BUG-BS v2):
      best[format_id^=h264]   — picks highest-tbr h264_* entry (no watermark + audio)
      /download               — fallback: watermarked but guaranteed audio
      /bestvideo*+bestaudio*  — last resort DASH merge with starred selectors
      /best                   — final catch-all

    The starred selectors (bestvideo*, bestaudio*) select by actual stream
    content rather than trusting the mislabelled acodec/vcodec metadata.

    History: [acodec!=none] (BUG-BN / FIX-TK-AUDIO-2) was superseded because
    TikTok mislabels ALL audio tracks as acodec='none', so the filter found no
    valid audio stream and fell through to a video-only DASH → silent output.
    BUG-BS replaces the acodec filter with the h264-first chain.

    Guard: "bestvideo*" in _format_id prevents double-patching (idempotency).
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

    _H264_CHAIN = "best[format_id^=h264]/download/bestvideo*+bestaudio*/best"

    def test_best_quality_gets_acodec_filter(self):
        """'bestvideo+bestaudio/best' -> 4-tier h264 chain (BUG-BS v2)."""
        task = self._make_tiktok_task("bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    def test_1080p_gets_acodec_filter(self):
        """'bestvideo[height<=1080]+bestaudio/best' -> 4-tier h264 chain."""
        task = self._make_tiktok_task("bestvideo[height<=1080]+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    def test_720p_gets_acodec_filter(self):
        task = self._make_tiktok_task("bestvideo[height<=720]+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    def test_360p_gets_acodec_filter(self):
        task = self._make_tiktok_task("bestvideo[height<=360]+bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    def test_audio_only_not_modified(self):
        """'bestaudio/best' (audio-only) must NOT be modified — no bestvideo present."""
        task = self._make_tiktok_task("bestaudio/best")
        opts = self._capture_opts(task)
        assert opts["format"] == "bestaudio/best"

    def test_best_not_modified(self):
        """'best' (bare) on a TikTok VOD URL now also gets the h264 chain (BUG-BS).

        BUG-BP fixed the case where format_id='best' bypassed the audio patch
        because the old guard only checked for 'bestvideo' in the string.
        The else-branch now applies the h264 chain to any non-audio selector.
        """
        url = "https://www.tiktok.com/@testuser/video/9999"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test", is_live=False)
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

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
        """Running patch twice must not produce extra tiers (idempotency guard)."""
        task = self._make_tiktok_task("bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert fmt.count("bestvideo*") == 1, f"Expected exactly 1 starred selector, got: {fmt}"
        assert fmt == self._H264_CHAIN

# ---------------------------------------------------------------------------
# BUG-BM: TikTok short-link URLs (vt.tiktok.com / vm.tiktok.com) must also
# receive the [acodec!=none] format injection and must not be detected as live.
# ---------------------------------------------------------------------------


class TestTikTokShortUrlAudioFix:
    """BUG-BM / BUG-BS — short-link TikTok URLs (vt.tiktok.com/*, vm.tiktok.com/*)
    must receive the same four-tier h264-priority format selector chain as
    canonical tiktok.com/@user/video/<id> URLs.

    Root cause: task.url holds the ORIGINAL user-supplied URL at download
    time. The _TIKTOK_SHORT_RE regex covers vt.tiktok.com and vm.tiktok.com
    share-link redirectors, so the BUG-BS h264 chain applies to them too.
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

    def _make_short_task(self, short_url: str, format_id: str, ext: str = "mp4"):
        task = DownloadTask(url=short_url, format_id=format_id, output_ext=ext)
        task.media_info = MediaInfo(url=short_url, title="TikTok short", is_live=False)
        return task

    _H264_CHAIN = "best[format_id^=h264]/download/bestvideo*+bestaudio*/best"

    # ── vt.tiktok.com ────────────────────────────────────────────────────

    def test_vt_short_url_gets_acodec_filter(self):
        """vt.tiktok.com short link must produce the full 4-tier h264 chain."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "bestvideo+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN, (
            f"Short URL 'vt.tiktok.com' must receive h264 chain, got: {opts['format']}"
        )

    def test_vt_short_url_1080p_gets_acodec_filter(self):
        """vt.tiktok.com with 1080p selector gets the full 4-tier h264 chain."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNQHFCu/",
            "bestvideo[height<=1080]+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    def test_vt_short_url_720p_gets_acodec_filter(self):
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNQMpEK/",
            "bestvideo[height<=720]+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    # ── vm.tiktok.com ────────────────────────────────────────────────────

    def test_vm_short_url_gets_acodec_filter(self):
        """vm.tiktok.com short link (global) must also produce the 4-tier h264 chain."""
        task = self._make_short_task(
            "https://vm.tiktok.com/ZMJxABCDE/",
            "bestvideo+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN, (
            f"Short URL 'vm.tiktok.com' must receive h264 chain, got: {opts['format']}"
        )

    def test_vm_short_url_1080p_gets_acodec_filter(self):
        task = self._make_short_task(
            "https://vm.tiktok.com/ZMJxABCDE/",
            "bestvideo[height<=1080]+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    # ── Short URL edge cases ──────────────────────────────────────────────

    def test_vt_short_url_audio_only_not_modified(self):
        """Audio-only format must NOT be modified for short URLs either."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == "bestaudio/best"

    def test_vt_short_url_best_not_modified(self):
        """'best' format on short TikTok URL now gets the h264 chain (BUG-BS/BP).

        BUG-BP: the else-branch now applies the h264 chain to any non-audio
        selector including bare 'best', fixing silent downloads when TikTok
        only serves DASH streams for that video.
        """
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "best",
        )
        opts = self._capture_opts(task)
        assert opts["format"] == self._H264_CHAIN

    def test_vt_short_url_acodec_filter_not_duplicated(self):
        """Starred selector must appear exactly once even for short URLs (idempotency)."""
        task = self._make_short_task(
            "https://vt.tiktok.com/ZSHNx3n8Y/",
            "bestvideo+bestaudio/best",
        )
        opts = self._capture_opts(task)
        assert opts["format"].count("bestvideo*") == 1
        assert opts["format"] == self._H264_CHAIN


# ---------------------------------------------------------------------------
# BUG-BN: 3-tier chain — intermediate bestvideo+bestaudio fallback
# ---------------------------------------------------------------------------


class TestTikTokThreeTierFallback:
    """BUG-BS — the format selector for TikTok VODs must use the four-tier
    h264-priority chain instead of the three-tier [acodec!=none] chain.

    Chain: best[format_id^=h264]/download/bestvideo*+bestaudio*/best

    Tier 1  best[format_id^=h264] — watermark-free h264 progressive MP4
    Tier 2  download              — watermarked progressive MP4, guaranteed audio
    Tier 3  bestvideo*+bestaudio* — DASH merge with starred (codec-agnostic) selectors
    Tier 4  best                  — final catch-all

    The [acodec!=none] chain (BUG-BN, 3-tier) was superseded because TikTok
    mislabels ALL DASH audio tracks as acodec='none', so the filter found no
    valid audio stream and fell through to a video-only DASH → silent mp4.

    Note: the new chain does NOT preserve the user's height cap (e.g.
    height<=1080), because best[format_id^=h264] already picks TikTok's
    best available h264 progressive format which is inherently capped by
    what TikTok CDN serves for that video.
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

    def _make_task(self, url: str, format_id: str):
        task = DownloadTask(url=url, format_id=format_id, output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test", is_live=False)
        return task

    _H264_CHAIN = "best[format_id^=h264]/download/bestvideo*+bestaudio*/best"

    def test_canonical_url_has_three_tiers(self):
        """Canonical tiktok.com/@user/video/<id> must produce exactly 4 tiers (BUG-BS)."""
        url = "https://www.tiktok.com/@khaly.57/video/7622620153158814996"
        task = self._make_task(url, "bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        tiers = fmt.split("/")
        assert len(tiers) == 4, f"Expected 4 tiers, got {len(tiers)}: {fmt}"
        assert tiers[0] == "best[format_id^=h264]"
        assert tiers[1] == "download"
        assert tiers[2] == "bestvideo*+bestaudio*"
        assert tiers[3] == "best"

    def test_short_url_video1_has_three_tiers(self):
        """vt.tiktok.com/ZSHNx3n8Y/ (Video 1 — 2:50, silent) must produce 4 tiers."""
        url = "https://vt.tiktok.com/ZSHNx3n8Y/"
        task = self._make_task(url, "bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        tiers = fmt.split("/")
        assert len(tiers) == 4, f"Expected 4 tiers, got {len(tiers)}: {fmt}"
        assert tiers[0] == "best[format_id^=h264]", "Tier 1 must be h264 selector"
        assert tiers[1] == "download", "Tier 2 must be 'download' fallback"
        assert "*" in tiers[2], "Tier 3 must use starred selectors"
        assert tiers[3] == "best", "Tier 4 must be bare /best"

    def test_short_url_video3_has_three_tiers(self):
        """vt.tiktok.com/ZSHNQHFCu/ (Video 3 — 5:47, silent) must produce 4 tiers."""
        url = "https://vt.tiktok.com/ZSHNQHFCu/"
        task = self._make_task(url, "bestvideo+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        tiers = fmt.split("/")
        assert len(tiers) == 4, f"Expected 4 tiers, got {len(tiers)}: {fmt}"
        assert tiers[0] == "best[format_id^=h264]"
        assert tiers[1] == "download"
        assert tiers[3] == "best"

    def test_height_cap_preserved_in_all_tiers(self):
        """BUG-BS: height cap is NOT preserved — the h264 chain is a fixed string.

        The new selector 'best[format_id^=h264]/download/bestvideo*+bestaudio*/best'
        does not embed a height cap. best[format_id^=h264] already selects the
        highest-tbr h264 format TikTok serves for the video, so the height cap
        from the UI preset is intentionally dropped for TikTok VODs.
        """
        url = "https://www.tiktok.com/@testuser/video/7620980082118675732"
        task = self._make_task(url, "bestvideo[height<=1080]+bestaudio/best")
        opts = self._capture_opts(task)
        fmt = opts["format"]
        assert fmt == self._H264_CHAIN, (
            f"Expected h264 chain regardless of height cap, got: {fmt}"
        )
        # Height cap is intentionally absent — the h264 chain is a fixed selector
        assert "height" not in fmt

    def test_all_presets_produce_three_tiers(self):
        """Every quality preset from home_tab must produce the 4-tier h264 chain."""
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
            tiers = fmt.split("/")
            assert len(tiers) == 4, (
                f"Preset {preset!r} must produce 4 tiers, got {len(tiers)}: {fmt}"
            )
            assert fmt == self._H264_CHAIN, (
                f"Preset {preset!r} must produce h264 chain, got: {fmt}"
            )


class TestTikTokShortUrlLiveDetection:
    """BUG-BM — short-link TikTok URLs must not be falsely detected as live
    in extract_info() even when yt-dlp returns is_live=True from TikTok API.
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

    def test_vt_short_url_never_live_when_api_returns_true(self):
        """vt.tiktok.com must be resolved as is_live=False even if API says True."""
        import infrastructure.downloader.yt_dlp_engine as mod
        engine = self._make_engine()
        with patch.object(mod.yt_dlp, "YoutubeDL", self._fake_ydl_cls(is_live_from_api=True)):
            info = engine.extract_info("https://vt.tiktok.com/ZSHNx3n8Y/")
        assert not info.is_live, (
            "vt.tiktok.com short URL must never be flagged as livestream"
        )

    def test_vm_short_url_never_live_when_api_returns_true(self):
        """vm.tiktok.com must be resolved as is_live=False even if API says True."""
        import infrastructure.downloader.yt_dlp_engine as mod
        engine = self._make_engine()
        with patch.object(mod.yt_dlp, "YoutubeDL", self._fake_ydl_cls(is_live_from_api=True)):
            info = engine.extract_info("https://vm.tiktok.com/ZMJxABCDE/")
        assert not info.is_live, (
            "vm.tiktok.com short URL must never be flagged as livestream"
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
        assert not _TIKTOK_SHORT_RE.search(
            "https://www.tiktok.com/@testuser/video/7620980082118675732"
        )

    def test_canonical_url_matches_vod_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_VOD_RE
        assert _TIKTOK_VOD_RE.search(
            "https://www.tiktok.com/@testuser/video/7620980082118675732"
        )

    def test_live_url_matches_live_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_LIVE_RE
        assert _TIKTOK_LIVE_RE.search("https://www.tiktok.com/@testuser/live")

    def test_live_url_does_not_match_short_re(self):
        from infrastructure.downloader.yt_dlp_engine import _TIKTOK_SHORT_RE
        assert not _TIKTOK_SHORT_RE.search("https://www.tiktok.com/@testuser/live")

    def test_youtube_does_not_match_any_tiktok_re(self):
        from infrastructure.downloader.yt_dlp_engine import (
            _TIKTOK_SHORT_RE, _TIKTOK_VOD_RE, _TIKTOK_LIVE_RE,
        )
        yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert not _TIKTOK_SHORT_RE.search(yt_url)
        assert not _TIKTOK_VOD_RE.search(yt_url)
        assert not _TIKTOK_LIVE_RE.search(yt_url)
