"""
tests/test_font_finder.py
Coverage for utils/font_finder.py: candidate ordering by platform and the
no-font-found fallback (None).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from utils import font_finder


def _clear_cache():
    font_finder.find_font_path.cache_clear()


class TestFindFontPath:
    def setup_method(self):
        _clear_cache()

    def teardown_method(self):
        _clear_cache()

    def test_returns_none_when_no_candidate_exists(self):
        with patch.object(Path, "exists", return_value=False):
            assert font_finder.find_font_path() is None

    def test_returns_first_existing_candidate(self):
        target = font_finder._LINUX_FONTS[1]

        def fake_exists(self):
            return str(self) == target

        with patch.object(Path, "exists", fake_exists):
            assert font_finder.find_font_path() == target

    def test_result_is_cached(self):
        with patch.object(Path, "exists", return_value=False) as mock_exists:
            first = font_finder.find_font_path()
            second = font_finder.find_font_path()

        assert first is None and second is None
        # lru_cache means Path.exists is only probed on the first call.
        assert mock_exists.call_count == len(
            font_finder._LINUX_FONTS + font_finder._WINDOWS_FONTS
        )
