"""
tests/test_log_feature_audit_310826.py

Regression guards for the logging-feature audit of 2026-08-31.

LOG-01  Every stdlib-logging record was attributed to the module name
        "logging" instead of the module that logged it.  All 119 named
        lines in report.log read "[INFO    ] logging - ...".  Cause:
        logging.currentframe() is sys._getframe(1) (CPython 3.11+), so it
        returned _InterceptHandler.emit's own frame; the while-loop guard
        `frame.f_code.co_filename == logging.__file__` was False on the
        first check and the hardcoded depth of 2 resolved to
        logging/__init__.py::callHandlers.

LOG-02  setup_logging() called logger.add(sys.stderr) unconditionally.
        sys.stderr is None in a PyInstaller --windowed build (both the
        Windows and macOS release builds), and loguru raises
        TypeError("Cannot log to objects of type 'NoneType'"), aborting
        startup before any log file existed.

LOG-03  logger.remove() inside setup_logging() drops every sink, including
        a debug sink from an earlier call, but _debug_sink_id kept the dead
        id.  apply_debug_logging(False) then raised ValueError out of the
        Settings toggle slot, and apply_debug_logging(True) was a silent
        no-op that left omnidl_debug.log empty.

LOG-04  apply_debug_logging(False) hardcoded root back to INFO, discarding
        the level setup_logging() was configured with.

LOG-05  log_dir.mkdir() ran outside the try/except OSError that guards the
        file sink, so an unwritable log directory crashed startup instead
        of degrading to stderr-only logging.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import utils.logger as lg


@pytest.fixture(autouse=True)
def _restore_logger_state(tmp_path):
    """Leave global loguru/root state as we found it."""
    original_dir = lg._log_dir_ref
    original_level = getattr(lg, "_base_level", logging.INFO)
    yield
    try:
        lg.apply_debug_logging(False)
    except Exception:
        lg._debug_sink_id = None
    lg._log_dir_ref = original_dir
    if hasattr(lg, "_base_level"):
        lg._base_level = original_level
    logging.getLogger().setLevel(logging.INFO)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── LOG-01: {name} must be the real caller module ────────────────────────


def test_stdlib_record_keeps_caller_module_name(tmp_path):
    lg.setup_logging(tmp_path)
    logging.getLogger("omnidl.anything").info("audit marker")

    line = _read(tmp_path / "omnidl.log")
    assert "audit marker" in line
    assert "] logging - " not in line, "record still attributed to the logging module"
    assert f"] {__name__} - " in line


def test_module_level_logging_call_keeps_caller_module_name(tmp_path):
    """logging.info() adds one extra logging/ frame; the walk must absorb it."""
    lg.setup_logging(tmp_path)
    logging.info("module level marker")

    line = _read(tmp_path / "omnidl.log")
    assert f"] {__name__} - " in line


def test_exception_traceback_still_reaches_the_log(tmp_path):
    lg.setup_logging(tmp_path)
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError:
        logging.getLogger("omnidl.anything").exception("boom")

    text = _read(tmp_path / "omnidl.log")
    assert "boom" in text
    assert "ZeroDivisionError" in text


# ── LOG-02: no console in a --windowed build ─────────────────────────────


def test_setup_logging_survives_stderr_none(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stderr", None)
    lg.setup_logging(tmp_path)  # must not raise TypeError

    logging.getLogger("omnidl.anything").info("windowed marker")
    assert "windowed marker" in _read(tmp_path / "omnidl.log")


# ── LOG-03: stale debug sink id after re-init ────────────────────────────


def test_re_setup_clears_stale_debug_sink_id(tmp_path):
    lg.setup_logging(tmp_path)
    lg.apply_debug_logging(True)
    assert lg._debug_sink_id is not None

    lg.setup_logging(tmp_path)
    assert lg._debug_sink_id is None


def test_disable_after_re_setup_does_not_raise(tmp_path):
    lg.setup_logging(tmp_path)
    lg.apply_debug_logging(True)
    lg.setup_logging(tmp_path)

    lg.apply_debug_logging(False)  # used to raise ValueError
    assert lg._debug_sink_id is None


def test_debug_sink_still_writes_after_re_setup(tmp_path):
    lg.setup_logging(tmp_path)
    lg.apply_debug_logging(True)
    lg.setup_logging(tmp_path)
    lg.apply_debug_logging(True)

    logging.getLogger("omnidl.anything").debug("debug marker")
    assert "debug marker" in _read(tmp_path / "omnidl_debug.log")


def test_disable_tolerates_externally_removed_sink(tmp_path):
    from loguru import logger as _loguru

    lg.setup_logging(tmp_path)
    lg.apply_debug_logging(True)
    _loguru.remove(lg._debug_sink_id)

    lg.apply_debug_logging(False)  # used to raise ValueError
    assert lg._debug_sink_id is None


# ── LOG-04: configured base level is restored ────────────────────────────


def test_disable_restores_configured_level(tmp_path):
    lg.setup_logging(tmp_path, level=logging.DEBUG)
    lg.apply_debug_logging(True)
    lg.apply_debug_logging(False)

    assert logging.getLogger().level == logging.DEBUG


def test_disable_restores_info_by_default(tmp_path):
    lg.setup_logging(tmp_path)
    lg.apply_debug_logging(True)
    lg.apply_debug_logging(False)

    assert logging.getLogger().level == logging.INFO


# ── LOG-05: unwritable log directory ─────────────────────────────────────


def test_unwritable_log_dir_does_not_crash_startup(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _boom)
    lg.setup_logging(tmp_path / "nope")  # must not raise

    logging.getLogger("omnidl.anything").info("still alive")
