"""Theme package — Marsh brand colors, fonts, and QSS stylesheet."""

from pathlib import Path

QSS_PATH = Path(__file__).parent / "marsh.qss"


def load_stylesheet() -> str:
    """Load the master QSS stylesheet."""
    return QSS_PATH.read_text(encoding="utf-8")
