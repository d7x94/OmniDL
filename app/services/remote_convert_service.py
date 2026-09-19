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
import re
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.services.ffmpeg_convert_service import (
    _HW_ENCODER_CATALOG,
    SUPPORTED_EXTS,
    ConvertQueue,
    ConvertResult,
    EncodeSettings,
    get_available_codec_options,
    get_available_encoder_options,
)
from app.services.whisper_subtitle_service import (
    DEFAULT_MODEL_KEY,
    TRANSCRIBABLE_EXTS,
    VALID_LANGUAGES,
    WHISPER_MODELS,
    is_transcribable,
    is_whisper_supported,
    model_label,
)
from domain.models.conversion_job import ConversionJob, ConversionStatus

if TYPE_CHECKING:
    from app.event_bus import EventBus
    from app.services.taildrop_service import TaildropService
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── Validation constants ──────────────────────────────────────────────────────

_VALID_ENCODERS: frozenset[str] = frozenset(_HW_ENCODER_CATALOG.keys()) | {"cpu", "auto"}
_VALID_QUALITIES: frozenset[str] = frozenset({"high", "standard", "small", "custom"})
_VALID_SPEEDS: frozenset[str] = frozenset({"quality", "balanced", "fast"})
# Superset of every codec OmniDL knows about.  The *effective* allowlist is
# narrower and computed at request time from the FFmpeg binary that is actually
# installed (see _allowed_codecs) — the shipped Windows build cannot encode AV1
# with SVT-AV1, and accepting a codec the binary cannot produce means handing
# the user a job that is guaranteed to fail.
_KNOWN_CODECS: frozenset[str] = frozenset({"h264", "hevc", "av1"})
_VALID_SUBTITLE_LANGUAGES: frozenset[str] = VALID_LANGUAGES
_VALID_SUBTITLE_MODELS: frozenset[str] = frozenset(m.key for m in WHISPER_MODELS)
# "webm" is deliberately absent: the conversion pipeline produces H.264 + AAC
# and then remuxes with "-c copy", and the WebM muxer accepts only VP8/VP9/AV1
# video with Vorbis/Opus audio.  Every webm request therefore failed at the
# remux step, after the full encode had already run.
_VALID_EXTS: frozenset[str] = frozenset({"mp4", "mkv", "mov", "avi", "mp3"})
# Codecs each container's muxer refuses, verified against FFmpeg 8 with a
# "-c copy" remux: the MOV muxer reports "av1 only supported in MP4 and AVIF".
# Catching the combination here beats running the full (slow) encode and only
# then dying at the remux step — the same reasoning that excluded "webm" above.
_EXT_REJECTED_CODECS: dict[str, frozenset[str]] = {
    "mov": frozenset({"av1"}),
}
# Extensions that may be used as a *source* for an audio-only (mp3) job.
# Video sources come from SUPPORTED_EXTS; these are the audio containers a
# user can reasonably ask to re-encode as MP3 from the file browser.
_AUDIO_SOURCE_EXTS: frozenset[str] = frozenset({"mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "wma"})
_CRF_MIN, _CRF_MAX = 0, 51

# Maximum number of jobs kept in memory (oldest terminal jobs purged first)
MAX_JOBS = 100
# Maximum number of jobs allowed to be PENDING/CONVERTING at once, independent
# of MAX_JOBS.  Without this, a client that fires job-creation requests in a
# tight loop starts one blocked OS thread per call (ConvertQueue.submit()) —
# MAX_JOBS only purges terminal jobs, so an all-active registry never shrinks.
MAX_ACTIVE_JOBS = 20

# Matches an absolute filesystem path (Windows drive-letter or POSIX) so it
# can be redacted down to just the filename before an error message leaves
# the server — raw ConversionError/SubtitleError text can embed the source
# file's or a temp file's full path (e.g. "File không tồn tại: D:\Taive\...",
# or an ffmpeg stderr tail mentioning a path), and job.error_msg /
# job.subtitle_error are returned verbatim to any client holding the shared
# API token.  The POSIX branch excludes a "/" preceded by ":", "/", or a word
# character so a doc URL ffmpeg sometimes prints (e.g. "see
# https://trac.ffmpeg.org/...") is left untouched instead of being mangled.
_PATH_RE = re.compile(r"(?<![:\w/])/[^\s'\"]+|[A-Za-z]:\\[^\s'\"]+")


def _redact_paths(msg: str) -> str:
    """Replace absolute filesystem paths in *msg* with just their basename."""
    return _PATH_RE.sub(lambda m: Path(m.group(0)).name, msg)


def _allowed_codecs() -> frozenset[str]:
    """Return the output codecs this machine's FFmpeg can actually encode.

    Backed by the same cached probe the desktop Convert tab uses, so this costs
    one test-encode per codec every 5 minutes, not one per request.
    """
    try:
        return frozenset(key for key, _ in get_available_codec_options())
    except Exception:
        # Detection is best-effort; never let a probe failure block conversion.
        return frozenset({"h264"})


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
        self._config = config
        self._bus = event_bus
        # Optional TaildropService — when provided, send_converted_file() is
        # called after each successful conversion so the iPhone receives the
        # output automatically (subject to send_mode / taildrop_enabled guards
        # already inside TaildropService).  Defaults to None so existing
        # callers (tests, older startup paths) are not broken.
        self._taildrop = taildrop
        # Dedicated queue — completely separate from desktop ConvertQueue.
        # Seeded from config so the desktop Convert tab's "Parallel" setting
        # and the Remote API agree on how many FFmpeg processes may run.
        try:
            _parallel = int(config.convert_max_concurrent)
        except (AttributeError, TypeError, ValueError):
            _parallel = 2
        self._queue = ConvertQueue(
            max_concurrent=max(1, min(ConvertQueue.MAX_CONCURRENT_LIMIT, _parallel))
        )
        self._jobs: dict[str, ConversionJob] = {}
        self._lock = threading.RLock()

    # ── Public API ────────────────────────────────────────────────────────

    def start_convert(
        self,
        source_task_id: str,
        file_path: Path,
        encoder_key: str = "auto",
        quality: str = "standard",
        speed_preset: str = "balanced",
        custom_crf: int = 23,
        target_ext: str = "mp4",
        output_codec: str = "h264",
        generate_subtitles: bool = False,
        subtitle_language: str = "auto",
        subtitle_model: str = DEFAULT_MODEL_KEY,
        compute_vmaf: bool = False,
        subtitles_only: bool = False,
    ) -> ConversionJob:
        """
        Validate settings, create a ConversionJob, and dispatch to ConvertQueue.

        Returns the ConversionJob immediately (non-blocking).
        Raises ValueError for invalid parameters — caller converts to HTTP 422.

        Security:
        • encoder_key validated against _VALID_ENCODERS allowlist.
        • quality validated against _VALID_QUALITIES allowlist.
        • speed_preset validated against _VALID_SPEEDS allowlist.
        • target_ext validated against _VALID_EXTS allowlist.
        • custom_crf clamped to [0, 51] (FFmpeg CRF valid range).
        • file_path must already have passed the path traversal guard in
          the endpoint — this service trusts the resolved Path object.
        """
        # ── Parameter validation ──────────────────────────────────────────
        if encoder_key not in _VALID_ENCODERS:
            raise ValueError(
                f"encoder_key '{encoder_key}' is not allowed. Valid values: {sorted(_VALID_ENCODERS)}"
            )
        if quality not in _VALID_QUALITIES:
            raise ValueError(f"quality '{quality}' is not allowed. Valid values: {sorted(_VALID_QUALITIES)}")
        if speed_preset not in _VALID_SPEEDS:
            raise ValueError(
                f"speed_preset '{speed_preset}' is not allowed. Valid values: {sorted(_VALID_SPEEDS)}"
            )
        custom_crf = max(_CRF_MIN, min(_CRF_MAX, int(custom_crf)))
        allowed_codecs = _allowed_codecs()
        if output_codec not in allowed_codecs:
            if output_codec in _KNOWN_CODECS:
                raise ValueError(
                    f"output_codec '{output_codec}' is not supported by the FFmpeg build "
                    f"installed on this machine. Available: {sorted(allowed_codecs)}"
                )
            raise ValueError(
                f"output_codec '{output_codec}' is not allowed. Valid values: {sorted(allowed_codecs)}"
            )
        if subtitle_language not in _VALID_SUBTITLE_LANGUAGES:
            raise ValueError(
                f"subtitle_language '{subtitle_language}' is not allowed. "
                f"Valid values: {sorted(_VALID_SUBTITLE_LANGUAGES)}"
            )
        if subtitle_model not in _VALID_SUBTITLE_MODELS:
            raise ValueError(
                f"subtitle_model '{subtitle_model}' is not allowed. "
                f"Valid values: {sorted(_VALID_SUBTITLE_MODELS)}"
            )
        if subtitles_only and not generate_subtitles:
            raise ValueError("subtitles_only requires generate_subtitles=True")
        if generate_subtitles and not is_whisper_supported():
            raise ValueError("Subtitle generation is unavailable: this FFmpeg build has no whisper filter.")
        if target_ext not in _VALID_EXTS:
            raise ValueError(f"target_ext '{target_ext}' is not allowed. Valid values: {sorted(_VALID_EXTS)}")
        if output_codec in _EXT_REJECTED_CODECS.get(target_ext, frozenset()):
            raise ValueError(
                f"output_codec '{output_codec}' cannot be stored in a .{target_ext} file. "
                f"Use target_ext 'mp4' or 'mkv' instead."
            )
        if not subtitles_only:
            _src_ext = file_path.suffix.lower().lstrip(".")
            _allowed_src = SUPPORTED_EXTS | _AUDIO_SOURCE_EXTS if target_ext == "mp3" else SUPPORTED_EXTS
            if _src_ext not in _allowed_src:
                # Without this an image, archive or subtitle file picked from
                # the file browser queued a job that always died on a raw
                # FFmpeg error.  start_subtitles() already guards its own path
                # with is_transcribable(); this is the encode-path equivalent.
                raise ValueError(
                    f"'{file_path.name}' is not a convertible media file. "
                    f"Allowed extensions: {sorted(_allowed_src)}"
                )
        if compute_vmaf and target_ext == "mp3":
            # VMAF compares two video streams; an MP3 output has none.  Say so
            # instead of accepting the flag and quietly dropping it.
            raise ValueError("compute_vmaf is not available for target_ext 'mp3' (audio-only output).")

        # Resolve "auto" → best available GPU encoder, fallback to CPU
        if encoder_key == "auto":
            opts = get_available_encoder_options()
            gpu_keys = [k for k, _ in opts if k != "cpu"]
            encoder_key = gpu_keys[0] if gpu_keys else "cpu"

        # ── Create job ────────────────────────────────────────────────────
        job = ConversionJob(
            source_task_id=source_task_id,
            source_filename=str(file_path),
            encoder_key=encoder_key,
            quality=quality,
            speed_preset=speed_preset,
            custom_crf=custom_crf,
            output_codec=output_codec,
            generate_subtitles=generate_subtitles,
            subtitle_language=subtitle_language,
            compute_vmaf=compute_vmaf,
            subtitles_only=subtitles_only,
            status=ConversionStatus.PENDING,
        )

        with self._lock:
            self._purge_old_jobs()
            active = sum(
                1
                for j in self._jobs.values()
                if j.status in (ConversionStatus.PENDING, ConversionStatus.CONVERTING)
            )
            if active >= MAX_ACTIVE_JOBS:
                raise ValueError(
                    f"Too many convert/subtitle jobs in progress (max {MAX_ACTIVE_JOBS}). "
                    "Wait for one to finish before starting another."
                )
            while job.job_id in self._jobs:
                job.job_id = uuid.uuid4().hex[:8]
            self._jobs[job.job_id] = job

        # ── Dispatch ──────────────────────────────────────────────────────
        encode_settings = EncodeSettings(
            encoder_key=encoder_key,
            quality=quality,
            speed_preset=speed_preset,
            custom_quality=custom_crf,
            output_codec=output_codec,
            generate_subtitles=generate_subtitles,
            subtitle_language=subtitle_language,
            subtitle_model=subtitle_model,
            compute_vmaf=compute_vmaf,
            subtitles_only=subtitles_only,
        )

        def _on_start() -> None:
            with job._lock:
                job.status = ConversionStatus.CONVERTING
                job.progress = 0.0
            self._bus.publish_convert_started(job=job)
            logger.info("RemoteConvert: started job %s (%s)", job.job_id, file_path.name)

        def _on_progress(pct: float) -> None:
            with job._lock:
                job.progress = pct
            self._bus.publish_convert_progress(job=job)

        def _on_result(result: ConvertResult) -> None:
            with job._lock:
                job.subtitle_filename = str(result.subtitle_path) if result.subtitle_path else ""
                job.subtitle_error = _redact_paths(result.subtitle_error) if result.subtitle_error else ""
                job.vmaf_score = result.vmaf_score
                # The actual encoder used, which may differ from the requested
                # one after a GPU→CPU fallback inside _try_encode_with_fallback.
                # Empty for jobs that never touched the video encode path
                # (subtitles-only) — leave job.encoder_key as set at creation.
                if result.encoder_key:
                    job.encoder_key = result.encoder_key

        def _on_done(output_path: Path) -> None:
            with job._lock:
                job.status = ConversionStatus.COMPLETED
                job.progress = 100.0
                # A subtitles-only job produces no video; leaving
                # output_filename empty keeps the client from offering a
                # "download the converted video" button for a .srt.
                job.output_filename = "" if subtitles_only else str(output_path)
                job.finished_at = time.time()
            self._bus.publish_convert_completed(job=job)
            logger.info(
                "RemoteConvert: completed job %s → %s",
                job.job_id,
                output_path.name,
            )
            # ── Auto-send converted file to iPhone via Taildrop ─────────────────
            # send_converted_file() is a no-op when taildrop_enabled=False,
            # send_mode="ask", or target_node is empty — all guards already
            # live inside TaildropService; safe to call unconditionally.
            # Never raises — any failure is logged by TaildropService.
            if self._taildrop is not None and not subtitles_only:
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
                    job.status = ConversionStatus.FAILED
                    # Log the raw detail (may contain an absolute server
                    # path) server-side only; the client-facing field is
                    # redacted down to filenames.
                    job.error_msg = _redact_paths(err)
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
            source=file_path,
            quality=quality,  # type: ignore[arg-type]
            output_dir=file_path.parent,
            on_progress=_on_progress,
            on_done=_on_done,
            on_error=_on_error,
            on_start=_on_start,
            encode_settings=encode_settings,
            target_ext=target_ext,
            on_result=_on_result,
        )
        # Store the ConvertQueue cancel callable so cancel_convert() can
        # call it.  We also wire the job's own cancel_event to it so the
        # watchdog in FfmpegConvertService sees the signal immediately.
        job._cancel_fn = _cancel_fn  # type: ignore[attr-defined]
        # The job is reachable through get_job() from the moment it lands in
        # _jobs, which is before submit() returns.  A cancel arriving in that
        # window found _cancel_fn still None, so it set only the job's own flag
        # and the ConvertQueue worker never saw it: FFmpeg ran to completion and
        # the job then reported CANCELLED with a finished file on disk.
        if job.is_cancel_requested:
            _cancel_fn()

        return job

    def start_convert_from_path(
        self,
        file_path: Path,
        encoder_key: str = "auto",
        quality: str = "standard",
        speed_preset: str = "balanced",
        custom_crf: int = 23,
        target_ext: str = "mp4",
        output_codec: str = "h264",
        generate_subtitles: bool = False,
        subtitle_language: str = "auto",
        subtitle_model: str = DEFAULT_MODEL_KEY,
        compute_vmaf: bool = False,
    ) -> ConversionJob:
        """
        Start a conversion job on an arbitrary local file.

        Identical to start_convert() but uses source_task_id="" to signal
        that this job is not tied to any download task.  Called by the
        POST /api/files/convert endpoint after path-traversal validation.
        """
        return self.start_convert(
            source_task_id="",
            file_path=file_path,
            encoder_key=encoder_key,
            quality=quality,
            speed_preset=speed_preset,
            custom_crf=custom_crf,
            target_ext=target_ext,
            output_codec=output_codec,
            generate_subtitles=generate_subtitles,
            subtitle_language=subtitle_language,
            subtitle_model=subtitle_model,
            compute_vmaf=compute_vmaf,
        )

    def start_subtitles(
        self,
        file_path: Path,
        source_task_id: str = "",
        subtitle_language: str = "auto",
        subtitle_model: str = DEFAULT_MODEL_KEY,
    ) -> ConversionJob:
        """
        Start a subtitle-only job: transcribe *file_path* to a sidecar .srt.

        No video is re-encoded, so encoder / quality / codec are irrelevant and
        the defaults are used purely to satisfy start_convert()'s validation.
        The finished .srt is fetched with
        ``GET /api/convert/{job_id}/file?kind=srt``.

        Raises ValueError (→ HTTP 422) when *file_path* is not a media file:
        without this, asking for subtitles on a .jpg from a gallery download
        queued a job that always died on a cryptic FFmpeg error.
        """
        if not is_transcribable(file_path):
            raise ValueError(
                f"'{file_path.name}' is not an audio/video file. "
                f"Allowed extensions: {sorted(TRANSCRIBABLE_EXTS)}"
            )
        return self.start_convert(
            source_task_id=source_task_id,
            file_path=file_path,
            generate_subtitles=True,
            subtitle_language=subtitle_language,
            subtitle_model=subtitle_model,
            subtitles_only=True,
        )

    def delete_convert_file(self, job_id: str, allowed_dir: Path) -> tuple[bool, str]:
        """
        Delete the converted output file from disk for a COMPLETED job.

        Returns (True, "") on success, or (False, reason) on any failure.

        Security constraints
        ────────────────────
        • Path is resolved against *allowed_dir* (the configured download_dir)
          before deletion — prevents path-traversal (CWE-22).
        • Deletion uses Path.unlink() — no subprocess, no shell=True,
          no string interpolation (CWE-78).
        • Only COMPLETED jobs with a non-empty output_filename are accepted;
          all other states are rejected with an explicit reason string so the
          API layer can return a meaningful HTTP error.
        • is_file() is checked immediately before unlinking to handle the
          race condition where the file was already removed externally.

        State update
        ────────────
        Sets job.output_deleted = True after successful deletion so the SSE
        snapshot and the ConvertJobResponse immediately reflect the new state
        without requiring the client to poll the filesystem.
        """
        job = self.get_job(job_id)
        if job is None:
            return False, "Convert job not found"
        if job.status != ConversionStatus.COMPLETED:
            return False, f"Job is not COMPLETED (current status: {job.status})"
        if not job.output_filename:
            return False, "No output file recorded for this job"

        # ── Path-traversal guard ──────────────────────────────────────────
        try:
            out_path = Path(job.output_filename).resolve()
            allowed = allowed_dir.resolve()
        except Exception as exc:
            return False, f"Path resolution error: {exc}"

        if not out_path.is_relative_to(allowed):
            # Should never happen under normal operation — logged at WARNING
            # because it indicates a misconfiguration or tampering attempt.
            logger.warning(
                "delete_convert_file: SECURITY — '%s' is outside allowed_dir '%s'",
                out_path,
                allowed,
            )
            return False, "Output file is outside the allowed download directory"

        # ── Delete ────────────────────────────────────────────────────────
        # The is_file() check and the unlink() are both done under job._lock
        # so two concurrent DELETE requests for the same job can't race each
        # other between the check and the unlink; FileNotFoundError (the
        # loser of that race, or the file already being gone) is treated as
        # success rather than surfaced as a 500 — a double-delete should be
        # idempotent, not an error.
        with job._lock:
            if job.output_deleted or not out_path.is_file():
                job.output_deleted = True
                return True, ""
            try:
                out_path.unlink()
            except FileNotFoundError:
                job.output_deleted = True
                return True, ""
            except PermissionError as exc:
                # Windows refuses to unlink while another process still holds
                # the file open — most often this app's own Taildrop transfer,
                # which is queued the instant the conversion completes and can
                # take a minute for a 400 MB recording. The lock clears on its
                # own, so this is a retry-shortly condition, not an error.
                logger.warning(
                    "delete_convert_file: '%s' is locked by another process: %s",
                    out_path.name,
                    exc,
                )
                return False, f"File is in use by another process: {out_path.name}"
            except OSError as exc:
                logger.error("delete_convert_file: failed to delete '%s': %s", out_path, exc)
                return False, str(exc)
            job.output_deleted = True

        logger.info(
            "RemoteConvert: deleted output file '%s' for job %s",
            out_path.name,
            job_id,
        )
        return True, ""

    def cancel_convert(self, job_id: str) -> bool:
        """
        Cancel an in-progress or pending conversion job.

        Returns True if the job was found and the cancel signal was sent,
        False if the job does not exist or is already terminal.
        """
        job = self.get_job(job_id)
        if job is None:
            return False
        if job.status in (ConversionStatus.COMPLETED, ConversionStatus.FAILED, ConversionStatus.CANCELLED):
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

    @property
    def max_concurrent(self) -> int:
        """How many conversions this service runs in parallel."""
        return self._queue.max_concurrent

    def set_max_concurrent(self, value: int) -> int:
        """Change the parallel limit and persist it; returns the applied value."""
        applied = self._queue.set_max_concurrent(value)
        self._config.set("convert_max_concurrent", applied)
        return applied

    def get_available_encoders(self) -> list[tuple[str, str]]:
        """
        Return (key, label) pairs for encoders available on this machine.
        Result is cached inside detect_available_encoders() for 5 min.
        """
        return get_available_encoder_options()

    def get_available_codecs(self) -> list[tuple[str, str]]:
        """
        Return (key, label) pairs for output codecs this FFmpeg build supports.
        Codecs the bundled binary cannot encode are omitted so clients never
        offer an option that is guaranteed to fail.
        """
        return get_available_codec_options()

    def supports_subtitles(self) -> bool:
        """Return True when this FFmpeg build can transcribe speech to SRT."""
        return is_whisper_supported()

    def get_subtitle_languages(self) -> list[str]:
        """Return the language codes accepted by generate_subtitles requests."""
        return sorted(_VALID_SUBTITLE_LANGUAGES)

    def get_subtitle_models(self) -> list[tuple[str, str, int]]:
        """Return (key, label, size_mb) for every whisper model, smallest first."""
        return [(m.key, model_label(m), m.size_mb) for m in WHISPER_MODELS]

    # ── Internal ──────────────────────────────────────────────────────────

    def _purge_old_jobs(self) -> None:
        """
        Remove oldest terminal jobs when registry exceeds MAX_JOBS.
        Must be called with self._lock held.
        """
        terminal = [
            j
            for j in self._jobs.values()
            if j.status in (ConversionStatus.COMPLETED, ConversionStatus.FAILED, ConversionStatus.CANCELLED)
        ]
        if len(self._jobs) < MAX_JOBS:
            return
        # Sort by finished_at ascending, remove oldest first
        terminal.sort(key=lambda j: j.finished_at)
        to_remove = terminal[: max(1, len(terminal) // 2)]
        if not to_remove:
            logger.warning(
                "_purge_old_jobs: registry at %d jobs but all are active - cannot purge",
                len(self._jobs),
            )
            return
        for j in to_remove:
            self._jobs.pop(j.job_id, None)
