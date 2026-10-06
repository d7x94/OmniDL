"""Theme change from Settings (v20.3.19): the top-bar theme button label must
re-sync, not only when the toggle button itself is pressed."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui import main_window as mw_mod


def test_on_theme_resyncs_theme_button():
    fake = SimpleNamespace(
        _current_tab=None,
        _pill_btns={},
        _apply_strip_styles=MagicMock(),
        _sync_theme_btn=MagicMock(),
    )
    with patch.object(mw_mod, "apply_theme"):
        mw_mod.MainWindow._on_theme(fake)
    fake._sync_theme_btn.assert_called_once()
