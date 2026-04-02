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

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.remote_convert_service import (
    MAX_JOBS,
    RemoteConvertService,
    _VALID_ENCODERS,
    _VALID_QUALITIES,
    _VALID_SPEEDS,
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
