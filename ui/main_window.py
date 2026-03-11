"""
ui/main_window.py
Root application window.

Layout (top to bottom):
  ┌──────────────────────────────────────────────────────┐
  │  Title Bar  (custom, no native chrome)               │
  ├──────────────────────────────────────────────────────┤
  │  Toolbar    (URL input — always visible)             │
  ├─────────────┬────────────────────────────────────────┤
  │             │                                        │
  │   Sidebar   │   Content (tabs)                       │
  │             │                                        │
  ├──────────────────────────────────────────────────────┤
  │  Status Bar (speed · active count · network)         │
  └──────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import logging
import sys
import tkinter as tk
import tkinter.messagebox as mb
from pathlib import Path
from typing import Optional

import customtkinter as ctk

from app.services.download_service import DownloadService
from domain.enums.download_status import DownloadStatus
from infrastructure.config.config_manager import ConfigManager
from ui.themes.tokens import T

logger = logging.getLogger(__name__)

NAV_ITEMS = [
    ("home",     "⬇",  "Download",  "DOWNLOADS"),
    ("queue",    "≡",  "Queue",     "DOWNLOADS"),
    ("convert",  "🍎", "Convert",   "TOOLS"),
    ("history",  "⏱",  "History",   "LIBRARY"),
    ("settings", "⚙",  "Settings",  "SYSTEM"),
]

_TOAST_BG = {
    "success": "success",
    "error":   "error",
    "info":    "primary",
    "warning": "warning",
}


class MainWindow(ctk.CTk):

    MIN_W = 1080
    MIN_H = 720

    def __init__(self, service: DownloadService, config: ConfigManager) -> None:
        super().__init__()
        self._service = service
        self._config  = config
        self._current_tab: Optional[str] = None
        self._toast_after: Optional[str] = None
        self.withdraw()          # hide during construction — prevents startup flicker
        self._setup_window()
        self._build_ui()
        self.deiconify()         # show once all widgets are built and themed

    # ── Exposed API ───────────────────────────────────────────────────────

    @property
    def service(self) -> "ServiceFacade":
        return self._facade

    @property
    def config(self) -> ConfigManager:
        return self._config

    def get_tab(self, key: str):
        return self._tabs.get(key)

    def get_toolbar(self):
        return getattr(self, "_toolbar", None)

    # ── Window setup ──────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.title("OmniDL — Ultimate Media Downloader")
        self.minsize(self.MIN_W, self.MIN_H)
        self.geometry(f"{self.MIN_W}x{self.MIN_H}")
        self.configure(fg_color=T.bg)
        self.overrideredirect(True)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - self.MIN_W)//2}+{(sh - self.MIN_H)//2}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "win32":
            self._restore_taskbar()

    def _restore_taskbar(self) -> None:
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00040000)
            self.wm_withdraw()
            self.wm_deiconify()
        except Exception as exc:  # non-fatal: taskbar style is cosmetic
            logger.debug("_restore_taskbar failed: %s", exc)

    def _minimize(self) -> None:
        """Minimize window — works correctly even with overrideredirect=True."""
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
                ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                return
            except Exception as exc:  # non-fatal: falls back to iconify()
                logger.debug("Win32 minimize failed, falling back to iconify: %s", exc)
        self.iconify()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._facade = ServiceFacade(self._service, self._config)

        # 1. Custom title bar
        self._build_title_bar()

        # 2. Persistent toolbar (URL input)
        from ui.components.toolbar import Toolbar
        self._toolbar = Toolbar(self, app=self)
        self._toolbar.pack(fill="x")

        # 3. Body: sidebar | content
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)

        self._sidebar = ctk.CTkFrame(
            body, width=220, fg_color=T.sidebar, corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        self._build_sidebar()

        self._divider = ctk.CTkFrame(body, width=1, fg_color=T.border, corner_radius=0)
        self._divider.pack(side="left", fill="y")

        self._content = ctk.CTkFrame(body, fg_color=T.bg, corner_radius=0)
        self._content.pack(side="left", fill="both", expand=True)

        self._build_tabs()
        self.navigate_to("home")

        # 4. Status bar
        from ui.components.status_bar import StatusBar
        self._status_bar = StatusBar(self, app=self)
        self._status_bar.pack(fill="x", side="bottom")

        # 5. Toast overlay
        self._toast_lbl = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=T.primary, corner_radius=8,
            text_color="white", padx=16, pady=8,
        )

        # Register for theme changes so structural frames refresh on toggle
        T.register(self._on_theme)
        # Sync theme-button label with the token system's current mode
        # (needed when the saved theme differs from the hardcoded default "dark")
        self._theme_btn.configure(
            text="🌙 Dark" if T.mode == "light" else "☀ Light"
        )

    def _build_title_bar(self) -> None:
        tb = ctk.CTkFrame(self, fg_color=T.sidebar, height=44, corner_radius=0)
        self._title_bar = tb          # ← ref for theme refresh
        tb.pack(fill="x")
        tb.pack_propagate(False)

        # Logo
        logo = ctk.CTkFrame(tb, fg_color="transparent")
        logo.pack(side="left", padx=18)

        self._logo_icon = ctk.CTkLabel(
            logo, text="⬇",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=T.primary,
        )
        self._logo_icon.pack(side="left", padx=(0, 6))
        self._logo_name = ctk.CTkLabel(
            logo, text="OmniDL",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=T.text,
        )
        self._logo_name.pack(side="left")

        # yt-dlp version badge
        try:
            import yt_dlp
            ver = yt_dlp.version.__version__
        except ImportError:
            ver = "not installed"
            logger.warning("yt-dlp not found — version badge will show 'not installed'")
        except Exception as exc:
            ver = "?"
            logger.debug("Could not read yt-dlp version: %s", exc)
        self._ytdlp_badge = ctk.CTkLabel(
            tb, text=f"yt-dlp {ver}",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
            fg_color=T.surface2,
            corner_radius=4, padx=8, pady=2,
        )
        self._ytdlp_badge.pack(side="left", padx=6)

        # Window controls
        bx = ctk.CTkFrame(tb, fg_color="transparent")
        bx.pack(side="right", padx=10)

        self._wctrl_btns: list[ctk.CTkButton] = []
        for text, cmd, hover in [
            ("—",  self._minimize,   T.surface3),
            ("⬜", self._toggle_max, T.surface3),
            ("✕",  self._on_close,  T.close_hover),
        ]:
            btn = ctk.CTkButton(
                bx, text=text, width=36, height=28,
                fg_color="transparent", hover_color=hover,
                font=ctk.CTkFont(size=12), text_color=T.text3,
                command=cmd,
            )
            btn.pack(side="left", padx=1)
            self._wctrl_btns.append(btn)

        for w in (tb, logo, *tb.winfo_children(), *logo.winfo_children()):
            w.bind("<ButtonPress-1>", self._drag_start, add="+")
            w.bind("<B1-Motion>",     self._drag_move,  add="+")
        self._dx = self._dy = 0

    def _build_sidebar(self) -> None:
        sections_seen: set[str] = set()
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        self._nav_indicators: dict[str, ctk.CTkFrame] = {}
        self._section_labels: list[ctk.CTkLabel] = []  # ← for theme refresh

        for key, icon, label, section in NAV_ITEMS:
            if section not in sections_seen:
                sections_seen.add(section)
                lbl = ctk.CTkLabel(
                    self._sidebar, text=section,
                    font=ctk.CTkFont(size=9, weight="bold"),
                    text_color=T.text3,
                )
                lbl.pack(anchor="w", padx=20, pady=(18, 4))
                self._section_labels.append(lbl)

            row = ctk.CTkFrame(self._sidebar, fg_color="transparent", height=40)
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)

            indicator = ctk.CTkFrame(
                row, width=3, fg_color="transparent", corner_radius=2)
            indicator.pack(side="left", fill="y", padx=(0, 1))

            btn = ctk.CTkButton(
                row,
                text=f"  {icon}   {label}",
                anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"),
                height=38, corner_radius=8,
                fg_color="transparent",
                hover_color=T.surface2,
                text_color=T.text3,
                command=lambda k=key: self.navigate_to(k),
            )
            btn.pack(side="left", fill="both", expand=True)

            self._nav_btns[key] = btn
            self._nav_indicators[key] = indicator

        # Bottom strip
        bottom = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        bottom.pack(side="bottom", fill="x", padx=12, pady=12)

        ctk.CTkLabel(
            bottom, text="v16.0.0",
            font=ctk.CTkFont(size=10), text_color=T.text3,
        ).pack(side="left")

        self._theme_btn = ctk.CTkButton(
            bottom, text="☀ Light",
            font=ctk.CTkFont(size=10),
            height=28, width=76, corner_radius=6,
            fg_color=T.surface2, hover_color=T.surface3,
            text_color=T.text2,
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right")

        self._powered_lbl = ctk.CTkLabel(
            self._sidebar, text="Powered by yt-dlp",
            font=ctk.CTkFont(size=9), text_color=T.text3,
        )
        self._powered_lbl.pack(side="bottom", pady=(0, 4))

    def _build_tabs(self) -> None:
        from ui.tabs.convert_tab import ConvertTab
        from ui.tabs.history_tab import HistoryTab
        from ui.tabs.home_tab import HomeTab
        from ui.tabs.queue_tab import QueueTab
        from ui.tabs.settings_tab import SettingsTab
        self._tabs: dict[str, ctk.CTkFrame] = {
            "home":     HomeTab(self._content, self),
            "queue":    QueueTab(self._content, self),
            "convert":  ConvertTab(self._content, self),
            "history":  HistoryTab(self._content, self),
            "settings": SettingsTab(self._content, self),
        }

    # ── Navigation ────────────────────────────────────────────────────────

    def navigate_to(self, key: str) -> None:
        if key not in self._tabs:
            return
        if self._current_tab:
            self._tabs[self._current_tab].pack_forget()
        self._tabs[key].pack(fill="both", expand=True)
        self._current_tab = key

        for k, btn in self._nav_btns.items():
            ind = self._nav_indicators[k]
            if k == key:
                btn.configure(fg_color=T.primary_dim, text_color=T.text)
                ind.configure(fg_color=T.primary)
            else:
                btn.configure(fg_color="transparent", text_color=T.text3)
                ind.configure(fg_color="transparent")

        if key == "history":
            self._tabs["history"].refresh()   # type: ignore
        if key == "home":
            self._tabs["home"].refresh()      # type: ignore

    # ── Theme toggle ──────────────────────────────────────────────────────

    def _toggle_theme(self) -> None:
        new_mode = "light" if T.mode == "dark" else "dark"
        T.set_mode(new_mode)            # fires _on_theme + all registered tab/component callbacks
        ctk.set_appearance_mode(new_mode)
        self._config.set("theme", new_mode)
        self._theme_btn.configure(
            text="🌙 Dark" if new_mode == "light" else "☀ Light")

    def _on_theme(self) -> None:
        """Refresh every structural widget in MainWindow after a theme change."""
        if not self.winfo_exists():
            return
        # Root + body frames
        self.configure(fg_color=T.bg)
        self._title_bar.configure(fg_color=T.sidebar)
        self._logo_icon.configure(text_color=T.primary)
        self._logo_name.configure(text_color=T.text)
        self._ytdlp_badge.configure(text_color=T.text3, fg_color=T.surface2)
        # Window-control buttons: — and ⬜ use surface3; ✕ uses close_hover
        for btn in self._wctrl_btns[:-1]:
            btn.configure(text_color=T.text3, hover_color=T.surface3)
        if self._wctrl_btns:
            self._wctrl_btns[-1].configure(text_color=T.text3, hover_color=T.close_hover)
        self._sidebar.configure(fg_color=T.sidebar)
        self._divider.configure(fg_color=T.border)
        self._content.configure(fg_color=T.bg)
        # Sidebar labels
        for lbl in self._section_labels:
            lbl.configure(text_color=T.text3)
        # Nav buttons — update colours and re-apply active state
        for k, btn in self._nav_btns.items():
            ind = self._nav_indicators[k]
            if k == self._current_tab:
                btn.configure(fg_color=T.primary_dim, text_color=T.text,
                               hover_color=T.surface2)
                ind.configure(fg_color=T.primary)
            else:
                btn.configure(fg_color="transparent", text_color=T.text3,
                               hover_color=T.surface2)
                ind.configure(fg_color="transparent")
        self._theme_btn.configure(
            fg_color=T.surface2, hover_color=T.surface3, text_color=T.text2)
        self._powered_lbl.configure(text_color=T.text3)

    # ── Toast ─────────────────────────────────────────────────────────────

    def toast(self, message: str, kind: str = "info") -> None:
        color = getattr(T, _TOAST_BG.get(kind, "primary"))
        self._toast_lbl.configure(
            text=f"  {message}  ",
            fg_color=color,
            text_color="white",
        )
        self._toast_lbl.place(relx=1.0, rely=1.0, anchor="se", x=-20, y=-48)
        if self._toast_after:
            self.after_cancel(self._toast_after)
        self._toast_after = self.after(3200, self._toast_lbl.place_forget)

    # ── Window management ─────────────────────────────────────────────────

    def _drag_start(self, e: tk.Event) -> None:
        self._dx = e.x_root - self.winfo_x()
        self._dy = e.y_root - self.winfo_y()

    def _drag_move(self, e: tk.Event) -> None:
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _toggle_max(self) -> None:
        if sys.platform == "win32":
            self.state("normal" if self.state() == "zoomed" else "zoomed")
        elif sys.platform == "darwin":
            self.wm_attributes("-fullscreen",
                               not bool(self.wm_attributes("-fullscreen")))
        else:
            self.attributes("-zoomed", not self.attributes("-zoomed"))

    def _on_close(self) -> None:
        active = [t for t in self._service.get_all_tasks()
                  if t.status in DownloadStatus.active_states()]
        if active:
            if not mb.askyesno(
                "OmniDL",
                f"{len(active)} download(s) in progress.\nClose and cancel all?",
                icon="warning",
            ):
                return
        for t in active:
            self._service.cancel_download(t.id)
        self._config.save()
        self.after(200, self.destroy)


# ── Service facade ────────────────────────────────────────────────────────────

class ServiceFacade:
    def __init__(self, svc: DownloadService, cfg: ConfigManager) -> None:
        self._svc = svc
        self._cfg = cfg

    def analyse_url(self, url, on_done, on_error):
        self._svc.analyse_url(url, on_done, on_error)

    def start_download(self, url, media_info, format_id, output_ext, output_dir=None):
        return self._svc.start_download(  # DEF-030: propagate DownloadTask to caller
            url, media_info, format_id, output_ext, output_dir
        )

    def pause_download(self, task_id):   self._svc.pause_download(task_id)
    def resume_download(self, task_id):  self._svc.resume_download(task_id)
    def cancel_download(self, task_id):  self._svc.cancel_download(task_id)
    def clear_finished(self):            self._svc.clear_finished()
    def get_all_tasks(self):             return self._svc.get_all_tasks()
    def get_task(self, tid):             return self._svc.get_task(tid)
    def get_history(self):               return self._svc.get_history()
    def search_history(self, q):         return self._svc.search_history(q)
    def clear_history(self):             self._svc.clear_history()
    def get_download_dir(self) -> Path:  return self._cfg.download_dir
    def set_download_dir(self, p: Path): self._cfg.set("download_dir", str(p))

    def convert_to_mp4(self, source: Path, on_progress=None,
                       on_done=None, on_error=None) -> None:
        self._svc.convert_to_mp4(
            source=source,
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
        )
