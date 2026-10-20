"""IG-DASH-DIAG (v20.3.26): when FFmpeg dies with "Unable to read to manifest"
on an Instagram live .mpd, the engine fetches the manifest once with the same
headers and logs how the CDN answered, so the open "IG live DASH I/O error"
finding gets a cause next time."""

from __future__ import annotations

import io
import logging
import time
from unittest.mock import MagicMock, patch

import infrastructure.downloader.instagram_live_engine as eng_mod
from domain.models.download_task import DownloadTask

_MPD = "https://instagram.fxxx-1.fna.fbcdn.net/live-dash/dash-abr/1.mpd?oh=x"


def _run(tmp_path, monkeypatch, caplog, stderr_lines, session):
    proc = MagicMock()
    proc.stderr = io.BytesIO("\n".join(stderr_lines).encode())
    polls = iter([None, 4294967291])
    proc.poll.side_effect = lambda: next(polls, 4294967291)
    real_sleep = time.sleep
    monkeypatch.setattr(eng_mod.time, "sleep", lambda _s: real_sleep(0.05))
    monkeypatch.setattr(eng_mod.subprocess, "Popen", lambda *a, **kw: proc)

    task = DownloadTask(url="https://www.instagram.com/u/live/1")
    engine = eng_mod.InstagramLiveEngine(MagicMock())
    with (
        patch("utils.tiktok_live_checker._get_impersonate_session", return_value=session),
        caplog.at_level(logging.WARNING, logger=eng_mod.logger.name),
    ):
        reason, _n = engine._record_segment(
            task,
            _MPD,
            {"User-Agent": "ua", "Referer": "https://www.instagram.com/"},
            task.url,
            tmp_path / "out.mkv.part0",
            True,
            "ffmpeg",
            0,
            None,
        )
    assert reason == "ffmpeg_error"
    return "\n".join(r.getMessage() for r in caplog.records)


_MANIFEST_FAIL = [
    f"[in#0 @ 0000] Unable to read to manifest '{_MPD}'",
    "[in#0 @ 0000] Error opening input: I/O error",
]


def test_manifest_read_failure_logs_cdn_answer(tmp_path, monkeypatch, caplog):
    resp = MagicMock(status_code=200, content=b"<MPD type='dynamic'>")
    resp.headers = {"Transfer-Encoding": "chunked", "Content-Type": "application/dash+xml"}
    session = MagicMock()
    session.get.return_value = resp

    logged = _run(tmp_path, monkeypatch, caplog, _MANIFEST_FAIL, session)

    sent = session.get.call_args
    assert sent.args[0] == _MPD
    assert sent.kwargs["headers"]["User-Agent"] == "ua"
    assert sent.kwargs["timeout"]
    assert "IG-DASH-DIAG" in logged
    assert "status=200" in logged
    assert "transfer-encoding=chunked" in logged
    assert "complete=False" in logged


def test_probe_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    session = MagicMock()
    session.get.side_effect = TimeoutError("read timed out")

    logged = _run(tmp_path, monkeypatch, caplog, _MANIFEST_FAIL, session)

    assert "IG-DASH-DIAG" in logged
    assert "read timed out" in logged


def test_other_ffmpeg_errors_do_not_probe(tmp_path, monkeypatch, caplog):
    session = MagicMock()

    _run(tmp_path, monkeypatch, caplog, ["[https @ 0000] HTTP error 403 Forbidden"], session)

    session.get.assert_not_called()
