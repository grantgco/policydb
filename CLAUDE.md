# PolicyDB — Claude Code Instructions

## Project Overview
PolicyDB is a local FastAPI + SQLite insurance book-of-business management tool. It runs as a local web server (`policydb serve`) at `http://127.0.0.1:8000`. The UI is server-rendered Jinja2 with HTMX for inline partial updates — no frontend build step, no JS framework.

---

## Workflow Guidance

Not every task needs a full brainstorm cycle. Use this to calibrate:

| Situation | Approach |
|-----------|----------|
| Bug fix with clear repro steps | Just fix it. Verify with QA. |
| Small UI tweak (move a button, change a label, adjust spacing) | Just do it. Screenshot to confirm. |
| Add a field to an existing form/table | Code directly. Remember: migration + token + importer alias. |
| New config list or settings entry | Code directly. Follow existing patterns. |
| Template/copy changes | Code directly. |
| New feature, new page, or new workflow | **Brainstorm first** — explore intent, requirements, edge cases. |
| Redesign or rethink an existing page | **Brainstorm first** — mockup before code. |
| Multi-file architectural change | **Plan first** — write a plan, get approval, then execute. |
| Ambiguous request ("make this better", "improve X") | **Brainstorm first** — clarify what "better" means. |
| New integration or external system | **Plan first** — scope, API, data flow. |

**Rule of thumb:** If the change touches 1–2 files and follows an existing pattern, skip brainstorm and just code. If it introduces a new pattern, affects 3+ files, or has design choices to make, brainstorm or plan first.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Web framework | FastAPI + uvicorn |
| Templates | Jinja2 (in `src/policydb/web/templates/`) |
| Interactivity | HTMX (partial HTML swaps) |
| Styling | Tailwind CSS (CDN, utility classes only) — see Visual Design System below |
| Database | SQLite via `sqlite3` with `row_factory`, WAL mode |
| CLI | Click (`policydb` / `pdb` entry points) |
| Parsing | **Humanize, Dateparser, RapidFuzz, Babel** — use these; do not write custom parsing code |
| Phone formatting | `phonenumbers` library via `format_phone()` in `src/policydb/utils.py` |
| Currency parsing | `parse_currency_with_magnitude()` in `src/policydb/utils.py` — supports shorthand like `1m`, `1.5M`, `500k`, `$2,000,000` |
| Address autocomplete | **Google Places API** via backend proxy (`src/policydb/geocoder.py`) — all address fields use `/api/address/autocomplete` + `/api/address/details/{place_id}`. API key stored in config, never exposed client-side. Daily rate limit configurable in Settings. |
| Geocoding | **Google Geocoding API** via `/api/address/geocode` — used for map display when cached lat/lng not available |
| Spreadsheet grids | **Tabulator 6.3** (CDN) — for large editable data tables. Reusable `_spreadsheet.html` partial + `initSpreadsheet(config)`. See Spreadsheet Component section below. |

### Address Autocomplete Rules

**All address input fields** MUST use the Google Places API backend proxy — never call any geocoding API directly from the frontend.

- **Standard inputs** (`input[name="address"]`, `input[name="exposure_address"]`): Handled automatically by `attachAutocomplete()` in `base.html`. No extra code needed.
- **Contenteditable cells** (e.g., location board): Call `/api/address/autocomplete?q=...` on input, then `/api/address/details/{place_id}` on selection to get parsed `{street, city, state, zip, lat, lon}`.
- **Map geocoding** (sidebar, project page): Call `/api/address/geocode?address=...` to get `{lat, lon}`. Cache results by PATCHing latitude/longitude back to the record.
- **API key**: Stored in `config.yaml` as `google_places_api_key`, editable in Settings > Database & Admin.
- **Rate limit**: `google_places_daily_limit` (default 1000) tracked in-memory, resets daily. Check `/api/address/usage` for current count.

## Visual Design System

### Color Theme

This project uses a **professional, data-forward design language** built on a deep navy foundation with clean warm neutrals and a structured data palette. The aesthetic is authoritative and analytical — appropriate for financial, insurance, or infrastructure dashboards — while remaining approachable through warm off-whites and clear typographic hierarchy.

---

### Core Brand Colors

| Token         | HEX       | Role                                                         |
| ------------- | --------- | ------------------------------------------------------------ |
| Midnight Blue | `#000F47` | Primary brand color; headings, borders, hero backgrounds     |
| Sky Blue      | `#CEECFF` | Light blue tints; card highlights, hover states, tag backgrounds |
| White         | `#FFFFFF` | Chart areas, modal backgrounds, content surfaces             |

**Usage guidance:** Midnight Blue should anchor any prominent UI element — navbars, section headers, primary buttons, and key borders. Sky Blue works as a softening complement for callouts and highlight states. Never use Midnight Blue as body text on dark backgrounds.

---

### Warm Neutral Scale

| Token        | HEX       | Role                                          |
| ------------ | --------- | --------------------------------------------- |
| Neutral 1000 | `#3D3C37` | Primary body text, data labels                |
| Neutral 750  | `#7B7974` | Secondary text, captions, metadata            |
| Neutral 500  | `#B9B6B1` | Dividers, input borders, disabled states      |
| Neutral 250  | `#F7F3EE` | Page background, subtotal rows, sidebar fills |

**Usage guidance:** Prefer Neutral 1000 over pure black for body text — it reads as warmer and more considered. Neutral 250 as a page background creates a subtle warmth that distinguishes this palette from sterile white-background UIs. Use Neutral 500 borders sparingly; let whitespace do structural work first.

---

### Active Accent

`#0B4BFF` — **Blue 750**

The primary interactive color. Use for:
- Hyperlinks and inline text actions
- Focus rings and active input states
- CTA buttons (filled)
- Progress indicators and loading states
- Selected/active navigation items

Do **not** use Blue 750 for decorative purposes — reserve it strictly for interactive affordances so users learn to trust it as a signal.

---

### Typography Pairing

| Role               | Font             | Weight                      |
| ------------------ | ---------------- | --------------------------- |
| Display / H1–H2    | DM Serif Display | Regular (400)               |
| UI / Body / Labels | DM Sans          | Regular (400), Medium (500) |
| Monospace / Code   | JetBrains Mono   | Regular (400)               |

Headings in DM Serif Display set in `#000F47` create immediate brand recognition. DM Sans at Neutral 1000 for body ensures legibility. Never set display headings in the warm neutrals — reserve those for supporting text.

---

### Design Principles

1. **Navy anchors, warmth softens.** Lead with Midnight Blue in structural chrome; let Neutral 250 backgrounds and warm text create approachability.
2. **Accent earns attention.** Blue 750 (`#0B4BFF`) is the only interactive signal — use it consistently and exclusively.
3. **Data colors are ordinal, not decorative.** Assign series colors by priority, not aesthetics. Never skip a tier without a documented reason.
4. **Borders are structural, not decorative.** Use Neutral 500 dividers only where a visual boundary is functionally necessary.

### Marsh Brand Guide (Charts & Deliverables)

All charts, deck slides, spreadsheets, exported tables, and client-facing exports MUST use the official Marsh color palette and typography.

**Typography:**
- **Noto Serif** — headings, chart titles, section headers
- **Noto Sans** — body text, data labels, axis labels, table content

**Core Colors:**

| Token | HEX | Role |
|-------|-----|------|
| Midnight Blue (1000) | `#000F47` | Core brand blue, primary heading/border color |
| Sky Blue (250) | `#CEECFF` | Light blue backgrounds, highlights |
| White | `#FFFFFF` | Chart backgrounds |

**Warm Neutrals:**

| Token | HEX | Use |
|-------|-----|-----|
| Neutral 1000 | `#3D3C37` | Dark text, labels |
| Neutral 750 | `#7B7974` | Secondary text |
| Neutral 500 | `#B9B6B1` | Borders, dividers |
| Neutral 250 | `#F7F3EE` | Light backgrounds, subtotal rows |

**Active Accent:** `#0B4BFF` (Blue 750) — interactive highlights, links, attention

**Data Color Order** (use in this sequence for multi-series charts):

| Priority | Color | 1000 | 750 | 500 | 250 |
|----------|-------|------|-----|-----|-----|
| 1st (Workhorse) | Blue | `#000F47` | — | `#82BAFF` | `#CEECFF` |
| 2nd | Green | `#2F7500` | `#6ABF30` | `#B0DC92` | `#DFECD7` |
| 3rd | Purple | `#5E017F` | `#8F20DE` | `#DEB1FF` | `#F5E8FF` |
| 4th | Gold | `#CB7E03` | `#FFBF00` | `#FFD98A` | `#FFF3DA` |

**Rules:**

- Additional tint stacks (500, 250) are for complex data sets and accessibility
- Each tint stack goes from dark (1000) to light (250) — use 1000 for fills, 250 for backgrounds
- The app UI and Marsh deliverables both use `#000F47` (Midnight Blue) as the primary brand color via Tailwind's `marsh` token

### Currency & Phone Rules

**Currency:** Every money field MUST use `parse_currency_with_magnitude()` from `utils.py` (supports `1m`, `500k`, `$2,000,000`). Never use raw `float()`. For display: `{{ value | currency }}` or `{{ value | currency_short }}`. Never use Python `%g` (produces scientific notation).

**Phone:** Always call `format_phone()` from `utils.py` when saving. **Email:** Always call `clean_email()` from `utils.py` when saving. Both must return `{"ok": true, "formatted": "..."}` in PATCH responses and flash the cell green when the value changes.

---

## Database & Migrations

- DB path: `~/.policydb/policydb.sqlite`
- Config path: `~/.policydb/config.yaml`
- Migrations: `src/policydb/migrations/NNN_description.sql` — sequentially numbered
- Migration runner: `src/policydb/db.py` — `init_db()` runs all migrations and rebuilds views on every server start
- Views are **always dropped and recreated** on startup — never reference non-existent columns in view SQL
- Current migration count: 106

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

**Ref tag pill partial:** `_ref_tag_pill.html` — reusable component. See template for usage. Copy depth for emails: Client + Location + Policy only — deeper suffixes are for internal linking only.

**Activity Timeline:** Auto-clustered by `activity_cluster_days` config (default 7 days). Display-only grouping, no data model.

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

### Critical Rule: New Fields → Add to Tokens
**Every time a new field is added to policies, clients, projects, or related tables, it must also be added to:**
1. The relevant `*_context()` function or `_*_tokens()` helper in `email_templates.py`
2. The `CONTEXT_TOKEN_GROUPS` dict in the same file (under the correct context and group)
3. For projects table changes: update `_project_tokens()` helper which feeds both `location_context()` and `policy_context()`

This makes the field available as a clickable token pill in the template builder at `/templates`.

---

## Copy Table Pattern (Clipboard Rich-Paste)

Reusable pattern for one-click "Copy Table" buttons that put both HTML (for Outlook/rich paste) and plain text (for plain editors) on the clipboard.

### Architecture

1. **Backend function** in `email_templates.py`: `build_policy_table(conn, client_id, project_name, rows)` returns `{"html": ..., "text": ...}`. Also: `_render_policy_table_html(rows)` and `_render_policy_table_text(rows)` for rendering pre-fetched rows.
2. **API endpoint**: Returns `JSONResponse({"html": ..., "text": ...})` — e.g. `GET /clients/{id}/copy-table?project=...`
3. **JS function** in `base.html`: `copyPolicyTable(url, btn)` — fetches the endpoint, writes both MIME types via `ClipboardItem`, with `writeText()` fallback.

### Adding a New Copy Table Button

**Template** — add a button that calls `copyPolicyTable(url, btn)`:
```html
<button type="button"
  onclick="copyPolicyTable('/your/endpoint?params=...', this)"
  class="text-xs text-gray-300 hover:text-marsh" title="Copy table to clipboard">Copy Table</button>
```

**Route** — return JSON with both formats:
```python
from policydb.email_templates import build_policy_table
result = build_policy_table(conn, client_id, project_name=project)
return JSONResponse(result)
```

For custom row sources (e.g. renewal pipeline), build row dicts with keys: `policy_type`, `carrier`, `access_point`, `policy_number`, `effective_date`, `expiration_date`, `premium`, `limit_amount`, `description` — then pass as `rows=` parameter.

### HTML Table Styling (Outlook-Safe)

- **Inline styles only** — Outlook strips `<style>` blocks and CSS classes
- Header: Marsh navy `#003865`, white text, Noto Sans font
- Alternating rows: `#FFFFFF` / `#F7F3EE`
- Borders: `1px solid #B9B6B1`
- Currency/limit columns: right-aligned
- Carrier column: `"Carrier via Access Point"` when access_point exists

### Current Deployment Locations

| Location | Endpoint | Template |
|----------|----------|----------|
| Project group header | `/clients/{id}/copy-table?project=...` | `_project_header.html` |
| Client policies section | `/clients/{id}/copy-table` | `_tab_policies.html` |
| Project detail page | `/clients/{id}/copy-table?project=...` | `project.html` |
| Renewal pipeline | `/renewals/copy-table?window=...&status=...&client_id=...` | `renewals.html` |

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

`src/policydb/reconciler.py` — matches imported rows to existing policies using additive scoring via `_score_pair()`.

**Core principles:**
- **No hard gates** — every signal contributes independently, no single field blocks a match
- **Two normalization categories:** display/save functions (write to DB) vs matching functions (comparison only, never save)
- Track diffs at **both** levels: `diff_fields` (real) AND `cosmetic_diffs` (same after normalization)
- **Railroad Protective Liability** is a distinct type — never alias to General Liability
- Coverage aliases: `_COVERAGE_ALIASES` in `utils.py` + user-learned `coverage_aliases` in config
- Carrier aliases: `carrier_aliases` in config (merged via `rebuild_carrier_aliases()`)

See `reconciler.py` for scoring weights/tiers and `utils.py` for normalization functions.

**Reconcile UI:** Upload → column mapping → validation panel → pairing board → confirm → export XLSX. Endpoints under `/reconcile/*`.

**Location Assignment Board:** `/clients/{id}/locations` — same pairing board pattern for policies → physical locations.

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

PATCH cell-save endpoints must return `{"ok": true, "formatted": "..."}`. The JS callback updates the cell and calls `flashCell()` when the formatted value differs from raw input. See `base.html` for the `flashCell` helper implementation.

### Jinja2 `tojson` in HTML Attributes

Use single-quote delimiters: `data-options='{{ items | tojson }}'`. Never use `| e` with `tojson` inside double-quoted attributes — it breaks the delimiter.

---

### Pairing Board Pattern

Reusable UI for matching records from two sources: left (source) | center (score badge) | right (target) | actions (confirm/break/create). Drag-to-pair supported. OOB counter pattern: every action returns row HTML + `hx-swap-oob` counter update.

**Colors:** Green (high >=75), Amber (medium 45–74), Red (unmatched source), Purple (extra target/draggable).

**Reference implementation:** `reconcile/_pairing_board.html`. See `reconciler.py` for the `_score_pair()` pattern.

---

## Spreadsheet Component (Tabulator)

Reusable spreadsheet/grid component for large editable data tables, built on **Tabulator 6.3** (CDN).

### Architecture

| File | Purpose |
|------|---------|
| `_spreadsheet.html` | Shared partial: Tabulator CDN includes, Marsh CSS overrides, `initSpreadsheet(config)` JS function |
| `policies/spreadsheet.html` | Policy spreadsheet wrapper (extends base.html, includes `_spreadsheet.html`) |
| (Future) `clients/spreadsheet.html` | Client spreadsheet wrapper |
| (Future) `followups/spreadsheet.html` | Follow-up spreadsheet wrapper |

### `initSpreadsheet(config)` API

```javascript
initSpreadsheet({
    el: "#spreadsheet-grid",         // container selector
    data: [...],                      // row objects
    columns: [...],                   // Tabulator column definitions
    frozenFields: ["client_name"],    // fields to freeze on left
    patchUrl: "/policies/{uid}/cell", // URL template for cell save
    idField: "policy_uid",            // row field for PATCH URL
    entityName: "policy",             // for UI labels
    addRowUrl: "/policies/quick-add", // POST endpoint for new rows (null to disable)
    exportUrl: "/policies/spreadsheet/export", // branded XLSX export
    projectsByClient: {...},          // client-scoped dropdown data
})
```

### When to Use

For any **large data table** or **bulk-editing UI** with 50+ rows, use this Tabulator component instead of building a custom contenteditable table. Benefits: virtual scrolling, column resize, built-in sort/filter/header filters, keyboard navigation.

For **small tables** (< 20 rows) within detail pages, continue using the existing `initMatrix()` contenteditable pattern — it's lighter and doesn't need a CDN dependency.

### Cell Save Pattern

`cellEdited` → `fetch(PATCH, {field, value})` → on success update cell with `formatted` value + green flash → on error restore old value + red flash. Same backend endpoints as existing inline editing.

### Theming

Tabulator's default dark gray theme is overridden in `_spreadsheet.html` to match Marsh brand: navy `#003865` headers, `#F7F3EE` alt-rows, `#B9B6B1` borders, Noto Sans font, `#CEECFF` selected rows.

---

## Manual Chart Library & Visual Builders

### Overview
`/charts/manual/` — editable Chart.js + HTML chart templates for client deliverables. Gallery at `/charts/manual`, editor at `/charts/manual/{chart_type}`. Templates live in `src/policydb/web/templates/charts/manual/_tpl_{id}.html`. Registry in `MANUAL_CHART_REGISTRY` in `charts.py`.

**Current types:** 9 charts (rate/premium, loss triangle, etc.), 1 quote comparison, 5 callout cards (stat, coverage, carrier, milestone, narrative), 1 timeline builder.

### Adding a New Chart/Visual (2 files only)
1. Add registry entry to `MANUAL_CHART_REGISTRY` in `charts.py` (id, title, description, category, icon)
2. Create template `_tpl_{id}.html` in `src/policydb/web/templates/charts/manual/`
3. No routes, migrations, or JS library changes needed — the editor wrapper auto-includes

### Template Structure Pattern
Every template follows this structure:
- **Display area** (top): `.manual-chart-page` (fixed 960×540) or `.manual-chart-page-auto` (variable height)
- **Editor panel** (bottom): `#editor-panel` with `.mc-editor` class — display toggles, content inputs, style options
- **Script** (IIFE): `window.refreshCurrentChart()` function for snapshot load, uses `ManualChart.COLORS` and `ManualChart.DATA_COLORS`

### Export-Safe Styling Rules (CRITICAL)
All elements inside the display area (`.manual-chart-page` / `.manual-chart-page-auto`) MUST use export-safe CSS because html2canvas renders them to PNG:

| Rule | Why |
|------|-----|
| **Use px units, not rem** | rem is relative to root font-size which doesn't exist in the offscreen clone |
| **Use solid hex colors, not rgba()** | html2canvas renders rgba() backgrounds incorrectly or transparent |
| **Use direct hex values, not CSS variables** | `var(--mc-text)` won't resolve in the offscreen container |
| **Use inline styles on JS-generated elements** | CSS class styles may not apply in the clone |
| **Use explicit `font-family` on generated elements** | Font inheritance breaks in offscreen clones |
| **No `font-variant-numeric`** | html2canvas ignores it |
| **`<canvas>` → `<img>` conversion** | html2canvas cannot render `<canvas>` drawings — `prepareClone()` handles this automatically |

**Editor panel** (`.mc-editor`) can use rem, CSS variables, and Tailwind classes freely — it's never exported.

### Snapshot System
- Save/Load via `ManualChart.collectAll()` / `populateAll()` — collects all `<input>`, `<select>`, `<textarea>` with `name` attributes inside the editor panel
- Complex state (dynamic rows, steps) serialized to a hidden `<textarea>` with a `name` attribute
- `window.refreshCurrentChart()` must handle two-phase restore: populateAll sets textarea value → refreshCurrentChart parses it and rebuilds UI
- Client tagging optional via snapshot bar combobox

### Display Toggle Pattern
- Toggle checkboxes in editor call `applyDisplayOptions()`
- `ManualChart.applyToggle(toggleId, targetId)` sets `display: ''` or `'none'`
- **Gotcha:** `applyToggle` sets `display: ''` which resets to the element's default (usually `block`). If the target needs `flex` or `grid`, handle it manually instead of using `applyToggle`

### Key Files
| File | Purpose |
|------|---------|
| `charts.py` | Registry, routes, snapshot CRUD, gallery |
| `editor.html` | Shared wrapper: snapshot bar, client combobox, export/copy buttons |
| `gallery.html` | Chart library gallery (Charts + Visual Builders sections) |
| `manual.js` | `ManualChart` namespace: COLORS, DATA_COLORS, fmtCurrency, collectAll/populateAll, applyToggle |
| `manual.css` | `.manual-chart-page`, `.manual-chart-page-auto`, `.mc-editor`, `.mc-legend` classes |
| `_tpl_*.html` | Individual chart/builder templates |

### Export Infrastructure
- `prepareClone()` in `editor.html` — strips `.no-print`, border/shadow/radius, converts `<canvas>` to `<img>`
- `exportChartAtSize()` — fixed 960×540 charts with size picker (Small/Medium/Large)
- `exportAutoHeight()` — variable-height cards at natural dimensions, 2x scale, single "Export PNG" button
- `copyToClipboard()` — same as export but writes to clipboard via `ClipboardItem`
- All three paths use `prepareClone()` — fixes apply globally

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
- SQLite migrations are one-way; use `ALTER TABLE ... ADD COLUMN` and never remove columns

### Lessons Learned (Bug Patterns to Avoid)

**1. Migration wiring:** `.sql` file alone is NOT enough — must also wire into `init_db()` in `db.py` with version check + INSERT into `schema_version`.

**2. `<form>` inside `<tr>` is invalid HTML:** Browsers silently discard it. Use `hx-post` + `hx-include` on the button instead, or move the form inside a `<td>`.

**3. `initMatrix()` combobox positioning:** Parent `<td>` MUST have `position: relative` (Tailwind: `relative`) or dropdown spans full page width.

**4. Config lists MUST be in Settings UI:** Add new config lists to BOTH `_DEFAULTS` in `config.py` AND `EDITABLE_LISTS` in `settings.py`.

**5. Source-level scoping propagation:** Compliance queries must check BOTH requirement's `project_id` AND source's `project_id`.

**6. `window.location.reload()` scroll jump:** Save `window.scrollY` to `sessionStorage` before reload, restore on load. Scope the key per-page.

**7. NOT NULL constraints on blank rows:** Use `""` (not `None`) for NOT NULL text columns in add-row endpoints.

**8. `initMatrix()` add-row must return a single `<tr>`:** Not the entire card/section HTML — that causes overlapping renders inside tbody.

**9. `initAtComplete()` on HTMX-loaded inputs:** Must be called in a `<script>` block within the partial template after the input renders.

**10. `table-fixed` breaks on narrow viewports:** Use `table-layout: auto` with `min-width` + `whitespace-nowrap` instead.

**11. Scratchpads are ephemeral → activities are the record:** "Log as Activity" creates the permanent record. `saved_notes` table is legacy.

**12. Sidebar responsive:** Use `hidden xl:block` (not `lg:block`) — `lg` overlaps tabbed content.

**13. Worktree pycache conflicts:** Drop stash, `git checkout -- '**/__pycache__/'`, re-run `pip install -e .`.

**14. Jinja2 has no `loop.parent`:** Capture outer loop index with `{% set outer_idx = loop.index0 %}` before inner loop.

**15. Config import:** `import policydb.config as cfg` then `cfg.get("key")`. Never `from policydb.config import cfg`.

**16. Verify config key names against `_DEFAULTS`:** Spec/plan key names drift from actual implementation. Always read `config.py` first.

**17. Milestone profiles use `renewal_milestones` names**, not `mandated_activities` names.

**18. Ref tag copy must use `build_ref_tag()`**, never bare `policy_uid`. Always use `copyRefTag()` JS function.

**19. `thread_id` column is legacy** — do not write to it. Timeline uses auto-clustering now.

**20. SQLite handler attaches in uvicorn worker**, not CLI process. Must use `@app.on_event("startup")` in `app.py`.

**21. Child loggers propagate automatically** — only configure handlers on root `policydb` logger.

**22. Kill existing servers before testing:** When starting `pdb serve` or uvicorn for testing, first kill any existing server on port 8000: `lsof -ti:8000 | xargs kill -9 2>/dev/null`.

**23. html2canvas cannot render `<canvas>` elements:** Chart.js draws on `<canvas>` which html2canvas skips. Must convert to `<img>` via `toDataURL()` before export. The shared `prepareClone()` in `editor.html` handles this automatically.

**24. html2canvas export styling:** Never use `rgba()`, CSS variables (`var(--x)`), `rem` units, or `font-variant-numeric` in elements that will be exported to PNG. Use solid hex colors, `px` units, and explicit `font-family`. See "Export-Safe Styling Rules" in Manual Chart Library section.

**25. `ManualChart.applyToggle` resets display to `''` (block):** If the toggled element needs `display:flex` or `display:grid`, handle the toggle manually instead of using `applyToggle`.

**26. `fmtCurrency` regex must not eat significant zeros:** The regex `/\.?0+$/` strips trailing zeros from integers too (`"40"` → `"4"`). For the K path, use `Math.round()` with no regex. For the M path, use a decimal-anchored regex: `.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')`.

**27. PNG export must strip decorative CSS:** `prepareClone()` removes `border`, `box-shadow`, and `border-radius` from the clone so exports are clean white with no gray edges.
