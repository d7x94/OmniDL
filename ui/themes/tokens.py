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

_DARK: dict[str, str] = {
    # Backgrounds
    "bg":              "#0B0B13",
    "surface":         "#111119",
    "surface2":        "#17171F",
    "surface3":        "#1C1C28",
    "sidebar":         "#0D0D16",
    # Borders
    "border":          "#1E1E2E",
    "border2":         "#26263A",
    # Brand / accent
    "primary":         "#6366F1",
    "primary_hover":   "#4F52D8",
    "primary_dim":     "#1E1F42",
    "primary_text":    "#A5B4FC",
    # Text
    "text":            "#F1F5F9",
    "text2":           "#94A3B8",
    "text3":           "#475569",
    "text_inv":        "#0F172A",
    # Semantic
    "success":         "#10B981",
    "success_bg":      "#052010",
    "success_text":    "#34D399",
    "error":           "#EF4444",
    "error_bg":        "#200808",
    "error_text":      "#FCA5A5",
    "warning":         "#F59E0B",
    "warning_bg":      "#1A1000",
    "warning_text":    "#FCD34D",
    "info":            "#38BDF8",
    "info_bg":         "#051A28",
    # Progress gradient
    "prog_start":      "#6366F1",
    "prog_end":        "#38BDF8",
    "prog_track":      "#1A1A28",
    # Status bar
    "statusbar":       "#090910",
    # Input
    "input":           "#0D0D18",
    # Scroll
    "scrollbar":       "#1E1E2E",
    "scrollbar_hover": "#2A2A3E",
    # Close button
    "close_hover":     "#4A0D0D",
}

_LIGHT: dict[str, str] = {
    # Backgrounds
    "bg":              "#F0F2F8",
    "surface":         "#FFFFFF",
    "surface2":        "#F4F6FB",
    "surface3":        "#E8ECF5",
    "sidebar":         "#FAFBFE",
    # Borders
    "border":          "#E2E8F0",
    "border2":         "#CBD5E1",
    # Brand / accent
    "primary":         "#6366F1",
    "primary_hover":   "#4F52D8",
    "primary_dim":     "#EEF2FF",
    "primary_text":    "#4F46E5",
    # Text
    "text":            "#0F172A",
    "text2":           "#475569",
    "text3":           "#94A3B8",
    "text_inv":        "#F1F5F9",
    # Semantic
    "success":         "#059669",
    "success_bg":      "#ECFDF5",
    "success_text":    "#047857",
    "error":           "#DC2626",
    "error_bg":        "#FEF2F2",
    "error_text":      "#B91C1C",
    "warning":         "#D97706",
    "warning_bg":      "#FFFBEB",
    "warning_text":    "#92400E",
    "info":            "#0284C7",
    "info_bg":         "#F0F9FF",
    # Progress gradient
    "prog_start":      "#6366F1",
    "prog_end":        "#38BDF8",
    "prog_track":      "#E2E8F0",
    # Status bar
    "statusbar":       "#FFFFFF",
    # Input
    "input":           "#F8FAFC",
    # Scroll
    "scrollbar":       "#E2E8F0",
    "scrollbar_hover": "#CBD5E1",
    # Close button
    "close_hover":     "#FEE2E2",
}


# ── Manager ───────────────────────────────────────────────────────────────────

class _ThemeManager:
    """Singleton managing the active colour palette and change callbacks."""

    def __init__(self) -> None:
        self._mode: str = "dark"
        self._palette: dict[str, str] = dict(_DARK)
        self._callbacks: list[Callable] = []

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch palette and notify all registered widgets."""
        if mode == self._mode:
            return
        self._mode = mode
        self._palette = dict(_DARK if mode != "light" else _LIGHT)
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
