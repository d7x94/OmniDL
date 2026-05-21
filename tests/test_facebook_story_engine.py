"""
tests/test_facebook_story_engine.py
Unit tests for infrastructure/downloader/facebook_story_engine.py

Covers the 5 pure / I/O-only functions that require no browser or network:
  - is_facebook_story_url   — URL recognition
  - _normalize_url          — view_single=1 injection
  - _is_fb_video_url        — CDN video URL detection (regex + thumb exclusion)
  - _full_video_url         — DASH byte-range param stripping
  - _validate_mp4           — magic-byte + size validation
  - _clear_crashed_flag     — Brave/Chrome Preferences patch (filesystem)

No subprocess, Playwright, requests, or browser required.
All filesystem tests use tmp_path (pytest built-in).
"""

from __future__ import annotations

import struct
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from infrastructure.downloader.facebook_story_engine import (
    _full_video_url,
    _is_fb_video_url,
    _normalize_url,
    _validate_mp4,
    is_facebook_story_url,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_mp4(path: Path, size: int = 200_000, magic: bytes = b"ftyp") -> Path:
    """Write a minimal fake MP4 at *path* with the given size and magic bytes.

    MP4 box layout: 4-byte big-endian box size + 4-byte type.
    We write a 12-byte header and pad the rest with zeros.
    """
    header = struct.pack(">I", size) + magic + b"\x00\x00\x00\x00"
    path.write_bytes(header + b"\x00" * (size - len(header)))
    return path


# ─────────────────────────────────────────────────────────────────────────────
# is_facebook_story_url
# ─────────────────────────────────────────────────────────────────────────────


class TestIsFacebookStoryUrl:
    """URL pattern recognition — no network, no I/O."""

    # ── Positive cases ────────────────────────────────────────────────────────

    def test_standard_story_url(self):
        url = "https://www.facebook.com/stories/122114830586207697/UzpfSVND/"
        assert is_facebook_story_url(url) is True

    def test_story_url_with_view_single(self):
        url = "https://www.facebook.com/stories/123456/?view_single=1"
        assert is_facebook_story_url(url) is True

    def test_fb_watch_short_url(self):
        assert is_facebook_story_url("https://fb.watch/abc123") is True

    def test_fb_watch_uppercase_scheme(self):
        # URL is lowercased before check
        assert is_facebook_story_url("HTTPS://FB.WATCH/xyz") is True

    def test_mobile_facebook_story(self):
        url = "https://m.facebook.com/stories/999888777666/hash"
        assert is_facebook_story_url(url) is True

    def test_story_url_no_trailing_slash(self):
        assert is_facebook_story_url("https://facebook.com/stories/111") is True

    # ── Negative cases ────────────────────────────────────────────────────────

    def test_regular_facebook_video(self):
        assert is_facebook_story_url("https://www.facebook.com/watch/?v=123") is False

    def test_facebook_post(self):
        assert is_facebook_story_url("https://www.facebook.com/photo?fbid=123") is False

    def test_facebook_profile(self):
        assert is_facebook_story_url("https://www.facebook.com/username") is False

    def test_youtube_url(self):
        assert is_facebook_story_url("https://www.youtube.com/watch?v=abc") is False

    def test_empty_string(self):
        assert is_facebook_story_url("") is False

    def test_plain_text(self):
        assert is_facebook_story_url("not a url at all") is False

    def test_instagram_story_url(self):
        # Must NOT match — different platform
        assert is_facebook_story_url("https://www.instagram.com/stories/user/123") is False

    def test_facebook_reels(self):
        assert is_facebook_story_url("https://www.facebook.com/reel/123456") is False


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_url
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeUrl:
    """view_single=1 is injected or preserved correctly."""

    def _qs(self, url: str) -> dict:
        return parse_qs(urlparse(url).query)

    def test_adds_view_single_when_absent(self):
        url = "https://www.facebook.com/stories/123/hash"
        result = _normalize_url(url)
        assert self._qs(result).get("view_single") == ["1"]

    def test_overwrites_existing_view_single_zero(self):
        url = "https://www.facebook.com/stories/123/?view_single=0"
        result = _normalize_url(url)
        assert self._qs(result).get("view_single") == ["1"]

    def test_preserves_other_query_params(self):
        url = "https://www.facebook.com/stories/123/?foo=bar&baz=qux"
        result = _normalize_url(url)
        qs = self._qs(result)
        assert qs.get("foo") == ["bar"]
        assert qs.get("baz") == ["qux"]
        assert qs.get("view_single") == ["1"]

    def test_scheme_host_path_unchanged(self):
        url = "https://www.facebook.com/stories/122114830586207697/UzpfSVND/"
        result = _normalize_url(url)
        p = urlparse(result)
        assert p.scheme == "https"
        assert p.netloc == "www.facebook.com"
        assert p.path == "/stories/122114830586207697/UzpfSVND/"

    def test_url_without_query_string(self):
        url = "https://www.facebook.com/stories/999"
        result = _normalize_url(url)
        assert "view_single=1" in result

    def test_idempotent_when_already_set(self):
        url = "https://www.facebook.com/stories/123/?view_single=1"
        result = _normalize_url(url)
        # Calling again should not duplicate the param
        result2 = _normalize_url(result)
        assert result2.count("view_single=1") == 1


# ─────────────────────────────────────────────────────────────────────────────
# _is_fb_video_url
# ─────────────────────────────────────────────────────────────────────────────


class TestIsFbVideoUrl:
    """CDN video URL recognition — regex match + thumbnail exclusion."""

    BASE = "https://scontent.fsgn13-1.fna.fbcdn.net"

    # ── Positive: video paths ─────────────────────────────────────────────────

    def test_m1_v_t_path(self):
        url = f"{self.BASE}/m1/v/t6/HASH_n.mp4?_nc_cat=1"
        assert _is_fb_video_url(url) is True

    def test_o1_v_path(self):
        url = f"{self.BASE}/o1/v/t2/HASH.mp4"
        assert _is_fb_video_url(url) is True

    def test_v_t42_legacy(self):
        url = f"{self.BASE}/v/t42.9040-2/HASH.mp4"
        assert _is_fb_video_url(url) is True

    def test_v_t64_legacy(self):
        url = f"{self.BASE}/v/t64.9040-2/HASH.mp4"
        assert _is_fb_video_url(url) is True

    def test_v_t66_legacy(self):
        url = f"{self.BASE}/v/t66.9040-2/HASH.mp4"
        assert _is_fb_video_url(url) is True

    def test_bytestart_param_dash_segment(self):
        url = f"{self.BASE}/o1/v/HASH.mp4?bytestart=0&byteend=4095"
        assert _is_fb_video_url(url) is True

    def test_case_insensitive_path(self):
        url = f"{self.BASE}/M1/V/T6/HASH.mp4"
        assert _is_fb_video_url(url) is True

    # ── Negative: non-fbcdn URLs ──────────────────────────────────────────────

    def test_non_fbcdn_host_rejected(self):
        assert _is_fb_video_url("https://example.com/v/t42.mp4") is False

    def test_empty_string(self):
        assert _is_fb_video_url("") is False

    def test_youtube_url(self):
        assert _is_fb_video_url("https://youtu.be/abc123") is False

    # ── Negative: thumbnail paths (excluded by _FB_THUMB_RE) ─────────────────

    def test_v_t15_thumbnail_excluded(self):
        url = f"{self.BASE}/v/t15.5256-10/HASH_n.jpg"
        assert _is_fb_video_url(url) is False

    def test_v_t39_thumbnail_excluded(self):
        url = f"{self.BASE}/v/t39.30808-6/HASH_n.jpg"
        assert _is_fb_video_url(url) is False

    def test_v_t51_thumbnail_excluded(self):
        url = f"{self.BASE}/v/t51.2885-15/HASH_n.jpg"
        assert _is_fb_video_url(url) is False

    def test_thumbnail_with_bytestart_still_excluded(self):
        # Thumbnail path wins even if bytestart is present
        url = f"{self.BASE}/v/t15.5256-10/HASH.jpg?bytestart=0"
        assert _is_fb_video_url(url) is False


# ─────────────────────────────────────────────────────────────────────────────
# _full_video_url
# ─────────────────────────────────────────────────────────────────────────────


class TestFullVideoUrl:
    """DASH byte-range params are stripped; other params are preserved."""

    BASE_URL = "https://scontent.fsgn13-1.fna.fbcdn.net/o1/v/t2/HASH.mp4"

    def _qs(self, url: str) -> dict:
        return parse_qs(urlparse(url).query)

    def test_removes_bytestart(self):
        url = f"{self.BASE_URL}?bytestart=0&_nc_cat=1"
        result = _full_video_url(url)
        assert "bytestart" not in self._qs(result)

    def test_removes_byteend(self):
        url = f"{self.BASE_URL}?bytestart=0&byteend=4095&_nc_cat=1"
        result = _full_video_url(url)
        assert "byteend" not in self._qs(result)

    def test_removes_range(self):
        url = f"{self.BASE_URL}?range=0-4095&_nc_cat=1"
        result = _full_video_url(url)
        assert "range" not in self._qs(result)

    def test_removes_all_three_together(self):
        url = f"{self.BASE_URL}?bytestart=0&byteend=4095&range=0-4095&efg=abc"
        result = _full_video_url(url)
        qs = self._qs(result)
        assert "bytestart" not in qs
        assert "byteend" not in qs
        assert "range" not in qs
        assert qs.get("efg") == ["abc"]  # non-DASH param preserved

    def test_preserves_unrelated_params(self):
        url = f"{self.BASE_URL}?_nc_cat=1&_nc_sid=abc&bytestart=0"
        result = _full_video_url(url)
        qs = self._qs(result)
        assert qs.get("_nc_cat") == ["1"]
        assert qs.get("_nc_sid") == ["abc"]

    def test_no_byte_params_unchanged_structure(self):
        url = f"{self.BASE_URL}?_nc_cat=1"
        result = _full_video_url(url)
        assert urlparse(result).netloc == urlparse(url).netloc
        assert urlparse(result).path == urlparse(url).path

    def test_url_without_any_params(self):
        url = self.BASE_URL
        result = _full_video_url(url)
        assert result == url

    def test_returns_string(self):
        url = f"{self.BASE_URL}?bytestart=0"
        assert isinstance(_full_video_url(url), str)

    def test_idempotent(self):
        url = f"{self.BASE_URL}?bytestart=0&byteend=4095&_nc_cat=1"
        once = _full_video_url(url)
        twice = _full_video_url(once)
        assert once == twice


# ─────────────────────────────────────────────────────────────────────────────
# _validate_mp4
# ─────────────────────────────────────────────────────────────────────────────


class TestValidateMp4:
    """MP4 magic-byte + minimum size validation."""

    # ── Valid files ───────────────────────────────────────────────────────────

    def test_valid_ftyp_magic(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=200_000, magic=b"ftyp")
        assert _validate_mp4(p) is True

    def test_valid_mdat_magic(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=200_000, magic=b"mdat")
        assert _validate_mp4(p) is True

    def test_valid_moov_magic(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=200_000, magic=b"moov")
        assert _validate_mp4(p) is True

    def test_valid_wide_magic(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=200_000, magic=b"wide")
        assert _validate_mp4(p) is True

    def test_valid_free_magic(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=200_000, magic=b"free")
        assert _validate_mp4(p) is True

    def test_exactly_100kb_is_valid(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=100_000, magic=b"ftyp")
        assert _validate_mp4(p) is True

    # ── Size failures ─────────────────────────────────────────────────────────

    def test_file_too_small_99999_bytes(self, tmp_path):
        p = tmp_path / "small.mp4"
        header = struct.pack(">I", 99_999) + b"ftyp" + b"\x00\x00\x00\x00"
        p.write_bytes(header + b"\x00" * (99_999 - len(header)))
        assert _validate_mp4(p) is False

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.mp4"
        p.write_bytes(b"")
        assert _validate_mp4(p) is False

    def test_8_byte_file(self, tmp_path):
        p = tmp_path / "tiny.mp4"
        p.write_bytes(b"\x00" * 8)
        assert _validate_mp4(p) is False

    # ── Magic byte failures ───────────────────────────────────────────────────

    def test_wrong_magic_zeros(self, tmp_path):
        p = tmp_path / "bad.mp4"
        p.write_bytes(b"\x00" * 200_000)
        assert _validate_mp4(p) is False

    def test_wrong_magic_jpeg(self, tmp_path):
        # JPEG starts with FF D8 FF
        p = tmp_path / "bad.mp4"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg" + b"\x00" * 199_994)
        assert _validate_mp4(p) is False

    def test_wrong_magic_png(self, tmp_path):
        p = tmp_path / "bad.mp4"
        p.write_bytes(b"\x89PNG" + b"IHDR" + b"\x00" * 199_994)
        assert _validate_mp4(p) is False

    def test_wrong_magic_zip(self, tmp_path):
        # ZIP magic: PK\x03\x04
        p = tmp_path / "bad.mp4"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 199_996)
        assert _validate_mp4(p) is False

    # ── Error resilience ──────────────────────────────────────────────────────

    def test_nonexistent_file_returns_false(self, tmp_path):
        p = tmp_path / "does_not_exist.mp4"
        assert _validate_mp4(p) is False

    def test_directory_path_returns_false(self, tmp_path):
        assert _validate_mp4(tmp_path) is False

    def test_returns_bool_not_truthy(self, tmp_path):
        p = _make_mp4(tmp_path / "video.mp4", size=200_000, magic=b"ftyp")
        result = _validate_mp4(p)
        assert result is True  # strict identity check


# ─────────────────────────────────────────────────────────────────────────────
# _clear_crashed_flag  (filesystem — uses tmp_path)
# ─────────────────────────────────────────────────────────────────────────────


class TestClearCrashedFlag:
    """Brave/Chrome Preferences crash flag is reset to Normal."""

    def _write_prefs(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_exit_type_set_to_normal(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        prefs = tmp_path / "Default" / "Preferences"
        self._write_prefs(prefs, '{"exit_type": "Crashed"}')
        _clear_crashed_flag(tmp_path)
        result = prefs.read_text()
        assert '"exit_type": "Normal"' in result
        assert "Crashed" not in result

    def test_crashed_flag_set_to_false(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        prefs = tmp_path / "Default" / "Preferences"
        self._write_prefs(prefs, '{"crashed": true, "exit_type": "Crashed"}')
        _clear_crashed_flag(tmp_path)
        result = prefs.read_text()
        assert '"crashed": false' in result

    def test_session_crash_detected_set_to_false(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        prefs = tmp_path / "Default" / "Preferences"
        self._write_prefs(prefs, '{"session_crash_detected": true}')
        _clear_crashed_flag(tmp_path)
        result = prefs.read_text()
        assert '"session_crash_detected": false' in result

    def test_all_three_flags_cleared_at_once(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        content = (
            '{"exit_type": "Crashed", "crashed": true, "session_crash_detected": true, "other": "value"}'
        )
        prefs = tmp_path / "Default" / "Preferences"
        self._write_prefs(prefs, content)
        _clear_crashed_flag(tmp_path)
        result = prefs.read_text()
        assert '"exit_type": "Normal"' in result
        assert '"crashed": false' in result
        assert '"session_crash_detected": false' in result
        assert '"other": "value"' in result

    def test_multiple_profile_slots_patched(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        for slot in ["Default", "Profile 1", "Profile 2"]:
            prefs = tmp_path / slot / "Preferences"
            self._write_prefs(prefs, '{"exit_type": "Crashed"}')

        _clear_crashed_flag(tmp_path)

        for slot in ["Default", "Profile 1", "Profile 2"]:
            result = (tmp_path / slot / "Preferences").read_text()
            assert '"exit_type": "Normal"' in result, f"Slot '{slot}' not patched"

    def test_missing_slot_silently_skipped(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        # Only "Default" exists — "Profile 1" and "Profile 2" are absent
        prefs = tmp_path / "Default" / "Preferences"
        self._write_prefs(prefs, '{"exit_type": "Crashed"}')
        _clear_crashed_flag(tmp_path)
        assert '"exit_type": "Normal"' in prefs.read_text()

    def test_nonexistent_profile_dir_silently_ignored(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        ghost = tmp_path / "no_such_dir"
        # Must not raise
        _clear_crashed_flag(ghost)

    def test_already_normal_is_idempotent(self, tmp_path):
        from infrastructure.downloader.facebook_story_engine import _clear_crashed_flag

        content = '{"exit_type": "Normal", "crashed": false}'
        prefs = tmp_path / "Default" / "Preferences"
        self._write_prefs(prefs, content)
        _clear_crashed_flag(tmp_path)
        result = prefs.read_text()
        assert '"exit_type": "Normal"' in result
        assert '"crashed": false' in result


# ─────────────────────────────────────────────────────────────────────────────
# _find_browser_exe  (platform-specific paths)
# ─────────────────────────────────────────────────────────────────────────────


class TestFindBrowserExe:
    """Browser executable lookup — mocked filesystem, no real browser needed."""

    def test_windows_brave_found(self, tmp_path, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "win32")
        brave_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: str(self) == brave_path)
        result = eng._find_browser_exe("brave")
        assert result == brave_path

    def test_windows_chrome_found(self, tmp_path, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "win32")
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: str(self) == chrome_path)
        result = eng._find_browser_exe("chrome")
        assert result == chrome_path

    def test_macos_brave_found(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        brave_path = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: self.as_posix() == brave_path)
        result = eng._find_browser_exe("brave")
        assert result == brave_path

    def test_macos_chrome_found(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: self.as_posix() == chrome_path)
        result = eng._find_browser_exe("chrome")
        assert result == chrome_path

    def test_macos_home_applications_fallback(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        # /Applications not present, ~/Applications is
        from infrastructure.downloader import facebook_story_engine as eng

        home_brave = str(Path.home() / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
        monkeypatch.setattr(eng.Path, "exists", lambda self: str(self) == home_brave)
        result = eng._find_browser_exe("brave")
        assert result == home_brave

    def test_windows_not_found_raises(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "win32")
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: False)
        with pytest.raises(RuntimeError, match="Không tìm thấy"):
            eng._find_browser_exe("brave")

    def test_macos_not_found_raises(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: False)
        with pytest.raises(RuntimeError, match="Không tìm thấy"):
            eng._find_browser_exe("chrome")

    def test_linux_raises_not_supported(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "linux")
        from infrastructure.downloader import facebook_story_engine as eng

        with pytest.raises(RuntimeError, match="macOS"):
            eng._find_browser_exe("brave")

    def test_case_insensitive_browser_name(self, monkeypatch):
        import sys

        monkeypatch.setattr(sys, "platform", "darwin")
        brave_path = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        from infrastructure.downloader import facebook_story_engine as eng

        monkeypatch.setattr(eng.Path, "exists", lambda self: self.as_posix() == brave_path)
        # "BRAVE" should resolve same as "brave"
        result = eng._find_browser_exe("BRAVE")
        assert result == brave_path
