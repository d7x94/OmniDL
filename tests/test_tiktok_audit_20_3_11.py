"""Regression tests for the 2026-09-29 platform download audit (v20.3.11) - TikTok."""

from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

from app.event_bus import EventBus
from domain.enums.download_status import DownloadStatus
from domain.models.download_task import DownloadTask
from infrastructure.downloader.account_pool import _AcquireAborted
from infrastructure.downloader.download_manager import DownloadManager


# BUG-TT-POOL-CANCEL: cancelling a task parked on a pool slot only set the
# cancel event; _run_task never ran, so the task stayed QUEUED for ever (and the
# duplicate guard kept rejecting its URL until restart).
def test_task_cancelled_while_waiting_for_a_pool_slot_ends_cancelled():
    cfg = MagicMock(max_concurrent=1, max_retries=0, download_dir=Path("."))
    bus = MagicMock()
    manager = DownloadManager(config=cfg, engine=MagicMock(), event_bus=bus)
    task = DownloadTask(url="https://www.tiktok.com/@someone/video/7690000000000000000")
    assert task.status == DownloadStatus.QUEUED

    @contextlib.contextmanager
    def _acquire(should_abort=None):
        raise _AcquireAborted
        yield  # pragma: no cover

    pool = MagicMock()
    pool.acquire = _acquire

    manager._gated_run_tiktok(task, pool)

    assert task.status == DownloadStatus.CANCELLED
    assert task.finished_at
    bus.publish.assert_any_call(EventBus.DOWNLOAD_CANCELLED, task=task)


# BUG-TT-STALL-TEXT: the message hard-coded "120 seconds" while the watchdog
# fires after _STALL_LIMIT_S (20), which sent diagnosis down the wrong path.
def test_ffmpeg_stall_message_names_the_real_watchdog_limit():
    import inspect

    import infrastructure.downloader.yt_dlp_engine as yt_mod
    from utils.i18n import t

    text = t("err.ffmpeg_stall", seconds=20)
    assert "120" not in text
    assert "20" in text
    assert '_keyed_exc("err.ffmpeg_stall", seconds=_STALL_LIMIT_S)' in inspect.getsource(yt_mod)


# BUG-RETRY-AFTER-CAP: a Retry-After value in the error text became the sleep
# time uncapped (the default back-off is capped at 30 s), so one large value
# pinned a worker and its pool slot for hours.
def test_retry_after_from_the_error_text_is_capped():
    from unittest.mock import patch

    cfg = MagicMock(max_concurrent=1, max_retries=1, download_dir=Path("."))
    yt_engine = MagicMock()
    yt_engine.download.side_effect = RuntimeError("Server busy, retry after: 36000")
    manager = DownloadManager(config=cfg, engine=yt_engine, event_bus=MagicMock())
    task = DownloadTask(url="https://www.tiktok.com/@someone/video/7690000000000000000")
    sleeps: list[float] = []

    with patch("infrastructure.downloader.download_manager.time.sleep", sleeps.append):
        manager._run_task(task)

    assert sleeps, "the retry never slept"
    assert max(sleeps) <= 120
