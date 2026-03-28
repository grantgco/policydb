# Activity–Issue Integration Design

**Date:** 2026-03-28
**Status:** Approved

## Overview

Restructure the activity system to make issues first-class citizens in the daily workflow. Activities are grouped by client and nested under issues in a kanban board view. Issues can be created, linked, and escalated from any activity entry point. The timeline engine and follow-up system feed escalation suggestions into a dedicated Weekly Plan review section.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Implementation approach | Issue-First Workflow (C) | Grouped view is easier to understand; makes the activity-issue relationship visible |
| Kanban grouping | Client columns (B) | Account-centric thinking matches how the user manages their book |
| Issue dropdown placement | Quick Log + Policy Row Log only | Issue detail log already auto-links; adding there is redundant |
| Escalate action scope | Both follow-up and activity rows | Any row could surface a pattern worth tracking |
| Escalate/create UX | Slideover panel (B) | Consistent with existing slideover pattern; room for context |
| Link activities UX | Slideover with filters + checkboxes (B) | Better filtering and bulk selection vs inline search |
| Issue widget behavior | Both link and navigate (C) | Primary: tag activity to issue; secondary: navigate to issue for full context |
| Auto-escalation | Suggest only, never auto-create (A) | Respects vacation catch-up; user stays in control |
| Suggestion location | Activities tab + Weekly Plan escalation review (C) | Day-to-day awareness in kanban; focused triage during weekly planning |
| ISS- prefix | Removed | New issues get plain 8-char hex UIDs (e.g., `A7F2B9E1`). Existing UIDs unchanged. |

---

## Component 1: Activities Tab — Client Kanban Board

### Layout

Replace the flat activity table with a horizontal kanban board. Each column represents one client that has recent activity within the filter window.

**Column structure:**
- **Header:** Client name (link to client page) + activity count + total hours
- **Issue cards:** One card per open issue for that client, colored by severity (red=Critical, amber=High, blue=Normal, gray=Low). Each card contains its linked activities in reverse chronological order, with a "+ log" action at the bottom.
- **Untracked section:** Below issue cards, separated by a dashed border. Shows activities not linked to any issue. Each has an "escalate" button.
- **Suggestion cards:** Amber cards at the bottom when stale patterns are detected. "Create Issue" opens the creation slideover; "Dismiss" hides the suggestion.

**Column sorting:** Clients with open issues sort first, then by most recent activity date descending.

**Activity cards within columns:**
- Type pill (color-coded: blue=Email, amber=Call, indigo=Meeting, pink=Note)
- Date
- Subject text
- "escalate" button (on untracked activities only)

### Board/Table Toggle

Segmented control in the filter bar: **Board** (new default) | **Table** (existing flat view).

- Same filters (days, type, client, search) apply to both views
- View preference saved to `sessionStorage` per-page
- Table view enhanced with issue badge pills and escalate buttons (see Component 3)

### Route Changes

- `_activities_ctx()` in `action_center.py` restructured to return client-grouped data:
  - `client_columns`: list of `{client_id, client_name, issues: [{issue, activities}], untracked: [activities], suggestions: [...]}`
  - Existing flat `activities` list still returned for table view
- New query function `get_client_activity_board(conn, days, activity_type, q, client_id)` in `queries.py`

### Template

New partial: `action_center/_activities_board.html` — the kanban view.
Existing `action_center/_activities.html` becomes the table view.
Wrapper partial switches based on `view_mode` variable.

---

## Component 2: Issue Dropdown on Activity Forms

### Quick Log Form (Action Center)

When the user selects a client in the Quick Log form, fetch open issues for that client via HTMX (`hx-get="/issues/for-client/{client_id}"`, `hx-trigger="change"`, `hx-target="#issue-widget"`).

**Issue widget** appears below the client/type row when the client has open issues:
- Header: "Open Issues for {Client}" with count badge
- Each issue row: severity dot, title (clickable link to issue detail), age, "Link" button
- Clicking "Link" sets a hidden `issue_id` field and shows a green confirmation bar: "Linked to: {issue title}" with "unlink" option
- If no open issues, widget is hidden (no empty state)

### Policy Row Inline Log

Add an optional "Issue" combobox field to the existing 6-column grid (making it 7 columns). Pre-filtered to open issues for the policy's client. Clearable. Uses the existing combobox pattern (`matrix-cell-combo`).

### API Endpoint

New endpoint: `GET /issues/for-client/{client_id}` — returns an HTML partial (the issue widget rows) for HTMX swap into the Quick Log form. Each row includes `id`, `issue_uid`, `subject`, `issue_severity`, `days_open`, `sla_days`. Registered on the issues router.

### Activity Log Route Changes

`POST /activities/log` and `POST /policies/{uid}/row/log` accept optional `issue_id` form field. If provided, the new activity is created with `issue_id` set. This replaces the silent auto-threading logic (which only worked when exactly one issue existed).

**Auto-threading removal:** The existing auto-link logic (check for exactly one open issue) is removed. Issue linking is now always explicit via the UI.

---

## Component 3: Issue Badge on Activity/Follow-up Rows

### Badge Design

Any activity or follow-up row that has an `issue_id` displays a colored pill badge:
- Severity dot (colored circle matching issue severity)
- Truncated issue title (max ~20 chars)
- Clickable — links to issue detail page
- Appears in both table view and follow-up rows

### Implementation

- Query joins: activity queries include `issue_uid`, `issue_subject`, `issue_severity` via LEFT JOIN on `activity_log` self-reference
- Partial: `_issue_badge.html` — reusable pill component
- Displayed in: `_activities.html` (table view), `_followup_sections.html`, `_activities_board.html` (within untracked cards, though these should be rare since linked items appear under their issue)

---

## Component 4: Escalate to Issue

### Escalate Button

Small button on every unlinked activity and follow-up row:
- Label: flag icon + "escalate"
- Style: `text-xs text-blue-600 border border-blue-200 rounded px-2 py-0.5`
- Hidden when the row already has an `issue_id` (shows issue badge instead)

### Behavior

Clicking "escalate" opens the Issue Creation Slideover (Component 5) pre-filled with:
- Title: activity's subject (editable)
- Client: inherited from activity (read-only)
- Policy: inherited from activity (read-only if set)
- Severity: defaults to Normal
- Context banner showing the originating activity
- Green confirmation: "Original activity will be linked"

On creation: the new issue is created, and the originating activity's `issue_id` is set to the new issue's `id`.

### Placement

- Follow-ups tab: all section rows (triage, today, overdue, stale, nudge_due, watching, scheduled)
- Activities tab table view: each row
- Activities tab board view: each untracked activity card

---

## Component 5: Issue Creation Slideover

### Unified Slideover

Single reusable slideover component for creating issues from any context. Follows the existing slideover pattern (`_compose_slideover.html`):

- Fixed right panel, 480px wide, z-50 with backdrop
- Three-zone layout: fixed header, scrollable content, sticky footer

### Fields

| Field | Escalate Entry | Suggestion Entry | New Issue Entry |
|-------|---------------|------------------|-----------------|
| Title | Pre-filled from activity subject | Pre-filled from suggestion context | Blank |
| Client | Inherited, read-only | Inherited, read-only | Selectable dropdown |
| Policy | Inherited if set, read-only | Optional | Selectable dropdown |
| Severity | Default Normal, pill selector | Pre-set by trigger type | Default Normal, pill selector |
| Details | Blank textarea | Blank textarea | Blank textarea |
| Auto-linked | Originating activity | All stale follow-ups from suggestion | None |

### Severity Selector

Four pill buttons (not a dropdown — per user preference for pills over selects):
- Critical (red dot, SLA 1d)
- High (amber dot, SLA 3d)
- Normal (blue dot, SLA 7d)
- Low (gray dot, SLA 14d)

SLA days auto-set from `issue_severities` config based on selection.

### Context Banner

When created from an activity or suggestion, a blue info banner shows the originating context:
- "Creating from activity" / "Creating from suggestion"
- Activity subject, date, client, policy

### Auto-Link Confirmation

Green bar at bottom of form: "Original activity will be linked" (or "3 stale follow-ups will be linked" for suggestions).

### Template

New partial: `_issue_create_slideover.html` — included in `action_center.html` and `base.html` (so it's available from any page where escalate buttons appear).

### Route

Existing `POST /issues/create` enhanced to accept:
- `source_activity_id` (optional) — link originating activity
- `source_activity_ids` (optional, comma-separated) — bulk link from suggestions
- Returns HTMX redirect to refresh the current view

The existing `POST /issues/convert/{activity_id}` remains for backward compatibility but the slideover is now the primary creation path.

### Replaces

The inline "New Issue" form in the Issues tab (`_issues.html`) is replaced with a "+ New Issue" button that opens this slideover.

---

## Component 6: Link Activities Slideover (Issue Detail Page)

### Trigger

New button on the issue detail page's Activity Timeline card: "+ Link Activity" — opens a slideover panel.

### Slideover Layout

Same pattern as creation slideover (480px, right-side, three zones):

**Header:** "Link Activities" + subtitle "Attach existing activities to this issue"

**Filter section** (below header, non-scrolling):
- Search input (by subject)
- Type filter dropdown
- Date range dropdown (Last 7/30/90 days)
- Helper text: "Showing unlinked activities for {client name}"

**Activity list** (scrollable):
- Pre-filtered to same client as the issue
- Shows only activities where `issue_id IS NULL`
- Each row: checkbox, type pill, date, subject, policy info, hours
- Selected rows get blue highlight background
- Sorted by date descending

**Footer:**
- "{N} selected" count
- "Link Selected" button (primary)
- "Cancel" button

### Route

New endpoint: `POST /issues/{issue_id}/link-activities` — accepts `activity_ids` (list of ints). Sets `issue_id` on each activity. Returns updated issue detail page via HTMX.

New endpoint: `GET /issues/{issue_id}/linkable-activities` — returns unlinked activities for the issue's client, filtered by query params (q, activity_type, days). Returns HTML partial for the activity list.

---

## Component 7: Weekly Plan Escalation Review

### Location

New section at the top of the Plan Week page (`/followups/plan`), above the existing day grid. Only visible when suggestions exist.

### Suggestion Banner

Amber card (`bg-amber-50 border-amber-200`) with:
- Header: warning icon + "Escalation Review" + count badge
- "Dismiss all" link (top right)
- List of suggestion rows

### Suggestion Row

Each row shows:
- Icon circle (amber for stale/nudge, red for timeline drift/critical)
- Description: client name + trigger summary
- Detail line: policy, metric, timeframe
- "Create Issue" button (opens creation slideover with pre-fills)
- "Dismiss" button

### Trigger Types

| Trigger | Source | Criteria | Severity Pre-set |
|---------|--------|----------|-----------------|
| Stale follow-ups | `activity_log` | Follow-ups overdue > `stale_threshold` (14d default) for same client, grouped | High |
| Timeline drift | `policy_timeline` | Milestone health = `at_risk` or `critical` | Critical for critical health, High for at_risk |
| Nudge escalation | `_compute_nudge_tier()` | 3+ unanswered `waiting_external` nudges on same policy in last 90d | High |
| Critical renewal | `get_escalation_alerts()` | CRITICAL tier: renewal ≤60d + Not Started + stale | Critical |

### Dismiss Behavior

- **Per-suggestion dismiss:** Stored in a new `escalation_dismissals` table: `(policy_id, trigger_type, dismissed_at)`. Suggestion reappears if underlying data changes (new activity logged, follow-up rescheduled, milestone date shifts).
- **"Dismiss all":** Inserts dismissals for all current suggestions. Useful for vacation catch-up.
- **No duplicates:** If an open issue already exists for the policy/program, that trigger is suppressed.
- **Reset condition:** A dismissal is ignored if `dismissed_at` is older than the most recent `activity_date` or `follow_up_date` change on the policy.

### Query Function

New function: `get_escalation_suggestions(conn)` in `queries.py` — aggregates all four trigger types, filters out dismissed and already-tracked (open issue exists), returns sorted by severity then age.

### Migration

New table `escalation_dismissals`:
```sql
CREATE TABLE IF NOT EXISTS escalation_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(policy_id, trigger_type)
);
```

---

## Data Model Changes

### New Table: `escalation_dismissals`

See migration SQL above. Tracks dismissed suggestions with automatic reset logic.

### Issue UID Change

`generate_issue_uid()` in `db.py` now returns plain 8-char uppercase hex (e.g., `A7F2B9E1`) without the `ISS-` prefix. Already implemented. Existing UIDs in the database retain their `ISS-` prefix — no migration needed since lookups are by exact string match.

### Activity Log Changes

No schema changes needed. The existing `issue_id` column handles all linking. The auto-threading logic in `POST /activities/log` is removed in favor of explicit `issue_id` from the form.

---

## Files Affected

### New Files

| File | Purpose |
|------|---------|
| `templates/action_center/_activities_board.html` | Kanban board view partial |
| `templates/_issue_create_slideover.html` | Unified issue creation slideover |
| `templates/_issue_badge.html` | Reusable issue badge pill partial |
| `templates/issues/_link_activities_slideover.html` | Link activities slideover for issue detail |
| `templates/followups/_escalation_review.html` | Weekly Plan escalation review banner |
| Migration `XXX_escalation_dismissals.sql` | New dismissals table |

### Modified Files

| File | Changes |
|------|---------|
| `routes/action_center.py` | `_activities_ctx()` restructured for client grouping; new API endpoints for issue widget |
| `routes/activities.py` | Accept `issue_id` on log; remove auto-threading; escalation review in plan_week |
| `routes/issues.py` | New endpoints: link-activities, linkable-activities; enhance create for source linking |
| `templates/action_center/_activities.html` | Add Board/Table toggle, issue badges, escalate buttons to table view |
| `templates/action_center/_issues.html` | Replace inline new-issue form with slideover trigger button |
| `templates/action_center/_followup_sections.html` | Add escalate button and issue badge to all row types |
| `templates/policies/_policy_row_log.html` | Add issue combobox field |
| `templates/issues/detail.html` | Add "+ Link Activity" button |
| `templates/followups/plan.html` | Add escalation review section |
| `queries.py` | New functions: `get_client_activity_board()`, `get_escalation_suggestions()` |
| `db.py` | `generate_issue_uid()` already updated (ISS- prefix removed) |
| `config.py` | Add `stale_threshold` to `_DEFAULTS` if not present (default 14) |
