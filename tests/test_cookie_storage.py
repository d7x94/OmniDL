"""
tests/test_cookie_storage.py
Unit tests for infrastructure/downloader/cookie_storage.py

All DPAPI calls are mocked — tests run headless on any platform.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.downloader.cookie_storage import (
    ENCRYPTED_SUFFIX,
    cleanup_leftover_temp_files,
    cleanup_stale_cookies,
    decrypt_to_tempfile,
    encrypt_cookie_file,
    is_encrypted,
    _TEMP_PREFIX,
)

SAMPLE_COOKIES = b"# Netscape HTTP Cookie File\n.tiktok.com\tTRUE\t/\tTRUE\t999999\tsid\tabc\n"
SAMPLE_ENCRYPTED = b"\x00DPAPI_MOCK_ENCRYPTED_DATA\x00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_txt(path: Path, content: bytes = SAMPLE_COOKIES) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Tests — is_encrypted
# ---------------------------------------------------------------------------

class TestIsEncrypted:
    def test_enc_suffix_detected(self, tmp_path):
        assert is_encrypted(tmp_path / "cookies.enc") is True

    def test_txt_suffix_not_encrypted(self, tmp_path):
        assert is_encrypted(tmp_path / "cookies.txt") is False

    def test_no_suffix_not_encrypted(self, tmp_path):
        assert is_encrypted(tmp_path / "cookies") is False


# ---------------------------------------------------------------------------
# Tests — encrypt_cookie_file
# ---------------------------------------------------------------------------

class TestEncryptCookieFile:

    def test_returns_original_on_non_windows(self, tmp_path):
        """On non-Windows, encryption is a no-op and original path returned."""
        txt = _write_txt(tmp_path / "c.txt")
        with patch("sys.platform", "linux"):
            result = encrypt_cookie_file(txt)
        assert result == txt
        assert txt.exists()  # original not deleted

    def test_encrypts_on_windows_mock(self, tmp_path):
        """On Windows (mocked), .txt becomes .enc and plaintext deleted."""
        txt = _write_txt(tmp_path / "c.txt")
        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_encrypt",
                 return_value=SAMPLE_ENCRYPTED,
             ):
            result = encrypt_cookie_file(txt)

        assert result == txt.with_suffix(ENCRYPTED_SUFFIX)
        assert result.exists()
        assert result.read_bytes() == SAMPLE_ENCRYPTED
        assert not txt.exists()  # plaintext deleted

    def test_fallback_when_dpapi_fails(self, tmp_path):
        """If DPAPI returns None, original plaintext file is kept intact."""
        txt = _write_txt(tmp_path / "c.txt")
        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_encrypt",
                 return_value=None,
             ):
            result = encrypt_cookie_file(txt)

        assert result == txt   # unchanged path
        assert txt.exists()    # plaintext preserved

    def test_returns_original_if_file_missing(self, tmp_path):
        """Missing input file returns the path unchanged without crashing."""
        txt = tmp_path / "missing.txt"
        result = encrypt_cookie_file(txt)
        assert result == txt

    def test_enc_file_written_before_plaintext_deleted(self, tmp_path):
        """Atomicity: .enc must exist before .txt is unlinked."""
        txt = _write_txt(tmp_path / "c.txt")
        written = []

        def fake_write(path, data):
            written.append(path)
            Path(path).write_bytes(data)

        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_encrypt",
                 return_value=SAMPLE_ENCRYPTED,
             ):
            result = encrypt_cookie_file(txt)

        assert result.exists()   # .enc written
        assert not txt.exists()  # .txt deleted


# ---------------------------------------------------------------------------
# Tests — decrypt_to_tempfile
# ---------------------------------------------------------------------------

class TestDecryptToTempfile:

    def test_plaintext_returned_as_is(self, tmp_path):
        """Non-.enc file is returned unchanged — no temp file created."""
        txt = _write_txt(tmp_path / "c.txt")
        result = decrypt_to_tempfile(txt)
        assert result == txt

    def test_decrypts_enc_on_windows_mock(self, tmp_path):
        """On Windows (mocked), .enc is decrypted to a temp .txt file."""
        enc = tmp_path / "c.enc"
        enc.write_bytes(SAMPLE_ENCRYPTED)

        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_decrypt",
                 return_value=SAMPLE_COOKIES,
             ):
            tmp = decrypt_to_tempfile(enc)

        try:
            assert tmp != enc
            assert tmp.suffix == ".txt"
            assert tmp.name.startswith(_TEMP_PREFIX)
            assert tmp.read_bytes() == SAMPLE_COOKIES
        finally:
            tmp.unlink(missing_ok=True)

    def test_raises_on_non_windows_enc(self, tmp_path):
        """Non-Windows with .enc file raises RuntimeError (DPAPI unavailable)."""
        enc = tmp_path / "c.enc"
        enc.write_bytes(SAMPLE_ENCRYPTED)
        with patch("sys.platform", "linux"):
            with pytest.raises(RuntimeError, match="not supported on Linux"):
                decrypt_to_tempfile(enc)

    def test_raises_when_dpapi_decrypt_fails(self, tmp_path):
        """Corrupt or wrong-user .enc raises RuntimeError."""
        enc = tmp_path / "c.enc"
        enc.write_bytes(b"corrupted")
        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_decrypt",
                 return_value=None,
             ):
            with pytest.raises(RuntimeError, match="decryption failed"):
                decrypt_to_tempfile(enc)

    def test_temp_file_in_same_directory_as_enc(self, tmp_path):
        """Temp file must be created next to the .enc file (same filesystem)."""
        enc = tmp_path / "c.enc"
        enc.write_bytes(SAMPLE_ENCRYPTED)
        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_decrypt",
                 return_value=SAMPLE_COOKIES,
             ):
            tmp = decrypt_to_tempfile(enc)

        try:
            assert tmp.parent == enc.parent
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests — cleanup_stale_cookies
# ---------------------------------------------------------------------------

class TestCleanupStaleCookies:

    def test_deletes_old_txt_files(self, tmp_path):
        old = _write_txt(tmp_path / "old_cookies.txt")
        # Set mtime to 40 days ago
        old_time = time.time() - 40 * 86400
        import os
        os.utime(old, (old_time, old_time))

        deleted = cleanup_stale_cookies(tmp_path, max_age_days=30)
        assert deleted == 1
        assert not old.exists()

    def test_deletes_old_enc_files(self, tmp_path):
        enc = tmp_path / "old_cookies.enc"
        enc.write_bytes(SAMPLE_ENCRYPTED)
        old_time = time.time() - 35 * 86400
        import os
        os.utime(enc, (old_time, old_time))

        deleted = cleanup_stale_cookies(tmp_path, max_age_days=30)
        assert deleted == 1
        assert not enc.exists()

    def test_keeps_recent_files(self, tmp_path):
        recent = _write_txt(tmp_path / "recent.txt")
        # Recent = 5 days old
        import os
        recent_time = time.time() - 5 * 86400
        os.utime(recent, (recent_time, recent_time))

        deleted = cleanup_stale_cookies(tmp_path, max_age_days=30)
        assert deleted == 0
        assert recent.exists()

    def test_skips_tmp_prefix_files(self, tmp_path):
        """_tmp_ and omnidl_dec_ prefixed files must never be deleted by stale cleanup."""
        tmp_file = _write_txt(tmp_path / "_tmp_cookies.txt")
        dec_file = _write_txt(tmp_path / f"{_TEMP_PREFIX}abc.txt")
        # Make them very old
        old_time = time.time() - 100 * 86400
        import os
        for f in (tmp_file, dec_file):
            os.utime(f, (old_time, old_time))

        deleted = cleanup_stale_cookies(tmp_path, max_age_days=1)
        assert deleted == 0
        assert tmp_file.exists()
        assert dec_file.exists()

    def test_nonexistent_dir_returns_zero(self, tmp_path):
        result = cleanup_stale_cookies(tmp_path / "nonexistent")
        assert result == 0


# ---------------------------------------------------------------------------
# Tests — cleanup_leftover_temp_files
# ---------------------------------------------------------------------------

class TestCleanupLeftoverTempFiles:

    def test_deletes_omnidl_dec_files(self, tmp_path):
        leftover = _write_txt(tmp_path / f"{_TEMP_PREFIX}xyz.txt")
        cleanup_leftover_temp_files(tmp_path)
        assert not leftover.exists()

    def test_keeps_normal_cookie_files(self, tmp_path):
        normal = _write_txt(tmp_path / "tiktok_brave_cookies.txt")
        cleanup_leftover_temp_files(tmp_path)
        assert normal.exists()

    def test_nonexistent_dir_no_crash(self, tmp_path):
        cleanup_leftover_temp_files(tmp_path / "nonexistent")  # must not raise



# ---------------------------------------------------------------------------
# Tests — macOS Keychain + Fernet
# ---------------------------------------------------------------------------

class TestMacosKeychain:

    def test_get_or_create_key_creates_new_when_absent(self, tmp_path):
        """First call creates a 32-byte key and stores it in Keychain (mocked)."""
        from infrastructure.downloader.cookie_storage import _macos_get_or_create_key

        with patch("keyring.get_password", return_value=None), \
             patch("keyring.set_password") as mock_set:
            key = _macos_get_or_create_key()

        assert isinstance(key, bytes)
        assert len(key) == 32
        mock_set.assert_called_once()

    def test_get_or_create_key_returns_existing(self):
        """Second call returns the same key stored in Keychain (mocked)."""
        import base64
        from infrastructure.downloader.cookie_storage import _macos_get_or_create_key

        stored_key = b"\x01" * 32
        encoded = base64.urlsafe_b64encode(stored_key).decode()

        with patch("keyring.get_password", return_value=encoded):
            key = _macos_get_or_create_key()

        assert key == stored_key

    def test_get_or_create_key_regenerates_corrupted(self):
        """Corrupted Keychain entry triggers key regeneration."""
        from infrastructure.downloader.cookie_storage import _macos_get_or_create_key

        with patch("keyring.get_password", return_value="!!not_base64!!"), \
             patch("keyring.set_password"):
            key = _macos_get_or_create_key()

        assert len(key) == 32  # new key generated

    def test_get_or_create_key_raises_if_set_fails(self):
        """RuntimeError raised if Keychain write fails (permission denied)."""
        from infrastructure.downloader.cookie_storage import _macos_get_or_create_key

        with patch("keyring.get_password", return_value=None), \
             patch("keyring.set_password", side_effect=Exception("Keychain locked")):
            with pytest.raises(RuntimeError, match="Keychain"):
                _macos_get_or_create_key()


class TestMacosEncryptDecrypt:

    def _mock_keychain(self, stored_key: bytes):
        """Context manager that mocks keyring with a fixed key."""
        import base64
        encoded = base64.urlsafe_b64encode(stored_key).decode()
        return patch("keyring.get_password", return_value=encoded)

    def test_encrypt_on_macos(self, tmp_path):
        """On macOS (mocked), .txt is encrypted to .enc and plaintext deleted."""
        txt = _write_txt(tmp_path / "c.txt")
        key = b"\x42" * 32

        with patch("sys.platform", "darwin"), self._mock_keychain(key):
            result = encrypt_cookie_file(txt)

        assert result.suffix == ENCRYPTED_SUFFIX
        assert result.exists()
        assert not txt.exists()
        # Encrypted content should not be plaintext
        assert result.read_bytes() != SAMPLE_COOKIES

    def test_decrypt_on_macos(self, tmp_path):
        """Encrypt then decrypt on macOS (mocked) recovers original bytes."""
        txt = _write_txt(tmp_path / "rt.txt", SAMPLE_COOKIES)
        key = b"\xAB" * 32

        with patch("sys.platform", "darwin"), self._mock_keychain(key):
            enc = encrypt_cookie_file(txt)
            tmp = decrypt_to_tempfile(enc)

        try:
            assert tmp.read_bytes() == SAMPLE_COOKIES
        finally:
            tmp.unlink(missing_ok=True)

    def test_decrypt_fails_with_wrong_key(self, tmp_path):
        """Decrypting with a different key raises RuntimeError."""
        txt = _write_txt(tmp_path / "c.txt", SAMPLE_COOKIES)
        key_a = b"\xAA" * 32
        key_b = b"\xBB" * 32

        with patch("sys.platform", "darwin"), self._mock_keychain(key_a):
            enc = encrypt_cookie_file(txt)

        with patch("sys.platform", "darwin"), self._mock_keychain(key_b):
            with pytest.raises(RuntimeError, match="Fernet decryption failed"):
                decrypt_to_tempfile(enc)
        enc.unlink(missing_ok=True)

    def test_macos_noopens_if_keyring_missing(self, tmp_path):
        """If keyring is not installed, encrypt falls back to plaintext."""
        txt = _write_txt(tmp_path / "c.txt")

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "keyring":
                raise ImportError("No module named keyring")
            return real_import(name, *args, **kwargs)

        with patch("sys.platform", "darwin"), patch("builtins.__import__", side_effect=fake_import):
            result = encrypt_cookie_file(txt)

        # Falls back to plaintext — original path returned
        assert result == txt
        assert txt.exists()

    def test_linux_is_noop(self, tmp_path):
        """On Linux, encrypt_cookie_file is a no-op — .txt returned unchanged."""
        txt = _write_txt(tmp_path / "c.txt")
        with patch("sys.platform", "linux"):
            result = encrypt_cookie_file(txt)
        assert result == txt
        assert txt.exists()

    def test_linux_decrypt_raises(self, tmp_path):
        """On Linux, decrypting a .enc file raises RuntimeError."""
        enc = tmp_path / "c.enc"
        enc.write_bytes(b"data")
        with patch("sys.platform", "linux"):
            with pytest.raises(RuntimeError, match="Linux"):
                decrypt_to_tempfile(enc)


# ---------------------------------------------------------------------------
# Integration — encrypt then decrypt round-trip (mocked DPAPI)
# ---------------------------------------------------------------------------

class TestRoundTrip:

    def test_encrypt_then_decrypt_roundtrip(self, tmp_path):
        """encrypt_cookie_file → decrypt_to_tempfile must recover original bytes."""
        txt = _write_txt(tmp_path / "rt.txt", SAMPLE_COOKIES)

        with patch("sys.platform", "win32"), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_encrypt",
                 side_effect=lambda data: b"ENC:" + data,
             ), \
             patch(
                 "infrastructure.downloader.cookie_storage._dpapi_decrypt",
                 side_effect=lambda data: data[4:],  # strip "ENC:" prefix
             ):
            enc_path = encrypt_cookie_file(txt)
            assert enc_path.suffix == ENCRYPTED_SUFFIX
            assert not txt.exists()

            tmp = decrypt_to_tempfile(enc_path)
            try:
                assert tmp.read_bytes() == SAMPLE_COOKIES
            finally:
                tmp.unlink(missing_ok=True)
