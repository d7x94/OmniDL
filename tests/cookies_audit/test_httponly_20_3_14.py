"""BUG-COOKIE-HTTPONLY (v20.3.14): Netscape files exported by browser extensions
prefix HttpOnly cookies with "#HttpOnly_". Those lines are cookies, not comments."""

from infrastructure.downloader.yt_dlp_engine import _build_ffmpeg_cookie_header

_JAR = (
    "# Netscape HTTP Cookie File\n"
    "# a real comment line\n"
    ".tiktok.com\tTRUE\t/\tTRUE\t4102444800\ttt_csrf_token\tFAKECSRF\n"
    "#HttpOnly_.tiktok.com\tTRUE\t/\tTRUE\t4102444800\tsessionid\tFAKESESSION\n"
)


def test_ffmpeg_header_keeps_httponly_cookies(tmp_path):
    jar = tmp_path / "tt.txt"
    jar.write_text(_JAR, encoding="utf-8")
    hdr = _build_ffmpeg_cookie_header(str(jar), "tiktok")
    assert "sessionid=FAKESESSION" in hdr, hdr
    assert "tt_csrf_token=FAKECSRF" in hdr


def test_ffmpeg_header_still_skips_comments(tmp_path):
    jar = tmp_path / "tt.txt"
    jar.write_text(_JAR, encoding="utf-8")
    assert "comment" not in _build_ffmpeg_cookie_header(str(jar), "tiktok")


def test_pool_health_reads_httponly_session(tmp_path):
    import infrastructure.downloader.account_pool as pool

    jar = tmp_path / "tiktok_pool_x.txt"
    jar.write_text(_JAR, encoding="utf-8")
    health = pool.inspect_tiktok_cookie(str(jar))
    assert health.status == "ok", health
    assert health.count == 2
