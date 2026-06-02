# tests/test_ffmpeg_trim_service.py
from pathlib import Path

from app.services.ffmpeg_trim_service import _build_cmd


def _cmd(**kwargs) -> list[str]:
    """Helper: call _build_cmd with minimal required args + any extras."""
    defaults = dict(
        rotate=None,
        mute=False,
        needs_reencode=True,
        speed=1.0,
        volume=1.0,
        text="",
        text_pos=2,
        text_size=24,
        text_color="white",
        text_box=False,
        text_shadow=False,
        brightness=0.0,
        contrast=1.0,
        saturation=1.0,
        hue=0.0,
        blur=0.0,
        fade_in_s=0.0,
        fade_out_s=0.0,
        duration_s=10.0,
    )
    defaults.update(kwargs)
    return _build_cmd(
        "ffmpeg",
        Path("/in.mp4"),
        Path("/out.mp4"),
        0.0,
        10.0,
        **defaults,
    )


def test_brightness_adds_eq_filter():
    cmd = _cmd(brightness=0.3, needs_reencode=True)
    joined = " ".join(cmd)
    assert "eq=brightness=0.3" in joined


def test_contrast_adds_eq_filter():
    cmd = _cmd(contrast=1.5, needs_reencode=True)
    assert "contrast=1.5" in " ".join(cmd)


def test_saturation_adds_eq_filter():
    cmd = _cmd(saturation=2.0, needs_reencode=True)
    assert "saturation=2.0" in " ".join(cmd)


def test_hue_adds_filter():
    cmd = _cmd(hue=45.0, needs_reencode=True)
    assert "hue=h=45.0" in " ".join(cmd)


def test_blur_adds_gblur():
    cmd = _cmd(blur=3.5, needs_reencode=True)
    assert "gblur=sigma=3.5" in " ".join(cmd)


def test_fade_in_adds_video_and_audio_fade():
    cmd = _cmd(fade_in_s=1.5, needs_reencode=True)
    joined = " ".join(cmd)
    assert "fade=t=in:st=0:d=1.50" in joined
    assert "afade=t=in:ss=0:d=1.50" in joined


def test_fade_out_uses_duration():
    cmd = _cmd(fade_out_s=2.0, duration_s=10.0, needs_reencode=True)
    joined = " ".join(cmd)
    assert "fade=t=out:st=8.00:d=2.00" in joined
    assert "afade=t=out:st=8.00:d=2.00" in joined


def test_text_box_adds_box_opts():
    cmd = _cmd(text="Hello", text_box=True, needs_reencode=True)
    assert "box=1" in " ".join(cmd)


def test_text_shadow_adds_shadow_opts():
    cmd = _cmd(text="Hi", text_shadow=True, needs_reencode=True)
    assert "shadowx=2" in " ".join(cmd)


def test_no_eq_when_defaults():
    cmd = _cmd()
    assert "eq=" not in " ".join(cmd)


def test_no_reencode_when_copy():
    cmd = _cmd(needs_reencode=False)
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
