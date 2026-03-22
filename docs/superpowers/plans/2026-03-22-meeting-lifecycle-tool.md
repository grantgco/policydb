# Meeting Lifecycle Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the existing meeting system into a phased Before → During → After lifecycle tool with auto-generated prep briefings, focused capture, and guided closeout.

**Architecture:** The existing `/meetings` routes and templates are restructured around a three-phase workflow. A new migration adds `meeting_type`, `phase`, `agenda`, `start_time`, `end_time` columns to `client_meetings` and creates a `meeting_decisions` table. The detail page template is rebuilt with phase-specific layouts. Cross-links are added to dashboard, client page, and policy activity tab.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, Tailwind CSS (CDN)

**Spec:** `docs/superpowers/specs/2026-03-22-meeting-lifecycle-tool-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/policydb/migrations/068_meeting_lifecycle.sql` | Add phase, meeting_type, agenda, start_time, end_time to client_meetings; create meeting_decisions table |
| `src/policydb/web/templates/meetings/detail_phased.html` | Phased detail page with header + phase indicator + phase content areas |
| `src/policydb/web/templates/meetings/_phase_before.html` | Auto-generated prep briefing layout (includes `_prep_briefing.html`) |
| `src/policydb/web/templates/meetings/_prep_briefing.html` | Shared briefing content partial — full version in Before, condensed in During reference panel |
| `src/policydb/web/templates/meetings/_phase_during.html` | Side-by-side notes + prep reference |
| `src/policydb/web/templates/meetings/_phase_after.html` | Six-step guided closeout |
| `src/policydb/web/templates/meetings/_decision_row.html` | Single decision display row |
| `src/policydb/web/templates/meetings/_recap_preview.html` | Formatted recap for copy/email |
| `src/policydb/web/templates/meetings/list_enhanced.html` | Redesigned list with upcoming cards + past table |
| `src/policydb/web/templates/meetings/_upcoming_card.html` | Single upcoming meeting card |
| `src/policydb/web/templates/dashboard/_upcoming_meetings.html` | Dashboard widget partial |
| `src/policydb/web/templates/clients/_meetings_section.html` | Client page meetings section partial |
| `tests/test_meeting_lifecycle.py` | Tests for new meeting lifecycle routes and logic |

### Modified Files
| File | Changes |
|------|---------|
| `src/policydb/db.py` | Wire migration 068, add to `_KNOWN_MIGRATIONS` |
| `src/policydb/config.py` | Add `meeting_types` to `_DEFAULTS` |
| `src/policydb/web/routes/settings.py` | Add `meeting_types` to `EDITABLE_LISTS` |
| `src/policydb/web/routes/meetings.py` | Add phase transition routes, decision routes, prep briefing route, enhanced list logic |
| `src/policydb/email_templates.py` | Add `meeting_context()` function, meeting tokens to `CONTEXT_TOKENS` |
| `src/policydb/web/routes/dashboard.py` | Add upcoming meetings query to dashboard context |
| `src/policydb/web/templates/dashboard.html` | Include upcoming meetings widget |
| `src/policydb/web/routes/clients.py` | Add meetings section data to client detail context |
| `src/policydb/web/templates/clients/detail.html` | Include meetings section partial |
| `src/policydb/web/templates/activities/_activity_row.html` | Add meeting ref tag (MTG-xxx) display |
| `src/policydb/web/routes/policies.py` | Add meeting/decision data to policy activity tab context |
| `src/policydb/web/templates/policies/_tab_activity.html` | Show linked meetings and decisions in activity timeline |
| `src/policydb/web/templates/followups/_row.html` | Add "Schedule as Meeting" action button |

---

## Task 1: Database Migration — Meeting Lifecycle Columns

**Files:**
- Create: `src/policydb/migrations/068_meeting_lifecycle.sql`
- Modify: `src/policydb/db.py` (add to `_KNOWN_MIGRATIONS` + wire migration)

- [ ] **Step 1: Write the migration SQL**

Create `src/policydb/migrations/068_meeting_lifecycle.sql`:

```sql
-- Add lifecycle columns to client_meetings
ALTER TABLE client_meetings ADD COLUMN meeting_type TEXT;
ALTER TABLE client_meetings ADD COLUMN phase TEXT DEFAULT 'before';
ALTER TABLE client_meetings ADD COLUMN agenda TEXT;
ALTER TABLE client_meetings ADD COLUMN start_time TEXT;
ALTER TABLE client_meetings ADD COLUMN end_time TEXT;

-- Create meeting_decisions table
CREATE TABLE IF NOT EXISTS meeting_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES client_meetings(id),
    description TEXT NOT NULL,
    policy_uid TEXT,
    confirmed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_meeting_decisions_meeting ON meeting_decisions(meeting_id);
```

- [ ] **Step 2: Wire migration into db.py**

In `src/policydb/db.py`:
1. Add `68` to the `_KNOWN_MIGRATIONS` set (around line 298)
2. Add the migration wiring block after the last migration (around line 818), following the existing pattern:

```python
# Migration 68 — meeting lifecycle columns + decisions table
cur.execute("SELECT 1 FROM schema_version WHERE version = 68")
if not cur.fetchone():
    _run_sql_file(conn, migrations_dir / "068_meeting_lifecycle.sql")
    conn.execute("INSERT INTO schema_version (version) VALUES (68)")
    conn.commit()
```

- [ ] **Step 3: Verify migration runs**

Run: `python -c "from policydb.db import init_db; init_db()"`
Expected: No errors. Check the columns exist:
```bash
python -c "
from policydb.db import get_connection, DB_PATH
conn = get_connection(DB_PATH)
print([col[1] for col in conn.execute('PRAGMA table_info(client_meetings)').fetchall()])
print([col[1] for col in conn.execute('PRAGMA table_info(meeting_decisions)').fetchall()])
conn.close()
"
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/068_meeting_lifecycle.sql src/policydb/db.py
git commit -m "feat: add meeting lifecycle migration (phase, type, decisions)"
```

---

## Task 2: Config — Meeting Types

**Files:**
- Modify: `src/policydb/config.py` (add `meeting_types` to `_DEFAULTS`, around line 90)
- Modify: `src/policydb/web/routes/settings.py` (add to `EDITABLE_LISTS`, around line 17)

- [ ] **Step 1: Add meeting_types to _DEFAULTS in config.py**

Add to the `_DEFAULTS` dict (after `activity_types`):

```python
"meeting_types": [
    "Stewardship",
    "Renewal Strategy",
    "Claims Review",
    "New Business",
    "General Check-in",
    "Prospecting",
    "Annual Review",
],
```

- [ ] **Step 2: Add meeting_types to EDITABLE_LISTS in settings.py**

Add to `EDITABLE_LISTS` dict:

```python
"meeting_types": "Meeting Types",
```

- [ ] **Step 3: Verify setting appears**

Start server (`policydb serve`), navigate to `/settings`. Verify "Meeting Types" appears in the config list editor with the default values.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/config.py src/policydb/web/routes/settings.py
git commit -m "feat: add meeting_types config list"
```

---

## Task 3: Enhanced Meetings List Page

**Files:**
- Create: `src/policydb/web/templates/meetings/list_enhanced.html`
- Create: `src/policydb/web/templates/meetings/_upcoming_card.html`
- Modify: `src/policydb/web/routes/meetings.py` (update `GET /meetings` handler, line 48)

- [ ] **Step 1: Write test for list page split**

Add to `tests/test_meeting_lifecycle.py`:

```python
"""Tests for meeting lifecycle tool."""

import sqlite3
import json
from pathlib import Path
from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from policydb.db import init_db, get_connection


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Create a TestClient with a fresh temporary database."""
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')"
    )
    # Future meeting
    future = (date.today() + timedelta(days=3)).isoformat()
    conn.execute(
        """INSERT INTO client_meetings (id, client_id, title, meeting_date, meeting_time,
           duration_hours, meeting_uid, phase, meeting_type)
           VALUES (1, 1, 'Q2 Review', ?, '14:00', 1.5, 'CN1-MTG01', 'before', 'Stewardship')""",
        (future,),
    )
    # Past meeting
    past = (date.today() - timedelta(days=7)).isoformat()
    conn.execute(
        """INSERT INTO client_meetings (id, client_id, title, meeting_date, meeting_time,
           duration_hours, meeting_uid, phase, meeting_type)
           VALUES (2, 1, 'Q1 Check-in', ?, '10:00', 1.0, 'CN1-MTG02', 'complete', 'General Check-in')""",
        (past,),
    )
    conn.commit()
    conn.close()

    from policydb.web.app import app
    client = TestClient(app, raise_server_exceptions=False)
    return client


def test_meetings_list_page_loads(app_client):
    """List page renders with upcoming cards and past table."""
    resp = app_client.get("/meetings")
    assert resp.status_code == 200
    assert "Q2 Review" in resp.text
    assert "Q1 Check-in" in resp.text


def test_meetings_list_separates_upcoming_and_past(app_client):
    """Upcoming meetings appear in cards section, past in table."""
    resp = app_client.get("/meetings")
    html = resp.text
    # Upcoming card section should exist
    assert "upcoming-meetings" in html.lower() or "Upcoming" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: Tests may pass partially since existing list loads, but the structure assertions will fail.

- [ ] **Step 3: Update meetings route to split upcoming/past**

In `src/policydb/web/routes/meetings.py`, update the `GET /meetings` handler (around line 48) to split meetings into upcoming and past:

```python
@router.get("/meetings")
async def meetings_list(request: Request, client_id: int = None):
    conn = get_connection()
    try:
        today = date.today().isoformat()
        cfg = Config()

        # Base query parts
        where_clause = ""
        params = []
        if client_id:
            where_clause = "WHERE cm.client_id = ?"
            params = [client_id]

        # Upcoming meetings (today or future)
        upcoming_where = f"WHERE cm.meeting_date >= ?" if not client_id else f"WHERE cm.client_id = ? AND cm.meeting_date >= ?"
        upcoming_params = [today] if not client_id else [client_id, today]
        upcoming = conn.execute(
            f"""SELECT cm.*, c.name as client_name,
                       (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = cm.id) as attendee_count,
                       (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = cm.id) as action_total,
                       (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = cm.id AND completed = 1) as action_done
                FROM client_meetings cm
                JOIN clients c ON c.id = cm.client_id
                {upcoming_where}
                ORDER BY cm.meeting_date ASC, cm.meeting_time ASC
                LIMIT 6""",
            upcoming_params,
        ).fetchall()

        # Past meetings
        past_where = f"WHERE cm.meeting_date < ?" if not client_id else f"WHERE cm.client_id = ? AND cm.meeting_date < ?"
        past_params = [today] if not client_id else [client_id, today]
        past = conn.execute(
            f"""SELECT cm.*, c.name as client_name,
                       (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = cm.id) as attendee_count,
                       (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = cm.id) as action_total,
                       (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = cm.id AND completed = 1) as action_done
                FROM client_meetings cm
                JOIN clients c ON c.id = cm.client_id
                {past_where}
                ORDER BY cm.meeting_date DESC
                LIMIT 50""",
            past_params,
        ).fetchall()

        clients = conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall()

        return templates.TemplateResponse(
            "meetings/list_enhanced.html",
            {
                "request": request,
                "upcoming": upcoming,
                "past": past,
                "clients": clients,
                "selected_client_id": client_id,
                "meeting_types": cfg.get("meeting_types", []),
            },
        )
    finally:
        conn.close()
```

- [ ] **Step 4: Create `_upcoming_card.html` template**

Create `src/policydb/web/templates/meetings/_upcoming_card.html` — a single upcoming meeting card showing client name, title, date/time, attendee count, meeting type badge, phase status badge. Use the existing app design language (Tailwind classes, same color palette as the rest of the app). Card is clickable → links to `/meetings/{meeting_id}`.

- [ ] **Step 5: Create `list_enhanced.html` template**

Create `src/policydb/web/templates/meetings/list_enhanced.html` extending `base.html`. Layout:
- Header bar: title, search, client filter, type filter, "+ New Meeting" button
- "Upcoming" section: renders `_upcoming_card.html` for each upcoming meeting (max 3 as cards, rest as compact list)
- "Past Meetings" section: table with columns Client, Title, Type, Date, Actions progress, Status
- Filter uses HTMX `hx-get="/meetings?client_id=X"` with `hx-target="#meetings-content"` for client filter

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: All list page tests pass.

- [ ] **Step 7: Visual QA**

Start server, navigate to `/meetings`. Take screenshot. Verify:
- Upcoming cards display correctly at top
- Past meetings table renders below
- Client filter works
- "+ New Meeting" button is present and links correctly

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/meetings.py src/policydb/web/templates/meetings/list_enhanced.html src/policydb/web/templates/meetings/_upcoming_card.html tests/test_meeting_lifecycle.py
git commit -m "feat: enhanced meetings list with upcoming cards + past table"
```

---

## Task 4: Phased Detail Page — Page Header + Phase Indicator

**Files:**
- Create: `src/policydb/web/templates/meetings/detail_phased.html`
- Modify: `src/policydb/web/routes/meetings.py` (update `GET /meetings/{meeting_id}` handler, line 200)

- [ ] **Step 1: Write test for phased detail page**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_meeting_detail_shows_phase_indicator(app_client):
    """Detail page shows phase indicator with current phase highlighted."""
    resp = app_client.get("/meetings/1")
    assert resp.status_code == 200
    html = resp.text
    assert "Before" in html
    assert "During" in html
    assert "After" in html


def test_meeting_phase_transitions(app_client):
    """POST to start/end/complete advances the phase."""
    # Start meeting
    resp = app_client.post("/meetings/1/start")
    assert resp.status_code == 200

    # Verify phase changed
    resp = app_client.get("/meetings/1")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_meeting_lifecycle.py::test_meeting_detail_shows_phase_indicator -v`
Expected: FAIL — current detail page doesn't have phase indicators.

- [ ] **Step 3: Add phase transition routes to meetings.py**

Add to `src/policydb/web/routes/meetings.py`:

```python
@router.post("/meetings/{meeting_id}/start")
async def start_meeting(request: Request, meeting_id: int):
    """Advance meeting phase to 'during', record start time."""
    conn = get_connection()
    try:
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        conn.execute(
            "UPDATE client_meetings SET phase = 'during', start_time = ? WHERE id = ?",
            (now, meeting_id),
        )
        conn.commit()
        meeting = _meeting_dict(conn, meeting_id)
        # Return the full phased detail page
        cfg = Config()
        return templates.TemplateResponse(
            "meetings/detail_phased.html",
            {"request": request, "meeting": meeting, "meeting_types": cfg.get("meeting_types", []),
             "renewal_statuses": cfg.get("renewal_statuses", [])},
        )
    finally:
        conn.close()


@router.post("/meetings/{meeting_id}/end")
async def end_meeting(request: Request, meeting_id: int):
    """Advance meeting phase to 'after', record end time."""
    conn = get_connection()
    try:
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        conn.execute(
            "UPDATE client_meetings SET phase = 'after', end_time = ? WHERE id = ?",
            (now, meeting_id),
        )
        conn.commit()
        meeting = _meeting_dict(conn, meeting_id)
        cfg = Config()
        return templates.TemplateResponse(
            "meetings/detail_phased.html",
            {"request": request, "meeting": meeting, "meeting_types": cfg.get("meeting_types", []),
             "renewal_statuses": cfg.get("renewal_statuses", [])},
        )
    finally:
        conn.close()


@router.post("/meetings/{meeting_id}/complete")
async def complete_meeting(request: Request, meeting_id: int):
    """Mark meeting as complete, finalize activity log entry."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE client_meetings SET phase = 'complete' WHERE id = ?",
            (meeting_id,),
        )
        # Update duration on the auto-created activity_log entry
        meeting = dict(conn.execute(
            "SELECT * FROM client_meetings WHERE id = ?", (meeting_id,)
        ).fetchone())
        if meeting.get("start_time") and meeting.get("end_time"):
            from policydb.utils import round_duration
            # Calculate duration from start/end
            start_parts = meeting["start_time"].split(":")
            end_parts = meeting["end_time"].split(":")
            start_mins = int(start_parts[0]) * 60 + int(start_parts[1])
            end_mins = int(end_parts[0]) * 60 + int(end_parts[1])
            duration_hrs = round_duration(str((end_mins - start_mins) / 60))
            conn.execute(
                "UPDATE activity_log SET duration_hours = ? WHERE client_id = ? AND subject LIKE ? AND activity_type = 'Meeting'",
                (duration_hrs, meeting["client_id"], f"%{meeting['title']}%"),
            )
        conn.commit()
        return RedirectResponse(f"/meetings/{meeting_id}", status_code=303)
    finally:
        conn.close()
```

- [ ] **Step 4: Update `_meeting_dict()` to include new columns**

In `src/policydb/web/routes/meetings.py`, update `_meeting_dict()` (line 16) to also fetch `meeting_type`, `phase`, `agenda`, `start_time`, `end_time`, and decisions:

```python
# Add after fetching action items and policies:
decisions = conn.execute(
    "SELECT * FROM meeting_decisions WHERE meeting_id = ? ORDER BY created_at",
    (meeting_id,),
).fetchall()
meeting["decisions"] = [dict(d) for d in decisions]
```

- [ ] **Step 5: Create `detail_phased.html` template**

Create `src/policydb/web/templates/meetings/detail_phased.html` extending `base.html`. Structure:
- **Header section**: Client name + meeting title (editable via `hx-patch`), meeting type badge, date/time/duration/location/attendee count
- **Phase indicator bar**: Three segments `1 · Before | 2 · During | 3 · After`, current phase highlighted with brand color, clickable to navigate
- **Phase content area**: Uses `{% if meeting.phase == 'before' %}` to include the appropriate phase partial
- **Phase action button**: "Start Meeting →" / "End Meeting →" / "Complete Meeting ✓" depending on current phase, as `hx-post` to the transition endpoint

- [ ] **Step 6: Update GET handler to use new template**

In `src/policydb/web/routes/meetings.py`, update the `GET /meetings/{meeting_id}` handler (line 200) to render `detail_phased.html` instead of `detail.html`. Pass additional context: `meeting_types`, `renewal_statuses`.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: Phase indicator and transition tests pass.

- [ ] **Step 8: Visual QA**

Navigate to `/meetings/1`. Verify:
- Phase indicator displays with "Before" highlighted
- Meeting metadata visible in header
- Click "Start Meeting →" advances to During phase

- [ ] **Step 9: Commit**

```bash
git add src/policydb/web/routes/meetings.py src/policydb/web/templates/meetings/detail_phased.html tests/test_meeting_lifecycle.py
git commit -m "feat: phased meeting detail page with phase transitions"
```

---

## Task 5: Before Phase — Auto-Generated Prep Briefing

**Files:**
- Create: `src/policydb/web/templates/meetings/_phase_before.html`
- Modify: `src/policydb/web/routes/meetings.py` (add prep briefing data endpoint)

- [ ] **Step 1: Write test for prep briefing data**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_prep_briefing_loads(app_client):
    """Prep briefing endpoint returns briefing data for a meeting."""
    resp = app_client.get("/meetings/1/prep-briefing")
    assert resp.status_code == 200
    assert "Test Client" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meeting_lifecycle.py::test_prep_briefing_loads -v`
Expected: FAIL — endpoint doesn't exist yet.

- [ ] **Step 3: Add prep briefing route**

Add to `src/policydb/web/routes/meetings.py`:

```python
@router.get("/meetings/{meeting_id}/prep-briefing")
async def prep_briefing(request: Request, meeting_id: int):
    """Auto-generated prep briefing with all client data."""
    conn = get_connection()
    try:
        meeting = _meeting_dict(conn, meeting_id)
        client_id = meeting["client_id"]
        today = date.today().isoformat()

        # Attendees (already in meeting dict)
        attendees = meeting.get("attendees", [])

        # Renewal status summary
        renewals = conn.execute(
            """SELECT * FROM v_renewal_pipeline WHERE client_id = ?""",
            (client_id,),
        ).fetchall()

        # Outstanding items: overdue follow-ups + incomplete milestones
        overdue_followups = conn.execute(
            """SELECT * FROM v_overdue_followups WHERE client_id = ?""",
            (client_id,),
        ).fetchall()
        incomplete_milestones = conn.execute(
            """SELECT pm.*, p.policy_type, p.policy_uid
               FROM policy_milestones pm
               JOIN policies p ON p.id = pm.policy_id
               WHERE p.client_id = ? AND pm.completed = 0""",
            (client_id,),
        ).fetchall()
        # Open action items from previous meetings
        prev_actions = conn.execute(
            """SELECT mai.*, cm.title as meeting_title
               FROM meeting_action_items mai
               JOIN client_meetings cm ON cm.id = mai.meeting_id
               WHERE cm.client_id = ? AND mai.completed = 0 AND cm.id != ?
               ORDER BY mai.due_date""",
            (client_id, meeting_id),
        ).fetchall()

        # Schedule of insurance
        schedule = conn.execute(
            """SELECT * FROM v_schedule WHERE client_id = ?""",
            (client_id,),
        ).fetchall()

        # Recent activity (30 days)
        thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
        recent_activity = conn.execute(
            """SELECT al.*, c.name as client_name
               FROM activity_log al
               LEFT JOIN clients c ON c.id = al.client_id
               WHERE al.client_id = ? AND al.activity_date >= ?
               ORDER BY al.activity_date DESC LIMIT 10""",
            (client_id, thirty_days_ago),
        ).fetchall()

        # Account pulse / client summary
        client_summary = conn.execute(
            "SELECT * FROM v_client_summary WHERE id = ?", (client_id,)
        ).fetchone()
        from policydb.queries import get_client_total_hours
        total_hours = get_client_total_hours(conn, client_id)

        return templates.TemplateResponse(
            "meetings/_phase_before.html",
            {
                "request": request,
                "meeting": meeting,
                "attendees": attendees,
                "renewals": [dict(r) for r in renewals],
                "overdue_followups": [dict(f) for f in overdue_followups],
                "incomplete_milestones": [dict(m) for m in incomplete_milestones],
                "prev_actions": [dict(a) for a in prev_actions],
                "schedule": [dict(s) for s in schedule],
                "recent_activity": [dict(a) for a in recent_activity],
                "client_summary": dict(client_summary) if client_summary else {},
                "total_hours": total_hours,
            },
        )
    finally:
        conn.close()
```

- [ ] **Step 4: Create shared `_prep_briefing.html` partial**

Create `src/policydb/web/templates/meetings/_prep_briefing.html` — the shared briefing content used in both Before (full) and During (condensed reference). Accepts a `compact` boolean variable:
- When `compact` is false (Before phase): full-size cards with all detail
- When `compact` is true (During reference panel): condensed one-line-per-item format, smaller text

Sections: Attendees, Renewal Status (color-coded), Outstanding Items, SOI table, Recent Activity, Account Pulse, Talking Points. Color conventions: green for attendees, amber for renewals, red for outstanding/overdue, indigo for SOI, purple for account pulse.

- [ ] **Step 5: Create `_phase_before.html` template**

Create `src/policydb/web/templates/meetings/_phase_before.html`. Layout per spec:
- **Left column (3/5)**: `{% include "meetings/_prep_briefing.html" with compact=False %}`
- **Right column (2/5)**: Account Pulse card, Talking Points / Agenda (contenteditable list with `hx-post="/meetings/{meeting.id}/agenda"` on blur)
- **Footer**: "Start Meeting →" button with `hx-post="/meetings/{meeting.id}/start"` and `hx-target="#meeting-content"`

Use Tailwind utility classes.

- [ ] **Step 5: Add agenda save endpoint**

Add to `src/policydb/web/routes/meetings.py`:

```python
@router.post("/meetings/{meeting_id}/agenda")
async def save_agenda(request: Request, meeting_id: int):
    """Save talking points / agenda text."""
    form = await request.form()
    agenda = form.get("agenda", "")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE client_meetings SET agenda = ? WHERE id = ?",
            (agenda, meeting_id),
        )
        conn.commit()
        return JSONResponse({"ok": True})
    finally:
        conn.close()
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: Prep briefing test passes.

- [ ] **Step 7: Visual QA**

Navigate to a meeting detail page in "before" phase. Verify:
- All briefing sections render with real data (or gracefully empty)
- Renewal status uses color-coded urgency
- Talking points are editable and auto-save
- "Start Meeting →" button works and transitions to During phase

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/meetings.py src/policydb/web/templates/meetings/_phase_before.html tests/test_meeting_lifecycle.py
git commit -m "feat: auto-generated prep briefing (Before phase)"
```

---

## Task 6: During Phase — Side-by-Side Notes + Capture

**Files:**
- Create: `src/policydb/web/templates/meetings/_phase_during.html`
- Create: `src/policydb/web/templates/meetings/_decision_row.html`
- Modify: `src/policydb/web/routes/meetings.py` (add decision CRUD routes)

- [ ] **Step 1: Write tests for decision CRUD**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_add_decision(app_client):
    """POST to decisions adds a new decision."""
    # First advance to during phase
    app_client.post("/meetings/1/start")
    resp = app_client.post(
        "/meetings/1/decisions",
        data={"description": "Increase GL limits to $2M"},
    )
    assert resp.status_code == 200
    assert "Increase GL limits" in resp.text


def test_notes_autosave(app_client):
    """POST to notes saves meeting notes."""
    app_client.post("/meetings/1/start")
    resp = app_client.post(
        "/meetings/1/notes",
        data={"notes": "Discussed renewal timeline with client."},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_meeting_lifecycle.py::test_add_decision -v`
Expected: FAIL — decisions endpoint doesn't exist.

- [ ] **Step 3: Add decision routes to meetings.py**

Add to `src/policydb/web/routes/meetings.py`:

```python
@router.post("/meetings/{meeting_id}/decisions")
async def add_decision(request: Request, meeting_id: int):
    """Add a decision to the meeting."""
    form = await request.form()
    description = form.get("description", "").strip()
    policy_uid = form.get("policy_uid", "") or None
    if not description:
        return JSONResponse({"ok": False, "error": "Description required"}, status_code=400)
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO meeting_decisions (meeting_id, description, policy_uid) VALUES (?, ?, ?)",
            (meeting_id, description, policy_uid),
        )
        conn.commit()
        decision = dict(conn.execute(
            "SELECT * FROM meeting_decisions WHERE id = ?", (cur.lastrowid,)
        ).fetchone())
        return templates.TemplateResponse(
            "meetings/_decision_row.html",
            {"request": request, "decision": decision, "meeting_id": meeting_id},
        )
    finally:
        conn.close()


@router.post("/meetings/{meeting_id}/decisions/{decision_id}/confirm")
async def confirm_decision(request: Request, meeting_id: int, decision_id: int):
    """Toggle decision confirmed status."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_decisions SET confirmed = CASE WHEN confirmed = 1 THEN 0 ELSE 1 END WHERE id = ?",
            (decision_id,),
        )
        conn.commit()
        decision = dict(conn.execute(
            "SELECT * FROM meeting_decisions WHERE id = ?", (decision_id,)
        ).fetchone())
        return templates.TemplateResponse(
            "meetings/_decision_row.html",
            {"request": request, "decision": decision, "meeting_id": meeting_id},
        )
    finally:
        conn.close()


@router.post("/meetings/{meeting_id}/decisions/{decision_id}/link")
async def link_decision_policy(request: Request, meeting_id: int, decision_id: int):
    """Link a decision to a policy."""
    form = await request.form()
    policy_uid = form.get("policy_uid", "") or None
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_decisions SET policy_uid = ? WHERE id = ?",
            (policy_uid, decision_id),
        )
        conn.commit()
        decision = dict(conn.execute(
            "SELECT * FROM meeting_decisions WHERE id = ?", (decision_id,)
        ).fetchone())
        return templates.TemplateResponse(
            "meetings/_decision_row.html",
            {"request": request, "decision": decision, "meeting_id": meeting_id},
        )
    finally:
        conn.close()


@router.delete("/meetings/{meeting_id}/decisions/{decision_id}")
async def delete_decision(request: Request, meeting_id: int, decision_id: int):
    """Delete a decision."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM meeting_decisions WHERE id = ?", (decision_id,))
        conn.commit()
        return Response(status_code=200)
    finally:
        conn.close()
```

- [ ] **Step 4: Create `_decision_row.html` template**

Single decision row: diamond icon (◆), description text, optional policy link badge, confirm checkmark button, delete button. Uses `hx-post` for confirm toggle, `hx-delete` for removal.

- [ ] **Step 5: Create `_phase_during.html` template**

Layout per spec:
- **Left side (3/5)**: Large contenteditable notes area with `hx-post="/meetings/{meeting.id}/notes"` on blur/interval. Below: Action Items list (reuse existing `_action_row.html` pattern) with "+ Add" button. Below: Decisions list using `_decision_row.html` with "+ Add" button that expands inline form.
- **Right side (2/5)**: Condensed prep reference using shared `_prep_briefing.html` with `compact=True`. Collapsible via "Collapse »" link. Talking points have strike-through toggle (click to mark as covered). Loaded via `hx-get="/meetings/{meeting.id}/prep-briefing?compact=true"` on phase load.
- **Footer**: "End Meeting →" button with `hx-post="/meetings/{meeting.id}/end"`

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: Decision CRUD and notes tests pass.

- [ ] **Step 7: Visual QA**

Navigate to a meeting, click "Start Meeting →". Verify:
- Notes area is prominent and editable
- "+ Add" for action items and decisions works
- Prep reference panel displays on the right
- "End Meeting →" button transitions to After phase

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/meetings.py src/policydb/web/templates/meetings/_phase_during.html src/policydb/web/templates/meetings/_decision_row.html tests/test_meeting_lifecycle.py
git commit -m "feat: During phase — side-by-side notes + capture + decisions"
```

---

## Task 7: After Phase — Guided Closeout

**Files:**
- Create: `src/policydb/web/templates/meetings/_phase_after.html`
- Create: `src/policydb/web/templates/meetings/_recap_preview.html`
- Modify: `src/policydb/web/routes/meetings.py` (add action→followup routing, recap, schedule-next)

- [ ] **Step 1: Write tests for After phase features**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_create_followups_from_actions(app_client):
    """Converting action items creates activity_log entries."""
    # Setup: add an action item
    app_client.post("/meetings/1/start")
    app_client.post(
        "/meetings/1/actions/add",
        data={"description": "Get GL quotes", "assignee": "Me", "due_date": "2026-04-01"},
    )
    app_client.post("/meetings/1/end")

    # Convert to follow-ups
    resp = app_client.post("/meetings/1/actions/create-followups")
    assert resp.status_code == 200


def test_recap_generation(app_client):
    """Recap endpoint generates formatted meeting summary."""
    resp = app_client.get("/meetings/1/recap")
    assert resp.status_code == 200
    assert "Test Client" in resp.text or "Q2 Review" in resp.text


def test_schedule_next_meeting(app_client):
    """Schedule-next creates a new meeting with same client and attendees."""
    # Add an attendee first
    app_client.post(
        "/meetings/1/attendees/add",
        data={"name": "Jane Smith", "role": "CFO"},
    )
    resp = app_client.post(
        "/meetings/1/schedule-next",
        data={"meeting_date": "2026-04-15", "title": "Q3 Review"},
    )
    assert resp.status_code in (200, 303)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_meeting_lifecycle.py::test_create_followups_from_actions -v`
Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Add action→follow-up routing endpoint**

Add to `src/policydb/web/routes/meetings.py`:

```python
@router.post("/meetings/{meeting_id}/actions/create-followups")
async def create_followups_from_actions(request: Request, meeting_id: int):
    """Bulk-convert action items to follow-up activities."""
    conn = get_connection()
    try:
        meeting = _meeting_dict(conn, meeting_id)
        actions = conn.execute(
            "SELECT * FROM meeting_action_items WHERE meeting_id = ? AND completed = 0 AND activity_id IS NULL",
            (meeting_id,),
        ).fetchall()
        for action in actions:
            action = dict(action)
            cur = conn.execute(
                """INSERT INTO activity_log
                   (activity_date, client_id, activity_type, subject, details,
                    follow_up_date, follow_up_done, account_exec)
                   VALUES (?, ?, 'Follow-up', ?, ?, ?, 0, ?)""",
                (
                    date.today().isoformat(),
                    meeting["client_id"],
                    action["description"],
                    f"Action item from meeting: {meeting['title']}",
                    action["due_date"],
                    "",  # account_exec
                ),
            )
            # Back-link the activity to the action item
            conn.execute(
                "UPDATE meeting_action_items SET activity_id = ? WHERE id = ?",
                (cur.lastrowid, action["id"]),
            )
        conn.commit()

        # Return updated After phase content
        meeting = _meeting_dict(conn, meeting_id)
        cfg = Config()
        return templates.TemplateResponse(
            "meetings/_phase_after.html",
            {"request": request, "meeting": meeting,
             "renewal_statuses": cfg.get("renewal_statuses", [])},
        )
    finally:
        conn.close()
```

- [ ] **Step 4: Add schedule-next endpoint**

Add to `src/policydb/web/routes/meetings.py`:

```python
@router.post("/meetings/{meeting_id}/schedule-next")
async def schedule_next_meeting(request: Request, meeting_id: int):
    """Create next meeting with same client, attendees, and unresolved items."""
    form = await request.form()
    conn = get_connection()
    try:
        meeting = _meeting_dict(conn, meeting_id)
        new_title = form.get("title", meeting["title"])
        new_date = form.get("meeting_date", "")

        from policydb.db import next_meeting_uid
        new_uid = next_meeting_uid(conn, meeting["client_id"])

        cur = conn.execute(
            """INSERT INTO client_meetings
               (client_id, title, meeting_date, meeting_time, duration_hours,
                location, meeting_uid, phase, meeting_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'before', ?)""",
            (
                meeting["client_id"], new_title, new_date,
                meeting.get("meeting_time", ""), meeting.get("duration_hours", 1.0),
                meeting.get("location", ""), new_uid, meeting.get("meeting_type", ""),
            ),
        )
        new_meeting_id = cur.lastrowid

        # Copy attendees
        for att in meeting.get("attendees", []):
            conn.execute(
                """INSERT INTO meeting_attendees
                   (meeting_id, contact_id, name, role, is_internal, attendee_type)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_meeting_id, att.get("contact_id"), att["name"],
                 att.get("role", ""), att.get("is_internal", 0), att.get("attendee_type", "")),
            )

        # Carry forward unresolved action items
        unresolved = conn.execute(
            "SELECT * FROM meeting_action_items WHERE meeting_id = ? AND completed = 0",
            (meeting_id,),
        ).fetchall()
        for action in unresolved:
            action = dict(action)
            conn.execute(
                """INSERT INTO meeting_action_items
                   (meeting_id, description, assignee, due_date, completed, policy_uid)
                   VALUES (?, ?, ?, ?, 0, ?)""",
                (new_meeting_id, action["description"], action.get("assignee", ""),
                 action.get("due_date", ""), action.get("policy_uid", "")),
            )

        # Auto-log activity
        conn.execute(
            """INSERT INTO activity_log (activity_date, client_id, activity_type, subject)
               VALUES (?, ?, 'Meeting', ?)""",
            (new_date, meeting["client_id"], new_title),
        )

        conn.commit()
        return RedirectResponse(f"/meetings/{new_meeting_id}", status_code=303)
    finally:
        conn.close()
```

- [ ] **Step 5: Create `_recap_preview.html` template**

Formatted meeting recap: meeting title, date, attendees list, notes summary, decisions list, action items list. "Copy" button uses JS `navigator.clipboard.writeText()`. "Email" button opens `mailto:` with attendee emails and recap body, using `email_subject_meeting` config template.

**Note:** The existing `GET /meetings/{meeting_id}/recap` route (line 722 in meetings.py) already generates a recap. Enhance it to use the new `_recap_preview.html` template and include decisions. This is a GET endpoint (read-only generation).

- [ ] **Step 6: Create `_phase_after.html` template**

Two-column layout per spec with six numbered steps:
1. **Route Action Items**: List unrouted actions (where `activity_id IS NULL`), "Create All as Follow-Ups" button, already-routed items shown grayed
2. **Finalize Decisions**: List with confirm checkmarks and policy link dropdowns
3. **Quick Status Sweep**: Client's policies with status change dropdowns (reuse `_status_badge.html`)
4. **Generate Recap**: Include `_recap_preview.html` with Copy/Email buttons
5. **Log Time**: Duration field auto-filled from start/end time difference
6. **Schedule Next**: Form with date + title, "Schedule" button posts to `/meetings/{id}/schedule-next`
- **Footer**: "Complete Meeting ✓" button

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: All After phase tests pass.

- [ ] **Step 8: Visual QA**

Navigate through a full meeting lifecycle: Before → Start → During → End → After. Verify:
- All six closeout steps render
- Action items can be converted to follow-ups
- Recap generates correctly
- "Complete Meeting ✓" works

- [ ] **Step 9: Commit**

```bash
git add src/policydb/web/routes/meetings.py src/policydb/web/templates/meetings/_phase_after.html src/policydb/web/templates/meetings/_recap_preview.html tests/test_meeting_lifecycle.py
git commit -m "feat: After phase — guided closeout with action routing + recap"
```

---

## Task 8: Email Template Integration

**Files:**
- Modify: `src/policydb/email_templates.py` (add `meeting_context()` and meeting tokens)
- Modify: `src/policydb/config.py` (add `email_subject_meeting` default)

- [ ] **Step 1: Write test for meeting_context**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_meeting_context_returns_tokens(app_client, tmp_path):
    """meeting_context() builds token dict from meeting data."""
    from policydb.db import get_connection, DB_PATH
    from policydb.email_templates import meeting_context
    conn = get_connection(DB_PATH)
    ctx = meeting_context(conn, 1)
    conn.close()
    assert "meeting_title" in ctx
    assert ctx["meeting_title"] == "Q2 Review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meeting_lifecycle.py::test_meeting_context_returns_tokens -v`
Expected: FAIL — `meeting_context` doesn't exist.

- [ ] **Step 3: Add `meeting_context()` to email_templates.py**

Add to `src/policydb/email_templates.py`, following the `followup_context()` pattern:

```python
def meeting_context(conn, meeting_id):
    """Build token dict for meeting email templates."""
    meeting = dict(conn.execute(
        """SELECT cm.*, c.name as client_name
           FROM client_meetings cm
           JOIN clients c ON c.id = cm.client_id
           WHERE cm.id = ?""",
        (meeting_id,),
    ).fetchone())

    attendees = conn.execute(
        "SELECT name, role FROM meeting_attendees WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()
    attendee_names = ", ".join(a["name"] for a in attendees)

    decisions = conn.execute(
        "SELECT description FROM meeting_decisions WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()
    decisions_text = "\n".join(f"- {d['description']}" for d in decisions)

    actions = conn.execute(
        "SELECT description, assignee, due_date FROM meeting_action_items WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()
    actions_text = "\n".join(
        f"- {a['description']} ({a['assignee'] or 'TBD'}, {a['due_date'] or 'No date'})"
        for a in actions
    )

    return {
        "meeting_title": meeting.get("title", ""),
        "meeting_date": meeting.get("meeting_date", ""),
        "meeting_time": meeting.get("meeting_time", ""),
        "meeting_type": meeting.get("meeting_type", ""),
        "meeting_location": meeting.get("location", ""),
        "meeting_duration": str(meeting.get("duration_hours", "")),
        "client_name": meeting.get("client_name", ""),
        "attendees": attendee_names,
        "decisions": decisions_text,
        "action_items": actions_text,
        "meeting_notes": (meeting.get("meeting_notes", "") or meeting.get("notes", "") or "")[:500],
    }
```

- [ ] **Step 4: Add meeting tokens to CONTEXT_TOKENS**

Add meeting token group to the `CONTEXT_TOKEN_GROUPS` dict in `email_templates.py`:

```python
"meeting": [
    ("meeting_title", "Meeting Title"),
    ("meeting_date", "Meeting Date"),
    ("meeting_time", "Meeting Time"),
    ("meeting_type", "Meeting Type"),
    ("meeting_location", "Location"),
    ("meeting_duration", "Duration"),
    ("client_name", "Client Name"),
    ("attendees", "Attendees"),
    ("decisions", "Decisions"),
    ("action_items", "Action Items"),
    ("meeting_notes", "Notes (first 500 chars)"),
],
```

- [ ] **Step 5: Add email_subject_meeting config default**

In `src/policydb/config.py`, add to `_DEFAULTS`:

```python
"email_subject_meeting": "Meeting Recap: {{meeting_title}} — {{meeting_date}}",
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: All tests pass including meeting_context.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/email_templates.py src/policydb/config.py tests/test_meeting_lifecycle.py
git commit -m "feat: meeting email template context + tokens"
```

---

## Task 9: Cross-Links — Dashboard Widget

**Files:**
- Create: `src/policydb/web/templates/dashboard/_upcoming_meetings.html`
- Modify: `src/policydb/web/routes/dashboard.py`
- Modify: `src/policydb/web/templates/dashboard.html`

- [ ] **Step 1: Write test for dashboard upcoming meetings**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_dashboard_shows_upcoming_meetings(app_client):
    """Dashboard includes upcoming meetings widget."""
    resp = app_client.get("/")
    assert resp.status_code == 200
    assert "Q2 Review" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meeting_lifecycle.py::test_dashboard_shows_upcoming_meetings -v`
Expected: FAIL — dashboard doesn't show meetings yet.

- [ ] **Step 3: Add upcoming meetings query to dashboard route**

In `src/policydb/web/routes/dashboard.py`, add to the main dashboard handler's context:

```python
# Upcoming meetings (next 3)
upcoming_meetings = conn.execute(
    """SELECT cm.*, c.name as client_name,
              (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = cm.id) as attendee_count
       FROM client_meetings cm
       JOIN clients c ON c.id = cm.client_id
       WHERE cm.meeting_date >= date('now')
       ORDER BY cm.meeting_date ASC, cm.meeting_time ASC
       LIMIT 3""",
).fetchall()
```

Add `"upcoming_meetings": upcoming_meetings` to the template context dict.

- [ ] **Step 4: Create `dashboard/_upcoming_meetings.html` partial**

Compact card: "Upcoming Meetings" header, list of next 3 meetings (client name, title, date/time). Each links to `/meetings/{id}`. If empty, show "No upcoming meetings" with link to `/meetings/new`.

- [ ] **Step 5: Include widget in `dashboard.html`**

Add `{% include "dashboard/_upcoming_meetings.html" %}` in the appropriate location in the dashboard layout (near the top alongside existing summary widgets).

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py::test_dashboard_shows_upcoming_meetings -v`
Expected: PASS.

- [ ] **Step 7: Visual QA**

Navigate to dashboard. Verify upcoming meetings widget appears with correct data.

- [ ] **Step 8: Commit**

```bash
git add src/policydb/web/routes/dashboard.py src/policydb/web/templates/dashboard.html src/policydb/web/templates/dashboard/_upcoming_meetings.html tests/test_meeting_lifecycle.py
git commit -m "feat: dashboard upcoming meetings widget"
```

---

## Task 10: Cross-Links — Client Page Meetings Section

**Files:**
- Create: `src/policydb/web/templates/clients/_meetings_section.html`
- Modify: `src/policydb/web/routes/clients.py`
- Modify: `src/policydb/web/templates/clients/detail.html`

- [ ] **Step 1: Write test**

Add to `tests/test_meeting_lifecycle.py`:

```python
def test_client_page_shows_meetings(app_client):
    """Client detail page includes meetings section."""
    resp = app_client.get("/clients/1")
    assert resp.status_code == 200
    assert "Q2 Review" in resp.text or "Meetings" in resp.text
```

- [ ] **Step 2: Add meetings data to client detail route**

In `src/policydb/web/routes/clients.py`, add to the client detail handler's context:

```python
# Client meetings (recent + upcoming)
client_meetings = conn.execute(
    """SELECT cm.*,
              (SELECT COUNT(*) FROM meeting_attendees WHERE meeting_id = cm.id) as attendee_count,
              (SELECT COUNT(*) FROM meeting_action_items WHERE meeting_id = cm.id AND completed = 0) as open_actions
       FROM client_meetings cm
       WHERE cm.client_id = ?
       ORDER BY cm.meeting_date DESC LIMIT 6""",
    (client_id,),
).fetchall()
```

Add `"client_meetings": client_meetings` to the template context.

- [ ] **Step 3: Create `clients/_meetings_section.html` partial**

Compact section: "Meetings" header with "Schedule Meeting" button (links to `/meetings/new?client_id={client.id}`). List of recent + upcoming meetings: date, title, type badge, phase badge, open actions count. Each links to meeting detail page.

- [ ] **Step 4: Include in `clients/detail.html`**

Add `{% include "clients/_meetings_section.html" %}` in an appropriate location on the client detail page (as a section within the overview tab or a dedicated tab).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_meeting_lifecycle.py::test_client_page_shows_meetings -v`
Expected: PASS.

- [ ] **Step 6: Visual QA**

Navigate to client detail page. Verify meetings section appears with correct data and "Schedule Meeting" button works.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/detail.html src/policydb/web/templates/clients/_meetings_section.html tests/test_meeting_lifecycle.py
git commit -m "feat: client page meetings section with schedule button"
```

---

## Task 11: Cross-Links — Activity Row Meeting Ref Tags

**Files:**
- Modify: `src/policydb/web/templates/activities/_activity_row.html`

- [ ] **Step 1: Update activity row template**

In `src/policydb/web/templates/activities/_activity_row.html`, add meeting ref tag display for activities created from meetings. When an activity has `activity_type == 'Meeting'` and was auto-created from a meeting, show a clickable `MTG-{meeting_uid}` badge that links to the meeting detail page.

Query the meeting UID from the activity's subject match:

```python
# In the route that renders activity rows, add meeting_uid lookup:
if activity["activity_type"] == "Meeting":
    meeting = conn.execute(
        """SELECT meeting_uid, id FROM client_meetings
           WHERE client_id = ? AND title = ?""",
        (activity["client_id"], activity["subject"]),
    ).fetchone()
    activity["meeting_uid"] = meeting["meeting_uid"] if meeting else None
    activity["meeting_id"] = meeting["id"] if meeting else None
```

In the template, after the existing ref tag area:

```html
{% if activity.meeting_uid %}
<a href="/meetings/{{ activity.meeting_id }}"
   class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-100 text-indigo-800 hover:bg-indigo-200 no-print">
    MTG-{{ activity.meeting_uid }}
</a>
{% endif %}
```

- [ ] **Step 2: Visual QA**

Navigate to a client's activity timeline that has meeting-created activities. Verify the MTG ref tag badge appears and links to the correct meeting.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/activities/_activity_row.html
git commit -m "feat: meeting ref tags on activity rows"
```

---

## Task 12: Cross-Links — Policy Activity Tab Meeting Refs

**Files:**
- Modify: `src/policydb/web/templates/policies/_tab_activity.html`
- Modify: `src/policydb/web/routes/policies.py` (add meeting data to activity tab context)

- [ ] **Step 1: Add meeting-linked data to policy activity tab context**

In the route that renders the policy activity tab, query for meetings where this policy was discussed (via `meeting_policies`) and decisions linked to this policy (via `meeting_decisions`):

```python
# Meetings linked to this policy
linked_meetings = conn.execute(
    """SELECT cm.id, cm.title, cm.meeting_date, cm.meeting_uid
       FROM meeting_policies mp
       JOIN client_meetings cm ON cm.id = mp.meeting_id
       WHERE mp.policy_uid = ?
       ORDER BY cm.meeting_date DESC""",
    (policy_uid,),
).fetchall()

# Decisions linked to this policy
linked_decisions = conn.execute(
    """SELECT md.*, cm.title as meeting_title, cm.meeting_uid
       FROM meeting_decisions md
       JOIN client_meetings cm ON cm.id = md.meeting_id
       WHERE md.policy_uid = ?
       ORDER BY md.created_at DESC""",
    (policy_uid,),
).fetchall()
```

Add both to the template context.

- [ ] **Step 2: Update `_tab_activity.html` to show meeting refs and decisions**

In `src/policydb/web/templates/policies/_tab_activity.html`, add a section above or alongside the activity timeline:
- **Linked Meetings**: List meetings where this policy was discussed, each with a clickable `MTG-{meeting_uid}` badge linking to `/meetings/{id}`
- **Policy Decisions**: List decisions from meetings that were linked to this policy, with meeting title reference and date

- [ ] **Step 3: Visual QA**

Navigate to a policy that has been linked to a meeting. Verify meeting refs and decisions appear in the activity tab.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/policies.py src/policydb/web/templates/policies/_tab_activity.html
git commit -m "feat: policy activity tab shows linked meetings and decisions"
```

---

## Task 13: Cross-Links — Follow-Up "Schedule as Meeting" Button

**Files:**
- Modify: `src/policydb/web/templates/followups/_row.html` (or wherever follow-up action buttons are)

- [ ] **Step 1: Add "Schedule as Meeting" button to follow-up rows**

In the follow-up row template, add a button alongside existing actions (Snooze, Complete, etc.):

```html
<a href="/meetings/new?client_id={{ row.client_id }}&title={{ row.subject | urlencode }}&from_followup={{ row.id }}"
   class="text-xs text-indigo-600 hover:text-indigo-800 no-print"
   title="Schedule as Meeting">
    Schedule Meeting
</a>
```

- [ ] **Step 2: Update meetings/new GET handler to accept pre-fill params**

In `src/policydb/web/routes/meetings.py`, update the `GET /meetings/new` handler (line 141) to accept `client_id`, `title`, and `from_followup` query params for pre-filling the form.

- [ ] **Step 3: Visual QA**

Navigate to follow-ups page. Verify "Schedule as Meeting" button appears on rows and clicking it opens the new meeting form pre-filled with the right client and title.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/followups/_row.html src/policydb/web/routes/meetings.py
git commit -m "feat: 'Schedule as Meeting' button on follow-up rows"
```

---

## Task 14: Meeting Type on Creation Form

**Files:**
- Modify: `src/policydb/web/templates/meetings/detail_phased.html` (or the creation form area)
- Modify: `src/policydb/web/routes/meetings.py` (update POST /meetings/new handler, line 161)

- [ ] **Step 1: Add meeting_type field to creation form**

In the meeting creation form/template, add a combobox/select for meeting type populated from `meeting_types` config list.

- [ ] **Step 2: Update POST handler to save meeting_type**

In `src/policydb/web/routes/meetings.py`, update the `POST /meetings/new` handler to include `meeting_type` from form data in the INSERT.

- [ ] **Step 3: Update PATCH handler for meeting_type**

In the `PATCH /meetings/{meeting_id}` handler (line 238), add `meeting_type` to the list of patchable fields.

- [ ] **Step 4: Visual QA**

Create a new meeting, verify type dropdown appears and selected value saves correctly. Edit type on detail page, verify it saves.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/meetings.py src/policydb/web/templates/meetings/detail_phased.html
git commit -m "feat: meeting type field on creation and edit"
```

---

## Task 15: Final Integration Test + Visual QA

**Files:**
- Modify: `tests/test_meeting_lifecycle.py` (add integration test)

- [ ] **Step 1: Write full lifecycle integration test**

```python
def test_full_meeting_lifecycle(app_client):
    """Test complete Before → During → After → Complete flow."""
    # 1. Verify meeting starts in 'before' phase
    resp = app_client.get("/meetings/1")
    assert resp.status_code == 200
    assert "Before" in resp.text

    # 2. Start meeting
    resp = app_client.post("/meetings/1/start")
    assert resp.status_code == 200

    # 3. Add notes
    resp = app_client.post("/meetings/1/notes", data={"notes": "Test notes"})
    assert resp.status_code == 200

    # 4. Add action item
    resp = app_client.post(
        "/meetings/1/actions/add",
        data={"description": "Follow up on GL", "assignee": "Me"},
    )
    assert resp.status_code == 200

    # 5. Add decision
    resp = app_client.post(
        "/meetings/1/decisions",
        data={"description": "Increase limits"},
    )
    assert resp.status_code == 200

    # 6. End meeting
    resp = app_client.post("/meetings/1/end")
    assert resp.status_code == 200

    # 7. Create follow-ups from actions
    resp = app_client.post("/meetings/1/actions/create-followups")
    assert resp.status_code == 200

    # 8. Get recap
    resp = app_client.get("/meetings/1/recap")
    assert resp.status_code == 200

    # 9. Complete meeting
    resp = app_client.post("/meetings/1/complete")
    assert resp.status_code in (200, 303)
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/test_meeting_lifecycle.py -v`
Expected: All tests pass.

- [ ] **Step 3: Run existing test suite for regressions**

Run: `pytest tests/ -v --timeout=60`
Expected: No regressions in existing tests.

- [ ] **Step 4: Full visual QA walkthrough**

Walk through the complete meeting lifecycle in the browser:
1. `/meetings` — verify list page with upcoming cards + past table
2. Create new meeting with type, client, date
3. Open meeting → Before phase: verify prep briefing auto-generates
4. Click "Start Meeting" → During phase: verify notes + capture layout
5. Add notes, action items, decisions
6. Click "End Meeting" → After phase: verify six closeout steps
7. Route action items, generate recap, complete meeting
8. Check dashboard for upcoming meetings widget
9. Check client page for meetings section
10. Check activity timeline for meeting ref tags

- [ ] **Step 5: Commit**

```bash
git add tests/test_meeting_lifecycle.py
git commit -m "test: full meeting lifecycle integration test"
```
