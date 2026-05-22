"""Live Stream Monitor tab."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import MediaInfo
from ui.signals import ui_bridge
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

MAX_MONITOR_URLS: int = 20
MIN_CHECK_INTERVAL_S = 15
DEFAULT_CHECK_INTERVAL = 30
_POLL_MS = 5_000
_COOKIE_WARN_DAYS = 7
MAX_CONSECUTIVE_FAILURES = 8
_CHECKING_TIMEOUT_S = 90


class _MonitorState(Enum):
    WAITING = auto()
    CHECKING = auto()
    LIVE = auto()
    RECORDING = auto()
    ENDED = auto()
    ERROR = auto()


_STATE_LABEL: dict[_MonitorState, str] = {
    _MonitorState.WAITING: "Chờ live",
    _MonitorState.CHECKING: "Đang kiểm tra…",
    _MonitorState.LIVE: "Đang LIVE",
    _MonitorState.RECORDING: "Đang ghi",
    _MonitorState.ENDED: "Đã ghi xong",
    _MonitorState.ERROR: "Lỗi",
}

_STATE_COLOR: dict[_MonitorState, str] = {
    _MonitorState.WAITING: "text3",
    _MonitorState.CHECKING: "primary_text",
    _MonitorState.LIVE: "error",
    _MonitorState.RECORDING: "success",
    _MonitorState.ENDED: "text2",
    _MonitorState.ERROR: "error",
}


@dataclass
class _MonitorItem:
    url: str
    state: _MonitorState = _MonitorState.WAITING
    media_info: Optional[MediaInfo] = None
    task_id: Optional[str] = None
    error_msg: str = ""
    last_check: float = 0.0
    added_at: float = field(default_factory=time.time)
    is_profile_watch: bool = False
    profile_platform: str = ""
    username: str = ""
    filename: str = ""
    consecutive_failures: int = 0
    rate_limited_until: float = 0.0
    mp4_converting: bool = False
    # UI widgets
    row_frame: Optional[QFrame] = field(default=None, repr=False)
    state_lbl: Optional[QLabel] = field(default=None, repr=False)
    title_lbl: Optional[QLabel] = field(default=None, repr=False)
    platform_lbl: Optional[QLabel] = field(default=None, repr=False)
    progress_lbl: Optional[QLabel] = field(default=None, repr=False)
    cancel_btn: Optional[QPushButton] = field(default=None, repr=False)
    open_folder_btn: Optional[QPushButton] = field(default=None, repr=False)
    mp4_btn: Optional[QPushButton] = field(default=None, repr=False)
    send_to_conv_btn: Optional[QPushButton] = field(default=None, repr=False)
    remove_btn: Optional[QPushButton] = field(default=None, repr=False)
    cookie_warn: Optional[QLabel] = field(default=None, repr=False)


class LiveMonitorTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._items: list[_MonitorItem] = []
        self._monitor_token: int = 0
        self._checking: bool = False
        self._build()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_anim = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background: transparent;")
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(28, 24, 28, 0)
        title = QLabel("Theo dõi trực tiếp")
        title.setObjectName("page_title")
        hdr_layout.addWidget(title)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        hdr_layout.addWidget(self._status_lbl)
        hdr_layout.addStretch()
        layout.addWidget(hdr)

        # Cookie warning banner (hidden by default)
        self._cookie_banner = QFrame()
        self._cookie_banner.setStyleSheet(f"""
            QFrame {{
                background-color: {T.warning_bg};
                border-radius: 8px;
                margin: 0 28px;
            }}
        """)
        banner_layout = QHBoxLayout(self._cookie_banner)
        banner_layout.setContentsMargins(14, 6, 14, 6)
        self._cookie_banner_lbl = QLabel("")
        self._cookie_banner_lbl.setStyleSheet(f"color: {T.warning}; font-size: 11px;")
        banner_layout.addWidget(self._cookie_banner_lbl)
        self._cookie_banner.hide()
        layout.addWidget(self._cookie_banner)

        # URL input card
        input_wrap = QWidget()
        input_wrap.setStyleSheet("background: transparent;")
        iw_layout = QVBoxLayout(input_wrap)
        iw_layout.setContentsMargins(28, 14, 28, 0)

        input_card = QFrame()
        input_card.setStyleSheet(f"""
            QFrame {{
                background-color: {T.surface};
                border: 1px solid {T.border};
                border-radius: 12px;
            }}
            QLabel {{ border: none; }}
        """)
        ic_layout = QVBoxLayout(input_card)
        ic_layout.setContentsMargins(16, 12, 16, 12)
        ic_layout.setSpacing(8)

        hint = QLabel(
            f"Dán URL live stream — Instagram, YouTube, TikTok, Facebook, Twitch…"
            f"  (tối đa {MAX_MONITOR_URLS})"
        )
        hint.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        ic_layout.addWidget(hint)

        entry_row = QWidget()
        entry_row.setStyleSheet("background: transparent;")
        er_layout = QHBoxLayout(entry_row)
        er_layout.setContentsMargins(0, 0, 0, 0)
        er_layout.setSpacing(8)

        self._url_entry = QLineEdit()
        self._url_entry.setPlaceholderText("https://www.instagram.com/username/live/")
        self._url_entry.setFixedHeight(36)
        self._url_entry.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.input};
                border: 1px solid {T.border2};
                border-radius: 8px;
                color: {T.text};
                font-size: 12px;
                padding: 0 8px;
            }}
        """)
        self._url_entry.returnPressed.connect(self._add_url)
        er_layout.addWidget(self._url_entry, 1)

        self._add_btn = QPushButton("Thêm")
        self._add_btn.setFixedSize(100, 36)
        self._add_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 8px; font-size: 12px;"
        )
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(self._add_url)
        er_layout.addWidget(self._add_btn)

        ic_layout.addWidget(entry_row)

        cfg_row = QWidget()
        cfg_row.setStyleSheet("background: transparent;")
        cr_layout = QHBoxLayout(cfg_row)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.setSpacing(6)

        ck_lbl = QLabel("Kiểm tra mới:")
        ck_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        cr_layout.addWidget(ck_lbl)

        self._interval_combo = QComboBox()
        self._interval_combo.addItems(["15", "30", "60", "120", "300"])
        self._interval_combo.setCurrentText(str(DEFAULT_CHECK_INTERVAL))
        self._interval_combo.setFixedSize(80, 28)
        cr_layout.addWidget(self._interval_combo)

        s_lbl = QLabel("giây")
        s_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        cr_layout.addWidget(s_lbl)
        cr_layout.addStretch()

        clear_all_btn = QPushButton("Xóa tất cả")
        clear_all_btn.setFixedSize(90, 28)
        clear_all_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 11px;"
        )
        clear_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_all_btn.clicked.connect(self._clear_all)
        cr_layout.addWidget(clear_all_btn)

        ic_layout.addWidget(cfg_row)
        iw_layout.addWidget(input_card)
        layout.addWidget(input_wrap)

        # Scroll area for monitor items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setStyleSheet("background: transparent;")
        self._items_layout = QVBoxLayout(self._scroll_content)
        self._items_layout.setContentsMargins(28, 12, 28, 20)
        self._items_layout.setSpacing(6)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._empty_lbl = QLabel(
            "Chưa có URL nào được theo dõi\nDán URL live stream ở trên để bắt đầu tự động ghi"
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(f"color: {T.text3}; font-size: 14px; background: transparent;")
        self._empty_lbl.setMinimumHeight(160)
        self._items_layout.addWidget(self._empty_lbl)

        scroll.setWidget(self._scroll_content)
        layout.addWidget(scroll, 1)

    # ── Add / Remove ───────────────────────────────────────────────────────────

    def _add_url(self) -> None:
        url = self._url_entry.text().strip()
        if not url:
            return
        if not is_valid_url(url):
            self._app.toast("URL không hợp lệ — phải bắt đầu bằng http:// hoặc https://", "error")
            return
        if len(self._items) >= MAX_MONITOR_URLS:
            self._app.toast(f"Đã đạt giới hạn {MAX_MONITOR_URLS} URL.", "error")
            return

        active_states = {
            _MonitorState.WAITING,
            _MonitorState.CHECKING,
            _MonitorState.LIVE,
            _MonitorState.RECORDING,
        }
        if any(i.url == url and i.state in active_states for i in self._items):
            self._app.toast("URL này đang được theo dõi.", "info")
            return

        _proxy = self._app.config.proxy
        is_ig_profile = is_instagram_profile_url(url)
        is_tiktok_profile = is_tiktok_profile_url(url, proxy=_proxy)

        _tiktok_live_username: str = ""
        if not is_tiktok_profile:
            _tiktok_live_username = extract_tiktok_username_from_live_url(url) or ""
            if _tiktok_live_username:
                is_tiktok_profile = True

        is_profile = is_ig_profile or is_tiktok_profile

        if is_ig_profile:
            username = extract_instagram_username(url) or ""
            profile_platform = "instagram"
        elif is_tiktok_profile:
            username = _tiktok_live_username or extract_tiktok_username(url, proxy=_proxy) or ""
            profile_platform = "tiktok"
        else:
            username = ""
            profile_platform = ""

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
        self._url_entry.clear()
        self._rebuild_item_ui(item)
        self._update_cookie_banner()
        self._update_status()

        if is_profile:
            self._app.toast(f"Đang theo dõi @{username} — sẽ tự ghi khi live bắt đầu.", "info")

    def _remove_item(self, item: _MonitorItem) -> None:
        self._monitor_token += 1
        if item.task_id:
            try:
                self._app.service.cancel_download(item.task_id)
            except Exception:
                pass
        if item in self._items:
            self._items.remove(item)
        if item.row_frame:
            self._items_layout.removeWidget(item.row_frame)
            item.row_frame.deleteLater()
            item.row_frame = None
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
            if item.row_frame:
                self._items_layout.removeWidget(item.row_frame)
                item.row_frame.deleteLater()
                item.row_frame = None
        self._items.clear()
        self._checking = False
        self._update_empty_state()
        self._update_status()

    # ── UI row building ────────────────────────────────────────────────────────

    def _rebuild_item_ui(self, item: _MonitorItem) -> None:
        if self._empty_lbl.isVisible():
            self._empty_lbl.hide()

        row = QFrame()
        row.setStyleSheet(f"""
            QFrame#monitorRow {{
                background-color: {T.surface};
                border: 1px solid {T.border};
                border-radius: 12px;
            }}
        """)
        row.setObjectName("monitorRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(14, 10, 10, 10)
        row_layout.setSpacing(8)
        item.row_frame = row

        # Left info panel
        left = QWidget()
        left.setStyleSheet("background: transparent;")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        # Top row: state label + platform badge
        top_row = QWidget()
        top_row.setStyleSheet("background: transparent;")
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        color_attr = _STATE_COLOR[item.state]
        color = getattr(T, color_attr, T.text3)

        state_lbl = QLabel(_STATE_LABEL[item.state])
        state_lbl.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: bold; background: transparent;"
        )
        top_layout.addWidget(state_lbl)
        item.state_lbl = state_lbl

        platform_lbl = QLabel("")
        platform_lbl.setStyleSheet(
            f"color: {T.text3}; background-color: {T.surface2}; border-radius: 4px; font-size: 10px; padding: 1px 6px;"
        )
        platform_lbl.hide()
        top_layout.addWidget(platform_lbl)
        item.platform_lbl = platform_lbl

        top_layout.addStretch()
        left_layout.addWidget(top_row)

        # Title / URL label
        title_text = (
            f"@{item.username}  (profile watch)" if item.is_profile_watch else self._short_url(item.url)
        )
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(f"color: {T.text2}; font-size: 11px; background: transparent;")
        left_layout.addWidget(title_lbl)
        item.title_lbl = title_lbl

        # Progress label
        progress_lbl = QLabel("")
        progress_lbl.setStyleSheet(f"color: {T.text3}; font-size: 10px; background: transparent;")
        progress_lbl.hide()
        left_layout.addWidget(progress_lbl)
        item.progress_lbl = progress_lbl

        # Cookie warning
        cookie_warn = QLabel("")
        cookie_warn.setStyleSheet(f"color: {T.warning}; font-size: 10px; background: transparent;")
        cookie_warn.hide()
        left_layout.addWidget(cookie_warn)
        item.cookie_warn = cookie_warn

        row_layout.addWidget(left, 1)

        # Right button panel
        right = QWidget()
        right.setStyleSheet("background: transparent;")
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        cancel_btn = QPushButton("Dừng")
        cancel_btn.setFixedSize(46, 28)
        cancel_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 12px;"
        )
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(lambda _=False, i=item: self._cancel_item(i))
        right_layout.addWidget(cancel_btn)
        item.cancel_btn = cancel_btn

        open_folder_btn = QPushButton("Mở")
        open_folder_btn.setFixedSize(46, 28)
        open_folder_btn.setStyleSheet(
            f"background: {T.success_bg}; color: {T.success_text}; border: none; border-radius: 8px; font-size: 12px;"
        )
        open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder_btn.clicked.connect(lambda _=False, i=item: self._open_folder_for_item(i))
        open_folder_btn.hide()
        right_layout.addWidget(open_folder_btn)
        item.open_folder_btn = open_folder_btn

        mp4_btn = QPushButton("MP4")
        mp4_btn.setFixedSize(46, 28)
        mp4_btn.setStyleSheet(
            f"background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 6px; font-size: 10px; font-weight: bold;"
        )
        mp4_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mp4_btn.clicked.connect(lambda _=False, i=item: self._start_mp4_convert(i))
        mp4_btn.hide()
        right_layout.addWidget(mp4_btn)
        item.mp4_btn = mp4_btn

        send_to_conv_btn = QPushButton("Chuyển")
        send_to_conv_btn.setFixedSize(46, 28)
        send_to_conv_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 13px;"
        )
        send_to_conv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_to_conv_btn.clicked.connect(lambda _=False, i=item: self._send_to_convert_tab(i))
        send_to_conv_btn.hide()
        right_layout.addWidget(send_to_conv_btn)
        item.send_to_conv_btn = send_to_conv_btn

        remove_btn = QPushButton("x")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet(
            f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 12px;"
        )
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(lambda _=False, i=item: self._remove_item(i))
        right_layout.addWidget(remove_btn)
        item.remove_btn = remove_btn

        row_layout.addWidget(right)
        self._items_layout.addWidget(row)
        self._refresh_item_ui(item)

    def _refresh_item_ui(self, item: _MonitorItem) -> None:
        if not item.row_frame:
            return

        state = item.state
        color_attr = _STATE_COLOR[state]
        color = getattr(T, color_attr, T.text3)

        if item.state_lbl:
            item.state_lbl.setText(_STATE_LABEL[state])
            item.state_lbl.setStyleSheet(
                f"color: {color}; font-size: 12px; font-weight: bold; background: transparent;"
            )

        if item.platform_lbl:
            platform = item.media_info.platform if item.media_info else ""
            if platform:
                item.platform_lbl.setText(f"  {platform}  ")
                item.platform_lbl.show()
            else:
                item.platform_lbl.hide()

        if item.title_lbl:
            if item.media_info and item.media_info.title != "Unknown":
                title = item.media_info.title[:80]
            elif item.error_msg:
                title = item.error_msg[:80]
            else:
                title = self._short_url(item.url)
            item.title_lbl.setText(title)

        if item.progress_lbl:
            progress_text = self._get_progress_text(item)
            if progress_text:
                item.progress_lbl.setText(progress_text)
                item.progress_lbl.show()
            else:
                item.progress_lbl.hide()

        if item.cancel_btn:
            if state == _MonitorState.RECORDING:
                item.cancel_btn.setEnabled(True)
                item.cancel_btn.setStyleSheet(
                    f"background: {T.surface2}; color: {T.warning}; border: none; border-radius: 8px; font-size: 12px;"
                )
            else:
                item.cancel_btn.setEnabled(False)
                item.cancel_btn.setStyleSheet(
                    f"background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 12px;"
                )

        if item.open_folder_btn:
            if state == _MonitorState.ENDED and item.filename:
                item.open_folder_btn.show()
            else:
                item.open_folder_btn.hide()

        _show_ts_btns = (
            state == _MonitorState.ENDED and item.filename and item.filename.lower().endswith(".ts")
        )
        if item.mp4_btn:
            if _show_ts_btns and not item.mp4_converting:
                item.mp4_btn.show()
            else:
                item.mp4_btn.hide()

        if item.send_to_conv_btn:
            if _show_ts_btns:
                item.send_to_conv_btn.show()
            else:
                item.send_to_conv_btn.hide()

        if item.cookie_warn:
            age = self._cookie_age_days()
            if age is not None and age > _COOKIE_WARN_DAYS:
                item.cookie_warn.setText(f"Cookie cũ {age} ngày — có thể bị lỗi auth")
                item.cookie_warn.show()
            else:
                item.cookie_warn.hide()

    def _get_progress_text(self, item: _MonitorItem) -> str:
        state = item.state
        if state == _MonitorState.WAITING:
            interval = self._current_interval()
            if item.last_check > 0:
                elapsed = int(time.time() - item.last_check)
                remaining = max(0, interval - elapsed)
                fail_hint = (
                    f"  (lỗi {item.consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                    if item.consecutive_failures > 0
                    else ""
                )
                return f"Kiểm tra lại sau {remaining}s{fail_hint}"
            return f"Kiểm tra mới {interval}s"
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
        age = self._cookie_age_days()
        if age is not None and age > _COOKIE_WARN_DAYS and self._items:
            text = (
                f"Cookie file đã {age} ngày tuổi — Instagram/TikTok Live "
                f"có thể thất bại. Refresh cookie trong Settings → Network."
            )
            self._cookie_banner_lbl.setText(text)
            self._cookie_banner.show()
        else:
            self._cookie_banner.hide()

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if not self.isVisible():
            return

        self._refresh_recording_items()
        self._recover_stuck_checks()
        self._enqueue_next_check()
        self._update_cookie_banner()
        self._update_status()

    def _recover_stuck_checks(self) -> None:
        now = time.time()
        for item in self._items:
            if item.state != _MonitorState.CHECKING:
                continue
            elapsed = now - item.last_check
            if elapsed > _CHECKING_TIMEOUT_S:
                logger.warning(
                    "LiveMonitor: check for %s stuck %.0fs — resetting",
                    item.url,
                    elapsed,
                )
                self._checking = False
                item.consecutive_failures += 1
                if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    item.state = _MonitorState.ERROR
                    item.error_msg = f"Kiểm tra bị treo {int(elapsed)}s. Thử lại hoặc kiểm tra kết nối mạng."
                else:
                    item.state = _MonitorState.WAITING
                self._refresh_item_ui(item)
                break

    def _refresh_recording_items(self) -> None:
        for item in self._items:
            if item.state != _MonitorState.RECORDING or not item.task_id:
                continue
            task = self._app.service.get_task(item.task_id)
            if task is None:
                item.state = _MonitorState.ENDED
                self._refresh_item_ui(item)
                continue
            snap = task.snapshot()
            status = snap.get("status")
            if status == DownloadStatus.COMPLETED:
                item.state = _MonitorState.ENDED
                item.filename = snap.get("filename", "") or ""
            elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                item.state = _MonitorState.ERROR
                item.error_msg = snap.get("error_msg", "Tải xuống thất bại")
            self._refresh_item_ui(item)

    def _enqueue_next_check(self) -> None:
        if self._checking:
            return

        now = time.time()
        interval = self._current_interval()

        candidate = None
        oldest_check = float("inf")
        for item in self._items:
            if item.state != _MonitorState.WAITING:
                continue
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
        token = self._monitor_token
        item.state = _MonitorState.CHECKING
        item.last_check = time.time()
        self._checking = True
        self._refresh_item_ui(item)

        if item.is_profile_watch:
            url = item.url

            def on_done(live_url: Optional[str]) -> None:
                ui_bridge.post(lambda u=live_url: self._on_profile_check_done(item, u, token))

            def on_error(err: str) -> None:
                ui_bridge.post(lambda e=err: self._on_check_error(item, e, token))

            if item.profile_platform == "tiktok":
                self._app.service.check_tiktok_profile_live(url=url, on_done=on_done, on_error=on_error)
            else:
                self._app.service.check_profile_live(url=url, on_done=on_done, on_error=on_error)
        else:
            url = item.url

            def on_done_analyse(info: MediaInfo) -> None:
                ui_bridge.post(lambda i=info: self._on_check_done(item, i, token))

            def on_error_analyse(err: str) -> None:
                ui_bridge.post(lambda e=err: self._on_check_error(item, e, token))

            self._app.service.analyse_url(url=url, on_done=on_done_analyse, on_error=on_error_analyse)

    def _on_check_done(self, item: _MonitorItem, info: MediaInfo, token: int) -> None:
        self._checking = False
        if token != self._monitor_token:
            return
        item.media_info = info
        item.consecutive_failures = 0

        if info.is_live:
            item.state = _MonitorState.LIVE
            self._refresh_item_ui(item)
            self._start_recording(item, token)
        else:
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)

    def _on_profile_check_done(
        self,
        item: _MonitorItem,
        live_url: Optional[str],
        token: int,
    ) -> None:
        self._checking = False
        if token != self._monitor_token:
            return

        if live_url:
            item.consecutive_failures = 0
            item.state = _MonitorState.LIVE
            self._refresh_item_ui(item)
            self._checking = True

            def on_done(info: MediaInfo) -> None:
                ui_bridge.post(lambda i=info: self._on_live_url_analysed(item, i, live_url, token))

            def on_error(err: str) -> None:
                ui_bridge.post(lambda e=err: self._on_live_url_analyse_fallback(item, live_url, token, e))

            self._app.service.analyse_url(url=live_url, on_done=on_done, on_error=on_error)
        else:
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)

    def _on_live_url_analysed(
        self,
        item: _MonitorItem,
        info: MediaInfo,
        live_url: str,
        token: int,
    ) -> None:
        self._checking = False
        if token != self._monitor_token:
            return
        item.media_info = info
        item.url = live_url
        self._start_recording(item, token)

    def _on_live_url_analyse_fallback(
        self,
        item: _MonitorItem,
        live_url: str,
        token: int,
        err: str,
    ) -> None:
        self._checking = False
        if token != self._monitor_token:
            return
        logger.warning("LiveMonitor: analyse fallback for %s: %s", live_url, err[:60])
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
        self._checking = False
        if token != self._monitor_token:
            return

        err_l = err.lower()
        if "not currently live" in err_l:
            item.consecutive_failures = 0
            item.error_msg = ""
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)
            return

        if "blocked" in err_l and ("rate" in err_l or "429" in err_l):
            _failures = item.consecutive_failures + 1
            _backoff_s = min(300 * (2 ** (_failures - 1)), 1800)
            item.rate_limited_until = time.time() + _backoff_s
            item.consecutive_failures = _failures
            item.state = _MonitorState.WAITING
            item.error_msg = ""
            self._refresh_item_ui(item)
            return

        hard = any(
            k in err_l
            for k in (
                "private",
                "not found",
                "404",
                "login",
                "checkpoint",
                "unsupported url",
                "removed",
                "not available",
            )
        )
        if hard:
            item.state = _MonitorState.ERROR
            item.error_msg = err[:120]
        else:
            item.consecutive_failures += 1
            if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                item.state = _MonitorState.ERROR
                item.error_msg = (
                    f"Da thu {item.consecutive_failures} lan that bai. "
                    f"Loi cuoi: {err[:80]}\n"
                    "Kiem tra cookie Instagram hoac ket noi mang, roi nhan x va them lai URL."
                )
            else:
                item.state = _MonitorState.WAITING
                item.error_msg = ""

        self._refresh_item_ui(item)

    def _start_recording(self, item: _MonitorItem, token: int) -> None:
        if token != self._monitor_token:
            return
        if item.media_info is None:
            return

        for existing in self._items:
            if (
                existing is not item
                and existing.url == item.url
                and existing.state == _MonitorState.RECORDING
            ):
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
        except Exception as exc:
            item.state = _MonitorState.ERROR
            item.error_msg = str(exc)[:120]

        self._refresh_item_ui(item)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _cancel_item(self, item: _MonitorItem) -> None:
        if item.task_id:
            try:
                self._app.service.cancel_download(item.task_id)
            except Exception as exc:
                logger.warning("LiveMonitor: cancel failed: %s", exc)
        item.task_id = None
        item.state = _MonitorState.WAITING
        item.last_check = 0.0
        self._refresh_item_ui(item)

    def _open_folder_for_item(self, item: _MonitorItem) -> None:
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
        if item.mp4_converting or not item.filename:
            return
        src_path = Path(item.filename)
        if not src_path.is_file():
            return

        item.mp4_converting = True
        if item.mp4_btn:
            item.mp4_btn.setEnabled(False)
            item.mp4_btn.setText("…")

        def _on_progress(pct: float) -> None:
            ui_bridge.post(lambda p=pct, i=item: i.mp4_btn.setText(f"{int(p)}%") if i.mp4_btn else None)

        def _on_done(out_path: Path) -> None:
            ui_bridge.post(lambda i=item, p=out_path: _finish(i, p))

        def _finish(i: _MonitorItem, out_path: Path) -> None:
            i.mp4_converting = False
            i.filename = str(out_path)
            self._refresh_item_ui(i)
            if i.mp4_btn:
                i.mp4_btn.setEnabled(True)
                i.mp4_btn.setText("MP4")

        def _on_error(msg: str) -> None:
            ui_bridge.post(lambda i=item: _fail(i))

        def _fail(i: _MonitorItem) -> None:
            i.mp4_converting = False
            if i.mp4_btn:
                i.mp4_btn.setEnabled(True)
                i.mp4_btn.setText("MP4")

        self._app.service.convert_to_mp4(
            src_path,
            on_progress=_on_progress,
            on_done=_on_done,
            on_error=_on_error,
        )

    def _send_to_convert_tab(self, item: _MonitorItem) -> None:
        if not item.filename:
            return
        path = Path(item.filename)
        if not path.is_file():
            self._app.toast("File .ts khong tim thay.", "error")
            return
        convert_tab = self._app.get_tab("convert")
        if convert_tab is None:
            self._app.toast("Convert Tab khong kha dung.", "error")
            return
        convert_tab._add_file(path)
        self._app.navigate_to("convert")

    # ── Status / helpers ───────────────────────────────────────────────────────

    def _update_status(self) -> None:
        waiting = sum(1 for i in self._items if i.state == _MonitorState.WAITING)
        recording = sum(1 for i in self._items if i.state == _MonitorState.RECORDING)
        ended = sum(1 for i in self._items if i.state == _MonitorState.ENDED)
        parts = []
        if recording:
            parts.append(f"{recording} dang ghi")
        if waiting:
            parts.append(f"{waiting} dang cho")
        if ended:
            parts.append(f"{ended} da xong")
        self._status_lbl.setText("  ·  ".join(parts))

    def _update_empty_state(self) -> None:
        if not self._items:
            self._empty_lbl.show()
        else:
            self._empty_lbl.hide()

    def _current_interval(self) -> int:
        try:
            v = int(self._interval_combo.currentText())
            return max(MIN_CHECK_INTERVAL_S, v)
        except (ValueError, AttributeError):
            return DEFAULT_CHECK_INTERVAL

    @staticmethod
    def _short_url(url: str, max_len: int = 70) -> str:
        return url if len(url) <= max_len else f"{url[: max_len - 1]}…"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fade_anim.stop()
        self._fade_anim.start()
