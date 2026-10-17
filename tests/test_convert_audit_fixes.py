"""
tests/test_convert_audit_fixes.py

Regression tests for the Convert audit (desktop + Web API).

Every test here pins one bug that was found by reading the convert pipeline
end to end.  Grouped by the layer the bug lived in:

  ffmpeg_convert_service.py
    C1  EncodeSettings.quality was ignored for the preset lookup on the
        ConvertQueue path ("small" lost its 720p downscale, "high"/"small"
        got the wrong audio bitrate).
    C2  target_ext="mp3" skipped the subtitle pass and never fired on_result.
    C3  the opt-in passes only ran when the caller also supplied on_result.
    C4  VMAF still ran a full extra decode after the user pressed cancel.
    C5  resolving encoder_key="auto" rebuilt EncodeSettings field by field and
        dropped the subtitle / VMAF fields.

  whisper_subtitle_service.py
    W1  a cancel during transcription was downgraded to "subtitles failed" and
        the job still reported COMPLETED.

  remote_convert_service.py / api
    A1  target_ext="webm" was accepted although H.264+AAC cannot be remuxed
        into WebM — the job always failed after the full encode.
    A2  compute_vmaf + target_ext="mp3" was accepted and silently dropped.
    A3  POST /api/queue/{task_id}/convert could not pick a container.
    A4  the preview endpoint always sent Content-Type: video/mp4.
    A5  ConversionJob.snapshot() omitted compute_vmaf.

  api/static/index.html
    U1  the codec grid was hard-coded instead of read from /api/convert/codecs.
    U2  a stale _pendingFilePath made the queue sheet convert the wrong file.

No real FFmpeg process is started anywhere in this file.
"""

from __future__ import annotations

import inspect
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import api.server as srv
from app.services import ffmpeg_convert_service as fcs
from app.services import remote_convert_service as rcs
from app.services import whisper_subtitle_service as wss
from app.services.ffmpeg_convert_service import (
    ConversionCancelledError,
    ConvertResult,
    EncodeSettings,
    FfmpegConvertService,
)
from domain.models.conversion_job import ConversionJob

_INDEX_HTML = Path(__file__).resolve().parent.parent / "api" / "static" / "index.html"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _silent_proc() -> MagicMock:
    proc = MagicMock()
    proc.stderr = iter([])
    proc.wait.return_value = None
    proc.returncode = 0
    return proc


def _stub_encode(monkeypatch, tmp_path: Path) -> dict:
    """Make _convert_sync runnable without FFmpeg; return the captured command."""
    captured: dict = {}
    ffmpeg_bin = tmp_path / "ffmpeg"
    ffmpeg_bin.write_bytes(b"")

    def fake_popen(cmd, **_kw):
        captured["cmd"] = list(cmd)
        # cmd[-1] is the temp output path; make it pass _validate_output.
        Path(cmd[-1]).write_bytes(b"0" * 2_000)
        return _silent_proc()

    monkeypatch.setattr(FfmpegConvertService, "_locate_ffmpeg_bin", staticmethod(lambda: ffmpeg_bin))
    monkeypatch.setattr(FfmpegConvertService, "_probe_duration", staticmethod(lambda *_a: 12.0))
    monkeypatch.setattr(fcs, "probe_media_info", lambda _p: None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured


def _source(tmp_path: Path, name: str = "clip.mp4") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00" * 64)
    return p


def _flag(cmd: list[str], name: str) -> str:
    """Return the value that follows *name* in *cmd*.

    Asserting on " ".join(cmd) is unsafe here: pytest's tmp_path contains the
    test's own name, so a test called ..._192k_audio would "find" 192k in the
    directory path and pass even when the flag is wrong.
    """
    assert name in cmd, f"{name} missing from command"
    return cmd[cmd.index(name) + 1]


# ── C1: the preset tier must come from EncodeSettings on every entry path ─────


class TestPresetTierReconciliation:
    """ConvertQueue.submit() forwards its own `quality` arg straight to _run(),
    so the reconciliation has to live in _convert_sync or the queue path uses
    the wrong preset."""

    def test_small_tier_applies_720p_downscale_and_96k_audio(self, tmp_path, monkeypatch):
        captured = _stub_encode(monkeypatch, tmp_path)
        FfmpegConvertService()._convert_sync(
            _source(tmp_path),
            "standard",  # what ConvertQueue.submit() passes by default
            tmp_path,
            None,
            EncodeSettings(quality="small"),
        )
        cmd = captured["cmd"]
        assert "if(gt(ih,720)" in _flag(cmd, "-vf"), "the 'small' preset lost its 720p downscale"
        assert _flag(cmd, "-b:a") == "96k", "the 'small' preset lost its 96k audio bitrate"

    def test_high_tier_applies_192k_audio(self, tmp_path, monkeypatch):
        captured = _stub_encode(monkeypatch, tmp_path)
        FfmpegConvertService()._convert_sync(
            _source(tmp_path), "standard", tmp_path, None, EncodeSettings(quality="high")
        )
        assert _flag(captured["cmd"], "-b:a") == "192k"

    def test_custom_tier_uses_custom_preset_row(self, tmp_path, monkeypatch):
        captured = _stub_encode(monkeypatch, tmp_path)
        FfmpegConvertService()._convert_sync(
            _source(tmp_path),
            "standard",
            tmp_path,
            None,
            EncodeSettings(quality="custom", custom_quality=30),
        )
        cmd = captured["cmd"]
        assert _flag(cmd, "-crf") == "30"
        assert _flag(cmd, "-b:a") == "128k"
        assert "if(gt(ih,720)" not in _flag(cmd, "-vf")

    def test_quality_arg_still_used_without_encode_settings(self, tmp_path, monkeypatch):
        captured = _stub_encode(monkeypatch, tmp_path)
        FfmpegConvertService()._convert_sync(_source(tmp_path), "small", tmp_path, None, None)
        assert "if(gt(ih,720)" in _flag(captured["cmd"], "-vf")

    def test_queue_submit_reaches_the_same_command(self, tmp_path, monkeypatch):
        """End-to-end through ConvertQueue, the path the desktop actually uses."""
        captured = _stub_encode(monkeypatch, tmp_path)
        done = threading.Event()
        queue = fcs.ConvertQueue(max_concurrent=1)
        queue.submit(
            source=_source(tmp_path),
            quality="standard",
            output_dir=tmp_path,
            encode_settings=EncodeSettings(quality="small"),
            on_done=lambda _p: done.set(),
            on_error=lambda _m: done.set(),
        )
        assert done.wait(timeout=20), "conversion worker never finished"
        assert "if(gt(ih,720)" in _flag(captured["cmd"], "-vf")
        assert _flag(captured["cmd"], "-b:a") == "96k"


# ── C2 / C3: the opt-in passes ────────────────────────────────────────────────


class TestPostPasses:
    def test_mp3_target_fires_on_result(self, tmp_path, monkeypatch):
        _stub_encode(monkeypatch, tmp_path)
        results: list[ConvertResult] = []
        out = FfmpegConvertService()._convert_sync(
            _source(tmp_path), "standard", tmp_path, None, target_ext="mp3", on_result=results.append
        )
        assert out.suffix == ".mp3"
        assert results and results[0].output == out

    def test_mp3_target_still_generates_subtitles(self, tmp_path, monkeypatch):
        _stub_encode(monkeypatch, tmp_path)
        calls: list[Path] = []

        def fake_srt(_bin, source, dest, **_kw):
            calls.append(source)
            dest.write_text("1\n", encoding="utf-8")
            return dest

        monkeypatch.setattr(wss, "generate_srt", fake_srt)
        results: list[ConvertResult] = []
        FfmpegConvertService()._convert_sync(
            _source(tmp_path),
            "standard",
            tmp_path,
            None,
            EncodeSettings(generate_subtitles=True),
            target_ext="mp3",
            on_result=results.append,
        )
        assert calls, "subtitle pass was skipped for an MP3 target"
        assert results[0].subtitle_path is not None

    def test_mp3_target_never_runs_vmaf(self, tmp_path, monkeypatch):
        """An MP3 has no video stream, so VMAF cannot mean anything."""
        _stub_encode(monkeypatch, tmp_path)
        monkeypatch.setattr(wss, "generate_srt", lambda _b, _s, d, **_k: d)
        vmaf = MagicMock(return_value=98.0)
        monkeypatch.setattr(fcs, "compute_vmaf", vmaf)
        FfmpegConvertService()._convert_sync(
            _source(tmp_path),
            "standard",
            tmp_path,
            None,
            EncodeSettings(compute_vmaf=True),
            target_ext="mp3",
        )
        vmaf.assert_not_called()

    def test_passes_run_without_an_on_result_callback(self, tmp_path, monkeypatch):
        """Asking for subtitles must produce subtitles even when the caller
        only wired on_done."""
        _stub_encode(monkeypatch, tmp_path)
        calls: list[Path] = []
        monkeypatch.setattr(wss, "generate_srt", lambda _b, s, d, **_k: (calls.append(s), d)[1])
        FfmpegConvertService()._convert_sync(
            _source(tmp_path), "standard", tmp_path, None, EncodeSettings(generate_subtitles=True)
        )
        assert calls, "subtitle pass never ran because on_result was not supplied"

    def test_no_passes_requested_still_reports_the_output(self, tmp_path, monkeypatch):
        _stub_encode(monkeypatch, tmp_path)
        results: list[ConvertResult] = []
        out = FfmpegConvertService()._convert_sync(
            _source(tmp_path), "standard", tmp_path, None, EncodeSettings(), on_result=results.append
        )
        assert results and results[0].output == out
        assert results[0].subtitle_path is None


# ── C4: cancel must stop the VMAF pass ────────────────────────────────────────


class TestVmafCancel:
    def test_vmaf_skipped_when_cancel_requested(self, tmp_path, monkeypatch):
        vmaf = MagicMock(return_value=95.0)
        monkeypatch.setattr(fcs, "compute_vmaf", vmaf)
        cancel = threading.Event()
        cancel.set()
        result = FfmpegConvertService()._run_post_passes(
            tmp_path / "ffmpeg",
            _source(tmp_path),
            _source(tmp_path, "out.mp4"),
            10.0,
            EncodeSettings(compute_vmaf=True),
            cancel,
        )
        vmaf.assert_not_called()
        assert result.vmaf_score is None

    def test_vmaf_runs_without_cancel(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fcs, "compute_vmaf", MagicMock(return_value=95.0))
        result = FfmpegConvertService()._run_post_passes(
            tmp_path / "ffmpeg",
            _source(tmp_path),
            _source(tmp_path, "out.mp4"),
            10.0,
            EncodeSettings(compute_vmaf=True),
            None,
        )
        assert result.vmaf_score == 95.0


# ── C5: "auto" must not drop EncodeSettings fields ────────────────────────────


class TestAutoEncoderResolution:
    def test_auto_preserves_subtitle_and_vmaf_fields(self, tmp_path, monkeypatch):
        seen: list[EncodeSettings] = []

        def fake_fresh(_self, *_a, encode_settings=None, **_kw):
            seen.append(encode_settings)
            return tmp_path / "out.mp4"

        monkeypatch.setattr(FfmpegConvertService, "_fresh_encode", fake_fresh)
        monkeypatch.setattr(fcs, "get_available_encoder_options", lambda: [("cpu", "CPU")])
        FfmpegConvertService()._try_encode_with_fallback(
            tmp_path / "ffmpeg",
            _source(tmp_path),
            tmp_path,
            tmp_path / "tmp.part.mp4",
            10.0,
            fcs._PRESETS["standard"],
            None,
            EncodeSettings(
                encoder_key="auto",
                generate_subtitles=True,
                subtitle_language="vi",
                compute_vmaf=True,
            ),
        )
        assert seen[0].generate_subtitles is True
        assert seen[0].subtitle_language == "vi"
        assert seen[0].compute_vmaf is True
        assert seen[0].encoder_key == "cpu"


# ── W1: cancel during transcription ───────────────────────────────────────────


class TestSubtitleCancel:
    def _stub_whisper(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wss, "is_whisper_supported", lambda _b=None: True)
        monkeypatch.setattr(wss, "ensure_model", lambda *_a, **_k: tmp_path / "ggml-base.bin")
        monkeypatch.setattr(wss, "ensure_vad_model", lambda: None)

    def test_cancel_propagates_instead_of_becoming_subtitle_error(self, tmp_path, monkeypatch):
        self._stub_whisper(monkeypatch, tmp_path)

        def boom(*_a, **_kw):
            raise ConversionCancelledError("Đã huỷ")

        monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(boom))
        with pytest.raises(ConversionCancelledError):
            wss.generate_srt(tmp_path / "ffmpeg", _source(tmp_path), tmp_path / "out.srt")

    def test_real_failure_is_still_wrapped_as_subtitle_error(self, tmp_path, monkeypatch):
        self._stub_whisper(monkeypatch, tmp_path)

        def boom(*_a, **_kw):
            raise fcs.ConversionError("ffmpeg thoát với lỗi 1")

        monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(boom))
        with pytest.raises(wss.SubtitleError):
            wss.generate_srt(tmp_path / "ffmpeg", _source(tmp_path), tmp_path / "out.srt")

    def test_gpu_failure_falls_back_to_cpu_and_succeeds(self, tmp_path, monkeypatch):
        """BUG: ffmpeg's whisper filter defaults to Vulkan GPU offload, which
        crashes (exit 3221225477 / 0xC0000005, ErrorOutOfDeviceMemory) on
        machines without enough VRAM. The first attempt must retry on CPU
        instead of failing the whole subtitle job."""
        self._stub_whisper(monkeypatch, tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd, *_a, **_kw):
            calls.append(cmd)
            if len(calls) == 1:
                raise fcs.ConversionError("ffmpeg thoát với lỗi 3221225477")
            # tmp_srt carries a UUID suffix (concurrent-job safety) — read the
            # real destination out of the whisper filter string instead of
            # assuming a fixed name.
            af = next(p for p in cmd if isinstance(p, str) and p.startswith("whisper="))
            dest = af.split("destination='")[1].split("'")[0]
            Path(dest).write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

        monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(fake_run))
        out = wss.generate_srt(tmp_path / "ffmpeg", _source(tmp_path), tmp_path / "out.srt")

        assert out.is_file()
        assert len(calls) == 2
        first_af = calls[0][calls[0].index("-af") + 1]
        second_af = calls[1][calls[1].index("-af") + 1]
        assert "use_gpu=false" not in first_af
        assert "use_gpu=false" in second_af

    def test_cpu_failure_does_not_retry_and_is_wrapped(self, tmp_path, monkeypatch):
        self._stub_whisper(monkeypatch, tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd, *_a, **_kw):
            calls.append(cmd)
            raise fcs.ConversionError("ffmpeg thoát với lỗi 1")

        monkeypatch.setattr(FfmpegConvertService, "_run_ffmpeg", staticmethod(fake_run))
        with pytest.raises(wss.SubtitleError):
            wss.generate_srt(tmp_path / "ffmpeg", _source(tmp_path), tmp_path / "out.srt")

        assert len(calls) == 2  # GPU attempt, then CPU retry — both fail, no third try

    def test_cancelled_job_reports_cancelled_not_completed(self, tmp_path, monkeypatch):
        """_run_post_passes must let the cancel through so _run() reports it."""
        monkeypatch.setattr(wss, "generate_srt", MagicMock(side_effect=ConversionCancelledError("Đã huỷ")))
        with pytest.raises(ConversionCancelledError):
            FfmpegConvertService()._run_post_passes(
                tmp_path / "ffmpeg",
                _source(tmp_path),
                _source(tmp_path, "out.mp4"),
                10.0,
                EncodeSettings(generate_subtitles=True),
                None,
            )


# ── A1 / A2: remote validation ────────────────────────────────────────────────


@pytest.fixture
def remote_svc(tmp_path: Path) -> rcs.RemoteConvertService:
    config = MagicMock()
    config.download_dir = tmp_path
    service = rcs.RemoteConvertService(config=config, event_bus=MagicMock())
    service._queue = MagicMock()
    service._queue.submit.return_value = lambda: None
    return service


class TestRemoteValidation:
    def test_webm_rejected(self, remote_svc, tmp_path):
        with pytest.raises(ValueError, match="target_ext"):
            remote_svc.start_convert("t1", _source(tmp_path), target_ext="webm")

    def test_webm_absent_from_allowlist(self):
        assert "webm" not in rcs._VALID_EXTS

    def test_supported_containers_still_accepted(self, remote_svc, tmp_path):
        for ext in ("mp4", "mkv", "mov", "avi", "mp3"):
            job = remote_svc.start_convert("t1", _source(tmp_path), target_ext=ext)
            assert job.job_id

    def test_vmaf_with_mp3_rejected(self, remote_svc, tmp_path):
        with pytest.raises(ValueError, match="compute_vmaf"):
            remote_svc.start_convert("t1", _source(tmp_path), target_ext="mp3", compute_vmaf=True)

    def test_vmaf_with_video_container_accepted(self, remote_svc, tmp_path):
        job = remote_svc.start_convert("t1", _source(tmp_path), target_ext="mkv", compute_vmaf=True)
        assert job.compute_vmaf is True


class TestRequestModels:
    def test_convert_request_has_target_ext(self):
        from api.models import ConvertRequest

        assert ConvertRequest().target_ext == "mp4"
        assert ConvertRequest(target_ext="mp3").target_ext == "mp3"

    def test_convert_request_rejects_webm(self):
        from api.models import ConvertRequest

        with pytest.raises(ValueError):
            ConvertRequest(target_ext="webm")

    def test_file_convert_request_rejects_webm(self):
        from api.models import FileConvertRequest

        with pytest.raises(ValueError):
            FileConvertRequest(file_path="/tmp/a.mp4", target_ext="webm")


# ── A3 / A4: endpoint wiring ──────────────────────────────────────────────────


def _make_app(tmp_path: Path, remote_convert, task=None):
    task = task or SimpleNamespace(
        id="t1",
        status=SimpleNamespace(name="COMPLETED"),
        filename=str(tmp_path / "clip.mp4"),
        save_dir=str(tmp_path),
    )
    service = SimpleNamespace(
        get_all_tasks=lambda: [task],
        get_task=lambda _tid: task,
        analyse_url=lambda url, on_done, on_error: None,
    )
    config = SimpleNamespace(api_token="", download_dir=tmp_path, taildrop_target_nodes=[])
    return srv.create_app(service, config, remote_convert=remote_convert)  # type: ignore[arg-type]


def _endpoint(app, path: str, method: str = "POST"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} not found")


class TestQueueConvertTargetExt:
    def test_target_ext_is_forwarded(self, tmp_path):
        from api.models import ConvertRequest

        _source(tmp_path)
        remote = MagicMock()
        remote.get_all_jobs.return_value = []
        remote.start_convert.return_value = ConversionJob(job_id="j1", source_task_id="t1")
        app = _make_app(tmp_path, remote)
        endpoint = _endpoint(app, "/api/queue/{task_id}/convert")
        endpoint("t1", ConvertRequest(target_ext="mp3"), None)
        assert remote.start_convert.call_args.kwargs["target_ext"] == "mp3"

    def test_default_is_still_mp4(self, tmp_path):
        from api.models import ConvertRequest

        _source(tmp_path)
        remote = MagicMock()
        remote.get_all_jobs.return_value = []
        remote.start_convert.return_value = ConversionJob(job_id="j1", source_task_id="t1")
        app = _make_app(tmp_path, remote)
        endpoint = _endpoint(app, "/api/queue/{task_id}/convert")
        endpoint("t1", ConvertRequest(), None)
        assert remote.start_convert.call_args.kwargs["target_ext"] == "mp4"


class TestPreviewMediaType:
    def test_media_type_map_covers_every_container(self):
        """The preview endpoint must not hard-code video/mp4 any more."""
        src = inspect.getsource(srv.create_app)
        assert "_CONVERT_MEDIA_TYPES" in src
        for suffix, mime in (
            (".mkv", "video/x-matroska"),
            (".mov", "video/quicktime"),
            (".avi", "video/x-msvideo"),
            (".mp3", "audio/mpeg"),
        ):
            assert f'"{suffix}": "{mime}"' in src
        assert 'media_type, disposition = "video/mp4", "inline"' not in src


# ── A5: snapshot completeness ─────────────────────────────────────────────────


class TestSnapshot:
    def test_compute_vmaf_is_exposed(self):
        job = ConversionJob(compute_vmaf=True)
        assert job.snapshot()["compute_vmaf"] is True


# ── U1 / U2: the iPhone web UI ────────────────────────────────────────────────


class TestWebUiConvertSheet:
    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_codec_grid_is_populated_from_the_server(self):
        html = self._html()
        assert "/api/convert/codecs" in html, "the codec capability endpoint is never called"
        assert "populateConvertOptions" in html

    def test_both_sheets_use_the_shared_populate_helper(self):
        html = self._html()
        assert html.count("populateConvertOptions()") >= 2

    def test_queue_sheet_clears_the_pending_file_path(self):
        html = self._html()
        start = html.index("async function openConvertSheet(")
        body = html[start : html.index("function closeConvertSheet(")]
        assert "_pendingFilePath      = null;" in body or "_pendingFilePath = null;" in body
