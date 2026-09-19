"""
tests/test_api_files_tab_audit.py
Regression tests for the Files-tab audit fixes in api/server.py.

FIX-FB-1  GET /api/files/browse 404'd when download_dir did not exist yet
          (fresh install / unmounted drive), leaving the whole tab dead.
          The root is now created on demand, mirroring
          DownloadService.start_download().
FIX-FB-2  Entries whose stat() fails (broken symlinks, files racing a delete)
          were silently dropped from the listing, so the user could neither
          see nor remove them.  lstat() is now the fallback.
FIX-FB-3  DELETE /api/files/delete left task.filename / the history row on a
          path that no longer existed.  It now calls
          DownloadService.clear_file_record(), the delete-side counterpart to
          rename_download().
FIX-FB-4  POST /api/files/rename silently dropped the extension when the new
          name carried none ("clip.mp4" -> "My Clip"), producing a file the
          convert allowlist rejects and /api/files/serve types as
          octet-stream.
FIX-FB-5  A symlink inside download_dir could never be deleted: resolve()
          followed it to its target, which then failed the confinement check.
          Deletion now addresses the link itself and never follows it.

Endpoint functions are invoked directly (the test_api_archive.py convention) —
this repo has no httpx dependency installed.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.server as srv
from api.models import FileDeleteRequest, FileRenameByPathRequest


class _FakeService:
    """Minimal DownloadService stand-in with the real clear_file_record contract."""

    def __init__(self) -> None:
        self.tasks: list = []
        self.history: list[dict] = []
        self.cleared_with: list[Path] = []

    def get_all_tasks(self) -> list:
        return self.tasks

    def get_history(self) -> list[dict]:
        return self.history

    def clear_file_record(self, deleted_path: Path) -> int:
        self.cleared_with.append(deleted_path)
        count = 0
        for task in self.tasks:
            raw = getattr(task, "filename", "") or ""
            if raw and _at_or_under(raw, deleted_path):
                task.filename = ""
                count += 1
        for entry in self.history:
            raw = entry.get("filename") or ""
            if raw and _at_or_under(raw, deleted_path):
                entry["filename"] = ""
                count += 1
        return count


def _at_or_under(raw: str, root: Path) -> bool:
    resolved = Path(raw).resolve()
    return resolved == root or root in resolved.parents


def _make_app(download_dir: Path, service: _FakeService | None = None):
    service = service or _FakeService()
    config = SimpleNamespace(
        api_token="",
        download_dir=download_dir,
        taildrop_target_nodes=[],
        taildrop_enabled=False,
    )
    return srv.create_app(service, config), service  # type: ignore[arg-type]


def _endpoint(app, path: str, method: str | None = None):
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        if method is None or method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method or ''} {path} not found")


def _call(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    return asyncio.run(result) if inspect.iscoroutine(result) else result


def _status(fn, *args, **kwargs) -> int:
    with pytest.raises(Exception) as exc_info:
        _call(fn, *args, **kwargs)
    return exc_info.value.status_code  # type: ignore[attr-defined]


# ── FIX-FB-1 ────────────────────────────────────────────────────────────────


class TestBrowseCreatesRootOnDemand:
    def test_missing_download_dir_is_created_not_404(self, tmp_path: Path) -> None:
        missing = tmp_path / "fresh" / "OmniDL"
        app, _ = _make_app(missing)

        response = _call(_endpoint(app, "/api/files/browse"), None, None)

        assert missing.is_dir()
        assert response.current_path == str(missing.resolve())
        assert response.items == []
        assert response.parent_path is None

    def test_missing_subdirectory_still_404s(self, tmp_path: Path) -> None:
        """Only the root is auto-created; a bogus subpath must stay a 404."""
        root = tmp_path / "dl"
        app, _ = _make_app(root)

        assert _status(_endpoint(app, "/api/files/browse"), str(root / "nope"), None) == 404

    def test_existing_root_is_not_disturbed(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        (root / "keep.mp4").write_bytes(b"x")
        app, _ = _make_app(root)

        response = _call(_endpoint(app, "/api/files/browse"), None, None)

        assert [i.name for i in response.items] == ["keep.mp4"]


# ── FIX-FB-2 ────────────────────────────────────────────────────────────────


class TestBrowseListsUnstatableEntries:
    def test_broken_symlink_is_listed_as_a_file(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        (root / "dangling").symlink_to(tmp_path / "does_not_exist")
        app, _ = _make_app(root)

        response = _call(_endpoint(app, "/api/files/browse"), None, None)
        names = {i.name: i for i in response.items}

        assert "dangling" in names, "a broken symlink must stay visible and removable"
        assert names["dangling"].type == "file"

    def test_directories_still_sort_before_files(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        (root / "aaa.mp4").write_bytes(b"x")
        (root / "zzz_folder").mkdir()
        (root / "dangling").symlink_to(tmp_path / "gone")
        app, _ = _make_app(root)

        response = _call(_endpoint(app, "/api/files/browse"), None, None)

        assert [i.type for i in response.items] == ["dir", "file", "file"]

    def test_real_entries_keep_their_real_size(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        (root / "clip.mp4").write_bytes(b"x" * 7)
        (root / "folder").mkdir()
        app, _ = _make_app(root)

        items = {i.name: i for i in _call(_endpoint(app, "/api/files/browse"), None, None).items}

        assert items["clip.mp4"].size == 7
        assert items["folder"].size is None


# ── FIX-FB-3 ────────────────────────────────────────────────────────────────


class TestDeleteSyncsDownloadRecords:
    def test_deleting_a_file_clears_the_owning_task(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        victim = root / "clip.mp4"
        victim.write_bytes(b"x")
        service = _FakeService()
        service.tasks = [SimpleNamespace(id="T1", filename=str(victim))]
        app, _ = _make_app(root, service)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(victim)), None)

        assert service.tasks[0].filename == ""

    def test_deleting_a_directory_clears_records_underneath(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        folder = root / "album"
        folder.mkdir()
        inner = folder / "a.mp4"
        inner.write_bytes(b"x")
        service = _FakeService()
        service.history = [{"id": "H1", "filename": str(inner)}]
        app, _ = _make_app(root, service)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(folder)), None)

        assert service.history[0]["filename"] == ""

    def test_unrelated_records_are_untouched(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        victim = root / "clip.mp4"
        victim.write_bytes(b"x")
        keeper = root / "other.mp4"
        keeper.write_bytes(b"y")
        service = _FakeService()
        service.tasks = [SimpleNamespace(id="T1", filename=str(keeper))]
        app, _ = _make_app(root, service)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(victim)), None)

        assert service.tasks[0].filename == str(keeper)

    def test_response_reports_the_resolved_path(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        victim = root / "clip.mp4"
        victim.write_bytes(b"x")
        app, _ = _make_app(root)

        response = _call(
            _endpoint(app, "/api/files/delete", "DELETE"),
            FileDeleteRequest(path=str(root / "." / "clip.mp4")),
            None,
        )

        assert response.path == str(victim.resolve())


# ── FIX-FB-4 ────────────────────────────────────────────────────────────────


class TestRenamePreservesExtension:
    def test_extensionless_new_name_keeps_the_original_suffix(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        clip = root / "clip.mp4"
        clip.write_bytes(b"x")
        app, _ = _make_app(root)

        response = _call(
            _endpoint(app, "/api/files/rename"),
            FileRenameByPathRequest(path=str(clip), new_name="Phim cua toi"),
            None,
        )

        assert Path(response.path).name == "Phim cua toi.mp4"
        assert (root / "Phim cua toi.mp4").exists()

    def test_explicit_extension_is_respected(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        clip = root / "clip.mp4"
        clip.write_bytes(b"x")
        app, _ = _make_app(root)

        response = _call(
            _endpoint(app, "/api/files/rename"),
            FileRenameByPathRequest(path=str(clip), new_name="out.mkv"),
            None,
        )

        assert Path(response.path).name == "out.mkv"

    def test_name_sanitised_to_nothing_still_keeps_the_suffix(self, tmp_path: Path) -> None:
        """sanitise_filename('   ') collapses to 'download' — it must stay playable."""
        root = tmp_path / "dl"
        root.mkdir()
        clip = root / "clip.mp4"
        clip.write_bytes(b"x")
        app, _ = _make_app(root)

        response = _call(
            _endpoint(app, "/api/files/rename"),
            FileRenameByPathRequest(path=str(clip), new_name="   "),
            None,
        )

        assert Path(response.path).name == "download.mp4"

    def test_extensionless_source_is_left_alone(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        blob = root / "README"
        blob.write_text("x")
        app, _ = _make_app(root)

        response = _call(
            _endpoint(app, "/api/files/rename"),
            FileRenameByPathRequest(path=str(blob), new_name="NOTES"),
            None,
        )

        assert Path(response.path).name == "NOTES"

    def test_traversal_in_new_name_is_still_neutralised(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        clip = root / "clip.mp4"
        clip.write_bytes(b"x")
        app, _ = _make_app(root)

        response = _call(
            _endpoint(app, "/api/files/rename"),
            FileRenameByPathRequest(path=str(clip), new_name="../../escaped.mp4"),
            None,
        )

        assert Path(response.path).parent == root.resolve()
        assert not (tmp_path / "escaped.mp4").exists()


# ── FIX-FB-5 ────────────────────────────────────────────────────────────────


class TestDeleteHandlesSymlinks:
    def test_dangling_symlink_can_be_deleted(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        link = root / "dangling"
        link.symlink_to(tmp_path / "gone")
        app, _ = _make_app(root)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(link)), None)

        assert not link.is_symlink()

    def test_symlink_to_outside_file_removes_only_the_link(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("hunter2")
        link = root / "lnk"
        link.symlink_to(secret)
        app, _ = _make_app(root)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(link)), None)

        assert not link.is_symlink()
        assert secret.exists(), "the symlink target must never be followed"

    def test_symlink_to_outside_directory_removes_only_the_link(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        secret_dir = tmp_path / "secretdir"
        secret_dir.mkdir()
        (secret_dir / "k.txt").write_text("k")
        link = root / "dlnk"
        link.symlink_to(secret_dir)
        app, _ = _make_app(root)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(link)), None)

        assert not link.is_symlink()
        assert (secret_dir / "k.txt").exists(), "rmtree must not run through the link"

    def test_deleting_through_a_symlink_is_still_blocked(self, tmp_path: Path) -> None:
        """The link may be removed, but nothing *behind* it may be."""
        root = tmp_path / "dl"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("hunter2")
        (root / "esc").symlink_to(tmp_path)
        app, _ = _make_app(root)

        assert (
            _status(
                _endpoint(app, "/api/files/delete", "DELETE"),
                FileDeleteRequest(path=str(root / "esc" / "secret.txt")),
                None,
            )
            == 400
        )
        assert secret.exists()

    def test_plain_directory_delete_is_still_recursive(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        folder = root / "album"
        folder.mkdir()
        (folder / "a.mp4").write_bytes(b"x")
        app, _ = _make_app(root)

        _call(_endpoint(app, "/api/files/delete", "DELETE"), FileDeleteRequest(path=str(folder)), None)

        assert not folder.exists()

    def test_absolute_path_outside_root_is_still_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("hunter2")
        app, _ = _make_app(root)

        assert (
            _status(
                _endpoint(app, "/api/files/delete", "DELETE"),
                FileDeleteRequest(path=str(secret)),
                None,
            )
            == 400
        )
        assert secret.exists()

    def test_root_download_dir_is_still_undeletable(self, tmp_path: Path) -> None:
        root = tmp_path / "dl"
        root.mkdir()
        app, _ = _make_app(root)

        assert (
            _status(
                _endpoint(app, "/api/files/delete", "DELETE"),
                FileDeleteRequest(path=str(root)),
                None,
            )
            == 400
        )
        assert root.exists()
