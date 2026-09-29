"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - Facebook."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from infrastructure.downloader.facebook_story_engine import _download_cdn_url


# BUG-FB-STORY-TRUNC: a network error while streaming escaped _download_cdn_url
# and left a truncated mp4; the caller's fallbacks (which only run on a returned
# None) never ran and every retry wrote one more broken file.
def test_story_cdn_stream_error_drops_the_partial_file_and_returns_none(tmp_path):
    def _broken_chunks(chunk_size):
        yield b"x" * 4096
        raise requests.exceptions.ChunkedEncodingError("connection reset")

    resp = MagicMock()
    resp.headers = {"content-length": "1000000"}
    resp.raise_for_status = lambda: None
    resp.iter_content = _broken_chunks
    head = MagicMock()
    head.headers = {"content-length": "1000000"}
    dest = tmp_path / "fb_story_1.mp4"

    with patch("requests.head", return_value=head), patch("requests.get", return_value=resp):
        result = _download_cdn_url("https://video.fbcdn.net/v/clip.mp4?oh=1", dest, None)

    assert result is None
    assert not dest.exists()


def _fb_manager(formats):
    from pathlib import Path

    from domain.models.download_task import DownloadTask, MediaInfo
    from infrastructure.downloader.download_manager import DownloadManager

    url = "https://www.facebook.com/somepage/videos/1234567890"
    cfg = MagicMock(max_concurrent=1, max_retries=2, download_dir=Path("."))
    yt_engine = MagicMock()
    yt_engine.download.side_effect = RuntimeError("ERROR: [facebook] 1234567890: Cannot parse data")
    gallery = MagicMock()
    manager = DownloadManager(config=cfg, engine=yt_engine, event_bus=MagicMock(), gallery_engine=gallery)
    task = DownloadTask(url=url)
    task.media_info = MediaInfo(url=url, title="clip", source_engine="yt_dlp", formats=formats)
    with patch("infrastructure.downloader.download_manager.time.sleep"):
        manager._run_task(task)
    return yt_engine, gallery


# BUG-FB-PARSE-VIDEO: "Cannot parse data" is the photo-post signal, but analyse
# had already found video formats here; the same text is also a transient
# FacebookIE failure that a retry recovers from (task c6e5e53b, 2026-09-09).
def test_cannot_parse_data_on_an_analysed_video_is_retried_not_sent_to_gallery_dl():
    yt_engine, gallery = _fb_manager(formats=[{"format_id": "hd"}])
    assert yt_engine.download.call_count == 3
    gallery.download.assert_not_called()


def test_cannot_parse_data_without_formats_still_falls_back_to_gallery_dl():
    yt_engine, gallery = _fb_manager(formats=[])
    assert yt_engine.download.call_count == 1
    gallery.download.assert_called_once()
