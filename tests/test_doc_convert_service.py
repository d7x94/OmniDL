"""
tests/test_doc_convert_service.py
Unit tests for app/services/doc_convert_service.py.

The pure-Python routes (Markdown/HTML/PDF) run for real. The LibreOffice
routes are exercised with a stubbed locator + subprocess so the suite stays
green on machines (and CI) without LibreOffice installed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import app.services.doc_convert_service as mod
from app.services.doc_convert_service import (
    DocConvertCancelled,
    DocConvertError,
    DocConvertService,
    DocConvertToolMissingError,
    DocConvertUnsupportedError,
    _escape_md_block,
    _make_url_fetcher,
    _unique_path,
    capabilities,
    source_kind,
    targets_for,
)

MD_SAMPLE = """# Tiêu đề

Đoạn văn **đậm** với `code`.

| A | B |
|---|---|
| 1 | 2 |
"""



def _spy_fetchers(monkeypatch) -> list:
    """Capture every confined fetcher the service builds, to inspect .blocked."""
    made: list = []
    real = mod._make_url_fetcher

    def _spy(root):
        fetcher = real(root)
        made.append(fetcher)
        return fetcher

    monkeypatch.setattr(mod, "_make_url_fetcher", _spy)
    return made

@pytest.fixture
def svc() -> DocConvertService:
    return DocConvertService()


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    p = tmp_path / "src" / "doc.md"
    p.parent.mkdir(parents=True)
    p.write_text(MD_SAMPLE, encoding="utf-8")
    return p


class TestFormatTables:
    @pytest.mark.parametrize(
        "ext,kind",
        [
            (".md", "md"),
            (".MARKDOWN", "md"),
            (".html", "html"),
            (".HTM", "html"),
            (".pdf", "pdf"),
            (".docx", "office"),
            (".xlsx", "office"),
            (".pptx", "office"),
            (".mp4", None),
            ("", None),
        ],
    )
    def test_source_kind(self, ext, kind):
        assert source_kind(ext) == kind

    def test_targets_for_matches_the_documented_matrix(self):
        assert targets_for(".md") == ["pdf", "html"]
        assert targets_for(".html") == ["pdf"]
        assert targets_for(".pdf") == ["html", "md", "docx"]
        assert targets_for(".docx") == ["pdf"]
        assert targets_for(".mp4") == []

    def test_capabilities_reports_every_route(self):
        caps = capabilities()
        assert set(caps.routes) == {
            "md->pdf",
            "md->html",
            "html->pdf",
            "pdf->md",
            "pdf->html",
            "pdf->docx",
            "office->pdf",
        }
        # markdown / weasyprint / pypdf are hard project dependencies
        assert caps.markdown and caps.weasyprint and caps.pypdf


class TestMarkdownRoutes:
    def test_md_to_html(self, svc, md_file, tmp_path):
        out = svc.convert(md_file, "html", tmp_path / "out")
        assert out.suffix == ".html"
        text = out.read_text(encoding="utf-8")
        assert "<h1" in text and "Tiêu đề" in text
        assert "<table>" in text  # the 'extra' extension must be enabled
        assert 'charset="utf-8"' in text

    def test_md_to_pdf(self, svc, md_file, tmp_path):
        out = svc.convert(md_file, "pdf", tmp_path / "out")
        assert out.suffix == ".pdf"
        assert out.read_bytes().startswith(b"%PDF")

    def test_progress_callback_reaches_100(self, svc, md_file, tmp_path):
        seen: list[float] = []
        svc.convert(md_file, "pdf", tmp_path / "out", on_progress=seen.append)
        assert seen and seen[-1] == 100.0

    def test_broken_progress_callback_does_not_fail_the_job(self, svc, md_file, tmp_path):
        def boom(_pct: float) -> None:
            raise RuntimeError("ui gone")

        out = svc.convert(md_file, "pdf", tmp_path / "out", on_progress=boom)
        assert out.is_file()


class TestHtmlRoutes:
    def test_html_to_pdf(self, svc, tmp_path):
        src = tmp_path / "page.html"
        src.write_text("<html><body><h1>Xin chào</h1></body></html>", encoding="utf-8")
        out = svc.convert(src, "pdf", tmp_path / "out")
        assert out.read_bytes().startswith(b"%PDF")

    def test_html_with_remote_image_is_never_fetched(self, svc, tmp_path, monkeypatch):
        """SSRF guard: a document must not make the server fetch a remote URL.

        Rendering still succeeds — the blocked resource is dropped — so a
        document that links a remote logo still converts.
        """
        made = _spy_fetchers(monkeypatch)
        src = tmp_path / "ssrf.html"
        src.write_text('<img src="http://127.0.0.1:1/x.png">', encoding="utf-8")
        out = svc.convert(src, "pdf", tmp_path / "out")
        assert out.read_bytes().startswith(b"%PDF")
        assert made and made[0].blocked == ["http://127.0.0.1:1/x.png"]

    def test_html_reading_a_file_outside_its_folder_is_blocked(self, svc, tmp_path, monkeypatch):
        """Local-file-disclosure guard."""
        made = _spy_fetchers(monkeypatch)
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        docdir = tmp_path / "docs"
        docdir.mkdir()
        src = docdir / "leak.html"
        src.write_text(f'<link rel="stylesheet" href="{secret.resolve().as_uri()}">', encoding="utf-8")
        out = svc.convert(src, "pdf", tmp_path / "out")
        assert out.read_bytes().startswith(b"%PDF")
        assert made and made[0].blocked, "the outside-folder stylesheet should have been blocked"
        assert b"top secret" not in out.read_bytes()


    def test_html_may_use_a_sibling_file(self, svc, tmp_path):
        css = tmp_path / "style.css"
        css.write_text("h1 { color: red; }", encoding="utf-8")
        src = tmp_path / "ok.html"
        src.write_text('<link rel="stylesheet" href="style.css"><h1>Hi</h1>', encoding="utf-8")
        out = svc.convert(src, "pdf", tmp_path / "out")
        assert out.read_bytes().startswith(b"%PDF")

    def test_non_utf8_html_is_still_readable(self, svc, tmp_path):
        src = tmp_path / "latin.html"
        src.write_bytes("<h1>café</h1>".encode("cp1252"))
        out = svc.convert(src, "pdf", tmp_path / "out")
        assert out.read_bytes().startswith(b"%PDF")


class TestUrlFetcher:
    def test_blocks_http(self, tmp_path):
        fetcher = _make_url_fetcher(tmp_path)
        with pytest.raises(DocConvertError):
            fetcher("http://example.com/a.png")

    def test_blocks_parent_directory_escape(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("x", encoding="utf-8")
        fetcher = _make_url_fetcher(sub)
        with pytest.raises(DocConvertError):
            fetcher(outside.resolve().as_uri())

    def test_blocks_sibling_directory_prefix_trick(self, tmp_path):
        """base=/x/docs must not accept /x/docs_evil/f — exact component match."""
        allowed = tmp_path / "docs"
        allowed.mkdir()
        evil = tmp_path / "docs_evil"
        evil.mkdir()
        target = evil / "f.txt"
        target.write_text("x", encoding="utf-8")
        fetcher = _make_url_fetcher(allowed)
        with pytest.raises(DocConvertError):
            fetcher(target.resolve().as_uri())

    def test_allows_data_uri(self, tmp_path):
        fetcher = _make_url_fetcher(tmp_path)
        response = fetcher("data:text/plain;base64,aGk=")
        try:
            assert response.read() == b"hi"
        finally:
            response.close()
        assert fetcher.blocked == []


class TestPdfRoutes:
    @pytest.fixture
    def pdf_file(self, svc, md_file, tmp_path) -> Path:
        return svc.convert(md_file, "pdf", tmp_path / "pdfs")

    def test_pdf_to_markdown(self, svc, pdf_file, tmp_path):
        out = svc.convert(pdf_file, "md", tmp_path / "out")
        assert out.suffix == ".md"
        text = out.read_text(encoding="utf-8")
        assert "Tiêu đề" in text
        assert "docs.page_n" not in text  # the i18n key must resolve

    def test_pdf_to_html_escapes_extracted_text(self, svc, tmp_path):
        """Text lifted out of a PDF must never be injected as live HTML."""
        src = tmp_path / "x.html"
        src.write_text("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>", encoding="utf-8")
        pdf = svc.convert(src, "pdf", tmp_path / "p")
        out = svc.convert(pdf, "html", tmp_path / "out")
        text = out.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in text
        assert "&lt;script&gt;" in text

    def test_scanned_pdf_without_text_raises(self, svc, tmp_path, monkeypatch):
        import app.services.doc_convert_service as mod

        monkeypatch.setattr(mod, "_extract_pdf_pages", mod._extract_pdf_pages)
        blank = tmp_path / "blank.html"
        blank.write_text("<html><body></body></html>", encoding="utf-8")
        pdf = svc.convert(blank, "pdf", tmp_path / "p")
        with pytest.raises(DocConvertError, match="No selectable text"):
            svc.convert(pdf, "md", tmp_path / "out")

    def test_encrypted_pdf_raises_a_clear_error(self, svc, tmp_path, monkeypatch):
        import app.services.doc_convert_service as mod

        class _FakeReader:
            is_encrypted = True

            def __init__(self, *_a, **_kw):
                pass

            def decrypt(self, _pw):
                return 0

        import pypdf

        monkeypatch.setattr(pypdf, "PdfReader", _FakeReader)
        src = tmp_path / "d.html"
        src.write_text("<h1>x</h1>", encoding="utf-8")
        pdf = mod.DocConvertService().convert(src, "pdf", tmp_path / "p")
        with pytest.raises(DocConvertError, match="password protected"):
            svc.convert(pdf, "md", tmp_path / "out")


class TestMarkdownEscaping:
    def test_inline_syntax_is_escaped(self):
        assert _escape_md_block("a *b* _c_ [d]") == r"a \*b\* \_c\_ \[d\]"

    def test_ordinary_punctuation_is_left_alone(self):
        assert _escape_md_block("Giá: 1.000 (VND) - xong!") == "Giá: 1.000 (VND) - xong!"

    def test_line_leading_markers_are_escaped(self):
        assert _escape_md_block("# not a heading") == r"\# not a heading"
        assert _escape_md_block("1. not a list") == r"1\. not a list"


class TestValidation:
    def test_missing_source(self, svc, tmp_path):
        with pytest.raises(DocConvertError, match="not found"):
            svc.convert(tmp_path / "nope.md", "pdf", tmp_path / "out")

    def test_unsupported_source_type(self, svc, tmp_path):
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"\x00")
        with pytest.raises(DocConvertUnsupportedError):
            svc.convert(src, "pdf", tmp_path / "out")

    def test_unsupported_target_format(self, svc, md_file, tmp_path):
        with pytest.raises(DocConvertUnsupportedError):
            svc.convert(md_file, "epub", tmp_path / "out")

    def test_route_that_does_not_exist(self, svc, md_file, tmp_path):
        with pytest.raises(DocConvertUnsupportedError, match="Cannot convert"):
            svc.convert(md_file, "docx", tmp_path / "out")

    def test_target_format_is_normalised(self, svc, md_file, tmp_path):
        out = svc.convert(md_file, " .PDF ", tmp_path / "out")
        assert out.suffix == ".pdf"

    def test_cancel_before_start(self, svc, md_file, tmp_path):
        ev = threading.Event()
        ev.set()
        with pytest.raises(DocConvertCancelled):
            svc.convert(md_file, "pdf", tmp_path / "out", cancel_event=ev)


class TestOutputNaming:
    def test_existing_output_is_never_overwritten(self, svc, md_file, tmp_path):
        out_dir = tmp_path / "out"
        first = svc.convert(md_file, "pdf", out_dir)
        second = svc.convert(md_file, "pdf", out_dir)
        assert first != second
        assert second.name == "doc (1).pdf"
        assert first.is_file() and second.is_file()

    def test_unique_path_returns_input_when_free(self, tmp_path):
        p = tmp_path / "a.txt"
        assert _unique_path(p) == p


class TestLibreOfficeRoutes:
    """LibreOffice is optional — stub the locator so these run everywhere."""

    def _stub(self, monkeypatch, tmp_path, *, present: bool, produce: bool = True, rc: int = 0):
        import app.services.doc_convert_service as mod

        monkeypatch.setattr(mod, "locate_soffice", lambda: (tmp_path / "soffice") if present else None)

        class _FakeProc:
            returncode = rc

            def __init__(self, cmd, **_kw):
                self.cmd = cmd
                outdir = Path(cmd[cmd.index("--outdir") + 1])
                target = cmd[cmd.index("--convert-to") + 1].split(":")[0]
                src = Path(cmd[-1])
                if produce:
                    outdir.mkdir(parents=True, exist_ok=True)
                    (outdir / f"{src.stem}.{target}").write_bytes(b"fake-output")

            def communicate(self, timeout=None):
                return (b"", b"")

            def kill(self):
                pass

        monkeypatch.setattr(mod.subprocess, "Popen", _FakeProc)
        return mod

    def test_office_to_pdf(self, monkeypatch, tmp_path):
        mod = self._stub(monkeypatch, tmp_path, present=True)
        src = tmp_path / "report.docx"
        src.write_bytes(b"PK\x03\x04")
        out = mod.DocConvertService().convert(src, "pdf", tmp_path / "out")
        assert out.name == "report.pdf"
        assert out.read_bytes() == b"fake-output"

    def test_pdf_to_docx_uses_the_writer_pdf_import_filter(self, monkeypatch, tmp_path):
        mod = self._stub(monkeypatch, tmp_path, present=True)
        seen: dict = {}
        real_popen = mod.subprocess.Popen

        class _Capture(real_popen):  # type: ignore[misc, valid-type]
            def __init__(self, cmd, **kw):
                seen["cmd"] = cmd
                super().__init__(cmd, **kw)

        monkeypatch.setattr(mod.subprocess, "Popen", _Capture)
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"%PDF-1.4")
        out = mod.DocConvertService().convert(src, "docx", tmp_path / "out")
        assert out.name == "scan.docx"
        assert "docx:writer_pdf_import" in seen["cmd"]

    def test_private_profile_is_passed(self, monkeypatch, tmp_path):
        mod = self._stub(monkeypatch, tmp_path, present=True)
        seen: dict = {}
        real_popen = mod.subprocess.Popen

        class _Capture(real_popen):  # type: ignore[misc, valid-type]
            def __init__(self, cmd, **kw):
                seen["cmd"] = cmd
                super().__init__(cmd, **kw)

        monkeypatch.setattr(mod.subprocess, "Popen", _Capture)
        src = tmp_path / "a.odt"
        src.write_bytes(b"PK")
        mod.DocConvertService().convert(src, "pdf", tmp_path / "out")
        assert any(str(a).startswith("-env:UserInstallation=") for a in seen["cmd"])

    def test_missing_libreoffice_is_a_clear_error(self, monkeypatch, tmp_path):
        mod = self._stub(monkeypatch, tmp_path, present=False)
        src = tmp_path / "a.docx"
        src.write_bytes(b"PK")
        with pytest.raises(DocConvertToolMissingError):
            mod.DocConvertService().convert(src, "pdf", tmp_path / "out")

    def test_non_zero_exit_is_reported(self, monkeypatch, tmp_path):
        mod = self._stub(monkeypatch, tmp_path, present=True, produce=False, rc=1)
        src = tmp_path / "a.docx"
        src.write_bytes(b"PK")
        with pytest.raises(DocConvertError, match="LibreOffice failed"):
            mod.DocConvertService().convert(src, "pdf", tmp_path / "out")

    def test_silent_no_output_is_reported(self, monkeypatch, tmp_path):
        mod = self._stub(monkeypatch, tmp_path, present=True, produce=False, rc=0)
        src = tmp_path / "a.docx"
        src.write_bytes(b"PK")
        with pytest.raises(DocConvertError, match="produced no output"):
            mod.DocConvertService().convert(src, "pdf", tmp_path / "out")

    def test_capabilities_follow_the_locator(self, monkeypatch, tmp_path):
        import app.services.doc_convert_service as mod

        monkeypatch.setattr(mod, "locate_soffice", lambda: None)
        caps = mod.capabilities()
        assert caps.libreoffice is False
        assert caps.routes["office->pdf"] is False
        assert caps.routes["md->pdf"] is True

        monkeypatch.setattr(mod, "locate_soffice", lambda: tmp_path / "soffice")
        caps = mod.capabilities()
        assert caps.libreoffice is True
        assert caps.routes["office->pdf"] is True


class TestAuditFixes:
    """Regression guards for the issues found auditing the document feature."""

    def test_percent_encoded_traversal_is_blocked(self, tmp_path):
        """%2e%2e decodes to '..' — the guard must see that and refuse."""
        allowed = tmp_path / "docs"
        allowed.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        fetcher = _make_url_fetcher(allowed)
        with pytest.raises(DocConvertError):
            fetcher(f"file://{allowed.as_posix()}/%2e%2e/secret.txt")

    def test_the_guard_decodes_exactly_once(self, tmp_path):
        """url2pathname() already unquotes; decoding again would turn a literal
        '%2e%2e' directory name into '..' and diverge from what the opener
        actually reads. %252e%252e must resolve to the literal folder."""
        allowed = tmp_path / "docs"
        literal = allowed / "%2e%2e"
        literal.mkdir(parents=True)
        inside = literal / "ok.css"
        inside.write_text("b{}", encoding="utf-8")
        fetcher = _make_url_fetcher(allowed)
        response = fetcher(f"file://{allowed.as_posix()}/%252e%252e/ok.css")
        try:
            assert response.read() == b"b{}"
        finally:
            response.close()
        assert fetcher.blocked == []

    def test_query_string_cannot_smuggle_a_path(self, tmp_path):
        allowed = tmp_path / "docs"
        allowed.mkdir()
        inside = allowed / "ok.css"
        inside.write_text("a{}", encoding="utf-8")
        fetcher = _make_url_fetcher(allowed)
        response = fetcher(inside.as_uri() + "?../../etc/passwd")
        try:
            assert response.read() == b"a{}"
        finally:
            response.close()

    def test_convert_does_not_probe_unrelated_backends(self, svc, tmp_path, monkeypatch):
        """A pdf->md job must not import WeasyPrint — that import costs seconds."""
        probed: list[str] = []
        real = mod._have_module
        monkeypatch.setattr(mod, "_have_module", lambda n: probed.append(n) or real(n))

        src = tmp_path / "a.html"
        src.write_text("<h1>hello</h1>", encoding="utf-8")
        pdf = mod.DocConvertService().convert(src, "pdf", tmp_path / "p")
        probed.clear()
        svc.convert(pdf, "md", tmp_path / "out")
        assert probed == ["pypdf"]

    def test_html_to_pdf_honours_the_documents_own_meta_charset(self, svc, tmp_path):
        """The file is handed to WeasyPrint so it sniffs the charset itself."""
        src = tmp_path / "sjis.html"
        src.write_bytes(
            '<html><head><meta charset="shift_jis"></head><body><p>日本語</p></body></html>'.encode(
                "shift_jis"
            )
        )
        out = svc.convert(src, "pdf", tmp_path / "out")
        assert out.read_bytes().startswith(b"%PDF")

    def test_require_backend_names_the_missing_piece(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "locate_soffice", lambda: None)
        src = tmp_path / "a.docx"
        src.write_bytes(b"PK")
        with pytest.raises(DocConvertToolMissingError, match="libreoffice"):
            mod.DocConvertService().convert(src, "pdf", tmp_path / "out")
