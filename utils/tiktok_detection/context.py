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
