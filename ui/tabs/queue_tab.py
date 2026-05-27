"""Active download queue tab."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from domain.enums.download_status import DownloadStatus
from ui.components.download_item_widget import DownloadItemWidget
from ui.signals import ui_bridge
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class QueueTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._widgets: dict[str, DownloadItemWidget] = {}
        self._build()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

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

        self._title_lbl = QLabel("Hàng đợi tải xuống")
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)

        hdr_layout.addStretch()

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(f"""
            color: {T.text2};
            background-color: {T.surface2};
            border-radius: 8px;
            font-size: 11px;
            padding: 4px 12px;
        """)
        hdr_layout.addWidget(self._count_lbl)

        self._clear_btn = QPushButton("Xóa đã xong")
        self._clear_btn.setFixedHeight(32)
        self._clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.surface2};
                color: {T.text2};
                border: none;
                border-radius: 8px;
                font-size: 11px;
                font-weight: bold;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: {T.surface3};
            }}
        """)
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self._clear_finished)
        hdr_layout.addWidget(self._clear_btn)

        layout.addWidget(hdr)

        # Scroll area for items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._scroll_content)
        self._items_layout.setContentsMargins(28, 0, 28, 20)
        self._items_layout.setSpacing(8)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._empty_lbl = QLabel("Không có tác vụ nào\nDán URL vào tab Tải xuống để bắt đầu")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(f"color: {T.text3}; font-size: 14px;")
        self._items_layout.addWidget(self._empty_lbl)

        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

    def _poll(self) -> None:
        try:
            tasks = self._app.service.get_all_tasks()
        except Exception:
            return

        current_ids = {t.id for t in tasks}

        for tid in list(self._widgets):
            if tid not in current_ids:
                w = self._widgets.pop(tid)
                self._items_layout.removeWidget(w)
                w.deleteLater()

        for task in tasks:
            if task.id not in self._widgets:
                w = DownloadItemWidget(
                    self._scroll_content,
                    task,
                    on_pause=self._on_pause,
                    on_cancel=self._on_cancel,
                    on_convert=lambda p, target_ext="mp4", encode_settings=None, **kw: (
                        self._app.service.convert_to_mp4(
                            p,
                            target_ext=target_ext,
                            encode_settings=encode_settings,
                            **kw,
                        )
                    ),
                    on_send=self._on_send,
                )
                self._items_layout.insertWidget(self._items_layout.count() - 1, w)
                self._widgets[task.id] = w
                w.refresh(task)
            else:
                self._widgets[task.id].refresh(task)

        has_tasks = bool(tasks)
        self._empty_lbl.setVisible(not has_tasks)

        active = sum(1 for t in tasks if t.status in DownloadStatus.active_states())
        self._count_lbl.setText(f"  {active} đang tải  ·  {len(tasks)} tổng  ")

        # Slow down poll when idle
        self._poll_timer.setInterval(500 if active else 2000)

    def _on_pause(self, task_id: str) -> None:
        task = self._app.service.get_task(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            self._app.service.resume_download(task_id)
        else:
            self._app.service.pause_download(task_id)

    def _on_cancel(self, task_id: str) -> None:
        self._app.service.cancel_download(task_id)

    def _clear_finished(self) -> None:
        self._app.service.clear_finished()

    def _on_send(self, file_path, restore_btn, task=None, specific_files=None) -> None:
        nodes = self._app.config.taildrop_target_nodes
        if not nodes:
            self._app.toast("Chưa cấu hình thiết bị đích trong Settings → Taildrop", "warning")
            restore_btn()
            return
        if not self._app.config.taildrop_enabled:
            self._app.toast("Taildrop chưa được bật trong Settings", "warning")
            restore_btn()
            return

        node_list_str = ", ".join(nodes)

        def _on_node_done(node: str) -> None:
            ui_bridge.post(lambda: self._app.toast(f"📲  Đã gửi → {node}", "success"))

        def _on_node_error(node: str, err: str) -> None:
            ui_bridge.post(lambda: self._app.toast(f"❌  Gửi thất bại → {node}: {err[:60]}", "error"))

        try:
            self._app.taildrop.send_file_to_nodes(
                file_path,
                nodes,
                on_node_done=_on_node_done,
                on_node_error=_on_node_error,
                task=task,
                specific_files_override=specific_files,
            )
            self._app.toast(f"📲  Đang gửi đến {len(nodes)} thiết bị: {node_list_str}", "info")
        except Exception as exc:
            logger.warning("QueueTab _on_send error: %s", exc)
            self._app.toast(f"❌  Lỗi gửi file: {exc}", "error")
        finally:
            QTimer.singleShot(800, restore_btn)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
