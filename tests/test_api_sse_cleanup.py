"""
tests/test_api_sse_cleanup.py
Regression tests for the SSE / analyse-cache retention fixes in api/server.py.

Both registrations used to happen in the request handler body while their
removals lived in the stream generator's finally block.  Starlette never
iterates the generator when the client drops early, so the SSE client entry
(up to 200 queued frames) and the analyse-cache entry (a full MediaInfo with
the raw yt-dlp format list) became immortal.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

import api.server as srv


def _make_app():
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        analyse_url=lambda url, on_done, on_error: None,
    )
    config = SimpleNamespace(api_token="")
    return srv.create_app(service, config)  # type: ignore[arg-type]


def _endpoint(app, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route {path} not found")


class TestSseClientRegistration:
    def test_client_registered_only_once_the_stream_starts(self):
        srv._sse_clients.clear()
        app = _make_app()
        endpoint = _endpoint(app, "/api/events")

        async def _run():
            request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
            response = await endpoint(request, None)
            # Client dropped before Starlette iterated the body: nothing may
            # have been registered yet.
            assert srv._sse_clients == []

            agen = response.body_iterator
            first = await agen.__anext__()
            assert first.startswith("event: snapshot")
            assert len(srv._sse_clients) == 1

            await agen.aclose()
            assert srv._sse_clients == []

        asyncio.run(_run())
        srv._sse_clients.clear()


class TestAnalyseCacheEviction:
    def setup_method(self):
        srv._analyse_cache.clear()

    def teardown_method(self):
        srv._analyse_cache.clear()

    def test_entry_past_max_age_evicted_even_with_refs(self):
        done = threading.Event()
        done.set()
        srv._analyse_cache["https://example.com/v"] = {
            "done": done,
            "result": {"info": object()},
            "ts": time.monotonic(),
            "created": time.monotonic() - srv._ANALYSE_CACHE_MAX_AGE - 1.0,
            "refs": 1,  # stuck refcount from a client that never started its stream
        }
        srv._analyse_cache_cleanup()
        assert srv._analyse_cache == {}

    def test_fresh_in_flight_entry_is_kept(self):
        srv._analyse_cache["https://example.com/v"] = {
            "done": threading.Event(),  # not set — job still running
            "result": {},
            "ts": 0.0,
            "created": time.monotonic(),
            "refs": 1,
        }
        srv._analyse_cache_cleanup()
        assert "https://example.com/v" in srv._analyse_cache

    def test_size_cap_drops_oldest_first(self):
        base = time.monotonic()
        for i in range(srv._ANALYSE_CACHE_MAX_ENTRIES + 5):
            srv._analyse_cache[f"https://example.com/{i}"] = {
                "done": threading.Event(),
                "result": {},
                "ts": 0.0,
                "created": base + i,
                "refs": 1,
            }
        srv._analyse_cache_cleanup()
        assert len(srv._analyse_cache) == srv._ANALYSE_CACHE_MAX_ENTRIES
        assert "https://example.com/0" not in srv._analyse_cache
        assert f"https://example.com/{srv._ANALYSE_CACHE_MAX_ENTRIES + 4}" in srv._analyse_cache

    def test_refs_incremented_inside_the_generator(self):
        app = _make_app()
        endpoint = _endpoint(app, "/api/analyse/stream")

        async def _run():
            await endpoint(url="https://example.com/video", _=None)
            entry = srv._analyse_cache.get("https://example.com/video")
            assert entry is not None
            assert entry["refs"] == 0  # generator never started

        asyncio.run(_run())


class TestBroadcastSkipsSerialisation:
    def test_no_clients_means_no_json_dump(self, monkeypatch):
        srv._sse_clients.clear()
        called = []
        monkeypatch.setattr(srv.json, "dumps", lambda *a, **kw: called.append(a) or "{}")
        srv._broadcast("progress", {"id": "x"})
        assert called == []


@pytest.mark.parametrize("path", ["/api/events", "/api/analyse/stream"])
def test_routes_exist(path):
    _endpoint(_make_app(), path)
