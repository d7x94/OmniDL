"""BUG-COOKIE-PROFILE-DL (v20.3.14): the Facebook Story capture launches the
browser on the profile picked in Settings, and refuses a profile that is gone."""

import types

import pytest

import infrastructure.downloader.cookie_extractor as cex
import infrastructure.downloader.facebook_story_engine as eng

pytest.importorskip("playwright.sync_api")

URL = "https://www.facebook.com/stories/122114830586207697/UzpfSVND/"


class _Stop(Exception):
    pass


def _launch(tmp_path, monkeypatch, browser="chrome", cfg_browser="chrome", profile="Profile 2"):
    (tmp_path / "Google" / "Chrome" / "User Data" / "Profile 2").mkdir(parents=True)
    monkeypatch.setattr(eng.sys, "platform", "win32")
    monkeypatch.setattr(eng, "_find_browser_exe", lambda b: "chrome.exe")
    monkeypatch.setattr(cex, "_is_browser_running", lambda b: False)
    monkeypatch.setattr(eng, "_free_port", lambda: 9999)
    seen: list = []

    def _popen(cmd, **kw):
        seen.append(cmd)
        raise _Stop

    monkeypatch.setattr(eng.subprocess, "Popen", _popen)
    cfg = types.SimpleNamespace(
        download_dir=str(tmp_path / "dl"), cookies_browser=cfg_browser, cookies_profile=profile
    )
    return cfg, seen


def test_story_launch_uses_selected_profile(tmp_path, monkeypatch):
    cfg, seen = _launch(tmp_path, monkeypatch)
    with pytest.raises(_Stop):
        eng.download_story(URL, cfg, browser="chrome", timeout=1)
    assert "--profile-directory=Profile 2" in seen[0], seen[0]


def test_story_refuses_missing_profile_before_launch(tmp_path, monkeypatch):
    cfg, seen = _launch(tmp_path, monkeypatch, profile="Profile 7")
    with pytest.raises(RuntimeError, match="Profile 7"):
        eng.download_story(URL, cfg, browser="chrome", timeout=1)
    assert seen == []


def test_story_other_browser_ignores_settings_profile(tmp_path, monkeypatch):
    cfg, seen = _launch(tmp_path, monkeypatch, browser="brave", cfg_browser="chrome")
    with pytest.raises(_Stop):
        eng.download_story(URL, cfg, browser="brave", timeout=1)
    assert not any(a.startswith("--profile-directory") for a in seen[0]), seen[0]


def test_story_without_profile_keeps_old_launch(tmp_path, monkeypatch):
    cfg, seen = _launch(tmp_path, monkeypatch, profile="")
    with pytest.raises(_Stop):
        eng.download_story(URL, cfg, browser="chrome", timeout=1)
    assert not any(a.startswith("--profile-directory") for a in seen[0]), seen[0]


def test_story_edge_settings_profile_not_checked_against_chrome_dir(tmp_path, monkeypatch):
    # The Story engine maps every non-brave browser to Chrome's exe and User Data,
    # so an Edge/Chromium profile name must not be applied (or refused) there.
    cfg, seen = _launch(tmp_path, monkeypatch, cfg_browser="edge", profile="Profile 7")
    with pytest.raises(_Stop):
        eng.download_story(URL, cfg, browser="edge", timeout=1)
    assert not any(a.startswith("--profile-directory") for a in seen[0]), seen[0]
