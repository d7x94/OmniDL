"""
ui/themes/tokens.py
Centralized design-token system for OmniDL.

Usage:
    from ui.themes.tokens import T

    widget.configure(fg_color=T.surface)

Instant theme switching:
    from ui.themes.tokens import ThemeManager
    ThemeManager.set_mode("light")   # or "dark"
"""

from __future__ import annotations

from typing import Callable

# ── Palettes ──────────────────────────────────────────────────────────────────

_VIOLET: dict[str, str] = {
    "bg": "#0D0D14",
    "surface": "#13131F",
    "surface2": "#1A1A2B",
    "surface3": "#202033",
    "sidebar": "#0F0F1A",
    "border": "#1E1E30",
    "border2": "#2A2A40",
    "primary": "#7C3AED",
    "primary_hover": "#6D28D9",
    "primary_dim": "#2D1F4E",
    "primary_text": "#A78BFA",
    "accent": "#6366F1",
    "accent_text": "#A5B4FC",
    "text": "#F0F0FF",
    "text2": "#8B8BAF",
    "text3": "#52526A",
    "text_inv": "#0D0D14",
    "success": "#10B981",
    "success_bg": "#052010",
    "success_text": "#34D399",
    "error": "#EF4444",
    "error_bg": "#200808",
    "error_text": "#FCA5A5",
    "warning": "#F59E0B",
    "warning_bg": "#1A1000",
    "warning_text": "#FCD34D",
    "info": "#A78BFA",
    "info_bg": "#1E1040",
    "edit_bg": "#2D0A1E",
    "edit_text": "#F9A8D4",
    "prog_start": "#7C3AED",
    "prog_end": "#A855F7",
    "prog_track": "#1A1A2B",
    "statusbar": "#0A0A12",
    "input": "#0F0F1C",
    "scrollbar": "#1E1E30",
    "scrollbar_hover": "#2A2A40",
    "close_hover": "#4A0D0D",
    "divider": "#181828",
    "card_hover": "#1C1C2E",
}

_LIGHT: dict[str, str] = {
    # Backgrounds
    "bg": "#F0F2F8",
    "surface": "#FFFFFF",
    "surface2": "#F4F6FB",
    "surface3": "#E8ECF5",
    "sidebar": "#FAFBFE",
    # Borders
    "border": "#E2E8F0",
    "border2": "#CBD5E1",
    # Brand / accent
    "primary": "#6366F1",
    "primary_hover": "#4F52D8",
    "primary_dim": "#EEF2FF",
    "primary_text": "#4F46E5",
    "accent": "#6366F1",
    "accent_text": "#A5B4FC",
    # Text
    "text": "#0F172A",
    "text2": "#475569",
    "text3": "#94A3B8",
    "text_inv": "#F1F5F9",
    # Semantic
    "success": "#059669",
    "success_bg": "#ECFDF5",
    "success_text": "#047857",
    "error": "#DC2626",
    "error_bg": "#FEF2F2",
    "error_text": "#B91C1C",
    "warning": "#D97706",
    "warning_bg": "#FFFBEB",
    "warning_text": "#92400E",
    "info": "#0284C7",
    "info_bg": "#F0F9FF",
    "edit_bg": "#FCE7F3",
    "edit_text": "#EC4899",
    # Progress gradient
    "prog_start": "#6366F1",
    "prog_end": "#38BDF8",
    "prog_track": "#E2E8F0",
    # Status bar
    "statusbar": "#FFFFFF",
    # Input
    "input": "#F8FAFC",
    # Scroll
    "scrollbar": "#E2E8F0",
    "scrollbar_hover": "#CBD5E1",
    # Close button
    "close_hover": "#FEE2E2",
    # Divider & card hover
    "divider": "#EDF0F7",
    "card_hover": "#F0F3FB",
}

_MIDNIGHT: dict[str, str] = {
    "bg": "#000000",
    "surface": "#090909",
    "surface2": "#0E0E0E",
    "surface3": "#141414",
    "sidebar": "#050505",
    "border": "#1C1C1C",
    "border2": "#242424",
    "primary": "#22D3EE",
    "primary_hover": "#06B6D4",
    "primary_dim": "#042830",
    "primary_text": "#67E8F9",
    "accent": "#6366F1",
    "accent_text": "#A5B4FC",
    "text": "#F8FAFC",
    "text2": "#94A3B8",
    "text3": "#475569",
    "text_inv": "#000000",
    "success": "#10B981",
    "success_bg": "#031A0D",
    "success_text": "#34D399",
    "error": "#EF4444",
    "error_bg": "#1A0404",
    "error_text": "#FCA5A5",
    "warning": "#F59E0B",
    "warning_bg": "#140D00",
    "warning_text": "#FCD34D",
    "info": "#22D3EE",
    "info_bg": "#032028",
    "edit_bg": "#200814",
    "edit_text": "#F9A8D4",
    "prog_start": "#22D3EE",
    "prog_end": "#818CF8",
    "prog_track": "#141414",
    "statusbar": "#030303",
    "input": "#0A0A0A",
    "scrollbar": "#1C1C1C",
    "scrollbar_hover": "#282828",
    "close_hover": "#3D0808",
    "divider": "#131313",
    "card_hover": "#121212",
}

# ── Palette registry ───────────────────────────────────────────────────────────

# Maps theme name → palette dict.  Order determines the Settings OptionMenu order.
_PALETTES: dict[str, dict[str, str]] = {
    "violet": _VIOLET,
    "midnight": _MIDNIGHT,
    "light": _LIGHT,
}

# Maps theme name → CustomTkinter base appearance mode ("dark" or "light").
# ctk.set_appearance_mode() only accepts "dark", "light", or "system".
_CTK_BASE: dict[str, str] = {
    "violet": "dark",
    "midnight": "dark",
    "light": "light",
}

# Ordered list of all custom theme names — exported for Settings UI.
THEME_NAMES: list[str] = list(_PALETTES.keys())

# ── Tab accents ────────────────────────────────────────────────────────────────

TAB_ACCENTS: dict[str, dict[str, str]] = {
    "home": {"accent": "#6366F1", "text": "#A5B4FC"},
    "queue": {"accent": "#6366F1", "text": "#A5B4FC"},
    "batch": {"accent": "#818CF8", "text": "#C7D2FE"},
    "live_monitor": {"accent": "#EF4444", "text": "#FCA5A5"},
    "convert": {"accent": "#14B8A6", "text": "#5EEAD4"},
    "history": {"accent": "#F59E0B", "text": "#FCD34D"},
    "settings": {"accent": "#6B7A8E", "text": "#94A3B8"},
    "special_dl": {"accent": "#A78BFA", "text": "#C4B5FD"},
    "editor": {"accent": "#EC4899", "text": "#F9A8D4"},
    "docs": {"accent": "#0EA5E9", "text": "#7DD3FC"},
}


# ── Manager ───────────────────────────────────────────────────────────────────


class _ThemeManager:
    """Singleton managing the active colour palette and change callbacks."""

    def __init__(self) -> None:
        self._mode: str = "violet"
        self._palette: dict[str, str] = dict(_VIOLET)
        self._callbacks: list[Callable] = []

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def ctk_base(self) -> str:
        """CustomTkinter base appearance mode for the active theme.

        ctk.set_appearance_mode() only accepts "dark", "light", or "system".
        This maps every custom theme to the correct CTk base so widgets render
        with the right built-in shadow/highlight behaviour.
        """
        return _CTK_BASE.get(self._mode, "dark")

    @property
    def is_dark(self) -> bool:
        """True when the active theme uses a dark CTk base."""
        return self.ctk_base == "dark"

    def set_mode(self, mode: str) -> None:
        """Switch palette and notify all registered widgets.

        Accepts any key in _PALETTES ("dark", "light", "midnight", "ocean",
        "forest", "rose", "amber", "nord", "solarized", "lavender") as well
        as the legacy "system" string (falls back to "dark").
        """
        if mode == self._mode:
            return
        # Normalise legacy / unknown values to "violet"
        self._mode = mode if mode in _PALETTES else "violet"
        self._palette = dict(_PALETTES[self._mode])
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                pass

    def register(self, callback: Callable) -> None:
        """Register a zero-arg callable to be invoked on theme change."""
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister(self, callback: Callable) -> None:
        self._callbacks = [c for c in self._callbacks if c is not callback]

    # ── Dict-style access ─────────────────────────────────────────────────

    def __getattr__(self, key: str) -> str:
        try:
            return self._palette[key]
        except KeyError:
            raise AttributeError(f"Unknown token: {key!r}") from None

    def get(self, key: str, fallback: str = "") -> str:
        return self._palette.get(key, fallback)


ThemeManager = _ThemeManager()

# `T` is the public shorthand — import this everywhere
T = ThemeManager
