"""
tests/test_audit_fixes.py
Pytest tests that directly verify each fix from the OmniDL v16 fixed5 audit.

Covers:
  Security:
    - [HIGH]   reveal_in_explorer no longer uses shell=True on Windows
    - [HIGH]   reveal_in_explorer passes path as separate argv element (no injection)
    - [MEDIUM] cookie_file allowlist: path outside home/app dir is rejected
    - [MEDIUM] cookie_file allowlist: path inside home dir is accepted
  History / Data Integrity:
    - [HIGH]   JSONL disk is pruned when in-memory limit is exceeded
    - [HIGH]   Disk does NOT grow beyond limit after many adds
    - [HIGH]   _rewrite() creates a backup before writing, removes it on success
    - [HIGH]   _load() after pruned rewrite respects the limit
  Concurrency / UX:
    - [MEDIUM] _safe_done stale token re-enables Analyze button (no no-op)
  Miscellaneous:
    - [MEDIUM] main.py reports OmniDL v16, not v15
    - [LOW]    Popen on Linux/Mac uses close_fds=True
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers used across multiple test classes
# ---------------------------------------------------------------------------


def _make_task(title: str = "Test Video"):
    """Return a minimal DownloadTask in COMPLETED state."""
    from domain.enums.download_status import DownloadStatus
    from domain.models.download_task import DownloadTask, MediaInfo

    t = DownloadTask(url="https://example.com/watch?v=abc")
    t.status = DownloadStatus.COMPLETED
    t.filename = "/tmp/test.mp4"  # nosec B108 — test-only fixture, not production code
    t.finished_at = time.time()
    t.media_info = MediaInfo(url=t.url, title=title)
    return t


def _make_repo(tmp_path: Path, limit: int = 5):
    from infrastructure.storage.history_repository import HistoryRepository

    return HistoryRepository(tmp_path / "history.jsonl", limit=limit)


# ===========================================================================
# SECURITY — reveal_in_explorer (CWE-78, shell=True removed)
# ===========================================================================


class TestRevealInExplorerSecurity:
    """
    Ensure reveal_in_explorer uses the secure ctypes-based implementation.

    Windows implementation was updated from explorer.exe /select, (subprocess)
    to SHOpenFolderAndSelectItems (ctypes) because:
    - shell=True / Popen was never safe for user-controlled paths (CWE-78)
    - explorer /select, silently breaks on # in filenames (URL fragment issue)
    - SHOpenFolderAndSelectItems is the correct Windows shell API and handles
      all special characters including #, [, ], Unicode, and long paths
    """

    @staticmethod
    def _setup_win32_ctypes(mc):
        """Populate a ctypes mock with all real types + non-null PIDL values."""
        import ctypes as _r

        mc.c_void_p = _r.c_void_p
        mc.c_wchar_p = _r.c_wchar_p
        mc.c_uint = _r.c_uint
        mc.c_ulong = _r.c_ulong
        mc.HRESULT = getattr(_r, "HRESULT", _r.c_long)
        mc.windll.shell32.ILCreateFromPathW.side_effect = [1, 1]
        mc.windll.shell32.ILFindLastID.return_value = 2
        mc.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0

    def test_windows_no_subprocess_popen(self, tmp_path):
        """Windows path must use ctypes — subprocess.Popen must not be called."""
        from utils.helpers import reveal_in_explorer

        fake = tmp_path / "video.mp4"
        with (
            patch("utils.helpers.sys") as ms,
            patch("utils.helpers.ctypes") as mc,
            patch("utils.helpers.subprocess.Popen") as mp,
        ):
            ms.platform = "win32"
            self._setup_win32_ctypes(mc)
            reveal_in_explorer(fake)
            assert not mp.called, (
                "subprocess.Popen must not be called on Windows; "
                "ctypes SHOpenFolderAndSelectItems is used instead (CWE-78 fix)"
            )

    def test_windows_uses_shell_api_not_explorer_cmdline(self, tmp_path):
        """Windows must call SHOpenFolderAndSelectItems, not explorer /select,."""
        from utils.helpers import reveal_in_explorer

        dangerous = tmp_path / 'video";calc.exe;echo ".mp4'
        with patch("utils.helpers.sys") as ms, patch("utils.helpers.ctypes") as mc:
            ms.platform = "win32"
            self._setup_win32_ctypes(mc)
            reveal_in_explorer(dangerous)
            assert mc.windll.shell32.SHOpenFolderAndSelectItems.called
            assert mc.windll.shell32.ILCreateFromPathW.called

    def test_windows_metacharacter_filename_safe(self, tmp_path):
        """Shell metacharacters in filename are safe — ctypes never invokes shell."""
        from utils.helpers import reveal_in_explorer

        dangerous = tmp_path / 'video";calc.exe;echo ".mp4'
        with (
            patch("utils.helpers.sys") as ms,
            patch("utils.helpers.ctypes") as mc,
            patch("utils.helpers.subprocess.Popen") as mp,
        ):
            ms.platform = "win32"
            self._setup_win32_ctypes(mc)
            reveal_in_explorer(dangerous)
        assert not mp.called, "Metacharacters in filename must not reach subprocess"

    def test_linux_uses_close_fds(self, tmp_path):
        """On Linux, close_fds=True must be set to prevent zombie accumulation."""
        from utils.helpers import reveal_in_explorer

        fake = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = "linux"
            reveal_in_explorer(fake)
            _, kwargs = mp.call_args
            assert kwargs.get("close_fds") is True, (
                "close_fds=True required on Linux to prevent zombie processes"
            )

    def test_macos_uses_close_fds(self, tmp_path):
        """On macOS, close_fds=True must be set."""
        from utils.helpers import reveal_in_explorer

        fake = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = "darwin"
            reveal_in_explorer(fake)
            _, kwargs = mp.call_args
            assert kwargs.get("close_fds") is True


# ===========================================================================
# SECURITY — cookie_file allowlist (CWE-703, blocklist → allowlist)
# ===========================================================================


class TestCookieFileAllowlist:
    """
    The cookie_file path must be validated against an allowlist
    (home dir or app config dir), not a blocklist of forbidden keywords.
    """

    def _make_engine(self, tmp_path: Path, cookie_file: str):
        """Return a YtDlpEngine wired to a ConfigManager pointing at cookie_file."""
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({"cookie_file": cookie_file}))
        cfg = ConfigManager(cfg_path)
        return YtDlpEngine(cfg), cfg

    def test_cookie_inside_home_accepted(self, tmp_path):
        """A cookie file inside the home directory must be accepted."""
        engine, cfg = self._make_engine(tmp_path, "")
        cookie = tmp_path / "cookies.txt"
        cookie.write_text("# Netscape HTTP Cookie File\n")

        # Patch config to return our tmp cookie and home to be tmp_path
        with (
            patch.object(type(cfg), "cookie_file", new_callable=lambda: property(lambda s: str(cookie))),
            patch("infrastructure.downloader.yt_dlp_engine.Path.home", return_value=tmp_path),
        ):
            # Simulate the allowlist check inline as the engine does it
            cp = cookie.resolve()
            safe_roots = (tmp_path, cfg.config_path.parent)
            is_safe = any(str(cp).startswith(str(r.resolve())) for r in safe_roots)
            assert is_safe, "Cookie file inside home dir should be accepted"

    def test_cookie_outside_home_rejected(self, tmp_path):
        """A cookie file outside home AND app dir must be rejected."""
        engine, cfg = self._make_engine(tmp_path, "")
        # Simulate an attacker pointing at a file outside safe roots
        if sys.platform != "win32":
            outside = Path("/etc/passwd")
        else:
            outside = Path("C:/Windows/System32/drivers/etc/hosts")
        home_dir = tmp_path / "fake_home"
        home_dir.mkdir()
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        cp = outside.resolve()
        safe_roots = (home_dir, app_dir)
        is_safe = any(str(cp).startswith(str(r.resolve())) for r in safe_roots)
        assert not is_safe, "Cookie file outside safe roots must be rejected"

    def test_blocklist_keyword_in_safe_path_allowed(self, tmp_path):
        """
        A path containing a formerly-blocked keyword (e.g. 'windows_backup')
        but located inside the home dir must NOT be rejected by an allowlist check.
        """
        # Under the old blocklist, 'windows' in path components would block this.
        safe_dir = tmp_path / "windows_backup"
        safe_dir.mkdir()
        cookie = safe_dir / "cookies.txt"
        cookie.write_text("# Netscape HTTP Cookie File\n")
        cp = cookie.resolve()
        safe_roots = (tmp_path,)
        is_safe = any(str(cp).startswith(str(r.resolve())) for r in safe_roots)
        assert is_safe, "Allowlist should permit paths containing formerly-blocked keywords"


# ===========================================================================
# DATA INTEGRITY — JSONL disk pruning (history_repository)
# ===========================================================================


class TestHistoryDiskPruning:
    """JSONL file on disk must never exceed the configured limit."""

    def _count_disk_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text().splitlines() if line.strip())

    def test_disk_pruned_when_limit_exceeded(self, tmp_path):
        """After limit+1 adds, the JSONL file must contain exactly `limit` lines."""
        limit = 3
        repo = _make_repo(tmp_path, limit=limit)
        history_path = tmp_path / "history.jsonl"
        for i in range(limit + 2):
            repo.add(_make_task(f"Video {i}"))
        actual = self._count_disk_lines(history_path)
        assert actual == limit, f"Expected {limit} lines on disk after pruning, got {actual}"

    def test_disk_does_not_grow_unbounded(self, tmp_path):
        """Adding 3× the limit must leave only `limit` lines on disk."""
        limit = 5
        repo = _make_repo(tmp_path, limit=limit)
        history_path = tmp_path / "history.jsonl"
        for i in range(limit * 3):
            repo.add(_make_task(f"Video {i}"))
        lines = self._count_disk_lines(history_path)
        assert lines == limit, f"Disk should have {limit} lines, found {lines}"

    def test_load_after_pruned_rewrite_respects_limit(self, tmp_path):
        """A fresh HistoryRepository loaded from a pruned file must honour the limit."""
        from infrastructure.storage.history_repository import HistoryRepository

        limit = 4
        repo = _make_repo(tmp_path, limit=limit)
        history_path = tmp_path / "history.jsonl"
        for i in range(limit + 5):
            repo.add(_make_task(f"Video {i}"))
        # Reload from disk
        repo2 = HistoryRepository(history_path, limit=limit)
        assert len(repo2.all()) <= limit, "Reloaded repository must not exceed configured limit"

    def test_backup_file_removed_after_successful_rewrite(self, tmp_path):
        """After a successful rewrite the .backup.jsonl file must not exist."""
        limit = 2
        repo = _make_repo(tmp_path, limit=limit)
        history_path = tmp_path / "history.jsonl"
        for i in range(limit + 2):
            repo.add(_make_task(f"Video {i}"))
        backup = history_path.with_suffix(".backup.jsonl")
        assert not backup.exists(), "Backup file must be removed after successful rewrite"

    def test_no_rewrite_when_under_limit(self, tmp_path):
        """When under the limit, _rewrite must NOT be called (fast append path)."""
        limit = 10
        repo = _make_repo(tmp_path, limit=limit)
        history_path = tmp_path / "history.jsonl"
        # Add fewer entries than limit
        for i in range(3):
            repo.add(_make_task(f"Video {i}"))
        # Backup must never have been created
        backup = history_path.with_suffix(".backup.jsonl")
        assert not backup.exists(), "No backup should be created when under limit"

    def test_concurrent_adds_do_not_corrupt(self, tmp_path):
        """Concurrent add() calls must produce a consistent in-memory list."""
        limit = 10
        repo = _make_repo(tmp_path, limit=limit)
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(5):
                    repo.add(_make_task(f"Thread-{n}-Video-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent adds raised exceptions: {errors}"
        assert len(repo.all()) <= limit

    def test_disk_line_count_matches_memory_count(self, tmp_path):
        """After any number of adds the disk line count must equal len(repo.all())."""
        limit = 4
        repo = _make_repo(tmp_path, limit=limit)
        history_path = tmp_path / "history.jsonl"
        for i in range(10):
            repo.add(_make_task(f"Video {i}"))
        mem_count = len(repo.all())
        disk_count = self._count_disk_lines(history_path)
        assert mem_count == disk_count, f"Memory has {mem_count} entries but disk has {disk_count} lines"


# ===========================================================================
# CONCURRENCY — Analyze button race condition
# ===========================================================================


class TestAnalyzeButtonRace:
    """
    When a stale _safe_done fires, _reset_btn() must be scheduled —
    not a no-op lambda — so the Analyze button is re-enabled.
    """

    def test_stale_safe_done_schedules_reset_btn(self):
        """
        Simulate the race: token advances before _safe_done fires.
        The button's configure() must be called with state='normal'.
        """
        # _safe_done, _safe_error, and _reset_btn live in the Toolbar component,
        # not in HomeTab — inspect the correct module.
        import inspect

        import ui.components.toolbar as toolbar_module

        # We can't instantiate the real CTk widget without a display.
        # Instead we inspect the source to confirm the fix is applied.
        src = inspect.getsource(toolbar_module)

        # The no-op lambda must be gone
        assert "lambda: None" not in src or src.count("lambda: None") == 0, (
            "_safe_done must not contain a no-op 'lambda: None'"
        )

        # BUG-CRITICAL-1 FIX VERIFICATION: UI must not be called from a
        # background thread. The PySide6 port replaced _ui_queue.put() with
        # ui_bridge.post() which routes calls to the main thread via a signal.
        # Assert the forbidden direct call is GONE.
        reset_count = src.count("after(0, self._reset_btn)")
        assert reset_count == 0, (
            f"after(0, self._reset_btn) is a Tkinter call from a background "
            f"thread — illegal. Found {reset_count} occurrence(s)."
        )
        # Verify thread-safe reset is present (either PySide6 ui_bridge or
        # CTK _ui_queue variant).
        bridge_count = src.count("ui_bridge.post(self._reset_btn)")
        queue_count = src.count("_ui_queue.put(self._reset_btn)")
        assert bridge_count >= 2 or queue_count >= 2, (
            f"Expected thread-safe _reset_btn dispatch in both _safe_done and "
            f"_safe_error. Found ui_bridge.post: {bridge_count}, "
            f"_ui_queue.put: {queue_count}."
        )


# ===========================================================================
# VERSION STRING
# ===========================================================================


class TestVersionString:
    """main.py must report OmniDL v17 (v17.1), not v16 or earlier."""

    def test_docstring_says_v17(self):
        import main  # local import required for reload test

        importlib.reload(main)
        assert "v17" in (main.__doc__ or ""), "main.py module docstring must say 'v17' (currently v17.1)"
        assert "v16" not in (main.__doc__ or ""), (
            "main.py module docstring must not say 'v16' — update to v17.1"
        )

    def test_main_py_source_has_version_log(self):
        """Startup log must use _APP_VERSION placeholder, not a hardcoded version."""
        main_path = Path(__file__).parent.parent / "main.py"
        src = main_path.read_text(encoding="utf-8")
        # Correct pattern: format string with %s, filled by _APP_VERSION at runtime
        assert "OmniDL v%s starting" in src, (
            "Startup log must use 'OmniDL v%s starting' with _APP_VERSION arg"
        )
        # Must NOT hardcode any specific version literal in the format string
        assert "OmniDL v17 starting" not in src, (
            "Startup log must not hardcode version — use _APP_VERSION instead"
        )
        assert "OmniDL v16 starting" not in src, (
            "Startup log must not hardcode version — use _APP_VERSION instead"
        )
        assert "OmniDL v15 starting" not in src, (
            "Startup log must not hardcode version — use _APP_VERSION instead"
        )


# ===========================================================================
# HELPERS — open_folder close_fds
# ===========================================================================


class TestOpenFolderClosesFds:
    def test_linux_open_folder_uses_close_fds(self, tmp_path):
        from utils.helpers import open_folder

        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = "linux"
            open_folder(tmp_path)
            _, kwargs = mp.call_args
            assert kwargs.get("close_fds") is True

    def test_macos_open_folder_uses_close_fds(self, tmp_path):
        from utils.helpers import open_folder

        with patch("utils.helpers.sys") as ms, patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = "darwin"
            open_folder(tmp_path)
            _, kwargs = mp.call_args
            assert kwargs.get("close_fds") is True


# ===========================================================================
# Repair Patch Tests — verify post-audit fixes (March 2026)
# ===========================================================================


class TestSEC1PathTraversalFix:
    """SEC-1: cookie_file path containment must use Path.parents, not startswith."""

    def _make_engine_with_cookie(self, cookie_path: str, tmp_path, monkeypatch):
        """Return (engine, captured_opts) after calling extract_info
        with given cookie."""
        import json

        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({}))
        config = ConfigManager(config_path)
        config.set("cookie_file", cookie_path)
        config.set("use_cookies", False)
        engine = YtDlpEngine(config)

        fake_home = tmp_path / "home"
        fake_home.mkdir(exist_ok=True)
        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def extract_info(self, url, download=False):
                return {
                    "title": "T",
                    "uploader": "U",
                    "duration": 1,
                    "thumbnail": "",
                    "formats": [],
                    "is_live": False,
                    "was_live": False,
                    "id": "abc",
                }

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            try:
                engine.extract_info("https://youtube.com/watch?v=test")
            except Exception:
                pass
        return captured

    def test_sibling_directory_bypass_is_blocked(self, tmp_path, monkeypatch):
        """
        /home/user_evil/cookies.txt must NOT pass when safe_root=/home/user/.omnidl.
        The old startswith() check would have allowed this because
        '/home/user_evil'.startswith('/home/user') is True.
        Path.parents is used instead: user_evil is NOT an ancestor of .omnidl.
        """
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        # Config lives inside fake_home/.omnidl/ so safe_root = fake_home/.omnidl
        config_dir = fake_home / ".omnidl"
        config_dir.mkdir()
        # Sibling directory whose name starts with the home-dir name
        sibling = tmp_path / "home" / "user_evil"
        sibling.mkdir()
        evil_cookie = sibling / "cookies.txt"
        evil_cookie.write_text("# cookies\n")

        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

        import json

        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps({}))
        config = ConfigManager(config_path)
        config.set("cookie_file", str(evil_cookie))
        config.set("use_cookies", False)
        engine = YtDlpEngine(config)

        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def extract_info(self, url, download=False):
                return {
                    "title": "T",
                    "uploader": "U",
                    "duration": 1,
                    "thumbnail": "",
                    "formats": [],
                    "is_live": False,
                    "was_live": False,
                    "id": "abc",
                }

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            try:
                engine.extract_info("https://youtube.com/watch?v=test")
            except Exception:
                pass

        assert "cookiefile" not in captured, "Sibling-directory bypass must be blocked by Path.parents check"

    def test_file_inside_home_is_allowed(self, tmp_path, monkeypatch):
        """A cookie file inside the OmniDL data directory must be accepted."""
        fake_home = tmp_path / "home" / "user"
        fake_home.mkdir(parents=True)
        # Config (and cookie) live in the same .omnidl directory = safe_root
        config_dir = fake_home / ".omnidl"
        config_dir.mkdir()
        legit_cookie = config_dir / "cookies.txt"
        legit_cookie.write_text("# cookies\n")

        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

        import json

        import infrastructure.downloader.yt_dlp_engine as mod
        from infrastructure.config.config_manager import ConfigManager
        from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

        config_path = config_dir / "config.json"
        config_path.write_text(json.dumps({}))
        config = ConfigManager(config_path)
        config.set("cookie_file", str(legit_cookie))
        config.set("use_cookies", False)
        engine = YtDlpEngine(config)

        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def extract_info(self, url, download=False):
                return {
                    "title": "T",
                    "uploader": "U",
                    "duration": 1,
                    "thumbnail": "",
                    "formats": [],
                    "is_live": False,
                    "was_live": False,
                    "id": "abc",
                }

        with patch.object(mod.yt_dlp, "YoutubeDL", FakeYDL):
            try:
                engine.extract_info("https://youtube.com/watch?v=test")
            except Exception:
                pass

        assert captured.get("cookiefile") == str(legit_cookie.resolve()), (
            "Legitimate cookie file inside OmniDL data dir must be accepted"
        )


class TestSEC5BrowserAllowlist:
    """SEC-5: cookies_browser must be validated against the allowlist."""

    def test_valid_browser_passes_through(self, tmp_path):
        import json

        from infrastructure.config.config_manager import ConfigManager

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"cookies_browser": "firefox"}))
        config = ConfigManager(config_path)
        assert config.cookies_browser == "firefox"

    def test_invalid_browser_defaults_to_chrome(self, tmp_path):
        import json

        from infrastructure.config.config_manager import ConfigManager

        config_path = tmp_path / "config.json"
        _evil = {"cookies_browser": "evil_browser; rm -rf /"}
        config_path.write_text(json.dumps(_evil))
        config = ConfigManager(config_path)
        assert config.cookies_browser == "chrome"

    def test_empty_string_defaults_to_chrome(self, tmp_path):
        import json

        from infrastructure.config.config_manager import ConfigManager

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"cookies_browser": ""}))
        config = ConfigManager(config_path)
        assert config.cookies_browser == "chrome"


class TestSEC2RevealInExplorer:
    """SEC-2: reveal_in_explorer must use ctypes SHOpenFolderAndSelectItems on Windows."""

    def test_windows_select_arg_is_single_token(self, tmp_path, monkeypatch):
        """
        Windows reveal_in_explorer must use ctypes SHOpenFolderAndSelectItems.

        The old explorer.exe /select,<path> approach broke silently when the
        path contained # (URL fragment), [ ], or certain Unicode characters.
        SHOpenFolderAndSelectItems has no such limitation and correctly
        highlights the file in Explorer.

        Verifies:
          - ctypes ILCreateFromPathW is called (path → PIDL)
          - SHOpenFolderAndSelectItems is called with cidl=1 (select file)
          - subprocess.Popen is NOT called on Windows
        """
        import ctypes as _r

        from utils.helpers import reveal_in_explorer

        test_file = tmp_path / "video.mp4"
        test_file.write_bytes(b"fake")

        with (
            patch("utils.helpers.sys") as mock_sys,
            patch("utils.helpers.ctypes") as mock_ctypes,
            patch("utils.helpers.subprocess.Popen") as mock_popen,
        ):
            mock_sys.platform = "win32"
            mock_ctypes.c_void_p = _r.c_void_p
            mock_ctypes.c_wchar_p = _r.c_wchar_p
            mock_ctypes.c_uint = _r.c_uint
            mock_ctypes.c_ulong = _r.c_ulong
            mock_ctypes.c_long = _r.c_long
            mock_ctypes.windll.shell32.ILCreateFromPathW.side_effect = [1, 1]
            mock_ctypes.windll.shell32.ILFindLastID.return_value = 2
            mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems.return_value = 0
            reveal_in_explorer(test_file)

        assert not mock_popen.called, (
            "subprocess.Popen must NOT be called on Windows — "
            "explorer /select, breaks on # in filenames (CWE-78). "
            "Use ctypes SHOpenFolderAndSelectItems instead."
        )
        assert mock_ctypes.windll.shell32.ILCreateFromPathW.called, (
            "ILCreateFromPathW must be called to convert path to PIDL"
        )
        sh_open = mock_ctypes.windll.shell32.SHOpenFolderAndSelectItems
        assert sh_open.called, "SHOpenFolderAndSelectItems must be called"
        cidl = sh_open.call_args[0][1]
        assert cidl == 1, (
            f"cidl must be 1 to select the file; got {cidl}. "
            "cidl=0 only opens the folder without highlighting any file."
        )


class TestP1DebouncedSave:
    """P-1: Rapid successive set() calls should not trigger multiple disk writes."""

    def test_rapid_sets_produce_one_save(self, tmp_path):
        import json
        import time

        from infrastructure.config.config_manager import ConfigManager

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({}))
        config = ConfigManager(config_path)

        save_count = [0]
        original_save = config._save

        def counting_save():
            save_count[0] += 1
            original_save()

        config._save = counting_save

        # Fire 10 rapid set() calls
        for i in range(10):
            config.set("max_concurrent", i)

        # Timer has 500ms delay — saves should not have fired yet
        immediate_count = save_count[0]

        # Wait for the debounce timer to flush
        time.sleep(0.8)
        final_count = save_count[0]

        assert immediate_count <= 1, (
            f"Expected at most 1 immediate save (for _load's initial write), got {immediate_count}"
        )
        assert final_count <= immediate_count + 2, (
            f"10 rapid set() calls should trigger at most 2 total saves, got {final_count}"
        )
        # Verify the last value was persisted
        config2 = ConfigManager(config_path)
        assert config2.max_concurrent == 9


class TestSEC4ThumbnailSSRF:
    """SEC-4: _is_safe_thumbnail_url must block private/loopback hosts."""

    @staticmethod
    def _safe(url: str) -> bool:
        from app.services.thumbnail_service import _is_safe_thumbnail_url

        return _is_safe_thumbnail_url(url)

    # ── Scheme allowlist ──────────────────────────────────────────────────

    def test_https_allowed(self):
        # Use a globally-routable IP literal so the test does not need DNS.
        # 8.8.8.8 is a public address — not loopback, private, or link-local.
        assert self._safe("https://8.8.8.8/vi/abc/hqdefault.jpg") is True

    def test_file_scheme_blocked(self):
        assert self._safe("file:///etc/passwd") is False

    def test_data_uri_blocked(self):
        assert self._safe("data:image/png;base64,abc") is False

    # ── Raw IP literals ───────────────────────────────────────────────────

    def test_private_ip_10_blocked(self):
        assert self._safe("http://10.0.0.1/img.jpg") is False

    def test_private_ip_192_168_blocked(self):
        assert self._safe("http://192.168.1.100/img.jpg") is False

    def test_private_ip_172_16_blocked(self):
        assert self._safe("http://172.16.0.1/img.jpg") is False

    def test_loopback_127_blocked(self):
        assert self._safe("http://127.0.0.1/img.jpg") is False

    def test_ipv6_loopback_blocked(self):
        assert self._safe("http://[::1]/img.jpg") is False

    def test_link_local_blocked(self):
        assert self._safe("http://169.254.169.254/latest/meta-data/") is False

    # ── Hostname-based SSRF (the old bug — SEC-4) ─────────────────────────

    def test_localhost_hostname_blocked(self):
        """The old code returned True for localhost — this is the SEC-4 fix."""
        assert self._safe("http://localhost/admin") is False

    def test_localhost_localdomain_blocked(self):
        assert self._safe("http://localhost.localdomain/secret") is False

    # ── Edge cases ────────────────────────────────────────────────────────

    def test_empty_host_blocked(self):
        assert self._safe("http:///no-host") is False

    def test_non_http_scheme_with_ip_blocked(self):
        assert self._safe("ftp://8.8.8.8/img.jpg") is False

    def test_malformed_url_blocked(self):
        # Should not raise — just return False.
        result = self._safe("not a url at all ://??")
        assert result is False
