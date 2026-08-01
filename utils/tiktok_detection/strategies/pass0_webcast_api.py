"""Pass-0: webcast room/list API via sec_user_id from share URL.

BUG-TT-09: Most reliable path when TikTok blocks page scraping.
Requires share_url with sec_user_id query param.
"""

from __future__ import annotations

import json
import logging
import time
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
    requires_share_url = False  # unique_id (username) is always available

    # BUG-TT-PASS0-COOLDOWN FIX: TikTok now requires signing for all param
    # combos (unique_id, sec_user_id, user_id all return 10013).  After all
    # combos fail with 10013, skip API calls for 120s to avoid waste.
    _all_10013_until: dict[str, float] = {}

    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        if time.monotonic() < Pass0WebcastApi._all_10013_until.get(ctx.username, 0.0):
            return None
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

        # Try multiple param combos: unique_id avoids signing requirement that
        # sec_user_id now triggers (10013 "Url does not match").
        param_combos = [{"aid": "1988", "unique_id": ctx.username}]
        if ctx.sec_user_id:
            param_combos.append({"aid": "1988", "sec_user_id": ctx.sec_user_id})
        if ctx.user_id:
            param_combos.append({"aid": "1988", "user_id": ctx.user_id})

        last_body = ""
        try:
            for params in param_combos:
                try:
                    resp = session.get(
                        _WEBCAST_ROOM_LIST_API,
                        params=params,
                        headers=headers,
                        proxies=proxies,
                        timeout=10,
                    )
                except Exception as exc:
                    raise RuntimeError(f"pass0 network error: {exc}") from exc

                if resp.status_code != 200:
                    # Webcast API errors are not authoritative (user may still exist).
                    # Only Pass-1 (profile page) raises hard 404/429 errors.
                    logger.debug(
                        "tiktok_detection: @%s pass-0 HTTP %s (params=%s)",
                        ctx.username,
                        resp.status_code,
                        list(params.keys()),
                    )
                    return None

                data = json.loads(resp.text)
                status_code = data.get("status_code", 0)
                if status_code == 10013:
                    # API rejected this param combo (signing required) — try next
                    last_body = resp.text
                    logger.debug(
                        "tiktok_detection: @%s pass-0 10013 with params=%s, trying next combo",
                        ctx.username,
                        list(params.keys()),
                    )
                    continue

                room_list = data.get("data", {}).get("room_list") or []
                for room in room_list:
                    r_id = _valid_room_id(room.get("id_str") or room.get("id"))
                    if r_id and room.get("status") == 2:
                        live_url = f"https://www.tiktok.com/@{ctx.username}/live"
                        logger.info(
                            "tiktok_detection: @%s LIVE via pass-0 roomId=%s params=%s",
                            ctx.username,
                            r_id,
                            list(params.keys()),
                        )
                        return LiveCheckResult(live_url=live_url, room_id=r_id, strategy_name=self.name)

                logger.debug(
                    "tiktok_detection: @%s pass-0 no active room (rooms=%d) params=%s body=%.200s",
                    ctx.username,
                    len(room_list),
                    list(params.keys()),
                    resp.text,
                )
                return None  # valid response with no live rooms — other combos are pointless
        finally:
            # Unclosed curl_cffi Sessions pin native libcurl memory the GC
            # cannot account for.
            session.close()

        logger.debug(
            "tiktok_detection: @%s pass-0 all combos returned 10013 body=%.200s",
            ctx.username,
            last_body,
        )
        now = time.monotonic()
        Pass0WebcastApi._all_10013_until = {
            u: t for u, t in Pass0WebcastApi._all_10013_until.items() if t > now
        }
        Pass0WebcastApi._all_10013_until[ctx.username] = now + 120.0
        return None
