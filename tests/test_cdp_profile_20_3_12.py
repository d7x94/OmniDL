"""Regression tests for BUG-CDP-PROFILE (v20.3.12): per-platform CDP read the wrong Brave profile.

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
_IG_COOKIE = [
    {
        "domain": ".instagram.com",
        "path": "/",
        "secure": True,
        "expires": 9999999999,
        "name": "sessionid",
        "value": "[REDACTED]",
    },
]


@pytest.fixture
def user_data(tmp_path):
    """Fake Brave user-data-dir: Default = "Personal", Profile 2 = "A1"."""
    root = tmp_path / "User Data"
    for d in ("Default", "Profile 2"):
        (root / d / "Network").mkdir(parents=True)
        (root / d / "Network" / "Cookies").write_bytes(b"")
    (root / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Personal"},
                        "Profile 2": {"name": "A1"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return root


def _run_cdp(tmp_path, user_data, profile, *, port_busy=False, running=False):
    launched: list[list[str]] = []
    get_cookies = MagicMock(return_value=list(_IG_COOKIE))

    def fake_popen(cmd, **kw):
        launched.append([str(a) for a in cmd])
        return MagicMock()

    conn = MagicMock()
    conn.getresponse.return_value.status = 200 if port_busy else 500
    with (
        patch(f"{_CE}._find_browser_exe", return_value=tmp_path / "brave.exe"),
        patch(f"{_CE}._find_browser_profile", return_value=user_data),
        patch(f"{_CE}._is_browser_running", return_value=running),
        patch("http.client.HTTPConnection", return_value=conn),
        patch(f"{_CE}._cdp_wait_ready", return_value=True),
        patch(f"{_CE}._cdp_get_page_ws_url", return_value="ws://localhost:9/devtools/page/x"),
        patch(f"{_CE}._cdp_ws_connect", return_value=MagicMock()),
        patch(f"{_CE}._cdp_get_all_cookies", get_cookies),
        patch("infrastructure.downloader.cookie_storage.encrypt_cookie_file", side_effect=lambda p: p),
        patch("subprocess.Popen", side_effect=fake_popen),
    ):
        count, error = ce.extract_via_cdp(
            tmp_path / "ig.txt", platform_key="instagram", browser="brave", profile=profile
        )
    return count, error, launched, get_cookies


def test_selected_profile_is_launched_never_default(tmp_path, user_data):
    count, error, launched, _ = _run_cdp(tmp_path, user_data, "Profile 2")
    assert error is None and count == 1
    assert "--profile-directory=Profile 2" in launched[0]
    assert "--profile-directory=Default" not in launched[0]


@pytest.mark.parametrize("bad", ["A1", "Personal", "Profile 9", "../Default", "Profile 2/..", "Default\\.."])
def test_unknown_or_display_name_profile_is_rejected(tmp_path, user_data, bad):
    count, error, launched, get_cookies = _run_cdp(tmp_path, user_data, bad)
    assert count == 0
    assert error and bad in error
    assert launched == []
    get_cookies.assert_not_called()


def test_running_debug_session_is_never_reused(tmp_path, user_data):
    # Something already answers /json/version on the port: it may be a Brave
    # session of another profile, so its cookies must not be read.
    count, error, launched, get_cookies = _run_cdp(tmp_path, user_data, "Profile 2", port_busy=True)
    assert count == 0 and error
    assert launched == []
    get_cookies.assert_not_called()


def test_running_browser_is_refused(tmp_path, user_data):
    count, error, launched, get_cookies = _run_cdp(tmp_path, user_data, "Profile 2", running=True)
    assert count == 0 and error
    assert launched == []
    get_cookies.assert_not_called()


def test_no_profile_keeps_default_behaviour(tmp_path, user_data):
    count, error, launched, _ = _run_cdp(tmp_path, user_data, None)
    assert error is None and count == 1
    assert "--profile-directory=Default" in launched[0]


def test_profile_list_keeps_dir_name_for_duplicate_display_names(tmp_path, user_data, monkeypatch):
    (user_data / "Profile 3" / "Network").mkdir(parents=True)
    (user_data / "Profile 3" / "Network" / "Cookies").write_bytes(b"")
    state = json.loads((user_data / "Local State").read_text(encoding="utf-8"))
    state["profile"]["info_cache"]["Profile 3"] = {"name": "A1"}
    (user_data / "Local State").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(ce, "_find_browser_profile", lambda b: user_data)
    profiles = ce.list_browser_profiles("brave")
    assert ("Profile 2", "A1") in profiles and ("Profile 3", "A1") in profiles


# ── config ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Profile 2", "Profile 2"),
        ("Default", "Default"),
        ("", ""),
        ("A1", ""),
        ("../x", ""),
        ("Profile 2/..", ""),
        (None, ""),
    ],
)
def test_config_cookies_profile_is_validated(value, expected):
    from infrastructure.config.config_manager import ConfigManager

    fake = SimpleNamespace(get=lambda k, d=None: value if k == "cookies_profile" else d)
    assert ConfigManager.cookies_profile.fget(fake) == expected


# ── settings panel wiring ────────────────────────────────────────────────────


def _panel(profile: str, calls: list):
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
        _pc_extract_btns=[],
        _pc_browse_btns=[],
        _pc_clear_btns=[],
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


class _SyncThread:
    def __init__(self, target, **kw):
        self._t = target

    def start(self):
        self._t()


@pytest.mark.parametrize(
    "method,extractor",
    [
        ("_extract_platform_cdp", "extract_via_cdp"),
        ("_extract_platform_cookie", "extract_browser_cookies"),
    ],
)
def test_platform_buttons_pass_selected_profile(method, extractor):
    calls: list = []
    np_mod, fake, cfg = _panel("Profile 2", calls)

    def fake_extract(*a, **kw):
        calls.append(kw)
        return 1, None

    with (
        patch(f"{_CE}.{extractor}", side_effect=fake_extract),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: None),
    ):
        getattr(np_mod.NetworkPanel, method)(fake, "instagram", MagicMock())
    assert calls and calls[0]["profile"] == "Profile 2"


@pytest.mark.parametrize(
    "method,extractor",
    [
        ("_extract_global_cdp", "extract_via_cdp"),
        ("_extract_global_cookies", "extract_browser_cookies"),
    ],
)
def test_global_buttons_pass_selected_profile(method, extractor):
    calls: list = []
    np_mod, fake, cfg = _panel("Profile 2", calls)

    def fake_extract(*a, **kw):
        calls.append(kw)
        return 1, None

    with (
        patch(f"{_CE}.{extractor}", side_effect=fake_extract),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: None),
        patch.object(np_mod.QMessageBox, "question", return_value=np_mod.QMessageBox.StandardButton.Yes),
    ):
        getattr(np_mod.NetworkPanel, method)(fake)
    assert calls and calls[0]["profile"] == "Profile 2"


def test_platform_cdp_error_is_not_saved_as_success():
    # A failed extraction used to re-register the previous run's file (possibly
    # from another profile) and toast success.
    np_mod, fake, cfg = _panel("Profile 2", [])
    with (
        patch(f"{_CE}.extract_via_cdp", return_value=(0, "Profile not found: Profile 2")),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: fn()),
    ):
        np_mod.NetworkPanel._extract_platform_cdp(fake, "instagram", MagicMock())
    cfg.set_cookie_for_platform.assert_not_called()
    assert fake._app.toast.call_args[0][1] == "error"


def test_global_cdp_error_is_not_saved_as_success():
    np_mod, fake, cfg = _panel("Profile 2", [])
    with (
        patch(f"{_CE}.extract_via_cdp", return_value=(0, "Profile not found: Profile 2")),
        patch.object(np_mod.threading, "Thread", _SyncThread),
        patch.object(np_mod.ui_bridge, "post", lambda fn: fn()),
        patch.object(np_mod.QMessageBox, "question", return_value=np_mod.QMessageBox.StandardButton.Yes),
    ):
        np_mod.NetworkPanel._extract_global_cdp(fake)
    cfg.set.assert_not_called()
    assert fake._app.toast.call_args[0][1] == "error"


def test_new_i18n_keys_exist_in_every_language():
    from utils.translations import CATALOG

    for lang in CATALOG.values():
        assert "cookie.err.profile_not_found" in lang
        assert "cookie.err.cdp_port_busy" in lang


@pytest.mark.parametrize("browser", ["firefox", "opera", "safari"])
def test_non_chromium_browser_lists_no_profiles(user_data, monkeypatch, browser):
    # _find_browser_profile maps unknown browsers to Chrome's dir; listing those
    # would send a Chrome profile name to yt-dlp's Firefox/Opera reader.
    monkeypatch.setattr(ce, "_find_browser_profile", lambda b: user_data)
    assert ce.list_browser_profiles(browser) == []
