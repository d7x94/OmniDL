"""Frame processing pipeline: QVideoFrame → PIL effects + text → QPixmap."""

from __future__ import annotations

import colorsys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPixmap

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

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


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
    return img.resize((max(1, int(w * scale)), _MAX_PROCESS_HEIGHT), Image.BILINEAR)


def _apply_effects(img: Image.Image, params: EffectParams) -> Image.Image:
    """Apply PIL color/blur effects. Input and output are RGBA."""
    rgb = img.convert("RGB")

    factor = max(0.0, 1.0 + params.brightness)
    rgb = ImageEnhance.Brightness(rgb).enhance(factor)
    rgb = ImageEnhance.Contrast(rgb).enhance(max(0.0, params.contrast))
    rgb = ImageEnhance.Color(rgb).enhance(max(0.0, params.saturation))

    if params.blur > 0.0:
        rgb = rgb.filter(ImageFilter.GaussianBlur(radius=params.blur))

    if params.hue != 0.0:
        h_offset = params.hue / 360.0
        pixels = list(rgb.getdata())
        new_pixels = []
        for r, g, b in pixels:
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            h = (h + h_offset) % 1.0
            nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
            new_pixels.append((int(nr * 255), int(ng * 255), int(nb * 255)))
        rgb.putdata(new_pixels)

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
    for path in _FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, params.text_size)
            break
        except (OSError, IOError):
            continue
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
        y = (h - th) // 2
    else:
        y = h - th - padding * 2

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
            img = img.resize((nw, nh), Image.BILINEAR)
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
        img = _scale_to_process_size(img)
        img = _apply_effects(img, self.params)
        img = _draw_text(img, self.params)
        return _pil_to_pixmap(img, display_size)
