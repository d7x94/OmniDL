"""Layout guards: nothing the window layout needs may exceed the space it is given."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

LANGS = ["en", "vi", "zh-CN"]


@pytest.fixture(scope="module")
def win():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from infrastructure.config.config_manager import ConfigManager
    from ui.main_window import MainWindow
    from ui.theme_qt import apply_theme
    from ui.themes.tokens import T

    T.set_mode("dark")
    apply_theme()
    w = MainWindow(service=MagicMock(), config=ConfigManager(Path(tempfile.mkdtemp()) / "config.json"))
    w.show()
    app.processEvents()
    yield w, app
    w.close()


@pytest.mark.parametrize("lang", LANGS)
def test_top_bar_fits_min_window_width(win, lang):
    from utils import i18n

    w, app = win
    i18n.set_language(lang)
    app.processEvents()
    app.processEvents()
    assert w._top_bar.minimumSizeHint().width() <= w.MIN_W
    i18n.set_language("en")
@pytest.mark.parametrize("lang", LANGS)
def test_editor_controls_fit_side_panel(win, lang):
    from utils import i18n

    w, app = win
    i18n.set_language(lang)
    w.navigate_to("editor")
    w.resize(w.MIN_W, w.MIN_H)
    app.processEvents()
    ed = w._tabs["editor"]
    assert ed._controls_panel.width() <= ed._right_scroll.viewport().width()
    i18n.set_language("en")
