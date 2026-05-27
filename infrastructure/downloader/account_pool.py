"""
infrastructure/downloader/account_pool.py
Thread-safe TikTok account pool for parallel downloads.
Each account holds a slot counter capping concurrent downloads to max_slots.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Generator


class _AccountSlot:
    """Condition-variable-based slot counter that supports live max resize.

    Unlike threading.Semaphore, set_max() takes effect immediately:
    - Increase: waiting acquirers are woken up if new slots become available.
    - Decrease: no new acquires proceed past the new cap; in-flight workers
      finish normally (cap applies to new entrants, not existing holders).
    """

    def __init__(self, n: int) -> None:
        self._max = n
        self._active = 0
        self._cv = threading.Condition()

    def acquire(self) -> None:
        with self._cv:
            self._cv.wait_for(lambda: self._active < self._max)
            self._active += 1

    def release(self) -> None:
        with self._cv:
            self._active = max(0, self._active - 1)
            self._cv.notify_all()

    def set_max(self, n: int) -> None:
        with self._cv:
            self._max = n
            self._cv.notify_all()


@dataclass
class TikTokAccount:
    name: str
    cookie_file: str
    max_slots: int = 1
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cookie_file": self.cookie_file,
            "max_slots": self.max_slots,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(d: dict) -> "TikTokAccount":
        return TikTokAccount(
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=d.get("name", "Account"),
            cookie_file=d.get("cookie_file", ""),
            max_slots=max(1, min(5, int(d.get("max_slots", 1)))),
            enabled=bool(d.get("enabled", True)),
        )


class TikTokAccountPool:
    """
    Thread-safe pool of TikTok accounts.
    acquire() picks the least-loaded enabled account and yields it.
    The slot is released automatically when the context exits.
    """

    def __init__(self, accounts: list[TikTokAccount]) -> None:
        self._lock = threading.Lock()
        self._accounts: list[TikTokAccount] = list(accounts)
        self._slots: dict[str, _AccountSlot] = {a.id: _AccountSlot(a.max_slots) for a in accounts}
        self._load: dict[str, int] = {a.id: 0 for a in accounts}

    def __len__(self) -> int:
        with self._lock:
            return len(self._accounts)

    @contextmanager
    def acquire(self) -> Generator[TikTokAccount, None, None]:
        """Block until a slot is available on the least-loaded enabled account."""
        # Capture account + slot reference under lock to prevent KeyError race
        # with a concurrent remove_account() between pick and slot lookup.
        with self._lock:
            candidates = [a for a in self._accounts if a.enabled and a.cookie_file]
            if not candidates:
                raise RuntimeError("TikTokAccountPool: no enabled accounts with a cookie file")

            def _ratio(a: TikTokAccount) -> float:
                return self._load.get(a.id, 0) / max(1, a.max_slots)

            account = min(candidates, key=_ratio)
            slot = self._slots[account.id]

        # slot.acquire() may block — must be outside _lock to avoid deadlock.
        # Holding a local reference to slot is safe even if remove_account()
        # runs concurrently: the slot object itself remains valid.
        slot.acquire()
        with self._lock:
            self._load[account.id] = self._load.get(account.id, 0) + 1
        try:
            yield account
        finally:
            with self._lock:
                self._load[account.id] = max(0, self._load.get(account.id, 1) - 1)
            slot.release()

    def _pick_account(self) -> TikTokAccount | None:
        """Return the enabled account with the lowest load ratio, or None."""
        with self._lock:
            candidates = [a for a in self._accounts if a.enabled and a.cookie_file]
            if not candidates:
                return None

            def _ratio(a: TikTokAccount) -> float:
                return self._load.get(a.id, 0) / max(1, a.max_slots)

            return min(candidates, key=_ratio)

    def add_account(self, account: TikTokAccount) -> None:
        with self._lock:
            self._accounts.append(account)
            self._slots[account.id] = _AccountSlot(account.max_slots)
            self._load[account.id] = 0

    def remove_account(self, account_id: str) -> None:
        with self._lock:
            self._accounts = [a for a in self._accounts if a.id != account_id]
            self._slots.pop(account_id, None)
            self._load.pop(account_id, None)

    def set_enabled(self, account_id: str, enabled: bool) -> None:
        with self._lock:
            for a in self._accounts:
                if a.id == account_id:
                    a.enabled = enabled
                    break

    def set_max_slots(self, account_id: str, n: int) -> None:
        n = max(1, min(5, n))
        with self._lock:
            for a in self._accounts:
                if a.id == account_id:
                    a.max_slots = n
                    slot = self._slots.get(account_id)
                    if slot:
                        slot.set_max(n)
                    break

    def get_status(self) -> list[tuple[TikTokAccount, int]]:
        """Return [(account, current_load), ...] for UI display."""
        with self._lock:
            return [(a, self._load.get(a.id, 0)) for a in self._accounts]

    def to_list(self) -> list[dict]:
        with self._lock:
            return [a.to_dict() for a in self._accounts]
