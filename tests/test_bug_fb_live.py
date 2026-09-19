"""
tests/test_bug_fb_live.py
BUG-FB-LIVE: Facebook livestreams were downloaded as VODs.

yt-dlp's FacebookIE never sets is_live/live_status, so an in-progress
broadcast took the VOD path: HlsFD grabbed the segments the playlist listed at
that instant and stopped, and the app reported a completed download.

Covers:
- _facebook_live_manifest_url: #EXT-X-ENDLIST decides live vs finished
- _facebook_live_manifest_url: MPD@type decides live vs finished (BUG-FB-LIVE-DASH)
- _facebook_live_manifest_url: non-Facebook CDN hosts are never fetched
- extract_info: MediaInfo.is_live is True for an in-progress broadcast
- extract_info: the decrypted temp cookie is deleted on hard-error raises
- _download_live_hls_direct: the Referer header follows the platform
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import infrastructure.downloader.yt_dlp_engine as mod
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

_LIVE_PLAYLIST = "#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nseg1.ts\n"
_ENDED_PLAYLIST = _LIVE_PLAYLIST + "#EXT-X-ENDLIST\n"

_FB_HLS = "https://video.xx.fbcdn.net/v/live-hls/playlist.m3u8?oh=abc"
_FB_MPD = "https://video.xx.fbcdn.net/v/live-dash/manifest.mpd?oh=abc"

_LIVE_MPD = '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="dynamic"></MPD>'
_ENDED_MPD = '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"></MPD>'


def _dash_formats(url=_FB_MPD):
    """Two representations of one manifest, the way yt-dlp returns Facebook DASH."""
    return [
        {"format_id": "dash-lp-md-a", "protocol": "http_dash_segments", "manifest_url": url},
        {"format_id": "dash-lp-pst-v", "protocol": "http_dash_segments", "manifest_url": url},
    ]


def _make_config(**kwargs):
    cfg = MagicMock()
    cfg.proxy = ""
    cfg.use_cookies = False
    cfg.cookies_browser = "brave"
    cfg.cookie_file = ""
    cfg.platform_cookies = {}
    cfg.max_retries = 1
    cfg.download_dir = Path("/tmp")
    cfg.embed_metadata = False
    cfg.embed_thumbnail = False
    cfg.extra_args = ""
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def _fake_curl(body: str):
    resp = MagicMock()
    resp.text = body
    req = MagicMock()
    req.get.return_value = resp
    return req


class TestFacebookLiveProbe:
    def test_a_non_http_scheme_is_never_fetched(self):
        # A file:// URL whose host looks like Facebook's CDN must not reach
        # urlopen, which would read a local file instead of a manifest.
        with patch.object(mod, "_CURL_CFFI_AVAILABLE", False):
            assert mod._fb_fetch_manifest("file://www.facebook.com/etc/passwd") is None

    def test_live_playlist_without_endlist_is_live(self):
        import curl_cffi

        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", _fake_curl(_LIVE_PLAYLIST)),
        ):
            result = mod._facebook_live_manifest_url([{"protocol": "m3u8_native", "url": _FB_HLS}])

        assert result == _FB_HLS

    def test_playlist_with_endlist_is_not_live(self):
        import curl_cffi

        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", _fake_curl(_ENDED_PLAYLIST)),
        ):
            result = mod._facebook_live_manifest_url([{"protocol": "m3u8_native", "url": _FB_HLS}])

        assert result is None

    def test_non_facebook_host_is_never_fetched(self):
        import curl_cffi

        fake = _fake_curl(_LIVE_PLAYLIST)
        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", fake),
        ):
            result = mod._facebook_live_manifest_url(
                [{"protocol": "m3u8_native", "url": "https://evil.example.com/p.m3u8"}]
            )

        assert result is None
        fake.get.assert_not_called()

    def test_notfbcdn_suffix_lookalike_rejected(self):
        import curl_cffi

        fake = _fake_curl(_LIVE_PLAYLIST)
        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", fake),
        ):
            result = mod._facebook_live_manifest_url(
                [{"protocol": "m3u8_native", "url": "https://notfbcdn.net/p.m3u8"}]
            )

        assert result is None
        fake.get.assert_not_called()

    @pytest.mark.parametrize("formats", [None, [], [{"protocol": "https", "url": _FB_HLS}]])
    def test_no_hls_formats_returns_none(self, formats):
        assert mod._facebook_live_manifest_url(formats) is None

    def test_dynamic_mpd_is_live(self):
        import curl_cffi

        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", _fake_curl(_LIVE_MPD)),
        ):
            assert mod._facebook_live_manifest_url(_dash_formats()) == _FB_MPD

    def test_static_mpd_is_not_live(self):
        import curl_cffi

        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", _fake_curl(_ENDED_MPD)),
        ):
            assert mod._facebook_live_manifest_url(_dash_formats()) is None

    def test_one_manifest_is_fetched_once_for_all_representations(self):
        import curl_cffi

        fake = _fake_curl(_LIVE_MPD)
        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", fake),
        ):
            mod._facebook_live_manifest_url(_dash_formats())

        assert fake.get.call_count == 1

    def test_non_facebook_mpd_host_is_never_fetched(self):
        import curl_cffi

        fake = _fake_curl(_LIVE_MPD)
        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", fake),
        ):
            result = mod._facebook_live_manifest_url(_dash_formats("https://evil.example.com/manifest.mpd"))

        assert result is None
        fake.get.assert_not_called()

    def test_hls_answer_wins_before_any_mpd_is_fetched(self):
        import curl_cffi

        fake = _fake_curl(_LIVE_PLAYLIST)
        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", fake),
        ):
            result = mod._facebook_live_manifest_url(
                [{"protocol": "m3u8_native", "url": _FB_HLS}, *_dash_formats()]
            )

        assert result == _FB_HLS
        assert fake.get.call_count == 1

    def test_dash_formats_without_manifest_url_are_skipped(self):
        assert (
            mod._facebook_live_manifest_url(
                [{"protocol": "http_dash_segments", "url": "https://video.xx.fbcdn.net/v/seg.mp4"}]
            )
            is None
        )

    def test_probe_failure_returns_none(self):
        import curl_cffi

        fake = MagicMock()
        fake.get.side_effect = Exception("network down")
        with (
            patch.object(mod, "_CURL_CFFI_AVAILABLE", True),
            patch.object(curl_cffi, "requests", fake),
        ):
            assert mod._facebook_live_manifest_url([{"protocol": "m3u8", "url": _FB_HLS}]) is None


class TestExtractInfoMarksFacebookLive:
    _URL = "https://www.facebook.com/story.php?story_fbid=1947052972630305&id=61590925058723"

    def _run(self, probe_result):
        engine = YtDlpEngine(_make_config())
        info = {
            "id": "194705297263",
            "title": "Facebook Live",
            "uploader": "TrisDen Tv",
            "duration": 0,
            "formats": [{"protocol": "m3u8_native", "url": _FB_HLS}],
            # yt-dlp leaves is_live unset for every Facebook URL
        }
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = info
        with (
            patch.object(mod.yt_dlp, "YoutubeDL", return_value=ydl),
            patch.object(mod, "_facebook_live_manifest_url", return_value=probe_result),
        ):
            return engine.extract_info(self._URL)

    def test_in_progress_broadcast_sets_is_live(self):
        assert self._run(_FB_HLS).is_live is True

    def test_finished_video_stays_vod(self):
        assert self._run(None).is_live is False


class TestExtractInfoCookieCleanup:
    """The decrypted plaintext cookie must not survive a failed analyse."""

    def test_hard_error_deletes_temp_cookie(self, tmp_path):
        temp_cookie = tmp_path / "omnidl_dec_test.txt"
        temp_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        engine = YtDlpEngine(_make_config())
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.side_effect = mod.yt_dlp.utils.DownloadError(
            "ERROR: [facebook] Video unavailable"
        )
        with (
            patch.object(mod, "_resolve_cookie", return_value=str(tmp_path / "fb.enc")),
            patch.object(mod, "_prepare_cookie_for_use", return_value=(str(temp_cookie), True)),
            patch.object(mod.yt_dlp, "YoutubeDL", return_value=ydl),
            pytest.raises(RuntimeError),
        ):
            engine.extract_info("https://www.facebook.com/watch/?v=123")

        assert not temp_cookie.exists()

    def test_all_retries_exhausted_deletes_temp_cookie(self, tmp_path):
        temp_cookie = tmp_path / "omnidl_dec_test2.txt"
        temp_cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

        engine = YtDlpEngine(_make_config())
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.side_effect = mod.yt_dlp.utils.DownloadError(
            "ERROR: connection reset by peer"
        )
        with (
            patch.object(mod, "_resolve_cookie", return_value=str(tmp_path / "fb.enc")),
            patch.object(mod, "_prepare_cookie_for_use", return_value=(str(temp_cookie), True)),
            patch.object(mod.yt_dlp, "YoutubeDL", return_value=ydl),
            patch.object(mod.time, "sleep", lambda _s: None),
            pytest.raises(RuntimeError),
        ):
            engine.extract_info("https://www.facebook.com/watch/?v=123")

        assert not temp_cookie.exists()


class TestDirectRecorderReferer:
    def _captured_cmd(self, referer, url=_FB_HLS):
        engine = YtDlpEngine(_make_config())
        task = MagicMock()
        task.is_cancellation_requested = True  # exit the poll loop immediately
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stderr = []
        captured = {}

        def _popen(cmd, **_kw):
            captured["cmd"] = cmd
            return proc

        with (
            patch.object(mod, "get_ffmpeg_path", return_value=None),
            patch.object(mod, "_prepare_cookie_for_use", return_value=("/tmp/c.txt", False)),
            patch.object(mod, "_build_ffmpeg_cookie_header", return_value="c_user=1"),
            patch("subprocess.Popen", _popen),
            pytest.raises(mod.yt_dlp.utils.DownloadError),
        ):
            engine._download_live_hls_direct(
                url, "/tmp/out.ts", task, "/tmp/cookie.enc", None, referer=referer
            )
        return captured["cmd"]

    def test_facebook_referer_is_sent(self):
        cmd = self._captured_cmd("https://www.facebook.com/")
        assert any("Referer: https://www.facebook.com/" in str(a) for a in cmd)

    def test_mpd_input_drops_http_persistent(self):
        # BUG-FB-LIVE-DASH: -http_persistent belongs to FFmpeg's HLS demuxer;
        # passing it with a .mpd input aborts FFmpeg on "Option not found".
        cmd = self._captured_cmd("https://www.facebook.com/", url=_FB_MPD)
        assert "-http_persistent" not in cmd
        assert cmd[cmd.index("-allowed_extensions") + 1] == "ALL"

    def test_hls_input_keeps_http_persistent(self):
        cmd = self._captured_cmd("https://www.tiktok.com/")
        assert "-http_persistent" in cmd
        assert "-allowed_extensions" not in cmd

    def test_tiktok_referer_is_the_default(self):
        engine = YtDlpEngine(_make_config())
        import inspect

        sig = inspect.signature(engine._download_live_hls_direct)
        assert sig.parameters["referer"].default == "https://www.tiktok.com/"


class TestFacebookLiveRecordingRegressions310826:
    """Log 27862-29653 (31/08/2026): four Facebook Live attempts, all dead.

    Direct FFmpeg exited 4294967291 (= -5, AVERROR(EIO), a transport-layer open
    failure) and the yt-dlp fallback then died on "Requested format is not
    available".  These guard the five defects found in that trace.
    """

    def _cmd_for(self, referer, url, cookie_text, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(cookie_text, encoding="utf-8")
        engine = YtDlpEngine(_make_config())
        task = MagicMock()
        task.is_cancellation_requested = True
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stderr = []
        captured = {}

        def _popen(cmd, **_kw):
            captured["cmd"] = cmd
            return proc

        with (
            patch.object(mod, "get_ffmpeg_path", return_value=None),
            patch.object(mod, "_prepare_cookie_for_use", return_value=(str(cookie_file), False)),
            patch("subprocess.Popen", _popen),
            pytest.raises(mod.yt_dlp.utils.DownloadError),
        ):
            engine._download_live_hls_direct(
                url, "/tmp/out.ts", task, str(cookie_file), None, referer=referer
            )
        return captured["cmd"]

    def test_facebook_cookies_reach_ffmpeg(self, tmp_path):
        # BUG-FB-LIVE-HDR: the domain filter was hardcoded to "tiktok", so a
        # Facebook cookie jar produced an empty header and no cookies were sent.
        cmd = self._cmd_for(
            "https://www.facebook.com/",
            _FB_MPD,
            ".facebook.com\tTRUE\t/\tFALSE\t0\tc_user\t61574701540239\n",
            tmp_path,
        )
        hdr = cmd[cmd.index("-headers") + 1]
        assert "Cookie: c_user=61574701540239" in hdr

    def test_facebook_referer_sent_even_without_cookies(self, tmp_path):
        # The Referer used to be emitted only inside the cookie branch, so a
        # cookie-less Facebook jar dropped it silently.
        cmd = self._cmd_for("https://www.facebook.com/", _FB_MPD, "# empty jar\n", tmp_path)
        hdr = cmd[cmd.index("-headers") + 1]
        assert "Referer: https://www.facebook.com/" in hdr
        assert "Cookie:" not in hdr

    def test_tiktok_cookies_still_selected(self, tmp_path):
        cmd = self._cmd_for(
            "https://www.tiktok.com/",
            "https://pull.tiktokcdn.com/x.m3u8",
            ".tiktok.com\tTRUE\t/\tFALSE\t0\tsessionid\tabc\n.facebook.com\tTRUE\t/\tFALSE\t0\tc_user\t999\n",
            tmp_path,
        )
        hdr = cmd[cmd.index("-headers") + 1]
        assert "sessionid=abc" in hdr
        assert "c_user=999" not in hdr

    def test_build_cookie_header_domain_keyword(self, tmp_path):
        cookie_file = tmp_path / "c.txt"
        cookie_file.write_text(
            ".facebook.com\tTRUE\t/\tFALSE\t0\txs\tsecret\n.tiktok.com\tTRUE\t/\tFALSE\t0\tsessionid\tabc\n",
            encoding="utf-8",
        )
        assert mod._build_ffmpeg_cookie_header(str(cookie_file), "facebook") == "xs=secret"
        # default stays TikTok-only for every existing caller
        assert mod._build_ffmpeg_cookie_header(str(cookie_file)) == "sessionid=abc"

    def test_ffmpeg_error_keeps_the_first_stderr_line(self, tmp_path):
        # BUG-FB-LIVE-DIAG: [-300:] kept only the closing summary, so the log
        # showed "Error opening input files: I/O error" with the cause cut off.
        engine = YtDlpEngine(_make_config())
        task = MagicMock()
        task.is_cancellation_requested = False
        task.elapsed = ""
        proc = MagicMock()
        proc.poll.return_value = 4294967291
        proc.returncode = 4294967291
        cause = "[tls @ 000001] handshake failed with Facebook CDN"
        noise = "\n".join(f"[https @ 0000{i:02d}] Will reconnect at 0 in 0 second(s)." for i in range(18))
        proc.stderr = [
            f"{cause}\n".encode(),
            *[f"{line}\n".encode() for line in noise.splitlines()],
            b"Error opening input files: I/O error\n",
        ]
        with (
            patch.object(mod, "get_ffmpeg_path", return_value=None),
            patch.object(mod, "_prepare_cookie_for_use", return_value=("/tmp/c.txt", False)),
            patch("subprocess.Popen", return_value=proc),
            pytest.raises(RuntimeError) as excinfo,
        ):
            engine._download_live_hls_direct(
                _FB_MPD, "/tmp/out.ts", task, "", None, referer="https://www.facebook.com/"
            )
        msg = str(excinfo.value)
        assert cause in msg
        assert "Error opening input files: I/O error" in msg

    def test_live_format_selector_falls_back_to_dash_pair(self):
        # BUG-FB-LIVE-FMT: bare "best" means "best muxed format"; a Facebook
        # broadcast only offers video-only + audio-only DASH representations.
        import re

        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert re.search(r'else "best/bv\*\+ba"', src)


class TestTempCookieNeverLeaksOnDownloadError:
    """BUG-COOKIE-LEAK: four plaintext omnidl_dec_*.txt files survived the
    session because the unlink sat at the end of the body, not in a finally."""

    def test_temp_cookie_removed_when_download_raises(self, tmp_path):
        engine = YtDlpEngine(_make_config())
        temp_cookie = tmp_path / "omnidl_dec_leak.txt"
        temp_cookie.write_text(".facebook.com\tTRUE\t/\tFALSE\t0\txs\tsecret\n", encoding="utf-8")

        def _impl(_self, _task, _on_p, _on_pp, temps):
            temps.append(str(temp_cookie))
            raise RuntimeError("ERROR: Requested format is not available")

        with (
            patch.object(YtDlpEngine, "_download_impl", _impl),
            pytest.raises(RuntimeError),
        ):
            engine.download(MagicMock())

        assert not temp_cookie.exists()


class TestFacebookLiveRecordingRegressions040926:
    """Log 16234-16349 (04/09/2026): the broadcast was still on air but OmniDL
    reported two "completed" recordings of ~60s each.

    Direct FFmpeg died on 'Unable to read to manifest ... I/O error' every time
    and the yt-dlp fallback captured only the segments the DASH manifest listed
    at that instant.
    """

    def _cmd_for_url(self, url):
        engine = YtDlpEngine(_make_config())
        task = MagicMock()
        task.is_cancellation_requested = True
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stderr = []
        captured = {}

        def _popen(cmd, **_kw):
            captured["cmd"] = cmd
            return proc

        with (
            patch.object(mod, "get_ffmpeg_path", return_value=None),
            patch("subprocess.Popen", _popen),
            pytest.raises(mod.yt_dlp.utils.DownloadError),
        ):
            engine._download_live_hls_direct(url, "/tmp/out.ts", task, "", None, referer="https://x/")
        return captured["cmd"]

    def test_mpd_input_drops_reconnect_at_eof(self):
        # BUG-FB-LIVE-EOF: the DASH demuxer reads the whole manifest and then
        # checks avio_feof(); -reconnect_at_eof turns that EOF into an endless
        # "Will reconnect at <size> in 0 second(s), error=End of file." loop and
        # dashdec aborts with "Unable to read to manifest" / AVERROR(EIO).
        assert "-reconnect_at_eof" not in self._cmd_for_url(_FB_MPD)

    def test_hls_input_keeps_reconnect_at_eof(self):
        # TikTok live HLS relies on it to survive the end of a segment stream.
        cmd = self._cmd_for_url("https://pull.tiktokcdn.com/x.m3u8")
        assert cmd[cmd.index("-reconnect_at_eof") + 1] == "1"

    def test_mpd_input_keeps_the_other_reconnect_flags(self):
        cmd = self._cmd_for_url(_FB_MPD)
        assert cmd[cmd.index("-reconnect") + 1] == "1"
        assert cmd[cmd.index("-reconnect_max_retries") + 1] == "10"
        assert cmd[cmd.index("-allowed_extensions") + 1] == "ALL"

    def test_append_seg_joins_and_deletes(self, tmp_path):
        main = tmp_path / "live.ts"
        main.write_bytes(b"AAA")
        seg = tmp_path / "live.ts.seg1"
        seg.write_bytes(b"BBB")
        mod._fb_append_seg(str(main), str(seg), 1)
        assert main.read_bytes() == b"AAABBB"
        assert not seg.exists()

    def test_append_seg_is_a_noop_for_the_first_run(self, tmp_path):
        main = tmp_path / "live.ts"
        main.write_bytes(b"AAA")
        mod._fb_append_seg(str(main), str(main), 0)
        assert main.read_bytes() == b"AAA"
        assert main.exists()

    def test_append_seg_survives_a_missing_segment(self, tmp_path):
        main = tmp_path / "live.ts"
        main.write_bytes(b"AAA")
        mod._fb_append_seg(str(main), str(tmp_path / "gone.seg1"), 1)
        assert main.read_bytes() == b"AAA"

    def test_facebook_live_filename_uses_the_page_id(self):
        # BUG-FB-LIVE-NAME: every Facebook Live was saved as "Unknown - [LIVE] ...".
        task = MagicMock()
        task.media_info = None
        task.url = "https://www.facebook.com/story.php?story_fbid=954074224400548&id=61591433312332"
        assert mod._live_final_name(task, "2026-09-04 19-20-19", "954074224400548").startswith(
            "FB_61591433312332 - "
        )

    def test_facebook_live_filename_uses_the_page_slug(self):
        task = MagicMock()
        task.media_info = None
        task.url = "https://www.facebook.com/somepage/videos/123456"
        assert mod._live_final_name(task, "2026-09-04 19-20-19", "123456").startswith("FB_somepage - ")

    def test_tiktok_live_filename_is_unchanged(self):
        task = MagicMock()
        task.media_info = None
        task.url = "https://www.tiktok.com/@someuser/live"
        assert mod._live_final_name(task, "2026-09-04 19-20-19", "1").startswith("someuser - ")

    def test_unknown_platform_still_falls_back_to_unknown(self):
        task = MagicMock()
        task.media_info = None
        task.url = "https://example.com/live"
        assert mod._live_final_name(task, "2026-09-04 19-20-19", "1").startswith("Unknown - ")


class TestFacebookLiveResumesWhileTheBroadcastRuns:
    """BUG-FB-LIVE-RESUME: one FFmpeg run is not one broadcast.

    Log 04/09/2026 19:20:19-19:21:23 and 19:22:54-19:23:45: both tasks were
    marked completed after ~60s while the stream was still on air.
    """

    def _run(self, manifest_sequence, chunk=b"X" * 4096, first_run_only=False, tmp_path=None):
        from domain.models.download_task import DownloadTask, MediaInfo

        task = DownloadTask(
            url="https://www.facebook.com/story.php?story_fbid=954074224400548&id=61591433312332",
            output_dir=str(tmp_path),
            media_info=MediaInfo(url="", is_live=True, video_id="954074224400548"),
        )
        engine = YtDlpEngine(_make_config(download_dir=tmp_path))
        runs = []

        def _record(_self, _url, out_path, _task, _cookie, _on_p, referer=""):
            runs.append(out_path)
            with open(out_path, "ab") as fh:
                fh.write(b"" if first_run_only and len(runs) > 1 else chunk)

        seq = iter(manifest_sequence)

        def _next_manifest(_self, *_a, **_kw):
            return next(seq, None)

        with (
            patch.object(YtDlpEngine, "_download_live_hls_direct", _record),
            patch.object(YtDlpEngine, "_extract_facebook_live_manifest_url", _next_manifest),
            patch.object(mod, "_resolve_cookie", return_value=""),
        ):
            engine._download_impl(task, None, None, [])
        return task, runs

    def test_recording_resumes_until_the_manifest_goes_static(self, tmp_path):
        # 1st call arms the recorder, 2nd/3rd say "still live", 4th says ended.
        _task, runs = self._run([_FB_MPD, _FB_MPD, _FB_MPD, None], tmp_path=tmp_path)
        assert len(runs) == 3, runs
        assert runs[1].endswith(".seg1")
        assert runs[2].endswith(".seg2")

    def test_resumed_parts_are_appended_into_one_file(self, tmp_path):
        task, _runs = self._run([_FB_MPD, _FB_MPD, None], tmp_path=tmp_path)
        out = Path(task.filename)
        assert out.is_file()
        assert out.stat().st_size == 2 * 4096
        assert not list(tmp_path.glob("*.seg*"))

    def test_a_finished_broadcast_records_once(self, tmp_path):
        _task, runs = self._run([_FB_MPD, None], tmp_path=tmp_path)
        assert len(runs) == 1

    def test_the_final_name_carries_the_facebook_page_id(self, tmp_path):
        task, _runs = self._run([_FB_MPD, None], tmp_path=tmp_path)
        assert Path(task.filename).name.startswith("FB_61591433312332 - [LIVE] ")

    def test_a_resume_that_records_nothing_stops_the_loop(self, tmp_path):
        # Guards against spinning when a resumed FFmpeg run exits instantly and
        # the manifest keeps claiming the broadcast is live.
        _task, runs = self._run([_FB_MPD] * 8, first_run_only=True, tmp_path=tmp_path)
        assert len(runs) == 2, runs
