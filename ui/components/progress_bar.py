"""
ui/components/progress_bar.py
Custom canvas-based progress bar with gradient fill and theme support.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from ui.themes.tokens import T


class OmniProgressBar(ctk.CTkFrame):
    # Track is 6px tall; 3px radius gives a fully-rounded pill appearance.
    _HEIGHT = 6
    _RADIUS = 3
    _ANIM_PILL  = 0.25   # bouncing pill = 25% of bar width
    _ANIM_RANGE = 0.75   # pill head travels 0.0 -> 0.75 then reverses
    _ANIM_STEP  = 0.015  # position advance per 16 ms frame

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._value = 0.0
        self._state = "active"   # "active" | "complete" | "failed" | "paused"
        self._anim_pos = 0.0
        self._anim_dir = 1
        self._anim_id  = None

        self._canvas = tk.Canvas(
            self, height=self._HEIGHT,
            bg=T.prog_track, highlightthickness=0, bd=0,
        )
        self._canvas.pack(side="left", fill="x", expand=True)
        self._canvas.bind("<Configure>", self._redraw)

        # Wider label so "100%" never gets clipped.
        self._pct = ctk.CTkLabel(
            self, text="  0%",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=T.text3,
            width=40, anchor="e",
        )
        self._pct.pack(side="left", padx=(8, 0))

        T.register(self._on_theme)

    def set_progress(self, value: float) -> None:
        self._value = max(0.0, min(100.0, value))
        self._pct.configure(
            text=f"{self._value:.0f}%",
            text_color=T.text if self._value >= 100 else T.text3,
        )
        if self._value > 0:
            self._stop_anim()
        self._redraw()

    def set_state(self, state: str) -> None:
        """state: 'active' | 'complete' | 'failed' | 'paused'"""
        self._state = state
        if state == "active" and self._value == 0:
            self._start_anim()
        else:
            self._stop_anim()
        self._redraw()

    # ── Animation ─────────────────────────────────────────────────────────

    def _start_anim(self) -> None:
        if self._anim_id is None and self.winfo_exists():
            self._anim_id = self.after(16, self._tick_anim)

    def _stop_anim(self) -> None:
        if self._anim_id is not None:
            self.after_cancel(self._anim_id)
            self._anim_id = None

    def _tick_anim(self) -> None:
        if not self.winfo_exists():
            self._anim_id = None
            return
        self._anim_pos += self._ANIM_STEP * self._anim_dir
        if self._anim_pos >= self._ANIM_RANGE:
            self._anim_pos = self._ANIM_RANGE
            self._anim_dir = -1
        elif self._anim_pos <= 0.0:
            self._anim_pos = 0.0
            self._anim_dir = 1
        self._redraw()
        if self._state == "active" and self._value == 0:
            self._anim_id = self.after(16, self._tick_anim)
        else:
            self._anim_id = None

    # ── Drawing ───────────────────────────────────────────────────────────

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
        r = self._RADIUS
        if w <= 1:
            return
        self._canvas.delete("all")

        # Track — pill shape
        self._draw_pill(0, w, T.prog_track)

        # Indeterminate bounce when converting but no progress yet
        if self._state == "active" and self._value == 0:
            pill_w = int(w * self._ANIM_PILL)
            x0 = int(w * self._anim_pos)
            x1 = min(x0 + pill_w, w)
            if x1 - x0 >= r * 2:
                self._draw_gradient_segment(x0, x1, h)
            return

        fill_w = int(w * self._value / 100)
        if fill_w < r * 2:
            return

        if self._state == "complete":
            self._draw_pill(0, fill_w, T.success)
        elif self._state == "failed":
            self._draw_pill(0, fill_w, T.error)
        elif self._state == "paused":
            self._draw_pill(0, fill_w, T.text3)
        else:
            # Gradient fill drawn in steps then capped with pill end-cap.
            self._draw_gradient_segment(0, fill_w, h)
            start_hex = T.prog_start
            end_hex = self._interp_hex(T.prog_start, T.prog_end, fill_w / max(w, 1))
            self._canvas.create_oval(0, 0, r * 2, h, fill=start_hex, outline="")
            self._canvas.create_oval(fill_w - r * 2, 0, fill_w, h, fill=end_hex, outline="")

    def _draw_gradient_segment(self, x0: int, x1: int, h: int) -> None:
        """Draw gradient-filled rectangle from x0 to x1 with pill caps."""
        r = self._RADIUS
        r1, g1, b1 = self._hex_to_rgb(T.prog_start)
        r2, g2, b2 = self._hex_to_rgb(T.prog_end)
        seg_w = x1 - x0
        step = max(1, seg_w // 80)
        x = x0
        while x < x1:
            t = (x - x0) / max(seg_w - 1, 1)
            rc = int(r1 + (r2 - r1) * t)
            gc = int(g1 + (g2 - g1) * t)
            bc = int(b1 + (b2 - b1) * t)
            x2 = min(x + step, x1)
            self._canvas.create_rectangle(x, 0, x2, h,
                fill=f"#{rc:02x}{gc:02x}{bc:02x}", outline="")
            x = x2
        start_c = T.prog_start
        end_c = self._interp_hex(T.prog_start, T.prog_end, seg_w / max(self._canvas.winfo_width(), 1))
        self._canvas.create_oval(x0, 0, x0 + r * 2, h, fill=start_c, outline="")
        self._canvas.create_oval(x1 - r * 2, 0, x1, h, fill=end_c, outline="")

    def _draw_pill(self, x0: int, x1: int, color: str) -> None:
        """Draw a horizontal rounded-rectangle (pill) between x0 and x1."""
        h = self._HEIGHT
        r = self._RADIUS
        w = x1 - x0
        if w <= 0:
            return
        if w <= r * 2:
            self._canvas.create_rectangle(x0, 0, x1, h, fill=color, outline="")
            return
        # Body
        self._canvas.create_rectangle(x0 + r, 0, x1 - r, h, fill=color, outline="")
        # Left cap
        self._canvas.create_oval(x0, 0, x0 + r * 2, h, fill=color, outline="")
        # Right cap
        self._canvas.create_oval(x1 - r * 2, 0, x1, h, fill=color, outline="")

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @classmethod
    def _interp_hex(cls, start: str, end: str, t: float) -> str:
        """Linearly interpolate between two hex colours at position t in [0,1]."""
        r1, g1, b1 = cls._hex_to_rgb(start)
        r2, g2, b2 = cls._hex_to_rgb(end)
        t = max(0.0, min(1.0, t))
        return (
            f"#{int(r1 + (r2-r1)*t):02x}"
            f"{int(g1 + (g2-g1)*t):02x}"
            f"{int(b1 + (b2-b1)*t):02x}"
        )
