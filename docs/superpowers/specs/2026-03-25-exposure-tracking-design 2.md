# Exposure Tracking — Design Spec

## Context

Insurance premium analysis requires normalizing premiums against client exposures (payroll, revenue, TIV, vehicle count, etc.) to make apples-to-apples comparisons across years and clients. Currently policydb tracks per-policy exposure fields (`exposure_basis`, `exposure_amount`, `exposure_unit`) but has no client-level annual exposure tracking.

Brokers need to:
1. Track annual global exposures per client — with a mix of standard and industry-specific custom types
2. Report YoY exposure changes to clients ("your payroll is up 9.5%")
3. Reference the source document for each annual figure (for audit trail and renewal submissions)
4. Feed exposure data into presentation charts (normalized premiums, exposure trends)

The exposure tracking system follows policydb's existing **contacts pattern** — a flexible per-client collection using contenteditable matrix tables, where each client has a different mix of items.

Exposures exist at two levels:
- **Corporate-level** (default) — whole-account exposures shown on the client detail Exposures tab (`project_id IS NULL`)
- **Project-level** (optional) — independent exposures for specific smaller placements, shown on the project detail page (`project_id = {id}`). Most clients only need corporate-level.

## Architecture

### New Tab: Exposures

Added as a new tab on the client detail page, alongside Overview, Policies, Contacts, and Risk & Compliance.

- **Route:** `GET /clients/{client_id}/tab/exposures` — HTMX lazy-loaded tab
- **Pattern:** Identical to the contacts tab — `initMatrix()` JS infrastructure, contenteditable cells, PATCH-per-cell saves

### Schema: New Table

**`client_exposures`** — one row per exposure type per client per year:

```sql
CREATE TABLE client_exposures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,  -- NULL = corporate level
    exposure_type TEXT NOT NULL,          -- e.g., "Payroll", "Revenue", "Gallons Sold"
    is_custom INTEGER NOT NULL DEFAULT 0, -- 1 if user-created type, 0 if standard
    unit TEXT NOT NULL DEFAULT 'number',  -- 'currency' or 'number' — controls formatting
    year INTEGER NOT NULL,                -- e.g., 2026
    amount REAL,                          -- numeric value (contenteditable)
    source_document TEXT,                 -- e.g., "2025 Annual Report", "Q4 Payroll Summary"
    notes TEXT,                           -- freeform observation notes
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, project_id, exposure_type, year)
);
-- Index for corporate-level queries (project_id IS NULL)
CREATE INDEX idx_client_exposures_corporate ON client_exposures(client_id, year) WHERE project_id IS NULL;
-- Index for project-level queries
CREATE INDEX idx_client_exposures_project ON client_exposures(project_id, year) WHERE project_id IS NOT NULL;
```

**No separate exposure_types table.** Standard types are defined in `config.py`; custom types are freeform text in the `exposure_type` column. The "Previously Used" autocomplete list is derived by querying `SELECT DISTINCT exposure_type FROM client_exposures WHERE is_custom = 1`.

### Standard Exposure Types (in `config.py`)

Defined as a dict mapping type name to unit format:

```python
STANDARD_EXPOSURE_TYPES = {
    "Payroll": "currency",
    "Revenue": "currency",
    "TIV": "currency",
    "Vehicle Count": "number",
    "Employee Count": "number",
    "Square Footage": "number",
}
```

When adding a standard type, `unit` is auto-set from this dict. When adding a custom type, the user picks "currency" or "number" (default: "number").

### New Files

```
src/policydb/
├── migrations/
│   └── 085_client_exposures.sql        # New table (verify number at implementation time)
├── web/
│   ├── routes/
│   │   └── (clients.py — add exposure tab + PATCH routes)
│   └── templates/
│       └── clients/
│           ├── _tab_exposures.html      # Tab wrapper (year selector, add button, copy forward)
│           ├── _exposure_matrix.html    # Matrix table (contenteditable)
│           └── _exposure_matrix_row.html # Single editable row
```

### Modified Files

```
src/policydb/web/routes/clients.py     # Add: corporate exposure tab + PATCH routes
src/policydb/web/routes/projects.py    # Add: project-level exposure section (reuses same templates)
src/policydb/web/templates/clients/detail.html  # Add: Exposures tab button
src/policydb/queries.py                # Add: get_client_exposures(), get_exposure_years(), get_distinct_custom_types()
src/policydb/config.py                 # Add: STANDARD_EXPOSURE_TYPES dict
```

## User Flow

### Viewing Exposures

1. Navigate to client detail → click "Exposures" tab
2. Tab loads via HTMX: `GET /clients/{client_id}/tab/exposures`
3. Default year: current calendar year (e.g., 2026). If no data exists for any year, show empty state: "No exposures tracked yet" with "+ Add Exposure" button.
4. Year selector dropdown at top — populated from `get_exposure_years(conn, client_id)` plus the current calendar year (always included even if no data yet)
5. Selecting a year reloads the matrix via HTMX: `GET /clients/{client_id}/tab/exposures?year=2025`
6. Prior year column shows the year immediately before the selected year (selected_year - 1). If no data exists for that prior year, prior column shows "—" per row.

### Adding an Exposure

1. Click "+ Add Exposure" button
2. Dropdown fetched via HTMX: `GET /clients/{client_id}/exposures/types?year={selected_year}`
3. Dropdown has three sections:
   - **Standard:** Payroll, Revenue, TIV, Vehicle Count, Employee Count, Square Footage
   - **Previously Used:** custom types from across all clients (`SELECT DISTINCT exposure_type FROM client_exposures WHERE is_custom = 1`)
   - **+ Custom type...** at bottom — opens freeform text input with a unit selector ("currency" or "number")
4. Types already present for the selected year are **grayed out** in the response (server annotates them)
5. Selecting a type POSTs to `/clients/{client_id}/exposures/add-row` with form data:
   - `exposure_type` (required): the type name
   - `year` (required): the selected year
   - `is_custom` (required): 0 or 1
   - `unit` (required): "currency" or "number" — auto-set for standard types, user-chosen for custom
6. Server creates the row, returns the rendered `_exposure_matrix_row.html` partial
7. New row inserted into tbody with focus on the amount cell

### Editing a Cell

Follows the contacts `initMatrix()` pattern:
1. Click a contenteditable cell (amount, source_document, or notes)
2. Edit inline
3. On blur/Enter: PATCH to `/clients/{client_id}/exposures/{exposure_id}/cell` with JSON body:
   ```json
   {"field": "amount", "value": "52800000"}
   ```
4. **Allowed fields:** `{"amount", "source_document", "notes"}`
5. **Validation per field:**
   - `amount`: strip `$`, commas, whitespace → parse to float. Reject non-numeric input with `{"ok": false, "error": "Invalid number"}`.
   - `source_document`: plain text, trimmed
   - `notes`: plain text, trimmed
6. **Formatting:** Server formats `amount` based on the row's `unit` field — `display.py` currency helpers for "currency", comma-separated integer for "number".
7. **Response:** `{"ok": true, "formatted": "$52,800,000", "yoy": "+9.5%", "yoy_direction": "up"}` — includes the recalculated YoY delta.
8. **DOM update:** JS callback in `initMatrix` updates the formatted cell text and finds the sibling YoY delta cell (identified by `data-field="yoy"` on the same row) to update its text content and CSS class (green for down, red for up).

### Copy Forward

1. Click "Copy Forward from {prior_year}" button
2. POSTs to `/clients/{client_id}/exposures/copy-forward` with form data: `from_year`, `to_year`
3. Server uses `INSERT OR IGNORE` — copies exposure rows (type, is_custom, unit) from source year to target year with `amount = NULL`, `source_document = NULL`, `notes = NULL`. Rows that already exist in the target year are skipped (UNIQUE constraint).
4. Returns refreshed matrix via HTMX swap with a toast: "Copied {N} of {M} types ({skipped} already existed)" or "All {M} types copied"
5. **Edge cases:**
   - Source year has zero rows → toast: "No exposures to copy from {year}"
   - All types already exist in target → toast: "All types already exist in {year}"
   - User clicks twice → second call is a no-op (INSERT OR IGNORE), safe

### Deleting a Row

1. Click × on a row
2. Uses `hx-delete` attribute: `DELETE /clients/{client_id}/exposures/{exposure_id}`
3. Server deletes the row, returns empty response
4. Row removed via HTMX `hx-swap="outerHTML"` (row replaces itself with nothing)
5. If last row deleted, show empty state message

## Data Display

### Matrix Table Columns

| Column | Editable | data-field | Description |
|--------|----------|------------|-------------|
| Exposure Type | No (set on creation) | — | Standard or custom type name |
| Prior Year (N-1) | No (read-only) | — | Value from selected_year - 1. Shows "—" if no prior data. |
| Current Year (N) | Yes (contenteditable) | `amount` | The value being entered/updated |
| YoY Δ | No (auto-calculated) | `yoy` | `(current - prior) / prior × 100`. Shows "—" if prior is 0, NULL, or missing. |
| Source Document | Yes (contenteditable) | `source_document` | Reference to where the number came from |
| Notes | Yes (contenteditable) | `notes` | Freeform observation |

### Amount Formatting

- **"currency" unit types** (Payroll, Revenue, TIV): formatted with `$` and commas via existing `display.py` currency helpers
- **"number" unit types** (Vehicle Count, Employee Count, Square Footage): formatted with commas, no `$`
- **Custom types:** `unit` is set on creation (user picks "currency" or "number")

### Key Observations Panel

Below the matrix table, an auto-generated panel shows YoY changes sorted by absolute % change (highest first):

- **Color bands:** Red (>15%), Orange (>8%), Blue (>5%), Green (≤5%)
- Each observation shows: "{Type} up/down {X}%" + notes from the row (if any)
- **Rendering:** Server-rendered — the tab route calculates changes from `client_exposures` for selected_year vs selected_year - 1 and passes them as template context
- **Empty state:** If no prior year data exists, panel shows "No prior year data for comparison"

## Project-Level Exposures

For smaller standalone placements, exposures can optionally be tracked at the project level. This uses the same `client_exposures` table with `project_id` set.

### UI Location
- **Project detail page** — an "Exposures" section (not a tab, since projects are simpler) using the same `_exposure_matrix.html` and `_exposure_matrix_row.html` templates
- Templates are parameterized: they accept either `client_id` alone (corporate) or `client_id + project_id` (project-level)

### Routes (on projects router)
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/projects/{project_id}/exposures` | Load project exposure matrix (HTMX partial) |
| POST | `/projects/{project_id}/exposures/add-row` | Add row (same form fields + `project_id`) |
| PATCH | `/projects/{project_id}/exposures/{id}/cell` | Update cell (same pattern) |
| DELETE | `/projects/{project_id}/exposures/{id}` | Remove row |
| POST | `/projects/{project_id}/exposures/copy-forward` | Copy types from prior year |

### Query Functions
- `get_client_exposures(conn, client_id, year, project_id=None)` — when `project_id` is None, filters to `WHERE project_id IS NULL` (corporate); when set, filters to that project
- Same function signature, different filter — no duplication

### Chart Integration
- Chart deck builder can show corporate or project-level exposures depending on context
- When building a deck for a project-specific placement, use `project_id` to scope the exposure data

## Integration with Chart Deck Builder

The exposure data enables new chart types in the deck builder (defined in the companion spec `2026-03-25-chart-deck-builder-design.md`). **Chart integration is out of scope for this spec** — it will be implemented when building the chart deck builder, which will add these functions to `charts.py`:

1. **Exposure Trend Chart** — multi-year line chart of exposure values by type
2. **Normalized Premium Chart** — premium per $M payroll, per $M revenue, etc.
3. **Key Observations Slide** — the observations panel rendered as a presentation-ready visual
4. **Exposure vs Premium** — dual-axis comparing exposure growth vs premium growth

## Routes Summary

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/clients/{client_id}/tab/exposures` | Load exposures tab (HTMX partial). Params: `year` (optional, default: current calendar year) |
| GET | `/clients/{client_id}/exposures/types?year={y}` | Dropdown data: standard + previously used types, annotated with `disabled: true` for types already present in the given year |
| POST | `/clients/{client_id}/exposures/add-row` | Add new exposure row. Form: `exposure_type`, `year`, `is_custom`, `unit` |
| PATCH | `/clients/{client_id}/exposures/{id}/cell` | Update single cell. JSON: `{"field": "...", "value": "..."}`. Allowed: `amount`, `source_document`, `notes` |
| DELETE | `/clients/{client_id}/exposures/{id}` | Remove exposure row (hx-delete) |
| POST | `/clients/{client_id}/exposures/copy-forward` | Copy types from prior year. Form: `from_year`, `to_year`. Uses INSERT OR IGNORE. |

## Verification

1. Start policydb: `pdb serve`
2. Navigate to a client → Exposures tab
3. Verify empty state shows when no data exists, with current calendar year selected
4. Add standard exposure types (Payroll, Revenue) — verify dropdown works, unit auto-set to "currency"
5. Add a custom type (e.g., "Gallons Sold") with unit "number" — verify freeform input
6. Verify already-added types are grayed out in the dropdown
7. Enter amounts — verify contenteditable saves on blur, currency formatting applied correctly
8. Enter source documents and notes — verify save
9. Verify YoY Δ auto-calculates when prior year value exists, shows "—" when it doesn't
10. Verify YoY delta cell updates in-place after amount save (no page reload)
11. Verify Key Observations panel sorts by % change with correct color bands
12. Test Copy Forward — create 2026 from 2025, verify types copy with blank amounts
13. Test Copy Forward twice — verify second call is a no-op with appropriate toast
14. Test Copy Forward with no source data — verify "No exposures to copy" message
15. Verify previously used custom types appear in the dropdown for a different client
16. Verify × delete removes a row, shows empty state when last row deleted
17. Switch year selector — verify prior year column updates correctly
