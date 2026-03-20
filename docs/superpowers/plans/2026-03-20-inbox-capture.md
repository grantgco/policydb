# Inbox Capture Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a quick capture input in the nav header, an inbox queue with INB-{id} UIDs, and a /inbox page for batch processing captured items into activities.

**Architecture:** New `inbox` table with simple pending/processed status flow. Capture via POST from nav header. Process via inline form on /inbox page that creates activity via existing activity_log system. Search integration for INB-{id} pattern.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS

**Spec:** `docs/superpowers/specs/2026-03-20-inbox-capture-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/migrations/064_inbox.sql` | Schema |
| Create | `src/policydb/web/routes/inbox.py` | All inbox endpoints |
| Create | `src/policydb/web/templates/inbox.html` | Inbox page |
| Create | `tests/test_inbox.py` | Tests |
| Modify | `src/policydb/db.py` | Register migration 064 |
| Modify | `src/policydb/web/app.py` | Register inbox router, pass pending count to all templates |
| Modify | `src/policydb/web/templates/base.html` | Nav capture input + inbox link with badge |
| Modify | `src/policydb/web/routes/dashboard.py` | Search integration for INB-{id} |

---

### Task 1: Migration + Route Module + Tests

**Files:**
- Create: `src/policydb/migrations/064_inbox.sql`
- Create: `src/policydb/web/routes/inbox.py`
- Create: `tests/test_inbox.py`
- Modify: `src/policydb/db.py`
- Modify: `src/policydb/web/app.py`

- [ ] **Step 1: Create migration**

```sql
-- 064_inbox.sql
CREATE TABLE IF NOT EXISTS inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_uid    TEXT NOT NULL UNIQUE,
    content      TEXT NOT NULL,
    client_id    INTEGER REFERENCES clients(id),
    status       TEXT NOT NULL DEFAULT 'pending',
    activity_id  INTEGER REFERENCES activity_log(id),
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);
```

Register in db.py (add 64 to `_KNOWN_MIGRATIONS`, add if-block).

- [ ] **Step 2: Create inbox route module**

Create `src/policydb/web/routes/inbox.py` with:

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from policydb.web.app import get_db, templates
from policydb import config as cfg
from datetime import date

router = APIRouter()

@router.post("/inbox/capture")
def inbox_capture(content: str = Form(...), client_id: int = Form(0), conn=Depends(get_db)):
    """Quick capture — create inbox item, return INB-{id} in toast."""
    conn.execute(
        "INSERT INTO inbox (content, client_id, inbox_uid) VALUES (?, ?, '')",
        (content.strip(), client_id or None),
    )
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    uid = f"INB-{row_id}"
    conn.execute("UPDATE inbox SET inbox_uid = ? WHERE id = ?", (uid, row_id))
    conn.commit()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "Captured ' + uid + ' - paste into email"}'
    })

@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, show_processed: str = "", conn=Depends(get_db)):
    """Inbox page — pending items for processing."""
    pending = [dict(r) for r in conn.execute("""
        SELECT i.*, c.name AS client_name
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """).fetchall()]
    processed = []
    if show_processed:
        processed = [dict(r) for r in conn.execute("""
            SELECT i.*, c.name AS client_name, a.subject AS activity_subject
            FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
            LEFT JOIN activity_log a ON i.activity_id = a.id
            WHERE i.status = 'processed'
            ORDER BY i.processed_at DESC LIMIT 50
        """).fetchall()]
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    return templates.TemplateResponse("inbox.html", {
        "request": request, "active": "inbox",
        "pending": pending,
        "processed": processed,
        "show_processed": bool(show_processed),
        "all_clients": all_clients,
        "activity_types": cfg.get("activity_types", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    })

@router.post("/inbox/{inbox_id}/process", response_class=HTMLResponse)
def inbox_process(
    request: Request, inbox_id: int,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    activity_type: str = Form("Note"),
    subject: str = Form(""),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    start_correspondence: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Process inbox item → create activity."""
    from policydb.utils import round_duration
    account_exec = cfg.get("default_account_exec", "Grant")
    dur = round_duration(duration_hours)
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, account_exec, duration_hours)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         subject or "Inbox item", details or None,
         follow_up_date or None, account_exec, dur),
    )
    activity_id = cursor.lastrowid
    # Start correspondence if requested
    if start_correspondence == "1":
        conn.execute("UPDATE activity_log SET thread_id = ? WHERE id = ?", (activity_id, activity_id))
    # Supersede follow-ups if needed
    if follow_up_date and policy_id:
        from policydb.queries import supersede_followups
        supersede_followups(conn, policy_id, follow_up_date)
    # Mark inbox item as processed
    conn.execute(
        "UPDATE inbox SET status='processed', activity_id=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (activity_id, inbox_id),
    )
    conn.commit()
    uid = conn.execute("SELECT inbox_uid FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return HTMLResponse("", headers={
        "HX-Trigger": '{"activityLogged": "' + (uid["inbox_uid"] if uid else '') + ' processed - activity created"}'
    })

@router.post("/inbox/{inbox_id}/dismiss")
def inbox_dismiss(inbox_id: int, conn=Depends(get_db)):
    """Dismiss inbox item without creating activity."""
    conn.execute(
        "UPDATE inbox SET status='processed', processed_at=CURRENT_TIMESTAMP WHERE id=?",
        (inbox_id,),
    )
    conn.commit()
    return JSONResponse({"ok": True})

@router.get("/inbox/{inbox_id}/policies")
def inbox_client_policies(inbox_id: int, client_id: int = 0, conn=Depends(get_db)):
    """Return policies for a client (for the process form policy picker)."""
    if not client_id:
        return JSONResponse([])
    rows = conn.execute("""
        SELECT policy_uid, policy_type, carrier
        FROM policies WHERE client_id = ? AND archived = 0
        ORDER BY policy_type
    """, (client_id,)).fetchall()
    return JSONResponse([{"uid": r["policy_uid"], "type": r["policy_type"], "carrier": r["carrier"] or ""} for r in rows])

def get_inbox_pending_count(conn) -> int:
    """Return count of pending inbox items."""
    return conn.execute("SELECT COUNT(*) FROM inbox WHERE status='pending'").fetchone()[0]
```

- [ ] **Step 3: Register router in app.py**

Add to `app.py`:
```python
from policydb.web.routes.inbox import router as inbox_router
app.include_router(inbox_router)
```

Also make `get_inbox_pending_count` available to all templates by adding it to the template context middleware or as a Jinja2 global.

- [ ] **Step 4: Write tests**

```python
# tests/test_inbox.py
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

def test_inbox_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "inbox" in tables
    conn.close()

def test_inbox_capture_and_uid(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO inbox (content, inbox_uid) VALUES ('Test item', '')")
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    uid = f"INB-{row_id}"
    conn.execute("UPDATE inbox SET inbox_uid = ? WHERE id = ?", (uid, row_id))
    conn.commit()
    row = conn.execute("SELECT * FROM inbox WHERE id = ?", (row_id,)).fetchone()
    assert row["inbox_uid"] == uid
    assert row["status"] == "pending"
    conn.close()

def test_inbox_process(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO inbox (content, inbox_uid, status) VALUES ('Test', 'INB-1', 'pending')")
    conn.execute("UPDATE inbox SET status='processed', processed_at=CURRENT_TIMESTAMP WHERE id=1")
    conn.commit()
    row = conn.execute("SELECT status FROM inbox WHERE id=1").fetchone()
    assert row["status"] == "processed"
    conn.close()
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: inbox table, route module, and tests (migration 064)"
```

---

### Task 2: Nav Header Capture + Inbox Link

**Files:**
- Modify: `src/policydb/web/templates/base.html`
- Modify: `src/policydb/web/app.py` (pass inbox count to all pages)

- [ ] **Step 1: Add capture input and inbox nav link to base.html**

In the nav bar, after the existing nav links and before the search area, add:

```html
<a href="/inbox" class="nav-link {% if active == 'inbox' %}bg-marsh-light{% endif %}">
  Inbox
  {% if inbox_pending_count %}<span class="bg-white/20 text-white text-[10px] px-1.5 py-0.5 rounded-full ml-1">{{ inbox_pending_count }}</span>{% endif %}
</a>
```

Add the capture input:
```html
<form hx-post="/inbox/capture" hx-swap="none"
      hx-on::after-request="if(event.detail.successful)this.reset()"
      class="flex items-center ml-2">
  <input type="text" name="content" placeholder="📥 Quick capture..." required
    class="bg-white/10 text-white placeholder-white/40 text-xs rounded px-3 py-1.5 w-48
           border border-white/20 focus:border-white/50 focus:outline-none focus:w-64 transition-all">
</form>
```

- [ ] **Step 2: Pass inbox_pending_count to all templates**

In `app.py`, add middleware or modify the template context to include `inbox_pending_count`. The simplest approach: add it as a Jinja2 context processor or pass it in the `templates.env.globals`.

Since inbox count needs a DB connection, use a middleware that adds it to every request:

```python
@app.middleware("http")
async def add_inbox_count(request, call_next):
    # Will be read by templates via request.state
    response = await call_next(request)
    return response
```

Or simpler: make it a Jinja2 global function that queries lazily.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: quick capture input in nav header + inbox link with badge"
```

---

### Task 3: Inbox Page

**Files:**
- Create: `src/policydb/web/templates/inbox.html`

- [ ] **Step 1: Create inbox.html**

Extends `base.html`. Shows:
- Header with count
- List of pending items, each with INB tag (blue pill, click to copy), content, client name, relative time, Process/Dismiss buttons
- Process form expands inline with: client picker, policy picker (filtered by client via HTMX), activity type pills, subject (pre-filled), details, follow-up date, COR toggle, duration
- Dismiss button confirms and removes the row
- Toggle for showing processed items

- [ ] **Step 2: Add client picker JS**

When client is selected in the process form, fetch policies via `GET /inbox/{id}/policies?client_id=X` and populate the policy select.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/inbox.html
git commit -m "feat: inbox page with process and dismiss actions"
```

---

### Task 4: Search Integration

**Files:**
- Modify: `src/policydb/web/routes/dashboard.py`

- [ ] **Step 1: Add INB-{id} pattern to search**

In the search handler, add:

```python
inb_match = re.match(r'^INB-(\d+)$', q.strip(), re.IGNORECASE)
if inb_match:
    inbox_id = int(inb_match.group(1))
    item = conn.execute("""
        SELECT i.*, c.name AS client_name, a.subject AS activity_subject, a.id AS act_id
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        LEFT JOIN activity_log a ON i.activity_id = a.id
        WHERE i.id = ?
    """, (inbox_id,)).fetchone()
    # Add to search results
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/routes/dashboard.py
git commit -m "feat: search by INB-{id} inbox item tag"
```

---

### Task 5: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`

- [ ] **Step 2: Manual test**

1. **Capture:** Type in nav bar "Got response from John on GL renewal" → Enter → toast shows INB-42
2. **Inbox badge:** Nav shows "Inbox (1)" badge
3. **Inbox page:** Navigate to /inbox → see the item
4. **Process:** Click Process → fill client, policy, type → Log Activity → item processed
5. **Dismiss:** Capture another → click Dismiss → gone
6. **Search:** Type "INB-42" in search → finds the item and linked activity
7. **Copy UID:** Click INB-42 pill → copied to clipboard

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for inbox capture"
```
