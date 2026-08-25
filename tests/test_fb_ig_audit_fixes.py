"""
Regression guards for the Facebook / Instagram audit fixes.

Each class maps to one issue found while auditing the Facebook and Instagram
features across the desktop app and the Remote Web API:

  FIX-FBIG-01  /api/download rejected source_engine="ig_cdn" (HTTP 422) even
               though /api/analyse returns exactly that value.
  FIX-FBIG-02  A pre-signed Instagram/Facebook CDN photo link was written with
               a hardcoded ".mp4" extension.
  FIX-FBIG-03  A Facebook photo-only post hard-failed during analyse instead of
               returning gallery-dl MediaInfo the way an Instagram one does.
  FIX-FBIG-04  Instagram Live re-capture kept the first URL's container format.
  FIX-FBIG-05  Every fb.watch link was treated as a Facebook Story permalink.
  FIX-FBIG-07  POST /api/monitor accepted any string as a URL.
  FIX-FBIG-08  A cookie for any platform unlocked Stories/Live for all of them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_CDN_VIDEO = "https://video-lhr8-1.xx.fbcdn.net/v/t66/1234_n.mp4?oh=aa&oe=bb"
_CDN_PHOTO = "https://scontent.cdninstagram.com/v/t51.2885-15/5678_n.jpg?oh=aa&oe=bb"


# ── FIX-FBIG-01 ───────────────────────────────────────────────────────────────
class TestIgCdnSourceEngineAccepted:
    def test_ig_cdn_is_a_valid_download_source_engine(self):
        from api.models import DownloadRequest

        req = DownloadRequest(url=_CDN_VIDEO, source_engine="ig_cdn")
        assert req.source_engine == "ig_cdn"

    def test_every_engine_analyse_can_return_is_accepted(self):
        """No AnalyseResponse.source_engine value may 422 on echo-back."""
        from api.models import DownloadRequest

        engines = (
            "yt_dlp",
            "gallery_dl",
            "kuaishou",
            "instagram_live",
            "waaw",
            "facebook_story",
            "ig_cdn",
        )
        for engine in engines:
            assert DownloadRequest(url=_CDN_VIDEO, source_engine=engine).source_engine == engine

    def test_unknown_engine_still_rejected(self):
        from api.models import DownloadRequest

        with pytest.raises(ValueError):
            DownloadRequest(url=_CDN_VIDEO, source_engine="totally_made_up")


# ── FIX-FBIG-02 ───────────────────────────────────────────────────────────────
class TestCdnExtensionFollowsUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_CDN_VIDEO, ".mp4"),
            (_CDN_PHOTO, ".jpg"),
            ("https://scontent.cdninstagram.com/v/t51/a.webp?oh=a&oe=b", ".webp"),
            ("https://scontent.cdninstagram.com/v/t51/a.heic?oh=a&oe=b", ".heic"),
            # Unknown/absent extension falls back to video, the common case.
            ("https://scontent.cdninstagram.com/v/t51/noext?oh=a&oe=b", ".mp4"),
            ("https://scontent.cdninstagram.com/v/t51/a.bin?oh=a&oe=b", ".mp4"),
        ],
    )
    def test_ext_for(self, url, expected):
        from infrastructure.downloader.instagram_cdn_engine import _ext_for

        assert _ext_for(url) == expected

    def test_photo_link_is_written_as_an_image(self, tmp_path):
        from infrastructure.downloader.instagram_cdn_engine import download_ig_cdn_url

        payload = b"\xff\xd8\xff\xe0" + b"x" * 4096  # JPEG magic + filler
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-length": str(len(payload))}
        resp.iter_content.return_value = [payload]
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False

        with patch("requests.get", return_value=resp):
            dest = download_ig_cdn_url(url=_CDN_PHOTO, output_dir=tmp_path, filename_hint="shot")

        assert dest.suffix == ".jpg"
        assert dest.read_bytes() == payload
        assert not list(tmp_path.glob("*.part"))

    def test_duplicate_photo_keeps_the_image_extension(self, tmp_path):
        from infrastructure.downloader.instagram_cdn_engine import download_ig_cdn_url

        (tmp_path / "shot.jpg").write_bytes(b"already here")
        payload = b"\xff\xd8" + b"y" * 4096
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-length": str(len(payload))}
        resp.iter_content.return_value = [payload]
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False

        with patch("requests.get", return_value=resp):
            dest = download_ig_cdn_url(url=_CDN_PHOTO, output_dir=tmp_path, filename_hint="shot")

        assert dest.name == "shot (1).jpg"


# ── FIX-FBIG-05 ───────────────────────────────────────────────────────────────
class TestFacebookStoryPermalink:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/stories/122114830586207697/UzpfSVND/",
            "https://m.facebook.com/stories/999888777666/hash",
            "https://facebook.com/stories/111",
        ],
    )
    def test_real_permalinks(self, url):
        from infrastructure.downloader.facebook_story_engine import is_facebook_story_permalink

        assert is_facebook_story_permalink(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://fb.watch/abc123",
            "https://fb.watch/xyz/",
            "https://www.facebook.com/watch/?v=123",
            "https://www.facebook.com/reel/456",
        ],
    )
    def test_fb_watch_and_plain_videos_are_not_permalinks(self, url):
        from infrastructure.downloader.facebook_story_engine import is_facebook_story_permalink

        assert is_facebook_story_permalink(url) is False

    def test_broad_matcher_still_covers_fb_watch(self):
        """is_facebook_story_url keeps its old, wider contract for callers
        that only use it to decide whether to *try* the CDP engine."""
        from infrastructure.downloader.facebook_story_engine import is_facebook_story_url

        assert is_facebook_story_url("https://fb.watch/abc123") is True

    def test_api_download_no_longer_rejects_fb_watch_off_windows(self):
        """The Linux/BSD guard must fire for permalinks only."""
        from infrastructure.downloader.facebook_story_engine import is_facebook_story_permalink

        assert is_facebook_story_permalink("https://fb.watch/abc123") is False
        assert is_facebook_story_permalink("https://www.facebook.com/stories/1/2/") is True


# ── FIX-FBIG-07 ───────────────────────────────────────────────────────────────
class TestMonitorUrlValidation:
    def test_plain_string_rejected(self):
        from api.models import MonitorAddRequest

        with pytest.raises(ValueError):
            MonitorAddRequest(url="definitely not a url")

    def test_instagram_profile_accepted(self):
        from api.models import MonitorAddRequest

        assert MonitorAddRequest(url="https://www.instagram.com/someuser/").url == (
            "https://www.instagram.com/someuser/"
        )

    def test_url_extracted_from_share_text(self):
        from api.models import MonitorAddRequest

        req = MonitorAddRequest(url="watch this https://www.instagram.com/someuser/ now")
        assert req.url == "https://www.instagram.com/someuser/"


# ── FIX-FBIG-03 ───────────────────────────────────────────────────────────────
class _FakeYDL:
    """YoutubeDL stand-in that always raises the photo-only error."""

    def __init__(self, opts):
        self._opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_post_processor(self, pp, when=None):
        pass

    def extract_info(self, url, download=False):
        import yt_dlp

        raise yt_dlp.utils.DownloadError("ERROR: [facebook] 123: There is no video in this post")


def _photo_config(tmp_path):
    cfg = MagicMock()
    cfg.extra_args = ""
    cfg.proxy = ""
    cfg.cookie_file = ""
    cfg.use_cookies = False
    cfg.cookies_browser = "chrome"
    cfg.max_retries = 3
    cfg.platform_cookies = {}
    cfg.get_cookie_for_platform.return_value = ""
    cfg.download_dir = tmp_path
    return cfg


class TestPhotoOnlyPostAnalyse:
    def _analyse(self, url, tmp_path):
        import infrastructure.downloader.yt_dlp_engine as mod

        cfg = _photo_config(tmp_path)
        cfg.cookie_file = "C:/cookies/all.txt"  # past the Stories/Live cookie gate
        engine = mod.YtDlpEngine(cfg)
        with patch.object(mod.yt_dlp, "YoutubeDL", _FakeYDL), patch.object(mod.time, "sleep"):
            return engine.extract_info(url)

    def test_facebook_photo_post_returns_gallery_dl_mediainfo(self, tmp_path):
        """Previously this raised — so the task could never be enqueued and
        DownloadManager's gallery-dl photo fallback was unreachable."""
        info = self._analyse("https://www.facebook.com/photo?fbid=123&set=a.456", tmp_path)

        assert info.source_engine == "gallery_dl"
        assert info.platform == "Facebook"
        assert info.formats == []
        assert info.duration == 0
        assert info.is_live is False

    def test_facebook_user_post_returns_gallery_dl_mediainfo(self, tmp_path):
        info = self._analyse("https://www.facebook.com/someuser/posts/pfbid0abc", tmp_path)
        assert info.source_engine == "gallery_dl"
        assert info.platform == "Facebook"

    def test_instagram_photo_post_still_carries_its_shortcode(self, tmp_path):
        info = self._analyse("https://www.instagram.com/p/DcDhOyLBG2R/", tmp_path)

        assert info.source_engine == "gallery_dl"
        assert info.platform == "Instagram"
        assert info.title == "DcDhOyLBG2R"
        assert info.video_id == "DcDhOyLBG2R"

    def test_instagram_story_photo_also_routes_to_gallery_dl(self, tmp_path):
        info = self._analyse("https://www.instagram.com/stories/someuser/123/", tmp_path)
        assert info.source_engine == "gallery_dl"
        assert info.platform == "Instagram"

    def test_non_meta_platform_still_raises(self, tmp_path):
        """A photo-only error off Instagram/Facebook has no gallery-dl route."""
        with pytest.raises(RuntimeError):
            self._analyse("https://www.youtube.com/watch?v=abc", tmp_path)


# ── FIX-FBIG-08 ───────────────────────────────────────────────────────────────
class TestPerPlatformCookieGate:
    def _msg(self, url, cfg, tmp_path):
        import infrastructure.downloader.yt_dlp_engine as mod

        captured = {}

        def _spy(u, has_cookies=False):
            captured["has_cookies"] = has_cookies
            return "blocked"

        engine = mod.YtDlpEngine(cfg)
        with patch.object(mod, "_check_unsupported_url", side_effect=_spy):
            with pytest.raises(RuntimeError):
                engine.extract_info(url)
        return captured["has_cookies"]

    def test_youtube_cookie_does_not_unlock_instagram_stories(self, tmp_path):
        cfg = _photo_config(tmp_path)
        cfg.platform_cookies = {"youtube": "C:/cookies/yt.txt"}
        cfg.get_cookie_for_platform.side_effect = lambda k: cfg.platform_cookies.get(k, "")

        assert self._msg("https://www.instagram.com/stories/u/1/", cfg, tmp_path) is False

    def test_instagram_cookie_unlocks_instagram_stories(self, tmp_path):
        cfg = _photo_config(tmp_path)
        cfg.platform_cookies = {"instagram": "C:/cookies/ig.txt"}
        cfg.get_cookie_for_platform.side_effect = lambda k: cfg.platform_cookies.get(k, "")

        assert self._msg("https://www.instagram.com/stories/u/1/", cfg, tmp_path) is True

    def test_facebook_cookie_unlocks_facebook_live(self, tmp_path):
        cfg = _photo_config(tmp_path)
        cfg.platform_cookies = {"facebook": "C:/cookies/fb.txt"}
        cfg.get_cookie_for_platform.side_effect = lambda k: cfg.platform_cookies.get(k, "")

        assert self._msg("https://www.facebook.com/live/xyz", cfg, tmp_path) is True

    def test_global_cookie_file_still_unlocks_everything(self, tmp_path):
        cfg = _photo_config(tmp_path)
        cfg.cookie_file = "C:/cookies/all.txt"

        assert self._msg("https://www.instagram.com/stories/u/1/", cfg, tmp_path) is True

    def test_browser_cookies_still_unlock_everything(self, tmp_path):
        cfg = _photo_config(tmp_path)
        cfg.use_cookies = True

        assert self._msg("https://www.facebook.com/live/xyz", cfg, tmp_path) is True


# ── FIX-FBIG-04 ───────────────────────────────────────────────────────────────
class TestInstagramLiveFormatSwitch:
    """The resume loop re-captures a fresh stream URL; is_dash must follow it.

    Before the fix is_dash was frozen at the first capture, so after a
    DASH -> HLS switch FFmpeg was still handed `-f matroska -map 0:v:0`, and
    after an HLS -> DASH switch it got `-f mpegts` with no -map at all (which
    writes zero bytes and reads back as "the stream ended").
    """

    def _run(self, tmp_path, captures):
        import infrastructure.downloader.instagram_live_engine as mod
        from domain.models.download_task import DownloadTask, MediaInfo

        cfg = MagicMock()
        cfg.download_dir = tmp_path
        engine = mod.InstagramLiveEngine(cfg)

        task = DownloadTask(url="https://www.instagram.com/someuser/live/", format_id="best", output_ext="ts")
        task.media_info = MediaInfo(url=task.url, title="live", source_engine="instagram_live")
        task.output_dir = str(tmp_path)

        seen: list[bool] = []
        seq = list(captures)

        def _fake_capture(_task, _url, _username, _timeout):
            return (seq.pop(0), {}) if seq else (None, {})

        def _fake_record(_task, hls_url, _hdrs, _url, out_path, is_dash, *_a, **_kw):
            seen.append(is_dash)
            out_path.write_bytes(b"z" * 400_000)
            return "stall", 400_000

        loc = MagicMock()
        loc.ffmpeg_bin = "ffmpeg"
        with (
            patch.object(engine, "_capture_stream_url", side_effect=_fake_capture),
            patch.object(engine, "_record_segment", side_effect=_fake_record),
            patch.object(mod, "_concat_parts") as concat,
            patch("utils.ffmpeg_locator.locate_ffmpeg", return_value=loc),
        ):
            engine.download(task)
        return seen, task, concat

    def test_dash_then_hls_records_each_part_with_its_own_args(self, tmp_path):
        seen, task, _ = self._run(
            tmp_path,
            ["https://x.fbcdn.net/live.mpd", "https://x.fbcdn.net/live.m3u8", None],
        )
        assert seen == [True, False]

    def test_hls_then_dash_records_each_part_with_its_own_args(self, tmp_path):
        seen, task, _ = self._run(
            tmp_path,
            ["https://x.fbcdn.net/live.m3u8", "https://x.fbcdn.net/live.mpd", None],
        )
        assert seen == [False, True]

    def test_mixed_format_recording_is_finalised_as_mkv(self, tmp_path):
        _seen, task, concat = self._run(
            tmp_path,
            ["https://x.fbcdn.net/live.mpd", "https://x.fbcdn.net/live.m3u8", None],
        )
        assert Path(task.filename).suffix == ".mkv"
        assert concat.call_args[0][2] == Path(task.filename)

    def test_single_format_recording_keeps_its_original_container(self, tmp_path):
        seen, task, _ = self._run(
            tmp_path,
            ["https://x.fbcdn.net/live.m3u8", "https://x.fbcdn.net/live2.m3u8", None],
        )
        assert seen == [False, False]
        assert Path(task.filename).suffix == ".ts"

    def test_dash_only_recording_keeps_mkv(self, tmp_path):
        seen, task, _ = self._run(
            tmp_path,
            ["https://x.fbcdn.net/live.mpd", "https://x.fbcdn.net/live2.mpd", None],
        )
        assert seen == [True, True]
        assert Path(task.filename).suffix == ".mkv"
