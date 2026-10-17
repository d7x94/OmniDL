"""BUG-COOKIE-PROFILE-DL (v20.3.14): gallery-dl --cookies-from-browser carries the Settings profile."""

from unittest.mock import patch

import infrastructure.downloader.yt_dlp_engine  # noqa: F401  (imported before the I/O guard is armed)
from infrastructure.config.config_manager import ConfigManager
from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine


def _config(tmp_path, profile="Profile 2", browser="chrome"):
    cfg = ConfigManager(tmp_path / "config.json")
    cfg.set("use_cookies", True)
    cfg.set("cookies_browser", browser)
    cfg.set("cookies_profile", profile)
    return cfg


def test_gallery_dl_passes_selected_profile(tmp_path):
    cfg = _config(tmp_path)
    engine = GalleryDlEngine(cfg)
    with patch.object(GalleryDlEngine, "_exe", return_value="gallery-dl"):
        cmd, _tmp = engine._base_cmd(url="https://www.instagram.com/p/abc/")
    i = cmd.index("--cookies-from-browser")
    assert cmd[i + 1] == "chrome:Profile 2"


def test_gallery_dl_without_profile_keeps_browser_only(tmp_path):
    cfg = _config(tmp_path, profile="")
    engine = GalleryDlEngine(cfg)
    with patch.object(GalleryDlEngine, "_exe", return_value="gallery-dl"):
        cmd, _tmp = engine._base_cmd(url="https://www.instagram.com/p/abc/")
    i = cmd.index("--cookies-from-browser")
    assert cmd[i + 1] == "chrome"
