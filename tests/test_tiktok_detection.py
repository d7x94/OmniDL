from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from utils.tiktok_detection.context import LiveCheckContext
from utils.tiktok_detection.dispatcher import LiveDetectionDispatcher
from utils.tiktok_detection.health import HealthDaemon, StrategyHealthRegistry
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
    import pytest

    with pytest.raises(RuntimeError, match="hard error"):
        dispatcher.check(ctx)


# ── Pass0WebcastApi ────────────────────────────────────────────────────────

import json

import pytest

from utils.tiktok_detection.context import LiveCheckResult
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
        {},
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

    strategy = Pass0WebcastApi()
    username = "cooldown_user_x"
    Pass0WebcastApi._all_10013_until[username] = time.monotonic() + 999.0
    ctx = _pass0_ctx(username=username)
    result = strategy.check(ctx)
    assert result is None
    del Pass0WebcastApi._all_10013_until[username]


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
    Pass0WebcastApi._all_10013_until.pop("testuser", None)
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

    assert Pass0WebcastApi._all_10013_until.get("testuser", 0) > time.monotonic()
    del Pass0WebcastApi._all_10013_until["testuser"]


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
