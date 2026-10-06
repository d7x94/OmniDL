"""The theme button names the kind of the theme a click switches to."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from utils.i18n import t


@pytest.mark.parametrize(
    ("current", "expected_key"),
    [
        ("violet", "topbar.theme_dark"),  # next is midnight (dark)
        ("midnight", "topbar.theme_light"),  # next is light
        ("light", "topbar.theme_dark"),  # next wraps to violet (dark)
    ],
)
def test_theme_btn_names_next_theme_kind(monkeypatch, current, expected_key):
    from ui.main_window import MainWindow
    from ui.themes.tokens import T

    monkeypatch.setattr(T, "_mode", current)
    fake = MagicMock()
    MainWindow._sync_theme_btn(fake)

    fake._theme_btn.setText.assert_called_once_with(t(expected_key))


def test_on_theme_resyncs_theme_button(monkeypatch):
    from ui import main_window as mw_mod

    monkeypatch.setattr(mw_mod, "apply_theme", MagicMock())
    fake = MagicMock(_current_tab=None, _pill_btns={})
    mw_mod.MainWindow._on_theme(fake)

    fake._sync_theme_btn.assert_called_once()
