"""
utils/logger.py
Centralised logging setup.
Call setup_logging() once at process startup.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Configure root logger: console + rotating persistent file.

    The file handler uses RotatingFileHandler (max 5 MB per file, 3 backups)
    to prevent unbounded log growth in long-running sessions or repeated
    restarts.  Total maximum disk usage for logs is therefore ~20 MB.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "omnidl.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    if root.handlers:   # DEF-012: prevent duplicate handlers on re-entry
        return
    root.setLevel(level)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    try:
        fh = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB per file
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        # Read-only filesystem — console-only logging is acceptable.
        logging.getLogger(__name__).warning(
            "Could not open log file %s — file logging disabled.", log_file
        )

    for noisy in ("PIL", "urllib3", "requests", "yt_dlp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
