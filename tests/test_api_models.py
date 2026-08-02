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
    @pytest.mark.parametrize("ext", ["mp4", "mkv", "mov", "avi", "webm", "mp3", None])
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
        ],
    )
    def test_disallowed_values_raise(self, ext):
        with pytest.raises(ValidationError):
            FileConvertRequest(file_path="/tmp/x.mp4", target_ext=ext)  # nosec B108


class TestDownloadRequestOutputExt:
    @pytest.mark.parametrize("ext", ["mp4", "mkv", "webm", "mov", "mp3", "m4a", None, ""])
    def test_allowed_values_pass(self, ext):
        req = DownloadRequest(url="https://example.com/v", output_ext=ext)
        assert req.output_ext == ext

    @pytest.mark.parametrize("ext", ["mp4/../../evil.bat", "exe", "bat"])
    def test_disallowed_values_raise(self, ext):
        with pytest.raises(ValidationError):
            DownloadRequest(url="https://example.com/v", output_ext=ext)
