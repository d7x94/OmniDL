"""
tests/test_helpers.py
Unit tests for utils/helpers.py

Covers every public function:
- fmt_bytes        — edge cases: 0, exact boundaries, each unit
- fmt_duration     — seconds-only, with minutes, with hours
- is_valid_url     — valid http/https, invalid schemes, garbage
- sanitise_filename — unsafe chars, unicode, empty result, max_len
- safe_path        — normal resolution, traversal attempt (..)
- reveal_in_explorer / open_folder — smoke tests (subprocess mocked)
"""
from unittest.mock import patch

import pytest

from utils.helpers import (
    fmt_bytes,
    fmt_duration,
    is_valid_url,
    sanitise_filename,
    safe_path,
    reveal_in_explorer,
    open_folder,
)


# ---------------------------------------------------------------------------
# fmt_bytes
# ---------------------------------------------------------------------------

class TestFmtBytes:
    def test_zero(self):
        assert fmt_bytes(0) == "0.0 B"

    def test_bytes_below_kb(self):
        assert fmt_bytes(512) == "512.0 B"

    def test_exact_one_kb(self):
        # 1024 B → 1.0 KB
        assert fmt_bytes(1024) == "1.0 KB"

    def test_megabytes(self):
        assert fmt_bytes(1024 ** 2) == "1.0 MB"

    def test_gigabytes(self):
        assert fmt_bytes(1024 ** 3) == "1.0 GB"

    def test_terabytes(self):
        assert fmt_bytes(1024 ** 4) == "1.0 TB"

    def test_petabytes(self):
        # Exceeds TB branch → PB
        assert fmt_bytes(1024 ** 5) == "1.0 PB"

    def test_fractional_mb(self):
        result = fmt_bytes(int(1.5 * 1024 ** 2))
        assert result == "1.5 MB"

    def test_small_kb(self):
        assert fmt_bytes(2048) == "2.0 KB"


# ---------------------------------------------------------------------------
# fmt_duration
# ---------------------------------------------------------------------------

class TestFmtDuration:
    def test_zero_seconds(self):
        assert fmt_duration(0) == "00:00"

    def test_less_than_one_minute(self):
        assert fmt_duration(45) == "00:45"

    def test_exact_one_minute(self):
        assert fmt_duration(60) == "01:00"

    def test_minutes_and_seconds(self):
        assert fmt_duration(90) == "01:30"

    def test_exact_one_hour(self):
        assert fmt_duration(3600) == "1:00:00"

    def test_hours_minutes_seconds(self):
        assert fmt_duration(3661) == "1:01:01"

    def test_float_input_truncated(self):
        # floats should be truncated to int
        assert fmt_duration(90.9) == "01:30"

    def test_two_hours(self):
        assert fmt_duration(7322) == "2:02:02"


# ---------------------------------------------------------------------------
# is_valid_url
# ---------------------------------------------------------------------------

class TestIsValidUrl:
    def test_valid_https(self):
        assert is_valid_url("https://www.youtube.com/watch?v=abc") is True

    def test_valid_http(self):
        assert is_valid_url("http://example.com") is True

    def test_valid_url_with_path_and_query(self):
        assert is_valid_url("https://example.com/path?foo=bar&baz=1") is True

    def test_ftp_scheme_rejected(self):
        assert is_valid_url("ftp://example.com/file.txt") is False

    def test_file_scheme_rejected(self):
        assert is_valid_url("file:///etc/passwd") is False

    def test_no_scheme(self):
        assert is_valid_url("www.youtube.com/watch") is False

    def test_empty_string(self):
        assert is_valid_url("") is False

    def test_whitespace_only(self):
        assert is_valid_url("   ") is False

    def test_leading_trailing_whitespace_stripped(self):
        assert is_valid_url("  https://example.com  ") is True

    def test_javascript_scheme_rejected(self):
        assert is_valid_url("javascript:alert(1)") is False

    def test_url_with_port(self):
        assert is_valid_url("https://example.com:8080/path") is True


# ---------------------------------------------------------------------------
# sanitise_filename
# ---------------------------------------------------------------------------

class TestSanitiseFilename:
    def test_normal_name_unchanged(self):
        assert sanitise_filename("My Video") == "My Video"

    def test_removes_angle_brackets(self):
        result = sanitise_filename("video<title>")
        assert "<" not in result and ">" not in result

    def test_removes_colon(self):
        assert ":" not in sanitise_filename("C: Drive")

    def test_removes_slash(self):
        assert "/" not in sanitise_filename("path/to/file")

    def test_removes_backslash(self):
        assert "\\" not in sanitise_filename("back\\slash")

    def test_removes_pipe(self):
        assert "|" not in sanitise_filename("a|b")

    def test_removes_question_mark(self):
        assert "?" not in sanitise_filename("what?")

    def test_removes_asterisk(self):
        assert "*" not in sanitise_filename("star*")

    def test_removes_quotes(self):
        assert '"' not in sanitise_filename('"quoted"')

    def test_strips_leading_dots(self):
        result = sanitise_filename("...hidden")
        assert not result.startswith(".")

    def test_empty_input_returns_download(self):
        assert sanitise_filename("") == "download"

    def test_only_unsafe_chars_returns_download(self):
        # Characters that get replaced by _ and then stripped must result in "download".
        # Null bytes and control chars are replaced by _, then .strip(". ") removes
        # nothing — so we need a string that becomes empty after replacement + strip.
        # Use a string of dots and spaces which strip(". ") removes entirely.
        assert sanitise_filename("... ...") == "download"

    def test_max_len_truncates(self):
        long_name = "a" * 300
        assert len(sanitise_filename(long_name)) == 200

    def test_custom_max_len(self):
        assert len(sanitise_filename("a" * 100, max_len=50)) == 50

    def test_unicode_preserved(self):
        result = sanitise_filename("Tải xuống video")
        assert "Tải xuống video" == result

    def test_null_byte_removed(self):
        assert "\x00" not in sanitise_filename("file\x00name")


# ---------------------------------------------------------------------------
# safe_path
# ---------------------------------------------------------------------------

class TestSafePath:
    def test_normal_child_path_allowed(self, tmp_path):
        result = safe_path(tmp_path, "subdir/file.txt")
        assert result == (tmp_path / "subdir" / "file.txt").resolve()

    def test_traversal_with_dotdot_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Path traversal"):
            safe_path(tmp_path, "../etc/passwd")

    def test_absolute_traversal_raises(self, tmp_path):
        # An absolute path pointing outside base should be rejected
        outside = str(tmp_path.parent / "outside.txt")
        with pytest.raises(ValueError):
            safe_path(tmp_path, outside)

    def test_same_directory_allowed(self, tmp_path):
        result = safe_path(tmp_path, "file.txt")
        assert str(result).startswith(str(tmp_path.resolve()))


# ---------------------------------------------------------------------------
# reveal_in_explorer / open_folder — subprocess mocked
# ---------------------------------------------------------------------------

class TestRevealInExplorer:
    def test_windows_uses_shell_api(self, tmp_path):
        """
        Windows reveal_in_explorer must use SHOpenFolderAndSelectItems via
        ctypes to open the parent folder and SELECT (highlight) the file.

        Correct 3-step idiom:
          parent_pidl = ILCreateFromPathW(parent_dir)
          file_pidl   = ILCreateFromPathW(file_path)
          rel_pidl    = ILFindLastID(file_pidl)   # child PIDL — no free
          SHOpenFolderAndSelectItems(parent_pidl, 1, [rel_pidl], 0)

        cidl=1 + relative child PIDL → Explorer opens folder AND selects file.
        cidl=0 → only opens folder, no file is selected/highlighted.
        """
        fake_file = tmp_path / "video#hash [1].mp4"
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.ctypes") as mock_ctypes:
            mock_sys.platform = "win32"
            # Both ILCreateFromPathW calls return non-NULL PIDLs.
            mock_ctypes.windll.shell32.ILCreateFromPathW.return_value = 1
            mock_ctypes.windll.shell32.ILFindLastID.return_value = 2
            mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
            import ctypes as _real_ctypes
            mock_ctypes.c_void_p = _real_ctypes.c_void_p
            result = reveal_in_explorer(fake_file)
            sh_open = mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems
            il_find = mock_ctypes.windll.shell32.ILFindLastID
            assert mock_ctypes.windll.shell32.ILCreateFromPathW.called
            assert il_find.called, (
                "ILFindLastID must be called to get the relative child PIDL"
            )
            assert sh_open.called
            # cidl must be 1 (select the file), NOT 0 (0 only opens folder)
            call_args = sh_open.call_args[0]
            assert call_args[1] == 1, (
                f"cidl must be 1 to select the file; got {call_args[1]}. "
                "cidl=0 only opens the folder without highlighting any file."
            )
            assert result is True

    def test_windows_returns_false_when_parent_pidl_null(self, tmp_path):
        """If parent dir ILCreateFromPathW returns NULL, return False."""
        fake_file = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.ctypes") as mock_ctypes:
            mock_sys.platform = "win32"
            # First call (parent dir) returns NULL — must bail.
            mock_ctypes.windll.shell32.ILCreateFromPathW.side_effect = [0, 1]
            result = reveal_in_explorer(fake_file)
            assert result is False

    def test_windows_returns_false_when_file_pidl_null(self, tmp_path):
        """If file ILCreateFromPathW returns NULL, return False."""
        fake_file = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.ctypes") as mock_ctypes:
            mock_sys.platform = "win32"
            # Second call (file path) returns NULL — must bail.
            mock_ctypes.windll.shell32.ILCreateFromPathW.side_effect = [1, 0]
            result = reveal_in_explorer(fake_file)
            assert result is False

    def test_macos_uses_open_r(self, tmp_path):
        fake_file = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.subprocess.Popen") as mock_popen:
            mock_sys.platform = "darwin"
            reveal_in_explorer(fake_file)
            args = mock_popen.call_args[0][0]
            assert args[0] == "open"
            assert "-R" in args

    def test_linux_uses_xdg_open(self, tmp_path):
        fake_file = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.subprocess.Popen") as mock_popen:
            mock_sys.platform = "linux"
            reveal_in_explorer(fake_file)
            args = mock_popen.call_args[0][0]
            assert args[0] == "xdg-open"

    def test_no_subprocess_on_windows(self, tmp_path):
        # Windows must use ctypes, NOT subprocess.Popen
        # (explorer /select, breaks silently on # in filenames).
        fake_file = tmp_path / "video#calisthenics [123].mp4"
        import ctypes as _real_ctypes
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.ctypes") as mock_ctypes, \
             patch("utils.helpers.subprocess.Popen") as mock_popen:
            mock_sys.platform = "win32"
            mock_ctypes.windll.shell32.ILCreateFromPathW.return_value = 1
            mock_ctypes.windll.shell32.ILFindLastID.return_value = 2
            mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
            mock_ctypes.c_void_p = _real_ctypes.c_void_p
            reveal_in_explorer(fake_file)
            assert not mock_popen.called, (
                "subprocess.Popen must not be called on Windows; "
                "explorer /select, breaks on # in filenames"
            )

    def test_exception_is_swallowed(self, tmp_path):
        """reveal_in_explorer must never raise, even if ctypes fails."""
        fake_file = tmp_path / "video.mp4"
        with (patch("utils.helpers.sys") as mock_sys,
             patch("utils.helpers.ctypes",
                   side_effect=ImportError("no ctypes"))):
            mock_sys.platform = "win32"
            reveal_in_explorer(fake_file)  # must not raise


class TestOpenFolder:
    @pytest.mark.skipif(
        __import__("sys").platform != "win32",
        reason="os.startfile unavailable on non-Windows (Python 3.13 frozen os)",
    )
    def test_windows_opens_explorer(self, tmp_path):
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.subprocess.Popen") as mock_popen:
            mock_sys.platform = "win32"
            open_folder(tmp_path)
            args = mock_popen.call_args[0][0]
            assert args[0] == "explorer"

    def test_macos_uses_open(self, tmp_path):
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.subprocess.Popen") as mock_popen:
            mock_sys.platform = "darwin"
            open_folder(tmp_path)
            args = mock_popen.call_args[0][0]
            assert args[0] == "open"

    def test_exception_is_swallowed(self, tmp_path):
        with patch("utils.helpers.sys") as mock_sys, \
             patch("utils.helpers.subprocess.Popen", side_effect=OSError):
            mock_sys.platform = "linux"
            open_folder(tmp_path)  # should not raise
