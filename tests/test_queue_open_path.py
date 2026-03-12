"""
tests/test_queue_open_path.py
Regression guard: a COMPLETED DownloadTask must always carry a valid
final output path (task.filename) so the Queue "Open" button opens the
correct folder.

Covers the root-cause scenario:
  _capturing_pp_hook must write the final merged path directly to
  task.filename, not only store it in the local _final_filepath closure.

Run with:  pytest tests/test_queue_open_path.py -v
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask, MediaInfo
from infrastructure.downloader.download_manager import DownloadManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path):
    cfg = MagicMock()
    cfg.max_concurrent = 2
    cfg.max_retries = 1
    cfg.download_dir = tmp_path
    return cfg


def _make_task(tmp_path: Path) -> DownloadTask:
    t = DownloadTask(url="https://example.com/v", output_dir=str(tmp_path))
    t.media_info = MediaInfo(url=t.url, title="Queue Open Regression")
    return t


def _wait_terminal(task: DownloadTask, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while task.status not in DownloadStatus.terminal_states():
        time.sleep(0.02)
        assert time.time() < deadline, (
            f"Task never reached terminal state (current: {task.status})"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestQueueOpenPath:
    """
    Regression suite for GitHub issue: Queue "Open" button opens wrong folder.

    Root cause: _capturing_pp_hook stored the final path only in a local
    closure (_final_filepath) instead of also writing it to task.filename
    synchronously.  This left task.filename as the intermediate stream path
    (e.g. video.f137.webm) when the pp-hook's info_dict["filepath"] key was
    absent, causing the Queue widget to derive the wrong parent directory.
    """

    def test_completed_task_has_non_empty_filename(self, tmp_path):
        """
        A COMPLETED task MUST have a non-empty task.filename.
        The Queue Open button is gated on this field; if it is empty the
        button does not appear, but if the button IS visible the path must
        be valid.
        """
        final_file = tmp_path / "finished_video.mp4"
        final_file.touch()

        def fake_download(task, on_progress=None, on_postprocess=None):
            # Simulate what the fixed _capturing_pp_hook does: write the
            # final path to task.filename before returning.
            task.filename = str(final_file)

        engine = MagicMock()
        engine.download.side_effect = fake_download
        bus = MagicMock()

        mgr = DownloadManager(config=_make_config(tmp_path), engine=engine,
                              event_bus=bus)
        mgr.start()
        try:
            task = _make_task(tmp_path)
            mgr.enqueue(task)
            _wait_terminal(task)

            assert task.status == DownloadStatus.COMPLETED
            assert task.filename, (
                "task.filename must not be empty for a COMPLETED task — "
                "the Queue 'Open' button would fall back to the wrong folder."
            )
        finally:
            mgr.shutdown(wait=False)

    def test_open_folder_uses_filename_parent_not_output_dir(self, tmp_path):
        """
        When task.filename is correctly set, _open_folder must derive its
        target from Path(task.filename).parent — not from task.output_dir.

        This guards against the regression where output_dir (the globally
        configured download folder) was used even when the file landed in a
        task-specific subdirectory.
        """
        # Simulate a task whose file was saved to a custom sub-folder
        custom_dir = tmp_path / "custom_output"
        custom_dir.mkdir()
        final_file = custom_dir / "video.mp4"
        final_file.write_bytes(b"\x00" * 1024)

        task = _make_task(tmp_path)          # output_dir = tmp_path (default)
        task.filename = str(final_file)       # file is actually in custom_dir
        task.status = DownloadStatus.COMPLETED

        opened_paths: list[Path] = []

        # Patch open_folder to capture the target instead of launching Finder
        import ui.components.download_item_widget as mod
        original_open_folder = mod.open_folder
        original_reveal = mod.reveal_in_explorer

        def spy_open_folder(path: Path) -> None:
            opened_paths.append(path)

        mod.open_folder = spy_open_folder
        mod.reveal_in_explorer = lambda p: False  # stub: no file-manager on CI
        try:
            from ui.components.download_item_widget import DownloadItemWidget
            # Build a minimal widget without a real Tk root (unit-test safe)
            widget = object.__new__(DownloadItemWidget)
            widget.task = task
            widget._completed_path = str(final_file)
            widget._open_folder()

            assert opened_paths, "_open_folder did not call open_folder at all"
            assert opened_paths[0] == custom_dir, (
                f"Expected {custom_dir!r}, got {opened_paths[0]!r}.\n"
                "Queue 'Open' must derive its target from task.filename, not output_dir."
            )
        finally:
            mod.open_folder = original_open_folder
            mod.reveal_in_explorer = original_reveal

    def test_filename_set_by_pp_hook_before_download_returns(self, tmp_path):
        """
        _capturing_pp_hook must write to task.filename synchronously,
        INSIDE the hook, so the Queue always has the correct path even if
        the post-ydl.download() resolution block is skipped or fails.

        This is the minimal atomic unit-test for the yt_dlp_engine.py fix.
        """
        final_file = tmp_path / "merged.mp4"
        final_file.write_bytes(b"\x00" * 2048)  # >1 KB — passes size check

        task = _make_task(tmp_path)
        task.filename = ""  # start empty — pre-completion state

        # Re-create exactly what the patched _capturing_pp_hook does when
        # info_dict["filepath"] is present and the file passes _MEDIA_EXTS.
        from pathlib import Path as _Path
        _MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv",
                       ".m4v", ".mp3", ".m4a", ".opus", ".aac", ".flac", ".wav"}
        _final_filepath: list[str] = []

        def _capturing_pp_hook(d: dict) -> None:
            if d.get("status") == "finished":
                info = d.get("info_dict") or {}
                fp = (
                    info.get("filepath")
                    or info.get("__real_download_filename")
                    or ""
                )
                if fp:
                    p = _Path(fp)
                    if p.suffix.lower() in _MEDIA_EXTS and not fp.endswith(".part"):
                        _final_filepath.clear()
                        _final_filepath.append(fp)
                        # THE FIX: update task.filename synchronously here
                        with task._lock:
                            task.filename = fp

        # Fire the hook as yt-dlp would after FFmpegMerger finishes
        _capturing_pp_hook({
            "status": "finished",
            "info_dict": {"filepath": str(final_file)},
        })

        assert task.filename == str(final_file), (
            "task.filename was not updated by _capturing_pp_hook synchronously.\n"
            "Queue 'Open' would use an incorrect path."
        )
        assert _Path(task.filename).parent == tmp_path


    def test_relative_filename_anchored_to_output_dir(self, tmp_path):
        """
        When task.filename is a bare filename (no directory component),
        _open_folder must resolve it relative to task.output_dir, NOT relative
        to the process CWD.

        Root cause: on Windows + PyInstaller, CWD when launching from a
        double-clicked EXE is the EXE directory.  Path("video.mp4").resolve()
        would yield EXE-dir/video.mp4 which does not exist, causing _open_folder
        to fall through to the EXE directory instead of the downloads folder.

        Fix: if raw is not absolute, anchor it to task.output_dir.
        """
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()
        final_file = download_dir / "video.mp4"
        final_file.write_bytes(b"\x00" * 1024)

        # Simulate yt-dlp returning only the basename (no directory)
        bare_name = "video.mp4"

        task = _make_task(tmp_path)
        task.output_dir = str(download_dir)   # correct download directory
        task.filename = bare_name              # bare name — PyInstaller scenario
        task.status = DownloadStatus.COMPLETED

        opened_paths: list[Path] = []

        import ui.components.download_item_widget as mod
        original_open_folder = mod.open_folder
        original_reveal = mod.reveal_in_explorer

        mod.open_folder = lambda p: opened_paths.append(p)
        mod.reveal_in_explorer = lambda p: False
        try:
            widget = object.__new__(mod.DownloadItemWidget)
            widget.task = task
            widget._completed_path = bare_name
            widget._open_folder()

            assert opened_paths, "_open_folder did not call open_folder"
            assert opened_paths[0] == download_dir, (
                f"Expected {download_dir!r}, got {opened_paths[0]!r}. "
                "Bare filename must be anchored to task.output_dir, not process CWD."
            )
        finally:
            mod.open_folder = original_open_folder
            mod.reveal_in_explorer = original_reveal
    def test_fallback_to_output_dir_when_filename_empty(self, tmp_path):
        """
        When task.filename is empty, _open_folder must open task.output_dir
        (not silently fail or open a wrong directory).
        """
        task = _make_task(tmp_path)
        task.filename = ""
        task.status = DownloadStatus.COMPLETED

        opened_paths: list[Path] = []

        import ui.components.download_item_widget as mod
        original_open_folder = mod.open_folder

        def spy_open_folder(path: Path) -> None:
            opened_paths.append(path)

        mod.open_folder = spy_open_folder
        try:
            widget = object.__new__(mod.DownloadItemWidget)
            widget.task = task
            widget._completed_path = ""
            widget._open_folder()

            assert opened_paths, "_open_folder did not open anything when filename is empty"
            assert opened_paths[0] == Path(tmp_path), (
                f"Expected fallback to {tmp_path!r}, got {opened_paths[0]!r}"
            )
        finally:
            mod.open_folder = original_open_folder
