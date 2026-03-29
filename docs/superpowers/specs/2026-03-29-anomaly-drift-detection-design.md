# Anomaly & Drift Detection System — Design Spec

**Date:** 2026-03-29
**Roadmap Item:** #7 — Anomaly & Drift Detection

## Goal

Proactively surface workflow problems, neglected accounts, workload imbalances, and data mismatches by scanning the book on every server startup. Findings appear in the Action Center and inline on affected records. All thresholds are user-configurable.

## Architecture

A startup scan engine (`anomaly_engine.py`) runs after data health and timeline engine in `init_db()`. It evaluates a set of rule functions against the current book state, writes findings to an `anomalies` table, and auto-resolves findings that no longer match on subsequent scans. No external dependencies — uses stdlib only.

## Rule Categories & Initial Rules

### 1. Falling Behind (severity: alert)

**renewal_not_started** — Policy expiring within `renewal_not_started_days` (default 60) with no logged activity containing a renewal-related disposition. Excludes opportunities and policies with renewal_status in excluded statuses.

**stale_followup_backlog** — More than `stale_followup_count` (default 10) open follow-ups across the entire book. Book-level finding (no specific client/policy).

**milestone_drift** — Policy has timeline milestones with health `at_risk` or `critical` that are not acknowledged. Links to the timeline.

**overdue_review** — Policy or program past its review cycle (`overdue_review_days`, default 90) with no `last_reviewed_at` within that window.

### 2. Neglected Accounts (severity: warning → alert)

**no_activity** — Client with active policies but no logged activity AND no review within `no_activity_days` (default 90). Severity escalates to `alert` at 2x the threshold (180 days).

**no_followup_scheduled** — Client with active policies but zero pending follow-ups. Warning-level — means nothing is planned.

### 3. Workload Forecasting (severity: warning)

**heavy_week** — More than `heavy_week_threshold` (default 5) policies expiring in any single week within the next `forecast_window_days` (default 30). Title includes the week date range and count.

**light_week** — Zero expirations in the next `light_week_window_days` (default 14). Informational — opportunity to catch up on neglected accounts or data health.

### 4. Mismatches (severity: alert)

**status_contradiction** — Renewal status is "Bound" or "Issued" but no effective date set. Or status is "In Progress" but no activity logged in `status_no_activity_days` (default 30).

**expired_no_renewal** — Policy expiration date is in the past, no renewal policy exists in the system, and status is not in a terminal state (Closed, Non-Renewed, etc.).

## Data Model

### `anomalies` table (new migration)

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER PK | Auto-increment |
| `rule_key` | TEXT NOT NULL | e.g., `renewal_not_started`, `no_activity` |
| `category` | TEXT NOT NULL | `falling_behind`, `neglected`, `workload`, `mismatch` |
| `severity` | TEXT NOT NULL | `warning` or `alert` |
| `client_id` | INTEGER | FK to clients (nullable for book-level findings) |
| `policy_id` | INTEGER | FK to policies (nullable for client-level findings) |
| `title` | TEXT NOT NULL | Human-readable one-liner |
| `details` | TEXT | Additional context (JSON or free text) |
| `status` | TEXT NOT NULL DEFAULT 'new' | `new`, `acknowledged`, `resolved` |
| `detected_at` | TEXT NOT NULL | ISO timestamp of first detection |
| `acknowledged_at` | TEXT | When user acknowledged |
| `resolved_at` | TEXT | When condition cleared (auto-resolved on next scan) |
| `scan_id` | TEXT NOT NULL | Groups findings from same scan run |

### Scan Behavior

- Each scan generates a `scan_id` (ISO timestamp of scan start).
- Before running rules, load all existing `new` and `acknowledged` findings.
- Each rule returns a list of `(rule_key, category, severity, client_id, policy_id, title, details)` tuples.
- For each finding, check if a matching prior finding exists (same `rule_key` + `client_id` + `policy_id`):
  - If yes and status is `new` or `acknowledged`: update `scan_id` to current (still active). Do not change status.
  - If no match: INSERT as `new`.
- After all rules run, any prior `new`/`acknowledged` findings whose `scan_id` was NOT updated to the current scan are auto-resolved: set `status = 'resolved'`, `resolved_at = now`.
- Resolved findings older than `log_retention_days` are purged on startup (same pattern as audit log).

## Review Gate

### Current Behavior
Clicking "Mark Reviewed" immediately stamps `last_reviewed_at`.

### New Behavior
Clicking "Mark Reviewed" triggers a condition check before stamping:

**Conditions evaluated:**
1. **Data health** — client/policy completeness score >= `review_min_health_score` (default 70%)
2. **Recent activity** — at least one logged activity within `review_activity_window_days` (default 30)
3. **Open anomalies** — any `new` anomalies for this record are flagged (not blocking, just shown)
4. **Overdue follow-ups** — no overdue follow-ups for the client/policy

**UI flow:**
- All conditions pass: stamp immediately, green flash confirmation.
- Any conditions fail: show a checklist card with pass/fail per condition.
  - **Fix & Review** — links to failing items (data health page, activity log, follow-ups)
  - **Override & Review** — requires a text reason. Stamps the date, stores the override reason on the review record.

**Override tracking:**
Add `review_override_reason` column to wherever `last_reviewed_at` is stored (policies, programs). Populated only on overrides, NULL on clean reviews.

## UI: Action Center Widget

### Anomalies Card (sidebar)

A compact card in the Action Center sidebar showing:
- Grouped counts: "3 falling behind · 1 neglected · 2 workload"
- Color-coded: red count for alerts, amber for warnings
- Click expands to a grouped list with one-line summaries per finding
- Each finding links to the affected client or policy
- "Acknowledge" button per finding (sets `status = 'acknowledged'`)
- "Refresh" button to re-run `scan_anomalies()` on demand

### Anomalies in Action Center main area

An optional "Anomalies" section within the Follow-ups tab (or its own tab if volume warrants it — start as a section, promote to tab if needed). Shows full finding cards with:
- Category badge (Falling Behind / Neglected / Workload / Mismatch)
- Severity indicator (amber dot / red dot)
- Title + details
- Link to affected record
- Acknowledge / Dismiss button

## UI: Inline Badges

### Client List
- Small amber or red dot on rows with active (non-resolved, non-acknowledged) anomalies.
- Tooltip on hover shows count and top finding title.

### Client Overview Tab
- Anomaly card (same pattern as the issues card): lists active findings for this client with severity dots, titles, and links.
- Only renders if `anomalies` exist for the client (conditional `{% if %}` like issues card).

### Policy Edit Page
- Badge in the header metadata row if the policy has active anomalies.
- Tooltip or small card showing finding titles.

### Review Queue
- Anomaly count badge per record so user can prioritize reviews that have flags.

## Configuration

All thresholds in `_DEFAULTS` under `anomaly_thresholds` key:

```yaml
anomaly_thresholds:
  # Falling Behind
  renewal_not_started_days: 60
  stale_followup_count: 10
  status_no_activity_days: 30

  # Neglected Accounts
  no_activity_days: 90
  no_followup_scheduled: true

  # Workload Forecasting
  heavy_week_threshold: 5
  forecast_window_days: 30
  light_week_window_days: 14

  # Mismatches
  bound_missing_effective: true
  expired_no_renewal: true

  # Review Gate
  review_min_health_score: 70
  review_activity_window_days: 30
  overdue_review_days: 90
```

Added to Settings UI under an "Anomaly Detection" section in `EDITABLE_LISTS` with labeled number inputs and boolean toggles.

## Files

| File | Action |
|------|--------|
| `src/policydb/anomaly_engine.py` | New: scan engine + rule functions |
| `src/policydb/migrations/109_anomalies.sql` | New: `anomalies` table |
| `src/policydb/db.py` | Wire migration 109, call `scan_anomalies()` on startup |
| `src/policydb/config.py` | Add `anomaly_thresholds` to `_DEFAULTS` |
| `src/policydb/web/routes/action_center.py` | Add anomaly counts to sidebar + main context |
| `src/policydb/web/routes/settings.py` | Add anomaly thresholds to Settings UI |
| `src/policydb/web/routes/issues.py` or new `anomalies.py` | Acknowledge/refresh endpoints |
| `src/policydb/web/templates/action_center/_sidebar.html` | Anomalies widget card |
| `src/policydb/web/templates/action_center/_anomalies.html` | New: anomalies section/card |
| `src/policydb/web/templates/clients/_tab_overview.html` | Anomaly card (like issues card) |
| `src/policydb/web/templates/policies/_header.html` | Anomaly badge |
| Review gate templates (policies/programs) | Condition checklist + override flow |
| `src/policydb/migrations/110_review_override.sql` | Add `review_override_reason` column |

## Dependencies

- Data health module (`data_health.py`) — for completeness scores in review gate
- Timeline engine (`timeline_engine.py`) — for milestone health in drift rules
- Review system (`last_reviewed_at`) — for neglect detection and review gate
- No new Python packages — stdlib `statistics` and `datetime` only

## Out of Scope

- Carrier concentration analysis (deferred — more strategic than workflow)
- Term-over-term premium comparison (requires historical policy data normalization)
- Email/notification alerts (future enhancement if anomaly system proves valuable)
- Scheduled background scanning (startup scan is sufficient for single-user local app)
