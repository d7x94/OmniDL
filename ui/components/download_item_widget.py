"""
ui/components/download_item_widget.py
One row per DownloadTask — compact, IDM-inspired, fully themed.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from ui.components.progress_bar import OmniProgressBar
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, open_folder, reveal_in_explorer

logger = logging.getLogger(__name__)

_STATUS: dict = {
    DownloadStatus.QUEUED:      ("Queued",       "text3",        "surface3"),
    DownloadStatus.DOWNLOADING: ("Downloading",  "primary",      "primary_dim"),
    DownloadStatus.PROCESSING:  ("Processing",   "warning",      "warning_bg"),
    DownloadStatus.PAUSED:      ("Paused",       "text3",        "surface3"),
    DownloadStatus.COMPLETED:   ("Completed",    "success",      "success_bg"),
    DownloadStatus.FAILED:      ("Failed",       "error",        "error_bg"),
    DownloadStatus.CANCELLED:   ("Cancelled",    "text3",        "surface2"),
}

_PROG_STATE: dict = {
    DownloadStatus.QUEUED:      "active",
    DownloadStatus.DOWNLOADING: "active",
    DownloadStatus.PROCESSING:  "active",
    DownloadStatus.PAUSED:      "paused",
    DownloadStatus.COMPLETED:   "complete",
    DownloadStatus.FAILED:      "failed",
    DownloadStatus.CANCELLED:   "failed",
}


class DownloadItemWidget(ctk.CTkFrame):

    def __init__(self, master, task: DownloadTask,
                 on_pause: Callable, on_cancel: Callable,
                 on_convert: Optional[Callable] = None, **kwargs) -> None:
        super().__init__(master, fg_color=T.surface, corner_radius=10,
                         border_width=1, border_color=T.border, **kwargs)
        self.task = task
        self._on_pause   = on_pause
        self._on_cancel  = on_cancel
        self._on_convert = on_convert
        # Snapshot of task.filename taken the first time status reaches
        # COMPLETED.  Mirrors what History does (persists task.filename at
        # DOWNLOAD_COMPLETED event time) so the Open button always opens the
        # folder that actually contains the downloaded file, regardless of any
        # later task-object mutations.
        self._completed_path: str = ""
        self._converting: bool = False   # True while background conversion runs
        self._convert_pct: float = 0.0
        self._build()
        T.register(self._on_theme)

    def _build(self) -> None:
        # Row 1: dot + title + badge + buttons
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 6))

        self._type_dot = ctk.CTkLabel(top, text="●", font=ctk.CTkFont(size=9),
                                       text_color=T.primary, width=12)
        self._type_dot.pack(side="left", padx=(0, 8))

        self._title_lbl = ctk.CTkLabel(
            top, text=self._trunc(self.task.title, 64),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=T.text, anchor="w")
        self._title_lbl.pack(side="left", fill="x", expand=True)

        s_label, s_dot_key, s_bg_key = _STATUS.get(
            self.task.status, ("Unknown", "text3", "surface2"))
        self._status_badge = ctk.CTkLabel(
            top, text=f"  {s_label}  ",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=getattr(T, s_dot_key),
            fg_color=getattr(T, s_bg_key), corner_radius=5)
        self._status_badge.pack(side="left", padx=(8, 8))

        self._btn_box = ctk.CTkFrame(top, fg_color="transparent")
        self._btn_box.pack(side="left")

        self._pause_btn = ctk.CTkButton(
            self._btn_box, text="⏸", width=30, height=26, corner_radius=6,
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2,
            command=lambda: self._on_pause(self.task.id))
        self._pause_btn.pack(side="left", padx=(0, 4))

        self._cancel_btn = ctk.CTkButton(
            self._btn_box, text="✕", width=30, height=26, corner_radius=6,
            fg_color=T.error_bg, hover_color=T.error_bg, text_color=T.error,
            command=lambda: self._on_cancel(self.task.id))
        self._cancel_btn.pack(side="left")

        self._folder_btn = ctk.CTkButton(
            self._btn_box, text="📂  Open", width=72, height=26, corner_radius=6,
            fg_color=T.success_bg, hover_color=T.success_bg,
            text_color=T.success_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._open_folder)

        self._convert_btn = ctk.CTkButton(
            self._btn_box, text="→ MP4", width=64, height=26, corner_radius=6,
            fg_color=T.primary_dim, hover_color=T.surface3,
            text_color=T.primary_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._start_convert)

        # Row 2: progress
        self._prog = OmniProgressBar(self)
        self._prog.pack(fill="x", padx=16, pady=(0, 6))

        # Row 3: stats
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(fill="x", padx=16, pady=(0, 12))

        self._speed_lbl = ctk.CTkLabel(stats, text="",
            font=ctk.CTkFont(size=10), text_color=T.primary_text)
        self._speed_lbl.pack(side="left")

        self._eta_lbl = ctk.CTkLabel(stats, text="",
            font=ctk.CTkFont(size=10), text_color=T.text3)
        self._eta_lbl.pack(side="left", padx=(12, 0))

        self._elapsed_lbl = ctk.CTkLabel(stats, text="",
            font=ctk.CTkFont(size=10), text_color=T.text3)
        self._elapsed_lbl.pack(side="left", padx=(12, 0))

        self._size_lbl = ctk.CTkLabel(stats, text="",
            font=ctk.CTkFont(size=10), text_color=T.text3)
        self._size_lbl.pack(side="right")

        self._err_lbl = ctk.CTkLabel(self, text="",
            font=ctk.CTkFont(size=11), text_color=T.error_text,
            wraplength=600, justify="left")

    def refresh(self, task: DownloadTask) -> None:
        self.task = task
        st = task.status
        s_label, s_dot_key, s_bg_key = _STATUS.get(st, ("Unknown", "text3", "surface2"))

        self._title_lbl.configure(text=self._trunc(task.title, 64))
        self._status_badge.configure(
            text=f"  {s_label}  ",
            text_color=getattr(T, s_dot_key),
            fg_color=getattr(T, s_bg_key))
        self._type_dot.configure(text_color=getattr(T, s_dot_key))
        self._prog.set_progress(task.progress)
        self._prog.set_state(_PROG_STATE.get(st, "active"))
        self._speed_lbl.configure(text=f"↓  {task.speed}" if task.speed else "")
        self._eta_lbl.configure(text=f"ETA  {task.eta}" if task.eta else "")
        elapsed = task.elapsed
        if hasattr(self, "_elapsed_lbl"):
            self._elapsed_lbl.configure(
                text=(
                    f"⏱  {elapsed}"
                    if elapsed and st not in DownloadStatus.terminal_states()
                    else ""
                )
            )

        if task.total_bytes > 0:
            self._size_lbl.configure(
                text=(
                    f"{fmt_bytes(task.downloaded_bytes)}"
                    f"  /  {fmt_bytes(task.total_bytes)}"
                )
            )
        elif task.downloaded_bytes > 0:
            self._size_lbl.configure(text=fmt_bytes(task.downloaded_bytes))
        else:
            self._size_lbl.configure(text="")

        if st == DownloadStatus.FAILED and task.error_msg:
            self._err_lbl.configure(text=f"  {task.error_msg}")
            self._err_lbl.pack(fill="x", padx=16, pady=(0, 8), anchor="w")
        else:
            self._err_lbl.pack_forget()

        terminal   = st in DownloadStatus.terminal_states()
        processing = st == DownloadStatus.PROCESSING

        if terminal:
            self._pause_btn.configure(state="disabled", text_color=T.text3)
            self._cancel_btn.configure(state="disabled", text_color=T.text3)
        elif processing:
            self._pause_btn.configure(state="disabled", text_color=T.text3)
            self._cancel_btn.configure(state="normal",  text_color=T.error)
        else:
            self._pause_btn.configure(
                state="normal", text_color=T.text2,
                text="▶" if st == DownloadStatus.PAUSED else "⏸")
            self._cancel_btn.configure(state="normal", text_color=T.error)

        if st == DownloadStatus.COMPLETED and task.filename:
            if not self._folder_btn.winfo_ismapped():
                # Snapshot the final output path the first time we see
                # COMPLETED.  task.filename is guaranteed to hold the
                # engine-resolved final path at this point.
                self._completed_path = task.filename
                self._folder_btn.pack(side="left", padx=(4, 0))
                self._cancel_btn.pack_forget()
                self._pause_btn.pack_forget()
                # Show → MP4 button for non-MP4 completed files.
                if (self._on_convert
                        and Path(task.filename).suffix.lower() != ".mp4"
                        and not self._converting):
                    self._convert_btn.pack(side="left", padx=(4, 0))
        else:
            if self._folder_btn.winfo_ismapped():
                self._folder_btn.pack_forget()
                self._convert_btn.pack_forget()
                if not self._pause_btn.winfo_ismapped():
                    self._pause_btn.pack(side="left", padx=(0, 4))
                    self._cancel_btn.pack(side="left")

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.surface, border_color=T.border)
        self._title_lbl.configure(text_color=T.text)
        self._speed_lbl.configure(text_color=T.primary_text)
        self._eta_lbl.configure(text_color=T.text3)
        self._elapsed_lbl.configure(text_color=T.text3)
        self._size_lbl.configure(text_color=T.text3)
        self._pause_btn.configure(fg_color=T.surface2, hover_color=T.surface3)
        self._cancel_btn.configure(fg_color=T.error_bg)
        self._folder_btn.configure(fg_color=T.success_bg)
        self._convert_btn.configure(fg_color=T.primary_dim)

    def _start_convert(self) -> None:
        """Launch background FFmpeg conversion and animate the button."""
        if self._converting or not self._on_convert:
            return
        path = self._completed_path or self.task.filename
        if not path:
            return
        self._converting = True
        self._convert_btn.configure(state="disabled", text="Converting…")
        self._prog.set_progress(0.0)
        self._prog.set_state("active")

        def _on_progress(pct: float) -> None:
            if self.winfo_exists():
                self.after(0, lambda p=pct: self._prog.set_progress(p))

        def _on_done(output_path) -> None:
            self._converting = False
            if self.winfo_exists():
                self.after(0, self._on_convert_done)

        def _on_error(msg: str) -> None:
            self._converting = False
            if self.winfo_exists():
                self.after(0, lambda m=msg: self._on_convert_error(m))

        self._on_convert(
            Path(path),
            on_progress=_on_progress,
            on_done=_on_done,
            on_error=_on_error,
        )

    def _on_convert_done(self) -> None:
        if not self.winfo_exists():
            return
        self._prog.set_progress(100.0)
        self._prog.set_state("complete")
        self._convert_btn.configure(text="✓ Done", state="disabled")

    def _on_convert_error(self, msg: str) -> None:
        if not self.winfo_exists():
            return
        self._prog.set_state("failed")
        self._convert_btn.configure(text="→ MP4", state="normal")
        self._err_lbl.configure(text=f"  Convert failed: {msg[:120]}")
        self._err_lbl.pack(fill="x", padx=16, pady=(0, 8), anchor="w")

    def _open_folder(self) -> None:
        """Open the folder that contains the completed download.

        Uses ``self._completed_path`` (snapshot taken at COMPLETED time) as
        the primary path source so a stale live reference cannot cause the
        wrong directory to open.  Falls back to ``self.task.filename`` when
        the snapshot is absent.

        A short retry loop (up to 3 attempts, 0.2 s apart) handles the edge
        case where the OS has not yet flushed the file to disk by the time
        the user clicks "Open".

        Priority order:
          1. File exists -- reveal it in the file manager (highlights the file).
          2. File absent -- open the containing folder if it exists.
          3. Last resort -- open ``task.output_dir`` (configured download dir).
        """
        path = self._completed_path or self.task.filename

        if path:
            p = Path(path)

            # Retry loop: the file may not be flushed to disk yet.
            for attempt in range(3):
                if p.is_file():
                    # File is present -- reveal and highlight it.
                    # Fall back to a plain folder open if reveal is unavailable.
                    if not reveal_in_explorer(p):
                        open_folder(p.parent)
                    return
                if attempt < 2:
                    time.sleep(0.2)

            # File still absent after retries -- open its containing folder.
            folder = p.parent
            if folder.is_dir():
                open_folder(folder)
                return

        # Last resort: the task's configured output directory.
        output_dir = self.task.output_dir
        if output_dir:
            fb = Path(output_dir)
            if fb.is_dir():
                open_folder(fb)
                return

        logger.warning(
            "_open_folder: no valid path for task %s "
            "(filename=%r, output_dir=%r)",
            self.task.id, path, self.task.output_dir,
        )

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")
