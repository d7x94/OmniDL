"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - UI."""

from __future__ import annotations

import types

from domain.models.download_task import MediaInfo


# BUG-HOME-START-ERROR: Home's start_download had no try/except (Batch has one),
# so an unwritable download folder raised inside the Qt slot with no message and
# the result card stayed as if nothing happened.
def test_home_add_to_queue_reports_a_failed_start_and_keeps_the_result_card():
    from ui.tabs.home_tab import HomeTab

    toasts: list[tuple[str, str]] = []
    hidden: list[str] = []

    def _boom(**kwargs):
        raise PermissionError("cannot create the download folder")

    info = MediaInfo(url="https://www.tiktok.com/@someone/video/1", title="clip")
    tab = types.SimpleNamespace(
        _media_info=info,
        _selected_quality="best",
        _format_combo=types.SimpleNamespace(currentText=lambda: "mp4"),
        _custom_output_dir=None,
        _result_card=types.SimpleNamespace(hide=lambda: hidden.append("card")),
        _app=types.SimpleNamespace(
            service=types.SimpleNamespace(start_download=_boom),
            toast=lambda msg, kind="info": toasts.append((msg, kind)),
            navigate_to=lambda name: hidden.append(f"nav:{name}"),
        ),
    )

    HomeTab._add_to_queue(tab)

    assert toasts and toasts[0][1] == "error"
    assert "cannot create the download folder" in toasts[0][0]
    assert hidden == []
    assert tab._media_info is info
