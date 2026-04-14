# PolicyDB — Claude Code Instructions

## Project Overview
PolicyDB is a local FastAPI + SQLite insurance book-of-business management tool. It runs as a local web server (`policydb serve`) at `http://127.0.0.1:8000`. The UI is server-rendered Jinja2 with HTMX for inline partial updates — no frontend build step, no JS framework.

---

## Golden Rule — Touch Once

**The user should never have to enter the same fact twice.** When designing or modifying any feature that touches data, ask: *"Where else could this information be used, and does our design let it flow there?"*

- Every new input field must be checked against existing data — is this fact already captured somewhere? If so, reuse it (derive, lookup, read from the canonical source).
- Every edit screen must be checked against other records — what *other* tables could benefit from what the user just told us? Write-back is as important as read-side lookup.
- Discovered facts flow to the canonical record, not just a local note. Example: if a user confirms an endorsement on a policy while reviewing a contract, the fact is written to `policies.endorsements`, not just the compliance requirement's notes field.
- Derived views pull from canonical sources, not copy them. One source of truth per fact.
- If you catch yourself about to add a second storage location for something that already exists, stop and consolidate instead.

This is non-negotiable. Duplicated data entry is friction the user notices every day, and parallel storage locations drift out of sync and become bugs.

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

**Rule of thumb:** If the change touches 1-2 files and follows an existing pattern, skip brainstorm and just code. If it introduces a new pattern, affects 3+ files, or has design choices to make, brainstorm or plan first.

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
| Address autocomplete | Google Places API via backend proxy (`src/policydb/geocoder.py`) — see `reference_address_autocomplete` memory |
| Spreadsheet grids | Tabulator 6.3 (CDN) — see `policydb-spreadsheet` skill |

### Currency & Phone Rules

**Currency:** Every money field MUST use `parse_currency_with_magnitude()` from `utils.py`. Never use raw `float()`. For display: `{{ value | currency }}` or `{{ value | currency_short }}`. Never use Python `%g` (produces scientific notation).

**Phone:** Always call `format_phone()` from `utils.py` when saving. **Email:** Always call `clean_email()` from `utils.py` when saving. Both must return `{"ok": true, "formatted": "..."}` in PATCH responses and flash the cell green when the value changes.

---

## Visual Design System

See the `policydb-design-system` skill for full color palette, typography, warm neutrals, design principles, and Marsh Brand Guide. Key quick-reference:

- **Primary brand:** Midnight Blue `#000F47` | **Accent:** Blue 750 `#0B4BFF`
- **Body text:** Neutral 1000 `#3D3C37` | **Page bg:** Neutral 250 `#F7F3EE`
- **Fonts:** DM Serif Display (headings), DM Sans (body), JetBrains Mono (code)
- **Charts/deliverables:** Noto Serif + Noto Sans with Marsh data color order

---

## Database & Migrations

- DB path: `~/.policydb/policydb.sqlite`
- Config path: `~/.policydb/config.yaml`
- Migrations: `src/policydb/migrations/NNN_description.sql` — sequentially numbered
- Migration runner: `src/policydb/db.py` — `init_db()` runs all migrations and rebuilds views on every server start
- Views are **always dropped and recreated** on startup — never reference non-existent columns in view SQL
- Current migration count: 135

### Key Tables
- `clients` — name, industry, contacts, account exec, scratchpad
- `policies` — all policy fields including `is_opportunity`, `first_named_insured`, `renewal_status`, `placement_colleague`, `underwriter_name`
- `client_contacts` — contact_type ('client' or 'internal'), phone, mobile, role, notes
- `policy_contacts` — policy-specific contacts (placement colleagues, underwriters)
- `activity_log` — activities, follow-ups, notes
- `policy_milestones` — checklist items per policy
- `email_templates` — user-managed email form letters with `{{token}}` placeholders
- `kb_bookmarks` — web bookmarks with BM-NNN UIDs, url, title, category, tags
- `user_notes` — global dashboard scratchpad (id=1)
- `client_scratchpad` — per-client freeform notes
- `prompt_templates` — LLM prompt templates with system_prompt, closing_instruction, required_record_types (JSON), depth_overrides (JSON)
- `prompt_export_log` — tracks clipboard copy events from Prompt Builder

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
| action_center.py | /action-center | Focus Queue (default), Follow-ups (legacy), + More menu (Inbox, Activities, Scratchpads, Issues, Anomalies, Review, Health) |
| activities.py | /followups/plan, /renewals | Plan Week, renewal pipeline, activity PATCH |
| settings.py | /settings | Config list management, email subjects |
| templates.py | /templates | Email template CRUD + compose panel |
| reconcile.py | /reconcile | Statement reconciliation |
| inbox.py | /inbox/* | Inbox capture, process, scratchpad process (redirects /inbox -> Action Center) |
| prompt_builder.py | /prompt-builder | AI Export Prompt Builder — record selection, template management, prompt assembly + copy |

**Note:** `/inbox`, `/followups`, and `/activities` all redirect to `/action-center?tab=...`. The Action Center is the primary UI for daily work management.

### HTMX Row Edit Pattern
Every pipeline/table view has three endpoint variants per row:
- `GET /{uid}/row/edit` -> inline edit form (replaces `#row-{uid}`)
- `POST /{uid}/row/edit` -> saves, returns display row
- `GET /{uid}/row` -> restore display row (Cancel button target)
- `GET /{uid}/row/log` -> inline activity log form
- `POST /{uid}/row/log` -> saves activity, restores display row

Variants exist for: `row` (client detail), `dash` (dashboard), `renew` (renewals page).

### Inline Status Badge
`src/policydb/web/templates/policies/_status_badge.html` — renders a `<select>` that auto-saves status via HTMX POST to `/policies/{uid}/status`. Needs `renewal_statuses` in template context.

### UID & Reference Tag System

**Policy UIDs:** Auto-generated sequential `POL-001`, `POL-002`, etc. via `next_policy_uid()` in `db.py`. Separate from `policy_number` (carrier's external number).

**Client Numbers:** `cn_number` on `clients` table — external account number from AMS. Used as root of ref tag hierarchy. Fallback: `C{client_id}`.

**Reference Tags:** Built by `build_ref_tag()` in `utils.py`. Hierarchical format: `CN{number}-L{project_id}-{policy_uid}`. Registered as Jinja2 global in `app.py`.

**Copy format:** `copyRefTag()` in `base.html` wraps with `[PDB:...]` for Outlook search distinctiveness. Clicking any ref tag pill copies `[PDB:CN123456789-POL042]` to clipboard.

**Ref tag pill partial:** `_ref_tag_pill.html` — reusable component. Copy depth for emails: Client + Location + Policy only.

**Activity Timeline:** Auto-clustered by `activity_cluster_days` config (default 7 days). Display-only grouping, no data model.

### Opportunities
Policies with `is_opportunity=1` are excluded from renewal pipeline, stale renewal alerts, and client summary policy counts. Opportunities are **included** in suggested follow-ups so they remain visible for tracking. Views that exclude opportunities use `AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)`. The "Convert to Policy" flow sets real dates and clears the flag.

### Renewal Status Exclusion
`renewal_statuses_excluded` config key stores statuses silenced from alerts. Pass `excluded_statuses=cfg.get("renewal_statuses_excluded", [])` to `get_renewal_pipeline()`, `get_suggested_followups()`, and `get_stale_renewals()`.

### Auto-Purge
- `_purge_old_logs()` in `db.py` runs on every server startup after health checks
- Deletes `audit_log` and `app_log` rows older than `log_retention_days` config (default: 730 = 2 years)

### FTS5 Search Index
- `search_index` FTS5 virtual table (migration 133), rebuilt on every startup via `rebuild_search_index()` in `queries.py`
- Tokenizer: `porter unicode61 remove_diacritics 2` (stemming + unicode + diacritic folding + prefix matching)
- Columns: `entity_type`, `entity_id`, `title` (weight 10), `subtitle` (5), `body` (1), `metadata` (3)
- Indexed entities: client, policy, activity (2yr), issue (open), contact, program, meeting, location, inbox (6mo), scratchpad, kb_article, kb_bookmark
- Adding new searchable fields: update `rebuild_search_index()` INSERT for that entity AND `_hydrate()` in `full_text_search()`
- Fuzzy fallback via RapidFuzz when FTS5 < 3 results (clients, contacts, programs only)
- Live search dropdown: `/search/live` endpoint, HTMX on navbar input with 300ms debounce

### Logs UI
- **Route:** `/logs` — tabbed page (App Log / Audit Log), lazy-loaded via HTMX
- **Old URL:** `/settings/audit-log` redirects to `/logs?tab=audit`

---

## Email Template System

### Critical Rule: New Fields -> Add to Tokens
**Every time a new field is added to policies, clients, projects, or related tables, it must also be added to:**
1. The relevant `*_context()` function or `_*_tokens()` helper in `email_templates.py`
2. The `CONTEXT_TOKEN_GROUPS` dict in the same file (under the correct context and group)
3. For projects table changes: update `_project_tokens()` helper which feeds both `location_context()` and `policy_context()`

---

## Config System

`src/policydb/config.py` — `_DEFAULTS` dict merged with `~/.policydb/config.yaml`.

Key config lists managed in Settings UI (`/settings`):
- `renewal_statuses`, `renewal_statuses_excluded`, `opportunity_statuses`
- `policy_types`, `carriers`, `activity_types`, `renewal_milestones`, etc.
- `email_subject_policy/client/followup` — mailto subject templates with `{{tokens}}`

`cfg.get(key, default)`, `cfg.add_list_item()`, `cfg.remove_list_item()`, `cfg.save_config()` are the main API.

**Critical rule: No hardcoded lists.** All categorized lists MUST be stored in `_DEFAULTS` in config.py and editable via the Settings UI. Never hardcode lists in Python code — always read from `cfg.get("key_name")` at runtime.

---

## Importer

`src/policydb/importer.py` — accepts CSV/Excel. Column aliases map alternative header names to canonical field names. When new fields are added to the schema, add aliases here too.

---

## UI Implementation Standards

### Core UI Defaults (Design Decisions)

These are standing decisions that apply across the entire application. Do not deviate without explicit user approval.

| Decision | Default | Notes |
|----------|---------|-------|
| **Page layout** | Tabbed (4 tabs per page), lazy-loaded via HTMX | Client page and policy page both use tabs |
| **Tab loading** | Lazy-load each tab on first click | Active tab loads on page render; others on demand |
| **Tab persistence** | sessionStorage remembers last tab per page | Returning to a page opens the last-used tab |
| **Save behavior** | Per-field PATCH on blur — no Save button | Every field saves individually when focus leaves |
| **Field style** | Contenteditable + combobox everywhere | ALL edit fields use contenteditable text or combobox, not `<input>` |
| **Form sections** | All open by default | No collapsed `<details>` on detail/edit pages |
| **Sidebar** | Sticky right sidebar on client page | Key Dates + Quick Actions always visible |
| **Policy drill-down** | Quick-edit popover from client page | "Open ->" link for full page |
| **Working Notes** | Floating panel accessible from any tab | Not locked to one tab |
| **Contacts on policy** | Editable inline (matrix pattern) | Full add/edit/remove capability |
| **Checklist/RFIs** | Summary on client, detail on policy | Per-policy items, aggregate progress on client |

### Input Pattern Hierarchy

| Field Type | Preferred Pattern | Avoid |
|---|---|---|
| Freeform text in tables | `contenteditable` cell | `<input>` inside `<td>` |
| Single-field edits | Click-to-edit (display -> input on click) | Always-visible input |
| Carrier, industry, LOB | Combobox with filtered dropdown | `<select>` dropdown |
| Multiple values (tags) | Pill/tag input (Enter to add, x to remove) | Multi-select `<select>` |
| Boolean flags | CSS toggle switch | `<input type="checkbox">` |
| 2-5 mutually exclusive options | Segmented control (pill button group) | `<select>` or radio buttons |
| Dates | `<input type="date">` styled to match UI | Plain text input |
| Limits, retentions | Stepper with +/- buttons | Plain `<input type="number">` |
| Row ordering | Drag-to-reorder with handle | Manual order fields |

### Contenteditable Tables

- Cells appear static; editable on click. Focused cell gets bottom border highlight in brand color.
- `Tab` advances to next cell; `Tab` on last cell appends a blank row
- Empty cells show placeholder text via `data-placeholder` and `::before` CSS
- Save on `blur` via `fetch` PATCH. New rows POST and store returned `id` as `data-id`
- `+ Add row` button below table, hidden in `@media print`

### General UI Principles

- No always-visible input boxes in table rows unless primary action (search bar)
- No `<select>` for fields where user might type — use combobox
- No raw checkboxes for boolean status — use toggle switch
- Keyboard navigable — all interactive elements reachable by keyboard
- Save on `blur` or `Enter`; destructive changes confirm via inline prompt, never `alert()`
- Error states: red border + inline message, never `alert()` or `console.error()` only
- Print safety: UI controls carry `no-print` class

### Server-Side Parsing & Visual Feedback

PATCH cell-save endpoints must return `{"ok": true, "formatted": "..."}`. The JS callback updates the cell and calls `flashCell()` when the formatted value differs from raw input.

### Jinja2 `tojson` in HTML Attributes

Use single-quote delimiters: `data-options='{{ items | tojson }}'`. Never use `| e` with `tojson` inside double-quoted attributes.

---

## Skills Reference

Specialized reference docs are available as on-demand skills:

| Skill | When to use |
|-------|-------------|
| `policydb-design-system` | Color palette, typography, Marsh brand guide |
| `policydb-charts` | Chart templates, exports, snapshot system, html2canvas rules |
| `policydb-copy-table` | Clipboard rich-paste buttons, Outlook-safe HTML tables |
| `policydb-spreadsheet` | Tabulator grid component, initSpreadsheet() API |
| `policydb-activities` | Focus Queue scoring, waiting promotion, activity lifecycle, issue tracking, auto-close, escalation |
| `policydb-activity-review` | Unlogged session detection, system vs user filtering, review gates, anomaly rules |
| `policydb-exports` | XLSX theming, CSV, HTML copy-table, _write_sheet(), build_generic_table() |
| `policydb-reports` | All 29 chart types, insurance schematics, tower notation, TCOR, benchmarking, data sources |
| `policydb-reconciler` | Reconciliation scoring, pairing board UI pattern |
| `policydb-timeline` | Timeline engine, milestone health, accountability tracking |
| `policydb-review` | Review queue, slideover, gate conditions, anomaly engine, override flow |
| `risk-analysis-skill` | Client risk assessment, coverage strategy, exposure analysis |
| `policydb-prompt-builder` | Prompt Builder assembler registry, depth tiers, template system, data assembly patterns |

---

## QA Testing Requirement

**After any change that impacts the UI** (template edits, route changes, new features, CSS changes), Claude MUST run a thorough QA test using the browser:
1. Navigate to the affected page(s) and take screenshots
2. Verify all elements render correctly — no overflow, no overlapping, no missing data
3. Test interactive elements — click buttons, fill forms, verify saves work
4. Check for regressions on related pages
5. Fix issues before committing

This is not optional — UI changes without visual verification have repeatedly shipped broken layouts.

---

## Development Notes

- Always pass `renewal_statuses` to any template that renders `_status_badge.html`
- Always call `_attach_milestone_progress(conn, rows)` before passing pipeline rows to templates
- `_attach_client_ids(conn, rows)` adds `client_id` to pipeline rows for linking
- SQLite migrations are one-way; use `ALTER TABLE ... ADD COLUMN` and never remove columns

### Critical Bug Patterns (most common)

**Migration wiring:** `.sql` file alone is NOT enough — must also wire into `init_db()` in `db.py` with version check + INSERT into `schema_version`.

**Config lists MUST be in Settings UI:** Add new config lists to BOTH `_DEFAULTS` in `config.py` AND `EDITABLE_LISTS` in `settings.py`.

See `feedback_lessons_learned` memory for the full list of 20+ historical bug patterns.
