"""
ui/components/post_download_actions.py
Post-download action bar: Convert · Send to device(s) · Delete.

Shown after a file has been successfully downloaded in:
  • QueueTab  → embedded inside DownloadItemWidget
  • SpecialDlTab → embedded inside the progress card

Design constraints
──────────────────
• Zero coupling to DownloadService / TaildropService / FfmpegConvertService.
  All side-effects are delivered through plain callable callbacks injected at
  construction time.  This makes the component fully unit-testable without a
  running Tk event loop or real services.

• Thread-safe: every background result is posted to _ui_queue (50 ms drain),
  never via self.after() on a worker thread — required for Python 3.14.

• Security:
    - Delete always shows a confirmation dialog before touching the filesystem.
    - Node validation is delegated to TaildropService.send_file_to_nodes()
      which runs its own _NODE_RE allowlist check.

• Stability:
    - Callbacks are wrapped in try/except so a misbehaving caller cannot crash
      the UI.
    - All widget-exists guards use winfo_exists() before configure/pack calls.
"""
from __future__ import annotations

import logging
import os
import queue
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.themes.tokens import T

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Supported target formats ──────────────────────────────────────────────────
# Each entry: (extension, menu_label)
# The first entry is the "recommended" format shown at the top of the picker.
CONVERT_FORMATS: list[tuple[str, str]] = [
    ("mp4",  "MP4 — H.264 / AAC (iPhone, Android)"),
    ("mp3",  "MP3 — Audio only"),
    ("mkv",  "MKV — Lossless container"),
    ("avi",  "AVI — Legacy compatibility"),
]

_BaseFrame    = ctk.CTkFrame    if ctk is not None else object
_BaseToplevel = ctk.CTkToplevel if ctk is not None else object


class PostDownloadActions(_BaseFrame):  # type: ignore[misc]
    """Three-button action bar shown after a successful download.

    Parameters
    ----------
    master:
        Parent Tkinter widget.
    on_convert:
        ``Callable[[Path, str], None]`` — called with (file_path, target_ext)
        when the user confirms a format conversion.  Runs on the UI thread.
    on_send:
        ``Callable[[Path, list[str]], None]`` — called with
        (file_path, node_names) when the user clicks "Send".  The caller is
        responsible for reading the target nodes from config and passing them
        in.  Runs on the UI thread.
    on_delete:
        ``Callable[[Path], None]`` — called *after* the file has been deleted
        from disk (deletion is performed here before the callback so the
        caller does not need to handle filesystem errors).  Runs on the UI
        thread.
    compact:
        When True, use shorter button labels suitable for narrow rows (e.g.
        inside DownloadItemWidget).  Default False.
    """

    def __init__(
        self,
        master,
        *,
        on_convert: Optional[Callable[[Path, str], None]] = None,
        on_send:    Optional[Callable[[Path, list], None]] = None,
        on_delete:  Optional[Callable[[Path], None]] = None,
        compact: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)

        self._on_convert = on_convert
        self._on_send    = on_send
        self._on_delete  = on_delete
        self._compact    = compact

        self._file_path: Optional[Path] = None
        self._converting = False

        # Thread-safe UI queue — worker threads post callables here.
        self._ui_queue: queue.Queue = queue.Queue()

        self._build()
        self._drain_ui_queue()
        T.register(self._on_theme)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Button 1: Convert ─────────────────────────────────────────────
        self._convert_btn = ctk.CTkButton(
            self,
            text="🔄 Convert" if not self._compact else "🔄 Conv",
            width=90 if not self._compact else 72,
            height=28,
            corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=T.primary_dim,
            hover_color=T.surface3,
            text_color=T.primary_text,
            command=self._on_convert_click,
        )
        self._convert_btn.pack(side="left", padx=(0, 4))

        # ── Button 2: Send to device ──────────────────────────────────────
        self._send_btn = ctk.CTkButton(
            self,
            text="📲 Gửi" if not self._compact else "📲 Gửi",
            width=72 if not self._compact else 68,
            height=28,
            corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=T.surface2,
            hover_color=T.surface3,
            text_color=T.text2,
            command=self._on_send_click,
        )
        self._send_btn.pack(side="left", padx=(0, 4))

        # ── Button 3: Delete ──────────────────────────────────────────────
        self._delete_btn = ctk.CTkButton(
            self,
            text="🗑 Xoá" if not self._compact else "🗑 Xoá",
            width=72 if not self._compact else 68,
            height=28,
            corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=T.error_bg,
            hover_color=T.error_bg,
            text_color=T.error,
            command=self._on_delete_click,
        )
        self._delete_btn.pack(side="left")

        # ── Format picker (hidden by default) ─────────────────────────────
        # Created as a child of self.master (not self) so it can be packed
        # into self.master's layout below the PostDownloadActions button row.
        # Creating it as a child of self would make pack(in_=self.master)
        # invalid — Tkinter only allows in_= for ancestors of the widget.
        self._format_frame = ctk.CTkFrame(
            self.master, fg_color=T.surface2, corner_radius=8,
            border_width=1, border_color=T.border,
        )

        ctk.CTkLabel(
            self._format_frame,
            text="Chọn định dạng đích:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=T.text2,
            anchor="w",
        ).pack(anchor="w", padx=10, pady=(8, 4))

        self._format_var = ctk.StringVar(value=CONVERT_FORMATS[0][0])
        for ext, label in CONVERT_FORMATS:
            ctk.CTkRadioButton(
                self._format_frame,
                text=label,
                variable=self._format_var,
                value=ext,
                font=ctk.CTkFont(size=11),
                text_color=T.text,
                fg_color=T.primary,
                hover_color=T.primary_hover,
            ).pack(anchor="w", padx=14, pady=2)

        btn_row = ctk.CTkFrame(self._format_frame, fg_color="transparent")
        btn_row.pack(anchor="e", padx=10, pady=(4, 10))

        ctk.CTkButton(
            btn_row, text="✕  Huỷ",
            width=64, height=26, corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text3,
            command=self._hide_format_picker,
        ).pack(side="left", padx=(0, 6))

        self._confirm_convert_btn = ctk.CTkButton(
            btn_row, text="✓  Convert",
            width=80, height=26, corner_radius=6,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=T.primary, hover_color=T.primary_hover,
            text_color=T.text_inv,
            command=self._confirm_convert,
        )
        self._confirm_convert_btn.pack(side="left")

        # ── Status label (converting / sending feedback) ───────────────────
        self._status_lbl = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
        )
        # Packed only when there is text to show.

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def destroy(self) -> None:
        """Destroy self and also clean up _format_frame (lives in self.master)."""
        try:
            if hasattr(self, "_format_frame") and self._format_frame.winfo_exists():
                self._format_frame.destroy()
        except Exception:
            pass
        super().destroy()

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self, file_path: Path) -> None:
        """Bind the action bar to *file_path* and reveal it in the parent layout.

        Must be called on the UI thread.
        """
        self._file_path = file_path
        self._converting = False
        self._set_status("")
        self._convert_btn.configure(
            state="normal",
            text="🔄 Convert" if not self._compact else "🔄 Conv",
        )
        self._send_btn.configure(state="normal")
        self._delete_btn.configure(state="normal")
        if not self.winfo_ismapped():
            self.pack(side="left")

    def hide(self) -> None:
        """Hide the action bar (e.g. when the widget is recycled)."""
        self._file_path = None
        self._hide_format_picker()
        if self.winfo_ismapped():
            self.pack_forget()

    # ── Internal — button handlers ────────────────────────────────────────────

    def _on_convert_click(self) -> None:
        """Show the format picker popup inline."""
        if self._converting or not self._file_path:
            return
        if self._format_frame.winfo_ismapped():
            self._hide_format_picker()
        else:
            self._show_format_picker()

    def _show_format_picker(self) -> None:
        # Pack _format_frame into self.master (its actual parent) below the
        # PostDownloadActions row.  No in_= needed — the widget already belongs
        # to self.master.  pack() appends it after all currently-packed children,
        # which places it just below the action buttons.
        if not self._format_frame.winfo_ismapped():
            self._format_frame.pack(
                anchor="w", padx=0, pady=(6, 0),
                fill="x",
            )

    def _hide_format_picker(self) -> None:
        if self._format_frame.winfo_ismapped():
            self._format_frame.pack_forget()

    def _confirm_convert(self) -> None:
        """User confirmed format selection — kick off conversion."""
        target_ext = self._format_var.get()
        if not self._file_path or not target_ext:
            return
        self._hide_format_picker()
        self._converting = True
        self._convert_btn.configure(
            state="disabled",
            text=f"Converting → .{target_ext}…" if not self._compact else "Converting…",
        )
        if self._on_convert:
            try:
                self._on_convert(self._file_path, target_ext)
            except Exception as exc:
                logger.warning("PostDownloadActions on_convert raised: %s", exc)
                self._set_status(f"Lỗi convert: {exc}")
                self._converting = False
                self._convert_btn.configure(state="normal", text="🔄 Convert")

    def _on_send_click(self) -> None:
        """Delegate to caller — caller reads node list from config."""
        if not self._file_path:
            return
        self._send_btn.configure(state="disabled", text="📲 Đang gửi…")

        def _restore() -> None:
            if self.winfo_exists():
                self._send_btn.configure(
                    state="normal",
                    text="📲 Gửi" if not self._compact else "📲 Gửi",
                )

        if self._on_send:
            try:
                self._on_send(self._file_path, _restore)
            except Exception as exc:
                logger.warning("PostDownloadActions on_send raised: %s", exc)
                _restore()
                self._set_status(f"Lỗi gửi: {exc}")
        else:
            _restore()

    def _on_delete_click(self) -> None:
        """Ask for confirmation, then delete the file and call on_delete."""
        if not self._file_path:
            return

        path = self._file_path  # local snapshot

        # ── Confirmation dialog ───────────────────────────────────────────
        dialog = _ConfirmDeleteDialog(self, filename=path.name)
        self.wait_window(dialog)
        if not dialog.confirmed:
            return

        # ── Delete ────────────────────────────────────────────────────────
        try:
            if path.exists():
                os.remove(path)
                logger.info("PostDownloadActions: deleted '%s'", path)
            else:
                logger.warning(
                    "PostDownloadActions: file already gone: '%s'", path
                )
        except OSError as exc:
            logger.error(
                "PostDownloadActions: cannot delete '%s': %s", path, exc
            )
            self._set_status(f"Không xoá được: {exc.strerror}")
            return

        self._file_path = None
        self.hide()

        if self._on_delete:
            try:
                self._on_delete(path)
            except Exception as exc:
                logger.warning("PostDownloadActions on_delete raised: %s", exc)

    # ── Feedback helpers ──────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        if not self.winfo_exists():
            return
        self._status_lbl.configure(text=text)
        if text and not self._status_lbl.winfo_ismapped():
            self._status_lbl.pack(side="left", padx=(6, 0))
        elif not text and self._status_lbl.winfo_ismapped():
            self._status_lbl.pack_forget()

    # ── Convert outcome helpers (called from caller's callbacks via ui_queue) ─

    def notify_convert_done(self, output_path: Path) -> None:
        """Caller invokes this after a successful conversion (on UI thread)."""
        self._converting = False
        if not self.winfo_exists():
            return
        self._convert_btn.configure(text="✓ Done", state="disabled")
        # Update internal path so subsequent Send/Delete targets the new file.
        self._file_path = output_path

    def notify_convert_error(self, msg: str) -> None:
        """Caller invokes this after a failed conversion (on UI thread)."""
        self._converting = False
        if not self.winfo_exists():
            return
        self._convert_btn.configure(
            state="normal",
            text="🔄 Convert" if not self._compact else "🔄 Conv",
        )
        self._set_status(f"Convert thất bại: {msg[:80]}")

    # ── UI queue drain ────────────────────────────────────────────────────────

    def _drain_ui_queue(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.warning("PostDownloadActions _ui_queue raised: %s", exc)
        except queue.Empty:
            pass
        self.after(50, self._drain_ui_queue)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self._convert_btn.configure(
            fg_color=T.primary_dim, hover_color=T.surface3, text_color=T.primary_text
        )
        self._send_btn.configure(
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2
        )
        self._delete_btn.configure(
            fg_color=T.error_bg, text_color=T.error
        )
        self._status_lbl.configure(text_color=T.text3)
        self._format_frame.configure(fg_color=T.surface2, border_color=T.border)


# ── Confirmation dialog ───────────────────────────────────────────────────────

class _ConfirmDeleteDialog(_BaseToplevel):  # type: ignore[misc]
    """Minimal modal confirmation dialog for file deletion.

    Attributes
    ----------
    confirmed : bool
        True if the user clicked "Xoá" / "Delete".
    """

    def __init__(self, parent, *, filename: str) -> None:
        # Guard: when ctk is None (headless test env), _BaseToplevel is
        # `object` and __init__ must not attempt Tk widget construction.
        if ctk is None:  # pragma: no cover
            self.confirmed = False
            return
        super().__init__(parent)
        self.confirmed = False
        self.title("Xác nhận xoá file")
        self.resizable(False, False)
        self.grab_set()          # Modal
        self.lift()
        self.focus_force()

        # ── Layout ────────────────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text="⚠  Bạn có chắc muốn xoá file này?",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=T.warning_text,
        ).pack(padx=24, pady=(20, 6))

        ctk.CTkLabel(
            self,
            text=f"{filename[:80]}{'…' if len(filename) > 80 else ''}",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
            wraplength=400,
        ).pack(padx=24, pady=(0, 16))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(0, 20))

        ctk.CTkButton(
            btn_row, text="Huỷ",
            width=90, height=32, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2,
            command=self.destroy,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text="🗑  Xoá",
            width=90, height=32, corner_radius=8,
            fg_color=T.error_bg, hover_color=T.error_bg,
            text_color=T.error,
            font=ctk.CTkFont(weight="bold"),
            command=self._confirm,
        ).pack(side="left")

        # Centre over parent
        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{px}+{py}")

    def _confirm(self) -> None:
        self.confirmed = True
        self.destroy()
