# Action Center — Design Spec

**Date:** 2026-03-22
**Status:** Draft
**Scope:** Redesign of Inbox, Follow-ups, and Activities into a unified tabbed "Action Center" page

## Context

The current Inbox (`/inbox`), Follow-ups (`/followups`), and Activities (`/activities`) pages are separate, requiring navigation between three pages to manage daily work. Scratchpad processing is buried inside the Inbox page. This redesign consolidates these into a single tabbed page with a persistent sidebar, consistent with the tabbed layout direction established for client and policy pages. The new reusable tab component in `base.html` (`initTabs()`) is ready but not yet used — this will be its first production deployment.

## Design Decisions

### Page Structure
- **URL:** `/action-center` (new route, replaces `/inbox`, `/followups`, `/activities`)
- **Layout:** Two-column — tabbed content area (left, ~2/3 width) + sticky sidebar (right, ~1/3 width)
- **Tabs:** Follow-ups | Inbox | Activities | Scratchpads
- **Tab component:** Uses `initTabs('action-center', 'action-center-tab')` from `base.html`
- **Tab badges:** Follow-ups shows overdue count (red), Inbox shows pending count (red), Scratchpads shows non-empty count (red)
- **Tab loading:** Each tab re-fetches via HTMX on every click (not cached). This is intentional — the Action Center shows live data that changes frequently. `initTabs()` currently re-fetches on every click; no code change needed. Follow-ups tab loads on page render (default tab).
- **Tab persistence:** `sessionStorage` remembers last-used tab

### Sidebar (persistent across all tabs)
Three sections, top to bottom:

1. **Cross-Tab Stats** — 2x2 grid of stat cards:
   - Overdue follow-ups (red)
   - Due this week (amber)
   - Inbox pending (teal)
   - Hours this month (gray) — reuse `get_dashboard_hours_this_month()` from `queries.py`

2. **Quick Actions** — three buttons:
   - `+ Log Activity` (primary, teal) — scrolls to and switches to Activities tab, then opens a quick-log form at the top of the tab
   - `+ New Follow-up` (secondary) — scrolls to and switches to Follow-ups tab, opens an inline "new follow-up" form at the top
   - `Compose Email` (secondary) — opens the compose email `<details>` panel (same pattern as policy/client pages)

3. **Recent Activity Feed** — last 5 activities across all types, each showing type, client, description snippet, and relative timestamp

**Stat computation:** All sidebar stats are computed server-side in the route handler and passed as template context. Do not call DB functions from templates. The sidebar partial endpoint (`GET /action-center/sidebar`) queries stats and returns rendered HTML.

### Nav Changes
- Quick capture input stays in the nav bar (globally accessible)
- Nav link updates: replace the "Activity" dropdown (Follow-Ups, Activity Log, Meetings, Review) with a single "Action Center" link. Meetings and Review pages remain accessible via their existing direct URLs but are removed from the primary nav — they are secondary pages that can be linked from within the Action Center or accessed via search.
- Badge shows combined count: overdue follow-ups + pending inbox items

---

## Tab 1: Follow-ups

### Data Sources
Combines three existing query sources (reuse `get_all_followups()` from `queries.py`):
1. **Activity follow-ups** — `activity_log` where `follow_up_done=0` and `follow_up_date IS NOT NULL`
2. **Policy follow-ups** — `policies` where `follow_up_date IS NOT NULL`
3. **Client follow-ups** — `clients` where `follow_up_date IS NOT NULL`

Plus **suggested follow-ups** via `get_suggested_followups()` from `queries.py`.

### Layout
- **Filter bar:** Pill toggles for All / Overdue / Today / This Week / Suggested (each with count) — these are **client-side JS filters** that toggle visibility via `data-status` attributes on rows (no server round-trip). Type and Client dropdowns plus text search trigger **server-side HTMX re-fetch** of the full tab partial via `hx-get` with query params. This matches the existing pairing board filter pattern.
- **Three sections** with section headers:
  - **Overdue** (red background `bg-red-50`) — items past due date, sorted oldest first
  - **Upcoming** (white/amber) — items due today through filter window, sorted soonest first
  - **Suggested** (green background `bg-green-50`) — policies needing attention per `get_suggested_followups()`

### Row Layout
Each follow-up row shows:
- Color dot (red/amber/gray/green by urgency)
- Client name (bold, linked)
- Subject text
- COR thread badge (if applicable): `COR-{id} · Attempt N`
- Previous disposition as subtext (if thread history exists)
- Due date / overdue duration (right-aligned)
- **Follow Up** button (primary) — expands inline disposition form
- **Snooze** button (secondary) — quick delay

### Inline Disposition Form
Expands below the row when "Follow Up" is clicked. Contains:
- **Disposition pills** — quick-click buttons from `follow_up_dispositions` config (Completed, Left VM, Sent Email, Awaiting Response, No Answer, etc.)
- **Hours** input + **Note** input (inline)
- **Next follow-up** shortcuts: +1d, +3d, +7d, +14d pills + date picker
- **Save** button — POST creates new activity, marks current done, re-diaries

### Suggested Row Actions
- **Schedule** button — creates a follow-up (same as current)
- **Dismiss** button — hides from suggestions

### Existing Code to Reuse
- `get_all_followups(conn, ...)` — `src/policydb/queries.py` line ~528
- `get_suggested_followups(conn, ...)` — `src/policydb/queries.py` line ~958
- Disposition form pattern — `src/policydb/web/templates/activities/_activity_row.html` lines 64-123
- Snooze endpoint — `src/policydb/web/routes/activities.py`
- Follow-up/complete endpoints — `src/policydb/web/routes/activities.py`

---

## Tab 2: Inbox

### Layout
- **Capture area** at top: larger input with teal background (`bg-teal-50`), "Add to Inbox" button. Supports `@` contact tagging autocomplete.
- **Pending header** with item count and "Show processed" toggle checkbox
- **Pending items list** — chronological, newest first

### @ Contact Tagging
- **Autocomplete trigger:** Typing `@` followed by 2+ characters triggers contact search via `/inbox/contacts/search`
- **Keyboard selection:** **Tab** key selects the highlighted suggestion (in addition to Enter and click) for fast keyboard entry
- **Visual display:** Tagged contacts render as a **distinct indigo/purple pill** with `@` prefix: `@Jane Smith` in `bg-indigo-50 text-indigo-700` — clearly distinguishable from other metadata
- **Data flow:** `contact_id` is stored on the inbox record via hidden form field

### Row Layout
Each inbox item shows:
- `INB-{id}` tag (teal badge, copyable)
- Content text
- **Tagged contact pill** in indigo/purple: `@Jane Smith` (if @ tagged) — visually distinct, not plain gray
- Relative timestamp
- Three action buttons: **Process** (primary), **Schedule**, **Dismiss**

### Contact Carryover on Process
**Bug fix:** Currently `contact_id` from the inbox record is NOT transferred to the `activity_log` when processing. This must be fixed:
- When processing, query the inbox item's `contact_id`
- Auto-populate the `activity_log.contact_id` column in the INSERT
- The process form shows the tagged contact as a **pre-filled, editable field** so the user can confirm or change it
- This enables per-contact follow-up tracking in the activity and follow-up systems

### Inline Process Form
Expands below the row when "Process" is clicked. Contains:
- **Client** picker (combobox)
- **Policy** picker (select, filtered by client)
- **Contact** picker (combobox, pre-filled from @ tag if present — **new field**)
- **Type** select (from `activity_types` config)
- **Subject** input (pre-filled from content)
- **Hours** input
- **Details** textarea (pre-filled with full content)
- **Follow-up** shortcuts: +1d, +3d, +7d pills + date picker
- **Start COR** checkbox
- **Save & Process** button — creates `activity_log` (including `contact_id`), updates inbox `status='processed'`, sets `activity_id`

### Processed History
When "Show processed" is toggled on, show last 50 processed items below pending items, with reduced styling (muted text, timestamps).

### Existing Code to Reuse
- `inbox_page()` — `src/policydb/web/routes/inbox.py` line ~43
- `process_inbox_item()` — `src/policydb/web/routes/inbox.py` line ~174
- `capture_inbox()` — `src/policydb/web/routes/inbox.py` line ~27
- Contact search — `/inbox/contacts/search` endpoint
- `get_inbox_pending_count()` — `src/policydb/web/routes/inbox.py`
- Current template — `src/policydb/web/templates/inbox.html`

---

## Tab 3: Activities

### Layout
- **Filter bar:** Time window select (Last 7/30/90 Days, This Year), Type select, Client select, text search. Summary stat badge: "N activities · N.N hrs"
- **Contenteditable table** with columns: Date | Type | Client | Subject | Hrs | Disposition | Ref

### Inline Editing
- All cells are click-to-edit using `contenteditable` pattern
- Focused cell shows teal bottom-border highlight (no full input box)
- Save on `blur` via PATCH to activity update endpoint
- Server returns formatted value; flash green on format change (`flashCell()`)
- Type and Disposition cells use combobox pattern (not raw contenteditable)

### Column Details
- **Date** — formatted as MM/DD
- **Type** — colored pill badge (Call=blue, Email=amber, Meeting=indigo, Note=pink)
- **Client** — teal link to client detail page
- **Subject** — primary editable text
- **Hrs** — editable decimal
- **Disposition** — colored pill badge
- **Ref** — COR thread link (purple, clickable)

### Existing Code to Reuse
- Activity list query — `src/policydb/web/routes/activities.py` line ~776
- Activity row template — `src/policydb/web/templates/activities/_activity_row.html`
- `initMatrix()` from `base.html` for contenteditable table behavior
- `flashCell()` helper from `base.html`

### New Endpoint Needed
- `PATCH /activities/{id}/field` — update single field on activity (subject, type, duration_hours, disposition). Returns `{"ok": true, "formatted": "..."}`.

---

## Tab 4: Scratchpads

### Layout
- **Card per non-empty scratchpad** — stacked vertically
- **Empty state** when all scratchpads are clear: "All other scratchpads are empty. Notes added on client or policy pages will appear here."

### Card Layout
Each card has:
- **Header:** Scope badge (Dashboard=gray, Client=teal, Policy=indigo) + name (linked for client/policy) + "Updated X ago" timestamp
- **Content area:** Editable textarea with dashed border, auto-saves via existing scratchpad PATCH endpoints (800ms debounce)
- **Footer:** Two action buttons + auto-save indicator
  - **Process** (primary) — expands inline form, then creates activity + saves note + clears scratchpad
  - **Clear** (muted) — empties scratchpad without creating any record

### Process Form (expanded)
- **Type** select (default "Note")
- **Subject** input
- **Hours** input
- **Follow-up** shortcuts: +1d, +3d, +7d pills + date picker
- **Process** button — three-in-one action: creates `activity_log` entry, pins content to `saved_notes` (via `save_note()` — **new behavior**, current `scratchpad_process()` does not do this), clears scratchpad

### Scratchpad Aggregation
Reuse the aggregation pattern from current `inbox_page()` in `inbox.py`:
- Dashboard scratchpad (if non-empty) from `user_notes` table
- All client scratchpads with content from `client_scratchpad` table
- All policy scratchpads with content from `policy_scratchpad` table

### Existing Code to Reuse
- Scratchpad aggregation — `src/policydb/web/routes/inbox.py` (within `inbox_page()`)
- Dashboard scratchpad save — `POST /dashboard/scratchpad`
- Client scratchpad save — `POST /clients/{id}/scratchpad`
- Policy scratchpad save — `POST /policies/{uid}/scratchpad`
- Save note — `save_note()` in `src/policydb/queries.py`
- Process scratchpad — `POST /inbox/scratchpad/process` in `src/policydb/web/routes/inbox.py`

### Backport: Unified Process Flow
**Also update scratchpad widgets on client and policy detail pages** to use the same combined Process flow (log activity + save note + clear) instead of the current three separate buttons. This ensures consistent behavior across the app.

---

## Routing & Navigation Changes

### New Routes (in new `action_center.py` route module)
- `GET /action-center` — main page, renders shell with tabs
- `GET /action-center/followups` — HTMX partial for Follow-ups tab content
- `GET /action-center/inbox` — HTMX partial for Inbox tab content
- `GET /action-center/activities` — HTMX partial for Activities tab content
- `GET /action-center/scratchpads` — HTMX partial for Scratchpads tab content
- `GET /action-center/sidebar` — HTMX partial for sidebar (stats + actions + recent)

### New Endpoint (in `activities.py`)
- `PATCH /activities/{id}/field` — update single field on activity. Lives in `activities.py` alongside other activity mutation endpoints.

### Existing Endpoints Retained
All existing action endpoints stay (process inbox, complete activity, follow-up, snooze, scratchpad save, etc.) — the Action Center just provides a new frontend for them.

### Old Route Redirects
Each redirect goes in the module that currently owns the route:
- `/inbox` → `/action-center?tab=inbox` (redirect in `inbox.py`)
- `/followups` → `/action-center?tab=followups` (redirect in `activities.py`)
- `/activities` → `/action-center?tab=activities` (redirect in `activities.py`)

### Nav Update
- Replace individual nav links with single "Action Center" link
- Badge shows combined count (overdue follow-ups + pending inbox items)

---

## Sidebar OOB Update Mechanics

When an action is taken within any tab (follow-up completed, inbox item processed, scratchpad cleared), the sidebar stats and recent feed should update without a full page reload.

**Pattern:** Each action endpoint returns its primary HTML response plus an OOB swap for the sidebar:
```html
<div id="action-center-sidebar" hx-swap-oob="innerHTML">
  {# re-rendered sidebar partial #}
</div>
```

**Target element:** `#action-center-sidebar` (the sidebar container in `page.html`)

**Endpoints that need OOB sidebar updates:**
- `POST /activities/{id}/followup` (disposition/re-diary)
- `POST /activities/{id}/complete`
- `POST /inbox/{id}/process`
- `POST /inbox/{id}/dismiss`
- `POST /inbox/capture`
- `POST /inbox/scratchpad/process`
- `POST /inbox/scratchpad/clear`

The sidebar partial is re-rendered server-side with fresh stat counts and recent activity. This keeps the sidebar live without polling.

---

## Print Safety

- Sidebar carries `no-print` class — hidden in `@media print`
- Tab bar carries `no-print` class — hidden in print
- Only the active tab's content prints
- All action buttons (Follow Up, Snooze, Process, etc.) carry `no-print`
- Filter bar carries `no-print`

---

## Files to Create/Modify

### New Files
- `src/policydb/web/routes/action_center.py` — new route module
- `src/policydb/web/templates/action_center/page.html` — main page shell with tabs + sidebar
- `src/policydb/web/templates/action_center/_followups.html` — Follow-ups tab partial
- `src/policydb/web/templates/action_center/_inbox.html` — Inbox tab partial
- `src/policydb/web/templates/action_center/_activities.html` — Activities tab partial
- `src/policydb/web/templates/action_center/_scratchpads.html` — Scratchpads tab partial
- `src/policydb/web/templates/action_center/_sidebar.html` — Sidebar partial

### Modified Files
- `src/policydb/web/app.py` — register new router, update old route redirects
- `src/policydb/web/templates/base.html` — update nav links (Action Center replaces Inbox/Follow-ups/Activities)
- `src/policydb/web/routes/activities.py` — add redirects from `/followups` and `/activities`, add `PATCH /activities/{id}/field` endpoint
- `src/policydb/web/routes/inbox.py` — add redirect from `/inbox`
- `src/policydb/web/templates/clients/_scratchpad.html` — update to combined Process flow
- `src/policydb/web/templates/policies/_scratchpad.html` — update to combined Process flow

---

## Verification

1. **Navigate to `/action-center`** — page loads with Follow-ups tab active and sidebar visible
2. **Tab switching** — click each tab, verify lazy loading works, tab persistence across page reloads
3. **Follow-ups tab:**
   - Overdue, upcoming, and suggested sections render with correct colors
   - Filter pills toggle sections
   - "Follow Up" button expands disposition form inline
   - Submitting disposition creates activity and re-diaries
   - Snooze delays follow-up
4. **Inbox tab:**
   - Capture area accepts text, @ tagging works
   - "Add to Inbox" creates pending item
   - Process/Schedule/Dismiss buttons work
   - Process form expands and creates activity on submit
   - "Show processed" toggle reveals history
5. **Activities tab:**
   - Table renders with correct data and filters
   - Click any cell to edit, saves on blur
   - Type and disposition show colored badges
   - COR refs are clickable
   - Summary stat updates with filters
6. **Scratchpads tab:**
   - Shows only non-empty scratchpads
   - Inline editing auto-saves (800ms debounce)
   - Process button expands form, creates activity + saves note + clears
   - Clear button empties scratchpad
   - Empty state shows when all are clear
7. **Sidebar:**
   - Stats update when actions are taken (use OOB swaps)
   - Quick Action buttons open appropriate forms
   - Recent feed shows latest activities
8. **Old routes** — `/inbox`, `/followups`, `/activities` redirect to Action Center with correct tab
9. **Nav** — single Action Center link with combined badge count
10. **Scratchpad backport** — client and policy detail pages use combined Process flow
