"""
infrastructure/downloader/account_pool.py
Thread-safe TikTok account pool for parallel downloads.
Each account holds a slot counter capping concurrent downloads to max_slots.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Generator

logger = logging.getLogger(__name__)

# Cookies TikTok sets only for a signed-in session.  A jar without one of these
# is an anonymous visitor jar: yt-dlp accepts it happily and then downloads as a
# logged-out guest, which is exactly the silent failure the pool exists to
# avoid.  sessionid is the primary; sid_tt/sessionid_ss are its aliases.
_TIKTOK_AUTH_COOKIES = ("sessionid", "sessionid_ss", "sid_tt")


class _AcquireAborted(Exception):
    """The waiter gave up on a slot (task cancelled) before one became free."""


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

    def acquire(self, timeout: "float | None" = None) -> bool:
        """Take a slot. Returns False if *timeout* elapsed without one."""
        with self._cv:
            if not self._cv.wait_for(lambda: self._active < self._max, timeout=timeout):
                return False
            self._active += 1
            return True

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
    # Where the cookies came from, so "Refresh" can re-extract in place
    # instead of making the user delete and re-add the account.
    browser: str = ""
    profile: str = ""
    # Truncated SHA-256 of the session cookie — identifies the TikTok account
    # behind the file so the same login cannot be added to the pool twice.
    session_fp: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cookie_file": self.cookie_file,
            "max_slots": self.max_slots,
            "enabled": self.enabled,
            "browser": self.browser,
            "profile": self.profile,
            "session_fp": self.session_fp,
        }

    @staticmethod
    def from_dict(d: dict) -> "TikTokAccount":
        return TikTokAccount(
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=d.get("name", "Account"),
            cookie_file=d.get("cookie_file", ""),
            max_slots=max(1, min(5, int(d.get("max_slots", 1)))),
            enabled=bool(d.get("enabled", True)),
            browser=str(d.get("browser", "")),
            profile=str(d.get("profile", "")),
            session_fp=str(d.get("session_fp", "")),
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

    def has_usable_account(self) -> bool:
        """True when at least one account is enabled *and* has a cookie file.

        len(pool) counts paused accounts too, so callers that used it as the
        "can this pool serve a download?" gate sent every task into acquire(),
        which then raised RuntimeError and surfaced as a failed download.
        """
        with self._lock:
            return any(a.enabled and a.cookie_file for a in self._accounts)

    @contextmanager
    def acquire(
        self, should_abort: "Callable[[], bool] | None" = None
    ) -> Generator[TikTokAccount, None, None]:
        """Block until a slot is available on the least-loaded enabled account.

        Raises _AcquireAborted if *should_abort* turns True while waiting.
        """
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
            # Pre-reserve the slot in _load before releasing the lock so that
            # concurrent acquire() calls see this account as loaded and pick a
            # different one.  Without this, all threads see load=0 and pile onto
            # account A, blocking on slot.acquire() while B and C sit idle.
            self._load[account.id] = self._load.get(account.id, 0) + 1

        # slot.acquire() may block — must be outside _lock to avoid deadlock.
        # Holding a local reference to slot is safe even if remove_account()
        # runs concurrently: the slot object itself remains valid.
        #
        # A live recording holds its slot for the whole broadcast (hours), so
        # everything queued behind it waits that long. Poll in 1 s steps and let
        # should_abort() bail out, otherwise a cancelled task stays parked in
        # wait_for() forever and burns a DownloadManager worker thread for the
        # life of the process.
        while not slot.acquire(timeout=1.0):
            if should_abort is not None and should_abort():
                with self._lock:
                    self._load[account.id] = max(0, self._load.get(account.id, 1) - 1)
                raise _AcquireAborted
        try:
            yield account
        finally:
            with self._lock:
                if account.id in self._slots:
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

    def adopt_state(self, other: "TikTokAccountPool") -> None:
        """Carry live slot counters over from a previous pool instance.

        The pool is rebuilt from config on every pause/rename/slot change.
        Without this, the new instance starts every account at load 0 while
        in-flight downloads still hold slots on the old one, so an account
        already saturated could be handed out again past its max_slots.
        Re-using the same _AccountSlot object also keeps the release() that
        the running download will issue against the old pool meaningful.
        """
        with other._lock:
            old_slots = dict(other._slots)
            old_load = dict(other._load)
        with self._lock:
            for a in self._accounts:
                slot = old_slots.get(a.id)
                if slot is None:
                    continue
                slot.set_max(a.max_slots)
                self._slots[a.id] = slot
                self._load[a.id] = old_load.get(a.id, 0)

    def get_status(self) -> list[tuple[TikTokAccount, int]]:
        """Return [(account, current_load), ...] for UI display."""
        with self._lock:
            return [(a, self._load.get(a.id, 0)) for a in self._accounts]

    def to_list(self) -> list[dict]:
        with self._lock:
            return [a.to_dict() for a in self._accounts]


# ── Cookie health -----------------------------------------------------------


@dataclass(frozen=True)
class CookieHealth:
    """What a TikTok cookie file actually contains.

    status is one of:
      "ok"             — signed in, session not expired
      "missing"        — the file is gone from disk
      "unreadable"     — present but cannot be parsed / decrypted
      "not_logged_in"  — parsed fine but carries no TikTok session cookie
      "expired"        — carries a session cookie whose expiry is in the past
    """

    status: str
    fingerprint: str = ""
    expires_at: int = 0
    count: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _resolve_cookie_file(cookie_file: str) -> "Path | None":
    """Return the cookie file that actually exists on disk, or None.

    encrypt_cookie_file() renames .txt -> .enc after the path has already been
    written to config (the startup migration does this to legacy plaintext
    jars).  The download engine follows that rename; the health check did not,
    so a perfectly working account showed up as "missing" in Settings.
    """
    if not cookie_file:
        return None
    path = Path(cookie_file)
    if path.is_file():
        return path
    if path.suffix == ".txt":
        enc = path.with_suffix(".enc")
        if enc.is_file():
            return enc
    return None


def _read_cookie_text(path: Path) -> str:
    """Return the plaintext Netscape body of *path*, decrypting .enc if needed."""
    from infrastructure.downloader.cookie_storage import decrypt_to_tempfile

    tmp = decrypt_to_tempfile(path)
    try:
        return tmp.read_text(encoding="utf-8", errors="replace")
    finally:
        if tmp != path:
            tmp.unlink(missing_ok=True)


def inspect_tiktok_cookie(cookie_file: str) -> CookieHealth:
    """Inspect a saved TikTok cookie file without exposing its secrets.

    The returned fingerprint is a truncated SHA-256 of the session cookie
    value — enough to tell two accounts apart (and to spot the same account
    added twice), never enough to reconstruct the session.
    """
    path = _resolve_cookie_file(cookie_file)
    if path is None:
        return CookieHealth("missing")

    try:
        body = _read_cookie_text(path)
    except (OSError, RuntimeError) as exc:
        logger.warning("inspect_tiktok_cookie: cannot read %s — %s", path.name, exc)
        return CookieHealth("unreadable")

    count = 0
    session_value = ""
    session_expiry = 0
    # A header-only jar parses fine — it just holds no cookies.  Reporting it
    # as "unreadable" pointed the user at the wrong fix (re-encrypt) instead of
    # the right one (log in to TikTok in that browser profile first).
    is_netscape = "Netscape HTTP Cookie File" in body[:200]
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        count += 1
        name, value = parts[5], parts[6]
        if name in _TIKTOK_AUTH_COOKIES and value and not session_value:
            session_value = value
            try:
                session_expiry = int(float(parts[4]))
            except ValueError:
                session_expiry = 0

    if not count:
        return CookieHealth("not_logged_in" if is_netscape else "unreadable")
    if not session_value:
        return CookieHealth("not_logged_in", count=count)

    fingerprint = hashlib.sha256(session_value.encode("utf-8")).hexdigest()[:16]
    # expiry 0 means "session cookie" in Netscape format — no expiry to check.
    if session_expiry and session_expiry < int(time.time()):
        return CookieHealth("expired", fingerprint=fingerprint, expires_at=session_expiry, count=count)
    return CookieHealth("ok", fingerprint=fingerprint, expires_at=session_expiry, count=count)
