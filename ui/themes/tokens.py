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

# ── Extra dark themes ──────────────────────────────────────────────────────────

_MIDNIGHT: dict[str, str] = {
    # OLED-friendly pure black with electric cyan accent
    "bg":              "#000000",
    "surface":         "#090909",
    "surface2":        "#0E0E0E",
    "surface3":        "#141414",
    "sidebar":         "#050505",
    "border":          "#1C1C1C",
    "border2":         "#242424",
    "primary":         "#22D3EE",
    "primary_hover":   "#06B6D4",
    "primary_dim":     "#042830",
    "primary_text":    "#67E8F9",
    "text":            "#F8FAFC",
    "text2":           "#94A3B8",
    "text3":           "#475569",
    "text_inv":        "#000000",
    "success":         "#10B981",
    "success_bg":      "#031A0D",
    "success_text":    "#34D399",
    "error":           "#EF4444",
    "error_bg":        "#1A0404",
    "error_text":      "#FCA5A5",
    "warning":         "#F59E0B",
    "warning_bg":      "#140D00",
    "warning_text":    "#FCD34D",
    "info":            "#22D3EE",
    "info_bg":         "#032028",
    "prog_start":      "#22D3EE",
    "prog_end":        "#818CF8",
    "prog_track":      "#141414",
    "statusbar":       "#030303",
    "input":           "#0A0A0A",
    "scrollbar":       "#1C1C1C",
    "scrollbar_hover": "#282828",
    "close_hover":     "#3D0808",
}

_OCEAN: dict[str, str] = {
    # Deep sea teal — blue-teal gradients with cyan accent
    "bg":              "#060D1B",
    "surface":         "#0A1628",
    "surface2":        "#0F1E35",
    "surface3":        "#152545",
    "sidebar":         "#080F20",
    "border":          "#152D4A",
    "border2":         "#1E3D60",
    "primary":         "#06B6D4",
    "primary_hover":   "#0891B2",
    "primary_dim":     "#062030",
    "primary_text":    "#38BDF8",
    "text":            "#E2F0FF",
    "text2":           "#7EB8D5",
    "text3":           "#3D6A82",
    "text_inv":        "#060D1B",
    "success":         "#10B981",
    "success_bg":      "#031A10",
    "success_text":    "#34D399",
    "error":           "#EF4444",
    "error_bg":        "#1A0608",
    "error_text":      "#FCA5A5",
    "warning":         "#F59E0B",
    "warning_bg":      "#140E00",
    "warning_text":    "#FCD34D",
    "info":            "#38BDF8",
    "info_bg":         "#041828",
    "prog_start":      "#06B6D4",
    "prog_end":        "#38BDF8",
    "prog_track":      "#0F1E35",
    "statusbar":       "#040A14",
    "input":           "#080E1E",
    "scrollbar":       "#152D4A",
    "scrollbar_hover": "#1E3D60",
    "close_hover":     "#3A0A0A",
}

_FOREST: dict[str, str] = {
    # Emerald woods — deep greens with emerald accent
    "bg":              "#040C06",
    "surface":         "#081509",
    "surface2":        "#0D1E0E",
    "surface3":        "#122514",
    "sidebar":         "#060F08",
    "border":          "#142818",
    "border2":         "#1C3820",
    "primary":         "#10B981",
    "primary_hover":   "#059669",
    "primary_dim":     "#042016",
    "primary_text":    "#34D399",
    "text":            "#ECFEF5",
    "text2":           "#6EBD95",
    "text3":           "#2E6B4A",
    "text_inv":        "#040C06",
    "success":         "#10B981",
    "success_bg":      "#031A0D",
    "success_text":    "#34D399",
    "error":           "#EF4444",
    "error_bg":        "#1A0404",
    "error_text":      "#FCA5A5",
    "warning":         "#F59E0B",
    "warning_bg":      "#140D00",
    "warning_text":    "#FCD34D",
    "info":            "#38BDF8",
    "info_bg":         "#041828",
    "prog_start":      "#10B981",
    "prog_end":        "#38BDF8",
    "prog_track":      "#0D1E0E",
    "statusbar":       "#020804",
    "input":           "#060E08",
    "scrollbar":       "#142818",
    "scrollbar_hover": "#1C3820",
    "close_hover":     "#3A0808",
}

_ROSE: dict[str, str] = {
    # Dark crimson rose — deep reds with rose accent
    "bg":              "#0D0408",
    "surface":         "#160609",
    "surface2":        "#1C0A0E",
    "surface3":        "#230D12",
    "sidebar":         "#0F0508",
    "border":          "#2A0F16",
    "border2":         "#38151E",
    "primary":         "#F43F5E",
    "primary_hover":   "#E11D48",
    "primary_dim":     "#280810",
    "primary_text":    "#FB7185",
    "text":            "#FFF0F3",
    "text2":           "#D4859A",
    "text3":           "#7E3A50",
    "text_inv":        "#0D0408",
    "success":         "#10B981",
    "success_bg":      "#031A0D",
    "success_text":    "#34D399",
    "error":           "#F43F5E",
    "error_bg":        "#200610",
    "error_text":      "#FB7185",
    "warning":         "#F59E0B",
    "warning_bg":      "#140D00",
    "warning_text":    "#FCD34D",
    "info":            "#38BDF8",
    "info_bg":         "#041828",
    "prog_start":      "#F43F5E",
    "prog_end":        "#FB923C",
    "prog_track":      "#1C0A0E",
    "statusbar":       "#070204",
    "input":           "#100408",
    "scrollbar":       "#2A0F16",
    "scrollbar_hover": "#38151E",
    "close_hover":     "#3D0810",
}

_AMBER: dict[str, str] = {
    # Warm golden dark — black base with amber/gold accent
    "bg":              "#0D0900",
    "surface":         "#141200",
    "surface2":        "#1A1800",
    "surface3":        "#211E00",
    "sidebar":         "#0F0B00",
    "border":          "#2C2600",
    "border2":         "#3A3200",
    "primary":         "#F59E0B",
    "primary_hover":   "#D97706",
    "primary_dim":     "#241A00",
    "primary_text":    "#FCD34D",
    "text":            "#FFFBEB",
    "text2":           "#C4A040",
    "text3":           "#6B5C20",
    "text_inv":        "#0D0900",
    "success":         "#10B981",
    "success_bg":      "#031A0D",
    "success_text":    "#34D399",
    "error":           "#EF4444",
    "error_bg":        "#1A0404",
    "error_text":      "#FCA5A5",
    "warning":         "#F59E0B",
    "warning_bg":      "#1A1000",
    "warning_text":    "#FCD34D",
    "info":            "#38BDF8",
    "info_bg":         "#041828",
    "prog_start":      "#F59E0B",
    "prog_end":        "#F97316",
    "prog_track":      "#1A1800",
    "statusbar":       "#070500",
    "input":           "#100E00",
    "scrollbar":       "#2C2600",
    "scrollbar_hover": "#3A3200",
    "close_hover":     "#3D0A00",
}

_NORD: dict[str, str] = {
    # Nord color scheme — cool arctic blues and greys
    "bg":              "#2E3440",
    "surface":         "#3B4252",
    "surface2":        "#434C5E",
    "surface3":        "#4C566A",
    "sidebar":         "#292E3C",
    "border":          "#3B4252",
    "border2":         "#4C566A",
    "primary":         "#88C0D0",
    "primary_hover":   "#81A1C1",
    "primary_dim":     "#3A4A52",
    "primary_text":    "#8FBCBB",
    "text":            "#ECEFF4",
    "text2":           "#D8DEE9",
    "text3":           "#7D8FA3",
    "text_inv":        "#2E3440",
    "success":         "#A3BE8C",
    "success_bg":      "#2E3B2E",
    "success_text":    "#A3BE8C",
    "error":           "#BF616A",
    "error_bg":        "#3D2426",
    "error_text":      "#BF616A",
    "warning":         "#EBCB8B",
    "warning_bg":      "#3D3420",
    "warning_text":    "#EBCB8B",
    "info":            "#88C0D0",
    "info_bg":         "#2E3D44",
    "prog_start":      "#88C0D0",
    "prog_end":        "#81A1C1",
    "prog_track":      "#434C5E",
    "statusbar":       "#252A36",
    "input":           "#2E3440",
    "scrollbar":       "#434C5E",
    "scrollbar_hover": "#4C566A",
    "close_hover":     "#4D2224",
}

# ── Extra light themes ─────────────────────────────────────────────────────────

_SOLARIZED: dict[str, str] = {
    # Solarized light — warm cream background with muted semantic colors
    "bg":              "#FDF6E3",
    "surface":         "#EEE8D5",
    "surface2":        "#E8E2CC",
    "surface3":        "#DDD8C4",
    "sidebar":         "#F5EED8",
    "border":          "#D4CEBC",
    "border2":         "#C8C2AE",
    "primary":         "#268BD2",
    "primary_hover":   "#1A73B8",
    "primary_dim":     "#D6EAF6",
    "primary_text":    "#1A6C9C",
    "text":            "#073642",
    "text2":           "#586E75",
    "text3":           "#839496",
    "text_inv":        "#FDF6E3",
    "success":         "#859900",
    "success_bg":      "#EAF0D6",
    "success_text":    "#5E6B00",
    "error":           "#DC322F",
    "error_bg":        "#FAE8E7",
    "error_text":      "#A81C1A",
    "warning":         "#B58900",
    "warning_bg":      "#F5EDD5",
    "warning_text":    "#7A5C00",
    "info":            "#268BD2",
    "info_bg":         "#D8EEF9",
    "prog_start":      "#268BD2",
    "prog_end":        "#2AA198",
    "prog_track":      "#DDD8C4",
    "statusbar":       "#EEE8D5",
    "input":           "#F5EED8",
    "scrollbar":       "#D4CEBC",
    "scrollbar_hover": "#C8C2AE",
    "close_hover":     "#FAE8E7",
}

_LAVENDER: dict[str, str] = {
    # Soft lavender light — white base with purple accent
    "bg":              "#F5F0FF",
    "surface":         "#FFFFFF",
    "surface2":        "#EFE8FF",
    "surface3":        "#E4D8FF",
    "sidebar":         "#FAF7FF",
    "border":          "#DDD0FF",
    "border2":         "#C8B8F0",
    "primary":         "#7C3AED",
    "primary_hover":   "#6D28D9",
    "primary_dim":     "#EDE9FE",
    "primary_text":    "#5B21B6",
    "text":            "#1E1040",
    "text2":           "#5B4B80",
    "text3":           "#9980C4",
    "text_inv":        "#F5F0FF",
    "success":         "#059669",
    "success_bg":      "#ECFDF5",
    "success_text":    "#047857",
    "error":           "#DC2626",
    "error_bg":        "#FEF2F2",
    "error_text":      "#B91C1C",
    "warning":         "#D97706",
    "warning_bg":      "#FFFBEB",
    "warning_text":    "#92400E",
    "info":            "#7C3AED",
    "info_bg":         "#EDE9FE",
    "prog_start":      "#7C3AED",
    "prog_end":        "#EC4899",
    "prog_track":      "#E4D8FF",
    "statusbar":       "#FFFFFF",
    "input":           "#FAF7FF",
    "scrollbar":       "#DDD0FF",
    "scrollbar_hover": "#C8B8F0",
    "close_hover":     "#FEE2E2",
}

# ── Palette registry ───────────────────────────────────────────────────────────

# Maps theme name → palette dict.  Order determines the Settings OptionMenu order.
_PALETTES: dict[str, dict[str, str]] = {
    "dark":       _DARK,
    "midnight":   _MIDNIGHT,
    "ocean":      _OCEAN,
    "forest":     _FOREST,
    "rose":       _ROSE,
    "amber":      _AMBER,
    "nord":       _NORD,
    "light":      _LIGHT,
    "solarized":  _SOLARIZED,
    "lavender":   _LAVENDER,
}

# Maps theme name → CustomTkinter base appearance mode ("dark" or "light").
# ctk.set_appearance_mode() only accepts "dark", "light", or "system".
_CTK_BASE: dict[str, str] = {
    "dark":       "dark",
    "midnight":   "dark",
    "ocean":      "dark",
    "forest":     "dark",
    "rose":       "dark",
    "amber":      "dark",
    "nord":       "dark",
    "light":      "light",
    "solarized":  "light",
    "lavender":   "light",
}

# Ordered list of all custom theme names — exported for Settings UI.
THEME_NAMES: list[str] = list(_PALETTES.keys())


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
        # Normalise legacy / unknown values to "dark"
        self._mode = mode if mode in _PALETTES else "dark"
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
