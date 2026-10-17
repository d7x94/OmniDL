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


# ── to_ascii_filename (BUG-TD-NAME) ──────────────────────────────────────
# Regression guard for the Taildrop fallback name.  The old code used
# encode("ascii", "ignore"), which deleted characters instead of
# transliterating them and produced unreadable names on the iPhone.


def test_ascii_fallback_transliterates_vietnamese():
    from utils.naming import to_ascii_filename

    result = to_ascii_filename(
        "1965dreamfootball - 2026-08-27 - YUSUKI cậu ấy thật đáng yêu [7678718864875719954].mp4"
    )
    assert result == (
        "1965dreamfootball - 2026-08-27 - YUSUKI cau ay that dang yeu [7678718864875719954].mp4"
    )


def test_ascii_fallback_drops_emoji_without_double_space():
    from utils.naming import to_ascii_filename

    result = to_ascii_filename("ALEE ❗️ - 2026-08-28 - Video by aleeshgt_ [DckY25PMpqH].mp4")
    assert result == "ALEE - 2026-08-28 - Video by aleeshgt_ [DckY25PMpqH].mp4"


def test_ascii_fallback_drops_empty_cjk_segment():
    from utils.naming import to_ascii_filename

    result = to_ascii_filename("家有兩兄妹 - 2026-06-10 - Video [1661273081771069].mp4")
    assert not result.startswith("-")
    assert result == "2026-06-10 - Video [1661273081771069].mp4"


def test_ascii_fallback_never_empty():
    from utils.naming import to_ascii_filename

    assert to_ascii_filename("家有兩兄妹.mp4") == "file.mp4"


def test_ascii_fallback_leaves_ascii_untouched():
    from utils.naming import to_ascii_filename

    name = "user - 2026-01-01 - My Video [abc123].mp4"
    assert to_ascii_filename(name) == name


def test_control_chars_stripped():
    assert sanitise_for_filesystem("bad\nname\tx.mp4") == "bad_name_x.mp4"


def test_sanitise_strips_invisible_emoji_tag_characters():
    """Taildrop peers answer 400 for the invisible U+E00xx payload of flag emoji.

    The visible flag survives; only the tag characters go.  Regression for the
    2026-09-02 12:11:08 / 2026-09-03 08:03:48 rejections of
    "Морган Ерболат 🏴\U000e0067\U000e0062\U000e0065\U000e006e - ... .mp4",
    whose ASCII retry dropped the Cyrillic uploader name entirely.
    """
    raw = "Морган Ерболат \U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e - 2026-08-31.mp4"
    out = sanitise_for_filesystem(raw)
    assert out == "Морган Ерболат \U0001f3f4 - 2026-08-31.mp4"
    assert all(not (0xE0000 <= ord(c) <= 0xE007F) for c in out)


def test_empty_uploader_keeps_title():
    # "" is a prefix of every string — an empty uploader must not drop the title.
    result = build_filename(uploader="", date_label="2026-01-01", title="My Clip", ext="mp4")
    assert "My Clip" in result


def test_build_from_task_rebuilds_name_for_directory_output(tmp_path):
    # gallery-dl albums land in a directory named after the account only;
    # that name carries no date/title/id, so it must not be reused verbatim.
    album = tmp_path / "aleeshgt_"
    album.mkdir()
    task = SimpleNamespace(
        filename=str(album),
        media_info=SimpleNamespace(
            uploader="aleeshgt_", title="Beach day", video_id="DckY25PMpqH",
            is_live=False, was_live=False,
        ),
        finished_at=1748008800.0,
        created_at=1748008800.0,
    )
    result = build_filename_from_task(task, ext="")
    assert result.startswith("aleeshgt_ - ")
    assert "Beach day" in result
    assert "[DckY25PMpqH]" in result


def test_build_from_task_keeps_full_tiktok_id():
    task = SimpleNamespace(
        filename="",
        media_info=SimpleNamespace(
            uploader="u", title="", video_id="7678718864875719954", is_live=False, was_live=False
        ),
        finished_at=1748008800.0,
        created_at=1748008800.0,
    )
    assert "[7678718864875719954]" in build_filename_from_task(task, ext="mp4")
