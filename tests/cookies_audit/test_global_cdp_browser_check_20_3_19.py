"""Global CDP (v20.3.19): an unsupported browser is rejected before the confirm dialog,
so the user is not asked to confirm an action that cannot run."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.tabs.settings import network_panel as np_mod


def test_unsupported_browser_rejected_before_confirm():
    fake = SimpleNamespace(
        _browser_combo=SimpleNamespace(currentText=lambda: "firefox"),
        _app=SimpleNamespace(toast=MagicMock()),
    )
    with patch.object(np_mod.QMessageBox, "question") as question:
        np_mod.NetworkPanel._extract_global_cdp(fake)
    question.assert_not_called()
    fake._app.toast.assert_called_once()
