"""Regression guards for the 2026-09-10 log audit (omnidl_debug.log 17248-17361)."""

from __future__ import annotations

from unittest.mock import patch

from utils.tiktok_detection.context import LiveCheckContext
from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
from utils.tiktok_detection.strategies.pass0_webcast_api import Pass0WebcastApi
from utils.tiktok_detection.strategies.pass4_api_live_room import Pass4ApiLiveRoom
from utils.tiktok_detection.strategy import LiveDetectionStrategy


class _NetDown(LiveDetectionStrategy):
    name = "net_down"

    def check(self, ctx):
        ctx.network_error = True
        return None


def test_network_error_does_not_reset_failure_counter():
    """curl (7) used to be logged as 'probe ok' and re-enable a blocked pass."""
    registry = StrategyHealthRegistry()
    registry.record_probe_failure("net_down")
    registry.record_probe_failure("net_down")
    assert not registry.is_enabled("net_down")

    HealthDaemon([_NetDown()], registry)._probe_all()
    assert not registry.is_enabled("net_down")


def test_pass4_marks_ended_room_so_page_passes_stop_reporting_live():
    from utils import tiktok_live_checker as tlc

    room_id = "7679022730909469458"
    tlc._clear_room_ended(room_id)

    class _Resp:
        status_code = 200
        text = '{"statusCode":0,"data":{"user":{"roomId":"%s","status":4},"liveRoom":{"status":4}}}' % room_id

    class _Session:
        def get(self, *a, **kw):
            return _Resp()

        def close(self):
            pass

    with (
        patch.object(tlc, "_get_impersonate_session", lambda jar: _Session()),
        patch.object(tlc, "_load_cookie_jar", lambda cf: None),
    ):
        assert Pass4ApiLiveRoom().check(LiveCheckContext(username="tiktok")) is None

    assert tlc._room_recently_ended(room_id)
    tlc._clear_room_ended(room_id)


def test_pass0_cooldown_skip_is_not_a_healthy_probe():
    import time

    with Pass0WebcastApi._all_10013_until_lock:
        Pass0WebcastApi._all_10013_until = time.monotonic() + 60
    try:
        ctx = LiveCheckContext(username="tiktok")
        assert Pass0WebcastApi().check(ctx) is None
        assert ctx.unavailable
    finally:
        Pass0WebcastApi._reset_cooldown()
