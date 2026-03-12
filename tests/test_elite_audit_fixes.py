"""
tests/test_elite_audit_fixes.py
Regression tests for the four bugs found by the AUDIT ELITE pass.

ISSUE-1  app/services/thumbnail_service.py
         SSRF via HTTP redirect: requests.get() follows 301/302 by default.
         A CDN URL whose SSRF check passes could redirect to an internal IP.
         Fix: allow_redirects=False.

ISSUE-2  infrastructure/config/config_manager.py
         Non-atomic config save: open("w") truncates before writing.
         A crash mid-write leaves a zero-byte config.json.
         Also: _save() reads self._data without holding self._lock —
         a concurrent set() could produce a partially-updated JSON file.
         Fix: snapshot under lock + write to .tmp, then os.rename.

ISSUE-3  utils/helpers.py
         Windows Popen calls (reveal_in_explorer + open_folder) omit
         close_fds=True.  Python inherits all open file descriptors to
         child processes by default on Windows.  If a download has an
         open yt-dlp file handle, Explorer inherits it, locking the file.
         Fix: close_fds=True on both Windows Popen calls.

ISSUE-4  domain/models/download_task.py
         to_dict() reads twelve fields (filename, status, downloaded_bytes,
         total_bytes, …) without holding self._lock.  A concurrent progress
         hook can write filename and status in separate GIL releases, so
         history could record status=COMPLETED with filename="" — causing
         the History "Open" button to silently do nothing.
         Fix: acquire self._lock for the full dict construction.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

REPO = pathlib.Path(__file__).parent.parent  # repo root, not tests/


# ─────────────────────────────────────────────────────────────────────────────
# ISSUE-1  thumbnail_service.py — SSRF redirect bypass
# ─────────────────────────────────────────────────────────────────────────────

class TestThumbnailSsrfRedirectBypass:
    """requests.get() must set allow_redirects=False to block redirect-based SSRF."""

    def test_allow_redirects_false_in_source(self):
        """
        The requests.get() call in ThumbnailService._fetch must pass
        allow_redirects=False.

        Without this flag, a CDN URL that passes the SSRF IP-check can
        issue a 301 redirect pointing at 169.254.169.254 (AWS metadata),
        192.168.x.x, or any other private address.  The SSRF guard on
        the *original* URL is then irrelevant — the actual HTTP connection
        goes to the redirected target.
        """
        src = (REPO / "app" / "services" / "thumbnail_service.py").read_text()
        idx = src.find("req_lib.get(")
        assert idx != -1, "requests.get() call not found in thumbnail_service.py"
        call_block = src[idx: idx + 120]
        assert "allow_redirects=False" in call_block, (
            "requests.get() must set allow_redirects=False to prevent "
            "redirect-based SSRF bypass (original URL passes check, "
            "redirect target is an internal IP)"
        )

    def test_ssrf_redirect_not_followed(self):
        """
        A mock response with status 301 must NOT result in a second request
        being issued to the redirect target.
        """
        from app.services.thumbnail_service import ThumbnailService

        redirect_response = MagicMock()
        redirect_response.status_code = 301
        redirect_response.raise_for_status.side_effect = Exception("301 redirect")
        redirect_response.headers = {"content-type": "text/html"}

        errors: list[str] = []
        done: list = []

        svc = ThumbnailService()
        with patch("app.services.thumbnail_service._is_safe_thumbnail_url",
                   return_value=True), \
             patch("requests.get", return_value=redirect_response) as mock_get:
            svc._fetch(
                url="https://cdn.example.com/thumb.jpg",
                width=180, height=102,
                on_done=done.append,
                on_error=errors.append,
            )

        # Only one HTTP call should have been made — no follow-up to redirect target.
        assert mock_get.call_count == 1, (
            f"Expected 1 HTTP call, got {mock_get.call_count} — "
            "SSRF redirect is being followed"
        )
        _, kwargs = mock_get.call_args
        assert kwargs.get("allow_redirects") is False, (
            "allow_redirects=False must be passed to requests.get()"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ISSUE-2  config_manager.py — non-atomic save + missing lock
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigManagerAtomicSave:
    """ConfigManager._save must write atomically (tmp → rename) under the lock."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        from infrastructure.config.config_manager import ConfigManager
        ConfigManager._cache.clear()
        yield
        ConfigManager._cache.clear()

    def test_save_uses_tmp_then_rename(self, tmp_path):
        """
        A crash between open("w") and the final flush must not corrupt the
        config file.  The fix writes to a .tmp file and renames atomically.
        """
        from infrastructure.config.config_manager import ConfigManager

        cfg_path = tmp_path / "config.json"
        cfg = ConfigManager(cfg_path)
        cfg.set("theme", "light")

        # Write synchronously (cancels the debounced timer)
        cfg.save()

        # The real config.json must exist and be valid JSON
        assert cfg_path.exists(), "config.json was not created"
        data = json.loads(cfg_path.read_text())
        assert data.get("theme") == "light", "Saved data is wrong"

        # The .tmp file must NOT be left behind after a successful save
        tmp_file = cfg_path.with_suffix(".tmp.json")
        assert not tmp_file.exists(), ".tmp.json left behind after successful save"

    def test_save_reads_data_under_lock(self, tmp_path):
        """
        _save() must snapshot self._data while holding self._lock so that a
        concurrent set() cannot produce a half-updated JSON file on disk.

        Strategy: patch self._lock to record whether it is held when
        self._data is accessed inside _save().
        """
        from infrastructure.config.config_manager import ConfigManager

        cfg_path = tmp_path / "config.json"
        cfg = ConfigManager(cfg_path)

        lock_held_during_data_access: list[bool] = []
        original_save = cfg._save

        orig_lock = cfg._lock
        class _TrackingRLock(threading.RLock().__class__):
            pass

        # Monitor: check if lock is acquired when json.dump runs
        data_accessed_while_locked = []

        orig_json_dump = json.dumps

        def tracking_save():
            # Patch json.dump inside _save to detect lock status
            import infrastructure.config.config_manager as cm_module
            original = cm_module.json.dump

            def _check_dump(obj, f, **kw):
                # Check if our RLock is held by the current thread
                acquired = cfg._lock.acquire(blocking=False)
                if acquired:
                    cfg._lock.release()
                    data_accessed_while_locked.append(False)
                else:
                    # Could not acquire → lock is already held ✓
                    data_accessed_while_locked.append(True)
                return original(obj, f, **kw)

            cm_module.json.dump = _check_dump
            try:
                original_save()
            finally:
                cm_module.json.dump = original

        tracking_save()
        assert data_accessed_while_locked, "_save() never called json.dump"
        assert data_accessed_while_locked[0], (
            "_save() calls json.dump without holding self._lock — "
            "concurrent set() can race and corrupt the written file"
        )

    def test_tmp_file_cleaned_on_oserror(self, tmp_path):
        """
        If the write to .tmp fails mid-way, the .tmp file must not be left
        behind on disk (stale fragment could confuse next startup).
        """
        from infrastructure.config.config_manager import ConfigManager

        cfg_path = tmp_path / "config.json"
        cfg = ConfigManager(cfg_path)
        tmp_file = cfg_path.with_suffix(".tmp.json")

        # Simulate a disk-full error during the write
        with patch("builtins.open", side_effect=[
            # First open() (the .tmp write) raises OSError
            OSError("No space left on device"),
        ]):
            cfg._save()  # must not raise

        # .tmp must be cleaned up (or was never written)
        assert not tmp_file.exists() or tmp_file.stat().st_size == 0, (
            ".tmp.json left behind after a failed write"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ISSUE-3  helpers.py — missing close_fds on Windows Popen
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowsPopenCloseFds:
    """Both Windows Popen calls must set close_fds=True to prevent FD inheritance."""

    def test_reveal_in_explorer_close_fds_windows(self, tmp_path):
        """
        reveal_in_explorer on Windows must pass close_fds=True so that
        open yt-dlp download file handles are not inherited by Explorer,
        which would lock the file and prevent deletion or overwrite.
        """
        from utils.helpers import reveal_in_explorer
        fake = tmp_path / "video.mp4"
        with patch("utils.helpers.sys") as ms, \
             patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = "win32"
            reveal_in_explorer(fake)
            _, kwargs = mp.call_args
            assert kwargs.get("close_fds") is True, (
                "reveal_in_explorer Windows Popen must set close_fds=True to "
                "prevent yt-dlp FDs being inherited by Explorer"
            )

    def test_open_folder_close_fds_windows(self, tmp_path):
        """
        open_folder on Windows must pass close_fds=True for the same reason.
        """
        from utils.helpers import open_folder
        fake = tmp_path / "downloads"
        fake.mkdir()
        with patch("utils.helpers.sys") as ms, \
             patch("utils.helpers.subprocess.Popen") as mp:
            ms.platform = "win32"
            open_folder(fake)
            _, kwargs = mp.call_args
            assert kwargs.get("close_fds") is True, (
                "open_folder Windows Popen must set close_fds=True"
            )

    def test_reveal_in_explorer_close_fds_in_source(self):
        """Source-level check: no Windows Popen call in helpers.py omits close_fds."""
        import re
        src = (REPO / "utils" / "helpers.py").read_text()
        # Find every Popen call and check close_fds presence
        popen_calls = list(re.finditer(r"subprocess\.Popen\([^)]+\)", src, re.DOTALL))
        for m in popen_calls:
            call_text = m.group()
            assert "close_fds=True" in call_text, (
                f"Popen call missing close_fds=True:\n  {call_text[:120]}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# ISSUE-4  download_task.py — to_dict() reads without _lock
# ─────────────────────────────────────────────────────────────────────────────

class TestDownloadTaskToDictLocked:
    """to_dict() must hold self._lock to prevent torn reads across GIL releases."""

    def test_to_dict_acquires_lock(self):
        """
        to_dict() must acquire self._lock before reading multi-field state.

        Without the lock, a history entry can record status=COMPLETED with
        filename="" if the progress hook sets them in separate GIL windows.
        """
        from domain.models.download_task import DownloadTask
        from domain.enums.download_status import DownloadStatus

        task = DownloadTask(url="https://example.com/v")
        task.status = DownloadStatus.COMPLETED
        task.filename = "/tmp/video.mp4"  # nosec B108 - test fixture

        lock_was_held = []

        orig_lock = task._lock

        class _SpyRLock:
            def __enter__(self_):
                orig_lock.acquire()
                lock_was_held.append(True)
                return self_
            def __exit__(self_, *a):
                orig_lock.release()
            def acquire(self_, **kw):
                return orig_lock.acquire(**kw)
            def release(self_):
                return orig_lock.release()

        task._lock = _SpyRLock()
        result = task.to_dict()
        assert lock_was_held, "to_dict() did not acquire self._lock"
        assert result["filename"] == "/tmp/video.mp4"  # nosec B108
        assert result["status"] == "COMPLETED"

    def test_to_dict_sees_consistent_state_under_concurrent_update(self):
        """
        Under concurrent updates to filename + status, to_dict() must never
        return a dict with status=COMPLETED and an empty filename.
        """
        from domain.models.download_task import DownloadTask
        from domain.enums.download_status import DownloadStatus

        task = DownloadTask(url="https://example.com/v")
        task.status = DownloadStatus.DOWNLOADING
        task.filename = ""

        inconsistent: list[dict] = []

        def _updater():
            for _ in range(500):
                with task._lock:
                    task.filename = "/tmp/video.mp4"  # nosec B108
                    task.status = DownloadStatus.COMPLETED
                time.sleep(0)
                with task._lock:
                    task.filename = ""
                    task.status = DownloadStatus.DOWNLOADING
                time.sleep(0)

        def _reader():
            for _ in range(500):
                d = task.to_dict()
                if d["status"] == "COMPLETED" and d["filename"] == "":
                    inconsistent.append(d)
                time.sleep(0)

        t1 = threading.Thread(target=_updater)
        t2 = threading.Thread(target=_reader)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not inconsistent, (
            f"to_dict() produced {len(inconsistent)} inconsistent snapshot(s): "
            "status=COMPLETED with empty filename — _lock not held"
        )
