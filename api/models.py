"""
api/models.py
Pydantic request and response models for the OmniDL Remote API.
All models are intentionally flat (no nested required objects) so the
iOS web client can build requests with a simple JSON.stringify().
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, SecretStr, field_validator

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
    # Echo back tiktok_room_id from AnalyseResponse so the download engine
    # can run BUG-TT-25 (signed room/info) without a second live check.
    tiktok_room_id: Optional[str] = None

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
        # "ig_cdn" is returned by /api/analyse for a pasted, pre-signed
        # Instagram/Facebook CDN link.  Omitting it here made every client
        # that echoes AnalyseResponse.source_engine back (the bundled web UI
        # included) fail the download with HTTP 422.
        allowed = {
            "yt_dlp",
            "gallery_dl",
            "kuaishou",
            "instagram_live",
            "waaw",
            "facebook_story",
            "ig_cdn",
            None,
        }
        if v not in allowed:
            raise ValueError(f"source_engine must be one of {allowed - {None}}")
        return v

    @field_validator("output_ext")
    @classmethod
    def _validate_output_ext(cls, v: Optional[str]) -> Optional[str]:
        # "ts" is the container the web UI sends for every live stream
        # (index.html doDownload) and the one the live monitor records into.
        allowed = {"mp4", "mkv", "webm", "mov", "mp3", "m4a", "ts", None, ""}
        if v not in allowed:
            raise ValueError(f"output_ext must be one of {allowed - {None, ''}}")
        return v


# ── Responses ─────────────────────────────────────────────────────────────────


class AnalyseResponse(BaseModel):
    """Metadata returned by the analysis step."""

    url: str
    title: str
    uploader: str
    duration: int  # seconds; 0 if unknown
    thumbnail: str
    platform: str
    formats: list[dict]  # raw yt-dlp format dicts — client picks format_id
    is_live: bool
    playlist_count: int  # 0 for single-item results
    # BUG-BT: source_engine was missing from AnalyseResponse.
    # When the analysis fell back from yt-dlp to gallery-dl (e.g. Instagram
    # photos), the client received no signal to forward source_engine="gallery_dl"
    # in the subsequent /api/download call.  The download therefore defaulted to
    # yt_dlp and failed with "no video in this post".
    # Fix: expose source_engine so Remote clients (iOS app, etc.) can echo it
    # back in DownloadRequest.source_engine and route correctly.
    source_engine: str = "yt_dlp"  # "yt_dlp" | "gallery_dl"
    # BUG-TT-25: tiktok_room_id found during analyse (via BUG-TT-06 live checker)
    # must be forwarded to /api/download so the engine can use the signed
    # room/info API instead of yt-dlp's unsigned path (which returns "not live").
    tiktok_room_id: str = ""


class TaskResponse(BaseModel):
    """Snapshot of a single DownloadTask."""

    id: str
    url: str
    title: str
    platform: str
    status: str  # DownloadStatus enum name, e.g. "DOWNLOADING"
    progress: float  # 0.0 – 100.0
    speed: str  # e.g. "3.2 MiB/s"
    eta: str  # e.g. "01:23"
    downloaded_bytes: int
    total_bytes: int
    filename: str
    error_msg: str
    created_at: float  # Unix timestamp
    is_live: bool = False


class ClearItemsRequest(BaseModel):
    """Body for DELETE /api/queue/items."""

    ids: list[str]


class QueueActionResponse(BaseModel):
    """Confirmation of a queue control action (pause / resume / cancel)."""

    task_id: str
    action: str  # "paused" | "resumed" | "cancelled"


class FileActionResponse(BaseModel):
    """Result of a file-level action on a completed task."""

    task_id: str
    action: str  # "transferred" | "deleted"
    detail: str = ""  # human-readable result or error message


class FileInfoResponse(BaseModel):
    """Metadata about the output file of a completed task."""

    task_id: str
    filename: str  # basename only
    size_bytes: int  # 0 if file no longer exists
    exists: bool
    preview_url: str  # relative URL to stream/preview the file


# ── Remote Convert ────────────────────────────────────────────────────────────


# Output containers both convert endpoints accept.  "webm" is excluded on
# purpose: the pipeline emits H.264 + AAC and remuxes with "-c copy", which the
# WebM muxer rejects (VP8/VP9/AV1 + Vorbis/Opus only).
_ALLOWED_TARGET_EXTS: set = {"mp4", "mkv", "mov", "avi", "mp3", None}


class ConvertRequest(BaseModel):
    """Start a remote conversion job for a completed download task."""

    encoder_key: Optional[str] = "cpu"
    # "high" | "standard" | "small" | "custom"
    quality: Optional[str] = "standard"
    # "quality" | "balanced" | "fast"
    speed_preset: Optional[str] = "balanced"
    # CRF value used when quality=="custom" (0–51)
    custom_crf: Optional[int] = 23
    # "h264" | "hevc" | "av1" — rejected with 422 when the server's FFmpeg
    # build cannot encode it (GET /api/convert/codecs lists what is available)
    output_codec: Optional[str] = "h264"
    # Transcribe the audio to a sidecar .srt with FFmpeg's whisper filter.
    # Requires an FFmpeg build compiled with --enable-whisper; the server
    # returns 422 when it is unavailable.
    generate_subtitles: Optional[bool] = False
    # ISO-639-1 code or "auto" for automatic detection
    subtitle_language: Optional[str] = "auto"
    # "tiny" | "base" | "small" | "medium" — GET /api/convert/codecs lists what
    # is available. Larger models are more accurate but slower and bigger to
    # download on first use.
    subtitle_model: Optional[str] = "base"
    # Score the output against the source with VMAF (roughly doubles job time)
    compute_vmaf: Optional[bool] = False
    # Output container. Matches FileConvertRequest so both convert entry points
    # offer the same formats; previously this endpoint was hard-wired to MP4.
    target_ext: Optional[str] = "mp4"

    @field_validator("target_ext")
    @classmethod
    def _validate_ext(cls, v: Optional[str]) -> Optional[str]:
        if v not in _ALLOWED_TARGET_EXTS:
            raise ValueError(f"target_ext must be one of {_ALLOWED_TARGET_EXTS - {None}}")
        return v


class ConvertJobResponse(BaseModel):
    """Snapshot of a single ConversionJob."""

    job_id: str
    source_task_id: str
    encoder_key: str
    quality: str
    speed_preset: str
    custom_crf: int
    output_codec: str = "h264"
    status: str  # ConversionStatus string
    progress: float  # 0.0 – 100.0
    output_filename: str  # basename of converted file, empty until COMPLETED
    error_msg: str
    created_at: float
    finished_at: float
    preview_url: str  # relative URL to stream the converted file
    # True once the converted output file has been deleted from disk.
    # Clients use this to hide the Delete/Preview buttons without re-polling.
    output_deleted: bool = False
    # basename of the generated .srt, empty when not requested or no speech found
    subtitle_filename: str = ""
    # why subtitle generation produced nothing; the conversion itself still succeeded
    subtitle_error: str = ""
    # mean VMAF score 0-100 of output vs source, None when not requested
    vmaf_score: Optional[float] = None
    # True for a transcribe-only job: there is no converted video, so a client
    # must keep offering "Convert" for this file instead of treating the job as
    # a finished conversion.
    subtitles_only: bool = False


class EncoderOption(BaseModel):
    """One available encoder entry returned by GET /api/convert/encoders."""

    key: str  # e.g. "nvenc"
    label: str  # e.g. "NVIDIA NVENC"


class CodecOption(BaseModel):
    """One available output codec returned by GET /api/convert/codecs."""

    key: str  # "h264" | "hevc" | "av1"
    label: str  # human-readable description


class SubtitleModelOption(BaseModel):
    """One whisper model entry returned by GET /api/convert/codecs."""

    key: str  # "tiny" | "base" | "small" | "medium"
    label: str  # human-readable description, includes download size
    size_mb: int  # download size of the model file


class ConvertCapabilities(BaseModel):
    """What the server's FFmpeg build can do, returned by GET /api/convert/codecs."""

    codecs: list[CodecOption]
    # True when the FFmpeg build has the whisper filter (auto-subtitles)
    subtitles: bool
    # Languages accepted by ConvertRequest.subtitle_language
    subtitle_languages: list[str]
    # Models accepted by ConvertRequest.subtitle_model, smallest first
    subtitle_models: list[SubtitleModelOption]


# ── Remote File Browse / Standalone Convert ───────────────────────────────────


class FileBrowseItem(BaseModel):
    """One entry in a directory listing."""

    name: str  # basename
    type: str  # "file" | "dir"
    size: Optional[int]  # bytes; None for directories
    modified_at: float  # Unix timestamp


class FileBrowseResponse(BaseModel):
    """Directory listing returned by GET /api/files/browse."""

    current_path: str  # absolute path on the server
    parent_path: Optional[str]  # None when already at download_dir root
    items: list[FileBrowseItem]


class FileConvertRequest(BaseModel):
    """Start a conversion job on an arbitrary local file (not tied to a task)."""

    # Absolute path on the server, must be within download_dir.
    file_path: str
    target_ext: Optional[str] = "mp4"
    encoder_key: Optional[str] = "cpu"
    quality: Optional[str] = "standard"
    speed_preset: Optional[str] = "balanced"
    custom_crf: Optional[int] = 23
    output_codec: Optional[str] = "h264"
    generate_subtitles: Optional[bool] = False
    subtitle_language: Optional[str] = "auto"
    subtitle_model: Optional[str] = "base"
    compute_vmaf: Optional[bool] = False

    @field_validator("target_ext")
    @classmethod
    def _validate_ext(cls, v: Optional[str]) -> Optional[str]:
        if v not in _ALLOWED_TARGET_EXTS:
            raise ValueError(f"target_ext must be one of {_ALLOWED_TARGET_EXTS - {None}}")
        return v


class SubtitleRequest(BaseModel):
    """Start a subtitle-only job on a completed download task."""

    # ISO-639-1 code or "auto"; rejected with 422 when unknown
    subtitle_language: Optional[str] = "auto"
    # "tiny" | "base" | "small" | "medium"
    subtitle_model: Optional[str] = "base"


class FileSubtitleRequest(SubtitleRequest):
    """Start a subtitle-only job on an arbitrary local file."""

    # Absolute path on the server, must be within download_dir.
    file_path: str


class FileConvertJobResponse(BaseModel):
    """Returned by POST /api/files/convert — client polls /api/convert/{job_id}."""

    job_id: str


class FileRenameRequest(BaseModel):
    """Rename the output file of a completed task."""

    new_name: str  # new basename (with extension); sanitised server-side


class FileDeleteRequest(BaseModel):
    """Delete a file or directory within download_dir."""

    path: str


class FileDeleteResponse(BaseModel):
    """Result of a file/directory delete from the file browser."""

    path: str
    action: str  # "deleted"
    detail: str = ""


class FileRenameByPathRequest(BaseModel):
    """Rename a file within download_dir, addressed by path (file browser)."""

    path: str
    new_name: str  # new basename (with extension); sanitised server-side


class FileRenameByPathResponse(BaseModel):
    """Result of a file-browser rename."""

    path: str
    action: str  # "renamed"
    detail: str = ""


class FileTransferRequest(BaseModel):
    """Send a file to one or more Taildrop nodes."""

    path: str
    nodes: list[str]


class FileTransferResponse(BaseModel):
    detail: str = ""


class HistoryListResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    limit: int


class HistoryStatsResponse(BaseModel):
    total: int
    total_bytes: int
    by_platform: dict
    by_status: dict


class ClipboardAnalyseRequest(BaseModel):
    """Analyse a URL from the Remote client's clipboard."""

    url: str

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        m = _URL_RE.search(v)
        if not m:
            raise ValueError("URL must start with http:// or https://")
        url = m.group(0)
        url = url.rstrip(".,;\"')")
        return url


class MonitorAddRequest(BaseModel):
    """Add a profile / live URL to the live monitor watch list."""

    url: str

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        # Without this, any string was accepted: LiveMonitorService._classify()
        # falls through to a non-profile watch and the poll loop then re-checks
        # a value that can never resolve.  Mirrors DownloadRequest.url.
        m = _URL_RE.search(v)
        if not m:
            raise ValueError("URL must start with http:// or https://")
        return m.group(0).rstrip(".,;\"')")


class MonitorItemResponse(BaseModel):
    id: str
    url: str
    state: str
    title: str = ""
    platform: str = ""
    task_id: Optional[str] = None
    error_msg: str = ""
    is_profile_watch: bool = False
    username: str = ""
    filename: str = ""
    paused: bool = False
    added_at: float = 0.0
    last_check: float = 0.0
    rate_limited_until: float = 0.0


class MonitorIntervalRequest(BaseModel):
    interval: int


class MonitorListResponse(BaseModel):
    items: list[MonitorItemResponse]
    interval: int = 30
    paused: bool = False


class ArchiveCompressRequest(BaseModel):
    """Compress one or more files/dirs (within download_dir) into a .zip/.7z archive."""

    sources: list[str]
    fmt: str
    archive_name: Optional[str] = "archive"
    password: Optional[SecretStr] = None
    # 7z only — real header/filename encryption. ZIP cannot encrypt filenames
    # (format limitation); requesting this with fmt="zip" is a 422.
    encrypt_header: bool = False
    individually: bool = False

    @field_validator("fmt")
    @classmethod
    def _validate_fmt(cls, v: str) -> str:
        if v not in ("zip", "7z"):
            raise ValueError("fmt must be 'zip' or '7z'")
        return v


class ArchiveExtractRequest(BaseModel):
    """Extract an archive (within download_dir) into dest_dir (defaults under download_dir)."""

    archive_path: str
    dest_dir: Optional[str] = None
    password: Optional[SecretStr] = None


class ArchiveContentsRequest(BaseModel):
    """List archive members without extracting. POST (not GET) so password never hits a query string."""

    archive_path: str
    password: Optional[SecretStr] = None


class ArchiveMemberResponse(BaseModel):
    name: str
    size: int
    compressed_size: int
    is_dir: bool


class ArchiveContentsResponse(BaseModel):
    members: list[ArchiveMemberResponse]


class ArchiveExtractResponse(BaseModel):
    dest_dir: str
    extracted_paths: list[str]
    total_bytes: int


# ── UI language ───────────────────────────────────────────────────────────────


class LanguageOption(BaseModel):
    """One selectable UI language."""

    code: str
    label: str


class LanguageResponse(BaseModel):
    """Current UI language plus everything the client may switch to."""

    language: str
    available: list[LanguageOption]


class LanguageRequest(BaseModel):
    """Set the UI language.  Unsupported codes are rejected with 422."""

    language: str

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        from utils.i18n import LANGUAGES, normalize

        code = normalize(v)
        # normalize() falls back to the default for anything it does not know,
        # so an unsupported code would be accepted silently.  Reject it unless
        # the input really named the language that came back.
        if v.strip().lower().replace("_", "-").split("-")[0] != code:
            raise ValueError(f"language must be one of: {', '.join(LANGUAGES)}")
        return code
