# Package Policy Sub-Coverages Design

**Date:** 2026-03-26
**Status:** Draft
**Scope:** Data model, UI, schedule, tower, coverage matrix, charts, reconciler, importer

---

## Problem

PolicyDB models each policy as a single row with one `policy_type`. This breaks down for bundled/package policies — a Business Owners Policy (BOP) that includes General Liability, Property, and Inland Marine under one policy number, or a Workers Compensation policy that implicitly includes Employers Liability. Today, a BOP gets flattened to "Property / Builders Risk" via coverage aliases, losing the GL and other sub-lines entirely. This means the GL section of a Schedule of Insurance is incomplete, the coverage matrix doesn't reflect reality, and tower diagrams can't see an umbrella that lives inside a package.

## Design Decisions

These decisions were validated during brainstorming:

1. **Sub-coverages are tags, not financial entities.** Premium, limits, and deductible stay at the parent policy level. Sub-coverages carry no per-line financials.
2. **Junction table approach** over JSON column or child rows. Follows the existing `program_carriers` pattern. Extensible if per-sub-line data is ever needed.
3. **Any policy can have sub-coverages.** Not limited to a special "Package" type. WC gets EL. BOP gets GL + Property. A standalone GL has zero sub-coverage rows.
4. **Manual entry is the primary flow.** Sub-coverages are selected from the existing `policy_types` config list on the policy edit page. The reconciler and importer don't auto-create sub-coverages.
5. **WC/EL uses the same model as any package** — no special-casing. WC auto-generates an EL sub-coverage on creation via a config-driven mapping.
6. **Schedule of Insurance: dual display.** Package policies get their own "Package Policies" section AND ghost/reference rows in each relevant coverage section.
7. **Tower/schematic: full participation.** A package's umbrella/excess sub-coverage gets a layer in the tower diagram just like a standalone umbrella, with a "Package" badge.
8. **Reconciler: no scoring changes.** Strong signals (policy_number, carrier, dates, premium) are sufficient. BOP alias updated to normalize to "Business Owners Policy" instead of "Property / Builders Risk".

---

## Data Model

### New Table: `policy_sub_coverages`

```sql
CREATE TABLE IF NOT EXISTS policy_sub_coverages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    coverage_type TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    UNIQUE(policy_id, coverage_type)
);
CREATE INDEX idx_sub_cov_policy ON policy_sub_coverages(policy_id);
CREATE INDEX idx_sub_cov_type ON policy_sub_coverages(coverage_type);
```

- `coverage_type` values come from the `policy_types` config list.
- `sort_order` controls display order within a policy's sub-coverage list.
- `UNIQUE(policy_id, coverage_type)` prevents duplicate sub-coverages on the same policy.

### Config Changes

**New policy types added to `_DEFAULTS["policy_types"]`:**
- "Business Owners Policy"
- "Employers Liability"

**New config key: `auto_sub_coverages`**

```yaml
auto_sub_coverages:
  "Workers Compensation": ["Employers Liability"]
```

When a policy is created with a `policy_type` that has an entry in `auto_sub_coverages`, the listed sub-coverages are auto-inserted into `policy_sub_coverages`. The user can still add or remove sub-coverages manually afterward.

Editable in the Settings UI.

### Coverage Alias Update

In `utils.py` `_COVERAGE_ALIASES`, change BOP-related entries:

```
"bop" → "Business Owners Policy"              (was: "Property / Builders Risk")
"businessowners" → "Business Owners Policy"     (was: "Property / Builders Risk")
"businessowners policy" → "Business Owners Policy"
"business owners policy" → "Business Owners Policy"
```

---

## UI: Policy Edit Page

### Sub-Coverage Input

A new **"Sub-Coverages"** section appears below the policy type field on the policy detail/edit page:

- **Pill/tag input pattern** — consistent with existing UI standards (e.g., markets, coverages).
- Each sub-coverage renders as a pill with an × to remove.
- Combobox to add new sub-coverages, filtered from the `policy_types` config list.
- Auto-generated sub-coverages (e.g., EL on WC) appear pre-populated but are removable.
- Save on change via PATCH to a new endpoint — per-field pattern, no Save button.

### API Endpoints

```
GET  /policies/{uid}/sub-coverages          → list sub-coverages for a policy
POST /policies/{uid}/sub-coverages          → add a sub-coverage (body: {coverage_type})
DELETE /policies/{uid}/sub-coverages/{id}   → remove a sub-coverage
```

Auto-generation fires on policy creation: when `policy_type` matches an `auto_sub_coverages` key, the mapped sub-coverages are inserted.

---

## Schedule of Insurance

### Package Policies Section

A new section at the top of the schedule (or grouped with other special sections):

- **Header:** "Package Policies" with a package icon.
- **Columns:** Policy UID, Carrier, Sub-Coverages (as pills/badges), Effective, Expiration, Premium.
- Shows every policy that has one or more rows in `policy_sub_coverages`.

### Ghost Rows in Coverage Sections

For each sub-coverage on a package policy, a **ghost/reference row** appears in the corresponding coverage section of the schedule:

- **Visual treatment:** Lighter text (gray), italic, purple "Package" badge next to the policy UID.
- **Premium column:** Shows "—" (dash) instead of a dollar amount to avoid double-counting.
- **Click behavior:** Links to the parent package policy's detail page.
- **Sort position:** Ghost rows sort after standalone policies within the same section.

### Query Logic

The `v_schedule` view (or the Python query that builds the schedule) needs to:

1. Select all policies as usual for their `policy_type` section.
2. Additionally select policies that have a matching `coverage_type` in `policy_sub_coverages` — these become ghost rows in that section.
3. Flag ghost rows with a `is_package_ghost` indicator so the template renders them differently.

---

## Tower / Schematic

### Layer Participation

When building the tower diagram, the query checks `policy_sub_coverages` for any sub-coverage matching "Umbrella / Excess" (or similar tower-eligible types). A package policy with an umbrella sub-coverage gets a layer in the tower.

### Visual Treatment

- **Purple border** on the layer block (distinct from standalone layers).
- **"Package" badge** in the top-right corner.
- **Subtitle:** "via {policy_type}" (e.g., "via Business Owners Policy") below the policy number.
- **Premium display:** "pkg premium" in italic instead of a dollar amount.
- **Limit/retention:** Uses the parent policy's `limit_amount` and `deductible` fields.

### Schematic Entry Page

The policy picker on `/clients/{id}/programs/{tower_group}` surfaces package policies with tower-eligible sub-coverages in the assignable list, with a "Package" indicator. When assigned, `layer_position` and `tower_group` are set on the parent policy as usual — no schema change needed.

---

## Coverage Matrix

The coverage matrix (briefing pages, linked group overview) currently groups by `policy_type`. With sub-coverages:

- A package policy contributes to **each** sub-coverage's column in the matrix.
- The matrix cell shows the carrier with a "Package" indicator.
- The parent `policy_type` column also shows the policy (so "Business Owners Policy" appears as its own column too).

### Query Change

`get_linked_group_overview` in `queries.py` needs to union:
1. Policies by their `policy_type` (existing behavior).
2. Policies by their `policy_sub_coverages.coverage_type` (new — creates entries in additional columns).

Flag package-sourced entries so the template can render them with a "Package" indicator.

---

## Charts

- **Premium attribution:** Package policy premium is attributed to its `policy_type` ("Business Owners Policy") in all charts. It is NOT split across sub-coverages — that would invent numbers.
- **Coverage type charts:** "Business Owners Policy" appears as its own category in charts that group by policy type (Premium Comparison, Carrier Breakdown, etc.).
- **No double-counting:** Sub-coverages are informational tags for schedule/matrix placement. They don't create separate premium entries in chart data.

---

## Reconciler

### No Scoring Changes

The reconciler's `_score_pair()` function is unchanged. Matching relies on policy_number (25 pts), carrier (15 pts), dates (15 pts), premium (15 pts), and policy_type (15 pts). These strong signals are sufficient to match a "BOP" statement row to the correct package policy.

### Alias Update Only

`normalize_coverage_type()` in `utils.py` is updated so "BOP" and variants normalize to "Business Owners Policy" instead of "Property / Builders Risk". This means a statement row labeled "BOP" will score 15/15 on policy_type when matched against a policy with `policy_type="Business Owners Policy"`.

Sub-coverages are added manually after the match — no reconciler automation.

---

## Importer

### Alias Update Only

When a CSV row has "BOP", "Business Owners", or similar in the coverage/policy_type column, it imports as `policy_type="Business Owners Policy"`. Same alias update as the reconciler.

No automatic sub-coverage creation on import. The user tags sub-coverages manually after import.

---

## Migration

**Migration 072:** Creates `policy_sub_coverages` table with indexes and unique constraint.

**Config defaults update:** Add "Business Owners Policy" and "Employers Liability" to `policy_types`. Add `auto_sub_coverages` with the WC → EL mapping.

**Alias update:** Modify `_COVERAGE_ALIASES` in `utils.py`.

---

## What This Does NOT Change

- **Policy table schema** — no new columns on `policies`. Sub-coverages live in the junction table.
- **Reconciler scoring** — no new scoring signals or matching logic.
- **Program model** — `is_program`, `program_id`, `program_carriers` are untouched. Programs and packages solve different problems.
- **Timeline engine** — package policies follow the same milestone/renewal workflow as any policy.
- **Renewal pipeline** — package policies appear once (by their `policy_type`), not multiplied by sub-coverage count.
- **Premium calculations** — premium is always the parent policy value. Never split, never multiplied.
