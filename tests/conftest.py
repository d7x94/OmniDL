"""
tests/conftest.py
Configure sys.path so that omnidl package modules are importable from tests.
"""

import logging
import sys
import types
from pathlib import Path

# Add the omnidl source root (parent of this tests/ directory) to sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# PySide6 stub for headless CI (no libEGL / display server).
# Activates only when PySide6 cannot be imported natively.
# Provides fake classes that are subclassable and instantiable so UI modules
# load cleanly; tests then access the pure-Python functions within them.
# ---------------------------------------------------------------------------
try:
    import PySide6.QtWidgets  # noqa: F401
except ImportError:

    class _QtMeta(type):
        """Metaclass: class-level attr access returns a stub class (supports chained access)."""

        def __getattr__(cls, name):
            sub = _QtMeta(name, (_Q,), {"__module__": getattr(cls, "__module__", "")})
            setattr(cls, name, sub)
            return sub

    class _Q(metaclass=_QtMeta):
        """Stub base: subclassable, instantiable, attribute-safe."""

        def __init__(self, *a, **kw):
            pass

        def __init_subclass__(cls, **kw):
            super().__init_subclass__(**kw)

        def __getattr__(self, name):
            return lambda *a, **kw: None

    class _FakeMod(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            cls = _QtMeta(name, (_Q,), {"__module__": self.__name__})
            setattr(self, name, cls)
            return cls

    _pyside6 = types.ModuleType("PySide6")
    sys.modules["PySide6"] = _pyside6
    for _sub in (
        "QtWidgets",
        "QtCore",
        "QtGui",
        "QtNetwork",
        "QtMultimedia",
        "QtSvg",
        "QtOpenGL",
    ):
        _m = _FakeMod(f"PySide6.{_sub}")
        sys.modules[f"PySide6.{_sub}"] = _m
        setattr(_pyside6, _sub, _m)
    del _pyside6, _sub, _m


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
