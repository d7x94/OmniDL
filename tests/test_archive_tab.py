"""Tests for ui/tabs/archive_tab.py — pure UI logic (no browser/network).

Uses the unbound-method pattern (SimpleNamespace as self) so no QApplication
or display server is required, matching tests/test_special_dl_tab.py.

Covers:
  - _on_compress_error / _on_extract_error: cancellation routed to a neutral
    status instead of QMessageBox.critical
  - _set_busy: cancel button visibility for cancellable vs non-cancellable ops
  - _refresh_name_controls: _use_orig_name_chk enable logic
  - _resolve_archive_name: original-name vs custom-name selection
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.archive_service import ArchiveError

# ─────────────────────────────────────────────────────────────────────────────
# Stubs
# ─────────────────────────────────────────────────────────────────────────────


class _W:
    """Minimal widget stub that records state changes."""

    def __init__(
        self, text: str = "", visible: bool = True, checked: bool = False, enabled: bool = True
    ) -> None:
        self._text = text
        self._visible = visible
        self._checked = checked
        self._enabled = enabled

    def setText(self, t: str) -> None:
        self._text = t

    def text(self) -> str:
        return self._text

    def setVisible(self, v: bool) -> None:
        self._visible = v

    def isVisible(self) -> bool:
        return self._visible

    def setChecked(self, c: bool) -> None:
        self._checked = c

    def isChecked(self) -> bool:
        return self._checked

    def setEnabled(self, e: bool) -> None:
        self._enabled = e

    def isEnabled(self) -> bool:
        return self._enabled


class _ProgressStub:
    def __init__(self) -> None:
        self._value = 0
        self._state = ""
        self._visible = True

    def set_progress(self, v: float) -> None:
        self._value = v

    def set_state(self, s: str) -> None:
        self._state = s

    def setVisible(self, v: bool) -> None:
        self._visible = v


def _error_stub() -> SimpleNamespace:
    return SimpleNamespace(
        _set_busy=MagicMock(),
        _progress=_ProgressStub(),
        _status_lbl=_W(),
    )


def _busy_stub() -> SimpleNamespace:
    return SimpleNamespace(
        _busy=False,
        _compress_btn=_W(),
        _extract_btn=_W(),
        _list_contents_btn=_W(),
        _progress=_ProgressStub(),
        _cancel_btn=_W(),
        _cancel_event=object(),
    )


def _name_controls_stub(sources, individually_checked: bool) -> SimpleNamespace:
    return SimpleNamespace(
        _compress_sources=sources,
        _individually_chk=_W(checked=individually_checked),
        _use_orig_name_chk=_W(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# _on_compress_error / _on_extract_error
# ─────────────────────────────────────────────────────────────────────────────


class TestOnCompressError:
    def _call(self, tab, exc):
        from ui.tabs.archive_tab import ArchiveTab

        ArchiveTab._on_compress_error(tab, exc)

    def test_cancellation_does_not_show_critical_dialog(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox") as mock_box:
            self._call(tab, ArchiveError("Compression cancelled"))
            mock_box.critical.assert_not_called()

    def test_cancellation_sets_neutral_progress_state(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox"):
            self._call(tab, ArchiveError("Compression cancelled"))
        assert tab._progress._state == "paused"

    def test_real_error_shows_critical_dialog(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox") as mock_box:
            self._call(tab, ArchiveError("Archive already exists: /tmp/x.zip"))  # nosec B108
            mock_box.critical.assert_called_once()

    def test_real_error_sets_failed_progress_state(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox"):
            self._call(tab, ArchiveError("boom"))
        assert tab._progress._state == "failed"

    def test_error_always_clears_busy(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox"):
            self._call(tab, ArchiveError("Compression cancelled"))
        tab._set_busy.assert_called_once_with(False)


class TestOnExtractError:
    def _call(self, tab, exc):
        from ui.tabs.archive_tab import ArchiveTab

        ArchiveTab._on_extract_error(tab, exc)

    def test_cancellation_does_not_show_critical_dialog(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox") as mock_box:
            self._call(tab, ArchiveError("Extraction cancelled"))
            mock_box.critical.assert_not_called()

    def test_cancellation_sets_neutral_progress_state(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox"):
            self._call(tab, ArchiveError("Extraction cancelled"))
        assert tab._progress._state == "paused"

    def test_real_error_shows_critical_dialog(self):
        tab = _error_stub()
        with patch("ui.tabs.archive_tab.QMessageBox") as mock_box:
            self._call(tab, ArchiveError("Incorrect or missing password"))
            mock_box.critical.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _set_busy
# ─────────────────────────────────────────────────────────────────────────────


class TestSetBusy:
    def _call(self, tab, busy, cancellable=False):
        from ui.tabs.archive_tab import ArchiveTab

        ArchiveTab._set_busy(tab, busy, cancellable)

    def test_busy_cancellable_shows_cancel_button(self):
        tab = _busy_stub()
        self._call(tab, True, cancellable=True)
        assert tab._cancel_btn.isVisible()

    def test_busy_non_cancellable_hides_cancel_button(self):
        tab = _busy_stub()
        self._call(tab, True, cancellable=False)
        assert not tab._cancel_btn.isVisible()

    def test_not_busy_hides_cancel_button(self):
        tab = _busy_stub()
        self._call(tab, False)
        assert not tab._cancel_btn.isVisible()

    def test_busy_disables_action_buttons(self):
        tab = _busy_stub()
        self._call(tab, True)
        assert not tab._compress_btn.isEnabled()
        assert not tab._extract_btn.isEnabled()
        assert not tab._list_contents_btn.isEnabled()

    def test_not_busy_clears_cancel_event(self):
        tab = _busy_stub()
        self._call(tab, False)
        assert tab._cancel_event is None


# ─────────────────────────────────────────────────────────────────────────────
# _refresh_name_controls
# ─────────────────────────────────────────────────────────────────────────────


class TestRefreshNameControls:
    def _call(self, tab):
        from ui.tabs.archive_tab import ArchiveTab

        ArchiveTab._refresh_name_controls(tab)

    def test_single_source_not_individually_enables_orig_name(self):
        tab = _name_controls_stub(sources=["a"], individually_checked=False)
        self._call(tab)
        assert tab._use_orig_name_chk.isEnabled()

    def test_multiple_sources_disables_orig_name(self):
        tab = _name_controls_stub(sources=["a", "b"], individually_checked=False)
        self._call(tab)
        assert not tab._use_orig_name_chk.isEnabled()

    def test_individually_mode_disables_orig_name(self):
        tab = _name_controls_stub(sources=["a"], individually_checked=True)
        self._call(tab)
        assert not tab._use_orig_name_chk.isEnabled()

    def test_no_sources_disables_orig_name(self):
        tab = _name_controls_stub(sources=[], individually_checked=False)
        self._call(tab)
        assert not tab._use_orig_name_chk.isEnabled()


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_archive_name
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_name_stub(orig_checked: bool, orig_enabled: bool, entry_text: str) -> SimpleNamespace:
    return SimpleNamespace(
        _use_orig_name_chk=_W(checked=orig_checked, enabled=orig_enabled),
        _archive_name_entry=_W(text=entry_text),
    )


class TestResolveArchiveName:
    def _call(self, tab, sources):
        from ui.tabs.archive_tab import ArchiveTab

        return ArchiveTab._resolve_archive_name(tab, sources)

    def test_unchecked_uses_custom_text(self):
        tab = _resolve_name_stub(orig_checked=False, orig_enabled=True, entry_text="my-custom")
        assert self._call(tab, [Path("/tmp/video.mp4")]) == "my-custom"  # nosec B108

    def test_checked_uses_source_stem_not_custom_text(self):
        tab = _resolve_name_stub(orig_checked=True, orig_enabled=True, entry_text="my-custom")
        assert self._call(tab, [Path("/tmp/video.mp4")]) == "video"  # nosec B108

    def test_checked_but_disabled_falls_back_to_custom_text(self):
        tab = _resolve_name_stub(orig_checked=True, orig_enabled=False, entry_text="my-custom")
        assert self._call(tab, [Path("/tmp/video.mp4")]) == "my-custom"  # nosec B108

    def test_unchecked_blank_entry_falls_back_to_archive(self):
        tab = _resolve_name_stub(orig_checked=False, orig_enabled=True, entry_text="  ")
        assert self._call(tab, [Path("/tmp/video.mp4")]) == "archive"  # nosec B108

    def test_checked_strips_custom_text_whitespace_not_applicable_to_stem(self):
        tab = _resolve_name_stub(orig_checked=True, orig_enabled=True, entry_text="fallback")
        assert self._call(tab, [Path("/tmp/My Video.mkv")]) == "My Video"
