"""
tests/test_instagram_http_extra.py
Coverage for utils/instagram_http.py branches not exercised elsewhere:
  - get_shared_session(): first-call creation, jar refresh on a reused
    session, and the swallow-on-cookie-jar-error path
  - close_shared_session(): closes an existing session and clears the
    singleton; swallows errors raised by .close()
  - is_ig_cdn_host(): urlparse exception path, empty-hostname path, and
    the exact-host allowlist match
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import utils.instagram_http as ih


def _reset_shared_session():
    with ih._shared_session_lock:
        ih._shared_session = None


class TestGetSharedSession:
    def setup_method(self):
        _reset_shared_session()

    def teardown_method(self):
        _reset_shared_session()

    def test_first_call_creates_session_via_impersonate(self):
        sentinel = MagicMock()
        with patch("utils.instagram_http._get_impersonate_session", return_value=sentinel) as mk:
            session = ih.get_shared_session()

        assert session is sentinel
        mk.assert_called_once_with(None)

    def test_second_call_reuses_session_and_refreshes_jar(self):
        first = MagicMock()
        with patch("utils.instagram_http._get_impersonate_session", return_value=first):
            ih.get_shared_session()

        jar = {"sessionid": "abc"}
        session = ih.get_shared_session(jar=jar)

        assert session is first
        first.cookies.clear.assert_called_once()
        first.cookies.update.assert_called_once_with(jar)

    def test_cookie_jar_refresh_error_is_swallowed(self):
        first = MagicMock()
        first.cookies.clear.side_effect = RuntimeError("boom")
        with patch("utils.instagram_http._get_impersonate_session", return_value=first):
            ih.get_shared_session()

        session = ih.get_shared_session(jar={"a": "b"})
        assert session is first


class TestCloseSharedSession:
    def setup_method(self):
        _reset_shared_session()

    def teardown_method(self):
        _reset_shared_session()

    def test_close_noop_when_no_session(self):
        ih.close_shared_session()
        assert ih._shared_session is None

    def test_close_closes_and_clears_singleton(self):
        sentinel = MagicMock()
        with patch("utils.instagram_http._get_impersonate_session", return_value=sentinel):
            ih.get_shared_session()

        ih.close_shared_session()

        sentinel.close.assert_called_once()
        assert ih._shared_session is None

    def test_close_error_is_swallowed_and_singleton_still_cleared(self):
        sentinel = MagicMock()
        sentinel.close.side_effect = RuntimeError("already closed")
        with patch("utils.instagram_http._get_impersonate_session", return_value=sentinel):
            ih.get_shared_session()

        ih.close_shared_session()

        assert ih._shared_session is None


class TestIsIgCdnHost:
    def test_unparsable_url_returns_false(self):
        assert ih.is_ig_cdn_host(123) is False

    def test_empty_hostname_returns_false(self):
        assert ih.is_ig_cdn_host("not-a-url") is False

    def test_exact_allowlisted_host_returns_true(self):
        assert ih.is_ig_cdn_host("https://live-upload.instagram.com/rupload/") is True
