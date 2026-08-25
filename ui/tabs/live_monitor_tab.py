"""Live Stream Monitor tab."""

from __future__ import annotations

import logging
import random
import re
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
from utils.i18n import t
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
# BUG-IG-ANTIBOT: mirrors app/services/live_monitor_service.py — see that
# module for the rationale (fixed-grid authenticated polling is a bot tell).
MIN_CHECK_INTERVAL_S = 60
DEFAULT_CHECK_INTERVAL = 180
_POLL_MS = 5_000
_COOKIE_WARN_DAYS = 7
MAX_CONSECUTIVE_FAILURES = 8
_CHECKING_TIMEOUT_S = 90
_JITTER_RANGE = (0.8, 1.2)
_DEEP_STORY_COOLDOWN_S = 1800.0

# vt/vm short links resolve over the network — classification must not
# touch them on the UI thread; the service worker resolves them instead.
_TIKTOK_SHORT_RE = re.compile(r"^https?://(?:vt|vm)\.tiktok\.com/", re.I)

# instagram.com/<user>/live URLs are watched via the cheap profile API checker
# instead of probing with the 120s CDP browser on every poll interval.
_IG_USER_LIVE_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)/live(?:/|$|\?)", re.I)


class _MonitorState(Enum):
    WAITING = auto()
    CHECKING = auto()
    LIVE = auto()
    RECORDING = auto()
    ENDED = auto()
    ERROR = auto()


_STATE_KEY: dict[_MonitorState, str] = {
    _MonitorState.WAITING: "live.state.waiting",
    _MonitorState.CHECKING: "live.state.checking",
    _MonitorState.LIVE: "live.state.live",
    _MonitorState.RECORDING: "live.state.recording",
    _MonitorState.ENDED: "live.state.ended",
    _MonitorState.ERROR: "archive.error_title",
}


def _state_label(state: "_MonitorState") -> str:
    return t(_STATE_KEY[state])


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
    # Original profile URL — item.url is overwritten with the live URL when a
    # recording starts; re-arming the watch needs the original back.
    watch_url: str = ""
    filename: str = ""
    consecutive_failures: int = 0
    rate_limited_until: float = 0.0
    paused: bool = False
    # Per-item jitter multiplier on the check interval — assigned once so the
    # due-check threshold stays stable across polls instead of flapping.
    interval_jitter: float = field(default_factory=lambda: random.uniform(*_JITTER_RANGE))
    # Timestamp of the last deep=True story-feed probe (0.0 = never done).
    deep_checked_at: float = 0.0
    # UI widgets
    row_frame: Optional[QFrame] = field(default=None, repr=False)
    state_lbl: Optional[QLabel] = field(default=None, repr=False)
    title_lbl: Optional[QLabel] = field(default=None, repr=False)
    platform_lbl: Optional[QLabel] = field(default=None, repr=False)
    progress_lbl: Optional[QLabel] = field(default=None, repr=False)
    pause_btn: Optional[QPushButton] = field(default=None, repr=False)
    cancel_btn: Optional[QPushButton] = field(default=None, repr=False)
    check_now_btn: Optional[QPushButton] = field(default=None, repr=False)
    open_folder_btn: Optional[QPushButton] = field(default=None, repr=False)
    send_to_conv_btn: Optional[QPushButton] = field(default=None, repr=False)
    remove_btn: Optional[QPushButton] = field(default=None, repr=False)
    cookie_warn: Optional[QLabel] = field(default=None, repr=False)


class LiveMonitorTab(QWidget):
    def __init__(self, app: "MainWindow") -> None:
        super().__init__()
        self._app = app
        self._items: list[_MonitorItem] = []
        self._checking_item: Optional[_MonitorItem] = None
        self._paused: bool = False
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
        self._title_lbl = QLabel(t("live.title"))
        self._title_lbl.setObjectName("page_title")
        hdr_layout.addWidget(self._title_lbl)
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

        self._hint_lbl = QLabel(t("live.hint", max=MAX_MONITOR_URLS))
        self._hint_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px; background: transparent;")
        ic_layout.addWidget(self._hint_lbl)

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

        self._add_btn = QPushButton(t("live.add_btn"))
        self._add_btn.setFixedSize(100, 36)
        self._add_btn.setStyleSheet(
            f"QPushButton {{ background: {T.primary_dim}; color: {T.primary_text}; border: none; border-radius: 8px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: {T.primary_hover}; color: white; }}"
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

        self._ck_lbl = QLabel(t("live.check_interval_label"))
        self._ck_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        cr_layout.addWidget(self._ck_lbl)

        self._interval_combo = QComboBox()
        self._interval_combo.addItems(["60", "180", "300", "600"])
        self._interval_combo.setCurrentText(str(DEFAULT_CHECK_INTERVAL))
        self._interval_combo.setFixedSize(80, 28)
        cr_layout.addWidget(self._interval_combo)

        self._s_lbl = QLabel(t("live.seconds_label"))
        self._s_lbl.setStyleSheet(f"color: {T.text3}; font-size: 11px;")
        cr_layout.addWidget(self._s_lbl)
        cr_layout.addStretch()

        self._clear_all_btn = QPushButton(t("history.clear_all"))
        self._clear_all_btn.setFixedSize(90, 28)
        self._clear_all_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {T.surface3}; color: {T.text}; }}"
        )
        self._clear_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_all_btn.clicked.connect(self._clear_all)
        cr_layout.addWidget(self._clear_all_btn)

        self._pause_btn = QPushButton(t("live.pause"))
        self._pause_btn.setFixedSize(100, 28)
        self._pause_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {T.surface3}; color: {T.text}; }}"
        )
        self._pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause_btn.clicked.connect(self._toggle_pause)
        cr_layout.addWidget(self._pause_btn)

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

        self._empty_lbl = QLabel(t("live.empty"))
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
            self._app.toast(t("live.invalid_url"), "error")
            return
        if len(self._items) >= MAX_MONITOR_URLS:
            self._app.toast(t("live.limit_reached", max=MAX_MONITOR_URLS), "error")
            return

        is_ig_profile = is_instagram_profile_url(url)
        _ig_live_username = ""
        if not is_ig_profile:
            _m = _IG_USER_LIVE_RE.search(url)
            if _m:
                # Watch the profile instead of probing the live URL directly:
                # the direct path always gets a synthetic is_live=True and
                # burns a 120s CDP probe per check.
                _ig_live_username = _m.group(1).lower()
                is_ig_profile = True
                url = f"https://www.instagram.com/{_ig_live_username}/"
        _is_short_link = bool(_TIKTOK_SHORT_RE.match(url))
        _tiktok_live_username = extract_tiktok_username_from_live_url(url) or ""
        is_tiktok_profile = _is_short_link or bool(_tiktok_live_username) or is_tiktok_profile_url(url)

        is_profile = is_ig_profile or is_tiktok_profile

        if is_ig_profile:
            username = _ig_live_username or extract_instagram_username(url) or ""
            profile_platform = "instagram"
        elif is_tiktok_profile:
            # Short links keep username empty here — the service worker
            # resolves them off the UI thread on the first check.
            username = _tiktok_live_username
            if not username and not _is_short_link:
                username = extract_tiktok_username(url) or ""
            profile_platform = "tiktok"
        else:
            username = ""
            profile_platform = ""

        active_states = {
            _MonitorState.WAITING,
            _MonitorState.CHECKING,
            _MonitorState.LIVE,
            _MonitorState.RECORDING,
        }
        for i in self._items:
            if i.state not in active_states:
                continue
            same_profile = (
                bool(username)
                and i.is_profile_watch
                and i.profile_platform == profile_platform
                and i.username == username
            )
            if i.url == url or same_profile:
                self._app.toast(t("live.already_watching"), "info")
                return

        if is_ig_profile:
            from infrastructure.downloader.yt_dlp_engine import _resolve_cookie

            if not _resolve_cookie("https://www.instagram.com/", self._app.config):
                self._app.toast(t("live.ig_cookie_needed"), "error")
                return

        item = _MonitorItem(
            url=url,
            is_profile_watch=is_profile,
            profile_platform=profile_platform,
            username=username,
            watch_url=url if is_profile else "",
        )
        self._items.append(item)
        self._url_entry.clear()
        self._rebuild_item_ui(item)
        self._update_cookie_banner()
        self._update_status()

        if is_profile:
            label = f"@{username}" if username else self._short_url(url, 40)
            self._app.toast(t("live.watching_toast", label=label), "info")

    def _remove_item(self, item: _MonitorItem) -> None:
        if self._checking_item is item:
            self._checking_item = None
        if item.task_id:
            try:
                self._app.service.cancel_download(item.task_id)
            except Exception:
                pass
        if item in self._items:
            self._items.remove(item)
        self._detach_row(item)
        self._update_empty_state()
        self._update_status()

    def _clear_all(self) -> None:
        self._checking_item = None
        for item in list(self._items):
            if item.task_id:
                try:
                    self._app.service.cancel_download(item.task_id)
                except Exception:
                    pass
            self._detach_row(item)
        self._items.clear()
        self._update_empty_state()
        self._update_status()

    def _detach_row(self, item: _MonitorItem) -> None:
        # Null every widget ref: deleteLater() destroys the children too, and
        # any in-flight callbacks would touch dead objects.
        if item.row_frame:
            self._items_layout.removeWidget(item.row_frame)
            item.row_frame.deleteLater()
        item.row_frame = None
        item.state_lbl = None
        item.title_lbl = None
        item.platform_lbl = None
        item.progress_lbl = None
        item.pause_btn = None
        item.cancel_btn = None
        item.open_folder_btn = None
        item.send_to_conv_btn = None
        item.remove_btn = None
        item.cookie_warn = None

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
            QFrame#monitorRow:hover {{
                background-color: {T.card_hover};
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

        state_lbl = QLabel(_state_label(item.state))
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
            f"@{item.username}  {t('live.profile_watch_suffix')}"
            if item.is_profile_watch and item.username
            else self._short_url(item.url)
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

        pause_btn = QPushButton(t("live.pause"))
        pause_btn.setFixedSize(90, 28)
        pause_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 11px; padding: 0; }}"
            f"QPushButton:hover {{ background: {T.surface3}; color: {T.text}; }}"
        )
        pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        pause_btn.clicked.connect(lambda _=False, i=item: self._toggle_item_pause(i))
        right_layout.addWidget(pause_btn)
        item.pause_btn = pause_btn

        check_now_btn = QPushButton(t("live.check_now"))
        check_now_btn.setFixedSize(70, 28)
        check_now_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 11px; padding: 0; }}"
            f"QPushButton:hover {{ background: {T.surface3}; color: {T.text}; }}"
        )
        check_now_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        check_now_btn.clicked.connect(lambda _=False, i=item: self._force_check_now(i))
        right_layout.addWidget(check_now_btn)
        item.check_now_btn = check_now_btn

        cancel_btn = QPushButton(t("toolbar.stop"))
        cancel_btn.setFixedSize(46, 28)
        cancel_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.warning}; border: none; border-radius: 8px; font-size: 12px; padding: 0; }}"
            f"QPushButton:hover {{ background: {T.surface3}; }}"
            f"QPushButton:disabled {{ color: {T.text3}; }}"
        )
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(lambda _=False, i=item: self._cancel_item(i))
        right_layout.addWidget(cancel_btn)
        item.cancel_btn = cancel_btn

        open_folder_btn = QPushButton(t("live.open"))
        open_folder_btn.setFixedSize(46, 28)
        open_folder_btn.setStyleSheet(
            f"QPushButton {{ background: {T.success_bg}; color: {T.success_text}; border: none; border-radius: 8px; font-size: 12px; padding: 0; }}"
            f"QPushButton:hover {{ background: {T.success}; color: white; }}"
        )
        open_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_folder_btn.clicked.connect(lambda _=False, i=item: self._open_folder_for_item(i))
        open_folder_btn.hide()
        right_layout.addWidget(open_folder_btn)
        item.open_folder_btn = open_folder_btn

        send_to_conv_btn = QPushButton(t("live.convert_btn"))
        send_to_conv_btn.setFixedSize(46, 28)
        send_to_conv_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text2}; border: none; border-radius: 8px; font-size: 13px; padding: 0; }}"
            f"QPushButton:hover {{ background: {T.surface3}; color: {T.text}; }}"
        )
        send_to_conv_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_to_conv_btn.clicked.connect(lambda _=False, i=item: self._send_to_convert_tab(i))
        send_to_conv_btn.hide()
        right_layout.addWidget(send_to_conv_btn)
        item.send_to_conv_btn = send_to_conv_btn

        remove_btn = QPushButton("x")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setStyleSheet(
            f"QPushButton {{ background: {T.surface2}; color: {T.text3}; border: none; border-radius: 8px; font-size: 12px; padding: 0; }}"
            f"QPushButton:hover {{ background: {T.error}; color: white; }}"
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
            item.state_lbl.setText(_state_label(state))
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

        if item.pause_btn:
            active = state in (
                _MonitorState.WAITING,
                _MonitorState.CHECKING,
                _MonitorState.LIVE,
                _MonitorState.RECORDING,
            )
            if active:
                item.pause_btn.setText(t("live.resume") if item.paused else t("live.pause"))
                item.pause_btn.show()
            else:
                item.pause_btn.hide()

        if item.cancel_btn:
            item.cancel_btn.setEnabled(state == _MonitorState.RECORDING)

        if item.check_now_btn:
            item.check_now_btn.setVisible(state in (_MonitorState.WAITING, _MonitorState.ERROR))

        if item.open_folder_btn:
            if state == _MonitorState.ENDED and item.filename:
                item.open_folder_btn.show()
            else:
                item.open_folder_btn.hide()

        _show_ts_btns = (
            state == _MonitorState.ENDED and item.filename and item.filename.lower().endswith(".ts")
        )
        if item.send_to_conv_btn:
            if _show_ts_btns:
                item.send_to_conv_btn.show()
            else:
                item.send_to_conv_btn.hide()

        if item.cookie_warn:
            age = self._cookie_age_days()
            if age is not None and age > _COOKIE_WARN_DAYS:
                item.cookie_warn.setText(t("live.cookie_old", age=age))
                item.cookie_warn.show()
            else:
                item.cookie_warn.hide()

    def _get_progress_text(self, item: _MonitorItem) -> str:
        state = item.state
        if state == _MonitorState.WAITING:
            if item.paused:
                return t("live.paused_status")
            now = time.time()
            if item.rate_limited_until > now:
                wait = int(item.rate_limited_until - now)
                return t("live.rate_limited", wait=wait)
            interval = self._current_interval()
            if item.last_check > 0:
                elapsed = int(time.time() - item.last_check)
                remaining = max(0, interval - elapsed)
                fail_hint = (
                    t("live.fail_hint", failed=item.consecutive_failures, max=MAX_CONSECUTIVE_FAILURES)
                    if item.consecutive_failures > 0
                    else ""
                )
                return t("live.recheck_in", remaining=remaining, fail_hint=fail_hint)
            return t("live.new_check_in", interval=interval)
        if state == _MonitorState.CHECKING:
            return t("live.checking_stream")
        if state == _MonitorState.LIVE:
            return t("live.stream_live_starting")
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
                return "  ·  ".join(parts) if parts else t("live.recording_dots")
        if state == _MonitorState.ENDED:
            if item.task_id:
                task = self._app.service.get_task(item.task_id)
                if task and task.filename:
                    p = Path(task.filename)
                    return t("editor.export_saved", name=p.name)
            return t("live.state.ended")
        if state == _MonitorState.ERROR:
            return item.error_msg[:80] if item.error_msg else t("toolbar.error.unknown")
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
            self._cookie_banner_lbl.setText(t("live.cookie_banner", age=age))
            self._cookie_banner.show()
        else:
            self._cookie_banner.hide()

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        # Checks and recording state must keep running while the tab is
        # hidden — only cosmetic refreshes are gated on visibility.
        self._refresh_recording_items()
        self._recover_stuck_checks()
        if not self._paused:
            self._enqueue_next_check()
        if self.isVisible():
            self._update_cookie_banner()
            self._update_status()

    def _recover_stuck_checks(self) -> None:
        now = time.time()
        for item in self._items:
            # LIVE items hold the in-flight slot during the post-detection
            # analyse (_on_profile_check_done); a lost callback there would
            # freeze every other item's checks forever.
            _stuck_live = item.state == _MonitorState.LIVE and self._checking_item is item
            if item.state != _MonitorState.CHECKING and not _stuck_live:
                continue
            elapsed = now - item.last_check
            if elapsed > _CHECKING_TIMEOUT_S:
                logger.warning(
                    "LiveMonitor: check for %s stuck %.0fs — resetting",
                    item.url,
                    elapsed,
                )
                if self._checking_item is item:
                    self._checking_item = None
                item.consecutive_failures += 1
                if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    item.state = _MonitorState.ERROR
                    item.error_msg = t("live.check_stuck_error", elapsed=int(elapsed))
                else:
                    item.state = _MonitorState.WAITING
                self._refresh_item_ui(item)
                break

    def _refresh_recording_items(self) -> None:
        # Iterate over a copy — _respawn_watch appends to self._items.
        for item in list(self._items):
            if item.state != _MonitorState.RECORDING or not item.task_id:
                continue
            task = self._app.service.get_task(item.task_id)
            if task is None:
                item.state = _MonitorState.ENDED
                self._refresh_item_ui(item)
                if item.is_profile_watch:
                    self._respawn_watch(item)
                continue
            snap = task.snapshot()
            status = snap.get("status")
            if status == DownloadStatus.COMPLETED:
                item.state = _MonitorState.ENDED
                item.filename = snap.get("filename", "") or ""
                if item.is_profile_watch:
                    self._respawn_watch(item)
            elif status == DownloadStatus.FAILED and item.is_profile_watch:
                # Re-arm: a failed recording must not end the watch.
                item.task_id = None
                item.url = item.watch_url or item.url
                item.consecutive_failures += 1
                if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    item.state = _MonitorState.ERROR
                    item.error_msg = snap.get("error_msg") or t("live.download_failed")
                else:
                    item.state = _MonitorState.WAITING
                    item.last_check = time.time()
                    item.error_msg = ""
            elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                item.state = _MonitorState.ERROR
                item.error_msg = snap.get("error_msg") or t("live.download_failed")
            self._refresh_item_ui(item)

    def _respawn_watch(self, finished: _MonitorItem) -> None:
        # The finished row keeps its ENDED state (file / MP4 buttons); a fresh
        # WAITING item carries the profile watch forward.
        if not finished.watch_url or len(self._items) >= MAX_MONITOR_URLS:
            return
        active = {
            _MonitorState.WAITING,
            _MonitorState.CHECKING,
            _MonitorState.LIVE,
            _MonitorState.RECORDING,
        }
        for i in self._items:
            if (
                i.state in active
                and i.is_profile_watch
                and i.profile_platform == finished.profile_platform
                and i.username == finished.username
            ):
                return
        item = _MonitorItem(
            url=finished.watch_url,
            is_profile_watch=True,
            profile_platform=finished.profile_platform,
            username=finished.username,
            watch_url=finished.watch_url,
            # Wait one full interval before re-checking the just-ended stream.
            last_check=time.time(),
            paused=finished.paused,
        )
        self._items.append(item)
        self._rebuild_item_ui(item)

    def _enqueue_next_check(self) -> None:
        if self._checking_item is not None:
            return

        now = time.time()
        interval = self._current_interval()

        candidate = None
        oldest_check = float("inf")
        for item in self._items:
            if item.state != _MonitorState.WAITING:
                continue
            if item.paused:
                continue
            if now < item.rate_limited_until:
                continue
            due = item.last_check + interval * item.interval_jitter
            if now >= due and item.last_check < oldest_check:
                oldest_check = item.last_check
                candidate = item

        if candidate is None:
            return

        self._trigger_check(candidate)

    def _trigger_check(self, item: _MonitorItem) -> None:
        # Deep story-feed check on manual check + newly added items only
        # (last_check == 0.0 exactly for those cases), never on periodic polls,
        # and throttled to at most once per _DEEP_STORY_COOLDOWN_S even then --
        # feed/user/{id}/story/ marks stories seen on the account.
        deep = item.last_check == 0.0 and (time.time() - item.deep_checked_at) >= _DEEP_STORY_COOLDOWN_S
        if deep:
            item.deep_checked_at = time.time()
        item.state = _MonitorState.CHECKING
        item.last_check = time.time()
        self._checking_item = item
        self._refresh_item_ui(item)

        if item.is_profile_watch:
            url = item.url

            def on_done(live_url: Optional[str]) -> None:
                ui_bridge.post(lambda u=live_url: self._on_profile_check_done(item, u))

            def on_error(err: str) -> None:
                ui_bridge.post(lambda e=err: self._on_check_error(item, e))

            if item.profile_platform == "tiktok":
                self._app.service.check_tiktok_profile_live(url=url, on_done=on_done, on_error=on_error)
            else:
                self._app.service.check_profile_live(url=url, on_done=on_done, on_error=on_error, deep=deep)
        else:
            url = item.url

            def on_done_analyse(info: MediaInfo) -> None:
                ui_bridge.post(lambda i=info: self._on_check_done(item, i))

            def on_error_analyse(err: str) -> None:
                ui_bridge.post(lambda e=err: self._on_check_error(item, e))

            self._app.service.analyse_url(url=url, on_done=on_done_analyse, on_error=on_error_analyse)

    def _is_stale_check(self, item: _MonitorItem) -> bool:
        # Release the in-flight slot only if this item still owns it, so a
        # late callback (after stuck-check recovery) can't clobber a check
        # that another item started in the meantime.
        if self._checking_item is item:
            self._checking_item = None
        return item not in self._items or item.state != _MonitorState.CHECKING

    def _on_check_done(self, item: _MonitorItem, info: MediaInfo) -> None:
        if self._is_stale_check(item):
            return
        item.media_info = info
        item.consecutive_failures = 0

        if info.is_live:
            item.state = _MonitorState.LIVE
            self._refresh_item_ui(item)
            self._start_recording(item)
        else:
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)

    def _on_profile_check_done(
        self,
        item: _MonitorItem,
        live_url: Optional[str],
    ) -> None:
        if self._is_stale_check(item):
            return

        if live_url:
            item.consecutive_failures = 0
            item.state = _MonitorState.LIVE
            self._refresh_item_ui(item)
            self._checking_item = item

            def on_done(info: MediaInfo) -> None:
                ui_bridge.post(lambda i=info: self._on_live_url_analysed(item, i, live_url))

            def on_error(err: str) -> None:
                ui_bridge.post(lambda e=err: self._on_live_url_analyse_fallback(item, live_url, e))

            self._app.service.analyse_url(url=live_url, on_done=on_done, on_error=on_error)
        else:
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)

    def _on_live_url_analysed(
        self,
        item: _MonitorItem,
        info: MediaInfo,
        live_url: str,
    ) -> None:
        if self._checking_item is item:
            self._checking_item = None
        if item not in self._items:
            return
        item.media_info = info
        item.url = live_url
        if not info.is_live:
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)
            return
        self._start_recording(item)

    def _on_live_url_analyse_fallback(
        self,
        item: _MonitorItem,
        live_url: str,
        err: str,
    ) -> None:
        if self._checking_item is item:
            self._checking_item = None
        if item not in self._items:
            return
        err_l = err.lower()
        if "not currently live" in err_l:
            item.consecutive_failures = 0
            item.error_msg = ""
            item.state = _MonitorState.WAITING
            self._refresh_item_ui(item)
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
        self._start_recording(item)

    def _on_check_error(self, item: _MonitorItem, err: str) -> None:
        if self._is_stale_check(item):
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

        # A soft block (checkpoint / bot challenge) is not a hard failure
        # requiring user action -- back off on the same schedule as a 429 so
        # polling doesn't keep hammering an already-flagged account.
        if "checkpoint" in err_l or "challenge_required" in err_l:
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
                item.error_msg = t(
                    "live.retry_failure_error", failures=item.consecutive_failures, err=err[:80]
                )
            else:
                item.state = _MonitorState.WAITING
                item.error_msg = ""

        self._refresh_item_ui(item)

    def _start_recording(self, item: _MonitorItem) -> None:
        if item not in self._items:
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

    def _force_check_now(self, item: _MonitorItem) -> None:
        if item.state not in (_MonitorState.WAITING, _MonitorState.ERROR):
            return
        item.rate_limited_until = 0.0
        item.last_check = 0.0
        item.consecutive_failures = 0
        item.error_msg = ""
        item.state = _MonitorState.WAITING
        self._refresh_item_ui(item)
        if self._checking_item is None and not self._paused and not item.paused:
            self._trigger_check(item)

    def _cancel_item(self, item: _MonitorItem) -> None:
        if item.task_id:
            try:
                task = self._app.service.get_task(item.task_id)
                if task is not None:
                    task.keep_partial = True
                self._app.service.cancel_download(item.task_id)
            except Exception as exc:
                logger.warning("LiveMonitor: cancel failed: %s", exc)
        item.task_id = None
        item.state = _MonitorState.WAITING
        item.last_check = time.time()
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

    def _send_to_convert_tab(self, item: _MonitorItem) -> None:
        if not item.filename:
            return
        path = Path(item.filename)
        if not path.is_file():
            self._app.toast(t("live.file_not_found"), "error")
            return
        self._app.navigate_to("convert", str(path))

    # ── Status / helpers ───────────────────────────────────────────────────────

    def _toggle_item_pause(self, item: _MonitorItem) -> None:
        item.paused = not item.paused
        self._refresh_item_ui(item)
        self._update_status()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause_btn.setText(t("live.resume") if self._paused else t("live.pause"))
        self._update_status()

    def _update_status(self) -> None:
        waiting = sum(1 for i in self._items if i.state == _MonitorState.WAITING)
        recording = sum(1 for i in self._items if i.state == _MonitorState.RECORDING)
        ended = sum(1 for i in self._items if i.state == _MonitorState.ENDED)
        parts = []
        if self._paused:
            parts.append(t("live.paused_status"))
        if recording:
            parts.append(t("live.status_recording", count=recording))
        if waiting:
            parts.append(t("live.status_waiting", count=waiting))
        if ended:
            parts.append(t("live.status_ended", count=ended))
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

    # ── i18n ─────────────────────────────────────────────────────────────

    def retranslate(self) -> None:
        self._title_lbl.setText(t("live.title"))
        self._hint_lbl.setText(t("live.hint", max=MAX_MONITOR_URLS))
        self._add_btn.setText(t("live.add_btn"))
        self._ck_lbl.setText(t("live.check_interval_label"))
        self._s_lbl.setText(t("live.seconds_label"))
        self._clear_all_btn.setText(t("history.clear_all"))
        self._pause_btn.setText(t("live.resume") if self._paused else t("live.pause"))
        self._empty_lbl.setText(t("live.empty"))
        self._update_cookie_banner()
        self._update_status()
        for item in self._items:
            self._refresh_item_ui(item)
