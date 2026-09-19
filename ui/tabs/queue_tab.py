"""Active download queue tab."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from domain.enums.download_status import DownloadStatus
from ui.components.download_item_widget import DownloadItemWidget
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class QueueTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._widgets: dict[str, DownloadItemWidget] = {}
        self._select_mode = False
        self._selected_ids: set[str] = set()
        self._build()
        T.register(self._on_theme)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll)

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

        self._title_lbl = QLabel(t("queue.title"))
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)

        hdr_layout.addStretch()

        self._count_lbl = QLabel("")
        self._style_count_lbl()
        hdr_layout.addWidget(self._count_lbl)

        self._select_btn = QPushButton(t("queue.select"))
        self._select_btn.setFixedHeight(32)
        self._select_btn.setCheckable(True)
        self._select_btn.setStyleSheet(f"""
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
            QPushButton:checked {{
                background-color: {T.primary_dim};
                color: {T.primary_text};
            }}
        """)
        self._select_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._select_btn.setToolTip(t("queue.select_tip"))
        self._select_btn.clicked.connect(self._toggle_select_mode)
        hdr_layout.addWidget(self._select_btn)

        self._clear_btn = QPushButton(t("queue.clear_finished"))
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

        self._empty_lbl = QLabel(t("queue.empty"))
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(f"color: {T.text3}; font-size: 14px;")
        self._items_layout.addWidget(self._empty_lbl)

        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

    def retranslate(self) -> None:
        self._title_lbl.setText(t("queue.title"))
        self._select_btn.setText(t("queue.select"))
        self._select_btn.setToolTip(t("queue.select_tip"))
        self._empty_lbl.setText(t("queue.empty"))
        self._update_clear_btn_label()
        for w in self._widgets.values():
            w.retranslate()

    def _poll(self) -> None:
        try:
            tasks = self._app.service.get_all_tasks()
        except Exception as exc:
            logger.warning("_poll: %s", exc)
            return

        current_ids = {t.id for t in tasks}

        for tid in list(self._widgets):
            if tid not in current_ids:
                w = self._widgets.pop(tid)
                self._selected_ids.discard(tid)
                self._items_layout.removeWidget(w)
                w.deleteLater()

        for task in tasks:
            if task.id not in self._widgets:
                w = DownloadItemWidget(
                    self._scroll_content,
                    task,
                    on_pause=self._on_pause,
                    on_cancel=self._on_cancel,
                    on_convert=lambda p, **kw: self._app.navigate_to("convert", file_path=str(p)),
                    on_send=self._on_send,
                    on_edit=lambda p: self._app.navigate_to("editor", file_path=str(p)),
                    on_rename=self._on_rename,
                )
                self._items_layout.insertWidget(self._items_layout.count() - 1, w)
                self._widgets[task.id] = w
                w.refresh(task)
                if self._select_mode:
                    w.set_select_mode(True, self._on_item_select)
            else:
                w = self._widgets[task.id]
                w.refresh(task)
                if self._select_mode:
                    # Re-apply: a task that finished since the last poll had no
                    # checkbox while it was running, and nothing else would ever
                    # give it one until select mode was toggled off and on.
                    w.set_select_mode(True, self._on_item_select)

        has_tasks = bool(tasks)
        self._empty_lbl.setVisible(not has_tasks)

        active = sum(1 for t in tasks if t.status in DownloadStatus.active_states())
        self._count_lbl.setText(t("queue.count", active=active, total=len(tasks)))

        # Slow down poll when idle
        self._poll_timer.setInterval(500 if active else 2000)

    def _on_rename(self, task_id: str, current_path: str) -> None:
        old_name = current_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        new_name, ok = QInputDialog.getText(
            self, t("queue.rename_title"), t("queue.rename_label"), text=old_name
        )
        if not ok or not new_name.strip():
            return
        try:
            self._app.service.rename_download(task_id, new_name.strip())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "OmniDL", str(exc))
            return
        w = self._widgets.get(task_id)
        task = self._app.service.get_task(task_id)
        if w and task:
            w.refresh(task)

    def _on_pause(self, task_id: str) -> None:
        task = self._app.service.get_task(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            self._app.service.resume_download(task_id)
        else:
            self._app.service.pause_download(task_id)

    def _on_cancel(self, task_id: str) -> None:
        self._app.service.cancel_download(task_id)

    def _toggle_select_mode(self) -> None:
        self._select_mode = self._select_btn.isChecked()
        self._selected_ids.clear()
        for w in self._widgets.values():
            if self._select_mode:
                w.set_select_mode(True, self._on_item_select)
            else:
                w.set_select_mode(False, None)
        self._update_clear_btn_label()

    def _on_item_select(self, task_id: str, checked: bool) -> None:
        if checked:
            self._selected_ids.add(task_id)
        else:
            self._selected_ids.discard(task_id)
        self._update_clear_btn_label()

    def _update_clear_btn_label(self) -> None:
        if self._select_mode and self._selected_ids:
            self._clear_btn.setText(t("queue.clear_selected", count=len(self._selected_ids)))
        else:
            self._clear_btn.setText(t("queue.clear_finished"))

    def _clear_finished(self) -> None:
        if self._select_mode and self._selected_ids:
            self._app.service.clear_specific(list(self._selected_ids))
            self._selected_ids.clear()
            self._update_clear_btn_label()
        else:
            self._app.service.clear_finished()

    def _on_send(self, file_path, restore_btn, task=None, specific_files=None) -> None:
        nodes = self._app.config.taildrop_target_nodes
        if not nodes:
            self._app.toast(t("queue.no_target"), "warning")
            restore_btn()
            return
        if not self._app.config.taildrop_enabled:
            self._app.toast(t("queue.taildrop_off"), "warning")
            restore_btn()
            return

        node_list_str = ", ".join(nodes)

        def _on_node_done(node: str) -> None:
            ui_bridge.post(lambda: self._app.toast(t("queue.sent_to", node=node), "success"))

        def _on_node_error(node: str, err: str) -> None:
            ui_bridge.post(lambda: self._app.toast(t("queue.send_failed", node=node, err=err[:60]), "error"))

        try:
            self._app.taildrop.send_file_to_nodes(
                file_path,
                nodes,
                on_node_done=_on_node_done,
                on_node_error=_on_node_error,
                task=task,
                specific_files_override=specific_files,
            )
            self._app.toast(t("queue.sending", count=len(nodes), nodes=node_list_str), "info")
        except Exception as exc:
            logger.warning("QueueTab _on_send error: %s", exc)
            self._app.toast(t("queue.send_error", err=str(exc)[:80]), "error")
        finally:
            QTimer.singleShot(800, restore_btn)

    def _style_count_lbl(self) -> None:
        self._count_lbl.setStyleSheet(f"""
            color: {T.text2};
            background-color: {T.surface2};
            border-radius: 8px;
            font-size: 11px;
            padding: 4px 12px;
        """)

    def _on_theme(self) -> None:
        self._style_count_lbl()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._poll_timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._poll_timer.start()
        self._fade_anim.stop()
        self._fade_anim.start()
