"""
tests/test_instagram_cdn_engine_extra.py
Coverage for infrastructure/downloader/instagram_cdn_engine.py branches not
exercised by tests/test_instagram_antibot.py::TestAnonymousCdnPaste:
  - destination filename collision (dest.exists() loop)
  - 403 status -> "blocked:" hard error
  - empty chunk skipped; on_progress exception swallowed
  - mid-stream exception deletes the .part file and re-raises
  - sub-1KB download raises "not found:" and deletes the .part file
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from infrastructure.downloader.instagram_cdn_engine import download_ig_cdn_url

_CDN_URL = (
    "https://scontent.cdninstagram.com/v/t66.30100-16/clip.mp4"
    "?_nc_cat=1&oh=abc123&oe=64ABCDEF"
)


def _fake_resp(status_code=200, headers=None, chunks=None, total=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or ({"content-length": str(total)} if total else {})
    resp.raise_for_status = lambda: None
    resp.iter_content = lambda chunk_size: iter(chunks or [])
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestDestinationCollision:
    def test_existing_file_gets_counter_suffix(self, tmp_path):
        (tmp_path / "clip.mp4").write_bytes(b"already here")
        resp = _fake_resp(chunks=[b"x" * 2048])

        with patch("requests.get", return_value=resp):
            dest = download_ig_cdn_url(url=_CDN_URL, output_dir=tmp_path, filename_hint="clip")

        assert dest.name == "clip (1).mp4"
        assert dest.exists()


class TestBlockedStatus:
    def test_403_raises_blocked_error(self, tmp_path):
        resp = _fake_resp(status_code=403)

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="blocked:"):
                download_ig_cdn_url(url=_CDN_URL, output_dir=tmp_path, filename_hint="clip")


class TestProgressCallback:
    def test_empty_chunk_skipped_and_progress_exception_swallowed(self, tmp_path):
        resp = _fake_resp(chunks=[b"", b"y" * 2048], total=2048)

        def bad_progress(pct, speed):
            raise RuntimeError("UI gone")

        with patch("requests.get", return_value=resp):
            dest = download_ig_cdn_url(
                url=_CDN_URL, output_dir=tmp_path, filename_hint="clip", on_progress=bad_progress
            )

        assert dest.exists()
        assert dest.read_bytes() == b"y" * 2048


class TestMidStreamDrop:
    def test_exception_during_iteration_deletes_part_and_reraises(self, tmp_path):
        def bad_iter_content(chunk_size):
            yield b"partial-data"
            raise ConnectionError("connection reset")

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        resp.raise_for_status = lambda: None
        resp.iter_content = bad_iter_content
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)

        with patch("requests.get", return_value=resp):
            with pytest.raises(ConnectionError):
                download_ig_cdn_url(url=_CDN_URL, output_dir=tmp_path, filename_hint="clip")

        assert not (tmp_path / "clip.mp4.part").exists()
        assert not (tmp_path / "clip.mp4").exists()


class TestTinyFileRejected:
    def test_sub_1kb_file_raises_not_found_and_cleans_up(self, tmp_path):
        resp = _fake_resp(chunks=[b"tiny"])

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="not found:"):
                download_ig_cdn_url(url=_CDN_URL, output_dir=tmp_path, filename_hint="clip")

        assert not (tmp_path / "clip.mp4.part").exists()
        assert not (tmp_path / "clip.mp4").exists()
