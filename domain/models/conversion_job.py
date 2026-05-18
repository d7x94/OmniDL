"""
domain/models/conversion_job.py
Domain entity for a remote-triggered FFmpeg conversion job.

Intentionally kept separate from DownloadTask — a conversion job has
a different lifecycle and does not belong in the download pipeline.

Thread-safety
─────────────
All mutable fields are protected by a single RLock.  Callers must use
``with job._lock:`` for multi-field reads/writes (e.g. status + progress).
The snapshot() method provides a safe atomic read of all display fields.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


class ConversionStatus:
    PENDING    = "PENDING"     # queued, waiting for a ConvertQueue slot
    CONVERTING = "CONVERTING"  # FFmpeg is running
    COMPLETED  = "COMPLETED"   # FFmpeg finished successfully
    FAILED     = "FAILED"      # FFmpeg or pre-flight error
    CANCELLED  = "CANCELLED"   # cancelled by user via Remote API


@dataclass
class ConversionJob:
    """
    Tracks the lifecycle of one remote-triggered conversion job.

    Fields
    ──────
    job_id          : stable identifier (8-char hex UUID fragment)
    source_task_id  : the DownloadTask.id whose file is being converted
    source_filename : absolute path of the input file at job creation time
    encoder_key     : "cpu" | "nvenc" | "qsv" | "amf" | "videotoolbox"
    quality         : "high" | "standard" | "small" | "custom"
    speed_preset    : "quality" | "balanced" | "fast"
    custom_crf      : CRF value used when quality=="custom" (0–51)
    status          : ConversionStatus string
    progress        : 0.0 – 100.0
    output_filename : absolute path of the converted MP4 (empty until done)
    error_msg       : human-readable error (empty unless FAILED)
    created_at      : Unix timestamp
    finished_at     : Unix timestamp (0.0 until terminal state)
    """

    # ── Identity ─────────────────────────────────────────────────────────
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_task_id: str = ""
    source_filename: str = ""

    # ── Encode settings (captured at submission time) ─────────────────────
    encoder_key:  str = "cpu"
    quality:      str = "standard"
    speed_preset: str = "balanced"
    custom_crf:   int = 23
    output_codec: str = "h264"

    # ── Mutable state ─────────────────────────────────────────────────────
    status:          str   = ConversionStatus.PENDING
    progress:        float = 0.0
    output_filename: str   = ""
    error_msg:       str   = ""
    # Set to True after the converted output file is deleted from disk.
    # Exposed in snapshot() so SSE clients and the API response can update
    # UI state (hide "delete" button, show "file removed" label) without
    # polling the filesystem.
    output_deleted:  bool  = False

    # ── Timing ────────────────────────────────────────────────────────────
    created_at:  float = field(default_factory=time.time)
    finished_at: float = 0.0

    # ── Internal cancel primitive (not serialised) ────────────────────────
    _cancel_event: threading.Event = field(
        default_factory=threading.Event, compare=False, repr=False
    )
    _lock: threading.RLock = field(
        default_factory=threading.RLock, compare=False, repr=False
    )

    # ── Control ───────────────────────────────────────────────────────────

    def request_cancel(self) -> None:
        """Signal the running FFmpeg worker to stop."""
        with self._lock:
            if self.status in (ConversionStatus.PENDING,
                               ConversionStatus.CONVERTING):
                self._cancel_event.set()

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    # ── Thread-safe snapshot ──────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Atomic read of all display fields for SSE serialisation."""
        with self._lock:
            return {
                "job_id":          self.job_id,
                "source_task_id":  self.source_task_id,
                "encoder_key":     self.encoder_key,
                "quality":         self.quality,
                "speed_preset":    self.speed_preset,
                "custom_crf":      self.custom_crf,
                "output_codec":    self.output_codec,
                "status":          self.status,
                "progress":        self.progress,
                "output_filename": self.output_filename,
                "error_msg":       self.error_msg,
                "created_at":      self.created_at,
                "finished_at":     self.finished_at,
                "output_deleted":  self.output_deleted,
            }
