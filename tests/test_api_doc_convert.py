"""
tests/test_api_doc_convert.py
Route-layer tests for /api/docs/* in api/server.py.

Endpoint functions are invoked directly (the convention used by
test_api_archive.py / test_api_sse_cleanup.py) — this repo has no httpx
dependency, so there is no TestClient. Auth wiring is verified structurally.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import api.server as srv
from api.models import DocConvertRequest

MD_SAMPLE = "# Tiêu đề\n\nNội dung **đậm**.\n"


def _make_app(download_dir: Path):
    service = SimpleNamespace(get_all_tasks=lambda: [], analyse_url=lambda *a, **k: None)
    config = SimpleNamespace(api_token="", download_dir=download_dir, taildrop_target_nodes=[])
    return srv.create_app(service, config)  # type: ignore[arg-type]


def _endpoint(app, path: str):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route {path} not found")


def _status_of(coro) -> tuple[int, str]:
    async def _run():
        with pytest.raises(Exception) as info:
            await coro
        return info.value.status_code, str(info.value.detail)

    return asyncio.run(_run())


@pytest.fixture
def dl_dir(tmp_path: Path) -> Path:
    d = tmp_path / "downloads"
    d.mkdir()
    return d


@pytest.fixture
def app(dl_dir: Path):
    return _make_app(dl_dir)


class TestAuthWiring:
    @pytest.mark.parametrize("path", ["/api/docs/capabilities", "/api/docs/convert"])
    def test_every_route_requires_auth(self, app, path):
        endpoint = _endpoint(app, path)
        dep = inspect.signature(endpoint).parameters["_"].default
        assert dep.dependency.__name__ == "_require_auth"


class TestRequestModel:
    @pytest.mark.parametrize("fmt", ["pdf", "html", "md", "docx", "PDF", " .pdf "])
    def test_accepted_targets(self, fmt):
        assert DocConvertRequest(source_path="/x/a.md", target_format=fmt).target_format in (
            "pdf",
            "html",
            "md",
            "docx",
        )

    @pytest.mark.parametrize("fmt", ["epub", "exe", "", "../pdf"])
    def test_rejected_targets(self, fmt):
        with pytest.raises(ValidationError):
            DocConvertRequest(source_path="/x/a.md", target_format=fmt)


class TestCapabilities:
    def test_reports_backends_and_routes(self, app):
        resp = asyncio.run(_endpoint(app, "/api/docs/capabilities")(None))
        assert resp.markdown is True
        assert resp.weasyprint is True
        assert resp.pypdf is True
        assert "md->pdf" in resp.routes
        assert ".docx" in resp.source_extensions
        assert resp.target_formats == ["pdf", "html", "md", "docx"]


class TestConvert:
    def test_markdown_to_pdf_lands_next_to_the_source(self, app, dl_dir):
        src = dl_dir / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        resp = asyncio.run(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="pdf"), None
            )
        )
        assert resp.filename == "note.pdf"
        out = Path(resp.output_path)
        assert out.parent == dl_dir
        assert out.read_bytes().startswith(b"%PDF")
        assert resp.size == out.stat().st_size

    def test_out_dir_is_honoured(self, app, dl_dir):
        src = dl_dir / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        target_dir = dl_dir / "converted"
        resp = asyncio.run(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="html", out_dir=str(target_dir)),
                None,
            )
        )
        assert Path(resp.output_path).parent == target_dir

    def test_source_outside_download_dir_is_400(self, app, tmp_path):
        outside = tmp_path / "outside.md"
        outside.write_text(MD_SAMPLE, encoding="utf-8")
        status, detail = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(outside), target_format="pdf"), None
            )
        )
        assert status == 400
        assert "outside" in detail

    def test_traversal_in_source_path_is_400(self, app, dl_dir):
        status, _ = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(dl_dir / ".." / "etc" / "passwd"), target_format="pdf"),
                None,
            )
        )
        assert status == 400

    def test_out_dir_outside_download_dir_is_400(self, app, dl_dir, tmp_path):
        src = dl_dir / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        status, _ = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(
                    source_path=str(src), target_format="pdf", out_dir=str(tmp_path / "elsewhere")
                ),
                None,
            )
        )
        assert status == 400

    def test_missing_source_is_404(self, app, dl_dir):
        status, _ = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(dl_dir / "nope.md"), target_format="pdf"), None
            )
        )
        assert status == 404

    def test_directory_source_is_400(self, app, dl_dir):
        sub = dl_dir / "folder"
        sub.mkdir()
        status, detail = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(sub), target_format="pdf"), None
            )
        )
        assert status == 400
        assert "must be a file" in detail

    def test_unsupported_route_is_422(self, app, dl_dir):
        src = dl_dir / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        status, _ = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="docx"), None
            )
        )
        assert status == 422

    def test_unsupported_source_type_is_422(self, app, dl_dir):
        src = dl_dir / "clip.mp4"
        src.write_bytes(b"\x00\x00")
        status, _ = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="pdf"), None
            )
        )
        assert status == 422

    def test_missing_libreoffice_is_503(self, app, dl_dir, monkeypatch):
        import app.services.doc_convert_service as mod

        monkeypatch.setattr(mod, "locate_soffice", lambda: None)
        src = dl_dir / "report.docx"
        src.write_bytes(b"PK\x03\x04")
        status, detail = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="pdf"), None
            )
        )
        assert status == 503
        assert "not installed" in detail.lower()

    def test_oversized_source_is_413(self, app, dl_dir, monkeypatch):
        monkeypatch.setattr(srv, "_DOC_CONVERT_MAX_BYTES", 10)
        src = dl_dir / "big.md"
        src.write_text("x" * 50, encoding="utf-8")
        status, detail = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="pdf"), None
            )
        )
        assert status == 413
        assert "limit" in detail

    def test_repeated_conversion_never_overwrites(self, app, dl_dir):
        src = dl_dir / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        ep = _endpoint(app, "/api/docs/convert")
        first = asyncio.run(ep(DocConvertRequest(source_path=str(src), target_format="pdf"), None))
        second = asyncio.run(ep(DocConvertRequest(source_path=str(src), target_format="pdf"), None))
        assert first.output_path != second.output_path
        assert Path(first.output_path).is_file()
        assert Path(second.output_path).is_file()

    def test_output_never_escapes_download_dir(self, app, dl_dir):
        """A source deep inside download_dir still writes inside download_dir."""
        deep = dl_dir / "a" / "b"
        deep.mkdir(parents=True)
        src = deep / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        resp = asyncio.run(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="pdf"), None
            )
        )
        assert Path(resp.output_path).resolve().is_relative_to(dl_dir.resolve())


class TestAuditFixes:
    """Regression guards for the issues found auditing the document feature."""

    def test_out_dir_that_is_a_file_is_400_not_500(self, app, dl_dir):
        """mkdir() on an existing file raises FileExistsError; that is a client error."""
        src = dl_dir / "note.md"
        src.write_text(MD_SAMPLE, encoding="utf-8")
        blocker = dl_dir / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        status, detail = _status_of(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(
                    source_path=str(src), target_format="pdf", out_dir=str(blocker)
                ),
                None,
            )
        )
        assert status == 400
        assert "not a directory" in detail

    @pytest.mark.parametrize(
        "name", ["page.html", "page.htm", "doc.xhtml", "logo.svg", "feed.xml", "a.mhtml"]
    )
    def test_active_content_is_served_as_an_attachment(self, app, dl_dir, name):
        """Stored-XSS guard: HTML/SVG/XML must not render in the API's own origin."""
        f = dl_dir / name
        f.write_text("<script>alert(1)</script>", encoding="utf-8")
        resp = asyncio.run(_endpoint(app, "/api/files/serve")(str(f), None))
        assert resp.media_type == "application/octet-stream"
        disposition = resp.headers["content-disposition"]
        assert disposition.startswith("attachment")

    def test_media_is_still_served_inline(self, app, dl_dir):
        """The XSS guard must not break the Files-tab video/image preview."""
        f = dl_dir / "clip.mp4"
        f.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        resp = asyncio.run(_endpoint(app, "/api/files/serve")(str(f), None))
        assert resp.media_type == "video/mp4"
        assert resp.headers["content-disposition"].startswith("inline")

    def test_converted_html_cannot_be_rendered_by_the_browser(self, app, dl_dir):
        """End-to-end: a <script> in a .md survives into .html but is never rendered."""
        src = dl_dir / "evil.md"
        src.write_text("# hi\n\n<script>alert(1)</script>\n", encoding="utf-8")
        res = asyncio.run(
            _endpoint(app, "/api/docs/convert")(
                DocConvertRequest(source_path=str(src), target_format="html"), None
            )
        )
        served = asyncio.run(_endpoint(app, "/api/files/serve")(res.output_path, None))
        assert served.media_type == "application/octet-stream"
        assert served.headers["content-disposition"].startswith("attachment")
