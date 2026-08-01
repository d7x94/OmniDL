"""
tests/test_memtrace.py
Regression tests for the opt-in memory sampler.

The sampler shipped with three defects that made its own log unusable:
  * stat.traceback[0] reported the OLDEST frame, so every line named main(),
    Thread._bootstrap_inner() or the import machinery instead of the real
    allocation site.
  * rss_mb() always returned 0.0 on Windows (truncated process handle).
  * tracemalloc.start(25) + compare_to(..., "traceback") grew without bound.
"""

from __future__ import annotations

import collections
import threading
import tracemalloc

import pytest

from utils import memtrace


@pytest.fixture(autouse=True)
def _clean_tracemalloc():
    """Never leave tracing on — it slows every test that runs after this file."""
    yield
    memtrace._started = False
    memtrace._stop.set()
    if tracemalloc.is_tracing():
        tracemalloc.stop()


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("YES", True), (" 1 ", True), ("0", False), ("", False), ("on", False)],
)
def test_enabled_parses_env(monkeypatch, value, expected):
    monkeypatch.setenv("OMNIDL_MEMTRACE", value)
    assert memtrace.enabled() is expected


def test_enabled_false_when_unset(monkeypatch):
    monkeypatch.delenv("OMNIDL_MEMTRACE", raising=False)
    assert memtrace.enabled() is False


def test_start_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OMNIDL_MEMTRACE", raising=False)
    monkeypatch.setattr(memtrace, "_started", False)

    memtrace.start(interval=1)

    assert not tracemalloc.is_tracing()
    assert not any(t.name == "omnidl-memtrace" for t in threading.enumerate())


def test_rss_mb_is_positive():
    assert memtrace.rss_mb() > 0.0


def _allocate_here(n: int) -> list:
    return [object() for _ in range(n)]  # allocation site under test


def test_format_sample_reports_the_allocation_site_not_the_root_frame():
    tracemalloc.start(memtrace._FRAMES)
    baseline = tracemalloc.take_snapshot().filter_traces(memtrace._FILTERS)
    keep = _allocate_here(30_000)
    snapshot = tracemalloc.take_snapshot().filter_traces(memtrace._FILTERS)

    lines = memtrace._format_sample(snapshot, baseline, 123, collections.Counter())

    alloc_line = _allocate_here.__code__.co_firstlineno + 1
    assert any(f"{__file__}:{alloc_line}" in line for line in lines[1:]), lines
    # The caller's line is the oldest frame — the old traceback[0] bug.
    assert not any(
        f"{__file__}:{test_format_sample_reports_the_allocation_site_not_the_root_frame.__code__.co_firstlineno}"
        in line
        for line in lines
    )
    assert len(keep) == 30_000


def test_format_sample_header_and_type_growth():
    tracemalloc.start(memtrace._FRAMES)
    baseline = tracemalloc.take_snapshot().filter_traces(memtrace._FILTERS)
    snapshot = tracemalloc.take_snapshot().filter_traces(memtrace._FILTERS)

    lines = memtrace._format_sample(snapshot, baseline, 4242, collections.Counter({"dict": 7}))

    assert "objects=4242" in lines[0]
    assert "RSS=" in lines[0]
    assert lines[1] == "  types: dict +7"


def test_format_sample_omits_types_line_when_nothing_grew():
    tracemalloc.start(memtrace._FRAMES)
    snapshot = tracemalloc.take_snapshot().filter_traces(memtrace._FILTERS)

    lines = memtrace._format_sample(snapshot, snapshot, 1, collections.Counter())

    assert not any("types:" in line for line in lines)


def test_start_then_stop_leaves_tracing_off(monkeypatch):
    monkeypatch.setenv("OMNIDL_MEMTRACE", "1")
    monkeypatch.setattr(memtrace, "_started", False)

    memtrace.start(interval=3600)
    assert tracemalloc.is_tracing()

    memtrace.stop()
    assert not tracemalloc.is_tracing()
    assert memtrace._stop.is_set()


def test_stop_is_a_noop_when_never_started(monkeypatch):
    monkeypatch.setattr(memtrace, "_started", False)
    memtrace.stop()  # must not raise
