---
name: policydb-activity-review
description: Activity Review Engine reference for PolicyDB. Use when working on unlogged session detection, suggested activities, audit log scanning, the review gate "Recent Activity" condition, the anomaly engine no_activity rule, system vs user activity filtering, or any code that touches activity_review.py, suggested_activities table, or the review gates in anomaly_engine.py.
---

# Activity Review Engine

## Overview

The Activity Review Engine detects work sessions that happened (audit trail evidence) but weren't captured as logged activities. It scans `audit_log`, clusters changes into sessions, and writes unlogged sessions to `suggested_activities` for user review.

**Core principle:** Only flag genuinely unlogged *user* work. System-generated operations (milestone auto-creation, outlook sync, renewal issue auto-creation) must be excluded — they aren't work the user performed.

## System vs User Activity — Critical Filtering Rule

Every query that counts or checks activities for review/anomaly purposes MUST exclude system-generated activities. Three filter conditions:

```sql
AND activity_type NOT IN ('Milestone')
AND source = 'manual'
```

**System-generated activity types to exclude:**
- `activity_type = 'Milestone'` — auto-logged when policy status changes to Bound
- `source = 'outlook_sync'` — auto-imported from Outlook email sweep

**System-generated audit operations to exclude:**
- `(policy_milestones, INSERT)` — renewal checklist items auto-populated by system
- `(policy_milestones, DELETE)` — system cleanup of milestones

**User operations to KEEP:**
- `(policy_milestones, UPDATE)` — user checking off a milestone = real work
- All `clients`, `policies`, `contacts` operations — user is editing data
- `source = 'manual'` activities — user logged it themselves

### Where Filtering Applies

| Location | Function | What to filter |
|----------|----------|----------------|
| `activity_review.py` | Entry list building | Exclude `_SYSTEM_OPS` from audit entries |
| `activity_review.py` | `_has_covering_activity()` | Only count `source='manual'` and non-Milestone |
| `anomaly_engine.py` | Review gate "Recent Activity" | Only count user activities |
| `anomaly_engine.py` | `_rule_no_activity()` | Only count user activities for staleness |

## Architecture

```
audit_log (system writes every INSERT/UPDATE/DELETE)
  → scan_for_unlogged_sessions() in activity_review.py
    → Filter out _SYSTEM_OPS
    → Resolve entries to client_ids
    → Cluster into sessions (30 min gap)
    → Check for covering manual activity (±30 min window)
    → Auto-dismiss bulk imports (>20 changes in 60 sec)
    → Write to suggested_activities table
      → Action Center "Activity Review" tab shows pending items
```

## Key Functions

| Function | File | Purpose |
|----------|------|---------|
| `scan_for_unlogged_sessions()` | `activity_review.py` | Main scanner — audit log → suggested_activities |
| `_resolve_client_ids()` | `activity_review.py` | Map audit entries to client_ids via table lookups |
| `_cluster_sessions()` | `activity_review.py` | Group entries by client + time gap |
| `_has_covering_activity()` | `activity_review.py` | Check if user already logged an activity for this window |
| `_is_bulk_operation()` | `activity_review.py` | Detect >20 changes in 60 sec (import) |
| `get_pending_suggestions()` | `activity_review.py` | Fetch all pending suggested activities |
| `expire_dismissed_suggestions()` | `activity_review.py` | Reset expired bulk dismissals to pending |
| `get_review_gate_status()` | `anomaly_engine.py` | 4-gate check for mark-reviewed eligibility |
| `_rule_no_activity()` | `anomaly_engine.py` | Flag clients with no user activity in N days |

## Tracked Tables & Operations

```python
_TRACKED_TABLES = {"clients", "policies", "contacts", "policy_milestones"}

_SYSTEM_OPS = {
    ("policy_milestones", "INSERT"),   # system auto-creates checklist
    ("policy_milestones", "DELETE"),    # system cleanup
}
# policy_milestones UPDATE = user checked off milestone → KEEP
```

## Session Clustering

- Gap threshold: `review_session_gap_minutes` config (default 30)
- Entries within the gap are one session
- Each session → one `suggested_activities` row
- Duration: `ceil(elapsed_hours * 10) / 10`, minimum 0.1h

## Covering Activity Check

A session is "covered" (already logged) if a **manual, non-Milestone** activity exists within ±30 min of the session window. This prevents suggesting activities the user already logged.

**Important:** Same-date alone is NOT sufficient — a 9am call doesn't cover 3pm policy work.

## Bulk Import Detection

- Threshold: >20 changes within 60 seconds
- Auto-dismissed with `dismiss_expires_at` = now + `review_dismiss_days` (default 7)
- After expiry, resets to `pending` so user can review if needed

## Review Gates (anomaly_engine.py)

Four gates, all must pass to allow "Mark Reviewed":

1. **Data Health** — `score_client()` or `score_policies()` >= `review_min_health_score` (default 70)
2. **Recent Activity** — User-created activity count > 0 in last `review_activity_window_days` (default 30). **Excludes Milestone and outlook_sync.**
3. **No Open Anomalies** — Zero unresolved anomalies
4. **No Overdue Follow-ups** — Zero overdue follow-ups

## suggested_activities Schema

```sql
CREATE TABLE suggested_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    session_date TEXT NOT NULL,
    session_start TEXT NOT NULL,
    session_end TEXT NOT NULL,
    estimated_duration_hours REAL,
    tables_touched TEXT,
    change_count INTEGER,
    policy_uids TEXT,
    summary TEXT,
    status TEXT DEFAULT 'pending',    -- 'pending' or 'dismissed'
    dismissed_at TEXT,
    dismiss_expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
-- UNIQUE(client_id, session_start)
```

## Configuration

| Key | Default | Purpose |
|-----|---------|---------|
| `review_session_gap_minutes` | 30 | Session clustering threshold |
| `review_dismiss_days` | 7 | Bulk dismissal expiry |
| `anomaly_thresholds.review_min_health_score` | 70 | Data health gate |
| `anomaly_thresholds.review_activity_window_days` | 30 | Recent activity gate window |
| `anomaly_thresholds.no_activity_days` | 90 | Staleness detection threshold |

## Key Files

| File | Purpose |
|------|---------|
| `src/policydb/activity_review.py` | Audit log scanner, session clustering, suggestion CRUD |
| `src/policydb/anomaly_engine.py` | Review gates, no_activity rule, anomaly detection |
| `src/policydb/web/routes/action_center.py` | Activity Review tab UI |
| `src/policydb/migrations/073_suggested_activities.sql` | Schema |

## Edge Cases & Gotchas

- **Milestone check-off vs creation:** INSERT = system, UPDATE = user. Only UPDATE should trigger a session suggestion.
- **Outlook sync activities:** `source='outlook_sync'` should not satisfy the "Recent Activity" gate — the user didn't do the work, the system imported an email.
- **Meeting action items:** Auto-created follow-ups from meetings are `source='manual'` (they originated from a user-created meeting). These correctly count as user activity.
- **Renewal issue auto-creation:** `renewal_issues.py` creates issues automatically — these have `activity_type='Issue'` and `item_kind='issue'`. Currently not excluded from review gates because issues represent real work that needs attention.
- **Bulk import edge:** If a user manually enters 25 records quickly, the bulk detector auto-dismisses. Dismissal expires after 7 days and resurfaces as pending.
- **Disposition auto-follow-ups:** When disposing an activity, config may auto-create a next follow-up. These are `source='manual'` since they stem from user action — correctly counted.
