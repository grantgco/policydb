# Focus Queue: Action Center Redesign

## Context

The current Action Center's Follow-ups tab has 8 urgency buckets (Triage, Today, Overdue, Stale, Nudge Due, Prep Coming, Watching, Scheduled) plus a separate Inbox tab with a multi-step processing workflow. This creates cognitive overload: too many buckets to scan, too many steps from capture to action, and the term "triage" is overloaded (a bucket, a workflow, and a verb).

The user needs one answer each morning: **"What needs my focus right now?"** — and a look-ahead mode for planning around time off. The redesign replaces the 8-bucket Follow-ups tab and separate Inbox processing flow with a single ranked Focus Queue and a Waiting Sidebar.

---

## Design

### Layout: Focus Queue + Waiting Sidebar

The Action Center default view becomes two panels:

**Focus Queue (main area, ~70%)** — A single ranked list of items where YOU need to act. Every source feeds into this one list:
- Renewals approaching expiration
- Milestones due or overdue
- Follow-ups you own (accountability = `my_action`)
- Unprocessed inbox items (emails, manual captures)
- Issues nearing SLA breach
- Waiting items that have auto-promoted (exceeded wait threshold)

**Waiting Sidebar (~30%)** — Compact list of items where the ball is in someone else's court (carrier, client, colleague). Shows days waiting and nudge alerts for items waiting 10+ days. Each waiting item has:
- "Nudge" button — sends follow-up, auto-logs activity
- "Pull to Focus" button — moves item to Focus Queue for direct action

### Top Bar Controls

| Control | Behavior |
|---------|----------|
| **Time Horizon** | Segmented control: "Today" / "This Week" / "Next 2 Weeks" / "Custom..." (date picker). Filters the Focus Queue to show items due within that window. **"Next 2 Weeks" is the look-ahead/vacation mode.** "Custom" opens a date picker so you can say "show me everything through April 18." |
| **Guide Me toggle** | When ON: highlights the single top-priority item with a purple border and shows a specific suggested action (e.g., "Call underwriter at Zurich re: GL renewal quote"). User works item-by-item. When OFF: standard list view. |
| **Client filter** | Dropdown to filter Focus Queue to a specific client. |

### Scoring Model (Focus Queue Ranking)

Items are ranked by an additive score. No hard gates — every signal contributes:

| Factor | Signal | Score Contribution |
|--------|--------|--------------------|
| **Deadline proximity** | Days until expiration, milestone date, SLA deadline, or follow-up date | Closer = higher. Items past due score highest. |
| **Staleness** | Days since last activity on the policy/client | No activity in 14+ days = boost |
| **Severity** | Issue severity (Critical > High > Normal > Low) | Critical/High get a bump |
| **Source weight** | Renewal expirations and active issues score slightly above routine follow-ups | Small tiebreaker |
| **Overdue multiplier** | Items past their due date get an escalating boost | Grows with days overdue |

The score is transparent — the context line on each item explains *why* it's ranked where it is (e.g., "Expires in 6 days", "No activity in 18 days", "SLA breach in 2 days").

### Inbox Integration (No Separate Processing Step)

Inbox items (from Outlook sync or manual capture) appear directly in the Focus Queue as regular items. The key change: **no separate "Process" step.**

- **Auto-matched emails** (client/policy identified by ref tag or sender): Appear in Focus Queue with client/policy pre-filled. Suggested action: "Log & Reply." One click logs the activity and opens compose.
- **Unmatched emails**: Appear in Focus Queue with a lightweight inline prompt: "Link to client: [autocomplete]". Once linked, the item gets scored like any other.
- **Manual captures**: Quick-capture input on the page (same as today's inbox capture). Item appears immediately in Focus Queue.

This eliminates the Inbox tab as a separate workflow. The capture mechanism stays, but processing happens inline in the Focus Queue.

### Guide Me Mode

When toggled ON:
1. The top-priority item gets a purple highlight border and an expanded suggestion panel
2. The suggestion panel shows: a specific next action, relevant context (last contact date, who to contact, what's pending), and a pre-filled completion form. **Suggestions are template-based** (not AI-generated) — derived from the item type, milestone name, days since last activity, and contact info already in the system.
3. **Smart completion**: Clicking the suggested action button pre-fills the activity log with the suggestion text as the note, auto-sets a sensible next follow-up date based on the disposition, and presents it as a one-click confirm
4. After completing an item, the next item auto-highlights
5. The user works through the queue item-by-item with minimal decision-making

When toggled OFF:
- Standard list view, all items visible, manual completion with an optional log form

### Completion Flow (Smart Default)

When completing a Focus Queue item:

**Guide Me ON:**
1. Click the suggested action button (e.g., "Nudge Carrier")
2. System pre-fills: activity type, note (from suggestion text), next follow-up date (from disposition cadence config)
3. One-click "Done" to accept defaults, or edit any field before confirming
4. Item removed from Focus Queue, activity logged

**Guide Me OFF:**
1. Click checkmark on any item
2. Small inline form expands: one-line note (optional), next follow-up date (optional), activity type (auto-detected)
3. Click "Done" to dismiss, or skip the form entirely to just mark done

### Waiting Sidebar Behavior

- Shows items where accountability = `waiting_external` or `scheduled`
- Sorted by days waiting (longest wait at top)
- **Auto-promotion**: Items waiting beyond the configured threshold (default: `stale_threshold_days` from config, currently 14) automatically move to the Focus Queue with a "Stale — consider nudging" label
- **Nudge alerts**: Yellow banner at top when 2+ items are waiting 10+ days
- **Quick stats** at bottom: items in focus, items waiting, hours logged today

### Disposition Rename

The word "disposition" is replaced throughout the UI with natural language:

| Old Term | New Term | Where Used |
|----------|----------|------------|
| Disposition | "Ball with" or "Status" | Completion form, waiting sidebar |
| `waiting_external` | "Waiting on [carrier/client/colleague]" | Sidebar labels |
| `my_action` | "My action" (or just omitted — it's the default) | Focus Queue items |
| `scheduled` | "Scheduled" | Sidebar items with a specific future date |

The underlying `disposition` field and config structure remain unchanged in the database. This is a UI-only rename.

### Other Tabs (Secondary Views)

The remaining Action Center tabs become secondary, accessible via a "More" menu or compact tab row below the main view:

| Tab | Status | Notes |
|-----|--------|-------|
| **Follow-ups** | **Replaced** | Merged into Focus Queue + Waiting Sidebar |
| **Inbox** | **Replaced** | Merged into Focus Queue (inline processing) |
| **Activities** | Kept as secondary | Historical log, reference view |
| **Scratchpads** | Kept as secondary | Working notes, "Log as Activity" still works |
| **Issues** | Kept as secondary | Full issue management view; active issues also appear in Focus Queue |
| **Anomalies** | Kept as secondary | Data quality audit |
| **Activity Review** | Kept as secondary | AI review flags |
| **Data Health** | Kept as secondary | Completeness metrics |

The default landing is always the Focus Queue view. Secondary tabs are one click away but don't compete for attention.

---

## Key Files to Modify

| File | Change |
|------|--------|
| `src/policydb/web/routes/action_center.py` | Replace `_classify_item()` 8-bucket logic with scoring model. New endpoint for Focus Queue data. Merge inbox item rendering. |
| `src/policydb/web/templates/action_center/page.html` | New layout: Focus Queue + Waiting Sidebar as default view, secondary tabs in "More" menu |
| `src/policydb/web/templates/action_center/_followups.html` | **Replace** with `_focus_queue.html` — ranked item list with inline actions |
| `src/policydb/web/templates/action_center/_inbox.html` | **Remove** as standalone tab — inbox capture moves to Focus Queue page, processing is inline |
| `src/policydb/web/templates/action_center/_followup_sections.html` | **Remove** — no more bucket sections |
| `src/policydb/web/routes/inbox.py` | Simplify processing endpoints — no slideover needed for matched items, lightweight inline for unmatched |
| `src/policydb/config.py` | Add `focus_score_weights` config for tuning scoring factors. Rename disposition labels in defaults. |
| `src/policydb/queries.py` | New `get_focus_queue()` function that merges all sources and returns scored/ranked items |

### Existing Code to Reuse

| Function/Pattern | Location | Reuse |
|-----------------|----------|-------|
| `get_all_followups()` | `queries.py` | Data source for follow-up items (remove bucket classification) |
| `get_suggested_followups()` | `queries.py` | Data source for renewal urgency items |
| `get_insurance_deadline_suggestions()` | `queries.py` | Data source for project insurance deadlines |
| `_compute_nudge_tier()` | `action_center.py` | Reuse for Waiting Sidebar escalation badges |
| `_attach_milestone_progress()` | `routes/policies.py` | Attach milestone data to Focus Queue items |
| `_attach_client_ids()` | `routes/policies.py` | Link items to clients for filtering |
| Inbox capture form | `_inbox.html` | Move capture input to Focus Queue page header |
| Process slideover | `_process_inbox_slideover.html` | Simplify to inline form for unmatched items only |

---

## Verification

1. **Start server**, navigate to `/action-center` — should show Focus Queue + Waiting Sidebar layout by default
2. **Time horizon**: Click "Today" / "This Week" / "Next 2 Weeks" — queue should filter correctly
3. **Guide Me mode**: Toggle on — top item should highlight with purple border and specific suggestion
4. **Complete an item**: Click suggested action in Guide Me mode — should pre-fill log, one-click confirm, item disappears, next item highlights
5. **Inbox email**: Sync an email — it should appear in Focus Queue (not a separate Inbox tab)
6. **Unmatched email**: Should show inline "Link to client" prompt in the Focus Queue
7. **Waiting sidebar**: Items with `waiting_external` disposition should appear in sidebar, sorted by days waiting
8. **Auto-promotion**: Create a waiting item 15+ days old — should appear in Focus Queue with stale label
9. **Nudge**: Click "Nudge" on a waiting item — should log activity and update follow-up date
10. **Secondary tabs**: Click "More" — should show Activities, Scratchpads, Issues, Anomalies, Review, Health
11. **Scoring order**: Create items with different deadlines/staleness — verify ranking feels correct (nearest deadline + most stale at top)
12. **Client filter**: Filter to one client — Focus Queue and Waiting Sidebar both filter
13. **Vacation planning**: Set horizon to "Next 2 Weeks" — should show everything that'll need attention in that window
