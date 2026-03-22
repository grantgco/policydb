# Proactive Timeline Engine — Design Spec

**Date:** 2026-03-22
**Status:** Draft
**Scope:** Follow-up intelligence, timeline tracking, accountability-aware workflow, prep alerts, risk notifications

---

## Problem Statement

PolicyDB's current follow-up system is reactive — it tracks what users tell it and flags items as "overdue" when dates pass. It lacks proactive intelligence:

1. **No accountability distinction.** A follow-up waiting on a carrier for 12 days looks the same as one the user forgot to act on. Everything overdue is red, creating alert fatigue and false guilt.
2. **No timeline awareness.** The system knows when milestones are due but doesn't track drift. When a renewal cycle slips because a client is slow, downstream milestones hold their original dates and pile up as "overdue" even though the delay is external.
3. **No prep lead time.** An RSM Meeting due at 120 days before expiry appears as a task on its due date. There's no advance warning to start preparing decks, pulling loss runs, or scheduling attendees.
4. **No graduated risk visibility.** Urgency is calendar-based (URGENT/WARNING/UPCOMING) not workflow-based. A policy 45 days out that's fully on track shows the same urgency badge as one 90 days out where quotes have been stuck for 3 weeks.
5. **No program-level scoping.** A property program with 50 policies generates 50 sets of identical milestones when only one renewal workflow is needed at the program level.

The user has ADD and needs the system to enforce consistency, surface the right thing at the right time, and distinguish "act now" from "keep watching" without overwhelming noise.

---

## Design Overview

**Approach:** Policy Timeline Engine (Approach B from brainstorming) — a new `policy_timeline` table that stores both the ideal and projected date for each milestone per policy/program. Dispositions feed back into accountability state, which drives how items appear and when the system nags. Prep alerts fire based on configurable lead times. Graduated health replaces binary urgency.

### Core Concepts

| Concept | Definition |
|---------|-----------|
| **Ideal date** | Original calculated milestone date from config. Immutable after creation. Drift reference. |
| **Projected date** | Current realistic target. Shifts when upstream milestones slip. |
| **Accountability state** | Who needs to act: `my_action`, `waiting_external`, or `scheduled` |
| **Health** | Workflow-aware status: `on_track`, `drifting`, `compressed`, `at_risk`, `critical` |
| **Prep alert** | Advance warning before a milestone's projected date. Configurable `prep_days` per milestone. |
| **Milestone profile** | A named subset of milestones (Full/Standard/Simple) that scales rigor to the account. |

---

## Section 1: Policy Timeline Model

### New Table: `policy_timeline`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER PK | Auto-increment |
| `policy_uid` | TEXT FK NOT NULL | Links to the program policy or standalone policy |
| `milestone_name` | TEXT NOT NULL | From `mandated_activities` config (e.g., "RSM Meeting") |
| `ideal_date` | DATE NOT NULL | Original calculated date — never changes after creation |
| `projected_date` | DATE NOT NULL | Current realistic target — shifts when upstream slips |
| `completed_date` | DATE | When actually completed (NULL until done) |
| `prep_alert_date` | DATE | `projected_date - prep_days` (recalculated on shift) |
| `accountability` | TEXT DEFAULT 'my_action' | `my_action` / `waiting_external` / `scheduled` |
| `waiting_on` | TEXT | Who/what you're waiting on (nullable — e.g., "Carrier - AmTrust") |
| `health` | TEXT DEFAULT 'on_track' | Computed: `on_track` / `drifting` / `compressed` / `at_risk` / `critical` |
| `acknowledged` | INTEGER DEFAULT 0 | Whether a risk alert has been acknowledged |
| `acknowledged_at` | DATETIME | When the acknowledgement occurred |

**Unique constraint:** `(policy_uid, milestone_name)`

### Health Computation Rules

| Health | Condition |
|--------|-----------|
| `on_track` | Projected date ≥ 7 days away AND drift from ideal ≤ 7 days, or milestone completed |
| `drifting` | Projected date slipped from ideal by > 7 days but projected date is still ≥ 7 days away |
| `compressed` | Downstream milestones have < 50% of their original spacing remaining |
| `at_risk` | Projected date is past OR < 7 days away and not completed |
| `critical` | Policy expiration ≤ 30 days away with incomplete critical milestones |

**Evaluation order:** `critical` → `at_risk` → `compressed` → `drifting` → `on_track`. First matching condition wins. This ensures no gaps — every milestone gets exactly one health status.

### Creation Trigger

Timelines are generated automatically by `generate_policy_timelines()` (replaces/extends current `generate_mandated_activities()`):

1. Runs on server startup (same as current behavior)
2. Finds all active, non-opportunity, non-archived policies
3. If policy has `program_id` set → skip (program handles it)
4. If policy has `is_program = 1` → generate timeline milestones for this program
5. If standalone policy → generate timeline milestones for this policy
6. For each applicable milestone (from the assigned profile):
   - Calculate `ideal_date` from config `days` + trigger type
   - Set `projected_date = ideal_date` initially
   - Set `prep_alert_date = projected_date - prep_days`
   - Skip if `ideal_date` is in the past
   - Skip if beyond `mandated_activity_horizon_days` (180d default)
7. Link to existing `mandated_activity_log` and `policy_milestones` for backward compatibility

---

## Section 2: Accountability & Follow-Up Intelligence

### Disposition → Accountability Mapping

Each disposition in `follow_up_dispositions` config gains an `accountability` field:

**Note:** Existing config uses `label` (not `name`) as the key for each disposition. The new `accountability` field is added alongside the existing structure.

```yaml
follow_up_dispositions:
  - label: "Left VM"
    default_days: 3
    accountability: "waiting_external"
  - label: "No Answer"
    default_days: 1
    accountability: "my_action"
  - label: "Sent Email"
    default_days: 7
    accountability: "waiting_external"
  - label: "Sent RFI"
    default_days: 7
    accountability: "waiting_external"
  - label: "Waiting on Colleague"
    default_days: 5
    accountability: "waiting_external"
  - label: "Waiting on Client"
    default_days: 7
    accountability: "waiting_external"
  - label: "Waiting on Carrier"
    default_days: 7
    accountability: "waiting_external"
  - label: "Connected"
    default_days: 0
    accountability: "my_action"
  - label: "Received Response"
    default_days: 0
    accountability: "my_action"
  - label: "Meeting Scheduled"
    default_days: 0
    accountability: "scheduled"
  - label: "Escalated"
    default_days: 3
    accountability: "my_action"
```

### Behavioral Changes

| State | Visual Treatment | Alert Behavior |
|-------|-----------------|----------------|
| `my_action` | Bold, full color — standard overdue rules apply | Hard due date, goes red when overdue |
| `waiting_external` | Muted, blue/gray tone — visible but not alarming | Soft nudge on cadence ("nudge carrier — 3 days since last check") |
| `scheduled` | Calendar icon, date shown — parked | No alerts until scheduled date arrives, then flips to `my_action` |

### Follow-Up → Timeline Integration

When a follow-up is re-diaried with a `waiting_external` disposition:

1. The corresponding `policy_timeline` milestone's `accountability` updates to `waiting_external`
2. The `waiting_on` field populates with context (e.g., "Carrier - AmTrust")
3. The milestone's `projected_date` extends to the new follow-up date
4. Downstream timeline recalculation fires (see Section 6)

When a follow-up is completed with a `my_action` disposition (e.g., "Connected", "Received Response"):

1. The corresponding milestone's `accountability` resets to `my_action`
2. No downstream shift — user is back in control of timing

---

## Section 3: Prep Alerts & Proactive Nudging

### Config Extension

Each entry in `mandated_activities` gains:

| Field | Type | Purpose |
|-------|------|---------|
| `prep_days` | INTEGER | Days before projected_date to surface a prep alert |
| `prep_notes` | TEXT (optional) | Description of what prep work is needed |
| `checklist_milestone` | TEXT (optional) | Name of `renewal_milestones` item to sync with (see Milestone Name Reconciliation) |

Example:

```yaml
mandated_activities:
  - name: "RSM Meeting"
    trigger: "days_before_expiry"
    days: 120
    prep_days: 30
    activity_type: "Meeting"
    subject: "RSM Meeting — {{policy_type}}"
    prep_notes: "Pull loss runs, build stewardship deck, confirm attendees"

  - name: "Market Submissions"
    trigger: "days_before_expiry"
    days: 90
    prep_days: 14
    checklist_milestone: "Submission Sent"
    activity_type: "Internal Strategy"
    subject: "Prepare submissions — {{policy_type}}"

  - name: "Quote Received"
    trigger: "days_before_expiry"
    days: 75
    prep_days: 7
    checklist_milestone: "Quote Received"
    activity_type: "Renewal Check-In"
    subject: "Follow up on quotes — {{policy_type}}"

  - name: "Coverage Comparison Prepared"
    trigger: "days_before_expiry"
    days: 60
    prep_days: 10
    checklist_milestone: "Coverage Comparison Prepared"
    activity_type: "Internal Strategy"
    subject: "Build comparison — {{policy_type}}"

  - name: "Client Presentation"
    trigger: "days_before_expiry"
    days: 50
    prep_days: 7
    activity_type: "Meeting"
    subject: "Renewal presentation — {{policy_type}}"

  - name: "Client Approved"
    trigger: "days_before_expiry"
    days: 40
    prep_days: 3
    checklist_milestone: "Client Approved"
    activity_type: "Renewal Check-In"
    subject: "Get client decision — {{policy_type}}"

  - name: "Binder Requested"
    trigger: "days_before_expiry"
    days: 30
    prep_days: 3
    checklist_milestone: "Binder Requested"
    activity_type: "Email"
    subject: "Request binder — {{policy_type}}"

  - name: "Policy Received"
    trigger: "days_before_expiry"
    days: 14
    prep_days: 0
    checklist_milestone: "Policy Received"
    activity_type: "Renewal Check-In"
    subject: "Confirm policy issued — {{policy_type}}"
```

### Prep Alert Behavior

- `prep_alert_date = projected_date - prep_days` — recalculated whenever projected_date shifts
- When `prep_alert_date` arrives, a follow-up appears in the **Prep Coming Up** section of Action Center
- Subject format: "Prep: {milestone_name} — {policy_type} ({client_name})"
- Shows days remaining until the milestone and prep_notes if configured
- Prep alerts shift with the timeline — if the milestone slips, the prep alert moves too

### Nudge Escalation

For items in `waiting_external` state, the system tracks consecutive nudge cycles:

| Nudge Count | Treatment |
|-------------|-----------|
| 1st nudge due | Normal appearance in Nudge Due section |
| 2nd nudge (2x cadence elapsed) | Amber emphasis — "still waiting" |
| 3rd+ nudge (3x cadence elapsed) | Stronger emphasis + suggests escalation |

Nudge count is derived from the thread's re-diary history (existing `thread_id` system). No new schema needed.

---

## Section 4: Action Center as Command Hub

### Follow-Ups Tab — Restructured Sections

The follow-ups tab reorganizes from flat overdue/upcoming into accountability-driven sections:

| Section | Source | Default State |
|---------|--------|--------------|
| **Act Now** | Follow-ups with `accountability = my_action`, due today or overdue | Expanded |
| **Nudge Due** | Follow-ups with `accountability = waiting_external`, nudge cadence elapsed | Expanded |
| **Prep Coming Up** | Milestones where `prep_alert_date` has arrived but milestone not started | Expanded |
| **Watching** | Follow-ups with `accountability = waiting_external`, not yet due for nudge | Collapsed by default |
| **Scheduled** | Follow-ups with `accountability = scheduled`, parked until future date | Collapsed by default |

### Sidebar — Portfolio Health Dashboard

The Action Center sidebar gains a portfolio health widget:

```
Portfolio Health
  ● 12 on track
  ● 4 drifting
  ● 2 compressed
  ● 1 at risk
  ● 0 critical

Due This Week    6
Nudges Due       3
Prep Starting    2
Hours This Month 14
```

Each health group is clickable — filters the pipeline to those policies.

### Sidebar Badge Change

The old "14 overdue" badge changes to "4 actions · 3 nudges" — only counts items genuinely requiring user action or a quick poke. Waiting/scheduled items excluded from the count.

### Risk Alerts Banner

When any policy's health transitions to `at_risk` or `critical`, a Risk Alerts card appears at the **top** of the Action Center, above all follow-up sections:

- Shows policy name, expiration date, days remaining, blocking reason, drift amount
- Three action buttons: **Draft Notification**, **Acknowledge**, **View Timeline**
- Acknowledged alerts show timestamp but remain visible until health improves

---

## Section 5: Formal Risk Notifications & Nudge Templates

### Risk Notification Drafts

When a policy reaches `at_risk` or `critical`, the "Draft Notification" button generates a pre-filled email using the template system.

**New template context: `timeline`**

| Token | Source |
|-------|--------|
| `{{client_name}}` | Client record |
| `{{policy_type}}` | Policy/program type |
| `{{expiration_date}}` | Policy expiration |
| `{{days_to_expiry}}` | Computed |
| `{{current_status}}` | Current accountability state + waiting_on context |
| `{{drift_days}}` | `projected_date - ideal_date` for the current active milestone |
| `{{blocking_reason}}` | Derived from waiting_on + nudge count |
| `{{milestones_complete}}` | "3 of 7" format |
| `{{milestones_remaining}}` | Comma-separated list of incomplete milestones |

**Acknowledgement model:**
- "Acknowledge" marks the alert as seen with a timestamp
- Creates a paper trail: "Acknowledged Mar 22 by [user]"
- Alert persists on screen until health improves — acknowledgement is not dismissal
- No auto-sending — user always chooses the recipient

### Config

```yaml
risk_alert_thresholds:
  at_risk_notify: true
  critical_notify: true
  critical_auto_draft: true
```

### Nudge Templates

A new template category for professional follow-up emails, auto-selected based on disposition and context.

**Starter templates shipped with system:**

1. **Waiting on Client — Document/Signature** — friendly check-in on pending materials
2. **Waiting on Client — Decision/Approval** — nudge on renewal options review
3. **Waiting on Carrier — Status Check** — internal/broker-facing quote follow-up
4. **Scheduled Meeting — Confirmation** — confirm upcoming meeting details

**New tokens for nudge templates:**

| Token | Source |
|-------|--------|
| `{{contact_first_name}}` | First word of contact name |
| `{{nudge_count}}` | Thread re-diary count |
| `{{meeting_date}}` | From scheduled follow-up date |
| `{{days_to_expiry}}` | From policy timeline |
| `{{blocking_reason}}` | From timeline context |

**Auto-selection logic:** When user clicks "Nudge" on a follow-up:
1. Match disposition → template category (client vs carrier)
2. Match context → sub-template (document vs decision, inferred from milestone)
3. Adjust tone by nudge count (1st = friendly, 3rd = direct)

**Progressive firmness:** Templates get slightly more direct as nudge count increases. First nudge: "just checking in." Third nudge: "want to make sure we stay ahead of the expiration date." All templates are user-editable in `/templates`.

---

## Section 6: Timeline Recalculation Engine

### Recalculation Triggers

| Event | Action |
|-------|--------|
| Milestone completed late | Downstream projected dates shift by the overshoot amount |
| Follow-up re-diaried with `waiting_external` | Current milestone's projected_date extends to new follow-up date; downstream recalculates |
| Follow-up re-diaried with `my_action` | No shift — user is setting their own deadline |
| Milestone completed early | Downstream dates do NOT shift earlier — keep breathing room |
| Manual override | User manually sets projected_date on any milestone; downstream recalculates |

### Recalculation Algorithm

```
When milestone N's projected_date changes:

1. slip = new_projected[N] - old_projected[N]
2. For each downstream milestone M (N+1, N+2, ...):
   a. original_gap = ideal[M] - ideal[M-1]       # intended spacing
   b. new_gap = max(original_gap - slip, minimum_gap)
   c. new_projected[M] = new_projected[M-1] + new_gap
3. Recompute prep_alert_date for all shifted milestones
4. Recompute health for all shifted milestones
```

### Constraints

- **`minimum_gap`** — configurable, default 3 days. Prevents milestones from stacking on top of each other.
- **Hard expiration boundary** — projected dates never push past the policy expiration date. If recalculation would place a milestone after expiration, it pins to `expiration - 1 day` and health flips to `critical`.
- **Recalculation is immediate** — fires on the HTMX response when a follow-up is re-diaried. User sees downstream impact instantly.

### Config

```yaml
timeline_engine:
  minimum_gap_days: 3
  drift_threshold_days: 7       # drift beyond this = "drifting" health
  compression_threshold: 0.5    # < 50% of original spacing = "compressed"
```

---

## Section 7: Program vs Policy Scoping

### Hierarchy Rule

Milestones live at the highest level — program if one exists, standalone policy otherwise.

| Scenario | Timeline owner |
|----------|---------------|
| Standalone policy (no `program_id`, not `is_program`) | The policy itself |
| Policy belongs to a program (`program_id` set) | The program — policy inherits program's timeline |
| Program policy (`is_program = 1`) | The program — owns milestones for all child policies |

### Timeline Generation Rules

`generate_policy_timelines()` logic:

1. If `program_id` is set → **skip** (parent program handles it)
2. If `is_program = 1` → generate timeline milestones for this program
3. If standalone → generate timeline milestones for this policy

### UI Behavior

**Program policy page:** Full timeline view with all milestones, health, drift visualization.

**Child policy page:** Compact banner referencing the parent program:
- Shows program name, current health, and next active milestone
- Links to the program timeline view
- "Timeline managed by: {Program Name}"

**Action Center:** Follow-ups and prep alerts reference the program, not individual child policies. One "Prep: RSM Meeting — Acme Property Program" alert, not 50 duplicates.

### Expiration Date Anchor

The program policy's expiration date is the timeline anchor. Individual child policy expirations only matter for compliance alerts (lapse risk).

---

## Section 8: Milestone Profiles

### Profile Definitions

Configurable sets of milestones that scale rigor to the account:

```yaml
milestone_profiles:
  - name: "Full Renewal"
    description: "Large/complex accounts with full service cycle"
    milestones:
      - "RSM Meeting"
      - "Market Submissions"
      - "Quote Received"
      - "Coverage Comparison Prepared"
      - "Client Presentation"
      - "Client Approved"
      - "Binder Requested"
      - "Policy Received"

  - name: "Standard Renewal"
    description: "Mid-size accounts, standard workflow"
    milestones:
      - "Market Submissions"
      - "Quote Received"
      - "Client Approved"
      - "Binder Requested"
      - "Policy Received"

  - name: "Simple Renewal"
    description: "Small accounts, minimal touchpoints"
    milestones:
      - "Quote Received"
      - "Client Approved"
      - "Binder Requested"
```

### Profile Assignment

| Method | Behavior |
|--------|----------|
| **Manual** (primary) | User picks profile on policy or program edit page via dropdown |
| **Auto-suggest** | System suggests based on premium threshold; user confirms |

Auto-suggest rules in config:

```yaml
milestone_profile_rules:
  - profile: "Full Renewal"
    conditions:
      min_premium: 100000
  - profile: "Standard Renewal"
    conditions:
      min_premium: 25000
  - profile: "Simple Renewal"
    conditions:
      default: true
```

**User always has final say.** Auto-suggest populates the field; user can override.

### Profile Inheritance

Programs inherit their profile to child policies. All child policies use the program's profile since milestones live at the program level.

### Timing Source

Each milestone inherits its `days`, `prep_days`, and other timing from `mandated_activities` config. Profiles only control *which* milestones apply. Changing "Quote Received" from 75 to 80 days applies everywhere that milestone is used across all profiles.

---

## Section 9: Review Panel Integration

The existing `/review` page cycles through policies, opportunities, and clients on a configurable cadence (weekly, biweekly, monthly, quarterly, etc.). It currently operates independently from the timeline engine. This design connects them.

### Remove Auto-Review

**Delete the auto-review system entirely.** The current `check_auto_review_policy()` and `check_auto_review_client()` functions in `queries.py` auto-mark items as "reviewed" when activity thresholds are met (2 field changes or 3 activities). This is counter to the purpose of the review — the review is a deliberate weekly sit-down where you force yourself to look at everything, not something that gets silently checked off because you happened to log 3 activities.

**What gets removed:**

*Core functions in `queries.py`:*
- `check_auto_review_policy()` (lines 1311-1351)
- `check_auto_review_client()` (lines 1354-1389)
- `count_changed_fields()` (line 1285) — only used by auto-review callers

*All call sites across four route modules:*

| File | Call sites to remove |
|------|---------------------|
| `review.py` | Lines 289, 381 (after edit save + after activity log) |
| `policies.py` | Lines 281, 362, 453, 592, 692, 1829, 2391 (after various field saves) |
| `activities.py` | Lines 114, 115, 430, 431, 1088 (after activity create/complete) |
| `clients.py` | Line 2302 (after client field save) |

*Config keys:*
- `auto_review_enabled`, `auto_review_field_threshold`, `auto_review_activity_threshold`

The auto-review is replaced by the deliberate weekly review workflow described below.

### Program-Level Review Scoping

Same hierarchy rule as timelines:

| Scenario | Review target |
|----------|--------------|
| Standalone policy | Review the policy directly |
| Policy in a program | Review the **program** — child policies roll up |
| Program (`is_program = 1`) | Review the program (covers all child policies) |

**Changes to `v_review_queue` view:**
- Exclude policies where `program_id IS NOT NULL` (child policies)
- Include program policies (`is_program = 1`) with aggregate stats from child policies
- When a program is marked "reviewed," all child policies' `last_reviewed_at` updates too

### Milestone Profile Selection in Review Row

The review row is the natural place to assign the milestone profile — you're already looking at the policy, assessing its complexity, deciding how to handle the renewal. Add a **profile dropdown** to the review row:

```
+-----------------------------------------------------------------+
|  Acme Corp -- General Liability      Exp: Jun 15 (85 days)      |
|  AmTrust  .  GL-2026-001  .  $45,000 premium                   |
|                                                                 |
|  Renewal: [Full Renewal v]    Status: [Not Started v]           |
|  Health: * on_track           Review: Weekly                    |
|                                                                 |
|  [Reviewed]  [Edit]  [Log]  [Open ->]                           |
+-----------------------------------------------------------------+
```

- The `[Full Renewal v]` dropdown sets `milestone_profile` on the policy/program
- When changed, the timeline engine regenerates milestones for the new profile
- Auto-suggest pre-fills based on premium, but the dropdown is always visible for override
- This is the **primary workflow** for assigning profiles — not buried in the policy edit page

### Review -> Timeline Connection

When reviewing a policy/program, the review row shows the current timeline health badge and the next active milestone. This gives context during the review: "this one is drifting, I should check on it" vs "this one is on track, just mark reviewed and move on."

After marking reviewed, if there's no active follow-up set, the system prompts: "Set a follow-up?" with a quick date picker. This ensures the review doesn't just mark a checkbox — it produces an action.

---

## Section 10: Plan Week Integration

The existing Plan Week view (`/followups/plan`) shows a 5-day grid of follow-ups with drag-to-rebalance. The timeline engine enriches this with context.

### Timeline Context in Plan Week Items

Each follow-up item in the Plan Week grid gains:

| Addition | Source | Purpose |
|----------|--------|---------|
| Health badge | `policy_timeline.health` | See at a glance which items are for drifting/at-risk policies |
| Accountability icon | Follow-up disposition | Distinguish "my action" items from "nudge" items |
| Milestone label | `policy_timeline.milestone_name` | Know which workflow step this follow-up relates to |
| Prep flag | `policy_timeline.prep_alert_date` | Items from prep alerts are visually distinct |

This means the Plan Week grid isn't just "things to do Monday through Friday" — it's "here's the strategic importance of each item." A nudge on a drifting policy is different from a prep task for an on-track one.

### Pinning Rules Enhanced

Current pinning: items within 14 days of renewal are pinned (can't be moved).

**Enhanced pinning:** Also pin items for policies with `critical` or `at_risk` health. These are the most important items and shouldn't be spread to lighter days — they need to happen when scheduled.

### Plan Week <-> Review Connection

The Plan Week view gets a link to the Review Board for items that are also due for review. Small badge on the follow-up card: "Due for review" — clicking it opens the review panel filtered to that policy.

Conversely, when doing the weekly review, a "Plan this week" button appears if there are unscheduled follow-ups. This creates a natural workflow: **Review -> Plan -> Execute**.

---

## Milestone Name Reconciliation

The existing codebase has two independent milestone systems that this design unifies:

**`renewal_milestones`** (checklist items — simple strings):
- Submission Sent, Loss Runs Received, Quote Received, Coverage Comparison Prepared, Client Approved, Binder Requested, Policy Received

**`mandated_activities`** (timed triggers — currently only 2):
- RSM Meeting (120d before expiry), Post-Binding Meeting (45d after effective)

This design **expands `mandated_activities`** to include timeline-aware versions of the renewal milestones. The naming must be consistent between the two systems. Implementation should:

1. Keep `renewal_milestones` as the canonical name list (user-editable in Settings)
2. `mandated_activities` references those names and adds timing/trigger metadata
3. When a `mandated_activities` entry shares a name with a `renewal_milestones` item, completing the timeline milestone also marks the `policy_milestones` checklist item done (and vice versa)
4. New milestones like "RSM Meeting" and "Client Presentation" exist only in `mandated_activities` (not in the basic checklist) since they are meeting-type activities, not deliverable checkpoints

**Name alignment table:**

**Approach:** `mandated_activities` and `renewal_milestones` use **independent names** because they serve different purposes. Mandated activities describe timed workflow steps ("Market Submissions" = the act of sending); renewal milestones describe deliverable checkpoints ("Submission Sent" = the result). The timeline engine maps between them at sync time using a `checklist_milestone` field on each mandated activity.

| `mandated_activities` name | `checklist_milestone` (syncs to `renewal_milestones`) | Notes |
|---------------------------|------------------------------------------------------|-------|
| RSM Meeting | — | Meeting, not a checklist item |
| Market Submissions | Submission Sent | Completing this timeline milestone also checks off "Submission Sent" |
| Quote Received | Quote Received | Exact match — same name in both systems |
| Coverage Comparison Prepared | Coverage Comparison Prepared | Exact match |
| Client Presentation | — | Meeting, not a checklist item |
| Client Approved | Client Approved | Exact match |
| Binder Requested | Binder Requested | Exact match |
| Policy Received | Policy Received | Exact match |
| Post-Binding Meeting | — | Post-effective trigger, not in renewal checklist |

When a mandated activity has a `checklist_milestone` value, completing the timeline milestone auto-marks the corresponding `policy_milestones` checklist item done (and vice versa). Activities without a `checklist_milestone` (meetings) are timeline-only.

---

## Settings UI for Nested Config

The current `EDITABLE_LISTS` in `settings.py` only handles flat string lists. Several new config keys in this design are nested structures (list of dicts):

- `follow_up_dispositions` (already nested — `label` + `default_days`, now + `accountability`)
- `mandated_activities` (already nested — `name` + `trigger` + `days`, now + `prep_days` + `prep_notes`)
- `milestone_profiles` (new — `name` + `description` + `milestones[]`)
- `timeline_engine` (new — flat dict of thresholds)
- `risk_alert_thresholds` (new — flat dict of booleans)

**Implementation approach:** Create custom editor sections in the Settings page for these structured configs, similar to how `follow_up_dispositions` is already managed (it has its own editor outside of `EDITABLE_LISTS`). Each gets a dedicated card/section with appropriate controls:

- **Dispositions:** Existing editor + new accountability dropdown per row
- **Mandated Activities:** Table editor with columns for name, trigger, days, prep_days, prep_notes
- **Milestone Profiles:** Card per profile with drag-to-reorder milestone list
- **Timeline Engine / Risk Alerts:** Simple key-value editors for threshold numbers and boolean toggles

---

## Schema Changes Summary

### New Table: `policy_timeline`

```sql
CREATE TABLE IF NOT EXISTS policy_timeline (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    milestone_name  TEXT NOT NULL,
    ideal_date      DATE NOT NULL,
    projected_date  DATE NOT NULL,
    completed_date  DATE,
    prep_alert_date DATE,
    accountability  TEXT NOT NULL DEFAULT 'my_action',
    waiting_on      TEXT,
    health          TEXT NOT NULL DEFAULT 'on_track',
    acknowledged    INTEGER NOT NULL DEFAULT 0,
    acknowledged_at DATETIME,
    created_at      DATETIME DEFAULT (datetime('now')),
    UNIQUE(policy_uid, milestone_name)
);
```

### Altered Table: `policies`

```sql
ALTER TABLE policies ADD COLUMN milestone_profile TEXT DEFAULT '';
```

**Migration numbering:** Next available migration is **069** (068 exists on disk as `068_migrate_saved_notes_to_activities.sql`). The `policy_timeline` table and `milestone_profile` column should be migration 069. **Pre-requisite fix:** Migration 068 is currently NOT in `_KNOWN_MIGRATIONS` set in `db.py` (line 298) — it must be added before wiring 069, otherwise the backup trigger logic won't cover it.

### Config Additions

- `follow_up_dispositions[].accountability` — new field per disposition
- `mandated_activities[].prep_days` — new field per milestone
- `mandated_activities[].prep_notes` — new optional field per milestone
- `mandated_activities[].checklist_milestone` — new optional field to sync with `renewal_milestones`
- `milestone_profiles` — new config key (list of profile definitions)
- `milestone_profile_rules` — new config key (auto-suggest rules)
- `timeline_engine` — new config key (minimum_gap_days, drift_threshold_days, compression_threshold)
- `risk_alert_thresholds` — new config key (at_risk_notify, critical_notify, critical_auto_draft)
- `nudge_templates` — new email template category

---

## Migration Path

### Backward Compatibility

- Existing `policy_milestones` table remains intact — `policy_timeline` is additive
- Existing `mandated_activity_log` continues to track rule execution history
- Current `generate_mandated_activities()` is extended (not replaced) to also populate `policy_timeline`
- Policies without a `milestone_profile` default to "Simple Renewal" behavior
- Action Center follow-ups tab gains new sections but existing queries still work as data sources

### Data Migration

- On first run after migration: `generate_policy_timelines()` creates timeline rows for all active policies based on current milestone state
- Existing completed milestones populate `completed_date` from `policy_milestones.completed_at`
- Existing mandated activities with future follow-up dates populate `projected_date`

---

## What This Design Does NOT Include

- **Background scheduled tasks / cron** — all logic remains query-driven at render time and event-driven on user actions. No background daemon.
- **Auto-send emails** — system drafts notifications, user always sends manually.
- **Workflow state machine** — no formal states with enforced transitions. The timeline is advisory, not gating.
- **Policy-level milestone overrides within a program** — if a policy is in a program, it uses the program's timeline. No per-policy exceptions within a program.

---

## Implementation Impact Analysis

### New Files

| File | Purpose |
|------|---------|
| `src/policydb/timeline_engine.py` | Core engine: timeline generation, recalculation, health computation. Keeps `queries.py` from growing further. |
| `src/policydb/migrations/069_policy_timeline.sql` | New `policy_timeline` table + `milestone_profile` column on policies |
| `src/policydb/web/templates/action_center/_risk_alerts.html` | Risk alert banner partial |
| `src/policydb/web/templates/action_center/_portfolio_health.html` | Sidebar portfolio health widget |
| `src/policydb/web/templates/action_center/_followup_sections.html` | Restructured follow-up sections (Act Now, Nudge Due, Prep, Watching, Scheduled) |
| `src/policydb/web/templates/policies/_timeline.html` | Policy/program timeline visualization |
| `src/policydb/web/templates/policies/_timeline_banner.html` | Compact banner for child policies in a program |
| `src/policydb/web/templates/settings/_mandated_activities_editor.html` | Custom editor for mandated activities config |
| `src/policydb/web/templates/settings/_milestone_profiles_editor.html` | Custom editor for milestone profiles config |

### Modified Files

| File | Changes |
|------|---------|
| **`src/policydb/config.py`** | Add `prep_days`/`prep_notes` to `mandated_activities` defaults; add `accountability` to `follow_up_dispositions`; add new keys: `milestone_profiles`, `milestone_profile_rules`, `timeline_engine`, `risk_alert_thresholds` |
| **`src/policydb/db.py`** | Wire migration 069 into `_KNOWN_MIGRATIONS`; call `generate_policy_timelines()` on startup alongside existing `generate_mandated_activities()` |
| **`src/policydb/queries.py`** | Modify `get_all_followups()` to include accountability state; modify `get_suggested_followups()` to use timeline health; modify `get_escalation_alerts()` to use timeline health; modify `get_stale_renewals()` to use timeline health. **Note:** `supersede_followups()` uses `policy_id` (int) but `policy_timeline` is keyed by `policy_uid` (text) — timeline recalc triggers should be placed at call sites where both IDs are available, or add a `policy_id` → `policy_uid` lookup inside the recalc function. |
| **`src/policydb/web/routes/activities.py`** | `activity_complete()` — update timeline accountability on completion; `activity_followup()` (re-diary) — trigger timeline recalculation; add timeline context to re-diary response |
| **`src/policydb/web/routes/action_center.py`** | Rewrite `_followups_ctx()` to produce 5 sections instead of overdue/upcoming; add `_portfolio_health_ctx()` for sidebar widget; add `_risk_alerts_ctx()` for banner; update `_sidebar_ctx()` with new badge counts |
| **`src/policydb/web/routes/policies.py`** | Add `milestone_profile` field handling to policy edit; add timeline view endpoint; modify milestone toggle to sync with `policy_timeline`; add program timeline banner endpoint |
| **`src/policydb/web/routes/dashboard.py`** | Replace urgency-based widgets with health-based widgets; update stale/suggested to use timeline data |
| **`src/policydb/web/routes/review.py`** | Remove auto-review call sites; add milestone profile dropdown to review row; add timeline health badge to review row; add program-level review scoping; add follow-up prompt after marking reviewed |
| **`src/policydb/web/routes/settings.py`** | Add custom editor routes for mandated activities, milestone profiles, timeline thresholds, risk alert config; remove auto-review config keys |
| **`src/policydb/views.py`** | Update `v_renewal_pipeline` to include `health` from timeline; update `v_overdue_followups` to include accountability state; update `v_review_queue` to exclude child policies in programs and include program policies |
| **`src/policydb/email_templates.py`** | Add `timeline_context()` function; add timeline tokens to `CONTEXT_TOKEN_GROUPS`; seed nudge templates in `email_templates` table |
| **`src/policydb/web/templates/action_center/page.html`** | Add risk alerts banner slot; restructure follow-ups tab layout |
| **`src/policydb/web/templates/action_center/_followups.html`** | Major rewrite for 5-section layout |
| **`src/policydb/web/templates/action_center/_sidebar.html`** | Add portfolio health widget; update badge format |
| **`src/policydb/web/templates/policies/_tab_details.html`** | Add milestone profile dropdown; add timeline view/banner |
| **`src/policydb/web/templates/review/index.html`** | Add program-level rows; exclude child policies |
| **`src/policydb/web/templates/review/_policy_row.html`** | Add milestone profile dropdown; add timeline health badge; add follow-up prompt after review; remove auto-review triggers |
| **`src/policydb/web/templates/followups/plan.html`** | Add health badges, accountability icons, milestone labels, prep flags to grid items; enhance pinning for at_risk/critical; add "Due for review" badge |
| **`src/policydb/web/templates/base.html`** | Update nav badge to "actions · nudges" format |

### Recommended Implementation Phases

| Phase | Scope | Depends On |
|-------|-------|-----------|
| **Phase 1: Foundation** | Migration 069, `timeline_engine.py` (generate + recalculate + health compute), config additions, `generate_policy_timelines()` wired into startup | Nothing |
| **Phase 2: Accountability** | `accountability` field on dispositions, modify re-diary to trigger recalc, modify complete to update timeline, sync milestone toggle with timeline | Phase 1 |
| **Phase 3: Review Panel Overhaul** | Remove auto-review, add program-level review scoping to `v_review_queue`, add milestone profile dropdown to review rows, add timeline health badge, add follow-up prompt after marking reviewed | Phase 1 |
| **Phase 4: Action Center Overhaul** | 5-section follow-ups, portfolio health sidebar, risk alerts banner, badge update | Phase 1 + 2 |
| **Phase 5: Plan Week Enrichment** | Timeline context (health badges, accountability icons, milestone labels, prep flags) in Plan Week items; enhanced pinning for at_risk/critical; review badge cross-link; Review -> Plan -> Execute workflow | Phase 1 + 2 + 4 |
| **Phase 6: Templates & Polish** | Nudge templates, risk notification drafts, timeline tokens in email system, timeline visualization on policy/program pages, Settings UI editors | Phase 1-5 |
