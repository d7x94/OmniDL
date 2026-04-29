"""
utils/logger.py
Centralised logging setup.
Call setup_logging() once at process startup.
Call apply_debug_logging() after config is loaded to enable/disable debug mode.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Module-level reference to the debug file handler so it can be added/removed
# at runtime without re-reading the log directory path.
_debug_handler: logging.Handler | None = None
_log_dir_ref: Path | None = None


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Configure root logger: console + rotating persistent file.

    The file handler uses RotatingFileHandler (max 5 MB per file, 3 backups)
    to prevent unbounded log growth in long-running sessions or repeated
    restarts.  Total maximum disk usage for logs is therefore ~20 MB.

    Level and noisy-logger settings are applied unconditionally so that
    test fixtures calling setup_logging() multiple times get a predictable
    root-logger state each time.
    """
    global _log_dir_ref
    _log_dir_ref = log_dir

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
    _fh_type = logging.FileHandler if isinstance(logging.FileHandler, type) else type(None)
    has_console = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, _fh_type)
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


def apply_debug_logging(enabled: bool) -> None:
    """Enable or disable DEBUG-level logging to a separate omnidl_debug.log file.

    Designed to be called at startup (after config is loaded) and also live
    when the user toggles the setting in the UI.

    When enabled:
      • Root logger level is lowered to DEBUG so all logger.debug() calls fire.
      • A separate RotatingFileHandler writes to omnidl_debug.log (5 MB × 3).
      • The existing omnidl.log stays at INFO (its handler keeps its own level).
      • Noisy third-party loggers (yt_dlp, urllib3, etc.) are NOT lowered —
        they remain at WARNING to keep the debug log focused on OmniDL internals.

    When disabled:
      • Root logger level is raised back to INFO.
      • The debug file handler is closed and removed.

    Thread-safe: addHandler / removeHandler on the root logger is protected by
    the logging module's own internal lock.
    """
    global _debug_handler

    root = logging.getLogger()
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if enabled:
        root.setLevel(logging.DEBUG)
        # Only add the debug handler once.
        if _debug_handler is None and _log_dir_ref is not None:
            debug_log = _log_dir_ref / "omnidl_debug.log"
            try:
                dh = RotatingFileHandler(
                    debug_log,
                    maxBytes=5 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                dh.setFormatter(fmt)
                dh.setLevel(logging.DEBUG)
                root.addHandler(dh)
                _debug_handler = dh
                logging.getLogger(__name__).info(
                    "Debug logging enabled → %s", debug_log
                )
            except OSError as exc:
                logging.getLogger(__name__).warning(
                    "Could not open debug log file: %s", exc
                )
    else:
        root.setLevel(logging.INFO)
        if _debug_handler is not None:
            _debug_handler.close()
            root.removeHandler(_debug_handler)
            _debug_handler = None
            logging.getLogger(__name__).info("Debug logging disabled")
