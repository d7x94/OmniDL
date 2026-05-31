"""
tests/test_account_pool.py
Unit tests for infrastructure/downloader/account_pool.py
Covers: _AccountSlot, TikTokAccount, TikTokAccountPool
"""

from __future__ import annotations

import threading
import time

import pytest

from infrastructure.downloader.account_pool import (
    TikTokAccount,
    TikTokAccountPool,
    _AccountSlot,
)

# ---------------------------------------------------------------------------
# _AccountSlot
# ---------------------------------------------------------------------------


class TestAccountSlot:
    def test_acquire_release_basic(self):
        slot = _AccountSlot(2)
        slot.acquire()
        assert slot._active == 1
        slot.release()
        assert slot._active == 0

    def test_release_clamps_to_zero(self):
        slot = _AccountSlot(2)
        # release without acquire — must not go below 0
        slot.release()
        assert slot._active == 0

    def test_set_max_increases_wakes_waiter(self):
        slot = _AccountSlot(1)
        slot.acquire()  # fills the slot

        results = []

        def waiter():
            slot.acquire()
            results.append("acquired")
            slot.release()

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        # Increase max — waiter should unblock
        slot.set_max(2)
        t.join(timeout=1.0)
        assert results == ["acquired"]
        slot.release()

    def test_set_max_decrease_does_not_evict_active(self):
        slot = _AccountSlot(3)
        slot.acquire()
        slot.acquire()
        slot.set_max(1)
        # existing holders remain active
        assert slot._active == 2
        slot.release()
        slot.release()

    def test_blocks_when_full(self):
        slot = _AccountSlot(1)
        slot.acquire()

        acquired = threading.Event()

        def try_acquire():
            slot.acquire()
            acquired.set()
            slot.release()

        t = threading.Thread(target=try_acquire)
        t.start()
        assert not acquired.wait(timeout=0.1), "Should block while slot full"
        slot.release()
        assert acquired.wait(timeout=1.0), "Should unblock after release"
        t.join(timeout=1.0)


# ---------------------------------------------------------------------------
# TikTokAccount
# ---------------------------------------------------------------------------


class TestTikTokAccount:
    def test_to_dict_round_trip(self):
        acc = TikTokAccount(name="Alice", cookie_file="/tmp/a.txt", max_slots=2, enabled=True)
        d = acc.to_dict()
        assert d["name"] == "Alice"
        assert d["cookie_file"] == "/tmp/a.txt"
        assert d["max_slots"] == 2
        assert d["enabled"] is True
        assert "id" in d

    def test_from_dict_defaults(self):
        acc = TikTokAccount.from_dict({})
        assert acc.name == "Account"
        assert acc.cookie_file == ""
        assert acc.max_slots == 1
        assert acc.enabled is True

    def test_from_dict_clamps_max_slots(self):
        acc = TikTokAccount.from_dict({"max_slots": 10})
        assert acc.max_slots == 5

        acc2 = TikTokAccount.from_dict({"max_slots": 0})
        assert acc2.max_slots == 1

    def test_from_dict_preserves_id(self):
        acc = TikTokAccount.from_dict({"id": "abc12345", "name": "Bob", "cookie_file": "b.txt"})
        assert acc.id == "abc12345"
        assert acc.name == "Bob"

    def test_from_dict_generates_id_when_missing(self):
        acc = TikTokAccount.from_dict({"name": "Charlie", "cookie_file": "c.txt"})
        assert acc.id  # non-empty

    def test_from_dict_enabled_false(self):
        acc = TikTokAccount.from_dict({"enabled": False, "cookie_file": "x.txt"})
        assert acc.enabled is False

    def test_id_auto_generated(self):
        a1 = TikTokAccount(name="A", cookie_file="a.txt")
        a2 = TikTokAccount(name="B", cookie_file="b.txt")
        assert a1.id != a2.id


# ---------------------------------------------------------------------------
# TikTokAccountPool
# ---------------------------------------------------------------------------


def _make_account(name="acc", cookie_file="/tmp/c.txt", max_slots=1, enabled=True):
    return TikTokAccount(name=name, cookie_file=cookie_file, max_slots=max_slots, enabled=enabled)


class TestTikTokAccountPool:
    def test_len_empty(self):
        pool = TikTokAccountPool([])
        assert len(pool) == 0

    def test_len_with_accounts(self):
        pool = TikTokAccountPool([_make_account("a"), _make_account("b")])
        assert len(pool) == 2

    def test_acquire_yields_account(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        with pool.acquire() as got:
            assert got is acc

    def test_acquire_increments_and_decrements_load(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        with pool.acquire():
            assert pool._load[acc.id] == 1
        assert pool._load[acc.id] == 0

    def test_acquire_no_enabled_raises(self):
        acc = _make_account(enabled=False)
        pool = TikTokAccountPool([acc])
        with pytest.raises(RuntimeError, match="no enabled"):
            with pool.acquire():
                pass

    def test_acquire_no_cookie_file_raises(self):
        acc = _make_account(cookie_file="")
        pool = TikTokAccountPool([acc])
        with pytest.raises(RuntimeError, match="no enabled"):
            with pool.acquire():
                pass

    def test_acquire_picks_least_loaded(self):
        a = _make_account("a", max_slots=2)
        b = _make_account("b", max_slots=2)
        pool = TikTokAccountPool([a, b])
        # Manually mark 'a' as loaded
        pool._load[a.id] = 1
        with pool.acquire() as got:
            assert got is b

    def test_add_account(self):
        pool = TikTokAccountPool([])
        acc = _make_account()
        pool.add_account(acc)
        assert len(pool) == 1
        assert acc.id in pool._slots
        assert pool._load[acc.id] == 0

    def test_remove_account(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        pool.remove_account(acc.id)
        assert len(pool) == 0
        assert acc.id not in pool._slots
        assert acc.id not in pool._load

    def test_remove_nonexistent_noop(self):
        pool = TikTokAccountPool([])
        pool.remove_account("does-not-exist")  # must not raise

    def test_set_enabled_false(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        pool.set_enabled(acc.id, False)
        assert not acc.enabled

    def test_set_enabled_true(self):
        acc = _make_account(enabled=False)
        pool = TikTokAccountPool([acc])
        pool.set_enabled(acc.id, True)
        assert acc.enabled

    def test_set_enabled_unknown_id_noop(self):
        pool = TikTokAccountPool([])
        pool.set_enabled("ghost", True)  # must not raise

    def test_set_max_slots(self):
        acc = _make_account(max_slots=1)
        pool = TikTokAccountPool([acc])
        pool.set_max_slots(acc.id, 3)
        assert acc.max_slots == 3
        assert pool._slots[acc.id]._max == 3

    def test_set_max_slots_clamps_to_5(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        pool.set_max_slots(acc.id, 99)
        assert acc.max_slots == 5

    def test_set_max_slots_clamps_to_1(self):
        acc = _make_account(max_slots=3)
        pool = TikTokAccountPool([acc])
        pool.set_max_slots(acc.id, 0)
        assert acc.max_slots == 1

    def test_set_max_slots_unknown_id_noop(self):
        pool = TikTokAccountPool([])
        pool.set_max_slots("ghost", 2)  # must not raise

    def test_get_status(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        status = pool.get_status()
        assert len(status) == 1
        assert status[0][0] is acc
        assert status[0][1] == 0

    def test_get_status_reflects_load(self):
        acc = _make_account(max_slots=2)
        pool = TikTokAccountPool([acc])
        with pool.acquire():
            status = pool.get_status()
            assert status[0][1] == 1

    def test_to_list(self):
        acc = _make_account(name="Test", cookie_file="/tmp/t.txt")
        pool = TikTokAccountPool([acc])
        result = pool.to_list()
        assert len(result) == 1
        assert result[0]["name"] == "Test"
        assert result[0]["cookie_file"] == "/tmp/t.txt"

    def test_pick_account_returns_none_when_empty(self):
        pool = TikTokAccountPool([])
        assert pool._pick_account() is None

    def test_pick_account_returns_none_when_all_disabled(self):
        acc = _make_account(enabled=False)
        pool = TikTokAccountPool([acc])
        assert pool._pick_account() is None

    def test_acquire_slot_respects_max_slots(self):
        """acquire() must block when all slots are filled; unblocks on release."""
        acc = _make_account(max_slots=1)
        pool = TikTokAccountPool([acc])

        entered = threading.Event()
        second_acquired = threading.Event()

        def first_holder():
            with pool.acquire():
                entered.set()
                time.sleep(0.2)

        def second_waiter():
            second_acquired.wait(timeout=0.05)  # wait for first to enter
            with pool.acquire():
                second_acquired.set()

        t1 = threading.Thread(target=first_holder)
        t2 = threading.Thread(
            target=lambda: (entered.wait(), pool.acquire().__enter__() and second_acquired.set())
        )

        t1.start()
        entered.wait(timeout=1.0)
        # slot is now full — second acquire should block
        blocked = threading.Event()
        unblocked = threading.Event()

        def waiter():
            blocked.set()
            with pool.acquire():
                unblocked.set()

        t_waiter = threading.Thread(target=waiter)
        t_waiter.start()
        blocked.wait(timeout=1.0)
        assert not unblocked.wait(timeout=0.1), "Should be blocked while slot full"
        t1.join(timeout=1.0)
        assert unblocked.wait(timeout=1.0), "Should unblock after first holder exits"
        t_waiter.join(timeout=1.0)

    def test_pick_account_returns_enabled(self):
        acc = _make_account()
        pool = TikTokAccountPool([acc])
        assert pool._pick_account() is acc

    def test_pick_account_picks_least_loaded(self):
        a = _make_account("a", max_slots=2)
        b = _make_account("b", max_slots=2)
        pool = TikTokAccountPool([a, b])
        pool._load[a.id] = 1
        assert pool._pick_account() is b
