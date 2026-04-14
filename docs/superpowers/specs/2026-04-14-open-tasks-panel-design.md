# Open Tasks Panel — Design

**Date:** 2026-04-14
**Status:** Design approved; ready for implementation plan

---

## 1. Problem

Renewal issues are the natural "command center" for a renewal in progress, but today the user cannot triage outstanding work from the issue page:

- The Scope Rollup card (shipped in PR #208) surfaces open follow-ups on the issue's linked policies, but read-only.
- Child follow-ups attached to the issue's activity thread are editable individually, but there's no unified surface for "everything outstanding on this renewal."
- Stray follow-ups on scope policies (not yet attached to the issue) can't be attached or triaged from the issue page.
- The same aggregation problem exists on client, program, and policy pages — there's no single "what's outstanding?" view on any of them.

The user wants a unified, interactive **Open Tasks panel** that rolls up outstanding activity follow-ups across the relevant scope and supports inline triage actions, available on every page where aggregation makes sense: issue, client, program, policy.

## 2. Goals

- One command-center panel, rendered on issue / client / program / policy pages, showing every open follow-up in scope.
- Inline actions on each row: mark done, snooze, toggle My Move / Waiting, log & close, attach to issue, add note.
- `+ Add task` button to create a new follow-up attached to the current scope without leaving the page.
- Shared component (one template, one backend helper) parameterized by scope type.
- Small shared toast library so every action gives explicit confirmation feedback.

## 3. Non-goals (v1)

- **No Focus Queue changes.** Issues and their child follow-ups continue to appear independently. Score rollup deferred to v2.
- **No bulk select / bulk actions.** Per-row actions are faster for typical issue sizes (<10 items).
- **No reassign between policies.** A follow-up stays on its original policy.
- **No unattach-from-issue action.** Attach is one-way; misattaches are corrected via mark-done + re-log.
- **No SLA recompute on the issue itself.** The issue's own `due_date` and `follow_up_date` are unchanged by this panel. It's a display + action surface, not a driver of the issue's own timing.
- **No keyboard shortcuts** in v1. Consider `j/k/d/s` if usage justifies.
- **No notifications.** Toasts cover in-context confirmation; no badges, no pings.

## 4. Data

### 4.1 Backend helper

```python
def get_open_tasks(
    conn: sqlite3.Connection,
    scope_type: Literal["issue", "client", "program", "policy"],
    scope_id: int,
) -> dict:
    """Returns {
        "groups": [GroupDict, ...],   # ordered top-down per scope rules
        "total": int,
        "overdue": int,
        "waiting": int,
    }"""
```

Lives in `src/policydb/queries.py` alongside `_rollup_open_followups`. The existing `_rollup_open_followups` stays in place — it still drives the program Scope Rollup card on non-issue surfaces and is simpler than what we need for the interactive panel.

### 4.2 Group / row shapes

```python
GroupDict = {
    "key": str,            # stable id, e.g. "on_issue", "loose", "issue:42", "direct_client"
    "title": str,          # rendered header, e.g. "On this issue", "On ISS-2026-042"
    "subtitle": str | None,
    "rows": list[TaskRow],
}

TaskRow = {
    "activity_id": str,         # numeric string for activity rows, "P{pid}" for policy-source rows
    "subject": str,
    "activity_type": str | None,
    "follow_up_date": str,      # ISO YYYY-MM-DD
    "days_overdue": int,        # negative if future
    "disposition": str,
    "accountability": Literal["my_action", "waiting_external", "scheduled"],
    "policy_id": int | None,
    "policy_uid": str | None,
    "policy_type": str | None,
    "client_id": int,
    "client_name": str,
    "source": Literal["activity", "policy", "client"],
    "is_on_issue": bool,                  # does activity_log.issue_id == current scope (issue page only)
    "linked_to_other_issue": str | None,  # issue_uid if attached elsewhere
    "attach_target_issue_id": int | None, # resolved target for attach action (policy/client/program pages)
}
```

### 4.3 Grouping rules

| Scope | Groups (top-down) |
|---|---|
| `issue` | `on_issue` (items where `activity_log.issue_id = scope_id`) → `loose` (items on linked policies via `v_issue_policy_coverage` where `issue_id` is NULL or a different issue) |
| `client` | `direct_client` → one group **per open issue** (`issue_status NOT IN ('Resolved','Closed')`) touching this client, ordered by severity then earliest open follow-up → `loose_policies` (follow-ups on client's policies not covered by any open issue) |
| `program` | `on_program_issue` (items linked to any of the program's open issues) → `loose` (items on child policies not linked to an open issue) |
| `policy` | Single flat group `on_policy` — every open follow-up on this policy regardless of issue linkage |

**`direct_client` group** includes:
- `clients.follow_up_date` itself (as a synthetic row `C{client_id}` with `source="client"`)
- Any `activity_log` row with `client_id = scope_id`, `policy_id IS NULL`, `follow_up_done = 0`, `follow_up_date IS NOT NULL`

**"Open issue"** means an `activity_log` row with `item_kind='issue'` and `issue_status NOT IN ('Resolved', 'Closed', 'Merged')` and `merged_into_id IS NULL`. Used consistently across client/program grouping.

Within every group: sort by `days_overdue` desc, then `follow_up_date` asc.

### 4.4 Dedup / suppression rules

- Policy-source rows (`policies.follow_up_date` with no activity) are suppressed when an activity-source follow-up exists on the same policy — same rule as `get_all_followups()` and `_rollup_open_followups`.
- Client-source rows (`clients.follow_up_date`) always appear on the client page in `direct_client`. They are NOT suppressed by policy/activity rows.
- Archived policies (`archived = 1`) are excluded from every scope.
- Merged issues (`merged_into_id IS NOT NULL`): activities linked to a merged source issue are shown under the target issue's group. On the client page, the source issue's group is hidden (it's Closed).

## 5. Routes

All routes live in a new module `src/policydb/web/routes/open_tasks.py`. Registered in `app.py`.

### 5.1 Render

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/open-tasks/panel?scope_type=X&scope_id=N` | Renders the full panel partial. Used for initial lazy-load from each page, and as the target of every action's HTMX swap. |

### 5.2 Row actions

Each endpoint takes `return_scope_type` and `return_scope_id` in the body (hidden inputs). After mutating, it calls `get_open_tasks(return_scope_type, return_scope_id)` and renders `_open_tasks_panel.html`.

| Method | Path | Body |
|---|---|---|
| `POST` | `/open-tasks/{activity_id}/done` | `return_scope_type`, `return_scope_id` |
| `POST` | `/open-tasks/{activity_id}/snooze` | `days` (int) or `date` (ISO), `return_scope_*` |
| `POST` | `/open-tasks/{activity_id}/disposition` | `move=my\|waiting`, `return_scope_*` |
| `POST` | `/open-tasks/{activity_id}/log-close` | `return_scope_*` |
| `POST` | `/open-tasks/{activity_id}/attach` | `target_issue_id`, `return_scope_*` |
| `POST` | `/open-tasks/{activity_id}/note` | `text`, `return_scope_*` |

`activity_id` is a path segment with two forms:
- `"123"` — `activity_log.id` for activity-source rows
- `"P456"` — synthetic prefix for policy-source rows (operates on `policies.id = 456`'s `follow_up_date`)
- `"C789"` — synthetic prefix for client-source rows (operates on `clients.id = 789`'s `follow_up_date`)

Policy-source and client-source rows support only `done` and `snooze`. Other actions return 400.

### 5.3 Creation

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/open-tasks/new?scope_type=X&scope_id=N` | Returns an inline quick-log form partial. Fields: subject, policy dropdown (scoped — see below), follow-up date, disposition toggle. |
| `POST` | `/open-tasks/new` | Body: `subject`, `policy_id` (optional), `follow_up_date`, `disposition`, `scope_type`, `scope_id`. Creates `activity_log` row with appropriate `issue_id` + `policy_id`, runs `supersede_followups()`, returns re-rendered panel. |

Policy dropdown content per scope:
- `issue` → policies linked to the issue via `v_issue_policy_coverage`
- `client` → all non-archived policies on the client
- `program` → all child policies of the program
- `policy` → current policy only (dropdown hidden, hidden input)

If scope is `issue` and policy is blank → activity is attached at client level with `issue_id = scope_id`.
If scope is `client` and policy is blank → direct client follow-up, `issue_id = NULL`.

### 5.4 Route ordering rule

`/open-tasks/new` must be declared before `/open-tasks/{activity_id}/...` so `new` isn't captured as an id. This is the project's standing `feedback_route_ordering_literals_first` rule.

## 6. UI

### 6.1 Template files

- `src/policydb/web/templates/_open_tasks_panel.html` — the shared panel (top-level, not scoped to a page subfolder)
- `src/policydb/web/templates/_open_tasks_new_form.html` — the inline quick-log form
- `src/policydb/web/templates/_toast.html` — shared toast container + JS helper (new global infrastructure)

### 6.2 Panel layout

```
┌─ Open Tasks ─────────────────── N total · M overdue · [+ Add task] ─┐
│                                                                      │
│  ▼ On this issue (3)                                                │
│    ┌──────────────────────────────────────────────────────────┐    │
│    │ [POL-042] Send loss runs to carrier    Due 4/12 · 2d OD  │    │
│    │ GL · Joe Client                  [✓] [💤▾] [⏳] [⊗] [💬] │    │
│    └──────────────────────────────────────────────────────────┘    │
│    ...                                                              │
│                                                                      │
│  ▼ Loose on scope (2) — not yet attached                            │
│    ┌──────────────────────────────────────────────────────────┐    │
│    │ [POL-043] Request SOV update           Due 4/20          │    │
│    │ Property · Joe Client            [✓] [💤▾] [⏳] [🔗] [💬] │    │
│    └──────────────────────────────────────────────────────────┘    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.3 Row anatomy

- **Line 1:** Policy UID pill (font-mono, click opens policy page in new tab) + subject (truncated)
- **Line 2 (meta):** `{policy_type} · {client_name} · {activity_type}` small gray
- **Right side:** due date + red "Nd overdue" chip if `days_overdue > 0`
- **Action buttons:** appear on row hover (desktop) / always visible (touch):
  - `✓` Mark done
  - `💤 ▾` Snooze — dropdown with +1d / +3d / +7d / +14d / "Pick date"
  - `⏳` / `🏃` Waiting / My Move toggle
  - `⊗` Log & close (hidden on policy-source rows)
  - `🔗` Attach to issue (hidden on `on_issue` rows and policy-source rows)
  - `💬` Inline note

### 6.4 Empty states

- **Nothing in any group:** Panel collapses to one-line card "✓ Nothing outstanding" + `[+ Add task]`.
- **One group empty:** That group's header is hidden; the other fills the panel.
- **Scope has no applicable policies** (e.g., an issue with no linked policies): panel does not render at all. Existing activity thread handles the display.

### 6.5 Cross-linked rows

If a scope policy has an open follow-up attached to a *different* issue (not in the current scope), the row renders in the `loose` group with:

- Gray background / reduced opacity
- `🔗` button replaced by a small `ISS-XXX` link pointing at the other issue
- Tooltip: "Attached to ISS-XXX — open that issue to triage"

This prevents silent theft of another issue's thread. Works on all four pages.

### 6.6 Quick-log form (+ Add task)

Inline form rendered in place of the `+ Add task` button when clicked. Fields:

- Subject (required, text)
- Policy (combobox, scoped per §5.3)
- Follow-up date (date input)
- Disposition toggle (My move / Waiting)
- Save / Cancel buttons

HTMX POSTs to `/open-tasks/new` and re-renders the full panel on success.

### 6.7 Toasts

Shared `_toast.html` rendered once in `base.html`. JS helper `showToast(message, kind='success')` creates a transient pill at the bottom-right of the viewport, fades after 2.5s. Action handlers include an `hx-swap-oob` `<div id="toast-trigger" data-message="Snoozed +7d" data-kind="success"></div>` in the response; a tiny HTMX `afterSwap` listener reads it and calls `showToast`.

Kinds: `success` (green), `info` (blue), `warning` (amber), `error` (red). All actions use `success` on the happy path; errors show `error`.

## 7. Handler behavior

| Action | Activity-source | Policy-source |
|---|---|---|
| Mark done | `follow_up_done=1`, `auto_close_reason='manual'`, `auto_closed_by='open_tasks_panel'` | Policy-source: `policies.follow_up_date = NULL`. Client-source: `clients.follow_up_date = NULL` |
| Snooze | Shifts `activity_log.follow_up_date` by N days; no new row; supersession NOT rerun | Shifts `policies.follow_up_date` or `clients.follow_up_date` respectively |
| Waiting toggle | Sets `activity_log.disposition` to first config entry with `accountability='waiting_external'` (or empty for My Move) | N/A — button hidden |
| Log & close | `follow_up_done=1` AND `follow_up_date=NULL` | N/A — button hidden |
| Attach to issue | `activity_log.issue_id = target`. If already linked to a different issue, return confirmation popover ("This task is already on ISS-XXX. Move?") | N/A — button hidden |
| Note | Creates new `activity_log` row: `activity_type='Note'`, `subject={text}`, `follow_up_done=1`, `follow_up_date=NULL`, `policy_id` + `issue_id` inherited from parent row. Sibling note, not a parent edit. | Same — parent's `policy_id` used, `issue_id` NULL |
| + Add task | Creates `activity_log` row with `issue_id` + `policy_id` per §5.3, `item_kind='followup'`, runs `supersede_followups()` | N/A — new rows are always activity-source |

### 7.1 Attach-target resolution on non-issue pages

- **Policy page:** `attach_target_issue_id` = the row's policy's single open renewal issue. If none, button hidden. If multiple, attach button opens a small picker (list of open issues touching the policy).
- **Client page:** Same rule, keyed off the row's policy.
- **Program page:** `attach_target_issue_id` = the program's own renewal issue.

### 7.2 Issue-scoped merge handling

- If current issue is merged into another (has `merged_into_id`), panel redirects to the target issue's panel with a banner "Merged into ISS-YYY."
- Closed / Resolved issues: panel renders but all action buttons disabled; banner "Issue resolved — reopen to triage tasks."

## 8. Integration points

### 8.1 Existing automation

All existing automation in `policydb-activities` continues to apply unchanged:

- `supersede_followups()` fires on `+ Add task`. Other actions (snooze, attach, note) do not fire it.
- `auto_link_to_renewal_issue()` does not fire for panel actions — we always set `issue_id` explicitly.
- `auto_close_followups(reason='issue_resolved' | 'issue_merged' | 'renewal_bound')` closes panel rows exactly as today when the parent issue hits those states.
- `auto_close_stale_followups()` applies unchanged; stale closures land in the activity history.
- Focus Queue / Action Center: no changes. Panel rows still appear in Focus Queue as FOLLOW-UP items.

### 8.2 Scope Rollup card

- The existing `_scope_rollup.html` "Open Follow-ups" sub-section is removed.
- Scope Rollup card continues to show: RFI, Checklist, Waiting On, Renewal Status, Financials, Contacts, Working Notes, Nested Issues.
- The removed sub-section is superseded by the new panel above it — same data, richer interaction.

### 8.3 Activity thread views

- Each page's existing activity thread (`_tab_activity.html` on clients, `_tab_activity.html` on programs, activity thread on issue detail, activity section on policy edit) becomes a **history** view.
- History view filters out any rows meeting the panel's "open task" criteria (`follow_up_done=0 AND follow_up_date IS NOT NULL`). Closed / Note / notes-only rows stay.
- The panel is the single source of truth for open tasks; the thread is the single source of truth for historical activity.

## 9. Pages receiving the panel

| Page | Template insertion point | Lazy or inline |
|---|---|---|
| Issue detail (`issues/detail.html`) | Above Scope Rollup card | Inline on page load |
| Client detail (`clients/edit.html`, Overview tab) | Top of Overview tab, above Account Summary | Lazy-loaded when Overview tab opens |
| Program detail (`programs/_tab_overview.html`) | Above Scope Rollup | Lazy-loaded |
| Policy edit (`policies/edit.html`) | Above activity thread | Inline |

Lazy-loading uses `hx-trigger="load"` on an empty container with `hx-get="/open-tasks/panel?scope_type=X&scope_id=N"`.

## 10. Edge cases

| Case | Handling |
|---|---|
| Issue has no linked policies | Panel does not render. Existing activity thread covers it. |
| Issue is merged | Panel redirects to target with banner. |
| Issue is Closed/Resolved | Panel renders read-only with banner. |
| Policy archived | Row excluded from every panel. |
| Policy has multiple open issues touching it | Attach button opens small picker. |
| Row's activity already attached to another issue | Grayed out, attach replaced with link to other issue. |
| Note text empty | Save button disabled; no-op. |
| Snooze to a past date | Allowed — same as manual re-diary today. |
| + Add task with no policy, on policy page | Not possible — dropdown hidden, always equal to current policy. |
| + Add task creates dup follow-up on same policy | Supersession handles it — new row wins, old row auto-closes with `superseded` reason. |
| Two users simultaneously (rare, local app) | Last write wins. Next action re-reads the panel. |

## 11. Config

No new config keys. Reuses:

- `follow_up_dispositions` — to resolve "Waiting on Response" label for the toggle
- `focus_score_weights` — unchanged (no Focus Queue integration)
- `stale_auto_close_days` — unchanged
- `renewal_issue_auto_create`, `renewal_issue_auto_link` — unchanged

## 12. Testing

- Unit tests for `get_open_tasks()` covering each `scope_type` with fixtures containing mixed activity-source / policy-source / cross-linked / archived / merged data.
- Route tests for each handler with both `activity_id` forms (`"123"` and `"P456"`).
- Template test: panel renders with empty groups, partial-empty groups, fully-empty state, and cross-linked grayed rows.
- Manual browser QA on all four pages per CLAUDE.md "QA Testing Requirement."

## 13. File inventory

### New files

- `src/policydb/web/routes/open_tasks.py` — route module
- `src/policydb/web/templates/_open_tasks_panel.html` — panel partial
- `src/policydb/web/templates/_open_tasks_new_form.html` — inline create form
- `src/policydb/web/templates/_toast.html` — shared toast container

### Modified files

- `src/policydb/queries.py` — add `get_open_tasks()` and any helper split-outs
- `src/policydb/web/app.py` — register `open_tasks` router + include `_toast.html` in base layout
- `src/policydb/web/templates/base.html` — include toast container + tiny `afterSwap` listener
- `src/policydb/web/templates/issues/_scope_rollup.html` — remove "Open Follow-ups" sub-section
- `src/policydb/web/templates/issues/detail.html` — insert panel above Scope Rollup
- `src/policydb/web/templates/clients/_tab_overview.html` — insert panel
- `src/policydb/web/templates/programs/_tab_overview.html` — insert panel
- `src/policydb/web/templates/policies/edit.html` — insert panel
- `src/policydb/web/templates/issues/detail.html` + friends — filter activity-thread history to exclude open task rows
- Corresponding `_tab_activity.html` files on clients/programs — same filter

## 14. Open questions

None — all design questions resolved through the brainstorming dialogue.
