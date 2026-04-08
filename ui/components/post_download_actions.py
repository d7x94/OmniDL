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
        # gallery_dl_files: set by show() for multi-file posts (carousel/gallery).
        # None means single-file (yt-dlp); list means multi-file (gallery-dl).
        self._gallery_dl_files: Optional[list] = None

        # Thread-safe UI queue — worker threads post callables here.
        self._ui_queue: queue.Queue = queue.Queue()

        self._build()
        self._drain_ui_queue()
        T.register(self._on_theme)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── Inner frame for button row ─────────────────────────────────────
        # Buttons are children of _btn_row (packed side="left" inside it).
        # _btn_row itself is packed side="top" (default) inside self, so
        # _format_frame — also a child of self — packs BELOW the buttons
        # instead of to the right of them.
        _btn_row = ctk.CTkFrame(self, fg_color="transparent")
        _btn_row.pack(fill="x")

        # ── Button 1: Convert ─────────────────────────────────────────────
        self._convert_btn = ctk.CTkButton(
            _btn_row,
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
            _btn_row,
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
            _btn_row,
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
            _btn_row, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
        )

        # ── Format picker (hidden by default) ─────────────────────────────
        # Child of self (not _btn_row) so it packs below _btn_row when shown.
        # Do NOT use in_=self.master; Tkinter only allows in_= with an ancestor.
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

    def show(self, file_path: Path, gallery_dl_files: Optional[list] = None) -> None:
        """Bind the action bar to *file_path* and reveal it.

        gallery_dl_files: list of individual file paths for multi-file posts
        (Instagram carousel/gallery).  When set:
          - Convert operates on each video file individually.
          - Delete removes all files in the list (with confirmation).
          - Send uses gallery_dl_files via the task object (forwarded by caller).
        When None, the post is single-file and Convert/Delete/Send work on file_path.
        """
        self._file_path  = file_path
        self._gallery_dl_files = gallery_dl_files
        self._converting = False
        self._set_status("")

        # For multi-file posts: check if there are any videos to convert.
        # If only images, disable Convert since FFmpeg can't convert images.
        _is_multifile = gallery_dl_files is not None and len(gallery_dl_files) > 0
        if _is_multifile:
            _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
            _has_video = any(
                Path(f).suffix.lower() in _vid_exts
                for f in gallery_dl_files
            )
            if _has_video:
                self._convert_btn.configure(
                    state="normal",
                    text="🔄 Conv video" if not self._compact else "🔄 Conv",
                )
            else:
                # Images only — convert not applicable
                self._convert_btn.configure(
                    state="disabled",
                    text="🔄 Conv" if not self._compact else "🔄 Conv",
                )
        else:
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
        self._gallery_dl_files = None
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
        """User confirmed — resolve target_ext + optional EncodeSettings and call on_convert.

        For multi-file posts (gallery_dl_files set): converts each video file
        individually.  Images in the list are skipped silently.
        """
        fmt_key = self._format_var.get()
        if not self._file_path or not fmt_key:
            return

        # "custom" maps to mp4 output but with user-specified EncodeSettings
        target_ext      = "mp4" if fmt_key == "custom" else fmt_key
        encode_settings = self._get_encode_settings()   # None unless custom

        self._hide_format_picker()
        self._converting = True
        display_ext = fmt_key if fmt_key != "custom" else "mp4 (custom)"
        self._convert_btn.configure(
            state="disabled",
            text=(f"Converting → .{display_ext}…" if not self._compact else "Converting…"),
        )

        if not self._on_convert:
            self._converting = False
            self._convert_btn.configure(
                state="normal",
                text="🔄 Convert" if not self._compact else "🔄 Conv",
            )
            return

        # Multi-file post: convert each video file; skip images.
        _vid_exts = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
        gdl = self._gallery_dl_files
        if gdl:
            video_files = [
                Path(f) for f in gdl
                if Path(f).suffix.lower() in _vid_exts and Path(f).is_file()
            ]
            if not video_files:
                self._converting = False
                self._set_status("Không có video để convert.")
                self._convert_btn.configure(
                    state="disabled",
                    text="🔄 Conv" if not self._compact else "🔄 Conv",
                )
                return
            # Convert all videos sequentially via the callback.
            # The caller (_on_post_convert in DownloadItemWidget) handles
            # progress/done/error notifications per file.
            for vf in video_files:
                try:
                    self._on_convert(vf, target_ext, encode_settings)
                except Exception as exc:
                    logger.warning("PostDownloadActions on_convert raised for %s: %s", vf.name, exc)
                    self._set_status(f"Lỗi convert {vf.name}: {exc}")
                    self._converting = False
                    self._convert_btn.configure(
                        state="normal",
                        text="🔄 Conv video" if not self._compact else "🔄 Conv",
                    )
                    return
        else:
            # Single file
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
        path = self._file_path

        # When path is a directory but no specific files are known (gallery_dl_files
        # is falsy), show a picker so the user can choose which file to send.
        # This prevents zipping the entire download folder unintentionally.
        specific_files = None
        if path.is_dir() and not self._gallery_dl_files:
            files = sorted(f for f in path.iterdir() if f.is_file())
            if not files:
                self._set_status("Thư mục rỗng, không có file nào để gửi.")
                return
            if len(files) == 1:
                specific_files = files  # auto-select the only file
            else:
                picker = _FolderFilePickerDialog(
                    self, directory=path,
                    title="Chọn file cần gửi",
                    confirm_text="📲  Gửi đã chọn",
                )
                self.wait_window(picker)
                if not picker.confirmed or not picker.selected:
                    return
                specific_files = picker.selected

        self._send_btn.configure(state="disabled", text="📲 Đang gửi…")

        def _restore() -> None:
            if self.winfo_exists():
                self._send_btn.configure(state="normal", text="📲 Gửi")

        if self._on_send:
            try:
                if specific_files is not None:
                    self._on_send(path, _restore, specific_files=specific_files)
                else:
                    self._on_send(path, _restore)
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
        gdl = self._gallery_dl_files

        if gdl:
            # ── Multi-file gallery-dl post ────────────────────────────────
            # Delete each individual file; never delete the parent directory
            # unconditionally — it may be shared with other posts.
            dialog = _ConfirmDeleteDialog(self, filename=path.name)
            self.wait_window(dialog)
            if not dialog.confirmed:
                return
            errors = []
            for f_str in gdl:
                fp = Path(f_str)
                try:
                    if fp.exists():
                        os.remove(fp)
                        logger.info("PostDownloadActions: deleted '%s'", fp)
                    else:
                        logger.warning("PostDownloadActions: file already gone: '%s'", fp)
                except OSError as exc:
                    logger.error("PostDownloadActions: cannot delete '%s': %s", fp, exc)
                    errors.append(fp.name)
            if errors:
                self._set_status(f"Không xoá được: {', '.join(errors[:3])}")
                return
            # Remove parent directory only if it is now completely empty.
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
                    logger.info("PostDownloadActions: removed empty dir '%s'", path)
            except OSError:
                pass

        elif path.is_dir():
            # ── Unexpected directory (should not normally happen after the
            # root-cause fix, but kept as a safety net) ────────────────────
            # Never call shutil.rmtree here — it would delete the entire
            # download folder.  Instead show a file picker so the user
            # explicitly selects which files to delete.
            files = sorted(f for f in path.iterdir() if f.is_file())
            if not files:
                # Already empty — safe to remove
                dialog = _ConfirmDeleteDialog(self, filename=path.name)
                self.wait_window(dialog)
                if not dialog.confirmed:
                    return
                try:
                    path.rmdir()
                    logger.info("PostDownloadActions: removed empty dir '%s'", path)
                except OSError as exc:
                    logger.error("PostDownloadActions: cannot rmdir '%s': %s", path, exc)
                    self._set_status(f"Không xoá được: {exc.strerror}")
                    return
            else:
                picker = _FolderFilePickerDialog(
                    self, directory=path,
                    title="Chọn file cần xoá",
                    confirm_text="🗑  Xoá đã chọn",
                )
                self.wait_window(picker)
                if not picker.confirmed or not picker.selected:
                    return
                errors = []
                for fp in picker.selected:
                    try:
                        if fp.exists():
                            os.remove(fp)
                            logger.info("PostDownloadActions: deleted '%s'", fp)
                    except OSError as exc:
                        logger.error("PostDownloadActions: cannot delete '%s': %s", fp, exc)
                        errors.append(fp.name)
                if errors:
                    self._set_status(f"Không xoá được: {', '.join(errors[:3])}")
                    return
                # Remove directory only if now empty
                try:
                    if not any(path.iterdir()):
                        path.rmdir()
                        logger.info("PostDownloadActions: removed empty dir '%s'", path)
                except OSError:
                    pass

        else:
            # ── Normal single file ────────────────────────────────────────
            dialog = _ConfirmDeleteDialog(self, filename=path.name)
            self.wait_window(dialog)
            if not dialog.confirmed:
                return
            try:
                if path.exists():
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
        # BUG-CA: For multi-file posts, keep _file_path pointing to the
        # parent directory so "Gửi" triggers _do_send's zip branch, which
        # respects specific_files built from task.gallery_dl_files (updated
        # by DownloadItemWidget._on_post_convert to include converted outputs).
        # NOTE: Use truthy check (not `is not None`) — DownloadTask.gallery_dl_files
        # defaults to [] for all tasks including yt-dlp, so an empty list must
        # NOT trigger the parent-dir assignment (which would point _file_path
        # at the download folder and cause shutil.rmtree on Delete).
        if self._gallery_dl_files:
            self._file_path = output_path.parent
        else:
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


# ── Folder file picker dialog ─────────────────────────────────────────────────

class _FolderFilePickerDialog(_BaseToplevel):  # type: ignore[misc]
    """Modal dialog to select specific files from a directory.

    Used by Send and Delete when the target path is a directory and the app
    cannot determine which file(s) to act on automatically.

    After ``wait_window()``:
      • ``confirmed`` — True if user clicked the confirm button with >= 1 file.
      • ``selected``  — list[Path] of checked files.
    """

    def __init__(
        self, parent, *,
        directory: Path,
        title: str,
        confirm_text: str,
    ) -> None:
        if ctk is None:  # pragma: no cover
            self.confirmed = False
            self.selected = []
            return
        super().__init__(parent)
        self.confirmed = False
        self.selected: list[Path] = []
        self._check_vars: dict[Path, "ctk.BooleanVar"] = {}

        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.lift()
        self.focus_force()

        ctk.CTkLabel(
            self,
            text=f"📁  {directory.name}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=T.text,
        ).pack(padx=24, pady=(20, 4))

        ctk.CTkLabel(
            self,
            text="Chọn file cần thực hiện:",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        ).pack(padx=24, pady=(0, 8))

        # Scrollable file list
        scroll = ctk.CTkScrollableFrame(
            self, width=460, height=180,
            fg_color=T.surface2, corner_radius=8,
        )
        scroll.pack(padx=24, fill="x")

        files = sorted(f for f in directory.iterdir() if f.is_file())
        for fp in files:
            var = ctk.BooleanVar(value=True)
            self._check_vars[fp] = var
            try:
                size_kb = fp.stat().st_size // 1024
                size_str = f"{size_kb} KB" if size_kb < 1024 else f"{size_kb // 1024} MB"
            except OSError:
                size_str = "?"
            label = f"{fp.name}  ({size_str})"
            ctk.CTkCheckBox(
                scroll,
                text=label[:80] + ("…" if len(label) > 80 else ""),
                variable=var,
                font=ctk.CTkFont(size=11),
                text_color=T.text,
                fg_color=T.primary,
                hover_color=T.primary_hover,
                checkmark_color=T.text_inv,
            ).pack(anchor="w", padx=8, pady=3)

        # Select-all / deselect-all helpers
        sel_row = ctk.CTkFrame(self, fg_color="transparent")
        sel_row.pack(padx=24, pady=(8, 0), fill="x")

        ctk.CTkButton(
            sel_row, text="Chọn tất cả",
            width=100, height=24, corner_radius=6,
            font=ctk.CTkFont(size=10),
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2,
            command=lambda: [v.set(True) for v in self._check_vars.values()],
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            sel_row, text="Bỏ chọn tất cả",
            width=110, height=24, corner_radius=6,
            font=ctk.CTkFont(size=10),
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2,
            command=lambda: [v.set(False) for v in self._check_vars.values()],
        ).pack(side="left")

        # Confirm / Cancel
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(12, 20))

        ctk.CTkButton(
            btn_row, text="Huỷ",
            width=90, height=32, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2,
            command=self.destroy,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            btn_row, text=confirm_text,
            width=130, height=32, corner_radius=8,
            fg_color=T.primary, hover_color=T.primary_hover,
            text_color=T.text_inv,
            font=ctk.CTkFont(weight="bold"),
            command=self._confirm,
        ).pack(side="left")

        self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        py = parent.winfo_rooty() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{px}+{py}")

    def _confirm(self) -> None:
        self.selected = [fp for fp, var in self._check_vars.items() if var.get()]
        self.confirmed = bool(self.selected)
        self.destroy()
