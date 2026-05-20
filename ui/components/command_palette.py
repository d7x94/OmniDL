from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

COMMANDS = [
    ("↓", "Tải xuống", "home"),
    ("≡", "Hàng đợi", "queue"),
    ("⊞", "Hàng loạt", "batch"),
    ("◉", "Live Monitor", "live_monitor"),
    ("⇄", "Chuyển đổi", "convert"),
    ("◷", "Lịch sử tải", "history"),
    ("⊙", "Cài đặt", "settings"),
    ("◆", "Tải đặc biệt", "special_dl"),
]


class CommandPalette(QDialog):
    def __init__(self, parent=None, on_navigate: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
        self._on_navigate = on_navigate
        self._all_commands = COMMANDS

        self.setObjectName("command_palette")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(420)
        self.setModal(True)

        self._build()
        self._populate(COMMANDS)

        if parent:
            self._center_on_parent()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Tim kiem lenh...")
        self._search.setFixedHeight(44)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.itemActivated.connect(self._execute_selected)
        layout.addWidget(self._list)

    def _populate(self, commands: list) -> None:
        self._list.clear()
        for icon, label, key in commands:
            item = QListWidgetItem(f"{icon}  {label}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _on_search(self, text: str) -> None:
        q = text.lower().strip()
        if not q:
            self._populate(self._all_commands)
            return
        filtered = [c for c in self._all_commands if q in c[1].lower() or q in c[2].lower()]
        self._populate(filtered)

    def _execute_selected(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        if self._on_navigate and key:
            self._on_navigate(key)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._execute_selected()
        elif key == Qt.Key.Key_Down:
            row = self._list.currentRow()
            if row < self._list.count() - 1:
                self._list.setCurrentRow(row + 1)
        elif key == Qt.Key.Key_Up:
            row = self._list.currentRow()
            if row > 0:
                self._list.setCurrentRow(row - 1)
        else:
            super().keyPressEvent(event)

    def _center_on_parent(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        pg = parent.geometry()
        x = pg.x() + (pg.width() - self.width()) // 2
        y = pg.y() + (pg.height() - self.height()) // 3
        self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._search.setFocus()
