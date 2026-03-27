"""
api/models.py
Pydantic request and response models for the OmniDL Remote API.
All models are intentionally flat (no nested required objects) so the
iOS web client can build requests with a simple JSON.stringify().
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator


# ── Requests ──────────────────────────────────────────────────────────────────

class AnalyseRequest(BaseModel):
    """Analyse a URL and return metadata without starting a download."""
    url: str

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class DownloadRequest(BaseModel):
    """Start a download job."""
    url: str
    # Human-readable title (shown in queue before yt-dlp resolves the real one).
    # Defaults to the URL if omitted.
    title: Optional[str] = None
    # Platform hint (e.g. "youtube", "tiktok").  Optional — only used for display.
    platform: Optional[str] = None
    # yt-dlp format selector string.  Empty → use config default.
    format_id: Optional[str] = None
    # Output container extension.  Empty → use config default.
    output_ext: Optional[str] = None
    # Which engine to use: "yt_dlp" (default) or "gallery_dl".
    source_engine: Optional[str] = "yt_dlp"
    # Forwarded from the /api/analyse response so the server can pre-wire
    # MediaInfo.is_live without a second extract_info call.  Optional so
    # existing clients that don't send this field keep working (defaults False).
    is_live: Optional[bool] = False

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("source_engine")
    @classmethod
    def _validate_engine(cls, v: Optional[str]) -> Optional[str]:
        allowed = {"yt_dlp", "gallery_dl", None}
        if v not in allowed:
            raise ValueError(f"source_engine must be one of {allowed - {None}}")
        return v


# ── Responses ─────────────────────────────────────────────────────────────────

class AnalyseResponse(BaseModel):
    """Metadata returned by the analysis step."""
    url: str
    title: str
    uploader: str
    duration: int           # seconds; 0 if unknown
    thumbnail: str
    platform: str
    formats: list[dict]     # raw yt-dlp format dicts — client picks format_id
    is_live: bool
    playlist_count: int     # 0 for single-item results


class TaskResponse(BaseModel):
    """Snapshot of a single DownloadTask."""
    id: str
    url: str
    title: str
    platform: str
    status: str             # DownloadStatus enum name, e.g. "DOWNLOADING"
    progress: float         # 0.0 – 100.0
    speed: str              # e.g. "3.2 MiB/s"
    eta: str                # e.g. "01:23"
    downloaded_bytes: int
    total_bytes: int
    filename: str
    error_msg: str
    created_at: float       # Unix timestamp


class QueueActionResponse(BaseModel):
    """Confirmation of a queue control action (pause / resume / cancel)."""
    task_id: str
    action: str             # "paused" | "resumed" | "cancelled"


class FileActionResponse(BaseModel):
    """Result of a file-level action on a completed task."""
    task_id: str
    action: str             # "transferred" | "deleted"
    detail: str = ""        # human-readable result or error message


class FileInfoResponse(BaseModel):
    """Metadata about the output file of a completed task."""
    task_id: str
    filename: str           # basename only
    size_bytes: int         # 0 if file no longer exists
    exists: bool
    preview_url: str        # relative URL to stream/preview the file
