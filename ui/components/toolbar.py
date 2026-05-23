"""Persistent top toolbar — URL input + Analyze button always accessible."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.signals import ui_bridge
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

from ui.components.url_utils import _CLIPBOARD_URL_RE, _URL_TRAILING_JUNK

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class Toolbar(QWidget):
    def __init__(self, app: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self._app = app
        self._analysing = False
        self._spinner_idx = 0
        self._analyse_token = 0
        self._current_cancel: Optional[threading.Event] = None

        self.setFixedHeight(68)
        self._build()
        T.register(self._apply_styles)

        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(100)
        self._spinner_timer.timeout.connect(self._tick_spinner)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top border
        top_line = QFrame()
        top_line.setFixedHeight(1)
        top_line.setStyleSheet(f"background-color: {T.border};")
        outer.addWidget(top_line)

        # Inner row
        inner = QWidget()
        inner.setStyleSheet(f"background-color: {T.surface};")
        row = QHBoxLayout(inner)
        row.setContentsMargins(20, 12, 20, 12)
        row.setSpacing(12)

        # URL entry
        self._url_entry = QLineEdit()
        self._url_entry.setPlaceholderText("Dán link video vào đây và nhấn Enter hoặc nhấp Phân tích...")
        self._url_entry.setObjectName("url_entry")
        self._url_entry.setFixedHeight(44)
        self._url_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.input};
                color: {T.text};
                border: 1.5px solid {T.border2};
                border-radius: 12px;
                padding: 0 18px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 2px solid {T.primary};
                background-color: {T.surface};
            }}
        """)
        self._url_entry.returnPressed.connect(self._start_analyse)
        self._url_entry.textChanged.connect(self._on_text_changed)
        row.addWidget(self._url_entry, 1)

        # Analyze button
        self._analyse_btn = QPushButton("Phân tích")
        self._analyse_btn.setObjectName("primary")
        self._analyse_btn.setFixedSize(128, 44)
        self._analyse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.primary};
                color: white;
                border: 1px solid {T.primary_hover};
                border-radius: 12px;
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }}
            QPushButton:hover {{
                background-color: {T.primary_hover};
                border-color: {T.primary};
            }}
            QPushButton:disabled {{
                background-color: {T.primary_dim};
                color: {T.text3};
                border-color: {T.border};
            }}
        """)
        self._analyse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._analyse_btn.clicked.connect(self._start_analyse)
        row.addWidget(self._analyse_btn)

        # Stop button (hidden initially)
        self._stop_btn = QPushButton("Dừng")
        self._stop_btn.setObjectName("danger")
        self._stop_btn.setFixedSize(80, 44)
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.error};
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: #c62828;  /* no error_hover token */
            }}
        """)
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.clicked.connect(self._cancel_analyse)
        self._stop_btn.hide()
        row.addWidget(self._stop_btn)

        # Paste button
        self._paste_btn = QPushButton("⎘")
        self._paste_btn.setFixedSize(40, 40)
        self._paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paste_btn.setToolTip("Dán từ clipboard")
        self._paste_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.surface2};
                color: {T.text2};
                border: none;
                border-radius: 12px;
                font-size: 14px;
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: {T.surface3};
                color: {T.text};
            }}
        """)
        self._paste_btn.clicked.connect(self._paste_clipboard)
        row.addWidget(self._paste_btn)

        # Clear button
        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedSize(40, 40)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setToolTip("Xóa URL")
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.surface2};
                color: {T.text3};
                border: none;
                border-radius: 12px;
                font-size: 13px;
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: {T.surface3};
                color: {T.text};
            }}
        """)
        self._clear_btn.clicked.connect(self._clear_url)
        row.addWidget(self._clear_btn)

        # Status label
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        row.addWidget(self._status_lbl)

        outer.addWidget(inner, 1)

        # Bottom border
        bot_line = QFrame()
        bot_line.setFixedHeight(1)
        bot_line.setStyleSheet(f"background-color: {T.border};")
        outer.addWidget(bot_line)

    # ── Public API ────────────────────────────────────────────────────────

    def set_url(self, url: str) -> None:
        self._url_entry.setText(url)

    def get_url(self) -> str:
        return self._url_entry.text().strip()

    def trigger_from_clipboard(self, url: str) -> None:
        self._url_entry.setText(url)
        self._set_status("URL từ clipboard - nhấn Enter để phân tích", T.text2)

    # ── Analysis flow ─────────────────────────────────────────────────────

    def _start_analyse(self) -> None:
        raw = self.get_url()
        if not raw:
            return
        _m = _CLIPBOARD_URL_RE.search(raw)
        url = _m.group(0).rstrip("".join(_URL_TRAILING_JUNK)) if _m else raw

        if self._current_cancel is not None:
            self._current_cancel.set()
            self._current_cancel = None

        self._analysing = True
        self._analyse_token += 1
        my_token = self._analyse_token

        self._analyse_btn.setText("Đang phân tích...")
        self._analyse_btn.setEnabled(False)
        self._stop_btn.show()
        self._set_status("Đang tải thông tin media...", T.text2)
        self._spinner_timer.start()

        self._app.navigate_to("home")
        home = self._app.get_tab("home")
        if home:
            home.on_analysis_start()

        def _safe_done(info) -> None:
            if my_token != self._analyse_token:
                ui_bridge.post(self._reset_btn)
                return
            ui_bridge.post(lambda: self._on_done(info))

        def _safe_error(err: str) -> None:
            if my_token != self._analyse_token:
                ui_bridge.post(self._reset_btn)
                return
            ui_bridge.post(lambda: self._on_error(err))

        try:
            cancel_event = self._app.service.analyse_url(url=url, on_done=_safe_done, on_error=_safe_error)
            self._current_cancel = cancel_event
        except Exception:
            self._reset_btn()

    def _cancel_analyse(self) -> None:
        if self._current_cancel is not None:
            self._current_cancel.set()
            self._current_cancel = None
        self._analyse_token += 1
        self._set_status("Đã hủy.", T.text3)
        self._reset_btn()

    def _on_done(self, info) -> None:
        self._spinner_timer.stop()
        self._reset_btn()
        if not info or not info.title:
            self._set_status("Không tìm thấy media", T.error)
            self._app.toast("Phân tích không trả về kết quả.", "error")
            return
        self._set_status(f"✓  {info.title[:50]}", T.success)
        home = self._app.get_tab("home")
        if home:
            home.on_analysis_done(info)
        self._app.toast(f"Sẵn sàng: {info.title[:44]}", "success")

    def _on_error(self, err: str) -> None:
        self._spinner_timer.stop()
        self._reset_btn()
        display = err[:100] if err else "Lỗi không xác định"
        self._set_status(f"  {display}", T.error)
        self._app.toast("Phân tích thất bại.", "error")
        home = self._app.get_tab("home")
        if home:
            home.on_analysis_error(err)

    def _reset_btn(self) -> None:
        self._analysing = False
        self._current_cancel = None
        self._spinner_timer.stop()
        self._stop_btn.hide()
        self._analyse_btn.setEnabled(True)
        self._analyse_btn.setText("Phân tích")

    # ── Spinner ───────────────────────────────────────────────────────────

    def _tick_spinner(self) -> None:
        if not self._analysing:
            self._spinner_timer.stop()
            return
        frame = _SPINNER[self._spinner_idx % len(_SPINNER)]
        self._analyse_btn.setText(f"{frame} Đang phân tích")
        self._spinner_idx += 1

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")
        if text.startswith("✓"):
            QTimer.singleShot(4000, lambda: self._status_lbl.setText(""))

    def _on_text_changed(self) -> None:
        pass  # placeholder for future URL validation

    def _paste_clipboard(self) -> None:
        from PySide6.QtGui import QGuiApplication

        text = QGuiApplication.clipboard().text().strip()
        m = _CLIPBOARD_URL_RE.search(text)
        if m:
            url = m.group(0).rstrip("".join(_URL_TRAILING_JUNK))
            self._url_entry.setText(url)
            self._set_status("Đã dán URL", T.text2)
            self._start_analyse()

    def _clear_url(self) -> None:
        self._url_entry.clear()
        self._set_status("", T.text3)
        home = self._app.get_tab("home")
        if home:
            home.clear_result()

    def _apply_styles(self) -> None:
        self._url_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.input};
                color: {T.text};
                border: 1.5px solid {T.border2};
                border-radius: 12px;
                padding: 0 18px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 2px solid {T.primary};
                background-color: {T.surface};
            }}
        """)
        self._analyse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.primary};
                color: white;
                border: 1px solid {T.primary_hover};
                border-radius: 12px;
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }}
            QPushButton:hover {{
                background-color: {T.primary_hover};
                border-color: {T.primary};
            }}
            QPushButton:disabled {{
                background-color: {T.primary_dim};
                color: {T.text3};
                border-color: {T.border};
            }}
        """)
        self._stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.error};
                color: white;
                border: none;
                border-radius: 12px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: #c62828; }}  /* no error_hover token */
        """)
        for btn, color in ((self._paste_btn, T.text2), (self._clear_btn, T.text3)):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {T.surface2};
                    color: {color};
                    border: none;
                    border-radius: 12px;
                    font-size: 13px;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background-color: {T.surface3};
                    color: {T.text};
                }}
            """)
