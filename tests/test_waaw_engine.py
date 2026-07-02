"""Tests for infrastructure/downloader/waaw_engine.py URL matching and HLS fallback."""

import json
from unittest.mock import MagicMock, patch

import pytest

from domain.models.download_task import DownloadTask
from infrastructure.downloader.waaw_engine import (
    _AD_BLOCK_URLS,
    _JS_MATCH,
    WaawEngine,
    _hls_download,
    _is_waaw_cdn_url,
    _parse_get_md5_manifest,
    _un,
    is_waaw_url,
)


class TestJsMatchDecoyExclusion:
    def test_js_match_rejects_decoy(self):
        assert "no_video" in _JS_MATCH


class TestAdBlockUrls:
    def test_non_empty(self):
        assert _AD_BLOCK_URLS

    def test_contains_log_grounded_ad_hosts(self):
        blob = " ".join(_AD_BLOCK_URLS)
        assert "bigboxads.com" in blob
        assert "videocdnmetrika.com" in blob
        assert "counter.yadro.ru" in blob

    def test_contains_vast_waterfall_hosts(self):
        # BUG-WAAW-01: waaw.ac's preroll VAST waterfall (player/waterfall.php
        # -> these five ad exchanges tried in sequence) was consuming the
        # full 60s poll window before the real CDN request ever fired.
        blob = " ".join(_AD_BLOCK_URLS)
        assert "twinrdsyte.com" in blob
        assert "megawebify.my" in blob
        assert "videosprofitnetwork.com" in blob
        assert "magsrv.com" in blob
        assert "vstserv.com" in blob
        assert "yomeno.xyz" in blob


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
