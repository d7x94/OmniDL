"""Layout guards: nothing the window layout needs may exceed the space it is given.

MainWindow is built in a child process: constructing it inside the shared pytest
process aborts after other Qt tests have run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
LANGS = ["en", "vi", "zh-CN"]

_SCRIPT = r"""
import json, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
app = QApplication([])
from infrastructure.config.config_manager import ConfigManager
from ui.main_window import MainWindow
from ui.theme_qt import apply_theme
from ui.themes.tokens import T
from utils import i18n
T.set_mode("dark"); apply_theme()
w = MainWindow(service=MagicMock(), config=ConfigManager(Path(tempfile.mkdtemp()) / "config.json"))
w.show(); app.processEvents()
out = {}
for lang in ("en", "vi", "zh-CN"):
    i18n.set_language(lang)
    w.navigate_to("editor")
    w.resize(w.MIN_W, w.MIN_H)
    app.processEvents(); app.processEvents()
    ed = w._tabs["editor"]
    out[lang] = {
        "top_bar_min": w._top_bar.minimumSizeHint().width(),
        "min_w": w.MIN_W,
        "panel_w": ed._controls_panel.width(),
        "viewport_w": ed._right_scroll.viewport().width(),
    }
print("RESULT" + json.dumps(out))
sys.stdout.flush()
import os; os._exit(0)
"""


@pytest.fixture(scope="module")
def measured():
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(_ROOT)}
    cmd = [sys.executable, "-c", _SCRIPT]
    r = subprocess.run(cmd, env=env, cwd=_ROOT, capture_output=True, text=True, timeout=120)
    line = next(ln for ln in r.stdout.splitlines() if ln.startswith("RESULT"))
    return json.loads(line[len("RESULT") :])


@pytest.mark.parametrize("lang", LANGS)
def test_top_bar_fits_min_window_width(measured, lang):
    m = measured[lang]
    assert m["top_bar_min"] <= m["min_w"]


@pytest.mark.parametrize("lang", LANGS)
def test_editor_controls_fit_side_panel(measured, lang):
    m = measured[lang]
    assert m["panel_w"] <= m["viewport_w"]
