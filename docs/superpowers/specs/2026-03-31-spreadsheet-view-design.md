# Spreadsheet View — Design Spec

**Date:** 2026-03-31
**Status:** Draft

## Context

PolicyDB currently displays policies across multiple fragmented views (client detail tab, dashboard pipeline, renewals page), each showing a subset of fields with limited inline editing. Users need a single, full-book, Excel-like spreadsheet view to rapidly review, edit, and input policy data across all fields — similar to working in a large editable spreadsheet.

This design also establishes a reusable spreadsheet component that can be extended to client and follow-up spreadsheet views in the future.

## Technology Choice

**Tabulator 6.3** via CDN (unpkg). Chosen over vanilla HTMX+JS for:
- Virtual scrolling (smooth performance at 500+ rows)
- Built-in column resize handles
- Built-in header filter row
- Built-in sort
- Reduced custom JS vs building all of this from scratch

**CDN includes:**
```html
<link rel="stylesheet" href="https://unpkg.com/tabulator-tables@6.3/dist/css/tabulator.min.css">
<script src="https://unpkg.com/tabulator-tables@6.3/dist/js/tabulator.min.js"></script>
```

---

## Architecture: Reusable Spreadsheet Component

### Shared Infrastructure (3 files)

**1. `_spreadsheet.html`** (Jinja2 partial template)
- Tabulator container `<div id="spreadsheet-grid">`
- Top bar: title, record count badge, action buttons (Add, Export XLSX)
- Marsh brand CSS overrides (see Theming section)
- `initSpreadsheet(config)` JS function

**2. `initSpreadsheet(config)` function**
Accepts a config object and creates the Tabulator instance:
```javascript
initSpreadsheet({
    el: "#spreadsheet-grid",        // container selector
    data: [...],                     // array of row objects (server-rendered JSON)
    columns: [...],                  // Tabulator column definitions
    frozenFields: ["client_name"],   // fields to freeze on left
    patchUrl: "/policies/{uid}/cell",// URL template for cell save
    idField: "policy_uid",           // row field used to build PATCH URL
    entityName: "policy",            // for UI labels ("234 policies")
    addRowUrl: "/policies/quick-add",// POST endpoint for new rows (null to disable)
    exportUrl: "/policies/spreadsheet/export", // GET endpoint for XLSX export
})
```

**3. Wrapper templates** (one per view)
Each spreadsheet view has a thin wrapper template that extends `base.html`, includes `_spreadsheet.html`, and passes the data + column config:
- `policies/spreadsheet.html` — policy spreadsheet
- (Future) `clients/spreadsheet.html` — client spreadsheet
- (Future) `followups/spreadsheet.html` — follow-up spreadsheet

### Per-View Route Pattern

Each route handler:
1. Queries the relevant data as a list of dicts
2. Builds the column definition list (field, title, editor type, editor params with config list values)
3. Renders the wrapper template with `data` and `columns` context

---

## Policy Spreadsheet View

### Route

`GET /policies/spreadsheet` — new route in `policies.py`

### Navigation

New "Spreadsheet" link in the app nav bar.

### Data Source

New query function `get_all_policies_for_grid(conn)`:
- All policies where `archived = 0` (includes opportunities)
- JOINs `clients.name AS client_name`, `clients.id AS client_id`
- Returns all editable fields plus `policy_uid`, `client_name`, `client_id`, `is_opportunity`
- Ordered by `client_name, policy_type, layer_position`

### Page Layout

- **Top bar:** "Policy Spreadsheet" title, "{N} policies" count badge, "+ Add Policy" button, "Export XLSX" button
- **Grid:** Full-width Tabulator instance filling remaining viewport height (`height: calc(100vh - <header height>)`)
- **No sidebar** — maximum horizontal space for the wide table

### Frozen Column

Client name only (column 1). Frozen with a `2px solid #003865` right border to visually separate from scrollable columns.

### Column Definitions

| Field | Display Title | Editor | Editor Params | Formatter | Header Filter |
|-------|--------------|--------|--------------|-----------|---------------|
| `client_name` | Client | `list` | Client name list from DB | Link to client page | Text input |
| `policy_uid` | UID | read-only | — | Link to policy page | Text input |
| `is_opportunity` | — | — | — | Opportunity left-border indicator | — |
| `policy_type` | Line of Business | `list` | `cfg.get("policy_types")` | — | Text input |
| `carrier` | Carrier | `list` | `cfg.get("carriers")` | — | Text input |
| `access_point` | Access Point | `input` | — | — | Text input |
| `policy_number` | Policy # | `input` | — | — | Text input |
| `effective_date` | Effective | `date` | — | MM/DD/YYYY | Date input |
| `expiration_date` | Expiration | `date` | — | MM/DD/YYYY | Date input |
| `premium` | Premium | `number` | `{precision:2}` | Currency `$X,XXX` | — |
| `limit_amount` | Limit | `number` | `{precision:2}` | Currency | — |
| `deductible` | Deductible | `number` | `{precision:2}` | Currency | — |
| `commission_rate` | Commission % | `number` | `{precision:2}` | Percentage | — |
| `prior_premium` | Prior Premium | `number` | `{precision:2}` | Currency | — |
| `renewal_status` | Status | `list` | `cfg.get("renewal_statuses")` | Status pill badge | Dropdown |
| `opportunity_status` | Opp Status | `list` | `cfg.get("opportunity_statuses")` | — | Dropdown |
| `follow_up_date` | Follow-Up | `date` | — | MM/DD/YYYY | Date input |
| `coverage_form` | Form | `list` | `cfg.get("coverage_forms")` | — | Text input |
| `layer_position` | Layer | `list` | `cfg.get("layer_positions")` | — | Text input |
| `project_name` | Location | `list` | Projects per client | — | Text input |
| `first_named_insured` | First Named Insured | `input` | — | — | Text input |
| `description` | Description | `input` | — | — | — |
| `notes` | Notes | `input` | — | — | — |
| `placement_colleague` | Placement Colleague | `input` | — | — | Text input |
| `underwriter_name` | Underwriter | `input` | — | — | Text input |
| `exposure_basis` | Exposure Basis | `input` | — | — | — |
| `exposure_amount` | Exposure Amount | `number` | `{precision:2}` | Currency | — |
| `exposure_address` | Address | `input` | — | — | — |
| `exposure_city` | City | `input` | — | — | — |
| `exposure_state` | State | `list` | US states list | — | Dropdown |
| `exposure_zip` | ZIP | `input` | — | — | — |
| `attachment_point` | Attachment Point | `number` | `{precision:2}` | Currency | — |
| `participation_of` | Participation | `number` | `{precision:2}` | Currency | — |

`is_opportunity` is not a visible column — it drives a row-level left border indicator (`3px solid #0B4BFF` on opportunity rows).

---

## Cell Save Flow

1. **Tabulator `cellEdited` event fires** with the Cell component
2. Extract `field = cell.getField()`, `value = cell.getValue()`, and `uid = cell.getRow().getData()[idField]`
3. Build PATCH URL from template: `/policies/{uid}/cell` → `/policies/POL-042/cell`
4. `fetch(url, {method: "PATCH", body: JSON.stringify({field, value})})`
5. **On success `{ok: true, formatted}`:** If `formatted` differs from current cell value, update via `cell.setValue(formatted, true)` (the `true` flag suppresses re-triggering `cellEdited`). Brief green flash on the cell.
6. **On error `{ok: false}` or network error:** Flash cell red, call `cell.restoreOldValue()` to revert.

### Currency Fields

The existing `policy_cell_save()` endpoint already calls `parse_currency_with_magnitude()` for currency fields. The `number` editor sends raw numbers; the endpoint returns the formatted value in `formatted`. The cell's display `formatter` function renders the value as `$X,XXX.XX`.

### Combobox (List) Fields

Tabulator's `list` editor with `autocomplete: true` and `freetext: true` allows typing to filter while still accepting values not in the list (matching the existing app behavior where users can type custom carriers, etc.).

### Client-Scoped Dropdowns

The `project_name` column needs options scoped to the row's client. Tabulator supports dynamic `editorParams` via a function:
```javascript
editorParams: function(cell) {
    var clientId = cell.getRow().getData().client_id;
    return { values: projectsByClient[clientId] || [], autocomplete: true, freetext: true };
}
```
The `projectsByClient` lookup is built server-side and passed as JSON alongside the main data.

---

## Add Row Flow

1. User clicks "+ Add Policy" button
2. Small modal/popover appears with a single combobox: **Select Client** (autocomplete from client list)
3. User selects client → POST to `/policies/quick-add` with `{client_id}`
4. Server creates a minimal policy record (auto-generates `policy_uid`, sets defaults) and returns the new row as JSON
5. `table.addRow(newRowData, false)` adds the row at the bottom
6. Tabulator scrolls to the new row and focuses the first editable cell (Line of Business)

---

## Export Flow

1. User clicks "Export XLSX" button
2. JS collects current header filter values and sort state
3. GET `/policies/spreadsheet/export?filters=...&sort=...` (query params encode the active filters)
4. Server re-runs the query with those filters applied, passes rows through `_write_sheet()` in `exporter.py`
5. Returns Marsh-branded XLSX as file download

This ensures exports always use the branded styling (navy headers, Noto Sans, alternating rows) rather than Tabulator's plain built-in export.

---

## Tabulator Theming (Marsh Brand CSS)

CSS overrides applied via a `<style>` block in `_spreadsheet.html`:

```
.tabulator .tabulator-header                 → background: #003865
.tabulator .tabulator-header .tabulator-col  → color: #FFFFFF; font: 600 11px 'Noto Sans'
.tabulator .tabulator-tableholder
  .tabulator-table .tabulator-row            → font: 12px 'Noto Sans'; color: #3D3C37
.tabulator .tabulator-row-even               → background: #F7F3EE
.tabulator .tabulator-cell                   → border-right: 1px solid #B9B6B1
.tabulator .tabulator-row                    → border-bottom: 1px solid #B9B6B1
.tabulator .tabulator-row.tabulator-selected → background: #CEECFF
.tabulator .tabulator-header-filter input    → border: 1px solid #ccc; border-radius: 3px
.tabulator .tabulator-frozen                 → border-right: 2px solid #003865
```

### Status Pill Formatter

Custom Tabulator `formatter` function for `renewal_status` column:
```javascript
function(cell) {
    var val = cell.getValue();
    var colors = {"Bound": ["#dcfce7","#166534"], "In Progress": ["#fef3c7","#92400e"], ...};
    var [bg, text] = colors[val] || ["#f3f4f6", "#4b5563"];
    return `<span style="background:${bg}; color:${text}; padding:1px 8px; border-radius:999px; font-size:11px;">${val}</span>`;
}
```

### Opportunity Row Indicator

Tabulator `rowFormatter` function:
```javascript
function(row) {
    if (row.getData().is_opportunity) {
        row.getElement().style.borderLeft = "3px solid #0B4BFF";
    }
}
```

---

## Keyboard Navigation

- **Tab:** Move to next editable cell in the row
- **Enter:** Confirm edit, move down to same column in next row
- **Escape:** Cancel edit, restore previous value

Configured via Tabulator's `tabEndNewRow: false` (Tab wraps to next row but does not create new rows — new rows are explicit via the button).

---

## Future Spreadsheet Views

The same `_spreadsheet.html` + `initSpreadsheet()` component supports:

### Client Spreadsheet (`/clients/spreadsheet`)
- Frozen: Client Name
- Columns: name, cn_number, industry_segment, account_exec, date_onboarded, website, fein, contacts count, policy count, total premium
- PATCH: `/clients/{id}/cell` (may need a new endpoint mirroring the policy pattern)
- Add: "+ Add Client" → POST to `/clients/quick-add`

### Follow-ups Spreadsheet (`/followups/spreadsheet`)
- Frozen: Client
- Columns: client_name, policy_type, subject, activity_type, follow_up_date, contact_person, disposition, details, duration_hours
- PATCH: `/activities/{id}/cell` (may need a new endpoint)
- Add: No — follow-ups are created through activity logging

These are not in scope for this build but the shared component should accommodate them without refactoring.

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/policydb/web/templates/policies/spreadsheet.html` | Create | Wrapper template for policy spreadsheet |
| `src/policydb/web/templates/_spreadsheet.html` | Create | Shared partial: grid container, top bar, CSS, `initSpreadsheet()` |
| `src/policydb/web/routes/policies.py` | Modify | Add `/policies/spreadsheet` GET route + `/policies/spreadsheet/export` GET route + `/policies/quick-add` POST route |
| `src/policydb/queries.py` | Modify | Add `get_all_policies_for_grid()` query |
| `src/policydb/web/templates/base.html` | Modify | Add "Spreadsheet" nav link |

---

## Verification

1. Navigate to `/policies/spreadsheet` — grid renders with all policies
2. Click a cell → editor opens (combobox for list fields, date picker for dates, text for inputs)
3. Edit a value, blur → PATCH fires, cell updates with server-formatted value, green flash
4. Edit a currency field with shorthand (`1m`) → cell displays `$1,000,000`
5. Type in a filter row → rows filter client-side instantly
6. Click column header → rows sort asc/desc
7. Scroll right → Client column stays frozen
8. Click "+ Add Policy" → modal, select client, new row appears
9. Click "Export XLSX" → downloads branded XLSX with only currently filtered/sorted rows
10. Opportunity rows show blue left border indicator
11. Resize a column by dragging the header border → column width changes
12. Test with 100+ policies → smooth virtual scrolling
