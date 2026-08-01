"""Archive tab — compress files into password-protected .zip/.7z and extract archives."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.archive_service import ArchiveError, ArchiveMember, ArchiveService, ExtractResult
from ui.components.progress_bar import OmniProgressBar
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, open_folder

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_MODE_TOGGLE_QSS = f"""
    QPushButton {{
        background-color: {T.surface2};
        color: {T.text2};
        border: none;
        border-radius: 8px;
        font-size: 12px;
        font-weight: 600;
        padding: 0 16px;
    }}
    QPushButton:hover {{
        background-color: {T.surface3};
    }}
    QPushButton:checked {{
        background-color: {T.primary_dim};
        color: {T.primary_text};
    }}
"""

_PW_TOGGLE_QSS = f"""
    QPushButton {{
        background: {T.surface2};
        color: {T.text2};
        border: 1px solid {T.border2};
        border-radius: 8px;
        font-size: 14px;
        padding: 0;
        font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;
    }}
    QPushButton:hover {{
        background: {T.surface3};
    }}
    QPushButton:checked {{
        background: {T.primary_dim};
        color: {T.primary_text};
        border: 1px solid {T.primary};
    }}
"""


class ArchiveTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._service = ArchiveService()
        self._compress_sources: list[Path] = []
        self._busy = False
        self._cancel_event: Optional[threading.Event] = None
        self._build()

    # ── Layout ───────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QVBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 10)
        hdr_layout.setSpacing(10)

        title = QLabel("Nén / Giải nén")
        title.setObjectName("page_title")
        hdr_layout.addWidget(title)

        mode_row = QHBoxLayout()
        self._compress_mode_btn = QPushButton("Nén")
        self._extract_mode_btn = QPushButton("Giải nén")
        for btn in (self._compress_mode_btn, self._extract_mode_btn):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(_MODE_TOGGLE_QSS)
        self._compress_mode_btn.setChecked(True)
        self._compress_mode_btn.clicked.connect(lambda: self._set_mode("compress"))
        self._extract_mode_btn.clicked.connect(lambda: self._set_mode("extract"))
        mode_row.addWidget(self._compress_mode_btn)
        mode_row.addWidget(self._extract_mode_btn)
        mode_row.addStretch()
        hdr_layout.addLayout(mode_row)

        layout.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(28, 0, 28, 20)
        c_layout.setSpacing(14)

        self._compress_panel = self._build_compress_panel()
        self._extract_panel = self._build_extract_panel()
        c_layout.addWidget(self._compress_panel)
        c_layout.addWidget(self._extract_panel)
        self._extract_panel.hide()

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        c_layout.addWidget(self._status_lbl)

        progress_row = QHBoxLayout()
        self._progress = OmniProgressBar()
        self._progress.hide()
        progress_row.addWidget(self._progress, 1)
        self._cancel_btn = QPushButton("Hủy")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self._cancel_current_operation)
        progress_row.addWidget(self._cancel_btn)
        c_layout.addLayout(progress_row)

        c_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def _build_compress_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        src_row = QHBoxLayout()
        add_file_btn = QPushButton("Thêm file")
        add_file_btn.clicked.connect(self._browse_compress_files)
        add_folder_btn = QPushButton("Thêm thư mục")
        add_folder_btn.clicked.connect(self._browse_compress_folder)
        remove_btn = QPushButton("Xóa mục chọn")
        remove_btn.clicked.connect(self._remove_selected_sources)
        for b in (add_file_btn, add_folder_btn, remove_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        src_row.addWidget(add_file_btn)
        src_row.addWidget(add_folder_btn)
        src_row.addWidget(remove_btn)
        src_row.addStretch()
        v.addLayout(src_row)

        self._sources_list = QListWidget()
        self._sources_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._sources_list.setFixedHeight(120)
        v.addWidget(self._sources_list)

        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("Định dạng:"))
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["zip", "7z"])
        self._fmt_combo.currentTextChanged.connect(self._on_fmt_changed)
        opts_row.addWidget(self._fmt_combo)

        self._individually_chk = QCheckBox("Nén từng file riêng")
        self._individually_chk.toggled.connect(lambda _: self._refresh_name_controls())
        opts_row.addWidget(self._individually_chk)
        opts_row.addStretch()
        v.addLayout(opts_row)

        enc_row = QHBoxLayout()
        self._header_enc_chk = QCheckBox("Mã hóa tên file (chỉ 7z)")
        self._header_enc_chk.setEnabled(False)
        enc_row.addWidget(self._header_enc_chk)
        enc_row.addStretch()
        v.addLayout(enc_row)

        pw_row = QHBoxLayout()
        pw_row.addWidget(QLabel("Mật khẩu:"))
        self._compress_pw_entry = QLineEdit()
        self._compress_pw_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._compress_pw_entry.setPlaceholderText("Để trống nếu không đặt mật khẩu")
        pw_row.addWidget(self._compress_pw_entry)
        compress_pw_toggle = QPushButton("👁")
        compress_pw_toggle.setFixedSize(38, 38)
        compress_pw_toggle.setStyleSheet(_PW_TOGGLE_QSS)
        compress_pw_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        compress_pw_toggle.setCheckable(True)
        compress_pw_toggle.toggled.connect(self._toggle_compress_pw_visibility)
        pw_row.addWidget(compress_pw_toggle)
        v.addLayout(pw_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Tên archive:"))
        self._archive_name_entry = QLineEdit("archive")
        name_row.addWidget(self._archive_name_entry)
        self._use_orig_name_chk = QCheckBox("Dùng tên gốc")
        self._use_orig_name_chk.setEnabled(False)
        self._use_orig_name_chk.toggled.connect(
            lambda checked: self._archive_name_entry.setEnabled(not checked)
        )
        name_row.addWidget(self._use_orig_name_chk)
        v.addLayout(name_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Thư mục lưu:"))
        self._output_dir_entry = QLineEdit(str(self._app.config.download_dir))
        out_row.addWidget(self._output_dir_entry)
        out_browse_btn = QPushButton("Chọn...")
        out_browse_btn.clicked.connect(self._browse_output_dir)
        out_row.addWidget(out_browse_btn)
        v.addLayout(out_row)

        self._compress_btn = QPushButton("Nén")
        self._compress_btn.setObjectName("primary")
        self._compress_btn.setFixedHeight(34)
        self._compress_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compress_btn.clicked.connect(self._start_compress)
        v.addWidget(self._compress_btn)

        return panel

    def _build_extract_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("card")
        v = QVBoxLayout(panel)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(10)

        arc_row = QHBoxLayout()
        arc_row.addWidget(QLabel("File archive:"))
        self._extract_archive_entry = QLineEdit()
        arc_row.addWidget(self._extract_archive_entry)
        arc_browse_btn = QPushButton("Chọn...")
        arc_browse_btn.clicked.connect(self._browse_extract_archive)
        arc_row.addWidget(arc_browse_btn)
        v.addLayout(arc_row)

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Thư mục giải nén:"))
        self._extract_dest_entry = QLineEdit()
        dest_row.addWidget(self._extract_dest_entry)
        dest_browse_btn = QPushButton("Chọn...")
        dest_browse_btn.clicked.connect(self._browse_extract_dest)
        dest_row.addWidget(dest_browse_btn)
        v.addLayout(dest_row)

        pw_row = QHBoxLayout()
        pw_row.addWidget(QLabel("Mật khẩu:"))
        self._extract_pw_entry = QLineEdit()
        self._extract_pw_entry.setEchoMode(QLineEdit.EchoMode.Password)
        pw_row.addWidget(self._extract_pw_entry)
        extract_pw_toggle = QPushButton("👁")
        extract_pw_toggle.setFixedSize(38, 38)
        extract_pw_toggle.setStyleSheet(_PW_TOGGLE_QSS)
        extract_pw_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        extract_pw_toggle.setCheckable(True)
        extract_pw_toggle.toggled.connect(self._toggle_extract_pw_visibility)
        pw_row.addWidget(extract_pw_toggle)
        v.addLayout(pw_row)

        btn_row = QHBoxLayout()
        self._list_contents_btn = QPushButton("Xem nội dung")
        self._list_contents_btn.clicked.connect(self._start_list_contents)
        self._extract_btn = QPushButton("Giải nén")
        self._extract_btn.setObjectName("primary")
        self._extract_btn.clicked.connect(self._start_extract)
        for b in (self._list_contents_btn, self._extract_btn):
            b.setFixedHeight(34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_row.addWidget(self._list_contents_btn)
        btn_row.addWidget(self._extract_btn)
        v.addLayout(btn_row)

        self._contents_list = QListWidget()
        self._contents_list.setFixedHeight(140)
        v.addWidget(self._contents_list)

        return panel

    # ── Mode / widget helpers ───────────────────────────────────────────

    def _set_mode(self, mode: str) -> None:
        self._compress_mode_btn.setChecked(mode == "compress")
        self._extract_mode_btn.setChecked(mode == "extract")
        self._compress_panel.setVisible(mode == "compress")
        self._extract_panel.setVisible(mode == "extract")

    def _on_fmt_changed(self, fmt: str) -> None:
        if fmt == "zip":
            self._header_enc_chk.setChecked(False)
            self._header_enc_chk.setEnabled(False)
        else:
            self._header_enc_chk.setEnabled(True)

    def _toggle_compress_pw_visibility(self, checked: bool) -> None:
        self._compress_pw_entry.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _toggle_extract_pw_visibility(self, checked: bool) -> None:
        self._extract_pw_entry.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _set_busy(self, busy: bool, cancellable: bool = False) -> None:
        self._busy = busy
        self._compress_btn.setEnabled(not busy)
        self._extract_btn.setEnabled(not busy)
        self._list_contents_btn.setEnabled(not busy)
        self._progress.setVisible(busy)
        self._cancel_btn.setVisible(busy and cancellable)
        if busy:
            self._progress.set_progress(0)
            self._progress.set_state("active")
        else:
            self._cancel_event = None

    def _cancel_current_operation(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def _refresh_name_controls(self) -> None:
        can_use_orig = len(self._compress_sources) == 1 and not self._individually_chk.isChecked()
        if not can_use_orig:
            self._use_orig_name_chk.setChecked(False)
        self._use_orig_name_chk.setEnabled(can_use_orig)

    # ── File pickers ─────────────────────────────────────────────────────

    def _browse_compress_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn file")
        for f in files:
            p = Path(f)
            if p not in self._compress_sources:
                self._compress_sources.append(p)
                self._sources_list.addItem(str(p))
        self._refresh_name_controls()

    def _browse_compress_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục")
        if folder:
            p = Path(folder)
            if p not in self._compress_sources:
                self._compress_sources.append(p)
                self._sources_list.addItem(str(p))
        self._refresh_name_controls()

    def _remove_selected_sources(self) -> None:
        rows = sorted(
            (self._sources_list.row(item) for item in self._sources_list.selectedItems()), reverse=True
        )
        for idx in rows:
            self._sources_list.takeItem(idx)
            del self._compress_sources[idx]
        self._refresh_name_controls()

    def _browse_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu")
        if folder:
            self._output_dir_entry.setText(folder)

    def _browse_extract_archive(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Chọn file archive", filter="Archives (*.zip *.7z)")
        if f:
            self._extract_archive_entry.setText(f)
            if not self._extract_dest_entry.text():
                self._extract_dest_entry.setText(str(Path(f).with_suffix("")))

    def _browse_extract_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục giải nén")
        if folder:
            self._extract_dest_entry.setText(folder)

    # ── Compress ─────────────────────────────────────────────────────────

    def _start_compress(self) -> None:
        if self._busy:
            return
        if not self._compress_sources:
            QMessageBox.warning(self, "Chưa chọn file", "Hãy thêm ít nhất một file hoặc thư mục để nén.")
            return

        output_dir = Path(self._output_dir_entry.text().strip() or str(self._app.config.download_dir))
        fmt = self._fmt_combo.currentText()
        password: Optional[bytes] = self._compress_pw_entry.text().encode("utf-8") or None
        encrypt_header = self._header_enc_chk.isChecked()
        individually = self._individually_chk.isChecked()
        sources = list(self._compress_sources)

        use_orig_name = self._use_orig_name_chk.isEnabled() and self._use_orig_name_chk.isChecked()
        if use_orig_name:
            archive_name = sources[0].stem or self._archive_name_entry.text().strip() or "archive"
        else:
            archive_name = self._archive_name_entry.text().strip() or "archive"

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True, cancellable=True)
        self._status_lbl.setText("Đang nén...")

        def _on_progress(pct: float) -> None:
            ui_bridge.post(lambda p=pct: self._progress.set_progress(p))

        def _worker() -> None:
            try:
                results = self._service.compress(
                    sources,
                    output_dir,
                    fmt,
                    archive_name=archive_name,
                    password=password,
                    encrypt_header=encrypt_header,
                    individually=individually,
                    on_progress=_on_progress,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                ui_bridge.post(lambda e=exc: self._on_compress_error(e))
                return
            ui_bridge.post(lambda r=results: self._on_compress_done(r, output_dir))

        threading.Thread(target=_worker, daemon=True, name="omnidl-archive-compress").start()

    def _on_compress_done(self, results: list[Path], output_dir: Path) -> None:
        self._progress.set_progress(100)
        self._progress.set_state("complete")
        self._set_busy(False)
        self._status_lbl.setText(f"Đã nén {len(results)} archive vào {output_dir}")
        reply = QMessageBox.question(
            self, "Hoàn tất", f"Đã tạo {len(results)} archive.\nMở thư mục chứa file?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            open_folder(output_dir)

    def _on_compress_error(self, exc: Exception) -> None:
        self._set_busy(False)
        if isinstance(exc, ArchiveError) and str(exc) == "Compression cancelled":
            self._progress.set_state("paused")
            self._status_lbl.setText("Đã hủy nén")
            return
        self._progress.set_state("failed")
        self._status_lbl.setText("Nén thất bại")
        QMessageBox.critical(self, "Lỗi nén", str(exc))

    # ── Extract ──────────────────────────────────────────────────────────

    def _start_extract(self) -> None:
        if self._busy:
            return
        archive_text = self._extract_archive_entry.text().strip()
        if not archive_text:
            QMessageBox.warning(self, "Chưa chọn file", "Hãy chọn file archive để giải nén.")
            return

        archive_path = Path(archive_text)
        dest_text = self._extract_dest_entry.text().strip()
        dest_dir = Path(dest_text) if dest_text else archive_path.with_suffix("")
        password: Optional[bytes] = self._extract_pw_entry.text().encode("utf-8") or None

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True, cancellable=True)
        self._status_lbl.setText("Đang giải nén...")

        def _on_progress(pct: float) -> None:
            ui_bridge.post(lambda p=pct: self._progress.set_progress(p))

        def _worker() -> None:
            try:
                result = self._service.extract(
                    archive_path,
                    dest_dir,
                    password=password,
                    on_progress=_on_progress,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                ui_bridge.post(lambda e=exc: self._on_extract_error(e))
                return
            ui_bridge.post(lambda r=result: self._on_extract_done(r, dest_dir))

        threading.Thread(target=_worker, daemon=True, name="omnidl-archive-extract").start()

    def _on_extract_done(self, result: ExtractResult, dest_dir: Path) -> None:
        self._progress.set_progress(100)
        self._progress.set_state("complete")
        self._set_busy(False)
        self._status_lbl.setText(
            f"Đã giải nén {len(result.extracted_paths)} file ({fmt_bytes(result.total_bytes)}) vào {dest_dir}"
        )
        reply = QMessageBox.question(self, "Hoàn tất", "Đã giải nén xong.\nMở thư mục?")
        if reply == QMessageBox.StandardButton.Yes:
            open_folder(dest_dir)

    def _on_extract_error(self, exc: Exception) -> None:
        self._set_busy(False)
        if isinstance(exc, ArchiveError) and str(exc) == "Extraction cancelled":
            self._progress.set_state("paused")
            self._status_lbl.setText("Đã hủy giải nén")
            return
        self._progress.set_state("failed")
        self._status_lbl.setText("Giải nén thất bại")
        QMessageBox.critical(self, "Lỗi giải nén", str(exc))

    # ── List contents ────────────────────────────────────────────────────

    def _start_list_contents(self) -> None:
        if self._busy:
            return
        archive_text = self._extract_archive_entry.text().strip()
        if not archive_text:
            QMessageBox.warning(self, "Chưa chọn file", "Hãy chọn file archive.")
            return

        archive_path = Path(archive_text)
        password: Optional[bytes] = self._extract_pw_entry.text().encode("utf-8") or None

        self._set_busy(True)
        self._status_lbl.setText("Đang đọc nội dung...")

        def _worker() -> None:
            try:
                members = self._service.list_contents(archive_path, password=password)
            except Exception as exc:
                ui_bridge.post(lambda e=exc: self._on_list_error(e))
                return
            ui_bridge.post(lambda m=members: self._on_list_done(m))

        threading.Thread(target=_worker, daemon=True, name="omnidl-archive-list").start()

    def _on_list_done(self, members: list[ArchiveMember]) -> None:
        self._progress.set_progress(100)
        self._progress.set_state("complete")
        self._set_busy(False)
        self._status_lbl.setText(f"{len(members)} mục trong archive")
        self._contents_list.clear()
        for m in members:
            kind = "📁" if m.is_dir else "📄"
            self._contents_list.addItem(f"{kind} {m.name}  ({fmt_bytes(m.size)})")

    def _on_list_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self._progress.set_state("failed")
        self._status_lbl.setText("Không đọc được nội dung")
        QMessageBox.critical(self, "Lỗi", str(exc))
