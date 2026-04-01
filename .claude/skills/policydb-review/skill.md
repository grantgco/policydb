---
name: policydb-review
description: >
  Review system reference for PolicyDB. Use when working on the review queue, review slideover,
  gate conditions, anomaly engine integration, mark-reviewed flow, review cycle configuration,
  override logic, or any code that touches last_reviewed_at, review_cycle, or the v_review_queue view.
---

# Review System

The review system is a weekly workflow engine that cycles through policies, opportunities, and clients. It uses a **review queue**, **gate conditions**, and **lazy-loaded slideoverss** to provide a focused review workspace.

## Core Workflow

1. `v_review_queue` / `v_review_clients` identify records due for review
2. User opens a slideover for each record
3. Gate conditions are evaluated (4 checks)
4. If all pass: "Mark Reviewed" button. If any fail: "Override & Review" (requires reason)
5. `last_reviewed_at = CURRENT_TIMESTAMP` is set on submit
6. For **programs**: review cascades to all child policies

## Database Fields

| Table | Column | Purpose |
|-------|--------|---------|
| `policies` | `last_reviewed_at` | DATETIME — when last marked reviewed |
| `policies` | `review_cycle` | TEXT default `'1w'` — review frequency |
| `policies` | `review_override_reason` | TEXT — reason if reviewed with failed gates |
| `clients` | `last_reviewed_at` | DATETIME |
| `clients` | `review_cycle` | TEXT default `'1w'` |

### Review Cycles

```python
REVIEW_CYCLE_DAYS = {
    "1w": 7, "2w": 14, "1m": 30, "1q": 90, "6m": 180, "1y": 365,
}
```

Users can change review cycle per-policy/client without marking reviewed.

## Review Queue Views

### `v_review_queue` (Policies & Opportunities)

Includes all policies/opportunities where `last_reviewed_at IS NULL` OR `days_since_review >= review_cycle_days`. Excludes archived and program children (`program_id IS NULL`). Ordered by `days_to_renewal ASC`, then `last_reviewed_at ASC NULLS FIRST`.

Key computed fields: `urgency` (OPPORTUNITY/EXPIRED/URGENT/WARNING/UPCOMING/OK), `days_since_review`, `review_cycle_days`.

### `v_review_clients`

Same pattern for clients. Includes summary stats: `total_policies`, `total_premium`, `next_renewal_days`, `opportunity_count`.

## Gate Conditions (4 Checks)

All must pass OR user must provide an override reason. Implemented in `src/policydb/anomaly_engine.py`.

| # | Name | Check | Threshold |
|---|------|-------|-----------|
| 1 | **Data Health** | Data completeness score | >= 70/100 (`review_min_health_score`) |
| 2 | **Recent Activity** | Any activity in last N days | `review_activity_window_days` (default 30) |
| 3 | **No Open Anomalies** | Unresolved workflow anomalies | Count == 0 |
| 4 | **No Overdue Follow-ups** | Overdue follow-ups | Count == 0 |

### Return Format

```python
{
    "all_pass": bool,
    "conditions": [
        {"name": "Data Health", "passed": bool, "detail": "Score 85/100 (min 70)"},
        {"name": "Recent Activity", "passed": bool, "detail": "3 activities in last 30d"},
        {"name": "No open anomalies", "passed": bool, "detail": "2 open findings"},
        {"name": "No overdue follow-ups", "passed": bool, "detail": "1 overdue follow-ups"},
    ]
}
```

### Override Mechanism

If `all_pass == False`, the "Override & Review" button shows. User must type a reason. Stored in `review_override_reason` column. Review proceeds despite failed conditions.

## Slideover Architecture

### Policy Review Slideover

**Route:** `GET /review/policies/{uid}/slideover`

**Template:** `templates/review/_policy_review_slideover.html`

**Structure:** Fixed header + scrollable content + fixed footer.

#### Header
- Client name, policy type, UID, carrier, expiration
- Urgency badge + timeline health indicator
- Prev/Next navigation with queue position ("3/45")
- Review cycle dropdown + "Last reviewed Xd ago"

#### Content Sections (Scrollable)

| Section | Template | Loading | Content |
|---------|----------|---------|---------|
| Status & Dates | _policy_review_slideover.html | Immediate | Status badge, dates grid, follow-up, milestone profile |
| Open Issues | _policy_review_issues.html | `hx-trigger="load"` | Issues by severity, days open, SLA |
| Contacts | _policy_review_slideover.html | Immediate | Policy + client contacts with roles |
| Recent Activity | _policy_review_activity.html | `hx-trigger="revealed"` | Last 5 activities, hours, quick-log form |
| Notes | _policy_review_notes.html | `hx-trigger="revealed"` | Description + notes (contenteditable) |

#### Footer
- **Left:** Gate condition status — green checkmark if all pass, or list of failed conditions with details
- **Right:** "Full Page" link + "Mark Reviewed" / "Override & Review" button

### Client Review Slideover

**Route:** `GET /review/clients/{client_id}/slideover`

**Template:** `templates/review/_client_review_slideover.html`

Sections: Client summary, active policies, overdue follow-ups, open issues (lazy), recent activity (lazy), scratchpad.

## Key Routes

### Main Page
- `GET /review` — Review page with queue tables + stats banner

### Policy Review
- `GET /review/policies/{uid}/slideover` — Open slideover
- `POST /review/policies/{uid}/slideover/reviewed` — Mark reviewed (from slideover)
- `POST /review/policies/{uid}/reviewed` — Mark reviewed (from table row)
- `GET /review/policies/{uid}/gate` — Gate checklist partial
- `POST /review/policies/{uid}/cycle` — Change review cycle
- `POST /review/policies/{uid}/profile` — Assign milestone profile
- `GET /review/policies/{uid}/slideover/issues` — Lazy-load issues
- `GET /review/policies/{uid}/slideover/activity` — Lazy-load activity
- `GET /review/policies/{uid}/slideover/notes` — Lazy-load notes
- `POST /review/policies/{uid}/slideover/log` — Log activity from slideover
- `POST /review/policies/{uid}/slideover/field` — Per-field save

### Client Review
- `GET /review/clients/{client_id}/slideover` — Open slideover
- `POST /review/clients/{client_id}/slideover/reviewed` — Mark reviewed
- `GET /review/clients/{client_id}/slideover/issues` — Lazy-load issues
- `GET /review/clients/{client_id}/slideover/activity` — Lazy-load activity

## Anomaly Engine Integration

The anomaly engine (`src/policydb/anomaly_engine.py`) scans for 10+ workflow problems on server startup. These power Gate Condition #3 ("No Open Anomalies").

### Anomaly Rules

| Rule | Trigger | Default Threshold |
|------|---------|-------------------|
| `renewal_not_started` | Expiring soon, no activity | 60 days out |
| `stale_followup_backlog` | Too many open follow-ups | 10 total |
| `milestone_drift` | Critical/at_risk timeline | Unacknowledged |
| `overdue_review` | Not reviewed within window | 90 days |
| `no_activity` | Client dormant | 90 days |
| `no_followup_scheduled` | Active policies, no pending FUs | Enabled by default |
| `status_contradiction` | Data/status mismatch | 30 days no activity |
| `expired_no_renewal` | Expired, no replacement | Enabled by default |
| `heavy_week` | Many expirations in one week | > 5 per week |
| `light_week` | No expirations coming | 14 day window |

### Config Thresholds

All in `cfg.get("anomaly_thresholds")`:

```python
"anomaly_thresholds": {
    "renewal_not_started_days": 60,
    "stale_followup_count": 10,
    "status_no_activity_days": 30,
    "no_activity_days": 90,
    "heavy_week_threshold": 5,
    "forecast_window_days": 30,
    "light_week_window_days": 14,
    "review_min_health_score": 70,
    "review_activity_window_days": 30,
    "overdue_review_days": 90,
}
```

## Template Files

| Template | Purpose |
|----------|---------|
| `review/index.html` | Main review page (3 tables: policies, opportunities, clients) |
| `review/_policy_review_slideover.html` | Policy slideover shell (header + footer + inline sections) |
| `review/_policy_review_issues.html` | Lazy-loaded issues section |
| `review/_policy_review_activity.html` | Lazy-loaded activity + quick-log |
| `review/_policy_review_notes.html` | Lazy-loaded notes editor |
| `review/_client_review_slideover.html` | Client slideover shell |
| `review/_client_review_issues.html` | Client issues section |
| `review/_client_review_activity.html` | Client activity section |
| `review/_policy_row.html` | Table row for policy in queue |
| `review/_client_row.html` | Table row for client in queue |
| `review/_stats_banner.html` | Progress banner (total needing, reviewed this week) |
| `review/_review_gate.html` | Gate condition checklist |

## Common Patterns

### Issue Filtering for Policy Review

Issues in the slideover should ONLY include issues with `policy_id` matching the policy being reviewed. Do NOT include client-level issues (`policy_id IS NULL`).

```sql
WHERE a.item_kind = 'issue'
  AND a.policy_id = ?
  AND a.issue_status NOT IN ('Resolved', 'Closed')
```

### Program Review Cascade

When marking a program policy reviewed, cascade to all children:

```python
conn.execute(
    "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE program_id = ?",
    (program_id,),
)
```

### Stats Refresh

After marking reviewed, trigger banner refresh:
```python
response.headers["HX-Trigger"] = "refreshReviewStats"
```

### Follow-up Supersession from Slideover

When setting a follow-up date from the slideover, supersede existing pending follow-ups for the same policy via `supersede_followups(conn, policy_id, new_date)`.
