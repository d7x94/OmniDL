"""
tests/test_e2e.py
End-to-End Integration Tests — Full Stack (No UI, No Real Network)

Giải thích đơn giản:
  - Unit test = kiểm tra từng bộ phận riêng lẻ (dùng mock)
  - E2E test  = wire toàn bộ hệ thống thật, chỉ mock phần ngoài
                (yt-dlp network call + thumbnail HTTP)

Các test này mô phỏng đúng luồng người dùng:
  1. Mở app → analyze URL → hiện thông tin video
  2. Bấm Download → file được tạo trong thư mục
  3. Lịch sử được lưu tự động
  4. Pause / Resume / Cancel hoạt động đúng
  5. Folder tùy chỉnh (Browse) được tôn trọng
  6. URL không hợp lệ báo lỗi rõ ràng
"""
from __future__ import annotations

import threading
import time
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from typing import Optional

import pytest

# ── Real components (không mock) ──────────────────────────────────────────
from app.event_bus import EventBus
from app.services.download_service import DownloadService
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
from infrastructure.storage.history_repository import HistoryRepository


# ---------------------------------------------------------------------------
# Fixture: wires toàn bộ stack thật như main.py, chỉ mock yt-dlp network
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    """
    Tạo một app stack hoàn chỉnh như production:
      ConfigManager → YtDlpEngine → DownloadManager → DownloadService
      HistoryRepository → EventBus

    yt-dlp network calls được mock để test chạy offline.
    Trả về dict chứa service + các thành phần để test có thể kiểm tra.
    """
    # Config thật, đọc từ file tạm
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "download_dir": str(tmp_path / "downloads"),
        "max_concurrent": 2,
        "max_retries": 1,
        "proxy": "",
        "use_cookies": False,
        "extra_args": "",
        "cookie_file": "",
        "embed_thumbnail": False,
        "embed_metadata": False,
    }), encoding="utf-8")

    config  = ConfigManager(config_path)
    history = HistoryRepository(tmp_path / "history.jsonl")
    bus     = EventBus()
    engine  = YtDlpEngine(config)
    manager = DownloadManager(config=config, engine=engine, event_bus=bus)
    manager.start()

    service = DownloadService(
        config=config,
        download_manager=manager,
        history_repo=history,
        engine=engine,
        event_bus=bus,
    )

    yield {
        "service": service,
        "manager": manager,
        "history": history,
        "config":  config,
        "bus":     bus,
        "engine":  engine,
        "tmp":     tmp_path,
    }

    manager.shutdown(wait=False)


def wait_for(condition, timeout=5.0, interval=0.02):
    """Chờ condition() trả về True trong vòng timeout giây."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def fake_media_info(url="https://youtube.com/watch?v=test") -> MediaInfo:
    return MediaInfo(
        url=url,
        title="Test Video — Rick Astley",
        uploader="RickAstleyVEVO",
        duration=213,
        thumbnail="https://i.ytimg.com/vi/test/default.jpg",
        platform="YouTube",
        formats=[],
        is_live=False,
        was_live=False,
    )


def fake_ydl_download(task: DownloadTask, on_progress=None, on_postprocess=None):
    """Giả lập yt-dlp tải file: tạo file thật trong output_dir."""
    out_dir = Path(task.output_dir) if task.output_dir else Path("/tmp")  # nosec B108
    out_dir.mkdir(parents=True, exist_ok=True)
    fake_file = out_dir / f"{task.id}.mp4"
    fake_file.write_bytes(b"fake video data")
    task.filename = str(fake_file)
    # Giả lập progress 0% → 100%
    if on_progress:
        task.downloaded_bytes = 500
        task.total_bytes = 1000
        task.progress = 50.0
        task.status = DownloadStatus.DOWNLOADING
        on_progress(task)


# ===========================================================================
# Scenario 1: Analyze URL → nhận thông tin video
# ===========================================================================

class TestAnalyzeURL:
    def test_valid_url_returns_media_info(self, app):
        """
        Người dùng paste URL hợp lệ → app phân tích → hiện tên video, kênh, thời lượng.
        """
        service = app["service"]
        engine  = app["engine"]
        info    = fake_media_info()

        results, errors = [], []
        done = threading.Event()

        with patch.object(engine, "extract_info", return_value=info):
            service.analyse_url(
                url="https://youtube.com/watch?v=dQw4w9WgXcQ",
                on_done=lambda i: (results.append(i), done.set()),
                on_error=lambda e: (errors.append(e), done.set()),
            )
            assert done.wait(5), "analyse_url không trả về kết quả"

        assert not errors, f"Không mong đợi lỗi: {errors}"
        assert results[0].title == "Test Video — Rick Astley"
        assert results[0].uploader == "RickAstleyVEVO"
        assert results[0].duration == 213
        assert results[0].platform == "YouTube"

    def test_invalid_url_returns_error_immediately(self, app):
        """
        Người dùng paste URL sai (không có http://) → báo lỗi ngay, không gọi yt-dlp.
        """
        service = app["service"]
        errors  = []

        service.analyse_url(
            url="not-a-url-at-all",
            on_done=lambda i: None,
            on_error=errors.append,
        )

        assert errors, "Phải báo lỗi với URL không hợp lệ"
        assert "invalid" in errors[0].lower() or "http" in errors[0].lower()

    def test_private_video_returns_friendly_error(self, app):
        """
        Video private → app hiện thông báo thân thiện, không crash.
        """
        service = app["service"]
        engine  = app["engine"]
        errors  = []
        done    = threading.Event()

        _err = RuntimeError("Content is private. Try enabling cookies.")
        with patch.object(engine, "extract_info", side_effect=_err):
            service.analyse_url(
                url="https://youtube.com/watch?v=private",
                on_done=lambda i: done.set(),
                on_error=lambda e: (errors.append(e), done.set()),
            )
            done.wait(5)

        assert errors
        assert "private" in errors[0].lower()

    def test_facebook_stories_blocked_without_cookies(self, app):
        """
        Facebook Stories bị chặn khi chưa cấu hình cookies.
        Khi có cookies, yt-dlp CÓ THỂ tải Facebook Stories.
        """
        service = app["service"]
        app["config"].set("use_cookies", False)
        app["config"].set("cookie_file", "")
        errors  = []
        done    = threading.Event()

        service.analyse_url(
            url="https://www.facebook.com/stories/user/123456",
            on_done=lambda i: done.set(),
            on_error=lambda e: (errors.append(e), done.set()),
        )
        done.wait(5)

        assert errors
        assert "facebook stories" in errors[0].lower() or "stories" in errors[0].lower()

    def test_instagram_stories_blocked_without_cookies(self, app):
        """
        Instagram Stories bị chặn khi chưa cấu hình cookies.
        """
        service = app["service"]
        # Đảm bảo không có cookies
        app["config"].set("use_cookies", False)
        app["config"].set("cookie_file", "")

        errors = []
        done   = threading.Event()

        service.analyse_url(
            url="https://www.instagram.com/stories/user/123456/",
            on_done=lambda i: done.set(),
            on_error=lambda e: (errors.append(e), done.set()),
        )
        done.wait(5)

        assert errors
        assert "cookie" in errors[0].lower()


# ===========================================================================
# Scenario 2: Download → file xuất hiện đúng thư mục
# ===========================================================================

class TestDownloadFlow:
    def test_download_creates_file_in_default_folder(self, app):
        """
        Người dùng bấm Download → file được tạo trong thư mục download mặc định.
        """
        service    = app["service"]
        engine     = app["engine"]
        config     = app["config"]
        info       = fake_media_info()
        default_dir = config.download_dir

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: task.status == DownloadStatus.COMPLETED), \
                f"Download không hoàn thành, status: {task.status}"

        # File phải tồn tại trong thư mục đúng
        assert task.filename, "task.filename phải được set"
        assert Path(task.filename).exists(), "File phải tồn tại trên disk"
        assert str(default_dir) in task.filename or task.output_dir == str(default_dir)

    def test_download_respects_custom_output_dir(self, app):
        """
        Người dùng bấm Browse → chọn thư mục khác → file đi vào thư mục đó.
        (BUG-1 fix: output_dir phải được truyền xuống)
        """
        service    = app["service"]
        engine     = app["engine"]
        custom_dir = app["tmp"] / "my_custom_folder"
        info       = fake_media_info()

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
                output_dir=custom_dir,          # ← BUG-1 fix
            )
            assert wait_for(lambda: task.status == DownloadStatus.COMPLETED), \
                f"Download không hoàn thành, status: {task.status}"

        assert task.output_dir == str(custom_dir), \
            f"output_dir phải là {custom_dir}, nhận được {task.output_dir}"
        assert custom_dir.exists(), "Thư mục tùy chỉnh phải được tạo"

    def test_failed_download_has_error_message(self, app):
        """
        yt-dlp gặp lỗi không thể khắc phục → task chuyển sang FAILED với thông báo lỗi.
        Dùng "not found" (hard error) để tránh retry delay trong test.
        """
        service = app["service"]
        engine  = app["engine"]
        info    = fake_media_info()

        with patch.object(engine, "download",
                          side_effect=RuntimeError("Video not found")):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: task.status == DownloadStatus.FAILED), \
                "Task phải chuyển sang FAILED"

        assert task.error_msg, "Phải có thông báo lỗi"
        assert task.finished_at is not None, "finished_at phải được set"

    def test_multiple_concurrent_downloads(self, app):
        """
        Người dùng add 3 video cùng lúc → tất cả đều hoàn thành.
        """
        service = app["service"]
        engine  = app["engine"]
        tasks   = []

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            for i in range(3):
                info = fake_media_info(url=f"https://youtube.com/watch?v=video{i}")
                task = service.start_download(
                    url=info.url, media_info=info,
                    format_id="best", output_ext="mp4",
                )
                tasks.append(task)

            assert wait_for(
                lambda: all(t.status == DownloadStatus.COMPLETED for t in tasks),
                timeout=10,
            ), f"Không phải tất cả đều hoàn thành: {[t.status for t in tasks]}"

        assert len(tasks) == 3


# ===========================================================================
# Scenario 3: Lịch sử tự động được lưu
# ===========================================================================

class TestHistoryAutoSave:
    def test_completed_download_appears_in_history(self, app):
        """
        Sau khi download xong → vào tab History → thấy video trong danh sách.
        """
        service = app["service"]
        engine  = app["engine"]
        info    = fake_media_info()

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: task.status == DownloadStatus.COMPLETED)
            # Đợi daemon thread lưu history
            assert wait_for(lambda: len(service.get_history()) > 0, timeout=3), \
                "History phải được lưu sau khi download xong"

        history_entries = service.get_history()
        assert any(e.get("id") == task.id for e in history_entries), \
            "Task phải xuất hiện trong history"

    def test_failed_download_also_saved_to_history(self, app):
        """
        Download thất bại cũng được lưu vào history (để người dùng biết).
        """
        service = app["service"]
        engine  = app["engine"]
        info    = fake_media_info()

        with patch.object(engine, "download",
                          side_effect=RuntimeError("404 Not Found")):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: task.status == DownloadStatus.FAILED)
            assert wait_for(lambda: len(service.get_history()) > 0, timeout=3)

        assert any(e.get("id") == task.id for e in service.get_history())

    def test_history_persists_across_restarts(self, app):
        """
        Tắt app rồi mở lại → history vẫn còn.
        """
        service = app["service"]
        engine  = app["engine"]
        info    = fake_media_info()
        tmp     = app["tmp"]

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: task.status == DownloadStatus.COMPLETED)
            assert wait_for(lambda: len(service.get_history()) > 0, timeout=3)

        # Giả lập restart: tạo HistoryRepository mới từ cùng file
        history2 = HistoryRepository(tmp / "history.jsonl")
        assert len(history2.all()) > 0, "History phải còn sau khi restart"
        assert any(e.get("id") == task.id for e in history2.all())

    def test_search_history(self, app):
        """
        Người dùng tìm kiếm trong history → chỉ hiện kết quả khớp.
        """
        service = app["service"]
        engine  = app["engine"]

        videos = [
            fake_media_info(url="https://youtube.com/watch?v=rick"),
            MediaInfo(url="https://youtube.com/watch?v=python",
                      title="Python Tutorial",
                      uploader="TechChannel", duration=600,
                      thumbnail="", platform="YouTube",
                      formats=[], is_live=False, was_live=False),
        ]

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            tasks = []
            for info in videos:
                task = service.start_download(
                    url=info.url, media_info=info,
                    format_id="best", output_ext="mp4",
                )
                tasks.append(task)
            assert wait_for(
                lambda: all(t.status == DownloadStatus.COMPLETED for t in tasks)
            )
            assert wait_for(lambda: len(service.get_history()) >= 2, timeout=3)

        results = service.search_history("rick")
        assert len(results) >= 1
        assert all("rick" in r.get("title", "").lower()
                   or "rick" in r.get("url", "").lower()
                   for r in results)


# ===========================================================================
# Scenario 4: Cancel download
# ===========================================================================

class TestCancelDownload:
    def test_cancel_stops_download(self, app):
        """
        Người dùng bấm Cancel trong lúc đang tải → download dừng lại.
        """
        service    = app["service"]
        engine     = app["engine"]
        info       = fake_media_info()
        cancel_called = threading.Event()

        def slow_download(task, on_progress=None, on_postprocess=None):
            # Giả lập download chậm
            for _ in range(50):
                if task.is_cancellation_requested:
                    cancel_called.set()
                    import yt_dlp
                    raise yt_dlp.utils.DownloadError("Cancelled by user")
                time.sleep(0.05)

        with patch.object(engine, "download", side_effect=slow_download):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            # Đợi bắt đầu download
            time.sleep(0.1)
            # Người dùng bấm Cancel
            service.cancel_download(task.id)

            _cancelled_or_failed = (DownloadStatus.CANCELLED, DownloadStatus.FAILED)
            assert wait_for(
                lambda: task.status in _cancelled_or_failed,
                timeout=5,
            ), f"Task phải bị cancel, status: {task.status}"


# ===========================================================================
# Scenario 5: EventBus — sự kiện được phát đúng
# ===========================================================================

class TestEvents:
    def test_download_started_event_fired(self, app):
        """
        Khi download bắt đầu → DOWNLOAD_STARTED event được phát.
        """
        service = app["service"]
        engine  = app["engine"]
        bus     = app["bus"]
        info    = fake_media_info()
        events  = []

        bus.subscribe(EventBus.DOWNLOAD_STARTED, lambda **kw: events.append("started"))

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            task = service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: task.status == DownloadStatus.COMPLETED)

        assert "started" in events

    def test_download_completed_event_fired(self, app):
        """
        Khi download xong → DOWNLOAD_COMPLETED event được phát.
        """
        service = app["service"]
        engine  = app["engine"]
        bus     = app["bus"]
        info    = fake_media_info()
        events  = []

        bus.subscribe(
            EventBus.DOWNLOAD_COMPLETED, lambda **kw: events.append("completed")
        )

        with patch.object(engine, "download", side_effect=fake_ydl_download):
            service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: "completed" in events, timeout=5)

        assert "completed" in events

    def test_download_failed_event_fired(self, app):
        """
        Khi download lỗi → DOWNLOAD_FAILED event được phát.
        """
        service = app["service"]
        engine  = app["engine"]
        bus     = app["bus"]
        info    = fake_media_info()
        events  = []

        bus.subscribe(EventBus.DOWNLOAD_FAILED, lambda **kw: events.append("failed"))

        with patch.object(engine, "download",
                          side_effect=RuntimeError("Server error")):
            service.start_download(
                url=info.url, media_info=info,
                format_id="best", output_ext="mp4",
            )
            assert wait_for(lambda: "failed" in events, timeout=5)

        assert "failed" in events


# ===========================================================================
# Scenario 6: Security — SSRF và path traversal không thể xảy ra
# ===========================================================================

class TestSecurity:
    def test_private_ip_thumbnail_blocked(self):
        """
        Thumbnail URL trỏ về LAN (192.168.x.x) → bị chặn, không fetch.
        (Fix S1 — SSRF)
        """
        from ui.tabs.home_tab import _is_safe_thumbnail_url
        assert _is_safe_thumbnail_url("http://192.168.1.1/admin") is False
        assert _is_safe_thumbnail_url("http://10.0.0.1/secret") is False
        assert _is_safe_thumbnail_url("http://169.254.169.254/meta-data") is False
        assert _is_safe_thumbnail_url("http://127.0.0.1/local") is False

    def test_public_thumbnail_url_allowed(self):
        """
        Thumbnail URL hợp lệ từ CDN công khai → được phép.
        """
        from ui.tabs.home_tab import _is_safe_thumbnail_url
        assert _is_safe_thumbnail_url("https://i.ytimg.com/vi/test/default.jpg") is True
        _tiktok = "https://p16-sign.tiktokcdn.com/thumb.jpg"
        assert _is_safe_thumbnail_url(_tiktok) is True

    def test_cookie_file_outside_home_rejected(self, app, tmp_path, monkeypatch):
        """
        cookie_file trỏ ra ngoài home dir → bị từ chối, không truyền vào yt-dlp.
        (Fix S3 — Path Traversal)
        """
        engine = app["engine"]
        outside = tmp_path / "evil_cookies.txt"
        outside.write_text("# cookies\n")

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        app["config"].set("cookie_file", str(outside))

        captured = {}

        class FakeYDL:
            def __init__(self, opts): captured.update(opts)
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, url, download=False):
                return {
                    "title": "T", "uploader": "U", "duration": 1,
                    "thumbnail": "", "formats": [],
                    "is_live": False, "was_live": False,
                }

        import infrastructure.downloader.yt_dlp_engine as mod
        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            engine.extract_info("https://youtube.com/watch?v=test")

        assert "cookiefile" not in captured, \
            "cookie_file ngoài home dir không được truyền vào yt-dlp"

    def test_path_traversal_in_safe_path_rejected(self):
        """
        Tên file chứa ../ → safe_path() ném ValueError.
        (Ngăn path traversal khi đặt tên file tải về)
        """
        from utils.helpers import safe_path
        import tempfile
        with tempfile.TemporaryDirectory() as base:
            with pytest.raises(ValueError, match="traversal"):
                safe_path(Path(base), "../../etc/passwd")
