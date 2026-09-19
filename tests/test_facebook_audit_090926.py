"""
Regression tests for the Facebook audit of 2026-09-09 (v20.2.0).

Covers the six defects found across the desktop and Remote API Facebook paths:

  FB-A  facebook_story_engine._has_audio_stream used ffmpeg_bin.parent/"ffprobe",
        which is missing ".exe" on Windows -> FileNotFoundError -> the except
        branch returned True, so a video-only DASH result was accepted as
        "with audio".
  FB-B  The Story route in DownloadManager ignored task.output_dir, so a Story
        queued with a custom folder was written to config.download_dir.
  FB-C  utils.facebook_live_checker.check_facebook_live never closed its
        curl_cffi session -- one leaked libcurl handle per live-monitor check.
  FB-D  facebook_story_engine._download_cdn_url never closed the streaming
        response, leaking a socket on the 300 s deadline path.
  FB-E  The derived audio URL was built from the raw CDN URL, so a captured
        segment URL's bytestart/byteend survived into the probe.
  FB-F  GET /api/ping reported a hard-coded "1.0.0" instead of the real version.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest

import infrastructure.downloader.facebook_story_engine as fbeng


# ─────────────────────────────────────────────────────────────────────────────
# FB-A — _has_audio_stream takes ffprobe_bin, not ffmpeg_bin
# ─────────────────────────────────────────────────────────────────────────────
class TestHasAudioStreamUsesFfprobeBin:
    def test_runs_the_ffprobe_binary_it_is_given(self, tmp_path):
        seen: list[list[str]] = []

        def _fake_run(cmd, **kwargs):
            seen.append(cmd)
            return types.SimpleNamespace(stdout=b"audio\n", stderr=b"", returncode=0)

        with patch.object(fbeng.subprocess, "run", side_effect=_fake_run):
            assert fbeng._has_audio_stream(r"C:\ff\bin\ffprobe.exe", tmp_path / "v.mp4") is True

        assert seen[0][0] == r"C:\ff\bin\ffprobe.exe"

    def test_no_audio_stream_reports_false(self, tmp_path):
        with patch.object(
            fbeng.subprocess,
            "run",
            return_value=types.SimpleNamespace(stdout=b"", stderr=b"", returncode=0),
        ):
            assert fbeng._has_audio_stream("/usr/bin/ffprobe", tmp_path / "v.mp4") is False

    def test_missing_ffprobe_sentinel_short_circuits(self, tmp_path):
        with patch.object(fbeng.subprocess, "run", side_effect=AssertionError("must not run")):
            assert fbeng._has_audio_stream("<not found>", tmp_path / "v.mp4") is True
            assert fbeng._has_audio_stream("", tmp_path / "v.mp4") is True

    def test_dash_all_path_probes_with_ffprobe_bin_not_ffmpeg_bin(self, tmp_path):
        """_ffmpeg_download_with_audio must hand _has_audio_stream the ffprobe path.

        Before the fix it passed loc.ffmpeg_bin, so on Windows the probe raised
        FileNotFoundError, the except branch returned True, and a silent file
        was returned as "audio captured via ffmpeg DASH demuxer".
        """
        dest = tmp_path / "story.mp4"
        loc = types.SimpleNamespace(
            ffmpeg_bin=r"C:\ff\bin\ffmpeg.exe",
            ffprobe_bin=r"C:\ff\bin\ffprobe.exe",
        )
        probed: list[str] = []

        def _fake_ffmpeg_run(cmd, **kwargs):
            dest.write_bytes(b"\x00\x00\x00\x18ftyp" + b"\x00" * 200_000)
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        with (
            patch("utils.ffmpeg_locator.locate_ffmpeg", return_value=loc),
            patch.object(fbeng.subprocess, "run", side_effect=_fake_ffmpeg_run),
            patch.object(
                fbeng,
                "_has_audio_stream",
                side_effect=lambda b, p: (probed.append(b), True)[1],
            ),
        ):
            result = fbeng._ffmpeg_download_with_audio(
                "https://scontent.fna.fbcdn.net/o1/v/t2/f2/m367/V.mp4?oh=v",
                dest,
                None,
            )

        assert result == dest
        assert probed == [r"C:\ff\bin\ffprobe.exe"]


# ─────────────────────────────────────────────────────────────────────────────
# FB-B — download_story honours an explicit output_dir
# ─────────────────────────────────────────────────────────────────────────────
class TestDownloadStoryOutputDir:
    URL = "https://www.facebook.com/stories/122114830586207697/UzpfSVND/"
    VIDEO_URL = "https://scontent.fna.fbcdn.net/o1/v/t2/f2/m367/VIDEO.mp4?oh=v&oe=1"

    @staticmethod
    def _write_cdn(cdn_url, dest, on_progress=None):
        dest.write_bytes(b"\x00" * 200_000)
        return dest

    def _run(self, tmp_path, output_dir):
        config = types.SimpleNamespace(download_dir=str(tmp_path / "default"))
        (tmp_path / "default").mkdir()
        with (
            patch.object(fbeng, "_cdp_intercept", return_value=(self.VIDEO_URL, None, None)),
            patch.object(fbeng, "_probe_audio_url", return_value=None),
            patch.object(fbeng, "_ffmpeg_mux") as mux,
            patch.object(fbeng, "_download_cdn_url", side_effect=self._write_cdn),
            patch.object(fbeng, "_ffmpeg_download_with_audio", return_value=None),
            patch.object(fbeng, "_ffmpeg_download") as single,
        ):
            result = fbeng.download_story(self.URL, config, output_dir=output_dir)
        mux.assert_not_called()
        single.assert_not_called()
        return result

    def test_custom_output_dir_wins_over_config(self, tmp_path):
        custom = tmp_path / "custom folder"
        result = self._run(tmp_path, custom)
        assert result.parent == custom.resolve() or result.parent == custom
        assert result.exists()

    def test_custom_output_dir_is_created(self, tmp_path):
        custom = tmp_path / "deep" / "nested"
        assert not custom.exists()
        self._run(tmp_path, custom)
        assert custom.is_dir()

    def test_none_output_dir_still_uses_config_download_dir(self, tmp_path):
        result = self._run(tmp_path, None)
        assert result.parent == tmp_path / "default"

    def test_missing_download_dir_still_raises_when_no_override(self, tmp_path):
        config = types.SimpleNamespace(download_dir="")
        with pytest.raises(RuntimeError):
            fbeng.download_story(self.URL, config)


class TestDownloadManagerPassesOutputDir:
    def test_story_route_forwards_task_output_dir(self, tmp_path):
        """DownloadManager must pass task.output_dir into download_story()."""
        import inspect

        from infrastructure.downloader import download_manager as dm

        src = inspect.getsource(dm.DownloadManager._run_task)
        assert "output_dir=Path(task.output_dir) if task.output_dir else None" in src

    def test_download_story_accepts_output_dir_keyword(self):
        import inspect

        assert "output_dir" in inspect.signature(fbeng.download_story).parameters


# ─────────────────────────────────────────────────────────────────────────────
# FB-C — check_facebook_live closes its session
# ─────────────────────────────────────────────────────────────────────────────
class _FakeResponse:
    def __init__(self, text="", status_code=200, url=""):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers: dict = {}


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.closed = False
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if not self._responses:
            return _FakeResponse(url=url)
        return self._responses.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture
def fb_cookie(tmp_path):
    """A Netscape cookie file carrying a valid-looking Facebook session."""
    p = tmp_path / "fb_cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t100000000000000\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\txs\tabcdef\n",
        encoding="utf-8",
    )
    return str(p)


class TestCheckFacebookLiveClosesSession:
    def _patch_session(self, session):
        import utils.tiktok_live_checker as ttlc

        return patch.object(ttlc, "_get_impersonate_session", return_value=session)

    def test_session_closed_on_not_live(self, fb_cookie):
        from utils.facebook_live_checker import check_facebook_live

        session = _FakeSession(
            [
                _FakeResponse(text="<html>offline</html>", url="https://www.facebook.com/page/live/"),
                _FakeResponse(text="<html>offline</html>", url="https://www.facebook.com/page"),
            ]
        )
        with self._patch_session(session):
            assert check_facebook_live("page", fb_cookie) is None
        assert session.closed is True

    def test_session_closed_on_live(self, fb_cookie):
        from utils.facebook_live_checker import check_facebook_live

        html = '{"is_live_streaming":true,"video_id":"1234567890"}'
        session = _FakeSession([_FakeResponse(text=html, url="https://www.facebook.com/page/live/")])
        with self._patch_session(session):
            live_url = check_facebook_live("page", fb_cookie)
        assert live_url == "https://www.facebook.com/page/videos/1234567890"
        assert session.closed is True

    def test_session_closed_when_facebook_rate_limits(self, fb_cookie):
        from utils.facebook_live_checker import check_facebook_live

        session = _FakeSession([_FakeResponse(status_code=429, url="https://www.facebook.com/")])
        with self._patch_session(session), pytest.raises(RuntimeError, match="blocked"):
            check_facebook_live("page", fb_cookie)
        assert session.closed is True

    def test_session_closed_when_the_request_itself_fails(self, fb_cookie):
        from utils.facebook_live_checker import check_facebook_live

        class _Boom(_FakeSession):
            def get(self, url, **kwargs):
                raise OSError("connect failed")

        session = _Boom([])
        with self._patch_session(session), pytest.raises(RuntimeError):
            check_facebook_live("page", fb_cookie)
        assert session.closed is True


# ─────────────────────────────────────────────────────────────────────────────
# FB-D — _download_cdn_url closes the streaming response
# ─────────────────────────────────────────────────────────────────────────────
class _FakeStreamResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=0):
        yield from self._chunks

    def close(self):
        self.closed = True


class TestDownloadCdnUrlClosesResponse:
    URL = "https://scontent.fna.fbcdn.net/o1/v/t2/f2/m367/V.mp4?oh=v&bytestart=0&byteend=99"

    def test_response_closed_on_success(self, tmp_path):
        body = b"\x00\x00\x00\x18ftyp" + b"\x00" * 200_000
        resp = _FakeStreamResponse([body], {"content-length": str(len(body))})
        with (
            patch("requests.head", side_effect=OSError("no head")),
            patch("requests.get", return_value=resp),
        ):
            out = fbeng._download_cdn_url(self.URL, tmp_path / "v.mp4", None)
        assert out is not None
        assert resp.closed is True

    def test_response_closed_when_the_stream_deadline_fires(self, tmp_path):
        resp = _FakeStreamResponse([b"x" * 1024, b"y" * 1024])
        # First monotonic() readings feed start/deadline, then jump past +300 s.
        ticks = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])

        with (
            patch("requests.head", side_effect=OSError("no head")),
            patch("requests.get", return_value=resp),
            patch.object(fbeng.time, "monotonic", side_effect=lambda: next(ticks, 10_000.0)),
        ):
            out = fbeng._download_cdn_url(self.URL, tmp_path / "v.mp4", None)

        assert out is None
        assert resp.closed is True


# ─────────────────────────────────────────────────────────────────────────────
# FB-E — audio derivation strips the byte-range params first
# ─────────────────────────────────────────────────────────────────────────────
class TestAudioDerivationStripsByteRange:
    URL = "https://www.facebook.com/stories/122114830586207697/UzpfSVND/"
    # A captured DASH *segment* URL: it still carries bytestart / byteend.
    SEGMENT_VIDEO_URL = (
        "https://scontent.fna.fbcdn.net/o1/v/t2/f2/m367/VIDEO.mp4?oh=v&oe=1&bytestart=125000&byteend=249999"
    )

    def test_probe_receives_a_range_free_audio_candidate(self, tmp_path):
        config = types.SimpleNamespace(download_dir=str(tmp_path))
        probed: list[str] = []

        with (
            patch.object(fbeng, "_cdp_intercept", return_value=(self.SEGMENT_VIDEO_URL, None, None)),
            patch.object(
                fbeng,
                "_probe_audio_url",
                side_effect=lambda c: (probed.append(c), None)[1],
            ),
            patch.object(
                fbeng,
                "_download_cdn_url",
                side_effect=lambda u, d, p=None: (d.write_bytes(b"\x00" * 200_000), d)[1],
            ),
            patch.object(fbeng, "_ffmpeg_download_with_audio", return_value=None),
            patch.object(fbeng, "_ffmpeg_download"),
        ):
            fbeng.download_story(self.URL, config)

        assert len(probed) == 1
        candidate = probed[0]
        assert "/o1/a/" in candidate
        assert "bytestart" not in candidate
        assert "byteend" not in candidate


# ─────────────────────────────────────────────────────────────────────────────
# FB-F — /api/ping reports the real application version
# ─────────────────────────────────────────────────────────────────────────────
class TestPingVersion:
    def test_ping_reports_utils_version(self):
        import re

        from utils.__version__ import __version__

        src = Path("api/server.py").read_text(encoding="utf-8")
        assert '"version": "1.0.0"' not in src
        assert '"version": _app_version' in src
        assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)

    def test_pyproject_and_utils_version_agree(self):
        import re

        from utils.__version__ import __version__

        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
        assert m and m.group(1) == __version__
