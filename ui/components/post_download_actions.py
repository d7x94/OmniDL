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

• on_convert signature (v17+):
    Callable[[Path, str, Optional[EncodeSettings]], None]
    where EncodeSettings is from app.services.ffmpeg_convert_service.
    The third argument is None for non-custom presets, or a populated
    EncodeSettings object when the user picks the Custom mode.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.themes.tokens import T

if TYPE_CHECKING:
    from app.services.ffmpeg_convert_service import EncodeSettings

logger = logging.getLogger(__name__)

# ── Supported target formats ──────────────────────────────────────────────────
# Each entry: (extension, menu_label)
# "custom" is a sentinel — it triggers the encoder/quality sub-panel.
CONVERT_FORMATS: list[tuple[str, str]] = [
    ("mp4",    "MP4 — H.264 / AAC (iPhone, Android)"),
    ("mp3",    "MP3 — Audio only"),
    ("mkv",    "MKV — Lossless container"),
    ("avi",    "AVI — Legacy compatibility"),
    ("custom", "⚙  Tuỳ chỉnh (chất lượng + encoder)…"),
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
        ``Callable[[Path, str, Optional[EncodeSettings]], None]`` —
        called with (file_path, target_ext, encode_settings) when the user
        confirms a format.  encode_settings is None unless the user chose
        the Custom preset, in which case it holds their encoder/quality picks.
        Runs on the UI thread.
    on_send:
        ``Callable[[Path, Callable], None]`` — called with
        (file_path, restore_fn).  The caller reads the target nodes from
        config and handles the Taildrop send.  Runs on the UI thread.
    on_delete:
        ``Callable[[Path], None]`` — called *after* the file has been deleted
        from disk.  Runs on the UI thread.
    compact:
        When True, use shorter button labels for narrow rows (DownloadItemWidget).
    """

    def __init__(
        self,
        master,
        *,
        on_convert: Optional[Callable] = None,
        on_send:    Optional[Callable] = None,
        on_delete:  Optional[Callable] = None,
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
            text="📲 Gửi",
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
            text="🗑 Xoá",
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

        # ── Status label ──────────────────────────────────────────────────
        self._status_lbl = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
        )

        # ── Format picker (hidden by default) ─────────────────────────────
        # Kept as a child of self so pack() correctly stacks it below the
        # button row inside PostDownloadActions.  Do NOT use in_=self.master;
        # Tkinter only allows in_= with an ancestor of the widget.
        self._format_frame = ctk.CTkFrame(
            self, fg_color=T.surface2, corner_radius=8,
            border_width=1, border_color=T.border,
        )
        # Not packed yet — shown on demand by _show_format_picker().

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
                command=self._on_format_radio_change,
            ).pack(anchor="w", padx=14, pady=2)

        # ── Custom encoder sub-panel (shown only when "custom" selected) ──
        self._custom_frame = ctk.CTkFrame(
            self._format_frame, fg_color=T.surface3, corner_radius=6,
        )
        # Not packed yet — toggled by _on_format_radio_change().

        self._build_custom_panel()

        # ── Confirm / Cancel row ──────────────────────────────────────────
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

    def _build_custom_panel(self) -> None:
        """Build the encoder / quality widgets inside _custom_frame."""
        from app.services.ffmpeg_convert_service import (
            SPEED_OPTIONS,
            get_available_encoder_options,
        )

        ctk.CTkLabel(
            self._custom_frame,
            text="Encoder:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=T.text2,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(10, 6), pady=(8, 2))

        available_encoders = get_available_encoder_options()
        encoder_labels = [label for _, label in available_encoders]
        encoder_keys   = [key   for key, _   in available_encoders]

        self._encoder_keys = encoder_keys
        self._encoder_var  = ctk.StringVar(value=encoder_labels[0] if encoder_labels else "")

        self._encoder_menu = ctk.CTkOptionMenu(
            self._custom_frame,
            values=encoder_labels,
            variable=self._encoder_var,
            width=200, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=T.surface2,
            button_color=T.primary_dim,
            button_hover_color=T.primary,
            text_color=T.text,
        )
        self._encoder_menu.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(8, 2))

        ctk.CTkLabel(
            self._custom_frame,
            text="Chất lượng:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=T.text2,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=(10, 6), pady=2)

        quality_labels = ["Cao (CRF 18)", "Chuẩn (CRF 23)", "Nhỏ 720p (CRF 28)", "CRF tuỳ chỉnh"]
        quality_keys   = ["high",         "standard",        "small",              "custom"]

        self._quality_keys  = quality_keys
        self._quality_var   = ctk.StringVar(value=quality_labels[1])

        self._quality_menu = ctk.CTkOptionMenu(
            self._custom_frame,
            values=quality_labels,
            variable=self._quality_var,
            width=200, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=T.surface2,
            button_color=T.primary_dim,
            button_hover_color=T.primary,
            text_color=T.text,
            command=self._on_quality_change,
        )
        self._quality_menu.grid(row=1, column=1, sticky="w", padx=(0, 10), pady=2)

        # CRF slider (shown only when "CRF tuỳ chỉnh" selected)
        self._crf_row = ctk.CTkFrame(self._custom_frame, fg_color="transparent")
        self._crf_row.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=2)

        ctk.CTkLabel(
            self._crf_row, text="CRF:",
            font=ctk.CTkFont(size=11), text_color=T.text2,
        ).pack(side="left")

        self._crf_val_lbl = ctk.CTkLabel(
            self._crf_row, text="23",
            font=ctk.CTkFont(size=11, weight="bold"), text_color=T.primary_text, width=30,
        )
        self._crf_val_lbl.pack(side="right")

        self._crf_slider = ctk.CTkSlider(
            self._crf_row,
            from_=0, to=51, number_of_steps=51,
            width=160,
            fg_color=T.surface2, progress_color=T.primary, button_color=T.primary,
            command=self._on_crf_slide,
        )
        self._crf_slider.set(23)
        self._crf_slider.pack(side="left", padx=(6, 6), fill="x", expand=True)
        self._crf_row.grid_remove()   # hidden until "CRF tuỳ chỉnh" selected

        ctk.CTkLabel(
            self._custom_frame,
            text="Tốc độ encode:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=T.text2,
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=(10, 6), pady=(2, 8))

        speed_labels = [label for _, label in SPEED_OPTIONS]
        speed_keys   = [key   for key, _   in SPEED_OPTIONS]

        self._speed_keys = speed_keys
        self._speed_var  = ctk.StringVar(value=speed_labels[1])

        ctk.CTkOptionMenu(
            self._custom_frame,
            values=speed_labels,
            variable=self._speed_var,
            width=200, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=T.surface2,
            button_color=T.primary_dim,
            button_hover_color=T.primary,
            text_color=T.text,
        ).grid(row=3, column=1, sticky="w", padx=(0, 10), pady=(2, 8))

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self, file_path: Path) -> None:
        """Bind the action bar to *file_path* and reveal it."""
        self._file_path  = file_path
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
        """Hide the action bar."""
        self._file_path = None
        self._hide_format_picker()
        if self.winfo_ismapped():
            self.pack_forget()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def destroy(self) -> None:
        """Destroy self; _format_frame is a child of self and destroyed automatically."""
        super().destroy()

    # ── Internal — format radio / custom panel ────────────────────────────────

    def _on_format_radio_change(self) -> None:
        """Show or hide the Custom encoder sub-panel based on selection."""
        if self._format_var.get() == "custom":
            if not self._custom_frame.winfo_ismapped():
                self._custom_frame.pack(
                    fill="x", padx=10, pady=(0, 6), before=self._confirm_convert_btn.master,
                )
        else:
            if self._custom_frame.winfo_ismapped():
                self._custom_frame.pack_forget()

    def _on_quality_change(self, label: str) -> None:
        """Show/hide CRF slider depending on quality selection."""
        values = self._quality_menu.cget("values")
        idx = values.index(label) if label in values else -1
        if idx < 0:
            return
        if self._quality_keys[idx] == "custom":
            self._crf_row.grid()
        else:
            self._crf_row.grid_remove()

    def _on_crf_slide(self, val: float) -> None:
        self._crf_val_lbl.configure(text=str(int(val)))

    def _get_encode_settings(self) -> "Optional[EncodeSettings]":
        """Return an EncodeSettings object when Custom is selected, else None."""
        if self._format_var.get() != "custom":
            return None
        from app.services.ffmpeg_convert_service import EncodeSettings

        # Resolve encoder key from selected label
        try:
            enc_label = self._encoder_var.get()
            enc_key   = self._encoder_keys[
                list(self._encoder_menu.cget("values")).index(enc_label)
            ]
        except (ValueError, IndexError):
            enc_key = "cpu"

        # Resolve quality key
        try:
            q_label = self._quality_var.get()
            q_key   = self._quality_keys[
                list(self._quality_menu.cget("values")).index(q_label)
            ]
        except (ValueError, IndexError):
            q_key = "standard"

        # Resolve speed key
        try:
            from app.services.ffmpeg_convert_service import SPEED_OPTIONS
            s_label = self._speed_var.get()
            s_key   = next(
                (k for k, lbl in SPEED_OPTIONS if lbl == s_label),
                "balanced",
            )
        except Exception:
            s_key = "balanced"

        crf_val = int(self._crf_slider.get()) if q_key == "custom" else 23

        return EncodeSettings(
            encoder_key=enc_key,
            quality=q_key,
            speed_preset=s_key,
            custom_quality=crf_val,
        )

    # ── Internal — button handlers ────────────────────────────────────────────

    def _on_convert_click(self) -> None:
        if self._converting or not self._file_path:
            return
        if self._format_frame.winfo_ismapped():
            self._hide_format_picker()
        else:
            self._show_format_picker()

    def _show_format_picker(self) -> None:
        """Pack _format_frame below the button row inside PostDownloadActions.

        _format_frame is a child of self, so we simply pack() it.  The frame
        uses fill="x" to span the full width of the parent container.
        No in_= argument is used — that caused TclError: can't pack X inside Y.
        """
        if not self._format_frame.winfo_ismapped():
            self._format_frame.pack(
                anchor="w", padx=0, pady=(6, 0), fill="x",
            )

    def _hide_format_picker(self) -> None:
        if self._custom_frame.winfo_ismapped():
            self._custom_frame.pack_forget()
        if self._format_frame.winfo_ismapped():
            self._format_frame.pack_forget()

    def _confirm_convert(self) -> None:
        """User confirmed — resolve target_ext + optional EncodeSettings and call on_convert."""
        fmt_key = self._format_var.get()
        if not self._file_path or not fmt_key:
            return

        # "custom" maps to mp4 output but with user-specified EncodeSettings
        target_ext     = "mp4" if fmt_key == "custom" else fmt_key
        encode_settings = self._get_encode_settings()   # None unless custom

        self._hide_format_picker()
        self._converting = True
        display_ext = fmt_key if fmt_key != "custom" else "mp4 (custom)"
        self._convert_btn.configure(
            state="disabled",
            text=(f"Converting → .{display_ext}…" if not self._compact else "Converting…"),
        )
        if self._on_convert:
            try:
                self._on_convert(self._file_path, target_ext, encode_settings)
            except Exception as exc:
                logger.warning("PostDownloadActions on_convert raised: %s", exc)
                self._set_status(f"Lỗi convert: {exc}")
                self._converting = False
                self._convert_btn.configure(
                    state="normal",
                    text="🔄 Convert" if not self._compact else "🔄 Conv",
                )

    def _on_send_click(self) -> None:
        if not self._file_path:
            return
        self._send_btn.configure(state="disabled", text="📲 Đang gửi…")

        def _restore() -> None:
            if self.winfo_exists():
                self._send_btn.configure(state="normal", text="📲 Gửi")

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
        if not self._file_path:
            return
        path = self._file_path

        dialog = _ConfirmDeleteDialog(self, filename=path.name)
        self.wait_window(dialog)
        if not dialog.confirmed:
            return

        try:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                logger.info("PostDownloadActions: deleted '%s'", path)
            else:
                logger.warning("PostDownloadActions: file already gone: '%s'", path)
        except OSError as exc:
            logger.error("PostDownloadActions: cannot delete '%s': %s", path, exc)
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

    def notify_convert_done(self, output_path: Path) -> None:
        """Caller invokes this after a successful conversion (on UI thread)."""
        self._converting = False
        if not self.winfo_exists():
            return
        self._convert_btn.configure(text="✓ Done", state="disabled")
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
        self._delete_btn.configure(fg_color=T.error_bg, text_color=T.error)
        self._status_lbl.configure(text_color=T.text3)
        self._format_frame.configure(fg_color=T.surface2, border_color=T.border)


# ── Confirmation dialog ───────────────────────────────────────────────────────

class _ConfirmDeleteDialog(_BaseToplevel):  # type: ignore[misc]
    """Minimal modal confirmation dialog for file deletion."""

    def __init__(self, parent, *, filename: str) -> None:
        if ctk is None:  # pragma: no cover
            self.confirmed = False
            return
        super().__init__(parent)
        self.confirmed = False
        self.title("Xác nhận xoá file")
        self.resizable(False, False)
        self.grab_set()
        self.lift()
        self.focus_force()

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

        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{px}+{py}")

    def _confirm(self) -> None:
        self.confirmed = True
        self.destroy()
