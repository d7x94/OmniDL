"""
tests/test_config_manager_coverage.py
Coverage for config_manager.py missing lines:
 133-134 (_load exception), 152-154 (_save OSError),
 339 (platform_cookies non-dict), 358-363 (set_cookie_for_platform),
 401-418 (api_token keyring hit), 427-449 (set_api_token keyring/fallback),
 455, 459-460, 464 (Tailscale HTTPS accessors), 509 (debug_logging).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from infrastructure.config.config_manager import ConfigManager


def _cfg(tmp_path: Path, data: dict = None) -> ConfigManager:  # type: ignore[assignment]
    p = tmp_path / "config.json"
    if data is not None:
        p.write_text(json.dumps(data), encoding="utf-8")
    return ConfigManager(p)


# ---------------------------------------------------------------------------
# _load exception (lines 133-134)
# ---------------------------------------------------------------------------

class TestLoadException:
    def test_corrupt_json_uses_defaults(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text("{bad json", encoding="utf-8")
        # Should not raise; silently uses defaults
        cfg = ConfigManager(p)
        assert cfg.max_concurrent > 0


# ---------------------------------------------------------------------------
# _save OSError (lines 152-154)
# ---------------------------------------------------------------------------

class TestSaveOSError:
    def test_save_oserror_is_swallowed(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            # Should not raise
            cfg._save()


# ---------------------------------------------------------------------------
# platform_cookies non-dict (line 339)
# ---------------------------------------------------------------------------

class TestPlatformCookiesNonDict:
    def test_non_dict_returns_empty(self, tmp_path):
        cfg = _cfg(tmp_path, {"platform_cookies": "not-a-dict"})
        assert cfg.platform_cookies == {}


# ---------------------------------------------------------------------------
# set_cookie_for_platform (lines 358-363)
# ---------------------------------------------------------------------------

class TestSetCookieForPlatform:
    def test_set_adds_entry(self, tmp_path):
        cfg = _cfg(tmp_path)
        cookie_path = str(tmp_path / "tt.txt")
        cfg.set_cookie_for_platform("tiktok", cookie_path)
        assert cfg.get_cookie_for_platform("tiktok") == cookie_path

    def test_set_empty_removes_entry(self, tmp_path):
        cookie_path = str(tmp_path / "tt.txt")
        cfg = _cfg(tmp_path, {"platform_cookies": {"tiktok": cookie_path}})
        cfg.set_cookie_for_platform("tiktok", "")
        assert cfg.get_cookie_for_platform("tiktok") == ""


# ---------------------------------------------------------------------------
# api_token keyring paths (lines 401-418)
# ---------------------------------------------------------------------------

class TestApiTokenKeyring:
    def test_keyring_hit_returns_stored_token(self, tmp_path):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "secret-token"
        cfg = _cfg(tmp_path)
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            token = cfg.api_token
        assert token == "secret-token"

    def test_keyring_hit_scrubs_plaintext(self, tmp_path):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "secret-token"
        cfg = _cfg(tmp_path, {"api_token": "old-plaintext"})
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            token = cfg.api_token
        assert token == "secret-token"
        # Plaintext should have been scrubbed
        assert cfg.get("api_token", "") == ""

    def test_keyring_unavailable_falls_back_to_config(self, tmp_path):
        cfg = _cfg(tmp_path, {"api_token": "fallback-token"})
        with patch.dict("sys.modules", {"keyring": None}):
            token = cfg.api_token
        assert token == "fallback-token"

    def test_keyring_raises_falls_back_to_config(self, tmp_path):
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = RuntimeError("no secret service")
        cfg = _cfg(tmp_path, {"api_token": "plain"})
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            token = cfg.api_token
        assert token == "plain"


# ---------------------------------------------------------------------------
# set_api_token keyring + fallback (lines 427-449)
# ---------------------------------------------------------------------------

class TestSetApiToken:
    def test_set_via_keyring(self, tmp_path):
        mock_kr = MagicMock()
        cfg = _cfg(tmp_path)
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            cfg.set_api_token("new-token")
        mock_kr.set_password.assert_called_once_with(
            "OmniDL", "api_token_v1", "new-token"
        )

    def test_set_via_keyring_scrubs_plaintext(self, tmp_path):
        mock_kr = MagicMock()
        cfg = _cfg(tmp_path, {"api_token": "old"})
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            cfg.set_api_token("new-token")
        assert cfg.get("api_token", "") == ""

    def test_set_keyring_unavailable_falls_back_to_config(self, tmp_path):
        mock_kr = MagicMock()
        mock_kr.set_password.side_effect = RuntimeError("keyring unavailable")
        cfg = _cfg(tmp_path)
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            cfg.set_api_token("fallback-token")
        assert cfg.get("api_token", "") == "fallback-token"

    def test_set_keyring_none_falls_back_to_config(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.dict("sys.modules", {"keyring": None}):
            cfg.set_api_token("plain-token")
        assert cfg.get("api_token", "") == "plain-token"


# ---------------------------------------------------------------------------
# Tailscale HTTPS accessors (lines 455, 459-460, 464)
# ---------------------------------------------------------------------------

class TestTailscaleHttpsAccessors:
    def test_api_ts_https_enabled_false_by_default(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.api_ts_https_enabled is False

    def test_api_ts_https_enabled_true(self, tmp_path):
        cfg = _cfg(tmp_path, {"api_ts_https_enabled": True})
        assert cfg.api_ts_https_enabled is True

    def test_api_ts_https_internal_port_zero_when_unset(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.api_ts_https_internal_port == 0

    def test_api_ts_https_internal_port_clamped(self, tmp_path):
        cfg = _cfg(tmp_path, {"api_ts_https_internal_port": 60000})
        assert cfg.api_ts_https_internal_port == 60000

    def test_api_ts_https_internal_port_below_min_clamped(self, tmp_path):
        cfg = _cfg(tmp_path, {"api_ts_https_internal_port": 1000})
        assert cfg.api_ts_https_internal_port == 50000

    def test_api_ts_https_dns_name_empty_by_default(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.api_ts_https_dns_name == ""

    def test_api_ts_https_dns_name_stripped(self, tmp_path):
        cfg = _cfg(tmp_path, {"api_ts_https_dns_name": "  myhost.ts.net  "})
        assert cfg.api_ts_https_dns_name == "myhost.ts.net"


# ---------------------------------------------------------------------------
# debug_logging accessor (line 509)
# ---------------------------------------------------------------------------

class TestDebugLogging:
    def test_debug_logging_default_false(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert cfg.debug_logging is False

    def test_debug_logging_true(self, tmp_path):
        cfg = _cfg(tmp_path, {"debug_logging": True})
        assert cfg.debug_logging is True
