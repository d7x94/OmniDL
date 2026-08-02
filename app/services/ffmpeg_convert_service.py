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
  -profile:v main -level 4.1 guarantees playback on every iPhone since 4S
  -progress pipe:1           accurate progress via FFmpeg progress API (not stderr regex)

Features
──────────
  • ConvertQueue          – bounded concurrency with configurable max_concurrent
  • Accurate progress     – uses -progress pipe:1 + out_time_ms parsing (no regex fragility)
  • FfmpegMediaInfo / ffprobe – structured metadata probe (codec, resolution, duration, bitrate)
  • scan_folder_for_media – recursive folder scan for supported media files
  • Cancel support        – per-job cancel_event kills FFmpeg and its process group
  • .part cleanup         – incomplete temp files deleted on failure or restart
  • GPU encoding          – NVENC / QSV / AMF / VideoToolbox with automatic detection
  • Quality system        – High / Standard / Small / Custom with per-encoder quality flags
  • Speed presets         – Quality / Balanced / Fast mapping per encoder
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Callable, Literal, Optional

from utils.ffmpeg_locator import locate_ffmpeg

# Suppress console window on Windows for all subprocess calls.
# subprocess.CREATE_NO_WINDOW is 0x08000000 on Windows; absent on other platforms.
_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

logger = logging.getLogger(__name__)

# ── Progress API regex ────────────────────────────────────────────────────────
_PROG_MS_RE = re.compile(r"^out_time_ms=(-?\d+)")
_TIME_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)")


def _parse_seconds(m: "re.Match[str]") -> float:
    """Convert a time=HH:MM:SS.cs regex match to total seconds (float).

    Accepts a match from a pattern with 4 groups: hours, minutes,
    seconds, centiseconds (0-99). Used by tests and inline progress parsing.
    """
    h, m_, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600 + m_ * 60 + s + cs / 100


Quality = Literal["high", "standard", "small", "custom"]

# ── Supported media extensions (without leading dot) ─────────────────────────
SUPPORTED_EXTS: frozenset[str] = frozenset(
    {
        "mp4",
        "mkv",
        "webm",
        "avi",
        "mov",
        "flv",
        "wmv",
        "m4v",
        "ts",
        "mpeg",
        "mpg",
        "3gp",
    }
)

# ── iPhone-safe preset table ─────────────────────────────────────────────────
_PRESETS: dict[str, dict] = {
    "high": {
        "crf": "18",
        "preset": "medium",
        "audio_b": "192k",
        "scale": None,
        "label": "Chất lượng cao",
    },
    "standard": {
        "crf": "23",
        "preset": "fast",
        "audio_b": "128k",
        "scale": None,
        "label": "Chuẩn",
    },
    "small": {
        "crf": "28",
        "preset": "fast",
        "audio_b": "96k",
        "scale": (
            "scale='if(gt(ih,720),trunc(iw*720/ih/2)*2,trunc(iw/2)*2)'"
            ":'if(gt(ih,720),720,trunc(ih/2)*2)':flags=lanczos"
        ),
        "label": "File nhỏ (720p)",
    },
    "custom": {
        "crf": "23",
        "preset": "fast",
        "audio_b": "128k",
        "scale": None,
        "label": "Tuỳ chỉnh",
    },
}


def _parse_duration(text: str) -> float:
    m = _TIME_RE.search(text)
    if not m:
        return 0.0
    h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    frac = m.group(4)
    return h * 3600 + mn * 60 + s + int(frac) / (10 ** len(frac))


def _ff_float(val, default: float = 0.0) -> float:
    """Safe float conversion for ffprobe JSON values that may be 'N/A'."""
    try:
        return float(val or 0)
    except (ValueError, TypeError):
        return default


def _ff_int(val, default: int = 0) -> int:
    """Safe int conversion for ffprobe JSON values that may be 'N/A'."""
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return default


# ── Public data types ─────────────────────────────────────────────────────────


@dataclass
class FfmpegMediaInfo:
    """Structured metadata retrieved by ffprobe for a media file.

    Named FfmpegMediaInfo to avoid collision with domain.models.download_task.MediaInfo
    which represents yt-dlp video metadata. This class represents local file metadata
    obtained via ffprobe (codecs, resolution, duration, bitrate).
    """

    video_codec: str = ""
    audio_codec: str = ""
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    bitrate_bps: int = 0
    video_nb_frames: int = 0
    video_fps: float = 0.0
    video_duration_s: float = 0.0
    audio_duration_s: float = 0.0


class ConversionError(RuntimeError):
    """Raised when ffmpeg exits with a non-zero return code."""


class ConversionCancelledError(ConversionError):
    """Raised when a conversion job is cancelled by the user."""


# ── Hardware encoder catalogue ────────────────────────────────────────────────


@dataclass
class HwEncoderSpec:
    """Specification for one hardware encoder backend."""

    ffmpeg_codec: str
    quality_flag: str
    quality_values: dict[str, str]
    supports_profile_level: bool
    speed_flag: str
    speed_map: dict[str, str]


_HW_ENCODER_CATALOG: dict[str, HwEncoderSpec] = {
    "nvenc": HwEncoderSpec(
        ffmpeg_codec="h264_nvenc",
        quality_flag="-cq",
        quality_values={"high": "19", "standard": "23", "small": "28"},
        supports_profile_level=True,
        speed_flag="-preset",
        speed_map={"quality": "p7", "balanced": "p5", "fast": "p3"},
    ),
    "qsv": HwEncoderSpec(
        ffmpeg_codec="h264_qsv",
        quality_flag="-global_quality",
        quality_values={"high": "18", "standard": "23", "small": "28"},
        supports_profile_level=True,
        speed_flag="-preset",
        speed_map={"quality": "slow", "balanced": "medium", "fast": "fast"},
    ),
    "amf": HwEncoderSpec(
        ffmpeg_codec="h264_amf",
        quality_flag="-qp",
        quality_values={"high": "18", "standard": "23", "small": "28"},
        supports_profile_level=True,
        speed_flag="-quality",
        speed_map={"quality": "quality", "balanced": "balanced", "fast": "speed"},
    ),
    "videotoolbox": HwEncoderSpec(
        ffmpeg_codec="h264_videotoolbox",
        quality_flag="-q:v",
        quality_values={"high": "65", "standard": "50", "small": "35"},
        supports_profile_level=False,
        speed_flag="",
        speed_map={},
    ),
    # ── HEVC / H.265 hardware encoders ────────────────────────────────────
    "nvenc_hevc": HwEncoderSpec(
        ffmpeg_codec="hevc_nvenc",
        quality_flag="-cq",
        quality_values={"high": "20", "standard": "24", "small": "29"},
        supports_profile_level=False,
        speed_flag="-preset",
        speed_map={"quality": "p7", "balanced": "p5", "fast": "p3"},
    ),
    "qsv_hevc": HwEncoderSpec(
        ffmpeg_codec="hevc_qsv",
        quality_flag="-global_quality",
        quality_values={"high": "20", "standard": "24", "small": "29"},
        supports_profile_level=False,
        speed_flag="-preset",
        speed_map={"quality": "slow", "balanced": "medium", "fast": "fast"},
    ),
    "amf_hevc": HwEncoderSpec(
        ffmpeg_codec="hevc_amf",
        quality_flag="-qp",
        quality_values={"high": "20", "standard": "24", "small": "29"},
        supports_profile_level=False,
        speed_flag="-quality",
        speed_map={"quality": "quality", "balanced": "balanced", "fast": "speed"},
    ),
    # ── AV1 hardware encoders (ffmpeg 8+) ─────────────────────────────────
    "nvenc_av1": HwEncoderSpec(
        ffmpeg_codec="av1_nvenc",
        quality_flag="-cq",
        quality_values={"high": "20", "standard": "28", "small": "36"},
        supports_profile_level=False,
        speed_flag="-preset",
        speed_map={"quality": "p7", "balanced": "p5", "fast": "p3"},
    ),
    "qsv_av1": HwEncoderSpec(
        ffmpeg_codec="av1_qsv",
        quality_flag="-global_quality",
        quality_values={"high": "20", "standard": "28", "small": "36"},
        supports_profile_level=False,
        speed_flag="-preset",
        speed_map={"quality": "slow", "balanced": "medium", "fast": "fast"},
    ),
    "amf_av1": HwEncoderSpec(
        ffmpeg_codec="av1_amf",
        quality_flag="-qp",
        quality_values={"high": "20", "standard": "28", "small": "36"},
        supports_profile_level=False,
        speed_flag="-quality",
        speed_map={"quality": "quality", "balanced": "balanced", "fast": "speed"},
    ),
}

# Maps ffmpeg codec string -> catalog key for detection
_CODEC_TO_KEY: dict[str, str] = {spec.ffmpeg_codec: key for key, spec in _HW_ENCODER_CATALOG.items()}

# H.264 probe targets — one per GPU brand; sufficient to populate ENCODER_OPTIONS base keys.
# HEVC/AV1 variants are looked up from _HW_ENCODER_CATALOG at encode time, not from the available set.
_PROBE_CODECS: list[tuple[str, str]] = [
    ("nvenc", "h264_nvenc"),
    ("qsv", "h264_qsv"),
    ("amf", "h264_amf"),
    ("videotoolbox", "h264_videotoolbox"),
]

# CPU speed-preset mapping
_CPU_SPEED_MAP: dict[str, str] = {
    "quality": "medium",
    "balanced": "fast",
    "fast": "veryfast",
}

# ── Public option lists (consumed by the UI) ──────────────────────────────────

ENCODER_OPTIONS: list[tuple[str, str]] = [
    ("cpu", "CPU (libx264)"),
    ("nvenc", "NVIDIA NVENC"),
    ("qsv", "Intel Quick Sync"),
    ("amf", "AMD AMF"),
    ("videotoolbox", "VideoToolbox (macOS)"),
]

CODEC_OPTIONS: list[tuple[str, str]] = [
    ("h264", "H.264 (tương thích cao nhất)"),
    ("hevc", "H.265 / HEVC (~30% nhỏ hơn)"),
    ("av1", "AV1 (~50% nhỏ hơn, cần ff8+)"),
]

SPEED_OPTIONS: list[tuple[str, str]] = [
    # "quality" → slower encode, better compression (not a quality *level*)
    ("quality", "Chậm (Nén tốt nhất)"),
    ("balanced", "Cân bằng"),
    ("fast", "Nhanh (Nén ít hơn)"),
]


@dataclass
class EncodeSettings:
    """User-facing encode configuration: encoder, quality tier and speed."""

    encoder_key: str = "cpu"
    quality: str = "standard"
    speed_preset: str = "balanced"
    custom_quality: int = 23
    output_codec: str = "h264"  # h264|hevc|av1


# ── Encoder detection cache ───────────────────────────────────────────────────
# Cache the result of detect_available_encoders() for _ENCODER_CACHE_TTL_S
# seconds so repeated calls (e.g. on tab re-focus) do not re-run the expensive
# validation test-encodes.  Protected by a lock so concurrent calls on different
# worker threads see a consistent result.

_ENCODER_CACHE_TTL_S: float = 300.0  # 5 minutes
_encoder_cache: Optional[set[str]] = None
_encoder_cache_ts: float = 0.0
_encoder_cache_lock: threading.Lock = threading.Lock()
_encoder_detection_running: bool = False
_encoder_detection_cond: threading.Condition = threading.Condition(threading.Lock())


def _encoder_cache_get() -> Optional[set[str]]:
    """Return the cached encoder set if still fresh, else ``None``."""
    with _encoder_cache_lock:
        if _encoder_cache is not None and (time.monotonic() - _encoder_cache_ts < _ENCODER_CACHE_TTL_S):
            return set(_encoder_cache)  # defensive copy
    return None


def _encoder_cache_set(result: set[str]) -> None:
    """Store *result* in the cache with the current timestamp."""
    global _encoder_cache, _encoder_cache_ts  # noqa: PLW0603
    with _encoder_cache_lock:
        _encoder_cache = set(result)
        _encoder_cache_ts = time.monotonic()


def _encoder_cache_invalidate() -> None:
    """Expire the encoder cache so the next call to detect_available_encoders() re-runs."""
    global _encoder_cache_ts  # noqa: PLW0603
    with _encoder_cache_lock:
        _encoder_cache_ts = 0.0


def get_available_encoder_options(
    ffmpeg_bin: Optional[Path] = None,
) -> list[tuple[str, str]]:
    """Return only the encoder options available on this machine.

    Calls :func:`detect_available_encoders` (with caching) and filters
    :data:`ENCODER_OPTIONS` down to the encoders that are actually available.
    CPU (``libx264``) is always included regardless of detection results.

    Returns:
        A list of ``(key, label)`` tuples in the same order as
        :data:`ENCODER_OPTIONS` but containing only available encoders.
    """
    available = detect_available_encoders(ffmpeg_bin=ffmpeg_bin)
    return [(key, label) for key, label in ENCODER_OPTIONS if key in available]


# ── Hardware detection ────────────────────────────────────────────────────────


def _validate_encoder_codec(ffmpeg_bin: Path, codec: str) -> bool:
    """Return ``True`` if *codec* can actually encode on this machine.

    Runs a one-frame synthetic test encode via FFmpeg's ``lavfi testsrc``
    source and discards the output with ``-f null``.  No files are written.
    This catches drivers that are listed by ``ffmpeg -encoders`` but fail at
    runtime (e.g. ``h264_nvenc`` without ``nvcuda.dll``, or ``h264_qsv``
    without an Intel GPU).

    The test is intentionally kept as cheap as possible:

    * 64×64 resolution — minimal GPU memory pressure
    * 1 output frame — sub-second wall time on any hardware
    * ``-f null -`` — no disk I/O

    Safe to call from any thread.  Always returns ``False`` on exception.
    """
    cmd = [
        str(ffmpeg_bin),
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=64x64:rate=1",
        "-vf",
        "format=yuv420p",
        "-c:v",
        codec,
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
        "-y",
        "-loglevel",
        "error",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            creationflags=_WIN_NO_WINDOW,
        )
        if result.returncode == 0:
            return True
        logger.debug(
            "_validate_encoder_codec: %s exited with code %d",
            codec,
            result.returncode,
        )
        return False
    except Exception as exc:
        logger.debug("_validate_encoder_codec: %s error: %s", codec, exc)
        return False


def detect_available_encoders(
    ffmpeg_bin: Optional[Path] = None,
) -> set[str]:
    """Return the set of encoder keys available **and working** on this machine.

    Results are cached for :data:`_ENCODER_CACHE_TTL_S` seconds (default
    5 minutes) so repeated calls do not re-run the expensive validation
    test-encodes.  Pass an explicit *ffmpeg_bin* to bypass the cache (useful
    in tests that need isolated results).

    Two-phase detection:

    1. **List phase** — runs ``ffmpeg -encoders`` and builds a candidate set
       of GPU encoder keys whose codec string appears in the output.
    2. **Validate phase** — for each candidate, calls
       :func:`_validate_encoder_codec` to perform a lightweight one-frame test
       encode.  Encoders that fail (e.g. NVENC without ``nvcuda.dll``, or QSV
       without an Intel GPU present) are silently excluded.

    CPU (``libx264``) is always included regardless of detection results.
    Safe to call from any thread.
    """
    global _encoder_detection_running
    # ── Cache lookup (only when ffmpeg_bin is not explicitly overridden) ──
    _use_cache = ffmpeg_bin is None

    if _use_cache:
        cached = _encoder_cache_get()
        if cached is not None:
            logger.debug("detect_available_encoders: returning cached result %s", cached)
            return cached

        # In-flight deduplication: if another thread is already detecting,
        # wait for it then return the cached result rather than spawning a
        # second ffmpeg -encoders subprocess concurrently.
        with _encoder_detection_cond:
            while _encoder_detection_running:
                _encoder_detection_cond.wait(timeout=60.0)
            cached = _encoder_cache_get()
            if cached is not None:
                logger.debug("detect_available_encoders: returning cached result %s", cached)
                return cached
            _encoder_detection_running = True

    try:
        available: set[str] = {"cpu"}

        if ffmpeg_bin is None:
            loc = locate_ffmpeg()
            if loc is None:
                if _use_cache:
                    _encoder_cache_set(available)
                return available
            ffmpeg_bin = Path(loc.ffmpeg_bin)

        # Skip ffmpeg -encoders (times out on Scoop shim + GPU driver init).
        # Probe each known GPU codec directly via test-encode in Phase 2.
        candidates: list[tuple[str, str]] = list(_PROBE_CODECS)

        # ── Phase 2: validate each candidate with a short test encode ─────────
        for key, codec in candidates:
            if _validate_encoder_codec(ffmpeg_bin, codec):
                available.add(key)
                logger.debug("detect_available_encoders: %s (%s) OK", key, codec)
            else:
                logger.info(
                    "detect_available_encoders: %s (%s) listed but failed "
                    "validation — excluded (missing drivers?)",
                    key,
                    codec,
                )

        if _use_cache:
            _encoder_cache_set(available)
        return available
    finally:
        if _use_cache:
            with _encoder_detection_cond:
                _encoder_detection_running = False
                _encoder_detection_cond.notify_all()


# ── Module-level utility functions ────────────────────────────────────────────


def probe_media_info(source: Path) -> Optional[FfmpegMediaInfo]:
    """Return FfmpegMediaInfo for *source* by running ffprobe.

    Returns ``None`` when ffprobe is unavailable or the probe fails.
    Safe to call from a background thread.
    """
    loc = locate_ffmpeg()
    if loc is None or loc.ffprobe_bin == "<not found>":
        logger.debug("probe_media_info: ffprobe not available")
        return None
    try:
        # FLV/TS with Enhanced codec (HEVC) require extended analysis to be
        # detected by ffprobe; without this the video stream is not found and
        # the post-conversion validation block is silently skipped.
        probe_extra: list[str] = []
        if source.suffix.lower() in {".flv", ".ts"}:
            probe_extra = ["-analyzeduration", "50M", "-probesize", "50M"]
        result = subprocess.run(
            [
                loc.ffprobe_bin,
                *probe_extra,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(source),
            ],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
            creationflags=_WIN_NO_WINDOW,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        data: dict = json.loads(result.stdout)
        streams: list = data.get("streams", [])
        fmt: dict = data.get("format", {})

        duration_s = _ff_float(fmt.get("duration"))
        bitrate_bps = _ff_int(fmt.get("bit_rate"))
        video_codec = audio_codec = ""
        width = height = 0
        video_nb_frames = 0
        video_fps = 0.0
        video_duration_s = 0.0
        audio_duration_s = 0.0

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            if codec_type == "video" and not video_codec:
                video_codec = stream.get("codec_name", "")
                width = _ff_int(stream.get("width"))
                height = _ff_int(stream.get("height"))
                video_nb_frames = _ff_int(stream.get("nb_frames"))
                video_duration_s = _ff_float(stream.get("duration"))
                if not duration_s:
                    duration_s = video_duration_s
                # Parse r_frame_rate ("30/1", "25/1") for nb_frames fallback
                rfr = stream.get("r_frame_rate", "")
                if rfr and "/" in rfr:
                    try:
                        num, den = rfr.split("/")
                        fps = float(num) / float(den) if float(den) else 0.0
                        video_fps = fps
                    except (ValueError, ZeroDivisionError):
                        pass
            elif codec_type == "audio" and not audio_codec:
                audio_codec = stream.get("codec_name", "")
                audio_duration_s = _ff_float(stream.get("duration"))
                if not duration_s:
                    duration_s = audio_duration_s

        # Fallback: estimate from frame count for FLV livestream (Duration: N/A)
        if not duration_s and video_nb_frames and video_fps > 0:
            duration_s = video_nb_frames / video_fps

        return FfmpegMediaInfo(
            video_codec=video_codec,
            audio_codec=audio_codec,
            width=width,
            height=height,
            duration_s=duration_s,
            bitrate_bps=bitrate_bps,
            video_nb_frames=video_nb_frames,
            video_fps=video_fps,
            video_duration_s=video_duration_s,
            audio_duration_s=audio_duration_s,
        )
    except Exception as exc:
        logger.debug("probe_media_info failed for %s: %s", source, exc)
        return None


def scan_folder_for_media(folder: Path) -> list[Path]:
    """Recursively scan *folder* and return all supported media files, sorted.

    Safe to call from a background thread.
    """
    results: list[Path] = []
    try:
        for p in sorted(folder.rglob("*")):
            if (
                p.is_file()
                and p.suffix.lower().lstrip(".") in SUPPORTED_EXTS
                and not p.name.endswith(".part.mp4")  # skip incomplete encode temps
            ):
                results.append(p)
    except PermissionError as exc:
        logger.warning("scan_folder_for_media: permission error under %s: %s", folder, exc)
    except Exception as exc:
        logger.warning("scan_folder_for_media: unexpected error: %s", exc)
    return results


# ── Conversion service ────────────────────────────────────────────────────────


class FfmpegConvertService:
    """Converts a video file to iPhone-compatible MP4/H.264/AAC."""

    @staticmethod
    def _kill_proc(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
        """Kill *proc* and its process group (Unix) or just the process (Windows).

        On POSIX, ``start_new_session=True`` was used at Popen time, so ffmpeg
        is the process group leader.  ``os.killpg`` terminates the whole group
        (ffmpeg + any child processes it may have spawned).  Falls back to a
        plain ``proc.kill()`` if the group kill fails for any reason.
        """
        try:
            if sys.platform != "win32":
                import os
                import signal

                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def convert(
        self,
        source: Path,
        quality: Quality = "standard",
        target_ext: str = "mp4",
        output_dir: Optional[Path] = None,
        on_progress: Optional[Callable[[float], None]] = None,
        on_done: Optional[Callable[[Path], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        encode_settings: Optional[EncodeSettings] = None,
    ) -> None:
        """Start a background conversion. All callbacks fire on worker thread.

        target_ext: output container without the dot — ``"mp4"`` (default),
        ``"mp3"``, ``"mkv"``, or ``"avi"``.  MP3 triggers audio-only extraction;
        other formats use the configured H.264/AAC video codec.
        encode_settings overrides quality/encoder when provided (Custom mode).
        When encode_settings is provided, its quality field is used instead of
        the *quality* parameter.
        """
        # When EncodeSettings carries a quality tier, use it as the quality arg
        # so the preset lookup is consistent.
        effective_quality: Quality = (
            encode_settings.quality  # type: ignore[assignment]
            if encode_settings is not None and encode_settings.quality in ("high", "standard", "small")
            else quality
        )
        thread = threading.Thread(
            target=self._run,
            args=(source, effective_quality, output_dir, on_progress, on_done, on_error),
            kwargs={"encode_settings": encode_settings, "target_ext": target_ext},
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
        encode_settings: Optional[EncodeSettings] = None,
        cancel_event: Optional[threading.Event] = None,
        target_ext: str = "mp4",
    ) -> None:
        try:
            out = self._convert_sync(
                source,
                quality,
                output_dir,
                on_progress,
                encode_settings,
                cancel_event=cancel_event,
                target_ext=target_ext,
            )
            if on_done:
                on_done(out)
        except ConversionCancelledError:
            logger.info("Conversion cancelled: %s", source.name)
            if on_error:
                on_error("Đã huỷ")
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
        encode_settings: Optional[EncodeSettings] = None,
        cancel_event: Optional[threading.Event] = None,
        target_ext: str = "mp4",
    ) -> Path:
        # Pre-flight cancel check: job may have been cancelled while queued
        if cancel_event is not None and cancel_event.is_set():
            raise ConversionCancelledError("Đã huỷ")

        if not source.is_file():
            raise ConversionError(f"File không tồn tại: {source}")

        target_ext = target_ext.lstrip(".").lower() or "mp4"

        # MP3: audio-only extraction — bypass the video encode pipeline entirely
        if target_ext == "mp3":
            return self._extract_audio_mp3(
                source,
                output_dir,
                on_progress,
                cancel_event=cancel_event,
            )

        ffmpeg_bin = self._locate_ffmpeg_bin()
        preset = _PRESETS.get(quality, _PRESETS["standard"])

        dest_dir = output_dir or source.parent
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Use target_ext for the temp and final output filenames.
        # Include a per-job UUID suffix so concurrent jobs on the same source
        # (e.g. desktop + remote auto-convert both triggered) write to distinct
        # temp paths and cannot corrupt each other's output.
        _job_id = uuid.uuid4().hex[:8]
        temp_output = dest_dir / f"{source.stem}_iPhone_{_job_id}.part.mp4"

        duration_s = self._probe_duration(ffmpeg_bin, source)

        # Clean up stale .part files from previous interrupted jobs on this source.
        # UUID-format files ({stem}_iPhone_{8hex}.part.mp4) may belong to a concurrent
        # job that is actively writing; only delete those older than 1 hour.
        # Non-UUID files ({stem}_iPhone.part.mp4) are from the old pre-UUID code path
        # and are safe to delete immediately.
        _UUID_PART_RE = re.compile(r"_[0-9a-f]{8}\.part\.mp4$")
        _now = time.time()
        for _stale in dest_dir.glob(f"{source.stem}_iPhone*.part.mp4"):
            try:
                if _UUID_PART_RE.search(_stale.name) and _now - _stale.stat().st_mtime <= 3600:
                    continue  # possibly an active concurrent job — leave it alone
                _stale.unlink(missing_ok=True)
                logger.info("Deleted stale .part file before restart: %s", _stale.name)
            except OSError:
                pass
        for _stale_trim in dest_dir.glob(f"{source.stem}_iPhone*.part.trim.mp4"):
            try:
                _stale_trim.unlink(missing_ok=True)
                logger.info("Deleted stale .part.trim.mp4 file: %s", _stale_trim.name)
            except OSError:
                pass

        output = self._try_encode_with_fallback(
            ffmpeg_bin,
            source,
            dest_dir,
            temp_output,
            duration_s,
            preset,
            on_progress,
            encode_settings,
            cancel_event=cancel_event,
            target_ext=target_ext,
        )

        size_mb = output.stat().st_size / 1_048_576
        logger.info("Done: %s (%.1f MB)", output.name, size_mb)
        return output

    def _extract_audio_mp3(
        self,
        source: Path,
        output_dir: Optional[Path],
        on_progress: Optional[Callable[[float], None]],
        cancel_event: Optional[threading.Event] = None,
    ) -> Path:
        """Extract audio from *source* to MP3 (libmp3lame, 192k)."""
        if cancel_event is not None and cancel_event.is_set():
            raise ConversionCancelledError("Đã huỷ")

        ffmpeg_bin = self._locate_ffmpeg_bin()
        dest_dir = output_dir or source.parent
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Unique output path
        candidate = dest_dir / f"{source.stem}.mp3"
        i = 1
        while candidate.exists():
            candidate = dest_dir / f"{source.stem}_{i}.mp3"
            i += 1

        _mp3_job_id = uuid.uuid4().hex[:8]
        temp_mp3 = dest_dir / f"{source.stem}_{_mp3_job_id}.part.mp3"
        _UUID_PART_MP3_RE = re.compile(r"_[0-9a-f]{8}\.part\.mp3$")
        _now_mp3 = time.time()
        for _stale_mp3 in dest_dir.glob(f"{source.stem}*.part.mp3"):
            try:
                if (
                    _UUID_PART_MP3_RE.search(_stale_mp3.name)
                    and _now_mp3 - _stale_mp3.stat().st_mtime <= 3600
                ):
                    continue
                _stale_mp3.unlink(missing_ok=True)
                logger.info("Deleted stale .part.mp3 file: %s", _stale_mp3.name)
            except OSError:
                pass

        duration_s = self._probe_duration(ffmpeg_bin, source)

        cmd = [
            str(ffmpeg_bin),
            "-y",
            "-i",
            str(source),
            "-vn",  # no video
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-progress",
            "pipe:1",
            "-nostats",
            "-loglevel",
            "error",
            str(temp_mp3),
        ]
        try:
            self._run_ffmpeg(cmd, duration_s, on_progress, cancel_event=cancel_event)
        except Exception:
            temp_mp3.unlink(missing_ok=True)
            raise

        temp_mp3.rename(candidate)
        logger.info("MP3 done: %s (%.1f MB)", candidate.name, candidate.stat().st_size / 1_048_576)
        return candidate

    def _try_encode_with_fallback(
        self,
        ffmpeg_bin: Path,
        source: Path,
        dest_dir: Path,
        temp_output: Path,
        duration_s: float,
        preset: dict,
        on_progress: Optional[Callable[[float], None]],
        encode_settings: Optional[EncodeSettings],
        cancel_event: Optional[threading.Event] = None,
        target_ext: str = "mp4",
    ) -> Path:
        """Attempt encode; if GPU fails, retry with CPU (libx264).

        CPU failures propagate immediately without retry.
        """
        # Resolve "auto" → best available GPU encoder, fallback to CPU
        if encode_settings is not None and encode_settings.encoder_key == "auto":
            avail = get_available_encoder_options()
            gpu_keys = [k for k, _ in avail if k != "cpu"]
            encode_settings = EncodeSettings(
                encoder_key=gpu_keys[0] if gpu_keys else "cpu",
                quality=encode_settings.quality,
                speed_preset=encode_settings.speed_preset,
                custom_quality=encode_settings.custom_quality,
                output_codec=encode_settings.output_codec,
            )

        is_gpu = (
            encode_settings is not None
            and encode_settings.encoder_key != "cpu"
            and encode_settings.encoder_key in _HW_ENCODER_CATALOG
        )

        try:
            return self._fresh_encode(
                ffmpeg_bin,
                source,
                dest_dir,
                temp_output,
                duration_s,
                preset,
                on_progress,
                encode_settings=encode_settings,
                cancel_event=cancel_event,
                target_ext=target_ext,
            )
        except ConversionCancelledError:
            raise  # cancelled — never retry
        except ConversionError as exc:
            if not is_gpu:
                raise

            logger.warning(
                "GPU encoder %r failed (%s) — retrying with libx264",
                encode_settings.encoder_key,  # type: ignore[union-attr]
                exc,
            )
            _encoder_cache_invalidate()
            encode_settings = dataclass_replace(encode_settings, encoder_key="cpu")  # type: ignore[union-attr,type-var]  # None guarded by outer check
            return self._fresh_encode(
                ffmpeg_bin,
                source,
                dest_dir,
                temp_output,
                duration_s,
                preset,
                on_progress,
                encode_settings=encode_settings,
                cancel_event=cancel_event,
                target_ext=target_ext,
            )

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
        encode_settings: Optional[EncodeSettings] = None,
        cancel_event: Optional[threading.Event] = None,
        target_ext: str = "mp4",
    ) -> Path:
        """Encode the full source to temp_output, then atomically rename.

        Guarantees that *temp_output* is deleted if the encode fails or is
        cancelled, preventing orphaned ``.part`` files (BUG 8).
        target_ext controls the output container (mp4/mkv/avi).
        """
        # For non-mp4 containers we pass a temporary .part.mp4 to FFmpeg for
        # the encode, then remux losslessly into the target container.  This
        # keeps the proven .part.mp4 temp workflow intact.
        src_info = probe_media_info(source)
        cmd = self._build_cmd(
            ffmpeg_bin,
            source,
            temp_output,
            preset,
            seek=0.0,
            encode_settings=encode_settings,
        )
        _watchdog_s = 120.0 if source.suffix.lower() in {".flv", ".ts"} else 30.0
        try:
            self._run_ffmpeg(
                cmd, duration_s, on_progress, watchdog_timeout_s=_watchdog_s, cancel_event=cancel_event
            )
        except Exception:
            temp_output.unlink(missing_ok=True)  # BUG 8: clean up on any failure
            raise

        # Validate that the output has a video track when the source did.
        # FLV/TS live files can silently produce audio-only output when ffmpeg
        # misses the H.264 SPS/PPS (hidden by -loglevel error). iPhone's hardware
        # decoder requires the avcc box and shows audio-only; desktop players
        # recover from the bitstream, so the bug is iPhone-specific.
        # Also check video_nb_frames > 0: a zero-frame H.264 track passes codec
        # detection (header metadata) but produces an unplayable file on iPhone/VLC.
        if src_info and src_info.video_codec:
            out_info = probe_media_info(temp_output)
            if not out_info or not out_info.video_codec or out_info.video_nb_frames == 0:
                temp_output.unlink(missing_ok=True)
                raise ConversionError(
                    "Output has no video frames — FLV source may use Enhanced "
                    "codec (HEVC) or has missing SPS/PPS. "
                    "Try re-downloading with yt-dlp instead of IDM."
                )
            # TS/FLV: audio track may extend beyond video due to unmatched demuxer
            # timestamp offset accumulation at the tail. Trim audio to video duration.
            is_flv_ts_source = source.suffix.lower() in {".flv", ".ts"}
            if (
                is_flv_ts_source
                and out_info.video_duration_s > 0
                and out_info.audio_duration_s > out_info.video_duration_s + 5.0
            ):
                trim_to = out_info.video_duration_s
                trim_tmp = temp_output.with_suffix(".trim.mp4")
                trim_cmd = [
                    str(ffmpeg_bin),
                    "-y",
                    "-i",
                    str(temp_output),
                    "-t",
                    f"{trim_to:.3f}",
                    "-c",
                    "copy",
                    str(trim_tmp),
                ]
                r = subprocess.run(trim_cmd, capture_output=True, timeout=120, creationflags=_WIN_NO_WINDOW)
                if r.returncode == 0:
                    temp_output.unlink(missing_ok=True)
                    trim_tmp.rename(temp_output)
                    logger.info(
                        "Trimmed audio tail: %.1f s → %.1f s",
                        out_info.audio_duration_s,
                        trim_to,
                    )
                else:
                    trim_tmp.unlink(missing_ok=True)
                    logger.warning(
                        "Audio trim remux failed (code %d) — keeping original",
                        r.returncode,
                    )

        # Remux to target container when target_ext differs from mp4.
        # mkv and avi accept the H.264+AAC stream without re-encode (-c copy).
        target_ext = target_ext.lower()
        if target_ext not in ("mp4", ""):
            remux_output = self._find_output_path(dest_dir, source, ext=target_ext)
            remux_cmd = [
                str(ffmpeg_bin),
                "-y",
                "-i",
                str(temp_output),
                "-c",
                "copy",
                str(remux_output),
            ]
            result = subprocess.run(remux_cmd, capture_output=True, timeout=120, creationflags=_WIN_NO_WINDOW)
            temp_output.unlink(missing_ok=True)
            if result.returncode != 0:
                remux_output.unlink(missing_ok=True)
                tail = result.stderr[-200:].decode("utf-8", errors="replace")
                raise ConversionError(f"Remux to .{target_ext} failed: {tail}")
            self._validate_output(remux_output)
            if on_progress:
                on_progress(100.0)
            return remux_output

        output = self._find_output_path(dest_dir, source)
        temp_output.rename(output)
        self._validate_output(output)
        if on_progress:
            on_progress(100.0)
        return output

    # ── FFmpeg command builder ────────────────────────────────────────────

    @staticmethod
    def _build_cpu_flags(
        preset: dict,
        encode_settings: Optional[EncodeSettings],
    ) -> list[str]:
        """Return the CPU (libx264) video-codec flags for one encode pass.

        When *encode_settings* is ``None`` the legacy preset dict is used
        directly; otherwise the speed preset and quality are taken from the
        settings object.  Always produces exactly the same flag sequence as the
        inline code it replaced.
        """
        if encode_settings is None:
            return [
                "-c:v",
                "libx264",
                "-profile:v",
                "main",
                "-level:v",
                "4.1",
                "-preset",
                preset["preset"],
                "-crf",
                preset["crf"],
            ]
        cpu_preset_val = _CPU_SPEED_MAP.get(encode_settings.speed_preset, "fast")
        crf_val = (
            str(encode_settings.custom_quality)
            if encode_settings.quality == "custom"
            else _PRESETS.get(encode_settings.quality, _PRESETS["standard"])["crf"]
        )
        return [
            "-c:v",
            "libx264",
            "-profile:v",
            "main",
            "-level:v",
            "4.1",
            "-preset",
            cpu_preset_val,
            "-crf",
            crf_val,
        ]

    @staticmethod
    def _build_cpu_flags_hevc(
        preset: dict,
        encode_settings: EncodeSettings,
    ) -> list[str]:
        _crf_map = {"high": "20", "standard": "26", "small": "30"}
        crf_val = (
            str(encode_settings.custom_quality)
            if encode_settings.quality == "custom"
            else _crf_map.get(encode_settings.quality, "26")
        )
        _speed_map = {"quality": "slow", "balanced": "medium", "fast": "fast"}
        x265_preset = _speed_map.get(encode_settings.speed_preset, "medium")
        # -tag:v hvc1: required for QuickTime/iOS to recognise HEVC in MP4
        return ["-c:v", "libx265", "-preset", x265_preset, "-crf", crf_val, "-tag:v", "hvc1"]

    @staticmethod
    def _build_cpu_flags_av1(
        preset: dict,
        encode_settings: EncodeSettings,
    ) -> list[str]:
        # SVT-AV1: CRF 1-63, preset 0(slowest)-13(fastest)
        _crf_map = {"high": "22", "standard": "32", "small": "40"}
        crf_val = (
            str(encode_settings.custom_quality)
            if encode_settings.quality == "custom"
            else _crf_map.get(encode_settings.quality, "32")
        )
        _speed_map = {"quality": "4", "balanced": "7", "fast": "10"}
        svt_preset = _speed_map.get(encode_settings.speed_preset, "7")
        return ["-c:v", "libsvtav1", "-preset", svt_preset, "-crf", crf_val]

    @staticmethod
    def _build_gpu_flags(
        hw_spec: HwEncoderSpec,
        encode_settings: EncodeSettings,
    ) -> list[str]:
        """Return the GPU hardware-encoder video-codec flags for one encode pass.

        Handles codec, optional profile/level, quality flag+value, and optional
        speed preset.  Quality value is looked up from *hw_spec.quality_values*
        or taken from *encode_settings.custom_quality* when quality is
        ``"custom"``.
        """
        quality_val = (
            str(encode_settings.custom_quality)
            if encode_settings.quality == "custom"
            else hw_spec.quality_values.get(
                encode_settings.quality,
                hw_spec.quality_values.get("standard", "23"),
            )
        )

        flags: list[str] = ["-c:v", hw_spec.ffmpeg_codec]

        if hw_spec.supports_profile_level:
            flags += ["-profile:v", "main", "-level:v", "4.1"]

        flags += [hw_spec.quality_flag, quality_val]

        if hw_spec.speed_flag and hw_spec.speed_map:
            speed_val = hw_spec.speed_map.get(
                encode_settings.speed_preset,
                next(iter(hw_spec.speed_map.values())),
            )
            flags += [hw_spec.speed_flag, speed_val]

        return flags

    @staticmethod
    def _build_cmd(
        ffmpeg_bin: Path,
        source: Path,
        output: Path,
        preset: dict,
        seek: float = 0.0,
        encode_settings: Optional[EncodeSettings] = None,
    ) -> list[str]:
        """Assemble the ffmpeg CLI command for a single encode pass.

        Delegates video-codec flag building to :meth:`_build_cpu_flags` or
        :meth:`_build_gpu_flags`.  When *encode_settings* is ``None`` the
        legacy CPU path is used for full backward compatibility.  Unknown
        encoder keys fall back to libx264 silently.
        """
        vf_parts = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        if preset["scale"]:
            vf_parts = [preset["scale"]]

        is_flv_ts = source.suffix.lower() in {".flv", ".ts"}
        # TS/FLV live streams have mid-stream timestamp discontinuities (TikTok segment
        # boundaries). setpts=PTS-STARTPTS only normalises the start offset; it cannot
        # handle backward mid-stream jumps, which produce non-monotonic output PTS and
        # cause strict players (Infuse/iOS) to drop the video track permanently.
        # Passing +genpts-corrected timestamps through unchanged is the safest approach.
        # setpts=N/FR/TB is skipped for TS/FLV: N resets on decoder reinit at each
        # segment boundary, producing a backward jump in output PTS.
        if not is_flv_ts:
            vf_parts.append("setpts=N/FR/TB")
        # QSV hw-encode requires nv12 frames at encoder input. Decoded frames
        # from software decoders are yuv420p; without this explicit conversion
        # the auto-inserted format filter can silently produce corrupt picture
        # data — valid H.264 bitstream, non-zero frame count, but blank video.
        if encode_settings is not None and encode_settings.encoder_key == "qsv":
            vf_parts.append("format=nv12")

        cmd: list[str] = [str(ffmpeg_bin), "-y"]

        # FLV and TS live recordings often have H.264 SPS/PPS after the first
        # keyframe (not in the container header). Extend analysis so ffmpeg finds
        # codec parameters before starting the encode; without this, libx264 may
        # receive 0 frames and the output MP4 has no video track — iPhone's
        # hardware decoder then falls back to audio-only while desktop players
        # recover by parsing NALUs directly from the bitstream.
        # +genpts applied to all inputs: regenerates container PTS before the encoder
        # sees them, preventing broken DTS in re-encoded live-stream MP4s from
        # propagating into the output.
        fflags = "+genpts"
        if is_flv_ts:
            # 30M is sufficient for SPS/PPS detection in TikTok live files.
            # +discardcorrupt removed: it dropped keyframes at TS segment boundaries
            # (TikTok live token-rotation concat), causing the h264 decoder to lose its
            # reference frame and produce black video for the remainder of the file.
            # +igndts removed: caused PTS regenerated from broken DTS to carry
            # mid-stream timestamp jumps → 1000+ duplicate frames inserted.
            cmd += [
                "-analyzeduration",
                "30M",
                "-probesize",
                "200M",
            ]
        cmd += ["-fflags", fflags]

        if seek > 0:
            cmd += ["-ss", f"{seek:.3f}"]

        cmd += [
            "-i",
            str(source),
            "-progress",
            "pipe:1",
            "-nostats",
            "-stats_period",
            "0.5",
            "-loglevel",
            "warning",
        ]

        codec = encode_settings.output_codec if encode_settings else "h264"

        # ── Video codec + quality (delegated to helpers) ──────────────────
        if encode_settings is None:
            cmd += FfmpegConvertService._build_cpu_flags(preset, encode_settings)
        else:
            encoder_key = encode_settings.encoder_key
            if encoder_key == "cpu":
                if codec == "hevc":
                    cmd += FfmpegConvertService._build_cpu_flags_hevc(preset, encode_settings)
                elif codec == "av1":
                    cmd += FfmpegConvertService._build_cpu_flags_av1(preset, encode_settings)
                else:
                    cmd += FfmpegConvertService._build_cpu_flags(preset, encode_settings)
            else:
                catalog_key = f"{encoder_key}_{codec}" if codec != "h264" else encoder_key
                hw_spec: Optional[HwEncoderSpec] = _HW_ENCODER_CATALOG.get(
                    catalog_key
                ) or _HW_ENCODER_CATALOG.get(encoder_key)
                if hw_spec is None:
                    logger.warning(
                        "_build_cmd: unknown encoder %r, falling back to libx264",
                        encoder_key,
                    )
                    cmd += FfmpegConvertService._build_cpu_flags(preset, encode_settings)
                else:
                    cmd += FfmpegConvertService._build_gpu_flags(hw_spec, encode_settings)

        # ── Common output flags ───────────────────────────────────────────
        # QSV encoders only accept nv12; libx264/NVENC/AMF work with yuv420p
        is_qsv = encode_settings is not None and encode_settings.encoder_key == "qsv"
        pix_fmt = "nv12" if is_qsv else "yuv420p"
        cmd += [
            "-pix_fmt",
            pix_fmt,
            "-vf",
            ",".join(vf_parts),
            "-c:a",
            "aac",
            "-b:a",
            preset["audio_b"],
            "-ac",
            "2",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
        ]
        # Explicit stream selection for FLV/TS: prevents silent wrong-stream
        # picks when the container has non-standard stream ordering.
        # 0:a? makes audio optional — handles video-only FLV without crashing.
        if is_flv_ts:
            # aresample=async=1000: compensates A/V drift up to 1s per discontinuity.
            # asetpts=PTS-STARTPTS: normalises audio PTS to start at 0 using the
            # demuxer-corrected timestamps. This keeps audio duration tied to actual
            # decoded packet timestamps rather than a sample counter, preventing the
            # NB_CONSUMED_SAMPLES overcount from tail segments (interleaved mid-stream)
            # from causing aresample to drop samples and audio to end before real content.
            cmd += ["-af", "aresample=async=1000,asetpts=PTS-STARTPTS"]
            cmd += ["-map", "0:v:0", "-map", "0:a?"]
        cmd += [str(output)]
        return cmd

    @staticmethod
    def _run_ffmpeg(
        cmd: list[str],
        duration_s: float,
        on_progress: Optional[Callable[[float], None]],
        watchdog_timeout_s: float = 30.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Execute *cmd*, parse stdout for FFmpeg progress API data.

        Two daemon threads drain stdout (progress) and stderr (errors) in
        parallel to prevent pipe stalls.  The calling thread blocks on
        ``proc.wait(timeout)``.

        A **progress watchdog** runs alongside: if no stdout output is received
        for *watchdog_timeout_s* seconds while the process is still alive the
        process is killed and a :class:`ConversionError` is raised.  This
        catches encoders that silently stall (e.g. GPU drivers hanging on
        initialisation or a broken pipe).

        A **cancel event** may be supplied; when set the watchdog kills the
        process immediately and :class:`ConversionCancelledError` is raised.

        On POSIX, FFmpeg is launched in a new session (``start_new_session``),
        making it the process group leader so that ``_kill_proc`` can terminate
        the full process tree — including any child processes spawned by exotic
        codec wrappers.

        Raises :class:`ConversionError` on non-zero exit, overall timeout, or
        watchdog timeout.  Raises :class:`ConversionCancelledError` on cancel.
        """
        timeout_s = max(
            60.0,
            min(
                duration_s * 6 if duration_s > 0 else 3600.0,
                14400.0,
            ),
        )

        popen_kwargs: dict = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True  # new process group on POSIX
        else:
            popen_kwargs["creationflags"] = _WIN_NO_WINDOW

        proc = subprocess.Popen(cmd, **popen_kwargs)

        stderr_lines: list[str] = []

        # Shared state for the watchdog — updated by _drain_stdout on every
        # stdout line (not only progress lines, so any FFmpeg output resets it).
        _last_stdout_activity = [time.monotonic()]
        _stdout_done = threading.Event()

        def _drain_stderr() -> None:
            assert proc.stderr is not None  # noqa: S101
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(line)

        def _drain_stdout() -> None:
            assert proc.stdout is not None  # noqa: S101
            last_heartbeat = time.monotonic()
            for raw in proc.stdout:
                _last_stdout_activity[0] = time.monotonic()
                line = raw.decode("utf-8", errors="replace").rstrip()
                if on_progress:
                    m = _PROG_MS_RE.match(line)
                    if m:
                        time_us = int(m.group(1))
                        if time_us >= 0:
                            elapsed_s = time_us / 1_000_000
                            if duration_s > 0:
                                pct = min(99.0, elapsed_s / duration_s * 100.0)
                            else:
                                # Duration unknown — asymptotic curve, never hits 100%
                                # t=30s→32%, t=60s→48%, t=120s→65%, t=300s→82%
                                pct = 95.0 * elapsed_s / (elapsed_s + 65.0)
                            on_progress(pct)
                        else:
                            # Analysis phase (out_time_ms=N/A): send heartbeat every 5s
                            # so the iPhone knows the job is alive even at 0% progress.
                            now = time.monotonic()
                            if now - last_heartbeat >= 5.0:
                                on_progress(0.0)
                                last_heartbeat = now
            _stdout_done.set()

        def _watchdog() -> None:
            """Kill the process on cancel request or stdout silence timeout."""
            while not _stdout_done.wait(timeout=1.0):
                if proc.poll() is not None:
                    break  # process already exited — watchdog not needed
                # Cancel requested by user
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("_run_ffmpeg: cancel requested — killing process")
                    FfmpegConvertService._kill_proc(proc)
                    break
                elapsed_since_last = time.monotonic() - _last_stdout_activity[0]
                if elapsed_since_last >= watchdog_timeout_s:
                    logger.warning(
                        "_run_ffmpeg: no stdout for %.0f s — killing stalled process",
                        elapsed_since_last,
                    )
                    FfmpegConvertService._kill_proc(proc)
                    break

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True, name="omnidl-ffmpeg-stderr")
        stdout_thread = threading.Thread(target=_drain_stdout, daemon=True, name="omnidl-ffmpeg-stdout")
        watchdog_thread = threading.Thread(target=_watchdog, daemon=True, name="omnidl-ffmpeg-watchdog")
        stderr_thread.start()
        stdout_thread.start()
        watchdog_thread.start()

        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired as exc:
            FfmpegConvertService._kill_proc(proc)
            proc.communicate()
            raise ConversionError(f"ffmpeg timed out after {timeout_s:.0f} s") from exc

        stderr_thread.join(timeout=5.0)
        stdout_thread.join(timeout=5.0)
        # Watchdog exits naturally once _stdout_done is set or process exits.
        watchdog_thread.join(timeout=2.0)

        if proc.returncode != 0:
            # Cancelled — raise the specific subclass so callers can distinguish
            if cancel_event is not None and cancel_event.is_set():
                raise ConversionCancelledError("Đã huỷ")
            tail = "\n".join(stderr_lines[-10:])
            raise ConversionError(f"ffmpeg thoát với lỗi {proc.returncode}.\n{tail}")

        # Log FFmpeg warnings even on success — helps diagnose FLV/TS issues
        # where returncode=0 but frames were dropped or codec errors occurred.
        for line in stderr_lines:
            logger.debug("ffmpeg: %s", line)

    @staticmethod
    def _concat(
        ffmpeg_bin: Path,
        part1: Path,
        part2: Path,
        output: Path,
    ) -> None:
        """Concatenate two same-codec MP4 segments via FFmpeg concat demuxer."""

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
                str(ffmpeg_bin),
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(output),
            ]
            result = subprocess.run(
                concat_cmd, capture_output=True, timeout=120, creationflags=_WIN_NO_WINDOW
            )
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-500:]
                raise ConversionError(f"Concat failed (code {result.returncode}): {err}")
        finally:
            list_file.unlink(missing_ok=True)

    # ── Path / validation helpers ─────────────────────────────────────────

    @staticmethod
    def _find_output_path(dest_dir: Path, source: Path, ext: str = "mp4") -> Path:
        """Return the next available non-colliding output path.

        ext: file extension without the leading dot (default "mp4").
        """
        ext = ext.lower().lstrip(".")
        suffix = f".{ext}"
        candidate = dest_dir / f"{source.stem}_iPhone{suffix}"
        if not candidate.exists():
            return candidate
        i = 2
        while True:
            c = dest_dir / f"{source.stem}_iPhone_{i}{suffix}"
            if not c.exists():
                return c
            i += 1

    @staticmethod
    def _validate_output(output: Path) -> None:
        if not output.is_file() or output.stat().st_size < 1_000:
            raise ConversionError(f"File output trong hoac khong ton tai: {output}")

    @staticmethod
    def _locate_ffmpeg_bin() -> Path:
        loc = locate_ffmpeg()
        if loc is None:
            raise ConversionError(
                "Khong tim thay FFmpeg.\nCai FFmpeg hoac dat ffmpeg.exe vao thu muc resources/ffmpeg/."
            )
        return Path(loc.ffmpeg_bin)

    @staticmethod
    def _probe_duration(ffmpeg_bin: Path, source: Path) -> float:
        """Return duration of *source* in seconds via ``ffmpeg -i``."""
        try:
            r = subprocess.run(
                [str(ffmpeg_bin), "-i", str(source)],
                capture_output=True,
                timeout=10,
                creationflags=_WIN_NO_WINDOW,
            )
            d = _parse_duration(r.stderr.decode("utf-8", errors="replace"))
            if d > 0:
                return d
        except Exception as exc:
            logger.debug("Duration probe failed: %s", exc)
        # Fallback: ffprobe JSON (more reliable for FLV/livestream with N/A duration)
        info = probe_media_info(source)
        return info.duration_s if info else 0.0


# ── Convert queue ─────────────────────────────────────────────────────────────


class ConvertQueue:
    """Thread-safe manager for concurrent FFmpeg conversion jobs.

    Limits active conversions to ``max_concurrent`` at a time.
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
        encode_settings: Optional[EncodeSettings] = None,
        target_ext: str = "mp4",
    ) -> Callable[[], None]:
        """Queue a conversion job.  Returns immediately.

        Returns a **cancel callable**: calling it signals the worker to stop
        as soon as possible and cleans up any in-progress ``.part`` file.
        ``encode_settings`` is forwarded verbatim to the underlying service,
        enabling GPU encoding and custom quality control.
        """
        cancel_event = threading.Event()

        def _worker() -> None:
            self._semaphore.acquire()
            try:
                if cancel_event.is_set():
                    if on_error:
                        on_error("Đã huỷ")
                    return
                if on_start:
                    on_start()
                self._svc._run(
                    source,
                    quality,
                    output_dir,
                    on_progress,
                    on_done,
                    on_error,
                    encode_settings=encode_settings,
                    cancel_event=cancel_event,
                    target_ext=target_ext,
                )
            finally:
                self._semaphore.release()

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"omnidl-queue-{source.stem[:20]}",
        ).start()

        return cancel_event.set
