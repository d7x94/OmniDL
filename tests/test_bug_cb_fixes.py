"""
tests/test_bug_cb_fixes.py
BUG-CB: curl_cffi TLS impersonation + clipboard URL extraction (Kuaishou vấn đề 22).

Covers:
- _CLIPBOARD_URL_RE extracts clean URL from mixed clipboard text
- _paste_clipboard strips trailing punctuation
- impersonate="chrome" present in extract_info opts when curl_cffi available
- impersonate="chrome" present in download opts when curl_cffi available
- no impersonate key when curl_cffi unavailable
- allow_unplayable_formats not regressed (was dropped in a prior edit)
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

# ---------------------------------------------------------------------------
# Helpers (mirrors make_config / make_task from test_yt_dlp_engine.py)
# ---------------------------------------------------------------------------

def _make_config(**kwargs):
    cfg = MagicMock()
    cfg.extra_args        = kwargs.get("extra_args", "")
    cfg.proxy             = kwargs.get("proxy", "")
    cfg.use_cookies       = kwargs.get("use_cookies", False)
    cfg.cookies_browser   = kwargs.get("cookies_browser", "chrome")
    cfg.max_retries       = kwargs.get("max_retries", 3)
    cfg.embed_thumbnail   = kwargs.get("embed_thumbnail", False)
    cfg.embed_metadata    = kwargs.get("embed_metadata", False)
    cfg.download_dir      = kwargs.get("download_dir", Path("/tmp"))  # nosec B108
    cfg.cookie_file       = ""
    cfg.platform_cookies  = {}
    cfg.config_path       = Path("/tmp/config.json")  # nosec B108
    return cfg


def _make_task(url="https://v.kuaishou.com/K9Zu4Iez") -> DownloadTask:
    task = DownloadTask(url=url, format_id="best", output_ext="mp4")
    task.media_info = MediaInfo(url=url, title="Test Kuaishou Video")
    return task


# ---------------------------------------------------------------------------
# Clipboard URL extraction — module-level regex (no Tkinter required)
# ---------------------------------------------------------------------------

# Import regex and junk set directly from the module under test so we don't
# duplicate the definition in the test.
from ui.components.toolbar import _CLIPBOARD_URL_RE, _URL_TRAILING_JUNK  # type: ignore[import]  # noqa: E402


def _extract(text: str) -> str | None:
    """Replicate _paste_clipboard URL extraction logic."""
    m = _CLIPBOARD_URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip("".join(_URL_TRAILING_JUNK))


class TestClipboardUrlExtraction:
    """BUG-CB / Vấn đề 1 — clipboard text with mixed content."""

    def test_pure_url_unchanged(self):
        url = "https://v.kuaishou.com/K9Zu4Iez"
        assert _extract(url) == url

    def test_kuaishou_share_text_extracted(self):
        text = (
            "https://v.kuaishou.com/K9Zu4Iez \u548b\u6ed4 "
            "\u201c\u8981\u505a\u4e00\u4e2a\u6709\u80f8\u808c\u7684\u7537\u4eba "
            "\u201c\u6c14\u8d28\u8fd9\u4e00\u5757\u5e05\u5c31\u5b8c\u4e8b\u4e86"
        )
        assert _extract(text) == "https://v.kuaishou.com/K9Zu4Iez"

    def test_url_at_end_of_sentence_stripped(self):
        text = "check this out: https://example.com/video."
        assert _extract(text) == "https://example.com/video"

    def test_url_followed_by_comma(self):
        text = "watch https://example.com/clip, enjoy"
        assert _extract(text) == "https://example.com/clip"

    def test_url_in_parentheses(self):
        text = "see (https://example.com/v)"
        assert _extract(text) == "https://example.com/v"

    def test_no_url_returns_none(self):
        assert _extract("plain text with no link") is None

    def test_http_url_also_extracted(self):
        text = "old link http://example.com/page here"
        assert _extract(text) == "http://example.com/page"

    def test_first_url_wins_when_multiple(self):
        text = "https://first.com/a and https://second.com/b"
        assert _extract(text) == "https://first.com/a"

    def test_query_params_preserved(self):
        url = "https://v.kuaishou.com/K9Zu4Iez?ref=share&mid=123"
        assert _extract(url) == url

    def test_tiktok_short_link(self):
        url = "https://vt.tiktok.com/ZSjXabcd/"
        result = _extract(url)
        # trailing slash is not in junk set — preserved
        assert result == url


# ---------------------------------------------------------------------------
# curl_cffi opts injection — extract_info path
# ---------------------------------------------------------------------------

class TestCurlCffiExtractInfoOpts:
    """BUG-CB — impersonate must appear in extract_info opts iff curl_cffi available."""

    def _captured_extract_opts(self, curl_available: bool) -> dict:
        import infrastructure.downloader.yt_dlp_engine as mod

        captured: dict = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, url, download=False):
                return {
                    "id": "abc",
                    "title": "Test",
                    "url": url,
                    "ext": "mp4",
                    "duration": 10,
                    "thumbnail": "",
                    "formats": [{"format_id": "best", "ext": "mp4", "url": url}],
                    "is_live": False,
                    "was_live": False,
                }
            def add_default_info_extractors(self): pass

        cfg = _make_config()
        engine = YtDlpEngine(cfg)

        fake_target = MagicMock()
        fake_target.client = "chrome"
        fake_target.__str__ = lambda s: "ImpersonateTarget(client='chrome')"

        # Use a non-Kuaishou URL so extract_info does NOT route to kuaishou_engine
        # (which never constructs YoutubeDL and would leave `captured` empty).
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        with patch.object(mod, "_CURL_CFFI_AVAILABLE", curl_available), \
             patch.object(mod, "_IMPERSONATE_TARGET", fake_target if curl_available else None), \
             patch.object(mod, "_check_unsupported_url", return_value=None), \
             patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            try:
                engine.extract_info(test_url)
            except Exception:
                pass  # we only need captured opts

        return captured

    def test_impersonate_present_when_curl_cffi_available(self):
        opts = self._captured_extract_opts(curl_available=True)
        imp = opts.get("impersonate")
        assert imp is not None, "impersonate must be set when _CURL_CFFI_AVAILABLE=True"
        # Value is an ImpersonateTarget object (not a plain string).
        assert str(imp).lower().startswith("impersonatetarget") or getattr(imp, "client", None) == "chrome", (
            f"impersonate must be ImpersonateTarget(client='chrome'), got {imp!r}"
        )

    def test_impersonate_absent_when_curl_cffi_unavailable(self):
        opts = self._captured_extract_opts(curl_available=False)
        assert "impersonate" not in opts, (
            "impersonate must not be set when _CURL_CFFI_AVAILABLE=False"
        )


# ---------------------------------------------------------------------------
# curl_cffi opts injection — download path
# ---------------------------------------------------------------------------

class TestCurlCffiDownloadOpts:
    """BUG-CB — impersonate must appear in download opts iff curl_cffi available."""

    def _captured_download_opts(self, curl_available: bool) -> dict:
        import infrastructure.downloader.yt_dlp_engine as mod

        captured: dict = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def download(self, urls):
                pass

        cfg = _make_config()
        engine = YtDlpEngine(cfg)
        task = _make_task()

        fake_target = MagicMock()
        fake_target.client = "chrome"
        fake_target.__str__ = lambda s: "ImpersonateTarget(client='chrome')"

        with patch.object(mod, "_CURL_CFFI_AVAILABLE", curl_available), \
             patch.object(mod, "_IMPERSONATE_TARGET", fake_target if curl_available else None), \
             patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.download(task)

        return captured

    def test_impersonate_present_when_curl_cffi_available(self):
        opts = self._captured_download_opts(curl_available=True)
        imp = opts.get("impersonate")
        assert imp is not None, "impersonate must be set when _CURL_CFFI_AVAILABLE=True"
        assert getattr(imp, "client", None) == "chrome", (
            f"impersonate must be ImpersonateTarget(client='chrome'), got {imp!r}"
        )

    def test_impersonate_absent_when_curl_cffi_unavailable(self):
        opts = self._captured_download_opts(curl_available=False)
        assert "impersonate" not in opts

    def test_allow_unplayable_formats_not_regressed(self):
        """allow_unplayable_formats=False must remain in download opts (regression guard)."""
        opts = self._captured_download_opts(curl_available=False)
        assert opts.get("allow_unplayable_formats") is False, (
            "allow_unplayable_formats was accidentally dropped — regression"
        )

    def test_remote_components_not_regressed(self):
        """remote_components must remain a list (BUG-BQ regression guard)."""
        opts = self._captured_download_opts(curl_available=False)
        rc = opts.get("remote_components")
        assert isinstance(rc, list), "remote_components must be a list"
        assert "ejs:github" in rc


# ---------------------------------------------------------------------------
# BUG-CC: TLS hard-stop and friendly error
# ---------------------------------------------------------------------------

class TestBugCcTlsHardStop:
    """BUG-CC: TLS errors must not retry (hard-stop) and must produce a friendly message."""

    def test_ssl_routines_is_hard_stop(self):
        from unittest.mock import MagicMock, patch

        import yt_dlp

        import infrastructure.downloader.yt_dlp_engine as mod

        cfg = _make_config()
        engine = YtDlpEngine(cfg)

        tls_exc = yt_dlp.utils.DownloadError(
            "ERROR: [generic] Unable to download webpage: "
            "curl: (35) TLS connect error: error:100000f7:SSL routines:OPENSSL_internal:WRONG_VERSION_NUMBER"
        )

        sleep_calls = []
        with patch.object(mod.yt_dlp, "YoutubeDL") as FakeYDL, \
             patch("infrastructure.downloader.yt_dlp_engine.time.sleep",
                   side_effect=lambda s: sleep_calls.append(s)):
            instance = MagicMock()
            instance.__enter__ = lambda s: s
            instance.__exit__ = MagicMock(return_value=False)
            instance.extract_info.side_effect = tls_exc
            FakeYDL.return_value = instance

            with pytest.raises(RuntimeError):
                engine.extract_info("https://v.kuaishou.com/nsLRaZq3")

        # hard-stop: no sleep means no retry
        assert sleep_calls == [], "TLS error must not trigger retries"

    def test_tls_connect_error_is_hard_stop(self):
        from unittest.mock import MagicMock, patch

        import yt_dlp

        import infrastructure.downloader.yt_dlp_engine as mod

        cfg = _make_config()
        engine = YtDlpEngine(cfg)

        tls_exc = yt_dlp.utils.DownloadError(
            "Failed to perform, curl: (35) TLS connect error"
        )

        sleep_calls = []
        with patch.object(mod.yt_dlp, "YoutubeDL") as FakeYDL, \
             patch("infrastructure.downloader.yt_dlp_engine.time.sleep",
                   side_effect=lambda s: sleep_calls.append(s)):
            instance = MagicMock()
            instance.__enter__ = lambda s: s
            instance.__exit__ = MagicMock(return_value=False)
            instance.extract_info.side_effect = tls_exc
            FakeYDL.return_value = instance

            with pytest.raises(RuntimeError):
                engine.extract_info("https://v.kuaishou.com/nsLRaZq3")

        assert sleep_calls == [], "TLS error must not trigger retries"

    def test_friendly_error_ssl_routines(self):
        from infrastructure.downloader.yt_dlp_engine import _friendly_error
        msg = ("ERROR: [generic] Unable to download webpage: curl: (35) TLS connect error: "
               "error:100000f7:SSL routines:OPENSSL_internal:WRONG_VERSION_NUMBER")
        result = _friendly_error(msg)
        assert "curl_cffi" in result or "TLS" in result or "curl-cffi" in result

    def test_friendly_error_curl_35(self):
        from infrastructure.downloader.yt_dlp_engine import _friendly_error
        result = _friendly_error("curl: (35) SSL connect error")
        assert "curl_cffi" in result or "TLS" in result or "curl-cffi" in result


# ---------------------------------------------------------------------------
# BUG-CC: Kuaishou short URL pre-resolver
# ---------------------------------------------------------------------------

class TestKuaishhouPreResolver:
    """BUG-CC: _resolve_kuaishou_url and _KUAISHOU_SHORT_RE."""

    def test_regex_matches_v_kuaishou(self):
        from infrastructure.downloader.yt_dlp_engine import _KUAISHOU_SHORT_RE
        assert _KUAISHOU_SHORT_RE.search("https://v.kuaishou.com/nsLRaZq3")

    def test_regex_matches_www_short_video(self):
        from infrastructure.downloader.yt_dlp_engine import _KUAISHOU_SHORT_RE
        assert _KUAISHOU_SHORT_RE.search("https://www.kuaishou.com/short-video/abc123")

    def test_regex_does_not_match_other(self):
        from infrastructure.downloader.yt_dlp_engine import _KUAISHOU_SHORT_RE
        assert not _KUAISHOU_SHORT_RE.search("https://www.youtube.com/watch?v=abc")
        assert not _KUAISHOU_SHORT_RE.search("https://v.tiktok.com/abc")

    def test_resolve_returns_final_url_on_success(self):
        from unittest.mock import MagicMock, patch

        import curl_cffi

        import infrastructure.downloader.yt_dlp_engine as mod

        fake_resp = MagicMock()
        fake_resp.url = "https://www.kuaishou.com/short-video/abc123xyz"

        fake_requests = MagicMock()
        fake_requests.head.return_value = fake_resp
        fake_requests.get.return_value = fake_resp

        with patch.object(mod, "_CURL_CFFI_AVAILABLE", True), \
             patch.object(curl_cffi, "requests", fake_requests):
            result = mod._resolve_kuaishou_url("https://v.kuaishou.com/nsLRaZq3")

        assert result == "https://www.kuaishou.com/short-video/abc123xyz"

    def test_resolve_falls_back_on_exception(self):
        from unittest.mock import MagicMock, patch

        import curl_cffi

        import infrastructure.downloader.yt_dlp_engine as mod

        fake_cffi = MagicMock()
        fake_cffi.head.side_effect = Exception("DNS failure")

        with patch.object(mod, "_CURL_CFFI_AVAILABLE", True), \
             patch.object(curl_cffi, "requests", fake_cffi):
            original = "https://v.kuaishou.com/nsLRaZq3"
            result = mod._resolve_kuaishou_url(original)
            assert result == original

    def test_resolve_skipped_when_curl_cffi_unavailable(self):
        from unittest.mock import patch

        import infrastructure.downloader.yt_dlp_engine as mod

        with patch.object(mod, "_CURL_CFFI_AVAILABLE", False):
            original = "https://v.kuaishou.com/nsLRaZq3"
            result = mod._resolve_kuaishou_url(original)
            assert result == original

    def test_extract_info_calls_resolver_for_kuaishou(self):
        """extract_info must pre-resolve Kuaishou URLs before passing to yt-dlp."""
        from unittest.mock import patch

        import infrastructure.downloader.yt_dlp_engine as mod

        resolved = "https://www.kuaishou.com/short-video/resolved123"
        cfg = _make_config()
        engine = mod.YtDlpEngine(cfg)

        fake_info = {
            "id": "resolved123", "title": "Test", "url": resolved,
            "ext": "mp4", "duration": 30, "thumbnail": "",
            "formats": [{"format_id": "best", "ext": "mp4", "url": resolved}],
            "is_live": False, "was_live": False,
        }
        captured_urls = []

        class FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False):
                captured_urls.append(url)
                return fake_info

        import re as _re

        import infrastructure.downloader.kuaishou_engine as ks_mod
        with patch.object(mod, "_KUAISHOU_SHORT_RE",
                          mod.re.compile(r"v\.kuaishou\.com/", mod.re.I)), \
             patch.object(mod, "_resolve_kuaishou_url", return_value=resolved) as mock_resolve, \
             patch.object(ks_mod, "_KUAISHOU_RE", _re.compile(r"(?!)")), \
             patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.extract_info("https://v.kuaishou.com/nsLRaZq3")

        mock_resolve.assert_called_once_with("https://v.kuaishou.com/nsLRaZq3")
        assert captured_urls == [resolved]
