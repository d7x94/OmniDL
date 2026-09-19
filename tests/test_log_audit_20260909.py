"""Regression guards for the 2026-09-09 omnidl_debug.log audit (15:02-15:31 window)."""

from __future__ import annotations

import pytest

from domain.models.download_task import DownloadTask
from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.gallery_dl_engine import normalize_gallery_dl_url

_STORY_URL = (
    "https://www.facebook.com/story.php"
    "?story_fbid=122123362857380258&id=61591407741159&mibextid=wwXIfr"
)
_POSTS_URL = "https://www.facebook.com/61591407741159/posts/122123362857380258"


# ── BUG-FB-SETID: gallery-dl mis-dispatches story.php as a profile ───────────


def test_story_php_is_rewritten_to_posts_form():
    assert normalize_gallery_dl_url(_STORY_URL) == _POSTS_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/ABC123/",
        "https://www.facebook.com/photo/?fbid=1234567890",
        "https://www.facebook.com/watch/?v=1234567890",
    ],
)
def test_other_urls_pass_through_unchanged(url):
    assert normalize_gallery_dl_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/story.php?story_fbid=notanumber&id=61591407741159",
        "https://www.facebook.com/story.php?id=61591407741159",
        "https://www.facebook.com/story.php?story_fbid=122123362857380258",
    ],
)
def test_incomplete_story_php_is_left_alone(url):
    """Better to hand gallery-dl the original URL than to invent a bogus post id."""
    assert normalize_gallery_dl_url(url) == url


def test_rewritten_url_reaches_the_right_gallery_dl_extractor():
    extractor = pytest.importorskip("gallery_dl.extractor")
    # The bug: story.php is captured as a username by gallery-dl's USER_PATTERN.
    assert type(extractor.find(_STORY_URL)).__name__ == "FacebookUserExtractor"
    assert type(extractor.find(_POSTS_URL)).__name__ == "FacebookSetExtractor"


# ── gallery-dl extractor crashes must not be retried ─────────────────────────


def test_gallery_dl_unexpected_error_is_a_hard_error():
    msg = "[facebook][error] An unexpected error occurred: KeyError - 'set_id'.".lower()
    assert any(k in msg for k in DownloadManager._HARD_ERROR_KEYWORDS)


def test_transient_network_error_stays_retryable():
    msg = "unable to download webpage: read timed out after 30s"
    assert not any(k in msg for k in DownloadManager._HARD_ERROR_KEYWORDS)


# ── BUG-TT-EFF-FP: an empty pp_hook event is not a vcodec reading ────────────


def _capture(events: "list[dict]") -> "list[str]":
    """Mirror of the _selected_vcodec capture in YtDlpEngine._capturing_pp_hook."""
    selected: list[str] = []
    for info in events:
        if not selected:
            vcodec = info.get("vcodec") or ""
            if vcodec:
                selected.append(vcodec)
    return selected


def test_pre_process_event_without_vcodec_is_ignored():
    # _FacebookMetaFixupPP fires first with an empty info_dict; VideoRemuxer follows.
    selected = _capture([{}, {"vcodec": "h264", "acodec": "aac"}])
    assert selected == ["h264"]
    # BUG-TT-EFF must not fire — it would FFprobe and then delete a good file.
    assert selected[0].lower() not in ("none", "")


def test_real_video_less_stream_still_trips_the_guard():
    selected = _capture([{}, {"vcodec": "none", "acodec": "aac"}])
    assert selected == ["none"]
    assert selected[0].lower() in ("none", "")


def test_no_finished_event_leaves_the_guard_disarmed():
    assert _capture([]) == []


# ── Retry-aware error logging ────────────────────────────────────────────────


def test_has_retry_remaining_defaults_to_false():
    """Default False so a single-attempt download still logs failures at ERROR."""
    assert DownloadTask(url="https://example.com/v").has_retry_remaining is False
