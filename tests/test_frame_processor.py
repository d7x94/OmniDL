"""Tests for FrameProcessor pipeline."""

from __future__ import annotations

import sys

from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)


def _rgba(w: int = 100, h: int = 100, color: tuple = (128, 128, 128, 255)) -> Image.Image:
    return Image.new("RGBA", (w, h), color)


def test_effect_params_defaults():
    from ui.components.frame_processor import EffectParams

    p = EffectParams()
    assert p.brightness == 0.0
    assert p.contrast == 1.0
    assert p.saturation == 1.0
    assert p.hue == 0.0
    assert p.blur == 0.0
    assert p.text == ""
    assert p.text_pos == 2
    assert p.text_size == 24
    assert p.text_color == "white"
    assert p.text_box is False
    assert p.text_shadow is False


def test_apply_effects_noop_preserves_size():
    from ui.components.frame_processor import EffectParams, _apply_effects

    img = _rgba()
    result = _apply_effects(img, EffectParams())
    assert result.size == img.size
    assert result.mode == "RGBA"


def test_apply_brightness_darkens():
    from ui.components.frame_processor import EffectParams, _apply_effects

    img = _rgba(color=(200, 200, 200, 255))
    result = _apply_effects(img, EffectParams(brightness=-0.5))
    r, g, b, _ = result.getpixel((50, 50))
    assert r < 200


def test_apply_blur_runs():
    from ui.components.frame_processor import EffectParams, _apply_effects

    img = _rgba()
    result = _apply_effects(img, EffectParams(blur=2.0))
    assert result.size == img.size


def test_draw_text_empty_returns_same():
    from ui.components.frame_processor import EffectParams, _draw_text

    img = _rgba()
    result = _draw_text(img, EffectParams(text=""))
    assert result is img


def test_draw_text_renders_without_error():
    from ui.components.frame_processor import EffectParams, _draw_text

    img = _rgba(w=400, h=300)
    result = _draw_text(img, EffectParams(text="Hello", text_pos=2, text_color="white"))
    assert result.size == (400, 300)


def test_draw_text_with_box_and_shadow():
    from ui.components.frame_processor import EffectParams, _draw_text

    img = _rgba(w=400, h=300)
    result = _draw_text(img, EffectParams(text="Test", text_box=True, text_shadow=True, text_color="yellow"))
    assert result.size == (400, 300)


def test_pil_to_pixmap_scales_down():
    from ui.components.frame_processor import _pil_to_pixmap

    img = _rgba(w=1920, h=1080)
    pixmap = _pil_to_pixmap(img, QSize(640, 360))
    assert not pixmap.isNull()
    assert pixmap.width() <= 640
    assert pixmap.height() <= 360


def test_pil_to_pixmap_zero_size_returns_valid():
    from ui.components.frame_processor import _pil_to_pixmap

    img = _rgba(w=100, h=100)
    pixmap = _pil_to_pixmap(img, QSize(0, 0))
    assert not pixmap.isNull()


def test_frame_processor_rerender_no_frame_returns_none():
    from ui.components.frame_processor import FrameProcessor

    fp = FrameProcessor()
    assert fp.rerender(QSize(640, 360)) is None


def test_frame_processor_update_params():
    from ui.components.frame_processor import EffectParams, FrameProcessor

    fp = FrameProcessor()
    p = EffectParams(brightness=0.5, text="hello", blur=1.0)
    fp.update_params(p)
    assert fp.params.brightness == 0.5
    assert fp.params.text == "hello"
