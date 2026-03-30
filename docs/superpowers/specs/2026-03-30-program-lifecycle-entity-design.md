# Program as Renewal Lifecycle Entity — Design Spec

## Problem

Programs with many child policies (e.g., a D&O program with 13 policies across 11 carriers) create noise across every system that operates per-policy: the renewal pipeline shows 13 individual rows, anomaly reports fire 13 separate alerts, and there's no way to log an activity or create an issue against the program as a whole. The user manages these renewals as one workflow, but the system treats each layer as independent.

## Solution

Make programs a first-class entity in the activity, anomaly, renewal pipeline, and issue systems. Programs own the renewal lifecycle; child policies own their data completeness.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Activity scope | Program-only (no cascade to children) | Avoids noise; child policies keep their own policy-specific activities |
| Pipeline appearance | Single program row, expandable to children | Collapses 13 rows to 1; click to see detail |
| Anomaly handling | Suppress child lifecycle alerts; fire at program level | One alert instead of 13; data completeness stays per-policy |
| Issue linking | Either program or policy (or both) | Flexible — invoice issues per-policy, renewal issues per-program |
| Spec scope | One spec, two implementation phases | Full picture upfront; Phase 1 delivers the highest-value changes |

---

## Phase 1 — Pipeline Collapse + Program Activities + Issues

### 1.1 Renewal Pipeline Collapse (Client Page)

**Query changes:**

The following queries in `src/policydb/queries.py` must exclude policies that belong to an active (non-archived) program:
- `get_renewal_pipeline()` — add `AND (p.program_id IS NULL OR NOT EXISTS (SELECT 1 FROM programs pg WHERE pg.id = p.program_id AND pg.archived = 0))`
- `get_suggested_followups()` — same filter
- `get_stale_renewals()` — same filter

**New query: `get_program_pipeline()`**

Returns one row per active program with renewal-relevant data:
- `program_uid`, `name`, `client_id`, `client_name`
- `renewal_status` (from programs table)
- `policy_count` (non-archived children)
- `carrier_count` (distinct carriers among children)
- `total_premium` (sum of children)
- `earliest_expiration` (MIN expiration_date among children)
- `days_to_renewal` (computed from earliest_expiration)
- `urgency` class (same logic as policy pipeline)

**UI changes — `src/policydb/web/templates/policies/_renewal_pipeline.html` (or equivalent):**

- Program rows render with a distinct style (program name instead of policy_type, policy count badge, carrier count)
- Click expands inline to show a compact child policy list: type, carrier, status pill, premium
- Standalone policies render unchanged
- Program rows sorted by `days_to_renewal` alongside standalone policies

**Client detail page** — the renewal pipeline visual/kanban at the top of the policies tab uses the same collapsed view. Programs appear as single cards.

### 1.2 Program-Level Activities

**No schema change needed** — `activity_log.program_id` exists (migration 104).

**Quick-log form on program overview tab:**

Add a compact log form to `src/policydb/web/templates/programs/_tab_overview.html` (below the child policies grid). Same pattern as `_policy_row_log.html`:
- Activity type (combobox), Subject, Details (optional), Duration (optional), Follow-up date (optional), Disposition (optional)
- POST to a new endpoint `POST /programs/{program_uid}/log`
- Sets `client_id` and `program_id` on the activity_log row; `policy_id = NULL`

**Route:** `src/policydb/web/routes/programs.py`
- `POST /programs/{program_uid}/log` — create activity with `program_id` set
- Reuses `round_duration()`, `supersede_followups()` patterns from existing activity logging

**Action Center integration:**

Follow-ups from program activities appear in the Action Center follow-ups tab. The follow-up row shows the program name instead of a policy UID. The existing `get_all_followups()` query in `queries.py` already includes `activity_log` rows — program activities with `follow_up_date` set will naturally appear. Need to add `program_id` and program name to the SELECT and display.

### 1.3 Program-Linked Issues

**No schema change needed** — `activity_log.program_id` exists. Issues are `item_kind='issue'` rows in `activity_log`.

**Issue creation:**
- The issue create slideover (`_issue_create_slideover.html`) gets an optional `program_id` field
- Escalating from a program activity auto-populates `program_id`
- Escalating from a policy activity auto-populates `policy_id` (existing behavior)
- Manual creation from program page defaults to program-linked

**Issue display:**
- Program detail page shows program-linked issues + child policy issues
- Issue detail page shows program name when `program_id` is set
- Action Center issue board includes program issues

**Issue queries:**
- `get_issues()` or equivalent needs to include `program_id` and join to `programs` for display name
- Program detail Activity tab already shows activities; issues (which are activities with `item_kind='issue'`) will appear naturally

---

## Phase 2 — Anomaly Suppression

### 2.1 Program-Level Anomaly Scan

**Schema change:** Add `program_id INTEGER` column to `anomalies` table (new migration).

**New anomaly rules in `src/policydb/anomaly_engine.py`:**

Program-level versions of lifecycle rules that check program data:
- `_rule_program_renewal_not_started` — program's `renewal_status` is "Not Started" and earliest child expiration is within threshold days
- `_rule_program_no_activity` — no activities logged against the program (or any child policy) within threshold days
- `_rule_program_expired_no_renewal` — all child policies expired and no newer term exists
- `_rule_program_no_followup_scheduled` — no follow-up date on any program activity or child policy

These rules query the `programs` table joined to child policies for aggregated date/activity data.

### 2.2 Child Policy Lifecycle Suppression

**Modify existing lifecycle rules** to skip policies that belong to an active program:

Rules to modify:
- `_rule_renewal_not_started` (line 122)
- `_rule_no_activity` (line 272)
- `_rule_expired_no_renewal` (line 542)
- `_rule_no_followup_scheduled` (line 341)
- `_rule_status_contradiction` (line 471)

Each adds a check: if `policy.program_id` is set and the program exists and is not archived, skip the policy. The program-level rule handles it instead.

**Rules that continue to fire per-policy** (data completeness, unaffected):
- `_rule_overdue_review`
- `_rule_milestone_drift`
- `_rule_stale_followup_backlog` (book-wide)
- `_rule_heavy_week` / `_rule_light_week` (book-wide)

### 2.3 Anomaly UI for Programs

- Anomaly dashboard shows program-level anomalies alongside policy anomalies
- Program anomaly rows link to the program detail page
- The program detail page shows its anomalies in a banner (same pattern as policy edit page escalation banner)

---

## Files Affected

### Phase 1
| File | Changes |
|------|---------|
| `src/policydb/queries.py` | Add program exclusion filter to pipeline queries; new `get_program_pipeline()` query; add `program_id`/program name to followup queries |
| `src/policydb/web/routes/programs.py` | New `POST /programs/{uid}/log` activity endpoint |
| `src/policydb/web/templates/programs/_tab_overview.html` | Quick-log form |
| `src/policydb/web/templates/policies/_renewal_pipeline.html` | Program row rendering + expand/collapse |
| `src/policydb/web/templates/clients/detail.html` or `_tab_policies.html` | Pipeline visual collapse |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Program name display for program follow-ups |
| `src/policydb/web/templates/_issue_create_slideover.html` | Optional program_id field |
| `src/policydb/web/routes/issues.py` | Accept program_id on issue create |
| `src/policydb/web/templates/programs/_tab_overview.html` | Issue display section |

### Phase 2
| File | Changes |
|------|---------|
| `src/policydb/migrations/112_anomaly_program_id.sql` | Add `program_id` to anomalies table |
| `src/policydb/db.py` | Wire migration 112 |
| `src/policydb/anomaly_engine.py` | Program-level rules + child suppression logic |
| `src/policydb/web/routes/anomalies.py` | Program anomaly display |
| `src/policydb/web/templates/programs/detail.html` | Anomaly banner |

---

## Key Reusable Functions

- `round_duration()` — `src/policydb/utils.py` — for program activity logging
- `supersede_followups()` — `src/policydb/queries.py` — follow-up chaining
- `get_program_child_policies()` — `src/policydb/queries.py:2612` — child policy aggregation
- `get_program_aggregates()` — `src/policydb/queries.py` — premium/carrier/count stats
- Anomaly rule pattern — `src/policydb/anomaly_engine.py` — each rule is a standalone function

## Verification

### Phase 1
1. Client policies tab: program children collapsed into single row; click to expand
2. Log activity from program overview → appears in program Activity tab and client timeline
3. Follow-up on program activity → appears in Action Center
4. Create issue from program → shows on program detail page
5. Standalone policies unaffected in pipeline

### Phase 2
1. Program with "Not Started" status and expiring children → single program anomaly, no child anomalies
2. Program with "In Progress" status → child lifecycle anomalies suppressed
3. Missing carrier on child policy → policy-level anomaly still fires (data completeness)
4. Program anomalies appear in anomaly dashboard with link to program
