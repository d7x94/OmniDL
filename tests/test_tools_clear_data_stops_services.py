"""Regression test: Settings > Tools' Clear Data must stop the services it
resets to defaults (API server, tailscale HTTPS serve, clipboard monitor) and
rebuild the TikTok pool — otherwise they keep running with stale config after
reset_to_defaults() silently flips their enable flags to False.

Uses the unbound-method pattern (SimpleNamespace as self) so no QApplication
or display server is required, matching tests/test_archive_tab.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from ui.tabs.settings.tools_panel import ToolsPanel


class _W:
    def __init__(self) -> None:
        self._text = ""
        self._style = ""

    def setText(self, text: str) -> None:
        self._text = text

    def setStyleSheet(self, style: str) -> None:
        self._style = style


def _make_self(app: MagicMock) -> SimpleNamespace:
    return SimpleNamespace(_app=app, _clear_data_status=_W())


def test_clear_all_data_stops_api_server_and_tailscale_and_rebuilds_pool() -> None:
    app = MagicMock()
    app.service.get_all_tasks.return_value = []
    app.get_tab.return_value = None
    app.config.api_ts_https_internal_port = 4455

    self_stub = _make_self(app)

    with (
        patch("ui.tabs.settings.tools_panel.QMessageBox") as mock_box,
        patch("api.server.is_api_running", return_value=True),
        patch("api.server.stop_api_server") as mock_stop_api,
        patch("api.tailscale_https.stop_tailscale_serve") as mock_stop_ts,
    ):
        mock_box.StandardButton = QMessageBox.StandardButton
        mock_box.question.return_value = QMessageBox.StandardButton.Yes

        ToolsPanel._clear_all_data(self_stub)

    mock_stop_api.assert_called_once()
    mock_stop_ts.assert_called_once_with(4455)
    app.stop_clipboard_monitor.assert_called_once()
    app.rebuild_tiktok_pool.assert_called_once()
    app.config.reset_to_defaults.assert_called_once()


def test_clear_all_data_skips_stop_calls_when_nothing_running() -> None:
    app = MagicMock()
    app.service.get_all_tasks.return_value = []
    app.get_tab.return_value = None
    app.config.api_ts_https_internal_port = 0

    self_stub = _make_self(app)

    with (
        patch("ui.tabs.settings.tools_panel.QMessageBox") as mock_box,
        patch("api.server.is_api_running", return_value=False),
        patch("api.server.stop_api_server") as mock_stop_api,
        patch("api.tailscale_https.stop_tailscale_serve") as mock_stop_ts,
    ):
        mock_box.StandardButton = QMessageBox.StandardButton
        mock_box.question.return_value = QMessageBox.StandardButton.Yes

        ToolsPanel._clear_all_data(self_stub)

    mock_stop_api.assert_not_called()
    mock_stop_ts.assert_not_called()
    app.stop_clipboard_monitor.assert_called_once()
    app.rebuild_tiktok_pool.assert_called_once()
