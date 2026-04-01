---
name: policydb-timeline
description: >
  Timeline Engine reference for PolicyDB. Use when working on milestone timelines, health
  computation, accountability tracking, milestone profiles, or Action Center follow-up
  sections. Covers the timeline_engine.py API, schema, accountability states, milestone
  profiles, and Action Center integration.
---

# Timeline Engine

`src/policydb/timeline_engine.py` — proactive workflow engine that tracks ideal vs projected dates per policy milestone.

## Key Functions
- `generate_policy_timelines(conn, policy_uid=None)` — generates timeline rows from milestone profiles. Called on startup. Pass `policy_uid` to regenerate a single policy.
- `get_policy_timeline(conn, policy_uid)` — returns all timeline rows ordered by ideal_date
- `compute_health(...)` — computes milestone health: `on_track` → `drifting` → `compressed` → `at_risk` → `critical`
- `recalculate_downstream(conn, policy_uid, changed_milestone, new_projected, expiration_date)` — shifts downstream dates when a milestone slips
- `update_timeline_from_followup(conn, policy_uid, milestone_name, disposition, new_followup_date, waiting_on)` — updates accountability + triggers recalc on re-diary
- `complete_timeline_milestone(conn, policy_uid, milestone_name)` — marks milestone done, syncs to checklist

## Schema
`policy_timeline` table (migration 070) with `ideal_date`, `projected_date`, `completed_date`, `prep_alert_date`, `accountability`, `waiting_on`, `health`, `acknowledged`, `acknowledged_at`. Policies have `milestone_profile` column.

## Accountability States
- `my_action` — your action needed
- `waiting_external` — ball in someone else's court
- `scheduled` — meeting/call booked
- Derived from disposition config.

## Milestone Profiles
`Full Renewal`, `Standard Renewal`, `Simple Renewal` — configurable in Settings. Each profile selects which milestones from `renewal_milestones` apply. Auto-suggest by premium threshold.

**Important:** Milestone profiles use `renewal_milestones` names, not `mandated_activities` names.

## Action Center Integration
Follow-ups tab restructured into 5 sections: Act Now, Nudge Due, Prep Coming Up, Watching, Scheduled. Portfolio health sidebar widget. Risk alerts banner with acknowledge.

## Programs
Timeline milestones live at the program level. Child policies (those with `program_id`) are excluded from timeline generation and from the review queue. Reviewing a program cascades `last_reviewed_at` to all children.
