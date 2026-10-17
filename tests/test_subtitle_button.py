"""
tests/test_subtitle_button.py

The "Create Subtitles" action: transcribe a video to a sidecar .srt without
re-encoding anything.  Before this, subtitles could only be produced as a
side-effect of a full conversion, so a user who already had the file they
wanted had to re-encode it just to get an .srt.

Covered here:

  S1  EncodeSettings.subtitles_only short-circuits the encode pipeline
  S2  a failed transcription fails the job (there is no video to fall back on)
  S3  RemoteConvertService.start_subtitles() wiring and validation
  S4  a subtitles-only job reports no video output and skips Taildrop
  S5  POST /api/files/subtitles — path traversal guard and happy path
  S6  POST /api/queue/{task_id}/subtitles — task state guard and happy path
  S7  the web UI has the button and posts to both endpoints
  S8  the desktop tab's "Tạo phụ đề" button submits subtitles-only jobs
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import api.server as srv
import ui.tabs.convert_tab as ct
from app.services import remote_convert_service as rcs
from app.services.ffmpeg_convert_service import (
    ConversionError,
    ConvertResult,
    EncodeSettings,
    FfmpegConvertService,
)
from app.services.remote_convert_service import RemoteConvertService


def _endpoint(app, path: str, method: str = "POST"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


def _call(endpoint, **kwargs):
    """Run an endpoint whether it is sync or async."""
    result = endpoint(**kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.run(result)
    return result


def _video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    f = tmp_path / name
    f.write_bytes(b"\x00" * 64)
    return f


# ── S1 / S2: the convert service's subtitles-only path ───────────────────────


class TestSubtitlesOnlyPipeline:
    def test_s1_default_is_off(self):
        assert EncodeSettings().subtitles_only is False

    def test_s1_no_encode_runs(self, tmp_path):
        """The whole point: no re-encode, no output video, just the .srt."""
        src = _video(tmp_path)
        srt = tmp_path / "clip.srt"
        svc = FfmpegConvertService()
        results: list[ConvertResult] = []

        with (
            patch.object(svc, "_locate_ffmpeg_bin", return_value=Path("ffmpeg")),
            patch.object(svc, "_probe_duration", return_value=12.0),
            patch.object(svc, "_try_encode_with_fallback") as encode,
            patch("app.services.whisper_subtitle_service.generate_srt", return_value=srt) as gen,
        ):
            out = svc._convert_sync(
                src,
                "standard",
                None,
                None,
                EncodeSettings(
                    generate_subtitles=True,
                    subtitle_language="vi",
                    subtitle_model="tiny",
                    subtitles_only=True,
                ),
                on_result=results.append,
            )

        encode.assert_not_called()
        assert out == srt
        assert gen.call_args.kwargs["language"] == "vi"
        assert gen.call_args.kwargs["model_key"] == "tiny"
        assert results[0].subtitle_path == srt

    def test_s1_progress_and_cancel_reach_whisper(self, tmp_path):
        """The .srt pass is the whole job, so it owns the progress bar and the
        Cancel button — both must be forwarded, not swallowed."""
        src = _video(tmp_path)
        svc = FfmpegConvertService()
        cancel = threading.Event()

        def _on_progress(_pct: float) -> None:
            pass

        with (
            patch.object(svc, "_locate_ffmpeg_bin", return_value=Path("ffmpeg")),
            patch.object(svc, "_probe_duration", return_value=1.0),
            patch(
                "app.services.whisper_subtitle_service.generate_srt",
                return_value=tmp_path / "clip.srt",
            ) as gen,
        ):
            svc._convert_sync(
                src,
                "standard",
                None,
                _on_progress,
                EncodeSettings(generate_subtitles=True, subtitles_only=True),
                cancel_event=cancel,
            )

        assert gen.call_args.kwargs["on_progress"] is _on_progress
        assert gen.call_args.kwargs["cancel_event"] is cancel

    def test_s1_output_dir_is_honoured(self, tmp_path):
        src = _video(tmp_path)
        dest = tmp_path / "subs"
        svc = FfmpegConvertService()

        with (
            patch.object(svc, "_locate_ffmpeg_bin", return_value=Path("ffmpeg")),
            patch.object(svc, "_probe_duration", return_value=1.0),
            patch(
                "app.services.whisper_subtitle_service.generate_srt",
                side_effect=lambda *a, **k: a[2],
            ) as gen,
        ):
            svc._convert_sync(
                src, "standard", dest, None, EncodeSettings(generate_subtitles=True, subtitles_only=True)
            )

        assert gen.call_args.args[2] == dest / "clip.srt"
        assert dest.is_dir()

    def test_s2_transcription_failure_fails_the_job(self, tmp_path):
        """In a normal conversion a failed .srt is only a note next to a working
        video.  Here there is no video, so the job itself must fail."""
        src = _video(tmp_path)
        svc = FfmpegConvertService()
        errors: list[str] = []

        with (
            patch.object(svc, "_locate_ffmpeg_bin", return_value=Path("ffmpeg")),
            patch.object(svc, "_probe_duration", return_value=1.0),
            patch(
                "app.services.whisper_subtitle_service.generate_srt",
                side_effect=ConversionError("no speech detected"),
            ),
        ):
            svc._run(
                src,
                "standard",
                None,
                None,
                None,
                errors.append,
                encode_settings=EncodeSettings(generate_subtitles=True, subtitles_only=True),
            )

        assert errors == ["no speech detected"]


# ── S3 / S4: RemoteConvertService ────────────────────────────────────────────


@pytest.fixture
def svc(tmp_path: Path) -> RemoteConvertService:
    config = MagicMock()
    config.download_dir = tmp_path
    service = RemoteConvertService(config=config, event_bus=MagicMock())
    service._queue = MagicMock()
    service._queue.submit.return_value = lambda: None
    return service


def _settings(svc: RemoteConvertService) -> EncodeSettings:
    return svc._queue.submit.call_args.kwargs["encode_settings"]


class TestStartSubtitles:
    def test_s3_submits_a_subtitles_only_job(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            job = svc.start_subtitles(_video(tmp_path), subtitle_language="vi", subtitle_model="tiny")

        settings = _settings(svc)
        assert settings.subtitles_only is True
        assert settings.generate_subtitles is True
        assert settings.subtitle_language == "vi"
        assert settings.subtitle_model == "tiny"
        assert job.source_task_id == ""

    def test_s3_task_id_is_carried_through(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            job = svc.start_subtitles(_video(tmp_path), source_task_id="t42")
        assert job.source_task_id == "t42"

    def test_s3_rejected_without_whisper(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=False):
            with pytest.raises(ValueError, match="whisper"):
                svc.start_subtitles(_video(tmp_path))

    def test_s3_language_allowlist_still_applies(self, svc, tmp_path):
        """The language reaches an FFmpeg filter string — never free-form."""
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            with pytest.raises(ValueError, match="subtitle_language"):
                svc.start_subtitles(_video(tmp_path), subtitle_language="en:model=/etc/passwd")

    def test_s3_model_allowlist_still_applies(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            with pytest.raises(ValueError, match="subtitle_model"):
                svc.start_subtitles(_video(tmp_path), subtitle_model="huge")

    def test_s3_subtitles_only_requires_generate_subtitles(self, svc, tmp_path):
        with pytest.raises(ValueError, match="subtitles_only"):
            svc.start_convert("t1", _video(tmp_path), subtitles_only=True)

    def test_s4_completed_job_reports_no_video_output(self, svc, tmp_path):
        srt = tmp_path / "clip.srt"
        taildrop = MagicMock()
        svc._taildrop = taildrop

        with patch.object(rcs, "is_whisper_supported", return_value=True):
            job = svc.start_subtitles(_video(tmp_path))

        kwargs = svc._queue.submit.call_args.kwargs
        kwargs["on_result"](ConvertResult(output=srt, subtitle_path=srt))
        kwargs["on_done"](srt)

        assert job.output_filename == ""
        assert job.subtitle_filename == str(srt)
        # An .srt is not a converted video; the auto-send hook must stay out.
        taildrop.send_converted_file.assert_not_called()


# ── S5 / S6: the HTTP endpoints ──────────────────────────────────────────────


def _app(tmp_path: Path, remote_convert=None, task=None):
    service = SimpleNamespace(
        get_all_tasks=lambda: [],
        get_history=lambda: [],
        get_task=lambda _tid: task,
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path)
    return srv.create_app(service, config, remote_convert=remote_convert)  # type: ignore[arg-type]


def _fake_job(job_id: str = "j1"):
    """Minimal stand-in for ConversionJob — _job_to_response() reads snapshot()."""
    snap = {
        "job_id": job_id,
        "source_task_id": "",
        "encoder_key": "cpu",
        "quality": "standard",
        "speed_preset": "balanced",
        "custom_crf": 23,
        "output_codec": "h264",
        "status": "PENDING",
        "progress": 0.0,
        "output_filename": "",
        "error_msg": "",
        "created_at": 0.0,
        "finished_at": 0.0,
        "output_deleted": False,
        "subtitle_filename": "",
        "subtitle_error": "",
        "vmaf_score": None,
    }
    return SimpleNamespace(job_id=job_id, snapshot=lambda: snap)


class TestFileSubtitlesEndpoint:
    def test_s5_starts_a_job_for_a_file_inside_download_dir(self, tmp_path):
        src = _video(tmp_path)
        remote = MagicMock()
        remote.start_subtitles.return_value = _fake_job()
        app = _app(tmp_path, remote)

        body = SimpleNamespace(file_path=str(src), subtitle_language="vi", subtitle_model="tiny")
        resp = _call(_endpoint(app, "/api/files/subtitles"), body=body, _=None)

        assert resp.job_id == "j1"
        assert remote.start_subtitles.call_args.kwargs == {
            "file_path": src.resolve(),
            "subtitle_language": "vi",
            "subtitle_model": "tiny",
        }

    def test_s5_path_outside_download_dir_is_rejected(self, tmp_path):
        outside = tmp_path.parent / "escape.mp4"
        outside.write_bytes(b"\x00")
        app = _app(tmp_path / "root", MagicMock())
        body = SimpleNamespace(file_path=str(outside), subtitle_language="auto", subtitle_model="base")

        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/files/subtitles"), body=body, _=None)
        assert exc.value.status_code == 400

    def test_s5_missing_file_is_404(self, tmp_path):
        app = _app(tmp_path, MagicMock())
        body = SimpleNamespace(
            file_path=str(tmp_path / "gone.mp4"), subtitle_language="auto", subtitle_model="base"
        )
        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/files/subtitles"), body=body, _=None)
        assert exc.value.status_code == 404

    def test_s5_directory_is_rejected(self, tmp_path):
        folder = tmp_path / "clips"
        folder.mkdir()
        app = _app(tmp_path, MagicMock())
        body = SimpleNamespace(file_path=str(folder), subtitle_language="auto", subtitle_model="base")
        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/files/subtitles"), body=body, _=None)
        assert exc.value.status_code == 400

    def test_s5_service_validation_error_becomes_422(self, tmp_path):
        src = _video(tmp_path)
        remote = MagicMock()
        remote.start_subtitles.side_effect = ValueError("no whisper filter")
        app = _app(tmp_path, remote)
        body = SimpleNamespace(file_path=str(src), subtitle_language="auto", subtitle_model="base")

        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/files/subtitles"), body=body, _=None)
        assert exc.value.status_code == 422

    def test_s5_503_without_convert_service(self, tmp_path):
        src = _video(tmp_path)
        app = _app(tmp_path, None)
        body = SimpleNamespace(file_path=str(src), subtitle_language="auto", subtitle_model="base")
        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/files/subtitles"), body=body, _=None)
        assert exc.value.status_code == 503


class TestTaskSubtitlesEndpoint:
    def _task(self, filename: Path, status: str = "COMPLETED"):
        return SimpleNamespace(id="t1", filename=str(filename), status=SimpleNamespace(name=status))

    def test_s6_starts_a_job_for_a_completed_task(self, tmp_path):
        src = _video(tmp_path)
        remote = MagicMock()
        remote.start_subtitles.return_value = _fake_job("j2")
        app = _app(tmp_path, remote, task=self._task(src))

        body = SimpleNamespace(subtitle_language="auto", subtitle_model="base")
        resp = _call(_endpoint(app, "/api/queue/{task_id}/subtitles"), task_id="t1", body=body, _=None)

        assert resp.job_id == "j2"
        assert remote.start_subtitles.call_args.kwargs["source_task_id"] == "t1"

    def test_s6_unfinished_task_is_rejected(self, tmp_path):
        src = _video(tmp_path)
        app = _app(tmp_path, MagicMock(), task=self._task(src, status="DOWNLOADING"))
        body = SimpleNamespace(subtitle_language="auto", subtitle_model="base")

        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/queue/{task_id}/subtitles"), task_id="t1", body=body, _=None)
        assert exc.value.status_code == 400

    def test_s6_multi_file_download_picks_the_first_video(self, tmp_path):
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "b.mp4").write_bytes(b"\x00")
        (folder / "a.mp4").write_bytes(b"\x00")
        (folder / "notes.txt").write_text("x")
        remote = MagicMock()
        remote.start_subtitles.return_value = _fake_job()
        app = _app(tmp_path, remote, task=self._task(folder))

        body = SimpleNamespace(subtitle_language="auto", subtitle_model="base")
        _call(_endpoint(app, "/api/queue/{task_id}/subtitles"), task_id="t1", body=body, _=None)

        assert remote.start_subtitles.call_args.kwargs["file_path"] == folder / "a.mp4"

    def test_s6_folder_without_video_is_rejected(self, tmp_path):
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "notes.txt").write_text("x")
        app = _app(tmp_path, MagicMock(), task=self._task(folder))
        body = SimpleNamespace(subtitle_language="auto", subtitle_model="base")

        with pytest.raises(srv.HTTPException) as exc:
            _call(_endpoint(app, "/api/queue/{task_id}/subtitles"), task_id="t1", body=body, _=None)
        assert exc.value.status_code == 400


# ── S7: the web UI ───────────────────────────────────────────────────────────

_INDEX_HTML = Path(__file__).resolve().parent.parent / "api" / "static" / "index.html"


class TestWebUiButton:
    def test_s7_button_exists(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="cvsubs-only-btn"' in html
        assert "submitSubtitles()" in html

    def test_s7_posts_to_both_endpoints(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "'/api/files/subtitles'" in html
        assert "/api/queue/${taskId}/subtitles" in html

    def test_s7_button_disabled_without_server_support(self):
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "subsBtn.disabled = !supported" in html


# ── S8: the desktop tab ──────────────────────────────────────────────────────


class _Btn:
    def __init__(self) -> None:
        self.enabled = True

    def setEnabled(self, v: bool) -> None:
        self.enabled = bool(v)


def _tab_self(jobs):
    ns = SimpleNamespace(
        _jobs=jobs,
        _quality="standard",
        _subtitle_language="vi",
        _subtitle_model="tiny",
        _active_count=0,
        _convert_btn=_Btn(),
        _subs_btn=_Btn(),
        _rebuild_card=MagicMock(),
        _submit_job=MagicMock(),
        _refresh_ui=MagicMock(),
    )
    # Bind the real selection filter rather than stubbing it: these tests are
    # about which files _start_subtitles_only picks up, which is exactly what
    # _selected_pending() decides.
    ns._selected_pending = lambda: ct.ConvertTab._selected_pending(ns)
    return ns


class TestDesktopButton:
    def test_s8_submits_subtitles_only_settings(self, tmp_path):
        job = ct.FileJob(source=_video(tmp_path))
        s = _tab_self({job.id: job})

        ct.ConvertTab._start_subtitles_only(s)

        settings = s._submit_job.call_args.args[2]
        assert settings.subtitles_only is True
        assert settings.generate_subtitles is True
        assert settings.subtitle_language == "vi"
        assert settings.subtitle_model == "tiny"
        assert job.state is ct.FileState.QUEUED
        assert s._active_count == 1
        assert s._subs_btn.enabled is False

    def test_s8_no_pending_files_does_nothing(self, tmp_path):
        job = ct.FileJob(source=_video(tmp_path), state=ct.FileState.DONE)
        s = _tab_self({job.id: job})

        ct.ConvertTab._start_subtitles_only(s)

        s._submit_job.assert_not_called()
        assert s._subs_btn.enabled is True
