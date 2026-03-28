"""
app/services/remote_convert_service.py
On-demand conversion service for the Remote API (iPhone control).

Design constraints
──────────────────
• Zero impact on existing download pipeline or desktop convert tab.
  This service is completely independent — it does NOT touch DownloadService,
  DownloadTask state, or the desktop ConvertQueue.
• Uses FfmpegConvertService and ConvertQueue internally (existing code).
• Publishes EventBus CONVERT_* events so SSE clients receive real-time
  progress updates without polling.
• All user-supplied encode parameters are validated before reaching FFmpeg
  (encoder_key allowlist, quality allowlist, CRF clamp 0–51).
• Path traversal guard: source file must reside inside download_dir.
• Thread-safe job registry protected by a single RLock.
• At most MAX_JOBS jobs are retained in memory (oldest completed purged).

Typical lifecycle
─────────────────
  POST /api/queue/{task_id}/convert
      → RemoteConvertService.start_convert()   → job_id returned
      → ConvertQueue.submit()                  → FFmpeg runs in daemon thread
      → EventBus.publish_convert_progress()   → SSE pushes to iPhone
      → EventBus.publish_convert_completed()  → iPhone shows ✅

  POST /api/convert/{job_id}/cancel
      → RemoteConvertService.cancel_convert()  → cancel_event.set()
      → FFmpeg watchdog kills process          → ConversionCancelledError
      → EventBus.publish_convert_cancelled()  → iPhone shows ⊘
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.services.ffmpeg_convert_service import (
    ConvertQueue,
    EncodeSettings,
    detect_available_encoders,
    get_available_encoder_options,
    _HW_ENCODER_CATALOG,
)
from domain.models.conversion_job import ConversionJob, ConversionStatus

if TYPE_CHECKING:
    from app.event_bus import EventBus
    from app.services.taildrop_service import TaildropService
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── Validation constants ──────────────────────────────────────────────────────

_VALID_ENCODERS: frozenset[str] = frozenset(_HW_ENCODER_CATALOG.keys()) | {"cpu"}
_VALID_QUALITIES: frozenset[str] = frozenset({"high", "standard", "small", "custom"})
_VALID_SPEEDS: frozenset[str] = frozenset({"quality", "balanced", "fast"})
_CRF_MIN, _CRF_MAX = 0, 51

# Maximum number of jobs kept in memory (oldest terminal jobs purged first)
MAX_JOBS = 100


class RemoteConvertService:
    """
    Manages on-demand conversion jobs triggered from the Remote API.

    Thread-safety
    ─────────────
    The job registry (_jobs dict) is protected by _lock.
    ConversionJob internal state uses its own per-job RLock.
    ConvertQueue is thread-safe by design (semaphore + daemon threads).
    """

    def __init__(
        self,
        config: "ConfigManager",
        event_bus: "EventBus",
        taildrop: "Optional[TaildropService]" = None,
    ) -> None:
        self._config   = config
        self._bus      = event_bus
        # Optional TaildropService — when provided, send_converted_file() is
        # called after each successful conversion so the iPhone receives the
        # output automatically (subject to send_mode / taildrop_enabled guards
        # already inside TaildropService).  Defaults to None so existing
        # callers (tests, older startup paths) are not broken.
        self._taildrop = taildrop
        # Dedicated queue — completely separate from desktop ConvertQueue.
        # max_workers=2: allows two simultaneous remote conversions without
        # overwhelming CPU on a typical laptop.
        self._queue    = ConvertQueue(max_concurrent=2)
        self._jobs: dict[str, ConversionJob] = {}
        self._lock     = threading.RLock()

    # ── Public API ────────────────────────────────────────────────────────

    def start_convert(
        self,
        source_task_id: str,
        file_path: Path,
        encoder_key:  str = "cpu",
        quality:      str = "standard",
        speed_preset: str = "balanced",
        custom_crf:   int = 23,
    ) -> ConversionJob:
        """
        Validate settings, create a ConversionJob, and dispatch to ConvertQueue.

        Returns the ConversionJob immediately (non-blocking).
        Raises ValueError for invalid parameters — caller converts to HTTP 422.

        Security:
        • encoder_key validated against _VALID_ENCODERS allowlist.
        • quality validated against _VALID_QUALITIES allowlist.
        • speed_preset validated against _VALID_SPEEDS allowlist.
        • custom_crf clamped to [0, 51] (FFmpeg CRF valid range).
        • file_path must already have passed the path traversal guard in
          the endpoint — this service trusts the resolved Path object.
        """
        # ── Parameter validation ──────────────────────────────────────────
        if encoder_key not in _VALID_ENCODERS:
            raise ValueError(
                f"encoder_key '{encoder_key}' is not allowed. "
                f"Valid values: {sorted(_VALID_ENCODERS)}"
            )
        if quality not in _VALID_QUALITIES:
            raise ValueError(
                f"quality '{quality}' is not allowed. "
                f"Valid values: {sorted(_VALID_QUALITIES)}"
            )
        if speed_preset not in _VALID_SPEEDS:
            raise ValueError(
                f"speed_preset '{speed_preset}' is not allowed. "
                f"Valid values: {sorted(_VALID_SPEEDS)}"
            )
        custom_crf = max(_CRF_MIN, min(_CRF_MAX, int(custom_crf)))

        # ── Create job ────────────────────────────────────────────────────
        job = ConversionJob(
            source_task_id  = source_task_id,
            source_filename = str(file_path),
            encoder_key     = encoder_key,
            quality         = quality,
            speed_preset    = speed_preset,
            custom_crf      = custom_crf,
            status          = ConversionStatus.PENDING,
        )

        with self._lock:
            self._purge_old_jobs()
            self._jobs[job.job_id] = job

        # ── Dispatch ──────────────────────────────────────────────────────
        encode_settings = EncodeSettings(
            encoder_key   = encoder_key,
            quality       = quality,
            speed_preset  = speed_preset,
            custom_quality= custom_crf,
        )

        def _on_start() -> None:
            with job._lock:
                job.status   = ConversionStatus.CONVERTING
                job.progress = 0.0
            self._bus.publish_convert_started(job=job)
            logger.info("RemoteConvert: started job %s (%s)", job.job_id, file_path.name)

        def _on_progress(pct: float) -> None:
            with job._lock:
                job.progress = pct
            self._bus.publish_convert_progress(job=job)

        def _on_done(output_path: Path) -> None:
            with job._lock:
                job.status          = ConversionStatus.COMPLETED
                job.progress        = 100.0
                job.output_filename = str(output_path)
                job.finished_at     = time.time()
            self._bus.publish_convert_completed(job=job)
            logger.info(
                "RemoteConvert: completed job %s → %s",
                job.job_id, output_path.name,
            )
            # ── Auto-send converted file to iPhone via Taildrop ─────────────────
            # send_converted_file() is a no-op when taildrop_enabled=False,
            # send_mode="ask", or target_node is empty — all guards already
            # live inside TaildropService; safe to call unconditionally.
            # Never raises — any failure is logged by TaildropService.
            if self._taildrop is not None:
                try:
                    self._taildrop.send_converted_file(output_path)
                except Exception:
                    logger.debug(
                        "RemoteConvert: Taildrop hook raised unexpectedly",
                        exc_info=True,
                    )

        def _on_error(err: str) -> None:
            cancelled = job.is_cancel_requested
            with job._lock:
                if cancelled:
                    job.status = ConversionStatus.CANCELLED
                else:
                    job.status    = ConversionStatus.FAILED
                    job.error_msg = err
                job.finished_at = time.time()
            if cancelled:
                self._bus.publish_convert_cancelled(job=job)
                logger.info("RemoteConvert: cancelled job %s", job.job_id)
            else:
                self._bus.publish_convert_failed(job=job)
                logger.warning("RemoteConvert: failed job %s: %s", job.job_id, err)

        # ConvertQueue.submit() stores the cancel callable returned, wiring
        # it to the job's own cancel_event so _on_error can detect cancellation.
        _cancel_fn = self._queue.submit(
            source          = file_path,
            quality         = quality,        # type: ignore[arg-type]
            output_dir      = file_path.parent,
            on_progress     = _on_progress,
            on_done         = _on_done,
            on_error        = _on_error,
            on_start        = _on_start,
            encode_settings = encode_settings,
        )
        # Store the ConvertQueue cancel callable so cancel_convert() can
        # call it.  We also wire the job's own cancel_event to it so the
        # watchdog in FfmpegConvertService sees the signal immediately.
        job._cancel_fn = _cancel_fn  # type: ignore[attr-defined]

        return job

    def cancel_convert(self, job_id: str) -> bool:
        """
        Cancel an in-progress or pending conversion job.

        Returns True if the job was found and the cancel signal was sent,
        False if the job does not exist or is already terminal.
        """
        job = self.get_job(job_id)
        if job is None:
            return False
        if job.status in (ConversionStatus.COMPLETED,
                          ConversionStatus.FAILED,
                          ConversionStatus.CANCELLED):
            return False
        job.request_cancel()
        cancel_fn = getattr(job, "_cancel_fn", None)
        if cancel_fn is not None:
            cancel_fn()
        return True

    def get_job(self, job_id: str) -> Optional[ConversionJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[ConversionJob]:
        with self._lock:
            return list(self._jobs.values())

    def get_available_encoders(self) -> list[tuple[str, str]]:
        """
        Return (key, label) pairs for encoders available on this machine.
        Result is cached inside detect_available_encoders() for 5 min.
        """
        return get_available_encoder_options()

    # ── Internal ──────────────────────────────────────────────────────────

    def _purge_old_jobs(self) -> None:
        """
        Remove oldest terminal jobs when registry exceeds MAX_JOBS.
        Must be called with self._lock held.
        """
        terminal = [
            j for j in self._jobs.values()
            if j.status in (ConversionStatus.COMPLETED,
                            ConversionStatus.FAILED,
                            ConversionStatus.CANCELLED)
        ]
        if len(self._jobs) < MAX_JOBS:
            return
        # Sort by finished_at ascending, remove oldest first
        terminal.sort(key=lambda j: j.finished_at)
        for j in terminal[:max(1, len(terminal) // 2)]:
            self._jobs.pop(j.job_id, None)
