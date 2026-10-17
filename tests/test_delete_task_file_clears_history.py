"""DELETE /api/queue/{id}/file must clear the history record too, not only the task."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import api.server as srv


def test_delete_task_file_calls_clear_file_record(tmp_path):
    target = tmp_path / "a.mp4"
    target.write_bytes(b"x")
    task = SimpleNamespace(
        status=SimpleNamespace(name="COMPLETED"),
        filename=str(target),
        media_info=None,
    )
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
        analyse_url=lambda url, on_done, on_error: None,
        get_task=lambda tid: task,
        clear_file_record=MagicMock(return_value=1),
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path)
    app = srv.create_app(service, config)  # type: ignore[arg-type]
    endpoint = next(
        r.endpoint
        for r in app.routes
        if getattr(r, "path", None) == "/api/queue/{task_id}/file"
        and "DELETE" in getattr(r, "methods", set())
    )

    endpoint(task_id="t1", _=None)

    assert not target.exists()
    service.clear_file_record.assert_called_once_with(target.resolve())
