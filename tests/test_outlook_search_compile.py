"""Compile + structural tests for the Outlook folder-scanning AppleScript bridges.

This file exists because of a real production bug: a reply email filed by the
user into a nested Outlook folder (e.g. ``Inbox/Clients/XYZ``) was silently
dropped by the sync sweep even though its body carried valid ``[PDB:]`` ref
tags. Root cause was that ``search_all_folders`` and ``get_flagged_emails``
both iterated only ``every mail folder of default account`` (top level) and
never descended into subfolders. The fix added a recursive ``scanTree`` /
``scanFlaggedTree`` handler.

Two safeguards live here:

1. ``osacompile`` smoke test — same pattern as ``test_outlook_contacts_compile``.
   AppleScript compile errors aren't caught by ``try`` blocks, so every script
   string we emit must parse before it runs.
2. Recursion regression guard — the script body must contain language that
   walks subfolders (``mail folders of f`` reference inside the recursive
   handler). If a future refactor accidentally drops the recursion, the
   silent-drop bug returns; this assertion catches it before shipping.

Skipped automatically when ``osacompile`` isn't available (non-mac CI).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from policydb import outlook

HAS_OSACOMPILE = shutil.which("osacompile") is not None


def _capture_script(fn, *args, **kwargs) -> str:
    """Invoke fn and return the AppleScript string it would have run."""
    captured: list[str] = []

    def fake_run(script: str, timeout=None):
        captured.append(script)
        return {"ok": True, "raw": "[]"}

    with patch.object(outlook, "is_outlook_available", return_value=True):
        with patch.object(outlook, "_run_applescript", side_effect=fake_run):
            fn(*args, **kwargs)

    assert captured, "no AppleScript was generated"
    return captured[-1]


def _assert_compiles(script: str, label: str) -> None:
    """osacompile must accept the script, or we fail with the compiler diag."""
    with tempfile.NamedTemporaryFile(suffix=".applescript", mode="w", delete=False) as tmp:
        tmp.write(script)
        src = Path(tmp.name)
    dst = src.with_suffix(".scpt")
    try:
        r = subprocess.run(
            ["osacompile", "-o", str(dst), str(src)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.fail(
                f"{label} AppleScript failed to compile:\n"
                f"  stderr: {r.stderr.strip()}\n"
                f"  script length: {len(script)} chars"
            )
    finally:
        src.unlink(missing_ok=True)
        if dst.exists():
            dst.unlink()


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_search_all_folders_compiles_with_category():
    script = _capture_script(outlook.search_all_folders, datetime(2026, 4, 15), "PDB")
    _assert_compiles(script, "search_all_folders(category=PDB)")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_search_all_folders_compiles_without_category():
    script = _capture_script(outlook.search_all_folders, datetime(2026, 4, 15), "")
    _assert_compiles(script, "search_all_folders(no category)")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_get_flagged_emails_compiles():
    script = _capture_script(outlook.get_flagged_emails, datetime(2026, 4, 15))
    _assert_compiles(script, "get_flagged_emails")


def test_search_all_folders_recurses_into_subfolders():
    """Regression guard: script must descend into subfolders, not just top level.

    Ref: production bug where an email filed into ``Inbox/Clients/XYZ`` was
    silently dropped because the bridge only iterated the top-level folder
    list. The recursive ``scanTree`` handler is what fixed it; if a future
    refactor removes the recursion the bug returns silently.
    """
    script = _capture_script(outlook.search_all_folders, datetime(2026, 4, 15), "PDB")
    # The recursive handler exists
    assert "scanTree" in script, "scanTree handler missing — recursion was dropped"
    # The handler reads subfolders of the current folder
    assert "mail folders of f" in script, "subfolder enumeration removed"
    # The handler calls itself
    assert "my scanTree" in script, "scanTree never recurses into subfolders"


def test_get_flagged_emails_recurses_into_subfolders():
    """Same regression guard for the flagged-emails sweep."""
    script = _capture_script(outlook.get_flagged_emails, datetime(2026, 4, 15))
    assert "scanFlaggedTree" in script, "scanFlaggedTree handler missing"
    assert "mail folders of f" in script, "subfolder enumeration removed"
    assert "my scanFlaggedTree" in script, "scanFlaggedTree never recurses"
