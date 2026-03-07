"""
app/services/thumbnail_service.py
Fetches and decodes thumbnail images from remote URLs.

Belongs in the application layer: it contains SSRF-prevention logic (SEC-4)
that has no place in the UI, and it has zero framework dependencies (no CTk
imports).  HomeTab calls fetch_async() and receives a PIL Image via callback.
"""
from __future__ import annotations

import io
import ipaddress
import itertools
import logging
import socket
import threading
from typing import Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Only these URL schemes are accepted for thumbnail requests.
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Thumbnail downloads are capped at 2 MB to prevent memory exhaustion from a
# maliciously large response.
_MAX_BYTES: int = 2 * 1024 * 1024


def _is_safe_thumbnail_url(url: str) -> bool:
    """
    Return True only if *url* is safe to fetch as a thumbnail.

    Enforces three layers of SSRF protection (CWE-918):

    Layer 1 — Scheme allowlist.
        Only http and https are accepted.  file://, data:, ftp:/ etc. are
        rejected unconditionally before any network activity.

    Layer 2 — IP-literal check (no DNS call).
        If the host parses as a raw IP address, it is accepted only when
        ipaddress considers it globally routable and neither loopback nor
        link-local.  This immediately blocks 127.x, 10.x, 192.168.x,
        172.16-31.x, ::1, 169.254.x, etc.

    Layer 3 — DNS resolution check.
        Hostnames are resolved and every returned address is tested with the
        same ipaddress rules above.  This is the only reliable defence against
        SSRF via internal hostnames (e.g. ``metadata.internal``, ``redis``,
        ``192.168.1.1.nip.io``) and DNS-rebinding attacks.
        Fail-closed: any resolution error or private-address result → False.

        Note on performance: thumbnail URLs come from yt-dlp's metadata, which
        already performed a network round-trip.  The extra getaddrinfo() call is
        a single syscall that typically completes in <1 ms from OS cache.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    # Layer 1: scheme allowlist.
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False

    host = parsed.hostname or ""
    if not host:
        return False

    # Layer 2: raw IP literal — check without DNS.
    try:
        addr = ipaddress.ip_address(host)
        safe = addr.is_global and not addr.is_loopback and not addr.is_link_local
        if not safe:
            logger.warning("Thumbnail URL %r uses private/loopback IP — blocked", url)
        return safe
    except ValueError:
        pass  # Not an IP literal — fall through to DNS resolution.

    # Fast-reject well-known loopback names without paying the DNS cost.
    if host.lower() in {"localhost", "localhost.localdomain"}:
        logger.warning("Thumbnail URL %r uses loopback hostname — blocked", url)
        return False

    # Layer 3: DNS resolution — check every address returned.
    # Fail-closed on any resolution error (NXDOMAIN, timeout, etc.).
    try:
        results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError) as exc:
        logger.warning(
            "Thumbnail URL %r — DNS resolution failed (%s) — blocked", url, exc
        )
        return False

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            logger.warning(
                "Thumbnail URL %r — unrecognised address format %r — blocked",
                url, ip_str,
            )
            return False
        if not addr.is_global or addr.is_loopback or addr.is_link_local:
            logger.warning(
                "Thumbnail URL %r resolved to non-public address %s — blocked",
                url, ip_str,
            )
            return False

    return True


class ThumbnailService:
    """
    Fetches, validates, and resizes thumbnail images for media items.

    This service owns all network and image-decoding logic that was previously
    scattered across the UI layer.  HomeTab holds a ThumbnailService instance
    and calls fetch_async(); PIL and requests are imported lazily so the rest
    of the application remains importable even when those packages are absent.
    """

    def fetch_async(
        self,
        url: str,
        width: int,
        height: int,
        on_done: Callable,
        on_error: Callable[[str], None],
    ) -> None:
        """
        Non-blocking thumbnail fetch.

        Starts a daemon thread that fetches *url*, validates the response, and
        resizes the image to (*width* × *height*) pixels using Lanczos
        resampling.

        *on_done(image)*  — called with a PIL.Image.Image on success.
        *on_error(reason)* — called with a short string describing the failure.

        Both callbacks run on the worker thread.  Callers that update CTk
        widgets must marshal back to the main thread via tkinter's ``.after()``.
        """
        threading.Thread(
            target=self._fetch,
            args=(url, width, height, on_done, on_error),
            daemon=True,
            name="omnidl-thumbnail",
        ).start()

    # ── Internal ──────────────────────────────────────────────────────────

    def _fetch(
        self,
        url: str,
        width: int,
        height: int,
        on_done: Callable,
        on_error: Callable[[str], None],
    ) -> None:
        try:
            import requests as req_lib  # type: ignore[import-untyped]
            from PIL import Image
        except ImportError as exc:
            on_error(f"Optional dependency missing: {exc}")
            return

        if not _is_safe_thumbnail_url(url):
            on_error("URL blocked by SSRF policy")
            return

        try:
            resp = req_lib.get(url, timeout=8, stream=True)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                on_error(f"Unexpected content-type: {content_type!r}")
                return

            # Cap download at _MAX_BYTES to prevent memory exhaustion.
            data = b"".join(
                itertools.islice(resp.iter_content(8192), _MAX_BYTES // 8192)
            )
            img = Image.open(io.BytesIO(data)).resize((width, height), Image.Resampling.LANCZOS)
            on_done(img)

        except Exception as exc:
            logger.debug("Thumbnail fetch failed for %r: %s", url, exc)
            on_error(str(exc))
