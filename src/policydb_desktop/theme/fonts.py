"""SF Pro + SF Mono font configuration — native macOS typography."""

# Font families
DISPLAY_FAMILY = "-apple-system"  # SF Pro Display for headings (auto via system)
BODY_FAMILY = "-apple-system"     # SF Pro Text for body (auto via system)
MONO_FAMILY = "SF Mono"           # SF Mono for data values
MONO_FALLBACK = "Menlo"           # Fallback monospace

# For QFont construction (PySide6 uses actual font names, not CSS aliases)
QT_DISPLAY = ".AppleSystemUIFont"  # Qt's name for SF Pro
QT_BODY = ".AppleSystemUIFont"
QT_MONO = "SF Mono"
QT_MONO_FALLBACK = "Menlo"

# Font sizes (in points)
SIZE_XS = 9       # Version numbers, fine print
SIZE_SM = 10      # Labels, badges, table headers
SIZE_BASE = 11    # Body text, table data
SIZE_MD = 12      # Nav items, card titles
SIZE_LG = 13      # Section headings
SIZE_XL = 15      # Sidebar logo
SIZE_2XL = 20     # Page titles
SIZE_3XL = 28     # Metric card numbers

# Font weights
WEIGHT_NORMAL = 400
WEIGHT_MEDIUM = 500
WEIGHT_SEMIBOLD = 600
WEIGHT_BOLD = 700

# Letter spacing
SPACING_TIGHT = -0.5   # Headings, large numbers
SPACING_NORMAL = 0
SPACING_WIDE = 0.5     # Uppercase labels
