"""
tests/test_coverage_gap4.py
Coverage for previously untested paths:

- app/services/ffmpeg_trim_service.py: trim_video worker thread (_work) and
  _build_cmd rotate/speed/volume/mute branches, _font_clause no-font branch.
- infrastructure/downloader/yt_dlp_engine.py:
  - _extract_tiktok_live_hls_url BUG-TT-25/26/29 "not currently live" fallbacks
  - _download_tiktok_live_direct (FFmpeg subprocess paths)
  - _download_tiktok_live_hls_curl (curl_cffi HLS paths)
"""

import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

import infrastructure.downloader.yt_dlp_engine as mod
from app.services.ffmpeg_convert_service import (
    ConversionCancelledError,
    FfmpegConvertService,
)
from app.services.ffmpeg_trim_service import _build_cmd, _font_clause, trim_video
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


def _make_live_task(url: str = "https://www.tiktok.com/@someuser/live") -> DownloadTask:
    task = DownloadTask(url=url, format_id="best", output_ext="ts")
    task.media_info = MediaInfo(url=url, title="TikTok Live", is_live=True)
    return task


class _RaisingYDL:
    """Context-manager fake whose extract_info raises 'not currently live'."""

    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def extract_info(self, url, download=False):
        raise yt_dlp.utils.DownloadError("This channel is not currently live")


def _resp(status_code=200, text="", content=b""):
    return types.SimpleNamespace(status_code=status_code, text=text, content=content)


# ---------------------------------------------------------------------------
# ffmpeg_trim_service: _font_clause
# ---------------------------------------------------------------------------


class TestFontClause:
    def test_returns_empty_when_no_font(self):
        with patch("app.services.ffmpeg_trim_service.find_font_path", return_value=None):
            assert _font_clause() == ""

    def test_returns_fontfile_clause(self):
        with patch("app.services.ffmpeg_trim_service.find_font_path", return_value="C:/Fonts/arial.ttf"):
            assert _font_clause() == "fontfile=C\\:/Fonts/arial.ttf:"


# ---------------------------------------------------------------------------
# ffmpeg_trim_service: trim_video worker thread
# ---------------------------------------------------------------------------


class TestTrimVideoWorker:
    def _wait(self, ev: threading.Event):
        assert ev.wait(timeout=5.0), "trim_video worker did not finish in time"

    def test_success_renames_temp_and_calls_on_done(self, tmp_path):
        src = tmp_path / "in.mp4"
        src.write_bytes(b"x")
        out = tmp_path / "out.mp4"
        done = threading.Event()
        results = []

        def fake_run(cmd, duration_s, on_progress, cancel_event=None):
            # _work expects _run_ffmpeg to have produced the temp file
            Path(cmd[-1]).write_bytes(b"video")

        loc = MagicMock()
        loc.ffmpeg_bin = "ffmpeg"
        with patch("app.services.ffmpeg_trim_service.locate_ffmpeg", return_value=loc), \
             patch.object(FfmpegConvertService, "_run_ffmpeg", staticmethod(fake_run)):
            trim_video(
                src, out, 0, 2000,
                on_done=lambda p: (results.append(p), done.set()),
                on_error=lambda m: (results.append(m), done.set()),
            )
            self._wait(done)

        assert results == [out]
        assert out.exists()

    def test_speed_adjusts_duration_and_reencodes(self, tmp_path):
        src = tmp_path / "in.mp4"
        src.write_bytes(b"x")
        out = tmp_path / "out.mp4"
        done = threading.Event()
        captured = {}

        def fake_run(cmd, duration_s, on_progress, cancel_event=None):
            captured["cmd"] = cmd
            captured["duration"] = duration_s
            Path(cmd[-1]).write_bytes(b"video")

        loc = MagicMock()
        loc.ffmpeg_bin = "ffmpeg"
        with patch("app.services.ffmpeg_trim_service.locate_ffmpeg", return_value=loc), \
             patch.object(FfmpegConvertService, "_run_ffmpeg", staticmethod(fake_run)):
            trim_video(src, out, 0, 4000, speed=2.0, on_done=lambda p: done.set())
            self._wait(done)

        assert captured["duration"] == pytest.approx(2.0)
        assert "setpts=PTS/2.0" in " ".join(captured["cmd"])

    def test_ffmpeg_missing_calls_on_error(self, tmp_path):
        src = tmp_path / "in.mp4"
        src.write_bytes(b"x")
        out = tmp_path / "out.mp4"
        done = threading.Event()
        errors = []

        with patch("app.services.ffmpeg_trim_service.locate_ffmpeg", return_value=None):
            trim_video(src, out, 0, 1000, on_error=lambda m: (errors.append(m), done.set()))
            self._wait(done)

        assert errors and "FFmpeg" in errors[0]
        assert not out.exists()

    def test_cancelled_calls_on_error_and_unlinks_temp(self, tmp_path):
        src = tmp_path / "in.mp4"
        src.write_bytes(b"x")
        out = tmp_path / "out.mp4"
        done = threading.Event()
        errors = []

        def fake_run(cmd, duration_s, on_progress, cancel_event=None):
            Path(cmd[-1]).write_bytes(b"partial")
            raise ConversionCancelledError()

        loc = MagicMock()
        loc.ffmpeg_bin = "ffmpeg"
        with patch("app.services.ffmpeg_trim_service.locate_ffmpeg", return_value=loc), \
             patch.object(FfmpegConvertService, "_run_ffmpeg", staticmethod(fake_run)):
            cancel = trim_video(src, out, 0, 1000, on_error=lambda m: (errors.append(m), done.set()))
            cancel()  # cancel handle is callable
            self._wait(done)

        assert errors == ["Đã huỷ"]
        assert not out.exists()
        assert not out.with_suffix(".part.mp4").exists()

    def test_generic_error_calls_on_error(self, tmp_path):
        src = tmp_path / "in.mp4"
        src.write_bytes(b"x")
        out = tmp_path / "out.mp4"
        done = threading.Event()
        errors = []

        def fake_run(cmd, duration_s, on_progress, cancel_event=None):
            raise RuntimeError("boom")

        loc = MagicMock()
        loc.ffmpeg_bin = "ffmpeg"
        with patch("app.services.ffmpeg_trim_service.locate_ffmpeg", return_value=loc), \
             patch.object(FfmpegConvertService, "_run_ffmpeg", staticmethod(fake_run)):
            trim_video(src, out, 0, 1000, on_error=lambda m: (errors.append(m), done.set()))
            self._wait(done)

        assert errors == ["boom"]


# ---------------------------------------------------------------------------
# ffmpeg_trim_service: _build_cmd branches
# ---------------------------------------------------------------------------


def _cmd(**overrides) -> list:
    kw = dict(
        ffmpeg_bin="ffmpeg",
        source=Path("in.mp4"),
        output=Path("out.mp4"),
        start_s=0.0,
        end_s=10.0,
        rotate=None,
        mute=False,
        needs_reencode=True,
        speed=1.0,
        volume=1.0,
        text="",
        text_pos=2,
        text_size=24,
        text_color="white",
        text_box=False,
        text_shadow=False,
        brightness=0.0,
        contrast=1.0,
        saturation=1.0,
        hue=0.0,
        blur=0.0,
        fade_in_s=0.0,
        fade_out_s=0.0,
        duration_s=10.0,
    )
    kw.update(overrides)
    return _build_cmd(**kw)  # type: ignore[arg-type]  # dict[str,object] from mixed literal dict; keys match signature


class TestBuildCmdBranches:
    def test_rotate_1_transpose(self):
        cmd = " ".join(_cmd(rotate=1))
        assert "transpose=1" in cmd

    def test_rotate_2_transpose(self):
        cmd = " ".join(_cmd(rotate=2))
        assert "transpose=2" in cmd

    def test_rotate_3_flips(self):
        cmd = " ".join(_cmd(rotate=3))
        assert "hflip,vflip" in cmd

    def test_speed_sets_setpts_and_atempo(self):
        cmd = " ".join(_cmd(speed=1.5))
        assert "setpts=PTS/1.5" in cmd
        assert "atempo=1.5" in cmd

    def test_volume_filter(self):
        cmd = " ".join(_cmd(volume=0.5))
        assert "volume=0.5" in cmd

    def test_mute_drops_audio(self):
        cmd = _cmd(mute=True)
        assert "-an" in cmd
        assert "-c:a" not in cmd


# ---------------------------------------------------------------------------
# yt_dlp_engine: _extract_tiktok_live_hls_url "not currently live" fallbacks
# ---------------------------------------------------------------------------

_TLC = "utils.tiktok_live_checker"


class TestExtractHlsNotLiveFallbacks:
    URL = "https://www.tiktok.com/@someuser/live"

    def _engine(self, tmp_path):
        return YtDlpEngine(_make_config(tmp_path))

    def test_tt25_webcast_room_info_direct_hit(self, tmp_path):
        engine = self._engine(tmp_path)
        with patch.object(mod.yt_dlp, "YoutubeDL", _RaisingYDL), \
             patch.object(mod, "_TT_RL", MagicMock()), \
             patch(f"{_TLC}._fetch_hls_from_webcast_room_info", return_value=("https://hls/x.m3u8", "r1")), \
             patch(f"{_TLC}._fetch_hls_from_live_page", return_value=None), \
             patch(f"{_TLC}._verify_room_alive", return_value=False):
            result = engine._extract_tiktok_live_hls_url(self.URL, room_id="r1")

        assert result == ("https://hls/x.m3u8", "r1", "someuser", "")

    def test_tt26_live_page_scrape_without_room_id(self, tmp_path):
        engine = self._engine(tmp_path)
        with patch.object(mod.yt_dlp, "YoutubeDL", _RaisingYDL), \
             patch.object(mod, "_TT_RL", MagicMock()), \
             patch(f"{_TLC}._fetch_hls_from_webcast_room_info", return_value=None), \
             patch(f"{_TLC}._fetch_hls_from_live_page", return_value=("https://hls/y.m3u8", "r2")), \
             patch(f"{_TLC}._verify_room_alive", return_value=False):
            result = engine._extract_tiktok_live_hls_url(self.URL)

        assert result == ("https://hls/y.m3u8", "r2", "someuser", "")

    def test_tt29_retry_loop_room_confirmed_ended(self, tmp_path):
        engine = self._engine(tmp_path)
        with patch.object(mod.yt_dlp, "YoutubeDL", _RaisingYDL), \
             patch.object(mod, "_TT_RL", MagicMock()), \
             patch.object(mod.time, "sleep"), \
             patch(f"{_TLC}._fetch_hls_from_webcast_room_info", return_value=None), \
             patch(f"{_TLC}._fetch_hls_from_live_page", return_value=None), \
             patch(f"{_TLC}._verify_room_alive", return_value=False) as verify:
            result = engine._extract_tiktok_live_hls_url(self.URL, room_id="r3")

        assert result is None
        verify.assert_called_once()

    def test_tt29_room_still_alive_returns_none(self, tmp_path):
        engine = self._engine(tmp_path)
        with patch.object(mod.yt_dlp, "YoutubeDL", _RaisingYDL), \
             patch.object(mod, "_TT_RL", MagicMock()), \
             patch.object(mod.time, "sleep"), \
             patch(f"{_TLC}._fetch_hls_from_webcast_room_info", return_value=None), \
             patch(f"{_TLC}._fetch_hls_from_live_page", return_value=None), \
             patch(f"{_TLC}._verify_room_alive", return_value=True):
            result = engine._extract_tiktok_live_hls_url(self.URL, room_id="r4")

        assert result is None

    def test_no_username_falls_through_to_none(self, tmp_path):
        engine = self._engine(tmp_path)
        with patch.object(mod.yt_dlp, "YoutubeDL", _RaisingYDL), \
             patch.object(mod, "_TT_RL", MagicMock()):
            result = engine._extract_tiktok_live_hls_url("https://vt.tiktok.com/ZS9/")

        assert result is None


# ---------------------------------------------------------------------------
# yt_dlp_engine: _download_tiktok_live_direct
# ---------------------------------------------------------------------------


class TestDownloadTiktokLiveDirect:
    HLS = "https://pull-hls-f16.tiktokcdn.com/stream/index.m3u8"

    def _engine(self, tmp_path):
        return YtDlpEngine(_make_config(tmp_path))

    def test_ffmpeg_not_found_raises_runtime_error(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        with patch.object(mod, "get_ffmpeg_path", return_value=""), \
             patch("subprocess.Popen", side_effect=FileNotFoundError()):
            with pytest.raises(RuntimeError, match="FFmpeg"):
                engine._download_tiktok_live_direct(
                    self.HLS, str(tmp_path / "o.ts"), task, "", None
                )

    def _proc(self, returncode, stderr_lines=b""):
        import io

        proc = MagicMock()
        proc.poll.return_value = returncode
        proc.returncode = returncode
        proc.stderr = io.BytesIO(stderr_lines)
        return proc

    def test_clean_exit_marks_progress_done(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        progressed = []
        with patch.object(mod, "get_ffmpeg_path", return_value=""), \
             patch("subprocess.Popen", return_value=self._proc(0)):
            engine._download_tiktok_live_direct(
                self.HLS, str(tmp_path / "o.ts"), task, "", lambda t: progressed.append(t)
            )

        assert task.progress == 100.0
        assert progressed

    def test_nonzero_exit_raises_with_stderr_tail(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        with patch.object(mod, "get_ffmpeg_path", return_value=""), \
             patch("subprocess.Popen", return_value=self._proc(1, b"404 Not Found\n")):
            with pytest.raises(RuntimeError, match="exited with code 1"):
                engine._download_tiktok_live_direct(
                    self.HLS, str(tmp_path / "o.ts"), task, "", None
                )

    def test_cancel_kills_process_and_raises_download_error(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        task.cancel()
        proc = self._proc(None)
        with patch.object(mod, "get_ffmpeg_path", return_value=""), \
             patch("subprocess.Popen", return_value=proc):
            with pytest.raises(yt_dlp.utils.DownloadError, match="Cancelled"):
                engine._download_tiktok_live_direct(
                    self.HLS, str(tmp_path / "o.ts"), task, "", None
                )
        proc.kill.assert_called()

    def test_stall_watchdog_kills_after_no_data(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        proc = self._proc(None)
        with patch.object(mod, "get_ffmpeg_path", return_value=""), \
             patch.object(mod.time, "sleep"), \
             patch("subprocess.Popen", return_value=proc):
            with pytest.raises(RuntimeError, match="stall watchdog"):
                engine._download_tiktok_live_direct(
                    self.HLS, str(tmp_path / "o.ts"), task, "", None
                )
        proc.kill.assert_called()


# ---------------------------------------------------------------------------
# yt_dlp_engine: _download_tiktok_live_hls_curl
# ---------------------------------------------------------------------------


class TestDownloadTiktokLiveHlsCurl:
    HLS = "https://pull-hls-f16.tiktokcdn.com/stream/index.m3u8"

    def _engine(self, tmp_path, proxy=""):
        cfg = _make_config(tmp_path)
        cfg.proxy = proxy
        return YtDlpEngine(cfg)

    def _run(self, engine, tmp_path, task, session, on_progress=None):
        out = tmp_path / "o.ts"
        with patch(f"{_TLC}._get_impersonate_session", return_value=session), \
             patch(f"{_TLC}._load_cookie_jar", return_value=None):
            engine._download_tiktok_live_hls_curl(self.HLS, str(out), task, "", on_progress)
        return out

    def test_downloads_segments_until_endlist(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        playlist = "#EXTM3U\nseg1.ts\n#EXT-X-ENDLIST\n"

        def fake_get(url, **kw):
            if url == self.HLS:
                return _resp(200, text=playlist)
            return _resp(200, content=b"SEGDATA")

        session = MagicMock()
        session.get.side_effect = fake_get
        progressed = []

        out = self._run(engine, tmp_path, task, session, lambda t: progressed.append(t))

        assert out.read_bytes() == b"SEGDATA"
        assert task.progress == 100.0
        assert task.downloaded_bytes == len(b"SEGDATA")
        assert progressed

    def test_playlist_404_raises(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        session = MagicMock()
        session.get.return_value = _resp(404)
        with pytest.raises(RuntimeError, match="404 Not Found"):
            self._run(engine, tmp_path, task, session)

    def test_playlist_403_raises_token_expired(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        session = MagicMock()
        session.get.return_value = _resp(403)
        with pytest.raises(RuntimeError, match="token expired"):
            self._run(engine, tmp_path, task, session)

    def test_playlist_request_exception_raises(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        session = MagicMock()
        session.get.side_effect = OSError("conn reset")
        with pytest.raises(RuntimeError, match="HLS playlist request failed"):
            self._run(engine, tmp_path, task, session)

    def test_cancel_raises_download_error(self, tmp_path):
        engine = self._engine(tmp_path)
        task = _make_live_task()
        task.cancel()
        session = MagicMock()
        with pytest.raises(yt_dlp.utils.DownloadError, match="Cancelled"):
            self._run(engine, tmp_path, task, session)

    def test_non_200_playlist_polls_then_continues(self, tmp_path):
        engine = self._engine(tmp_path, proxy="http://127.0.0.1:8080")
        task = _make_live_task()
        session = MagicMock()
        session.get.side_effect = [
            _resp(500),
            _resp(200, text="#EXTM3U\n#EXT-X-ENDLIST\n"),
        ]
        with patch.object(mod.time, "sleep"):
            out = self._run(engine, tmp_path, task, session)
        assert out.exists()
        assert task.progress == 100.0
