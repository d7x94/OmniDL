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
from PySide6.QtWidgets import QApplication, QPushButton
app = QApplication([])
from infrastructure.config.config_manager import ConfigManager
from ui.main_window import MainWindow
from ui.theme_qt import apply_theme
from ui.themes.tokens import T
from utils import i18n
T.set_mode("dark"); apply_theme()
svc = MagicMock()
svc.get_history.return_value = [{"id": "a", "url": "https://x/1", "filename": "a.mp4", "output_dir": "/tmp",
    "status": "COMPLETED", "platform": "TikTok", "downloaded_bytes": 1, "finished_at": 1}]
w = MainWindow(service=svc, config=ConfigManager(Path(tempfile.mkdtemp()) / "config.json"))
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
    hist = w._tabs["history"]
    hist.refresh(); app.processEvents()
    conv, home = w._tabs["convert"], w._tabs["home"]
    def fixed(btns):
        return [b.text() for b in btns if b.minimumWidth() == b.maximumWidth()]
    out[lang]["fixed"] = {
        "history_row": fixed(hist._scroll_content.findChildren(QPushButton)),
        "convert_clear": fixed([conv._clear_btn]),
        "convert_subs": fixed([conv._subs_btn]),
        "home_browse": fixed([home._browse_btn]),
        "theme": fixed([w._theme_btn]),
    }
print("RESULT" + json.dumps(out))
sys.stdout.flush()
import os; os._exit(0)
"""


@pytest.fixture(scope="module")
def measured():
    # Qt's default PipeWire audio backend segfaults in QAudioOutput() (EditorTab) under WSLg.
    env = {"QT_AUDIO_BACKEND": "null", **os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(_ROOT)}
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


# Text buttons must not get a fixed width: QSS clips the label to the content
# rect, and Segoe UI on Windows is wider than the CI fallback font.
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("scope", ["history_row", "convert_clear", "convert_subs", "home_browse", "theme"])
def test_text_buttons_have_no_fixed_width(measured, lang, scope):
    assert measured[lang]["fixed"][scope] == []
