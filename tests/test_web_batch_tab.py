"""Regression tests for the Web API Batch tab (parity with ui/tabs/batch_tab.py).

WB-01  POST /api/download/batch enqueues every item in one call.
WB-02  A bad item reports its own error without sinking the rest of the batch.
WB-03  The request is capped at MAX_BATCH_ITEMS and rejects an empty list.
WB-04  Analyse responses carry playlist_entries so the web UI can hand a
       playlist to the Batch tab the way HomeTab does on the desktop.
WB-05  The web UI ships the Batch markup, the catalogue keys and the JS.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import api.server as srv
from api.models import MAX_BATCH_ITEMS, BatchDownloadItem, BatchDownloadRequest
from domain.models.download_task import DownloadTask, MediaInfo

_INDEX_HTML = Path(__file__).resolve().parents[1] / "api" / "static" / "index.html"


def _endpoint(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _api(*, reject: str = ""):
    """Real FastAPI app over a stub service that records every enqueue."""
    started: list[dict] = []

    def start_download(url, media_info, format_id, output_ext):
        if reject and reject in url:
            raise RuntimeError("nope: " + url)
        started.append(
            {
                "url": url,
                "title": media_info.title,
                "is_live": media_info.is_live,
                "room_id": media_info.tiktok_room_id,
                "format_id": format_id,
                "output_ext": output_ext,
            }
        )
        task = DownloadTask(
            url=url, media_info=media_info, format_id=format_id, output_ext=output_ext
        )
        return task

    service = SimpleNamespace(
        start_download=start_download, get_all_tasks=lambda: [], get_history=lambda: []
    )
    config = SimpleNamespace(
        api_token="", download_dir=Path("/tmp"), default_quality="best", default_format="mp4"
    )
    return srv.create_app(service, config), started  # type: ignore[arg-type]


def _item(url: str, **kw) -> BatchDownloadItem:
    return BatchDownloadItem(url=url, **kw)


# ── WB-01 ─────────────────────────────────────────────────────────────────


def test_batch_endpoint_enqueues_every_item():
    """One request, N tasks — the per-URL POST loop hit the 60 req/min limiter."""
    app, started = _api()
    call = _endpoint(app, "/api/download/batch", "POST")
    body = BatchDownloadRequest(
        items=[
            _item("https://a.com/1", title="One", format_id="best", output_ext="mkv"),
            _item("https://a.com/2", title="Two"),
        ]
    )

    resp = asyncio.run(call(body=body, _=None))

    assert resp.queued == 2
    assert resp.failed == 0
    assert [r.url for r in resp.results] == ["https://a.com/1", "https://a.com/2"]
    assert all(r.task_id and not r.error for r in resp.results)
    assert [s["title"] for s in started] == ["One", "Two"]
    assert started[0]["format_id"] == "best"
    assert started[0]["output_ext"] == "mkv"
    # Falls back to the configured defaults, exactly like POST /api/download.
    assert started[1]["format_id"] == "best"
    assert started[1]["output_ext"] == "mp4"


def test_batch_forwards_live_and_room_id():
    app, started = _api()
    call = _endpoint(app, "/api/download/batch", "POST")
    body = BatchDownloadRequest(
        items=[_item("https://tiktok.com/@u/live", is_live=True, tiktok_room_id="12345")]
    )

    asyncio.run(call(body=body, _=None))

    assert started[0]["is_live"] is True
    assert started[0]["room_id"] == "12345"


def test_batch_rejects_a_forged_room_id():
    """Only a plain room number is ever echoed on to TikTok's room/info API."""
    app, started = _api()
    call = _endpoint(app, "/api/download/batch", "POST")
    body = BatchDownloadRequest(items=[_item("https://tiktok.com/@u/live", tiktok_room_id="1;drop")])

    asyncio.run(call(body=body, _=None))

    assert started[0]["room_id"] == ""


# ── WB-02 ─────────────────────────────────────────────────────────────────


def test_one_bad_item_does_not_sink_the_batch():
    app, started = _api(reject="/bad")
    call = _endpoint(app, "/api/download/batch", "POST")
    body = BatchDownloadRequest(
        items=[_item("https://a.com/ok"), _item("https://a.com/bad"), _item("https://a.com/ok2")]
    )

    resp = asyncio.run(call(body=body, _=None))

    assert resp.queued == 2
    assert resp.failed == 1
    assert resp.results[1].task_id is None
    assert "nope" in resp.results[1].error
    assert [s["url"] for s in started] == ["https://a.com/ok", "https://a.com/ok2"]


def test_cdp_only_url_is_reported_per_item(monkeypatch):
    """A Facebook Story on Linux is a 400 for that item only, not for the batch."""
    monkeypatch.setattr(srv, "cdp_only_reason", lambda url: "needs a browser" if "story" in url else "")
    app, started = _api()
    call = _endpoint(app, "/api/download/batch", "POST")
    body = BatchDownloadRequest(items=[_item("https://fb.com/story/1"), _item("https://a.com/ok")])

    resp = asyncio.run(call(body=body, _=None))

    assert resp.results[0].error == "needs a browser"
    assert resp.results[1].task_id
    assert [s["url"] for s in started] == ["https://a.com/ok"]


# ── WB-03 ─────────────────────────────────────────────────────────────────


def test_batch_request_rejects_an_empty_list():
    with pytest.raises(ValidationError):
        BatchDownloadRequest(items=[])


def test_batch_request_is_capped():
    over = [_item(f"https://a.com/{n}") for n in range(MAX_BATCH_ITEMS + 1)]
    with pytest.raises(ValidationError):
        BatchDownloadRequest(items=over)
    # The cap itself is allowed.
    assert len(BatchDownloadRequest(items=over[:MAX_BATCH_ITEMS]).items) == MAX_BATCH_ITEMS


def test_cap_matches_the_desktop_tab():
    from ui.tabs.batch_tab import MAX_BATCH_URLS

    assert MAX_BATCH_ITEMS == MAX_BATCH_URLS
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert f"const MAX_BATCH_URLS = {MAX_BATCH_ITEMS};" in html


# ── WB-04 ─────────────────────────────────────────────────────────────────


def test_playlist_entries_are_exposed_and_capped():
    info = MediaInfo(
        url="https://yt.com/list",
        title="L",
        playlist_entries=[f"https://yt.com/{n}" for n in range(MAX_BATCH_ITEMS + 10)],
    )
    entries = srv._playlist_entries(info)
    assert len(entries) == MAX_BATCH_ITEMS
    assert entries[0] == "https://yt.com/0"


def test_analyse_response_carries_playlist_entries():
    """Without these the web UI cannot do what HomeTab does on the desktop."""
    from api.models import AnalyseResponse

    assert "playlist_entries" in AnalyseResponse.model_fields
    src = (Path(__file__).resolve().parents[1] / "api" / "server.py").read_text(encoding="utf-8")
    # POST /api/analyse, POST /api/clipboard/analyse and the SSE payload.
    assert src.count("_playlist_entries(info)") == 3


# ── WB-05 ─────────────────────────────────────────────────────────────────


def _catalogues() -> dict[str, set[str]]:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    body = html[html.index("const I18N = {") : html.index("\nlet uiLang =")]
    out: dict[str, set[str]] = {}
    current = None
    for line in body.splitlines():
        stripped = line.strip()
        header = re.fullmatch(r"(en|vi|zh):\s*\{", stripped)
        if header:
            current = header.group(1)
            out[current] = set()
            continue
        entry = re.match(r"'([^']+)':", stripped)
        if entry and current:
            out[current].add(entry.group(1))
    return out


def test_web_ui_ships_the_batch_tab():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="tab-batch"' in html
    assert 'id="panel-batch"' in html
    for el in ("batch-input", "batch-list", "batch-select-all", "batch-sequential",
               "batch-quality", "batch-format", "batch-retry-btn", "batch-queue-btn"):
        assert f'id="{el}"' in html, el


def test_batch_catalogue_keys_exist_in_all_three_languages():
    keys = _catalogues()
    required = {
        "nav.batch", "batch.title", "batch.hint", "batch.empty", "batch.select_all",
        "batch.sequential", "batch.queue_all", "batch.queue_add_count",
        "batch.ready_summary", "batch.no_valid_urls", "batch.sequential_done_status",
        "batch.playlist_loaded", "dl.send_to_batch",
    }
    for lang in ("en", "vi", "zh"):
        assert required <= keys[lang], sorted(required - keys[lang])


def test_batch_queue_uses_the_bulk_endpoint():
    """A per-URL POST loop is what the 60 req/min limiter used to cut off."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    body = html[html.index("async function queueBatchAll()") : html.index("async function _batchSeqNext(")]
    assert "'/api/download/batch'" in body


def test_language_switch_rerenders_the_batch_rows():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    body = html[html.index("function applyI18n()") : html.index("function buildLangSelects()")]
    assert "renderBatchList" in body
    assert "renderBatchControls" in body
