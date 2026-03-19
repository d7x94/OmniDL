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

    Level and noisy-logger settings are applied unconditionally so that
    test fixtures calling setup_logging() multiple times get a predictable
    root-logger state each time.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "omnidl.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    # Always update the root level.
    root.setLevel(level)

    # Always silence noisy third-party loggers.
    for noisy in ("PIL", "urllib3", "requests", "yt_dlp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Prevent duplicate handlers on re-entry (DEF-012).
    existing_files = {
        getattr(h, "baseFilename", "") for h in root.handlers
    }
    log_file_abs = str(log_file.resolve())

    # Add console handler only if none exists yet.
    has_console = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if not has_console:
        # On Windows the default console encoding (cp1252/cp1258) cannot
        # represent every Unicode character that may appear in filenames or
        # yt-dlp output.  Wrapping stdout with errors='replace' prevents
        # UnicodeEncodeError from spamming "--- Logging error ---" to the
        # console while downloads with emoji/Vietnamese titles are in progress.
        import io
        # Wrap stdout only in real Windows deployments where UnicodeEncodeError
        # can occur on cp1252/cp1258 consoles.  In test environments (pytest
        # replaces sys.stdout with a capture object) we must NOT wrap because
        # TextIOWrapper takes ownership of the underlying buffer and causes
        # pytest's capture mechanism to hit "I/O on closed file" at teardown.
        _is_real_file = (
            hasattr(sys.stdout, "buffer")
            and hasattr(sys.stdout.buffer, "raw")
        )
        safe_stdout = (
            io.TextIOWrapper(
                sys.stdout.buffer,
                encoding=sys.stdout.encoding or "utf-8",
                errors="replace",
                line_buffering=True,
            )
            if _is_real_file
            else sys.stdout
        )
        ch = logging.StreamHandler(safe_stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # Add file handler only if this specific log file is not already open.
    if log_file_abs not in existing_files:
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
            logging.getLogger(__name__).warning(
                "Could not open log file %s - file logging disabled.", log_file
            )
