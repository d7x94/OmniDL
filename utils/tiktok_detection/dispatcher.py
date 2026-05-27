from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from typing import TYPE_CHECKING, Optional

from utils.tiktok_detection.context import LiveCheckContext
from utils.tiktok_detection.strategy import StreamConfirmedEndedError

if TYPE_CHECKING:
    from utils.tiktok_detection.health import StrategyHealthRegistry
    from utils.tiktok_detection.strategy import LiveDetectionStrategy

logger = logging.getLogger(__name__)


class LiveDetectionDispatcher:
    def __init__(
        self,
        strategies: list[LiveDetectionStrategy],
        health: StrategyHealthRegistry,
    ) -> None:
        self._strategies = strategies
        self._health = health

    def check(self, ctx: LiveCheckContext) -> Optional[tuple[str, str]]:
        """Return (live_url, room_id) or None. Runs enabled strategies in parallel.

        Re-raises RuntimeError if no strategy returned a result and at least one
        raised a hard error (404, 429, network). Cancels all passes immediately
        when StreamConfirmedEndedError is raised (stream status=4/5 confirmed).
        """
        runnable = [s for s in self._strategies if s.can_run(ctx) and self._health.is_enabled(s.name)]
        if not runnable:
            logger.debug("tiktok_detection: no runnable strategies for @%s", ctx.username)
            return None

        last_error: Optional[RuntimeError] = None

        with ThreadPoolExecutor(max_workers=len(runnable)) as ex:
            futures = {ex.submit(s.check, ctx): s for s in runnable}
            done, pending = wait(futures, return_when=FIRST_COMPLETED, timeout=20.0)

            # Positive result wins over StreamConfirmedEndedError: profile page
            # caching can return status=4/5 while the stream is still live, so
            # we must not cancel pending strategies on the basis of that signal
            # alone. Collect results from all completed futures first; only
            # respect confirmed-ended if no strategy returned a live URL.
            confirmed_ended = False
            for f in done:
                try:
                    r = f.result()
                    if r is not None:
                        for p in pending:
                            p.cancel()
                        return r.live_url, r.room_id
                except StreamConfirmedEndedError:
                    confirmed_ended = True
                except RuntimeError as exc:
                    last_error = exc
                except Exception as exc:
                    logger.debug("tiktok_detection: %s raised: %s", futures[f].name, exc)

            for f in as_completed(pending, timeout=10.0):
                try:
                    r = f.result()
                    if r is not None:
                        return r.live_url, r.room_id
                except StreamConfirmedEndedError:
                    confirmed_ended = True
                except RuntimeError as exc:
                    last_error = exc
                except Exception as exc:
                    logger.debug("tiktok_detection: %s raised: %s", futures[f].name, exc)

            if confirmed_ended:
                return None

        if last_error is not None:
            raise last_error
        return None
