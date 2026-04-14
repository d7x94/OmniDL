"""
api/models.py
Pydantic request and response models for the OmniDL Remote API.
All models are intentionally flat (no nested required objects) so the
iOS web client can build requests with a simple JSON.stringify().
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator

# ── Requests ──────────────────────────────────────────────────────────────────

# Extract first URL from mixed clipboard/share text (e.g. Kuaishou share text).
_URL_RE = re.compile(r"https?://\S+")

class AnalyseRequest(BaseModel):
    """Analyse a URL and return metadata without starting a download."""
    url: str

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        m = _URL_RE.search(v)
        if not m:
            raise ValueError("URL must start with http:// or https://")
        url = m.group(0)
        # strip trailing punctuation that may follow URL in share text
        url = url.rstrip(".,;\"')")
        return url


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
        m = _URL_RE.search(v)
        if not m:
            raise ValueError("URL must start with http:// or https://")
        url = m.group(0)
        # strip trailing punctuation that may follow URL in share text
        url = url.rstrip(".,;\"')")
        return url

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
    # BUG-BT: source_engine was missing from AnalyseResponse.
    # When the analysis fell back from yt-dlp to gallery-dl (e.g. Instagram
    # photos), the client received no signal to forward source_engine="gallery_dl"
    # in the subsequent /api/download call.  The download therefore defaulted to
    # yt_dlp and failed with "no video in this post".
    # Fix: expose source_engine so Remote clients (iOS app, etc.) can echo it
    # back in DownloadRequest.source_engine and route correctly.
    source_engine: str = "yt_dlp"   # "yt_dlp" | "gallery_dl"


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


# ── Remote Convert ────────────────────────────────────────────────────────────

class ConvertRequest(BaseModel):
    """Start a remote conversion job for a completed download task."""
    encoder_key:  Optional[str] = "cpu"
    # "high" | "standard" | "small" | "custom"
    quality:      Optional[str] = "standard"
    # "quality" | "balanced" | "fast"
    speed_preset: Optional[str] = "balanced"
    # CRF value used when quality=="custom" (0–51)
    custom_crf:   Optional[int] = 23


class ConvertJobResponse(BaseModel):
    """Snapshot of a single ConversionJob."""
    job_id:          str
    source_task_id:  str
    encoder_key:     str
    quality:         str
    speed_preset:    str
    custom_crf:      int
    status:          str    # ConversionStatus string
    progress:        float  # 0.0 – 100.0
    output_filename: str    # basename of converted file, empty until COMPLETED
    error_msg:       str
    created_at:      float
    finished_at:     float
    preview_url:     str    # relative URL to stream the converted file
    # True once the converted output file has been deleted from disk.
    # Clients use this to hide the Delete/Preview buttons without re-polling.
    output_deleted:  bool = False


class EncoderOption(BaseModel):
    """One available encoder entry returned by GET /api/convert/encoders."""
    key:   str   # e.g. "nvenc"
    label: str   # e.g. "NVIDIA NVENC"


# ── Remote File Browse / Standalone Convert ───────────────────────────────────

class FileBrowseItem(BaseModel):
    """One entry in a directory listing."""
    name:        str            # basename
    type:        str            # "file" | "dir"
    size:        Optional[int]  # bytes; None for directories
    modified_at: float          # Unix timestamp


class FileBrowseResponse(BaseModel):
    """Directory listing returned by GET /api/files/browse."""
    current_path: str                  # absolute path on the server
    parent_path:  Optional[str]        # None when already at download_dir root
    items:        list[FileBrowseItem]


class FileConvertRequest(BaseModel):
    """Start a conversion job on an arbitrary local file (not tied to a task)."""
    # Absolute path on the server, must be within download_dir.
    file_path:     str
    target_ext:    Optional[str] = "mp4"
    encoder_key:   Optional[str] = "cpu"
    quality:       Optional[str] = "standard"
    speed_preset:  Optional[str] = "balanced"
    custom_crf:    Optional[int] = 23


class FileConvertJobResponse(BaseModel):
    """Returned by POST /api/files/convert — client polls /api/convert/{job_id}."""
    job_id: str


class FileDeleteRequest(BaseModel):
    """Delete a file or directory within download_dir."""
    path: str


class FileDeleteResponse(BaseModel):
    """Result of a file/directory delete from the file browser."""
    path: str
    action: str    # "deleted"
    detail: str = ""
