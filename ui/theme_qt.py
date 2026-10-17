"""Qt theming: generates QSS from the token palette and applies it to QApplication."""

from __future__ import annotations

from pathlib import Path
from string import Template

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

from ui.themes.tokens import T

_THEMES_DIR = Path(__file__).parent / "themes"


class CheckBoxStyle(QProxyStyle):
    """Paints the checkbox indicator with QPainter instead of a QSS image.

    QStyleSheetStyle's ::indicator image compositing is unreliable on the
    native Windows paint engine — the checkmark silently fails to draw even
    with a valid absolute path and a decoded pixmap. Painting it directly
    sidesteps that engine entirely.
    """

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric in (
            QStyle.PixelMetric.PM_IndicatorWidth,
            QStyle.PixelMetric.PM_IndicatorHeight,
        ):
            return 16
        if metric == QStyle.PixelMetric.PM_CheckBoxLabelSpacing:
            return 8
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element != QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            super().drawPrimitive(element, option, painter, widget)
            return

        checked = bool(option.state & QStyle.StateFlag.State_On)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)

        rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = QColor(T.primary if checked else T.surface2)
        border = QColor(T.primary if (checked or hovered) else T.border2)
        if not enabled:
            fill.setAlpha(120)
            border.setAlpha(120)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(rect, 4, 4)

        if checked:
            pen = QPen(QColor("#FFFFFF"), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            if not enabled:
                pen.setColor(QColor(255, 255, 255, 160))
            painter.setPen(pen)
            x, y, w, h = rect.left(), rect.top(), rect.width(), rect.height()
            p1 = QPointF(x + w * 0.24, y + h * 0.52)
            p2 = QPointF(x + w * 0.42, y + h * 0.72)
            p3 = QPointF(x + w * 0.78, y + h * 0.26)
            painter.drawLine(p1, p2)
            painter.drawLine(p2, p3)
        painter.restore()


def get_stylesheet() -> str:
    return _build_qss()


def apply_theme() -> None:
    app = QApplication.instance()
    if app:
        app.setPalette(_build_palette())
        app.setStyleSheet(_build_qss())


def _build_palette() -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.ColorRole.Window, c(T.bg))
    p.setColor(QPalette.ColorRole.WindowText, c(T.text))
    p.setColor(QPalette.ColorRole.Base, c(T.input))
    p.setColor(QPalette.ColorRole.AlternateBase, c(T.surface))
    p.setColor(QPalette.ColorRole.Button, c(T.surface))
    p.setColor(QPalette.ColorRole.ButtonText, c(T.text))
    p.setColor(QPalette.ColorRole.Highlight, c(T.primary))
    p.setColor(QPalette.ColorRole.HighlightedText, c(T.primary_text))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(T.surface2))
    p.setColor(QPalette.ColorRole.ToolTipText, c(T.text))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(T.text3))
    p.setColor(QPalette.ColorRole.Link, c(T.primary))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, c(T.text3))
    return p


def _build_qss() -> str:
    fname = "dark.qss" if T.is_dark else "light.qss"
    template = Template((_THEMES_DIR / fname).read_text(encoding="utf-8"))
    return template.safe_substitute(T._palette)
