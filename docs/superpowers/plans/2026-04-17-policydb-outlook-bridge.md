# PolicyDB ↔ Outlook Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Outlook ⇄ PolicyDB lookup gap in both directions — a pinnable "Dock" for copying ref tags into Outlook replies (forward flow), and a "Search Outlook" button on record pages that runs a wide OR-joined search covering the record and all its relatives (reverse flow).

**Architecture:** Two features sharing one new module (`ref_tags.build_wide_search`) and one extended module (`outlook.trigger_search`). Reverse flow (Phase 1) ships first as one PR — it solves the "wrong UID type" pain structurally. Forward flow (Phase 2) ships second and reuses the existing `/search/live` FTS5 endpoint with a dock-flavored partial.

**Tech Stack:** FastAPI + Jinja2 + HTMX, SQLite via `sqlite3`, `osascript` subprocess for AppleScript, pytest with in-memory SQLite fixtures via `tmp_db` pattern in `tests/test_db.py`. All JavaScript is vanilla (no build step).

**Spec:** `docs/superpowers/specs/2026-04-17-policydb-outlook-bridge-design.md`

---

## Phase 1 — Reverse Flow (Search Outlook)

### Task 1: Add config key `outlook_search_auto_paste`

**Files:**
- Modify: `src/policydb/config.py` (extend `_DEFAULTS`)
- Modify: `src/policydb/web/routes/settings.py` (add to the Email & Contacts `EDITABLE_LISTS` entry for booleans if one exists, otherwise to whichever mechanism shows boolean settings — follow existing patterns for `outlook_contact_sync_enabled`)
- Test: none — config key addition

- [ ] **Step 1: Add default to `_DEFAULTS` in `config.py`**

Open `src/policydb/config.py`, find the `_DEFAULTS` dict (search for `outlook_contact_sync_enabled`), and add this key near the other `outlook_*` keys:

```python
"outlook_search_auto_paste": True,
```

- [ ] **Step 2: Expose the toggle in Settings UI**

Search `src/policydb/web/routes/settings.py` for how `outlook_contact_sync_enabled` is exposed in the Email & Contacts tab. Add `outlook_search_auto_paste` using the exact same mechanism (it will be a boolean toggle). If the settings page reads the key directly by name, only step 1 is required; if there is an explicit allow-list of editable keys, add `"outlook_search_auto_paste"` to it.

- [ ] **Step 3: Verify the config round-trips**

```bash
cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/typed-wishing-papert
python -c "from policydb.config import load_config; print(load_config().get('outlook_search_auto_paste'))"
```

Expected output: `True`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py
git commit -m "feat(outlook): add outlook_search_auto_paste config key"
```

---

### Task 2: Create `ref_tags.build_wide_search()` — TDD

**Files:**
- Create: `src/policydb/ref_tags.py`
- Test: `tests/test_ref_tags.py`

- [ ] **Step 1: Write the failing test for client entity type**

Create `tests/test_ref_tags.py`:

```python
"""Tests for src/policydb/ref_tags.py — wide Outlook search builder."""
from __future__ import annotations

from datetime import date

import pytest

from policydb.db import get_connection, init_db
from policydb.ref_tags import build_wide_search


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


@pytest.fixture
def seeded(tmp_db):
    """Client with CN, two policies, one issue on POL-042, one program."""
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO clients (name, cn_number, industry_segment, account_exec) "
        "VALUES ('Acme Corp', '122333627', 'Manufacturing', 'Grant')"
    )
    client_id = conn.execute(
        "SELECT id FROM clients WHERE name='Acme Corp'"
    ).fetchone()["id"]
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, premium, account_exec) "
        "VALUES ('POL-042', ?, 'GL', 'Zurich', ?, ?, 10000, 'Grant')",
        (client_id, today, today),
    )
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, premium, account_exec) "
        "VALUES ('POL-043', ?, 'AUTO', 'Chubb', ?, ?, 5000, 'Grant')",
        (client_id, today, today),
    )
    policy_id = conn.execute(
        "SELECT id FROM policies WHERE policy_uid='POL-042'"
    ).fetchone()["id"]
    # Issue linked to POL-042
    conn.execute(
        "INSERT INTO activity_log (client_id, policy_id, item_kind, issue_uid, "
        "subject, activity_type) "
        "VALUES (?, ?, 'issue', 'ISS-2026-007', 'Claim on GL', 'Issue')",
        (client_id, policy_id),
    )
    # Program on client
    conn.execute(
        "INSERT INTO programs (program_uid, client_id, name) "
        "VALUES ('PGM-3', ?, 'Acme Main Program')",
        (client_id,),
    )
    conn.commit()
    yield {"conn": conn, "client_id": client_id}
    conn.close()


def test_wide_search_client_includes_all_relatives(seeded):
    result = build_wide_search(seeded["conn"], "client", seeded["client_id"], mode="wide")
    # Issue token (verbatim), both policy forms, both program forms, CN
    assert "ISS-2026-007" in result.tokens
    assert "POL-042" in result.tokens
    assert "POL042" in result.tokens
    assert "POL-043" in result.tokens
    assert "POL043" in result.tokens
    assert "PGM-3" in result.tokens
    assert "PGM3" in result.tokens
    assert "CN122333627" in result.tokens
    # Quoted, OR-joined
    assert result.query == " OR ".join(f'"{t}"' for t in result.tokens)
    assert result.truncated is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_ref_tags.py::test_wide_search_client_includes_all_relatives -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'policydb.ref_tags'`

- [ ] **Step 3: Create the minimal module**

Create `src/policydb/ref_tags.py`:

```python
"""Build wide OR-joined Outlook search queries for a record and its relatives.

Informed by the `[PDB:...]` tag format produced by `utils.build_ref_tag()`:
    - Policy UIDs stored as POL-042, appear undashed (POL042) in compound tags.
    - Program UIDs stored as PGM-3, appear undashed (PGM3) in compound tags.
    - Issue UIDs stored as ISS-2026-001, appear verbatim.
    - Client CN appears as CN{number}.
    - Project location shows up only as L{id} — too ambiguous to search on.

So this module emits BOTH dashed and undashed forms for policies/programs
(to catch natural-text mentions and compound-tag mentions), verbatim forms
for issues/CN, and skips projects' own token in favor of their children.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Literal


EntityType = Literal["client", "policy", "issue", "project", "program"]
Mode = Literal["wide", "narrow", "client"]


@dataclass
class WideSearchResult:
    query: str
    tokens: list[str]
    total_available: int
    truncated: bool = False


def build_wide_search(
    conn: sqlite3.Connection,
    entity_type: EntityType,
    entity_id: int | str,
    mode: Mode = "wide",
    cap: int = 60,
) -> WideSearchResult:
    """Return an OR-joined quoted Outlook search covering a record + relatives.

    Tokens are ordered by specificity (most specific first), so the cap drops
    the broadest tokens last. See module docstring for the token format rules.
    """
    walker = _WALKERS.get(entity_type)
    if walker is None:
        raise ValueError(f"Unknown entity_type: {entity_type}")

    tokens = walker(conn, entity_id, mode)
    total = len(tokens)
    truncated = False
    if total > cap:
        tokens = tokens[:cap]
        truncated = True

    query = " OR ".join(f'"{t}"' for t in tokens)
    return WideSearchResult(
        query=query,
        tokens=tokens,
        total_available=total,
        truncated=truncated,
    )


def _policy_tokens(policy_uid: str) -> list[str]:
    """Policy contributes two tokens: dashed and undashed."""
    undashed = policy_uid.replace("-", "")
    if undashed == policy_uid:
        return [policy_uid]
    return [policy_uid, undashed]


def _program_tokens(program_uid: str) -> list[str]:
    """Program contributes two tokens: dashed and undashed."""
    undashed = program_uid.replace("-", "")
    if undashed == program_uid:
        return [program_uid]
    return [program_uid, undashed]


def _issue_tokens(issue_uid: str) -> list[str]:
    """Issue UID appears verbatim in compound tags — single token."""
    return [issue_uid]


def _cn_tokens(cn_number: str | None, client_id: int) -> list[str]:
    """Client CN — CN-prefixed, no dashes. Falls back to C{client_id}."""
    if cn_number and cn_number not in ("None", "none", ""):
        cleaned = cn_number.lstrip("Cc").lstrip("Nn")
        return [f"CN{cleaned}"]
    return [f"C{client_id}"]


def _walk_client(
    conn: sqlite3.Connection, client_id: int | str, mode: Mode
) -> list[str]:
    cid = int(client_id)
    row = conn.execute(
        "SELECT cn_number FROM clients WHERE id = ?", (cid,)
    ).fetchone()
    if row is None:
        raise KeyError(f"client {client_id} not found")
    cn_tokens = _cn_tokens(row["cn_number"], cid)

    if mode == "client" or mode == "narrow":
        return cn_tokens

    tokens: list[str] = []
    # Issues first (most specific)
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE client_id = ? AND item_kind = 'issue' AND issue_uid IS NOT NULL "
        "AND (merged_into_id IS NULL)",
        (cid,),
    ):
        tokens.extend(_issue_tokens(r["issue_uid"]))
    # Policies
    for r in conn.execute(
        "SELECT policy_uid FROM policies WHERE client_id = ? "
        "AND policy_uid IS NOT NULL",
        (cid,),
    ):
        tokens.extend(_policy_tokens(r["policy_uid"]))
    # Programs
    for r in conn.execute(
        "SELECT program_uid FROM programs WHERE client_id = ? "
        "AND program_uid IS NOT NULL",
        (cid,),
    ):
        tokens.extend(_program_tokens(r["program_uid"]))
    tokens.extend(cn_tokens)
    return tokens


def _walk_policy(
    conn: sqlite3.Connection, policy_uid: int | str, mode: Mode
) -> list[str]:
    uid = str(policy_uid)
    row = conn.execute(
        "SELECT id, client_id, policy_uid FROM policies WHERE policy_uid = ?",
        (uid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"policy {policy_uid} not found")
    policy_tokens = _policy_tokens(row["policy_uid"])

    if mode == "narrow":
        return policy_tokens

    client_row = conn.execute(
        "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
    ).fetchone()
    cn_tokens = _cn_tokens(
        client_row["cn_number"] if client_row else None, row["client_id"]
    )

    if mode == "client":
        return cn_tokens

    tokens: list[str] = list(policy_tokens)
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE policy_id = ? AND item_kind = 'issue' AND issue_uid IS NOT NULL "
        "AND (merged_into_id IS NULL)",
        (row["id"],),
    ):
        tokens.extend(_issue_tokens(r["issue_uid"]))
    tokens.extend(cn_tokens)
    return tokens


def _walk_issue(
    conn: sqlite3.Connection, issue_uid: int | str, mode: Mode
) -> list[str]:
    uid = str(issue_uid)
    row = conn.execute(
        "SELECT client_id, policy_id, issue_uid FROM activity_log "
        "WHERE issue_uid = ? AND item_kind = 'issue'",
        (uid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"issue {issue_uid} not found")
    issue_tokens = _issue_tokens(row["issue_uid"])

    if mode == "narrow":
        return issue_tokens

    if mode == "client":
        cli = conn.execute(
            "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
        ).fetchone()
        return _cn_tokens(cli["cn_number"] if cli else None, row["client_id"])

    tokens: list[str] = list(issue_tokens)
    if row["policy_id"]:
        pol = conn.execute(
            "SELECT policy_uid FROM policies WHERE id = ?", (row["policy_id"],)
        ).fetchone()
        if pol and pol["policy_uid"]:
            tokens.extend(_policy_tokens(pol["policy_uid"]))
    cli = conn.execute(
        "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
    ).fetchone()
    tokens.extend(
        _cn_tokens(cli["cn_number"] if cli else None, row["client_id"])
    )
    return tokens


def _walk_project(
    conn: sqlite3.Connection, project_id: int | str, mode: Mode
) -> list[str]:
    pid = int(project_id)
    row = conn.execute(
        "SELECT client_id FROM projects WHERE id = ?", (pid,)
    ).fetchone()
    if row is None:
        raise KeyError(f"project {project_id} not found")
    cli = conn.execute(
        "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
    ).fetchone()
    cn_tokens = _cn_tokens(
        cli["cn_number"] if cli else None, row["client_id"]
    )

    if mode == "narrow" or mode == "client":
        # Projects have no own searchable token → fall back to client CN.
        return cn_tokens

    tokens: list[str] = []
    for r in conn.execute(
        "SELECT issue_uid FROM activity_log "
        "WHERE project_id = ? AND item_kind = 'issue' AND issue_uid IS NOT NULL "
        "AND (merged_into_id IS NULL)",
        (pid,),
    ):
        tokens.extend(_issue_tokens(r["issue_uid"]))
    for r in conn.execute(
        "SELECT policy_uid FROM policies WHERE project_id = ? "
        "AND policy_uid IS NOT NULL",
        (pid,),
    ):
        tokens.extend(_policy_tokens(r["policy_uid"]))
    tokens.extend(cn_tokens)
    return tokens


def _walk_program(
    conn: sqlite3.Connection, program_uid: int | str, mode: Mode
) -> list[str]:
    uid = str(program_uid)
    row = conn.execute(
        "SELECT id, client_id, program_uid FROM programs WHERE program_uid = ?",
        (uid,),
    ).fetchone()
    if row is None:
        raise KeyError(f"program {program_uid} not found")
    program_tokens = _program_tokens(row["program_uid"])

    if mode == "narrow":
        return program_tokens

    cli = conn.execute(
        "SELECT cn_number FROM clients WHERE id = ?", (row["client_id"],)
    ).fetchone()
    cn_tokens = _cn_tokens(
        cli["cn_number"] if cli else None, row["client_id"]
    )

    if mode == "client":
        return cn_tokens

    tokens: list[str] = list(program_tokens)
    # Member policies via program_policies junction
    member_policy_ids = [
        r["policy_id"]
        for r in conn.execute(
            "SELECT policy_id FROM program_policies WHERE program_id = ?",
            (row["id"],),
        )
    ]
    if member_policy_ids:
        placeholders = ",".join("?" * len(member_policy_ids))
        # Issues on member policies
        for r in conn.execute(
            f"SELECT issue_uid FROM activity_log "
            f"WHERE policy_id IN ({placeholders}) AND item_kind = 'issue' "
            f"AND issue_uid IS NOT NULL AND (merged_into_id IS NULL)",
            member_policy_ids,
        ):
            tokens.extend(_issue_tokens(r["issue_uid"]))
        # Member policies themselves
        for r in conn.execute(
            f"SELECT policy_uid FROM policies WHERE id IN ({placeholders}) "
            f"AND policy_uid IS NOT NULL",
            member_policy_ids,
        ):
            tokens.extend(_policy_tokens(r["policy_uid"]))
    tokens.extend(cn_tokens)
    return tokens


_WALKERS: dict[str, callable] = {
    "client": _walk_client,
    "policy": _walk_policy,
    "issue": _walk_issue,
    "project": _walk_project,
    "program": _walk_program,
}
```

Note: before writing final code, verify table/column names match the current schema by running:

```bash
python -c "from policydb.db import get_connection, init_db; import tempfile, pathlib; p = pathlib.Path(tempfile.mkdtemp())/'t.db'; init_db(path=p); c=get_connection(p); print([r[0] for r in c.execute(\"SELECT name FROM pragma_table_info('programs')\")])"
```

If `program_policies` doesn't exist or has different columns, adjust `_walk_program` to use the actual junction (search `grep -r "program_policies\|program_id" src/policydb/migrations/`).

- [ ] **Step 4: Run the test to verify it passes**

```bash
pytest tests/test_ref_tags.py::test_wide_search_client_includes_all_relatives -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/ref_tags.py tests/test_ref_tags.py
git commit -m "feat(ref_tags): add build_wide_search() for Outlook search queries"
```

---

### Task 3: Add tests for policy/issue/project/program + edge cases

**Files:**
- Modify: `tests/test_ref_tags.py`

- [ ] **Step 1: Add tests covering each entity type, modes, truncation, and missing entities**

Append to `tests/test_ref_tags.py`:

```python
def test_wide_search_policy(seeded):
    result = build_wide_search(seeded["conn"], "policy", "POL-042", mode="wide")
    # Self + linked issue + client CN
    assert "POL-042" in result.tokens
    assert "POL042" in result.tokens
    assert "ISS-2026-007" in result.tokens
    assert "CN122333627" in result.tokens
    # Other policy POL-043 NOT included when searching from POL-042
    assert "POL-043" not in result.tokens


def test_wide_search_issue(seeded):
    result = build_wide_search(
        seeded["conn"], "issue", "ISS-2026-007", mode="wide"
    )
    assert result.tokens[0] == "ISS-2026-007"
    assert "POL-042" in result.tokens
    assert "POL042" in result.tokens
    assert "CN122333627" in result.tokens


def test_wide_search_program(seeded):
    result = build_wide_search(seeded["conn"], "program", "PGM-3", mode="wide")
    assert "PGM-3" in result.tokens
    assert "PGM3" in result.tokens
    assert "CN122333627" in result.tokens


def test_narrow_mode_issue(seeded):
    result = build_wide_search(
        seeded["conn"], "issue", "ISS-2026-007", mode="narrow"
    )
    assert result.tokens == ["ISS-2026-007"]
    assert result.query == '"ISS-2026-007"'


def test_narrow_mode_policy(seeded):
    result = build_wide_search(seeded["conn"], "policy", "POL-042", mode="narrow")
    assert result.tokens == ["POL-042", "POL042"]


def test_client_mode_collapses_to_cn(seeded):
    result = build_wide_search(
        seeded["conn"], "policy", "POL-042", mode="client"
    )
    assert result.tokens == ["CN122333627"]


def test_truncation(seeded):
    result = build_wide_search(
        seeded["conn"], "client", seeded["client_id"], mode="wide", cap=2
    )
    assert result.truncated is True
    assert result.total_available > 2
    assert len(result.tokens) == 2
    # Most specific first — issue tokens should survive
    assert result.tokens[0] == "ISS-2026-007"


def test_unknown_entity_type_raises(seeded):
    with pytest.raises(ValueError, match="Unknown entity_type"):
        build_wide_search(seeded["conn"], "foobar", 1)  # type: ignore[arg-type]


def test_missing_entity_raises_keyerror(seeded):
    with pytest.raises(KeyError):
        build_wide_search(seeded["conn"], "policy", "POL-9999")


def test_client_with_missing_cn_falls_back_to_cnumeric(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) "
        "VALUES ('NoCN', 'Technology', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='NoCN'").fetchone()["id"]
    conn.commit()
    result = build_wide_search(conn, "client", cid, mode="wide")
    assert result.tokens == [f"C{cid}"]
    conn.close()
```

- [ ] **Step 2: Run all ref_tags tests**

```bash
pytest tests/test_ref_tags.py -v
```

Expected: ALL PASS.

If any fail due to missing columns (e.g., `projects.project_id` isn't the FK name in your schema), search for the actual column name with `grep -rn "ALTER TABLE policies ADD COLUMN.*project" src/policydb/migrations/` and adjust the query in the walker.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ref_tags.py
git commit -m "test(ref_tags): cover all entity types, modes, truncation, errors"
```

---

### Task 4: Add `outlook.trigger_search()` — TDD with subprocess mocking

**Files:**
- Modify: `src/policydb/outlook.py` (add function)
- Test: `tests/test_outlook_trigger_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_outlook_trigger_search.py`:

```python
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
    assert "keystroke \"v\"" not in captured_scripts[0]
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_outlook_trigger_search.py -v
```

Expected: FAIL — `AttributeError: module 'policydb.outlook' has no attribute 'trigger_search'`

- [ ] **Step 3: Implement `trigger_search` in `outlook.py`**

Append to `src/policydb/outlook.py`:

```python
def trigger_search(query: str, auto_paste: bool = True) -> dict:
    """Put ``query`` on the clipboard, activate Outlook, optionally UI-script
    the paste + return sequence in the search field.

    Returns a dict with keys:
        status:  "searched" | "clipboard_only" | "unavailable"
        query:   the input query (for UI use)
        message: human-readable, used for toast copy
    """
    escaped = _escape_for_applescript(query)

    if auto_paste:
        script = (
            'set the clipboard to "' + escaped + '"\n'
            'try\n'
            '    tell application "Microsoft Outlook" to activate\n'
            'on error\n'
            '    return "unavailable"\n'
            'end try\n'
            'try\n'
            '    tell application "System Events"\n'
            '        tell process "Microsoft Outlook"\n'
            '            keystroke "f" using {command down, option down}\n'
            '            delay 0.15\n'
            '            keystroke "v" using {command down}\n'
            '            delay 0.05\n'
            '            keystroke return\n'
            '        end tell\n'
            '    end tell\n'
            '    return "searched"\n'
            'on error\n'
            '    return "clipboard_only"\n'
            'end try\n'
        )
    else:
        # Clipboard-only: skip System Events entirely.
        script = (
            'set the clipboard to "' + escaped + '"\n'
            'try\n'
            '    tell application "Microsoft Outlook" to activate\n'
            '    return "clipboard_only"\n'
            'on error\n'
            '    return "unavailable"\n'
            'end try\n'
        )

    result = _run_applescript(script, timeout=_resolve_timeout("availability"))
    status = (result.get("raw") or "").strip() or "unavailable"
    if not result.get("ok", False):
        status = "unavailable"
    if status not in ("searched", "clipboard_only", "unavailable"):
        status = "unavailable"

    messages = {
        "searched": "Searched Outlook.",
        "clipboard_only": "Copied — ⌘V into Outlook search, then Return.",
        "unavailable": "Outlook isn't running. Query copied — paste into search.",
    }
    return {"status": status, "query": query, "message": messages[status]}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_outlook_trigger_search.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/outlook.py tests/test_outlook_trigger_search.py
git commit -m "feat(outlook): add trigger_search() with UI scripting + fallback"
```

---

### Task 5: Add `POST /outlook/search` route — TDD

**Files:**
- Modify: `src/policydb/web/routes/outlook_routes.py`
- Test: `tests/test_outlook_search_route.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_outlook_search_route.py`:

```python
"""Tests for POST /outlook/search route."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from policydb.db import get_connection, init_db
from policydb.web.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (name, cn_number, industry_segment, account_exec) "
        "VALUES ('Acme', '122333627', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='Acme'").fetchone()["id"]
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, premium, account_exec) "
        "VALUES ('POL-042', ?, 'GL', 'Zurich', ?, ?, 10000, 'Grant')",
        (cid, today, today),
    )
    conn.commit()
    conn.close()

    return TestClient(app)


def test_search_policy_returns_query_and_tokens(client):
    with patch(
        "policydb.outlook.trigger_search",
        return_value={
            "status": "searched",
            "query": "",
            "message": "Searched Outlook.",
        },
    ):
        r = client.post(
            "/outlook/search",
            json={"entity_type": "policy", "entity_id": "POL-042", "mode": "wide"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "searched"
    assert "POL-042" in body["tokens"]
    assert "POL042" in body["tokens"]
    assert "CN122333627" in body["tokens"]
    assert body["truncated"] is False
    assert body["query"].startswith('"')


def test_search_missing_entity_returns_404(client):
    r = client.post(
        "/outlook/search",
        json={"entity_type": "policy", "entity_id": "POL-9999"},
    )
    assert r.status_code == 404


def test_search_invalid_entity_type_returns_422(client):
    r = client.post(
        "/outlook/search",
        json={"entity_type": "foobar", "entity_id": "x"},
    )
    assert r.status_code == 422  # Pydantic Literal rejects


def test_search_respects_auto_paste_config(client, monkeypatch):
    """When outlook_search_auto_paste=False, trigger_search gets auto_paste=False."""
    captured: list[bool] = []

    def fake_trigger(query, auto_paste=True):
        captured.append(auto_paste)
        return {"status": "clipboard_only", "query": query, "message": "..."}

    monkeypatch.setattr("policydb.outlook.trigger_search", fake_trigger)

    # Patch config to return False
    from policydb import config as cfg_mod
    orig_load = cfg_mod.load_config

    class _FakeCfg:
        def get(self, key, default=None):
            if key == "outlook_search_auto_paste":
                return False
            return default

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _FakeCfg())

    client.post(
        "/outlook/search",
        json={"entity_type": "policy", "entity_id": "POL-042"},
    )

    assert captured == [False]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_outlook_search_route.py -v
```

Expected: FAIL with 404/405 because the route doesn't exist yet.

- [ ] **Step 3: Add the route**

Find `src/policydb/web/routes/outlook_routes.py`, read the existing imports and response-model patterns (look at `POST /outlook/compose`). Add to the top imports:

```python
from typing import Literal

from policydb.ref_tags import build_wide_search
```

Add a request model near any existing Pydantic models:

```python
class OutlookSearchRequest(BaseModel):
    entity_type: Literal["client", "policy", "issue", "project", "program"]
    entity_id: str  # numeric id for client/project (stringified), UIDs otherwise
    mode: Literal["wide", "narrow", "client"] = "wide"
```

Add the route (place it next to `POST /outlook/compose`):

```python
@router.post("/outlook/search")
def outlook_search(
    req: OutlookSearchRequest,
    conn=Depends(get_db),
):
    """Generate a wide Outlook search query and attempt to run it."""
    from policydb.config import load_config
    from policydb import outlook as outlook_mod

    auto_paste = bool(load_config().get("outlook_search_auto_paste", True))

    try:
        result = build_wide_search(
            conn, req.entity_type, req.entity_id, mode=req.mode
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    trigger = outlook_mod.trigger_search(result.query, auto_paste=auto_paste)

    return {
        "status": trigger["status"],
        "query": result.query,
        "tokens": result.tokens,
        "total_available": result.total_available,
        "truncated": result.truncated,
        "message": trigger["message"],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
pytest tests/test_outlook_search_route.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/outlook_routes.py tests/test_outlook_search_route.py
git commit -m "feat(outlook): add POST /outlook/search route"
```

---

### Task 6: Create reusable `_search_outlook_btn.html` partial + JS handler

**Files:**
- Create: `src/policydb/web/templates/_search_outlook_btn.html`
- Modify: `src/policydb/web/templates/base.html` (add JS handler to global script block)

- [ ] **Step 1: Create the partial**

Create `src/policydb/web/templates/_search_outlook_btn.html`:

```html
{# Reusable "Search Outlook" button.
   Caller supplies:
     entity_type: "client" | "policy" | "issue" | "project" | "program"
     entity_id:   string — the UID or numeric id
#}
<button type="button"
        class="btn-secondary text-xs no-print flex items-center gap-1"
        data-entity-type="{{ entity_type }}"
        data-entity-id="{{ entity_id }}"
        onclick="searchOutlookForRecord(this)"
        title="Find all correspondence about this {{ entity_type }} and its relatives (record + client + related policies/issues)">
  <span>🔍</span>
  <span>Search Outlook</span>
</button>
```

- [ ] **Step 2: Add the handler in `base.html`**

Find the existing global `<script>` block in `src/policydb/web/templates/base.html` (search for `copyRefTag` — the handler sits near it). Add:

```html
<script>
  // Search Outlook for a record + relatives. Called from _search_outlook_btn.html.
  async function searchOutlookForRecord(btn, mode = "wide") {
    const entityType = btn.dataset.entityType;
    const entityId = btn.dataset.entityId;
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="animate-pulse">Searching…</span>';
    try {
      const resp = await fetch("/outlook/search", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({entity_type: entityType, entity_id: entityId, mode: mode}),
      });
      if (!resp.ok) {
        showToast("Search failed — " + resp.status, "error");
        return;
      }
      const body = await resp.json();
      const count = body.tokens.length;
      let msg = body.message;
      if (body.status === "searched") {
        msg = `Searched Outlook for ${count} related tag${count === 1 ? "" : "s"}.`;
      }
      if (body.truncated) {
        const shown = body.tokens.length;
        const total = body.total_available;
        msg += ` Showing ${shown} of ${total} — `;
        const tone = body.status === "searched" ? "ok" : "warn";
        showToast(msg, tone, {
          actionLabel: "narrow to this UID",
          onAction: () => searchOutlookForRecord(btn, "narrow"),
        });
      } else {
        const tone = body.status === "searched" ? "ok"
                   : body.status === "clipboard_only" ? "warn" : "error";
        showToast(msg, tone);
      }
    } catch (e) {
      showToast("Search failed — " + e.message, "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = originalHTML;
    }
  }
</script>
```

- [ ] **Step 3: Ensure `showToast()` supports the tone + action signature**

Search `base.html` for `function showToast`. The PolicyDB codebase already has toast helpers — reuse whatever exists. If the existing signature is `showToast(message, tone)`, extend it in-place to accept an optional `{actionLabel, onAction}` options arg that appends a clickable link to the toast. If no `showToast` exists yet, fall back to `window.alert()` in this plan's code (acceptable for v1 — a visible failure is better than a silent one) and open a separate cleanup task later.

- [ ] **Step 4: Quick smoke test in the browser**

Start the server:

```bash
cd /Users/grantgreeson/Documents/Projects/policydb/.claude/worktrees/typed-wishing-papert
~/.policydb/venv/bin/policydb serve --port 8006 &
```

Visit `http://127.0.0.1:8006/docs` in the browser, expand `POST /outlook/search`, click "Try it out", paste:

```json
{"entity_type":"policy","entity_id":"POL-001","mode":"wide"}
```

Expected: 200 response with `tokens`, `query`, `status` populated (status may be `unavailable` if Outlook isn't running — that's fine). Then kill the server (`jobs -l; kill %1` or `pkill -f "policydb serve"`).

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/_search_outlook_btn.html src/policydb/web/templates/base.html
git commit -m "feat(outlook): add Search Outlook button partial + JS handler"
```

---

### Task 7: Wire button into Issue, Policy, Project, Program, Client pages

**Files:**
- Modify: `src/policydb/web/templates/issues/detail.html`
- Modify: `src/policydb/web/templates/policies/edit.html`
- Modify: `src/policydb/web/templates/policies/_tab_pulse.html`
- Modify: `src/policydb/web/templates/projects/detail.html`
- Modify: `src/policydb/web/templates/programs/detail.html`
- Modify: `src/policydb/web/templates/clients/detail.html`

- [ ] **Step 1: Identify the header actions area in each template**

For each of the six templates above, open the file and locate the header/top actions bar. Look for where existing action buttons live — typically a `<div>` containing a Compose button, a ref-tag pill, or similar. The button should go next to the Compose button if present, otherwise in the same actions row.

- [ ] **Step 2: Drop in the partial on the Issue page**

Open `src/policydb/web/templates/issues/detail.html`. Identify the issue's UID (it's passed to the template — look for `issue.issue_uid` or similar). Add where actions live:

```html
{% include "_search_outlook_btn.html" with entity_type="issue", entity_id=issue.issue_uid %}
```

If Jinja2's `with` keyword isn't used in this codebase (check other includes), use `{% set %}` instead:

```html
{% set entity_type = "issue" %}{% set entity_id = issue.issue_uid %}
{% include "_search_outlook_btn.html" %}
```

- [ ] **Step 3: Wire the other five templates**

Same pattern for:

| Template | entity_type | entity_id (template variable) |
|---|---|---|
| `policies/edit.html` | `"policy"` | `policy.policy_uid` (confirm the variable name via the template) |
| `policies/_tab_pulse.html` | `"policy"` | `policy.policy_uid` |
| `projects/detail.html` | `"project"` | `project.id|string` (project is an int id) |
| `programs/detail.html` | `"program"` | `program.program_uid` |
| `clients/detail.html` | `"client"` | `client.id|string` (numeric id) |

For each file, locate the header actions region by searching for existing buttons (e.g., a Compose button will have `compose` in nearby onclick/hx-post attributes). Insert the include after the Compose button.

- [ ] **Step 4: Visual smoke test**

Start the server, hit each record page in the browser, verify the 🔍 Search Outlook button renders next to Compose. Hover — tooltip shows. Click — if Outlook isn't running you'll see an `unavailable` toast. If Outlook is running, test the `clipboard_only` fallback by denying Accessibility: the toast should say "Copied — ⌘V into Outlook search."

```bash
~/.policydb/venv/bin/policydb serve --port 8006 &
open http://127.0.0.1:8006/issues/<some-issue-uid>
open http://127.0.0.1:8006/policies/<some-policy-uid>/edit
# ...etc
pkill -f "policydb serve"
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/issues/detail.html \
        src/policydb/web/templates/policies/edit.html \
        src/policydb/web/templates/policies/_tab_pulse.html \
        src/policydb/web/templates/projects/detail.html \
        src/policydb/web/templates/programs/detail.html \
        src/policydb/web/templates/clients/detail.html
git commit -m "feat(outlook): wire Search Outlook button into record pages"
```

---

### Task 8 (optional): Document Accessibility permission

**Files:**
- Create: `docs/outlook-setup.md` (or append to an existing Outlook setup doc if one exists — search first)

- [ ] **Step 1: Find existing Outlook docs**

```bash
ls docs/ 2>/dev/null | grep -i outlook
```

If a file exists, append to it. Otherwise create new.

- [ ] **Step 2: Write the permission note**

```markdown
## Search Outlook — Accessibility permission

The "Search Outlook" button (on issue / policy / project / program / client pages) attempts to focus Outlook's search field and paste the generated query via macOS System Events. This requires **Accessibility** permission for whatever process is running `osascript` — usually your Terminal, iTerm, or VS Code.

### To grant it (one time)

1. Open **System Settings → Privacy & Security → Accessibility**.
2. Click the **+** button and add your terminal app (e.g., Terminal.app, iTerm.app).
3. Toggle it on.
4. Restart the `policydb serve` process.

### If you haven't granted it

The button still works — it falls back to clipboard-only mode. You'll see a toast saying "Copied — ⌘V into Outlook search, then Return." Paste manually into Outlook's search bar; same result.

### Forcing clipboard-only mode

If the UI scripting gets flaky after an Outlook update, flip `outlook_search_auto_paste` to `false` in Settings → Email & Contacts. That makes every search clipboard-only.
```

- [ ] **Step 3: Commit**

```bash
git add docs/outlook-setup.md
git commit -m "docs(outlook): document Accessibility permission for Search Outlook"
```

---

### Task 9: Full-pass regression — run the whole test suite

- [ ] **Step 1: Run every test in the repo**

```bash
pytest -q
```

Expected: all pre-existing tests pass + the new test files pass. No failures introduced.

- [ ] **Step 2: If any pre-existing tests fail, investigate**

Our changes only touched `config.py` (added a key), `outlook.py` (appended a function), new route, new template — none should break existing code. If something fails, check that the config key addition didn't conflict with a test that snapshots `_DEFAULTS`. Fix any issue in the smallest way possible.

- [ ] **Step 3: Manual QA with Outlook running**

1. Open Legacy Outlook for Mac.
2. Start `policydb serve`.
3. Visit an issue detail page, click "🔍 Search Outlook".
4. If Accessibility is granted: Outlook should come forward with the search field populated and results shown.
5. If not: toast says "Copied — ⌘V into Outlook search". Paste manually into Outlook's search bar — same results.
6. Click a client with many records; verify truncation toast shows "Showing 60 of N — narrow to this UID" link.
7. Click the narrow link; verify it re-fires with just the client's CN.

---

## Phase 2 — Forward Flow (The Dock)

### Task 10: Extend `/search/live` with `mode=dock` partial

**Files:**
- Modify: `src/policydb/web/routes/dashboard.py` (search_live function, line ~298)
- Create: `src/policydb/web/templates/_search_dropdown_dock.html`

- [ ] **Step 1: Add `mode` parameter to `search_live`**

In `src/policydb/web/routes/dashboard.py`, change the signature:

```python
@router.get("/search/live", response_class=HTMLResponse)
def search_live(
    request: Request,
    q: str = "",
    mode: str = "",  # "" for navbar dropdown, "dock" for /dock view
    conn=Depends(get_db),
):
```

And at the return:

```python
template = "_search_dropdown_dock.html" if mode == "dock" else "_search_dropdown.html"
return templates.TemplateResponse(template, {
    "request": request,
    "items": items,
    "q": q,
    "total": sum(len(v) for v in results.values()),
})
```

- [ ] **Step 2: Create the dock-flavored partial**

Create `src/policydb/web/templates/_search_dropdown_dock.html`. Model it on `_search_dropdown.html` (the existing navbar dropdown). Read `_search_dropdown.html` first — the per-item fields (type, data) are the same, but the dock version renders rows taller, with a 🔍 button and an ↗ icon:

```html
{# Dock search results. Each row exposes:
     - copy-the-ref-tag (primary click on row body)
     - 🔍 Search Outlook for this record (secondary)
     - ↗ Open the record in a new tab (tertiary)
   Requires that each result dict has {type, data.ref_tag, data.display, data.url, data.entity_id}.
   If your full_text_search() result schema differs, adjust the field names here.
#}
{% if items %}
<ul class="dock-results" role="listbox">
  {% for item in items %}
    {% set t = item.type %}
    {% set d = item.data %}
    <li class="dock-row" role="option"
        data-ref="{{ d.ref_tag }}"
        data-display="{{ d.display }}"
        data-type="{{ t }}"
        data-url="{{ d.url }}">
      <div class="dock-row-main" onclick="dockCopy(this.parentElement)">
        <span class="dock-type-badge">
          {% if t == 'clients' %}🏢{% elif t == 'policies' %}📄
          {% elif t == 'issues' %}⚠️{% elif t == 'programs' %}🗂️
          {% else %}•{% endif %}
        </span>
        <span class="dock-name">{{ d.display }}</span>
        <span class="dock-tag">[PDB:{{ d.ref_tag }}]</span>
      </div>
      <button class="dock-action" title="Search Outlook"
              data-entity-type="{{ t|replace('s','') if t.endswith('s') else t }}"
              data-entity-id="{{ d.entity_id }}"
              onclick="event.stopPropagation(); searchOutlookForRecord(this);">🔍</button>
      <a class="dock-open" href="{{ d.url }}" target="_blank"
         onclick="event.stopPropagation();" title="Open in new tab">↗</a>
    </li>
  {% endfor %}
</ul>
{% elif q %}
  <p class="dock-empty">No matches for "{{ q }}"</p>
{% endif %}
```

**IMPORTANT:** Before finalizing, read `src/policydb/web/templates/_search_dropdown.html` and `src/policydb/queries.py:full_text_search` to confirm the exact field names (`ref_tag`, `display`, `url`, `entity_id`). If they don't exist on the existing result objects, add them in `full_text_search()` OR compute them in the route before rendering.

- [ ] **Step 3: Smoke test the new mode**

```bash
~/.policydb/venv/bin/policydb serve --port 8006 &
curl "http://127.0.0.1:8006/search/live?q=acme&mode=dock"
pkill -f "policydb serve"
```

Expected: HTML with `class="dock-results"` and row elements. Navbar dropdown (without `mode=dock`) still returns the unchanged `_search_dropdown.html` partial.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/dashboard.py \
        src/policydb/web/templates/_search_dropdown_dock.html
git commit -m "feat(search): add mode=dock partial to /search/live"
```

---

### Task 11: Create `/dock` route + template

**Files:**
- Create: `src/policydb/web/routes/dock.py`
- Create: `src/policydb/web/templates/dock.html`
- Modify: `src/policydb/web/app.py` (register router)

- [ ] **Step 1: Create the router**

Create `src/policydb/web/routes/dock.py`:

```python
"""The Dock — narrow pinnable PolicyDB view for quickly copying ref tags
into Outlook replies. Reuses /search/live?mode=dock for the results partial."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from policydb.web.app_common import templates  # adjust import to wherever templates is exported

router = APIRouter()


@router.get("/dock", response_class=HTMLResponse)
@router.get("/d", response_class=HTMLResponse)
def dock(request: Request):
    return templates.TemplateResponse("dock.html", {"request": request})
```

If the templates global lives somewhere other than `app_common`, check one of the existing routes (e.g., `dashboard.py`) for the correct import path and use that.

- [ ] **Step 2: Create the template**

Create `src/policydb/web/templates/dock.html`. This does NOT extend `base.html` — the dock is its own chrome. Copy over the Tailwind CDN + brand fonts from `base.html` so styling still works:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PolicyDB Dock</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/htmx.org@1.9.10"></script>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  body { font-family: 'DM Sans', sans-serif; background: #F7F3EE; color: #3D3C37; }
  .dock { max-width: 400px; margin: 0 auto; padding: 0.75rem; }
  .dock-search { width: 100%; padding: 0.5rem 0.75rem; border: 1px solid #e5e7eb;
                 border-radius: 0.5rem; font-size: 0.875rem; }
  .dock-search:focus { outline: none; border-color: #0B4BFF;
                       box-shadow: 0 0 0 1px #0B4BFF; }
  .dock-results { list-style: none; padding: 0; margin: 0.75rem 0 0 0; }
  .dock-row { display: flex; align-items: center; gap: 0.5rem;
              padding: 0.5rem; border-radius: 0.375rem; cursor: pointer; }
  .dock-row:hover, .dock-row.selected { background: #ffffff;
                                        box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
  .dock-row-main { display: flex; gap: 0.5rem; flex: 1; align-items: center;
                   overflow: hidden; }
  .dock-type-badge { font-size: 0.875rem; }
  .dock-name { font-size: 0.8125rem; overflow: hidden; text-overflow: ellipsis;
               white-space: nowrap; flex: 1; }
  .dock-tag { font-family: 'JetBrains Mono', monospace; font-size: 0.6875rem;
              background: #dbeafe; color: #1e40af; padding: 0.125rem 0.375rem;
              border-radius: 0.25rem; }
  .dock-action, .dock-open { background: transparent; border: none;
                             cursor: pointer; padding: 0.25rem; }
  .dock-action:hover, .dock-open:hover { background: #f3f4f6;
                                         border-radius: 0.25rem; }
  .flash-green { animation: flash 0.6s ease-out; }
  @keyframes flash { 0% { background: #d1fae5; } 100% { background: transparent; } }
  .dock-section-label { font-size: 0.6875rem; color: #9ca3af;
                        text-transform: uppercase; font-weight: 600;
                        letter-spacing: 0.05em; margin-top: 0.75rem; }
</style>
</head>
<body>
<div class="dock">
  <input
    id="q"
    class="dock-search"
    type="text"
    placeholder="Client, policy, issue…"
    autofocus
    autocomplete="off"
    hx-get="/search/live?mode=dock"
    hx-trigger="keyup changed delay:150ms, search"
    hx-target="#results"
    hx-swap="innerHTML"
  >
  <div id="results"></div>
  <div id="recents-wrap">
    <div class="dock-section-label" id="recents-label" style="display:none;">Recent</div>
    <ul id="recents" class="dock-results"></ul>
  </div>
</div>

<script>
  // Toast helper — minimal implementation for the dock.
  // If the full base.html showToast is available via opener window, use that.
  function showToast(msg, tone) {
    const t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;bottom:1rem;left:1rem;right:1rem;padding:0.5rem 0.75rem;border-radius:0.375rem;font-size:0.8125rem;z-index:50;' +
      (tone === 'ok' ? 'background:#d1fae5;color:#065f46;' :
       tone === 'warn' ? 'background:#fef3c7;color:#78350f;' :
       tone === 'error' ? 'background:#fee2e2;color:#7f1d1d;' :
                          'background:#e5e7eb;color:#374151;');
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2400);
  }
</script>
<script src="/static/dock.js"></script>
</body>
</html>
```

- [ ] **Step 3: Register the router in `app.py`**

Open `src/policydb/web/app.py`. Near line 343 (where `ref_lookup.router` is included), add:

```python
from policydb.web.routes import dock as dock_routes  # near the other imports
...
app.include_router(dock_routes.router)
```

- [ ] **Step 4: Smoke test**

```bash
~/.policydb/venv/bin/policydb serve --port 8006 &
curl "http://127.0.0.1:8006/dock" | head -20
open http://127.0.0.1:8006/dock
pkill -f "policydb serve"
```

Expected: HTML loads, page shows the search box. Type something → HTMX fires `/search/live?mode=dock` → results pane fills. (The dock.js static file doesn't exist yet, so nothing clickable yet — that's the next task.)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/dock.py \
        src/policydb/web/templates/dock.html \
        src/policydb/web/app.py
git commit -m "feat(dock): add /dock and /d routes with HTMX-powered search"
```

---

### Task 12: Write `dock.js` — keyboard, copy, flash, recents

**Files:**
- Create: `src/policydb/web/static/dock.js`

- [ ] **Step 1: Confirm `/static/` is served**

```bash
grep -n "StaticFiles\|mount.*static" src/policydb/web/app.py
```

Expected: an existing mount for `/static`. If it exists, static files go in `src/policydb/web/static/`. If not, add one — follow the FastAPI pattern.

- [ ] **Step 2: Create the JS file**

Create `src/policydb/web/static/dock.js`:

```javascript
// The Dock — keyboard navigation, copy-to-clipboard, recents.

const RECENTS_KEY = 'dock:recents';
const MAX_RECENTS = 10;

function getRecents() {
  try { return JSON.parse(localStorage.getItem(RECENTS_KEY) || '[]'); }
  catch { return []; }
}

function setRecents(list) {
  localStorage.setItem(RECENTS_KEY, JSON.stringify(list.slice(0, MAX_RECENTS)));
}

function renderRecents() {
  const list = getRecents();
  const ul = document.getElementById('recents');
  const label = document.getElementById('recents-label');
  ul.innerHTML = '';
  if (!list.length) { label.style.display = 'none'; return; }
  label.style.display = '';
  for (const r of list) {
    const li = document.createElement('li');
    li.className = 'dock-row';
    li.dataset.ref = r.ref;
    li.dataset.display = r.display;
    li.dataset.type = r.type;
    li.dataset.url = r.url;
    li.innerHTML = `
      <div class="dock-row-main">
        <span class="dock-type-badge">${iconFor(r.type)}</span>
        <span class="dock-name">${escapeHtml(r.display)}</span>
        <span class="dock-tag">[PDB:${escapeHtml(r.ref)}]</span>
      </div>`;
    li.addEventListener('click', () => dockCopy(li));
    ul.appendChild(li);
  }
}

function iconFor(t) {
  if (t === 'clients') return '🏢';
  if (t === 'policies') return '📄';
  if (t === 'issues') return '⚠️';
  if (t === 'programs') return '🗂️';
  return '•';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

async function dockCopy(row) {
  const ref = row.dataset.ref;
  const display = row.dataset.display;
  const type = row.dataset.type;
  const url = row.dataset.url;
  const wrapped = `[PDB:${ref}]`;
  try {
    await navigator.clipboard.writeText(wrapped);
  } catch {
    // Fallback for http:// (non-secure) contexts — unlikely on localhost, but safe.
    const ta = document.createElement('textarea');
    ta.value = wrapped;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  }
  row.classList.add('flash-green');
  showToast(`${wrapped} copied`, 'ok');

  // Update recents
  const recents = getRecents().filter(r => r.ref !== ref);
  recents.unshift({ ref, display, type, url });
  setRecents(recents);
  renderRecents();

  // Clear and refocus
  const q = document.getElementById('q');
  setTimeout(() => {
    row.classList.remove('flash-green');
    q.value = '';
    document.getElementById('results').innerHTML = '';
    q.focus();
  }, 400);
}

function moveSelection(delta) {
  const rows = Array.from(document.querySelectorAll('#results .dock-row'));
  if (!rows.length) return;
  let idx = rows.findIndex(r => r.classList.contains('selected'));
  rows.forEach(r => r.classList.remove('selected'));
  idx = Math.max(0, Math.min(rows.length - 1, idx + delta));
  if (idx < 0) idx = 0;
  rows[idx].classList.add('selected');
  rows[idx].scrollIntoView({ block: 'nearest' });
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { e.preventDefault(); moveSelection(1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); moveSelection(-1); }
  else if (e.key === 'Enter') {
    const selected = document.querySelector('#results .dock-row.selected')
      || document.querySelector('#results .dock-row');
    if (selected) { e.preventDefault(); dockCopy(selected); }
  } else if (e.key === 'Escape') {
    const q = document.getElementById('q');
    q.value = '';
    document.getElementById('results').innerHTML = '';
    q.focus();
  }
});

// Refocus search when the window regains focus (coming back from Outlook).
window.addEventListener('focus', () => {
  document.getElementById('q').focus();
});

// After HTMX swaps results, preselect the first row.
document.body.addEventListener('htmx:afterSwap', (e) => {
  if (e.target.id === 'results') {
    const first = document.querySelector('#results .dock-row');
    if (first) first.classList.add('selected');
  }
});

// Wire up result row clicks (HTMX adds them dynamically).
document.body.addEventListener('click', (e) => {
  const row = e.target.closest('#results .dock-row');
  if (row && !e.target.closest('.dock-action, .dock-open')) {
    dockCopy(row);
  }
});

// searchOutlookForRecord is defined on-row in the dock partial.
// We need a local version since base.html's script isn't loaded here.
async function searchOutlookForRecord(btn, mode = 'wide') {
  const entityType = btn.dataset.entityType;
  const entityId = btn.dataset.entityId;
  btn.disabled = true;
  try {
    const resp = await fetch('/outlook/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entity_type: entityType, entity_id: entityId, mode }),
    });
    const body = await resp.json();
    showToast(body.message, body.status === 'searched' ? 'ok'
                         : body.status === 'clipboard_only' ? 'warn' : 'error');
  } catch (e) {
    showToast('Search failed — ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

// Initial render of recents.
document.addEventListener('DOMContentLoaded', renderRecents);
```

- [ ] **Step 3: Manual browser QA**

```bash
~/.policydb/venv/bin/policydb serve --port 8006 &
open http://127.0.0.1:8006/dock
pkill -f "policydb serve"
```

In the browser:
1. Type a client name → results populate.
2. Arrow Down → first row highlights; arrow up/down moves.
3. Press Enter → row flashes green, `[PDB:xxx]` copied to clipboard (paste into TextEdit to verify), toast appears, search box clears and refocuses, recents list updates.
4. Clear and try again — recents show at bottom, clicking a recent re-copies.
5. Click the 🔍 icon on a result → Outlook search fires (toast shows status).
6. Window-switch to Outlook and back → search box is refocused.
7. Esc on search box → clears.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/static/dock.js
git commit -m "feat(dock): keyboard navigation, copy-ref-tag, recents, Outlook search"
```

---

### Task 13: Full-pass regression — Phase 2

- [ ] **Step 1: Run every test**

```bash
pytest -q
```

Expected: all tests pass. No regressions.

- [ ] **Step 2: Manual cross-feature smoke test**

1. Start Outlook.
2. Start `policydb serve`.
3. Open `/dock` in a narrow browser window on one side of the screen.
4. Open Outlook beside it.
5. In Outlook: open a sent email that has a `[PDB:...]` tag.
6. Switch to the dock (window focus returns → search box autofocus).
7. Type the client name, Enter to copy the ref tag for the specific policy/issue.
8. Switch back to Outlook, reply, paste — ref tag appears in the reply.
9. On the dock, click the 🔍 on the same record → Outlook foregrounds with search populated.
10. Also test from any record page's "🔍 Search Outlook" button.

---

## Self-Review Notes

Ran through the plan against the spec:

**Spec coverage:**
- "The Dock" feature → Tasks 10–12 ✓
- "Search Outlook" button feature → Tasks 1–7 ✓
- `build_wide_search()` → Tasks 2–3 ✓
- `trigger_search()` with three-way graceful degradation → Task 4 ✓
- `POST /outlook/search` route → Task 5 ✓
- Config key `outlook_search_auto_paste` → Task 1 ✓
- Accessibility docs → Task 8 ✓
- Record-page buttons (issue, policy, project, program, client) → Task 7 ✓
- Dock integration of Search Outlook button → Task 12 ✓

**Gaps found and filled:**
- Added verification step in Task 2 about confirming `program_policies` junction exists before writing the program walker.
- Added "if showToast doesn't exist" fallback note in Task 6.
- Dock can't rely on `base.html`'s showToast — embedded a minimal toast in Task 11 + 12.

**Type consistency:**
- `build_wide_search` signature matches across Tasks 2, 3, 5 ✓
- `trigger_search` signature matches across Tasks 4, 5 ✓
- Data attributes on button partials match the JS handlers in both Tasks 6 (base.html) and 12 (dock.js) ✓
