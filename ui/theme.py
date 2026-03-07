"""
ui/theme.py
Centralised design-system constants.

BEFORE this file existed, the same hex literals were scattered across 7 files
(~150+ occurrences).  Import from here instead of inlining strings.

Issue fixed: #16 — hardcoded colour constants duplicated across all UI files.
"""

class Theme:
    # ── Backgrounds ───────────────────────────────────────────────────────
    BG_APP     = "#0A0A12"   # root window / tab background
    BG_SIDEBAR = "#06060D"   # sidebar + title bar
    BG_CONTENT = "#0D0D18"   # main content area
    BG_CARD    = "#12121E"   # card surfaces
    BG_ENTRY   = "#0D0D1A"   # text entry fields
    BG_ITEM    = "#0D0D1A"   # list-item backgrounds
    BG_SELECTED = "#1A1A2E"  # selected / active state

    # ── Borders / dividers ────────────────────────────────────────────────
    BORDER     = "#1E1E2E"
    BORDER_ENTRY = "#2D2D4A"

    # ── Hover states ──────────────────────────────────────────────────────
    HOVER      = "#1E1E2E"
    HOVER_MED  = "#2D2D4A"
    HOVER_STR  = "#3D3D6A"

    # ── Brand / accent ────────────────────────────────────────────────────
    PRIMARY    = "#6366F1"   # indigo — primary buttons, badges, highlights
    PRIMARY_HV = "#4F46E5"   # primary hover

    # ── Semantic colours ──────────────────────────────────────────────────
    SUCCESS    = "#1DB954"
    ERROR      = "#EF4444"
    WARNING    = "#F59E0B"
    INFO       = "#6366F1"   # same as PRIMARY for toast info

    # ── Text ──────────────────────────────────────────────────────────────
    TEXT_BRIGHT = "#F1F5F9"
    TEXT_MED    = "#94A3B8"
    TEXT_DIM    = "#64748B"
    TEXT_MUTED  = "#334155"
    TEXT_INVIS  = "#1E1E2E"  # version string at bottom of sidebar
