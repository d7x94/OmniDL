"""
BUG-IG-MIX — Instagram posts containing BOTH photos and videos only ever
downloaded the photos.

Evidence (omnidl_debug.log, lines 3446-3469):
    [yt-dlp diag] [Instagram] Playlist Post by by_doha_: Downloading 5 items of 5
    [yt-dlp] ERROR: [Instagram] DcDhOyLBG2R: No video formats found!
    Instagram photo detected (no video stream) -> gallery-dl route
    gallery-dl fallback scan: found 3 image(s) + 0 video(s)
    gallery-dl complete: 3 file(s)          <- 5 items in, 3 files out, no rescue

Root cause: GalleryDlEngine.download() runs gallery-dl with
"--filter extension in ('jpg', ...)" for /p/ carousels, so gallery-dl can
never write a video file.  The yt-dlp video rescue pass was then guarded by
`_all_images_only` (True whenever every gallery-dl file is an image), which
is *always* True for a carousel — so the rescue never ran and the 2 videos
of the post were silently dropped.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from domain.models.download_task import DownloadTask

_REAL_POPEN = subprocess.Popen


class _FakeProc:
    """Stand-in for subprocess.Popen running gallery-dl with -q (no stdout)."""

    def __new__(cls, cmd, **kw):
        # patch() replaces subprocess.Popen globally, so unrelated callers
        # (ctypes.util.find_library, ...) land here too — pass those through.
        if "-d" not in cmd:
            return _REAL_POPEN(cmd, **kw)
        return object.__new__(cls)

    def __init__(self, cmd, **_kw):
        self.returncode = 0
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        # gallery-dl writes into the directory given by "-d"
        self.out_dir = Path(cmd[cmd.index("-d") + 1])
        self._filtered = "--filter" in cmd
        self._write_files()

    def _write_files(self) -> None:
        # A 5-item carousel: 3 photos + 2 videos.  The --filter makes
        # gallery-dl write the photos only.
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (self.out_dir / f"photo_{i}.jpg").write_bytes(b"x" * 5000)
        if not self._filtered:
            for i in range(2):
                (self.out_dir / f"silent_{i}.mp4").write_bytes(b"x" * 5000)

    def wait(self):
        return self.returncode

    def kill(self):
        pass


def _make_engine():
    from infrastructure.downloader.gallery_dl_engine import GalleryDlEngine

    cfg = MagicMock(proxy="", use_cookies=False, cookies_browser="")
    return GalleryDlEngine(cfg)


def _run_download(tmp_path, url, rescue_return):
    """Run GalleryDlEngine.download() with gallery-dl + yt-dlp mocked out."""
    import infrastructure.downloader.gallery_dl_engine as gdl

    task = DownloadTask(url=url, output_dir=str(tmp_path))
    rescue_calls: list[dict] = []

    def _fake_rescue(**kwargs):
        rescue_calls.append(kwargs)
        return rescue_return(kwargs)

    with (
        patch.object(gdl, "_find_executable", return_value="gallery-dl"),
        patch.object(gdl.subprocess, "Popen", _FakeProc),
        patch.object(gdl, "_ytdlp_carousel_videos", side_effect=_fake_rescue),
        patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=None),
        patch("infrastructure.downloader.yt_dlp_engine._validate_cookie_path", return_value=None),
        patch("utils.ffmpeg_locator.get_ffmpeg_path", return_value=None),
    ):
        gdl.GalleryDlEngine.download(_make_engine(), task)

    return task, rescue_calls


class TestMixedCarousel:
    def test_video_rescue_runs_even_when_gallery_dl_returned_only_images(self, tmp_path):
        """The whole point of --filter is that gallery-dl returns images only.

        'All files are images' therefore proves nothing about the post and must
        NOT be used to skip the yt-dlp video pass.
        """

        def _rescue(kwargs):
            d = kwargs["rescue_dir"] or kwargs["output_dir"]
            out = []
            for i in range(2):
                p = Path(d) / f"video_{i}.mp4"
                p.write_bytes(b"x" * 20000)
                out.append(str(p))
            return out

        task, calls = _run_download(
            tmp_path,
            "https://www.instagram.com/p/DcDh0HdH-_F/?img_index=3&igsh=MWRubGV3cnZuamEyMQ==",
            _rescue,
        )

        assert calls, "yt-dlp video rescue was never invoked for a /p/ carousel"

        exts = sorted(Path(f).suffix.lower() for f in task.gallery_dl_files)
        assert exts.count(".mp4") == 2, f"videos missing from task files: {task.gallery_dl_files}"
        assert exts.count(".jpg") == 3, f"images missing from task files: {task.gallery_dl_files}"

    def test_rescue_writes_into_the_post_folder_when_there_are_no_images(self, tmp_path):
        """A video-only /p/ post gives gallery-dl nothing to download.

        The rescue must still target the per-post slug folder, not a nested
        '<uploader>/' subdirectory, so every file of the post stays together.
        """
        import infrastructure.downloader.gallery_dl_engine as gdl

        task = DownloadTask(url="https://www.instagram.com/p/VIDONLY01/", output_dir=str(tmp_path))
        calls: list[dict] = []

        class _EmptyProc(_FakeProc):
            def _write_files(self) -> None:
                self.out_dir.mkdir(parents=True, exist_ok=True)

        def _fake_rescue(**kwargs):
            calls.append(kwargs)
            return []

        with (
            patch.object(gdl, "_find_executable", return_value="gallery-dl"),
            patch.object(gdl.subprocess, "Popen", _EmptyProc),
            patch.object(gdl, "_ytdlp_carousel_videos", side_effect=_fake_rescue),
            patch("infrastructure.downloader.yt_dlp_engine._resolve_cookie", return_value=None),
            patch("infrastructure.downloader.yt_dlp_engine._validate_cookie_path", return_value=None),
            patch("utils.ffmpeg_locator.get_ffmpeg_path", return_value=None),
        ):
            gdl.GalleryDlEngine.download(gdl.GalleryDlEngine(MagicMock(proxy="", use_cookies=False)), task)

        assert calls, "rescue not invoked for a video-only post"
        rescue_dir = calls[0]["rescue_dir"]
        assert rescue_dir is not None, "rescue_dir is None -> videos land in a nested uploader folder"
        assert "VIDONLY0" in str(rescue_dir)
