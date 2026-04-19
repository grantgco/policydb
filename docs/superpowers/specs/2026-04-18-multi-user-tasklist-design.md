# Multi-User Task List + Cross-Platform Packaging — Design Spec

**Date:** 2026-04-18
**Status:** Awaiting implementation (decisions locked during 2026-04-18 brainstorm)
**Branch:** `feat/multi-user-tasklist`
**Scope:** Two coupled deliverables on one feature branch, shipped in order: (1) a new "Today" task list that replaces the Focus Queue as the Action Center's default tab; (2) cross-platform desktop installers (Mac + Windows) so Mark can run a private, single-user copy alongside Grant's Python/CLI dev install.

## Problem

Two problems, one branch, because they are coupled by the same trigger: Mark (Grant's husband and coworker) now needs to use PolicyDB on his own machine.

**Problem 1 — the Focus Queue does not fit a "what do I need to do today" mental model.** The current Action Center lands on the Focus Queue, a scored list that mixes follow-ups with synthetic suggestions (inbox items, open issues, overdue milestones, upcoming project/opportunity deadlines, insurance-needed-by dates). The scoring engine is useful, but the top-level surface obscures the simple question most brokers start their day with: *what's on my list today?* There is no standalone task concept; anything that isn't tied to a client/policy has nowhere to live. Mark has already asked for a "to-do list."

**Problem 2 — PolicyDB ships only as a Python CLI.** Grant runs it daily out of a venv; this is fine for Grant. Mark is not going to install Python, uvicorn, or a venv. He needs a double-clickable app on Windows. Grant wants to keep his Python dev install so nothing he does day-to-day changes, and the packaged app must be single-user per install (no sync, no shared DB) so the two books of business stay cleanly separated.

The two deliverables are coupled because the day one Mark opens the packaged app, he needs a landing page that reads as a task list — not a scored Focus Queue that requires explaining. Today tab ships first so that when the installer lands, the default view already makes sense.

## Goals

1. **Today tab** becomes the Action Center's default landing surface and replaces the visible Focus Queue. Reads the user's follow-ups directly via a new `v_today_tasks` SQL view.
2. **Standalone tasks** — the ability to create a follow-up with no client/policy link — are first-class, with no schema migration (the constraint that currently prevents this is just lifted).
3. **Smart Suggestions rail** keeps the scoring engine's value (surfacing things the user *could* turn into tasks) without cluttering the primary list. One-click fast capture from a suggestion to an actual task.
4. **Focus Queue retires cleanly** — the tab, templates, and sidebar go away, but the engine (`focus_queue.py`) stays, renamed in purpose only (powers Suggestions now).
5. **Plan Week re-skin** — visual parity with Today (shared Tabulator components) without behavior change.
6. **Cross-platform installers** — signed-eventually .msi and .dmg produced from a single repo via one build script and GitHub Actions, bundling Python + uvicorn + SQLite.
7. **Private per-install data** — each install has its own platform-appropriate data dir (`~/.policydb/` on Mac, `%APPDATA%/PolicyDB/` on Windows) and no network sync.
8. **Friction-free upgrade for Grant** — the first time Grant launches the packaged Mac build, his existing `~/.policydb/` data is used without prompting.
9. **Touch-once principle preserved** — Today tab, Add Task modal, and Smart Suggestions all read from canonical sources and write back to canonical sources. No parallel task table, no scratch follow-up duplication.

## Non-goals (v1)

- Multi-user shared data, cross-device sync, or shared tasklists. Every install is its own universe.
- Outlook integration on Windows. The Mac AppleScript bridge stays Mac-only; Windows UI hides Outlook affordances.
- Auto-updater. v1 is manual installer download. Auto-update is noted as a v2 candidate but not in scope.
- Code signing certificates. Gatekeeper/SmartScreen will warn on first launch; documented workaround is right-click → Open (Mac) / "More info → Run anyway" (Windows). Cert acquisition is deferred.
- iOS / iPadOS / Android packaging.
- Analytics or telemetry phoned home from the packaged app.
- New task schema. Follow-ups *are* tasks.
- Re-opening the design of the scoring engine itself. Weights and windows stay as configured.

---

## Terminology

- **Task** — user-facing name for an `activity_log` row with `follow_up_date IS NOT NULL` and `follow_up_done = 0`. Synonym for "follow-up" in UI copy; backend code continues to use `follow_up_*`.
- **Standalone task** — a task with no `client_id` or policy link. Created from the Add Task modal when both combobox fields are left empty.
- **Today tab** — the new default Action Center tab, Tabulator grid backed by `v_today_tasks`.
- **Smart Suggestions** — right-rail panel on the Today tab that shows everything the scoring engine thinks could become a task but isn't one yet. Powered by `build_focus_queue(..., suggestions_only=True)`.
- **Fast capture** — one-click `+` on a suggestion row creates the task immediately with sensible defaults, no modal.
- **Suggestion kinds** — `suggested`, `inbox`, `issue` (only those without a linked follow-up), `milestone` (only those without a linked follow-up), `insurance_deadline`, `project_deadline`, `opportunity`. These correspond to the existing `_normalize_*` functions in `focus_queue.py`.
- **Desktop launcher** — new entry point (`src/policydb/desktop.py`) that boots uvicorn in-process and opens a pywebview window; only active when `sys.frozen` is True (i.e., inside the packaged app).
- **Data dir** — platform-dependent per-install storage root. `~/.policydb/` on Mac, `%APPDATA%/PolicyDB/` on Windows.

---

## Deliverable 1 — Today Task List

### Data model

**No migration.** Follow-ups are already `activity_log` rows keyed by `follow_up_date` + `follow_up_done`. The "standalone task" concept is unlocked by lifting an existing UI-level constraint that required a client link on follow-up creation. Backend accepts `client_id = NULL` on follow-up rows (schema already permits).

### New view — `v_today_tasks`

Rebuilt on every server startup via `src/policydb/views.py` like every other view (the standard `DROP VIEW IF EXISTS; CREATE VIEW ...` pattern). Columns:

```
id                -- activity_log.id
subject           -- activity_log.subject
note              -- activity_log.note (trimmed for context line)
kind              -- 'task' (always; placeholder for future differentiation)
priority          -- derived from follow_up_date vs today (overdue=3, today=2, tomorrow=1, later=0)
follow_up_date    -- DATE; drives the bucket filter pills
client_id         -- NULLABLE (null = standalone)
client_name       -- JOINed display string
policy_id         -- NULLABLE
policy_uid        -- JOINed display string
contact_name      -- activity_log.follow_up_with (freeform name)
last_activity_at  -- MAX(activity_log.activity_at) on same (client, policy) for the context line
waiting_on        -- activity_log.waiting_on (NULLABLE — populates the "Waiting" bucket)
waiting_since     -- days since waiting started, used for the amber-nudge row flag
created_at
updated_at
```

Exclusion rules in the view:
- `follow_up_done = 0`
- `follow_up_date IS NOT NULL`
- `is_superseded = 0`
- Opportunities **are included** as tasks (they are real follow-ups a user created); the exclusion rule only applies to synthetic suggestions.

### Route + template wiring

New routes in `src/policydb/web/routes/action_center.py`:
- `GET /action-center?tab=today` — renders the Today tab (new default; see *Focus retirement* below)
- `GET /action-center/today` — HTMX partial, called when switching tabs
- `GET /action-center/today/suggestions` — HTMX partial, lazy-loaded with `hx-trigger="load delay:200ms, every 5m"` so the Today tab paints instantly and suggestions stream in

New routes in the same module for task CRUD:
- `POST /tasks/create` — Add Task handler (subject required, client/policy/contact/follow_up_date optional). Accepts both modal and fast-capture form bodies.
- `POST /tasks/{id}/complete` — toggles `follow_up_done=1`, returns an empty row plus an HX-Trigger emitting the undo toast.
- `POST /tasks/{id}/undo-complete` — re-opens the task (triggered by the undo toast within 5s).
- `POST /tasks/{id}/snooze` — body: `{"option": "tomorrow|this_week|next_week|custom", "date": "YYYY-MM-DD"}`. Writes new `follow_up_date`, returns the updated row.
- Existing activity PATCH routes continue to handle subject/note/contact edits — **no duplication** (touch-once).

Literals first in route ordering (matches `feedback_route_ordering_literals_first`): `/tasks/create` before `/tasks/{id}/...`.

New templates (all inside `src/policydb/web/templates/action_center/`):
- `_today.html` — tab shell: toolbar (filter pills, Plan Week link, Add Task button) + `#today-grid` + right-rail `#today-suggestions`.
- `_today_grid.html` — Tabulator grid (sets the column spec; see below).
- `_today_suggestions.html` — grouped Smart Suggestions panel (one section per group, greyed-out when empty).
- `_add_task_modal.html` — modal shown on Add Task click or Cmd/Ctrl+N. Fields: subject (textarea autofocus, required), client combobox (existing `_combobox.html` partial), policy combobox (scoped to selected client, hidden when empty), follow-up date (date input, default today), contact name (freeform text, no dropdown in v1).
- `_undo_toast.html` — 5-second fading toast with Undo button targeting `POST /tasks/{id}/undo-complete`.

Deleted templates:
- `src/policydb/web/templates/action_center/_focus_queue.html`
- `src/policydb/web/templates/action_center/_focus_item.html`
- `src/policydb/web/templates/action_center/_waiting_sidebar.html`

### Layout E — dense tabular grid

Rendering uses Tabulator 6.3 (follow the `policydb-spreadsheet` skill's `initSpreadsheet()` pattern). Keep it dense: user explicitly chose density over a focus-minimal layout for this redesign. **Do not soften** row height, padding, or density even though the `user_add_focus` memory notes an ADD-friendly bias — Grant resolved that tension in favor of density for the Today tab.

Columns, left to right:

| Width | Column | Content |
|---|---|---|
| 40px | ✓ | Checkbox that completes the task via `POST /tasks/{id}/complete` |
| 4px | priority bar | Left-edge stripe, color by urgency (red=overdue, amber=today, blue=tomorrow, neutral=later) |
| 72px | Kind chip | Badge reading "Task" (reserved for future kinds like "Standalone") |
| flex | Subject + context line | Line 1: `activity_log.subject` bolded. Line 2 muted: `{client_name} · {last_activity_preview}` or "Standalone task" |
| 180px | Client · Policy | `C123 · POL-042` with Client as link, Policy drill-down via existing slideover pattern |
| 140px | Contact | `follow_up_with` (freeform) |
| 90px | Last | Humanize-formatted `last_activity_at` (e.g., "3d ago") |
| 90px | Due | `follow_up_date` humanized, colored per priority |
| 40px | ⋯ | Actions menu: Snooze → {Tomorrow, This week, Next week, Custom}, Edit, Delete |

Row affordances:
- Rows with `waiting_since > cfg.get("focus_nudge_alert_days", 10)` get a faint amber background (this replaces the "nudge alert" concept from the Focus Queue).
- Tabulator initial sort: `(priority DESC, follow_up_date ASC, id ASC)`.
- Tabulator groupBy: none by default. Filter pills above the grid drive the active dataset.

### Filter pills

Pill buttons above the grid (multi-select, default-active set noted):

- **All open** (All open tasks) — deselects the others
- **Overdue** ✓ default-active — `follow_up_date < today`
- **Today** ✓ default-active — `follow_up_date = today`
- **Tomorrow** ✓ default-active — `follow_up_date = today + 1`
- **This week** — `follow_up_date` between today and next Sunday
- **Waiting** — rows where `waiting_on IS NOT NULL` (folds the old Waiting sidebar into a pill; count badge shows `Waiting (N)`)
- **Standalone** — rows where `client_id IS NULL`

Pill state persists in `sessionStorage` under `today-filter-pills` (array of active pill IDs). Toggling pills does not re-fetch; Tabulator filtering is in-memory over the already-loaded dataset.

### Add Task flow

- **Trigger:** top-right `+ Add Task` button; keyboard shortcut `Cmd+N` (Mac) / `Ctrl+N` (Windows) globally on the Today tab.
- **Modal (`_add_task_modal.html`):**
  - Subject (required, textarea autofocus)
  - Client combobox (existing `_combobox.html`) — empty allowed
  - Policy combobox — appears when a client is chosen, scoped to that client's policies; empty allowed
  - Follow-up date — `<input type="date">`, default = today
  - Contact name — freeform text (no dropdown v1)
- **Validation:** subject min 1 char, max 200 chars. Client/policy comboboxes accept only existing rows or blank.
- **Submit:** `POST /tasks/create` → returns the new Tabulator row payload and an HX-Trigger `taskCreated` which closes the modal and prepends the row.

### Complete flow

- Clicking the ✓ checkbox: fade the row out (200ms CSS transition), then `POST /tasks/{id}/complete`.
- Server returns 204 plus an HX-Trigger carrying the task id and original subject so the client can render the undo toast.
- Undo toast lives 5s, bottom-right, with "Task completed — Undo" wording. Undo fires `POST /tasks/{id}/undo-complete`, which re-opens the task and re-renders the row at its original position.
- If the user completes another task while an undo toast is visible, the previous undo is committed and the toast is replaced.

### Snooze flow

- ⋯ menu → Snooze submenu with canonical options: **Tomorrow**, **This week** (next Monday), **Next week** (Monday after next), **Custom** (inline date picker).
- `POST /tasks/{id}/snooze` returns the replacement row payload; Tabulator updates in place.

### Smart Suggestions panel (right rail, ~340px)

- Loaded via a separate HTMX request: `<div id="today-suggestions" hx-get="/action-center/today/suggestions" hx-trigger="load delay:200ms, every 5m">`. Non-blocking — the main grid paints first.
- Backed by a new signature on the existing engine:
  - `build_focus_queue(conn, horizon_days=0, client_id=0, suggestions_only=False)` — when `suggestions_only=True`, filters out any item whose underlying `activity_log.id` already appears in `v_today_tasks`, and skips the final focus/waiting split (returns a single list grouped by kind).
- Groupings rendered in the panel (fixed order):
  1. **Renewals expiring** — items with kind `suggested` or `insurance_deadline`
  2. **Inbox emails** — kind `inbox`
  3. **Issues at SLA risk** — kind `issue` (only unlinked ones)
  4. **Milestones at risk** — kind `milestone` (only unlinked ones)
  5. **Project / insurance deadlines** — kind `project_deadline`, `opportunity`
- **Empty groups are shown greyed out**, not hidden. This is a deliberate "caught up" signal.
- Each row shows: subject, client/policy context, urgency dot, and a `+` fast-capture button.
- **Fast capture** — clicking `+` posts to `/tasks/create` with pre-filled values derived from the suggestion (subject = suggestion title; client/policy from the suggestion; follow_up_date = suggestion's implied due date or today). No modal. Row disappears from Suggestions and appears in the grid.
- **Shift-click `+`** opens the Add Task modal pre-populated, allowing tweaks before save.

### Focus Center retirement

**Delete (files removed from git):**
- `src/policydb/web/templates/action_center/_focus_queue.html`
- `src/policydb/web/templates/action_center/_focus_item.html`
- `src/policydb/web/templates/action_center/_waiting_sidebar.html`

**Edit (not delete):**
- `src/policydb/web/routes/action_center.py` — flip default tab from `"focus"` to `"today"` (line ~938). Remove the Focus branch from the `action_center_page` dispatcher and replace with a Today branch. Remove the `_focus_ctx`/related helpers that are no longer used. Keep imports clean.
- `src/policydb/web/templates/action_center/page.html` — remove the Focus Queue tab button from the tab strip; remove the focus-tab JS stub; add the Today tab button as the leftmost tab.
- `src/policydb/web/routes/dashboard.py` — the existing "Active focus items" count on the dashboard repoints to `SELECT COUNT(*) FROM v_today_tasks`. Label text updates to "Open tasks today".

**Keep (renamed in purpose only):**
- `src/policydb/focus_queue.py` — filename unchanged (smaller diff, clearer review). Add a module docstring at the top: `"""Suggestion engine. Originally built for the Focus Queue tab (retired 2026-04-18); now powers Smart Suggestions on the Today tab."""`
- `build_focus_queue()` gets a new keyword `suggestions_only: bool = False`. When True:
  - `get_all_followups()` call is skipped (no pre-existing follow-ups in suggestions)
  - The final step filters out any normalized item whose `source='activity'` id appears in `v_today_tasks.id`
  - Returns just `(suggestions, [], stats)` — waiting list is empty in suggestions mode
- Config keys stay untouched: `focus_score_weights`, `focus_auto_promote_days`, `focus_nudge_alert_days`. Only the Settings UI labels for those keys are reworded to reference "Suggestions" (per `feedback_config_editable_lists` — no hardcoded lists; edit labels in `EDITABLE_LISTS` in `settings.py`).
- `auto_close_stale_followups()` and `generate_due_recurring_instances()` calls at the top of `build_focus_queue()` remain — they need to run regardless of which surface triggered the build.

**Backwards-compat shims:**
- `GET /action-center?tab=focus` — returns `302` → `/action-center?tab=today`
- `GET /focus` — returns `302` → `/today` (if either URL is deep-linked anywhere)
- One-time sessionStorage migration in `page.html`: on first load after the branch lands, if `sessionStorage.getItem('action-center-tab') === 'focus'`, overwrite to `'today'` and proceed. No warning shown — quiet upgrade.

**Ripples to audit (spec reviewer should confirm each is handled):**
- Dashboard count label + query
- Any cross-links from client detail or policy detail that pointed at `tab=focus`
- Links in email templates if any reference the Focus tab (unlikely — audit via grep)
- Help text / any user-facing strings that say "Focus Queue"

### Plan Week re-skin

- Existing route `GET /followups/plan` in `src/policydb/web/routes/activities.py` (line 1497) keeps its URL, handler, spread/dismiss/escalation logic — **no behavior change.**
- Presentation refactored to use the same Tabulator base component and shared CSS variables as the Today grid. Goal: visual consistency so flipping between Today and Plan Week feels like one product.
- Add a toolbar entry point on the Today tab: `Plan Week →` link (right-aligned in the toolbar). This becomes the sanctioned way to reach Plan Week.
- Legacy entry point `GET /followups` (which previously served as a pre-Action-Center landing page) is retired alongside the Focus Queue — it already 302s into the Action Center; that redirect target flips from `tab=focus` to `tab=today`.
- QA each spread action, each dismiss action, each escalation after re-skin — the visual refactor is the regression risk surface.

---

### Visual refinements

Guiding principle: **on-brand editorial density.** The existing Midnight Blue + warm parchment + DM Serif / DM Sans / JetBrains Mono pairing already differentiates this product from generic SaaS. These refinements move the Today tab from "competent wireframe" to "shipped product" without pivoting the aesthetic. All are additive to the Tier 1 layout already specified above.

Where items in this section refine (rather than add to) a locked decision, the refinement is called out explicitly so it can be reverted cleanly if QA pushes back.

#### Color-coding discipline — priority vs. kind

- The **left priority bar** (the 4px left-edge column) owns urgency only: red = overdue, amber = today, blue = tomorrow, neutral parchment = later.
- **Kind chips** own category only: all chips render on a neutral parchment (`--bg`) background with a colored dot or 2px left border indicating kind (Renewal, Issue, Milestone, Oppty, Task, Follow-up, Standalone).
- This removes the current mockup's double-coding where a "Renewal" chip inherits `.chip.high` red and sits next to a red priority bar — two signals for the same fact. Chips become a stable category language; urgency belongs entirely to the bar.

#### Grid ergonomics

- **Ledger hairline every 5th row** in `--border` color — not full zebra stripes. Anchors the eye during a 25-row scan and fits the accounting-ledger metaphor appropriate to insurance.
- **Row hover (120ms ease-out):** priority bar momentarily replaced by a 2px Midnight Blue inset; `•••` action button fades from `opacity: 0.35` to `1`; subject line gains a 1px accent-blue underline with 3px offset. All three animate together.
- **Stagger fade-in on first paint:** first 8 rows fade up over 240ms with 20ms stagger, `cubic-bezier(0.2, 0.9, 0.3, 1.0)`. Initial paint only — filter toggles are instant.
- **Overdue-only priority-bar pulse:** 2.8s ease-in-out loop between `opacity: 1.0` and `opacity: 0.55` on priority bars where `due < today`. This is the *only* ambient animation in the grid; everything else is user-triggered.

#### Editorial header above the grid

Replace the generic `<h2>Today</h2>` with a two-line editorial block sitting above the toolbar:

- **Line 1:** today's date formatted as `Saturday · April 18` in DM Serif Display italic, ~18px, brand blue.
- Thin horizontal rule (`border-top: 1px solid var(--border)`) spanning the frame.
- **Line 2:** inline stats `10 open · 3 overdue · 11 suggestions` in DM Sans 12px muted, letter-spacing 0.02em.

Sets a calm "you opened the paper" tone without toy-ifying the interface.

#### Filter pills — ring, not fill

- **Active pill:** 2px `var(--brand)` ring, transparent background, brand-blue text, small count dot (`● 3`) after the label.
- **Inactive pill:** `--bg` parchment background, `--muted` text, 1px `--border`.
- **All open** pill visually distinct: rendered with a thin slash divider prefix to signal "clear filters" semantics rather than just "another bucket."

Matches how the codebase's existing segmented-tab controls read, and avoids the mockup's current "active = filled blue button" which visually competes with the primary `+ Add task` button.

#### Empty "caught up" state

When active filters produce zero rows:

- 3px brand-blue left rule, ~120px tall
- `Inbox Zero for today.` — DM Serif Display italic, ~22px, brand blue
- `Take the afternoon off — or add a task.` — DM Sans 13px muted body
- `+ Add task` button directly below, left-aligned

Treats a cleared queue as a celebrated state, not an error state. Fits the ADD-aware design intent noted in `user_add_focus.md`.

#### Complete-task micro-interaction

Replace the default `<input type="checkbox">` with a custom SVG control whose transitions make completion feel earned (and the 5s undo toast feel like a safety net, not a bug):

- **Unchecked:** 16px square, 1.5px `--muted` stroke, parchment fill.
- **Hover:** stroke color snaps to `--accent` over 120ms.
- **Checked:** stroke + fill flood to `--brand`; tick SVG path draws in via `stroke-dashoffset` over 200ms.
- **Row then applies** `text-decoration: line-through` in muted color for 400ms before the existing 200ms fade-out defined in the Complete flow.

The user literally sees their task strike out before the row vanishes.

#### Ref-pill consistency in context lines

When the muted context line (line 2 of the Subject column) references a policy or client UID, wrap the ID in the same `ref-pill` JetBrains Mono treatment used in the Client · Policy column. One typographic language for identifiers across the entire tab — no plaintext IDs, no underlined links masquerading as IDs.

#### Nudge-age visual — refinement of the locked "faint amber background"

The locked decisions specified that rows past `focus_nudge_alert_days` get a faint amber row background. **Refinement:** in a dense 25-row grid a full-row color wash competes with the priority bar and the red-overdue rows above it. Apply instead:

- A 4×8px amber notch at the top-left of the priority bar (reads as a folded-corner "bookmark").
- The "Last" column text flips to `var(--amber)`.

Less color bomb, more signal. **Revert to the original full-row amber background** if QA finds the notch too subtle — call it out explicitly during implementation PR review so the tradeoff is reviewed, not silently decided.

#### Suggestions rail polish

- Each suggestion row gets a 3px left-edge stripe in the same priority color language as the main grid (consistency reward when the user's eye flicks between the two surfaces).
- **Fast-capture `+` becomes a 22px circular ghost button:** 1px `--border`, parchment fill, brand-blue `+` glyph.
- **Hover:** ring fills to `--accent`, `+` glyph inverts to white, 120ms ease.
- Shift-click opens the Add Task modal (per locked decision). Tooltip on hover mentions the shift-modifier; visual is identical click vs. shift-click.

#### Typography + button micro-details

- **Subject line:** DM Sans 13px / 600 weight, `--brand` color.
- **Context line:** DM Sans 12px / 400 weight, color slightly darker than `--muted` (roughly `#7A7468`) — improves scan legibility without losing the secondary-text hierarchy.
- **Kind chips:** 10px / 600, uppercase, letter-spacing 0.06em. Matches existing design-system chip treatment.
- **Add Task button** renders a keyboard hint: `+ Add task  ⌘N` where `⌘N` (or `Ctrl+N` on Windows) is 10px at 0.75 opacity inside the primary button. One detail that separates shipped software from mockup.
- **Sort-direction affordance:** replace Unicode `▾` in column headers with a 10×6px SVG caret stroke-matched to DM Sans weight. Only the currently-sorted column shows a filled caret + a small brand-blue dot to the right of the label indicating direction.

#### Accessibility + print

- **Reduced motion:** wrap the overdue pulse, stagger fade-in, checkbox draw, and row-hover transitions in `@media (prefers-reduced-motion: reduce)` — each collapses to an instant state change when the user prefers reduced motion. Non-negotiable for the ADD-aware audience.
- **Print (`@media print`):**
  - Priority bar column collapses into a `»` glyph prefix on the subject line
  - Chips render as bracketed text (`[Renewal]`, `[Issue]`, `[Milestone]`) for legibility on paper
  - All animations disabled; row hover disabled
  - Hairline ledger rule retained (actually more useful on paper)
  - Existing `.no-print` toolbar class convention applies to filter pills and Add Task

#### Open question — dark mode

Not in the locked decisions. **Recommended answer: defer to v2.** The `policydb-design-system` palette is tuned for warm-neutral light surfaces; inverting it cleanly requires a parallel token set and bespoke chip-background handling. Mark's primary use case is Windows daytime, so parchment-on-brand doesn't glare in most sessions. Re-evaluate after his first week if a dark-theme-wide Windows user finds the light surface uncomfortable. Don't build it into v1.

---

## Deliverable 2 — Cross-Platform Desktop App

### Architecture

- **Shell:** `pywebview` ≥ 5.x. Native window, not a browser tab. Target dimensions 1400×900, resizable, minimum 900×600.
- **Backend:** the existing FastAPI app, booted programmatically via `uvicorn.Server` on a free local port. No separate subprocess.
- **Mode:** single-user per install. No login screen, no account switching, no sync mechanism. Data isolation is by DATA_DIR, not by in-app auth.
- **Platform feature gate:** Outlook integration is **disabled** on Windows. Buttons, settings, and sync entrypoints that depend on AppleScript are hidden when `not outlook_available()`.
- **Distribution:** `.dmg` for macOS, `.msi` for Windows. No auto-update. No code signing in v1.
- **Coexistence with Grant's dev install:** the packaged Mac app reads/writes the same `~/.policydb/` directory as the Python dev install by default. Grant can flip freely between the two.

### New module — `src/policydb/paths.py`

```python
"""Platform-aware paths for PolicyDB. Used by both dev (CLI) and packaged (desktop) runs."""
import os
import sys
from pathlib import Path

def data_dir() -> Path:
    """Return the per-install data root. Creates if missing."""
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "PolicyDB"
    else:
        root = Path.home() / ".policydb"
    root.mkdir(parents=True, exist_ok=True)
    return root

DATA_DIR: Path = data_dir()

def db_path() -> Path:
    return DATA_DIR / "policydb.sqlite"

def config_path() -> Path:
    return DATA_DIR / "config.yaml"

def outlook_available() -> bool:
    """True when the current platform supports the Outlook AppleScript bridge."""
    return sys.platform == "darwin"
```

**Call-site edits:**
- `src/policydb/db.py` — replace the `DB_DIR` constant with `from policydb.paths import DATA_DIR, db_path`.
- `src/policydb/config.py` — replace hardcoded `~/.policydb/config.yaml` with `from policydb.paths import config_path`.
- `src/policydb/web/routes/outlook.py` (and any other Outlook callers) — gate entry handlers on `outlook_available()`; return HTTP 404 on Windows, include "Outlook integration is macOS only" message.
- `src/policydb/web/app.py` — register `outlook_available` as a Jinja global so templates can hide buttons with `{% if outlook_available %}...{% endif %}`.

### New module — `src/policydb/desktop.py`

Entry point used only when the binary is packaged (`sys.frozen` is True). Flow:

1. **First-launch silent migration** (detail below) — runs before `init_db()` so a migrated DB picks up any pending schema migrations on the way in.
2. `init_db()` — idempotent; runs schema migrations on first launch and every launch thereafter.
3. **Onboarding check:** if the DB has zero clients and zero contacts, redirect to `/onboarding` (see below).
4. Pick a free port via `socket.socket(AF_INET).bind(('127.0.0.1', 0))`.
5. Start uvicorn in a daemon thread with the FastAPI app, bound to that port.
6. Health-check loop (retry `GET /healthz` up to 5s); fail fast on timeout with a native error dialog.
7. Open a pywebview window pointing at `http://127.0.0.1:<port>/action-center?tab=today`. Window title "PolicyDB".
8. On window close, call `uvicorn.Server.should_exit = True` and join the thread. Return 0.

### First-launch silent migration (detailed)

Runs exactly once per install. Gated by a sentinel file `DATA_DIR / .migrated_from_old`; the sentinel is written on first successful migration (or first successful no-op), so subsequent launches skip the entire block:

```
sentinel = DATA_DIR / ".migrated_from_old"
if not sentinel.exists():
    legacy = Path.home() / ".policydb"
    if legacy != DATA_DIR and legacy.exists() and any(legacy.iterdir()):
        shutil.copytree(legacy, DATA_DIR, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".DS_Store", "*.lock", "*.sqlite-journal"))
    sentinel.write_text(f"migrated_at={datetime.now().isoformat()}\n")
```

Behavior by platform:
- **Mac (Grant's packaged build):** `DATA_DIR == legacy == ~/.policydb/`, so the `legacy != DATA_DIR` guard fires and the copy is skipped. Sentinel still written. Grant's existing data is already visible because the paths match.
- **Windows (Mark's fresh install):** `DATA_DIR = %APPDATA%/PolicyDB/`, `legacy = ~/.policydb/` which does not exist on Windows. Copy skipped; sentinel written.
- **Future Grant-on-Windows** (out of v1 scope, hook pre-wired): if someone manually seeds `~/.policydb/` on a Windows box before first launch, the copy would fire. Not exercised in v1 but the code path exists.

No prompt. No friction. Success/no-op both logged to `DATA_DIR/first_launch.log`.

### Onboarding — `GET /onboarding`

New route + template. Single-screen form (no multi-step wizard):

- Full name (required)
- Email (required, validated via existing `clean_email()` in `utils.py`)
- "Import existing clients from CSV" — optional file upload, accepts the current importer format
- Submit button text: "Get started"

Handler:
- Saves `user_name`, `user_email` to config
- If CSV provided, runs the existing `importer.import_clients_csv()` path and captures counts into a flash banner
- Sets sensible defaults for `renewal_statuses`, `opportunity_statuses`, `policy_types`, `activity_types` if the corresponding config lists are empty (these already have `_DEFAULTS` entries in `config.py` — onboarding just ensures they're materialized into `config.yaml`)
- Redirects to `/action-center?tab=today`

Onboarding is shown exactly once (guarded by "is there any meaningful content in the DB?" check, same as the launcher uses).

### Packaging pipeline — new `packaging/` directory

- `packaging/build.py` — single-entry build script. Usage: `python packaging/build.py --platform mac|win|both`. Calls `pyinstaller packaging/policydb.spec` with the right OS flags, then post-processes (codesign Mac if cert available else skip; wrap Windows output in an MSI via WiX Toolset if available else produce an unpackaged `dist/` folder).
- `packaging/policydb.spec` — PyInstaller `--onedir` spec. Includes:
  - `src/policydb/` (full tree)
  - `src/policydb/migrations/*.sql`
  - `src/policydb/web/templates/**`
  - `src/policydb/web/static/**`
  - `src/policydb/data/**` (default config fragments, example CSVs)
  - Hidden imports: `uvicorn.workers`, `jinja2.ext`, `sqlite3`, `phonenumbers`, `rapidfuzz`, `dateparser`, `humanize`, `babel`
- `packaging/README.md` — build instructions including the iCloud escape hatch ("Run builds from `~/Developer/policydb` or any other non-iCloud path; PyInstaller's temp churn fights iCloud materialization").
- GitHub Actions:
  - `.github/workflows/package-mac.yml` — macos-14 runner; `python packaging/build.py --platform mac`; uploads `.dmg` as artifact.
  - `.github/workflows/package-win.yml` — windows-latest runner; same script; uploads `.msi` as artifact.

### Windows constraints

- **WebView2** runtime must be present for pywebview's Windows backend. The `.msi` ships with the WebView2 Evergreen Bootstrapper as a prerequisite check (runs silently if runtime is missing; downloads from Microsoft). Document in `packaging/README.md`.
- **Outlook** — the AppleScript bridge is dead on Windows. `outlook_available()` gates: Settings › Outlook panel hidden, Compose-via-Outlook buttons swapped for a plain `mailto:` fallback, email sync section removed from the Action Center menu.
- **Gatekeeper / SmartScreen** — unsigned v1. Document right-click → Open (Mac) and More info → Run anyway (Windows) in a post-install README bundled into the installer.

### Updates (v1)

No auto-update. Users check `/about` for the running version; if a newer version exists they download the installer manually. Auto-update is noted in a `project_desktop_auto_update.md` memory as a v2 candidate — out of scope here.

---

## Build Sequence

Each step is PR-able independently into `feat/multi-user-tasklist` (review-friendly slices). The order respects dependencies:

1. **paths.py + call-site refactor + Outlook feature gate** — pure refactor, no user-visible change. Verifies we can swap the data root without breaking anything. Includes a smoke test that `from policydb.paths import DATA_DIR` works on both platforms and that `db.py` + `config.py` pick up the new module.
2. **Today tab MVP** — `v_today_tasks` view, `GET /action-center?tab=today`, Tabulator grid with columns above, Add Task modal, complete checkbox, snooze menu, filter pills. Focus Queue still present and still the default in this PR (feature-flagged by query param only).
3. **Smart Suggestions panel** — `suggestions_only=True` mode on `build_focus_queue`, `/action-center/today/suggestions` partial, fast-capture `+`, greyed-out empty groups.
4. **Focus retirement** — delete `_focus_queue.html`, `_focus_item.html`, `_waiting_sidebar.html`. Flip default tab to `today`. Add `/action-center?tab=focus` → `tab=today` redirect. Add `/focus` → `/today` redirect. Add one-time sessionStorage migration. Reword Settings UI labels.
5. **Plan Week re-skin** — swap in shared Tabulator components, add `Plan Week →` entry point on Today toolbar. QA each spread/dismiss/escalate interaction. Zero behavior change.
6. **Desktop launcher (Mac smoke test)** — `desktop.py` + pywebview glue + first-launch migration hook. Manual QA: build locally, launch, verify window opens, click into Today and Plan Week, close and reopen.
7. **Windows build** — `packaging/policydb.spec`, `packaging/build.py`, GitHub Actions workflow. Produce a signed-eventually `.msi`. Smoke test on a clean Windows VM.
8. **Onboarding** — `/onboarding` route + template, first-launch detection in `desktop.py`, CSV import wiring.

PRs 1–5 can land on `main` as they complete; they're useful to Grant even before the packaged app ships. PRs 6–8 are the packaging bundle Mark needs.

---

## Risks

1. **iCloud-in-Documents fights PyInstaller.** The repo's primary dev path now lives at `~/Developer/policydb` (moved out of iCloud Documents in response to the 2026-04-18 deadlock). Future builds must originate from a non-iCloud path or PyInstaller's tempdir will thrash against iCloud materialization. Documented in `packaging/README.md`. See `feedback_icloud_deadlock.md`.
2. **Tabulator drift between Today and Plan Week.** Both grids share components; if one diverges, visual consistency breaks. Mitigation: extract shared column renderers and base config into `src/policydb/web/static/js/tabulator_today.js` and import from both grid initializers.
3. **WebView2 dependency on Windows.** Installer must include the Evergreen bootstrapper and surface a useful error ("PolicyDB needs WebView2 — click here to install") if the runtime check fails post-install.
4. **Code signing deferred.** First-launch warnings on both platforms. Document the manual approval steps in the bundled README and on a `/help/first-launch` page shown once post-onboarding.
5. **Plan Week re-skin regression.** Spread, dismiss, and escalate are multi-row operations with side effects (workload rebalancing, activity-log inserts). Visual refactor mustn't touch the data paths. Regression-test each action manually in Chrome + Playwright smoke after the re-skin PR.
6. **`focus_queue.py` module name kept despite no Focus tab.** Future reader may be confused by a `focus_queue.py` that doesn't serve a Focus Queue. Mitigated by the module docstring ("Originally built for the Focus Queue tab; now powers Smart Suggestions") and a short note in `CLAUDE.md` under the Architecture Patterns section.
7. **Suggestion/task duplicate prevention relies on `v_today_tasks` id match.** If a suggestion's underlying `activity_log.id` changes (e.g., dedup pass merges rows), a suggestion could reappear in the panel after the user already captured it. Mitigation: dedup pass runs *before* the suggestions filter, and `build_focus_queue(suggestions_only=True)` re-queries `v_today_tasks` inside its final step rather than caching.
8. **`outlook_available()` gating forgotten somewhere.** Any template that references `outlook.py` routes without the Jinja guard will 404 on Windows. Audit via grep for `outlook` in templates after step 1.

---

## Non-negotiables

Pulled forward so reviewers can hold the implementation to these:

- **Touch-once.** Today tab reads from `activity_log` via `v_today_tasks`. Add Task writes to `activity_log`. Suggestions → fast capture writes to `activity_log`. Complete/snooze writes to `activity_log`. No parallel task table. See `feedback_touch_once_data_flow.md`.
- **No hardcoded lists.** Any new option set (e.g., snooze presets if they become configurable) goes through `_DEFAULTS` in `config.py` + `EDITABLE_LISTS` in `settings.py`. See `feedback_config_editable_lists.md`.
- **Density stays dense.** `user_add_focus.md` advocates focus-minimal layouts in general; Grant explicitly pivoted to Layout E (dense) for this redesign. Do not soften row height, padding, or typography density.
- **Config keys preserved.** `focus_score_weights`, `focus_auto_promote_days`, `focus_nudge_alert_days` stay — only their Settings UI labels change.
- **Windows is not an afterthought.** Every ripple (Outlook gating, WebView2, DATA_DIR, signing) is owned by the packaging PR set, not retrofitted later.

---

## Reference material

- Visual mockup (Layout E, selected): `.superpowers/brainstorm/20920-1776515539/content/today-layout-dense.html` (a duplicate copy exists at `.superpowers/brainstorm/27004-1776517917/content/today-layout-dense.html`).
- Brainstorm summary memory: `project_multi_user_tasklist_brainstorm.md`.
- Related memories: `user_add_focus.md` (density caveat), `feedback_touch_once_data_flow.md`, `feedback_icloud_deadlock.md`, `project_outlook_integration.md`, `project_tui_build.md`.
- Relevant skills: `policydb-design-system` (color tokens, typography, chip/button/pill conventions — the Visual Refinements section above leans heavily on this), `policydb-spreadsheet` (Tabulator), `policydb-activities` (follow-up lifecycle), `policydb-route-patterns` (literals-first, HTMX row pattern).

---

## Open questions

None — all design decisions were locked during the 2026-04-18 brainstorm prior to spec write. If the reviewer surfaces a question, treat it as a spec gap to patch, not a re-opened decision.
