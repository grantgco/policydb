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
| activities.py | /activities, /followups, /renewals | Follow-ups, activities, renewal pipeline |
| settings.py | /settings | Config list management, email subjects |
| templates.py | /templates | Email template CRUD + compose panel |
| reconcile.py | /reconcile | Statement reconciliation |

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

### Opportunities
Policies with `is_opportunity=1` are excluded from:
- Renewal pipeline, suggested follow-ups, stale renewal alerts
- Client summary policy counts
- All views use `AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)`

Opportunities have optional dates/carrier; the "Convert to Policy" flow sets real dates and clears the flag.

### Renewal Status Exclusion
`renewal_statuses_excluded` config key stores statuses silenced from alerts. Pass `excluded_statuses=cfg.get("renewal_statuses_excluded", [])` to `get_renewal_pipeline()`, `get_suggested_followups()`, and `get_stale_renewals()`.

---

## Email Template System

### Token Rendering
- **Module:** `src/policydb/email_templates.py`
- `render_tokens(template_text, context_dict)` — replaces `{{token}}` placeholders
- `policy_context(conn, policy_uid)` — builds token dict for policy context
- `client_context(conn, client_id)` — builds token dict for client context
- `followup_context(row_dict)` — builds token dict for follow-up rows
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

### Input Pattern Hierarchy

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

## Development Notes

- Always pass `renewal_statuses` to any template that renders `_status_badge.html`
- Always call `_attach_milestone_progress(conn, rows)` before passing pipeline rows to templates that show checklist progress
- `_attach_client_ids(conn, rows)` adds `client_id` to pipeline rows for linking
- Phone formatting: always call `format_phone()` from `src/policydb/utils.py` when saving phone fields
- SQLite migrations are one-way; use `ALTER TABLE ... ADD COLUMN` and never remove columns
