"""Facebook feed posts that hold photos, photos + music or photos + video.

Regression guards for BUG-FB-PARSE and BUG-FB-POST (v20.3.0):

* yt-dlp's FacebookIE ends a photo-only post at ``ExtractorError('Cannot parse
  data')`` — not at any of the Instagram photo messages the fallbacks matched —
  so analyse burned three retries and failed the post outright.
* gallery-dl has no extractor for ``/share/p/<token>``; the resolution to the
  canonical URL used to happen only inside ``yt_dlp_engine.extract_info``.
* gallery-dl's ``FacebookSetExtractor`` yields the photos of a post and skips
  its video items, so a mixed post needs a yt-dlp pass afterwards.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import infrastructure.downloader.gallery_dl_engine as gdl_mod
import infrastructure.downloader.yt_dlp_engine as yt_mod
from app.services.download_service import _should_fallback_to_gallery_dl
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.gallery_dl_engine import (
    is_facebook_post_url,
    normalize_gallery_dl_url,
)

_POST_URLS = [
    "https://www.facebook.com/story.php?story_fbid=1693898632398361&id=100053347221935",
    "https://www.facebook.com/permalink.php?story_fbid=123&id=456",
    "https://www.facebook.com/100053347221935/posts/1693898632398361",
    "https://www.facebook.com/somepage/posts/pfbid02AbCdEf",
    "https://www.facebook.com/share/p/1FfXYiEjX8/?mibextid=wwXIfr",
]

_NON_POST_URLS = [
    "https://www.facebook.com/someuser/videos/123456789",
    "https://www.facebook.com/watch/?v=123456789",
    "https://www.facebook.com/stories/1538677989539155/Uzpf/",
    "https://www.instagram.com/p/ABC123/",
]


@pytest.mark.parametrize("url", _POST_URLS)
def test_facebook_post_urls_detected(url):
    assert is_facebook_post_url(url) is True


@pytest.mark.parametrize("url", _NON_POST_URLS)
def test_non_post_urls_not_detected(url):
    assert is_facebook_post_url(url) is False


# ---------------------------------------------------------------------------
# BUG-FB-SHARE: /share/p/ must be resolved before gallery-dl sees it
# ---------------------------------------------------------------------------


def test_normalize_resolves_share_link_then_rewrites_story_php():
    share = "https://www.facebook.com/share/p/1FfXYiEjX8/"
    canonical = "https://www.facebook.com/story.php?story_fbid=1693898632398361&id=100053347221935"

    with patch.object(yt_mod, "_resolve_facebook_share_url", return_value=canonical) as resolve:
        out = normalize_gallery_dl_url(share)

    resolve.assert_called_once_with(share)
    assert out == "https://www.facebook.com/100053347221935/posts/1693898632398361"


def test_normalize_leaves_non_share_urls_untouched():
    url = "https://www.instagram.com/p/ABC123/"
    with patch.object(yt_mod, "_resolve_facebook_share_url") as resolve:
        assert normalize_gallery_dl_url(url) == url
    resolve.assert_not_called()


# ---------------------------------------------------------------------------
# BUG-FB-PARSE: "Cannot parse data" is Facebook's photo-only signal
# ---------------------------------------------------------------------------


def test_extract_info_returns_gallery_dl_media_info_for_cannot_parse_data():
    import yt_dlp as real_yt_dlp

    cfg = MagicMock(
        cookie_file="",
        use_cookies=False,
        proxy="",
        extra_args="",
        download_dir=Path("/tmp"),  # nosec B108
    )
    cfg.get_cookie_for_platform.return_value = ""

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            raise real_yt_dlp.utils.DownloadError("ERROR: [facebook] 169389: Cannot parse data")

    url = "https://www.facebook.com/100053347221935/posts/1693898632398361"
    with patch.object(yt_mod.yt_dlp, "YoutubeDL", FakeYDL):
        info = yt_mod.YtDlpEngine(cfg).extract_info(url)

    assert info.source_engine == "gallery_dl"
    assert info.formats == []


def test_cannot_parse_data_still_fails_for_non_facebook():
    """Only Facebook maps this message to a photo post — never YouTube."""
    import yt_dlp as real_yt_dlp

    cfg = MagicMock(
        cookie_file="",
        use_cookies=False,
        proxy="",
        extra_args="",
        download_dir=Path("/tmp"),  # nosec B108
    )
    cfg.get_cookie_for_platform.return_value = ""

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            raise real_yt_dlp.utils.DownloadError("ERROR: Cannot parse data")

    with patch.object(yt_mod.yt_dlp, "YoutubeDL", FakeYDL), patch.object(yt_mod.time, "sleep"):
        with pytest.raises(RuntimeError):
            yt_mod.YtDlpEngine(cfg).extract_info("https://youtube.com/watch?v=abc")


def test_analyse_fallback_accepts_cannot_parse_data_for_facebook():
    assert (
        _should_fallback_to_gallery_dl(
            "https://www.facebook.com/story.php?story_fbid=1&id=2",
            "ERROR: [facebook] 1: Cannot parse data",
        )
        is True
    )


def test_analyse_fallback_rejects_cannot_parse_data_for_other_hosts():
    assert (
        _should_fallback_to_gallery_dl(
            "https://www.instagram.com/p/ABC123/",
            "ERROR: Cannot parse data",
        )
        is False
    )


def test_download_manager_falls_back_to_gallery_dl_on_cannot_parse_data():
    from infrastructure.downloader.download_manager import DownloadManager

    cfg = MagicMock(max_concurrent=1, max_retries=1, download_dir=Path("/tmp"))  # nosec B108
    yt_engine = MagicMock()
    yt_engine.download.side_effect = RuntimeError("ERROR: [facebook] 1: Cannot parse data")
    gallery = MagicMock()
    manager = DownloadManager(
        config=cfg, engine=yt_engine, event_bus=MagicMock(), gallery_engine=gallery
    )

    url = "https://www.facebook.com/100053347221935/posts/1693898632398361"
    task = DownloadTask(url=url)
    task.media_info = MediaInfo(url=url, title="Post", source_engine="yt_dlp")

    manager._run_task(task)

    gallery.download.assert_called_once()
    assert yt_engine.download.call_count == 1  # no pointless yt-dlp retries


# ---------------------------------------------------------------------------
# BUG-FB-POST: photos come from gallery-dl, videos from the yt-dlp pass
# ---------------------------------------------------------------------------


class _FakeProc:
    """Minimal stand-in for the gallery-dl subprocess."""

    def __init__(self, returncode=0, stdout_lines=()):
        self.returncode = returncode
        self.stdout = iter(stdout_lines)
        self.stderr = iter(())

    def wait(self):
        return self.returncode

    def kill(self):
        pass


def _run_gallery_download(tmp_path, monkeypatch, *, image, returncode, rescued):
    cfg = MagicMock(proxy="", download_dir=tmp_path)
    engine = gdl_mod.GalleryDlEngine(cfg)

    stdout_lines = []
    if image is not None:
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"x" * 2000)
        stdout_lines.append(f"#:{image}\n")

    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    monkeypatch.setattr(
        gdl_mod.subprocess, "Popen", lambda *a, **k: _FakeProc(returncode, stdout_lines)
    )
    monkeypatch.setattr(gdl_mod.threading, "Thread", lambda **k: MagicMock())
    rescue = MagicMock(return_value=rescued)
    monkeypatch.setattr(gdl_mod, "_ytdlp_carousel_videos", rescue)

    url = "https://www.facebook.com/100053347221935/posts/1693898632398361"
    task = DownloadTask(url=url, output_dir=str(tmp_path))
    task.media_info = MediaInfo(url=url, title="Post", source_engine="gallery_dl")
    engine.download(task)
    return task, rescue


def test_facebook_post_runs_ytdlp_video_pass_after_photos(tmp_path, monkeypatch):
    video = tmp_path / "clip.mp4"
    task, rescue = _run_gallery_download(
        tmp_path,
        monkeypatch,
        image=tmp_path / "fb" / "photo.jpg",
        returncode=0,
        rescued=[str(video)],
    )
    rescue.assert_called_once()
    assert str(video) in task.gallery_dl_files
    assert any(f.endswith("photo.jpg") for f in task.gallery_dl_files)


def test_facebook_post_with_no_photo_set_still_keeps_the_video(tmp_path, monkeypatch):
    """A post gallery-dl cannot turn into a photo set is not a failure yet."""
    video = tmp_path / "clip.mp4"
    task, rescue = _run_gallery_download(
        tmp_path, monkeypatch, image=None, returncode=1, rescued=[str(video)]
    )
    rescue.assert_called_once()
    assert task.gallery_dl_files == [str(video)]


def test_facebook_post_raises_when_both_engines_find_nothing(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError):
        _run_gallery_download(tmp_path, monkeypatch, image=None, returncode=1, rescued=[])
