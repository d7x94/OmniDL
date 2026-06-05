"""Post-download action bar: Convert · Send to device · Delete."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui.themes.tokens import T

logger = logging.getLogger(__name__)

CONVERT_FORMATS: list[tuple[str, str]] = [
    ("mp4", "MP4 — H.264 / AAC (iPhone, Android)"),
    ("mp3", "MP3 — Audio only"),
    ("mkv", "MKV — Lossless container"),
    ("avi", "AVI — Legacy compatibility"),
    ("custom", "Tuỳ chỉnh (chất lượng + encoder)..."),
]


_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts", ".flv", ".wmv"})


class PostDownloadActions(QWidget):
    def __init__(
        self,
        parent=None,
        *,
        on_convert: Optional[Callable] = None,
        on_send: Optional[Callable] = None,
        on_delete: Optional[Callable] = None,
        on_edit: Optional[Callable] = None,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._on_convert = on_convert
        self._on_send = on_send
        self._on_delete = on_delete
        self._on_edit = on_edit
        self._compact = compact
        self._file_path: Optional[Path] = None
        self._gallery_dl_files: Optional[list] = None
        self._converting = False

        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self._convert_btn = QPushButton("🔄 Conv" if self._compact else "🔄 Convert")
        self._convert_btn.setFixedHeight(28)
        self._convert_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border-radius: 6px; border: none; font-size: 11px; font-weight: bold; padding: 0 8px;"
        )
        self._convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_btn.clicked.connect(self._on_convert_click)
        btn_row.addWidget(self._convert_btn)

        self._send_btn = QPushButton("📲 Gửi")
        self._send_btn.setFixedHeight(28)
        self._send_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border-radius: 6px; border: none; font-size: 11px; font-weight: bold; padding: 0 8px;"
        )
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.clicked.connect(self._on_send_click)
        btn_row.addWidget(self._send_btn)

        self._delete_btn = QPushButton("🗑 Xoá")
        self._delete_btn.setFixedHeight(28)
        self._delete_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error}; border-radius: 6px; border: none; font-size: 11px; font-weight: bold; padding: 0 8px;"
        )
        self._delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_btn.clicked.connect(self._on_delete_click)
        btn_row.addWidget(self._delete_btn)

        self._edit_btn = QPushButton("✂ Sửa")
        self._edit_btn.setFixedHeight(28)
        self._edit_btn.setStyleSheet(
            "background: #FCE7F3; color: #EC4899; border-radius: 6px; border: none; font-size: 11px; font-weight: bold; padding: 0 8px;"
        )
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.clicked.connect(self._on_edit_click)
        self._edit_btn.hide()
        btn_row.addWidget(self._edit_btn)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px;")
        self._status_lbl.hide()
        btn_row.addWidget(self._status_lbl)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Format picker panel (hidden by default)
        self._format_panel = _FormatPickerPanel(self)
        self._format_panel.hide()
        self._format_panel.confirmed.connect(self._on_format_confirmed)
        self._format_panel.cancelled.connect(self._format_panel.hide)
        layout.addWidget(self._format_panel)

    # ── Public API ────────────────────────────────────────────────────────

    def show(self, file_path: Path, gallery_dl_files: Optional[list] = None) -> None:
        self._file_path = file_path
        self._gallery_dl_files = gallery_dl_files
        self._converting = False
        self._set_status("")

        _is_multifile = gallery_dl_files is not None and len(gallery_dl_files) > 0
        if _is_multifile:
            _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
            _has_video = any(Path(f).suffix.lower() in _vid_exts for f in gallery_dl_files)
            self._convert_btn.setEnabled(_has_video)
        else:
            self._convert_btn.setEnabled(True)

        self._send_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)

        if self._on_edit and file_path.suffix.lower() in _VIDEO_EXTS:
            self._edit_btn.show()
        else:
            self._edit_btn.hide()

        self.setVisible(True)

    def hide(self) -> None:
        self._file_path = None
        self._gallery_dl_files = None
        self._format_panel.hide()
        self.setVisible(False)

    def notify_convert_done(self, output_path: Path) -> None:
        self._converting = False
        self._convert_btn.setText("✓ Done")
        self._convert_btn.setEnabled(False)
        if self._gallery_dl_files:
            self._file_path = output_path.parent
        else:
            self._file_path = output_path

    def notify_convert_error(self, msg: str) -> None:
        self._converting = False
        self._convert_btn.setText("🔄 Conv" if self._compact else "🔄 Convert")
        self._convert_btn.setEnabled(True)
        self._set_status(f"Convert thất bại: {msg[:80]}")

    # ── Handlers ──────────────────────────────────────────────────────────

    def _on_edit_click(self) -> None:
        if not self._file_path or not self._on_edit:
            return
        try:
            self._on_edit(self._file_path)
        except Exception as exc:
            logger.warning("on_edit raised: %s", exc)

    def _on_convert_click(self) -> None:
        if self._converting or not self._file_path:
            return
        if self._format_panel.isVisible():
            self._format_panel.hide()
        else:
            self._format_panel.show()

    def _on_format_confirmed(self, target_ext: str, encode_settings) -> None:
        if not self._file_path or not target_ext:
            return
        self._format_panel.hide()
        self._converting = True
        display_ext = target_ext if target_ext != "custom" else "mp4 (custom)"
        self._convert_btn.setText(f"Converting → .{display_ext}..." if not self._compact else "Converting...")
        self._convert_btn.setEnabled(False)

        if not self._on_convert:
            self._converting = False
            self._convert_btn.setText("🔄 Conv" if self._compact else "🔄 Convert")
            self._convert_btn.setEnabled(True)
            return

        _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
        gdl = self._gallery_dl_files
        if gdl:
            video_files = [Path(f) for f in gdl if Path(f).suffix.lower() in _vid_exts and Path(f).is_file()]
            for vf in video_files:
                try:
                    self._on_convert(vf, target_ext, encode_settings)
                except Exception as exc:
                    logger.warning("PostDownloadActions on_convert raised: %s", exc)
                    self._set_status(f"Lỗi convert {vf.name}: {exc}")
                    self._converting = False
                    self._convert_btn.setText("🔄 Conv" if self._compact else "🔄 Convert")
                    self._convert_btn.setEnabled(True)
                    return
        else:
            try:
                self._on_convert(self._file_path, target_ext, encode_settings)
            except Exception as exc:
                logger.warning("PostDownloadActions on_convert raised: %s", exc)
                self._set_status(f"Lỗi convert: {exc}")
                self._converting = False
                self._convert_btn.setText("🔄 Convert" if not self._compact else "🔄 Conv")
                self._convert_btn.setEnabled(True)

    def _on_send_click(self) -> None:
        if not self._file_path:
            return
        path = self._file_path
        specific_files = None

        if path.is_dir() and not self._gallery_dl_files:
            files = sorted(f for f in path.iterdir() if f.is_file())
            if not files:
                self._set_status("Thư mục rỗng.")
                return
            if len(files) == 1:
                specific_files = files
            else:
                dlg = _FolderFilePickerDialog(
                    self, directory=path, title="Chọn file cần gửi", confirm_text="📲  Gửi đã chọn"
                )
                if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected:
                    return
                specific_files = dlg.selected

        self._send_btn.setEnabled(False)
        self._send_btn.setText("📲 Đang gửi...")

        def _restore() -> None:
            self._send_btn.setEnabled(True)
            self._send_btn.setText("📲 Gửi")

        if self._on_send:
            try:
                if specific_files is not None:
                    self._on_send(path, _restore, specific_files=specific_files)
                else:
                    self._on_send(path, _restore)
            except Exception as exc:
                logger.warning("on_send raised: %s", exc)
                _restore()
        else:
            _restore()

    def _on_delete_click(self) -> None:
        if not self._file_path:
            return
        path = self._file_path
        gdl = self._gallery_dl_files

        if gdl:
            from PySide6.QtWidgets import QMessageBox

            r = QMessageBox.question(self, "Xác nhận xoá", f"Xoá tất cả {len(gdl)} file của bài đăng này?")
            if r != QMessageBox.StandardButton.Yes:
                return
            for f_str in gdl:
                fp = Path(f_str)
                try:
                    if fp.exists():
                        os.remove(fp)
                except OSError as exc:
                    logger.error("cannot delete '%s': %s", fp, exc)
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass

        elif path.is_dir():
            files = sorted(f for f in path.iterdir() if f.is_file())
            if not files:
                from PySide6.QtWidgets import QMessageBox

                r = QMessageBox.question(self, "Xác nhận xoá", f"Xoá thư mục rỗng '{path.name}'?")
                if r != QMessageBox.StandardButton.Yes:
                    return
                try:
                    path.rmdir()
                except OSError as exc:
                    self._set_status(f"Không xoá được: {exc.strerror}")
                    return
            else:
                dlg = _FolderFilePickerDialog(
                    self, directory=path, title="Chọn file cần xoá", confirm_text="🗑  Xoá đã chọn"
                )
                if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.selected:
                    return
                for fp in dlg.selected:
                    try:
                        if fp.exists():
                            os.remove(fp)
                    except OSError as exc:
                        logger.error("cannot delete '%s': %s", fp, exc)
                try:
                    if not any(path.iterdir()):
                        path.rmdir()
                except OSError:
                    pass
        else:
            from PySide6.QtWidgets import QMessageBox

            r = QMessageBox.question(
                self, "Xác nhận xoá", f"Bạn có chắc muốn xoá file này?\n{path.name[:80]}"
            )
            if r != QMessageBox.StandardButton.Yes:
                return
            try:
                if path.exists():
                    os.remove(path)
            except OSError as exc:
                self._set_status(f"Không xoá được: {exc.strerror}")
                return

        self._file_path = None
        self.hide()
        if self._on_delete:
            try:
                self._on_delete(path)
            except Exception as exc:
                logger.warning("on_delete raised: %s", exc)

    def _set_status(self, text: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setVisible(bool(text))


# ── Format picker panel ──────────────────────────────────────────────────────


class _FormatPickerPanel(QFrame):
    confirmed = Signal(str, object)  # target_ext, encode_settings (or None)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface2};
                border: 1px solid {T.border};
                border-radius: 8px;
            }}
        """)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(4)

        QLabel("Chọn định dạng đích:", self).setParent(None)
        lbl = QLabel("Chọn định dạng đích:")
        lbl.setStyleSheet(
            f"color: {T.text2}; font-weight: bold; font-size: 11px; background: transparent; border: none;"
        )
        layout.addWidget(lbl)

        self._btn_group = QButtonGroup(self)
        self._radios: list[tuple[str, QRadioButton]] = []

        for ext, label in CONVERT_FORMATS:
            rb = QRadioButton(label)
            rb.setStyleSheet(f"color: {T.text}; font-size: 11px; background: transparent; border: none;")
            self._btn_group.addButton(rb)
            self._radios.append((ext, rb))
            layout.addWidget(rb)
            if ext == "mp4":
                rb.setChecked(True)

        self._radios[-1][1].toggled.connect(self._on_custom_toggled)

        # Custom encoder panel
        self._custom_panel = _CustomEncodePanel(self)
        self._custom_panel.hide()
        layout.addWidget(self._custom_panel)

        # Confirm / Cancel row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("✕  Huỷ")
        cancel_btn.setFixedHeight(26)
        cancel_btn.setStyleSheet(
            f"background: {T.surface3}; color: {T.text3}; border-radius: 6px; border: none; font-size: 11px; padding: 0 8px;"
        )
        cancel_btn.clicked.connect(self.cancelled)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("✓  Convert")
        confirm_btn.setFixedHeight(26)
        confirm_btn.setObjectName("primary")
        confirm_btn.setFixedHeight(26)
        confirm_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border-radius: 6px; border: none; font-size: 11px; font-weight: bold; padding: 0 12px;"
        )
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        layout.addLayout(btn_row)

    def _on_custom_toggled(self, checked: bool) -> None:
        self._custom_panel.setVisible(checked)

    def _on_confirm(self) -> None:
        selected_ext = "mp4"
        for ext, rb in self._radios:
            if rb.isChecked():
                selected_ext = ext
                break
        encode_settings = None
        if selected_ext == "custom":
            selected_ext = "mp4"
            encode_settings = self._custom_panel.get_encode_settings()
        self.confirmed.emit(selected_ext, encode_settings)


class _CustomEncodePanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent; border: none;")
        self._build()

    def _build(self) -> None:
        from app.services.ffmpeg_convert_service import SPEED_OPTIONS, get_available_encoder_options

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Encoder
        row1 = QHBoxLayout()
        lbl = QLabel("Encoder:")
        lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-weight: bold;")
        row1.addWidget(lbl)
        self._encoder_combo = QComboBox()
        available = get_available_encoder_options()
        self._encoder_keys = [k for k, _ in available]
        self._encoder_combo.addItems([lbl for _, lbl in available])
        row1.addWidget(self._encoder_combo)
        layout.addLayout(row1)

        # Quality
        row2 = QHBoxLayout()
        lbl2 = QLabel("Chất lượng:")
        lbl2.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-weight: bold;")
        row2.addWidget(lbl2)
        self._quality_combo = QComboBox()
        self._quality_labels = ["Cao (CRF 18)", "Chuẩn (CRF 23)", "Nhỏ 720p (CRF 28)", "CRF tuỳ chỉnh"]
        self._quality_keys = ["high", "standard", "small", "custom"]
        self._quality_combo.addItems(self._quality_labels)
        self._quality_combo.setCurrentIndex(1)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        row2.addWidget(self._quality_combo)
        layout.addLayout(row2)

        # CRF row (hidden unless custom quality)
        self._crf_row = QWidget()
        crf_layout = QHBoxLayout(self._crf_row)
        crf_layout.setContentsMargins(0, 0, 0, 0)
        lbl3 = QLabel("CRF:")
        lbl3.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        crf_layout.addWidget(lbl3)
        self._crf_slider = QSlider(Qt.Orientation.Horizontal)
        self._crf_slider.setRange(0, 51)
        self._crf_slider.setValue(23)
        self._crf_slider.valueChanged.connect(self._on_crf_changed)
        crf_layout.addWidget(self._crf_slider)
        self._crf_lbl = QLabel("23")
        self._crf_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 11px; font-weight: bold;")
        crf_layout.addWidget(self._crf_lbl)
        self._crf_row.hide()
        layout.addWidget(self._crf_row)

        # Speed
        row3 = QHBoxLayout()
        lbl4 = QLabel("Tốc độ:")
        lbl4.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-weight: bold;")
        row3.addWidget(lbl4)
        self._speed_combo = QComboBox()
        self._speed_keys = [k for k, _ in SPEED_OPTIONS]
        self._speed_combo.addItems([lbl for _, lbl in SPEED_OPTIONS])
        self._speed_combo.setCurrentIndex(1)
        row3.addWidget(self._speed_combo)
        layout.addLayout(row3)

    def _on_quality_changed(self, idx: int) -> None:
        self._crf_row.setVisible(self._quality_keys[idx] == "custom")

    def _on_crf_changed(self, val: int) -> None:
        self._crf_lbl.setText(str(val))

    def get_encode_settings(self):
        from app.services.ffmpeg_convert_service import EncodeSettings

        enc_idx = self._encoder_combo.currentIndex()
        enc_key = self._encoder_keys[enc_idx] if enc_idx < len(self._encoder_keys) else "cpu"
        q_idx = self._quality_combo.currentIndex()
        q_key = self._quality_keys[q_idx] if q_idx < len(self._quality_keys) else "standard"
        s_idx = self._speed_combo.currentIndex()
        s_key = self._speed_keys[s_idx] if s_idx < len(self._speed_keys) else "balanced"
        crf_val = self._crf_slider.value() if q_key == "custom" else 23
        return EncodeSettings(encoder_key=enc_key, quality=q_key, speed_preset=s_key, custom_quality=crf_val)


# ── File picker dialog ────────────────────────────────────────────────────────


class _FolderFilePickerDialog(QDialog):
    def __init__(self, parent, *, directory: Path, title: str, confirm_text: str) -> None:
        super().__init__(parent)
        self.selected: list[Path] = []
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self._checks: dict[Path, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"📁  {directory.name}"))
        layout.addWidget(QLabel("Chọn file cần thực hiện:"))

        scroll = QScrollArea()
        scroll.setFixedHeight(200)
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        files = sorted(f for f in directory.iterdir() if f.is_file())
        for fp in files:
            try:
                size_kb = fp.stat().st_size // 1024
                size_str = f"{size_kb} KB" if size_kb < 1024 else f"{size_kb // 1024} MB"
            except OSError:
                size_str = "?"
            cb = QCheckBox(f"{fp.name}  ({size_str})")
            cb.setChecked(True)
            self._checks[fp] = cb
            inner_layout.addWidget(cb)
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        sel_row = QHBoxLayout()
        sel_all = QPushButton("Chọn tất cả")
        sel_all.clicked.connect(lambda: [cb.setChecked(True) for cb in self._checks.values()])
        desel_all = QPushButton("Bỏ chọn")
        desel_all.clicked.connect(lambda: [cb.setChecked(False) for cb in self._checks.values()])
        sel_row.addWidget(sel_all)
        sel_row.addWidget(desel_all)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        btn_box = QDialogButtonBox()
        cancel_btn = btn_box.addButton("Huỷ", QDialogButtonBox.ButtonRole.RejectRole)
        confirm_btn = btn_box.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn.clicked.connect(self.reject)
        confirm_btn.clicked.connect(self._on_confirm)
        layout.addWidget(btn_box)

    def _on_confirm(self) -> None:
        self.selected = [fp for fp, cb in self._checks.items() if cb.isChecked()]
        if self.selected:
            self.accept()
