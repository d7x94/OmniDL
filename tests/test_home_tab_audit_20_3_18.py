"""tab-home audit v20.3.18: stale theme, stale thumbnail, stale status, tooltips, analyse cancel."""

from __future__ import annotations

import asyncio
import os
import threading
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import api.server as srv
from domain.models.download_task import MediaInfo


@pytest.fixture
def home():
    QApplication.instance() or QApplication([])
    from ui.tabs.home_tab import HomeTab

    fetched: list[dict] = []
    app = types.SimpleNamespace(
        service=types.SimpleNamespace(
            get_download_dir=lambda: Path("/tmp/dl"),
            fetch_thumbnail=lambda **kw: fetched.append(kw),
        ),
        toast=lambda *a, **k: None,
    )
    tab = HomeTab(app)  # type: ignore[arg-type]
    tab._fetched = fetched  # type: ignore[attr-defined]
    yield tab
    from ui.themes.tokens import T

    T.unregister(tab._theme_cb)


def _info(**kw) -> MediaInfo:
    base = dict(url="https://example.com/v", title="clip", platform="YouTube", duration=10)
    base.update(kw)
    return MediaInfo(**base)  # type: ignore[arg-type]


def test_theme_change_restyles_home(home):
    from ui.themes.tokens import T

    before = home._welcome_title_lbl.styleSheet()
    orig = T._mode
    try:
        T.set_mode("light" if orig != "light" else "dark")
        assert home._welcome_title_lbl.styleSheet() != before
        assert T.text in home._welcome_title_lbl.styleSheet()
    finally:
        T.set_mode(orig)


def test_stale_thumbnail_not_applied_to_card_without_thumbnail(home):
    home._populate_card(_info(thumbnail="https://example.com/a.jpg"))
    stale = home._fetched[0]
    home._populate_card(_info(thumbnail=""))
    applied: list[int] = []
    home._thumb_lbl.setPixmap = lambda p: applied.append(1)  # type: ignore[method-assign]

    stale_token = 1
    home._apply_thumb(types.SimpleNamespace(save=lambda *a, **k: None), stale_token)
    assert applied == []
    assert stale  # fetch was issued


def test_live_item_clears_previous_status(home):
    home._set_status("old photo note", "red")
    home._populate_card(_info(is_live=True, formats=[{"format_id": "1"}]))
    assert home._status_lbl.text() == ""
