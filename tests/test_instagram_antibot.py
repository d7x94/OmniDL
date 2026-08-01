"""
tests/test_instagram_antibot.py
Tests for the Instagram "We suspect automated behavior" account-flagging fix.

Covers:
  1. _build_ffmpeg_headers — CDN host gets no Cookie/X-IG-App-ID, instagram.com host still does
  2. strip_cdn_headers — drops Cookie, keeps User-Agent/Referer
  3. build_web_headers — full browser header set, echoes record_www_claim
  4. Source-level guards — i.instagram.com / heartbeat_and_get_viewer_count / Android UA gone
  5. _api_get_hls routes through get_shared_session, not bare requests.get
  6. MIN_CHECK_INTERVAL raised to >= 60 in both live-monitor modules
  7. _IG_RL.acquire is called on the download path
  8. gallery-dl cmd for an Instagram URL carries --sleep-request / --user-agent
  9. Anonymous CDN paste: analyse_url -> source_engine="ig_cdn"; stream download
     sends no Cookie header; an expired-signature 404 is a hard error
"""

from __future__ import annotations

import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from domain.models.download_task import DownloadTask, MediaInfo

# ---------------------------------------------------------------------------
# Helpers (copied from tests/test_instagram_live_fix.py)
# ---------------------------------------------------------------------------

_NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n"


def _make_cookie_file(extras: dict[str, str] | None = None) -> str:
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
        tmp.write(f".instagram.com\tTRUE\t/\tTRUE\t9999999999\t{name}\t{value}\n")
    tmp.close()
    return tmp.name


def _make_response(
    status: int = 200, json_data: dict | None = None, headers: dict | None = None
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def _make_session_mock(resp: MagicMock) -> MagicMock:
    session = MagicMock()
    session.get.return_value = resp
    session.cookies = MagicMock()
    return session


_CDN_URL = "https://instagram.fhan5-1.fna.fbcdn.net/o1/v/t2/f2/m86/x.mp4?_nc_cat=1&oh=00_x&oe=6543210A"


# ---------------------------------------------------------------------------
# 1. _build_ffmpeg_headers CDN/host split
# ---------------------------------------------------------------------------


class TestBuildFfmpegHeadersCdnSplit:
    def test_cdn_host_has_no_cookie_or_app_id(self):
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        engine = InstagramLiveEngine(MagicMock(proxy=""))
        headers = engine._build_ffmpeg_headers(_CDN_URL)

        assert "Cookie:" not in headers
        assert "X-IG-App-ID:" not in headers
        assert "User-Agent:" in headers
        assert "Referer:" in headers

    def test_instagram_com_host_still_has_cookie(self):
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        cookie_path = _make_cookie_file()
        engine = InstagramLiveEngine(MagicMock(proxy=""))

        with (
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=cookie_path),
            patch(
                "infrastructure.downloader.yt_dlp_engine._prepare_cookie_for_use",
                return_value=(cookie_path, False),
            ),
        ):
            headers = engine._build_ffmpeg_headers("https://www.instagram.com/someuser/live/")

        assert "Cookie:" in headers
        assert "sessionid=abc123" in headers


# ---------------------------------------------------------------------------
# 2. strip_cdn_headers
# ---------------------------------------------------------------------------


class TestStripCdnHeaders:
    def test_drops_cookie_and_identity_keeps_ua_referer(self):
        from utils.instagram_http import strip_cdn_headers

        headers = {
            "Cookie": "sessionid=abc",
            "X-IG-App-ID": "936619743392459",
            "X-IG-WWW-Claim": "hmac.x",
            "X-CSRFToken": "tok",
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.instagram.com/",
            "Range": "bytes=0-",
        }
        stripped = strip_cdn_headers(headers)

        assert "Cookie" not in stripped
        assert "X-IG-App-ID" not in stripped
        assert "X-IG-WWW-Claim" not in stripped
        assert "X-CSRFToken" not in stripped
        assert stripped["User-Agent"] == "Mozilla/5.0"
        assert stripped["Referer"] == "https://www.instagram.com/"
        assert stripped["Range"] == "bytes=0-"

    def test_is_ig_cdn_host(self):
        from utils.instagram_http import is_ig_cdn_host

        assert is_ig_cdn_host(_CDN_URL)
        assert is_ig_cdn_host("https://scontent.cdninstagram.com/v/x.jpg")
        assert not is_ig_cdn_host("https://www.instagram.com/someuser/live/")


# ---------------------------------------------------------------------------
# 3. build_web_headers
# ---------------------------------------------------------------------------


class TestBuildWebHeaders:
    def test_full_header_set_and_www_claim_echo(self):
        from utils.instagram_http import build_web_headers, record_www_claim

        record_www_claim({"X-IG-Set-WWW-Claim": "hmac.abc123"})
        try:
            headers = build_web_headers(referer="https://www.instagram.com/user/", csrftoken="tok")
            assert headers["X-ASBD-ID"]
            assert headers["Sec-Fetch-Site"] == "same-origin"
            assert headers["Sec-Fetch-Mode"] == "cors"
            assert headers["Sec-Fetch-Dest"] == "empty"
            assert "sec-ch-ua" in headers
            assert "sec-ch-ua-mobile" in headers
            assert headers["X-IG-WWW-Claim"] == "hmac.abc123"
        finally:
            record_www_claim({"X-IG-Set-WWW-Claim": "0"})  # restore default for other tests

    def test_record_www_claim_ignores_missing_header(self):
        from utils.instagram_http import build_web_headers, record_www_claim

        record_www_claim({"X-IG-Set-WWW-Claim": "hmac.keep-me"})
        record_www_claim({})  # no claim header in this response -> no change
        try:
            headers = build_web_headers(referer="https://www.instagram.com/", csrftoken="")
            assert headers["X-IG-WWW-Claim"] == "hmac.keep-me"
        finally:
            record_www_claim({"X-IG-Set-WWW-Claim": "0"})


# ---------------------------------------------------------------------------
# 4. Source-level guards
# ---------------------------------------------------------------------------


class TestSourceLevelGuards:
    def test_checker_has_no_private_host_or_heartbeat(self):
        import inspect

        import utils.instagram_live_checker as mod

        src = inspect.getsource(mod)
        assert "i.instagram.com" not in src
        assert "heartbeat_and_get_viewer_count" not in src

    def test_engine_has_no_private_host_or_heartbeat(self):
        import inspect

        import infrastructure.downloader.instagram_live_engine as mod

        src = inspect.getsource(mod)
        assert "i.instagram.com" not in src
        assert "heartbeat_and_get_viewer_count" not in src

    def test_engine_android_ua_gone(self):
        import inspect

        import infrastructure.downloader.instagram_live_engine as mod

        src = inspect.getsource(mod)
        assert "Instagram 319." not in src


# ---------------------------------------------------------------------------
# 5. _api_get_hls routes through get_shared_session
# ---------------------------------------------------------------------------


class TestApiGetHlsRoutesThroughSharedSession:
    def test_uses_get_shared_session_not_bare_requests(self):
        from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine

        cookie_path = _make_cookie_file()
        resp = _make_response(200, {"broadcast": {"playback_url": "https://cdn.example/x.m3u8"}})
        session = _make_session_mock(resp)

        engine = InstagramLiveEngine(MagicMock(proxy=""))
        with patch("utils.instagram_http.get_shared_session", return_value=session) as mock_get_session:
            hls = engine._api_get_hls(
                "https://www.instagram.com/someuser/live/17912345/",
                "someuser",
                cookie_path,
            )

        assert hls == "https://cdn.example/x.m3u8"
        mock_get_session.assert_called_once()
        session.get.assert_called_once()
        call_url = session.get.call_args.args[0]
        assert call_url.startswith("https://www.instagram.com/api/v1/")


# ---------------------------------------------------------------------------
# 6. MIN_CHECK_INTERVAL raised
# ---------------------------------------------------------------------------


class TestMinCheckIntervalRaised:
    def test_service_min_interval_at_least_60(self):
        from app.services.live_monitor_service import MIN_CHECK_INTERVAL

        assert MIN_CHECK_INTERVAL >= 60

    def test_tab_min_interval_at_least_60(self):
        from ui.tabs.live_monitor_tab import MIN_CHECK_INTERVAL_S

        assert MIN_CHECK_INTERVAL_S >= 60


# ---------------------------------------------------------------------------
# 7. _IG_RL.acquire called on the download path
# ---------------------------------------------------------------------------


def _make_yt_config(tmp_path):
    cfg = MagicMock()
    cfg.extra_args = ""
    cfg.proxy = ""
    cfg.cookie_file = ""
    cfg.use_cookies = False
    cfg.cookies_browser = "chrome"
    cfg.max_retries = 3
    cfg.embed_thumbnail = False
    cfg.embed_metadata = False
    cfg.download_dir = tmp_path
    return cfg


class TestIgRateLimiterOnDownloadPath:
    def test_ig_rl_acquire_called_during_download(self, tmp_path):
        import yt_dlp

        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg = _make_yt_config(tmp_path)
        engine = YtDlpEngine(cfg)

        url = "https://www.instagram.com/reel/ABC123/"
        task = DownloadTask(url=url, format_id="best", output_ext="mp4")
        task.media_info = MediaInfo(url=url, title="Test Reel")
        task.output_dir = str(tmp_path)

        merged_file = tmp_path / "channel - title.mp4"

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def add_post_processor(self, pp, when=None):
                pass

            def download(self, urls):
                merged_file.write_bytes(b"fake merged video content" * 3000)

        with (
            patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL),
            patch.object(mod._IG_RL, "acquire") as acquire_mock,
        ):
            engine.download(task)

        acquire_mock.assert_called()
        assert yt_dlp  # keep import used


# ---------------------------------------------------------------------------
# 8. gallery-dl Instagram pacing
# ---------------------------------------------------------------------------


class TestGalleryDlInstagramPacing:
    def test_base_cmd_adds_sleep_request_and_user_agent(self):
        from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine

        cfg = MagicMock(proxy="")
        engine = GalleryDlEngine(cfg)

        with (
            patch(
                "infrastructure.downloader.gallery_dl_engine._find_executable",
                return_value="gallery-dl",
            ),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=None),
        ):
            cmd, _cookie_temp = engine._base_cmd(url="https://www.instagram.com/p/ABC123/")

        assert "--sleep-request" in cmd
        assert "6.0-12.0" in cmd
        assert "--user-agent" in cmd

    def test_base_cmd_skips_pacing_for_non_instagram_url(self):
        from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine

        cfg = MagicMock(proxy="")
        engine = GalleryDlEngine(cfg)

        with (
            patch(
                "infrastructure.downloader.gallery_dl_engine._find_executable",
                return_value="gallery-dl",
            ),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=None),
        ):
            cmd, _cookie_temp = engine._base_cmd(url="https://twitter.com/user/status/1")

        assert "--sleep-request" not in cmd


# ---------------------------------------------------------------------------
# 9. Anonymous CDN paste (IDM parity)
# ---------------------------------------------------------------------------


class TestAnonymousCdnPaste:
    def test_analyse_url_returns_ig_cdn_source_engine(self):
        from app.services.download_service import DownloadService

        svc = DownloadService.__new__(DownloadService)
        svc._bus = MagicMock()

        done_event = threading.Event()
        result: dict = {}

        def on_done(info):
            result["info"] = info
            done_event.set()

        def on_error(err):
            result["err"] = err
            done_event.set()

        svc.analyse_url(url=_CDN_URL, on_done=on_done, on_error=on_error)
        assert done_event.wait(timeout=5), "worker thread did not finish"

        assert "err" not in result
        assert result["info"].source_engine == "ig_cdn"
        assert result["info"].platform == "Instagram"
        assert result["info"].is_live is False

    def test_stream_download_sends_no_cookie_header(self, tmp_path):
        from infrastructure.downloader.instagram_cdn_engine import download_ig_cdn_url

        captured = {}

        def fake_get(url, headers=None, stream=None, timeout=None):
            captured["headers"] = headers
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            resp.raise_for_status = lambda: None
            resp.iter_content = lambda chunk_size: iter([b"x" * 2048])
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("requests.get", side_effect=fake_get):
            dest = download_ig_cdn_url(url=_CDN_URL, output_dir=tmp_path, filename_hint="clip")

        assert dest.exists()
        header_keys_lower = {k.lower() for k in captured["headers"]}
        assert "cookie" not in header_keys_lower
        assert "user-agent" in header_keys_lower
        assert "referer" in header_keys_lower

    def test_expired_signature_404_is_hard_error(self, tmp_path):
        from infrastructure.downloader.instagram_cdn_engine import download_ig_cdn_url

        def fake_get(url, headers=None, stream=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 404
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("requests.get", side_effect=fake_get):
            with pytest.raises(RuntimeError, match="not found:"):
                download_ig_cdn_url(url=_CDN_URL, output_dir=tmp_path, filename_hint="clip")

    def test_is_ig_cdn_url_matches_report_url(self):
        from infrastructure.downloader.instagram_cdn_engine import is_ig_cdn_url

        assert is_ig_cdn_url(_CDN_URL)
        assert not is_ig_cdn_url("https://www.instagram.com/someuser/live/")
