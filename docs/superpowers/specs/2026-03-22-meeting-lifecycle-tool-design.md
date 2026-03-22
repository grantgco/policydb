# Meeting Lifecycle Tool — Design Spec

**Date:** 2026-03-22
**Status:** Approved
**Scope:** Enhanced meeting system with phased lifecycle (Before → During → After)

---

## Overview

A phased meeting lifecycle tool at `/meetings` (standalone, not inside Activity Center) that guides the user through Before → During → After for every client meeting. Auto-generates prep briefings from existing data, provides a focused side-by-side capture environment during meetings, and closes out with action routing, recaps, status updates, and next-meeting scheduling.

### Design Principles

- **Focus-first:** User has ADD — minimize distraction, show only what's needed for the current phase
- **Guided workflow:** Three sequential phases with a step indicator, not an open canvas
- **Auto-generated prep:** The system assembles the briefing; the user walks in prepared without manual effort
- **Easy capture:** Quick-add forms for action items and decisions during meetings (5-10 second capture)
- **No fragile parsing:** AI notes paste as-is into the notes field; user manually extracts action items using the same quick-add tools

---

## Page Structure

### 1. Meetings List Page (`GET /meetings`)

**Layout:** Split — upcoming meeting cards at top, past meetings as searchable table below.

**Header bar:**
- Page title "Meetings"
- Search input (searches client name, title, notes)
- Client filter dropdown
- Meeting type filter dropdown
- "+ New Meeting" button

**Upcoming section:**
- Shows next 3 upcoming meetings as prominent cards
- Each card displays: client name, meeting title, date/time, attendee count, meeting type badge, action item count
- Phase status badge: "New" (no prep), "Needs Prep" (has attendees but no talking points), "Prepped" (has talking points)
- Click card → meeting detail page

**Past meetings section:**
- Searchable table with columns: Client, Title, Type, Date, Actions (progress e.g. "2/4"), Status (Complete / "2 open")
- Click row → meeting detail page
- Sortable by date (default: most recent first)

### 2. Meeting Detail Page (`GET /meetings/{meeting_id}`)

**Page header (always visible):**
- Client name + meeting title (editable)
- Meeting type badge (from `meeting_types` config list)
- Date, time, duration, location, attendee count
- Phase indicator: `1 · Before` | `2 · During` | `3 · After` — clickable but natural flow is sequential

**Phase auto-advancement:**
- Opening a future meeting lands on "Before"
- Clicking "Start Meeting" moves to "During"
- Clicking "End Meeting" moves to "After"
- Clicking "Complete Meeting" marks as done
- Phases are always navigable (can go back to review)

---

## Phase 1: Before (Prep Briefing)

Auto-generated briefing from existing client/policy data. All sections populate automatically when the meeting is opened. Only the Talking Points section is manually editable.

### Briefing sections (in priority order):

**Left column (3/5 width):**

1. **Attendees & Contact Info** — Names, roles, phone, email. Pulled from meeting attendee list cross-referenced with contacts table. Always visible at top.

2. **Renewal Status Summary** — All active renewals for this client: policy type, carrier, effective date, current status, days to renewal. Color-coded urgency (red = urgent, amber = approaching, green = bound/complete).

3. **Outstanding Items** — Open follow-ups (with overdue count), incomplete milestones/RFIs, pending action items from previous meetings. "What's still hanging?" — the stuff to bring up.

4. **Schedule of Insurance** — Compact table: policy type, carrier, limits, deductible, premium, eff/exp dates.

5. **Recent Activity (Last 30 Days)** — Chronological timeline of calls, emails, meetings, follow-ups.

**Right column (2/5 width):**

6. **Account Pulse / Health** — Total premium, policy count, hours invested (YTD), overall health indicator.

7. **Talking Points / Agenda** — Editable list. User adds personal prep notes and agenda items. Not auto-generated. Contenteditable with click-to-add pattern.

**Footer:** "Start Meeting →" button advances to During phase.

### Data sources:
- Attendees: `meeting_attendees` joined with `contacts`
- Renewals: `v_renewal_pipeline` filtered to client
- Outstanding: `v_overdue_followups` + incomplete milestones from `policy_milestones` + open action items from previous `meeting_action_items`
- SOI: `v_schedule` filtered to client
- Activity: `activity_log` filtered to client, last 30 days
- Account Pulse: `v_client_summary` + `get_client_total_hours()`
- Talking points: stored in `client_meetings.agenda` column (TEXT)

---

## Phase 2: During (Side-by-Side Capture)

Split layout optimized for focus during the meeting.

### Left side (3/5 width) — Notes & Capture:

**Meeting Notes:**
- Large freeform contenteditable area, auto-saves on blur/interval
- This is where AI-generated notes get pasted
- Stored in `client_meetings.meeting_notes` (TEXT column)

**Action Items:**
- Running list below notes
- "+ Add" button expands an inline row: description, assignee (combobox from attendees + contacts), due date, optional policy link
- Saves immediately via HTMX POST
- Shows count badge: "Action Items (2)"

**Decisions:**
- Running list below action items
- "+ Add" button expands an inline row: description, optional policy link
- Diamond icon (◆) prefix for visual distinction from action items
- Shows count badge: "Decisions (1)"

### Right side (2/5 width) — Prep Reference:

Condensed read-only version of the Before briefing. Collapsible via "Collapse »" link.

Sections (compact):
- Attendees (names + roles only)
- Renewal statuses (one-line each)
- Outstanding items (one-line each)
- Talking points (strike-through as covered — click to toggle)

The talking points strike-through provides a passive checklist so the user doesn't lose track of what they meant to cover.

**Footer:** "End Meeting →" button advances to After phase.

---

## Phase 3: After (Guided Closeout)

Six numbered steps in a two-column layout. Each step is independent — can be completed in any order or skipped.

### Left column:

**1. Route Action Items → Follow-Ups**
- Lists all action items from the During phase
- "Create All as Follow-Ups" button converts each to an `activity_log` entry with `follow_up_date`, linked to the correct client and policy
- Individual review: each item shows assignee, due date, and inferred policy link. Can edit before creating.
- Uses existing `activity_log` INSERT pattern with `activity_type = 'Follow-up'`
- Back-links the created `activity_log.id` into `meeting_action_items.activity_id` to prevent duplicate creation
- Action items with a non-null `activity_id` are shown as "already routed" and skipped by "Create All"

**2. Finalize Decision Log**
- Lists decisions captured during the meeting
- Each can be linked to a policy (dropdown or combobox)
- Confirmed decisions get a checkmark
- Creates entries in `meeting_decisions` table with optional `policy_uid`
- Decisions linked to a policy also appear in that policy's activity timeline

**3. Quick Status Sweep**
- Shows all client renewals/policies with current status
- Each row has a "Change ▾" dropdown to update renewal status
- Uses existing `_status_badge.html` pattern with HTMX POST to `/policies/{uid}/status`
- Only shows active policies (not opportunities)

### Right column:

**4. Generate Recap**
- Auto-composed from: attendees, meeting notes (first ~500 chars or full), decisions, action items
- Rendered in a preview box
- "Copy" button → clipboard
- "Email" button → opens mailto with recap body, using `email_subject_meeting` config template, recipients from attendee email addresses
- Uses the existing email template token system: `{{meeting_title}}`, `{{meeting_date}}`, `{{attendees}}`, `{{decisions}}`, `{{action_items}}`

**5. Log Time**
- Duration field, auto-filled from meeting start/end time if both are set
- Manual override allowed
- Saved to the auto-created `activity_log` entry's `duration_hours` field
- Flows into time tracking / Account Pulse

**6. Schedule Next Meeting**
- "+ Schedule" button creates a new meeting pre-filled with:
  - Same client
  - Same attendees
  - Unresolved action items carried forward
  - Meeting type carried forward
- User just sets the date and optional title change
- Redirects to the new meeting's detail page (Before phase)

**Footer:** "Complete Meeting ✓" button marks the meeting phase as `complete`, ensures the activity_log entry is finalized with duration.

---

## Database Changes

### Existing tables (referenced, not modified):

**`meeting_attendees`** (migration 055 + 057):
- `id`, `meeting_id` (FK → client_meetings), `contact_id`, `name`, `role`, `is_internal`, `attendee_type`
- Used in: Before phase (attendee list), During phase (assignee combobox), After phase (recap recipients)

**`meeting_action_items`** (migration 055 + 056):
- `id`, `meeting_id` (FK → client_meetings), `description`, `assignee`, `due_date`, `completed`, `activity_id` (FK → activity_log), `policy_uid`
- Used in: During phase (action item capture), After phase (follow-up routing)
- `activity_id` populated when action item is converted to a follow-up activity

**`meeting_policies`** (migration 056):
- `id`, `meeting_id` (FK → client_meetings), `policy_uid`
- Rows created when user explicitly links a policy to a meeting (existing feature in meeting detail)
- Also used for cross-link queries on policy activity tab

### Modified tables:

**`client_meetings`** — add columns:
- `meeting_type` TEXT — from `meeting_types` config list
- `phase` TEXT DEFAULT 'before' — current phase: before, during, after, complete
- `meeting_notes` TEXT — large freeform notes from During phase
- `agenda` TEXT — talking points / agenda from Before phase
- `start_time` TEXT — actual meeting start time (when "Start Meeting" clicked)
- `end_time` TEXT — actual meeting end time (when "End Meeting" clicked)

### New tables:

**`meeting_decisions`**
```sql
CREATE TABLE IF NOT EXISTS meeting_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES client_meetings(id),
    description TEXT NOT NULL,
    policy_uid TEXT,
    confirmed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### New config list:

**`meeting_types`** added to `_DEFAULTS` in `config.py`:
```python
"meeting_types": [
    "Stewardship",
    "Renewal Strategy",
    "Claims Review",
    "New Business",
    "General Check-in",
    "Prospecting",
    "Annual Review"
]
```

Added to `EDITABLE_LISTS` in `settings.py` for Settings UI management.

### New email template tokens:

Added to `CONTEXT_TOKENS` under a `meeting` context key:
- `meeting_title`, `meeting_date`, `meeting_time`, `meeting_type`, `meeting_location`, `meeting_duration`
- `attendees` (comma-separated names)
- `decisions` (bullet list)
- `action_items` (bullet list with assignee + due date)
- `meeting_notes` (first 500 chars or configurable)

New config key: `email_subject_meeting` — default: `"Meeting Recap: {{meeting_title}} — {{meeting_date}}"`

New function: `meeting_context(conn, meeting_id)` in `email_templates.py` — builds token dict for meeting context, matching the existing pattern of `policy_context()`, `client_context()`, and `followup_context()`.

---

## Cross-Links

### Dashboard Widget
- "Upcoming Meetings" card in dashboard showing next 2-3 meetings
- Compact: client name, title, date/time
- Click → meeting detail page for prep
- Query: `SELECT * FROM client_meetings WHERE meeting_date >= date('now') ORDER BY meeting_date LIMIT 3`

### Client Page Section
- "Meetings" section on client detail page
- Shows recent (last 3) and upcoming meetings for that client
- "Schedule Meeting" button: creates new meeting with client auto-filled, suggests client contacts as attendees
- Compact list: date, title, type, phase status

### Policy Activity Tab
- Meetings where the policy was discussed appear in the activity timeline via two paths:
  1. **Explicit links:** rows in `meeting_policies` (user linked the policy to the meeting)
  2. **Decision links:** rows in `meeting_decisions` where `policy_uid` matches
- Clickable meeting ref tag (`MTG-{meeting_uid}`) links back to meeting detail
- Decisions linked to the policy also appear as separate timeline entries
- Query: `SELECT ... FROM meeting_policies mp JOIN client_meetings cm ... WHERE mp.policy_uid = ? UNION SELECT ... FROM meeting_decisions md JOIN client_meetings cm ... WHERE md.policy_uid = ?`

### Follow-Up → Meeting
- "Schedule as Meeting" action button on follow-up rows
- Creates a meeting pre-linked to the follow-up's client and policy
- Auto-fills client, suggests contacts, sets meeting title from follow-up subject

---

## Meeting Creation Entry Points

1. **`/meetings` → "New Meeting"** — Full creation form: client (combobox), date, time, duration, location, type, attendees
2. **Client page → "Schedule Meeting"** — Auto-fills client, suggests client contacts as attendees
3. **Follow-up row → "Schedule as Meeting"** — Auto-fills client + policy from follow-up context
4. **Completed meeting → "Schedule Next"** — Clones attendees, carries forward unresolved action items, same client + type

All entry points use the same creation form; they just pre-fill different fields.

---

## Implementation Phases

### Phase 1 (Ship First)
- Migration: add columns to `client_meetings`, create `meeting_decisions` table
- `meeting_types` config list + Settings UI entry
- Phased detail page with Before/During/After workflow
- Auto-generated prep briefing (Before phase)
- Side-by-side notes + prep reference (During phase)
- Enhanced list page (upcoming cards + past table)
- Dashboard upcoming meetings widget
- Client page meetings section
- Policy activity tab meeting refs
- Meeting ref tags on auto-created activities

### Phase 2 (Build On)
- Action item → follow-up routing (After phase step 1)
- Decision log with policy linking (After phase step 2)
- Quick status sweep (After phase step 3)
- Meeting recap generation + mailto (After phase step 4)
- Time logging with auto-fill (After phase step 5)
- Schedule Next meeting flow (After phase step 6)
- Convert follow-up to meeting entry point
- Create from client page entry point
- Meeting-specific email template tokens

---

## Routes

### Existing (enhanced):
- `GET /meetings` — list page (redesigned with cards + table)
- `GET /meetings/new` — creation form (enhanced with type field)
- `POST /meetings/new` — create meeting (add meeting_type, phase)
- `GET /meetings/{id}` — detail page (redesigned with phased layout)

### New:
- `POST /meetings/{id}/start` — advance phase to "during", record start_time
- `POST /meetings/{id}/end` — advance phase to "after", record end_time
- `POST /meetings/{id}/complete` — advance phase to "complete", finalize activity log
- `POST /meetings/{id}/notes` — save meeting notes (auto-save endpoint)
- `POST /meetings/{id}/agenda` — save talking points / agenda
- `POST /meetings/{id}/decisions` — add a decision
- `POST /meetings/{id}/decisions/{did}/confirm` — confirm a decision
- `POST /meetings/{id}/decisions/{did}/link` — link decision to policy
- `POST /meetings/{id}/actions/create-followups` — bulk convert action items to follow-ups
- `POST /meetings/{id}/recap` — generate recap HTML
- `POST /meetings/{id}/schedule-next` — create next meeting from current
- `GET /meetings/{id}/prep-briefing` — HTMX partial for prep briefing content
- `GET /dashboard/upcoming-meetings` — HTMX partial for dashboard widget
- `GET /clients/{id}/meetings` — HTMX partial for client page section

---

## Templates

### New:
- `meetings/detail_phased.html` — main detail page with phase indicator and phase content areas
- `meetings/_phase_before.html` — prep briefing layout
- `meetings/_phase_during.html` — side-by-side notes + reference
- `meetings/_phase_after.html` — six-step closeout
- `meetings/_prep_briefing.html` — auto-generated briefing content (reused in Before and During reference)
- `meetings/_decision_row.html` — single decision display/edit
- `meetings/_recap_preview.html` — formatted recap for copy/email
- `meetings/list_enhanced.html` — redesigned list with cards + table
- `meetings/_upcoming_card.html` — single upcoming meeting card
- `dashboard/_upcoming_meetings.html` — dashboard widget partial
- `clients/_meetings_section.html` — client page meetings partial

### Modified:
- `activities/_activity_row.html` — add meeting ref tag (MTG-xxx) for meeting-created activities
- `policies/_tab_activity.html` — include meeting-linked decisions in timeline
- `followups/_row.html` — add "Schedule as Meeting" action button
- `dashboard.html` — include upcoming meetings widget
- `clients/detail.html` — include meetings section
