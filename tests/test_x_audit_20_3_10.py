"""Regression tests for the 2026-09-28 platform download audit (v20.3.10) - X/Twitter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import infrastructure.downloader.yt_dlp_engine as yt_mod
from domain.models.download_task import DownloadTask, MediaInfo

# yt-dlp TwitterIE drops type=="photo" media and ends an image-only tweet with
# raise_no_formats('No video could be found in this tweet').
_X_PHOTO_ERR = "ERROR: [twitter] 1234567890: No video could be found in this tweet"
_X_URL = "https://x.com/someone/status/1234567890"


def _cfg():
    cfg = MagicMock(cookie_file="", use_cookies=False, proxy="", extra_args="", download_dir=Path("."))
    cfg.get_cookie_for_platform.return_value = ""
    return cfg


# BUG-X-PHOTO: an image-only tweet matched no photo signal, so analyse burned
# three retries and failed; gallery-dl (which supports X) was never tried.
def test_extract_info_routes_x_photo_tweet_to_gallery_dl():
    import yt_dlp as real_yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            raise real_yt_dlp.utils.DownloadError(_X_PHOTO_ERR)

    with (
        patch.object(yt_mod.yt_dlp, "YoutubeDL", FakeYDL),
        patch.object(yt_mod.time, "sleep"),
        patch(
            "infrastructure.downloader.gallery_dl_engine.GalleryDlEngine.extract_info",
            side_effect=RuntimeError("no network in tests"),
        ),
    ):
        info = yt_mod.YtDlpEngine(_cfg()).extract_info(_X_URL)

    assert info.source_engine == "gallery_dl"


def test_x_photo_message_is_not_a_photo_signal_elsewhere():
    import yt_dlp as real_yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def extract_info(self, url, download=False):
            raise real_yt_dlp.utils.DownloadError("ERROR: No video could be found in this tweet")

    with patch.object(yt_mod.yt_dlp, "YoutubeDL", FakeYDL), patch.object(yt_mod.time, "sleep"):
        with pytest.raises(RuntimeError):
            yt_mod.YtDlpEngine(_cfg()).extract_info("https://youtube.com/watch?v=abc")


def test_download_manager_falls_back_to_gallery_dl_for_x_photo_tweet():
    from infrastructure.downloader.download_manager import DownloadManager

    cfg = MagicMock(max_concurrent=1, max_retries=2, download_dir=Path("."))
    yt_engine = MagicMock()
    yt_engine.download.side_effect = RuntimeError(_X_PHOTO_ERR)
    gallery = MagicMock()
    manager = DownloadManager(config=cfg, engine=yt_engine, event_bus=MagicMock(), gallery_engine=gallery)

    task = DownloadTask(url=_X_URL)
    task.media_info = MediaInfo(url=_X_URL, title="Tweet", source_engine="yt_dlp")

    with patch("infrastructure.downloader.download_manager.time.sleep"):
        manager._run_task(task)

    gallery.download.assert_called_once()
    assert yt_engine.download.call_count == 1
