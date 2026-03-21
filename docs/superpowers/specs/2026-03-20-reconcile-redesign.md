# Reconcile System Redesign

**Date:** 2026-03-20
**Status:** Approved
**Scope:** Algorithm rewrite, Pairing Board UI, normalization unification, data prep layer

## Problem Statement

The reconcile system has fundamental matching issues:

- **False negatives**: Client name hard gate (WRatio >= 60) rejects valid matches when names differ in casing, suffixes, or abbreviation. Three bug fixes in rapid succession (25e6acb, a3cae09, ab08989) all stem from normalization inconsistencies.
- **False positives**: Client name weighted at 60% of base score causes cross-type matches (GL matched to WC for same client). Policy type only 20% — too low to prevent this.
- **Tedious review**: Results dump into a single expandable table. No triage, no overview. Every row requires individual expansion and per-field clicking.
- **Opaque scoring**: Score shown as a percentage with no breakdown. Users can't diagnose why a match failed or succeeded.
- **No data quality feedback**: Unrecognized coverage types and carrier names silently reduce match scores without user visibility.

**User context**: Reconciling AMS exports reshaped into renewal lists, typically <30 rows per run. Policy numbers and dates are strong signals. User wants transparency and manual control — less automation, more assistance with review.

---

## 1. Algorithm Redesign

### 1.1 Additive Scoring (No Hard Gates)

Every signal contributes points additively. No single field can block a match.

| Signal | Max Points | Scoring |
|--------|-----------|---------|
| Policy Number | 40 | Exact normalized = 40. Fuzzy >= 90 = 32. Fuzzy >= 75 = 20. Missing on either side = 0 (neutral). |
| Dates (eff + exp) | 30 | Split 15+15. Each: exact = 15, <= 14d = 12, <= 45d = 8, same year = 4, > 1yr = 0. |
| Policy Type | 15 | Normalized match = 15. Fuzzy >= 85 = 12. Fuzzy >= 70 = 8. Below 70 = 0. |
| Carrier | 10 | Normalized match = 10. Fuzzy >= 80 = 7. Fuzzy >= 60 = 4. Below 60 = 0. |
| Client Name | 5 | Normalized match = 5. Fuzzy >= 80 = 4. Fuzzy >= 60 = 2. Below 60 = 0. |
| **Total** | **100** | |

### 1.2 Confidence Tiers

Tiers are for UI sorting — nothing auto-confirms.

| Tier | Score Range | UI Treatment |
|------|-----------|--------------|
| High (green) | 75+ | Algorithm confident, user still confirms |
| Medium (amber) | 45–74 | Needs review, score breakdown shown inline |
| Low / Unmatched (red) | < 45 | Probably not a match, manual pairing |

### 1.3 Matching Passes

Pass structure is preserved but simplified:

1. **Pass 1: Exact policy number** — Normalized policy number exact match. Score calculated for all fields. Immediate pairing.
2. **Pass 2: Scored match** — All remaining rows scored against all remaining candidates. Best score wins. No hard gates. Pairs formed for scores >= 45.
3. **Pass 3: Unmatched** — Remaining upload rows = unmatched (red). Remaining DB rows = extras (purple).

Pass 1.5 (date pair match) is absorbed into Pass 2 — dates contribute 30 points naturally, which is enough to form medium-confidence pairs without a separate pass.

### 1.4 Per-Field Score Breakdown

Every ReconcileRow carries individual field scores. The `status` field is preserved for XLSX export, summary counts, and template rendering compatibility:

```python
@dataclass
class ReconcileRow:
    ext: dict | None
    db: dict | None
    status: str                 # "PAIRED" | "UNMATCHED" | "EXTRA" (for XLSX/summary/sort)
    match_score: float          # 0–100 total
    confidence: str             # "high" | "medium" | "low" | "none"
    match_method: str           # "policy_number" | "scored" | "manual"
    confirmed: bool = False     # user stamped

    # Per-field score breakdown
    score_policy_number: float = 0   # 0–40
    score_dates: float = 0           # 0–30
    score_type: float = 0            # 0–15
    score_carrier: float = 0         # 0–10
    score_name: float = 0            # 0–5

    # Diff tracking
    diff_fields: list[str]
    cosmetic_diffs: list[str]
    fillable_fields: list[str]

    # Program support
    is_program_match: bool = False
    matched_carrier_id: int | None = None
```

**Status mapping**: `"PAIRED"` replaces both `"MATCH"` and `"DIFF"` — whether a pair has diffs is determined by `len(diff_fields) > 0`, not a separate status. `"UNMATCHED"` replaces `"MISSING"`. `"EXTRA"` is unchanged. The `summarize()` function and `build_reconcile_xlsx()` update to use these values. XLSX sheets: "Paired" (with sub-column for diff count), "Unmatched", "Extra".

### 1.5 FNI Cross-Matching

Preserved: `first_named_insured` checked as alternate name. Best score across ext-client vs db-client, ext-client vs db-FNI, ext-FNI vs db-client, ext-FNI vs db-FNI.

### 1.6 Single-Client Mode

When reconciling filtered to one client, `score_name` auto-maxes to 5 (user already told us the client).

### 1.7 Program Matching

Programs can match multiple upload rows (one per carrier). Carrier-level policy numbers and premiums checked against program_carriers table. Programs not marked EXTRA if they received at least one match.

---

## 2. Pairing Board UI

### 2.1 Layout

Side-by-side board replacing the current results table:

- **Left column**: Upload row data (type, carrier, policy#, dates, premium)
- **Center column**: Score badge (clickable for breakdown)
- **Right column**: Matched DB policy data (with diff tags on mismatched fields)
- **Action column**: Confirm / Break buttons

**Sort order**: Unconfirmed pairs first (amber then green by score ascending — lowest confidence first so the items needing most attention are at top), then unmatched (red), then confirmed pairs (collapsed). Purple extras pool at bottom in a separate section.

### 2.2 Toolbar

- Status counters: paired / review / unmatched / extra (with colored dots)
- "Confirm All Paired" bulk action
- "Export XLSX" download
- Filter tabs: All / Needs Review / Confirmed / Diffs Only

### 2.3 Row Interactions

| Action | Trigger | Server Endpoint | Response |
|--------|---------|----------------|----------|
| Confirm pair | Click "Confirm" | POST /reconcile/confirm/{idx} | Updated _pair_row.html (confirmed state) + OOB counters |
| Break pair | Click "Break" | POST /reconcile/break/{idx} | _unmatched_row.html replaces row + OOB _extra_row appended to extras pool + OOB counters |
| Manual pair (drag) | Drop extra onto drop zone | POST /reconcile/pair/{idx} | _pair_row.html (new pair with score) + OOB removal of dragged _extra_row from extras pool (via `hx-swap-oob="delete"` on the extra's `id`) + OOB counters |
| Manual pair (search) | Click "Search coverage" | GET /reconcile/search-coverage | Dropdown results with "Pair" buttons |
| Confirm all | Click "Confirm All Paired" | POST /reconcile/confirm-all | HX-Trigger full refresh |
| Accept upload value | Click diff tag | PATCH /reconcile/apply-field/{uid} | Updated diff tag (green = applied) |
| Create new policy | Click "Create" | POST /reconcile/create/{idx} | _pair_row.html (created policy) |
| Archive extra | Click "Archive" | POST /reconcile/archive/{uid} | OOB removal of extra row + OOB counters |

### 2.4 Score Breakdown (Expandable)

Click the score badge to toggle inline breakdown:

```
SCORE: [pol# 40/40] [dates 30/30] [type 8/15] [carrier 10/10] [name 0/5]
       Type: "Workers Compensation" vs "Workers Comp" · Premium: $8,200 vs $7,850
```

Score pills color-coded: green (>= 80% of max), amber (40-79%), red (< 40%).

### 2.5 Drag-and-Drop

HTML5 Drag API (~40 lines JS):

- Extra rows: `draggable="true"`, `data-policy-uid`, `ondragstart` sets dataTransfer
- Drop zones on unmatched rows: `ondragover` adds highlight class, `ondrop` calls `htmx.ajax('POST', '/reconcile/pair/' + idx, ...)`
- On successful pair: server response includes OOB `<div id="extra-{uid}" hx-swap-oob="delete"></div>` to remove the dragged extra from the extras pool

### 2.6 Template Structure

| Template | Purpose |
|----------|---------|
| `_pairing_board.html` | Full board layout, toolbar, filters, wraps all rows |
| `_pair_row.html` | Single paired row (green/amber) with Confirm/Break |
| `_unmatched_row.html` | Unmatched upload row (red) with drop zone + Create |
| `_extra_row.html` | Extra DB policy (purple, draggable) with Archive |
| `_score_breakdown.html` | Expandable score pills + diff details |
| `_validation_panel.html` | Pre-match validation (coverage types, carriers, dates, programs) |
| `_reference_guide.html` | Printable data prep reference (canonical types + aliases) |

Removes: `_results_table.html`, `_review_panel.html`, `_pair_section.html`, `_suggest_panel.html`, `_extra_panel.html`, `_edit_form.html`, `_batch_create_review.html`. Net: 7 old templates → 7 new templates (same count, each with a single clear responsibility).

### 2.7 Server-Side State

Reconcile results cached in-memory keyed by UUID token (same pattern as current). Token stored as hidden field in the board. All actions include token. 1-hour TTL with auto-cleanup. Confirm/Break/Pair modify cache in-place. DB writes only on "Accept upload value" (PATCH) or "Create" (POST).

### 2.8 OOB Counter Updates

Every action endpoint returns the primary row HTML plus an out-of-band swap:

```html
<!-- Primary response -->
<div id="pair-3" ...>...confirmed row...</div>

<!-- OOB counters -->
<div id="board-counters" hx-swap-oob="true">
  9 paired · 1 review · 2 unmatched · 5 extra
</div>

<!-- OOB extra row removal (for pair/archive actions) -->
<div id="extra-POL-2025-0199" hx-swap-oob="delete"></div>
```

---

## 3. Normalization Layer

### 3.1 Functions in utils.py

Two categories: **display/save** functions (write to DB) and **matching** functions (comparison only). Both live in `src/policydb/utils.py`.

**Display/save functions** (existing, unchanged):

| Function | What It Does | Used By |
|----------|-------------|---------|
| `normalize_client_name(s)` | Collapse whitespace, title case with acronym preservation, canonicalize legal suffixes ("corp" → "Corp."). Preserves suffixes. | Importer, policy save endpoints, DB normalization |
| `normalize_policy_number(s)` | Uppercase + trim. Preserves formatting characters (dashes, dots). | Importer, policy save endpoints, DB normalization |
| `normalize_coverage_type(s)` | Alias map lookup → canonical name, or title-cased original | Importer, reconciler, everywhere |
| `normalize_carrier(s)` | Config-driven alias lookup → canonical carrier name | Importer, reconciler, everywhere |

**Matching functions** (reconciler-specific, for comparison only — never write results to DB):

| Function | What It Does | Used By |
|----------|-------------|---------|
| `normalize_client_name_for_matching(s)` | Strip legal suffixes entirely, collapse whitespace, title case. "Acme Corp." → "Acme" | Reconciler scoring, find_candidates |
| `normalize_policy_number_for_matching(s)` | Strip spaces/dashes/dots/slashes, uppercase, filter placeholders (TBD, NA, 999, etc.) | Reconciler scoring, find_candidates |
| `parse_currency(s)` | Strip $, commas, parse to float. Returns 0.0 on error. Promoted from importer's `_parse_currency` with full existing behavior preserved (handles None, empty strings, non-numeric edge cases). | Reconciler, importer |

### 3.2 Consolidation

- Reconciler's `_normalize_client_name()` becomes `normalize_client_name_for_matching()` in utils.py — distinct from the display version which preserves suffixes
- Reconciler's `_normalize_policy_number()` becomes `normalize_policy_number_for_matching()` in utils.py — distinct from the display version which preserves formatting
- `_normalize_coverage()` wrapper in reconciler.py removed — call `normalize_coverage_type()` directly (same function for display and matching — alias resolution is the same either way)
- `normalize_carrier()` — same function for display and matching (alias resolution is the same either way)
- Importer's `_parse_currency()` promoted to `parse_currency()` in utils.py as a shared function. Existing behavior and edge case handling fully preserved.

### 3.3 Application Points

1. **On upload parse** — `_process_raw_rows()` normalizes all fields using matching functions
2. **On DB load** — `_load_db_policies()` normalizes all fields using matching functions
3. **In scoring** — `_score_pair()` compares normalized values (see 3.4)
4. **In find_candidates** — same `_score_pair()` function with lower threshold
5. **In importer** — display functions when saving to DB

No matching-normalized values written to DB — raw values preserved. Display-normalized values (title case, canonical suffixes) are written to DB as they are today.

### 3.4 Unified Scoring Function

A single `_score_pair(ext_row, db_row)` function replaces both `_fuzzy_match()` and the separate scoring in `find_candidates()`. Returns a `ScoreBreakdown` namedtuple with all per-field scores. Both the main reconcile loop and `find_candidates()` call this same function — the only difference is the acceptance threshold:

- **Reconcile pairing**: score >= 45
- **find_candidates (search-coverage)**: no threshold, return top 8 sorted by score with full breakdowns

### 3.5 Coverage Alias Persistence

`_COVERAGE_ALIASES` is currently hardcoded in utils.py. To support auto-learn (Section 4.4):

- Add `coverage_aliases` key to config.yaml (same pattern as existing `carrier_aliases`)
- Add `rebuild_coverage_aliases()` function in utils.py (mirrors existing `rebuild_carrier_aliases()`)
- On startup: merge hardcoded base aliases with config-stored aliases (config overrides on conflict)
- On auto-learn: save new alias to config, call `rebuild_coverage_aliases()` to update runtime dict

---

## 4. Data Prep Layer

### 4.1 Pre-Match Validation Panel

Loaded via `GET /reconcile/validation-panel` as an HTMX partial. Triggered after column mapping succeeds — the column mapping form POSTs to `/reconcile/preview-columns`, and on success the UI swaps in the validation panel (passing the parsed data as a token-cached intermediate). The validation panel replaces the area where the upload form was, showing parsed results and a "Run Match →" button.

Shows:

- **Coverage types**: N/N recognized. Shows alias mappings as pills (e.g., "GL → General Liability"). Unrecognized types flagged with dropdown to map to canonical name.
- **Carriers**: N recognized, M unrecognized. Unrecognized carriers clickable to map (with "Remember this alias" option).
- **Dates**: N/N parsed. Date format detected. Date range shown.
- **Client names**: N unique clients. Each fuzzy-matched to PolicyDB clients with score. Confirm/correct before match.
- **Programs**: Auto-detected from rows sharing same client + type + dates + different carriers. Or detected via `program` column.
- **Policy numbers**: N/N present. Rows missing policy numbers noted (will rely on date + type matching).

User can fix issues or proceed — unrecognized values still match via fuzzy scoring.

### 4.2 Program Flagging in CSV

A `program` column (aliases: `program_id`, `master_policy`, `program_group`) groups rows:

- Rows sharing same `program` value are grouped
- First row (or highest premium) becomes master
- Remaining rows become program carrier entries
- Empty column = standalone policy

### 4.3 Auto-Detection (No Column Needed)

Multiple rows with same client + same type + same dates + different carriers → flagged in validation: "These 3 Umbrella rows look like a program — group them?"

### 4.4 Auto-Learn Aliases

When user manually maps an unrecognized carrier or coverage type during validation:
- Carrier: save to `carrier_aliases` in config.yaml, call `rebuild_carrier_aliases()`
- Coverage type: save to `coverage_aliases` in config.yaml, call `rebuild_coverage_aliases()`

One-click "Remember this alias" so the same AMS quirk doesn't repeat.

### 4.5 Template CSV Downloads & Data Prep Reference

**Templates** (downloadable from reconcile page):

- **Standard template**: client, type, carrier, policy_number, effective, expiration, premium
- **Full template**: + limit, deductible, program, layer, FNI, placement_colleague, underwriter

**Data Prep Reference Guide** — accessible as a downloadable/printable resource and inline help on the pre-import page:

- **Reconcile page**: collapsible reference panel on the upload/pre-import page — always available while prepping or reviewing data
- **Endpoint**: GET /reconcile/reference-guide — renders a clean, printable HTML page with all canonical coverage types, accepted aliases, carrier alias list, column header aliases, and program flagging instructions
- Dynamically generated from `_COVERAGE_ALIASES`, `carrier_aliases` config, and `PolicyImporter.ALIASES` — always current, including any auto-learned aliases
- Printable/bookmarkable so the user can reference it while prepping data in Excel

---

## 5. Location Assignment Tool

### 5.1 Purpose

A dedicated pairing board for assigning policies to locations/projects within a client. Reuses the same side-by-side UI pattern as reconcile. Accessible from:

- **Client detail page**: "Organize by Location" button
- **Post-reconcile hook**: if the upload CSV had a `location` or `address` column, offer to launch location assignment after pairing is done

### 5.2 Layout

Same pairing board structure:

- **Left column**: Client's policies (grouped by current `project_name` assignment — unassigned at top)
- **Center column**: Assignment indicator (assigned / unassigned)
- **Right column**: Locations/projects (from `projects` table + `exposure_address` fields)
- **Bottom pool**: Available locations not yet used, or "Create New Location" button

### 5.3 Existing Schema (No Migrations Needed)

Policies already have the fields needed:

| Field | Table | Purpose |
|-------|-------|---------|
| `project_name` | policies | Groups policies by location/project name |
| `project_id` | policies | FK to projects table |
| `exposure_address` | policies | Street address |
| `exposure_city` | policies | City |
| `exposure_state` | policies | State |
| `exposure_zip` | policies | ZIP |
| `access_point` | policies | Entry point / risk location |

The `projects` table is the canonical location registry. Assigning a policy to a location means setting `project_name` and `project_id`.

### 5.4 Interactions

| Action | What Happens |
|--------|-------------|
| Drag policy to location | Sets `project_name` + `project_id` on the policy. PATCH endpoint. |
| Unassign policy | Clears `project_name` + `project_id`. Policy moves to unassigned pool. |
| Create new location | Inline form to create a `projects` row with name + address. |
| Bulk assign | Select multiple policies, drop onto a location. |
| CSV import | Upload a CSV mapping policy_uid or policy_number → location name. Auto-assigns. |

### 5.5 Smart Suggestions

When policies have `exposure_address` populated but no `project_name`:
- Group by address similarity (fuzzy match on street + city + state)
- Suggest: "These 4 policies share address '4500 Capital of TX Hwy' — create a location?"

When a location exists with matching address:
- Auto-suggest the assignment with a confidence badge

### 5.6 Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | /clients/{id}/locations | Location assignment board for a client |
| PATCH | /clients/{id}/locations/assign | Assign policy(s) to a location |
| PATCH | /clients/{id}/locations/unassign | Remove policy from a location |
| POST | /clients/{id}/locations/create | Create new location/project |
| POST | /clients/{id}/locations/import-csv | Bulk assign from CSV mapping |

### 5.7 Templates

| Template | Purpose |
|----------|---------|
| `clients/_location_board.html` | Full location assignment board |
| `clients/_location_policy_row.html` | Draggable policy row |
| `clients/_location_group.html` | Location group with drop zone |

### 5.8 Relationship to Program Management

Location assignment and program management are two dimensions of the same organizational problem:

- **Programs** group policies by *carrier structure* — a master umbrella with multiple carriers underneath
- **Locations** group policies by *physical site* — multiple coverage types at the same address

A single client can have both: an umbrella program spanning 3 carriers, with those policies spread across 4 job sites. The pairing board pattern handles both — the difference is what the "right side" represents (carriers vs locations). Future consideration: a unified "Organize" tool on the client detail page with tabs for "By Location" and "By Program", sharing the same drag-to-assign interaction model.

### 5.9 Post-Reconcile Hook

If the reconcile upload CSV contains a `location`, `address`, `project`, or `site` column:

1. During validation panel: show "Location data detected — will be available for assignment after matching"
2. After pairing is confirmed: offer "Assign locations now?" button
3. Button opens the location assignment board pre-populated with the CSV's location values as suggestions

---

## 7. Migration & Compatibility

### 7.1 Routes

Existing endpoints preserved where possible:
- POST /reconcile — still the main entry point, now returns _pairing_board.html
- GET /reconcile/preview-columns — unchanged
- PATCH /reconcile/apply-field/{uid} — kept
- POST /reconcile/create — kept (pre-filled form)
- POST /reconcile/archive/{uid} — kept

New endpoints:
- POST /reconcile/confirm/{idx}
- POST /reconcile/break/{idx}
- POST /reconcile/pair/{idx}
- POST /reconcile/confirm-all
- GET /reconcile/search-coverage
- GET /reconcile/validation-panel
- GET /reconcile/template-csv/{type}
- GET /reconcile/reference-guide — printable data prep reference

Removed endpoints:
- GET /reconcile/suggest — replaced by search-coverage
- POST /reconcile/confirm-match — replaced by /reconcile/pair/{idx}
- POST /reconcile/confirm-pair — replaced by /reconcile/pair/{idx}
- POST /reconcile/batch-create — replaced by individual create actions

### 7.2 Reconciler Module

`src/policydb/reconciler.py` changes:
- `reconcile()` function simplified: 3 passes instead of 4
- `_fuzzy_match()` replaced by `_score_pair()` with new additive scoring weights
- `_compare_fields()` returns per-field scores in addition to diff lists
- `ReconcileRow` dataclass updated with score breakdown fields; `status` field preserved with new values ("PAIRED"/"UNMATCHED"/"EXTRA")
- `find_candidates()` calls same `_score_pair()` function — no separate scoring code path
- `_find_likely_pairs()` removed — absorbed into scored matching
- `summarize()` updated for new status values
- `build_reconcile_xlsx()` updated: sheets become "Paired" (with diff count sub-column), "Unmatched", "Extra"

### 7.3 No Schema Changes

No database migrations required. All changes are in Python code and Jinja2 templates. Config additions (auto-learned aliases) use existing config.yaml infrastructure with new `coverage_aliases` key.

---

## 8. Files Changed

| File | Change |
|------|--------|
| `src/policydb/reconciler.py` | Algorithm rewrite: `_score_pair()`, ReconcileRow, passes, `summarize()`, `build_reconcile_xlsx()` |
| `src/policydb/utils.py` | Add `normalize_client_name_for_matching()`, `normalize_policy_number_for_matching()`, promote `parse_currency()`, add `rebuild_coverage_aliases()` |
| `src/policydb/web/routes/reconcile.py` | New endpoints, validation panel, reference guide, template downloads |
| `src/policydb/web/templates/reconcile/_pairing_board.html` | NEW: full board layout |
| `src/policydb/web/templates/reconcile/_pair_row.html` | NEW: paired row |
| `src/policydb/web/templates/reconcile/_unmatched_row.html` | NEW: unmatched row |
| `src/policydb/web/templates/reconcile/_extra_row.html` | NEW: extra row |
| `src/policydb/web/templates/reconcile/_score_breakdown.html` | NEW: score pills |
| `src/policydb/web/templates/reconcile/_validation_panel.html` | NEW: pre-match validation |
| `src/policydb/web/templates/reconcile/_reference_guide.html` | NEW: printable data prep reference |
| `src/policydb/web/templates/reconcile/index.html` | Add template downloads, reference panel link |
| `src/policydb/web/templates/reconcile/_results_table.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_review_panel.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_pair_section.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_suggest_panel.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_extra_panel.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_edit_form.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_batch_create_review.html` | REMOVE |
| `src/policydb/web/templates/reconcile/_create_form.html` | KEEP: reused for Create action |
| `src/policydb/web/routes/clients.py` | NEW: location assignment endpoints (GET/PATCH/POST under /clients/{id}/locations) |
| `src/policydb/web/templates/clients/_location_board.html` | NEW: location assignment pairing board |
| `src/policydb/web/templates/clients/_location_policy_row.html` | NEW: draggable policy row for location board |
| `src/policydb/web/templates/clients/_location_group.html` | NEW: location group with drop zone |
