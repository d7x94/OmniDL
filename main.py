#!/usr/bin/env python3
"""
OmniDL v17.1 — Ultimate Media Downloader
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

from utils.__version__ import __version__ as _APP_VERSION

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


# Saved by _hide_console() so _close_console() can send WM_CLOSE at exit.
_console_hwnd: int = 0


def _hide_console() -> None:
    """Hide the PowerShell/cmd console window on Windows without freeing it.

    Called once at startup, AFTER setup_logging() so RotatingFileHandler is
    already open before the console handle is hidden.

    Hides the window (SW_HIDE) so:
      • PowerShell never surfaces over OmniDL when a media player closes.
      • Subprocess inheritance continues to work (HANDLE stays valid).
      • yt-dlp internal stdout writes succeed — downloads unaffected.

    Also removes the console from the Windows activation stack by adding
    WS_EX_TOOLWINDOW so Windows skips it entirely when searching for the
    next window to activate after a media player closes.

    The console HWND is saved in _console_hwnd so _close_console() can
    send WM_CLOSE when OmniDL exits, preventing orphan PowerShell processes.

    No-op on macOS/Linux (guarded by caller). No-op on frozen builds
    (GetConsoleWindow() returns 0 when no console is attached).
    Bandit/ruff/mypy clean — ctypes stdlib, no shell=True.
    """
    global _console_hwnd
    import ctypes as _ctypes

    hwnd = _ctypes.windll.kernel32.GetConsoleWindow()
    if not hwnd:
        return   # no console attached (frozen build) — nothing to do

    _console_hwnd = hwnd

    # Step 1: hide the window so it is never visible.
    _ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0

    # Step 2: remove from Windows activation stack so it cannot surface
    # when a media player closes.  WS_EX_TOOLWINDOW = 0x00000080.
    _GWL_EXSTYLE      = -20
    _WS_EX_APPWINDOW  = 0x00040000
    _WS_EX_TOOLWINDOW = 0x00000080
    style = _ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    style = (style & ~_WS_EX_APPWINDOW) | _WS_EX_TOOLWINDOW
    _ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)


def _close_console() -> None:
    """Send WM_CLOSE to the hidden console so PowerShell exits with OmniDL.

    Problem solved:
      _hide_console() hides the PowerShell window but the process keeps
      running. When python.exe exits, PS returns to its command prompt —
      with a HIDDEN window. The user cannot see it or close it. Each
      OmniDL session leaves one orphan PS process in the background.

    Fix:
      PostMessage(console_hwnd, WM_CLOSE, 0, 0) sends a non-blocking
      close request to the hidden console window. Windows delivers it
      after the calling process exits. PowerShell receives the close
      signal and terminates cleanly — no orphan process remains.

      PostMessage (not SendMessage) is used so the call returns
      immediately and never blocks the shutdown sequence.

    Only called when _hide_console() successfully ran (hwnd != 0).
    No-op if _console_hwnd was never set (non-Windows, frozen build).
    """
    if not _console_hwnd:
        return
    import ctypes as _ctypes
    _WM_CLOSE = 0x0010
    _ctypes.windll.user32.PostMessageW(_console_hwnd, _WM_CLOSE, 0, 0)


def main() -> None:
    from utils.logger import setup_logging
    setup_logging(LOG_DIR)

    # Hide the console window so PowerShell never surfaces over OmniDL.
    # Must be called AFTER setup_logging() so the RotatingFileHandler is
    # already configured before we hide the console.
    # No-op on macOS/Linux. No-op on frozen builds (no console attached).
    if sys.platform == "win32":
        _hide_console()

    import logging
    logger = logging.getLogger("omnidl.main")
    logger.info(
        "OmniDL v%s starting | binary=%s | data=%s | frozen=%s",
        _APP_VERSION,
        APP_BINARY_DIR, DATA_DIR, getattr(sys, "frozen", False),
    )

    _check_deps()
    _migrate_legacy_data()

    from infrastructure.config.config_manager import ConfigManager
    from infrastructure.downloader.download_manager import DownloadManager
    from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine
    from infrastructure.storage.history_repository import HistoryRepository

    # All user-mutable data lives in DATA_DIR, not next to the binary.
    config  = ConfigManager(DATA_DIR / "config.json")
    history = HistoryRepository(
        DATA_DIR / "download_history.jsonl", config.history_limit
    )
    _clear_history_on_version_change(config, history)

    # Apply debug logging mode from config (must run after ConfigManager is ready
    # and before any worker threads start so all subsequent logger.debug() calls
    # are captured from the beginning of the session).
    if config.debug_logging:
        from utils.logger import apply_debug_logging as _apply_debug
        _apply_debug(True)
        logger.info("Debug logging active — writing to omnidl_debug.log")

    # ── Cookie security maintenance ───────────────────────────────────────
    # 1. Clean up leftover decrypted temp files from any previous crash
    # 2. Auto-delete cookie files older than 30 days
    _cookie_dir = DATA_DIR / "cookies"
    try:
        from infrastructure.downloader.cookie_storage import (
            cleanup_leftover_temp_files,
            cleanup_stale_cookies,
            encrypt_plaintext_cookies,
        )
        cleanup_leftover_temp_files(_cookie_dir)
        n_encrypted = encrypt_plaintext_cookies(_cookie_dir)
        if n_encrypted:
            logger.info("Startup cookie migration: %d plaintext file(s) encrypted", n_encrypted)
        n_deleted = cleanup_stale_cookies(_cookie_dir, max_age_days=30)
        if n_deleted:
            logger.info("Startup cookie cleanup: %d stale file(s) removed", n_deleted)
    except Exception as exc:
        logger.warning("Cookie startup cleanup failed (non-fatal): %s", exc)

    engine         = YtDlpEngine(config)
    gallery_engine = GalleryDlEngine(config)

    from infrastructure.downloader.instagram_live_engine import InstagramLiveEngine
    instagram_live_engine = InstagramLiveEngine(config)

    from infrastructure.downloader.kuaishou_engine import KuaishouEngine
    kuaishou_engine = KuaishouEngine(config)

    # Inject bundled Deno into PATH once on the main thread before any worker
    # thread starts.  os.environ.update() is not thread-safe on CPython — calling
    # it from ThreadPoolExecutor workers (the old approach) was a latent race.
    try:
        from utils.deno_locator import get_deno_env as _get_deno_env
        _deno_env = _get_deno_env()
        if _deno_env:
            import os as _os
            _os.environ.update(_deno_env)
            logger.info("Deno PATH injected into environment (startup, main thread)")
    except Exception as _deno_exc:
        logger.warning("Deno PATH injection failed (non-fatal): %s", _deno_exc)

    manager = DownloadManager(
        config, engine=engine, gallery_engine=gallery_engine,
        story_engine_enabled=True, instagram_live_engine=instagram_live_engine,
        kuaishou_engine=kuaishou_engine,
    )
    manager.start()

    from app.services.download_service import DownloadService
    service = DownloadService(
        config=config,
        download_manager=manager,
        history_repo=history,
        engine=engine,
        gallery_engine=gallery_engine,
    )

    # ── Remote API server (Hướng 1: iOS / mobile remote control) ─────────
    # Starts a FastAPI/uvicorn server in a daemon thread when api_enabled=True.
    # Enable via Settings → Remote API tab (or set "api_enabled": true in
    # config.json).  The server is a no-op (returns None) when disabled,
    # so it adds zero overhead to normal desktop operation.
    # SAFETY: api/server.py imports fastapi at module level, so we guard the
    # import behind api_enabled to avoid a ModuleNotFoundError crash when
    # fastapi/uvicorn are not installed (standard desktop-only installs).
    # This preserves zero import cost on normal desktop startups.
    _api_thread = None
    if getattr(config, "api_enabled", False):
        try:
            from api.server import start_api_server as _start_api
            from app.event_bus import bus as _event_bus
            _api_thread = _start_api(service=service, config=config, bus=_event_bus)
        except ImportError as _api_err:
            import logging as _log_api
            _log_api.getLogger(__name__).warning(
                "Remote API disabled — fastapi/uvicorn not installed: %s. "
                "Run: pip install fastapi uvicorn",
                _api_err,
            )

    import customtkinter as ctk
    ctk.set_default_color_theme("blue")

    from ui.themes.tokens import T
    T.set_mode(config.theme)    # sync token palette before any widget reads T.*
    ctk.set_appearance_mode(T.ctk_base)  # map custom theme → "dark"/"light" for CTk

    from ui.main_window import MainWindow
    window = MainWindow(service=service, config=config)

    logger.info("Entering main loop")
    try:
        window.mainloop()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        manager.shutdown(wait=True)   # drain all running downloads first (PV-002)
        service.close()               # then flush history writes
        config.save()
        logger.info("OmniDL shutdown complete")
        # Close the hidden PowerShell console so it does not linger as an
        # orphan process after python.exe exits.  PostMessage is async and
        # never blocks.  No-op if running without a console (frozen build).
        if sys.platform == "win32":
            _close_console()


def _clear_history_on_version_change(config, history) -> None:
    """Track the installed app version in config.

    Settings are never auto-reset on update — the user controls this
    explicitly via Settings → Data & Privacy → Clear All Data.
    New config keys introduced in any version are automatically populated
    with their defaults by ConfigManager on load (missing-key merge).
    """
    stored = config.get("app_version", "")
    if stored != _APP_VERSION:
        config.set("app_version", _APP_VERSION)


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
        APP_BINARY_DIR / "config.json": DATA_DIR / "config.json",
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
        ("customtkinter", "customtkinter>=5.2.2"),
        ("yt_dlp",        "yt-dlp>=2025.1.1"),
        ("PIL",           "Pillow>=10.3.0"),
        ("requests",      "requests>=2.31.0"),
        ("platformdirs",  "platformdirs>=4.0.0"),  # DEF-024
        ("gallery_dl",    "gallery-dl>=1.27.0"),   # image fallback engine
        ("playwright",    "playwright>=1.40"),      # Facebook Story CDP
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
