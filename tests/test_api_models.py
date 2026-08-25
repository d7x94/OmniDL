"""
tests/test_api_models.py
Validator regression tests for api/models.py.

Covers:
- H2: FileConvertRequest.target_ext allowlist (path-traversal via convert extension)
- M3: DownloadRequest.output_ext allowlist
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models import DownloadRequest, FileConvertRequest


class TestFileConvertRequestTargetExt:
    @pytest.mark.parametrize("ext", ["mp4", "mkv", "mov", "avi", "mp3", None])
    def test_allowed_values_pass(self, ext):
        req = FileConvertRequest(file_path="/tmp/x.mp4", target_ext=ext)  # nosec B108
        assert req.target_ext == ext

    @pytest.mark.parametrize(
        "ext",
        [
            "mp4/../../../../evil.bat",
            "../../evil",
            "exe",
            "mp4;rm -rf /",
            # The pipeline emits H.264 + AAC and remuxes with "-c copy"; the
            # WebM muxer takes only VP8/VP9/AV1 + Vorbis/Opus, so every webm
            # job failed after the full encode had already run.
            "webm",
        ],
    )
    def test_disallowed_values_raise(self, ext):
        with pytest.raises(ValidationError):
            FileConvertRequest(file_path="/tmp/x.mp4", target_ext=ext)  # nosec B108


class TestFileConvertRequestSubtitleAndVmafFields:
    """Regression: convert_file() (api/server.py) reads body.generate_subtitles,
    body.subtitle_language, body.compute_vmaf -- missing fields on the model
    raised AttributeError (500) on every POST /api/files/convert call."""

    def test_defaults(self):
        req = FileConvertRequest(file_path="/tmp/x.mp4")  # nosec B108
        assert req.generate_subtitles is False
        assert req.subtitle_language == "auto"
        assert req.compute_vmaf is False

    def test_explicit_values(self):
        req = FileConvertRequest(
            file_path="/tmp/x.mp4",  # nosec B108
            generate_subtitles=True,
            subtitle_language="en",
            compute_vmaf=True,
        )
        assert req.generate_subtitles is True
        assert req.subtitle_language == "en"
        assert req.compute_vmaf is True


class TestDownloadRequestOutputExt:
    @pytest.mark.parametrize("ext", ["mp4", "mkv", "webm", "mov", "mp3", "m4a", None, ""])
    def test_allowed_values_pass(self, ext):
        req = DownloadRequest(url="https://example.com/v", output_ext=ext)
        assert req.output_ext == ext

    @pytest.mark.parametrize("ext", ["mp4/../../evil.bat", "exe", "bat"])
    def test_disallowed_values_raise(self, ext):
        with pytest.raises(ValidationError):
            DownloadRequest(url="https://example.com/v", output_ext=ext)
