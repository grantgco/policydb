# PolicyDB — Claude Code Instructions

## Project Overview
PolicyDB is a local FastAPI + SQLite insurance book-of-business management tool. It runs as a local web server (`policydb serve`) at `http://127.0.0.1:8000`. The UI is server-rendered Jinja2 with HTMX for inline partial updates — no frontend build step, no JS framework.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Web framework | FastAPI + uvicorn |
| Templates | Jinja2 (in `src/policydb/web/templates/`) |
| Interactivity | HTMX (partial HTML swaps) |
| Styling | Tailwind CSS (CDN, utility classes only) |
| Database | SQLite via `sqlite3` with `row_factory`, WAL mode |
| CLI | Click (`policydb` / `pdb` entry points) |
| Parsing | **Humanize, Dateparser, RapidFuzz, Babel** — use these; do not write custom parsing code |
| Phone formatting | `phonenumbers` library via `format_phone()` in `src/policydb/utils.py` |
| Currency parsing | `parse_currency_with_magnitude()` in `src/policydb/utils.py` — supports shorthand like `1m`, `1.5M`, `500k`, `$2,000,000` |

### Currency Shorthand Parsing

**Every money/currency input field** (limits, deductibles, premiums, retentions, etc.) MUST use `parse_currency_with_magnitude()` from `src/policydb/utils.py` when saving. This function supports shorthand notation:

| Input | Parsed Value |
|-------|-------------|
| `1m` or `1M` | 1,000,000 |
| `1.5m` | 1,500,000 |
| `500k` | 500,000 |
| `$2,000,000` | 2,000,000 |
| `25000` | 25,000 |

**Never use raw `float()` for currency fields.** Always import and use `parse_currency_with_magnitude` to ensure consistent shorthand support across the entire platform.

---

## Database & Migrations

- DB path: `~/.policydb/policydb.sqlite`
- Config path: `~/.policydb/config.yaml`
- Migrations: `src/policydb/migrations/NNN_description.sql` — sequentially numbered
- Migration runner: `src/policydb/db.py` — `init_db()` runs all migrations and rebuilds views on every server start
- Views are **always dropped and recreated** on startup — never reference non-existent columns in view SQL
- Current migration count: ~022

### Key Tables
- `clients` — name, industry, contacts, account exec, scratchpad
- `policies` — all policy fields including `is_opportunity`, `first_named_insured`, `renewal_status`, `placement_colleague`, `underwriter_name`
- `client_contacts` — contact_type ('client' or 'internal'), phone, mobile, role, notes
- `policy_contacts` — policy-specific contacts (placement colleagues, underwriters)
- `activity_log` — activities, follow-ups, notes
- `policy_milestones` — checklist items per policy
- `email_templates` — user-managed email form letters with `{{token}}` placeholders
- `user_notes` — global dashboard scratchpad (id=1)
- `client_scratchpad` — per-client freeform notes

### Key Views (in `src/policydb/views.py`)
- `v_policy_status` — all active non-opportunity policies with urgency, days_to_renewal
- `v_renewal_pipeline` — policies within renewal window (180d default), excludes opportunities
- `v_client_summary` — aggregate stats per client, excludes opportunities from counts
- `v_schedule` — schedule of insurance view
- `v_tower` — tower/layering view
- `v_overdue_followups` — follow-ups past due date

**Important:** `milestone_done`/`milestone_total` are NOT columns in any table or view — they are computed at Python runtime by `_attach_milestone_progress()` in `src/policydb/web/routes/policies.py`.

---

## Architecture Patterns

### Route Structure
Each route module is in `src/policydb/web/routes/`. Routers registered in `src/policydb/web/app.py`.

| Module | Prefix | Purpose |
|--------|--------|---------|
| dashboard.py | / | Dashboard, search, pipeline partial |
| clients.py | /clients | Client CRUD, contacts, team |
| policies.py | /policies | Policy CRUD, row edit, quick log, inline forms |
| action_center.py | /action-center | Unified tabbed page: Follow-ups, Inbox, Activities, Scratchpads |
| activities.py | /followups/plan, /renewals | Plan Week, renewal pipeline, activity PATCH |
| settings.py | /settings | Config list management, email subjects |
| templates.py | /templates | Email template CRUD + compose panel |
| reconcile.py | /reconcile | Statement reconciliation |
| inbox.py | /inbox/* | Inbox capture, process, scratchpad process (redirects /inbox → Action Center) |

**Note:** `/inbox`, `/followups`, and `/activities` all redirect to `/action-center?tab=...`. The Action Center is the primary UI for daily work management.

### HTMX Row Edit Pattern
Every pipeline/table view has three endpoint variants per row:
- `GET /{uid}/row/edit` → inline edit form (replaces `#row-{uid}`)
- `POST /{uid}/row/edit` → saves, returns display row
- `GET /{uid}/row` → restore display row (Cancel button target)
- `GET /{uid}/row/log` → inline activity log form
- `POST /{uid}/row/log` → saves activity, restores display row

Variants exist for: `row` (client detail), `dash` (dashboard), `renew` (renewals page).

### Inline Status Badge
`src/policydb/web/templates/policies/_status_badge.html` — renders a `<select>` that auto-saves status via HTMX POST to `/policies/{uid}/status`. Needs `renewal_statuses` in template context.

### UID & Reference Tag System

**Policy UIDs:** Auto-generated sequential `POL-001`, `POL-002`, etc. via `next_policy_uid()` in `db.py`. Separate from `policy_number` (carrier's external number).

**Client Numbers:** `cn_number` on `clients` table — external account number from AMS. Used as root of ref tag hierarchy. Fallback: `C{client_id}`.

**Reference Tags:** Built by `build_ref_tag()` in `utils.py`. Hierarchical format: `CN{number}-L{project_id}-{policy_uid}`. Registered as Jinja2 global in `app.py`.

**Copy format:** `copyRefTag()` in `base.html` wraps with `[PDB:...]` for Outlook search distinctiveness. Clicking any ref tag pill copies `[PDB:CN123456789-POL042]` to clipboard.

**Ref tag pill partial:** `_ref_tag_pill.html` — reusable component. Usage:
```jinja2
{% set _ref = build_ref_tag(cn_number=..., client_id=..., policy_uid=..., project_id=...) %}
{% with ref_tag=_ref %}{% include "_ref_tag_pill.html" %}{% endwith %}
```

**Copy depth for emails:** Client + Location + Policy only — no activity/thread suffixes in email tags. Deeper suffixes (`-A{id}`, `-RFI{nn}`) are for internal PolicyDB linking only.

**Activity Timeline:** Activities on the policy Contacts tab are auto-clustered by time proximity (`activity_cluster_days` config, default 7 days). Display-only grouping — no data model. Replaces the old COR correspondence threading (manual `thread_id` approach removed).

### Opportunities
Policies with `is_opportunity=1` are excluded from:
- Renewal pipeline, suggested follow-ups, stale renewal alerts
- Client summary policy counts
- All views use `AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)`

Opportunities have optional dates/carrier; the "Convert to Policy" flow sets real dates and clears the flag.

### Renewal Status Exclusion
`renewal_statuses_excluded` config key stores statuses silenced from alerts. Pass `excluded_statuses=cfg.get("renewal_statuses_excluded", [])` to `get_renewal_pipeline()`, `get_suggested_followups()`, and `get_stale_renewals()`.

### Timeline Engine
`src/policydb/timeline_engine.py` — proactive workflow engine that tracks ideal vs projected dates per policy milestone.

**Key functions:**
- `generate_policy_timelines(conn, policy_uid=None)` — generates timeline rows from milestone profiles. Called on startup. Pass `policy_uid` to regenerate a single policy.
- `get_policy_timeline(conn, policy_uid)` — returns all timeline rows ordered by ideal_date
- `compute_health(...)` — computes milestone health: `on_track` → `drifting` → `compressed` → `at_risk` → `critical`
- `recalculate_downstream(conn, policy_uid, changed_milestone, new_projected, expiration_date)` — shifts downstream dates when a milestone slips
- `update_timeline_from_followup(conn, policy_uid, milestone_name, disposition, new_followup_date, waiting_on)` — updates accountability + triggers recalc on re-diary
- `complete_timeline_milestone(conn, policy_uid, milestone_name)` — marks milestone done, syncs to checklist

**Schema:** `policy_timeline` table (migration 070) with `ideal_date`, `projected_date`, `completed_date`, `prep_alert_date`, `accountability`, `waiting_on`, `health`, `acknowledged`, `acknowledged_at`. Policies have `milestone_profile` column.

**Accountability states:** `my_action` (your action needed), `waiting_external` (ball in someone else's court), `scheduled` (meeting/call booked). Derived from disposition config.

**Milestone profiles:** `Full Renewal`, `Standard Renewal`, `Simple Renewal` — configurable in Settings. Each profile selects which milestones from `renewal_milestones` apply. Auto-suggest by premium threshold.

**Action Center integration:** Follow-ups tab restructured into 5 sections: Act Now, Nudge Due, Prep Coming Up, Watching, Scheduled. Portfolio health sidebar widget. Risk alerts banner with acknowledge.

**Programs:** Timeline milestones live at the program level. Child policies (those with `program_id`) are excluded from timeline generation and from the review queue. Reviewing a program cascades `last_reviewed_at` to all children.

---

## Logging & Audit System

### Application Logging
- **Module:** `src/policydb/logging_config.py` — `setup_logging()` + `setup_sqlite_handler()`
- **File handler:** `~/.policydb/logs/policydb.log` — RotatingFileHandler (5MB x 5 files), level from `cfg.get("log_level")`
- **SQLite handler:** Background writer thread inserts into `app_log` table (flushes every 5s or 50 entries)
- **Request middleware:** `app.py` logs every non-static HTTP request (method, path, status, duration_ms) at INFO/WARNING/ERROR based on status code
- **Business events:** Lightweight `logger.info()` calls in `policies.py`, `clients.py`, `activities.py`, `reconcile.py`, `inbox.py`

### Audit Log (Database Triggers)
- **Migration 067:** SQLite triggers on 7 tables (clients, policies, activity_log, contacts, inbox, policy_milestones, saved_notes)
- Captures INSERT/UPDATE/DELETE with JSON old_values/new_values

### Auto-Purge
- `_purge_old_logs()` in `db.py` runs on every server startup after health checks
- Deletes `audit_log` and `app_log` rows older than `log_retention_days` config (default: 730 = 2 years)
- VACUUM only on large purges (>10,000 rows)

### Logs UI
- **Route:** `/logs` — tabbed page (App Log / Audit Log), lazy-loaded via HTMX
- **Old URL:** `/settings/audit-log` redirects to `/logs?tab=audit`
- **Config keys:** `log_level` (default INFO), `log_retention_days` (default 730)

---

## Email Template System

### Token Rendering
- **Module:** `src/policydb/email_templates.py`
- `render_tokens(template_text, context_dict)` — replaces `{{token}}` placeholders
- `policy_context(conn, policy_uid)` — builds token dict for policy context
- `client_context(conn, client_id)` — builds token dict for client context
- `followup_context(row_dict)` — builds token dict for follow-up rows
- `timeline_context(conn, policy_uid)` — builds token dict for timeline data (drift, blocking reason, milestones)
- `CONTEXT_TOKENS` — dict of `{context: [(key, label), ...]}` pairs used to build pill toolbars

### Critical Rule: New Fields → Add to Tokens
**Every time a new field is added to policies, clients, or related tables, it must also be added to:**
1. The relevant `*_context()` function in `email_templates.py`
2. The `CONTEXT_TOKENS` dict in the same file (under the correct context key)

This makes the field available as a clickable token pill in the template builder at `/templates`.

### Compose Panel
Triggered from policy edit page, client detail page, and follow-ups page via a `<details>` element. The HTMX trigger pattern is:
```html
hx-trigger="toggle from:#compose-panel-id once"
```
**Do NOT use `toggle[open]`** — the `[open]` filter evaluates on the `<div>` (not the `<details>`), which is always falsy and the request never fires.

---

## JavaScript in Jinja2 Templates

**Critical:** Jinja2 processes `{{ }}` everywhere including inside `<script>` blocks. Never write `'{{' + jsVar + '}}'` — Jinja2 treats the `{{` as a template expression.

**Wrong:** `var insert = '{{' + token + '}}';`
**Correct:** `var insert = '{' + '{' + token + '}' + '}';`

The same issue applies anywhere `{{` or `}}` appears inside JavaScript string literals in Jinja2 templates.

---

## Config System

`src/policydb/config.py` — `_DEFAULTS` dict merged with `~/.policydb/config.yaml`.

Key config lists managed in Settings UI (`/settings`):
- `renewal_statuses` — status dropdown options
- `renewal_statuses_excluded` — statuses silenced from pipeline/alerts
- `opportunity_statuses` — opportunity stage options
- `policy_types`, `carriers`, `activity_types`, `renewal_milestones`, etc.
- `email_subject_policy/client/followup` — mailto subject templates with `{{tokens}}`

`cfg.get(key, default)`, `cfg.add_list_item()`, `cfg.remove_list_item()`, `cfg.save_config()` are the main API.

**Critical rule: No hardcoded lists.** All categorized lists (dropdowns, prompt categories, endorsement types, coverage categories, etc.) MUST be stored in `_DEFAULTS` in config.py and editable via the Settings UI. Never hardcode lists in Python code — always read from `cfg.get("key_name")` at runtime.

---

## Reconciler

`src/policydb/reconciler.py` — matches imported statement/renewal list rows to existing policies using additive scoring.

### Scoring Algorithm (`_score_pair()`)

Additive scoring with **no hard gates** — every signal contributes independently:

| Signal | Max Points | Details |
|--------|-----------|---------|
| Policy Number | 40 | Exact normalized = 40, fuzzy >= 90 = 32, >= 75 = 20, missing = 0 (neutral) |
| Dates (eff + exp) | 30 | Split 15+15. Exact = 15, <= 14d = 12, <= 45d = 8, same year = 4 |
| Policy Type | 15 | Normalized match = 15, fuzzy >= 85 = 12, >= 70 = 8 |
| Carrier | 10 | Normalized match = 10, fuzzy >= 80 = 7, >= 60 = 4 |
| Client Name | 5 | Normalized match = 5, fuzzy >= 80 = 4, >= 60 = 2 |

**Confidence tiers:** high >= 75, medium >= 45, low < 45

**Important rules:**
- **No hard gates** — no single field can block a match
- **Railroad Protective Liability** is a distinct policy type — do NOT alias it to General Liability
- `_score_pair()` must track diffs at **both** levels: `diff_fields` (real differences) AND `cosmetic_diffs` (same after normalization) — both need UI update controls
- FNI cross-matching: checks all combinations of ext client/FNI vs db client/FNI, takes best score
- Single-client mode: auto-max name score to 5

### Normalization (Two Categories)

**Display/save functions** (write to DB):
- `normalize_client_name()` — preserves legal suffixes, title case
- `normalize_policy_number()` — uppercase + trim, preserves formatting
- `normalize_coverage_type()` — alias map → canonical name
- `normalize_carrier()` — config-driven alias → canonical carrier

**Matching functions** (comparison only, never write to DB):
- `normalize_client_name_for_matching()` — strips legal suffixes entirely
- `normalize_policy_number_for_matching()` — strips all formatting + filters placeholders

### Coverage Aliases

- `_COVERAGE_ALIASES` in `utils.py` — hardcoded base aliases (~237 entries)
- `coverage_aliases` in config.yaml — user-learned aliases (merged via `rebuild_coverage_aliases()`)
- `carrier_aliases` in config.yaml — carrier name mappings (merged via `rebuild_carrier_aliases()`)

### Reconcile UI (Pairing Board)

The reconcile page uses the **Pairing Board pattern** (see below). Flow:
1. Upload CSV/XLSX → column mapping
2. **Validation panel** — pre-match data quality check with auto-learn aliases
3. **Pairing board** — side-by-side with drag-to-match, score breakdowns, field-level diff accept/fill
4. Confirm pairs → export XLSX

Key endpoints: `/reconcile`, `/reconcile/run-match`, `/reconcile/confirm/{idx}`, `/reconcile/break/{idx}`, `/reconcile/manual-pair`, `/reconcile/search-coverage`, `/reconcile/apply-field/{uid}`

### Location Assignment Board

`/clients/{id}/locations` — same pairing board pattern for assigning policies to physical locations. Drag policies to location groups, smart suggestions from shared `exposure_address`.

---

## Importer

`src/policydb/importer.py` — accepts CSV/Excel. Column aliases map alternative header names to canonical field names. When new fields are added to the schema, add aliases here too.

---

## UI Implementation Standards

### Core UI Defaults (Design Decisions)

These are standing decisions that apply across the entire application. Do not deviate without explicit user approval.

| Decision | Default | Notes |
|----------|---------|-------|
| **Page layout for detail/edit pages** | Tabbed (4 tabs per page), lazy-loaded via HTMX | Client page and policy page both use tabs |
| **Tab loading** | Lazy-load each tab on first click | Active tab loads on page render; others on demand |
| **Tab persistence** | sessionStorage remembers last tab per page | Returning to a page opens the last-used tab |
| **Default tab** | Always first tab (Overview/Details) | No context-aware routing from entry point |
| **Save behavior** | Per-field PATCH on blur — no Save button | Every field saves individually when focus leaves. Toast confirms. No form POST. |
| **Field style** | Contenteditable + combobox everywhere | ALL edit fields use contenteditable text or combobox pattern, not `<input>` boxes |
| **Form sections** | All open by default | No collapsed `<details>` on detail/edit pages — everything visible |
| **Sidebar** | Sticky right sidebar on client page | Key Dates + Quick Actions always visible. Independent scroll. |
| **Summary cards** | Condensed to one compact horizontal bar | Not 6 separate cards — single row with key stats |
| **Policy drill-down from client** | Quick-edit popover (status, follow-up, premium, checklist) | Not inline row expand. "Open →" link for full page. |
| **Working Notes** | Floating panel accessible from any tab | Not locked to one tab — always available |
| **Contacts on policy page** | Editable inline (matrix pattern) | Not read-only. Full add/edit/remove capability. |
| **Checklist/RFIs** | Both pages — summary on client, detail on policy | Per-policy checklist items, aggregate progress on client |

### Input Pattern Hierarchy

**Default:** ALL data entry fields across the app should use the `contenteditable` + combobox pattern with per-field PATCH saves on blur. This is the universal standard — not just for tables but for ALL edit pages including policy edit and client edit. Traditional `<input>` boxes with form POST are being phased out.

When implementing any data entry field, form element, or interactive control — whether adding a new feature or modifying an existing one — prefer modern contextual inputs over default browser form elements. Select the pattern that best matches the data type and interaction context:

| Field Type | Preferred Pattern | Avoid |
|---|---|---|
| Names, notes, freeform text in a table | `contenteditable` cell | `<input>` inside `<td>` |
| Single-field edits in a detail view | Click-to-edit (display → input on click) | Always-visible input |
| Carrier, industry, line of business | Combobox with filtered dropdown | `<select>` dropdown |
| Multiple values (coverages, markets, tags) | Pill/tag input (Enter to add, × to remove) | Multi-select `<select>` or checkboxes |
| Boolean flags (active, bound, auto-renew) | CSS toggle switch | `<input type="checkbox">` |
| 2–5 mutually exclusive options (status, view) | Segmented control (pill button group) | `<select>` or radio buttons |
| Dates (eff, exp, renewal) | `<input type="date">` styled to match UI | Plain text input |
| Limits, retentions, round-number values | Stepper with +/− buttons | Plain `<input type="number">` |
| Row ordering / prioritization | Drag-to-reorder with ⠿ handle (HTML5 draggable or SortableJS) | Manual order fields |
| Cross-record navigation | Command palette (⌘K, search + filter) | Sidebar lists only |

### Contenteditable Tables

When building or modifying any tabular data view (policy schedules, client lists, activity logs):

- Cells should appear as static text by default; editable on click
- Focused cell gets a bottom border highlight in brand color — no full input box border
- `Tab` advances to next cell; `Tab` on last cell appends a blank row
- Empty cells show placeholder text via `data-placeholder` and `::before` CSS
- Save on `blur` via `fetch` PUT/PATCH to the relevant API endpoint
- New rows POST to the API and store the returned `id` as `data-id` on the `<tr>`
- An `+ Add row` button below the table appends a blank row and focuses the first cell
- The add-row button carries a `no-print` class and is hidden in `@media print`

### General UI Principles

- **No always-visible input boxes** in table rows or detail views unless the field is a primary action (e.g. a search bar or a command palette)
- **No `<select>` elements** for fields where the user might type — use a combobox instead
- **No raw checkboxes** for boolean status fields visible in the main UI — use a toggle switch
- **Keyboard navigable** — every interactive element must be reachable and operable by keyboard; document any custom shortcuts in a visible tooltip or help section
- **Save behavior**: table cells and click-to-edit fields save on `blur` or `Enter`; destructive changes (delete, status change) confirm via an inline prompt, not a browser `alert()`
- **Error states**: invalid input shows a red border and an inline message below the field — never a browser `alert()` or `console.error()` only
- **Print safety**: any UI control (buttons, toggles, add-row links, tooltips) that should not appear in printed output carries the class `no-print`, and the stylesheet includes `@media print { .no-print { display: none; } }`

### Server-Side Parsing & Visual Feedback

When a cell value is saved via PATCH, the server may clean or reformat the input (phone formatting, email normalization). The response must return the formatted value so the UI can update the cell and signal the change to the user.

**PATCH response format:**
```json
{"ok": true, "formatted": "(512) 555-1234"}
```

**Frontend pattern:**
1. On blur, send the raw cell text to the PATCH endpoint
2. On success, compare `data.formatted` to the raw value that was sent
3. If they differ, update the cell text **and** flash the cell green (800ms fade) to indicate the server cleaned/formatted the input
4. If they match, update silently (no flash needed)

**`flashCell` helper** (used in all matrix controllers):
```javascript
function flashCell(el) {
  el.style.transition = 'background-color 0.3s ease';
  el.style.backgroundColor = '#d1fae5';
  setTimeout(function() {
    el.style.backgroundColor = '';
    setTimeout(function() { el.style.transition = ''; }, 300);
  }, 800);
}
```

**Fields that trigger server-side formatting:**

| Field | Function | Example |
|-------|----------|---------|
| Phone, Mobile | `format_phone()` from `src/policydb/utils.py` | `5125551234` → `(512) 555-1234` |
| Email | `clean_email()` from `src/policydb/utils.py` | `Jane <jane@co.com>` → `jane@co.com` |

**Rules for new PATCH cell-save endpoints:**
- Always run `format_phone()` on phone/mobile fields before saving
- Always run `clean_email()` on email fields before saving
- Always return `{"ok": true, "formatted": "..."}` with the post-processing value
- The JS callback must update `cell.textContent` (or rebuild the display HTML for email links) with the `formatted` value and call `flashCell()` when it differs from the raw input

### Jinja2 `tojson` in HTML Attributes

Never use `{{ list | tojson | e }}` inside double-quoted HTML attributes. With autoescape enabled, `tojson` returns `Markup` (safe), so `| e` is a no-op — raw JSON double quotes break the attribute delimiter.

**Correct:** Use single-quote attribute delimiters with `| tojson` alone:
```html
data-options='{{ items | tojson }}'
```
`tojson` auto-escapes `'` as `\u0027`, so single-quote delimiters are safe even for values containing apostrophes.

---

### Pairing Board Pattern

The **pairing board** is a reusable UI pattern for matching/comparing records from two sources side by side. Use it whenever records from one list need to be matched, paired, or assigned to records in another list.

**Structure:**
- **Left column**: Source records (upload rows, unassigned items)
- **Center column**: Score/status badge (clickable for breakdown)
- **Right column**: Target records (DB matches, locations, programs)
- **Action column**: Confirm / Break / Create buttons

**Implementation recipe:**
1. **Define two sides** — "source" rows and "target" rows
2. **Write a scoring function** — returns 0–100 with per-signal breakdown (use `_score_pair()` pattern from `reconciler.py`)
3. **Cache results server-side** with a UUID token (in-memory dict, 1-hour TTL)
4. **Create 4 row templates** — paired row, unmatched-source row, extra-target row, score breakdown
5. **Wire HTMX endpoints** — confirm, break, pair, create (each returns one row HTML + OOB counter updates)
6. **Add drag-drop** — `draggable="true"` on extras, drop zone on unmatched, `htmx.ajax()` on drop (~40 lines JS)
7. **Filter tabs** — client-side toggle on `data-status` attributes, no server round-trip

**Color conventions:**
- Green (`bg-green-50`): high-confidence pair (score >= 75)
- Amber (`bg-amber-50`): medium-confidence pair (score 45–74)
- Red (`bg-red-50`): unmatched source row
- Purple (`bg-indigo-50`): extra target row (draggable)

**OOB counter pattern:** Every action endpoint returns the updated row HTML plus `<div id="board-counters" hx-swap-oob="true">` with updated counts.

**Current implementations:**
- `src/policydb/web/templates/reconcile/_pairing_board.html` — reconcile upload vs DB policies
- `src/policydb/web/templates/clients/_location_board.html` — policies vs locations (planned)

---

## Skills

- Risk analysis skill available at `.claude/skills/risk-analysis-skill/` — use for any client risk assessment, coverage strategy, or exposure analysis work.

---

## QA Testing Requirement

**After any change that impacts the UI** (template edits, route changes, new features, CSS changes), Claude MUST run a thorough QA test using the browser:
1. Navigate to the affected page(s) and take screenshots
2. Verify all elements render correctly — no overflow, no overlapping, no missing data
3. Test interactive elements — click buttons, fill forms, verify saves work
4. Check for regressions on related pages (e.g., changing a contact template → test contacts on client detail, contacts directory, and policy edit pages)
5. Document any visual UI bugs or functional issues found
6. Fix issues before committing, or log them in a bug report if deferred

This is not optional — UI changes without visual verification have repeatedly shipped broken layouts, invisible form fields, and non-functional buttons.

---

## Development Notes

- Always pass `renewal_statuses` to any template that renders `_status_badge.html`
- Always call `_attach_milestone_progress(conn, rows)` before passing pipeline rows to templates that show checklist progress
- `_attach_client_ids(conn, rows)` adds `client_id` to pipeline rows for linking
- Phone formatting: always call `format_phone()` from `src/policydb/utils.py` when saving phone fields
- SQLite migrations are one-way; use `ALTER TABLE ... ADD COLUMN` and never remove columns

### Lessons Learned (Bug Patterns to Avoid)

**1. Migration wiring:** Adding a `.sql` file to `migrations/` is NOT enough. Every migration MUST also be wired into `init_db()` in `db.py` with the version check + INSERT into `schema_version`. Forgetting this causes "no such table" errors at runtime.

**2. `<form>` inside `<tr>` is invalid HTML:** Browsers silently discard `<form>` elements that are direct children of `<tr>`. This causes Save buttons to silently do nothing. Solutions:
   - Move the `<form>` inside a `<td>` (valid HTML)
   - Use `hx-post` + `hx-include` on the button instead of wrapping in a form
   - Never use `<form class="contents">` inside `<tr>` — it doesn't work

**3. Currency display — never use `%g` format:** Python's `%g` produces scientific notation (`1e+06`) for large numbers. Always use:
   - `'{:,.0f}'.format(value)` for comma-separated integers (1,000,000)
   - `{{ value | currency }}` for dollar display ($1,000,000)
   - `{{ value | currency_short }}` for shorthand ($1.0M, $500K)
   - PATCH endpoints returning formatted values should use shorthand format

**4. `initMatrix()` combobox positioning:** Combobox dropdown uses `position: absolute`. The parent `<td>` MUST have `position: relative` (add Tailwind class `relative`) or the dropdown will span full page width.

**5. Config lists MUST be in Settings UI:** When adding new config lists to `_DEFAULTS` in `config.py`, ALSO add them to `EDITABLE_LISTS` in `settings.py` so users can manage them from the Settings page. Otherwise the list exists but is invisible/uneditable.

**6. Source-level scoping propagation:** When a `requirement_source` is scoped to a `project_id`, the compliance engine query must check BOTH the requirement's `project_id` AND the source's `project_id` to determine which locations see those requirements. A source scoped to Location B should not have its requirements inherited by Location A.

**7. `window.location.reload()` scroll jump:** Full page reloads reset scroll position. When a JS action triggers a reload, save `window.scrollY` to `sessionStorage` (with a page-specific key) before reload, and restore on page load. Always scope the storage key to prevent cross-page interference.

**8. NOT NULL constraints on blank row creation:** When creating blank/empty rows for rapid-entry patterns, save empty string `""` (not `None`) for NOT NULL text columns. Check the migration schema for which columns are NOT NULL before implementing add-row endpoints.

**9. `initMatrix()` add-row endpoint must return a single `<tr>`:** The `createNewRow` function in `base.html` POSTs to `addRowUrl` and appends the response HTML children to the `<tbody>`. If the endpoint returns the entire card/section HTML (`<div><table><tbody>...</tbody></table></div>`), the card markup gets appended INSIDE the tbody, causing overlapping renders. Always return just the `<tr>` row template from add-row endpoints used with `initMatrix()`.

**10. `initAtComplete()` must be called on dynamically-loaded inputs:** The `@` contact autocomplete only works on inputs that have `initAtComplete(input, hiddenId)` called on them. HTMX-loaded tab content doesn't get this automatically — call it in a `<script>` block within the partial template after the input is rendered.

**11. `table-fixed` breaks with narrow viewports:** Using `table-fixed` with pixel-width `<col>` elements causes text to wrap character-by-character when the table is compressed. Prefer `table-layout: auto` with `min-width` on key columns and `whitespace-nowrap` on narrow columns.

**12. Scratchpads are working documents — activities are the record:** The platform philosophy is that scratchpads are ephemeral working spaces. When done, "Log as Activity" creates the permanent record. There is no separate "Saved Notes" layer — notes become activities. The `saved_notes` table exists but is legacy (data migrated to `activity_log` via migration 068).

**13. Sidebar responsive visibility:** Use `hidden xl:block` (not `lg:block`) for sidebars on pages with tabbed content. At `lg` (1024px) the sidebar overlaps tab content. `xl` (1280px) gives enough room for both.

**14. Worktree rebase stash conflicts with pycache:** When rebasing a worktree, `git stash pop` can fail on `.pyc` file conflicts. Drop the stash, `git checkout -- '**/__pycache__/'`, and re-run `pip install -e .` to regenerate.

**15. Jinja2 `loop.parent` does not exist:** Jinja2 has no `loop.parent` to access an outer loop's context from a nested loop. Capture the outer loop index into a `{% set %}` variable before entering the inner loop:
   - **Wrong:** `{{ loop.parent.loop.index0 }}`
   - **Correct:** `{% set outer_idx = loop.index0 %}` before the inner `{% for %}`, then use `{{ outer_idx }}`

**16. Config import pattern — `import policydb.config as cfg`:** The config module is imported as `import policydb.config as cfg`, then accessed via `cfg.get("key")`. Never use `from policydb.config import cfg` — there is no `cfg` object to import. The module itself IS the config interface.

**17. Config key names must match between Python and templates:** When a subagent or plan spec defines config keys (e.g., `conditions.min_premium`, `profile`), verify the actual config structure in `_DEFAULTS` before writing templates. Config keys drift between spec and implementation. Always read `config.py` to confirm the real key names before referencing them in Jinja2 templates.

**18. Milestone profiles use `renewal_milestones` names, not `mandated_activities` names:** The `milestone_profiles` config references milestone names from `renewal_milestones` (e.g., "Submission Sent", "Quote Received"). The `mandated_activities` config uses activity names (e.g., "RSM Meeting", "Market Submissions") which map to checklist milestones via the `checklist_milestone` field. When building UI for profiles, iterate over `renewal_milestones` for the pill list, not `mandated_activities`.

**19. Ref tag copy must use `build_ref_tag()`, never bare `policy_uid`:** Every copy-to-clipboard for a policy reference MUST use `build_ref_tag()` to build the full hierarchical tag (client + location + policy). Copying bare `policy_uid` (e.g., "POL-042") defeats the purpose — the ref tag needs the `CN{number}` prefix for Outlook searchability. The `copyRefTag()` JS function auto-wraps with `[PDB:...]` — always use it instead of direct `navigator.clipboard.writeText()`.

**20. `thread_id` column is legacy — do not write to it:** The `activity_log.thread_id` column exists for backwards compatibility but is no longer written to. New activities get `NULL` thread_id. The auto-clustered activity timeline (grouped by `activity_cluster_days` time gap) replaces the old manual COR correspondence threading. COR search in ref_lookup still works for old data.

**21. NEVER force-remove worktrees without checking for uncommitted work:** Before removing ANY worktree, run `git -C <worktree-path> status` to check for uncommitted changes. If there are uncommitted changes, STOP and ask the user. Never batch-remove worktrees. When user says "clean up" or "merge all", explicitly ask which branches are still actively being worked on. Assume worktrees have active work unless confirmed otherwise. This rule exists because force-removing an active worktree destroyed in-progress uncommitted work with no recovery path.

**22. SQLite handler must attach in the uvicorn worker process, not the CLI process:** When using `--reload` mode, `setup_logging()` in `cli.py` runs in the parent process, but uvicorn forks a new worker process. The SQLite logging handler must be attached via `@app.on_event("startup")` in `app.py` so it runs in the actual worker. Otherwise the handler's background writer thread and DB connection exist in the wrong process and no logs reach the `app_log` table.

**23. `logging.getLogger()` child loggers propagate automatically:** When you configure handlers on `logging.getLogger("policydb")`, all child loggers like `policydb.db`, `policydb.web.requests` automatically propagate up. No need to add handlers to each child logger — just use `logging.getLogger("policydb.module_name")` in each module and the root `policydb` logger's handlers capture everything.

**24. HTMX swap targets must match actual DOM element IDs:** When HTMX forms use `hx-target="#some-id"`, that element ID must actually exist in the DOM. A mismatch causes HTMX to silently discard the response — the server endpoint runs and saves correctly, but the UI never updates. Always verify `hx-target` IDs match real element IDs in the template. Additionally, when HTMX responses replace table rows or other interactive content, the response HTML must include the full interactive markup (contenteditable cells, selects, event handlers) — not a simplified read-only summary. Use Jinja2 partials (extracting the row loop into a separate `_rows.html` file) so the same template renders both the initial page load and HTMX swap responses. Finally, attach JS event listeners with an idempotent init function (guarded by a `_initialized` flag) and call it from both `DOMContentLoaded` and `htmx:afterSettle` so dynamically-inserted elements get wired up.

**25. Opportunities share policy infrastructure — don't exclude them without reason:** Opportunities are stored in the `policies` table with `is_opportunity=1` and have valid `policy_uid` values. Features that operate on `policy_uid` (RFI items, quick-add, policy-view partials, compose) work for opportunities automatically. When adding `AND (is_opportunity=0 OR is_opportunity IS NULL)` filters, ask whether the exclusion is intentional — seeding RFIs, linking items, and showing workflow views should include opportunities unless there's a specific reason not to.

**26. Action Center tab pattern is the canonical reference for HTMX tabs:** When adding tabbed navigation to any page, follow `action_center/page.html` — it implements `?tab=` query param support, sessionStorage persistence, HTMX lazy-loading of tab content, and server-side initial tab rendering to avoid loading flash. Reuse `initTabs()` from `base.html`.

**27. Redirect-after-POST must include `?tab=` when page uses tabs:** Any route that redirects back to a tabbed page (e.g., `RedirectResponse("/settings")`) must append `?tab=X` to preserve the user's tab context. Otherwise the redirect lands on the default tab, losing the user's place.

**28. HTMX `hx-target` with element IDs works across tabs:** Partials that use `hx-target="#list-{key}"` or similar ID-based targeting continue to work when moved into tab partials, because HTMX targets by element ID regardless of which tab container the element is in. No changes needed to existing partial targeting when refactoring to tabs.

**29. Pages with 10+ config sections need tabbed navigation, not long scroll:** A monolithic settings page with collapsible `<details>` sections doesn't scale — users with ADD especially struggle to find what they need. Group related settings into tabs with a search bar for cross-tab discovery. Complex editors get stacked cards (all open), simple lists get a 2-column grid within each tab.

**30. Worktree edits require `pip install -e .` for server visibility:** When working in a git worktree, template/code edits are NOT visible to the running `policydb serve` unless the package is installed in editable mode (`pip install -e .`) from the worktree directory. The server uses the installed package — if it was installed from the main repo, it reads templates from there, not the worktree. Always run `pip install -e .` from the worktree before starting the dev server.

**31. `fetch()` bypasses HTMX OOB swap processing:** When using `fetch()` + `innerHTML` instead of `htmx.ajax()` (e.g. to inspect HTTP status codes for error handling), `hx-swap-oob` attributes in the response HTML are NOT processed. You must manually extract OOB elements from the response, find their targets by ID, and swap their innerHTML before setting the main target content. Pattern:
```javascript
var temp = document.createElement('div');
temp.innerHTML = html;
temp.querySelectorAll('[hx-swap-oob]').forEach(function(el) {
    var target = document.getElementById(el.id);
    if (target) target.innerHTML = el.innerHTML;
    el.remove();
});
mainTarget.innerHTML = temp.innerHTML;
htmx.process(mainTarget);
```

**32. HTMX swap targets must exist before the response arrives:** When a slideover/panel triggers a `fetch()` that swaps content into a page element (e.g. `#ai-import-target`), the target element must have a stable ID in the page template. If the swap target is dynamically created or inside lazy-loaded content, verify it exists at swap time. Missing targets silently fail — the response HTML is fetched but never displayed.

**33. Slideover templates must be self-contained OR use a container — not both:** When building a right-side slideover panel loaded via HTMX, either (a) the template provides its own fixed-position panel + backdrop markup and the base template just has a simple target `<div>`, or (b) the base template provides the container/backdrop and the template renders only content. Mixing both creates nested fixed-position elements that fight each other. In PolicyDB, the compose slideover template (`_compose_slideover.html`) is self-contained — base.html has just `<div id="compose-slideover-body"></div>` as the HTMX target.

**34. Subagent template output must match route variable names exactly:** When a subagent creates Jinja2 templates, they may use different variable names than what the route endpoint passes (e.g., `primary_to` vs `primary_contact`, `available_templates` vs `templates`). Always verify template variable names match the route's `TemplateResponse` context dict after a subagent creates templates. Read both the route and template to confirm alignment before testing.
