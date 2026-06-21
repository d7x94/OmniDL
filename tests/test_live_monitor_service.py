"""Tests for the headless LiveMonitorService used by the Remote API."""

from __future__ import annotations

import time as _time
from unittest.mock import MagicMock

import pytest

from app.services import live_monitor_service as lms
from app.services.live_monitor_service import LiveMonitorService, MonitorItem
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import MediaInfo


class StubTask:
    def __init__(self, task_id: str, status=DownloadStatus.DOWNLOADING, filename: str = ""):
        self.id = task_id
        self._status = status
        self.filename = filename

    def snapshot(self) -> dict:
        return {"status": self._status, "filename": self.filename, "error_msg": "boom"}


class StubService:
    """DownloadService stand-in. Live-check / analyse callbacks fire synchronously."""

    def __init__(self):
        self.started: list[dict] = []
        self.cancelled: list[str] = []
        self.tasks: dict[str, StubTask] = {}
        self.tiktok_live_url = None
        self.ig_live_url = None
        self.analyse_info: MediaInfo | None = None
        self.analyse_error: str | None = None
        self._n = 0

    def analyse_url(self, url, on_done, on_error):
        if self.analyse_error is not None:
            on_error(self.analyse_error)
        else:
            on_done(self.analyse_info)

    def check_tiktok_profile_live(self, url, on_done, on_error):
        on_done(self.tiktok_live_url)

    def check_profile_live(self, url, on_done, on_error):
        on_done(self.ig_live_url)

    def start_download(self, url, media_info, format_id, output_ext):
        self._n += 1
        task = StubTask(f"task{self._n}")
        self.tasks[task.id] = task
        self.started.append(
            {"url": url, "format_id": format_id, "output_ext": output_ext, "media_info": media_info}
        )
        return task

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def cancel_download(self, task_id):
        self.cancelled.append(task_id)


def _make(service: StubService):
    events: list[tuple[str, dict]] = []
    svc = LiveMonitorService(service, MagicMock(), broadcast=lambda ev, data: events.append((ev, data)))  # type: ignore[arg-type]  # StubService duck-types DownloadService
    return svc, events


def test_direct_live_url_starts_recording_as_ts():
    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", title="Live", is_live=True)
    svc, events = _make(service)

    item = svc.add_url("https://www.youtube.com/watch?v=abc")
    assert item["state"] == "WAITING"

    svc._poll()

    state = svc.list_items()[0]["state"]
    assert state == "RECORDING"
    assert len(service.started) == 1
    assert service.started[0]["output_ext"] == "ts"
    assert service.started[0]["format_id"] == "best"
    assert any(ev == "monitor_update" for ev, _ in events)


def test_direct_offline_url_returns_to_waiting():
    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", title="VOD", is_live=False)
    svc, _ = _make(service)

    svc.add_url("https://www.youtube.com/watch?v=abc")
    svc._poll()

    assert svc.list_items()[0]["state"] == "WAITING"
    assert service.started == []


def test_tiktok_profile_goes_live_records():
    service = StubService()
    service.tiktok_live_url = "https://www.tiktok.com/@user/live"
    service.analyse_info = MediaInfo(url=service.tiktok_live_url, title="@user", is_live=True)
    svc, _ = _make(service)

    item = svc.add_url("https://www.tiktok.com/@user")
    assert item["is_profile_watch"] is True
    assert item["platform"] == "tiktok"

    svc._poll()

    assert svc.list_items()[0]["state"] == "RECORDING"
    assert len(service.started) == 1


def test_tiktok_profile_not_live_stays_waiting():
    service = StubService()
    service.tiktok_live_url = None
    svc, _ = _make(service)

    svc.add_url("https://www.tiktok.com/@user")
    svc._poll()

    assert svc.list_items()[0]["state"] == "WAITING"
    assert service.started == []


def test_completed_recording_ends_and_respawns_profile_watch():
    service = StubService()
    service.tiktok_live_url = "https://www.tiktok.com/@user/live"
    service.analyse_info = MediaInfo(url=service.tiktok_live_url, title="@user", is_live=True)
    svc, _ = _make(service)

    svc.add_url("https://www.tiktok.com/@user")
    svc._poll()  # -> RECORDING
    rec = svc.list_items()[0]
    task = service.tasks[rec["task_id"]]
    task._status = DownloadStatus.COMPLETED
    task.filename = "out.ts"

    svc._poll()  # refresh -> ENDED + respawn

    states = sorted(i["state"] for i in svc.list_items())
    assert states == ["ENDED", "WAITING"], states


def test_add_invalid_url_raises():
    svc, _ = _make(StubService())
    with pytest.raises(ValueError):
        svc.add_url("not-a-url")


def test_duplicate_url_raises():
    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", is_live=False)
    svc, _ = _make(service)
    svc.add_url("https://www.youtube.com/watch?v=abc")
    with pytest.raises(ValueError):
        svc.add_url("https://www.youtube.com/watch?v=abc")


def test_cap_enforced(monkeypatch):
    monkeypatch.setattr(lms, "MAX_MONITOR_URLS", 2)
    svc, _ = _make(StubService())
    svc.add_url("https://www.youtube.com/watch?v=a")
    svc.add_url("https://www.youtube.com/watch?v=b")
    with pytest.raises(ValueError):
        svc.add_url("https://www.youtube.com/watch?v=c")


def test_remove_cancels_recording_and_emits():
    service = StubService()
    svc, events = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="task9")
    svc._items.append(item)

    assert svc.remove(item.id) is True
    assert "task9" in service.cancelled
    assert svc.list_items() == []
    assert any(ev == "monitor_remove" for ev, _ in events)


def test_cancel_keeps_watch_and_resets():
    service = StubService()
    svc, _ = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="task9")
    svc._items.append(item)

    assert svc.cancel(item.id) is True
    assert "task9" in service.cancelled
    snap = svc.list_items()[0]
    assert snap["state"] == "WAITING"
    assert snap["task_id"] is None


def test_remove_and_cancel_unknown_id_return_false():
    svc, _ = _make(StubService())
    assert svc.remove("nope") is False
    assert svc.cancel("nope") is False


def test_start_stop_runs_poll_thread():
    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", is_live=False)
    svc, _ = _make(service)
    svc.add_url("https://www.youtube.com/watch?v=z")
    svc.start()
    svc.start()  # idempotent — already running
    import time as _t

    _t.sleep(0.15)  # let the daemon run at least one poll
    svc.stop()
    svc.stop()  # safe when already stopped
    assert svc.list_items()[0]["state"] in ("WAITING", "CHECKING")


def test_classify_instagram_and_tiktok_variants(monkeypatch):
    import infrastructure.downloader.yt_dlp_engine as ytmod

    monkeypatch.setattr(ytmod, "_resolve_cookie", lambda *a, **k: "/tmp/ig.txt")  # nosec B108
    svc, _ = _make(StubService())

    ig = svc.add_url("https://www.instagram.com/someone/")
    assert ig["platform"] == "instagram" and ig["is_profile_watch"]

    ig_live = svc.add_url("https://www.instagram.com/liveuser/live")
    assert ig_live["platform"] == "instagram" and ig_live["username"] == "liveuser"

    short = svc.add_url("https://vt.tiktok.com/ZSABC123/")
    assert short["platform"] == "tiktok" and short["is_profile_watch"]

    live = svc.add_url("https://www.tiktok.com/@liveguy/live")
    assert live["platform"] == "tiktok" and live["username"] == "liveguy"


def test_instagram_requires_cookie(monkeypatch):
    import infrastructure.downloader.yt_dlp_engine as ytmod

    monkeypatch.setattr(ytmod, "_resolve_cookie", lambda *a, **k: "")
    svc, _ = _make(StubService())
    with pytest.raises(ValueError):
        svc.add_url("https://www.instagram.com/someone/")


def _checking(svc, **kw):
    item = MonitorItem(url="https://x", state="CHECKING", **kw)
    svc._items.append(item)
    svc._checking_item = item
    return item


def test_check_error_not_live_resets_to_waiting():
    svc, _ = _make(StubService())
    item = _checking(svc)
    svc._on_check_error(item, "The channel is not currently live")
    assert item.state == "WAITING"


def test_check_error_rate_limited_backs_off():
    svc, _ = _make(StubService())
    item = _checking(svc)
    svc._on_check_error(item, "blocked: rate limit 429")
    assert item.state == "WAITING"
    assert item.rate_limited_until > 0


def test_check_error_hard_marks_error():
    svc, _ = _make(StubService())
    item = _checking(svc)
    svc._on_check_error(item, "This account is private")
    assert item.state == "ERROR"


def test_check_error_generic_escalates_to_error():
    svc, _ = _make(StubService())
    item = _checking(svc, consecutive_failures=lms.MAX_CONSECUTIVE_FAILURES - 1)
    svc._on_check_error(item, "weird transient glitch")
    assert item.state == "ERROR"


def test_refresh_task_gone_ends_item():
    svc, _ = _make(StubService())
    item = MonitorItem(url="https://x", state="RECORDING", task_id="ghost")
    svc._items.append(item)
    svc._poll()
    assert item.state == "ENDED"


def test_refresh_failed_nonprofile_marks_error():
    service = StubService()
    svc, _ = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="t")
    service.tasks["t"] = StubTask("t", status=DownloadStatus.FAILED)
    svc._items.append(item)
    svc._poll()
    assert item.state == "ERROR"


def test_refresh_failed_profile_rearms_to_waiting():
    service = StubService()
    svc, _ = _make(service)
    item = MonitorItem(
        url="https://www.tiktok.com/@u/live",
        state="RECORDING",
        task_id="t",
        is_profile_watch=True,
        profile_platform="tiktok",
        username="u",
        watch_url="https://www.tiktok.com/@u",
    )
    service.tasks["t"] = StubTask("t", status=DownloadStatus.FAILED)
    svc._items.append(item)
    svc._poll()
    assert item.state == "WAITING"
    assert item.task_id is None


def test_profile_live_analyse_error_uses_synthetic_fallback():
    service = StubService()
    service.tiktok_live_url = "https://www.tiktok.com/@user/live"
    service.analyse_error = "boom"
    svc, _ = _make(service)
    svc.add_url("https://www.tiktok.com/@user")
    svc._poll()
    assert svc.list_items()[0]["state"] == "RECORDING"
    assert len(service.started) == 1
    assert service.started[0]["media_info"].is_live is True


def test_start_recording_dedupes_same_url():
    svc, _ = _make(StubService())
    rec = MonitorItem(url="https://x", state="RECORDING", task_id="t1")
    dup = MonitorItem(url="https://x", state="LIVE", media_info=MediaInfo(url="https://x", is_live=True))
    svc._items.extend([rec, dup])
    svc._start_recording(dup)
    assert dup.state == "WAITING"


def test_start_recording_error_marks_error():
    service = StubService()

    def boom(**kw):
        raise RuntimeError("nope")

    service.start_download = boom  # type: ignore[method-assign]
    svc, _ = _make(service)
    item = MonitorItem(url="https://x", state="LIVE", media_info=MediaInfo(url="https://x", is_live=True))
    svc._items.append(item)
    svc._start_recording(item)
    assert item.state == "ERROR"


def test_recover_stuck_check_resets():
    svc, _ = _make(StubService())
    item = _checking(svc)
    item.last_check = 1.0  # far in the past → exceeds timeout
    svc._recover_stuck_checks()
    assert item.state == "WAITING"
    assert svc._checking_item is None


def test_set_check_interval_clamps_to_minimum():
    svc, _ = _make(StubService())
    assert svc.set_check_interval(5) == 15
    assert svc.get_check_interval() == 15
    assert svc.set_check_interval(60) == 60
    assert svc.get_check_interval() == 60


def test_enqueue_uses_configured_interval(monkeypatch):

    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", is_live=False)
    svc, _ = _make(service)
    svc.set_check_interval(60)
    item = MonitorItem(url="https://www.youtube.com/watch?v=x", state="WAITING")
    svc._items.append(item)

    now = _time.time()
    monkeypatch.setattr(lms.time, "time", lambda: now)

    # 31s elapsed - below 60s interval, should NOT trigger
    item.last_check = now - 31
    svc._enqueue_next_check()
    # _trigger_check resets last_check to time.time(); if not triggered it stays at now-31
    assert item.last_check == now - 31

    # 61s elapsed - above 60s interval, SHOULD trigger (_trigger_check sets last_check=now)
    item.last_check = now - 61
    svc._enqueue_next_check()
    assert item.last_check == now


def test_cancel_sets_keep_partial():
    service = StubService()
    task = StubTask("task9")
    task.keep_partial = False
    service.tasks["task9"] = task
    svc, _ = _make(service)
    item = MonitorItem(url="https://x", state="RECORDING", task_id="task9")
    svc._items.append(item)

    svc.cancel(item.id)

    assert task.keep_partial is True
    assert "task9" in service.cancelled


def test_pause_item_skips_enqueue():
    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", is_live=False)
    svc, events = _make(service)
    item = svc.add_url("https://www.youtube.com/watch?v=x")
    item_id = item["id"]
    item_obj = svc._items[0]
    item_obj.last_check = 0.0

    assert svc.pause_item(item_id) is True
    assert item_obj.paused is True
    assert any(ev == "monitor_update" for ev, _ in events)

    svc._enqueue_next_check()
    assert svc._checking_item is None


def test_resume_item_re_arms():

    service = StubService()
    service.analyse_info = MediaInfo(url="https://x", is_live=False)
    svc, _ = _make(service)
    item = svc.add_url("https://www.youtube.com/watch?v=y")
    item_id = item["id"]
    item_obj = svc._items[0]
    item_obj.last_check = 0.0

    svc.pause_item(item_id)
    svc._enqueue_next_check()
    assert svc._checking_item is None
    assert item_obj.last_check == 0.0  # was not triggered

    assert svc.resume_item(item_id) is True
    assert item_obj.paused is False
    before = _time.time()
    svc._enqueue_next_check()
    assert item_obj.last_check >= before  # _trigger_check was called


def test_pause_resume_unknown_id():
    svc, _ = _make(StubService())
    assert svc.pause_item("nope") is False
    assert svc.resume_item("nope") is False


def test_to_dict_includes_paused():
    svc, _ = _make(StubService())
    item = MonitorItem(url="https://x")
    svc._items.append(item)
    d = item.to_dict()
    assert "paused" in d
    assert d["paused"] is False
    item.paused = True
    assert item.to_dict()["paused"] is True


# ---------------------------------------------------------------------------
# Gate 2 -- record gate: "not currently live" and is_live=False handling
# ---------------------------------------------------------------------------


def _live_checking(svc, **kw):
    """Item in LIVE state holding the in-flight slot (post-profile-check)."""
    item = MonitorItem(url="https://www.tiktok.com/@u/live", state=lms.LIVE, **kw)
    svc._items.append(item)
    svc._checking_item = item
    return item


def test_live_url_fallback_not_live_resets_to_waiting():
    service = StubService()
    svc, _ = _make(service)
    item = _live_checking(svc)
    svc._on_live_url_fallback(item, item.url, "The channel is not currently live")
    assert item.state == lms.WAITING
    assert service.started == []
    assert svc._checking_item is None


def test_live_url_fallback_transient_error_still_records():
    service = StubService()
    svc, _ = _make(service)
    item = _live_checking(svc)
    svc._on_live_url_fallback(item, item.url, "blocked: rate limit 429")
    assert item.state == lms.RECORDING
    assert len(service.started) == 1
    assert service.started[0]["media_info"].is_live is True


def test_live_url_analysed_not_live_resets_to_waiting():
    service = StubService()
    svc, _ = _make(service)
    item = _live_checking(svc)
    info = MediaInfo(url=item.url, title="vod", is_live=False)
    svc._on_live_url_analysed(item, info, item.url)
    assert item.state == lms.WAITING
    assert service.started == []


def _make_service():
    svc, _ = _make(StubService())
    return svc


def test_to_dict_includes_timestamp_fields():
    svc = _make_service()
    item_dict = svc.add_url("https://www.tiktok.com/@testuser")
    assert "added_at" in item_dict
    assert "last_check" in item_dict
    assert "rate_limited_until" in item_dict
    assert isinstance(item_dict["added_at"], float)
    assert item_dict["added_at"] > 0
    assert item_dict["last_check"] == 0.0
    assert item_dict["rate_limited_until"] == 0.0


def test_check_now_resets_last_check():
    svc = _make_service()
    d = svc.add_url("https://www.tiktok.com/@testuser")
    item_id = d["id"]
    with svc._lock:
        item = next(i for i in svc._items if i.id == item_id)
        item.last_check = _time.time()
    assert svc.check_now(item_id) is True
    with svc._lock:
        item = next(i for i in svc._items if i.id == item_id)
        assert item.last_check == 0.0


def test_check_now_unknown_id_returns_false():
    svc = _make_service()
    assert svc.check_now("doesnotexist") is False


def test_check_now_error_state_resets_to_waiting():
    svc = _make_service()
    d = svc.add_url("https://www.tiktok.com/@testuser")
    item_id = d["id"]
    with svc._lock:
        item = next(i for i in svc._items if i.id == item_id)
        item.state = "ERROR"
        item.error_msg = "some error"
        item.consecutive_failures = 3
    assert svc.check_now(item_id) is True
    with svc._lock:
        item = next(i for i in svc._items if i.id == item_id)
        assert item.state == "WAITING"
        assert item.last_check == 0.0
        assert item.error_msg == ""
        assert item.consecutive_failures == 0


def test_check_now_noop_when_recording():
    svc = _make_service()
    d = svc.add_url("https://www.tiktok.com/@testuser")
    item_id = d["id"]
    with svc._lock:
        item = next(i for i in svc._items if i.id == item_id)
        item.state = "RECORDING"
        item.last_check = 999999.0
    assert svc.check_now(item_id) is True
    with svc._lock:
        item = next(i for i in svc._items if i.id == item_id)
        assert item.last_check == 999999.0
