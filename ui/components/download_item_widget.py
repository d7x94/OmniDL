"""One row per DownloadTask — card layout with progress and action buttons."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from ui.components.progress_bar import OmniProgressBar
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, open_file, open_folder, reveal_in_explorer

logger = logging.getLogger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts", ".flv", ".wmv"})

_STATUS: dict = {
    DownloadStatus.QUEUED: ("Chờ", "text3", "surface3"),
    DownloadStatus.DOWNLOADING: ("Đang tải", "primary", "primary_dim"),
    DownloadStatus.PROCESSING: ("Xử lý", "warning", "warning_bg"),
    DownloadStatus.PAUSED: ("Tạm dừng", "text3", "surface3"),
    DownloadStatus.COMPLETED: ("Hoàn tất", "success", "success_bg"),
    DownloadStatus.FAILED: ("Lỗi", "error", "error_bg"),
    DownloadStatus.CANCELLED: ("Đã hủy", "text3", "surface2"),
    DownloadStatus.PARTIAL_SAVED: ("Lưu tạm", "warning", "warning_bg"),
}

_PROG_STATE: dict = {
    DownloadStatus.QUEUED: "active",
    DownloadStatus.DOWNLOADING: "active",
    DownloadStatus.PROCESSING: "active",
    DownloadStatus.PAUSED: "paused",
    DownloadStatus.COMPLETED: "complete",
    DownloadStatus.FAILED: "failed",
    DownloadStatus.CANCELLED: "failed",
    DownloadStatus.PARTIAL_SAVED: "complete",
}


class DownloadItemWidget(QFrame):
    def __init__(
        self,
        parent,
        task: DownloadTask,
        on_pause: Callable,
        on_cancel: Callable,
        on_convert: Optional[Callable] = None,
        on_send: Optional[Callable] = None,
        on_edit: Optional[Callable] = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self._on_pause = on_pause
        self._on_cancel = on_cancel
        self._on_convert = on_convert
        self._on_send = on_send
        self._on_edit = on_edit
        self._completed_path: str = ""
        self._converting = False
        self._checkbox = None  # QCheckBox, created by set_select_mode

        self.setObjectName("download_card")
        self._build()
        self._set_active_accent(False)
        self._theme_cb = self._on_theme
        T.register(self._theme_cb)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(9)

        # Row 1: dot + title + badge + buttons
        top = QHBoxLayout()
        top.setSpacing(8)

        self._type_dot = QLabel("●")
        self._type_dot.setStyleSheet(f"color: {T.primary}; font-size: 8px;")
        top.addWidget(self._type_dot)

        self._live_badge = QLabel("LIVE")
        self._live_badge.setObjectName("live_badge")
        self._live_badge.setStyleSheet(
            f"background: {T.error}; color: #fff; font-size: 10px; font-weight: 700;"
            "border-radius: 4px; padding: 1px 6px; letter-spacing: 0.5px;"
        )
        self._live_badge.setVisible(False)
        top.addWidget(self._live_badge)

        self._title_lbl = QLabel(self._trunc(self.task.title, 72))
        self._title_lbl.setStyleSheet(f"color: {T.text}; font-size: 14px; font-weight: 600;")
        self._title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(self._title_lbl, 1)

        self._status_badge = QLabel("Chờ")
        self._status_badge.setStyleSheet(f"""
            color: {T.text3}; background-color: {T.surface3};
            border-radius: 8px; font-size: 10px; font-weight: 600;
            padding: 3px 10px; letter-spacing: 0.2px;
        """)
        top.addWidget(self._status_badge)

        # Button box (right side)
        self._btn_box = QWidget()
        self._btn_box.setObjectName("btn_box")
        self._btn_box.setStyleSheet("#btn_box { background: transparent; }")
        btn_row = QHBoxLayout(self._btn_box)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)

        self._pause_btn = QPushButton("⏸")
        self._pause_btn.setFixedSize(30, 30)
        self._pause_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.primary_dim}; color: {T.primary_text};
                border-radius: 10px; border: 1.5px solid {T.primary}; font-size: 12px;
                padding: 0;
                font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;
            }}
            QPushButton:hover {{ background: {T.primary}; color: white; }}
        """)
        self._pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause_btn.setToolTip("Tạm dừng / Tiếp tục")
        self._pause_btn.clicked.connect(lambda: self._on_pause(self.task.id))
        btn_row.addWidget(self._pause_btn)

        self._cancel_btn = QPushButton("✕")
        self._cancel_btn.setFixedSize(30, 30)
        self._cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.error_bg}; color: {T.error_text};
                border-radius: 10px; border: 1.5px solid {T.error}; font-size: 12px;
                padding: 0;
                font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;
            }}
            QPushButton:hover {{ background: {T.error}; color: white; }}
        """)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setToolTip("Hủy tải xuống")
        self._cancel_btn.clicked.connect(self._on_cancel_click)
        btn_row.addWidget(self._cancel_btn)

        self._folder_btn = QPushButton("📂  Mở")
        self._folder_btn.setFixedHeight(30)
        self._folder_btn.setStyleSheet(
            f"background: {T.success_bg}; color: {T.success_text}; border-radius: 10px; border: none; font-size: 11px; font-weight: 600; padding: 0 12px;"
            f' font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;'
        )
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.setToolTip("Mở thư mục chứa file")
        self._folder_btn.clicked.connect(self._open_folder)
        self._folder_btn.hide()
        btn_row.addWidget(self._folder_btn)

        self._preview_btn = QPushButton("▶  Xem")
        self._preview_btn.setFixedHeight(30)
        self._preview_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border-radius: 10px; border: none; font-size: 11px; font-weight: 600; padding: 0 12px;"
            f' font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;'
        )
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.setToolTip("Xem / phát file")
        self._preview_btn.clicked.connect(self._open_preview)
        self._preview_btn.hide()
        btn_row.addWidget(self._preview_btn)

        self._convert_btn = QPushButton("🔄  Chuyển")
        self._convert_btn.setFixedHeight(30)
        self._convert_btn.setStyleSheet(
            f"background: {T.warning_bg}; color: {T.warning}; border-radius: 10px; border: none;"
            f" font-size: 11px; font-weight: 600; padding: 0 12px;"
            f' font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;'
        )
        self._convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_btn.setToolTip("Chuyển đổi định dạng")
        self._convert_btn.clicked.connect(self._on_convert_click)
        self._convert_btn.hide()
        btn_row.addWidget(self._convert_btn)

        self._edit_btn = QPushButton("✂  Sửa")
        self._edit_btn.setFixedHeight(30)
        self._edit_btn.setStyleSheet(
            f"background: {T.edit_bg}; color: {T.edit_text}; border-radius: 10px; border: none;"
            f" font-size: 11px; font-weight: 600; padding: 0 12px;"
            f' font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;'
        )
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.setToolTip("Cắt / chỉnh sửa video")
        self._edit_btn.clicked.connect(self._on_edit_click)
        self._edit_btn.hide()
        btn_row.addWidget(self._edit_btn)

        top.addWidget(self._btn_box)
        outer.addLayout(top)

        # Row 2: progress
        self._prog = OmniProgressBar(self)
        outer.addWidget(self._prog)

        # Row 3: stats
        stats = QHBoxLayout()
        stats.setSpacing(12)

        self._speed_lbl = QLabel("")
        self._speed_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; font-weight: 500;")
        stats.addWidget(self._speed_lbl)

        self._eta_lbl = QLabel("")
        self._eta_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        stats.addWidget(self._eta_lbl)

        self._elapsed_lbl = QLabel("")
        self._elapsed_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        stats.addWidget(self._elapsed_lbl)

        stats.addStretch()

        self._size_lbl = QLabel("")
        self._size_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        stats.addWidget(self._size_lbl)

        outer.addLayout(stats)

        # Error / URL label (hidden by default)
        self._url_lbl = QLabel("")
        self._url_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        self._url_lbl.setWordWrap(True)
        self._url_lbl.hide()
        outer.addWidget(self._url_lbl)

        self._err_lbl = QLabel("")
        self._err_lbl.setStyleSheet(f"color: {T.error_text}; font-size: 11px;")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.hide()
        outer.addWidget(self._err_lbl)

    def refresh(self, task: DownloadTask) -> None:
        self.task = task
        st = task.status
        s_label, s_dot_key, s_bg_key = _STATUS.get(st, ("Unknown", "text3", "surface2"))

        self._title_lbl.setText(self._trunc(task.title, 72))

        is_live = bool(getattr(task.media_info, "is_live", False)) if task.media_info else False
        self._live_badge.setVisible(is_live)

        s_color = getattr(T, s_dot_key)
        s_bg = getattr(T, s_bg_key)
        self._status_badge.setText(s_label)
        self._status_badge.setStyleSheet(f"""
            color: {s_color}; background-color: {s_bg};
            border-radius: 8px; font-size: 10px; font-weight: 600;
            padding: 3px 10px; letter-spacing: 0.2px;
        """)
        self._type_dot.setStyleSheet(f"color: {s_color}; font-size: 8px;")

        self._prog.set_progress(task.progress)
        self._prog.set_state(_PROG_STATE.get(st, "active"))

        self._speed_lbl.setText(f"↓  {task.speed}" if task.speed else "")
        self._eta_lbl.setText(f"ETA  {task.eta}" if task.eta else "")
        elapsed = task.elapsed
        self._elapsed_lbl.setText(
            f"⏱  {elapsed}" if elapsed and st not in DownloadStatus.terminal_states() else ""
        )

        if task.total_bytes > 0:
            self._size_lbl.setText(f"{fmt_bytes(task.downloaded_bytes)}  /  {fmt_bytes(task.total_bytes)}")
        elif task.downloaded_bytes > 0:
            self._size_lbl.setText(fmt_bytes(task.downloaded_bytes))
        else:
            self._size_lbl.setText("")

        if st == DownloadStatus.FAILED:
            url_display = getattr(task, "url", "")
            if url_display:
                self._url_lbl.setText(f"🔗 {url_display[:100]}")
                self._url_lbl.show()
            if task.error_msg:
                self._err_lbl.setText(task.error_msg)
                self._err_lbl.show()
        else:
            self._url_lbl.hide()
            self._err_lbl.hide()

        terminal = st in DownloadStatus.terminal_states()
        processing = st == DownloadStatus.PROCESSING
        is_gallery_dl = (
            task.media_info is not None
            and getattr(task.media_info, "source_engine", "yt_dlp") == "gallery_dl"
        )

        if terminal:
            self._pause_btn.hide()
            self._cancel_btn.hide()
        elif processing:
            self._pause_btn.hide()
            self._cancel_btn.setEnabled(True)
            self._cancel_btn.show()
        else:
            self._pause_btn.setEnabled(not is_gallery_dl)
            self._cancel_btn.setEnabled(True)
            self._pause_btn.setText("▶" if st == DownloadStatus.PAUSED else "⏸")
            self._pause_btn.show()
            self._cancel_btn.show()

        if st in {DownloadStatus.COMPLETED, DownloadStatus.PARTIAL_SAVED} and task.filename:
            if not self._completed_path:
                self._completed_path = task.filename
            self._folder_btn.show()
            self._preview_btn.show()
            if self._on_convert and not self._converting:
                self._convert_btn.show()
            if self._on_edit and Path(self._completed_path).suffix.lower() in _VIDEO_EXTS:
                self._edit_btn.show()
            self._pause_btn.hide()
            self._cancel_btn.hide()
        elif not terminal:
            self._completed_path = ""
            self._folder_btn.hide()
            self._preview_btn.hide()
            self._convert_btn.hide()
            self._edit_btn.hide()

        is_active = st in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING, DownloadStatus.QUEUED)
        self._set_active_accent(is_active)

    def _set_active_accent(self, active: bool) -> None:
        if active:
            self.setStyleSheet(f"""
                QFrame#download_card {{
                    background-color: {T.surface};
                    border: 1px solid {T.border};
                    border-left: 3px solid {T.primary};
                    border-radius: 14px;
                }}
                QFrame#download_card:hover {{
                    background-color: {T.card_hover};
                    border-color: {T.border2};
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame#download_card {{
                    background-color: {T.surface};
                    border: 1px solid {T.border};
                    border-radius: 14px;
                }}
                QFrame#download_card:hover {{
                    background-color: {T.card_hover};
                    border-color: {T.border2};
                }}
            """)

    def _on_theme(self) -> None:
        self.refresh(self.task)

    def _on_cancel_click(self) -> None:
        if (
            self.task.media_info
            and self.task.media_info.is_live
            and self.task.status in DownloadStatus.active_states()
        ):
            self.task.keep_partial = True
        self._on_cancel(self.task.id)

    def _on_edit_click(self) -> None:
        if not self._completed_path or not self._on_edit:
            return
        try:
            self._on_edit(Path(self._completed_path))
        except Exception as exc:
            logger.warning("on_edit raised: %s", exc)

    def _on_convert_click(self) -> None:
        if not self._completed_path or not self._on_convert:
            return
        try:
            self._on_convert(Path(self._completed_path))
        except Exception as exc:
            logger.warning("on_convert raised: %s", exc)

    def _open_preview(self) -> None:
        p_str = self._completed_path or getattr(self.task, "filename", "")
        if not p_str:
            return
        p = Path(p_str)
        if not p.is_absolute() and getattr(self.task, "output_dir", ""):
            p = Path(self.task.output_dir) / p
        if p.exists():
            open_file(p)
        else:
            open_folder(p.parent)

    def _open_folder(self) -> None:
        raw = self._completed_path or self.task.filename
        if raw:
            _raw_p = Path(raw)
            if _raw_p.is_absolute():
                p = _raw_p.resolve()
            else:
                _base = Path(self.task.output_dir) if self.task.output_dir else Path.cwd()
                p = (_base / raw).resolve()
            if p.is_file():
                if not reveal_in_explorer(p):
                    open_folder(p.parent)
                return
            # Retry after 200ms if file not yet flushed
            QTimer.singleShot(200, lambda: self._open_folder_delayed(p))
            return
        output_dir = self.task.output_dir
        if output_dir:
            fb = Path(output_dir).resolve()
            if fb.is_dir():
                open_folder(fb)

    def _open_folder_delayed(self, p: Path) -> None:
        if p.is_file():
            if not reveal_in_explorer(p):
                open_folder(p.parent)
        elif p.parent.is_dir():
            open_folder(p.parent)

    def set_select_mode(self, enabled: bool, on_toggle) -> None:
        from PySide6.QtWidgets import QCheckBox  # noqa: PLC0415

        if enabled:
            terminal = self.task.status in DownloadStatus.terminal_states()
            if not terminal:
                return
            if self._checkbox is None:
                cb = QCheckBox(self)
                cb.setChecked(False)
                # Insert checkbox at position 0 of the top row
                top_layout = self.layout().itemAt(0).layout()
                top_layout.insertWidget(0, cb)
                self._checkbox = cb
            self._checkbox.setVisible(True)
            try:
                self._checkbox.toggled.disconnect()
            except RuntimeError:
                pass
            if on_toggle:
                tid = self.task.id
                self._checkbox.toggled.connect(lambda checked, t=tid: on_toggle(t, checked))
        else:
            if self._checkbox is not None:
                self._checkbox.setVisible(False)
                try:
                    self._checkbox.toggled.disconnect()
                except RuntimeError:
                    pass

    def deleteLater(self) -> None:
        T.unregister(self._theme_cb)
        # Qt destroys children at the C++ level without calling their Python
        # deleteLater() override, so the child's theme callback must be
        # released explicitly here.
        self._prog.deleteLater()
        super().deleteLater()

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")
