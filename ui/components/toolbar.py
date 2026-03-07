"""
ui/components/toolbar.py
Persistent top toolbar — URL input + Analyze button always accessible.
Sits below the title bar, above the sidebar+content split.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

# Spinner frames
_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class Toolbar(ctk.CTkFrame):
    """
    Persistent URL bar — always visible regardless of active tab.
    Sends analyse requests and delegates result to HomeTab.
    """

    def __init__(self, master, app: "MainWindow", **kwargs) -> None:
        super().__init__(
            master,
            height=60,
            fg_color=T.surface,
            corner_radius=0,
            **kwargs,
        )
        self.pack_propagate(False)
        self._app = app
        self._analysing = False
        self._spinner_idx = 0
        self._spinner_job: Optional[str] = None
        self._analyse_token = 0

        self._build()
        T.register(self._on_theme)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Top border
        ctk.CTkFrame(
            self, height=1, fg_color=T.border, corner_radius=0,
        ).pack(fill="x", side="top")

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=10)

        # ── URL input ─────────────────────────────────────────────────────
        self._url_entry = ctk.CTkEntry(
            inner,
            placeholder_text="  Paste video URL here and press Enter or click Analyze…",
            font=ctk.CTkFont(size=13),
            height=40,
            fg_color=T.input,
            border_color=T.border2,
            border_width=1,
            text_color=T.text,
            corner_radius=8,
        )
        self._url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._url_entry.bind("<Return>", lambda _: self._start_analyse())
        self._url_entry.bind("<FocusIn>",  self._on_focus_in)
        self._url_entry.bind("<FocusOut>", self._on_focus_out)

        # ── Analyze button ────────────────────────────────────────────────
        self._analyse_btn = ctk.CTkButton(
            inner,
            text="Analyze",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            width=110,
            corner_radius=8,
            fg_color=T.primary,
            hover_color=T.primary_hover,
            text_color="white",
            command=self._start_analyse,
        )
        self._analyse_btn.pack(side="left", padx=(0, 8))

        # ── Quick actions ─────────────────────────────────────────────────
        self._paste_btn = ctk.CTkButton(
            inner,
            text="⎘",
            width=40, height=40,
            corner_radius=8,
            font=ctk.CTkFont(size=14),
            fg_color=T.surface2,
            hover_color=T.surface3,
            text_color=T.text2,
            command=self._paste_clipboard,
        )
        self._paste_btn.pack(side="left", padx=(0, 4))

        self._clear_btn = ctk.CTkButton(
            inner,
            text="✕",
            width=40, height=40,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color=T.surface2,
            hover_color=T.surface3,
            text_color=T.text3,
            command=self._clear_url,
        )
        self._clear_btn.pack(side="left")

        # Status label (spinner + message)
        self._status_lbl = ctk.CTkLabel(
            inner, text="",
            font=ctk.CTkFont(size=12),
            text_color=T.text3,
        )
        self._status_lbl.pack(side="left", padx=(12, 0))

        # Bottom border
        ctk.CTkFrame(
            self, height=1, fg_color=T.border, corner_radius=0,
        ).pack(fill="x", side="bottom")

    # ── Analysis flow ─────────────────────────────────────────────────────

    def set_url(self, url: str) -> None:
        """Programmatically set the URL (called from HomeTab pass-through)."""
        self._url_entry.delete(0, "end")
        self._url_entry.insert(0, url)

    def get_url(self) -> str:
        return self._url_entry.get().strip()

    def _start_analyse(self) -> None:
        url = self.get_url()
        if not url or self._analysing:
            return

        self._analysing = True
        self._analyse_token += 1
        my_token = self._analyse_token

        self._analyse_btn.configure(state="disabled", text="Analyzing…")
        self._set_status("Fetching media info…", T.text2)
        self._start_spinner()

        # Navigate to home tab immediately so user sees it loading
        self._app.navigate_to("home")

        # Notify HomeTab to clear its previous result
        home = self._app.get_tab("home")
        if home:
            home.on_analysis_start()

        def _safe_done(info) -> None:
            if not self.winfo_exists():
                return
            if my_token != self._analyse_token:
                self.after(0, self._reset_btn)
                return
            self.after(0, lambda: self._on_done(info))

        def _safe_error(err: str) -> None:
            if not self.winfo_exists():
                return
            if my_token != self._analyse_token:
                self.after(0, self._reset_btn)
                return
            self.after(0, lambda: self._on_error(err))

        self._app.service.analyse_url(
            url=url, on_done=_safe_done, on_error=_safe_error)

    def _on_done(self, info) -> None:
        self._stop_spinner()
        self._reset_btn()
        if not info or not info.title:
            self._set_status("⚠  No media found", T.error)
            self._app.toast("Analysis returned empty result.", "error")
            return
        self._set_status(f"✓  {info.title[:50]}", T.success)
        home = self._app.get_tab("home")
        if home:
            home.on_analysis_done(info)
        self._app.toast(f"Ready: {info.title[:44]}", "success")

    def _on_error(self, err: str) -> None:
        self._stop_spinner()
        self._reset_btn()
        display = err[:100] if err else "Unknown error"
        self._set_status(f"⚠  {display}", T.error)
        self._app.toast("Analysis failed.", "error")
        home = self._app.get_tab("home")
        if home:
            home.on_analysis_error(err)

    def _reset_btn(self) -> None:
        self._analysing = False
        if self.winfo_exists():
            self._analyse_btn.configure(state="normal", text="Analyze")

    # ── Spinner ───────────────────────────────────────────────────────────

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        self._tick_spinner()

    def _tick_spinner(self) -> None:
        if not self._analysing or not self.winfo_exists():
            return
        frame = _SPINNER[self._spinner_idx % len(_SPINNER)]
        self._analyse_btn.configure(text=f"{frame} Analyzing")
        self._spinner_idx += 1
        self._spinner_job = self.after(100, self._tick_spinner)

    def _stop_spinner(self) -> None:
        if self._spinner_job:
            self.after_cancel(self._spinner_job)
            self._spinner_job = None

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.configure(text=text, text_color=color)
        # Auto-clear success message after 4 s
        if text.startswith("✓"):
            self.after(4000, lambda: self._status_lbl.configure(text=""))

    def _paste_clipboard(self) -> None:
        try:
            text = self.clipboard_get().strip()
            if text.startswith(("http://", "https://")):
                self._url_entry.delete(0, "end")
                self._url_entry.insert(0, text)
                self._set_status("URL pasted", T.text2)
        except Exception:
            pass

    def _clear_url(self) -> None:
        self._url_entry.delete(0, "end")
        self._set_status("", T.text3)
        home = self._app.get_tab("home")
        if home:
            home.clear_result()

    def _on_focus_in(self, _e) -> None:
        self._url_entry.configure(border_color=T.primary)

    def _on_focus_out(self, _e) -> None:
        self._url_entry.configure(border_color=T.border2)

    # ── Theme ──────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.surface)
        self._url_entry.configure(
            fg_color=T.input, border_color=T.border2, text_color=T.text)
        self._analyse_btn.configure(
            fg_color=T.primary, hover_color=T.primary_hover)
        self._paste_btn.configure(
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2)
        self._clear_btn.configure(
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text3)
        self._status_lbl.configure(text_color=T.text3)
