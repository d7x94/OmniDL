"""
tests/test_bug_tt18_tt19.py
Unit tests for BUG-TT-18 and BUG-TT-19 fixes.

BUG-TT-18: _extract_tiktok_live_hls_url must select the highest-quality
           m3u8 format (height/tbr DESC), not the first (lowest) in the list.
           On attempt-0 zero-byte failure, re-extract a fresh HLS URL before
           falling back to yt-dlp.

BUG-TT-19: analyse_url must retry extract_info after 5s when the TikTok live
           checker returns None (bot-detection / API race), not fail immediately.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import yt_dlp

from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.extra_args = ""
    cfg.proxy = ""
    cfg.cookie_file = ""
    cfg.use_cookies = False
    cfg.cookies_browser = "chrome"
    cfg.max_retries = 3
    cfg.embed_thumbnail = False
    cfg.embed_metadata = False
    cfg.download_dir = tmp_path
    cfg.config_path = tmp_path / "config.json"
    return cfg


def _make_tiktok_live_task(url: str) -> DownloadTask:
    task = DownloadTask(url=url, format_id="best", output_ext="ts")
    task.media_info = MediaInfo(url=url, title="TikTok Live", is_live=True)
    return task


# ---------------------------------------------------------------------------
# BUG-TT-18 Change A: _extract_tiktok_live_hls_url selects best quality
# ---------------------------------------------------------------------------

class TestExtractTiktokLiveHlsQuality:
    """BUG-TT-18: highest-quality m3u8 format must be selected, not the first."""

    def _make_engine(self, tmp_path: Path) -> YtDlpEngine:
        return YtDlpEngine(_make_config(tmp_path))

    def _make_formats(self):
        """Return a list of formats ordered lowest-to-highest (yt-dlp default)."""
        return [
            {
                "protocol": "m3u8_native",
                "url": "https://cdn.tiktok.com/stream_ld/index.m3u8",
                "height": 270,
                "tbr": 400.0,
                "ext": "mp4",
            },
            {
                "protocol": "m3u8_native",
                "url": "https://cdn.tiktok.com/stream_sd/index.m3u8",
                "height": 540,
                "tbr": 1000.0,
                "ext": "mp4",
            },
            {
                "protocol": "m3u8_native",
                "url": "https://cdn.tiktok.com/stream_hd/index.m3u8",
                "height": 720,
                "tbr": 2000.0,
                "ext": "mp4",
            },
        ]

    def test_selects_highest_quality_format(self, tmp_path):
        """Must return the 720p URL, not the 270p (_ld) URL."""
        import infrastructure.downloader.yt_dlp_engine as mod

        engine = self._make_engine(tmp_path)
        formats = self._make_formats()

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False):
                return {"formats": formats, "id": "7637468409928977159"}

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            result = engine._extract_tiktok_live_hls_url(
                "https://vt.tiktok.com/ZS9FUF87/"
            )

        assert result is not None
        hls_url, vid_id = result
        assert "stream_hd" in hls_url, (
            f"Expected HD stream URL, got: {hls_url!r} (BUG-TT-18)"
        )
        assert "stream_ld" not in hls_url, (
            f"Must not select _ld stream, got: {hls_url!r} (BUG-TT-18)"
        )

    def test_selects_by_height_then_tbr(self, tmp_path):
        """When multiple formats have the same height, pick highest tbr."""
        import infrastructure.downloader.yt_dlp_engine as mod

        engine = self._make_engine(tmp_path)
        formats = [
            {
                "protocol": "m3u8_native",
                "url": "https://cdn.tiktok.com/stream_low_tbr/index.m3u8",
                "height": 720,
                "tbr": 1000.0,
            },
            {
                "protocol": "m3u8_native",
                "url": "https://cdn.tiktok.com/stream_high_tbr/index.m3u8",
                "height": 720,
                "tbr": 3000.0,
            },
        ]

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False):
                return {"formats": formats, "id": "abc123"}

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            result = engine._extract_tiktok_live_hls_url("https://vt.tiktok.com/ZS9/")

        assert result is not None
        hls_url, _ = result
        assert "high_tbr" in hls_url, (
            f"Must prefer higher tbr at same height, got: {hls_url!r} (BUG-TT-18)"
        )

    def test_returns_none_when_no_m3u8_and_no_fallback(self, tmp_path):
        """Returns None when no m3u8 formats and no .m3u8 fallback URLs."""
        import infrastructure.downloader.yt_dlp_engine as mod

        engine = self._make_engine(tmp_path)

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False):
                return {"formats": [{"protocol": "https", "url": "https://cdn/video.mp4"}], "id": "x"}

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            result = engine._extract_tiktok_live_hls_url("https://vt.tiktok.com/ZS9/")

        assert result is None


# ---------------------------------------------------------------------------
# BUG-TT-19: analyse_url retries extract_info after checker returns None
# ---------------------------------------------------------------------------

class TestAnalyseUrlTt19Retry:
    """BUG-TT-19: when TikTok checker returns None, wait 5s and retry extract_info."""

    def _make_engine(self, tmp_path: Path) -> YtDlpEngine:
        return YtDlpEngine(_make_config(tmp_path))

    def _make_download_service(self, tmp_path: Path):
        from app.event_bus import EventBus  # noqa: PLC0415
        from app.services.download_service import DownloadService  # noqa: PLC0415
        cfg = _make_config(tmp_path)
        bus = EventBus()
        engine = self._make_engine(tmp_path)
        return DownloadService(
            config=cfg,
            download_manager=MagicMock(),
            history_repo=MagicMock(),
            engine=engine,
            event_bus=bus,
        )

    def _run_analyse_url(self, ds, url, on_done, on_error):
        """Run analyse_url and wait for the background thread to finish."""
        import threading
        done = threading.Event()
        _orig_done = on_done.side_effect
        _orig_error = on_error.side_effect

        def _wrap_done(info):
            on_done(info)
            done.set()

        def _wrap_error(msg):
            on_error(msg)
            done.set()

        ds.analyse_url(url, on_done=_wrap_done, on_error=_wrap_error)
        done.wait(timeout=10)

    def test_retry_succeeds_after_checker_returns_none(self, tmp_path):
        """When checker returns None but retry extract_info succeeds, on_done is called."""
        url = "https://vt.tiktok.com/ZS9FUF87/"
        ds = self._make_download_service(tmp_path)

        retry_info = MediaInfo(
            url="https://www.tiktok.com/@user/live",
            title="@user -- TikTok Live",
            is_live=True,
        )

        on_done = MagicMock()
        on_error = MagicMock()

        not_live_exc = yt_dlp.utils.DownloadError("The channel is not currently live")

        with patch.object(
                 ds._engine, "extract_info",
                 side_effect=[not_live_exc, retry_info],
             ) as mock_extract, \
             patch(
                 "utils.tiktok_live_checker._check_tiktok_live_with_room_id",
                 return_value=None,
             ), \
             patch(
                 "utils.tiktok_live_checker._resolve_short_link",
                 return_value="https://www.tiktok.com/@user/live",
             ), \
             patch("app.services.download_service._resolve_cookie", return_value=""), \
             patch("app.services.download_service._prepare_cookie_for_use",
                   return_value=("", False)), \
             patch("time.sleep"):

            self._run_analyse_url(ds, url, on_done, on_error)

        assert mock_extract.call_count == 2, (
            f"extract_info must be called twice (initial + retry), got {mock_extract.call_count}"
        )
        on_done.assert_called_once_with(retry_info)
        on_error.assert_not_called()

    def test_retry_fails_falls_through_to_on_error(self, tmp_path):
        """When both initial and retry extract_info fail, on_error is called."""
        url = "https://vt.tiktok.com/ZS9FUF87/"
        ds = self._make_download_service(tmp_path)

        on_done = MagicMock()
        on_error = MagicMock()

        not_live_exc = yt_dlp.utils.DownloadError("The channel is not currently live")

        with patch.object(
                 ds._engine, "extract_info",
                 side_effect=[not_live_exc, not_live_exc],
             ), \
             patch(
                 "utils.tiktok_live_checker._check_tiktok_live_with_room_id",
                 return_value=None,
             ), \
             patch(
                 "utils.tiktok_live_checker._resolve_short_link",
                 return_value="https://www.tiktok.com/@user/live",
             ), \
             patch("app.services.download_service._resolve_cookie", return_value=""), \
             patch("app.services.download_service._prepare_cookie_for_use",
                   return_value=("", False)), \
             patch("time.sleep"):

            self._run_analyse_url(ds, url, on_done, on_error)

        on_done.assert_not_called()
        on_error.assert_called_once()
