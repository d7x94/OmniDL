"""
tests/test_interface_audit_2026.py
Regression tests for the desktop + Web-API interface audit (26 Aug 2026).

FIX-IA-1  api/server.py — _wire_event_bus() subscribed _on_removed to
          EventBus.DOWNLOAD_REMOVED but never recorded it in
          _bus_subscriptions, so _unwire_bus() could not detach it.  Every
          Settings -> Remote API restart (token rotate, port change, HTTPS
          toggle) left one more live handler behind, and the browser received
          one duplicate "removed" SSE frame per restart.
FIX-IA-2  index.html — doDownload() reused analysedInfo even after the user
          replaced the URL in the box, attaching the previous URL's
          title / platform / is_live / tiktok_room_id to the new job.  For
          Kuaishou it went further: dlUrl swapped in the *previous* video's
          CDN URL, so the wrong video was downloaded.
FIX-IA-3  index.html — the analyse flow and the two preview helpers printed
          hard-coded English while every other string went through T().
FIX-IA-4  index.html — toast() accepted one argument but 8 call sites passed
          ('error') as a second, so failures were indistinguishable from
          successes and long server messages were ellipsised to one line.
FIX-IA-5  index.html — the monitor "paused" tag rendered T('mon.pause'), the
          *button* label ("Pause"), as a *status* label.
FIX-IA-6  index.html — the select-mode checkbox used accent-color:
          var(--primary), a variable this stylesheet never defines, so it fell
          back to the browser default instead of the app accent.
FIX-IA-7  index.html — #settings-overlay carried onclick="e => {...}", which
          only evaluates an arrow-function expression and throws it away; the
          working backdrop handler is the addEventListener() at the bottom.
FIX-IA-8  download_item_widget.py — refresh() used `elif not terminal`, so a
          COMPLETED task whose file was deleted elsewhere (the Remote API
          clears task.filename) kept Open / Preview / Send / Convert / Rename
          visible on a dead path.
FIX-IA-9  status_bar.py / main_window.py — MainWindow.update_tab_badge() had
          no caller at all, so the Queue pill badge never appeared even though
          both QSS themes style QLabel#badge.
FIX-IA-10 api/models.py — MonitorIntervalRequest.interval was unbounded above.
          set_check_interval() only clamps the lower end, so a raw API call
          could answer 200 OK while switching live monitoring off for years.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_INDEX_HTML = Path(__file__).resolve().parent.parent / "api" / "static" / "index.html"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


# ── FIX-IA-1 ──────────────────────────────────────────────────────────────────


class TestEventBusUnsubscribe:
    def test_unwire_detaches_every_subscription(self):
        import api.server as srv
        from app.event_bus import EventBus

        bus = EventBus()
        srv._wire_event_bus(bus)
        assert bus._handlers[EventBus.DOWNLOAD_REMOVED], "removed handler not attached"

        srv._unwire_bus()
        leftover = {ev: len(hs) for ev, hs in bus._handlers.items() if hs}
        assert leftover == {}, f"handlers survived _unwire_bus(): {leftover}"

    def test_restart_cycles_do_not_accumulate_handlers(self):
        """Three stop/start cycles used to leave three DOWNLOAD_REMOVED handlers,
        so one queue-clear broadcast three identical SSE frames."""
        import api.server as srv
        from app.event_bus import EventBus

        bus = EventBus()
        for _ in range(3):
            srv._wire_event_bus(bus)
            srv._unwire_bus()

        assert not any(bus._handlers.values())

    def test_removed_event_broadcasts_exactly_once_after_a_restart(self):
        import api.server as srv
        from app.event_bus import EventBus

        frames: list[tuple[str, dict]] = []
        original = srv._broadcast
        srv._broadcast = lambda ev, data: frames.append((ev, data))  # type: ignore[assignment]
        try:
            bus = EventBus()
            srv._wire_event_bus(bus)
            srv._unwire_bus()
            srv._wire_event_bus(bus)  # the restart
            bus.publish(EventBus.DOWNLOAD_REMOVED, ids=["a", "b"])
            srv._unwire_bus()
        finally:
            srv._broadcast = original  # type: ignore[assignment]

        removed = [f for f in frames if f[0] == "removed"]
        assert len(removed) == 1, f"expected one 'removed' frame, got {len(removed)}"
        assert removed[0][1] == {"ids": ["a", "b"]}


# ── FIX-IA-2 ──────────────────────────────────────────────────────────────────


class TestAnalysedInfoIsUrlScoped:
    def test_analysed_url_is_tracked(self):
        html = _html()
        assert "let analysedUrl" in html
        assert "analysedUrl  = url;" in html, "analyse success must record the URL it belongs to"

    def test_do_download_ignores_metadata_from_a_different_url(self):
        html = _html()
        assert "const info = (analysedUrl && analysedUrl === url) ? analysedInfo : null;" in html

    def test_do_download_body_no_longer_reads_analysed_info_directly(self):
        """Every field in the POST /api/download body must come from the
        url-scoped `info`, never from the raw analysedInfo global.

        The body literal lives in buildDownloadBody(), shared with the Batch
        tab; `info` reaches it as a parameter, so the global is out of reach.
        """
        html = _html()
        body = re.search(
            r"function buildDownloadBody\(url, info, formatId, outputExt\) \{(.*?)\n\}",
            html,
            re.DOTALL,
        )
        assert body, "buildDownloadBody() not found"
        assert "analysedInfo" not in body.group(1)
        assert "const info = (analysedUrl && analysedUrl === url) ? analysedInfo : null;" in html

    def test_editing_the_url_clears_the_stale_metadata_card(self):
        html = _html()
        assert "url-input').addEventListener('input'" in html


# ── FIX-IA-3 ──────────────────────────────────────────────────────────────────


def _catalogues() -> dict[str, list[str]]:
    """{lang: [key, ...]} parsed out of the I18N object literal."""
    html = _html()
    block = html[html.index("const I18N = {") : html.index("\nlet uiLang")]
    marks = [(m.group(1), m.start()) for m in re.finditer(r"^  (\w+): \{$", block, re.M)]
    marks.append(("__end__", len(block)))
    return {
        name: re.findall(r"^    '([^']+)':", block[start:end], re.M)
        for (name, start), (_, end) in zip(marks, marks[1:])
    }


class TestWebI18nCatalogue:
    def test_every_language_has_the_same_keys(self):
        cat = _catalogues()
        assert set(cat) == {"vi", "en", "zh"}
        base = set(cat["vi"])
        for lang, keys in cat.items():
            assert set(keys) == base, f"{lang} key set diverged"

    def test_no_duplicate_keys(self):
        for lang, keys in _catalogues().items():
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            assert not dupes, f"{lang} defines {dupes} twice"

    @pytest.mark.parametrize(
        "key",
        [
            "dl.analysing",
            "dl.reconnecting",
            "dl.ready",
            "dl.playlist_detected",
            "dl.analyse_cancelled",
            "dl.analyse_timeout",
            "dl.analyse_failed",
            "dl.analyse_bad_response",
            "dl.load_failed",
            "preview.no_audio",
            "preview.title_default",
            "mon.paused_tag",
        ],
    )
    def test_new_audit_keys_exist_in_all_languages(self, key):
        for lang, keys in _catalogues().items():
            assert key in keys, f"{key} missing from {lang}"

    def test_every_referenced_key_exists(self):
        html = _html()
        script = html[html.index("\nlet uiLang") :]
        used = set(re.findall(r"T\(\s*'([^']+)'", script))
        used |= set(re.findall(r'data-i18n(?:-html|-ph)?="([^"]+)"', html))
        missing = sorted(used - set(_catalogues()["vi"]))
        assert not missing, f"T()/data-i18n keys with no catalogue entry: {missing}"


class TestAnalyseFlowIsTranslated:
    @pytest.mark.parametrize(
        "english",
        [
            "Analysing…",
            "Reconnecting…",
            "Analysis timed out",
            "Analysis failed",
            "Invalid response from server",
            "Analysis cancelled",
            "Ready to download",
            "Playlist detected",
            "Load failed",
            "Your browser does not support video playback.",
            "Your browser does not support audio playback.",
        ],
    )
    def test_hardcoded_english_is_gone(self, english):
        """Scoped to the script body: these phrases are legitimate *values* in
        the `en` catalogue — what must be gone is the literal in the code."""
        html = _html()
        script = html[html.index("\nlet uiLang") :]
        assert english not in script

    def test_preview_helpers_use_the_catalogue(self):
        html = _html()
        # One per <video>/<audio> fallback across previewFile,
        # previewFileByPath and previewConvertFile.
        assert html.count("T('preview.no_video')") == 3
        assert html.count("T('preview.no_audio')") == 2
        assert html.count("T('preview.title_default')") == 2

    def test_no_undefined_css_variables(self):
        html = _html()
        defined = set(re.findall(r"^\s*(--[a-z0-9-]+):", html, re.M))
        undefined = {
            m.group(1).strip()
            for m in re.finditer(r"var\((\s*--[a-z0-9-]+)\s*\)", html)
            if m.group(1).strip() not in defined
        }
        assert not undefined, f"var() references with no definition and no fallback: {undefined}"


# ── FIX-IA-4 / 5 / 6 / 7 ──────────────────────────────────────────────────────


class TestWebUiWidgets:
    def test_toast_accepts_the_kind_argument_its_callers_pass(self):
        html = _html()
        assert "function toast(msg, kind)" in html
        assert "classList.toggle('error', kind === 'error')" in html
        assert "#toast.error {" in html

    def test_monitor_paused_tag_is_a_status_not_a_button_label(self):
        html = _html()
        tag = re.search(r"const pausedTag = paused\n\s*\?[^\n]*", html)
        assert tag, "pausedTag not found"
        assert "mon.paused_tag" in tag.group(0)
        assert "T('mon.pause')" not in tag.group(0)

    def test_select_checkbox_uses_a_defined_accent_variable(self):
        html = _html()
        assert "accent-color:var(--primary)" not in html
        assert "accent-color:var(--accent)" in html

    def test_settings_overlay_has_no_dead_inline_handler(self):
        html = _html()
        assert 'onclick="e => {' not in html
        # The working handler is still registered.
        assert "document.getElementById('settings-overlay').addEventListener('click'" in html


# ── FIX-IA-8 ──────────────────────────────────────────────────────────────────


class TestDownloadItemFileActions:
    """Unbound-method pattern (see tests/test_queue_open_folder_fix.py): no
    QApplication, no display server."""

    _STUBBED = (
        "_title_lbl",
        "_status_badge",
        "_type_dot",
        "_prog",
        "_speed_lbl",
        "_eta_lbl",
        "_elapsed_lbl",
        "_size_lbl",
        "_url_lbl",
        "_err_lbl",
        "_live_badge",
        "_pause_btn",
        "_cancel_btn",
        "_folder_btn",
        "_preview_btn",
        "_convert_btn",
        "_edit_btn",
        "_rename_btn",
        "_send_btn",
    )

    def _widget(self, task):
        from ui.components.download_item_widget import DownloadItemWidget

        widget = DownloadItemWidget.__new__(DownloadItemWidget)
        widget.task = task
        widget._completed_path = ""
        widget._converting = False
        widget._on_convert = lambda _p, **_kw: None
        widget._on_edit = lambda _p: None
        widget._on_rename = lambda *_a: None
        widget._on_send = lambda *_a, **_kw: None
        for attr in self._STUBBED:
            setattr(widget, attr, MagicMock())
        widget.setStyleSheet = MagicMock()
        return widget

    def _task(self, tmp_path, filename=""):
        from domain.models.download_task import DownloadTask, MediaInfo

        task = DownloadTask(url="https://x/1", media_info=MediaInfo(url="https://x/1", title="clip"))
        task.filename = filename
        task.output_dir = str(tmp_path)
        return task

    def test_file_actions_hide_when_the_file_is_deleted_elsewhere(self, tmp_path):
        from domain.enums.download_status import DownloadStatus

        media = tmp_path / "clip.mp4"
        media.write_bytes(b"x")
        task = self._task(tmp_path, filename=str(media))
        task.status = DownloadStatus.COMPLETED
        widget = self._widget(task)

        widget.refresh(task)
        for name in ("_folder_btn", "_preview_btn", "_convert_btn", "_edit_btn", "_rename_btn", "_send_btn"):
            getattr(widget, name).show.assert_called()

        # DELETE /api/queue/{id}/file (or /api/files/delete) clears filename.
        for name in self._STUBBED:
            getattr(widget, name).reset_mock()
        task.filename = ""
        widget.refresh(task)

        for name in ("_folder_btn", "_preview_btn", "_convert_btn", "_edit_btn", "_rename_btn", "_send_btn"):
            btn = getattr(widget, name)
            btn.hide.assert_called()
            btn.show.assert_not_called()
        assert widget._completed_path == ""

    def test_failed_task_never_shows_file_actions(self, tmp_path):
        from domain.enums.download_status import DownloadStatus

        task = self._task(tmp_path)
        task.status = DownloadStatus.FAILED
        task.error_msg = "boom"
        widget = self._widget(task)
        widget.refresh(task)

        for name in ("_folder_btn", "_preview_btn", "_convert_btn", "_edit_btn", "_rename_btn", "_send_btn"):
            btn = getattr(widget, name)
            btn.hide.assert_called()
            btn.show.assert_not_called()


# ── FIX-IA-9 ──────────────────────────────────────────────────────────────────


class TestQueuePillBadge:
    """The badge machinery (MainWindow._pill_badges + QLabel#badge in both QSS
    themes) already existed; nothing ever fed it.  A full MainWindow is not
    built here on purpose — Qt aborts the shared pytest process once other Qt
    tests have run — so the wiring is verified through StatusBar with a stub
    owner instead."""

    def test_status_bar_drives_the_queue_badge(self):
        source = (Path(__file__).resolve().parent.parent / "ui" / "components" / "status_bar.py").read_text(
            encoding="utf-8"
        )
        assert 'update_tab_badge("queue", active)' in source

    def test_update_status_forwards_the_active_count(self):
        from ui.components.status_bar import StatusBar

        calls: list[tuple[str, int]] = []

        bar = StatusBar.__new__(StatusBar)
        bar._last_status = None
        bar._app = SimpleNamespace(update_tab_badge=lambda key, count: calls.append((key, count)))
        for attr in ("_dot", "_active_chip", "_progress_bar", "_speed_chip", "_eta_label"):
            setattr(bar, attr, MagicMock())

        StatusBar.update_status(bar, 3, 0.0, 0.0, -1)
        StatusBar.update_status(bar, 0, 0.0, 0.0, -1)

        assert calls == [("queue", 3), ("queue", 0)]

    def test_identical_polls_do_not_re_emit_the_badge(self):
        """_poll runs ~1.25x/s forever; the badge must ride the existing
        no-change short-circuit rather than repainting every tick."""
        from ui.components.status_bar import StatusBar

        calls: list[tuple[str, int]] = []
        bar = StatusBar.__new__(StatusBar)
        bar._last_status = None
        bar._app = SimpleNamespace(update_tab_badge=lambda key, count: calls.append((key, count)))
        for attr in ("_dot", "_active_chip", "_progress_bar", "_speed_chip", "_eta_label"):
            setattr(bar, attr, MagicMock())

        for _ in range(5):
            StatusBar.update_status(bar, 2, 0.0, 0.0, -1)

        assert calls == [("queue", 2)]

    def test_main_window_badge_is_styled_by_both_themes(self):
        themes = Path(__file__).resolve().parent.parent / "ui" / "themes"
        for qss in ("dark.qss", "light.qss"):
            assert "QLabel#badge {" in (themes / qss).read_text(encoding="utf-8")


# ── FIX-IA-10 ─────────────────────────────────────────────────────────────────


class TestMonitorIntervalBounds:
    @pytest.mark.parametrize("value", [60, 180, 300, 600])
    def test_ui_offered_values_are_accepted(self, value):
        from api.models import MonitorIntervalRequest

        assert MonitorIntervalRequest(interval=value).interval == value

    @pytest.mark.parametrize("value", [-5, 0, 59, 601, 10**9])
    def test_out_of_range_is_rejected(self, value):
        from pydantic import ValidationError

        from api.models import MonitorIntervalRequest

        with pytest.raises(ValidationError):
            MonitorIntervalRequest(interval=value)

    def test_bounds_match_the_service_minimum(self):
        from api.models import MonitorIntervalRequest
        from app.services.live_monitor_service import MIN_CHECK_INTERVAL

        field = MonitorIntervalRequest.model_fields["interval"]
        lower = next(m.ge for m in field.metadata if hasattr(m, "ge"))
        assert lower == MIN_CHECK_INTERVAL
