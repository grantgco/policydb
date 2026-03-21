# Follow-Up Workload Balancer — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Plan Week view for follow-up workload visualization and redistribution, with auto-spread, drag-to-rebalance, and urgency-based pinning.

---

## Problem Statement

Follow-ups cluster on certain days (especially Mondays) due to disposition auto-scheduling and manual date picking. With 15 items on one day and 2 on another, items slip or get rushed. There's no way to visualize the week's workload or redistribute items across days.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Daily target | Configurable, default 5 | User's comfortable capacity is 3-5/day |
| View type | Dedicated Plan Week view (5 columns, Mon-Fri) | Full visibility of the week's load |
| Access point | Tab/link from follow-ups page | Natural workflow — see follow-ups, plan the week |
| Spread algorithm | Fill lightest days first across Mon-Fri | Pure load balancing |
| Drag behavior | Any direction (forward or backward) | User has full control after auto-spread |
| Pinning | Policy expiring within configurable window OR EXPIRED/URGENT status | Urgent items must not be deferred |
| Confirmation | Preview changes before applying | No surprise date changes |

---

## 1. Plan Week View

### Route

`GET /followups/plan` — shows the Plan Week view for the current or specified week. Uses `active = "followups"` for nav highlighting.

**Template:** `src/policydb/web/templates/followups/plan.html`

Query params: `week_start=YYYY-MM-DD` (Monday of the target week). Defaults to current week's Monday.

### Layout

5 columns (Mon-Fri), each containing:

```
┌─────────────────┐
│ MON Mar 24       │
│ 15 items  ██████ │  ← red (> 2× target)
│                  │
│ 🔒 Acme GL renew │  ← pinned (lock icon, no drag)
│ 🔒 Widget exp 3d │
│ ≡ Call John re WC│  ← draggable (grip handle)
│ ≡ Follow up EPLI │
│ ≡ Send RFI ...   │
│ ...              │
└─────────────────┘
```

**Color coding on item count:**
- Green: count ≤ `daily_target`
- Amber: count > `daily_target`
- Red: count > `daily_target × 2`

**Each item card shows:**
- Subject (truncated)
- Client name
- Policy type (if applicable)
- Lock icon if pinned, drag grip (≡) if movable
- Pinned items have a subtle background tint and no drag handle

### Week Navigation

Prev/Next week arrows at the top. "This Week" button to jump back to current week.

```
← Prev    Mar 24 – Mar 28, 2026    Next →    [This Week]    [Spread]
```

---

## 2. Config

### New config key: `daily_followup_target`

Default: `5`

Add to `_DEFAULTS` in `config.py`. Not a list — a simple integer. Managed in Settings as a number input (not a list card).

### Pin threshold: `pin_renewal_days`

Default: `14`

Items linked to policies expiring within this many days are pinned. Also add to `_DEFAULTS`.

### Pin statuses

Items linked to policies with renewal_status in the existing `renewal_statuses_excluded` list are NOT pinned (those are silenced). Items with renewal_status `EXPIRED` or `URGENT` (from the urgency calculation) ARE pinned. No new config needed — use the existing urgency logic from `v_policy_status`.

---

## 3. Spread Algorithm

`spread_followups(conn, week_start, daily_target, pin_threshold_days)` — a query function in `queries.py`.

### Steps:

1. **Load all follow-ups for the week** (Mon-Fri, plus Sat/Sun bucketed into Monday) — use `get_all_followups()` which returns activity, policy, and client follow-up sources via UNION ALL. Filter to `follow_up_date BETWEEN week_start - 2 days (Sat) AND week_start + 4 days (Fri)`. Bucket Saturday/Sunday items into Monday.

2. **Classify each item as pinned or movable:**
   - Pinned if policy has `days_to_renewal ≤ pin_threshold_days` (computed from expiration_date)
   - Pinned if policy urgency is `EXPIRED` or `URGENT`
   - Items with no policy are always movable

3. **Count per day** — build `{date: [items]}` map

4. **Identify overloaded days** — any day with total items > `daily_target`

5. **Redistribute movable items from overloaded days:**
   - Sort movable items from overloaded days (no particular order — they're all roughly equal priority)
   - For each movable item, assign to the day with the fewest total items
   - Continue until all movable items from overloaded days are redistributed as evenly as possible (days with only pinned items may remain above target — that's expected)

6. **Return proposed changes:** list of `(activity_id, old_date, new_date)` tuples

### Does not:
- Move items to weekends
- Move items to days outside the target week
- Touch pinned items
- Auto-apply — returns proposals for preview

---

## 4. Drag to Rebalance

Each movable item card has a drag handle (≡). Dragging between columns updates the follow-up date.

### Implementation

Use HTML5 Drag and Drop (or SortableJS if already available):

- Each day column is a drop zone
- Dragging a card to a different column fires a PATCH request:
  ```
  PATCH /followups/{activity_id}/reschedule
  Body: {"follow_up_date": "2026-03-25"}
  ```
- The PATCH endpoint updates `activity_log.follow_up_date` and returns success
- On success, the card moves to the new column and counts update
- Pinned items have `draggable="false"` and no drag handle

### Endpoint

Use the existing `POST /activities/{activity_id}/reschedule` endpoint pattern with Form data for consistency. For drag-and-drop, call it via `fetch()` with form-encoded body. The existing endpoint already handles activity-source follow-ups. For policy-source and client-source follow-ups, use the `source` prefix pattern from the existing `bulk-reschedule` endpoint (`activity-{id}`, `policy-{uid}`, `client-{id}`).

---

## 5. Spread Button Flow

1. User clicks "Spread" button
2. Server computes proposed redistribution via `spread_followups()`
3. Response shows the Plan Week with proposed moves highlighted:
   - Moved items have an amber left border and show `"← from Mon"` tag
   - Day counts update to show proposed new totals
4. Two buttons appear: "Apply Changes" and "Cancel"
5. **Apply:** batch-updates all follow_up_dates in one request
6. **Cancel:** reloads the page with current (unchanged) dates

### Apply Endpoint

```
POST /followups/plan/apply-spread
Body: JSON array of [{activity_id, new_date}, ...]
```

Updates all follow_up_dates in a single transaction.

---

## 6. Data Query

The Plan Week view needs follow-ups with policy context for pinning. Query:

```sql
SELECT a.id, a.subject, a.follow_up_date, a.client_id, a.policy_id,
       c.name AS client_name,
       p.policy_type, p.expiration_date, p.renewal_status,
       julianday(p.expiration_date) - julianday('now') AS days_to_renewal
FROM activity_log a
JOIN clients c ON a.client_id = c.id
LEFT JOIN policies p ON a.policy_id = p.id
WHERE a.follow_up_date BETWEEN ? AND ?
  AND a.follow_up_done = 0
ORDER BY a.follow_up_date, a.client_id
```

---

## 7. Edge Cases

| Scenario | Behavior |
|----------|----------|
| All items on a day are pinned | Day stays overloaded — can't spread, show a warning |
| Week has no overloaded days | Spread button disabled or shows "Week is balanced" |
| Target is 5, Mon has 12 pinned + 3 movable | Only 3 can move — Mon stays at 12 but at least the movable ones go elsewhere |
| Drag item to a day already at target | Allowed — user chose it explicitly |
| Item has no policy (client-only follow-up) | Always movable (no urgency to pin) |
| Weekend dates in follow_up_date | Not shown — Plan Week is Mon-Fri only. Weekend items appear on the following Monday. |
| Next week has items already | Spread only redistributes within the viewed week, doesn't push to next week |
| Spread then drag then spread again | Each spread recalculates from current state |
