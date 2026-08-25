"""Video editor tab — preview, trim, rotate, and mute for downloaded files."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.services.ffmpeg_trim_service import trim_video
from ui.components.frame_processor import EffectParams, FrameProcessor
from ui.components.timeline_widget import TimelineWidget
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.i18n import t

if TYPE_CHECKING:
    from PySide6.QtMultimedia import QVideoFrame

    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_VIDEO_EXTS = frozenset({".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".ts", ".flv", ".wmv"})

_ROTATE_KEYS = [
    ("editor.rotate.none", None),
    ("editor.rotate.cw90", 1),
    ("editor.rotate.ccw90", 2),
    ("editor.rotate.180", 3),
]

_SPEED_LABELS = ["0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x"]
_SPEED_VALUES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
_SPEED_DEFAULT = 2  # index for 1.0

_VOLUME_LABELS = ["0%", "50%", "75%", "100%", "150%", "200%"]
_VOLUME_VALUES = [0.0, 0.5, 0.75, 1.0, 1.5, 2.0]
_VOLUME_DEFAULT = 3  # index for 1.0

_TEXT_COLOR_KEYS = ["editor.color.white", "editor.color.black", "editor.color.yellow", "editor.color.red"]
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


class _AspectRatioLabel(QWidget):
    """Black-background widget that paints a QPixmap centered with aspect ratio preserved."""

    def __init__(self) -> None:
        super().__init__()
        self._pixmap: Optional[QPixmap] = None
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background: #000; border-radius: 4px;")

    def set_frame(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def clear_frame(self) -> None:
        self._pixmap = None
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._pixmap or self._pixmap.isNull():
            return
        painter = QPainter(self)
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()


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
        self._preview_gen: int = 0
        self._frame_processor = FrameProcessor()
        self._saved_effect_params: EffectParams = EffectParams()
        self._file_path: str = ""
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

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(12)

        # ── Main splitter ─────────────────────────────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(4)
        self._splitter.setStyleSheet(f"QSplitter::handle {{ background: {T.border}; border-radius: 2px; }}")
        layout.addWidget(self._splitter, 1)

        # ── LEFT: preview + timeline + play controls ──────────────────────────
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(8)

        self._preview_label = _AspectRatioLabel()
        self._preview_label.setVisible(False)
        left_layout.addWidget(self._preview_label, 1)

        self._empty_lbl = QLabel(t("editor.empty"))
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color: {T.text3}; font-size: 13px; background: {T.surface2}; border-radius: 8px; padding: 40px;"
        )
        self._empty_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self._empty_lbl, 1)

        self._timeline = TimelineWidget()
        self._timeline.setEnabled(False)
        self._timeline.seeked.connect(self._on_timeline_seek)
        self._timeline.in_changed.connect(self._on_timeline_in)
        self._timeline.out_changed.connect(self._on_timeline_out)
        left_layout.addWidget(self._timeline)

        # Play controls row (inside left panel)
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

        self._toggle_ctrl_btn = QPushButton(t("editor.hide_panel"))
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

        left_layout.addLayout(ctrl_row)
        self._splitter.addWidget(left_panel)

        # ── RIGHT: scrollable controls panel ─────────────────────────────────
        self._right_scroll = QScrollArea()
        self._right_scroll.setWidgetResizable(True)
        self._right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._right_scroll.setMinimumWidth(320)
        self._right_scroll.setMaximumWidth(460)
        self._right_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            f" QScrollBar:vertical {{ background: {T.surface2}; width: 6px; border-radius: 3px; }}"
            f" QScrollBar::handle:vertical {{ background: {T.border}; border-radius: 3px; }}"
        )

        self._controls_panel = QWidget()
        self._controls_panel.setStyleSheet("background: transparent;")
        panel_layout = QVBoxLayout(self._controls_panel)
        panel_layout.setContentsMargins(6, 0, 0, 0)
        panel_layout.setSpacing(8)

        # ── File buttons row ──────────────────────────────────────────────────
        file_btn_row = QHBoxLayout()
        file_btn_row.setSpacing(6)

        self._open_btn = QPushButton(t("editor.open_file"))
        self._open_btn.setFixedHeight(30)
        self._open_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 8px;"
            f" font-size: 12px; font-weight: 600; padding: 0 16px;"
        )
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(self._pick_file)
        file_btn_row.addWidget(self._open_btn)

        self._clear_btn = QPushButton(t("editor.close_file"))
        self._clear_btn.setFixedHeight(30)
        self._clear_btn.setToolTip(t("editor.close_file_tip"))
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: 1px solid {T.border};"
            f" border-radius: 8px; font-size: 12px; font-weight: 600; padding: 0 12px; }}"
            f" QPushButton:hover {{ background: {T.error}; color: white; border-color: {T.error}; }}"
        )
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self._clear_file)
        file_btn_row.addWidget(self._clear_btn)
        file_btn_row.addStretch()

        panel_layout.addLayout(file_btn_row)

        # ── Trim rows ─────────────────────────────────────────────────────────
        in_row = QHBoxLayout()
        in_row.setSpacing(8)

        self._set_in_btn = QPushButton(t("editor.set_in"))
        self._set_in_btn.setFixedHeight(28)
        self._set_in_btn.setEnabled(False)
        self._set_in_btn.setStyleSheet(self._pill_style(T.primary_dim, T.primary_text))
        self._set_in_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_in_btn.clicked.connect(self._set_in)
        in_row.addWidget(self._set_in_btn)

        self._in_lbl = QLabel(t("editor.in_label", time="0:00"))
        self._in_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-family: monospace;")
        in_row.addWidget(self._in_lbl)
        in_row.addStretch()
        panel_layout.addLayout(in_row)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)

        self._set_out_btn = QPushButton(t("editor.set_out"))
        self._set_out_btn.setFixedHeight(28)
        self._set_out_btn.setEnabled(False)
        self._set_out_btn.setStyleSheet(self._pill_style(T.primary_dim, T.primary_text))
        self._set_out_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_out_btn.clicked.connect(self._set_out)
        out_row.addWidget(self._set_out_btn)

        self._out_lbl = QLabel(t("editor.out_placeholder"))
        self._out_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; font-family: monospace;")
        out_row.addWidget(self._out_lbl)
        out_row.addStretch()
        panel_layout.addLayout(out_row)

        # ── Edit options row ──────────────────────────────────────────────────
        edit_row = QHBoxLayout()
        edit_row.setSpacing(10)

        self._rotate_lbl = QLabel(t("editor.rotate_label"))
        self._rotate_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        edit_row.addWidget(self._rotate_lbl)

        self._rotate_combo = QComboBox()
        self._rotate_combo.addItems([t(key) for key, _ in _ROTATE_KEYS])
        self._rotate_combo.setEnabled(False)
        self._rotate_combo.setFixedHeight(28)
        self._rotate_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        edit_row.addWidget(self._rotate_combo)

        self._mute_check = QCheckBox(t("editor.mute"))
        self._mute_check.setEnabled(False)
        self._mute_check.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        edit_row.addWidget(self._mute_check)

        edit_row.addStretch()
        panel_layout.addLayout(edit_row)

        # ── Export row ────────────────────────────────────────────────────────
        export_row = QHBoxLayout()
        export_row.setSpacing(8)

        self._preview_btn = QPushButton(t("editor.preview_btn"))
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
        export_row.addWidget(self._preview_btn)

        self._export_btn = QPushButton(t("editor.export_btn"))
        self._export_btn.setFixedHeight(32)
        self._export_btn.setEnabled(False)
        self._export_btn.setStyleSheet(
            f"QPushButton {{ background: {T.edit_text}; color: white; border: none;"
            f" border-radius: 8px; font-size: 12px; font-weight: 600; padding: 0 18px; }}"
            f" QPushButton:hover {{ background: #DB2777; }}"
            f" QPushButton:disabled {{ background: {T.surface3}; color: {T.text3}; }}"
        )
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.clicked.connect(self._export)
        export_row.addWidget(self._export_btn)

        self._export_status = QLabel("")
        self._export_status.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        export_row.addWidget(self._export_status, 1)

        panel_layout.addLayout(export_row)

        # ── Speed + Volume row ────────────────────────────────────────────────
        sv_row = QHBoxLayout()
        sv_row.setSpacing(10)

        self._speed_lbl = QLabel(t("editor.speed_label"))
        self._speed_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        sv_row.addWidget(self._speed_lbl)

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

        self._vol_lbl = QLabel(t("editor.volume_label"))
        self._vol_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        sv_row.addWidget(self._vol_lbl)

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

        # ── Text overlay: input line ──────────────────────────────────────────
        text_input_row = QHBoxLayout()
        text_input_row.setSpacing(8)

        self._text_lbl = QLabel(t("editor.text_label"))
        self._text_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_input_row.addWidget(self._text_lbl)

        self._text_input = QLineEdit()
        self._text_input.setMaxLength(80)
        self._text_input.setPlaceholderText(t("editor.text_placeholder"))
        self._text_input.setEnabled(False)
        self._text_input.setFixedHeight(28)
        self._text_input.setStyleSheet(
            f"background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px;"
        )
        text_input_row.addWidget(self._text_input, 1)

        panel_layout.addLayout(text_input_row)

        # ── Text overlay: options line ────────────────────────────────────────
        text_opt_row = QHBoxLayout()
        text_opt_row.setSpacing(8)

        self._pos_lbl = QLabel(t("editor.position_label"))
        self._pos_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_opt_row.addWidget(self._pos_lbl)

        self._text_pos_combo = QComboBox()
        self._text_pos_combo.addItems([t("editor.pos.top"), t("editor.pos.middle"), t("editor.pos.bottom")])
        self._text_pos_combo.setCurrentIndex(2)
        self._text_pos_combo.setEnabled(False)
        self._text_pos_combo.setFixedHeight(28)
        self._text_pos_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        text_opt_row.addWidget(self._text_pos_combo)

        self._size_lbl = QLabel(t("editor.size_label"))
        self._size_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_opt_row.addWidget(self._size_lbl)

        self._text_size_spin = QSpinBox()
        self._text_size_spin.setRange(8, 72)
        self._text_size_spin.setValue(24)
        self._text_size_spin.setEnabled(False)
        self._text_size_spin.setFixedHeight(28)
        self._text_size_spin.setStyleSheet(
            f"QSpinBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 6px; font-size: 11px; }}"
        )
        text_opt_row.addWidget(self._text_size_spin)

        self._color_lbl = QLabel(t("editor.color_label"))
        self._color_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_opt_row.addWidget(self._color_lbl)

        self._text_color_combo = QComboBox()
        self._text_color_combo.addItems([t(key) for key in _TEXT_COLOR_KEYS])
        self._text_color_combo.setEnabled(False)
        self._text_color_combo.setFixedHeight(28)
        self._text_color_combo.setStyleSheet(
            f"QComboBox {{ background: {T.surface2}; color: {T.text}; border: 1px solid {T.border};"
            f" border-radius: 6px; padding: 0 8px; font-size: 11px; }}"
            f" QComboBox::drop-down {{ border: none; }}"
        )
        text_opt_row.addWidget(self._text_color_combo)

        self._text_box_check = QCheckBox(t("editor.box"))
        self._text_box_check.setEnabled(False)
        self._text_box_check.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_opt_row.addWidget(self._text_box_check)

        self._text_shadow_check = QCheckBox(t("editor.shadow"))
        self._text_shadow_check.setEnabled(False)
        self._text_shadow_check.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        text_opt_row.addWidget(self._text_shadow_check)

        text_opt_row.addStretch()
        panel_layout.addLayout(text_opt_row)

        # ── Effects section ───────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {T.border};")
        panel_layout.addWidget(sep)

        eff_header_row = QHBoxLayout()
        self._eff_lbl = QLabel(t("editor.effects_title"))
        self._eff_lbl.setStyleSheet(f"color: {T.text}; font-size: 12px; font-weight: 700;")
        eff_header_row.addWidget(self._eff_lbl)
        eff_header_row.addStretch()
        self._toggle_effects_btn = QPushButton(t("editor.hide"))
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
        self._effects_panel.setVisible(True)
        eff_layout = QVBoxLayout(self._effects_panel)
        eff_layout.setContentsMargins(0, 4, 0, 0)
        eff_layout.setSpacing(6)

        bcs_row = QHBoxLayout()
        bcs_row.setSpacing(10)
        self._brightness_lbl = self._mk_eff_label(t("editor.brightness"))
        bcs_row.addWidget(self._brightness_lbl)
        self._brightness_slider, self._brightness_val = self._mk_slider(-100, 100, 0)
        bcs_row.addWidget(self._brightness_slider, 2)
        bcs_row.addWidget(self._brightness_val)
        bcs_row.addSpacing(8)
        self._contrast_lbl = self._mk_eff_label(t("editor.contrast"))
        bcs_row.addWidget(self._contrast_lbl)
        self._contrast_slider, self._contrast_val = self._mk_slider(50, 300, 100)
        bcs_row.addWidget(self._contrast_slider, 2)
        bcs_row.addWidget(self._contrast_val)
        bcs_row.addSpacing(8)
        self._saturation_lbl = self._mk_eff_label(t("editor.saturation"))
        bcs_row.addWidget(self._saturation_lbl)
        self._saturation_slider, self._saturation_val = self._mk_slider(0, 300, 100)
        bcs_row.addWidget(self._saturation_slider, 2)
        bcs_row.addWidget(self._saturation_val)
        eff_layout.addLayout(bcs_row)

        hb_row = QHBoxLayout()
        hb_row.setSpacing(10)
        self._hue_lbl = self._mk_eff_label(t("editor.hue"))
        hb_row.addWidget(self._hue_lbl)
        self._hue_slider, self._hue_val = self._mk_slider(-180, 180, 0)
        hb_row.addWidget(self._hue_slider, 2)
        hb_row.addWidget(self._hue_val)
        hb_row.addSpacing(8)
        self._blur_lbl = self._mk_eff_label(t("editor.blur"))
        hb_row.addWidget(self._blur_lbl)
        self._blur_slider, self._blur_val = self._mk_slider(0, 100, 0)
        hb_row.addWidget(self._blur_slider, 2)
        hb_row.addWidget(self._blur_val)
        eff_layout.addLayout(hb_row)

        fade_row = QHBoxLayout()
        fade_row.setSpacing(10)
        self._fade_in_lbl = self._mk_eff_label(t("editor.fade_in"))
        fade_row.addWidget(self._fade_in_lbl)
        self._fade_in_slider, self._fade_in_val = self._mk_slider(0, 100, 0)
        fade_row.addWidget(self._fade_in_slider, 2)
        fade_row.addWidget(self._fade_in_val)
        fade_row.addSpacing(8)
        self._fade_out_lbl = self._mk_eff_label(t("editor.fade_out"))
        fade_row.addWidget(self._fade_out_lbl)
        self._fade_out_slider, self._fade_out_val = self._mk_slider(0, 100, 0)
        fade_row.addWidget(self._fade_out_slider, 2)
        fade_row.addWidget(self._fade_out_val)
        eff_layout.addLayout(fade_row)

        panel_layout.addWidget(self._effects_panel)
        panel_layout.addStretch()

        # Wire live preview signals
        self._text_input.textChanged.connect(self._update_effect_params)
        self._text_pos_combo.currentIndexChanged.connect(self._update_effect_params)
        self._text_size_spin.valueChanged.connect(self._update_effect_params)
        self._text_color_combo.currentIndexChanged.connect(self._update_effect_params)
        self._text_box_check.toggled.connect(self._update_effect_params)
        self._text_shadow_check.toggled.connect(self._update_effect_params)
        self._brightness_slider.valueChanged.connect(self._update_effect_params)
        self._contrast_slider.valueChanged.connect(self._update_effect_params)
        self._saturation_slider.valueChanged.connect(self._update_effect_params)
        self._hue_slider.valueChanged.connect(self._update_effect_params)
        self._blur_slider.valueChanged.connect(self._update_effect_params)

        self._right_scroll.setWidget(self._controls_panel)
        self._splitter.addWidget(self._right_scroll)

        # Splitter proportions: preview expands, controls fixed
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([700, 380])

        # Media player with QVideoSink for frame capture
        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._video_sink = QVideoSink(self._player)
        self._player.setVideoOutput(self._video_sink)
        self._video_sink.videoFrameChanged.connect(self._on_video_frame)
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
        self._toggle_effects_btn.setText(t("editor.hide") if not vis else t("editor.show"))

    # ── Public ────────────────────────────────────────────────────────────────

    def load_file(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            logger.warning("editor_tab: file not found: %s", path)
            return
        self._cleanup_preview()
        if self._cancel_trim is not None:
            self._cancel_trim()
            self._cancel_trim = None
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(p)))
        self._file_path = str(p)
        self._current_source = p
        self._original_duration = 0
        self._set_all_controls_enabled(True)
        self._clear_btn.setVisible(True)
        self._empty_lbl.setVisible(False)
        self._preview_label.setVisible(True)
        self._frame_processor.update_params(EffectParams())
        self._saved_effect_params = EffectParams()
        self._info_lbl.setText(p.name)
        self._in_ms = 0
        self._out_ms = -1
        self._in_lbl.setText(t("editor.in_label", time="0:00"))
        self._out_lbl.setText(t("editor.out_placeholder"))
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
        self._export_btn.setText(t("editor.export_btn"))
        self._preview_btn.setText(t("editor.preview_btn"))
        if not self._right_scroll.isVisible():
            self._right_scroll.setVisible(True)
            self._toggle_ctrl_btn.setText(t("editor.hide_panel"))
        self._player.play()

    def _clear_file(self) -> None:
        self._cleanup_preview()
        if self._cancel_trim is not None:
            self._cancel_trim()
            self._cancel_trim = None
        self._player.stop()
        self._player.setSource(QUrl())
        self._file_path = ""
        self._current_source = None
        self._original_duration = 0
        self._set_all_controls_enabled(False)
        self._timeline.reset()
        self._clear_btn.setVisible(False)
        self._preview_label.clear_frame()
        self._preview_label.setVisible(False)
        self._frame_processor.update_params(EffectParams())
        self._empty_lbl.setVisible(True)
        self._time_lbl.setText("0:00 / 0:00")
        self._info_lbl.setText("")
        self._export_status.setText("")
        self._in_ms = 0
        self._out_ms = -1
        self._in_lbl.setText(t("editor.in_label", time="0:00"))
        self._out_lbl.setText(t("editor.out_placeholder"))
        self._export_btn.setText(t("editor.export_btn"))
        self._preview_btn.setText(t("editor.preview_btn"))

    def _cleanup_preview(self) -> None:
        self._preview_gen += 1
        if self._cancel_preview is not None:
            self._cancel_preview()
            self._cancel_preview = None
        if self._preview_temp is not None:
            self._preview_temp.unlink(missing_ok=True)
            self._preview_temp = None
        self._preview_mode = False

    # ── Toggle controls ───────────────────────────────────────────────────────

    def _toggle_controls(self) -> None:
        visible = self._right_scroll.isVisible()
        self._right_scroll.setVisible(not visible)
        self._toggle_ctrl_btn.setText(t("editor.show_panel") if visible else t("editor.hide_panel"))

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_preview_click(self) -> None:
        if self._cancel_trim is not None:
            return
        if self._preview_mode:
            self._back_to_original()
        elif self._cancel_preview is not None:
            self._preview_gen += 1
            self._cancel_preview()
            self._cancel_preview = None
            self._preview_btn.setText(t("editor.preview_btn"))
            self._export_status.setText("")
        else:
            self._start_preview()

    def _start_preview(self) -> None:
        if not self._current_source:
            return
        end_ms = self._out_ms if self._out_ms >= 0 else self._original_duration
        preview_dur = min(10000, max(1000, end_ms - self._in_ms))
        temp = Path(tempfile.gettempdir()) / f"omnidl_preview_{uuid4().hex[:8]}.mp4"

        self._preview_btn.setText(t("editor.creating_preview_btn"))
        self._export_status.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        self._export_status.setText(t("editor.creating_preview_status"))

        gen = self._preview_gen

        def _on_done(path: Path) -> None:
            ui_bridge.post(lambda p=path, g=gen: self._on_preview_done(p, g))

        def _on_err(msg: str) -> None:
            ui_bridge.post(lambda m=msg, g=gen: self._on_preview_error(m, g))

        self._cancel_preview = trim_video(
            self._current_source,
            temp,
            self._in_ms,
            self._in_ms + preview_dur,
            **self._collect_params(),
            on_done=_on_done,
            on_error=_on_err,
        )

    def _on_preview_done(self, temp_path: Path, gen: int) -> None:
        if gen != self._preview_gen:
            temp_path.unlink(missing_ok=True)
            return
        self._cancel_preview = None
        self._preview_mode = True
        self._preview_temp = temp_path
        self._saved_effect_params = self._frame_processor.params
        self._frame_processor.update_params(EffectParams())
        self._timeline.setEnabled(False)
        self._set_in_btn.setEnabled(False)
        self._set_out_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._set_effect_controls_enabled(False)
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(temp_path)))
        self._player.play()
        self._preview_btn.setText(t("editor.back_to_original"))
        self._export_status.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        self._export_status.setText(t("editor.previewing_status"))

    def _on_preview_error(self, msg: str, gen: int) -> None:
        if gen != self._preview_gen:
            return
        self._cancel_preview = None
        self._preview_btn.setText(t("editor.preview_btn"))
        self._export_status.setStyleSheet(f"color: {T.error}; font-size: 11px;")
        self._export_status.setText(t("editor.preview_error", msg=msg[:60]))

    def _back_to_original(self) -> None:
        self._preview_mode = False
        old_temp = self._preview_temp
        self._preview_temp = None
        self._frame_processor.update_params(self._saved_effect_params)
        self._timeline.setEnabled(True)
        self._set_in_btn.setEnabled(True)
        self._set_out_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._set_effect_controls_enabled(True)
        self._player.stop()
        if self._current_source:
            self._player.setSource(QUrl.fromLocalFile(str(self._current_source)))
            self._player.play()
        if old_temp is not None:
            old_temp.unlink(missing_ok=True)
        self._preview_btn.setText(t("editor.preview_btn"))
        self._export_status.setText("")

    def _set_all_controls_enabled(self, enabled: bool) -> None:
        for w in (
            self._play_btn,
            self._timeline,
            self._set_in_btn,
            self._set_out_btn,
            self._rotate_combo,
            self._mute_check,
            self._speed_combo,
            self._volume_combo,
            self._export_btn,
            self._preview_btn,
            self._toggle_ctrl_btn,
            self._toggle_effects_btn,
            self._brightness_slider,
            self._contrast_slider,
            self._saturation_slider,
            self._hue_slider,
            self._blur_slider,
            self._fade_in_slider,
            self._fade_out_slider,
            self._text_input,
            self._text_pos_combo,
            self._text_size_spin,
            self._text_color_combo,
            self._text_box_check,
            self._text_shadow_check,
        ):
            w.setEnabled(enabled)

    def _set_effect_controls_enabled(self, enabled: bool) -> None:
        for w in (
            self._brightness_slider,
            self._contrast_slider,
            self._saturation_slider,
            self._hue_slider,
            self._blur_slider,
            self._fade_in_slider,
            self._fade_out_slider,
            self._text_input,
            self._text_pos_combo,
            self._text_size_spin,
            self._text_color_combo,
            self._text_box_check,
            self._text_shadow_check,
        ):
            w.setEnabled(enabled)

    # ── Trim controls ─────────────────────────────────────────────────────────

    def _set_in(self) -> None:
        self._in_ms = self._player.position()
        self._in_lbl.setText(t("editor.in_label", time=_fmt_ms(self._in_ms)))
        self._timeline.set_in(self._in_ms)

    def _set_out(self) -> None:
        self._out_ms = self._player.position()
        self._out_lbl.setText(t("editor.out_label", time=_fmt_ms(self._out_ms)))
        self._timeline.set_out(self._out_ms)

    def _collect_params(self) -> dict:
        return dict(
            rotate=_ROTATE_KEYS[self._rotate_combo.currentIndex()][1],
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

        src_text = self._file_path
        if not src_text:
            return
        source = Path(src_text)
        dur = self._original_duration if self._original_duration > 0 else self._player.duration()
        start_ms = self._in_ms
        end_ms = self._out_ms if self._out_ms >= 0 else dur

        if end_ms <= start_ms:
            self._export_status.setStyleSheet(f"color: {T.error}; font-size: 11px;")
            self._export_status.setText(t("editor.in_after_out_error"))
            return

        output = _unique_output(source)

        self._export_btn.setText(t("editor.export_cancel_btn"))
        self._export_status.setStyleSheet(f"color: {T.text2}; font-size: 11px;")
        self._export_status.setText(t("editor.exporting_status"))

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
        self._export_btn.setText(t("editor.export_btn"))
        self._export_status.setStyleSheet(f"color: {T.success}; font-size: 11px;")
        self._export_status.setText(t("editor.export_saved", name=path.name))

    def _fail_export(self, msg: str) -> None:
        self._cancel_trim = None
        self._export_btn.setText(t("editor.export_btn"))
        self._export_status.setStyleSheet(f"color: {T.error}; font-size: 11px;")
        self._export_status.setText(t("editor.export_error", msg=msg[:80]))

    # ── Playback handlers ─────────────────────────────────────────────────────

    def _pick_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(_VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self, t("editor.choose_video_file"), "", f"Video ({exts});;{t('editor.all_files_filter')} (*)"
        )
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

        QTimer.singleShot(100, self._clear_seeking)

    def _clear_seeking(self) -> None:
        self._seeking = False

    def _on_timeline_in(self, ms: int) -> None:
        self._in_ms = ms
        self._in_lbl.setText(t("editor.in_label", time=_fmt_ms(ms)))

    def _on_timeline_out(self, ms: int) -> None:
        self._out_ms = ms
        self._out_lbl.setText(t("editor.out_label", time=_fmt_ms(ms)))

    def _on_position_changed(self, pos: int) -> None:
        self._time_lbl.setText(f"{_fmt_ms(pos)} / {_fmt_ms(self._player.duration())}")
        if not self._seeking:
            self._timeline.set_position(pos)

    def _on_duration_changed(self, dur: int) -> None:
        if not self._preview_mode:
            self._timeline.set_duration(dur)
            self._original_duration = dur
            if self._out_ms < 0:
                self._out_lbl.setText(t("editor.out_label", time=_fmt_ms(dur)))
                self._timeline.set_out(-1)
        self._time_lbl.setText(f"{_fmt_ms(self._player.position())} / {_fmt_ms(dur)}")

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setText("⏸")
        else:
            self._play_btn.setText("▶")

    def _on_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            logger.error("QMediaPlayer error: %s - %s", error, error_string)
            self._info_lbl.setText(t("editor.export_error", msg=error_string))

    def _on_video_frame(self, frame: "QVideoFrame") -> None:
        pixmap = self._frame_processor.process(frame, self._preview_label.size())
        if pixmap is not None:
            self._preview_label.set_frame(pixmap)

    def _update_effect_params(self) -> None:
        if self._preview_mode:
            return
        params = EffectParams(
            brightness=self._brightness_slider.value() / 100.0,
            contrast=self._contrast_slider.value() / 100.0,
            saturation=self._saturation_slider.value() / 100.0,
            hue=float(self._hue_slider.value()),
            blur=self._blur_slider.value() / 10.0,
            text=self._text_input.text().strip(),
            text_pos=self._text_pos_combo.currentIndex(),
            text_size=self._text_size_spin.value(),
            text_color=_TEXT_COLOR_VALUES[self._text_color_combo.currentIndex()],
            text_box=self._text_box_check.isChecked(),
            text_shadow=self._text_shadow_check.isChecked(),
        )
        self._frame_processor.update_params(params)
        pixmap = self._frame_processor.rerender(self._preview_label.size())
        if pixmap is not None:
            self._preview_label.set_frame(pixmap)

    # ── i18n ─────────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._empty_lbl.setText(t("editor.empty"))
        self._toggle_ctrl_btn.setText(
            t("editor.show_panel") if not self._right_scroll.isVisible() else t("editor.hide_panel")
        )
        self._open_btn.setText(t("editor.open_file"))
        self._clear_btn.setText(t("editor.close_file"))
        self._clear_btn.setToolTip(t("editor.close_file_tip"))
        self._set_in_btn.setText(t("editor.set_in"))
        self._set_out_btn.setText(t("editor.set_out"))
        self._in_lbl.setText(t("editor.in_label", time=_fmt_ms(self._in_ms)))
        self._out_lbl.setText(
            t("editor.out_label", time=_fmt_ms(self._out_ms))
            if self._out_ms >= 0
            else t("editor.out_placeholder")
        )
        self._rotate_lbl.setText(t("editor.rotate_label"))
        rotate_idx = self._rotate_combo.currentIndex()
        self._rotate_combo.clear()
        self._rotate_combo.addItems([t(key) for key, _ in _ROTATE_KEYS])
        self._rotate_combo.setCurrentIndex(rotate_idx)
        self._mute_check.setText(t("editor.mute"))

        if self._cancel_trim is None:
            self._export_btn.setText(t("editor.export_btn"))
        if self._preview_mode:
            self._preview_btn.setText(t("editor.back_to_original"))
        elif self._cancel_preview is None:
            self._preview_btn.setText(t("editor.preview_btn"))

        self._speed_lbl.setText(t("editor.speed_label"))
        self._vol_lbl.setText(t("editor.volume_label"))
        self._text_lbl.setText(t("editor.text_label"))
        self._text_input.setPlaceholderText(t("editor.text_placeholder"))
        self._pos_lbl.setText(t("editor.position_label"))
        pos_idx = self._text_pos_combo.currentIndex()
        self._text_pos_combo.blockSignals(True)
        self._text_pos_combo.clear()
        self._text_pos_combo.addItems([t("editor.pos.top"), t("editor.pos.middle"), t("editor.pos.bottom")])
        self._text_pos_combo.setCurrentIndex(pos_idx)
        self._text_pos_combo.blockSignals(False)
        self._size_lbl.setText(t("editor.size_label"))
        self._color_lbl.setText(t("editor.color_label"))
        color_idx = self._text_color_combo.currentIndex()
        self._text_color_combo.blockSignals(True)
        self._text_color_combo.clear()
        self._text_color_combo.addItems([t(key) for key in _TEXT_COLOR_KEYS])
        self._text_color_combo.setCurrentIndex(color_idx)
        self._text_color_combo.blockSignals(False)
        self._text_box_check.setText(t("editor.box"))
        self._text_shadow_check.setText(t("editor.shadow"))

        self._eff_lbl.setText(t("editor.effects_title"))
        self._toggle_effects_btn.setText(
            t("editor.hide") if self._effects_panel.isVisible() else t("editor.show")
        )
        self._brightness_lbl.setText(t("editor.brightness"))
        self._contrast_lbl.setText(t("editor.contrast"))
        self._saturation_lbl.setText(t("editor.saturation"))
        self._hue_lbl.setText(t("editor.hue"))
        self._blur_lbl.setText(t("editor.blur"))
        self._fade_in_lbl.setText(t("editor.fade_in"))
        self._fade_out_lbl.setText(t("editor.fade_out"))
