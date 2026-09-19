"""
infrastructure/storage/history_repository.py
Append-only JSON-Lines history store with in-memory search.

Design rationale: the original implementation serialised ALL history entries
(~215 KB for 500 entries) to a JSON array on every single download completion.
With max_concurrent=8, up to 8 near-simultaneous completions caused sequential
lock contention and blocked any concurrent get_history() call.

Current approach:
- add()        → O(1) single-line JSONL append (no full rewrite)
- all/search   → served from in-memory list (no disk read)
- remove/clear → rare; full rewrite is acceptable at this frequency
- On startup   : reads the JSONL file line-by-line; migrates old JSON array
  is detected it migrates automatically (one-time).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from domain.models.download_task import DownloadTask

logger = logging.getLogger(__name__)


class HistoryRepository:
    """
    Persists completed/failed/cancelled DownloadTasks to disk.
    Keeps at most *limit* entries (oldest pruned first).
    """

    def __init__(self, history_path: Path, limit: int = 500) -> None:
        self._path = history_path
        self._limit = limit
        self._lock = threading.Lock()
        # Serializes disk writers (append vs rewrite) without blocking
        # all()/search() readers, which only need self._lock.
        self._io_lock = threading.Lock()
        self._entries: list[dict] = []
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
            if not raw:
                return
            # Migration: if the file starts with '[' it is the old JSON-array
            # format.  Parse it once and rewrite as JSONL.
            if raw.startswith("["):
                logger.info("Migrating history from JSON array to JSONL format")
                data = json.loads(raw)
                if isinstance(data, list):
                    self._entries = data
                self._rewrite()  # convert on disk immediately
                return
            # Normal JSONL: one JSON object per line
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    try:
                        self._entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed history line: %s", line[:80])
            # add() keeps self._entries newest-first, but _append_line() writes
            # each new record to the END of the file, so reading the JSONL back
            # top-to-bottom yields OLDEST-first. Two consequences before this
            # sort existed: the History tab listed the oldest downloads at the
            # top after every restart, and the over-limit trim below (which
            # keeps the FIRST _limit entries) permanently erased the NEWEST
            # records from disk. Sorting on finished_at restores newest-first
            # regardless of how the file was written, so files produced by
            # older builds heal themselves on the next launch.
            self._entries.sort(key=lambda e: e.get("finished_at") or 0, reverse=True)
            logger.debug("History loaded: %d entries", len(self._entries))
        except Exception as exc:
            logger.warning("History load failed (%s)", exc)
        finally:
            # add() trims to the limit, but nothing else does — a file that
            # already exceeds the limit (older build, lowered history_limit,
            # crash between append and rewrite) would otherwise stay fully
            # resident for the whole session.
            if len(self._entries) > self._limit:
                logger.info(
                    "History file has %d entries, trimming to limit %d",
                    len(self._entries),
                    self._limit,
                )
                self._entries = self._entries[: self._limit]
                self._rewrite()

    def _append_line(self, entry: dict) -> None:
        """O(1) disk append — the hot path for add(). Caller holds _io_lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("History append failed: %s", exc)

    def _rewrite(self) -> None:
        """Full JSONL rewrite — prunes disk to match in-memory list.

        A backup copy is written first so data is not lost if the write fails
        mid-way (power loss, full disk, etc.).  The backup is removed on
        success.

        Callers (``remove``, ``clear``, and ``_load`` migration) must ensure
        they are **not** holding ``self._lock`` when calling this — it
        delegates to ``_rewrite_unlocked`` which performs disk I/O.
        """
        self._rewrite_unlocked(self._entries)

    def _rewrite_unlocked(self, entries: list[dict]) -> None:
        """Write *entries* atomically to disk without requiring ``self._lock``.

        Accepts a consistent snapshot so the caller can release the lock
        before performing the slow disk write, keeping ``all()`` and
        ``search()`` responsive for the UI's 500 ms polling loop.
        Caller holds ``_io_lock`` (except the single-threaded ``_load`` path).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        backup = self._path.with_suffix(".backup.jsonl")
        try:
            # Write to a temp file then atomically replace the original.
            tmp = self._path.with_suffix(".tmp.jsonl")
            with tmp.open("w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # Keep a backup until the rename succeeds.
            if self._path.exists():
                self._path.replace(backup)
            tmp.replace(self._path)
            # Remove backup only after successful replacement.
            if backup.exists():
                backup.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("History rewrite failed: %s", exc)
            # Restore from backup if available.
            if backup.exists() and not self._path.exists():
                try:
                    backup.replace(self._path)
                    logger.info("History restored from backup after failed rewrite")
                except OSError as rb_exc:
                    logger.error("History backup restore failed: %s", rb_exc)

    # ── Public API ────────────────────────────────────────────────────────

    def add(self, task: DownloadTask) -> None:
        entry = task.to_dict()
        # _io_lock spans the whole operation so concurrent writers (add vs
        # remove/clear) cannot interleave a stale snapshot rewrite over a
        # fresh append. The disk write happens BEFORE self._entries is
        # updated: readers (all/search) only need self._lock, so an entry
        # must never become visible in memory before it is durable on disk —
        # otherwise a reader could observe it, then a fresh HistoryRepository
        # opened on the same file (e.g. after a restart) would not find it.
        with self._io_lock:
            with self._lock:
                new_entries = [e for e in self._entries if e.get("id") != task.id]
            new_entries.insert(0, entry)
            # Check overflow BEFORE pruning so we know whether the disk file
            # also needs to be truncated (CWE-400: uncontrolled resource growth).
            # Calling _append_line() unconditionally would let the JSONL file
            # grow without bound; on restart _load() would read every line back
            # into memory, silently bypassing the configured limit.
            overflow = len(new_entries) > self._limit
            if overflow:
                new_entries = new_entries[: self._limit]
                self._rewrite_unlocked(new_entries)
            else:
                # Fast O(1) path: no pruning needed, just append one line.
                self._append_line(entry)
            with self._lock:
                self._entries = new_entries

    def all(self) -> list[dict]:
        with self._lock:
            # Return shallow copies so callers cannot mutate the internal store.
            return [dict(e) for e in self._entries]

    def search(self, query: str) -> list[dict]:
        """Case-insensitive substring search across url / title / filename."""
        q = query.lower()
        with self._lock:
            # Return copies — returning references into self._entries would
            # allow callers to silently mutate the internal store.
            return [
                dict(e)
                for e in self._entries
                if q in (e.get("url") or "").lower()
                or q in (e.get("title") or "").lower()
                or q in (e.get("filename") or "").lower()
            ]

    def remove(self, task_id: str) -> None:
        with self._io_lock:
            with self._lock:
                self._entries = [e for e in self._entries if e.get("id") != task_id]
                snapshot = list(self._entries)
            self._rewrite_unlocked(snapshot)

    def clear(self) -> None:
        with self._io_lock:
            with self._lock:
                self._entries = []
            self._rewrite_unlocked([])

    def update_filename(self, task_id: str, new_filename: str) -> bool:
        """Patch the ``filename`` field of one entry. Returns False if not found."""
        with self._io_lock:
            with self._lock:
                found = False
                for e in self._entries:
                    if e.get("id") == task_id:
                        e["filename"] = new_filename
                        found = True
                        break
                if not found:
                    return False
                snapshot = list(self._entries)
            self._rewrite_unlocked(snapshot)
            return True

    def get_by_id(self, task_id: str) -> Optional[dict]:
        with self._lock:
            for e in self._entries:
                if e.get("id") == task_id:
                    return dict(e)
        return None
