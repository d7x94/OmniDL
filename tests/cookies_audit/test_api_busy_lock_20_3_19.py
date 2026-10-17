"""Remote API panel (v20.3.19): while any API/HTTPS action runs, every Remote API
control stays disabled. Re-enable only when the last action finishes, so two
restarts cannot overlap."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.tabs.settings import remote_api_panel as rap_mod


def test_controls_stay_disabled_until_last_action_finishes():
    widgets = [MagicMock() for _ in range(4)]
    fake = SimpleNamespace(
        _api_busy=0,
        _api_switch=widgets[0],
        _api_rotate_btn=widgets[1],
        _ts_https_switch=widgets[2],
        _ts_https_reset_btn=widgets[3],
    )
    changed = rap_mod.RemoteApiPanel._api_busy_changed
    changed(fake, 1)
    changed(fake, 1)
    changed(fake, -1)
    for w in widgets:
        w.setEnabled.assert_called_with(False)
    changed(fake, -1)
    for w in widgets:
        w.setEnabled.assert_called_with(True)
