"""
ui/components/status_bar.py
Bottom status bar — active count, total speed, network indicator.
Polls the service every second.
"""
from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

import customtkinter as ctk

from domain.enums.download_status import DownloadStatus
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_NET_CHECK_HOST = ("8.8.8.8", 53)
_NET_CHECK_TIMEOUT = 1.5


def _check_network() -> bool:
    """Non-blocking network check — runs in background thread.

    Uses s.settimeout() on the individual socket rather than the global
    socket.setdefaulttimeout(), which would affect all concurrently-created
    sockets (e.g. yt-dlp download connections) and is not thread-safe.
    The socket is closed via a context manager to prevent FD leaks.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(_NET_CHECK_TIMEOUT)
            s.connect(_NET_CHECK_HOST)
        return True
    except Exception:
        return False


class StatusBar(ctk.CTkFrame):
    """Thin bottom bar showing live download stats."""

    def __init__(self, master, app: "MainWindow", **kwargs) -> None:
        super().__init__(
            master,
            height=34,
            fg_color=T.statusbar,
            corner_radius=0,
            **kwargs,
        )
        self.pack_propagate(False)
        self._app = app
        self._net_ok: bool = True
        self._net_check_interval = 15_000   # ms between network checks

        self._build()
        self._poll()
        self._check_net()

        # Re-style on theme change
        T.register(self._on_theme)

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Separator line at top
        ctk.CTkFrame(
            self, height=1, fg_color=T.border, corner_radius=0,
        ).pack(fill="x", side="top")

        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16)

        # ── Left: active downloads ────────────────────────────────────────
        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left", fill="y")

        self._dot_lbl = ctk.CTkLabel(
            left, text="●",
            font=ctk.CTkFont(size=8),
            text_color=T.text3,
        )
        self._dot_lbl.pack(side="left", padx=(0, 5))

        self._active_lbl = ctk.CTkLabel(
            left, text="No active downloads",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        )
        self._active_lbl.pack(side="left")

        # ── Center: total speed ───────────────────────────────────────────
        center = ctk.CTkFrame(inner, fg_color="transparent")
        center.pack(side="left", fill="y", expand=True)

        self._speed_lbl = ctk.CTkLabel(
            center, text="",
            font=ctk.CTkFont(size=11),
            text_color=T.primary_text,
        )
        self._speed_lbl.pack(expand=True)

        # ── Right: network status ─────────────────────────────────────────
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right", fill="y")

        self._net_dot = ctk.CTkLabel(
            right, text="●",
            font=ctk.CTkFont(size=8),
            text_color=T.success,
        )
        self._net_dot.pack(side="left", padx=(0, 5))

        self._net_lbl = ctk.CTkLabel(
            right, text="Network OK",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        )
        self._net_lbl.pack(side="left")

    # ── Polling ───────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            tasks = self._app.service.get_all_tasks()
            active = [t for t in tasks if t.status in DownloadStatus.active_states()]

            if active:
                self._dot_lbl.configure(text_color=T.primary)
                self._active_lbl.configure(
                    text=f"{len(active)} active  ·  {len(tasks)} total",
                    text_color=T.text2,
                )
                # Aggregate speed
                total_bps = sum(
                    self._parse_speed(t.speed)
                    for t in active
                    if t.speed
                )
                if total_bps > 0:
                    self._speed_lbl.configure(
                        text=f"↓  {self._fmt_speed(total_bps)}"
                    )
                else:
                    self._speed_lbl.configure(text="")
            else:
                self._dot_lbl.configure(text_color=T.text3)
                self._active_lbl.configure(
                    text="No active downloads", text_color=T.text3,
                )
                self._speed_lbl.configure(text="")
        except Exception:
            pass
        self.after(800, self._poll)

    def _check_net(self) -> None:
        """Check network in a background thread so it never blocks UI."""
        if not self.winfo_exists():
            return
        import threading
        threading.Thread(target=self._do_net_check, daemon=True).start()
        self.after(self._net_check_interval, self._check_net)

    def _do_net_check(self) -> None:
        ok = _check_network()
        if self.winfo_exists():
            self.after(0, lambda: self._update_net(ok))

    def _update_net(self, ok: bool) -> None:
        self._net_ok = ok
        if ok:
            self._net_dot.configure(text_color=T.success)
            self._net_lbl.configure(text="Network OK", text_color=T.text3)
        else:
            self._net_dot.configure(text_color=T.error)
            self._net_lbl.configure(text="No connection", text_color=T.error_text)

    # ── Theme ──────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.statusbar)
        self._update_net(self._net_ok)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_speed(speed_str: str) -> float:
        """Convert speed string to float bytes/s.

        Handles both SI units (MB/s, KB/s) and IEC units (MiB/s, KiB/s).
        The yt-dlp engine emits IEC units exclusively; SI is kept for
        forward-compatibility if the format ever changes.
        """
        if not speed_str:
            return 0.0
        try:
            s = speed_str.strip()
            num, _, unit = s.partition(" ")
            v = float(num)
            unit = unit.lower()
            if "gib" in unit or "gb" in unit:
                return v * 1024 ** 3
            if "mib" in unit or "mb" in unit:
                return v * 1024 ** 2
            if "kib" in unit or "kb" in unit:
                return v * 1024
            return v
        except Exception:
            return 0.0

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps >= 1024 ** 3:
            return f"{bps / 1024 ** 3:.1f} GB/s"
        if bps >= 1024 ** 2:
            return f"{bps / 1024 ** 2:.1f} MB/s"
        if bps >= 1024:
            return f"{bps / 1024:.0f} KB/s"
        return f"{bps:.0f} B/s"
