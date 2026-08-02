"""
api/tailscale_https.py
Helpers for configuring tailscale serve as an HTTPS reverse proxy in front of
the OmniDL Remote API server.

All subprocess calls use shell=False and CREATE_NO_WINDOW on Windows.
No OmniDL imports — safe to import at any time without triggering the
lazy api_enabled import guard.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# Windows: hide the console window spawned by subprocess.
# On non-Windows platforms CREATE_NO_WINDOW does not exist, so we fall back to 0.
_SUBPROCESS_EXTRA: dict[str, Any] = {
    "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
}


def get_tailscale_dns_name() -> str:
    """Return the MagicDNS FQDN for this machine (e.g. 'my-laptop.tail1abc2.ts.net').

    Returns an empty string on any failure: CLI not found, timeout, JSON parse
    error, MagicDNS disabled on the tailnet, etc.
    """
    ts = shutil.which("tailscale")
    if not ts:
        return ""
    try:
        out = subprocess.run(
            [ts, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            **_SUBPROCESS_EXTRA,
        )
        if out.returncode != 0:
            logger.debug("tailscale status --json returned %d", out.returncode)
            return ""
        data: dict[str, Any] = json.loads(out.stdout)
        name: str = data.get("Self", {}).get("DNSName", "")
        return name.rstrip(".")   # strip trailing dot from FQDN
    except Exception as exc:
        logger.warning("get_tailscale_dns_name failed: %s", exc)
        return ""


def start_tailscale_serve(internal_port: int) -> bool:
    """Configure the Tailscale daemon to proxy HTTPS:443 → localhost:<internal_port>.

    BUG-BZ: Tailscale v1.56+ changed 'tailscale serve' to run in foreground
    (blocking) — the process never exits after setting the rule, so
    subprocess.run(timeout=15) always raised TimeoutExpired.
    Fix: try '--bg' flag first (v1.62+, exits immediately after setting rule);
    fall back to the old syntax (v1.55 and earlier) if '--bg' is not recognised
    (rc=1 with "unknown flag" in stderr).

    Returns True on success, False on any failure.  Never raises.
    """
    ts = shutil.which("tailscale")
    if not ts:
        logger.warning("start_tailscale_serve: tailscale CLI not found on PATH")
        return False
    try:
        # Try modern syntax first (Tailscale >= v1.62)
        out = subprocess.run(
            [ts, "serve", "--bg", "--https=443", f"localhost:{internal_port}"],
            capture_output=True,
            text=True,
            timeout=15,
            **_SUBPROCESS_EXTRA,
        )
        if out.returncode == 0:
            logger.debug("start_tailscale_serve: --bg succeeded (port %d)", internal_port)
            return True
        # If '--bg' is not recognised, fall back to old syntax (Tailscale <= v1.55)
        stderr_lower = out.stderr.lower()
        if "unknown flag" in stderr_lower or "flag provided but not defined" in stderr_lower:
            logger.debug("start_tailscale_serve: --bg not supported, trying legacy syntax")
            out2 = subprocess.run(
                [ts, "serve", "--https=443", f"localhost:{internal_port}"],
                capture_output=True,
                text=True,
                timeout=15,
                **_SUBPROCESS_EXTRA,
            )
            if out2.returncode != 0:
                logger.warning(
                    "tailscale serve start failed (legacy, rc=%d): %s",
                    out2.returncode,
                    out2.stderr.strip(),
                )
            return out2.returncode == 0
        logger.warning(
            "tailscale serve start failed (rc=%d): %s",
            out.returncode,
            out.stderr.strip(),
        )
        return False
    except Exception as exc:
        logger.warning("start_tailscale_serve error: %s", exc)
        return False


def stop_tailscale_serve(internal_port: int) -> bool:
    """Remove the HTTPS proxy rule for <internal_port>.

    BUG-BZ: old 'tailscale serve --https=443 localhost:PORT off' syntax was
    deprecated in v1.56+.  Use 'tailscale serve --bg --https=443 off' (new)
    with fallback to the old positional 'off' syntax for older clients.

    Idempotent — returns True even when tailscale is not installed (nothing to undo).
    Never raises.
    """
    ts = shutil.which("tailscale")
    if not ts:
        return True
    try:
        # Modern syntax (Tailscale >= v1.62)
        out = subprocess.run(
            [ts, "serve", "--bg", "--https=443", "off"],
            capture_output=True,
            text=True,
            timeout=10,
            **_SUBPROCESS_EXTRA,
        )
        if out.returncode == 0:
            return True
        stderr_lower = out.stderr.lower()
        if "unknown flag" in stderr_lower or "flag provided but not defined" in stderr_lower:
            # Legacy syntax (Tailscale <= v1.55)
            out2 = subprocess.run(
                [ts, "serve", "--https=443", f"localhost:{internal_port}", "off"],
                capture_output=True,
                text=True,
                timeout=10,
                **_SUBPROCESS_EXTRA,
            )
            if out2.returncode != 0:
                logger.debug(
                    "tailscale serve stop returned %d: %s",
                    out2.returncode,
                    out2.stderr.strip(),
                )
            return out2.returncode == 0
        logger.debug(
            "tailscale serve stop returned %d: %s",
            out.returncode,
            out.stderr.strip(),
        )
        return out.returncode == 0
    except Exception as exc:
        logger.warning("stop_tailscale_serve error: %s", exc)
        return False


def reset_tailscale_serve() -> bool:
    """Remove ALL tailscale serve rules ('tailscale serve reset').

    Used by the Reset Profile button.  Idempotent.  Never raises.
    """
    ts = shutil.which("tailscale")
    if not ts:
        return True
    try:
        out = subprocess.run(
            [ts, "serve", "reset"],
            capture_output=True,
            text=True,
            timeout=10,
            **_SUBPROCESS_EXTRA,
        )
        if out.returncode != 0:
            logger.debug(
                "tailscale serve reset returned %d: %s",
                out.returncode,
                out.stderr.strip(),
            )
        return out.returncode == 0
    except Exception as exc:
        logger.warning("reset_tailscale_serve error: %s", exc)
        return False
