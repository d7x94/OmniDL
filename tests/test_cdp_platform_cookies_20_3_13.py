"""Regression tests for BUG-CDP-NO-PROFILE and BUG-COOKIE-OKRU (v20.3.13).

Per-platform "Cookies theo nen tang" > CDP must launch the browser with the
profile the user picked beforehand, and must refuse instead of silently reading
whatever Chromium calls "Default".

Everything is mocked: no browser, no CDP socket, no network, no real profile dir.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from infrastructure.downloader import cookie_extractor as ce

_CE = "infrastructure.downloader.cookie_extractor"


class _SyncThread:
    """Run the worker inline so the test sees its calls."""

    def __init__(self, target, **kw):
        self._t = target

    def start(self):
        self._t()


def _panel(profile: str):
    """A NetworkPanel stand-in holding only what the extract handlers touch."""
    from ui.tabs.settings import network_panel as np_mod

    cfg = MagicMock()
    cfg.config_path = Path("/nonexistent/config.json")
    cfg.get_cookie_for_platform.return_value = ""
    combo = MagicMock()
    combo.currentData.return_value = profile
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
    return np_mod, fake, cfg


# -- BUG-CDP-NO-PROFILE: per-platform CDP without a resolved profile ----------


def test_platform_cdp_refuses_when_no_profile_is_resolved():
    # An empty profile used to reach extract_via_cdp, which launched
    # --profile-directory=Default: the cookies of a different account.
    np_mod, fake, cfg = _panel("")
    cdp = MagicMock(return_value=(1, None))
    with (
        patch(f"{_CE}.extract_via_cdp", cdp),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: fn()),
    ):
        np_mod.NetworkPanel._extract_platform_cdp(fake, "instagram", MagicMock())
    cdp.assert_not_called()
    cfg.set_cookie_for_platform.assert_not_called()
    assert fake._app.toast.call_args[0][1] == "error"


def test_platform_cdp_keeps_buttons_usable_after_refusing():
    # The refusal happens before the buttons are disabled, so a user can pick a
    # profile and press CDP again without restarting the app.
    np_mod, fake, cfg = _panel("")
    btn = fake._pc_extract_btns[0]
    with (
        patch(f"{_CE}.extract_via_cdp", MagicMock(return_value=(1, None))),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: fn()),
    ):
        np_mod.NetworkPanel._extract_platform_cdp(fake, "instagram", MagicMock())
    btn.setEnabled.assert_not_called()


def test_platform_cdp_still_runs_with_a_resolved_profile():
    np_mod, fake, cfg = _panel("Profile 2")
    calls: list = []

    def fake_extract(*a, **kw):
        calls.append(kw)
        return 1, None

    with (
        patch(f"{_CE}.extract_via_cdp", side_effect=fake_extract),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: None),
    ):
        np_mod.NetworkPanel._extract_platform_cdp(fake, "instagram", MagicMock())
    assert calls and calls[0]["profile"] == "Profile 2"


# -- the other cookie modes keep their behaviour -----------------------------


def test_platform_ytdlp_button_still_runs_without_a_profile():
    # yt-dlp reads the cookie DB directly; no browser is launched, so an empty
    # profile keeps meaning "whatever the browser calls default".
    np_mod, fake, cfg = _panel("")
    calls: list = []

    def fake_extract(*a, **kw):
        calls.append(kw)
        return 1, None

    with (
        patch(f"{_CE}.extract_browser_cookies", side_effect=fake_extract),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: None),
    ):
        np_mod.NetworkPanel._extract_platform_cookie(fake, "instagram", MagicMock())
    assert calls and calls[0]["profile"] is None


def test_global_cdp_button_is_unchanged_without_a_profile():
    # Global Cookie is a different section and keeps the engine default.
    np_mod, fake, cfg = _panel("")
    calls: list = []

    def fake_extract(*a, **kw):
        calls.append(kw)
        return 1, None

    with (
        patch(f"{_CE}.extract_via_cdp", side_effect=fake_extract),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: None),
        patch.object(np_mod.QMessageBox, "question", return_value=np_mod.QMessageBox.StandardButton.Yes),
    ):
        np_mod.NetworkPanel._extract_global_cdp(fake)
    assert calls and calls[0]["profile"] is None


def test_tiktok_add_form_cdp_is_unchanged_without_a_profile(tmp_path):
    # The TikTok account pool must keep working for accounts added before the
    # profile picker existed.
    np_mod, fake, cfg = _panel("")
    fake._cookies_dir = lambda: tmp_path
    fake._set_add_form_busy = MagicMock()
    fake._set_add_form_status = MagicMock()
    fake._tt_form_gen = 0
    calls: list = []

    def fake_extract(*a, **kw):
        calls.append(kw)
        return 1, None

    with (
        patch(f"{_CE}.extract_via_cdp", side_effect=fake_extract),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: None),
    ):
        np_mod.NetworkPanel._start_add_form_extraction(fake, "cdp", "brave", "")
    assert calls and calls[0]["profile"] is None


# -- BUG-COOKIE-OKRU: every listed platform needs a domain list --------------


def test_every_settings_platform_has_cookie_domains():
    # A key missing from _PLATFORM_DOMAINS filters every cookie away, so the
    # platform always failed with "no cookies for this platform".
    from ui.tabs.settings.network_panel import _PC_PLATFORMS

    for key, _label in _PC_PLATFORMS:
        assert ce._PLATFORM_DOMAINS.get(key), f"{key} has no cookie domains"


# -- domain filtering --------------------------------------------------------


@pytest.fixture
def user_data(tmp_path):
    """Fake Brave user-data-dir: Default = "Personal", Profile 2 = "A1"."""
    root = tmp_path / "User Data"
    for d in ("Default", "Profile 2"):
        (root / d / "Network").mkdir(parents=True)
        (root / d / "Network" / "Cookies").write_bytes(b"")
    (root / "Local State").write_text(
        json.dumps(
            {"profile": {"info_cache": {"Default": {"name": "Personal"}, "Profile 2": {"name": "A1"}}}}
        ),
        encoding="utf-8",
    )
    return root


def _cookie(domain: str, name: str = "sessionid"):
    return {
        "domain": domain,
        "path": "/",
        "secure": True,
        "expires": 9999999999,
        "name": name,
        "value": "[REDACTED]",
    }


def _run_cdp(tmp_path, user_data, platform_key, cookies, profile="Profile 2"):
    written: list[str] = []
    conn = MagicMock()
    conn.getresponse.return_value.status = 500  # port free

    def fake_write(self, text, **kw):
        written.append(text)

    out = tmp_path / "out.txt"
    with (
        patch(f"{_CE}._find_browser_exe", return_value=tmp_path / "brave.exe"),
        patch(f"{_CE}._find_browser_profile", return_value=user_data),
        patch(f"{_CE}._is_browser_running", return_value=False),
        patch("http.client.HTTPConnection", return_value=conn),
        patch(f"{_CE}._cdp_wait_ready", return_value=True),
        patch(f"{_CE}._cdp_get_page_ws_url", return_value="ws://localhost:9/devtools/page/x"),
        patch(f"{_CE}._cdp_ws_connect", return_value=MagicMock()),
        patch(f"{_CE}._cdp_get_all_cookies", MagicMock(return_value=list(cookies))),
        patch("infrastructure.downloader.cookie_storage.encrypt_cookie_file", side_effect=lambda p: p),
        patch("subprocess.Popen", return_value=MagicMock()),
        patch.object(Path, "write_text", fake_write),
    ):
        count, error = ce.extract_via_cdp(out, platform_key=platform_key, browser="brave", profile=profile)
    return count, error, written


def test_cdp_keeps_only_the_selected_platform_domain(tmp_path, user_data):
    cookies = [
        _cookie(".instagram.com"),
        _cookie(".tiktok.com"),
        _cookie("notinstagram.com"),  # lookalike must not pass the suffix test
    ]
    count, error, written = _run_cdp(tmp_path, user_data, "instagram", cookies)
    assert error is None and count == 1
    assert ".instagram.com" in written[0]
    assert "tiktok.com" not in written[0]
    assert "notinstagram.com" not in written[0]


def test_cdp_extracts_ok_ru_cookies(tmp_path, user_data):
    count, error, written = _run_cdp(tmp_path, user_data, "ok_ru", [_cookie(".ok.ru")])
    assert error is None and count == 1
    assert ".ok.ru" in written[0]


def test_cdp_reports_when_the_platform_has_no_cookies(tmp_path, user_data):
    count, error, written = _run_cdp(tmp_path, user_data, "instagram", [_cookie(".tiktok.com")])
    assert count == 0 and error
    assert written == []
