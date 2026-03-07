"""
tests/conftest.py
Configure sys.path so that omnidl package modules are importable from tests.
"""
import logging
import sys
from pathlib import Path

# Add the omnidl source root (parent of this tests/ directory) to sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def pytest_configure(config):
    """Suppress 'I/O operation on closed file' noise from background threads.

    When pytest finishes a test it closes stdout/stderr. ThreadPoolExecutor
    workers may still be alive and try to emit log records to the now-closed
    stream. Python's logging module prints a traceback for each attempt —
    this is harmless noise. Patch handleError to swallow it silently.
    """
    _original = logging.Handler.handleError

    def _quiet(self, record):
        exc = sys.exc_info()[1]
        if isinstance(exc, ValueError) and "closed file" in str(exc).lower():
            return
        _original(self, record)

    logging.Handler.handleError = _quiet
