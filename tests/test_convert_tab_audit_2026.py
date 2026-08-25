"""Regression guards for the 2026-08 Convert tab / Web API audit.

Each test names the defect it locks down; all of them fail on the code as it
stood before the audit fixes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import remote_convert_service as rcs
from app.services.ffmpeg_convert_service import (
    _HW_ENCODER_CATALOG,
    EncodeSettings,
    FfmpegConvertService,
    _crf_to_percent_quality,
)

_WEB_UI = Path(__file__).resolve().parents[1] / "api" / "static" / "index.html"


# ── BUG 1: custom CRF fed verbatim to percent-scale GPU encoders ─────────────


@pytest.mark.parametrize("key", ["videotoolbox", "mf", "mf_hevc", "mf_av1"])
def test_percent_scale_encoders_are_flagged(key):
    assert _HW_ENCODER_CATALOG[key].percent_quality is True


@pytest.mark.parametrize("key", ["nvenc", "qsv", "amf", "nvenc_hevc", "qsv_av1"])
def test_crf_scale_encoders_are_not_flagged(key):
    assert _HW_ENCODER_CATALOG[key].percent_quality is False


def test_low_crf_becomes_high_percent_quality():
    """CRF 16 means "near-lossless"; on a 0-100 scale that is a HIGH number."""
    spec = _HW_ENCODER_CATALOG["videotoolbox"]
    assert int(_crf_to_percent_quality(16, spec)) > int(spec.quality_values["high"])


def test_high_crf_becomes_low_percent_quality():
    spec = _HW_ENCODER_CATALOG["videotoolbox"]
    assert int(_crf_to_percent_quality(35, spec)) < int(spec.quality_values["small"])


def test_percent_quality_is_clamped_to_the_ffmpeg_range():
    spec = _HW_ENCODER_CATALOG["mf"]
    assert 0 <= int(_crf_to_percent_quality(0, spec)) <= 100
    assert 0 <= int(_crf_to_percent_quality(51, spec)) <= 100


def test_custom_quality_is_inverted_for_percent_encoders():
    """A custom CRF of 16 must not reach h264_mf as "-quality 16"."""
    flags = FfmpegConvertService._build_gpu_flags(
        _HW_ENCODER_CATALOG["mf"],
        EncodeSettings(encoder_key="mf", quality="custom", custom_quality=16),
    )
    value = flags[flags.index("-quality") + 1]
    assert int(value) >= 80, f"CRF 16 became quality {value}/100"


def test_custom_quality_passes_through_for_crf_encoders():
    flags = FfmpegConvertService._build_gpu_flags(
        _HW_ENCODER_CATALOG["nvenc"],
        EncodeSettings(encoder_key="nvenc", quality="custom", custom_quality=16),
    )
    assert flags[flags.index("-cq") + 1] == "16"


def test_named_tiers_are_untouched_by_the_inversion():
    for quality in ("high", "standard", "small"):
        flags = FfmpegConvertService._build_gpu_flags(
            _HW_ENCODER_CATALOG["mf"],
            EncodeSettings(encoder_key="mf", quality=quality),
        )
        expected = _HW_ENCODER_CATALOG["mf"].quality_values[quality]
        assert flags[flags.index("-quality") + 1] == expected


# ── BUG 2: encoder_key reported after a silent CPU fallback ──────────────────


def test_effective_key_is_cpu_when_the_gpu_lacks_the_codec():
    """VideoToolbox has no AV1 entry, so _build_cmd emits the CPU AV1 encoder."""
    settings = EncodeSettings(encoder_key="videotoolbox", output_codec="av1")
    assert FfmpegConvertService._effective_encoder_key(settings) == "cpu"


def test_effective_key_survives_a_supported_combination():
    settings = EncodeSettings(encoder_key="nvenc", output_codec="hevc")
    assert FfmpegConvertService._effective_encoder_key(settings) == "nvenc"


def test_effective_key_of_plain_cpu_settings():
    assert FfmpegConvertService._effective_encoder_key(None) == "cpu"
    assert FfmpegConvertService._effective_encoder_key(EncodeSettings()) == "cpu"


def test_effective_key_matches_the_flags_build_cmd_actually_emits(tmp_path):
    """The reported key must agree with the encoder in the FFmpeg command."""
    settings = EncodeSettings(encoder_key="videotoolbox", output_codec="av1")
    cmd = FfmpegConvertService._build_cmd(
        Path("ffmpeg"),
        tmp_path / "in.mp4",
        tmp_path / "out.part.mp4",
        {"crf": "23", "preset": "fast", "audio_b": "128k", "scale": None},
        encode_settings=settings,
    )
    codec = cmd[cmd.index("-c:v") + 1]
    assert not codec.endswith("videotoolbox")
    assert FfmpegConvertService._effective_encoder_key(settings) == "cpu"


# ── BUG 3: non-media source accepted by the convert endpoints ────────────────


def _service():
    return rcs.RemoteConvertService(config=object(), event_bus=object())


@pytest.mark.parametrize("name", ["photo.jpg", "bundle.zip", "notes.txt", "subs.srt"])
def test_non_media_source_is_rejected(tmp_path, name):
    svc = _service()
    src = tmp_path / name
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="not a convertible media file"):
        svc.start_convert_from_path(file_path=src)


def test_video_source_is_still_accepted(tmp_path, monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc._queue, "submit", lambda **kw: lambda: None)
    src = tmp_path / "clip.mkv"
    src.write_bytes(b"x")
    job = svc.start_convert_from_path(file_path=src)
    assert job.job_id


def test_audio_source_is_accepted_for_mp3_extraction(tmp_path, monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc._queue, "submit", lambda **kw: lambda: None)
    src = tmp_path / "podcast.m4a"
    src.write_bytes(b"x")
    job = svc.start_convert_from_path(file_path=src, target_ext="mp3")
    assert job.job_id


def test_audio_source_is_rejected_for_a_video_target(tmp_path):
    svc = _service()
    src = tmp_path / "podcast.m4a"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="not a convertible media file"):
        svc.start_convert_from_path(file_path=src, target_ext="mp4")


# ── BUG 4: codec the target container cannot hold ────────────────────────────


def test_av1_into_mov_is_rejected_before_the_encode(tmp_path):
    """The MOV muxer refuses AV1; catching it here saves a full wasted encode."""
    svc = _service()
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="cannot be stored in a .mov file"):
        svc.start_convert_from_path(file_path=src, target_ext="mov", output_codec="av1")


def test_h264_into_mov_is_still_allowed(tmp_path, monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc._queue, "submit", lambda **kw: lambda: None)
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    job = svc.start_convert_from_path(file_path=src, target_ext="mov", output_codec="h264")
    assert job.job_id


# ── BUG 5 / 6: Web UI regressions ────────────────────────────────────────────


def test_queue_card_renders_a_failed_conversion():
    """A FAILED convert job used to leave no trace on the queue card."""
    html = _WEB_UI.read_text(encoding="utf-8")
    assert "const failedJob = Object.values(convertJobs).find(" in html
    assert "j.status === 'FAILED'" in html
    assert "cv.job.convert_failed" in html


def test_file_browser_hides_convert_for_non_media_files():
    html = _WEB_UI.read_text(encoding="utf-8")
    m = re.search(r"const isConvertible = (/[^/]+/i)\.test\(item\.name\);", html)
    assert m, "isConvertible guard missing from renderFileBrowser"
    pattern = m.group(1)
    for ext in ("mp4", "mkv", "mov", "ts", "flv"):
        assert ext in pattern
    for ext in ("jpg", "zip", "srt", "txt"):
        assert f"|{ext}" not in pattern


def test_file_browser_keeps_delete_for_non_media_files():
    """Dropping the Convert button must not take the Delete button with it."""
    html = _WEB_UI.read_text(encoding="utf-8")
    assert "grid-template-columns:${isConvertible ? '1fr auto' : '1fr'}" in html
