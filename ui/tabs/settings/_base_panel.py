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

    def _card(self, _parent=None) -> QFrame:
        wrapper = QWidget()
        wrapper.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(28, 0, 28, 10)
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {T.surface}; border: none; border-radius: 12px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        wl.addWidget(card)
        self._layout.addWidget(wrapper)
        return card

    def _slider_row(self, card: QFrame, label_text: str, initial_val: int,
                    lo: int, hi: int, cmd) -> QSlider:
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

    def _switch_row(self, card: QFrame, label_text: str,
                    initial_val: bool, cmd) -> QCheckBox:
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
            f"color: {color or T.text3}; font-size: 11px; background: transparent;"
            " padding: 0 20px 10px;"
        )
        card.layout().addWidget(lbl)
        return lbl

    def _row_label(self, card: QFrame, text: str, color: str = "",
                   wrap: bool = False) -> QLabel:
        lbl = QLabel(text)
        if wrap:
            lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {color or T.text3}; font-size: 11px; background: transparent;"
            " padding: 4px 20px 4px;"
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
