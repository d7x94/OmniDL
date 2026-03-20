"""
tests/test_cookie_extractor.py
Unit tests for infrastructure/downloader/cookie_extractor.py

All tests are pure unit tests — no real browser access, no network calls.
yt_dlp.YoutubeDL is fully mocked so the test suite remains headless.
"""
from __future__ import annotations

import http.cookiejar
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.downloader.cookie_extractor import (
    _PLATFORM_DOMAINS,
    _friendly_extract_error,
    extract_browser_cookies,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cookie(domain: str, name: str = "session", value: str = "abc") -> http.cookiejar.Cookie:
    """Build a minimal http.cookiejar.Cookie for testing."""
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=9999999999,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )


def _write_mock_netscape(path: Path, cookies: list[http.cookiejar.Cookie]) -> None:
    """Write a minimal Netscape cookie file for tests."""
    jar = http.cookiejar.MozillaCookieJar(str(path))
    for c in cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True, ignore_expires=True)


# ---------------------------------------------------------------------------
# Tests — _PLATFORM_DOMAINS
# ---------------------------------------------------------------------------

class TestPlatformDomains:
    def test_expected_keys_present(self):
        assert set(_PLATFORM_DOMAINS) >= {
            "youtube", "tiktok", "instagram", "facebook", "twitter", "threads"
        }

    def test_youtube_included_with_google_auth(self):
        """YouTube needs google.com for age-verified account cookies."""
        assert "youtube" in _PLATFORM_DOMAINS
        assert "youtube.com" in _PLATFORM_DOMAINS["youtube"]
        assert "google.com" in _PLATFORM_DOMAINS["youtube"], (
            "google.com required for YouTube age-restriction auth cookies"
        )

    def test_twitch_vimeo_not_included(self):
        """Non-auth platforms should not have per-platform cookie entries."""
        all_keys = set(_PLATFORM_DOMAINS.keys())
        assert "twitch" not in all_keys
        assert "vimeo" not in all_keys

    def test_threads_includes_instagram(self):
        # Threads auth is Instagram-backed — instagram.com must be in threads domains
        assert "instagram.com" in _PLATFORM_DOMAINS["threads"]

    def test_no_duplicates_within_platform(self):
        for key, domains in _PLATFORM_DOMAINS.items():
            assert len(domains) == len(set(domains)), f"Duplicate domains in {key}"


# ---------------------------------------------------------------------------
# Tests — _friendly_extract_error
# ---------------------------------------------------------------------------

class TestFriendlyExtractError:
    def test_dpapi_error_brave_chrome_127_limitation(self):
        """DPAPI/App-Bound Encryption → explains Firefox workaround clearly."""
        msg = _friendly_extract_error(
            "ERROR: Failed to decrypt with DPAPI. "
            "See https://github.com/yt-dlp/yt-dlp/issues/10927 for more info"
        )
        # Must explain App-Bound limitation AND suggest Firefox as solution
        assert "firefox" in msg.lower()
        assert "brave" in msg.lower() or "chrome" in msg.lower() or "app-bound" in msg.lower()

    def test_dpapi_error_short_message(self):
        """Short DPAPI error message also caught correctly."""
        msg = _friendly_extract_error("ERROR: Failed to decrypt with DPAPI.")
        assert "firefox" in msg.lower()

    def test_could_not_copy_database_brave_open(self):
        """yt-dlp 'Could not copy Chrome cookie database' = browser is open/locked."""
        msg = _friendly_extract_error(
            "ERROR: Could not copy Chrome cookie database. "
            "See https://github.com/yt-dlp/yt-dlp/issues/7271 for more info"
        )
        # Must tell user to close the browser, not to switch browsers
        assert "đóng" in msg.lower() or "close" in msg.lower() or "brave" in msg.lower()
        assert "🔄" in msg or "lại" in msg.lower()

    def test_locked_database(self):
        msg = _friendly_extract_error("database is locked: unable to open cookie db")
        assert "đóng" in msg.lower() or "locked" in msg.lower() or "khóa" in msg.lower()

    def test_decrypt_error(self):
        msg = _friendly_extract_error("failed to decrypt cookie: DPAPI error")
        assert len(msg) > 0

    def test_not_found_database_with_browser_name(self):
        """yt-dlp 'could not find X cookies database' → clear dropdown hint."""
        msg = _friendly_extract_error(
            'ERROR: could not find chrome cookies database in '
            '"C:\\Users\\PC\\AppData\\Local\\Google\\Chrome\\User Data"'
        )
        # Must tell the user to check the dropdown, not just "profile not found"
        assert "dropdown" in msg.lower() or "cookie source" in msg.lower() or "chrome" in msg.lower()
        assert "🔄" in msg or "lại" in msg.lower()

    def test_not_found_database_without_browser_name(self):
        """Generic 'could not find cookies database' still gives dropdown hint."""
        msg = _friendly_extract_error("could not find cookies database in /tmp/x")
        assert "dropdown" in msg.lower() or "cookie source" in msg.lower()

    def test_not_found(self):
        msg = _friendly_extract_error("Could not find browser profile")
        assert "dropdown" in msg.lower() or "profile" in msg.lower() or len(msg) > 0

    def test_permission_denied(self):
        msg = _friendly_extract_error("Permission denied: /home/user/.config/chrome")
        assert len(msg) > 0

    def test_generic_fallback_capped_at_150(self):
        long_msg = "x" * 300
        result = _friendly_extract_error(long_msg)
        assert len(result) <= 150


# ---------------------------------------------------------------------------
# Tests — extract_browser_cookies (mocked yt-dlp + MozillaCookieJar)
# ---------------------------------------------------------------------------

class TestExtractBrowserCookies:

    def _make_ydl_mock(self, tmp_path: Path, cookies: list[http.cookiejar.Cookie]):
        """Return a context manager mock that writes cookie file on __exit__."""
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)

        def fake_exit(*args):
            _write_mock_netscape(tmp_path, cookies)

        mock_ydl.__exit__ = MagicMock(side_effect=fake_exit)
        return mock_ydl

    # ── Global (unfiltered) path ──────────────────────────────────────────

    def test_global_success(self, tmp_path):
        output = tmp_path / "global_cookies.txt"
        ig_cookie = _make_cookie(".instagram.com", "sessionid", "ig123")
        tt_cookie = _make_cookie(".tiktok.com", "sessionid", "tt456")

        ydl_mock = self._make_ydl_mock(
            tmp_path / "_tmp_global_cookies.txt",
            [ig_cookie, tt_cookie],
        )

        with patch("yt_dlp.YoutubeDL", return_value=ydl_mock):
            count, error = extract_browser_cookies("chrome", output, platform_key=None)

        assert error is None
        assert count == 2
        assert output.exists()

    def test_global_empty_jar_returns_error(self, tmp_path):
        output = tmp_path / "global_cookies.txt"
        ydl_mock = self._make_ydl_mock(tmp_path / "_tmp_global_cookies.txt", [])

        with patch("yt_dlp.YoutubeDL", return_value=ydl_mock):
            count, error = extract_browser_cookies("chrome", output, platform_key=None)

        assert count == 0
        assert error is not None

    def test_global_tmp_file_missing_returns_error(self, tmp_path):
        """If yt-dlp does not write the tmp file, return an error."""
        output = tmp_path / "global_cookies.txt"
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)  # writes nothing

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            count, error = extract_browser_cookies("chrome", output, platform_key=None)

        assert count == 0
        assert error is not None

    # ── Platform-filtered path ────────────────────────────────────────────

    def test_platform_filter_keeps_matching_cookies(self, tmp_path):
        output = tmp_path / "instagram_cookies.txt"
        ig_cookie = _make_cookie(".instagram.com", "sessionid", "ig123")
        tt_cookie = _make_cookie(".tiktok.com", "sessionid", "tt456")

        ydl_mock = self._make_ydl_mock(
            tmp_path / "_tmp_instagram_cookies.txt",
            [ig_cookie, tt_cookie],
        )

        with patch("yt_dlp.YoutubeDL", return_value=ydl_mock):
            count, error = extract_browser_cookies("chrome", output, platform_key="instagram")

        assert error is None
        assert count == 1  # only instagram.com cookie kept

        # Verify the saved file only contains instagram cookies
        saved_jar = http.cookiejar.MozillaCookieJar()
        saved_jar.load(str(output), ignore_discard=True, ignore_expires=True)
        saved_domains = {c.domain for c in saved_jar}
        assert not any("tiktok" in d for d in saved_domains)

    def test_platform_filter_no_match_returns_error(self, tmp_path):
        output = tmp_path / "instagram_cookies.txt"
        tt_cookie = _make_cookie(".tiktok.com", "sessionid", "tt456")

        ydl_mock = self._make_ydl_mock(
            tmp_path / "_tmp_instagram_cookies.txt",
            [tt_cookie],
        )

        with patch("yt_dlp.YoutubeDL", return_value=ydl_mock):
            count, error = extract_browser_cookies("chrome", output, platform_key="instagram")

        assert count == 0
        assert error is not None

    def test_threads_captures_instagram_cookies(self, tmp_path):
        """threads platform domain list includes instagram.com (Threads auth)."""
        output = tmp_path / "threads_cookies.txt"
        ig_cookie = _make_cookie(".instagram.com", "sessionid", "ig123")
        threads_cookie = _make_cookie(".threads.net", "csrftoken", "csrf456")

        ydl_mock = self._make_ydl_mock(
            tmp_path / "_tmp_threads_cookies.txt",
            [ig_cookie, threads_cookie],
        )

        with patch("yt_dlp.YoutubeDL", return_value=ydl_mock):
            count, error = extract_browser_cookies("chrome", output, platform_key="threads")

        assert error is None
        assert count == 2  # both instagram.com and threads.net kept

    def test_tmp_file_cleaned_on_ydl_exception(self, tmp_path):
        """tmp file must be deleted if yt-dlp raises an exception."""
        output = tmp_path / "global_cookies.txt"

        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(side_effect=RuntimeError("DPAPI error"))
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            count, error = extract_browser_cookies("chrome", output, platform_key=None)

        assert count == 0
        assert error is not None
        # No leftover tmp files
        tmp_files = list(tmp_path.glob("_tmp_*"))
        assert tmp_files == []

    def test_output_dir_created_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "subdir" / "cookies.txt"
        ig_cookie = _make_cookie(".instagram.com", "sessionid", "ig123")

        ydl_mock = self._make_ydl_mock(
            nested.parent / f"_tmp_{nested.name}",
            [ig_cookie],
        )

        with patch("yt_dlp.YoutubeDL", return_value=ydl_mock):
            count, error = extract_browser_cookies("chrome", nested, platform_key=None)

        assert nested.parent.exists()


# ---------------------------------------------------------------------------
# Tests — CDP extraction helpers
# ---------------------------------------------------------------------------

class TestCdpHelpers:

    def test_cdp_cookies_to_netscape_basic(self):
        from infrastructure.downloader.cookie_extractor import _cdp_cookies_to_netscape
        cookies = [
            {"domain": ".tiktok.com", "path": "/", "secure": True,
             "expires": 9999999999, "name": "sessionid", "value": "abc123"},
            {"domain": "instagram.com", "path": "/", "secure": False,
             "expires": 0, "name": "csrftoken", "value": "xyz"},
        ]
        output = _cdp_cookies_to_netscape(cookies)
        assert "# Netscape HTTP Cookie File" in output
        assert ".tiktok.com\tTRUE\t/\tTRUE\t9999999999\tsessionid\tabc123" in output
        assert "instagram.com\tFALSE\t/\tFALSE\t0\tcsrftoken\txyz" in output

    def test_cdp_cookies_to_netscape_negative_expiry_clamped(self):
        """Session cookies with expires=-1 must be stored as 0."""
        from infrastructure.downloader.cookie_extractor import _cdp_cookies_to_netscape
        cookies = [
            {"domain": ".tiktok.com", "path": "/", "secure": False,
             "expires": -1, "name": "sid", "value": "v"},
        ]
        output = _cdp_cookies_to_netscape(cookies)
        assert "\t0\tsid\tv" in output

    def test_find_browser_exe_returns_none_for_unknown(self, tmp_path):
        """Non-existent browser → None (not an exception)."""
        from infrastructure.downloader.cookie_extractor import _find_browser_exe
        result = _find_browser_exe("nonexistent_browser_xyz")
        assert result is None

    def test_friendly_cdp_error_connection_refused(self):
        from infrastructure.downloader.cookie_extractor import _friendly_cdp_error
        msg = _friendly_cdp_error("Connection refused to localhost:9223")
        assert len(msg) > 0

    def test_friendly_cdp_error_fallback_capped(self):
        from infrastructure.downloader.cookie_extractor import _friendly_cdp_error
        msg = _friendly_cdp_error("x" * 300)
        assert len(msg) <= 150


class TestExtractViaCdp:

    def _mock_cdp(self, cookies: list, tmp_path: Path):
        """Return a context manager that patches all CDP internals."""
        from unittest.mock import MagicMock, patch
        import json, subprocess

        patches = [
            patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                  return_value=tmp_path / "fake_brave.exe"),
            patch("infrastructure.downloader.cookie_extractor._cdp_wait_ready", return_value=True),
            patch("infrastructure.downloader.cookie_extractor._cdp_get_page_ws_url",
                  return_value=f"ws://localhost:9223/devtools/browser/fake"),
            patch("infrastructure.downloader.cookie_extractor._cdp_ws_connect",
                  return_value=MagicMock()),
            patch("infrastructure.downloader.cookie_extractor._cdp_get_all_cookies",
                  return_value=cookies),
            patch("subprocess.Popen", return_value=MagicMock()),
        ]
        return patches

    def test_cdp_global_success(self, tmp_path):
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch, MagicMock

        cookies = [
            {"domain": ".tiktok.com", "path": "/", "secure": True,
             "expires": 9999999999, "name": "sessionid", "value": "abc"},
        ]
        output = tmp_path / "cdp_cookies.txt"

        real_profile = tmp_path / "User Data"
        real_profile.mkdir()
        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=tmp_path / "brave.exe"), \
             patch("infrastructure.downloader.cookie_extractor._find_browser_profile",
                   return_value=real_profile), \
             patch("infrastructure.downloader.cookie_extractor._is_browser_running",
                   return_value=False), \
             patch("infrastructure.downloader.cookie_extractor._cdp_wait_ready", return_value=True), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_page_ws_url",
                   return_value="ws://localhost:9223/devtools/browser/x"), \
             patch("infrastructure.downloader.cookie_extractor._cdp_ws_connect",
                   return_value=MagicMock()), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_all_cookies",
                   return_value=cookies), \
             patch("subprocess.Popen", return_value=MagicMock()):
            count, error = extract_via_cdp(output, platform_key=None, browser="brave")

        assert error is None
        assert count == 1
        assert output.exists()
        assert "tiktok.com" in output.read_text(encoding="utf-8")

    def test_cdp_platform_filter(self, tmp_path):
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch, MagicMock

        cookies = [
            {"domain": ".tiktok.com", "path": "/", "secure": True,
             "expires": 9999999999, "name": "sessionid", "value": "tt"},
            {"domain": ".instagram.com", "path": "/", "secure": True,
             "expires": 9999999999, "name": "sessionid", "value": "ig"},
        ]
        output = tmp_path / "tiktok_cdp.txt"

        real_profile = tmp_path / "User Data"
        real_profile.mkdir()
        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=tmp_path / "brave.exe"), \
             patch("infrastructure.downloader.cookie_extractor._find_browser_profile",
                   return_value=real_profile), \
             patch("infrastructure.downloader.cookie_extractor._is_browser_running",
                   return_value=False), \
             patch("infrastructure.downloader.cookie_extractor._cdp_wait_ready", return_value=True), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_page_ws_url",
                   return_value="ws://localhost:9223/devtools/browser/x"), \
             patch("infrastructure.downloader.cookie_extractor._cdp_ws_connect",
                   return_value=MagicMock()), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_all_cookies",
                   return_value=cookies), \
             patch("subprocess.Popen", return_value=MagicMock()):
            count, error = extract_via_cdp(output, platform_key="tiktok", browser="brave")

        assert error is None
        assert count == 1
        content = output.read_text(encoding="utf-8")
        assert "tiktok.com" in content
        assert "instagram.com" not in content

    def test_cdp_browser_not_found(self, tmp_path):
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch
        output = tmp_path / "out.txt"

        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=None):
            count, error = extract_via_cdp(output, browser="brave")

        assert count == 0
        assert error is not None

    def test_cdp_timeout_returns_error(self, tmp_path):
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch, MagicMock
        output = tmp_path / "out.txt"

        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=tmp_path / "brave.exe"), \
             patch("infrastructure.downloader.cookie_extractor._cdp_wait_ready",
                   return_value=False), \
             patch("subprocess.Popen", return_value=MagicMock()):
            count, error = extract_via_cdp(output, browser="brave")

        assert count == 0
        assert error is not None

    def test_cdp_empty_cookies_returns_error(self, tmp_path):
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch, MagicMock
        output = tmp_path / "out.txt"

        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=tmp_path / "brave.exe"), \
             patch("infrastructure.downloader.cookie_extractor._cdp_wait_ready", return_value=True), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_page_ws_url",
                   return_value="ws://localhost:9223/devtools/browser/x"), \
             patch("infrastructure.downloader.cookie_extractor._cdp_ws_connect",
                   return_value=MagicMock()), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_all_cookies",
                   return_value=[]), \
             patch("subprocess.Popen", return_value=MagicMock()):
            count, error = extract_via_cdp(output, browser="brave")

        assert count == 0
        assert error is not None

    def test_cdp_user_data_dir_is_real_profile(self, tmp_path):
        """When browser is NOT running, --user-data-dir must point to real profile, not a temp dir."""
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch, MagicMock

        launched_cmds = []
        real_profile = tmp_path / "BraveSoftware" / "User Data"
        real_profile.mkdir(parents=True)

        def fake_popen(cmd, **kwargs):
            launched_cmds.append(cmd)
            return MagicMock()

        cookies = [{"domain": ".tiktok.com", "path": "/", "secure": True,
                    "expires": 9999999999, "name": "s", "value": "v"}]

        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=tmp_path / "brave.exe"), \
             patch("infrastructure.downloader.cookie_extractor._find_browser_profile",
                   return_value=real_profile), \
             patch("infrastructure.downloader.cookie_extractor._is_browser_running",
                   return_value=False), \
             patch("infrastructure.downloader.cookie_extractor._cdp_wait_ready", return_value=True), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_page_ws_url",
                   return_value="ws://localhost:9223/devtools/browser/x"), \
             patch("infrastructure.downloader.cookie_extractor._cdp_ws_connect",
                   return_value=MagicMock()), \
             patch("infrastructure.downloader.cookie_extractor._cdp_get_all_cookies",
                   return_value=cookies), \
             patch("subprocess.Popen", side_effect=fake_popen):
            count, error = extract_via_cdp(tmp_path / "out.txt", browser="brave")

        assert error is None
        assert launched_cmds, "Popen was not called"
        cmd_str = " ".join(str(a) for a in launched_cmds[0])
        # Must use the real profile dir, NOT a temp/OmniDL-CDP-tmp dir
        assert "OmniDL-CDP-tmp" not in cmd_str
        assert str(real_profile) in cmd_str

    def test_cdp_returns_error_when_browser_running(self, tmp_path):
        """When browser is running, CDP must return a clear close-browser message."""
        from infrastructure.downloader.cookie_extractor import extract_via_cdp
        from unittest.mock import patch

        with patch("infrastructure.downloader.cookie_extractor._find_browser_exe",
                   return_value=tmp_path / "brave.exe"), \
             patch("infrastructure.downloader.cookie_extractor._is_browser_running",
                   return_value=True):
            count, error = extract_via_cdp(tmp_path / "out.txt", browser="brave")

        assert count == 0
        assert error is not None
        # Message must tell user to close the browser
        assert "đóng" in error.lower() or "close" in error.lower()
