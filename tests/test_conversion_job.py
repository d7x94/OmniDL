"""
tests/test_conversion_job.py
Unit tests for domain/models/conversion_job.py — ConversionJob + ConversionStatus.

Coverage targets:
- ConversionStatus string constants
- ConversionJob default field values and field_factory uniqueness
- request_cancel() / is_cancel_requested
- snapshot() returns all expected keys with correct types
- output_deleted flag lifecycle
- Thread-safety: concurrent snapshot() calls do not raise
"""
from __future__ import annotations

import threading
import time

from domain.models.conversion_job import ConversionJob, ConversionStatus

# ===========================================================================
# ConversionStatus
# ===========================================================================

class TestConversionStatus:
    """Status constants must be strings and form a closed set."""

    def test_all_statuses_are_strings(self):
        for attr in ("PENDING", "CONVERTING", "COMPLETED", "FAILED", "CANCELLED"):
            val = getattr(ConversionStatus, attr)
            assert isinstance(val, str), f"ConversionStatus.{attr} must be str"

    def test_status_values_are_distinct(self):
        statuses = [
            ConversionStatus.PENDING,
            ConversionStatus.CONVERTING,
            ConversionStatus.COMPLETED,
            ConversionStatus.FAILED,
            ConversionStatus.CANCELLED,
        ]
        assert len(statuses) == len(set(statuses)), "All status values must be unique"

    def test_pending_is_default_status_string(self):
        job = ConversionJob()
        assert job.status == ConversionStatus.PENDING


# ===========================================================================
# Default field values
# ===========================================================================

class TestConversionJobDefaults:
    """Verify factory defaults so API serialisation is predictable."""

    def test_job_id_is_8_hex_chars(self):
        job = ConversionJob()
        assert len(job.job_id) == 8
        int(job.job_id, 16)  # raises ValueError if not hex

    def test_each_job_gets_unique_id(self):
        ids = {ConversionJob().job_id for _ in range(50)}
        assert len(ids) == 50, "All job_ids must be unique across instances"

    def test_numeric_defaults(self):
        job = ConversionJob()
        assert job.progress == 0.0
        assert job.custom_crf == 23
        assert job.finished_at == 0.0

    def test_string_defaults_are_empty_or_expected(self):
        job = ConversionJob()
        assert job.source_task_id == ""
        assert job.source_filename == ""
        assert job.output_filename == ""
        assert job.error_msg == ""
        assert job.encoder_key == "cpu"
        assert job.quality == "standard"
        assert job.speed_preset == "balanced"

    def test_output_deleted_default_is_false(self):
        job = ConversionJob()
        assert job.output_deleted is False

    def test_created_at_is_recent_unix_timestamp(self):
        before = time.time()
        job = ConversionJob()
        after = time.time()
        assert before <= job.created_at <= after, (
            "created_at must be a Unix timestamp set at construction time"
        )


# ===========================================================================
# request_cancel / is_cancel_requested
# ===========================================================================

class TestCancelMechanism:
    """Cancel flag semantics match the description in the docstring."""

    def test_not_cancelled_by_default(self):
        job = ConversionJob()
        assert not job.is_cancel_requested

    def test_request_cancel_pending(self):
        job = ConversionJob()
        job.status = ConversionStatus.PENDING
        job.request_cancel()
        assert job.is_cancel_requested

    def test_request_cancel_converting(self):
        job = ConversionJob()
        job.status = ConversionStatus.CONVERTING
        job.request_cancel()
        assert job.is_cancel_requested

    def test_request_cancel_ignored_when_completed(self):
        """request_cancel() on a terminal job must not set the flag."""
        job = ConversionJob()
        job.status = ConversionStatus.COMPLETED
        job.request_cancel()
        assert not job.is_cancel_requested

    def test_request_cancel_ignored_when_failed(self):
        job = ConversionJob()
        job.status = ConversionStatus.FAILED
        job.request_cancel()
        assert not job.is_cancel_requested

    def test_request_cancel_ignored_when_cancelled(self):
        job = ConversionJob()
        job.status = ConversionStatus.CANCELLED
        job.request_cancel()
        assert not job.is_cancel_requested

    def test_cancel_is_idempotent(self):
        """Calling request_cancel() twice must not raise."""
        job = ConversionJob()
        job.status = ConversionStatus.PENDING
        job.request_cancel()
        job.request_cancel()
        assert job.is_cancel_requested


# ===========================================================================
# snapshot()
# ===========================================================================

class TestSnapshot:
    """snapshot() must return a complete, consistent dict safe for serialisation."""

    EXPECTED_KEYS = {
        "job_id", "source_task_id", "encoder_key", "quality", "speed_preset",
        "custom_crf", "output_codec", "status", "progress", "output_filename",
        "error_msg", "created_at", "finished_at", "output_deleted",
    }

    def test_snapshot_contains_all_keys(self):
        job = ConversionJob()
        snap = job.snapshot()
        assert set(snap.keys()) == self.EXPECTED_KEYS

    def test_snapshot_values_match_fields(self):
        job = ConversionJob(
            source_task_id="abc123",
            encoder_key="nvenc",
            quality="high",
        )
        snap = job.snapshot()
        assert snap["source_task_id"] == "abc123"
        assert snap["encoder_key"] == "nvenc"
        assert snap["quality"] == "high"
        assert snap["status"] == ConversionStatus.PENDING
        assert snap["progress"] == 0.0
        assert snap["output_deleted"] is False

    def test_snapshot_reflects_mutation(self):
        job = ConversionJob()
        with job._lock:
            job.status = ConversionStatus.CONVERTING
            job.progress = 42.5
        snap = job.snapshot()
        assert snap["status"] == ConversionStatus.CONVERTING
        assert snap["progress"] == 42.5

    def test_output_deleted_in_snapshot(self):
        job = ConversionJob()
        with job._lock:
            job.status = ConversionStatus.COMPLETED
            job.output_deleted = True
        snap = job.snapshot()
        assert snap["output_deleted"] is True

    def test_snapshot_is_dict_not_job(self):
        job = ConversionJob()
        snap = job.snapshot()
        assert isinstance(snap, dict)
        assert not isinstance(snap, ConversionJob)


# ===========================================================================
# Thread-safety
# ===========================================================================

class TestThreadSafety:
    """Concurrent reads and writes must not raise or corrupt data."""

    def test_concurrent_snapshots_do_not_raise(self):
        job = ConversionJob()
        errors: list[Exception] = []

        def reader():
            for _ in range(200):
                try:
                    job.snapshot()
                except Exception as exc:
                    errors.append(exc)

        def writer():
            for pct in range(101):
                with job._lock:
                    job.progress = float(pct)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads.append(threading.Thread(target=writer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"snapshot() raised in background thread: {errors}"

    def test_concurrent_cancel_and_snapshot(self):
        """request_cancel() from one thread while snapshot() runs on another."""
        job = ConversionJob()
        job.status = ConversionStatus.PENDING
        errors: list[Exception] = []

        def canceller():
            for _ in range(100):
                try:
                    job.request_cancel()
                except Exception as exc:
                    errors.append(exc)

        def reader():
            for _ in range(100):
                try:
                    job.snapshot()
                except Exception as exc:
                    errors.append(exc)

        t1 = threading.Thread(target=canceller)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not errors
