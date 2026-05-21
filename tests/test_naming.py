from types import SimpleNamespace

from utils.naming import build_filename, build_filename_from_task, sanitise_for_filesystem


def test_vietnamese_preserved():
    assert (
        build_filename(
            uploader="vanhoangtrung",
            date_label="[LIVE] 2026-05-21 07-11",
            title="Hà Nội",
            video_id="7642126380520",
            ext="ts",
        )
        == "vanhoangtrung - [LIVE] 2026-05-21 07-11 - Hà Nội [7642126380520].ts"
    )


def test_leading_underscore_preserved():
    assert (
        build_filename(
            uploader="_dhs08",
            date_label="[LIVE] 2026-05-21 07-17",
            video_id="7642129109480885012",
            ext="ts",
        )
        == "_dhs08 - [LIVE] 2026-05-21 07-17 [7642129109480885012].ts"
    )


def test_cjk_preserved():
    assert (
        build_filename(
            uploader="某用戶",
            date_label="2026-05-20",
            title="今日放送",
            video_id="205709069714",
            ext="mp4",
        )
        == "某用戶 - 2026-05-20 - 今日放送 [205709069714].mp4"
    )


def test_illegal_chars_stripped():
    assert sanitise_for_filesystem("foo/bar:baz*.mp4") == "foo_bar_baz_.mp4"


def test_synthetic_title_dropped():
    assert (
        build_filename(
            uploader="x",
            date_label="[LIVE] 2026-01-01 10-00",
            title="tiktok-live video #123 2026-01-01_10_00",
            video_id="abc",
            ext="ts",
        )
        == "x - [LIVE] 2026-01-01 10-00 [abc].ts"
    )


def test_suffix():
    assert (
        build_filename(
            uploader="vanhoangtrung",
            date_label="[LIVE] 2026-05-21 07-11",
            title="Hà",
            video_id="7642126380520",
            ext="mp4",
            suffix="_iPhone",
        )
        == "vanhoangtrung - [LIVE] 2026-05-21 07-11 - Hà [7642126380520]_iPhone.mp4"
    )


def test_uploader_at_prefix_title_dropped():
    result = build_filename(
        uploader="someuser",
        date_label="[LIVE] 2026-05-21 08-00",
        title="@someuser -- TikTok Live",
        video_id="123",
        ext="ts",
    )
    assert " - @someuser" not in result
    assert "[123]" in result


def test_trailing_timestamp_stripped_from_title():
    result = build_filename(
        uploader="abc",
        date_label="2026-05-20",
        title="Gift gallery 2026-05-05 09_40",
        video_id="999",
        ext="mp4",
    )
    assert "Gift gallery" in result
    assert "09_40" not in result


def test_no_video_id():
    assert (
        build_filename(
            uploader="user",
            date_label="2026-05-20",
            title="My Video",
            ext="mp4",
        )
        == "user - 2026-05-20 - My Video.mp4"
    )


def test_empty_ext_no_dot():
    result = build_filename(uploader="u", date_label="2026-01-01", ext="")
    assert not result.endswith(".")


def test_build_from_task_uses_existing_filename():
    task = SimpleNamespace(
        filename="/dl/zwe.htet.aung161 - 2026-05-19 - @Pyae Sone Phyo [764142654721].mp4",
        media_info=SimpleNamespace(uploader="", title="", video_id="", is_live=False, was_live=False),
        finished_at=1748000000.0,
        created_at=1748000000.0,
    )
    result = build_filename_from_task(task, ext="mp4")
    assert result == "zwe.htet.aung161 - 2026-05-19 - @Pyae Sone Phyo [764142654721].mp4"


def test_build_from_task_falls_back_when_no_filename():
    task = SimpleNamespace(
        filename="",
        media_info=SimpleNamespace(
            uploader="someuser", title="", video_id="123", is_live=False, was_live=False
        ),
        finished_at=1748008800.0,
        created_at=1748008800.0,
    )
    result = build_filename_from_task(task, ext="mp4")
    assert result.startswith("someuser - ")
    assert "[123]" in result


def test_build_from_task_live_ignores_existing_filename():
    task = SimpleNamespace(
        filename="/dl/some-temp-live-file.ts",
        media_info=SimpleNamespace(
            uploader="liveuser", title="", video_id="abc", is_live=True, was_live=False
        ),
        finished_at=0.0,
        created_at=1748008800.0,
    )
    result = build_filename_from_task(task, is_live=True, live_ts="2026-05-21 08-00", ext="ts")
    assert result.startswith("liveuser - [LIVE]")


def test_cap_utf8_truncates():
    from utils.naming import _cap_utf8

    long = "a" * 200
    result = _cap_utf8(long, 10)
    assert len(result.encode("utf-8")) <= 10


def test_build_from_task_live_no_explicit_ts():
    task = SimpleNamespace(
        filename="",
        media_info=SimpleNamespace(
            uploader="liveuser2", title="", video_id="x", is_live=True, was_live=False
        ),
        finished_at=1748008800.0,
        created_at=1748008800.0,
    )
    result = build_filename_from_task(task, is_live=True, ext="ts")
    assert "[LIVE]" in result
    assert result.endswith(".ts")
