"""
ui/components/progress_bar.py
Custom canvas-based progress bar with gradient fill and theme support.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from ui.themes.tokens import T


class OmniProgressBar(ctk.CTkFrame):
    _HEIGHT = 4

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._value = 0.0
        self._state = "active"   # "active" | "complete" | "failed" | "paused"

        self._canvas = tk.Canvas(
            self, height=self._HEIGHT,
            bg=T.prog_track, highlightthickness=0, bd=0,
        )
        self._canvas.pack(side="left", fill="x", expand=True)
        self._canvas.bind("<Configure>", self._redraw)

        self._pct = ctk.CTkLabel(
            self, text="0%",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=T.text3,
            width=34, anchor="e",
        )
        self._pct.pack(side="left", padx=(6, 0))

        T.register(self._on_theme)

    def set_progress(self, value: float) -> None:
        self._value = max(0.0, min(100.0, value))
        self._pct.configure(
            text=f"{self._value:.0f}%",
            text_color=T.text if self._value >= 100 else T.text3,
        )
        self._redraw()

    def set_state(self, state: str) -> None:
        """state: 'active' | 'complete' | 'failed' | 'paused'"""
        self._state = state
        self._redraw()

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self._canvas.configure(bg=T.prog_track)
        self._pct.configure(
            text_color=T.text if self._value >= 100 else T.text3,
        )
        self._redraw()

    def _redraw(self, _event=None) -> None:
        w = self._canvas.winfo_width()
        h = self._HEIGHT
        if w <= 1:
            return
        self._canvas.delete("all")
        self._canvas.create_rectangle(0, 0, w, h, fill=T.prog_track, outline="")
        fill_w = int(w * self._value / 100)
        if fill_w < 2:
            return

        if self._state == "complete":
            self._canvas.create_rectangle(0, 0, fill_w, h, fill=T.success, outline="")
        elif self._state == "failed":
            self._canvas.create_rectangle(0, 0, fill_w, h, fill=T.error, outline="")
        elif self._state == "paused":
            self._canvas.create_rectangle(0, 0, fill_w, h, fill=T.text3, outline="")
        else:
            r1, g1, b1 = self._hex_to_rgb(T.prog_start)
            r2, g2, b2 = self._hex_to_rgb(T.prog_end)
            step = max(1, fill_w // 80)
            x = 0
            while x < fill_w:
                t = x / max(fill_w - 1, 1)
                r = int(r1 + (r2 - r1) * t)
                g = int(g1 + (g2 - g1) * t)
                b = int(b1 + (b2 - b1) * t)
                x2 = min(x + step, fill_w)
                self._canvas.create_rectangle(
                    x, 0, x2, h,
                    fill=f"#{r:02x}{g:02x}{b:02x}", outline="",
                )
                x = x2

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
