"""
tests/test_queue_open_folder_fix.py
Regression guard for the Queue "Open" wrong-folder bug.

Ensures that:
  1. DownloadItemWidget._completed_path is snapshotted from task.filename
     the first time task.status == COMPLETED is seen in refresh().
  2. The snapshot is immune to later mutations of task.filename.
  3. _open_folder() uses the snapshotted path (not task.output_dir) to
     derive the folder — identical behaviour to the History tab.
  4. The progress hook in YtDlpEngine only stores absolute paths in
     task.filename (relative / bare basenames are silently ignored).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Minimal stubs so we can import the widget without a running Tk instance ──

def _stub_customtkinter():
    """Replace customtkinter with a thin MagicMock so widget import succeeds."""
    ctk = types.ModuleType("customtkinter")
    # Any attribute access returns a new MagicMock class/instance.
    ctk.__getattr__ = lambda name: MagicMock  # noqa: ARG001
    # CTkFrame must be a class we can subclass.
    class _Frame:
        def __init__(self, *a, **kw): pass
        def pack(self, **kw): pass
        def pack_forget(self): pass
        def winfo_ismapped(self): return False
        def winfo_exists(self): return True
        def configure(self, **kw): pass
    ctk.CTkFrame = _Frame
    ctk.CTkLabel = MagicMock
    ctk.CTkButton = MagicMock
    ctk.CTkFont = MagicMock
    sys.modules["customtkinter"] = ctk
    return ctk


def _stub_ui_deps():
    for mod in ("ui.themes.tokens", "ui.components.progress_bar"):
        m = types.ModuleType(mod)
        m.__getattr__ = lambda name: MagicMock()  # noqa: ARG001
        sys.modules[mod] = m
    # T token object
    T = MagicMock()
    T.register = lambda cb: None  # type: ignore[assignment]
    sys.modules["ui.themes.tokens"].T = T


_stub_customtkinter()
_stub_ui_deps()


# ── Now import the modules under test ────────────────────────────────────────

from domain.enums.download_status import DownloadStatus        # noqa: E402
from domain.models.download_task import DownloadTask, MediaInfo  # noqa: E402


def _make_task(filename: str = "", output_dir: str = "/downloads") -> DownloadTask:
    t = DownloadTask(url="https://example.com/v", output_dir=output_dir)
    t.media_info = MediaInfo(url=t.url, title="Test Video")
    t.filename = filename
    return t


# ── Test 1: _completed_path is snapshotted on first COMPLETED transition ──────

def test_completed_path_snapshotted_on_first_completed(tmp_path):
    """refresh() must set _completed_path when status first becomes COMPLETED."""
    from ui.components.download_item_widget import DownloadItemWidget

    final_file = tmp_path / "video.mp4"
    final_file.touch()

    task = _make_task(filename=str(final_file), output_dir=str(tmp_path))
    task.status = DownloadStatus.COMPLETED

    widget = DownloadItemWidget.__new__(DownloadItemWidget)
    widget.task = task
    widget._completed_path = ""
    # Mock the CTk children so refresh() doesn't crash
    for attr in ("_title_lbl", "_status_badge", "_type_dot", "_prog",
                 "_speed_lbl", "_eta_lbl", "_size_lbl", "_err_lbl",
                 "_pause_btn", "_cancel_btn", "_folder_btn",
                 "_preview_btn", "_convert_btn"):
        m = MagicMock()
        m.winfo_ismapped.return_value = False
        m.winfo_exists.return_value = True
        setattr(widget, attr, m)
    widget._on_convert = None
    widget._converting = False

    widget.refresh(task)

    assert widget._completed_path == str(final_file), (
        "_completed_path must be set to task.filename on first COMPLETED refresh"
    )


# ── Test 2: _completed_path is NOT overwritten on subsequent polls ────────────

def test_completed_path_not_overwritten_by_later_refresh(tmp_path):
    """Once snapshotted, _completed_path must survive subsequent refresh() calls."""
    from ui.components.download_item_widget import DownloadItemWidget

    original_file = tmp_path / "video.mp4"
    original_file.touch()

    task = _make_task(filename=str(original_file), output_dir=str(tmp_path))
    task.status = DownloadStatus.COMPLETED

    widget = DownloadItemWidget.__new__(DownloadItemWidget)
    widget.task = task
    widget._completed_path = ""
    for attr in ("_title_lbl", "_status_badge", "_type_dot", "_prog",
                 "_speed_lbl", "_eta_lbl", "_size_lbl", "_err_lbl",
                 "_pause_btn", "_cancel_btn", "_folder_btn",
                 "_preview_btn", "_convert_btn"):
        m = MagicMock()
        m.winfo_ismapped.return_value = False
        m.winfo_exists.return_value = True
        setattr(widget, attr, m)
    widget._on_convert = None
    widget._converting = False

    widget.refresh(task)
    assert widget._completed_path == str(original_file)

    # Simulate a second poll where winfo_ismapped() returns True (button already
    # packed) — the snapshot must NOT be overwritten.
    widget._folder_btn.winfo_ismapped.return_value = True
    task.filename = "/some/other/path.mp4"   # mutation after completion
    widget.refresh(task)

    assert widget._completed_path == str(original_file), (
        "_completed_path must not be overwritten after first COMPLETED snapshot"
    )


# ── Test 3: _open_folder derives folder from _completed_path ─────────────────

def test_open_folder_uses_completed_path_parent(tmp_path):
    """_open_folder must open Path(_completed_path).parent, not output_dir."""
    from ui.components.download_item_widget import DownloadItemWidget

    actual_dir = tmp_path / "actual_output"
    actual_dir.mkdir()
    wrong_dir  = tmp_path / "wrong_output_dir"
    wrong_dir.mkdir()

    final_file = actual_dir / "video.mp4"
    final_file.touch()

    task = _make_task(
        filename=str(final_file),
        output_dir=str(wrong_dir),   # deliberately wrong
    )
    task.status = DownloadStatus.COMPLETED

    widget = DownloadItemWidget.__new__(DownloadItemWidget)
    widget.task = task
    widget._completed_path = str(final_file)   # snapshotted correctly

    opened_paths: list[Path] = []

    with patch("ui.components.download_item_widget.reveal_in_explorer",
               return_value=False) as mock_reveal, \
         patch("ui.components.download_item_widget.open_folder",
               side_effect=opened_paths.append) as mock_open:
        widget._open_folder()

    # Must open the *actual* directory containing the file, not output_dir
    assert len(opened_paths) == 1
    assert opened_paths[0] == actual_dir, (
        f"Expected {actual_dir}, got {opened_paths[0]}. "
        "Queue Open must use Path(completed_path).parent, not task.output_dir."
    )


# ── Test 4: _open_folder matches History tab behaviour (file-gone case) ───────

def test_open_folder_opens_parent_when_file_missing(tmp_path):
    """When the file no longer exists, open its parent folder (same as History)."""
    from ui.components.download_item_widget import DownloadItemWidget

    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    missing_file = output_dir / "video_deleted.mp4"
    # Deliberately do NOT create the file — simulates a moved/deleted download.

    task = _make_task(filename=str(missing_file), output_dir=str(output_dir))
    task.status = DownloadStatus.COMPLETED

    widget = DownloadItemWidget.__new__(DownloadItemWidget)
    widget.task = task
    widget._completed_path = str(missing_file)

    opened_paths: list[Path] = []
    with patch("ui.components.download_item_widget.open_folder",
               side_effect=opened_paths.append):
        widget._open_folder()

    assert len(opened_paths) == 1
    assert opened_paths[0] == output_dir, (
        "When the file is gone, _open_folder must open its parent directory."
    )


# ── Test 5: progress hook ignores relative/bare filenames ────────────────────

def test_progress_hook_ignores_relative_filename():
    """
    The progress hook must NOT store a relative (bare-basename) filename in
    task.filename — doing so makes Path(task.filename).parent resolve to CWD.
    """
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

    config = MagicMock()
    engine = YtDlpEngine(config)
    import os, sys as _sys
    if _sys.platform == "win32":
        _abs = os.path.join(os.path.splitdrive(os.getcwd())[0] or "C:\\", "absolute", "prior.mp4")
    else:
        _abs = "/absolute/prior.mp4"
    task = _make_task(filename=_abs)

    hook = engine._make_progress_hook(task, callback=None)

    # Feed a hook dict with a bare basename (no directory component)
    hook({
        "status": "downloading",
        "filename": "bare_basename_no_dir.mp4",   # relative — must be ignored
        "downloaded_bytes": 1024,
        "total_bytes": 4096,
        "speed": 512_000,
        "eta": 10,
    })

    assert Path(task.filename).is_absolute(), (
        "task.filename must remain absolute after a relative filename from "
        "the progress hook.  Got: %r" % task.filename
    )
    # The prior absolute value must be preserved
    assert task.filename == _abs, (
        "A relative progress-hook filename must not overwrite an existing "
        "absolute task.filename."
    )


# ── Test 6: progress hook DOES store absolute filenames ──────────────────────

def test_progress_hook_stores_absolute_filename(tmp_path):
    """The progress hook must store an absolute path when yt-dlp provides one."""
    from infrastructure.downloader.yt_dlp_engine import YtDlpEngine

    config = MagicMock()
    engine = YtDlpEngine(config)
    task = _make_task()

    abs_path = str(tmp_path / "video.mp4")
    hook = engine._make_progress_hook(task, callback=None)
    hook({
        "status": "downloading",
        "filename": abs_path,
        "downloaded_bytes": 500,
        "total_bytes": 1000,
        "speed": None,
        "eta": None,
    })

    assert task.filename == abs_path, (
        "An absolute filename from the progress hook must be stored in task.filename."
    )


# ── Test 7: bare-filename in _completed_path anchored to task.output_dir ─────

def test_open_folder_bare_filename_anchored_to_output_dir(tmp_path):
    """
    When _completed_path is a bare filename with no directory component,
    _open_folder must resolve it against task.output_dir, NOT against CWD.

    PyInstaller scenario: process CWD = EXE directory.  A bare "video.mp4"
    would resolve to EXE-dir/video.mp4 (nonexistent), then fall through to
    EXE dir — completely wrong.  Correct: anchor to task.output_dir so the
    resolved path is download-dir/video.mp4 and the opened folder is
    download-dir.
    """
    from ui.components.download_item_widget import DownloadItemWidget

    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    final_file = download_dir / "video.mp4"
    final_file.touch()

    task = _make_task(filename="video.mp4", output_dir=str(download_dir))
    task.status = DownloadStatus.COMPLETED

    widget = DownloadItemWidget.__new__(DownloadItemWidget)
    widget.task = task
    widget._completed_path = "video.mp4"   # bare name — PyInstaller scenario

    opened_paths: list[Path] = []

    with patch("ui.components.download_item_widget.reveal_in_explorer",
               return_value=False),          patch("ui.components.download_item_widget.open_folder",
               side_effect=opened_paths.append):
        widget._open_folder()

    assert len(opened_paths) == 1, "_open_folder must call open_folder"
    assert opened_paths[0] == download_dir, (
        f"Expected {download_dir!r} (task.output_dir), got {opened_paths[0]!r}. "
        "Bare filename must be anchored to task.output_dir, not process CWD."
    )


# ── Test 8: relative output_dir fallback is also resolved to absolute ─────────

def test_open_folder_resolves_relative_output_dir_fallback(tmp_path):
    """
    When both _completed_path and task.filename are empty, _open_folder falls
    back to task.output_dir.  A relative output_dir must be resolved before
    calling open_folder so the correct directory is opened.
    """
    from ui.components.download_item_widget import DownloadItemWidget
    import os

    download_dir = tmp_path / "downloads"
    download_dir.mkdir()

    old_cwd = os.getcwd()
    try:
        os.chdir(download_dir)   # simulate: CWD == download_dir here

        task = _make_task(filename="", output_dir=".")  # relative
        task.status = DownloadStatus.COMPLETED

        widget = DownloadItemWidget.__new__(DownloadItemWidget)
        widget.task = task
        widget._completed_path = ""

        opened_paths: list[Path] = []
        with patch("ui.components.download_item_widget.open_folder",
                   side_effect=opened_paths.append):
            widget._open_folder()
    finally:
        os.chdir(old_cwd)

    assert len(opened_paths) == 1
    assert opened_paths[0] == download_dir, (
        f"Expected {download_dir!r}, got {opened_paths[0]!r}. "
        "Relative output_dir fallback must be resolved to absolute."
    )


# ── Test 9: BUG BT — pause/cancel always hidden when COMPLETED (empty filename) ─

def test_pause_cancel_hidden_on_completed_with_empty_filename():
    """BUG BT regression: pause and cancel buttons must be pack_forgotten when
    status reaches COMPLETED even if task.filename is empty/falsy.

    Previously the pack_forget() calls only ran inside the block guarded by
    ``if st == DownloadStatus.COMPLETED and task.filename``, so a COMPLETED
    task whose filename was not yet populated left both buttons visible-but-
    disabled — clicking them silently did nothing.
    """
    from ui.components.download_item_widget import DownloadItemWidget

    task = _make_task(filename="", output_dir="/downloads")   # no filename
    task.status = DownloadStatus.COMPLETED

    widget = DownloadItemWidget.__new__(DownloadItemWidget)
    widget.task = task
    widget._completed_path = ""
    widget._converting = False
    widget._on_convert = None

    # Build mock widgets; start with pause/cancel *mapped* (winfo_ismapped=True)
    # so we can verify pack_forget is called on them.
    pause_btn  = MagicMock()
    cancel_btn = MagicMock()
    pause_btn.winfo_ismapped.return_value  = True   # currently visible
    cancel_btn.winfo_ismapped.return_value = True   # currently visible

    folder_btn  = MagicMock()
    preview_btn = MagicMock()
    folder_btn.winfo_ismapped.return_value  = False
    preview_btn.winfo_ismapped.return_value = False

    for attr in ("_title_lbl", "_status_badge", "_type_dot", "_prog",
                 "_speed_lbl", "_eta_lbl", "_size_lbl", "_err_lbl",
                 "_convert_btn"):
        m = MagicMock()
        m.winfo_ismapped.return_value = False
        setattr(widget, attr, m)

    widget._pause_btn   = pause_btn
    widget._cancel_btn  = cancel_btn
    widget._folder_btn  = folder_btn
    widget._preview_btn = preview_btn
    widget._url_lbl     = MagicMock()
    widget._url_lbl.winfo_ismapped.return_value = False
    widget._elapsed_lbl = MagicMock()

    widget.refresh(task)

    pause_btn.pack_forget.assert_called_once(), (
        "pause_btn.pack_forget() must be called when COMPLETED even with empty filename"
    )
    cancel_btn.pack_forget.assert_called_once(), (
        "cancel_btn.pack_forget() must be called when COMPLETED even with empty filename"
    )
