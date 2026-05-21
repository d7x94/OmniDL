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
