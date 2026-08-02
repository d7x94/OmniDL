"""
utils/memtrace.py
Opt-in memory sampler. No-op unless the environment variable
OMNIDL_MEMTRACE is set to 1/true/yes.

Logs RSS, live object count, thread count, the tracemalloc top-10 allocation
sites (diffed against a baseline snapshot) and the object types that grew since
the previous sample, every *interval* seconds to the "omnidl.memtrace" logger.

Usage (Windows):
    set OMNIDL_MEMTRACE=1 && python main.py
"""

from __future__ import annotations

import collections
import gc
import logging
import os
import sys
import threading
import tracemalloc

logger = logging.getLogger("omnidl.memtrace")

_TOP_N = 10
_TOP_TYPES = 5

# One frame per trace. Deeper tracebacks make tracemalloc's internal traceback
# table grow without bound (a new key per distinct call path), which turns the
# sampler itself into the memory growth it is supposed to measure.
_FRAMES = 1

_started = False
_stop = threading.Event()

# Import machinery and the sampler's own allocations are noise in the top-10.
_FILTERS = (
    tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
    tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
    tracemalloc.Filter(False, tracemalloc.__file__),
    tracemalloc.Filter(False, __file__),
)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    # Without an explicit restype the pseudo-handle (-1) comes back as a 32-bit
    # int and is passed truncated into the 64-bit HANDLE parameter below, so
    # GetProcessMemoryInfo fails and RSS reads as 0.0 forever.
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    ]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def enabled() -> bool:
    return os.environ.get("OMNIDL_MEMTRACE", "").strip().lower() in ("1", "true", "yes")


def rss_mb() -> float:
    """Current resident set size in MB."""
    if sys.platform == "win32":
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        if not _psapi.GetProcessMemoryInfo(
            _kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            return 0.0
        return counters.WorkingSetSize / (1024 * 1024)

    if sys.platform == "darwin":
        import resource

        # ru_maxrss is PEAK RSS on macOS (bytes) — it never decreases.
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

    # /proc/self/statm field 1 is the resident page count. getrusage would give
    # peak RSS here too, which cannot show a leak levelling off.
    with open("/proc/self/statm", encoding="ascii") as fh:
        pages = int(fh.read().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


def _type_counts(objects: list) -> collections.Counter:
    return collections.Counter(type(o).__name__ for o in objects)


def _format_sample(
    snapshot: tracemalloc.Snapshot,
    baseline: tracemalloc.Snapshot,
    total_objects: int,
    type_delta: collections.Counter,
) -> list[str]:
    """Build the log lines for one sample. Pure — no logging, no snapshotting."""
    lines = [f"RSS={rss_mb():.1f} MB | objects={total_objects} | threads={threading.active_count()}"]

    growth = type_delta.most_common(_TOP_TYPES)
    if growth:
        lines.append("  types: " + ", ".join(f"{name} {count:+d}" for name, count in growth))

    for rank, stat in enumerate(snapshot.compare_to(baseline, "lineno")[:_TOP_N], 1):
        # Frames are ordered oldest-first, so [-1] is the allocation site and
        # [0] would be whatever called into it at the bottom of the stack.
        frame = stat.traceback[-1]
        lines.append(
            f"  #{rank} {stat.size_diff / 1024:+.1f} KB "
            f"({stat.count_diff:+d} blocks) {frame.filename}:{frame.lineno}"
        )
    return lines


def start(interval: int = 60) -> None:
    """Start the sampler thread. No-op when disabled or already running."""
    global _started
    if _started or not enabled():
        return
    _started = True
    _stop.clear()

    tracemalloc.start(_FRAMES)
    baseline = tracemalloc.take_snapshot().filter_traces(_FILTERS)
    prev_types = _type_counts(gc.get_objects())
    logger.info("memtrace active — interval=%ds, baseline RSS=%.1f MB", interval, rss_mb())

    def _loop() -> None:
        nonlocal prev_types
        while not _stop.wait(interval):
            try:
                snapshot = tracemalloc.take_snapshot().filter_traces(_FILTERS)
                objects = gc.get_objects()
                types_now = _type_counts(objects)
                total = len(objects)
                del objects
                for line in _format_sample(snapshot, baseline, total, types_now - prev_types):
                    logger.info("%s", line)
                prev_types = types_now
            except Exception as exc:
                logger.warning("memtrace sample failed: %s", exc)

    threading.Thread(target=_loop, daemon=True, name="omnidl-memtrace").start()


def stop() -> None:
    """Stop the sampler thread and tracing. No-op when never started."""
    global _started
    if not _started:
        return
    _started = False
    _stop.set()
    if tracemalloc.is_tracing():
        tracemalloc.stop()
