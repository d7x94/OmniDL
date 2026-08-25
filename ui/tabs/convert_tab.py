"""Convert tab — convert local video files to iPhone-compatible MP4."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
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
    ConvertResult,
    EncodeSettings,
    FfmpegMediaInfo,
    get_available_codec_options,
    get_available_encoder_options,
    probe_media_info,
    scan_folder_for_media,
)
from app.services.whisper_subtitle_service import (
    DEFAULT_MODEL_KEY,
    SUBTITLE_LANGUAGE_OPTIONS,
    WHISPER_MODELS,
    is_model_installed,
    is_whisper_supported,
    language_label,
    model_label,
)
from ui.components.progress_bar import OmniProgressBar
from ui.signals import ui_bridge
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, fmt_duration, open_file, open_folder, reveal_in_explorer
from utils.i18n import t

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_QUALITY_KEYS = [
    ("high", "convert.quality.high.label", "convert.quality.high.desc"),
    ("standard", "convert.quality.standard.label", "convert.quality.standard.desc"),
    ("small", "convert.quality.small.label", "convert.quality.small.desc"),
    ("custom", "convert.quality.custom.label", "convert.quality.custom.desc"),
]

_MAX_CONCURRENT = 2


class FileState(Enum):
    PENDING = auto()
    QUEUED = auto()
    CONVERTING = auto()
    DONE = auto()
    FAILED = auto()


_STATE_BADGE_KEY: dict[FileState, tuple[str, str, str]] = {
    FileState.PENDING: ("convert.state.pending", "text3", "surface3"),
    FileState.QUEUED: ("convert.state.queued", "text2", "surface2"),
    FileState.CONVERTING: ("convert.state.converting", "warning", "warning_bg"),
    FileState.DONE: ("convert.state.done", "success", "success_bg"),
    FileState.FAILED: ("convert.state.failed", "error", "error_bg"),
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
    output_media_info: Optional[FfmpegMediaInfo] = field(default=None)
    subtitle_path: Optional[Path] = None
    # Why the subtitle pass produced nothing.  The video itself still converted,
    # so this is shown as a note next to a successful job, not as a failure.
    subtitle_error: str = ""
    # Mean VMAF score (0-100) of the output against the source, when requested.
    vmaf_score: Optional[float] = None
    cancel_fn: Optional[Callable[[], None]] = field(default=None, repr=False)


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

        s_key, s_txt, s_bg = _STATE_BADGE_KEY[self.job.state]
        self._state_badge = QLabel(f"  {t(s_key)}  ")
        self._state_badge.setStyleSheet(
            f"color: {getattr(T, s_txt)}; background-color: {getattr(T, s_bg)}; border-radius: 5px; font-size: 10px; font-weight: bold; padding: 2px 4px;"
        )
        top_layout.addWidget(self._state_badge)

        # Buttons in order
        self._cancel_btn = QPushButton(t("archive.cancel"))
        self._cancel_btn.setFixedSize(60, 26)
        self._cancel_btn.setStyleSheet(
            f"background: {T.warning_bg}; color: {T.warning}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold; padding: 0;"
        )
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(lambda: self._on_cancel(self.job.id))
        self._cancel_btn.hide()
        top_layout.addWidget(self._cancel_btn)

        self._open_btn = QPushButton(t("live.open"))
        self._open_btn.setFixedSize(52, 26)
        self._open_btn.setStyleSheet(
            f"background: {T.success_bg}; color: {T.success_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold; padding: 0;"
        )
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.clicked.connect(lambda: self._on_open_folder(self.job.id))
        self._open_btn.hide()
        top_layout.addWidget(self._open_btn)

        self._preview_btn = QPushButton(t("special.view"))
        self._preview_btn.setFixedSize(52, 26)
        self._preview_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold; padding: 0;"
        )
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.clicked.connect(self._toggle_preview)
        self._preview_btn.hide()
        top_layout.addWidget(self._preview_btn)

        self._delete_output_btn = QPushButton(t("convert.card.delete_output"))
        self._delete_output_btn.setFixedSize(70, 26)
        self._delete_output_btn.setStyleSheet(
            f"background: {T.error_bg}; color: {T.error_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold; padding: 0;"
        )
        self._delete_output_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_output_btn.clicked.connect(lambda: self._on_delete_output(self.job.id))
        self._delete_output_btn.hide()
        top_layout.addWidget(self._delete_output_btn)

        self._remove_btn = QPushButton("x")
        self._remove_btn.setFixedSize(28, 26)
        self._remove_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 6px;"
            f" padding: 0; font-size: 13px; font-weight: bold;"
            f' font-family: "Segoe UI Symbol", "Segoe UI Emoji", "Segoe UI", sans-serif;'
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

        # Preview panel (before/after comparison)
        self._preview_panel = QFrame()
        self._preview_panel.setObjectName("preview_panel")
        self._preview_panel.setStyleSheet(
            f"#preview_panel {{ background: {T.surface3}; border-radius: 8px;"
            f" border: 1px solid {T.border}; }}"
            f" QLabel {{ border: none; background: transparent; }}"
        )
        prev_layout = QVBoxLayout(self._preview_panel)
        prev_layout.setContentsMargins(0, 2, 0, 2)
        prev_layout.setSpacing(0)

        self._prev_before_row = _ClickableFrame(
            self._preview_panel,
            on_click=lambda: open_file(self.job.source),
        )
        self._prev_before_row.setObjectName("prev_before_row")
        self._prev_before_row.setStyleSheet(
            f"#prev_before_row {{ background: transparent; border-radius: 6px; }}"
            f" #prev_before_row:hover {{ background: {T.surface2}; }}"
        )
        before_row_l = QHBoxLayout(self._prev_before_row)
        before_row_l.setContentsMargins(12, 6, 12, 6)
        before_row_l.setSpacing(8)
        self._before_pfx_lbl = QLabel(t("convert.card.before"))
        self._before_pfx_lbl.setFixedWidth(42)
        self._before_pfx_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; font-weight: bold;")
        self._prev_before_lbl = QLabel("…")
        self._prev_before_lbl.setStyleSheet(f"color: {T.text2}; font-size: 10px;")
        before_row_l.addWidget(self._before_pfx_lbl)
        before_row_l.addWidget(self._prev_before_lbl)

        self._prev_after_row = _ClickableFrame(
            self._preview_panel,
            on_click=lambda: open_file(self.job.output) if self.job.output else None,
        )
        self._prev_after_row.setObjectName("prev_after_row")
        self._prev_after_row.setStyleSheet(
            f"#prev_after_row {{ background: transparent; border-radius: 6px; }}"
            f" #prev_after_row:hover {{ background: {T.surface2}; }}"
        )
        after_row_l = QHBoxLayout(self._prev_after_row)
        after_row_l.setContentsMargins(12, 6, 12, 6)
        after_row_l.setSpacing(8)
        self._after_pfx_lbl = QLabel(t("convert.card.after"))
        self._after_pfx_lbl.setFixedWidth(42)
        self._after_pfx_lbl.setStyleSheet(f"color: {T.success_text}; font-size: 10px; font-weight: bold;")
        self._prev_after_lbl = QLabel(t("convert.card.reading"))
        self._prev_after_lbl.setStyleSheet(f"color: {T.text2}; font-size: 10px;")
        after_row_l.addWidget(self._after_pfx_lbl)
        after_row_l.addWidget(self._prev_after_lbl)

        prev_layout.addWidget(self._prev_before_row)
        prev_layout.addWidget(self._prev_after_row)
        self._preview_panel.hide()
        layout.addWidget(self._preview_panel)

        if self.job.media_info is not None:
            self.update_info(self.job.media_info)

    def refresh(self) -> None:
        job = self.job
        s_key, s_txt, s_bg = _STATE_BADGE_KEY[job.state]
        self._state_badge.setText(f"  {t(s_key)}  ")
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
            # The output can vanish between conversions: the user deletes it
            # with the button below, or removes it outside OmniDL.  Keeping the
            # "open it / preview it" buttons alive for a file that is gone
            # opened an empty folder and left the card claiming a size of "".
            on_disk = job.output.is_file()
            if on_disk:
                text = f"→ {job.output.name}  {fmt_bytes(job.output.stat().st_size)}"
                if job.vmaf_score is not None:
                    text += f"  ·  VMAF {job.vmaf_score:.1f}"
            else:
                text = t("convert.card.output_gone", name=job.output.name)
            self._out_lbl.setText(text)
            self._open_btn.setVisible(on_disk)
            # A subtitles-only job's output is the .srt itself — there is no
            # before/after media info to compare, so the panel would sit on
            # "Đang đọc…" forever.
            self._preview_btn.setVisible(on_disk and job.output.suffix.lower() != ".srt")
            self._delete_output_btn.setVisible(on_disk)
            if not on_disk:
                self._preview_panel.hide()
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

    def _toggle_preview(self) -> None:
        if self._preview_panel.isVisible():
            self._preview_panel.hide()
        else:
            self._refresh_preview_content()
            self._preview_panel.show()

    def _refresh_preview_content(self) -> None:
        self._prev_before_lbl.setText(self._media_info_str(self.job.media_info, self.job.source))
        if self.job.output_media_info is not None:
            self._prev_after_lbl.setText(self._media_info_str(self.job.output_media_info, self.job.output))
        else:
            self._prev_after_lbl.setText(t("convert.card.reading"))

    def update_output_info(self) -> None:
        if self._preview_panel.isVisible():
            self._refresh_preview_content()

    @staticmethod
    def _media_info_str(info: Optional[FfmpegMediaInfo], path: Optional[Path] = None) -> str:
        if info is None:
            return "—"
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
            parts.append(f"{info.bitrate_bps / 1_000_000:.1f} Mbps")
        if path and path.is_file():
            parts.append(fmt_bytes(path.stat().st_size))
        return "  ·  ".join(parts) if parts else "—"

    def _initial_info_text(self) -> str:
        return "" if self.job.media_info is not None else t("convert.card.reading_info")

    def _file_size_str(self) -> str:
        try:
            return fmt_bytes(self.job.source.stat().st_size)
        except Exception:
            return ""

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")

    def retranslate(self) -> None:
        self._cancel_btn.setText(t("archive.cancel"))
        self._open_btn.setText(t("live.open"))
        self._preview_btn.setText(t("special.view"))
        self._delete_output_btn.setText(t("convert.card.delete_output"))
        self._before_pfx_lbl.setText(t("convert.card.before"))
        self._after_pfx_lbl.setText(t("convert.card.after"))
        if self.job.media_info is None:
            self._info_lbl.setText(t("convert.card.reading_info"))
        if self._preview_panel.isVisible():
            self._refresh_preview_content()
        self.refresh()


# ── Convert Tab ───────────────────────────────────────────────────────────────


class ConvertTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._jobs: dict[str, FileJob] = {}
        self._cards: dict[str, FileCard] = {}
        self._quality = "standard"
        self._cfg_collapsed = False
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
        # Codecs the installed FFmpeg can actually encode.  Starts permissive so
        # the cards render immediately; the async probe narrows it a moment later.
        self._available_codecs: set[str] = {key for key, _ in CODEC_OPTIONS}
        self._gen_subtitles = False
        self._subtitle_language = "auto"
        self._subtitle_model = DEFAULT_MODEL_KEY
        # Set by the async probe; gates the standalone "Tạo phụ đề" button.
        self._whisper_ok = False
        self._compute_vmaf = False

        threading.Thread(
            target=self._detect_capabilities_async,
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

        _bus = self._app.taildrop.bus
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
        hdr_layout.setContentsMargins(28, 16, 28, 0)
        hdr_layout.setSpacing(6)

        self._add_btn = QPushButton(t("archive.add_file"))
        self._add_btn.setFixedSize(100, 30)
        self._add_btn.setStyleSheet(
            f"background: {T.primary}; color: white; border: none; border-radius: 7px; font-size: 11px; font-weight: bold;"
        )
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._browse_files)
        hdr_layout.addWidget(self._add_btn)

        self._folder_btn = QPushButton(t("archive.add_folder"))
        self._folder_btn.setFixedSize(120, 30)
        self._folder_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 7px; font-size: 11px; font-weight: bold;"
        )
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.clicked.connect(self._browse_folder)
        hdr_layout.addWidget(self._folder_btn)

        self._clear_btn = QPushButton(t("convert.clear_done"))
        self._clear_btn.setFixedSize(110, 30)
        self._clear_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 7px; font-size: 11px;"
        )
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.clicked.connect(self._clear_done)
        hdr_layout.addWidget(self._clear_btn)

        hdr_layout.addStretch()

        self._cfg_toggle_btn = QPushButton(t("convert.settings_toggle_up"))
        self._cfg_toggle_btn.setFixedSize(130, 30)
        self._cfg_toggle_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 7px; font-size: 11px;"
        )
        self._cfg_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cfg_toggle_btn.clicked.connect(self._toggle_cfg_panel)
        hdr_layout.addWidget(self._cfg_toggle_btn)

        layout.addWidget(hdr)

        # Config panel
        cfg_wrap = QWidget()
        cfg_wrap.setStyleSheet("background: transparent;")
        cfg_wrap_layout = QVBoxLayout(cfg_wrap)
        cfg_wrap_layout.setContentsMargins(28, 16, 28, 0)

        self._cfg_panel = QFrame()
        cfg = self._cfg_panel
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

        self._quality_row_lbl = QLabel(t("convert.quality_label"))
        self._quality_row_lbl.setFixedWidth(90)
        self._quality_row_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        q_layout.addWidget(self._quality_row_lbl)

        self._quality_desc_labels: dict[str, Optional[QLabel]] = {}
        for key, label_key, desc_key in _QUALITY_KEYS:
            card = self._make_option_card(
                t(label_key),
                t(desc_key),
                selected=(key == self._quality),
                on_click=lambda k=key: self._on_quality_change(k),
            )
            q_layout.addWidget(card, 1)
            self._quality_cards[key] = card
            self._quality_main_labels[key] = card.findChild(QLabel, "main_lbl")
            self._quality_desc_labels[key] = card.findChild(QLabel, "desc_lbl")

        cfg_layout.addWidget(q_row)

        # Custom quality row
        self._custom_row = QWidget()
        self._custom_row.setStyleSheet("background: transparent;")
        cr_layout = QHBoxLayout(self._custom_row)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.setSpacing(6)

        spacer_lbl = QLabel("")
        spacer_lbl.setFixedWidth(90)
        cr_layout.addWidget(spacer_lbl)

        self._cq_lbl = QLabel(t("convert.custom_value_label"))
        self._cq_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        cr_layout.addWidget(self._cq_lbl)

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

        self._enc_lbl = QLabel(t("convert.encoder_label"))
        self._enc_lbl.setFixedWidth(90)
        self._enc_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        er_layout.addWidget(self._enc_lbl)

        self._encoder_combo = QComboBox()
        self._encoder_combo.addItems([lbl for _, lbl in self._available_encoder_options])
        self._encoder_combo.setFixedSize(200, 32)
        self._encoder_combo.currentTextChanged.connect(self._on_encoder_change)
        er_layout.addWidget(self._encoder_combo)

        self._encoder_status_lbl = QLabel(t("live.state.checking"))
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

        self._spd_lbl = QLabel(t("convert.speed_label"))
        self._spd_lbl.setFixedWidth(90)
        self._spd_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        sr_layout.addWidget(self._spd_lbl)

        for s_key, s_label_key in SPEED_OPTIONS:
            s_card = self._make_option_card(
                t(s_label_key),
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

        self._cod_lbl = QLabel(t("convert.codec_label"))
        self._cod_lbl.setFixedWidth(90)
        self._cod_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        co_layout.addWidget(self._cod_lbl)

        for c_key, c_label_key in CODEC_OPTIONS:
            c_card = self._make_option_card(
                t(c_label_key),
                "",
                selected=(c_key == self._output_codec),
                on_click=lambda k=c_key: self._on_codec_change(k),
            )
            co_layout.addWidget(c_card)
            self._codec_cards[c_key] = c_card
            self._codec_main_labels[c_key] = c_card.findChild(QLabel, "main_lbl")

        co_layout.addStretch()
        cfg_layout.addWidget(cod_row)

        # Subtitle row
        sub_row = QWidget()
        sub_row.setStyleSheet("background: transparent;")
        sb_layout = QHBoxLayout(sub_row)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(6)

        self._sub_lbl = QLabel(t("convert.subtitle_label"))
        self._sub_lbl.setFixedWidth(90)
        self._sub_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        sb_layout.addWidget(self._sub_lbl)

        self._subs_check = QCheckBox(t("convert.gen_subs_check"))
        self._subs_check.setStyleSheet(f"color: {T.text2}; font-size: 12px;")
        # Disabled until the async probe confirms this FFmpeg build has whisper.
        self._subs_check.setEnabled(False)
        self._subs_check.toggled.connect(self._on_subs_toggled)
        sb_layout.addWidget(self._subs_check)

        self._subs_lang_combo = QComboBox()
        self._subs_lang_combo.addItems([language_label(c, lbl) for c, lbl in SUBTITLE_LANGUAGE_OPTIONS])
        self._subs_lang_combo.setFixedWidth(170)
        self._subs_lang_combo.setEnabled(False)
        self._subs_lang_combo.currentIndexChanged.connect(self._on_subs_lang_change)
        sb_layout.addWidget(self._subs_lang_combo)

        self._subs_model_combo = QComboBox()
        self._subs_model_combo.addItems([model_label(m) for m in WHISPER_MODELS])
        self._subs_model_combo.setCurrentIndex(
            next((i for i, m in enumerate(WHISPER_MODELS) if m.key == DEFAULT_MODEL_KEY), 0)
        )
        self._subs_model_combo.setFixedWidth(190)
        self._subs_model_combo.setEnabled(False)
        self._subs_model_combo.currentIndexChanged.connect(self._on_subs_model_change)
        sb_layout.addWidget(self._subs_model_combo)

        sb_layout.addStretch()
        cfg_layout.addWidget(sub_row)

        # VMAF row
        vmaf_row = QWidget()
        vmaf_row.setStyleSheet("background: transparent;")
        vm_layout = QHBoxLayout(vmaf_row)
        vm_layout.setContentsMargins(0, 0, 0, 0)
        vm_layout.setSpacing(6)

        self._vmaf_lbl = QLabel(t("convert.rating_label"))
        self._vmaf_lbl.setFixedWidth(90)
        self._vmaf_lbl.setStyleSheet(f"color: {T.text2}; font-size: 12px; font-weight: bold;")
        vm_layout.addWidget(self._vmaf_lbl)

        self._vmaf_check = QCheckBox(t("convert.vmaf_check"))
        self._vmaf_check.setStyleSheet(f"color: {T.text2}; font-size: 12px;")
        self._vmaf_check.setToolTip(t("convert.vmaf_tip"))
        self._vmaf_check.toggled.connect(self._on_vmaf_toggled)
        vm_layout.addWidget(self._vmaf_check)

        vm_layout.addStretch()
        cfg_layout.addWidget(vmaf_row)

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

        self._empty_title_lbl = QLabel(t("convert.empty_title"))
        self._empty_title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title_lbl.setStyleSheet(f"color: {T.text3}; font-size: 16px; font-weight: bold;")
        empty_layout.addWidget(self._empty_title_lbl)
        self._empty_hint_lbl = QLabel(t("convert.empty_hint"))
        self._empty_hint_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint_lbl.setStyleSheet(f"color: {T.text3}; font-size: 12px;")
        empty_layout.addWidget(self._empty_hint_lbl)
        self._empty_formats_lbl = QLabel(t("convert.empty_formats"))
        self._empty_formats_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_formats_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px;")
        empty_layout.addWidget(self._empty_formats_lbl)
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

        self._subs_btn = QPushButton(t("convert.generate_subs_btn"))
        self._subs_btn.setFixedSize(130, 40)
        self._subs_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text}; border: 1px solid {T.border}; "
            "border-radius: 8px; font-size: 13px; font-weight: bold;"
        )
        self._subs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._subs_btn.setToolTip(t("convert.generate_subs_tip"))
        # Enabled by the async whisper probe (_apply_whisper_support).
        self._subs_btn.setEnabled(False)
        self._subs_btn.clicked.connect(self._start_subtitles_only)
        bar_layout.addWidget(self._subs_btn)

        self._convert_btn = QPushButton(t("convert.convert_all_btn"))
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
            desc_lbl.setObjectName("desc_lbl")
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

    def _detect_capabilities_async(self) -> None:
        """Probe encoders, output codecs and whisper support off the UI thread."""
        available_opts = get_available_encoder_options()
        codec_opts = get_available_codec_options()
        whisper_ok = is_whisper_supported()
        ui_bridge.post(lambda opts=available_opts: self._apply_available_encoders(opts))
        ui_bridge.post(lambda c=codec_opts: self._apply_available_codecs(c))
        ui_bridge.post(lambda ok=whisper_ok: self._apply_whisper_support(ok))

    def _apply_available_codecs(self, codec_opts: list[tuple[str, str]]) -> None:
        """Hide codec cards this FFmpeg build cannot produce.

        The shipped Windows binary has no SVT-AV1, so selecting AV1 used to
        queue a conversion that always failed.  Hiding the card removes the
        failure instead of reporting it.
        """
        self._available_codecs = {key for key, _ in codec_opts} or {"h264"}
        for key, card in self._codec_cards.items():
            card.setVisible(key in self._available_codecs)
        if self._output_codec not in self._available_codecs:
            self._on_codec_change("h264")

    def _apply_whisper_support(self, supported: bool) -> None:
        """Enable the subtitle controls only when the FFmpeg build supports them."""
        self._subs_check.setEnabled(supported)
        # The language/model combos also drive the standalone "Tạo phụ đề"
        # button, so they follow whisper support alone, not the checkbox.
        self._subs_lang_combo.setEnabled(supported)
        self._subs_model_combo.setEnabled(supported)
        self._whisper_ok = supported
        self._subs_btn.setEnabled(supported)
        if not supported:
            self._subs_btn.setToolTip(t("convert.no_whisper_tip"))
            self._subs_check.setChecked(False)
            self._gen_subtitles = False
            self._subs_check.setToolTip(t("convert.no_whisper_tip"))
        elif not is_model_installed(DEFAULT_MODEL_KEY):
            self._subs_check.setToolTip(t("convert.first_enable_tip"))

    def _on_subs_toggled(self, checked: bool) -> None:
        self._gen_subtitles = bool(checked)

    def _on_subs_lang_change(self, index: int) -> None:
        if 0 <= index < len(SUBTITLE_LANGUAGE_OPTIONS):
            self._subtitle_language = SUBTITLE_LANGUAGE_OPTIONS[index][0]

    def _on_subs_model_change(self, index: int) -> None:
        if 0 <= index < len(WHISPER_MODELS):
            self._subtitle_model = WHISPER_MODELS[index].key

    def _on_vmaf_toggled(self, checked: bool) -> None:
        self._compute_vmaf = bool(checked)

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
        status = (
            t("convert.gpu_status", labels=", ".join(gpu_labels))
            if gpu_labels
            else t("convert.cpu_only_status")
        )
        self._encoder_status_lbl.setText(status)

    # ── File operations ───────────────────────────────────────────────────────

    def _browse_files(self) -> None:
        ext_filter = " ".join(f"*.{ext}" for ext in sorted(SUPPORTED_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("convert.choose_videos_title"),
            "",
            f"Video files ({ext_filter});;All files (*.*)",
        )
        self._add_files([Path(p) for p in paths])
        self._refresh_ui()

    def _browse_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, t("convert.choose_folder_title"))
        if not d:
            return
        self._status_lbl.setText(t("convert.scanning_folder"))
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
        self._add_files(files)
        self._refresh_ui()
        if not files:
            self._status_lbl.setText(t("convert.no_video_in_folder", folder=folder.name))
        else:
            self._status_lbl.setText(t("convert.added_from_folder", count=len(files), folder=folder.name))
            self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;")

    def _toggle_cfg_panel(self) -> None:
        self._cfg_collapsed = not self._cfg_collapsed
        self._cfg_panel.setVisible(not self._cfg_collapsed)
        self._cfg_toggle_btn.setText(
            t("convert.settings_toggle_down") if self._cfg_collapsed else t("convert.settings_toggle_up")
        )

    def load_file(self, path: str) -> None:
        self._add_file(Path(path))
        self._refresh_ui()

    def _add_file(self, path: Path) -> None:
        self._add_files([path])

    def _add_files(self, paths: list[Path]) -> None:
        existing = {j.source.resolve() for j in self._jobs.values()}
        for path in paths:
            resolved = path.resolve()
            if resolved in existing:
                continue
            if path.suffix.lower().lstrip(".") not in SUPPORTED_EXTS:
                continue
            existing.add(resolved)
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

        encoder_key = self._encoder_key
        if encoder_key not in self._available_encoders:
            encoder_key = "cpu"

        self._validate_custom_quality()
        output_codec = self._output_codec
        if output_codec not in self._available_codecs:
            output_codec = "h264"
        encode_settings = EncodeSettings(
            encoder_key=encoder_key,
            quality=self._quality,
            speed_preset=self._speed_preset,
            custom_quality=self._custom_quality_val,
            output_codec=output_codec,
            generate_subtitles=self._gen_subtitles,
            subtitle_language=self._subtitle_language,
            subtitle_model=self._subtitle_model,
            compute_vmaf=self._compute_vmaf,
        )

        self._convert_btn.setEnabled(False)
        self._subs_btn.setEnabled(False)

        for job in pending:
            job.state = FileState.QUEUED
            job.progress = 0.0
            self._active_count += 1
            self._rebuild_card(job)

        for job in pending:
            self._submit_job(job, self._quality, encode_settings)

        self._refresh_ui()

    def _start_subtitles_only(self) -> None:
        """Transcribe every pending file to a .srt — no video re-encode."""
        pending = [j for j in self._jobs.values() if j.state == FileState.PENDING]
        if not pending:
            return

        encode_settings = EncodeSettings(
            generate_subtitles=True,
            subtitle_language=self._subtitle_language,
            subtitle_model=self._subtitle_model,
            subtitles_only=True,
        )

        self._convert_btn.setEnabled(False)
        self._subs_btn.setEnabled(False)

        for job in pending:
            job.state = FileState.QUEUED
            job.progress = 0.0
            self._active_count += 1
            self._rebuild_card(job)

        for job in pending:
            self._submit_job(job, self._quality, encode_settings)

        self._refresh_ui()

    def _submit_job(
        self,
        job: FileJob,
        quality: str,
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

        def on_result(result: ConvertResult) -> None:
            job.subtitle_path = result.subtitle_path
            job.subtitle_error = result.subtitle_error
            job.vmaf_score = result.vmaf_score

        job.cancel_fn = self._queue.submit(
            source=job.source,
            quality=quality,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            on_start=on_start,
            encode_settings=encode_settings,
            on_result=on_result,
        )

    # ── Card management ───────────────────────────────────────────────────────

    def _rebuild_card(self, job: FileJob) -> None:
        old = self._cards.pop(job.id, None)
        idx = self._items_layout.count()
        if old:
            old_idx = self._items_layout.indexOf(old)
            if old_idx != -1:
                idx = old_idx
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
        self._items_layout.insertWidget(idx, card)
        self._cards[job.id] = card
        card.refresh()

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
            self._subs_btn.setEnabled(self._whisper_ok)
        if job.subtitle_error:
            self._status_lbl.setText(f"{job.source.name}: {job.subtitle_error}")
            self._status_lbl.setStyleSheet(f"color: {T.warning}; font-size: 11px;")
        elif job.subtitle_path is not None:
            self._status_lbl.setText(t("convert.subtitle_created", name=job.subtitle_path.name))
            self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;")
        # A subtitles-only job's "output" is the .srt itself — ffprobe has
        # nothing to report on it.
        if job.output and job.output.is_file() and job.output.suffix.lower() != ".srt":
            threading.Thread(
                target=self._probe_output_async,
                args=(job,),
                daemon=True,
                name=f"omnidl-probe-out-{job.id}",
            ).start()

    def _probe_output_async(self, job: FileJob) -> None:
        job.output_media_info = probe_media_info(job.output)
        ui_bridge.post(lambda j=job: self._update_card_output_info(j))

    def _update_card_output_info(self, job: FileJob) -> None:
        card = self._cards.get(job.id)
        if card:
            card.update_output_info()

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
                parts.append(t("convert.status.processing", count=converting))
            if queued:
                parts.append(t("convert.status.waiting_count", count=queued))
            self._status_lbl.setText("  ·  ".join(parts) + t("convert.status.total_files", total=total))
            self._status_lbl.setStyleSheet(f"color: {T.warning}; font-size: 11px;")
        elif done == total:
            self._status_lbl.setText(t("convert.status.complete", done=done, total=total))
            self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;")
        else:
            parts2 = []
            if done:
                parts2.append(t("convert.status.done_count", count=done))
            if failed:
                parts2.append(t("convert.status.failed_count", count=failed))
            pending = total - done - failed
            if pending:
                parts2.append(t("convert.status.waiting_count", count=pending))
            self._status_lbl.setText("  ·  ".join(parts2))
            self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")

    def _remove_job(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job and job.state in (FileState.QUEUED, FileState.CONVERTING) and job.cancel_fn is not None:
            job.cancel_fn()
        self._refresh_ui()

    def _cancel_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job and job.cancel_fn is not None:
            job.cancel_fn()
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
            t("convert.delete_output_title"),
            t("convert.delete_output_msg", name=job.output.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            job.output.unlink()
        except OSError as exc:
            QMessageBox.critical(
                self, t("convert.delete_error_title"), t("convert.delete_error_msg", err=exc)
            )
            return

        card = self._cards.get(job_id)
        if card:
            card.refresh()

    # ── Taildrop event handlers ───────────────────────────────────────────────

    def _on_convert_taildrop_completed(self, *, out_path: Path, dest_node: str, **_kw) -> None:
        msg = t("convert.taildrop_sent", name=out_path.name, node=dest_node)
        ui_bridge.post(
            lambda m=msg: (
                self._status_lbl.setText(m),
                self._status_lbl.setStyleSheet(f"color: {T.success}; font-size: 11px;"),
            )
        )

    def _on_convert_taildrop_failed(self, *, out_path: Path, dest_node: str, error: str = "", **_kw) -> None:
        msg = t("convert.taildrop_failed", name=out_path.name, err=error)
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

    # ── i18n ─────────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._add_btn.setText(t("archive.add_file"))
        self._folder_btn.setText(t("archive.add_folder"))
        self._clear_btn.setText(t("convert.clear_done"))
        self._cfg_toggle_btn.setText(
            t("convert.settings_toggle_down") if self._cfg_collapsed else t("convert.settings_toggle_up")
        )

        self._quality_row_lbl.setText(t("convert.quality_label"))
        for key, label_key, desc_key in _QUALITY_KEYS:
            lbl = self._quality_main_labels.get(key)
            if lbl:
                lbl.setText(t(label_key))
            desc_lbl = self._quality_desc_labels.get(key)
            if desc_lbl:
                desc_lbl.setText(t(desc_key))
        self._cq_lbl.setText(t("convert.custom_value_label"))

        self._enc_lbl.setText(t("convert.encoder_label"))
        gpu_labels = [lbl for k, lbl in self._available_encoder_options if k != "cpu"]
        self._encoder_status_lbl.setText(
            t("convert.gpu_status", labels=", ".join(gpu_labels))
            if gpu_labels
            else t("convert.cpu_only_status")
        )

        self._spd_lbl.setText(t("convert.speed_label"))
        for s_key, s_label_key in SPEED_OPTIONS:
            lbl = self._speed_main_labels.get(s_key)
            if lbl:
                lbl.setText(t(s_label_key))

        self._cod_lbl.setText(t("convert.codec_label"))
        for c_key, c_label_key in CODEC_OPTIONS:
            lbl = self._codec_main_labels.get(c_key)
            if lbl:
                lbl.setText(t(c_label_key))

        # The two combos are filled once at build time — refill them in place
        # so the "Auto-detect" entry and the whisper model names follow too.
        for combo, labels in (
            (self._subs_lang_combo, [language_label(c, lbl) for c, lbl in SUBTITLE_LANGUAGE_OPTIONS]),
            (self._subs_model_combo, [model_label(m) for m in WHISPER_MODELS]),
        ):
            idx = combo.currentIndex()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(labels)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

        self._sub_lbl.setText(t("convert.subtitle_label"))
        self._subs_check.setText(t("convert.gen_subs_check"))
        self._vmaf_lbl.setText(t("convert.rating_label"))
        self._vmaf_check.setText(t("convert.vmaf_check"))
        self._vmaf_check.setToolTip(t("convert.vmaf_tip"))

        self._subs_btn.setText(t("convert.generate_subs_btn"))
        if not self._whisper_ok:
            self._subs_btn.setToolTip(t("convert.no_whisper_tip"))
            self._subs_check.setToolTip(t("convert.no_whisper_tip"))
        else:
            self._subs_btn.setToolTip(t("convert.generate_subs_tip"))
            if not is_model_installed(DEFAULT_MODEL_KEY):
                self._subs_check.setToolTip(t("convert.first_enable_tip"))

        self._empty_title_lbl.setText(t("convert.empty_title"))
        self._empty_hint_lbl.setText(t("convert.empty_hint"))
        self._empty_formats_lbl.setText(t("convert.empty_formats"))

        self._convert_btn.setText(t("convert.convert_all_btn"))

        for card in self._cards.values():
            card.retranslate()
        self._refresh_status()
