"""Pass-2: live page /@username/live -> SIGI_STATE (mirrors yt-dlp pass 2)."""

from __future__ import annotations

import logging
from typing import Optional

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
from utils.tiktok_detection.strategy import LiveDetectionStrategy

logger = logging.getLogger(__name__)


class Pass2LivePage(LiveDetectionStrategy):
    name = "pass2_live_page"

    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        from utils.tiktok_live_checker import (
            _fetch_tiktok_live_page,
            _room_id_from_live_page,
            _verify_room_alive,
        )

        live_page_text = _fetch_tiktok_live_page(ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file)
        if not live_page_text:
            return None

        room_id = _room_id_from_live_page(live_page_text, ctx.username)
        if not room_id:
            return None

        if not _verify_room_alive(room_id, ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file):
            logger.debug(
                "tiktok_detection: @%s pass-2 roomId=%s check_alive=false",
                ctx.username,
                room_id,
            )
            return None

        live_url = f"https://www.tiktok.com/@{ctx.username}/live"
        logger.info("tiktok_detection: @%s LIVE via pass-2 roomId=%s", ctx.username, room_id)
        return LiveCheckResult(live_url=live_url, room_id=room_id, strategy_name=self.name)
