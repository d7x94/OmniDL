"""Regression guards for the cookie / TikTok-account-pool audit (Aug 2026).

Each class maps to one issue found in the audit:
  #1 all-paused pool hard-failed every TikTok download
  #2 extracted cookie jars leaked on rejection / cancel
  #3 health check missed the .txt -> .enc rename
  #4 30-day cleanup deleted cookie files the config still referenced
  #5 pool rebuild reset live slot counters
  #6 header-only jar reported "unreadable" instead of "not_logged_in"
  #7 plaintext cookie cache was unbounded and never invalidated
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from infrastructure.downloader import cookie_storage
from infrastructure.downloader.account_pool import (
    TikTokAccount,
    TikTokAccountPool,
    inspect_tiktok_cookie,
)

_HEADER = "# Netscape HTTP Cookie File\n"


def _write_jar(path: Path, *, session: str = "abc123", expires: int | None = None) -> Path:
    exp = int(time.time()) + 86400 * 30 if expires is None else expires
    body = _HEADER
    if session:
        body += f".tiktok.com\tTRUE\t/\tTRUE\t{exp}\tsessionid\t{session}\n"
    path.write_text(body, encoding="utf-8")
    return path


# ── #1 all-paused pool ──────────────────────────────────────────────────────


class TestUsableAccountGate:
    def test_paused_accounts_do_not_count_as_usable(self, tmp_path):
        jar = _write_jar(tmp_path / "a.txt")
        pool = TikTokAccountPool([TikTokAccount(name="A", cookie_file=str(jar), enabled=False)])
        assert len(pool) == 1
        assert pool.has_usable_account() is False

    def test_account_without_cookie_is_not_usable(self):
        pool = TikTokAccountPool([TikTokAccount(name="A", cookie_file="")])
        assert pool.has_usable_account() is False

    def test_enabled_account_with_cookie_is_usable(self, tmp_path):
        jar = _write_jar(tmp_path / "a.txt")
        pool = TikTokAccountPool([TikTokAccount(name="A", cookie_file=str(jar))])
        assert pool.has_usable_account() is True

    def test_manager_falls_back_when_pool_becomes_unusable(self, tmp_path):
        from infrastructure.downloader.download_manager import DownloadManager

        mgr = DownloadManager.__new__(DownloadManager)
        mgr._platform_sems = {"tiktok": threading.Semaphore(2)}
        ran: list[str] = []
        mgr._gated_run = lambda task, sem: ran.append("fallback")
        mgr._run_task = lambda task: ran.append("pool")

        pool = TikTokAccountPool([TikTokAccount(name="A", cookie_file="", enabled=False)])
        task = MagicMock(id="t1", is_cancellation_requested=False)

        DownloadManager._gated_run_tiktok(mgr, task, pool)
        assert ran == ["fallback"]


# ── #3 / #6 health classification ───────────────────────────────────────────


class TestCookieHealth:
    def test_txt_in_config_resolves_to_enc_on_disk(self, tmp_path, monkeypatch):
        enc = tmp_path / "a.enc"
        enc.write_bytes(b"ciphertext")
        monkeypatch.setattr(
            "infrastructure.downloader.account_pool._read_cookie_text",
            lambda p: _HEADER + f".tiktok.com\tTRUE\t/\tTRUE\t{int(time.time()) + 9999}\tsessionid\tzz\n",
        )
        health = inspect_tiktok_cookie(str(tmp_path / "a.txt"))
        assert health.status == "ok"

    def test_still_missing_when_neither_spelling_exists(self, tmp_path):
        assert inspect_tiktok_cookie(str(tmp_path / "nope.txt")).status == "missing"

    def test_header_only_jar_is_not_logged_in(self, tmp_path):
        jar = _write_jar(tmp_path / "empty.txt", session="")
        assert inspect_tiktok_cookie(str(jar)).status == "not_logged_in"

    def test_garbage_file_is_unreadable(self, tmp_path):
        jar = tmp_path / "junk.txt"
        jar.write_text("not a cookie file at all\n", encoding="utf-8")
        assert inspect_tiktok_cookie(str(jar)).status == "unreadable"

    def test_expired_session_still_detected(self, tmp_path):
        jar = _write_jar(tmp_path / "old.txt", expires=int(time.time()) - 10)
        assert inspect_tiktok_cookie(str(jar)).status == "expired"


# ── #4 stale cleanup must not delete in-use jars ────────────────────────────


class TestStaleCleanupKeepsInUseFiles:
    def _age(self, path: Path, days: int) -> None:
        old = time.time() - days * 86400
        import os

        os.utime(path, (old, old))

    def test_in_use_file_survives(self, tmp_path):
        keep = _write_jar(tmp_path / "keep.txt")
        drop = _write_jar(tmp_path / "drop.txt")
        self._age(keep, 90)
        self._age(drop, 90)

        deleted = cookie_storage.cleanup_stale_cookies(tmp_path, max_age_days=30, keep={str(keep)})
        assert deleted == 1
        assert keep.is_file()
        assert not drop.is_file()

    def test_no_keep_set_behaves_as_before(self, tmp_path):
        drop = _write_jar(tmp_path / "drop.txt")
        self._age(drop, 90)
        assert cookie_storage.cleanup_stale_cookies(tmp_path, max_age_days=30) == 1
        assert not drop.is_file()

    def test_main_collects_both_spellings(self, tmp_path):
        import main as main_mod

        cfg = MagicMock()
        cfg.cookie_file = str(tmp_path / "global.enc")
        cfg.platform_cookies = {"tiktok": str(tmp_path / "tt.txt")}
        cfg.tiktok_account_pool = [{"cookie_file": str(tmp_path / "pool.enc")}, {}]

        paths = main_mod._cookie_paths_in_use(cfg)
        for name in ("global.enc", "global.txt", "tt.txt", "tt.enc", "pool.enc", "pool.txt"):
            assert str(tmp_path / name) in paths


# ── #5 pool rebuild keeps live slot counters ────────────────────────────────


class TestPoolRebuildKeepsState:
    def test_load_and_slot_carry_over(self, tmp_path):
        jar = _write_jar(tmp_path / "a.txt")
        acc = TikTokAccount(id="fixed", name="A", cookie_file=str(jar), max_slots=1)
        old = TikTokAccountPool([acc])

        started = threading.Event()
        release = threading.Event()

        def _hold():
            with old.acquire():
                started.set()
                release.wait(5)

        worker = threading.Thread(target=_hold, daemon=True)
        worker.start()
        assert started.wait(5)

        new = TikTokAccountPool([TikTokAccount(id="fixed", name="A", cookie_file=str(jar), max_slots=1)])
        new.adopt_state(old)

        # The in-flight download is still counted, and its slot is the same
        # object — so the rebuilt pool cannot hand the account out twice.
        assert new.get_status()[0][1] == 1
        assert new._slots["fixed"] is old._slots["fixed"]

        release.set()
        worker.join(5)

    def test_unknown_account_is_left_untouched(self, tmp_path):
        jar = _write_jar(tmp_path / "a.txt")
        old = TikTokAccountPool([TikTokAccount(id="gone", name="G", cookie_file=str(jar))])
        new = TikTokAccountPool([TikTokAccount(id="fresh", name="F", cookie_file=str(jar))])
        new.adopt_state(old)
        assert new.get_status()[0][1] == 0

    def test_manager_rebuild_preserves_load(self, tmp_path):
        from infrastructure.downloader.download_manager import DownloadManager

        jar = _write_jar(tmp_path / "a.txt")
        entry = {"id": "fixed", "name": "A", "cookie_file": str(jar), "max_slots": 2, "enabled": True}
        mgr = DownloadManager.__new__(DownloadManager)
        mgr._lock = threading.Lock()
        mgr._config = MagicMock(tiktok_account_pool=[entry])
        mgr._tiktok_pool = mgr._build_tiktok_pool()
        mgr._tiktok_pool._load["fixed"] = 2

        mgr.rebuild_tiktok_pool()
        assert mgr._tiktok_pool.get_status()[0][1] == 2


# ── #7 bounded, invalidatable plaintext cache ───────────────────────────────


class TestCookieCache:
    def test_cache_is_bounded(self):
        cookie_storage._cookie_cache.clear()
        for i in range(cookie_storage._COOKIE_CACHE_MAX + 5):
            with cookie_storage._cookie_cache_lock:
                while len(cookie_storage._cookie_cache) >= cookie_storage._COOKIE_CACHE_MAX:
                    cookie_storage._cookie_cache.pop(next(iter(cookie_storage._cookie_cache)))
                cookie_storage._cookie_cache[f"p{i}"] = (b"x", 0.0)
        assert len(cookie_storage._cookie_cache) <= cookie_storage._COOKIE_CACHE_MAX

    def test_invalidate_drops_plaintext(self):
        cookie_storage._cookie_cache["/tmp/x.enc"] = (b"secret", 1.0)
        cookie_storage.invalidate_cookie_cache("/tmp/x.enc")
        assert "/tmp/x.enc" not in cookie_storage._cookie_cache

    def test_invalidate_unknown_path_is_a_noop(self):
        cookie_storage.invalidate_cookie_cache("/tmp/never-cached.enc")


# ── #2 add-form jar lifecycle ───────────────────────────────────────────────


class _FormStub:
    """Only the pieces of NetworkPanel that the add-form lifecycle touches."""

    def __init__(self, tmp_path: Path):
        self.deleted: list[str] = []
        self._tt_add_pending_cookie = ""
        self._tt_add_pending_fp = ""
        self._tt_form_gen = 0
        self._tmp = tmp_path
        self._tt_add_pending_browser = ""
        self._tt_add_pending_profile = ""
        self.status: list[str] = []

    def _set_add_form_status(self, text: str, color: str) -> None:
        self.status.append(text)

    def _delete_pool_cookie_file(self, cookie_file: str) -> None:
        if cookie_file:
            self.deleted.append(cookie_file)
            Path(cookie_file).unlink(missing_ok=True)


@pytest.fixture
def panel_cls():
    pytest.importorskip("PySide6.QtWidgets")
    from ui.tabs.settings.network_panel import NetworkPanel

    return NetworkPanel


class TestAddFormCookieLifecycle:
    def test_rejected_jar_is_deleted(self, tmp_path, panel_cls):
        jar = _write_jar(tmp_path / "reject.txt", session="")  # not logged in
        stub = _FormStub(tmp_path)
        stub._tt_add_save_btn = MagicMock()
        stub._duplicate_account_name = lambda fp, exclude_id="": ""

        panel_cls._accept_cookie_for_form(stub, str(jar), "brave", "Default")

        assert stub.deleted == [str(jar)]
        assert not jar.is_file()
        assert stub._tt_add_pending_cookie == ""

    def test_duplicate_jar_is_deleted(self, tmp_path, panel_cls):
        jar = _write_jar(tmp_path / "dup.txt")
        stub = _FormStub(tmp_path)
        stub._tt_add_save_btn = MagicMock()
        stub._duplicate_account_name = lambda fp, exclude_id="": "Existing"

        panel_cls._accept_cookie_for_form(stub, str(jar), "brave", "Default")

        assert stub.deleted == [str(jar)]
        assert not jar.is_file()

    def test_superseded_jar_is_deleted(self, tmp_path, panel_cls):
        first = _write_jar(tmp_path / "first.txt")
        second = _write_jar(tmp_path / "second.txt", session="other")
        stub = _FormStub(tmp_path)
        stub._tt_add_pending_cookie = str(first)
        stub._tt_add_save_btn = MagicMock()
        stub._tt_add_name = MagicMock()
        stub._tt_add_name.text.return_value = "Name"
        stub._duplicate_account_name = lambda fp, exclude_id="": ""

        panel_cls._accept_cookie_for_form(stub, str(second), "brave", "Default")

        assert str(first) in stub.deleted
        assert not first.is_file()
        assert stub._tt_add_pending_cookie == str(second)
        assert second.is_file()

    def test_cancelling_the_form_deletes_the_pending_jar(self, tmp_path, panel_cls):
        jar = _write_jar(tmp_path / "pending.txt")
        stub = _FormStub(tmp_path)
        stub._tt_add_pending_cookie = str(jar)
        stub._tt_add_form = MagicMock()
        stub._tt_add_btn = MagicMock()

        panel_cls._hide_tiktok_add_form(stub)

        assert stub.deleted == [str(jar)]
        assert not jar.is_file()
        assert stub._tt_add_pending_cookie == ""

    def test_hiding_after_save_keeps_the_jar(self, tmp_path, panel_cls):
        jar = _write_jar(tmp_path / "saved.txt")
        stub = _FormStub(tmp_path)
        stub._tt_add_pending_cookie = ""  # ownership handed to the saved account
        stub._tt_add_form = MagicMock()
        stub._tt_add_btn = MagicMock()

        panel_cls._hide_tiktok_add_form(stub)

        assert stub.deleted == []
        assert jar.is_file()
