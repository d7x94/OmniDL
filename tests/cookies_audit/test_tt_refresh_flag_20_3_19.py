"""TikTok pool refresh (v20.3.19): the in-progress flag must clear when the
extraction result is applied, or the account's Refresh button stays disabled
and later clicks return early until the panel is rebuilt."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.tabs.settings import network_panel as np_mod


def test_refresh_flag_cleared_when_extraction_fails():
    fake = SimpleNamespace(
        _tt_refreshing={"a1"},
        _app=SimpleNamespace(
            config=SimpleNamespace(tiktok_account_pool=[{"id": "a1", "name": "n", "cookie_file": ""}]),
            toast=MagicMock(),
        ),
        _delete_pool_cookie_file=MagicMock(),
        _refresh_tiktok_accounts_list=MagicMock(),
    )
    np_mod.NetworkPanel._apply_refreshed_cookie(fake, "a1", "", "boom")
    assert "a1" not in fake._tt_refreshing
