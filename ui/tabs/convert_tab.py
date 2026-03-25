"""
ui/tabs/convert_tab.py
Tab chuyển đổi video sang MP4 tương thích iPhone.

Luồng:
  1. User thêm file qua Browse / drag-and-drop, hoặc chọn cả thư mục
  2. Chọn preset chất lượng (Cao / Chuẩn / Nhỏ) và thư mục output
  3. Nhấn "Convert All" → jobs vào hàng chờ, tối đa 2 job chạy song song
  4. Mỗi file có card riêng với media-info, progress bar + trạng thái
  5. Sau khi xong có nút "📂 Mở thư mục"

States:
  PENDING    → chưa bắt đầu (mới thêm vào)
  QUEUED     → đang chờ slot (convert đã bắt đầu nhưng vượt giới hạn concurrency)
  CONVERTING → đang encode
  DONE       → hoàn tất
  FAILED     → lỗi
"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
import tkinter.filedialog as fd
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from app.services.ffmpeg_convert_service import (
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
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, fmt_duration, open_file, open_folder, reveal_in_explorer

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

# ── Supported input formats ───────────────────────────────────────────────────
_INPUT_EXTS = tuple(f"*.{ext}" for ext in sorted(SUPPORTED_EXTS))
_FILETYPES = [
    ("Video files", " ".join(_INPUT_EXTS)),
    ("All files",   "*.*"),
]

# ── Preset definitions (mirrors ffmpeg_convert_service) ──────────────────────
_QUALITY_OPTIONS = [
    ("high",     "🏆  Chất lượng cao",  "H.264 CRF 18 · AAC 192k · Giữ độ phân giải"),
    ("standard", "📱  Chuẩn",           "H.264 CRF 23 · AAC 128k · Phù hợp mọi iPhone"),
    ("small",    "💾  File nhỏ",        "H.264 CRF 28 · AAC 96k · Tối đa 720p"),
    ("custom",   "✏️  Tuỳ chỉnh",       "Giá trị CRF/CQ tuỳ chọn (16–35)"),
]

# ── Max concurrent conversions ────────────────────────────────────────────────
_MAX_CONCURRENT = 2


class FileState(Enum):
    PENDING    = auto()
    QUEUED     = auto()    # waiting for a concurrency slot
    CONVERTING = auto()
    DONE       = auto()
    FAILED     = auto()


_STATE_BADGE: dict[FileState, tuple[str, str, str]] = {
    #                         label               text_token    bg_token
    FileState.PENDING:    ("Chờ",              "text3",       "surface3"),
    FileState.QUEUED:     ("⏳ Hàng chờ",      "text2",       "surface2"),
    FileState.CONVERTING: ("Đang chuyển…",     "warning",     "warning_bg"),
    FileState.DONE:       ("✓ Xong",           "success",     "success_bg"),
    FileState.FAILED:     ("✕ Lỗi",            "error",       "error_bg"),
}

_STATE_PROG: dict[FileState, str] = {
    FileState.PENDING:    "active",
    FileState.QUEUED:     "paused",
    FileState.CONVERTING: "active",
    FileState.DONE:       "complete",
    FileState.FAILED:     "failed",
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
    cancel_fn: Optional[object] = field(default=None, repr=False)  # () -> None


# ─────────────────────────────────────────────────────────────────────────────
# File card widget
# ─────────────────────────────────────────────────────────────────────────────

class FileCard(ctk.CTkFrame):
    """One card per FileJob in the scroll list."""

    def __init__(self, master, job: FileJob,
                 on_remove,          # callable(job_id)
                 on_open_folder,     # callable(job_id)
                 on_cancel,          # callable(job_id)
                 **kwargs) -> None:
        super().__init__(
            master,
            fg_color=T.surface, corner_radius=10,
            border_width=1, border_color=T.border,
            **kwargs,
        )
        self.job = job
        self._on_remove = on_remove
        self._on_open_folder = on_open_folder
        self._on_cancel = on_cancel
        self._build()
        T.register(self._on_theme)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Row 1: icon + filename + badges + buttons ─────────────────────
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))

        ext = self.job.source.suffix.lower().lstrip(".")
        self._dot = ctk.CTkLabel(
            top, text="●",
            font=ctk.CTkFont(size=9), text_color=T.primary, width=12,
        )
        self._dot.pack(side="left", padx=(0, 8))

        self._name_lbl = ctk.CTkLabel(
            top,
            text=self._trunc(self.job.source.name, 55),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=T.text, anchor="w",
        )
        self._name_lbl.pack(side="left", fill="x", expand=True)

        self._ext_badge = ctk.CTkLabel(
            top, text=f"  {ext.upper()}  ",
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color=T.text3, fg_color=T.surface3, corner_radius=4,
        )
        self._ext_badge.pack(side="left", padx=(0, 8))

        s_lbl, s_txt, s_bg = _STATE_BADGE[self.job.state]
        self._state_badge = ctk.CTkLabel(
            top, text=f"  {s_lbl}  ",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=getattr(T, s_txt),
            fg_color=getattr(T, s_bg), corner_radius=5,
        )
        self._state_badge.pack(side="left", padx=(0, 8))

        self._btn_box = ctk.CTkFrame(top, fg_color="transparent")
        self._btn_box.pack(side="left")

        self._remove_btn = ctk.CTkButton(
            self._btn_box, text="✕", width=28, height=26, corner_radius=6,
            fg_color=T.surface2, hover_color=T.error_bg, text_color=T.text3,
            font=ctk.CTkFont(size=11),
            command=lambda: self._on_remove(self.job.id),
        )
        self._remove_btn.pack(side="left")

        self._cancel_btn = ctk.CTkButton(
            self._btn_box, text="⏹  Huỷ", width=72, height=26, corner_radius=6,
            fg_color=T.warning_bg, hover_color=T.error_bg,
            text_color=T.warning,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda: self._on_cancel(self.job.id),
        )
        # _cancel_btn starts hidden; refresh() shows it during QUEUED/CONVERTING

        self._open_btn = ctk.CTkButton(
            self._btn_box, text="📂  Mở", width=72, height=26, corner_radius=6,
            fg_color=T.success_bg, hover_color=T.success_bg,
            text_color=T.success_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda: self._on_open_folder(self.job.id),
        )

        self._preview_btn = ctk.CTkButton(
            self._btn_box, text="▶  Xem", width=62, height=26, corner_radius=6,
            fg_color=T.primary_dim, hover_color=T.primary,
            text_color=T.primary_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._open_preview,
        )

        # ── Row 2: media info ─────────────────────────────────────────────
        info_row = ctk.CTkFrame(self, fg_color="transparent")
        info_row.pack(fill="x", padx=16, pady=(0, 4))

        self._info_lbl = ctk.CTkLabel(
            info_row, text=self._initial_info_text(),
            font=ctk.CTkFont(size=10), text_color=T.text3, anchor="w",
        )
        self._info_lbl.pack(side="left")

        # ── Row 3: progress bar ───────────────────────────────────────────
        self._prog = OmniProgressBar(self)
        self._prog.pack(fill="x", padx=16, pady=(0, 6))

        # ── Row 4: stats ──────────────────────────────────────────────────
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(fill="x", padx=16, pady=(0, 12))

        self._size_lbl = ctk.CTkLabel(
            stats, text=self._file_size_str(),
            font=ctk.CTkFont(size=10), text_color=T.text3,
        )
        self._size_lbl.pack(side="left")

        self._pct_lbl = ctk.CTkLabel(
            stats, text="",
            font=ctk.CTkFont(size=10), text_color=T.primary_text,
        )
        self._pct_lbl.pack(side="left", padx=(12, 0))

        self._err_lbl = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=T.error_text, wraplength=580, justify="left",
        )

        self._out_lbl = ctk.CTkLabel(
            stats, text="",
            font=ctk.CTkFont(size=10), text_color=T.success_text,
            anchor="e",
        )
        self._out_lbl.pack(side="right")

        # Apply FfmpegMediaInfo if already available (e.g. on card rebuild)
        if self.job.media_info is not None:
            self.update_info(self.job.media_info)

    # ── Refresh ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        job = self.job
        s_lbl, s_txt, s_bg = _STATE_BADGE[job.state]
        self._state_badge.configure(
            text=f"  {s_lbl}  ",
            text_color=getattr(T, s_txt),
            fg_color=getattr(T, s_bg),
        )
        self._dot.configure(text_color=getattr(T, s_txt))
        self._prog.set_progress(job.progress)
        self._prog.set_state(_STATE_PROG[job.state])

        if job.state == FileState.CONVERTING:
            self._pct_lbl.configure(text=f"{job.progress:.0f}%")
        else:
            self._pct_lbl.configure(text="")

        if job.state == FileState.DONE and job.output:
            sz = fmt_bytes(job.output.stat().st_size) if job.output.is_file() else ""
            self._out_lbl.configure(text=f"→ {job.output.name}  {sz}")
            if not self._open_btn.winfo_ismapped():
                self._remove_btn.pack_forget()
                self._open_btn.pack(side="left")
                self._preview_btn.pack(side="left", padx=(4, 0))
                self._remove_btn.pack(side="left", padx=(4, 0))
        elif job.state == FileState.FAILED and job.error_msg:
            self._err_lbl.configure(text=f"  {job.error_msg[:160]}")
            self._err_lbl.pack(fill="x", padx=16, pady=(0, 8), anchor="w")
        else:
            if self._open_btn.winfo_ismapped():
                self._open_btn.pack_forget()
                self._preview_btn.pack_forget()

        self._remove_btn.configure(
            state="disabled" if job.state in (
                FileState.QUEUED, FileState.CONVERTING
            ) else "normal"
        )

        # Show cancel button while active, hide otherwise
        is_active = job.state in (FileState.QUEUED, FileState.CONVERTING)
        if is_active:
            if not self._cancel_btn.winfo_ismapped():
                self._cancel_btn.pack(side="left", padx=(4, 0))
        else:
            if self._cancel_btn.winfo_ismapped():
                self._cancel_btn.pack_forget()

    def update_info(self, info: Optional[FfmpegMediaInfo]) -> None:
        """Refresh the media-info label from an ffprobe FfmpegMediaInfo result."""
        if info is None:
            self._info_lbl.configure(text="")
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
        self._info_lbl.configure(text="  ·  ".join(parts) if parts else "")

    # ── Theme ──────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.surface, border_color=T.border)
        self._name_lbl.configure(text_color=T.text)
        self._ext_badge.configure(text_color=T.text3, fg_color=T.surface3)
        self._size_lbl.configure(text_color=T.text3)
        self._info_lbl.configure(text_color=T.text3)
        self._out_lbl.configure(text_color=T.success_text)
        self._remove_btn.configure(fg_color=T.surface2)
        self._cancel_btn.configure(
            fg_color=T.warning_bg, hover_color=T.error_bg,
            text_color=T.warning,
        )
        self._open_btn.configure(fg_color=T.success_bg)
        self._preview_btn.configure(fg_color=T.primary_dim, hover_color=T.primary)

    def _open_preview(self) -> None:
        """Open the converted output file with the OS default application."""
        if self.job.output and self.job.output.exists():
            open_file(self.job.output)
        elif self.job.output:
            open_folder(self.job.output.parent)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _initial_info_text(self) -> str:
        """Show a 'loading…' placeholder until the ffprobe result arrives."""
        if self.job.media_info is not None:
            return ""   # will be filled by update_info() at end of _build
        return "Đang đọc thông tin…"

    def _file_size_str(self) -> str:
        try:
            return fmt_bytes(self.job.source.stat().st_size)
        except Exception:
            return ""

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")


# ─────────────────────────────────────────────────────────────────────────────
# Convert Tab
# ─────────────────────────────────────────────────────────────────────────────

class ConvertTab(ctk.CTkFrame):
    """Dedicated tab for converting local video files to iPhone-compatible MP4."""

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        self._jobs: dict[str, FileJob] = {}
        self._cards: dict[str, FileCard] = {}
        self._quality = tk.StringVar(value="standard")
        self._output_dir: Optional[Path] = None
        self._queue = ConvertQueue(max_concurrent=_MAX_CONCURRENT)
        # Count of jobs in QUEUED or CONVERTING state (for "Convert All" gating)
        self._active_count = 0
        # GPU encoder + speed state
        self._encoder_key = tk.StringVar(value="cpu")
        self._speed_preset = tk.StringVar(value="balanced")
        self._custom_quality = tk.StringVar(value="23")
        self._available_encoders: set[str] = {"cpu"}
        # Filtered (key, label) pairs — kept in sync with _available_encoders.
        # Starts as CPU-only so the dropdown is always usable before detection
        # finishes.  Replaced on the UI thread once detection completes.
        self._available_encoder_options: list[tuple[str, str]] = [
            opt for opt in ENCODER_OPTIONS if opt[0] == "cpu"
        ]
        # Thread-safe callback queue: background threads post callables here;
        # _poll_ui_queue() drains it on the UI thread every 50 ms.
        # Required for Python 3.14+ where self.after() is no longer callable
        # from non-main threads (RuntimeError: main thread is not in main loop).
        self._ui_queue: queue.Queue = queue.Queue()
        # Detect available encoders in background; UI enabled when ready
        threading.Thread(
            target=self._detect_encoders_async,
            daemon=True,
            name="omnidl-detect-encoders",
        ).start()
        self._build()
        self._poll_ui_queue()   # start draining _ui_queue on UI thread
        T.register(self._on_theme)

    # ── Thread-safe UI callback pump ─────────────────────────────────────

    def _poll_ui_queue(self) -> None:
        """Drain _ui_queue on the UI thread.

        Background threads post callables to self._ui_queue instead of
        calling self.after() directly.  Python 3.14 made self.after()
        non-callable from non-main threads; this poller is the safe bridge.
        Runs every 50 ms while the widget exists.
        """
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        "_poll_ui_queue callback raised: %s", exc)
        except queue.Empty:
            pass
        self.after(50, self._poll_ui_queue)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Header ────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(24, 0))

        left_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        left_hdr.pack(side="left", fill="y")

        ctk.CTkLabel(
            left_hdr, text="🍎  Chuyển sang iPhone MP4",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=T.text,
        ).pack(anchor="w")

        ctk.CTkLabel(
            left_hdr,
            text="H.264 · AAC · yuv420p · profile High — chạy mượt trên mọi iPhone",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        ).pack(anchor="w", pady=(2, 0))

        # Header buttons
        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        right_hdr.pack(side="right", fill="y")

        self._add_btn = ctk.CTkButton(
            right_hdr, text="＋  Thêm file",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=36, width=130, corner_radius=8,
            fg_color=T.primary, hover_color=T.primary_hover,
            text_color="white",
            command=self._browse_files,
        )
        self._add_btn.pack(side="left", padx=(0, 8))

        # NEW: add whole folder
        self._folder_btn = ctk.CTkButton(
            right_hdr, text="📁  Thêm thư mục",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=36, width=150, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2,
            command=self._browse_folder,
        )
        self._folder_btn.pack(side="left", padx=(0, 8))

        self._clear_btn = ctk.CTkButton(
            right_hdr, text="Xóa xong",
            font=ctk.CTkFont(size=11),
            height=36, width=100, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2,
            command=self._clear_done,
        )
        self._clear_btn.pack(side="left")

        # ── Config panel ──────────────────────────────────────────────────
        cfg = ctk.CTkFrame(self, fg_color=T.surface, corner_radius=12,
                           border_width=1, border_color=T.border)
        cfg.pack(fill="x", padx=28, pady=(16, 0))
        self._cfg_frame = cfg

        q_row = ctk.CTkFrame(cfg, fg_color="transparent")
        q_row.pack(fill="x", padx=20, pady=(16, 8))

        # col 0 = label cố định, col 1-4 = 4 card chia đều không gian còn lại
        q_row.columnconfigure(0, minsize=90)
        q_row.columnconfigure((1, 2, 3, 4), weight=1, uniform="qual_card")

        ctk.CTkLabel(
            q_row, text="Chất lượng",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=T.text2,
            width=90, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=4)

        self._quality_cards: dict[str, ctk.CTkFrame] = {}
        self._quality_main_labels: dict[str, ctk.CTkLabel] = {}
        for i, (key, label, desc) in enumerate(_QUALITY_OPTIONS):
            card = self._make_quality_card(q_row, key, label, desc)
            pad_right = 8 if i < len(_QUALITY_OPTIONS) - 1 else 0
            card.grid(row=0, column=i + 1, padx=(0, pad_right), sticky="nsew")
            self._quality_cards[key] = card

        # Custom quality value entry (shown only when "custom" is selected)
        custom_row = ctk.CTkFrame(cfg, fg_color="transparent")
        custom_row.pack(fill="x", padx=20, pady=(0, 4))

        ctk.CTkLabel(
            custom_row, text="",
            width=90,
        ).pack(side="left")

        self._custom_lbl = ctk.CTkLabel(
            custom_row, text="Giá trị (16–35):",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        )
        self._custom_lbl.pack(side="left", padx=(0, 6))

        self._custom_entry = ctk.CTkEntry(
            custom_row,
            textvariable=self._custom_quality,
            width=60, height=28, corner_radius=6,
            fg_color=T.input, border_color=T.border2, border_width=1,
            text_color=T.text, font=ctk.CTkFont(size=12),
        )
        self._custom_entry.pack(side="left")
        # Validate and clamp on FocusOut so user sees the corrected value
        # immediately rather than being silently changed at convert time.
        self._custom_entry.bind("<FocusOut>", self._validate_custom_quality)
        self._custom_entry.bind("<Return>",   self._validate_custom_quality)
        custom_row.pack_forget()   # hidden until "custom" quality selected
        self._custom_row = custom_row

        # ── Encoder row ───────────────────────────────────────────────────
        enc_row = ctk.CTkFrame(cfg, fg_color="transparent")
        enc_row.pack(fill="x", padx=20, pady=(4, 4))

        ctk.CTkLabel(
            enc_row, text="Encoder",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=T.text2,
            width=90, anchor="w",
        ).pack(side="left")

        self._encoder_menu = ctk.CTkOptionMenu(
            enc_row,
            values=[label for _, label in self._available_encoder_options],
            command=self._on_encoder_change,
            width=200, height=32, corner_radius=6,
            fg_color=T.surface2, button_color=T.surface3,
            button_hover_color=T.border, text_color=T.text,
            font=ctk.CTkFont(size=11),
        )
        self._encoder_menu.pack(side="left", padx=(0, 12))

        self._encoder_status_lbl = ctk.CTkLabel(
            enc_row, text="Đang kiểm tra…",
            font=ctk.CTkFont(size=10), text_color=T.text3,
        )
        self._encoder_status_lbl.pack(side="left")

        # ── Speed preset row ──────────────────────────────────────────────
        spd_row = ctk.CTkFrame(cfg, fg_color="transparent")
        spd_row.pack(fill="x", padx=20, pady=(4, 4))

        ctk.CTkLabel(
            spd_row, text="Tốc độ",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=T.text2,
            width=90, anchor="w",
        ).pack(side="left")

        self._speed_cards: dict[str, ctk.CTkFrame] = {}
        self._speed_main_labels: dict[str, ctk.CTkLabel] = {}
        for s_key, s_label in SPEED_OPTIONS:
            s_card = ctk.CTkFrame(
                spd_row,
                corner_radius=6,
                fg_color=T.primary_dim if s_key == "balanced" else T.surface2,
                border_width=1,
                border_color=T.primary if s_key == "balanced" else T.border,
                cursor="hand2",
            )
            lbl = ctk.CTkLabel(
                s_card, text=s_label,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=T.text if s_key == "balanced" else T.text2,
            )
            lbl.pack(padx=12, pady=6)
            self._speed_main_labels[s_key] = lbl
            for w in (s_card, lbl):
                w.bind("<Button-1>", lambda _e, k=s_key: self._on_speed_change(k))
            s_card.pack(side="left", padx=(0, 6))
            self._speed_cards[s_key] = s_card

        out_row = ctk.CTkFrame(cfg, fg_color="transparent")
        out_row.pack(fill="x", padx=20, pady=(4, 16))

        ctk.CTkLabel(
            out_row, text="Lưu vào",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=T.text2,
            width=90, anchor="w",
        ).pack(side="left")

        self._out_entry = ctk.CTkEntry(
            out_row,
            placeholder_text="Cùng thư mục với video gốc",
            font=ctk.CTkFont(size=12),
            height=36, corner_radius=8,
            fg_color=T.input, border_color=T.border2, border_width=1,
            text_color=T.text,
        )
        self._out_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            out_row, text="Browse",
            font=ctk.CTkFont(size=11),
            height=36, width=80, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2,
            command=self._browse_output,
        ).pack(side="left")

        # ── Bottom action bar ─────────────────────────────────────────────
        # QUAN TRỌNG: pack side="bottom" TRƯỚC khi pack fill+expand để tkinter
        # cấp phát không gian cho bar trước — tránh bar bị scroll đẩy ra ngoài
        # khi cửa sổ nhỏ.
        bar = ctk.CTkFrame(self, fg_color=T.surface, corner_radius=0,
                           border_width=1, border_color=T.border)
        bar.pack(fill="x", side="bottom")
        self._bar = bar

        ctk.CTkFrame(bar, height=1, fg_color=T.border, corner_radius=0).pack(
            fill="x", side="top")

        inner_bar = ctk.CTkFrame(bar, fg_color="transparent")
        inner_bar.pack(fill="both", expand=True, padx=20, pady=10)

        self._status_lbl = ctk.CTkLabel(
            inner_bar, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        )
        self._status_lbl.pack(side="left")

        self._convert_btn = ctk.CTkButton(
            inner_bar,
            text="▶  Convert All",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40, width=160, corner_radius=8,
            fg_color=T.primary, hover_color=T.primary_hover, text_color="white",
            command=self._start_all,
        )
        self._convert_btn.pack(side="right")

        # ── File list ─────────────────────────────────────────────────────
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        self._scroll.pack(fill="both", expand=True, padx=28, pady=(12, 0))

        self._empty = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._empty.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self._empty, text="📂",
            font=ctk.CTkFont(size=40), text_color=T.text3,
        ).pack(pady=(50, 8))
        ctk.CTkLabel(
            self._empty, text="Chưa có file nào",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=T.text3,
        ).pack()
        ctk.CTkLabel(
            self._empty,
            text='Nhấn "＋ Thêm file" hoặc "📁 Thêm thư mục" để chọn video',
            font=ctk.CTkFont(size=12), text_color=T.text3,
        ).pack(pady=(4, 0))
        ctk.CTkLabel(
            self._empty,
            text="Hỗ trợ: MP4, MKV, WebM, AVI, MOV, FLV, WMV, TS, 3GP…",
            font=ctk.CTkFont(size=10), text_color=T.text3,
        ).pack(pady=(2, 50))

        self._on_quality_change("standard")

    def _make_quality_card(
        self, parent, key: str, label: str, desc: str
    ) -> ctk.CTkFrame:
        is_selected = (key == self._quality.get())
        card = ctk.CTkFrame(
            parent,
            corner_radius=8,
            fg_color=T.primary_dim if is_selected else T.surface2,
            border_width=1,
            border_color=T.primary if is_selected else T.border,
            cursor="hand2",
        )
        main_lbl = ctk.CTkLabel(
            card, text=label,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=T.text if is_selected else T.text2,
        )
        main_lbl.pack(padx=14, pady=(10, 2))
        self._quality_main_labels[key] = main_lbl
        ctk.CTkLabel(
            card, text=desc,
            font=ctk.CTkFont(size=9), text_color=T.text3,
            wraplength=130,
        ).pack(padx=14, pady=(0, 10))
        for w in (card, *card.winfo_children()):
            w.bind("<Button-1>", lambda _e, k=key: self._on_quality_change(k))
        return card

    # ── Event handlers ────────────────────────────────────────────────────

    def _on_quality_change(self, key: str) -> None:
        self._quality.set(key)
        for k, card in self._quality_cards.items():
            selected = (k == key)
            card.configure(
                fg_color=T.primary_dim if selected else T.surface2,
                border_color=T.primary if selected else T.border,
            )
            lbl = self._quality_main_labels.get(k)
            if lbl and lbl.winfo_exists():
                lbl.configure(
                    text_color=T.text if selected else T.text2
                )
        # Show custom quality entry only when "custom" is selected
        if key == "custom":
            self._custom_row.pack(fill="x", padx=20, pady=(0, 4))
        else:
            self._custom_row.pack_forget()

    # ── Encoder detection ─────────────────────────────────────────────────

    def _validate_custom_quality(self, _event=None) -> None:
        """Clamp the custom CRF entry to [16, 35] on FocusOut / Enter.

        Provides immediate feedback: the field is corrected in place so the
        user sees the actual value that will be used, instead of silently
        being clamped only at convert time.  Border turns red briefly when
        the value was out of range.
        """
        try:
            val = int(self._custom_quality.get())
        except (ValueError, tk.TclError):
            val = 23
        clamped = max(16, min(35, val))
        self._custom_quality.set(str(clamped))
        if val != clamped:
            # Flash border red to signal the value was out of range
            self._custom_entry.configure(border_color=T.error)
            self.after(1200, lambda: self._custom_entry.configure(
                border_color=T.border2))

    def _detect_encoders_async(self) -> None:
        """Background thread: call get_available_encoder_options(), then hand
        the result to the UI thread via after(0, …).  Never touches widgets
        directly — Tkinter is not thread-safe."""
        available_opts = get_available_encoder_options()
        self._ui_queue.put(lambda opts=available_opts: self._apply_available_encoders(opts))

    def _apply_available_encoders(
        self, available_opts: list[tuple[str, str]]
    ) -> None:
        """UI-thread: replace dropdown values with the filtered encoder list.

        ``available_opts`` is the list returned by
        :func:`get_available_encoder_options` — it already contains only
        encoders that are detected *and* validated on this machine, with CPU
        always first.
        """
        # Guard: widget may have been destroyed while encoder detection ran
        # (e.g. app closed in the first 1-2 s after startup).
        if not self.winfo_exists():
            return
        # Guarantee CPU is present even if something went wrong upstream
        if not any(key == "cpu" for key, _ in available_opts):
            cpu_opt = next((o for o in ENCODER_OPTIONS if o[0] == "cpu"), ("cpu", "CPU (libx264)"))
            available_opts = [cpu_opt] + list(available_opts)

        self._available_encoder_options = available_opts
        self._available_encoders = {key for key, _ in available_opts}

        # Replace dropdown values — ConfigureError is safe to ignore if widget
        # was destroyed while detection was running.
        labels = [label for _, label in available_opts]
        try:
            self._encoder_menu.configure(values=labels)
        except Exception:
            return

        # If current selection is no longer available, fall back to CPU
        current_key = self._encoder_key.get()
        if current_key not in self._available_encoders:
            self._encoder_key.set("cpu")
            cpu_label = next((lbl for k, lbl in available_opts if k == "cpu"), labels[0])
            self._encoder_menu.set(cpu_label)

        # Update status label
        gpu_labels = [lbl for k, lbl in available_opts if k != "cpu"]
        status = f"GPU: {', '.join(gpu_labels)}" if gpu_labels else "Chỉ CPU"
        self._encoder_status_lbl.configure(text=status, text_color=T.text3)

    # ── Encoder / speed event handlers ───────────────────────────────────

    def _on_encoder_change(self, label: str) -> None:
        # Map label back to key using the *filtered* options, not the static
        # full list — the two may have different entries after detection.
        for key, opt_label in self._available_encoder_options:
            if opt_label == label:
                self._encoder_key.set(key)
                return
        # Fallback: if label somehow not found, default to CPU
        self._encoder_key.set("cpu")

    def _on_speed_change(self, key: str) -> None:
        self._speed_preset.set(key)
        for k, card in self._speed_cards.items():
            selected = (k == key)
            card.configure(
                fg_color=T.primary_dim if selected else T.surface2,
                border_color=T.primary if selected else T.border,
            )
            lbl = self._speed_main_labels.get(k)
            if lbl and lbl.winfo_exists():
                lbl.configure(
                    text_color=T.text if selected else T.text2
                )

    def _browse_files(self) -> None:
        paths = fd.askopenfilenames(
            title="Chọn video để chuyển đổi",
            filetypes=_FILETYPES,
        )
        for p in paths:
            self._add_file(Path(p))
        self._refresh_ui()

    def _browse_folder(self) -> None:
        """Open a folder dialog, then scan it for media files in a background thread."""
        d = fd.askdirectory(title="Chọn thư mục chứa video")
        if not d:
            return
        self._status_lbl.configure(text="Đang quét thư mục…", text_color=T.text3)
        threading.Thread(
            target=self._scan_folder_async,
            args=(Path(d),),
            daemon=True,
            name="omnidl-folder-scan",
        ).start()

    def _scan_folder_async(self, folder: Path) -> None:
        """Background: scan folder, then dispatch results back to the UI thread."""
        found = scan_folder_for_media(folder)
        self._ui_queue.put(lambda f=found, d=folder: self._add_files_from_scan(f, d))

    def _add_files_from_scan(self, files: list[Path], folder: Path) -> None:
        """UI-thread: bulk-add scanned files and refresh."""
        for f in files:
            self._add_file(f)
        self._refresh_ui()
        count = len(files)
        if count == 0:
            self._status_lbl.configure(
                text=f"Không tìm thấy video trong {folder.name}",
                text_color=T.text3,
            )
        else:
            self._status_lbl.configure(
                text=f"Đã thêm {count} file từ {folder.name}",
                text_color=T.success,
            )

    def _browse_output(self) -> None:
        d = fd.askdirectory(title="Chọn thư mục lưu file đã chuyển")
        if d:
            self._output_dir = Path(d)
            self._out_entry.delete(0, "end")
            self._out_entry.insert(0, str(self._output_dir))

    def _add_file(self, path: Path) -> None:
        """Add a single file to the job list and start background media probe."""
        existing = {j.source.resolve() for j in self._jobs.values()}
        if path.resolve() in existing:
            return
        if path.suffix.lower().lstrip(".") not in SUPPORTED_EXTS:
            return
        job = FileJob(source=path)
        self._jobs[job.id] = job
        # Probe media info in background; update the card when ready
        threading.Thread(
            target=self._probe_info_async,
            args=(job,),
            daemon=True,
            name=f"omnidl-probe-{path.stem[:16]}",
        ).start()

    def _probe_info_async(self, job: FileJob) -> None:
        """Background: run ffprobe and update the card with the result."""
        info = probe_media_info(job.source)
        job.media_info = info
        self._ui_queue.put(lambda j=job: self._update_card_info(j))

    def _update_card_info(self, job: FileJob) -> None:
        """UI-thread: push FfmpegMediaInfo result into the job's FileCard."""
        card = self._cards.get(job.id)
        if card and card.winfo_exists():
            card.update_info(job.media_info)

    def _start_all(self) -> None:
        pending = [j for j in self._jobs.values()
                   if j.state == FileState.PENDING]
        if not pending:
            return

        quality = self._quality.get()
        out_entry_val = self._out_entry.get().strip()
        output_dir: Optional[Path] = None
        if out_entry_val:
            output_dir = Path(out_entry_val)
        elif self._output_dir:
            output_dir = self._output_dir

        # Build encode settings — fall back to CPU if selected encoder unavailable
        encoder_key = self._encoder_key.get()
        if encoder_key not in self._available_encoders:
            logger.info(
                "Encoder %r not available; falling back to CPU", encoder_key
            )
            encoder_key = "cpu"

        try:
            custom_val = int(self._custom_quality.get())
        except (ValueError, tk.TclError):
            custom_val = 23
            self._custom_quality.set("23")  # restore valid value in UI

        encode_settings = EncodeSettings(
            encoder_key=encoder_key,
            quality=quality,
            speed_preset=self._speed_preset.get(),
            custom_quality=max(16, min(35, custom_val)),
        )

        self._convert_btn.configure(state="disabled")

        # Mark all as QUEUED first, then submit to the queue
        for job in pending:
            job.state = FileState.QUEUED
            job.progress = 0.0
            self._active_count += 1
            self._rebuild_card(job)

        for job in pending:
            self._submit_job(job, quality, output_dir, encode_settings)

        self._refresh_ui()

    def _submit_job(
        self,
        job: FileJob,
        quality: str,
        output_dir: Optional[Path],
        encode_settings: Optional[EncodeSettings] = None,
    ) -> None:
        """Submit *job* to the ConvertQueue.  All callbacks are thread-safe."""

        def on_start() -> None:
            """Called from worker thread when the semaphore slot is acquired."""
            job.state = FileState.CONVERTING
            job.progress = 0.0
            self._ui_queue.put(lambda j=job: self._tick_card(j))

        def on_progress(pct: float) -> None:
            job.progress = pct
            self._ui_queue.put(lambda j=job: self._tick_card(j))

        def on_done(out_path: Path) -> None:
            job.state = FileState.DONE
            job.progress = 100.0
            job.output = out_path
            self._ui_queue.put(lambda j=job: self._finish_job(j))

        def on_error(msg: str) -> None:
            job.state = FileState.FAILED
            job.error_msg = msg
            self._ui_queue.put(lambda j=job: self._finish_job(j))

        job.cancel_fn = self._queue.submit(
            source=job.source,
            quality=quality,        # type: ignore[arg-type]
            output_dir=output_dir,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            on_start=on_start,
            encode_settings=encode_settings,
        )

    # ── Card management ───────────────────────────────────────────────────

    def _rebuild_card(self, job: FileJob) -> None:
        old = self._cards.pop(job.id, None)
        if old and old.winfo_exists():
            old.destroy()
        card = FileCard(
            self._scroll, job,
            on_remove=self._remove_job,
            on_open_folder=self._open_output,
            on_cancel=self._cancel_job,
        )
        card.pack(fill="x", pady=(0, 8))
        self._cards[job.id] = card

    def _tick_card(self, job: FileJob) -> None:
        card = self._cards.get(job.id)
        if card and card.winfo_exists():
            card.refresh()
        self._refresh_status()

    def _finish_job(self, job: FileJob) -> None:
        """UI-thread: decrement active count then refresh the card."""
        self._active_count -= 1
        self._finish_card(job)

    def _finish_card(self, job: FileJob) -> None:
        card = self._cards.get(job.id)
        if card and card.winfo_exists():
            card.refresh()
        self._refresh_status()
        if self._active_count == 0 and self.winfo_exists():
            self._convert_btn.configure(state="normal")

    def _refresh_ui(self) -> None:
        """Sync full card list with self._jobs."""
        for jid in list(self._cards):
            if jid not in self._jobs:
                c = self._cards.pop(jid)
                if c.winfo_exists():
                    c.destroy()

        for job in self._jobs.values():
            if job.id not in self._cards:
                card = FileCard(
                    self._scroll, job,
                    on_remove=self._remove_job,
                    on_open_folder=self._open_output,
                    on_cancel=self._cancel_job,
                )
                card.pack(fill="x", pady=(0, 8))
                self._cards[job.id] = card

        if self._jobs:
            self._empty.pack_forget()
        elif not self._empty.winfo_ismapped():
            self._empty.pack(fill="both", expand=True)

        self._refresh_status()

    def _refresh_status(self) -> None:
        # Guard: widget may be destroyed when called from _finish_card during shutdown.
        if not self.winfo_exists():
            return
        total      = len(self._jobs)
        done       = sum(1 for j in self._jobs.values() if j.state == FileState.DONE)
        converting = sum(1 for j in self._jobs.values() if j.state == FileState.CONVERTING)
        queued     = sum(1 for j in self._jobs.values() if j.state == FileState.QUEUED)
        failed     = sum(1 for j in self._jobs.values() if j.state == FileState.FAILED)

        if total == 0:
            self._status_lbl.configure(text="")
        elif converting > 0 or queued > 0:
            parts = []
            if converting:
                parts.append(f"Đang xử lý {converting}")
            if queued:
                parts.append(f"{queued} chờ")
            self._status_lbl.configure(
                text="  ·  ".join(parts) + f" / {total} file",
                text_color=T.warning,
            )
        elif done == total:
            self._status_lbl.configure(
                text=f"Hoàn tất {done}/{total} file ✓",
                text_color=T.success,
            )
        else:
            parts2 = []
            if done:
                parts2.append(f"{done} xong")
            if failed:
                parts2.append(f"{failed} lỗi")
            pending = total - done - failed
            if pending:
                parts2.append(f"{pending} chờ")
            self._status_lbl.configure(
                text="  ·  ".join(parts2),
                text_color=T.text3,
            )

    def _remove_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)
        self._refresh_ui()

    def _cancel_job(self, job_id: str) -> None:
        """Signal the worker to stop, then remove the card immediately."""
        job = self._jobs.get(job_id)
        if job and job.cancel_fn is not None:
            job.cancel_fn()           # type: ignore[operator]
        self._jobs.pop(job_id, None)
        self._refresh_ui()

    def _clear_done(self) -> None:
        done_ids = [
            jid for jid, j in self._jobs.items()
            if j.state in (FileState.DONE, FileState.FAILED)
        ]
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

    # ── Theme ──────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._cfg_frame.configure(fg_color=T.surface, border_color=T.border)
        self._bar.configure(fg_color=T.surface, border_color=T.border)
        self._out_entry.configure(fg_color=T.input, border_color=T.border2,
                                  text_color=T.text)
        self._add_btn.configure(fg_color=T.primary, hover_color=T.primary_hover)
        self._folder_btn.configure(fg_color=T.surface2, hover_color=T.surface3,
                                   text_color=T.text2)
        self._clear_btn.configure(fg_color=T.surface2, hover_color=T.surface3,
                                  text_color=T.text2)
        self._convert_btn.configure(fg_color=T.primary, hover_color=T.primary_hover)
        self._scroll.configure(scrollbar_button_color=T.scrollbar,
                               scrollbar_button_hover_color=T.scrollbar_hover)
        self._on_quality_change(self._quality.get())
        self._on_speed_change(self._speed_preset.get())
        # Refresh custom entry border in case it was left in error state
        self._custom_entry.configure(
            fg_color=T.input, border_color=T.border2, text_color=T.text
        )
