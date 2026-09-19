"""Tests for the 2026-08 TikTok Accounts Pool usability work.

Covers the three things that made adding an account painful or wrong:
  - one browser could only ever yield one account (no profile selector)
  - cookies that are not signed in / expired / duplicated were accepted
  - accounts could not be renamed or refreshed, and deleting one leaked its file
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from infrastructure.downloader import cookie_extractor as ce
from infrastructure.downloader.account_pool import TikTokAccount, inspect_tiktok_cookie
from ui.tabs.settings.network_panel import NetworkPanel, _browser_family_supported_by_cdp
from utils.i18n import t

_NET_SRC = Path("ui/tabs/settings/network_panel.py").read_text(encoding="utf-8")


def _write_cookies(path: Path, entries: "list[tuple[str, str, int]]") -> Path:
    lines = ["# Netscape HTTP Cookie File", ""]
    for name, value, expiry in entries:
        lines.append(f".tiktok.com\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_FUTURE = int(time.time()) + 86400 * 30
_PAST = int(time.time()) - 86400


# ── cookie health ────────────────────────────────────────────────────────────


def test_missing_file_is_reported_as_missing(tmp_path):
    assert inspect_tiktok_cookie(str(tmp_path / "nope.txt")).status == "missing"
    assert inspect_tiktok_cookie("").status == "missing"


def test_anonymous_jar_is_not_treated_as_a_login(tmp_path):
    """ttwid/tt_csrf_token exist when logged out — the old code accepted these."""
    f = _write_cookies(tmp_path / "c.txt", [("ttwid", "x", _FUTURE), ("tt_csrf_token", "y", _FUTURE)])
    health = inspect_tiktok_cookie(str(f))
    assert health.status == "not_logged_in"
    assert health.ok is False
    assert health.count == 2


def test_signed_in_jar_is_ok_and_fingerprinted(tmp_path):
    f = _write_cookies(tmp_path / "c.txt", [("ttwid", "x", _FUTURE), ("sessionid", "SECRET", _FUTURE)])
    health = inspect_tiktok_cookie(str(f))
    assert health.ok
    assert health.expires_at == _FUTURE
    assert health.fingerprint and "SECRET" not in health.fingerprint


def test_expired_session_is_rejected(tmp_path):
    f = _write_cookies(tmp_path / "c.txt", [("sessionid", "SECRET", _PAST)])
    assert inspect_tiktok_cookie(str(f)).status == "expired"


def test_session_cookie_without_expiry_is_still_ok(tmp_path):
    f = _write_cookies(tmp_path / "c.txt", [("sessionid", "SECRET", 0)])
    health = inspect_tiktok_cookie(str(f))
    assert health.ok and health.expires_at == 0


def test_different_logins_get_different_fingerprints(tmp_path):
    a = inspect_tiktok_cookie(str(_write_cookies(tmp_path / "a.txt", [("sessionid", "AAA", _FUTURE)])))
    b = inspect_tiktok_cookie(str(_write_cookies(tmp_path / "b.txt", [("sessionid", "BBB", _FUTURE)])))
    c = inspect_tiktok_cookie(str(_write_cookies(tmp_path / "c.txt", [("sessionid", "AAA", _FUTURE)])))
    assert a.fingerprint != b.fingerprint
    assert a.fingerprint == c.fingerprint  # same login added twice


def test_garbage_file_is_unreadable(tmp_path):
    f = tmp_path / "junk.txt"
    f.write_text("not a cookie file at all\n", encoding="utf-8")
    assert inspect_tiktok_cookie(str(f)).status == "unreadable"


# ── browser family dispatch ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "browser,family",
    [
        ("brave", "brave"),
        ("Brave", "brave"),
        ("edge", "edge"),
        ("chromium", "chromium"),
        ("chrome", "chrome"),
        ("firefox", "chrome"),  # unknown falls back, callers gate on CDP support
    ],
)
def test_browser_family_mapping(browser, family):
    assert ce._browser_family(browser) == family


def test_edge_no_longer_resolves_to_the_chrome_executable():
    """Picking Edge used to launch Chrome and read Chrome's cookies."""
    assert ce._EXE_CANDIDATES["edge"] is not ce._CHROME_EXE_CANDIDATES
    assert all("msedge.exe" in c for c in ce._EXE_CANDIDATES["edge"])
    assert ce._PROCESS_NAMES["edge"] == "msedge.exe"
    assert "Microsoft" in ce._PROFILE_CANDIDATES["edge"][0]


def test_cdp_support_gate():
    assert _browser_family_supported_by_cdp("brave")
    assert _browser_family_supported_by_cdp("edge")
    assert not _browser_family_supported_by_cdp("firefox")
    assert not _browser_family_supported_by_cdp("opera")


# ── profile discovery ────────────────────────────────────────────────────────


def _fake_user_data(tmp_path: Path) -> Path:
    root = tmp_path / "User Data"
    for name in ("Default", "Profile 1", "Profile 2", "Profile 9", "System Profile"):
        (root / name).mkdir(parents=True)
    for name in ("Default", "Profile 1", "Profile 9"):
        (root / name / "Cookies").write_bytes(b"x")
    (root / "Profile 2" / "Network").mkdir()
    (root / "Profile 2" / "Network" / "Cookies").write_bytes(b"x")
    (root / "Local State").write_text(
        '{"profile": {"info_cache": {'
        '"Default": {"name": "Person 1", "user_name": "a@gmail.com"},'
        '"Profile 1": {"name": "Work"},'
        '"Profile 2": {"name": "b@gmail.com", "user_name": "b@gmail.com"}'
        "}}}",
        encoding="utf-8",
    )
    return root


def test_list_browser_profiles_returns_human_names_in_order(tmp_path, monkeypatch):
    root = _fake_user_data(tmp_path)
    monkeypatch.setattr(ce, "_find_browser_profile", lambda b: root)

    profiles = ce.list_browser_profiles("chrome")

    assert [p[0] for p in profiles] == ["Default", "Profile 1", "Profile 2", "Profile 9"]
    assert profiles[0][1] == "Person 1 (a@gmail.com)"
    assert profiles[1][1] == "Work"
    assert profiles[2][1] == "b@gmail.com"  # name == user_name, not doubled
    assert profiles[3][1] == "Profile 9"  # absent from Local State


def test_list_browser_profiles_skips_never_used_profiles(tmp_path, monkeypatch):
    root = _fake_user_data(tmp_path)
    monkeypatch.setattr(ce, "_find_browser_profile", lambda b: root)
    assert "System Profile" not in [p[0] for p in ce.list_browser_profiles("chrome")]


def test_list_browser_profiles_survives_a_broken_local_state(tmp_path, monkeypatch):
    root = _fake_user_data(tmp_path)
    (root / "Local State").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ce, "_find_browser_profile", lambda b: root)
    assert [p[1] for p in ce.list_browser_profiles("chrome")] == [
        "Default",
        "Profile 1",
        "Profile 2",
        "Profile 9",
    ]


def test_list_browser_profiles_empty_when_browser_absent(monkeypatch):
    monkeypatch.setattr(ce, "_find_browser_profile", lambda b: None)
    assert ce.list_browser_profiles("chrome") == []


# ── the profile actually reaches the extractors ──────────────────────────────


def test_ytdlp_extraction_passes_the_profile_through(tmp_path, monkeypatch):
    seen = {}

    class _FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", SimpleNamespace(YoutubeDL=_FakeYDL))
    ce.extract_browser_cookies("chrome", tmp_path / "out.txt", profile="Profile 1")

    assert seen["cookiesfrombrowser"] == ("chrome", "Profile 1", None, None)


def test_cdp_launch_selects_the_requested_profile():
    assert "f\"--profile-directory={profile or 'Default'}\"" in Path(
        "infrastructure/downloader/cookie_extractor.py"
    ).read_text(encoding="utf-8")


# ── account model carries its source ─────────────────────────────────────────


def test_account_round_trips_browser_profile_and_fingerprint():
    acc = TikTokAccount(
        name="A", cookie_file="c.txt", browser="brave", profile="Profile 1", session_fp="deadbeef"
    )
    back = TikTokAccount.from_dict(acc.to_dict())
    assert (back.browser, back.profile, back.session_fp) == ("brave", "Profile 1", "deadbeef")


def test_account_from_legacy_dict_defaults_to_empty_source():
    acc = TikTokAccount.from_dict({"name": "old", "cookie_file": "c.txt"})
    assert acc.browser == "" and acc.profile == "" and acc.session_fp == ""


# ── add-form validation ──────────────────────────────────────────────────────


def _form_panel(pool=None, name=""):
    stored = list(pool or [])
    cfg = SimpleNamespace(tiktok_account_pool=stored)
    panel = SimpleNamespace(
        _tt_add_pending_cookie="",
        _tt_add_pending_browser="",
        _tt_add_pending_profile="",
        _tt_add_pending_fp="",
        _tt_add_name=SimpleNamespace(text=lambda: name, setText=MagicMock()),
        _tt_add_save_btn=SimpleNamespace(setEnabled=MagicMock()),
        _set_add_form_status=MagicMock(),
        # The form now deletes a rejected jar instead of stranding it in the
        # cookies folder, so the stub has to answer that call too.
        _delete_pool_cookie_file=MagicMock(),
        _app=SimpleNamespace(config=cfg, toast=MagicMock()),
    )
    panel._duplicate_account_name = lambda fp, exclude_id="": NetworkPanel._duplicate_account_name(
        panel, fp, exclude_id
    )
    panel._suggest_account_name = lambda b, p: NetworkPanel._suggest_account_name(panel, b, p)
    return panel


def test_form_rejects_a_jar_that_is_not_signed_in(tmp_path):
    panel = _form_panel()
    f = _write_cookies(tmp_path / "c.txt", [("ttwid", "x", _FUTURE)])

    NetworkPanel._accept_cookie_for_form(panel, str(f), "brave", "Default")

    assert panel._tt_add_pending_cookie == ""
    panel._tt_add_save_btn.setEnabled.assert_called_with(False)
    assert panel._set_add_form_status.call_args[0][0] == t("settings.network.reject_not_logged_in")


def test_form_rejects_the_same_login_twice(tmp_path):
    f = _write_cookies(tmp_path / "c.txt", [("sessionid", "SAME", _FUTURE)])
    fp = inspect_tiktok_cookie(str(f)).fingerprint
    panel = _form_panel(pool=[{"id": "a1", "name": "Acc 1", "session_fp": fp}])

    NetworkPanel._accept_cookie_for_form(panel, str(f), "brave", "Default")

    assert panel._tt_add_pending_cookie == ""
    panel._tt_add_save_btn.setEnabled.assert_called_with(False)


def test_form_accepts_a_good_jar_and_records_its_source(tmp_path):
    panel = _form_panel()
    f = _write_cookies(tmp_path / "c.txt", [("sessionid", "OK", _FUTURE)])

    NetworkPanel._accept_cookie_for_form(panel, str(f), "brave", "Profile 1")

    assert panel._tt_add_pending_cookie == str(f)
    assert panel._tt_add_pending_browser == "brave"
    assert panel._tt_add_pending_profile == "Profile 1"
    assert panel._tt_add_pending_fp
    panel._tt_add_save_btn.setEnabled.assert_called_with(True)
    panel._tt_add_name.setText.assert_called_once_with("Brave Profile 1")


def test_form_does_not_overwrite_a_name_the_user_typed(tmp_path):
    panel = _form_panel(name="My account")
    f = _write_cookies(tmp_path / "c.txt", [("sessionid", "OK", _FUTURE)])

    NetworkPanel._accept_cookie_for_form(panel, str(f), "brave", "Default")

    panel._tt_add_name.setText.assert_not_called()


def test_suggested_names_stay_unique():
    panel = _form_panel(pool=[{"name": "Brave Default"}, {"name": "Brave Default 2"}])
    assert NetworkPanel._suggest_account_name(panel, "brave", "Default") == "Brave Default 3"


# ── save / rename / delete ───────────────────────────────────────────────────


def _pool_panel(stored):
    cfg = SimpleNamespace(
        tiktok_account_pool=stored,
        set_tiktok_account_pool=lambda pool: (stored.clear(), stored.extend(pool)),
        config_path=Path("/tmp/omnidl/config.json"),  # nosec B108
    )
    panel = SimpleNamespace(
        _tt_add_name=SimpleNamespace(text=lambda: "dup"),
        _tt_add_slots=SimpleNamespace(value=lambda: 3),
        _tt_add_pending_cookie="/x/cookies.txt",
        _tt_add_pending_browser="brave",
        _tt_add_pending_profile="Default",
        _tt_add_pending_fp="fp",
        _app=SimpleNamespace(config=cfg, toast=MagicMock()),
        _hide_tiktok_add_form=MagicMock(),
        _refresh_tiktok_accounts_list=MagicMock(),
        _rebuild_pool=MagicMock(),
        _delete_pool_cookie_file=MagicMock(),
    )
    panel._duplicate_account_name = lambda fp, exclude_id="": NetworkPanel._duplicate_account_name(
        panel, fp, exclude_id
    )
    panel._suggest_account_name = lambda b, p: NetworkPanel._suggest_account_name(panel, b, p)
    return panel


def test_saving_a_duplicate_name_is_refused():
    panel = _pool_panel([{"id": "a", "name": "Dup"}])

    NetworkPanel._save_new_tiktok_account(panel)

    assert panel._app.toast.call_args[0][1] == "error"
    assert len(panel._app.config.tiktok_account_pool) == 1


def test_saving_keeps_the_slot_count_chosen_in_the_form():
    stored: list[dict] = []
    panel = _pool_panel(stored)

    NetworkPanel._save_new_tiktok_account(panel)

    assert stored[0]["max_slots"] == 3
    assert stored[0]["browser"] == "brave"
    assert stored[0]["session_fp"] == "fp"


def test_renaming_persists_and_rebuilds_the_pool():
    stored = [{"id": "a", "name": "Old"}]
    panel = _pool_panel(stored)

    NetworkPanel._rename_tiktok_account(panel, "a", "  New  ")

    assert stored[0]["name"] == "New"
    panel._rebuild_pool.assert_called_once()


def test_renaming_to_an_existing_name_is_refused():
    stored = [{"id": "a", "name": "One"}, {"id": "b", "name": "Two"}]
    panel = _pool_panel(stored)

    NetworkPanel._rename_tiktok_account(panel, "a", "Two")

    assert stored[0]["name"] == "One"
    assert panel._app.toast.call_args[0][1] == "error"


def test_empty_rename_is_ignored():
    stored = [{"id": "a", "name": "One"}]
    panel = _pool_panel(stored)

    NetworkPanel._rename_tiktok_account(panel, "a", "   ")

    assert stored[0]["name"] == "One"
    panel._rebuild_pool.assert_not_called()


def test_removing_an_account_deletes_its_cookie_file():
    stored = [{"id": "a", "name": "One", "cookie_file": "/x/cookies/a.enc"}]
    panel = _pool_panel(stored)

    NetworkPanel._remove_tiktok_account(panel, "a")

    assert stored == []
    panel._delete_pool_cookie_file.assert_called_once_with("/x/cookies/a.enc")


def test_delete_pool_cookie_file_refuses_paths_outside_the_cookies_dir(tmp_path):
    safe = tmp_path / "cookies"
    safe.mkdir()
    inside = safe / "keep.txt"
    inside.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    panel = SimpleNamespace(
        _app=SimpleNamespace(config=SimpleNamespace(config_path=tmp_path / "config.json"))
    )

    NetworkPanel._delete_pool_cookie_file(panel, str(outside))
    assert outside.exists()

    NetworkPanel._delete_pool_cookie_file(panel, str(inside))
    assert not inside.exists()


# ── refresh ──────────────────────────────────────────────────────────────────


def test_refresh_needs_a_recorded_browser():
    panel = _pool_panel([{"id": "a", "name": "Manual", "cookie_file": "/x/c.enc"}])

    NetworkPanel._refresh_tiktok_account_cookie(panel, "a")

    assert panel._app.toast.call_args[0][1] == "error"


def test_refresh_swaps_the_cookie_file_and_drops_the_old_one(tmp_path):
    old = _write_cookies(tmp_path / "old.txt", [("sessionid", "OLD", _FUTURE)])
    new = _write_cookies(tmp_path / "new.txt", [("sessionid", "NEW", _FUTURE)])
    stored = [{"id": "a", "name": "Acc", "cookie_file": str(old), "session_fp": "old-fp"}]
    panel = _pool_panel(stored)

    NetworkPanel._apply_refreshed_cookie(panel, "a", str(new), "")

    assert stored[0]["cookie_file"] == str(new)
    assert stored[0]["session_fp"] == inspect_tiktok_cookie(str(new)).fingerprint
    panel._delete_pool_cookie_file.assert_called_once_with(str(old))
    assert panel._app.toast.call_args[0][1] == "success"


def test_refresh_that_returns_a_logged_out_jar_keeps_the_old_cookie(tmp_path):
    old = _write_cookies(tmp_path / "old.txt", [("sessionid", "OLD", _FUTURE)])
    new = _write_cookies(tmp_path / "new.txt", [("ttwid", "x", _FUTURE)])
    stored = [{"id": "a", "name": "Acc", "cookie_file": str(old), "session_fp": "old-fp"}]
    panel = _pool_panel(stored)

    NetworkPanel._apply_refreshed_cookie(panel, "a", str(new), "")

    assert stored[0]["cookie_file"] == str(old)
    panel._delete_pool_cookie_file.assert_called_once_with(str(new))
    assert panel._app.toast.call_args[0][1] == "error"


def test_refresh_onto_another_pool_account_is_refused(tmp_path):
    new = _write_cookies(tmp_path / "new.txt", [("sessionid", "SHARED", _FUTURE)])
    fp = inspect_tiktok_cookie(str(new)).fingerprint
    stored = [
        {"id": "a", "name": "A", "cookie_file": "/x/a.enc", "session_fp": "a-fp"},
        {"id": "b", "name": "B", "cookie_file": "/x/b.enc", "session_fp": fp},
    ]
    panel = _pool_panel(stored)

    NetworkPanel._apply_refreshed_cookie(panel, "a", str(new), "")

    assert stored[0]["cookie_file"] == "/x/a.enc"
    assert panel._app.toast.call_args[0][1] == "error"


def test_refresh_error_leaves_the_account_untouched(tmp_path):
    stored = [{"id": "a", "name": "Acc", "cookie_file": "/x/a.enc", "session_fp": "fp"}]
    panel = _pool_panel(stored)

    NetworkPanel._apply_refreshed_cookie(panel, "a", "/x/new.enc", "browser is running")

    assert stored[0]["cookie_file"] == "/x/a.enc"
    panel._delete_pool_cookie_file.assert_called_once_with("/x/new.enc")


def test_refresh_result_for_a_deleted_account_is_discarded(tmp_path):
    panel = _pool_panel([])

    NetworkPanel._apply_refreshed_cookie(panel, "gone", "/x/new.enc", "")

    panel._delete_pool_cookie_file.assert_called_once_with("/x/new.enc")
    panel._app.toast.assert_not_called()


# ── stale extraction results cannot leak into a reopened form ────────────────


def test_extraction_worker_is_guarded_by_a_form_generation():
    assert "gen = self._tt_form_gen" in _NET_SRC
    assert "if gen != self._tt_form_gen:" in _NET_SRC
    assert _NET_SRC.count("self._tt_form_gen += 1") == 2


# ── i18n ─────────────────────────────────────────────────────────────────────


def test_every_new_key_exists_in_every_language():
    from utils.translations import CATALOG

    keys = [
        "settings.network.profile_label",
        "settings.network.profile_default",
        "settings.network.profile_tip",
        "settings.network.slots_label",
        "settings.network.source_manual",
        "settings.network.cookie_ready",
        "settings.network.reject_missing",
        "settings.network.reject_unreadable",
        "settings.network.reject_not_logged_in",
        "settings.network.reject_expired",
        "settings.network.duplicate_account",
        "settings.network.name_in_use",
        "settings.network.rename_tip",
        "settings.network.refresh_btn",
        "settings.network.refresh_tip",
        "settings.network.refresh_no_source",
        "settings.network.refreshing",
        "settings.network.account_refreshed",
        "settings.network.status_ok",
        "settings.network.status_ok_session",
        "settings.network.status_paused",
        "settings.network.status_missing",
        "settings.network.status_unreadable",
        "settings.network.status_not_logged_in",
        "settings.network.status_expired",
        "settings.network.pool_hint",
    ]
    for lang, catalogue in CATALOG.items():
        for key in keys:
            assert key in catalogue, f"{key} missing in {lang}"


def test_every_cookie_health_status_has_a_reject_and_status_string():
    from utils.translations import CATALOG

    for status in ("missing", "unreadable", "not_logged_in", "expired"):
        for lang, catalogue in CATALOG.items():
            assert f"settings.network.reject_{status}" in catalogue, (status, lang)
            assert f"settings.network.status_{status}" in catalogue, (status, lang)
