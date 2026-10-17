"""BUG-COOKIE-TMP-JAR (v20.3.14): extract_browser_cookies() writes the whole
browser jar to "_tmp_<name>" first. A crash mid-extraction left it on disk
forever: startup cleanup ignored it and the migration re-encrypted it."""

from infrastructure.downloader import cookie_storage as cs


def _startup(safe_dir):
    cs.cleanup_leftover_temp_files(safe_dir)
    cs.encrypt_plaintext_cookies(safe_dir)
    cs.cleanup_stale_cookies(safe_dir, max_age_days=30, keep=set())


def test_startup_removes_leftover_tmp_jar(tmp_path):
    leftover = tmp_path / "_tmp_tiktok_chrome_cookies.txt"
    leftover.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    _startup(tmp_path)
    assert not leftover.exists()
    assert not (tmp_path / "_tmp_tiktok_chrome_cookies.enc").exists()


def test_startup_removes_leftover_encrypted_tmp_jar(tmp_path):
    leftover = tmp_path / "_tmp_tiktok_chrome_cookies.enc"
    leftover.write_bytes(b"\x01\x02")
    _startup(tmp_path)
    assert not leftover.exists()


def test_startup_keeps_real_jars(tmp_path):
    real = tmp_path / "tiktok_chrome_cookies.txt"
    real.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    _startup(tmp_path)
    assert real.exists() or real.with_suffix(".enc").exists()
