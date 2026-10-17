"""
tests/test_web_ui_subtitle_vmaf.py

Regression tests for the missing subtitle / VMAF controls in the web UI.

The API has accepted generate_subtitles, subtitle_language and compute_vmaf
since the FFMPEG-901 work, and ConvertJobResponse has reported
subtitle_filename, subtitle_error and vmaf_score back — but api/static/index.html
never sent the flags and never showed the results, so the whole feature was
unreachable from a phone.  Each test below pins one half of that wiring:

  W1  the convert sheet has a subtitle checkbox + language <select>
  W2  the convert sheet has a VMAF checkbox
  W3  both POST bodies carry all three flags
  W4  both open*() helpers clear the checkboxes (no leak between jobs)
  W5  the subtitle block is driven by GET /api/convert/codecs capabilities
  W6  finished jobs show the VMAF score and a .srt download on both surfaces
  W7  the .srt download uses ?kind=srt and carries the auth token

These are static assertions against index.html, matching the U1/U2 tests in
tests/test_convert_audit_fixes.py — the page has no build step, so its source
is what ships.
"""

from __future__ import annotations

import re
from pathlib import Path

_INDEX_HTML = Path(__file__).resolve().parent.parent / "api" / "static" / "index.html"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _fn_body(html: str, name: str) -> str:
    """Return the source of a top-level JS function by brace matching."""
    m = re.search(rf"^(?:async )?function {re.escape(name)}\s*\(", html, re.M)
    assert m, f"{name}() not found in index.html"
    start = html.index("{", m.end() - 1)
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start : i + 1]
    raise AssertionError(f"unbalanced braces in {name}()")


# ── W1 / W2: the controls exist ───────────────────────────────────────────────


class TestControlsPresent:
    def test_w1_subtitle_toggle_and_language_select_exist(self):
        html = _html()
        assert 'id="cvsubs-toggle"' in html, "subtitle checkbox missing from convert sheet"
        assert 'id="cvsubs-lang"' in html, "subtitle language select missing from convert sheet"

    def test_w1_language_select_starts_disabled(self):
        # Picking a language is meaningless until subtitles are switched on.
        html = _html()
        m = re.search(r'<select id="cvsubs-lang"([^>]*)>', html)
        assert m, "cvsubs-lang select not found"
        assert "disabled" in m.group(1)

    def test_w1_capabilities_enable_the_language_select(self):
        # The selects are gated by whisper support rather than by the checkbox,
        # because the standalone "Chỉ tạo phụ đề" button reads them too.
        body = _fn_body(_html(), "applySubtitleCaps")
        assert "select.disabled = !supported;" in body
        assert "modelSelect.disabled = !supported;" in body

    def test_w2_vmaf_toggle_exists(self):
        assert 'id="cvvmaf-toggle"' in _html(), "VMAF checkbox missing from convert sheet"


# ── W3: the flags actually reach the server ───────────────────────────────────


class TestSubmitSendsFlags:
    def test_w3_all_convert_paths_send_all_three_flags(self):
        body = _fn_body(_html(), "submitConvert")
        # One occurrence each for /api/files/convert/batch, /api/files/convert
        # and /api/queue/{id}/convert.
        for field in ("generate_subtitles:", "subtitle_language:", "compute_vmaf:"):
            assert body.count(field) == 3, f"{field} sent on {body.count(field)}/3 convert paths"

    def test_w3_flags_read_from_the_controls(self):
        body = _fn_body(_html(), "submitConvert")
        assert "cvsubs-toggle" in body
        assert "cvsubs-lang" in body
        assert "cvvmaf-toggle" in body

    def test_w3_disabled_subtitle_toggle_is_never_sent_as_true(self):
        # A checked-then-disabled box would otherwise post a flag the server 422s.
        body = _fn_body(_html(), "submitConvert")
        assert re.search(r"checked\s*&&\s*!\s*\w+\.disabled", body), (
            "genSubs must also check that the toggle is enabled"
        )

    def test_w3_flags_read_before_the_sheet_closes(self):
        # closeConvertSheet() tears down pending state; reading after it would
        # repeat the _pendingFilePath bug (U2) for the new fields.
        body = _fn_body(_html(), "submitConvert")
        # Match the call, not the comment above it that also names the function.
        assert body.index("cvvmaf-toggle") < body.index("closeConvertSheet();")


# ── W4: no state leak between two conversions ─────────────────────────────────


class TestResetBetweenJobs:
    def test_w4_reset_helper_clears_every_control(self):
        body = _fn_body(_html(), "_resetConvertExtras")
        assert "cvsubs-toggle" in body and "checked = false" in body
        assert "cvvmaf-toggle" in body
        assert "cvsubs-lang" in body and "'auto'" in body

    def test_w4_both_open_helpers_call_the_reset(self):
        html = _html()
        for fn in ("openConvertSheet", "openFileConvertSheet"):
            assert "_resetConvertExtras()" in _fn_body(html, fn), f"{fn}() does not reset the extras"


# ── W5: subtitle block follows the server's real capabilities ─────────────────


class TestCapabilityGating:
    def test_w5_caps_are_applied_after_fetching_codecs(self):
        body = _fn_body(_html(), "populateConvertOptions")
        assert "/api/convert/codecs" in body
        assert "applySubtitleCaps(caps)" in body

    def test_w5_toggle_is_disabled_when_whisper_is_unavailable(self):
        body = _fn_body(_html(), "applySubtitleCaps")
        assert "caps.subtitles" in body
        assert "toggle.disabled" in body
        assert "hint" in body, "no explanation shown when subtitles are unsupported"

    def test_w5_languages_come_from_the_server(self):
        body = _fn_body(_html(), "applySubtitleCaps")
        assert "caps.subtitle_languages" in body
        # Unknown codes must survive as their raw code rather than vanish.
        assert "SUBTITLE_LANG_LABELS[code] || code" in body

    def test_w5_auto_is_the_default_selection(self):
        body = _fn_body(_html(), "applySubtitleCaps")
        assert "select.value = 'auto'" in body

    def test_w5_language_options_are_escaped(self):
        body = _fn_body(_html(), "applySubtitleCaps")
        assert body.count("escHtml(") >= 2, "language options interpolated without escHtml()"


# ── W6 / W7: results are visible once the job finishes ───────────────────────


class TestResultsRendered:
    def test_w6_extras_renderer_shows_score_subtitle_and_error(self):
        body = _fn_body(_html(), "convertExtrasHtml")
        assert "vmaf_score" in body
        assert "subtitle_filename" in body
        assert "subtitle_error" in body, "a failed subtitle pass must still be reported"

    def test_w6_score_is_rendered_on_both_surfaces(self):
        html = _html()
        # Both cards call the shared renderer for a normal conversion and again
        # for a subtitles-only job, which no longer reuses the conversion block.
        assert html.count("convertExtrasHtml(") == 5, (
            "expected 1 definition + 4 call sites "
            "(queue card + file browser, each for convert and subtitles-only)"
        )

    def test_w6_subtitle_error_is_escaped(self):
        body = _fn_body(_html(), "convertExtrasHtml")
        assert "escHtml(job.subtitle_error)" in body

    def test_w7_srt_download_uses_the_kind_parameter(self):
        body = _fn_body(_html(), "downloadSubtitle")
        assert "kind=srt" in body

    def test_w7_srt_download_carries_the_auth_token(self):
        # /api/convert/{id}/file is behind _require_auth_stream, which reads the
        # token from the query string.
        body = _fn_body(_html(), "downloadSubtitle")
        assert "cfg.token" in body and "encodeURIComponent" in body


# ── Guard against undefined CSS variables in the new markup ──────────────────


class TestStyling:
    def test_new_colors_use_defined_css_variables(self):
        # Scoped to the markup this change added.  A page-wide sweep also trips
        # on a pre-existing var(--primary) at index.html:1725, which is out of
        # scope here.
        html = _html()
        declared = set(re.findall(r"^\s*(--[\w-]+):", html, re.M))
        new_css = "".join(
            html[html.index(m) : html.index(m) + 700] for m in (".cv-check-row {", ".cv-hint {")
        ) + _fn_body(html, "convertExtrasHtml")
        used = set(re.findall(r"var\((--[\w-]+)\)", new_css))
        assert used, "no CSS variables found in the new markup - locator is stale"
        assert used <= declared, f"undefined CSS variables: {sorted(used - declared)}"
