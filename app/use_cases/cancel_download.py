"""app/use_cases/cancel_download.py"""
from __future__ import annotations

from app.services.download_service import DownloadService


class CancelDownload:
    def __init__(self, service: DownloadService) -> None:
        self._service = service

    def execute(self, task_id: str) -> None:
        self._service.cancel_download(task_id)
