"""
tests/test_subtitle_button_audit.py

Regressions found while auditing the "Tạo phụ đề" / "Chỉ tạo phụ đề (.srt)"
button on the desktop tab and on the Remote API.

  A1  a subtitles-only job is flagged as such end-to-end (job → snapshot →
      API response → SSE), so a client can tell it apart from a conversion
  A2  the web UI no longer treats a finished subtitle job as a finished
      conversion — the "Convert" button used to disappear for good
  A3  start_subtitles() rejects files whisper cannot read (.jpg, .txt …)
  A4  the whisper model download reports progress and honours cancel
  A5  concurrent downloads of *different* models no longer serialise
  A6  an existing .srt is never overwritten
  A7  the desktop card hides the before/after preview for an .srt output
  A8  SSE never leaks the server-side path of the generated .srt
"""

from __future__ import annotations

import inspect
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import api.server as srv
import ui.tabs.convert_tab as ct
from app.services import remote_convert_service as rcs
from app.services import whisper_subtitle_service as wss
from app.services.ffmpeg_convert_service import ConversionCancelledError
from app.services.remote_convert_service import RemoteConvertService
from domain.models.conversion_job import ConversionJob

_INDEX_HTML = Path(__file__).resolve().parents[1] / "api" / "static" / "index.html"


def _video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    f = tmp_path / name
    f.write_bytes(b"\x00" * 64)
    return f


@pytest.fixture
def svc(tmp_path: Path) -> RemoteConvertService:
    config = MagicMock()
    config.download_dir = tmp_path
    service = RemoteConvertService(config=config, event_bus=MagicMock())
    service._queue = MagicMock()
    service._queue.submit.return_value = lambda: None
    return service


# ── A1: the subtitles_only flag travels to the client ────────────────────────


class TestSubtitlesOnlyFlag:
    def test_a1_default_is_false(self):
        assert ConversionJob().subtitles_only is False
        assert ConversionJob().snapshot()["subtitles_only"] is False

    def test_a1_set_by_start_subtitles(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            job = svc.start_subtitles(_video(tmp_path))
        assert job.subtitles_only is True
        assert job.snapshot()["subtitles_only"] is True

    def test_a1_normal_convert_is_not_flagged(self, svc, tmp_path):
        job = svc.start_convert("t1", _video(tmp_path))
        assert job.subtitles_only is False

    def test_a1_reaches_the_api_response(self, tmp_path):
        job = ConversionJob(subtitles_only=True)
        app = srv.create_app(
            SimpleNamespace(get_all_tasks=lambda: [], get_history=lambda: [], get_task=lambda _t: None),
            SimpleNamespace(api_token="", download_dir=tmp_path),
            remote_convert=MagicMock(get_job=lambda _jid: job),
        )
        for route in app.routes:
            if getattr(route, "path", None) == "/api/convert/{job_id}":
                import asyncio

                resp = asyncio.run(route.endpoint(job_id=job.job_id, _=None))
                assert resp.subtitles_only is True
                return
        raise AssertionError("GET /api/convert/{job_id} not found")


# ── A2 / A7: the surfaces that render a finished job ─────────────────────────


class TestClientRendering:
    def test_a2_completed_convert_lookup_skips_subtitle_jobs(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "!j.subtitles_only" in html, "queue card still matches subtitle jobs"
        assert "job.status === 'COMPLETED' && !job.output_deleted && !job.subtitles_only" in html

    def test_a2_action_row_survives_a_subtitle_job(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "const keepActions" in html
        assert "if (keepActions)" in html

    def test_a2_progress_label_names_the_right_job(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert html.count("Đang tạo phụ đề…") >= 2, "queue card and file browser both need it"

    def test_a7_srt_output_hides_the_media_preview(self):
        src = inspect.getsource(ct.FileCard.refresh)
        assert '".srt"' in src and "_preview_btn.setVisible" in src


# ── A3: only media files can be transcribed ──────────────────────────────────


class TestTranscribableGuard:
    @pytest.mark.parametrize("name", ["photo.jpg", "notes.txt", "bundle.zip", "clip.mp4.part"])
    def test_a3_non_media_is_rejected(self, svc, tmp_path, name):
        f = tmp_path / name
        f.write_bytes(b"x")
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            with pytest.raises(ValueError, match="audio/video"):
                svc.start_subtitles(f)

    @pytest.mark.parametrize("name", ["clip.mp4", "clip.mkv", "talk.mp3", "talk.m4a"])
    def test_a3_media_is_accepted(self, svc, tmp_path, name):
        f = tmp_path / name
        f.write_bytes(b"x")
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            svc.start_subtitles(f)
        assert svc._queue.submit.called

    def test_a3_rejection_happens_before_the_job_is_queued(self, svc, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            with pytest.raises(ValueError):
                svc.start_subtitles(f)
        svc._queue.submit.assert_not_called()


# ── A4 / A5: the model download ──────────────────────────────────────────────


class _FakeResponse:
    """urlopen() stand-in that hands out *chunks* one read() at a time."""

    def __init__(self, chunks: list[bytes], length: int | None = None):
        self._chunks = list(chunks)
        total = length if length is not None else sum(len(c) for c in chunks)
        self.headers = {"Content-Length": str(total)}

    def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestModelDownload:
    def test_a4_progress_is_reported(self, tmp_path, monkeypatch):
        dest = tmp_path / "m.bin"
        monkeypatch.setattr(
            wss.urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse([b"a" * 50, b"b" * 50])
        )
        seen: list[float] = []
        wss._download("https://x/m.bin", dest, seen.append)
        assert seen == [50.0, 100.0]
        assert dest.read_bytes() == b"a" * 50 + b"b" * 50

    def test_a4_cancel_aborts_the_download(self, tmp_path, monkeypatch):
        dest = tmp_path / "m.bin"
        monkeypatch.setattr(
            wss.urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse([b"a" * 50] * 100)
        )
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ConversionCancelledError):
            wss._download("https://x/m.bin", dest, None, cancel)
        assert not dest.exists()
        assert not dest.with_suffix(".bin.part").exists()

    def test_a4_cancel_mid_stream_leaves_no_partial_file(self, tmp_path, monkeypatch):
        dest = tmp_path / "m.bin"
        cancel = threading.Event()
        monkeypatch.setattr(
            wss.urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse([b"a" * 50] * 100)
        )
        with pytest.raises(ConversionCancelledError):
            wss._download("https://x/m.bin", dest, lambda _p: cancel.set(), cancel)
        assert not dest.exists()

    def test_a4_generate_srt_maps_the_download_onto_the_first_5_percent(self, tmp_path):
        """The bar must move while a 141 MB model downloads, not sit at 0%."""
        seen: list[float] = []
        captured: dict = {}

        def _fake_ensure(key, on_progress=None, cancel_event=None):
            captured["on_progress"] = on_progress
            captured["cancel_event"] = cancel_event
            on_progress(100.0)
            raise wss.SubtitleError("stop here")

        with (
            patch.object(wss, "is_whisper_supported", return_value=True),
            patch.object(wss, "ensure_model", _fake_ensure),
        ):
            cancel = threading.Event()
            with pytest.raises(wss.SubtitleError):
                wss.generate_srt(
                    Path("ffmpeg"),
                    _video(tmp_path),
                    tmp_path / "clip.srt",
                    on_progress=seen.append,
                    cancel_event=cancel,
                )
        assert seen == [5.0]
        assert captured["cancel_event"] is cancel

    def test_a5_different_models_use_different_locks(self):
        assert wss._lock_for("ggml-tiny.bin") is not wss._lock_for("ggml-medium.bin")
        assert wss._lock_for("ggml-tiny.bin") is wss._lock_for("ggml-tiny.bin")


# ── A6: never clobber an existing .srt ───────────────────────────────────────


class TestSrtNotOverwritten:
    def test_a6_free_path_when_nothing_exists(self, tmp_path):
        target = tmp_path / "clip.srt"
        assert wss._free_srt_path(target) == target

    def test_a6_existing_file_gets_a_suffix(self, tmp_path):
        (tmp_path / "clip.srt").write_text("hand-corrected", encoding="utf-8")
        assert wss._free_srt_path(tmp_path / "clip.srt") == tmp_path / "clip_1.srt"

    def test_a6_suffix_increments(self, tmp_path):
        (tmp_path / "clip.srt").write_text("a", encoding="utf-8")
        (tmp_path / "clip_1.srt").write_text("b", encoding="utf-8")
        assert wss._free_srt_path(tmp_path / "clip.srt") == tmp_path / "clip_2.srt"

    def test_a6_existing_subtitles_survive_a_new_run(self, tmp_path):
        source = _video(tmp_path)
        existing = tmp_path / "clip.srt"
        existing.write_text("1\n00:00:00,000 --> 00:00:01,000\nĐừng xoá tôi\n", encoding="utf-8")

        def _fake_run(cmd, *_a, **_kw):
            af = next(p for p in cmd if isinstance(p, str) and p.startswith("whisper="))
            out = re.search(r"destination='([^']+)'", af).group(1)
            Path(out).write_text("0\n00:00:00,000 --> 00:00:02,000\nxin chào\n", encoding="utf-8")

        with (
            patch.object(wss, "is_whisper_supported", return_value=True),
            patch.object(wss, "ensure_model", lambda *_a, **_k: tmp_path / "model.bin"),
            patch.object(wss, "ensure_vad_model", lambda: None),
            patch(
                "app.services.ffmpeg_convert_service.FfmpegConvertService._run_ffmpeg",
                side_effect=_fake_run,
            ),
        ):
            out = wss.generate_srt(Path("ffmpeg"), source, tmp_path / "clip.srt")

        assert out == tmp_path / "clip_1.srt"
        assert existing.read_text(encoding="utf-8").strip().endswith("Đừng xoá tôi")
        assert "xin chào" in out.read_text(encoding="utf-8")
        # _renumber_srt still ran on the new file: whisper emits a 0-based index.
        assert out.read_text(encoding="utf-8").startswith("1\n")


# ── A8: no server paths in SSE ───────────────────────────────────────────────


class TestSsePayload:
    def test_a8_subtitle_filename_is_a_basename(self, tmp_path):
        from app.event_bus import EventBus

        bus = EventBus()
        job = ConversionJob(subtitles_only=True)
        job.subtitle_filename = str(tmp_path / "nested" / "clip.srt")

        seen: list[dict] = []
        srv._wire_event_bus(bus)
        try:
            with patch.object(srv, "_broadcast", lambda _evt, data: seen.append(data)):
                bus.publish_convert_completed(job=job)
        finally:
            srv._unwire_bus()

        assert seen, "convert_completed was not broadcast"
        assert seen[-1]["subtitle_filename"] == "clip.srt"
        assert str(tmp_path) not in seen[-1]["subtitle_filename"]
        assert seen[-1]["subtitles_only"] is True
