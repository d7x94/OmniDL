"""
tests/test_patch_fixes.py
Tests verifying the four bugs fixed by omnidl_audit_fixes.patch.
Run with: pytest tests/test_patch_fixes.py -v
"""

import json
import pathlib
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


# ─────────────────────────────────────────────────────────────────────────────
# BUG-1 — _check_network: socket leak + global timeout
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckNetwork:
    """ui/components/status_bar.py :: _check_network()"""

    def _import(self):
        from ui.components.status_bar import _check_network

        return _check_network

    def test_socket_closed_on_success(self):
        """Socket must be closed even on a successful connection."""
        closed = []

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                closed.append(True)

            def settimeout(self, t):
                pass

            def connect(self, addr):
                pass

        with patch("socket.socket", return_value=_FakeSock()):
            fn = self._import()
            result = fn()

        assert result is True
        assert closed == [True], "Socket was not closed after successful connect"

    def test_socket_closed_on_failure(self):
        """Socket must be closed even when connect() raises."""
        closed = []

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                closed.append(True)

            def settimeout(self, t):
                pass

            def connect(self, addr):
                raise OSError("refused")

        with patch("socket.socket", return_value=_FakeSock()):
            fn = self._import()
            result = fn()

        assert result is False
        assert closed == [True], "Socket was not closed after failed connect"

    def test_setdefaulttimeout_never_called(self):
        """Global socket.setdefaulttimeout() must NOT be called — it is not thread-safe."""

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def settimeout(self, t):
                pass

            def connect(self, addr):
                pass

        with (
            patch("socket.socket", return_value=_FakeSock()),
            patch("socket.setdefaulttimeout") as mock_global,
        ):
            fn = self._import()
            fn()

        mock_global.assert_not_called()

    def test_per_socket_timeout_set(self):
        """s.settimeout() must be called with the module constant."""
        timeouts = []

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def settimeout(self, t):
                timeouts.append(t)

            def connect(self, addr):
                pass

        from ui.components import status_bar as sb

        with patch("socket.socket", return_value=_FakeSock()):
            fn = self._import()
            fn()

        assert timeouts == [sb._NET_CHECK_TIMEOUT]

    def test_returns_false_on_connection_error(self):
        """Any OSError from connect must return False, not raise."""

        class _FakeSock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def settimeout(self, t):
                pass

            def connect(self, addr):
                raise OSError("network unreachable")

        with patch("socket.socket", return_value=_FakeSock()):
            fn = self._import()
            assert fn() is False


# ─────────────────────────────────────────────────────────────────────────────
# BUG-2 — _parse_speed: IEC units (MiB/s, KiB/s) unrecognised
# ─────────────────────────────────────────────────────────────────────────────


class TestParseSpeed:
    """ui/components/status_bar.py :: StatusBar._parse_speed()"""

    @pytest.fixture(autouse=True)
    def _import(self):
        from ui.components.status_bar import _parse_speed

        self.fn = _parse_speed

    # ── IEC units (what yt-dlp engine actually emits) ─────────────────────

    def test_mib_per_sec(self):
        assert self.fn("5.2 MiB/s") == pytest.approx(5.2 * 1024**2)

    def test_kib_per_sec(self):
        assert self.fn("843 KiB/s") == pytest.approx(843 * 1024)

    def test_gib_per_sec(self):
        assert self.fn("1.1 GiB/s") == pytest.approx(1.1 * 1024**3)

    def test_mib_case_insensitive(self):
        assert self.fn("3.0 mib/s") == pytest.approx(3.0 * 1024**2)

    # ── SI units (forward-compat) ─────────────────────────────────────────

    def test_mb_per_sec(self):
        assert self.fn("3.0 MB/s") == pytest.approx(3.0 * 1024**2)

    def test_kb_per_sec(self):
        assert self.fn("100 KB/s") == pytest.approx(100 * 1024)

    def test_gb_per_sec(self):
        assert self.fn("1.0 GB/s") == pytest.approx(1.0 * 1024**3)

    # ── Edge cases ────────────────────────────────────────────────────────

    def test_bytes_per_sec(self):
        assert self.fn("500 B/s") == pytest.approx(500.0)

    def test_empty_string_returns_zero(self):
        assert self.fn("") == 0.0

    def test_garbage_returns_zero(self):
        assert self.fn("N/A") == 0.0

    def test_old_bug_regression(self):
        """5.2 MiB/s must NOT be interpreted as 5.2 bytes/s (the original bug)."""
        result = self.fn("5.2 MiB/s")
        assert result > 1024, "IEC unit parsed as raw bytes — regression of BUG-2"


# ─────────────────────────────────────────────────────────────────────────────
# BUG-3 — ConfigManager._load: missing cache write after disk read
# ─────────────────────────────────────────────────────────────────────────────


class TestConfigManagerCacheLoad:
    """infrastructure/config/config_manager.py :: ConfigManager._load()"""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        from infrastructure.config.config_manager import ConfigManager

        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def test_load_populates_shared_cache(self, tmp_path):
        """After first instantiation from disk, _cache must be populated."""
        from infrastructure.config.config_manager import ConfigManager

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"max_concurrent": 6}))

        ConfigManager(cfg_path)

        path_key = str(cfg_path.resolve())
        assert path_key in ConfigManager._cache, "_cache not populated after _load() from disk"
        assert ConfigManager._cache[path_key]["max_concurrent"] == 6

    def test_second_instance_uses_cache(self, tmp_path):
        """A second instance for the same path must read from cache, not disk."""
        from infrastructure.config.config_manager import ConfigManager

        cfg_path = tmp_path / "config.json"
        a = ConfigManager(cfg_path)
        a.set("max_concurrent", 7)

        # Corrupt the disk file so a disk-read would return wrong data.
        cfg_path.write_text(json.dumps({"max_concurrent": 99}))

        b = ConfigManager(cfg_path)
        # Should see the cached value (7), not the disk value (99)
        assert b.max_concurrent == 7, "Second instance read from disk instead of cache"

    def test_old_bug_regression(self, tmp_path):
        """
        Original bug: first instance loaded from disk but didn't write cache.
        A second instance would then also read from disk (no cache hit), missing
        any in-flight set() that hadn't been flushed yet.
        """
        from infrastructure.config.config_manager import ConfigManager

        cfg_path = tmp_path / "config.json"
        a = ConfigManager(cfg_path)
        a.set("theme", "light")

        # Cancel debounced save so data is in _cache but NOT yet on disk
        with a._lock:
            if a._save_timer and a._save_timer.is_alive():
                a._save_timer.cancel()

        b = ConfigManager(cfg_path)
        # b must see theme=light from cache even before disk flush
        assert b.theme == "light", "Second instance missed in-flight set() — BUG-3 regression"


# ─────────────────────────────────────────────────────────────────────────────
# BUG-4 — SettingsTab: cookies_browser OptionMenu missing variable=
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsTabBrowserVar:
    """ui/tabs/settings/network_panel.py :: NetworkPanel — cookies_browser combo"""

    @pytest.fixture(autouse=True)
    def _src(self):
        repo_root = pathlib.Path(__file__).parent.parent
        self.src = (repo_root / "ui" / "tabs" / "settings" / "network_panel.py").read_text(encoding="utf-8")

    def test_browser_var_attribute_exists(self):
        """NetworkPanel must define self._browser_combo."""
        import ast

        tree = ast.parse(self.src)
        assigns = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "_browser_combo" for t in n.targets)
        ]
        assert assigns, "_browser_combo assignment not found in NetworkPanel"

    def test_optionmenu_has_variable_kwarg(self):
        """The cookies_browser QComboBox must be created and populated in source."""
        assert "QComboBox" in self.src, "QComboBox not found in network_panel.py"
        assert "_browser_combo" in self.src, "_browser_combo not found in network_panel.py"

    def test_browser_var_initialised_from_config(self):
        """_browser_combo must be initialised from cfg.cookies_browser."""
        assert "cfg.cookies_browser" in self.src or "cookies_browser" in self.src, (
            "_browser_combo not initialised from cfg.cookies_browser"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TikTok live — "not currently live" retry cap
# ─────────────────────────────────────────────────────────────────────────────


class TestNotLiveRetryCap:
    """DownloadManager must stop after 3 consecutive 'not currently live' errors."""

    def _make_dm(self, max_retries=10):
        import threading
        from unittest.mock import MagicMock

        from infrastructure.downloader.download_manager import DownloadManager

        config = MagicMock()
        config.max_retries = max_retries
        config.max_concurrent = 1

        engine = MagicMock()
        dm = DownloadManager(config=config, engine=engine)
        dm._bus = MagicMock()

        mi = MagicMock()
        mi.is_live = True
        mi.source_engine = "yt_dlp"

        task = MagicMock()
        task.id = "live-task"
        task.url = "https://www.tiktok.com/@user/live"
        task.is_cancellation_requested = False
        task.media_info = mi
        task._lock = threading.Lock()
        task.progress = 0.0
        task.speed = ""
        task.eta = ""
        task.status = None
        task.started_at = None

        return dm, engine, task

    def test_stops_after_3_consecutive(self):
        dm, engine, task = self._make_dm(max_retries=10)
        call_count = 0

        def fake_download(t, **kw):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("channel is not currently live")

        engine.download.side_effect = fake_download
        with patch("infrastructure.downloader.download_manager.time.sleep"):
            dm._run_task(task)

        assert call_count == 3, f"Expected 3 attempts, got {call_count}"

    def test_counter_resets_on_other_error(self):
        """A non-'not-live' error between 'not-live' errors resets the counter."""
        dm, engine, task = self._make_dm(max_retries=10)
        call_count = 0

        def fake_download(t, **kw):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("network timeout")
            raise RuntimeError("channel is not currently live")

        engine.download.side_effect = fake_download
        with patch("infrastructure.downloader.download_manager.time.sleep"):
            dm._run_task(task)

        # alternating not-live / other: counter resets on "other", so we hit
        # max_retries (11 total) before reaching 3 consecutive not-live
        assert call_count == 11, f"Expected 11 attempts (max_retries=10), got {call_count}"
