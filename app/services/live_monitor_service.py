"""Headless live-stream monitor for the Remote API.

UI-agnostic port of ui/tabs/live_monitor_tab.py. It watches TikTok / Instagram
profile or live URLs and auto-records when a stream goes live, driving the same
thread-safe DownloadService the desktop tab uses. State changes are pushed to web
clients through an injected broadcast callback (the API SSE broadcaster).

Unlike the desktop tab (single Qt UI thread + QTimer), this runs its own daemon
poll thread and DownloadService callbacks land on worker threads, so every access
to the item list / in-flight slot is guarded by a re-entrant lock.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import MediaInfo
from utils.helpers import is_valid_url
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
    from collections.abc import Callable

    from app.services.download_service import DownloadService
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

MAX_MONITOR_URLS = 20
# BUG-IG-ANTIBOT: ~2,880 authenticated calls/day at the old 30s interval was
# an exact-grid, non-browser polling pattern. 180s + per-item jitter below
# cuts that by 6x and removes the fixed-grid signal.
DEFAULT_CHECK_INTERVAL = 180
MIN_CHECK_INTERVAL = 60
_POLL_S = 5
MAX_CONSECUTIVE_FAILURES = 8
_CHECKING_TIMEOUT_S = 90
# Per-item random jitter applied to the check interval so polling is not on
# a fixed grid (a hallmark of scripted, not browser, traffic).
_JITTER_RANGE = (0.8, 1.2)
# The deep=True story-feed probe marks stories seen on the account -- throttle
# it to at most once per this many seconds per item, not every check.
_DEEP_STORY_COOLDOWN_S = 1800.0

_TIKTOK_SHORT_RE = re.compile(r"^https?://(?:vt|vm)\.tiktok\.com/", re.I)
_IG_USER_LIVE_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)/live(?:/|$|\?)", re.I)

WAITING = "WAITING"
CHECKING = "CHECKING"
LIVE = "LIVE"
RECORDING = "RECORDING"
ENDED = "ENDED"
ERROR = "ERROR"

_ACTIVE_STATES = {WAITING, CHECKING, LIVE, RECORDING}


@dataclass
class MonitorItem:
    url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: str = WAITING
    media_info: Optional[MediaInfo] = None
    task_id: Optional[str] = None
    error_msg: str = ""
    last_check: float = 0.0
    added_at: float = field(default_factory=time.time)
    is_profile_watch: bool = False
    profile_platform: str = ""
    username: str = ""
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

    def to_dict(self) -> dict:
        title = ""
        if self.media_info and self.media_info.title and self.media_info.title != "Unknown":
            title = self.media_info.title[:120]
        platform = (self.media_info.platform if self.media_info else "") or self.profile_platform
        return {
            "id": self.id,
            "url": self.url,
            "state": self.state,
            "title": title,
            "platform": platform,
            "task_id": self.task_id,
            "error_msg": self.error_msg[:200],
            "is_profile_watch": self.is_profile_watch,
            "username": self.username,
            "filename": self.filename,
            "paused": self.paused,
            "added_at": self.added_at,
            "last_check": self.last_check,
            "rate_limited_until": self.rate_limited_until,
        }


class LiveMonitorService:
    def __init__(
        self,
        service: "DownloadService",
        config: "ConfigManager",
        broadcast: "Optional[Callable[[str, dict], None]]" = None,
    ) -> None:
        self._service = service
        self._config = config
        self._broadcast = broadcast
        self._items: list[MonitorItem] = []
        self._checking_item: Optional[MonitorItem] = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._check_interval = DEFAULT_CHECK_INTERVAL
        self._paused = False

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="LiveMonitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception:
                logger.exception("LiveMonitor poll loop error")
            self._stop.wait(_POLL_S)

    # ── Public API ─────────────────────────────────────────────────────────
    def get_check_interval(self) -> int:
        with self._lock:
            return self._check_interval

    def set_check_interval(self, seconds: int) -> int:
        with self._lock:
            self._check_interval = max(MIN_CHECK_INTERVAL, int(seconds))
            return self._check_interval

    def pause(self) -> None:
        with self._lock:
            self._paused = True
        if self._broadcast:
            self._broadcast("monitor_state", {"paused": True})

    def resume(self) -> None:
        with self._lock:
            self._paused = False
        if self._broadcast:
            self._broadcast("monitor_state", {"paused": False})

    def is_paused(self) -> bool:
        return self._paused

    def list_items(self) -> list[dict]:
        with self._lock:
            return [i.to_dict() for i in self._items]

    def add_url(self, url: str) -> dict:
        url = (url or "").strip()
        if not is_valid_url(url):
            raise ValueError("URL không hợp lệ — phải bắt đầu bằng http:// hoặc https://")

        with self._lock:
            if len(self._items) >= MAX_MONITOR_URLS:
                raise ValueError(f"Đã đạt giới hạn {MAX_MONITOR_URLS} URL.")

            url, is_profile, profile_platform, username = self._classify(url)

            for i in self._items:
                if i.state not in _ACTIVE_STATES:
                    continue
                same_profile = (
                    bool(username)
                    and i.is_profile_watch
                    and i.profile_platform == profile_platform
                    and i.username == username
                )
                if i.url == url or same_profile:
                    raise ValueError("URL này đang được theo dõi.")

            if profile_platform == "instagram":
                from infrastructure.downloader.yt_dlp_engine import _resolve_cookie

                if not _resolve_cookie("https://www.instagram.com/", self._config):
                    raise ValueError(
                        "Profile watcher cần cookie file Instagram. "
                        "Cấu hình trong Settings → Network → Cookie file."
                    )

            item = MonitorItem(
                url=url,
                is_profile_watch=is_profile,
                profile_platform=profile_platform,
                username=username,
                watch_url=url if is_profile else "",
            )
            self._items.append(item)
            self._emit(item)
            return item.to_dict()

    def remove(self, item_id: str) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return False
            if self._checking_item is item:
                self._checking_item = None
            if item.task_id:
                try:
                    self._service.cancel_download(item.task_id)
                except Exception:
                    pass
            self._items.remove(item)
        if self._broadcast:
            self._broadcast("monitor_remove", {"id": item_id})
        return True

    def cancel(self, item_id: str) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return False
            if item.task_id:
                try:
                    task = self._service.get_task(item.task_id)
                    if task is not None:
                        task.keep_partial = True
                    self._service.cancel_download(item.task_id)
                except Exception as exc:
                    logger.warning("LiveMonitor: cancel failed: %s", exc)
            item.task_id = None
            item.state = WAITING
            item.last_check = 0.0
            self._emit(item)
            return True

    def pause_item(self, item_id: str) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return False
            item.paused = True
            self._emit(item)
            return True

    def resume_item(self, item_id: str) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return False
            item.paused = False
            self._emit(item)
            return True

    def check_now(self, item_id: str) -> bool:
        with self._lock:
            item = self._find(item_id)
            if item is None:
                return False
            if item.state in (WAITING, ERROR):
                item.state = WAITING
                item.last_check = 0.0
                item.rate_limited_until = 0.0
                item.error_msg = ""
                item.consecutive_failures = 0
                self._emit(item)
            elif item.state in (CHECKING, LIVE):
                item.last_check = 0.0
                item.rate_limited_until = 0.0
            # RECORDING: noop — stream is already being captured
            return True

    # ── Helpers ────────────────────────────────────────────────────────────
    def _find(self, item_id: str) -> Optional[MonitorItem]:
        return next((i for i in self._items if i.id == item_id), None)

    def _emit(self, item: MonitorItem) -> None:
        if self._broadcast:
            self._broadcast("monitor_update", item.to_dict())

    def _classify(self, url: str) -> tuple[str, bool, str, str]:
        is_ig_profile = is_instagram_profile_url(url)
        ig_live_username = ""
        if not is_ig_profile:
            m = _IG_USER_LIVE_RE.search(url)
            if m:
                ig_live_username = m.group(1).lower()
                is_ig_profile = True
                url = f"https://www.instagram.com/{ig_live_username}/"

        is_short_link = bool(_TIKTOK_SHORT_RE.match(url))
        tiktok_live_username = extract_tiktok_username_from_live_url(url) or ""
        is_tiktok_profile = is_short_link or bool(tiktok_live_username) or is_tiktok_profile_url(url)

        if is_ig_profile:
            username = ig_live_username or extract_instagram_username(url) or ""
            return url, True, "instagram", username
        if is_tiktok_profile:
            username = tiktok_live_username
            if not username and not is_short_link:
                username = extract_tiktok_username(url) or ""
            return url, True, "tiktok", username
        return url, False, "", ""

    # ── Poll loop ──────────────────────────────────────────────────────────
    def _poll(self) -> None:
        with self._lock:
            self._refresh_recording_items()
            self._recover_stuck_checks()
            if not self._paused:
                self._enqueue_next_check()

    def _refresh_recording_items(self) -> None:
        for item in list(self._items):
            if item.state != RECORDING or not item.task_id:
                continue
            task = self._service.get_task(item.task_id)
            if task is None:
                item.state = ENDED
                self._emit(item)
                if item.is_profile_watch:
                    self._respawn_watch(item)
                continue
            snap = task.snapshot()
            status = snap.get("status")
            if status == DownloadStatus.COMPLETED:
                item.state = ENDED
                item.filename = snap.get("filename", "") or ""
                if item.is_profile_watch:
                    self._respawn_watch(item)
            elif status == DownloadStatus.FAILED and item.is_profile_watch:
                item.task_id = None
                item.url = item.watch_url or item.url
                item.consecutive_failures += 1
                if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    item.state = ERROR
                    item.error_msg = snap.get("error_msg", "Tải xuống thất bại")
                else:
                    item.state = WAITING
                    item.last_check = time.time()
                    item.error_msg = ""
            elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                item.state = ERROR
                item.error_msg = snap.get("error_msg", "Tải xuống thất bại")
            self._emit(item)

    def _respawn_watch(self, finished: MonitorItem) -> None:
        if not finished.watch_url or len(self._items) >= MAX_MONITOR_URLS:
            return
        for i in self._items:
            if (
                i.state in _ACTIVE_STATES
                and i.is_profile_watch
                and i.profile_platform == finished.profile_platform
                and i.username == finished.username
            ):
                return
        item = MonitorItem(
            url=finished.watch_url,
            is_profile_watch=True,
            profile_platform=finished.profile_platform,
            username=finished.username,
            watch_url=finished.watch_url,
            last_check=time.time(),
            paused=finished.paused,
        )
        self._items.append(item)
        self._emit(item)

    def _recover_stuck_checks(self) -> None:
        now = time.time()
        for item in self._items:
            stuck_live = item.state == LIVE and self._checking_item is item
            if item.state != CHECKING and not stuck_live:
                continue
            if now - item.last_check <= _CHECKING_TIMEOUT_S:
                continue
            if self._checking_item is item:
                self._checking_item = None
            item.consecutive_failures += 1
            if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                item.state = ERROR
                item.error_msg = "Kiểm tra bị treo. Thử lại hoặc kiểm tra kết nối mạng."
            else:
                item.state = WAITING
            self._emit(item)
            break

    def _enqueue_next_check(self) -> None:
        if self._checking_item is not None:
            return
        now = time.time()
        candidate = None
        oldest_check = float("inf")
        for item in self._items:
            if item.state != WAITING or item.paused or now < item.rate_limited_until:
                continue
            due_at = item.last_check + self._check_interval * item.interval_jitter
            if now >= due_at and item.last_check < oldest_check:
                oldest_check = item.last_check
                candidate = item
        if candidate is not None:
            self._trigger_check(candidate)

    def _trigger_check(self, item: MonitorItem) -> None:
        # Deep story-feed check on manual check + newly added items only
        # (last_check == 0.0 exactly for those cases), never on periodic polls,
        # and throttled to at most once per _DEEP_STORY_COOLDOWN_S even then --
        # feed/user/{id}/story/ marks stories seen on the account.
        deep = item.last_check == 0.0 and (time.time() - item.deep_checked_at) >= _DEEP_STORY_COOLDOWN_S
        if deep:
            item.deep_checked_at = time.time()
        item.state = CHECKING
        item.last_check = time.time()
        self._checking_item = item
        self._emit(item)

        def on_err(err: str) -> None:
            self._on_check_error(item, err)

        if item.is_profile_watch:

            def on_profile_done(live_url: Optional[str]) -> None:
                self._on_profile_check_done(item, live_url)

            if item.profile_platform == "tiktok":
                self._service.check_tiktok_profile_live(
                    url=item.url, on_done=on_profile_done, on_error=on_err
                )
            else:
                self._service.check_profile_live(
                    url=item.url, on_done=on_profile_done, on_error=on_err, deep=deep
                )
        else:

            def on_done(info: MediaInfo) -> None:
                self._on_check_done(item, info)

            self._service.analyse_url(url=item.url, on_done=on_done, on_error=on_err)

    def _is_stale_check(self, item: MonitorItem) -> bool:
        if self._checking_item is item:
            self._checking_item = None
        return item not in self._items or item.state != CHECKING

    def _on_check_done(self, item: MonitorItem, info: MediaInfo) -> None:
        with self._lock:
            if self._is_stale_check(item):
                return
            item.media_info = info
            item.consecutive_failures = 0
            if info.is_live:
                item.state = LIVE
                self._emit(item)
                self._start_recording(item)
            else:
                item.state = WAITING
                self._emit(item)

    def _on_profile_check_done(self, item: MonitorItem, live_url: Optional[str]) -> None:
        with self._lock:
            if self._is_stale_check(item):
                return
            if not live_url:
                item.state = WAITING
                self._emit(item)
                return
            item.consecutive_failures = 0
            item.state = LIVE
            self._checking_item = item
            self._emit(item)

            def on_done(info: MediaInfo) -> None:
                self._on_live_url_analysed(item, info, live_url)

            def on_error(err: str) -> None:
                self._on_live_url_fallback(item, live_url, err)

            self._service.analyse_url(url=live_url, on_done=on_done, on_error=on_error)

    def _on_live_url_analysed(self, item: MonitorItem, info: MediaInfo, live_url: str) -> None:
        with self._lock:
            if self._checking_item is item:
                self._checking_item = None
            if item not in self._items:
                return
            item.media_info = info
            item.url = live_url
            if not info.is_live:
                item.state = WAITING
                self._emit(item)
                return
            self._start_recording(item)

    def _on_live_url_fallback(self, item: MonitorItem, live_url: str, err: str) -> None:
        with self._lock:
            if self._checking_item is item:
                self._checking_item = None
            if item not in self._items:
                return
            err_l = err.lower()
            if "not currently live" in err_l:
                item.consecutive_failures = 0
                item.error_msg = ""
                item.state = WAITING
                self._emit(item)
                return
            logger.warning("LiveMonitor: analyse fallback for %s: %s", live_url, err[:60])
            item.media_info = MediaInfo(
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

    def _on_check_error(self, item: MonitorItem, err: str) -> None:
        with self._lock:
            if self._is_stale_check(item):
                return
            err_l = err.lower()
            if "not currently live" in err_l:
                item.consecutive_failures = 0
                item.error_msg = ""
                item.state = WAITING
                self._emit(item)
                return
            if "blocked" in err_l and ("rate" in err_l or "429" in err_l):
                failures = item.consecutive_failures + 1
                item.rate_limited_until = time.time() + min(300 * (2 ** (failures - 1)), 1800)
                item.consecutive_failures = failures
                item.state = WAITING
                item.error_msg = ""
                self._emit(item)
                return
            # A soft block (checkpoint / bot challenge) is not a hard failure
            # requiring user action -- back off on the same schedule as a 429
            # so polling doesn't keep hammering an already-flagged account.
            if "checkpoint" in err_l or "challenge_required" in err_l:
                failures = item.consecutive_failures + 1
                item.rate_limited_until = time.time() + min(300 * (2 ** (failures - 1)), 1800)
                item.consecutive_failures = failures
                item.state = WAITING
                item.error_msg = ""
                self._emit(item)
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
                item.state = ERROR
                item.error_msg = err[:120]
            else:
                item.consecutive_failures += 1
                if item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    item.state = ERROR
                    item.error_msg = f"Đã thử {item.consecutive_failures} lần thất bại. Lỗi cuối: {err[:80]}"
                else:
                    item.state = WAITING
                    item.error_msg = ""
            self._emit(item)

    def _start_recording(self, item: MonitorItem) -> None:
        if item not in self._items or item.media_info is None:
            return
        for existing in self._items:
            if existing is not item and existing.url == item.url and existing.state == RECORDING:
                item.state = WAITING
                self._emit(item)
                return
        try:
            task = self._service.start_download(
                url=item.url,
                media_info=item.media_info,
                format_id="best",
                output_ext="ts",
            )
            item.task_id = task.id
            item.state = RECORDING
        except Exception as exc:
            item.state = ERROR
            item.error_msg = str(exc)[:120]
        self._emit(item)
