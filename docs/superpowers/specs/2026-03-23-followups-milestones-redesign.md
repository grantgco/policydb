# Follow-ups Urgency Tiers + Timeline Milestone Activation

**Date:** 2026-03-23
**Status:** Draft

## Context

Two connected problems need solving:

1. **Follow-ups "Act Now" section is broken.** Everything lands in a single red "Act Now" bucket because items without a disposition default to `my_action`. The red color implies emergency when it should convey "here's your work." Legacy data (pre-disposition system) and policy/client reminders all pile in, making the section useless for prioritization.

2. **Timeline milestone engine is dead.** The code is fully implemented (`timeline_engine.py`, migration 070, `policy_timeline` table) but the table is empty because no policies have `milestone_profile` assigned. The auto-assignment rules exist in config (`milestone_profile_rules`) but are never called. The "Prep Coming Up" section in Action Center has nothing to show.

**Intended outcome:** A follow-ups tab with proportional urgency signals (red is earned), a triage bucket for uncategorized items, milestone prep as a separate lane, and overdue milestones auto-surfacing as follow-up items. The timeline engine starts producing data through a suggest-and-confirm profile assignment flow.

---

## Part 1: Follow-ups Urgency Tiers

### Section Structure (top to bottom)

| Section | Color | Border | Background | Criteria | Default State |
|---------|-------|--------|------------|----------|---------------|
| **Triage** | Gray | `#d1d5db` | `#f9fafb` | Activity follow-ups with NULL/empty disposition | Expanded |
| **Today** | Blue | `#3b82f6` | `#eff6ff` | `my_action` items due today + milestone items due today | Expanded |
| **Overdue** | Amber | `#f59e0b` | `#fffbeb` | `my_action` items 1-14 days past due + milestone items 1-14d past projected_date | Expanded |
| **Stale** | Red | `#ef4444` | `#fef2f2` | `my_action` items 14+ days past due + milestone items 14+d past projected_date | Expanded |
| **Nudge Due** | Indigo | `#6366f1` | `#eef2ff` | `waiting_external` items due/overdue | Expanded |
| **Prep Coming Up** | Purple | `#8b5cf6` | `#f5f3ff` | Timeline milestones with `prep_alert_date <= today` AND `projected_date > today` | Expanded if items |
| **Watching** | Gray | `#d1d5db` | white | Future items: `waiting_external` + future `my_action` (with "my turn" badge) | Collapsed |
| **Scheduled** | Indigo | `#818cf8` | white | `accountability == 'scheduled'` | Collapsed |

### Summary Bar

Compact horizontal bar at top of follow-ups tab showing counts per section:
`3 Today | 2 Overdue | 1 Stale | 4 Triage | 2 Prep | 1 Nudge`

Color-coded count numbers matching section colors. Clickable to jump to section.

### Triage Rules

**Goes to Triage:**
- Activity follow-ups (`activity_log` source) with `disposition` that is NULL or empty string

**Skips Triage (goes to date-based tiers):**
- Policy reminders (`policies.follow_up_date` source) — user deliberately set a date
- Client follow-ups (`clients.follow_up_date` source) — user deliberately set a date
- Any activity follow-up with a disposition already set
- Milestone-generated items (they have inherent accountability)

**Triage item UX:**
- Dashed border (visually distinct from "real" work items)
- "Set disposition →" button opens inline disposition pill selector
- On disposition set: item moves to the correct section based on new accountability + date
- Returns updated HTML via HTMX swap

### Date Tier Thresholds

| Tier | Condition | Config Key |
|------|-----------|------------|
| Today | `follow_up_date == today` | — |
| Overdue | `1 <= days_overdue <= stale_threshold` | `stale_threshold_days` (existing, default: 14) |
| Stale | `days_overdue > stale_threshold` | `stale_threshold_days` (existing, default: 14) |

`stale_threshold_days` already exists in `config.py` with default 14. Add to Settings UI for user adjustment.

### Date Display

| Tier | Date Format | Example |
|------|-------------|---------|
| Today | "Today" | Today |
| Overdue | "Nd ago" | 3d ago |
| Stale | "Nd ago" | 14d ago |
| Nudge Due | "Nd ago" | 2d ago |
| Watching | Date | Apr 5 |
| Scheduled | Date | Mar 28 |

### Watching Section — Mixed Items

The Watching section holds two kinds of future items:
- **`waiting_external`** items with future `follow_up_date` — shown with `↻` icon
- **`my_action`** items with future `follow_up_date` — shown with `●` icon and a **"my turn"** badge

Both are collapsed by default. This handles the case where a user sets a callback for tomorrow (my_action, future date) — it stays in Watching until the date arrives, then moves to Today.

### Nudge Escalation — Policy + Disposition Counting

**Replaces broken `thread_id` counting.** The old approach counted activities by `thread_id`, but `thread_id` is legacy (NULL for all new data).

**New approach:** Count consecutive `waiting_external` activities for the same `policy_uid`:

```sql
SELECT COUNT(*) FROM activity_log
WHERE policy_id = (SELECT id FROM policies WHERE policy_uid = ?)
  AND disposition IN (
    SELECT json_extract(value, '$.label')
    FROM json_each(?)  -- follow_up_dispositions config
    WHERE json_extract(value, '$.accountability') = 'waiting_external'
  )
  AND activity_date >= date('now', '-90 days')
```

**Escalation tiers (unchanged thresholds):**
- Normal (1 nudge): Indigo styling
- Elevated (2+): Amber badge "2nd nudge"
- Urgent (3+): Red badge "Consider escalating"

---

## Part 2: Timeline Engine Activation

### Profile Suggestion Flow

1. **On server startup:** `generate_policy_timelines(conn)` runs as today (existing behavior)
2. **New: Compute suggestions.** For policies without a `milestone_profile`, compute the suggested profile from `milestone_profile_rules` config (premium thresholds)
3. **Review screen:** Policies without profiles show a suggestion badge: `Suggested: Standard Renewal [Accept]`
4. **Individual accept:** Click Accept → sets `milestone_profile`, calls `generate_policy_timelines(conn, policy_uid)`, returns updated row
5. **Bulk accept:** "Accept All Suggestions" button on review screen → assigns suggested profile to all policies without one, regenerates all timelines

### Bulk Accept Endpoint

`POST /review/accept-all-profiles`

Logic:
1. Query all active, non-opportunity, non-archived policies where `milestone_profile` is NULL or empty string
2. For each, compute suggested profile from `milestone_profile_rules`
3. UPDATE each policy's `milestone_profile`
4. Call `generate_policy_timelines(conn)` once (handles all)
5. Return updated review page content

### Profile Suggestion Function

New function in `timeline_engine.py`:

```python
def suggest_profile(conn, policy_uid=None):
    """Return {policy_uid: suggested_profile_name} for policies without a profile."""
```

Uses `milestone_profile_rules` from config:
- Premium >= $100k → "Full Renewal"
- Premium >= $25k → "Standard Renewal"
- Otherwise → "Simple Renewal"

### Regeneration Triggers (New)

Currently timeline only regenerates on startup and manual profile change. Add triggers for:

| Event | Action |
|-------|--------|
| Policy dates changed (effective/expiration) | Regenerate that policy's timeline |
| Policy milestone_profile changed | Regenerate that policy's timeline (existing) |
| Policy archived or deleted | Remove timeline rows |
| Policy converted from opportunity | Generate timeline if profile set |

These are lightweight — each trigger calls `generate_policy_timelines(conn, policy_uid=uid)` for the single affected policy.

---

## Part 3: Milestone → Follow-up Integration

### Mandated Activity Flow

Mandated activities define the renewal workflow schedule:

```
mandated_activities config → timing (days_before_expiry, prep_days)
milestone_profiles config → which milestones per profile
timeline_engine.py → generates policy_timeline rows with ideal/projected dates
```

**Three surfaces for milestone visibility:**

1. **Prep Coming Up** (proactive) — milestone appears when `prep_alert_date <= today` AND `projected_date > today`. "Start preparing for this."

2. **Today/Overdue/Stale** (reactive) — milestone injected into urgency tiers when `projected_date <= today` AND `completed_date IS NULL`. "This milestone is due/overdue."

3. **Policy Timeline** (detail view) — full vertical timeline on policy page showing all milestones with health indicators.

### Milestone Items in Urgency Tiers

When a milestone's `projected_date` arrives without completion, it appears in the follow-up tiers as a **virtual item** (not an `activity_log` row):

**Query:** In `_followups_ctx()`, after building follow-up lists, also query:

```sql
SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
       pt.ideal_date, pt.health, pt.accountability,
       p.policy_type, c.name AS client_name, c.id AS client_id
FROM policy_timeline pt
JOIN policies p ON p.policy_uid = pt.policy_uid
JOIN clients c ON c.id = p.client_id
WHERE pt.projected_date <= ?  -- today
  AND pt.completed_date IS NULL
ORDER BY pt.projected_date
```

**Bucketing:** Same date-tier logic as regular follow-ups:
- `projected_date == today` → **Today**
- 1-14 days past `projected_date` → **Overdue**
- 14+ days past → **Stale**

**Visual distinction:** Milestone items in tiers get a `◆` icon and milestone name label (e.g., "◆ Submission Sent") to distinguish from regular follow-ups.

**Actions on milestone items:**
- **Complete** — marks milestone done via `complete_timeline_milestone()`, syncs to checklist
- **Follow Up** — opens disposition form. Disposition triggers `update_timeline_from_followup()`, which shifts `projected_date` and recalculates downstream milestones
- **Snooze** — shifts `projected_date` forward by N days, triggers downstream recalculation

### Prep Coming Up Adjustment

Since milestones auto-surface in urgency tiers when `projected_date` arrives, the Prep Coming Up section should only show milestones where:
- `prep_alert_date <= today` (prep time has started)
- `projected_date > today` (not yet due — if due, it's in the urgency tiers instead)
- `completed_date IS NULL`

This prevents duplicate display: a milestone is either in Prep (future, prepare now) or in a tier (due/overdue, act now), never both.

---

## Part 4: Action Center Integration

### `_followups_ctx()` Refactor

Current function returns: `act_now`, `nudge_due`, `prep_coming`, `watching`, `scheduled`

New function returns: `triage`, `today`, `overdue`, `stale`, `nudge_due`, `prep_coming`, `watching`, `scheduled`

**Bucketing logic (in order):**

```
for each follow-up item:
  1. If activity source AND no disposition → TRIAGE
  2. Map disposition → accountability
  3. If accountability == 'scheduled' → SCHEDULED
  4. If accountability == 'waiting_external':
     - If follow_up_date <= today → NUDGE_DUE
     - Else → WATCHING
  5. If accountability == 'my_action':
     - If follow_up_date == today → TODAY
     - If 1 <= days_overdue <= stale_threshold → OVERDUE
     - If days_overdue > stale_threshold → STALE
     - If future → WATCHING (with "my turn" badge)

then, inject milestone items:
  6. Query policy_timeline for projected_date <= today, not completed
     - projected_date == today → TODAY
     - 1 <= days_past <= stale_threshold → OVERDUE
     - days_past > stale_threshold → STALE
  7. Query policy_timeline for prep_alert_date <= today AND projected_date > today
     → PREP_COMING
```

### Template Changes

**File:** `src/policydb/web/templates/action_center/_followup_sections.html`

- Replace the single `act_now` section with four sections (triage, today, overdue, stale)
- Each uses the existing `fu_row` macro with section-specific color variables
- Add milestone item variant to `fu_row` macro (◆ icon, milestone name, Complete button)
- Update filter pill bar: replace "Act Now" pill with Triage/Today/Overdue/Stale pills

**File:** `src/policydb/web/templates/action_center/_followups.html`

- Add summary bar partial at top
- Update section rendering order

### Config Changes

`stale_threshold_days` already exists in `config.py` at default 14. Add to `EDITABLE_LISTS` in `settings.py` so users can adjust via Settings UI.

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/policydb/web/routes/action_center.py` | Refactor `_followups_ctx()` — split act_now into triage/today/overdue/stale; inject milestone items; replace thread_id nudge counting with policy+disposition counting |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Replace act_now with 4 tiered sections + triage; add milestone item variant; update filter pills |
| `src/policydb/web/templates/action_center/_followups.html` | Add summary bar; update section rendering |
| `src/policydb/timeline_engine.py` | Add `suggest_profile()` function |
| `src/policydb/web/routes/review.py` | Add suggestion badge, bulk accept endpoint |
| `src/policydb/web/templates/review/_policy_row.html` | Show suggestion badge + Accept button |
| `src/policydb/web/routes/policies.py` | Add regen triggers on date/archive changes |
| `src/policydb/web/routes/settings.py` | Add `stale_threshold_days` to editable config UI |
| `src/policydb/queries.py` | Ensure `disposition` column returned in all follow-up query branches |

## Existing Code to Reuse

| Function/Module | Location | Purpose |
|-----------------|----------|---------|
| `_followups_ctx()` | `action_center.py:51` | Refactor in place — don't rewrite from scratch |
| `generate_policy_timelines()` | `timeline_engine.py` | Existing, works — just needs data |
| `milestone_profile_rules` | `config.py:544` | Existing config, never called — wire it |
| `fu_row` macro | `_followup_sections.html` | Existing row renderer — parameterize colors, add milestone variant |
| `get_all_followups()` | `queries.py:530` | Existing query — ensure disposition in output |
| `complete_timeline_milestone()` | `timeline_engine.py` | Existing — use for milestone Complete action |
| `update_timeline_from_followup()` | `timeline_engine.py` | Existing — fires on disposition set for milestone items |
| Disposition → accountability mapping | `config.py:108` | Existing, used in `_followups_ctx` |
| `stale_threshold_days` | `config.py:20` | Existing config key, default 14 |

---

## Verification

### Follow-ups Tiers
1. Start server, navigate to `/action-center`
2. Create test follow-ups with varying dates (today, 3 days ago, 15 days ago)
3. Create activity follow-ups with and without dispositions
4. Verify: no-disposition items appear in Triage, not in date tiers
5. Verify: items sort into Today/Overdue/Stale based on date and `stale_threshold_days` (14d)
6. Verify: setting a disposition on a Triage item moves it to the correct section
7. Verify: future `my_action` items appear in Watching with "my turn" badge
8. Verify: summary bar counts match section contents
9. Verify: Watching and Scheduled remain collapsed by default

### Nudge Escalation
1. Create multiple `waiting_external` follow-ups for the same policy
2. Verify: nudge count reflects policy+disposition counting, not thread_id
3. Verify: escalation badges show at 2+ and 3+ nudges

### Timeline Activation
1. Navigate to review screen
2. Verify: policies without profiles show "Suggested: X Renewal" badge
3. Click Accept on one → verify timeline rows generated in `policy_timeline`
4. Click "Accept All Suggestions" → verify all policies get profiles + timelines
5. Navigate to Action Center → verify "Prep Coming Up" section shows milestones with prep_alert_date <= today
6. Change a policy's expiration date → verify timeline regenerates
7. Verify: milestone health badges display correctly (on_track, drifting, etc.)

### Milestone → Follow-up Integration
1. Set up a policy with a milestone whose projected_date is today
2. Verify: milestone item appears in Today section with ◆ icon
3. Set up a milestone 5 days past projected_date → verify appears in Overdue
4. Set up a milestone 15 days past → verify appears in Stale
5. Click Complete on a milestone item → verify it disappears and checklist syncs
6. Click Follow Up on a milestone item → verify disposition form, timeline update
7. Verify: milestone in Prep Coming Up does NOT also appear in Today (no duplicates)

### Edge Cases
- Policy with $0 premium → should suggest "Simple Renewal"
- Opportunity policies → should NOT get timeline suggestions
- Archived policies → should NOT get timeline suggestions
- Child policies in programs → should NOT get individual timelines
- Milestone exactly on projected_date boundary → Today, not Overdue
