"""Browser-started convert jobs (no source task) must not be reported as excluded queue tasks."""

from __future__ import annotations

from types import SimpleNamespace

import api.server as srv
from app.services.remote_convert_service import ConversionStatus


def test_active_file_browser_convert_is_not_an_excluded_task(monkeypatch):
    job = SimpleNamespace(status=ConversionStatus.PENDING, source_task_id="")
    fake = SimpleNamespace(get_all_jobs=lambda: [job])
    monkeypatch.setattr(srv, "_active_remote_convert", fake)

    assert srv.tasks_with_active_convert() == frozenset()
