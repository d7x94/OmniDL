"""ui/components/timeline_widget.py — Visual timeline for the editor tab (OpenReel-inspired)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QWidget

from ui.themes.tokens import T


class TimelineWidget(QWidget):
    """QPainter timeline widget.

    Replaces the plain QSlider seek bar with a visual clip strip:
    - Full duration shown as a horizontal track bar
    - Trim region (in..out) highlighted in primary colour with film-strip detail
    - Time ruler above (adaptive tick spacing)
    - Draggable In (green) and Out (red) handle triangles
    - Playhead (white vertical line)

    Signals
    -------
    seeked(int ms)      -- user clicked/dragged the track → seek player
    in_changed(int ms)  -- user dragged the In handle
    out_changed(int ms) -- user dragged the Out handle
    """

    seeked: Signal = Signal(int)
    in_changed: Signal = Signal(int)
    out_changed: Signal = Signal(int)

    _RULER_H = 18
    _TRACK_H = 32
    _HANDLE_H = 14
    _HANDLE_ZONE = 8   # px hit radius around each handle

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_ms: int = 0
        self._position_ms: int = 0
        self._in_ms: int = 0
        self._out_ms: int = -1          # -1 means "end of clip"
        self._drag: str | None = None   # 'seek' | 'in' | 'out'
        self.setFixedHeight(self._RULER_H + self._TRACK_H + self._HANDLE_H)
        self.setMouseTracking(True)

    # ── Public API ──────────────────────────────────────────────────────────────

    def set_duration(self, ms: int) -> None:
        self._duration_ms = max(0, ms)
        self.update()

    def set_position(self, ms: int) -> None:
        self._position_ms = max(0, ms)
        self.update()

    def set_in(self, ms: int) -> None:
        self._in_ms = max(0, ms)
        self.update()

    def set_out(self, ms: int) -> None:
        self._out_ms = ms
        self.update()

    def reset(self) -> None:
        self._duration_ms = 0
        self._position_ms = 0
        self._in_ms = 0
        self._out_ms = -1
        self.update()

    # ── Coordinate helpers ──────────────────────────────────────────────────────

    def _ms_to_x(self, ms: int) -> int:
        if self._duration_ms <= 0 or self.width() <= 0:
            return 0
        return int(ms / self._duration_ms * self.width())

    def _x_to_ms(self, x: float) -> int:
        if self._duration_ms <= 0 or self.width() <= 0:
            return 0
        return max(0, min(int(x / self.width() * self._duration_ms), self._duration_ms))

    def _out_ms_eff(self) -> int:
        return self._out_ms if self._out_ms >= 0 else self._duration_ms

    # ── Painting ────────────────────────────────────────────────────────────────

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        total_h = self.height()

        # Widget background
        p.fillRect(0, 0, w, total_h, QColor(T.surface2))

        if self._duration_ms <= 0:
            p.end()
            return

        ty = self._RULER_H  # track y-start

        # Track background
        p.fillRect(0, ty, w, self._TRACK_H, QColor(T.surface3))

        in_x = self._ms_to_x(self._in_ms)
        out_x = self._ms_to_x(self._out_ms_eff())

        # Trim region highlight (OpenReel: clip shown as block on track)
        trim_c = QColor(T.primary)
        trim_c.setAlpha(160)
        trim_w = max(0, out_x - in_x)
        p.fillRect(in_x, ty, trim_w, self._TRACK_H, trim_c)

        # Film-strip vertical lines inside trim region
        line_c = QColor(T.primary)
        line_c.setAlpha(80)
        p.setPen(QPen(line_c, 1))
        fx = in_x + 12
        while fx < out_x - 4:
            p.drawLine(fx, ty + 4, fx, ty + self._TRACK_H - 4)
            fx += 18

        # Time ruler
        self._draw_ruler(p, w)

        hy = ty + self._TRACK_H  # handle y-start

        # In handle (green)
        in_c = QColor(T.success)
        p.setPen(QPen(in_c, 2))
        p.drawLine(in_x, ty, in_x, hy)
        p.setBrush(QBrush(in_c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(
            QPolygon([
                QPoint(in_x - 5, hy),
                QPoint(in_x + 5, hy),
                QPoint(in_x, hy + self._HANDLE_H - 2),
            ])
        )

        # Out handle (red/error colour)
        out_c = QColor(T.error)
        p.setPen(QPen(out_c, 2))
        p.drawLine(out_x, ty, out_x, hy)
        p.setBrush(QBrush(out_c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(
            QPolygon([
                QPoint(out_x - 5, hy),
                QPoint(out_x + 5, hy),
                QPoint(out_x, hy + self._HANDLE_H - 2),
            ])
        )

        # Playhead (white vertical line + circle cap)
        ph_x = self._ms_to_x(self._position_ms)
        ph_c = QColor("white")
        p.setPen(QPen(ph_c, 2))
        p.drawLine(ph_x, 0, ph_x, total_h)
        p.setBrush(QBrush(ph_c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(ph_x - 4, 0, 8, 8)

        p.end()

    def _draw_ruler(self, p: QPainter, w: int) -> None:
        p.fillRect(0, 0, w, self._RULER_H, QColor(T.surface))
        p.setPen(QPen(QColor(T.text3), 1))
        font = QFont()
        font.setPixelSize(9)
        p.setFont(font)

        dur_s = self._duration_ms / 1000.0
        if dur_s <= 30:
            step_s = 1
        elif dur_s <= 120:
            step_s = 5
        elif dur_s <= 600:
            step_s = 30
        else:
            step_s = 60

        t_s = 0
        while t_s <= dur_s:
            x = self._ms_to_x(t_s * 1000)
            p.drawLine(x, self._RULER_H - 5, x, self._RULER_H)
            if w > 200:
                m, s = divmod(t_s, 60)
                p.drawText(x + 2, self._RULER_H - 2, f"{m}:{s:02d}")
            t_s += step_s

    # ── Mouse events ────────────────────────────────────────────────────────────

    def mousePressEvent(self, event: object) -> None:
        if self._duration_ms <= 0:
            return
        x = event.position().x()
        in_x = self._ms_to_x(self._in_ms)
        out_x = self._ms_to_x(self._out_ms_eff())

        if abs(x - in_x) <= self._HANDLE_ZONE:
            self._drag = "in"
        elif abs(x - out_x) <= self._HANDLE_ZONE:
            self._drag = "out"
        else:
            self._drag = "seek"
            self.seeked.emit(self._x_to_ms(x))
        event.accept()

    def mouseMoveEvent(self, event: object) -> None:
        x = event.position().x()
        if self._drag is None or self._duration_ms <= 0:
            # Update cursor hint even without drag
            in_x = self._ms_to_x(self._in_ms)
            out_x = self._ms_to_x(self._out_ms_eff())
            if abs(x - in_x) <= self._HANDLE_ZONE or abs(x - out_x) <= self._HANDLE_ZONE:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return

        ms = self._x_to_ms(x)
        if self._drag == "seek":
            self.seeked.emit(ms)
        elif self._drag == "in":
            ms = max(0, min(ms, self._out_ms_eff() - 100))
            self._in_ms = ms
            self.in_changed.emit(ms)
            self.update()
        elif self._drag == "out":
            ms = max(self._in_ms + 100, min(ms, self._duration_ms))
            self._out_ms = ms
            self.out_changed.emit(ms)
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event: object) -> None:
        self._drag = None
        event.accept()
