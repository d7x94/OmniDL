"""Tests for ui/tabs/special_dl_tab.py — pure UI logic (no browser/network).

Uses the unbound-method pattern (SimpleNamespace as self) so no QApplication
or display server is required.

Covers:
  - _on_platform_change: guide text, placeholder, browser row visibility
  - _set_status: status label, progress bar, retry row show/hide
  - _clear_status: reset all status UI and _last_dest
  - _on_post_send: taildrop validation (no nodes, non-iterable, disabled)
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from utils.i18n import t

# ─────────────────────────────────────────────────────────────────────────────
# Stubs
# ─────────────────────────────────────────────────────────────────────────────


class _W:
    """Minimal widget stub that records state changes."""

    def __init__(self, text: str = "", visible: bool = True) -> None:
        self._text = text
        self._visible = visible
        self._style = ""
        self._value = 0
        self._placeholder = ""

    def setText(self, t: str) -> None:
        self._text = t

    def text(self) -> str:
        return self._text

    def setStyleSheet(self, s: str) -> None:
        self._style = s

    def styleSheet(self) -> str:
        return self._style

    def show(self) -> None:
        self._visible = True

    def hide(self) -> None:
        self._visible = False

    def isVisible(self) -> bool:
        return self._visible

    def setValue(self, v: int) -> None:
        self._value = v

    def value(self) -> int:
        return self._value

    def setPlaceholderText(self, t: str) -> None:
        self._placeholder = t

    def placeholderText(self) -> str:
        return self._placeholder


def _status_stub(last_dest=None, retry_visible=False, btn_visible=False, progress=0):
    """Return a SimpleNamespace wired for _set_status / _clear_status calls."""
    tab = SimpleNamespace(
        _status_dot=_W(),
        _status_lbl=_W(text="Đang chờ..."),
        _speed_lbl=_W(),
        _progress=_W(),
        _retry_row=_W(visible=retry_visible),
        _btn_row=_W(visible=btn_visible),
        _post_actions=_W(),
        _last_dest=last_dest,
    )
    tab._progress._value = progress
    return tab


def _platform_stub():
    """Return a SimpleNamespace wired for _on_platform_change calls."""
    return SimpleNamespace(
        _guide_lbl=_W(),
        _url_entry=_W(),
        _browser_row=_W(visible=True),
    )


# ─────────────────────────────────────────────────────────────────────────────
# _set_status
# ─────────────────────────────────────────────────────────────────────────────


class TestSetStatus:
    def _call(self, tab, kind, message, pct=-1, speed=""):
        from ui.tabs.special_dl_tab import SpecialDlTab

        SpecialDlTab._set_status(tab, kind, message, pct, speed)

    def test_error_shows_retry_row(self):
        tab = _status_stub()
        self._call(tab, "error", "fail")
        assert tab._retry_row.isVisible()

    def test_info_pct0_hides_retry_row(self):
        tab = _status_stub(retry_visible=True)
        self._call(tab, "info", "starting", 0)
        assert not tab._retry_row.isVisible()

    def test_success_sets_progress(self):
        tab = _status_stub()
        self._call(tab, "success", "done", 100)
        assert tab._progress.value() == 100

    def test_negative_pct_leaves_progress_unchanged(self):
        tab = _status_stub(progress=42)
        self._call(tab, "info", "msg")  # pct defaults to -1
        assert tab._progress.value() == 42

    def test_status_label_updated(self):
        tab = _status_stub()
        self._call(tab, "info", "Đang khởi động...")
        assert tab._status_lbl.text() == "Đang khởi động..."

    def test_speed_label_updated(self):
        tab = _status_stub()
        self._call(tab, "info", "msg", 50, "2.1 MB/s")
        assert tab._speed_lbl.text() == "2.1 MB/s"


# ─────────────────────────────────────────────────────────────────────────────
# _clear_status
# ─────────────────────────────────────────────────────────────────────────────


class TestClearStatus:
    def _call(self, tab):
        from ui.tabs.special_dl_tab import SpecialDlTab

        SpecialDlTab._clear_status(tab)

    def test_hides_retry_row(self):
        tab = _status_stub(retry_visible=True)
        self._call(tab)
        assert not tab._retry_row.isVisible()

    def test_hides_btn_row(self):
        tab = _status_stub(btn_visible=True)
        self._call(tab)
        assert not tab._btn_row.isVisible()

    def test_clears_last_dest(self):
        tab = _status_stub(last_dest=Path("/tmp/foo.mp4"))  # nosec B108
        self._call(tab)
        assert tab._last_dest is None

    def test_resets_status_label(self):
        tab = _status_stub()
        tab._status_lbl.setText("something")
        self._call(tab)
        assert tab._status_lbl.text() == t("special.status.waiting")

    def test_resets_progress(self):
        tab = _status_stub(progress=75)
        self._call(tab)
        assert tab._progress.value() == 0

    def test_clears_speed_label(self):
        tab = _status_stub()
        tab._speed_lbl.setText("1.5 MB/s")
        self._call(tab)
        assert tab._speed_lbl.text() == ""


# ─────────────────────────────────────────────────────────────────────────────
# _on_platform_change
# ─────────────────────────────────────────────────────────────────────────────


class TestOnPlatformChange:
    def _call(self, tab, name):
        from ui.tabs.special_dl_tab import SpecialDlTab

        SpecialDlTab._on_platform_change(tab, name)

    def test_facebook_story_shows_browser_row(self):
        tab = _platform_stub()
        tab._browser_row._visible = False
        self._call(tab, "Facebook Story")
        assert tab._browser_row.isVisible()

    def test_facebook_story_sets_nonempty_guide_text(self):
        tab = _platform_stub()
        self._call(tab, "Facebook Story")
        assert len(tab._guide_lbl.text()) > 0

    def test_facebook_story_sets_placeholder(self):
        tab = _platform_stub()
        self._call(tab, "Facebook Story")
        assert "facebook.com" in tab._url_entry.placeholderText()

    def test_unknown_platform_falls_back_to_facebook_story(self):
        tab = _platform_stub()
        tab._browser_row._visible = False
        self._call(tab, "Unknown Platform XYZ")
        assert tab._browser_row.isVisible()


# ─────────────────────────────────────────────────────────────────────────────
# _on_post_send validation
# ─────────────────────────────────────────────────────────────────────────────


def _send_stub(nodes, enabled=True):
    """Namespace for _on_post_send calls."""
    cfg = SimpleNamespace(taildrop_target_nodes=nodes, taildrop_enabled=enabled)
    recorded = []

    def _set_status(kind, msg, pct=-1, speed=""):
        recorded.append((kind, msg))

    tab = SimpleNamespace(
        _config=cfg,
        _app=MagicMock(),
        _set_status=_set_status,
        _status_lbl=_W(),
        _recorded=recorded,
    )
    return tab


class TestOnPostSendValidation:
    def _call(self, tab, file_path, restore, specific_files=None):
        from ui.tabs.special_dl_tab import SpecialDlTab

        SpecialDlTab._on_post_send(tab, file_path, restore, specific_files)

    def test_empty_nodes_warns_and_restores(self):
        tab = _send_stub(nodes=[])
        restore = MagicMock()
        self._call(tab, Path("/tmp/f.mp4"), restore)  # nosec B108
        assert any(k == "warning" for k, _ in tab._recorded)
        restore.assert_called_once()

    def test_none_nodes_warns_and_restores(self):
        tab = _send_stub(nodes=None)
        restore = MagicMock()
        self._call(tab, Path("/tmp/f.mp4"), restore)  # nosec B108
        restore.assert_called_once()

    def test_non_iterable_nodes_warns_and_restores(self):
        tab = _send_stub(nodes=42)
        restore = MagicMock()
        self._call(tab, Path("/tmp/f.mp4"), restore)  # nosec B108
        restore.assert_called_once()

    def test_taildrop_disabled_warns_and_restores(self):
        tab = _send_stub(nodes=["node1"], enabled=False)
        restore = MagicMock()
        self._call(tab, Path("/tmp/f.mp4"), restore)  # nosec B108
        assert any(k == "warning" for k, _ in tab._recorded)
        restore.assert_called_once()

    def test_valid_config_attempts_send(self):
        tab = _send_stub(nodes=["node1"], enabled=True)
        restore = MagicMock()
        self._call(tab, Path("/tmp/f.mp4"), restore)  # nosec B108
        tab._app.taildrop.send_file_to_nodes.assert_called_once()
