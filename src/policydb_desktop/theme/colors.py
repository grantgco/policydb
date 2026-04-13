"""Slate Command color palette — dark professional with indigo accent."""

# Base backgrounds (darkest to lightest)
BG_BASE = "#111827"        # Main content background
BG_SURFACE = "#1f2937"     # Cards, sidebar, panels
BG_ELEVATED = "#374151"    # Hover states, active nav, filter pills
BG_INSET = "#0f1623"       # Inset areas (inside cards, table rows)

# Borders
BORDER_DEFAULT = "#374151"  # Standard borders
BORDER_SUBTLE = "rgba(55, 65, 81, 0.5)"  # Table row dividers
BORDER_FOCUS = "#6366f1"    # Focus rings

# Text
TEXT_PRIMARY = "#f9fafb"    # Headings, primary content
TEXT_SECONDARY = "#d1d5db"  # Body text, table data
TEXT_MUTED = "#9ca3af"      # Labels, placeholders
TEXT_DIM = "#6b7280"        # Subtle metadata, timestamps
TEXT_FAINT = "#4b5563"      # Version numbers, disabled

# Accent — Indigo
ACCENT = "#6366f1"          # Primary accent (buttons, active states, links)
ACCENT_HOVER = "#5558e6"    # Button hover
ACCENT_MUTED = "rgba(99, 102, 241, 0.15)"  # Badge backgrounds, subtle highlights
ACCENT_TEXT = "#a5b4fc"     # Accent text on dark backgrounds

# Status colors
STATUS_DANGER = "#ef4444"
STATUS_DANGER_BG = "#7c2d12"
STATUS_DANGER_TEXT = "#fdba74"

STATUS_WARNING = "#f59e0b"
STATUS_WARNING_BG = "rgba(234, 179, 8, 0.15)"
STATUS_WARNING_TEXT = "#fde047"

STATUS_SUCCESS = "#10b981"
STATUS_SUCCESS_BG = "#064e3b"
STATUS_SUCCESS_TEXT = "#6ee7b7"

STATUS_INFO_BG = "rgba(99, 102, 241, 0.15)"
STATUS_INFO_TEXT = "#a5b4fc"

# Sidebar
SIDEBAR_BG = BG_SURFACE
SIDEBAR_ACTIVE = BG_ELEVATED
SIDEBAR_TEXT = TEXT_MUTED
SIDEBAR_TEXT_ACTIVE = TEXT_PRIMARY
SIDEBAR_BADGE_BG = STATUS_DANGER
SIDEBAR_BADGE_TEXT = "#ffffff"

# Scrollbar
SCROLLBAR_BG = BG_BASE
SCROLLBAR_HANDLE = BG_ELEVATED
SCROLLBAR_HANDLE_HOVER = "#4b5563"

# Selection
SELECTION_BG = "rgba(99, 102, 241, 0.25)"
SELECTION_TEXT = TEXT_PRIMARY
