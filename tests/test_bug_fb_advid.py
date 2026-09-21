"""
BUG-FB-ADVID: a Facebook photo post must never come back as the advert video
that Facebook injects into the same post page.

Log evidence (omnidl_debug.log, 2026-09-20 09:05:44 → 09:06:01, task a09e9e9f):
post 1750153570452113 owned by 100063724590889 was delivered as
"คุณชายดำ ต๊วดงัด đã thêm  [1767163571189463].mp4".
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yt_dlp

import infrastructure.downloader.gallery_dl_engine as gdl_mod
from domain.models.download_task import DownloadTask, MediaInfo

# ---------------------------------------------------------------------------
# facebook_owner_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.facebook.com/100063724590889/posts/1750153570452113", "100063724590889"),
        (
            "https://www.facebook.com/story.php?story_fbid=1750153570452113&id=100063724590889",
            "100063724590889",
        ),
        # A group post puts the group id where the owner id would be.
        ("https://www.facebook.com/groups/123456/posts/999", ""),
        # A vanity owner cannot be compared against a numeric uploader_id.
        ("https://www.facebook.com/somepage/posts/999", ""),
        ("https://www.instagram.com/p/ABCdef123/", ""),
    ],
)
def test_facebook_owner_id(url, expected):
    assert gdl_mod.facebook_owner_id(url) == expected


# ---------------------------------------------------------------------------
# the match_filter handed to yt-dlp
# ---------------------------------------------------------------------------


def _capture_opts(monkeypatch, **kwargs):
    """Run the rescue pass against a stubbed yt-dlp and return the opts it built."""
    captured = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            return 0

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    gdl_mod._ytdlp_carousel_videos(
        url="https://www.facebook.com/100063724590889/posts/1750153570452113",
        output_dir=Path(kwargs.pop("output_dir")),
        dl_start_ts=0.0,
        **kwargs,
    )
    return captured


def test_no_owner_id_means_no_filter(tmp_path, monkeypatch):
    opts = _capture_opts(monkeypatch, output_dir=tmp_path)
    assert "match_filter" not in opts


def test_foreign_uploader_is_rejected(tmp_path, monkeypatch):
    opts = _capture_opts(monkeypatch, output_dir=tmp_path, owner_id="100063724590889")
    mf = opts["match_filter"]

    # The advert from the log: a different numeric page owns it.
    assert mf({"id": "1767163571189463", "uploader_id": "61551234567890"})
    # The post's own video is kept.
    assert mf({"id": "1750153570452113", "uploader_id": "100063724590889"}) is None
    # An opaque profile id cannot be compared — keep it rather than lose a video.
    assert mf({"id": "1", "uploader_id": "pfbid028xue38TBXRyNbiqBSV2LFs"}) is None
    # Nothing to judge yet while the entry is still a bare url_result.
    assert mf({"id": "1", "uploader_id": "61551234567890"}, incomplete=True) is None
    assert mf({"id": "1"}) is None


# ---------------------------------------------------------------------------
# download() wiring
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, returncode=1):
        self.returncode = returncode
        self.stdout = iter(())
        self.stderr = iter(())

    def wait(self):
        return self.returncode

    def kill(self):
        pass


def _rescue_kwargs_for(url, tmp_path, monkeypatch, *, rescued=()):
    cfg = MagicMock(proxy="", download_dir=tmp_path)
    engine = gdl_mod.GalleryDlEngine(cfg)
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    monkeypatch.setattr(gdl_mod.subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(gdl_mod.threading, "Thread", lambda **k: MagicMock())
    rescue = MagicMock(return_value=list(rescued))
    monkeypatch.setattr(gdl_mod, "_ytdlp_carousel_videos", rescue)

    task = DownloadTask(url=url, output_dir=str(tmp_path))
    task.media_info = MediaInfo(url=url, title="Post", source_engine="gallery_dl")
    try:
        engine.download(task)
    except RuntimeError:
        pass  # both engines empty — expected for the advert case
    return rescue


def test_facebook_post_passes_owner_id_to_the_rescue_pass(tmp_path, monkeypatch):
    rescue = _rescue_kwargs_for(
        "https://www.facebook.com/story.php?story_fbid=1750153570452113&id=100063724590889",
        tmp_path,
        monkeypatch,
    )
    assert rescue.call_args.kwargs["owner_id"] == "100063724590889"


def test_instagram_carousel_gets_no_owner_filter(tmp_path, monkeypatch):
    rescue = _rescue_kwargs_for(
        "https://www.instagram.com/p/ABCdef123/",
        tmp_path,
        monkeypatch,
        rescued=[str(tmp_path / "clip.mp4")],
    )
    assert rescue.call_args.kwargs["owner_id"] == ""


def test_photo_post_that_yields_only_an_advert_fails_instead_of_succeeding(tmp_path, monkeypatch):
    """gallery-dl found no photos and the filter dropped the advert — that is a failure."""
    cfg = MagicMock(proxy="", download_dir=tmp_path)
    engine = gdl_mod.GalleryDlEngine(cfg)
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    monkeypatch.setattr(gdl_mod.subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(gdl_mod.threading, "Thread", lambda **k: MagicMock())
    monkeypatch.setattr(gdl_mod, "_ytdlp_carousel_videos", MagicMock(return_value=[]))

    url = "https://www.facebook.com/100063724590889/posts/1750153570452113"
    task = DownloadTask(url=url, output_dir=str(tmp_path))
    task.media_info = MediaInfo(url=url, title="Post", source_engine="gallery_dl")
    with pytest.raises(RuntimeError):
        engine.download(task)


# ---------------------------------------------------------------------------
# facebook_story_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.facebook.com/100063724590889/posts/1750153570452113", "1750153570452113"),
        (
            "https://www.facebook.com/story.php?story_fbid=122230506620352435&id=61560573071079",
            "122230506620352435",
        ),
        ("https://www.facebook.com/groups/123456/posts/999", "999"),
        # An opaque story token is not numeric and must not be used as a filter.
        (
            "https://www.facebook.com/permalink.php?story_fbid=pfbid0fqQuVEQyXRa&id=100068861234290",
            "",
        ),
        ("https://www.facebook.com/cnn/videos/10155529876156509/", ""),
    ],
)
def test_facebook_story_id(url, expected):
    assert gdl_mod.facebook_story_id(url) == expected


# ---------------------------------------------------------------------------
# strict_story_id — the lone-entry case the owner filter cannot judge
# ---------------------------------------------------------------------------


def test_strict_story_id_rejects_the_advert_that_inherits_the_post_owner(tmp_path, monkeypatch):
    """merge_dicts stamps the post owner on a single result, so only the id helps."""
    opts = _capture_opts(
        monkeypatch,
        output_dir=tmp_path,
        owner_id="100063724590889",
        strict_story_id="1750153570452113",
    )
    mf = opts["match_filter"]

    # The advert from the log, wearing the post owner's uploader_id.
    assert mf({"id": "1767163571189463", "uploader_id": "100063724590889"})
    # The requested story's own video survives.
    assert mf({"id": "1750153570452113", "uploader_id": "100063724590889"}) is None
    assert mf({"id": "1767163571189463"}, incomplete=True) is None


def test_no_image_saved_switches_the_rescue_pass_to_strict(tmp_path, monkeypatch):
    rescue = _rescue_kwargs_for(
        "https://www.facebook.com/story.php?story_fbid=1750153570452113&id=100063724590889",
        tmp_path,
        monkeypatch,
    )
    assert rescue.call_args.kwargs["strict_story_id"] == "1750153570452113"


def test_instagram_carousel_never_goes_strict(tmp_path, monkeypatch):
    rescue = _rescue_kwargs_for(
        "https://www.instagram.com/p/ABCdef123/",
        tmp_path,
        monkeypatch,
        rescued=[str(tmp_path / "clip.mp4")],
    )
    assert rescue.call_args.kwargs["strict_story_id"] == ""


# ---------------------------------------------------------------------------
# analyse-side guard (desktop and Web API share this code path)
# ---------------------------------------------------------------------------


def _analyse(monkeypatch, url, info, gdl_result):
    """Run YtDlpEngine.extract_info against a stubbed yt-dlp and gallery-dl."""
    import infrastructure.downloader.yt_dlp_engine as ydl_mod

    class _FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return info

    monkeypatch.setattr(ydl_mod.yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(ydl_mod, "_resolve_cookie", lambda *a, **k: None)
    monkeypatch.setattr(ydl_mod, "get_ffmpeg_path", lambda: None)

    class _FakeGdl:
        def __init__(self, config):
            pass

        def extract_info(self, url):
            if isinstance(gdl_result, Exception):
                raise gdl_result
            return gdl_result

    monkeypatch.setattr(gdl_mod, "GalleryDlEngine", _FakeGdl)

    cfg = MagicMock(proxy="", use_cookies=False, cookies_browser="brave")
    return ydl_mod.YtDlpEngine(cfg).extract_info(url)


_FB_PHOTO_POST = "https://www.facebook.com/story.php?story_fbid=122230506620352435&id=61560573071079"


def test_analyse_prefers_the_photo_set_over_an_injected_advert(monkeypatch):
    photos = MediaInfo(
        url=_FB_PHOTO_POST,
        title="5 ảnh",
        uploader="NET Việt Nam",
        source_engine="gallery_dl",
    )
    out = _analyse(
        monkeypatch,
        _FB_PHOTO_POST,
        {"id": "1767163571189463", "uploader_id": "61560573071079", "formats": []},
        photos,
    )
    assert out.source_engine == "gallery_dl"
    assert out.title == "5 ảnh"


def test_analyse_keeps_the_video_when_the_post_has_no_photo_set(monkeypatch):
    out = _analyse(
        monkeypatch,
        _FB_PHOTO_POST,
        {"id": "1767163571189463", "uploader_id": "61560573071079", "formats": []},
        RuntimeError("gallery-dl found no content"),
    )
    assert out.source_engine == "yt_dlp"
    assert out.video_id == "1767163571189463"


def test_analyse_skips_the_probe_when_the_video_is_the_requested_story(monkeypatch):
    def _boom(config):
        raise AssertionError("gallery-dl must not be consulted")

    monkeypatch.setattr(gdl_mod, "GalleryDlEngine", _boom)
    out = _analyse(
        monkeypatch,
        _FB_PHOTO_POST,
        {"id": "122230506620352435", "uploader_id": "61560573071079", "formats": []},
        None,
    )
    assert out.source_engine == "yt_dlp"


# ---------------------------------------------------------------------------
# BUG-FB-GROUPPOST
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/100063724590889/posts/1750153570452113",
        "https://www.facebook.com/groups/1024490957622648/posts/1396382447100162",
        "https://www.facebook.com/story.php?story_fbid=1750153570452113&id=100063724590889",
    ],
)
def test_group_posts_are_recognised_as_facebook_posts(url):
    assert gdl_mod.is_facebook_post_url(url)


# ---------------------------------------------------------------------------
# BUG-FB-PCB: the candidate URL forms handed to gallery-dl
# ---------------------------------------------------------------------------


_PCB_STORY = "https://www.facebook.com/story.php?story_fbid=122182130744968412&id=61579052360106"
_PCB_SET = "https://www.facebook.com/media/set/?set=pcb.122182130744968412"
_PCB_POSTS = "https://www.facebook.com/61579052360106/posts/122182130744968412"


def test_photo_set_form_is_tried_before_the_posts_form():
    assert gdl_mod.gallery_dl_url_candidates(_PCB_STORY) == [_PCB_SET, _PCB_POSTS]
    assert gdl_mod.gallery_dl_url_candidates(_PCB_POSTS) == [_PCB_SET, _PCB_POSTS]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/ABCdef123/",
        "https://www.facebook.com/photo/?fbid=1234567890",
        "https://www.facebook.com/watch/?v=1234567890",
        # An opaque story token gives no numeric set id to build from.
        "https://www.facebook.com/permalink.php?story_fbid=pfbid0fqQuVEQyXRa&id=100068861234290",
    ],
)
def test_urls_without_a_numeric_story_get_a_single_candidate(url):
    assert gdl_mod.gallery_dl_url_candidates(url) == [url]


def _run_extract_info(monkeypatch, tmp_path, outputs):
    """Stub gallery-dl so each candidate URL returns its own --dump-json text."""
    calls: list[str] = []

    class _Result:
        def __init__(self, stdout):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0 if stdout else 1

    def _fake_run(cmd, **kwargs):
        calls.append(cmd[-1])
        return _Result(outputs.get(cmd[-1], ""))

    monkeypatch.setattr(gdl_mod.subprocess, "run", _fake_run)
    engine = gdl_mod.GalleryDlEngine(MagicMock(proxy="", download_dir=tmp_path))
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    return engine, calls


def _dump_line(photo_id):
    import json

    return json.dumps([1, f"https://cdn/{photo_id}.jpg", {"id": photo_id, "username": "Mì Kokomi"}])


def test_extract_info_falls_back_to_the_posts_form(tmp_path, monkeypatch):
    engine, calls = _run_extract_info(monkeypatch, tmp_path, {_PCB_POSTS: _dump_line("122182130522968412")})
    info = engine.extract_info(_PCB_STORY)
    assert calls == [_PCB_SET, _PCB_POSTS]
    assert info.source_engine == "gallery_dl"
    assert info.video_id == "122182130522968412"


def test_extract_info_stops_at_the_first_form_that_works(tmp_path, monkeypatch):
    engine, calls = _run_extract_info(monkeypatch, tmp_path, {_PCB_SET: _dump_line("122182130522968412")})
    info = engine.extract_info(_PCB_STORY)
    assert calls == [_PCB_SET]
    assert info.uploader == "Mì Kokomi"


def test_extract_info_raises_when_no_form_works(tmp_path, monkeypatch):
    engine, calls = _run_extract_info(monkeypatch, tmp_path, {})
    with pytest.raises(RuntimeError):
        engine.extract_info(_PCB_STORY)
    assert calls == [_PCB_SET, _PCB_POSTS]


def test_download_hands_gallery_dl_every_candidate(tmp_path, monkeypatch):
    captured: list[list[str]] = []

    def _fake_popen(cmd, **kwargs):
        captured.append(cmd)
        return _FakeProc()

    cfg = MagicMock(proxy="", download_dir=tmp_path)
    engine = gdl_mod.GalleryDlEngine(cfg)
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    monkeypatch.setattr(gdl_mod.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(gdl_mod.threading, "Thread", lambda **k: MagicMock())
    monkeypatch.setattr(gdl_mod, "_ytdlp_carousel_videos", MagicMock(return_value=[]))

    task = DownloadTask(url=_PCB_STORY, output_dir=str(tmp_path))
    task.media_info = MediaInfo(url=_PCB_STORY, title="Post", source_engine="gallery_dl")
    with pytest.raises(RuntimeError):
        engine.download(task)

    assert captured[0][-2:] == [_PCB_SET, _PCB_POSTS]


# ---------------------------------------------------------------------------
# BUG-FB-ADVID-2: gallery-dl silent + a foreign owner must not yield an advert
# ---------------------------------------------------------------------------


def test_analyse_refuses_a_foreign_owner_when_gallery_dl_cannot_confirm(monkeypatch):
    with pytest.raises(Exception) as excinfo:
        _analyse(
            monkeypatch,
            _FB_PHOTO_POST,
            # 61551234567890 is not the post owner 61560573071079.
            {"id": "1789050085345855", "uploader_id": "61551234567890", "formats": []},
            RuntimeError("gallery-dl found no content"),
        )
    assert "quảng cáo" in str(excinfo.value) or "advert" in str(excinfo.value)
