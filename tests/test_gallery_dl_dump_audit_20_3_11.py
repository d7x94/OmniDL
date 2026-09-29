"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - gallery-dl --dump-json."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import infrastructure.downloader.gallery_dl_engine as gdl_mod

_IG_URL = "https://www.instagram.com/p/AbCdEfGhIjK/"


def _real_dump(*entries):
    # gallery-dl 1.32.14 DataJob: one indent=2 array, Url messages are type 3.
    return json.dumps(list(entries), indent=2)


# BUG-GDL-DUMP: the parser wanted one array per line with type 1 (Version), so
# extract_info never returned an item for real gallery-dl output.
def test_parse_dump_json_reads_the_real_pretty_printed_array():
    out = _real_dump([2, {"category": "instagram"}], [3, "https://cdn/x.jpg", {"id": "1", "extension": "jpg"}])
    assert gdl_mod._parse_dump_json(out) == [{"id": "1", "extension": "jpg"}]


def test_parse_dump_json_reads_output_jsonl():
    out = "\n".join(json.dumps(m) for m in ([2, {"a": 1}], [3, "https://cdn/x.jpg", {"id": "1"}]))
    assert gdl_mod._parse_dump_json(out) == [{"id": "1"}]


def test_parse_dump_json_keeps_only_image_entries():
    out = _real_dump(
        [3, "https://cdn/a.jpg", {"id": "img", "extension": "jpg"}],
        [3, "https://cdn/b.mp4?sig=1", {"id": "vid"}],
        [3, "https://cdn/c", {"id": "vid2", "extension": "mp4"}],
    )
    assert [i["id"] for i in gdl_mod._parse_dump_json(out)] == ["img"]


# gallery-dl reports an extractor failure as a [-1, {...}] entry with exit code
# 0 and empty stderr, so extract_info fell through to the generic "no content".
def test_extract_info_surfaces_the_error_entry(tmp_path, monkeypatch):
    err = [-1, {"error": "AuthRequired", "message": "HTTP redirect to login page (https://www.instagram.com/accounts/login/)"}]

    class _Result:
        stdout = _real_dump(err)
        stderr = ""
        returncode = 0

    monkeypatch.setattr(gdl_mod.subprocess, "run", lambda cmd, **kw: _Result())
    engine = gdl_mod.GalleryDlEngine(MagicMock(proxy="", download_dir=tmp_path))
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    with pytest.raises(RuntimeError) as exc:
        engine.extract_info(_IG_URL)
    assert str(exc.value).startswith("login:")


def test_extract_info_reads_uploader_from_real_output(tmp_path, monkeypatch):
    class _Result:
        stdout = _real_dump([3, "https://cdn/x.jpg", {"id": "1", "username": "someone", "extension": "jpg"}])
        stderr = ""
        returncode = 0

    monkeypatch.setattr(gdl_mod.subprocess, "run", lambda cmd, **kw: _Result())
    engine = gdl_mod.GalleryDlEngine(MagicMock(proxy="", download_dir=tmp_path))
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))
    assert engine.extract_info(_IG_URL).uploader == "someone"


# BUG-GDL-LOG-ARGV: the whole argv was logged, including "--proxy user:pass@..."
# and the path of the decrypted cookie temp file.
def test_extract_info_does_not_log_proxy_credentials_or_cookie_path(tmp_path, monkeypatch, caplog):
    import logging

    class _Result:
        stdout = _real_dump([3, "https://cdn/x.jpg", {"id": "1", "extension": "jpg"}])
        stderr = ""
        returncode = 0

    monkeypatch.setattr(gdl_mod.subprocess, "run", lambda cmd, **kw: _Result())
    engine = gdl_mod.GalleryDlEngine(MagicMock(proxy="", download_dir=tmp_path))
    monkeypatch.setattr(
        engine,
        "_base_cmd",
        lambda url: (["gallery-dl", "--cookies", "/tmp/omnidl_dec_secretname.txt", "--proxy", "http://u:hunter2@px:8080"], None),
    )
    with caplog.at_level(logging.DEBUG, logger=gdl_mod.logger.name):
        engine.extract_info(_IG_URL)

    assert "hunter2" not in caplog.text
    assert "omnidl_dec_secretname" not in caplog.text


# BUG-GDL-RATE-SUBSTR: "rate" matched inside "generate"/"operation", so an
# unrelated gallery-dl crash became a hard "blocked:" error with its text lost.
def test_friendly_error_does_not_read_rate_inside_other_words():
    msg = "[facebook][error] An unexpected error occurred: KeyError - 'generate_id'"
    assert gdl_mod._friendly_error(msg) == msg


@pytest.mark.parametrize("msg", ["HTTP Error 429: Too Many Requests", "rate limit exceeded", "Rate-limited by Instagram"])
def test_friendly_error_still_maps_real_rate_limits(msg):
    assert gdl_mod._friendly_error(msg).startswith("blocked:")


# The photo-set probe must never mistake a video for a photo set: a Facebook
# reel share link analysed with yt-dlp has to keep its video (BUG-FB-ADVID).
@pytest.mark.parametrize(
    "stdout",
    [
        _real_dump([3, "ytdl:https://www.facebook.com/page/videos/123", {"id": "123"}]),
        _real_dump([3, "https://cdn/v", {"id": "123", "type": "video"}]),
        _real_dump([3, "https://cdn/v.mp4?sig=1", {"id": "123"}]),
        _real_dump([-1, {"error": "HttpError", "message": "404 Not Found"}]),
    ],
)
def test_extract_info_finds_no_photo_set_in_a_video_only_dump(tmp_path, monkeypatch, stdout):
    class _Result:
        pass

    _Result.stdout = stdout
    _Result.stderr = ""
    _Result.returncode = 0
    monkeypatch.setattr(gdl_mod.subprocess, "run", lambda cmd, **kw: _Result())
    engine = gdl_mod.GalleryDlEngine(MagicMock(proxy="", download_dir=tmp_path))
    monkeypatch.setattr(engine, "_base_cmd", lambda url: (["gallery-dl"], None))

    with pytest.raises(RuntimeError):
        engine.extract_info("https://www.facebook.com/story.php?story_fbid=122127588831380258&id=61")
