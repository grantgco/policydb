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
| `on_track` | Projected date ≥ 14 days away, or milestone completed |
| `drifting` | Projected date slipped from ideal by > 7 days but still achievable |
| `compressed` | Downstream milestones have < 50% of their original spacing remaining |
| `at_risk` | Projected date is past OR < 7 days away and not completed |
| `critical` | Policy expiration ≤ 30 days away with incomplete critical milestones |

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

```yaml
follow_up_dispositions:
  - name: "Left VM"
    default_days: 3
    accountability: "waiting_external"
  - name: "No Answer"
    default_days: 1
    accountability: "my_action"
  - name: "Sent Email"
    default_days: 7
    accountability: "waiting_external"
  - name: "Sent RFI"
    default_days: 7
    accountability: "waiting_external"
  - name: "Waiting on Colleague"
    default_days: 5
    accountability: "waiting_external"
  - name: "Waiting on Client"
    default_days: 7
    accountability: "waiting_external"
  - name: "Waiting on Carrier"
    default_days: 7
    accountability: "waiting_external"
  - name: "Connected"
    default_days: 0
    accountability: "my_action"
  - name: "Received Response"
    default_days: 0
    accountability: "my_action"
  - name: "Meeting Scheduled"
    default_days: 0
    accountability: "scheduled"
  - name: "Escalated"
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
    activity_type: "Internal Strategy"
    subject: "Prepare submissions — {{policy_type}}"

  - name: "Quote Received"
    trigger: "days_before_expiry"
    days: 75
    prep_days: 7
    activity_type: "Renewal Check-In"
    subject: "Follow up on quotes — {{policy_type}}"

  - name: "Coverage Comparison Prepared"
    trigger: "days_before_expiry"
    days: 60
    prep_days: 10
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
    activity_type: "Renewal Check-In"
    subject: "Get client decision — {{policy_type}}"

  - name: "Binder Requested"
    trigger: "days_before_expiry"
    days: 30
    prep_days: 3
    activity_type: "Email"
    subject: "Request binder — {{policy_type}}"

  - name: "Policy Received"
    trigger: "days_before_expiry"
    days: 14
    prep_days: 0
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

### Config Additions

- `follow_up_dispositions[].accountability` — new field per disposition
- `mandated_activities[].prep_days` — new field per milestone
- `mandated_activities[].prep_notes` — new optional field per milestone
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
