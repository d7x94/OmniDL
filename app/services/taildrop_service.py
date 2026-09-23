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
import sys
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from utils.naming import build_filename_from_task, sanitise_for_filesystem, to_ascii_filename

if TYPE_CHECKING:
    from app.event_bus import EventBus
    from domain.models.download_task import DownloadTask
    from infrastructure.config.config_manager import ConfigManager

logger = logging.getLogger(__name__)

# Suppress console window on Windows for all subprocess calls.
# Only injected on win32 — on Linux/macOS creationflags must be absent entirely.
_SUBPROCESS_EXTRA: dict = (
    {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    if sys.platform == "win32"
    else {}
)

# ── tailscale CLI stderr sanitiser ───────────────────────────────────────
# `tailscale file cp` draws a progress bar, so its stderr carries ANSI escapes
# and CR overwrites, and it prefixes a "# warning: <peer> is reportedly
# offline; trying anyway" advisory before the real error.  Logged raw that
# split one record across several physical lines of omnidl_debug.log and left
# the advisory as the whole user-visible error while the actual cause sat on an
# untimestamped line below it (2026-09-17 11:06:41: the real failure was
# "502 Bad Gateway").
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _clean_cli_error(raw: str) -> str:
    """Collapse tailscale CLI output into one log-safe line.

    Drops the "# warning:" advisory unless it is all tailscale printed.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for chunk in _ANSI_RE.sub("", raw).replace("\r", "\n").splitlines():
        line = chunk.strip()
        if not line:
            continue
        (warnings if line.startswith("#") else errors).append(line)
    return " | ".join(errors or warnings)


# ── Security: allowlist for Tailscale node names / IPs ───────────────────
# Accepts:
#   • Tailscale Magic DNS names  e.g. "iphone", "my-iphone", "pixel-7"
#   • Dotted IPv4                e.g. "100.64.0.5"
#   • FQDN form                  e.g. "iphone.tail1abc2.ts.net"
# Rejects anything with shell metacharacters, path separators, or spaces.
_NODE_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,252}[A-Za-z0-9])?$")

# Destination suffix required by Tailscale CLI file send.
# The trailing colon tells tailscale "this is a node name, not a local path".
_NODE_SUFFIX = ":"


@dataclass(frozen=True)
class TransferResult:
    success: bool
    dest_node: str
    error: str = ""
    # Name the peer actually received.  Empty when the send failed.
    sent_name: str = ""


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

    @property
    def bus(self) -> "EventBus":
        """Public read-only handle on the event bus (UI subscribes to convert events)."""
        return self._bus

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
        # BUG-GD: use the full node list so multi-device auto-send works.
        # taildrop_target_nodes already falls back to [taildrop_target_node]
        # for legacy single-node configs, so no migration needed.
        nodes = self._config.taildrop_target_nodes
        if not nodes:
            logger.debug("Taildrop: skip — no target nodes configured")
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
                task.id,
                file_path,
            )
            return

        with self._lock:
            if self._closed:
                return
            # Submit one transfer job per node; the single-worker executor
            # serialises them so Tailscale is not hammered concurrently.
            for node in nodes:
                self._executor.submit(self._transfer, task, file_path, node)

    def send_converted_file(self, out_path: Path) -> None:
        """
        Hook for the convert pipeline.

        Called from ConvertTab.on_done() after FFmpeg finishes successfully.
        Returns immediately — the actual transfer runs in the background executor.

        Design notes
        ────────────
        • Reuses the same executor, _do_send(), and security validation as the
          download pipeline so there is a single code path for all Taildrop sends.
        • Does NOT require a DownloadTask — the convert pipeline has no task object.
        • A disabled Taildrop or missing node silently no-ops; callers never need
          to guard against exceptions from this method.
        • Respects send_mode — consistent with on_download_completed().
          When send_mode == "ask", the auto-send is skipped.  A future
          manual-trigger UI for the convert pipeline (e.g. a "Send to iPhone"
          button on the finished card) should call send_file() directly and
          bypass this guard, exactly as send_now() does for the download pipeline.
        • Transfer result is broadcast on the event bus as
          CONVERT_TAILDROP_COMPLETED / CONVERT_TAILDROP_FAILED so the UI can
          react (e.g. show a toast) without coupling to this service directly.
        """
        if not self._config.taildrop_enabled:
            return
        # ── FIX: Respect send_mode, consistent with on_download_completed ──
        # Default config has send_mode="ask" which means the user must trigger
        # transfers manually.  Without this guard, send_converted_file() would
        # silently attempt (and fail) a Taildrop send on every conversion even
        # when the user has not opted in to automatic sending.
        if self._config.taildrop_send_mode == "ask":
            logger.debug("Taildrop convert: skip auto-send — send_mode is 'ask'")
            return
        node = self._config.taildrop_target_node
        if not node:
            logger.debug("Taildrop convert: skip — target_node not configured")
            return
        if not out_path.exists():
            logger.warning("Taildrop convert: skip — file not found: %s", out_path)
            return

        with self._lock:
            if self._closed:
                logger.warning(
                    "Taildrop convert: service is closed — cannot send '%s'",
                    out_path.name,
                )
                return
            self._executor.submit(self._transfer_converted, out_path, node)
        logger.debug("Taildrop convert: queued '%s' → %s", out_path.name, node)

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
                task.id,
                file_path,
            )
            return

        with self._lock:
            if self._closed:
                logger.warning("Taildrop send_now: service is closed — cannot send")
                return
            self._executor.submit(self._transfer, task, file_path, node)
        logger.debug("Taildrop send_now: queued '%s' → %s", file_path.name, node)

    def send_file(self, file_path: Path, node: str) -> TransferResult:
        """
        Synchronous send — primarily for testing / manual invocation.
        Use on_download_completed() for the automated pipeline.
        """
        return self._do_send(file_path, node)

    def send_file_to_nodes(
        self,
        file_path: Path,
        nodes: list,
        on_node_done: Optional[Callable[..., None]] = None,
        on_node_error: Optional[Callable[..., None]] = None,
        task: "Optional[DownloadTask]" = None,
        specific_files_override: "Optional[list[Path]]" = None,
    ) -> None:
        """Send *file_path* to every node in *nodes* concurrently.

        Each node gets its own executor slot so a slow or unreachable peer
        does not block the others.  Callbacks fire on the worker thread —
        callers must marshal to the UI thread via ``widget.after()`` or a
        ``_ui_queue``.

        Parameters
        ----------
        file_path:
            Absolute path to the file to send.  Must exist at call time.
        nodes:
            List of validated Tailscale node names / IPs.  Any entry that
            fails the ``_NODE_RE`` allowlist check is silently skipped with a
            warning log (defence-in-depth — callers should pre-validate too).
        on_node_done:
            Optional ``Callable[[str], None]`` called with the node name after
            each successful transfer.
        on_node_error:
            Optional ``Callable[[str, str], None]`` called with
            ``(node_name, error_message)`` after each failed transfer.
        task:
            Optional DownloadTask.  When provided and task.gallery_dl_files
            is set, only those specific files are zipped (not the full dir).
        specific_files_override:
            Optional list of Path objects selected by the user via the file
            picker dialog.  Takes precedence over task.gallery_dl_files when
            provided.  Used when the user explicitly picks which files to send
            from a directory (e.g. after an ambiguous single-task download).
        """
        if not file_path.exists():
            logger.warning("send_file_to_nodes: file not found: %s", file_path)
            return

        safe_nodes = [n for n in nodes if _NODE_RE.match(n)]
        skipped = set(nodes) - set(safe_nodes)
        if skipped:
            logger.warning("send_file_to_nodes: skipped invalid node names: %s", skipped)

        if not safe_nodes:
            logger.warning("send_file_to_nodes: no valid nodes — nothing to send")
            return

        # Build specific_files — user's picker selection takes priority over
        # task.gallery_dl_files so the file picker result is always respected.
        specific_files: "list[Path] | None" = None
        if specific_files_override is not None:
            specific_files = [f for f in specific_files_override if f.exists()]
        elif task is not None:
            gdl = getattr(task, "gallery_dl_files", None)
            if gdl:
                specific_files = [Path(f) for f in gdl if Path(f).exists()]

        safe_display = build_filename_from_task(task, ext=file_path.suffix.lstrip(".")) if task else None

        def _send_one(node: str) -> None:
            result = self._do_send(file_path, node, specific_files=specific_files, display_name=safe_display)
            if result.success:
                logger.info(
                    "send_file_to_nodes: ✓ '%s' → %s", result.sent_name or file_path.name, node
                )
                if on_node_done:
                    try:
                        on_node_done(node)
                    except Exception as exc:
                        logger.warning("on_node_done raised: %s", exc)
            else:
                logger.warning(
                    "send_file_to_nodes: ✗ '%s' → %s: %s",
                    file_path.name,
                    node,
                    result.error,
                )
                if on_node_error:
                    try:
                        on_node_error(node, result.error or "unknown error")
                    except Exception as exc:
                        logger.warning("on_node_error raised: %s", exc)

        with self._lock:
            if self._closed:
                logger.warning("send_file_to_nodes: service is closed — skipping")
                return
            for node in safe_nodes:
                self._executor.submit(_send_one, node)

        logger.debug(
            "send_file_to_nodes: queued '%s' → %s node(s): %s",
            file_path.name,
            len(safe_nodes),
            safe_nodes,
        )

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
                capture_output=True,
                text=True,
                timeout=8,
                **_SUBPROCESS_EXTRA,
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
                capture_output=True,
                text=True,
                timeout=8,
                **_SUBPROCESS_EXTRA,
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
        # For gallery-dl image downloads, task.gallery_dl_files contains the
        # exact files downloaded in this task.  Pass them to _do_send so it
        # can zip only those files instead of the entire account directory.
        specific_files: list[Path] | None = None
        gdl = getattr(task, "gallery_dl_files", None)
        # BUG-GA: check for None explicitly — empty list [] is falsy but
        # means "gallery-dl ran but scan found nothing", which is different
        # from None (yt-dlp task, no gallery_dl_files attribute).
        if gdl is not None:
            specific_files = [Path(f) for f in gdl if Path(f).exists()]

        safe_display = build_filename_from_task(task, ext=file_path.suffix.lstrip("."))
        result = self._do_send(file_path, node, specific_files=specific_files, display_name=safe_display)
        if result.success:
            logger.info("Taildrop: ✅ sent '%s' → %s", result.sent_name or file_path.name, node)
            self._bus.publish_taildrop_completed(task=task, dest_node=node)
        else:
            logger.warning(
                "Taildrop: ❌ failed '%s' → %s : %s",
                file_path.name,
                node,
                result.error,
            )
            self._bus.publish_taildrop_failed(task=task, dest_node=node, error=result.error)

    def _transfer_converted(self, out_path: Path, node: str) -> None:
        """
        Worker for send_converted_file() — submitted to the background executor.

        Analogous to _transfer() but operates on a bare Path instead of a
        DownloadTask, and emits CONVERT_TAILDROP_* events on the bus.
        """
        result = self._do_send(out_path, node, display_name=sanitise_for_filesystem(out_path.name))
        if result.success:
            logger.info(
                "Taildrop convert: ✅ sent '%s' → %s", result.sent_name or out_path.name, node
            )
            self._bus.publish_convert_taildrop_completed(out_path=out_path, dest_node=node)
        else:
            logger.warning(
                "Taildrop convert: ❌ failed '%s' → %s : %s",
                out_path.name,
                node,
                result.error,
            )
            self._bus.publish_convert_taildrop_failed(out_path=out_path, dest_node=node, error=result.error)

    def _do_send(
        self,
        file_path: Path,
        node: str,
        specific_files: "list[Path] | None" = None,
        display_name: Optional[str] = None,
    ) -> TransferResult:
        """
        Core send logic. Validates inputs then calls tailscale CLI.

        Security:
        • node validated against _NODE_RE before use in subprocess args.
        • file_path is passed as a Path object (no string interpolation).
        • subprocess called with a list (not shell=True) — no shell injection.

        Directory support (BUG-BV):
        • Tailscale CLI rejects directories with "directories not supported".
        • When file_path is a directory, it is zipped into a NamedTemporaryFile
          first, the zip is sent under --name <folder>.zip, then the temp file
          is deleted in a finally block regardless of success or failure.

        specific_files (BUG-BW):
        • When provided, only these files are zipped instead of the full directory.
        • Used by gallery-dl image downloads where multiple posts from the same
          account share one directory — without this, successive downloads zip the
          entire growing account folder, sending previously-sent images again.
        """
        # 1. Validate node name
        if not _NODE_RE.match(node):
            return TransferResult(
                success=False,
                dest_node=node,
                error=f"Invalid node name '{node}' — rejected by security policy",
            )

        # 2. Validate path exists
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

        # 4. If path is a directory, zip it to a temp file.
        #    send_path and display_name are updated; tmp_zip is cleaned up in finally.
        tmp_zip: Optional[Path] = None
        send_path = file_path
        display_name = display_name or file_path.name

        try:
            if file_path.is_dir():
                # Use display_name (already defaulted to file_path.name above) so a
                # descriptive name built from the task survives the zipping step.
                safe_stem = sanitise_for_filesystem(display_name)
                display_name = safe_stem if safe_stem.endswith(".zip") else safe_stem + ".zip"
                logger.debug(
                    "Taildrop: '%s' is a directory — zipping as '%s'",
                    file_path.name,
                    display_name,
                )
                fd, tmp_str = tempfile.mkstemp(suffix=".zip", prefix="omnidl_td_")
                import os as _os

                _os.close(fd)
                tmp_zip = Path(tmp_str)
                with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    if specific_files:
                        # BUG-BW: zip only the files from this task, not the
                        # entire account directory which accumulates across downloads.
                        for member in sorted(specific_files):
                            if member.is_file():
                                # BUG-BT: guard against files that land outside
                                # file_path.parent (e.g. rescue files from a
                                # different subdir) — fall back to bare filename
                                # so the zip still includes them instead of
                                # crashing the entire send silently.
                                try:
                                    arc_path = member.relative_to(file_path.parent)
                                except ValueError:
                                    arc_path = Path(member.name)
                                zf.write(member, arc_path)
                        logger.debug(
                            "Taildrop: zipped %d specific file(s) (not full dir)",
                            len([f for f in specific_files if f.is_file()]),
                        )
                    else:
                        for member in sorted(file_path.rglob("*")):
                            if member.is_file():
                                zf.write(member, member.relative_to(file_path.parent))
                send_path = tmp_zip
                logger.debug("Taildrop: zip ready — %d byte(s)", tmp_zip.stat().st_size)

            # 5. Execute: tailscale file cp [--name <name>] <send_path> <node>:
            #
            # Filename policy (BUG-TD-NAME):
            # Older Taildrop receivers (iOS / macOS) answered "400 Bad Request:
            # invalid filename" for non-ASCII names, so this used to force an
            # ASCII name for every send.  That flattening was lossy — it deleted
            # Vietnamese diacritics, CJK and emoji outright, so the iPhone got
            # "YUSUKI cu y tht ng yu" or a name starting with a bare " - ".
            #
            # Now the full Unicode name is attempted first and the transliterated
            # ASCII name is used only as a retry, so a modern receiver keeps the
            # complete name and an old one still gets a readable fallback.
            safe_name = sanitise_for_filesystem(display_name)
            ascii_name = to_ascii_filename(safe_name)

            # Candidate names in preference order. ``None`` = no --name flag, the
            # peer then uses send_path's own name (only valid for real files).
            attempts: list[Optional[str]] = []
            if tmp_zip is not None or safe_name != file_path.name:
                attempts.append(safe_name)
            else:
                attempts.append(None)
            if not safe_name.isascii() and ascii_name and ascii_name != safe_name:
                attempts.append(ascii_name)

            last: Optional[TransferResult] = None
            for index, name in enumerate(attempts):
                cmd = [tailscale, "file", "cp"]
                if name is not None:
                    cmd += ["--name", name]
                cmd += [str(send_path), node + _NODE_SUFFIX]

                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,  # 5-min timeout for large files / multi-image zips
                        **_SUBPROCESS_EXTRA,
                    )
                except subprocess.TimeoutExpired:
                    # A timeout says nothing about the filename — do not retry.
                    return TransferResult(
                        success=False,
                        dest_node=node,
                        error="Transfer timed out after 300 s — file may be too large",
                    )
                except Exception as exc:
                    return TransferResult(success=False, dest_node=node, error=str(exc))

                if result.returncode == 0:
                    return TransferResult(
                        success=True,
                        dest_node=node,
                        sent_name=name if name is not None else send_path.name,
                    )

                stderr = _clean_cli_error(result.stderr or result.stdout or "")
                last = TransferResult(
                    success=False,
                    dest_node=node,
                    error=f"tailscale exit {result.returncode}: {stderr}",
                )
                # Only a filename rejection is worth an ASCII retry; a 502/offline
                # peer would just re-upload the whole file and fail again.
                if "invalid filename" not in stderr.lower():
                    break
                if index + 1 < len(attempts):
                    logger.info(
                        "Taildrop: peer rejected %r (%s) — retrying as %r",
                        name if name is not None else send_path.name,
                        stderr or f"exit {result.returncode}",
                        attempts[index + 1],
                    )

            return last or TransferResult(
                success=False, dest_node=node, error="no transfer attempt was made"
            )

        finally:
            if tmp_zip is not None and tmp_zip.exists():
                try:
                    tmp_zip.unlink()
                    logger.debug("Taildrop: deleted temp zip %s", tmp_zip)
                except Exception as exc:
                    logger.warning("Taildrop: failed to delete temp zip %s: %s", tmp_zip, exc)
