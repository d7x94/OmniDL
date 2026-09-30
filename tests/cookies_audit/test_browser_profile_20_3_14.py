"""BUG-COOKIE-PROFILE-DL (v20.3.14): download-time "cookies from browser" must use
the profile picked in Settings, not whatever profile yt-dlp / gallery-dl find first."""

from unittest.mock import patch

import infrastructure.downloader.yt_dlp_engine as mod
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.config.config_manager import ConfigManager


def _config(tmp_path, profile="Profile 2", browser="chrome"):
    cfg = ConfigManager(tmp_path / "config.json")
    cfg.set("use_cookies", True)
    cfg.set("cookies_browser", browser)
    cfg.set("cookies_profile", profile)
    cfg.set("download_dir", str(tmp_path / "dl"))
    return cfg


class _FakeYDL:
    captured: list = []

    def __init__(self, opts):
        _FakeYDL.captured.append(dict(opts))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add_post_processor(self, pp, when=None):
        pass

    def download(self, urls):
        pass

    def extract_info(self, url, download=False):
        return {"id": "abc", "title": "t", "duration": 5, "formats": [], "extractor_key": "Youtube"}


def _run_ytdlp(tmp_path, cfg, call):
    _FakeYDL.captured = []
    with patch.object(mod.yt_dlp, "YoutubeDL", _FakeYDL):
        engine = mod.YtDlpEngine(cfg)
        try:
            call(engine)
        except Exception:
            pass
    return [o["cookiesfrombrowser"] for o in _FakeYDL.captured if "cookiesfrombrowser" in o]


def test_ytdlp_download_passes_selected_profile(tmp_path):
    cfg = _config(tmp_path)
    url = "https://www.youtube.com/watch?v=abc"
    task = DownloadTask(url=url, format_id="best", output_ext="mp4")
    task.media_info = MediaInfo(url=url, title="t")
    specs = _run_ytdlp(tmp_path, cfg, lambda e: e.download(task))
    assert specs, "download() never set cookiesfrombrowser"
    assert all(s[:2] == ("chrome", "Profile 2") for s in specs), specs


def test_ytdlp_extract_info_passes_selected_profile(tmp_path):
    cfg = _config(tmp_path)
    specs = _run_ytdlp(tmp_path, cfg, lambda e: e.extract_info("https://www.youtube.com/watch?v=abc"))
    assert specs, "extract_info() never set cookiesfrombrowser"
    assert all(s[:2] == ("chrome", "Profile 2") for s in specs), specs


def test_ytdlp_without_profile_keeps_browser_default(tmp_path):
    cfg = _config(tmp_path, profile="")
    specs = _run_ytdlp(tmp_path, cfg, lambda e: e.extract_info("https://www.youtube.com/watch?v=abc"))
    assert specs and all(s[0] == "chrome" and (len(s) < 2 or s[1] is None) for s in specs), specs
