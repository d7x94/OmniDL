"""
ui/tabs/settings/general_panel.py
Settings panel: Download Location · Download Behaviour · Appearance.

Dependencies on MainWindow:
  self._app.config   – download_dir, max_concurrent, max_retries,
                       embed_thumbnail, embed_metadata, theme
  self._app.toast    – feedback toasts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover
    ctk = None               # type: ignore[assignment]

from ui.tabs.settings._base_panel import _BasePanel
from ui.themes.tokens import THEME_NAMES, T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = __import__("logging").getLogger(__name__)


class GeneralPanel(_BasePanel):
    """
    Renders the three purely-general settings sections:
      📁 Download Location
      ⚙  Download Behaviour
      🎨 Appearance
    No background workers — all handlers run synchronously on the UI thread.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, app)
        self._build()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        p   = self
        cfg = self._app.config

        # -- Download location ---------------------------------------------
        self._section(p, "📁   DOWNLOAD LOCATION")
        self._card_loc = loc = self._card(p)
        row = ctk.CTkFrame(loc, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=14)
        self._dir_lbl = ctk.CTkLabel(
            row, text=str(cfg.download_dir),
            font=ctk.CTkFont(size=12), text_color=T.primary_text)
        self._dir_lbl.pack(side="left", fill="x", expand=True)
        self._browse_dir_btn = ctk.CTkButton(
            row, text="Browse", width=80, height=32, corner_radius=8,
            fg_color=T.surface3, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=12),
            command=self._browse_dir,
        )
        self._browse_dir_btn.pack(side="left", padx=(10, 0))

        # -- Download behaviour --------------------------------------------
        self._section(p, "⚙   DOWNLOAD BEHAVIOUR")
        self._card_beh = beh = self._card(p)

        self._concurrent_var = ctk.IntVar(value=cfg.max_concurrent)
        self._slider_row(beh, "Max concurrent downloads",
                         self._concurrent_var, 1, 8,
                         lambda v: cfg.set("max_concurrent", int(v)))
        self._add_value_label(beh, self._concurrent_var)
        self._concurrent_hint = ctk.CTkLabel(
            beh,
            text="⚠  Changes take effect after restarting the app.",
            font=ctk.CTkFont(size=11),
            text_color=T.warning_text,
            anchor="e",
        )
        self._concurrent_hint.pack(anchor="e", padx=16, pady=(0, 8))

        self._retries_var = ctk.IntVar(value=cfg.max_retries)
        self._slider_row(beh, "Max retries on failure",
                         self._retries_var, 0, 10,
                         lambda v: cfg.set("max_retries", int(v)))
        self._add_value_label(beh, self._retries_var)

        self._thumb_var = ctk.BooleanVar(value=cfg.embed_thumbnail)
        self._switch_row(beh, "Embed thumbnail", self._thumb_var,
                         lambda v: cfg.set("embed_thumbnail", v))

        self._meta_var = ctk.BooleanVar(value=cfg.embed_metadata)
        self._switch_row(beh, "Embed metadata", self._meta_var,
                         lambda v: cfg.set("embed_metadata", v))

        # -- Appearance ----------------------------------------------------
        self._section(p, "🎨   APPEARANCE")
        self._card_app = app_card = self._card(p)
        theme_row = ctk.CTkFrame(app_card, fg_color="transparent")
        theme_row.pack(fill="x", padx=16, pady=14)
        ctk.CTkLabel(theme_row, text="Theme",
                     font=ctk.CTkFont(size=12), text_color=T.text2).pack(side="left")
        self._theme_om = ctk.CTkOptionMenu(
            theme_row, values=THEME_NAMES,
            command=self._change_theme, width=140, corner_radius=8,
        )
        current_theme = self._app.config.theme
        if current_theme in THEME_NAMES:
            self._theme_om.set(current_theme)
        self._theme_om.pack(side="right")

        # -- Debug logging -------------------------------------------------
        self._section(p, "🐛   DEVELOPER")
        self._card_dev = dev = self._card(p)

        self._debug_var = ctk.BooleanVar(value=cfg.debug_logging)
        self._switch_row(dev, "Debug Logging", self._debug_var,
                         self._on_debug_toggle)

        self._debug_hint = ctk.CTkLabel(
            dev,
            text="Writes detailed trace to omnidl_debug.log  •  Restart not required",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
            anchor="e",
        )
        self._debug_hint.pack(anchor="e", padx=16, pady=(0, 10))

    # ── Handlers ──────────────────────────────────────────────────────────

    def _browse_dir(self) -> None:
        import tkinter.filedialog as fd
        chosen = fd.askdirectory(
            title="Select download folder",
            initialdir=str(self._app.config.download_dir))
        if chosen:
            self._app.config.set("download_dir", chosen)
            self._dir_lbl.configure(text=chosen)

    def _change_theme(self, theme: str) -> None:
        self._app.config.set("theme", theme)
        T.set_mode(theme)                        # update palette + fire all _on_theme callbacks
        ctk.set_appearance_mode(T.ctk_base)     # map custom theme → "dark"/"light" for CTk

    def _on_debug_toggle(self, enabled: bool) -> None:
        """Enable or disable debug logging live — no restart required."""
        self._app.config.set("debug_logging", enabled)
        from utils.logger import apply_debug_logging
        apply_debug_logging(enabled)
        msg = "Debug logging ON — writing to omnidl_debug.log" if enabled else "Debug logging OFF"
        logger.info(msg)
        if hasattr(self._app, "toast"):
            self._app.toast(msg)

    # ── Theme refresh ─────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        for attr in ("_card_loc", "_card_beh", "_card_app", "_card_dev"):
            card = getattr(self, attr, None)
            if card and card.winfo_exists():
                card.configure(fg_color=T.surface, border_color=T.border)
        for lbl in self._section_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text3)
        for lbl in self._row_labels:
            if lbl.winfo_exists():
                lbl.configure(text_color=T.text2)
        for sl in self._sliders:
            if sl.winfo_exists():
                sl.configure(button_color=T.primary, progress_color=T.primary)
        for sw in self._switches:
            if sw.winfo_exists():
                sw.configure(progress_color=T.primary)
        w = getattr(self, "_dir_lbl", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.primary_text)
        w = getattr(self, "_browse_dir_btn", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface3, hover_color=T.border2, text_color=T.text2)
        w = getattr(self, "_concurrent_hint", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.warning_text)
        w = getattr(self, "_theme_om", None)
        if w and w.winfo_exists():
            w.configure(fg_color=T.surface3, button_color=T.border2, text_color=T.text2)
        w = getattr(self, "_debug_hint", None)
        if w and w.winfo_exists():
            w.configure(text_color=T.text3)
