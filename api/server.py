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

import json
import logging
import queue
import secrets
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from api.models import (
    AnalyseRequest,
    AnalyseResponse,
    DownloadRequest,
    QueueActionResponse,
    TaskResponse,
)
from app.event_bus import EventBus
from domain.models.download_task import DownloadTask, MediaInfo

if TYPE_CHECKING:
    from app.services.download_service import DownloadService
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── SSE broadcast helpers ─────────────────────────────────────────────────────

_sse_clients: list[queue.Queue] = []
_sse_lock    = threading.Lock()
_PING_INTERVAL = 15  # seconds — keeps iOS Safari connections alive

# ── Runtime server state (module-level so stop/restart can reach it) ─────────
_active_server: "uvicorn.Server | None" = None   # type: ignore[name-defined]
_active_thread: threading.Thread | None = None
_active_bus:    "EventBus | None" = None          # held to allow re-wiring on restart
_server_lock = threading.Lock()  # guards _active_server / _active_thread


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
        "id":               task.id,
        "url":              task.url,
        "title":            task.title,
        "platform":         task.platform,
        "status":           snap["status"].name,
        "progress":         round(snap["progress"], 1),
        "speed":            snap["speed"],
        "eta":              snap["eta"],
        "downloaded_bytes": snap["downloaded_bytes"],
        "total_bytes":      snap["total_bytes"],
        "filename":         snap["filename"],
        "error_msg":        snap["error_msg"],
        "created_at":       task.created_at,
    }


# ── EventBus → SSE wiring ─────────────────────────────────────────────────────

def _wire_event_bus(bus: EventBus) -> None:
    """
    Subscribe to all relevant EventBus events and fan them out to SSE clients.
    Called once at server startup — subscriptions are permanent for the
    lifetime of the process.
    """
    def _on_started(task: DownloadTask)   -> None: _broadcast("started",   _task_to_dict(task))
    def _on_progress(task: DownloadTask)  -> None: _broadcast("progress",  _task_to_dict(task))
    def _on_completed(task: DownloadTask) -> None: _broadcast("completed", _task_to_dict(task))
    def _on_failed(task: DownloadTask)    -> None: _broadcast("failed",    _task_to_dict(task))
    def _on_cancelled(task: DownloadTask) -> None: _broadcast("cancelled", _task_to_dict(task))

    bus.subscribe(EventBus.DOWNLOAD_STARTED,   _on_started)
    bus.subscribe(EventBus.DOWNLOAD_PROGRESS,  _on_progress)
    bus.subscribe(EventBus.DOWNLOAD_COMPLETED, _on_completed)
    bus.subscribe(EventBus.DOWNLOAD_FAILED,    _on_failed)
    bus.subscribe(EventBus.DOWNLOAD_CANCELLED, _on_cancelled)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(service: "DownloadService", config: "ConfigManager") -> FastAPI:
    """Build and return the FastAPI application."""

    app = FastAPI(
        title="OmniDL Remote API",
        version="1.0.0",
        docs_url=None,    # disable Swagger UI — reduces attack surface
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

    _token: str = config.api_token   # captured at factory time, immutable

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
        if not _token:
            return   # auth disabled — local trusted network only
        provided = token  # query param first (SSE path)
        if not provided:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
        if not provided:
            raise HTTPException(status_code=401, detail="Authorization required")
        if not secrets.compare_digest(provided, _token):
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
        Timeout: 60 seconds (consistent with yt-dlp's own network timeout).
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
        done.wait(timeout=60)

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        if "info" not in result:
            raise HTTPException(status_code=408, detail="Analysis timed out after 60 s")

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
        )

    # ── Download ──────────────────────────────────────────────────────────

    @app.post("/api/download", response_model=TaskResponse)
    async def start_download(body: DownloadRequest, _: None = Depends(_require_auth)):
        """
        Enqueue a download job.
        A minimal MediaInfo is constructed from the request so the job can
        be queued immediately — yt-dlp fills in the real title during download.
        """
        info = MediaInfo(
            url=body.url,
            title=body.title or body.url[:80],
            platform=body.platform or "unknown",
            source_engine=body.source_engine or "yt_dlp",
            is_live=bool(body.is_live),  # forwarded from /api/analyse — avoids a second extract_info
        )
        try:
            task = service.start_download(
                url=body.url,
                media_info=info,
                format_id=body.format_id or config.default_quality,
                output_ext=body.output_ext or config.default_format,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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
        """Remove all COMPLETED / FAILED / CANCELLED tasks from the queue."""
        service.clear_finished()
        return {"status": "ok"}

    # ── History ───────────────────────────────────────────────────────────

    @app.get("/api/history")
    async def get_history(_: None = Depends(_require_auth)):
        """Return the full download history (newest first)."""
        return list(reversed(service.get_history()))

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
                "Cache-Control":    "no-cache",
                "X-Accel-Buffering": "no",   # disable nginx buffering
            },
        )

    # ── Web UI ────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def web_ui():
        """Serve the mobile PWA web interface."""
        html_path = Path(__file__).parent / "static" / "index.html"
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


def stop_api_server(timeout: float = 4.0) -> None:
    """Gracefully stop the running API server.

    Signals uvicorn to shut down, waits up to *timeout* seconds for the
    thread to exit, then clears the module-level references.

    Safe to call when no server is running (no-op).
    """
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


def restart_api_server(
    service: "DownloadService",
    config: "ConfigManager",
    bus: "EventBus",
) -> Optional[threading.Thread]:
    """Stop any running server then start a fresh one.

    Used by the Settings tab when the user rotates the token or toggles
    the API back on.  Returns the new Thread (or None if api_enabled is False).
    """
    stop_api_server()
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
        logger.info(
            "OmniDL API: no token configured — generated new token: %s",
            new_token,
        )

    # Wire EventBus → SSE broadcaster before the server starts accepting
    # connections, so no events are missed.
    _wire_event_bus(bus)

    app = create_app(service, config)

    try:
        import uvicorn
    except ImportError:
        logger.error(
            "uvicorn is not installed — cannot start the remote API server. "
            "Run: pip install 'fastapi[standard]'"
        )
        return None

    uv_config = uvicorn.Config(
        app=app,
        host=config.api_host,
        port=config.api_port,
        log_level="warning",
        access_log=False,
        # loop="asyncio" is the default and works on all platforms.
        # Do NOT use "uvloop" — it requires a separate install and may
        # not be available in the frozen PyInstaller bundle.
    )
    uv_server = uvicorn.Server(uv_config)

    def _run_server() -> None:
        logger.info(
            "OmniDL API server listening on http://%s:%d  (token: %s)",
            config.api_host,
            config.api_port,
            config.api_token[:8] + "...",   # show only prefix in logs
        )
        uv_server.run()

    with _server_lock:
        global _active_server, _active_thread
        _active_server = uv_server
        thread = threading.Thread(
            target=_run_server,
            daemon=True,   # exits automatically when the main process exits
            name="omnidl-api-server",
        )
        _active_thread = thread
        thread.start()
    return thread
