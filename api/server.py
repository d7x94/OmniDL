"""
api/server.py
FastAPI remote-control server for OmniDL.

Architecture:
  • Runs entirely in a single daemon thread (uvicorn + anyio).
  • Does NOT share the asyncio event loop with Tkinter — they live in
    separate threads and communicate only through DownloadService
    (which is already thread-safe) and the EventBus.
  • Analyse endpoint bridges the callback-based DownloadService.analyse_url()
    to a synchronous wait using threading.Event.
  • Real-time progress is pushed to SSE clients via a per-client queue.Queue
    fed by EventBus subscriber callbacks (which fire on worker threads).

Token auth:
  • All endpoints require a Bearer token in the Authorization header.
  • The SSE endpoint additionally accepts the token as ?token= query param
    because the browser EventSource API cannot set custom headers.
  • Tokens are compared with secrets.compare_digest() to prevent
    timing-oracle attacks.
"""

from __future__ import annotations

import collections
import json
import logging
import mimetypes
import queue
import secrets
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from api.models import (
    AnalyseRequest,
    AnalyseResponse,
    ClipboardAnalyseRequest,
    ConvertJobResponse,
    ConvertRequest,
    DownloadRequest,
    EncoderOption,
    FileActionResponse,
    FileBrowseItem,
    FileBrowseResponse,
    FileConvertJobResponse,
    FileConvertRequest,
    FileDeleteRequest,
    FileDeleteResponse,
    FileInfoResponse,
    FileTransferRequest,
    FileTransferResponse,
    HistoryListResponse,
    HistoryStatsResponse,
    QueueActionResponse,
    TaskResponse,
)
from app.event_bus import EventBus
from domain.models.conversion_job import ConversionJob, ConversionStatus
from domain.models.download_task import DownloadTask, MediaInfo

if TYPE_CHECKING:
    import uvicorn

    from app.services.download_service import DownloadService
    from app.services.remote_convert_service import RemoteConvertService
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── SSE broadcast helpers ─────────────────────────────────────────────────────

_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_PING_INTERVAL = 15  # seconds — keeps iOS Safari connections alive

# ── Analyse job cache ─────────────────────────────────────────────────────────
# Deduplicates concurrent SSE analyse requests for the same URL.
# When a client reconnects (iOS background suspend), the new EventSource
# attaches to the in-flight job instead of spawning a second extract run.
# Entry lifecycle: created when job starts, removed 30 s after job completes
# (gives the client time to reconnect and read the cached result).
#
# Per entry:
#   "done"   threading.Event  — set when result/error is ready
#   "result" dict             — {"info": MediaInfo} or {"error": str}
#   "ts"     float            — monotonic time when done was set
#   "refs"   int              — number of active SSE streams on this job

_analyse_cache: dict[str, dict] = {}
_analyse_cache_lock = threading.Lock()
_ANALYSE_CACHE_TTL = 30.0  # seconds to keep result after completion
_EXTRA_MIME = {".ts": "video/mp2t"}  # missing from Python's default mimetypes DB

# Per-IP sliding-window rate limiter (stdlib only, no new deps).
_RATE_LIMIT = 60  # max requests per window
_RATE_WINDOW = 60.0  # seconds
_rate_buckets: dict[str, collections.deque] = {}
_rate_lock = threading.Lock()


def _check_rate_limit(ip: str) -> bool:
    """Return True if within limit, False if over. Thread-safe."""
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, collections.deque())
        while bucket and now - bucket[0] > _RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT:
            return False
        bucket.append(now)
        return True


def _analyse_cache_cleanup() -> None:
    """Remove completed entries older than TTL. Must be called under _analyse_cache_lock."""
    now = time.monotonic()
    stale = [
        k
        for k, v in _analyse_cache.items()
        if v["done"].is_set() and v.get("refs", 0) == 0 and now - v.get("ts", now) > _ANALYSE_CACHE_TTL
    ]
    for k in stale:
        del _analyse_cache[k]


# ── Runtime server state (module-level so stop/restart can reach it) ─────────
_active_server: "uvicorn.Server | None" = None  # type: ignore[name-defined]
_active_thread: threading.Thread | None = None
_active_bus: "EventBus | None" = None  # held to allow re-wiring on restart
_server_lock = threading.Lock()  # guards _active_server / _active_thread
_bus_wired = False  # BUG-CB: prevent duplicate subscriptions on restart
_wired_remote_convert: "Optional[RemoteConvertService]" = None  # tracks active auto_convert subscription


def _broadcast(event_type: str, data: dict) -> None:
    """Push an SSE frame to all connected clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead: list[queue.Queue] = []
        for q in _sse_clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _task_to_dict(task: DownloadTask) -> dict:
    """Serialize a DownloadTask to a JSON-safe dict for SSE / REST responses."""
    snap = task.snapshot()
    return {
        "id": task.id,
        "url": task.url,
        "title": task.title,
        "platform": task.platform,
        "status": snap["status"].name,
        "progress": round(snap["progress"], 1),
        "speed": snap["speed"],
        "eta": snap["eta"],
        "downloaded_bytes": snap["downloaded_bytes"],
        "total_bytes": snap["total_bytes"],
        "filename": snap["filename"],
        "error_msg": snap["error_msg"],
        "created_at": task.created_at,
        "is_live": bool(task.media_info.is_live) if task.media_info else False,
    }


# ── EventBus → SSE wiring ─────────────────────────────────────────────────────


def _wire_event_bus(bus: EventBus) -> None:
    """
    Subscribe to all relevant EventBus events and fan them out to SSE clients.
    Called once at server startup — subscriptions are permanent for the
    lifetime of the process.
    """

    def _on_started(task: DownloadTask) -> None:
        _broadcast("started", _task_to_dict(task))

    def _on_progress(task: DownloadTask) -> None:
        _broadcast("progress", _task_to_dict(task))

    def _on_completed(task: DownloadTask) -> None:
        _broadcast("completed", _task_to_dict(task))

    def _on_failed(task: DownloadTask) -> None:
        _broadcast("failed", _task_to_dict(task))

    def _on_cancelled(task: DownloadTask) -> None:
        _broadcast("cancelled", _task_to_dict(task))

    # Taildrop transfer results — broadcast so the Remote UI can restore
    # the transfer button and show a completion / failure toast.
    # kwargs: task, dest_node  (and error for FAILED)
    def _on_taildrop_completed(task: DownloadTask, dest_node: str, **_kw) -> None:
        _broadcast(
            "taildrop_completed",
            {
                "task_id": task.id,
                "dest_node": dest_node,
            },
        )

    def _on_taildrop_failed(task: DownloadTask, dest_node: str, error: str = "", **_kw) -> None:
        _broadcast(
            "taildrop_failed",
            {
                "task_id": task.id,
                "dest_node": dest_node,
                "error": error,
            },
        )

    bus.subscribe(EventBus.DOWNLOAD_STARTED, _on_started)
    bus.subscribe(EventBus.DOWNLOAD_PROGRESS, _on_progress)
    bus.subscribe(EventBus.DOWNLOAD_COMPLETED, _on_completed)
    bus.subscribe(EventBus.DOWNLOAD_FAILED, _on_failed)
    bus.subscribe(EventBus.DOWNLOAD_CANCELLED, _on_cancelled)
    bus.subscribe(EventBus.TAILDROP_COMPLETED, _on_taildrop_completed)
    bus.subscribe(EventBus.TAILDROP_FAILED, _on_taildrop_failed)

    # ── Convert events → SSE ─────────────────────────────────────────────
    # Serialise ConversionJob snapshots the same way as tasks, so the iOS
    # client can update convert progress with the same SSE infrastructure.

    def _job_to_sse(job: ConversionJob) -> dict:
        snap = job.snapshot()
        # Expose only the basename so the client doesn't see server paths.
        out = snap.get("output_filename", "") or ""
        snap["output_filename"] = Path(out).name if out else ""
        return snap

    def _on_convert_started(job: ConversionJob, **_kw) -> None:
        _broadcast("convert_started", _job_to_sse(job))

    def _on_convert_progress(job: ConversionJob, **_kw) -> None:
        _broadcast("convert_progress", _job_to_sse(job))

    def _on_convert_completed(job: ConversionJob, **_kw) -> None:
        _broadcast("convert_completed", _job_to_sse(job))

    def _on_convert_failed(job: ConversionJob, **_kw) -> None:
        _broadcast("convert_failed", _job_to_sse(job))

    def _on_convert_cancelled(job: ConversionJob, **_kw) -> None:
        _broadcast("convert_cancelled", _job_to_sse(job))

    bus.subscribe(EventBus.CONVERT_STARTED, _on_convert_started)
    bus.subscribe(EventBus.CONVERT_PROGRESS, _on_convert_progress)
    bus.subscribe(EventBus.CONVERT_COMPLETED, _on_convert_completed)
    bus.subscribe(EventBus.CONVERT_FAILED, _on_convert_failed)
    bus.subscribe(EventBus.CONVERT_CANCELLED, _on_convert_cancelled)

    # auto_convert_tiktok_live is wired separately in start_api_server() so it
    # can be swapped on each restart without duplicating the static _on_* handlers.


# ── App factory ───────────────────────────────────────────────────────────────


def create_app(
    service: "DownloadService",
    config: "ConfigManager",
    remote_convert: "Optional[RemoteConvertService]" = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    ``remote_convert`` is optional so existing callers (tests, older startup
    code) continue to work unchanged — convert endpoints simply return 503
    when the service is not provided.
    """

    app = FastAPI(
        title="OmniDL Remote API",
        version="1.0.0",
        docs_url=None,  # disable Swagger UI — reduces attack surface
        redoc_url=None,
        openapi_url=None,
    )

    # Allow cross-origin requests so the PWA can connect from any origin
    # (including the iOS "Add to Home Screen" launch context).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    _token: str = config.api_token  # captured at factory time, immutable

    # ── Auth dependency ───────────────────────────────────────────────────

    async def _require_auth(
        request: Request,
        token: Optional[str] = Query(default=None),  # for EventSource (SSE)
    ) -> None:
        """
        Verify Bearer token.  Accepts it from either:
          • Authorization: Bearer <token>  header  (all endpoints)
          • ?token=<token>                 query   (EventSource only)
        Skipped entirely when api_token is empty (open/local-only mode).
        """
        client_ip = request.client.host if request.client else "unknown"
        if not _check_rate_limit(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded — try again later")
        if not _token:
            return  # auth disabled — local trusted network only
        provided = token  # query param first (SSE path)
        if not provided:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
        if not provided:
            logger.warning("API auth: missing token from %s", client_ip)
            raise HTTPException(status_code=401, detail="Authorization required")
        if not secrets.compare_digest(provided, _token):
            logger.warning("API auth: invalid token from %s", client_ip)
            raise HTTPException(status_code=403, detail="Invalid token")

    # ── Health check ──────────────────────────────────────────────────────

    @app.get("/api/ping")
    async def ping(_: None = Depends(_require_auth)):
        """Health check — no auth required when token is empty."""
        return {"status": "ok", "app": "OmniDL", "version": "1.0.0"}

    # ── URL analysis ──────────────────────────────────────────────────────

    @app.post("/api/analyse", response_model=AnalyseResponse)
    async def analyse(body: AnalyseRequest, _: None = Depends(_require_auth)):
        """
        Extract metadata for a URL.
        Bridges the callback-based DownloadService.analyse_url() to a
        synchronous HTTP response via threading.Event.
        Timeout: 120 seconds — Kuaishou CDP strategy needs up to ~90s
        (short-URL resolution + strategies A-D + CDP intercept).
        """
        result: dict = {}
        done = threading.Event()

        def on_done(info: MediaInfo) -> None:
            result["info"] = info
            done.set()

        def on_error(err: str) -> None:
            result["error"] = err
            done.set()

        service.analyse_url(body.url, on_done=on_done, on_error=on_error)
        done.wait(timeout=180)

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        if "info" not in result:
            raise HTTPException(status_code=408, detail="Analysis timed out after 180 s")

        info: MediaInfo = result["info"]
        return AnalyseResponse(
            url=info.url,
            title=info.title,
            uploader=info.uploader,
            duration=info.duration,
            thumbnail=info.thumbnail,
            platform=info.platform,
            formats=info.formats,
            is_live=info.is_live,
            playlist_count=len(info.playlist_entries),
            source_engine=info.source_engine,  # BUG-BT fix: forward engine choice to client
        )

    # ── URL analysis — SSE streaming variant ─────────────────────────────
    #
    # Uses _analyse_cache to deduplicate concurrent requests for the same URL.
    # If a job is already running (e.g. previous EventSource from before iOS
    # background suspend), the new connection attaches to the existing job's
    # threading.Event and waits — no second extract is spawned.
    # If the job completed within the last 30 s, the cached result is returned
    # immediately without touching the download service at all.

    @app.get("/api/analyse/stream")
    async def analyse_stream(
        url: str = Query(...),
        _: None = Depends(_require_auth),
    ):
        """
        SSE streaming analyse — deduplicates concurrent requests for same URL.
        Sends keepalive comments every 10 s while the background worker runs,
        then sends a single "result" or "error_result" event and closes.
        """
        # Validate + normalise URL (strips share text, trailing punctuation).
        try:
            req = AnalyseRequest(url=url)
        except Exception as exc:
            _exc_msg = str(exc)

            def _invalid() -> Generator[str, None, None]:
                payload = json.dumps({"detail": _exc_msg})
                yield f"event: error_result\ndata: {payload}\n\n"

            return StreamingResponse(
                _invalid(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        clean_url = req.url

        with _analyse_cache_lock:
            _analyse_cache_cleanup()
            entry = _analyse_cache.get(clean_url)
            if entry is None:
                # First request for this URL — create job and start extract.
                entry = {
                    "done": threading.Event(),
                    "result": {},
                    "ts": 0.0,
                    "refs": 0,
                }
                _analyse_cache[clean_url] = entry

                def on_done(info: MediaInfo) -> None:
                    with _analyse_cache_lock:
                        entry["result"]["info"] = info
                        entry["ts"] = time.monotonic()
                    entry["done"].set()

                def on_error(err: str) -> None:
                    with _analyse_cache_lock:
                        entry["result"]["error"] = err
                        entry["ts"] = time.monotonic()
                    entry["done"].set()

                service.analyse_url(clean_url, on_done=on_done, on_error=on_error)
                logger.debug("Analyse cache: new job for %s", clean_url[:80])
            else:
                logger.debug(
                    "Analyse cache: attaching to existing job for %s (done=%s)",
                    clean_url[:80],
                    entry["done"].is_set(),
                )
            entry["refs"] += 1

        def _stream() -> Generator[str, None, None]:
            try:
                done = entry["done"]
                result = entry["result"]

                deadline = time.monotonic() + 180.0
                keepalive_interval = 10.0
                last_ka = time.monotonic()

                while not done.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    wait_s = min(keepalive_interval, remaining)
                    done.wait(timeout=wait_s)
                    now = time.monotonic()
                    if not done.is_set() and now - last_ka >= keepalive_interval:
                        yield ": keepalive\n\n"
                        last_ka = now

                if "error" in result:
                    payload = json.dumps({"detail": result["error"]})
                    yield f"event: error_result\ndata: {payload}\n\n"
                elif "info" in result:
                    info: MediaInfo = result["info"]
                    payload = json.dumps(
                        {
                            "url": info.url,
                            "title": info.title,
                            "uploader": info.uploader,
                            "duration": info.duration,
                            "thumbnail": info.thumbnail,
                            "platform": info.platform,
                            "formats": info.formats,
                            "is_live": info.is_live,
                            "playlist_count": len(info.playlist_entries),
                            "source_engine": info.source_engine,
                            "tiktok_room_id": info.tiktok_room_id or "",
                        }
                    )
                    yield f"event: result\ndata: {payload}\n\n"
                else:
                    payload = json.dumps({"detail": "Analysis timed out after 180 s"})
                    yield f"event: error_result\ndata: {payload}\n\n"
            finally:
                # Decrement ref count so TTL cleanup can remove the entry.
                # BUG-KS-AC-01 FIX: Failed/timed-out entries were kept in cache
                # for the full 30 s TTL. When the client retried immediately after
                # a Kuaishou strategy-E timeout (~150 s), the new SSE connection
                # attached to the dead error entry and received the cached error
                # instantly — without spawning a new extract job. The retry never
                # ran strategies again, so the user saw the same error every time
                # even though a second attempt would succeed (network recovered).
                # Fix: evict error and timeout entries immediately when the last
                # ref drops, so the next request always starts a fresh job.
                with _analyse_cache_lock:
                    entry["refs"] = max(0, entry["refs"] - 1)
                    if entry["refs"] == 0 and "info" not in entry.get("result", {}):
                        # Error or timeout — evict so next request spawns a fresh job.
                        _analyse_cache.pop(clean_url, None)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Clipboard analyse (Remote client sends URL from its own clipboard) ──

    @app.post("/api/clipboard/analyse", response_model=AnalyseResponse)
    async def clipboard_analyse(body: ClipboardAnalyseRequest, _: None = Depends(_require_auth)):
        """
        Analyse a URL submitted from the Remote client's clipboard.

        The iPhone PWA can read its own clipboard and POST the URL here.
        This is identical to /api/analyse but has a dedicated endpoint so
        the client can distinguish clipboard-triggered requests from manual
        ones (e.g. to show a different toast or auto-queue immediately).
        """
        result: dict = {}
        done = threading.Event()

        def on_done(info: MediaInfo) -> None:
            result["info"] = info
            done.set()

        def on_error(err: str) -> None:
            result["error"] = err
            done.set()

        service.analyse_url(body.url, on_done=on_done, on_error=on_error)
        done.wait(timeout=180)

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        if "info" not in result:
            raise HTTPException(status_code=408, detail="Analysis timed out after 180 s")

        info: MediaInfo = result["info"]
        return AnalyseResponse(
            url=info.url,
            title=info.title,
            uploader=info.uploader,
            duration=info.duration,
            thumbnail=info.thumbnail,
            platform=info.platform,
            formats=info.formats,
            is_live=info.is_live,
            playlist_count=len(info.playlist_entries),
            source_engine=info.source_engine,
            tiktok_room_id=info.tiktok_room_id,
        )

    # ── Download ──────────────────────────────────────────────────────────

    @app.post("/api/download", response_model=TaskResponse)
    async def start_download(body: DownloadRequest, _: None = Depends(_require_auth)):
        """
        Enqueue a download job.
        A minimal MediaInfo is constructed from the request so the job can
        be queued immediately — yt-dlp fills in the real title during download.
        """
        # BUG-TT-DOWNLOAD-CANONICAL: iOS shortcuts send the original short URL
        # (vt.tiktok.com/ZSxxx) to /api/download even after /api/analyse resolved
        # it to a canonical @user/live URL. Without this lookup, task.url stays as
        # the short URL → [vm.tiktok] extractor re-resolves it → HTTP 429.
        # Fix: look up the analyse cache for the canonical URL and tiktok_room_id.
        _info_url = body.url
        _info_room_id = body.tiktok_room_id or ""
        with _analyse_cache_lock:
            _cached = _analyse_cache.get(body.url)
            if _cached is not None and _cached["done"].is_set():
                _ci = _cached.get("result", {}).get("info")
                if _ci is not None:
                    if _ci.url and _ci.url.startswith("http"):
                        _info_url = _ci.url
                    if _ci.tiktok_room_id and not _info_room_id:
                        _info_room_id = _ci.tiktok_room_id

        info = MediaInfo(
            url=_info_url,
            title=body.title or body.url[:80],
            platform=body.platform or "unknown",
            source_engine=body.source_engine or "yt_dlp",
            is_live=bool(body.is_live),  # forwarded from /api/analyse — avoids a second extract_info
            tiktok_room_id=_info_room_id,  # BUG-TT-25: enables signed room/info fallback
        )
        try:
            task = service.start_download(
                url=body.url,
                media_info=info,
                format_id=body.format_id or config.default_quality,
                output_ext=body.output_ext or config.default_format,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TaskResponse(**_task_to_dict(task))

    # ── Queue ─────────────────────────────────────────────────────────────

    @app.get("/api/queue", response_model=list[TaskResponse])
    async def get_queue(_: None = Depends(_require_auth)):
        """Return all tasks currently in the queue (active + terminal)."""
        return [TaskResponse(**_task_to_dict(t)) for t in service.get_all_tasks()]

    @app.get("/api/queue/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str, _: None = Depends(_require_auth)):
        task = service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskResponse(**_task_to_dict(task))

    @app.post("/api/queue/{task_id}/pause", response_model=QueueActionResponse)
    async def pause_task(task_id: str, _: None = Depends(_require_auth)):
        _get_task_or_404(service, task_id)
        service.pause_download(task_id)
        return QueueActionResponse(task_id=task_id, action="paused")

    @app.post("/api/queue/{task_id}/resume", response_model=QueueActionResponse)
    async def resume_task(task_id: str, _: None = Depends(_require_auth)):
        _get_task_or_404(service, task_id)
        service.resume_download(task_id)
        return QueueActionResponse(task_id=task_id, action="resumed")

    @app.post("/api/queue/{task_id}/cancel", response_model=QueueActionResponse)
    async def cancel_task(task_id: str, _: None = Depends(_require_auth)):
        _get_task_or_404(service, task_id)
        service.cancel_download(task_id)
        return QueueActionResponse(task_id=task_id, action="cancelled")

    @app.delete("/api/queue/finished")
    async def clear_finished(_: None = Depends(_require_auth)):
        """Remove all COMPLETED / FAILED / CANCELLED tasks from the queue.

        Tasks that currently have an active (PENDING/CONVERTING) remote convert
        job are excluded — clearing them would orphan the running FFmpeg process
        and leave it with no corresponding queue entry on next reconnect.
        """
        exclude: frozenset[str] = frozenset()
        if remote_convert is not None:
            _ACTIVE = {ConversionStatus.PENDING, ConversionStatus.CONVERTING}
            exclude = frozenset(
                j.source_task_id for j in remote_convert.get_all_jobs() if j.status in _ACTIVE
            )
        service.clear_finished(exclude_ids=exclude or None)
        return {"status": "ok", "excluded_count": len(exclude), "excluded_ids": list(exclude)}

    # ── File actions (completed tasks only) ───────────────────────────────

    def _resolve_task_file(task: DownloadTask) -> Path:
        """
        Resolve and validate the output file path for a completed task.

        Security: ensures the path sits inside the configured download_dir.
        Raises HTTPException(404/403) on any problem so callers stay clean.
        """
        raw = getattr(task, "filename", None)
        if not raw:
            raise HTTPException(status_code=404, detail="File path not recorded for this task")
        resolved = Path(raw).resolve()
        allowed = config.download_dir.resolve()
        # Path.is_relative_to() — Python 3.9+; project targets 3.11+ so safe.
        if not resolved.is_relative_to(allowed):
            raise HTTPException(
                status_code=403,
                detail="File is outside the configured download directory",
            )
        return resolved

    @app.post(
        "/api/queue/{task_id}/transfer",
        response_model=FileActionResponse,
        summary="Send completed file to iPhone via Taildrop",
    )
    async def transfer_to_device(task_id: str, _: None = Depends(_require_auth)) -> FileActionResponse:
        """
        Trigger an on-demand Taildrop transfer for a completed task.

        Requirements:
        • task must be COMPLETED
        • taildrop_enabled=True and taildrop_target_node set in config
        • tailscale CLI must be reachable on the server

        The transfer runs in TaildropService's background executor; this
        endpoint returns immediately with status "queued" or raises 4xx on
        pre-flight failures.
        """
        task = _get_task_or_404(service, task_id)
        if task.status.name != "COMPLETED":
            raise HTTPException(status_code=400, detail="Task is not COMPLETED")

        td = service.taildrop
        if not config.taildrop_enabled:
            raise HTTPException(status_code=503, detail="Taildrop is disabled in settings")
        node = config.taildrop_target_node
        if not node:
            raise HTTPException(status_code=503, detail="Taildrop target node not configured")
        if not td.is_tailscale_available():
            raise HTTPException(status_code=503, detail="tailscale CLI not found on server")

        file_path = _resolve_task_file(task)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Output file not found on disk")

        # Dispatch to the background executor — non-blocking.
        # send_now() bypasses the send_mode guard so the transfer fires
        # regardless of whether the mode is "always" or "ask".
        # TaildropService publishes TAILDROP_COMPLETED / TAILDROP_FAILED events
        # on the EventBus which SSE clients will receive automatically.
        td.send_now(task)
        return FileActionResponse(
            task_id=task_id,
            action="transfer_queued",
            detail=f"Sending to {node} via Taildrop…",
        )

    @app.delete(
        "/api/queue/{task_id}/file",
        response_model=FileActionResponse,
        summary="Delete the output file of a completed task from the server",
    )
    async def delete_task_file(task_id: str, _: None = Depends(_require_auth)) -> FileActionResponse:
        """
        Permanently delete the output file from the server's disk.

        The task entry remains in the queue (so the user still sees it),
        but filename is cleared so file-action buttons are disabled.
        Only works for COMPLETED tasks that still have an output file.

        Security: path is validated against download_dir before deletion.
        """
        task = _get_task_or_404(service, task_id)
        if task.status.name != "COMPLETED":
            raise HTTPException(status_code=400, detail="Task is not COMPLETED")

        file_path = _resolve_task_file(task)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File already deleted or not found")

        if file_path.is_dir():
            # Gallery-dl multi-file download: delete only the known files so
            # the download folder is never rmtree'd silently.
            gdl: list = getattr(task, "gallery_dl_files", None) or []
            if not gdl:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Task output is a directory but no file list is available. "
                        "Delete the files manually from the server."
                    ),
                )
            errors: list[str] = []
            for f_str in gdl:
                fp = Path(f_str)
                try:
                    if fp.exists():
                        fp.unlink()
                except OSError as exc:
                    errors.append(f"{fp.name}: {exc.strerror}")
            if errors:
                raise HTTPException(
                    status_code=500,
                    detail=f"Partial delete — could not remove: {'; '.join(errors[:3])}",
                )
            # Remove directory only if now empty
            try:
                if not any(file_path.iterdir()):
                    file_path.rmdir()
            except OSError:
                pass
        else:
            file_path.unlink()
        # Clear filename on the task so the UI knows the file is gone.
        task.filename = ""
        logger.info("Remote API: deleted file '%s' for task %s", file_path.name, task_id)

        return FileActionResponse(
            task_id=task_id,
            action="deleted",
            detail=f"Deleted: {file_path.name}",
        )

    @app.get(
        "/api/queue/{task_id}/file",
        summary="Stream / preview the output file of a completed task",
    )
    async def preview_task_file(task_id: str, _: None = Depends(_require_auth)):
        """
        Serve the output file inline so iOS Safari can preview it.

        • Uses FastAPI FileResponse which handles Range requests automatically
          — required for iOS video seeking.
        • Content-Disposition: inline so Safari renders in-browser instead
          of forcing a download.
        • Only COMPLETED tasks with an existing file are served.
        • Same path-traversal guard as the delete endpoint.
        """
        task = _get_task_or_404(service, task_id)
        if task.status.name != "COMPLETED":
            raise HTTPException(status_code=400, detail="Task is not COMPLETED")

        file_path = _resolve_task_file(task)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")
        # BUG-BY: gallery-dl multi-file downloads set task.filename to a
        # directory (the account subfolder).  FileResponse raises RuntimeError
        # when passed a directory path.  Return 400 so the client knows it
        # cannot preview a multi-file download directly — use Taildrop instead.
        if file_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail="Task output is a folder (multi-file download) — use Taildrop to transfer",
            )

        media_type = (
            _EXTRA_MIME.get(file_path.suffix.lower())
            or mimetypes.guess_type(file_path.name)[0]
            or "application/octet-stream"
        )
        return FileResponse(
            path=str(file_path),
            media_type=media_type,
            filename=file_path.name,
            content_disposition_type="inline",
            headers={"Accept-Ranges": "bytes"},
        )

    @app.get(
        "/api/queue/{task_id}/fileinfo",
        response_model=FileInfoResponse,
        summary="Get file metadata for a completed task",
    )
    async def get_task_fileinfo(task_id: str, _: None = Depends(_require_auth)) -> FileInfoResponse:
        """
        Return file metadata (name, size, existence) without streaming the file.
        Used by the UI to decide which action buttons to show.
        """
        task = _get_task_or_404(service, task_id)
        raw = getattr(task, "filename", None) or ""
        exists = False
        size = 0
        name = ""
        if raw:
            p = Path(raw).resolve()
            allowed = config.download_dir.resolve()
            if p.is_relative_to(allowed) and p.exists():
                exists = True
                size = p.stat().st_size
                name = p.name

        return FileInfoResponse(
            task_id=task_id,
            filename=name,
            size_bytes=size,
            exists=exists,
            preview_url=f"/api/queue/{task_id}/file",
        )

    # ── Remote Convert ─────────────────────────────────────────────────────

    def _job_to_response(job: ConversionJob) -> ConvertJobResponse:
        """Serialise a ConversionJob to the API response model."""
        snap = job.snapshot()
        out = snap.get("output_filename", "") or ""
        return ConvertJobResponse(
            job_id=snap["job_id"],
            source_task_id=snap["source_task_id"],
            encoder_key=snap["encoder_key"],
            quality=snap["quality"],
            speed_preset=snap["speed_preset"],
            custom_crf=snap["custom_crf"],
            output_codec=snap.get("output_codec", "h264"),
            status=snap["status"],
            progress=snap["progress"],
            output_filename=Path(out).name if out else "",
            error_msg=snap["error_msg"],
            created_at=snap["created_at"],
            finished_at=snap["finished_at"],
            preview_url=f"/api/convert/{snap['job_id']}/file",
            output_deleted=snap.get("output_deleted", False),
        )

    @app.get(
        "/api/convert/encoders",
        response_model=list[EncoderOption],
        summary="List GPU/CPU encoders available on the server",
    )
    async def list_encoders(_: None = Depends(_require_auth)) -> list[EncoderOption]:
        """
        Return available encoder options detected on the server machine.
        CPU (libx264) is always present; GPU encoders appear only when
        the corresponding hardware and drivers are installed.

        Result is cached for 5 minutes inside detect_available_encoders().
        """
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")
        opts = remote_convert.get_available_encoders()
        return [EncoderOption(key=k, label=lbl) for k, lbl in opts]

    @app.post(
        "/api/queue/{task_id}/convert",
        response_model=ConvertJobResponse,
        summary="Start a remote conversion job for a completed download task",
    )
    async def start_convert(
        task_id: str,
        body: ConvertRequest,
        _: None = Depends(_require_auth),
    ) -> ConvertJobResponse:
        """
        Trigger FFmpeg conversion on a COMPLETED download task.

        • task must be COMPLETED and have an output file on disk
        • source file must reside inside download_dir (path traversal guard)
        • encoder_key / quality / speed_preset are validated server-side
        • returns immediately; progress arrives via SSE convert_progress events
        • at most 2 remote conversions run simultaneously (ConvertQueue)

        Security: encoder_key is validated against an explicit allowlist
        before being passed to FFmpeg — no arbitrary codec injection possible.
        """
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")

        task = _get_task_or_404(service, task_id)
        if task.status.name != "COMPLETED":
            raise HTTPException(status_code=400, detail="Task is not COMPLETED")

        file_path = _resolve_task_file(task)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Output file not found on disk")

        # BUG-BZ: gallery-dl multi-file downloads set task.filename to a
        # directory.  Scan for individual video files and start one conversion
        # job per file.  Progress for every job arrives via SSE convert_* events.
        if file_path.is_dir():
            from app.services.ffmpeg_convert_service import SUPPORTED_EXTS

            video_files = sorted(
                [
                    f
                    for f in file_path.rglob("*")
                    if (
                        f.is_file()
                        and f.suffix.lower().lstrip(".") in SUPPORTED_EXTS
                        and not f.name.endswith(".part.mp4")
                        and not f.stem.endswith("_iPhone")
                    )
                ]
            )
            if not video_files:
                raise HTTPException(
                    status_code=400,
                    detail="No convertible video files found in the multi-file download folder",
                )
            jobs = []
            for vf in video_files:
                try:
                    j = remote_convert.start_convert(
                        source_task_id=task_id,
                        file_path=vf,
                        encoder_key=body.encoder_key or "auto",
                        quality=body.quality or "standard",
                        speed_preset=body.speed_preset or "balanced",
                        custom_crf=body.custom_crf if body.custom_crf is not None else 23,
                        output_codec=body.output_codec or "h264",
                    )
                    jobs.append(j)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            # Return first job; all jobs' progress is broadcast via SSE.
            return _job_to_response(jobs[0])

        try:
            job = remote_convert.start_convert(
                source_task_id=task_id,
                file_path=file_path,
                encoder_key=body.encoder_key or "auto",
                quality=body.quality or "standard",
                speed_preset=body.speed_preset or "balanced",
                custom_crf=body.custom_crf if body.custom_crf is not None else 23,
                output_codec=body.output_codec or "h264",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return _job_to_response(job)

    @app.get(
        "/api/convert/{job_id}",
        response_model=ConvertJobResponse,
        summary="Get status and progress of a conversion job",
    )
    async def get_convert_job(job_id: str, _: None = Depends(_require_auth)) -> ConvertJobResponse:
        """Poll a conversion job. Prefer SSE convert_progress events instead."""
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")
        job = remote_convert.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Convert job not found")
        return _job_to_response(job)

    @app.post(
        "/api/convert/{job_id}/cancel",
        response_model=FileActionResponse,
        summary="Cancel an in-progress conversion job",
    )
    async def cancel_convert_job(job_id: str, _: None = Depends(_require_auth)) -> FileActionResponse:
        """
        Signal the FFmpeg worker to stop.

        The cancel is asynchronous: the job transitions to CANCELLED once
        FFmpeg exits and the SSE convert_cancelled event fires.
        """
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")
        ok = remote_convert.cancel_convert(job_id)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail="Job not found or already in terminal state",
            )
        return FileActionResponse(
            task_id=job_id, action="cancel_requested", detail="Cancellation signal sent"
        )

    @app.get(
        "/api/convert/{job_id}/file",
        summary="Stream / preview the converted MP4 output",
    )
    async def preview_convert_file(job_id: str, _: None = Depends(_require_auth)):
        """
        Serve the converted MP4 inline for iOS Safari preview.

        Only available once the job status is COMPLETED.
        Same Range-request support as the download preview endpoint.
        """
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")
        job = remote_convert.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Convert job not found")
        if job.status != "COMPLETED":
            raise HTTPException(status_code=400, detail="Conversion not completed yet")

        out_path = Path(job.output_filename).resolve()
        allowed = config.download_dir.resolve()
        if not out_path.is_relative_to(allowed):
            raise HTTPException(status_code=403, detail="Output file is outside download directory")
        if not out_path.exists():
            raise HTTPException(status_code=404, detail="Converted file not found on disk")

        return FileResponse(
            path=str(out_path),
            media_type="video/mp4",
            filename=out_path.name,
            content_disposition_type="inline",
            headers={"Accept-Ranges": "bytes"},
        )

    @app.delete(
        "/api/convert/{job_id}/file",
        response_model=FileActionResponse,
        summary="Delete the converted output file from the server's disk",
    )
    async def delete_convert_file(job_id: str, _: None = Depends(_require_auth)) -> FileActionResponse:
        """
        Permanently delete the converted MP4 output file from the laptop.

        The ConversionJob entry is retained in memory (job history stays
        visible) but output_deleted is set to True so the client can
        immediately hide the Delete / Preview buttons without polling.

        Only works for COMPLETED jobs that still have an output file on disk.

        Security:
        • output_filename is resolved against config.download_dir before
          deletion — prevents path-traversal (CWE-22).
        • Uses Path.unlink() — no subprocess, no shell=True (CWE-78).
        """
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")

        ok, reason = remote_convert.delete_convert_file(
            job_id=job_id,
            allowed_dir=config.download_dir,
        )

        if not ok:
            # Map well-known reasons to appropriate HTTP status codes.
            if "not found" in reason.lower():
                raise HTTPException(status_code=404, detail=reason)
            if "not COMPLETED" in reason or "No output" in reason:
                raise HTTPException(status_code=400, detail=reason)
            if "outside the allowed" in reason:
                raise HTTPException(status_code=403, detail=reason)
            raise HTTPException(status_code=500, detail=reason)

        job = remote_convert.get_job(job_id)
        filename = ""
        if job and job.output_filename:
            filename = Path(job.output_filename).name

        logger.info(
            "Remote API: deleted converted file '%s' for convert job %s",
            filename,
            job_id,
        )
        return FileActionResponse(
            task_id=job_id,
            action="deleted",
            detail=f"Deleted: {filename}",
        )

    @app.get(
        "/api/history/stats",
        response_model=HistoryStatsResponse,
        summary="Return download history statistics",
    )
    async def get_history_stats(_: None = Depends(_require_auth)):
        return service.get_history_stats()

    @app.get(
        "/api/history",
        response_model=HistoryListResponse,
        summary="Return download history with optional search/filter/pagination",
    )
    async def get_history(
        q: Optional[str] = Query(None, description="Search in title, URL, filename"),
        status: Optional[str] = Query(None, description="Filter by status (COMPLETED/FAILED/CANCELLED)"),
        platform: Optional[str] = Query(None, description="Filter by platform (youtube/tiktok/...)"),
        page: int = Query(1, ge=1, description="Page number (1-based)"),
        limit: int = Query(50, ge=1, le=500, description="Items per page"),
        _: None = Depends(_require_auth),
    ) -> HistoryListResponse:
        items = list(reversed(service.get_history()))
        if q:
            ql = q.lower()
            items = [
                x
                for x in items
                if ql in x.get("url", "").lower()
                or ql in x.get("title", "").lower()
                or ql in x.get("filename", "").lower()
            ]
        if status:
            items = [x for x in items if x.get("status") == status]
        if platform:
            items = [x for x in items if x.get("platform") == platform]
        total = len(items)
        start = (page - 1) * limit
        return HistoryListResponse(items=items[start : start + limit], total=total, page=page, limit=limit)

    @app.delete("/api/history/{task_id}", summary="Delete a single history entry")
    async def delete_history_entry(task_id: str, _: None = Depends(_require_auth)):
        service.delete_history_entry(task_id)
        return {"deleted": task_id}

    # ── File Browser ──────────────────────────────────────────────────────

    @app.get(
        "/api/files/browse",
        response_model=FileBrowseResponse,
        summary="Browse files/directories on the server within download_dir",
    )
    async def browse_files(
        path: Optional[str] = Query(None, description="Absolute path to browse; defaults to download_dir"),
        _: None = Depends(_require_auth),
    ) -> FileBrowseResponse:
        """
        List the contents of a directory on the server.

        Security:
        - The resolved path MUST be within config.download_dir.
        - Returns 400 if the path escapes download_dir.
        - Returns 404 if the path does not exist or is not a directory.
        """
        root = config.download_dir.resolve()

        if path is None:
            target = root
        else:
            try:
                target = Path(path).resolve()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid path") from None

        if not target.is_relative_to(root):
            raise HTTPException(
                status_code=400,
                detail="Path is outside the allowed download directory",
            )
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path does not exist")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")

        parent_path: Optional[str] = None
        if target != root:
            parent_path = str(target.parent)

        items: list[FileBrowseItem] = []
        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError as err:
            raise HTTPException(status_code=403, detail="Permission denied reading directory") from err

        for entry in entries:
            try:
                stat = entry.stat()
            except OSError:
                continue
            items.append(
                FileBrowseItem(
                    name=entry.name,
                    type="file" if entry.is_file() else "dir",
                    size=stat.st_size if entry.is_file() else None,
                    modified_at=stat.st_mtime,
                )
            )

        return FileBrowseResponse(
            current_path=str(target),
            parent_path=parent_path,
            items=items,
        )

    @app.post(
        "/api/files/convert",
        response_model=FileConvertJobResponse,
        summary="Start a conversion job on a local file (not tied to a download task)",
    )
    async def convert_file(
        body: FileConvertRequest, _: None = Depends(_require_auth)
    ) -> FileConvertJobResponse:
        """
        Convert any file on the server that lives within download_dir.

        Typical flow from iPhone:
          1. GET /api/files/browse  → pick a file path
          2. POST /api/files/convert { file_path, target_ext, encoder_key, ... }
          3. GET /api/convert/{job_id}  (or SSE events)  → track progress

        Security:
        - file_path resolved against config.download_dir (CWE-22 guard).
        - encoder_key validated against _VALID_ENCODERS allowlist.
        """
        if remote_convert is None:
            raise HTTPException(status_code=503, detail="Convert service not available")

        root = config.download_dir.resolve()
        try:
            file_path = Path(body.file_path).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid file_path") from None

        if not file_path.is_relative_to(root):
            raise HTTPException(
                status_code=400,
                detail="file_path is outside the allowed download directory",
            )
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found on server")
        if not file_path.is_file():
            raise HTTPException(status_code=400, detail="file_path is not a file")

        try:
            job = remote_convert.start_convert_from_path(
                file_path=file_path,
                encoder_key=body.encoder_key or "auto",
                quality=body.quality or "standard",
                speed_preset=body.speed_preset or "balanced",
                custom_crf=body.custom_crf if body.custom_crf is not None else 23,
                target_ext=body.target_ext or "mp4",
                output_codec=body.output_codec or "h264",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        logger.info(
            "Remote API: started standalone convert job %s for '%s'",
            job.job_id,
            file_path.name,
        )
        return FileConvertJobResponse(job_id=job.job_id)

    @app.delete(
        "/api/files/delete",
        response_model=FileDeleteResponse,
        summary="Delete a file or directory within download_dir",
    )
    async def delete_file(body: FileDeleteRequest, _: None = Depends(_require_auth)) -> FileDeleteResponse:
        """
        Permanently delete a file or directory from the server.

        Security:
        - Resolved path MUST be within config.download_dir (CWE-22).
        - Root download_dir itself is never deletable.
        - No shell=True, no subprocess.
        """
        root = config.download_dir.resolve()
        try:
            target = Path(body.path).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path") from None

        if not target.is_relative_to(root):
            raise HTTPException(
                status_code=400,
                detail="Path is outside the allowed download directory",
            )
        if target == root:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the root download directory",
            )
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path does not exist")

        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail=exc.strerror) from exc

        logger.info("Remote API: deleted '%s'", target)
        return FileDeleteResponse(
            path=body.path,
            action="deleted",
            detail=f"Deleted: {target.name}",
        )

    @app.get("/api/nodes", summary="List configured Taildrop target nodes")
    async def list_nodes(_: None = Depends(_require_auth)) -> list[str]:
        return config.taildrop_target_nodes

    @app.get("/api/files/serve", summary="Stream a file by absolute path (within download_dir)")
    async def serve_file(path: str = Query(...), _: None = Depends(_require_auth)):
        root = config.download_dir.resolve()
        try:
            target = Path(path).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path") from None
        if not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="Path outside download directory")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        mime = (
            _EXTRA_MIME.get(target.suffix.lower())
            or mimetypes.guess_type(target.name)[0]
            or "application/octet-stream"
        )
        return FileResponse(
            path=str(target),
            media_type=mime,
            filename=target.name,
            content_disposition_type="inline",
            headers={"Accept-Ranges": "bytes"},
        )

    @app.post(
        "/api/files/transfer",
        response_model=FileTransferResponse,
        summary="Send a file to selected Taildrop nodes",
    )
    async def transfer_file_by_path(
        body: FileTransferRequest, _: None = Depends(_require_auth)
    ) -> FileTransferResponse:
        if not config.taildrop_enabled:
            raise HTTPException(status_code=503, detail="Taildrop is disabled in settings")
        td = getattr(service, "taildrop", None)
        if td is None:
            raise HTTPException(status_code=503, detail="Taildrop service unavailable")
        if not body.nodes:
            raise HTTPException(status_code=400, detail="No nodes specified")
        root = config.download_dir.resolve()
        try:
            target = Path(body.path).resolve()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path") from None
        if not target.is_relative_to(root):
            raise HTTPException(status_code=400, detail="Path outside download directory")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        def _on_done(node: str) -> None:
            _broadcast("file_transfer_done", {"path": body.path, "node": node})

        def _on_error(node: str, error: str = "") -> None:
            _broadcast("file_transfer_failed", {"path": body.path, "node": node, "error": error})

        td.send_file_to_nodes(target, body.nodes, on_node_done=_on_done, on_node_error=_on_error)
        return FileTransferResponse(detail=f"Queued to: {', '.join(body.nodes)}")

    # ── SSE ───────────────────────────────────────────────────────────────

    @app.get("/api/events")
    async def sse_events(request: Request, _: None = Depends(_require_auth)):
        """
        Server-Sent Events stream.
        Sends an initial 'snapshot' event with the full queue, then pushes
        incremental updates as EventBus events fire.
        Keepalive comments are sent every _PING_INTERVAL seconds so iOS
        Safari does not close idle connections.
        """
        client_q: queue.Queue = queue.Queue(maxsize=200)
        with _sse_lock:
            _sse_clients.append(client_q)

        def _stream() -> Generator[str, None, None]:
            # Initial full snapshot so the client doesn't need a separate
            # GET /api/queue call on first connect.
            snapshot = [_task_to_dict(t) for t in service.get_all_tasks()]
            yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"

            # ── Convert-job snapshot ──────────────────────────────────────
            # Sent immediately after the download snapshot so that if the
            # client reconnects AFTER a convert_completed event was already
            # broadcast (e.g. SSE drop mid-conversion), it can restore the
            # correct COMPLETED / FAILED / CANCELLED state without polling.
            # Only non-terminal or recently-finished jobs are included so the
            # payload stays small (RemoteConvertService already caps at
            # MAX_JOBS = 100 and purges oldest terminal jobs automatically).
            if remote_convert is not None:
                convert_snap = [_job_to_response(job).model_dump() for job in remote_convert.get_all_jobs()]
                if convert_snap:
                    yield f"event: convert_snapshot\ndata: {json.dumps(convert_snap)}\n\n"

            last_ping = time.monotonic()
            try:
                while True:
                    try:
                        msg = client_q.get(timeout=1.0)
                        yield msg
                    except queue.Empty:
                        now = time.monotonic()
                        if now - last_ping >= _PING_INTERVAL:
                            yield ": ping\n\n"
                            last_ping = now
            finally:
                with _sse_lock:
                    try:
                        _sse_clients.remove(client_q)
                    except ValueError:
                        pass

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering
            },
        )

    # ── Web UI ────────────────────────────────────────────────────────────

    # No auth on GET / — HTML shell only; all actual API calls still require Bearer token.
    @app.get("/", response_class=HTMLResponse)
    async def web_ui():
        """Serve the mobile PWA web interface.

        Path resolution order (handles both source-run and PyInstaller bundle):
          1. Path(__file__).parent / "static" — works in source mode and in most
             frozen builds where __file__ resolves inside sys._MEIPASS/api/.
          2. sys._MEIPASS / "api" / "static" — explicit PyInstaller fallback for
             edge cases where __file__ does not resolve as expected inside the
             bundle (e.g. some one-file builds).
        Both paths target the same file when the bundle is built with:
          --add-data "api/static:api/static"  (macOS / Linux)
          --add-data "api/static;api/static"  (Windows)
        """
        # Primary path: works in source mode and standard onedir frozen builds.
        html_path = Path(__file__).parent / "static" / "index.html"

        # Fallback: explicit _MEIPASS lookup for PyInstaller frozen builds.
        if not html_path.exists() and getattr(sys, "frozen", False):
            html_path = Path(sys._MEIPASS) / "api" / "static" / "index.html"  # type: ignore[attr-defined]

        if html_path.exists():
            return HTMLResponse(
                content=html_path.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-cache"},
            )
        return HTMLResponse(
            content="<h1>OmniDL API</h1><p>Web UI not found.</p>",
            status_code=500,
        )

    return app


# ── Internal helpers ──────────────────────────────────────────────────────────


def _get_task_or_404(service: "DownloadService", task_id: str) -> DownloadTask:
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return task


# ── Public control functions ─────────────────────────────────────────────────


def is_api_running() -> bool:
    """Return True if the API server thread is alive."""
    with _server_lock:
        return _active_thread is not None and _active_thread.is_alive()


def stop_api_server(timeout: float = 8.0) -> None:
    """Gracefully stop the running API server.

    Signals uvicorn to shut down, waits up to *timeout* seconds for the
    thread to exit, then clears the module-level references.

    Safe to call when no server is running (no-op).
    """
    # BUG-BX: timeout raised from 4.0 to 8.0 — IocpProactor on Windows can
    # take >4s to drain its completion queue during shutdown, causing the
    # socket to remain bound when restart_api_server() tries to rebind
    # immediately after → [Errno 10048] address already in use.
    global _active_server, _active_thread

    with _server_lock:
        srv = _active_server
        thr = _active_thread
        _active_server = None
        _active_thread = None

    if srv is not None:
        # Tell uvicorn to exit its event loop
        srv.should_exit = True

    if thr is not None and thr.is_alive():
        thr.join(timeout=timeout)
        if thr.is_alive():
            logger.warning("API server thread did not stop within %.1fs", timeout)

    logger.info("OmniDL API server stopped.")


def _stop_and_wait(timeout: float = 8.0) -> None:
    """Stop the API server and add a brief OS socket-release pause.

    BUG-BX: On Windows, even after the uvicorn thread exits, the TCP stack
    can hold the port in TIME_WAIT for a short period.  A 0.5s sleep after
    join lets the OS release the socket before the caller tries to rebind.
    """
    stop_api_server(timeout=timeout)
    time.sleep(0.5)


def restart_api_server(
    service: "DownloadService",
    config: "ConfigManager",
    bus: "EventBus",
) -> Optional[threading.Thread]:
    """Stop any running server then start a fresh one.

    Used by the Settings tab when the user rotates the token or toggles
    the API back on.  Returns the new Thread (or None if api_enabled is False).
    """
    _stop_and_wait()  # BUG-BX: includes 0.5s socket-release sleep
    return start_api_server(service=service, config=config, bus=bus)


# ── Public startup function ───────────────────────────────────────────────────


def start_api_server(
    service: "DownloadService",
    config: "ConfigManager",
    bus: EventBus,
) -> Optional[threading.Thread]:
    """
    Start the FastAPI/uvicorn server in a daemon thread.

    Call this after DownloadService and DownloadManager are fully
    initialised but before window.mainloop().

    Returns the started Thread, or None if api_enabled is False.
    """
    if not config.api_enabled:
        logger.debug("OmniDL API server is disabled (api_enabled=False)")
        return None

    # Auto-generate a token on first enable — stored in OS credential store
    # (Windows Credential Manager / macOS Keychain) via config.set_api_token().
    # Falls back to config.json plaintext when keyring is unavailable.
    if not config.api_token:
        new_token = secrets.token_urlsafe(24)
        config.set_api_token(new_token)
        logger.info("OmniDL API: no token configured -- generated new token [stored in keyring]")
    if not config.api_token:
        logger.warning(
            "OmniDL API: token generation failed — server will run in OPEN MODE "
            "(no authentication) on %s:%d. Set an API token in Settings -> Remote API.",
            config.api_host,
            config.api_port,
        )

    # Instantiate RemoteConvertService before wiring the EventBus so that
    # auto_convert_tiktok_live can be subscribed inside the _bus_wired guard.
    # BUG-BU FIX: Use getattr() so that if start_api_server is called with a
    # ServiceFacade that pre-dates the taildrop property (e.g. an older build
    # started from Settings toggle), the call degrades gracefully to taildrop=None
    # instead of raising AttributeError and leaving the API permanently disabled.
    from app.services.remote_convert_service import RemoteConvertService

    remote_convert = RemoteConvertService(
        config=config,
        event_bus=bus,
        taildrop=getattr(service, "taildrop", None),  # BUG-BU: safe fallback
    )

    # Wire EventBus → SSE broadcaster before the server starts accepting
    # connections, so no events are missed.
    # BUG-CB: guard prevents duplicate subscriptions when restart_api_server()
    # calls start_api_server() again (token rotate, port change, etc.).
    global _bus_wired, _wired_remote_convert
    if not _bus_wired:
        _wire_event_bus(bus)
        _bus_wired = True
    # Always swap the auto_convert subscription to the current remote_convert instance.
    if _wired_remote_convert is not None:
        bus.unsubscribe(EventBus.DOWNLOAD_COMPLETED, _wired_remote_convert.auto_convert_tiktok_live)
    if remote_convert is not None:
        bus.subscribe(EventBus.DOWNLOAD_COMPLETED, remote_convert.auto_convert_tiktok_live)
    _wired_remote_convert = remote_convert

    app = create_app(service, config, remote_convert=remote_convert)

    try:
        import uvicorn
    except ImportError:
        logger.error(
            "uvicorn is not installed — cannot start the remote API server. "
            "Run: pip install 'fastapi[standard]'"
        )
        return None

    # When the Tailscale HTTPS Profile is active, bind to 127.0.0.1 on the
    # random internal port so only tailscale serve can reach the API from the
    # network.  Otherwise use the user-configured host/port (default: 0.0.0.0:7799).
    if getattr(config, "api_ts_https_enabled", False) and getattr(config, "api_ts_https_internal_port", 0):
        _bind_host = "127.0.0.1"
        _bind_port = config.api_ts_https_internal_port
        # Pre-verify the saved port is actually bindable.  On Windows, ports in
        # excluded ranges (Hyper-V / WSL2 reservations) return winerror 10013
        # even on loopback, causing uvicorn to fail silently.  Pick a new port
        # and persist it so subsequent restarts also use the working port.
        import random as _random
        import socket as _socket

        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s:
            try:
                _s.bind((_bind_host, _bind_port))
            except OSError:
                logger.warning(
                    "api_ts_https_internal_port %d is not bindable — picking a new port",
                    _bind_port,
                )
                for _ in range(30):
                    _candidate = _random.randint(50000, 65000)
                    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as _s2:
                        try:
                            _s2.bind((_bind_host, _candidate))
                            _bind_port = _candidate
                            config.set("api_ts_https_internal_port", _bind_port)
                            config.save()
                            logger.info("api_ts_https_internal_port updated to %d", _bind_port)
                            break
                        except OSError:
                            continue
    else:
        _bind_host = config.api_host
        _bind_port = config.api_port

    uv_config = uvicorn.Config(
        app=app,
        host=_bind_host,
        port=_bind_port,
        log_level="warning",
        access_log=False,
        # BUG-BT: log_config=None disables uvicorn's default logging setup.
        # Without this, uvicorn.Config.__init__ calls configure_logging() →
        # dictConfig() → DefaultFormatter.__init__ → sys.stdout.isatty().
        # In a PyInstaller --windowed EXE sys.stdout is None (no console),
        # causing:  AttributeError: 'NoneType' object has no attribute 'isatty'
        # OmniDL already has its own logging via setup_logging(), so we do not
        # need uvicorn's DefaultFormatter at all.
        log_config=None,
        # loop="asyncio" is the default and works on all platforms.
        # Do NOT use "uvloop" — it requires a separate install and may
        # not be available in the frozen PyInstaller bundle.
    )
    uv_server = uvicorn.Server(uv_config)

    def _run_server() -> None:
        _tok_status = "[set]" if config.api_token else "[NONE - open mode]"
        logger.info(
            "OmniDL API server listening on http://%s:%d  (token: %s)",
            _bind_host,
            _bind_port,
            _tok_status,
        )
        uv_server.run()

    with _server_lock:
        global _active_server, _active_thread
        _active_server = uv_server
        thread = threading.Thread(
            target=_run_server,
            daemon=True,  # exits automatically when the main process exits
            name="omnidl-api-server",
        )
        _active_thread = thread
        thread.start()
    return thread
