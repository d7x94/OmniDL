"""Pass-0: webcast room/list API via sec_user_id from share URL.

BUG-TT-09: Most reliable path when TikTok blocks page scraping.
Requires share_url with sec_user_id query param.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
from utils.tiktok_detection.strategy import LiveDetectionStrategy

logger = logging.getLogger(__name__)

_WEBCAST_ROOM_LIST_API = "https://webcast.tiktok.com/webcast/room/list/"


def _valid_room_id(v: object) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    return s if s.isdigit() and int(s) != 0 else None


class Pass0WebcastApi(LiveDetectionStrategy):
    name = "pass0_webcast_api"
    requires_share_url = True

    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        from utils.tiktok_live_checker import _CHROME_UA, _get_impersonate_session, _load_cookie_jar

        proxies = {"http": ctx.proxy, "https": ctx.proxy} if ctx.proxy else None
        headers = {
            "User-Agent": _CHROME_UA,
            "Accept": "application/json, */*",
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
        }
        jar = _load_cookie_jar(ctx.cookie_file)
        session = _get_impersonate_session(jar)
        try:
            resp = session.get(
                _WEBCAST_ROOM_LIST_API,
                params={"aid": "1988", "sec_user_id": ctx.sec_user_id},
                headers=headers,
                proxies=proxies,
                timeout=10,
            )
        except Exception as exc:
            raise RuntimeError(f"pass0 network error: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"pass0 HTTP {resp.status_code}")

        data = json.loads(resp.text)
        room_list = data.get("data", {}).get("room_list") or []
        for room in room_list:
            r_id = _valid_room_id(room.get("id_str") or room.get("id"))
            if r_id and room.get("status") == 2:
                live_url = f"https://www.tiktok.com/@{ctx.username}/live"
                logger.info(
                    "tiktok_detection: @%s LIVE via pass-0 roomId=%s",
                    ctx.username,
                    r_id,
                )
                return LiveCheckResult(live_url=live_url, room_id=r_id, strategy_name=self.name)

        logger.debug(
            "tiktok_detection: @%s pass-0 no active room (rooms=%d)",
            ctx.username,
            len(room_list),
        )
        return None
