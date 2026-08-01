"""
tests/test_config_manager.py
Unit tests for infrastructure/config/config_manager.py

Issue fixed: #10 (HIGH) — zero tests in the entire codebase.
"""

import json
import sys
import threading
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_config(tmp_path, overrides=None):
    from infrastructure.config.config_manager import ConfigManager

    cfg_file = tmp_path / "config.json"
    if overrides:
        cfg_file.write_text(json.dumps(overrides), encoding="utf-8")
    return ConfigManager(cfg_file)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestConfigManagerDefaults:
    def test_default_download_dir_is_not_empty(self, tmp_path):
        """download_dir must resolve to a real path even if config is absent."""
        cfg = make_config(tmp_path)
        assert cfg.download_dir != Path("")
        assert cfg.download_dir.is_absolute()

    def test_null_download_dir_returns_default(self, tmp_path):
        """JSON null for download_dir should fall back to the OS default."""
        cfg = make_config(tmp_path, {"download_dir": None})
        assert cfg.download_dir != Path("")
        assert cfg.download_dir.is_absolute()

    def test_empty_string_download_dir_returns_default(self, tmp_path):
        """Empty-string download_dir (the original bug) must not resolve to CWD."""
        cfg = make_config(tmp_path, {"download_dir": ""})
        assert cfg.download_dir != Path("")
        assert cfg.download_dir.is_absolute()

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix absolute paths only")
    def test_explicit_download_dir_is_honoured(self, tmp_path):
        cfg = make_config(tmp_path, {"download_dir": "/tmp/omnidl_test"})  # nosec B108
        assert cfg.download_dir == Path("/tmp/omnidl_test")  # nosec B108

    def test_max_concurrent_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.max_concurrent == 3

    def test_max_retries_default(self, tmp_path):
        cfg = make_config(tmp_path)
        assert cfg.max_retries == 3


class TestConfigManagerPersistence:
    def test_set_persists_to_disk(self, tmp_path):
        cfg = make_config(tmp_path)
        cfg.set("max_concurrent", 7)
        # Re-load from the same file
        from infrastructure.config.config_manager import ConfigManager

        cfg2 = ConfigManager(tmp_path / "config.json")
        assert cfg2.max_concurrent == 7

    def test_update_persists_multiple_keys(self, tmp_path):
        cfg = make_config(tmp_path)
        cfg.update({"max_concurrent": 5, "max_retries": 2})
        from infrastructure.config.config_manager import ConfigManager

        cfg2 = ConfigManager(tmp_path / "config.json")
        assert cfg2.max_concurrent == 5
        assert cfg2.max_retries == 2

    def test_save_does_not_raise(self, tmp_path):
        cfg = make_config(tmp_path)
        cfg.save()  # should not raise


class TestConfigManagerPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions only")
    def test_saved_config_is_not_group_or_world_readable(self, tmp_path):
        """M1: config.json (may hold the plaintext API bearer token) must not
        be readable by other users on the machine."""
        cfg = make_config(tmp_path)
        cfg.set("max_concurrent", 5)
        mode = (tmp_path / "config.json").stat().st_mode
        assert mode & 0o077 == 0


class TestConfigManagerThreadSafety:
    def test_concurrent_sets_do_not_corrupt(self, tmp_path):
        cfg = make_config(tmp_path)
        errors = []

        def writer(value):
            try:
                cfg.set("max_concurrent", value)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert 1 <= cfg.max_concurrent <= 19
