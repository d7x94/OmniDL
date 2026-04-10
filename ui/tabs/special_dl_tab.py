"""
ui/tabs/special_dl_tab.py
Special Downloads tab — handles platforms that the main yt-dlp/gallery-dl
pipeline cannot process: Facebook Stories (CDP).

Architecture
────────────
• Completely isolated: no imports from YtDlpEngine, DownloadManager, DownloadService
• Each platform has its own engine called directly from this tab
• Errors here CANNOT affect normal downloads
• User explicitly triggers each download (no auto-intercept)
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:        # pragma: no cover
    ctk = None             # type: ignore[assignment]

from ui.components.post_download_actions import PostDownloadActions
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

# ── Platform registry ─────────────────────────────────────────────────────────

_PLATFORMS = {
    "facebook_story": {
        "label":        "Facebook Story",
        "placeholder":  "https://www.facebook.com/stories/...",
        "needs_browser": True,
        "browsers":     ["brave", "chrome"],
        "guide": (
            "Hướng dẫn:\n"
            "1. Đóng hoàn toàn Brave / Chrome (kể cả System Tray / Dock)\n"
            "2. Dán URL Story vào ô trên\n"
            "3. Chọn trình duyệt → nhấn  Tải về\n"
            "4. Trình duyệt sẽ tự mở, tải xong tự đóng\n"
            "⚠  Story hết hạn sau 24 giờ\n"
            "⚠  Dùng bản cài từ website — App Store không hỗ trợ"
        ),
    },
}


_BaseFrame = ctk.CTkFrame if ctk is not None else object


class SpecialDlTab(_BaseFrame):   # type: ignore[misc]
    """Special Downloads tab — placed in SYSTEM section of sidebar."""

    def __init__(self, master, main_window: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._win        = main_window
        self._config     = main_window._config
        self._running    = False
        self._ui_queue: queue.Queue = queue.Queue()

        T.register(self._apply_theme)
        self._build()
        self._drain_ui_queue()   # start draining on UI thread

    # ── UI queue drain (Python 3.14 thread-safety) ────────────────────────────

    def _drain_ui_queue(self) -> None:
        """Drain _ui_queue on the UI thread every 50 ms.

        Background threads (worker, on_progress) post callables here instead
        of calling self.after(0, ...) directly — required for Python 3.14
        which enforces strict main-thread-only Tkinter access (BUG AY pattern).
        """
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.debug("_drain_ui_queue callback raised: %s", exc)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(50, self._drain_ui_queue)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Title
        ctk.CTkLabel(
            self, text="Special Downloads",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=T.text,
        ).pack(anchor="w", padx=28, pady=(24, 4))

        ctk.CTkLabel(
            self,
            text="Tải nội dung mà yt-dlp không hỗ trợ — Facebook Story v.v.",
            font=ctk.CTkFont(size=12),
            text_color=T.text2,
        ).pack(anchor="w", padx=28, pady=(0, 20))

        # ── Card ──────────────────────────────────────────────────────────────
        card = ctk.CTkFrame(self, fg_color=T.surface2, corner_radius=12)
        card.pack(fill="x", padx=24, pady=(0, 16))

        # Row 1: platform selector
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            row1, text="Platform:", width=90,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=T.text, anchor="w",
        ).pack(side="left")

        self._platform_var = ctk.StringVar(value="facebook_story")
        self._platform_menu = ctk.CTkOptionMenu(
            row1,
            values=list(_PLATFORMS.keys()),
            variable=self._platform_var,
            command=self._on_platform_change,
            fg_color=T.surface3,
            button_color=T.primary,
            button_hover_color=T.primary_hover,
            text_color=T.text,
            font=ctk.CTkFont(size=13),
            width=200,
            dynamic_resizing=False,
        )
        self._platform_menu.pack(side="left", padx=(0, 0))
        # Display friendly labels
        _friendly = [v["label"] for v in _PLATFORMS.values()]
        self._platform_menu.configure(values=_friendly)
        self._platform_menu.set(_friendly[0])

        # Row 2: URL entry + download button
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=20, pady=(0, 10))

        self._url_entry = ctk.CTkEntry(
            row2,
            placeholder_text="https://www.facebook.com/stories/...",
            font=ctk.CTkFont(size=13),
            fg_color=T.surface3,
            border_color=T.border,
            text_color=T.text,
            height=40,
        )
        self._url_entry.pack(side="left", fill="x", expand=True, padx=(0, 12))

        self._dl_btn = ctk.CTkButton(
            row2, text="⬇  Tải về",
            command=self._on_download,
            fg_color=T.primary,
            hover_color=T.primary_hover,
            text_color=T.text_inv,
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40, width=120, corner_radius=8,
        )
        self._dl_btn.pack(side="right")

        # Row 3: browser selector (only for CDP platforms)
        row3 = ctk.CTkFrame(card, fg_color="transparent")
        row3.pack(fill="x", padx=20, pady=(0, 8))

        self._browser_label = ctk.CTkLabel(
            row3, text="Trình duyệt:", width=90,
            font=ctk.CTkFont(size=12),
            text_color=T.text2, anchor="w",
        )
        self._browser_label.pack(side="left")

        self._browser_var = ctk.StringVar(value="brave")
        self._browser_seg = ctk.CTkSegmentedButton(
            row3,
            values=["brave", "chrome"],
            variable=self._browser_var,
            fg_color=T.surface3,
            selected_color=T.primary,
            selected_hover_color=T.primary_hover,
            unselected_color=T.surface3,
            unselected_hover_color=T.border2,
            text_color=T.text,
            font=ctk.CTkFont(size=12),
            height=30,
        )
        self._browser_seg.pack(side="left", padx=(0, 0))

        # Separator
        ctk.CTkFrame(card, fg_color=T.border, height=1, corner_radius=0).pack(
            fill="x", padx=20, pady=(8, 0))

        # Guide text
        self._guide_lbl = ctk.CTkLabel(
            card,
            text=_PLATFORMS["facebook_story"]["guide"],
            font=ctk.CTkFont(size=12),
            text_color=T.text2,
            justify="left", anchor="nw",
        )
        self._guide_lbl.pack(anchor="w", padx=20, pady=(12, 20))

        # ── Progress card ─────────────────────────────────────────────────────
        prog_card = ctk.CTkFrame(self, fg_color=T.surface2, corner_radius=12)
        prog_card.pack(fill="x", padx=24, pady=(0, 16))

        # Status row
        status_row = ctk.CTkFrame(prog_card, fg_color="transparent")
        status_row.pack(fill="x", padx=20, pady=(16, 4))

        self._status_dot = ctk.CTkLabel(
            status_row, text="●", width=16,
            font=ctk.CTkFont(size=14),
            text_color=T.text3,
        )
        self._status_dot.pack(side="left")

        self._status_lbl = ctk.CTkLabel(
            status_row, text="Đang chờ...",
            font=ctk.CTkFont(size=13),
            text_color=T.text2, anchor="w",
        )
        self._status_lbl.pack(side="left", padx=(6, 0), fill="x", expand=True)

        self._speed_lbl = ctk.CTkLabel(
            status_row, text="",
            font=ctk.CTkFont(size=12),
            text_color=T.text3, anchor="e",
        )
        self._speed_lbl.pack(side="right")

        # Retry + Clear row (shown on error)
        self._retry_row = ctk.CTkFrame(prog_card, fg_color="transparent")
        self._retry_row.pack(fill="x", padx=20, pady=(0, 4))
        self._retry_row.pack_forget()

        self._retry_btn = ctk.CTkButton(
            self._retry_row, text="🔄  Thử lại",
            command=self._on_download,
            fg_color=T.primary_dim,
            hover_color=T.primary,
            text_color=T.primary_text,
            font=ctk.CTkFont(size=12),
            height=28, corner_radius=6,
        )
        self._retry_btn.pack(side="left", padx=(0, 8))

        self._clear_btn = ctk.CTkButton(
            self._retry_row, text="🗑  Xoá lịch sử",
            command=self._clear_status,
            fg_color=T.surface3,
            hover_color=T.border2,
            text_color=T.text3,
            font=ctk.CTkFont(size=12),
            height=28, corner_radius=6,
        )
        self._clear_btn.pack(side="left")

        # Progress bar
        self._progress = ctk.CTkProgressBar(
            prog_card,
            fg_color=T.surface3,
            progress_color=T.primary,
            height=6, corner_radius=3,
        )
        self._progress.pack(fill="x", padx=20, pady=(4, 8))
        self._progress.set(0)

        # Open folder button (hidden until done)
        # Button row: open folder + preview + post-download actions (convert/send/delete)
        self._btn_row = ctk.CTkFrame(prog_card, fg_color="transparent")
        self._btn_row.pack(anchor="e", padx=20, pady=(0, 14))
        self._btn_row.pack_forget()   # hidden until done

        self._open_btn = ctk.CTkButton(
            self._btn_row, text="📂  Mở thư mục",
            command=self._open_output_folder,
            fg_color=T.surface3,
            hover_color=T.border2,
            text_color=T.text2,
            font=ctk.CTkFont(size=12),
            height=32, corner_radius=6,
        )
        self._open_btn.pack(side="left", padx=(0, 8))

        self._preview_btn = ctk.CTkButton(
            self._btn_row, text="▶  Xem",
            command=self._open_preview,
            fg_color=T.primary_dim,
            hover_color=T.primary,
            text_color=T.primary_text,
            font=ctk.CTkFont(size=12),
            height=32, corner_radius=6,
        )
        self._preview_btn.pack(side="left", padx=(0, 8))

        # ── Unified post-download action bar: Convert · Send · Delete ─────
        self._post_actions = PostDownloadActions(
            self._btn_row,
            on_convert=self._on_post_convert,
            on_send=self._on_post_send,
            on_delete=self._on_post_delete,
            compact=False,
        )
        self._post_actions.pack(side="left")

        self._last_dest: Path | None       = None

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_platform_change(self, friendly_name: str) -> None:
        # Resolve friendly label → internal key
        key = next(
            (k for k, v in _PLATFORMS.items() if v["label"] == friendly_name),
            "facebook_story",
        )
        info = _PLATFORMS.get(key, _PLATFORMS["facebook_story"])
        self._guide_lbl.configure(text=info["guide"])
        self._url_entry.configure(placeholder_text=info["placeholder"])
        # Show/hide browser selector
        if info.get("needs_browser"):
            self._browser_label.pack(side="left")
            self._browser_seg.pack(side="left", padx=(0, 0))
        else:
            self._browser_label.pack_forget()
            self._browser_seg.pack_forget()

    def _on_download(self) -> None:
        if self._running:
            return

        url = self._url_entry.get().strip()
        if not url:
            self._set_status("error", "Hãy dán URL vào ô trên.")
            return

        friendly = self._platform_menu.get()
        platform_key = next(
            (k for k, v in _PLATFORMS.items() if v["label"] == friendly),
            "facebook_story",
        )
        browser = self._browser_var.get()

        self._running = True
        self._dl_btn.configure(state="disabled", text="Đang tải...")
        self._set_status("info", "Đang khởi động...", 0)

        threading.Thread(
            target=self._worker,
            args=(platform_key, url, browser),
            daemon=True,
        ).start()

    def _worker(self, platform_key: str, url: str, browser: str) -> None:
        try:
            if platform_key == "facebook_story":
                results = [self._run_facebook_story(url, browser)]
            else:
                results = []

            # Filter out None values
            results = [r for r in results if r is not None]

            if results:
                self._last_dest = results[0]
                name  = results[0].name
                label = f"✅ Đã tải: {name}"
                self._ui_queue.put(lambda lb=label: self._set_status("success", lb, 100))
                self._ui_queue.put(lambda: self._btn_row.pack(anchor="e", padx=20, pady=(0, 14)))
                # Show PostDownloadActions bound to the downloaded file.
                dest = results[0]
                self._ui_queue.put(lambda d=dest: self._post_actions.show(d))
            else:
                self._ui_queue.put(lambda: self._set_status("error", "Không có kết quả."))

        except RuntimeError as exc:
            msg = str(exc)
            self._ui_queue.put(lambda m=msg: self._set_status("error", m))
        except Exception as exc:
            logger.exception("SpecialDlTab worker error")
            self._ui_queue.put(lambda m=str(exc): self._set_status("error", f"Lỗi: {m}"))
        finally:
            self._running = False
            self._ui_queue.put(lambda: self._dl_btn.configure(state="normal", text="⬇  Tải về"))

    def _run_facebook_story(self, url: str, browser: str) -> "Path | None":
        from infrastructure.downloader.facebook_story_engine import download_story

        def _on_progress(pct: int, speed: str, status: str) -> None:
            self._ui_queue.put(lambda p=pct, sp=speed, st=status:
                               self._set_status("info", st, p, sp))

        return download_story(
            url=url,
            config=self._config,
            browser=browser,
            on_progress=_on_progress,
            timeout=45.0,
        )

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _set_status(
        self,
        kind: str,        # "info" | "success" | "error" | "warning"
        message: str,
        pct: int = -1,    # -1 = don't update bar
        speed: str = "",
    ) -> None:
        color_map = {
            "info":    T.primary_text,
            "success": T.success_text,
            "error":   T.error_text,
            "warning": T.warning_text,
        }
        dot_map = {
            "info":    T.primary,
            "success": T.success,
            "error":   T.error,
            "warning": T.warning_text,
        }
        self._status_dot.configure(text_color=dot_map.get(kind, T.text3))
        self._status_lbl.configure(text=message, text_color=color_map.get(kind, T.text2))
        self._speed_lbl.configure(text=speed)
        if pct >= 0:
            self._progress.set(pct / 100)
        # Show retry row only on errors
        if kind == "error":
            self._retry_row.pack(fill="x", padx=20, pady=(0, 4))
        elif kind in ("success", "info") and pct == 0:
            self._retry_row.pack_forget()

    def _clear_status(self) -> None:
        """Reset status to idle."""
        self._retry_row.pack_forget()
        self._btn_row.pack_forget()
        self._post_actions.hide()
        self._progress.set(0)
        self._status_dot.configure(text_color=T.text3)
        self._status_lbl.configure(text="Đang chờ...", text_color=T.text2)
        self._speed_lbl.configure(text="")
        self._last_dest = None

    def _open_output_folder(self) -> None:
        from utils.helpers import open_folder, reveal_in_explorer
        dest = self._last_dest
        if dest and dest.exists():
            if not reveal_in_explorer(dest):
                open_folder(dest.parent)
        elif dest and dest.parent.exists():
            open_folder(dest.parent)

    def _open_preview(self) -> None:
        from utils.helpers import open_file, open_folder
        dest = self._last_dest
        if dest and dest.exists():
            open_file(dest)
        elif dest and dest.parent.exists():
            open_folder(dest.parent)

    # ── Post-download action handlers ─────────────────────────────────────────

    def _on_post_convert(self, file_path: Path, target_ext: str, encode_settings=None) -> None:
        """Bridge PostDownloadActions → FFmpeg convert service.

        Forwards target_ext (mp4/mp3/mkv/avi) and optional encode_settings
        (EncodeSettings for Custom mode) so the user's choices are honoured.
        """
        try:
            convert_svc = self._win.service.convert_to_mp4
        except AttributeError:
            self._post_actions.notify_convert_error("Convert service không khả dụng.")
            return

        def _on_progress(pct: float) -> None:
            self._ui_queue.put(lambda p=pct: self._progress.set(p / 100))

        def _on_done(output_path: Path) -> None:
            self._last_dest = output_path
            self._ui_queue.put(
                lambda op=output_path: self._post_actions.notify_convert_done(op)
            )
            self._ui_queue.put(lambda: self._set_status(
                "success", f"✅ Convert xong: {output_path.name}", 100
            ))

        def _on_error(msg: str) -> None:
            self._ui_queue.put(
                lambda m=msg: self._post_actions.notify_convert_error(m)
            )

        self._set_status("info", f"Đang convert → .{target_ext}…", 0)
        try:
            convert_svc(
                file_path,
                target_ext=target_ext,
                encode_settings=encode_settings,
                on_progress=_on_progress,
                on_done=_on_done,
                on_error=_on_error,
            )
        except Exception as exc:
            logger.warning("SpecialDlTab _on_post_convert error: %s", exc)
            self._post_actions.notify_convert_error(str(exc))

    def _on_post_send(self, file_path: Path, restore_btn, specific_files=None) -> None:
        """Bridge PostDownloadActions → TaildropService.send_file_to_nodes()."""
        cfg = self._config
        nodes = cfg.taildrop_target_nodes
        if not nodes:
            self._set_status("warning", "⚠  Chưa cấu hình thiết bị đích trong Settings → Taildrop")
            restore_btn()
            return
        if not cfg.taildrop_enabled:
            self._set_status("warning", "⚠  Taildrop chưa được bật trong Settings")
            restore_btn()
            return

        node_list_str = ", ".join(nodes)

        def _on_node_done(node: str) -> None:
            self._ui_queue.put(lambda n=node: self._set_status(
                "success", f"📲 Đã gửi → {n}"
            ))

        def _on_node_error(node: str, err: str) -> None:
            self._ui_queue.put(lambda n=node, e=err: self._set_status(
                "error", f"❌ Gửi thất bại → {n}: {e[:60]}"
            ))

        try:
            self._win.taildrop.send_file_to_nodes(
                file_path,
                nodes,
                on_node_done=_on_node_done,
                on_node_error=_on_node_error,
                specific_files_override=specific_files,
            )
            self._set_status("info", f"📲 Đang gửi đến: {node_list_str}")
        except Exception as exc:
            logger.warning("SpecialDlTab _on_post_send error: %s", exc)
            self._set_status("error", f"Lỗi gửi: {exc}")
        finally:
            # Re-enable Send button after short delay.
            if self.winfo_exists():
                self.after(800, restore_btn)

    def _on_post_delete(self, file_path: Path) -> None:
        """Called after file has been deleted — reset the tab status."""
        self._last_dest = None
        self._set_status("info", "🗑  File đã được xoá.")
        # Hide the button row since there's nothing left to act on.
        if self._btn_row.winfo_ismapped():
            self._btn_row.pack_forget()

    def _apply_theme(self) -> None:
        """Re-apply theme tokens when user switches theme."""
        try:
            self.configure(fg_color=T.bg)
        except Exception:
            pass
