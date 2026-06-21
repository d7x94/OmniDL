"""Frame processing pipeline: QVideoFrame → PIL effects + text → QPixmap."""

from __future__ import annotations

import colorsys
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPixmap

from utils.font_finder import find_font_path

if TYPE_CHECKING:
    from PySide6.QtMultimedia import QVideoFrame

_FPS_CAP = 30
_FRAME_INTERVAL = 1.0 / _FPS_CAP
_MAX_PROCESS_HEIGHT = 480  # scale down to 480p before applying effects

_TEXT_COLORS: dict[str, tuple[int, int, int, int]] = {
    "white": (255, 255, 255, 255),
    "black": (0, 0, 0, 255),
    "yellow": (255, 255, 0, 255),
    "red": (255, 0, 0, 255),
}


@dataclass
class EffectParams:
    brightness: float = 0.0  # -1.0 to 1.0 (offset: 0 = no change)
    contrast: float = 1.0  # 0.5 to 3.0
    saturation: float = 1.0  # 0.0 to 3.0
    hue: float = 0.0  # -180 to 180 degrees
    blur: float = 0.0  # 0.0 to 10.0 radius
    text: str = ""
    text_pos: int = 2  # 0=top, 1=middle, 2=bottom
    text_size: int = 24
    text_color: str = "white"
    text_box: bool = False
    text_shadow: bool = False


@lru_cache(maxsize=720)  # covers -180..180 integer degrees
def _make_hue_lut(h_offset_deg: int) -> ImageFilter.Color3DLUT:
    """Build a 17^3 color LUT for hue rotation. Cached per integer degree."""
    h_offset = h_offset_deg / 360.0
    size = 17
    step = 1.0 / (size - 1)
    table: list[float] = []
    for b_i in range(size):
        for g_i in range(size):
            for r_i in range(size):
                r_v = r_i * step
                g_v = g_i * step
                b_v = b_i * step
                h, s, v = colorsys.rgb_to_hsv(r_v, g_v, b_v)
                h = (h + h_offset) % 1.0
                nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
                table.extend([nr, ng, nb])
    return ImageFilter.Color3DLUT(size, table)


def _qframe_to_pil(frame: QVideoFrame) -> Optional[Image.Image]:
    qimg = frame.toImage()
    if qimg.isNull():
        return None
    qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    data = bytes(qimg.bits())
    return Image.frombytes("RGBA", (qimg.width(), qimg.height()), data)


def _scale_to_process_size(img: Image.Image) -> Image.Image:
    w, h = img.size
    if h <= _MAX_PROCESS_HEIGHT:
        return img
    scale = _MAX_PROCESS_HEIGHT / h
    return img.resize((max(1, int(w * scale)), _MAX_PROCESS_HEIGHT), Image.BILINEAR)  # type: ignore[attr-defined]  # BILINEAR alias exists at runtime, removed from stubs in Pillow 10


def _apply_effects(img: Image.Image, params: EffectParams) -> Image.Image:
    """Apply PIL color/blur effects. Input and output are RGBA."""
    rgb = img.convert("RGB")

    if params.brightness != 0.0:
        offset = int(params.brightness * 255)
        lut = [max(0, min(255, i + offset)) for i in range(256)]
        rgb = rgb.point(lut * 3)
    rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, params.contrast))
    rgb = ImageEnhance.Color(rgb).enhance(max(0.0, params.saturation))

    if params.blur > 0.0:
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=params.blur))

    if params.hue != 0.0:
        rgb = rgb.filter(_make_hue_lut(round(params.hue)))

    r, g, b = rgb.split()
    _, _, _, a = img.split()
    return Image.merge("RGBA", (r, g, b, a))


def _draw_text(img: Image.Image, params: EffectParams) -> Image.Image:
    """Draw text overlay. Returns img unchanged if text is empty."""
    if not params.text:
        return img

    draw = ImageDraw.Draw(img)
    w, h = img.size

    font: Optional[ImageFont.FreeTypeFont | ImageFont.ImageFont] = None
    path = find_font_path()
    if path is not None:
        try:
            font = ImageFont.truetype(path, params.text_size)
        except (OSError, IOError):
            pass
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), params.text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    padding = max(6, params.text_size // 4)

    x = (w - tw) // 2
    if params.text_pos == 0:
        y = padding
    elif params.text_pos == 1:
        y = (h - th) // 2  # type: ignore[assignment]  # textbbox stubs return float but runtime is int
    else:
        y = h - th - padding * 2  # type: ignore[assignment]  # textbbox stubs return float but runtime is int

    color = _TEXT_COLORS.get(params.text_color, (255, 255, 255, 255))

    if params.text_box:
        draw.rectangle(
            [x - padding, y - padding // 2, x + tw + padding, y + th + padding // 2],
            fill=(0, 0, 0, 160),
        )
    if params.text_shadow:
        draw.text((x + 2, y + 2), params.text, font=font, fill=(0, 0, 0, 200))

    draw.text((x, y), params.text, font=font, fill=color)
    return img


def _pil_to_pixmap(img: Image.Image, display_size: QSize) -> QPixmap:
    """Convert PIL RGBA image to QPixmap, scaled to fit display_size."""
    iw, ih = img.size
    dw, dh = display_size.width(), display_size.height()
    if dw > 0 and dh > 0:
        scale = min(dw / iw, dh / ih)
        if scale < 1.0:
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            img = img.resize((nw, nh), Image.BILINEAR)  # type: ignore[attr-defined]  # BILINEAR alias exists at runtime, removed from stubs in Pillow 10
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


class FrameProcessor:
    """Throttled video frame pipeline: QVideoFrame → QPixmap with PIL effects."""

    def __init__(self) -> None:
        self.params = EffectParams()
        self._last_time: float = 0.0
        self._last_frame: Optional[QVideoFrame] = None

    def update_params(self, params: EffectParams) -> None:
        self.params = params

    def process(self, frame: QVideoFrame, display_size: QSize) -> Optional[QPixmap]:
        """Process frame with 30fps throttle. Returns None if throttled."""
        now = time.monotonic()
        if now - self._last_time < _FRAME_INTERVAL:
            return None
        self._last_time = now
        self._last_frame = frame
        return self._render(frame, display_size)

    def rerender(self, display_size: QSize) -> Optional[QPixmap]:
        """Re-render last captured frame with current params. Used after slider changes."""
        if self._last_frame is None:
            return None
        return self._render(self._last_frame, display_size)

    def _render(self, frame: QVideoFrame, display_size: QSize) -> Optional[QPixmap]:
        img = _qframe_to_pil(frame)
        if img is None:
            return None
        orig_h = img.size[1]
        img = _scale_to_process_size(img)
        params = self.params
        if orig_h > 0 and img.size[1] != orig_h:
            scale = img.size[1] / orig_h
            params = replace(params, text_size=max(1, round(params.text_size * scale)))
        img = _apply_effects(img, params)
        img = _draw_text(img, params)
        return _pil_to_pixmap(img, display_size)
