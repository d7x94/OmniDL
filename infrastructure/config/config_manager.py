"""
infrastructure/config/config_manager.py
JSON-backed configuration with typed accessors and thread-safe writes.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Allowlist of browser names accepted by yt-dlp's cookiesfrombrowser option
# (CWE-20: Improper Input Validation).  Rejects arbitrary strings that could
# cause yt-dlp to attempt reading an unexpected browser profile path.
_VALID_BROWSERS: frozenset[str] = frozenset({
    "chrome", "firefox", "safari", "edge", "opera",
    "brave", "chromium", "vivaldi",
})

# Accepted URI schemes for the proxy setting.  Rejecting arbitrary schemes
# prevents a tampered config.json from routing all traffic through an
# attacker-controlled proxy (e.g. a file:// or data: URI).
_VALID_PROXY_SCHEMES: tuple[str, ...] = (
    "http://", "https://", "socks4://", "socks4a://",
    "socks5://", "socks5h://",
)

_DEFAULTS: dict[str, Any] = {
    "download_dir": str(Path.home() / "Downloads" / "OmniDL"),
    "theme": "dark",
    "language": "en",
    "max_concurrent": 3,
    "max_retries": 3,
    "proxy": "",
    "use_cookies": False,
    "cookies_browser": "chrome",
    "cookie_file": "",        # path to a Netscape-format .txt cookie file (global fallback)
    # Per-platform cookie files — take priority over cookie_file for each platform.
    # Keys: "tiktok", "instagram", "facebook", "twitter", "threads"
    # Values: absolute path to a Netscape-format .txt file (empty = not set)
    "platform_cookies": {},
    "embed_thumbnail": True,
    "embed_metadata": True,
    "default_quality": "bestvideo+bestaudio/best",
    "default_format": "mp4",
    "show_notifications": True,
    "history_limit": 500,
    "extra_args": "",
}


class ConfigManager:
    """Thread-safe configuration manager backed by a JSON file."""

    # Class-level in-memory cache keyed by resolved path string.
    # Allows a freshly-created instance to see data written by set()/update()
    # on another instance for the same path, even before the debounced timer
    # has flushed to disk.
    _cache: dict[str, dict[str, Any]] = {}
    _cache_lock: threading.Lock = threading.Lock()

    def __init__(self, config_path: Path) -> None:
        self._path = config_path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = dict(_DEFAULTS)
        # Config writes are debounced (see _schedule_save) so that rapid
        # successive set() calls — e.g. dragging a settings slider — do not
        # hammer the disk on every tick.
        self._save_timer: Optional[threading.Timer] = None
        self._load()

    # ── Load / Save ──────────────────────────────────────────────────────

    def _load(self) -> None:
        path_key = str(self._path.resolve())
        with ConfigManager._cache_lock:
            if path_key in ConfigManager._cache:
                with self._lock:
                    self._data.update(ConfigManager._cache[path_key])
                return
        if not self._path.exists():
            self._save()
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            with self._lock:
                self._data.update(loaded)
                _snapshot = dict(self._data)
            # Populate the shared cache so a second instance for the same
            # path sees this data immediately, without re-reading the file.
            with ConfigManager._cache_lock:
                ConfigManager._cache[path_key] = _snapshot
            logger.debug("Config loaded from %s", self._path)
        except Exception as exc:
            logger.warning("Could not load config (%s) — using defaults", exc)

    def _save(self) -> None:
        import io as _io
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp.json")
        try:
            # Snapshot and serialise inside the lock.  Writing to StringIO has
            # no syscalls, so the lock is released quickly — concurrent get()
            # and set() calls are not blocked during the (slower) disk write.
            buf = _io.StringIO()
            with self._lock:
                snapshot = dict(self._data)
                json.dump(snapshot, buf, indent=2, ensure_ascii=False)
            # Lock is now released — write the pre-serialised data to disk.
            with tmp.open("w", encoding="utf-8") as f:
                f.write(buf.getvalue())
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("Config save failed: %s", exc)
            tmp.unlink(missing_ok=True)

    def reset_to_defaults(self) -> None:
        """Reset all settings to factory defaults and flush to disk immediately.

        Called when the installed app version changes so a fresh install always
        starts with a clean, known-good configuration rather than potentially
        incompatible settings left over from an older build.
        """
        with self._lock:
            self._data = dict(_DEFAULTS)
            _snapshot = dict(self._data)
        path_key = str(self._path.resolve())
        with ConfigManager._cache_lock:
            ConfigManager._cache[path_key] = _snapshot
        self._save()
        logger.info("Config reset to factory defaults")

    def save(self) -> None:
        """Flush config to disk immediately (synchronous; use at shutdown)."""
        # Cancel any pending debounced write — this is the authoritative flush.
        # Cancel the timer while holding the lock, then call _save() OUTSIDE
        # the lock — _save() acquires it internally for the snapshot, so calling
        # it while already holding the lock would deadlock with threading.Lock.
        with self._lock:
            if self._save_timer is not None and self._save_timer.is_alive():
                self._save_timer.cancel()
                self._save_timer = None
        self._save()

    def _schedule_save(self) -> None:
        """
        Schedule a disk write 500 ms from now.  If called again before the
        timer fires, the existing timer is cancelled and a new one started,
        debouncing bursts of set()/update() calls (e.g. slider drag events)
        into a single flush.  save() cancels any pending timer and writes
        immediately, so normal shutdown is always safe.
        """
        with self._lock:
            if self._save_timer is not None and self._save_timer.is_alive():
                self._save_timer.cancel()
            timer = threading.Timer(0.5, self._save)
            timer.daemon = True
            timer.start()
            self._save_timer = timer

    # ── Generic get / set ────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:   # DEF-006: snapshot inside lock to prevent TOCTOU race
            self._data[key] = value
            _snapshot = dict(self._data)
        path_key = str(self._path.resolve())
        with ConfigManager._cache_lock:
            ConfigManager._cache[path_key] = _snapshot
        self._schedule_save()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:   # DEF-006: snapshot inside lock to prevent TOCTOU race
            self._data.update(values)
            _snapshot = dict(self._data)
        path_key = str(self._path.resolve())
        with ConfigManager._cache_lock:
            ConfigManager._cache[path_key] = _snapshot
        self._schedule_save()

    # ── Typed property accessors ─────────────────────────────────────────

    @property
    def config_path(self) -> Path:
        """Return the path to the config.json file."""
        return self._path

    @property
    def download_dir(self) -> Path:
        # Treat both None (JSON null) and "" (empty string) as "not set" and
        # fall back to the OS default.  Path("") resolves to CWD, which would
        # silently route downloads into the app's working directory.
        # Always return an absolute path (.resolve()) so that callers never
        # accidentally write files relative to the process CWD.  This is
        # especially important on Windows + PyInstaller where the CWD is the
        # EXE directory, not the user's home.
        raw = self.get("download_dir")
        if not raw:
            return Path(_DEFAULTS["download_dir"]).resolve()
        return Path(raw).resolve()

    @property
    def theme(self) -> str:
        return str(self.get("theme", "dark"))

    @property
    def max_concurrent(self) -> int:
        # Clamp to [1, 10]: 0 would block all downloads; >10 is unnecessary
        # on a desktop machine and risks exhausting network/disk resources.
        return max(1, min(10, int(self.get("max_concurrent", 3))))

    @property
    def max_retries(self) -> int:
        # Clamp to [0, 10]: 0 = no retry (valid); >10 = pathological loop.
        return max(0, min(10, int(self.get("max_retries", 3))))

    @property
    def proxy(self) -> str:
        """Return the configured proxy URL, or '' if absent or invalid.

        Only http://, https://, and socks4/5 schemes are accepted (CWE-20).
        An empty string tells yt-dlp to use no proxy.
        """
        val = str(self.get("proxy", "")).strip()
        if not val:
            return ""
        if any(val.lower().startswith(scheme) for scheme in _VALID_PROXY_SCHEMES):
            return val
        logger.warning(
            "proxy value %r has an unrecognised scheme — ignored. "
            "Valid schemes: %s",
            val, ", ".join(_VALID_PROXY_SCHEMES),
        )
        return ""

    @property
    def use_cookies(self) -> bool:
        return bool(self.get("use_cookies", False))

    @property
    def cookies_browser(self) -> str:
        # Validate against the allowlist defined at module level (CWE-20).
        # An out-of-allowlist value could cause yt-dlp to read an unexpected
        # browser profile path if config.json is tampered with.
        val = str(self.get("cookies_browser", "chrome"))
        if val not in _VALID_BROWSERS:
            logger.warning(
                "cookies_browser %r is not in the allowed browser list — "
                "defaulting to 'chrome'.  Valid values: %s",
                val, ", ".join(sorted(_VALID_BROWSERS)),
            )
            return "chrome"
        return val

    @property
    def embed_thumbnail(self) -> bool:
        return bool(self.get("embed_thumbnail", True))

    @property
    def embed_metadata(self) -> bool:
        return bool(self.get("embed_metadata", True))

    @property
    def default_quality(self) -> str:
        return str(self.get("default_quality", "bestvideo+bestaudio/best"))

    @property
    def default_format(self) -> str:
        return str(self.get("default_format", "mp4"))

    @property
    def history_limit(self) -> int:
        # Clamp to [10, 5000]: <10 makes history useless; >5000 risks
        # noticeable memory and slow JSONL rewrites on app startup.
        return max(10, min(5000, int(self.get("history_limit", 500))))

    @property
    def extra_args(self) -> str:
        return str(self.get("extra_args", ""))

    @property
    def cookie_file(self) -> str:
        """Path to a Netscape-format cookie file, or '' if not set."""
        return str(self.get("cookie_file", ""))

    @property
    def platform_cookies(self) -> "dict[str, str]":
        """Per-platform cookie file paths.

        Returns a dict mapping platform key → absolute path (or '').
        Keys: "tiktok", "instagram", "facebook", "twitter", "threads".
        Always returns a dict — never None.
        """
        val = self.get("platform_cookies", {})
        if not isinstance(val, dict):
            return {}
        return {k: str(v) for k, v in val.items() if isinstance(v, str)}

    def get_cookie_for_platform(self, platform_key: str) -> str:
        """Return the cookie file path for *platform_key*, or '' if not set.

        platform_key is one of: "tiktok", "instagram", "facebook",
        "twitter", "threads".

        Returns '' (not None) so callers can do .strip() safely.
        """
        return self.platform_cookies.get(platform_key, "")

    def set_cookie_for_platform(self, platform_key: str, path: str) -> None:
        """Set or clear the cookie path for *platform_key*.

        Reads the current dict, modifies the key, writes back atomically.
        Thread-safe: uses config.set() which holds self._lock.
        """
        d = dict(self.platform_cookies)   # copy
        if path:
            d[platform_key] = path
        else:
            d.pop(platform_key, None)
        self.set("platform_cookies", d)
