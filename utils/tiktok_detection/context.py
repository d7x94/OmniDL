from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from urllib.parse import unquote_plus

_SEC_USER_ID_RE = re.compile(r"[?&]sec_user_id=([A-Za-z0-9_~%-]+)", re.I)
_USER_ID_RE = re.compile(r"[?&]user_id=(\d+)", re.I)


@dataclass
class LiveCheckContext:
    username: str
    proxy: str = ""
    cookie_file: str = ""
    share_url: str = ""

    # Set by check() when the strategy could not function at all — a blocked
    # endpoint, an empty bot-detection body — as opposed to answering "this
    # user is not live".  Returning None means both things, so without this
    # flag HealthDaemon counted a permanently broken pass as healthy and kept
    # calling it on every poll forever.  It lives on the context, not on the
    # strategy: strategy objects are singletons shared by the dispatcher's
    # thread pool and the health daemon, so an instance attribute could be
    # overwritten by a concurrent check for a different account between the
    # daemon's own check() returning and the daemon reading the verdict.
    unavailable: bool = False

    # Set by check() when the strategy never reached TikTok at all (DNS down,
    # connection refused, timeout).  This is neither "not live" nor "this
    # strategy is broken": HealthDaemon must record no verdict, because a
    # network blip used to be logged as a probe *success* and reset the
    # failure counter of a permanently blocked pass (see the 11:11 window of
    # omnidl_debug.log, where pass3_user_api was "re-enabled" by curl (7)).
    network_error: bool = False

    @cached_property
    def sec_user_id(self) -> str:
        m = _SEC_USER_ID_RE.search(self.share_url)
        return unquote_plus(m.group(1)) if m else ""

    @cached_property
    def user_id(self) -> str:
        m = _USER_ID_RE.search(self.share_url)
        return m.group(1) if m else ""


@dataclass
class LiveCheckResult:
    live_url: str
    room_id: str
    strategy_name: str
