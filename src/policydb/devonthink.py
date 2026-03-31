"""DevonThink 3 integration via AppleScript for macOS.

Provides:
- is_devonthink_available() — check if DT3 is installed
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


def is_devonthink_available() -> bool:
    """Check if DevonThink 3 is installed on this Mac."""
    return shutil.which("osascript") is not None and _dt_app_exists()


def _dt_app_exists() -> bool:
    """Check if DEVONthink 3 application bundle exists."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'id of application "DEVONthink 3"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


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
    """Call AppleScript to get item metadata from DevonThink 3.

    Returns dict with keys: name, type, size, path, filename, uuid, url
    Returns None if DevonThink is unavailable or item not found.
    """
    script = f"""
tell application "DEVONthink 3"
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
