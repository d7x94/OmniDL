"""Desktop half of the FFmpeg 9.0.1 audit fixes — ui/tabs/convert_tab.py.

Uses the unbound-method pattern (SimpleNamespace as self) so no QApplication or
display server is required, matching tests/test_archive_tab.py.

Covers:
  - _apply_available_codecs: codec cards the FFmpeg build cannot produce are
    hidden, and a stale selection falls back to h264 (the P0 AV1 bug)
  - _apply_whisper_support: subtitle controls stay disabled on a build without
    the whisper filter
  - _on_subs_toggled / _on_subs_lang_change: state plumbing
  - _detect_capabilities_async: all three probes run off the UI thread
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ui.tabs.convert_tab as ct
from app.services.whisper_subtitle_service import SUBTITLE_LANGUAGE_OPTIONS, VALID_LANGUAGES


class _Card:
    def __init__(self) -> None:
        self.visible = True

    def setVisible(self, v: bool) -> None:
        self.visible = bool(v)


class _Check:
    def __init__(self) -> None:
        self.enabled = False
        self.checked = False
        self.tooltip = ""

    def setEnabled(self, v: bool) -> None:
        self.enabled = bool(v)

    def setChecked(self, v: bool) -> None:
        self.checked = bool(v)

    def setToolTip(self, t: str) -> None:
        self.tooltip = t


class _Combo:
    def __init__(self) -> None:
        self.enabled = False

    def setEnabled(self, v: bool) -> None:
        self.enabled = bool(v)


def _self(**over):
    ns = SimpleNamespace(
        _codec_cards={k: _Card() for k, _ in ct.CODEC_OPTIONS},
        _codec_main_labels={},
        _available_codecs={k for k, _ in ct.CODEC_OPTIONS},
        _output_codec="h264",
        _gen_subtitles=False,
        _subtitle_language="auto",
        _subtitle_model="base",
        _subs_check=_Check(),
        _subs_btn=_Check(),
        _subs_lang_combo=_Combo(),
        _subs_model_combo=_Combo(),
        _whisper_ok=False,
        _update_card_selection=MagicMock(),
        _on_codec_change=MagicMock(),
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# ── Codec cards ──────────────────────────────────────────────────────────────


class TestApplyAvailableCodecs:
    def test_av1_card_hidden_on_essentials_build(self):
        """The shipped Windows FFmpeg has no SVT-AV1; the option must disappear
        rather than queue a conversion that always fails."""
        s = _self()
        ct.ConvertTab._apply_available_codecs(s, [("h264", "H"), ("hevc", "X")])
        assert s._codec_cards["av1"].visible is False
        assert s._codec_cards["h264"].visible is True
        assert s._codec_cards["hevc"].visible is True

    def test_stale_av1_selection_falls_back_to_h264(self):
        s = _self(_output_codec="av1")
        ct.ConvertTab._apply_available_codecs(s, [("h264", "H")])
        s._on_codec_change.assert_called_once_with("h264")

    def test_valid_selection_is_left_alone(self):
        s = _self(_output_codec="hevc")
        ct.ConvertTab._apply_available_codecs(s, [("h264", "H"), ("hevc", "X")])
        s._on_codec_change.assert_not_called()
        assert s._output_codec == "hevc"

    def test_all_cards_shown_on_full_build(self):
        s = _self()
        ct.ConvertTab._apply_available_codecs(s, list(ct.CODEC_OPTIONS))
        assert all(c.visible for c in s._codec_cards.values())

    def test_empty_probe_result_still_leaves_h264_usable(self):
        """A totally failed probe must not hide every option."""
        s = _self()
        ct.ConvertTab._apply_available_codecs(s, [])
        assert s._available_codecs == {"h264"}
        assert s._codec_cards["h264"].visible is True


# ── Subtitle controls ────────────────────────────────────────────────────────


class TestApplyWhisperSupport:
    def test_disabled_and_explained_without_whisper(self):
        s = _self(_gen_subtitles=True)
        ct.ConvertTab._apply_whisper_support(s, False)
        assert s._subs_check.enabled is False
        assert s._subs_check.checked is False
        assert s._gen_subtitles is False
        assert "whisper" in s._subs_check.tooltip
        # The standalone "Tạo phụ đề" button must go with it.
        assert s._subs_btn.enabled is False
        assert "whisper" in s._subs_btn.tooltip

    def test_enabled_with_whisper(self):
        s = _self()
        with patch.object(ct, "is_model_installed", return_value=True):
            ct.ConvertTab._apply_whisper_support(s, True)
        assert s._subs_check.enabled is True
        assert s._subs_btn.enabled is True
        assert s._whisper_ok is True

    def test_first_use_download_is_announced(self):
        s = _self()
        with patch.object(ct, "is_model_installed", return_value=False):
            ct.ConvertTab._apply_whisper_support(s, True)
        assert "MB" in s._subs_check.tooltip

    def test_combos_follow_whisper_support_not_the_checkbox(self):
        """Both combos also drive the standalone "Tạo phụ đề" button, so they
        are usable whenever the build supports whisper — checkbox or not."""
        s = _self(_gen_subtitles=False)
        with patch.object(ct, "is_model_installed", return_value=True):
            ct.ConvertTab._apply_whisper_support(s, True)
        assert s._subs_lang_combo.enabled is True
        assert s._subs_model_combo.enabled is True

    def test_combos_disabled_without_whisper(self):
        s = _self(_gen_subtitles=True)
        ct.ConvertTab._apply_whisper_support(s, False)
        assert s._subs_lang_combo.enabled is False
        assert s._subs_model_combo.enabled is False


class TestSubtitleStatePlumbing:
    def test_toggle_updates_state(self):
        s = _self()
        ct.ConvertTab._on_subs_toggled(s, True)
        assert s._gen_subtitles is True

        ct.ConvertTab._on_subs_toggled(s, False)
        assert s._gen_subtitles is False

    def test_language_index_maps_to_code(self):
        s = _self()
        for i, (code, _) in enumerate(SUBTITLE_LANGUAGE_OPTIONS):
            ct.ConvertTab._on_subs_lang_change(s, i)
            assert s._subtitle_language == code

    def test_out_of_range_index_is_ignored(self):
        s = _self()
        ct.ConvertTab._on_subs_lang_change(s, 999)
        ct.ConvertTab._on_subs_lang_change(s, -1)
        assert s._subtitle_language == "auto"

    def test_every_offered_language_is_server_side_valid(self):
        """The desktop combo and the Web API allowlist must not drift apart."""
        assert {c for c, _ in SUBTITLE_LANGUAGE_OPTIONS} <= VALID_LANGUAGES

    def test_model_index_maps_to_key(self):
        s = _self()
        for i, model in enumerate(ct.WHISPER_MODELS):
            ct.ConvertTab._on_subs_model_change(s, i)
            assert s._subtitle_model == model.key

    def test_model_out_of_range_index_is_ignored(self):
        s = _self()
        ct.ConvertTab._on_subs_model_change(s, 999)
        ct.ConvertTab._on_subs_model_change(s, -1)
        assert s._subtitle_model == "base"


# ── Detection thread ─────────────────────────────────────────────────────────


class TestDetectCapabilitiesAsync:
    def test_probes_encoders_codecs_and_whisper(self):
        posted: list = []
        with (
            patch.object(ct, "get_available_encoder_options", return_value=[("cpu", "CPU")]) as enc,
            patch.object(ct, "get_available_codec_options", return_value=[("h264", "H")]) as cod,
            patch.object(ct, "is_whisper_supported", return_value=True) as wh,
            patch.object(ct.ui_bridge, "post", side_effect=posted.append),
        ):
            ct.ConvertTab._detect_capabilities_async(_self())

        enc.assert_called_once()
        cod.assert_called_once()
        wh.assert_called_once()
        # All three results are handed back to the UI thread, never applied here.
        assert len(posted) == 3


# ── Job result plumbing ──────────────────────────────────────────────────────


class TestFileJobSubtitleFields:
    def test_defaults_are_empty(self):
        job = ct.FileJob(source=Path("a.mp4"))
        assert job.subtitle_path is None
        assert job.subtitle_error == ""

    def test_convert_result_is_recorded(self):
        job = ct.FileJob(source=Path("a.mp4"))
        result = ct.ConvertResult(
            output=Path("a_iPhone.mp4"), subtitle_path=Path("a_iPhone.srt"), vmaf_score=91.0
        )
        job.subtitle_path = result.subtitle_path
        job.subtitle_error = result.subtitle_error
        assert job.subtitle_path == Path("a_iPhone.srt")
        assert job.subtitle_error == ""


# ── VMAF control ─────────────────────────────────────────────────────────────


class TestVmafStatePlumbing:
    def test_toggle_updates_state(self):
        s = _self(_compute_vmaf=False)
        ct.ConvertTab._on_vmaf_toggled(s, True)
        assert s._compute_vmaf is True

        ct.ConvertTab._on_vmaf_toggled(s, False)
        assert s._compute_vmaf is False


class TestFileJobVmafField:
    def test_default_is_none(self):
        job = ct.FileJob(source=Path("a.mp4"))
        assert job.vmaf_score is None

    def test_convert_result_score_is_recorded(self):
        job = ct.FileJob(source=Path("a.mp4"))
        result = ct.ConvertResult(output=Path("a_iPhone.mp4"), vmaf_score=91.0)
        job.vmaf_score = result.vmaf_score
        assert job.vmaf_score == 91.0
