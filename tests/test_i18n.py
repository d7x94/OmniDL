"""
tests/test_i18n.py
Coverage for the multi-language feature (English / Vietnamese / Chinese).

1. utils.i18n  — catalogue completeness, normalisation, fallback, formatting.
2. ConfigManager.language — validation of a hand-edited / stale config.json.
3. api/server.py GET+POST /api/settings/language — round-trip and rejection.
4. api/static/index.html — every data-i18n key exists in all three JS catalogues.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import api.server as srv
from api.models import LanguageRequest
from infrastructure.config.config_manager import ConfigManager
from utils import i18n
from utils.translations import CATALOG

_INDEX_HTML = Path(__file__).parent.parent / "api" / "static" / "index.html"


@pytest.fixture(autouse=True)
def _restore_language():
    """Language is process-global — put it back so test order cannot matter."""
    before = i18n.get_language()
    yield
    i18n.set_language(before)


# ── 1. Catalogue ──────────────────────────────────────────────────────────


def test_every_language_has_the_same_keys():
    reference = set(CATALOG["vi"])
    for code, table in CATALOG.items():
        assert set(table) == reference, f"{code} key set differs from vi"


def test_catalogue_covers_every_supported_language():
    assert set(CATALOG) == set(i18n.LANGUAGES)


def test_placeholders_match_across_languages():
    """A {placeholder} present in vi must exist in every translation."""
    pattern = re.compile(r"\{(\w+)\}")
    for key, vi_text in CATALOG["vi"].items():
        expected = set(pattern.findall(vi_text))
        for code, table in CATALOG.items():
            assert set(pattern.findall(table[key])) == expected, f"{code}:{key}"


def test_no_empty_translations():
    for code, table in CATALOG.items():
        for key, text in table.items():
            assert text.strip(), f"{code}:{key} is empty"


# ── 2. Lookup ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("en", "en"),
        ("vi", "vi"),
        ("zh", "zh"),
        ("EN", "en"),
        ("en-US", "en"),
        ("zh_CN", "zh"),
        ("  vi  ", "vi"),
        ("fr", "en"),  # unsupported → default
        ("", "en"),
        (None, "en"),
        (123, "en"),
    ],
)
def test_normalize(raw, expected):
    assert i18n.normalize(raw) == expected


def test_set_language_returns_resolved_code():
    assert i18n.set_language("zh-Hans") == "zh"
    assert i18n.get_language() == "zh"


def test_translation_switches_with_language():
    i18n.set_language("en")
    assert i18n.t("nav.settings") == "Settings"
    i18n.set_language("vi")
    assert i18n.t("nav.settings") == "Cài đặt"
    i18n.set_language("zh")
    assert i18n.t("nav.settings") == "设置"


def test_missing_key_returns_the_key():
    i18n.set_language("en")
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_format_arguments_are_applied():
    i18n.set_language("en")
    assert i18n.t("queue.clear_selected", count=3) == "Clear selected (3)"


def test_format_failure_falls_back_to_raw_text():
    """A missing kwarg must not raise into the UI thread."""
    i18n.set_language("en")
    assert "{count}" in i18n.t("queue.clear_selected", wrong=1)


def test_available_lists_every_language():
    codes = [opt["code"] for opt in i18n.available()]
    assert codes == list(i18n.LANGUAGES)
    assert all(opt["label"] for opt in i18n.available())


def test_listeners_fire_once_per_actual_change():
    seen: list[str] = []
    i18n.set_language("en")
    i18n.register(seen.append)
    try:
        i18n.set_language("vi")
        i18n.set_language("vi")  # no change → no callback
        i18n.set_language("zh")
    finally:
        i18n.unregister(seen.append)
    assert seen == ["vi", "zh"]


def test_broken_listener_does_not_block_the_switch():
    def _boom(_code):
        raise RuntimeError("listener blew up")

    i18n.set_language("en")
    i18n.register(_boom)
    try:
        assert i18n.set_language("zh") == "zh"
        assert i18n.get_language() == "zh"
    finally:
        i18n.unregister(_boom)


# ── 3. Config persistence ─────────────────────────────────────────────────


def test_config_language_defaults_and_round_trips(tmp_path):
    ConfigManager._cache.clear()
    cfg = ConfigManager(tmp_path / "config.json")
    assert cfg.language == "en"

    cfg.set("language", "zh")
    assert cfg.language == "zh"

    cfg.save()
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["language"] == "zh"


def test_config_language_rejects_unsupported_value(tmp_path):
    """A hand-edited config.json must not push an unknown code into the UI."""
    ConfigManager._cache.clear()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"language": "klingon"}), encoding="utf-8")
    assert ConfigManager(path).language == "en"


# ── 4. API ────────────────────────────────────────────────────────────────


def _endpoint(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _make_app(tmp_path):
    ConfigManager._cache.clear()
    config = ConfigManager(tmp_path / "config.json")
    config.set("api_token", "")
    service = SimpleNamespace(get_all_tasks=lambda: [], get_history=lambda: [])
    return srv.create_app(service, config), config  # type: ignore[arg-type]


def test_get_language_returns_current_and_available(tmp_path):
    app, config = _make_app(tmp_path)
    config.set("language", "vi")
    result = asyncio.run(_endpoint(app, "/api/settings/language", "GET")(None))
    assert result.language == "vi"
    assert [o.code for o in result.available] == list(i18n.LANGUAGES)


def test_post_language_persists_and_activates(tmp_path):
    app, config = _make_app(tmp_path)
    result = asyncio.run(
        _endpoint(app, "/api/settings/language", "POST")(LanguageRequest(language="zh"), None)
    )
    assert result.language == "zh"
    assert config.language == "zh"
    assert i18n.get_language() == "zh"


def test_post_language_normalises_locale_codes(tmp_path):
    app, config = _make_app(tmp_path)
    asyncio.run(_endpoint(app, "/api/settings/language", "POST")(LanguageRequest(language="en-GB"), None))
    assert config.language == "en"


@pytest.mark.parametrize("bad", ["klingon", "", "de", "../../etc/passwd"])
def test_language_request_rejects_unsupported_codes(bad):
    with pytest.raises(ValidationError):
        LanguageRequest(language=bad)


def test_get_language_falls_back_for_a_corrupt_stored_value(tmp_path):
    app, config = _make_app(tmp_path)
    config.set("language", "klingon")
    result = asyncio.run(_endpoint(app, "/api/settings/language", "GET")(None))
    assert result.language == "en"


# ── 5. Web UI catalogue ───────────────────────────────────────────────────


def _js_catalogue_keys() -> dict[str, set[str]]:
    """Parse the per-language key sets out of the I18N object in index.html."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    body = html[html.index("const I18N = {") : html.index("\nlet uiLang =")]
    out: dict[str, set[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        header = re.fullmatch(r"(en|vi|zh):\s*\{", stripped)
        if header:
            current = header.group(1)
            out[current] = set()
            continue
        entry = re.match(r"'([^']+)':", stripped)
        if entry and current:
            out[current].add(entry.group(1))
    return out


def test_web_catalogues_share_one_key_set():
    keys = _js_catalogue_keys()
    assert set(keys) == {"en", "vi", "zh"}
    assert keys["en"] == keys["vi"] == keys["zh"]
    assert len(keys["en"]) > 50


def test_every_data_i18n_attribute_has_a_translation():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    keys = _js_catalogue_keys()
    used = set(re.findall(r'data-i18n(?:-html|-ph)?="([^"]+)"', html))
    assert used, "markup carries no data-i18n attributes"
    missing = used - keys["en"]
    assert not missing, f"untranslated markup keys: {sorted(missing)}"


def test_dynamic_translation_calls_have_a_translation():
    """Every T('key') used by the render functions must exist in the catalogue."""
    html = _INDEX_HTML.read_text(encoding="utf-8")
    keys = _js_catalogue_keys()
    used = set(re.findall(r"\bT\('([^']+)'", html))
    missing = used - keys["en"]
    assert not missing, f"untranslated T() keys: {sorted(missing)}"
