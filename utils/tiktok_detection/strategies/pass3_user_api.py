"""Pass-3: TikTok /api/user/detail/ endpoint.

Fallback when webcast/room/list (Pass-0) returns rooms=0 due to bot-detection
or stale msToken. The user-detail API uses a different rate-limit bucket and
sometimes returns roomId even when the webcast API is blocked.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
from utils.tiktok_detection.strategy import LiveDetectionStrategy

logger = logging.getLogger(__name__)

_USER_DETAIL_API = "https://www.tiktok.com/api/user/detail/"


def _valid_room_id(v: object) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    return s if s.isdigit() and int(s) != 0 else None


class Pass3UserApi(LiveDetectionStrategy):
    name = "pass3_user_api"

    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        from utils.tiktok_live_checker import (
            _CHROME_UA,
            _get_impersonate_session,
            _load_cookie_jar,
            _verify_room_alive,
        )

        proxies = {"http": ctx.proxy, "https": ctx.proxy} if ctx.proxy else None
        headers = {
            "User-Agent": _CHROME_UA,
            "Accept": "application/json, */*",
            "Referer": f"https://www.tiktok.com/@{ctx.username}",
            "Origin": "https://www.tiktok.com",
        }
        params: dict = {"uniqueId": ctx.username, "aid": "1988"}
        if ctx.sec_user_id:
            params["secUid"] = ctx.sec_user_id

        jar = _load_cookie_jar(ctx.cookie_file)
        session = _get_impersonate_session(jar)
        try:
            resp = session.get(
                _USER_DETAIL_API,
                params=params,
                headers=headers,
                proxies=proxies,
                timeout=10,
            )
        except Exception as exc:
            raise RuntimeError(f"pass3 network error: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"pass3 HTTP {resp.status_code}")

        try:
            data = json.loads(resp.text)
        except ValueError as exc:
            logger.debug("tiktok_detection: @%s pass-3 JSON parse error: %s", ctx.username, exc)
            return None

        status_code = data.get("statusCode", -1)
        if status_code != 0:
            logger.debug(
                "tiktok_detection: @%s pass-3 statusCode=%s (not logged in or blocked)",
                ctx.username,
                status_code,
            )
            return None

        user = data.get("userInfo", {}).get("user", {})
        room_id = _valid_room_id(user.get("roomId"))
        if not room_id:
            logger.debug("tiktok_detection: @%s pass-3 no roomId in user detail", ctx.username)
            return None

        if not _verify_room_alive(room_id, ctx.username, proxy=ctx.proxy, cookie_file=ctx.cookie_file):
            logger.debug(
                "tiktok_detection: @%s pass-3 roomId=%s check_alive=false",
                ctx.username,
                room_id,
            )
            return None

        live_url = f"https://www.tiktok.com/@{ctx.username}/live"
        logger.info("tiktok_detection: @%s LIVE via pass-3 roomId=%s", ctx.username, room_id)
        return LiveCheckResult(live_url=live_url, room_id=room_id, strategy_name=self.name)
