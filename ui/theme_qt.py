"""Qt theming: generates QSS from the token palette and applies it to QApplication."""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.themes.tokens import T

_THEMES_DIR = Path(__file__).parent / "themes"


def _assets_dir() -> str:
    """Absolute, forward-slash path to ui/assets — works frozen or from source.

    Qt resolves a relative url() in a stylesheet against the process's cwd, not
    against the .qss file's location, so the path must be absolute. PyInstaller
    unpacks --add-data "ui/assets;ui/assets" under sys._MEIPASS/ui/assets.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) / "ui" / "assets" if meipass else Path(__file__).parent / "assets"
    return base.as_posix()


def get_stylesheet() -> str:
    return _build_qss()


def apply_theme() -> None:
    app = QApplication.instance()
    if app:
        app.setPalette(_build_palette())
        app.setStyleSheet(_build_qss())


def _build_palette() -> QPalette:
    p = QPalette()
    c = QColor
    p.setColor(QPalette.ColorRole.Window, c(T.bg))
    p.setColor(QPalette.ColorRole.WindowText, c(T.text))
    p.setColor(QPalette.ColorRole.Base, c(T.input))
    p.setColor(QPalette.ColorRole.AlternateBase, c(T.surface))
    p.setColor(QPalette.ColorRole.Button, c(T.surface))
    p.setColor(QPalette.ColorRole.ButtonText, c(T.text))
    p.setColor(QPalette.ColorRole.Highlight, c(T.primary))
    p.setColor(QPalette.ColorRole.HighlightedText, c(T.primary_text))
    p.setColor(QPalette.ColorRole.ToolTipBase, c(T.surface2))
    p.setColor(QPalette.ColorRole.ToolTipText, c(T.text))
    p.setColor(QPalette.ColorRole.PlaceholderText, c(T.text3))
    p.setColor(QPalette.ColorRole.Link, c(T.primary))
    return p


def _build_qss() -> str:
    fname = "dark.qss" if T.is_dark else "light.qss"
    template = Template((_THEMES_DIR / fname).read_text(encoding="utf-8"))
    return template.safe_substitute({**T._palette, "assets_dir": _assets_dir()})
