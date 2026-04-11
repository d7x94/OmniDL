"""
ui/tabs/queue_tab.py
Live download queue — polls service every 500ms.
"""
from __future__ import annotations

import logging
import queue
from typing import TYPE_CHECKING

import customtkinter as ctk

from domain.enums.download_status import DownloadStatus
from ui.components.download_item_widget import DownloadItemWidget
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)


class QueueTab(ctk.CTkFrame):

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        self._widgets: dict[str, DownloadItemWidget] = {}
        self._ui_queue: queue.Queue = queue.Queue()
        self._build()
        self._drain_ui_queue()
        self._poll()
        T.register(self._on_theme)

    def _drain_ui_queue(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.warning("QueueTab _ui_queue raised: %s", exc)
        except queue.Empty:
            pass
        self.after(150, self._drain_ui_queue)

    def _build(self) -> None:
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(24, 14))

        self._title_lbl = ctk.CTkLabel(
            hdr, text="Download Queue",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=T.text)
        self._title_lbl.pack(side="left")

        right = ctk.CTkFrame(hdr, fg_color="transparent")
        right.pack(side="right")

        self._count_lbl = ctk.CTkLabel(
            right, text="",
            font=ctk.CTkFont(size=11),
            text_color=T.text2,
            fg_color=T.surface2, corner_radius=6, padx=12, pady=4)
        self._count_lbl.pack(side="left", padx=(0, 10))

        self._clear_btn = ctk.CTkButton(
            right, text="Clear Finished",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32, width=130, corner_radius=8,
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2,
            command=self._clear_finished)
        self._clear_btn.pack(side="left")

        # Scrollable list
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover)
        self._scroll.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        self._empty_lbl = ctk.CTkLabel(
            self._scroll,
            text="No active downloads\nPaste a URL on the Download tab to get started",
            font=ctk.CTkFont(size=14), text_color=T.text3, justify="center")
        self._empty_lbl.pack(expand=True, pady=80)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        if not self.winfo_ismapped():
            self.after(500, self._poll)
            return

        tasks = self._app.service.get_all_tasks()
        current_ids = {t.id for t in tasks}

        for tid in list(self._widgets):
            if tid not in current_ids:
                w = self._widgets.pop(tid)
                if w.winfo_exists():
                    w.destroy()

        for task in tasks:
            if task.id not in self._widgets:
                w = DownloadItemWidget(
                    self._scroll, task,
                    on_pause=self._on_pause,
                    on_cancel=self._on_cancel,
                    on_convert=lambda p, target_ext="mp4", encode_settings=None, **kw: (
                        self._app.service.convert_to_mp4(
                            p,
                            target_ext=target_ext,
                            encode_settings=encode_settings,
                            **kw,
                        )
                    ),
                    on_send=self._on_send,
                    on_delete=self._on_delete_file)
                w.pack(fill="x", pady=(0, 8))
                self._widgets[task.id] = w
            else:
                w = self._widgets[task.id]
                if w.winfo_exists():
                    w.refresh(task)

        if tasks:
            self._empty_lbl.pack_forget()
        elif not self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack(expand=True, pady=80)

        active = sum(1 for t in tasks if t.status in DownloadStatus.active_states())
        self._count_lbl.configure(
            text=f"  {active} active  ·  {len(tasks)} total  ")
        # Use a shorter interval while downloads are active so progress updates
        # feel responsive.  Slow down when nothing is happening to reduce CPU
        # overhead — 2 000 ms is imperceptible for a static list.
        interval = 500 if active else 2000
        self.after(interval, self._poll)

    def _on_pause(self, task_id: str) -> None:
        task = self._app.service.get_task(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            self._app.service.resume_download(task_id)
        else:
            self._app.service.pause_download(task_id)

    def _on_cancel(self, task_id: str) -> None:
        self._app.service.cancel_download(task_id)

    def _clear_finished(self) -> None:
        self._app.service.clear_finished()

    def _on_send(self, file_path, restore_btn, task=None, specific_files=None) -> None:
        """Send completed file to all configured Taildrop nodes.

        Reads node list from config (multi-node list, falls back to legacy
        single-node scalar).  Runs in the background via TaildropService so
        the UI never blocks.  restore_btn() is always called — on success,
        on error, and when Taildrop is not configured — so the Send button
        is never left in a disabled state.  task is forwarded to
        send_file_to_nodes so gallery_dl_files is used for multi-file posts.
        """
        nodes = self._app.config.taildrop_target_nodes
        if not nodes:
            self._app.toast("⚠  Chưa cấu hình thiết bị đích trong Settings → Taildrop", "warning")
            restore_btn()
            return
        if not self._app.config.taildrop_enabled:
            self._app.toast("⚠  Taildrop chưa được bật trong Settings", "warning")
            restore_btn()
            return

        node_list_str = ", ".join(nodes)

        def _on_node_done(node: str) -> None:
            self._ui_queue.put(lambda: self._app.toast(
                f"📲  Đã gửi → {node}", "success"
            ))

        def _on_node_error(node: str, err: str) -> None:
            self._ui_queue.put(lambda: self._app.toast(
                f"❌  Gửi thất bại → {node}: {err[:60]}", "error"
            ))

        try:
            self._app.taildrop.send_file_to_nodes(
                file_path,
                nodes,
                on_node_done=_on_node_done,
                on_node_error=_on_node_error,
                task=task,
                specific_files_override=specific_files,
            )
            self._app.toast(f"📲  Đang gửi đến {len(nodes)} thiết bị: {node_list_str}", "info")
        except Exception as exc:
            logger.warning("QueueTab _on_send error: %s", exc)
            self._app.toast(f"❌  Lỗi gửi file: {exc}", "error")
        finally:
            # Restore button on UI thread after a short delay so feedback toast
            # appears before the button is re-enabled.
            self.after(800, restore_btn)

    def _on_delete_file(self, file_path, task_id: str) -> None:
        """Remove task from the queue display after file deletion."""
        # File is already removed from disk by PostDownloadActions.
        # We just remove it from the in-memory task list so the widget
        # disappears cleanly on the next poll cycle.
        try:
            self._app.service.cancel_download(task_id)
        except Exception as exc:
            logger.debug("QueueTab _on_delete_file cancel error (non-fatal): %s", exc)

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._title_lbl.configure(text_color=T.text)
        self._count_lbl.configure(text_color=T.text2, fg_color=T.surface2)
        self._clear_btn.configure(fg_color=T.surface2, hover_color=T.surface3,
                                  text_color=T.text2)
        self._empty_lbl.configure(text_color=T.text3)
        self._scroll.configure(scrollbar_button_color=T.scrollbar,
                               scrollbar_button_hover_color=T.scrollbar_hover)
