"""
tests/test_log_audit_300826.py

Regression guards for the defects found auditing omnidl_debug.log
lines 6163-27857 (2026-08-30 00:00 → 2026-08-31 09:32).

AUDIT-01  An ended TikTok room re-detected forever.
          webcast/room/info answered status=4 (ended) 641 times for
          @tomluoc211 room 7679856087437429522, yet the live page kept
          serving that same stale roomId in SIGI_STATE and check_alive kept
          answering alive=True, so pass-2 logged "LIVE" 246 times over
          9.5 hours for a stream that had already finished.

AUDIT-02  DELETE /api/convert/{job_id}/file answered 500 on a Windows file
          lock.  /api/queue/{task_id}/file already mapped OSError to 409
          (see test_api_file_lock_409.py); the convert route was missed.

AUDIT-03  yt-dlp's own ERROR lines for *recoverable* retry-ladder attempts
          reached the log at ERROR level, and carried raw ANSI colour
          escapes.  39 ERROR records in a window where 41/41 downloads
          completed.

AUDIT-04  Pass-0's 10013 cooldown was keyed per username and expired after
          120 s while polls run every ~70 s, so a globally broken endpoint
          was re-probed 1,395 times with 0 successes.

AUDIT-05  The health daemon counted "strategy returned None" as a probe
          success, so a strategy that never worked once was never disabled.

AUDIT-06  Windows exit codes were rendered unsigned: "ffmpeg thoát với lỗi
          4294967256" instead of -40.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

# ── AUDIT-01: ended-room negative cache ──────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_ended_rooms():
    from utils import tiktok_live_checker as tlc

    tlc._ENDED_ROOM_IDS.clear()
    yield
    tlc._ENDED_ROOM_IDS.clear()


def test_room_marked_ended_is_remembered():
    from utils import tiktok_live_checker as tlc

    assert tlc._room_recently_ended("7679856087437429522") is False
    tlc._mark_room_ended("7679856087437429522")
    assert tlc._room_recently_ended("7679856087437429522") is True


def test_ended_mark_expires_after_ttl(monkeypatch):
    from utils import tiktok_live_checker as tlc

    monkeypatch.setattr(tlc, "_ENDED_ROOM_TTL", 0.0)
    tlc._mark_room_ended("123")
    assert tlc._room_recently_ended("123") is False


def test_room_info_status_4_marks_room_ended(monkeypatch):
    """The 641 'BUG-TT-25: room/info status=4 (ended)' lines must leave a mark."""
    from utils import tiktok_live_checker as tlc

    monkeypatch.setattr(
        tlc,
        "_get_impersonate_session",
        lambda jar: _FakeSession('{"data": {"status": 4}}'),
    )
    assert tlc._fetch_hls_from_webcast_room_info("7679856087437429522", "tomluoc211") is None
    assert tlc._room_recently_ended("7679856087437429522") is True


def test_room_info_status_2_clears_ended_mark(monkeypatch):
    """A room id can be reused by a restarted broadcast — status=2 must un-mark it."""
    from utils import tiktok_live_checker as tlc

    tlc._mark_room_ended("7679856087437429522")
    body = '{"data": {"status": 2, "stream_url": {"hls_pull_url": "https://pull.tiktokcdn.com/live/x.m3u8"}}}'
    monkeypatch.setattr(tlc, "_get_impersonate_session", lambda jar: _FakeSession(body))
    result = tlc._fetch_hls_from_webcast_room_info("7679856087437429522", "tomluoc211")
    assert result is not None
    assert tlc._room_recently_ended("7679856087437429522") is False


def test_pass2_skips_ended_room_without_logging_live(monkeypatch, caplog):
    """Pass-2 must not announce LIVE for a roomId room/info already called ended."""
    from utils import tiktok_live_checker as tlc
    from utils.tiktok_detection.context import LiveCheckContext
    from utils.tiktok_detection.strategies.pass2_live_page import Pass2LivePage

    tlc._mark_room_ended("7679856087437429522")
    monkeypatch.setattr(tlc, "_fetch_tiktok_live_page", lambda *a, **k: "<html/>")
    monkeypatch.setattr(tlc, "_room_id_from_live_page", lambda *a, **k: "7679856087437429522")

    def _boom(*a, **k):
        raise AssertionError("check_alive must not be called for a known-ended room")

    monkeypatch.setattr(tlc, "_verify_room_alive", _boom)

    with caplog.at_level(logging.INFO):
        assert Pass2LivePage().check(LiveCheckContext(username="tomluoc211")) is None
    assert "LIVE via pass-2" not in caplog.text


def test_pass1_skips_ended_room(monkeypatch):
    from utils import tiktok_live_checker as tlc
    from utils.tiktok_detection.context import LiveCheckContext
    from utils.tiktok_detection.strategies.pass1_profile_page import Pass1ProfilePage

    tlc._mark_room_ended("999")
    monkeypatch.setattr(tlc, "_fetch_tiktok_profile_page", lambda *a, **k: "<html/>")
    monkeypatch.setattr(tlc, "_room_id_from_profile_page", lambda *a, **k: ("999", False))

    def _boom(*a, **k):
        raise AssertionError("check_alive must not be called for a known-ended room")

    monkeypatch.setattr(tlc, "_verify_room_alive", _boom)
    assert Pass1ProfilePage().check(LiveCheckContext(username="x")) is None


def test_ended_room_message_is_not_scheduled_stream(monkeypatch, caplog):
    """status=4 is 'ended', not 'scheduled stream, not live yet'."""
    from utils import tiktok_live_checker as tlc

    monkeypatch.setattr(tlc, "_get_dispatcher", lambda: _FakeDispatcher(("u", "555")))
    monkeypatch.setattr(tlc, "_verify_room_alive", lambda *a, **k: True)
    tlc._mark_room_ended("555")
    monkeypatch.setattr(tlc, "_fetch_hls_from_webcast_room_info", lambda *a, **k: None)

    with caplog.at_level(logging.DEBUG):
        assert tlc._check_tiktok_live_with_room_id("tomluoc211") is None
    assert "scheduled stream, not live yet" not in caplog.text
    assert "ended" in caplog.text


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200


class _FakeSession:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, *a, **k):
        return _FakeResp(self._text)

    def close(self) -> None:
        pass


class _FakeDispatcher:
    def __init__(self, result):
        self._result = result

    def check(self, ctx):
        return self._result


# ── AUDIT-02: convert delete must answer 409 on a locked file ────────────

_LOCKED = PermissionError(
    13, "The process cannot access the file because it is being used by another process"
)


def _convert_app(tmp_path: Path, reason: str):
    import api.server as srv

    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_task=lambda tid: None,
        analyse_url=lambda url, on_done, on_error: None,
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path, taildrop_target_nodes=[])
    remote_convert = SimpleNamespace(
        delete_convert_file=lambda job_id, allowed_dir: (False, reason),
        get_job=lambda job_id: None,
    )
    app = srv.create_app(service, config, remote_convert=remote_convert)  # type: ignore[arg-type]
    for route in app.routes:
        if getattr(route, "path", None) == "/api/convert/{job_id}/file" and "DELETE" in getattr(
            route, "methods", set()
        ):
            return route.endpoint
    raise AssertionError("DELETE /api/convert/{job_id}/file not found")


def test_convert_delete_of_locked_file_returns_409(tmp_path):
    import asyncio

    endpoint = _convert_app(tmp_path, str(_LOCKED))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint("d65d25b8"))
    assert exc.value.status_code == 409


def test_convert_delete_unknown_job_still_404(tmp_path):
    import asyncio

    endpoint = _convert_app(tmp_path, "Convert job not found")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint("nope"))
    assert exc.value.status_code == 404


def test_locked_delete_logs_warning_not_error(tmp_path, caplog):
    """A transient Windows lock that resolves on its own is not an ERROR."""
    from app.services.remote_convert_service import RemoteConvertService

    out = tmp_path / "clip_iPhone.mp4"
    out.write_bytes(b"x")

    svc = RemoteConvertService.__new__(RemoteConvertService)
    job = SimpleNamespace(
        status=_Completed(),
        output_filename=str(out),
        output_deleted=False,
        _lock=_NullLock(),
    )
    svc.get_job = lambda job_id: job  # type: ignore[method-assign]

    import app.services.remote_convert_service as rcs

    original = rcs.ConversionStatus.COMPLETED
    job.status = original

    def _raise(self, **kw):
        raise _LOCKED

    with caplog.at_level(logging.DEBUG):
        import unittest.mock as m

        with m.patch.object(Path, "unlink", _raise):
            ok, reason = svc.delete_convert_file("d65d25b8", tmp_path)

    assert ok is False
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


class _Completed:
    pass


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── AUDIT-03: yt-dlp retry noise ─────────────────────────────────────────


def test_diag_logger_error_is_downgradable():
    """A recoverable retry attempt must not write ERROR records."""
    from infrastructure.downloader.yt_dlp_engine import _DiagLogger

    lg = _DiagLogger()
    assert lg.errors_are_recoverable is False
    lg.errors_are_recoverable = True
    assert lg.errors_are_recoverable is True


def test_recoverable_diag_logger_logs_at_debug(caplog):
    from infrastructure.downloader.yt_dlp_engine import _DiagLogger

    lg = _DiagLogger()
    lg.errors_are_recoverable = True
    with caplog.at_level(logging.DEBUG):
        lg.error("ERROR: [TikTok] 123: Unexpected response from webpage request")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert caplog.records


def test_recoverable_errors_can_be_switched_off(caplog):
    """enabled=False leaves ERROR records alone (non-TikTok downloads keep them)."""
    from infrastructure.downloader.yt_dlp_engine import _DiagLogger, _recoverable_yt_dlp_errors

    lg = _DiagLogger()
    with caplog.at_level(logging.DEBUG), _recoverable_yt_dlp_errors(lg, enabled=False):
        lg.error("ERROR: [Instagram] boom")
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_recoverable_errors_restores_previous_flag():
    """The first ladder rung must not leave errors muted afterwards."""
    from infrastructure.downloader.yt_dlp_engine import _DiagLogger, _recoverable_yt_dlp_errors

    lg = _DiagLogger()
    with _recoverable_yt_dlp_errors(lg, enabled=True):
        assert lg.errors_are_recoverable is True
    assert lg.errors_are_recoverable is False


def test_diag_logger_strips_ansi_colour(caplog):
    """yt-dlp wrote raw \\x1b[0;31m escapes into omnidl_debug.log."""
    from infrastructure.downloader.yt_dlp_engine import _DiagLogger

    with caplog.at_level(logging.DEBUG):
        _DiagLogger().error("\x1b[0;31mERROR:\x1b[0m [TikTok] 123: boom")
    assert "\x1b[" not in caplog.text


def test_extract_opts_disable_yt_dlp_colour():
    """color="no_color" keeps ANSI escapes out of the log file.

    The deprecated "no_color": True made yt-dlp write params["color"] back into
    the opts dict we pass it, so every retry that reused those opts logged
    'Overwriting params from "color" with "no_color"'.
    """
    import inspect

    import infrastructure.downloader.yt_dlp_engine as eng

    src = inspect.getsource(eng)
    assert src.count('"color": "no_color"') >= 2
    assert '"no_color": True' not in src


# ── AUDIT-04: pass-0 global 10013 backoff ────────────────────────────────


def test_pass0_10013_cooldown_is_global_not_per_user(monkeypatch):
    """10013 means TikTok requires signing — that is not user-specific."""
    from utils.tiktok_detection.context import LiveCheckContext
    from utils.tiktok_detection.strategies.pass0_webcast_api import Pass0WebcastApi

    Pass0WebcastApi._reset_cooldown()
    calls: list[str] = []

    def _session(jar):
        calls.append("x")
        return _FakeSession('{"status_code": 10013}')

    monkeypatch.setattr("utils.tiktok_live_checker._get_impersonate_session", _session, raising=True)

    s = Pass0WebcastApi()
    assert s.check(LiveCheckContext(username="alice")) is None
    before = len(calls)
    # A *different* username must also be suppressed by the same cooldown.
    assert s.check(LiveCheckContext(username="bob")) is None
    assert len(calls) == before
    Pass0WebcastApi._reset_cooldown()


def test_pass0_marks_itself_unavailable_after_all_10013(monkeypatch):
    from utils.tiktok_detection.context import LiveCheckContext
    from utils.tiktok_detection.strategies.pass0_webcast_api import Pass0WebcastApi

    Pass0WebcastApi._reset_cooldown()
    monkeypatch.setattr(
        "utils.tiktok_live_checker._get_impersonate_session",
        lambda jar: _FakeSession('{"status_code": 10013}'),
        raising=True,
    )
    s = Pass0WebcastApi()
    ctx = LiveCheckContext(username="alice")
    assert ctx.unavailable is False
    s.check(ctx)
    assert ctx.unavailable is True
    Pass0WebcastApi._reset_cooldown()


# ── AUDIT-05: health daemon must be able to disable a dead strategy ──────


def test_probe_records_failure_when_strategy_reports_unavailable():
    from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
    from utils.tiktok_detection.strategy import LiveDetectionStrategy

    class Dead(LiveDetectionStrategy):
        name = "dead"

        def check(self, ctx):
            ctx.unavailable = True
            return None

    registry = StrategyHealthRegistry()
    daemon = HealthDaemon([Dead()], registry)
    daemon._schedule = lambda: None  # type: ignore[method-assign]  # no background timer
    daemon._probe_all()
    daemon._probe_all()
    assert registry.is_enabled("dead") is False


def test_probe_keeps_working_strategy_enabled():
    from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
    from utils.tiktok_detection.strategy import LiveDetectionStrategy

    class OfflineButHealthy(LiveDetectionStrategy):
        name = "ok"

        def check(self, ctx):
            return None  # user simply is not live

    registry = StrategyHealthRegistry()
    daemon = HealthDaemon([OfflineButHealthy()], registry)
    daemon._schedule = lambda: None  # type: ignore[method-assign]  # no background timer
    daemon._probe_all()
    daemon._probe_all()
    assert registry.is_enabled("ok") is True


def test_probe_verdict_survives_concurrent_check_on_same_strategy():
    """The unavailable verdict is per-call, not per-strategy instance.

    The dispatcher's thread pool and the health daemon run the *same* strategy
    objects, so a concurrent check for another account must not overwrite the
    daemon's verdict between its check() returning and the daemon reading it.
    """
    from utils.tiktok_detection.context import LiveCheckContext
    from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
    from utils.tiktok_detection.strategy import LiveDetectionStrategy

    class Blocked(LiveDetectionStrategy):
        name = "busy"

        def check(self, ctx):
            ctx.unavailable = True
            # Simulate the interleaved check for another account that used to
            # reset the shared instance flag back to False.
            LiveCheckContext(username="someone-else").unavailable = False
            return None

    registry = StrategyHealthRegistry()
    daemon = HealthDaemon([Blocked()], registry)
    daemon._schedule = lambda: None  # type: ignore[method-assign]
    daemon._probe_all()
    daemon._probe_all()
    assert registry.is_enabled("busy") is False


# ── AUDIT-06: signed Windows exit codes ──────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        (4294967256, -40),  # the qsv failure at 2026-08-30 18:49:53
        (4294967295, -1),  # h264_nvenc validation probe
        (0, 0),
        (1, 1),
        (-9, -9),
    ],
)
def test_exit_code_rendered_signed(raw, shown):
    from app.services.ffmpeg_convert_service import _signed_exit_code

    assert _signed_exit_code(raw) == shown
