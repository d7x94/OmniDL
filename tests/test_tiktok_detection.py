from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher
from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
from utils.tiktok_detection.strategies.pass0_webcast_api import (
    Pass0WebcastApi,
    _valid_room_id,
)
from utils.tiktok_detection.strategies.pass3_user_api import (
    Pass3UserApi,
)
from utils.tiktok_detection.strategies.pass3_user_api import (
    _valid_room_id as _pass3_valid_room_id,
)
from utils.tiktok_detection.strategies.pass4_api_live_room import (
    Pass4ApiLiveRoom,
)
from utils.tiktok_detection.strategy import StreamConfirmedEndedError


def _make_strategy(name="pass0", can_run=True):
    s = MagicMock()
    s.name = name
    s.can_run.return_value = can_run
    return s


def _make_health():
    return StrategyHealthRegistry()


# ── HealthDaemon ──────────────────────────────────────────────────────────────


def test_health_daemon_stop_cancels_timer():
    daemon = HealthDaemon(strategies=[], registry=_make_health())
    mock_timer = MagicMock(spec=threading.Timer)
    daemon._timer = mock_timer
    daemon._stopped = False
    daemon.stop()
    assert daemon._stopped is True
    mock_timer.cancel.assert_called_once()
    assert daemon._timer is None


def test_health_daemon_stop_no_timer():
    daemon = HealthDaemon(strategies=[], registry=_make_health())
    daemon._timer = None
    daemon._stopped = False
    daemon.stop()
    assert daemon._stopped is True


def test_health_daemon_schedule_noop_when_stopped():
    daemon = HealthDaemon(strategies=[], registry=_make_health())
    daemon._stopped = True
    daemon._schedule()
    assert daemon._timer is None


def test_health_daemon_probe_all_noop_when_stopped():
    daemon = HealthDaemon(strategies=[], registry=_make_health())
    daemon._stopped = True
    daemon._probe_all()


def test_health_daemon_probe_all_records_success():
    strategy = _make_strategy()
    strategy.check.return_value = None
    registry = _make_health()
    daemon = HealthDaemon(strategies=[strategy], registry=registry)
    daemon._stopped = False
    with patch.object(daemon, "_schedule"):
        daemon._probe_all()
    assert registry.is_enabled("pass0")


def test_health_daemon_probe_all_records_404_as_success():
    strategy = _make_strategy()
    strategy.check.side_effect = RuntimeError("404 not found")
    registry = _make_health()
    daemon = HealthDaemon(strategies=[strategy], registry=registry)
    daemon._stopped = False
    with patch.object(daemon, "_schedule"):
        daemon._probe_all()
    assert registry.is_enabled("pass0")


def test_health_daemon_probe_all_records_failure():
    strategy = _make_strategy()
    strategy.check.side_effect = RuntimeError("rate limited")
    registry = _make_health()
    daemon = HealthDaemon(strategies=[strategy], registry=registry)
    daemon._stopped = False
    with patch.object(daemon, "_schedule"):
        daemon._probe_all()
        daemon._probe_all()
    assert not registry.is_enabled("pass0")


def test_health_daemon_probe_all_generic_exception():
    strategy = _make_strategy()
    strategy.check.side_effect = ValueError("unexpected")
    registry = _make_health()
    daemon = HealthDaemon(strategies=[strategy], registry=registry)
    daemon._stopped = False
    with patch.object(daemon, "_schedule"):
        daemon._probe_all()
        daemon._probe_all()
    assert not registry.is_enabled("pass0")


# ── LiveDetectionDispatcher ───────────────────────────────────────────────────


def test_dispatcher_generic_exception_in_done():
    strategy = _make_strategy()
    strategy.check.side_effect = ValueError("oops")
    dispatcher = LiveDetectionDispatcher(strategies=[strategy], health=_make_health())
    ctx = LiveCheckContext(username="testuser")
    result = dispatcher.check(ctx)
    assert result is None


def test_dispatcher_stream_confirmed_ended_cancels():
    strategy = _make_strategy()
    strategy.check.side_effect = StreamConfirmedEndedError("ended")
    dispatcher = LiveDetectionDispatcher(strategies=[strategy], health=_make_health())
    ctx = LiveCheckContext(username="testuser")
    result = dispatcher.check(ctx)
    assert result is None


def test_dispatcher_reraises_last_runtime_error():
    strategy = _make_strategy()
    strategy.check.side_effect = RuntimeError("hard error")
    dispatcher = LiveDetectionDispatcher(strategies=[strategy], health=_make_health())
    ctx = LiveCheckContext(username="testuser")
    with pytest.raises(RuntimeError, match="hard error"):
        dispatcher.check(ctx)


def test_dispatcher_live_result_wins_over_confirmed_ended():
    """A pass in `done` says ended, a slower pass in `pending` returns a live
    result — the live result must win (profile-page caching false-ends)."""
    import time

    ended = _make_strategy(name="ended")
    ended.check.side_effect = StreamConfirmedEndedError("ended")

    def _slow_live(ctx):
        time.sleep(0.3)
        return LiveCheckResult(live_url="https://x/live", room_id="555", strategy_name="live")

    live = _make_strategy(name="live")
    live.check.side_effect = _slow_live

    dispatcher = LiveDetectionDispatcher(strategies=[ended, live], health=_make_health())
    result = dispatcher.check(LiveCheckContext(username="testuser"))
    assert result == ("https://x/live", "555")


def test_dispatcher_all_confirmed_ended_returns_none():
    s1 = _make_strategy(name="e1")
    s1.check.side_effect = StreamConfirmedEndedError("ended")
    s2 = _make_strategy(name="e2")
    s2.check.side_effect = StreamConfirmedEndedError("ended")
    dispatcher = LiveDetectionDispatcher(strategies=[s1, s2], health=_make_health())
    assert dispatcher.check(LiveCheckContext(username="testuser")) is None


def test_dispatcher_pending_timeout_does_not_raise(monkeypatch):
    """B2: a never-completing pending pass triggers TimeoutError in as_completed;
    the dispatcher must swallow it and return None, not propagate."""
    import time

    import utils.tiktok_detection.dispatcher as disp

    fast = _make_strategy(name="fast")
    fast.check.return_value = None
    slow = _make_strategy(name="slow")
    slow.check.side_effect = lambda ctx: time.sleep(0.5)

    def _raise_timeout(*a, **k):
        raise TimeoutError("timed out")

    monkeypatch.setattr(disp, "as_completed", _raise_timeout)
    dispatcher = LiveDetectionDispatcher(strategies=[fast, slow], health=_make_health())
    assert dispatcher.check(LiveCheckContext(username="testuser")) is None


# ── Pass0WebcastApi ────────────────────────────────────────────────────────

# ── _valid_room_id (shared helper) ────────────────────────────────────────


def test_valid_room_id_digit_string():
    assert _valid_room_id("123456") == "123456"


def test_valid_room_id_zero_returns_none():
    assert _valid_room_id("0") is None


def test_valid_room_id_empty_returns_none():
    assert _valid_room_id("") is None
    assert _valid_room_id(None) is None


def test_valid_room_id_non_digit_returns_none():
    assert _valid_room_id("abc") is None


def test_pass3_valid_room_id_int_input():
    assert _pass3_valid_room_id(789) == "789"


# ── Pass0WebcastApi ────────────────────────────────────────────────────────


def _pass0_ctx(**kw):
    return LiveCheckContext(username=kw.pop("username", "testuser"), **kw)


def _mock_session(status_code=200, body="{}"):
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    session.get.return_value = resp
    return session


def _patch_pass0(session):
    return patch(
        "utils.tiktok_detection.strategies.pass0_webcast_api.Pass0WebcastApi.check",
        wraps=None,
    )


def _call_pass0(ctx, session):
    strategy = Pass0WebcastApi()
    with patch(
        "utils.tiktok_detection.strategies.pass0_webcast_api.Pass0WebcastApi._all_10013_until",
        0.0,
    ):
        with patch(
            "utils.tiktok_live_checker._get_impersonate_session",
            return_value=session,
        ):
            with patch(
                "utils.tiktok_live_checker._load_cookie_jar",
                return_value=None,
            ):
                return strategy.check(ctx)


def test_pass0_cooldown_returns_none():
    import time

    # The cooldown is one shared deadline, not a per-username entry: 10013
    # ("signing required") is a property of the endpoint, not of an account.
    strategy = Pass0WebcastApi()
    Pass0WebcastApi._all_10013_until = time.monotonic() + 999.0
    try:
        assert strategy.check(_pass0_ctx(username="cooldown_user_x")) is None
        # A different account is suppressed by the same deadline.
        assert strategy.check(_pass0_ctx(username="someone_else")) is None
    finally:
        Pass0WebcastApi._reset_cooldown()


def test_pass0_http_non_200_returns_none():
    ctx = _pass0_ctx()
    session = _mock_session(status_code=403)
    result = _call_pass0(ctx, session)
    assert result is None


def test_pass0_status_code_10013_all_combos_sets_cooldown():
    ctx = _pass0_ctx()
    body = json.dumps({"status_code": 10013})
    session = _mock_session(body=body)
    strategy = Pass0WebcastApi()
    Pass0WebcastApi._reset_cooldown()
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=session,
    ):
        with patch(
            "utils.tiktok_live_checker._load_cookie_jar",
            return_value=None,
        ):
            result = strategy.check(ctx)
    assert result is None
    import time

    assert Pass0WebcastApi._all_10013_until > time.monotonic()
    Pass0WebcastApi._reset_cooldown()


def test_pass0_no_rooms_returns_none():
    ctx = _pass0_ctx()
    body = json.dumps({"status_code": 0, "data": {"room_list": []}})
    session = _mock_session(body=body)
    result = _call_pass0(ctx, session)
    assert result is None


def test_pass0_live_room_returns_result():
    ctx = _pass0_ctx()
    body = json.dumps(
        {
            "status_code": 0,
            "data": {"room_list": [{"id_str": "99991", "status": 2}]},
        }
    )
    session = _mock_session(body=body)
    result = _call_pass0(ctx, session)
    assert isinstance(result, LiveCheckResult)
    assert result.room_id == "99991"
    assert "testuser" in result.live_url
    assert result.strategy_name == "pass0_webcast_api"


def test_pass0_room_status_not_2_returns_none():
    ctx = _pass0_ctx()
    body = json.dumps(
        {
            "status_code": 0,
            "data": {"room_list": [{"id_str": "88881", "status": 1}]},
        }
    )
    session = _mock_session(body=body)
    result = _call_pass0(ctx, session)
    assert result is None


def test_pass0_network_error_raises():
    ctx = _pass0_ctx()
    session = MagicMock()
    session.get.side_effect = ConnectionError("timeout")
    with pytest.raises(RuntimeError, match="pass0 network error"):
        _call_pass0(ctx, session)


def test_pass0_with_sec_user_id_adds_combo():
    ctx = _pass0_ctx(share_url="https://t.co/?sec_user_id=MS4wLjABAAAA")
    body = json.dumps({"status_code": 0, "data": {"room_list": []}})
    session = _mock_session(body=body)
    result = _call_pass0(ctx, session)
    # Two combos tried (unique_id + sec_user_id) — both returned no rooms
    assert result is None
    assert session.get.call_count >= 1


def test_pass0_with_user_id_adds_combo():
    ctx = _pass0_ctx(share_url="https://t.co/?user_id=123456")
    body = json.dumps({"status_code": 0, "data": {"room_list": []}})
    session = _mock_session(body=body)
    result = _call_pass0(ctx, session)
    # unique_id + user_id combos tried — both returned no rooms
    assert result is None
    assert session.get.call_count >= 1


# ── Pass3UserApi ──────────────────────────────────────────────────────────


def _call_pass3(ctx, session, verify_alive=False):
    strategy = Pass3UserApi()
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=session,
    ):
        with patch(
            "utils.tiktok_live_checker._load_cookie_jar",
            return_value=None,
        ):
            with patch(
                "utils.tiktok_live_checker._verify_room_alive",
                return_value=verify_alive,
            ):
                return strategy.check(ctx)


def _pass3_ctx(**kw):
    return LiveCheckContext(username=kw.pop("username", "p3user"), **kw)


def test_pass3_http_non_200_returns_none():
    ctx = _pass3_ctx()
    session = _mock_session(status_code=429)
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_network_error_returns_none():
    ctx = _pass3_ctx()
    session = MagicMock()
    session.get.side_effect = ConnectionError("timeout")
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_empty_body_returns_none():
    ctx = _pass3_ctx()
    session = _mock_session(body="   ")
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_invalid_json_returns_none():
    ctx = _pass3_ctx()
    session = _mock_session(body="not-json{{{")
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_status_code_nonzero_returns_none():
    ctx = _pass3_ctx()
    body = json.dumps({"statusCode": 2061})
    session = _mock_session(body=body)
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_no_room_id_returns_none():
    ctx = _pass3_ctx()
    body = json.dumps({"statusCode": 0, "userInfo": {"user": {"roomId": "0"}}})
    session = _mock_session(body=body)
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_room_id_missing_entirely_returns_none():
    ctx = _pass3_ctx()
    body = json.dumps({"statusCode": 0, "userInfo": {"user": {}}})
    session = _mock_session(body=body)
    result = _call_pass3(ctx, session)
    assert result is None


def test_pass3_room_id_not_alive_returns_none():
    ctx = _pass3_ctx()
    body = json.dumps({"statusCode": 0, "userInfo": {"user": {"roomId": "55551"}}})
    session = _mock_session(body=body)
    result = _call_pass3(ctx, session, verify_alive=False)
    assert result is None


def test_pass3_live_room_alive_returns_result():
    ctx = _pass3_ctx()
    body = json.dumps({"statusCode": 0, "userInfo": {"user": {"roomId": "77771"}}})
    session = _mock_session(body=body)
    result = _call_pass3(ctx, session, verify_alive=True)
    assert isinstance(result, LiveCheckResult)
    assert result.room_id == "77771"
    assert "p3user" in result.live_url
    assert result.strategy_name == "pass3_user_api"


def test_pass3_with_sec_user_id_adds_param():
    ctx = _pass3_ctx(share_url="https://t.co/?sec_user_id=MS4wLjABAAAA")
    body = json.dumps({"statusCode": 0, "userInfo": {"user": {"roomId": "0"}}})
    session = _mock_session(body=body)
    _call_pass3(ctx, session)
    call_kwargs = session.get.call_args
    params = call_kwargs[1].get("params", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
    assert "secUid" in params or "secUid" in str(call_kwargs)


# ── Pass4ApiLiveRoom ──────────────────────────────────────────────────────


def _call_pass4(ctx, session):
    strategy = Pass4ApiLiveRoom()
    with patch(
        "utils.tiktok_live_checker._get_impersonate_session",
        return_value=session,
    ):
        with patch(
            "utils.tiktok_live_checker._load_cookie_jar",
            return_value=None,
        ):
            return strategy.check(ctx)


def _pass4_ctx(**kw):
    return LiveCheckContext(username=kw.pop("username", "p4user"), **kw)


def test_pass4_http_non_200_returns_none():
    session = _mock_session(status_code=403)
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_network_error_returns_none():
    session = MagicMock()
    session.get.side_effect = ConnectionError("timeout")
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_empty_body_returns_none():
    session = _mock_session(body="   ")
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_status_code_nonzero_returns_none():
    body = json.dumps({"statusCode": 2061})
    session = _mock_session(body=body)
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_status_4_ended_returns_none():
    body = json.dumps({"statusCode": 0, "data": {"user": {"roomId": "12345", "status": 4}}})
    session = _mock_session(body=body)
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_live_no_room_id_returns_none():
    body = json.dumps({"statusCode": 0, "data": {"user": {"roomId": "0", "status": 2}}})
    session = _mock_session(body=body)
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_room_id_missing_entirely_returns_none():
    body = json.dumps({"statusCode": 0, "data": {"user": {"status": 2}}})
    session = _mock_session(body=body)
    assert _call_pass4(_pass4_ctx(), session) is None


def test_pass4_live_returns_result():
    body = json.dumps({"statusCode": 0, "data": {"user": {"roomId": "7651097689811880724", "status": 2}}})
    session = _mock_session(body=body)
    result = _call_pass4(_pass4_ctx(), session)
    assert isinstance(result, LiveCheckResult)
    assert result.room_id == "7651097689811880724"
    assert "p4user" in result.live_url
    assert result.strategy_name == "pass4_api_live_room"


def test_pass4_live_status_from_live_room_fallback():
    body = json.dumps(
        {
            "statusCode": 0,
            "data": {"user": {"roomId": "999888"}, "liveRoom": {"status": 2}},
        }
    )
    session = _mock_session(body=body)
    result = _call_pass4(_pass4_ctx(), session)
    assert isinstance(result, LiveCheckResult)
    assert result.room_id == "999888"


def test_pass4_data_null_returns_none():
    # B1 regression: {"statusCode":0,"data":null} must return None, not raise
    # AttributeError (data.get("data", {}) returns None when the value is null).
    body = json.dumps({"statusCode": 0, "data": None})
    session = _mock_session(body=body)
    assert _call_pass4(_pass4_ctx(), session) is None


# ---------------------------------------------------------------------------
# Gate 1 -- detection gate: _verify_room_alive called on every dispatcher hit
# ---------------------------------------------------------------------------


def test_detection_gate_verify_alive_false_returns_none(monkeypatch):
    """Dispatcher found roomId but stream already ended -> check_tiktok_live returns None."""
    import utils.tiktok_live_checker as ttlc

    # dispatcher.check() returns (live_url, room_id) tuple
    fake_tuple = ("https://www.tiktok.com/@gateuser/live", "111222")
    monkeypatch.setattr(
        ttlc, "_get_dispatcher", lambda: type("D", (), {"check": lambda self, ctx: fake_tuple})()
    )
    monkeypatch.setattr(ttlc, "_verify_room_alive", lambda *a, **k: False)
    monkeypatch.setattr(ttlc, "_ROOM_ID_CACHE", {})

    result = ttlc.check_tiktok_live("gateuser")
    assert result is None


def test_detection_gate_verify_alive_true_returns_result(monkeypatch):
    """Dispatcher found roomId and stream confirmed live -> returns live URL."""
    import utils.tiktok_live_checker as ttlc

    fake_tuple = ("https://www.tiktok.com/@gateuser/live", "111222")
    monkeypatch.setattr(
        ttlc, "_get_dispatcher", lambda: type("D", (), {"check": lambda self, ctx: fake_tuple})()
    )
    monkeypatch.setattr(ttlc, "_verify_room_alive", lambda *a, **k: True)
    monkeypatch.setattr(
        ttlc,
        "_fetch_hls_from_webcast_room_info",
        lambda *a, **k: ("https://hls.example.com/live.m3u8", "111222"),
    )
    monkeypatch.setattr(ttlc, "_ROOM_ID_CACHE", {})

    result = ttlc.check_tiktok_live("gateuser")
    assert result == "https://www.tiktok.com/@gateuser/live"


# ---------------------------------------------------------------------------
# BUG-TT-PROBE-429 -- a non-200 is not a probe success
# ---------------------------------------------------------------------------


def test_pass0_non_200_sets_network_error():
    ctx = _pass0_ctx()
    assert _call_pass0(ctx, _mock_session(status_code=403)) is None
    assert ctx.network_error is True


def test_pass3_non_200_sets_network_error():
    ctx = _pass3_ctx()
    assert _call_pass3(ctx, _mock_session(status_code=429)) is None
    assert ctx.network_error is True


def test_pass4_non_200_sets_network_error():
    ctx = _pass4_ctx()
    assert _call_pass4(ctx, _mock_session(status_code=503)) is None
    assert ctx.network_error is True


def test_health_daemon_non_200_probe_does_not_re_enable():
    """A 429 must not resurrect a strategy the daemon already disabled."""
    registry = _make_health()
    strategy = _make_strategy(name="pass3_user_api")

    def _check(ctx):
        ctx.network_error = True
        return None

    strategy.check.side_effect = _check

    registry.record_probe_failure("pass3_user_api")
    registry.record_probe_failure("pass3_user_api")
    assert registry.is_enabled("pass3_user_api") is False

    HealthDaemon(strategies=[strategy], registry=registry)._probe_all()
    assert registry.is_enabled("pass3_user_api") is False


# ---------------------------------------------------------------------------
# BUG-TT-PASS4-REMARK -- _mark_room_ended is idempotent and TTL-bounded
# ---------------------------------------------------------------------------


def test_mark_room_ended_first_call_only():
    import utils.tiktok_live_checker as ttlc

    ttlc._ENDED_ROOM_IDS.clear()
    assert ttlc._mark_room_ended("555111") is True
    assert ttlc._mark_room_ended("555111") is False
    assert ttlc._mark_room_ended("") is False


def test_mark_room_ended_does_not_refresh_ttl():
    """Re-marking must not push the expiry out, or the TTL never elapses."""
    import time

    import utils.tiktok_live_checker as ttlc

    ttlc._ENDED_ROOM_IDS.clear()
    ttlc._mark_room_ended("555222")
    stamped = ttlc._ENDED_ROOM_IDS["555222"]

    # Age the mark to just inside the TTL, then poll again like pass-4 does.
    ttlc._ENDED_ROOM_IDS["555222"] = stamped - (ttlc._ENDED_ROOM_TTL - 1.0)
    ttlc._mark_room_ended("555222")
    assert ttlc._ENDED_ROOM_IDS["555222"] == stamped - (ttlc._ENDED_ROOM_TTL - 1.0)

    # Past the TTL the mark is dropped, so a restarted broadcast reusing the
    # same roomId is visible to pass-1/pass-2 again.
    ttlc._ENDED_ROOM_IDS["555222"] = time.monotonic() - ttlc._ENDED_ROOM_TTL - 1.0
    assert ttlc._room_recently_ended("555222") is False


def test_pass4_marked_suffix_logged_once(caplog):
    import utils.tiktok_live_checker as ttlc

    ttlc._ENDED_ROOM_IDS.clear()
    body = json.dumps({"statusCode": 0, "data": {"user": {"roomId": "555333", "status": 4}}})
    with caplog.at_level("DEBUG", logger="utils.tiktok_detection.strategies.pass4_api_live_room"):
        _call_pass4(_pass4_ctx(), _mock_session(body=body))
        _call_pass4(_pass4_ctx(), _mock_session(body=body))
    marked = [r for r in caplog.records if "marked room 555333 ended" in r.getMessage()]
    assert len(marked) == 1
