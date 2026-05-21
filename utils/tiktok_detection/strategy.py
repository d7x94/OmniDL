from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult


class StreamConfirmedEndedError(Exception):
    """Pass-1 parsed liveRoomInfo with status=4/5. Dispatcher must not retry other passes."""


class LiveDetectionStrategy(ABC):
    name: str
    requires_share_url: bool = False

    def can_run(self, ctx: LiveCheckContext) -> bool:
        return not (self.requires_share_url and not ctx.sec_user_id)

    @abstractmethod
    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        """None = not live; raise RuntimeError for hard fail (404, 429, network)."""
        ...
