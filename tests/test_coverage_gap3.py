"""Coverage gap tests — batch 3.

Targets:
- utils/tiktok_detection/dispatcher.py lines 69-74
- utils/tiktok_detection/strategies/pass1_profile_page.py lines 29-30, 46-51
- utils/tiktok_detection/strategies/pass2_live_page.py lines 35-37, 42-47
- app/services/download_service.py lines 469, 489-490, 492-503
- infrastructure/downloader/download_manager.py line 99
- infrastructure/downloader/yt_dlp_engine.py lines 907, 910, 1031, 1033
- app/services/thumbnail_service.py lines 59-60, 182-184
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Pass-1 profile page strategy
# ---------------------------------------------------------------------------


class TestPass1ProfilePage:
    def _make_ctx(self, **kw):
        from utils.tiktok_detection.context import LiveCheckContext

        return LiveCheckContext(username=kw.pop("username", "p1user"), **kw)

    def test_fetch_fails_returns_none(self):
        from utils.tiktok_detection.strategies.pass1_profile_page import Pass1ProfilePage

        strategy = Pass1ProfilePage()
        ctx = self._make_ctx()
        with (
            patch("utils.tiktok_live_checker._fetch_tiktok_profile_page", return_value=None),
            patch("utils.tiktok_live_checker._room_id_from_profile_page", return_value=(None, False)),
            patch("utils.tiktok_live_checker._verify_room_alive", return_value=False),
        ):
            result = strategy.check(ctx)
        assert result is None

    def test_verify_alive_false_returns_none(self):
        from utils.tiktok_detection.strategies.pass1_profile_page import Pass1ProfilePage

        strategy = Pass1ProfilePage()
        ctx = self._make_ctx()
        with (
            patch("utils.tiktok_live_checker._fetch_tiktok_profile_page", return_value="<html>page</html>"),
            patch("utils.tiktok_live_checker._room_id_from_profile_page", return_value=("12345", False)),
            patch("utils.tiktok_live_checker._verify_room_alive", return_value=False),
        ):
            result = strategy.check(ctx)
        assert result is None

    def test_stream_ended_raises(self):
        from utils.tiktok_detection.strategies.pass1_profile_page import Pass1ProfilePage
        from utils.tiktok_detection.strategy import StreamConfirmedEndedError

        strategy = Pass1ProfilePage()
        ctx = self._make_ctx()
        with (
            patch("utils.tiktok_live_checker._fetch_tiktok_profile_page", return_value="<html>page</html>"),
            patch("utils.tiktok_live_checker._room_id_from_profile_page", return_value=("", True)),
        ):
            with pytest.raises(StreamConfirmedEndedError):
                strategy.check(ctx)


# ---------------------------------------------------------------------------
# Pass-2 live page strategy
# ---------------------------------------------------------------------------


class TestPass2LivePage:
    def _make_ctx(self, **kw):
        from utils.tiktok_detection.context import LiveCheckContext

        return LiveCheckContext(username=kw.pop("username", "p2user"), **kw)

    def test_live_page_none_returns_none(self):
        from utils.tiktok_detection.strategies.pass2_live_page import Pass2LivePage

        strategy = Pass2LivePage()
        ctx = self._make_ctx()
        with (
            patch("utils.tiktok_live_checker._fetch_tiktok_live_page", return_value=None),
            patch("utils.tiktok_live_checker._room_id_from_live_page", return_value=None),
            patch("utils.tiktok_live_checker._verify_room_alive", return_value=False),
        ):
            result = strategy.check(ctx)
        assert result is None

    def test_no_room_with_cookie_retries_without(self):
        from utils.tiktok_detection.strategies.pass2_live_page import Pass2LivePage

        strategy = Pass2LivePage()
        ctx = self._make_ctx(cookie_file="/tmp/cook.txt")
        call_count = {"n": 0}

        def fake_fetch(username, proxy="", cookie_file=""):
            call_count["n"] += 1
            return "<html>page</html>"

        with (
            patch("utils.tiktok_live_checker._fetch_tiktok_live_page", side_effect=fake_fetch),
            patch("utils.tiktok_live_checker._room_id_from_live_page", return_value=None),
            patch("utils.tiktok_live_checker._verify_room_alive", return_value=False),
        ):
            result = strategy.check(ctx)
        assert result is None
        assert call_count["n"] == 2

    def test_verify_alive_false_returns_none(self):
        from utils.tiktok_detection.strategies.pass2_live_page import Pass2LivePage

        strategy = Pass2LivePage()
        ctx = self._make_ctx()
        with (
            patch("utils.tiktok_live_checker._fetch_tiktok_live_page", return_value="<html>live</html>"),
            patch("utils.tiktok_live_checker._room_id_from_live_page", return_value="99991"),
            patch("utils.tiktok_live_checker._verify_room_alive", return_value=False),
        ):
            result = strategy.check(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Dispatcher — pending futures exception paths
# ---------------------------------------------------------------------------


class TestDispatcherPendingExceptions:
    def _make_health(self, enabled=True):
        from utils.tiktok_detection.health import StrategyHealthRegistry

        h = StrategyHealthRegistry()
        return h

    def _make_ctx(self):
        from utils.tiktok_detection.context import LiveCheckContext

        return LiveCheckContext(username="dispuser")

    def _make_strategy(self, name, fn, requires_share=False):
        from utils.tiktok_detection.strategy import LiveDetectionStrategy

        _fn = fn

        class S(LiveDetectionStrategy):
            def check(self, ctx):
                return _fn()

            def can_run(self, ctx):
                return True

        S.name = name
        S.requires_share_url = requires_share
        return S()

    def test_pending_stream_confirmed_ended(self):
        from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher
        from utils.tiktok_detection.strategy import StreamConfirmedEndedError

        # fast returns None immediately; slow raises StreamConfirmedEndedError after delay
        fast = self._make_strategy("fast_s", lambda: None)
        slow_event = threading.Event()

        def slow_fn():
            slow_event.wait(timeout=5.0)
            raise StreamConfirmedEndedError("ended")

        slow = self._make_strategy("slow_s", slow_fn)

        health = self._make_health()
        dispatcher = LiveDetectionDispatcher([fast, slow], health)
        ctx = self._make_ctx()

        # Run dispatcher in background, release slow after a brief moment
        result_container = []

        def run():
            result_container.append(dispatcher.check(ctx))

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.05)
        slow_event.set()
        t.join(timeout=3.0)
        assert result_container and result_container[0] is None

    def test_pending_runtime_error_propagates(self):
        from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher

        fast = self._make_strategy("fast_r", lambda: None)
        slow_event = threading.Event()

        def slow_fn():
            slow_event.wait(timeout=5.0)
            raise RuntimeError("network error from pending")

        slow = self._make_strategy("slow_r", slow_fn)

        health = self._make_health()
        dispatcher = LiveDetectionDispatcher([fast, slow], health)
        ctx = self._make_ctx()

        result_container = []
        exc_container = []

        def run():
            try:
                result_container.append(dispatcher.check(ctx))
            except RuntimeError as e:
                exc_container.append(e)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.05)
        slow_event.set()
        t.join(timeout=3.0)
        assert exc_container and "network error from pending" in str(exc_container[0])

    def test_pending_generic_exception_logged(self):
        from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher

        fast = self._make_strategy("fast_g", lambda: None)
        slow_event = threading.Event()

        def slow_fn():
            slow_event.wait(timeout=5.0)
            raise ValueError("unexpected generic error")

        slow = self._make_strategy("slow_g", slow_fn)

        health = self._make_health()
        dispatcher = LiveDetectionDispatcher([fast, slow], health)
        ctx = self._make_ctx()

        result_container = []

        def run():
            result_container.append(dispatcher.check(ctx))

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.05)
        slow_event.set()
        t.join(timeout=3.0)
        assert result_container and result_container[0] is None


# ---------------------------------------------------------------------------
# DownloadService query methods
# ---------------------------------------------------------------------------


def _make_service():
    from app.event_bus import EventBus
    from app.services.download_service import DownloadService

    config = MagicMock()
    config.download_dir = "/tmp/omnidl_test"
    manager = MagicMock()
    history = MagicMock()
    history.all.return_value = []
    engine = MagicMock()
    bus = EventBus()
    svc = DownloadService(
        config=config,
        download_manager=manager,
        history_repo=history,
        engine=engine,
        event_bus=bus,
    )
    return svc, manager, history


class TestDownloadServiceQueryMethods:
    def test_delete_history_entry(self):
        svc, _, history = _make_service()
        svc.delete_history_entry("task-123")
        history.remove.assert_called_once_with("task-123")

    def test_rebuild_tiktok_pool(self):
        svc, manager, _ = _make_service()
        svc.rebuild_tiktok_pool()
        manager.rebuild_tiktok_pool.assert_called_once()

    def test_get_history_stats_empty(self):
        svc, _, history = _make_service()
        history.all.return_value = []
        stats = svc.get_history_stats()
        assert stats["total"] == 0
        assert stats["total_bytes"] == 0
        assert stats["by_platform"] == {}
        assert stats["by_status"] == {}

    def test_get_history_stats_with_entries(self):
        svc, _, history = _make_service()
        history.all.return_value = [
            {"platform": "tiktok", "status": "completed", "downloaded_bytes": 1000},
            {"platform": "youtube", "status": "completed", "downloaded_bytes": 2000},
            {"platform": "tiktok", "status": "failed", "downloaded_bytes": 0},
        ]
        stats = svc.get_history_stats()
        assert stats["total"] == 3
        assert stats["total_bytes"] == 3000
        assert stats["by_platform"]["tiktok"] == 2
        assert stats["by_platform"]["youtube"] == 1
        assert stats["by_status"]["completed"] == 2
        assert stats["by_status"]["failed"] == 1

    def test_get_history_stats_missing_fields(self):
        svc, _, history = _make_service()
        history.all.return_value = [{"platform": "twitter"}]
        stats = svc.get_history_stats()
        assert stats["total"] == 1
        assert stats["by_status"].get("unknown") == 1
        assert stats["total_bytes"] == 0


# ---------------------------------------------------------------------------
# DownloadManager.rebuild_tiktok_pool
# ---------------------------------------------------------------------------


class TestDownloadManagerRebuildPool:
    def test_rebuild_tiktok_pool(self):
        from infrastructure.downloader.download_manager import DownloadManager

        cfg = MagicMock()
        cfg.max_concurrent = 1
        cfg.max_retries = 1
        cfg.tiktok_accounts = []
        engine = MagicMock()
        bus = MagicMock()
        bus.publish = MagicMock()
        mgr = DownloadManager(config=cfg, engine=engine, event_bus=bus)
        mgr.rebuild_tiktok_pool()
        assert mgr._tiktok_pool is None


# ---------------------------------------------------------------------------
# YtDlpEngine.extract_info — proxy and ffmpeg_location opts
# ---------------------------------------------------------------------------


def _make_engine_config(**kw):
    cfg = MagicMock()
    cfg.proxy = kw.get("proxy", "")
    cfg.cookie_file = ""
    cfg.use_cookies = False
    cfg.platform_cookies = {}
    cfg.extra_yt_dlp_args = ""
    cfg.max_retries = 0
    cfg.embed_thumbnail = False
    cfg.embed_metadata = False
    return cfg


class TestYtDlpExtractInfoOpts:
    def _fake_ydl(self, info_dict):
        class FakeYDL:
            captured = {}

            def __init__(self, opts):
                FakeYDL.captured = opts

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def extract_info(self, url, download=False):
                return info_dict

        return FakeYDL

    def _info(self):
        return {
            "title": "T",
            "uploader": "U",
            "duration": 1,
            "thumbnail": "",
            "formats": [],
            "is_live": False,
            "was_live": False,
        }

    def test_proxy_included_in_opts(self):
        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg = _make_engine_config(proxy="http://localhost:8888")
        engine = YtDlpEngine(cfg)
        FakeYDL = self._fake_ydl(self._info())
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.extract_info("https://youtube.com/watch?v=abc")
        assert FakeYDL.captured.get("proxy") == "http://localhost:8888"

    def test_ffmpeg_location_included_when_path_returned(self):
        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg = _make_engine_config()
        engine = YtDlpEngine(cfg)
        FakeYDL = self._fake_ydl(self._info())
        with (
            patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL),
            patch("infrastructure.downloader.yt_dlp_engine.get_ffmpeg_path", return_value="/usr/bin"),
        ):
            engine.extract_info("https://youtube.com/watch?v=abc")
        assert FakeYDL.captured.get("ffmpeg_location") == "/usr/bin"

    def test_no_ffmpeg_opts_when_path_none(self):
        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg = _make_engine_config()
        engine = YtDlpEngine(cfg)
        FakeYDL = self._fake_ydl(self._info())
        with (
            patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL),
            patch("infrastructure.downloader.yt_dlp_engine.get_ffmpeg_path", return_value=None),
        ):
            engine.extract_info("https://youtube.com/watch?v=abc")
        assert "ffmpeg_location" not in FakeYDL.captured

    def test_transient_download_error_retried(self):
        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg = _make_engine_config()
        engine = YtDlpEngine(cfg)
        info = self._info()
        call_count = {"n": 0}

        class RetryYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def extract_info(self, url, download=False):
                call_count["n"] += 1
                if call_count["n"] < 2:
                    raise yt_dlp.utils.DownloadError("temporary server error")
                return info

        import yt_dlp

        with patch.object(mod.yt_dlp, "YoutubeDL", RetryYDL), patch("time.sleep"):
            result = engine.extract_info("https://youtube.com/watch?v=abc")
        assert result.title == "T"
        assert call_count["n"] == 2

    def test_all_attempts_fail_rate_limit_message(self):
        import yt_dlp

        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg = _make_engine_config()
        engine = YtDlpEngine(cfg)

        class AlwaysFailYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def extract_info(self, url, download=False):
                raise yt_dlp.utils.DownloadError("HTTP Error 429: Too Many Requests")

        with patch.object(mod.yt_dlp, "YoutubeDL", AlwaysFailYDL), patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc_info:
                engine.extract_info("https://www.tiktok.com/@user/video/1")
        assert "rate limit" in str(exc_info.value).lower() or "429" in str(exc_info.value)


# ---------------------------------------------------------------------------
# ThumbnailService._is_safe_thumbnail_url — edge cases
# ---------------------------------------------------------------------------


class TestIsSafeThumbnailUrl:
    def test_urlparse_value_error_returns_false(self):
        from app.services.thumbnail_service import _is_safe_thumbnail_url

        with patch("app.services.thumbnail_service.urlparse", side_effect=ValueError("bad url")):
            assert _is_safe_thumbnail_url("http://example.com/img.jpg") is False

    def test_import_error_for_requests(self):
        from app.services.thumbnail_service import ThumbnailService

        svc = ThumbnailService()

        errors = []
        done = threading.Event()

        def on_error(msg):
            errors.append(msg)
            done.set()

        import sys

        saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k in ("requests", "PIL", "PIL.Image")}
        try:
            with patch.dict("sys.modules", {"requests": None, "PIL": None, "PIL.Image": None}):
                svc.fetch_async(
                    url="http://example.com/img.jpg",
                    width=100,
                    height=100,
                    on_done=lambda *a: done.set(),
                    on_error=on_error,
                )
                done.wait(timeout=2.0)
        finally:
            sys.modules.update(saved)
        assert errors and "Optional dependency missing" in errors[0]


# ---------------------------------------------------------------------------
# HistoryRepository error paths
# ---------------------------------------------------------------------------


class TestHistoryRepositoryErrorPaths:
    def test_load_exception_is_swallowed(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository

        p = tmp_path / "history.jsonl"
        p.write_text("valid line\n")
        repo = HistoryRepository(p)
        with patch("infrastructure.storage.history_repository.json.loads", side_effect=RuntimeError("boom")):
            repo._load()
        assert repo._entries == []

    def test_append_oserror_is_logged(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository

        p = tmp_path / "history.jsonl"
        repo = HistoryRepository(p)
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            repo._append_line({"id": "x"})

    def test_rewrite_backup_restore_oserror(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository

        p = tmp_path / "history.jsonl"
        p.write_text("{}\n")
        repo = HistoryRepository(p)
        backup = p.with_suffix(".jsonl.bak")
        backup.write_text("{}\n")
        p.unlink()
        # Make the rewrite fail so the restore path is taken, then make replace() fail
        with patch("pathlib.Path.replace", side_effect=OSError("no space")):
            repo._rewrite()


# ---------------------------------------------------------------------------
# DenoLocator — source-mode Deno found in resources/deno
# ---------------------------------------------------------------------------


class TestDenoLocatorSourceMode:
    def test_finds_deno_in_resources(self, tmp_path):
        import sys

        from utils.deno_locator import locate_deno

        fake_deno = str(tmp_path / "deno")

        # Ensure no _MEIPASS so the source-mode branch (lines 68-73) is taken
        saved = getattr(sys, "_MEIPASS", None)
        if hasattr(sys, "_MEIPASS"):
            delattr(sys, "_MEIPASS")
        try:
            with (
                patch("utils.deno_locator._find_deno_in", return_value=fake_deno),
                patch("utils.deno_locator.shutil.which", return_value=None),
            ):
                result = locate_deno()
        finally:
            if saved is not None:
                sys._MEIPASS = saved
        assert result == str(fake_deno)


# ---------------------------------------------------------------------------
# ClipboardMonitor — poll loop edge cases
# ---------------------------------------------------------------------------


class TestClipboardMonitorPollEdgeCases:
    def _make_monitor(self, on_new_url=None):
        from utils.clipboard_monitor import ClipboardMonitor

        return ClipboardMonitor(
            get_clipboard=lambda: "",
            on_new_url=on_new_url or (lambda u: None),
        )

    def test_duplicate_url_no_callback(self):
        mon = self._make_monitor()
        mon._last_url = "https://example.com/v"
        # Directly invoke _poll_loop with mocked clipboard and stopped after one pass
        mon._running = True
        calls = []

        def fake_get():
            mon._running = False
            return "https://example.com/v"

        mon._get_clipboard = fake_get
        with patch("time.sleep"):
            mon._poll_loop()
        assert calls == []

    def test_callback_exception_swallowed(self):
        def _raise(u):
            raise RuntimeError("cb error")

        mon = self._make_monitor(on_new_url=_raise)
        mon._running = True
        mon._last_url = None

        def fake_get():
            mon._running = False
            return "https://new.com/v"

        mon._get_clipboard = fake_get
        with patch("time.sleep"):
            mon._poll_loop()

    def test_clipboard_read_exception_swallowed(self):
        mon = self._make_monitor()
        mon._running = True
        call_count = {"n": 0}

        def fake_get():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("clipboard unavailable")
            mon._running = False
            return ""

        mon._get_clipboard = fake_get
        with patch("time.sleep"):
            mon._poll_loop()


# ---------------------------------------------------------------------------
# RemoteConvertService.start_convert — invalid output_codec
# ---------------------------------------------------------------------------


class TestRemoteConvertServiceValidation:
    def test_invalid_output_codec_raises(self):
        from app.services.remote_convert_service import RemoteConvertService

        svc = RemoteConvertService.__new__(RemoteConvertService)
        with pytest.raises(ValueError, match="output_codec"):
            svc.start_convert(
                source_task_id="task1",
                file_path=MagicMock(),
                output_codec="not_a_codec",
            )
