"""
tests/test_rate_limit_prune.py
Regression test for the per-IP rate-limiter prune in api/server.py.

The old prune deleted only buckets whose deque was empty, and a deque is
drained only when that same IP sends another request — so a one-shot IP
(scanner, bot, single iOS Shortcuts run) kept its key for the process
lifetime.  The prune must now drop any bucket whose newest timestamp is
older than the sliding window.
"""

from __future__ import annotations

import collections
import time

import api.server as srv


def _reset() -> None:
    srv._rate_buckets.clear()
    srv._rate_last_cleanup = 0.0


def test_stale_one_shot_bucket_is_pruned():
    _reset()
    now = time.monotonic()
    # A bucket that is NOT empty but whose only entry is older than the window.
    srv._rate_buckets["1.2.3.4"] = collections.deque([now - srv._RATE_WINDOW - 5.0])
    srv._rate_last_cleanup = now - 301.0  # force the 5-minute prune branch

    assert srv._check_rate_limit("9.9.9.9") is True

    assert "1.2.3.4" not in srv._rate_buckets
    assert "9.9.9.9" in srv._rate_buckets
    _reset()


def test_active_bucket_survives_prune():
    _reset()
    now = time.monotonic()
    srv._rate_buckets["5.6.7.8"] = collections.deque([now - 1.0])
    srv._rate_last_cleanup = now - 301.0

    assert srv._check_rate_limit("9.9.9.9") is True

    assert "5.6.7.8" in srv._rate_buckets
    _reset()


def test_limit_still_enforced():
    _reset()
    for _ in range(srv._RATE_LIMIT):
        assert srv._check_rate_limit("7.7.7.7") is True
    assert srv._check_rate_limit("7.7.7.7") is False
    _reset()
