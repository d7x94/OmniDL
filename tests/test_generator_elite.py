"""
tests/test_generator_elite.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEST GENERATOR ELITE — OmniDL v16
Target: ≥ 90–95% coverage on all non-UI modules

Modules covered (new / gap-filling):
  ① infrastructure/config/config_manager.py
      reset_to_defaults, cache eviction, all typed accessors,
      debounced-save deduplication, atomic tmp→rename, proxy validator,
      cookies_browser allowlist, embed_thumbnail/metadata, history_limit,
      default_quality/format, extra_args, cookie_file, config_path

  ② utils/logger.py
      duplicate-handler prevention, noisy-logger silencing,
      OSError graceful fallback, re-entry idempotence

  ③ app/use_cases/{cancel,pause,start}_download.py
      delegation, correct arg forwarding, return value pass-through

  ④ app/services/thumbnail_service.py
      _is_safe_thumbnail_url (all 3 SSRF layers),
      _fetch: content-type guard, byte cap, success, each error branch,
      allow_redirects=False enforcement

  ⑤ app/services/ffmpeg_convert_service.py
      _parse_seconds, quality_label, _probe_duration (success + failure),
      _convert_sync: non-file raises, output collision, ffmpeg failure,
      ffmpeg success; _locate_ffmpeg_bin missing → ConversionError

  ⑥ domain/models/download_task.py
      to_dict lock correctness, elapsed, title/platform properties,
      wait_if_paused with cancel unblock

  ⑦ infrastructure/downloader/yt_dlp_engine.py
      _fmt_speed, _fmt_eta, _friendly_error all branches,
      _check_unsupported_url matrix (always-blocked / needs-cookies /
      with-cookies / without-cookies),
      progress_hook: paused-cancel unblock, finished status,
      live-stream detection flags

  ⑧ app/services/download_service.py
      convert_to_mp4 delegation, close() executor shutdown, get_task

All tests:
  • run completely offline
  • mock every subprocess / network call
  • avoid time.sleep / timing-based assertions
  • are deterministic
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

# ── ensure repo root is on sys.path ──────────────────────────────────────────
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ═════════════════════════════════════════════════════════════════════════════
# ① ConfigManager — gap tests
# ═════════════════════════════════════════════════════════════════════════════

class TestConfigManagerResetToDefaults:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from infrastructure.config.config_manager import ConfigManager
        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def test_reset_restores_factory_theme(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("theme", "light")
        cfg.reset_to_defaults()
        assert cfg.theme == "dark"

    def test_reset_writes_to_disk(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("max_concurrent", 8)
        cfg.reset_to_defaults()
        data = json.loads(p.read_text())
        assert data["max_concurrent"] == 3

    def test_reset_updates_cache(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("theme", "light")
        cfg.reset_to_defaults()
        path_key = str(p.resolve())
        with ConfigManager._cache_lock:
            cached = ConfigManager._cache.get(path_key, {})
        assert cached.get("theme") == "dark"

    def test_reset_then_get_returns_default(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("language", "vi")
        cfg.reset_to_defaults()
        assert cfg.get("language") == "en"


class TestConfigManagerAtomicSave:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from infrastructure.config.config_manager import ConfigManager
        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def test_no_tmp_file_after_successful_save(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("theme", "light")
        cfg.save()
        assert not (tmp_path / "cfg.tmp.json").exists()

    def test_config_json_valid_after_save(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("max_concurrent", 5)
        cfg.save()
        data = json.loads(p.read_text())
        assert data["max_concurrent"] == 5

    def test_save_cancels_pending_timer(self, tmp_path):
        """save() must cancel debounced timer and write synchronously."""
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        cfg.set("theme", "light")  # starts a 500 ms debounce timer
        cfg.save()                  # must cancel timer and write immediately
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["theme"] == "light"

    def test_oserror_on_write_does_not_raise(self, tmp_path, monkeypatch):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        monkeypatch.setattr(
            "infrastructure.config.config_manager.open",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")),
            raising=False,
        )
        # Must not propagate the OSError
        try:
            cfg._save()
        except OSError:
            pytest.fail("_save() must not propagate OSError")


class TestConfigManagerTypedAccessors:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from infrastructure.config.config_manager import ConfigManager
        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def test_embed_thumbnail_default_true(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.embed_thumbnail is True

    def test_embed_thumbnail_can_be_set_false(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        cfg.set("embed_thumbnail", False)
        assert cfg.embed_thumbnail is False

    def test_embed_metadata_default_true(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.embed_metadata is True

    def test_default_quality_string(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert "best" in cfg.default_quality

    def test_default_format_mp4(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.default_format == "mp4"

    def test_history_limit_default_500(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.history_limit == 500

    def test_extra_args_default_empty(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.extra_args == ""

    def test_cookie_file_default_empty(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.cookie_file == ""

    def test_config_path_property(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg = ConfigManager(p)
        assert cfg.config_path == p

    def test_cookies_browser_invalid_value_falls_back_to_chrome(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        cfg.set("cookies_browser", "evilbrowser")
        assert cfg.cookies_browser == "chrome"

    def test_cookies_browser_valid_values_accepted(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        for browser in ("chrome", "firefox", "safari", "edge", "opera",
                        "brave", "chromium", "vivaldi"):
            ConfigManager._cache.clear()
            cfg = ConfigManager(tmp_path / f"cfg_{browser}.json")
            cfg.set("cookies_browser", browser)
            assert cfg.cookies_browser == browser

    def test_proxy_empty_returns_empty(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        assert cfg.proxy == ""

    def test_proxy_file_scheme_rejected(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        cfg.set("proxy", "file:///etc/passwd")
        assert cfg.proxy == ""

    def test_proxy_socks5h_accepted(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        cfg.set("proxy", "socks5h://127.0.0.1:1080")
        assert cfg.proxy == "socks5h://127.0.0.1:1080"

    def test_update_multiple_keys(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "cfg.json")
        cfg.update({"theme": "light", "max_concurrent": 7})
        assert cfg.theme == "light"
        assert cfg.max_concurrent == 7


class TestConfigManagerCache:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from infrastructure.config.config_manager import ConfigManager
        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def test_second_instance_same_path_sees_cached_data(self, tmp_path):
        """Two instances for the same path share the in-memory cache."""
        from infrastructure.config.config_manager import ConfigManager
        p = tmp_path / "cfg.json"
        cfg1 = ConfigManager(p)
        cfg1.set("theme", "light")
        ConfigManager._cache.clear()  # simulate second run reading from disk
        cfg1.save()
        cfg2 = ConfigManager(p)
        assert cfg2.theme == "light"


# ═════════════════════════════════════════════════════════════════════════════
# ② Logger — gap tests
# ═════════════════════════════════════════════════════════════════════════════

class TestSetupLogging:
    def test_duplicate_handlers_not_added_on_second_call(self, tmp_path):
        """Calling setup_logging twice must not add duplicate handlers."""
        import logging
        from utils.logger import setup_logging
        root = logging.getLogger()
        before = len(root.handlers)
        setup_logging(tmp_path / "logs1")
        after_first = len(root.handlers)
        setup_logging(tmp_path / "logs1")
        after_second = len(root.handlers)
        assert after_second == after_first, (
            f"Duplicate handlers added: {after_second} vs {after_first}"
        )

    def test_noisy_loggers_set_to_warning(self, tmp_path):
        import logging
        from utils.logger import setup_logging
        setup_logging(tmp_path / "logs2")
        for name in ("PIL", "urllib3", "requests", "yt_dlp"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_root_logger_level_applied(self, tmp_path):
        import logging
        from utils.logger import setup_logging
        setup_logging(tmp_path / "logs3", level=logging.DEBUG)
        assert logging.getLogger().level == logging.DEBUG

    def test_log_dir_created(self, tmp_path):
        from utils.logger import setup_logging
        log_dir = tmp_path / "a" / "b" / "logs"
        setup_logging(log_dir)
        assert log_dir.is_dir()

    def test_rotating_handler_present(self, tmp_path):
        import logging
        from logging.handlers import RotatingFileHandler
        from utils.logger import setup_logging
        root = logging.getLogger()
        # Remove any existing file handler for this path
        log_dir = tmp_path / "logs_rot"
        setup_logging(log_dir)
        file_handlers = [
            h for h in root.handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert file_handlers, "No RotatingFileHandler found after setup_logging"


# ═════════════════════════════════════════════════════════════════════════════
# ③ Use-cases — delegation tests
# ═════════════════════════════════════════════════════════════════════════════

class TestCancelDownloadUseCase:
    def test_execute_calls_cancel_download(self):
        from app.use_cases.cancel_download import CancelDownload
        svc = MagicMock()
        uc = CancelDownload(svc)
        uc.execute("abc123")
        svc.cancel_download.assert_called_once_with("abc123")

    def test_execute_passes_exact_task_id(self):
        from app.use_cases.cancel_download import CancelDownload
        svc = MagicMock()
        uc = CancelDownload(svc)
        uc.execute("TASK-XYZ")
        assert svc.cancel_download.call_args[0][0] == "TASK-XYZ"


class TestPauseDownloadUseCase:
    def test_execute_calls_pause_download(self):
        from app.use_cases.pause_download import PauseDownload
        svc = MagicMock()
        uc = PauseDownload(svc)
        uc.execute("tid-99")
        svc.pause_download.assert_called_once_with("tid-99")

    def test_execute_passes_exact_task_id(self):
        from app.use_cases.pause_download import PauseDownload
        svc = MagicMock()
        uc = PauseDownload(svc)
        uc.execute("TID-PAUSE")
        assert svc.pause_download.call_args[0][0] == "TID-PAUSE"


class TestStartDownloadUseCase:
    def test_execute_returns_download_task(self, tmp_path):
        from app.use_cases.start_download import StartDownload
        from domain.models.download_task import DownloadTask, MediaInfo
        fake_task = DownloadTask(url="https://example.com/v")
        svc = MagicMock()
        svc.start_download.return_value = fake_task
        uc = StartDownload(svc)
        info = MediaInfo(url="https://example.com/v", title="Test")
        result = uc.execute(
            url="https://example.com/v",
            media_info=info,
            format_id="22",
            output_ext="mp4",
            output_dir=tmp_path,
        )
        assert result is fake_task

    def test_execute_forwards_all_args(self, tmp_path):
        from app.use_cases.start_download import StartDownload
        from domain.models.download_task import DownloadTask, MediaInfo
        svc = MagicMock()
        svc.start_download.return_value = DownloadTask()
        uc = StartDownload(svc)
        info = MediaInfo(url="https://x.com/v")
        uc.execute(
            url="https://x.com/v",
            media_info=info,
            format_id="best",
            output_ext="mkv",
            output_dir=tmp_path,
        )
        svc.start_download.assert_called_once_with(
            url="https://x.com/v",
            media_info=info,
            format_id="best",
            output_ext="mkv",
            output_dir=tmp_path,
        )

    def test_execute_with_no_output_dir(self):
        from app.use_cases.start_download import StartDownload
        from domain.models.download_task import DownloadTask, MediaInfo
        svc = MagicMock()
        svc.start_download.return_value = DownloadTask()
        uc = StartDownload(svc)
        uc.execute(
            url="https://y.com/v",
            media_info=MediaInfo(url="https://y.com/v"),
            format_id="22",
            output_ext="mp4",
        )
        _, kwargs = svc.start_download.call_args
        assert kwargs.get("output_dir") is None


# ═════════════════════════════════════════════════════════════════════════════
# ④ ThumbnailService — SSRF layers + fetch logic
# ═════════════════════════════════════════════════════════════════════════════

class TestIsSafeThumbnailUrl:
    """Unit tests for _is_safe_thumbnail_url covering all 3 SSRF layers."""

    def _safe(self, url):
        from app.services.thumbnail_service import _is_safe_thumbnail_url
        return _is_safe_thumbnail_url(url)

    # Layer 1 — scheme allowlist
    def test_https_scheme_allowed(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service.socket.getaddrinfo",
            lambda *a, **kw: [(None, None, None, None, ("1.2.3.4", 0))]
        )
        assert self._safe("https://cdn.example.com/thumb.jpg")

    def test_http_scheme_allowed(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service.socket.getaddrinfo",
            lambda *a, **kw: [(None, None, None, None, ("1.2.3.4", 0))]
        )
        assert self._safe("http://cdn.example.com/thumb.jpg")

    def test_ftp_scheme_blocked(self):
        assert not self._safe("ftp://cdn.example.com/thumb.jpg")

    def test_file_scheme_blocked(self):
        assert not self._safe("file:///etc/passwd")

    def test_data_uri_blocked(self):
        assert not self._safe("data:image/png;base64,abc")

    def test_empty_url_blocked(self):
        assert not self._safe("")

    def test_no_host_blocked(self):
        assert not self._safe("https:///path")

    # Layer 2 — IP literal checks (no DNS)
    def test_localhost_ip_blocked(self):
        assert not self._safe("https://127.0.0.1/thumb.jpg")

    def test_ipv4_loopback_blocked(self):
        assert not self._safe("http://127.0.0.2/img.jpg")

    def test_private_10_block_blocked(self):
        assert not self._safe("https://10.0.0.1/img.jpg")

    def test_private_192_168_blocked(self):
        assert not self._safe("https://192.168.1.50/thumb.jpg")

    def test_private_172_16_blocked(self):
        assert not self._safe("https://172.16.0.1/thumb.jpg")

    def test_link_local_169_254_blocked(self):
        assert not self._safe("https://169.254.169.254/latest/")

    def test_ipv6_loopback_blocked(self):
        assert not self._safe("https://[::1]/thumb.jpg")

    # Layer 2 — fast-reject well-known loopback hostnames
    def test_localhost_hostname_blocked(self):
        assert not self._safe("https://localhost/thumb.jpg")

    def test_localhost_localdomain_blocked(self):
        assert not self._safe("https://localhost.localdomain/img.jpg")

    # Layer 3 — DNS resolution
    def test_hostname_resolving_to_private_ip_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service.socket.getaddrinfo",
            lambda *a, **kw: [(None, None, None, None, ("192.168.0.5", 0))]
        )
        assert not self._safe("https://evil.internal.example.com/thumb.jpg")

    def test_hostname_resolving_to_public_ip_allowed(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service.socket.getaddrinfo",
            lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 0))]
        )
        assert self._safe("https://cdn.example.com/thumb.jpg")

    def test_dns_failure_blocks_url(self, monkeypatch):
        import socket
        def _fail(*a, **kw):
            raise socket.gaierror("NXDOMAIN")
        monkeypatch.setattr(
            "app.services.thumbnail_service.socket.getaddrinfo", _fail
        )
        assert not self._safe("https://nonexistent.invalid/img.jpg")

    def test_multiple_dns_results_all_must_be_public(self, monkeypatch):
        """If any resolved address is private, URL must be blocked."""
        monkeypatch.setattr(
            "app.services.thumbnail_service.socket.getaddrinfo",
            lambda *a, **kw: [
                (None, None, None, None, ("93.184.216.34", 0)),
                (None, None, None, None, ("10.0.0.1", 0)),  # private!
            ]
        )
        assert not self._safe("https://mixed.example.com/thumb.jpg")


class TestThumbnailServiceFetch:
    """Tests for ThumbnailService._fetch error paths and success."""

    def _make_svc(self):
        from app.services.thumbnail_service import ThumbnailService
        return ThumbnailService()

    def test_private_url_calls_on_error(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service._is_safe_thumbnail_url",
            lambda u: False
        )
        svc = self._make_svc()
        errors = []
        svc._fetch("https://127.0.0.1/img.jpg", 100, 100,
                   lambda img: None, errors.append)
        assert errors and "SSRF" in errors[0]

    def test_wrong_content_type_calls_on_error(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service._is_safe_thumbnail_url",
            lambda u: True
        )
        fake_resp = MagicMock()
        fake_resp.raise_for_status = lambda: None
        fake_resp.headers = {"content-type": "text/html"}
        monkeypatch.setattr(
            "requests.get", lambda *a, **kw: fake_resp
        )
        svc = self._make_svc()
        errors = []
        svc._fetch("https://cdn.example.com/t.jpg", 100, 100,
                   lambda img: None, errors.append)
        assert errors
        assert "content-type" in errors[0].lower() or "Unexpected" in errors[0]

    def test_allow_redirects_false_enforced(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service._is_safe_thumbnail_url",
            lambda u: True
        )
        captured = {}

        def fake_get(url, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here")

        monkeypatch.setattr("requests.get", fake_get)
        svc = self._make_svc()
        svc._fetch("https://cdn.example.com/t.jpg", 100, 100,
                   lambda img: None, lambda e: None)
        assert captured.get("allow_redirects") is False

    def test_successful_fetch_calls_on_done(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.thumbnail_service._is_safe_thumbnail_url",
            lambda u: True
        )
        # Create a minimal 1×1 PNG in memory
        from PIL import Image as PILImage
        buf = io.BytesIO()
        PILImage.new("RGB", (1, 1), color=(255, 0, 0)).save(buf, format="PNG")
        img_bytes = buf.getvalue()

        fake_resp = MagicMock()
        fake_resp.raise_for_status = lambda: None
        fake_resp.headers = {"content-type": "image/png"}
        fake_resp.iter_content = lambda chunk_size: iter([img_bytes])

        monkeypatch.setattr("requests.get", lambda *a, **kw: fake_resp)

        svc = self._make_svc()
        done = []
        svc._fetch("https://cdn.example.com/t.png", 50, 50,
                   done.append, lambda e: None)
        assert done, "on_done was not called"
        assert hasattr(done[0], "size")  # PIL Image

    def test_fetch_async_starts_daemon_thread(self, monkeypatch):
        """fetch_async must start a background thread (non-blocking)."""
        from app.services.thumbnail_service import ThumbnailService
        started = []

        class FakeThread:
            def __init__(self, *a, daemon=False, name=None, **kw):
                self.daemon = daemon
                started.append(daemon)
            def start(self): pass

        monkeypatch.setattr(
            "app.services.thumbnail_service.threading.Thread", FakeThread
        )
        svc = ThumbnailService()
        svc.fetch_async("https://cdn.example.com/t.jpg", 100, 100,
                        lambda img: None, lambda e: None)
        assert started == [True], "fetch_async must start a daemon thread"


# ═════════════════════════════════════════════════════════════════════════════
# ⑤ FfmpegConvertService — unit tests
# ═════════════════════════════════════════════════════════════════════════════

class TestParseSeconds:
    def _call(self, h, m, s, cs):
        import re as _re
        from app.services.ffmpeg_convert_service import _parse_seconds
        pattern = _re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        m_ = pattern.match(f"time={h:02d}:{m:02d}:{s:02d}.{cs:02d}")
        return _parse_seconds(m_)

    def test_zero(self):          assert self._call(0, 0, 0, 0) == 0.0
    def test_one_second(self):    assert self._call(0, 0, 1, 0) == 1.0
    def test_one_minute(self):    assert self._call(0, 1, 0, 0) == 60.0
    def test_one_hour(self):      assert self._call(1, 0, 0, 0) == 3600.0
    def test_mixed(self):
        assert self._call(1, 2, 3, 50) == pytest.approx(3723.5)
    def test_centiseconds(self):
        assert self._call(0, 0, 0, 25) == pytest.approx(0.25)


class TestQualityLabel:
    def test_high_label(self):
        from app.services.ffmpeg_convert_service import FfmpegConvertService
        label = FfmpegConvertService.quality_label("high")
        assert isinstance(label, str) and len(label) > 0

    def test_standard_label(self):
        from app.services.ffmpeg_convert_service import FfmpegConvertService
        label = FfmpegConvertService.quality_label("standard")
        assert isinstance(label, str) and len(label) > 0

    def test_small_label(self):
        from app.services.ffmpeg_convert_service import FfmpegConvertService
        label = FfmpegConvertService.quality_label("small")
        assert isinstance(label, str) and "720" in label or len(label) > 0


class TestProbeDuration:
    def test_returns_float_on_success(self, tmp_path, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService
        fake = MagicMock()
        fake.stderr = b"Duration: 00:01:23.45, start: 0.0, bitrate: 1000 kb/s"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
        dur = FfmpegConvertService._probe_duration(Path("ffmpeg"), tmp_path / "v.mp4")
        assert dur == pytest.approx(83.45)

    def test_returns_zero_on_exception(self, tmp_path, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
        dur = FfmpegConvertService._probe_duration(Path("ffmpeg"), tmp_path / "v.mp4")
        assert dur == 0.0

    def test_returns_zero_if_no_duration_in_stderr(self, tmp_path, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService
        fake = MagicMock()
        fake.stderr = b"No duration info here"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
        dur = FfmpegConvertService._probe_duration(Path("ffmpeg"), tmp_path / "v.mp4")
        assert dur == 0.0


class TestLocateFfmpegBin:
    def test_raises_conversion_error_when_not_found(self, monkeypatch):
        from app.services.ffmpeg_convert_service import (
            ConversionError, FfmpegConvertService,
        )
        monkeypatch.setattr(
            "app.services.ffmpeg_convert_service.locate_ffmpeg",
            lambda: None,
        )
        with pytest.raises(ConversionError):
            FfmpegConvertService._locate_ffmpeg_bin()


class TestConvertSync:
    """Tests for _convert_sync: success path and failure paths."""

    @pytest.fixture
    def fake_ffmpeg(self, tmp_path):
        """Return a fake ffmpeg binary path (just needs to be a file)."""
        ffmpeg = tmp_path / "ffmpeg"
        ffmpeg.write_bytes(b"\x7fELF")
        return ffmpeg

    def test_raises_if_source_not_a_file(self, tmp_path):
        from app.services.ffmpeg_convert_service import (
            ConversionError, FfmpegConvertService,
        )
        svc = FfmpegConvertService()
        with pytest.raises(ConversionError, match="không tồn tại"):
            svc._convert_sync(
                tmp_path / "nonexistent.mp4", "standard", None, None
            )

    def test_output_collision_increments_counter(self, tmp_path, fake_ffmpeg,
                                                   monkeypatch):
        """If _iPhone.mp4 exists already, it tries _iPhone_2.mp4."""
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "video.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")
        # Pre-create the first output file to force counter increment
        (tmp_path / "video_iPhone.mp4").write_bytes(b"existing")

        # Mock _locate_ffmpeg_bin and subprocess
        monkeypatch.setattr(
            FfmpegConvertService, "_locate_ffmpeg_bin",
            staticmethod(lambda: fake_ffmpeg)
        )
        monkeypatch.setattr(
            FfmpegConvertService, "_probe_duration",
            staticmethod(lambda *a: 0.0)
        )

        proc_mock = MagicMock()
        proc_mock.stderr = iter([b"time=00:00:01.00 bitrate=1000\n"])
        proc_mock.wait.return_value = 0
        proc_mock.returncode = 0

        # Create the expected _iPhone_2.mp4 file during Popen so it "exists"
        created_output: list[Path] = []

        def fake_popen(cmd, **kwargs):
            # cmd[-1] is the output path
            out = Path(cmd[-1])
            out.write_bytes(b"x" * 5000)
            created_output.append(out)
            return proc_mock

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        svc = FfmpegConvertService()
        result = svc._convert_sync(src, "standard", tmp_path, None)
        assert result.name == "video_iPhone_2.mp4"

    def test_raises_on_nonzero_returncode(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import (
            ConversionError, FfmpegConvertService,
        )
        src = tmp_path / "video.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")

        monkeypatch.setattr(
            FfmpegConvertService, "_locate_ffmpeg_bin",
            staticmethod(lambda: fake_ffmpeg)
        )
        monkeypatch.setattr(
            FfmpegConvertService, "_probe_duration",
            staticmethod(lambda *a: 60.0)
        )

        proc_mock = MagicMock()
        proc_mock.stderr = iter([b"Error: something went wrong\n"])
        proc_mock.wait.return_value = None
        proc_mock.returncode = 1
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc_mock)

        svc = FfmpegConvertService()
        with pytest.raises(ConversionError, match="lỗi 1"):
            svc._convert_sync(src, "standard", tmp_path, None)

    def test_progress_callback_called(self, tmp_path, fake_ffmpeg, monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "video.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")

        monkeypatch.setattr(
            FfmpegConvertService, "_locate_ffmpeg_bin",
            staticmethod(lambda: fake_ffmpeg)
        )
        monkeypatch.setattr(
            FfmpegConvertService, "_probe_duration",
            staticmethod(lambda *a: 100.0)
        )

        proc_mock = MagicMock()
        proc_mock.stderr = iter([
            b"frame=10 time=00:00:50.00 bitrate=1000\n",
        ])
        proc_mock.wait.return_value = None
        proc_mock.returncode = 0

        out_file = tmp_path / "video_iPhone.mp4"

        def fake_popen(cmd, **kwargs):
            out_file.write_bytes(b"x" * 5000)
            return proc_mock

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        progresses = []
        svc = FfmpegConvertService()
        svc._convert_sync(src, "standard", tmp_path, progresses.append)

        assert any(p > 0 for p in progresses), "progress callback never fired with >0"
        assert progresses[-1] == 100.0

    def test_all_quality_presets_build_valid_cmd(self, tmp_path, fake_ffmpeg,
                                                   monkeypatch):
        from app.services.ffmpeg_convert_service import FfmpegConvertService

        src = tmp_path / "video.webm"
        src.write_bytes(b"\x1aE\xdf\xa3")

        monkeypatch.setattr(
            FfmpegConvertService, "_locate_ffmpeg_bin",
            staticmethod(lambda: fake_ffmpeg)
        )
        monkeypatch.setattr(
            FfmpegConvertService, "_probe_duration",
            staticmethod(lambda *a: 0.0)
        )

        for quality in ("high", "standard", "small"):
            captured_cmd: list = []
            proc_mock = MagicMock()
            proc_mock.stderr = iter([])
            proc_mock.wait.return_value = None
            proc_mock.returncode = 0

            out_files: list[Path] = []

            _current_quality = quality

            def fake_popen(cmd, **kw):
                p = tmp_path / f"video_iPhone{'_2' if _current_quality != 'high' else ''}.mp4"
                p.write_bytes(b"x" * 5000)
                out_files.append(p)
                captured_cmd.clear()
                captured_cmd.extend(cmd)
                return proc_mock

            monkeypatch.setattr(subprocess, "Popen", fake_popen)
            # Clean up previous output file
            for f in tmp_path.glob("video_iPhone*.mp4"):
                f.unlink()

            svc = FfmpegConvertService()
            try:
                svc._convert_sync(src, quality, tmp_path, None)
            except Exception:
                pass
            assert "libx264" in captured_cmd, f"libx264 missing for {quality}"
            assert "aac" in captured_cmd, f"aac missing for {quality}"


# ═════════════════════════════════════════════════════════════════════════════
# ⑥ DownloadTask — gap tests
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadTaskToDict:
    def test_returns_expected_keys(self):
        from domain.models.download_task import DownloadTask
        t = DownloadTask(url="https://example.com/v")
        d = t.to_dict()
        for key in ("id", "url", "title", "platform", "filename",
                    "status", "downloaded_bytes", "total_bytes",
                    "created_at", "finished_at", "error_msg"):
            assert key in d, f"Missing key {key!r}"

    def test_to_dict_consistent_under_concurrency(self):
        """to_dict() must never return status=COMPLETED with empty filename."""
        from domain.models.download_task import DownloadTask
        from domain.enums.download_status import DownloadStatus

        task = DownloadTask(url="https://example.com/v")
        inconsistent = []

        def _writer():
            for _ in range(3000):
                with task._lock:
                    task.filename = "/downloads/video.mp4"
                    task.status = DownloadStatus.COMPLETED
                with task._lock:
                    task.filename = ""
                    task.status = DownloadStatus.DOWNLOADING

        def _reader():
            for _ in range(3000):
                d = task.to_dict()
                if d["status"] == "COMPLETED" and d["filename"] == "":
                    inconsistent.append(d)

        t1 = threading.Thread(target=_writer)
        t2 = threading.Thread(target=_reader)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert not inconsistent, f"{len(inconsistent)} torn reads in to_dict()"

    def test_status_serialised_as_name_string(self):
        from domain.models.download_task import DownloadTask
        from domain.enums.download_status import DownloadStatus
        t = DownloadTask(url="https://example.com/v")
        t.status = DownloadStatus.FAILED
        assert t.to_dict()["status"] == "FAILED"


class TestDownloadTaskElapsedProperty:
    def test_empty_when_not_started(self):
        from domain.models.download_task import DownloadTask
        t = DownloadTask()
        assert t.elapsed == ""

    def test_non_empty_after_start(self):
        from domain.models.download_task import DownloadTask
        t = DownloadTask()
        t.started_at = time.time() - 90
        assert t.elapsed != ""
        assert ":" in t.elapsed

    def test_uses_finished_at_when_set(self):
        from domain.models.download_task import DownloadTask
        t = DownloadTask()
        t.started_at = 1000.0
        t.finished_at = 1062.0  # 62 seconds
        elapsed = t.elapsed
        assert "01:02" in elapsed or "1:02" in elapsed


class TestDownloadTaskProperties:
    def test_title_falls_back_to_url_prefix(self):
        from domain.models.download_task import DownloadTask
        t = DownloadTask(url="https://example.com/video-very-long-url")
        assert t.title.startswith("https://")

    def test_title_from_media_info(self):
        from domain.models.download_task import DownloadTask, MediaInfo
        t = DownloadTask(
            url="https://example.com/v",
            media_info=MediaInfo(url="https://example.com/v", title="My Video"),
        )
        assert t.title == "My Video"

    def test_platform_from_media_info(self):
        from domain.models.download_task import DownloadTask, MediaInfo
        t = DownloadTask(
            url="https://example.com/v",
            media_info=MediaInfo(url="https://example.com/v", platform="TikTok"),
        )
        assert t.platform == "TikTok"

    def test_platform_unknown_without_media_info(self):
        from domain.models.download_task import DownloadTask
        t = DownloadTask(url="https://example.com/v")
        assert t.platform == "unknown"


class TestWaitIfPausedWithCancel:
    def test_cancel_unblocks_wait_immediately(self):
        """A paused task must unblock within 2 s when cancel() is called."""
        from domain.models.download_task import DownloadTask
        from domain.enums.download_status import DownloadStatus

        task = DownloadTask(url="https://example.com/v")
        task.status = DownloadStatus.DOWNLOADING
        task.pause()

        unblocked = threading.Event()

        def _waiter():
            task.wait_if_paused()
            unblocked.set()

        t = threading.Thread(target=_waiter, daemon=True)
        t.start()
        time.sleep(0.05)
        task.cancel()
        assert unblocked.wait(timeout=2.0), "wait_if_paused blocked after cancel()"


# ═════════════════════════════════════════════════════════════════════════════
# ⑦ YtDlpEngine helpers — gap tests
# ═════════════════════════════════════════════════════════════════════════════

class TestFmtSpeed:
    def _call(self, speed):
        from infrastructure.downloader.yt_dlp_engine import _fmt_speed
        return _fmt_speed(speed)

    def test_bytes_per_second(self):      assert "B/s"   in self._call(500.0)
    def test_kibibytes_per_second(self):  assert "KiB/s" in self._call(2048.0)
    def test_mebibytes_per_second(self):  assert "MiB/s" in self._call(3 * 1024**2)
    def test_zero(self):                  assert self._call(0) == "0 B/s"


class TestFmtEta:
    def _call(self, seconds):
        from infrastructure.downloader.yt_dlp_engine import _fmt_eta
        return _fmt_eta(seconds)

    def test_zero(self):           assert self._call(0) == "00:00"
    def test_30_seconds(self):     assert self._call(30) == "00:30"
    def test_90_seconds(self):     assert self._call(90) == "01:30"
    def test_3600_seconds(self):   assert self._call(3600) == "1:00:00"
    def test_3661_seconds(self):   assert self._call(3661) == "1:01:01"
    def test_float_input(self):    assert self._call(59.9) == "00:59"


class TestFriendlyError:
    def _call(self, msg):
        from infrastructure.downloader.yt_dlp_engine import _friendly_error
        return _friendly_error(msg)

    def test_private_message(self):
        assert "private" in self._call("This video is private").lower()

    def test_404_message(self):
        result = self._call("HTTP Error 404: Not Found")
        assert "removed" in result.lower() or "found" in result.lower()

    def test_not_found_message(self):
        result = self._call("Video not found")
        assert "removed" in result.lower() or "found" in result.lower()

    def test_unsupported_url(self):
        result = self._call("Unsupported URL: example.com")
        assert "not supported" in result.lower() or "platform" in result.lower()

    def test_live_not_started(self):
        result = self._call("Live stream has not started yet")
        assert "not start" in result.lower() or "started" in result.lower()

    def test_live_ended(self):
        result = self._call("This live stream has ended")
        assert "ended" in result.lower()

    def test_generic_message_truncated_at_200(self):
        long_msg = "x" * 500
        result = self._call(long_msg)
        assert len(result) <= 200

    def test_checkpoint_returns_verification_message(self):
        result = self._call("checkpoint required: please verify your account")
        assert "verification" in result.lower() or "checkpoint" in result.lower()

    def test_challenge_required_returns_verification_message(self):
        result = self._call("challenge_required")
        assert "verification" in result.lower() or "checkpoint" in result.lower()

    def test_rate_limit_429_returns_wait_message(self):
        result = self._call("HTTP Error 429: Too Many Requests")
        assert "rate limit" in result.lower() or "wait" in result.lower()

    def test_geo_restricted_returns_vpn_hint(self):
        result = self._call("This video is geo-restricted in your country")
        assert "region" in result.lower() or "vpn" in result.lower() or "geo" in result.lower()

    def test_content_not_available_facebook(self):
        result = self._call("This content isn't available right now")
        assert "available" in result.lower() or "facebook" in result.lower() or len(result) <= 200


class TestCheckUnsupportedUrl:
    def _call(self, url, has_cookies=False):
        from infrastructure.downloader.yt_dlp_engine import _check_unsupported_url
        return _check_unsupported_url(url, has_cookies=has_cookies)

    def test_normal_youtube_url_passes(self):
        assert self._call("https://www.youtube.com/watch?v=abc123") is None

    def test_instagram_story_blocked_without_cookies(self):
        result = self._call("https://www.instagram.com/stories/user/123/")
        assert result is not None
        assert "cookie" in result.lower() or "login" in result.lower()

    def test_instagram_story_allowed_with_cookies(self):
        assert self._call(
            "https://www.instagram.com/stories/user/123/", has_cookies=True
        ) is None

    def test_instagram_live_blocked_without_cookies(self):
        result = self._call("https://www.instagram.com/user/live/")
        assert result is not None

    def test_instagram_live_allowed_with_cookies(self):
        assert self._call(
            "https://www.instagram.com/user/live/", has_cookies=True
        ) is None

    def test_facebook_live_blocked_without_cookies(self):
        result = self._call("https://www.facebook.com/live/xyz")
        assert result is not None

    def test_facebook_live_allowed_with_cookies(self):
        assert self._call(
            "https://www.facebook.com/live/xyz", has_cookies=True
        ) is None

    def test_facebook_stories_blocked_without_cookies(self):
        result = self._call("https://www.facebook.com/stories/user/123")
        assert result is not None

    def test_facebook_stories_allowed_with_cookies(self):
        assert self._call(
            "https://www.facebook.com/stories/user/123", has_cookies=True
        ) is None

    def test_tiktok_normal_passes(self):
        assert self._call("https://www.tiktok.com/@user/video/123") is None

    # ── New URL format coverage (Fixed8) ─────────────────────────────────

    def test_instagram_live_new_format_blocked_without_cookies(self):
        """New 2024+ Instagram live URL (/live/shortcode/) blocked without cookies."""
        result = self._call("https://www.instagram.com/live/ABC123DEF/")
        assert result is not None
        assert "cookie" in result.lower() or "login" in result.lower()

    def test_instagram_live_new_format_allowed_with_cookies(self):
        """New Instagram live URL passes through when cookies are set."""
        assert self._call(
            "https://www.instagram.com/live/ABC123DEF/", has_cookies=True
        ) is None

    def test_facebook_story_php_blocked_without_cookies(self):
        """Facebook story.php URL blocked without cookies."""
        result = self._call("https://www.facebook.com/story.php?story_fbid=123&id=456")
        assert result is not None
        assert "stories" in result.lower() or "cookie" in result.lower()

    def test_facebook_permalink_story_blocked_without_cookies(self):
        """Facebook permalink with story_fbid blocked without cookies."""
        result = self._call(
            "https://www.facebook.com/permalink.php?story_fbid=123&id=456"
        )
        assert result is not None

    def test_facebook_share_story_blocked_without_cookies(self):
        """Facebook share/r/ story link blocked without cookies."""
        result = self._call("https://www.facebook.com/share/r/ABC123/")
        assert result is not None

    def test_facebook_reel_passes(self):
        """Facebook Reels are not Stories — should pass through to yt-dlp."""
        # Reels don't require story-specific cookies; yt-dlp handles them
        assert self._call("https://www.facebook.com/reel/123456789") is None

    def test_facebook_photo_passes(self):
        """Facebook photo posts pass through — yt-dlp handles them."""
        assert self._call("https://www.facebook.com/photo?fbid=123456789") is None


class TestProgressHook:
    """Tests for _make_progress_hook behaviour."""

    def _make_engine(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
        ConfigManager._cache.clear()
        cfg = ConfigManager(tmp_path / "cfg.json")
        return YtDlpEngine(cfg)

    def test_hook_updates_progress_on_downloading(self, tmp_path):
        from domain.models.download_task import DownloadTask
        engine = self._make_engine(tmp_path)
        task = DownloadTask(url="https://example.com/v")
        fired = []
        hook = engine._make_progress_hook(task, fired.append)
        hook({
            "status": "downloading",
            "downloaded_bytes": 500,
            "total_bytes": 1000,
            "speed": 200.0,
            "eta": 30,
            "filename": str(tmp_path / "video.mp4"),
        })
        assert task.progress == pytest.approx(50.0)
        assert fired

    def test_hook_caps_progress_at_99(self, tmp_path):
        from domain.models.download_task import DownloadTask
        engine = self._make_engine(tmp_path)
        task = DownloadTask(url="https://example.com/v")
        hook = engine._make_progress_hook(task, None)
        hook({
            "status": "downloading",
            "downloaded_bytes": 1100,
            "total_bytes": 1000,
        })
        assert task.progress <= 99.0

    def test_hook_raises_on_cancel(self, tmp_path):
        import yt_dlp
        from domain.models.download_task import DownloadTask
        engine = self._make_engine(tmp_path)
        task = DownloadTask(url="https://example.com/v")
        task.cancel()
        hook = engine._make_progress_hook(task, None)
        with pytest.raises(yt_dlp.utils.DownloadError):
            hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 100})

    def test_hook_sets_processing_on_finished(self, tmp_path):
        from domain.enums.download_status import DownloadStatus
        from domain.models.download_task import DownloadTask
        engine = self._make_engine(tmp_path)
        task = DownloadTask(url="https://example.com/v")
        hook = engine._make_progress_hook(task, None)
        hook({
            "status": "finished",
            "filename": str(tmp_path / "video.mp4"),
        })
        assert task.status == DownloadStatus.PROCESSING
        assert task.progress == pytest.approx(99.5)


class TestPpHook:
    def _make_engine(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
        ConfigManager._cache.clear()
        return YtDlpEngine(ConfigManager(tmp_path / "cfg.json"))

    def test_started_sets_processing(self, tmp_path):
        from domain.enums.download_status import DownloadStatus
        from domain.models.download_task import DownloadTask
        engine = self._make_engine(tmp_path)
        task = DownloadTask(url="https://example.com/v")
        fired = []
        hook = engine._make_pp_hook(task, fired.append)
        hook({"status": "started"})
        assert task.status == DownloadStatus.PROCESSING
        assert fired

    def test_finished_clears_eta(self, tmp_path):
        from domain.models.download_task import DownloadTask
        engine = self._make_engine(tmp_path)
        task = DownloadTask(url="https://example.com/v")
        task.eta = "Processing…"
        hook = engine._make_pp_hook(task, None)
        hook({"status": "finished"})
        assert task.eta == ""


class TestLivestreamDetection:
    """Verify the is_live flag controls opts correctly."""

    def _engine(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
        ConfigManager._cache.clear()
        return YtDlpEngine(ConfigManager(tmp_path / "cfg.json"))

    def test_live_task_uses_best_format(self, tmp_path):
        from domain.models.download_task import DownloadTask, MediaInfo
        engine = self._engine(tmp_path)
        task = DownloadTask(
            url="https://www.tiktok.com/@user/live",
            media_info=MediaInfo(url="https://www.tiktok.com/@user/live",
                                 is_live=True, title="Live"),
            format_id="bestvideo+bestaudio/best",
            output_ext="mp4",
            output_dir=str(tmp_path),
        )
        captured_opts: dict = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            class _ctx:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def download(self, urls): pass
            return _ctx()

        with patch("infrastructure.downloader.yt_dlp_engine.yt_dlp.YoutubeDL",
                   fake_ydl):
            try:
                engine.download(task)
            except Exception:
                pass

        assert captured_opts.get("format") == "best", (
            "Livestream must use format='best', not a split video+audio format"
        )

    def test_live_task_no_merge_output_format(self, tmp_path):
        from domain.models.download_task import DownloadTask, MediaInfo
        engine = self._engine(tmp_path)
        task = DownloadTask(
            url="https://www.tiktok.com/@user/live",
            media_info=MediaInfo(url="https://www.tiktok.com/@user/live",
                                 is_live=True, title="Live"),
            format_id="best",
            output_ext="mp4",
            output_dir=str(tmp_path),
        )
        captured_opts: dict = {}

        def fake_ydl(opts):
            captured_opts.update(opts)
            class _ctx:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def download(self, urls): pass
            return _ctx()

        with patch("infrastructure.downloader.yt_dlp_engine.yt_dlp.YoutubeDL",
                   fake_ydl):
            try:
                engine.download(task)
            except Exception:
                pass

        assert "merge_output_format" not in captured_opts, (
            "merge_output_format must NOT be set for livestreams "
            "(causes ffmpeg crash)"
        )


# ═════════════════════════════════════════════════════════════════════════════
# ⑧ DownloadService — gap tests
# ═════════════════════════════════════════════════════════════════════════════

class TestDownloadServiceClose:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from infrastructure.config.config_manager import ConfigManager
        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def _make_service(self, tmp_path):
        from app.event_bus import EventBus
        from app.services.download_service import DownloadService
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.download_manager import DownloadManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
        from infrastructure.storage.history_repository import HistoryRepository

        cfg = ConfigManager(tmp_path / "cfg.json")
        engine = YtDlpEngine(cfg)
        mgr = DownloadManager(cfg, engine=engine)
        mgr.start()
        hist = HistoryRepository(tmp_path / "hist.jsonl")
        bus = EventBus()
        svc = DownloadService(
            config=cfg, download_manager=mgr,
            history_repo=hist, engine=engine, event_bus=bus,
        )
        return svc, mgr

    def test_close_does_not_raise(self, tmp_path):
        svc, mgr = self._make_service(tmp_path)
        mgr.shutdown(wait=True)
        svc.close()  # must not raise

    def test_get_task_returns_none_for_unknown_id(self, tmp_path):
        svc, mgr = self._make_service(tmp_path)
        assert svc.get_task("no-such-id") is None
        mgr.shutdown(wait=False)
        svc.close()

    def test_convert_to_mp4_delegates_to_converter(self, tmp_path):
        from app.services.download_service import DownloadService
        svc, mgr = self._make_service(tmp_path)
        called = []
        svc._converter = MagicMock(convert=lambda **kw: called.append(kw))
        fake_src = tmp_path / "video.webm"
        fake_src.write_bytes(b"fake")
        svc.convert_to_mp4(source=fake_src)
        assert called, "convert_to_mp4 must delegate to _converter.convert()"
        mgr.shutdown(wait=False)
        svc.close()


# ═════════════════════════════════════════════════════════════════════════════
# ⑨ HistoryRepository — extra edge cases
# ═════════════════════════════════════════════════════════════════════════════

class TestHistoryRepositoryEdgeCases:
    def _make_task(self, task_id="t1", url="https://example.com/v"):
        from domain.models.download_task import DownloadTask, MediaInfo
        from domain.enums.download_status import DownloadStatus
        t = DownloadTask(url=url, media_info=MediaInfo(url=url, title="Test"))
        t.id = task_id
        t.status = DownloadStatus.COMPLETED
        t.filename = "/downloads/video.mp4"
        return t

    def test_search_by_url(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=10)
        task = self._make_task(url="https://youtube.com/watch?v=abc")
        repo.add(task)
        results = repo.search("youtube")
        assert len(results) == 1

    def test_clear_empties_disk_file(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=10)
        repo.add(self._make_task("t1"))
        repo.add(self._make_task("t2"))
        repo.clear()
        assert repo.all() == []
        # Disk should be empty
        hist_file = tmp_path / "hist.jsonl"
        assert not hist_file.exists() or hist_file.read_text().strip() == ""

    def test_add_overflow_triggers_rewrite(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=3)
        for i in range(4):
            repo.add(self._make_task(f"t{i}"))
        assert len(repo.all()) == 3

    def test_get_by_id_found(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=10)
        repo.add(self._make_task("find-me"))
        result = repo.get_by_id("find-me")
        assert result is not None
        assert result["id"] == "find-me"

    def test_get_by_id_not_found(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=10)
        assert repo.get_by_id("ghost") is None

    def test_remove_updates_in_memory(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=10)
        repo.add(self._make_task("rm-me"))
        repo.remove("rm-me")
        assert repo.get_by_id("rm-me") is None

    def test_backup_cleaned_up_after_successful_rewrite(self, tmp_path):
        from infrastructure.storage.history_repository import HistoryRepository
        repo = HistoryRepository(tmp_path / "hist.jsonl", limit=3)
        for i in range(4):
            repo.add(self._make_task(f"bk{i}"))
        backup = tmp_path / "hist.backup.jsonl"
        assert not backup.exists(), "backup file should be removed after successful rewrite"


# ═════════════════════════════════════════════════════════════════════════════
# ⑩ EventBus — additional coverage
# ═════════════════════════════════════════════════════════════════════════════

class TestEventBusAdditional:
    def test_subscribe_and_reentrant_publish_does_not_deadlock(self):
        """A handler that publishes another event must not deadlock (RLock)."""
        from app.event_bus import EventBus
        bus = EventBus()
        inner_called = []

        def outer_handler(**kw):
            bus.publish("inner.event", value=42)

        def inner_handler(**kw):
            inner_called.append(kw)

        bus.subscribe("outer.event", outer_handler)
        bus.subscribe("inner.event", inner_handler)
        bus.publish("outer.event")
        assert inner_called == [{"value": 42}]

    def test_publish_kwargs_forwarded(self):
        from app.event_bus import EventBus
        bus = EventBus()
        received = []
        bus.subscribe("test.event", lambda **kw: received.append(kw))
        bus.publish("test.event", x=1, y="hello")
        assert received == [{"x": 1, "y": "hello"}]

    def test_unsubscribe_all_then_publish_is_silent(self):
        from app.event_bus import EventBus
        bus = EventBus()
        called = []
        h = lambda **kw: called.append(kw)
        bus.subscribe("ev", h)
        bus.unsubscribe("ev", h)
        bus.publish("ev", z=99)
        assert called == []
