"""Convert tab — convert local video files to iPhone-compatible MP4."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
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

from app.event_bus import EventBus
from app.services.ffmpeg_convert_service import (
    CODEC_OPTIONS,
    ENCODER_OPTIONS,
    SPEED_OPTIONS,
    SUPPORTED_EXTS,
    ConvertQueue,
    EncodeSettings,
    FfmpegMediaInfo,
    get_available_encoder_options,
    probe_media_info,
    scan_folder_for_media,
)
from ui.components.progress_bar import OmniProgressBar
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, fmt_duration, open_file, open_folder, reveal_in_explorer

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_QUALITY_OPTIONS = [
    ("high", "Chất lượng cao", "H.264 CRF 18 · AAC 192k · Giữ độ phân giải"),
    ("standard", "Chuẩn", "H.264 CRF 23 · AAC 128k · Phù hợp mọi iPhone"),
    ("small", "File nhỏ", "H.264 CRF 28 · AAC 96k · Tối đa 720p"),
    ("custom", "Tùy chỉnh", "Giá trị CRF/CQ tùy chọn (16-35)"),
]

_MAX_CONCURRENT = 2


class FileState(Enum):
    PENDING = auto()
    QUEUED = auto()
    CONVERTING = auto()
    DONE = auto()
    FAILED = auto()


_STATE_BADGE: dict[FileState, tuple[str, str, str]] = {
    FileState.PENDING: ("Chờ", "text3", "surface3"),
    FileState.QUEUED: ("Hàng chờ", "text2", "surface2"),
    FileState.CONVERTING: ("Đang chuyển…", "warning", "warning_bg"),
    FileState.DONE: ("✓ Xong", "success", "success_bg"),
    FileState.FAILED: ("✕ Lỗi", "error", "error_bg"),
}

_STATE_PROG: dict[FileState, str] = {
    FileState.PENDING: "active",
    FileState.QUEUED: "paused",
    FileState.CONVERTING: "active",
    FileState.DONE: "complete",
    FileState.FAILED: "failed",
}


@dataclass
class FileJob:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source: Path = field(default_factory=Path)
    state: FileState = FileState.PENDING
    progress: float = 0.0
    output: Optional[Path] = None
    error_msg: str = ""
    media_info: Optional[FfmpegMediaInfo] = field(default=None)
    cancel_fn: Optional[object] = field(default=None, repr=False)


# ── Clickable card frame ──────────────────────────────────────────────────────


class _ClickableFrame(QFrame):
    def __init__(self, parent=None, on_click=None):
        super().__init__(parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if self._on_click and event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mousePressEvent(event)


# ── File card ─────────────────────────────────────────────────────────────────


class FileCard(QFrame):
    def __init__(
        self,
        parent,
        job: FileJob,
        on_remove,
        on_open_folder,
        on_cancel,
        on_delete_output,
    ) -> None:
        super().__init__(parent)
        self.job = job
        self._on_remove = on_remove
        self._on_open_folder = on_open_folder
        self._on_cancel = on_cancel
        self._on_delete_output = on_delete_output
        self.setStyleSheet(f"""
            FileCard {{
                background-color: {T.surface};
                border: 1px solid {T.border};
                border-radius: 10px;
            }}
        """)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 6)
        layout.setSpacing(4)

        # Row 1: filename + badges + buttons
        top = QWidget()
        top.setStyleSheet("background: transparent;")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        ext = self.job.source.suffix.lower().lstrip(".")
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {T.primary}; font-size: 9px; background: transparent;")
        top_layout.addWidget(self._dot)

        self._name_lbl = QLabel(self._trunc(self.job.source.name, 55))
        self._name_lbl.setStyleSheet(
            f"color: {T.text}; font-size: 13px; font-weight: bold; background: transparent;"
        )
        self._name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top_layout.addWidget(self._name_lbl)

        self._ext_badge = QLabel(f"  {ext.upper()}  ")
        self._ext_badge.setStyleSheet(
            f"color: {T.text3}; background-color: {T.surface3}; border-radius: 4px; font-size: 9px; font-weight: bold; padding: 2px 4px;"
        )
        top_layout.addWidget(self._ext_badge)

        s_lbl, s_txt, s_bg = _STATE_BADGE[self.job.state]
        self._state_badge = QLabel(f"  {s_lbl}  ")
        self._state_badge.setStyleSheet(
            f"color: {getattr(T, s_txt)}; background-color: {getattr(T, s_bg)}; border-radius: 5px; font-size: 10px; font-weight: bold; padding: 2px 4px;"
        )
        top_layout.addWidget(self._state_badge)

        # Buttons in order
        self._cancel_btn = QPushButton("Huy")
        self._cancel_btn.setFixedSize(60, 26)
        self._cancel_btn.setStyleSheet(
            f"background: {T.warning_bg}; color: {T.warning}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold;"
        )
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(lambda: self._on_cancel(self.job.id))
        self._cancel_btn.hide()
        top_layout.addWidget(self._cancel_btn)

        self._open_btn = QPushButton("Mo")
        self._open_btn.setFixedSize(52, 26)
        self._open_btn.setStyleSheet(
            f"background: {T.success_bg}; color: {T.success_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold;"
        )
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(lambda: self._on_open_folder(self.job.id))
        self._open_btn.hide()
        top_layout.addWidget(self._open_btn)

        self._preview_btn = QPushButton("Xem")
        self._preview_btn.setFixedSize(52, 26)
        self._preview_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold;"
        )
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.clicked.connect(self._open_preview)
        self._preview_btn.hide()
        top_layout.addWidget(self._preview_btn)

        self._delete_output_btn = QPushButton("Xóa file")
        self._delete_output_btn.setFixedSize(70, 26)
        self._delete_output_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold;"
        )
        self._delete_output_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_output_btn.clicked.connect(lambda: self._on_delete_output(self.job.id))
        self._delete_output_btn.hide()
        top_layout.addWidget(self._delete_output_btn)

        self._remove_btn = QPushButton("x")
        self._remove_btn.setFixedSize(28, 26)
        self._remove_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 6px;"
        )
        self._remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remove_btn.clicked.connect(lambda: self._on_remove(self.job.id))
        top_layout.addWidget(self._remove_btn)

        layout.addWidget(top)

        # Row 2: media info
        self._info_lbl = QLabel(self._initial_info_text())
        self._info_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; background: transparent;")
        layout.addWidget(self._info_lbl)

        # Row 3: progress bar
        self._prog = OmniProgressBar(self)
        layout.addWidget(self._prog)

        # Row 4: stats
        stats = QWidget()
        stats.setStyleSheet("background: transparent;")
        stats_layout = QHBoxLayout(stats)
        stats_layout.setContentsMargins(0, 0, 0, 6)
        stats_layout.setSpacing(12)

        self._size_lbl = QLabel(self._file_size_str())
        self._size_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; background: transparent;")
        stats_layout.addWidget(self._size_lbl)

        self._pct_lbl = QLabel("")
        self._pct_lbl.setStyleSheet(f"color: {T.primary_text}; font-size: 10px; background: transparent;")
        stats_layout.addWidget(self._pct_lbl)

        stats_layout.addStretch()

        self._out_lbl = QLabel("")
        self._out_lbl.setStyleSheet(f"color: {T.success_text}; font-size: 10px; background: transparent;")
        self._out_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        stats_layout.addWidget(self._out_lbl)

        layout.addWidget(stats)

        self._err_lbl = QLabel("")
        self._err_lbl.setStyleSheet(f"color: {T.error_text}; font-size: 11px; background: transparent;")
        self._err_lbl.setWordWrap(True)
        self._err_lbl.hide()
        layout.addWidget(self._err_lbl)

        if self.job.media_info is not None:
            self.update_info(self.job.media_info)

    def refresh(self) -> None:
        job = self.job
        s_lbl, s_txt, s_bg = _STATE_BADGE[job.state]
        self._state_badge.setText(f"  {s_lbl}  ")
        self._state_badge.setStyleSheet(
            f"color: {getattr(T, s_txt)}; background-color: {getattr(T, s_bg)}; border-radius: 5px; font-size: 10px; font-weight: bold; padding: 2px 4px;"
        )
        self._dot.setStyleSheet(f"color: {getattr(T, s_txt)}; font-size: 9px; background: transparent;")

        self._prog.set_progress(job.progress)
        self._prog.set_state(_STATE_PROG[job.state])

        if job.state == FileState.CONVERTING:
            self._pct_lbl.setText(f"{job.progress:.0f}%")
        else:
            self._pct_lbl.setText("")

        is_active = job.state in (FileState.QUEUED, FileState.CONVERTING)
        self._cancel_btn.setVisible(is_active)
        self._remove_btn.setEnabled(not is_active)

        if job.state == FileState.DONE and job.output:
            sz = fmt_bytes(job.output.stat().st_size) if job.output.is_file() else ""
            self._out_lbl.setText(f"→ {job.output.name}  {sz}")
            self._open_btn.show()
            self._preview_btn.show()
            self._delete_output_btn.setVisible(job.output.is_file())
            self._err_lbl.hide()
        elif job.state == FileState.FAILED and job.error_msg:
            self._err_lbl.setText(f"  {job.error_msg[:160]}")
            self._err_lbl.show()
            self._open_btn.hide()
            self._preview_btn.hide()
            self._delete_output_btn.hide()
        else:
            self._open_btn.hide()
            self._preview_btn.hide()
            self._delete_output_btn.hide()
            self._err_lbl.hide()

    def update_info(self, info: Optional[FfmpegMediaInfo]) -> None:
        if info is None:
            self._info_lbl.setText("")
            return
        parts: list[str] = []
        if info.video_codec:
            parts.append(info.video_codec.upper())
        if info.audio_codec:
            parts.append(info.audio_codec.upper())
        if info.width and info.height:
            parts.append(f"{info.width}×{info.height}")
        if info.duration_s > 0:
            parts.append(fmt_duration(info.duration_s))
        if info.bitrate_bps > 0:
            mbps = info.bitrate_bps / 1_000_000
            parts.append(f"{mbps:.1f} Mbps")
        self._info_lbl.setText("  ·  ".join(parts) if parts else "")

    def _open_preview(self) -> None:
        if self.job.output and self.job.output.exists():
            open_file(self.job.output)
        elif self.job.output:
            open_folder(self.job.output.parent)

    def _initial_info_text(self) -> str:
        return "" if self.job.media_info is not None else "Đang đọc thông tin…"

    def _file_size_str(self) -> str:
        try:
            return fmt_bytes(self.job.source.stat().st_size)
        except Exception:
            return ""

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")


# ── Convert Tab ───────────────────────────────────────────────────────────────


class ConvertTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._jobs: dict[str, FileJob] = {}
        self._cards: dict[str, FileCard] = {}
        self._quality = "standard"
        self._output_dir: Optional[Path] = None
        self._queue = ConvertQueue(max_concurrent=_MAX_CONCURRENT)
        self._active_count = 0
        self._encoder_key = "cpu"
        self._encoder_auto_selected = False
        self._speed_preset = "balanced"
        self._output_codec = "h264"
        self._custom_quality_val = 23
        self._available_encoders: set[str] = {"cpu"}
        self._available_encoder_options: list[tuple[str, str]] = [
            opt for opt in ENCODER_OPTIONS if opt[0] == "cpu"
        ]
        # Quality / speed / codec card refs
        self._quality_cards: dict[str, _ClickableFrame] = {}
        self._quality_main_labels: dict[str, QLabel] = {}
        self._speed_cards: dict[str, _ClickableFrame] = {}
        self._speed_main_labels: dict[str, QLabel] = {}
        self._codec_cards: dict[str, _ClickableFrame] = {}
        self._codec_main_labels: dict[str, QLabel] = {}

        threading.Thread(
            target=self._detect_encoders_async,
            daemon=True,
            name="omnidl-detect-encoders",
        ).start()
        self._build()

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

        _bus = self._app.taildrop._bus
        _bus.subscribe(EventBus.CONVERT_TAILDROP_COMPLETED, self._on_convert_taildrop_completed)
        _bus.subscribe(EventBus.CONVERT_TAILDROP_FAILED, self._on_convert_taildrop_failed)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 0)

        left_hdr = QWidget()
        left_hdr.setStyleSheet("background: transparent;")
        lh_layout = QVBoxLayout(left_hdr)
        lh_layout.setContentsMargins(0, 0, 0, 0)
        lh_layout.setSpacing(2)
        t1 = QLabel("Chuyển sang iPhone MP4")
        t1.setObjectName("page_title")
        lh_layout.addWidget(t1)
        t2 = QLabel("H.264 · AAC · yuv420p · profile High — chạy mượt trên mọi iPhone")
        t2.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        lh_layout.addWidget(t2)
        hdr_layout.addWidget(left_hdr)
        hdr_layout.addStretch()

        self._add_btn = QPushButton("Thêm file")
        self._add_btn.setFixedSize(130, 36)
        self._add_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 8px; font-size: 12px; font-weight: bold;"
        )
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._browse_files)
        hdr_layout.addWidget(self._add_btn)

        self._folder_btn = QPushButton("Thêm thư mục")
        self._folder_btn.setFixedSize(150, 36)
        self._folder_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 12px; font-weight: bold;"
        )
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.clicked.connect(self._browse_folder)
        hdr_layout.addWidget(self._folder_btn)

        self._clear_btn = QPushButton("Xóa xong")
        self._clear_btn.setFixedSize(100, 36)
        self._clear_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 11px;"
        )
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self._clear_done)
        hdr_layout.addWidget(self._clear_btn)

        layout.addWidget(hdr)

        # Config panel
        cfg_wrap = QWidget()
        cfg_wrap.setStyleSheet("background: transparent;")
        cfg_wrap_layout = QVBoxLayout(cfg_wrap)
        cfg_wrap_layout.setContentsMargins(28, 16, 28, 0)

        cfg = QFrame()
        cfg.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface};
                border: 1px solid {T.border};
                border-radius: 12px;
            }}
            QLabel {{ border: none; }}
        """)
        cfg_layout = QVBoxLayout(cfg)
        cfg_layout.setContentsMargins(20, 16, 20, 16)
        cfg_layout.setSpacing(8)

        # Quality row
        q_row = QWidget()
        q_row.setStyleSheet("background: transparent;")
        q_layout = QHBoxLayout(q_row)
        q_layout.setContentsMargins(0, 0, 0, 0)
        q_layout.setSpacing(6)

        q_lbl = QLabel("Chất lượng")
        q_lbl.setFixedWidth(90)
        q_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        q_layout.addWidget(q_lbl)

        for key, label, desc in _QUALITY_OPTIONS:
            card = self._make_option_card(
                label,
                desc,
                selected=(key == self._quality),
                on_click=lambda k=key: self._on_quality_change(k),
            )
            q_layout.addWidget(card, 1)
            self._quality_cards[key] = card
            self._quality_main_labels[key] = card.findChild(QLabel, "main_lbl")

        cfg_layout.addWidget(q_row)

        # Custom quality row
        self._custom_row = QWidget()
        self._custom_row.setStyleSheet("background: transparent;")
        cr_layout = QHBoxLayout(self._custom_row)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.setSpacing(6)

        QLabel("").setParent(None)  # spacer-style
        spacer_lbl = QLabel("")
        spacer_lbl.setFixedWidth(90)
        cr_layout.addWidget(spacer_lbl)

        cq_lbl = QLabel("Gia tri (16-35):")
        cq_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        cr_layout.addWidget(cq_lbl)

        self._custom_entry = QLineEdit("23")
        self._custom_entry.setFixedSize(60, 28)
        self._custom_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.input};
                border: 1px solid {T.border2};
                border-radius: 6px;
                color: {T.text};
                font-size: 12px;
                padding: 0 6px;
            }}
        """)
        self._custom_entry.editingFinished.connect(self._validate_custom_quality)
        cr_layout.addWidget(self._custom_entry)
        cr_layout.addStretch()

        self._custom_row.hide()
        cfg_layout.addWidget(self._custom_row)

        # Encoder row
        enc_row = QWidget()
        enc_row.setStyleSheet("background: transparent;")
        er_layout = QHBoxLayout(enc_row)
        er_layout.setContentsMargins(0, 0, 0, 0)
        er_layout.setSpacing(8)

        enc_lbl = QLabel("Encoder")
        enc_lbl.setFixedWidth(90)
        enc_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        er_layout.addWidget(enc_lbl)

        self._encoder_combo = QComboBox()
        self._encoder_combo.addItems([lbl for _, lbl in self._available_encoder_options])
        self._encoder_combo.setFixedSize(200, 32)
        self._encoder_combo.currentTextChanged.connect(self._on_encoder_change)
        er_layout.addWidget(self._encoder_combo)

        self._encoder_status_lbl = QLabel("Đang kiểm tra…")
        self._encoder_status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px;")
        er_layout.addWidget(self._encoder_status_lbl)
        er_layout.addStretch()

        cfg_layout.addWidget(enc_row)

        # Speed row
        spd_row = QWidget()
        spd_row.setStyleSheet("background: transparent;")
        sr_layout = QHBoxLayout(spd_row)
        sr_layout.setContentsMargins(0, 0, 0, 0)
        sr_layout.setSpacing(6)

        spd_lbl = QLabel("Tốc độ")
        spd_lbl.setFixedWidth(90)
        spd_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        sr_layout.addWidget(spd_lbl)

        for s_key, s_label in SPEED_OPTIONS:
            s_card = self._make_option_card(
                s_label,
                "",
                selected=(s_key == self._speed_preset),
                on_click=lambda k=s_key: self._on_speed_change(k),
            )
            sr_layout.addWidget(s_card)
            self._speed_cards[s_key] = s_card
            self._speed_main_labels[s_key] = s_card.findChild(QLabel, "main_lbl")

        sr_layout.addStretch()
        cfg_layout.addWidget(spd_row)

        # Codec row
        cod_row = QWidget()
        cod_row.setStyleSheet("background: transparent;")
        co_layout = QHBoxLayout(cod_row)
        co_layout.setContentsMargins(0, 0, 0, 0)
        co_layout.setSpacing(6)

        cod_lbl = QLabel("Codec")
        cod_lbl.setFixedWidth(90)
        cod_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        co_layout.addWidget(cod_lbl)

        for c_key, c_label in CODEC_OPTIONS:
            c_card = self._make_option_card(
                c_label,
                "",
                selected=(c_key == self._output_codec),
                on_click=lambda k=c_key: self._on_codec_change(k),
            )
            co_layout.addWidget(c_card)
            self._codec_cards[c_key] = c_card
            self._codec_main_labels[c_key] = c_card.findChild(QLabel, "main_lbl")

        co_layout.addStretch()
        cfg_layout.addWidget(cod_row)

        # Output dir row
        out_row = QWidget()
        out_row.setStyleSheet("background: transparent;")
        or_layout = QHBoxLayout(out_row)
        or_layout.setContentsMargins(0, 0, 0, 0)
        or_layout.setSpacing(8)

        out_lbl = QLabel("Lưu vào")
        out_lbl.setFixedWidth(90)
        out_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        or_layout.addWidget(out_lbl)

        self._out_entry = QLineEdit()
        self._out_entry.setPlaceholderText("Cùng thư mục với video gốc")
        self._out_entry.setFixedHeight(36)
        self._out_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.input};
                border: 1px solid {T.border2};
                border-radius: 8px;
                color: {T.text};
                font-size: 12px;
                padding: 0 8px;
            }}
        """)
        or_layout.addWidget(self._out_entry, 1)

        browse_out_btn = QPushButton("Duyệt...")
        browse_out_btn.setFixedSize(80, 36)
        browse_out_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 11px;"
        )
        browse_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        browse_out_btn.clicked.connect(self._browse_output)
        or_layout.addWidget(browse_out_btn)

        cfg_layout.addWidget(out_row)
        cfg_wrap_layout.addWidget(cfg)
        layout.addWidget(cfg_wrap)

        # Scroll area for file list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._scroll_content)
        self._items_layout.setContentsMargins(28, 12, 28, 80)
        self._items_layout.setSpacing(8)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Empty state
        self._empty = QWidget()
        self._empty.setStyleSheet("background: transparent;")
        empty_layout = QVBoxLayout(self._empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(8)

        QLabel("Chưa có file nào").setParent(None)
        el1 = QLabel("Chưa có file nào")
        el1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el1.setStyleSheet(f"color: {T.text3}; font-size: 16px; font-weight: bold;")
        empty_layout.addWidget(el1)
        el2 = QLabel('Nhấn "Thêm file" hoặc "Thêm thư mục" để chọn video')
        el2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el2.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        empty_layout.addWidget(el2)
        el3 = QLabel("Hỗ trợ: MP4, MKV, WebM, AVI, MOV, FLV, WMV, TS, 3GP…")
        el3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el3.setStyleSheet(f"color: {T.text3}; font-size: 10px;")
        empty_layout.addWidget(el3)
        self._empty.setMinimumHeight(200)

        self._items_layout.addWidget(self._empty)
        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

        # Bottom action bar
        bar = QFrame()
        bar.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface};
                border-top: 1px solid {T.border};
            }}
            QLabel {{ border: none; }}
        """)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 10, 20, 10)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        bar_layout.addWidget(self._status_lbl, 1)

        self._convert_btn = QPushButton("Chuyển đổi tất cả")
        self._convert_btn.setFixedSize(160, 40)
        self._convert_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 8px; font-size: 13px; font-weight: bold;"
        )
        self._convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_btn.clicked.connect(self._start_all)
        bar_layout.addWidget(self._convert_btn)

        layout.addWidget(bar)

    def _make_option_card(self, label: str, desc: str, selected: bool, on_click) -> _ClickableFrame:
        card = _ClickableFrame(on_click=on_click)
        card.setStyleSheet(f"""
            _ClickableFrame {{
                background-color: {T.primary_dim if selected else T.surface2};
                border: 1px solid {T.primary if selected else T.border};
                border-radius: 8px;
            }}
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(2)

        main_lbl = QLabel(label)
        main_lbl.setObjectName("main_lbl")
        main_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_lbl.setStyleSheet(
            f"color: {T.text if selected else T.text2}; font-size: 11px; font-weight: bold; background: transparent;"
        )
        main_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        card_layout.addWidget(main_lbl)

        if desc:
            desc_lbl = QLabel(desc)
            desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_lbl.setStyleSheet(f"color: {T.text3}; font-size: 9px; background: transparent;")
            desc_lbl.setWordWrap(True)
            desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            card_layout.addWidget(desc_lbl)

        return card

    def _update_card_selection(self, cards: dict, labels: dict, selected_key: str) -> None:
        for k, card in cards.items():
            sel = k == selected_key
            card.setStyleSheet(f"""
                _ClickableFrame {{
                    background-color: {T.primary_dim if sel else T.surface2};
                    border: 1px solid {T.primary if sel else T.border};
                    border-radius: 8px;
                }}
            """)
            lbl = labels.get(k)
            if lbl:
                lbl.setStyleSheet(
                    f"color: {T.text if sel else T.text2}; font-size: 11px; font-weight: bold; background: transparent;"
                )

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_quality_change(self, key: str) -> None:
        self._quality = key
        self._update_card_selection(self._quality_cards, self._quality_main_labels, key)
        if key == "custom":
            self._custom_row.show()
        else:
            self._custom_row.hide()

    def _on_speed_change(self, key: str) -> None:
        self._speed_preset = key
        self._update_card_selection(self._speed_cards, self._speed_main_labels, key)

    def _on_codec_change(self, key: str) -> None:
        self._output_codec = key
        self._update_card_selection(self._codec_cards, self._codec_main_labels, key)

    def _on_encoder_change(self, label: str) -> None:
        for key, opt_label in self._available_encoder_options:
            if opt_label == label:
                self._encoder_key = key
                return
        self._encoder_key = "cpu"

    def _validate_custom_quality(self) -> None:
        try:
            val = int(self._custom_entry.text())
        except ValueError:
            val = 23
        clamped = max(16, min(35, val))
        self._custom_quality_val = clamped
        self._custom_entry.setText(str(clamped))
        if val != clamped:
            self._custom_entry.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {T.input};
                    border: 1px solid {T.error};
                    border-radius: 6px;
                    color: {T.text};
                    font-size: 12px;
                    padding: 0 6px;
                }}
            """)
            QTimer.singleShot(
                1200,
                lambda: self._custom_entry.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {T.input};
                    border: 1px solid {T.border2};
                    border-radius: 6px;
                    color: {T.text};
                    font-size: 12px;
                    padding: 0 6px;
                }}
            """),
            )

    # ── Encoder detection ─────────────────────────────────────────────────────

    def _detect_encoders_async(self) -> None:
        available_opts = get_available_encoder_options()
        ui_bridge.post(lambda opts=available_opts: self._apply_available_encoders(opts))

    def _apply_available_encoders(self, available_opts: list[tuple[str, str]]) -> None:
        if not any(key == "cpu" for key, _ in available_opts):
            cpu_opt = next((o for o in ENCODER_OPTIONS if o[0] == "cpu"), ("cpu", "CPU (libx264)"))
            available_opts = [cpu_opt] + list(available_opts)

        self._available_encoder_options = available_opts
        self._available_encoders = {key for key, _ in available_opts}

        labels = [label for _, label in available_opts]
        self._encoder_combo.blockSignals(True)
        self._encoder_combo.clear()
        self._encoder_combo.addItems(labels)
        self._encoder_combo.blockSignals(False)

        if self._encoder_key not in self._available_encoders:
            self._encoder_key = "cpu"
            cpu_label = next((lbl for k, lbl in available_opts if k == "cpu"), labels[0])
            self._encoder_combo.setCurrentText(cpu_label)

        if not self._encoder_auto_selected:
            self._encoder_auto_selected = True
            gpu_opts = [(k, lbl) for k, lbl in available_opts if k != "cpu"]
            if gpu_opts and self._encoder_key == "cpu":
                best_key, best_label = gpu_opts[0]
                self._encoder_key = best_key
                self._encoder_combo.setCurrentText(best_label)

        gpu_labels = [lbl for k, lbl in available_opts if k != "cpu"]
        status = f"GPU: {', '.join(gpu_labels)}" if gpu_labels else "Chỉ CPU"
        self._encoder_status_lbl.setText(status)

    # ── File operations ───────────────────────────────────────────────────────

    def _browse_files(self) -> None:
        ext_filter = " ".join(f"*.{ext}" for ext in sorted(SUPPORTED_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Chọn video để chuyển đổi",
            "",
            f"Video files ({ext_filter});;All files (*.*)",
        )
        for p in paths:
            self._add_file(Path(p))
        self._refresh_ui()

    def _browse_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa video")
        if not d:
            return
        self._status_lbl.setText("Đang quét thư mục…")
        threading.Thread(
            target=self._scan_folder_async,
            args=(Path(d),),
            daemon=True,
            name="omnidl-folder-scan",
        ).start()

    def _scan_folder_async(self, folder: Path) -> None:
        found = scan_folder_for_media(folder)
        ui_bridge.post(lambda f=found, d=folder: self._add_files_from_scan(f, d))

    def _add_files_from_scan(self, files: list[Path], folder: Path) -> None:
        for f in files:
            self._add_file(f)
        self._refresh_ui()
        if not files:
            self._status_lbl.setText(f"Không tìm thấy video trong {folder.name}")
        else:
            self._status_lbl.setText(f"Đã thêm {len(files)} file từ {folder.name}")
            self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;")

    def _browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu file đã chuyển")
        if d:
            self._output_dir = Path(d)
            self._out_entry.setText(str(self._output_dir))

    def _add_file(self, path: Path) -> None:
        existing = {j.source.resolve() for j in self._jobs.values()}
        if path.resolve() in existing:
            return
        if path.suffix.lower().lstrip(".") not in SUPPORTED_EXTS:
            return
        job = FileJob(source=path)
        self._jobs[job.id] = job
        threading.Thread(
            target=self._probe_info_async,
            args=(job,),
            daemon=True,
            name=f"omnidl-probe-{path.stem[:16]}",
        ).start()

    def _probe_info_async(self, job: FileJob) -> None:
        info = probe_media_info(job.source)
        job.media_info = info
        ui_bridge.post(lambda j=job: self._update_card_info(j))

    def _update_card_info(self, job: FileJob) -> None:
        card = self._cards.get(job.id)
        if card:
            card.update_info(job.media_info)

    # ── Convert ───────────────────────────────────────────────────────────────

    def _start_all(self) -> None:
        pending = [j for j in self._jobs.values() if j.state == FileState.PENDING]
        if not pending:
            return

        out_entry_val = self._out_entry.text().strip()
        output_dir: Optional[Path] = None
        if out_entry_val:
            output_dir = Path(out_entry_val)
        elif self._output_dir:
            output_dir = self._output_dir

        encoder_key = self._encoder_key
        if encoder_key not in self._available_encoders:
            encoder_key = "cpu"

        self._validate_custom_quality()
        encode_settings = EncodeSettings(
            encoder_key=encoder_key,
            quality=self._quality,
            speed_preset=self._speed_preset,
            custom_quality=self._custom_quality_val,
            output_codec=self._output_codec,
        )

        self._convert_btn.setEnabled(False)

        for job in pending:
            job.state = FileState.QUEUED
            job.progress = 0.0
            self._active_count += 1
            self._rebuild_card(job)

        for job in pending:
            self._submit_job(job, self._quality, output_dir, encode_settings)

        self._refresh_ui()

    def _submit_job(
        self,
        job: FileJob,
        quality: str,
        output_dir: Optional[Path],
        encode_settings: Optional[EncodeSettings] = None,
    ) -> None:
        def on_start() -> None:
            job.state = FileState.CONVERTING
            job.progress = 0.0
            ui_bridge.post(lambda j=job: self._tick_card(j))

        def on_progress(pct: float) -> None:
            job.progress = pct
            ui_bridge.post(lambda j=job: self._tick_card(j))

        def on_done(out_path: Path) -> None:
            job.state = FileState.DONE
            job.progress = 100.0
            job.output = out_path
            ui_bridge.post(lambda j=job: self._finish_job(j))
            try:
                self._app.taildrop.send_converted_file(out_path)
            except Exception:
                logger.debug("Taildrop convert hook raised unexpectedly", exc_info=True)

        def on_error(msg: str) -> None:
            job.state = FileState.FAILED
            job.error_msg = msg
            ui_bridge.post(lambda j=job: self._finish_job(j))

        job.cancel_fn = self._queue.submit(
            source=job.source,
            quality=quality,
            output_dir=output_dir,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            on_start=on_start,
            encode_settings=encode_settings,
        )

    # ── Card management ───────────────────────────────────────────────────────

    def _rebuild_card(self, job: FileJob) -> None:
        old = self._cards.pop(job.id, None)
        if old:
            self._items_layout.removeWidget(old)
            old.deleteLater()
        card = FileCard(
            self._scroll_content,
            job,
            on_remove=self._remove_job,
            on_open_folder=self._open_output,
            on_cancel=self._cancel_job,
            on_delete_output=self._delete_output,
        )
        self._items_layout.insertWidget(self._items_layout.count(), card)
        self._cards[job.id] = card

    def _tick_card(self, job: FileJob) -> None:
        card = self._cards.get(job.id)
        if card:
            card.refresh()
        self._refresh_status()

    def _finish_job(self, job: FileJob) -> None:
        self._active_count -= 1
        card = self._cards.get(job.id)
        if card:
            card.refresh()
        self._refresh_status()
        if self._active_count == 0:
            self._convert_btn.setEnabled(True)

    def _refresh_ui(self) -> None:
        for jid in list(self._cards):
            if jid not in self._jobs:
                c = self._cards.pop(jid)
                self._items_layout.removeWidget(c)
                c.deleteLater()

        for job in self._jobs.values():
            if job.id not in self._cards:
                card = FileCard(
                    self._scroll_content,
                    job,
                    on_remove=self._remove_job,
                    on_open_folder=self._open_output,
                    on_cancel=self._cancel_job,
                    on_delete_output=self._delete_output,
                )
                self._items_layout.insertWidget(self._items_layout.count(), card)
                self._cards[job.id] = card

        self._empty.setVisible(not bool(self._jobs))
        self._refresh_status()

    def _refresh_status(self) -> None:
        total = len(self._jobs)
        done = sum(1 for j in self._jobs.values() if j.state == FileState.DONE)
        converting = sum(1 for j in self._jobs.values() if j.state == FileState.CONVERTING)
        queued = sum(1 for j in self._jobs.values() if j.state == FileState.QUEUED)
        failed = sum(1 for j in self._jobs.values() if j.state == FileState.FAILED)

        if total == 0:
            self._status_lbl.setText("")
            self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        elif converting > 0 or queued > 0:
            parts = []
            if converting:
                parts.append(f"Đang xử lý {converting}")
            if queued:
                parts.append(f"{queued} chờ")
            self._status_lbl.setText("  ·  ".join(parts) + f" / {total} file")
            self._status_lbl.setStyleSheet(f"color: {T.warning}; font-size: 11px;")
        elif done == total:
            self._status_lbl.setText(f"Hoàn tất {done}/{total} file ✓")
            self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;")
        else:
            parts2 = []
            if done:
                parts2.append(f"{done} xong")
            if failed:
                parts2.append(f"{failed} lỗi")
            pending = total - done - failed
            if pending:
                parts2.append(f"{pending} chờ")
            self._status_lbl.setText("  ·  ".join(parts2))
            self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")

    def _remove_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        self._refresh_ui()

    def _cancel_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job and job.cancel_fn is not None:
            job.cancel_fn()
        self._jobs.pop(job_id, None)
        self._refresh_ui()

    def _clear_done(self) -> None:
        done_ids = [jid for jid, j in self._jobs.items() if j.state in (FileState.DONE, FileState.FAILED)]
        for jid in done_ids:
            self._jobs.pop(jid, None)
        self._refresh_ui()

    def _open_output(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job or not job.output:
            return
        if job.output.is_file():
            if not reveal_in_explorer(job.output):
                open_folder(job.output.parent)
        elif job.output.parent.is_dir():
            open_folder(job.output.parent)

    def _delete_output(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job or not job.output:
            return
        if not job.output.is_file():
            card = self._cards.get(job_id)
            if card:
                card.refresh()
            return

        reply = QMessageBox.question(
            self,
            "Xóa file đã convert",
            f"Bạn có chắc muốn xóa file đã convert trên laptop không?\n\n"
            f"{job.output.name}\n\n"
            f"Hãy chắc chắn file đã được lưu trên iPhone trước khi xóa.\n"
            f"Thao tác này không thể hoàn tác.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            job.output.unlink()
        except OSError as exc:
            QMessageBox.critical(self, "Lỗi xóa file", f"Không thể xóa file:\n{exc}")
            return

        card = self._cards.get(job_id)
        if card:
            card.refresh()

    # ── Taildrop event handlers ───────────────────────────────────────────────

    def _on_convert_taildrop_completed(self, *, out_path: Path, dest_node: str, **_kw) -> None:
        msg = f"Đã gửi '{out_path.name}' đến {dest_node}"
        ui_bridge.post(
            lambda m=msg: (
                self._status_lbl.setText(m),
                self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;"),
            )
        )

    def _on_convert_taildrop_failed(self, *, out_path: Path, dest_node: str, error: str = "", **_kw) -> None:
        msg = f"Taildrop thất bại '{out_path.name}': {error}"
        ui_bridge.post(
            lambda m=msg: (
                self._status_lbl.setText(m),
                self._status_lbl.setStyleSheet(f"color: {T.error}; font-size: 11px;"),
            )
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
