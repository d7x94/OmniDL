"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - Web API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api.server as srv
from api.models import ClipboardAnalyseRequest

_STORY = "https://www.facebook.com/stories/12345/"


def _make_app(calls):
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
        analyse_url=lambda url, on_done, on_error: calls.append(url),
    )
    config = SimpleNamespace(api_token="", download_dir=Path("/tmp"))
    return srv.create_app(service, config)  # type: ignore[arg-type]


def _endpoint(app, path, method="GET"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _frames(response):
    async def _run():
        return [chunk async for chunk in response.body_iterator]

    return asyncio.run(_run())


# BUG-API-CDP-GUARD: cdp_only_reason() guarded POST /api/analyse and
# /api/download but not the SSE route the bundled web UI uses, nor the
# clipboard route, so a Linux host analysed a Story fine and failed at download.
def test_analyse_stream_rejects_cdp_only_url_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    srv._analyse_cache.clear()
    calls: list[str] = []
    endpoint = _endpoint(_make_app(calls), "/api/analyse/stream")

    response = asyncio.run(endpoint(url=_STORY, _=None))
    frames = _frames(response)

    assert calls == []
    assert frames and frames[0].startswith("event: error_result")
    assert "Facebook Story" in json.loads(frames[0].split("data: ", 1)[1])["detail"]
    assert srv._analyse_cache == {}


def test_clipboard_analyse_rejects_cdp_only_url_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    calls: list[str] = []
    endpoint = _endpoint(_make_app(calls), "/api/clipboard/analyse", "POST")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(request=None, body=ClipboardAnalyseRequest(url=_STORY), _=None))

    assert exc.value.status_code == 400
    assert calls == []
