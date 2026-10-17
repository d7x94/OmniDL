"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - X/Twitter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import infrastructure.downloader.yt_dlp_engine as yt_mod
from domain.models.download_task import DownloadTask, MediaInfo

_PHOTO_URL = "https://x.com/someone/status/1234567890/photo/1"
# TwitterIE, URL with /photo/N and noplaylist=True: the photo is not a video.
_NOT_A_VIDEO = "ERROR: [twitter] 1234567890: Media #1 is not a video"
_NSFW = (
    "ERROR: [twitter] 1234567890: NSFW tweet requires authentication. Use --cookies-from-browser or "
    "--cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
    "for how to manually pass cookies"
)
_PROTECTED = "ERROR: [twitter] 1234567890: You are not authorized to view this protected tweet"


def _cfg():
    cfg = MagicMock(cookie_file="", use_cookies=False, proxy="", extra_args="", download_dir=Path("."))
    cfg.get_cookie_for_platform.return_value = ""
    return cfg


def _fake_ydl(message, calls):
    import yt_dlp as real_yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            calls.append(url)
            raise real_yt_dlp.utils.DownloadError(message)

    return FakeYDL


def _manager(engine_error):
    from infrastructure.downloader.download_manager import DownloadManager

    cfg = MagicMock(max_concurrent=1, max_retries=2, download_dir=Path("."))
    yt_engine = MagicMock()
    yt_engine.download.side_effect = RuntimeError(engine_error)
    gallery = MagicMock()
    manager = DownloadManager(config=cfg, engine=yt_engine, event_bus=MagicMock(), gallery_engine=gallery)
    task = DownloadTask(url=_PHOTO_URL)
    task.media_info = MediaInfo(url=_PHOTO_URL, title="Tweet", source_engine="yt_dlp")
    with patch("infrastructure.downloader.download_manager.time.sleep"):
        manager._run_task(task)
    return yt_engine, gallery, task


# BUG-X-PHOTO-N: a /photo/N URL (what X generates when an image is opened)
# ends in "Media #N is not a video", which matched no photo signal.
def test_extract_info_routes_x_photo_index_url_to_gallery_dl():
    calls: list[str] = []
    with (
        patch.object(yt_mod.yt_dlp, "YoutubeDL", _fake_ydl(_NOT_A_VIDEO, calls)),
        patch.object(yt_mod.time, "sleep"),
        patch(
            "infrastructure.downloader.gallery_dl_engine.GalleryDlEngine.extract_info",
            side_effect=RuntimeError("no network in tests"),
        ),
    ):
        info = yt_mod.YtDlpEngine(_cfg()).extract_info(_PHOTO_URL)
    assert info.source_engine == "gallery_dl"


def test_download_manager_falls_back_to_gallery_dl_for_x_photo_index_url():
    yt_engine, gallery, _task = _manager(_NOT_A_VIDEO)
    gallery.download.assert_called_once()
    assert yt_engine.download.call_count == 1


# BUG-X-AUTH: "requires authentication" / "not authorized" carry none of the
# hard-error keywords, so analyse and download retried a login wall 3 times.
def test_extract_info_does_not_retry_x_auth_wall():
    import pytest

    for message in (_NSFW, _PROTECTED):
        calls: list[str] = []
        with patch.object(yt_mod.yt_dlp, "YoutubeDL", _fake_ydl(message, calls)), patch.object(yt_mod.time, "sleep"):
            with pytest.raises(RuntimeError):
                yt_mod.YtDlpEngine(_cfg()).extract_info("https://x.com/someone/status/1234567890")
        assert len(calls) == 1, message


def test_download_manager_does_not_retry_x_auth_wall():
    for message in (_NSFW, _PROTECTED):
        yt_engine, gallery, task = _manager(message)
        assert yt_engine.download.call_count == 1, message
        gallery.download.assert_not_called()


# BUG-X-TCO: t.co is X's link shortener; yt-dlp resolves it, but OmniDL picked
# cookie, rate limiter and photo branch from the t.co host and found none.
def test_platform_for_url_maps_t_co_to_twitter():
    assert yt_mod.platform_for_url("https://t.co/AbCdEf1234") == "twitter"
    assert yt_mod.platform_for_url("https://not-t.co/AbCdEf1234") is None
