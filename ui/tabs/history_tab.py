"""Searchable, filterable download history tab."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.themes.tokens import T
from utils.helpers import open_folder, reveal_in_explorer

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_PAGE_SIZE = 30
_STATUS_TOKENS = {
    "COMPLETED": ("success", "success_bg"),
    "FAILED": ("error", "error_bg"),
    "CANCELLED": ("text3", "surface2"),
}
_FILTER_OPTIONS = ["Tất cả", "COMPLETED", "FAILED", "CANCELLED"]
_FILTER_MAP = {"Tất cả": "All", "COMPLETED": "COMPLETED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}
_STATUS_VI = {"COMPLETED": "Hoàn tất", "FAILED": "Lỗi", "CANCELLED": "Đã hủy"}


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_date(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%y %H:%M")
    except Exception:
        return ""


class HistoryTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._entries: list[dict] = []
        self._rendered: int = 0
        self._debounce: Optional[QTimer] = None
        self._build()

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 14)

        title = QLabel("Lịch sử tải xuống")
        title.setObjectName("page_title")
        hdr_layout.addWidget(title)
        hdr_layout.addStretch()

        clear_btn = QPushButton("Xóa tất cả")
        clear_btn.setFixedHeight(32)
        clear_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error}; border: none; border-radius: 8px; font-size: 11px; font-weight: 600; padding: 0 14px;"
        )
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_all)
        hdr_layout.addWidget(clear_btn)

        layout.addWidget(hdr)

        # Search + filter row
        ctrl = QWidget()
        ctrl.setStyleSheet("background: transparent;")
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(28, 0, 28, 10)
        ctrl_layout.setSpacing(8)

        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText("Tìm theo tiêu đề, URL hoặc tên file...")
        self._search_entry.setFixedHeight(40)
        self._search_entry.textChanged.connect(self._on_search_change)
        ctrl_layout.addWidget(self._search_entry, 1)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(_FILTER_OPTIONS)
        self._filter_combo.setFixedSize(140, 40)
        self._filter_combo.currentTextChanged.connect(self._on_search_change)
        ctrl_layout.addWidget(self._filter_combo)

        layout.addWidget(ctrl)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._scroll_content)
        self._items_layout.setContentsMargins(28, 0, 28, 20)
        self._items_layout.setSpacing(12)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

    def _on_search_change(self) -> None:
        if self._debounce:
            self._debounce.stop()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
        self._debounce.start(300)

    def refresh(self) -> None:
        query = self._search_entry.text().strip()
        status_filter = _FILTER_MAP.get(self._filter_combo.currentText(), "All")

        entries = self._app.service.search_history(query) if query else self._app.service.get_history()

        if status_filter != "All":
            entries = [e for e in entries if e.get("status") == status_filter]

        self._entries = entries
        self._rendered = 0

        # Clear existing items
        while self._items_layout.count():
            item = self._items_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not entries:
            empty = QLabel(
                "Không tìm thấy kết quả" if (query or status_filter != "All") else "Chưa có lịch sử tải xuống"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {T.text3}; font-size: 14px; background: transparent;")
            empty.setMinimumHeight(200)
            self._items_layout.addWidget(empty)
            return

        self._render_page()

    def _render_page(self) -> None:
        batch = self._entries[self._rendered : self._rendered + _PAGE_SIZE]
        for entry in batch:
            row = self._make_row(entry)
            self._items_layout.addWidget(row)
        self._rendered += len(batch)

        remaining = len(self._entries) - self._rendered
        if remaining > 0:
            load_more = QPushButton(f"Tải thêm ({remaining} mục còn lại)")
            load_more.setFixedHeight(36)
            load_more.setStyleSheet(
                f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 12px; font-weight: 500; padding: 0 16px;"
            )
            load_more.clicked.connect(lambda: self._load_more(load_more))
            self._items_layout.addWidget(load_more)

    def _load_more(self, btn: QPushButton) -> None:
        self._items_layout.removeWidget(btn)
        btn.deleteLater()
        self._render_page()

    def _make_row(self, entry: dict) -> QFrame:
        status = entry.get("status", "")
        fg_key, bg_key = _STATUS_TOKENS.get(status, ("text3", "surface2"))
        task_id = entry.get("id", "")
        url = entry.get("url", "")
        fname = entry.get("filename", "")
        platform = entry.get("platform", "")
        size_str = _fmt_bytes(entry.get("downloaded_bytes", 0))
        date_str = _fmt_date(entry.get("finished_at", 0))

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface};
                border: none;
                border-radius: 12px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 14)
        card_layout.setSpacing(5)

        # Title + status badge
        top = QHBoxLayout()
        if entry.get("is_live", False):
            live_lbl = QLabel("LIVE")
            live_lbl.setStyleSheet(
                "background: #e53935; color: #fff; font-size: 10px; font-weight: 700;"
                "border-radius: 4px; padding: 1px 6px; letter-spacing: 0.5px;"
            )
            top.insertWidget(0, live_lbl)
        title_lbl = QLabel((entry.get("title") or url)[:72])
        title_lbl.setStyleSheet(
            f"color: {T.text}; font-size: 13px; font-weight: 600; background: transparent;"
        )
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(title_lbl)

        badge_text = _STATUS_VI.get(status, status)
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"color: {getattr(T, fg_key)}; font-size: 11px; font-weight: 700; background: transparent;"
        )
        top.addWidget(badge)
        card_layout.addLayout(top)

        # Filename
        if fname:
            fname_lbl = QLabel(fname[-64:] if fname else "—")
            fname_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
            card_layout.addWidget(fname_lbl)

        # Meta + actions row
        bot = QHBoxLayout()
        meta_parts = [p for p in [platform, size_str, date_str] if p]
        if meta_parts:
            meta_lbl = QLabel("  •  ".join(meta_parts))
            meta_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
            bot.addWidget(meta_lbl)
        bot.addStretch()

        # Action buttons
        _btn_ss = "font-size: 11px; font-weight: 600; border: none; border-radius: 6px; padding: 0 4px;"
        if url:
            re_btn = QPushButton("Tải lại")
            re_btn.setFixedSize(62, 26)
            re_btn.setStyleSheet(f"background: transparent; color: {T.text3}; {_btn_ss}")
            re_btn.setToolTip("Tải xuống lại")
            re_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            re_btn.clicked.connect(lambda _=False, u=url: self._redownload(u))
            bot.addWidget(re_btn)

            copy_btn = QPushButton("Copy URL")
            copy_btn.setFixedSize(72, 26)
            copy_btn.setStyleSheet(f"background: transparent; color: {T.text3}; {_btn_ss}")
            copy_btn.setToolTip("Sao chép URL")
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.clicked.connect(lambda _=False, u=url: QGuiApplication.clipboard().setText(u))
            bot.addWidget(copy_btn)

        if fname:
            open_btn = QPushButton("Thư mục")
            open_btn.setFixedSize(72, 28)
            open_btn.setStyleSheet(f"background: {T.success_bg}; color: {T.success}; {_btn_ss}")
            open_btn.setToolTip("Mở thư mục chứa file")
            open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_btn.clicked.connect(lambda _=False, f=fname: self._open_file(f))
            bot.addWidget(open_btn)

        del_btn = QPushButton("Xóa")
        del_btn.setFixedSize(44, 28)
        del_btn.setStyleSheet(f"background: {T.error_bg}; color: {T.error}; {_btn_ss}")
        del_btn.setToolTip("Xóa khỏi lịch sử")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda _=False, tid=task_id, c=card: self._delete_entry(tid, c))
        bot.addWidget(del_btn)

        card_layout.addLayout(bot)
        return card

    def _open_file(self, path_str: str) -> None:
        p = Path(path_str).resolve()
        if p.is_file():
            if not reveal_in_explorer(p):
                open_folder(p.parent)
        elif p.parent.is_dir():
            open_folder(p.parent)

    def _redownload(self, url: str) -> None:
        toolbar = self._app.get_toolbar()
        if toolbar:
            toolbar.set_url(url)
        self._app.navigate_to("home")

    def _delete_entry(self, task_id: str, card: QFrame) -> None:
        self._app.service.delete_history_entry(task_id)
        card.setParent(None)
        card.deleteLater()

    def _clear_all(self) -> None:
        reply = QMessageBox.question(
            self,
            "OmniDL",
            "Xóa toàn bộ lịch sử tải xuống?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._app.service.clear_history()
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
