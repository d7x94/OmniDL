"""Regression tests for the 2026-09-28 platform download audit (v20.3.10) - Instagram."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from app.event_bus import EventBus
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.download_manager import DownloadManager


def _make_mgr(engine, gallery_engine):
    cfg = MagicMock()
    cfg.max_concurrent = 1
    cfg.max_retries = 0
    cfg.proxy = ""
    mgr = DownloadManager(config=cfg, engine=engine, event_bus=EventBus(), gallery_engine=gallery_engine)
    mgr.start()
    return mgr


def _wait_terminal(task, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.status in DownloadStatus.terminal_states():
            return True
        time.sleep(0.05)
    return False


# BUG-BU-SWEEP: the orphan sweep deleted every new file under the shared
# output folder, including files other tasks were writing there.
def test_bug_bu_sweep_keeps_other_tasks_files(tmp_path):
    other = tmp_path / "someone - 2026-09-28 - Video [OTHERID1].mp4"
    other.write_bytes(b"x" * 10)
    other_part = tmp_path / "someone - 2026-09-28 - Clip [OTHERID2].mp4.part"
    other_part.write_bytes(b"x" * 10)
    orphan = tmp_path / "me - 2026-09-28 - Post [MYVID1].fdash-1v.mp4"

    def fake_yt_dl(task, on_progress=None, on_postprocess=None):
        orphan.write_bytes(b"x" * 10)
        getattr(task, "ytdlp_ids", []).append("MYVID1")
        raise RuntimeError("no video formats found")

    def fake_gallery_dl(task, on_progress=None, on_postprocess=None):
        photo = tmp_path / "slug" / "photo.jpg"
        photo.parent.mkdir(exist_ok=True)
        photo.write_bytes(b"photo")
        task.filename = str(photo)
        task.gallery_dl_files = [str(photo)]

    engine = MagicMock()
    engine.download.side_effect = fake_yt_dl
    gallery = MagicMock()
    gallery.download.side_effect = fake_gallery_dl
    mgr = _make_mgr(engine, gallery)
    try:
        task = DownloadTask(url="https://www.instagram.com/p/abc/", output_dir=str(tmp_path))
        task.media_info = MediaInfo(url=task.url, title="Post", source_engine="yt_dlp")
        mgr.enqueue(task)
        assert _wait_terminal(task)
    finally:
        mgr.shutdown(wait=False)

    assert task.status == DownloadStatus.COMPLETED
    assert not orphan.exists()
    assert other.exists()
    assert other_part.exists()


def test_progress_hook_records_downloaded_entry_id(tmp_path):
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

    engine = YtDlpEngine.__new__(YtDlpEngine)
    task = DownloadTask(url="https://www.instagram.com/p/abc/")
    hook = YtDlpEngine._make_progress_hook(engine, task, None)
    fname = str(tmp_path / "a [ID1].mp4")
    hook({"status": "downloading", "filename": fname, "info_dict": {"id": "ID1"}})
    hook({"status": "finished", "filename": fname, "info_dict": {"id": "ID1"}})

    assert getattr(task, "ytdlp_ids", None) == ["ID1"]


# BUG-PROFILE-COOKIE: the profile fast path deleted the decrypted cookie
# before handing opts["cookiefile"] to the flat extract, so private profiles
# were listed logged out and yt-dlp's save_cookies() rewrote a plaintext file.
def test_profile_fast_path_keeps_cookie_until_extract_done(tmp_path):
    from unittest.mock import patch

    import infrastructure.downloader.yt_dlp_engine as mod

    temp_cookie = tmp_path / "omnidl_dec_test.txt"
    temp_cookie.write_text("# Netscape HTTP Cookie File\n")
    seen = {}

    def fake_flat(self, url, opts):
        seen["exists"] = Path(opts["cookiefile"]).exists()
        return MediaInfo(url=url, title="profile")

    cfg = MagicMock()
    cfg.cookie_file = ""
    cfg.proxy = ""
    cfg.use_cookies = False
    engine = mod.YtDlpEngine(cfg)
    with (
        patch.object(mod, "_resolve_cookie", return_value=str(tmp_path / "instagram.enc")),
        patch.object(mod, "_prepare_cookie_for_use", return_value=(str(temp_cookie), True)),
        patch.object(mod.YtDlpEngine, "_extract_playlist_flat", fake_flat),
    ):
        engine.extract_info("https://www.instagram.com/someprofile/")

    assert seen["exists"] is True
    assert not temp_cookie.exists()


# ── gallery-dl engine ─────────────────────────────────────────────────────
import io  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402

import pytest  # noqa: E402

_REAL_POPEN = subprocess.Popen


class _GdlProc:
    """gallery-dl run with -q: nothing on stdout, exit code set per test."""

    returncode_value = 1
    stderr_text = "[instagram][error] HttpError: 401 Unauthorized (login required)\n"
    block = False

    def __new__(cls, cmd, **kw):
        if "-d" not in cmd:
            return _REAL_POPEN(cmd, **kw)
        return object.__new__(cls)

    def __init__(self, cmd, **_kw):
        self.returncode = None
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO(self.stderr_text)
        self.killed = threading.Event()
        Path(cmd[cmd.index("-d") + 1]).mkdir(parents=True, exist_ok=True)
        type(self).last = self

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.block:
            self.killed.wait(timeout=5.0)
        self.returncode = -9 if self.killed.is_set() else self.returncode_value
        return self.returncode

    def kill(self):
        self.killed.set()


def _run_gdl(tmp_path, url, proc_cls, task=None, cookie_temp=None):
    from unittest.mock import patch

    import infrastructure.downloader.gallery_dl_engine as gdl

    task = task or DownloadTask(url=url, output_dir=str(tmp_path))
    cfg = MagicMock(proxy="", use_cookies=False, cookies_browser="")
    prep = (str(cookie_temp), True) if cookie_temp else None
    with (
        patch.object(gdl, "_find_executable", return_value="gallery-dl"),
        patch.object(gdl.subprocess, "Popen", proc_cls),
        patch.object(gdl, "_ytdlp_carousel_videos", return_value=[]),
        patch(
            "infrastructure.downloader.yt_dlp_engine._resolve_cookie",
            return_value=str(tmp_path / "c.enc") if prep else None,
        ),
        patch("infrastructure.downloader.yt_dlp_engine._prepare_cookie_for_use", return_value=prep),
        patch("infrastructure.downloader.yt_dlp_engine._validate_cookie_path", return_value=None),
        patch("utils.ffmpeg_locator.get_ffmpeg_path", return_value=None),
    ):
        gdl.GalleryDlEngine.download(gdl.GalleryDlEngine(cfg), task)
    return task


# BUG-GDL-IG-SILENT: a failed gallery-dl run on a /p/ post (login required,
# private, 429) with no video rescued was reported COMPLETED with no file.
def test_ig_carousel_gallery_dl_failure_is_raised(tmp_path):
    with pytest.raises(RuntimeError):
        _run_gdl(tmp_path, "https://www.instagram.com/p/PRIVATE01/", _GdlProc)


# BUG-GDL-COOKIE-LEAK: the error and cancel raises skipped the unlink of the
# decrypted plaintext cookie.
def test_gallery_dl_error_removes_temp_cookie(tmp_path):
    cookie = tmp_path / "omnidl_dec_gdl.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    with pytest.raises(RuntimeError):
        _run_gdl(tmp_path, "https://x.com/someone/status/1/photo/1", _GdlProc, cookie_temp=cookie)
    assert not cookie.exists()


# BUG-GDL-CANCEL: with -q gallery-dl prints nothing, so the kill inside the
# stdout loop never ran and Cancel waited for gallery-dl to finish.
def test_gallery_dl_cancel_kills_process_and_removes_cookie(tmp_path):
    from yt_dlp.utils import DownloadError

    class _Blocking(_GdlProc):
        block = True
        returncode_value = 0

    cookie = tmp_path / "omnidl_dec_gdl2.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    task = DownloadTask(url="https://www.instagram.com/p/CANCEL01/", output_dir=str(tmp_path))
    threading.Timer(0.3, task.cancel).start()
    with pytest.raises(DownloadError):
        _run_gdl(tmp_path, task.url, _Blocking, task=task, cookie_temp=cookie)
    assert _Blocking.last.killed.is_set()
    assert not cookie.exists()
