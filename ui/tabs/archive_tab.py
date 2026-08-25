"""Archive tab — compress files into password-protected .zip/.7z and extract archives."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
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
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _mode_toggle_qss() -> str:
    return f"""
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
    QPushButton:pressed {{
        background-color: {T.border};
    }}
    QPushButton:checked {{
        background-color: {T.primary_dim};
        color: {T.primary_text};
    }}
    QPushButton:disabled {{
        background-color: {T.surface};
        color: {T.text3};
    }}
"""


def _pw_toggle_qss() -> str:
    return f"""
    QPushButton {{
        background: {T.surface2};
        color: {T.text2};
        border: 1px solid {T.border2};
        border-radius: 8px;
        font-size: 14px;
        padding: 0;
    }}
    QPushButton:hover {{
        background: {T.surface3};
    }}
    QPushButton:pressed {{
        background: {T.border};
    }}
    QPushButton:checked {{
        background: {T.primary_dim};
        color: {T.primary_text};
        border: 1px solid {T.primary};
    }}
    QPushButton:disabled {{
        background: {T.surface};
        border: 1px solid {T.border2};
    }}
"""


class _EyeToggleButton(QPushButton):
    """Password visibility toggle painted with QPainter instead of an emoji glyph.

    A font-glyph eye ("👁") depends on the runtime having a matching emoji
    font — the same silent-render-failure class as the old checkbox
    checkmark bug. Painting it directly sidesteps that dependency.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(38, 38)
        self.setStyleSheet(_pw_toggle_qss())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        checked = self.isChecked()
        color = QColor(T.primary_text if checked else T.text2)
        if not self.isEnabled():
            color.setAlpha(120)

        r = self.rect().adjusted(11, 14, -11, -14)
        left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
        cx = r.center().x()

        eye = QPainterPath()
        eye.moveTo(left, r.center().y())
        eye.quadTo(cx, top, right, r.center().y())
        eye.quadTo(cx, bottom, left, r.center().y())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(eye)
        painter.setBrush(color)
        painter.drawEllipse(r.center(), 2, 2)
        if not checked:
            painter.drawLine(left - 1, bottom + 1, right + 1, top - 1)
        painter.end()


class ArchiveTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._service = ArchiveService()
        self._compress_sources: list[Path] = []
        self._busy = False
        self._cancel_event: Optional[threading.Event] = None
        self._build()

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()

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

        self._title_lbl = QLabel(t("archive.title"))
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)

        mode_row = QHBoxLayout()
        self._compress_mode_btn = QPushButton(t("archive.mode.compress"))
        self._extract_mode_btn = QPushButton(t("archive.mode.extract"))
        for btn in (self._compress_mode_btn, self._extract_mode_btn):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(_mode_toggle_qss())
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
        self._cancel_btn = QPushButton(t("archive.cancel"))
        self._cancel_btn.setObjectName("danger")
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
        self._add_file_btn = QPushButton(t("archive.add_file"))
        self._add_file_btn.clicked.connect(self._browse_compress_files)
        self._add_folder_btn = QPushButton(t("archive.add_folder"))
        self._add_folder_btn.clicked.connect(self._browse_compress_folder)
        self._remove_btn = QPushButton(t("archive.remove_selected"))
        self._remove_btn.clicked.connect(self._remove_selected_sources)
        for b in (self._add_file_btn, self._add_folder_btn, self._remove_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        src_row.addWidget(self._add_file_btn)
        src_row.addWidget(self._add_folder_btn)
        src_row.addWidget(self._remove_btn)
        src_row.addStretch()
        v.addLayout(src_row)

        self._sources_list = QListWidget()
        self._sources_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._sources_list.setFixedHeight(120)
        v.addWidget(self._sources_list)

        opts_row = QHBoxLayout()
        self._fmt_lbl = QLabel(t("archive.format_label"))
        opts_row.addWidget(self._fmt_lbl)
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(["zip", "7z"])
        self._fmt_combo.currentTextChanged.connect(self._on_fmt_changed)
        opts_row.addWidget(self._fmt_combo)

        self._individually_chk = QCheckBox(t("archive.compress_individually"))
        self._individually_chk.toggled.connect(lambda _: self._refresh_name_controls())
        opts_row.addWidget(self._individually_chk)
        opts_row.addStretch()
        v.addLayout(opts_row)

        enc_row = QHBoxLayout()
        self._header_enc_chk = QCheckBox(t("archive.encrypt_names"))
        self._header_enc_chk.setEnabled(False)
        enc_row.addWidget(self._header_enc_chk)
        enc_row.addStretch()
        v.addLayout(enc_row)

        pw_row = QHBoxLayout()
        self._compress_pw_lbl = QLabel(t("archive.password_label"))
        pw_row.addWidget(self._compress_pw_lbl)
        self._compress_pw_entry = QLineEdit()
        self._compress_pw_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self._compress_pw_entry.setPlaceholderText(t("archive.password_placeholder"))
        pw_row.addWidget(self._compress_pw_entry)
        compress_pw_toggle = _EyeToggleButton()
        compress_pw_toggle.toggled.connect(self._toggle_compress_pw_visibility)
        pw_row.addWidget(compress_pw_toggle)
        v.addLayout(pw_row)

        name_row = QHBoxLayout()
        self._name_lbl = QLabel(t("archive.name_label"))
        name_row.addWidget(self._name_lbl)
        self._archive_name_entry = QLineEdit("archive")
        name_row.addWidget(self._archive_name_entry)
        self._use_orig_name_chk = QCheckBox(t("archive.use_orig_name"))
        self._use_orig_name_chk.setEnabled(False)
        self._use_orig_name_chk.setToolTip(t("archive.use_orig_name_tip"))
        self._use_orig_name_chk.toggled.connect(
            lambda checked: self._archive_name_entry.setEnabled(not checked)
        )
        name_row.addWidget(self._use_orig_name_chk)
        v.addLayout(name_row)

        out_row = QHBoxLayout()
        self._out_dir_lbl = QLabel(t("archive.save_dir_label"))
        out_row.addWidget(self._out_dir_lbl)
        self._output_dir_entry = QLineEdit(str(self._app.config.download_dir))
        out_row.addWidget(self._output_dir_entry)
        self._out_browse_btn = QPushButton(t("archive.choose"))
        self._out_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._out_browse_btn.clicked.connect(self._browse_output_dir)
        out_row.addWidget(self._out_browse_btn)
        v.addLayout(out_row)

        self._compress_btn = QPushButton(t("archive.mode.compress"))
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
        self._arc_lbl = QLabel(t("archive.file_label"))
        arc_row.addWidget(self._arc_lbl)
        self._extract_archive_entry = QLineEdit()
        arc_row.addWidget(self._extract_archive_entry)
        self._arc_browse_btn = QPushButton(t("archive.choose"))
        self._arc_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._arc_browse_btn.clicked.connect(self._browse_extract_archive)
        arc_row.addWidget(self._arc_browse_btn)
        v.addLayout(arc_row)

        dest_row = QHBoxLayout()
        self._extract_dest_lbl = QLabel(t("archive.extract_dest_label"))
        dest_row.addWidget(self._extract_dest_lbl)
        self._extract_dest_entry = QLineEdit()
        dest_row.addWidget(self._extract_dest_entry)
        self._dest_browse_btn = QPushButton(t("archive.choose"))
        self._dest_browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dest_browse_btn.clicked.connect(self._browse_extract_dest)
        dest_row.addWidget(self._dest_browse_btn)
        v.addLayout(dest_row)

        pw_row = QHBoxLayout()
        self._extract_pw_lbl = QLabel(t("archive.password_label"))
        pw_row.addWidget(self._extract_pw_lbl)
        self._extract_pw_entry = QLineEdit()
        self._extract_pw_entry.setEchoMode(QLineEdit.EchoMode.Password)
        pw_row.addWidget(self._extract_pw_entry)
        extract_pw_toggle = _EyeToggleButton()
        extract_pw_toggle.toggled.connect(self._toggle_extract_pw_visibility)
        pw_row.addWidget(extract_pw_toggle)
        v.addLayout(pw_row)

        btn_row = QHBoxLayout()
        self._list_contents_btn = QPushButton(t("archive.view_contents"))
        self._list_contents_btn.clicked.connect(self._start_list_contents)
        self._extract_btn = QPushButton(t("archive.mode.extract"))
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
        self._compress_mode_btn.setEnabled(not busy)
        self._extract_mode_btn.setEnabled(not busy)
        self._add_file_btn.setEnabled(not busy)
        self._add_folder_btn.setEnabled(not busy)
        self._remove_btn.setEnabled(not busy)
        self._individually_chk.setEnabled(not busy)
        if busy:
            self._header_enc_chk.setEnabled(False)
            self._use_orig_name_chk.setEnabled(False)
            self._use_orig_name_chk.update()
        else:
            self._on_fmt_changed(self._fmt_combo.currentText())
            self._refresh_name_controls()
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
        self._use_orig_name_chk.update()

    # ── File pickers ─────────────────────────────────────────────────────

    def _browse_compress_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, t("archive.choose_file"))
        for f in files:
            p = Path(f)
            if p not in self._compress_sources:
                self._compress_sources.append(p)
                self._sources_list.addItem(str(p))
        self._refresh_name_controls()

    def _browse_compress_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("archive.choose_folder"))
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
        folder = QFileDialog.getExistingDirectory(self, t("archive.choose_save_folder"))
        if folder:
            self._output_dir_entry.setText(folder)

    def _browse_extract_archive(self) -> None:
        f, _ = QFileDialog.getOpenFileName(
            self, t("archive.choose_archive_file"), filter="Archives (*.zip *.7z)"
        )
        if f:
            self._extract_archive_entry.setText(f)
            if not self._extract_dest_entry.text():
                self._extract_dest_entry.setText(str(Path(f).with_suffix("")))

    def _browse_extract_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("archive.choose_extract_folder"))
        if folder:
            self._extract_dest_entry.setText(folder)

    # ── Compress ─────────────────────────────────────────────────────────

    def _start_compress(self) -> None:
        if self._busy:
            return
        if not self._compress_sources:
            QMessageBox.warning(self, t("archive.no_file_title"), t("archive.no_file_compress_msg"))
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
        self._status_lbl.setText(t("archive.status.compressing"))

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
        self._status_lbl.setText(t("archive.status.compress_done", count=len(results), dir=output_dir))
        reply = QMessageBox.question(
            self, t("archive.done_title"), t("archive.compress_done_msg", count=len(results))
        )
        if reply == QMessageBox.StandardButton.Yes:
            open_folder(output_dir)

    def _on_compress_error(self, exc: Exception) -> None:
        self._set_busy(False)
        if isinstance(exc, ArchiveError) and str(exc) == "Compression cancelled":
            self._progress.set_state("paused")
            self._status_lbl.setText(t("archive.status.compress_cancelled"))
            return
        self._progress.set_state("failed")
        self._status_lbl.setText(t("archive.status.compress_failed"))
        QMessageBox.critical(self, t("archive.error_compress_title"), str(exc))

    # ── Extract ──────────────────────────────────────────────────────────

    def _start_extract(self) -> None:
        if self._busy:
            return
        archive_text = self._extract_archive_entry.text().strip()
        if not archive_text:
            QMessageBox.warning(self, t("archive.no_file_title"), t("archive.no_file_extract_msg"))
            return

        archive_path = Path(archive_text)
        dest_text = self._extract_dest_entry.text().strip()
        dest_dir = Path(dest_text) if dest_text else archive_path.with_suffix("")
        password: Optional[bytes] = self._extract_pw_entry.text().encode("utf-8") or None

        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self._set_busy(True, cancellable=True)
        self._status_lbl.setText(t("archive.status.extracting"))

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
            t(
                "archive.status.extract_done",
                count=len(result.extracted_paths),
                size=fmt_bytes(result.total_bytes),
                dir=dest_dir,
            )
        )
        reply = QMessageBox.question(self, t("archive.done_title"), t("archive.extract_done_msg"))
        if reply == QMessageBox.StandardButton.Yes:
            open_folder(dest_dir)

    def _on_extract_error(self, exc: Exception) -> None:
        self._set_busy(False)
        if isinstance(exc, ArchiveError) and str(exc) == "Extraction cancelled":
            self._progress.set_state("paused")
            self._status_lbl.setText(t("archive.status.extract_cancelled"))
            return
        self._progress.set_state("failed")
        self._status_lbl.setText(t("archive.status.extract_failed"))
        QMessageBox.critical(self, t("archive.error_extract_title"), str(exc))

    # ── List contents ────────────────────────────────────────────────────

    def _start_list_contents(self) -> None:
        if self._busy:
            return
        archive_text = self._extract_archive_entry.text().strip()
        if not archive_text:
            QMessageBox.warning(self, t("archive.no_file_title"), t("archive.no_file_list_msg"))
            return

        archive_path = Path(archive_text)
        password: Optional[bytes] = self._extract_pw_entry.text().encode("utf-8") or None

        self._set_busy(True)
        self._status_lbl.setText(t("archive.status.listing"))

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
        self._status_lbl.setText(t("archive.status.list_count", count=len(members)))
        self._contents_list.clear()
        for m in members:
            kind = "📁" if m.is_dir else "📄"
            self._contents_list.addItem(f"{kind} {m.name}  ({fmt_bytes(m.size)})")

    def _on_list_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self._progress.set_state("failed")
        self._status_lbl.setText(t("archive.status.list_failed"))
        QMessageBox.critical(self, t("archive.error_title"), str(exc))

    # ── i18n ─────────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._title_lbl.setText(t("archive.title"))
        self._compress_mode_btn.setText(t("archive.mode.compress"))
        self._extract_mode_btn.setText(t("archive.mode.extract"))
        self._cancel_btn.setText(t("archive.cancel"))
        self._add_file_btn.setText(t("archive.add_file"))
        self._add_folder_btn.setText(t("archive.add_folder"))
        self._remove_btn.setText(t("archive.remove_selected"))
        self._fmt_lbl.setText(t("archive.format_label"))
        self._individually_chk.setText(t("archive.compress_individually"))
        self._header_enc_chk.setText(t("archive.encrypt_names"))
        self._compress_pw_lbl.setText(t("archive.password_label"))
        self._compress_pw_entry.setPlaceholderText(t("archive.password_placeholder"))
        self._name_lbl.setText(t("archive.name_label"))
        self._use_orig_name_chk.setText(t("archive.use_orig_name"))
        self._use_orig_name_chk.setToolTip(t("archive.use_orig_name_tip"))
        self._out_dir_lbl.setText(t("archive.save_dir_label"))
        self._out_browse_btn.setText(t("archive.choose"))
        self._compress_btn.setText(t("archive.mode.compress"))
        self._arc_lbl.setText(t("archive.file_label"))
        self._arc_browse_btn.setText(t("archive.choose"))
        self._extract_dest_lbl.setText(t("archive.extract_dest_label"))
        self._dest_browse_btn.setText(t("archive.choose"))
        self._extract_pw_lbl.setText(t("archive.password_label"))
        self._list_contents_btn.setText(t("archive.view_contents"))
        self._extract_btn.setText(t("archive.mode.extract"))
