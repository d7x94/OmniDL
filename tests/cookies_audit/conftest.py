"""
tests/cookies_audit/conftest.py
Isolation guard for the v20.3.14 cookie audit tests.

While a test in this folder runs, an audit hook makes any real socket
connection, subprocess launch, or file open outside the repo, the temp dir
and the Python install fail immediately. Browser profile lookups are pointed
at the test's tmp_path, so no real browser profile or cookie store is read.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ALLOWED_ROOTS = tuple(
    str(Path(p).resolve())
    for p in {
        str(_REPO),
        tempfile.gettempdir(),
        sys.prefix,
        sys.base_prefix,
        sys.exec_prefix,
        sys.base_exec_prefix,
    }
)
_BLOCKED_EVENTS = (
    "socket.connect",
    "socket.sendto",
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.startfile",
)

_state = {"active": False}


def _path_allowed(raw) -> bool:
    if isinstance(raw, int):
        return True
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    raw = str(raw)
    if raw == os.devnull:
        return True
    real = os.path.realpath(raw)
    return any(real == root or real.startswith(root + os.sep) for root in _ALLOWED_ROOTS)


def _audit(event: str, args: tuple) -> None:
    if not _state["active"]:
        return
    if event in _BLOCKED_EVENTS:
        raise RuntimeError(f"cookies_audit guard: blocked {event}")
    if event == "open" and args and not _path_allowed(args[0]):
        raise RuntimeError(f"cookies_audit guard: blocked open outside repo/tmp: {args[0]!r}")


sys.addaudithook(_audit)


@pytest.fixture(autouse=True)
def _no_real_io(tmp_path, monkeypatch):
    for var in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "APPDATA", "HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _state["active"] = True
    try:
        yield
    finally:
        _state["active"] = False
