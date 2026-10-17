"""Facebook photo/album downloads + Facebook live monitoring.

Covers the two features added in v20.1.0:
  1. Facebook photo and album URLs route to gallery-dl on both the desktop
     path and the Remote API (yt-dlp cannot parse those URLs at all).
  2. Facebook pages / profiles can be watched in the Live Monitor.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.services.download_service import DownloadService, _should_fallback_to_gallery_dl
from app.services.live_monitor_service import LiveMonitorService
from infrastructure.downloader.gallery_dl_engine import is_facebook_photo_url
from utils.facebook_live_checker import (
    check_facebook_live,
    extract_facebook_username,
    is_facebook_profile_url,
)

# ---------------------------------------------------------------------------
# 1. Facebook photo / album URL classification
# ---------------------------------------------------------------------------

_PHOTO_URLS = [
    "https://www.facebook.com/photo/?fbid=123&set=a.456",
    "https://www.facebook.com/photo.php?fbid=123",
    "https://www.facebook.com/media/set/?set=a.10150146071605987",
    "https://www.facebook.com/someuser/photos/a.123/456/",
    "https://www.facebook.com/someuser/photos_by",
    "https://www.facebook.com/someuser/photos_albums/123",
]

_NON_PHOTO_URLS = [
    "https://www.facebook.com/someuser/videos/123456789",
    "https://www.facebook.com/watch/?v=123456789",
    "https://www.facebook.com/someuser/posts/123456789",
    "https://fb.watch/abc123/",
    "https://www.instagram.com/p/ABC123/",
]


@pytest.mark.parametrize("url", _PHOTO_URLS)
def test_facebook_photo_urls_detected(url):
    assert is_facebook_photo_url(url) is True


@pytest.mark.parametrize("url", _NON_PHOTO_URLS)
def test_non_photo_urls_not_detected(url):
    assert is_facebook_photo_url(url) is False


def test_unsupported_url_error_falls_back_for_facebook_photo():
    """yt-dlp reports 'Unsupported URL' for album links — still a gallery-dl job."""
    assert (
        _should_fallback_to_gallery_dl(
            "https://www.facebook.com/media/set/?set=a.123",
            "ERROR: Unsupported URL: https://www.facebook.com/media/set/?set=a.123",
        )
        is True
    )


def test_unsupported_url_error_does_not_fall_back_for_facebook_video():
    assert (
        _should_fallback_to_gallery_dl(
            "https://www.facebook.com/someuser/videos/123",
            "ERROR: Unsupported URL",
        )
        is False
    )


# ---------------------------------------------------------------------------
# 2. analyse_url routes Facebook photo URLs straight to gallery-dl
# ---------------------------------------------------------------------------


def test_analyse_url_uses_gallery_engine_for_facebook_album():
    from domain.models.download_task import MediaInfo

    svc = DownloadService.__new__(DownloadService)
    svc._bus = MagicMock()
    gallery = MagicMock()
    gallery.extract_info.return_value = MediaInfo(
        url="https://www.facebook.com/media/set/?set=a.9",
        title="Album",
        platform="Facebook",
        source_engine="gallery_dl",
    )
    svc._gallery_engine = gallery

    done = threading.Event()
    result: dict = {}

    svc.analyse_url(
        url="https://www.facebook.com/media/set/?set=a.9",
        on_done=lambda info: (result.update(info=info), done.set()),
        on_error=lambda err: (result.update(err=err), done.set()),
    )
    assert done.wait(timeout=5)

    assert "err" not in result
    assert result["info"].source_engine == "gallery_dl"
    gallery.extract_info.assert_called_once()


def test_download_manager_forces_gallery_engine_for_facebook_album():
    """A Remote API client sending the default source_engine must still get gallery-dl."""
    from domain.models.download_task import DownloadTask, MediaInfo
    from infrastructure.downloader.download_manager import DownloadManager

    cfg = MagicMock(max_concurrent=1, max_retries=1)
    yt_engine = MagicMock()
    gallery = MagicMock()
    manager = DownloadManager(
        config=cfg, engine=yt_engine, event_bus=MagicMock(), gallery_engine=gallery
    )

    url = "https://www.facebook.com/media/set/?set=a.9"
    task = DownloadTask(url=url)
    task.media_info = MediaInfo(url=url, title="Album", source_engine="yt_dlp")

    manager._run_task(task)

    gallery.download.assert_called_once()
    yt_engine.download.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Facebook profile URL classification for the Live Monitor
# ---------------------------------------------------------------------------

_PROFILE_URLS = {
    "https://www.facebook.com/somepage": "somepage",
    "https://www.facebook.com/somepage/live": "somepage",
    "https://www.facebook.com/profile.php?id=100012345678": "100012345678",
    "https://www.facebook.com/people/Some-Name/61550000000000/": "61550000000000",
}


@pytest.mark.parametrize(("url", "username"), list(_PROFILE_URLS.items()))
def test_facebook_profile_urls(url, username):
    assert is_facebook_profile_url(url) is True
    assert extract_facebook_username(url) == username


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/watch/?v=1",
        "https://www.facebook.com/somepage/videos/1",
        "https://www.facebook.com/groups/123",
        "https://www.facebook.com/stories/123",
        "https://www.facebook.com/photo/?fbid=1",
        "https://www.instagram.com/someone/",
    ],
)
def test_non_profile_urls_rejected(url):
    assert is_facebook_profile_url(url) is False


# ---------------------------------------------------------------------------
# 4. LiveMonitorService classification + dispatch
# ---------------------------------------------------------------------------


class _StubService:
    def __init__(self, live_url=None):
        self.live_url = live_url
        self.facebook_calls: list[str] = []
        self.instagram_calls: list[str] = []

    def check_facebook_profile_live(self, url, on_done, on_error):
        self.facebook_calls.append(url)
        on_done(self.live_url)

    def check_profile_live(self, url, on_done, on_error, deep=False):
        self.instagram_calls.append(url)
        on_done(None)

    def analyse_url(self, url, on_done, on_error):
        on_error("not currently live")


def _monitor(service):
    return LiveMonitorService(service, MagicMock(), broadcast=None)  # type: ignore[arg-type]


def test_facebook_page_is_classified_as_profile_watch():
    svc = _monitor(_StubService())
    with patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value="c.txt"):
        item = svc.add_url("https://www.facebook.com/somepage/live")

    assert item["is_profile_watch"] is True
    assert item["platform"] == "facebook"
    assert item["username"] == "somepage"
    # /live is normalised away so the same page cannot be watched twice.
    assert item["url"] == "https://www.facebook.com/somepage"


def test_facebook_watch_requires_cookie():
    svc = _monitor(_StubService())
    with (
        patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=None),
        pytest.raises(ValueError),
    ):
        svc.add_url("https://www.facebook.com/somepage")


def test_facebook_check_uses_facebook_checker_not_instagram():
    service = _StubService(live_url=None)
    svc = _monitor(service)
    with patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value="c.txt"):
        svc.add_url("https://www.facebook.com/somepage")

    svc._poll()

    assert service.facebook_calls == ["https://www.facebook.com/somepage"]
    assert service.instagram_calls == []


# ---------------------------------------------------------------------------
# 5. check_facebook_live
# ---------------------------------------------------------------------------


def _cookie_file(tmp_path, *, with_session=True):
    path = tmp_path / "fb_cookies.txt"
    rows = ["# Netscape HTTP Cookie File"]
    if with_session:
        rows += [
            "\t".join([".facebook.com", "TRUE", "/", "TRUE", "0", "c_user", "100012345678"]),
            "\t".join([".facebook.com", "TRUE", "/", "TRUE", "0", "xs", "abc%3Adef"]),
        ]
    else:
        rows.append("\t".join([".facebook.com", "TRUE", "/", "TRUE", "0", "datr", "xyz"]))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def test_check_facebook_live_requires_cookie_file():
    with pytest.raises(RuntimeError, match="login:"):
        check_facebook_live(username="somepage", cookie_file="")


def test_check_facebook_live_rejects_cookie_without_session(tmp_path):
    with pytest.raises(RuntimeError, match="login:"):
        check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path, with_session=False))


def _fake_session(pages):
    """pages: list of (status_code, url, text) served in call order."""
    calls = iter(pages)
    session = MagicMock()

    def _get(url, **_kwargs):
        status, final_url, text = next(calls)
        resp = MagicMock()
        resp.status_code = status
        resp.url = final_url
        resp.text = text
        return resp

    session.get.side_effect = _get
    return session


def test_check_facebook_live_returns_video_url_when_live(tmp_path):
    html = '{"is_live_streaming":true,"video_id":"1234567890"}'
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=_fake_session([(200, "https://www.facebook.com/somepage/live/", html)]),
    ):
        url = check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path))

    assert url == "https://www.facebook.com/somepage/videos/1234567890"


def test_check_facebook_live_returns_none_when_offline(tmp_path):
    offline = '{"is_live_streaming":false}'
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=_fake_session(
            [
                (200, "https://www.facebook.com/somepage/live/", offline),
                (200, "https://www.facebook.com/somepage", offline),
            ]
        ),
    ):
        assert check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path)) is None


def test_check_facebook_live_falls_back_to_profile_page(tmp_path):
    live_html = '{"broadcast_status":"LIVE","broadcast_id":"9876543210"}'
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=_fake_session(
            [
                (200, "https://www.facebook.com/somepage/live/", "<html>past broadcasts</html>"),
                (200, "https://www.facebook.com/somepage", live_html),
            ]
        ),
    ):
        url = check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path))

    assert url == "https://www.facebook.com/somepage/videos/9876543210"


def test_check_facebook_live_rate_limit_is_blocked_error(tmp_path):
    with (
        patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=_fake_session([(429, "https://www.facebook.com/somepage/live/", "")]),
        ),
        pytest.raises(RuntimeError, match="blocked:"),
    ):
        check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path))


def test_check_facebook_live_login_wall_is_login_error(tmp_path):
    with (
        patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=_fake_session([(200, "https://www.facebook.com/login/?next=x", "")]),
        ),
        pytest.raises(RuntimeError, match="login:"),
    ):
        check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path))


def test_check_facebook_live_without_video_id_reports_offline(tmp_path):
    """A live marker with no resolvable id must not enqueue an undownloadable task."""
    html = '{"is_live_streaming":true}'
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=_fake_session([(200, "https://www.facebook.com/somepage/live/", html)]),
    ):
        assert check_facebook_live(username="somepage", cookie_file=_cookie_file(tmp_path)) is None


# ---------------------------------------------------------------------------
# 6. DownloadService.check_facebook_profile_live
# ---------------------------------------------------------------------------


def test_check_facebook_profile_live_rejects_unparseable_url():
    svc = DownloadService.__new__(DownloadService)
    svc._config = MagicMock(proxy="")
    errors: list[str] = []

    svc.check_facebook_profile_live(
        url="https://www.facebook.com/watch/?v=1",
        on_done=lambda _u: None,
        on_error=errors.append,
    )

    assert errors and "Facebook" in errors[0]
