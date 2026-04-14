# Open Tasks Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a shared, interactive "Open Tasks" panel on issue, client, program, and policy pages that rolls up outstanding activity follow-ups across each scope and supports inline triage actions (done, snooze, waiting toggle, log & close, attach, note, + add task), with full touch-once compliance.

**Architecture:** One Jinja2 partial template + one FastAPI route module + one backend helper parameterized by `scope_type`. The `activity_log` table is the single source of truth; `policies.follow_up_date` and `clients.follow_up_date` are treated as caches and re-synced after every mutation via new helpers. Existing activity-creation code paths are refactored to call one shared helper so the panel's "+ Add task" doesn't drift from policy-edit / client-activity / inbox-process quick-logs.

**Tech Stack:** FastAPI + Jinja2 + HTMX (existing PolicyDB stack). SQLite via `sqlite3.Row`. Pytest for tests. Tailwind CDN for styling. No new dependencies.

**Related spec:** `docs/superpowers/specs/2026-04-14-open-tasks-panel-design.md`

---

## File Inventory

### New files
- `src/policydb/web/routes/open_tasks.py` — new route module (panel render + 7 action endpoints)
- `src/policydb/web/templates/_open_tasks_panel.html` — shared panel partial
- `src/policydb/web/templates/_open_tasks_row.html` — single row partial (reusable)
- `src/policydb/web/templates/_open_tasks_new_form.html` — inline + Add task quick-log form
- `src/policydb/web/templates/_toast.html` — shared toast container + JS helper
- `tests/test_open_tasks.py` — unit tests for `get_open_tasks`, sync helpers, `create_followup_activity`
- `tests/test_open_tasks_routes.py` — route tests for panel render + each action endpoint

### Modified files
- `src/policydb/queries.py` — add `sync_policy_follow_up_date`, `sync_client_follow_up_date`, `create_followup_activity`, `filter_thread_for_history`, `get_open_tasks`
- `src/policydb/web/app.py` — register `open_tasks` router, mount shared helpers
- `src/policydb/web/templates/base.html` — include `_toast.html` + HTMX `afterSwap` listener
- `src/policydb/web/templates/issues/_scope_rollup.html` — remove "Open Follow-ups" subsection (lines 192–216 in current file)
- `src/policydb/web/templates/issues/detail.html` — insert panel above Scope Rollup card
- `src/policydb/web/templates/clients/_tab_overview.html` — insert lazy-loaded panel near top
- `src/policydb/web/templates/clients/_sticky_sidebar.html` — replace follow-up list with count link
- `src/policydb/web/templates/programs/_tab_overview.html` — insert lazy-loaded panel above Scope Rollup
- `src/policydb/web/templates/policies/edit.html` — insert panel above activity thread
- `src/policydb/web/routes/policies.py` — refactor 4 `INSERT INTO activity_log` call sites to `create_followup_activity`
- `src/policydb/web/routes/activities.py` — refactor raw inserts to `create_followup_activity`
- `src/policydb/web/routes/clients.py` — refactor raw inserts to `create_followup_activity`
- `src/policydb/web/routes/inbox.py` — refactor raw inserts to `create_followup_activity`
- `src/policydb/web/routes/issues.py` — apply `filter_thread_for_history` to the issue activity thread
- `src/policydb/web/templates/clients/_tab_activity.html` — filter thread via route
- `src/policydb/web/templates/programs/_tab_activity.html` — filter thread via route

---

## Task 1: Scalar-date sync helpers

**Files:**
- Modify: `src/policydb/queries.py` (add after `supersede_followups()` at line 1034)
- Test: `tests/test_open_tasks.py` (new file)

- [ ] **Step 1.1: Create the test file skeleton**

Create `tests/test_open_tasks.py`:

```python
"""Tests for Open Tasks panel: sync helpers, creation helper, get_open_tasks."""
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


def _seed_client(conn, name="Sync Test Co"):
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES (?, 'Test')", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_policy(conn, client_id, uid="POL-001"):
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date) "
        "VALUES (?, ?, 'GL', 'Test Carrier', '2026-01-01', '2027-01-01')",
        (uid, client_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_followup(conn, client_id, policy_id, subject, fu_date, done=0):
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', ?, ?, ?, 'followup', 'Grant')",
        (date.today().isoformat(), client_id, policy_id, subject, fu_date, done),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
```

- [ ] **Step 1.2: Write failing test for `sync_policy_follow_up_date`**

Append to `tests/test_open_tasks.py`:

```python
# ── sync_policy_follow_up_date ────────────────────────────────────────────────

def test_sync_policy_fu_date_sets_earliest_open(tmp_db):
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "later", "2026-05-10")
    _insert_followup(conn, cid, pid, "earlier", "2026-05-01")
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] == "2026-05-01"


def test_sync_policy_fu_date_clears_when_no_open(tmp_db):
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "done", "2026-05-01", done=1)
    conn.execute("UPDATE policies SET follow_up_date='2026-05-01' WHERE id=?", (pid,))
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] is None


def test_sync_client_fu_date_sets_earliest_open(tmp_db):
    from policydb.queries import sync_client_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    # Client-level follow-ups have policy_id = NULL
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'direct1', '2026-06-10', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'direct2', '2026-06-01', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.commit()

    sync_client_follow_up_date(conn, cid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM clients WHERE id=?", (cid,)).fetchone()
    assert row["follow_up_date"] == "2026-06-01"
```

- [ ] **Step 1.3: Run test and confirm ImportError**

Run: `pytest tests/test_open_tasks.py::test_sync_policy_fu_date_sets_earliest_open -v`
Expected: FAIL with `ImportError: cannot import name 'sync_policy_follow_up_date'`.

- [ ] **Step 1.4: Implement the sync helpers**

In `src/policydb/queries.py`, add immediately after `supersede_followups()` (after line 1034):

```python
def sync_policy_follow_up_date(conn, policy_id: int) -> None:
    """Re-derive policies.follow_up_date from the earliest open activity follow-up.

    The activity_log is the source of truth; policies.follow_up_date is a scalar
    cache used by renewal pipeline views, Action Center, and policy pages.
    This helper keeps the cache coherent after any mutation that could change
    the outcome (mark done, snooze, log-close).

    Behavior: picks the earliest follow_up_date across open activity follow-ups
    on this policy; sets NULL if none exist.
    """
    row = conn.execute(
        """SELECT MIN(follow_up_date) AS earliest
           FROM activity_log
           WHERE policy_id = ?
             AND follow_up_done = 0
             AND follow_up_date IS NOT NULL
             AND item_kind = 'followup'""",
        (policy_id,),
    ).fetchone()
    earliest = row["earliest"] if row else None
    conn.execute(
        "UPDATE policies SET follow_up_date = ? WHERE id = ?",
        (earliest, policy_id),
    )


def sync_client_follow_up_date(conn, client_id: int) -> None:
    """Re-derive clients.follow_up_date from earliest open client-level follow-up.

    Client-level means activity_log rows with client_id set and policy_id NULL.
    Same rule as sync_policy_follow_up_date: source of truth is activity_log.
    """
    row = conn.execute(
        """SELECT MIN(follow_up_date) AS earliest
           FROM activity_log
           WHERE client_id = ?
             AND policy_id IS NULL
             AND follow_up_done = 0
             AND follow_up_date IS NOT NULL
             AND item_kind = 'followup'""",
        (client_id,),
    ).fetchone()
    earliest = row["earliest"] if row else None
    conn.execute(
        "UPDATE clients SET follow_up_date = ? WHERE id = ?",
        (earliest, client_id),
    )
```

- [ ] **Step 1.5: Run tests and confirm pass**

Run: `pytest tests/test_open_tasks.py -v -k "sync"`
Expected: 3 tests pass.

- [ ] **Step 1.6: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): scalar-date sync helpers for open tasks panel

Add sync_policy_follow_up_date and sync_client_follow_up_date that re-derive
the scalar follow_up_date fields from the earliest open activity follow-up.
Touch-once cache for policies.follow_up_date / clients.follow_up_date — the
activity_log is always the source of truth."
```

---

## Task 2: Shared `create_followup_activity` helper

**Files:**
- Modify: `src/policydb/queries.py` (add after sync helpers from Task 1)
- Test: `tests/test_open_tasks.py` (append)

- [ ] **Step 2.1: Write failing test**

Append to `tests/test_open_tasks.py`:

```python
# ── create_followup_activity ─────────────────────────────────────────────────

def test_create_followup_activity_inserts_and_supersedes(tmp_db):
    from policydb.queries import create_followup_activity
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    # Pre-existing older follow-up that should be superseded
    old_id = _insert_followup(conn, cid, pid, "old", "2026-03-01")
    conn.commit()

    new_id = create_followup_activity(
        conn,
        client_id=cid,
        policy_id=pid,
        issue_id=None,
        subject="New task",
        activity_type="Task",
        follow_up_date="2026-04-20",
        follow_up_done=False,
        disposition="",
    )
    conn.commit()

    assert new_id is not None and new_id != old_id
    row = conn.execute("SELECT subject, follow_up_done FROM activity_log WHERE id=?", (new_id,)).fetchone()
    assert row["subject"] == "New task"
    assert row["follow_up_done"] == 0

    # Supersession fired: old row should be closed
    old_row = conn.execute("SELECT follow_up_done, auto_close_reason FROM activity_log WHERE id=?", (old_id,)).fetchone()
    assert old_row["follow_up_done"] == 1
    assert old_row["auto_close_reason"] == "superseded"

    # policies.follow_up_date should be synced to the new date
    pol = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert pol["follow_up_date"] == "2026-04-20"


def test_create_followup_activity_note_mode_no_supersede(tmp_db):
    from policydb.queries import create_followup_activity
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    old_id = _insert_followup(conn, cid, pid, "still open", "2026-03-01")
    conn.commit()

    # A note: done=True, date=None → should NOT trigger supersede
    create_followup_activity(
        conn,
        client_id=cid,
        policy_id=pid,
        issue_id=None,
        subject="FYI note",
        activity_type="Note",
        follow_up_date=None,
        follow_up_done=True,
        disposition="",
    )
    conn.commit()

    old_row = conn.execute("SELECT follow_up_done FROM activity_log WHERE id=?", (old_id,)).fetchone()
    assert old_row["follow_up_done"] == 0  # untouched
```

- [ ] **Step 2.2: Run test — expect failure**

Run: `pytest tests/test_open_tasks.py::test_create_followup_activity_inserts_and_supersedes -v`
Expected: FAIL with `ImportError: cannot import name 'create_followup_activity'`.

- [ ] **Step 2.3: Implement `create_followup_activity`**

Append to `src/policydb/queries.py` after the sync helpers from Task 1:

```python
def create_followup_activity(
    conn,
    *,
    client_id: int,
    policy_id: int | None,
    issue_id: int | None,
    subject: str,
    activity_type: str = "Task",
    follow_up_date: str | None,
    follow_up_done: bool = False,
    disposition: str = "",
    contact_person: str | None = None,
    contact_id: int | None = None,
    details: str | None = None,
    duration_hours: float | None = None,
) -> int:
    """Single creation path for any follow-up activity. All quick-log endpoints
    and the Open Tasks panel's + Add task button call this helper.

    Behavior:
    - Inserts into activity_log with item_kind='followup'
    - If follow_up_date is set and follow_up_done is False (new open follow-up
      on a policy), runs supersede_followups() to close older siblings and
      sync policies.follow_up_date
    - If issue_id is None and policy_id is set, runs auto_link_to_renewal_issue
      to attach to an open renewal issue if one exists
    - If policy_id is None but client_id is set, syncs clients.follow_up_date

    Returns the new activity_log.id.
    """
    from datetime import date as _date

    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person,
            contact_id, subject, details, follow_up_date, follow_up_done,
            account_exec, duration_hours, disposition, issue_id, item_kind)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'followup')""",
        (
            _date.today().isoformat(), client_id, policy_id, activity_type,
            contact_person, contact_id, subject, details,
            follow_up_date, 1 if follow_up_done else 0,
            account_exec, duration_hours,
            disposition or None, issue_id,
        ),
    )
    new_id = cursor.lastrowid

    # Auto-link to renewal issue if not explicitly set
    if issue_id is None and policy_id is not None:
        from policydb.renewal_issues import auto_link_to_renewal_issue
        auto_link_to_renewal_issue(conn, policy_id, new_id)

    # Supersede older open follow-ups when this is a new open follow-up
    if follow_up_date and not follow_up_done and policy_id is not None:
        supersede_followups(conn, policy_id, follow_up_date)
        # supersede_followups syncs policies.follow_up_date already
    elif policy_id is not None:
        # Done-at-creation or no date — still re-sync the cache
        sync_policy_follow_up_date(conn, policy_id)

    # Client-level sync for direct client follow-ups
    if policy_id is None and client_id is not None:
        sync_client_follow_up_date(conn, client_id)

    return new_id
```

- [ ] **Step 2.4: Run tests and confirm pass**

Run: `pytest tests/test_open_tasks.py -v -k "create_followup"`
Expected: 2 tests pass.

- [ ] **Step 2.5: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): shared create_followup_activity helper

Single creation path for follow-up activities. Wraps INSERT INTO activity_log
with supersession, auto-link to renewal issues, and scalar-date sync. Existing
quick-log endpoints (policies.py, activities.py, clients.py, inbox.py) will
be refactored to call this helper in a later task."
```

---

## Task 3: `filter_thread_for_history` helper

**Files:**
- Modify: `src/policydb/queries.py` (append)
- Test: `tests/test_open_tasks.py` (append)

- [ ] **Step 3.1: Write failing test**

Append to `tests/test_open_tasks.py`:

```python
# ── filter_thread_for_history ────────────────────────────────────────────────

def test_filter_thread_drops_open_followups():
    from policydb.queries import filter_thread_for_history
    rows = [
        {"id": 1, "item_kind": "followup", "follow_up_done": 0, "follow_up_date": "2026-05-01", "subject": "open"},
        {"id": 2, "item_kind": "followup", "follow_up_done": 1, "follow_up_date": "2026-04-01", "subject": "closed"},
        {"id": 3, "item_kind": "followup", "follow_up_done": 0, "follow_up_date": None, "subject": "note-ish"},
        {"id": 4, "item_kind": "issue", "follow_up_done": 0, "follow_up_date": "2026-05-01", "subject": "issue header"},
    ]
    kept = filter_thread_for_history(rows)
    ids = [r["id"] for r in kept]
    assert 1 not in ids  # open followup with date — panel owns it
    assert 2 in ids      # closed followup — history
    assert 3 in ids      # followup with no date — history (note-like)
    assert 4 in ids      # issue header — history
```

- [ ] **Step 3.2: Run test — expect failure**

Run: `pytest tests/test_open_tasks.py::test_filter_thread_drops_open_followups -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3.3: Implement**

Append to `src/policydb/queries.py`:

```python
def filter_thread_for_history(rows: list) -> list:
    """Filter an activity thread to drop rows owned by the Open Tasks panel.

    A row is panel-owned when all of: item_kind='followup', follow_up_done=0,
    follow_up_date IS NOT NULL. Everything else (closed follow-ups, notes
    with no follow-up, issue headers, non-followup item_kinds) stays in the
    thread as history.

    Accepts either sqlite3.Row or dict rows; reads fields via subscription.
    """
    out = []
    for r in rows:
        item_kind = r["item_kind"] if "item_kind" in r.keys() if hasattr(r, "keys") else r.get("item_kind")
        done = r["follow_up_done"] if hasattr(r, "keys") and "follow_up_done" in r.keys() else r.get("follow_up_done")
        fu = r["follow_up_date"] if hasattr(r, "keys") and "follow_up_date" in r.keys() else r.get("follow_up_date")
        if item_kind == "followup" and not done and fu:
            continue
        out.append(r)
    return out
```

**Note:** The conditional attribute access handles both `sqlite3.Row` (which has `.keys()`) and plain dicts. If the test fails due to syntax, replace with a cleaner version:

```python
def filter_thread_for_history(rows: list) -> list:
    """Filter an activity thread to drop rows owned by the Open Tasks panel."""
    def _get(r, key):
        if isinstance(r, dict):
            return r.get(key)
        try:
            return r[key]
        except (KeyError, IndexError):
            return None

    out = []
    for r in rows:
        if (_get(r, "item_kind") == "followup"
                and not _get(r, "follow_up_done")
                and _get(r, "follow_up_date")):
            continue
        out.append(r)
    return out
```

Use the second version.

- [ ] **Step 3.4: Run test — confirm pass**

Run: `pytest tests/test_open_tasks.py::test_filter_thread_drops_open_followups -v`
Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): filter_thread_for_history helper

Shared filter for activity thread templates. Drops rows the Open Tasks panel
owns so the thread can act as a history view. One rule, one place — every
thread template uses the same helper."
```

---

## Task 4: Shared toast library

**Files:**
- Create: `src/policydb/web/templates/_toast.html`
- Modify: `src/policydb/web/templates/base.html`

- [ ] **Step 4.1: Inspect base.html**

Read `src/policydb/web/templates/base.html` to locate the closing `</body>` and verify HTMX script tag. No code changes yet — just know where to inject.

- [ ] **Step 4.2: Create `_toast.html`**

Create `src/policydb/web/templates/_toast.html`:

```html
{# Shared toast container + JS. Include once from base.html near </body>.

   Action handlers emit:
     <div id="toast-trigger" hx-swap-oob="true"
          data-message="Snoozed +7d" data-kind="success"></div>
   in their response. An HTMX afterSwap listener reads the data-* attrs and
   calls showToast(). Kinds: success, info, warning, error.
#}
<div id="toast-area"
     class="fixed bottom-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
</div>
<div id="toast-trigger" hx-swap-oob="true" data-message="" data-kind=""></div>

<script>
(function() {
  const COLORS = {
    success: "bg-emerald-600 text-white",
    info:    "bg-blue-600 text-white",
    warning: "bg-amber-500 text-white",
    error:   "bg-red-600 text-white",
  };

  window.showToast = function(message, kind) {
    if (!message) return;
    const area = document.getElementById("toast-area");
    if (!area) return;
    const color = COLORS[kind || "success"] || COLORS.success;
    const pill = document.createElement("div");
    pill.className = `pointer-events-auto ${color} rounded-lg px-4 py-2 text-sm shadow-lg transition-opacity duration-300`;
    pill.textContent = message;
    area.appendChild(pill);
    setTimeout(() => { pill.style.opacity = "0"; }, 2200);
    setTimeout(() => { pill.remove(); }, 2500);
  };

  document.body.addEventListener("htmx:afterSwap", function(evt) {
    const trigger = document.getElementById("toast-trigger");
    if (!trigger) return;
    const msg = trigger.dataset.message;
    const kind = trigger.dataset.kind;
    if (msg) {
      window.showToast(msg, kind);
      trigger.dataset.message = "";
      trigger.dataset.kind = "";
    }
  });
})();
</script>
```

- [ ] **Step 4.3: Include `_toast.html` in `base.html`**

Open `src/policydb/web/templates/base.html` and locate the `</body>` tag. Immediately before `</body>`, add:

```html
{% include "_toast.html" %}
```

- [ ] **Step 4.4: Start the dev server and sanity-check**

Run: `~/.policydb/venv/bin/policydb serve --port 8123 --reload` (background OK; use a random port >8005 per `feedback_server_restart`).

Open `http://127.0.0.1:8123/` in a browser, open DevTools Console, and paste:

```javascript
window.showToast("Hello from toast", "success");
```

Expected: a green pill appears at bottom-right, fades after ~2.5s. If it doesn't, check that `_toast.html` is included and the `#toast-area` div exists in the DOM.

- [ ] **Step 4.5: Commit**

```bash
git add src/policydb/web/templates/_toast.html src/policydb/web/templates/base.html
git commit -m "feat(ui): shared toast library

Add _toast.html included from base.html. Exposes window.showToast(msg, kind)
and an htmx:afterSwap listener that reads #toast-trigger data-* attributes
written by action handlers via hx-swap-oob. Kinds: success, info, warning,
error. Reusable across the app; first consumer is the Open Tasks panel."
```

---

## Task 5: `get_open_tasks()` — issue scope

**Files:**
- Modify: `src/policydb/queries.py` (append)
- Test: `tests/test_open_tasks.py` (append)

- [ ] **Step 5.1: Write failing test**

Append to `tests/test_open_tasks.py`:

```python
# ── get_open_tasks (issue scope) ─────────────────────────────────────────────

def test_get_open_tasks_issue_scope_splits_on_issue_and_loose(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid, "POL-100")

    # Create an issue header
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Renewal POL-100', 'issue', 'ISS-1', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Open task attached to the issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'on-issue task', '2026-04-15', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid, issue_id),
    )

    # Loose task on same policy, no issue_id
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose task', '2026-04-20', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    conn.commit()

    result = get_open_tasks(conn, "issue", issue_id)
    groups = {g["key"]: g for g in result["groups"]}
    assert "on_issue" in groups
    assert "loose" in groups
    assert len(groups["on_issue"]["rows"]) == 1
    assert groups["on_issue"]["rows"][0]["subject"] == "on-issue task"
    assert len(groups["loose"]["rows"]) == 1
    assert groups["loose"]["rows"][0]["subject"] == "loose task"
    assert result["total"] == 2
```

- [ ] **Step 5.2: Run — expect ImportError**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_issue_scope_splits_on_issue_and_loose -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 5.3: Implement `get_open_tasks` for issue scope**

Append to `src/policydb/queries.py`:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Open Tasks panel — aggregated follow-up rollup for issue/client/program/policy
# ─────────────────────────────────────────────────────────────────────────────

def _open_task_row_from_activity(r) -> dict:
    """Convert a sqlite row from activity_log (+ policy/client joins) into the
    panel's TaskRow shape. Helper shared across scopes."""
    from datetime import date as _date
    today = _date.today()
    fu = r["follow_up_date"]
    days_overdue = 0
    try:
        days_overdue = (today - _date.fromisoformat(fu)).days
    except (ValueError, TypeError):
        pass

    disposition = (r["disposition"] or "") if "disposition" in r.keys() else ""
    # Resolve accountability from config
    accountability = "my_action"
    for d in cfg.get("follow_up_dispositions", []):
        if d.get("label") == disposition:
            accountability = d.get("accountability", "my_action")
            break

    return {
        "activity_id": str(r["id"]),
        "subject": r["subject"] or r["activity_type"] or "Follow-up",
        "activity_type": r["activity_type"],
        "follow_up_date": fu,
        "days_overdue": days_overdue,
        "disposition": disposition,
        "accountability": accountability,
        "policy_id": r["policy_id"] if "policy_id" in r.keys() else None,
        "policy_uid": r["policy_uid"] if "policy_uid" in r.keys() else None,
        "policy_type": r["policy_type"] if "policy_type" in r.keys() else None,
        "client_id": r["client_id"],
        "client_name": r["client_name"] if "client_name" in r.keys() else "",
        "source": "activity",
        "is_on_issue": False,  # caller sets
        "linked_to_other_issue": None,  # caller sets
        "attach_target_issue_id": None,  # caller sets
    }


def _sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-r["days_overdue"], r["follow_up_date"] or ""))


def get_open_tasks(
    conn,
    scope_type: str,
    scope_id: int,
) -> dict:
    """Unified open-tasks rollup for the Open Tasks panel.

    scope_type: 'issue', 'client', 'program', or 'policy'.
    Returns {groups: [GroupDict], total, overdue, waiting}.
    """
    if scope_type == "issue":
        return _open_tasks_for_issue(conn, scope_id)
    raise ValueError(f"Unsupported scope_type: {scope_type}")


def _open_tasks_for_issue(conn, issue_id: int) -> dict:
    # Resolve the issue's covered policies via v_issue_policy_coverage
    covered = conn.execute(
        """SELECT DISTINCT ipc.policy_id
           FROM v_issue_policy_coverage ipc
           WHERE ipc.issue_id = ?""",
        (issue_id,),
    ).fetchall()
    policy_ids = [r["policy_id"] for r in covered if r["policy_id"] is not None]

    on_issue_rows: list[dict] = []
    loose_rows: list[dict] = []
    total = 0
    overdue = 0
    waiting = 0

    # On-issue: all open follow-ups linked to this issue (may include rows on
    # non-covered policies too, e.g. client-level activities)
    on_issue_raw = conn.execute(
        """SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                  a.disposition, a.policy_id, a.client_id, a.issue_id,
                  p.policy_uid, p.policy_type,
                  c.name AS client_name
           FROM activity_log a
           LEFT JOIN policies p ON p.id = a.policy_id
           LEFT JOIN clients c ON c.id = a.client_id
           WHERE a.issue_id = ?
             AND a.follow_up_done = 0
             AND a.follow_up_date IS NOT NULL
             AND a.item_kind = 'followup'""",
        (issue_id,),
    ).fetchall()
    for r in on_issue_raw:
        row = _open_task_row_from_activity(r)
        row["is_on_issue"] = True
        on_issue_rows.append(row)
        total += 1
        if row["days_overdue"] > 0:
            overdue += 1
        if row["accountability"] == "waiting_external":
            waiting += 1

    # Loose on scope: open follow-ups on covered policies whose issue_id is
    # NULL or points at a different issue
    if policy_ids:
        placeholders = ",".join("?" * len(policy_ids))
        loose_raw = conn.execute(
            f"""SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                       a.disposition, a.policy_id, a.client_id, a.issue_id,
                       p.policy_uid, p.policy_type,
                       c.name AS client_name,
                       other.issue_uid AS other_issue_uid
                FROM activity_log a
                JOIN policies p ON p.id = a.policy_id
                LEFT JOIN clients c ON c.id = a.client_id
                LEFT JOIN activity_log other ON other.id = a.issue_id AND other.item_kind = 'issue'
                WHERE a.policy_id IN ({placeholders})
                  AND a.follow_up_done = 0
                  AND a.follow_up_date IS NOT NULL
                  AND a.item_kind = 'followup'
                  AND (a.issue_id IS NULL OR a.issue_id != ?)""",  # noqa: S608
            (*policy_ids, issue_id),
        ).fetchall()
        for r in loose_raw:
            row = _open_task_row_from_activity(r)
            row["is_on_issue"] = False
            row["linked_to_other_issue"] = r["other_issue_uid"]
            loose_rows.append(row)
            total += 1
            if row["days_overdue"] > 0:
                overdue += 1
            if row["accountability"] == "waiting_external":
                waiting += 1

    groups = []
    if on_issue_rows:
        groups.append({
            "key": "on_issue",
            "title": "On this issue",
            "subtitle": None,
            "rows": _sort_rows(on_issue_rows),
        })
    if loose_rows:
        groups.append({
            "key": "loose",
            "title": "Loose on scope",
            "subtitle": "Not yet attached to this issue",
            "rows": _sort_rows(loose_rows),
        })

    return {"groups": groups, "total": total, "overdue": overdue, "waiting": waiting}
```

- [ ] **Step 5.4: Run test — confirm pass**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_issue_scope_splits_on_issue_and_loose -v`
Expected: PASS.

- [ ] **Step 5.5: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): get_open_tasks for issue scope

Aggregates open follow-ups across an issue's covered policies (via
v_issue_policy_coverage) and splits into on_issue / loose groups. Each row
carries enough metadata (policy_uid, disposition, accountability, other-issue
link) for the panel template to render inline actions."
```

---

## Task 6: `get_open_tasks()` — client scope

**Files:**
- Modify: `src/policydb/queries.py` (add `_open_tasks_for_client`)
- Test: `tests/test_open_tasks.py` (append)

- [ ] **Step 6.1: Write failing test**

Append to `tests/test_open_tasks.py`:

```python
def test_get_open_tasks_client_scope_groups_by_issue(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid1 = _seed_policy(conn, cid, "POL-200")
    pid2 = _seed_policy(conn, cid, "POL-201")

    # Issue touching POL-200 only
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'POL-200 renewal', 'issue', 'ISS-200', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid1),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Task on the issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'iss-200 task', '2026-04-18', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid1, issue_id),
    )
    # Task loose on POL-201 (not covered by any open issue)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose on POL-201', '2026-04-22', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid, pid2),
    )
    # Direct client follow-up (policy_id NULL)
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'client-direct', '2026-04-25', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.commit()

    result = get_open_tasks(conn, "client", cid)
    keys = [g["key"] for g in result["groups"]]
    assert "direct_client" in keys
    assert f"issue:{issue_id}" in keys
    assert "loose_policies" in keys
    assert result["total"] == 3
```

- [ ] **Step 6.2: Run — expect failure**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_client_scope_groups_by_issue -v`
Expected: FAIL (either ValueError from unsupported scope_type, or assertion).

- [ ] **Step 6.3: Implement `_open_tasks_for_client` and wire into `get_open_tasks`**

In `src/policydb/queries.py`, update `get_open_tasks`:

```python
def get_open_tasks(conn, scope_type: str, scope_id: int) -> dict:
    if scope_type == "issue":
        return _open_tasks_for_issue(conn, scope_id)
    if scope_type == "client":
        return _open_tasks_for_client(conn, scope_id)
    raise ValueError(f"Unsupported scope_type: {scope_type}")
```

Add after `_open_tasks_for_issue`:

```python
def _open_tasks_for_client(conn, client_id: int) -> dict:
    # 1. Find all open issues touching this client (via direct client_id OR
    #    via policies belonging to the client through v_issue_policy_coverage).
    open_issues = conn.execute(
        """SELECT DISTINCT a.id AS issue_id, a.issue_uid, a.subject, a.issue_severity,
                  a.issue_status
           FROM activity_log a
           WHERE a.item_kind = 'issue'
             AND a.issue_status NOT IN ('Resolved', 'Closed', 'Merged')
             AND a.merged_into_id IS NULL
             AND (a.client_id = ? OR a.id IN (
                 SELECT DISTINCT ipc.issue_id
                 FROM v_issue_policy_coverage ipc
                 JOIN policies p ON p.id = ipc.policy_id
                 WHERE p.client_id = ? AND p.archived = 0
             ))
           ORDER BY
             CASE a.issue_severity
               WHEN 'Critical' THEN 0
               WHEN 'High' THEN 1
               WHEN 'Normal' THEN 2
               WHEN 'Low' THEN 3
               ELSE 4 END,
             a.id""",
        (client_id, client_id),
    ).fetchall()
    issue_ids = [r["issue_id"] for r in open_issues]

    # 2. Policies covered by any open issue (for "loose policies" exclusion)
    covered_policy_ids: set[int] = set()
    if issue_ids:
        placeholders = ",".join("?" * len(issue_ids))
        cov = conn.execute(
            f"""SELECT DISTINCT policy_id FROM v_issue_policy_coverage
                WHERE issue_id IN ({placeholders})""",  # noqa: S608
            issue_ids,
        ).fetchall()
        covered_policy_ids = {r["policy_id"] for r in cov if r["policy_id"] is not None}

    total = 0
    overdue = 0
    waiting = 0

    def _count(row):
        nonlocal total, overdue, waiting
        total += 1
        if row["days_overdue"] > 0:
            overdue += 1
        if row["accountability"] == "waiting_external":
            waiting += 1

    # ── Group 1: direct_client
    direct_rows: list[dict] = []
    for r in conn.execute(
        """SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                  a.disposition, a.policy_id, a.client_id, a.issue_id,
                  c.name AS client_name
           FROM activity_log a
           JOIN clients c ON c.id = a.client_id
           WHERE a.client_id = ?
             AND a.policy_id IS NULL
             AND a.follow_up_done = 0
             AND a.follow_up_date IS NOT NULL
             AND a.item_kind = 'followup'""",
        (client_id,),
    ).fetchall():
        row = _open_task_row_from_activity(r)
        direct_rows.append(row)
        _count(row)

    # clients.follow_up_date itself (synthetic row, source="client")
    client_row = conn.execute(
        "SELECT id, name, follow_up_date FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    if client_row and client_row["follow_up_date"]:
        from datetime import date as _date
        fu = client_row["follow_up_date"]
        try:
            days_od = (_date.today() - _date.fromisoformat(fu)).days
        except (ValueError, TypeError):
            days_od = 0
        synth = {
            "activity_id": f"C{client_row['id']}",
            "subject": "Client-level follow-up",
            "activity_type": None,
            "follow_up_date": fu,
            "days_overdue": days_od,
            "disposition": "",
            "accountability": "my_action",
            "policy_id": None,
            "policy_uid": None,
            "policy_type": None,
            "client_id": client_row["id"],
            "client_name": client_row["name"],
            "source": "client",
            "is_on_issue": False,
            "linked_to_other_issue": None,
            "attach_target_issue_id": None,
        }
        direct_rows.append(synth)
        _count(synth)

    # ── Group 2..N: per open issue
    issue_groups: list[dict] = []
    for iss in open_issues:
        iss_id = iss["issue_id"]
        iss_rows: list[dict] = []
        for r in conn.execute(
            """SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                      a.disposition, a.policy_id, a.client_id, a.issue_id,
                      p.policy_uid, p.policy_type,
                      c.name AS client_name
               FROM activity_log a
               LEFT JOIN policies p ON p.id = a.policy_id
               LEFT JOIN clients c ON c.id = a.client_id
               WHERE a.issue_id = ?
                 AND a.follow_up_done = 0
                 AND a.follow_up_date IS NOT NULL
                 AND a.item_kind = 'followup'""",
            (iss_id,),
        ).fetchall():
            row = _open_task_row_from_activity(r)
            row["is_on_issue"] = True
            iss_rows.append(row)
            _count(row)
        if iss_rows:
            issue_groups.append({
                "key": f"issue:{iss_id}",
                "title": f"On {iss['issue_uid']}",
                "subtitle": iss["subject"],
                "rows": _sort_rows(iss_rows),
            })

    # ── Group last: loose_policies (open follow-ups on client's policies not
    # covered by any open issue)
    loose_rows: list[dict] = []
    client_policy_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM policies WHERE client_id = ? AND archived = 0",
            (client_id,),
        ).fetchall()
    ]
    uncovered = [pid for pid in client_policy_ids if pid not in covered_policy_ids]
    if uncovered:
        ph = ",".join("?" * len(uncovered))
        for r in conn.execute(
            f"""SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                       a.disposition, a.policy_id, a.client_id, a.issue_id,
                       p.policy_uid, p.policy_type,
                       c.name AS client_name
                FROM activity_log a
                JOIN policies p ON p.id = a.policy_id
                LEFT JOIN clients c ON c.id = a.client_id
                WHERE a.policy_id IN ({ph})
                  AND a.follow_up_done = 0
                  AND a.follow_up_date IS NOT NULL
                  AND a.item_kind = 'followup'""",  # noqa: S608
            uncovered,
        ).fetchall():
            row = _open_task_row_from_activity(r)
            # Resolve attach target: policy's open renewal issue (if exactly one)
            target = conn.execute(
                """SELECT id FROM activity_log
                   WHERE item_kind = 'issue'
                     AND policy_id = ?
                     AND issue_status NOT IN ('Resolved', 'Closed', 'Merged')
                     AND merged_into_id IS NULL""",
                (row["policy_id"],),
            ).fetchall()
            if len(target) == 1:
                row["attach_target_issue_id"] = target[0]["id"]
            loose_rows.append(row)
            _count(row)

    groups = []
    if direct_rows:
        groups.append({
            "key": "direct_client",
            "title": "Direct client follow-ups",
            "subtitle": None,
            "rows": _sort_rows(direct_rows),
        })
    groups.extend(issue_groups)
    if loose_rows:
        groups.append({
            "key": "loose_policies",
            "title": "Loose on other policies",
            "subtitle": "Not covered by any open issue",
            "rows": _sort_rows(loose_rows),
        })

    return {"groups": groups, "total": total, "overdue": overdue, "waiting": waiting}
```

- [ ] **Step 6.4: Run test — confirm pass**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_client_scope_groups_by_issue -v`
Expected: PASS.

- [ ] **Step 6.5: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): get_open_tasks client scope

Groups: direct_client → per open issue → loose_policies. Each per-issue
group is keyed 'issue:{id}' and ordered by severity. Direct client group
includes both activity_log client-level follow-ups and the synthetic
clients.follow_up_date scalar row."
```

---

## Task 7: `get_open_tasks()` — program scope

**Files:**
- Modify: `src/policydb/queries.py`
- Test: `tests/test_open_tasks.py`

- [ ] **Step 7.1: Write failing test**

Append to `tests/test_open_tasks.py`:

```python
def test_get_open_tasks_program_scope(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)

    # Program row
    conn.execute(
        "INSERT INTO programs (program_uid, client_id, name, effective_date, expiration_date) "
        "VALUES ('PGM-1', ?, 'Test Program', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pgm_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Child policies
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, program_id, policy_type, carrier, "
        "effective_date, expiration_date) "
        "VALUES ('POL-P1', ?, ?, 'GL', 'Test', '2026-01-01', '2027-01-01')",
        (cid, pgm_id),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Program-level renewal issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, program_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Program renewal', 'issue', 'ISS-PGM', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pgm_id),
    )
    iss_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Task attached to program issue
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'on program issue', '2026-05-01', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid, iss_id),
    )
    # Loose task on child policy
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose on child', '2026-05-05', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    conn.commit()

    result = get_open_tasks(conn, "program", pgm_id)
    keys = [g["key"] for g in result["groups"]]
    assert "on_program_issue" in keys
    assert "loose" in keys
    assert result["total"] == 2
```

- [ ] **Step 7.2: Run — expect failure**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_program_scope -v`
Expected: FAIL with ValueError or assertion.

- [ ] **Step 7.3: Implement `_open_tasks_for_program`**

In `src/policydb/queries.py`, update `get_open_tasks` dispatch:

```python
def get_open_tasks(conn, scope_type: str, scope_id: int) -> dict:
    if scope_type == "issue":
        return _open_tasks_for_issue(conn, scope_id)
    if scope_type == "client":
        return _open_tasks_for_client(conn, scope_id)
    if scope_type == "program":
        return _open_tasks_for_program(conn, scope_id)
    raise ValueError(f"Unsupported scope_type: {scope_type}")
```

Add:

```python
def _open_tasks_for_program(conn, program_id: int) -> dict:
    # Find all open issues linked directly to the program
    open_issues = conn.execute(
        """SELECT id AS issue_id, issue_uid, subject
           FROM activity_log
           WHERE item_kind = 'issue'
             AND program_id = ?
             AND issue_status NOT IN ('Resolved', 'Closed', 'Merged')
             AND merged_into_id IS NULL""",
        (program_id,),
    ).fetchall()
    issue_ids = [r["issue_id"] for r in open_issues]

    # Child policies
    child_policies = conn.execute(
        "SELECT id FROM policies WHERE program_id = ? AND archived = 0",
        (program_id,),
    ).fetchall()
    child_policy_ids = [r["id"] for r in child_policies]

    total = 0
    overdue = 0
    waiting = 0

    def _count(row):
        nonlocal total, overdue, waiting
        total += 1
        if row["days_overdue"] > 0:
            overdue += 1
        if row["accountability"] == "waiting_external":
            waiting += 1

    # Group 1: on_program_issue — any follow-up whose issue_id is in issue_ids
    on_issue_rows: list[dict] = []
    if issue_ids:
        ph = ",".join("?" * len(issue_ids))
        for r in conn.execute(
            f"""SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                       a.disposition, a.policy_id, a.client_id, a.issue_id,
                       p.policy_uid, p.policy_type,
                       c.name AS client_name
                FROM activity_log a
                LEFT JOIN policies p ON p.id = a.policy_id
                LEFT JOIN clients c ON c.id = a.client_id
                WHERE a.issue_id IN ({ph})
                  AND a.follow_up_done = 0
                  AND a.follow_up_date IS NOT NULL
                  AND a.item_kind = 'followup'""",  # noqa: S608
            issue_ids,
        ).fetchall():
            row = _open_task_row_from_activity(r)
            row["is_on_issue"] = True
            on_issue_rows.append(row)
            _count(row)

    # Group 2: loose — open follow-ups on child policies with issue_id NULL or
    # an issue NOT in our open_issues list
    loose_rows: list[dict] = []
    if child_policy_ids:
        ph = ",".join("?" * len(child_policy_ids))
        exclude_clause = ""
        params = list(child_policy_ids)
        if issue_ids:
            iph = ",".join("?" * len(issue_ids))
            exclude_clause = f" AND (a.issue_id IS NULL OR a.issue_id NOT IN ({iph}))"
            params.extend(issue_ids)
        else:
            exclude_clause = " AND a.issue_id IS NULL"
        for r in conn.execute(
            f"""SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                       a.disposition, a.policy_id, a.client_id, a.issue_id,
                       p.policy_uid, p.policy_type,
                       c.name AS client_name,
                       other.issue_uid AS other_issue_uid
                FROM activity_log a
                JOIN policies p ON p.id = a.policy_id
                LEFT JOIN clients c ON c.id = a.client_id
                LEFT JOIN activity_log other ON other.id = a.issue_id AND other.item_kind = 'issue'
                WHERE a.policy_id IN ({ph})
                  AND a.follow_up_done = 0
                  AND a.follow_up_date IS NOT NULL
                  AND a.item_kind = 'followup'{exclude_clause}""",  # noqa: S608
            params,
        ).fetchall():
            row = _open_task_row_from_activity(r)
            row["linked_to_other_issue"] = r["other_issue_uid"]
            if issue_ids and not row["linked_to_other_issue"]:
                # The program's own open issue is the attach target when unique
                row["attach_target_issue_id"] = issue_ids[0] if len(issue_ids) == 1 else None
            loose_rows.append(row)
            _count(row)

    groups = []
    if on_issue_rows:
        groups.append({
            "key": "on_program_issue",
            "title": "On program issue",
            "subtitle": None,
            "rows": _sort_rows(on_issue_rows),
        })
    if loose_rows:
        groups.append({
            "key": "loose",
            "title": "Loose on child policies",
            "subtitle": None,
            "rows": _sort_rows(loose_rows),
        })

    return {"groups": groups, "total": total, "overdue": overdue, "waiting": waiting}
```

- [ ] **Step 7.4: Run test — confirm pass**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_program_scope -v`
Expected: PASS.

- [ ] **Step 7.5: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): get_open_tasks program scope

Groups: on_program_issue → loose on child policies. Program issue is the
unique attach target for loose rows when exactly one open program issue
exists."
```

---

## Task 8: `get_open_tasks()` — policy scope

**Files:**
- Modify: `src/policydb/queries.py`
- Test: `tests/test_open_tasks.py`

- [ ] **Step 8.1: Write failing test**

```python
def test_get_open_tasks_policy_scope_single_group(tmp_db):
    from policydb.queries import get_open_tasks
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid, "POL-SOLO")
    _insert_followup(conn, cid, pid, "task-a", "2026-04-10")
    _insert_followup(conn, cid, pid, "task-b", "2026-04-20")
    conn.commit()

    result = get_open_tasks(conn, "policy", pid)
    assert len(result["groups"]) == 1
    assert result["groups"][0]["key"] == "on_policy"
    assert result["total"] == 2
    subjects = [r["subject"] for r in result["groups"][0]["rows"]]
    assert subjects == ["task-a", "task-b"]  # sort: earlier date first
```

- [ ] **Step 8.2: Run — expect failure**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_policy_scope_single_group -v`
Expected: FAIL with ValueError.

- [ ] **Step 8.3: Implement `_open_tasks_for_policy`**

Update `get_open_tasks` dispatch:

```python
def get_open_tasks(conn, scope_type: str, scope_id: int) -> dict:
    if scope_type == "issue":
        return _open_tasks_for_issue(conn, scope_id)
    if scope_type == "client":
        return _open_tasks_for_client(conn, scope_id)
    if scope_type == "program":
        return _open_tasks_for_program(conn, scope_id)
    if scope_type == "policy":
        return _open_tasks_for_policy(conn, scope_id)
    raise ValueError(f"Unsupported scope_type: {scope_type}")
```

Add:

```python
def _open_tasks_for_policy(conn, policy_id: int) -> dict:
    rows: list[dict] = []
    total = 0
    overdue = 0
    waiting = 0

    for r in conn.execute(
        """SELECT a.id, a.subject, a.activity_type, a.follow_up_date,
                  a.disposition, a.policy_id, a.client_id, a.issue_id,
                  p.policy_uid, p.policy_type,
                  c.name AS client_name,
                  other.issue_uid AS other_issue_uid
           FROM activity_log a
           JOIN policies p ON p.id = a.policy_id
           LEFT JOIN clients c ON c.id = a.client_id
           LEFT JOIN activity_log other ON other.id = a.issue_id AND other.item_kind = 'issue'
           WHERE a.policy_id = ?
             AND a.follow_up_done = 0
             AND a.follow_up_date IS NOT NULL
             AND a.item_kind = 'followup'""",
        (policy_id,),
    ).fetchall():
        row = _open_task_row_from_activity(r)
        row["linked_to_other_issue"] = r["other_issue_uid"]

        # Attach target: the policy's single open renewal issue if unique
        tgt = conn.execute(
            """SELECT id FROM activity_log
               WHERE item_kind = 'issue'
                 AND policy_id = ?
                 AND issue_status NOT IN ('Resolved', 'Closed', 'Merged')
                 AND merged_into_id IS NULL""",
            (policy_id,),
        ).fetchall()
        if len(tgt) == 1:
            row["attach_target_issue_id"] = tgt[0]["id"]

        rows.append(row)
        total += 1
        if row["days_overdue"] > 0:
            overdue += 1
        if row["accountability"] == "waiting_external":
            waiting += 1

    groups = []
    if rows:
        groups.append({
            "key": "on_policy",
            "title": "Open tasks on this policy",
            "subtitle": None,
            "rows": _sort_rows(rows),
        })
    return {"groups": groups, "total": total, "overdue": overdue, "waiting": waiting}
```

- [ ] **Step 8.4: Run test — confirm pass**

Run: `pytest tests/test_open_tasks.py::test_get_open_tasks_policy_scope_single_group -v`
Expected: PASS.

- [ ] **Step 8.5: Run full test file**

Run: `pytest tests/test_open_tasks.py -v`
Expected: all 8+ tests pass.

- [ ] **Step 8.6: Commit**

```bash
git add src/policydb/queries.py tests/test_open_tasks.py
git commit -m "feat(queries): get_open_tasks policy scope

Single flat group 'on_policy'. The full get_open_tasks API now covers all
four scope types (issue, client, program, policy)."
```

---

## Task 9: Route module skeleton + panel render endpoint

**Files:**
- Create: `src/policydb/web/routes/open_tasks.py`
- Modify: `src/policydb/web/app.py` (register router)
- Create: `tests/test_open_tasks_routes.py`

- [ ] **Step 9.1: Create the route module**

Create `src/policydb/web/routes/open_tasks.py`:

```python
"""Open Tasks Panel — shared command-center panel on issue/client/program/policy
pages. See docs/superpowers/specs/2026-04-14-open-tasks-panel-design.md."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from policydb.db import get_db
from policydb.queries import (
    create_followup_activity,
    get_open_tasks,
    sync_client_follow_up_date,
    sync_policy_follow_up_date,
)

router = APIRouter(prefix="/open-tasks", tags=["open-tasks"])
templates = Jinja2Templates(directory="src/policydb/web/templates")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _render_panel(
    request: Request,
    conn,
    scope_type: str,
    scope_id: int,
    toast_message: str | None = None,
    toast_kind: str = "success",
) -> HTMLResponse:
    data = get_open_tasks(conn, scope_type, scope_id)
    return templates.TemplateResponse(
        "_open_tasks_panel.html",
        {
            "request": request,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "data": data,
            "toast_message": toast_message,
            "toast_kind": toast_kind,
        },
    )


def _parse_activity_id(activity_id: str) -> tuple[str, int]:
    """Returns (kind, id). kind: 'activity' | 'policy' | 'client'."""
    if activity_id.startswith("P"):
        return ("policy", int(activity_id[1:]))
    if activity_id.startswith("C"):
        return ("client", int(activity_id[1:]))
    return ("activity", int(activity_id))


def _fetch_activity(conn, activity_id: int):
    row = conn.execute(
        "SELECT id, client_id, policy_id, follow_up_date, issue_id, subject "
        "FROM activity_log WHERE id = ?",
        (activity_id,),
    ).fetchone()
    return row


# ── Render ───────────────────────────────────────────────────────────────────

@router.get("/panel", response_class=HTMLResponse)
def panel(
    request: Request,
    scope_type: str,
    scope_id: int,
    conn=Depends(get_db),
):
    """Render the full Open Tasks panel for the given scope. Used for initial
    lazy-load from each page and as the target of every action's HTMX swap."""
    if scope_type not in ("issue", "client", "program", "policy"):
        raise HTTPException(status_code=400, detail="Invalid scope_type")
    return _render_panel(request, conn, scope_type, scope_id)
```

- [ ] **Step 9.2: Register router in `app.py`**

Open `src/policydb/web/app.py`, find where other routers are imported and included (search for `from policydb.web.routes import`), and add:

```python
from policydb.web.routes import open_tasks  # noqa: F401
# ... (later in the file, after other app.include_router calls)
app.include_router(open_tasks.router)
```

Match the exact existing pattern in the file — don't guess formatting; it likely uses a dotted import or conditional style.

- [ ] **Step 9.3: Create route test file**

Create `tests/test_open_tasks_routes.py`:

```python
"""Route tests for the Open Tasks panel endpoints."""
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


@pytest.fixture
def seeded(tmp_db):
    conn = get_connection()
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Route Co', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date) "
        "VALUES ('POL-R1', ?, 'GL', 'Test', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Test issue', 'issue', 'ISS-R', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'route task', '2026-04-15', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid, issue_id),
    )
    act_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return {"client_id": cid, "policy_id": pid, "issue_id": issue_id, "activity_id": act_id}


def test_panel_renders_for_issue_scope(app_client, seeded):
    r = app_client.get("/open-tasks/panel", params={"scope_type": "issue", "scope_id": seeded["issue_id"]})
    assert r.status_code == 200
    assert "route task" in r.text


def test_panel_rejects_invalid_scope_type(app_client, seeded):
    r = app_client.get("/open-tasks/panel", params={"scope_type": "bogus", "scope_id": 1})
    assert r.status_code == 400
```

- [ ] **Step 9.4: Run tests — expect one fails (panel template missing)**

Run: `pytest tests/test_open_tasks_routes.py -v`
Expected: `test_panel_rejects_invalid_scope_type` PASSES; `test_panel_renders_for_issue_scope` FAILS with `TemplateNotFound: _open_tasks_panel.html`.

The template comes in Task 10. Commit the skeleton now.

- [ ] **Step 9.5: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py src/policydb/web/app.py tests/test_open_tasks_routes.py
git commit -m "feat(routes): open_tasks router skeleton + panel render endpoint

New /open-tasks/panel endpoint dispatches to get_open_tasks() and renders
the shared panel template. Registered in app.py. Template is added next;
panel-render test will pass once _open_tasks_panel.html exists."
```

---

## Task 10: Panel + row templates

**Files:**
- Create: `src/policydb/web/templates/_open_tasks_panel.html`
- Create: `src/policydb/web/templates/_open_tasks_row.html`

- [ ] **Step 10.1: Create `_open_tasks_row.html`**

Create `src/policydb/web/templates/_open_tasks_row.html`:

```html
{# Single row in the Open Tasks panel.

   Expected context: row (a TaskRow dict), scope_type, scope_id.
#}
{% set grayed = row.linked_to_other_issue and not row.is_on_issue %}
<div class="group flex items-start gap-2 px-3 py-2 border-b border-gray-100 hover:bg-gray-50 {% if grayed %}opacity-60{% endif %}"
     data-activity-id="{{ row.activity_id }}">

  {# Policy UID pill #}
  <div class="shrink-0 pt-0.5">
    {% if row.policy_uid %}
    <a href="/policies/{{ row.policy_uid }}/edit" target="_blank"
       class="font-mono text-[10px] text-marsh hover:underline bg-gray-100 px-1.5 py-0.5 rounded">
      {{ row.policy_uid }}
    </a>
    {% else %}
    <span class="font-mono text-[10px] text-gray-400 bg-gray-50 px-1.5 py-0.5 rounded">—</span>
    {% endif %}
  </div>

  {# Subject + meta #}
  <div class="flex-1 min-w-0">
    <div class="text-sm text-gray-800 truncate">{{ row.subject }}</div>
    <div class="text-[10px] text-gray-400 mt-0.5 truncate">
      {{ row.policy_type or '—' }} &middot; {{ row.client_name }}{% if row.activity_type %} &middot; {{ row.activity_type }}{% endif %}
    </div>
  </div>

  {# Due date + overdue chip #}
  <div class="shrink-0 text-right pt-0.5">
    <div class="text-[11px] text-gray-600 tabular-nums">{{ row.follow_up_date }}</div>
    {% if row.days_overdue > 0 %}
    <div class="text-[10px] text-red-600 font-medium">{{ row.days_overdue }}d overdue</div>
    {% endif %}
  </div>

  {# Action buttons — always visible to avoid hover-discoverability problem #}
  <div class="shrink-0 flex items-center gap-1 pt-0.5">
    {# Mark done #}
    <button type="button"
            title="Mark done"
            hx-post="/open-tasks/{{ row.activity_id }}/done"
            hx-vals='{"return_scope_type": "{{ scope_type }}", "return_scope_id": {{ scope_id }}}'
            hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
            hx-swap="outerHTML"
            class="text-green-600 hover:bg-green-50 rounded p-1 text-xs">✓</button>

    {# Snooze #}
    <div class="relative" x-data="{open:false}">
      <button type="button" title="Snooze"
              onclick="this.nextElementSibling.classList.toggle('hidden')"
              class="text-blue-600 hover:bg-blue-50 rounded p-1 text-xs">💤</button>
      <div class="hidden absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded shadow-lg z-10 text-xs">
        {% for d in [1, 3, 7, 14] %}
        <button type="button"
                hx-post="/open-tasks/{{ row.activity_id }}/snooze"
                hx-vals='{"days": {{ d }}, "return_scope_type": "{{ scope_type }}", "return_scope_id": {{ scope_id }}}'
                hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
                hx-swap="outerHTML"
                class="block px-3 py-1 hover:bg-gray-50 w-full text-left">+{{ d }}d</button>
        {% endfor %}
      </div>
    </div>

    {# Waiting toggle #}
    {% if row.source == 'activity' %}
    <button type="button"
            title="Toggle My Move / Waiting"
            hx-post="/open-tasks/{{ row.activity_id }}/disposition"
            hx-vals='{"move": "{{ 'my' if row.accountability == 'waiting_external' else 'waiting' }}", "return_scope_type": "{{ scope_type }}", "return_scope_id": {{ scope_id }}}'
            hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
            hx-swap="outerHTML"
            class="rounded p-1 text-xs {% if row.accountability == 'waiting_external' %}text-amber-600 hover:bg-amber-50{% else %}text-gray-500 hover:bg-gray-100{% endif %}">
      {% if row.accountability == 'waiting_external' %}⏳{% else %}🏃{% endif %}
    </button>
    {% endif %}

    {# Log & close — activity-source only, on-issue rows only #}
    {% if row.source == 'activity' and row.is_on_issue %}
    <button type="button"
            title="Log & close (no follow-up)"
            hx-post="/open-tasks/{{ row.activity_id }}/log-close"
            hx-vals='{"return_scope_type": "{{ scope_type }}", "return_scope_id": {{ scope_id }}}'
            hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
            hx-swap="outerHTML"
            class="text-gray-500 hover:bg-gray-100 rounded p-1 text-xs">⊗</button>
    {% endif %}

    {# Attach / linked-to-other-issue #}
    {% if row.source == 'activity' and not row.is_on_issue %}
      {% if grayed %}
      <a href="/issues/{{ row.linked_to_other_issue }}"
         class="text-[10px] text-marsh hover:underline px-1">{{ row.linked_to_other_issue }}</a>
      {% elif row.attach_target_issue_id %}
      <button type="button"
              title="Attach to this issue"
              hx-post="/open-tasks/{{ row.activity_id }}/attach"
              hx-vals='{"target_issue_id": {{ row.attach_target_issue_id }}, "return_scope_type": "{{ scope_type }}", "return_scope_id": {{ scope_id }}}'
              hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
              hx-swap="outerHTML"
              class="text-marsh hover:bg-blue-50 rounded p-1 text-xs">🔗</button>
      {% endif %}
    {% endif %}

    {# Note #}
    {% if row.source == 'activity' %}
    <button type="button"
            title="Add note"
            onclick="const box = this.parentElement.parentElement.nextElementSibling; box.classList.toggle('hidden'); if (!box.classList.contains('hidden')) box.querySelector('textarea').focus();"
            class="text-gray-500 hover:bg-gray-100 rounded p-1 text-xs">💬</button>
    {% endif %}
  </div>
</div>

{# Note form (hidden until 💬 clicked) #}
{% if row.source == 'activity' %}
<div class="hidden px-3 py-2 bg-gray-50 border-b border-gray-100">
  <form hx-post="/open-tasks/{{ row.activity_id }}/note"
        hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
        hx-swap="outerHTML"
        class="flex gap-2">
    <input type="hidden" name="return_scope_type" value="{{ scope_type }}">
    <input type="hidden" name="return_scope_id" value="{{ scope_id }}">
    <textarea name="text" rows="2" required
              placeholder="Quick note — saves as a new activity linked to this task"
              class="flex-1 text-xs px-2 py-1 border border-gray-300 rounded"></textarea>
    <button type="submit" class="text-xs bg-marsh text-white px-3 py-1 rounded hover:bg-marsh-dark">Save</button>
    <button type="button" onclick="this.closest('div').classList.add('hidden')"
            class="text-xs text-gray-500 px-2">Cancel</button>
  </form>
</div>
{% endif %}
```

- [ ] **Step 10.2: Create `_open_tasks_panel.html`**

Create `src/policydb/web/templates/_open_tasks_panel.html`:

```html
{# Shared Open Tasks panel.

   Expected context:
     scope_type: 'issue' | 'client' | 'program' | 'policy'
     scope_id: int
     data: dict from get_open_tasks()
     toast_message, toast_kind: optional (for HX action responses)
#}
<div id="open-tasks-panel-{{ scope_type }}-{{ scope_id }}" class="card">
  <div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <span class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Open Tasks</span>
      <span class="text-[10px] text-gray-400">
        {{ data.total }} total{% if data.overdue %} &middot; <span class="text-red-600 font-medium">{{ data.overdue }} overdue</span>{% endif %}{% if data.waiting %} &middot; <span class="text-amber-600">{{ data.waiting }} waiting</span>{% endif %}
      </span>
    </div>
    <button type="button"
            hx-get="/open-tasks/new"
            hx-vals='{"scope_type": "{{ scope_type }}", "scope_id": {{ scope_id }}}'
            hx-target="#open-tasks-new-slot-{{ scope_type }}-{{ scope_id }}"
            hx-swap="innerHTML"
            class="text-xs bg-marsh text-white px-2 py-1 rounded hover:bg-marsh-dark">+ Add task</button>
  </div>

  <div id="open-tasks-new-slot-{{ scope_type }}-{{ scope_id }}"></div>

  {% if data.groups %}
    {% for group in data.groups %}
    <div class="border-b border-gray-100">
      <div class="px-4 py-1.5 bg-gray-25 text-[10px] font-semibold text-gray-500 uppercase tracking-wide">
        {{ group.title }} ({{ group.rows | length }}){% if group.subtitle %} <span class="text-gray-400 normal-case">— {{ group.subtitle }}</span>{% endif %}
      </div>
      {% for row in group.rows %}
        {% include "_open_tasks_row.html" %}
      {% endfor %}
    </div>
    {% endfor %}
  {% else %}
  <div class="px-4 py-6 text-center text-sm text-gray-400">
    ✓ Nothing outstanding — all tasks on this {{ scope_type }} are closed.
  </div>
  {% endif %}
</div>

{# Toast trigger — consumed by the global afterSwap listener in _toast.html #}
{% if toast_message %}
<div id="toast-trigger" hx-swap-oob="true"
     data-message="{{ toast_message }}" data-kind="{{ toast_kind or 'success' }}"></div>
{% endif %}
```

- [ ] **Step 10.3: Run panel render test**

Run: `pytest tests/test_open_tasks_routes.py::test_panel_renders_for_issue_scope -v`
Expected: PASS.

- [ ] **Step 10.4: Commit**

```bash
git add src/policydb/web/templates/_open_tasks_panel.html src/policydb/web/templates/_open_tasks_row.html
git commit -m "feat(ui): Open Tasks panel + row templates

Shared Jinja2 partials rendered by /open-tasks/panel. Groups come from
get_open_tasks() and iterate into _open_tasks_row.html per item. Six inline
actions per row with HTMX hx-post wiring; note textarea hidden until 💬
clicked. Panel id namespaced by scope for HTMX targeting."
```

---

## Task 11: Mark done action

**Files:**
- Modify: `src/policydb/web/routes/open_tasks.py`
- Test: `tests/test_open_tasks_routes.py`

- [ ] **Step 11.1: Write failing test**

Append to `tests/test_open_tasks_routes.py`:

```python
def test_mark_done_closes_activity_and_syncs_policy(app_client, seeded):
    conn = get_connection()
    # Seed policies.follow_up_date to match the activity
    conn.execute(
        "UPDATE policies SET follow_up_date='2026-04-15' WHERE id=?",
        (seeded["policy_id"],),
    )
    conn.commit()

    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/done",
        data={"return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT follow_up_done, auto_close_reason FROM activity_log WHERE id=?",
        (seeded["activity_id"],),
    ).fetchone()
    assert row["follow_up_done"] == 1
    assert row["auto_close_reason"] == "manual"

    pol = conn.execute(
        "SELECT follow_up_date FROM policies WHERE id=?", (seeded["policy_id"],)
    ).fetchone()
    assert pol["follow_up_date"] is None  # synced after mark-done
```

- [ ] **Step 11.2: Run — expect 404 or similar**

Run: `pytest tests/test_open_tasks_routes.py::test_mark_done_closes_activity_and_syncs_policy -v`
Expected: FAIL (route not defined).

- [ ] **Step 11.3: Implement the done action**

Append to `src/policydb/web/routes/open_tasks.py`:

```python
# ── Actions ──────────────────────────────────────────────────────────────────

@router.post("/{activity_id}/done", response_class=HTMLResponse)
def action_done(
    request: Request,
    activity_id: str,
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind == "activity":
        act = _fetch_activity(conn, rid)
        if not act:
            raise HTTPException(404, "Activity not found")
        conn.execute(
            """UPDATE activity_log
               SET follow_up_done = 1,
                   auto_close_reason = 'manual',
                   auto_closed_at = datetime('now'),
                   auto_closed_by = 'open_tasks_panel'
               WHERE id = ?""",
            (rid,),
        )
        if act["policy_id"]:
            sync_policy_follow_up_date(conn, act["policy_id"])
        elif act["client_id"]:
            sync_client_follow_up_date(conn, act["client_id"])
    elif kind == "policy":
        conn.execute(
            "UPDATE policies SET follow_up_date = NULL WHERE id = ?", (rid,)
        )
    elif kind == "client":
        conn.execute(
            "UPDATE clients SET follow_up_date = NULL WHERE id = ?", (rid,)
        )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Task marked done",
    )
```

- [ ] **Step 11.4: Run test**

Run: `pytest tests/test_open_tasks_routes.py::test_mark_done_closes_activity_and_syncs_policy -v`
Expected: PASS.

- [ ] **Step 11.5: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py tests/test_open_tasks_routes.py
git commit -m "feat(routes): open_tasks mark done action

POST /open-tasks/{activity_id}/done handles activity-, policy-, and
client-source rows. Activity-source path marks done + calls
sync_policy_follow_up_date / sync_client_follow_up_date for touch-once
consistency. Emits success toast via oob trigger."
```

---

## Task 12: Snooze action

**Files:**
- Modify: `src/policydb/web/routes/open_tasks.py`
- Test: `tests/test_open_tasks_routes.py`

- [ ] **Step 12.1: Write failing test**

Append to `tests/test_open_tasks_routes.py`:

```python
def test_snooze_shifts_date_by_days(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/snooze",
        data={"days": 7, "return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT follow_up_date FROM activity_log WHERE id=?", (seeded["activity_id"],)
    ).fetchone()
    # Original date was 2026-04-15; +7 = 2026-04-22
    assert row["follow_up_date"] == "2026-04-22"
```

- [ ] **Step 12.2: Run — expect failure**

Expected: FAIL (route missing).

- [ ] **Step 12.3: Implement snooze**

Append to `src/policydb/web/routes/open_tasks.py`:

```python
@router.post("/{activity_id}/snooze", response_class=HTMLResponse)
def action_snooze(
    request: Request,
    activity_id: str,
    days: int = Form(0),
    new_date: Optional[str] = Form(None),
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    def _compute_new_date(current: Optional[str]) -> Optional[str]:
        if new_date:
            return new_date
        if not days:
            return current
        try:
            base = date.fromisoformat(current) if current else date.today()
        except (ValueError, TypeError):
            base = date.today()
        return (base + timedelta(days=days)).isoformat()

    kind, rid = _parse_activity_id(activity_id)
    if kind == "activity":
        act = _fetch_activity(conn, rid)
        if not act:
            raise HTTPException(404, "Activity not found")
        updated = _compute_new_date(act["follow_up_date"])
        conn.execute(
            "UPDATE activity_log SET follow_up_date = ? WHERE id = ?",
            (updated, rid),
        )
        if act["policy_id"]:
            sync_policy_follow_up_date(conn, act["policy_id"])
        elif act["client_id"]:
            sync_client_follow_up_date(conn, act["client_id"])
    elif kind == "policy":
        row = conn.execute(
            "SELECT follow_up_date FROM policies WHERE id = ?", (rid,)
        ).fetchone()
        updated = _compute_new_date(row["follow_up_date"] if row else None)
        conn.execute(
            "UPDATE policies SET follow_up_date = ? WHERE id = ?", (updated, rid)
        )
    elif kind == "client":
        row = conn.execute(
            "SELECT follow_up_date FROM clients WHERE id = ?", (rid,)
        ).fetchone()
        updated = _compute_new_date(row["follow_up_date"] if row else None)
        conn.execute(
            "UPDATE clients SET follow_up_date = ? WHERE id = ?", (updated, rid)
        )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message=f"Snoozed +{days}d" if days else "Snoozed",
    )
```

- [ ] **Step 12.4: Run test**

Run: `pytest tests/test_open_tasks_routes.py::test_snooze_shifts_date_by_days -v`
Expected: PASS.

- [ ] **Step 12.5: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py tests/test_open_tasks_routes.py
git commit -m "feat(routes): open_tasks snooze action

POST /open-tasks/{activity_id}/snooze accepts days (int) or new_date (ISO).
Works on activity, policy, and client source rows. Policy sync runs for
activity-source rows so policies.follow_up_date never lags."
```

---

## Task 13: Waiting toggle + Log & close actions

**Files:**
- Modify: `src/policydb/web/routes/open_tasks.py`
- Test: `tests/test_open_tasks_routes.py`

- [ ] **Step 13.1: Write failing tests**

Append:

```python
def test_disposition_toggles_to_waiting(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/disposition",
        data={"move": "waiting", "return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200
    conn = get_connection()
    row = conn.execute(
        "SELECT disposition FROM activity_log WHERE id=?", (seeded["activity_id"],)
    ).fetchone()
    # First waiting_external disposition label from config should be set
    assert row["disposition"]


def test_log_close_clears_date_and_marks_done(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/log-close",
        data={"return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200
    conn = get_connection()
    row = conn.execute(
        "SELECT follow_up_done, follow_up_date FROM activity_log WHERE id=?",
        (seeded["activity_id"],),
    ).fetchone()
    assert row["follow_up_done"] == 1
    assert row["follow_up_date"] is None
```

- [ ] **Step 13.2: Run — expect failure**

- [ ] **Step 13.3: Implement disposition + log-close**

Append to `src/policydb/web/routes/open_tasks.py`:

```python
@router.post("/{activity_id}/disposition", response_class=HTMLResponse)
def action_disposition(
    request: Request,
    activity_id: str,
    move: str = Form(...),  # "my" or "waiting"
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Disposition only supported on activity-source rows")

    from policydb.config import cfg
    label = ""
    if move == "waiting":
        for d in cfg.get("follow_up_dispositions", []):
            if d.get("accountability") == "waiting_external":
                label = d.get("label", "Waiting on Response")
                break
    conn.execute(
        "UPDATE activity_log SET disposition = ? WHERE id = ?",
        (label or None, rid),
    )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Marked waiting" if move == "waiting" else "Marked my move",
    )


@router.post("/{activity_id}/log-close", response_class=HTMLResponse)
def action_log_close(
    request: Request,
    activity_id: str,
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Log & close only supported on activity-source rows")
    act = _fetch_activity(conn, rid)
    if not act:
        raise HTTPException(404, "Activity not found")
    conn.execute(
        """UPDATE activity_log
           SET follow_up_done = 1,
               follow_up_date = NULL,
               auto_close_reason = 'manual',
               auto_closed_at = datetime('now'),
               auto_closed_by = 'open_tasks_panel'
           WHERE id = ?""",
        (rid,),
    )
    if act["policy_id"]:
        sync_policy_follow_up_date(conn, act["policy_id"])
    elif act["client_id"]:
        sync_client_follow_up_date(conn, act["client_id"])
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Logged & closed",
    )
```

- [ ] **Step 13.4: Run tests**

Run: `pytest tests/test_open_tasks_routes.py -v -k "disposition or log_close"`
Expected: 2 passes.

- [ ] **Step 13.5: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py tests/test_open_tasks_routes.py
git commit -m "feat(routes): disposition toggle + log-close actions

Disposition toggle maps 'waiting' to first config entry with
accountability='waiting_external'. Log-close clears follow_up_date AND
marks done — the 'log without follow-up' pattern. Both fire the scalar
sync helper for touch-once consistency."
```

---

## Task 14: Attach action

**Files:**
- Modify: `src/policydb/web/routes/open_tasks.py`
- Test: `tests/test_open_tasks_routes.py`

- [ ] **Step 14.1: Write failing test**

Append:

```python
def test_attach_sets_issue_id(app_client, seeded):
    # Create a second issue and a loose activity, then attach it
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Second issue', 'issue', 'ISS-B', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), seeded["client_id"], seeded["policy_id"]),
    )
    iss_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose-one', '2026-05-01', 0, 'followup', 'Grant')",
        (date.today().isoformat(), seeded["client_id"], seeded["policy_id"]),
    )
    loose_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    r = app_client.post(
        f"/open-tasks/{loose_id}/attach",
        data={"target_issue_id": iss_b, "return_scope_type": "issue", "return_scope_id": iss_b},
    )
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT issue_id FROM activity_log WHERE id=?", (loose_id,)
    ).fetchone()
    assert row["issue_id"] == iss_b
```

- [ ] **Step 14.2: Run — expect failure**

- [ ] **Step 14.3: Implement attach**

Append:

```python
@router.post("/{activity_id}/attach", response_class=HTMLResponse)
def action_attach(
    request: Request,
    activity_id: str,
    target_issue_id: int = Form(...),
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Attach only supported on activity-source rows")
    # Verify target is a valid issue row
    iss = conn.execute(
        "SELECT id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (target_issue_id,),
    ).fetchone()
    if not iss:
        raise HTTPException(404, "Target issue not found")
    conn.execute(
        "UPDATE activity_log SET issue_id = ? WHERE id = ?",
        (target_issue_id, rid),
    )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Attached to issue",
    )
```

- [ ] **Step 14.4: Run test — confirm pass**

- [ ] **Step 14.5: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py tests/test_open_tasks_routes.py
git commit -m "feat(routes): attach to issue action

POST /open-tasks/{activity_id}/attach sets activity_log.issue_id to the
verified target. No supersession — attach is a link change, not a new
follow-up."
```

---

## Task 15: Note action

**Files:**
- Modify: `src/policydb/web/routes/open_tasks.py`
- Test: `tests/test_open_tasks_routes.py`

- [ ] **Step 15.1: Write failing test**

Append:

```python
def test_note_creates_sibling_activity(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/note",
        data={
            "text": "Quick FYI",
            "return_scope_type": "issue",
            "return_scope_id": seeded["issue_id"],
        },
    )
    assert r.status_code == 200

    conn = get_connection()
    # Original task should still be open
    orig = conn.execute(
        "SELECT follow_up_done FROM activity_log WHERE id=?",
        (seeded["activity_id"],),
    ).fetchone()
    assert orig["follow_up_done"] == 0

    # A new sibling note activity should exist
    note = conn.execute(
        """SELECT id, subject, activity_type, follow_up_done, follow_up_date, issue_id
           FROM activity_log
           WHERE subject = 'Quick FYI' AND activity_type = 'Note'"""
    ).fetchone()
    assert note is not None
    assert note["follow_up_done"] == 1
    assert note["follow_up_date"] is None
    assert note["issue_id"] == seeded["issue_id"]
```

- [ ] **Step 15.2: Run — expect failure**

- [ ] **Step 15.3: Implement note**

Append:

```python
@router.post("/{activity_id}/note", response_class=HTMLResponse)
def action_note(
    request: Request,
    activity_id: str,
    text: str = Form(...),
    return_scope_type: str = Form(...),
    return_scope_id: int = Form(...),
    conn=Depends(get_db),
):
    kind, rid = _parse_activity_id(activity_id)
    if kind != "activity":
        raise HTTPException(400, "Note only supported on activity-source rows")
    if not text.strip():
        raise HTTPException(400, "Note text required")
    act = _fetch_activity(conn, rid)
    if not act:
        raise HTTPException(404, "Parent activity not found")

    create_followup_activity(
        conn,
        client_id=act["client_id"],
        policy_id=act["policy_id"],
        issue_id=act["issue_id"],
        subject=text.strip(),
        activity_type="Note",
        follow_up_date=None,
        follow_up_done=True,
        disposition="",
    )
    conn.commit()
    return _render_panel(
        request, conn, return_scope_type, return_scope_id,
        toast_message="Note saved",
    )
```

- [ ] **Step 15.4: Run test**

- [ ] **Step 15.5: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py tests/test_open_tasks_routes.py
git commit -m "feat(routes): note action creates sibling activity

POST /open-tasks/{activity_id}/note creates a new activity_log row with
activity_type='Note', follow_up_done=1, no follow_up_date, inheriting the
parent's client/policy/issue linkage. Parent task is untouched — activities
are immutable once logged."
```

---

## Task 16: + Add task create form + endpoint

**Files:**
- Create: `src/policydb/web/templates/_open_tasks_new_form.html`
- Modify: `src/policydb/web/routes/open_tasks.py`
- Test: `tests/test_open_tasks_routes.py`

- [ ] **Step 16.1: Create the form template**

Create `src/policydb/web/templates/_open_tasks_new_form.html`:

```html
{# Inline + Add task form. Rendered by GET /open-tasks/new into the panel header slot.

   Context: scope_type, scope_id, policy_options (list of (id, uid, policy_type))
#}
<form class="px-4 py-3 bg-gray-50 border-b border-gray-200"
      hx-post="/open-tasks/new"
      hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
      hx-swap="outerHTML">
  <input type="hidden" name="scope_type" value="{{ scope_type }}">
  <input type="hidden" name="scope_id" value="{{ scope_id }}">

  <div class="grid grid-cols-1 md:grid-cols-6 gap-2 items-end">
    <div class="md:col-span-2">
      <label class="text-[10px] text-gray-500 uppercase tracking-wide block">Subject</label>
      <input name="subject" required autofocus
             class="w-full text-xs px-2 py-1 border border-gray-300 rounded">
    </div>

    {% if policy_options and policy_options | length > 1 %}
    <div>
      <label class="text-[10px] text-gray-500 uppercase tracking-wide block">Policy</label>
      <select name="policy_id" class="w-full text-xs px-2 py-1 border border-gray-300 rounded">
        <option value="">— (no specific policy)</option>
        {% for opt in policy_options %}
        <option value="{{ opt[0] }}">{{ opt[1] }} — {{ opt[2] or '' }}</option>
        {% endfor %}
      </select>
    </div>
    {% elif policy_options and policy_options | length == 1 %}
    <input type="hidden" name="policy_id" value="{{ policy_options[0][0] }}">
    {% endif %}

    <div>
      <label class="text-[10px] text-gray-500 uppercase tracking-wide block">Follow-up</label>
      <input name="follow_up_date" type="date" required
             class="w-full text-xs px-2 py-1 border border-gray-300 rounded">
    </div>

    <div>
      <label class="text-[10px] text-gray-500 uppercase tracking-wide block">Disposition</label>
      <select name="disposition" class="w-full text-xs px-2 py-1 border border-gray-300 rounded">
        <option value="">My move</option>
        <option value="waiting">Waiting</option>
      </select>
    </div>

    <div class="flex gap-2">
      <button type="submit" class="text-xs bg-marsh text-white px-3 py-1.5 rounded hover:bg-marsh-dark">Save</button>
      <button type="button"
              hx-get="/open-tasks/panel"
              hx-vals='{"scope_type": "{{ scope_type }}", "scope_id": {{ scope_id }}}'
              hx-target="#open-tasks-panel-{{ scope_type }}-{{ scope_id }}"
              hx-swap="outerHTML"
              class="text-xs text-gray-500 px-2">Cancel</button>
    </div>
  </div>
</form>
```

- [ ] **Step 16.2: Write failing test**

Append to `tests/test_open_tasks_routes.py`:

```python
def test_new_task_create_issue_scope(app_client, seeded):
    r = app_client.post(
        "/open-tasks/new",
        data={
            "scope_type": "issue",
            "scope_id": seeded["issue_id"],
            "subject": "Net new task",
            "policy_id": seeded["policy_id"],
            "follow_up_date": "2026-05-30",
            "disposition": "",
        },
    )
    assert r.status_code == 200

    conn = get_connection()
    new = conn.execute(
        "SELECT id, issue_id, subject FROM activity_log WHERE subject = 'Net new task'"
    ).fetchone()
    assert new is not None
    assert new["issue_id"] == seeded["issue_id"]


def test_new_task_form_get_renders(app_client, seeded):
    r = app_client.get(
        "/open-tasks/new",
        params={"scope_type": "issue", "scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200
    assert "hx-post=\"/open-tasks/new\"" in r.text
```

- [ ] **Step 16.3: Run — expect failure**

- [ ] **Step 16.4: Implement the GET form + POST create**

Append to `src/policydb/web/routes/open_tasks.py` (**before** the action routes that match `/{activity_id}/...` to preserve route ordering):

Find the line `# ── Actions ──` in open_tasks.py and insert the following **above** it (right after the `panel` endpoint):

```python
# ── New task (create) ────────────────────────────────────────────────────────
# IMPORTANT: these literal routes must be declared BEFORE /{activity_id}/...

def _policy_options_for_scope(conn, scope_type: str, scope_id: int) -> list[tuple]:
    """Return list of (policy_id, policy_uid, policy_type) for the form's
    policy dropdown, scoped to the current context."""
    if scope_type == "issue":
        rows = conn.execute(
            """SELECT DISTINCT p.id, p.policy_uid, p.policy_type
               FROM v_issue_policy_coverage ipc
               JOIN policies p ON p.id = ipc.policy_id
               WHERE ipc.issue_id = ? AND p.archived = 0
               ORDER BY p.policy_uid""",
            (scope_id,),
        ).fetchall()
    elif scope_type == "client":
        rows = conn.execute(
            """SELECT id, policy_uid, policy_type FROM policies
               WHERE client_id = ? AND archived = 0
               ORDER BY policy_uid""",
            (scope_id,),
        ).fetchall()
    elif scope_type == "program":
        rows = conn.execute(
            """SELECT id, policy_uid, policy_type FROM policies
               WHERE program_id = ? AND archived = 0
               ORDER BY policy_uid""",
            (scope_id,),
        ).fetchall()
    elif scope_type == "policy":
        rows = conn.execute(
            "SELECT id, policy_uid, policy_type FROM policies WHERE id = ?",
            (scope_id,),
        ).fetchall()
    else:
        rows = []
    return [(r["id"], r["policy_uid"], r["policy_type"]) for r in rows]


def _resolve_scope_context(conn, scope_type: str, scope_id: int) -> dict:
    """Returns {'client_id': int, 'issue_id': int|None, 'policy_id': int|None}
    for creating a new activity under this scope when no policy is selected."""
    if scope_type == "issue":
        iss = conn.execute(
            "SELECT client_id, program_id FROM activity_log WHERE id = ?",
            (scope_id,),
        ).fetchone()
        return {"client_id": iss["client_id"] if iss else None, "issue_id": scope_id, "policy_id": None}
    if scope_type == "client":
        return {"client_id": scope_id, "issue_id": None, "policy_id": None}
    if scope_type == "program":
        pgm = conn.execute(
            "SELECT client_id FROM programs WHERE id = ?", (scope_id,)
        ).fetchone()
        return {"client_id": pgm["client_id"] if pgm else None, "issue_id": None, "policy_id": None}
    if scope_type == "policy":
        pol = conn.execute(
            "SELECT client_id FROM policies WHERE id = ?", (scope_id,)
        ).fetchone()
        return {"client_id": pol["client_id"] if pol else None, "issue_id": None, "policy_id": scope_id}
    return {"client_id": None, "issue_id": None, "policy_id": None}


@router.get("/new", response_class=HTMLResponse)
def new_task_form(
    request: Request,
    scope_type: str,
    scope_id: int,
    conn=Depends(get_db),
):
    policy_options = _policy_options_for_scope(conn, scope_type, scope_id)
    return templates.TemplateResponse(
        "_open_tasks_new_form.html",
        {
            "request": request,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "policy_options": policy_options,
        },
    )


@router.post("/new", response_class=HTMLResponse)
def new_task_create(
    request: Request,
    scope_type: str = Form(...),
    scope_id: int = Form(...),
    subject: str = Form(...),
    policy_id: Optional[int] = Form(None),
    follow_up_date: str = Form(...),
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    ctx = _resolve_scope_context(conn, scope_type, scope_id)
    if not ctx["client_id"]:
        raise HTTPException(400, "Could not resolve client for scope")

    # Resolve disposition label
    from policydb.config import cfg
    disp_label = ""
    if disposition == "waiting":
        for d in cfg.get("follow_up_dispositions", []):
            if d.get("accountability") == "waiting_external":
                disp_label = d.get("label", "Waiting on Response")
                break

    # Pick policy: form value > scope default
    effective_policy_id = policy_id if policy_id else ctx["policy_id"]

    create_followup_activity(
        conn,
        client_id=ctx["client_id"],
        policy_id=effective_policy_id,
        issue_id=ctx["issue_id"],
        subject=subject.strip(),
        activity_type="Task",
        follow_up_date=follow_up_date,
        follow_up_done=False,
        disposition=disp_label,
    )
    conn.commit()
    return _render_panel(
        request, conn, scope_type, scope_id,
        toast_message="Task added",
    )
```

- [ ] **Step 16.5: Run tests**

Run: `pytest tests/test_open_tasks_routes.py -v -k "new_task"`
Expected: 2 passes.

- [ ] **Step 16.6: Run full test file**

Run: `pytest tests/test_open_tasks_routes.py tests/test_open_tasks.py -v`
Expected: all tests pass.

- [ ] **Step 16.7: Commit**

```bash
git add src/policydb/web/routes/open_tasks.py src/policydb/web/templates/_open_tasks_new_form.html tests/test_open_tasks_routes.py
git commit -m "feat(routes): + Add task create form + POST endpoint

GET /open-tasks/new returns the inline quick-log form with a policy dropdown
scoped to the current context (issue → covered policies, client → all client
policies, program → child policies, policy → hidden). POST /open-tasks/new
calls the shared create_followup_activity helper and re-renders the panel.

Route ordering: /open-tasks/new literal is declared before
/open-tasks/{activity_id}/... so 'new' isn't captured as an id (per
feedback_route_ordering_literals_first)."
```

---

## Task 17: Integrate panel on issue detail page + remove Scope Rollup follow-ups

**Files:**
- Modify: `src/policydb/web/templates/issues/detail.html`
- Modify: `src/policydb/web/templates/issues/_scope_rollup.html`

- [ ] **Step 17.1: Read issues/detail.html to find insertion point**

Read `src/policydb/web/templates/issues/detail.html`. Locate where `_scope_rollup.html` is included. The panel goes directly above that include.

- [ ] **Step 17.2: Insert the panel**

Add immediately before the `{% include "issues/_scope_rollup.html" %}` line:

```html
{# Open Tasks panel — command-center for this renewal #}
<div class="mb-4">
  {% include "_open_tasks_panel.html" with context %}
</div>
```

**Important:** The route module renders the panel by calling `_render_panel()`, which passes `scope_type`, `scope_id`, and `data`. For the inline include on issue detail, the issue route handler must also provide these. Update `src/policydb/web/routes/issues.py` — find the function that renders `issues/detail.html` and add:

```python
from policydb.queries import get_open_tasks
# ... inside the detail handler, near other context building:
open_tasks_data = get_open_tasks(conn, "issue", issue["id"])
```

Then add to the template context dict:

```python
"scope_type": "issue",
"scope_id": issue["id"],
"data": open_tasks_data,
```

- [ ] **Step 17.3: Remove the "Open Follow-ups" subsection from Scope Rollup**

Open `src/policydb/web/templates/issues/_scope_rollup.html`. Delete the block from `{# ── Open Follow-ups sub-section ── #}` through its closing `{% endif %}` (approximately lines 192–216 in the current file — confirm by reading).

The delete target (verbatim from the current file):

```html
  {# ── Open Follow-ups sub-section ── #}
  {% if _fu.total %}
  <div class="px-4 py-3 border-b border-gray-100">
    <div class="flex items-center justify-between mb-2">
      <span class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">Open Follow-ups</span>
      <span class="text-[10px] text-gray-400">
        {{ _fu.total }} total{% if _fu.overdue %} &middot; <span class="text-red-600 font-medium">{{ _fu.overdue }} overdue</span>{% endif %}
      </span>
    </div>
    <div class="space-y-1">
      {% for f in _fu.by_policy %}
      <div class="flex items-center gap-2 text-xs">
        <a href="/policies/{{ f.policy_uid }}/edit" class="font-mono text-[10px] text-marsh hover:underline shrink-0">{{ f.policy_uid }}</a>
        <span class="text-[9px] text-gray-400 uppercase tracking-wide shrink-0">{{ f.source }}</span>
        <span class="text-gray-700 truncate flex-1">{{ f.subject }}</span>
        {% if f.days_overdue > 0 %}
        <span class="text-red-600 font-medium text-[10px] shrink-0">⚠ {{ f.days_overdue }}d overdue</span>
        {% else %}
        <span class="text-gray-500 text-[10px] shrink-0">Due {{ f.follow_up_date }}</span>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
```

- [ ] **Step 17.4: Manual browser verification**

Start server on random port >8005: `~/.policydb/venv/bin/policydb serve --port 8125 --reload`
Open an issue with open follow-ups. Verify:
- New Open Tasks panel renders above Scope Rollup
- Scope Rollup no longer has the duplicate "Open Follow-ups" subsection
- Mark-done button actually closes the activity (refresh and confirm)

- [ ] **Step 17.5: Commit**

```bash
git add src/policydb/web/templates/issues/detail.html src/policydb/web/templates/issues/_scope_rollup.html src/policydb/web/routes/issues.py
git commit -m "feat(issues): integrate Open Tasks panel on issue detail

Panel renders above Scope Rollup card on every issue detail page.
Removed the old read-only 'Open Follow-ups' subsection from
_scope_rollup.html — its data is now editable in the new panel."
```

---

## Task 18: Integrate panel on client page

**Files:**
- Modify: `src/policydb/web/routes/clients.py` (add scope vars to client detail context)
- Modify: `src/policydb/web/templates/clients/_tab_overview.html` (insert panel)
- Modify: `src/policydb/web/templates/clients/_sticky_sidebar.html` (remove dup follow-up list)

- [ ] **Step 18.1: Locate the client detail route handler**

Search `src/policydb/web/routes/clients.py` for the function rendering the main client page (`clients/edit.html` or `detail.html`). Add to its context dict:

```python
from policydb.queries import get_open_tasks
# ...
"scope_type": "client",
"scope_id": client_id,
"data": get_open_tasks(conn, "client", client_id),
```

- [ ] **Step 18.2: Insert the panel (lazy-loaded) in the Overview tab template**

Open `src/policydb/web/templates/clients/_tab_overview.html`. At the very top of the tab body (before the first existing card), add:

```html
{# Open Tasks panel — aggregates outstanding follow-ups across all client policies #}
<div class="mb-4"
     hx-get="/open-tasks/panel"
     hx-vals='{"scope_type": "client", "scope_id": {{ client.id }}}'
     hx-trigger="load"
     hx-swap="outerHTML">
  <div class="card px-4 py-6 text-center text-xs text-gray-400">Loading open tasks…</div>
</div>
```

The lazy `hx-trigger="load"` fires as soon as the element is in the DOM, so the panel appears on first Overview tab render.

- [ ] **Step 18.3: Reconcile the sticky sidebar**

Read `src/policydb/web/templates/clients/_sticky_sidebar.html`. Locate any section that lists open follow-ups (search for `follow_up` or `followup`). Replace it with a single summary/link:

```html
{# Follow-ups summary — full list lives in the Open Tasks panel on Overview tab #}
{% if open_tasks_total and open_tasks_total > 0 %}
<div class="px-3 py-2 border-t border-gray-200">
  <a href="#open-tasks-panel-client-{{ client.id }}" class="flex items-center justify-between text-xs hover:bg-gray-50 rounded px-1 py-1">
    <span class="text-gray-600">Open tasks</span>
    <span class="font-semibold {% if open_tasks_overdue %}text-red-600{% else %}text-gray-800{% endif %}">
      {{ open_tasks_total }}{% if open_tasks_overdue %} &middot; {{ open_tasks_overdue }} overdue{% endif %}
    </span>
  </a>
</div>
{% endif %}
```

In the client detail route handler (Step 18.1), add the counts to the context:

```python
_ot = get_open_tasks(conn, "client", client_id)
# ...
"open_tasks_total": _ot["total"],
"open_tasks_overdue": _ot["overdue"],
```

(If the sidebar template doesn't currently render a follow-up list, only add the summary link — don't delete code that doesn't exist.)

- [ ] **Step 18.4: Browser QA**

Open a client with multiple open follow-ups across policies. Verify:
- Panel lazy-loads at top of Overview tab
- Groups correctly: direct_client → per-issue → loose_policies
- Sticky sidebar shows count link, no duplicate list
- Clicking the sidebar link scrolls to the panel

- [ ] **Step 18.5: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_tab_overview.html src/policydb/web/templates/clients/_sticky_sidebar.html
git commit -m "feat(clients): integrate Open Tasks panel on client page

Lazy-loaded panel at top of Overview tab. Sticky sidebar follow-up list
consolidated into a single count link into the panel (touch-once — one
interaction surface, no duplicate triage)."
```

---

## Task 19: Integrate panel on program page

**Files:**
- Modify: `src/policydb/web/routes/programs.py`
- Modify: `src/policydb/web/templates/programs/_tab_overview.html`

- [ ] **Step 19.1: Add scope context to program detail handler**

In `src/policydb/web/routes/programs.py`, find the program detail / overview route. Add to context:

```python
"scope_type": "program",
"scope_id": program["id"],
```

- [ ] **Step 19.2: Insert lazy panel at top of program overview**

In `src/policydb/web/templates/programs/_tab_overview.html`, add at top:

```html
<div class="mb-4"
     hx-get="/open-tasks/panel"
     hx-vals='{"scope_type": "program", "scope_id": {{ program.id }}}'
     hx-trigger="load"
     hx-swap="outerHTML">
  <div class="card px-4 py-6 text-center text-xs text-gray-400">Loading open tasks…</div>
</div>
```

- [ ] **Step 19.3: Browser QA**

Open a program page with outstanding follow-ups. Verify panel loads with `on_program_issue` + `loose` groups.

- [ ] **Step 19.4: Commit**

```bash
git add src/policydb/web/routes/programs.py src/policydb/web/templates/programs/_tab_overview.html
git commit -m "feat(programs): integrate Open Tasks panel on program overview

Lazy-loaded panel above Scope Rollup on program detail. Groups:
on_program_issue → loose on child policies."
```

---

## Task 20: Integrate panel on policy edit page

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/templates/policies/edit.html`

- [ ] **Step 20.1: Add scope context to policy edit handler**

In `src/policydb/web/routes/policies.py`, find the handler rendering `policies/edit.html`. Add:

```python
"scope_type": "policy",
"scope_id": policy["id"],
```

- [ ] **Step 20.2: Insert inline panel in edit template**

In `src/policydb/web/templates/policies/edit.html`, find the activity thread section (search for "activity" or "scratchpad"). Add immediately above it:

```html
<div class="mb-4">
  {% include "_open_tasks_panel.html" with context %}
</div>
```

And in the route context add `data = get_open_tasks(conn, "policy", policy["id"])` alongside `scope_type` and `scope_id`.

- [ ] **Step 20.3: Browser QA**

Open a policy edit page with open follow-ups. Verify the single flat `on_policy` group renders correctly.

- [ ] **Step 20.4: Commit**

```bash
git add src/policydb/web/routes/policies.py src/policydb/web/templates/policies/edit.html
git commit -m "feat(policies): integrate Open Tasks panel on policy edit

Inline panel above the activity thread. Single flat 'on_policy' group
lists every open follow-up on this policy regardless of issue linkage."
```

---

## Task 21: Thread history filter — apply `filter_thread_for_history`

**Files:**
- Modify: `src/policydb/web/routes/issues.py`
- Modify: `src/policydb/web/routes/clients.py`
- Modify: `src/policydb/web/routes/programs.py`
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 21.1: Grep for activity thread queries on each page**

For each of the four route modules, find where the activity thread rows are fetched (look for `SELECT ... FROM activity_log`). Wrap the result with `filter_thread_for_history`.

Example for `issues.py`:

```python
from policydb.queries import filter_thread_for_history
# ...
thread_rows = conn.execute(
    "SELECT ... FROM activity_log WHERE issue_id = ? ORDER BY activity_date DESC",
    (issue_id,),
).fetchall()
thread_rows = filter_thread_for_history([dict(r) for r in thread_rows])
```

Apply the same pattern in `clients.py` (client activity tab handler), `programs.py` (program activity tab handler), and `policies.py` (any activity list on the policy edit page).

- [ ] **Step 21.2: Browser QA**

Verify on all four pages that:
- Open follow-ups appear ONLY in the Open Tasks panel
- Closed activities, notes, and non-follow-up items still appear in the activity thread below

- [ ] **Step 21.3: Commit**

```bash
git add src/policydb/web/routes/issues.py src/policydb/web/routes/clients.py src/policydb/web/routes/programs.py src/policydb/web/routes/policies.py
git commit -m "refactor(threads): filter open tasks out of activity history views

All four activity thread views (issue, client, program, policy) now call
filter_thread_for_history() to drop rows owned by the Open Tasks panel.
One rule, one helper — no duplicate listings between panel and thread."
```

---

## Task 22: Refactor existing quick-log endpoints to use `create_followup_activity`

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/routes/activities.py`
- Modify: `src/policydb/web/routes/clients.py`
- Modify: `src/policydb/web/routes/inbox.py`

- [ ] **Step 22.1: Map the existing INSERT sites**

Run:

```bash
grep -n "INSERT INTO activity_log" src/policydb/web/routes/policies.py src/policydb/web/routes/activities.py src/policydb/web/routes/clients.py src/policydb/web/routes/inbox.py
```

You'll see ~10 sites. Each handles a quick-log action (policy row log, opportunity log, activity create, inbox process, etc.).

- [ ] **Step 22.2: Refactor one site at a time**

For each INSERT site whose purpose is "create a follow-up activity from user input":

1. Replace the raw `INSERT INTO activity_log (...) VALUES (...)` plus any following `supersede_followups()` call with a single `create_followup_activity(...)` call.
2. Keep the handler's surrounding logic (response rendering, redirect, etc.) unchanged.
3. Skip sites that are creating issue headers (`item_kind='issue'`) or other non-follow-up rows — those have different shapes.

Example transformation (from `policies.py` around line 664):

Before:
```python
if follow_up_date:
    from policydb.queries import supersede_followups
    supersede_followups(conn, policy_id, follow_up_date)

account_exec = cfg.get("default_account_exec", "Grant")
conn.execute(
    """INSERT INTO activity_log
       (activity_date, client_id, policy_id, activity_type, subject, details,
        follow_up_date, account_exec, duration_hours, issue_id)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (
        _date.today().isoformat(), client_id, policy_id,
        activity_type, subject, details or None,
        follow_up_date or None, account_exec, round_duration(duration_hours),
        issue_id or None,
    ),
)
conn.commit()
```

After:
```python
from policydb.queries import create_followup_activity
create_followup_activity(
    conn,
    client_id=client_id,
    policy_id=policy_id,
    issue_id=issue_id,
    subject=subject,
    activity_type=activity_type,
    follow_up_date=follow_up_date or None,
    follow_up_done=False,
    disposition="",
    details=details or None,
    duration_hours=round_duration(duration_hours),
)
conn.commit()
```

The helper handles supersession and auto-linking internally — delete those calls from the handler.

- [ ] **Step 22.3: Run the whole test suite**

Run: `pytest tests/ -v -x`
Expected: all tests pass. If any existing test fails, the refactor broke behavior — fix before continuing.

- [ ] **Step 22.4: Manual QA on quick-log forms**

Start the server. Test:
- Policy edit → log new activity with follow-up date
- Opportunity log
- Inbox process → create follow-up
- Action Center follow-up re-diary

Each should still create the activity and still propagate to the Open Tasks panel.

- [ ] **Step 22.5: Commit**

```bash
git add src/policydb/web/routes/policies.py src/policydb/web/routes/activities.py src/policydb/web/routes/clients.py src/policydb/web/routes/inbox.py
git commit -m "refactor(activities): route quick-log endpoints through create_followup_activity

All user-facing follow-up creation endpoints now call the shared helper.
Eliminates parallel INSERT paths and guarantees supersession + auto-link
+ scalar sync run identically across policy edit, opportunity log,
activities router, and inbox process. Touch-once: one creation path."
```

---

## Task 23: Final browser QA pass across all four pages

**Files:** None — manual verification only.

- [ ] **Step 23.1: Start dev server**

Run: `~/.policydb/venv/bin/policydb serve --port 8127 --reload`

- [ ] **Step 23.2: Test issue page**

Open an issue with:
- At least one follow-up on the issue (`on_issue` group)
- At least one loose follow-up on a covered policy (`loose` group)

Verify each action from the panel: ✓ done, 💤 snooze +7, ⏳ waiting toggle, ⊗ log-close, 🔗 attach (on loose row), 💬 note, + Add task. After each, verify:
- Toast appears
- Panel re-renders
- Related surfaces (policy edit page, sticky sidebar, Focus Queue) reflect the change

- [ ] **Step 23.3: Test client page**

Open a client with direct client follow-ups + tasks under multiple open issues + loose tasks. Verify all three group types render with correct ordering. Run one action from each group.

- [ ] **Step 23.4: Test program page**

Open a program with a renewal issue and child-policy tasks. Verify `on_program_issue` and `loose` groups, + Add task flow.

- [ ] **Step 23.5: Test policy page**

Open a policy with 2+ open follow-ups. Verify flat `on_policy` group, all actions work, + Add task works with the hidden policy input.

- [ ] **Step 23.6: Regression check: no duplicate displays**

Confirm on each page:
- Activity thread below panel does NOT list the same open follow-ups
- Scope Rollup card on issue/program no longer has the "Open Follow-ups" subsection
- Client sticky sidebar shows a count link, not a duplicate list

- [ ] **Step 23.7: Run full test suite one more time**

Run: `pytest tests/ -v`
Expected: 100% pass.

- [ ] **Step 23.8: Commit any QA fixes**

If any issues surfaced during QA:

```bash
git add -p  # review changes
git commit -m "fix(open-tasks): QA adjustments

[list what was fixed]"
```

---

## Self-Review (run this before marking the plan complete)

### Spec coverage — every section mapped to a task

- §2 Goals (panel on 4 pages, 6 inline actions, + Add task, shared component, toasts, touch-once) → Tasks 5–22
- §4.1 `get_open_tasks` signature → Task 5 (declaration), Tasks 6–8 (per-scope implementations)
- §4.2 TaskRow / GroupDict shape → Task 5 (`_open_task_row_from_activity`)
- §4.3 Grouping rules per scope → Tasks 5 (issue), 6 (client), 7 (program), 8 (policy)
- §4.4 Dedup + suppression (policy-source, client-source, archived, merged) → Tasks 6 (client direct+loose), 8 (policy scope)
- §5 Routes (render, 6 actions, + Add task) → Task 9 (skeleton), Tasks 11–16
- §5.4 Route ordering rule → Task 16 places `/new` before `/{activity_id}/*`
- §6 UI (panel + row templates, hover buttons, note textarea, empty state, cross-linked grayed rows, + Add task form) → Task 10 (templates), Task 16 (form template)
- §6.7 Toasts → Task 4 (library) + every action handler emitting `toast_message`
- §7.1 Action behavior per source → Tasks 11–16
- §7.2 Scalar-date sync → Task 1 (helpers) + Tasks 11, 12, 13 wire them into actions
- §7.3 Single creation helper → Task 2 + Task 22 refactor
- §7.4 Duplicate display removal → Task 17 (Scope Rollup), Task 18 (sticky sidebar)
- §7.5 Thread history filter → Task 3 (helper) + Task 21 (integration)
- §7.6 Merge handling → not explicitly coded; issue panel treats merged issues per existing `merged_into_id` logic in `get_open_tasks` (covered in Task 5/6 SQL). **Gap:** the spec mentions a redirect banner for merged issues — this is a UI convenience, not a v1 blocker. Add as follow-up if needed.
- §9 Page integrations → Tasks 17–20
- §10 Edge cases → partial: empty state (Task 10 template), archived policies (filtered in SQL), cross-linked rows (grayed in row template). Closed/Resolved issue disabled state is NOT in v1 — add as follow-up.
- §12 Testing → Tasks 1–16 each have unit tests; Task 23 covers manual QA.

Gaps I'm flagging explicitly (not in v1):
1. Merged-issue redirect banner on panel (spec §10 — not user-facing for the core flow; covered by existing auto-close behavior).
2. Closed/Resolved issue disabled-state banner (spec §10 — for v1 the panel still renders and actions still work on resolved issues; no harm done since resolution auto-closes follow-ups already).

### Placeholder scan

- No "TBD" / "TODO" / "implement later" in any task.
- Every step that writes code shows the code.
- Test code is complete, not "write tests for the above."
- Commands have exact invocations and expected output.

### Type consistency

- `get_open_tasks(conn, scope_type, scope_id)` signature consistent in Tasks 5–8 and routes.
- `create_followup_activity(...)` signature consistent between Task 2 definition and Task 22 refactor examples.
- `TaskRow` field names (`activity_id`, `is_on_issue`, `attach_target_issue_id`) consistent between Task 5 helper and Task 10 template usage.
- `scope_type` literals (`"issue"`, `"client"`, `"program"`, `"policy"`) consistent everywhere.
- Panel HTMX target id pattern `#open-tasks-panel-{scope_type}-{scope_id}` consistent between Task 10 template and Task 17–20 page integrations.

Plan is ready for execution.
