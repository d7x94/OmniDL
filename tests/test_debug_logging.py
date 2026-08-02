"""
tests/test_debug_logging.py
Tests for utils.logger.apply_debug_logging (loguru backend).

apply_debug_logging enables/disables a separate omnidl_debug.log loguru sink.
Branches tested:
  1. enable=True, _log_dir_ref set   -> sink added, root=DEBUG
  2. enable=True, _log_dir_ref None  -> no sink added, root=DEBUG
  3. enable=True twice               -> idempotent (sink not doubled)
  4. enable=False, sink active       -> sink removed, root=INFO
  5. enable=False, no sink           -> no-op, root=INFO
  6. enable=True, OSError on open    -> warning logged, no crash, _debug_sink_id=None
  7. Round-trip enable -> disable    -> clean state
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


def _reset_logger_state() -> None:
    """Reset module globals between tests so each test starts clean."""
    from loguru import logger as _loguru_logger

    import utils.logger as _lg

    if _lg._debug_sink_id is not None:
        try:
            _loguru_logger.remove(_lg._debug_sink_id)
        except Exception:
            pass
    _lg._debug_sink_id = None
    logging.getLogger().setLevel(logging.INFO)


@pytest.fixture(autouse=True)
def clean_logger_state(tmp_path):
    """Always reset module-level globals before and after each test."""
    import utils.logger as _lg
    original_dir = _lg._log_dir_ref
    _reset_logger_state()
    yield tmp_path
    _reset_logger_state()
    _lg._log_dir_ref = original_dir


class TestApplyDebugLoggingEnable:
    def test_enable_with_log_dir_adds_sink(self, tmp_path):
        """enable=True + _log_dir_ref set -> debug sink added, root=DEBUG."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)

        assert _lg._debug_sink_id is not None
        assert logging.getLogger().level == logging.DEBUG

    def test_enable_creates_debug_log_file(self, tmp_path):
        """Loguru creates omnidl_debug.log on sink add."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)

        assert (tmp_path / "omnidl_debug.log").exists()

    def test_enable_without_log_dir_does_not_add_sink(self):
        """enable=True with _log_dir_ref=None -> root=DEBUG, no sink."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = None
        apply_debug_logging(True)

        assert _lg._debug_sink_id is None
        assert logging.getLogger().level == logging.DEBUG

    def test_enable_twice_is_idempotent(self, tmp_path):
        """Calling enable=True a second time does NOT add a second sink."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)
        sink_id_first = _lg._debug_sink_id

        apply_debug_logging(True)

        assert _lg._debug_sink_id == sink_id_first


class TestApplyDebugLoggingDisable:
    def test_disable_removes_sink(self, tmp_path):
        """enable=False after enable=True -> sink removed, root=INFO."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)
        assert _lg._debug_sink_id is not None

        apply_debug_logging(False)

        assert _lg._debug_sink_id is None
        assert logging.getLogger().level == logging.INFO

    def test_disable_when_no_sink_is_noop(self):
        """enable=False with no active sink -> no error, root=INFO."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._debug_sink_id = None
        apply_debug_logging(False)

        assert _lg._debug_sink_id is None
        assert logging.getLogger().level == logging.INFO

    def test_disable_clears_sink_id(self, tmp_path):
        """After disable, _debug_sink_id is None."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)
        assert _lg._debug_sink_id is not None

        apply_debug_logging(False)

        assert _lg._debug_sink_id is None


class TestApplyDebugLoggingRoundTrip:
    def test_round_trip_enable_disable_enable(self, tmp_path):
        """enable -> disable -> enable leaves a single fresh sink active."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path

        apply_debug_logging(True)
        apply_debug_logging(False)
        apply_debug_logging(True)

        assert _lg._debug_sink_id is not None
        assert logging.getLogger().level == logging.DEBUG


class TestApplyDebugLoggingOSError:
    def test_oserror_on_sink_add_does_not_crash(self, tmp_path):
        """If logger.add raises OSError, _debug_sink_id stays None."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path

        with patch("utils.logger.logger") as mock_log:
            mock_log.add.side_effect = OSError("disk full")
            mock_log.warning = MagicMock()
            apply_debug_logging(True)

        assert _lg._debug_sink_id is None
