"""
infrastructure/downloader/cookie_storage.py
Cookie file encryption/decryption at rest.

Supported platforms
───────────────────
Windows  — DPAPI (CryptProtectData / CryptUnprotectData)
           Key is derived from the user's Windows login credentials.
           Only the exact same account can decrypt — even Administrator
           on a different account cannot read the data.

macOS    — macOS Keychain + Fernet (AES-128-CBC + HMAC-SHA256)
           A random 32-byte key is generated once and stored in the
           macOS Keychain under service="OmniDL", account="cookie_key_v1".
           Keychain access is protected by the user's login password.
           `cryptography` (Fernet) performs the symmetric encryption.

Linux    — plaintext fallback (Secret Service integration future work)

Design
──────
• `.enc` suffix marks an encrypted file (platform-agnostic).
• `encrypt_cookie_file(path)` encrypts .txt → .enc, deletes .txt.
  Atomic: .enc is fully written BEFORE .txt is deleted.
• `decrypt_to_tempfile(enc_path)` decrypts to omnidl_dec_*.txt temp file.
  Caller MUST delete the temp file in a finally block.
• Graceful fallback: if encryption fails, plaintext is kept and warning logged.

Thread safety
─────────────
All functions are pure (no shared state) and safe to call from any thread.
"""
from __future__ import annotations

import atexit
import logging
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

ENCRYPTED_SUFFIX = ".enc"
_TEMP_PREFIX = "omnidl_dec_"

_KEYCHAIN_SERVICE = "OmniDL"
_KEYCHAIN_ACCOUNT = "cookie_encryption_key_v1"


def _platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


def is_encrypted(path: Path) -> bool:
    """Return True if *path* is an encrypted cookie file (.enc)."""
    return path.suffix == ENCRYPTED_SUFFIX


# ── Windows — DPAPI ───────────────────────────────────────────────────────────

def _dpapi_encrypt(data: bytes) -> "bytes | None":
    import ctypes, ctypes.wintypes

    class _B(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data)
    ib, ob = _B(len(data), buf), _B()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(ib), None, None, None, None, 0, ctypes.byref(ob)
    ):
        logger.warning("CryptProtectData failed (%d)",
                       ctypes.windll.kernel32.GetLastError())
        return None
    result = bytes(ob.pbData[: ob.cbData])
    ctypes.windll.kernel32.LocalFree(ob.pbData)
    return result


def _dpapi_decrypt(data: bytes) -> "bytes | None":
    import ctypes, ctypes.wintypes

    class _B(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data)
    ib, ob = _B(len(data), buf), _B()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(ib), None, None, None, None, 0, ctypes.byref(ob)
    ):
        logger.warning("CryptUnprotectData failed (%d)",
                       ctypes.windll.kernel32.GetLastError())
        return None
    result = bytes(ob.pbData[: ob.cbData])
    ctypes.windll.kernel32.LocalFree(ob.pbData)
    return result


# ── macOS — Keychain + Fernet ─────────────────────────────────────────────────

def _macos_get_or_create_key() -> bytes:
    """Return the 32-byte Fernet key from macOS Keychain, creating one if absent.

    Stored under: service="OmniDL", account="cookie_encryption_key_v1".
    Raises RuntimeError if Keychain is inaccessible.
    """
    import base64, os

    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError(
            "macOS cookie encryption requires the `keyring` package.\n"
            "Run: pip install keyring"
        ) from exc

    stored = keyring.get_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT)
    if stored:
        try:
            key = base64.urlsafe_b64decode(stored.encode())
            if len(key) == 32:
                return key
        except Exception:
            logger.warning("macOS Keychain cookie key corrupted — regenerating")

    # Generate and persist a new key
    new_key = os.urandom(32)
    encoded = base64.urlsafe_b64encode(new_key).decode()
    try:
        keyring.set_password(_KEYCHAIN_SERVICE, _KEYCHAIN_ACCOUNT, encoded)
        logger.info("Created OmniDL cookie encryption key in macOS Keychain")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot write encryption key to macOS Keychain: {exc}\n"
            "Grant OmniDL Keychain access in System Settings → Privacy & Security."
        ) from exc
    return new_key


def _macos_fernet(key: bytes):
    import base64
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(key))


def _macos_encrypt(data: bytes) -> "bytes | None":
    try:
        return _macos_fernet(_macos_get_or_create_key()).encrypt(data)
    except Exception as exc:
        logger.warning("macOS Fernet encrypt failed: %s", exc)
        return None


def _macos_decrypt(data: bytes) -> "bytes | None":
    try:
        return _macos_fernet(_macos_get_or_create_key()).decrypt(data)
    except Exception as exc:
        logger.warning("macOS Fernet decrypt failed: %s", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def encrypt_cookie_file(txt_path: Path) -> Path:
    """Encrypt *txt_path* → sibling .enc file.

    Windows → DPAPI  |  macOS → Fernet+Keychain  |  Linux → no-op
    Atomic: .enc written before .txt deleted.
    On failure: logs warning, returns original .txt path (graceful fallback).
    """
    plat = _platform()
    if plat == "other":
        logger.debug("Cookie encryption skipped (Linux — not yet supported)")
        # Restrict permissions so only the owning user can read the plaintext
        # cookie file (mode 0o600 = rw-------).  On Linux we have no DPAPI/
        # Keychain equivalent, so tight filesystem permissions are the only
        # protection available.
        try:
            import os
            os.chmod(txt_path, 0o600)
        except OSError as exc:
            logger.warning("encrypt_cookie_file: chmod 0o600 failed for %s — %s", txt_path, exc)
        return txt_path

    try:
        plaintext = txt_path.read_bytes()
    except OSError as exc:
        logger.warning("encrypt_cookie_file: cannot read %s — %s", txt_path, exc)
        return txt_path

    if plat == "windows":
        encrypted, method = _dpapi_encrypt(plaintext), "DPAPI"
    else:
        encrypted, method = _macos_encrypt(plaintext), "Fernet/Keychain"

    if encrypted is None:
        logger.warning(
            "encrypt_cookie_file: %s failed — keeping plaintext %s", method, txt_path
        )
        return txt_path

    enc_path = txt_path.with_suffix(ENCRYPTED_SUFFIX)
    try:
        enc_path.write_bytes(encrypted)
    except OSError as exc:
        logger.warning("encrypt_cookie_file: cannot write %s — %s", enc_path, exc)
        return txt_path

    try:
        txt_path.unlink()
    except OSError as exc:
        logger.warning("encrypt_cookie_file: cannot delete plaintext %s — %s", txt_path, exc)

    logger.info("Cookie file encrypted (%s): %s → %s", method, txt_path.name, enc_path.name)
    return enc_path


def decrypt_to_tempfile(enc_path: Path) -> Path:
    """Decrypt *enc_path* → omnidl_dec_*.txt temp file.

    Returns enc_path unchanged if it is not .enc (plaintext fallback).
    Caller MUST delete the returned temp path in a finally block.
    Raises RuntimeError if decryption fails.
    """
    if not is_encrypted(enc_path):
        return enc_path

    plat = _platform()

    if plat == "other":
        raise RuntimeError(
            f"Cannot decrypt {enc_path.name} — encryption not supported on Linux.\n"
            "Delete the .enc file and re-extract cookies."
        )

    try:
        ciphertext = enc_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"Cannot read encrypted cookie file: {exc}") from exc

    if plat == "windows":
        plaintext = _dpapi_decrypt(ciphertext)
        if plaintext is None:
            raise RuntimeError(
                f"DPAPI decryption failed for {enc_path.name}.\n"
                "Cookies may have been extracted under a different Windows user account.\n"
                "Please re-extract cookies on this account."
            )
    else:  # macos
        plaintext = _macos_decrypt(ciphertext)
        if plaintext is None:
            raise RuntimeError(
                f"Fernet decryption failed for {enc_path.name}.\n"
                "The Keychain key may have been lost (e.g. after a Keychain reset).\n"
                "Please re-extract cookies."
            )

    tmp_fd, tmp_str = tempfile.mkstemp(
        suffix=".txt", prefix=_TEMP_PREFIX, dir=enc_path.parent
    )
    tmp_path = Path(tmp_str)
    try:
        import os
        os.write(tmp_fd, plaintext)
    finally:
        import os
        os.close(tmp_fd)
        # Restrict temp file permissions immediately after close so the
        # plaintext window is as narrow as possible (mode 0o600 = rw-------).
        try:
            os.chmod(tmp_path, 0o600)
        except OSError as exc:
            logger.warning("decrypt_to_tempfile: chmod 0o600 failed for %s — %s", tmp_path.name, exc)

    logger.debug("Decrypted %s → temp %s", enc_path.name, tmp_path.name)
    return tmp_path


def cleanup_stale_cookies(safe_dir: Path, max_age_days: int = 30) -> int:
    """Delete .txt and .enc cookie files older than max_age_days. Returns count deleted."""
    import time

    if not safe_dir.is_dir():
        return 0
    now = time.time()
    deleted = 0
    for f in safe_dir.iterdir():
        if f.name.startswith("_tmp_") or f.name.startswith(_TEMP_PREFIX):
            continue
        if f.suffix not in (".txt", ENCRYPTED_SUFFIX):
            continue
        try:
            age_days = (now - f.stat().st_mtime) / 86400
            if age_days > max_age_days:
                f.unlink()
                deleted += 1
                logger.info("Auto-deleted stale cookie file: %s (%.0f days old)",
                            f.name, age_days)
        except OSError:
            pass
    if deleted:
        logger.info("Stale cookie cleanup: %d file(s) removed from %s", deleted, safe_dir)
    return deleted


def cleanup_leftover_temp_files(safe_dir: Path) -> None:
    """Delete omnidl_dec_*.txt files left over from a previous crashed session.

    Also registers an atexit handler so any temp files created in the *current*
    session are cleaned up on normal exit (KeyboardInterrupt, sys.exit, etc.).
    Hard kills (SIGKILL / Task Manager) are not catchable — startup cleanup
    handles those on next launch.
    """
    if not safe_dir.is_dir():
        return

    def _atexit_cleanup(d: Path = safe_dir) -> None:
        for f in d.glob(f"{_TEMP_PREFIX}*.txt"):
            try:
                f.unlink()
                logger.debug("atexit: removed temp cookie file %s", f.name)
            except OSError:
                pass

    atexit.register(_atexit_cleanup)

    for f in safe_dir.glob(f"{_TEMP_PREFIX}*.txt"):
        try:
            f.unlink()
            logger.info("Cleaned up leftover temp cookie file: %s", f.name)
        except OSError:
            pass
