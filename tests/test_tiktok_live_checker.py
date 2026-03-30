"""
tests/test_tiktok_live_checker.py
Tests for TikTok Live profile checker.

Covers:
  1.  is_tiktok_profile_url — accepts valid profile URLs
  2.  is_tiktok_profile_url — rejects live/video/tag URLs
  3.  extract_tiktok_username — extracts correctly
  4.  extract_tiktok_username — returns None for non-profile URLs
  5.  check_tiktok_live — returns live URL when __NEXT_DATA__ has status=2
  6.  check_tiktok_live — returns live URL via __UNIVERSAL_DATA__ path
  7.  check_tiktok_live — returns None when liveRoomInfo.status == 4 (ended)
  8.  check_tiktok_live — returns None when no liveRoomInfo in page
  9.  check_tiktok_live — returns None when no __NEXT_DATA__ blob
 10.  check_tiktok_live — raises RuntimeError on 404 (user not found)
 11.  check_tiktok_live — raises RuntimeError on 429 (rate-limit)
 12.  check_tiktok_live — raises RuntimeError on ConnectionError
 13.  check_tiktok_live — raises RuntimeError on Timeout
 14.  check_tiktok_live — live URL format is correct
 15.  check_tiktok_live — passes proxy to requests.get
 16.  _extract_live_status — path3 fallback scan detects status=2 + roomId
 17.  _extract_live_status — path3 does NOT fire on status=2 without roomId
 18.  _MonitorItem.profile_platform — field exists with default ""
 19.  LiveMonitorTab module — imports is_tiktok_profile_url and extract_tiktok_username
 20.  DownloadService — check_tiktok_profile_live method exists
 21.  ServiceFacade — check_tiktok_profile_live method exists
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# UI stubs — must run before any ui.* import
# Mirrors the pattern in test_queue_open_folder_fix.py
# ---------------------------------------------------------------------------

def _install_ui_stubs():
    """Stub tkinter, customtkinter and ui.themes so headless CI can import UI modules."""
    if "tkinter" not in sys.modules:
        tk_mod = types.ModuleType("tkinter")
        tk_mod.StringVar = MagicMock
        tk_mod.IntVar = MagicMock
        tk_mod.BooleanVar = MagicMock
        # Submodules that main_window.py imports at top level
        mb_mod = types.ModuleType("tkinter.messagebox")
        mb_mod.askyesno = MagicMock(return_value=False)
        tk_mod.messagebox = mb_mod
        sys.modules["tkinter"] = tk_mod
        sys.modules["tkinter.messagebox"] = mb_mod

    if "customtkinter" not in sys.modules:
        ctk = types.ModuleType("customtkinter")

        class _BaseWidget:
            def __init__(self, *a, **kw): pass
            def pack(self, **kw): pass
            def pack_forget(self): pass
            def grid(self, **kw): pass
            def winfo_exists(self): return True
            def winfo_ismapped(self): return False
            def configure(self, **kw): pass
            def cget(self, key): return ""
            def after(self, ms, fn=None, *args): pass

        ctk.CTk    = _BaseWidget  # MainWindow inherits from CTk
        ctk.CTkFrame  = _BaseWidget
        ctk.CTkLabel  = MagicMock
        ctk.CTkButton = MagicMock
        ctk.CTkEntry  = MagicMock
        ctk.CTkFont   = MagicMock
        ctk.CTkScrollableFrame = _BaseWidget
        ctk.CTkToplevel = _BaseWidget
        ctk.set_appearance_mode = MagicMock()
        ctk.set_default_color_theme = MagicMock()
        sys.modules["customtkinter"] = ctk

    for mod_name in ("ui.themes.tokens",):
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            T = MagicMock()
            T.register = lambda cb: None
            m.T = T
            sys.modules[mod_name] = m


_install_ui_stubs()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _next_data_page(user_data: dict) -> str:
    """Wrap user_data in a minimal __NEXT_DATA__ blob."""
    payload = {
        "props": {
            "pageProps": {
                "userInfo": {
                    "liveRoomInfo": user_data,
                }
            }
        }
    }
    return f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'


def _universal_data_page(user_data: dict) -> str:
    """Wrap user_data in a minimal __UNIVERSAL_DATA__ blob."""
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "userInfo": {
                    "liveRoomInfo": user_data,
                }
            }
        }
    }
    return f'<script id="__UNIVERSAL_DATA__">{json.dumps(payload)}</script>'


# ---------------------------------------------------------------------------
# 1-2. is_tiktok_profile_url
# ---------------------------------------------------------------------------

class TestIsTiktokProfileUrl:
    def test_accepts_simple_profile(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@someuser") is True

    def test_accepts_profile_with_trailing_slash(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@someuser/") is True

    def test_accepts_profile_with_query(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@user123?lang=en") is True

    def test_rejects_live_url(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@user/live") is False

    def test_rejects_video_url(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@user/video/123456") is False

    def test_rejects_non_tiktok(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.instagram.com/@user") is False

    def test_rejects_instagram_profile(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.instagram.com/someuser/") is False

    def test_rejects_plain_tiktok_root(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/") is False


# ---------------------------------------------------------------------------
# 3-4. extract_tiktok_username
# ---------------------------------------------------------------------------

class TestExtractTiktokUsername:
    def test_simple_username(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@baki_baby") == "baki_baby"

    def test_username_lowercased(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@CoolUser") == "cooluser"

    def test_returns_none_for_live_url(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@user/live") is None

    def test_returns_none_for_non_tiktok(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.instagram.com/user/") is None

    def test_username_with_dots(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@user.name") == "user.name"


# ---------------------------------------------------------------------------
# 5. __NEXT_DATA__ path — status=2 → live
# ---------------------------------------------------------------------------

class TestNextDataLiveDetection:
    def test_status_2_returns_live_url(self):
        page = _next_data_page({"status": 2, "roomId": "room123"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result == "https://www.tiktok.com/@testuser/live"

    def test_status_4_returns_none(self):
        """status=4 means stream ended."""
        page = _next_data_page({"status": 4, "roomId": "room123"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result is None

    def test_room_id_with_active_status_returns_live(self):
        """roomId present and status=2 → live."""
        page = _next_data_page({"status": 2, "roomId": "abc"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result is not None

    def test_empty_live_room_info_returns_none(self):
        page = _next_data_page({})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result is None


# ---------------------------------------------------------------------------
# 6. __UNIVERSAL_DATA__ path
# ---------------------------------------------------------------------------

class TestUniversalDataLiveDetection:
    def test_universal_data_status_2(self):
        page = _universal_data_page({"status": 2, "roomId": "room999"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("tiktokuser")
        assert result == "https://www.tiktok.com/@tiktokuser/live"

    def test_universal_data_status_4_returns_none(self):
        page = _universal_data_page({"status": 4, "roomId": "room999"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("tiktokuser")
        assert result is None


# ---------------------------------------------------------------------------
# 7-9. Returns None cases
# ---------------------------------------------------------------------------

class TestReturnsNone:
    def test_no_live_keywords_in_page(self):
        """Page with no liveRoomInfo or roomId → early return None."""
        resp = _make_response(200, "<html><body>normal profile</body></html>")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("regularuser")
        assert result is None

    def test_no_next_data_script_tag(self):
        """liveRoomInfo keyword present but no script blob → None."""
        resp = _make_response(200, "<div>liveRoomInfo sometext roomId</div>")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("user2")
        assert result is None

    def test_invalid_json_in_blob(self):
        """Malformed JSON in __NEXT_DATA__ → None (no crash)."""
        page = '<script id="__NEXT_DATA__">liveRoomInfo roomId {not valid json}</script>'
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("user3")
        assert result is None


# ---------------------------------------------------------------------------
# 10-11. HTTP error codes
# ---------------------------------------------------------------------------

class TestHttpErrors:
    def test_404_raises_runtime_error(self):
        resp = _make_response(404, "")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="not found"):
                check_tiktok_live("ghostuser")

    def test_429_raises_runtime_error(self):
        resp = _make_response(429, "")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="blocked"):
                check_tiktok_live("someuser")


# ---------------------------------------------------------------------------
# 12-13. Network errors
# ---------------------------------------------------------------------------

class TestNetworkErrors:
    def test_connection_error_raises_runtime_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.ConnectionError("timeout")):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="kết nối"):
                check_tiktok_live("user")

    def test_timeout_raises_runtime_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.Timeout()):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="hết thời gian"):
                check_tiktok_live("user")

    def test_request_exception_raises_runtime_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.RequestException("err")):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="HTTP"):
                check_tiktok_live("user")


# ---------------------------------------------------------------------------
# 14. Live URL format
# ---------------------------------------------------------------------------

class TestLiveUrlFormat:
    def test_live_url_contains_username(self):
        page = _next_data_page({"status": 2, "roomId": "r1"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("myuser")
        assert result == "https://www.tiktok.com/@myuser/live"

    def test_live_url_uses_passed_username(self):
        page = _next_data_page({"status": 2, "roomId": "r1"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("myuser")
        assert "@myuser/live" in result


# ---------------------------------------------------------------------------
# 15. Proxy forwarding
# ---------------------------------------------------------------------------

class TestProxyForwarding:
    def test_proxy_passed_to_requests(self):
        resp = _make_response(200, "<html>no live keywords</html>")
        with patch("requests.get", return_value=resp) as mock_get:
            from utils.tiktok_live_checker import check_tiktok_live
            check_tiktok_live("user", proxy="http://127.0.0.1:8080")
        _, kwargs = mock_get.call_args
        proxies = kwargs.get("proxies") or {}
        assert proxies.get("http") == "http://127.0.0.1:8080"
        assert proxies.get("https") == "http://127.0.0.1:8080"

    def test_no_proxy_passes_none(self):
        resp = _make_response(200, "<html>no live keywords</html>")
        with patch("requests.get", return_value=resp) as mock_get:
            from utils.tiktok_live_checker import check_tiktok_live
            check_tiktok_live("user", proxy="")
        _, kwargs = mock_get.call_args
        proxies = kwargs.get("proxies")
        assert proxies is None


# ---------------------------------------------------------------------------
# 16-17. _extract_live_status path3 fallback
# ---------------------------------------------------------------------------

class TestExtractLiveStatusPath3:
    def test_path3_fires_on_status2_with_room_id(self):
        """Path3 fallback: raw string contains 'status': 2 AND roomId."""
        from utils.tiktok_live_checker import _extract_live_status
        data = {"other": {"nested": {"status": 2, "roomId": "abc123"}}}
        assert _extract_live_status(data, "user") is True

    def test_path3_does_not_fire_without_room_id(self):
        """Path3 should NOT return True if roomId is absent (avoid false positives)."""
        from utils.tiktok_live_checker import _extract_live_status
        data = {"other": {"nested": {"status": 2}}}
        assert _extract_live_status(data, "user") is False

    def test_path3_does_not_fire_on_status_4(self):
        """status=4 without path1/2 data — path3 scans for status=2 only."""
        from utils.tiktok_live_checker import _extract_live_status
        data = {"other": {"nested": {"status": 4, "roomId": "abc"}}}
        assert _extract_live_status(data, "user") is False


# ---------------------------------------------------------------------------
# 18. _MonitorItem.profile_platform field
# (requires UI stubs installed at module level above)
# ---------------------------------------------------------------------------

class TestMonitorItemProfilePlatform:
    def test_profile_platform_field_exists_with_empty_default(self):
        """_MonitorItem must have profile_platform: str = ''."""
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(url="https://www.tiktok.com/@user")
        assert hasattr(item, "profile_platform")
        assert item.profile_platform == ""

    def test_profile_platform_can_be_set_to_tiktok(self):
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(
            url="https://www.tiktok.com/@user",
            profile_platform="tiktok",
            is_profile_watch=True,
            username="user",
        )
        assert item.profile_platform == "tiktok"

    def test_profile_platform_can_be_set_to_instagram(self):
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(
            url="https://www.instagram.com/user/",
            profile_platform="instagram",
            is_profile_watch=True,
            username="user",
        )
        assert item.profile_platform == "instagram"


# ---------------------------------------------------------------------------
# 19. LiveMonitorTab module-level imports
# ---------------------------------------------------------------------------

class TestLiveMonitorTabImports:
    def test_imports_is_tiktok_profile_url(self):
        import ui.tabs.live_monitor_tab as mod
        assert hasattr(mod, "is_tiktok_profile_url")

    def test_imports_extract_tiktok_username(self):
        import ui.tabs.live_monitor_tab as mod
        assert hasattr(mod, "extract_tiktok_username")


# ---------------------------------------------------------------------------
# 20. DownloadService.check_tiktok_profile_live
# ---------------------------------------------------------------------------

class TestDownloadServiceTiktok:
    def test_method_exists(self):
        from app.services.download_service import DownloadService
        assert callable(getattr(DownloadService, "check_tiktok_profile_live", None))

    def test_calls_on_error_for_invalid_url(self):
        """Non-TikTok URL → on_error called immediately (no thread needed)."""
        from app.services.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc._config = MagicMock()

        errors = []
        svc.check_tiktok_profile_live(
            url="https://www.instagram.com/user/",
            on_done=lambda u: None,
            on_error=lambda e: errors.append(e),
        )
        assert errors, "on_error should have been called for non-TikTok URL"

    def test_spawns_daemon_thread_for_valid_url(self):
        """Valid TikTok URL → a daemon thread is started."""
        import threading
        from app.services.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc._config = MagicMock()
        svc._config.proxy = ""

        threads_started = []

        def fake_start(self_thread):
            threads_started.append(self_thread)

        with patch.object(threading.Thread, "start", fake_start):
            svc.check_tiktok_profile_live(
                url="https://www.tiktok.com/@someuser",
                on_done=lambda u: None,
                on_error=lambda e: None,
            )

        assert len(threads_started) == 1
        assert threads_started[0].daemon is True


# ---------------------------------------------------------------------------
# 21. ServiceFacade.check_tiktok_profile_live
# ---------------------------------------------------------------------------

class TestServiceFacadeTiktok:
    def test_method_exists_on_facade(self):
        from ui.main_window import ServiceFacade
        assert callable(getattr(ServiceFacade, "check_tiktok_profile_live", None))

    def test_facade_delegates_to_service(self):
        from ui.main_window import ServiceFacade
        mock_svc = MagicMock()
        mock_cfg = MagicMock()
        facade = ServiceFacade(mock_svc, mock_cfg)

        on_done  = MagicMock()
        on_error = MagicMock()
        facade.check_tiktok_profile_live(
            url="https://www.tiktok.com/@user",
            on_done=on_done,
            on_error=on_error,
        )

        mock_svc.check_tiktok_profile_live.assert_called_once_with(
            url="https://www.tiktok.com/@user",
            on_done=on_done,
            on_error=on_error,
        )



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _next_data_page(user_data: dict) -> str:
    """Wrap user_data in a minimal __NEXT_DATA__ blob."""
    payload = {
        "props": {
            "pageProps": {
                "userInfo": {
                    "liveRoomInfo": user_data,
                }
            }
        }
    }
    return f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'


def _universal_data_page(user_data: dict) -> str:
    """Wrap user_data in a minimal __UNIVERSAL_DATA__ blob."""
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "userInfo": {
                    "liveRoomInfo": user_data,
                }
            }
        }
    }
    return f'<script id="__UNIVERSAL_DATA__">{json.dumps(payload)}</script>'


# ---------------------------------------------------------------------------
# 1-2. is_tiktok_profile_url
# ---------------------------------------------------------------------------

class TestIsTiktokProfileUrl:
    def test_accepts_simple_profile(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@someuser") is True

    def test_accepts_profile_with_trailing_slash(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@someuser/") is True

    def test_accepts_profile_with_query(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@user123?lang=en") is True

    def test_rejects_live_url(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@user/live") is False

    def test_rejects_video_url(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/@user/video/123456") is False

    def test_rejects_non_tiktok(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.instagram.com/@user") is False

    def test_rejects_instagram_profile(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.instagram.com/someuser/") is False

    def test_rejects_plain_tiktok_root(self):
        from utils.tiktok_live_checker import is_tiktok_profile_url
        assert is_tiktok_profile_url("https://www.tiktok.com/") is False


# ---------------------------------------------------------------------------
# 3-4. extract_tiktok_username
# ---------------------------------------------------------------------------

class TestExtractTiktokUsername:
    def test_simple_username(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@baki_baby") == "baki_baby"

    def test_username_lowercased(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@CoolUser") == "cooluser"

    def test_returns_none_for_live_url(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@user/live") is None

    def test_returns_none_for_non_tiktok(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.instagram.com/user/") is None

    def test_username_with_dots(self):
        from utils.tiktok_live_checker import extract_tiktok_username
        assert extract_tiktok_username("https://www.tiktok.com/@user.name") == "user.name"


# ---------------------------------------------------------------------------
# 5. __NEXT_DATA__ path — status=2 → live
# ---------------------------------------------------------------------------

class TestNextDataLiveDetection:
    def test_status_2_returns_live_url(self):
        page = _next_data_page({"status": 2, "roomId": "room123"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result == "https://www.tiktok.com/@testuser/live"

    def test_status_4_returns_none(self):
        """status=4 means stream ended."""
        page = _next_data_page({"status": 4, "roomId": "room123"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result is None

    def test_room_id_present_status_not_ended_returns_live(self):
        """roomId present and status is not 4/5 → live."""
        page = _next_data_page({"status": 2, "roomId": "abc"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result is not None

    def test_empty_live_room_info_returns_none(self):
        page = _next_data_page({})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("testuser")
        assert result is None


# ---------------------------------------------------------------------------
# 6. __UNIVERSAL_DATA__ path
# ---------------------------------------------------------------------------

class TestUniversalDataLiveDetection:
    def test_universal_data_status_2(self):
        page = _universal_data_page({"status": 2, "roomId": "room999"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("tiktokuser")
        assert result == "https://www.tiktok.com/@tiktokuser/live"

    def test_universal_data_status_4_returns_none(self):
        page = _universal_data_page({"status": 4, "roomId": "room999"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("tiktokuser")
        assert result is None


# ---------------------------------------------------------------------------
# 7-9. Returns None cases
# ---------------------------------------------------------------------------

class TestReturnsNone:
    def test_no_live_keywords_in_page(self):
        """Page with no liveRoomInfo or roomId → early return None."""
        resp = _make_response(200, "<html><body>normal profile</body></html>")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("regularuser")
        assert result is None

    def test_no_next_data_script_tag(self):
        """liveRoomInfo keyword present but no script blob → None."""
        resp = _make_response(200, "<div>liveRoomInfo sometext roomId</div>")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("user2")
        assert result is None

    def test_invalid_json_in_blob(self):
        """Malformed JSON in __NEXT_DATA__ → None (no crash)."""
        page = '<script id="__NEXT_DATA__">liveRoomInfo roomId {not valid json}</script>'
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("user3")
        assert result is None


# ---------------------------------------------------------------------------
# 10-11. HTTP error codes
# ---------------------------------------------------------------------------

class TestHttpErrors:
    def test_404_raises_runtime_error(self):
        resp = _make_response(404, "")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="not found"):
                check_tiktok_live("ghostuser")

    def test_429_raises_runtime_error(self):
        resp = _make_response(429, "")
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="blocked"):
                check_tiktok_live("someuser")


# ---------------------------------------------------------------------------
# 12-13. Network errors
# ---------------------------------------------------------------------------

class TestNetworkErrors:
    def test_connection_error_raises_runtime_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.ConnectionError("timeout")):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="kết nối"):
                check_tiktok_live("user")

    def test_timeout_raises_runtime_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.Timeout()):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="hết thời gian"):
                check_tiktok_live("user")

    def test_request_exception_raises_runtime_error(self):
        import requests as req
        with patch("requests.get", side_effect=req.exceptions.RequestException("err")):
            from utils.tiktok_live_checker import check_tiktok_live
            with pytest.raises(RuntimeError, match="HTTP"):
                check_tiktok_live("user")


# ---------------------------------------------------------------------------
# 14. Live URL format
# ---------------------------------------------------------------------------

class TestLiveUrlFormat:
    def test_live_url_contains_username(self):
        page = _next_data_page({"status": 2, "roomId": "r1"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("myuser")
        assert result == "https://www.tiktok.com/@myuser/live"

    def test_live_url_uses_lowercased_username(self):
        """Username passed in is used as-is — checker trusts caller."""
        page = _next_data_page({"status": 2, "roomId": "r1"})
        resp = _make_response(200, page)
        with patch("requests.get", return_value=resp):
            from utils.tiktok_live_checker import check_tiktok_live
            result = check_tiktok_live("myuser")
        assert "@myuser/live" in result


# ---------------------------------------------------------------------------
# 15. Proxy forwarding
# ---------------------------------------------------------------------------

class TestProxyForwarding:
    def test_proxy_passed_to_requests(self):
        resp = _make_response(200, "<html>no live keywords</html>")
        with patch("requests.get", return_value=resp) as mock_get:
            from utils.tiktok_live_checker import check_tiktok_live
            check_tiktok_live("user", proxy="http://127.0.0.1:8080")
        _, kwargs = mock_get.call_args
        proxies = kwargs.get("proxies") or {}
        assert proxies.get("http") == "http://127.0.0.1:8080"
        assert proxies.get("https") == "http://127.0.0.1:8080"

    def test_no_proxy_passes_none(self):
        resp = _make_response(200, "<html>no live keywords</html>")
        with patch("requests.get", return_value=resp) as mock_get:
            from utils.tiktok_live_checker import check_tiktok_live
            check_tiktok_live("user", proxy="")
        _, kwargs = mock_get.call_args
        proxies = kwargs.get("proxies")
        assert proxies is None


# ---------------------------------------------------------------------------
# 16-17. _extract_live_status path3 fallback
# ---------------------------------------------------------------------------

class TestExtractLiveStatusPath3:
    def test_path3_fires_on_status2_with_room_id(self):
        """Path3 fallback: raw string contains 'status': 2 AND roomId."""
        from utils.tiktok_live_checker import _extract_live_status
        data = {"other": {"nested": {"status": 2, "roomId": "abc123"}}}
        assert _extract_live_status(data, "user") is True

    def test_path3_does_not_fire_without_room_id(self):
        """Path3 should NOT return True if roomId is absent (avoid false positives)."""
        from utils.tiktok_live_checker import _extract_live_status
        data = {"other": {"nested": {"status": 2}}}
        assert _extract_live_status(data, "user") is False

    def test_path3_does_not_fire_on_status_4(self):
        """status=4 without path1/2 data — path3 scans for status=2 only."""
        from utils.tiktok_live_checker import _extract_live_status
        data = {"other": {"nested": {"status": 4, "roomId": "abc"}}}
        assert _extract_live_status(data, "user") is False


# ---------------------------------------------------------------------------
# 18. _MonitorItem.profile_platform field
# ---------------------------------------------------------------------------

class TestMonitorItemProfilePlatform:
    def test_profile_platform_field_exists_with_empty_default(self):
        """_MonitorItem must have profile_platform: str = ''."""
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(url="https://www.tiktok.com/@user")
        assert hasattr(item, "profile_platform")
        assert item.profile_platform == ""

    def test_profile_platform_can_be_set_to_tiktok(self):
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(
            url="https://www.tiktok.com/@user",
            profile_platform="tiktok",
            is_profile_watch=True,
            username="user",
        )
        assert item.profile_platform == "tiktok"

    def test_profile_platform_can_be_set_to_instagram(self):
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(
            url="https://www.instagram.com/user/",
            profile_platform="instagram",
            is_profile_watch=True,
            username="user",
        )
        assert item.profile_platform == "instagram"


# ---------------------------------------------------------------------------
# 19. LiveMonitorTab imports
# ---------------------------------------------------------------------------

class TestLiveMonitorTabImports:
    def test_imports_is_tiktok_profile_url(self):
        import ui.tabs.live_monitor_tab as mod
        assert hasattr(mod, "is_tiktok_profile_url")

    def test_imports_extract_tiktok_username(self):
        import ui.tabs.live_monitor_tab as mod
        assert hasattr(mod, "extract_tiktok_username")


# ---------------------------------------------------------------------------
# 20. DownloadService.check_tiktok_profile_live
# ---------------------------------------------------------------------------

class TestDownloadServiceTiktok:
    def test_method_exists(self):
        from app.services.download_service import DownloadService
        assert callable(getattr(DownloadService, "check_tiktok_profile_live", None))

    def test_calls_on_error_for_invalid_url(self):
        """Non-TikTok URL → on_error called immediately (no thread needed)."""
        from app.services.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc._config = MagicMock()

        errors = []
        svc.check_tiktok_profile_live(
            url="https://www.instagram.com/user/",
            on_done=lambda u: None,
            on_error=lambda e: errors.append(e),
        )
        assert errors, "on_error should have been called for non-TikTok URL"

    def test_spawns_daemon_thread_for_valid_url(self):
        """Valid TikTok URL → a daemon thread is started."""
        import threading
        from app.services.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc._config = MagicMock()
        svc._config.proxy = ""

        threads_started = []
        original_start = threading.Thread.start

        def fake_start(self_thread):
            threads_started.append(self_thread)

        with patch.object(threading.Thread, "start", fake_start):
            svc.check_tiktok_profile_live(
                url="https://www.tiktok.com/@someuser",
                on_done=lambda u: None,
                on_error=lambda e: None,
            )

        assert len(threads_started) == 1
        assert threads_started[0].daemon is True


# ---------------------------------------------------------------------------
# 21. ServiceFacade.check_tiktok_profile_live
# ---------------------------------------------------------------------------

class TestServiceFacadeTiktok:
    def test_method_exists_on_facade(self):
        from ui.main_window import ServiceFacade
        assert callable(getattr(ServiceFacade, "check_tiktok_profile_live", None))

    def test_facade_delegates_to_service(self):
        from ui.main_window import ServiceFacade
        mock_svc = MagicMock()
        mock_cfg = MagicMock()
        facade = ServiceFacade(mock_svc, mock_cfg)

        on_done  = MagicMock()
        on_error = MagicMock()
        facade.check_tiktok_profile_live(
            url="https://www.tiktok.com/@user",
            on_done=on_done,
            on_error=on_error,
        )

        mock_svc.check_tiktok_profile_live.assert_called_once_with(
            url="https://www.tiktok.com/@user",
            on_done=on_done,
            on_error=on_error,
        )
