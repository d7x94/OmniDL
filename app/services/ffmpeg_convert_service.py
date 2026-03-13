"""
app/services/ffmpeg_convert_service.py
Background FFmpeg video conversion — MP4 / H.264 / AAC / yuv420p.

iPhone-compatible presets
──────────────────────────
  high     : CRF 18, preset medium, 192k AAC, H.264 High@4.0  (lossless-ish)
  standard : CRF 23, preset fast,   128k AAC, H.264 High@4.0  (default)
  small    : CRF 28, preset fast,    96k AAC, scale ≤720p      (data saving)

All presets use:
  -pix_fmt yuv420p          broadest player compat (iPhone, Android, Windows)
  -movflags +faststart       moov atom at front for instant playback
  -vf scale=trunc…           force even dimensions (libx264 requirement)
  -profile:v high -level 4.0 guarantees playback on every iPhone since 4S
  -progress pipe:1           accurate progress via FFmpeg progress API (not stderr regex)

Improvements in this version
──────────────────────────────
  • ConvertQueue          – bounded concurrency with configurable max_concurrent
  • Accurate progress     – uses -progress pipe:1 + out_time_ms parsing (no regex fragility)
  • MediaInfo / ffprobe   – structured metadata probe (codec, resolution, duration, bitrate)
  • scan_folder_for_media – recursive folder scan for supported media files
  • Resume support        – encodes to .part temp file; resumes interrupted jobs via concat
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from utils.ffmpeg_locator import locate_ffmpeg

logger = logging.getLogger(__name__)

# ── Progress API regex ────────────────────────────────────────────────────────
# FFmpeg -progress pipe:1 writes "out_time_ms=<microseconds>" to stdout.
# Despite the "ms" suffix, the value is in *microseconds* (longstanding FFmpeg quirk).
_PROG_MS_RE = re.compile(r"^out_time_ms=(-?\d+)")

# Legacy helper: parse HH:MM:SS.cs timestamps (used by _probe_duration only)
_TIME_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)")

Quality = Literal["high", "standard", "small"]

# ── Supported media extensions (without leading dot) ─────────────────────────
SUPPORTED_EXTS: frozenset[str] = frozenset({
    "mp4", "mkv", "webm", "avi", "mov", "flv",
    "wmv", "m4v", "ts", "mpeg", "mpg", "3gp",
})

# ── iPhone-safe preset table ─────────────────────────────────────────────────
_PRESETS: dict[Quality, dict] = {
    "high": {
        "crf":     "18",
        "preset":  "medium",
        "audio_b": "192k",
        "scale":   None,
        "label":   "Chất lượng cao",
    },
    "standard": {
        "crf":     "23",
        "preset":  "fast",
        "audio_b": "128k",
        "scale":   None,
        "label":   "Chuẩn",
    },
    "small": {
        "crf":     "28",
        "preset":  "fast",
        "audio_b": "96k",
        "scale":   (
            "scale='if(gt(ih,720),trunc(iw*720/ih/2)*2,trunc(iw/2)*2)'"
            ":'if(gt(ih,720),720,trunc(ih/2)*2)'"
        ),
        "label":   "File nhỏ (≤720p)",
    },
}


def _parse_duration(text: str) -> float:
    """Extract duration in seconds from ffmpeg stderr text."""
    m = _TIME_RE.search(text)
    if not m:
        return 0.0
    h, mn, s, cs = (int(g) for g in m.groups())
    return h * 3600 + mn * 60 + s + cs / 100.0


# ── Public data types ─────────────────────────────────────────────────────────

@dataclass
class MediaInfo:
    """Structured metadata retrieved by ffprobe for a media file."""

    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    bitrate_bps: int = 0


class ConversionError(RuntimeError):
    """Raised when ffmpeg exits with a non-zero return code."""


# ── Module-level utility functions ────────────────────────────────────────────

def probe_media_info(source: Path) -> Optional[MediaInfo]:
    """Return MediaInfo for *source* by running ffprobe.

    Uses ``ffprobe -v quiet -print_format json -show_format -show_streams``.
    Returns ``None`` when ffprobe is unavailable or the probe fails.
    Safe to call from a background thread.
    """
    loc = locate_ffmpeg()
    if loc is None or loc.ffprobe_bin == "<not found>":
        logger.debug("probe_media_info: ffprobe not available")
        return None
    try:
        result = subprocess.run(
            [
                loc.ffprobe_bin,
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(source),
            ],
            capture_output=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        data: dict = json.loads(result.stdout)
        streams: list = data.get("streams", [])
        fmt: dict = data.get("format", {})

        duration_s = float(fmt.get("duration") or 0)
        bitrate_bps = int(fmt.get("bit_rate") or 0)
        video_codec = audio_codec = ""
        width = height = 0

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            if codec_type == "video" and not video_codec:
                video_codec = stream.get("codec_name", "")
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                if not duration_s:
                    duration_s = float(stream.get("duration") or 0)
            elif codec_type == "audio" and not audio_codec:
                audio_codec = stream.get("codec_name", "")
                if not duration_s:
                    duration_s = float(stream.get("duration") or 0)

        return MediaInfo(
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=width,
            height=height,
            duration_s=duration_s,
            bitrate_bps=bitrate_bps,
        )
    except Exception as exc:
        logger.debug("probe_media_info failed for %s: %s", source, exc)
        return None


def scan_folder_for_media(folder: Path) -> list[Path]:
    """Recursively scan *folder* and return all supported media files, sorted.

    Safe to call from a background thread.  Permission errors are logged and
    skipped rather than propagated so large or partially-accessible trees are
    handled gracefully.
    """
    results: list[Path] = []
    try:
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix.lower().lstrip(".") in SUPPORTED_EXTS:
                results.append(p)
    except PermissionError as exc:
        logger.warning("scan_folder_for_media: permission error under %s: %s", folder, exc)
    except Exception as exc:
        logger.warning("scan_folder_for_media: unexpected error: %s", exc)
    return results


# ── Conversion service ────────────────────────────────────────────────────────

class FfmpegConvertService:
    """
    Converts a video file to iPhone-compatible MP4/H.264/AAC.

    Usage::

        svc = FfmpegConvertService()
        svc.convert(
            source=Path("video.webm"),
            quality="standard",
            output_dir=Path("D:/iPhone"),   # None = same folder as source
            on_progress=lambda pct: ...,
            on_done=lambda out_path: ...,
            on_error=lambda msg: ...,
        )
    """

    # ── Public API ────────────────────────────────────────────────────────

    def convert(
        self,
        source: Path,
        quality: Quality = "standard",
        output_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_done: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Start a background conversion. All callbacks fire on worker thread."""
        thread = threading.Thread(
            target=self._run,
            args=(source, quality, output_dir, on_progress, on_done, on_error),
            daemon=True,
            name=f"omnidl-convert-{source.stem[:20]}",
        )
        thread.start()

    @staticmethod
    def quality_label(quality: Quality) -> str:
        return _PRESETS[quality]["label"]

    # ── Internal ──────────────────────────────────────────────────────────

    def _run(
        self,
        source: Path,
        quality: Quality,
        output_dir: Optional[Path],
        on_progress: Optional[Callable[[float], None]],
        on_done: Optional[Callable[[Path], None]],
        on_error: Optional[Callable[[str], None]],
    ) -> None:
        try:
            out = self._convert_sync(source, quality, output_dir, on_progress)
            if on_done:
                on_done(out)
        except Exception as exc:
            logger.error("Conversion failed for %s: %s", source, exc)
            if on_error:
                on_error(str(exc))

    def _convert_sync(
        self,
        source: Path,
        quality: Quality,
        output_dir: Optional[Path],
        on_progress: Optional[Callable[[float], None]],
    ) -> Path:
        if not source.is_file():
            raise ConversionError(f"File không tồn tại: {source}")

        ffmpeg_bin = self._locate_ffmpeg_bin()
        preset = _PRESETS.get(quality, _PRESETS["standard"])

        dest_dir = output_dir or source.parent
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Temp file for atomic rename on success and resume on restart
        temp_output = dest_dir / f"{source.stem}_iPhone.part.mp4"

        # Probe source duration (used for progress % and timeout)
        duration_s = self._probe_duration(ffmpeg_bin, source)

        # ── Resume detection ─────────────────────────────────────────────
        # If a .part file exists with meaningful content, resume from where
        # it stopped rather than re-encoding the already-done portion.
        resume_from_s = 0.0
        if temp_output.is_file() and temp_output.stat().st_size > 1_048_576:
            partial_dur = self._probe_duration(ffmpeg_bin, temp_output)
            if duration_s > 0 and 10.0 < partial_dur < duration_s - 5.0:
                resume_from_s = partial_dur
                logger.info(
                    "Resume detected: %s — starting from %.1f s / %.1f s",
                    source.name, resume_from_s, duration_s,
                )

        if resume_from_s > 0:
            output = self._resume_encode(
                ffmpeg_bin, source, dest_dir, temp_output,
                resume_from_s, duration_s, preset, on_progress,
            )
        else:
            output = self._fresh_encode(
                ffmpeg_bin, source, dest_dir, temp_output,
                duration_s, preset, on_progress,
            )

        size_mb = output.stat().st_size / 1_048_576
        logger.info("Done: %s (%.1f MB)", output.name, size_mb)
        return output

    # ── Encode paths ──────────────────────────────────────────────────────

    def _fresh_encode(
        self,
        ffmpeg_bin: Path,
        source: Path,
        dest_dir: Path,
        temp_output: Path,
        duration_s: float,
        preset: dict,
        on_progress: Optional[Callable[[float], None]],
    ) -> Path:
        """Encode the full source to temp_output, then atomically rename."""
        cmd = self._build_cmd(ffmpeg_bin, source, temp_output, preset, seek=0.0)
        self._run_ffmpeg(cmd, duration_s, on_progress)

        output = self._find_output_path(dest_dir, source)
        temp_output.rename(output)
        self._validate_output(output)
        if on_progress:
            on_progress(100.0)
        return output

    def _resume_encode(
        self,
        ffmpeg_bin: Path,
        source: Path,
        dest_dir: Path,
        temp_output: Path,
        resume_from_s: float,
        duration_s: float,
        preset: dict,
        on_progress: Optional[Callable[[float], None]],
    ) -> Path:
        """Encode remaining portion, then concatenate with the existing partial."""
        remaining_s = duration_s - resume_from_s
        # Map raw segment-progress (0..100) onto the full timeline percentage
        base_pct = resume_from_s / duration_s * 100.0 if duration_s > 0 else 0.0

        def scaled_progress(pct: float) -> None:
            if on_progress:
                full_pct = base_pct + pct * (1.0 - base_pct / 100.0)
                on_progress(min(99.0, full_pct))

        temp2 = dest_dir / f"{source.stem}_iPhone.part2.mp4"
        cmd = self._build_cmd(ffmpeg_bin, source, temp2, preset, seek=resume_from_s)
        try:
            self._run_ffmpeg(cmd, remaining_s, scaled_progress)
            output = self._find_output_path(dest_dir, source)
            self._concat(ffmpeg_bin, temp_output, temp2, output)
            self._validate_output(output)
            if on_progress:
                on_progress(100.0)
            return output
        finally:
            temp_output.unlink(missing_ok=True)
            temp2.unlink(missing_ok=True)

    # ── FFmpeg command builders ───────────────────────────────────────────

    @staticmethod
    def _build_cmd(
        ffmpeg_bin: Path,
        source: Path,
        output: Path,
        preset: dict,
        seek: float = 0.0,
    ) -> list[str]:
        """Assemble the ffmpeg CLI command for a single encode pass."""
        vf_parts = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        if preset["scale"]:
            vf_parts = [preset["scale"]]

        cmd: list[str] = [str(ffmpeg_bin), "-y"]

        if seek > 0:
            cmd += ["-ss", f"{seek:.3f}"]  # input-side fast seek

        cmd += [
            "-i", str(source),
            # ── Progress API: write key=value pairs to stdout each frame ──
            "-progress", "pipe:1",
            # Suppress verbose stats on stderr (keep only genuine errors)
            "-nostats",
            "-loglevel", "error",
            # Video
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level:v", "4.0",
            "-preset", preset["preset"],
            "-crf", preset["crf"],
            "-pix_fmt", "yuv420p",
            "-vf", ",".join(vf_parts),
            # Audio
            "-c:a", "aac",
            "-b:a", preset["audio_b"],
            "-ar", "44100",
            # Container
            "-movflags", "+faststart",
            str(output),
        ]
        return cmd

    @staticmethod
    def _run_ffmpeg(
        cmd: list[str],
        duration_s: float,
        on_progress: Optional[Callable[[float], None]],
    ) -> None:
        """Execute *cmd*, parse stdout for FFmpeg progress API data.

        Two daemon threads drain stdout (progress) and stderr (errors) in
        parallel to prevent pipe stalls.  The calling thread blocks on
        ``proc.wait(timeout)``.

        Raises :class:`ConversionError` on non-zero exit or timeout.
        """
        timeout_s = max(60.0, min(
            duration_s * 6 if duration_s > 0 else 3600.0,
            14400.0,
        ))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,   # receives -progress pipe:1 output
            stderr=subprocess.PIPE,   # receives error messages
        )

        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None  # noqa: S101
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(line)

        def _drain_stdout() -> None:
            """Parse out_time_ms=<μs> lines; each represents one progress update."""
            assert proc.stdout is not None  # noqa: S101
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if on_progress and duration_s > 0:
                    m = _PROG_MS_RE.match(line)
                    if m:
                        time_us = int(m.group(1))
                        if time_us >= 0:
                            elapsed_s = time_us / 1_000_000
                            pct = min(99.0, elapsed_s / duration_s * 100.0)
                            on_progress(pct)

        stderr_thread = threading.Thread(
            target=_drain_stderr, daemon=True, name="omnidl-ffmpeg-stderr"
        )
        stdout_thread = threading.Thread(
            target=_drain_stdout, daemon=True, name="omnidl-ffmpeg-stdout"
        )
        stderr_thread.start()
        stdout_thread.start()

        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            proc.communicate()
            raise ConversionError(
                f"ffmpeg timed out after {timeout_s:.0f} s — process killed"
            ) from exc

        stderr_thread.join()
        stdout_thread.join()

        if proc.returncode != 0:
            tail = "\n".join(stderr_lines[-10:])
            raise ConversionError(
                f"ffmpeg thoát với lỗi {proc.returncode}.\n{tail}"
            )

    @staticmethod
    def _concat(
        ffmpeg_bin: Path,
        part1: Path,
        part2: Path,
        output: Path,
    ) -> None:
        """Concatenate two same-codec MP4 segments via FFmpeg concat demuxer.

        Stream-copies without re-encoding.  The concat list file uses
        ``as_posix()`` paths (forward slashes) which FFmpeg accepts on Windows
        and POSIX alike.  Single-quotes inside paths are escaped for the
        concat file format.
        """
        def _escape(p: Path) -> str:
            return p.as_posix().replace("'", "'\\''")

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            dir=str(part1.parent),
            encoding="utf-8",
        ) as lf:
            lf.write(f"file '{_escape(part1)}'\n")
            lf.write(f"file '{_escape(part2)}'\n")
            list_file = Path(lf.name)

        try:
            concat_cmd = [
                str(ffmpeg_bin), "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(output),
            ]
            result = subprocess.run(concat_cmd, capture_output=True, timeout=120)
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-500:]
                raise ConversionError(
                    f"Concat failed (code {result.returncode}): {err}"
                )
        finally:
            list_file.unlink(missing_ok=True)

    # ── Path / validation helpers ─────────────────────────────────────────

    @staticmethod
    def _find_output_path(dest_dir: Path, source: Path) -> Path:
        """Return the next available non-colliding output path."""
        candidate = dest_dir / f"{source.stem}_iPhone.mp4"
        if not candidate.exists():
            return candidate
        i = 2
        while True:
            c = dest_dir / f"{source.stem}_iPhone_{i}.mp4"
            if not c.exists():
                return c
            i += 1

    @staticmethod
    def _validate_output(output: Path) -> None:
        if not output.is_file() or output.stat().st_size < 1_000:
            raise ConversionError(
                f"File output trống hoặc không tồn tại: {output}"
            )

    @staticmethod
    def _locate_ffmpeg_bin() -> Path:
        loc = locate_ffmpeg()
        if loc is None:
            raise ConversionError(
                "Không tìm thấy FFmpeg.\n"
                "Cài FFmpeg hoặc đặt ffmpeg.exe vào thư mục resources/ffmpeg/."
            )
        return Path(loc.ffmpeg_bin)

    @staticmethod
    def _probe_duration(ffmpeg_bin: Path, source: Path) -> float:
        """Return duration of *source* in seconds via ``ffmpeg -i``."""
        try:
            r = subprocess.run(
                [str(ffmpeg_bin), "-i", str(source)],
                capture_output=True, timeout=10,
            )
            return _parse_duration(r.stderr.decode("utf-8", errors="replace"))
        except Exception as exc:
            logger.debug("Duration probe failed: %s", exc)
        return 0.0


# ── Convert queue ─────────────────────────────────────────────────────────────

class ConvertQueue:
    """Thread-safe manager for concurrent FFmpeg conversion jobs.

    Limits active conversions to ``max_concurrent`` at a time.  Extra jobs
    wait in background threads without blocking the UI thread.

    Job lifecycle::

        submit() called → thread starts → waits for semaphore slot
        → on_start() fired (QUEUED→CONVERTING)
        → FfmpegConvertService._run() executes
        → on_done() or on_error() fired
        → semaphore released → next waiting job acquires it

    Example (max 2 concurrent)::

        queue = ConvertQueue(max_concurrent=2)
        for path in files:
            queue.submit(source=path, on_start=..., on_done=..., on_error=...)
        # Only 2 jobs convert simultaneously; the rest wait.
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._semaphore = threading.Semaphore(max_concurrent)
        self._svc = FfmpegConvertService()
        self.max_concurrent = max_concurrent

    def submit(
        self,
        source: Path,
        quality: Quality = "standard",
        output_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_done: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_start: Optional[Callable[[], None]] = None,
    ) -> None:
        """Queue a conversion job.  Returns immediately.

        ``on_start`` fires when the job acquires a concurrency slot (i.e. when
        it transitions from *queued* to *converting*).  All callbacks execute
        on worker threads — callers must use ``widget.after(0, ...)`` for UI
        updates.
        """
        def _worker() -> None:
            self._semaphore.acquire()
            try:
                if on_start:
                    on_start()
                self._svc._run(
                    source, quality, output_dir,
                    on_progress, on_done, on_error,
                )
            finally:
                self._semaphore.release()

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"omnidl-queue-{source.stem[:20]}",
        ).start()
