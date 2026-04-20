"""Platform-aware paths for PolicyDB. Used by both dev (CLI) and packaged (desktop) runs.

On macOS DATA_DIR is ``~/.policydb/`` (matches the historical dev install).
On Windows DATA_DIR is ``%APPDATA%/PolicyDB/``. Both are created on import.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Return the per-install data root. Creates the directory if missing."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        root = Path(appdata) / "PolicyDB"
    else:
        root = Path.home() / ".policydb"
    root.mkdir(parents=True, exist_ok=True)
    return root


DATA_DIR: Path = data_dir()


def db_path() -> Path:
    """SQLite DB path inside the data directory."""
    return data_dir() / "policydb.sqlite"


def config_path() -> Path:
    """config.yaml path inside the data directory."""
    return data_dir() / "config.yaml"


def outlook_available() -> bool:
    """True when the current platform supports the Outlook AppleScript bridge.

    AppleScript is macOS-only, so Windows / Linux return False and the Jinja
    global wired in app.py hides Outlook-dependent UI.
    """
    return sys.platform == "darwin"
