"""Searchable, filterable download history tab."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_PAGE_SIZE = 30
_STATUS_TOKENS = {
    "COMPLETED": ("success", "success_bg"),
    "FAILED": ("error", "error_bg"),
    "CANCELLED": ("text3", "surface2"),
}
_FILTER_STATUSES = ["All", "COMPLETED", "FAILED", "CANCELLED"]


def _status_label(status: str) -> str:
    return {
        "All": t("history.filter.all"),
        "COMPLETED": t("history.status.completed"),
        "FAILED": t("history.status.failed"),
        "CANCELLED": t("history.status.cancelled"),
    }.get(status, status)


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
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self.refresh)
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

        self._title_lbl = QLabel(t("history.title"))
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)
        hdr_layout.addStretch()

        self._clear_btn = QPushButton(t("history.clear_all"))
        self._clear_btn.setFixedHeight(32)
        self._clear_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error}; border: none; border-radius: 8px; font-size: 11px; font-weight: 600; padding: 0 14px;"
        )
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self._clear_all)
        hdr_layout.addWidget(self._clear_btn)

        layout.addWidget(hdr)

        # Search + filter row
        ctrl = QWidget()
        ctrl.setStyleSheet("background: transparent;")
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(28, 0, 28, 10)
        ctrl_layout.setSpacing(8)

        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText(t("history.search_placeholder"))
        self._search_entry.setFixedHeight(40)
        self._search_entry.textChanged.connect(self._on_search_change)
        ctrl_layout.addWidget(self._search_entry, 1)

        self._filter_combo = QComboBox()
        for status in _FILTER_STATUSES:
            self._filter_combo.addItem(_status_label(status), status)
        self._filter_combo.setFixedSize(140, 40)
        self._filter_combo.currentIndexChanged.connect(self._on_search_change)
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
        self._debounce.start(300)

    def refresh(self) -> None:
        query = self._search_entry.text().strip()
        status_filter = self._filter_combo.currentData() or "All"

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
                t("history.empty_no_results")
                if (query or status_filter != "All")
                else t("history.empty_no_history")
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
            load_more = QPushButton(t("history.load_more", remaining=remaining))
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

        badge = QLabel(_status_label(status))
        badge.setStyleSheet(
            f"color: {getattr(T, fg_key)}; font-size: 11px; font-weight: 700; background: transparent;"
        )
        top.addWidget(badge)
        card_layout.addLayout(top)

        # Filename
        fname_lbl = None
        if fname:
            fname_lbl = QLabel(fname[-64:] if fname else "—")
            fname_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
            card_layout.addWidget(fname_lbl)
        card._fname_lbl = fname_lbl

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
            re_btn = QPushButton(t("history.redownload"))
            re_btn.setFixedSize(62, 26)
            re_btn.setStyleSheet(f"background: transparent; color: {T.text3}; {_btn_ss}")
            re_btn.setToolTip(t("history.redownload_tip"))
            re_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            re_btn.clicked.connect(lambda _=False, u=url: self._redownload(u))
            bot.addWidget(re_btn)

            copy_btn = QPushButton(t("history.copy_url"))
            copy_btn.setFixedSize(72, 26)
            copy_btn.setStyleSheet(f"background: transparent; color: {T.text3}; {_btn_ss}")
            copy_btn.setToolTip(t("history.copy_url_tip"))
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.clicked.connect(lambda _=False, u=url: QGuiApplication.clipboard().setText(u))
            bot.addWidget(copy_btn)

        if fname:
            open_btn = QPushButton(t("history.folder"))
            open_btn.setFixedSize(72, 28)
            open_btn.setStyleSheet(f"background: {T.success_bg}; color: {T.success}; {_btn_ss}")
            open_btn.setToolTip(t("history.folder_tip"))
            open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            open_btn.clicked.connect(lambda _=False, tid=task_id: self._open_file(tid))
            bot.addWidget(open_btn)

            if not Path(fname).is_dir():
                ren_btn = QPushButton(t("history.rename"))
                ren_btn.setFixedSize(64, 28)
                ren_btn.setStyleSheet(f"background: {T.surface2}; color: {T.text2}; {_btn_ss}")
                ren_btn.setToolTip(t("history.rename_tip"))
                ren_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                ren_btn.clicked.connect(lambda _=False, tid=task_id, c=card: self._rename_entry(tid, c))
                bot.addWidget(ren_btn)

        del_btn = QPushButton(t("history.delete"))
        del_btn.setFixedSize(44, 28)
        del_btn.setStyleSheet(f"background: {T.error_bg}; color: {T.error}; {_btn_ss}")
        del_btn.setToolTip(t("history.delete_tip"))
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda _=False, tid=task_id, c=card: self._delete_entry(tid, c))
        bot.addWidget(del_btn)

        card_layout.addLayout(bot)
        return card

    def _entry_path(self, task_id: str) -> Path | None:
        """Current on-disk path of *task_id*, or None if the entry has no file.

        Resolved at click time rather than captured when the card was built —
        _rename_entry() rewrites entry["filename"] in place, and a lambda
        holding the old name would send Folder to a path that no longer exists.
        A relative filename is joined onto output_dir; Path.resolve() alone
        would anchor it to the process CWD.
        """
        entry = next((e for e in self._entries if e.get("id") == task_id), None)
        raw = (entry or {}).get("filename") or ""
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            out_dir = entry.get("output_dir") or ""
            if out_dir:
                p = Path(out_dir) / p
        return p.resolve()

    def _open_file(self, task_id: str) -> None:
        p = self._entry_path(task_id)
        if p is None:
            return
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
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get("id") != task_id]
        self._rendered -= before - len(self._entries)
        card.setParent(None)
        card.deleteLater()
        if not self._entries:
            # Nothing left to show — re-render so the "no history" / "no
            # results" placeholder appears instead of an empty scroll area.
            self.refresh()

    def _rename_entry(self, task_id: str, card: QFrame) -> None:
        entry = next((e for e in self._entries if e.get("id") == task_id), None)
        if entry is None:
            return
        old_path = Path(entry.get("filename", ""))
        new_name, ok = QInputDialog.getText(
            self, t("queue.rename_title"), t("queue.rename_label"), text=old_path.name
        )
        if not ok or not new_name.strip():
            return
        try:
            new_path = self._app.service.rename_download(task_id, new_name.strip())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "OmniDL", str(exc))
            return
        entry["filename"] = new_path
        fname_lbl = getattr(card, "_fname_lbl", None)
        if fname_lbl is not None:
            fname_lbl.setText(new_path[-64:])

    def _clear_all(self) -> None:
        reply = QMessageBox.question(
            self,
            "OmniDL",
            t("history.clear_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._app.service.clear_history()
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()

    def retranslate(self) -> None:
        self._title_lbl.setText(t("history.title"))
        self._clear_btn.setText(t("history.clear_all"))
        self._search_entry.setPlaceholderText(t("history.search_placeholder"))

        current_status = self._filter_combo.currentData() or "All"
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        for status in _FILTER_STATUSES:
            self._filter_combo.addItem(_status_label(status), status)
        idx = self._filter_combo.findData(current_status)
        if idx >= 0:
            self._filter_combo.setCurrentIndex(idx)
        self._filter_combo.blockSignals(False)

        self.refresh()
