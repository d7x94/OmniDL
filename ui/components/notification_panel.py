from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.themes.tokens import T

_MAX_ENTRIES = 5
_AUTO_DISMISS_MS = 8000
_ANIM_MS = 220
_PANEL_W = 260
_PANEL_MAX_H = 400


class NotificationPanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("notification_panel")
        self.setFixedWidth(_PANEL_W)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.hide()

        self._entries: list[QFrame] = []
        self._anim: QPropertyAnimation | None = None

        self._build()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(12, 10, 8, 10)
        hrow.setSpacing(0)

        title = QLabel("THONG BAO")
        title.setStyleSheet(
            f"color: {T.text}; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;"
        )
        hrow.addWidget(title, 1)

        self._clear_btn = QPushButton("Xoa het")
        self._clear_btn.setFixedHeight(22)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T.text3};
                border: none;
                font-size: 11px;
                padding: 0 4px;
            }}
            QPushButton:hover {{ color: {T.text}; }}
        """)
        self._clear_btn.clicked.connect(self.clear_all)
        hrow.addWidget(self._clear_btn)

        root.addWidget(header)

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {T.border};")
        root.addWidget(div)

        # Scroll area for entries
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll.setMaximumHeight(_PANEL_MAX_H - 44)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_widget)
        root.addWidget(self._scroll)

        self._update_size()

    # ── Public API ─────────────────────────────────────────────────────────

    def add_notification(self, ntype: str, title: str, detail: str = "") -> None:
        if len(self._entries) >= _MAX_ENTRIES:
            self._remove_entry(self._entries[0])

        entry = QFrame()
        entry.setObjectName("notification_entry")
        entry.setProperty("type", ntype)
        entry.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        entry.setStyleSheet(f"""
            QFrame#notification_entry {{
                background: {T.surface2};
                border-radius: 6px;
                padding: 0px;
            }}
        """)

        dot_color = {
            "success": T.success,
            "error": T.error,
            "info": T.primary,
        }.get(ntype, T.primary)

        row = QHBoxLayout(entry)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {dot_color}; font-size: 8px; background: transparent;")
        dot.setFixedWidth(10)
        dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        row.addWidget(dot)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        title_lbl = QLabel(title)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"color: {T.text}; font-size: 12px; font-weight: 600; background: transparent;"
        )
        text_col.addWidget(title_lbl)

        if detail:
            detail_lbl = QLabel(detail)
            detail_lbl.setWordWrap(True)
            detail_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
            text_col.addWidget(detail_lbl)

        row.addLayout(text_col, 1)

        dismiss_btn = QPushButton("x")
        dismiss_btn.setFixedSize(16, 16)
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T.text3};
                border: none;
                font-size: 10px;
                padding: 0;
            }}
            QPushButton:hover {{ color: {T.text}; }}
        """)
        dismiss_btn.clicked.connect(lambda: self._remove_entry(entry))
        row.addWidget(dismiss_btn, 0, Qt.AlignmentFlag.AlignTop)

        # Insert before the stretch (last item)
        count = self._list_layout.count()
        self._list_layout.insertWidget(count - 1, entry)
        self._entries.append(entry)

        self._update_size()

        if ntype == "success":
            QTimer.singleShot(_AUTO_DISMISS_MS, lambda: self._remove_entry(entry))

    def toggle(self) -> None:
        if self.isVisible():
            self._slide_out()
        else:
            self._reposition()
            self.show()
            self.raise_()
            self._slide_in()

    def clear_all(self) -> None:
        for entry in list(self._entries):
            self._list_layout.removeWidget(entry)
            entry.deleteLater()
        self._entries.clear()
        self._update_size()
        self._slide_out()

    # ── Positioning ────────────────────────────────────────────────────────

    def _reposition(self) -> None:
        parent = self.parent()
        if not isinstance(parent, QWidget):
            return
        pw = parent.width()
        # 8px margin from right, position below toolbar(68)+top_bar(40)+sep(1)+accent(3)+4gap
        x = pw - _PANEL_W - 8
        y = 116
        self.move(x, y)

    # ── Animation ──────────────────────────────────────────────────────────

    def _slide_in(self) -> None:
        parent = self.parent()
        if not isinstance(parent, QWidget):
            return
        pw = parent.width()
        end_x = pw - _PANEL_W - 8
        start_x = pw  # off-screen right
        y = self.y()
        self.move(start_x, y)
        self._animate(QPoint(start_x, y), QPoint(end_x, y))

    def _slide_out(self) -> None:
        parent = self.parent()
        if not isinstance(parent, QWidget):
            self.hide()
            return
        pw = parent.width()
        end_x = pw
        self._animate(self.pos(), QPoint(end_x, self.y()), on_finish=self.hide)

    def _animate(self, start: QPoint, end: QPoint, on_finish=None) -> None:
        # One animation object reused for the panel's lifetime: creating a new
        # one per toggle left a dead QPropertyAnimation child (plus its finished
        # connection) on the panel every time.
        if self._anim is None:
            self._anim = QPropertyAnimation(self, b"pos", self)
            self._anim.setDuration(_ANIM_MS)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim = self._anim
        anim.stop()
        try:
            anim.finished.disconnect()
        except RuntimeError:
            pass
        anim.setStartValue(start)
        anim.setEndValue(end)
        if on_finish:
            anim.finished.connect(on_finish)
        anim.start()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _remove_entry(self, entry: QFrame) -> None:
        if entry not in self._entries:
            return
        self._entries.remove(entry)
        self._list_layout.removeWidget(entry)
        entry.deleteLater()
        self._update_size()

    def _update_size(self) -> None:
        self._list_widget.adjustSize()
        content_h = self._list_widget.sizeHint().height()
        scroll_h = min(content_h, _PANEL_MAX_H - 44)
        self._scroll.setFixedHeight(max(scroll_h, 0))
        self.adjustSize()
