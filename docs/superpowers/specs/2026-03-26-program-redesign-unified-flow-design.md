# Program Redesign — Unified Flow

**Date:** 2026-03-26
**Status:** Approved
**Supersedes:** Portions of `2026-03-18-programs-aggregate-design.md`, `2026-03-18-program-carriers-table-design.md`, `2026-03-26-program-schematic-entry-design.md`

---

## Problem

Programs and towers are the same concept in the broker's head — "the Casualty program" — but PolicyDB treats them as three separate things:

1. **Tower group** — a text label (`tower_group`) on policies for visual grouping
2. **Program** — a policy with `is_program=1` that aggregates carriers
3. **Linked policies** — child policies with `program_id` pointing to a parent

This creates friction:
- **Too many hops to create a program:** Add Policy → check "This is a Program" → type tower group name → save → navigate to client → find tower section → click "Edit Program"
- **Confusing concepts:** Users must understand the relationship between tower groups, programs, and linked policies to use the system effectively
- **Duplicate UI:** The client Policies tab has both a "Corporate Programs" card and a separate "Tower Structure" section showing the same data differently

---

## Solution

Unify "program" as the single concept. Tower group = program name. One creation flow, one hub (the schematic page), one section on the client page.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Program = tower group | Program name IS the `tower_group` value | Matches broker mental model — they're the same thing |
| Creation entry point | Inline form on client Policies tab | Eliminates the "Add Policy → check Program" detour |
| Program hub | Enhanced schematic page | Already has the matrices; just needs header metadata and assign-existing panel |
| Client page display | Merge Corporate Programs + Tower Structure into one section | Eliminates duplicate UI showing the same data |
| Quota share | Two patterns: program carriers (formal) + same attachment point (ad-hoc) | Both exist in practice |
| Multi-line umbrella | Multi-column tower where umbrella spans underlying lines it covers | Matches how casualty programs actually work |
| Sub-coverage participation | Package sub-coverage limits feed into tower as underlying layers | BOP GL limit participates in tower alongside standalone policies |
| Limit sync | Schematic writes to policy records + tower position badge on policy detail | Single entry point, no double-entry |

---

## 1. Program Creation Flow

### New Entry Point

**Location:** Client Policies tab, next to existing "+ Add Policy" and "+ Opportunity" buttons.

**Button:** `+ New Program` (styled as primary action — filled brand color)

**Inline form** (expands below buttons on click):
- **Program Name** — text input with placeholder "e.g., Casualty, Property, D&O..."
- **Primary Line of Business** — optional combobox from `policy_types` config
- **"Create & Open →"** button

**On submit:**
1. Create a new policy record:
   - `client_id` from page context
   - `is_program = 1`
   - `tower_group` = program name (entered by user)
   - `policy_type` = selected line of business (or program name as fallback)
   - `policy_uid` via `next_policy_uid()`
   - All other fields blank/default
2. Redirect to schematic page: `/clients/{client_id}/programs/{tower_group}`

**Route:** `POST /clients/{client_id}/programs/new` — lives in `routes/programs.py` (registered before the `{tower_group}` parameterized route per literal-first ordering rule). No conflict since POST vs GET.

**No changes to existing "Add Policy" flow** — the program checkbox and tower_group field remain for advanced users who want to create programs through the full form. The new flow is a shortcut, not a replacement.

---

## 2. Schematic Page Enhancements

The existing schematic page (`/clients/{client_id}/programs/{tower_group}`) becomes the program hub. Current functionality (underlying/excess matrices, cell editing, live preview) is preserved. Additions:

### 2a. Program Header

**New section** at top of page, above the matrices:

```
Breadcrumb: Clients / {Client Name} / Programs / {Program Name}
Header:     {Program Name} — {Client Name}               [← Back to Client]

            Term: {eff_date} – {exp_date}    Premium: $1,245,000    Status: [Bound]
```

Fields are inline-editable (contenteditable + PATCH):
- **Program name** — edits `tower_group` on all policies in this program AND the program policy's `policy_type`
- **Term dates** — edits `effective_date` / `expiration_date` on the program policy
- **Status** — edits `renewal_status` on the program policy via status badge select
- **Total premium / total limit** — auto-summed from all underlying + excess rows (read-only)

**Route for name rename:** `PATCH /clients/{client_id}/programs/{tower_group}/rename`
- Updates `tower_group` on all policies where `tower_group = old_name AND client_id = client_id` (regardless of `project_name`)
- `program_id` FK references are unaffected (they point to `policies.id`, not tower_group)
- Returns `HX-Redirect` header to new schematic URL with updated tower_group name

### 2b. Unassigned Policies Panel & Package-Aware Assignment

**New section** below the two matrices, above the tower preview.

Shows policies for this client that are not assigned to any program (no `tower_group` or `program_id`):

```
Unassigned Policies (3)
[Business Owners Policy — $45K  ▼ + Assign]  [Auto Liability — $1M  + Assign]  [Hired Auto — $8K  + Assign]
```

#### Assignment Flow — Standalone vs Package

**Standalone policy** (no sub-coverages, e.g., Auto Liability):
- Click `+ Assign` → policy is immediately added to the program
- Appears as a single underlying column in the tower
- Its `limit_amount` determines the column height

**Package policy** (has sub-coverages with limits, e.g., BOP):
- Click `+ Assign` → the policy is assigned to the program, AND its sub-coverages with `limit_amount` values are **exploded into individual underlying columns** on the schematic
- The parent policy row is NOT shown as an underlying line — only its sub-coverages are
- Each sub-coverage column shows: sub-coverage type as label, sub-coverage limit as height, parent policy carrier

```
After assigning BOP ($45K, carrier: Acme Ins Co):
  Underlying matrix now shows:
  ┌──────────────────┬──────────────────┬──────────────────┐
  │ General Liability │ Property         │ Auto Liability   │  ← already assigned
  │ Acme Ins Co       │ Acme Ins Co      │ Liberty Mutual   │
  │ $1,000,000        │ $500,000         │ $1,000,000       │
  │ (from BOP)        │ (from BOP)       │                  │
  └──────────────────┴──────────────────┴──────────────────┘
```

Sub-coverages **without** `limit_amount` are not shown (they need limits entered first on the policy edit page).

**WC/EL special case:** Workers' Compensation is statutory — it stays as its own column. But the Employers' Liability sub-coverage (which has a limit) gets its own separate column.

#### Tower Line Participation Toggle

Not every sub-coverage from a package belongs in the tower. After assignment, each sub-coverage column has a toggle to include/exclude it from the tower visualization:

```
Underlying Lines:
  ☑ General Liability — $1M (from BOP, Acme Ins)     ← in tower
  ☑ Auto Liability — $1M (Liberty Mutual)             ← in tower
  ☐ Property — $500K (from BOP, Acme Ins)             ← excluded from tower
```

Excluded lines stay in the underlying matrix for reference but are not rendered as tower columns and cannot be selected in the "Covers" relationship.

**Implementation:** Toggle state stored in a new `include_in_tower` boolean on `program_tower_lines` (see §4). Default: included for liability-type coverages (GL, Auto, EL, PL), excluded for property-type coverages. User can override.

#### "Covers" Selector on Excess/Umbrella Rows

Each excess row in the schematic gets a **"Covers" pill selector** showing all included tower lines:

```
Umbrella Liability — $5M xs $1M — National Indemnity
  Covers: [GL ×] [Auto ×]  [+ add]
```

Clicking `[+ add]` shows a dropdown of included tower lines not yet covered. Clicking `×` removes coverage. Changes are saved via `PUT /tower-coverage/{excess_id}`.

The umbrella's width in the tower preview = the number of columns it covers.

#### Assign/Unassign Mechanics

**"+ Assign" action:**
- Sets the policy's `tower_group` to this program's name
- Sets `program_id` to the program policy's `id` (establishing the parent-child FK link)
- Sets `layer_position = 'Primary'` (default — user can change on schematic)
- For package policies: auto-creates `program_tower_lines` rows for each sub-coverage with a `limit_amount`
- Assigns next `schematic_column` (one per sub-coverage column for packages)
- Returns the policy/sub-coverage rows via HTMX swap
- Removes from unassigned list

**"× Remove" action** on existing rows:
- Clears `tower_group` and `program_id` on the policy
- Deletes associated `program_tower_lines` rows
- Deletes associated `program_tower_coverage` rows (both as underlying and as excess)
- Moves it back to unassigned panel

**Route:** `POST /clients/{client_id}/programs/{tower_group}/assign/{policy_uid}`
**Route:** `POST /clients/{client_id}/programs/{tower_group}/unassign/{policy_uid}`

**Query for unassigned:**
```sql
SELECT policy_uid, policy_type, carrier, premium, limit_amount, id
FROM policies
WHERE client_id = ? AND archived = 0
  AND (is_opportunity = 0 OR is_opportunity IS NULL)
  AND is_program = 0
  AND (tower_group IS NULL OR tower_group = '')
  AND program_id IS NULL
ORDER BY policy_type
```

### 2c. Quota Share Support

**Formal quota share** (program layer): Already supported — excess rows with `is_program=1` have a nested `program_carriers` sub-table. No changes needed.

**Ad-hoc quota share** (separate policies at same attachment point):
- Two excess policies with the same `attachment_point` are automatically displayed side-by-side in the tower preview
- The schematic excess table shows them as separate rows (each independently editable)
- The tower preview renders them at the same vertical level with a "QS" badge
- No new data model — this is purely a visualization behavior based on matching `attachment_point` values

**Tower preview rendering rule:**
- Group excess rows by `attachment_point`
- If multiple rows share an `attachment_point`, render them side-by-side with carrier names and participation percentages
- Use gold/amber color tint for quota share layers to distinguish from sole-carrier layers

### 2d. Multi-Line Umbrella Towers & Sub-Coverage Participation

A common program structure: a BOP has a GL sub-coverage with a $1M limit, an Auto Liability policy has its own $1M limit, and a $5M Umbrella sits over both. The tower must render this as a **multi-column visualization** where the umbrella spans the underlying lines it covers.

#### Data Source: Sub-Coverage Limits

The `policy_sub_coverages` table (from `nervous-jones` branch, migration 090–092) stores per-sub-coverage `limit_amount` and `deductible`. This means a BOP's GL sub-coverage has its own limit distinct from the parent BOP's aggregate limit. The schematic page reads these to build tower columns.

**Sub-coverage → tower column mapping:**
- When a package policy (BOP, WC/EL) is assigned to a program, its sub-coverages with `limit_amount` values become available as underlying tower columns
- The user selects which sub-coverages participate as tower lines (not all are tower-relevant — e.g., Inland Marine from a BOP probably isn't under the umbrella)
- Each participating sub-coverage renders as its own column in the tower with its `limit_amount` as the layer height

#### Multi-Column Tower Visualization

The tower preview shifts from a single-column stack to a multi-column layout when an umbrella or excess policy covers multiple underlying lines:

```
                         ┌──────────────────────────────────────┐
                         │     Umbrella — $5M xs $1M            │
                         │     National Indemnity                │
                         ├──────────────────┬───────────────────┤
 $1M ────────────────────┤  GL (from BOP)   │  Auto Liability   │
                         │  $1M             │  $1M              │
                         │  Acme Ins Co     │  Liberty Mutual   │
                         └──────────────────┴───────────────────┘
```

**Key behaviors:**
- The umbrella layer spans the full width of the columns it covers
- Each underlying column shows the line of business, limit, and carrier
- The attachment point of the umbrella aligns with the top of the underlying columns
- Underlying columns can be standalone policies OR sub-coverages from package policies — they're visually identical in the tower

#### Schematic Page: Line Selection

**New UI element** in the schematic underlying section — "Tower Lines" selector:

For each policy assigned to the program:
- **Standalone policy** (e.g., Auto Liability): automatically a tower line, shown as a single entry
- **Package policy** (e.g., BOP): expandable to show sub-coverages with limits. User checks which sub-coverages participate as tower lines.

```
Tower Lines:
  ☑ Auto Liability — $1M (Liberty Mutual)           [standalone]
  ▼ Business Owners Policy (Acme Ins Co)             [package]
      ☑ General Liability — $1M
      ☐ Property — $500K
      ☐ Inland Marine — $100K
```

**"Covers" relationship on excess/umbrella rows:**
- Each excess row in the schematic gets a "Covers" multi-select showing available tower lines
- The umbrella's width in the tower preview = the columns it covers
- Stored as a new junction: `program_tower_coverage` (see §4 Data Model Changes)

#### Quota Share in Multi-Column Context

Quota share layers at the same attachment point render at the same vertical level, spanning the same columns as the layer they share:

```
                         ┌──────────────────────────────────────┐
                         │  QS Layer — Carrier A 60% / B 40%   │
                         │  $10M xs $6M                         │
                         ├──────────────────────────────────────┤
                         │     Umbrella — $5M xs $1M            │
                         ├──────────────────┬───────────────────┤
                         │  GL — $1M        │  Auto — $1M       │
                         └──────────────────┴───────────────────┘
```

### 2e. Limit Sync to Policy Records

When any field is saved on the schematic page (underlying or excess), the PATCH endpoint already writes to the `policies` table. Additionally:

**Auto-derived fields** (computed and saved on each cell edit):
- `layer_notation` — new TEXT column on `policies`, e.g., "$10M xs $5M" or "$25M po $30M xs $20M"
  - Computed by `_layer_notation()` from `charts.py` (already exists — needs `%g` format replaced with proper currency shorthand to avoid scientific notation per project convention)
  - Saved on every limit/attachment/participation_of change on the schematic page
  - Also recalculated when limit/attachment fields are edited on the policy edit page or via reconciler/import
  - A Jinja2 filter `| layer_notation` already exists for runtime computation — the column serves as a cached value for exports and views. On read, prefer the column if populated; fall back to runtime computation.
  - Displayed on policy detail page and in exports

**Policy detail page — Tower Position badge:**
- Read-only section on the policy edit/detail page (when policy has a `tower_group`)
- Shows: program name, layer position (e.g., "Layer 2 of Casualty"), notation
- Link: "View in schematic →" goes to `/clients/{client_id}/programs/{tower_group}`
- Auto-populated fields: limit, attachment point, premium, notation

**Migration:** Add `layer_notation TEXT` column to `policies` table.

---

## 3. Client Page — Unified Programs Section

### Merge Corporate Programs + Tower Structure

**Remove:** The separate "Tower Structure" section from `_tab_policies.html` (search for `{# ── Tower Structure ── #}` comment).

**Replace Corporate Programs card** with a unified "Programs" section that includes both the tabular data and the tower visualization.

### New Programs Section Layout

Each program gets a card:

```
┌─────────────────────────────────────────────────────────────────┐
│ [PGM] Casualty   5 carriers · $1.25M   [Bound]   Open Program →│
│ ┌─ GL · Auto · EL ─┬─ UMB $10M ─┬─ XS $10M ─┬─ QS $25M ─────┐│
│ └──────────────────┴────────────┴────────────┴─────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

**Card contents:**
- **Summary row:** PGM badge, program name (link to schematic), carrier count, total premium, status badge, "Open Program →" link
- **Mini tower visualization:** Horizontal bar showing the tower layers proportionally, with abbreviated labels. Click anywhere on the bar to open schematic.

**Expandable carrier detail** (click program name or chevron):
- Nested carrier rows (same as current `_programs.html` pattern)
- Each carrier row has "Edit ↗" link (current behavior preserved)

**Section header:**
```
PROGRAMS · 2 programs · $2.45M total premium           [+ New Program]
```

### Template Changes

| File | Change |
|------|--------|
| `clients/_tab_policies.html` | Remove Tower Structure section (search for `{# ── Tower Structure ── #}`). Keep existing `{% include "clients/_programs.html" %}` |
| `clients/_programs.html` | Rewrite in-place to unified card layout with mini tower viz |

### Program-Linked Policies Section

The "Program-Linked Policies" collapsed section (added earlier today) remains as-is — it provides quick access to individual policies that belong to programs without cluttering the main policy list.

---

## 4. Data Model Changes

### Already Merged (from prior PRs)

- **Migration 091–093:** `policy_sub_coverages` table with `limit_amount`, `deductible`, `coverage_form`, `notes`
- **Migration 094:** `layer_notation TEXT` column on `policies`
- **Migration 095:** `program_tower_coverage` junction table

### New Migration: `program_tower_lines`

Tracks which underlying lines participate in the tower visualization, including exploded sub-coverage columns from package policies.

```sql
-- Migration C: Tower lines — which underlying lines participate in the tower
CREATE TABLE IF NOT EXISTS program_tower_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    program_policy_id   INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    source_policy_id    INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    sub_coverage_id     INTEGER REFERENCES policy_sub_coverages(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,
    include_in_tower    INTEGER NOT NULL DEFAULT 1,
    sort_order          INTEGER DEFAULT 0,
    UNIQUE(program_policy_id, source_policy_id, sub_coverage_id)
);
CREATE INDEX IF NOT EXISTS idx_ptl_program ON program_tower_lines(program_policy_id);
CREATE INDEX IF NOT EXISTS idx_ptl_source ON program_tower_lines(source_policy_id);
```

**Fields:**
- `program_policy_id` — the program (is_program=1 policy) this line belongs to
- `source_policy_id` — the policy this line comes from (the BOP, the Auto Liability, etc.)
- `sub_coverage_id` — NULL for standalone policies, or FK to `policy_sub_coverages.id` for package sub-coverages
- `label` — display label (e.g., "General Liability", "Auto Liability", "Employers' Liability")
- `include_in_tower` — 1 = shown as tower column, 0 = in matrix but excluded from tower viz
- `sort_order` — column ordering in the tower

**When a policy is assigned to a program:**
- Standalone policy → one `program_tower_lines` row with `sub_coverage_id = NULL`
- Package policy → one row per sub-coverage that has a `limit_amount`
- WC policy → one row for WC (statutory), plus one row per EL/WC sub-coverage with limits

**Default `include_in_tower` logic:**
- Liability-type coverages (GL, Auto, EL, PL, Professional Liability) → default 1 (included)
- Property-type coverages (Property, Inland Marine, Equipment) → default 0 (excluded)
- User can toggle via the tower lines selector UI

### Updated Junction Table: `program_tower_coverage`

Already created (migration 095). Tracks which excess/umbrella layers cover which underlying tower lines.

```
excess_policy_id → points to the umbrella/excess policy
underlying_policy_id → points to a standalone policy tower line, OR
underlying_sub_coverage_id → points to a sub-coverage tower line from a package
```

The "Covers" pill selector on each excess row writes to this table. The tower preview reads it to determine umbrella column span.

### Preserved Model

The existing program model is preserved:
- `is_program` flag on `policies` table
- `tower_group` text field on `policies` table
- `program_id` FK on `policies` table for parent-child links
- `program_carriers` table for multi-carrier program layers
- `policy_sub_coverages` table for package sub-coverage limits

New additions: `program_tower_lines` (which lines are in the tower), `program_tower_coverage` (which excess covers which lines), `layer_notation` (cached display notation).

---

## 5. Files

### New Files

| File | Purpose |
|------|---------|
| `migrations/094_layer_notation.sql` | Add `layer_notation` column (already merged) |
| `migrations/095_program_tower_coverage.sql` | Tower coverage junction table (already merged) |
| `migrations/096_program_tower_lines.sql` | Tower lines table — tracks exploded sub-coverage columns |
| `templates/clients/_programs.html` | Rewrite in-place to unified card layout with mini tower viz |
| `templates/programs/_header.html` | Program header partial for schematic page |
| `templates/programs/_unassigned_panel.html` | Unassigned policies panel |
| `templates/programs/_tower_lines.html` | Tower line selector (sub-coverage participation checkboxes) |

### Modified Files

| File | Change |
|------|--------|
| `routes/programs.py` | Add program creation endpoint (before parameterized routes), assign/unassign endpoints, rename endpoint, header metadata PATCH |
| `routes/clients.py` | Pass unified program + tower data to template |
| `templates/clients/_tab_policies.html` | Replace Corporate Programs include + remove Tower Structure section |
| `templates/programs/schematic.html` | Add header section, unassigned panel, link to new partials |
| `templates/policies/edit.html` | Add tower position badge when policy has tower_group |
| `db.py` | Wire migration 090 |
| `views.py` | Add `layer_notation` to relevant views |
| `charts.py` | Fix `_layer_notation()` to use proper currency shorthand instead of `%g`; add multi-column tower rendering with sub-coverage columns; explode packages into individual columns |
| `charts.py` (`get_schedule_data`) | Ghost rows for package sub-coverages in SOI already exist from `nervous-jones` — ensure sub-coverage limits display correctly |
| `queries.py` | Add `get_tower_lines()`, `get_tower_coverage_map()` for program tower line and coverage relationships |

---

## 6. Reconciler Impact

**No breaking changes.** The reconciler already matches against programs via `program_carriers` rows (structured carrier/policy-number matching from the `2026-03-18-program-carriers-table-design.md` spec). Since this redesign is a UI unification with no data model changes, reconciler behavior is preserved.

**Minor enhancements to consider during implementation:**
- When reconcile batch-creates a program, set `tower_group` = program name (currently may not be set)
- When reconcile matches an import row to a program carrier, update `layer_notation` on the parent policy if limit/attachment changed
- The "Create Program from Selected" flow in reconcile should redirect to the schematic page after creation (currently redirects to policy edit page)

These are incremental improvements, not blockers.

---

## 7. Edge Cases & Empty States

| Scenario | Behavior |
|----------|----------|
| New program (zero lines) | Schematic shows empty matrices with helpful prompt: "No underlying lines yet. Click '+ Add Line' to start." |
| Client with no programs | Programs section still renders with header and `[+ New Program]` button. Body shows: "No programs yet." |
| Rename program | Updates `tower_group` on all matching policies. `program_id` FK unaffected. HX-Redirect to new URL. |
| Delete program | Deferred to future spec. Current behavior: individual rows can be deleted from schematic, but no "delete entire program" action exists yet. |
| Quota share (ad-hoc) | Two excess rows at same `attachment_point` — tower preview groups them. Requires changes to `get_tower_data()` in `charts.py` to detect shared attachment points. |
| Assign policy that already has a tower_group | Should not appear in unassigned list (filtered out by query). If moved between programs, use unassign first. |
| Policy edited outside schematic | `layer_notation` column recalculated on policy edit save if limit/attachment fields change. |
| Package policy with no sub-coverage limits | Sub-coverages without `limit_amount` are excluded from tower line selection. User must enter limits on the policy edit page or schematic first. |
| Umbrella covers 0 lines | If no "Covers" selections are made, umbrella renders as a single-column layer above the first underlying line (backward-compatible with current single-column tower). |
| Sub-coverage deleted while in tower | `ON DELETE CASCADE` on `program_tower_coverage.underlying_sub_coverage_id` removes the junction row. Tower preview re-renders without that column. |
| Same sub-coverage type from different package policies | Each renders as its own column (e.g., "GL (BOP #1)" and "GL (BOP #2)") — carrier name disambiguates. |
| Assign BOP with 3 sub-coverages, 2 have limits | Two tower line rows created (GL, Property). The third (no limit) is excluded until limit is entered on policy edit page. |
| WC assigned to program | WC stays as statutory column (no limit-based height). EL sub-coverage gets its own column with limit-based height. |
| SOI ghost rows | Sub-coverage ghost rows already appear in Schedule of Insurance via `get_schedule_data()`. Ghost rows show sub-coverage type, limit, deductible from `policy_sub_coverages`. |
| Toggle tower line to excluded | Line stays in underlying matrix for reference but disappears from tower visualization and cannot be selected in "Covers". |

---

## 8. Verification

1. **Creation flow:** Click "+ New Program" on client Policies tab → enter name → lands on schematic page with empty matrices
2. **Schematic header:** Program name, term, status, total premium all visible and editable
3. **Assign existing:** Unassigned policies panel shows policies without tower_group; clicking "+ Assign" adds them to underlying lines
4. **Quota share:** Two excess rows at same attachment point render side-by-side in tower preview with QS badge
5. **Limit sync:** Editing limit/attachment on schematic auto-populates `layer_notation`; policy detail page shows tower position badge
6. **Client page:** Single "Programs" section with cards showing summary + mini tower viz; no separate Tower Structure section
7. **Program-Linked Policies:** Collapsed section still shows individual policies belonging to programs
8. **Package assign flow:** Assign BOP to program → GL, Property sub-coverages appear as separate underlying lines with correct limits and "(from BOP)" badge
9. **Multi-line umbrella:** Add Umbrella → "Covers" selector shows GL + Auto → umbrella spans both columns in tower preview
10. **Sub-coverage limits:** BOP GL sub-coverage at $1M renders as its own column at correct height
11. **Tower line toggle:** Uncheck Property from tower → disappears from tower viz but stays in matrix
12. **WC/EL handling:** Assign WC → WC statutory column + EL column with limit
13. **SOI ghost rows:** Schedule of Insurance shows sub-coverage ghost rows with limits from `policy_sub_coverages`
14. **Covers pill selector:** Each excess row shows covered lines as pills with × to remove, + to add
15. **Backward compatibility:** Existing programs with `tower_group` values display correctly; single-column towers still work when no tower lines are defined
