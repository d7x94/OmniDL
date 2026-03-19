"""
tests/test_ffmpeg_convert_service.py
Unit tests for the improved FFmpeg convert service.

Covers:
  ConvertQueue:
    - Respects max_concurrent concurrency limit
    - on_start fires when slot is acquired (not on submit)
    - on_done / on_error fire correctly
    - max_concurrent=1 rejects invalid values

  FfmpegConvertService internals:
    - _find_output_path deduplication
    - _parse_duration / _probe_duration
    - _build_cmd includes -progress pipe:1 and -nostats flags
    - Resume detection: _convert_sync skips resume when partial < 10 s
    - _validate_output raises on missing / empty file

  probe_media_info:
    - Returns None when ffprobe unavailable
    - Parses JSON correctly

  scan_folder_for_media:
    - Returns only supported extensions
    - Recursively finds nested files
    - Handles empty / nonexistent folders gracefully
    - Respects SUPPORTED_EXTS

  SUPPORTED_EXTS:
    - Contains expected extensions
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.ffmpeg_convert_service import (
    ConversionError,
    ConvertQueue,
    FfmpegConvertService,
    FfmpegMediaInfo,
    SUPPORTED_EXTS,
    probe_media_info,
    scan_folder_for_media,
)


# ─────────────────────────────────────────────────────────────────────────────
# SUPPORTED_EXTS
# ─────────────────────────────────────────────────────────────────────────────

class TestSupportedExts:
    def test_contains_common_video_formats(self):
        assert "mp4" in SUPPORTED_EXTS
        assert "mkv" in SUPPORTED_EXTS
        assert "webm" in SUPPORTED_EXTS
        assert "avi" in SUPPORTED_EXTS
        assert "mov" in SUPPORTED_EXTS

    def test_no_leading_dots(self):
        for ext in SUPPORTED_EXTS:
            assert not ext.startswith("."), f"Extension {ext!r} must not start with '.'"

    def test_lowercase_only(self):
        for ext in SUPPORTED_EXTS:
            assert ext == ext.lower(), f"Extension {ext!r} must be lowercase"


# ─────────────────────────────────────────────────────────────────────────────
# scan_folder_for_media
# ─────────────────────────────────────────────────────────────────────────────

class TestScanFolderForMedia:
    def test_finds_supported_files(self, tmp_path: Path):
        (tmp_path / "a.mp4").touch()
        (tmp_path / "b.mkv").touch()
        (tmp_path / "c.txt").touch()  # unsupported

        result = scan_folder_for_media(tmp_path)
        names = {p.name for p in result}
        assert "a.mp4" in names
        assert "b.mkv" in names
        assert "c.txt" not in names

    def test_recursive(self, tmp_path: Path):
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.avi").touch()
        (tmp_path / "top.mp4").touch()

        result = scan_folder_for_media(tmp_path)
        names = {p.name for p in result}
        assert "nested.avi" in names
        assert "top.mp4" in names

    def test_empty_folder_returns_empty_list(self, tmp_path: Path):
        result = scan_folder_for_media(tmp_path)
        assert result == []

    def test_nonexistent_folder_returns_empty_list(self, tmp_path: Path):
        # scan_folder_for_media must not raise even for a missing path
        missing = tmp_path / "does_not_exist"
        result = scan_folder_for_media(missing)
        assert result == []

    def test_case_insensitive_extension(self, tmp_path: Path):
        (tmp_path / "video.MP4").touch()
        (tmp_path / "clip.MKV").touch()
        result = scan_folder_for_media(tmp_path)
        assert len(result) == 2

    def test_returns_sorted_list(self, tmp_path: Path):
        (tmp_path / "z.mp4").touch()
        (tmp_path / "a.mp4").touch()
        (tmp_path / "m.mp4").touch()
        result = scan_folder_for_media(tmp_path)
        names = [p.name for p in result]
        assert names == sorted(names)


# ─────────────────────────────────────────────────────────────────────────────
# probe_media_info
# ─────────────────────────────────────────────────────────────────────────────

class TestProbeMediaInfo:
    def test_returns_none_when_ffprobe_not_found(self, tmp_path: Path):
        src = tmp_path / "video.mp4"
        src.touch()
        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg", return_value=None):
            result = probe_media_info(src)
        assert result is None

    def test_returns_none_when_ffprobe_sentinel(self, tmp_path: Path):
        src = tmp_path / "video.mp4"
        src.touch()
        loc = MagicMock()
        loc.ffprobe_bin = "<not found>"
        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg", return_value=loc):
            result = probe_media_info(src)
        assert result is None

    def test_parses_json_output(self, tmp_path: Path):
        src = tmp_path / "video.mp4"
        src.touch()

        fake_json = json.dumps({
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                },
            ],
            "format": {
                "duration": "3723.45",
                "bit_rate": "8000000",
            },
        })

        loc = MagicMock()
        loc.ffprobe_bin = "/usr/bin/ffprobe"

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_json

        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg", return_value=loc):
            with patch("subprocess.run", return_value=fake_result):
                info = probe_media_info(src)

        assert info is not None
        assert info.video_codec == "h264"
        assert info.audio_codec == "aac"
        assert info.width == 1920
        assert info.height == 1080
        assert abs(info.duration_s - 3723.45) < 0.01
        assert info.bitrate_bps == 8_000_000

    def test_returns_none_on_nonzero_exit(self, tmp_path: Path):
        src = tmp_path / "video.mp4"
        src.touch()
        loc = MagicMock()
        loc.ffprobe_bin = "/usr/bin/ffprobe"
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg", return_value=loc):
            with patch("subprocess.run", return_value=fake_result):
                result = probe_media_info(src)
        assert result is None

    def test_returns_none_on_exception(self, tmp_path: Path):
        src = tmp_path / "video.mp4"
        src.touch()
        loc = MagicMock()
        loc.ffprobe_bin = "/usr/bin/ffprobe"
        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg", return_value=loc):
            with patch("subprocess.run", side_effect=Exception("timeout")):
                result = probe_media_info(src)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# FfmpegConvertService internals
# ─────────────────────────────────────────────────────────────────────────────

class TestFindOutputPath:
    def test_returns_base_path_when_no_collision(self, tmp_path: Path):
        source = tmp_path / "video.mkv"
        result = FfmpegConvertService._find_output_path(tmp_path, source)
        assert result == tmp_path / "video_iPhone.mp4"

    def test_increments_on_collision(self, tmp_path: Path):
        source = tmp_path / "video.mkv"
        (tmp_path / "video_iPhone.mp4").touch()
        result = FfmpegConvertService._find_output_path(tmp_path, source)
        assert result == tmp_path / "video_iPhone_2.mp4"

    def test_increments_past_multiple_collisions(self, tmp_path: Path):
        source = tmp_path / "clip.mp4"
        (tmp_path / "clip_iPhone.mp4").touch()
        (tmp_path / "clip_iPhone_2.mp4").touch()
        result = FfmpegConvertService._find_output_path(tmp_path, source)
        assert result == tmp_path / "clip_iPhone_3.mp4"


class TestValidateOutput:
    def test_raises_when_missing(self, tmp_path: Path):
        missing = tmp_path / "out.mp4"
        with pytest.raises(ConversionError):
            FfmpegConvertService._validate_output(missing)

    def test_raises_when_too_small(self, tmp_path: Path):
        tiny = tmp_path / "out.mp4"
        tiny.write_bytes(b"x" * 100)
        with pytest.raises(ConversionError):
            FfmpegConvertService._validate_output(tiny)

    def test_passes_when_file_large_enough(self, tmp_path: Path):
        ok = tmp_path / "out.mp4"
        ok.write_bytes(b"x" * 2000)
        FfmpegConvertService._validate_output(ok)  # must not raise


class TestBuildCmd:
    """_build_cmd must include -progress pipe:1 and -nostats for the progress API."""

    def test_contains_progress_api_flags(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        source = tmp_path / "in.mkv"
        output = tmp_path / "out.mp4"
        from app.services.ffmpeg_convert_service import _PRESETS
        preset = _PRESETS["standard"]
        cmd = FfmpegConvertService._build_cmd(ffmpeg, source, output, preset)
        assert "-progress" in cmd
        assert "pipe:1" in cmd
        assert "-nostats" in cmd

    def test_seek_added_when_nonzero(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        source = tmp_path / "in.mkv"
        output = tmp_path / "out.mp4"
        from app.services.ffmpeg_convert_service import _PRESETS
        preset = _PRESETS["standard"]
        cmd = FfmpegConvertService._build_cmd(ffmpeg, source, output, preset, seek=42.5)
        assert "-ss" in cmd
        idx = cmd.index("-ss")
        assert float(cmd[idx + 1]) == pytest.approx(42.5)

    def test_no_seek_when_zero(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        source = tmp_path / "in.mkv"
        output = tmp_path / "out.mp4"
        from app.services.ffmpeg_convert_service import _PRESETS
        preset = _PRESETS["standard"]
        cmd = FfmpegConvertService._build_cmd(ffmpeg, source, output, preset, seek=0.0)
        assert "-ss" not in cmd


class TestConvertSync:
    """High-level smoke test: _convert_sync raises ConversionError for missing source."""

    def test_raises_for_missing_source(self, tmp_path: Path):
        svc = FfmpegConvertService()
        missing = tmp_path / "does_not_exist.mkv"
        with pytest.raises(ConversionError, match="không tồn tại"):
            svc._convert_sync(missing, "standard", None, None)

    def test_resume_skipped_when_no_partial(self, tmp_path: Path):
        """When no .part file exists, _convert_sync calls _fresh_encode.
        _resume_encode was removed (BUG A — timestamp discontinuity, disabled by design).
        """
        source = tmp_path / "video.mkv"
        source.write_bytes(b"x" * 100)

        svc = FfmpegConvertService()
        fresh_calls: list = []

        with patch.object(svc, "_probe_duration", return_value=120.0):
            with patch.object(svc, "_fresh_encode", side_effect=lambda *a, **kw: fresh_calls.append(1) or tmp_path / "out.mp4"):
                try:
                    svc._convert_sync(source, "standard", tmp_path, None)
                except Exception:
                    pass

        assert len(fresh_calls) == 1

    def test_resume_triggered_when_large_partial_exists(self, tmp_path: Path):
        """When a .part file exists, it must be deleted and _fresh_encode called.
        Resume encoding is intentionally disabled (BUG A — timestamp discontinuity).
        _resume_encode has been removed from the codebase entirely.
        """
        source = tmp_path / "video.mkv"
        source.write_bytes(b"x" * 100)
        part = tmp_path / "video_iPhone.part.mp4"
        part.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

        svc = FfmpegConvertService()
        fresh_calls: list = []

        def fake_probe(ffmpeg_bin: Path, src: Path) -> float:
            return 30.0 if src == part else 120.0

        with patch.object(svc, "_probe_duration", side_effect=fake_probe):
            with patch.object(svc, "_fresh_encode",
                              side_effect=lambda *a, **kw: fresh_calls.append(1) or (tmp_path / "out.mp4")):
                with patch.object(svc.__class__, "_locate_ffmpeg_bin",
                                  staticmethod(lambda: tmp_path / "ffmpeg")):
                    try:
                        svc._convert_sync(source, "standard", tmp_path, None)
                    except Exception:
                        pass

        # .part file must have been deleted before encoding
        assert not part.exists(), ".part file must be deleted before conversion restart"
        # Fresh encode must be called — resume is permanently disabled
        assert len(fresh_calls) == 1, "Expected _fresh_encode to be called once"


# ─────────────────────────────────────────────────────────────────────────────
# ConvertQueue
# ─────────────────────────────────────────────────────────────────────────────

class TestConvertQueue:
    def test_invalid_max_concurrent_raises(self):
        with pytest.raises(ValueError):
            ConvertQueue(max_concurrent=0)

    def test_on_start_fires_before_run(self, tmp_path: Path):
        """on_start must be called once the semaphore slot is acquired."""
        source = tmp_path / "video.mkv"
        source.touch()

        started = threading.Event()
        done = threading.Event()

        queue = ConvertQueue(max_concurrent=1)

        with patch.object(queue._svc, "_run") as mock_run:
            mock_run.side_effect = lambda *a, **kw: done.set()
            queue.submit(
                source=source,
                on_start=lambda: started.set(),
            )
            started.wait(timeout=3)
            done.wait(timeout=3)

        assert started.is_set(), "on_start was never called"

    def test_concurrency_limited_to_max(self, tmp_path: Path):
        """At most max_concurrent jobs run simultaneously."""
        sources = [tmp_path / f"vid{i}.mkv" for i in range(4)]
        for s in sources:
            s.touch()

        max_concurrent = 2
        queue = ConvertQueue(max_concurrent=max_concurrent)

        active: list[int] = []
        max_seen: list[int] = []
        lock = threading.Lock()
        all_done = threading.Event()
        job_count = len(sources)
        finished = [0]

        def make_run(delay: float = 0.05):
            def _run(source, quality, output_dir, on_progress, on_done, on_error):
                with lock:
                    active.append(1)
                    max_seen.append(len(active))
                time.sleep(delay)
                with lock:
                    active.pop()
                    finished[0] += 1
                    if finished[0] == job_count:
                        all_done.set()
                if on_done:
                    on_done(source)
            return _run

        with patch.object(queue._svc, "_run", side_effect=make_run()):
            for src in sources:
                queue.submit(source=src)

            all_done.wait(timeout=10)

        assert max(max_seen) <= max_concurrent, (
            f"Concurrency exceeded: saw {max(max_seen)} simultaneous, limit={max_concurrent}"
        )

    def test_on_done_fires_after_completion(self, tmp_path: Path):
        source = tmp_path / "video.mkv"
        source.touch()
        done_paths: list[Path] = []
        done_event = threading.Event()
        queue = ConvertQueue(max_concurrent=1)

        def fake_run(src, quality, output_dir, on_progress, on_done, on_error):
            if on_done:
                on_done(src)

        with patch.object(queue._svc, "_run", side_effect=fake_run):
            queue.submit(
                source=source,
                on_done=lambda p: (done_paths.append(p), done_event.set()),
            )
            done_event.wait(timeout=3)

        assert len(done_paths) == 1
        assert done_paths[0] == source

    def test_on_error_fires_on_exception(self, tmp_path: Path):
        source = tmp_path / "video.mkv"
        source.touch()
        errors: list[str] = []
        err_event = threading.Event()
        queue = ConvertQueue(max_concurrent=1)

        def fake_run(src, quality, output_dir, on_progress, on_done, on_error):
            if on_error:
                on_error("boom")

        with patch.object(queue._svc, "_run", side_effect=fake_run):
            queue.submit(
                source=source,
                on_error=lambda msg: (errors.append(msg), err_event.set()),
            )
            err_event.wait(timeout=3)

        assert errors == ["boom"]

    def test_semaphore_released_after_error(self, tmp_path: Path):
        """Semaphore must be released even when a job raises, so next job can proceed."""
        sources = [tmp_path / f"v{i}.mkv" for i in range(2)]
        for s in sources:
            s.touch()

        queue = ConvertQueue(max_concurrent=1)
        second_started = threading.Event()
        call_order: list[int] = []

        def make_run(idx: int, fail: bool):
            def _run(src, quality, output_dir, on_progress, on_done, on_error):
                call_order.append(idx)
                if idx == 1:
                    second_started.set()
                if fail and on_error:
                    on_error("error")
                elif on_done:
                    on_done(src)
            return _run

        # Patch _run differently for each call
        run_count = [0]
        def dispatch(src, quality, output_dir, on_progress, on_done, on_error):
            i = run_count[0]
            run_count[0] += 1
            fail = (i == 0)
            idx = i
            call_order.append(idx)
            if fail and on_error:
                on_error("first job failed")
            elif on_done:
                on_done(src)
            if idx == 1:
                second_started.set()

        with patch.object(queue._svc, "_run", side_effect=dispatch):
            for src in sources:
                queue.submit(source=src)

            second_started.wait(timeout=5)

        assert second_started.is_set(), "Second job never started — semaphore may have leaked"


# ─────────────────────────────────────────────────────────────────────────────
# NEW: EncodeSettings dataclass
# ─────────────────────────────────────────────────────────────────────────────

from app.services.ffmpeg_convert_service import (
    EncodeSettings,
    _HW_ENCODER_CATALOG,
    ENCODER_OPTIONS,
    SPEED_OPTIONS,
    _validate_encoder_codec,
    detect_available_encoders,
)


class TestEncodeSettings:
    def test_default_is_cpu_standard(self):
        s = EncodeSettings()
        assert s.encoder_key == "cpu"
        assert s.quality == "standard"
        assert s.speed_preset == "balanced"
        assert s.custom_quality == 23

    def test_custom_fields(self):
        s = EncodeSettings(quality="custom", custom_quality=20,
                           encoder_key="nvenc", speed_preset="fast")
        assert s.quality == "custom"
        assert s.custom_quality == 20
        assert s.encoder_key == "nvenc"
        assert s.speed_preset == "fast"

    def test_encoder_options_contains_cpu(self):
        keys = [k for k, _ in ENCODER_OPTIONS]
        assert "cpu" in keys

    def test_encoder_options_contains_known_gpus(self):
        keys = [k for k, _ in ENCODER_OPTIONS]
        assert "nvenc" in keys
        assert "qsv" in keys
        assert "amf" in keys
        assert "videotoolbox" in keys

    def test_speed_options_has_three_tiers(self):
        assert len(SPEED_OPTIONS) == 3
        keys = [k for k, _ in SPEED_OPTIONS]
        assert "quality" in keys
        assert "balanced" in keys
        assert "fast" in keys


# ─────────────────────────────────────────────────────────────────────────────
# NEW: _HW_ENCODER_CATALOG structure
# ─────────────────────────────────────────────────────────────────────────────

class TestHwEncoderCatalog:
    def test_all_known_encoders_present(self):
        expected = {"nvenc", "qsv", "amf", "videotoolbox"}
        assert expected == set(_HW_ENCODER_CATALOG.keys())

    def test_nvenc_uses_cq_flag(self):
        assert _HW_ENCODER_CATALOG["nvenc"].quality_flag == "-cq"
        assert _HW_ENCODER_CATALOG["nvenc"].ffmpeg_codec == "h264_nvenc"

    def test_qsv_uses_global_quality_flag(self):
        assert _HW_ENCODER_CATALOG["qsv"].quality_flag == "-global_quality"
        assert _HW_ENCODER_CATALOG["qsv"].ffmpeg_codec == "h264_qsv"

    def test_amf_uses_qp_flag(self):
        assert _HW_ENCODER_CATALOG["amf"].quality_flag == "-qp"
        assert _HW_ENCODER_CATALOG["amf"].ffmpeg_codec == "h264_amf"

    def test_videotoolbox_no_profile_level(self):
        assert not _HW_ENCODER_CATALOG["videotoolbox"].supports_profile_level

    def test_nvenc_speed_map(self):
        speed = _HW_ENCODER_CATALOG["nvenc"].speed_map
        assert speed["quality"] == "p7"
        assert speed["balanced"] == "p5"
        assert speed["fast"] == "p3"

    def test_quality_values_present_for_all_encoders(self):
        for key, spec in _HW_ENCODER_CATALOG.items():
            for tier in ("high", "standard", "small"):
                assert tier in spec.quality_values, (
                    f"Encoder {key!r} missing quality_values[{tier!r}]"
                )

    def test_quality_values_are_numeric_strings(self):
        for key, spec in _HW_ENCODER_CATALOG.items():
            for tier, val in spec.quality_values.items():
                assert val.isdigit(), (
                    f"Encoder {key!r} quality_values[{tier!r}]={val!r} "
                    "must be a numeric string"
                )


# ─────────────────────────────────────────────────────────────────────────────
# NEW: detect_available_encoders
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectAvailableEncoders:
    def test_always_includes_cpu(self, tmp_path: Path):
        """CPU must always be in the result even when ffmpeg is not found."""
        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg", return_value=None):
            result = detect_available_encoders()
        assert "cpu" in result

    def test_detects_nvenc_when_present_in_output(self, tmp_path: Path):
        fake_output = (
            "Encoders:\n"
            " V..... h264_nvenc           NVIDIA NVENC H.264 encoder\n"
            " V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC\n"
        )
        ffmpeg = tmp_path / "ffmpeg"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_output
        fake_result.stderr = ""
        with patch("subprocess.run", return_value=fake_result):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)
        assert "nvenc" in result
        assert "cpu" in result

    def test_does_not_detect_absent_encoder(self, tmp_path: Path):
        fake_output = (
            "Encoders:\n"
            " V..... libx264              libx264 H.264\n"
        )
        ffmpeg = tmp_path / "ffmpeg"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_output
        fake_result.stderr = ""
        with patch("subprocess.run", return_value=fake_result):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)
        assert "nvenc" not in result
        assert "qsv" not in result
        assert "amf" not in result

    def test_detects_multiple_gpus(self, tmp_path: Path):
        fake_output = (
            " V..... h264_nvenc    NVIDIA\n"
            " V..... h264_qsv      Intel\n"
            " V..... h264_amf      AMD\n"
        )
        ffmpeg = tmp_path / "ffmpeg"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_output
        fake_result.stderr = ""
        with patch("subprocess.run", return_value=fake_result):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)
        assert "nvenc" in result
        assert "qsv" in result
        assert "amf" in result

    def test_returns_cpu_only_on_subprocess_exception(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        with patch("subprocess.run", side_effect=Exception("timeout")):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)
        assert result == {"cpu"}

    def test_videotoolbox_detected_by_codec_name(self, tmp_path: Path):
        fake_output = " V..... h264_videotoolbox   VideoToolbox H.264\n"
        ffmpeg = tmp_path / "ffmpeg"
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = fake_output
        fake_result.stderr = ""
        with patch("subprocess.run", return_value=fake_result):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)
        assert "videotoolbox" in result


# ─────────────────────────────────────────────────────────────────────────────
# NEW: _build_cmd with EncodeSettings (GPU path)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCmdGpu:
    """GPU encoder paths in _build_cmd must use the correct codec and flags."""

    def _cmd(
        self,
        tmp_path: Path,
        encoder_key: str,
        quality: str = "standard",
        custom_quality: int = 23,
        speed_preset: str = "balanced",
    ) -> list[str]:
        from app.services.ffmpeg_convert_service import _PRESETS
        settings = EncodeSettings(
            quality=quality,
            custom_quality=custom_quality,
            encoder_key=encoder_key,
            speed_preset=speed_preset,
        )
        preset = _PRESETS.get(quality, _PRESETS["standard"])
        return FfmpegConvertService._build_cmd(
            tmp_path / "ffmpeg",
            tmp_path / "in.mkv",
            tmp_path / "out.mp4",
            preset,
            encode_settings=settings,
        )

    # ── NVENC ─────────────────────────────────────────────────────────────

    def test_nvenc_uses_h264_nvenc_codec(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc")
        assert "h264_nvenc" in cmd

    def test_nvenc_does_not_use_libx264(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc")
        assert "libx264" not in cmd

    def test_nvenc_uses_cq_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", quality="standard")
        assert "-cq" in cmd
        assert "-crf" not in cmd

    def test_nvenc_standard_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", quality="standard")
        idx = cmd.index("-cq")
        assert cmd[idx + 1] == "23"

    def test_nvenc_high_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", quality="high")
        idx = cmd.index("-cq")
        assert cmd[idx + 1] == "19"

    def test_nvenc_small_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", quality="small")
        idx = cmd.index("-cq")
        assert cmd[idx + 1] == "28"

    def test_nvenc_custom_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", quality="custom", custom_quality=20)
        idx = cmd.index("-cq")
        assert cmd[idx + 1] == "20"

    def test_nvenc_speed_preset_quality(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", speed_preset="quality")
        assert "-preset" in cmd
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "p7"

    def test_nvenc_speed_preset_balanced(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", speed_preset="balanced")
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "p5"

    def test_nvenc_speed_preset_fast(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc", speed_preset="fast")
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "p3"

    def test_nvenc_includes_profile_level(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "nvenc")
        assert "-profile:v" in cmd
        assert "-level:v" in cmd

    # ── QSV ───────────────────────────────────────────────────────────────

    def test_qsv_uses_h264_qsv_codec(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "qsv")
        assert "h264_qsv" in cmd

    def test_qsv_uses_global_quality_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "qsv", quality="high")
        assert "-global_quality" in cmd
        assert "-crf" not in cmd
        assert "-cq" not in cmd

    def test_qsv_high_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "qsv", quality="high")
        idx = cmd.index("-global_quality")
        assert cmd[idx + 1] == "18"

    def test_qsv_custom_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "qsv", quality="custom", custom_quality=25)
        idx = cmd.index("-global_quality")
        assert cmd[idx + 1] == "25"

    def test_qsv_speed_preset_balanced(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "qsv", speed_preset="balanced")
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "medium"

    # ── AMF ───────────────────────────────────────────────────────────────

    def test_amf_uses_h264_amf_codec(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "amf")
        assert "h264_amf" in cmd

    def test_amf_uses_qp_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "amf", quality="standard")
        assert "-qp" in cmd

    def test_amf_standard_quality_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "amf", quality="standard")
        idx = cmd.index("-qp")
        assert cmd[idx + 1] == "23"

    def test_amf_speed_flag_is_quality_not_preset(self, tmp_path: Path):
        """AMF uses -quality not -preset for speed control."""
        cmd = self._cmd(tmp_path, "amf", speed_preset="quality")
        assert "-quality" in cmd

    def test_amf_speed_balanced_value(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "amf", speed_preset="balanced")
        idx = cmd.index("-quality")
        assert cmd[idx + 1] == "balanced"

    # ── VideoToolbox ──────────────────────────────────────────────────────

    def test_videotoolbox_uses_h264_videotoolbox_codec(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "videotoolbox")
        assert "h264_videotoolbox" in cmd

    def test_videotoolbox_no_profile_level(self, tmp_path: Path):
        """VideoToolbox does not accept -profile:v/-level:v."""
        cmd = self._cmd(tmp_path, "videotoolbox")
        assert "-profile:v" not in cmd
        assert "-level:v" not in cmd

    def test_videotoolbox_uses_qv_flag(self, tmp_path: Path):
        cmd = self._cmd(tmp_path, "videotoolbox")
        assert "-q:v" in cmd

    # ── Common GPU requirements ────────────────────────────────────────────

    def test_gpu_cmd_still_has_progress_api(self, tmp_path: Path):
        for key in ("nvenc", "qsv", "amf", "videotoolbox"):
            cmd = self._cmd(tmp_path, key)
            assert "-progress" in cmd, f"{key}: missing -progress"
            assert "pipe:1" in cmd, f"{key}: missing pipe:1"
            assert "-nostats" in cmd, f"{key}: missing -nostats"

    def test_gpu_cmd_has_audio_flags(self, tmp_path: Path):
        for key in ("nvenc", "qsv", "amf"):
            cmd = self._cmd(tmp_path, key)
            assert "-c:a" in cmd, f"{key}: missing -c:a"
            assert "aac" in cmd, f"{key}: missing aac"
            assert "-movflags" in cmd, f"{key}: missing -movflags"

    def test_unknown_encoder_falls_back_to_cpu(self, tmp_path: Path):
        """An unrecognized encoder_key must silently fall back to libx264."""
        from app.services.ffmpeg_convert_service import _PRESETS
        settings = EncodeSettings(encoder_key="unknown_gpu_xyz")
        cmd = FfmpegConvertService._build_cmd(
            tmp_path / "ffmpeg",
            tmp_path / "in.mkv",
            tmp_path / "out.mp4",
            _PRESETS["standard"],
            encode_settings=settings,
        )
        assert "libx264" in cmd
        assert "-crf" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# NEW: _build_cmd CPU path with EncodeSettings (speed preset mapping)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildCmdCpuWithSettings:
    def _cpu_cmd(self, tmp_path: Path, quality: str = "standard",
                 speed: str = "balanced", custom: int = 23) -> list[str]:
        from app.services.ffmpeg_convert_service import _PRESETS
        settings = EncodeSettings(
            quality=quality, custom_quality=custom,
            encoder_key="cpu", speed_preset=speed,
        )
        preset = _PRESETS.get(quality, _PRESETS["standard"])
        return FfmpegConvertService._build_cmd(
            tmp_path / "ffmpeg", tmp_path / "in.mkv",
            tmp_path / "out.mp4", preset,
            encode_settings=settings,
        )

    def test_cpu_quality_speed_maps_to_medium(self, tmp_path: Path):
        cmd = self._cpu_cmd(tmp_path, speed="quality")
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "medium"

    def test_cpu_balanced_speed_maps_to_fast(self, tmp_path: Path):
        cmd = self._cpu_cmd(tmp_path, speed="balanced")
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "fast"

    def test_cpu_fast_speed_maps_to_veryfast(self, tmp_path: Path):
        cmd = self._cpu_cmd(tmp_path, speed="fast")
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "veryfast"

    def test_cpu_custom_quality_uses_custom_value(self, tmp_path: Path):
        cmd = self._cpu_cmd(tmp_path, quality="custom", custom=18)
        idx = cmd.index("-crf")
        assert cmd[idx + 1] == "18"

    def test_cpu_standard_quality_uses_preset_crf(self, tmp_path: Path):
        cmd = self._cpu_cmd(tmp_path, quality="standard")
        idx = cmd.index("-crf")
        assert cmd[idx + 1] == "23"

    def test_cpu_high_quality_uses_preset_crf(self, tmp_path: Path):
        cmd = self._cpu_cmd(tmp_path, quality="high")
        idx = cmd.index("-crf")
        assert cmd[idx + 1] == "18"


# ─────────────────────────────────────────────────────────────────────────────
# NEW: GPU fallback behaviour in _convert_sync
# ─────────────────────────────────────────────────────────────────────────────

class TestGpuFallback:
    """When a GPU encoder fails, _convert_sync retries with CPU (libx264)."""

    def test_fallback_to_cpu_on_gpu_failure(self, tmp_path: Path):
        source = tmp_path / "video.mkv"
        source.write_bytes(b"x" * 100)

        svc = FfmpegConvertService()
        fresh_calls: list[dict] = []

        gpu_settings = EncodeSettings(
            quality="high", encoder_key="nvenc", speed_preset="balanced"
        )

        call_count = [0]

        def fake_fresh(ffmpeg_bin, src, dest_dir, temp_output,
                       duration_s, preset, on_progress,
                       encode_settings=None, cancel_event=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call (GPU) — fail
                raise ConversionError("NVENC not supported")
            # Second call (CPU fallback) — succeed
            out = dest_dir / "video_iPhone.mp4"
            out.write_bytes(b"x" * 2000)
            fresh_calls.append({"encoder": encode_settings})
            return out

        with patch.object(svc, "_probe_duration", return_value=120.0):
            with patch.object(svc, "_fresh_encode", side_effect=fake_fresh):
                with patch.object(
                    svc.__class__, "_locate_ffmpeg_bin",
                    staticmethod(lambda: tmp_path / "ffmpeg")
                ):
                    try:
                        result = svc._convert_sync(
                            source, "high", tmp_path, None, gpu_settings
                        )
                    except ConversionError:
                        pytest.fail("Should have fallen back to CPU, not raised")

        assert call_count[0] == 2, "Expected exactly 2 encode calls (GPU + CPU retry)"
        assert fresh_calls[0]["encoder"].encoder_key == "cpu", (
            "Second call must use CPU encoder"
        )

    def test_cpu_failure_not_retried(self, tmp_path: Path):
        """CPU (libx264) failures must propagate immediately without retry."""
        source = tmp_path / "video.mkv"
        source.write_bytes(b"x" * 100)

        svc = FfmpegConvertService()
        cpu_settings = EncodeSettings(quality="standard", encoder_key="cpu")
        call_count = [0]

        def fake_fresh(*a, **kw):
            call_count[0] += 1
            raise ConversionError("libx264 failed for real")

        with patch.object(svc, "_probe_duration", return_value=60.0):
            with patch.object(svc, "_fresh_encode", side_effect=fake_fresh):
                with patch.object(
                    svc.__class__, "_locate_ffmpeg_bin",
                    staticmethod(lambda: tmp_path / "ffmpeg")
                ):
                    with pytest.raises(ConversionError, match="libx264"):
                        svc._convert_sync(
                            source, "standard", tmp_path, None, cpu_settings
                        )

        assert call_count[0] == 1, "CPU failure must not be retried"


# ─────────────────────────────────────────────────────────────────────────────
# NEW: ConvertQueue passes encode_settings through
# ─────────────────────────────────────────────────────────────────────────────

class TestConvertQueueEncodeSettings:
    def test_encode_settings_passed_to_svc_run(self, tmp_path: Path):
        """ConvertQueue.submit must forward encode_settings to _svc._run."""
        source = tmp_path / "video.mkv"
        source.touch()

        received: list[Optional[EncodeSettings]] = []
        done_event = threading.Event()
        queue = ConvertQueue(max_concurrent=1)

        def fake_run(src, quality, output_dir, on_progress, on_done, on_error,
                     encode_settings=None, cancel_event=None):
            received.append(encode_settings)
            done_event.set()

        settings = EncodeSettings(quality="high", encoder_key="nvenc")
        with patch.object(queue._svc, "_run", side_effect=fake_run):
            queue.submit(source=source, encode_settings=settings)
            done_event.wait(timeout=3)

        assert len(received) == 1
        assert received[0] is not None
        assert received[0].encoder_key == "nvenc"
        assert received[0].quality == "high"

    def test_none_encode_settings_preserved(self, tmp_path: Path):
        """Passing encode_settings=None must reach _run as None (backward compat)."""
        source = tmp_path / "video.mkv"
        source.touch()

        received: list = []
        done_event = threading.Event()
        queue = ConvertQueue(max_concurrent=1)

        def fake_run(src, quality, output_dir, on_progress, on_done, on_error,
                     encode_settings=None, cancel_event=None):
            received.append(encode_settings)
            done_event.set()

        with patch.object(queue._svc, "_run", side_effect=fake_run):
            queue.submit(source=source, encode_settings=None)
            done_event.wait(timeout=3)

        assert received[0] is None


# ─────────────────────────────────────────────────────────────────────────────
# NEW: _validate_encoder_codec
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateEncoderCodec:
    """Unit tests for the lightweight per-encoder smoke-test function."""

    def test_returns_true_when_ffmpeg_exits_zero(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        fake = MagicMock()
        fake.returncode = 0
        with patch("subprocess.run", return_value=fake):
            assert _validate_encoder_codec(ffmpeg, "h264_nvenc") is True

    def test_returns_false_when_ffmpeg_exits_nonzero(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        fake = MagicMock()
        fake.returncode = 1
        with patch("subprocess.run", return_value=fake):
            assert _validate_encoder_codec(ffmpeg, "h264_nvenc") is False

    def test_returns_false_on_subprocess_exception(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg"
        with patch("subprocess.run", side_effect=OSError("not found")):
            assert _validate_encoder_codec(ffmpeg, "h264_nvenc") is False

    def test_returns_false_on_timeout(self, tmp_path: Path):
        import subprocess as sp
        ffmpeg = tmp_path / "ffmpeg"
        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd=[], timeout=30)):
            assert _validate_encoder_codec(ffmpeg, "h264_nvenc") is False

    def test_command_uses_lavfi_testsrc(self, tmp_path: Path):
        """The test encode must use a synthetic lavfi source, not a real file."""
        ffmpeg = tmp_path / "ffmpeg"
        captured: list[list[str]] = []
        fake = MagicMock(returncode=0)

        def capture_cmd(cmd, **kw):
            captured.append(list(cmd))
            return fake

        with patch("subprocess.run", side_effect=capture_cmd):
            _validate_encoder_codec(ffmpeg, "h264_nvenc")

        assert len(captured) == 1
        cmd = captured[0]
        assert "-f" in cmd
        lavfi_idx = cmd.index("-f")
        assert cmd[lavfi_idx + 1] == "lavfi"
        # Input source must contain "testsrc"
        assert any("testsrc" in tok for tok in cmd)

    def test_command_uses_null_output(self, tmp_path: Path):
        """No output file should be written — output must be discarded."""
        ffmpeg = tmp_path / "ffmpeg"
        captured: list[list[str]] = []
        fake = MagicMock(returncode=0)

        def capture_cmd(cmd, **kw):
            captured.append(list(cmd))
            return fake

        with patch("subprocess.run", side_effect=capture_cmd):
            _validate_encoder_codec(ffmpeg, "h264_nvenc")

        cmd = captured[0]
        assert "null" in cmd
        assert "-" in cmd

    def test_command_includes_given_codec(self, tmp_path: Path):
        """The function must encode with the requested codec."""
        ffmpeg = tmp_path / "ffmpeg"
        captured: list[list[str]] = []
        fake = MagicMock(returncode=0)

        def capture_cmd(cmd, **kw):
            captured.append(list(cmd))
            return fake

        for codec in ("h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"):
            captured.clear()
            with patch("subprocess.run", side_effect=capture_cmd):
                _validate_encoder_codec(ffmpeg, codec)
            cmd = captured[0]
            assert "-c:v" in cmd
            cv_idx = cmd.index("-c:v")
            assert cmd[cv_idx + 1] == codec, (
                f"Expected codec {codec!r}, got {cmd[cv_idx + 1]!r}"
            )

    def test_command_limits_frames(self, tmp_path: Path):
        """Encode must be limited to at most 1 frame to stay fast."""
        ffmpeg = tmp_path / "ffmpeg"
        captured: list[list[str]] = []
        fake = MagicMock(returncode=0)

        def capture_cmd(cmd, **kw):
            captured.append(list(cmd))
            return fake

        with patch("subprocess.run", side_effect=capture_cmd):
            _validate_encoder_codec(ffmpeg, "h264_nvenc")

        cmd = captured[0]
        assert "-frames:v" in cmd
        frames_idx = cmd.index("-frames:v")
        assert int(cmd[frames_idx + 1]) <= 1

    def test_does_not_use_shell_true(self, tmp_path: Path):
        """subprocess must never be invoked with shell=True."""
        ffmpeg = tmp_path / "ffmpeg"
        captured_kwargs: list[dict] = []
        fake = MagicMock(returncode=0)

        def capture_cmd(cmd, **kw):
            captured_kwargs.append(kw)
            return fake

        with patch("subprocess.run", side_effect=capture_cmd):
            _validate_encoder_codec(ffmpeg, "h264_nvenc")

        for kw in captured_kwargs:
            assert not kw.get("shell", False), "shell=True must never be used"


# ─────────────────────────────────────────────────────────────────────────────
# NEW: detect_available_encoders — validation integration
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectWithValidation:
    """Test that detect_available_encoders gates on _validate_encoder_codec."""

    def _encoders_result(self, stdout: str) -> MagicMock:
        r = MagicMock()
        r.returncode = 0
        r.stdout = stdout
        r.stderr = ""
        return r

    def test_encoder_excluded_when_validation_fails(self, tmp_path: Path):
        """An encoder listed in -encoders but rejected by validation is excluded."""
        ffmpeg = tmp_path / "ffmpeg"
        list_output = " V..... h264_nvenc   NVIDIA NVENC H.264\n"
        list_result = self._encoders_result(list_output)

        call_count = [0]

        def side_effect(cmd, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: -encoders listing
                return list_result
            # Subsequent calls: validation — simulate driver missing
            r = MagicMock()
            r.returncode = 1
            return r

        with patch("subprocess.run", side_effect=side_effect):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)

        assert "nvenc" not in result, (
            "nvenc must be excluded when validation fails"
        )
        assert "cpu" in result

    def test_encoder_included_when_validation_succeeds(self, tmp_path: Path):
        """An encoder listed in -encoders and passing validation is included."""
        ffmpeg = tmp_path / "ffmpeg"
        list_output = " V..... h264_nvenc   NVIDIA NVENC H.264\n"
        list_result = self._encoders_result(list_output)
        ok_result = MagicMock(returncode=0)

        call_count = [0]

        def side_effect(cmd, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return list_result
            return ok_result

        with patch("subprocess.run", side_effect=side_effect):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)

        assert "nvenc" in result
        assert "cpu" in result

    def test_partial_validation_failure(self, tmp_path: Path):
        """When one GPU validates and another fails, only the valid one is added."""
        ffmpeg = tmp_path / "ffmpeg"
        list_output = (
            " V..... h264_nvenc   NVIDIA\n"
            " V..... h264_qsv     Intel\n"
        )
        list_result = self._encoders_result(list_output)

        call_count = [0]

        def side_effect(cmd, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return list_result
            # First validation (nvenc): fails
            if call_count[0] == 2:
                return MagicMock(returncode=1)
            # Second validation (qsv): passes
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=side_effect):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)

        # Exactly one GPU passed; exact key depends on iteration order,
        # so check the total GPU count rather than which one.
        gpu_keys = result - {"cpu"}
        assert len(gpu_keys) == 1, (
            f"Expected exactly 1 GPU encoder, got {gpu_keys}"
        )
        assert "cpu" in result

    def test_validation_called_once_per_detected_encoder(self, tmp_path: Path):
        """detect_available_encoders must validate every detected GPU codec."""
        ffmpeg = tmp_path / "ffmpeg"
        list_output = (
            " V..... h264_nvenc   NVIDIA\n"
            " V..... h264_qsv     Intel\n"
            " V..... h264_amf     AMD\n"
        )
        list_result = self._encoders_result(list_output)
        ok_result = MagicMock(returncode=0)

        calls: list[list[str]] = []

        def side_effect(cmd, **kw):
            calls.append(list(cmd))
            if len(calls) == 1:
                return list_result
            return ok_result

        with patch("subprocess.run", side_effect=side_effect):
            detect_available_encoders(ffmpeg_bin=ffmpeg)

        # 1 list call + 3 validation calls
        assert len(calls) == 4, (
            f"Expected 4 subprocess calls (1 list + 3 validate), got {len(calls)}"
        )
        # The first call must be the -encoders listing
        assert "-encoders" in calls[0]
        # The remaining 3 must each be test encodes with -f lavfi
        for validation_cmd in calls[1:]:
            assert "-f" in validation_cmd
            assert "lavfi" in validation_cmd

    def test_cpu_not_validated(self, tmp_path: Path):
        """CPU (libx264) must always be present without running a test encode."""
        ffmpeg = tmp_path / "ffmpeg"
        # No GPU codecs in the output → only the list call is made
        list_output = " V..... libx264   CPU\n"
        list_result = self._encoders_result(list_output)

        calls: list = []

        def side_effect(cmd, **kw):
            calls.append(cmd)
            return list_result

        with patch("subprocess.run", side_effect=side_effect):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)

        assert "cpu" in result
        # Only 1 subprocess call (the -encoders list); no validation needed for CPU
        assert len(calls) == 1

    def test_validation_via_patch(self, tmp_path: Path):
        """Patch _validate_encoder_codec directly to test composition."""
        ffmpeg = tmp_path / "ffmpeg"
        list_output = (
            " V..... h264_nvenc   NVIDIA\n"
            " V..... h264_qsv     Intel\n"
        )
        list_result = self._encoders_result(list_output)

        validated: list[str] = []

        def fake_validate(fb: Path, codec: str) -> bool:
            validated.append(codec)
            return codec == "h264_nvenc"   # only nvenc passes

        with patch("subprocess.run", return_value=list_result):
            with patch(
                "app.services.ffmpeg_convert_service._validate_encoder_codec",
                side_effect=fake_validate,
            ):
                result = detect_available_encoders(ffmpeg_bin=ffmpeg)

        assert "nvenc" in result
        assert "qsv" not in result
        assert set(validated) == {"h264_nvenc", "h264_qsv"}


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Encoder detection cache
# ─────────────────────────────────────────────────────────────────────────────

import time as _time_mod
import app.services.ffmpeg_convert_service as _svc_mod

from app.services.ffmpeg_convert_service import (
    _encoder_cache_get,
    _encoder_cache_set,
    _ENCODER_CACHE_TTL_S,
    get_available_encoder_options,
)


def _reset_cache() -> None:
    """Helper: clear the module-level encoder cache between tests."""
    _svc_mod._encoder_cache = None
    _svc_mod._encoder_cache_ts = 0.0


class FakeLoc:
    """Minimal locate_ffmpeg() return value that points at a dummy binary."""
    def __init__(self, path: Path) -> None:
        self.ffmpeg_bin = str(path)
        self.ffprobe_bin = str(path)


class TestEncoderCache:
    """Encoder detection result is cached to avoid repeated test-encodes."""

    def test_cache_miss_returns_none_initially(self):
        _reset_cache()
        assert _encoder_cache_get() is None

    def test_cache_set_then_get_returns_same_set(self):
        _reset_cache()
        _encoder_cache_set({"cpu", "nvenc"})
        result = _encoder_cache_get()
        assert result == {"cpu", "nvenc"}

    def test_cache_get_returns_defensive_copy(self):
        _reset_cache()
        _encoder_cache_set({"cpu"})
        copy1 = _encoder_cache_get()
        copy1.add("bogus")  # mutate the returned copy
        copy2 = _encoder_cache_get()
        assert "bogus" not in copy2, "Mutating returned copy must not corrupt cache"

    def test_cache_expires_after_ttl(self):
        _reset_cache()
        _encoder_cache_set({"cpu"})
        # Wind the clock past the TTL
        _svc_mod._encoder_cache_ts = _time_mod.monotonic() - _ENCODER_CACHE_TTL_S - 1
        assert _encoder_cache_get() is None

    def test_cache_still_fresh_within_ttl(self):
        _reset_cache()
        _encoder_cache_set({"cpu", "qsv"})
        # Move clock forward but stay within the TTL window
        _svc_mod._encoder_cache_ts = _time_mod.monotonic() - _ENCODER_CACHE_TTL_S + 60
        result = _encoder_cache_get()
        assert result == {"cpu", "qsv"}

    def test_detect_second_call_uses_cache(self, tmp_path: Path):
        """When called twice without explicit ffmpeg_bin, subprocess runs once."""
        _reset_cache()
        ffmpeg = tmp_path / "ffmpeg"
        fake_loc = FakeLoc(ffmpeg)
        list_result = MagicMock(returncode=0, stdout=" V..... libx264\n", stderr="")
        call_count = [0]

        def se(cmd, **kw):
            call_count[0] += 1
            return list_result

        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg",
                   return_value=fake_loc):
            with patch("subprocess.run", side_effect=se):
                r1 = detect_available_encoders()
                r2 = detect_available_encoders()

        assert call_count[0] == 1, (
            f"Expected 1 subprocess call (second uses cache), got {call_count[0]}"
        )
        assert r1 == r2

    def test_detect_explicit_ffmpeg_bin_bypasses_cache_read(self, tmp_path: Path):
        """An explicit ffmpeg_bin always runs fresh (no cache read)."""
        _reset_cache()
        # Pre-populate cache with a value
        _encoder_cache_set({"cpu", "nvenc"})
        ffmpeg = tmp_path / "ffmpeg"
        list_result = MagicMock(returncode=0, stdout=" V..... libx264\n", stderr="")
        call_count = [0]

        def se(cmd, **kw):
            call_count[0] += 1
            return list_result

        with patch("subprocess.run", side_effect=se):
            result = detect_available_encoders(ffmpeg_bin=ffmpeg)

        # subprocess must have been called even though cache was populated
        assert call_count[0] >= 1, "Explicit ffmpeg_bin must bypass cache read"
        # Result comes from fresh detection (libx264-only output → cpu only)
        assert result == {"cpu"}

    def test_detect_populates_cache(self, tmp_path: Path):
        """After detect runs, cache is populated for subsequent no-bin calls."""
        _reset_cache()
        ffmpeg = tmp_path / "ffmpeg"
        fake_loc = FakeLoc(ffmpeg)
        list_result = MagicMock(returncode=0, stdout=" V..... libx264\n", stderr="")

        with patch("app.services.ffmpeg_convert_service.locate_ffmpeg",
                   return_value=fake_loc):
            with patch("subprocess.run", return_value=list_result):
                detect_available_encoders()

        cached = _encoder_cache_get()
        assert cached is not None, "Cache must be populated after detection"
        assert "cpu" in cached

    def test_ttl_constant_is_positive(self):
        assert _ENCODER_CACHE_TTL_S > 0


# ─────────────────────────────────────────────────────────────────────────────
# NEW: get_available_encoder_options
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAvailableEncoderOptions:
    """UI helper returns only encoders that are actually available."""

    def _run(self, available_keys: set[str]) -> list[tuple[str, str]]:
        _reset_cache()
        with patch(
            "app.services.ffmpeg_convert_service.detect_available_encoders",
            return_value=available_keys,
        ):
            return get_available_encoder_options()

    def test_cpu_always_included(self):
        opts = self._run({"cpu"})
        keys = [k for k, _ in opts]
        assert "cpu" in keys

    def test_cpu_only_when_no_gpu(self):
        opts = self._run({"cpu"})
        assert len(opts) == 1
        assert opts[0][0] == "cpu"

    def test_gpu_included_when_available(self):
        opts = self._run({"cpu", "nvenc"})
        keys = [k for k, _ in opts]
        assert "nvenc" in keys

    def test_absent_gpu_excluded(self):
        opts = self._run({"cpu"})
        keys = [k for k, _ in opts]
        assert "nvenc" not in keys
        assert "qsv" not in keys
        assert "amf" not in keys

    def test_multiple_gpus_included(self):
        opts = self._run({"cpu", "nvenc", "qsv", "amf"})
        keys = [k for k, _ in opts]
        assert "nvenc" in keys and "qsv" in keys and "amf" in keys

    def test_order_matches_encoder_options(self):
        """Returned order must match ENCODER_OPTIONS, not arbitrary set order."""
        from app.services.ffmpeg_convert_service import ENCODER_OPTIONS
        opts = self._run({"cpu", "nvenc", "qsv"})
        keys = [k for k, _ in opts]
        expected_order = [k for k, _ in ENCODER_OPTIONS if k in {"cpu", "nvenc", "qsv"}]
        assert keys == expected_order

    def test_labels_match_encoder_options(self):
        """Labels returned must be the same as in ENCODER_OPTIONS."""
        from app.services.ffmpeg_convert_service import ENCODER_OPTIONS
        opts = self._run({"cpu", "nvenc"})
        label_map = {k: l for k, l in ENCODER_OPTIONS}
        for key, label in opts:
            assert label == label_map[key]

    def test_returns_list_of_tuples(self):
        opts = self._run({"cpu"})
        assert isinstance(opts, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in opts)


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Speed preset label clarity
# ─────────────────────────────────────────────────────────────────────────────

class TestSpeedOptionLabels:
    """Speed preset labels must be clear and not confuse speed with quality."""

    def test_three_speed_options_exist(self):
        assert len(SPEED_OPTIONS) == 3

    def test_speed_option_keys_unchanged(self):
        keys = [k for k, _ in SPEED_OPTIONS]
        assert keys == ["quality", "balanced", "fast"]

    def test_quality_label_not_ambiguously_named_quality(self):
        """'quality' speed preset label must not just say 'Chất lượng'/'Chat luong'
        which users confuse with the output quality level."""
        quality_label = next(l for k, l in SPEED_OPTIONS if k == "quality")
        # The old ambiguous label was "Chat luong" — must be changed
        assert quality_label.lower() not in ("chat luong", "chất lượng"), (
            f"Label {quality_label!r} is too ambiguous — must clarify it means slower"
        )

    def test_all_labels_are_non_empty_strings(self):
        for key, label in SPEED_OPTIONS:
            assert isinstance(label, str) and label.strip()


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Command builder helper functions
# ─────────────────────────────────────────────────────────────────────────────

from app.services.ffmpeg_convert_service import (
    _HW_ENCODER_CATALOG as _CATALOG,
)


class TestBuildCpuFlags:
    """_build_cpu_flags returns the correct libx264 flags."""

    def test_legacy_none_settings_reads_preset(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], None)
        assert "-c:v" in flags and flags[flags.index("-c:v") + 1] == "libx264"
        assert "-crf" in flags and flags[flags.index("-crf") + 1] == "23"
        assert "-preset" in flags and flags[flags.index("-preset") + 1] == "fast"

    def test_high_preset_via_none_settings(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["high"], None)
        assert flags[flags.index("-crf") + 1] == "18"
        assert flags[flags.index("-preset") + 1] == "medium"

    def test_speed_quality_maps_to_medium(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        s = EncodeSettings(encoder_key="cpu", speed_preset="quality")
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], s)
        assert flags[flags.index("-preset") + 1] == "medium"

    def test_speed_balanced_maps_to_fast(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        s = EncodeSettings(encoder_key="cpu", speed_preset="balanced")
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], s)
        assert flags[flags.index("-preset") + 1] == "fast"

    def test_speed_fast_maps_to_veryfast(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        s = EncodeSettings(encoder_key="cpu", speed_preset="fast")
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], s)
        assert flags[flags.index("-preset") + 1] == "veryfast"

    def test_custom_quality_uses_custom_value(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        s = EncodeSettings(encoder_key="cpu", quality="custom", custom_quality=20)
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], s)
        assert flags[flags.index("-crf") + 1] == "20"

    def test_always_includes_profile_and_level(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], None)
        assert "-profile:v" in flags and flags[flags.index("-profile:v") + 1] == "main"
        assert "-level:v" in flags and flags[flags.index("-level:v") + 1] == "4.1"

    def test_result_is_list_of_strings(self):
        from app.services.ffmpeg_convert_service import _PRESETS
        flags = FfmpegConvertService._build_cpu_flags(_PRESETS["standard"], None)
        assert isinstance(flags, list)
        assert all(isinstance(f, str) for f in flags)


class TestBuildGpuFlags:
    """_build_gpu_flags returns the correct hardware-encoder flags."""

    def test_nvenc_codec(self):
        s = EncodeSettings(encoder_key="nvenc", quality="standard")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["nvenc"], s)
        assert "h264_nvenc" in flags

    def test_nvenc_quality_flag_is_cq(self):
        s = EncodeSettings(encoder_key="nvenc", quality="standard")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["nvenc"], s)
        assert "-cq" in flags and flags[flags.index("-cq") + 1] == "23"

    def test_nvenc_custom_quality(self):
        s = EncodeSettings(encoder_key="nvenc", quality="custom", custom_quality=17)
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["nvenc"], s)
        assert flags[flags.index("-cq") + 1] == "17"

    def test_nvenc_speed_balanced(self):
        s = EncodeSettings(encoder_key="nvenc", speed_preset="balanced")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["nvenc"], s)
        assert "-preset" in flags and flags[flags.index("-preset") + 1] == "p5"

    def test_qsv_codec_and_quality_flag(self):
        s = EncodeSettings(encoder_key="qsv", quality="high")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["qsv"], s)
        assert "h264_qsv" in flags
        assert "-global_quality" in flags and flags[flags.index("-global_quality") + 1] == "18"

    def test_amf_codec_and_quality_flag(self):
        s = EncodeSettings(encoder_key="amf", quality="standard")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["amf"], s)
        assert "h264_amf" in flags
        assert "-qp" in flags

    def test_amf_speed_flag_is_quality_not_preset(self):
        s = EncodeSettings(encoder_key="amf", speed_preset="balanced")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["amf"], s)
        assert "-quality" in flags and flags[flags.index("-quality") + 1] == "balanced"

    def test_videotoolbox_no_profile_level(self):
        s = EncodeSettings(encoder_key="videotoolbox", quality="standard")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["videotoolbox"], s)
        assert "-profile:v" not in flags
        assert "-level:v" not in flags

    def test_videotoolbox_uses_qv_flag(self):
        s = EncodeSettings(encoder_key="videotoolbox", quality="standard")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["videotoolbox"], s)
        assert "-q:v" in flags

    def test_result_is_list_of_strings(self):
        s = EncodeSettings(encoder_key="nvenc", quality="standard")
        flags = FfmpegConvertService._build_gpu_flags(_CATALOG["nvenc"], s)
        assert isinstance(flags, list)
        assert all(isinstance(f, str) for f in flags)

    def test_build_cmd_delegates_to_helpers(self, tmp_path: Path):
        """_build_cmd result must be identical whether inline or via helpers."""
        from app.services.ffmpeg_convert_service import _PRESETS
        s = EncodeSettings(encoder_key="nvenc", quality="high", speed_preset="fast")
        cmd = FfmpegConvertService._build_cmd(
            tmp_path / "ffmpeg", tmp_path / "in.mkv",
            tmp_path / "out.mp4", _PRESETS["high"],
            encode_settings=s,
        )
        # Verify the full command still contains the expected flags
        assert "h264_nvenc" in cmd
        assert "-cq" in cmd and cmd[cmd.index("-cq") + 1] == "19"
        assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "p3"
        assert "-progress" in cmd and "pipe:1" in cmd


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Progress watchdog
# ─────────────────────────────────────────────────────────────────────────────

import io as _io_mod


class _HealthyProc:
    """Simulates an ffmpeg process that produces stdout quickly and exits 0."""

    returncode = 0

    def __init__(self) -> None:
        lines = b"out_time_ms=1000000\nout_time_ms=2000000\nprogress=end\n"
        self.stdout = _io_mod.BytesIO(lines)
        self.stderr = _io_mod.BytesIO(b"")
        self.killed = False

    def wait(self, timeout=None) -> int:
        return 0

    def poll(self) -> Optional[int]:
        return 0

    def kill(self) -> None:
        self.killed = True

    def communicate(self):
        return b"", b""


class _StalledProc:
    """Simulates an ffmpeg process that produces no stdout and never exits."""

    returncode: Optional[int] = None

    def __init__(self) -> None:
        self._killed = threading.Event()
        self.stderr = _io_mod.BytesIO(b"")
        self.stdout = self._make_blocking_stdout()
        self.killed = False

    def _make_blocking_stdout(self):
        parent = self

        class _BlockIO(_io_mod.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, b) -> int:
                parent._killed.wait()
                return 0   # EOF after kill

        return _io_mod.BufferedReader(_BlockIO())

    def wait(self, timeout=None) -> int:
        self._killed.wait(timeout=timeout)
        self.returncode = -9
        return -9

    def poll(self) -> Optional[int]:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._killed.set()

    def communicate(self):
        return b"", b""


class TestProgressWatchdog:
    """Watchdog terminates a stalled FFmpeg process after silence timeout."""

    def test_healthy_process_not_killed(self):
        proc = _HealthyProc()
        with patch("subprocess.Popen", return_value=proc):
            FfmpegConvertService._run_ffmpeg(
                ["/ffmpeg"], 10.0, None, watchdog_timeout_s=2.0
            )
        assert not proc.killed, "Watchdog must not kill a process that produces output"

    def test_stalled_process_is_killed(self):
        proc = _StalledProc()
        with patch("subprocess.Popen", return_value=proc):
            try:
                FfmpegConvertService._run_ffmpeg(
                    ["/ffmpeg"], 10.0, None, watchdog_timeout_s=1.0
                )
            except ConversionError:
                pass   # expected — process was killed → non-zero exit
        assert proc.killed, "Watchdog must kill a process that produces no stdout"

    def test_stalled_process_raises_conversion_error(self):
        proc = _StalledProc()
        with patch("subprocess.Popen", return_value=proc):
            with pytest.raises(ConversionError):
                FfmpegConvertService._run_ffmpeg(
                    ["/ffmpeg"], 10.0, None, watchdog_timeout_s=1.0
                )

    def test_watchdog_default_timeout_is_positive(self):
        """The default watchdog_timeout_s must be a positive number."""
        import inspect
        sig = inspect.signature(FfmpegConvertService._run_ffmpeg)
        default = sig.parameters["watchdog_timeout_s"].default
        assert isinstance(default, (int, float)) and default > 0

    def test_watchdog_default_timeout_is_reasonable(self):
        """Default timeout must be in a reasonable range (10 s – 5 min)."""
        import inspect
        sig = inspect.signature(FfmpegConvertService._run_ffmpeg)
        default = sig.parameters["watchdog_timeout_s"].default
        assert 10 <= default <= 300, (
            f"watchdog_timeout_s default {default} is outside 10–300 s range"
        )

    def test_progress_resets_watchdog_timer(self):
        """A process that produces stdout periodically must not be killed."""
        import queue

        class _SlowButAliveProc:
            returncode: Optional[int] = None

            def __init__(self) -> None:
                self._q: queue.Queue = queue.Queue()
                self.stderr = _io_mod.BytesIO(b"")
                self.stdout = self._make_trickle_stdout()
                self.killed = False
                # Emit one progress line every 0.2 s for 0.8 s then close
                def _feeder():
                    for i in range(4):
                        _time_mod.sleep(0.2)
                        self._q.put(
                            f"out_time_ms={i * 250_000}\n".encode()
                        )
                    self._q.put(None)  # sentinel → EOF
                threading.Thread(target=_feeder, daemon=True).start()

            def _make_trickle_stdout(self):
                parent = self

                class _TrickleIO(_io_mod.RawIOBase):
                    def readable(self): return True

                    def readinto(self, b):
                        item = parent._q.get()
                        if item is None:
                            return 0
                        n = len(item)
                        b[:n] = item
                        return n

                return _io_mod.BufferedReader(_TrickleIO())

            def wait(self, timeout=None):
                _time_mod.sleep(1.5)
                self.returncode = 0
                return 0

            def poll(self): return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

            def communicate(self): return b"", b""

        proc = _SlowButAliveProc()
        # Watchdog fires after 0.5 s without output; process emits every 0.2 s
        with patch("subprocess.Popen", return_value=proc):
            FfmpegConvertService._run_ffmpeg(
                ["/ffmpeg"], 10.0, None, watchdog_timeout_s=0.5
            )
        assert not proc.killed, (
            "Watchdog must not kill a process that produces output before the timeout"
        )


# ─────────────────────────────────────────────────────────────────────────────
# NEW: get_available_encoder_options — edge cases and contract
# ─────────────────────────────────────────────────────────────────────────────

class TestGetAvailableEncoderOptionsContract:
    """get_available_encoder_options must satisfy its full public contract."""

    def _run(self, available_keys: set[str]) -> list[tuple[str, str]]:
        _reset_cache()
        with patch(
            "app.services.ffmpeg_convert_service.detect_available_encoders",
            return_value=available_keys,
        ):
            return get_available_encoder_options()

    def test_cpu_present_when_only_cpu_available(self):
        opts = self._run({"cpu"})
        assert opts and opts[0][0] == "cpu"

    def test_cpu_present_when_gpu_also_available(self):
        opts = self._run({"cpu", "nvenc"})
        keys = [k for k, _ in opts]
        assert "cpu" in keys

    def test_unknown_key_not_in_result(self):
        opts = self._run({"cpu", "hypothetical_encoder"})
        keys = [k for k, _ in opts]
        assert "hypothetical_encoder" not in keys

    def test_result_subset_of_encoder_options(self):
        """Every returned key must come from the known ENCODER_OPTIONS list."""
        opts = self._run({"cpu", "nvenc", "qsv"})
        known_keys = {k for k, _ in ENCODER_OPTIONS}
        for key, _ in opts:
            assert key in known_keys

    def test_all_four_gpus_returned_when_all_available(self):
        opts = self._run({"cpu", "nvenc", "qsv", "amf", "videotoolbox"})
        keys = [k for k, _ in opts]
        for k in ("nvenc", "qsv", "amf", "videotoolbox"):
            assert k in keys

    def test_passes_ffmpeg_bin_through(self, tmp_path: Path):
        """Explicit ffmpeg_bin must reach detect_available_encoders."""
        received: list = []

        def fake_detect(ffmpeg_bin=None):
            received.append(ffmpeg_bin)
            return {"cpu"}

        with patch(
            "app.services.ffmpeg_convert_service.detect_available_encoders",
            side_effect=fake_detect,
        ):
            get_available_encoder_options(ffmpeg_bin=tmp_path / "ffmpeg")

        assert received[0] == tmp_path / "ffmpeg"
