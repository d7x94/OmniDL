"""
ui/tabs/settings_tab.py  ── REFACTORED  (thin orchestrator, ~70 lines)
All user-configurable options -- themed.

Original file: 2256 lines / 1 monolithic class.
Refactored to: thin container + 5 sub-panels in ui/tabs/settings/.

Sub-panel layout
────────────────
  GeneralPanel    — Download Location · Behaviour · Appearance
  NetworkPanel    — Network/Auth · Global Cookie · Per-Platform Cookies
  ToolsPanel      — YT-DLP · Gallery-DL · Data & Privacy
  TaildropPanel   — Taildrop  (send to iPhone via Tailscale)
  RemoteApiPanel  — Remote API (iOS / Mobile remote control)

Backward-compatibility re-exports
──────────────────────────────────
_install_ytdlp_frozen and _install_gallery_dl_frozen are re-exported
here so any import that references them from this module path keeps
working unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover — headless CI
    ctk = None               # type: ignore[assignment]

from ui.themes.tokens import T
from ui.tabs.settings import (
    GeneralPanel,
    NetworkPanel,
    RemoteApiPanel,
    TaildropPanel,
    ToolsPanel,
)
# Re-export frozen-build updaters for backward compatibility
from ui.tabs.settings.tools_panel import (   # noqa: F401
    _install_gallery_dl_frozen,
    _install_ytdlp_frozen,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow

_BaseFrame = ctk.CTkFrame if ctk is not None else object


class SettingsTab(_BaseFrame):   # type: ignore[misc]
    """
    Thin container: builds a scrollable frame and instantiates sub-panels.
    All widget logic, handlers, and background workers live in the panels.
    Each panel registers its own _on_theme() callback with T independently.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app
        self._build()
        # Register only for the outer container background + scrollbar colours.
        # Individual panels register themselves inside _BasePanel.__init__.
        T.register(self._on_theme)

    def _build(self) -> None:
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        self._scroll.pack(fill="both", expand=True)
        p = self._scroll

        ctk.CTkLabel(
            p, text="Settings",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=T.text,
        ).pack(anchor="w", padx=28, pady=(24, 18))

        # Instantiate panels — each panel packs itself onto `p`.
        self._general_panel  = GeneralPanel(p,  self._app)
        self._network_panel  = NetworkPanel(p,  self._app)
        self._tools_panel    = ToolsPanel(p,    self._app)
        self._taildrop_panel = TaildropPanel(p, self._app)
        self._api_panel      = RemoteApiPanel(p, self._app)

        for panel in (
            self._general_panel,
            self._network_panel,
            self._tools_panel,
            self._taildrop_panel,
            self._api_panel,
        ):
            panel.pack(fill="x", padx=0, pady=0)

    def _on_theme(self) -> None:
        """Refresh the outer container and scrollbar only."""
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        self._scroll.configure(
            fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
