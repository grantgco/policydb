# Meeting Notes — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

Meeting notes are currently captured as flat activity_log entries with no structure for attendees, agenda, action items, or easy retrieval. AEs need a central place to capture structured meeting notes tied to clients, with action items that feed into the follow-up system, and a prep view that pulls client context before the meeting.

---

## Solution

Dedicated `client_meetings` table with structured fields. Toast UI markdown editor for notes. Contact picker for attendees. Action items auto-create follow-ups. Meeting prep panel auto-pulls client context. Dedicated `/meetings` hub page for cross-client view.

---

## Data Model

### New migration: `055_add_meetings.sql`

```sql
CREATE TABLE IF NOT EXISTS client_meetings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    meeting_date  DATE NOT NULL DEFAULT (date('now')),
    meeting_time  TEXT,
    duration_hours REAL,
    location      TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meeting_attendees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES client_meetings(id) ON DELETE CASCADE,
    contact_id  INTEGER REFERENCES contacts(id),
    name        TEXT NOT NULL,
    role        TEXT,
    is_internal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meeting_action_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES client_meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    assignee    TEXT,
    due_date    DATE,
    completed   INTEGER NOT NULL DEFAULT 0,
    activity_id INTEGER REFERENCES activity_log(id)
);

CREATE INDEX IF NOT EXISTS idx_meetings_client ON client_meetings(client_id);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON client_meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meeting_attendees ON meeting_attendees(meeting_id);
CREATE INDEX IF NOT EXISTS idx_meeting_actions ON meeting_action_items(meeting_id);

CREATE TRIGGER IF NOT EXISTS client_meetings_updated_at
AFTER UPDATE ON client_meetings
BEGIN
    UPDATE client_meetings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

---

## Hub Page — `/meetings`

New route in a new `src/policydb/web/routes/meetings.py` module. Registered in `app.py`.

### List view
- Default: upcoming + recent meetings (next 7 days + last 30 days)
- Filter by client, date range
- Each row: date, time, client name (linked), title, attendee count, action item count (done/total), duration
- "New Meeting" button at top

### New meeting form
- Client selector (required)
- Title, date, time, location, duration
- Attendees: contact picker from client + internal contacts, plus free-text for external
- Notes: Toast UI markdown editor with auto-save
- Action items: inline add (description, assignee from attendees, due date)

### Meeting detail/edit page — `/meetings/{id}`
- Same form as create, pre-populated
- Notes editable with Toast UI
- Action items: check off completed, add new
- Meeting prep panel (collapsible)

---

## Meeting Prep Panel

Collapsible `<details>` section on the meeting form/detail page. Auto-loads via HTMX when opened. Shows:

- **Upcoming renewals** (next 180 days for this client)
- **Open follow-ups** (overdue + upcoming)
- **Recent activity** (last 30 days, 5 most recent)
- **Open RFI bundles** (not yet complete)
- **High/critical risks**

Data sources: reuse existing queries (`get_renewal_pipeline`, `get_all_followups`, `get_activities`, `_get_request_bundles`, client_risks query). Client ID drives all queries.

---

## Action Items → Follow-ups

When a meeting is saved with action items that have due dates:

1. For each action item with a `due_date` and no existing `activity_id`:
   - Create an `activity_log` entry: `activity_type="Meeting Action"`, `subject="[Meeting Title]: [action description]"`, `follow_up_date=due_date`, `client_id=meeting.client_id`
   - Store returned `activity_id` on the `meeting_action_items` row
2. When an action item is marked completed on the meeting page:
   - Also mark the linked `activity_log` entry's `follow_up_done=1`
3. When a linked follow-up is completed via the normal follow-up flow:
   - Also mark the `meeting_action_items.completed=1`

---

## Client Detail Page

New "Meetings" section on the client detail page (between Compose Email and Working Notes). Shows last 5 meetings for the client with date, title, attendee count. "View all →" links to `/meetings?client_id={id}`.

---

## Activity Log Integration

When a meeting is created, also create a single `activity_log` entry:
- `activity_type = "Meeting"`
- `subject = meeting.title`
- `details = "Attendees: [names]. [first 200 chars of notes]"`
- `duration_hours = meeting.duration_hours`
- `client_id = meeting.client_id`

This keeps the unified activity timeline intact.

### Flexible Time Tracking

The `duration_hours` on the meeting record captures the scheduled meeting length. However, AEs also spend time on prep and debrief. The meeting detail page should allow logging additional time entries:

- **Meeting time** — the scheduled duration (auto-populated from the meeting record)
- **Prep time** — optional, logged as a separate activity: `activity_type="Meeting Prep"`, `subject="Prep: [meeting title]"`
- **Debrief/follow-up time** — optional, logged as: `activity_type="Meeting Debrief"`, `subject="Debrief: [meeting title]"`

On the meeting detail page, show a small "Log additional time" section with:
- Prep hours input + "Log Prep" button
- Debrief hours input + "Log Debrief" button
- Total time display: meeting + prep + debrief

Each creates a separate `activity_log` entry linked to the same client, keeping granular time tracking while showing the total effort for the meeting.

---

## Files

| Action | File |
|--------|------|
| Create | `src/policydb/migrations/055_add_meetings.sql` |
| Create | `src/policydb/web/routes/meetings.py` |
| Create | `src/policydb/web/templates/meetings/list.html` |
| Create | `src/policydb/web/templates/meetings/detail.html` |
| Create | `src/policydb/web/templates/meetings/_prep_panel.html` |
| Create | `src/policydb/web/templates/clients/_meetings.html` |
| Modify | `src/policydb/web/app.py` (register meetings router) |
| Modify | `src/policydb/web/templates/base.html` (add Meetings nav link) |
| Modify | `src/policydb/web/templates/clients/detail.html` (include _meetings.html) |
| Modify | `src/policydb/db.py` (migration runner) |

---

## Verification

1. `policydb serve` — migration runs, tables created
2. Navigate to `/meetings` — empty state shows "No meetings yet"
3. Click "New Meeting" — form with client picker, date, attendees, notes editor
4. Add attendees via contact picker + free text
5. Add 2 action items with due dates
6. Save → redirects to meeting detail, action items visible
7. Check `/followups` — 2 new follow-ups from the action items appear
8. Mark one action item complete on meeting page → follow-up also marked done
9. Client detail page shows the meeting in the Meetings section
10. Open meeting detail → expand Prep panel → see renewals, follow-ups, recent activity
11. `/meetings` hub shows the meeting with filters working
