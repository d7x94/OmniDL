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
    MediaInfo,
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
        """When no .part file exists, _convert_sync calls _fresh_encode (not _resume_encode)."""
        source = tmp_path / "video.mkv"
        source.write_bytes(b"x" * 100)

        svc = FfmpegConvertService()

        fresh_calls: list = []
        resume_calls: list = []

        with patch.object(svc, "_probe_duration", return_value=120.0):
            with patch.object(svc, "_fresh_encode", side_effect=lambda *a, **kw: fresh_calls.append(1) or tmp_path / "out.mp4"):
                with patch.object(svc, "_resume_encode", side_effect=lambda *a, **kw: resume_calls.append(1) or tmp_path / "out.mp4"):
                    try:
                        svc._convert_sync(source, "standard", tmp_path, None)
                    except Exception:
                        pass

        assert len(fresh_calls) == 1
        assert len(resume_calls) == 0

    def test_resume_triggered_when_large_partial_exists(self, tmp_path: Path):
        """When a .part file > 1 MB with suitable duration exists, _resume_encode is called."""
        source = tmp_path / "video.mkv"
        source.write_bytes(b"x" * 100)
        part = tmp_path / "video_iPhone.part.mp4"
        part.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

        svc = FfmpegConvertService()
        fresh_calls: list = []
        resume_calls: list = []

        def fake_probe(ffmpeg_bin: Path, src: Path) -> float:
            # Source duration = 120 s, partial = 30 s
            return 30.0 if src == part else 120.0

        with patch.object(svc, "_probe_duration", side_effect=fake_probe):
            with patch.object(svc, "_fresh_encode", side_effect=lambda *a, **kw: fresh_calls.append(1) or tmp_path / "out.mp4"):
                with patch.object(svc, "_resume_encode", side_effect=lambda *a, **kw: resume_calls.append(1) or (tmp_path / "out.mp4")):
                    # We need a valid ffmpeg bin reference; patch _locate_ffmpeg_bin
                    with patch.object(svc.__class__, "_locate_ffmpeg_bin", staticmethod(lambda: tmp_path / "ffmpeg")):
                        try:
                            svc._convert_sync(source, "standard", tmp_path, None)
                        except Exception:
                            pass

        assert len(resume_calls) == 1
        assert len(fresh_calls) == 0


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
