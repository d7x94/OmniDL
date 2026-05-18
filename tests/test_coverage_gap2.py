"""
tests/test_coverage_gap2.py
Targeted coverage tests for lines missing from the 80% threshold.

Targets (by file):
  config_manager.py:370,378-379,384        api_enabled / api_host / api_port
  download_task.py:165-166                 wait_if_paused cancellation branch
  ffmpeg_convert_service.py:718,727-812    cancel-before-start + full MP3 path
  ffmpeg_convert_service.py:659-671        convert() thread dispatch
  ffmpeg_convert_service.py:691-704        _run() success/error/cancel paths
  ffmpeg_convert_service.py:928-931        probe validation failure
  ffmpeg_convert_service.py:941-957        remux to non-mp4 container
  download_service.py:113-119              _auto_convert_tiktok_live branches
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# config_manager.py  lines 370, 378-379, 384
# ---------------------------------------------------------------------------

class TestConfigManagerApiProperties:
    def _make(self, tmp_path, overrides):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps(overrides), encoding="utf-8")
        return ConfigManager(p)

    def test_api_enabled_true(self, tmp_path):
        cfg = self._make(tmp_path, {"api_enabled": True})
        assert cfg.api_enabled is True

    def test_api_host_custom(self, tmp_path):
        cfg = self._make(tmp_path, {"api_host": "192.168.1.10"})
        assert cfg.api_host == "192.168.1.10"

    def test_api_host_empty_falls_back(self, tmp_path):
        cfg = self._make(tmp_path, {"api_host": ""})
        assert cfg.api_host == "0.0.0.0"  # nosec B104

    def test_api_port_clamped(self, tmp_path):
        cfg = self._make(tmp_path, {"api_port": 9000})
        assert cfg.api_port == 9000

    def test_api_port_default(self, tmp_path):
        cfg = self._make(tmp_path, {})
        assert cfg.api_port == 7799


# ---------------------------------------------------------------------------
# download_task.py  lines 165-166
# ---------------------------------------------------------------------------

class TestDownloadTaskWaitIfPausedCancel:
    def test_returns_immediately_when_cancelled(self):
        from domain.models.download_task import DownloadTask

        task = DownloadTask(url="https://example.com", format_id="best", output_ext="mp4")
        # Simulate paused state: wait() returns False (timed out, event not set)
        task._pause_event = MagicMock()
        task._pause_event.wait.return_value = False
        task.cancel()

        task.wait_if_paused()  # must return, not loop forever
        assert task.is_cancellation_requested


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_ffmpeg(tmp_path):
    p = tmp_path / "ffmpeg"
    p.write_bytes(b"")
    return p


def _silent_proc():
    proc = MagicMock()
    proc.stderr = iter([])
    proc.wait.return_value = None
    proc.returncode = 0
    return proc


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py  line 718  (cancel before start)
# ---------------------------------------------------------------------------

class TestConvertSyncCancelBeforeStart:
    def test_cancel_event_set_raises(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import (
            ConversionCancelledError,
            FfmpegConvertService,
        )

        src = tmp_path / "video.mp4"
        src.write_bytes(b"fake")

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))

        cancel = threading.Event()
        cancel.set()

        svc = FfmpegConvertService()
        with pytest.raises(ConversionCancelledError):
            svc._convert_sync(src, "standard", tmp_path, None, cancel_event=cancel)


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py  lines 727-812  (full MP3 extraction path)
# ---------------------------------------------------------------------------

class TestConvertSyncMp3:
    def test_mp3_extraction_happy_path(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "audio.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")

        proc = _silent_proc()

        def fake_popen(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"mp3data")
            return proc

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))
        monkeypatch.setattr(FfmpegConvertService, "_probe_duration",
                            staticmethod(lambda *a: 0.0))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        svc = FfmpegConvertService()
        result = svc._convert_sync(src, "standard", tmp_path, None, target_ext="mp3")
        assert result.suffix == ".mp3"
        assert result.exists()

    def test_mp3_cancelled_before_start(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import (
            ConversionCancelledError,
            FfmpegConvertService,
        )

        src = tmp_path / "audio.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))

        cancel = threading.Event()
        cancel.set()

        svc = FfmpegConvertService()
        with pytest.raises(ConversionCancelledError):
            svc._convert_sync(src, "standard", tmp_path, None,
                              cancel_event=cancel, target_ext="mp3")

    def test_mp3_with_progress_callback(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "audio.mp4"
        src.write_bytes(b"fake")

        proc = MagicMock()
        proc.stderr = iter([b"out_time_ms=5000000\n"])
        proc.wait.return_value = None
        proc.returncode = 0

        def fake_popen(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"mp3data")
            return proc

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))
        monkeypatch.setattr(FfmpegConvertService, "_probe_duration",
                            staticmethod(lambda *a: 10.0))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        progresses = []
        svc = FfmpegConvertService()
        svc._convert_sync(src, "standard", tmp_path, progresses.append, target_ext="mp3")

    def test_mp3_existing_output_increments_filename(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "song.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")
        (tmp_path / "song.mp3").write_bytes(b"existing")

        proc = _silent_proc()

        def fake_popen(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"mp3data")
            return proc

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))
        monkeypatch.setattr(FfmpegConvertService, "_probe_duration",
                            staticmethod(lambda *a: 0.0))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        svc = FfmpegConvertService()
        result = svc._convert_sync(src, "standard", tmp_path, None, target_ext="mp3")
        assert result.name == "song_1.mp3"


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py  lines 659-671, 691-704  (convert() + _run())
# ---------------------------------------------------------------------------

class TestFfmpegConvertPublicApi:
    def test_run_success_calls_on_done(self, tmp_path):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "v.mp4"
        src.write_bytes(b"fake")
        out = tmp_path / "v_iPhone.mp4"
        out.write_bytes(b"result")

        svc = FfmpegConvertService()
        on_done = MagicMock()
        on_error = MagicMock()

        with patch.object(svc, "_convert_sync", return_value=out):
            svc._run(src, "standard", tmp_path, None, on_done, on_error)

        on_done.assert_called_once_with(out)
        on_error.assert_not_called()

    def test_run_conversion_error_calls_on_error(self, tmp_path):
        from app.services.ffmpeg_convert_service import (
            ConversionError,
            FfmpegConvertService,
        )

        src = tmp_path / "v.mp4"
        src.write_bytes(b"fake")
        svc = FfmpegConvertService()
        on_error = MagicMock()

        with patch.object(svc, "_convert_sync", side_effect=ConversionError("bad")):
            svc._run(src, "standard", tmp_path, None, None, on_error)

        on_error.assert_called_once_with("bad")

    def test_run_cancelled_calls_on_error_with_cancel_msg(self, tmp_path):
        from app.services.ffmpeg_convert_service import (
            ConversionCancelledError,
            FfmpegConvertService,
        )

        src = tmp_path / "v.mp4"
        src.write_bytes(b"fake")
        svc = FfmpegConvertService()
        on_error = MagicMock()

        with patch.object(svc, "_convert_sync", side_effect=ConversionCancelledError("x")):
            svc._run(src, "standard", tmp_path, None, None, on_error)

        on_error.assert_called_once_with("Đã huỷ")

    def test_convert_dispatches_background_thread(self, tmp_path):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "v.mp4"
        src.write_bytes(b"fake")

        done_event = threading.Event()
        result_holder = []

        def on_done(path):
            result_holder.append(path)
            done_event.set()

        svc = FfmpegConvertService()
        out = tmp_path / "v_iPhone.mp4"
        out.write_bytes(b"result")

        with patch.object(svc, "_convert_sync", return_value=out):
            svc.convert(src, "standard", output_dir=tmp_path, on_done=on_done)

        done_event.wait(timeout=5)
        assert result_holder == [out]


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py  lines 928-931  (probe validation failure)
# ---------------------------------------------------------------------------

class TestFreshEncodeProbeValidation:
    def test_no_video_frames_raises_conversion_error(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import (
            ConversionError,
            FfmpegConvertService,
            FfmpegMediaInfo,
        )

        src = tmp_path / "video.flv"
        src.write_bytes(b"FLV\x01")

        proc = _silent_proc()

        def fake_popen(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"encoded")
            return proc

        src_info = FfmpegMediaInfo(video_codec="h264")
        # out_info has no video frames
        out_info = FfmpegMediaInfo(video_codec="", video_nb_frames=0)

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))
        monkeypatch.setattr(FfmpegConvertService, "_probe_duration",
                            staticmethod(lambda *a: 0.0))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(
            "app.services.ffmpeg_convert_service.probe_media_info",
            lambda path: src_info if path == src else out_info,
        )

        svc = FfmpegConvertService()
        with pytest.raises(ConversionError, match="no video frames"):
            svc._convert_sync(src, "standard", tmp_path, None)


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py  lines 941-957  (remux to non-mp4 container)
# ---------------------------------------------------------------------------

class TestConvertSyncRemux:
    def test_remux_to_mkv_returns_mkv_file(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "video.ts"
        src.write_bytes(b"fake ts")

        proc = _silent_proc()

        def fake_popen(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"encoded mp4 data")
            return proc

        run_result = MagicMock()
        run_result.returncode = 0

        def fake_run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"x" * 2000)
            return run_result

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))
        monkeypatch.setattr(FfmpegConvertService, "_probe_duration",
                            staticmethod(lambda *a: 0.0))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            "app.services.ffmpeg_convert_service.probe_media_info",
            lambda path: None,
        )

        progresses = []
        svc = FfmpegConvertService()
        result = svc._convert_sync(src, "standard", tmp_path, progresses.append,
                                   target_ext="mkv")
        assert result.suffix == ".mkv"
        assert 100.0 in progresses

    def test_remux_failure_raises_conversion_error(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import (
            ConversionError,
            FfmpegConvertService,
        )

        src = tmp_path / "video.ts"
        src.write_bytes(b"fake ts")

        proc = _silent_proc()

        def fake_popen(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"encoded")
            return proc

        run_result = MagicMock()
        run_result.returncode = 1
        run_result.stderr = b"remux error"

        monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin",
                            staticmethod(lambda: fake_ffmpeg))
        monkeypatch.setattr(FfmpegConvertService, "_probe_duration",
                            staticmethod(lambda *a: 0.0))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: run_result)
        monkeypatch.setattr(
            "app.services.ffmpeg_convert_service.probe_media_info",
            lambda path: None,
        )

        svc = FfmpegConvertService()
        with pytest.raises(ConversionError, match="Remux"):
            svc._convert_sync(src, "standard", tmp_path, None, target_ext="avi")


# ---------------------------------------------------------------------------
# download_service.py  lines 113-119  (_auto_convert_tiktok_live branches)
# ---------------------------------------------------------------------------

class TestAutoConvertTiktokLive:
    def _make_service(self, tmp_path, api_enabled=False):
        from app.event_bus import EventBus
        from app.services.download_service import DownloadService

        config = MagicMock()
        config.download_dir = tmp_path
        config.api_enabled = api_enabled

        return DownloadService(
            config=config,
            download_manager=MagicMock(),
            history_repo=MagicMock(),
            engine=MagicMock(),
            event_bus=EventBus(),
        )

    def _make_tiktok_live_task(self, tmp_path, filename="live.ts"):
        from domain.models.download_task import DownloadTask, MediaInfo

        task = DownloadTask(
            url="https://www.tiktok.com/@user/live",
            format_id="best",
            output_ext="ts",
        )
        task.media_info = MediaInfo(
            url="https://www.tiktok.com/@user/live",
            title="Live",
            is_live=True,
        )
        task.filename = str(tmp_path / filename)
        return task

    def test_api_enabled_returns_early(self, tmp_path):
        svc = self._make_service(tmp_path, api_enabled=True)
        task = self._make_tiktok_live_task(tmp_path)

        queue_mock = MagicMock()
        svc._auto_convert_queue = queue_mock

        svc._auto_convert_tiktok_live(task)
        queue_mock.submit.assert_not_called()

    def test_file_not_exists_returns_early(self, tmp_path):
        svc = self._make_service(tmp_path, api_enabled=False)
        task = self._make_tiktok_live_task(tmp_path, filename="nonexistent.ts")

        queue_mock = MagicMock()
        svc._auto_convert_queue = queue_mock

        svc._auto_convert_tiktok_live(task)
        queue_mock.submit.assert_not_called()

    def test_mp4_file_returns_early(self, tmp_path):
        svc = self._make_service(tmp_path, api_enabled=False)
        task = self._make_tiktok_live_task(tmp_path, filename="live.mp4")
        (tmp_path / "live.mp4").write_bytes(b"data")

        queue_mock = MagicMock()
        svc._auto_convert_queue = queue_mock

        svc._auto_convert_tiktok_live(task)
        queue_mock.submit.assert_not_called()

    def test_ts_file_submits_conversion(self, tmp_path):
        svc = self._make_service(tmp_path, api_enabled=False)
        task = self._make_tiktok_live_task(tmp_path, filename="live.ts")
        (tmp_path / "live.ts").write_bytes(b"data")

        queue_mock = MagicMock()
        svc._auto_convert_queue = queue_mock

        svc._auto_convert_tiktok_live(task)
        queue_mock.submit.assert_called_once()


# ---------------------------------------------------------------------------
# ffmpeg_convert_service.py  lines 124-125, 132-133  (_ff_float / _ff_int)
# ---------------------------------------------------------------------------

class TestFfHelpers:
    def test_ff_float_returns_default_on_invalid(self):
        from app.services.ffmpeg_convert_service import _ff_float
        assert _ff_float("N/A") == 0.0
        assert _ff_float("not_a_number", 9.9) == 9.9

    def test_ff_int_returns_default_on_invalid(self):
        from app.services.ffmpeg_convert_service import _ff_int
        assert _ff_int("N/A") == 0
        assert _ff_int("not_a_number", 42) == 42


# ---------------------------------------------------------------------------
# download_service.py  lines 159-172  (Instagram live short-circuit)
# ---------------------------------------------------------------------------

class TestAnalyseUrlInstagramLive:
    def test_instagram_live_url_returns_synthetic_media_info(self, tmp_path):
        import time
        from app.event_bus import EventBus
        from app.services.download_service import DownloadService
        from unittest.mock import patch

        config = MagicMock()
        config.download_dir = tmp_path
        bus = EventBus()

        svc = DownloadService(
            config=config,
            download_manager=MagicMock(),
            history_repo=MagicMock(),
            engine=MagicMock(),
            event_bus=bus,
        )

        url = "https://www.instagram.com/testuser/live/"
        on_done = MagicMock()
        on_error = MagicMock()

        with patch(
            "infrastructure.downloader.instagram_live_engine.is_instagram_live_url",
            return_value=True,
        ):
            svc.analyse_url(url, on_done=on_done, on_error=on_error)
            deadline = time.time() + 5
            while time.time() < deadline:
                if on_done.called or on_error.called:
                    break
                time.sleep(0.02)

        on_done.assert_called_once()
        info = on_done.call_args[0][0]
        assert info.is_live is True
        assert info.platform == "instagram"
        on_error.assert_not_called()
