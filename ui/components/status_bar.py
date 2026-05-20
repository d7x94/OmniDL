"""Bottom status bar — active download count, speed, network indicator."""

from __future__ import annotations

import logging
import socket
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QStatusBar,
)

from ui.signals import ui_bridge
from ui.themes.tokens import T

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

_NET_CHECK_HOST = ("8.8.8.8", 53)
_NET_CHECK_TIMEOUT = 1.5


def _check_network() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(_NET_CHECK_TIMEOUT)
            s.connect(_NET_CHECK_HOST)
        return True
    except Exception:
        return False


def _chip_style(bg: str, color: str, border: str) -> str:
    return (
        f"background-color: {bg}; color: {color}; border: 1px solid {border};"
        f" border-radius: 10px; padding: 2px 10px; font-size: 10px; font-weight: 600;"
    )


def _fmt_speed(bps: float) -> str:
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} KB/s"
    return f"{bps:.0f} B/s"


def _fmt_eta(eta_s: int) -> str:
    if eta_s < 0:
        return ""
    if eta_s >= 60:
        return f"còn ~{eta_s // 60} phút"
    return f"còn ~{eta_s}s"


class StatusBar(QStatusBar):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._net_ok = True
        self._net_checking = False

        self.setSizeGripEnabled(False)
        self.setFixedHeight(32)

        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(800)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        self._net_timer = QTimer(self)
        self._net_timer.setInterval(15_000)
        self._net_timer.timeout.connect(self._schedule_net_check)
        self._net_timer.start()
        self._schedule_net_check()

        T.register(self._on_theme)

    def _build(self) -> None:
        widget = QFrame()
        widget.setObjectName("mini_status")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        # Dot indicator: green when active, gray when idle
        self._dot = QLabel("◎")
        self._dot.setFixedWidth(16)
        layout.addWidget(self._dot)

        # Active count / idle label
        self._active_chip = QLabel("Không có tác vụ")
        self._active_chip.setStyleSheet(_chip_style(T.surface2, T.text3, T.border))
        layout.addWidget(self._active_chip)

        # Progress bar (hidden when idle)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar, 1)

        # Speed label (hidden when idle)
        self._speed_chip = QLabel("")
        self._speed_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._speed_chip.setStyleSheet(_chip_style(T.primary_dim, T.primary_text, T.primary_dim))
        self._speed_chip.hide()
        layout.addWidget(self._speed_chip)

        # ETA label (hidden when idle)
        self._eta_label = QLabel("")
        self._eta_label.setStyleSheet(f"color: {T.text3}; font-size: 10px;")
        self._eta_label.hide()
        layout.addWidget(self._eta_label)

        layout.addStretch()

        # Right: network chip
        self._net_chip = QLabel("● Mạng OK")
        self._net_chip.setStyleSheet(_chip_style(T.success_bg, T.success_text, T.success))
        layout.addWidget(self._net_chip)

        self.addPermanentWidget(widget, 1)

    def update_status(self, active: int, speed_bps: float, progress: float, eta_s: int) -> None:
        if active == 0:
            self._dot.setStyleSheet(f"color: {T.text3};")
            self._active_chip.setText("◎  Không có tác vụ")
            self._active_chip.setStyleSheet(_chip_style(T.surface2, T.text3, T.border))
            self._progress_bar.hide()
            self._speed_chip.hide()
            self._eta_label.hide()
        else:
            self._dot.setStyleSheet("color: #4caf50;")
            self._active_chip.setText(f"{active} đang tải")
            self._active_chip.setStyleSheet(_chip_style(T.primary_dim, T.primary_text, T.primary))
            self._progress_bar.setValue(int(progress * 1000))
            self._progress_bar.show()
            if speed_bps > 0:
                self._speed_chip.setText(_fmt_speed(speed_bps))
                self._speed_chip.show()
            else:
                self._speed_chip.hide()
            eta_text = _fmt_eta(eta_s)
            if eta_text:
                self._eta_label.setText(eta_text)
                self._eta_label.show()
            else:
                self._eta_label.hide()

    def _poll(self) -> None:
        try:
            tasks = self._app.service.get_all_tasks()
            downloading = [t for t in tasks if t.status.name == "DOWNLOADING"]
            queued = [t for t in tasks if t.status.name == "QUEUED"]
            processing = [t for t in tasks if t.status.name == "PROCESSING"]
            active = downloading + queued + processing

            if active:
                parts = []
                if downloading:
                    parts.append(f"{len(downloading)} đang tải")
                if processing:
                    parts.append(f"{len(processing)} xử lý")
                if queued:
                    parts.append(f"{len(queued)} chờ")
                self._active_chip.setText("↓  " + "  ·  ".join(parts))
                self._active_chip.setStyleSheet(_chip_style(T.primary_dim, T.primary_text, T.primary))

                total_bps = sum(_parse_speed(t.speed) for t in downloading if t.speed)
                if total_bps > 0:
                    self._speed_chip.setText(f"↓  {_fmt_speed(total_bps)}")
                    self._speed_chip.show()
                else:
                    self._speed_chip.hide()
            else:
                self._active_chip.setText("Không có tải xuống")
                self._active_chip.setStyleSheet(_chip_style(T.surface2, T.text3, T.border))
                self._speed_chip.hide()
        except Exception:
            pass

    def _schedule_net_check(self) -> None:
        if self._net_checking:
            return
        self._net_checking = True

        def _run() -> None:
            ok = _check_network()
            self._net_checking = False
            ui_bridge.post(lambda result=ok: self._update_net(result))

        threading.Thread(target=_run, daemon=True, name="omnidl-net-check").start()

    def _update_net(self, ok: bool) -> None:
        self._net_ok = ok
        if ok:
            self._net_chip.setText("● Mạng OK")
            self._net_chip.setStyleSheet(_chip_style(T.success_bg, T.success_text, T.success))
        else:
            self._net_chip.setText("● Mất kết nối")
            self._net_chip.setStyleSheet(_chip_style(T.error_bg, T.error_text, T.error))

    def _on_theme(self) -> None:
        self._update_net(self._net_ok)
        # Reset active chip to idle style if no active downloads
        try:
            tasks = self._app.service.get_all_tasks()
            active = [t for t in tasks if t.status.name in ("DOWNLOADING", "QUEUED", "PROCESSING")]
            if not active:
                self._active_chip.setStyleSheet(_chip_style(T.surface2, T.text3, T.border))
        except Exception:
            self._active_chip.setStyleSheet(_chip_style(T.surface2, T.text3, T.border))

    def stop(self) -> None:
        self._poll_timer.stop()
        self._net_timer.stop()


def _parse_speed(speed_str: str) -> float:
    if not speed_str:
        return 0.0
    try:
        s = speed_str.strip()
        num, _, unit = s.partition(" ")
        v = float(num)
        unit = unit.lower()
        if "gib" in unit or "gb" in unit:
            return v * 1024**3
        if "mib" in unit or "mb" in unit:
            return v * 1024**2
        if "kib" in unit or "kb" in unit:
            return v * 1024
        return v
    except Exception:
        return 0.0
