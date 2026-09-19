"""Regression tests for the September 2026 convert audit.

Each test pins one bug found while auditing the desktop Convert tab and the
Remote API convert endpoints:

  A1  glob metacharacters in a filename broke stale ``.part`` cleanup
  A2  a remux timeout orphaned both the ``.part.mp4`` and the target container
  A3  a cancel racing ``ConvertQueue.submit()`` was lost
  A4  the file-based convert/subtitle endpoints blocked the event loop
  A5  a subtitles-only desktop job auto-sent the ``.srt`` over Taildrop
"""

from __future__ import annotations

import inspect
import subprocess
import threading
from pathlib import Path

import pytest

from app.services import ffmpeg_convert_service as fcs
from app.services.ffmpeg_convert_service import FfmpegConvertService

# ── A1: stale .part cleanup with glob metacharacters in the name ─────────────


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


@pytest.mark.parametrize(
    "stem",
    ["Plain Name", "Song [MV] 4K", "What? Really*", "Mix [a][b]"],
)
def test_stale_part_cleanup_handles_glob_metacharacters(tmp_path, monkeypatch, stem):
    """_convert_sync deletes the old non-UUID .part file whatever the name."""
    source = _touch(tmp_path / f"{stem}.mp4")
    stale = _touch(tmp_path / f"{stem}_iPhone.part.mp4")
    stale_trim = _touch(tmp_path / f"{stem}_iPhone.part.trim.mp4")

    monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin", staticmethod(lambda: Path("ffmpeg")))
    monkeypatch.setattr(FfmpegConvertService, "_probe_duration", staticmethod(lambda *_a: 10.0))

    def _fake_encode(self, *_a, **_kw):
        raise fcs.ConversionError("stop after cleanup")

    monkeypatch.setattr(FfmpegConvertService, "_try_encode_with_fallback", _fake_encode)

    with pytest.raises(fcs.ConversionError):
        FfmpegConvertService()._convert_sync(source, "standard", tmp_path, None)

    assert not stale.exists(), "stale .part.mp4 was not cleaned up"
    assert not stale_trim.exists(), "stale .part.trim.mp4 was not cleaned up"


def test_stale_mp3_part_cleanup_handles_glob_metacharacters(tmp_path, monkeypatch):
    stem = "Track [01]"
    source = _touch(tmp_path / f"{stem}.mp4")
    stale = _touch(tmp_path / f"{stem}_deadbeef.part.mp3")
    # Backdate so the 1-hour "possibly active job" guard does not protect it.
    import os
    import time

    os.utime(stale, (time.time() - 7200, time.time() - 7200))

    monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin", staticmethod(lambda: Path("ffmpeg")))
    monkeypatch.setattr(FfmpegConvertService, "_probe_duration", staticmethod(lambda *_a: 10.0))

    def _boom(*_a, **_kw):
        raise fcs.ConversionError("stop after cleanup")

    monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(_boom))

    with pytest.raises(fcs.ConversionError):
        FfmpegConvertService()._extract_audio_mp3(source, tmp_path, None)

    assert not stale.exists(), "stale .part.mp3 was not cleaned up"


# ── A2: remux timeout must not orphan temp files ─────────────────────────────


def test_remux_timeout_cleans_up_and_raises_conversion_error(tmp_path, monkeypatch):
    source = _touch(tmp_path / "clip.flv")
    temp_output = _touch(tmp_path / "clip_iPhone_deadbeef.part.mp4")

    monkeypatch.setattr(fcs, "probe_media_info", lambda _p: None)
    monkeypatch.setattr(FfmpegConvertService, "_build_cmd", staticmethod(lambda *_a, **_kw: ["ffmpeg"]))
    monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(lambda *_a, **_kw: None))

    def _timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)

    monkeypatch.setattr(subprocess, "run", _timeout)

    with pytest.raises(fcs.ConversionError) as err:
        FfmpegConvertService()._fresh_encode(
            Path("ffmpeg"),
            source,
            tmp_path,
            temp_output,
            10.0,
            fcs._PRESETS["standard"],
            None,
            target_ext="mkv",
        )

    assert "timed out" in str(err.value)
    assert not temp_output.exists(), ".part.mp4 orphaned after remux timeout"
    assert not (tmp_path / "clip_iPhone.mkv").exists(), "partial .mkv orphaned"


def test_remux_timeout_scales_with_file_size(tmp_path):
    """A flat 120 s killed remuxes of multi-GB recordings; the budget scales."""
    small = _touch(tmp_path / "small.mp4")
    assert FfmpegConvertService._remux_timeout_s(small) == 120.0
    # A file that cannot be stat()-ed still gets the floor, never an error.
    assert FfmpegConvertService._remux_timeout_s(tmp_path / "gone.mp4") == 120.0

    big = tmp_path / "big.mp4"
    with big.open("wb") as fh:  # sparse file, no disk cost
        fh.truncate(8 * 1024**3)
    # 8 GB / 10 MB-per-second = 819 s, comfortably above the 120 s floor and
    # below the 3600 s cap.
    assert FfmpegConvertService._remux_timeout_s(big) == pytest.approx(819.2, rel=1e-3)

    huge = tmp_path / "huge.mp4"
    with huge.open("wb") as fh:
        fh.truncate(80 * 1024**3)
    assert FfmpegConvertService._remux_timeout_s(huge) == 3600.0


def test_trim_remux_timeout_keeps_the_untrimmed_output(tmp_path, monkeypatch):
    """A timed-out audio trim is a degraded result, not a failed conversion."""
    source = _touch(tmp_path / "live.ts")
    temp_output = _touch(tmp_path / "live_iPhone_deadbeef.part.mp4")

    info = fcs.FfmpegMediaInfo(
        video_codec="h264",
        video_nb_frames=100,
        video_duration_s=10.0,
        audio_duration_s=30.0,
    )
    monkeypatch.setattr(fcs, "probe_media_info", lambda _p: info)
    monkeypatch.setattr(FfmpegConvertService, "_build_cmd", staticmethod(lambda *_a, **_kw: ["ffmpeg"]))
    monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(lambda *_a, **_kw: None))
    monkeypatch.setattr(FfmpegConvertService, "_validate_output", staticmethod(lambda _p: None))

    def _timeout(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)

    monkeypatch.setattr(subprocess, "run", _timeout)

    out = FfmpegConvertService()._fresh_encode(
        Path("ffmpeg"),
        source,
        tmp_path,
        temp_output,
        10.0,
        fcs._PRESETS["standard"],
        None,
        target_ext="mp4",
    )

    assert out == tmp_path / "live_iPhone.mp4"
    assert out.exists()
    assert not (tmp_path / "live_iPhone_deadbeef.part.trim.mp4").exists()


# ── A3: cancel racing submit() ───────────────────────────────────────────────


def test_cancel_during_submit_is_not_lost(tmp_path, monkeypatch):
    """A cancel that lands while submit() is still running must reach the queue."""
    from app.services.remote_convert_service import RemoteConvertService

    source = _touch(tmp_path / "clip.mp4")
    cancelled = threading.Event()

    class _Cfg:
        convert_max_concurrent = 2

        def set(self, *_a):
            pass

    class _Bus:
        def __getattr__(self, _name):
            return lambda **_kw: None

    svc = RemoteConvertService(_Cfg(), _Bus())

    real_submit = svc._queue.submit

    def _submit_then_cancel(*args, **kwargs):
        # Simulate the client's POST /cancel landing inside submit().
        job = next(iter(svc._jobs.values()))
        job.request_cancel()
        real_submit  # noqa: B018 - keep the reference readable
        return cancelled.set

    monkeypatch.setattr(svc._queue, "submit", _submit_then_cancel)
    monkeypatch.setattr(
        "app.services.remote_convert_service._allowed_codecs", lambda: frozenset({"h264"})
    )

    svc.start_convert(source_task_id="t1", file_path=source, encoder_key="cpu")

    assert cancelled.is_set(), "cancel raced submit() and was dropped"


# ── A4: the file endpoints must not block the event loop ─────────────────────


@pytest.mark.parametrize(
    "endpoint",
    ["convert_file", "convert_files_batch", "subtitle_file"],
)
def test_file_convert_endpoints_offload_blocking_probes(endpoint):
    """These coroutines shell out to FFmpeg probes; they must use to_thread."""
    src = Path("api/server.py").read_text(encoding="utf-8")
    start = src.index(f"async def {endpoint}(")
    body = src[start : start + 3500]
    assert "asyncio.to_thread(" in body, f"{endpoint} still calls the service inline"


# ── A5: subtitles-only desktop job must not Taildrop the .srt ────────────────


def test_desktop_subtitles_only_skips_taildrop():
    src = inspect.getsource(
        __import__("ui.tabs.convert_tab", fromlist=["ConvertTab"]).ConvertTab._submit_job
    )
    assert "subtitles_only" in src, "_submit_job no longer guards the Taildrop hook"
    assert src.index("if subtitles_only:") < src.index("send_converted_file")
