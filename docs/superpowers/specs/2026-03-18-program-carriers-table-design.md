# Program Carriers Table — Design Spec

**Date:** 2026-03-18
**Status:** Draft
**Scope:** New `program_carriers` table, reconciler integration, UI overhaul, design system documentation

---

## Problem Statement

Programs in PolicyDB currently store participating carrier information as a comma-separated text field (`program_carriers`) on the `policies` table. This creates three problems:

1. **Weak reconciler matching** — the reconciler can only do substring matching against the text field (+15 bonus), missing the powerful +30 policy number bonus available for structured data
2. **Lost import detail** — batch-creating a program from reconcile aggregates premium/limit but discards per-carrier policy numbers, premiums, and limits
3. **Unstructured UI** — the policy edit page uses a plain textarea for carrier entry, inconsistent with the contenteditable matrix pattern used elsewhere in the app

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Per-carrier fields | Carrier, Policy #, Premium, Limit | Enough for reconciler matching + financial visibility without duplicating full policy records |
| Child policy creation | Not created | Programs aggregate; carrier rows in the table ARE the detail. One-click break-out can be added later |
| Reconcile diff handling | Per-carrier accept/reject | Consistent with existing DIFF pattern on regular policies |
| Text field deprecation | Full deprecation | Minimal existing program data makes this low-risk. Single source of truth from day one |
| Carrier matrix columns | Carrier, Policy #, Premium, Limit (4 cols) | Programs share dates/status at the program level; per-carrier dates would indicate a separate policy |

---

## 1. Schema

### New Table: `program_carriers`

```sql
CREATE TABLE IF NOT EXISTS program_carriers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id    INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    carrier       TEXT NOT NULL DEFAULT '',
    policy_number TEXT DEFAULT '',
    premium       REAL DEFAULT 0,
    limit_amount  REAL DEFAULT 0,
    sort_order    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_program_carriers_program ON program_carriers(program_id);
```

**Migration file:** `src/policydb/migrations/058_program_carriers_table.sql`

**Migration logic:**
1. Create the table
2. Parse any existing `program_carriers` text into rows (safety net — minimal data expected)
3. The `program_carriers` and `program_carrier_count` columns remain on `policies` but are no longer read or written by application code

### Deprecation of Text Fields

The following columns on `policies` are deprecated (kept in schema, ignored in code):
- `program_carriers` (TEXT) — replaced by `program_carriers` table
- `program_carrier_count` (INTEGER) — replaced by `SELECT COUNT(*) FROM program_carriers WHERE program_id = ?`

**Files that currently read/write these fields (all must be updated):**

| File | Current Usage | Change |
|------|--------------|--------|
| `src/policydb/views.py` | `v_policy_status` selects `program_carriers`, `program_carrier_count`; `v_schedule` uses `program_carriers` for carrier display; `v_client_summary` counts `program_carrier_count` | Replace with JOINs/subqueries against `program_carriers` table |
| `src/policydb/reconciler.py` | Substring match on `program_carriers` text (+15 bonus) | Query `program_carriers` table for structured matching |
| `src/policydb/web/routes/policies.py` | Reads/writes `program_carriers` text and `program_carrier_count` on create/edit | Read/write `program_carriers` table rows instead |
| `src/policydb/web/routes/reconcile.py` | Batch create writes `program_carriers` text | Insert rows into `program_carriers` table |
| `src/policydb/web/routes/clients.py` | Selects `program_carriers`, `program_carrier_count` for client detail | Query `program_carriers` table, attach as list to each program dict |
| `src/policydb/web/templates/policies/edit.html` | Textarea for `program_carriers`, number input for count | Contenteditable matrix (see UI section) |
| `src/policydb/web/templates/policies/new.html` | Textarea for `program_carriers` on new policy form | Same contenteditable matrix pattern |
| `src/policydb/web/templates/clients/_programs.html` | Displays comma-separated text | Structured carrier rows nested under each program |
| `src/policydb/web/templates/reconcile/_create_form.html` | Textarea for `program_carriers` in single-create form | Input fields per carrier or simplified entry |
| `src/policydb/exporter.py` | Reads `program_carriers` for export | Query table, join carrier names with comma for export output |

---

## 2. API Endpoints

### New Endpoints

**`PATCH /policies/{policy_uid}/program-carrier/{carrier_id}`**
- Updates a single cell in the carrier matrix (carrier, policy_number, premium, limit_amount)
- Request: `{"field": "premium", "value": "350000"}`
- Response: `{"ok": true, "formatted": "$350,000"}`
- Saves on blur from contenteditable cell

**`POST /policies/{policy_uid}/program-carrier`**
- Adds a new carrier row to the program
- Request: `{"carrier": "", "policy_number": "", "premium": 0, "limit_amount": 0}`
- Response: HTML partial of the new `<tr>` for HTMX swap
- Triggered by "+ Add Carrier" button

**`DELETE /policies/{policy_uid}/program-carrier/{carrier_id}`**
- Removes a carrier row from the program
- Response: `{"ok": true}`

**`POST /policies/{policy_uid}/program-carrier/reorder`**
- Updates `sort_order` for all carrier rows
- Request: `{"order": [3, 1, 4, 2]}` (list of carrier IDs in new order)
- Response: `{"ok": true}`

### Modified Endpoints

**`POST /reconcile/batch-create-program`**
- Currently: creates one policy with `program_carriers` text and `program_carrier_count`
- New: creates one policy with `is_program=1`, then inserts one `program_carriers` row per selected import row with carrier, policy_number, premium, limit_amount populated from the import data
- Program-level `premium` and `limit_amount` are the SUM of all carrier rows
- Program-level `carrier` is set to the first carrier (lead carrier)

**`POST /reconcile/apply-field/{policy_uid}`** (for program DIFF rows)
- Currently: updates a single field on the policy
- New: when the policy is a program and the field targets a carrier row, updates the `program_carriers` table row instead
- Request includes `carrier_row_id` to identify which carrier row to update

**`POST /reconcile/create`** (single policy create from reconcile)
- When `is_program=1`, also accepts carrier detail to insert into `program_carriers` table

---

## 3. Reconciler Changes

### Structured Matching (replaces substring matching)

**Current** (`reconciler.py:654-657`):
```python
elif db.get("is_program") and db.get("program_carriers"):
    if ext_carrier.strip().lower() in db["program_carriers"].lower():
        combined += 15
```

**New:**
```python
elif db.get("is_program") and db.get("_program_carrier_rows"):
    for pc in db["_program_carrier_rows"]:
        # Carrier name match
        if fuzz.WRatio(ext_carrier, pc.get("carrier", "")) >= 70:
            combined += 10
            # Policy number match within that carrier row
            pc_pn = _normalize_policy_number(pc.get("policy_number") or "")
            if ext_pn and pc_pn:
                if ext_pn == pc_pn:
                    combined += 30
                elif fuzz.ratio(ext_pn, pc_pn) >= 90:
                    combined += 25
                elif fuzz.ratio(ext_pn, pc_pn) >= 75:
                    combined += 10
            break  # matched a carrier row, stop
```

**Key changes:**
- `_program_carrier_rows` is pre-loaded and attached to each program DB row before matching begins
- Carrier name uses fuzzy WRatio (>=70) for +10, same as regular carrier matching
- Policy number uses the same graduated scoring as regular policies (+30/+25/+10)
- Combined bonus is up to +40 (carrier + exact policy number) vs old +15
- `break` after first matching carrier row — one imported row matches one carrier entry

### Pre-loading Carrier Rows

Before calling `reconcile()`, the route loads carrier rows for all program policies:

```python
program_ids = [r["id"] for r in db_rows if r.get("is_program")]
carrier_rows = conn.execute(
    "SELECT * FROM program_carriers WHERE program_id IN ({})".format(
        ",".join("?" * len(program_ids))
    ), program_ids
).fetchall()
# Group by program_id
carrier_map = {}
for cr in carrier_rows:
    carrier_map.setdefault(cr["program_id"], []).append(dict(cr))
# Attach to db_rows
for r in db_rows:
    if r.get("is_program"):
        r["_program_carrier_rows"] = carrier_map.get(r["id"], [])
```

### Per-Carrier Diff Tracking

When a match is found between an imported row and a program, the reconciler identifies WHICH carrier row it matched and stores the mapping:

```python
row = ReconcileRow(status, ext, db, diff_fields, score,
                   cosmetic_diffs=cosmetic,
                   is_program_match=True,
                   matched_carrier_id=matched_pc["id"])  # NEW field
```

This enables the UI to show per-carrier accept/reject buttons.

### Enhanced Program Summary

`program_reconcile_summary()` now returns per-carrier detail:

```python
{
    "POL-2025-001": {
        "policy_type": "Property Program",
        "total_premium": 1245000,
        "matched_premium": 1245000,
        "matched_count": 4,
        "carrier_count": 4,
        "fully_reconciled": True,
        "carrier_detail": [
            {"carrier": "AIG", "db_premium": 350000, "ext_premium": 350000, "status": "MATCH"},
            {"carrier": "Chubb", "db_premium": 425000, "ext_premium": 460000, "status": "DIFF"},
            ...
        ],
        "new_carriers": [
            {"carrier": "Hartford", "policy_number": "HFD-20260401", "premium": 180000, "limit": 5000000}
        ]
    }
}
```

---

## 4. UI Implementation

### 4a. Policy Edit Page — Program Carriers Matrix

**Replaces:** Textarea for `program_carriers` + number input for `program_carrier_count` (lines 336-347 of `edit.html`)

**New component:** Contenteditable table following the app's matrix pattern.

**Structure:**
```
┌──────────────────────────────────────────────────────────────────┐
│ Participating Carriers                    4 carriers · $1.25M   │
├────┬────────────────┬───────────────┬────────────┬──────────────┤
│ ⠿  │ Carrier        │ Policy #      │    Premium │        Limit │
├────┼────────────────┼───────────────┼────────────┼──────────────┤
│ ⠿  │ AIG            │ POL-2025-4481 │   $350,000 │   $5,000,000 │
│ ⠿  │ Chubb          │ CHB-88912     │   $425,000 │  $10,000,000 │
│ ⠿  │ Zurich         │ ZNA-003312    │   $275,000 │   $5,000,000 │
│ ⠿  │ Liberty Mutual │ LM-91204      │   $195,000 │   $2,000,000 │
├────┴────────────────┴───────────────┼────────────┼──────────────┤
│ + Add Carrier                       │ $1,245,000 │  $22,000,000 │
└─────────────────────────────────────┴────────────┴──────────────┘
```

**Interactions:**
- Click cell to edit — blue bottom-border highlight on focused cell
- Tab advances to next cell; Tab on last cell of last row adds a new blank row
- Blur saves via `PATCH /policies/{uid}/program-carrier/{id}` with `{"field": "...", "value": "..."}`
- Server returns `{"ok": true, "formatted": "..."}` — `flashCell()` if formatted differs from raw input
- Empty cells show placeholder text via `data-placeholder` and `::before` CSS
- Drag handle `⠿` for reorder (HTML5 draggable), saves via POST to reorder endpoint
- Delete via right-click context menu or row-level action
- `+ Add Carrier` button below table (carries `no-print` class)
- Summary line above table shows carrier count and total premium (auto-updated on cell save)
- Footer row shows column totals for premium and limit

**HTMX pattern:**
- New row: `hx-post="/policies/{uid}/program-carrier"` → swaps new `<tr>` into `<tbody>`
- Cell save: vanilla `fetch()` PATCH on blur (same as other matrix controllers in the app)
- Reorder: drag end fires POST with new order array

### 4b. Client Detail — Programs Card

**Replaces:** Current `_programs.html` that shows `program_carriers` text as a tooltip/subtitle

**New structure:** Each program row expands to show a nested carrier detail table:

```
┌──────────────────────────────────────────────────────────────────┐
│ Corporate Programs · 2 programs · $2,450,000 total premium      │
├────┬────────────────┬──────────┬───────────┬──────────┬─────────┤
│    │ Program        │ Lead     │     Limit │  Premium │ Status  │
├────┼────────────────┼──────────┼───────────┼──────────┼─────────┤
│ PGM│ Corp Property  │ AIG      │     $22M  │  $1.25M  │ Bound   │
│    │  ├ AIG         │ POL-4481 │       $5M │   $350K  │         │
│    │  ├ Chubb       │ CHB-889  │      $10M │   $425K  │         │
│    │  ├ Zurich      │ ZNA-003  │       $5M │   $275K  │         │
│    │  └ Liberty M.  │ LM-912   │       $2M │   $195K  │         │
├────┼────────────────┼──────────┼───────────┼──────────┼─────────┤
│ PGM│ Corp Casualty  │ Travelers│     $15M  │  $1.20M  │ In Prog │
│    │  └ (3 carriers)│          │           │          │         │
└────┴────────────────┴──────────┴───────────┴──────────┴─────────┘
```

**Key changes:**
- Carrier rows are fetched from `program_carriers` table (not parsed from text)
- Nested rows show carrier, policy_number, premium, limit with indentation
- Lead carrier is derived from `program_carriers` row with `sort_order = 0` (or first row)
- Carrier count badge computed from `COUNT(*)` on the table
- Programs without carrier rows show "(no carriers)" in subtitle
- Abbreviated currency for nested rows (e.g., `$350K`, `$5M`) to save space
- Collapsed by default if more than 4 carriers; expandable via click

### 4c. Reconcile Batch Create — Program Flow

**Replaces:** Current `_batch_create_review.html` Option 2 section

**New flow:**
1. User selects MISSING rows with checkboxes (existing behavior)
2. User clicks "Create Program from Selected" (existing button)
3. **New preview panel** replaces the inline input — shows:
   - Program name field (editable, defaults to common policy_type across selected rows)
   - Client field (auto-matched, same as current)
   - Term fields (effective/expiration from selected rows)
   - **Carrier preview table** showing what will become `program_carriers` rows:
     - Carrier | Policy # | Premium | Limit | Source Row
   - Totals row
   - "Create Program" and "Cancel" buttons
4. On submit, POST creates program + inserts carrier rows
5. Response confirms creation with link to program edit page

### 4d. Reconcile Results — Program Carrier-Level Diffs

**New pattern for program DIFF display:**

When reconcile finds matches against a program's carrier rows, the detail expansion shows per-carrier comparison instead of aggregate-only:

```
┌──────────────────────────────────────────────────────────────────┐
│ Corporate Property [PROGRAM]          Acme Holdings Inc.         │
├───────────────┬───────────────┬───────────┬───────────┬─────────┤
│ Carrier       │ Policy #      │ DB Prem   │ Imp Prem  │ Action  │
├───────────────┼───────────────┼───────────┼───────────┼─────────┤
│ AIG           │ POL-2025-4481 │  $350,000 │  $350,000 │ ✓ Match │
│ Chubb (DIFF)  │ CHB-88912     │  $425,000 │  $460,000 │ Accept  │
│ Zurich        │ ZNA-003312    │  $275,000 │  $275,000 │ ✓ Match │
│ Liberty Mutual│ LM-91204      │  $195,000 │  $195,000 │ ✓ Match │
│ Hartford (NEW)│ HFD-20260401  │     —     │  $180,000 │ + Add   │
├───────────────┴───────────────┼───────────┼───────────┼─────────┤
│ Program Total                 │$1,245,000 │$1,460,000 │         │
└───────────────────────────────┴───────────┴───────────┴─────────┘
```

**Actions:**
- **Match rows:** Green check, no action needed
- **Diff rows:** "Accept" updates the `program_carriers` row with import value; "Keep" retains DB value
- **New carrier rows:** "+ Add" inserts a new row into `program_carriers` table with import data
- **Missing carrier rows:** (carrier in DB but not in import) shown with amber "(NOT IN IMPORT)" label — user decides whether to remove or keep
- Accept/Keep buttons use HTMX POST to `PATCH /policies/{uid}/program-carrier/{id}` with the import value

---

## 5. Design System — Visual Patterns for Policy Views

This section documents the visual patterns established in this spec so they can be applied consistently across all policy views in a follow-up pass.

### 5a. Table Structure

**Outer container:**
```html
<details open class="card mb-4 overflow-hidden">
  <summary class="px-4 py-2.5 bg-{color}-50 border-b border-{color}-100 cursor-pointer select-none list-none flex items-center gap-2 hover:bg-{color}-100 transition-colors">
    <span class="text-xs text-{color}-400 details-arrow">▶</span>
    <span class="text-xs font-bold text-marsh uppercase tracking-wide">{Section Title}</span>
    <span class="text-xs text-gray-400">· {count} items · {total} total</span>
  </summary>
  <div class="overflow-x-auto">
    <table class="w-full text-sm"> ... </table>
  </div>
</details>
```

**Header row:**
```html
<thead>
  <tr class="border-b border-gray-100 text-left text-xs text-gray-400">
    <th class="px-4 py-2 font-medium">{Column}</th>
    <!-- Right-align currency columns -->
    <th class="px-4 py-2 font-medium text-right">{Currency Column}</th>
  </tr>
</thead>
```

**Data row:**
```html
<tr class="border-b border-gray-50 hover:bg-gray-50 transition-colors">
  <td class="px-4 py-2.5 text-gray-600">{value}</td>
  <!-- Currency values -->
  <td class="px-4 py-2.5 text-right font-medium text-gray-900 tabular-nums">{currency}</td>
</tr>
```

**Nested/child row** (indented under parent):
```html
<tr class="border-b border-gray-50 bg-blue-50/30">
  <td class="px-4 py-1.5 pl-8">
    <span class="text-[9px] text-gray-300">└</span>
  </td>
  <td class="px-4 py-1.5 text-xs text-gray-500">{child value}</td>
</tr>
```

### 5b. Contenteditable Matrix Pattern

**Cell (display state):**
```html
<td class="px-3 py-2 text-sm text-gray-800"
    contenteditable="true"
    data-field="{field_name}"
    data-id="{row_id}"
    data-placeholder="{placeholder text}"
    data-endpoint="/api/endpoint/{id}">
  {value}
</td>
```

**Cell CSS:**
```css
/* Placeholder for empty cells */
td[contenteditable][data-placeholder]:empty::before {
  content: attr(data-placeholder);
  color: #94a3b8;  /* gray-400 */
  font-style: italic;
  pointer-events: none;
}

/* Focused cell — bottom border highlight, no full box border */
td[contenteditable]:focus {
  outline: none;
  border-bottom: 2px solid #3b82f6;  /* blue-500 — brand color */
  background: rgba(59, 130, 246, 0.03);  /* barely visible blue tint */
}
```

**Cell save JS pattern:**
```javascript
cell.addEventListener('blur', function() {
  var raw = this.textContent.trim();
  var field = this.dataset.field;
  var id = this.dataset.id;
  var endpoint = this.dataset.endpoint;

  fetch(endpoint, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({field: field, value: raw})
  })
  .then(r => r.json())
  .then(data => {
    if (data.ok && data.formatted !== raw) {
      this.textContent = data.formatted;
      flashCell(this);
    }
  });
});
```

**`flashCell` helper** (green fade on server-reformatted values):
```javascript
function flashCell(el) {
  el.style.transition = 'background-color 0.3s ease';
  el.style.backgroundColor = '#d1fae5';  /* green-100 */
  setTimeout(function() {
    el.style.backgroundColor = '';
    setTimeout(function() { el.style.transition = ''; }, 300);
  }, 800);
}
```

**Tab navigation:**
```javascript
cell.addEventListener('keydown', function(e) {
  if (e.key === 'Tab') {
    e.preventDefault();
    var cells = Array.from(this.closest('table').querySelectorAll('td[contenteditable]'));
    var idx = cells.indexOf(this);
    if (idx === cells.length - 1) {
      // Last cell — trigger add-row, then focus first cell of new row
      addRow().then(function(newRow) {
        newRow.querySelector('td[contenteditable]').focus();
      });
    } else {
      cells[idx + 1].focus();
    }
  }
});
```

### 5c. Drag-to-Reorder Pattern

```html
<td class="px-2 py-2 text-gray-400 cursor-grab no-print" draggable="true"
    ondragstart="dragStart(event)" ondragover="dragOver(event)" ondrop="drop(event)">
  ⠿
</td>
```

```javascript
var dragRow = null;
function dragStart(e) { dragRow = e.target.closest('tr'); }
function dragOver(e) { e.preventDefault(); }
function drop(e) {
  e.preventDefault();
  var target = e.target.closest('tr');
  if (target && dragRow !== target) {
    var tbody = target.closest('tbody');
    tbody.insertBefore(dragRow, target);
    // Collect new order and save
    var order = Array.from(tbody.querySelectorAll('tr')).map(function(tr) {
      return parseInt(tr.dataset.id);
    });
    fetch(reorderEndpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({order: order})
    });
  }
}
```

### 5d. Add Row Pattern

```html
<tfoot>
  <tr>
    <td colspan="{n}" class="px-3 py-2">
      <button type="button" class="no-print text-xs text-gray-400 border border-dashed border-gray-300 px-3 py-1 rounded hover:border-gray-400 hover:text-gray-600 transition-colors"
              onclick="addCarrierRow(this)">
        + Add {Row Type}
      </button>
    </td>
    <!-- Totals columns -->
    <td class="px-3 py-2 text-right text-xs font-semibold text-gray-500 border-t border-gray-200 tabular-nums">{total}</td>
  </tr>
</tfoot>
```

### 5e. Status Badges

```html
<!-- Bound / positive -->
<span class="text-xs px-2 py-0.5 rounded bg-green-50 text-green-700">{status}</span>

<!-- In Progress / active -->
<span class="text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700">{status}</span>

<!-- Pending / warning -->
<span class="text-xs px-2 py-0.5 rounded bg-amber-50 text-amber-700">{status}</span>

<!-- Default / neutral -->
<span class="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-600">{status}</span>

<!-- Program badge -->
<span class="bg-blue-100 text-blue-700 text-[10px] font-bold px-1.5 py-0.5 rounded">PGM</span>
```

### 5f. Diff Display Pattern (for reconcile)

```html
<!-- Match — green check -->
<td class="text-center text-green-500">✓ Match</td>

<!-- Diff — strikethrough old, bold new, with action buttons -->
<td class="text-right text-red-400 line-through tabular-nums">{old_value}</td>
<td class="text-right text-green-500 font-semibold tabular-nums">{new_value}</td>
<td class="text-center">
  <button class="text-xs bg-blue-600 text-white px-2 py-0.5 rounded hover:bg-blue-700">Accept</button>
  <button class="text-xs text-gray-500 px-2 py-0.5 hover:text-gray-700">Keep</button>
</td>

<!-- New carrier row — green background tint -->
<tr class="bg-green-950/20">
  <td class="text-green-400 italic">{carrier} <span class="text-green-500 text-[10px]">(NEW)</span></td>
  ...
  <td><button class="text-xs bg-green-700 text-green-100 px-2 py-0.5 rounded">+ Add</button></td>
</tr>

<!-- Missing from import — amber warning -->
<tr class="bg-amber-950/20">
  <td class="text-amber-400">{carrier} <span class="text-amber-500 text-[10px]">(NOT IN IMPORT)</span></td>
</tr>
```

### 5g. Currency Formatting Rules

| Context | Format | Example |
|---------|--------|---------|
| Full table cells, edit forms | Full with commas | `$1,245,000` |
| Nested/child rows (space-constrained) | Abbreviated | `$350K`, `$1.25M` |
| Summary/header lines | Abbreviated | `$2.45M total premium` |
| Totals rows | Full with commas | `$1,245,000` |

### 5h. Print Safety

All interactive controls carry `no-print` class:
```css
@media print {
  .no-print { display: none !important; }
}
```

Elements that must carry `no-print`:
- Add row buttons
- Drag handles (⠿)
- Action buttons (Accept/Keep/Add/Delete)
- Checkboxes in batch views
- Tooltips and popovers

### 5i. Views Requiring Consistency Pass (Follow-Up Spec)

The following views should be updated to match these patterns in a separate implementation:

| View | Template | Current State | Target |
|------|----------|--------------|--------|
| Dashboard pipeline rows | `_pipeline_table.html`, `_policy_dash_row.html` | Mixed input/display | Contenteditable cells where applicable |
| Renewal pipeline | `renewals.html`, `_policy_renew_row.html` | Row edit partial swap | Consistent cell styling, status badges |
| Client detail policy list | `clients/detail.html`, `_table_rows.html` | Basic table | Nested structure, consistent currency format |
| Policy row edit partials | `_policy_row_edit.html`, `_policy_dash_row_edit.html`, `_policy_renew_row_edit.html` | Form inputs in cells | Contenteditable cells with PATCH save |
| Schedule of insurance | `v_schedule` view | Text-based | Consistent table structure |
| Follow-ups table | `followups.html`, `_row.html` | Basic rows | Consistent styling |
| Reconcile results | `_results_table.html` | Existing diff pattern | Enhanced with per-carrier program diffs |

---

## 6. Data Flow Summary

### Batch Create Program (from Reconcile)
```
User selects MISSING rows → Preview panel shows carrier detail
→ POST /reconcile/batch-create-program
→ INSERT policies (is_program=1, premium=SUM, limit=SUM, carrier=first)
→ INSERT program_carriers (one row per selected import row)
→ Response: confirmation with link to program edit page
```

### Edit Program Carriers (Policy Edit Page)
```
User clicks cell → contenteditable activates
→ User types → blur fires
→ PATCH /policies/{uid}/program-carrier/{id} {field, value}
→ Server formats, saves, returns {ok, formatted}
→ JS updates cell text, flashCell() if reformatted
→ Summary line (count + total premium) auto-updates
```

### Reconcile Existing Program
```
Import uploaded → reconciler loads program_carriers rows
→ Each import row matched against carrier rows (carrier name + policy number)
→ Results grouped by program with per-carrier status (MATCH/DIFF/NEW/MISSING)
→ UI shows expandable program section with per-carrier comparison
→ Accept/Keep per carrier row → PATCH updates program_carriers table
→ + Add for new carriers → INSERT into program_carriers table
```

---

## 7. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Program with zero carrier rows | Shows "No carriers yet" with just the "+ Add Carrier" button |
| Duplicate carrier name in program | Allowed — same carrier can participate multiple times (e.g., different layers) |
| Delete last carrier row | Allowed — program becomes empty but remains valid |
| Carrier with $0 premium | Allowed — carrier may be participating without direct premium (e.g., fronting) |
| Import row matches program carrier AND a standalone policy | Program carrier match takes precedence if score is higher |
| Program deleted | CASCADE delete removes all `program_carriers` rows automatically |
| Export with programs | Comma-join carrier names from table for backward-compatible export format |
