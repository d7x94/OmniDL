"""Tests for the live monitor tab audit fixes.

Covers (without instantiating Qt widgets — same pattern as
test_instagram_live_fix.py):

1. B1 — _poll() runs scheduling work even when the tab is hidden.
2. B2 — check_tiktok_profile_live() resolves the username in the worker
   thread, never on the caller (UI) thread; short links are resolved there too.
3. B4/B5 — _is_stale_check() membership/state guard replaces the global
   token, and only the owning item releases the in-flight slot.
4. B6 — _add_url() dedupes profile watches by (platform, username), and
   classifies vt/vm short links without network resolution.
"""

from __future__ import annotations

import threading
import types

# ---------------------------------------------------------------------------
# Stubs — LiveMonitorTab methods are called unbound with a SimpleNamespace
# standing in for self, so no QApplication is needed.
# ---------------------------------------------------------------------------


class _StubApp:
    def __init__(self):
        self.toasts = []
        self.config = types.SimpleNamespace(proxy="", cookie_file="")

    def toast(self, msg, level="info"):
        self.toasts.append((msg, level))


class _StubEntry:
    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def clear(self):
        self._t = ""


def _make_tab_stub(url=""):
    from ui.tabs.live_monitor_tab import LiveMonitorTab

    tab = types.SimpleNamespace()
    tab._app = _StubApp()
    tab._items = []
    tab._checking_item = None
    tab._url_entry = _StubEntry(url)
    tab._rebuilt = []
    tab._rebuild_item_ui = tab._rebuilt.append
    tab._update_cookie_banner = lambda: None
    tab._update_status = lambda: None
    tab._short_url = LiveMonitorTab._short_url
    return tab


# ---------------------------------------------------------------------------
# B4/B5 — _is_stale_check
# ---------------------------------------------------------------------------


class TestStaleCheckGuard:
    def _item(self, state):
        from ui.tabs.live_monitor_tab import _MonitorItem

        item = _MonitorItem(url="https://www.tiktok.com/@u")
        item.state = state
        return item

    def test_fresh_check_not_stale_and_releases_slot(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub()
        item = self._item(_MonitorState.CHECKING)
        tab._items = [item]
        tab._checking_item = item

        assert LiveMonitorTab._is_stale_check(tab, item) is False
        assert tab._checking_item is None

    def test_removed_item_is_stale(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub()
        item = self._item(_MonitorState.CHECKING)
        tab._checking_item = item  # in flight, but item already removed

        assert LiveMonitorTab._is_stale_check(tab, item) is True
        assert tab._checking_item is None

    def test_recovered_item_is_stale(self):
        """Late callback after _recover_stuck_checks reset the item to WAITING."""
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub()
        item = self._item(_MonitorState.WAITING)
        tab._items = [item]

        assert LiveMonitorTab._is_stale_check(tab, item) is True

    def test_late_callback_does_not_clobber_other_items_slot(self):
        """A stale callback must not release a slot owned by another item."""
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub()
        recovered = self._item(_MonitorState.WAITING)
        current = self._item(_MonitorState.CHECKING)
        tab._items = [recovered, current]
        tab._checking_item = current

        assert LiveMonitorTab._is_stale_check(tab, recovered) is True
        assert tab._checking_item is current

    def test_removing_one_item_does_not_invalidate_others(self):
        """B4: with membership checks, no global token to poison siblings."""
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub()
        removed = self._item(_MonitorState.CHECKING)
        other = self._item(_MonitorState.CHECKING)
        tab._items = [other]  # 'removed' already gone
        tab._checking_item = other

        assert LiveMonitorTab._is_stale_check(tab, removed) is True
        # other's in-flight check is still valid
        assert LiveMonitorTab._is_stale_check(tab, other) is False


# ---------------------------------------------------------------------------
# B1 — _poll runs while hidden
# ---------------------------------------------------------------------------


class TestPollWhileHidden:
    def test_scheduling_runs_when_hidden(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        calls = []
        tab = types.SimpleNamespace(
            _paused=False,
            isVisible=lambda: False,
            _refresh_recording_items=lambda: calls.append("recording"),
            _recover_stuck_checks=lambda: calls.append("recover"),
            _enqueue_next_check=lambda: calls.append("enqueue"),
            _update_cookie_banner=lambda: calls.append("banner"),
            _update_status=lambda: calls.append("status"),
        )

        LiveMonitorTab._poll(tab)

        assert "recording" in calls
        assert "recover" in calls
        assert "enqueue" in calls
        # cosmetic refreshes stay gated on visibility
        assert "banner" not in calls
        assert "status" not in calls

    def test_cosmetics_run_when_visible(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        calls = []
        tab = types.SimpleNamespace(
            _paused=False,
            isVisible=lambda: True,
            _refresh_recording_items=lambda: calls.append("recording"),
            _recover_stuck_checks=lambda: calls.append("recover"),
            _enqueue_next_check=lambda: calls.append("enqueue"),
            _update_cookie_banner=lambda: calls.append("banner"),
            _update_status=lambda: calls.append("status"),
        )

        LiveMonitorTab._poll(tab)

        assert calls == ["recording", "recover", "enqueue", "banner", "status"]


# ---------------------------------------------------------------------------
# B6 — _add_url dedupe + short-link classification
# ---------------------------------------------------------------------------


class TestAddUrlDedupe:
    def test_profile_dedupe_by_username_across_url_forms(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub("https://www.tiktok.com/@someuser")
        LiveMonitorTab._add_url(tab)
        assert len(tab._items) == 1

        # Same profile, different URL form (item.url may also have been
        # rewritten to the live URL after a live was detected).
        tab._items[0].url = "https://www.tiktok.com/@someuser/live"
        tab._items[0].state = _MonitorState.RECORDING
        tab._url_entry = _StubEntry("https://www.tiktok.com/@someuser")
        LiveMonitorTab._add_url(tab)

        assert len(tab._items) == 1
        assert any("theo dõi" in msg for msg, _ in tab._app.toasts)

    def test_ended_item_does_not_block_readd(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        tab = _make_tab_stub("https://www.tiktok.com/@someuser")
        LiveMonitorTab._add_url(tab)
        tab._items[0].state = _MonitorState.ENDED

        tab._url_entry = _StubEntry("https://www.tiktok.com/@someuser")
        LiveMonitorTab._add_url(tab)
        assert len(tab._items) == 2

    def test_short_link_classified_without_network(self, monkeypatch):
        import utils.tiktok_live_checker as ttlc
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        def _boom(*a, **k):
            raise AssertionError("short link must not be resolved on the UI thread")

        monkeypatch.setattr(ttlc, "_resolve_short_link", _boom)

        tab = _make_tab_stub("https://vt.tiktok.com/ZS9N8sGVN/")
        LiveMonitorTab._add_url(tab)

        assert len(tab._items) == 1
        item = tab._items[0]
        assert item.is_profile_watch is True
        assert item.profile_platform == "tiktok"
        assert item.username == ""

    def test_live_url_username_extracted(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        tab = _make_tab_stub("https://www.tiktok.com/@LiveUser/live")
        LiveMonitorTab._add_url(tab)

        assert len(tab._items) == 1
        assert tab._items[0].username == "liveuser"
        assert tab._items[0].is_profile_watch is True


# ---------------------------------------------------------------------------
# B2 — check_tiktok_profile_live off the UI thread
# ---------------------------------------------------------------------------


class TestTikTokCheckOffUIThread:
    def _run(self, url, monkeypatch, extract_result="someuser"):
        import app.services.download_service as ds
        import utils.tiktok_live_checker as ttlc

        seen = {}
        done = threading.Event()

        def _fake_extract(target, proxy=""):
            seen["extract_thread"] = threading.current_thread()
            return extract_result

        def _fake_check(username, proxy="", cookie_file=""):
            seen["username"] = username
            return None

        monkeypatch.setattr(ttlc, "extract_tiktok_username", _fake_extract)
        monkeypatch.setattr(ttlc, "check_tiktok_live", _fake_check)
        monkeypatch.setattr(ds, "_resolve_cookie", lambda *a, **k: "")

        svc = types.SimpleNamespace(_config=types.SimpleNamespace(proxy=""))

        results = {}

        def on_done(live_url):
            results["done"] = live_url
            done.set()

        def on_error(err):
            results["error"] = err
            done.set()

        ds.DownloadService.check_tiktok_profile_live(svc, url, on_done, on_error)
        assert done.wait(5), "callback never fired"
        return seen, results

    def test_username_extraction_runs_in_worker_thread(self, monkeypatch):
        seen, results = self._run("https://www.tiktok.com/@someuser", monkeypatch)
        assert seen["extract_thread"] is not threading.main_thread()
        assert results == {"done": None}
        assert seen["username"] == "someuser"

    def test_extraction_failure_reports_error_async(self, monkeypatch):
        import utils.tiktok_live_checker as ttlc

        monkeypatch.setattr(ttlc, "extract_tiktok_username_from_live_url", lambda u: None)
        seen, results = self._run("https://www.tiktok.com/@someuser", monkeypatch, extract_result=None)
        assert "error" in results
        assert "username" in results["error"].lower() or "URL" in results["error"]

    def test_short_link_resolved_in_worker(self, monkeypatch):
        import utils.tiktok_live_checker as ttlc

        resolve_thread = {}

        def _fake_resolve(url, proxy=""):
            resolve_thread["thread"] = threading.current_thread()
            return "https://www.tiktok.com/@resolveduser/live"

        monkeypatch.setattr(ttlc, "_resolve_short_link", _fake_resolve)
        seen, results = self._run("https://vt.tiktok.com/ZS9N8sGVN/", monkeypatch, extract_result=None)
        assert resolve_thread["thread"] is not threading.main_thread()
        # falls back to the live-URL extractor on the resolved URL
        assert seen["username"] == "resolveduser"
        assert results == {"done": None}


# ---------------------------------------------------------------------------
# M5 — instagram.com/<user>/live URLs are watched as profiles, not probed
# directly with the 120s CDP browser on every check.
# ---------------------------------------------------------------------------


class TestInstagramLiveUrlClassification:
    def test_user_live_url_becomes_profile_watch(self, monkeypatch):
        import infrastructure.downloader.yt_dlp_engine as yde
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        monkeypatch.setattr(yde, "_resolve_cookie", lambda *a, **k: "/tmp/cookies.txt")  # nosec B108

        tab = _make_tab_stub("https://www.instagram.com/SomeUser/live/")
        LiveMonitorTab._add_url(tab)

        assert len(tab._items) == 1
        item = tab._items[0]
        assert item.is_profile_watch is True
        assert item.profile_platform == "instagram"
        assert item.username == "someuser"
        assert item.url == "https://www.instagram.com/someuser/"
        assert item.watch_url == item.url

    def test_live_id_url_stays_direct(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        tab = _make_tab_stub("https://www.instagram.com/live/17912345/")
        LiveMonitorTab._add_url(tab)

        assert len(tab._items) == 1
        assert tab._items[0].is_profile_watch is False

    def test_user_live_url_dedupes_against_profile_watch(self, monkeypatch):
        import infrastructure.downloader.yt_dlp_engine as yde
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        monkeypatch.setattr(yde, "_resolve_cookie", lambda *a, **k: "/tmp/cookies.txt")  # nosec B108

        tab = _make_tab_stub("https://www.instagram.com/someuser/")
        LiveMonitorTab._add_url(tab)
        tab._url_entry = _StubEntry("https://www.instagram.com/someuser/live/")
        LiveMonitorTab._add_url(tab)

        assert len(tab._items) == 1


# ---------------------------------------------------------------------------
# M6 — a LIVE item holding the in-flight slot is recovered by the stuck check
# ---------------------------------------------------------------------------


class TestStuckLiveSlotRecovery:
    def _item(self, state):
        from ui.tabs.live_monitor_tab import _MonitorItem

        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.state = state
        return item

    def test_stuck_live_slot_holder_released(self):
        import time

        from ui.tabs.live_monitor_tab import (
            _CHECKING_TIMEOUT_S,
            LiveMonitorTab,
            _MonitorState,
        )

        tab = _make_tab_stub()
        item = self._item(_MonitorState.LIVE)
        item.last_check = time.time() - (_CHECKING_TIMEOUT_S + 10)
        tab._items = [item]
        tab._checking_item = item
        tab._refresh_item_ui = lambda i: None

        LiveMonitorTab._recover_stuck_checks(tab)

        assert tab._checking_item is None
        assert item.state in (_MonitorState.WAITING, _MonitorState.ERROR)

    def test_live_item_without_slot_untouched(self):
        import time

        from ui.tabs.live_monitor_tab import (
            _CHECKING_TIMEOUT_S,
            LiveMonitorTab,
            _MonitorState,
        )

        tab = _make_tab_stub()
        item = self._item(_MonitorState.LIVE)
        item.last_check = time.time() - (_CHECKING_TIMEOUT_S + 10)
        tab._items = [item]
        tab._refresh_item_ui = lambda i: None

        LiveMonitorTab._recover_stuck_checks(tab)

        assert item.state == _MonitorState.LIVE


# ---------------------------------------------------------------------------
# M7 — profile watch re-arms after a recording finishes or fails
# ---------------------------------------------------------------------------


class TestProfileWatchRearm:
    def _recording_item(self):
        from ui.tabs.live_monitor_tab import _MonitorItem, _MonitorState

        item = _MonitorItem(
            url="https://www.instagram.com/u/live/",
            is_profile_watch=True,
            profile_platform="instagram",
            username="u",
            watch_url="https://www.instagram.com/u/",
        )
        item.state = _MonitorState.RECORDING
        item.task_id = "t1"
        return item

    def _tab_with_task(self, snapshot):
        from ui.tabs.live_monitor_tab import LiveMonitorTab

        tab = _make_tab_stub()
        tab._refresh_item_ui = lambda i: None
        task = types.SimpleNamespace(snapshot=lambda: snapshot)
        tab._app.service = types.SimpleNamespace(get_task=lambda tid: task)
        tab._respawn_watch = lambda fin: LiveMonitorTab._respawn_watch(tab, fin)
        return tab

    def test_completed_recording_respawns_watch(self):
        from domain.enums.download_status import DownloadStatus
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        item = self._recording_item()
        tab = self._tab_with_task({"status": DownloadStatus.COMPLETED, "filename": "/x/y.ts"})
        tab._items = [item]

        LiveMonitorTab._refresh_recording_items(tab)

        assert item.state == _MonitorState.ENDED
        waiting = [i for i in tab._items if i.state == _MonitorState.WAITING]
        assert len(waiting) == 1
        assert waiting[0].url == "https://www.instagram.com/u/"
        assert waiting[0].is_profile_watch is True

    def test_failed_recording_rearms_same_item(self):
        from domain.enums.download_status import DownloadStatus
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        item = self._recording_item()
        tab = self._tab_with_task({"status": DownloadStatus.FAILED, "error_msg": "stream died"})
        tab._items = [item]

        LiveMonitorTab._refresh_recording_items(tab)

        assert item.state == _MonitorState.WAITING
        assert item.task_id is None
        assert item.url == "https://www.instagram.com/u/"
        assert item.consecutive_failures == 1
        assert len(tab._items) == 1  # no clone for failures

    def test_cancelled_recording_stays_terminal(self):
        from domain.enums.download_status import DownloadStatus
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorState

        item = self._recording_item()
        tab = self._tab_with_task({"status": DownloadStatus.CANCELLED})
        tab._items = [item]

        LiveMonitorTab._refresh_recording_items(tab)

        assert item.state == _MonitorState.ERROR
        assert len(tab._items) == 1

    def test_non_profile_failed_stays_error(self):
        from domain.enums.download_status import DownloadStatus
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem, _MonitorState

        item = _MonitorItem(url="https://www.instagram.com/live/123/")
        item.state = _MonitorState.RECORDING
        item.task_id = "t1"
        tab = self._tab_with_task({"status": DownloadStatus.FAILED})
        tab._items = [item]

        LiveMonitorTab._refresh_recording_items(tab)

        assert item.state == _MonitorState.ERROR

    def test_no_duplicate_respawn_when_watch_already_active(self):
        from domain.enums.download_status import DownloadStatus
        from ui.tabs.live_monitor_tab import LiveMonitorTab, _MonitorItem

        item = self._recording_item()
        existing = _MonitorItem(
            url="https://www.instagram.com/u/",
            is_profile_watch=True,
            profile_platform="instagram",
            username="u",
            watch_url="https://www.instagram.com/u/",
        )
        tab = self._tab_with_task({"status": DownloadStatus.COMPLETED, "filename": "/x/y.ts"})
        tab._items = [item, existing]

        LiveMonitorTab._refresh_recording_items(tab)

        assert len(tab._items) == 2  # ENDED item + the pre-existing watch
