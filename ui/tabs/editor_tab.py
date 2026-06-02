"""Video editor tab — preview, trim, rotate, and mute for downloaded files."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional
from uuid import uuid4

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.components.timeline_widget import TimelineWidget
from ui.signals import ui_bridge
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts", ".flv", ".wmv"})

_ROTATE_OPTIONS = [
    ("Không xoay", None),
    ("90° thuận chiều kim đồng hồ", 1),
    ("90° ngược chiều kim đồng hồ", 2),
    ("180°", 3),
]

_SPEED_LABELS = ["0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"]
_SPEED_VALUES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
_SPEED_DEFAULT = 2  # index for 1.0

_VOLUME_LABELS = ["0%", "50%", "75%", "100%", "150%", "200%"]
_VOLUME_VALUES = [0.0, 0.5, 0.75, 1.0, 1.5, 2.0]
_VOLUME_DEFAULT = 3  # index for 1.0

_TEXT_COLOR_LABELS = ["Trắng", "Đen", "Vàng", "Đỏ"]
_TEXT_COLOR_VALUES = ["white", "black", "yellow", "red"]


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _unique_output(source: Path) -> Path:
    stem = source.stem + "_trim"
    suf = source.suffix
    out = source.parent / f"{stem}{suf}"
    i = 1
    while out.exists():
        out = source.parent / f"{stem}_{i}{suf}"
        i += 1
    return out


class EditorTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._seeking = False
        self._in_ms: int = 0
        self._out_ms: int = -1
        self._cancel_trim: Optional[Callable[[], None]] = None
        self._current_source: Optional[Path] = None
        self._original_duration: int = 0
        self._preview_mode: bool = False
        self._preview_temp: Optional[Path] = None
        self._cancel_preview: Optional[Callable[[], None]] = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Header
        header = QLabel("Chỉnh sửa video")
        header.setStyleSheet(f"color: {T.text}; font-size: 20px; font-weight: 700;")
        layout.addWidget(header)

        # File picker row
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Chọn file video hoặc mở từ lịch sử tải xuống...")
        self._path_edit.setReadOnly(True)
        self._path_edit.setStyleSheet(
            f"background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 8px; padding: 6px 10px; font-size: 12px;"
        )
        file_row.addWidget(self._path_edit, 1)

        open_btn = QPushButton("📂  Mở file")
        open_btn.setFixedHeight(34)
        open_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 8px;"
            f" font-size: 12px; font-weight: 600; padding: 0 16px;"
        )
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(self._pick_file)
        file_row.addWidget(open_btn)

        self._clear_btn = QPushButton("X")
        self._clear_btn.setFixedSize(34, 34)
        self._clear_btn.setToolTip("Đóng file")
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: 1px solid {T.border};"
            f" border-radius: 8px; font-size: 14px; font-weight: 600; }}"
            f" QPushButton:hover {{ background: {T.error}; color: white; border-color: {T.error}; }}"
        )
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self._clear_file)
        file_row.addWidget(self._clear_btn)

        layout.addLayout(file_row)

        # Video widget
        self._video_widget = QVideoWidget()
        self._video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._video_widget.setStyleSheet("background: #000;")
        layout.addWidget(self._video_widget, 1)

        # Empty state label
        self._empty_lbl = QLabel("Chưa có video. Mở file hoặc nhấn 'Chỉnh sửa' từ lịch sử tải xuống.")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(f"color: {T.text3}; font-size: 13px;")
        self._video_widget.setVisible(False)
        layout.addWidget(self._empty_lbl)

        # Timeline widget (OpenReel-inspired visual timeline)
        self._timeline = TimelineWidget()
        self._timeline.setEnabled(False)
        self._timeline.seeked.connect(self._on_timeline_seek)
        self._timeline.in_changed.connect(self._on_timeline_in)
        self._timeline.out_changed.connect(self._on_timeline_out)
        layout.addWidget(self._timeline)

        # Controls row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)

        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(40, 40)
        self._play_btn.setEnabled(False)
        self._play_btn.setStyleSheet(
            f"QPushButton {{ background: {T.primary}; color: white; border: none;"
            f" border-radius: 20px; font-size: 14px; }}"
            f" QPushButton:hover {{ background: {T.primary_text}; }}"
            f" QPushButton:disabled {{ background: {T.surface3}; color: {T.text3}; }}"
        )
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.clicked.connect(self._toggle_play)
        ctrl_row.addWidget(self._play_btn)

        self._time_lbl = QLabel("0:00 / 0:00")
        self._time_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-family: monospace;")
        ctrl_row.addWidget(self._time_lbl)

        ctrl_row.addStretch()

        self._info_lbl = QLabel("")
        self._info_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        ctrl_row.addWidget(self._info_lbl)

        self._toggle_ctrl_btn = QPushButton("⊡ Ẩn")
        self._toggle_ctrl_btn.setFixedHeight(28)
        self._toggle_ctrl_btn.setEnabled(False)
        self._toggle_ctrl_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: 1px solid {T.border};"
            f" border-radius: 6px; font-size: 11px; padding: 0 10px; }}"
            f" QPushButton:hover {{ background: {T.surface3}; }}"
            f" QPushButton:disabled {{ color: {T.text3}; }}"
        )
        self._toggle_ctrl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_ctrl_btn.clicked.connect(self._toggle_controls)
        ctrl_row.addWidget(self._toggle_ctrl_btn)

        layout.addLayout(ctrl_row)

        # Controls panel — wraps trim + edit + speed/volume + text rows
        self._controls_panel = QWidget()
        self._controls_panel.setStyleSheet("background: transparent;")
        panel_layout = QVBoxLayout(self._controls_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        # ── Trim row ──────────────────────────────────────────────────────────
        trim_row = QHBoxLayout()
        trim_row.setSpacing(8)

        self._set_in_btn = QPushButton("[ Set In")
        self._set_in_btn.setFixedHeight(28)
        self._set_in_btn.setEnabled(False)
        self._set_in_btn.setStyleSheet(self._pill_style(T.primary_dim, T.primary_text))
        self._set_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_in_btn.clicked.connect(self._set_in)
        trim_row.addWidget(self._set_in_btn)

        self._in_lbl = QLabel("In: 0:00")
        self._in_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-family: monospace;")
        trim_row.addWidget(self._in_lbl)

        trim_row.addStretch()

        self._out_lbl = QLabel("Out: --:--")
        self._out_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-family: monospace;")
        trim_row.addWidget(self._out_lbl)

        self._set_out_btn = QPushButton("Set Out ]")
        self._set_out_btn.setFixedHeight(28)
        self._set_out_btn.setEnabled(False)
        self._set_out_btn.setStyleSheet(self._pill_style(T.primary_dim, T.primary_text))
        self._set_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_out_btn.clicked.connect(self._set_out)
        trim_row.addWidget(self._set_out_btn)

        panel_layout.addLayout(trim_row)

        # ── Edit options row ──────────────────────────────────────────────────
        edit_row = QHBoxLayout()
        edit_row.setSpacing(10)

        rotate_lbl = QLabel("Xoay:")
        rotate_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        edit_row.addWidget(rotate_lbl)

        self._rotate_combo = QComboBox()
        self._rotate_combo.addItems([label for label, _ in _ROTATE_OPTIONS])
        self._rotate_combo.setEnabled(False)
        self._rotate_combo.setFixedHeight(28)
        self._rotate_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        edit_row.addWidget(self._rotate_combo)

        self._mute_check = QCheckBox("Tắt tiếng")
        self._mute_check.setEnabled(False)
        self._mute_check.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        edit_row.addWidget(self._mute_check)

        edit_row.addStretch()

        self._preview_btn = QPushButton("▶ Xem thử")
        self._preview_btn.setFixedHeight(32)
        self._preview_btn.setEnabled(False)
        self._preview_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: 1px solid {T.border};"
            f" border-radius: 8px; font-size: 12px; font-weight: 600; padding: 0 14px; }}"
            f" QPushButton:hover {{ background: {T.surface3}; }}"
            f" QPushButton:disabled {{ background: {T.surface3}; color: {T.text3}; }}"
        )
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.clicked.connect(self._on_preview_click)
        edit_row.addWidget(self._preview_btn)

        self._export_btn = QPushButton("✂  Xuất")
        self._export_btn.setFixedHeight(32)
        self._export_btn.setEnabled(False)
        self._export_btn.setStyleSheet(
            f"QPushButton {{ background: #EC4899; color: white; border: none;"
            f" border-radius: 8px; font-size: 12px; font-weight: 600; padding: 0 18px; }}"
            f" QPushButton:hover {{ background: #DB2777; }}"
            f" QPushButton:disabled {{ background: {T.surface3}; color: {T.text3}; }}"
        )
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.clicked.connect(self._export)
        edit_row.addWidget(self._export_btn)

        self._export_status = QLabel("")
        self._export_status.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        edit_row.addWidget(self._export_status)

        panel_layout.addLayout(edit_row)

        # ── Speed + Volume row ────────────────────────────────────────────────
        sv_row = QHBoxLayout()
        sv_row.setSpacing(10)

        sv_row.addWidget(QLabel("Tốc độ:"))
        sv_row.itemAt(0).widget().setStyleSheet(f"color: {T.text2}; font-size: 11px;")

        self._speed_combo = QComboBox()
        self._speed_combo.addItems(_SPEED_LABELS)
        self._speed_combo.setCurrentIndex(_SPEED_DEFAULT)
        self._speed_combo.setEnabled(False)
        self._speed_combo.setFixedHeight(28)
        self._speed_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        sv_row.addWidget(self._speed_combo)

        sv_row.addSpacing(16)

        vol_lbl = QLabel("Âm lượng:")
        vol_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        sv_row.addWidget(vol_lbl)

        self._volume_combo = QComboBox()
        self._volume_combo.addItems(_VOLUME_LABELS)
        self._volume_combo.setCurrentIndex(_VOLUME_DEFAULT)
        self._volume_combo.setEnabled(False)
        self._volume_combo.setFixedHeight(28)
        self._volume_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        sv_row.addWidget(self._volume_combo)
        sv_row.addStretch()

        panel_layout.addLayout(sv_row)

        # ── Text overlay row ──────────────────────────────────────────────────
        text_row = QHBoxLayout()
        text_row.setSpacing(8)

        text_lbl = QLabel("Văn bản:")
        text_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_row.addWidget(text_lbl)

        self._text_input = QLineEdit()
        self._text_input.setMaxLength(80)
        self._text_input.setPlaceholderText("Văn bản chồng lên video...")
        self._text_input.setEnabled(False)
        self._text_input.setFixedHeight(28)
        self._text_input.setStyleSheet(
            f"background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px;"
        )
        text_row.addWidget(self._text_input, 1)

        pos_lbl = QLabel("Vị trí:")
        pos_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_row.addWidget(pos_lbl)

        self._text_pos_combo = QComboBox()
        self._text_pos_combo.addItems(["Trên", "Giữa", "Dưới"])
        self._text_pos_combo.setCurrentIndex(2)
        self._text_pos_combo.setEnabled(False)
        self._text_pos_combo.setFixedHeight(28)
        self._text_pos_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        text_row.addWidget(self._text_pos_combo)

        size_lbl = QLabel("Cỡ:")
        size_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_row.addWidget(size_lbl)

        self._text_size_spin = QSpinBox()
        self._text_size_spin.setRange(8, 72)
        self._text_size_spin.setValue(24)
        self._text_size_spin.setEnabled(False)
        self._text_size_spin.setFixedHeight(28)
        self._text_size_spin.setStyleSheet(
            f"QSpinBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 6px; font-size: 11px; }}"
        )
        text_row.addWidget(self._text_size_spin)

        color_lbl = QLabel("Màu:")
        color_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_row.addWidget(color_lbl)

        self._text_color_combo = QComboBox()
        self._text_color_combo.addItems(_TEXT_COLOR_LABELS)
        self._text_color_combo.setEnabled(False)
        self._text_color_combo.setFixedHeight(28)
        self._text_color_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        text_row.addWidget(self._text_color_combo)

        self._text_box_check = QCheckBox("Nền")
        self._text_box_check.setEnabled(False)
        self._text_box_check.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_row.addWidget(self._text_box_check)

        self._text_shadow_check = QCheckBox("Bóng")
        self._text_shadow_check.setEnabled(False)
        self._text_shadow_check.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_row.addWidget(self._text_shadow_check)

        panel_layout.addLayout(text_row)

        # ── Effects section (OpenReel-inspired) ───────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {T.border};")
        panel_layout.addWidget(sep)

        eff_header_row = QHBoxLayout()
        eff_lbl = QLabel("Hiệu ứng video")
        eff_lbl.setStyleSheet(f"color: {T.text}; font-size: 12px; font-weight: 700;")
        eff_header_row.addWidget(eff_lbl)
        eff_header_row.addStretch()
        self._toggle_effects_btn = QPushButton("+ Hiển thị")
        self._toggle_effects_btn.setFixedHeight(24)
        self._toggle_effects_btn.setEnabled(False)
        self._toggle_effects_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface3}; color: {T.text2}; border: 1px solid {T.border};"
            f" border-radius: 5px; font-size: 11px; padding: 0 8px; }}"
            f" QPushButton:hover {{ background: {T.surface2}; }}"
        )
        self._toggle_effects_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_effects_btn.clicked.connect(self._toggle_effects)
        eff_header_row.addWidget(self._toggle_effects_btn)
        panel_layout.addLayout(eff_header_row)

        self._effects_panel = QWidget()
        self._effects_panel.setVisible(False)
        eff_layout = QVBoxLayout(self._effects_panel)
        eff_layout.setContentsMargins(0, 4, 0, 0)
        eff_layout.setSpacing(6)

        bcs_row = QHBoxLayout()
        bcs_row.setSpacing(10)
        bcs_row.addWidget(self._mk_eff_label("Sáng:"))
        self._brightness_slider, self._brightness_val = self._mk_slider(-100, 100, 0)
        bcs_row.addWidget(self._brightness_slider, 2)
        bcs_row.addWidget(self._brightness_val)
        bcs_row.addSpacing(8)
        bcs_row.addWidget(self._mk_eff_label("Tương phản:"))
        self._contrast_slider, self._contrast_val = self._mk_slider(50, 300, 100)
        bcs_row.addWidget(self._contrast_slider, 2)
        bcs_row.addWidget(self._contrast_val)
        bcs_row.addSpacing(8)
        bcs_row.addWidget(self._mk_eff_label("Bão hòa:"))
        self._saturation_slider, self._saturation_val = self._mk_slider(0, 300, 100)
        bcs_row.addWidget(self._saturation_slider, 2)
        bcs_row.addWidget(self._saturation_val)
        eff_layout.addLayout(bcs_row)

        hb_row = QHBoxLayout()
        hb_row.setSpacing(10)
        hb_row.addWidget(self._mk_eff_label("Màu (hue):"))
        self._hue_slider, self._hue_val = self._mk_slider(-180, 180, 0)
        hb_row.addWidget(self._hue_slider, 2)
        hb_row.addWidget(self._hue_val)
        hb_row.addSpacing(8)
        hb_row.addWidget(self._mk_eff_label("Mờ (blur):"))
        self._blur_slider, self._blur_val = self._mk_slider(0, 100, 0)
        hb_row.addWidget(self._blur_slider, 2)
        hb_row.addWidget(self._blur_val)
        eff_layout.addLayout(hb_row)

        fade_row = QHBoxLayout()
        fade_row.setSpacing(10)
        fade_row.addWidget(self._mk_eff_label("Fade in:"))
        self._fade_in_slider, self._fade_in_val = self._mk_slider(0, 100, 0)
        fade_row.addWidget(self._fade_in_slider, 2)
        fade_row.addWidget(self._fade_in_val)
        fade_row.addSpacing(8)
        fade_row.addWidget(self._mk_eff_label("Fade out:"))
        self._fade_out_slider, self._fade_out_val = self._mk_slider(0, 100, 0)
        fade_row.addWidget(self._fade_out_slider, 2)
        fade_row.addWidget(self._fade_out_val)
        eff_layout.addLayout(fade_row)

        panel_layout.addWidget(self._effects_panel)

        layout.addWidget(self._controls_panel)

        # Media player
        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_error)

    @staticmethod
    def _pill_style(bg: str, fg: str) -> str:
        return (
            f"QPushButton {{ background: {bg}; color: {fg}; border: none;"
            f" border-radius: 6px; font-size: 11px; font-weight: 600; padding: 0 10px; }}"
            f" QPushButton:disabled {{ background: {T.surface3}; color: {T.text3}; }}"
        )

    @staticmethod
    def _mk_eff_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        return lbl

    def _mk_slider(self, lo: int, hi: int, val: int) -> tuple[QSlider, QLabel]:
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)
        sl.setEnabled(False)
        sl.setFixedHeight(20)
        sl.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 3px; background: {T.border}; border-radius: 1px; }}"
            f" QSlider::sub-page:horizontal {{ background: {T.primary}; border-radius: 1px; }}"
            f" QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -4px 0;"
            f" background: {T.primary}; border-radius: 5px; }}"
        )
        lbl = QLabel(str(val))
        lbl.setFixedWidth(36)
        lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; font-family: monospace;")
        sl.valueChanged.connect(lambda v, lb=lbl: lb.setText(str(v)))
        return sl, lbl

    def _toggle_effects(self) -> None:
        vis = self._effects_panel.isVisible()
        self._effects_panel.setVisible(not vis)
        self._toggle_effects_btn.setText("- Ẩn" if not vis else "+ Hiển thị")

    # ── Public ────────────────────────────────────────────────────────────────

    def load_file(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            logger.warning("editor_tab: file not found: %s", path)
            return
        self._cleanup_preview()
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(p)))
        self._path_edit.setText(str(p))
        self._current_source = p
        self._original_duration = 0
        self._play_btn.setEnabled(True)
        self._timeline.setEnabled(True)
        self._set_in_btn.setEnabled(True)
        self._set_out_btn.setEnabled(True)
        self._rotate_combo.setEnabled(True)
        self._mute_check.setEnabled(True)
        self._speed_combo.setEnabled(True)
        self._volume_combo.setEnabled(True)
        self._text_input.setEnabled(True)
        self._text_pos_combo.setEnabled(True)
        self._text_size_spin.setEnabled(True)
        self._text_color_combo.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._preview_btn.setEnabled(True)
        self._toggle_ctrl_btn.setEnabled(True)
        self._toggle_effects_btn.setEnabled(True)
        self._brightness_slider.setEnabled(True)
        self._contrast_slider.setEnabled(True)
        self._saturation_slider.setEnabled(True)
        self._hue_slider.setEnabled(True)
        self._blur_slider.setEnabled(True)
        self._fade_in_slider.setEnabled(True)
        self._fade_out_slider.setEnabled(True)
        self._text_box_check.setEnabled(True)
        self._text_shadow_check.setEnabled(True)
        self._clear_btn.setVisible(True)
        self._empty_lbl.setVisible(False)
        self._video_widget.setVisible(True)
        self._info_lbl.setText(p.name)
        self._in_ms = 0
        self._out_ms = -1
        self._in_lbl.setText("In: 0:00")
        self._out_lbl.setText("Out: --:--")
        self._rotate_combo.setCurrentIndex(0)
        self._mute_check.setChecked(False)
        self._speed_combo.setCurrentIndex(_SPEED_DEFAULT)
        self._volume_combo.setCurrentIndex(_VOLUME_DEFAULT)
        self._text_input.clear()
        self._text_pos_combo.setCurrentIndex(2)
        self._text_size_spin.setValue(24)
        self._text_color_combo.setCurrentIndex(0)
        self._text_box_check.setChecked(False)
        self._text_shadow_check.setChecked(False)
        self._brightness_slider.setValue(0)
        self._contrast_slider.setValue(100)
        self._saturation_slider.setValue(100)
        self._hue_slider.setValue(0)
        self._blur_slider.setValue(0)
        self._fade_in_slider.setValue(0)
        self._fade_out_slider.setValue(0)
        self._timeline.reset()
        self._timeline.set_in(0)
        self._export_status.setText("")
        self._cancel_trim = None
        self._export_btn.setText("✂  Xuất")
        self._preview_btn.setText("▶ Xem thử")
        if not self._controls_panel.isVisible():
            self._controls_panel.setVisible(True)
            self._toggle_ctrl_btn.setText("⊡ Ẩn")
        self._player.play()

    def _clear_file(self) -> None:
        self._cleanup_preview()
        if self._cancel_trim is not None:
            self._cancel_trim()
            self._cancel_trim = None
        self._player.stop()
        self._player.setSource(QUrl())
        self._path_edit.clear()
        self._current_source = None
        self._original_duration = 0
        self._play_btn.setEnabled(False)
        self._timeline.setEnabled(False)
        self._timeline.reset()
        self._set_in_btn.setEnabled(False)
        self._set_out_btn.setEnabled(False)
        self._rotate_combo.setEnabled(False)
        self._mute_check.setEnabled(False)
        self._speed_combo.setEnabled(False)
        self._volume_combo.setEnabled(False)
        self._text_input.setEnabled(False)
        self._text_pos_combo.setEnabled(False)
        self._text_size_spin.setEnabled(False)
        self._text_color_combo.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._toggle_ctrl_btn.setEnabled(False)
        self._toggle_effects_btn.setEnabled(False)
        self._brightness_slider.setEnabled(False)
        self._contrast_slider.setEnabled(False)
        self._saturation_slider.setEnabled(False)
        self._hue_slider.setEnabled(False)
        self._blur_slider.setEnabled(False)
        self._fade_in_slider.setEnabled(False)
        self._fade_out_slider.setEnabled(False)
        self._text_box_check.setEnabled(False)
        self._text_shadow_check.setEnabled(False)
        self._clear_btn.setVisible(False)
        self._video_widget.setVisible(False)
        self._empty_lbl.setVisible(True)
        self._time_lbl.setText("0:00 / 0:00")
        self._info_lbl.setText("")
        self._export_status.setText("")
        self._in_ms = 0
        self._out_ms = -1
        self._in_lbl.setText("In: 0:00")
        self._out_lbl.setText("Out: --:--")
        self._export_btn.setText("✂  Xuất")
        self._preview_btn.setText("▶ Xem thử")

    def _cleanup_preview(self) -> None:
        if self._cancel_preview is not None:
            self._cancel_preview()
            self._cancel_preview = None
        if self._preview_temp is not None:
            self._preview_temp.unlink(missing_ok=True)
            self._preview_temp = None
        self._preview_mode = False

    # ── Toggle controls ───────────────────────────────────────────────────────

    def _toggle_controls(self) -> None:
        visible = self._controls_panel.isVisible()
        self._controls_panel.setVisible(not visible)
        self._toggle_ctrl_btn.setText("⊞ Hiện" if visible else "⊡ Ẩn")

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_preview_click(self) -> None:
        if self._preview_mode:
            self._back_to_original()
        elif self._cancel_preview is not None:
            self._cancel_preview()
            self._cancel_preview = None
            self._preview_btn.setText("▶ Xem thử")
            self._export_status.setText("")
        else:
            self._start_preview()

    def _start_preview(self) -> None:
        if not self._current_source:
            return
        end_ms = self._out_ms if self._out_ms >= 0 else self._original_duration
        preview_dur = min(10000, max(1000, end_ms - self._in_ms))
        temp = Path(tempfile.gettempdir()) / f"omnidl_preview_{uuid4().hex[:8]}.mp4"

        self._preview_btn.setText("⏳ Đang tạo...")
        self._export_status.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        self._export_status.setText("Đang tạo xem thử...")

        from app.services.ffmpeg_trim_service import trim_video

        def _on_done(path: Path) -> None:
            ui_bridge.post(lambda p=path: self._on_preview_done(p))

        def _on_err(msg: str) -> None:
            ui_bridge.post(lambda m=msg: self._on_preview_error(m))

        self._cancel_preview = trim_video(
            self._current_source,
            temp,
            self._in_ms,
            self._in_ms + preview_dur,
            **self._collect_params(),
            on_done=_on_done,
            on_error=_on_err,
        )

    def _on_preview_done(self, temp_path: Path) -> None:
        self._cancel_preview = None
        self._preview_mode = True
        self._preview_temp = temp_path
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(temp_path)))
        self._player.play()
        self._preview_btn.setText("← Gốc")
        self._export_status.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        self._export_status.setText("Đang xem thử (10s)")

    def _on_preview_error(self, msg: str) -> None:
        self._cancel_preview = None
        self._preview_btn.setText("▶ Xem thử")
        self._export_status.setStyleSheet(f"color: {T.error}; font-size: 11px;")
        self._export_status.setText(f"Lỗi xem thử: {msg[:60]}")

    def _back_to_original(self) -> None:
        self._preview_mode = False
        old_temp = self._preview_temp
        self._preview_temp = None
        self._player.stop()
        if self._current_source:
            self._player.setSource(QUrl.fromLocalFile(str(self._current_source)))
            self._player.play()
        if old_temp is not None:
            old_temp.unlink(missing_ok=True)
        self._preview_btn.setText("▶ Xem thử")
        self._export_status.setText("")

    # ── Trim controls ─────────────────────────────────────────────────────────

    def _set_in(self) -> None:
        self._in_ms = self._player.position()
        self._in_lbl.setText(f"In: {_fmt_ms(self._in_ms)}")
        self._timeline.set_in(self._in_ms)

    def _set_out(self) -> None:
        self._out_ms = self._player.position()
        self._out_lbl.setText(f"Out: {_fmt_ms(self._out_ms)}")
        self._timeline.set_out(self._out_ms)

    def _collect_params(self) -> dict:
        return dict(
            rotate=_ROTATE_OPTIONS[self._rotate_combo.currentIndex()][1],
            mute=self._mute_check.isChecked(),
            speed=_SPEED_VALUES[self._speed_combo.currentIndex()],
            volume=_VOLUME_VALUES[self._volume_combo.currentIndex()],
            text=self._text_input.text().strip(),
            text_pos=self._text_pos_combo.currentIndex(),
            text_size=self._text_size_spin.value(),
            text_color=_TEXT_COLOR_VALUES[self._text_color_combo.currentIndex()],
            text_box=self._text_box_check.isChecked(),
            text_shadow=self._text_shadow_check.isChecked(),
            brightness=self._brightness_slider.value() / 100.0,
            contrast=self._contrast_slider.value() / 100.0,
            saturation=self._saturation_slider.value() / 100.0,
            hue=float(self._hue_slider.value()),
            blur=self._blur_slider.value() / 10.0,
            fade_in_s=self._fade_in_slider.value() / 10.0,
            fade_out_s=self._fade_out_slider.value() / 10.0,
        )

    def _export(self) -> None:
        if self._cancel_trim is not None:
            self._cancel_trim()
            self._cancel_trim = None
            return

        src_text = self._path_edit.text()
        if not src_text:
            return
        source = Path(src_text)
        dur = self._original_duration if self._original_duration > 0 else self._player.duration()
        start_ms = self._in_ms
        end_ms = self._out_ms if self._out_ms >= 0 else dur

        if end_ms <= start_ms:
            self._export_status.setStyleSheet(f"color: {T.error}; font-size: 11px;")
            self._export_status.setText("In phải nhỏ hơn Out")
            return

        output = _unique_output(source)

        self._export_btn.setText("Huỷ")
        self._export_status.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        self._export_status.setText("Đang xuất...")

        from app.services.ffmpeg_trim_service import trim_video

        def _on_progress(pct: float) -> None:
            ui_bridge.post(lambda p=pct: self._export_status.setText(f"{p:.0f}%"))

        def _on_done(path: Path) -> None:
            ui_bridge.post(lambda p=path: self._finish_export(p))

        def _on_error(msg: str) -> None:
            ui_bridge.post(lambda m=msg: self._fail_export(m))

        self._cancel_trim = trim_video(
            source,
            output,
            start_ms,
            end_ms,
            **self._collect_params(),
            on_progress=_on_progress,
            on_done=_on_done,
            on_error=_on_error,
        )

    def _finish_export(self, path: Path) -> None:
        self._cancel_trim = None
        self._export_btn.setText("✂  Xuất")
        self._export_status.setStyleSheet(f"color: {T.success}; font-size: 11px;")
        self._export_status.setText(f"Đã lưu: {path.name}")

    def _fail_export(self, msg: str) -> None:
        self._cancel_trim = None
        self._export_btn.setText("✂  Xuất")
        self._export_status.setStyleSheet(f"color: {T.error}; font-size: 11px;")
        self._export_status.setText(f"Lỗi: {msg[:80]}")

    # ── Playback handlers ─────────────────────────────────────────────────────

    def _pick_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(_VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file video", "", f"Video ({exts});;Tất cả (*)")
        if path:
            self.load_file(path)

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_timeline_seek(self, ms: int) -> None:
        self._seeking = True
        self._player.setPosition(ms)
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, self._clear_seeking)

    def _clear_seeking(self) -> None:
        self._seeking = False

    def _on_timeline_in(self, ms: int) -> None:
        self._in_ms = ms
        self._in_lbl.setText(f"In: {_fmt_ms(ms)}")

    def _on_timeline_out(self, ms: int) -> None:
        self._out_ms = ms
        self._out_lbl.setText(f"Out: {_fmt_ms(ms)}")

    def _on_position_changed(self, pos: int) -> None:
        self._time_lbl.setText(f"{_fmt_ms(pos)} / {_fmt_ms(self._player.duration())}")
        if not self._seeking:
            self._timeline.set_position(pos)

    def _on_duration_changed(self, dur: int) -> None:
        self._timeline.set_duration(dur)
        self._time_lbl.setText(f"0:00 / {_fmt_ms(dur)}")
        if not self._preview_mode:
            self._original_duration = dur
            if self._out_ms < 0:
                self._out_lbl.setText(f"Out: {_fmt_ms(dur)}")
                self._timeline.set_out(-1)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("⏸")
        else:
            self._play_btn.setText("▶")

    def _on_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            logger.error("QMediaPlayer error: %s - %s", error, error_string)
            self._info_lbl.setText(f"Lỗi: {error_string}")
