"""
ui/components/download_item_widget.py
One row per DownloadTask — compact, IDM-inspired, fully themed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

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
                 on_pause: Callable, on_cancel: Callable, **kwargs) -> None:
        super().__init__(master, fg_color=T.surface, corner_radius=10,
                         border_width=1, border_color=T.border, **kwargs)
        self.task = task
        self._on_pause  = on_pause
        self._on_cancel = on_cancel
        # Snapshot of task.filename taken the first time status reaches
        # COMPLETED.  Mirrors what History does (persists task.filename at
        # DOWNLOAD_COMPLETED event time) so the Open button always opens the
        # folder that actually contains the downloaded file, regardless of any
        # later task-object mutations.
        self._completed_path: str = ""
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
                # COMPLETED.  This is the same moment DownloadService calls
                # history_repo.add(task) — task.filename is guaranteed to hold
                # the engine-resolved final path, not a progress-hook
                # intermediate.  Storing it here means _open_folder() always
                # opens the correct folder even if the task object is later
                # overwritten by a subsequent poll cycle.
                self._completed_path = task.filename
                self._folder_btn.pack(side="left", padx=(4, 0))
                self._cancel_btn.pack_forget()
                self._pause_btn.pack_forget()
        else:
            if self._folder_btn.winfo_ismapped():
                self._folder_btn.pack_forget()
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
        self._size_lbl.configure(text_color=T.text3)
        self._pause_btn.configure(fg_color=T.surface2, hover_color=T.surface3)
        self._cancel_btn.configure(fg_color=T.error_bg)
        self._folder_btn.configure(fg_color=T.success_bg)

    def _open_folder(self) -> None:
        """Open the folder that contains the completed download.

        ROOT-CAUSE FIX: the previous implementation derived the folder via
        ``task.output_dir`` as a fallback, which is the *configured* output
        directory — not necessarily the *actual* directory where yt-dlp wrote
        the file (they can differ when a per-task override is used, or when
        yt-dlp places the merged output in a temp-adjacent path).

        The History tab is always correct because it uses::

            open_folder(Path(fname).parent)

        where ``fname`` is ``task.filename`` snapshotted at the moment the
        DOWNLOAD_COMPLETED event fires — i.e. after the engine has resolved
        the final output path.

        This method now mirrors that approach:
        1. Use ``self._completed_path`` (snapshotted at first-COMPLETED in
           ``refresh()``) as the authoritative source.
        2. Derive the containing folder as ``Path(completed_path).parent``.
        3. If the file still exists, reveal it in the file manager (better UX).
        4. If the file is gone (moved/deleted), open its containing folder.
        5. Only fall back to ``task.output_dir`` when the derived folder does
           not exist — this is a genuine last-resort for edge cases such as
           the user deleting the entire output directory.
        """
        # Use the snapshotted path from first-COMPLETED; guard against the
        # rare case where refresh() hasn't run yet by falling back to the
        # live field (both reference the same engine-resolved value).
        path = self._completed_path or self.task.filename

        if path:
            p = Path(path)
            # Derive the folder that contains the file — identical to History:
            #   open_folder(Path(fname).parent)
            folder = p.parent

            if p.is_file():
                # File present: reveal it (highlights the file in the manager).
                # Fall back to a plain folder open when reveal is unavailable.
                if not reveal_in_explorer(p):
                    open_folder(folder)
                return

            # File absent (moved, renamed, or not yet flushed to disk).
            # Open the containing folder if it still exists.
            if folder.is_dir():
                open_folder(folder)
                return

        # Absolute last resort: the task's configured output directory.
        # This covers the edge case where the entire output folder was deleted
        # or the path stored in task.filename has become invalid.
        output_dir = self.task.output_dir
        if output_dir:
            fb = Path(output_dir)
            if fb.is_dir():
                open_folder(fb)
                return
        logger.warning(
            "_open_folder: no valid path for task %s "
            "(completed_path=%r, output_dir=%r)",
            self.task.id, path, self.task.output_dir,
        )

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")
