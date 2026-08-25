"""Regression tests for the Settings tab audit fixes.

Uses the unbound-method pattern (SimpleNamespace as self) so no QApplication
or display server is required, matching tests/test_archive_tab.py.

Covers:
  - _haystack / _on_search: searching matches setting rows, not just section
    titles (searching "proxy" used to hide every section and blank the page)
  - _on_search: the no-results label appears only when nothing matched
  - _on_search: a section collapsed by the user stays collapsed once the
    search box is cleared
  - NetworkPanel._on_proxy_focusout: a valid proxy restores the field's
    stylesheet instead of clearing it
  - RemoteApiPanel._on_api_toggle: a failed server start turns the switch and
    api_enabled back off instead of persisting a broken enabled state
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ui.tabs.settings_tab import SettingsTab


class _Lbl:
    """Widget stub exposing the text getters _haystack scrapes."""

    def __init__(self, text: str = "", placeholder: str = "", tooltip: str = "") -> None:
        self._t, self._p, self._tt = text, placeholder, tooltip

    def text(self) -> str:
        return self._t

    def placeholderText(self) -> str:
        return self._p

    def toolTip(self) -> str:
        return self._tt


class _Content:
    """Section content stub: holds children and dynamic properties."""

    def __init__(self, children) -> None:
        self._children = children
        self._props: dict = {}

    def findChildren(self, _type):
        return self._children

    def property(self, name):
        return self._props.get(name)

    def setProperty(self, name, value) -> None:
        self._props[name] = value


class _Wrapper:
    def __init__(self) -> None:
        self.visible = True

    def setVisible(self, v: bool) -> None:
        self.visible = bool(v)


def _section(title: str, children, cfg_key: str):
    """Build one (_sections) tuple plus a recorder for set_expanded calls."""
    wrapper = _Wrapper()
    content = _Content(children)
    state = {"expanded": True}

    def set_expanded(expanded: bool, persist: bool = False) -> None:
        state["expanded"] = bool(expanded)

    return (title.lower(), cfg_key, wrapper, content, set_expanded), state


def _tab(sections, collapsed_keys=()):
    """SimpleNamespace standing in for a built SettingsTab."""
    cfg = SimpleNamespace(get=lambda k, d=False: k in collapsed_keys)
    panel = SimpleNamespace(_sections=[s for s, _ in sections], _app=SimpleNamespace(config=cfg))
    empty = SimpleNamespace(_sections=[], _app=SimpleNamespace(config=cfg))
    no_results = _Wrapper()
    return SimpleNamespace(
        _general_panel=panel,
        _network_panel=empty,
        _tools_panel=empty,
        _taildrop_panel=empty,
        _api_panel=empty,
        _no_results=no_results,
        _haystack=SettingsTab._haystack,
    )


# ── search ───────────────────────────────────────────────────────────────────


def test_search_matches_setting_row_not_only_section_title():
    net, _ = _section(
        "Network & Authentication", [_Lbl(text="Proxy"), _Lbl(placeholder="http://host:port")], "k1"
    )
    gen, _ = _section("Appearance", [_Lbl(text="Theme")], "k2")
    tab = _tab([(net, None), (gen, None)])

    SettingsTab._on_search(tab, "proxy")

    assert net[2].visible is True
    assert gen[2].visible is False
    assert tab._no_results.visible is False


def test_search_matches_tooltip_text():
    sec, _ = _section("Per-Platform Cookies", [_Lbl(text="CDP", tooltip="Extract via Chrome DevTools")], "k1")
    tab = _tab([(sec, None)])

    SettingsTab._on_search(tab, "devtools")

    assert sec[2].visible is True


def test_search_expands_matching_section():
    sec, state = _section("Appearance", [_Lbl(text="Theme")], "k1")
    state["expanded"] = False
    tab = _tab([(sec, state)])

    SettingsTab._on_search(tab, "theme")

    assert state["expanded"] is True


def test_search_with_no_match_shows_no_results_label():
    sec, _ = _section("Appearance", [_Lbl(text="Theme")], "k1")
    tab = _tab([(sec, None)])

    SettingsTab._on_search(tab, "zzzznope")

    assert sec[2].visible is False
    assert tab._no_results.visible is True


def test_cleared_search_restores_collapsed_state_from_config():
    sec, state = _section("Appearance", [_Lbl(text="Theme")], "k1")
    tab = _tab([(sec, state)], collapsed_keys={"k1"})

    SettingsTab._on_search(tab, "")

    assert sec[2].visible is True
    assert state["expanded"] is False
    assert tab._no_results.visible is False


def test_haystack_is_cached_on_the_content_widget():
    content = _Content([_Lbl(text="Theme")])

    first = SettingsTab._haystack(content, "Appearance")
    content._children = [_Lbl(text="Something else")]
    second = SettingsTab._haystack(content, "Appearance")

    assert "theme" in first
    assert second == first


# ── proxy field styling ──────────────────────────────────────────────────────


def test_valid_proxy_restores_input_stylesheet():
    from ui.tabs.settings.network_panel import NetworkPanel

    entry = MagicMock()
    entry.text.return_value = "socks5://127.0.0.1:1080"
    panel = SimpleNamespace(
        _proxy_entry=entry,
        _app=SimpleNamespace(config=MagicMock(), toast=MagicMock()),
    )

    NetworkPanel._on_proxy_focusout(panel)

    panel._app.config.set.assert_called_once_with("proxy", "socks5://127.0.0.1:1080")
    applied = entry.setStyleSheet.call_args[0][0]
    assert "QLineEdit" in applied and applied != ""


# ── API toggle rollback ──────────────────────────────────────────────────────


def test_failed_api_start_turns_the_switch_back_off():
    import api.server as api_server
    from ui.tabs.settings.remote_api_panel import RemoteApiPanel

    cfg = MagicMock(api_enabled=False)
    switch = MagicMock()
    panel = SimpleNamespace(
        _api_switch=switch,
        _app=SimpleNamespace(config=cfg, toast=MagicMock(), _service=object()),
        _refresh_api_status_label=MagicMock(),
    )

    original = api_server.start_api_server
    api_server.start_api_server = MagicMock(side_effect=RuntimeError("port busy"))
    api_server.is_api_running = MagicMock(return_value=False)
    try:
        RemoteApiPanel._on_api_toggle(panel, True)
    finally:
        api_server.start_api_server = original

    switch.setChecked.assert_called_once_with(False)
    assert ("api_enabled", False) in [c[0] for c in cfg.set.call_args_list]
