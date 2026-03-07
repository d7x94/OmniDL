"""
domain/enums/download_status.py
All possible states a DownloadTask can be in.
"""
from __future__ import annotations

from enum import Enum, auto


class DownloadStatus(Enum):
    QUEUED      = auto()   # waiting for a free worker slot
    DOWNLOADING = auto()   # active transfer
    PROCESSING  = auto()   # post-processing (FFmpeg merge / thumbnail)
    PAUSED      = auto()   # user-paused
    COMPLETED   = auto()   # finished successfully
    FAILED      = auto()   # unrecoverable error
    CANCELLED   = auto()   # user cancelled

    # Convenience groups (not real states)
    @classmethod
    def active_states(cls) -> frozenset[DownloadStatus]:
        return frozenset({cls.QUEUED, cls.DOWNLOADING, cls.PROCESSING})

    @classmethod
    def terminal_states(cls) -> frozenset[DownloadStatus]:
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})
