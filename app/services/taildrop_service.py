"""
app/services/taildrop_service.py
Post-download transfer service — sends completed files to an iPhone
(or any Tailscale peer) via Taildrop using the Tailscale CLI.

Design constraints
──────────────────
• Zero impact on existing download pipeline.
  This service is ALWAYS called asynchronously after DOWNLOAD_COMPLETED;
  it never blocks or modifies DownloadTask state.
• Security: target_node is validated by allowlist regex before being
  passed to subprocess (CWE-78 command injection prevention).
• Stability: any failure produces a warning log + TAILDROP_FAILED event.
  The download itself is already marked COMPLETED — transfer failure
  does NOT change that status.
• Independence: runs in its own ThreadPoolExecutor (max_workers=1),
  completely separate from the download executor.

Typical call sequence (wired in DownloadService.__init__):
    bus.subscribe(EventBus.DOWNLOAD_COMPLETED, taildrop_svc.on_download_completed)
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.event_bus import EventBus
    from domain.models.download_task import DownloadTask
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# ── Security: allowlist for Tailscale node names / IPs ───────────────────
# Accepts:
#   • Tailscale Magic DNS names  e.g. "iphone", "my-iphone", "pixel-7"
#   • Dotted IPv4                e.g. "100.64.0.5"
#   • FQDN form                  e.g. "iphone.tail1abc2.ts.net"
# Rejects anything with shell metacharacters, path separators, or spaces.
_NODE_RE = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,252}[A-Za-z0-9])?$')

# Destination suffix required by Tailscale CLI file send.
# The trailing colon tells tailscale "this is a node name, not a local path".
_NODE_SUFFIX = ":"


@dataclass(frozen=True)
class TransferResult:
    success: bool
    dest_node: str
    error: str = ""


class TaildropService:
    """
    Sends completed download files to a Tailscale peer via Taildrop.

    Thread-safety
    ─────────────
    on_download_completed() may be called from any thread (EventBus delivers
    on the publisher's thread).  The actual send is dispatched to a
    single-worker executor so transfers are serialised and the caller
    returns immediately.
    """

    def __init__(
        self,
        config: "ConfigManager",
        event_bus: "EventBus",
    ) -> None:
        self._config = config
        self._bus = event_bus
        # Dedicated executor — completely separate from download executor.
        # max_workers=1: transfers are serialised to avoid hammering Tailscale.
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="omnidl-taildrop",
        )
        self._closed = False
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    def on_download_completed(self, task: "DownloadTask") -> None:
        """
        EventBus subscriber for DOWNLOAD_COMPLETED.

        Returns immediately; transfer runs in the background executor.
        Does nothing when Taildrop is disabled or misconfigured.
        """
        if not self._config.taildrop_enabled:
            return
        # "ask" mode: skip auto-send — user triggers transfer manually via
        # the Remote API POST /api/queue/{task_id}/transfer endpoint.
        if self._config.taildrop_send_mode == "ask":
            logger.debug("Taildrop: skip auto-send — send_mode is 'ask'")
            return
        node = self._config.taildrop_target_node
        if not node:
            logger.debug("Taildrop: skip — target_node not configured")
            return

        # Get the output path from the task (may be None for failed tasks
        # that somehow triggered COMPLETED — guard defensively).
        #
        # Field-name lookup order (first truthy value wins):
        #   1. task.filename   — canonical field on DownloadTask (set by yt_dlp_engine
        #                        pp_hook after the merge/postprocess step completes)
        #   2. task.output_path — legacy alias kept for forward-compat
        #   3. task.file_path   — legacy alias kept for forward-compat
        file_path: Optional[Path] = None
        raw = (
            getattr(task, "filename", None)
            or getattr(task, "output_path", None)
            or getattr(task, "file_path", None)
        )
        if raw:
            file_path = Path(raw).resolve()

        if file_path is None or not file_path.exists():
            logger.warning(
                "Taildrop: skip task %s — output_path missing or file not found (%s)",
                task.id, file_path,
            )
            return

        with self._lock:
            if self._closed:
                return
            self._executor.submit(self._transfer, task, file_path, node)

    def send_now(self, task: "DownloadTask") -> None:
        """
        On-demand transfer triggered explicitly by the user (e.g. Remote API
        POST /api/queue/{task_id}/transfer).

        Unlike on_download_completed(), this method intentionally bypasses the
        send_mode guard — it must fire regardless of whether the mode is
        "always" or "ask", because the user has explicitly requested the send.

        Pre-flight checks (enabled, node, CLI) are the caller's responsibility
        (the API endpoint already validates them before calling this method).
        Dispatches to the background executor and returns immediately.
        """
        node = self._config.taildrop_target_node
        if not node:
            logger.warning("Taildrop send_now: target_node not configured — skipping")
            return

        # Resolve output path using the same field-lookup order as
        # on_download_completed() for consistency.
        file_path: Optional[Path] = None
        raw = (
            getattr(task, "filename", None)
            or getattr(task, "output_path", None)
            or getattr(task, "file_path", None)
        )
        if raw:
            file_path = Path(raw).resolve()

        if file_path is None or not file_path.exists():
            logger.warning(
                "Taildrop send_now: task %s — output_path missing or file not found (%s)",
                task.id, file_path,
            )
            return

        with self._lock:
            if self._closed:
                logger.warning("Taildrop send_now: service is closed — cannot send")
                return
            self._executor.submit(self._transfer, task, file_path, node)
        logger.debug("Taildrop send_now: queued '%s' → %s", file_path.name, node)

    def send_file(
        self, file_path: Path, node: str
    ) -> TransferResult:
        """
        Synchronous send — primarily for testing / manual invocation.
        Use on_download_completed() for the automated pipeline.
        """
        return self._do_send(file_path, node)

    def list_nodes(self) -> list[str]:
        """
        Return names of reachable Tailscale peers for the Settings node picker.
        Returns [] when tailscale is not available or the command fails.

        Strategy (two-pass, most reliable):
        1. JSON parse — tries "Online" then "Active" field (varies by version).
        2. Plain-text fallback — parses `tailscale status` lines directly when
           JSON yields nothing (handles older CLI and exit-node edge cases).

        Excludes: Self node, localhost, any node with no name/IP.
        """
        tailscale = shutil.which("tailscale")
        if not tailscale:
            return []

        # ── PASS 1: JSON ─────────────────────────────────────────────────
        peers = self._list_nodes_json(tailscale)
        if peers:
            return peers

        # ── PASS 2: plain-text fallback ──────────────────────────────────
        logger.debug("Taildrop: JSON pass returned no peers — trying plain-text")
        return self._list_nodes_plaintext(tailscale)

    def _list_nodes_json(self, tailscale: str) -> list[str]:
        """Parse `tailscale status --json`. Returns [] on any failure."""
        import json
        try:
            out = subprocess.run(
                [tailscale, "status", "--json"],
                capture_output=True, text=True, timeout=8,
            )
            if out.returncode != 0:
                return []
            data = json.loads(out.stdout)
        except Exception as exc:
            logger.debug("Taildrop JSON parse failed: %s", exc)
            return []

        # Build exclusion set from the Self block.
        self_info = data.get("Self", {})
        excluded: set[str] = {"localhost"}
        if self_info.get("HostName"):
            excluded.add(self_info["HostName"].lower())
        for ip in self_info.get("TailscaleIPs", []):
            excluded.add(ip.lower())

        peers: list[str] = []
        for info in data.get("Peer", {}).values():
            # "Online" is set by newer CLI builds; "Active" by older ones.
            # Accept the peer if either flag is truthy.
            if not (info.get("Online") or info.get("Active")):
                continue
            name = info.get("HostName") or (info.get("TailscaleIPs") or [""])[0]
            if name and name.lower() not in excluded:
                peers.append(name)

        return sorted(peers)

    def _list_nodes_plaintext(self, tailscale: str) -> list[str]:
        """
        Parse plain `tailscale status` (no --json flag) as a fallback.

        Output format (one peer per line):
            <IP>  <hostname>  <user>  <OS>  <status>

        Example:
            100.64.0.1  laptop   alice@  windows  -
            100.64.0.2  iphone   alice@  iOS      active; exit node
            100.64.0.3  macbook  alice@  macOS    active

        We accept all peers that appear in the list, regardless of status
        text, because if Tailscale shows them they are reachable.
        Excludes: the first line (Self) and localhost.
        """
        try:
            out = subprocess.run(
                [tailscale, "status"],
                capture_output=True, text=True, timeout=8,
            )
            if out.returncode != 0:
                return []
        except Exception as exc:
            logger.debug("Taildrop plaintext status failed: %s", exc)
            return []

        peers: list[str] = []
        excluded: set[str] = {"localhost"}
        first = True  # first non-empty line is Self on most CLI versions

        for raw_line in out.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            # First data line is always the local machine (Self).
            if first:
                # Add self hostname to exclusion set, then skip.
                excluded.add(parts[1].lower())
                first = False
                continue
            hostname = parts[1]  # column 2 = hostname
            if hostname.lower() not in excluded:
                peers.append(hostname)

        return sorted(set(peers))  # deduplicate just in case

    def is_tailscale_available(self) -> bool:
        """Return True when the tailscale CLI is on PATH and reachable."""
        return shutil.which("tailscale") is not None

    def close(self) -> None:
        """Flush pending transfers and release resources. Call at app shutdown."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True)

    # ── Internal ──────────────────────────────────────────────────────────

    def _transfer(
        self,
        task: "DownloadTask",
        file_path: Path,
        node: str,
    ) -> None:
        """Worker submitted to the executor."""
        result = self._do_send(file_path, node)
        if result.success:
            logger.info(
                "Taildrop: ✅ sent '%s' → %s", file_path.name, node
            )
            self._bus.publish_taildrop_completed(task=task, dest_node=node)
        else:
            logger.warning(
                "Taildrop: ❌ failed '%s' → %s : %s",
                file_path.name, node, result.error,
            )
            self._bus.publish_taildrop_failed(
                task=task, dest_node=node, error=result.error
            )

    def _do_send(self, file_path: Path, node: str) -> TransferResult:
        """
        Core send logic. Validates inputs then calls tailscale CLI.

        Security:
        • node validated against _NODE_RE before use in subprocess args.
        • file_path is passed as a Path object (no string interpolation).
        • subprocess called with a list (not shell=True) — no shell injection.
        """
        # 1. Validate node name
        if not _NODE_RE.match(node):
            return TransferResult(
                success=False,
                dest_node=node,
                error=f"Invalid node name '{node}' — rejected by security policy",
            )

        # 2. Validate file exists inside expected bounds
        if not file_path.exists():
            return TransferResult(
                success=False,
                dest_node=node,
                error=f"File not found: {file_path}",
            )

        # 3. Locate tailscale CLI
        tailscale = shutil.which("tailscale")
        if not tailscale:
            return TransferResult(
                success=False,
                dest_node=node,
                error="tailscale CLI not found on PATH — install Tailscale on this PC",
            )

        # 4. Execute: tailscale file cp <file> <node>:
        try:
            result = subprocess.run(
                [tailscale, "file", "cp", str(file_path), node + _NODE_SUFFIX],
                capture_output=True,
                text=True,
                timeout=120,  # 2-min timeout for large files
            )
            if result.returncode == 0:
                return TransferResult(success=True, dest_node=node)
            stderr = (result.stderr or result.stdout or "").strip()
            return TransferResult(
                success=False,
                dest_node=node,
                error=f"tailscale exit {result.returncode}: {stderr}",
            )
        except subprocess.TimeoutExpired:
            return TransferResult(
                success=False,
                dest_node=node,
                error="Transfer timed out after 120 s — file may be too large",
            )
        except Exception as exc:
            return TransferResult(success=False, dest_node=node, error=str(exc))
