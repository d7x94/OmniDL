"""BUG-COOKIE-EXTRACT-RACE (v20.3.14): while an extraction runs, Choose and Delete
for the same cookie slot stay disabled. Otherwise the finished worker registers
its jar over a file the user just chose, or re-registers a slot the user cleared."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ui.tabs.settings import network_panel as np_mod

_CE = "infrastructure.downloader.cookie_extractor"


def _panel():
    cfg = MagicMock()
    cfg.config_path = Path("/nonexistent/config.json")
    cfg.get_cookie_for_platform.return_value = ""
    cfg.get.return_value = ""
    combo = MagicMock()
    combo.currentData.return_value = "Profile 2"
    fake = SimpleNamespace(
        _browser_combo=SimpleNamespace(currentText=lambda: "brave"),
        _cookie_profile_combo=combo,
        _app=SimpleNamespace(config=cfg, toast=MagicMock()),
        _pc_extract_status=MagicMock(),
        _pc_extract_btns=[MagicMock()],
        _pc_browse_btns=[MagicMock()],
        _pc_clear_btns=[MagicMock()],
        _extract_global_status=MagicMock(),
        _extract_global_btn=MagicMock(),
        _extract_cdp_btn=MagicMock(),
        _browse_cf_btn=MagicMock(),
        _clear_cf_btn=MagicMock(),
        _cf_lbl=MagicMock(),
        _resolve_saved_cookie_path=np_mod.NetworkPanel._resolve_saved_cookie_path,
        _delete_old_cookie_if_replaced=MagicMock(),
        _short_cookie_path=lambda p: p,
    )
    fake._selected_profile = lambda: np_mod.NetworkPanel._selected_profile(fake)
    return fake


class _HeldThread:
    """Capture the worker without running it, so the 'busy' state is observable."""

    held: list = []

    def __init__(self, target, **kw):
        _HeldThread.held.append(target)

    def start(self):
        pass


def _run(fake, call, extractor):
    _HeldThread.held = []
    with (
        patch(f"{_CE}.{extractor}", MagicMock(return_value=(3, None))),
        patch.object(np_mod.threading, "Thread", _HeldThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: fn()),
        patch.object(np_mod.QTimer, "singleShot", lambda *a, **k: None),
        patch.object(np_mod.QMessageBox, "question", return_value=np_mod.QMessageBox.StandardButton.Yes),
    ):
        call(fake)
        busy = fake
        yield busy
        _HeldThread.held[0]()


@pytest.mark.parametrize(
    "method,extractor",
    [("_extract_platform_cookie", "extract_browser_cookies"), ("_extract_platform_cdp", "extract_via_cdp")],
)
def test_platform_choose_and_delete_locked_while_extracting(method, extractor):
    fake = _panel()
    gen = _run(fake, lambda f: getattr(np_mod.NetworkPanel, method)(f, "instagram", MagicMock()), extractor)
    next(gen)
    for btn in (fake._pc_browse_btns[0], fake._pc_clear_btns[0]):
        btn.setEnabled.assert_called_with(False)
    next(gen, None)
    for btn in (fake._pc_browse_btns[0], fake._pc_clear_btns[0]):
        btn.setEnabled.assert_called_with(True)


@pytest.mark.parametrize(
    "method,extractor",
    [("_extract_global_cookies", "extract_browser_cookies"), ("_extract_global_cdp", "extract_via_cdp")],
)
def test_global_choose_and_clear_locked_while_extracting(method, extractor):
    fake = _panel()
    gen = _run(fake, lambda f: getattr(np_mod.NetworkPanel, method)(f), extractor)
    next(gen)
    for btn in (fake._browse_cf_btn, fake._clear_cf_btn):
        btn.setEnabled.assert_called_with(False)
    next(gen, None)
    for btn in (fake._browse_cf_btn, fake._clear_cf_btn):
        btn.setEnabled.assert_called_with(True)
