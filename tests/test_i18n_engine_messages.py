"""
tests/test_i18n_engine_messages.py
Guards the engine/service half of the multi-language feature.

Before this, download engines raised hard-coded Vietnamese text and
download_manager decided "retry or not" by substring-matching that text — so a
user on English/Chinese saw Vietnamese errors, and translating them would have
silently broken the retry and gallery-dl fallback paths.

1. Engine errors carry a stable ``err.*`` key and follow the active language.
2. Retry / fallback classification reads the key, not the text.
3. No user-facing Vietnamese literal creeps back into the engine layer.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from infrastructure.downloader.download_manager import DownloadManager
from infrastructure.downloader.yt_dlp_engine import (
    _check_unsupported_url,
    _error_key,
    _friendly_error,
    _friendly_exc,
    _keyed_exc,
)
from utils import i18n
from utils.translations import CATALOG

_ROOT = Path(__file__).parent.parent

# Modules that raise or report text the user reads in the UI / Web API.
_ENGINE_MODULES = (
    "app/services/download_service.py",
    "app/services/live_monitor_service.py",
    "infrastructure/downloader/cookie_extractor.py",
    "infrastructure/downloader/facebook_story_engine.py",
    "infrastructure/downloader/gallery_dl_engine.py",
    "infrastructure/downloader/instagram_cdn_engine.py",
    "infrastructure/downloader/kuaishou_engine.py",
    "infrastructure/downloader/waaw_engine.py",
    "infrastructure/downloader/yt_dlp_engine.py",
    "utils/instagram_live_checker.py",
    "utils/tiktok_live_checker.py",
)

_VIETNAMESE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]"
)

# "Tiếng Việt" is the native language label; it must stay untranslated.
_VIETNAMESE_ALLOWED = {"Tiếng Việt"}


@pytest.fixture(autouse=True)
def _restore_language():
    before = i18n.get_language()
    yield
    i18n.set_language(before)


# ── 1. Errors follow the active language ──────────────────────────────────
@pytest.mark.parametrize(
    "raw, key",
    [
        ("Video is private", "err.private"),
        ("HTTP Error 404: Not Found", "err.not_found"),
        ("There is no video in this post", "err.ig_photo_only"),
        ("The channel is not currently live", "err.not_currently_live"),
        ("ffmpeg exited with code 1", "err.ffmpeg_livestream"),
    ],
)
def test_error_key_is_language_independent(raw, key):
    for code in CATALOG:
        i18n.set_language(code)
        assert _error_key(raw) == key


def test_friendly_error_follows_the_active_language():
    i18n.set_language("en")
    english = _friendly_error("Video is private")
    i18n.set_language("vi")
    vietnamese = _friendly_error("Video is private")
    i18n.set_language("zh")
    chinese = _friendly_error("Video is private")
    assert english == CATALOG["en"]["err.private"]
    assert vietnamese == CATALOG["vi"]["err.private"]
    assert chinese == CATALOG["zh"]["err.private"]
    assert len({english, vietnamese, chinese}) == 3


def test_unknown_error_falls_through_unchanged():
    assert _error_key("something entirely unexpected") is None
    assert _friendly_error("something entirely unexpected") == "something entirely unexpected"


def test_blocked_url_message_follows_the_active_language():
    url = "https://www.instagram.com/stories/someone/123/"
    i18n.set_language("en")
    assert _check_unsupported_url(url) == CATALOG["en"]["err.ig_stories_cookies"]
    i18n.set_language("zh")
    assert _check_unsupported_url(url) == CATALOG["zh"]["err.ig_stories_cookies"]
    # A cookie file is configured — the URL is allowed through to yt-dlp.
    assert _check_unsupported_url(url, has_cookies=True) is None


# ── 2. Retry classification reads the key ─────────────────────────────────
def test_raised_errors_carry_their_key():
    exc = _friendly_exc("Video is private")
    assert exc.error_key == "err.private"
    keyed = _keyed_exc("err.playlist_failed", err="boom")
    assert keyed.error_key == "err.playlist_failed"
    assert "boom" in str(keyed)


def test_hard_error_keys_are_real_catalogue_keys():
    assert DownloadManager._HARD_ERROR_KEYS
    for key in DownloadManager._HARD_ERROR_KEYS:
        assert key in CATALOG["vi"], key


def test_hard_errors_are_recognised_in_every_language():
    """The retry decision must not depend on the UI language."""
    for code in CATALOG:
        i18n.set_language(code)
        exc = _friendly_exc("Video is private")
        assert exc.error_key in DownloadManager._HARD_ERROR_KEYS


def test_photo_only_error_keeps_its_key_in_every_language():
    for code in CATALOG:
        i18n.set_language(code)
        exc = _friendly_exc("ERROR: There is no video in this post")
        assert exc.error_key == "err.ig_photo_only"


# ── 3. No Vietnamese literals left in the engine layer ────────────────────
def _translatable_literals(path: Path):
    """String constants in *path* that are not docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings or node.value in _VIETNAMESE_ALLOWED:
                continue
            yield node.lineno, node.value


@pytest.mark.parametrize("rel", _ENGINE_MODULES)
def test_engine_modules_have_no_hardcoded_vietnamese(rel):
    path = _ROOT / rel
    offenders = [
        f"{rel}:{lineno}: {value[:60]!r}"
        for lineno, value in _translatable_literals(path)
        # The injected browser script is JS source, not UI text.
        if _VIETNAMESE.search(value) and "function()" not in value
    ]
    assert not offenders, "hard-coded Vietnamese must go through t():\n" + "\n".join(offenders)


def test_every_key_used_by_the_engine_layer_exists():
    pattern = re.compile(r'\bt\(\s*(["\'])([\w.]+)\1')
    missing = set()
    for rel in _ENGINE_MODULES:
        source = (_ROOT / rel).read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            if match.group(2) not in CATALOG["vi"]:
                missing.add(f"{rel}: {match.group(2)}")
    assert not missing, missing
