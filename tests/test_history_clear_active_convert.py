"""History Clear all must keep tasks that still have an active remote convert job."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch


def test_clear_all_excludes_active_convert_tasks():
    from ui.tabs.history_tab import HistoryTab

    service = MagicMock()
    qt = types.SimpleNamespace(_app=types.SimpleNamespace(service=service), refresh=MagicMock())

    with (
        patch("ui.tabs.history_tab.QMessageBox.question", return_value=1),
        patch("ui.tabs.history_tab.QMessageBox.StandardButton") as btn,
        patch("api.server.tasks_with_active_convert", return_value=frozenset({"t-conv"})),
    ):
        btn.Yes = 1
        HistoryTab._clear_all(qt)

    service.clear_history.assert_called_once_with(exclude_ids=frozenset({"t-conv"}))
    qt.refresh.assert_called_once()
