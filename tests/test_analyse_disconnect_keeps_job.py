"""A client dropping an analyse SSE stream must not evict the still-running job."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import api.server as srv


def test_disconnect_mid_extract_keeps_running_job_cached():
    srv._analyse_cache.clear()
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
        analyse_url=lambda url, on_done, on_error: None,  # job still running
    )
    config = SimpleNamespace(api_token="", download_dir=None)
    app = srv.create_app(service, config)  # type: ignore[arg-type]
    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", None) == "/api/analyse/stream")

    response = asyncio.run(endpoint(url="https://example.com/v", _=None))

    async def _disconnect():
        task = asyncio.ensure_future(response.body_iterator.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await task

    asyncio.run(_disconnect())

    assert "https://example.com/v" in srv._analyse_cache
    srv._analyse_cache.clear()
