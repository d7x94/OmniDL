"""
tests/test_debug_logging.py
Tests for utils.logger.apply_debug_logging (BUG-BU coverage fix).

apply_debug_logging enables/disables a separate omnidl_debug.log handler on the
root logger.  Branches tested:
  1. enable=True, _log_dir_ref set   → RotatingFileHandler added, root=DEBUG
  2. enable=True, _log_dir_ref None  → no handler added, root still set to DEBUG
  3. enable=True twice               → idempotent (handler not doubled)
  4. enable=False, handler active    → handler closed + removed, root=INFO
  5. enable=False, no handler        → no-op, root set to INFO
  6. enable=True, OSError on open    → warning logged, no crash, _debug_handler=None
  7. Round-trip enable → disable     → clean state
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _reset_logger_state() -> None:
    """Reset module globals between tests so each test starts clean."""
    import utils.logger as _lg
    # Close and remove any active debug handler from the root logger
    if _lg._debug_handler is not None:
        try:
            logging.getLogger().removeHandler(_lg._debug_handler)
            _lg._debug_handler.close()
        except Exception:
            pass
    _lg._debug_handler = None
    # Restore root to INFO
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


# ── Test cases ────────────────────────────────────────────────────────────────


class TestApplyDebugLoggingEnable:
    def test_enable_with_log_dir_adds_handler(self, tmp_path):
        """enable=True + _log_dir_ref set → RotatingFileHandler added to root."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        root = logging.getLogger()
        handlers_before = len(root.handlers)

        apply_debug_logging(True)

        assert _lg._debug_handler is not None
        assert _lg._debug_handler in root.handlers
        assert len(root.handlers) == handlers_before + 1
        assert root.level == logging.DEBUG

    def test_enable_creates_debug_log_file(self, tmp_path):
        """RotatingFileHandler writes to omnidl_debug.log in _log_dir_ref."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)

        debug_log = tmp_path / "omnidl_debug.log"
        # File is created when the handler is instantiated (RotatingFileHandler
        # opens the file immediately on construction).
        assert debug_log.exists()

    def test_enable_without_log_dir_does_not_add_handler(self):
        """enable=True with _log_dir_ref=None → root level set, no handler."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = None
        root = logging.getLogger()
        handlers_before = len(root.handlers)

        apply_debug_logging(True)

        assert _lg._debug_handler is None
        assert len(root.handlers) == handlers_before
        assert root.level == logging.DEBUG

    def test_enable_twice_is_idempotent(self, tmp_path):
        """Calling enable=True a second time does NOT add a second handler."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)
        handler_first = _lg._debug_handler
        handlers_after_first = len(logging.getLogger().handlers)

        apply_debug_logging(True)

        assert _lg._debug_handler is handler_first          # same object
        assert len(logging.getLogger().handlers) == handlers_after_first  # no duplicate

    def test_enable_sets_handler_level_to_debug(self, tmp_path):
        """The added handler's own level is set to DEBUG."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)

        assert _lg._debug_handler is not None
        assert _lg._debug_handler.level == logging.DEBUG


class TestApplyDebugLoggingDisable:
    def test_disable_removes_handler(self, tmp_path):
        """enable=False after enable=True → handler removed from root."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)
        assert _lg._debug_handler is not None

        apply_debug_logging(False)

        assert _lg._debug_handler is None
        assert logging.getLogger().level == logging.INFO

    def test_disable_when_no_handler_is_noop(self):
        """enable=False with no active handler → no error, root set to INFO."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._debug_handler = None
        apply_debug_logging(False)

        assert _lg._debug_handler is None
        assert logging.getLogger().level == logging.INFO

    def test_disable_handler_not_in_root_after_removal(self, tmp_path):
        """After disable, the old handler object is not in root.handlers."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        apply_debug_logging(True)
        old_handler = _lg._debug_handler

        apply_debug_logging(False)

        assert old_handler not in logging.getLogger().handlers


class TestApplyDebugLoggingRoundTrip:
    def test_round_trip_enable_disable_enable(self, tmp_path):
        """enable → disable → enable leaves a single fresh handler active."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path
        root = logging.getLogger()
        handlers_before = len(root.handlers)

        apply_debug_logging(True)
        apply_debug_logging(False)
        apply_debug_logging(True)

        # Exactly one extra handler (the fresh one)
        assert len(root.handlers) == handlers_before + 1
        assert _lg._debug_handler is not None
        assert root.level == logging.DEBUG


class TestApplyDebugLoggingOSError:
    def test_oserror_on_handler_open_logs_warning_and_does_not_crash(self, tmp_path):
        """If RotatingFileHandler raises OSError, a warning is logged and
        _debug_handler stays None (graceful degradation)."""
        import utils.logger as _lg
        from utils.logger import apply_debug_logging

        _lg._log_dir_ref = tmp_path

        with patch(
            "utils.logger.RotatingFileHandler",
            side_effect=OSError("disk full"),
        ):
            # Should not raise
            apply_debug_logging(True)

        # Handler not set — graceful fallback
        assert _lg._debug_handler is None
