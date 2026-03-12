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
                self._rewrite()   # convert on disk immediately
                return
            # Normal JSONL: one JSON object per line
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    try:
                        self._entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed history line: %s", line[:80])
            logger.debug("History loaded: %d entries", len(self._entries))
        except Exception as exc:
            logger.warning("History load failed (%s)", exc)

    def _append_line(self, entry: dict) -> None:
        """O(1) disk append — the hot path for add()."""
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
        with self._lock:
            entry = task.to_dict()
                            
            # Remove duplicate by id if re-queued
            self._entries = [e for e in self._entries if e.get("id") != task.id]
            self._entries.insert(0, entry)
            # Check overflow BEFORE pruning so we know whether the disk file
            # also needs to be truncated (CWE-400: uncontrolled resource growth).
            # Calling _append_line() unconditionally would let the JSONL file
            # grow without bound; on restart _load() would read every line back
            # into memory, silently bypassing the configured limit.
            overflow = len(self._entries) > self._limit
            if overflow:
                self._entries = self._entries[: self._limit]
                # Take a snapshot for the disk rewrite — the lock is released
                # immediately after so that concurrent all()/search() calls are
                # not blocked during the (slow) full-file write.
                entries_snapshot: list[dict] = list(self._entries)
            else:
                entries_snapshot = []
        # ── Lock released — perform disk I/O outside the critical section ──
        if overflow:
            self._rewrite_unlocked(entries_snapshot)
        else:
            # Fast O(1) path: no pruning needed, just append one line.
            self._append_line(entry)

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
                dict(e) for e in self._entries
                if q in e.get("url", "").lower()
                or q in e.get("title", "").lower()
                or q in e.get("filename", "").lower()
            ]

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._entries = [e for e in self._entries if e.get("id") != task_id]
            snapshot = list(self._entries)
        self._rewrite_unlocked(snapshot)

    def clear(self) -> None:
        with self._lock:
            self._entries = []
        self._rewrite_unlocked([])

    def get_by_id(self, task_id: str) -> Optional[dict]:
        with self._lock:
            for e in self._entries:
                if e.get("id") == task_id:
                    return dict(e)
        return None
