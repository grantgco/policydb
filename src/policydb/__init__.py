"""PolicyDB — Insurance Book of Business Management System."""

_FALLBACK_VERSION = "7.1.0"


def _get_version():
    """Derive version from git describe. Falls back to static version
    when git is unavailable (e.g., installed from wheel)."""
    import subprocess
    from pathlib import Path

    try:
        repo = Path(__file__).resolve().parent.parent.parent
        if not (repo / ".git").exists() and not (repo / ".git").is_file():
            return _FALLBACK_VERSION
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        # git describe returns: v7.1.0 (on tag) or v7.1.0-14-g55abef4 (after tag)
        if out.startswith("v"):
            out = out[1:]
        # Convert v7.1.0-14-g55abef4 → 7.1.0+14.g55abef4 (PEP 440 local version)
        parts = out.split("-", 1)
        if len(parts) == 1:
            return parts[0]  # exactly on tag: "7.1.0"
        base = parts[0]
        rest = parts[1].replace("-", ".")  # "14-g55abef4" → "14.g55abef4"
        return f"{base}+{rest}"
    except Exception:
        return _FALLBACK_VERSION


__version__ = _get_version()
