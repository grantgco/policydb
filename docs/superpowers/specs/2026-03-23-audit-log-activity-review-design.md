# Audit Log Activity Review — Design Spec

**Date:** 2026-03-23
**Issue:** #34
**Status:** Draft

## Problem

The audit log captures every data change automatically via SQLite triggers, but the activity log only has entries when you manually log an activity. You can spend significant time working on client accounts — updating policies, editing contacts, changing statuses — and have zero activity entries to show for it. This means:

1. **Lost billable time** — work sessions go unrecorded, making it hard to justify account effort
2. **Incomplete service records** — client activity history has gaps, hurting continuity and handoff quality

## Solution

A **batch analysis engine** that scans the audit log, clusters changes into per-client work sessions, identifies sessions with no corresponding activity entry, and surfaces them for review. Users triage suggestions via a new "Activity Review" tab in Action Center.

---

## Core Engine: Session Detection

### Algorithm

1. **Query audit log** for a date range, joining `audit_log.row_id` → `policies.policy_uid` (for policy changes) or directly to `clients` (for client changes) to resolve `client_id`
2. **Cluster by client + time gap** — audit entries for the same client within 30 minutes of each other form one work session
3. **Estimate duration** — `session_end - session_start`, rounded up to nearest 0.1 hours using `math.ceil(x * 10) / 10`. Minimum 0.1 hours for single-change sessions.
4. **Check for coverage** — query `activity_log` for entries matching the same `client_id` on the same date. If an activity exists whose `activity_date` falls within the session window (with ±30 min tolerance), the session is "covered" and suppressed.
5. **Write unmatched sessions** to `suggested_activities` table with status `pending`. Skip sessions that already have a suggestion row (idempotent re-runs).

### Table Mapping for Client Resolution

| audit_log.table_name | Join path to client_id |
|---|---|
| `clients` | `row_id` IS the client id |
| `policies` | `row_id` → `policies.policy_uid` → `policies.client_id` |
| `activity_log` | Already has `client_id` — skip (these ARE activities) |
| `contacts` | `row_id` → `contacts.id` → `contact_client_assignments.contact_id` → `contact_client_assignments.client_id` (use primary assignment; if multiple, attribute to all assigned clients) |
| `policy_milestones` | `row_id` → `policy_milestones.id` → `policy_milestones.policy_uid` → `policies.policy_uid` → `policies.client_id` |
| `inbox` | Skip — inbox processing is its own workflow |
| `saved_notes` | Skip — scratchpad edits aren't client work sessions |

### Filtered Operations

- **Skip `activity_log` changes** — these are the activities themselves, not work to be logged
- **Skip `inbox` and `saved_notes`** — not client-facing work
- **Skip INSERT on `audit_log` itself** — meta/recursive

### Bulk Operation Detection

When the reconciler or importer runs, it can generate dozens of audit entries within seconds. To avoid noisy suggestions:
- If a session has >20 changes AND all changes fall within a 60-second window, flag it as `bulk_operation` in the summary
- Bulk sessions are still written to `suggested_activities` but with status `dismissed` and a summary prefixed with "[Bulk Import]" — the user can review if they want but they won't clutter the pending queue

### Summary Text Generation

| table_name | INSERT | UPDATE | DELETE |
|---|---|---|---|
| `clients` | "Created client" | "Updated client info" | "Deleted client" |
| `policies` | "Created policy {uid}" | "Updated policy {uid}" | "Deleted policy {uid}" |
| `contacts` | "Added contact" | "Edited contact" | "Removed contact" |
| `policy_milestones` | "Added milestone" | "Updated milestone" | "Removed milestone" |

Summaries are aggregated: "Updated 3 policies (POL-042, POL-043, POL-044), edited 1 contact"

---

## Data Model

### `suggested_activities` table (new, migration 073)

```sql
CREATE TABLE IF NOT EXISTS suggested_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    session_date DATE NOT NULL,
    session_start DATETIME NOT NULL,
    session_end DATETIME NOT NULL,
    estimated_duration_hours REAL NOT NULL,
    tables_touched TEXT,          -- comma-separated: "policies, contacts"
    change_count INTEGER NOT NULL,
    policy_uids TEXT,             -- comma-separated: "POL-042, POL-043"
    summary TEXT NOT NULL,        -- "Updated 3 policies, edited 2 contacts"
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | dismissed | logged
    dismissed_at DATETIME,
    dismiss_expires_at DATETIME,  -- dismissed + 7 days
    logged_activity_id INTEGER,   -- FK to activity_log.id when logged
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE UNIQUE INDEX idx_suggested_activities_unique ON suggested_activities(client_id, session_start);
CREATE INDEX idx_suggested_activities_status ON suggested_activities(status);
CREATE INDEX idx_suggested_activities_date ON suggested_activities(session_date);
CREATE INDEX idx_suggested_activities_client ON suggested_activities(client_id);
```

### Status Flow

```
pending → dismissed (soft, expires in 7 days)
pending → logged (permanent, links to activity_log.id)
dismissed → pending (after 7-day expiry, if still no covering activity)
dismissed → logged (if user logs activity covering this window)
```

---

## UI: Action Center Tab

### Tab Placement

New "Activity Review" tab in Action Center, after the existing tabs. Tab label shows pending count badge (e.g., "Activity Review (3)").

### Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `GET /action-center/activity-review` | GET | Tab content partial (lazy-loaded) |
| `POST /action-center/activity-review/scan` | POST | Run analysis engine for date range |
| `POST /action-center/activity-review/{id}/log` | POST | Open pre-filled activity form |
| `POST /action-center/activity-review/{id}/dismiss` | POST | Soft dismiss a suggestion |

### Config Keys

| Key | Default | Purpose |
|---|---|---|
| `default_review_activity_type` | `"Other"` | Default activity type for pre-filled forms |
| `review_session_gap_minutes` | `30` | Minutes between changes to split sessions |
| `review_dismiss_days` | `7` | Days before dismissed suggestions resurface |

### Layout

**Header bar:**
- Date picker (default: today, can select range or "This Week")
- "Scan" button — runs the analysis engine for the selected range
- Summary: "X unlogged sessions found"

**Session cards** (one per unmatched work session, ordered by time):

```
┌─────────────────────────────────────────────────────────────┐
│ Acme Corporation                           2:15 PM - 2:42 PM│
│ ~0.5 hours · 12 changes                                     │
│ Updated 3 policies (POL-042, POL-043, POL-044)              │
│ Edited 1 contact                                            │
│                                                              │
│                              [Dismiss]  [Log Activity →]     │
└─────────────────────────────────────────────────────────────┘
```

- Client name links to client page
- Policy UIDs are clickable pills
- "Dismiss" → soft dismiss (7-day expiry), card fades out
- "Log Activity →" → opens pre-filled activity form

### Pre-filled Activity Form

Opens as inline expand or slideover (consistent with existing Action Center patterns). Pre-filled with:

| Field | Pre-filled Value |
|---|---|
| Client | From session |
| Activity Type | From `cfg.get("default_review_activity_type", "Other")` (editable combobox, pulls from `activity_types` config list) |
| Subject | Auto-generated summary (e.g., "Policy updates and contact edits") |
| Duration | Estimated from session, rounded to 0.1h |
| Date | Session date |
| Policy | First policy touched (editable) |
| Details | Bullet list of changes: "• Updated carrier on POL-042\n• Edited contact phone\n• Added milestone" |

On save: creates activity_log entry, marks suggestion as `logged` with `logged_activity_id`.

### Passive Badge

The Action Center navigation shows a count badge on the "Activity Review" tab when there are pending suggestions. This uses the same OOB counter pattern already used in Action Center tabs.

Computed via: `SELECT COUNT(*) FROM suggested_activities WHERE status = 'pending'`

---

## Trigger & Scheduling

| Trigger | What happens |
|---|---|
| Open Activity Review tab | Auto-scan for today if no scan has been run today (tracked via in-memory `_last_scan_date` variable in the route module; resets on server restart, which is fine for a local app) |
| Click "Scan" button | Run analysis for selected date range |
| Badge count | Lightweight COUNT query on page loads (no re-scan) |

### Dismiss Expiry

- `dismiss_expires_at = dismissed_at + 7 days`
- On each scan run, expired dismissals are reset to `pending`
- If an activity is subsequently logged covering the time window, suggestion becomes `logged` permanently

### Data Retention

- `suggested_activities` follows the same `log_retention_days` purge as `audit_log` (default 730 days)
- Added to existing `_purge_old_logs()` in `db.py`

---

## Files to Create/Modify

| File | Change |
|---|---|
| `src/policydb/migrations/073_suggested_activities.sql` | New table |
| `src/policydb/db.py` | Wire migration, add to purge function |
| `src/policydb/activity_review.py` | **New** — session detection engine |
| `src/policydb/web/routes/action_center.py` | Add Activity Review tab, scan endpoint, log/dismiss endpoints |
| `src/policydb/web/templates/action_center/_activity_review.html` | **New** — tab content partial |
| `src/policydb/web/templates/action_center/_review_card.html` | **New** — session card partial |
| `src/policydb/web/templates/action_center/page.html` | Add tab button + lazy-load trigger |

---

## Verification

1. **Engine test:** Create audit log entries manually, run scan, verify sessions are clustered correctly
2. **Coverage detection:** Log an activity for a client, re-scan, verify that covered session is suppressed
3. **UI test:** Open Action Center → Activity Review tab, verify cards render with correct data
4. **Log flow:** Click "Log Activity" on a card, verify form pre-fills correctly, submit, verify activity created and card marked as logged
5. **Dismiss flow:** Click "Dismiss", verify card disappears, verify it resurfaces after 7 days
6. **Badge test:** Verify pending count shows on tab, updates after logging/dismissing
7. **Idempotent scan:** Run scan twice for same date, verify no duplicate suggestions
