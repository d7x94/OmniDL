"""Queue Clear must work when the optional api extra (fastapi) is not installed."""

from __future__ import annotations

import sys
import types
from unittest.mock import patch


def test_clear_finished_without_api_module_clears_all():
    from ui.tabs.queue_tab import QueueTab

    calls = []
    service = types.SimpleNamespace(
        clear_finished=lambda exclude_ids=None: calls.append(exclude_ids),
    )
    qt = types.SimpleNamespace()
    qt._app = types.SimpleNamespace(service=service)
    qt._select_mode = False
    qt._selected_ids = set()

    with patch.dict(sys.modules, {"api.server": None}):
        QueueTab._clear_finished(qt)

    assert calls == [None]
