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
import mimetypes
import queue
import secrets
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
    ConvertJobResponse,
    ConvertRequest,
    DownloadRequest,
    EncoderOption,
    FileActionResponse,
    FileInfoResponse,
    QueueActionResponse,
    TaskResponse,
)
from app.event_bus import EventBus
from domain.models.conversion_job import ConversionJob
from domain.models.download_task import DownloadTask, MediaInfo

if TYPE_CHECKING:
    import uvicorn

    from app.services.download_service import DownloadService
    from app.services.remote_convert_service import RemoteConvertService
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

    # Taildrop transfer results — broadcast so the Remote UI can restore
    # the transfer button and show a completion / failure toast.
    # kwargs: task, dest_node  (and error for FAILED)
    def _on_taildrop_completed(task: DownloadTask, dest_node: str, **_kw) -> None:
        _broadcast("taildrop_completed", {
            "task_id":   task.id,
            "dest_node": dest_node,
        })

    def _on_taildrop_failed(task: DownloadTask, dest_node: str, error: str = "", **_kw) -> None:
        _broadcast("taildrop_failed", {
            "task_id":   task.id,
            "dest_node": dest_node,
            "error":     error,
        })

    bus.subscribe(EventBus.DOWNLOAD_STARTED,   _on_started)
    bus.subscribe(EventBus.DOWNLOAD_PROGRESS,  _on_progress)
    bus.subscribe(EventBus.DOWNLOAD_COMPLETED, _on_completed)
    bus.subscribe(EventBus.DOWNLOAD_FAILED,    _on_failed)
    bus.subscribe(EventBus.DOWNLOAD_CANCELLED, _on_cancelled)
    bus.subscribe(EventBus.TAILDROP_COMPLETED, _on_taildrop_completed)
    bus.subscribe(EventBus.TAILDROP_FAILED,    _on_taildrop_failed)

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

    bus.subscribe(EventBus.CONVERT_STARTED,   _on_convert_started)
    bus.subscribe(EventBus.CONVERT_PROGRESS,  _on_convert_progress)
    bus.subscribe(EventBus.CONVERT_COMPLETED, _on_convert_completed)
    bus.subscribe(EventBus.CONVERT_FAILED,    _on_convert_failed)
    bus.subscribe(EventBus.CONVERT_CANCELLED, _on_convert_cancelled)


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
            source_engine=info.source_engine,  # BUG-BT fix: forward engine choice to client
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
        """Remove all COMPLETED / FAILED / CANCELLED tasks from the queue."""
        service.clear_finished()
        return {"status": "ok"}

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
        allowed  = config.download_dir.resolve()
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
    async def transfer_to_device(
        task_id: str, _: None = Depends(_require_auth)
    ) -> FileActionResponse:
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
    async def delete_task_file(
        task_id: str, _: None = Depends(_require_auth)
    ) -> FileActionResponse:
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
    async def preview_task_file(
        task_id: str, _: None = Depends(_require_auth)
    ):
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

        media_type = (
            mimetypes.guess_type(file_path.name)[0]
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
    async def get_task_fileinfo(
        task_id: str, _: None = Depends(_require_auth)
    ) -> FileInfoResponse:
        """
        Return file metadata (name, size, existence) without streaming the file.
        Used by the UI to decide which action buttons to show.
        """
        task = _get_task_or_404(service, task_id)
        raw = getattr(task, "filename", None) or ""
        exists = False
        size   = 0
        name   = ""
        if raw:
            p = Path(raw).resolve()
            allowed = config.download_dir.resolve()
            if p.is_relative_to(allowed) and p.exists():
                exists = True
                size   = p.stat().st_size
                name   = p.name

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
        out  = snap.get("output_filename", "") or ""
        return ConvertJobResponse(
            job_id          = snap["job_id"],
            source_task_id  = snap["source_task_id"],
            encoder_key     = snap["encoder_key"],
            quality         = snap["quality"],
            speed_preset    = snap["speed_preset"],
            custom_crf      = snap["custom_crf"],
            status          = snap["status"],
            progress        = snap["progress"],
            output_filename = Path(out).name if out else "",
            error_msg       = snap["error_msg"],
            created_at      = snap["created_at"],
            finished_at     = snap["finished_at"],
            preview_url     = f"/api/convert/{snap['job_id']}/file",
            output_deleted  = snap.get("output_deleted", False),
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

        try:
            job = remote_convert.start_convert(
                source_task_id = task_id,
                file_path      = file_path,
                encoder_key    = body.encoder_key or "cpu",
                quality        = body.quality or "standard",
                speed_preset   = body.speed_preset or "balanced",
                custom_crf     = body.custom_crf if body.custom_crf is not None else 23,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return _job_to_response(job)

    @app.get(
        "/api/convert/{job_id}",
        response_model=ConvertJobResponse,
        summary="Get status and progress of a conversion job",
    )
    async def get_convert_job(
        job_id: str, _: None = Depends(_require_auth)
    ) -> ConvertJobResponse:
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
    async def cancel_convert_job(
        job_id: str, _: None = Depends(_require_auth)
    ) -> FileActionResponse:
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
        return FileActionResponse(task_id=job_id, action="cancel_requested",
                                  detail="Cancellation signal sent")

    @app.get(
        "/api/convert/{job_id}/file",
        summary="Stream / preview the converted MP4 output",
    )
    async def preview_convert_file(
        job_id: str, _: None = Depends(_require_auth)
    ):
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
        allowed  = config.download_dir.resolve()
        if not out_path.is_relative_to(allowed):
            raise HTTPException(status_code=403,
                                detail="Output file is outside download directory")
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
    async def delete_convert_file(
        job_id: str, _: None = Depends(_require_auth)
    ) -> FileActionResponse:
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
            filename, job_id,
        )
        return FileActionResponse(
            task_id=job_id,
            action="deleted",
            detail=f"Deleted: {filename}",
        )


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

            # ── Convert-job snapshot ──────────────────────────────────────
            # Sent immediately after the download snapshot so that if the
            # client reconnects AFTER a convert_completed event was already
            # broadcast (e.g. SSE drop mid-conversion), it can restore the
            # correct COMPLETED / FAILED / CANCELLED state without polling.
            # Only non-terminal or recently-finished jobs are included so the
            # payload stays small (RemoteConvertService already caps at
            # MAX_JOBS = 100 and purges oldest terminal jobs automatically).
            if remote_convert is not None:
                convert_snap = []
                for job in remote_convert.get_all_jobs():
                    s = job.snapshot()
                    out = s.get("output_filename", "") or ""
                    convert_snap.append({
                        "job_id":          s["job_id"],
                        "source_task_id":  s["source_task_id"],
                        "encoder_key":     s["encoder_key"],
                        "quality":         s["quality"],
                        "speed_preset":    s["speed_preset"],
                        "custom_crf":      s["custom_crf"],
                        "status":          s["status"],
                        "progress":        s["progress"],
                        "output_filename": Path(out).name if out else "",
                        "error_msg":       s["error_msg"],
                        "created_at":      s["created_at"],
                        "finished_at":     s["finished_at"],
                        "preview_url":     f"/api/convert/{s['job_id']}/file",
                        "output_deleted":  s.get("output_deleted", False),
                    })
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
                "Cache-Control":    "no-cache",
                "X-Accel-Buffering": "no",   # disable nginx buffering
            },
        )

    # ── Web UI ────────────────────────────────────────────────────────────

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

    # Instantiate RemoteConvertService — shares the same EventBus so convert
    # progress events flow through the existing SSE broadcaster automatically.
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

    app = create_app(service, config, remote_convert=remote_convert)

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
