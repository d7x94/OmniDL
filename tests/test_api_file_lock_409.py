"""
tests/test_api_file_lock_409.py
A locked output file must answer 409, not crash the request with a 500.

Windows refuses to unlink or rename a file another process still has open
(FFmpeg, a Taildrop transfer, a media player).  PermissionError escaped both
/api/queue/{id}/file endpoints and surfaced as "Exception in ASGI application"
with a full traceback in omnidl_debug.log, and the phone showed a bare 500.

Endpoint functions are invoked directly, matching the test_api_archive.py
convention (this repo has no httpx dependency for a TestClient).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api.server as srv

_LOCKED = PermissionError(
    13, "The process cannot access the file because it is being used by another process"
)


def _make_app(download_dir: Path, task):
    service = SimpleNamespace(
        get_all_tasks=lambda: [task],
        get_task=lambda tid: task if tid == task.id else None,
        analyse_url=lambda url, on_done, on_error: None,
        rename_download=lambda tid, name: (_ for _ in ()).throw(_LOCKED),
    )
    config = SimpleNamespace(api_token="", download_dir=download_dir, taildrop_target_nodes=[])
    return srv.create_app(service, config), service  # type: ignore[arg-type]


def _endpoint(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


@pytest.fixture
def locked_task(tmp_path):
    target = tmp_path / "live.mp4"
    target.write_bytes(b"data")
    return SimpleNamespace(
        id="abc123",
        filename=str(target),
        status=SimpleNamespace(name="COMPLETED"),
    )


def test_delete_of_locked_file_returns_409(tmp_path, locked_task, monkeypatch):
    app, _ = _make_app(tmp_path, locked_task)
    delete_task_file = _endpoint(app, "/api/queue/{task_id}/file", "DELETE")

    monkeypatch.setattr(Path, "unlink", lambda self, **kw: (_ for _ in ()).throw(_LOCKED))

    with pytest.raises(HTTPException) as exc:
        delete_task_file(locked_task.id)

    assert exc.value.status_code == 409
    assert "live.mp4" in exc.value.detail
    # The task keeps its filename — the file is still on disk.
    assert locked_task.filename.endswith("live.mp4")


def test_rename_of_locked_file_returns_409(tmp_path, locked_task):
    from api.models import FileRenameRequest

    app, _ = _make_app(tmp_path, locked_task)
    rename_task_file = _endpoint(app, "/api/queue/{task_id}/rename", "POST")

    with pytest.raises(HTTPException) as exc:
        rename_task_file(locked_task.id, FileRenameRequest(new_name="renamed.mp4"))

    assert exc.value.status_code == 409


def test_successful_delete_still_clears_the_filename(tmp_path, locked_task):
    app, _ = _make_app(tmp_path, locked_task)
    delete_task_file = _endpoint(app, "/api/queue/{task_id}/file", "DELETE")

    result = delete_task_file(locked_task.id)

    assert result.action == "deleted"
    assert locked_task.filename == ""
    assert not (tmp_path / "live.mp4").exists()


def test_gallery_dir_delete_of_a_locked_file_returns_409(tmp_path, monkeypatch):
    """A gallery-dl multi-file task deletes each known file individually; a
    lock on any of them (AUDIT-02) must answer 409, not 500."""
    f1 = tmp_path / "album"
    f1.mkdir()
    (f1 / "a.jpg").write_bytes(b"x")
    task = SimpleNamespace(
        id="gdl1",
        filename=str(f1),
        gallery_dl_files=[str(f1 / "a.jpg")],
        status=SimpleNamespace(name="COMPLETED"),
    )
    app, _ = _make_app(tmp_path, task)
    delete_task_file = _endpoint(app, "/api/queue/{task_id}/file", "DELETE")

    monkeypatch.setattr(Path, "unlink", lambda self, **kw: (_ for _ in ()).throw(_LOCKED))

    with pytest.raises(HTTPException) as exc:
        delete_task_file(task.id)

    assert exc.value.status_code == 409


def test_files_delete_of_a_locked_file_returns_409(tmp_path, monkeypatch):
    from api.models import FileDeleteRequest

    target = tmp_path / "locked.mp4"
    target.write_bytes(b"data")
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
        clear_file_record=lambda p: 0,
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path, taildrop_target_nodes=[])
    app = srv.create_app(service, config)  # type: ignore[arg-type]
    delete_file = _endpoint(app, "/api/files/delete", "DELETE")

    monkeypatch.setattr(Path, "unlink", lambda self, **kw: (_ for _ in ()).throw(_LOCKED))

    with pytest.raises(HTTPException) as exc:
        delete_file(FileDeleteRequest(path=str(target)))

    assert exc.value.status_code == 409


def test_files_rename_by_path_of_a_locked_file_returns_409(tmp_path, monkeypatch):
    from api.models import FileRenameByPathRequest

    target = tmp_path / "locked.mp4"
    target.write_bytes(b"data")
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path, taildrop_target_nodes=[])
    app = srv.create_app(service, config)  # type: ignore[arg-type]
    rename_file_by_path = _endpoint(app, "/api/files/rename", "POST")

    monkeypatch.setattr(Path, "rename", lambda self, target_: (_ for _ in ()).throw(_LOCKED))

    with pytest.raises(HTTPException) as exc:
        rename_file_by_path(FileRenameByPathRequest(path=str(target), new_name="renamed.mp4"))

    assert exc.value.status_code == 409
