"""
tests/test_kuaishou_engine.py
Unit tests for the fixes applied to infrastructure/downloader/kuaishou_engine.py:
  - hlsPlayUrl no longer selected as a downloadable URL
  - _clean_caption() simplified signature (no unused photo_id/uploader/cdn_url)
  - _looks_like_photo_id() id-vs-URL guard
  - _build_output_path() filename/bracket/collision-avoidance
  - _strategy_kwai() sends the Cookie header
  - _EXTRACT_BUDGET_S caps the strategy-E timeout
"""

from unittest.mock import MagicMock

import infrastructure.downloader.kuaishou_engine as ks_mod
from domain.models.download_task import MediaInfo


class TestPickBestVideoUrl:
    def test_prefers_photo_url(self):
        photo = {"photoUrl": "https://cdn.example/video.mp4", "hlsPlayUrl": "https://cdn.example/x.m3u8"}
        assert ks_mod._pick_best_video_url(photo) == "https://cdn.example/video.mp4"

    def test_does_not_fall_back_to_hls(self):
        # BUG-KS-HLS: hlsPlayUrl is an m3u8 playlist that download() cannot
        # turn into a valid MP4 by raw byte-copy — it must never be selected.
        photo = {"hlsPlayUrl": "https://cdn.example/x.m3u8"}
        assert ks_mod._pick_best_video_url(photo) is None

    def test_falls_back_to_main_mv_urls(self):
        photo = {"mainMvUrls": [{"url": "https://cdn.example/legacy.mp4"}]}
        assert ks_mod._pick_best_video_url(photo) == "https://cdn.example/legacy.mp4"

    def test_falls_back_to_video_resource_h264(self):
        photo = {
            "videoResource": {
                "h264": {
                    "adaptationSet": [
                        {
                            "representation": [
                                {"url": "https://cdn.example/low.mp4", "avgBitrate": 500},
                                {"url": "https://cdn.example/high.mp4", "avgBitrate": 2000},
                            ]
                        }
                    ]
                }
            }
        }
        assert ks_mod._pick_best_video_url(photo) == "https://cdn.example/high.mp4"

    def test_no_usable_url_returns_none(self):
        assert ks_mod._pick_best_video_url({}) is None


class TestCleanCaption:
    def test_empty_caption_falls_back(self):
        assert ks_mod._clean_caption("") == "kuaishou"

    def test_strips_mentions_and_hashtags(self):
        text = ks_mod._clean_caption("hello @user(O3xrgtux2ehryffe) world #tag1 #tag2")
        assert "@" not in text
        assert "#" not in text
        assert "hello" in text and "world" in text

    def test_pure_cjk_falls_back(self):
        assert ks_mod._clean_caption("你好世界") == "kuaishou"

    def test_ascii_content_preserved(self):
        assert ks_mod._clean_caption("simple title") == "simple title"


class TestLooksLikePhotoId:
    def test_valid_id(self):
        assert ks_mod._looks_like_photo_id("3x78s79ptbs94km")

    def test_empty_string(self):
        assert not ks_mod._looks_like_photo_id("")

    def test_full_url_rejected(self):
        # BUG-KS-VIDID: a full URL must never be treated as a bare photo id.
        assert not ks_mod._looks_like_photo_id("https://cdn.kwaicdn.com/video.mp4")

    def test_too_short_rejected(self):
        assert not ks_mod._looks_like_photo_id("abc")


class TestBuildOutputPath:
    def test_appends_id_bracket(self, tmp_path):
        media_info = MediaInfo(url="https://cdn.example/v.mp4", title="My Video", video_id="abc123456789")
        filename, part_path = ks_mod._build_output_path(tmp_path, media_info)
        assert filename.name == "My Video [abc123456789].mp4"
        assert part_path == filename.with_suffix(".part")

    def test_no_id_no_bracket(self, tmp_path):
        media_info = MediaInfo(url="https://cdn.example/v.mp4", title="My Video", video_id="")
        filename, _ = ks_mod._build_output_path(tmp_path, media_info)
        assert filename.name == "My Video.mp4"

    def test_avoids_collision(self, tmp_path):
        media_info = MediaInfo(url="https://cdn.example/v.mp4", title="dup", video_id="id123456")
        first, _ = ks_mod._build_output_path(tmp_path, media_info)
        first.write_bytes(b"x")
        second, _ = ks_mod._build_output_path(tmp_path, media_info)
        assert second != first
        assert second.name == "dup [id123456] (1).mp4"

    def test_long_title_capped(self, tmp_path):
        media_info = MediaInfo(url="https://cdn.example/v.mp4", title="a" * 300, video_id="idxxxxxxxxxx")
        filename, _ = ks_mod._build_output_path(tmp_path, media_info)
        assert len(filename.stem) <= 180


class TestStrategyKwaiCookies:
    def test_sends_cookie_header_when_present(self):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, json=lambda: {"photo": {"id": "x"}})
        ks_mod._strategy_kwai(session, "photo123", cookie_str="did=abc; userId=xyz")
        _, kwargs = session.post.call_args
        assert kwargs["headers"]["Cookie"] == "did=abc; userId=xyz"

    def test_no_cookie_header_when_absent(self):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=200, json=lambda: {"photo": {"id": "x"}})
        ks_mod._strategy_kwai(session, "photo123", cookie_str="")
        _, kwargs = session.post.call_args
        assert "Cookie" not in kwargs["headers"]


class TestExtractBudget:
    def test_cdp_timeout_shrinks_with_elapsed_time(self, monkeypatch):
        # BUG-KS-BUDGET: strategy E's timeout must respect the shared budget,
        # not always request the full window regardless of time already spent.
        monkeypatch.setattr(ks_mod, "_resolve_short_url", lambda url, session: url)
        monkeypatch.setattr(ks_mod, "_extract_photo_id", lambda url: "photo123")
        monkeypatch.setattr(ks_mod, "_strategy_html", lambda *a, **kw: None)
        monkeypatch.setattr(ks_mod, "_strategy_gql", lambda *a, **kw: None)
        monkeypatch.setattr(ks_mod, "_strategy_kwai", lambda *a, **kw: None)
        monkeypatch.setattr(ks_mod, "_strategy_mobile", lambda *a, **kw: None)
        monkeypatch.setattr(ks_mod, "_load_cookie_str", lambda config: "")

        captured = {}

        def fake_cdp(resolved, config, timeout=None, cancel_event=None):
            captured["timeout"] = timeout
            return None

        monkeypatch.setattr(ks_mod, "_strategy_cdp", fake_cdp)

        # Simulate ~100s already elapsed before strategy E starts.
        times = iter([1000.0, 1100.0])
        monkeypatch.setattr(ks_mod.time, "monotonic", lambda: next(times, 1100.0))

        try:
            ks_mod.extract_info_kuaishou("https://v.kuaishou.com/abc", config=None)
        except RuntimeError:
            pass  # all strategies return None -> raises; we only care about the timeout passed

        assert captured["timeout"] == max(20.0, ks_mod._EXTRACT_BUDGET_S - 100.0)
        assert captured["timeout"] < ks_mod._EXTRACT_BUDGET_S
