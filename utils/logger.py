from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import FrameType

from loguru import logger

_FMT = "{time:YYYY-MM-DD HH:mm:ss} [{level:<8}] {name} - {message}"

_debug_sink_id: int | None = None
_log_dir_ref: Path | None = None
_base_level: int = logging.INFO


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # Walk up out of logging/__init__.py so {name} resolves to the module
        # that actually logged.  logging.currentframe() is sys._getframe(1),
        # i.e. this emit() frame, so the old "start at currentframe(), depth=2"
        # form never entered the loop and every line was attributed to
        # "logging" (the callHandlers frame).
        frame: FrameType | None = sys._getframe(0)
        depth = 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    global _log_dir_ref, _debug_sink_id, _base_level
    _log_dir_ref = log_dir
    _base_level = level

    logger.remove()
    # logger.remove() dropped every sink, including a debug sink from an
    # earlier setup_logging() call.  Forget its id, otherwise
    # apply_debug_logging(False) raises ValueError on the dead id and
    # apply_debug_logging(True) is a silent no-op.
    _debug_sink_id = None

    # sys.stderr is None in a PyInstaller --windowed build; logger.add(None)
    # raises TypeError and would abort startup before any log exists.
    if sys.stderr is not None:
        logger.add(sys.stderr, format=_FMT, level=logging.getLevelName(level))

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "omnidl.log",
            format=_FMT,
            rotation="5 MB",
            retention=3,
            encoding="utf-8",
            level="INFO",
            colorize=False,
        )
    except OSError:
        logger.warning("Could not open log file {} - file logging disabled.", log_dir / "omnidl.log")

    for noisy in ("PIL", "urllib3", "requests", "yt_dlp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.basicConfig(handlers=[_InterceptHandler()], level=level, force=True)


def apply_debug_logging(enabled: bool) -> None:
    global _debug_sink_id
    root = logging.getLogger()
    if enabled:
        root.setLevel(logging.DEBUG)
        if _debug_sink_id is None and _log_dir_ref is not None:
            try:
                _debug_sink_id = logger.add(
                    _log_dir_ref / "omnidl_debug.log",
                    format=_FMT,
                    rotation="5 MB",
                    retention=3,
                    encoding="utf-8",
                    level="DEBUG",
                    colorize=False,
                )
            except OSError as exc:
                logger.warning("Could not open debug log file: {}", exc)
    else:
        root.setLevel(_base_level)
        if _debug_sink_id is not None:
            try:
                logger.remove(_debug_sink_id)
            except ValueError:
                pass  # sink already gone (logger.remove() elsewhere)
            _debug_sink_id = None
