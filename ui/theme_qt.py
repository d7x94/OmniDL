"""Qt theming: generates QSS from the token palette and applies it to QApplication."""

from __future__ import annotations

import sys
from pathlib import Path
from string import Template

from PySide6.QtCore import QDir
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.themes.tokens import T

_THEMES_DIR = Path(__file__).parent / "themes"
_ASSETS_SEARCH_PREFIX = "omnidl-assets"


def _assets_dir() -> str:
    """Absolute path to ui/assets — works both frozen and from source.

    PyInstaller unpacks --add-data "ui/assets;ui/assets" under
    sys._MEIPASS/ui/assets; from source it sits next to this file.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) / "ui" / "assets" if meipass else Path(__file__).parent / "assets"
    return str(base)


def _register_assets_search_path() -> None:
    """Make ui/assets reachable from QSS as url(omnidl-assets:file.png).

    A raw filesystem path in QSS url() is resolved through QUrl, where an
    absolute Windows path (e.g. "C:/Users/.../check.png") can have its drive
    letter misparsed as a URL scheme, and any space in the path breaks the
    unquoted url() token — both silently fail to load with no error, no
    matter how carefully the path is built. QDir.addSearchPath sidesteps
    all of that: it's Qt's own mechanism for referencing bundled files from
    stylesheets without exposing OS path syntax to the QSS parser.
    """
    QDir.addSearchPath(_ASSETS_SEARCH_PREFIX, _assets_dir())


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
    _register_assets_search_path()
    fname = "dark.qss" if T.is_dark else "light.qss"
    template = Template((_THEMES_DIR / fname).read_text(encoding="utf-8"))
    return template.safe_substitute(T._palette)
