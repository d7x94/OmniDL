"""
tests/test_history_repository_extra.py
Additional tests for infrastructure/storage/history_repository.py

Fills coverage gaps:
- get_by_id: hit, miss
- _load: corrupted JSONL lines are skipped gracefully
- _load: old JSON-array format is migrated automatically
- _load: empty file, missing file
- remove: deletes correct entry and rewrites
- add: prunes oldest when limit exceeded
- add: deduplicates by id
"""

import json
from pathlib import Path

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.storage.history_repository import HistoryRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(title="Test Video", url="https://youtube.com/watch?v=abc") -> DownloadTask:
    t = DownloadTask(url=url, format_id="best", output_ext="mp4")
    t.media_info = MediaInfo(url=url, title=title)
    t.status = DownloadStatus.COMPLETED
    return t


def write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


class TestGetById:
    def test_returns_entry_when_found(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        task = make_task()
        repo.add(task)
        result = repo.get_by_id(task.id)
        assert result is not None
        assert result["id"] == task.id

    def test_returns_none_when_not_found(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        assert repo.get_by_id("nonexistent-id") is None

    def test_returns_copy_not_reference(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        task = make_task()
        repo.add(task)
        result = repo.get_by_id(task.id)
        result["title"] = "MUTATED"
        # Internal state must not be affected
        assert repo.get_by_id(task.id)["title"] != "MUTATED"


# ---------------------------------------------------------------------------
# _load — file parsing edge cases
# ---------------------------------------------------------------------------


class TestLoad:
    def test_missing_file_loads_empty(self, tmp_path):
        repo = HistoryRepository(tmp_path / "nonexistent.jsonl")
        assert repo.all() == []

    def test_empty_file_loads_empty(self, tmp_path):
        path = tmp_path / "h.jsonl"
        path.write_text("", encoding="utf-8")
        repo = HistoryRepository(path)
        assert repo.all() == []

    def test_corrupted_lines_are_skipped(self, tmp_path):
        path = tmp_path / "h.jsonl"
        good = {"id": "1", "title": "Good", "url": "https://example.com"}
        good2 = {"id": "2", "title": "Also Good", "url": "https://example.com"}
        path.write_text(
            json.dumps(good) + "\n" + "NOT VALID JSON {{{\n" + json.dumps(good2) + "\n",
            encoding="utf-8",
        )
        repo = HistoryRepository(path)
        ids = [e["id"] for e in repo.all()]
        assert "1" in ids
        assert "2" in ids
        assert len(ids) == 2  # corrupted line skipped

    def test_old_json_array_format_is_migrated(self, tmp_path):
        path = tmp_path / "h.jsonl"
        old_data = [
            {"id": "a1", "title": "Old Video 1", "url": "https://example.com/1"},
            {"id": "a2", "title": "Old Video 2", "url": "https://example.com/2"},
        ]
        path.write_text(json.dumps(old_data), encoding="utf-8")
        repo = HistoryRepository(path)
        ids = [e["id"] for e in repo.all()]
        assert "a1" in ids
        assert "a2" in ids
        # File should now be JSONL (not starting with '[')
        assert not path.read_text(encoding="utf-8").startswith("[")

    def test_load_trims_and_rewrites_when_over_limit(self, tmp_path):
        path = tmp_path / "h.jsonl"
        entries = [{"id": str(i), "title": f"t{i}", "url": "https://example.com"} for i in range(5)]
        write_jsonl(path, entries)
        repo = HistoryRepository(path, limit=2)
        assert len(repo.all()) == 2
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 2


class TestRewriteFailureRecovery:
    def test_restores_from_backup_when_tmp_replace_fails(self, tmp_path, monkeypatch):
        path = tmp_path / "h.jsonl"
        path.write_text(json.dumps({"id": "1"}) + "\n", encoding="utf-8")
        repo = HistoryRepository(path)

        orig_replace = Path.replace

        def failing_replace(self, target):
            if self.name.endswith(".tmp.jsonl"):
                raise OSError("simulated disk failure")
            return orig_replace(self, target)

        monkeypatch.setattr(Path, "replace", failing_replace)

        repo._rewrite_unlocked([{"id": "1"}, {"id": "2"}])

        # tmp.replace(self._path) failed, so the original was restored from
        # the backup created just before the failed step.
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["id"] == "1"

    def test_logs_when_backup_restore_also_fails(self, tmp_path, monkeypatch):
        path = tmp_path / "h.jsonl"
        path.write_text(json.dumps({"id": "1"}) + "\n", encoding="utf-8")
        repo = HistoryRepository(path)

        orig_replace = Path.replace

        def failing_replace(self, target):
            if self.name == path.name:
                # Allow the very first move (original -> backup) to succeed.
                return orig_replace(self, target)
            raise OSError("simulated disk failure")

        monkeypatch.setattr(Path, "replace", failing_replace)

        # Must not raise -- both the primary write and the backup restore
        # fail, and the failure is only logged.
        repo._rewrite_unlocked([{"id": "1"}, {"id": "2"}])


# ---------------------------------------------------------------------------
# add — limit and deduplication
# ---------------------------------------------------------------------------


class TestAdd:
    def test_prunes_oldest_when_limit_exceeded(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl", limit=3)
        tasks = [make_task(title=f"Video {i}") for i in range(4)]
        for t in tasks:
            repo.add(t)
        entries = repo.all()
        assert len(entries) == 3
        # Oldest (tasks[0]) should have been pruned
        ids = [e["id"] for e in entries]
        assert tasks[0].id not in ids

    def test_deduplicates_by_id(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        task = make_task(title="Original")
        repo.add(task)
        # Re-add the same task (simulates re-queue)
        task.status = DownloadStatus.COMPLETED
        repo.add(task)
        entries = repo.all()
        matching = [e for e in entries if e["id"] == task.id]
        assert len(matching) == 1  # no duplicate


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_deletes_entry(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        task = make_task()
        repo.add(task)
        repo.remove(task.id)
        assert repo.get_by_id(task.id) is None

    def test_remove_persists_to_disk(self, tmp_path):
        path = tmp_path / "h.jsonl"
        repo = HistoryRepository(path)
        task = make_task()
        repo.add(task)
        repo.remove(task.id)
        # Reload from disk
        repo2 = HistoryRepository(path)
        assert repo2.get_by_id(task.id) is None

    def test_remove_nonexistent_id_does_not_raise(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        repo.remove("does-not-exist")  # should not raise


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestUpdateFilename:
    def test_updates_existing_entry(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        task = make_task()
        repo.add(task)
        assert repo.update_filename(task.id, "/new/path.mp4") is True
        assert repo.get_by_id(task.id)["filename"] == "/new/path.mp4"

    def test_persists_to_disk(self, tmp_path):
        path = tmp_path / "h.jsonl"
        repo = HistoryRepository(path)
        task = make_task()
        repo.add(task)
        repo.update_filename(task.id, "/new/path.mp4")
        repo2 = HistoryRepository(path)
        assert repo2.get_by_id(task.id)["filename"] == "/new/path.mp4"

    def test_returns_false_when_not_found(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        assert repo.update_filename("does-not-exist", "/x.mp4") is False


class TestSearch:
    def test_search_by_title(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        repo.add(make_task(title="Rick Astley Never Gonna"))
        repo.add(make_task(title="Coding Tutorial Python"))
        results = repo.search("rick")
        assert len(results) == 1
        assert "Rick" in results[0]["title"]

    def test_search_case_insensitive(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        repo.add(make_task(title="PYTHON TUTORIAL"))
        assert len(repo.search("python")) == 1
        assert len(repo.search("PYTHON")) == 1

    def test_search_no_match_returns_empty(self, tmp_path):
        repo = HistoryRepository(tmp_path / "h.jsonl")
        repo.add(make_task(title="Something"))
        assert repo.search("zzznomatch") == []
