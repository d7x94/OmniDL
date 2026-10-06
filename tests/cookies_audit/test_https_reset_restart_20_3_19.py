"""HTTPS profile reset (v20.3.19): a failed API restart after the new serve is up
must roll HTTPS back, the same way the enable path does, and refresh the token label."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.tabs.settings import remote_api_panel as rap_mod


def test_reset_restart_failure_rolls_back_https():
    cfg = MagicMock()
    cfg.api_ts_https_enabled = True
    cfg.get.return_value = ""
    panel = SimpleNamespace(
        _app=SimpleNamespace(config=cfg, toast=MagicMock(), _service=object()),
        _ts_https_reset_status=MagicMock(),
        _ts_https_reset_btn=MagicMock(),
        _ts_https_switch=MagicMock(),
        _refresh_ts_https_status=MagicMock(),
        _refresh_api_token_label=MagicMock(),
    )
    panel_cls = rap_mod.RemoteApiPanel
    worker = {}

    class _Thread:
        def __init__(self, target, **kw):
            worker["fn"] = target

        def start(self):
            pass

    with (
        patch.object(rap_mod, "_pick_bindable_port", return_value=55555),
        patch.object(rap_mod.QMessageBox, "question", return_value=rap_mod.QMessageBox.StandardButton.Yes),
        patch.object(rap_mod.threading, "Thread", _Thread),
        patch.object(rap_mod.ui_bridge, "post", lambda fn: fn()),
        patch.object(rap_mod.QTimer, "singleShot", lambda *a, **k: None),
        patch("api.tailscale_https.reset_tailscale_serve"),
        patch("api.tailscale_https.start_tailscale_serve", return_value=True),
        patch("api.tailscale_https.get_tailscale_dns_name", return_value=""),
        patch("api.server.restart_api_server", side_effect=RuntimeError("boom")),
    ):
        panel_cls._on_ts_https_reset(panel)
        worker["fn"]()

    cfg.set.assert_any_call("api_ts_https_enabled", False)
    panel._ts_https_switch.setChecked.assert_called_with(False)
    panel._refresh_api_token_label.assert_called()
