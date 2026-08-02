"""
tests/test_config_manager_extra.py
Additional tests for infrastructure/config/config_manager.py

Fills coverage gaps -- typed property accessors (lines 116-157) and
download_dir fallback when value is empty string.
"""

import json
from pathlib import Path

from infrastructure.config.config_manager import ConfigManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_config(tmp_path: Path, data: dict = None) -> ConfigManager:  # type: ignore[assignment]
    path = tmp_path / "config.json"
    if data is not None:
        path.write_text(json.dumps(data), encoding="utf-8")
    return ConfigManager(path)


# ---------------------------------------------------------------------------
# download_dir fallback
# ---------------------------------------------------------------------------


class TestDownloadDirFallback:
    def test_empty_string_returns_default(self, tmp_path):
        cfg = make_config(tmp_path, {"download_dir": ""})
        # Should return the OS default, not Path("")
        assert cfg.download_dir != Path("")
        assert cfg.download_dir.is_absolute()

    def test_null_returns_default(self, tmp_path):
        cfg = make_config(tmp_path, {"download_dir": None})
        assert cfg.download_dir != Path("")

    def test_valid_path_returned(self, tmp_path):
        cfg = make_config(tmp_path, {"download_dir": str(tmp_path)})
        assert cfg.download_dir == tmp_path


# ---------------------------------------------------------------------------
# Typed accessors
# ---------------------------------------------------------------------------


class TestTypedAccessors:
    def test_theme_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.theme, str)

    def test_theme_custom(self, tmp_path):
        cfg = make_config(tmp_path, {"theme": "light"})
        assert cfg.theme == "light"

    def test_max_concurrent_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.max_concurrent, int)
        assert cfg.max_concurrent > 0

    def test_max_concurrent_custom(self, tmp_path):
        cfg = make_config(tmp_path, {"max_concurrent": 5})
        assert cfg.max_concurrent == 5

    def test_max_retries_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.max_retries, int)

    def test_max_retries_custom(self, tmp_path):
        cfg = make_config(tmp_path, {"max_retries": 7})
        assert cfg.max_retries == 7

    def test_proxy_default_empty(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.proxy == ""

    def test_proxy_custom(self, tmp_path):
        cfg = make_config(tmp_path, {"proxy": "http://proxy:8080"})
        assert cfg.proxy == "http://proxy:8080"

    def test_use_cookies_default_false(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.use_cookies is False

    def test_use_cookies_custom_true(self, tmp_path):
        cfg = make_config(tmp_path, {"use_cookies": True})
        assert cfg.use_cookies is True

    def test_cookies_browser_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.cookies_browser, str)

    def test_embed_thumbnail_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.embed_thumbnail, bool)

    def test_embed_metadata_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.embed_metadata, bool)

    def test_default_quality_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert "best" in cfg.default_quality.lower()

    def test_default_format_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert isinstance(cfg.default_format, str)

    def test_history_limit_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.history_limit > 0

    def test_extra_args_default_empty(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.extra_args == ""

    def test_extra_args_custom(self, tmp_path):
        cfg = make_config(tmp_path, {"extra_args": "--ratelimit 500K"})
        assert cfg.extra_args == "--ratelimit 500K"

    def test_cookie_file_default_empty(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.cookie_file == ""

    def test_cookie_file_custom(self, tmp_path):
        cfg = make_config(tmp_path, {"cookie_file": "/home/user/cookies.txt"})
        assert cfg.cookie_file == "/home/user/cookies.txt"

    def test_set_and_get_roundtrip(self, tmp_path):
        cfg = make_config(tmp_path)
        cfg.set("proxy", "http://new-proxy:3128")
        assert cfg.proxy == "http://new-proxy:3128"

    def test_update_multiple_keys(self, tmp_path):
        cfg = make_config(tmp_path)
        cfg.update({"max_concurrent": 8, "max_retries": 5})
        assert cfg.max_concurrent == 8
        assert cfg.max_retries == 5
