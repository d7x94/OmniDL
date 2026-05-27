"""Pass-1: profile page /@username -> __UNIVERSAL_DATA_FOR_REHYDRATION__.

Also includes Pass-3 raw HTML regex scan as fallback (no separate network call).
"""

from __future__ import annotations

import logging
from typing import Optional

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
from utils.tiktok_detection.strategy import LiveDetectionStrategy, StreamConfirmedEndedError

logger = logging.getLogger(__name__)


class Pass1ProfilePage(LiveDetectionStrategy):
    name = "pass1_profile_page"

    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        from utils.tiktok_live_checker import (
            _fetch_tiktok_profile_page,
            _room_id_from_profile_page,
            _verify_room_alive,
        )

        page_text = _fetch_tiktok_profile_page(ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file)
        if page_text is None:
            logger.debug("tiktok_detection: @%s pass-1 profile page fetch failed", ctx.username)
            return None

        room_id, status_ended = _room_id_from_profile_page(page_text, ctx.username)

        # Signal the dispatcher to cancel all other passes - stream is confirmed ended.
        if status_ended:
            raise StreamConfirmedEndedError(f"@{ctx.username} stream status=4/5")

        if not room_id:
            logger.debug(
                "tiktok_detection: @%s pass-1 no roomId in profile page (cookies expired or not live)",
                ctx.username,
            )
            return None

        if not _verify_room_alive(room_id, ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file):
            logger.debug(
                "tiktok_detection: @%s pass-1 roomId=%s check_alive=false",
                ctx.username,
                room_id,
            )
            return None

        live_url = f"https://www.tiktok.com/@{ctx.username}/live"
        logger.info("tiktok_detection: @%s LIVE via pass-1 roomId=%s", ctx.username, room_id)
        return LiveCheckResult(live_url=live_url, room_id=room_id, strategy_name=self.name)
