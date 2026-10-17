"""
tests/test_ffmpeg_901_fixes.py

Regression tests for the FFmpeg 9.0.1 audit fixes:

  P0  AV1 was offered in the UI and the Web API while the shipped FFmpeg build
      (gyan.dev "essentials") has no libsvtav1, so every CPU AV1 conversion
      failed with "Encoder not found" and had no fallback.
  P1  Picking a codec a GPU has no encoder for silently produced H.264 instead
      of the requested codec.
  NEW MediaFoundation as a vendor-neutral GPU fallback on Windows.
  NEW VMAF quality scoring.
  NEW Whisper auto-subtitles (model management, filter construction, escaping).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import whisper_subtitle_service as wss
from app.services.ffmpeg_convert_service import (
    _CODEC_CPU_ENCODERS,
    _HW_ENCODER_CATALOG,
    CODEC_OPTIONS,
    ConvertResult,
    EncodeSettings,
    FfmpegConvertService,
    compute_vmaf,
    detect_available_codecs,
    get_available_codec_options,
    resolve_cpu_encoder,
)

_PRESET = {"scale": "", "audio_b": "128k", "crf": "23", "preset": "fast", "label": "x"}


def _cmd(encoder_key: str, codec: str, **kw) -> list[str]:
    settings = EncodeSettings(encoder_key=encoder_key, output_codec=codec, **kw)
    return FfmpegConvertService._build_cmd(
        Path("ffmpeg"), Path("in.mp4"), Path("out.mp4"), _PRESET, encode_settings=settings
    )


def _codec_of(cmd: list[str]) -> str:
    return cmd[cmd.index("-c:v") + 1]


# ── P0: AV1 must never emit an encoder the binary does not have ───────────────


class TestAv1EncoderSelection:
    def test_av1_uses_libsvtav1_when_available(self):
        with patch("app.services.ffmpeg_convert_service.resolve_cpu_encoder", return_value="libsvtav1"):
            cmd = _cmd("cpu", "av1")
        assert _codec_of(cmd) == "libsvtav1"
        assert "-preset" in cmd

    def test_av1_falls_back_to_libaom_on_essentials_build(self):
        """The shipped build has no SVT-AV1; libaom must be used instead."""
        with patch("app.services.ffmpeg_convert_service.resolve_cpu_encoder", return_value="libaom-av1"):
            cmd = _cmd("cpu", "av1")
        assert _codec_of(cmd) == "libaom-av1"
        # -b:v 0 is mandatory: without it libaom ignores -crf and encodes at a
        # default bitrate, silently producing the wrong quality.
        assert cmd[cmd.index("-b:v") + 1] == "0"
        assert "-cpu-used" in cmd
        assert "-crf" in cmd

    def test_libaom_speed_presets_are_distinct(self):
        seen = set()
        for speed in ("quality", "balanced", "fast"):
            with patch(
                "app.services.ffmpeg_convert_service.resolve_cpu_encoder",
                return_value="libaom-av1",
            ):
                cmd = _cmd("cpu", "av1", speed_preset=speed)
            seen.add(cmd[cmd.index("-cpu-used") + 1])
        assert len(seen) == 3

    def test_custom_quality_reaches_crf(self):
        with patch("app.services.ffmpeg_convert_service.resolve_cpu_encoder", return_value="libaom-av1"):
            cmd = _cmd("cpu", "av1", quality="custom", custom_quality=41)
        assert cmd[cmd.index("-crf") + 1] == "41"


class TestCodecDetection:
    def test_av1_hidden_when_no_av1_encoder_works(self):
        """essentials build: neither libsvtav1 nor libaom-av1 passes the probe."""

        def fake_validate(_bin: Path, codec: str) -> bool:
            return codec in ("libx264", "libx265")

        with patch("app.services.ffmpeg_convert_service._validate_encoder_codec", fake_validate):
            detected = detect_available_codecs(ffmpeg_bin=Path("ffmpeg"))
            opts = get_available_codec_options(ffmpeg_bin=Path("ffmpeg"))

        assert "av1" not in detected
        assert [k for k, _ in opts] == ["h264", "hevc"]

    def test_av1_present_via_libaom_only(self):
        def fake_validate(_bin: Path, codec: str) -> bool:
            return codec != "libsvtav1"

        with patch("app.services.ffmpeg_convert_service._validate_encoder_codec", fake_validate):
            detected = detect_available_codecs(ffmpeg_bin=Path("ffmpeg"))

        assert detected["av1"] == "libaom-av1"

    def test_svtav1_preferred_over_libaom(self):
        with patch("app.services.ffmpeg_convert_service._validate_encoder_codec", return_value=True):
            detected = detect_available_codecs(ffmpeg_bin=Path("ffmpeg"))
        assert detected["av1"] == "libsvtav1"

    def test_h264_always_survives_total_probe_failure(self):
        """A machine that cannot convert at all must still report h264, not {}."""
        with patch("app.services.ffmpeg_convert_service._validate_encoder_codec", return_value=False):
            detected = detect_available_codecs(ffmpeg_bin=Path("ffmpeg"))
        assert detected == {"h264": "libx264"}

    def test_every_codec_option_has_encoder_candidates(self):
        assert {k for k, _ in CODEC_OPTIONS} == set(_CODEC_CPU_ENCODERS)

    def test_resolve_cpu_encoder_falls_back_when_undetected(self):
        with patch("app.services.ffmpeg_convert_service.detect_available_codecs", return_value={}):
            assert resolve_cpu_encoder("av1") == "libsvtav1"
            assert resolve_cpu_encoder("hevc") == "libx265"
            assert resolve_cpu_encoder("nonsense") == "libx264"


# ── P1: a GPU without the requested codec must not silently emit H.264 ────────


class TestGpuCodecMismatch:
    def test_videotoolbox_av1_does_not_silently_become_h264(self):
        with patch("app.services.ffmpeg_convert_service.resolve_cpu_encoder", return_value="libsvtav1"):
            cmd = _cmd("videotoolbox", "av1")
        assert _codec_of(cmd) != "h264_videotoolbox"
        assert _codec_of(cmd) == "libsvtav1"

    def test_videotoolbox_hevc_uses_libx265_not_h264(self):
        cmd = _cmd("videotoolbox", "hevc")
        assert _codec_of(cmd) == "libx265"

    def test_unknown_encoder_still_falls_back_to_libx264(self):
        cmd = _cmd("no_such_gpu", "h264")
        assert _codec_of(cmd) == "libx264"

    @pytest.mark.parametrize("key", ["nvenc", "qsv", "amf"])
    @pytest.mark.parametrize("codec", ["h264", "hevc", "av1"])
    def test_vendor_gpus_keep_their_native_encoders(self, key: str, codec: str):
        """The vendors that do have per-codec entries must be unaffected."""
        expected = _HW_ENCODER_CATALOG[key if codec == "h264" else f"{key}_{codec}"].ffmpeg_codec
        assert _codec_of(_cmd(key, codec)) == expected


# ── MediaFoundation backend ──────────────────────────────────────────────────


class TestMediaFoundation:
    def test_mf_h264_flags(self):
        cmd = _cmd("mf", "h264")
        assert _codec_of(cmd) == "h264_mf"
        # -quality is ignored by MediaFoundation unless rate control is set to
        # quality mode first.
        assert cmd[cmd.index("-rate_control") + 1] == "quality"
        assert cmd.index("-rate_control") < cmd.index("-quality")

    def test_mf_quality_scale_is_inverted_vs_crf(self):
        """MF -quality is 0-100 with HIGHER meaning better, unlike a CRF."""
        high = _cmd("mf", "h264", quality="high")
        small = _cmd("mf", "h264", quality="small")
        assert int(high[high.index("-quality") + 1]) > int(small[small.index("-quality") + 1])

    def test_mf_hevc_and_av1_entries_exist(self):
        assert _HW_ENCODER_CATALOG["mf_hevc"].ffmpeg_codec == "hevc_mf"
        assert _HW_ENCODER_CATALOG["mf_av1"].ffmpeg_codec == "av1_mf"

    def test_mf_uses_yuv420p_not_nv12(self):
        """Only QSV needs the nv12 conversion; MF accepts yuv420p directly."""
        cmd = _cmd("mf", "h264")
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert "format=nv12" not in cmd[cmd.index("-vf") + 1]

    def test_vendor_encoders_have_no_extra_flags(self):
        for key in ("nvenc", "qsv", "amf", "videotoolbox"):
            assert _HW_ENCODER_CATALOG[key].extra_flags == ()


# ── FLV/TS livestream guards must be untouched by all of the above ───────────


class TestLivestreamGuardsIntact:
    def test_flv_keeps_its_analyzeduration_and_audio_resample(self):
        settings = EncodeSettings(encoder_key="mf", output_codec="h264")
        cmd = FfmpegConvertService._build_cmd(
            Path("ffmpeg"), Path("live.flv"), Path("out.mp4"), _PRESET, encode_settings=settings
        )
        assert cmd[cmd.index("-analyzeduration") + 1] == "30M"
        assert cmd[cmd.index("-probesize") + 1] == "200M"
        assert cmd[cmd.index("-af") + 1] == "aresample=async=1000,asetpts=PTS-STARTPTS"
        # setpts=N/FR/TB must stay off for FLV/TS (BUG-TT timestamp handling)
        assert "setpts=N/FR/TB" not in cmd[cmd.index("-vf") + 1]

    def test_mp4_still_gets_setpts(self):
        assert "setpts=N/FR/TB" in _cmd("cpu", "h264")[_cmd("cpu", "h264").index("-vf") + 1]

    def test_iphone_contract_unchanged_for_h264(self):
        cmd = _cmd("cpu", "h264")
        assert cmd[cmd.index("-profile:v") + 1] == "main"
        assert cmd[cmd.index("-level:v") + 1] == "4.1"
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert cmd[cmd.index("-movflags") + 1] == "+faststart"


# ── VMAF ─────────────────────────────────────────────────────────────────────


class TestComputeVmaf:
    def test_parses_pooled_mean(self, tmp_path: Path):
        def fake_run(cmd, **kw):
            log = cmd[cmd.index("-lavfi") + 1].split("log_path='")[1].split("':log_fmt")[0]
            Path(log.replace("\\:", ":")).write_text(
                json.dumps({"pooled_metrics": {"vmaf": {"mean": 94.3172}}}), encoding="utf-8"
            )

            class R:
                returncode = 0
                stdout = b""
                stderr = b""

            return R()

        with patch("app.services.ffmpeg_convert_service.subprocess.run", fake_run):
            assert compute_vmaf(Path("ffmpeg"), tmp_path / "a.mp4", tmp_path / "b.mp4") == 94.32

    def test_returns_none_on_ffmpeg_failure(self, tmp_path: Path):
        class R:
            returncode = 1
            stdout = b""
            stderr = b"No such filter: 'libvmaf'"

        with patch("app.services.ffmpeg_convert_service.subprocess.run", return_value=R()):
            assert compute_vmaf(Path("ffmpeg"), tmp_path / "a.mp4", tmp_path / "b.mp4") is None

    def test_returns_none_and_never_raises_on_exception(self, tmp_path: Path):
        with patch("app.services.ffmpeg_convert_service.subprocess.run", side_effect=OSError("boom")):
            assert compute_vmaf(Path("ffmpeg"), tmp_path / "a.mp4", tmp_path / "b.mp4") is None


# ── Post-conversion passes never break a successful conversion ───────────────


class TestPostPasses:
    def _result(self, settings, **patches) -> ConvertResult:
        svc = FfmpegConvertService()
        with patch.dict("sys.modules"):
            return svc._run_post_passes(Path("ffmpeg"), Path("in.mp4"), Path("out.mp4"), 10.0, settings, None)

    def test_no_settings_returns_bare_result(self):
        res = FfmpegConvertService()._run_post_passes(
            Path("ffmpeg"), Path("in.mp4"), Path("out.mp4"), 10.0, None, None
        )
        assert res.output == Path("out.mp4")
        assert res.subtitle_path is None
        assert res.vmaf_score is None

    def test_defaults_run_neither_pass(self):
        with patch("app.services.ffmpeg_convert_service.compute_vmaf") as vmaf:
            res = self._result(EncodeSettings())
        vmaf.assert_not_called()
        assert res.subtitle_path is None

    def test_subtitle_failure_is_reported_not_raised(self):
        settings = EncodeSettings(generate_subtitles=True)
        with patch.object(wss, "generate_srt", side_effect=wss.SubtitleError("no speech")):
            res = self._result(settings)
        assert res.subtitle_error == "no speech"
        assert res.subtitle_path is None
        # The video conversion itself still succeeded.
        assert res.output == Path("out.mp4")

    def test_subtitle_success_records_path(self, tmp_path: Path):
        settings = EncodeSettings(generate_subtitles=True, subtitle_language="vi")
        srt = tmp_path / "out.srt"
        with patch.object(wss, "generate_srt", return_value=srt) as gen:
            res = self._result(settings)
        assert res.subtitle_path == srt
        assert gen.call_args.kwargs["language"] == "vi"

    def test_vmaf_runs_only_when_requested(self):
        with patch("app.services.ffmpeg_convert_service.compute_vmaf", return_value=91.5):
            res = self._result(EncodeSettings(compute_vmaf=True))
        assert res.vmaf_score == 91.5


# ── Whisper subtitle service ─────────────────────────────────────────────────


class TestFilterPathEscaping:
    def test_windows_path_gets_escaped_colon_and_forward_slashes(self):
        out = wss.escape_filter_path(Path(r"C:\Users\a\ggml-base.bin"))
        # Verified against a real FFmpeg 9.0.1 run: single quotes, forward
        # slashes, backslash-escaped drive colon.
        assert out == r"'C\:/Users/a/ggml-base.bin'"

    def test_posix_path_is_quoted_unchanged(self):
        assert wss.escape_filter_path(Path("/home/u/m.bin")) == "'/home/u/m.bin'"

    def test_single_quote_in_path_is_escaped(self):
        assert r"\'" in wss.escape_filter_path(Path("/tmp/bob's/m.bin"))


class TestBuildWhisperFilter:
    def test_minimal_filter(self):
        f = wss.build_whisper_filter(Path("/m/base.bin"))
        assert f.startswith("whisper=")
        assert "format=srt" in f
        assert "language=auto" in f
        assert "translate" not in f
        assert "vad_model" not in f

    def test_all_options(self):
        f = wss.build_whisper_filter(
            Path("/m/base.bin"),
            language="vi",
            destination=Path("/out/v.srt"),
            translate=True,
            vad_model=Path("/m/vad.bin"),
            max_len=30,
        )
        assert "language=vi" in f
        assert "destination='/out/v.srt'" in f
        assert "translate=true" in f
        assert "vad_model='/m/vad.bin'" in f
        assert "max_len=30" in f


class TestWhisperModels:
    def test_default_model_is_a_known_model(self):
        assert wss.DEFAULT_MODEL_KEY in {m.key for m in wss.WHISPER_MODELS}

    def test_model_keys_are_unique_and_ordered_by_size(self):
        sizes = [m.size_mb for m in wss.WHISPER_MODELS]
        assert sizes == sorted(sizes)
        assert len({m.key for m in wss.WHISPER_MODELS}) == len(wss.WHISPER_MODELS)

    def test_unknown_model_key_rejected(self):
        with pytest.raises(wss.SubtitleError):
            wss.model_path("gigantic")

    def test_is_model_installed_false_for_missing_file(self, tmp_path: Path):
        with patch.object(wss, "model_dir", return_value=tmp_path):
            assert wss.is_model_installed("base") is False

    def test_is_model_installed_rejects_truncated_file(self, tmp_path: Path):
        (tmp_path / "ggml-base.bin").write_bytes(b"x" * 1024)
        with patch.object(wss, "model_dir", return_value=tmp_path):
            assert wss.is_model_installed("base") is False

    def test_installed_models_lists_only_present_ones(self, tmp_path: Path):
        (tmp_path / "ggml-tiny.bin").write_bytes(b"x" * 2_000_000)
        with patch.object(wss, "model_dir", return_value=tmp_path):
            assert wss.installed_models() == ["tiny"]

    def test_ensure_model_skips_download_when_present(self, tmp_path: Path):
        (tmp_path / "ggml-base.bin").write_bytes(b"x" * 2_000_000)
        with patch.object(wss, "model_dir", return_value=tmp_path):
            with patch.object(wss, "_download") as dl:
                assert wss.ensure_model("base").name == "ggml-base.bin"
        dl.assert_not_called()


class TestWhisperSupportProbe:
    def _proc(self, out: bytes):
        class R:
            stdout = out
            stderr = b""

        return R()

    def test_detects_supported_build(self):
        with patch.object(wss.subprocess, "run", return_value=self._proc(b"whisper AVOptions:\n model ...")):
            assert wss.is_whisper_supported(Path("ffmpeg")) is True

    def test_detects_essentials_build(self):
        with patch.object(wss.subprocess, "run", return_value=self._proc(b"Unknown filter 'whisper'.")):
            assert wss.is_whisper_supported(Path("ffmpeg")) is False

    def test_probe_failure_is_not_supported(self):
        with patch.object(wss.subprocess, "run", side_effect=OSError("nope")):
            assert wss.is_whisper_supported(Path("ffmpeg")) is False


class TestGenerateSrtGuards:
    def test_rejects_language_outside_allowlist(self, tmp_path: Path):
        """subtitle_language reaches an FFmpeg filter string — it must be validated."""
        # The message is translated; match the offending value, which is not.
        with pytest.raises(wss.SubtitleError, match=re.escape("vi;rm -rf /")):
            wss.generate_srt(Path("ffmpeg"), tmp_path / "a.mp4", tmp_path / "a.srt", language="vi;rm -rf /")

    def test_rejects_build_without_whisper(self, tmp_path: Path):
        with patch.object(wss, "is_whisper_supported", return_value=False):
            with pytest.raises(wss.SubtitleError, match="whisper"):
                wss.generate_srt(Path("ffmpeg"), tmp_path / "a.mp4", tmp_path / "a.srt")

    def test_every_ui_language_is_in_the_allowlist(self):
        for key, _ in wss.SUBTITLE_LANGUAGE_OPTIONS:
            assert key in wss.VALID_LANGUAGES


class TestRenumberSrt:
    def test_zero_based_indices_become_one_based(self, tmp_path: Path):
        """FFmpeg's whisper filter emits 0-based cues; iOS drops cue 0."""
        srt = tmp_path / "a.srt"
        srt.write_text(
            "0\n00:00:00,000 --> 00:00:02,000\nHello\n\n1\n00:00:02,000 --> 00:00:04,000\nWorld\n",
            encoding="utf-8",
        )
        wss._renumber_srt(srt)
        lines = srt.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "1"
        assert "2" in srt.read_text(encoding="utf-8").split("\n\n")[1]

    def test_text_is_preserved(self, tmp_path: Path):
        srt = tmp_path / "a.srt"
        srt.write_text("0\n00:00:00,000 --> 00:00:02,000\nTiếng Việt có dấu\n", encoding="utf-8")
        wss._renumber_srt(srt)
        assert "Tiếng Việt có dấu" in srt.read_text(encoding="utf-8")

    def test_empty_file_does_not_raise(self, tmp_path: Path):
        srt = tmp_path / "a.srt"
        srt.write_text("", encoding="utf-8")
        wss._renumber_srt(srt)


# ── Regressions caught by running the generated commands for real ────────────


class TestFilterArgQuotingRegressions:
    """Both of these produced a working-looking command that FFmpeg rejected."""

    def test_vmaf_log_path_is_single_quoted(self, tmp_path: Path):
        """Unquoted, FFmpeg stops at the Windows drive colon:
        "No option name near '/Users/.../vmaf.json:log_fmt=json'"."""
        captured = {}

        def fake_run(cmd, **kw):
            captured["lavfi"] = cmd[cmd.index("-lavfi") + 1]

            class R:
                returncode = 1
                stdout = b""
                stderr = b""

            return R()

        with patch("app.services.ffmpeg_convert_service.subprocess.run", fake_run):
            compute_vmaf(Path("ffmpeg"), tmp_path / "a.mp4", tmp_path / "b.mp4")

        lavfi = captured["lavfi"]
        assert "log_path='" in lavfi
        assert "':log_fmt=json" in lavfi

    def test_mediafoundation_profile_is_numeric(self):
        """h264_mf registers no profile names and errors on -profile:v main."""
        cmd = _cmd("mf", "h264")
        # 77 is H.264 profile_idc for Main, the same profile the named form asks for.
        assert cmd[cmd.index("-profile:v") + 1] == "77"
        assert cmd[cmd.index("-level:v") + 1] == "4.1"

    def test_named_profile_kept_for_encoders_that_accept_it(self):
        for key in ("cpu", "nvenc", "qsv", "amf"):
            cmd = _cmd(key, "h264")
            assert cmd[cmd.index("-profile:v") + 1] == "main"

    def test_escaper_is_shared_between_vmaf_and_whisper(self):
        """One escaping rule, one implementation — they drifted apart once."""
        from app.services.ffmpeg_convert_service import escape_filter_path

        assert wss.escape_filter_path is escape_filter_path
