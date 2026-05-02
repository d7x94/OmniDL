"""
tests/test_clipboard_monitor.py
Unit tests for utils/clipboard_monitor.py
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from utils.clipboard_monitor import ClipboardMonitor, _extract_url


# ── _extract_url ──────────────────────────────────────────────────────────────

def test_extract_plain_url():
    assert _extract_url("https://www.youtube.com/watch?v=abc123") == "https://www.youtube.com/watch?v=abc123"


def test_extract_url_from_share_text():
    assert _extract_url("Watch this video https://v.kuaishou.com/xyz123 cool!") == "https://v.kuaishou.com/xyz123"


def test_extract_url_strips_trailing_junk():
    assert _extract_url("https://example.com/path,") == "https://example.com/path"
    assert _extract_url("https://example.com/path)") == "https://example.com/path"


def test_extract_url_none_on_no_url():
    assert _extract_url("no url here at all") is None
    assert _extract_url("") is None


def test_extract_url_http():
    assert _extract_url("http://example.com/video") == "http://example.com/video"


# ── ClipboardMonitor ──────────────────────────────────────────────────────────

def _make_monitor(clipboard_values, callback=None):
    """Create a monitor that reads from a list of clipboard values sequentially."""
    idx = [0]

    def _get():
        v = clipboard_values[min(idx[0], len(clipboard_values) - 1)]
        idx[0] += 1
        return v

    cb = callback or MagicMock()
    return ClipboardMonitor(get_clipboard=_get, on_new_url=cb), cb


def test_monitor_fires_on_new_url():
    fired = []
    monitor = ClipboardMonitor(
        get_clipboard=lambda: "https://www.youtube.com/watch?v=test1",
        on_new_url=lambda u: fired.append(u),
    )
    monitor.start()
    time.sleep(0.05)
    monitor.stop()
    time.sleep(0.05)
    assert fired == ["https://www.youtube.com/watch?v=test1"]


def test_monitor_does_not_fire_twice_for_same_url():
    fired = []
    monitor = ClipboardMonitor(
        get_clipboard=lambda: "https://www.youtube.com/watch?v=same",
        on_new_url=lambda u: fired.append(u),
    )
    monitor.start()
    time.sleep(0.05)
    monitor.stop()
    time.sleep(0.05)
    assert len(fired) == 1


def test_monitor_fires_again_after_reset():
    fired = []
    monitor = ClipboardMonitor(
        get_clipboard=lambda: "https://www.youtube.com/watch?v=same",
        on_new_url=lambda u: fired.append(u),
    )
    monitor.start()
    time.sleep(0.05)
    monitor.stop()
    time.sleep(0.05)
    assert len(fired) == 1

    monitor.reset_last()
    monitor.start()
    time.sleep(0.05)
    monitor.stop()
    time.sleep(0.05)
    assert len(fired) == 2


def test_monitor_ignores_non_url_clipboard():
    fired = []
    monitor = ClipboardMonitor(
        get_clipboard=lambda: "just some text, no link",
        on_new_url=lambda u: fired.append(u),
    )
    monitor.start()
    time.sleep(0.05)
    monitor.stop()
    time.sleep(0.05)
    assert fired == []


def test_monitor_stop_is_idempotent():
    monitor = ClipboardMonitor(
        get_clipboard=lambda: "",
        on_new_url=MagicMock(),
    )
    monitor.stop()
    monitor.stop()  # should not raise


def test_monitor_start_is_idempotent():
    monitor = ClipboardMonitor(
        get_clipboard=lambda: "",
        on_new_url=MagicMock(),
    )
    monitor.start()
    monitor.start()  # second call should no-op
    monitor.stop()


# ── Config key ────────────────────────────────────────────────────────────────

def test_config_clipboard_monitor_default(tmp_path):
    from infrastructure.config.config_manager import ConfigManager
    cfg = ConfigManager(tmp_path / "config.json")
    assert cfg.clipboard_monitor_enabled is False


def test_config_clipboard_monitor_set(tmp_path):
    from infrastructure.config.config_manager import ConfigManager
    cfg = ConfigManager(tmp_path / "config2.json")
    cfg.set("clipboard_monitor_enabled", True)
    assert cfg.clipboard_monitor_enabled is True


# ── API model ─────────────────────────────────────────────────────────────────

def test_clipboard_analyse_request_valid():
    from api.models import ClipboardAnalyseRequest
    req = ClipboardAnalyseRequest(url="https://www.youtube.com/watch?v=abc")
    assert req.url == "https://www.youtube.com/watch?v=abc"


def test_clipboard_analyse_request_extracts_from_share_text():
    from api.models import ClipboardAnalyseRequest
    req = ClipboardAnalyseRequest(url="check out https://tiktok.com/@user/video/123 now")
    assert req.url == "https://tiktok.com/@user/video/123"


def test_clipboard_analyse_request_rejects_no_url():
    from api.models import ClipboardAnalyseRequest
    import pydantic
    with pytest.raises((pydantic.ValidationError, ValueError)):
        ClipboardAnalyseRequest(url="not a url at all")
