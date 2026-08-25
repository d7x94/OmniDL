"""Runtime translation lookup for OmniDL (desktop UI + Web API).

Source strings are Vietnamese, so ``vi`` is the reference catalogue.  A missing
key in the active language falls back to English, then Vietnamese, then to the
key itself so a typo shows up as a visible slug instead of an empty widget.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from utils.translations import CATALOG

logger = logging.getLogger(__name__)

# code -> native label shown in the language pickers
LANGUAGES: dict[str, str] = {
    "en": "English",
    "vi": "Tiếng Việt",
    "zh": "中文",
}

DEFAULT_LANGUAGE = "en"

_current: str = DEFAULT_LANGUAGE
_listeners: list[Callable[[str], None]] = []


def normalize(code: Any) -> str:
    """Return a supported language code for *code*, or DEFAULT_LANGUAGE.

    Accepts locale-ish input ("en-US", "zh_CN", "VI") so values coming from a
    browser Accept-Language header or a hand-edited config.json still resolve.
    """
    if not isinstance(code, str):
        return DEFAULT_LANGUAGE
    base = code.strip().lower().replace("_", "-").split("-")[0]
    return base if base in LANGUAGES else DEFAULT_LANGUAGE


def set_language(code: str) -> str:
    """Activate *code* and notify listeners.  Returns the code actually used."""
    global _current
    resolved = normalize(code)
    if resolved != _current:
        _current = resolved
        for cb in list(_listeners):
            try:
                cb(resolved)
            except Exception as exc:  # a broken listener must not block the switch
                logger.warning("i18n listener failed: %s", exc)
    return resolved


def get_language() -> str:
    return _current


def register(callback: Callable[[str], None]) -> None:
    """Call *callback* with the new code whenever the language changes."""
    if callback not in _listeners:
        _listeners.append(callback)


def unregister(callback: Callable[[str], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def available() -> list[dict[str, str]]:
    """Language list for the API / pickers: ``[{code, label}, ...]``."""
    return [{"code": code, "label": label} for code, label in LANGUAGES.items()]


def t(key: str, **kwargs: Any) -> str:
    """Translate *key* into the active language.

    ``kwargs`` are applied with str.format, e.g. ``t("queue.count", active=2)``.
    """
    text = CATALOG.get(_current, {}).get(key)
    if text is None:
        text = CATALOG.get("en", {}).get(key)
    if text is None:
        text = CATALOG.get("vi", {}).get(key)
    if text is None:
        logger.debug("i18n: missing key %r", key)
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("i18n: format failed for %r: %s", key, exc)
    return text
