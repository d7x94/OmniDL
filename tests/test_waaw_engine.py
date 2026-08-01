"""Tests for infrastructure/downloader/waaw_engine.py URL matching and HLS fallback."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from domain.models.download_task import DownloadTask
from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.waaw_engine import (
    _AD_BLOCK_URLS,
    _JS_MATCH,
    WaawEngine,
    _handle_request_paused,
    _hls_download,
    _is_waaw_cdn_url,
    _needs_captcha,
    _parse_get_md5_manifest,
    _un,
    is_waaw_cdn_link,
    is_waaw_url,
)


class TestJsMatchDecoyExclusion:
    def test_js_match_rejects_decoy(self):
        assert "no_video" in _JS_MATCH


class TestAdBlockUrls:
    def test_non_empty(self):
        assert _AD_BLOCK_URLS

    def test_contains_overlay_click_stealer_hosts(self):
        blob = " ".join(_AD_BLOCK_URLS)
        assert "counter.yadro.ru" in blob
        assert "/ad/banner/" in blob

    def test_does_not_block_vast_waterfall_hosts(self):
        # BUG-WAAW-02: these exchanges feed get_md5.php's `adscore` field.
        # Blocking them left adscore empty and the player stuck on
        # need_captcha forever, so they must stay unblocked.
        blob = " ".join(_AD_BLOCK_URLS)
        assert "twinrdsyte.com" not in blob
        assert "megawebify.my" not in blob
        assert "videosprofitnetwork.com" not in blob
        assert "magsrv.com" not in blob
        assert "vstserv.com" not in blob
        assert "yomeno.xyz" not in blob
        assert "bigboxads.com" not in blob
        assert "videocdnmetrika.com" not in blob
        assert "netu.php" not in blob


class TestIsWaawUrl:
    def test_accepts_video_page(self):
        assert is_waaw_url("https://waaw.ac/f/ofYvCQ2DQBDk")

    def test_rejects_other_site(self):
        assert not is_waaw_url("https://example.com/f/abc")


class TestIsWaawCdnUrl:
    def test_accepts_serv1_md5(self):
        assert _is_waaw_cdn_url("https://cdn.example.com/vid.mp4?serv=1&md5=abc&time=1")

    def test_accepts_serv4_md5(self):
        assert _is_waaw_cdn_url("https://cdn.example.com/vid.mp4?serv=4&md5=abc&time=1")

    def test_accepts_hls_path_token(self):
        url = (
            "https://4fw4gd.cfglobalcdn.com/secip/1/861rQM940fF8R1fZDCdglg/"
            "OTQuMjUuMTcwLjI2/1606597200/hls-vod-s03/flv/api/files/videos/"
            "2018/08/01/153311550983uua.mp4.m3u8"
        )
        assert _is_waaw_cdn_url(url)

    def test_accepts_new_manifest_format_without_old_markers(self):
        # waaw.ac's CDN URL format changes over time and may drop the old
        # hls-vod/secip markers; any .m3u8 is accepted (downstream
        # ffmpeg/MP4-magic validation rejects false positives).
        assert _is_waaw_cdn_url("https://edge9.waawcdn.net/stream/9f3a2b/index.m3u8?token=xyz")

    def test_rejects_page_url(self):
        assert not _is_waaw_cdn_url("https://waaw.ac/f/ofYvCQ2DQBDk")

    def test_rejects_js_asset(self):
        assert not _is_waaw_cdn_url("https://waaw.ac/js/hls.tune.144.js?98")

    def test_rejects_css_asset(self):
        assert not _is_waaw_cdn_url("https://waaw.ac/css/player.css")

    def test_rejects_ad_without_m3u8(self):
        assert not _is_waaw_cdn_url("https://ads.example.com/preroll/click?id=1")

    def test_rejects_no_video_decoy(self):
        assert not _is_waaw_cdn_url("https://127.0.0.1/no_video.mp4.m3u8")

    def test_rejects_localhost_m3u8(self):
        assert not _is_waaw_cdn_url("https://localhost/x/index.m3u8")

    def test_accepts_real_centipede_manifest(self):
        url = (
            "https://f9rw3r.cfglobalcdn.com/silverlight/secip/213969/0/"
            "WQPVj8WRlOiJOC3iC4RR8w/MTYwLjE4Ny4xNDguMTQ0/1782980549/"
            "hls-vod-s0013/flv/api/files/videos/2026/03/15/1773568531d5pmp.mp4.m3u8"
        )
        assert _is_waaw_cdn_url(url)

    def test_rejects_decoy_fragment(self):
        url = "https://cfeucdn.com/2017/02/18/xxxmp666/Frag-28-v1-a1"
        assert not _is_waaw_cdn_url(url)


class TestHlsDownloadFallback:
    def test_no_ffmpeg_falls_back_to_bare_mp4(self, tmp_path):
        url = "https://cdn.example.com/secip/x/hls-vod-s03/videos/file.mp4.m3u8"
        dest = tmp_path / "out.mp4"
        with (
            patch("utils.ffmpeg_locator.locate_ffmpeg", return_value=None),
            patch("infrastructure.downloader.waaw_engine._stream_download") as stream,
        ):
            _hls_download(url, dest)
        stream.assert_called_once()
        assert stream.call_args[0][0] == "https://cdn.example.com/secip/x/hls-vod-s03/videos/file.mp4"

    def test_no_ffmpeg_and_no_mp4_fallback_raises(self, tmp_path):
        url = "https://cdn.example.com/hls-vod-s03/master.m3u8?x=1"
        with (
            patch("utils.ffmpeg_locator.locate_ffmpeg", return_value=None),
            pytest.raises(RuntimeError),
        ):
            _hls_download(url.replace(".m3u8", ""), tmp_path / "out.mp4")

    def test_fallback_preserves_query_string(self, tmp_path):
        url = "https://cdn.example.com/hls-vod-s03/file.mp4.m3u8?serv=4&md5=abc"
        dest = tmp_path / "out.mp4"
        with (
            patch("utils.ffmpeg_locator.locate_ffmpeg", return_value=None),
            patch("infrastructure.downloader.waaw_engine._stream_download") as stream,
        ):
            _hls_download(url, dest)
        assert stream.call_args[0][0] == "https://cdn.example.com/hls-vod-s03/file.mp4?serv=4&md5=abc"


class TestUnDeobfuscator:
    def test_passthrough_when_contains_dot(self):
        url = "//host.cfeucdn.com/x.m3u8"
        assert _un(url) == url

    def test_decodes_code_unit_groups(self):
        # Inverted vector: '/'=0x2f, 'w'=0x77, 'a'=0x61 -> "//waaw".
        # First char is a discarded sentinel (any value), then 3-char hex
        # groups for each decoded char.
        obf = "!" + "02f02f077061061077"
        assert _un(obf) == "//waaw"


class TestParseGetMd5Manifest:
    def test_valid_obf_link_builds_manifest(self):
        # obf_link "//host.cfeucdn.com/x.m3u8" has a "." so _un() passes it through.
        body = json.dumps({"obf_link": "//host.cfeucdn.com/x.m3u8"})
        assert _parse_get_md5_manifest(body) == "https://host.cfeucdn.com/x.m3u8"

    def test_missing_obf_link_returns_none(self):
        assert _parse_get_md5_manifest(json.dumps({})) is None

    def test_empty_obf_link_returns_none(self):
        assert _parse_get_md5_manifest(json.dumps({"obf_link": ""})) is None

    def test_decoy_obf_link_returns_none(self):
        body = json.dumps({"obf_link": "//127.0.0.1/no_video.mp4.m3u8"})
        assert _parse_get_md5_manifest(body) is None

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_get_md5_manifest("not json")

    def test_sentinel_obf_link_returns_none(self):
        # obf_link "0" decodes to "" -> manifest "https:" -- junk that must
        # not be accepted as a real manifest URL.
        assert _parse_get_md5_manifest(json.dumps({"obf_link": "0"})) is None

    def test_pending_response_returns_none(self):
        body = json.dumps({"pending": "1", "obf_link": "0"})
        assert _parse_get_md5_manifest(body) is None

    def test_blocked_response_returns_none(self):
        assert _parse_get_md5_manifest(json.dumps({"blocked": "1"})) is None

    def test_ready_response_with_valid_obf_link_builds_manifest(self):
        # Regression guard for BUG-WAAW-03: pending:"0" must not be treated
        # as truthy (bool("0") is True in Python) and discard a ready response.
        body = json.dumps({"pending": "0", "need_captcha": "0", "obf_link": "//host.cfeucdn.com/x.m3u8"})
        assert _parse_get_md5_manifest(body) == "https://host.cfeucdn.com/x.m3u8"

    def test_need_captcha_response_returns_none(self):
        # Real anti-bot wall body: obf_link is the "#" sentinel.
        body = json.dumps(
            {
                "pending": "0",
                "need_captcha": "1",
                "adscore": "",
                "try_again": "1",
                "updatecxt": "1",
                "obf_link": "#",
            }
        )
        assert _parse_get_md5_manifest(body) is None


class TestGetMd5RoutingFix:
    def _make_task(self, tmp_path):
        return DownloadTask(url="https://waaw.ac/f/ofYvCQ2DQBDk", output_dir=str(tmp_path))

    def test_extensionless_manifest_routes_to_hls(self, tmp_path):
        manifest = (
            "https://uc0o6r.cfeucdn.com/silverlight/secip/234/243254/"
            "eDFetg2523etfe/3254232523/hls-vod-s8/flv/api"
        )
        task = self._make_task(tmp_path)
        engine = WaawEngine(config=MagicMock())
        with (
            patch("infrastructure.downloader.waaw_engine.sys.platform", "win32"),
            patch("infrastructure.downloader.waaw_engine._cdp_intercept_waaw", return_value=manifest),
            patch("infrastructure.downloader.waaw_engine._hls_download") as hls,
            patch("infrastructure.downloader.waaw_engine._stream_download") as stream,
        ):
            engine.download(task)
        hls.assert_called_once()
        stream.assert_not_called()

    def test_legacy_mp4_query_routes_to_stream(self, tmp_path):
        manifest = "https://cdn.example.com/vid.mp4?serv=4&md5=abc"
        task = self._make_task(tmp_path)
        engine = WaawEngine(config=MagicMock())
        with (
            patch("infrastructure.downloader.waaw_engine.sys.platform", "win32"),
            patch("infrastructure.downloader.waaw_engine._cdp_intercept_waaw", return_value=manifest),
            patch("infrastructure.downloader.waaw_engine._hls_download") as hls,
            patch("infrastructure.downloader.waaw_engine._stream_download") as stream,
        ):
            engine.download(task)
        stream.assert_called_once()
        hls.assert_not_called()


class TestNeedsCaptcha:
    def test_true_on_captcha_wall_body(self):
        body = json.dumps(
            {"pending": "0", "need_captcha": "1", "adscore": "", "try_again": "1", "obf_link": "#"}
        )
        assert _needs_captcha(body)

    def test_false_on_ready_body(self):
        body = json.dumps({"pending": "0", "need_captcha": "0", "obf_link": "//host.cfeucdn.com/x.m3u8"})
        assert not _needs_captcha(body)

    def test_false_on_non_captcha_junk(self):
        assert not _needs_captcha("not json, no urls here at all")


class TestParseGetMd5ManifestRegexFallback:
    def test_non_json_body_with_real_manifest_url_returns_it(self):
        body = '<html><script>var x={"other":1,"m":"https://f9rw3r.cfglobalcdn.com/x/y.m3u8?t=1"}</script></html>'
        assert _parse_get_md5_manifest(body) == "https://f9rw3r.cfglobalcdn.com/x/y.m3u8?t=1"

    def test_non_json_body_with_obf_link_key_returns_manifest(self):
        body = '<html>{"obf_link": "//host.cfeucdn.com/x.m3u8", garbage</html>'
        assert _parse_get_md5_manifest(body) == "https://host.cfeucdn.com/x.m3u8"

    def test_non_json_body_with_only_decoy_url_returns_none(self):
        body = "<html>redirect to https://127.0.0.1/no_video.mp4.m3u8</html>"
        assert _parse_get_md5_manifest(body) is None

    def test_garbage_body_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_get_md5_manifest("not json, no urls here at all")

    def test_non_json_body_with_junk_obf_link_returns_none(self):
        body = '<html>{"obf_link": "0", garbage</html>'
        assert _parse_get_md5_manifest(body) is None


class FakeCdpSession:
    def __init__(self, response_body: dict):
        self._response_body = response_body
        self.sent: list[tuple[str, dict]] = []

    def send(self, method: str, params: dict | None = None):
        self.sent.append((method, params or {}))
        if method == "Fetch.getResponseBody":
            return self._response_body
        return {}


class TestHandleRequestPaused:
    def test_get_md5_response_stage_with_valid_body_captures_manifest(self):
        body = json.dumps({"obf_link": "//host.cfeucdn.com/x.m3u8"})
        session = FakeCdpSession({"body": body, "base64Encoded": False})
        captured: list[str] = []
        captcha_event = threading.Event()
        params = {
            "requestId": "req1",
            "responseStatusCode": 200,
            "request": {"url": "https://waaw.ac/player/get_md5.php?id=1"},
        }
        _handle_request_paused(session, params, captured, captcha_event)
        assert captured == ["https://host.cfeucdn.com/x.m3u8"]
        assert ("Fetch.continueResponse", {"requestId": "req1"}) in session.sent
        assert not captcha_event.is_set()

    def test_ad_url_pause_sends_vast_stub(self):
        session = FakeCdpSession({})
        captured: list[str] = []
        captcha_event = threading.Event()
        params = {"requestId": "req2", "request": {"url": "https://bigboxads.com/x"}}
        _handle_request_paused(session, params, captured, captcha_event)
        assert captured == []
        methods = [m for m, _ in session.sent]
        assert "Fetch.fulfillRequest" in methods
        assert "Fetch.getResponseBody" not in methods

    def test_get_md5_unparseable_body_still_continues_response(self):
        # Body that makes _parse_get_md5_manifest raise (no JSON, no
        # recoverable obf_link/manifest URL). The paused Fetch request must
        # still be resumed, or the browser hangs on this request forever.
        session = FakeCdpSession({"body": "not json, no urls here at all", "base64Encoded": False})
        captured: list[str] = []
        captcha_event = threading.Event()
        params = {
            "requestId": "req3",
            "responseStatusCode": 200,
            "request": {"url": "https://waaw.ac/player/get_md5.php?id=1"},
        }
        _handle_request_paused(session, params, captured, captcha_event)
        assert captured == []
        assert ("Fetch.continueResponse", {"requestId": "req3"}) in session.sent

    def test_captcha_wall_body_sets_event_and_still_continues_response(self):
        body = json.dumps(
            {
                "pending": "0",
                "need_captcha": "1",
                "adscore": "",
                "try_again": "1",
                "obf_link": "#",
            }
        )
        session = FakeCdpSession({"body": body, "base64Encoded": False})
        captured: list[str] = []
        captcha_event = threading.Event()
        params = {
            "requestId": "req4",
            "responseStatusCode": 200,
            "request": {"url": "https://waaw.ac/player/get_md5.php?id=1"},
        }
        _handle_request_paused(session, params, captured, captcha_event)
        assert captured == []
        assert captcha_event.is_set()
        assert ("Fetch.continueResponse", {"requestId": "req4"}) in session.sent


class TestIsWaawCdnLink:
    def test_accepts_cfglobalcdn_m3u8(self):
        url = (
            "https://f9rw3r.cfglobalcdn.com/silverlight/secip/213969/0/"
            "WQPVj8WRlOiJOC3iC4RR8w/MTYwLjE4Ny4xNDguMTQ0/1782980549/"
            "hls-vod-s0013/flv/api/files/videos/2026/03/15/1773568531d5pmp.mp4.m3u8"
        )
        assert is_waaw_cdn_link(url)

    def test_accepts_cfeucdn_mp4(self):
        assert is_waaw_cdn_link("https://edge1.cfeucdn.com/x/y/video.mp4")

    def test_rejects_waaw_page_url(self):
        assert not is_waaw_cdn_link("https://waaw.ac/f/ofYvCQ2DQBDk")

    def test_rejects_random_m3u8_host(self):
        assert not is_waaw_cdn_link("https://example.com/x/y.m3u8")

    def test_rejects_localhost_decoy(self):
        assert not is_waaw_cdn_link("https://127.0.0.1/no_video.mp4.m3u8")


class TestCdnLinkShortCircuit:
    def test_cdn_link_skips_intercept_and_derives_filename(self, tmp_path):
        cdn_url = (
            "https://f9rw3r.cfglobalcdn.com/silverlight/secip/213969/0/"
            "WQPVj8WRlOiJOC3iC4RR8w/MTYwLjE4Ny4xNDguMTQ0/1782980549/"
            "hls-vod-s0013/flv/api/files/videos/2026/03/15/1773568531d5pmp.mp4.m3u8"
        )
        task = DownloadTask(url=cdn_url, output_dir=str(tmp_path))
        engine = WaawEngine(config=MagicMock())
        with (
            patch("infrastructure.downloader.waaw_engine._cdp_intercept_waaw") as intercept,
            patch("infrastructure.downloader.waaw_engine._hls_download") as hls,
            patch("infrastructure.downloader.waaw_engine._stream_download") as stream,
        ):
            engine.download(task)
        intercept.assert_not_called()
        hls.assert_called_once()
        stream.assert_not_called()
        assert task.filename == str(tmp_path / "waaw_1773568531d5pmp.mp4")


class TestDownloadManagerCdnLinkRouting:
    def test_cdn_link_routes_to_waaw_engine(self, tmp_path):
        yt_engine = MagicMock()
        waaw_engine = MagicMock()
        cfg = MagicMock()
        cfg.max_concurrent = 2
        cfg.max_retries = 1
        bus = MagicMock()
        bus.publish = MagicMock()
        mgr = DownloadManager(config=cfg, engine=yt_engine, event_bus=bus, waaw_engine=waaw_engine)
        mgr.start()
        try:
            task = DownloadTask(
                url="https://f9rw3r.cfglobalcdn.com/x/video.mp4",
                output_dir=str(tmp_path),
            )
            mgr.enqueue(task)
            deadline = time.time() + 5
            while not waaw_engine.download.called and time.time() < deadline:
                time.sleep(0.02)
            waaw_engine.download.assert_called_once()
            yt_engine.download.assert_not_called()
        finally:
            mgr.shutdown(wait=False)
