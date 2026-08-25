"""
tests/test_api_audit_fixes.py
Regression tests for the web-API audit fixes in api/server.py.

1. GET /api/analyse/stream called service.analyse_url() while holding the
   non-reentrant _analyse_cache_lock.  DownloadService.analyse_url() invokes
   on_error *synchronously in the caller's thread* for a URL with no host
   (e.g. "https:///x" — passes AnalyseRequest's regex but fails is_valid_url),
   and that callback re-enters the same lock → the uvicorn event-loop thread
   deadlocks and every endpoint + SSE stream hangs for the life of the process.

2. The stream's finally block popped the analyse cache by key, so a stream
   whose entry had already been evicted (MAX_AGE / MAX_ENTRIES) removed the
   *newer* in-flight entry for the same URL instead of its own.

3. POST /api/files/rename renamed the file on disk without telling
   DownloadService, leaving task.filename / the history entry pointing at the
   old name — every task-scoped file endpoint then 404s.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.server as srv
from utils.helpers import is_valid_url


def _endpoint(app, path: str, method: str = "GET"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _drain(agen) -> list[str]:
    """Run an async generator to completion and return its frames."""

    async def _run():
        out = []
        async for chunk in agen:
            out.append(chunk)
        return out

    return asyncio.run(_run())


# ── 1. analyse-stream deadlock ────────────────────────────────────────────


def _analyse_url_like_service(url, on_done, on_error):
    """Faithful stand-in for DownloadService.analyse_url (see its lines 125-127)."""
    if not is_valid_url(url):
        on_error("Invalid URL - must start with http:// or https://")
        return
    threading.Thread(target=lambda: on_done(SimpleNamespace()), daemon=True).start()


def _make_app(analyse_url=_analyse_url_like_service, tmp_root: Path | None = None):
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
        analyse_url=analyse_url,
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_root or Path("/tmp"))
    return srv.create_app(service, config)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_url", ["https:///x", "https://#frag", "http://?q=1"])
def test_hostless_url_does_not_deadlock_the_server(bad_url):
    """A URL that passes AnalyseRequest but fails is_valid_url must not hang."""
    srv._analyse_cache.clear()
    app = _make_app()
    endpoint = _endpoint(app, "/api/analyse/stream")

    box: dict = {}

    def _call():
        response = asyncio.run(endpoint(url=bad_url, _=None))
        box["response"] = response

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "analyse_stream deadlocked on _analyse_cache_lock"

    # The lock must be free afterwards for every other request.
    assert srv._analyse_cache_lock.acquire(timeout=2)
    srv._analyse_cache_lock.release()

    frames = _drain(box["response"].body_iterator)
    assert any(f.startswith("event: error_result") for f in frames), frames


def test_hostless_url_leaves_no_cache_entry():
    srv._analyse_cache.clear()
    app = _make_app()
    endpoint = _endpoint(app, "/api/analyse/stream")

    response = asyncio.run(endpoint(url="https:///x", _=None))
    _drain(response.body_iterator)
    assert srv._analyse_cache == {}


# ── 2. cache eviction must not pop a newer entry ──────────────────────────


def test_finally_does_not_evict_a_newer_entry_for_the_same_url():
    srv._analyse_cache.clear()
    app = _make_app()
    endpoint = _endpoint(app, "/api/analyse/stream")

    response = asyncio.run(endpoint(url="https:///x", _=None))
    agen = response.body_iterator

    # Simulate what _analyse_cache_cleanup() does to an over-age / overflowing
    # entry: drop it while a stream is still attached, then let a later request
    # install a fresh entry under the same key.
    newer = {"done": threading.Event(), "result": {}, "ts": 0.0, "created": time.monotonic(), "refs": 1}
    with srv._analyse_cache_lock:
        srv._analyse_cache["https:///x"] = newer

    _drain(agen)

    assert srv._analyse_cache.get("https:///x") is newer, "the newer in-flight entry was evicted"
    srv._analyse_cache.clear()


# ── 3. /api/files/rename must keep DownloadService in sync ────────────────


def test_files_rename_syncs_the_owning_task(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"data")

    task = SimpleNamespace(id="t1", filename=str(src))
    renamed: dict = {}

    def rename_download(task_id, new_name):
        new = Path(task.filename).parent / new_name
        Path(task.filename).rename(new)
        task.filename = str(new)
        renamed["called"] = (task_id, new_name)
        return str(new)

    service = SimpleNamespace(
        get_all_tasks=lambda: [task],
        get_history=lambda: [],
        analyse_url=_analyse_url_like_service,
        rename_download=rename_download,
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path)
    app = srv.create_app(service, config)  # type: ignore[arg-type]
    endpoint = _endpoint(app, "/api/files/rename", "POST")

    from api.models import FileRenameByPathRequest

    result = endpoint(FileRenameByPathRequest(path=str(src), new_name="new.mp4"), None)

    assert renamed.get("called") == ("t1", "new.mp4"), "service.rename_download was not used"
    assert task.filename == str(tmp_path / "new.mp4"), "task.filename left stale"
    assert (tmp_path / "new.mp4").exists()
    assert result.path == str(tmp_path / "new.mp4")


def test_files_rename_still_works_for_an_unowned_file(tmp_path):
    src = tmp_path / "orphan.mp4"
    src.write_bytes(b"data")
    app = _make_app(tmp_root=tmp_path)
    endpoint = _endpoint(app, "/api/files/rename", "POST")

    from api.models import FileRenameByPathRequest

    result = endpoint(FileRenameByPathRequest(path=str(src), new_name="kept.mp4"), None)
    assert (tmp_path / "kept.mp4").exists()
    assert result.action == "renamed"
