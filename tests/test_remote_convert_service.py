"""
tests/test_remote_convert_service.py
Unit tests for app/services/remote_convert_service.py — RemoteConvertService.

Coverage targets:
- start_convert() validation (encoder_key, quality, speed_preset, custom_crf)
- start_convert() creates a ConversionJob with correct fields and registers it
- get_job() / get_all_jobs()
- cancel_convert(): rejects terminal jobs, sets cancel flag on active jobs
- delete_convert_file(): guards against non-COMPLETED jobs, path traversal,
  sets output_deleted=True on success
- MAX_JOBS purge: oldest terminal job evicted when limit reached

All filesystem and FFmpeg calls are mocked — no real subprocess or disk I/O.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.remote_convert_service import (
    _VALID_ENCODERS,
    _VALID_EXTS,
    _VALID_QUALITIES,
    _VALID_SPEEDS,
    MAX_JOBS,
    RemoteConvertService,
)
from domain.models.conversion_job import ConversionJob, ConversionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(tmp_path: Path) -> RemoteConvertService:
    """Build a RemoteConvertService with mocked dependencies."""
    config = MagicMock()
    config.download_dir = str(tmp_path)
    event_bus = MagicMock()
    return RemoteConvertService(config=config, event_bus=event_bus, taildrop=None)


def _dummy_file(tmp_path: Path, name: str = "video.mp4") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00" * 16)
    return p


# ---------------------------------------------------------------------------
# start_convert — validation
# ---------------------------------------------------------------------------


class TestStartConvertValidation:
    """Invalid parameters must raise ValueError before any job is created."""

    def test_invalid_encoder_key_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="encoder_key"):
            svc.start_convert("tid", _dummy_file(tmp_path), encoder_key="h265_magic")

    def test_invalid_quality_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="quality"):
            svc.start_convert("tid", _dummy_file(tmp_path), quality="ultra")

    def test_invalid_speed_preset_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="speed_preset"):
            svc.start_convert("tid", _dummy_file(tmp_path), speed_preset="instant")

    def test_custom_crf_clamped_low(self, tmp_path):
        """custom_crf below 0 must be clamped to 0, not raise."""
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path), quality="custom", custom_crf=-5)
        assert job.custom_crf == 0

    def test_custom_crf_clamped_high(self, tmp_path):
        """custom_crf above 51 must be clamped to 51, not raise."""
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path), quality="custom", custom_crf=999)
        assert job.custom_crf == 51

    def test_all_valid_encoders_accepted(self, tmp_path):
        svc = _make_service(tmp_path)
        for enc in _VALID_ENCODERS:
            with patch.object(svc._queue, "submit", return_value=None):
                job = svc.start_convert("tid", _dummy_file(tmp_path), encoder_key=enc)
            # "auto" is resolved to the best available encoder before job creation
            if enc == "auto":
                assert job.encoder_key in _VALID_ENCODERS - {"auto"}
            else:
                assert job.encoder_key == enc

    def test_all_valid_qualities_accepted(self, tmp_path):
        svc = _make_service(tmp_path)
        for q in _VALID_QUALITIES:
            with patch.object(svc._queue, "submit", return_value=None):
                job = svc.start_convert("tid", _dummy_file(tmp_path), quality=q)
            assert job.quality == q

    def test_all_valid_speeds_accepted(self, tmp_path):
        svc = _make_service(tmp_path)
        for sp in _VALID_SPEEDS:
            with patch.object(svc._queue, "submit", return_value=None):
                job = svc.start_convert("tid", _dummy_file(tmp_path), speed_preset=sp)
            assert job.speed_preset == sp

    def test_invalid_target_ext_raises(self, tmp_path):
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="target_ext"):
            svc.start_convert("tid", _dummy_file(tmp_path), target_ext="exe")

    def test_target_ext_path_traversal_raises(self, tmp_path):
        """H2 regression: target_ext must not accept a path-traversal payload."""
        svc = _make_service(tmp_path)
        with pytest.raises(ValueError, match="target_ext"):
            svc.start_convert("tid", _dummy_file(tmp_path), target_ext="mp4/../../../../evil.bat")

    def test_all_valid_exts_accepted(self, tmp_path):
        svc = _make_service(tmp_path)
        for ext in _VALID_EXTS:
            with patch.object(svc._queue, "submit", return_value=None):
                svc.start_convert("tid", _dummy_file(tmp_path), target_ext=ext)


# ---------------------------------------------------------------------------
# start_convert — job creation
# ---------------------------------------------------------------------------


class TestStartConvertJobCreation:
    """Successful start_convert must create and register a ConversionJob."""

    def test_returns_conversion_job(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("task-abc", _dummy_file(tmp_path))
        assert isinstance(job, ConversionJob)

    def test_job_registered_in_get_job(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("task-abc", _dummy_file(tmp_path))
        assert svc.get_job(job.job_id) is job

    def test_job_source_task_id_set(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("my-task-id", _dummy_file(tmp_path))
        assert job.source_task_id == "my-task-id"

    def test_job_starts_as_pending(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        assert job.status == ConversionStatus.PENDING

    def test_output_deleted_is_false_initially(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        assert job.output_deleted is False

    def test_multiple_jobs_get_unique_ids(self, tmp_path):
        svc = _make_service(tmp_path)
        ids = set()
        for i in range(10):
            f = _dummy_file(tmp_path, f"video_{i}.mp4")
            with patch.object(svc._queue, "submit", return_value=None):
                job = svc.start_convert("tid", f)
            ids.add(job.job_id)
        assert len(ids) == 10


# ---------------------------------------------------------------------------
# get_job / get_all_jobs
# ---------------------------------------------------------------------------


class TestStartConvertFromPath:
    """start_convert_from_path() must delegate to start_convert() with
    source_task_id=''."""

    def test_delegates_to_start_convert_with_empty_source_task_id(self, tmp_path):
        svc = _make_service(tmp_path)
        sentinel = object()
        file_path = _dummy_file(tmp_path)
        with patch.object(svc, "start_convert", return_value=sentinel) as mock_start:
            result = svc.start_convert_from_path(
                file_path,
                encoder_key="nvenc",
                quality="high",
                speed_preset="fast",
                custom_crf=20,
                target_ext="mkv",
                output_codec="h265",
            )
        assert result is sentinel
        mock_start.assert_called_once_with(
            source_task_id="",
            file_path=file_path,
            encoder_key="nvenc",
            quality="high",
            speed_preset="fast",
            custom_crf=20,
            target_ext="mkv",
            output_codec="h265",
            generate_subtitles=False,
            subtitle_language="auto",
            subtitle_model="base",
            compute_vmaf=False,
        )


class TestGetJob:
    def test_get_job_returns_none_for_unknown_id(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.get_job("nonexistent") is None

    def test_get_all_jobs_empty_initially(self, tmp_path):
        svc = _make_service(tmp_path)
        assert svc.get_all_jobs() == []

    def test_get_all_jobs_includes_submitted_job(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        all_jobs = svc.get_all_jobs()
        assert job in all_jobs

    def test_get_all_jobs_returns_list(self, tmp_path):
        svc = _make_service(tmp_path)
        assert isinstance(svc.get_all_jobs(), list)


# ---------------------------------------------------------------------------
# cancel_convert
# ---------------------------------------------------------------------------


class TestCancelConvert:
    def test_cancel_pending_job_succeeds(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        result = svc.cancel_convert(job.job_id)
        assert result is True
        assert job.is_cancel_requested

    def test_cancel_unknown_job_returns_false(self, tmp_path):
        svc = _make_service(tmp_path)
        result = svc.cancel_convert("ghost-id")
        assert result is False

    def test_cancel_completed_job_returns_false(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        with job._lock:
            job.status = ConversionStatus.COMPLETED
        result = svc.cancel_convert(job.job_id)
        assert result is False

    def test_cancel_failed_job_returns_false(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        with job._lock:
            job.status = ConversionStatus.FAILED
        result = svc.cancel_convert(job.job_id)
        assert result is False


# ---------------------------------------------------------------------------
# delete_convert_file
# ---------------------------------------------------------------------------


class TestDeleteConvertFile:
    def test_delete_non_completed_job_fails(self, tmp_path):
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        # job is still PENDING
        ok, msg = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert not ok
        assert "not COMPLETED" in msg or "COMPLETED" in msg

    def test_delete_unknown_job_fails(self, tmp_path):
        svc = _make_service(tmp_path)
        ok, msg = svc.delete_convert_file("ghost", allowed_dir=tmp_path)
        assert not ok

    def test_delete_completed_job_sets_output_deleted(self, tmp_path):
        svc = _make_service(tmp_path)
        output_file = _dummy_file(tmp_path, "converted.mp4")
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            job.output_filename = str(output_file)
        ok, _ = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert ok
        assert job.output_deleted is True
        assert not output_file.exists()

    def test_delete_already_deleted_file_still_sets_flag(self, tmp_path):
        """If file is already gone, output_deleted must still be set True."""
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            # Point to a file that does NOT exist
            job.output_filename = str(tmp_path / "already_gone.mp4")
        ok, _ = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert ok
        assert job.output_deleted is True

    def test_delete_path_traversal_rejected(self, tmp_path):
        """Output file outside allowed_dir must be rejected (CWE-22)."""
        svc = _make_service(tmp_path)
        evil_file = tmp_path.parent / "evil.mp4"
        evil_file.write_bytes(b"\x00" * 8)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            job.output_filename = str(evil_file)
        ok, msg = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert not ok
        # File must NOT be deleted
        assert evil_file.exists()
        evil_file.unlink()


# ---------------------------------------------------------------------------
# MAX_JOBS purge
# ---------------------------------------------------------------------------


class TestMaxJobsPurge:
    def test_jobs_purged_when_limit_exceeded(self, tmp_path):
        """Oldest terminal jobs are evicted when MAX_JOBS is reached."""
        svc = _make_service(tmp_path)
        submitted: list[ConversionJob] = []
        # Fill up to MAX_JOBS with COMPLETED jobs
        for i in range(MAX_JOBS):
            f = _dummy_file(tmp_path, f"v{i}.mp4")
            with patch.object(svc._queue, "submit", return_value=None):
                job = svc.start_convert(f"tid-{i}", f)
            with job._lock:
                job.status = ConversionStatus.COMPLETED
            submitted.append(job)

        # Adding one more should trigger purge of the oldest
        extra = _dummy_file(tmp_path, "extra.mp4")
        with patch.object(svc._queue, "submit", return_value=None):
            new_job = svc.start_convert("tid-new", extra)

        all_ids = {j.job_id for j in svc.get_all_jobs()}
        # The new job must be present
        assert new_job.job_id in all_ids
        # Total job count must not exceed MAX_JOBS + 1 (purge fires before insert)
        assert len(svc.get_all_jobs()) <= MAX_JOBS


# ---------------------------------------------------------------------------
# Internal callbacks (lines 165-215 coverage)
# ---------------------------------------------------------------------------


class TestStartConvertCallbacks:
    """Exercise the closures passed to ConvertQueue.submit()."""

    def _capture_callbacks(self, tmp_path):
        """Return (svc, job, callbacks_dict) where callbacks are the real closures."""
        svc = _make_service(tmp_path)
        captured = {}

        def fake_submit(**kwargs):
            captured.update(kwargs)
            return lambda: None  # fake cancel_fn

        with patch.object(svc._queue, "submit", side_effect=fake_submit):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        return svc, job, captured

    def test_on_start_sets_converting_status(self, tmp_path):
        _, job, cbs = self._capture_callbacks(tmp_path)
        cbs["on_start"]()
        with job._lock:
            assert job.status == ConversionStatus.CONVERTING
            assert job.progress == 0.0

    def test_on_start_publishes_event(self, tmp_path):
        svc, job, cbs = self._capture_callbacks(tmp_path)
        cbs["on_start"]()
        svc._bus.publish_convert_started.assert_called()

    def test_on_progress_updates_job(self, tmp_path):
        _, job, cbs = self._capture_callbacks(tmp_path)
        cbs["on_progress"](42.5)
        with job._lock:
            assert job.progress == 42.5

    def test_on_progress_publishes_event(self, tmp_path):
        svc, job, cbs = self._capture_callbacks(tmp_path)
        cbs["on_progress"](10.0)
        svc._bus.publish_convert_progress.assert_called()

    def test_on_done_sets_completed_status(self, tmp_path):
        _, job, cbs = self._capture_callbacks(tmp_path)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"\x00" * 8)
        cbs["on_done"](out)
        with job._lock:
            assert job.status == ConversionStatus.COMPLETED
            assert job.progress == 100.0
            assert job.output_filename == str(out)
            assert job.finished_at is not None

    def test_on_done_publishes_event(self, tmp_path):
        svc, job, cbs = self._capture_callbacks(tmp_path)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"\x00" * 8)
        cbs["on_done"](out)
        svc._bus.publish_convert_completed.assert_called()

    def test_on_done_with_taildrop(self, tmp_path):
        """on_done calls taildrop.send_converted_file when taildrop is set."""
        config = MagicMock()
        config.download_dir = str(tmp_path)
        event_bus = MagicMock()
        taildrop = MagicMock()
        from app.services.remote_convert_service import RemoteConvertService

        svc = RemoteConvertService(config=config, event_bus=event_bus, taildrop=taildrop)

        captured = {}

        def fake_submit(**kwargs):
            captured.update(kwargs)
            return lambda: None

        with patch.object(svc._queue, "submit", side_effect=fake_submit):
            svc.start_convert("tid", _dummy_file(tmp_path))

        out = tmp_path / "out.mp4"
        out.write_bytes(b"\x00" * 8)
        captured["on_done"](out)
        taildrop.send_converted_file.assert_called_once_with(out)

    def test_on_done_taildrop_exception_swallowed(self, tmp_path):
        """Taildrop exception in on_done must not propagate."""
        config = MagicMock()
        config.download_dir = str(tmp_path)
        event_bus = MagicMock()
        taildrop = MagicMock()
        taildrop.send_converted_file.side_effect = RuntimeError("boom")
        from app.services.remote_convert_service import RemoteConvertService

        svc = RemoteConvertService(config=config, event_bus=event_bus, taildrop=taildrop)

        captured = {}

        def fake_submit(**kwargs):
            captured.update(kwargs)
            return lambda: None

        with patch.object(svc._queue, "submit", side_effect=fake_submit):
            svc.start_convert("tid", _dummy_file(tmp_path))

        out = tmp_path / "out.mp4"
        out.write_bytes(b"\x00" * 8)
        # Must not raise
        captured["on_done"](out)

    def test_on_error_sets_failed_status(self, tmp_path):
        _, job, cbs = self._capture_callbacks(tmp_path)
        cbs["on_error"]("encode failed")
        with job._lock:
            assert job.status == ConversionStatus.FAILED
            assert job.error_msg == "encode failed"
            assert job.finished_at is not None

    def test_on_error_publishes_failed_event(self, tmp_path):
        svc, job, cbs = self._capture_callbacks(tmp_path)
        cbs["on_error"]("oops")
        svc._bus.publish_convert_failed.assert_called()

    def test_on_error_sets_cancelled_when_requested(self, tmp_path):
        _, job, cbs = self._capture_callbacks(tmp_path)
        job.request_cancel()
        cbs["on_error"]("cancelled")
        with job._lock:
            assert job.status == ConversionStatus.CANCELLED

    def test_on_error_publishes_cancelled_event(self, tmp_path):
        svc, job, cbs = self._capture_callbacks(tmp_path)
        job.request_cancel()
        cbs["on_error"]("cancelled")
        svc._bus.publish_convert_cancelled.assert_called()


# ---------------------------------------------------------------------------
# Additional branch coverage for delete_convert_file and cancel_convert
# ---------------------------------------------------------------------------


class TestDeleteConvertFileBranches:
    def test_delete_no_output_filename_fails(self, tmp_path):
        """Job with empty output_filename must be rejected."""
        svc = _make_service(tmp_path)
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", _dummy_file(tmp_path))
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            job.output_filename = ""
        ok, msg = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert not ok
        assert "No output file" in msg

    def test_delete_oserror_returns_false(self, tmp_path):
        """OSError during unlink must return (False, error_string)."""
        svc = _make_service(tmp_path)
        output_file = _dummy_file(tmp_path, "del_test.mp4")
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", output_file)
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            job.output_filename = str(output_file)
        with patch("pathlib.Path.unlink", side_effect=OSError("permission denied")):
            ok, msg = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert not ok
        assert "permission denied" in msg

    def test_delete_path_resolution_error_returns_false(self, tmp_path):
        """An exception while resolving the output path must return (False, reason)."""
        svc = _make_service(tmp_path)
        output_file = _dummy_file(tmp_path, "resolve_fail.mp4")
        with patch.object(svc._queue, "submit", return_value=None):
            job = svc.start_convert("tid", output_file)
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            job.output_filename = str(output_file)
        with patch("pathlib.Path.resolve", side_effect=RuntimeError("boom")):
            ok, msg = svc.delete_convert_file(job.job_id, allowed_dir=tmp_path)
        assert not ok
        assert "Path resolution error" in msg


class TestCancelConvertBranches:
    def test_cancel_with_cancel_fn_calls_it(self, tmp_path):
        """cancel_convert() must invoke _cancel_fn when set."""
        svc = _make_service(tmp_path)
        cancel_called = []

        def fake_cancel():
            return cancel_called.append(True)

        with patch.object(svc._queue, "submit", return_value=fake_cancel):
            job = svc.start_convert("tid", _dummy_file(tmp_path))

        result = svc.cancel_convert(job.job_id)
        assert result is True
        assert cancel_called

    def test_get_available_encoders_returns_list(self, tmp_path):
        """get_available_encoders() must return a list."""
        svc = _make_service(tmp_path)
        with patch(
            "app.services.remote_convert_service.get_available_encoder_options",
            return_value=[("libx264", "H.264 (CPU)")],
        ):
            result = svc.get_available_encoders()
        assert isinstance(result, list)
        assert result[0][0] == "libx264"
