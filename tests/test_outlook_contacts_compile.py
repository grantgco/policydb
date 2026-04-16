"""Compile-only smoke test for the Outlook contact AppleScript bridges.

AppleScript try/end try blocks only catch RUNTIME errors. A bad property
name (e.g. `business phone` vs `business phone number`) is a COMPILE error
that aborts the whole script before any try runs. Before the April 2026 bug
hunt, outlook_contacts.py had five such errors and contact sync never
worked. This test prevents regressions by shelling out to `osacompile` on
every script that outlook_contacts.py produces.

Skipped automatically when osacompile isn't available (non-mac CI).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# The worktree's src/ must beat the editable-installed main repo copy so we
# test the code on THIS branch rather than whatever's installed globally.
_WORKTREE_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_WORKTREE_SRC) not in sys.path:
    sys.path.insert(0, str(_WORKTREE_SRC))

# Force-reimport in case policydb was already loaded from the install path
for mod_name in list(sys.modules):
    if mod_name == "policydb" or mod_name.startswith("policydb."):
        del sys.modules[mod_name]

from policydb import outlook_contacts  # noqa: E402

HAS_OSACOMPILE = shutil.which("osacompile") is not None


def _capture_scripts(fn, *args, **kwargs) -> list[str]:
    """Invoke fn and return every AppleScript string it would have run."""
    captured: list[str] = []

    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if cmd and cmd[0] == "osascript" and "-e" in cmd:
            captured.append(cmd[cmd.index("-e") + 1])
        class _R:
            returncode = 0
            stdout = "[]"
            stderr = ""
        return _R()

    with patch.object(outlook_contacts, "is_outlook_available", return_value=True):
        with patch("subprocess.run", side_effect=fake_run):
            fn(*args, **kwargs)

    # Restore — patch context handles this, but be defensive for long test runs
    subprocess.run = real_run
    return captured


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
                f"  script length: {len(script)} chars\n"
                f"  (first 500 chars): {script[:500]}"
            )
    finally:
        src.unlink(missing_ok=True)
        if dst.exists():
            dst.unlink()


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_ensure_pdb_category_compiles():
    scripts = _capture_scripts(outlook_contacts.ensure_pdb_category, "PDB")
    assert scripts, "no AppleScript captured"
    _assert_compiles(scripts[-1], "ensure_pdb_category")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_list_pdb_contacts_compiles():
    scripts = _capture_scripts(outlook_contacts.list_pdb_contacts, "PDB")
    assert scripts, "no AppleScript captured"
    _assert_compiles(scripts[-1], "list_pdb_contacts")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_upsert_contact_create_compiles():
    payload = {
        "first_name": "Jane", "last_name": "Doe", "display_name": "Jane Doe",
        "company": "ACME Inc.", "job_title": "CFO",
        "email": "jane@acme.com",
        "business_phone": "555-111-2222", "mobile_phone": "555-333-4444",
        "notes": "line one\nline two\nline three",
        "business_address_street": "123 Main St",
    }
    scripts = _capture_scripts(outlook_contacts.upsert_contact, payload, outlook_id=None)
    assert scripts, "no AppleScript captured"
    _assert_compiles(scripts[-1], "upsert_contact(create)")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_upsert_contact_update_compiles():
    payload = {"first_name": "Jane", "last_name": "Doe", "email": "jane@acme.com"}
    scripts = _capture_scripts(outlook_contacts.upsert_contact, payload, outlook_id="42")
    assert scripts, "no AppleScript captured"
    _assert_compiles(scripts[-1], "upsert_contact(update)")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_delete_contact_compiles():
    scripts = _capture_scripts(outlook_contacts.delete_contact, "42")
    assert scripts, "no AppleScript captured"
    _assert_compiles(scripts[-1], "delete_contact")


@pytest.mark.skipif(not HAS_OSACOMPILE, reason="osacompile not available (non-mac)")
def test_upsert_contact_escapes_embedded_quotes():
    """Names/notes with double-quotes must not break the generated AppleScript."""
    payload = {
        "first_name": 'John "JD"', "last_name": "O'Brien",
        "notes": 'He said: "hello" and she replied: "world"',
        "business_address_street": "42 \"Quoted\" Ave",
    }
    scripts = _capture_scripts(outlook_contacts.upsert_contact, payload, outlook_id=None)
    assert scripts, "no AppleScript captured"
    _assert_compiles(scripts[-1], "upsert_contact(escaped quotes)")
