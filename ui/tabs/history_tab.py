"""
ui/tabs/history_tab.py
Searchable, filterable download history with lazy rendering.
"""
from __future__ import annotations

import logging
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from ui.themes.tokens import T
from utils.helpers import open_folder, reveal_in_explorer

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_PAGE_SIZE = 30

_STATUS_TOKENS = {
    "COMPLETED": ("success",  "success_bg"),
    "FAILED":    ("error",    "error_bg"),
    "CANCELLED": ("text3",    "surface2"),
}

_FILTER_OPTIONS = ["All", "COMPLETED", "FAILED", "CANCELLED"]


def _fmt_bytes(n: int) -> str:
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_date(ts: float) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%y %H:%M")
    except Exception:
        return ""


class HistoryTab(ctk.CTkFrame):

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        self._entries: list[dict] = []
        self._rendered: int = 0
        self._debounce_id: Optional[str] = None
        self._load_more_btn: Optional[ctk.CTkButton] = None
        self._build()
        T.register(self._on_theme)

    def _build(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=28, pady=(24, 14))

        self._title_lbl = ctk.CTkLabel(
            hdr, text="Download History",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=T.text)
        self._title_lbl.pack(side="left")

        self._clear_btn = ctk.CTkButton(
            hdr, text="Clear All",
            font=ctk.CTkFont(size=11, weight="bold"),
            height=32, width=96, corner_radius=8,
            fg_color=T.error_bg, hover_color=T.error_bg,
            text_color=T.error,
            command=self._clear_all)
        self._clear_btn.pack(side="right")

        # Search + filter row
        ctrl_row = ctk.CTkFrame(self, fg_color="transparent")
        ctrl_row.pack(fill="x", padx=28, pady=(0, 10))

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_change)

        self._search_entry = ctk.CTkEntry(
            ctrl_row, textvariable=self._search_var,
            placeholder_text="Search title, URL or filename...",
            height=38, corner_radius=10,
            fg_color=T.input, border_color=T.border2,
            text_color=T.text, font=ctk.CTkFont(size=12))
        self._search_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._status_var = tk.StringVar(value="All")
        self._status_var.trace_add("write", self._on_search_change)

        self._filter_menu = ctk.CTkOptionMenu(
            ctrl_row, variable=self._status_var,
            values=_FILTER_OPTIONS,
            width=130, height=38, corner_radius=10,
            fg_color=T.surface2, button_color=T.surface2,
            button_hover_color=T.surface3,
            text_color=T.text, font=ctk.CTkFont(size=12))
        self._filter_menu.pack(side="left")

        # Scroll area
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover)
        self._scroll.pack(fill="both", expand=True, padx=28, pady=(0, 20))

    def _on_search_change(self, *_) -> None:
        if self._debounce_id:
            self.after_cancel(self._debounce_id)
        self._debounce_id = self.after(300, self.refresh)

    def refresh(self) -> None:
        self._debounce_id = None
        query = self._search_var.get().strip()
        status_filter = self._status_var.get()

        entries = (self._app.service.search_history(query)
                   if query else self._app.service.get_history())

        if status_filter != "All":
            entries = [e for e in entries if e.get("status") == status_filter]

        self._entries = entries
        self._rendered = 0
        self._load_more_btn = None

        for w in self._scroll.winfo_children():
            w.destroy()

        if not entries:
            ctk.CTkLabel(
                self._scroll,
                text="No results found" if (query or status_filter != "All")
                     else "No download history yet",
                font=ctk.CTkFont(size=14), text_color=T.text3,
            ).pack(expand=True, pady=80)
            return

        self._render_page()

    def _render_page(self) -> None:
        batch = self._entries[self._rendered: self._rendered + _PAGE_SIZE]
        for entry in batch:
            self._make_row(entry)
        self._rendered += len(batch)

        remaining = len(self._entries) - self._rendered
        if remaining > 0:
            self._load_more_btn = ctk.CTkButton(
                self._scroll,
                text=f"Load more ({remaining} remaining)",
                font=ctk.CTkFont(size=12),
                height=36, corner_radius=8,
                fg_color=T.surface2, hover_color=T.surface3,
                text_color=T.text2,
                command=self._load_more)
            self._load_more_btn.pack(pady=(4, 8))

    def _load_more(self) -> None:
        if self._load_more_btn:
            self._load_more_btn.destroy()
            self._load_more_btn = None
        self._render_page()

    def _make_row(self, entry: dict) -> None:
        status = entry.get("status", "")
        fg_key, bg_key = _STATUS_TOKENS.get(status, ("text3", "surface2"))
        task_id = entry.get("id", "")
        url = entry.get("url", "")
        fname = entry.get("filename", "")
        platform = entry.get("platform", "")
        size_str = _fmt_bytes(entry.get("downloaded_bytes", 0))
        date_str = _fmt_date(entry.get("finished_at", 0))

        card = ctk.CTkFrame(
            self._scroll, fg_color=T.surface,
            corner_radius=10, border_width=1, border_color=T.border)
        card.pack(fill="x", pady=(0, 8))

        # Title + status badge
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            top,
            text=(entry.get("title") or url)[:72],
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=T.text, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            top, text=f"  {status}  ",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=getattr(T, fg_key),
            fg_color=getattr(T, bg_key),
            corner_radius=5,
        ).pack(side="left")

        # Filename row
        mid = ctk.CTkFrame(card, fg_color="transparent")
        mid.pack(fill="x", padx=16, pady=(0, 4))

        ctk.CTkLabel(
            mid, text=fname[-60:] if fname else "—",
            font=ctk.CTkFont(size=10), text_color=T.text3,
        ).pack(side="left", fill="x", expand=True)

        # Metadata + actions row
        bot = ctk.CTkFrame(card, fg_color="transparent")
        bot.pack(fill="x", padx=16, pady=(0, 10))

        # Metadata chips
        meta_parts = []
        if platform:
            meta_parts.append(platform)
        if size_str:
            meta_parts.append(size_str)
        if date_str:
            meta_parts.append(date_str)

        if meta_parts:
            ctk.CTkLabel(
                bot, text="  |  ".join(meta_parts),
                font=ctk.CTkFont(size=10), text_color=T.text3,
            ).pack(side="left", fill="x", expand=True)

        # Action buttons (right-aligned, reverse order of pack)
        if fname:
            def _open(path_str=fname):
                p = Path(path_str).resolve()
                if p.is_file():
                    if not reveal_in_explorer(p):
                        open_folder(p.parent)
                elif p.parent.is_dir():
                    open_folder(p.parent)
            ctk.CTkButton(
                bot, text="📂", width=32, height=26, corner_radius=6,
                fg_color=T.success_bg, hover_color=T.success_bg,
                text_color=T.success, command=_open,
            ).pack(side="right", padx=(4, 0))

        def _delete(tid=task_id, card_ref=card):
            self._app.service.delete_history_entry(tid)
            card_ref.destroy()
        ctk.CTkButton(
            bot, text="🗑", width=32, height=26, corner_radius=6,
            fg_color=T.error_bg, hover_color=T.error_bg,
            text_color=T.error, command=_delete,
        ).pack(side="right", padx=(4, 0))

        if url:
            def _copy_url(u=url):
                self.clipboard_clear()
                self.clipboard_append(u)
            ctk.CTkButton(
                bot, text="📋", width=32, height=26, corner_radius=6,
                fg_color=T.surface2, hover_color=T.surface3,
                text_color=T.text2, command=_copy_url,
            ).pack(side="right", padx=(4, 0))

            def _redownload(u=url):
                toolbar = self._app.get_toolbar()
                if toolbar:
                    toolbar.set_url(u)
                self._app.navigate_to("home")
            ctk.CTkButton(
                bot, text="↩", width=32, height=26, corner_radius=6,
                fg_color=T.surface2, hover_color=T.surface3,
                text_color=T.text2, command=_redownload,
            ).pack(side="right", padx=(4, 0))

    def _clear_all(self) -> None:
        import tkinter.messagebox as mb
        if mb.askyesno("OmniDL", "Clear all download history?", icon="warning"):
            self._app.service.clear_history()
            self.refresh()

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._title_lbl.configure(text_color=T.text)
        self._clear_btn.configure(fg_color=T.error_bg, text_color=T.error)
        self._search_entry.configure(fg_color=T.input, border_color=T.border2,
                                     text_color=T.text)
        self._filter_menu.configure(fg_color=T.surface2, button_color=T.surface2,
                                    button_hover_color=T.surface3, text_color=T.text)
        self._scroll.configure(scrollbar_button_color=T.scrollbar,
                               scrollbar_button_hover_color=T.scrollbar_hover)
