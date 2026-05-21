"""Custom progress bar with gradient fill and indeterminate animation."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ui.themes.tokens import T


class OmniProgressBar(QWidget):
    _HEIGHT = 6
    _RADIUS = 3
    _ANIM_PILL  = 0.25
    _ANIM_RANGE = 0.75
    _ANIM_STEP  = 0.015

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._state = "active"
        self._anim_pos = 0.0
        self._anim_dir = 1

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._bar = _BarCanvas(self)
        self._bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._bar.setFixedHeight(self._HEIGHT)
        layout.addWidget(self._bar)

        self._pct_lbl = QLabel("  0%", self)
        self._pct_lbl.setFixedWidth(40)
        self._pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._pct_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; font-weight: bold;")
        layout.addWidget(self._pct_lbl)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick_anim)

        T.register(self._on_theme)

    def set_progress(self, value: float) -> None:
        self._value = max(0.0, min(100.0, value))
        color = T.text if self._value >= 100 else T.text3
        self._pct_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self._pct_lbl.setText(f"{self._value:.0f}%")
        if self._value > 0:
            self._timer.stop()
        self._bar.update_state(self._value, self._state, self._anim_pos)

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "active" and self._value == 0:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self._bar.update_state(self._value, self._state, self._anim_pos)

    def _tick_anim(self) -> None:
        self._anim_pos += self._ANIM_STEP * self._anim_dir
        if self._anim_pos >= self._ANIM_RANGE:
            self._anim_pos = self._ANIM_RANGE
            self._anim_dir = -1
        elif self._anim_pos <= 0.0:
            self._anim_pos = 0.0
            self._anim_dir = 1
        self._bar.update_state(self._value, self._state, self._anim_pos)
        if not (self._state == "active" and self._value == 0):
            self._timer.stop()

    def _on_theme(self) -> None:
        color = T.text if self._value >= 100 else T.text3
        self._pct_lbl.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: bold;")
        self._bar.update()


class _BarCanvas(QWidget):
    _ANIM_PILL = 0.25

    def __init__(self, parent: OmniProgressBar) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._state = "active"
        self._anim_pos = 0.0

    def update_state(self, value: float, state: str, anim_pos: float) -> None:
        self._value = value
        self._state = state
        self._anim_pos = anim_pos
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = OmniProgressBar._RADIUS

        # Track
        self._draw_pill(painter, 0, w, h, r, QColor(T.prog_track))

        if self._state == "active" and self._value == 0:
            pill_w = int(w * self._ANIM_PILL)
            x0 = int(w * self._anim_pos)
            x1 = min(x0 + pill_w, w)
            if x1 - x0 >= r * 2:
                self._draw_gradient_pill(painter, x0, x1, h, r)
            return

        fill_w = int(w * self._value / 100)
        if fill_w < r * 2:
            return

        if self._state == "complete":
            self._draw_pill(painter, 0, fill_w, h, r, QColor(T.success))
        elif self._state == "failed":
            self._draw_pill(painter, 0, fill_w, h, r, QColor(T.error))
        elif self._state == "paused":
            self._draw_pill(painter, 0, fill_w, h, r, QColor(T.text3))
        else:
            self._draw_gradient_pill(painter, 0, fill_w, h, r)

    def _draw_pill(self, painter: QPainter, x0: int, x1: int,
                   h: int, r: int, color: QColor) -> None:
        if x1 - x0 <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(x0, 0, x1 - x0, h, r, r)
        painter.fillPath(path, color)

    def _draw_gradient_pill(self, painter: QPainter, x0: int, x1: int,
                             h: int, r: int) -> None:
        if x1 - x0 <= 0:
            return
        grad = QLinearGradient(x0, 0, x1, 0)
        grad.setColorAt(0, QColor(T.prog_start))
        grad.setColorAt(1, QColor(T.prog_end))
        path = QPainterPath()
        path.addRoundedRect(x0, 0, x1 - x0, h, r, r)
        painter.fillPath(path, grad)
