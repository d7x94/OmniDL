"""
tests/test_theme_callback_leak.py
Regression test for the theme-callback retention leak.

ui/themes/tokens.py keeps a module-level list of theme callbacks.
OmniProgressBar registered itself there but never unregistered, so every
DownloadItemWidget ever built stayed reachable through its progress bar's
bound method and was never freed by deleteLater().

tests/test_queue_open_folder_fix.py replaces ui.themes.tokens and
ui.components.progress_bar in sys.modules at import time and never restores
them, so these tests re-import the real modules through monkeypatch.delitem
(which puts the stubs back afterwards).
"""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture
def real_modules(monkeypatch):
    """Import the real ui modules, restoring any stubs on teardown."""
    for name in (
        "ui.themes.tokens",
        "ui.components.progress_bar",
        "ui.components.download_item_widget",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return (
        importlib.import_module("ui.components.progress_bar"),
        importlib.import_module("ui.components.download_item_widget"),
    )


class _Recorder:
    def __init__(self) -> None:
        self.unregistered: list = []

    def unregister(self, cb) -> None:
        self.unregistered.append(cb)


def test_progress_bar_unregisters_its_theme_callback(real_modules, monkeypatch):
    progress_mod, _ = real_modules
    recorder = _Recorder()
    monkeypatch.setattr(progress_mod, "T", recorder)

    bar = progress_mod.OmniProgressBar.__new__(progress_mod.OmniProgressBar)
    bar._theme_cb = bar._on_theme

    # super().deleteLater() needs a real C++ object; the unregister call under
    # test runs before it.
    try:
        progress_mod.OmniProgressBar.deleteLater(bar)
    except Exception:
        pass

    assert recorder.unregistered == [bar._theme_cb]


def test_item_widget_delete_releases_the_progress_bar_callback(real_modules, monkeypatch):
    """DownloadItemWidget.deleteLater must forward to its child progress bar.

    Qt destroys children at the C++ level without calling their Python
    deleteLater() override, so the forwarding call is what actually releases
    the child's theme callback.
    """
    _, item_mod = real_modules
    recorder = _Recorder()
    monkeypatch.setattr(item_mod, "T", recorder)

    calls: list[str] = []
    widget = item_mod.DownloadItemWidget.__new__(item_mod.DownloadItemWidget)
    widget._theme_cb = lambda: None
    widget._prog = type("_Prog", (), {"deleteLater": lambda self: calls.append("prog")})()

    try:
        item_mod.DownloadItemWidget.deleteLater(widget)
    except Exception:
        pass

    assert calls == ["prog"]
    assert recorder.unregistered == [widget._theme_cb]
