"""
ui/tabs/settings/_base_panel.py
Base class shared by every Settings sub-panel.

Provides:
  • self._app          – reference to MainWindow (config, service, toast…)
  • self._ui_queue     – thread-safe queue for background → UI thread marshal
  • self._drain_ui_queue() – called every 150 ms on the UI thread
  • Render helpers: _section(), _card(), _slider_row(), _switch_row(),
                    _add_value_label()
  • _on_theme() stub – each subclass overrides to refresh its own widgets

Design contract
───────────────
• Each Panel owns its widget refs and its _ui_queue — no shared mutable state
  between panels.
• T.register(self._on_theme) is called in __init__ so every panel
  automatically refreshes its widgets when the user changes the app theme,
  without any coordination from SettingsTab.
• The only dependency injected from outside is `app` (MainWindow).
"""
from __future__ import annotations

import logging
import queue
from typing import TYPE_CHECKING

try:
    import customtkinter as ctk
except ImportError:          # pragma: no cover — headless CI
    ctk = None               # type: ignore[assignment]

from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_BaseFrame = ctk.CTkFrame if ctk is not None else object


class _BasePanel(_BaseFrame):   # type: ignore[misc]
    """
    Lightweight base for Settings panels.
    Subclasses call super().__init__(master, app) then build their own UI.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color="transparent")
        self._app = app

        # Per-panel widget tracking lists — used by _on_theme() in each subclass
        # to refresh colors without tree-walking (see OMNIDL_STABILITY_RULES §UI-thread).
        self._section_labels: list = []
        self._row_labels:     list = []
        self._sliders:        list = []
        self._switches:       list = []

        # Thread-safe queue: background workers post lambdas here;
        # _drain_ui_queue runs them on the Tk main thread every 150 ms.
        # Each panel has its OWN queue — no contention between panels.
        self._ui_queue: queue.Queue = queue.Queue()
        self._drain_ui_queue()

        # Auto-register for theme-change callbacks.
        # T.register() is idempotent — re-registering the same callable is safe.
        T.register(self._on_theme)

    # ── Background → UI thread bridge ────────────────────────────────────

    def _drain_ui_queue(self) -> None:
        """Drain _ui_queue every 150 ms on the UI thread (Python 3.14 safe)."""
        if not self.winfo_exists():
            return
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.warning("_BasePanel _ui_queue raised: %s", exc)
        except queue.Empty:
            pass
        self.after(150, self._drain_ui_queue)

    # ── Theme callback stub ───────────────────────────────────────────────

    def _on_theme(self) -> None:
        """Override in each subclass to refresh widget colors after theme change."""
        if not self.winfo_exists():
            return

    # ── UI render helpers (shared across all panels) ──────────────────────

    def _section(self, parent, text: str) -> None:
        lbl = ctk.CTkLabel(
            parent, text=text,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=T.text3,
        )
        lbl.pack(anchor="w", padx=28, pady=(18, 5))
        self._section_labels.append(lbl)

    def _card(self, parent) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent, fg_color=T.surface,
            corner_radius=10, border_width=1, border_color=T.border)
        card.pack(fill="x", padx=28, pady=(0, 4))
        return card

    def _slider_row(self, parent, label, var, lo, hi, cmd) -> None:
        def debounced(v):
            attr = f"_sa_{label.replace(' ', '_')}"
            aid = getattr(self, attr, None)
            if aid:
                self.after_cancel(aid)
            setattr(self, attr, self.after(500, lambda: cmd(v)))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(12, 2))
        lbl = ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont(size=12), text_color=T.text2)
        lbl.pack(side="left")
        self._row_labels.append(lbl)
        sl = ctk.CTkSlider(
            row, from_=lo, to=hi, number_of_steps=hi - lo,
            variable=var, command=debounced, width=160,
            button_color=T.primary, progress_color=T.primary,
        )
        sl.pack(side="right")
        self._sliders.append(sl)

    def _add_value_label(self, parent, var: ctk.IntVar) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            parent, textvariable=var,
            font=ctk.CTkFont(size=11), text_color=T.primary_text)
        lbl.pack(anchor="e", padx=16, pady=(0, 4))
        return lbl

    def _switch_row(self, parent, label, var, cmd) -> None:
        def debounced_cmd(v: bool) -> None:
            attr = f"_sw_{label.replace(' ', '_')}"
            aid = getattr(self, attr, None)
            if aid:
                self.after_cancel(aid)
            setattr(self, attr, self.after(300, lambda: cmd(v)))

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 4))
        lbl = ctk.CTkLabel(row, text=label,
                     font=ctk.CTkFont(size=12), text_color=T.text2)
        lbl.pack(side="left")
        self._row_labels.append(lbl)
        sw = ctk.CTkSwitch(
            row, variable=var, text="",
            command=lambda: debounced_cmd(var.get()),
            onvalue=True, offvalue=False,
            progress_color=T.primary, button_color=T.primary_text,
        )
        sw.pack(side="right")
        self._switches.append(sw)
