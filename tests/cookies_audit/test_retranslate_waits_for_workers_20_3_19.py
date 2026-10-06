"""Settings rebuild (v20.3.19): retranslate() waits while a Settings worker runs,
so a finished-later worker cannot re-enable a button of a second run. The API
server thread is long-lived and must not block the rebuild."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.tabs import settings_tab as st_mod


def _thread(name):
    th = MagicMock()
    th.name = name
    th.is_alive.return_value = True
    return th


def _fake_self():
    return SimpleNamespace(
        _search_box=MagicMock(),
        _no_results=MagicMock(),
        _scroll=MagicMock(),
        _build_content=MagicMock(),
        _on_search=MagicMock(),
        retranslate=MagicMock(),
    )


def test_rebuild_deferred_while_settings_worker_runs():
    fake = _fake_self()
    with (
        patch.object(st_mod.threading, "enumerate", return_value=[_thread("omnidl-cookie-extract-x")]),
        patch.object(st_mod.QTimer, "singleShot") as single,
    ):
        st_mod.SettingsTab.retranslate(fake)
    fake._build_content.assert_not_called()
    single.assert_called_once()


def test_rebuild_not_blocked_by_api_server_thread():
    fake = _fake_self()
    with patch.object(st_mod.threading, "enumerate", return_value=[_thread("omnidl-api-server")]):
        st_mod.SettingsTab.retranslate(fake)
    fake._build_content.assert_called_once()
