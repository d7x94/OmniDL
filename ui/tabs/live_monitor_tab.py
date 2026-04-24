"""
ui/tabs/live_monitor_tab.py
Live Stream Monitor tab — watch multiple live URLs and auto-record
when they go live.

Architecture notes
──────────────────
• Zero changes to any existing file except main_window.py (2 lines).
• Uses ONLY ServiceFacade methods: analyse_url(), start_download(),
  get_task(), cancel_download(), config.cookie_file, config.download_dir.
  Never imports from infrastructure/ directly.
• Thread safety: _monitor_token (mirrors BatchTab._batch_token) guards
  all analyse callbacks against stale results from navigate-away/clear.
• Sequential analysis: at most ONE analyse_url() in flight at a time
  (_check_queue pattern). Prevents rate-limit hammering on Instagram/TikTok.
• Poll loop: self.after(_POLL_MS, self._poll) + winfo_ismapped() guard —
  same pattern as QueueTab. Does NOT poll when tab is hidden.
• Hard caps: MAX_MONITOR_URLS=20, MIN_CHECK_INTERVAL_S=15.
• Cookie age warning: reads Path(config.cookie_file).stat().st_mtime —
  no changes to ConfigManager.
"""
from __future__ import annotations

import logging
import queue
import time
import tkinter as tk
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import MediaInfo
from ui.themes.tokens import T
from utils.helpers import is_valid_url, open_folder, reveal_in_explorer
from utils.instagram_live_checker import (
    extract_instagram_username,
    is_instagram_profile_url,
)
from utils.tiktok_live_checker import (
    extract_tiktok_username,
    extract_tiktok_username_from_live_url,
    is_tiktok_profile_url,
)

if TYPE_CHECKING:
    from ui.main_window import MainWindow

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_MONITOR_URLS: int  = 20     # hard cap — prevents API spam
MIN_CHECK_INTERVAL_S   = 15     # minimum seconds between checks per URL
DEFAULT_CHECK_INTERVAL = 30     # default polling interval (seconds)
_POLL_MS               = 5_000  # UI poll cadence (ms)
_COOKIE_WARN_DAYS      = 7      # warn if cookie file older than this
MAX_CONSECUTIVE_FAILURES = 8    # escalate to ERROR after N consecutive check failures
_CHECKING_TIMEOUT_S    = 90     # max seconds a single check can be in-flight


# ── State machine ──────────────────────────────────────────────────────────────
class _MonitorState(Enum):
    WAITING   = auto()  # added, waiting for stream to start
    CHECKING  = auto()  # analyse_url() in flight right now
    LIVE      = auto()  # stream is live, starting/started download
    RECORDING = auto()  # DownloadTask active
    ENDED     = auto()  # task COMPLETED — file saved
    ERROR     = auto()  # auth / URL / network error


_STATE_LABEL: dict[_MonitorState, str] = {
    _MonitorState.WAITING:   "⏳ Chờ live",
    _MonitorState.CHECKING:  "🔍 Đang kiểm tra…",
    _MonitorState.LIVE:      "🔴 Đang LIVE",
    _MonitorState.RECORDING: "⏺ Đang ghi",
    _MonitorState.ENDED:     "✅ Đã ghi xong",
    _MonitorState.ERROR:     "❌ Lỗi",
}

_STATE_COLOR: dict[_MonitorState, str] = {
    _MonitorState.WAITING:   "text3",
    _MonitorState.CHECKING:  "primary_text",
    _MonitorState.LIVE:      "error",
    _MonitorState.RECORDING: "success",
    _MonitorState.ENDED:     "text2",
    _MonitorState.ERROR:     "error",
}


@dataclass
class _MonitorItem:
    """State for one monitored URL."""
    url:               str
    state:             _MonitorState = _MonitorState.WAITING
    media_info:        Optional[MediaInfo] = None
    task_id:           Optional[str] = None    # set when recording starts
    error_msg:         str  = ""
    last_check:        float = 0.0             # time.time() of last check
    added_at:          float = field(default_factory=time.time)
    # Profile watch — set when url is an Instagram or TikTok profile page
    is_profile_watch:  bool  = False           # True for profile URLs
    profile_platform:  str   = ""              # "instagram" | "tiktok" | ""
    username:          str   = ""              # e.g. "baki_babyboy"

    # Captured filename when recording completes — used by open_folder_btn
    filename: str = ""

    # Consecutive check failures — escalates to ERROR after MAX_CONSECUTIVE_FAILURES
    # to break the infinite WAITING loop caused by persistent transient errors.
    consecutive_failures: int = 0

    # BUG-TT-04 FIX: timestamp until which this item must not be checked again
    # due to a 429 rate-limit response. Set to time.time() + backoff_seconds.
    # _enqueue_next_check skips items where time.time() < rate_limited_until.
    rate_limited_until: float = 0.0

    # UI widgets — assigned after row is built
    row_frame:        Optional[ctk.CTkFrame]  = field(default=None, repr=False)
    state_lbl:        Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    title_lbl:        Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    platform_lbl:     Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    progress_lbl:     Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    cancel_btn:       Optional[ctk.CTkButton] = field(default=None, repr=False)
    open_folder_btn:  Optional[ctk.CTkButton] = field(default=None, repr=False)
    mp4_btn:          Optional[ctk.CTkButton] = field(default=None, repr=False)
    send_to_conv_btn: Optional[ctk.CTkButton] = field(default=None, repr=False)
    remove_btn:       Optional[ctk.CTkButton] = field(default=None, repr=False)
    cookie_warn:      Optional[ctk.CTkLabel]  = field(default=None, repr=False)
    # Convert state — True while FFmpeg is running for this item
    mp4_converting:   bool = False


# ── Main tab widget ────────────────────────────────────────────────────────────
class LiveMonitorTab(ctk.CTkFrame):
    """
    Live Stream Monitor tab.

    User adds live URLs → tab polls each URL periodically → auto-records
    when stream goes live → shows recording progress inline.

    All network calls go through ServiceFacade. No direct infrastructure
    imports. Fully isolated from all existing tabs and services.
    """

    def __init__(self, master, app: "MainWindow") -> None:
        super().__init__(master, fg_color=T.bg, corner_radius=0)
        self._app = app

        # ── State ──────────────────────────────────────────────────────────
        self._items:         list[_MonitorItem] = []
        self._monitor_token: int  = 0   # incremented on clear/remove to cancel stale callbacks
        self._checking:      bool = False  # True while analyse_url() is in flight

        # Thread-safe callback queue (Python 3.14: self.after() is not
        # callable from background threads).  Service callbacks post
        # callables here; _poll() drains them on the UI thread.
        self._ui_queue: queue.Queue = queue.Queue()

        self._build()
        T.register(self._on_theme)
        self._poll()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            hdr, text="Live Monitor",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=T.text,
        ).pack(side="left")

        self._status_lbl = ctk.CTkLabel(
            hdr, text="",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        )
        self._status_lbl.pack(side="left", padx=(14, 0))

        # Cookie warning banner (hidden by default)
        self._cookie_banner = ctk.CTkFrame(
            self, fg_color=T.warning_bg, corner_radius=8,
        )
        self._cookie_banner_lbl = ctk.CTkLabel(
            self._cookie_banner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=T.warning,
        )
        self._cookie_banner_lbl.pack(padx=14, pady=6)

        # URL input card
        input_card = ctk.CTkFrame(self, fg_color=T.surface, corner_radius=12)
        input_card.pack(fill="x", padx=24, pady=(14, 0))

        ctk.CTkLabel(
            input_card,
            text=f"Dán URL live stream — Instagram, YouTube, TikTok, Facebook, Twitch…"
                 f"  (tối đa {MAX_MONITOR_URLS})",
            font=ctk.CTkFont(size=11),
            text_color=T.text3,
        ).pack(anchor="w", padx=16, pady=(12, 4))

        entry_row = ctk.CTkFrame(input_card, fg_color="transparent")
        entry_row.pack(fill="x", padx=12, pady=(0, 12))

        self._url_entry = ctk.CTkEntry(
            entry_row,
            placeholder_text="https://www.instagram.com/username/live/",
            height=36, corner_radius=8,
            fg_color=T.input, border_color=T.border2,
            font=ctk.CTkFont(size=12), text_color=T.text,
        )
        self._url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._url_entry.bind("<Return>", lambda _: self._add_url())

        self._add_btn = ctk.CTkButton(
            entry_row, text="＋  Thêm",
            width=100, height=36, corner_radius=8,
            fg_color=T.primary, hover_color=T.primary_hover,
            text_color=T.primary_text, font=ctk.CTkFont(size=12),
            command=self._add_url,
        )
        self._add_btn.pack(side="left")

        # Interval setting row
        cfg_row = ctk.CTkFrame(input_card, fg_color="transparent")
        cfg_row.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkLabel(
            cfg_row, text="Kiểm tra mỗi:",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        ).pack(side="left")

        self._interval_var = tk.StringVar(value=str(DEFAULT_CHECK_INTERVAL))
        self._interval_menu = ctk.CTkOptionMenu(
            cfg_row,
            values=["15", "30", "60", "120", "300"],
            variable=self._interval_var,
            width=80, height=28, corner_radius=6,
            fg_color=T.surface2, button_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=11),
        )
        self._interval_menu.pack(side="left", padx=(6, 4))

        ctk.CTkLabel(
            cfg_row, text="giây",
            font=ctk.CTkFont(size=11), text_color=T.text3,
        ).pack(side="left")

        self._clear_all_btn = ctk.CTkButton(
            cfg_row, text="Xoá tất cả",
            width=90, height=28, corner_radius=6,
            fg_color=T.surface2, hover_color=T.error_bg,
            text_color=T.text3, font=ctk.CTkFont(size=11),
            command=self._clear_all,
        )
        self._clear_all_btn.pack(side="right")

        # Monitor list
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.scrollbar,
            scrollbar_button_hover_color=T.scrollbar_hover,
        )
        self._scroll.pack(fill="both", expand=True, padx=24, pady=(12, 20))

        self._empty_lbl = ctk.CTkLabel(
            self._scroll,
            text=(
                "Chưa có URL nào được theo dõi\n"
                "Dán URL live stream ở trên để bắt đầu tự động ghi"
            ),
            font=ctk.CTkFont(size=14), text_color=T.text3, justify="center",
        )
        self._empty_lbl.pack(expand=True, pady=80)

    # ── Add / Remove ───────────────────────────────────────────────────────────

    def _add_url(self) -> None:
        url = self._url_entry.get().strip()
        if not url:
            return
        if not is_valid_url(url):
            self._app.toast("URL không hợp lệ — phải bắt đầu bằng http:// hoặc https://", "error")
            return
        if len(self._items) >= MAX_MONITOR_URLS:
            self._app.toast(f"Đã đạt giới hạn {MAX_MONITOR_URLS} URL.", "error")
            return
        # Prevent duplicate active monitors
        active_states = {_MonitorState.WAITING, _MonitorState.CHECKING,
                         _MonitorState.LIVE, _MonitorState.RECORDING}
        if any(i.url == url and i.state in active_states for i in self._items):
            self._app.toast("URL này đang được theo dõi.", "info")
            return

        # Detect profile URL — supports Instagram and TikTok
        # BUG-CH FIX: pass proxy so vt/vm.tiktok.com short links are resolved
        # before the regex match; without this, short links return False and
        # fall through to analyse_url() which fails with "not currently live".
        _proxy            = self._app.config.proxy
        is_ig_profile     = is_instagram_profile_url(url)
        is_tiktok_profile = is_tiktok_profile_url(url, proxy=_proxy)

        # Route tiktok.com/@user/live URLs through the profile-watch path.
        # yt-dlp's TikTok live extractor calls webcast.tiktok.com which requires
        # a signed device-ID request — it returns "not currently live" even when
        # the stream IS live.  The profile-watch path fetches the profile page and
        # parses __NEXT_DATA__ JSON which is reliable and does not need signing.
        _tiktok_live_username: str = ""
        if not is_tiktok_profile:
            _tiktok_live_username = extract_tiktok_username_from_live_url(url) or ""
            if _tiktok_live_username:
                is_tiktok_profile = True

        is_profile = is_ig_profile or is_tiktok_profile

        if is_ig_profile:
            username         = extract_instagram_username(url) or ""
            profile_platform = "instagram"
        elif is_tiktok_profile:
            username         = _tiktok_live_username or extract_tiktok_username(url, proxy=_proxy) or ""
            profile_platform = "tiktok"
        else:
            username         = ""
            profile_platform = ""

        # Instagram profile watcher requires a cookie file
        if is_ig_profile and not self._app.config.cookie_file:
            self._app.toast(
                "Profile watcher cần cookie file Instagram.\n"
                "Cấu hình trong Settings → Network → Cookie file.",
                "error",
            )
            return

        item = _MonitorItem(
            url=url,
            is_profile_watch=is_profile,
            profile_platform=profile_platform,
            username=username,
        )
        self._items.append(item)
        self._url_entry.delete(0, "end")
        self._rebuild_item_ui(item)
        self._update_cookie_banner()
        self._update_status()

        if is_profile:
            logger.info(
                "LiveMonitor: added %s profile watch for @%s",
                profile_platform, username,
            )
            self._app.toast(
                f"Đang theo dõi @{username} — sẽ tự ghi khi live bắt đầu.",
                "info",
            )
        else:
            logger.info("LiveMonitor: added live URL %s", url)

    def _remove_item(self, item: _MonitorItem) -> None:
        """Remove item, cancel any in-progress download, invalidate callbacks."""
        self._monitor_token += 1  # invalidate all pending callbacks for this item
        if item.task_id:
            try:
                self._app.service.cancel_download(item.task_id)
            except Exception:
                pass
        if item in self._items:
            self._items.remove(item)
        if item.row_frame and item.row_frame.winfo_exists():
            item.row_frame.destroy()
        self._update_empty_state()
        self._update_status()

    def _clear_all(self) -> None:
        self._monitor_token += 1
        for item in list(self._items):
            if item.task_id:
                try:
                    self._app.service.cancel_download(item.task_id)
                except Exception:
                    pass
            if item.row_frame and item.row_frame.winfo_exists():
                item.row_frame.destroy()
        self._items.clear()
        self._checking = False
        self._update_empty_state()
        self._update_status()

    # ── UI row building ────────────────────────────────────────────────────────

    def _rebuild_item_ui(self, item: _MonitorItem) -> None:
        """Build the UI row for a monitor item."""
        if self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack_forget()

        row = ctk.CTkFrame(
            self._scroll, fg_color=T.surface,
            corner_radius=10, border_width=1, border_color=T.border,
        )
        row.pack(fill="x", pady=(0, 6))
        item.row_frame = row

        # Left: status indicator + info
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=14, pady=10)

        # Row 1: state label + platform
        top_row = ctk.CTkFrame(left, fg_color="transparent")
        top_row.pack(fill="x")

        item.state_lbl = ctk.CTkLabel(
            top_row,
            text=_STATE_LABEL[item.state],
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=getattr(T, _STATE_COLOR[item.state]),
        )
        item.state_lbl.pack(side="left")

        item.platform_lbl = ctk.CTkLabel(
            top_row, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
            fg_color=T.surface2, corner_radius=4, padx=6, pady=1,
        )
        item.platform_lbl.pack(side="left", padx=(8, 0))

        # Row 2: title / URL
        title_text = (
            f"@{item.username}  (profile watch)"
            if item.is_profile_watch
            else self._short_url(item.url)
        )
        item.title_lbl = ctk.CTkLabel(
            left,
            text=title_text,
            font=ctk.CTkFont(size=11),
            text_color=T.text2,
            anchor="w",
        )
        item.title_lbl.pack(fill="x", pady=(2, 0))

        # Row 3: progress / eta
        item.progress_lbl = ctk.CTkLabel(
            left, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.text3,
            anchor="w",
        )
        item.progress_lbl.pack(fill="x")

        # Row 4: cookie warning (hidden by default)
        item.cookie_warn = ctk.CTkLabel(
            left, text="",
            font=ctk.CTkFont(size=10),
            text_color=T.warning,
            anchor="w",
        )

        # Right: buttons
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.pack(side="right", padx=10, pady=10)

        item.cancel_btn = ctk.CTkButton(
            right, text="⏹",
            width=32, height=28, corner_radius=6,
            fg_color=T.surface2, hover_color=T.warning_bg,
            text_color=T.text3, font=ctk.CTkFont(size=12),
            command=lambda i=item: self._cancel_item(i),
        )
        item.cancel_btn.pack(side="left", padx=(0, 4))

        # Open-folder button — only visible when state == ENDED and filename set
        item.open_folder_btn = ctk.CTkButton(
            right, text="📂",
            width=32, height=28, corner_radius=6,
            fg_color=T.success_bg, hover_color=T.success_bg,
            text_color=T.success_text, font=ctk.CTkFont(size=12),
            command=lambda i=item: self._open_folder_for_item(i),
        )

        # → MP4 button — shown when ENDED + filename is a .ts file
        item.mp4_btn = ctk.CTkButton(
            right, text="→ MP4",
            width=56, height=28, corner_radius=6,
            fg_color=T.primary_dim, hover_color=T.surface3,
            text_color=T.primary_text, font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda i=item: self._start_mp4_convert(i),
        )
        # ⚙ Send-to-Convert button — opens Convert tab with this .ts file loaded
        item.send_to_conv_btn = ctk.CTkButton(
            right, text="⚙",
            width=32, height=28, corner_radius=6,
            fg_color=T.surface2, hover_color=T.border2,
            text_color=T.text2, font=ctk.CTkFont(size=13),
            command=lambda i=item: self._send_to_convert_tab(i),
        )
        # All hidden initially — shown in _refresh_item_ui when appropriate

        item.remove_btn = ctk.CTkButton(
            right, text="✕",
            width=32, height=28, corner_radius=6,
            fg_color=T.surface2, hover_color=T.error_bg,
            text_color=T.text3, font=ctk.CTkFont(size=12),
            command=lambda i=item: self._remove_item(i),
        )
        item.remove_btn.pack(side="left")

        self._refresh_item_ui(item)

    def _refresh_item_ui(self, item: _MonitorItem) -> None:
        """Update all widgets in item's row to reflect current state."""
        if not item.row_frame or not item.row_frame.winfo_exists():
            return

        state = item.state
        color_attr = _STATE_COLOR[state]
        color = getattr(T, color_attr, T.text3)

        if item.state_lbl and item.state_lbl.winfo_exists():
            item.state_lbl.configure(
                text=_STATE_LABEL[state],
                text_color=color,
            )

        if item.platform_lbl and item.platform_lbl.winfo_exists():
            platform = item.media_info.platform if item.media_info else ""
            if platform:
                item.platform_lbl.configure(text=f"  {platform}  ")
                if not item.platform_lbl.winfo_ismapped():
                    item.platform_lbl.pack(side="left", padx=(8, 0))
            else:
                item.platform_lbl.pack_forget()

        if item.title_lbl and item.title_lbl.winfo_exists():
            if item.media_info and item.media_info.title != "Unknown":
                title = item.media_info.title[:80]
            elif item.error_msg:
                title = item.error_msg[:80]
            else:
                title = self._short_url(item.url)
            item.title_lbl.configure(text=title)

        if item.progress_lbl and item.progress_lbl.winfo_exists():
            progress_text = self._get_progress_text(item)
            item.progress_lbl.configure(text=progress_text)
            if progress_text:
                if not item.progress_lbl.winfo_ismapped():
                    item.progress_lbl.pack(fill="x")
            else:
                item.progress_lbl.pack_forget()

        # Show cancel only when recording
        if item.cancel_btn and item.cancel_btn.winfo_exists():
            if state == _MonitorState.RECORDING:
                item.cancel_btn.configure(state="normal", text_color=T.warning)
            else:
                item.cancel_btn.configure(state="disabled", text_color=T.text3)

        # Show 📂 open-folder button only when ENDED and filename captured
        if item.open_folder_btn and item.open_folder_btn.winfo_exists():
            if state == _MonitorState.ENDED and item.filename:
                if not item.open_folder_btn.winfo_ismapped():
                    item.open_folder_btn.pack(side="left", padx=(4, 0))
            else:
                if item.open_folder_btn.winfo_ismapped():
                    item.open_folder_btn.pack_forget()

        # Show → MP4 and ⚙ buttons when ENDED + .ts file + not converting
        _show_ts_btns = (
            state == _MonitorState.ENDED
            and item.filename
            and item.filename.lower().endswith(".ts")
        )
        if item.mp4_btn and item.mp4_btn.winfo_exists():
            show_mp4 = _show_ts_btns and not item.mp4_converting
            if show_mp4:
                if not item.mp4_btn.winfo_ismapped():
                    item.mp4_btn.pack(side="left", padx=(4, 0))
            else:
                if item.mp4_btn.winfo_ismapped():
                    item.mp4_btn.pack_forget()

        # ⚙ Send-to-Convert — visible whenever .ts file ready (even during convert)
        if item.send_to_conv_btn and item.send_to_conv_btn.winfo_exists():
            if _show_ts_btns:
                if not item.send_to_conv_btn.winfo_ismapped():
                    item.send_to_conv_btn.pack(side="left", padx=(4, 0))
            else:
                if item.send_to_conv_btn.winfo_ismapped():
                    item.send_to_conv_btn.pack_forget()

        # Cookie warning per row
        if item.cookie_warn and item.cookie_warn.winfo_exists():
            age = self._cookie_age_days()
            if age is not None and age > _COOKIE_WARN_DAYS:
                warn_text = f"⚠ Cookie cũ {age} ngày — có thể bị lỗi auth"
                item.cookie_warn.configure(text=warn_text)
                if not item.cookie_warn.winfo_ismapped():
                    item.cookie_warn.pack(fill="x", pady=(2, 0))
            else:
                item.cookie_warn.pack_forget()

    def _get_progress_text(self, item: _MonitorItem) -> str:
        """Build the progress/status text for item row."""
        state = item.state
        if state == _MonitorState.WAITING:
            interval = self._current_interval()
            if item.last_check > 0:
                elapsed = int(time.time() - item.last_check)
                remaining = max(0, interval - elapsed)
                fail_hint = (
                    f"  (lỗi {item.consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                    if item.consecutive_failures > 0 else ""
                )
                return f"Kiểm tra lại sau {remaining}s{fail_hint}"
            return f"Kiểm tra mỗi {interval}s"
        if state == _MonitorState.CHECKING:
            return "Đang kiểm tra stream…"
        if state == _MonitorState.LIVE:
            return "Stream đang phát — đang khởi động ghi…"
        if state == _MonitorState.RECORDING and item.task_id:
            task = self._app.service.get_task(item.task_id)
            if task:
                snap = task.snapshot()
                parts = []
                if snap.get("eta"):
                    parts.append(snap["eta"])
                if snap.get("speed"):
                    parts.append(snap["speed"])
                if snap.get("downloaded_bytes", 0) > 0:
                    mb = snap["downloaded_bytes"] / 1024 / 1024
                    parts.append(f"{mb:.1f} MiB")
                return "  ·  ".join(parts) if parts else "Đang ghi…"
        if state == _MonitorState.ENDED:
            if item.task_id:
                task = self._app.service.get_task(item.task_id)
                if task and task.filename:
                    p = Path(task.filename)
                    return f"Đã lưu: {p.name}"
            return "Đã ghi xong"
        if state == _MonitorState.ERROR:
            return item.error_msg[:80] if item.error_msg else "Lỗi không xác định"
        return ""

    # ── Cookie helpers ─────────────────────────────────────────────────────────

    def _cookie_age_days(self) -> Optional[int]:
        """Return age in days of the configured cookie file, or None if not set."""
        try:
            cookie_path = self._app.config.cookie_file
            if not cookie_path:
                return None
            p = Path(cookie_path)
            if not p.is_file():
                return None
            age_s = time.time() - p.stat().st_mtime
            return int(age_s / 86400)
        except Exception:
            return None

    def _update_cookie_banner(self) -> None:
        """Show/hide top-level cookie age banner."""
        age = self._cookie_age_days()
        if age is not None and age > _COOKIE_WARN_DAYS and self._items:
            text = (
                f"⚠  Cookie file đã {age} ngày tuổi — Instagram/TikTok Live "
                f"có thể thất bại.  Refresh cookie trong Settings → Network."
            )
            self._cookie_banner_lbl.configure(text=text)
            if not self._cookie_banner.winfo_ismapped():
                self._cookie_banner.pack(fill="x", padx=24, pady=(8, 0))
        else:
            if self._cookie_banner.winfo_ismapped():
                self._cookie_banner.pack_forget()

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        """Main poll loop — runs every _POLL_MS on the UI thread."""
        if not self.winfo_exists():
            return

        try:
            self._poll_body()
        except tk.TclError:
            # Widget was destroyed mid-poll (e.g. theme switch or tab reinit).
            # Stop scheduling further callbacks — next tab init will restart.
            return

    def _poll_body(self) -> None:
        """Inner implementation of the poll loop, separated for TclError isolation."""
        # Drain thread-safe callback queue first (Python 3.14 thread-safety).
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    logger.warning("_ui_queue callback raised: %s", exc)
        except queue.Empty:
            pass

        # Don't poll when tab is hidden (saves CPU + API calls)
        if not self.winfo_ismapped():
            self.after(_POLL_MS, self._poll)
            return

        self._refresh_recording_items()
        self._recover_stuck_checks()   # unblock _checking if thread died silently
        self._enqueue_next_check()
        self._update_cookie_banner()   # refresh if user changed cookie in Settings
        self._update_status()
        self.after(_POLL_MS, self._poll)

    def _recover_stuck_checks(self) -> None:
        """Detect and recover from checks stuck in CHECKING state.

        If a background thread crashes without calling on_done/on_error
        (rare but possible), _checking stays True and no more checks run.
        Guard: if any item has been in CHECKING for > _CHECKING_TIMEOUT_S,
        release _checking and push the item back to WAITING.
        """
        now = time.time()
        for item in self._items:
            if item.state != _MonitorState.CHECKING:
                continue
            elapsed = now - item.last_check
            if elapsed > _CHECKING_TIMEOUT_S:
                logger.warning(
                    "LiveMonitor: check for %s stuck in CHECKING for %.0fs — "
                    "releasing lock and resetting to WAITING",
                    item.url, elapsed,
                )
                self._checking = False
                item.consecutive_failures += 1
                if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    item.state = _MonitorState.ERROR
                    item.error_msg = (
                        f"Kiểm tra bị treo {int(elapsed)}s. "
                        "Thử lại hoặc kiểm tra kết nối mạng."
                    )
                else:
                    item.state = _MonitorState.WAITING
                self._refresh_item_ui(item)
                break  # only fix one at a time per poll cycle

    def _refresh_recording_items(self) -> None:
        """Update progress display and detect stream end for RECORDING items."""
        for item in self._items:
            if item.state != _MonitorState.RECORDING:
                continue
            if not item.task_id:
                continue
            task = self._app.service.get_task(item.task_id)
            if task is None:
                # Task was cleared — treat as ended
                item.state = _MonitorState.ENDED
                self._refresh_item_ui(item)
                continue
            snap = task.snapshot()
            status = snap.get("status")
            if status == DownloadStatus.COMPLETED:
                item.state = _MonitorState.ENDED
                # Capture filename so open_folder_btn can reveal the file
                item.filename = snap.get("filename", "") or ""
                logger.info("LiveMonitor: recording ended for %s → %s",
                            item.url, item.filename or "(no path)")
            elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                item.state = _MonitorState.ERROR
                item.error_msg = snap.get("error_msg", "Download thất bại")
                logger.info("LiveMonitor: recording failed/cancelled for %s", item.url)
            self._refresh_item_ui(item)

    def _enqueue_next_check(self) -> None:
        """
        If no check is in flight, find the WAITING item most overdue
        for a check and trigger analyse_url() on it.

        Sequential: only 1 analyse_url() at a time to avoid rate-limiting.
        """
        if self._checking:
            return  # already one in flight

        now = time.time()
        interval = self._current_interval()

        # Find most-overdue WAITING item
        candidate = None
        oldest_check = float("inf")
        for item in self._items:
            if item.state != _MonitorState.WAITING:
                continue
            # BUG-TT-04 FIX: skip items that are in a rate-limit backoff window.
            if now < item.rate_limited_until:
                continue
            due = item.last_check + interval
            if now >= due and item.last_check < oldest_check:
                oldest_check = item.last_check
                candidate = item

        if candidate is None:
            return

        self._trigger_check(candidate)

    def _trigger_check(self, item: _MonitorItem) -> None:
        """Start one check for item.

        - Instagram profile: calls service.check_profile_live()        → Instagram API
        - TikTok profile:    calls service.check_tiktok_profile_live() → TikTok API
        - Live URL:          calls service.analyse_url()               → yt-dlp
        Sets _checking=True (sequential lock).
        """
        token = self._monitor_token
        item.state = _MonitorState.CHECKING
        item.last_check = time.time()
        self._checking = True
        self._refresh_item_ui(item)

        if item.is_profile_watch:
            url = item.url

            def on_done(live_url: "Optional[str]") -> None:
                self._ui_queue.put(lambda u=live_url: self._on_profile_check_done(
                    item, u, token
                ))

            def on_error(err: str) -> None:
                self._ui_queue.put(lambda e=err: self._on_check_error(item, e, token))

            if item.profile_platform == "tiktok":
                # TikTok profile watcher — public API, no cookie needed
                self._app.service.check_tiktok_profile_live(
                    url=url, on_done=on_done, on_error=on_error
                )
                logger.debug("LiveMonitor: TikTok profile check @%s", item.username)
            else:
                # Instagram profile watcher — private API, cookie required
                self._app.service.check_profile_live(
                    url=url, on_done=on_done, on_error=on_error
                )
                logger.debug("LiveMonitor: Instagram profile check @%s", item.username)
        else:
            # Regular live URL — use existing yt-dlp analyse_url path
            url = item.url

            def on_done_analyse(info: MediaInfo) -> None:
                self._ui_queue.put(lambda i=info: self._on_check_done(item, i, token))

            def on_error_analyse(err: str) -> None:
                self._ui_queue.put(lambda e=err: self._on_check_error(item, e, token))

            self._app.service.analyse_url(
                url=url, on_done=on_done_analyse, on_error=on_error_analyse
            )
            logger.debug("LiveMonitor: checking live URL %s", url)

    def _on_check_done(self, item: _MonitorItem, info: MediaInfo, token: int) -> None:
        """Callback when analyse_url() succeeds (live URL path)."""
        self._checking = False  # release sequential lock

        if token != self._monitor_token:
            return  # stale — item was removed/cleared

        item.media_info = info
        item.consecutive_failures = 0  # reset on any successful API response

        if info.is_live:
            logger.info("LiveMonitor: LIVE detected — %s", item.url)
            item.state = _MonitorState.LIVE
            self._refresh_item_ui(item)
            self._start_recording(item, token)
        else:
            # Not live yet — back to WAITING
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)

    def _on_profile_check_done(
        self,
        item: _MonitorItem,
        live_url: "Optional[str]",
        token: int,
    ) -> None:
        """Callback when check_profile_live() completes (profile watch path).

        live_url is the full live URL if currently live, None if not live.
        When live is detected:
          1. Store live_url in item so start_recording() uses it
          2. Transition to LIVE → trigger analyse_url on the live URL
             to get a proper MediaInfo before start_download()
        """
        self._checking = False

        if token != self._monitor_token:
            return

        if live_url:
            logger.info(
                "LiveMonitor: profile @%s is LIVE → %s",
                item.username, live_url,
            )
            item.consecutive_failures = 0
            item.state = _MonitorState.LIVE
            self._refresh_item_ui(item)
            # Get MediaInfo for the live URL so start_download() has full metadata
            self._checking = True  # block next poll until analyse completes

            def on_done(info: MediaInfo) -> None:
                self._ui_queue.put(lambda i=info: self._on_live_url_analysed(
                    item, i, live_url, token
                ))

            def on_error(err: str) -> None:
                # analyse failed but we know it's live — start with minimal info
                self._ui_queue.put(lambda e=err: self._on_live_url_analyse_fallback(
                    item, live_url, token, e
                ))

            self._app.service.analyse_url(
                url=live_url, on_done=on_done, on_error=on_error
            )
        else:
            # Not live yet — back to WAITING
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)

    def _on_live_url_analysed(
        self,
        item: _MonitorItem,
        info: MediaInfo,
        live_url: str,
        token: int,
    ) -> None:
        """Called when analyse_url() succeeds on the live URL after profile check."""
        self._checking = False

        if token != self._monitor_token:
            return

        item.media_info = info
        item.url = live_url  # switch item URL to live URL for recording
        self._start_recording(item, token)

    def _on_live_url_analyse_fallback(
        self,
        item: _MonitorItem,
        live_url: str,
        token: int,
        err: str,
    ) -> None:
        """Called when analyse_url() fails on the live URL after profile check.

        Constructs a minimal MediaInfo and starts recording anyway —
        the live URL is confirmed via the profile API check.
        """
        self._checking = False

        if token != self._monitor_token:
            return

        logger.warning(
            "LiveMonitor: analyse_url failed for live %s (%s) — "
            "starting with minimal MediaInfo",
            live_url, err[:60],
        )
        from domain.models.download_task import MediaInfo as _MI
        item.media_info = _MI(
            url=live_url,
            title=f"@{item.username} Live",
            uploader=item.username,
            duration=0,
            platform=item.profile_platform or "unknown",
            formats=[],
            is_live=True,
        )
        item.url = live_url
        self._start_recording(item, token)

    def _on_check_error(self, item: _MonitorItem, err: str, token: int) -> None:
        """Callback when analyse_url() fails."""
        self._checking = False

        if token != self._monitor_token:
            return

        # Distinguish hard errors (auth, removed) from transient (network, rate limit)
        err_l = err.lower()
        # "not currently live" means the channel is simply offline — not an error.
        # Reset consecutive_failures so a channel that goes offline doesn't
        # accumulate failures and eventually escalate to ERROR state.
        if "not currently live" in err_l:
            item.consecutive_failures = 0
            item.error_msg = ""
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)
            return

        # BUG-TT-04 FIX: TikTok 429 rate-limit — apply exponential backoff
        # instead of immediately retrying on the next poll cycle (which re-hits
        # the 429 and creates an infinite error loop).
        # Backoff: 5 min * 2^(failures-1), capped at 30 min.
        if "blocked" in err_l and ("rate" in err_l or "429" in err_l):
            _failures = item.consecutive_failures + 1
            _backoff_s = min(300 * (2 ** (_failures - 1)), 1800)
            item.rate_limited_until = time.time() + _backoff_s
            item.consecutive_failures = _failures
            item.state = _MonitorState.WAITING
            item.error_msg = ""
            logger.warning(
                "LiveMonitor: TikTok 429 for @%s — backoff %.0fs (failure #%d)",
                item.username or item.url[:40], _backoff_s, _failures,
            )
            self._refresh_item_ui(item)
            return

        hard = any(k in err_l for k in (
            "private", "not found", "404", "login", "checkpoint",
            "unsupported url", "removed", "not available",  # BUG-CD: impersonate target missing in EXE
        ))
        if hard:
            item.state = _MonitorState.ERROR
            item.error_msg = err[:120]
            logger.warning("LiveMonitor: hard error for %s: %s", item.url, err[:80])
        else:
            # Transient error — increment failure counter.
            # After MAX_CONSECUTIVE_FAILURES, escalate to ERROR so the user
            # sees a clear message instead of an infinite WAITING loop.
            item.consecutive_failures += 1
            if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                item.state = _MonitorState.ERROR
                item.error_msg = (
                    f"Đã thử {item.consecutive_failures} lần thất bại. "
                    f"Lỗi cuối: {err[:80]}\n"
                    "Kiểm tra cookie Instagram hoặc kết nối mạng, rồi nhấn ✕ và thêm lại URL."
                )
                logger.warning(
                    "LiveMonitor: escalating to ERROR after %d failures for %s: %s",
                    item.consecutive_failures, item.url, err[:60],
                )
            else:
                # Still within retry budget — stay WAITING
                item.state = _MonitorState.WAITING
                item.error_msg = ""
                logger.debug(
                    "LiveMonitor: transient error %d/%d for %s: %s",
                    item.consecutive_failures, MAX_CONSECUTIVE_FAILURES,
                    item.url, err[:60],
                )

        self._refresh_item_ui(item)

    def _start_recording(self, item: _MonitorItem, token: int) -> None:
        """
        Start a download task for a live stream.

        Uses format="best" (combined HLS stream) — required for live.
        output_ext="ts" matches hls_use_mpegts=True in YtDlpEngine.
        """
        if token != self._monitor_token:
            return
        if item.media_info is None:
            return

        # Guard: don't start if already recording this URL
        for existing in self._items:
            if (existing is not item
                    and existing.url == item.url
                    and existing.state == _MonitorState.RECORDING):
                logger.info("LiveMonitor: duplicate recording guard triggered for %s", item.url)
                item.state = _MonitorState.WAITING
                self._refresh_item_ui(item)
                return

        try:
            task = self._app.service.start_download(
                url=item.url,
                media_info=item.media_info,
                format_id="best",
                output_ext="ts",
            )
            item.task_id = task.id
            item.state = _MonitorState.RECORDING
            logger.info("LiveMonitor: started recording task %s for %s", task.id, item.url)
        except Exception as exc:
            item.state = _MonitorState.ERROR
            item.error_msg = str(exc)[:120]
            logger.error("LiveMonitor: failed to start recording for %s: %s", item.url, exc)

        self._refresh_item_ui(item)

    # ── Cancel ─────────────────────────────────────────────────────────────────

    def _open_folder_for_item(self, item: _MonitorItem) -> None:
        """Open Explorer/Finder and highlight the recorded file."""
        if not item.filename:
            return
        p = Path(item.filename)
        if not p.is_absolute() and item.task_id:
            task = self._app.service.get_task(item.task_id)
            if task and task.output_dir:
                p = (Path(task.output_dir) / p).resolve()
        if p.is_file():
            if not reveal_in_explorer(p):
                open_folder(p.parent)
        elif p.parent.is_dir():
            open_folder(p.parent)

    def _start_mp4_convert(self, item: _MonitorItem) -> None:
        """Start FFmpeg conversion of item.filename (.ts → .mp4).

        Uses self._ui_queue.put() for all callbacks — required for
        Python 3.14 thread-safety (self.after() not callable from bg threads).
        """
        if item.mp4_converting or not item.filename:
            return
        src_path = Path(item.filename)
        if not src_path.is_file():
            return

        item.mp4_converting = True
        if item.mp4_btn and item.mp4_btn.winfo_exists():
            item.mp4_btn.configure(state="disabled", text="Converting…")

        def _on_progress(pct: float) -> None:
            # post to UI thread via _ui_queue (Python 3.14 safe)
            self._ui_queue.put(lambda p=pct, i=item: _update_progress(i, p))

        def _update_progress(i: "_MonitorItem", pct: float) -> None:
            if i.mp4_btn and i.mp4_btn.winfo_exists():
                label = f"{int(pct)}%"
                i.mp4_btn.configure(text=label)

        def _on_done(out_path: "Path") -> None:
            self._ui_queue.put(lambda i=item, p=out_path: _finish_convert(i, p))

        def _finish_convert(i: "_MonitorItem", out_path: "Path") -> None:
            i.mp4_converting = False
            i.filename = str(out_path)   # point to the new .mp4
            self._refresh_item_ui(i)
            if i.mp4_btn and i.mp4_btn.winfo_exists():
                i.mp4_btn.configure(state="normal", text="→ MP4")
            logger.info("LiveMonitor: converted %s → %s", src_path.name, out_path.name)

        def _on_error(msg: str) -> None:
            self._ui_queue.put(lambda i=item, m=msg: _fail_convert(i, m))

        def _fail_convert(i: "_MonitorItem", msg: str) -> None:
            i.mp4_converting = False
            if i.mp4_btn and i.mp4_btn.winfo_exists():
                i.mp4_btn.configure(state="normal", text="→ MP4")
            logger.warning("LiveMonitor: convert failed for %s: %s", src_path.name, msg[:80])

        self._app.service.convert_to_mp4(
            src_path,
            on_progress=_on_progress,
            on_done=_on_done,
            on_error=_on_error,
        )

    def _send_to_convert_tab(self, item: _MonitorItem) -> None:
        """Add item.filename to Convert Tab and navigate there.

        Lets the user choose preset, quality, GPU encoder and other
        options that _start_mp4_convert() (the quick-convert button)
        does not expose.

        Safe to call while _start_mp4_convert() is running — the two
        operations are independent.  The .ts file will appear in the
        Convert Tab job list and the user can configure the encode
        before pressing Convert All.
        """
        if not item.filename:
            return
        path = Path(item.filename)
        if not path.is_file():
            self._app.toast("File .ts không tìm thấy.", "error")
            return

        # Get ConvertTab reference via main_window
        convert_tab = self._app.get_tab("convert")
        if convert_tab is None:
            self._app.toast("Convert Tab không khả dụng.", "error")
            return

        # _add_file is UI-thread method — safe to call directly here
        # (this method is always called from UI thread via button command)
        convert_tab._add_file(path)

        # Navigate to Convert Tab so user sees the file was added
        self._app.navigate_to("convert")
        logger.info("LiveMonitor: sent %s to Convert Tab", path.name)

    def _cancel_item(self, item: _MonitorItem) -> None:
        """Cancel an active recording and return item to WAITING state."""
        if item.task_id:
            try:
                self._app.service.cancel_download(item.task_id)
            except Exception as exc:
                logger.warning("LiveMonitor: cancel failed: %s", exc)
        item.task_id = None
        item.state = _MonitorState.WAITING
        item.last_check = 0.0  # reset so it re-checks soon
        self._refresh_item_ui(item)

    # ── Status / helpers ───────────────────────────────────────────────────────

    def _update_status(self) -> None:
        if not self.winfo_exists():
            return
        waiting   = sum(1 for i in self._items if i.state == _MonitorState.WAITING)
        recording = sum(1 for i in self._items if i.state == _MonitorState.RECORDING)
        ended     = sum(1 for i in self._items if i.state == _MonitorState.ENDED)
        parts = []
        if recording:
            parts.append(f"⏺ {recording} đang ghi")
        if waiting:
            parts.append(f"⏳ {waiting} đang chờ")
        if ended:
            parts.append(f"✅ {ended} đã xong")
        if self._status_lbl.winfo_exists():
            self._status_lbl.configure(text="  ·  ".join(parts))

    def _update_empty_state(self) -> None:
        if not self._items:
            if not self._empty_lbl.winfo_ismapped():
                self._empty_lbl.pack(expand=True, pady=80)
        else:
            if self._empty_lbl.winfo_ismapped():
                self._empty_lbl.pack_forget()

    def _current_interval(self) -> int:
        try:
            v = int(self._interval_var.get())
            return max(MIN_CHECK_INTERVAL_S, v)
        except (ValueError, tk.TclError):
            return DEFAULT_CHECK_INTERVAL

    @staticmethod
    def _short_url(url: str, max_len: int = 70) -> str:
        return url if len(url) <= max_len else f"{url[:max_len - 1]}…"

    # ── Theme ──────────────────────────────────────────────────────────────────

    def _on_theme(self) -> None:
        if not self.winfo_exists():
            return
        self.configure(fg_color=T.bg)
        if self._scroll.winfo_exists():
            self._scroll.configure(
                fg_color="transparent",
                scrollbar_button_color=T.scrollbar,
                scrollbar_button_hover_color=T.scrollbar_hover,
            )
        if self._status_lbl.winfo_exists():
            self._status_lbl.configure(text_color=T.text3)
        if self._url_entry.winfo_exists():
            self._url_entry.configure(fg_color=T.input, border_color=T.border2)
        if self._add_btn.winfo_exists():
            self._add_btn.configure(
                fg_color=T.primary, hover_color=T.primary_hover,
                text_color=T.primary_text,
            )
        if self._clear_all_btn.winfo_exists():
            self._clear_all_btn.configure(
                fg_color=T.surface2, hover_color=T.error_bg, text_color=T.text3,
            )
        if self._interval_menu.winfo_exists():
            self._interval_menu.configure(
                fg_color=T.surface2, button_color=T.border2, text_color=T.text2,
            )
        if self._cookie_banner.winfo_exists():
            self._cookie_banner.configure(fg_color=T.warning_bg)
        if self._cookie_banner_lbl.winfo_exists():
            self._cookie_banner_lbl.configure(text_color=T.warning)
        if self._empty_lbl.winfo_exists():
            self._empty_lbl.configure(text_color=T.text3)
        # Refresh all item rows
        for item in self._items:
            if item.row_frame and item.row_frame.winfo_exists():
                item.row_frame.configure(fg_color=T.surface, border_color=T.border)
            if item.state_lbl and item.state_lbl.winfo_exists():
                color_attr = _STATE_COLOR[item.state]
                item.state_lbl.configure(text_color=getattr(T, color_attr, T.text3))
            if item.title_lbl and item.title_lbl.winfo_exists():
                item.title_lbl.configure(text_color=T.text2)
            if item.progress_lbl and item.progress_lbl.winfo_exists():
                item.progress_lbl.configure(text_color=T.text3)
            if item.platform_lbl and item.platform_lbl.winfo_exists():
                item.platform_lbl.configure(fg_color=T.surface2, text_color=T.text3)
            if item.cookie_warn and item.cookie_warn.winfo_exists():
                item.cookie_warn.configure(text_color=T.warning)
            if item.cancel_btn and item.cancel_btn.winfo_exists():
                item.cancel_btn.configure(fg_color=T.surface2, hover_color=T.warning_bg)
            if item.open_folder_btn and item.open_folder_btn.winfo_exists():
                item.open_folder_btn.configure(
                    fg_color=T.success_bg, hover_color=T.success_bg,
                    text_color=T.success_text,
                )
            if item.mp4_btn and item.mp4_btn.winfo_exists():
                item.mp4_btn.configure(
                    fg_color=T.primary_dim, hover_color=T.surface3,
                    text_color=T.primary_text,
                )
            if item.send_to_conv_btn and item.send_to_conv_btn.winfo_exists():
                item.send_to_conv_btn.configure(
                    fg_color=T.surface2, hover_color=T.border2,
                    text_color=T.text2,
                )
            if item.remove_btn and item.remove_btn.winfo_exists():
                item.remove_btn.configure(fg_color=T.surface2, hover_color=T.error_bg)
