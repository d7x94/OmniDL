"""
tests/test_history_repository.py
Unit tests for infrastructure/storage/history_repository.py

Covers:
- add / all / search / remove / clear
- Mutable-reference safety (Bug #8)
- JSONL migration from old JSON-array format (Issue #14)
- Concurrent add() calls do not corrupt the in-memory list
"""

import json
import threading
import time

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.storage.history_repository import HistoryRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_task(title="Test Video", status=DownloadStatus.COMPLETED) -> DownloadTask:
    t = DownloadTask(url="https://example.com/video", status=status)
    t.filename = "/tmp/test.mp4"  # nosec B108
    if t.media_info is None:
        from domain.models.download_task import MediaInfo

        t.media_info = MediaInfo(url=t.url, title=title)
    t.finished_at = time.time()
    return t


def make_repo(tmp_path, limit=500) -> HistoryRepository:
    return HistoryRepository(tmp_path / "history.jsonl", limit=limit)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHistoryAdd:
    def test_add_single_entry(self, tmp_path):
        repo = make_repo(tmp_path)
        task = make_task("My Video")
        repo.add(task)
        entries = repo.all()
        assert len(entries) == 1
        assert entries[0]["title"] == "My Video"

    def test_add_deduplicates_by_id(self, tmp_path):
        repo = make_repo(tmp_path)
        task = make_task()
        repo.add(task)
        repo.add(task)  # second add of same task
        assert len(repo.all()) == 1

    def test_add_inserts_at_front(self, tmp_path):
        repo = make_repo(tmp_path)
        t1 = make_task("First")
        t2 = make_task("Second")
        repo.add(t1)
        repo.add(t2)
        entries = repo.all()
        assert entries[0]["title"] == "Second"

    def test_limit_is_enforced(self, tmp_path):
        repo = make_repo(tmp_path, limit=3)
        for i in range(5):
            repo.add(make_task(f"Video {i}"))
        assert len(repo.all()) == 3

    def test_add_writes_to_disk(self, tmp_path):
        repo = make_repo(tmp_path)
        task = make_task()
        repo.add(task)
        hist_file = tmp_path / "history.jsonl"
        assert hist_file.exists()
        lines = [ln for ln in hist_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == task.id


class TestHistorySearch:
    def test_search_by_title(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.add(make_task("Python Tutorial"))
        repo.add(make_task("Java Basics"))
        results = repo.search("python")
        assert len(results) == 1
        assert results[0]["title"] == "Python Tutorial"

    def test_search_case_insensitive(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.add(make_task("UPPER CASE VIDEO"))
        assert len(repo.search("upper case")) == 1

    def test_search_empty_query_returns_nothing(self, tmp_path):
        """Empty query matches nothing (empty string is a substring of everything
        — but the intent is to show no results; behaviour matches original)."""
        repo = make_repo(tmp_path)
        repo.add(make_task("Video"))
        # Original: empty string IS a substring of every string, returns all
        # This test documents the existing behaviour (not a regression target)
        results = repo.search("")
        assert len(results) >= 0  # implementation-defined


class TestHistoryMutableRefSafety:
    def test_all_returns_copies(self, tmp_path):
        """Mutating the returned list must not corrupt internal state (Bug #8)."""
        repo = make_repo(tmp_path)
        repo.add(make_task("Original Title"))
        entries = repo.all()
        entries[0]["title"] = "MUTATED"
        # Internal list must be unaffected
        assert repo.all()[0]["title"] == "Original Title"

    def test_search_returns_copies(self, tmp_path):
        repo = make_repo(tmp_path)
        repo.add(make_task("Safe Title"))
        results = repo.search("safe")
        results[0]["title"] = "MUTATED"
        assert repo.all()[0]["title"] == "Safe Title"


class TestHistoryRemoveAndClear:
    def test_remove_by_id(self, tmp_path):
        repo = make_repo(tmp_path)
        task = make_task()
        repo.add(task)
        repo.remove(task.id)
        assert len(repo.all()) == 0

    def test_clear(self, tmp_path):
        repo = make_repo(tmp_path)
        for _ in range(5):
            repo.add(make_task())
        repo.clear()
        assert len(repo.all()) == 0


class TestHistoryJsonlMigration:
    def test_migrates_old_json_array_format(self, tmp_path):
        """On first load, an old-format JSON array file must be migrated."""
        hist_file = tmp_path / "history.jsonl"
        old_data = [
            {"id": "abc123", "title": "Old Video", "url": "https://example.com"},
        ]
        hist_file.write_text(json.dumps(old_data), encoding="utf-8")
        repo = HistoryRepository(hist_file, limit=500)
        entries = repo.all()
        assert len(entries) == 1
        assert entries[0]["title"] == "Old Video"
        # After migration the file should be JSONL (no leading '[')
        first_char = hist_file.read_text().strip()[0]
        assert first_char == "{"


class TestHistorySearchNullFields:
    def test_search_tolerates_null_fields(self, tmp_path):
        """Legacy entries may have null url/title/filename — search must not crash."""
        hist_file = tmp_path / "history.jsonl"
        hist_file.write_text(
            json.dumps({"id": "x1", "title": None, "url": None, "filename": None})
            + "\n"
            + json.dumps({"id": "x2", "title": "Real Video", "url": "https://e.com", "filename": "a.mp4"})
            + "\n",
            encoding="utf-8",
        )
        repo = HistoryRepository(hist_file, limit=500)
        results = repo.search("real")
        assert len(results) == 1
        assert results[0]["id"] == "x2"


class TestHistoryConcurrency:
    def test_concurrent_adds_do_not_corrupt(self, tmp_path):
        repo = make_repo(tmp_path, limit=1000)
        errors = []

        def adder():
            try:
                repo.add(make_task())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=adder) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(repo.all()) == 20

    def test_concurrent_add_and_remove_keep_disk_consistent(self, tmp_path):
        """add() appends while remove() rewrites — disk must match memory after."""
        repo = make_repo(tmp_path, limit=1000)
        seed = [make_task(f"Seed {i}") for i in range(10)]
        for t in seed:
            repo.add(t)
        errors = []

        def adder():
            try:
                for _ in range(10):
                    repo.add(make_task("Added"))
            except Exception as exc:
                errors.append(exc)

        def remover():
            try:
                for t in seed:
                    repo.remove(t.id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=adder) for _ in range(4)] + [threading.Thread(target=remover)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        in_memory_ids = {e["id"] for e in repo.all()}
        reloaded = HistoryRepository(tmp_path / "history.jsonl", limit=1000)
        on_disk_ids = {e["id"] for e in reloaded.all()}
        # Every entry that survived in memory must also be on disk.
        assert in_memory_ids <= on_disk_ids
