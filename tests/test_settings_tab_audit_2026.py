"""Regression tests for the 2026-08 Settings tab audit.

Covers:
  - RemoteApiPanel: every API (re)start hands over the raw DownloadService, never
    the desktop ServiceFacade (the facade has no clear_file_record, so
    DELETE /api/files broke after any Tailscale HTTPS toggle)
  - QTimer.singleShot status-clearing callbacks pass a context object, so a panel
    rebuild (theme / language switch) cannot fire them at deleted widgets
  - NetworkPanel._save_new_tiktok_account: no success toast when the config layer
    rejects the cookie path and the account never lands in the pool
  - NetworkPanel: both global cookie-extract buttons share one busy state
  - NetworkPanel: platform display names cover every entry of _PC_PLATFORMS
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.tabs.settings.network_panel import _PC_PLATFORMS, _PLATFORM_NAMES, NetworkPanel

_NET_SRC = Path("ui/tabs/settings/network_panel.py").read_text(encoding="utf-8")
_API_SRC = Path("ui/tabs/settings/remote_api_panel.py").read_text(encoding="utf-8")


# ── API server gets the DownloadService, not the facade ──────────────────────


def test_remote_api_panel_never_passes_the_service_facade():
    assert 'getattr(self._app, "service", None)' not in _API_SRC
    assert _API_SRC.count('svc = getattr(self._app, "_service", None)') == 7


def test_service_facade_lacks_clear_file_record():
    """Guards the reason the facade must not reach api.server."""
    from ui.main_window import ServiceFacade

    assert not hasattr(ServiceFacade, "clear_file_record")


# ── deferred status clears carry a context object ────────────────────────────


def test_status_clearing_timers_pass_a_context_object():
    """QTimer.singleShot(ms, context, fn) — Qt drops fn if context was deleted."""
    assert 'QTimer.singleShot(3000, st, lambda: st.setText(""))' in _API_SRC
    assert "QTimer.singleShot(1500, self._proxy_entry," in _NET_SRC
    # The four 6s "clear the extract status" timers.
    assert len(re.findall(r"QTimer\.singleShot\(\s*6000,\s*status,", _NET_SRC)) == 4
    assert "QTimer.singleShot(\n                    6000,\n                    lambda" not in _NET_SRC


# ── TikTok pool: no false success ────────────────────────────────────────────


def _pool_panel(stored):
    cfg = SimpleNamespace(
        tiktok_account_pool=stored,
        set_tiktok_account_pool=MagicMock(),
    )
    return SimpleNamespace(
        _tt_add_name=SimpleNamespace(text=lambda: "acc1"),
        _tt_add_slots=SimpleNamespace(value=lambda: 1),
        _tt_add_pending_cookie="/outside/cookies.txt",
        _tt_add_pending_browser="brave",
        _tt_add_pending_profile="Default",
        _tt_add_pending_fp="fp1",
        _app=SimpleNamespace(config=cfg, toast=MagicMock()),
        _hide_tiktok_add_form=MagicMock(),
        _refresh_tiktok_accounts_list=MagicMock(),
        _rebuild_pool=MagicMock(),
        # A rejected account's jar is now removed rather than left behind.
        _delete_pool_cookie_file=MagicMock(),
    )


def test_rejected_tiktok_account_reports_an_error_not_success():
    panel = _pool_panel(stored=[])  # config drops the entry -> pool stays empty

    NetworkPanel._save_new_tiktok_account(panel)

    kind = panel._app.toast.call_args[0][1]
    assert kind == "error"
    panel._hide_tiktok_add_form.assert_not_called()
    panel._rebuild_pool.assert_not_called()


def test_accepted_tiktok_account_still_reports_success():
    stored: list[dict] = []
    panel = _pool_panel(stored=stored)
    # Mimic a config layer that accepts the entry.
    panel._app.config.set_tiktok_account_pool = lambda pool: stored.extend(pool)

    NetworkPanel._save_new_tiktok_account(panel)

    assert panel._app.toast.call_args[0][1] == "success"
    panel._hide_tiktok_add_form.assert_called_once()
    panel._rebuild_pool.assert_called_once()


# ── global extract buttons share one busy state ──────────────────────────────


def test_both_global_extract_buttons_disable_together():
    assert _NET_SRC.count("busy_btns = (self._extract_global_btn, self._extract_cdp_btn)") == 2
    assert "for b in busy_btns:\n            b.setEnabled(False)" in _NET_SRC
    assert _NET_SRC.count("[b.setEnabled(True) for b in busy_btns]") == 3


# ── platform display names ───────────────────────────────────────────────────


def test_every_platform_row_has_a_display_name():
    for key, label in _PC_PLATFORMS:
        assert _PLATFORM_NAMES[key] == label
    assert _PLATFORM_NAMES["youtube"] == "YouTube"


def test_translation_key_for_a_rejected_account_exists_in_every_language():
    from utils.translations import CATALOG

    for lang, catalogue in CATALOG.items():
        assert "settings.network.account_rejected" in catalogue, lang
