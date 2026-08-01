"""
tests/test_api_static_xss.py
Regression tests for H1 (stored XSS via video title) in api/static/index.html.

No TestClient/API test module exists for this pure-frontend file, so these
are file-content checks: assert the vulnerable JS-string-interpolation
pattern is gone from inline onclick handlers, and that escHtml() escapes
quote/backtick characters used to break out of single-quoted JS strings.
"""

from __future__ import annotations

import re
from pathlib import Path

_INDEX_HTML = Path(__file__).resolve().parent.parent / "api" / "static" / "index.html"


def _read_index_html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


class TestNoJsStringInterpolationInOnclick:
    def test_no_esc_html_value_inside_js_string_literal(self):
        """Regression: onclick="fn('${escHtml(t.title)}')" put attacker-scraped
        metadata inside a single-quoted JS string literal — a title containing
        "');someJs;//" closed the string and executed. escHtml() output must
        never be interpolated directly inside a JS string literal; it must
        only be used to build data-* attribute values, read back via
        this.dataset in the handler."""
        html = _read_index_html()
        assert not re.search(r"'\$\{escHtml\(", html)

    def test_preview_and_convert_buttons_use_dataset_pattern(self):
        html = _read_index_html()
        assert 'onclick="previewFile(this.dataset.tid, this.dataset.title)"' in html
        assert 'onclick="previewConvertFile(this.dataset.jid, this.dataset.title)"' in html
        assert 'onclick="openConvertSheet(this.dataset.tid, this.dataset.title)"' in html


class TestEscHtmlHardening:
    def test_esc_html_escapes_single_quote_and_backtick(self):
        html = _read_index_html()
        m = re.search(r"function escHtml\(s\)\s*\{(.*?)\n\}", html, re.DOTALL)
        assert m, "escHtml() not found in index.html"
        body = m.group(1)
        assert "&#39;" in body
        assert "&#96;" in body
