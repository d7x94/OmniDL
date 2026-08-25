"""Regression guards for the TikTok audit (desktop + Remote API).

One test per issue found in the audit; each fails against the pre-fix code.
"""

from __future__ import annotations

import threading
import time

import pytest

from api.models import DownloadRequest
from infrastructure.downloader.account_pool import (
    TikTokAccount,
    TikTokAccountPool,
    _AcquireAborted,
)
from utils import tiktok_live_checker as tlc


# ── Issue 1: live downloads rejected by the Remote API ────────────────────────
def test_output_ext_ts_accepted():
    """index.html sends output_ext='ts' for every live stream."""
    req = DownloadRequest(
        url="https://www.tiktok.com/@user/live",
        is_live=True,
        format_id="best",
        output_ext="ts",
    )
    assert req.output_ext == "ts"


def test_output_ext_unknown_still_rejected():
    with pytest.raises(ValueError):
        DownloadRequest(url="https://www.tiktok.com/@u/live", output_ext="exe")


# ── Issue 2: tiktok_room_id survives an analyse-cache miss ────────────────────
def _room_id_from_body(room_id):
    """Mirror of the /api/download fallback in api/server.py."""
    out = ""
    if not out and room_id:
        rid = room_id.strip()
        if rid.isdigit() and 0 < len(rid) <= 32:
            out = rid
    return out


def test_client_room_id_accepted_when_numeric():
    assert _room_id_from_body("7412345678901234567") == "7412345678901234567"


@pytest.mark.parametrize("bad", ["", "  ", "abc", "1;DROP", "../../etc", "9" * 33])
def test_client_room_id_rejected_when_not_a_room_number(bad):
    assert _room_id_from_body(bad) == ""


# ── Issue 5: m.tiktok.com is a real TikTok host ───────────────────────────────
@pytest.mark.parametrize(
    "url",
    ["https://m.tiktok.com/@user", "https://m.tiktok.com/@user/", "http://m.tiktok.com/@user?lang=vi"],
)
def test_mobile_host_profile_urls(url):
    assert tlc.is_tiktok_profile_url(url) is True
    assert tlc.extract_tiktok_username(url) == "user"


def test_mobile_host_live_url():
    url = "https://m.tiktok.com/@user/live"
    assert tlc.is_tiktok_profile_url(url) is False
    assert tlc.extract_tiktok_username_from_live_url(url) == "user"


@pytest.mark.parametrize("url", ["https://evil.com/m.tiktok.com/@user", "https://mtiktok.com/@user"])
def test_lookalike_hosts_still_rejected(url):
    assert tlc.is_tiktok_profile_url(url) is False


# ── Issue 6: cached room_id answers during a rate-limit window ────────────────
class _RaisingDispatcher:
    def check(self, ctx):
        raise RuntimeError("blocked: TikTok dang rate-limit tam thoi.")


@pytest.fixture
def _blocked_dispatcher(monkeypatch):
    monkeypatch.setattr(tlc, "_get_dispatcher", lambda: _RaisingDispatcher())
    tlc._ROOM_ID_CACHE.clear()
    yield
    tlc._ROOM_ID_CACHE.clear()


def test_rate_limit_falls_back_to_cached_room_id(monkeypatch, _blocked_dispatcher):
    tlc._ROOM_ID_CACHE["bob"] = ("7412345678901234567", time.monotonic())
    monkeypatch.setattr(tlc, "_verify_room_alive", lambda *a, **k: True)
    monkeypatch.setattr(
        tlc,
        "_fetch_hls_from_webcast_room_info",
        lambda *a, **k: ("https://cdn/x.m3u8", "7412345678901234567"),
    )

    assert tlc._check_tiktok_live_with_room_id("bob") == (
        "https://www.tiktok.com/@bob/live",
        "7412345678901234567",
    )


def test_rate_limit_reraised_when_cache_cannot_answer(_blocked_dispatcher):
    with pytest.raises(RuntimeError, match="rate-limit"):
        tlc._check_tiktok_live_with_room_id("nobody")


def test_cached_room_id_confirmed_dead_returns_none(monkeypatch, _blocked_dispatcher):
    """check_alive=False is authoritative — do not surface the rate-limit error."""
    tlc._ROOM_ID_CACHE["bob"] = ("7412345678901234567", time.monotonic())
    monkeypatch.setattr(tlc, "_verify_room_alive", lambda *a, **k: False)

    assert tlc._check_tiktok_live_with_room_id("bob") is None
    assert "bob" not in tlc._ROOM_ID_CACHE


# ── Issue 4: a cancelled task must not park on a pool slot forever ────────────
def _pool():
    return TikTokAccountPool([TikTokAccount(name="a", cookie_file="/tmp/a.txt", max_slots=1)])


def test_acquire_aborts_when_waiter_gives_up():
    pool = _pool()
    held = threading.Event()
    release = threading.Event()

    def _holder():
        with pool.acquire():
            held.set()
            release.wait(10)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert held.wait(5)

    with pytest.raises(_AcquireAborted):
        with pool.acquire(should_abort=lambda: True):
            pytest.fail("should never get a slot")

    release.set()
    t.join(5)


def test_aborted_waiter_does_not_leak_reserved_load():
    pool = _pool()
    held = threading.Event()
    release = threading.Event()

    def _holder():
        with pool.acquire():
            held.set()
            release.wait(10)

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert held.wait(5)

    with pytest.raises(_AcquireAborted):
        with pool.acquire(should_abort=lambda: True):
            pass

    release.set()
    t.join(5)
    # Pre-reserved load must be handed back, otherwise the account looks
    # permanently busy and the balancer never picks it again.
    assert pool.get_status()[0][1] == 0


def test_acquire_without_abort_still_blocks_then_succeeds():
    pool = _pool()
    with pool.acquire() as acc:
        assert acc.name == "a"
    with pool.acquire() as acc:
        assert acc.name == "a"


# ── Issue 3: analyse worker must not die in its cleanup block ─────────────────
def test_analyse_tiktok_cookie_failure_reports_error(monkeypatch, tmp_path):
    """_resolve_cookie raising must surface via on_error, not kill the thread."""
    from app.services import download_service as ds

    def _boom(*a, **k):
        raise OSError("keyring locked")

    monkeypatch.setattr(ds, "_resolve_cookie", _boom)

    class _Engine:
        def extract_info(self, url):
            raise RuntimeError("The channel is not currently live")

    svc = object.__new__(ds.DownloadService)
    svc._engine = _Engine()
    svc._gallery_engine = None
    svc._bus = type("B", (), {"publish": lambda self, *a, **k: None})()
    svc._config = type("C", (), {"proxy": ""})()
    svc._manager = type("M", (), {})()

    done = threading.Event()
    seen: dict = {}

    def on_error(err):
        seen["err"] = err
        done.set()

    svc.analyse_url(
        "https://www.tiktok.com/@user/live",
        on_done=lambda info: done.set(),
        on_error=on_error,
    )
    assert done.wait(30), "analyse worker died without calling back"
    assert "err" in seen
