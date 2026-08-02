"""Base class shared by every Settings sub-panel (PySide6)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class _BasePanel(QWidget):
    def __init__(self, parent, app: "MainWindow") -> None:
        super().__init__(parent)
        self._app = app
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._sections: list[tuple[str, str, QWidget, QWidget]] = []
        # (display_text_lower, cfg_key, section_wrapper, content)

    def _section(self, _parent, text: str) -> None:
        self._layout.addSpacing(28)
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(28, 0, 28, 6)
        lbl = QLabel(text)
        lbl.setObjectName("section_title")
        wl.addWidget(lbl)
        self._layout.addWidget(wrapper)

    def _collapsible_section(
        self,
        text: str,
        key: str = "",
        icon: str = "",
        badge: str = "",
        badge_color: str = "",
    ) -> QWidget:
        cfg_key = f"ui.settings.collapsed.{key or text}"
        collapsed = bool(self._app.config.get(cfg_key, False))

        accent = badge_color or T.primary

        section_wrapper = QWidget()
        section_wrapper.setStyleSheet("background: transparent;")
        sw_layout = QVBoxLayout(section_wrapper)
        sw_layout.setContentsMargins(0, 20, 0, 0)
        sw_layout.setSpacing(0)

        # Clickable header row
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(28, 0, 28, 8)
        hl.setSpacing(0)

        # Accent bar
        bar = QFrame()
        bar.setFixedSize(3, 16)
        bar.setStyleSheet(f"background: {accent}; border-radius: 2px; border: none;")
        bar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hl.addWidget(bar)
        hl.addSpacing(8)

        # Icon (optional)
        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet("background: transparent; font-size: 14px;")
            icon_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            hl.addWidget(icon_lbl)
            hl.addSpacing(6)

        # Label
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 11px; font-weight: 600; "
            f"letter-spacing: 0.5px; background: transparent;"
        )
        lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hl.addWidget(lbl)
        hl.addSpacing(10)

        # Divider line (flex)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {T.divider}; border: none;")
        divider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hl.addWidget(divider, 1)

        # Badge (optional)
        if badge:
            hl.addSpacing(8)
            badge_lbl = QLabel(badge)
            badge_lbl.setStyleSheet(
                f"color: {accent}; font-size: 10px; font-weight: 600; "
                f"background: transparent; border: 1px solid {accent}; "
                f"border-radius: 3px; padding: 1px 5px;"
            )
            badge_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            hl.addWidget(badge_lbl)

        # Chevron
        hl.addSpacing(8)
        chevron = QLabel("▼" if collapsed else "▲")
        chevron.setStyleSheet(
            f"color: {T.text3 if collapsed else accent}; font-size: 10px; background: transparent;"
        )
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        hl.addWidget(chevron)

        sw_layout.addWidget(header)

        # Collapsible content area
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        content.setVisible(not collapsed)
        sw_layout.addWidget(content)

        self._layout.addWidget(section_wrapper)
        self._sections.append((text.lower(), cfg_key, section_wrapper, content))

        _state = [collapsed]

        def _toggle():
            _state[0] = not _state[0]
            content.setVisible(not _state[0])
            chevron.setText("▼" if _state[0] else "▲")
            chevron.setStyleSheet(
                f"color: {T.text3 if _state[0] else accent}; font-size: 10px; background: transparent;"
            )
            self._app.config.set(cfg_key, _state[0])

        header.mousePressEvent = lambda e: _toggle()
        return content

    def _card(self, _parent=None, container: QWidget | None = None) -> QFrame:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(28, 0, 28, 10)
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background-color: {T.surface}; border: none; border-radius: 12px; }}")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        wl.addWidget(card)
        if container is not None:
            container.layout().addWidget(wrapper)
        else:
            self._layout.addWidget(wrapper)
        return card

    def _slider_row(self, card: QFrame, label_text: str, initial_val: int, lo: int, hi: int, cmd) -> QSlider:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(20, 14, 20, 2)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; background: transparent;")
        hl.addWidget(lbl, 1)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(initial_val)
        slider.setFixedWidth(160)
        hl.addWidget(slider)
        card.layout().addWidget(row)

        val_row = QWidget()
        val_row.setStyleSheet("background: transparent;")
        vl = QHBoxLayout(val_row)
        vl.setContentsMargins(20, 0, 20, 6)
        vl.addStretch()
        val_lbl = QLabel(str(initial_val))
        val_lbl.setStyleSheet(
            f"color: {T.primary_text}; font-size: 11px; font-weight: 600; background: transparent;"
        )
        vl.addWidget(val_lbl)
        card.layout().addWidget(val_row)

        timer = QTimer(self)
        timer.setSingleShot(True)
        _v = [initial_val]

        def _on_change(v, _lbl=val_lbl, _vref=_v, _t=timer):
            _lbl.setText(str(v))
            _vref[0] = v
            _t.stop()
            _t.start(500)

        timer.timeout.connect(lambda: cmd(_v[0]))
        slider.valueChanged.connect(_on_change)
        return slider

    def _switch_row(self, card: QFrame, label_text: str, initial_val: bool, cmd) -> QCheckBox:
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(20, 10, 20, 10)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {T.text}; font-size: 13px; background: transparent;")
        hl.addWidget(lbl, 1)
        sw = QCheckBox()
        sw.setChecked(initial_val)
        sw.clicked.connect(cmd)
        hl.addWidget(sw)
        card.layout().addWidget(row)
        return sw

    def _hint(self, card: QFrame, text: str, color: str = "") -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        lbl.setStyleSheet(
            f"color: {color or T.text3}; font-size: 11px; background: transparent; padding: 0 20px 10px;"
        )
        card.layout().addWidget(lbl)
        return lbl

    def _row_label(self, card: QFrame, text: str, color: str = "", wrap: bool = False) -> QLabel:
        lbl = QLabel(text)
        if wrap:
            lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {color or T.text3}; font-size: 11px; background: transparent; padding: 4px 20px 4px;"
        )
        card.layout().addWidget(lbl)
        return lbl

    def _separator(self, card: QFrame) -> None:
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T.divider}; border: none;")
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(20, 0, 20, 0)
        wl.addWidget(sep)
        card.layout().addWidget(wrapper)
