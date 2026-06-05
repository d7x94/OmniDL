from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

_FMT = "{time:YYYY-MM-DD HH:mm:ss} [{level:<8}] {name} - {message}"

_debug_sink_id: int | None = None
_log_dir_ref: Path | None = None


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    global _log_dir_ref
    _log_dir_ref = log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(sys.stderr, format=_FMT, level=logging.getLevelName(level))
    try:
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
                    level="DEBUG",
                    colorize=False,
                )
            except OSError as exc:
                logger.warning("Could not open debug log file: {}", exc)
    else:
        root.setLevel(logging.INFO)
        if _debug_sink_id is not None:
            logger.remove(_debug_sink_id)
            _debug_sink_id = None
