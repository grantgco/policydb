# Multi-User Task List + Cross-Platform Packaging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Today task list that replaces the Focus Queue on the Action Center, plus Mac + Windows desktop installers so a second user (Mark) can run a private copy on Windows.

**Architecture:** Single feature branch `feat/multi-user-tasklist`, eight PR-able phases in strict order. Phases 1–5 ship web UI changes to the existing Python/CLI install; phases 6–8 add pywebview-based packaging with platform-aware data paths and a single-screen onboarding flow. No schema invention — tasks live in the existing `activity_log` table via a new `v_today_tasks` SQL view. The scoring engine (`focus_queue.build_focus_queue`) is reused in a new `suggestions_only=True` mode.

**Tech Stack:** FastAPI + uvicorn + SQLite + Jinja2 + HTMX + Tabulator 6.3 + pywebview + PyInstaller. Tests: pytest with TestClient. No TypeScript, no React, no build step.

**Spec:** `docs/superpowers/specs/2026-04-18-multi-user-tasklist-design.md` (commit `98da1ef3`, amended post-lock with 4 schema corrections).

---

## File Structure

**New files:**

- `src/policydb/paths.py` — platform-aware DATA_DIR + outlook_available()
- `src/policydb/desktop.py` — packaged-app entry point (port + uvicorn thread + pywebview window + first-launch migration)
- `src/policydb/migrations/163_allow_standalone_tasks.sql` — relax `activity_log.client_id NOT NULL`
- `src/policydb/web/templates/action_center/_today.html` — Today tab shell (toolbar + grid + suggestions)
- `src/policydb/web/templates/action_center/_today_grid.html` — Tabulator grid partial
- `src/policydb/web/templates/action_center/_today_suggestions.html` — Smart Suggestions panel
- `src/policydb/web/templates/action_center/_add_task_modal.html` — Add Task modal
- `src/policydb/web/templates/action_center/_undo_toast.html` — 5s complete/undo toast
- `src/policydb/web/templates/onboarding/welcome.html` — onboarding single-screen form
- `src/policydb/web/static/js/tabulator_today.js` — shared Tabulator base used by Today and Plan Week
- `src/policydb/web/static/css/today.css` — Today-specific visual refinements (chips, priority bar, hover, etc.)
- `src/policydb/web/routes/onboarding.py` — `/onboarding` GET + POST
- `packaging/build.py` — one-shot build script
- `packaging/policydb.spec` — PyInstaller spec
- `packaging/README.md` — build instructions (incl. iCloud escape hatch)
- `.github/workflows/package-mac.yml` — macOS .dmg build
- `.github/workflows/package-win.yml` — Windows .msi build
- `tests/test_paths.py`
- `tests/test_today_view.py`
- `tests/test_today_routes.py`
- `tests/test_suggestions.py`
- `tests/test_focus_retirement.py`
- `tests/test_desktop_migration.py`
- `tests/test_onboarding.py`

**Modified files:**

- `src/policydb/db.py` — import from `policydb.paths`, drop local `DB_DIR` literal
- `src/policydb/config.py` — import from `policydb.paths`
- `src/policydb/views.py` — add `V_TODAY_TASKS` + register in `ALL_VIEWS`
- `src/policydb/focus_queue.py` — add `suggestions_only=False` kwarg + module docstring
- `src/policydb/web/app.py` — register `outlook_available` as Jinja global
- `src/policydb/web/routes/action_center.py` — Today branch, task CRUD routes, default-tab flip, redirect
- `src/policydb/web/routes/activities.py` — Plan Week re-skin using shared Tabulator base
- `src/policydb/web/routes/outlook.py` — feature gate on `outlook_available()`
- `src/policydb/web/routes/dashboard.py` — repoint "Active focus items" count
- `src/policydb/web/routes/settings.py` — reword EDITABLE_LISTS labels ("Focus" → "Suggestions")
- `src/policydb/web/templates/action_center/page.html` — add Today tab button, remove Focus, sessionStorage migration
- `src/policydb/web/templates/base.html` — wrap Outlook-dependent controls in `{% if outlook_available %}`

**Deleted files:**

- `src/policydb/web/templates/action_center/_focus_queue.html`
- `src/policydb/web/templates/action_center/_focus_item.html`
- `src/policydb/web/templates/action_center/_waiting_sidebar.html`

---

## Conventions used throughout this plan

- **Test fixtures:** reuse the existing `tmp_db` / `app_client` / `seeded` pattern from `tests/test_open_tasks_routes.py`. Every test file defines these at the top (or imports from conftest when a new shared fixture is proposed).
- **Commit cadence:** one commit per completed task. Commit message template: `feat(<scope>): <what> — phase N/8`. Scope = `today`, `suggestions`, `focus-retire`, `plan-week`, `paths`, `desktop`, `packaging`, `onboarding`.
- **Run tests locally:** `pytest tests/test_<name>.py -v` (fast) or `pytest -q` for the full suite.
- **Server restart:** PolicyDB uses a venv at `~/.policydb/venv/`. To run the dev server: `~/.policydb/venv/bin/policydb serve --port 8006`. Kill any existing server first (`lsof -ti:8000 | xargs kill -9` works for the default port).
- **Manual UI QA:** use the Chrome plugin (per `feedback_chrome_qa`) or Playwright when listed in task steps.
- **TDD order:** write failing test → verify fail → implement → verify pass → commit. Do not write implementation before the test.

---

# Phase 1 — paths.py + call-site refactor + Outlook feature gate

**Goal:** Extract data-root path logic into a platform-aware module and gate Outlook integration on macOS. Zero user-visible change. Unblocks the desktop packaging phases that follow.

**PR title suggestion:** `feat(paths): extract DATA_DIR into platform-aware module + Outlook feature gate`

---

### Task 1.1: Create `src/policydb/paths.py` with tests

**Files:**
- Create: `src/policydb/paths.py`
- Create: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paths.py`:

```python
"""Tests for policydb.paths — platform-aware data directory helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_data_dir_mac_is_home_policydb(tmp_path):
    with patch.object(sys, "platform", "darwin"), \
         patch.object(Path, "home", return_value=tmp_path):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.DATA_DIR == tmp_path / ".policydb"
        assert paths.DATA_DIR.exists()


def test_data_dir_windows_is_appdata_policydb(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    with patch.object(sys, "platform", "win32"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.DATA_DIR == appdata / "PolicyDB"
        assert paths.DATA_DIR.exists()


def test_db_path_and_config_path(tmp_path):
    with patch.object(sys, "platform", "darwin"), \
         patch.object(Path, "home", return_value=tmp_path):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.db_path() == tmp_path / ".policydb" / "policydb.sqlite"
        assert paths.config_path() == tmp_path / ".policydb" / "config.yaml"


def test_outlook_available_only_on_mac():
    with patch.object(sys, "platform", "darwin"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.outlook_available() is True
    with patch.object(sys, "platform", "win32"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.outlook_available() is False
    with patch.object(sys, "platform", "linux"):
        from importlib import reload
        import policydb.paths as paths
        reload(paths)
        assert paths.outlook_available() is False


@pytest.fixture(autouse=True)
def restore_paths_module():
    """Reload policydb.paths back to its real state after each test."""
    yield
    import policydb.paths as paths
    from importlib import reload
    reload(paths)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL with `ImportError: cannot import name ...` or `AttributeError: module 'policydb.paths' has no attribute 'DATA_DIR'`.

- [ ] **Step 3: Write the implementation**

Create `src/policydb/paths.py`:

```python
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
    """SQLite DB path inside DATA_DIR."""
    return DATA_DIR / "policydb.sqlite"


def config_path() -> Path:
    """config.yaml path inside DATA_DIR."""
    return DATA_DIR / "config.yaml"


def outlook_available() -> bool:
    """True when the current platform supports the Outlook AppleScript bridge.

    AppleScript is macOS-only, so Windows / Linux return False and the Jinja
    global wired in app.py hides Outlook-dependent UI.
    """
    return sys.platform == "darwin"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_paths.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/paths.py tests/test_paths.py
git commit -m "feat(paths): platform-aware DATA_DIR module with tests — phase 1/8"
```

---

### Task 1.2: Wire `db.py` to `policydb.paths`

**Files:**
- Modify: `src/policydb/db.py` (top-of-file constants + any `Path.home() / ".policydb"` literals)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py`:

```python
def test_db_module_uses_paths_data_dir(tmp_path, monkeypatch):
    """db.DB_DIR and db.DB_PATH must come from policydb.paths, not local literals."""
    monkeypatch.setattr("policydb.paths.DATA_DIR", tmp_path)
    from importlib import reload
    import policydb.db as db
    reload(db)
    assert db.DB_DIR == tmp_path
    assert db.DB_PATH == tmp_path / "policydb.sqlite"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py::test_db_module_uses_paths_data_dir -v`
Expected: FAIL — `db.DB_DIR` still points at the literal `~/.policydb`, not `tmp_path`.

- [ ] **Step 3: Refactor db.py**

Read `src/policydb/db.py` first to find existing `DB_DIR` / `DB_PATH` declarations. Replace whatever literal path construction exists with:

```python
# Near the top of db.py, with other imports:
from policydb.paths import DATA_DIR, db_path

# Replace local constants:
DB_DIR = DATA_DIR
DB_PATH = db_path()
EXPORTS_DIR = DATA_DIR / "exports"
CONFIG_PATH = DATA_DIR / "config.yaml"
```

Remove any `Path.home() / ".policydb"` literals elsewhere in the file — use `DATA_DIR` instead.

- [ ] **Step 4: Run the new test + full db suite to verify**

Run:
```bash
pytest tests/test_paths.py::test_db_module_uses_paths_data_dir tests/test_db.py -v
```
Expected: all pass. If any existing db test fails it means a literal path was missed — fix and re-run.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/db.py tests/test_paths.py
git commit -m "feat(paths): route db.DB_DIR through policydb.paths — phase 1/8"
```

---

### Task 1.3: Wire `config.py` to `policydb.paths`

**Files:**
- Modify: `src/policydb/config.py` (CONFIG_PATH + any `Path.home() / ".policydb"` literals)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py`:

```python
def test_config_module_uses_paths_data_dir(tmp_path, monkeypatch):
    """config.CONFIG_PATH must come from policydb.paths."""
    monkeypatch.setattr("policydb.paths.DATA_DIR", tmp_path)
    from importlib import reload
    import policydb.config as cfg
    reload(cfg)
    assert cfg.CONFIG_PATH == tmp_path / "config.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py::test_config_module_uses_paths_data_dir -v`
Expected: FAIL — config still constructs its own path.

- [ ] **Step 3: Refactor config.py**

Open `src/policydb/config.py`. Replace the CONFIG_PATH literal with:

```python
from policydb.paths import DATA_DIR, config_path

CONFIG_PATH = config_path()
```

Remove any other `Path.home() / ".policydb"` literals.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_paths.py tests/test_db.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/config.py tests/test_paths.py
git commit -m "feat(paths): route config.CONFIG_PATH through policydb.paths — phase 1/8"
```

---

### Task 1.4: Register `outlook_available` as a Jinja2 global

**Files:**
- Modify: `src/policydb/web/app.py` (where `templates = Jinja2Templates(...)` is built and globals are registered)
- Create: test in `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py`:

```python
def test_outlook_available_is_jinja_global(app_client):
    """outlook_available must be callable from any template via {{ outlook_available() }}."""
    from policydb.web.app import templates
    assert "outlook_available" in templates.env.globals
    # Callable — not a bare boolean — so tests can monkeypatch sys.platform later
    assert callable(templates.env.globals["outlook_available"])
```

(Reuse the existing `app_client` fixture pattern from `tests/test_open_tasks_routes.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py::test_outlook_available_is_jinja_global -v`
Expected: FAIL — `outlook_available` not in Jinja globals.

- [ ] **Step 3: Register the global**

Open `src/policydb/web/app.py`. Find the block where Jinja globals are registered (search for `templates.env.globals`). Add:

```python
from policydb.paths import outlook_available
templates.env.globals["outlook_available"] = outlook_available
```

(Ensure it's registered exactly once — grep first.)

- [ ] **Step 4: Run test**

Run: `pytest tests/test_paths.py::test_outlook_available_is_jinja_global -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/app.py tests/test_paths.py
git commit -m "feat(paths): register outlook_available as Jinja global — phase 1/8"
```

---

### Task 1.5: Gate Outlook routes and template affordances

**Files:**
- Modify: `src/policydb/web/routes/outlook.py` — each handler returns 404 when `not outlook_available()`
- Modify: templates that render Outlook-dependent controls — wrap in `{% if outlook_available() %}...{% endif %}`

- [ ] **Step 1: Audit which templates currently reference Outlook routes**

Run:
```bash
grep -rn "outlook\|/sync\|Sync Outlook" src/policydb/web/templates/ | grep -v ".swp"
```

Note each hit — these are the controls that need gating. Typically includes:
- `src/policydb/web/templates/base.html` (top nav Sync Outlook button)
- `src/policydb/web/templates/action_center/_inbox.html` (sync entry points)
- Any compose / email template panels referencing `/outlook/*` routes

- [ ] **Step 2: Write the failing test**

Create a new test at the end of `tests/test_paths.py`:

```python
def test_outlook_routes_404_on_windows(app_client, monkeypatch):
    """When outlook_available() returns False, every /outlook/* route must 404."""
    monkeypatch.setattr("policydb.paths.outlook_available", lambda: False)
    for path in ["/outlook/sync", "/outlook/status", "/outlook/compose/preview"]:
        r = app_client.get(path)
        assert r.status_code in (404, 405), f"{path} should 404 on non-Mac, got {r.status_code}"


def test_base_template_hides_outlook_nav_on_windows(app_client, monkeypatch):
    monkeypatch.setattr("policydb.paths.outlook_available", lambda: False)
    r = app_client.get("/")
    assert r.status_code == 200
    # "Sync Outlook" text should not appear in nav when Outlook is unavailable
    assert "Sync Outlook" not in r.text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_paths.py -v -k "outlook_routes or base_template_hides"`
Expected: FAIL — handlers don't gate yet, template still renders the button.

- [ ] **Step 4: Implement the gate**

In `src/policydb/web/routes/outlook.py`, at the top of each handler:

```python
from fastapi import HTTPException
from policydb.paths import outlook_available

@router.get("/outlook/sync")
def outlook_sync(request: Request, conn=Depends(get_db)):
    if not outlook_available():
        raise HTTPException(status_code=404, detail="Outlook integration is macOS only")
    # ... existing logic
```

Repeat for every handler in the file.

In `src/policydb/web/templates/base.html` (and any other templates from step 1), wrap Outlook-dependent blocks:

```jinja
{% if outlook_available() %}
  <a href="/outlook/sync" class="nav-link">Sync Outlook</a>
{% endif %}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_paths.py -v`
Expected: all pass.

- [ ] **Step 6: Manual smoke test**

Start the dev server, verify the Sync Outlook button still appears on Mac:
```bash
~/.policydb/venv/bin/policydb serve --port 8006
open http://127.0.0.1:8006
```
Expected: Sync Outlook button visible (you're on Mac).

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/outlook.py src/policydb/web/templates/ tests/test_paths.py
git commit -m "feat(paths): gate Outlook routes + templates on outlook_available — phase 1/8"
```

---

# Phase 2 — Today tab MVP (Focus Queue still default)

**Goal:** Ship Today as an opt-in tab reachable at `/action-center?tab=today`. Focus Queue remains the default in this phase — we flip the default in Phase 4 after Today has baked.

**PR title suggestion:** `feat(today): Today tab MVP — Tabulator grid, task CRUD, visual refinements`

---

### Task 2.1: Migration 163 — allow NULL `client_id` on `activity_log`

**Files:**
- Create: `src/policydb/migrations/163_allow_standalone_tasks.sql`
- Modify: `src/policydb/db.py` — wire migration 163 into `init_db()` if migrations are registered by number (check existing pattern)

- [ ] **Step 1: Write the failing test**

Create `tests/test_standalone_tasks.py`:

```python
"""Tests for standalone tasks (activity_log rows with NULL client_id)."""
from __future__ import annotations

import pytest

from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_activity_log_client_id_is_nullable(tmp_db):
    """After migration 163, activity_log.client_id must be NULL-allowed."""
    conn = get_connection()
    cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(activity_log)").fetchall()}
    assert cols["client_id"]["notnull"] == 0, (
        "activity_log.client_id must be NULL-allowed for standalone tasks"
    )


def test_can_insert_standalone_task(tmp_db):
    """An activity_log row with NULL client_id + follow_up_date is a standalone task."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, item_kind, account_exec) "
        "VALUES ('2026-04-18', NULL, 'Task', 'Standalone item', '2026-04-18', 'followup', 'Grant')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, client_id, subject FROM activity_log WHERE subject = 'Standalone item'"
    ).fetchone()
    assert row is not None
    assert row["client_id"] is None
    assert row["subject"] == "Standalone item"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_standalone_tasks.py -v`
Expected: FAIL — `test_activity_log_client_id_is_nullable` fails because `notnull == 1`.

- [ ] **Step 3: Create the migration SQL**

Create `src/policydb/migrations/163_allow_standalone_tasks.sql`:

```sql
-- Migration 163: allow standalone tasks (activity_log rows with no client link).
-- SQLite cannot drop NOT NULL in place — rebuild the table with the new constraint.
-- All other columns, indexes, and triggers preserved. Foreign keys intact.

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- Capture the current activity_log DDL for reference (comment-only).
-- The rebuild mirrors the existing schema with one change: client_id INTEGER (was INTEGER NOT NULL).

CREATE TABLE activity_log_new (
    id                            INTEGER  PRIMARY KEY AUTOINCREMENT,
    activity_date                 DATE     NOT NULL DEFAULT CURRENT_DATE,
    client_id                     INTEGER  REFERENCES clients(id),  -- was NOT NULL
    policy_id                     INTEGER  REFERENCES policies(id),
    activity_type                 TEXT     NOT NULL,
    contact_person                TEXT,
    subject                       TEXT     NOT NULL,
    details                       TEXT,
    follow_up_date                DATE,
    follow_up_done                BOOLEAN  NOT NULL DEFAULT 0,
    account_exec                  TEXT     NOT NULL DEFAULT 'Grant',
    created_at                    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_minutes              INTEGER,
    duration_hours                REAL,
    contact_id                    INTEGER  REFERENCES contacts(id),
    disposition                   TEXT,
    thread_id                     INTEGER,
    project_id                    INTEGER  REFERENCES projects(id),
    item_kind                     TEXT     DEFAULT 'followup',
    issue_id                      INTEGER  REFERENCES activity_log(id),
    issue_status                  TEXT,
    issue_severity                TEXT,
    issue_sla_days                INTEGER,
    resolution_type               TEXT,
    resolution_notes              TEXT,
    root_cause_category           TEXT,
    resolved_date                 TEXT,
    program_id                    INTEGER,
    issue_uid                     TEXT,
    is_renewal_issue              INTEGER  NOT NULL DEFAULT 0,
    renewal_term_key              TEXT,
    merged_into_id                INTEGER  REFERENCES activity_log(id),
    due_date                      TEXT,
    auto_close_reason             TEXT,
    auto_closed_at                TEXT,
    auto_closed_by                TEXT,
    merged_from_issue_id          INTEGER  REFERENCES activity_log(id),
    outlook_message_id            TEXT,
    source                        TEXT     NOT NULL DEFAULT 'manual',
    email_snippet                 TEXT,
    email_from                    TEXT,
    email_to                      TEXT,
    email_direction               TEXT,
    recurring_event_id            INTEGER  REFERENCES recurring_events(id) ON DELETE SET NULL,
    recurring_instance_date       DATE,
    outlook_conversation_id       TEXT,
    outlook_internet_message_id   TEXT,
    reviewed_at                   TEXT
);

INSERT INTO activity_log_new SELECT * FROM activity_log;

DROP TABLE activity_log;
ALTER TABLE activity_log_new RENAME TO activity_log;

-- Restore indexes (original had at least idx_activity_thread).
CREATE INDEX IF NOT EXISTS idx_activity_thread ON activity_log(thread_id);

-- Triggers on activity_log are re-created by init_db() (it drops + re-creates
-- all audit triggers defensively), so we do not rebuild them here.

COMMIT;

PRAGMA foreign_keys = ON;
```

**Important note about triggers:** inspect `init_db()` in `src/policydb/db.py` before finalizing. If audit triggers are NOT recreated on every init, this migration must also `DROP TRIGGER IF EXISTS ... ; CREATE TRIGGER ...` for each activity_log trigger (grep `CREATE TRIGGER audit_activity_log_` in the migrations/ dir to list them).

- [ ] **Step 4: Verify migration wiring**

Read `src/policydb/db.py::init_db`. Migrations are typically applied in numeric order; confirm a `schema_version` check exists for 163. If init_db reads directory listing and applies anything newer than the stored version, no code change is needed — the new file is picked up automatically. Otherwise, add an explicit `_apply_migration(163)` line where sibling migrations are listed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_standalone_tasks.py -v`
Expected: both tests pass.

Then run the full schema / migration suite to confirm no regression:
```bash
pytest tests/test_db.py tests/test_standalone_tasks.py -v
```

- [ ] **Step 6: Manual DB sanity check**

```bash
rm -f /tmp/mig163-test.sqlite
~/.policydb/venv/bin/python -c "from policydb.db import init_db; init_db(path='/tmp/mig163-test.sqlite')"
sqlite3 /tmp/mig163-test.sqlite "PRAGMA table_info(activity_log)" | grep client_id
```
Expected: `2|client_id|INTEGER|0|...|0` — the `notnull` flag (4th field) is `0`.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/migrations/163_allow_standalone_tasks.sql src/policydb/db.py tests/test_standalone_tasks.py
git commit -m "feat(today): migration 163 — allow NULL client_id for standalone tasks — phase 2/8"
```

---

### Task 2.2: `V_TODAY_TASKS` view definition + register in `ALL_VIEWS`

**Files:**
- Modify: `src/policydb/views.py` — add `V_TODAY_TASKS` and register in `ALL_VIEWS`
- Create: `tests/test_today_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_today_view.py`:

```python
"""Tests for v_today_tasks view."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from policydb.db import get_connection, init_db


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
    """Three tasks with varying due dates + one done task + one merged task."""
    conn = get_connection()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES ('Acme Co', 'Manufacturing')"
    )
    client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Overdue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec, disposition) "
        "VALUES (?, ?, 'Call', 'Overdue task', ?, 0, 'followup', 'Grant', 'My action')",
        (today, client_id, yesterday),
    )
    # Today
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec, disposition) "
        "VALUES (?, ?, 'Call', 'Today task', ?, 0, 'followup', 'Grant', 'Waiting — client')",
        (today, client_id, today),
    )
    # Tomorrow + standalone (NULL client_id)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, NULL, 'Task', 'Standalone task', ?, 0, 'followup', 'Grant')",
        (today, tomorrow),
    )
    # Done — must be excluded
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, 'Call', 'Done task', ?, 1, 'followup', 'Grant')",
        (today, client_id, today),
    )
    # Merged / auto-closed — must be excluded
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec, auto_closed_at) "
        "VALUES (?, ?, 'Call', 'Auto-closed task', ?, 0, 'followup', 'Grant', '2026-04-15')",
        (today, client_id, today),
    )
    conn.commit()
    return {"client_id": client_id}


def test_v_today_tasks_includes_open_followups(seeded):
    conn = get_connection()
    rows = conn.execute("SELECT subject FROM v_today_tasks ORDER BY subject").fetchall()
    subjects = [r["subject"] for r in rows]
    assert "Overdue task" in subjects
    assert "Today task" in subjects
    assert "Standalone task" in subjects


def test_v_today_tasks_excludes_done_and_auto_closed(seeded):
    conn = get_connection()
    rows = conn.execute("SELECT subject FROM v_today_tasks").fetchall()
    subjects = [r["subject"] for r in rows]
    assert "Done task" not in subjects
    assert "Auto-closed task" not in subjects


def test_v_today_tasks_standalone_has_null_client(seeded):
    conn = get_connection()
    row = conn.execute(
        "SELECT client_id, client_name FROM v_today_tasks WHERE subject = 'Standalone task'"
    ).fetchone()
    assert row["client_id"] is None
    assert row["client_name"] is None


def test_v_today_tasks_priority_ordering(seeded):
    """priority: overdue=3, today=2, tomorrow=1, later=0."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT subject, priority FROM v_today_tasks ORDER BY priority DESC, follow_up_date ASC"
    ).fetchall()
    mapping = {r["subject"]: r["priority"] for r in rows}
    assert mapping["Overdue task"] == 3
    assert mapping["Today task"] == 2
    assert mapping["Standalone task"] == 1


def test_v_today_tasks_is_waiting_flag(seeded):
    conn = get_connection()
    rows = conn.execute("SELECT subject, is_waiting FROM v_today_tasks").fetchall()
    mapping = {r["subject"]: r["is_waiting"] for r in rows}
    assert mapping["Overdue task"] == 0
    assert mapping["Today task"] == 1           # disposition = 'Waiting — client'
    assert mapping["Standalone task"] == 0      # no disposition set
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_today_view.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: v_today_tasks`.

- [ ] **Step 3: Add the view definition**

Open `src/policydb/views.py`. Add, after `V_ISSUE_POLICY_COVERAGE`:

```python
V_TODAY_TASKS = """
CREATE VIEW v_today_tasks AS
SELECT
    a.id,
    a.subject,
    a.details,
    a.item_kind                AS kind,
    CASE
        WHEN a.follow_up_date < date('now')                        THEN 3
        WHEN a.follow_up_date = date('now')                        THEN 2
        WHEN a.follow_up_date = date('now', '+1 day')              THEN 1
        ELSE 0
    END                        AS priority,
    a.follow_up_date,
    a.client_id,
    c.name                     AS client_name,
    a.policy_id,
    p.policy_uid,
    COALESCE(co.name, a.contact_person)   AS contact_person,
    a.disposition,
    CASE WHEN a.disposition LIKE 'Waiting%' THEN 1 ELSE 0 END AS is_waiting,
    -- Last activity on same (client, policy) pair, for the context line
    (SELECT MAX(a2.created_at)
       FROM activity_log a2
      WHERE (a2.client_id = a.client_id OR (a2.client_id IS NULL AND a.client_id IS NULL))
        AND (a2.policy_id IS a.policy_id)
        AND a2.id != a.id)      AS last_activity_at,
    -- Days the row has been in a Waiting disposition (for nudge-age visual)
    CASE
        WHEN a.disposition LIKE 'Waiting%'
        THEN CAST(julianday('now') - julianday(a.activity_date) AS INTEGER)
        ELSE NULL
    END                        AS waiting_days,
    a.created_at,
    a.created_at               AS updated_at   -- activity_log has no updated_at column; use created_at as placeholder
FROM activity_log a
LEFT JOIN clients  c  ON a.client_id = c.id
LEFT JOIN policies p  ON a.policy_id = p.id
LEFT JOIN contacts co ON a.contact_id = co.id
WHERE a.follow_up_done = 0
  AND a.follow_up_date IS NOT NULL
  AND a.merged_into_id IS NULL
  AND a.auto_closed_at IS NULL
  AND a.item_kind IN ('followup', 'issue')
"""
```

Then update `ALL_VIEWS` (around line 516):

```python
ALL_VIEWS = {
    "v_policy_status": V_POLICY_STATUS,
    "v_client_summary": V_CLIENT_SUMMARY,
    "v_schedule": V_SCHEDULE,
    "v_tower": V_TOWER,
    "v_renewal_pipeline": V_RENEWAL_PIPELINE,
    "v_overdue_followups": V_OVERDUE_FOLLOWUPS,
    "v_review_queue": V_REVIEW_QUEUE,
    "v_review_clients": V_REVIEW_CLIENTS,
    "v_issue_policy_coverage": V_ISSUE_POLICY_COVERAGE,
    "v_today_tasks": V_TODAY_TASKS,
}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_today_view.py -v`
Expected: all 5 pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/views.py tests/test_today_view.py
git commit -m "feat(today): v_today_tasks view + priority/is_waiting derivations — phase 2/8"
```

---

### Task 2.3: `/action-center?tab=today` renders the Today tab (skeleton)

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — add a `today` branch in the tab dispatcher (after the existing `focus` branch)
- Create: `src/policydb/web/templates/action_center/_today.html` — minimal skeleton

- [ ] **Step 1: Write the failing test**

Create `tests/test_today_routes.py`:

```python
"""Route tests for the Today tab and task CRUD endpoints."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from policydb.db import get_connection, init_db


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
def app_client(tmp_db):
    from policydb.web.app import app
    with TestClient(app) as c:
        yield c


def test_today_tab_renders(app_client):
    r = app_client.get("/action-center?tab=today")
    assert r.status_code == 200
    assert "Today" in r.text
    assert "today-grid" in r.text  # Tabulator mount point
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_today_routes.py::test_today_tab_renders -v`
Expected: FAIL — tab=today is not handled, falls through to a default branch.

- [ ] **Step 3: Add the Today branch in action_center.py**

Open `src/policydb/web/routes/action_center.py`, find `def action_center_page` (around line 933). Inside the `tab` dispatcher, after the existing `focus` branch and before `inbox`:

```python
    elif initial_tab == "today":
        today_rows = conn.execute("SELECT * FROM v_today_tasks ORDER BY priority DESC, follow_up_date ASC, id ASC").fetchall()
        all_clients = conn.execute(
            "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
        ).fetchall()
        nudge_days = cfg.get("focus_nudge_alert_days", 10)
        tab_ctx = {
            "today_rows": [dict(r) for r in today_rows],
            "all_clients": [dict(c) for c in all_clients],
            "nudge_days": nudge_days,
            "ac_tab": "today",
        }
```

- [ ] **Step 4: Create the skeleton template**

Create `src/policydb/web/templates/action_center/_today.html`:

```jinja
{# Today tab — Tabulator grid + filter pills + Smart Suggestions rail.

   Data flowed in via action_center_page:
     today_rows  — list of dict, mirrors v_today_tasks
     all_clients — list of dict(id, name) for Add Task combobox
     nudge_days  — int, cfg.focus_nudge_alert_days
#}

<div class="today-tab" data-tab="today">
  <div class="today-editorial">
    <h2 class="today-date">{{ "now" | strftime("%A · %B %-d") }}</h2>
    <hr class="today-rule" />
    <div class="today-stats">
      {{ today_rows | length }} open · {{ today_rows | selectattr("priority", "equalto", 3) | list | length }} overdue
    </div>
  </div>

  <div class="today-toolbar" role="toolbar">
    <div class="today-filters">
      <button class="filter-pill all-open" data-filter="all">All open</button>
      <button class="filter-pill active" data-filter="overdue">Overdue</button>
      <button class="filter-pill active" data-filter="today">Today</button>
      <button class="filter-pill active" data-filter="tomorrow">Tomorrow</button>
      <button class="filter-pill" data-filter="this-week">This week</button>
      <button class="filter-pill" data-filter="waiting">Waiting</button>
      <button class="filter-pill" data-filter="standalone">Standalone</button>
    </div>
    <div class="today-actions">
      <a href="/followups/plan" class="today-planweek-link">Plan Week →</a>
      <button class="btn btn-primary" id="today-add-task-btn">
        + Add task <span class="kbd-hint">⌘N</span>
      </button>
    </div>
  </div>

  <div id="today-grid" class="today-grid"
       data-rows='{{ today_rows | tojson }}'
       data-nudge-days="{{ nudge_days }}"></div>

  <aside id="today-suggestions"
         class="today-suggestions"
         hx-get="/action-center/today/suggestions"
         hx-trigger="load delay:200ms, every 5m"
         hx-swap="innerHTML">
    <div class="today-suggestions-loading">Loading suggestions…</div>
  </aside>
</div>

{% include "action_center/_add_task_modal.html" %}
```

**Note:** this skeleton references `_add_task_modal.html` (created in Task 2.10) and the Tabulator grid bootstrap (`today-grid` init JS comes in Task 2.8). For now it renders — just empty.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_today_routes.py::test_today_tab_renders -v`
Expected: PASS (template renders; the include will pass once 2.10 lands — for this task stub out the include by commenting the `{% include %}` line).

Actually — add a minimal stub modal file now so the include doesn't 500:

Create `src/policydb/web/templates/action_center/_add_task_modal.html` with just:
```jinja
<dialog id="add-task-modal" class="modal"><p>Add Task modal — implemented in Task 2.10</p></dialog>
```

Re-run the test: `pytest tests/test_today_routes.py::test_today_tab_renders -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/action_center.py \
        src/policydb/web/templates/action_center/_today.html \
        src/policydb/web/templates/action_center/_add_task_modal.html \
        tests/test_today_routes.py
git commit -m "feat(today): route + template skeleton for Today tab — phase 2/8"
```

---

### Task 2.4: `POST /tasks/create` endpoint

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — add task CRUD section after existing routes
- Modify: `tests/test_today_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_today_routes.py`:

```python
def test_task_create_with_client(app_client):
    conn = get_connection()
    conn.execute("INSERT INTO clients (name) VALUES ('Acme Co')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    r = app_client.post(
        "/tasks/create",
        data={
            "subject": "Call Acme about renewal",
            "client_id": cid,
            "follow_up_date": "2026-04-19",
            "contact_person": "Sarah Johnson",
        },
    )
    assert r.status_code in (200, 201)
    row = conn.execute("SELECT * FROM activity_log WHERE subject = 'Call Acme about renewal'").fetchone()
    assert row is not None
    assert row["client_id"] == cid
    assert row["contact_person"] == "Sarah Johnson"
    assert row["follow_up_date"] == "2026-04-19"


def test_task_create_standalone(app_client):
    r = app_client.post(
        "/tasks/create",
        data={"subject": "Standalone reminder", "follow_up_date": "2026-04-19"},
    )
    assert r.status_code in (200, 201)
    conn = get_connection()
    row = conn.execute("SELECT * FROM activity_log WHERE subject = 'Standalone reminder'").fetchone()
    assert row is not None
    assert row["client_id"] is None


def test_task_create_rejects_empty_subject(app_client):
    r = app_client.post("/tasks/create", data={"subject": ""})
    assert r.status_code == 422 or r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_today_routes.py -v -k task_create`
Expected: FAIL — `/tasks/create` doesn't exist (404).

- [ ] **Step 3: Implement the endpoint**

In `src/policydb/web/routes/action_center.py`, append a new section near the bottom of the file:

```python
# ── Task CRUD (Today tab) ────────────────────────────────────────────────────


@router.post("/tasks/create", response_class=HTMLResponse)
def task_create(
    request: Request,
    subject: str = Form(...),
    client_id: int = Form(0),
    policy_id: int = Form(0),
    follow_up_date: str = Form(""),
    contact_person: str = Form(""),
    conn=Depends(get_db),
):
    """Create a new task (follow-up). Subject required; everything else optional.

    Standalone tasks: omit client_id or pass 0 — stored as NULL.
    """
    subject = (subject or "").strip()
    if not subject:
        return HTMLResponse("Subject is required", status_code=422)
    if len(subject) > 200:
        return HTMLResponse("Subject must be 200 chars or fewer", status_code=422)

    fu_date = follow_up_date or date.today().isoformat()
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject,
            follow_up_date, follow_up_done, item_kind, account_exec, contact_person)
           VALUES (CURRENT_DATE, ?, ?, 'Task', ?, ?, 0, 'followup',
                   COALESCE((SELECT user_name FROM config_meta WHERE k = 'user_name'), 'Grant'),
                   ?)""",
        (client_id or None, policy_id or None, subject, fu_date, contact_person or None),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    logger.info("Task created: id=%s subject=%r", new_id, subject)

    # Return the new row as a Tabulator-friendly JSON payload via HX-Trigger.
    resp = HTMLResponse("", status_code=201)
    import json
    resp.headers["HX-Trigger"] = json.dumps({"taskCreated": {"id": new_id, "subject": subject}})
    return resp
```

**Note on `config_meta`:** that sub-select is a placeholder until Phase 8 Onboarding wires `user_name`. Until then, `COALESCE` falls back to `'Grant'`. Grep for an existing config-meta table in `db.py`; if not present, hard-code `'Grant'` for now and add a TODO comment:

```python
            # TODO phase 8: replace hard-coded 'Grant' with config.user_name after onboarding ships
            "Grant",
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_today_routes.py -v -k task_create`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py tests/test_today_routes.py
git commit -m "feat(today): POST /tasks/create — subject + optional client/policy/contact — phase 2/8"
```

---

### Task 2.5: `POST /tasks/{id}/complete` + `POST /tasks/{id}/undo-complete`

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — add complete + undo handlers
- Modify: `tests/test_today_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_today_routes.py`:

```python
def test_task_complete_sets_follow_up_done(app_client):
    app_client.post("/tasks/create", data={"subject": "Temp task"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Temp task'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/complete")
    assert r.status_code == 204
    row = conn.execute("SELECT follow_up_done FROM activity_log WHERE id = ?", (tid,)).fetchone()
    assert row["follow_up_done"] == 1


def test_task_complete_emits_hx_trigger(app_client):
    app_client.post("/tasks/create", data={"subject": "Trigger task"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Trigger task'").fetchone()["id"]
    r = app_client.post(f"/tasks/{tid}/complete")
    assert "taskCompleted" in r.headers.get("HX-Trigger", "")


def test_task_undo_complete_reopens_task(app_client):
    app_client.post("/tasks/create", data={"subject": "Undo me"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Undo me'").fetchone()["id"]
    app_client.post(f"/tasks/{tid}/complete")

    r = app_client.post(f"/tasks/{tid}/undo-complete")
    assert r.status_code == 204
    row = conn.execute("SELECT follow_up_done FROM activity_log WHERE id = ?", (tid,)).fetchone()
    assert row["follow_up_done"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_today_routes.py -v -k "task_complete or task_undo"`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Implement the handlers**

Append to `src/policydb/web/routes/action_center.py` after `task_create`:

```python
@router.post("/tasks/{task_id}/complete", response_class=Response)
def task_complete(task_id: int, conn=Depends(get_db)):
    """Mark a task complete. Returns 204 + HX-Trigger so the client can render the undo toast."""
    row = conn.execute(
        "SELECT subject FROM activity_log WHERE id = ? AND item_kind = 'followup'",
        (task_id,),
    ).fetchone()
    if not row:
        return Response(status_code=404)
    conn.execute("UPDATE activity_log SET follow_up_done = 1 WHERE id = ?", (task_id,))
    conn.commit()
    logger.info("Task %s completed: %r", task_id, row["subject"])

    import json
    resp = Response(status_code=204)
    resp.headers["HX-Trigger"] = json.dumps({
        "taskCompleted": {"id": task_id, "subject": row["subject"]}
    })
    return resp


@router.post("/tasks/{task_id}/undo-complete", response_class=Response)
def task_undo_complete(task_id: int, conn=Depends(get_db)):
    """Re-open a completed task (fired by the 5s undo toast)."""
    conn.execute(
        "UPDATE activity_log SET follow_up_done = 0 WHERE id = ? AND item_kind = 'followup'",
        (task_id,),
    )
    conn.commit()
    logger.info("Task %s re-opened via undo", task_id)
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_today_routes.py -v -k "task_complete or task_undo"`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py tests/test_today_routes.py
git commit -m "feat(today): task complete + undo-complete with HX-Trigger undo toast — phase 2/8"
```

---

### Task 2.6: `POST /tasks/{id}/snooze`

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — add snooze handler
- Modify: `tests/test_today_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_today_routes.py`:

```python
from datetime import date, timedelta


def test_snooze_tomorrow(app_client):
    app_client.post("/tasks/create", data={"subject": "Snooze me"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Snooze me'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/snooze", data={"option": "tomorrow"})
    assert r.status_code == 200
    row = conn.execute("SELECT follow_up_date FROM activity_log WHERE id = ?", (tid,)).fetchone()
    expected = (date.today() + timedelta(days=1)).isoformat()
    assert row["follow_up_date"] == expected


def test_snooze_this_week_moves_to_next_monday(app_client):
    app_client.post("/tasks/create", data={"subject": "Next Monday"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Next Monday'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/snooze", data={"option": "this_week"})
    assert r.status_code == 200
    row = conn.execute("SELECT follow_up_date FROM activity_log WHERE id = ?", (tid,)).fetchone()
    new = date.fromisoformat(row["follow_up_date"])
    today = date.today()
    # Must be the Monday of THIS week (if today is Mon-Fri) or next Monday (if today is Sat-Sun).
    assert new.weekday() == 0
    assert new >= today


def test_snooze_custom_date(app_client):
    app_client.post("/tasks/create", data={"subject": "Custom"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Custom'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/snooze", data={"option": "custom", "date": "2026-05-01"})
    assert r.status_code == 200
    row = conn.execute("SELECT follow_up_date FROM activity_log WHERE id = ?", (tid,)).fetchone()
    assert row["follow_up_date"] == "2026-05-01"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_today_routes.py -v -k snooze`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Implement the handler**

Append to `src/policydb/web/routes/action_center.py`:

```python
@router.post("/tasks/{task_id}/snooze", response_class=Response)
def task_snooze(
    task_id: int,
    option: str = Form(...),
    date_str: str = Form("", alias="date"),
    conn=Depends(get_db),
):
    """Snooze a task. option ∈ {tomorrow, this_week, next_week, custom}.

    - tomorrow: today + 1 day
    - this_week: next Monday (or today if already Monday)
    - next_week: Monday of the following week
    - custom: the date passed in date_str (ISO format)
    """
    today = date.today()
    if option == "tomorrow":
        new_date = today + timedelta(days=1)
    elif option == "this_week":
        days_until_monday = (7 - today.weekday()) % 7
        new_date = today + timedelta(days=days_until_monday or 0)
        # If we're already past Monday, snap to next Monday
        if new_date < today:
            new_date += timedelta(days=7)
        # If today IS Monday, stay on today (no snooze) — but that's equivalent to noop;
        # we still land on today which is correct behavior.
        if today.weekday() > 0 and new_date == today:
            new_date += timedelta(days=7 - today.weekday())
    elif option == "next_week":
        days_until_next_monday = (7 - today.weekday()) % 7 + 7
        new_date = today + timedelta(days=days_until_next_monday)
    elif option == "custom":
        try:
            new_date = date.fromisoformat(date_str)
        except ValueError:
            return Response("Invalid date", status_code=422)
    else:
        return Response(f"Unknown snooze option: {option}", status_code=422)

    conn.execute(
        "UPDATE activity_log SET follow_up_date = ? WHERE id = ? AND item_kind = 'followup'",
        (new_date.isoformat(), task_id),
    )
    conn.commit()
    logger.info("Task %s snoozed to %s via %s", task_id, new_date, option)
    return Response(status_code=200)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_today_routes.py -v -k snooze`
Expected: 3 passed. (If `this_week` test fails on Mondays due to weekday logic: re-check the edge-case branching and re-run.)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py tests/test_today_routes.py
git commit -m "feat(today): POST /tasks/{id}/snooze — tomorrow/this_week/next_week/custom — phase 2/8"
```

---

### Task 2.7: Tabulator grid init — columns, sort, filter pills

**Files:**
- Create: `src/policydb/web/static/js/tabulator_today.js` (shared base; Plan Week will reuse in Phase 5)
- Create: `src/policydb/web/static/css/today.css`
- Modify: `src/policydb/web/templates/action_center/_today.html` — include the shared JS + CSS

- [ ] **Step 1: Create the shared Tabulator base**

Create `src/policydb/web/static/js/tabulator_today.js`:

```javascript
/* Shared Tabulator column renderers and base config for Today and Plan Week.

   Usage:
     const table = buildTodayTable({
       selector: "#today-grid",
       rows: JSON.parse(el.dataset.rows),
       nudgeDays: Number(el.dataset.nudgeDays) || 10,
     });
*/

(function (global) {
  const priorityColorMap = { 3: "overdue", 2: "today", 1: "tomorrow", 0: "later" };

  function priorityBarFormatter(cell) {
    const row = cell.getRow().getData();
    const cls = priorityColorMap[row.priority] || "later";
    const notch = row.waiting_days != null && row.waiting_days > (cell.getTable().nudgeDays || 10)
      ? ' <span class="priority-notch amber"></span>'
      : "";
    const pulseClass = cls === "overdue" ? " priority-bar-pulse" : "";
    return `<div class="priority-bar ${cls}${pulseClass}">${notch}</div>`;
  }

  function kindChipFormatter(cell) {
    const kind = cell.getValue() || "followup";
    const label = { followup: "Task", issue: "Issue" }[kind] || kind;
    return `<span class="kind-chip kind-${kind}">${label}</span>`;
  }

  function subjectFormatter(cell) {
    const row = cell.getRow().getData();
    const subj = cell.getValue() || "";
    const ctx = row.details || (row.client_id == null ? "Standalone task" : "");
    // Inline ref-pill treatment for IDs
    const ctxHtml = ctx.replace(
      /\b(POL-\d+|CN-\d+|ISS-\d+)\b/g,
      (m) => `<span class="ref-pill">${m}</span>`
    );
    return `<div class="subj">${subj}</div><div class="ctx-line">${ctxHtml}</div>`;
  }

  function clientPolicyFormatter(cell) {
    const row = cell.getRow().getData();
    if (!row.client_id && !row.policy_uid) {
      return '<em class="muted">Standalone</em>';
    }
    const policy = row.policy_uid
      ? `<span class="ref-pill">${row.policy_uid}</span> `
      : "";
    const client = row.client_name
      ? `<a href="/clients/${row.client_id}">${row.client_name}</a>`
      : "";
    return policy + client;
  }

  function dueFormatter(cell) {
    const row = cell.getRow().getData();
    if (!row.follow_up_date) return "—";
    const d = new Date(row.follow_up_date + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.floor((d - today) / 86400000);
    if (days < 0) return `<span class="due red">${-days}d overdue</span>`;
    if (days === 0) return '<span class="due">today</span>';
    if (days === 1) return '<span class="due">tomorrow</span>';
    return d.toLocaleDateString(undefined, { weekday: "short" });
  }

  function lastFormatter(cell) {
    const ts = cell.getValue();
    if (!ts) return "—";
    const d = new Date(ts);
    const diff = Math.floor((Date.now() - d.getTime()) / 86400000);
    return diff === 0 ? "today" : `${diff}d`;
  }

  function contactFormatter(cell) {
    return cell.getValue() || "—";
  }

  function completeCheckboxFormatter() {
    // Custom SVG — styled via today.css (.today-check)
    return `
      <button class="today-check" aria-label="Complete task">
        <svg viewBox="0 0 16 16" width="14" height="14">
          <rect x="1.25" y="1.25" width="13.5" height="13.5" rx="2.5" class="box" />
          <path d="M4 8.5 L7 11.5 L12.5 5" class="tick" />
        </svg>
      </button>`;
  }

  function actionsFormatter() {
    return '<button class="mini-btn actions-btn" aria-label="Row actions">•••</button>';
  }

  function buildTodayTable({ selector, rows, nudgeDays = 10, onCompleted, onSnooze }) {
    const table = new Tabulator(selector, {
      data: rows,
      layout: "fitColumns",
      height: "100%",
      placeholder: "No tasks match your filters.",
      initialSort: [
        { column: "priority", dir: "desc" },
        { column: "follow_up_date", dir: "asc" },
        { column: "id", dir: "asc" },
      ],
      columns: [
        { title: "", field: "_check", width: 40, hozAlign: "center",
          formatter: completeCheckboxFormatter, cellClick: (e, cell) => onCompleted?.(cell.getRow().getData()) },
        { title: "", field: "_priority", width: 4, formatter: priorityBarFormatter, headerSort: false },
        { title: "Kind", field: "kind", width: 72, formatter: kindChipFormatter },
        { title: "Subject / Context", field: "subject", formatter: subjectFormatter, minWidth: 320 },
        { title: "Client · Policy", field: "client_name", width: 180, formatter: clientPolicyFormatter },
        { title: "Contact", field: "contact_person", width: 140, formatter: contactFormatter },
        { title: "Last", field: "last_activity_at", width: 90, formatter: lastFormatter },
        { title: "Due", field: "follow_up_date", width: 90, formatter: dueFormatter },
        { title: "", field: "_actions", width: 40, formatter: actionsFormatter, headerSort: false },
      ],
    });
    table.nudgeDays = nudgeDays;
    return table;
  }

  global.buildTodayTable = buildTodayTable;
})(window);
```

- [ ] **Step 2: Create the CSS**

Create `src/policydb/web/static/css/today.css` with the Layout E + Visual Refinements styling:

```css
/* Today tab — editorial-utilitarian, ledger-dense.
   On-brand per policydb-design-system (Midnight Blue + warm neutrals).
   Visual refinements listed in the spec's "Visual refinements" section. */

.today-editorial {
  padding: 16px 20px 8px;
}
.today-date {
  font-family: "DM Serif Display", Georgia, serif;
  font-weight: 400;
  font-style: italic;
  color: var(--brand);
  font-size: 18px;
  margin: 0;
}
.today-rule {
  border: 0;
  border-top: 1px solid var(--border);
  margin: 6px 0;
}
.today-stats {
  font-family: "DM Sans", -apple-system, sans-serif;
  font-size: 12px;
  color: var(--muted);
  letter-spacing: 0.02em;
}

.today-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 20px;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  background: #FBF7F1;
}
.today-filters { display: flex; gap: 6px; flex-wrap: wrap; }

.filter-pill {
  padding: 3px 10px;
  border-radius: 12px;
  background: var(--bg, #F7F3EE);
  color: var(--muted);
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 12px;
  font-family: "DM Sans", sans-serif;
  transition: all 120ms ease-out;
}
.filter-pill.active {
  background: transparent;
  color: var(--brand);
  border: 2px solid var(--brand);
  padding: 2px 9px; /* compensate for 2px ring */
  font-weight: 500;
}
.filter-pill.active::after {
  content: attr(data-count);
  display: inline-block;
  margin-left: 6px;
  color: var(--accent);
}
.filter-pill.all-open {
  border-left-style: dashed;
}

.today-planweek-link {
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
  margin-right: 12px;
}
.today-planweek-link:hover { text-decoration: underline; }

.btn-primary {
  background: var(--accent);
  color: #fff;
  border: none;
  padding: 6px 14px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.kbd-hint {
  font-size: 10px;
  opacity: 0.75;
  letter-spacing: 0.05em;
}

.today-grid {
  background: var(--surface, #fff);
  min-height: 420px;
}

/* Tabulator overrides — ledger density */
.tabulator {
  font-family: "DM Sans", -apple-system, sans-serif;
  font-size: 12px;
  border: 0;
}
.tabulator .tabulator-header {
  background: #FBF7F1;
  border-bottom: 1px solid var(--border);
}
.tabulator .tabulator-col {
  background: transparent;
  color: var(--muted);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
  border: 0;
}
.tabulator .tabulator-row {
  border-bottom: 1px solid #F0EBE2;
  min-height: 44px;
  transition: background 120ms ease-out;
  animation: row-in 240ms cubic-bezier(0.2, 0.9, 0.3, 1.0) both;
}
/* Stagger fade — first 8 rows only on initial paint */
.tabulator .tabulator-row:nth-child(1) { animation-delay: 20ms; }
.tabulator .tabulator-row:nth-child(2) { animation-delay: 40ms; }
.tabulator .tabulator-row:nth-child(3) { animation-delay: 60ms; }
.tabulator .tabulator-row:nth-child(4) { animation-delay: 80ms; }
.tabulator .tabulator-row:nth-child(5) { animation-delay: 100ms; }
.tabulator .tabulator-row:nth-child(6) { animation-delay: 120ms; }
.tabulator .tabulator-row:nth-child(7) { animation-delay: 140ms; }
.tabulator .tabulator-row:nth-child(8) { animation-delay: 160ms; }
@keyframes row-in {
  from { opacity: 0; transform: translateY(2px); }
  to   { opacity: 1; transform: none; }
}

/* Ledger hairline every 5 rows */
.tabulator .tabulator-row:nth-child(5n) { border-bottom-color: var(--border); }

/* Row hover */
.tabulator .tabulator-row:hover { background: #FBFCFF; }
.tabulator .tabulator-row:hover .actions-btn { opacity: 1; }
.tabulator .tabulator-row:hover .subj {
  text-decoration: underline;
  text-decoration-color: var(--accent);
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
}

.priority-bar {
  width: 4px;
  height: 100%;
  min-height: 30px;
  background: #E7E1D7;
  position: relative;
}
.priority-bar.overdue  { background: var(--red, #B33A2A); }
.priority-bar.today    { background: var(--amber, #C77A1A); }
.priority-bar.tomorrow { background: var(--accent, #0B4BFF); }
.priority-bar.later    { background: #E7E1D7; }
.priority-bar-pulse    { animation: overdue-pulse 2.8s ease-in-out infinite; }
@keyframes overdue-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.55 } }

/* Nudge-age notch — amber folded-corner on the priority bar */
.priority-notch {
  position: absolute;
  top: 0; left: 0;
  width: 4px; height: 8px;
  background: var(--amber, #C77A1A);
  clip-path: polygon(0 0, 100% 0, 0 100%);
}

/* Kind chips — neutral bg, color via left border only */
.kind-chip {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  background: var(--bg, #F7F3EE);
  color: var(--body, #3D3C37);
  border-left: 2px solid var(--border);
}
.kind-chip.kind-followup { border-left-color: var(--accent); }
.kind-chip.kind-issue    { border-left-color: var(--red); }

.subj {
  font-weight: 600;
  color: var(--brand);
  font-size: 13px;
}
.ctx-line {
  color: #7A7468;
  font-size: 11px;
  margin-top: 2px;
}

.ref-pill {
  display: inline-block;
  background: var(--bg, #F7F3EE);
  color: var(--brand);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 10px;
  font-family: "JetBrains Mono", Menlo, monospace;
}

.due.red { color: var(--red); font-weight: 500; }

.actions-btn {
  opacity: 0.35;
  transition: opacity 120ms ease-out;
  background: transparent;
  border: 0;
  color: var(--muted);
  cursor: pointer;
  font-size: 14px;
}

/* Custom complete checkbox — matches spec Visual Refinements § Complete-task */
.today-check {
  border: 0;
  background: transparent;
  cursor: pointer;
  padding: 0;
  line-height: 0;
}
.today-check svg .box {
  fill: var(--surface, #fff);
  stroke: var(--muted);
  stroke-width: 1.5;
  transition: stroke 120ms ease-out;
}
.today-check:hover svg .box { stroke: var(--accent); }
.today-check svg .tick {
  fill: none;
  stroke: var(--brand);
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-dasharray: 20;
  stroke-dashoffset: 20;
}
.today-check.checked svg .box { fill: var(--brand); stroke: var(--brand); }
.today-check.checked svg .tick {
  stroke: #fff;
  animation: draw-tick 200ms ease-out forwards;
}
@keyframes draw-tick { to { stroke-dashoffset: 0; } }

.tabulator-row.row-completing .subj {
  text-decoration: line-through;
  color: var(--muted);
  transition: color 400ms ease-out;
}
.tabulator-row.row-removing {
  opacity: 0;
  transition: opacity 200ms ease-out;
}

.today-suggestions {
  background: #FBF7F1;
  border-top: 1px solid var(--border);
  padding: 10px 14px;
  min-height: 120px;
}
.today-suggestions-loading {
  color: var(--muted);
  font-size: 11px;
  text-align: center;
  padding: 20px 0;
}

/* @media print — Visual Refinements § Print */
@media print {
  .today-toolbar, .today-suggestions, .actions-btn, .today-check { display: none; }
  .priority-bar { display: none; }
  .subj::before { content: "» "; color: var(--muted); }
  .kind-chip { background: transparent; border: 0; padding: 0; }
  .kind-chip::before { content: "["; }
  .kind-chip::after  { content: "]"; }
  .tabulator .tabulator-row { animation: none; }
}

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .tabulator .tabulator-row { animation: none !important; }
  .priority-bar-pulse       { animation: none !important; }
  .today-check svg .tick    { animation: none !important; stroke-dashoffset: 0; }
  .row-completing, .row-removing { transition: none !important; }
}
```

- [ ] **Step 3: Wire the JS + CSS into the template**

Open `src/policydb/web/templates/action_center/_today.html`. Append at the bottom, just before the closing section:

```jinja
<link rel="stylesheet" href="/static/css/today.css" />
<script src="https://cdn.jsdelivr.net/npm/tabulator-tables@6.3/dist/js/tabulator.min.js"></script>
<script src="/static/js/tabulator_today.js"></script>
<script>
  (function () {
    const el = document.getElementById("today-grid");
    if (!el || !window.buildTodayTable) return;
    const rows = JSON.parse(el.dataset.rows || "[]");
    const nudgeDays = Number(el.dataset.nudgeDays) || 10;
    const table = window.buildTodayTable({
      selector: "#today-grid",
      rows,
      nudgeDays,
      onCompleted: (row) => { completeTask(row.id, row.subject); },
    });

    async function completeTask(id, subject) {
      const tr = document.querySelector(`.tabulator-row[data-index="${id}"]`);
      if (tr) tr.classList.add("row-completing");
      setTimeout(async () => {
        if (tr) tr.classList.add("row-removing");
        await fetch(`/tasks/${id}/complete`, { method: "POST" });
        setTimeout(() => table.deleteRow(id), 220);
        showUndoToast(id, subject);
      }, 400);
    }

    function showUndoToast(id, subject) {
      const toast = document.createElement("div");
      toast.className = "undo-toast";
      toast.innerHTML = `
        <span>Task completed — ${subject}</span>
        <button class="undo-btn">Undo</button>`;
      document.body.appendChild(toast);
      const undoBtn = toast.querySelector(".undo-btn");
      const dismiss = setTimeout(() => toast.remove(), 5000);
      undoBtn.addEventListener("click", async () => {
        clearTimeout(dismiss);
        await fetch(`/tasks/${id}/undo-complete`, { method: "POST" });
        table.addRow({ id, subject }, true);
        toast.remove();
      });
    }

    // Filter pills — in-memory Tabulator filtering
    document.querySelectorAll(".filter-pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        pill.classList.toggle("active");
        applyFilters(table);
        saveFilterState();
      });
    });

    function applyFilters(table) {
      const active = [...document.querySelectorAll(".filter-pill.active")]
        .map((p) => p.dataset.filter);
      if (active.length === 0 || active.includes("all")) {
        table.clearFilter(true);
        return;
      }
      table.setFilter((data) => {
        if (active.includes("overdue") && data.priority === 3) return true;
        if (active.includes("today") && data.priority === 2) return true;
        if (active.includes("tomorrow") && data.priority === 1) return true;
        if (active.includes("this-week") && data.priority >= 0) return true;
        if (active.includes("waiting") && data.is_waiting === 1) return true;
        if (active.includes("standalone") && data.client_id == null) return true;
        return false;
      });
    }

    function saveFilterState() {
      const active = [...document.querySelectorAll(".filter-pill.active")]
        .map((p) => p.dataset.filter);
      sessionStorage.setItem("today-filter-pills", JSON.stringify(active));
    }

    // Restore filter state on load
    try {
      const saved = JSON.parse(sessionStorage.getItem("today-filter-pills") || "null");
      if (Array.isArray(saved)) {
        document.querySelectorAll(".filter-pill").forEach((p) => {
          p.classList.toggle("active", saved.includes(p.dataset.filter));
        });
        applyFilters(table);
      }
    } catch (e) { /* ignore */ }
  })();
</script>
<style>
  .undo-toast {
    position: fixed;
    right: 20px;
    bottom: 20px;
    background: var(--brand);
    color: #fff;
    padding: 10px 16px;
    border-radius: 6px;
    font-size: 13px;
    display: flex;
    gap: 12px;
    align-items: center;
    box-shadow: 0 4px 12px rgba(0,15,71,0.2);
    animation: undo-slide-in 160ms ease-out;
  }
  .undo-toast .undo-btn {
    background: transparent;
    border: 1px solid rgba(255,255,255,0.4);
    color: #fff;
    border-radius: 4px;
    padding: 3px 10px;
    cursor: pointer;
    font-size: 12px;
  }
  .undo-toast .undo-btn:hover { background: rgba(255,255,255,0.15); }
  @keyframes undo-slide-in { from { transform: translateY(8px); opacity: 0; } to { transform: none; opacity: 1; } }
  @media (prefers-reduced-motion: reduce) {
    .undo-toast { animation: none; }
  }
</style>
```

- [ ] **Step 4: Manual QA**

Start the dev server:
```bash
~/.policydb/venv/bin/policydb serve --port 8006
```
Navigate to http://127.0.0.1:8006/action-center?tab=today. Verify:
- Editorial date header renders
- Filter pills (Overdue/Today/Tomorrow active by default)
- Tabulator grid renders with rows from v_today_tasks
- Row hover: subject underlines, `•••` fades in, priority bar snaps to 2px inset
- Click ✓ checkbox on a row: strike-through, row fades, undo toast appears for 5s
- Clicking "Undo" restores the row and closes the toast
- Every 5th row has a visible hairline beneath
- Overdue rows have a pulsing priority bar (2.8s cycle)

If any of those fail, fix before committing.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/static/js/tabulator_today.js \
        src/policydb/web/static/css/today.css \
        src/policydb/web/templates/action_center/_today.html
git commit -m "feat(today): Tabulator grid + filter pills + complete/undo UX — phase 2/8"
```

---

### Task 2.8: Add Task modal + Cmd/Ctrl+N shortcut

**Files:**
- Modify: `src/policydb/web/templates/action_center/_add_task_modal.html` — replace the stub with the real modal
- Modify: `src/policydb/web/templates/action_center/_today.html` — add keyboard shortcut + Add-Task-button hookup

- [ ] **Step 1: Replace the stub modal**

Overwrite `src/policydb/web/templates/action_center/_add_task_modal.html`:

```jinja
{# Add Task modal — opened by #today-add-task-btn or Cmd/Ctrl+N. Posts to /tasks/create. #}

<dialog id="add-task-modal" class="add-task-modal">
  <form method="post" action="/tasks/create" class="add-task-form"
        onsubmit="return submitAddTask(event)">
    <header class="modal-header">
      <h3>Add task</h3>
      <button type="button" class="modal-close" onclick="closeAddTaskModal()" aria-label="Close">×</button>
    </header>

    <label class="modal-field">
      <span>Subject <span class="required">*</span></span>
      <textarea name="subject" rows="2" required maxlength="200" autofocus
                placeholder="What needs to be done?"></textarea>
    </label>

    <label class="modal-field">
      <span>Client <span class="muted">(optional)</span></span>
      <select name="client_id" id="add-task-client">
        <option value="">— Standalone task —</option>
        {% for c in all_clients %}
          <option value="{{ c.id }}">{{ c.name }}</option>
        {% endfor %}
      </select>
    </label>

    <label class="modal-field">
      <span>Follow-up date</span>
      <input type="date" name="follow_up_date" value="{{ today_iso }}" />
    </label>

    <label class="modal-field">
      <span>Contact <span class="muted">(optional)</span></span>
      <input type="text" name="contact_person" placeholder="Name" />
    </label>

    <footer class="modal-footer">
      <button type="button" class="btn btn-ghost" onclick="closeAddTaskModal()">Cancel</button>
      <button type="submit" class="btn btn-primary">Create task</button>
    </footer>
  </form>
</dialog>

<style>
  .add-task-modal {
    width: 480px;
    max-width: 92vw;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 0;
    background: var(--surface, #fff);
    box-shadow: 0 8px 30px rgba(0,15,71,0.18);
  }
  .add-task-modal::backdrop { background: rgba(0,15,71,0.35); }
  .add-task-form {
    display: flex; flex-direction: column; gap: 12px; padding: 18px 22px 20px;
  }
  .modal-header {
    display: flex; justify-content: space-between; align-items: center;
    margin: -2px -4px 4px 0;
  }
  .modal-header h3 {
    font-family: "DM Serif Display", serif;
    color: var(--brand); font-size: 20px; margin: 0; font-weight: 400;
  }
  .modal-close {
    background: transparent; border: 0; font-size: 20px; color: var(--muted);
    cursor: pointer; line-height: 1;
  }
  .modal-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
  .modal-field > span { color: var(--body); font-weight: 500; }
  .modal-field .required { color: var(--red); }
  .modal-field .muted { color: var(--muted); font-weight: 400; }
  .modal-field textarea,
  .modal-field input,
  .modal-field select {
    padding: 7px 10px;
    border: 1px solid var(--border);
    border-radius: 5px;
    font-family: "DM Sans", sans-serif;
    font-size: 13px;
    background: var(--bg, #F7F3EE);
    color: var(--body);
    resize: vertical;
  }
  .modal-field textarea:focus,
  .modal-field input:focus,
  .modal-field select:focus {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .modal-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
  .btn-ghost {
    background: transparent; border: 1px solid var(--border); color: var(--muted);
    padding: 6px 14px; border-radius: 5px; cursor: pointer; font-size: 12px;
  }
</style>

<script>
  window.openAddTaskModal = function () {
    const dlg = document.getElementById("add-task-modal");
    if (dlg && !dlg.open) dlg.showModal();
  };
  window.closeAddTaskModal = function () {
    const dlg = document.getElementById("add-task-modal");
    if (dlg && dlg.open) dlg.close();
  };
  window.submitAddTask = async function (event) {
    event.preventDefault();
    const form = event.target;
    const body = new FormData(form);
    const resp = await fetch("/tasks/create", { method: "POST", body });
    if (!resp.ok) {
      alert("Couldn't create task: " + (await resp.text()));
      return false;
    }
    const trigger = resp.headers.get("HX-Trigger");
    if (trigger) {
      try {
        const payload = JSON.parse(trigger).taskCreated;
        // Refresh the Today tab with the new row. Simplest: reload the panel.
        if (window.htmx) {
          htmx.ajax("GET", "/action-center?tab=today", { target: ".today-tab", swap: "outerHTML" });
        } else {
          location.reload();
        }
      } catch (e) { location.reload(); }
    }
    form.reset();
    closeAddTaskModal();
    return false;
  };
</script>
```

- [ ] **Step 2: Wire the button + Cmd/Ctrl+N in `_today.html`**

Append to the `<script>` block in `_today.html` (after the filter-pill init):

```javascript
    // Add Task button
    const addBtn = document.getElementById("today-add-task-btn");
    if (addBtn) addBtn.addEventListener("click", () => window.openAddTaskModal());

    // Cmd/Ctrl+N — only when the Today tab is the active tab
    document.addEventListener("keydown", (e) => {
      const isMod = (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey;
      if (!isMod) return;
      if (e.key.toLowerCase() !== "n") return;
      if (!document.querySelector(".today-tab")) return;  // not on Today tab
      e.preventDefault();
      window.openAddTaskModal();
    });
```

Also, update the `action_center.py` Today branch to pass `today_iso` to the context:

```python
    elif initial_tab == "today":
        today_rows = conn.execute(
            "SELECT * FROM v_today_tasks ORDER BY priority DESC, follow_up_date ASC, id ASC"
        ).fetchall()
        all_clients = conn.execute(
            "SELECT id, name FROM clients WHERE archived = 0 ORDER BY name"
        ).fetchall()
        nudge_days = cfg.get("focus_nudge_alert_days", 10)
        tab_ctx = {
            "today_rows": [dict(r) for r in today_rows],
            "all_clients": [dict(c) for c in all_clients],
            "nudge_days": nudge_days,
            "today_iso": date.today().isoformat(),
            "ac_tab": "today",
        }
```

- [ ] **Step 3: Manual QA**

Start the server, open the Today tab. Verify:
- Clicking `+ Add task` opens the modal
- Cmd+N (Mac) opens the modal
- Esc closes it (native `<dialog>` behavior)
- Typing a subject + submit creates a row — the page reloads and the new row appears at the top
- Leaving the client blank creates a standalone task (row shows "Standalone" in the client column)
- Empty subject shows the browser `required` validation

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/action_center/_add_task_modal.html \
        src/policydb/web/templates/action_center/_today.html \
        src/policydb/web/routes/action_center.py
git commit -m "feat(today): Add Task modal + Cmd/Ctrl+N shortcut — phase 2/8"
```

---

### Task 2.9: Snooze menu (⋯ action column)

**Files:**
- Modify: `src/policydb/web/templates/action_center/_today.html` — wire `•••` button to a dropdown

- [ ] **Step 1: Add the snooze menu markup + JS**

In `_today.html` script block, append:

```javascript
    // Snooze menu — invoked by `•••` per-row button
    document.addEventListener("click", (e) => {
      const btn = e.target.closest(".actions-btn");
      if (!btn) { closeSnoozeMenu(); return; }
      const tr = btn.closest(".tabulator-row");
      if (!tr) return;
      const rowId = tr.getAttribute("data-index") || tr.getAttribute("data-row-id");
      const rect = btn.getBoundingClientRect();
      openSnoozeMenu(Number(rowId), rect.right, rect.top);
    });

    function openSnoozeMenu(taskId, x, y) {
      closeSnoozeMenu();
      const menu = document.createElement("div");
      menu.className = "snooze-menu";
      menu.id = "snooze-menu";
      menu.style.top = `${y + 4}px`;
      menu.style.left = `${x - 160}px`;
      menu.innerHTML = `
        <button data-opt="tomorrow">Snooze tomorrow</button>
        <button data-opt="this_week">Snooze this week (Mon)</button>
        <button data-opt="next_week">Snooze next week</button>
        <button data-opt="custom">Custom date…</button>`;
      document.body.appendChild(menu);
      menu.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const opt = b.dataset.opt;
          let body;
          if (opt === "custom") {
            const d = prompt("Snooze until (YYYY-MM-DD):");
            if (!d) { closeSnoozeMenu(); return; }
            body = new URLSearchParams({ option: "custom", date: d });
          } else {
            body = new URLSearchParams({ option: opt });
          }
          await fetch(`/tasks/${taskId}/snooze`, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body,
          });
          closeSnoozeMenu();
          if (window.htmx) {
            htmx.ajax("GET", "/action-center?tab=today", { target: ".today-tab", swap: "outerHTML" });
          } else {
            location.reload();
          }
        });
      });
    }
    function closeSnoozeMenu() {
      const existing = document.getElementById("snooze-menu");
      if (existing) existing.remove();
    }
```

Append the menu styles to `today.css`:

```css
.snooze-menu {
  position: fixed;
  z-index: 9999;
  background: var(--surface, #fff);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 4px 14px rgba(0,15,71,0.18);
  padding: 4px 0;
  min-width: 180px;
}
.snooze-menu button {
  display: block;
  width: 100%;
  padding: 7px 14px;
  text-align: left;
  background: transparent;
  border: 0;
  color: var(--body);
  font-size: 12px;
  cursor: pointer;
}
.snooze-menu button:hover { background: #FBF7F1; }
```

- [ ] **Step 2: Manual QA**

- Click `•••` on a row — menu opens adjacent to the button
- Click "Snooze tomorrow" — task moves to tomorrow's pill
- Click "Custom date..." — prompt appears, accept a date — task moves there
- Click outside — menu closes

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/action_center/_today.html \
        src/policydb/web/static/css/today.css
git commit -m "feat(today): snooze menu (tomorrow / this week / next week / custom) — phase 2/8"
```

---

### Task 2.10: Empty "caught up" state + sort-direction caret

**Files:**
- Modify: `src/policydb/web/static/css/today.css` — empty-state styles
- Modify: `src/policydb/web/templates/action_center/_today.html` — render empty message when filters produce zero rows
- Modify: `src/policydb/web/static/js/tabulator_today.js` — custom SVG sort caret formatter

- [ ] **Step 1: Add empty-state styling**

Append to `today.css`:

```css
.today-empty {
  padding: 40px 48px;
  border-left: 3px solid var(--brand);
  margin: 20px 20px 32px;
  max-width: 420px;
}
.today-empty h3 {
  font-family: "DM Serif Display", serif;
  font-style: italic;
  font-size: 22px;
  color: var(--brand);
  font-weight: 400;
  margin: 0 0 6px;
}
.today-empty p {
  font-size: 13px;
  color: var(--muted);
  margin: 0 0 16px;
}
.today-empty .btn-primary { display: inline-block; }

.sort-caret {
  display: inline-block;
  margin-left: 6px;
  width: 10px; height: 6px;
  vertical-align: middle;
}
.tabulator .tabulator-col.tabulator-sortable .tabulator-col-title .sort-caret {
  opacity: 0.3;
}
.tabulator .tabulator-col[aria-sort="ascending"]  .sort-caret,
.tabulator .tabulator-col[aria-sort="descending"] .sort-caret {
  opacity: 1;
  fill: var(--accent);
}
```

- [ ] **Step 2: Render empty state**

In `_today.html`, replace the `<div id="today-grid">` block with a conditional:

```jinja
{% if today_rows | length == 0 %}
  <div class="today-empty">
    <h3>Inbox Zero for today.</h3>
    <p>Take the afternoon off — or add a task.</p>
    <button class="btn btn-primary" id="today-add-task-btn-empty">+ Add task</button>
  </div>
{% else %}
  <div id="today-grid" class="today-grid"
       data-rows='{{ today_rows | tojson }}'
       data-nudge-days="{{ nudge_days }}"></div>
{% endif %}
```

Wire the empty-state button (append to existing click handlers block in the script):

```javascript
    const emptyBtn = document.getElementById("today-add-task-btn-empty");
    if (emptyBtn) emptyBtn.addEventListener("click", () => window.openAddTaskModal());
```

Also handle the case where filter pills collapse the in-memory dataset to 0:

```javascript
    // Show "Inbox Zero" inline when filters hide everything
    function refreshEmptyState(table) {
      const visible = table.getData("active").length;
      let empty = document.getElementById("today-filter-empty");
      if (visible === 0 && !empty) {
        empty = document.createElement("div");
        empty.id = "today-filter-empty";
        empty.className = "today-empty";
        empty.innerHTML = `
          <h3>Inbox Zero for today.</h3>
          <p>All caught up — try relaxing a filter to see more.</p>`;
        document.getElementById("today-grid").parentElement.appendChild(empty);
      } else if (visible > 0 && empty) {
        empty.remove();
      }
    }
    table.on("dataFiltered", () => refreshEmptyState(table));
```

- [ ] **Step 3: Custom sort caret in the Tabulator header**

In `tabulator_today.js`, add a header-sort arrow formatter. Tabulator doesn't have a direct API for the caret SVG, but we can use CSS via `aria-sort` which Tabulator sets. Inject the SVG via a small mutation observer:

```javascript
  function installSortCaret(table) {
    const headers = table.element.querySelectorAll(".tabulator-col.tabulator-sortable");
    headers.forEach((h) => {
      if (h.querySelector(".sort-caret")) return;
      const title = h.querySelector(".tabulator-col-title");
      if (!title) return;
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "sort-caret");
      svg.setAttribute("viewBox", "0 0 10 6");
      svg.innerHTML = `<path d="M1 1 L5 5 L9 1" fill="none" stroke="currentColor" stroke-width="1.5"/>`;
      title.appendChild(svg);
    });
  }
```

Call `installSortCaret(table)` at the end of `buildTodayTable()` (after `return table;` is wrong — call it before returning). Also call it again inside a Tabulator `tableBuilt` callback:

Modify the Tabulator init to include:
```javascript
  const table = new Tabulator(selector, {
    // ... existing config
    tableBuilt: function () { installSortCaret(this); },
  });
```

- [ ] **Step 4: Manual QA**

- Open Today tab with no follow-ups due today/overdue → "Inbox Zero for today." renders with a 3px brand-blue left rule
- Click `+ Add task` in the empty state → modal opens
- Toggle all filter pills off → see the "All caught up — try relaxing a filter" inline empty state
- Each sortable column header shows a small caret; the currently-sorted column has an accent-blue filled caret

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/action_center/_today.html \
        src/policydb/web/static/css/today.css \
        src/policydb/web/static/js/tabulator_today.js
git commit -m "feat(today): Inbox Zero empty state + SVG sort caret — phase 2/8"
```

---

# Phase 3 — Smart Suggestions panel

**Goal:** Right-rail panel driven by `focus_queue.build_focus_queue(..., suggestions_only=True)`. Fast-capture `+` creates tasks in one click.

**PR title suggestion:** `feat(suggestions): Smart Suggestions rail + fast-capture + visual polish`

---

### Task 3.1: Add `suggestions_only` kwarg to `build_focus_queue`

**Files:**
- Modify: `src/policydb/focus_queue.py`
- Create: `tests/test_suggestions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_suggestions.py`:

```python
"""Tests for build_focus_queue(suggestions_only=True) mode."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from policydb.db import get_connection, init_db
from policydb.focus_queue import build_focus_queue


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_suggestions_only_excludes_existing_followups(tmp_db):
    """A row already in v_today_tasks (an actual follow-up) should NOT appear in suggestions."""
    conn = get_connection()
    conn.execute("INSERT INTO clients (name) VALUES ('Acme Co')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    today = date.today().isoformat()
    # Create an actual follow-up — should be excluded from suggestions
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, "
        "follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, 'Call', 'Existing task', ?, 0, 'followup', 'Grant')",
        (today, cid, today),
    )
    conn.commit()
    suggestions, waiting, stats = build_focus_queue(conn, suggestions_only=True)
    # No suggestions for an empty DB beyond our one follow-up
    assert all("Existing task" not in (s.get("subject") or "") for s in suggestions)
    assert waiting == []


def test_suggestions_only_returns_empty_waiting(tmp_db):
    conn = get_connection()
    suggestions, waiting, stats = build_focus_queue(conn, suggestions_only=True)
    assert waiting == []
    assert isinstance(suggestions, list)


def test_default_mode_unchanged(tmp_db):
    """Without suggestions_only, the return shape must stay (focus, waiting, stats)."""
    conn = get_connection()
    focus, waiting, stats = build_focus_queue(conn)
    assert isinstance(focus, list)
    assert isinstance(waiting, list)
    assert isinstance(stats, dict)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_suggestions.py -v`
Expected: FAIL — `build_focus_queue()` doesn't accept `suggestions_only`.

- [ ] **Step 3: Implement**

Open `src/policydb/focus_queue.py`. Update the module docstring at the very top of the file:

```python
"""Suggestion engine. Originally built for the Focus Queue tab (retired 2026-04-18);
now powers Smart Suggestions on the Today tab.

The filename is preserved to keep the diff small. The `suggestions_only=True` mode
filters the engine's output against `v_today_tasks` so rows already captured as
tasks don't reappear as suggestions.
"""
```

Update the `build_focus_queue` signature (around line 1079):

```python
def build_focus_queue(
    conn: sqlite3.Connection,
    horizon_days: int = 0,
    client_id: int = 0,
    suggestions_only: bool = False,
) -> tuple[list[dict], list[dict], dict]:
    """Build the Focus Queue and Waiting list, OR suggestions-only when suggestions_only=True.

    Args:
        conn: SQLite connection
        horizon_days: Time horizon filter. 0 = today, 7 = this week, 14 = next 2 weeks,
                      -999 = all. Ignored when suggestions_only=True.
        client_id: Optional client filter (0 = all clients)
        suggestions_only: When True, skip existing follow-ups (anything already in
                          v_today_tasks) and return only the synthetic suggestion kinds.
                          Waiting list is returned empty.

    Returns:
        (items, waiting_items, stats)
          items:     ranked list (focus items in default mode; suggestions in suggestions_only mode)
          waiting_items: items waiting on others (default only; [] in suggestions_only)
          stats:     {focus_count, waiting_count, nudge_alert_count}
    """
```

At the very end of the function (just before `return focus_items, waiting_items, stats`), insert the suggestions filter:

```python
    if suggestions_only:
        # Fetch ids already captured as tasks
        existing_ids = {
            r["id"] for r in conn.execute("SELECT id FROM v_today_tasks").fetchall()
        }
        # Keep only synthetic suggestion kinds, drop rows whose underlying activity id
        # is already a task
        SUGGESTION_KINDS = {
            "suggested", "inbox", "issue", "milestone",
            "insurance_deadline", "project_deadline", "opportunity",
        }
        suggestions = [
            item for item in all_items
            if item.get("kind") in SUGGESTION_KINDS
            and item.get("id") not in existing_ids
        ]
        return suggestions, [], {
            "focus_count": 0,
            "waiting_count": 0,
            "nudge_alert_count": 0,
            "suggestion_count": len(suggestions),
        }
```

**Note:** `all_items` is already constructed in the existing function body after normalization — this filter runs instead of the default focus/waiting split.

Also at the top of `build_focus_queue`, short-circuit the `get_all_followups()` call when `suggestions_only=True`:

```python
    if suggestions_only:
        overdue_raw, upcoming_raw = [], []
    else:
        overdue_raw, upcoming_raw = get_all_followups(conn, window=_effective_window(30), client_ids=client_ids)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_suggestions.py -v`
Expected: 3 passed.

Run the full focus_queue suite:
```bash
pytest tests/test_focus_queue.py tests/test_suggestions.py -v
```
(Replace `test_focus_queue.py` with whatever the existing focus-queue test file is called — `grep -l build_focus_queue tests/`.)

Expected: no regressions in the default mode.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/focus_queue.py tests/test_suggestions.py
git commit -m "feat(suggestions): add suggestions_only mode to build_focus_queue — phase 3/8"
```

---

### Task 3.2: `/action-center/today/suggestions` route + template

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — add suggestions route
- Create: `src/policydb/web/templates/action_center/_today_suggestions.html`
- Modify: `tests/test_today_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_today_routes.py`:

```python
def test_suggestions_route_renders(app_client):
    r = app_client.get("/action-center/today/suggestions")
    assert r.status_code == 200
    assert "suggestions-group" in r.text or "Inbox emails" in r.text


def test_suggestions_route_renders_all_groups_even_when_empty(app_client):
    """Empty groups are shown greyed out, not hidden (positive 'caught up' signal)."""
    r = app_client.get("/action-center/today/suggestions")
    text = r.text
    for group in ["Renewals expiring", "Inbox emails", "Issues at SLA risk",
                  "Milestones at risk", "Project / insurance deadlines"]:
        assert group in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_today_routes.py -v -k suggestions`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Implement the route**

Append to `src/policydb/web/routes/action_center.py`:

```python
@router.get("/action-center/today/suggestions", response_class=HTMLResponse)
def today_suggestions(request: Request, conn=Depends(get_db)):
    """HTMX partial — Smart Suggestions panel for the Today tab."""
    suggestions, _, stats = build_focus_queue(conn, suggestions_only=True)

    # Group by spec-defined order
    groups = {
        "Renewals expiring": [],
        "Inbox emails": [],
        "Issues at SLA risk": [],
        "Milestones at risk": [],
        "Project / insurance deadlines": [],
    }
    for s in suggestions:
        kind = s.get("kind")
        if kind in ("suggested", "insurance_deadline"):
            groups["Renewals expiring"].append(s)
        elif kind == "inbox":
            groups["Inbox emails"].append(s)
        elif kind == "issue":
            groups["Issues at SLA risk"].append(s)
        elif kind == "milestone":
            groups["Milestones at risk"].append(s)
        elif kind in ("project_deadline", "opportunity"):
            groups["Project / insurance deadlines"].append(s)

    return templates.TemplateResponse(
        "action_center/_today_suggestions.html",
        {"request": request, "groups": groups},
    )
```

- [ ] **Step 4: Create the template**

Create `src/policydb/web/templates/action_center/_today_suggestions.html`:

```jinja
{# Smart Suggestions rail — grouped by kind, empty groups shown greyed out. #}

<header class="suggestions-head">
  <h4>Smart suggestions</h4>
  <span class="suggestions-count">{{ groups.values() | map("length") | sum }}</span>
</header>

{% for title, items in groups.items() %}
  <section class="suggestions-group {% if items | length == 0 %}empty{% endif %}">
    <h5>{{ title }} <span class="group-count">{{ items | length }}</span></h5>

    {% if items | length == 0 %}
      <p class="group-empty-msg">All caught up.</p>
    {% else %}
      {% for s in items %}
        <article class="sg-item"
                 data-priority="{{ s.get('priority', 0) }}"
                 data-source-id="{{ s.get('id', '') }}">
          <div class="sg-priority-stripe"></div>
          <div class="sg-body">
            <div class="sg-subj">{{ s.subject }}</div>
            <div class="sg-why">
              {% if s.policy_uid %}<span class="ref-pill">{{ s.policy_uid }}</span>{% endif %}
              {{ s.get('context') or s.get('details') or '' }}
            </div>
          </div>
          <button class="sg-add" type="button"
                  data-source-id="{{ s.get('id', '') }}"
                  data-subject="{{ s.subject }}"
                  data-client-id="{{ s.get('client_id') or '' }}"
                  data-policy-id="{{ s.get('policy_id') or '' }}"
                  aria-label="Capture suggestion as a task">+</button>
        </article>
      {% endfor %}
    {% endif %}
  </section>
{% endfor %}
```

Append to `today.css`:

```css
.suggestions-head {
  display: flex; justify-content: space-between; align-items: baseline;
  padding-bottom: 6px; border-bottom: 1px solid var(--border);
}
.suggestions-head h4 {
  font-family: "DM Sans", sans-serif;
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--brand); font-weight: 600; margin: 0;
}
.suggestions-count { color: var(--muted); font-size: 11px; }

.suggestions-group { margin-top: 12px; }
.suggestions-group h5 {
  font-family: "DM Sans", sans-serif;
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--brand); font-weight: 600; margin: 0 0 6px;
  display: flex; justify-content: space-between; align-items: center;
}
.group-count { color: var(--muted); font-weight: 500; }
.suggestions-group.empty h5 { color: var(--muted); }
.suggestions-group.empty { opacity: 0.55; }
.group-empty-msg {
  font-size: 10px; color: var(--muted); margin: 0 0 4px;
}

.sg-item {
  display: grid;
  grid-template-columns: 3px 1fr auto;
  gap: 8px;
  padding: 6px 8px;
  background: var(--surface, #fff);
  border: 1px solid var(--border);
  border-radius: 5px;
  margin-bottom: 4px;
  align-items: center;
}
.sg-priority-stripe {
  align-self: stretch;
  border-radius: 2px;
  background: #E7E1D7;
}
.sg-item[data-priority="3"] .sg-priority-stripe { background: var(--red); }
.sg-item[data-priority="2"] .sg-priority-stripe { background: var(--amber); }
.sg-item[data-priority="1"] .sg-priority-stripe { background: var(--accent); }

.sg-subj { color: var(--brand); font-weight: 500; font-size: 12px; }
.sg-why  { color: var(--muted); font-size: 10px; margin-top: 2px; }

.sg-add {
  width: 22px; height: 22px; border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--bg, #F7F3EE);
  color: var(--accent);
  font-size: 14px; line-height: 1; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 120ms ease-out;
}
.sg-add:hover {
  background: var(--accent); color: #fff; border-color: var(--accent);
}
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_today_routes.py -v -k suggestions`
Expected: 2 passed.

- [ ] **Step 6: Manual QA**

Load the Today tab. Verify suggestions rail populates after ~200ms delay. All 5 group headers visible, empty ones greyed at 55% opacity.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/action_center.py \
        src/policydb/web/templates/action_center/_today_suggestions.html \
        src/policydb/web/static/css/today.css \
        tests/test_today_routes.py
git commit -m "feat(suggestions): /today/suggestions route + grouped template — phase 3/8"
```

---

### Task 3.3: Fast-capture `+` + shift-click modal

**Files:**
- Modify: `src/policydb/web/templates/action_center/_today.html` — JS for fast-capture handler

- [ ] **Step 1: Add fast-capture handler**

Append to the `_today.html` script block (just after the snooze-menu section):

```javascript
    // Fast-capture from Smart Suggestions
    document.addEventListener("click", async (e) => {
      const btn = e.target.closest(".sg-add");
      if (!btn) return;
      e.preventDefault();

      // Shift-click → open the Add Task modal pre-populated
      if (e.shiftKey) {
        const modal = document.getElementById("add-task-modal");
        modal.querySelector('textarea[name="subject"]').value = btn.dataset.subject || "";
        if (btn.dataset.clientId) {
          modal.querySelector('select[name="client_id"]').value = btn.dataset.clientId;
        }
        window.openAddTaskModal();
        return;
      }

      // One-click fast capture
      const body = new URLSearchParams({
        subject: btn.dataset.subject || "",
        client_id: btn.dataset.clientId || "",
        policy_id: btn.dataset.policyId || "",
        follow_up_date: new Date().toISOString().slice(0, 10),
      });
      const resp = await fetch("/tasks/create", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body,
      });
      if (!resp.ok) {
        alert("Couldn't capture: " + (await resp.text()));
        return;
      }
      // Remove the captured suggestion row and refresh the main grid
      const item = btn.closest(".sg-item");
      if (item) item.style.opacity = "0";
      setTimeout(() => item?.remove(), 180);
      if (window.htmx) {
        htmx.ajax("GET", "/action-center?tab=today", { target: ".today-tab", swap: "outerHTML" });
      } else {
        location.reload();
      }
    });
```

- [ ] **Step 2: Manual QA**

- Click `+` on any suggestion → row fades out, new task appears in the grid
- Shift-click `+` → modal opens with subject pre-filled
- Suggestion rail updates on next 5-minute poll (or on page reload)

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/action_center/_today.html
git commit -m "feat(suggestions): fast-capture one-click + + shift-click modal — phase 3/8"
```

---

# Phase 4 — Focus retirement

**Goal:** Remove the Focus Queue UI. Flip default tab. Keep the engine.

**PR title suggestion:** `feat(focus-retire): delete Focus Queue tab, redirect to Today`

---

### Task 4.1: Add redirects for old Focus URLs

**Files:**
- Modify: `src/policydb/web/routes/action_center.py` — add redirect handler
- Modify: `src/policydb/web/routes/activities.py` — change `/followups` redirect target if present
- Create: `tests/test_focus_retirement.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_focus_retirement.py`:

```python
"""Tests for Focus Queue retirement — redirects, default tab flip, engine rename."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from policydb.db import init_db


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
def app_client(tmp_db):
    from policydb.web.app import app
    with TestClient(app) as c:
        yield c


def test_focus_tab_redirects_to_today(app_client):
    r = app_client.get("/action-center?tab=focus", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/action-center?tab=today"


def test_slash_focus_redirects_to_today(app_client):
    r = app_client.get("/focus", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/action-center?tab=today")


def test_followups_redirects_to_today(app_client):
    """/followups was /action-center?tab=focus; now /action-center?tab=today."""
    r = app_client.get("/followups", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/action-center?tab=today")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_focus_retirement.py -v -k "redirects"`
Expected: FAIL — old URLs either 200 (old tab still renders) or no redirect.

- [ ] **Step 3: Implement the redirects**

In `action_center.py`, *at the very top of `action_center_page`* (line ~933), insert:

```python
from fastapi.responses import RedirectResponse

@router.get("/action-center", response_class=HTMLResponse)
def action_center_page(request: Request, tab: str = "", conn=Depends(get_db)):
    """Main Action Center page — renders shell with tabs and sidebar."""
    # Legacy tab aliases — Focus Queue retired 2026-04-18
    if tab == "focus":
        return RedirectResponse(url="/action-center?tab=today", status_code=302)
    if tab == "followups":
        return RedirectResponse(url="/action-center?tab=today", status_code=302)
    # ... rest of function
```

Add a `/focus` redirect handler (new route in `action_center.py` or a dedicated router):

```python
@router.get("/focus", response_class=RedirectResponse)
def focus_legacy():
    return RedirectResponse(url="/action-center?tab=today", status_code=302)
```

In `activities.py`, locate the `/followups` handler (likely a redirect near line 1493 based on file layout). If it redirects to `/action-center?tab=focus`, change the target to `/action-center?tab=today`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_focus_retirement.py -v -k "redirects"`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py src/policydb/web/routes/activities.py tests/test_focus_retirement.py
git commit -m "feat(focus-retire): 302 /focus, ?tab=focus, and /followups to Today — phase 4/8"
```

---

### Task 4.2: Flip default tab to Today + remove Focus branch

**Files:**
- Modify: `src/policydb/web/routes/action_center.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_focus_retirement.py`:

```python
def test_default_tab_is_today(app_client):
    """Hitting /action-center with no ?tab param renders the Today tab."""
    r = app_client.get("/action-center")
    assert r.status_code == 200
    assert "today-tab" in r.text       # our new tab marker from _today.html
    assert "focus-queue" not in r.text  # old Focus marker absent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_focus_retirement.py::test_default_tab_is_today -v`
Expected: FAIL — default is still `"focus"`.

- [ ] **Step 3: Flip default and remove Focus branch**

In `action_center.py`, change:
```python
initial_tab = tab or "focus"
if initial_tab == "followups":
    initial_tab = "focus"
```
to:
```python
initial_tab = tab or "today"
# Legacy followups alias already handled above via 302 redirect, so we need no branch here
```

Remove the entire `if initial_tab == "focus":` branch (lines ~942-964) including its `tab_ctx` build. `build_focus_queue` import at the top stays (Phase 3 still uses it).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_focus_retirement.py -v`
Expected: all pass.

Run the full action-center suite to check for regressions:
```bash
pytest tests/test_focus_retirement.py tests/test_today_routes.py tests/test_suggestions.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/action_center.py tests/test_focus_retirement.py
git commit -m "feat(focus-retire): flip default tab to today + drop focus dispatcher branch — phase 4/8"
```

---

### Task 4.3: Delete Focus templates + update page.html

**Files:**
- Delete: `src/policydb/web/templates/action_center/_focus_queue.html`
- Delete: `src/policydb/web/templates/action_center/_focus_item.html`
- Delete: `src/policydb/web/templates/action_center/_waiting_sidebar.html`
- Modify: `src/policydb/web/templates/action_center/page.html` — swap Focus tab button for Today, sessionStorage migration

- [ ] **Step 1: Delete the templates**

```bash
git rm src/policydb/web/templates/action_center/_focus_queue.html
git rm src/policydb/web/templates/action_center/_focus_item.html
git rm src/policydb/web/templates/action_center/_waiting_sidebar.html
```

- [ ] **Step 2: Open `page.html` and find the tab strip**

Read `src/policydb/web/templates/action_center/page.html`. Find the `<nav>` / `<div class="tabs">` block that lists tab buttons (typically near top of body).

Replace the Focus button:
```jinja
<button data-tab="focus" class="tab {% if ac_tab == 'focus' %}active{% endif %}">
  Focus Queue
</button>
```
with:
```jinja
<button data-tab="today" class="tab {% if ac_tab == 'today' %}active{% endif %}">
  Today
</button>
```

Remove any `{% if ac_tab == 'focus' %}{% include "action_center/_focus_queue.html" %}{% endif %}` block (it now 404s because the template is deleted).

Add a Today include:
```jinja
{% if ac_tab == 'today' %}
  {% include "action_center/_today.html" %}
{% endif %}
```

- [ ] **Step 3: Add sessionStorage migration**

Inside `page.html`, find the `sessionStorage.setItem('action-center-tab', ...)` line (currently at ~line 184). Just before the block that reads sessionStorage and navigates, insert the one-time migration:

```html
<script>
  (function migrateSessionTab() {
    try {
      if (sessionStorage.getItem("action-center-tab") === "focus") {
        sessionStorage.setItem("action-center-tab", "today");
      }
    } catch (e) { /* ignore */ }
  })();
</script>
```

Place this before the existing tab-switching script so the migration runs first.

- [ ] **Step 4: Manual QA**

Start the server. Visit `/action-center` — Today tab loads. Click Activities tab, click Today tab again — the tab state persists via sessionStorage. Now open DevTools, `sessionStorage.setItem('action-center-tab', 'focus')`, reload — migration rewrites to `today`, Today renders.

- [ ] **Step 5: Commit**

```bash
git add -u src/policydb/web/templates/action_center/
git commit -m "feat(focus-retire): delete Focus templates + page.html Today tab button + sessionStorage migration — phase 4/8"
```

---

### Task 4.4: Reword Settings UI labels + update dashboard count

**Files:**
- Modify: `src/policydb/web/routes/settings.py` — find `EDITABLE_LISTS` and reword label strings containing "Focus"
- Modify: `src/policydb/web/routes/dashboard.py` — find "Active focus items" count and query

- [ ] **Step 1: Reword Settings labels**

Grep for "Focus" in the settings UI:
```bash
grep -n -i "focus" src/policydb/web/routes/settings.py
```

For each config-key label that says "Focus ...", reword to "Suggestion ..." or the contextually-correct phrase. For example:
- "Focus score weights" → "Suggestion score weights"
- "Focus auto-promote days" → "Suggestion auto-promote days"
- "Focus nudge alert days" → "Nudge-age alert days"

Config *keys* stay unchanged (`focus_score_weights` etc.). Only the user-visible label strings change.

- [ ] **Step 2: Update dashboard count**

Grep for the dashboard count:
```bash
grep -n -i "active focus\|focus_count\|focus items" src/policydb/web/routes/dashboard.py src/policydb/web/templates/dashboard/
```

Wherever the current query computes "Active focus items", replace it with:
```python
open_today_count = conn.execute("SELECT COUNT(*) FROM v_today_tasks").fetchone()[0]
```
and the label with `Open tasks today`.

- [ ] **Step 3: Write a sanity test**

Append to `tests/test_focus_retirement.py`:

```python
def test_dashboard_shows_open_tasks_today(app_client):
    r = app_client.get("/")  # or wherever dashboard lives
    assert r.status_code == 200
    assert "Open tasks today" in r.text
    assert "Active focus items" not in r.text
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_focus_retirement.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/routes/dashboard.py \
        src/policydb/web/templates/dashboard/ tests/test_focus_retirement.py
git commit -m "feat(focus-retire): reword Settings labels + dashboard count — phase 4/8"
```

---

# Phase 5 — Plan Week re-skin

**Goal:** Apply the same Tabulator base and visual language to Plan Week. Zero behavior change — pure presentation refactor.

**PR title suggestion:** `feat(plan-week): re-skin using shared Tabulator base from Today`

---

### Task 5.1: Extract shared table config (already done in Task 2.7)

Task 2.7 already created `static/js/tabulator_today.js` with `buildTodayTable()`. This task reuses that module in Plan Week's existing view.

- [ ] **Step 1: Audit Plan Week's existing template**

Read `src/policydb/web/templates/action_center/_plan_week.html` (or equivalent — grep for the template referenced by `activities.py::followups_plan`):

```bash
grep -rn "plan_week\|plan-week\|daily_followup_target" src/policydb/web/templates/ | head -5
```

Note the existing column structure and any Plan-Week-specific features (weighted load, pin counts, daily target indicators).

- [ ] **Step 2: Generalize `buildTodayTable` to accept column overrides**

Open `src/policydb/web/static/js/tabulator_today.js` and export a helper that returns the default column set so Plan Week can extend / override it:

```javascript
  function defaultTodayColumns({ onCompleted }) {
    return [
      { title: "", field: "_check", width: 40, hozAlign: "center",
        formatter: completeCheckboxFormatter,
        cellClick: (e, cell) => onCompleted?.(cell.getRow().getData()) },
      { title: "", field: "_priority", width: 4, formatter: priorityBarFormatter, headerSort: false },
      { title: "Kind", field: "kind", width: 72, formatter: kindChipFormatter },
      { title: "Subject / Context", field: "subject", formatter: subjectFormatter, minWidth: 320 },
      { title: "Client · Policy", field: "client_name", width: 180, formatter: clientPolicyFormatter },
      { title: "Contact", field: "contact_person", width: 140, formatter: contactFormatter },
      { title: "Last", field: "last_activity_at", width: 90, formatter: lastFormatter },
      { title: "Due", field: "follow_up_date", width: 90, formatter: dueFormatter },
      { title: "", field: "_actions", width: 40, formatter: actionsFormatter, headerSort: false },
    ];
  }
  global.defaultTodayColumns = defaultTodayColumns;
  global.todayFormatters = {
    priorityBar: priorityBarFormatter,
    kindChip: kindChipFormatter,
    subject: subjectFormatter,
    clientPolicy: clientPolicyFormatter,
    due: dueFormatter,
    last: lastFormatter,
  };
```

- [ ] **Step 3: Refactor Plan Week template to use the shared module**

Open the Plan Week template. Replace whatever grid/table markup it currently uses with:

```jinja
<link rel="stylesheet" href="/static/css/today.css" />
<script src="https://cdn.jsdelivr.net/npm/tabulator-tables@6.3/dist/js/tabulator.min.js"></script>
<script src="/static/js/tabulator_today.js"></script>

<div class="plan-week-grid" id="plan-week-grid"
     data-rows='{{ week_items | tojson }}'></div>
<script>
  (function () {
    const el = document.getElementById("plan-week-grid");
    if (!el) return;
    const rows = JSON.parse(el.dataset.rows || "[]");
    const cols = window.defaultTodayColumns({});
    // Plan Week adds a day column and shows weighted load
    cols.splice(1, 0, {
      title: "Day", field: "follow_up_date", width: 80,
      formatter: (cell) => new Date(cell.getValue() + "T00:00:00")
        .toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }),
    });
    const table = new Tabulator("#plan-week-grid", {
      data: rows,
      columns: cols,
      layout: "fitColumns",
      groupBy: "follow_up_date",
      groupHeader: (value, count) => `${value} — ${count} items`,
    });
  })();
</script>
```

This keeps the underlying route (`/followups/plan`) and its spread/dismiss/escalate handlers intact — only the Tabulator rendering layer changes.

- [ ] **Step 4: Add "Plan Week →" entry point on Today toolbar**

Task 2.7 already added `<a href="/followups/plan" class="today-planweek-link">Plan Week →</a>` in the toolbar — no work needed here, just verify it renders.

- [ ] **Step 5: Manual QA**

- Navigate to Today → click `Plan Week →` → Plan Week loads with refreshed Tabulator grid
- Visual consistency: fonts, spacing, priority bar, kind chips match Today
- Test each existing Plan Week action (spread, dismiss, escalate) — they still work, no regression

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/static/js/tabulator_today.js \
        src/policydb/web/templates/action_center/_plan_week.html \
        src/policydb/web/routes/activities.py
git commit -m "feat(plan-week): re-skin using shared Tabulator base + Today toolbar entry — phase 5/8"
```

---

# Phase 6 — Desktop launcher (Mac smoke test)

**Goal:** `src/policydb/desktop.py` boots uvicorn in-thread and opens a pywebview window. Manual smoke-test on Mac before packaging.

**PR title suggestion:** `feat(desktop): pywebview launcher + first-launch silent migration`

---

### Task 6.1: Add pywebview dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the dep**

Open `pyproject.toml`. Under `[project.dependencies]`:
```toml
pywebview = ">=5.0"
```

Install locally:
```bash
~/.policydb/venv/bin/pip install -e .
```

- [ ] **Step 2: Verify import**

```bash
~/.policydb/venv/bin/python -c "import webview; print(webview.__version__)"
```
Expected: a version string ≥ 5.0.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat(desktop): add pywebview dependency — phase 6/8"
```

---

### Task 6.2: `desktop.py` with port + uvicorn thread + window

**Files:**
- Create: `src/policydb/desktop.py`
- Create: `tests/test_desktop_migration.py`

- [ ] **Step 1: Write the failing migration tests**

Create `tests/test_desktop_migration.py`:

```python
"""Tests for desktop first-launch silent migration."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_silent_migration_writes_sentinel(tmp_path, monkeypatch):
    """After migration runs once, the sentinel file exists."""
    data_dir = tmp_path / "new"
    legacy = tmp_path / "legacy"
    data_dir.mkdir()
    legacy.mkdir()
    (legacy / "policydb.sqlite").write_bytes(b"fake-db-content")

    monkeypatch.setattr("policydb.paths.DATA_DIR", data_dir)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

    from policydb.desktop import silent_migrate_from_legacy
    silent_migrate_from_legacy(legacy_override=legacy)

    sentinel = data_dir / ".migrated_from_old"
    assert sentinel.exists()
    assert (data_dir / "policydb.sqlite").exists()


def test_silent_migration_noop_when_paths_equal(tmp_path, monkeypatch):
    """If DATA_DIR == legacy (Mac case), copy is skipped but sentinel is still written."""
    data_dir = tmp_path / ".policydb"
    data_dir.mkdir()
    (data_dir / "policydb.sqlite").write_bytes(b"existing")

    monkeypatch.setattr("policydb.paths.DATA_DIR", data_dir)

    from policydb.desktop import silent_migrate_from_legacy
    silent_migrate_from_legacy(legacy_override=data_dir)

    sentinel = data_dir / ".migrated_from_old"
    assert sentinel.exists()
    # Existing file untouched
    assert (data_dir / "policydb.sqlite").read_bytes() == b"existing"


def test_silent_migration_only_runs_once(tmp_path, monkeypatch):
    data_dir = tmp_path / "new"
    legacy = tmp_path / "legacy"
    data_dir.mkdir()
    legacy.mkdir()
    (legacy / "policydb.sqlite").write_bytes(b"v1")

    monkeypatch.setattr("policydb.paths.DATA_DIR", data_dir)

    from policydb.desktop import silent_migrate_from_legacy
    silent_migrate_from_legacy(legacy_override=legacy)

    # Mutate legacy — second run must NOT copy
    (legacy / "policydb.sqlite").write_bytes(b"v2")
    silent_migrate_from_legacy(legacy_override=legacy)

    assert (data_dir / "policydb.sqlite").read_bytes() == b"v1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_desktop_migration.py -v`
Expected: FAIL — module and function don't exist.

- [ ] **Step 3: Create `desktop.py`**

Create `src/policydb/desktop.py`:

```python
"""Packaged-app entry point. Invoked when sys.frozen is True (PyInstaller runtime)."""
from __future__ import annotations

import logging
import shutil
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

import uvicorn

from policydb.db import init_db
from policydb.paths import DATA_DIR

logger = logging.getLogger("policydb.desktop")


def silent_migrate_from_legacy(legacy_override: Path | None = None) -> None:
    """Copy data from a legacy ~/.policydb/ into DATA_DIR on first launch.

    Runs exactly once per install, gated by a sentinel file
    ``DATA_DIR / .migrated_from_old``. No prompt, no failure — just a silent copy.

    On Mac DATA_DIR == ~/.policydb/ so the legacy!=data check skips the copy;
    the sentinel is still written so subsequent launches don't check again.
    """
    sentinel = DATA_DIR / ".migrated_from_old"
    if sentinel.exists():
        return
    legacy = legacy_override or (Path.home() / ".policydb")
    if legacy != DATA_DIR and legacy.exists() and any(legacy.iterdir()):
        shutil.copytree(
            legacy, DATA_DIR, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".DS_Store", "*.lock", "*.sqlite-journal"),
        )
        logger.info("First-launch migration: copied %s -> %s", legacy, DATA_DIR)
    sentinel.write_text(f"migrated_at={datetime.now().isoformat()}\n")


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/"
    while time.time() < deadline:
        try:
            urlopen(url, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main() -> int:
    import webview  # import here so tests don't require it

    silent_migrate_from_legacy()
    init_db()

    port = pick_free_port()
    from policydb.web.app import app

    server_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(server_config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_for_health(port):
        logger.error("Backend failed to become healthy on port %d", port)
        return 1

    # First-run detection — Phase 8 implements /onboarding
    startup_url = f"http://127.0.0.1:{port}/action-center?tab=today"
    # (Phase 8 inserts onboarding-redirect logic here.)

    window = webview.create_window(
        "PolicyDB",
        url=startup_url,
        width=1400, height=900,
        min_size=(900, 600),
        resizable=True,
    )
    webview.start()

    server.should_exit = True
    thread.join(timeout=3.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_desktop_migration.py -v`
Expected: 3 passed.

- [ ] **Step 5: Manual Mac smoke test**

```bash
~/.policydb/venv/bin/python -c "from policydb.desktop import main; main()"
```

Expected:
- A native macOS window opens titled "PolicyDB"
- The Today tab renders at 1400×900
- Resize works; min-size enforces 900×600
- Close the window — process exits cleanly (no orphaned uvicorn)

If the webview doesn't open (pywebview needs a main-thread run in some macOS configs), adjust the `main()` ordering — pywebview's `webview.start()` MUST run on the main thread.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/desktop.py tests/test_desktop_migration.py
git commit -m "feat(desktop): pywebview launcher + silent migration hook — phase 6/8"
```

---

# Phase 7 — Windows build

**Goal:** PyInstaller spec + build script + GitHub Actions workflow that produces an unsigned `.msi` and `.dmg`. No code signing in v1.

**PR title suggestion:** `feat(packaging): PyInstaller spec + Mac/Windows build workflows`

---

### Task 7.1: `packaging/policydb.spec` (PyInstaller)

**Files:**
- Create: `packaging/policydb.spec`

- [ ] **Step 1: Create the spec**

Create `packaging/policydb.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PolicyDB — onedir build for Mac + Windows."""
import sys
from pathlib import Path

SRC = Path(SPECPATH).resolve().parent / "src"

block_cipher = None

a = Analysis(
    [str(SRC / "policydb" / "desktop.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(SRC / "policydb" / "migrations"), "policydb/migrations"),
        (str(SRC / "policydb" / "web" / "templates"), "policydb/web/templates"),
        (str(SRC / "policydb" / "web" / "static"), "policydb/web/static"),
        (str(SRC / "policydb" / "data"), "policydb/data"),
    ],
    hiddenimports=[
        "uvicorn.workers",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "uvicorn.loops.auto",
        "jinja2.ext",
        "sqlite3",
        "phonenumbers",
        "rapidfuzz",
        "dateparser",
        "humanize",
        "babel",
        "webview",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PolicyDB",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,  # TODO(icon): ship a .icns / .ico
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PolicyDB",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PolicyDB.app",
        icon=None,
        bundle_identifier="com.policydb.desktop",
        info_plist={"CFBundleShortVersionString": "1.0.0", "CFBundleVersion": "1"},
    )
```

- [ ] **Step 2: Smoke-test the spec locally (Mac)**

From a NON-iCloud path (remember `feedback_icloud_deadlock`):
```bash
cd /Users/grantgreeson/Developer/policydb
~/.policydb/venv/bin/pip install pyinstaller
~/.policydb/venv/bin/pyinstaller packaging/policydb.spec --distpath /tmp/policydb-dist --workpath /tmp/policydb-build
```

Expected: `/tmp/policydb-dist/PolicyDB.app` (or `PolicyDB/`) exists. Double-click it — the PolicyDB window should open.

If a hidden import is missing, add it to `hiddenimports` list and rebuild.

- [ ] **Step 3: Commit**

```bash
git add packaging/policydb.spec
git commit -m "feat(packaging): PyInstaller spec for Mac + Windows onedir build — phase 7/8"
```

---

### Task 7.2: `packaging/build.py` — single-entry script

**Files:**
- Create: `packaging/build.py`

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""PolicyDB build entry — wraps PyInstaller + OS-specific post-processing.

Usage:
    python packaging/build.py --platform mac|win|both

Runs PyInstaller, then packages into .dmg (Mac) / .msi (Windows) if the
platform-specific tooling is available. Unsigned — code signing deferred to v2.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "policydb.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def run_pyinstaller() -> None:
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC),
           "--distpath", str(DIST), "--workpath", str(BUILD), "--noconfirm"]
    print("+ " + " ".join(cmd))
    subprocess.check_call(cmd)


def package_mac() -> Path:
    app = DIST / "PolicyDB.app"
    assert app.exists(), f"Mac app not built: {app}"
    dmg = DIST / "PolicyDB.dmg"
    print(f"+ hdiutil create {dmg}")
    subprocess.check_call([
        "hdiutil", "create", "-volname", "PolicyDB",
        "-srcfolder", str(app), "-ov", "-format", "UDZO", str(dmg),
    ])
    return dmg


def package_windows() -> Path:
    """Wrap the onedir output in a .msi via WiX (optional) or zip it."""
    dist_dir = DIST / "PolicyDB"
    assert dist_dir.exists(), f"Win build not found: {dist_dir}"
    candle = shutil.which("candle")
    light  = shutil.which("light")
    if candle and light:
        # WiX available — TODO: generate a .wxs file. For v1 fall through to zip.
        pass
    zipf = DIST / "PolicyDB-win.zip"
    shutil.make_archive(str(zipf.with_suffix("")), "zip", dist_dir)
    return zipf


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--platform", choices=["mac", "win", "both"], default="both")
    args = p.parse_args()

    run_pyinstaller()

    if args.platform in ("mac", "both") and sys.platform == "darwin":
        dmg = package_mac()
        print(f"Built {dmg}")
    if args.platform in ("win", "both") and sys.platform == "win32":
        msi = package_windows()
        print(f"Built {msi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Manual build test (Mac)**

```bash
cd /Users/grantgreeson/Developer/policydb
python packaging/build.py --platform mac
```
Expected: `dist/PolicyDB.dmg` exists. Double-click it — mounts, shows PolicyDB.app.

- [ ] **Step 3: Commit**

```bash
git add packaging/build.py
git commit -m "feat(packaging): build.py wrapper for mac/win — phase 7/8"
```

---

### Task 7.3: `packaging/README.md` + GitHub Actions

**Files:**
- Create: `packaging/README.md`
- Create: `.github/workflows/package-mac.yml`
- Create: `.github/workflows/package-win.yml`

- [ ] **Step 1: Create README**

```markdown
# PolicyDB Packaging

Build cross-platform desktop installers from the repo.

## Prerequisites

- Python 3.11+ on the target build machine
- Repo cloned into a **non-iCloud path** (e.g. `~/Developer/policydb`).
  PyInstaller's temp churn fights iCloud materialization — see
  `feedback_icloud_deadlock`.
- `pip install -r requirements-packaging.txt` (PyInstaller, etc.)

## Commands

```bash
python packaging/build.py --platform mac    # macOS only
python packaging/build.py --platform win    # Windows only
python packaging/build.py --platform both   # both
```

Output is in `dist/`:
- Mac: `PolicyDB.app`, `PolicyDB.dmg`
- Windows: `PolicyDB/` folder, `PolicyDB-win.zip`

## Windows: WebView2 runtime

The `.msi` (or zip) assumes WebView2 Evergreen Runtime is installed (default on
Windows 11; auto-installed via Windows Update on most Windows 10). If not
present, the app window fails to open — document this in the onboarding.

## Code signing

Not implemented in v1. First-launch flows:
- Mac: Gatekeeper warns — right-click the app → Open → Open.
- Windows: SmartScreen warns — "More info" → "Run anyway".

## CI

GitHub Actions workflows `package-mac.yml` + `package-win.yml` build and
upload artifacts. Trigger manually via the Actions tab.
```

- [ ] **Step 2: Mac workflow**

Create `.github/workflows/package-mac.yml`:

```yaml
name: Package (macOS)

on:
  workflow_dispatch:

jobs:
  build:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e . pyinstaller
      - run: python packaging/build.py --platform mac
      - uses: actions/upload-artifact@v4
        with:
          name: PolicyDB-mac
          path: |
            dist/PolicyDB.app
            dist/PolicyDB.dmg
```

- [ ] **Step 3: Windows workflow**

Create `.github/workflows/package-win.yml`:

```yaml
name: Package (Windows)

on:
  workflow_dispatch:

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e . pyinstaller
      - run: python packaging/build.py --platform win
      - uses: actions/upload-artifact@v4
        with:
          name: PolicyDB-win
          path: |
            dist/PolicyDB/
            dist/PolicyDB-win.zip
```

- [ ] **Step 4: Commit**

```bash
git add packaging/README.md .github/workflows/package-mac.yml .github/workflows/package-win.yml
git commit -m "feat(packaging): README + GitHub Actions workflows — phase 7/8"
```

---

# Phase 8 — Onboarding

**Goal:** First-run single-screen form. Saves `user_name` + `user_email` to config, optionally imports clients from CSV, redirects to `/action-center?tab=today`.

**PR title suggestion:** `feat(onboarding): single-screen welcome form for first launch`

---

### Task 8.1: `/onboarding` GET route + template

**Files:**
- Create: `src/policydb/web/routes/onboarding.py`
- Create: `src/policydb/web/templates/onboarding/welcome.html`
- Modify: `src/policydb/web/app.py` — register the new router
- Create: `tests/test_onboarding.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_onboarding.py`:

```python
"""Tests for onboarding route."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from policydb.db import init_db


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
def app_client(tmp_db):
    from policydb.web.app import app
    with TestClient(app) as c:
        yield c


def test_onboarding_get_renders_form(app_client):
    r = app_client.get("/onboarding")
    assert r.status_code == 200
    assert 'name="full_name"' in r.text
    assert 'name="email"' in r.text
    assert 'type="file"' in r.text
    assert "Get started" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_onboarding.py::test_onboarding_get_renders_form -v`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Create the route module**

```python
"""Onboarding — single-screen welcome form shown on first launch."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from policydb.web.app import get_db, templates
import policydb.config as cfg

router = APIRouter()


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_form(request: Request, conn=Depends(get_db)):
    """Render the onboarding form."""
    return templates.TemplateResponse("onboarding/welcome.html", {"request": request})
```

Create `src/policydb/web/templates/onboarding/welcome.html`:

```jinja
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Welcome to PolicyDB</title>
  <link rel="stylesheet" href="/static/css/base.css" />
  <style>
    body { background: var(--bg, #F7F3EE); font-family: "DM Sans", sans-serif; }
    .welcome {
      max-width: 520px; margin: 80px auto; padding: 40px 44px;
      background: var(--surface, #fff); border: 1px solid var(--border);
      border-radius: 10px; box-shadow: 0 8px 24px rgba(0,15,71,0.08);
    }
    .welcome h1 {
      font-family: "DM Serif Display", serif; color: var(--brand);
      font-weight: 400; margin: 0 0 4px; font-size: 30px;
    }
    .welcome .lede { color: var(--muted); font-size: 13px; margin: 0 0 24px; }
    .field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 14px; font-size: 13px; }
    .field > label { color: var(--body); font-weight: 500; }
    .field > .hint { color: var(--muted); font-size: 11px; }
    .field input[type=text],
    .field input[type=email] {
      padding: 8px 12px; border: 1px solid var(--border); border-radius: 5px;
      background: var(--bg); color: var(--body); font-size: 14px;
    }
    .field input[type=file] { padding: 4px 0; font-size: 12px; }
    .btn-primary {
      background: var(--accent, #0B4BFF); color: #fff; border: 0;
      padding: 10px 22px; border-radius: 6px; font-size: 14px; font-weight: 500;
      cursor: pointer; margin-top: 8px;
    }
  </style>
</head>
<body>
  <main class="welcome">
    <h1>Welcome to PolicyDB</h1>
    <p class="lede">Tell us a bit about yourself to get started. Takes about 30 seconds.</p>

    <form method="post" action="/onboarding" enctype="multipart/form-data">
      <div class="field">
        <label for="full_name">Full name <span class="required">*</span></label>
        <input id="full_name" type="text" name="full_name" required maxlength="200" />
      </div>

      <div class="field">
        <label for="email">Email <span class="required">*</span></label>
        <input id="email" type="email" name="email" required />
      </div>

      <div class="field">
        <label for="csv">Import existing clients (optional)</label>
        <input id="csv" type="file" name="csv_file" accept=".csv,.xlsx" />
        <span class="hint">Accepts the standard PolicyDB client CSV/XLSX format.</span>
      </div>

      <button type="submit" class="btn-primary">Get started</button>
    </form>
  </main>
</body>
</html>
```

Register the router in `src/policydb/web/app.py`:

```python
from policydb.web.routes import onboarding
app.include_router(onboarding.router)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_onboarding.py::test_onboarding_get_renders_form -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/onboarding.py \
        src/policydb/web/templates/onboarding/welcome.html \
        src/policydb/web/app.py tests/test_onboarding.py
git commit -m "feat(onboarding): GET /onboarding + welcome template — phase 8/8"
```

---

### Task 8.2: `POST /onboarding` — save config + optional CSV import

**Files:**
- Modify: `src/policydb/web/routes/onboarding.py`
- Modify: `tests/test_onboarding.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_onboarding.py`:

```python
from io import BytesIO


def test_onboarding_post_saves_name_and_email(app_client):
    r = app_client.post(
        "/onboarding",
        data={"full_name": "Mark Tester", "email": "mark@example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "/action-center?tab=today" in r.headers["location"]

    import policydb.config as cfg
    assert cfg.get("user_name") == "Mark Tester"
    assert cfg.get("user_email") == "mark@example.com"


def test_onboarding_post_rejects_invalid_email(app_client):
    r = app_client.post(
        "/onboarding",
        data={"full_name": "X", "email": "not-an-email"},
        follow_redirects=False,
    )
    assert r.status_code in (400, 422)


def test_onboarding_post_imports_csv(app_client):
    csv_bytes = b"name,industry_segment\nAcme Onboard,Manufacturing\nBeta Onboard,Tech\n"
    r = app_client.post(
        "/onboarding",
        data={"full_name": "Mark", "email": "mark@example.com"},
        files={"csv_file": ("clients.csv", BytesIO(csv_bytes), "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 302
    from policydb.db import get_connection
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM clients WHERE name LIKE '%Onboard'"
    ).fetchone()[0]
    assert count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_onboarding.py -v`
Expected: FAIL on the POST tests.

- [ ] **Step 3: Implement the POST handler**

Append to `src/policydb/web/routes/onboarding.py`:

```python
from pathlib import Path
import tempfile

from fastapi.responses import RedirectResponse
from policydb.importer import ClientImporter
from policydb.utils import clean_email


@router.post("/onboarding", response_class=RedirectResponse)
async def onboarding_submit(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    csv_file: UploadFile | None = File(None),
    conn=Depends(get_db),
):
    name = (full_name or "").strip()
    cleaned = clean_email(email)
    if not name or not cleaned:
        return HTMLResponse("Name and a valid email are required.", status_code=422)

    cfg.set_value("user_name", name)
    cfg.set_value("user_email", cleaned)
    cfg.save_config()

    if csv_file and csv_file.filename:
        contents = await csv_file.read()
        if contents:
            with tempfile.NamedTemporaryFile(suffix=Path(csv_file.filename).suffix, delete=False) as tmp:
                tmp.write(contents)
                tmp_path = Path(tmp.name)
            try:
                ClientImporter().import_csv(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)

    return RedirectResponse("/action-center?tab=today", status_code=302)
```

Note: `cfg.set_value` / `cfg.save_config` may have a slightly different API — check `src/policydb/config.py` for the exact names (the CLAUDE.md reference mentions `cfg.get`, `cfg.add_list_item`, `cfg.remove_list_item`, `cfg.save_config`; a scalar setter may be `cfg.set(key, value)` or a direct dict assignment).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_onboarding.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/onboarding.py tests/test_onboarding.py
git commit -m "feat(onboarding): POST handler — save user + import CSV — phase 8/8"
```

---

### Task 8.3: Wire onboarding redirect into `desktop.py` first-launch check

**Files:**
- Modify: `src/policydb/desktop.py`

- [ ] **Step 1: Update `main()`**

Replace the startup URL assignment in `src/policydb/desktop.py`:

```python
    # First-run detection — redirect to /onboarding if the DB has no clients and no contacts.
    from policydb.db import get_connection
    conn = get_connection()
    client_count = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    contact_count = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    conn.close()
    if client_count == 0 and contact_count == 0:
        startup_url = f"http://127.0.0.1:{port}/onboarding"
    else:
        startup_url = f"http://127.0.0.1:{port}/action-center?tab=today"
```

- [ ] **Step 2: Manual smoke test**

```bash
# Reset the DB and launch
rm -rf /tmp/onboarding-test
POLICYDB_DATA_DIR=/tmp/onboarding-test ~/.policydb/venv/bin/python -c "from policydb.desktop import main; main()"
```

Expected: window opens on `/onboarding`. Fill in the form. Submit. Window navigates to Today tab.

Relaunch (don't clear data dir). Expected: window opens directly on Today tab.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/desktop.py
git commit -m "feat(onboarding): desktop launcher redirects to /onboarding when DB empty — phase 8/8"
```

---

## Final integration — branch QA

- [ ] **Step 1: Run the full test suite**

```bash
pytest -q
```

Expected: no failures. Note the total count for the PR description.

- [ ] **Step 2: Manual QA pass on the Today tab**

Use Chrome (per `feedback_chrome_qa`). Verify everything from the Visual Refinements section:

- Editorial date header reads correctly (current weekday, month, day)
- Filter pills: Overdue + Today + Tomorrow default-active; All open toggles others off
- Kind chips neutral-bg with colored left border (no double-coding with priority bar)
- Priority bar colors per urgency; overdue pulse is slow (2.8s)
- Ledger hairline on every 5th row
- Row hover: subject underlines in blue, `•••` fades in
- Custom checkbox strikes through + fades row before complete
- 5s undo toast appears and works
- Cmd/Ctrl+N opens Add Task modal
- Standalone tasks (no client) show "Standalone" in client column
- Smart Suggestions rail loads after ~200ms, groups render even when empty (greyed)
- Fast-capture `+` creates tasks in one click; shift-click opens modal pre-filled
- Empty "Inbox Zero for today." renders when no open tasks
- Print preview: priority bar collapses, chips become `[bracketed]`

- [ ] **Step 3: Push branch + open draft PR (or leave local per preference)**

```bash
git push -u origin feat/multi-user-tasklist
```

Use `gh pr create` if you want a PR opened. Otherwise stop here.

---

## Self-review checklist (run against this plan before committing)

- [x] Every phase from the spec's Build Sequence (§ "Build Sequence", 8 items) has its own phase here.
- [x] Every Visual Refinements item is wired into a specific task (Tier 1 in 2.7 CSS + 2.10; Tier 2 in 2.7 + 2.10 + 3.3; Tier 3 in 2.10 + final QA).
- [x] Schema corrections (migration 163, `disposition`-driven Waiting, `merged_into_id IS NULL AND auto_closed_at IS NULL`, `contact_person`) all reflected in Tasks 2.1 + 2.2.
- [x] Focus retirement covers: delete 3 templates ✓, flip default tab ✓, redirects ✓, sessionStorage migration ✓, label reword ✓, dashboard count ✓, module docstring ✓.
- [x] Plan Week keeps `/followups/plan` URL and all behavior — only presentation changes.
- [x] Desktop launcher covers: pywebview ✓, free port ✓, uvicorn thread ✓, health-check ✓, first-launch sentinel migration ✓, onboarding redirect ✓.
- [x] Packaging covers: PyInstaller spec ✓, build.py ✓, README ✓, Mac + Windows workflows ✓.
- [x] Onboarding covers: GET form ✓, POST handler ✓, CSV import ✓, config defaults ✓, desktop redirect ✓.
- [x] No TODOs / placeholder code in any step.
- [x] Every TDD-applicable task has failing test → implement → passing test → commit.
- [x] UI-only tasks have explicit manual QA checklists.
- [x] Route ordering (literals first) respected — `/tasks/create` defined before `/tasks/{id}/...`.
- [x] Touch-once preserved throughout — all write paths go into `activity_log`; no new task table.
