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
            def __init__(self, *a, **kw):
                pass

            def pack(self, **kw):
                pass

            def pack_forget(self):
                pass

            def grid(self, **kw):
                pass

            def winfo_exists(self):
                return True

            def winfo_ismapped(self):
                return False

            def configure(self, **kw):
                pass

            def cget(self, key):
                return ""

            def after(self, ms, fn=None, *args):
                pass

        ctk.CTk = _BaseWidget  # MainWindow inherits from CTk
        ctk.CTkFrame = _BaseWidget
        ctk.CTkLabel = MagicMock
        ctk.CTkButton = MagicMock
        ctk.CTkEntry = MagicMock
        ctk.CTkFont = MagicMock
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


def _make_response(status: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _make_session_mock(resp: MagicMock) -> MagicMock:
    """Return a mock session whose .get() returns *resp*."""
    session = MagicMock()
    session.get.return_value = resp
    session.cookies = MagicMock()
    return session


def _patch_session(resp: MagicMock):
    """Context manager: patch _get_impersonate_session to return a mock session
    and also suppress _verify_room_alive so tests don't need webcast API calls.
    """
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        session = _make_session_mock(resp)
        with (
            patch(
                "utils.tiktok_live_checker._get_impersonate_session",
                return_value=session,
            ),
            patch(
                "utils.tiktok_live_checker._verify_room_alive",
                return_value=True,
            ),
            patch(
                "utils.tiktok_live_checker._ROOM_ID_CACHE",
                {},
            ),
        ):
            yield session

    return _ctx()


# _next_data_page uses __NEXT_DATA__ but _room_id_from_profile_page reads
# __UNIVERSAL_DATA_FOR_REHYDRATION__ and __NEXT_DATA__.  roomId in test data
# must be a numeric string (non-zero) because _valid_room_id rejects
# non-numeric values.  Helpers below embed a 10-digit numeric roomId so the
# raw-HTML regex pass-3 also works as fallback.
_NUMERIC_ROOM_ID = "1234567890"


def _next_data_page(user_data: dict) -> str:
    """Wrap user_data in a minimal __NEXT_DATA__ blob.

    If user_data contains a roomId, replace it with a valid numeric ID so that
    _valid_room_id() accepts it.  status is preserved as-is.
    """
    import copy

    data = copy.deepcopy(user_data)
    if "roomId" in data and data["roomId"]:
        data["roomId"] = _NUMERIC_ROOM_ID
    payload = {
        "props": {
            "pageProps": {
                "userInfo": {
                    "liveRoomInfo": data,
                }
            }
        }
    }
    return f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'


def _universal_data_page(user_data: dict) -> str:
    """Wrap user_data in a minimal __UNIVERSAL_DATA_FOR_REHYDRATION__ blob."""
    import copy

    data = copy.deepcopy(user_data)
    if "roomId" in data and data["roomId"]:
        data["roomId"] = _NUMERIC_ROOM_ID
    payload = {
        "__DEFAULT_SCOPE__": {
            "webapp.user-detail": {
                "userInfo": {
                    "liveRoomInfo": data,
                }
            }
        }
    }
    return f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{json.dumps(payload)}</script>'


class TestNextDataLiveDetection:
    def test_status_2_returns_live_url(self):
        page = _next_data_page({"status": 2, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("testuser")
        assert result == "https://www.tiktok.com/@testuser/live"

    def test_status_4_with_room_id_returns_live(self):
        """BUG-TT-25: TikTok returns status=4 for unsigned requests even when
        stream is active. roomId present → return live URL optimistically."""
        page = _next_data_page({"status": 4, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("testuser")
        assert result == "https://www.tiktok.com/@testuser/live"

    def test_room_id_present_status_not_ended_returns_live(self):
        """roomId present and status is not 4/5 → live."""
        page = _next_data_page({"status": 2, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("testuser")
        assert result is not None

    def test_empty_live_room_info_returns_none(self):
        page = _next_data_page({})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("testuser")
        assert result is None


# ---------------------------------------------------------------------------
# 6. __UNIVERSAL_DATA__ path
# ---------------------------------------------------------------------------


class TestUniversalDataLiveDetection:
    def test_universal_data_status_2(self):
        page = _universal_data_page({"status": 2, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("tiktokuser")
        assert result == "https://www.tiktok.com/@tiktokuser/live"

    def test_universal_data_status_4_with_room_id_returns_live(self):
        """BUG-TT-25: status=4 with roomId → live URL (TikTok serves status=4
        for unsigned requests even when stream is active)."""
        page = _universal_data_page({"status": 4, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("tiktokuser")
        assert result == "https://www.tiktok.com/@tiktokuser/live"


# ---------------------------------------------------------------------------
# 7-9. Returns None cases
# ---------------------------------------------------------------------------


class TestReturnsNone:
    def test_no_live_keywords_in_page(self):
        """Page with no liveRoomInfo or roomId → early return None."""
        resp = _make_response(200, "<html><body>normal profile</body></html>")
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("regularuser")
        assert result is None

    def test_no_next_data_script_tag(self):
        """liveRoomInfo keyword present but no script blob → None."""
        resp = _make_response(200, "<div>liveRoomInfo sometext roomId</div>")
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("user2")
        assert result is None

    def test_invalid_json_in_blob(self):
        """Malformed JSON in __NEXT_DATA__ → None (no crash)."""
        page = '<script id="__NEXT_DATA__">liveRoomInfo roomId {not valid json}</script>'
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("user3")
        assert result is None


# ---------------------------------------------------------------------------
# 10-11. HTTP error codes
# ---------------------------------------------------------------------------


class TestHttpErrors:
    def test_404_raises_runtime_error(self):
        resp = _make_response(404, "")
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            with pytest.raises(RuntimeError, match="not found"):
                check_tiktok_live("ghostuser")

    def test_429_raises_runtime_error(self):
        resp = _make_response(429, "")
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            with pytest.raises(RuntimeError, match="blocked"):
                check_tiktok_live("someuser")


# ---------------------------------------------------------------------------
# 12-13. Network errors
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    def test_connection_error_raises_runtime_error(self):
        # curl_cffi raises its own ConnectionError; source catches any Exception
        # and re-raises RuntimeError if "connect" in message.
        session = MagicMock()
        session.get.side_effect = Exception("connect: connection refused")
        session.cookies = MagicMock()
        with patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=session,
        ):
            from utils.tiktok_live_checker import check_tiktok_live

            with pytest.raises(RuntimeError, match="connect"):
                check_tiktok_live("user")

    def test_timeout_raises_runtime_error(self):
        session = MagicMock()
        session.get.side_effect = Exception("timeout exceeded")
        session.cookies = MagicMock()
        with patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=session,
        ):
            from utils.tiktok_live_checker import check_tiktok_live

            with pytest.raises(RuntimeError, match="timeout|hết thời gian"):
                check_tiktok_live("user")

    def test_request_exception_raises_runtime_error(self):
        session = MagicMock()
        session.get.side_effect = Exception("some HTTP error")
        session.cookies = MagicMock()
        with patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=session,
        ):
            from utils.tiktok_live_checker import check_tiktok_live

            with pytest.raises(RuntimeError, match="HTTP"):
                check_tiktok_live("user")


# ---------------------------------------------------------------------------
# 14. Live URL format
# ---------------------------------------------------------------------------


class TestLiveUrlFormat:
    def test_live_url_contains_username(self):
        page = _next_data_page({"status": 2, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("myuser")
        assert result == "https://www.tiktok.com/@myuser/live"

    def test_live_url_uses_lowercased_username(self):
        """Username passed in is used as-is — checker trusts caller."""
        page = _next_data_page({"status": 2, "roomId": _NUMERIC_ROOM_ID})
        resp = _make_response(200, page)
        with _patch_session(resp):
            from utils.tiktok_live_checker import check_tiktok_live

            result = check_tiktok_live("myuser")
        assert "@myuser/live" in result


# ---------------------------------------------------------------------------
# 15. Proxy forwarding
# ---------------------------------------------------------------------------


class TestProxyForwarding:
    def test_proxy_passed_to_requests(self):
        resp = _make_response(200, "<html>no live keywords</html>")
        session = _make_session_mock(resp)
        with patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=session,
        ):
            from utils.tiktok_live_checker import check_tiktok_live

            check_tiktok_live("user", proxy="http://127.0.0.1:8080")
        _, kwargs = session.get.call_args
        proxies = kwargs.get("proxies") or {}
        assert proxies.get("http") == "http://127.0.0.1:8080"
        assert proxies.get("https") == "http://127.0.0.1:8080"

    def test_no_proxy_passes_none(self):
        resp = _make_response(200, "<html>no live keywords</html>")
        session = _make_session_mock(resp)
        with patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=session,
        ):
            from utils.tiktok_live_checker import check_tiktok_live

            check_tiktok_live("user", proxy="")
        _, kwargs = session.get.call_args
        proxies = kwargs.get("proxies")
        assert proxies is None


# ---------------------------------------------------------------------------
# 16-17. _extract_live_status path3 fallback
# ---------------------------------------------------------------------------


class TestExtractLiveStatusPath3:
    def test_path3_fires_on_status2_with_room_id(self):
        """Path3 fallback: raw string contains 'status': 2 AND roomId."""
        from utils.tiktok_live_checker import _extract_live_status

        data = {"other": {"nested": {"status": 2, "roomId": "1234567890"}}}
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

        on_done = MagicMock()
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
# 22-26. Short-link resolution (BUG-CH FIX)
# ---------------------------------------------------------------------------


class TestShortLinkResolution:
    """vt.tiktok.com and vm.tiktok.com short links must be resolved before
    the profile regex is applied — otherwise Live Monitor ignores them."""

    def _head_resp(self, final_url: str) -> MagicMock:
        resp = MagicMock()
        resp.url = final_url
        return resp

    def test_vt_short_link_recognised_as_profile(self):
        """vt.tiktok.com link that resolves to /@username -> True."""
        from utils.tiktok_live_checker import is_tiktok_profile_url

        resolved = "https://www.tiktok.com/@q_kiet2212"
        with patch("requests.head", return_value=self._head_resp(resolved)):
            assert is_tiktok_profile_url("https://vt.tiktok.com/ZS9N8sGVN33Go-yNEKU/") is True

    def test_vm_short_link_recognised_as_profile(self):
        """vm.tiktok.com link that resolves to /@username -> True."""
        from utils.tiktok_live_checker import is_tiktok_profile_url

        resolved = "https://www.tiktok.com/@someuser"
        with patch("requests.head", return_value=self._head_resp(resolved)):
            assert is_tiktok_profile_url("https://vm.tiktok.com/ABCDEF/") is True

    def test_vt_short_link_extract_username(self):
        """extract_tiktok_username resolves short link and returns username."""
        from utils.tiktok_live_checker import extract_tiktok_username

        resolved = "https://www.tiktok.com/@q_kiet2212"
        with patch("requests.head", return_value=self._head_resp(resolved)):
            assert extract_tiktok_username("https://vt.tiktok.com/ZS9N8sGVN33Go-yNEKU/") == "q_kiet2212"

    def test_short_link_resolving_to_video_returns_false(self):
        """Short link that resolves to a video URL -> False (not a profile)."""
        from utils.tiktok_live_checker import is_tiktok_profile_url

        resolved = "https://www.tiktok.com/@user/video/123456789"
        with patch("requests.head", return_value=self._head_resp(resolved)):
            assert is_tiktok_profile_url("https://vt.tiktok.com/ZZZZ/") is False

    def test_short_link_resolve_network_error_returns_false(self):
        """If HEAD request fails, short link is not mistaken for a profile."""
        import requests as req

        from utils.tiktok_live_checker import is_tiktok_profile_url

        with patch("requests.head", side_effect=req.exceptions.ConnectionError("fail")):
            # Falls back to original URL which doesn't match _PROFILE_RE -> False
            assert is_tiktok_profile_url("https://vt.tiktok.com/ZS9N8sGVN33Go-yNEKU/") is False

    def test_canonical_url_does_not_call_head(self):
        """Non-short-link URLs must NOT trigger a HEAD request."""
        from utils.tiktok_live_checker import is_tiktok_profile_url

        with patch("requests.head") as mock_head:
            result = is_tiktok_profile_url("https://www.tiktok.com/@someuser")
        mock_head.assert_not_called()
        assert result is True

    def test_extract_username_canonical_no_head(self):
        """extract_tiktok_username on canonical URL must NOT trigger HEAD."""
        from utils.tiktok_live_checker import extract_tiktok_username

        with patch("requests.head") as mock_head:
            result = extract_tiktok_username("https://www.tiktok.com/@q_kiet2212")
        mock_head.assert_not_called()
        assert result == "q_kiet2212"

    def test_proxy_forwarded_to_head_request(self):
        """proxy param is forwarded to requests.head during short-link resolve."""
        from utils.tiktok_live_checker import is_tiktok_profile_url

        resolved = "https://www.tiktok.com/@user"
        resp = self._head_resp(resolved)
        with patch("requests.head", return_value=resp) as mock_head:
            is_tiktok_profile_url("https://vt.tiktok.com/ABCD/", proxy="http://127.0.0.1:8080")
        _, kwargs = mock_head.call_args
        assert kwargs.get("proxies", {}).get("http") == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# Dispatcher: first-success wins
# ---------------------------------------------------------------------------


class TestDispatcherFirstSuccess:
    def test_dispatcher_returns_first_success(self):
        """pass1 succeeds immediately; pass2 has a 5s delay -- result arrives < 2s."""
        import time
        from unittest.mock import MagicMock

        from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
        from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher
        from utils.tiktok_detection.health import StrategyHealthRegistry

        fast = MagicMock()
        fast.name = "fast"
        fast.can_run.return_value = True
        fast.check.return_value = LiveCheckResult(
            live_url="https://www.tiktok.com/@u/live",
            room_id="1234567890",
            strategy_name="fast",
        )

        def _slow_check(ctx):
            time.sleep(5)
            return None

        slow = MagicMock()
        slow.name = "slow"
        slow.can_run.return_value = True
        slow.check.side_effect = _slow_check

        registry = StrategyHealthRegistry()
        dispatcher = LiveDetectionDispatcher([fast, slow], registry)

        ctx = LiveCheckContext(username="u")
        t0 = time.monotonic()
        result = dispatcher.check(ctx)
        elapsed = time.monotonic() - t0

        assert result == ("https://www.tiktok.com/@u/live", "1234567890")
        assert elapsed < 2.0, f"Expected result < 2s but took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Health daemon: disables broken strategy
# ---------------------------------------------------------------------------


class TestHealthDaemonDisablesBrokenStrategy:
    def test_health_daemon_disables_broken_strategy(self):
        """After 2 probe failures, registry marks strategy disabled."""
        from utils.tiktok_detection.health import StrategyHealthRegistry

        registry = StrategyHealthRegistry()
        assert registry.is_enabled("pass0_webcast_api") is True

        registry.record_probe_failure("pass0_webcast_api")
        assert registry.is_enabled("pass0_webcast_api") is True  # 1 failure, not yet disabled

        registry.record_probe_failure("pass0_webcast_api")
        assert registry.is_enabled("pass0_webcast_api") is False  # 2 failures -> disabled

    def test_health_registry_reenables_on_success(self):
        """A probe success re-enables a disabled strategy."""
        from utils.tiktok_detection.health import StrategyHealthRegistry

        registry = StrategyHealthRegistry()
        registry.record_probe_failure("pass1_profile_page")
        registry.record_probe_failure("pass1_profile_page")
        assert registry.is_enabled("pass1_profile_page") is False

        registry.record_probe_success("pass1_profile_page")
        assert registry.is_enabled("pass1_profile_page") is True

    def test_dispatcher_skips_disabled_strategy(self):
        """Dispatcher does not call a strategy the registry has disabled."""
        from unittest.mock import MagicMock

        from utils.tiktok_detection.context import LiveCheckContext
        from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher
        from utils.tiktok_detection.health import StrategyHealthRegistry

        broken = MagicMock()
        broken.name = "broken"
        broken.can_run.return_value = True
        broken.check.return_value = None

        registry = StrategyHealthRegistry()
        registry.record_probe_failure("broken")
        registry.record_probe_failure("broken")

        dispatcher = LiveDetectionDispatcher([broken], registry)
        ctx = LiveCheckContext(username="u")
        result = dispatcher.check(ctx)

        assert result is None
        broken.check.assert_not_called()
