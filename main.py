#!/usr/bin/env python3
"""
OmniDL v16 — Ultimate Media Downloader
Entry point: wires all layers together and launches the UI.

multiprocessing.freeze_support() MUST be called before any other code
to prevent PyInstaller from spawning infinite child processes on Windows.

FIX SEC-3: User data (config, history, logs) is now stored in the
platform-appropriate user-data directory via platformdirs, NOT next to
the executable.  This prevents silent write failures when OmniDL is
installed in a read-only location (e.g. C:\\Program Files on Windows).

  Windows : %APPDATA%\\OmniDL\\
  macOS   : ~/Library/Application Support/OmniDL/
  Linux   : ~/.local/share/OmniDL/

APP_BINARY_DIR still points to the executable's parent and is used only
for read-only bundled assets (e.g. default_config.json in the future).
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

multiprocessing.freeze_support()


def _get_app_dir() -> Path:
    """
    Return the application binary directory.
    Frozen (PyInstaller): parent of the .exe / .app Contents/MacOS/
    Source mode:          directory containing this file
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _get_data_dir() -> Path:
    """
    Return the platform-appropriate user-data directory for OmniDL.
    Always writable by the current user, regardless of install location.

    FIX SEC-3: replaces the previous APP_DIR-based approach which failed
    silently when the app was installed under Program Files or any other
    read-only directory.
    """
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir("OmniDL", appauthor=False))
    except ImportError:
        # Graceful fallback if platformdirs is somehow missing at runtime
        return Path.home() / ".omnidl"


def _get_log_dir() -> Path:
    """Return the platform-appropriate log directory for OmniDL."""
    try:
        from platformdirs import user_log_dir
        return Path(user_log_dir("OmniDL", appauthor=False))
    except ImportError:
        return _get_data_dir() / "logs"


APP_BINARY_DIR = _get_app_dir()
DATA_DIR       = _get_data_dir()
LOG_DIR        = _get_log_dir()

# Ensure paths are importable when running from source
if str(APP_BINARY_DIR) not in sys.path:
    sys.path.insert(0, str(APP_BINARY_DIR))


def main() -> None:
    from utils.logger import setup_logging
    setup_logging(LOG_DIR)

    import logging
    logger = logging.getLogger("omnidl.main")
    logger.info(
        "OmniDL v16 starting | binary=%s | data=%s | frozen=%s",
        APP_BINARY_DIR, DATA_DIR, getattr(sys, "frozen", False),
    )

    _check_deps()
    _migrate_legacy_data()

    from infrastructure.config.config_manager import ConfigManager
    from infrastructure.downloader.download_manager import DownloadManager
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
    from infrastructure.storage.history_repository import HistoryRepository

    # All user-mutable data lives in DATA_DIR, not next to the binary.
    config  = ConfigManager(DATA_DIR / "config.json")
    history = HistoryRepository(
        DATA_DIR / "download_history.jsonl", config.history_limit
    )
    engine  = YtDlpEngine(config)
    manager = DownloadManager(config, engine=engine)
    manager.start()

    from app.services.download_service import DownloadService
    service = DownloadService(
        config=config,
        download_manager=manager,
        history_repo=history,
        engine=engine,
    )

    import customtkinter as ctk
    ctk.set_appearance_mode(config.theme)
    ctk.set_default_color_theme("blue")

    from ui.main_window import MainWindow
    window = MainWindow(service=service, config=config)

    logger.info("Entering main loop")
    try:
        window.mainloop()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        manager.shutdown(wait=False)
        config.save()
        logger.info("OmniDL shutdown complete")


def _migrate_legacy_data() -> None:
    """
    One-time migration: copy config/history from the old APP_BINARY_DIR
    location to the new DATA_DIR location so existing users don't lose
    their settings and download history on first upgrade.
    """
    import logging
    import shutil
    logger = logging.getLogger("omnidl.main")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    _LEGACY_MAP = {
        APP_BINARY_DIR / "config.json":            DATA_DIR / "config.json",
        APP_BINARY_DIR / "download_history.json":  DATA_DIR / "download_history.jsonl",
        APP_BINARY_DIR / "download_history.jsonl": DATA_DIR / "download_history.jsonl",
    }

    for src, dst in _LEGACY_MAP.items():
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
                logger.info("Migrated %s → %s", src.name, dst)
            except OSError as exc:
                logger.warning("Legacy migration failed for %s: %s", src.name, exc)


def _check_deps() -> None:
    missing = []
    for pkg, install in [
        ("customtkinter", "customtkinter==5.2.2"),
        ("yt_dlp",        "yt-dlp"),
        ("PIL",           "Pillow"),
        ("requests",      "requests"),
    ]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(install)
    if missing:
        print("Missing dependencies. Run:")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
