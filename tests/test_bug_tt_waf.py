"""
tests/test_bug_tt_waf.py
BUG-TT-WAF: TikTok's edge blocks the newest Chrome TLS fingerprint on the video
watch page and answers with a 537-byte "Site Maintenance" page, which yt-dlp
reports as "Unexpected response from webpage request".

Covers:
- the wildcard impersonate target (what impersonate=True produces) no longer
  overrides an explicitly configured target
- a specific extension target still wins over the configured one
- TikTok URLs get a TikTok-safe target, other URLs keep the generic one
- the TikTok target is not the blocked newest-Chrome default
- web-block fallbacks try alternate fingerprints before the app_info attempts
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import infrastructure.downloader.yt_dlp_engine as mod
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

pytestmark = pytest.mark.skipif(
    not mod._CURL_CFFI_AVAILABLE,
    reason="curl_cffi/impersonate not available in this environment",
)


def _make_config():
    cfg = MagicMock()
    cfg.extra_args = ""
    cfg.proxy = ""
    cfg.use_cookies = False
    cfg.cookies_browser = "chrome"
    cfg.max_retries = 3
    cfg.embed_thumbnail = False
    cfg.embed_metadata = False
    cfg.download_dir = Path("/tmp")  # nosec B108
    cfg.cookie_file = ""
    cfg.platform_cookies = {}
    cfg.config_path = Path("/tmp/config.json")  # nosec B108
    return cfg


# ---------------------------------------------------------------------------
# _get_request_target override
# ---------------------------------------------------------------------------


class _StubHandler:
    """Minimal stand-in for an ImpersonateRequestHandler instance."""

    def __init__(self, configured):
        self.impersonate = configured

    def _resolve_target(self, target):
        return target


def _call_get_request_target(configured, extension_target):
    from yt_dlp.networking.impersonate import ImpersonateRequestHandler

    request = SimpleNamespace(extensions={"impersonate": extension_target})
    return ImpersonateRequestHandler._get_request_target(_StubHandler(configured), request)


class TestWildcardImpersonateOverride:
    def test_wildcard_defers_to_configured_target(self):
        from yt_dlp.networking.impersonate import ImpersonateTarget

        configured = ImpersonateTarget.from_str("safari-18.4")
        assert _call_get_request_target(configured, ImpersonateTarget()) == configured

    def test_specific_extension_target_still_wins(self):
        from yt_dlp.networking.impersonate import ImpersonateTarget

        configured = ImpersonateTarget.from_str("safari-18.4")
        requested = ImpersonateTarget.from_str("chrome-110")
        assert _call_get_request_target(configured, requested) == requested

    def test_wildcard_without_configured_target_is_unchanged(self):
        from yt_dlp.networking.impersonate import ImpersonateTarget

        # No configured target — the wildcard must still resolve normally
        # (here: the stub resolver echoes it back).
        assert _call_get_request_target(None, ImpersonateTarget()) == ImpersonateTarget()


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------


class TestTikTokImpersonateTarget:
    def test_tiktok_target_is_supported_by_curl_cffi(self):
        from yt_dlp.networking._curlcffi import CurlCFFIRH

        assert mod._TIKTOK_IMPERSONATE_TARGETS, "no TikTok-safe impersonate target resolved"
        supported = CurlCFFIRH._SUPPORTED_IMPERSONATE_TARGET_MAP
        for target in mod._TIKTOK_IMPERSONATE_TARGETS:
            assert target in supported, f"{target} is not a curl_cffi target"

    def test_tiktok_target_is_not_the_blocked_default(self):
        assert mod._TIKTOK_IMPERSONATE_TARGET != mod._IMPERSONATE_TARGET, (
            "TikTok must not use the newest-Chrome default — that fingerprint is blocked"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://vt.tiktok.com/ZSVjUmPoc/",
            "https://www.tiktok.com/@user/video/7672564746742451474",
            "https://www.tiktok.com/@user/live",
        ],
    )
    def test_tiktok_urls_use_tiktok_target(self, url):
        assert mod._impersonate_target_for(url) == mod._TIKTOK_IMPERSONATE_TARGET

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://v.kuaishou.com/K9Zu4Iez",
            "https://www.instagram.com/p/Cabcdef/",
        ],
    )
    def test_other_urls_keep_generic_target(self, url):
        assert mod._impersonate_target_for(url) == mod._IMPERSONATE_TARGET


# ---------------------------------------------------------------------------
# Fallback ordering
# ---------------------------------------------------------------------------


class TestWebBlockFallbacks:
    def test_fingerprints_come_before_app_info(self):
        labels = [label for label, _ in mod._tiktok_web_block_fallbacks()]
        imp = [i for i, label in enumerate(labels) if label.startswith("impersonate=")]
        app = [i for i, label in enumerate(labels) if label.startswith("app_info=")]
        assert imp and app, f"expected both kinds of fallback, got {labels}"
        assert max(imp) < min(app), f"fingerprint retries must come first: {labels}"

    def test_fingerprint_fallbacks_exclude_the_primary_target(self):
        targets = [
            override["impersonate"]
            for label, override in mod._tiktok_web_block_fallbacks()
            if label.startswith("impersonate=")
        ]
        assert mod._TIKTOK_IMPERSONATE_TARGET not in targets, (
            "retrying the target that just failed wastes a request"
        )

    def test_overrides_do_not_drop_base_opts(self):
        base = {"quiet": True, "impersonate": mod._TIKTOK_IMPERSONATE_TARGET}
        for _label, override in mod._tiktok_web_block_fallbacks():
            merged = {**base, **override}
            assert merged["quiet"] is True


# ---------------------------------------------------------------------------
# opts injection — TikTok URL must carry the TikTok target
# ---------------------------------------------------------------------------


class TestOptsUseTikTokTarget:
    def _captured_extract_opts(self, url: str) -> dict:
        captured: dict = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def add_post_processor(self, pp, when=None):
                pass

            def add_default_info_extractors(self):
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

        engine = YtDlpEngine(_make_config())
        with (
            patch.object(mod, "_check_unsupported_url", return_value=None),
            patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL),
        ):
            try:
                engine.extract_info(url)
            except Exception:
                pass  # only the captured opts matter
        return captured

    def test_tiktok_extract_opts(self):
        opts = self._captured_extract_opts("https://www.tiktok.com/@user/video/7672564746742451474")
        assert opts.get("impersonate") == mod._TIKTOK_IMPERSONATE_TARGET

    def test_youtube_extract_opts(self):
        opts = self._captured_extract_opts("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert opts.get("impersonate") == mod._IMPERSONATE_TARGET

    def _captured_download_opts(self, url: str) -> dict:
        from domain.models.download_task import DownloadTask, MediaInfo

        captured: dict = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def add_post_processor(self, pp, when=None):
                pass

            def download(self, urls):
                pass

        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test")
        engine = YtDlpEngine(_make_config())
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            try:
                engine.download(task)
            except Exception:
                pass  # only the captured opts matter
        return captured

    def test_tiktok_download_opts(self):
        opts = self._captured_download_opts("https://www.tiktok.com/@user/video/7672564746742451474")
        assert opts.get("impersonate") == mod._TIKTOK_IMPERSONATE_TARGET

    def test_kuaishou_download_opts(self):
        opts = self._captured_download_opts("https://v.kuaishou.com/K9Zu4Iez")
        assert opts.get("impersonate") == mod._IMPERSONATE_TARGET
