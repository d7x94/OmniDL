"""app/services/ffmpeg_trim_service.py — Trim/rotate/mute/speed/volume/text export for editor tab."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from app.services.ffmpeg_convert_service import (
    ConversionCancelledError,
    FfmpegConvertService,
)
from utils.ffmpeg_locator import locate_ffmpeg
from utils.font_finder import find_font_path

logger = logging.getLogger(__name__)

_TEXT_Y_EXPR = {0: "20", 1: "(h-th)/2", 2: "h-th-20"}
_TEXT_COLORS = {"white", "black", "yellow", "red"}


def _font_clause() -> str:
    """Return 'fontfile=...:' for drawtext, or '' if no font found."""
    p = find_font_path()
    if p is None:
        return ""
    escaped = p.replace("C:/", "C\\:/")
    return f"fontfile={escaped}:"


def _escape_drawtext(s: str) -> str:
    s = s.replace("\\", "\\\\\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace("%", "%%")
    return s


def trim_video(
    source: Path,
    output: Path,
    start_ms: int,
    end_ms: int,
    *,
    rotate: Optional[int] = None,
    mute: bool = False,
    speed: float = 1.0,
    volume: float = 1.0,
    text: str = "",
    text_pos: int = 2,
    text_size: int = 24,
    text_color: str = "white",
    text_box: bool = False,
    text_shadow: bool = False,
    brightness: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    hue: float = 0.0,
    blur: float = 0.0,
    fade_in_s: float = 0.0,
    fade_out_s: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    on_done: Optional[Callable[[Path], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> Callable[[], None]:
    cancel_event = threading.Event()

    def _work() -> None:
        temp = output.with_suffix(".part" + output.suffix)
        try:
            loc = locate_ffmpeg()
            if loc is None:
                raise RuntimeError("FFmpeg không tìm thấy. Hãy cài FFmpeg và thử lại.")

            start_s = start_ms / 1000.0
            end_s = end_ms / 1000.0
            duration_s = max(0.1, (end_ms - start_ms) / 1000.0)
            if speed != 1.0:
                duration_s = max(0.1, duration_s / speed)
            needs_reencode = (
                rotate is not None
                or mute
                or speed != 1.0
                or volume != 1.0
                or bool(text)
                or brightness != 0.0
                or contrast != 1.0
                or saturation != 1.0
                or hue != 0.0
                or blur > 0.0
                or fade_in_s > 0.0
                or fade_out_s > 0.0
            )

            cmd = _build_cmd(
                loc.ffmpeg_bin,
                source,
                temp,
                start_s,
                end_s,
                rotate,
                mute,
                needs_reencode,
                speed,
                volume,
                text,
                text_pos,
                text_size,
                text_color,
                text_box,
                text_shadow,
                brightness,
                contrast,
                saturation,
                hue,
                blur,
                fade_in_s,
                fade_out_s,
                duration_s,
            )
            FfmpegConvertService._run_ffmpeg(cmd, duration_s, on_progress, cancel_event=cancel_event)
            temp.rename(output)
            if on_done:
                on_done(output)
        except ConversionCancelledError:
            temp.unlink(missing_ok=True)
            if on_error:
                on_error("Đã huỷ")
        except Exception as exc:
            temp.unlink(missing_ok=True)
            logger.error("trim_video: %s", exc)
            if on_error:
                on_error(str(exc))

    threading.Thread(target=_work, daemon=True, name=f"omnidl-trim-{source.stem[:20]}").start()
    return cancel_event.set


def _build_cmd(
    ffmpeg_bin: str,
    source: Path,
    output: Path,
    start_s: float,
    end_s: float,
    rotate: Optional[int],
    mute: bool,
    needs_reencode: bool,
    speed: float,
    volume: float,
    text: str,
    text_pos: int,
    text_size: int,
    text_color: str,
    text_box: bool,
    text_shadow: bool,
    brightness: float,
    contrast: float,
    saturation: float,
    hue: float,
    blur: float,
    fade_in_s: float,
    fade_out_s: float,
    duration_s: float,
) -> list[str]:
    base = [str(ffmpeg_bin), "-y", "-ss", str(start_s), "-to", str(end_s), "-i", str(source)]
    tail = ["-progress", "pipe:1", "-nostats", "-loglevel", "error", str(output)]

    if not needs_reencode:
        return base + ["-c", "copy", "-avoid_negative_ts", "make_zero"] + tail

    vf: list[str] = []
    if rotate == 1:
        vf.append("transpose=1")
    elif rotate == 2:
        vf.append("transpose=2")
    elif rotate == 3:
        vf += ["hflip", "vflip"]

    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed}")

    if brightness != 0.0 or contrast != 1.0 or saturation != 1.0:
        vf.append(f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}")

    if hue != 0.0:
        vf.append(f"hue=h={hue:.1f}")

    if blur > 0.0:
        vf.append(f"gblur=sigma={blur:.1f}")

    if fade_in_s > 0.0:
        vf.append(f"fade=t=in:st=0:d={fade_in_s:.2f}")
    if fade_out_s > 0.0:
        fade_out_start = max(0.0, duration_s - fade_out_s)
        vf.append(f"fade=t=out:st={fade_out_start:.2f}:d={fade_out_s:.2f}")

    if text:
        color = text_color if text_color in _TEXT_COLORS else "white"
        y_expr = _TEXT_Y_EXPR.get(text_pos, "h-th-20")
        escaped = _escape_drawtext(text)
        box_opts = ":box=1:boxcolor=black@0.5:boxborderw=5" if text_box else ""
        shadow_opts = ":shadowx=2:shadowy=2:shadowcolor=black@0.8" if text_shadow else ""
        vf.append(
            f"drawtext={_font_clause()}text='{escaped}':fontsize={text_size}"
            f":fontcolor={color}:x=(w-tw)/2:y={y_expr}{box_opts}{shadow_opts}"
        )

    af: list[str] = []
    if speed != 1.0:
        af.append(f"atempo={speed}")
    if not mute and volume != 1.0:
        af.append(f"volume={volume}")
    if fade_in_s > 0.0:
        af.append(f"afade=t=in:ss=0:d={fade_in_s:.2f}")
    if fade_out_s > 0.0:
        fade_out_start = max(0.0, duration_s - fade_out_s)
        af.append(f"afade=t=out:st={fade_out_start:.2f}:d={fade_out_s:.2f}")

    cmd = base + ["-c:v", "libx264", "-crf", "23", "-preset", "fast", "-pix_fmt", "yuv420p"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
        if af:
            cmd += ["-af", ",".join(af)]
    return cmd + tail
