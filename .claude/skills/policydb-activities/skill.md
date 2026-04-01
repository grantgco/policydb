---
name: policydb-activities
description: >
  Activity, follow-up, issue, and escalation system reference for PolicyDB. Use when working on
  activity logging, follow-up management, issue tracking, Action Center buckets, auto-close logic,
  supersession, merge/dissolve, nudge escalation, or any code that touches the activity_log table.
---

# Activity & Follow-up System

The `activity_log` table is the central hub — it stores both logged activities (past work) and follow-ups (future actions), plus issue headers. Understanding this dual-purpose design is critical.

## Table: `activity_log`

| Column | Purpose |
|--------|---------|
| `item_kind` | `'followup'` (default — activities & follow-ups) or `'issue'` (issue header rows) |
| `follow_up_date` | When set, makes the row a follow-up; NULL = just a logged activity |
| `follow_up_done` | 0 = open, 1 = completed/auto-closed |
| `disposition` | Outcome label (e.g., "Waiting on Client") — drives accountability |
| `issue_id` | FK to parent issue header row (self-referential) |
| `auto_close_reason` | Why auto-closed: `superseded`, `issue_resolved`, `issue_merged`, `stale`, `renewal_bound` |
| `auto_closed_at` | Timestamp of auto-closure |
| `auto_closed_by` | Which function/process triggered closure |
| `merged_from_issue_id` | Tracks source issue for activities moved during merge (enables dissolve) |

---

## Follow-up Sources

`get_all_followups()` in `queries.py` aggregates from 4 UNION sources:

| Source | What | Condition |
|--------|------|-----------|
| `activity` | `activity_log` rows | `follow_up_done=0 AND follow_up_date IS NOT NULL` |
| `project` | Project-scoped activities | Same, but `project_id IS NOT NULL AND policy_id IS NULL` |
| `policy` | `policies.follow_up_date` | Only if NO open activity follow-ups exist for that policy |
| `client` | `clients.follow_up_date` | Direct client-level reminders |

**Dedup rule:** Policy-source follow-ups are suppressed when activity-source follow-ups exist for the same policy — either directly (`policy_id`) or via program-level issue coverage (`v_issue_policy_coverage`). The `NOT EXISTS` subquery checks both paths. This prevents double-listing when a program issue has active follow-ups that cover child policies.

### Follow-up Date Precedence: Most Recent Record Controls

Follow-up dates can be set at three levels — activity, policy, and client. The **most recently set date always controls** what appears in the Action Center:

**Policy level:**
- When a new activity follow-up is logged on a policy, `supersede_followups()` in `queries.py`:
  1. Marks ALL older open activity follow-ups on that policy as `follow_up_done=1` (reason: `superseded`)
  2. Syncs `policies.follow_up_date` to the new activity's date
- The `NOT EXISTS` clause in `get_all_followups()` suppresses the policy-source row whenever any open activity follow-up exists — so the activity follow-up takes precedence
- If the user later sets `policies.follow_up_date` directly (e.g., via inline edit on the policy page) and there are no open activity follow-ups, the policy date appears instead
- **Net effect:** The last-written follow-up date wins. Activity follow-ups supersede policy dates; a direct policy date edit is visible only when no activity follow-ups are open.

**Client level:**
- `clients.follow_up_date` always appears in the follow-up list as its own row (source: `client`)
- It is NOT suppressed by activity or policy follow-ups — it represents a separate client-level reminder
- When marked done via Action Center bulk ops, `clients.follow_up_date` is set to NULL
- When snoozed, `clients.follow_up_date` is shifted forward by the snooze interval

**Key code paths that sync `policies.follow_up_date`:**
- `supersede_followups()` (`queries.py:824`) — syncs policy date when new activity follow-up is logged
- `complete_timeline_milestone()` / `re_diary()` — can clear or set policy date
- Policy row edit / quick-log endpoints — set policy date directly
- Bulk mark_done (`activities.py`) — sets `policies.follow_up_date = NULL`
- Bulk snooze (`activities.py`) — shifts `policies.follow_up_date` forward

**Key code paths that set `clients.follow_up_date`:**
- Client detail page inline edit (`clients.py:3175`)
- Bulk mark_done (`activities.py`) — sets `clients.follow_up_date = NULL`
- Bulk snooze (`activities.py`) — shifts `clients.follow_up_date` forward

---

## Classification Buckets

`_classify_item()` in `action_center.py` sorts each follow-up into exactly one bucket:

```
triage      — activity/project items with no disposition, due today or past
today       — my_action items due today
overdue     — my_action items 1..stale_threshold days past due
stale       — my_action items > stale_threshold days past due
nudge_due   — waiting_external items with follow_up_date <= today
watching    — future items (both my_action and waiting_external)
scheduled   — items with 'scheduled' accountability
```

**Stale threshold:** `cfg.get("stale_followup_days", 14)` — configurable in Settings.

**Triage exception:** Future items with no disposition go to `watching`, not `triage` — they're not actionable yet.

---

## Disposition & Accountability

Config: `follow_up_dispositions` list in `config.py`. Each entry:

```yaml
- label: "Waiting on Client"
  accountability: "waiting_external"
  default_days: 7
```

### Accountability values:
- **`my_action`** — ball is in your court (default for unknown dispositions)
- **`waiting_external`** — ball in someone else's court
- **`scheduled`** — meeting/call booked, date is firm

### Cadence tracking:
Compares actual `days_overdue` to disposition's `default_days`:
- **on_cadence** — within expected window
- **mild** — 1-2x over `default_days`
- **severe** — 2x+ over `default_days`

---

## Escalation & Nudge Tiers

`_compute_nudge_tier()` in `action_center.py` counts `waiting_external` activities for a policy in the last 90 days:

| Count | Tier | Meaning |
|-------|------|---------|
| 1 | `normal` | First follow-up |
| 2 | `elevated` | Second attempt |
| 3+ | `urgent` | Multiple attempts, escalation needed |

Used in the nudge_due bucket to signal how aggressively to follow up.

---

## Issue System

Issues are **header rows** in `activity_log` with `item_kind='issue'`. Child activities link via `issue_id` FK.

### Two kinds:
- **Renewal issues** (`is_renewal_issue=1`) — auto-created per policy/program
- **Manual issues** (`is_renewal_issue=0`) — user-created for ad-hoc problems

### Key fields on issue headers:
- `issue_uid` — unique identifier (e.g., "ISS-2026-042")
- `issue_status` — Open, Investigating, Waiting, Resolved, Closed
- `issue_severity` — Critical, High, Normal, Low (with SLA days)
- `renewal_term_key` — `policy_uid` or `program:{program_uid}` (uniqueness constraint)
- `merged_into_id` — points to target issue if merged

### Policy coverage:
`v_issue_policy_coverage` view maps each issue to all policies it covers:
- **Direct:** issue's own `policy_id`
- **Program:** all child policies of issue's `program_id`
- **Merged:** target issue inherits source issue's policies

---

## Automation Chain

### 1. Auto-Create Renewal Issues
**Function:** `ensure_renewal_issues()` in `renewal_issues.py`
**When:** Server startup
**Logic:** Scans policies/programs with expiration within `renewal_issue_window_days` (default 120). Creates one issue per standalone policy or per program. Child policies in programs are skipped — they roll up to the program issue.
**Config:** `renewal_issue_auto_create` (default: true)

### 2. Backfill Link
**Function:** `_backfill_link()` in `renewal_issues.py`
**When:** Immediately after issue creation
**Logic:** Links recent unlinked activities (within window_days) to the new issue. For program issues, links activities on the program OR any child policy.

### 3. Auto-Link on Activity Creation
**Function:** `auto_link_to_renewal_issue()` in `renewal_issues.py`
**When:** New activity is created without explicit `issue_id`
**Logic:** Checks for open renewal issue matching the activity's policy_uid, then falls back to program-level issue.
**Config:** `renewal_issue_auto_link` (default: true)

### 4. Supersede Follow-ups
**Function:** `supersede_followups()` in `queries.py`
**When:** New follow-up logged on a policy
**Logic:** Marks ALL older open follow-ups on that policy as `follow_up_done=1` with `auto_close_reason='superseded'`. Syncs `policies.follow_up_date` to the new date.

### 5. Auto-Close on Issue Resolution
**Function:** `auto_close_followups(reason='issue_resolved')` in `queries.py`
**When:** Issue status changes to Resolved or Closed
**Logic:** Closes all open follow-ups linked to that issue. Called from:
- `issues.py` status update endpoint
- `issues.py` resolve endpoint
- `issues.py` bulk resolve/status endpoints

### 6. Auto-Close on Issue Merge
**Function:** `auto_close_followups(reason='issue_merged')` in `queries.py`
**When:** Source issue is merged into target
**Logic:** Auto-closes stale follow-ups on source issue, then relinks activities to target with `merged_from_issue_id` tracking.

### 7. Auto-Resolve Renewal Issue on Bind
**Function:** `auto_resolve_renewal_issue()` in `renewal_issues.py`
**When:** Policy reaches terminal renewal status (e.g., "Bound")
**Logic:** Looks up the renewal issue `id`, sets issue to Resolved with `resolution_type='Completed'`, then calls `auto_close_followups(issue_id=..., reason='renewal_bound')` to close all linked follow-ups. Returns count of closed follow-ups.

### 8. Cascade Program Close
**Function:** `cascade_program_renewal_close()` in `renewal_issues.py`
**When:** A policy in a program is bound
**Logic:** Resolves the program-level renewal issue AND all child policy renewal issues in that program. For each resolved issue, calls `auto_close_followups()` to close linked follow-ups. Also closes direct policy follow-ups on each child policy and clears `policies.follow_up_date` on all sibling policies. Returns total count of closed follow-ups.

### 9. Stale Auto-Close
**Function:** `auto_close_stale_followups()` in `queries.py`
**When:** Server startup AND each Action Center follow-ups tab load
**Logic:** Closes follow-ups overdue by more than `stale_auto_close_days` (default 30). Only closes items without existing `auto_close_reason` (doesn't re-close manually superseded items). Runs periodically via Action Center to prevent accumulation during long-running sessions.

### 10. Severity Sync
**Function:** `sync_renewal_issue_severity()` in `renewal_issues.py`
**When:** Timeline health changes
**Logic:** Maps worst incomplete milestone health to issue severity:
- critical -> Critical, at_risk -> High, compressed/drifting -> Normal, on_track -> Low

---

## Merge & Dissolve

### Merge (`/issues/{target_id}/merge`)
1. For each source issue:
   - Auto-close stale follow-ups on source (`reason='issue_merged'`)
   - Relink all child activities from source to target (`issue_id = target`)
   - Set `merged_from_issue_id` on moved activities for dissolve tracking
   - Close source as "Duplicate" with `merged_into_id = target`

### Dissolve (`/issues/{target_id}/dissolve/{source_id}`)
- Moves activities with `merged_from_issue_id = source` back to source
- Reopens source issue (clears `merged_into_id`, resets status to Open)

### Merge Suggestions (`/issues/{issue_id}/mergeable`)
Scoring function `_score_merge_relevance()`:
- Same policy: +30, Same program: +20, Same location: +15
- Same renewal_term_key: +15, Fuzzy subject match: +0-20
- Same type (renewal/manual): +5, Same severity: +3
- Temporal proximity (<14 days): +0-7

---

## Milestone → Follow-up Dedup

In `_followups_ctx()` (action_center.py), overdue milestones are injected into the follow-up list but **skipped** if the policy is already covered by an active follow-up:

```python
_activity_policy_uids = {item.get("policy_uid") for item in all_items if item.get("source") == "activity"}
# Also suppress milestones for policies covered by program-level issues
_covered_uids = conn.execute("""
    SELECT DISTINCT p.policy_uid FROM v_issue_policy_coverage ipc
    JOIN policies p ON p.id = ipc.policy_id
    JOIN activity_log a ON a.issue_id = ipc.issue_id
    WHERE a.item_kind != 'issue' AND a.follow_up_done = 0 AND a.follow_up_date IS NOT NULL
""").fetchall()
_activity_policy_uids.update(r["policy_uid"] for r in _covered_uids)
```

This prevents double-listing from both direct activity follow-ups AND program-level issue follow-ups.

---

## Key Files

| File | Functions |
|------|-----------|
| `src/policydb/queries.py` | `get_all_followups`, `auto_close_followups`, `supersede_followups`, `auto_close_stale_followups`, `get_suggested_followups` |
| `src/policydb/renewal_issues.py` | `ensure_renewal_issues`, `auto_link_to_renewal_issue`, `_backfill_link`, `auto_resolve_renewal_issue`, `cascade_program_renewal_close`, `sync_renewal_issue_severity` |
| `src/policydb/web/routes/action_center.py` | `_classify_item`, `_compute_nudge_tier`, `_followups_ctx`, `_sidebar_ctx` |
| `src/policydb/web/routes/issues.py` | Issue CRUD, merge/dissolve, resolution, bulk operations |
| `src/policydb/web/routes/activities.py` | Activity creation, completion, supersession, re-diary |
| `src/policydb/timeline_engine.py` | Milestone health computation, severity sync trigger |
| `src/policydb/views.py` | `v_issue_policy_coverage`, `v_overdue_followups` |

---

## Config Keys

| Key | Default | Purpose |
|-----|---------|---------|
| `renewal_issue_auto_create` | true | Auto-create renewal issues on startup |
| `renewal_issue_auto_link` | true | Auto-link new activities to open renewal issues |
| `renewal_issue_window_days` | 120 | Lookahead window for issue creation + backfill |
| `stale_auto_close_days` | 30 | Days overdue before auto-closing stale follow-ups |
| `stale_followup_days` | 14 | Threshold for overdue vs stale bucket classification |
| `issue_auto_close_days` | 14 | Days after resolution before housekeeping closes issues |
| `follow_up_dispositions` | (list) | Disposition labels with accountability + default_days |
| `renewal_statuses_excluded` | (list) | Statuses excluded from suggested follow-ups and alerts |
| `issue_severities` | (list) | Severity labels with SLA days mapping |

---

## Automation Flow Diagram

```
Server Startup
  |-> ensure_renewal_issues() -- creates/updates renewal issues
  |-> auto_close_stale_followups() -- cleans 30+ day overdue items
  |-> housekeep_issues() -- closes old resolved issues

Action Center Follow-ups Tab Load
  |-> auto_close_stale_followups() -- periodic cleanup during long sessions

Activity Created
  |-> auto_link_to_renewal_issue() -- links to open renewal issue
  |-> supersede_followups() -- closes older follow-ups on same policy

Issue Status -> Resolved/Closed
  |-> auto_close_followups(reason='issue_resolved') -- closes linked follow-ups

Issue Merge
  |-> auto_close_followups(reason='issue_merged') -- closes source follow-ups
  |-> Relink activities to target (with merged_from tracking)

Policy Bound (terminal renewal status)
  |-> auto_resolve_renewal_issue() -- resolves issue + closes linked follow-ups
  |-> cascade_program_renewal_close() -- resolves program + sibling issues
       |-> auto_close_followups() per resolved issue
       |-> Clears policies.follow_up_date on all sibling policies

Timeline Health Changes
  |-> sync_renewal_issue_severity() -- updates issue severity from worst milestone
```
