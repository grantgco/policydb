"""Tests for policydb.outlook.trigger_search()."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from policydb import outlook


def _mock_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_trigger_search_success_returns_searched():
    with patch("policydb.outlook.subprocess.run", return_value=_mock_run(stdout="searched")):
        result = outlook.trigger_search('"POL-042" OR "ISS-007"')
    assert result["status"] == "searched"
    assert result["query"] == '"POL-042" OR "ISS-007"'
    assert "searched" in result["message"].lower()


def test_trigger_search_clipboard_only_when_ui_scripting_fails():
    with patch("policydb.outlook.subprocess.run", return_value=_mock_run(stdout="clipboard_only")):
        result = outlook.trigger_search("query")
    assert result["status"] == "clipboard_only"
    assert "⌘V" in result["message"] or "copied" in result["message"].lower()


def test_trigger_search_unavailable_when_outlook_missing():
    with patch("policydb.outlook.subprocess.run", return_value=_mock_run(stdout="unavailable")):
        result = outlook.trigger_search("query")
    assert result["status"] == "unavailable"


def test_trigger_search_subprocess_error_returns_unavailable():
    with patch(
        "policydb.outlook.subprocess.run",
        return_value=_mock_run(returncode=1, stderr="Application can't be found"),
    ):
        result = outlook.trigger_search("query")
    assert result["status"] == "unavailable"


def test_trigger_search_auto_paste_false_skips_ui_scripting():
    """When auto_paste=False, the generated script must NOT contain keystroke paste."""
    captured_scripts: list[str] = []

    def fake_run(args, **kwargs):
        # args is ["osascript", "-e", SCRIPT]
        captured_scripts.append(args[2])
        return _mock_run(stdout="clipboard_only")

    with patch("policydb.outlook.subprocess.run", side_effect=fake_run):
        outlook.trigger_search("query", auto_paste=False)

    assert len(captured_scripts) == 1
    assert 'keystroke "v"' not in captured_scripts[0]
    assert "keystroke return" not in captured_scripts[0]


def test_trigger_search_escapes_quotes_in_query():
    """Queries contain double quotes — must be escaped for AppleScript."""
    captured: list[str] = []

    def fake_run(args, **kwargs):
        captured.append(args[2])
        return _mock_run(stdout="searched")

    with patch("policydb.outlook.subprocess.run", side_effect=fake_run):
        outlook.trigger_search('"POL-042"')

    # AppleScript literal must have escaped quotes, not raw ones that break the script
    assert r'\"POL-042\"' in captured[0]
