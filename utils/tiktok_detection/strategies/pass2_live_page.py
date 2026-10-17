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
        from utils import tiktok_live_checker as tlc

        live_page_text = tlc._fetch_tiktok_live_page(
            ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file
        )
        if not live_page_text:
            # _fetch_tiktok_live_page returns None for a network failure or an
            # unexpected HTTP status — never for "this user is not live".
            ctx.network_error = True
            logger.debug("tiktok_detection: @%s pass-2 live page fetch failed", ctx.username)
            return None

        room_id = tlc._room_id_from_live_page(live_page_text, ctx.username)
        if not room_id:
            logger.debug("tiktok_detection: @%s pass-2 no roomId in live page", ctx.username)
            # With cookies, TikTok may serve a SPA variant that strips SIGI_STATE LiveRoom.
            # Retry without cookies to get the standard pre-rendered page.
            if ctx.cookie_file:
                live_page_nc = tlc._fetch_tiktok_live_page(ctx.username, proxy=ctx.proxy, cookie_file="")
                if live_page_nc:
                    room_id = tlc._room_id_from_live_page(live_page_nc, ctx.username)
            if not room_id:
                return None

        # BUG-TT-ENDEDROOM FIX: the live page keeps serving the finished
        # broadcast's roomId, and check_alive answers alive=True for it.
        if tlc._room_recently_ended(room_id):
            logger.debug(
                "tiktok_detection: @%s pass-2 roomId=%s already reported ended by room/info",
                ctx.username,
                room_id,
            )
            return None

        if not tlc._verify_room_alive(room_id, ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file):
            logger.debug(
                "tiktok_detection: @%s pass-2 roomId=%s check_alive=false",
                ctx.username,
                room_id,
            )
            return None

        live_url = f"https://www.tiktok.com/@{ctx.username}/live"
        logger.info("tiktok_detection: @%s LIVE via pass-2 roomId=%s", ctx.username, room_id)
        return LiveCheckResult(live_url=live_url, room_id=room_id, strategy_name=self.name)
