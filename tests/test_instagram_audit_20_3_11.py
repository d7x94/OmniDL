"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - Instagram."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from domain.models.download_task import DownloadTask
from infrastructure.downloader.instagram_cdn_engine import download_ig_cdn_url

_CDN_URL = "https://scontent.cdninstagram.com/v/t66.30100-16/clip.mp4?_nc_cat=1&oh=abc123&oe=64ABCDEF"


# BUG-IG-CDN-CANCEL: the stream loop had no cancel check, so Cancel on a pasted
# CDN link kept downloading until the whole file was written.
def test_ig_cdn_download_stops_when_cancelled_and_drops_the_part_file(tmp_path):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-length": "8192"}
    resp.raise_for_status = lambda: None
    resp.iter_content = lambda chunk_size: iter([b"x" * 2048] * 4)
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)

    with patch("requests.get", return_value=resp):
        with pytest.raises(RuntimeError, match="(?i)cancelled by user"):
            download_ig_cdn_url(
                url=_CDN_URL,
                output_dir=tmp_path,
                filename_hint="clip",
                should_cancel=lambda: True,
            )

    assert list(tmp_path.iterdir()) == []


def test_download_manager_hands_the_cancel_flag_to_the_ig_cdn_engine():
    from infrastructure.downloader.download_manager import DownloadManager

    cfg = MagicMock(max_concurrent=1, max_retries=0, download_dir=Path("."))
    manager = DownloadManager(config=cfg, engine=MagicMock(), event_bus=MagicMock())
    task = DownloadTask(url=_CDN_URL)
    seen: dict = {}

    def _fake_download(**kwargs):
        seen.update(kwargs)
        return Path("clip.mp4")

    with patch("infrastructure.downloader.instagram_cdn_engine.download_ig_cdn_url", _fake_download):
        manager._run_task(task)

    should_cancel = seen["should_cancel"]
    assert should_cancel() is False
    task.cancel()
    assert should_cancel() is True
