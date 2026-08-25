"""
tests/test_api_ffmpeg_901.py

Web API half of the FFmpeg 9.0.1 audit fixes.

The desktop app and the iPhone PWA share one convert pipeline, so every fix has
to hold on both sides.  These tests cover the API-visible behaviour:

  • an iPhone can no longer request a codec the server's FFmpeg cannot encode
  • GET /api/convert/codecs reports what is actually available
  • generate_subtitles / subtitle_language / compute_vmaf are validated and
    carried through to EncodeSettings
  • the job snapshot and the ConvertJobResponse expose the new fields
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from api.models import (
    CodecOption,
    ConvertCapabilities,
    ConvertJobResponse,
    ConvertRequest,
    SubtitleModelOption,
)
from app.services import remote_convert_service as rcs
from app.services.remote_convert_service import RemoteConvertService
from domain.models.conversion_job import ConversionJob, ConversionStatus


@pytest.fixture
def svc(tmp_path: Path) -> RemoteConvertService:
    config = MagicMock()
    config.download_dir = tmp_path
    service = RemoteConvertService(config=config, event_bus=MagicMock())
    service._queue = MagicMock()
    service._queue.submit.return_value = lambda: None
    return service


def _src(tmp_path: Path) -> Path:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 64)
    return f


def _settings(svc: RemoteConvertService):
    """Return the EncodeSettings the service handed to ConvertQueue.submit()."""
    return svc._queue.submit.call_args.kwargs["encode_settings"]


# ── Codec allowlist is now derived from the real binary ──────────────────────


class TestCodecValidation:
    def test_av1_rejected_when_build_cannot_encode_it(self, svc, tmp_path):
        """P0: the shipped Windows build has no SVT-AV1 — accepting av1 handed
        the user a job that was guaranteed to fail."""
        with patch.object(rcs, "_allowed_codecs", return_value=frozenset({"h264", "hevc"})):
            with pytest.raises(ValueError, match="not supported by the FFmpeg build"):
                svc.start_convert("t1", _src(tmp_path), output_codec="av1")

    def test_av1_accepted_when_build_supports_it(self, svc, tmp_path):
        with patch.object(rcs, "_allowed_codecs", return_value=frozenset({"h264", "av1"})):
            job = svc.start_convert("t1", _src(tmp_path), output_codec="av1")
        assert job.output_codec == "av1"
        assert _settings(svc).output_codec == "av1"

    def test_unknown_codec_still_rejected(self, svc, tmp_path):
        with patch.object(rcs, "_allowed_codecs", return_value=frozenset({"h264"})):
            with pytest.raises(ValueError, match="not allowed"):
                svc.start_convert("t1", _src(tmp_path), output_codec="h266")

    def test_h264_always_accepted(self, svc, tmp_path):
        with patch.object(rcs, "_allowed_codecs", return_value=frozenset({"h264"})):
            assert svc.start_convert("t1", _src(tmp_path), output_codec="h264") is not None

    def test_probe_failure_degrades_to_h264_only(self):
        with patch.object(rcs, "get_available_codec_options", side_effect=OSError("ffmpeg gone")):
            assert rcs._allowed_codecs() == frozenset({"h264"})

    def test_allowed_codecs_reads_the_shared_probe(self):
        with patch.object(
            rcs,
            "get_available_codec_options",
            return_value=[("h264", "H"), ("hevc", "X")],
        ):
            assert rcs._allowed_codecs() == frozenset({"h264", "hevc"})


# ── Subtitle parameters ──────────────────────────────────────────────────────


class TestSubtitleValidation:
    def test_language_outside_allowlist_rejected(self, svc, tmp_path):
        """subtitle_language is interpolated into an FFmpeg filter string."""
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            with pytest.raises(ValueError, match="subtitle_language"):
                svc.start_convert("t1", _src(tmp_path), generate_subtitles=True, subtitle_language="en:x=1")

    def test_rejected_when_build_has_no_whisper(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=False):
            with pytest.raises(ValueError, match="whisper"):
                svc.start_convert("t1", _src(tmp_path), generate_subtitles=True)

    def test_not_rejected_when_subtitles_not_requested(self, svc, tmp_path):
        """A build without whisper must still convert normally."""
        with patch.object(rcs, "is_whisper_supported", return_value=False):
            assert svc.start_convert("t1", _src(tmp_path)) is not None

    def test_flags_reach_encode_settings(self, svc, tmp_path):
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            job = svc.start_convert(
                "t1",
                _src(tmp_path),
                generate_subtitles=True,
                subtitle_language="vi",
                compute_vmaf=True,
            )
        settings = _settings(svc)
        assert settings.generate_subtitles is True
        assert settings.subtitle_language == "vi"
        assert settings.compute_vmaf is True
        assert job.generate_subtitles is True
        assert job.subtitle_language == "vi"

    def test_defaults_are_off(self, svc, tmp_path):
        svc.start_convert("t1", _src(tmp_path))
        settings = _settings(svc)
        assert settings.generate_subtitles is False
        assert settings.compute_vmaf is False

    def test_on_result_callback_populates_job(self, svc, tmp_path):
        from app.services.ffmpeg_convert_service import ConvertResult

        job = svc.start_convert("t1", _src(tmp_path))
        on_result = svc._queue.submit.call_args.kwargs["on_result"]
        on_result(ConvertResult(output=tmp_path / "o.mp4", subtitle_path=tmp_path / "o.srt", vmaf_score=93.1))
        assert job.subtitle_filename.endswith("o.srt")
        assert job.vmaf_score == 93.1
        assert job.subtitle_error == ""

    def test_on_result_records_subtitle_failure(self, svc, tmp_path):
        from app.services.ffmpeg_convert_service import ConvertResult

        job = svc.start_convert("t1", _src(tmp_path))
        svc._queue.submit.call_args.kwargs["on_result"](
            ConvertResult(output=tmp_path / "o.mp4", subtitle_error="no speech")
        )
        assert job.subtitle_error == "no speech"
        assert job.subtitle_filename == ""


# ── Capability reporting ─────────────────────────────────────────────────────


class TestCapabilityReporting:
    def test_get_available_codecs_delegates_to_probe(self, svc):
        with patch.object(rcs, "get_available_codec_options", return_value=[("h264", "H.264")]):
            assert svc.get_available_codecs() == [("h264", "H.264")]

    def test_supports_subtitles_delegates_to_probe(self, svc):
        with patch.object(rcs, "is_whisper_supported", return_value=True):
            assert svc.supports_subtitles() is True

    def test_subtitle_languages_are_sorted_and_include_auto(self, svc):
        langs = svc.get_subtitle_languages()
        assert langs == sorted(langs)
        assert "auto" in langs and "vi" in langs

    def test_capabilities_model_serialises(self):
        payload = ConvertCapabilities(
            codecs=[CodecOption(key="h264", label="H.264")],
            subtitles=True,
            subtitle_languages=["auto", "vi"],
            subtitle_models=[SubtitleModelOption(key="base", label="Base", size_mb=141)],
        ).model_dump()
        assert payload["codecs"][0]["key"] == "h264"
        assert payload["subtitles"] is True
        assert payload["subtitle_models"][0]["key"] == "base"

    def test_get_subtitle_models_smallest_first(self, svc):
        models = svc.get_subtitle_models()
        keys = [k for k, _, _ in models]
        assert keys[0] == "tiny"
        assert "base" in keys


# ── Request / response models ────────────────────────────────────────────────


class TestApiModels:
    def test_convert_request_defaults_keep_new_passes_off(self):
        req = ConvertRequest()
        assert req.generate_subtitles is False
        assert req.compute_vmaf is False
        assert req.subtitle_language == "auto"

    def test_convert_request_accepts_new_fields(self):
        req = ConvertRequest(generate_subtitles=True, subtitle_language="vi", compute_vmaf=True)
        assert req.generate_subtitles and req.compute_vmaf
        assert req.subtitle_language == "vi"

    def test_job_response_new_fields_default_empty(self):
        resp = ConvertJobResponse(
            job_id="a",
            source_task_id="b",
            encoder_key="cpu",
            quality="standard",
            speed_preset="balanced",
            custom_crf=23,
            status="COMPLETED",
            progress=100.0,
            output_filename="o.mp4",
            error_msg="",
            created_at=0.0,
            finished_at=1.0,
            preview_url="/x",
        )
        assert resp.subtitle_filename == ""
        assert resp.subtitle_error == ""
        assert resp.vmaf_score is None

    def test_snapshot_exposes_new_fields(self):
        job = ConversionJob(
            source_task_id="t",
            generate_subtitles=True,
            subtitle_language="vi",
            status=ConversionStatus.COMPLETED,
        )
        job.subtitle_filename = "/d/o.srt"
        job.vmaf_score = 88.4
        snap = job.snapshot()
        assert snap["generate_subtitles"] is True
        assert snap["subtitle_language"] == "vi"
        assert snap["subtitle_filename"] == "/d/o.srt"
        assert snap["vmaf_score"] == 88.4

    def test_snapshot_defaults_are_backward_compatible(self):
        snap = ConversionJob().snapshot()
        assert snap["subtitle_filename"] == ""
        assert snap["vmaf_score"] is None
        assert snap["generate_subtitles"] is False


# ── Route layer ──────────────────────────────────────────────────────────────
# Endpoint functions are invoked directly, matching the test_api_archive.py
# convention (this repo has no httpx dependency for a TestClient).


import asyncio  # noqa: E402
import inspect  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import api.server as srv  # noqa: E402


def _make_app(download_dir: Path, remote_convert=None):
    service = SimpleNamespace(get_all_tasks=lambda: [], analyse_url=lambda url, on_done, on_error: None)
    config = SimpleNamespace(api_token="", download_dir=download_dir, taildrop_target_nodes=[])
    return srv.create_app(service, config, remote_convert=remote_convert)  # type: ignore[arg-type]


def _endpoint(app, path: str, method: str = "GET"):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {path} [{method}] not found")


class TestCodecsRoute:
    def test_route_exists_and_is_authenticated(self, tmp_path):
        app = _make_app(tmp_path)
        endpoint = _endpoint(app, "/api/convert/codecs")
        dep = inspect.signature(endpoint).parameters["_"].default
        assert dep.dependency.__name__ == "_require_auth"

    def test_reports_probe_results(self, tmp_path):
        rc = MagicMock()
        rc.get_available_codecs.return_value = [("h264", "H.264"), ("hevc", "H.265")]
        rc.supports_subtitles.return_value = True
        rc.get_subtitle_languages.return_value = ["auto", "vi"]
        endpoint = _endpoint(_make_app(tmp_path, rc), "/api/convert/codecs")

        result = asyncio.run(endpoint(_=None))

        assert [c.key for c in result.codecs] == ["h264", "hevc"]
        assert result.subtitles is True
        assert result.subtitle_languages == ["auto", "vi"]

    def test_av1_absent_when_binary_cannot_encode_it(self, tmp_path):
        """The iPhone UI must not be able to offer the broken AV1 option."""
        rc = MagicMock()
        rc.get_available_codecs.return_value = [("h264", "H.264")]
        rc.supports_subtitles.return_value = False
        rc.get_subtitle_languages.return_value = ["auto"]
        result = asyncio.run(_endpoint(_make_app(tmp_path, rc), "/api/convert/codecs")(_=None))
        assert "av1" not in [c.key for c in result.codecs]

    def test_503_without_convert_service(self, tmp_path):
        endpoint = _endpoint(_make_app(tmp_path, None), "/api/convert/codecs")
        with pytest.raises(Exception) as exc:
            asyncio.run(endpoint(_=None))
        assert exc.value.status_code == 503


class TestSrtDownloadRoute:
    def _job(self, tmp_path, srt: str = ""):
        return SimpleNamespace(
            status="COMPLETED",
            output_filename=str(tmp_path / "o.mp4"),
            subtitle_filename=srt,
        )

    def test_kind_param_is_constrained_to_video_or_srt(self, tmp_path):
        """kind reaches a filesystem read — it must be an enum, not free text."""
        schema = _make_app(tmp_path).openapi()
        params = schema["paths"]["/api/convert/{job_id}/file"]["get"]["parameters"]
        kind = next(p for p in params if p["name"] == "kind")
        assert kind["schema"]["pattern"] == "^(video|srt)$"
        assert kind["required"] is False

    def test_srt_served_when_present(self, tmp_path):
        srt = tmp_path / "o.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nxin chao\n", encoding="utf-8")
        (tmp_path / "o.mp4").write_bytes(b"\x00")
        rc = MagicMock()
        rc.get_job.return_value = self._job(tmp_path, str(srt))
        endpoint = _endpoint(_make_app(tmp_path, rc), "/api/convert/{job_id}/file")

        resp = asyncio.run(endpoint(job_id="j1", kind="srt", _=None))

        assert Path(resp.path) == srt
        assert "text/plain" in resp.media_type

    def test_404_when_no_subtitles_generated(self, tmp_path):
        rc = MagicMock()
        rc.get_job.return_value = self._job(tmp_path, "")
        endpoint = _endpoint(_make_app(tmp_path, rc), "/api/convert/{job_id}/file")
        with pytest.raises(Exception) as exc:
            asyncio.run(endpoint(job_id="j1", kind="srt", _=None))
        assert exc.value.status_code == 404

    def test_video_kind_is_unchanged_default(self, tmp_path):
        (tmp_path / "o.mp4").write_bytes(b"\x00")
        rc = MagicMock()
        rc.get_job.return_value = self._job(tmp_path)
        endpoint = _endpoint(_make_app(tmp_path, rc), "/api/convert/{job_id}/file")

        resp = asyncio.run(endpoint(job_id="j1", kind="video", _=None))

        assert resp.media_type == "video/mp4"
        assert Path(resp.path).name == "o.mp4"

    def test_srt_outside_download_dir_is_rejected(self, tmp_path):
        """Path-traversal guard must apply to the subtitle path too."""
        outside = tmp_path.parent / "escaped.srt"
        outside.write_text("x", encoding="utf-8")
        rc = MagicMock()
        rc.get_job.return_value = self._job(tmp_path, str(outside))
        endpoint = _endpoint(_make_app(tmp_path, rc), "/api/convert/{job_id}/file")
        with pytest.raises(Exception) as exc:
            asyncio.run(endpoint(job_id="j1", kind="srt", _=None))
        assert exc.value.status_code == 403
