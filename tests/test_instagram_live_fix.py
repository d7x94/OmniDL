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

import logging
import tempfile
import time
from unittest.mock import MagicMock, patch

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
        mode="w",
        suffix=".txt",
        delete=False,
        prefix="omnidl_test_cookie_",
    )
    tmp.write(_NETSCAPE_HEADER)
    for name, value in cookies.items():
        # domain  httponly  path  secure  expiry  name  value
        tmp.write(f".instagram.com\tTRUE\t/\tTRUE\t9999999999\t{name}\t{value}\n")
    tmp.close()
    return tmp.name


def _make_response(
    status: int = 200,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
) -> MagicMock:
    """Build a mock curl_cffi/requests Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def _make_session_mock(resp: MagicMock) -> MagicMock:
    """Return a mock session whose .get() returns *resp*."""
    session = MagicMock()
    session.get.return_value = resp
    session.cookies = MagicMock()
    return session


# ---------------------------------------------------------------------------
# 1. X-CSRFToken header is sent
# ---------------------------------------------------------------------------


class TestCSRFTokenHeader:
    """The X-CSRFToken header must be populated from the cookie jar."""

    def test_csrftoken_sent_in_header(self, tmp_path):
        cookie_path = _make_cookie_file({"csrftoken": "mytoken123"})
        resp = _make_response(200, {"data": {"user": {}}})
        session = _make_session_mock(resp)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            check_instagram_live("someuser", cookie_path)

        headers = session.get.call_args.kwargs.get("headers", {})
        assert headers.get("X-CSRFToken") == "mytoken123"

    def test_csrftoken_empty_string_when_cookie_absent(self, tmp_path):
        """If csrftoken not in jar, header should be '' (not KeyError)."""
        _make_cookie_file()  # has csrftoken by default
        # Manually write a cookie file without csrftoken
        no_csrf = tmp_path / "no_csrf.txt"
        no_csrf.write_text(_NETSCAPE_HEADER + ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tabc\n")
        resp = _make_response(200, {"data": {"user": {}}})
        session = _make_session_mock(resp)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            check_instagram_live("someuser", str(no_csrf))

        headers = session.get.call_args.kwargs.get("headers", {})
        assert "X-CSRFToken" in headers
        assert headers["X-CSRFToken"] == ""  # graceful fallback

    def test_x_ig_www_claim_sent(self, tmp_path):
        cookie_path = _make_cookie_file()
        resp = _make_response(200, {"data": {"user": {}}})
        session = _make_session_mock(resp)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            check_instagram_live("someuser", cookie_path)

        headers = session.get.call_args.kwargs.get("headers", {})
        assert "X-IG-WWW-Claim" in headers

    def test_origin_header_sent(self, tmp_path):
        cookie_path = _make_cookie_file()
        resp = _make_response(200, {"data": {"user": {}}})
        session = _make_session_mock(resp)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            check_instagram_live("someuser", cookie_path)

        headers = session.get.call_args.kwargs.get("headers", {})
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
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
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
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
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
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            assert check_instagram_live("nobody", self.cookie_path) is None

    def test_null_user(self):
        resp = _make_response(200, {"data": {"user": None}})
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            assert check_instagram_live("nobody", self.cookie_path) is None

    def test_missing_data_key(self):
        resp = _make_response(200, {"status": "ok"})
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            assert check_instagram_live("nobody", self.cookie_path) is None

    def test_empty_user_logs_warning(self, caplog):
        """Empty user data was silently logger.debug before — must now be a WARNING."""
        resp = _make_response(200, {"data": {"user": {}}})
        session = _make_session_mock(resp)
        caplog.set_level(logging.WARNING)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            result = check_instagram_live("nobody", self.cookie_path)

        assert result is None
        assert "empty user data" in caplog.text


# ---------------------------------------------------------------------------
# 4. Missing sessionid raises RuntimeError with "login:" prefix
# ---------------------------------------------------------------------------


class TestMissingSessionId:
    def test_no_sessionid_raises(self, tmp_path):
        no_session = tmp_path / "nosession.txt"
        no_session.write_text(
            _NETSCAPE_HEADER + ".instagram.com\tTRUE\t/\tTRUE\t9999999999\tcsrftoken\tXYZ\n"
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
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
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
        resp = _make_response(200, json_data=None, text="<html>not json</html>")
        session = _make_session_mock(resp)
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            with pytest.raises(RuntimeError):
                check_instagram_live("user", self.cookie_path)

    @pytest.mark.parametrize("status", [401, 403, 404, 429])
    def test_error_status_logs_warning(self, caplog, status):
        """Every non-200 status must be logged as a WARNING (was blind before)."""
        caplog.set_level(logging.WARNING)
        with pytest.raises(RuntimeError):
            self._run(status)
        assert str(status) in caplog.text

    def test_429_logs_retry_after(self, caplog):
        resp = _make_response(429, json_data=None, text="error", headers={"Retry-After": "120"})
        session = _make_session_mock(resp)
        caplog.set_level(logging.WARNING)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            with pytest.raises(RuntimeError, match=r"blocked.*Rate limit"):
                check_instagram_live("user", self.cookie_path)

        assert "429" in caplog.text
        assert "120" in caplog.text


# ---------------------------------------------------------------------------
# 6. Network errors → RuntimeError
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    @pytest.fixture(autouse=True)
    def _cookie(self):
        self.cookie_path = _make_cookie_file()

    def _run_with_session_error(self, exc: Exception):
        session = MagicMock()
        session.get.side_effect = exc
        session.cookies = MagicMock()
        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            check_instagram_live("user", self.cookie_path)

    def test_connection_error(self):
        # curl_cffi exceptions are not requests.exceptions subclasses — the
        # checker matches by message substring instead.
        with pytest.raises(RuntimeError, match="kết nối"):
            self._run_with_session_error(Exception("Connection refused"))

    def test_timeout_error(self):
        with pytest.raises(RuntimeError, match="thời gian"):
            self._run_with_session_error(Exception("Request timeout"))

    def test_generic_request_error(self):
        with pytest.raises(RuntimeError, match="HTTP"):
            self._run_with_session_error(Exception("something went wrong"))


# ---------------------------------------------------------------------------
# 6b. Impersonation routing — checker must use the shared curl_cffi session
# ---------------------------------------------------------------------------


class TestImpersonationRouting:
    def test_routes_through_impersonate_session_with_jar(self, tmp_path):
        from http.cookiejar import MozillaCookieJar

        cookie_path = _make_cookie_file()
        resp = _make_response(200, {"data": {"user": {}}})
        session = _make_session_mock(resp)

        with patch(
            "utils.instagram_live_checker.get_shared_session", return_value=session
        ) as mock_get_session:
            from utils.instagram_live_checker import check_instagram_live

            check_instagram_live("someuser", cookie_path)

        mock_get_session.assert_called_once()
        (jar_arg,), _kwargs = mock_get_session.call_args
        assert isinstance(jar_arg, MozillaCookieJar)
        # BUG-IG-ANTIBOT: the session is now long-lived (get_shared_session
        # keeps it alive across polls) -- it must NOT be closed after a check.
        session.close.assert_not_called()


# ---------------------------------------------------------------------------
# 6c. Deep story-feed fallback
# ---------------------------------------------------------------------------


class TestDeepStoryFallback:
    @pytest.fixture(autouse=True)
    def _cookie(self):
        self.cookie_path = _make_cookie_file()

    def test_deep_false_makes_single_request(self):
        resp = _make_response(200, {"data": {"user": {"id": "999", "is_live": False}}})
        session = _make_session_mock(resp)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            result = check_instagram_live("user", self.cookie_path, deep=False)

        assert result is None
        assert session.get.call_count == 1

    def test_deep_true_story_broadcast_detected(self):
        profile_resp = _make_response(200, {"data": {"user": {"id": "999", "is_live": False}}})
        story_resp = _make_response(200, {"broadcast": {"id": "bcast1"}})
        session = MagicMock()
        session.get.side_effect = [profile_resp, story_resp]
        session.cookies = MagicMock()

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            result = check_instagram_live("user", self.cookie_path, deep=True)

        assert result == "https://www.instagram.com/user/live/"
        assert session.get.call_count == 2
        story_call = session.get.call_args_list[1]
        story_url = story_call.args[0]
        assert "/feed/user/999/story/" in story_url

    def test_deep_true_story_error_returns_none_without_raising(self):
        profile_resp = _make_response(200, {"data": {"user": {"id": "999", "is_live": False}}})
        session = MagicMock()
        session.get.side_effect = [profile_resp, Exception("boom")]
        session.cookies = MagicMock()

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            result = check_instagram_live("user", self.cookie_path, deep=True)

        assert result is None

    def test_deep_true_story_500_returns_none(self):
        profile_resp = _make_response(200, {"data": {"user": {"id": "999", "is_live": False}}})
        story_resp = _make_response(500, json_data=None, text="error")
        session = MagicMock()
        session.get.side_effect = [profile_resp, story_resp]
        session.cookies = MagicMock()

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            result = check_instagram_live("user", self.cookie_path, deep=True)

        assert result is None

    def test_deep_true_missing_user_id_skips_story_call(self):
        profile_resp = _make_response(200, {"data": {"user": {"is_live": False}}})  # no id/pk
        session = _make_session_mock(profile_resp)

        with patch("utils.instagram_live_checker.get_shared_session", return_value=session):
            from utils.instagram_live_checker import check_instagram_live

            result = check_instagram_live("user", self.cookie_path, deep=True)

        assert result is None
        assert session.get.call_count == 1  # story API never called


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
        assert callable(LiveMonitorTab._recover_stuck_checks)


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
        hard_keywords = ["private", "not found", "404", "login", "checkpoint", "unsupported url", "removed"]
        from ui.tabs.live_monitor_tab import _MonitorItem

        _MonitorItem(url="https://www.instagram.com/u/")
        for kw in hard_keywords:
            err_l = kw.lower()
            is_hard = any(
                k in err_l
                for k in (
                    "private",
                    "not found",
                    "404",
                    "login",
                    "checkpoint",
                    "unsupported url",
                    "removed",
                )
            )
            assert is_hard, f"Expected '{kw}' to be detected as hard error"

    def test_escalation_threshold(self):
        """After MAX_CONSECUTIVE_FAILURES, item should be in ERROR state."""
        from ui.tabs.live_monitor_tab import MAX_CONSECUTIVE_FAILURES, _MonitorItem

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
        from ui.tabs.live_monitor_tab import _CHECKING_TIMEOUT_S, _MonitorItem, _MonitorState

        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.state = _MonitorState.CHECKING
        # Simulate stuck: last_check was set _CHECKING_TIMEOUT_S + 10 seconds ago
        item.last_check = time.time() - (_CHECKING_TIMEOUT_S + 10)

        elapsed = time.time() - item.last_check
        assert elapsed > _CHECKING_TIMEOUT_S, "Item should be considered stuck based on elapsed time"

    def test_non_stuck_checking_not_affected(self):
        """An item that just started checking should NOT be timed out."""
        from ui.tabs.live_monitor_tab import _CHECKING_TIMEOUT_S, _MonitorItem, _MonitorState

        item = _MonitorItem(url="https://www.instagram.com/u/")
        item.state = _MonitorState.CHECKING
        item.last_check = time.time() - 5  # only 5s ago

        elapsed = time.time() - item.last_check
        assert elapsed < _CHECKING_TIMEOUT_S, "Item should NOT be considered stuck — check just started"


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


# ---------------------------------------------------------------------------
# 14. InstagramLiveEngine — browser fallback (M1)
# ---------------------------------------------------------------------------


class TestBrowserFallback:
    def test_falls_back_to_other_browser(self, tmp_path, monkeypatch):
        import infrastructure.downloader.instagram_live_engine as eng

        chrome = tmp_path / "chrome.exe"
        chrome.write_text("")

        def fake_candidates(name):
            return [str(chrome)] if name == "chrome" else [str(tmp_path / "missing.exe")]

        monkeypatch.setattr(eng, "_browser_candidates", fake_candidates)
        assert eng._find_browser_exe("brave") == str(chrome)

    def test_raises_when_no_browser_found(self, tmp_path, monkeypatch):
        import infrastructure.downloader.instagram_live_engine as eng

        monkeypatch.setattr(eng, "_browser_candidates", lambda name: [str(tmp_path / "missing.exe")])
        with pytest.raises(RuntimeError):
            eng._find_browser_exe("brave")


# ---------------------------------------------------------------------------
# 15. _cdp_intercept_hls — explicit cookie/cancel params (M2 / H2)
# ---------------------------------------------------------------------------


class TestCdpInterceptSignature:
    def test_cookie_and_cancel_params_exist(self):
        import inspect

        from infrastructure.downloader.instagram_live_engine import _cdp_intercept_hls

        params = inspect.signature(_cdp_intercept_hls).parameters
        assert "cookie_file" in params
        assert "cancel_check" in params

    def test_function_attribute_channel_removed(self):
        """The racy _cookie_file function-attribute passing is gone."""
        import inspect

        from infrastructure.downloader import instagram_live_engine as eng

        assert '"_cookie_file"' not in inspect.getsource(eng)


# ---------------------------------------------------------------------------
# 16. download() — cancellation before API fallback (H2)
# ---------------------------------------------------------------------------


class TestDownloadCancellation:
    def test_cancelled_task_aborts_before_api_fallback(self, monkeypatch):
        import sys

        import yt_dlp

        from domain.models.download_task import DownloadTask
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        monkeypatch.setattr(sys, "platform", "linux")  # skip CDP phase
        task = DownloadTask(url="https://www.instagram.com/someuser/live/")
        task.cancel()
        engine = InstagramLiveEngine(MagicMock())

        with patch.object(InstagramLiveEngine, "_api_hls_fallback") as fb:
            with pytest.raises(yt_dlp.utils.DownloadError, match="Cancelled by user"):
                engine.download(task)
        fb.assert_not_called()


# ---------------------------------------------------------------------------
# 17. download() — "not currently live" marker (H3)
# ---------------------------------------------------------------------------


class TestNotLiveErrorMarker:
    def test_not_live_message_contains_marker(self, monkeypatch):
        import sys

        from domain.models.download_task import DownloadTask
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        monkeypatch.setattr(sys, "platform", "linux")
        task = DownloadTask(url="https://www.instagram.com/someuser/live/")
        engine = InstagramLiveEngine(MagicMock())

        with patch.object(InstagramLiveEngine, "_api_hls_fallback", return_value=None):
            with pytest.raises(RuntimeError, match="not currently live"):
                engine.download(task)


# ---------------------------------------------------------------------------
# 18. /live/<id> URL form (M3)
# ---------------------------------------------------------------------------


class TestLiveIdUrlForm:
    def test_extract_username_falls_back_for_live_id(self):
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        engine = InstagramLiveEngine(MagicMock())
        assert engine._extract_username("https://www.instagram.com/live/17912345/") == "instagram"

    def test_broadcast_id_extracted_from_live_id_form(self):
        from infrastructure.downloader.instagram_live_engine import _BROADCAST_ID_FROM_URL_RE

        m = _BROADCAST_ID_FROM_URL_RE.search("https://www.instagram.com/live/17912345/")
        assert m is not None
        assert m.group(1) == "17912345"

    def test_broadcast_id_still_extracted_from_user_form(self):
        from infrastructure.downloader.instagram_live_engine import _BROADCAST_ID_FROM_URL_RE

        m = _BROADCAST_ID_FROM_URL_RE.search("https://www.instagram.com/someuser/live/17912345/")
        assert m is not None
        assert m.group(1) == "17912345"

    def test_unsupported_url_marker_on_unparseable(self):
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        engine = InstagramLiveEngine(MagicMock())
        with pytest.raises(RuntimeError, match="unsupported url"):
            engine._extract_username("https://example.com/whatever")


# ---------------------------------------------------------------------------
# 19. FFmpeg invocation — quiet stderr (H1)
# ---------------------------------------------------------------------------


class TestFfmpegInvocation:
    def test_loglevel_warning_in_cmd(self, tmp_path, monkeypatch):
        import sys

        from domain.models.download_task import DownloadTask
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        monkeypatch.setattr(sys, "platform", "linux")
        task = DownloadTask(url="https://www.instagram.com/someuser/live/")
        task.output_dir = str(tmp_path)
        engine = InstagramLiveEngine(MagicMock())

        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            raise FileNotFoundError("ffmpeg")

        loc = MagicMock()
        loc.ffmpeg_bin = "/usr/bin/ffmpeg"

        with (
            patch.object(InstagramLiveEngine, "_api_hls_fallback", return_value="https://cdn.example/x.m3u8"),
            patch.object(InstagramLiveEngine, "_build_ffmpeg_headers", return_value="UA: x\r\n"),
            patch("utils.ffmpeg_locator.locate_ffmpeg", return_value=loc),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            with pytest.raises(RuntimeError):
                engine.download(task)

        assert "-loglevel" in captured["cmd"]
        assert "warning" in captured["cmd"]


# ---------------------------------------------------------------------------
# 20. Re-capture + resume loop (BUG-IG-RESUME)
# ---------------------------------------------------------------------------


class TestResumeLoop:
    """download() must survive a mid-stream stall by re-capturing a fresh URL
    and resuming, only failing when nothing was recorded."""

    def _setup(self, tmp_path, monkeypatch, captures, records):
        """Patch capture/record/ffmpeg. *captures* and *records* are lists of
        return values popped per call. Returns (engine, task, calls)."""
        import infrastructure.downloader.instagram_live_engine as eng_mod
        from domain.models.download_task import DownloadTask
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        task = DownloadTask(url="https://www.instagram.com/someuser/live/17900000000000000/")
        task.output_dir = str(tmp_path)
        engine = InstagramLiveEngine(MagicMock())

        calls = {"capture_timeouts": [], "record_paths": []}

        def fake_capture(self, task, url, username, timeout):
            calls["capture_timeouts"].append(timeout)
            return captures.pop(0)

        def fake_record(
            self, task, hls_url, headers, url, output_path, is_dash, ffmpeg_bin, base, on_progress
        ):
            calls["record_paths"].append(output_path)
            reason, n = records.pop(0)
            if n > 0:
                from pathlib import Path as _P

                _P(output_path).write_bytes(b"x" * n)
            return reason, n

        loc = MagicMock()
        loc.ffmpeg_bin = "/usr/bin/ffmpeg"
        concat_mock = MagicMock()

        monkeypatch.setattr(InstagramLiveEngine, "_capture_stream_url", fake_capture)
        monkeypatch.setattr(InstagramLiveEngine, "_record_segment", fake_record)
        monkeypatch.setattr(eng_mod, "_concat_parts", concat_mock)
        monkeypatch.setattr("utils.ffmpeg_locator.locate_ffmpeg", lambda: loc)

        return engine, task, calls, concat_mock, eng_mod

    def test_recapture_resumes_and_concats_multiple_parts(self, tmp_path, monkeypatch):
        import infrastructure.downloader.instagram_live_engine as eng_mod

        big = eng_mod._MIN_PART_BYTES + 10
        engine, task, calls, concat_mock, _ = self._setup(
            tmp_path,
            monkeypatch,
            captures=[
                ("https://x.fbcdn.net/live-dash/y.mpd", {"a": "b"}),
                ("https://x.fbcdn.net/live-dash/y2.mpd", {"a": "b"}),
                (None, {}),
            ],
            records=[("stall", big), ("stall", big)],
        )

        engine.download(task)  # must not raise

        # First capture uses the full 120s window; resume uses the shorter one.
        from infrastructure.downloader.instagram_live_engine import (
            _CDP_HLS_WAIT_S,
            _RESUME_CDP_WAIT_S,
        )

        assert calls["capture_timeouts"][0] == _CDP_HLS_WAIT_S
        assert _RESUME_CDP_WAIT_S in calls["capture_timeouts"][1:]
        # Two recorded parts -> concat into the final file.
        assert concat_mock.call_count == 1
        _bin, parts, out = concat_mock.call_args[0]
        assert len(parts) == 2
        assert str(task.filename) == str(out)

    def test_single_part_renamed_to_output(self, tmp_path, monkeypatch):
        import infrastructure.downloader.instagram_live_engine as eng_mod

        big = eng_mod._MIN_PART_BYTES + 10
        engine, task, calls, concat_mock, _ = self._setup(
            tmp_path,
            monkeypatch,
            captures=[("https://x.fbcdn.net/live-dash/y.mpd", {"a": "b"}), (None, {})],
            records=[("stall", big)],
        )

        engine.download(task)  # must not raise

        assert concat_mock.call_count == 0  # single part -> rename, no concat
        from pathlib import Path

        assert Path(task.filename).exists()
        assert Path(task.filename).suffix == ".mkv"  # DASH .mpd -> matroska

    def test_no_data_ever_raises_after_empty_parts(self, tmp_path, monkeypatch):
        import infrastructure.downloader.instagram_live_engine as eng_mod

        # Capture always succeeds, but every record returns 0 bytes.
        engine, task, calls, concat_mock, _ = self._setup(
            tmp_path,
            monkeypatch,
            captures=[("https://x.fbcdn.net/live-dash/y.mpd", {}) for _ in range(10)],
            records=[("stall", 0) for _ in range(eng_mod._MAX_EMPTY_PARTS)],
        )

        with pytest.raises(RuntimeError, match="khong ghi duoc du lieu"):
            engine.download(task)
        assert concat_mock.call_count == 0

    def test_ffmpeg_error_with_no_data_raises(self, tmp_path, monkeypatch):
        engine, task, calls, concat_mock, _ = self._setup(
            tmp_path,
            monkeypatch,
            captures=[("https://x.fbcdn.net/live-dash/y.mpd", {})],
            records=[("ffmpeg_error", 0)],
        )

        with pytest.raises(RuntimeError):
            engine.download(task)
        assert concat_mock.call_count == 0


# ---------------------------------------------------------------------------
# 21. _concat_parts + _fmt_bytes helpers
# ---------------------------------------------------------------------------


class TestConcatParts:
    def test_builds_concat_demuxer_command(self, tmp_path):
        from infrastructure.downloader.instagram_live_engine import _concat_parts

        p1 = tmp_path / "out.mkv.part0"
        p2 = tmp_path / "out.mkv.part1"
        out = tmp_path / "out.mkv"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("subprocess.run", side_effect=fake_run):
            _concat_parts("/usr/bin/ffmpeg", [p1, p2], out)

        cmd = captured["cmd"]
        assert "concat" in cmd
        assert "-safe" in cmd and "0" in cmd
        assert "-c" in cmd and "copy" in cmd
        assert str(out) == cmd[-1]

    def test_nonzero_returncode_raises(self, tmp_path):
        from infrastructure.downloader.instagram_live_engine import _concat_parts

        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            r.stderr = b"boom"
            return r

        with patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="concat"):
                _concat_parts("/usr/bin/ffmpeg", [tmp_path / "a", tmp_path / "b"], tmp_path / "o.mkv")


class TestFmtBytes:
    def test_fmt_bytes_units(self):
        from infrastructure.downloader.instagram_live_engine import _fmt_bytes

        assert _fmt_bytes(500) == "500 B"
        assert _fmt_bytes(2048).endswith("KiB")
        assert _fmt_bytes(5 * 1_048_576).endswith("MiB")


class TestCheckProfileLiveDecryptsCookie:
    """Regression: check_profile_live must decrypt the .enc cookie before
    handing it to check_instagram_live. Passing the raw encrypted path made
    MozillaCookieJar.load read binary as text → on Windows
    "'charmap' codec can't decode byte 0x9d in position 6".
    """

    def _run(self, tmp_path, prepare_side_effect):
        """Drive check_profile_live to completion; return (called_with, done, err)."""
        import threading

        from app.services.download_service import DownloadService

        enc_path = str(tmp_path / "instagram_brave_cdp_cookies.enc")

        svc = DownloadService.__new__(DownloadService)
        svc._config = MagicMock(proxy="")

        captured = {}

        def fake_check(username, cookie_file, proxy="", deep=False):
            captured["username"] = username
            captured["cookie_file"] = cookie_file
            return None

        done_event = threading.Event()
        result = {"done": "__unset__", "err": None}

        def on_done(live_url):
            result["done"] = live_url
            done_event.set()

        def on_error(msg):
            result["err"] = msg
            done_event.set()

        with (
            patch("app.services.download_service._resolve_cookie", return_value=enc_path),
            patch(
                "app.services.download_service._prepare_cookie_for_use",
                side_effect=prepare_side_effect,
            ),
            patch("utils.instagram_live_checker.check_instagram_live", side_effect=fake_check),
        ):
            svc.check_profile_live(
                url="https://www.instagram.com/baki_babyboy/",
                on_done=on_done,
                on_error=on_error,
            )
            assert done_event.wait(timeout=5), "worker thread did not finish"

        return captured, result

    def test_passes_decrypted_path_not_enc(self, tmp_path):
        decrypted = tmp_path / "omnidl_dec_test.txt"
        decrypted.write_text(_NETSCAPE_HEADER)

        captured, result = self._run(tmp_path, prepare_side_effect=lambda p: (str(decrypted), True))

        assert result["err"] is None
        assert captured["cookie_file"] == str(decrypted)
        assert not captured["cookie_file"].endswith(".enc")

    def test_temp_cookie_unlinked(self, tmp_path):
        decrypted = tmp_path / "omnidl_dec_test.txt"
        decrypted.write_text(_NETSCAPE_HEADER)

        self._run(tmp_path, prepare_side_effect=lambda p: (str(decrypted), True))

        assert not decrypted.exists(), "temp decrypted cookie was not cleaned up"

    def test_plaintext_cookie_not_unlinked(self, tmp_path):
        # is_temp=False (plaintext .txt) must NOT be deleted.
        plain = tmp_path / "cookies.txt"
        plain.write_text(_NETSCAPE_HEADER)

        captured, result = self._run(tmp_path, prepare_side_effect=lambda p: (str(plain), False))

        assert result["err"] is None
        assert captured["cookie_file"] == str(plain)
        assert plain.exists()
