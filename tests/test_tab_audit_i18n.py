"""Regression guards for the second tab audit (desktop + Web API).

The audit's dominant finding was an incomplete i18n rollout: six UI surfaces
still shipped hard-coded Vietnamese, so an English or Chinese user saw a
mixed-language app. These tests pin the fixes plus the three functional bugs
found alongside them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import i18n
from utils.translations import CATALOG

_ROOT = Path(__file__).parent.parent

# Modules that must not carry a Vietnamese string literal any more — every one
# of them renders text for all three languages.
_MUST_BE_TRANSLATED = [
    "ui/tabs/home_tab.py",
    "ui/components/download_item_widget.py",
    "ui/components/post_download_actions.py",
    "ui/components/status_bar.py",
    "ui/components/command_palette.py",
    "ui/main_window.py",
    "app/services/ffmpeg_convert_service.py",
    "app/services/ffmpeg_trim_service.py",
    "app/services/whisper_subtitle_service.py",
]

_VI_CHARS = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]")

# Endonyms: a language's own name is the same string in every UI language, so
# these are data, not untranslated copy (the language picker shows them as-is).
_ENDONYMS = {"Tiếng Việt"}


@pytest.fixture(autouse=True)
def _restore_language():
    before = i18n.get_language()
    yield
    i18n.set_language(before)


# ── i18n rollout ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", _MUST_BE_TRANSLATED)
def test_no_vietnamese_string_literals_left(rel):
    """A Vietnamese literal here is a string the other two languages never get."""
    tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _VI_CHARS.search(node.value)
        and node.value not in _ENDONYMS
    ]
    assert not offenders, f"{rel} still hard-codes: {offenders}"


@pytest.mark.parametrize(
    "module,cls",
    [
        ("ui.tabs.home_tab", "HomeTab"),
        ("ui.components.download_item_widget", "DownloadItemWidget"),
        ("ui.components.status_bar", "StatusBar"),
        ("ui.tabs.settings_tab", "SettingsTab"),
    ],
)
def test_surface_exposes_retranslate(module, cls):
    """MainWindow.retranslate() fans out by duck-typing on retranslate()."""
    mod = __import__(module, fromlist=[cls])
    assert callable(getattr(getattr(mod, cls), "retranslate", None))


def test_main_window_retranslates_the_status_bar_too():
    src = (_ROOT / "ui/main_window.py").read_text(encoding="utf-8")
    assert "self._status_bar, *self._tabs.values()" in src


def test_queue_tab_retranslates_its_item_widgets():
    """The cards are built once; without this pass they keep the old language."""
    src = (_ROOT / "ui/tabs/queue_tab.py").read_text(encoding="utf-8")
    body = src[src.index("def retranslate") : src.index("def _poll")]
    assert "w.retranslate()" in body


def test_main_window_registers_an_i18n_listener():
    """The Web UI can switch the language from the API thread."""
    src = (_ROOT / "ui/main_window.py").read_text(encoding="utf-8")
    assert "i18n_register(self._on_language_changed)" in src
    assert "ui_bridge.post(self.retranslate)" in src


def test_command_palette_can_reach_every_tab():
    from ui.components.command_palette import COMMANDS
    from ui.main_window import NAV_ITEMS

    palette_keys = {key for _icon, _label, key in COMMANDS}
    nav_keys = {key for key, _icon, _label, _section in NAV_ITEMS}
    assert nav_keys - palette_keys == set(), "Ctrl+K cannot reach every tab"


def test_command_palette_labels_are_catalogue_keys():
    from ui.components.command_palette import COMMANDS

    for _icon, label_key, _key in COMMANDS:
        assert label_key in CATALOG["en"], label_key


def test_convert_cancel_message_follows_the_language():
    """It reaches the iPhone client verbatim as the convert job's error_msg."""
    i18n.set_language("en")
    assert i18n.t("convert.cancelled") == "Cancelled"
    i18n.set_language("vi")
    assert i18n.t("convert.cancelled") == "Đã huỷ"


def test_convert_format_labels_are_catalogue_keys():
    from ui.components.post_download_actions import CONVERT_FORMATS

    for _ext, label_key in CONVERT_FORMATS:
        assert label_key in CATALOG["en"], label_key


def test_web_ui_rerenders_the_file_browser_on_language_switch():
    html = (_ROOT / "api/static/index.html").read_text(encoding="utf-8")
    body = html[html.index("function applyI18n()") : html.index("function buildLangSelects()")]
    assert "renderFileBrowser" in body, "file browser rows keep the old language"


# ── Functional fixes ──────────────────────────────────────────────────────


def _batch_tab_stub():
    """A BatchTab with only the analysis bookkeeping wired up (no QApplication)."""
    from ui.tabs.batch_tab import BatchTab

    tab = BatchTab.__new__(BatchTab)
    tab._items = []
    tab._batch_token = 1
    tab._analysing_count = 0
    tab._spinner_idx = 0
    tab._refresh_item_ui = lambda _item: None
    tab._analyse_next = lambda _token: None
    return tab


def test_removing_an_item_mid_analysis_keeps_the_counter_sane():
    """The stale callback used to drive _analysing_count to -1.

    _tick_spinner only stops at zero, so a negative count left a 100 ms timer
    rescheduling itself for the life of the process.
    """
    from ui.tabs.batch_tab import BatchTab, _BatchItem, _ItemState

    tab = _batch_tab_stub()
    item = _BatchItem(url="https://example.com/v", state=_ItemState.ANALYSING)
    tab._items = [item]
    tab._analysing_count = 1

    # User removes the row while its analysis is still in flight.
    tab._items.remove(item)
    tab._analysing_count -= 1

    # …and the analysis result lands afterwards.
    BatchTab._on_item_error(tab, item, "boom", tab._batch_token)

    assert tab._analysing_count == 0


def test_tick_spinner_stops_on_a_non_positive_count():
    from ui.tabs.batch_tab import BatchTab

    tab = _batch_tab_stub()
    tab._analysing_count = -1
    BatchTab._tick_spinner(tab, tab._batch_token)  # must return, not reschedule
    assert tab._spinner_idx == 0


def test_preview_convert_file_404s_for_a_subtitles_only_job(tmp_path):
    """Path("").resolve() is the CWD, which used to surface as a bogus 403."""
    import asyncio

    from fastapi import HTTPException

    import api.server as srv
    from infrastructure.config.config_manager import ConfigManager

    ConfigManager._cache.clear()
    config = ConfigManager(tmp_path / "config.json")
    config.set("api_token", "")
    config.set("download_dir", str(tmp_path))

    job = SimpleNamespace(status="COMPLETED", output_filename="", subtitle_filename="subs.srt")
    remote = SimpleNamespace(get_job=lambda _jid: job)
    service = SimpleNamespace(get_all_tasks=lambda: [], get_history=lambda: [])
    app = srv.create_app(service, config, remote_convert=remote)  # type: ignore[arg-type]

    endpoint = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/convert/{job_id}/file")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint("job1", "video", None))
    assert exc.value.status_code == 404
