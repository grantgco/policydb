# Program Schematic Entry Page — Design Spec

## Context

The Chart Deck Builder's tower/layer diagram renders a Marsh-style program schematic, but entering the tower data (tower_group, layer_position, attachment_point, deductible, schematic_column, participation_of) is currently scattered across individual policy edit pages. Brokers need a dedicated, data-intensive entry point to build out a program's tower structure quickly — especially when setting up a new client or preparing for a renewal presentation.

This spec covers three connected pieces:
1. **Program Schematic Entry Page** — dedicated matrix page for one program's tower structure
2. **LLM Import Extension** — extend the extraction prompt to capture tower fields
3. **Data Completeness Guidance** — passive badges + active chart prompts to nudge field completion

## Architecture

### New Route & Template

**Route:** `GET /clients/{client_id}/programs/{tower_group}` — full-page schematic entry for one program.

**Entry points:**
- Client detail Policies tab → tower visualization → "Edit Program →" link per tower_group
- Chart deck builder → tower chart incomplete banner → "Set up {program} →"

### Page Layout (Full Width, Data Top, Preview Bottom)

```
┌──────────────────────────────────────────────────────────────────┐
│ Breadcrumb: Clients / {Name} / Programs / {Tower Group}          │
│ Header: {Tower Group} — {Client Name}              [← Back]     │
├───────────────────────────────┬──────────────────────────────────┤
│ UNDERLYING LINES              │ EXCESS LAYERS                    │
│ ┌───────────────────────────┐ │ ┌──────────────────────────────┐ │
│ │ Line | Carrier | Limit |  │ │ │ # | Carrier(s) | Limit |    │ │
│ │ Ded | Premium | Pol# |   │ │ │ Attach | PO | Premium |      │ │
│ │ Form | Col#              │ │ │ Pol# | Form                   │ │
│ │ (contenteditable matrix) │ │ │ (contenteditable matrix)      │ │
│ │ + Add underlying line    │ │ │ + Add excess layer            │ │
│ └───────────────────────────┘ │ └──────────────────────────────┘ │
├───────────────────────────────┴──────────────────────────────────┤
│ SCHEMATIC PREVIEW (live-updating D3 tower diagram, full width)   │
│ Same rendering as chart deck tower chart, re-renders on save     │
└──────────────────────────────────────────────────────────────────┘
```

### New Files

```
src/policydb/web/
├── routes/
│   └── programs.py                    # New router: /clients/{id}/programs/{tg}
├── templates/
│   └── programs/
│       ├── schematic.html             # Full page (extends base.html)
│       ├── _underlying_matrix.html    # Contenteditable table for underlying lines
│       ├── _underlying_row.html       # Single underlying line row
│       ├── _excess_matrix.html        # Contenteditable table for excess layers
│       ├── _excess_row.html           # Single excess layer row
│       └── _schematic_preview.html    # D3 tower preview (HTMX partial)
```

### Modified Files

```
src/policydb/web/app.py                      # Register programs router
src/policydb/web/routes/clients.py           # Add "Edit Program →" links to tower visual
src/policydb/web/templates/clients/_tab_policies.html  # Add edit links per tower_group
src/policydb/llm_schemas.py                  # Add participation_of to schemas
src/policydb/importer.py                     # Add aliases for attachment_point, participation_of
```

## Underlying Lines Table

### Columns

| Column | Editable | data-field | Description |
|--------|----------|------------|-------------|
| Line | Yes (combobox) | `policy_type` | Coverage line (GL, Auto, EL, WC, etc.) from `policy_types` config |
| Carrier | Yes (combobox) | `carrier` | From `carriers` config |
| Limit | Yes (contenteditable) | `limit_amount` | Currency, parsed via `parse_currency_with_magnitude()` |
| Deductible | Yes (contenteditable) | `deductible` | Currency — shown as gray block in schematic |
| Premium | Yes (contenteditable) | `premium` | Currency |
| Policy # | Yes (contenteditable) | `policy_number` | Carrier's policy number |
| Form | Yes (combobox) | `coverage_form` | Coverage form (CG, OL, etc.) |
| Col # | Drag handle | `schematic_column` | Reorder via drag — sets column position in schematic |

### Add Row Behavior

1. Click "+ Add underlying line"
2. Server creates a minimal policy record:
   - `client_id` from page context
   - `tower_group` from page context
   - `layer_position = 'Primary'`
   - `schematic_column` = next available (max existing + 1)
   - `policy_uid` via `next_policy_uid()`
   - All other fields blank (empty string for NOT NULL text columns)
3. Returns rendered `_underlying_row.html` partial
4. Focus on first editable cell (policy_type)

### Cell Save

`PATCH /clients/{client_id}/programs/{tower_group}/underlying/{policy_id}/cell`
- Same `{"field": "...", "value": "..."}` pattern as exposure matrix
- Allowed fields: `policy_type`, `carrier`, `limit_amount`, `deductible`, `premium`, `policy_number`, `coverage_form`
- Currency fields: `parse_currency_with_magnitude()` then format
- Response: `{"ok": true, "formatted": "..."}`

### Reorder

`POST /clients/{client_id}/programs/{tower_group}/underlying/reorder`
- Body: `{"order": [policy_id_1, policy_id_2, ...]}`
- Updates `schematic_column` = 1, 2, 3... in order
- Returns updated preview partial

### Delete

`DELETE /clients/{client_id}/programs/{tower_group}/underlying/{policy_id}`
- Deletes the policy record
- Returns empty (HTMX outerHTML swap removes row)
- Renumbers remaining `schematic_column` values

## Excess Layers Table

### Columns

| Column | Editable | data-field | Description |
|--------|----------|------------|-------------|
| Layer # | Auto-numbered | — | Sequential from 1 (umbrella first if present) |
| Type | Badge | `layer_position` | "Umbrella" or "Excess" (set on creation) |
| Carrier(s) | Yes (contenteditable) | `carrier` | Single carrier name, or "(expand)" for programs |
| Limit | Yes (contenteditable) | `limit_amount` | Currency |
| Attachment | Yes (contenteditable) | `attachment_point` | Currency — "x $10M" |
| Participation | Yes (contenteditable) | `participation_of` | Currency — "po $30M" (null if sole carrier) |
| Premium | Yes (contenteditable) | `premium` | Currency |
| Policy # | Yes (contenteditable) | `policy_number` | Text |
| Notation | Auto-generated | — | Read-only, computed: "$10M po $30M x $70M" |

### Umbrella Row

The first row in the excess table is the umbrella (if one exists). Visually distinguished with a blue-tinted background. Created via "+ Add umbrella" button (only shown if no umbrella exists). Sets `layer_position = 'Umbrella'`, `attachment_point = 0`.

### Co-Insured / Program Layers

When an excess layer has `is_program = 1` (or the user marks it as shared):
- The row shows carrier as "3 carriers — expand ▾"
- Clicking expands nested sub-rows for `program_carriers` entries
- Sub-rows: Carrier, Policy #, Premium, Limit, Sort Order
- Uses the same `program_carriers` endpoints that already exist on policies

### Add Row Behavior

1. Click "+ Add excess layer"
2. Server creates policy with:
   - `tower_group` from context
   - `layer_position = 'Excess'`
   - `attachment_point` = top of current highest layer (auto-calculated)
   - `policy_type = 'Excess Liability'` (default, editable)
3. Option: "+ Add umbrella" if none exists — creates with `layer_position = 'Umbrella'`, `attachment_point = 0`

### Cell Save

`PATCH /clients/{client_id}/programs/{tower_group}/excess/{policy_id}/cell`
- Same pattern as underlying. Allowed: `carrier`, `limit_amount`, `attachment_point`, `participation_of`, `premium`, `policy_number`, `layer_position`, `coverage_form`

### Delete

`DELETE /clients/{client_id}/programs/{tower_group}/excess/{policy_id}`
- Deletes the policy and any associated `program_carriers` rows

## Schematic Preview

### HTMX Partial

`GET /clients/{client_id}/programs/{tower_group}/preview`
- Returns `_schematic_preview.html` — a container with inline `<script>` that calls the same D3 tower rendering used in the chart deck
- Data from `get_tower_data()` filtered to just this tower_group
- Triggered after each cell save via `hx-trigger="tower-updated from:body"` (custom event dispatched by save callbacks)

### Behavior

- Re-renders the full tower SVG on each data change
- Shows the same Marsh-style layout: underlying columns, deductible blocks, umbrella drop-fill, excess layers
- Not exportable from this page (that's the chart deck's job)
- Serves as immediate visual feedback that the data is structurally correct

## LLM Import Extension

### Schema Changes

**`src/policydb/llm_schemas.py`:**
- `POLICY_EXTRACTION_SCHEMA`: already has `tower_group`, `layer_position`, `attachment_point`. **Add `participation_of`** (type: number, description: "Total layer limit if co-participation / part-of arrangement")
- `POLICY_BULK_IMPORT_SCHEMA` → `program_layers` → fields: **Add `participation_of`** to the layer fields array

### Importer Aliases

**`src/policydb/importer.py` ALIASES dict:**
- Add: `"attachment": "attachment_point"`, `"attachment point": "attachment_point"`, `"xs": "attachment_point"`
- Add: `"participation": "participation_of"`, `"part of": "participation_of"`, `"po": "participation_of"`

### Import Apply Route

**`src/policydb/web/routes/clients.py` → `client_ai_bulk_import_apply()`:**
- Ensure `participation_of` is included in both UPDATE and INSERT statements for policies
- Include `attachment_point` and `participation_of` when creating `program_carriers` rows

### Review Step Enhancement

During import validation panel, tower fields (`tower_group`, `layer_position`, `attachment_point`, `participation_of`) that were inferred by the model (not explicitly stated in source) get a yellow highlight with tooltip: "Model's best guess — verify before confirming". This uses a `confidence` flag in the parsed data, set by the prompt asking the model to indicate certainty.

## Data Completeness Guidance

### Passive: Readiness Badges

**Location:** Client detail Policies tab, tower visualization section header.

Per tower_group, show a small badge:
- Format: `"Casualty — 4 lines, 6 layers · 85% complete"`
- Color: green (>80%), amber (50-80%), gray (<50%)
- Clickable → links to `/clients/{id}/programs/{tower_group}`

**Completeness scoring per row:**

| Row Type | Fields checked | Weight |
|----------|---------------|--------|
| Underlying | policy_type (required), carrier, deductible | 3 fields |
| Umbrella | carrier, limit | 2 fields |
| Excess | carrier OR participants, limit, attachment_point | 3 fields |

Score = filled fields / total expected fields × 100

### Active: Chart Generation Prompts

When the tower chart is selected in the deck configurator and data is incomplete:
- Banner on the tower chart slide: `"⚠ {Tower Group} is missing {N} fields. Set up program →"`
- Link opens schematic entry page in new tab
- Chart still renders what it can — never blocks

### Implementation

**New function:** `get_schematic_completeness(conn, client_id)` in `queries.py`
- Returns: `[{"tower_group": "Casualty", "underlying_count": 4, "excess_count": 6, "pct_complete": 85, "missing_fields": ["GL: carrier", "Layer 3: attachment_point"]}, ...]`
- Called by the policies tab route and the chart deck view route

## Routes Summary

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/clients/{id}/programs/{tg}` | Schematic entry page |
| POST | `/clients/{id}/programs/{tg}/underlying/add` | Add underlying line (auto-creates policy) |
| PATCH | `/clients/{id}/programs/{tg}/underlying/{pid}/cell` | Save underlying cell |
| POST | `/clients/{id}/programs/{tg}/underlying/reorder` | Reorder underlying columns |
| DELETE | `/clients/{id}/programs/{tg}/underlying/{pid}` | Delete underlying line |
| POST | `/clients/{id}/programs/{tg}/excess/add` | Add excess layer (auto-creates policy) |
| POST | `/clients/{id}/programs/{tg}/umbrella/add` | Add umbrella (auto-creates policy) |
| PATCH | `/clients/{id}/programs/{tg}/excess/{pid}/cell` | Save excess cell |
| DELETE | `/clients/{id}/programs/{tg}/excess/{pid}` | Delete excess layer |
| GET | `/clients/{id}/programs/{tg}/preview` | Live schematic preview (HTMX partial) |

## Verification

1. Navigate to a client with tower data → Policies tab → click "Edit Program →" on a tower_group
2. Verify schematic entry page loads with two side-by-side tables and preview below
3. Add an underlying line → verify policy auto-created, row appears, preview updates
4. Edit cells (carrier, limit, deductible) → verify PATCH saves, formatting, preview re-renders
5. Add an umbrella → verify it appears at top of excess table with blue highlight
6. Add excess layers → verify attachment_point auto-calculated, notation renders
7. Add a co-insured layer → expand to see program_carriers sub-rows
8. Drag-reorder underlying lines → verify schematic_column updates, preview reflects new order
9. Delete a row → verify policy deleted, preview updates
10. Test LLM import with a document containing "Excess $10M xs $5M" → verify attachment_point extracted
11. Check completeness badge on client Policies tab → verify percentage and link
12. Generate chart deck with incomplete tower → verify warning banner appears
