# Disposition Editing, Cadence Enforcement & Bulk Operations

**Date:** 2026-03-24
**Status:** Approved

## Problem

Dispositions drive the entire follow-up triage system — they determine whether an item is "my action," "waiting external," or "scheduled," which controls urgency buckets and visibility. But setting and changing dispositions is inconsistent, buried behind multi-step forms, and unavailable in key workflows:

- Changing a disposition requires opening the full "Follow Up" form (4 steps for a 1-click operation)
- Items without dispositions lack a clear prompt to set one
- No visibility into whether follow-up cadence is being maintained
- No bulk operations for cleaning up queues
- Quick log forms don't expose disposition at all

## Source Types & ID Conventions

Follow-up items come from 4 sources, each with different data characteristics:

| Source | Table | Has disposition column? | ID format | How to "set disposition" |
|--------|-------|------------------------|-----------|--------------------------|
| `activity` | `activity_log` | Yes | `activity-{id}` | UPDATE activity_log row |
| `project` | `activity_log` | Yes | `activity-{id}` | UPDATE activity_log row |
| `policy` | `policies` | No | `policy-{policy_uid}` | Auto-create activity_log row, mark policy reminder superseded |
| `client` | `clients` | No | `client-{id}` | Auto-create activity_log row |
| `milestone` | `policy_timeline` | No (has `accountability`) | `ms-{policy_uid}-{name}` | Not applicable — excluded from disposition editing |

**Composite IDs:** All bulk and inline operations use composite IDs (e.g., `"activity-123"`, `"policy-POL042"`) consistent with existing patterns in `activities.py` (bulk-complete endpoint at line 1165, Plan Week apply-spread at line 725).

**Policy/client source conversion:** When setting a disposition on a policy-source or client-source item, the system auto-creates an `activity_log` row (subject derived from the existing reminder, disposition set, follow_up_date carried over). The original policy/client `follow_up_date` is then managed via `supersede_followups()`. This converts the reminder into a proper activity, which the triage engine can fully classify.

## Design

### 1. Inline Disposition Editing

**Interaction:** Every follow-up row (except milestones) gets a clickable disposition area in the date/info column.

- **Has disposition (activity/project source):** Shows the existing badge (e.g., "Left VM") with dashed border on hover. Click opens the hybrid pill bar.
- **No disposition (any source):** Shows a "+ disposition" prompt in gray dashed styling. Click opens the same pill bar.
- **Milestone items:** No disposition prompt — milestones use `accountability` on the timeline, not activity dispositions.

**Hybrid pill bar behavior:**

1. Click badge/prompt → pill bar appears inline below the row
2. Pick a disposition pill → single POST request to a new endpoint `/activities/update-disposition` that handles:
   - For activity/project source: UPDATE existing `activity_log.disposition` and `follow_up_date` (computed from `default_days`)
   - For policy/client source: auto-create `activity_log` row with disposition + follow_up_date, supersede the policy/client reminder
   - Timeline re-sync via `update_timeline_from_followup()` if the item has a `policy_uid`
3. HTMX swaps the section to reflect the reclassified item

**Optional expand ("..." button on pill bar):** Expands to show:
- Date override field with +1d/+3d/+7d shortcuts (overrides the auto-calculated date)
- Optional note text input (appended to activity details)
- Save button (combines disposition + date + note in one request)

**Simple pill click (no expand):** Disposition + auto-date are computed server-side in one request. No second PATCH needed.

**Update behavior:** For activity/project sources, the existing row is updated in place. No new activity created. The `audit_log` trigger captures old/new values.

**Screens:** Action Center follow-up rows, client detail activity rows, policy detail activity rows.

**New endpoint:** `POST /activities/update-disposition`
```
composite_id: "activity-123" | "policy-POL042" | "client-5"
disposition: "Left VM"
follow_up_date: "2026-03-27"  (optional — if omitted, auto-compute from default_days)
note: "Called again"           (optional — appended to details)
```

### 2. Cadence Enforcement

Each disposition has a `default_days` that defines the expected follow-up cadence. When an item exceeds that cadence, two visual indicators appear.

**Applies to:** Activity and project source items that have a disposition with `default_days > 0`. Policy/client source items and items without dispositions show no cadence indicator (cadence is undefined). Dispositions with `default_days = 0` (e.g., "Connected", "Received Response") also skip cadence display.

**Badge:** A small "cadence +Nd" text appears next to the overdue indicator on each row.
- On cadence (days_overdue <= default_days): green "on cadence" badge
- Mild break (days_overdue > default_days but < 2x): amber "cadence +Nd" badge
- Severe break (days_overdue >= 2x default_days): red "cadence +Nd !" badge

**Row heat:** The row's background color shifts as cadence degrades:
- On cadence: normal section background
- 1-2x over: warmer tint (blend toward `bg-amber-50`)
- 2x+ over: red tint (blend toward `bg-red-50`)

**Computation:** Server-side in `_followups_ctx()` — adds `cadence_over` and `cadence_tier` ("on_cadence", "mild", "severe") to each item dict. Template reads these to select badge/background classes.

**Screens:** Action Center (Overdue, Stale, Nudge Due sections), client activity Overdue section, policy activity Overdue section.

### 3. Bulk Operations

A "Bulk Edit" toggle button appears in the filter bar area on all follow-up screens.

**Activation:** Click "Bulk Edit" toggle → checkboxes appear on every follow-up row (except milestones). Selected rows highlight with blue tint.

**Floating action bar:** A dark bar appears at the bottom of the viewport (fixed position) when 1+ items are selected. Contains:
- Selection count ("3 selected")
- **Set Disposition** dropdown → opens disposition pill picker, applies to all selected
- **Snooze +1d / +3d / +7d** buttons → adjusts follow_up_date on all selected
- **Mark Done** button → completes all selected items
- Cancel button → exits bulk mode

**New endpoint:** `POST /activities/bulk-action`
```json
{
  "ids": ["activity-123", "activity-456", "policy-POL042"],
  "action": "set_disposition" | "snooze" | "mark_done",
  "disposition": "Left VM",
  "days": 3
}
```

**Source-aware handling per action:**

| Action | activity/project source | policy source | client source | milestone |
|--------|------------------------|---------------|---------------|-----------|
| set_disposition | UPDATE activity_log.disposition + follow_up_date | Auto-create activity_log row | Auto-create activity_log row | Excluded |
| snooze | UPDATE activity_log.follow_up_date += days | UPDATE policies.follow_up_date += days | UPDATE clients.follow_up_date += days | Excluded |
| mark_done | SET follow_up_done=1 | SET policies.follow_up_date=NULL | SET clients.follow_up_date=NULL | Excluded |

This mirrors the existing source-aware branching in the bulk-complete endpoint (`activities.py:1165-1216`).

**After bulk action:** The follow-up section refreshes via HTMX. Bulk mode stays active.

**Screens:** Action Center, client detail, policy detail — all follow-up sections.

### 4. Quick Log Disposition

The inline "Log Activity" forms on client and policy pages gain a disposition pill row.

**Placement:** Below the subject/details fields, above the submit button. Labeled "Disposition (optional — sets accountability)".

**Behavior:** Selecting a disposition pill:
1. Sets a hidden `disposition` form field value
2. Auto-fills the follow-up date field using `default_days` from today (only if the follow-up date field is currently empty)

The disposition value is submitted as part of the existing form POST to `/activities/log`. No endpoint changes needed — the log endpoint already accepts a `disposition` parameter.

## Files to Modify

| File | Change |
|------|--------|
| `src/policydb/web/routes/activities.py` | New `POST /activities/update-disposition` endpoint; new `POST /activities/bulk-action` endpoint |
| `src/policydb/web/routes/action_center.py` | Add cadence computation to `_followups_ctx()` |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Clickable disposition in `fu_row` + `triage_row` macros; cadence badge/heat; bulk checkboxes |
| `src/policydb/web/templates/activities/_activity_row.html` | Clickable disposition badge; cadence badge; bulk checkbox |
| `src/policydb/web/templates/activities/_activity_sections.html` | Bulk Edit toggle in header; floating action bar |
| `src/policydb/web/templates/clients/_tab_overview.html` | Disposition pills in quick log form |
| `src/policydb/web/templates/policies/_tab_activity.html` | Disposition pills in policy log form |
| `src/policydb/web/templates/base.html` | Shared JS for pill bar toggle, bulk mode |

## Implementation Order

1. **Inline disposition editing** (Tier 2) — highest daily impact, establishes composite ID pattern and the `update-disposition` endpoint
2. **Quick log disposition** — small template add-on, uses same pill bar component
3. **Cadence enforcement** (Tier 3) — server-side computation + template display, independent of Tier 2
4. **Bulk operations** (Tier 4) — builds on composite ID pattern from Tier 2, adds bulk endpoint

## Verification

- Set a disposition on an activity-source item without one → item reclassifies into correct bucket
- Set a disposition on a policy-source reminder → activity_log row auto-created, policy reminder superseded
- Change a disposition → item moves between buckets (e.g., "Left VM" → "Connected" moves from Nudge Due to Today/Overdue)
- Cadence badge shows correct values on overdue items with dispositions; no badge on items without dispositions
- Bulk select 3 items (mixed sources) → Set Disposition → all 3 reclassify correctly
- Bulk snooze → all selected items' dates shift (activity, policy, and client sources handled)
- Bulk Mark Done → activity items get follow_up_done=1, policy items get follow_up_date=NULL
- Quick log with disposition → new activity appears in correct triage bucket
- Milestone items excluded from all disposition/bulk operations
- All operations work on Action Center, client pages, and policy pages
