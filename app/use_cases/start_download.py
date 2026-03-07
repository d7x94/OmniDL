"""
app/use_cases/start_download.py
Single-responsibility use-case: validate + kick off a download.
Thin wrapper kept for Clean Architecture compliance.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.services.download_service import DownloadService
from domain.models.download_task import DownloadTask, MediaInfo


class StartDownload:
    def __init__(self, service: DownloadService) -> None:
        self._service = service

    def execute(
        self,
        url: str,
        media_info: MediaInfo,
        format_id: str,
        output_ext: str,
        output_dir: Optional[Path] = None,
    ) -> DownloadTask:
        return self._service.start_download(
            url=url,
            media_info=media_info,
            format_id=format_id,
            output_ext=output_ext,
            output_dir=output_dir,
        )
