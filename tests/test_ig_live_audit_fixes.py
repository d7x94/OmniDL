"""
tests/test_ig_live_audit_fixes.py
Regression tests for the 2026-09-27 Instagram Live log audit (v20.3.9).

  BUG-IG-ROUTE-TEARDOWN: context routes must be removed before the
      sync_playwright() block exits, or an in-flight route.continue_() is
      cancelled during teardown and asyncio logs two CancelledError tracebacks.
  BUG-IG-STDERR-TAIL: the FFmpeg-failure log line must keep whole stderr lines;
      a 600-char tail cut off the line naming the real error whenever the
      stream URL was long.
"""

from __future__ import annotations

import io
import logging
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

import infrastructure.downloader.instagram_live_engine as eng_mod

_MPD = "https://instagram.fhan5-2.fna.fbcdn.net/live-dash/dash-abr/1.mpd?oh=x"


def _run_capture(monkeypatch, cancel_side_effect):
    """Drive _cdp_intercept_hls_impl() against a fake Playwright.

    Returns (result, events) where *events* records the order of
    ctx.unroute_all() and sync_playwright().__exit__().
    """
    events: list[str] = []

    ctx = MagicMock()
    ctx.unroute_all.side_effect = lambda **kw: events.append(f"unroute_all:{kw.get('behavior')}")
    ctx.new_cdp_session.side_effect = RuntimeError("no CDP session in test")
    page = ctx.new_page.return_value
    page.url = "https://www.instagram.com/u/live/1"
    page.evaluate.return_value = _MPD

    pw = MagicMock()
    pw.chromium.connect_over_cdp.return_value.new_context.return_value = ctx

    cm = MagicMock()
    cm.__enter__.return_value = pw
    cm.__exit__.side_effect = lambda *a: events.append("exit") or False

    proc = MagicMock(pid=1234)
    proc.poll.return_value = None

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(eng_mod, "_find_browser_exe", lambda _b: "/usr/bin/brave")
    monkeypatch.setattr(eng_mod, "_free_port", lambda: 9222)
    monkeypatch.setattr(eng_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(eng_mod.subprocess, "Popen", lambda *a, **kw: proc)

    with patch("playwright.sync_api.sync_playwright", return_value=cm):
        result = eng_mod._cdp_intercept_hls_impl(
            "https://www.instagram.com/u/live/1",
            "brave",
            5.0,
            None,
            MagicMock(side_effect=cancel_side_effect),
            None,
        )
    return result, events


class TestRouteTeardown:
    def test_routes_removed_before_playwright_exits(self, monkeypatch):
        (url, _hdrs), events = _run_capture(monkeypatch, lambda: False)

        assert url == _MPD
        assert events == ["unroute_all:wait", "exit"]

    def test_routes_removed_when_cancelled_mid_poll(self, monkeypatch):
        calls = iter([False, True])
        result, events = _run_capture(monkeypatch, lambda: next(calls, True))

        assert result == (None, {})
        assert events == ["unroute_all:wait", "exit"]


class TestStderrTail:
    def test_ffmpeg_error_log_keeps_the_line_naming_the_cause(self, tmp_path, monkeypatch, caplog):
        from domain.models.download_task import DownloadTask

        cause = "[https @ 0000] HTTP error 403 Forbidden"
        long_url = "https://instagram.fhan5-2.fna.fbcdn.net/" + "a" * 600 + ".mpd"
        stderr = "\n".join(
            [
                cause,
                "[in#0 @ 0000] Error opening input: I/O error",
                f"Error opening input file {long_url}.",
                "Error opening input files: I/O error",
            ]
        ).encode()

        proc = MagicMock()
        proc.stderr = io.BytesIO(stderr)
        polls = iter([None, 4294967291])
        proc.poll.side_effect = lambda: next(polls, 4294967291)

        real_sleep = time.sleep
        monkeypatch.setattr(eng_mod.time, "sleep", lambda _s: real_sleep(0.05))
        monkeypatch.setattr(eng_mod.subprocess, "Popen", lambda *a, **kw: proc)

        task = DownloadTask(url="https://www.instagram.com/u/live/1")
        engine = eng_mod.InstagramLiveEngine(MagicMock())
        with caplog.at_level(logging.ERROR, logger=eng_mod.logger.name):
            reason, _n = engine._record_segment(
                task,
                long_url,
                {"User-Agent": "x"},
                task.url,
                tmp_path / "out.mkv.part0",
                True,
                "ffmpeg",
                0,
                None,
            )

        assert reason == "ffmpeg_error"
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "FFmpeg exited" in logged
        assert cause in logged


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
