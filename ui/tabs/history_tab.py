"""
ui/tabs/history_tab.py
Searchable, scrollable download history.
"""
from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from ui.themes.tokens import T
from utils.helpers import open_folder

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_STATUS_TOKENS = {
    "COMPLETED": ("success",  "success_bg"),
    "FAILED":    ("error",    "error_bg"),
    "CANCELLED": ("text3",    "surface2"),
}


class HistoryTab(ctk.CTkFrame):

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
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

        # Search bar
        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.pack(fill="x", padx=28, pady=(0, 14))

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self.refresh())

        self._search_entry = ctk.CTkEntry(
            search_row, textvariable=self._search_var,
            placeholder_text="🔍  Search title, URL or filename…",
            height=40, corner_radius=10,
            fg_color=T.input, border_color=T.border2,
            text_color=T.text, font=ctk.CTkFont(size=12))
        self._search_entry.pack(fill="x")

        # Scroll area
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover)
        self._scroll.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        self._empty_lbl = ctk.CTkLabel(
            self._scroll, text="No download history yet",
            font=ctk.CTkFont(size=14), text_color=T.text3)
        self._empty_lbl.pack(expand=True, pady=80)

    def refresh(self) -> None:
        query = self._search_var.get().strip()
        entries = (self._app.service.search_history(query)
                   if query else self._app.service.get_history())

        for w in self._scroll.winfo_children():
            w.destroy()

        if not entries:
            ctk.CTkLabel(
                self._scroll,
                text="No results found" if query else "No download history yet",
                font=ctk.CTkFont(size=14), text_color=T.text3,
            ).pack(expand=True, pady=80)
            return

        for entry in entries:
            self._make_row(entry)

    def _make_row(self, entry: dict) -> None:
        status = entry.get("status", "")
        fg_key, bg_key = _STATUS_TOKENS.get(status, ("text3", "surface2"))

        card = ctk.CTkFrame(
            self._scroll, fg_color=T.surface,
            corner_radius=10, border_width=1, border_color=T.border)
        card.pack(fill="x", pady=(0, 8))

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 4))

        ctk.CTkLabel(
            top,
            text=(entry.get("title") or entry.get("url", ""))[:72],
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

        bot = ctk.CTkFrame(card, fg_color="transparent")
        bot.pack(fill="x", padx=16, pady=(0, 12))

        fname = entry.get("filename", "")
        ctk.CTkLabel(
            bot, text=fname[-60:] if fname else "—",
            font=ctk.CTkFont(size=10), text_color=T.text3,
        ).pack(side="left", fill="x", expand=True)

        if fname:
            ctk.CTkButton(
                bot, text="📂", width=32, height=26, corner_radius=6,
                fg_color=T.success_bg, hover_color=T.success_bg,
                text_color=T.success,
                command=lambda p=fname: open_folder(Path(p).parent),
            ).pack(side="right")

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
        self._scroll.configure(scrollbar_button_color=T.scrollbar,
                               scrollbar_button_hover_color=T.scrollbar_hover)
