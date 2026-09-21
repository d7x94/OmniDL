"""Pass-4: TikTok /api-live/user/room/ endpoint — canonical live status + roomId.

BUG-TT-PASS4 FIX: every other detection path is now defeated by TikTok
anti-bot changes:
  - Pass-0 webcast/room/list/ returns 10013 "Url does not match" for every
    param combo (unique_id, sec_user_id, user_id) — signing is now required.
  - Pass-1/Pass-2 page scraping returns no roomId (live data stripped from HTML).
  - Pass-3 /api/user/detail/ returns an empty body under bot-detection.

The /api-live/user/room/ endpoint (the same one TikTok's own web LIVE player
calls) still returns roomId + live status without cookies or signing, so it is
the most reliable detection path. Response shape:
    {"statusCode":0,"data":{"user":{"roomId":"<id>","status":2},
                            "liveRoom":{"status":2,...}}}
status==2 means LIVE; status==4 means ended (roomId is then stale).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from utils.tiktok_detection.context import LiveCheckContext, LiveCheckResult
from utils.tiktok_detection.strategy import LiveDetectionStrategy

logger = logging.getLogger(__name__)

_API_LIVE_USER_ROOM = "https://www.tiktok.com/api-live/user/room/"


def _valid_room_id(v: object) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    return s if s.isdigit() and int(s) != 0 else None


class Pass4ApiLiveRoom(LiveDetectionStrategy):
    name = "pass4_api_live_room"

    def check(self, ctx: LiveCheckContext) -> Optional[LiveCheckResult]:
        from utils.tiktok_live_checker import _CHROME_UA, _get_impersonate_session, _load_cookie_jar

        proxies = {"http": ctx.proxy, "https": ctx.proxy} if ctx.proxy else None
        headers = {
            "User-Agent": _CHROME_UA,
            "Accept": "application/json, */*",
            "Referer": f"https://www.tiktok.com/@{ctx.username}/live",
            "Origin": "https://www.tiktok.com",
        }
        params = {"aid": "1988", "sourceType": "54", "uniqueId": ctx.username}
        jar = _load_cookie_jar(ctx.cookie_file)
        session = _get_impersonate_session(jar)
        try:
            resp = session.get(
                _API_LIVE_USER_ROOM,
                params=params,
                headers=headers,
                proxies=proxies,
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            ctx.network_error = True
            logger.debug("tiktok_detection: @%s pass-4 network error: %s", ctx.username, exc)
            return None
        finally:
            # Unclosed curl_cffi Sessions pin native libcurl memory the GC
            # cannot account for.
            session.close()

        if resp.status_code != 200:
            # BUG-TT-PROBE-429 FIX: see pass3_user_api — a non-200 is not an
            # answer about live status, so HealthDaemon must record no verdict
            # rather than a probe success.
            ctx.network_error = True
            logger.debug("tiktok_detection: @%s pass-4 HTTP %s", ctx.username, resp.status_code)
            return None

        try:
            data = json.loads(resp.text)
        except ValueError:
            if not resp.text.strip():
                ctx.unavailable = True
                logger.debug("tiktok_detection: @%s pass-4 empty body (bot-detection)", ctx.username)
            return None

        if data.get("statusCode", -1) != 0:
            logger.debug("tiktok_detection: @%s pass-4 statusCode=%s", ctx.username, data.get("statusCode"))
            return None

        room = data.get("data") or {}
        user = room.get("user", {})
        status = user.get("status")
        if status is None:
            status = room.get("liveRoom", {}).get("status")
        if status != 2:
            # BUG-TT-PASS4-ENDED FIX: this endpoint is the canonical live
            # status, but its verdict stayed local to pass-4.  Pass-1/pass-2
            # kept reading the finished broadcast's roomId out of the page and
            # check_alive kept answering alive=True for it, so the dispatcher
            # announced "LIVE" every poll for an account pass-4 had already
            # reported as ended (@tiktok room 7679022730909469458, every 5 min
            # for the whole 10:55-11:26 window of omnidl_debug.log).  Publish
            # the verdict to the shared ended-room set the page passes consult.
            # One record per verdict.  Marking the room and reporting "not
            # live" are the same event, but they were logged as two lines, so
            # a finished broadcast polled every 55s wrote 836 of the 3,586
            # lines in omnidl_debug.log (2026-09-19) on its own.
            marked = ""
            if status in (4, 5):
                stale_room = _valid_room_id(user.get("roomId")) or _valid_room_id(
                    room.get("liveRoom", {}).get("id_str") or room.get("liveRoom", {}).get("id")
                )
                if stale_room:
                    from utils.tiktok_live_checker import _mark_room_ended

                    # _mark_room_ended is idempotent and reports whether this
                    # poll is the one that recorded the verdict, so the suffix
                    # stays truthful instead of claiming a fresh mark on every
                    # poll of a broadcast that ended hours ago.
                    if _mark_room_ended(stale_room):
                        marked = f" — marked room {stale_room} ended"
            logger.debug(
                "tiktok_detection: @%s pass-4 status=%s (not live)%s",
                ctx.username,
                status,
                marked,
            )
            return None

        room_id = _valid_room_id(user.get("roomId"))
        if not room_id:
            logger.debug("tiktok_detection: @%s pass-4 status=2 but no roomId", ctx.username)
            return None

        live_url = f"https://www.tiktok.com/@{ctx.username}/live"
        logger.info("tiktok_detection: @%s LIVE via pass-4 roomId=%s", ctx.username, room_id)
        return LiveCheckResult(live_url=live_url, room_id=room_id, strategy_name=self.name)
