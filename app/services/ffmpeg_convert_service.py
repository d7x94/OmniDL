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
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, Literal, Optional

from utils.ffmpeg_locator import locate_ffmpeg

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")

Quality = Literal["high", "standard", "small"]

# ── iPhone-safe preset table ─────────────────────────────────────────────────
_PRESETS: dict[Quality, dict] = {
    "high": {
        "crf":     "18",
        "preset":  "medium",
        "audio_b": "192k",
        "scale":   None,         # keep original resolution
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
        # Scale down: keep aspect ratio, max height 720p, force even dims
        "scale":   (
            "scale='if(gt(ih,720),trunc(iw*720/ih/2)*2,trunc(iw/2)*2)'"
            ":'if(gt(ih,720),720,trunc(ih/2)*2)'"
        ),
        "label":   "File nhỏ (≤720p)",
    },
}


def _parse_seconds(match: re.Match) -> float:
    h, m, s, cs = (int(g) for g in match.groups())
    return h * 3600 + m * 60 + s + cs / 100.0


class ConversionError(RuntimeError):
    """Raised when ffmpeg exits with a non-zero return code."""


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

    def _run(self, source, quality, output_dir, on_progress, on_done, on_error):
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

        # ── Output path ───────────────────────────────────────────────────
        dest_dir = output_dir or source.parent
        dest_dir.mkdir(parents=True, exist_ok=True)

        output = dest_dir / (source.stem + "_iPhone.mp4")
        if output.exists():
            i = 2
            while True:
                candidate = dest_dir / f"{source.stem}_iPhone_{i}.mp4"
                if not candidate.exists():
                    output = candidate
                    break
                i += 1

        # ── Duration probe ────────────────────────────────────────────────
        duration_s = self._probe_duration(ffmpeg_bin, source)

        # ── Build ffmpeg command ──────────────────────────────────────────
        # video filters: even-dimension fix (mandatory for libx264) + optional scale
        vf_parts = ["scale=trunc(iw/2)*2:trunc(ih/2)*2"]
        if preset["scale"]:
            vf_parts = [preset["scale"]]   # scale already forces even dims

        cmd = [
            str(ffmpeg_bin),
            "-y",
            "-i", str(source),
            # Video
            "-c:v", "libx264",
            "-profile:v", "high",     # iPhone 4S+ supports High profile
            "-level:v", "4.0",        # level 4.0 = up to 1080p30
            "-preset", preset["preset"],
            "-crf", preset["crf"],
            "-pix_fmt", "yuv420p",
            "-vf", ",".join(vf_parts),
            # Audio
            "-c:a", "aac",
            "-b:a", preset["audio_b"],
            "-ar", "44100",           # 44.1 kHz — universally supported
            # Container
            "-movflags", "+faststart",
            str(output),
        ]

        logger.info("Convert [%s]: %s → %s", quality, source.name, output.name)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        stderr_lines: list[str] = []
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            stderr_lines.append(line)
            if on_progress and duration_s > 0:
                m = _TIME_RE.search(line)
                if m:
                    pct = min(99.0, _parse_seconds(m) / duration_s * 100.0)
                    on_progress(pct)

        proc.wait()

        if proc.returncode != 0:
            tail = "\n".join(stderr_lines[-10:])
            raise ConversionError(
                f"ffmpeg thoát với lỗi {proc.returncode}.\n{tail}"
            )

        if not output.is_file() or output.stat().st_size < 1_000:
            raise ConversionError(f"File output trống hoặc không tồn tại: {output}")

        size_mb = output.stat().st_size / 1_048_576
        logger.info("Done: %s (%.1f MB)", output.name, size_mb)
        if on_progress:
            on_progress(100.0)
        return output

    # ── Helpers ───────────────────────────────────────────────────────────

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
        try:
            r = subprocess.run(
                [str(ffmpeg_bin), "-i", str(source)],
                capture_output=True, timeout=10,
            )
            text = r.stderr.decode("utf-8", errors="replace")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", text)
            if m:
                return _parse_seconds(m)
        except Exception as exc:
            logger.debug("Duration probe failed: %s", exc)
        return 0.0

