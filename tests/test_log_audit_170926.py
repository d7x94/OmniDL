"""Regression guards for the 2026-09-17 log audit.

Evidence: C:\\Users\\PC120125\\AppData\\Local\\OmniDL\\Logs\\omnidl_debug.log
(session 06:00:47 - 12:51:46) and the INFO sink omnidl.log beside it.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from unittest.mock import MagicMock

import infrastructure.downloader.yt_dlp_engine as eng


def _config(tmp_path: Path, cookie: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.config_path = tmp_path / "config.json"
    cfg.cookie_file = ""
    cfg.get_cookie_for_platform.return_value = str(cookie)
    return cfg


def test_cookie_resolution_does_not_log_at_info(tmp_path, caplog):
    """omnidl.log was 20,484 of 21,141 lines of "Using tiktok cookie: ...".

    _resolve_cookie runs once per live-monitor poll (7 accounts x 60 s for
    ~7 h in the audited session).  At INFO it consumed the whole 5 MB x 3
    rotation budget of omnidl.log and evicted every real diagnostic.
    """
    cookie = tmp_path / "tiktok_cookies.txt"
    cookie.write_bytes(b"# Netscape HTTP Cookie File\n")

    with caplog.at_level(logging.DEBUG):
        resolved = eng._resolve_cookie("https://www.tiktok.com/@x/live", _config(tmp_path, cookie))

    assert resolved == str(cookie.resolve())
    matching = [r for r in caplog.records if "Using tiktok cookie" in r.getMessage()]
    assert matching, "resolution should still be traceable in the debug log"
    assert all(r.levelno == logging.DEBUG for r in matching)


def test_yt_dlp_opts_use_color_not_deprecated_no_color():
    """'Overwriting params from "color" with "no_color"' fired 6x this session.

    YoutubeDL does `self.params = params` and then writes
    params["color"] = "no_color" into the caller's dict, so any retry reusing
    or shallow-copying those opts carried both keys.  The warning was appended
    to params["_warnings"], a list that {**opts} shares by reference, so it was
    replayed on every further retry.
    """
    src = inspect.getsource(eng)
    assert '"no_color": True' not in src
    assert src.count('"color": "no_color"') >= 4
