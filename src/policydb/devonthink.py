"""DevonThink integration via AppleScript for macOS.

Provides:
- is_devonthink_available() — check if DT is installed
- parse_dt_link(input_str) — extract UUID from x-devonthink-item:// URL or raw UUID
- fetch_item_metadata(uuid) — AppleScript call to get item metadata
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

_DT_URL_PATTERN = re.compile(
    r"(?:x-devonthink-item://)?([A-Fa-f0-9-]{8,})", re.IGNORECASE
)

# Resolved app name — cached after first detection
_DT_APP_NAME: str | None = None
_DT_APP_NAMES = ["DEVONthink 3", "DEVONthink"]


def _resolve_dt_app_name() -> str | None:
    """Detect installed DevonThink app name. Cached per process."""
    global _DT_APP_NAME
    if _DT_APP_NAME is not None:
        return _DT_APP_NAME
    if shutil.which("osascript") is None:
        return None
    for name in _DT_APP_NAMES:
        try:
            result = subprocess.run(
                ["osascript", "-e", f'id of application "{name}"'],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                _DT_APP_NAME = name
                logger.info("DevonThink detected as: %s", name)
                return name
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def is_devonthink_available() -> bool:
    """Check if DevonThink is installed on this Mac."""
    return _resolve_dt_app_name() is not None


def parse_dt_link(input_str: str) -> str | None:
    """Extract UUID from x-devonthink-item://UUID or a raw UUID string.

    Returns the UUID string or None if no valid UUID found.
    """
    if not input_str:
        return None
    input_str = input_str.strip()
    m = _DT_URL_PATTERN.search(input_str)
    if m:
        return m.group(1)
    return None


def build_dt_url(uuid: str) -> str:
    """Build the x-devonthink-item:// URL from a UUID."""
    return f"x-devonthink-item://{uuid}"


def fetch_item_metadata(uuid: str) -> dict | None:
    """Call AppleScript to get item metadata from DevonThink.

    Returns dict with keys: name, type, size, path, filename, uuid, url
    Returns None if DevonThink is unavailable or item not found.
    """
    app_name = _resolve_dt_app_name()
    if not app_name:
        return None
    script = f"""
tell application "{app_name}"
    try
        set theRecord to get record with uuid "{uuid}"
        set theName to name of theRecord
        set theType to (type of theRecord) as string
        set theSize to size of theRecord
        set thePath to path of theRecord
        set theFilename to filename of theRecord
        return theName & "||" & theType & "||" & (theSize as text) & "||" & thePath & "||" & theFilename
    on error
        return "ERROR"
    end try
end tell
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip() or result.stdout.strip() == "ERROR":
            logger.warning("DevonThink metadata fetch failed for UUID %s: %s", uuid, result.stderr.strip())
            return None

        parts = result.stdout.strip().split("||")
        if len(parts) < 5:
            logger.warning("Unexpected AppleScript output for UUID %s: %s", uuid, result.stdout.strip())
            return None

        name, dt_type, size_str, path, filename = parts[0], parts[1], parts[2], parts[3], parts[4]

        try:
            size = int(float(size_str))
        except (ValueError, TypeError):
            size = 0

        mime_type = _dt_type_to_mime(dt_type, filename)

        return {
            "name": name,
            "type": dt_type,
            "size": size,
            "path": path,
            "filename": filename,
            "uuid": uuid,
            "url": build_dt_url(uuid),
            "mime_type": mime_type,
        }

    except subprocess.TimeoutExpired:
        logger.warning("DevonThink AppleScript timed out for UUID %s", uuid)
        return None
    except FileNotFoundError:
        logger.warning("osascript not found — not on macOS?")
        return None


def _dt_type_to_mime(dt_type: str, filename: str) -> str:
    """Map DevonThink type string to a MIME type, falling back to extension."""
    dt_type_lower = dt_type.lower().strip()

    type_map = {
        "pdf document": "application/pdf",
        "pdf+text": "application/pdf",
        "word processing": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "html": "text/html",
        "formatted note": "text/html",
        "plain text": "text/plain",
        "markdown": "text/markdown",
        "rtf": "application/rtf",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "tiff": "image/tiff",
        "gif": "image/gif",
    }

    for key, mime in type_map.items():
        if key in dt_type_lower:
            return mime

    # Fallback: extension-based
    ext_map = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".html": "text/html",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
    }
    if filename:
        import os
        ext = os.path.splitext(filename)[1].lower()
        if ext in ext_map:
            return ext_map[ext]

    return "application/octet-stream"
