"""
app/services/whisper_subtitle_service.py
Local speech-to-subtitle generation via FFmpeg's ``whisper`` audio filter.

FFmpeg 9.0.1 full builds are compiled with ``--enable-whisper``, which embeds
whisper.cpp as an audio filter.  Given a ``ggml-*.bin`` model file it transcribes
the audio track of any media file straight to SRT — entirely offline, no cloud
service and no API key.

Model files are **not** bundled with OmniDL (the smallest useful one is 74 MB).
They are downloaded on demand into the platform user-data directory the first
time a user asks for subtitles, and reused from then on.

Availability
────────────
``essentials_build`` FFmpeg has no whisper filter.  Call :func:`is_whisper_supported`
before offering the feature; every entry point degrades to "not supported" rather
than failing mid-conversion.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.services.ffmpeg_convert_service import escape_filter_path
from utils.ffmpeg_locator import locate_ffmpeg
from utils.i18n import t

logger = logging.getLogger(__name__)

_WIN_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
_VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"


@dataclass(frozen=True)
class WhisperModel:
    """One downloadable whisper.cpp model."""

    key: str
    filename: str
    size_mb: int
    # i18n key, not display text — resolve with model_label() so the label
    # follows the active language on the desktop combo and the Web API alike.
    label_key: str


# Ordered smallest → largest.  "base" is the default: good enough for clear
# speech, small enough that the first-use download is not painful.
WHISPER_MODELS: tuple[WhisperModel, ...] = (
    WhisperModel("tiny", "ggml-tiny.bin", 74, "subs.model.tiny"),
    WhisperModel("base", "ggml-base.bin", 141, "subs.model.base"),
    WhisperModel("small", "ggml-small.bin", 465, "subs.model.small"),
    WhisperModel("medium", "ggml-medium.bin", 1463, "subs.model.medium"),
)

_MODELS_BY_KEY: dict[str, WhisperModel] = {m.key: m for m in WHISPER_MODELS}


def model_label(model: WhisperModel) -> str:
    """Display name for *model* in the active UI language."""
    return t(model.label_key)


def language_label(code: str, fallback: str) -> str:
    """Display name for a subtitle language code.

    Every entry but "auto" is an endonym that reads the same in every UI
    language, so only "auto" carries a catalogue key.
    """
    return t("subs.lang.auto") if code == "auto" else fallback

DEFAULT_MODEL_KEY = "base"

# Languages whisper.cpp accepts, restricted to the ones OmniDL's source sites
# realistically produce.  "auto" lets whisper detect the language itself.
# This doubles as the Web API allowlist — the value reaches an FFmpeg filter
# string, so it must never be free-form user input.
VALID_LANGUAGES: frozenset[str] = frozenset(
    {
        "auto",
        "vi",
        "en",
        "zh",
        "ja",
        "ko",
        "th",
        "id",
        "ms",
        "tl",
        "fr",
        "de",
        "es",
        "pt",
        "it",
        "ru",
        "hi",
        "ar",
        "tr",
        "nl",
        "pl",
    }
)

# Containers whose audio track can be transcribed.  Video formats mirror
# ffmpeg_convert_service.SUPPORTED_EXTS; the audio-only ones are here because a
# podcast or a ripped soundtrack is a perfectly good subtitle source even though
# the convert pipeline cannot re-encode it as video.
TRANSCRIBABLE_EXTS: frozenset[str] = frozenset(
    {
        "mp4",
        "mkv",
        "webm",
        "avi",
        "mov",
        "flv",
        "wmv",
        "m4v",
        "ts",
        "mpeg",
        "mpg",
        "3gp",
        "mp3",
        "m4a",
        "aac",
        "wav",
        "flac",
        "ogg",
        "opus",
        "wma",
    }
)


def is_transcribable(path: Path) -> bool:
    """Return ``True`` when *path* looks like a media file whisper can read."""
    return path.suffix.lower().lstrip(".") in TRANSCRIBABLE_EXTS


# SUBTITLE_LANGUAGE_OPTIONS drives the desktop combo box.
SUBTITLE_LANGUAGE_OPTIONS: list[tuple[str, str]] = [
    # Only "auto" is translated — the rest are endonyms, identical in every UI
    # language, exactly like the language picker itself.
    ("auto", ""),
    ("vi", "Tiếng Việt"),
    ("en", "English"),
    ("zh", "中文"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("th", "ไทย"),
    ("id", "Bahasa Indonesia"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ru", "Русский"),
]


class SubtitleError(RuntimeError):
    """Raised when subtitle generation cannot be completed."""


# ── Model storage ─────────────────────────────────────────────────────────────


def _data_dir() -> Path:
    """Return the platform user-data directory (mirrors main.py::_get_data_dir)."""
    try:
        from platformdirs import user_data_dir

        return Path(user_data_dir("OmniDL", appauthor=False))
    except ImportError:
        return Path.home() / ".omnidl"


def model_dir() -> Path:
    """Return the directory holding downloaded whisper models."""
    return _data_dir() / "whisper_models"


def model_path(model_key: str) -> Path:
    """Return the on-disk path a model *would* occupy (may not exist yet)."""
    model = _MODELS_BY_KEY.get(model_key)
    if model is None:
        raise SubtitleError(t("subs.err.invalid_model", model=model_key))
    return model_dir() / model.filename


def is_model_installed(model_key: str) -> bool:
    """Return ``True`` when the model file is present and non-empty."""
    try:
        path = model_path(model_key)
    except SubtitleError:
        return False
    return path.is_file() and path.stat().st_size > 1_000_000


def installed_models() -> list[str]:
    """Return the keys of every model already downloaded, smallest first."""
    return [m.key for m in WHISPER_MODELS if is_model_installed(m.key)]


def _raise_cancelled() -> None:
    """Raise the convert pipeline's cancel exception.

    Imported lazily to avoid the circular import with ffmpeg_convert_service;
    reusing that exception means a cancel during the model download travels the
    same path as a cancel during transcription.
    """
    from app.services.ffmpeg_convert_service import ConversionCancelledError

    raise ConversionCancelledError(t("convert.cancelled"))


def _download(
    url: str,
    dest: Path,
    on_progress: Optional[Callable[[float], None]],
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """Stream *url* to *dest* atomically, reporting 0–100 progress.

    The cancel check lives in the read loop rather than in *on_progress* so a
    server that omits Content-Length is still interruptible.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        # url is always built from the https literals _HF_BASE / _VAD_URL
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310  # nosec B310
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        _raise_cancelled()
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(min(100.0, done * 100.0 / total))
        if total and tmp.stat().st_size != total:
            raise SubtitleError(t("subs.err.incomplete_download", got=tmp.stat().st_size, total=total))
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# One lock per model file.  A single global lock made a job that only needs
# "tiny" (74 MB) wait behind another job downloading "medium" (1.4 GB).
_download_locks: dict[str, threading.Lock] = {}
_download_locks_guard = threading.Lock()


def _lock_for(filename: str) -> threading.Lock:
    with _download_locks_guard:
        return _download_locks.setdefault(filename, threading.Lock())


def ensure_model(
    model_key: str = DEFAULT_MODEL_KEY,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Return the path to *model_key*, downloading it on first use.

    Serialised by a per-file lock so two concurrent conversions asking for the
    same model do not download it twice into the same file, while a job wanting
    a different model is not made to wait.
    """
    model = _MODELS_BY_KEY.get(model_key)
    if model is None:
        raise SubtitleError(t("subs.err.invalid_model", model=model_key))

    dest = model_path(model_key)
    if is_model_installed(model_key):
        return dest

    with _lock_for(model.filename):
        if is_model_installed(model_key):
            return dest
        logger.info("Downloading whisper model %s (~%d MB)…", model.filename, model.size_mb)
        _download(f"{_HF_BASE}/{model.filename}", dest, on_progress, cancel_event)
        logger.info("Whisper model ready: %s", dest)
    return dest


def ensure_vad_model() -> Optional[Path]:
    """Return the Silero VAD model path, downloading it if needed.

    VAD skips silent stretches, which both speeds transcription up and stops
    whisper hallucinating text over background noise.  It is small (~0.9 MB)
    so it is fetched alongside the first real model.  Returns ``None`` on any
    failure — VAD is an optimisation, never a hard requirement.
    """
    dest = model_dir() / "ggml-silero-v5.1.2.bin"
    if dest.is_file() and dest.stat().st_size > 100_000:
        return dest
    try:
        with _lock_for(dest.name):
            if dest.is_file() and dest.stat().st_size > 100_000:
                return dest
            _download(_VAD_URL, dest, None)
        return dest
    except Exception as exc:
        logger.warning("VAD model download failed (continuing without VAD): %s", exc)
        return None


# ── Capability probe ──────────────────────────────────────────────────────────

_whisper_supported: Optional[bool] = None
_whisper_probe_lock = threading.Lock()


def is_whisper_supported(ffmpeg_bin: Optional[Path] = None) -> bool:
    """Return ``True`` when this FFmpeg build has the ``whisper`` filter.

    Probes with ``-h filter=whisper``, which is instant and does not touch the
    GPU (unlike ``-filters``, which is slow behind a Scoop shim).  The result is
    cached for the process lifetime — the binary cannot change under a running
    app.  Pass an explicit *ffmpeg_bin* to bypass the cache (used by tests).
    """
    global _whisper_supported  # noqa: PLW0603

    if ffmpeg_bin is None:
        if _whisper_supported is not None:
            return _whisper_supported
        with _whisper_probe_lock:
            if _whisper_supported is not None:
                return _whisper_supported
            _whisper_supported = _probe_whisper(None)
            return _whisper_supported
    return _probe_whisper(ffmpeg_bin)


def _probe_whisper(ffmpeg_bin: Optional[Path]) -> bool:
    if ffmpeg_bin is None:
        loc = locate_ffmpeg()
        if loc is None:
            return False
        ffmpeg_bin = Path(loc.ffmpeg_bin)
    try:
        result = subprocess.run(
            [str(ffmpeg_bin), "-hide_banner", "-h", "filter=whisper"],
            capture_output=True,
            timeout=30,
            creationflags=_WIN_NO_WINDOW,
        )
        out = (result.stdout or b"") + (result.stderr or b"")
        # A build without the filter prints "Unknown filter 'whisper'".
        return b"whisper AVOptions" in out
    except Exception as exc:
        logger.debug("is_whisper_supported: probe failed: %s", exc)
        return False


# ── Subtitle generation ───────────────────────────────────────────────────────


def _renumber_srt(srt: Path) -> None:
    """Rewrite cue indices to start at 1.

    FFmpeg's whisper filter emits a 0-based index.  The SRT spec is 1-based and
    strict players (iOS/QuickTime) drop the first cue when it is numbered 0.
    """
    try:
        text = srt.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    out: list[str] = []
    for i, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines[0] = str(i)
        else:
            lines.insert(0, str(i))
        out.append("\n".join(lines))
    srt.write_text("\n\n".join(out) + "\n", encoding="utf-8")


def _free_srt_path(preferred: Path) -> Path:
    """Return *preferred*, or ``name_1.srt`` / ``name_2.srt`` … if it is taken."""
    if not preferred.exists():
        return preferred
    i = 1
    while True:
        candidate = preferred.with_name(f"{preferred.stem}_{i}.srt")
        if not candidate.exists():
            return candidate
        i += 1


def build_whisper_filter(
    model: Path,
    language: str = "auto",
    destination: Optional[Path] = None,
    translate: bool = False,
    vad_model: Optional[Path] = None,
    max_len: int = 42,
    use_gpu: bool = True,
) -> str:
    """Return the ``whisper=…`` filter string for ``-af``.

    *language* must already be a member of :data:`VALID_LANGUAGES`; callers that
    accept remote input validate it before reaching here.
    """
    parts = [
        f"model={escape_filter_path(model)}",
        f"language={language}",
        "format=srt",
        f"max_len={max_len}",
    ]
    if destination is not None:
        parts.append(f"destination={escape_filter_path(destination)}")
    if translate:
        parts.append("translate=true")
    if vad_model is not None:
        parts.append(f"vad_model={escape_filter_path(vad_model)}")
    if not use_gpu:
        parts.append("use_gpu=false")
    return "whisper=" + ":".join(parts)


def generate_srt(
    ffmpeg_bin: Path,
    source: Path,
    output_srt: Path,
    duration_s: float = 0.0,
    model_key: str = DEFAULT_MODEL_KEY,
    language: str = "auto",
    translate: bool = False,
    use_vad: bool = True,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Path:
    """Transcribe *source*'s audio into *output_srt* and return its path.

    Runs a second, audio-only FFmpeg pass (``-vn -f null -``) so it never
    touches the converted video.  Raises :class:`SubtitleError` when the build
    lacks the filter, the language is invalid, or FFmpeg produces no output.
    """
    # Imported lazily: whisper_subtitle_service is imported by the convert
    # service, so a module-level import would be circular.
    from app.services.ffmpeg_convert_service import (
        ConversionCancelledError,
        ConversionError,
        FfmpegConvertService,
    )

    if language not in VALID_LANGUAGES:
        raise SubtitleError(t("subs.err.invalid_language", language=language))
    if not is_whisper_supported(ffmpeg_bin):
        raise SubtitleError(t("subs.err.no_whisper"))

    if cancel_event is not None and cancel_event.is_set():
        _raise_cancelled()

    # The first run downloads 74 MB – 1.4 GB before FFmpeg even starts.  Feed
    # that into the job's progress bar (mapped onto the first 5%) so the UI is
    # not frozen at 0% for minutes, and pass the cancel event through so the
    # Cancel button works during the download too.
    def _model_progress(pct: float) -> None:
        if on_progress:
            on_progress(pct * 0.05)

    model = ensure_model(model_key, _model_progress, cancel_event)
    vad = ensure_vad_model() if use_vad else None

    if cancel_event is not None and cancel_event.is_set():
        _raise_cancelled()

    output_srt.parent.mkdir(parents=True, exist_ok=True)
    # Never clobber an existing .srt — it may be a sidecar downloaded with the
    # video or a transcript the user has already corrected by hand.
    output_srt = _free_srt_path(output_srt)

    # Write to a temp file next to the target, then rename, so a cancelled or
    # crashed run never leaves a half-written .srt that looks complete.
    # UUID-suffixed so two concurrent subtitle jobs on the same source (e.g.
    # a retried request racing the original) cannot delete or overwrite each
    # other's in-progress temp file — mirrors the pattern used for
    # _convert_sync's temp_output and _extract_audio_mp3's temp_mp3.
    tmp_srt = output_srt.with_name(f"{output_srt.stem}_{uuid.uuid4().hex[:8]}.srt.part")
    tmp_srt.unlink(missing_ok=True)

    def _build_cmd(use_gpu: bool) -> list[str]:
        af = build_whisper_filter(
            model=model,
            language=language,
            destination=tmp_srt,
            translate=translate,
            vad_model=vad,
            use_gpu=use_gpu,
        )
        return [
            str(ffmpeg_bin),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-af",
            af,
            "-f",
            "null",
            "-",
            "-progress",
            "pipe:1",
            "-nostats",
            "-stats_period",
            "0.5",
            "-loglevel",
            "warning",
        ]

    # Whisper on CPU runs slower than real time (measured ~0.16x on a laptop
    # without GPU offload), so the convert pipeline's duration*6 budget is far
    # too tight.  Allow 30x duration, ceiling 6 h.  A 10-minute floor only
    # applies when duration is unknown (duration_s <= 0) — applying it
    # unconditionally let a short clip's failed GPU+CPU attempt pair (each
    # capped at the floor) run up to 20 minutes before erroring out.  The
    # watchdog is also relaxed because a single transcription chunk can be
    # silent on stdout for a while on a slow machine.
    timeout_s = min(duration_s * 30, 21600.0) if duration_s > 0 else 600.0
    timeout_s = max(60.0, timeout_s)

    # GPU (Vulkan) offload can fail with an out-of-device-memory crash on
    # machines with little/no dedicated VRAM (BUG: exit 3221225477 /
    # 0xC0000005). That kills the whole ffmpeg process, so it cannot be
    # recovered mid-run — retry once on CPU, mirroring the encoder GPU→CPU
    # fallback in FfmpegConvertService._try_encode_with_fallback.
    for use_gpu in (True, False):
        try:
            FfmpegConvertService._run_ffmpeg(
                _build_cmd(use_gpu),
                duration_s,
                on_progress,
                watchdog_timeout_s=300.0,
                cancel_event=cancel_event,
                timeout_s=timeout_s,
            )
            break
        except ConversionCancelledError:
            # Subclass of ConversionError — must be re-raised *before* the wrapping
            # branch below, otherwise a user cancel is downgraded to "subtitles
            # failed" and the job still reports COMPLETED.  Transcription can run
            # for hours, so that made the Cancel button look dead.
            tmp_srt.unlink(missing_ok=True)
            raise
        except ConversionError as exc:
            tmp_srt.unlink(missing_ok=True)
            if use_gpu:
                logger.warning("Whisper GPU transcription failed (%s) — retrying on CPU", exc)
                continue
            raise SubtitleError(t("subs.err.failed", err=exc)) from exc

    if not tmp_srt.is_file() or tmp_srt.stat().st_size == 0:
        tmp_srt.unlink(missing_ok=True)
        raise SubtitleError(t("subs.err.no_speech"))

    shutil.move(str(tmp_srt), str(output_srt))
    _renumber_srt(output_srt)
    logger.info("Subtitles written: %s (%d bytes)", output_srt.name, output_srt.stat().st_size)
    return output_srt
