"""
ui/components/download_item_widget.py
One row per DownloadTask — compact, IDM-inspired, fully themed.
"""
from __future__ import annotations

import logging
import queue
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from ui.components.post_download_actions import PostDownloadActions
from ui.components.progress_bar import OmniProgressBar
from ui.themes.tokens import T
from utils.helpers import fmt_bytes, open_file, open_folder, reveal_in_explorer

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
                 on_convert: Optional[Callable] = None,
                 on_send: Optional[Callable] = None,
                 on_delete: Optional[Callable] = None,
                 **kwargs) -> None:
        super().__init__(master, fg_color=T.surface, corner_radius=10,
                         border_width=1, border_color=T.border, **kwargs)
        self.task = task
        self._on_pause   = on_pause
        self._on_cancel  = on_cancel
        self._on_convert = on_convert
        self._on_send    = on_send
        self._on_delete  = on_delete
        # Snapshot of task.filename taken the first time status reaches
        # COMPLETED.  Mirrors what History does (persists task.filename at
        # DOWNLOAD_COMPLETED event time) so the Open button always opens the
        # folder that actually contains the downloaded file, regardless of any
        # later task-object mutations.
        self._completed_path: str = ""
        self._converting: bool = False   # True while background conversion runs
        self._convert_pct: float = 0.0
        # Thread-safe callback queue for FFmpeg conversion callbacks
        # (Python 3.14: self.after() not callable from background threads)
        self._ui_queue: queue.Queue = queue.Queue()
        # URL label shown below title on FAILED — initialised here so
        # refresh() is safe even if _build() is mocked in tests
        self._url_lbl: object = None
        self._build()
        self._drain_ui_queue()   # start poller
        T.register(self._on_theme)

    def _drain_ui_queue(self) -> None:
        """Drain _ui_queue on the UI thread (Python 3.14 thread-safety).

        FFmpeg conversion callbacks fire on a worker thread and post
        callables here instead of calling self.after() directly.
        Runs every 50 ms while widget exists.
        """
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.warning("_ui_queue callback raised: %s", exc)
        except queue.Empty:
            pass
        self.after(50, self._drain_ui_queue)

    def _build(self) -> None:
        # Row 1: dot + title + badge + buttons
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 6))

        self._type_dot = ctk.CTkLabel(top, text="●", font=ctk.CTkFont(size=9),
                                       text_color=T.primary, width=12)

        s_label, s_dot_key, s_bg_key = _STATUS.get(
            self.task.status, ("Unknown", "text3", "surface2"))
        self._status_badge = ctk.CTkLabel(
            top, text=f"  {s_label}  ",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=getattr(T, s_dot_key),
            fg_color=getattr(T, s_bg_key), corner_radius=5)

        self._btn_box = ctk.CTkFrame(top, fg_color="transparent")

        # Pack right-anchored widgets FIRST so they always get their space;
        # _title_lbl (expand=True) then fills whatever remains.
        self._btn_box.pack(side="right")
        self._status_badge.pack(side="right", padx=(8, 8))
        self._type_dot.pack(side="left", padx=(0, 8))

        self._title_lbl = ctk.CTkLabel(
            top, text=self._trunc(self.task.title, 64),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=T.text, anchor="w", width=1)
        self._title_lbl.pack(side="left", fill="x", expand=True)

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

        self._preview_btn = ctk.CTkButton(
            self._btn_box, text="▶  Xem", width=62, height=26, corner_radius=6,
            fg_color=T.primary_dim, hover_color=T.primary,
            text_color=T.primary_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._open_preview)

        self._convert_btn = ctk.CTkButton(
            self._btn_box, text="→ MP4", width=64, height=26, corner_radius=6,
            fg_color=T.primary_dim, hover_color=T.surface3,
            text_color=T.primary_text,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=self._start_convert)

        # ── Post-download action bar (hidden until COMPLETED) ──────────────
        # Replaces the old standalone → MP4 button with the unified
        # Convert / Send / Delete trio from PostDownloadActions.
        self._post_actions = PostDownloadActions(
            self._btn_box,
            on_convert=self._on_post_convert,
            on_send=self._on_post_send,
            on_delete=self._on_post_delete,
            compact=True,
        )
        # Not packed yet — shown by refresh() on first COMPLETED tick.

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

        # URL label — shown only when download fails so user knows which link to retry
        self._url_lbl = ctk.CTkLabel(self, text="",
            font=ctk.CTkFont(size=10), text_color=T.text3,
            wraplength=680, justify="left", anchor="w")

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

        if st == DownloadStatus.FAILED:
            # Show the source URL so user knows which link to retry
            _url_display = task.url if hasattr(task, "url") and task.url else ""
            _ulbl = getattr(self, "_url_lbl", None)
            if _url_display and _ulbl is not None:
                try:
                    if not _ulbl.winfo_ismapped():
                        _ulbl.pack(fill="x", padx=16, pady=(0, 2))
                    _ulbl.configure(text=f"🔗 {_url_display[:100]}")
                except Exception:
                    pass
            if task.error_msg:
                self._err_lbl.configure(text=f"  {task.error_msg}")
        else:
            _ulbl = getattr(self, "_url_lbl", None)
            if _ulbl is not None:
                try:
                    if _ulbl.winfo_ismapped():
                        _ulbl.pack_forget()
                except Exception:
                    pass
            self._err_lbl.pack_forget()

        terminal   = st in DownloadStatus.terminal_states()
        processing = st == DownloadStatus.PROCESSING
        # gallery-dl downloads run as subprocesses — pause has no effect.
        # Disable the pause button for these tasks so the user is not misled.
        # Safe guard: task.media_info may be None before analysis completes.
        is_gallery_dl = (
            task.media_info is not None
            and getattr(task.media_info, "source_engine", "yt_dlp") == "gallery_dl"
        )

        if terminal:
            self._pause_btn.configure(state="disabled", text_color=T.text3)
            self._cancel_btn.configure(state="disabled", text_color=T.text3)
            # BUG BT: For COMPLETED status the pause/cancel buttons must be
            # hidden unconditionally — even when task.filename is not yet
            # populated.  Previously pack_forget() only ran inside the
            # "if st == COMPLETED and task.filename" block below, so a
            # COMPLETED task with an empty filename left both buttons visible
            # (but disabled) with no way to dismiss them.
            if st == DownloadStatus.COMPLETED:
                if self._pause_btn.winfo_ismapped():
                    self._pause_btn.pack_forget()
                if self._cancel_btn.winfo_ismapped():
                    self._cancel_btn.pack_forget()
        elif processing:
            self._pause_btn.configure(state="disabled", text_color=T.text3)
            self._cancel_btn.configure(state="normal",  text_color=T.error)
        else:
            self._pause_btn.configure(
                state="disabled" if is_gallery_dl else "normal",
                text_color=T.text3 if is_gallery_dl else T.text2,
                text="⏸")
            self._cancel_btn.configure(state="normal", text_color=T.error)

        if st == DownloadStatus.COMPLETED and task.filename:
            if not self._folder_btn.winfo_ismapped():
                # Snapshot the final output path the first time we see
                # COMPLETED.  task.filename is guaranteed to hold the
                # engine-resolved final path at this point.
                self._completed_path = task.filename
                self._folder_btn.pack(side="left", padx=(4, 0))
                self._preview_btn.pack(side="left", padx=(4, 0))
                self._cancel_btn.pack_forget()
                self._pause_btn.pack_forget()
                # Show the unified Convert/Send/Delete action bar.
                # The old standalone → MP4 button is intentionally skipped —
                # PostDownloadActions covers Convert and adds Send + Delete.
                _pa = getattr(self, "_post_actions", None)
                if _pa is not None and not _pa.winfo_ismapped():
                    _pa.pack(side="left", padx=(4, 0))
                    _gdl = getattr(task, "gallery_dl_files", None)
                    _pa.show(Path(task.filename), gallery_dl_files=_gdl)
                # Hide the legacy → MP4 button (kept in code for safety;
                # PostDownloadActions supersedes it).
                if hasattr(self, "_convert_btn") and self._convert_btn.winfo_ismapped():
                    self._convert_btn.pack_forget()
        else:
            if self._folder_btn.winfo_ismapped():
                self._folder_btn.pack_forget()
                self._preview_btn.pack_forget()
                self._convert_btn.pack_forget()
                _pa = getattr(self, "_post_actions", None)
                if _pa is not None:
                    _pa.hide()
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
        self._preview_btn.configure(fg_color=T.primary_dim, hover_color=T.primary)
        self._convert_btn.configure(fg_color=T.primary_dim)

    def _start_convert(self) -> None:
        """Launch background FFmpeg conversion and animate the button."""
        if self._converting or not getattr(self, "_on_convert", None):
            return
        path = self._completed_path or self.task.filename
        if not path:
            return
        self._converting = True
        self._convert_btn.configure(state="disabled", text="Converting…")
        self._prog.set_progress(0.0)
        self._prog.set_state("active")

        def _on_progress(pct: float) -> None:
            self._ui_queue.put(lambda p=pct: self._prog.set_progress(p))

        def _on_done(output_path) -> None:
            self._converting = False
            self._ui_queue.put(self._on_convert_done)

        def _on_error(msg: str) -> None:
            self._converting = False
            self._ui_queue.put(lambda m=msg: self._on_convert_error(m))

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

    def _open_preview(self) -> None:
        """Open the completed file with the OS default application."""
        p_str = self._completed_path or getattr(self.task, "filename", "")
        if not p_str:
            return
        p = Path(p_str)
        if not p.is_absolute() and getattr(self.task, "output_dir", ""):
            p = Path(self.task.output_dir) / p
        if p.exists():
            open_file(p)
        else:
            # File moved/deleted — fall back to opening parent folder
            open_folder(p.parent)

    def _open_folder(self) -> None:
        """Open the folder that contains the completed download.

        Uses ``self._completed_path`` (snapshot taken at COMPLETED time) as
        the primary path source so a stale live reference cannot cause the
        wrong directory to open.  Falls back to ``self.task.filename`` when
        the snapshot is absent.

        If the file is present immediately it is revealed synchronously.
        If it is absent (e.g. OS flush still in progress) up to 2 additional
        checks are scheduled via ``widget.after()`` so the Tk event loop
        remains fully responsive — no ``time.sleep`` on the UI thread.

        IMPORTANT — always call .resolve() on the raw string before any
        further path operations.  yt-dlp occasionally returns a relative
        path on Windows + PyInstaller environments (CWD may be the EXE
        directory, not the download folder), so resolving here makes
        p.parent reliable regardless of how the app was launched.

        Priority order:
          1. File exists -- reveal it in the file manager (highlights the file).
          2. File absent -- open the containing folder if it exists.
          3. Last resort -- open ``task.output_dir`` (configured download dir).
        """
        raw = self._completed_path or self.task.filename

        if raw:
            # Resolve to absolute path.  If *raw* is a bare filename or a
            # relative path (yt-dlp on Windows + PyInstaller sometimes returns
            # one) we must NOT resolve against CWD — the process CWD when
            # launched from a double-clicked EXE is the EXE directory, not the
            # downloads folder.  Instead, anchor the relative path against
            # task.output_dir so the resolved parent is always the correct
            # downloads folder.
            _raw_p = Path(raw)
            if _raw_p.is_absolute():
                p = _raw_p.resolve()
            else:
                _base = (
                    Path(self.task.output_dir)
                    if self.task.output_dir else Path.cwd()
                )
                p = (_base / raw).resolve()

            if p.is_file():
                # File present — reveal and highlight it synchronously.
                if not reveal_in_explorer(p):
                    open_folder(p.parent)
                return

            # File not yet visible — schedule non-blocking retries.
            # Falls back to parent folder / output_dir after all attempts.
            self._open_folder_retry(p, attempts_left=2)
            return

        # Last resort: the task's configured output directory.
        output_dir = self.task.output_dir
        if output_dir:
            # Resolve here too: output_dir from config may be relative on
            # some portable / PyInstaller setups.
            fb = Path(output_dir).resolve()
            if fb.is_dir():
                open_folder(fb)
                return

        logger.warning(
            "_open_folder: no valid path for task %s "
            "(filename=%r, output_dir=%r)",
            self.task.id, raw, self.task.output_dir,
        )

    def _open_folder_retry(self, p: Path, attempts_left: int) -> None:
        """Retry opening the folder without sleeping on the UI thread.

        Uses ``widget.after()`` to reschedule each check 200 ms later so
        the Tk event loop stays responsive.  When ``after()`` is unavailable
        (e.g. unit tests without a running Tk event loop) the method falls
        through immediately to the parent-folder / output-dir fallback so
        existing synchronous tests continue to work.
        """
        if not self.winfo_exists():
            return
        if p.is_file():
            if not reveal_in_explorer(p):
                open_folder(p.parent)
            return
        _after = getattr(self, "after", None)
        if attempts_left > 0 and callable(_after):
            _after(200, lambda: self._open_folder_retry(p, attempts_left - 1))
            return
        # All retries exhausted (or no event loop available) — open parent dir.
        folder = p.parent
        if folder.is_dir():
            open_folder(folder)
            return
        output_dir = self.task.output_dir
        if output_dir:
            fb = Path(output_dir).resolve()
            if fb.is_dir():
                open_folder(fb)

    # ── Post-download action handlers ─────────────────────────────────────────

    def _on_post_convert(self, file_path: Path, target_ext: str, encode_settings=None) -> None:
        """Bridge PostDownloadActions → existing convert pipeline.

        Forwards target_ext and encode_settings so the user's format and
        encoder choices are respected.  Both are passed as keyword arguments
        to the on_convert callback provided by QueueTab.
        """
        if not self._on_convert:
            self._post_actions.notify_convert_error("Convert chưa được cấu hình.")
            return

        def _on_progress(pct: float) -> None:
            self._ui_queue.put(lambda p=pct: self._prog.set_progress(p))

        def _on_done(output_path: Path) -> None:
            self._converting = False
            # BUG-CA: For multi-file posts, append the converted output to
            # task.gallery_dl_files so a subsequent "Gửi" click zips all
            # converted files instead of sending only the last one.
            # _notify runs on the UI thread via _ui_queue — safe to mutate task.
            def _notify(op=output_path):
                gdl = getattr(self.task, "gallery_dl_files", None)
                # Truthy check: DownloadTask.gallery_dl_files defaults to []
                # for all tasks — only append when gallery-dl actually populated
                # the list (non-empty), otherwise we'd corrupt a yt-dlp task's
                # gallery_dl_files and make send_file_to_nodes zip the directory.
                if gdl and str(op) not in gdl:
                    self.task.gallery_dl_files = gdl + [str(op)]
                self._post_actions.notify_convert_done(op)
            self._ui_queue.put(_notify)

        def _on_error(msg: str) -> None:
            self._converting = False
            self._ui_queue.put(
                lambda m=msg: self._post_actions.notify_convert_error(m)
            )

        self._converting = True
        self._prog.set_progress(0.0)
        self._prog.set_state("active")
        self._on_convert(
            file_path,
            target_ext=target_ext,
            encode_settings=encode_settings,
            on_progress=_on_progress,
            on_done=_on_done,
            on_error=_on_error,
        )

    def _on_post_send(self, file_path: Path, restore_btn: callable, specific_files=None) -> None:
        """Bridge PostDownloadActions → TaildropService.send_file_to_nodes().

        Reads the target node list from config via the on_send callback
        provided at widget construction.  If no on_send callback was given,
        restores the button immediately.  Forwards self.task so TaildropService
        can use gallery_dl_files for multi-file posts.
        specific_files: list[Path] | None — when provided (from file picker),
        takes precedence over task.gallery_dl_files in send_file_to_nodes.
        """
        if not self._on_send:
            restore_btn()
            return
        try:
            self._on_send(file_path, restore_btn, task=self.task, specific_files=specific_files)
        except Exception as exc:
            logger.warning("DownloadItemWidget on_send raised: %s", exc)
            restore_btn()

    def _on_post_delete(self, file_path: Path) -> None:
        """Called by PostDownloadActions after the file has been deleted.

        Hides the widget from the queue list by cancelling the task entry
        (the task is already COMPLETED so cancel is a visual-only removal).
        """
        if self._on_delete:
            try:
                self._on_delete(file_path, self.task.id)
            except Exception as exc:
                logger.warning("DownloadItemWidget on_delete raised: %s", exc)

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        return s[:n] + "…" if s and len(s) > n else (s or "")
