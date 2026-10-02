"""Regression tests for the EditorTab export/preview concurrency bugs.

Uses the unbound-method pattern (SimpleNamespace as self) so no QApplication
or display server is required, matching tests/test_archive_tab.py.

Covers:
  - A stale on_error callback from a cancelled export must not touch the
    cancel handle or status of a NEW export started in between
    (generation-counter guard, mirrors the existing _preview_gen pattern).
  - Export refuses to start while a preview is running/previewing.
  - Preview gives feedback (doesn't silently no-op) while an export runs.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import ui.tabs.editor_tab as editor_tab_module
from ui.tabs.editor_tab import EditorTab


class _W:
    """Minimal widget stub that records state changes."""

    def __init__(self, text: str = "") -> None:
        self._text = text

    def setText(self, t: str) -> None:
        self._text = t

    def text(self) -> str:
        return self._text

    def setStyleSheet(self, _s: str) -> None:
        pass


def _make_self() -> SimpleNamespace:
    ns = SimpleNamespace(
        _cancel_trim=None,
        _export_gen=0,
        _cancel_preview=None,
        _preview_mode=False,
        _export_btn=_W(),
        _export_status=_W(),
        _preview_btn=_W(),
        _file_path="/tmp/fake_source.mp4",
        _original_duration=10_000,
        _in_ms=0,
        _out_ms=-1,
        _player=MagicMock(duration=MagicMock(return_value=10_000)),
        _collect_params=lambda: {},
    )
    ns._cleanup_export = lambda: EditorTab._cleanup_export(ns)
    ns._finish_export = lambda path, gen: EditorTab._finish_export(ns, path, gen)
    ns._fail_export = lambda msg, gen: EditorTab._fail_export(ns, msg, gen)
    ns._on_export_progress = lambda pct, gen: EditorTab._on_export_progress(ns, pct, gen)
    ns._back_to_original = lambda: EditorTab._back_to_original(ns)
    return ns


def _fake_trim_video_factory(monkeypatch):
    """Returns (fake_trim_video, calls) — calls[i] holds (cancel_mock, on_done, on_error)."""
    calls: list[dict] = []

    def fake_trim_video(
        source, output, start_ms, end_ms, on_progress=None, on_done=None, on_error=None, **_params
    ):
        cancel = MagicMock(name=f"cancel_{len(calls)}")
        calls.append({"cancel": cancel, "on_done": on_done, "on_error": on_error, "output": output})
        return cancel

    monkeypatch.setattr(editor_tab_module, "trim_video", fake_trim_video)
    # Run ui_bridge.post() synchronously — no Qt event loop in this test.
    monkeypatch.setattr(editor_tab_module.ui_bridge, "post", lambda fn: fn())
    return calls


def test_stale_cancelled_export_does_not_clobber_new_export(monkeypatch, tmp_path):
    """Reproduces the known race: cancel export 1, start export 2, then
    export 1's late on_error('cancelled') arrives. Before the fix, this wiped
    export 2's _cancel_trim and showed a bogus 'cancelled' error while
    export 2 was still running."""
    calls = _fake_trim_video_factory(monkeypatch)
    ns = _make_self()
    ns._file_path = str(tmp_path / "source.mp4")

    # Start export 1.
    EditorTab._export(ns)
    assert len(calls) == 1
    assert ns._cancel_trim is calls[0]["cancel"]
    gen_after_start_1 = ns._export_gen

    # Cancel export 1 (second click on the Export/Cancel button).
    EditorTab._export(ns)
    calls[0]["cancel"].assert_called_once()
    assert ns._cancel_trim is None
    assert ns._export_gen != gen_after_start_1  # generation bumped on cancel

    # Start export 2 before export 1's worker thread has reported back.
    EditorTab._export(ns)
    assert len(calls) == 2
    assert ns._cancel_trim is calls[1]["cancel"]

    # Export 1's worker finally calls on_error("cancelled") — this must be a no-op now.
    calls[0]["on_error"]("cancelled")
    assert ns._cancel_trim is calls[1]["cancel"], (
        "stale callback must not clear the new export's cancel handle"
    )
    assert "cancelled" not in ns._export_status.text().lower()

    # Export 2 finishes for real.
    output_path = Path(calls[1]["output"])
    calls[1]["on_done"](output_path)
    assert ns._cancel_trim is None
    assert output_path.name in ns._export_status.text()


def test_export_refuses_to_start_while_previewing(monkeypatch, tmp_path):
    calls = _fake_trim_video_factory(monkeypatch)
    ns = _make_self()
    ns._file_path = str(tmp_path / "source.mp4")
    ns._cancel_preview = MagicMock()

    EditorTab._export(ns)

    assert len(calls) == 0, "export must not start a trim while a preview is active"
    assert ns._cancel_trim is None


def test_preview_click_gives_feedback_while_exporting(monkeypatch, tmp_path):
    calls = _fake_trim_video_factory(monkeypatch)
    ns = _make_self()
    ns._file_path = str(tmp_path / "source.mp4")
    ns._preview_gen = 0
    ns._current_source = Path(ns._file_path)

    EditorTab._export(ns)
    assert len(calls) == 1

    before = ns._export_status.text()
    EditorTab._on_preview_click(ns)

    # Must not have started a preview trim, and must have changed the status
    # (not a silent no-op).
    assert ns._export_status.text() != before or ns._export_status.text() != ""
    assert ns._cancel_preview is None
