"""
app/services/doc_convert_service.py
Document conversion: Markdown <-> PDF, HTML <-> PDF, Office <-> PDF.

Design constraints
──────────────────
• Three independent back-ends, each optional at runtime:
    - ``markdown``   -> Markdown to HTML                    (pure Python)
    - ``weasyprint`` -> HTML to PDF                         (pure Python)
    - ``pypdf``      -> PDF text extraction (PDF to md/html) (pure Python)
    - LibreOffice    -> Office to PDF and PDF to DOCX        (external binary)
  ``capabilities()`` reports which routes are usable so the desktop tab and
  the Remote API can grey out / 503 what the host cannot do, instead of
  failing with an opaque ImportError deep inside a worker thread.

• HTML to PDF is a *hostile-input* path: an HTML document can reference
  ``file:///etc/passwd`` or ``http://internal-host/`` through <img>, <link>,
  @import, or a CSS url(). WeasyPrint would happily fetch both (local file
  disclosure + SSRF). Every render therefore runs through a url_fetcher that
  allows only ``data:`` URIs and local files inside the source document's own
  directory; everything else raises before a byte is read.

• LibreOffice is driven headless with a private, per-call user profile
  (``-env:UserInstallation``). Without it, two concurrent conversions fight
  over the single default profile and the second one silently exits 0 having
  produced nothing.

• Stateless across calls — no job registry, no shared mutable state, so no
  lock is needed. ``convert()`` is blocking; callers run it off their own
  thread (UI: threading.Thread, API: asyncio.to_thread).
"""

from __future__ import annotations

import html as html_mod
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NoReturn, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from utils.i18n import t
from utils.soffice_locator import locate_soffice

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────


class DocConvertError(Exception):
    """Base exception for document conversion."""


class DocConvertUnsupportedError(DocConvertError):
    """The requested source-extension -> target-format pair has no route."""


class DocConvertToolMissingError(DocConvertError):
    """A required back-end (Python package or LibreOffice) is not installed."""


class DocConvertBlockedResource(DocConvertError):
    """The document referenced a resource the renderer refuses to fetch."""


class DocConvertCancelled(DocConvertError):
    """The caller set the cancel event before/while the conversion ran."""


# ── Format tables ─────────────────────────────────────────────────────────

MARKDOWN_EXTS: frozenset[str] = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mdtxt"})
HTML_EXTS: frozenset[str] = frozenset({".html", ".htm", ".xhtml"})
PDF_EXTS: frozenset[str] = frozenset({".pdf"})
OFFICE_EXTS: frozenset[str] = frozenset(
    {
        ".doc",
        ".docx",
        ".docm",
        ".odt",
        ".rtf",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".ods",
        ".csv",
        ".ppt",
        ".pptx",
        ".odp",
    }
)

SOURCE_EXTS: frozenset[str] = MARKDOWN_EXTS | HTML_EXTS | PDF_EXTS | OFFICE_EXTS

TARGET_FORMATS: tuple[str, ...] = ("pdf", "html", "md", "docx")

# (source kind, target format) -> internal route name.
# Exactly the matrix the feature asks for — Markdown <-> PDF, HTML <-> PDF,
# Office <-> PDF — plus md->html, which the md->pdf pipeline produces anyway.
_ROUTES: dict[tuple[str, str], str] = {
    ("md", "pdf"): "md2pdf",
    ("md", "html"): "md2html",
    ("html", "pdf"): "html2pdf",
    ("pdf", "md"): "pdf2md",
    ("pdf", "html"): "pdf2html",
    ("pdf", "docx"): "pdf2docx",
    ("office", "pdf"): "office2pdf",
}

# Back-end each route needs, for capabilities() and the pre-flight check.
_ROUTE_BACKEND: dict[str, str] = {
    "md2html": "markdown",
    "md2pdf": "weasyprint",  # also needs markdown; checked explicitly below
    "html2pdf": "weasyprint",
    "pdf2md": "pypdf",
    "pdf2html": "pypdf",
    "pdf2docx": "libreoffice",
    "office2pdf": "libreoffice",
}

# LibreOffice can hang on a malformed document; a bounded wait keeps the
# worker thread (and the API's to_thread slot) from leaking forever.
_SOFFICE_TIMEOUT_SEC = 300

_MD_EXTENSIONS = ["extra", "sane_lists", "toc", "nl2br"]


def source_kind(ext: str) -> Optional[str]:
    """Map a file extension (with leading dot, any case) to a source kind."""
    ext = ext.lower()
    if ext in MARKDOWN_EXTS:
        return "md"
    if ext in HTML_EXTS:
        return "html"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in OFFICE_EXTS:
        return "office"
    return None


def targets_for(ext: str) -> list[str]:
    """Target formats reachable from *ext*, in TARGET_FORMATS order."""
    kind = source_kind(ext)
    if kind is None:
        return []
    return [fmt for fmt in TARGET_FORMATS if (kind, fmt) in _ROUTES]


@dataclass
class DocConvertCapability:
    """Which back-ends are present on this host."""

    markdown: bool
    weasyprint: bool
    pypdf: bool
    libreoffice: bool
    soffice_path: Optional[str] = None
    routes: dict[str, bool] = field(default_factory=dict)


# ── Back-end probes ───────────────────────────────────────────────────────


def _have_module(name: str) -> bool:
    try:
        __import__(name)
    except Exception:  # ImportError, or a broken native dependency
        return False
    return True


def capabilities() -> DocConvertCapability:
    """Report which conversion back-ends and routes are available right now.

    Not cached: LibreOffice can be installed while OmniDL is running and
    ``locate_soffice()`` already caches the expensive part.
    """
    have_md = _have_module("markdown")
    have_weasy = _have_module("weasyprint")
    have_pypdf = _have_module("pypdf")
    soffice = locate_soffice()

    backend_ok = {
        "markdown": have_md,
        "weasyprint": have_weasy,
        "pypdf": have_pypdf,
        "libreoffice": soffice is not None,
    }
    routes: dict[str, bool] = {}
    for (kind, fmt), route in _ROUTES.items():
        ok = backend_ok[_ROUTE_BACKEND[route]]
        if route == "md2pdf":
            ok = ok and have_md  # needs both markdown and weasyprint
        routes[f"{kind}->{fmt}"] = ok

    return DocConvertCapability(
        markdown=have_md,
        weasyprint=have_weasy,
        pypdf=have_pypdf,
        libreoffice=soffice is not None,
        soffice_path=str(soffice) if soffice else None,
        routes=routes,
    )



def _require_backend_for(route: str, kind: str, target_fmt: str) -> None:
    """Probe only the back-end *route* needs.

    Deliberately NOT ``capabilities()``: that probes all four back-ends, and
    importing WeasyPrint alone costs seconds. A pdf->md job must not pay for it.
    """
    needed = [_ROUTE_BACKEND[route]]
    if route == "md2pdf":
        needed.append("markdown")  # md2pdf needs the Markdown parser as well
    for backend in needed:
        ok = (locate_soffice() is not None) if backend == "libreoffice" else _have_module(backend)
        if not ok:
            raise DocConvertToolMissingError(
                f"The back-end required for {kind} -> {target_fmt} "
                f"({backend}) is not installed on this machine."
            )

# ── HTML scaffolding ──────────────────────────────────────────────────────

# Font stack deliberately lists faces with full Vietnamese (and CJK) coverage
# first: WeasyPrint resolves fonts through fontconfig / the system font list,
# and a stack of Latin-1-only faces renders Vietnamese diacritics as boxes.
_PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm; }
html { font-size: 12pt; }
body {
  font-family: "DejaVu Sans", "Segoe UI", "Noto Sans", "Liberation Sans",
               "Arial Unicode MS", sans-serif;
  line-height: 1.55;
  color: #111;
  word-wrap: break-word;
}
h1, h2, h3, h4, h5, h6 { line-height: 1.25; page-break-after: avoid; margin: 1.1em 0 .5em; }
h1 { font-size: 1.9em; border-bottom: 1px solid #ddd; padding-bottom: .2em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #eee; padding-bottom: .15em; }
h3 { font-size: 1.22em; }
p, li { orphans: 2; widows: 2; }
code, pre, kbd, samp {
  font-family: "DejaVu Sans Mono", "Consolas", "Liberation Mono", monospace;
  font-size: .9em;
}
pre {
  background: #f5f5f5; border: 1px solid #e2e2e2; border-radius: 4px;
  padding: .7em .9em; white-space: pre-wrap; word-break: break-word;
  page-break-inside: avoid;
}
code { background: #f5f5f5; border-radius: 3px; padding: .1em .3em; }
pre code { background: none; padding: 0; }
blockquote {
  margin: 1em 0; padding: .2em 1em; border-left: 4px solid #ccc; color: #444;
}
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ccc; padding: .45em .6em; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
img { max-width: 100%; }
a { color: #1a5fb4; text-decoration: none; word-break: break-all; }
hr { border: none; border-top: 1px solid #ddd; margin: 1.6em 0; }
"""

_HTML_DOC = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def _wrap_html(body: str, title: str, *, lang: str = "vi") -> str:
    return _HTML_DOC.format(
        lang=html_mod.escape(lang, quote=True),
        title=html_mod.escape(title),
        css=_PRINT_CSS,
        body=body,
    )


# ── WeasyPrint resource sandbox ───────────────────────────────────────────


def _make_url_fetcher(allowed_root: Path):
    """Return a WeasyPrint URL fetcher confined to *allowed_root*.

    Allows:
      • ``data:`` URIs                      (inline images / fonts)
      • ``file:`` URLs resolving inside *allowed_root*

    Rejects everything else — notably http(s) (SSRF against the machine that
    runs OmniDL, which on the Remote API path is reachable by any client) and
    local files outside the document's own folder (file disclosure).

    A rejection raises a plain (non-fatal) error, so WeasyPrint skips that one
    resource and still produces a PDF. Hard-failing the whole render would make
    the converter useless for the very common case of a document that links a
    remote logo. Every rejection is logged and recorded in ``.blocked``.
    """
    from weasyprint.urls import URLFetcher

    class _ConfinedURLFetcher(URLFetcher):
        def __init__(self, root: Path) -> None:
            # allowed_protocols is the belt; the checks below are the braces —
            # they also confine ``file:`` to one directory, which the protocol
            # allowlist alone cannot express.
            super().__init__(allowed_protocols=("file", "data"), allow_redirects=False)
            self._root = root.resolve()
            self.blocked: list[str] = []

        def fetch(self, url, headers=None):  # noqa: ANN001 - matches the base signature
            scheme = urlparse(url).scheme.lower()
            if scheme == "data":
                return super().fetch(url, headers)
            if scheme != "file":
                self._block(url, "remote resource")
            # url2pathname() takes everything after "file:" and does the
            # percent-decoding itself (plus the Windows drive-letter / UNC
            # handling that urlparse().path would throw away). Decoding here as
            # well would be a *double* decode, turning "%252e%252e" into "..".
            # WeasyPrint strips ?query from file URLs; do the same first.
            try:
                # URLError (non-local file:// authority) is an OSError subclass.
                local = Path(url2pathname(url.split("?")[0][len("file:") :])).resolve()
            except (OSError, ValueError):
                self._block(url, "unreadable local resource")
            if not (local == self._root or self._root in local.parents):
                self._block(url, "local file outside the document folder")
            return super().fetch(url, headers)

        def _block(self, url: str, reason: str) -> NoReturn:
            self.blocked.append(url)
            logger.warning("doc-convert: blocked %s referenced by document: %s", reason, url[:200])
            raise DocConvertBlockedResource(f"Blocked {reason}: {url[:120]}")

    return _ConfinedURLFetcher(allowed_root)


def _render_pdf(
    out_path: Path,
    base_dir: Path,
    *,
    html_string: Optional[str] = None,
    html_file: Optional[Path] = None,
) -> None:
    """Render HTML to *out_path*, resolving resources only inside *base_dir*.

    Pass ``html_file`` for a document that came off disk: WeasyPrint then runs
    the HTML5 encoding-sniffing algorithm (BOM, <meta charset>) instead of us
    guessing an encoding. ``html_string`` is for HTML we generated ourselves.
    """
    try:
        from weasyprint import HTML
    except Exception as exc:  # noqa: BLE001 - broken native deps look like this too
        raise DocConvertToolMissingError(
            "WeasyPrint is not available — HTML/Markdown to PDF is disabled."
        ) from exc

    fetcher = _make_url_fetcher(base_dir)
    if html_file is not None:
        doc = HTML(filename=str(html_file), url_fetcher=fetcher)
    else:
        doc = HTML(
            string=html_string or "",
            base_url=base_dir.resolve().as_uri() + "/",
            url_fetcher=fetcher,
        )
    doc.write_pdf(str(out_path))
    if fetcher.blocked:
        logger.warning(
            "doc-convert: %d external resource(s) were blocked while rendering '%s'",
            len(fetcher.blocked),
            out_path.name,
        )


# ── Text helpers ──────────────────────────────────────────────────────────

_PARA_SPLIT = re.compile(r"\n\s*\n")
# Characters that would be re-interpreted as *inline* Markdown syntax when
# extracted PDF text is written into a .md file. Deliberately narrow: escaping
# every punctuation mark (., -, (, )) turns ordinary prose into backslash soup.
_MD_ESCAPE = re.compile(r"([\\`*_\[\]<>|])")
# Line-leading characters that would start a heading / list / quote / rule.
_MD_LINE_ORDERED = re.compile(r"^(\s*\d+)([.)])")
_MD_LINE_MARKER = re.compile(r"^(\s*)([#>+]|[-=])")


def _escape_md_block(text: str) -> str:
    """Escape a block of extracted PDF text so it survives as literal Markdown."""
    lines = []
    for line in text.split("\n"):
        line = _MD_ESCAPE.sub(r"\\\1", line)
        # Escape the punctuation, not the digit: "1\." stops an ordered list,
        # "\1." does not.
        line = _MD_LINE_ORDERED.sub(r"\1\\\2", line)
        line = _MD_LINE_MARKER.sub(r"\1\\\2", line)
        lines.append(line)
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    """Read a text file, tolerating a BOM and non-UTF-8 bytes."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf_pages(path: Path, cancel: Optional[threading.Event]) -> list[str]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except Exception as exc:  # noqa: BLE001
        raise DocConvertToolMissingError(
            "pypdf is not installed — PDF to Markdown/HTML is disabled."
        ) from exc

    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise DocConvertError(f"Cannot read PDF: {exc}") from exc

    if reader.is_encrypted:
        # An owner-password-only PDF decrypts with the empty user password.
        opened = False
        try:
            opened = bool(reader.decrypt(""))
        except Exception:  # noqa: BLE001 - pypdf raises several types here
            opened = False
        if not opened:
            raise DocConvertError("The PDF is password protected and cannot be read.")

    pages: list[str] = []
    for index, page in enumerate(reader.pages):
        _check_cancelled(cancel)
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the job
            logger.warning("PDF page %d could not be extracted: %s", index + 1, exc)
            text = ""
        pages.append(text.replace("\r\n", "\n").replace("\r", "\n").strip())

    if not any(pages):
        raise DocConvertError(
            "No selectable text found in this PDF — it is most likely a scan. OCR is not supported."
        )
    return pages


def _check_cancelled(cancel: Optional[threading.Event]) -> None:
    if cancel is not None and cancel.is_set():
        raise DocConvertCancelled("Conversion cancelled")


# ── LibreOffice driver ────────────────────────────────────────────────────


def _run_soffice(
    src: Path,
    out_dir: Path,
    convert_to: str,
    *,
    infilter: Optional[str],
    cancel: Optional[threading.Event],
) -> Path:
    soffice = locate_soffice()
    if soffice is None:
        raise DocConvertToolMissingError(
            "LibreOffice was not found — Office <-> PDF conversion is unavailable. "
            "Install LibreOffice and try again."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    # Private profile per call: the default profile is a single-instance lock,
    # so two concurrent conversions would otherwise clash and one would exit 0
    # without writing anything.
    profile_dir = Path(tempfile.mkdtemp(prefix="omnidl-soffice-"))
    # Convert into a private staging dir so a name collision in out_dir cannot
    # be silently overwritten by LibreOffice (it never asks).
    stage_dir = Path(tempfile.mkdtemp(prefix="omnidl-docconv-"))

    target = f"{convert_to}:{infilter}" if infilter else convert_to
    cmd = [
        str(soffice),
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        target,
        "--outdir",
        str(stage_dir),
        str(src),
    ]

    logger.info("LibreOffice: %s -> %s", src.name, target)
    creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            stdout = _wait_with_cancel(proc, cancel)
        except DocConvertCancelled:
            _kill(proc)
            raise
        if proc.returncode != 0:
            tail = (stdout or b"").decode("utf-8", errors="replace")[-400:]
            raise DocConvertError(f"LibreOffice failed (exit {proc.returncode}): {tail.strip()}")

        produced = sorted(p for p in stage_dir.iterdir() if p.is_file())
        if not produced:
            raise DocConvertError(
                "LibreOffice produced no output — the source format is probably "
                "not supported by the installed version."
            )
        final = _unique_path(out_dir / produced[0].name)
        shutil.move(str(produced[0]), str(final))
        return final
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
        shutil.rmtree(stage_dir, ignore_errors=True)


def _wait_with_cancel(proc: subprocess.Popen, cancel: Optional[threading.Event]) -> bytes:
    """Wait for *proc*, polling the cancel event; enforce _SOFFICE_TIMEOUT_SEC."""
    waited = 0.0
    while True:
        try:
            stdout, _ = proc.communicate(timeout=0.5)
            return stdout or b""
        except subprocess.TimeoutExpired:
            waited += 0.5
            if cancel is not None and cancel.is_set():
                raise DocConvertCancelled("Conversion cancelled") from None
            if waited >= _SOFFICE_TIMEOUT_SEC:
                _kill(proc)
                raise DocConvertError(f"LibreOffice timed out after {_SOFFICE_TIMEOUT_SEC}s.") from None


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except Exception:  # noqa: BLE001 - best effort teardown
        logger.debug("LibreOffice process teardown failed", exc_info=True)


# ── Output naming ─────────────────────────────────────────────────────────


def _unique_path(path: Path) -> Path:
    """Return *path*, or ``name (1).ext`` / ``name (2).ext`` if it is taken."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 1000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise DocConvertError(f"Could not find a free output name for {path.name}")


# ── Service ───────────────────────────────────────────────────────────────


class DocConvertService:
    """Convert documents between Markdown, HTML, PDF and Office formats.

    Stateless — one instance can be shared by the desktop tab and the API.
    """

    def convert(
        self,
        src: Path,
        target_fmt: str,
        out_dir: Path,
        *,
        cancel_event: Optional[threading.Event] = None,
        on_progress: Optional[Callable[[float], None]] = None,
    ) -> Path:
        """Convert *src* to *target_fmt*, writing into *out_dir*.

        Returns the path actually written (never overwrites an existing file).
        Raises DocConvertUnsupportedError / DocConvertToolMissingError /
        DocConvertCancelled / DocConvertError.
        """
        src = Path(src)
        target_fmt = (target_fmt or "").strip().lower().lstrip(".")
        out_dir = Path(out_dir)

        if not src.is_file():
            raise DocConvertError(f"Source file not found: {src}")
        kind = source_kind(src.suffix)
        if kind is None:
            raise DocConvertUnsupportedError(f"Unsupported source type: {src.suffix or '(none)'}")
        if target_fmt not in TARGET_FORMATS:
            raise DocConvertUnsupportedError(f"Unsupported target format: {target_fmt}")
        route = _ROUTES.get((kind, target_fmt))
        if route is None:
            raise DocConvertUnsupportedError(
                f"Cannot convert {src.suffix.lstrip('.') or 'file'} to {target_fmt}"
            )

        _require_backend_for(route, kind, target_fmt)

        _check_cancelled(cancel_event)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._progress(on_progress, 5.0)

        handler = getattr(self, f"_{route}")
        result = handler(src, out_dir, cancel_event, on_progress)
        self._progress(on_progress, 100.0)
        logger.info("Converted '%s' -> '%s'", src.name, result.name)
        return result

    # ── Routes ────────────────────────────────────────────────────────────

    def _md2html(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        body = self._markdown_body(src)
        self._progress(on_progress, 60.0)
        _check_cancelled(cancel)
        out = _unique_path(out_dir / f"{src.stem}.html")
        out.write_text(_wrap_html(body, src.stem), encoding="utf-8")
        return out

    def _md2pdf(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        body = self._markdown_body(src)
        self._progress(on_progress, 40.0)
        _check_cancelled(cancel)
        out = _unique_path(out_dir / f"{src.stem}.pdf")
        _render_pdf(out, src.parent, html_string=_wrap_html(body, src.stem))
        return out

    def _html2pdf(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        self._progress(on_progress, 40.0)
        _check_cancelled(cancel)
        out = _unique_path(out_dir / f"{src.stem}.pdf")
        _render_pdf(out, src.parent, html_file=src)
        return out

    def _pdf2md(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        pages = _extract_pdf_pages(src, cancel)
        self._progress(on_progress, 70.0)
        chunks: list[str] = [f"# {src.stem}\n"]
        for i, text in enumerate(pages, start=1):
            chunks.append(f"\n## {t('docs.page_n', n=i)}\n")
            if not text:
                continue
            for para in _PARA_SPLIT.split(text):
                para = para.strip()
                if para:
                    chunks.append(_escape_md_block(para) + "\n")
        out = _unique_path(out_dir / f"{src.stem}.md")
        out.write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")
        return out

    def _pdf2html(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        pages = _extract_pdf_pages(src, cancel)
        self._progress(on_progress, 70.0)
        parts: list[str] = [f"<h1>{html_mod.escape(src.stem)}</h1>"]
        for i, text in enumerate(pages, start=1):
            parts.append(f"<h2>{html_mod.escape(t('docs.page_n', n=i))}</h2>")
            for para in _PARA_SPLIT.split(text):
                para = para.strip()
                if para:
                    parts.append("<p>" + html_mod.escape(para).replace("\n", "<br>") + "</p>")
        out = _unique_path(out_dir / f"{src.stem}.html")
        out.write_text(_wrap_html("\n".join(parts), src.stem), encoding="utf-8")
        return out

    def _pdf2docx(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        self._progress(on_progress, 20.0)
        return _run_soffice(src, out_dir, "docx", infilter="writer_pdf_import", cancel=cancel)

    def _office2pdf(self, src: Path, out_dir: Path, cancel, on_progress) -> Path:
        self._progress(on_progress, 20.0)
        return _run_soffice(src, out_dir, "pdf", infilter=None, cancel=cancel)

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _markdown_body(src: Path) -> str:
        try:
            import markdown
        except Exception as exc:  # noqa: BLE001
            raise DocConvertToolMissingError(
                "The 'markdown' package is not installed — Markdown conversion is disabled."
            ) from exc
        return markdown.markdown(_read_text(src), extensions=_MD_EXTENSIONS, output_format="html")

    @staticmethod
    def _progress(cb: Optional[Callable[[float], None]], pct: float) -> None:
        if cb is None:
            return
        try:
            cb(pct)
        except Exception:  # noqa: BLE001 - a broken UI callback must not fail the job
            logger.debug("doc-convert progress callback raised", exc_info=True)
