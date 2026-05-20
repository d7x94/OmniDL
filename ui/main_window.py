"""Main application window."""

from __future__ import annotations

import logging
import threading as _threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.download_service import DownloadService
from app.services.taildrop_service import TaildropService
from domain.enums.download_status import DownloadStatus
from infrastructure.config.config_manager import ConfigManager
from ui.theme_qt import apply_theme
from ui.themes.tokens import THEME_NAMES, T
from utils.clipboard_monitor import ClipboardMonitor

logger = logging.getLogger(__name__)

NAV_ITEMS = [
    ("home", "↓", "Tải xuống", "TẢI XUỐNG"),
    ("queue", "≡", "Hàng đợi", "TẢI XUỐNG"),
    ("batch", "⊞", "Hàng loạt", "TẢI XUỐNG"),
    ("live_monitor", "◉", "Live Monitor", "TẢI XUỐNG"),
    ("convert", "⇄", "Chuyển đổi", "CÔNG CỤ"),
    ("history", "◷", "Lịch sử", "THƯ VIỆN"),
    ("settings", "⊙", "Cài đặt", "HỆ THỐNG"),
    ("special_dl", "◆", "Đặc biệt", "HỆ THỐNG"),
]

_TOAST_COLOR = {
    "success": "success",
    "error": "error",
    "info": "primary",
    "warning": "warning",
}


class MainWindow(QMainWindow):
    MIN_W = 1080
    MIN_H = 720

    def __init__(self, service: DownloadService, config: ConfigManager) -> None:
        super().__init__()
        self._service = service
        self._config = config
        self._current_tab: Optional[str] = None
        self._clipboard_monitor = None
        self._toast_timer: Optional[QTimer] = None
        self._pill_btns: dict[str, QPushButton] = {}
        self._pill_badges: dict[str, QLabel] = {}
        self._build()
        self._start_clipboard_monitor_if_enabled()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self._facade = ServiceFacade(self._service, self._config)
        self.setWindowTitle("OmniDL — Ultimate Media Downloader")
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self.resize(self.MIN_W, self.MIN_H)
        self._center_on_screen()
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        from ui.components.toolbar import Toolbar

        self._toolbar = Toolbar(app=self)
        root.addWidget(self._toolbar)

        # Top bar: pill tabs + right controls
        top_bar = QWidget()
        top_bar.setObjectName("top_bar")
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(12, 6, 12, 6)
        top_bar_layout.setSpacing(8)

        self._pill_bar = self._build_pill_tabs()
        top_bar_layout.addWidget(self._pill_bar, 1)

        # Theme toggle button
        self._theme_btn = QPushButton("☀ Sáng")
        self._theme_btn.setFixedSize(80, 28)
        self._theme_btn.clicked.connect(self._toggle_theme)
        top_bar_layout.addWidget(self._theme_btn)

        # Notification bell
        self._notif_btn = QPushButton("🔔")
        self._notif_btn.setFixedSize(32, 28)
        self._notif_btn.setToolTip("Thông báo")
        self._notif_btn.clicked.connect(self._open_notification_panel)
        top_bar_layout.addWidget(self._notif_btn)

        root.addWidget(top_bar)

        # Thin separator below top bar
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {T.border};")
        root.addWidget(sep)

        # Content area
        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._stack, 1)

        # Build tabs
        self._tabs: dict[str, QWidget] = {}
        self._build_tabs()

        # Status bar
        from ui.components.status_bar import StatusBar

        self._status_bar = StatusBar(app=self)
        self.setStatusBar(self._status_bar)

        # Toast label (overlay)
        self._toast_lbl = QLabel("", self)
        self._toast_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {T.primary};
                color: white;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: bold;
            }}
        """)
        self._toast_lbl.hide()

        # Ctrl+K shortcut
        shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        shortcut.activated.connect(self._open_command_palette)

        # Notification panel (floating overlay)
        from ui.components.notification_panel import NotificationPanel

        self._notification_panel = NotificationPanel(self)

        # Register theme callback
        T.register(self._on_theme)
        is_dark = T.mode not in ("light", "solarized", "lavender")
        self._theme_btn.setText("🌙 Tối" if not is_dark else "☀ Sáng")

        self.navigate_to("home")

    def _build_pill_tabs(self) -> QWidget:
        pill_bar = QWidget()
        pill_bar.setObjectName("pill_bar")

        layout = QHBoxLayout(pill_bar)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        for key, icon, label, _section in NAV_ITEMS:
            btn = QPushButton(f"{icon}  {label}")
            btn.setObjectName("pill_tab")
            btn.setProperty("active", "false")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self.navigate_to(k))
            layout.addWidget(btn)
            self._pill_btns[key] = btn

        layout.addStretch()
        return pill_bar

    def _build_tabs(self) -> None:
        from ui.tabs.batch_tab import BatchTab
        from ui.tabs.convert_tab import ConvertTab
        from ui.tabs.history_tab import HistoryTab
        from ui.tabs.home_tab import HomeTab
        from ui.tabs.live_monitor_tab import LiveMonitorTab
        from ui.tabs.queue_tab import QueueTab
        from ui.tabs.settings_tab import SettingsTab
        from ui.tabs.special_dl_tab import SpecialDlTab

        tab_classes = {
            "home": HomeTab,
            "queue": QueueTab,
            "batch": BatchTab,
            "live_monitor": LiveMonitorTab,
            "convert": ConvertTab,
            "history": HistoryTab,
            "settings": SettingsTab,
            "special_dl": SpecialDlTab,
        }
        for key, cls in tab_classes.items():
            widget = cls(self)
            widget.setObjectName("tab_content")
            widget.setProperty("tab_key", key)
            self._tabs[key] = widget
            self._stack.addWidget(widget)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def service(self) -> "ServiceFacade":
        return self._facade

    @property
    def config(self) -> ConfigManager:
        return self._config

    @property
    def taildrop(self) -> "TaildropService":
        return self._service.taildrop

    def get_tab(self, key: str) -> Optional[QWidget]:
        return self._tabs.get(key)

    def get_toolbar(self):
        return getattr(self, "_toolbar", None)

    # ── Navigation ────────────────────────────────────────────────────────

    def navigate_to(self, key: str) -> None:
        if key not in self._tabs:
            return
        self._stack.setCurrentWidget(self._tabs[key])

        # Update pill button active state
        prev = self._current_tab
        self._current_tab = key

        if prev and prev in self._pill_btns:
            btn = self._pill_btns[prev]
            btn.setProperty("active", "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if key in self._pill_btns:
            btn = self._pill_btns[key]
            btn.setProperty("active", "true")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        if key == "history":
            tab = self._tabs.get("history")
            if tab and hasattr(tab, "refresh"):
                tab.refresh()
        if key == "home":
            tab = self._tabs.get("home")
            if tab and hasattr(tab, "refresh"):
                tab.refresh()

    def update_tab_badge(self, tab_key: str, count: int) -> None:
        """Update badge count on pill tab. count=0 removes badge."""
        btn = self._pill_btns.get(tab_key)
        if not btn:
            return
        badge = self._pill_badges.get(tab_key)
        if count == 0:
            if badge:
                badge.hide()
            return
        if badge is None:
            badge = QLabel(btn)
            badge.setObjectName("badge")
            badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._pill_badges[tab_key] = badge
        badge.setText(str(count))
        badge.adjustSize()
        # Position badge at top-right of button (guard: width is 0 before window is shown)
        if btn.width() > 0:
            badge.move(btn.width() - badge.width() - 2, 2)
        badge.show()
        badge.raise_()

    # ── Placeholder actions ───────────────────────────────────────────────

    def _open_command_palette(self) -> None:
        from ui.components.command_palette import CommandPalette

        palette = CommandPalette(self, on_navigate=self.navigate_to)
        palette.exec()

    def _open_notification_panel(self) -> None:
        self._notification_panel.toggle()

    # ── Theme ─────────────────────────────────────────────────────────────

    def _toggle_theme(self) -> None:
        themes = list(THEME_NAMES)
        current_idx = themes.index(T.mode) if T.mode in themes else 0
        new_mode = themes[(current_idx + 1) % len(themes)]
        T.set_mode(new_mode)
        self._config.set("theme", new_mode)
        is_dark = T.mode not in ("light", "solarized", "lavender")
        self._theme_btn.setText("🌙 Tối" if not is_dark else "☀ Sáng")

    def _on_theme(self) -> None:
        apply_theme()
        if self._current_tab and self._current_tab in self._pill_btns:
            btn = self._pill_btns[self._current_tab]
            btn.setProperty("active", "true")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ── Drag & drop ───────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0].toString()
        elif event.mimeData().hasText():
            url = event.mimeData().text()
        else:
            return
        self.navigate_to("home")
        home = self._tabs.get("home")
        if home and hasattr(home, "url_input"):
            home.url_input.setText(url)

    # ── Toast ─────────────────────────────────────────────────────────────

    def toast(self, message: str, kind: str = "info") -> None:
        color_key = _TOAST_COLOR.get(kind, "primary")
        color = getattr(T, color_key)
        self._toast_lbl.setText(f"  {message}  ")
        self._toast_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {color};
                color: white;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
                font-weight: bold;
            }}
        """)
        self._toast_lbl.adjustSize()
        self._position_toast()
        self._toast_lbl.show()
        self._toast_lbl.raise_()

        if self._toast_timer:
            self._toast_timer.stop()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast_lbl.hide)
        self._toast_timer.start(3200)

    def _position_toast(self) -> None:
        w = self._toast_lbl.width()
        h = self._toast_lbl.height()
        x = self.width() - w - 20
        y = self.height() - h - 48
        self._toast_lbl.move(x, y)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._toast_lbl.isVisible():
            self._position_toast()
        if self._notification_panel.isVisible():
            self._notification_panel._reposition()

    # ── Clipboard monitor ─────────────────────────────────────────────────

    def _start_clipboard_monitor_if_enabled(self) -> None:
        if not self._config.clipboard_monitor_enabled:
            return
        self.start_clipboard_monitor()

    def start_clipboard_monitor(self) -> None:
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()

        def _safe_get_clipboard() -> str:
            result: list = []
            done = _threading.Event()

            def _fetch() -> None:
                try:
                    result.append(QGuiApplication.clipboard().text())
                except Exception:
                    result.append("")
                done.set()

            QTimer.singleShot(0, _fetch)
            done.wait(timeout=2.0)
            return result[0] if result else ""

        def _on_new_url(url: str) -> None:
            from ui.signals import ui_bridge

            toolbar = self.get_toolbar()
            if toolbar is not None:
                ui_bridge.post(lambda u=url: toolbar.trigger_from_clipboard(u))

        monitor = ClipboardMonitor(
            get_clipboard=_safe_get_clipboard,
            on_new_url=_on_new_url,
        )
        self._clipboard_monitor = monitor
        monitor.start()

    def stop_clipboard_monitor(self) -> None:
        if self._clipboard_monitor is not None:
            self._clipboard_monitor.stop()
            self._clipboard_monitor = None

    # ── Close ────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        active_dl = [t for t in self._service.get_all_tasks() if t.status in DownloadStatus.active_states()]
        convert_tab = self._tabs.get("convert")
        active_cv = getattr(convert_tab, "_active_count", 0)
        total_active = len(active_dl) + active_cv

        if total_active:
            parts = []
            if active_dl:
                parts.append(f"{len(active_dl)} download(s)")
            if active_cv:
                parts.append(f"{active_cv} conversion(s)")
            summary = " và ".join(parts)
            reply = QMessageBox.question(
                self,
                "OmniDL",
                f"{summary} đang chạy.\nĐóng và huỷ tất cả?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        for t in active_dl:
            self._service.cancel_download(t.id)

        self._config.save()
        self.stop_clipboard_monitor()
        if hasattr(self, "_status_bar"):
            self._status_bar.stop()
        event.accept()

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen().geometry()
        x = (screen.width() - self.MIN_W) // 2
        y = (screen.height() - self.MIN_H) // 2
        self.move(x, y)


# ── Service facade ────────────────────────────────────────────────────────────


class ServiceFacade:
    def __init__(self, svc: DownloadService, cfg: ConfigManager) -> None:
        self._svc = svc
        self._cfg = cfg

    def analyse_url(self, url, on_done, on_error):
        return self._svc.analyse_url(url, on_done, on_error)

    def start_download(self, url, media_info, format_id, output_ext, output_dir=None):
        return self._svc.start_download(url, media_info, format_id, output_ext, output_dir)

    def pause_download(self, task_id):
        self._svc.pause_download(task_id)

    def resume_download(self, task_id):
        self._svc.resume_download(task_id)

    def cancel_download(self, task_id):
        self._svc.cancel_download(task_id)

    def clear_finished(self, exclude_ids=None):
        self._svc.clear_finished(exclude_ids=exclude_ids)

    def get_all_tasks(self):
        return self._svc.get_all_tasks()

    def get_task(self, tid):
        return self._svc.get_task(tid)

    def get_history(self):
        return self._svc.get_history()

    def search_history(self, q):
        return self._svc.search_history(q)

    def clear_history(self):
        self._svc.clear_history()

    def delete_history_entry(self, tid):
        self._svc.delete_history_entry(tid)

    def get_history_stats(self):
        return self._svc.get_history_stats()

    def get_download_dir(self) -> Path:
        return self._cfg.download_dir

    def set_download_dir(self, p: Path):
        self._cfg.set("download_dir", str(p))

    def convert_to_mp4(
        self,
        source: Path,
        on_progress=None,
        on_done=None,
        on_error=None,
        target_ext: str = "mp4",
        encode_settings=None,
    ) -> None:
        self._svc.convert_to_mp4(
            source=source,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            target_ext=target_ext,
            encode_settings=encode_settings,
        )

    def fetch_thumbnail(self, url: str, width: int, height: int, on_done, on_error) -> None:
        self._svc.fetch_thumbnail(url=url, width=width, height=height, on_done=on_done, on_error=on_error)

    def check_profile_live(self, url: str, on_done, on_error) -> None:
        self._svc.check_profile_live(url=url, on_done=on_done, on_error=on_error)

    def check_tiktok_profile_live(self, url: str, on_done, on_error) -> None:
        self._svc.check_tiktok_profile_live(url=url, on_done=on_done, on_error=on_error)

    @property
    def taildrop(self) -> "TaildropService":
        return self._svc.taildrop
