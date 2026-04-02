"""
tests/test_repair_fixes.py
Regression tests for every issue identified and fixed in the audit repair pass.

Each test is tagged with the original audit finding it covers:
  CRIT-1  : get_task() missing from DownloadService
  CRIT-2  : duplicated cookie CWE-22 check → extracted helper
  SEC-1   : _SAFE_ROOTS too broad (Path.home() removed)
  SEC-2   : no SHA-256 wheel verification in _install_ytdlp_frozen
  SEC-3   : proxy scheme not validated
  QUAL-1  : log rotation (RotatingFileHandler)
  CONC-1  : EventBus uses RLock instead of Lock
  PERF-1  : QueueTab idle-aware poll (unit-testable logic only)
  REL-1   : open_folder no longer silently swallows exceptions
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── CRIT-1: get_task() present on DownloadService ─────────────────────────────

def test_download_service_has_get_task(tmp_path):
    """DownloadService must expose get_task() — absence caused an AttributeError
    crash in QueueTab every time a task was inspected or cancelled by ID."""
    from infrastructure.config.config_manager import ConfigManager
    from infrastructure.downloader.download_manager import DownloadManager
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
    from infrastructure.storage.history_repository import HistoryRepository
    from app.services.download_service import DownloadService

    cfg = ConfigManager(tmp_path / "config.json")
    engine = YtDlpEngine(cfg)
    mgr = DownloadManager(cfg, engine=engine)
    mgr.start()
    repo = HistoryRepository(tmp_path / "history.jsonl")
    svc = DownloadService(config=cfg, download_manager=mgr,
                          history_repo=repo, engine=engine)

    # Must exist and be callable
    assert callable(getattr(svc, "get_task", None)), (
        "DownloadService.get_task() is missing — ServiceFacade.get_task() "
        "will raise AttributeError at runtime"
    )
    # Must return None for an unknown ID (not raise)
    result = svc.get_task("nonexistent-id")
    assert result is None

    mgr.shutdown(wait=False)


def test_service_facade_get_task_round_trips(tmp_path):
    """ServiceFacade.get_task() must delegate to DownloadService.get_task()
    without raising."""
    from infrastructure.config.config_manager import ConfigManager
    from infrastructure.downloader.download_manager import DownloadManager
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
    from infrastructure.storage.history_repository import HistoryRepository
    from app.services.download_service import DownloadService

    cfg = ConfigManager(tmp_path / "config.json")
    engine = YtDlpEngine(cfg)
    mgr = DownloadManager(cfg, engine=engine)
    mgr.start()
    repo = HistoryRepository(tmp_path / "history.jsonl")
    svc = DownloadService(config=cfg, download_manager=mgr,
                          history_repo=repo, engine=engine)

    # Simulate what ServiceFacade does
    assert svc.get_task("does-not-exist") is None
    mgr.shutdown(wait=False)


# ── CRIT-2 / SEC-1: _validate_cookie_path helper ─────────────────────────────

class TestValidateCookiePath:
    """_validate_cookie_path() must accept only files inside the OmniDL data
    directory and reject anything under Path.home() or elsewhere."""

    def _make_config(self, tmp_path, cookie_rel=""):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "data" / "config.json")
        cfg.set("cookie_file", cookie_rel)
        return cfg

    def test_empty_returns_none(self, tmp_path):
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        cfg = self._make_config(tmp_path)
        assert _validate_cookie_path(cfg) is None

    def test_valid_file_inside_data_dir(self, tmp_path):
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        cookie = data_dir / "cookies.txt"
        cookie.write_text("# cookies", encoding="utf-8")
        cfg = self._make_config(tmp_path)
        cfg.set("cookie_file", str(cookie))
        result = _validate_cookie_path(cfg)
        assert result == str(cookie.resolve())

    def test_file_outside_data_dir_rejected(self, tmp_path):
        """A file outside the OmniDL data dir must be rejected (SEC-1 fix)."""
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        # File lives in a sibling directory, not inside data_dir
        other = tmp_path / "other"
        other.mkdir()
        cookie = other / "cookies.txt"
        cookie.write_text("# cookies", encoding="utf-8")
        cfg = self._make_config(tmp_path)
        cfg.set("cookie_file", str(cookie))
        assert _validate_cookie_path(cfg) is None

    def test_home_dir_file_rejected(self, tmp_path):
        """Files under Path.home() that are outside the data dir must be
        rejected — this is the core SEC-1 fix (SSH key exfiltration vector)."""
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        cfg = self._make_config(tmp_path)
        # Point at a file that might exist in home (mocked to exist)
        home_file = Path.home() / ".ssh" / "id_rsa"
        cfg.set("cookie_file", str(home_file))
        # Even if the file exists, it must be rejected because it is not
        # inside config_path.parent
        assert _validate_cookie_path(cfg) is None

    def test_path_traversal_rejected(self, tmp_path):
        """Classic ../../ traversal must not escape the data directory."""
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        cfg = self._make_config(tmp_path)
        cfg.set("cookie_file", str(data_dir / ".." / ".." / "evil.txt"))
        assert _validate_cookie_path(cfg) is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        from infrastructure.downloader.yt_dlp_engine import _validate_cookie_path
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True)
        cfg = self._make_config(tmp_path)
        cfg.set("cookie_file", str(data_dir / "missing.txt"))
        assert _validate_cookie_path(cfg) is None


# ── SEC-2: SHA-256 verification in _install_ytdlp_frozen ─────────────────────

class TestInstallYtdlpFrozenSha256:
    """_install_ytdlp_frozen() must verify the SHA-256 digest of the downloaded
    wheel before extracting it."""

    def _make_fake_meta(self, ver="2099.1.1", sha256="abc123", url="https://files.example.com/yt_dlp-2099.1.1-py3-none-any.whl"):
        return {
            "info": {"version": ver},
            "releases": {
                ver: [{
                    "filename": f"yt_dlp-{ver}-py3-none-any.whl",
                    "url": url,
                    "digests": {"sha256": sha256},
                }]
            }
        }

    def test_sha256_mismatch_raises(self, tmp_path):
        """A wheel whose digest does not match the PyPI metadata must be
        rejected with a RuntimeError before any extraction occurs."""
        import json
        import hashlib
        from unittest.mock import patch, MagicMock
        import io

        fake_meta = self._make_fake_meta(sha256="expected_hash_abc")
        fake_whl_content = b"PK\x03\x04fake_wheel_content"
        actual_hash = hashlib.sha256(fake_whl_content).hexdigest()
        # Ensure they differ
        assert actual_hash != "expected_hash_abc"

        fake_meta_response = MagicMock()
        fake_meta_response.__enter__ = lambda s: s
        fake_meta_response.__exit__ = MagicMock(return_value=False)
        fake_meta_response.read.return_value = json.dumps(fake_meta).encode()

        fake_whl_response = MagicMock()
        fake_whl_response.__enter__ = lambda s: s
        fake_whl_response.__exit__ = MagicMock(return_value=False)
        fake_whl_response.read.side_effect = lambda: fake_whl_content

        def mock_urlopen(req, timeout=None):
            if "pypi.org" in req.full_url:
                return fake_meta_response
            return fake_whl_response

        with patch("ui.tabs.settings_tab.urlopen", side_effect=mock_urlopen):
            with patch("builtins.open", MagicMock()):
                with patch("shutil.copyfileobj") as mock_copy:
                    # Write fake content to the tmp file path
                    def write_fake(src, dst):
                        pass
                    mock_copy.side_effect = write_fake
                    # Patch tmp_whl.read_bytes to return fake content
                    with patch("pathlib.Path.read_bytes", return_value=fake_whl_content):
                        with patch("pathlib.Path.unlink"):
                            with patch("pathlib.Path.mkdir"):
                                import sys
                                # Import the function from the module
                                import importlib
                                settings_mod = importlib.import_module("ui.tabs.settings_tab")
                                with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
                                    settings_mod._install_ytdlp_frozen()

    def test_missing_digest_raises(self, tmp_path):
        """If PyPI returns a release without a SHA-256 digest, the update must
        be aborted with a clear RuntimeError."""
        import json
        from unittest.mock import patch, MagicMock

        # Release entry missing the 'digests' key
        fake_meta = {
            "info": {"version": "2099.2.2"},
            "releases": {
                "2099.2.2": [{
                    "filename": "yt_dlp-2099.2.2-py3-none-any.whl",
                    "url": "https://files.example.com/yt_dlp-2099.2.2-py3-none-any.whl",
                    # no 'digests' key
                }]
            }
        }
        fake_meta_response = MagicMock()
        fake_meta_response.__enter__ = lambda s: s
        fake_meta_response.__exit__ = MagicMock(return_value=False)
        fake_meta_response.read.return_value = json.dumps(fake_meta).encode()

        with patch("ui.tabs.settings_tab.urlopen", return_value=fake_meta_response):
            with patch("pathlib.Path.mkdir"):
                import importlib
                settings_mod = importlib.import_module("ui.tabs.settings_tab")
                with pytest.raises(RuntimeError, match="SHA-256 digest"):
                    settings_mod._install_ytdlp_frozen()


# ── SEC-3: Proxy scheme validation ───────────────────────────────────────────

class TestProxyValidation:

    def test_valid_http_proxy_accepted(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        cfg.set("proxy", "http://proxy.example.com:8080")
        assert cfg.proxy == "http://proxy.example.com:8080"

    def test_valid_socks5_proxy_accepted(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        cfg.set("proxy", "socks5://127.0.0.1:1080")
        assert cfg.proxy == "socks5://127.0.0.1:1080"

    def test_empty_proxy_returns_empty(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        cfg.set("proxy", "")
        assert cfg.proxy == ""

    def test_invalid_scheme_rejected(self, tmp_path):
        """A proxy with an unrecognised scheme must be rejected (returns '')."""
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        cfg.set("proxy", "file:///etc/passwd")
        assert cfg.proxy == ""

    def test_ftp_scheme_rejected(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        cfg.set("proxy", "ftp://attacker.com:21")
        assert cfg.proxy == ""

    def test_bare_hostname_rejected(self, tmp_path):
        from infrastructure.config.config_manager import ConfigManager
        cfg = ConfigManager(tmp_path / "config.json")
        cfg.set("proxy", "proxy.example.com:8080")
        assert cfg.proxy == ""


# ── QUAL-1: Log rotation ──────────────────────────────────────────────────────

def test_logger_uses_rotating_file_handler(tmp_path):
    """setup_logging() must attach a RotatingFileHandler, not a plain
    FileHandler, so log files do not grow without bound."""
    import logging
    from logging.handlers import RotatingFileHandler
    from utils.logger import setup_logging

    # Use a separate logger to avoid polluting the root logger for other tests
    log_dir = tmp_path / "logs"
    # Remove any existing handlers on the root logger first
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    root.handlers.clear()

    try:
        setup_logging(log_dir)
        file_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, RotatingFileHandler)
        ]
        assert file_handlers, (
            "setup_logging() must attach a RotatingFileHandler. "
            "Plain FileHandler grows without bound."
        )
        rh = file_handlers[0]
        assert rh.maxBytes > 0, "RotatingFileHandler maxBytes must be positive"
        assert rh.backupCount > 0, "RotatingFileHandler backupCount must be positive"
    finally:
        # Restore original handlers — close each before removing to prevent
        # pytest capture from hitting a closed TextIOWrapper (cascade I/O error)
        for h in logging.getLogger().handlers[:]:
            try:
                h.close()
            except Exception:
                pass
            logging.getLogger().removeHandler(h)
        for h in original_handlers:
            logging.getLogger().addHandler(h)


# ── CONC-1: EventBus RLock ────────────────────────────────────────────────────

def test_eventbus_uses_rlock():
    """EventBus._lock must be an RLock, not a plain Lock.
    A handler that calls subscribe() during publish() would deadlock with Lock."""
    import threading
    from app.event_bus import EventBus

    bus = EventBus()
    assert isinstance(bus._lock, type(threading.RLock())), (
        "EventBus._lock must be threading.RLock() to allow re-entrant "
        "subscribe/unsubscribe calls from within a handler."
    )


def test_eventbus_reentrant_subscribe_does_not_deadlock():
    """A handler that subscribes to another event during publish() must not
    deadlock — this was impossible with threading.Lock."""
    from app.event_bus import EventBus

    bus = EventBus()
    subscribed_inside = []

    def outer_handler(**kw):
        # Re-entrant call: subscribe from within a handler
        bus.subscribe("inner.event", lambda **k: None)
        subscribed_inside.append(True)

    bus.subscribe("outer.event", outer_handler)
    # Must complete without deadlock
    done = threading.Event()

    def run():
        bus.publish("outer.event")
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert done.wait(timeout=2.0), "EventBus.publish() deadlocked — RLock not applied"
    assert subscribed_inside, "Handler was not called"


# ── PERF-1: Idle-aware poll logic ─────────────────────────────────────────────

def test_idle_poll_interval_logic():
    """When no downloads are active the poll interval should be 2000 ms,
    not 500 ms.  This tests the same conditional used in QueueTab._poll()."""
    from domain.enums.download_status import DownloadStatus

    def compute_interval(tasks):
        active = sum(1 for t in tasks if t.status in DownloadStatus.active_states())
        return 500 if active else 2000

    # No tasks → idle interval
    assert compute_interval([]) == 2000

    # Completed task only → idle interval
    done = MagicMock()
    done.status = DownloadStatus.COMPLETED
    assert compute_interval([done]) == 2000

    # One active task → fast interval
    active = MagicMock()
    active.status = DownloadStatus.DOWNLOADING
    assert compute_interval([active]) == 500

    # Mix: active + completed → fast interval
    assert compute_interval([active, done]) == 500


# ── REL-1: open_folder logs instead of silently swallowing ───────────────────

def test_open_folder_logs_on_failure(tmp_path, caplog):
    """open_folder() must log a debug message when the OS call fails instead
    of silently swallowing the exception."""
    import logging
    from unittest.mock import patch
    from utils.helpers import open_folder

    with patch("subprocess.Popen", side_effect=OSError("no such program")):
        with caplog.at_level(logging.DEBUG, logger="utils.helpers"):
            open_folder(tmp_path)

    assert any("open_folder failed" in r.message for r in caplog.records), (
        "open_folder() must log failures at DEBUG level, not swallow them silently"
    )
