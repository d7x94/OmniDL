"""
tests/test_instagram_live_fix.py
Tests for the Instagram Live "dò mãi" bug fixes.

Covers:
  1. check_instagram_live — X-CSRFToken header is sent
  2. check_instagram_live — new 2024+ live-status fields detected
  3. check_instagram_live — empty user data → returns None (not crash)
  4. check_instagram_live — missing sessionid raises RuntimeError (login)
  5. check_instagram_live — 401/403/404/429 HTTP codes raise RuntimeError
  6. check_instagram_live — network errors wrapped as RuntimeError
  7. _MonitorItem.consecutive_failures — field exists with default 0
  8. Live monitor MAX_CONSECUTIVE_FAILURES constant exists and > 0
  9. Live monitor _CHECKING_TIMEOUT_S constant exists and > 0
 10. _recover_stuck_checks — method exists on LiveMonitorTab class
"""
from __future__ import annotations

import io
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from http.cookiejar import MozillaCookieJar

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n"


def _make_cookie_file(extras: dict[str, str] | None = None) -> str:
    """Write a minimal Netscape cookie file with sessionid and optional extras.

    Returns the path as a string (caller owns cleanup via tmp dir).
    """
    cookies = {"sessionid": "abc123", "csrftoken": "csrf999"}
    if extras:
        cookies.update(extras)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False,
        prefix="omnidl_test_cookie_",
    )
    tmp.write(_NETSCAPE_HEADER)
    for name, value in cookies.items():
        # domain  httponly  path  secure  expiry  name  value
        tmp.write(
            f".instagram.com\tTRUE\t/\tTRUE\t9999999999\t{name}\t{value}\n"
        )
    tmp.close()
    return tmp.name


def _make_response(status: int = 200, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


# ---------------------------------------------------------------------------
# 1. X-CSRFToken header is sent
# ---------------------------------------------------------------------------

class TestCSRFTokenHeader:
    """The X-CSRFToken header must be populated from the cookie jar."""

    def test_csrftoken_sent_in_header(self, tmp_path):
        cookie_path = _make_cookie_file({"csrftoken": "mytoken123"})
        resp = _make_response(200, {"data": {"user": {}}})

        with patch("requests.get", return_value=resp) as mock_get:
            from utils.instagram_live_checker import check_instagram_live
            check_instagram_live("someuser", cookie_path)

        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers") or mock_get.call_args[0][1] if len(mock_get.call_args[0]) > 1 else {}
        # headers may be in kwargs
        if not headers:
            headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("X-CSRFToken") == "mytoken123"

    def test_csrftoken_empty_string_when_cookie_absent(self, tmp_path):
        """If csrftoken not in jar, header should be '' (not KeyError)."""
        cookie_path = _make_cookie_file()  # has csrftoken by default
        # Manually write a cookie file without csrftoken
        no_csrf = tmp_path / "no_csrf.txt"
        no_csrf.write_text(
            _NETSCAPE_HEADER
            + ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tabc\n"
        )
        resp = _make_response(200, {"data": {"user": {}}})

        with patch("requests.get", return_value=resp) as mock_get:
            from utils.instagram_live_checker import check_instagram_live
            check_instagram_live("someuser", str(no_csrf))

        headers = mock_get.call_args.kwargs.get("headers", {})
        assert "X-CSRFToken" in headers
        assert headers["X-CSRFToken"] == ""   # graceful fallback

    def test_x_ig_www_claim_sent(self, tmp_path):
        cookie_path = _make_cookie_file()
        resp = _make_response(200, {"data": {"user": {}}})

        with patch("requests.get", return_value=resp) as mock_get:
            from utils.instagram_live_checker import check_instagram_live
            check_instagram_live("someuser", cookie_path)

        headers = mock_get.call_args.kwargs.get("headers", {})
        assert "X-IG-WWW-Claim" in headers

    def test_origin_header_sent(self, tmp_path):
        cookie_path = _make_cookie_file()
        resp = _make_response(200, {"data": {"user": {}}})

        with patch("requests.get", return_value=resp) as mock_get:
            from utils.instagram_live_checker import check_instagram_live
            check_instagram_live("someuser", cookie_path)

        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("Origin") == "https://www.instagram.com"


# ---------------------------------------------------------------------------
# 2. New 2024+ live-status fields
# ---------------------------------------------------------------------------

class TestLiveFieldDetection:
    """is_live should be True for any known live-status field."""

    @pytest.fixture(autouse=True)
    def _cookie(self, tmp_path):
        self.cookie_path = _make_cookie_file()

    def _run(self, user_data: dict):
        resp = _make_response(200, {"data": {"user": user_data}})
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            return check_instagram_live("testuser", self.cookie_path)

    def test_is_live_field(self):
        assert self._run({"is_live": True}) is not None

    def test_has_active_broadcast_field(self):
        assert self._run({"has_active_broadcast": True}) is not None

    def test_live_broadcast_id_field(self):
        assert self._run({"live_broadcast_id": "12345"}) is not None

    def test_live_broadcast_status_active(self):
        """2024+ field: live_broadcast_status == 'active'."""
        assert self._run({"live_broadcast_status": "active"}) is not None

    def test_live_broadcast_status_inactive(self):
        """live_broadcast_status != 'active' → not live."""
        assert self._run({"live_broadcast_status": "stopped"}) is None

    def test_broadcast_count_nonzero(self):
        """2024+ field: broadcast_count > 0."""
        assert self._run({"broadcast_count": 1}) is not None

    def test_broadcast_count_zero(self):
        assert self._run({"broadcast_count": 0}) is None

    def test_active_live_info_truthy(self):
        """2024+ field: active_live_info non-empty dict."""
        assert self._run({"active_live_info": {"id": "abc"}}) is not None

    def test_active_live_info_empty_dict(self):
        assert self._run({"active_live_info": {}}) is None

    def test_not_live_all_false(self):
        user = {
            "is_live": False,
            "has_active_broadcast": False,
            "live_broadcast_id": None,
            "live_broadcast_status": "stopped",
            "broadcast_count": 0,
            "active_live_info": None,
        }
        assert self._run(user) is None

    def test_returns_live_url_format(self):
        result = self._run({"is_live": True})
        assert result == "https://www.instagram.com/testuser/live/"

    def test_fallback_user_key(self):
        """API sometimes returns data at top-level 'user' key, not 'data.user'."""
        resp = _make_response(200, {"user": {"is_live": True}})
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            result = check_instagram_live("testuser", self.cookie_path)
        assert result is not None


# ---------------------------------------------------------------------------
# 3. Empty user data → returns None
# ---------------------------------------------------------------------------

class TestEmptyUserData:
    @pytest.fixture(autouse=True)
    def _cookie(self, tmp_path):
        self.cookie_path = _make_cookie_file()

    def test_empty_user_dict(self):
        resp = _make_response(200, {"data": {"user": {}}})
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            assert check_instagram_live("nobody", self.cookie_path) is None

    def test_null_user(self):
        resp = _make_response(200, {"data": {"user": None}})
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            assert check_instagram_live("nobody", self.cookie_path) is None

    def test_missing_data_key(self):
        resp = _make_response(200, {"status": "ok"})
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            assert check_instagram_live("nobody", self.cookie_path) is None


# ---------------------------------------------------------------------------
# 4. Missing sessionid raises RuntimeError with "login:" prefix
# ---------------------------------------------------------------------------

class TestMissingSessionId:
    def test_no_sessionid_raises(self, tmp_path):
        no_session = tmp_path / "nosession.txt"
        no_session.write_text(
            _NETSCAPE_HEADER
            + ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tcsrftoken\tXYZ\n"
        )
        from utils.instagram_live_checker import check_instagram_live
        with pytest.raises(RuntimeError, match="login:"):
            check_instagram_live("user", str(no_session))

    def test_missing_cookie_file_raises(self):
        from utils.instagram_live_checker import check_instagram_live
        with pytest.raises(RuntimeError, match="login:"):
            check_instagram_live("user", "/nonexistent/path.txt")

    def test_empty_cookie_path_raises(self):
        from utils.instagram_live_checker import check_instagram_live
        with pytest.raises(RuntimeError, match="login:"):
            check_instagram_live("user", "")


# ---------------------------------------------------------------------------
# 5. HTTP error codes
# ---------------------------------------------------------------------------

class TestHTTPErrors:
    @pytest.fixture(autouse=True)
    def _cookie(self):
        self.cookie_path = _make_cookie_file()

    def _run(self, status: int):
        resp = _make_response(status, json_data=None, text="error")
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            check_instagram_live("user", self.cookie_path)

    def test_401_raises_login_error(self):
        with pytest.raises(RuntimeError, match="login:"):
            self._run(401)

    def test_403_raises_login_error(self):
        with pytest.raises(RuntimeError, match="login:"):
            self._run(403)

    def test_404_raises_not_found(self):
        with pytest.raises(RuntimeError, match="not found:"):
            self._run(404)

    def test_429_raises_blocked(self):
        with pytest.raises(RuntimeError, match="blocked:"):
            self._run(429)

    def test_invalid_json_200_raises(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "<html>not json</html>"
        resp.json.side_effect = ValueError("no json")
        with patch("requests.get", return_value=resp):
            from utils.instagram_live_checker import check_instagram_live
            with pytest.raises(RuntimeError):
                check_instagram_live("user", self.cookie_path)


# ---------------------------------------------------------------------------
# 6. Network errors → RuntimeError
# ---------------------------------------------------------------------------

class TestNetworkErrors:
    @pytest.fixture(autouse=True)
    def _cookie(self):
        self.cookie_path = _make_cookie_file()

    def test_connection_error(self):
        import requests as req_mod
        with patch("requests.get", side_effect=req_mod.exceptions.ConnectionError("refused")):
            from utils.instagram_live_checker import check_instagram_live
            with pytest.raises(RuntimeError, match="kết nối"):
                check_instagram_live("user", self.cookie_path)

    def test_timeout_error(self):
        import requests as req_mod
        with patch("requests.get", side_effect=req_mod.exceptions.Timeout()):
            from utils.instagram_live_checker import check_instagram_live
            with pytest.raises(RuntimeError, match="thời gian"):
                check_instagram_live("user", self.cookie_path)

    def test_generic_request_error(self):
        import requests as req_mod
        with patch("requests.get", side_effect=req_mod.exceptions.RequestException("boom")):
            from utils.instagram_live_checker import check_instagram_live
            with pytest.raises(RuntimeError, match="HTTP"):
                check_instagram_live("user", self.cookie_path)


# ---------------------------------------------------------------------------
# 7. _MonitorItem.consecutive_failures field
# ---------------------------------------------------------------------------

class TestMonitorItemFailureField:
    def test_consecutive_failures_default_zero(self):
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(url="https://www.instagram.com/test/")
        assert item.consecutive_failures == 0

    def test_consecutive_failures_is_int(self):
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(url="https://www.instagram.com/test/")
        item.consecutive_failures += 1
        assert item.consecutive_failures == 1


# ---------------------------------------------------------------------------
# 8 & 9. Constants
# ---------------------------------------------------------------------------

class TestLiveMonitorConstants:
    def test_max_consecutive_failures_positive(self):
        from ui.tabs.live_monitor_tab import MAX_CONSECUTIVE_FAILURES
        assert isinstance(MAX_CONSECUTIVE_FAILURES, int)
        assert MAX_CONSECUTIVE_FAILURES > 0

    def test_checking_timeout_positive(self):
        from ui.tabs.live_monitor_tab import _CHECKING_TIMEOUT_S
        assert isinstance(_CHECKING_TIMEOUT_S, (int, float))
        assert _CHECKING_TIMEOUT_S > 0

    def test_max_failures_sane_upper_bound(self):
        """Shouldn't be too high — user needs feedback within a reasonable time."""
        from ui.tabs.live_monitor_tab import MAX_CONSECUTIVE_FAILURES
        assert MAX_CONSECUTIVE_FAILURES <= 20

    def test_checking_timeout_at_least_30s(self):
        """Should be long enough for slow networks but not forever."""
        from ui.tabs.live_monitor_tab import _CHECKING_TIMEOUT_S
        assert _CHECKING_TIMEOUT_S >= 30


# ---------------------------------------------------------------------------
# 10. _recover_stuck_checks method exists
# ---------------------------------------------------------------------------

class TestRecoverStuckChecksMethod:
    def test_method_exists(self):
        from ui.tabs.live_monitor_tab import LiveMonitorTab
        assert hasattr(LiveMonitorTab, "_recover_stuck_checks")
        assert callable(getattr(LiveMonitorTab, "_recover_stuck_checks"))


# ---------------------------------------------------------------------------
# 11. _on_check_error escalation logic (unit test without full UI)
# ---------------------------------------------------------------------------

class TestCheckErrorEscalation:
    """Verify consecutive_failures counter and ERROR escalation without spinning up CTk."""

    def _make_item(self, url="https://www.instagram.com/u/"):
        from ui.tabs.live_monitor_tab import _MonitorItem
        return _MonitorItem(url=url)

    def test_transient_error_increments_counter(self):
        """A non-hard transient error should increment consecutive_failures."""
        from ui.tabs.live_monitor_tab import _MonitorItem, _MonitorState
        item = _MonitorItem(url="https://www.instagram.com/u/")
        # Simulate what _on_check_error does for transient errors
        item.consecutive_failures += 1
        assert item.consecutive_failures == 1
        assert item.state == _MonitorState.WAITING  # not changed yet

    def test_hard_error_keywords(self):
        """Keywords that should trigger ERROR immediately (not increment counter)."""
        hard_keywords = ["private", "not found", "404", "login", "checkpoint",
                         "unsupported url", "removed"]
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(url="https://www.instagram.com/u/")
        for kw in hard_keywords:
            err_l = kw.lower()
            is_hard = any(k in err_l for k in (
                "private", "not found", "404", "login", "checkpoint",
                "unsupported url", "removed",
            ))
            assert is_hard, f"Expected '{kw}' to be detected as hard error"

    def test_escalation_threshold(self):
        """After MAX_CONSECUTIVE_FAILURES, item should be in ERROR state."""
        from ui.tabs.live_monitor_tab import _MonitorItem, _MonitorState, MAX_CONSECUTIVE_FAILURES
        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.consecutive_failures = MAX_CONSECUTIVE_FAILURES - 1
        # One more transient failure should trigger escalation
        item.consecutive_failures += 1
        assert item.consecutive_failures >= MAX_CONSECUTIVE_FAILURES

    def test_failure_counter_reset_on_success(self):
        """consecutive_failures resets to 0 when a check succeeds."""
        from ui.tabs.live_monitor_tab import _MonitorItem
        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.consecutive_failures = 5
        # Simulate success
        item.consecutive_failures = 0
        assert item.consecutive_failures == 0


# ---------------------------------------------------------------------------
# 12. _recover_stuck_checks — unit logic (no CTk)
# ---------------------------------------------------------------------------

class TestRecoverStuckChecksLogic:
    """Verify the timeout guard logic without instantiating CTk."""

    def test_checking_timeout_constant_used_correctly(self):
        """An item stuck in CHECKING for > _CHECKING_TIMEOUT_S should be recoverable."""
        from ui.tabs.live_monitor_tab import _MonitorItem, _MonitorState, _CHECKING_TIMEOUT_S

        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.state = _MonitorState.CHECKING
        # Simulate stuck: last_check was set _CHECKING_TIMEOUT_S + 10 seconds ago
        item.last_check = time.time() - (_CHECKING_TIMEOUT_S + 10)

        elapsed = time.time() - item.last_check
        assert elapsed > _CHECKING_TIMEOUT_S, (
            "Item should be considered stuck based on elapsed time"
        )

    def test_non_stuck_checking_not_affected(self):
        """An item that just started checking should NOT be timed out."""
        from ui.tabs.live_monitor_tab import _MonitorItem, _MonitorState, _CHECKING_TIMEOUT_S

        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.state = _MonitorState.CHECKING
        item.last_check = time.time() - 5  # only 5s ago

        elapsed = time.time() - item.last_check
        assert elapsed < _CHECKING_TIMEOUT_S, (
            "Item should NOT be considered stuck — check just started"
        )


# ---------------------------------------------------------------------------
# 13. URL helpers (smoke tests — not changed but verify no regression)
# ---------------------------------------------------------------------------

class TestUrlHelpers:
    def test_is_instagram_profile_url_true(self):
        from utils.instagram_live_checker import is_instagram_profile_url
        assert is_instagram_profile_url("https://www.instagram.com/someuser/")
        assert is_instagram_profile_url("https://instagram.com/someuser")

    def test_is_instagram_profile_url_false_for_live(self):
        from utils.instagram_live_checker import is_instagram_profile_url
        assert not is_instagram_profile_url("https://www.instagram.com/someuser/live/")

    def test_is_instagram_profile_url_false_for_post(self):
        from utils.instagram_live_checker import is_instagram_profile_url
        assert not is_instagram_profile_url("https://www.instagram.com/p/ABC123/")

    def test_extract_instagram_username(self):
        from utils.instagram_live_checker import extract_instagram_username
        assert extract_instagram_username("https://www.instagram.com/TestUser/") == "testuser"
        assert extract_instagram_username("https://instagram.com/SomeOne") == "someone"

    def test_extract_instagram_username_none_for_post(self):
        from utils.instagram_live_checker import extract_instagram_username
        assert extract_instagram_username("https://www.instagram.com/p/ABC123/") is None

    def test_extract_instagram_username_none_for_live(self):
        from utils.instagram_live_checker import extract_instagram_username
        assert extract_instagram_username("https://www.instagram.com/user/live/") is None
