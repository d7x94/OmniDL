from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from utils.tiktok_detection.strategy import StreamConfirmedEndedError

if TYPE_CHECKING:
    from utils.tiktok_detection.strategy import LiveDetectionStrategy

logger = logging.getLogger(__name__)

_DISABLE_THRESHOLD = 2
_CANARY_USERNAME = "tiktok"


class StrategyHealthRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {}
        self._disabled: set[str] = set()

    def is_enabled(self, name: str) -> bool:
        with self._lock:
            return name not in self._disabled

    def record_probe_success(self, name: str) -> None:
        with self._lock:
            self._failures[name] = 0
            if name in self._disabled:
                self._disabled.discard(name)
                logger.info("tiktok_detection: strategy %s re-enabled", name)

    def record_probe_failure(self, name: str) -> None:
        with self._lock:
            count = self._failures.get(name, 0) + 1
            self._failures[name] = count
            if count >= _DISABLE_THRESHOLD and name not in self._disabled:
                self._disabled.add(name)
                logger.warning(
                    "tiktok_detection: strategy %s disabled after %d probe failures",
                    name,
                    count,
                )


class HealthDaemon:
    PROBE_INTERVAL_SECONDS = 300

    def __init__(
        self,
        strategies: list[LiveDetectionStrategy],
        registry: StrategyHealthRegistry,
    ) -> None:
        self._strategies = strategies
        self._registry = registry
        self._timer: threading.Timer | None = None
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._schedule()

    def stop(self) -> None:
        self._stopped = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule(self) -> None:
        if self._stopped:
            return
        t = threading.Timer(self.PROBE_INTERVAL_SECONDS, self._probe_all)
        t.daemon = True
        t.start()
        self._timer = t

    def _probe_all(self) -> None:
        if self._stopped:
            return
        from utils.tiktok_detection.context import LiveCheckContext

        for strategy in self._strategies:
            # One context per strategy: it is the probe's private output slot.
            ctx = LiveCheckContext(username=_CANARY_USERNAME)
            try:
                result = strategy.check(ctx)
                if ctx.network_error:
                    # Never got a usable answer — the request never reached
                    # TikTok, or it came back non-200.  No evidence either way
                    # about this strategy.  Recording a success here reset the
                    # failure counter of an endpoint that is permanently blocked.
                    logger.debug(
                        "tiktok_detection: probe %s skipped (no usable answer)",
                        strategy.name,
                    )
                elif ctx.unavailable:
                    # The strategy reached the network but could not function
                    # (blocked endpoint / bot-detection body). None here does
                    # not mean "the canary is offline".
                    self._registry.record_probe_failure(strategy.name)
                    logger.debug(
                        "tiktok_detection: probe %s unavailable (endpoint blocked)",
                        strategy.name,
                    )
                else:
                    # None = not live, that's fine - strategy is working
                    self._registry.record_probe_success(strategy.name)
                    logger.debug("tiktok_detection: probe %s ok (result=%s)", strategy.name, result)
            except StreamConfirmedEndedError:
                # Strategy correctly detected the canary's stream ended - working as intended.
                self._registry.record_probe_success(strategy.name)
            except RuntimeError as exc:
                msg = str(exc).lower()
                if "not found" in msg or "404" in msg:
                    # API responded - strategy is working, canary account just not live
                    self._registry.record_probe_success(strategy.name)
                else:
                    self._registry.record_probe_failure(strategy.name)
                    logger.debug("tiktok_detection: probe %s failed: %s", strategy.name, exc)
            except Exception as exc:
                self._registry.record_probe_failure(strategy.name)
                logger.debug("tiktok_detection: probe %s error: %s", strategy.name, exc)
        self._schedule()
