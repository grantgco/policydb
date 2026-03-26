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

### 2b. Unassigned Policies Panel

**New section** below the two matrices, above the tower preview.

Shows policies for this client that are not assigned to any program (no `tower_group` or `program_id`):

```
Unassigned Policies (3)
[Workers' Comp — $18K  + Assign]  [Employers' Liab — $12K  + Assign]  [Hired Auto — $8K  + Assign]
```

**"+ Assign" action:**
- Sets the policy's `tower_group` to this program's name
- Sets `program_id` to the program policy's `id` (establishing the parent-child FK link)
- Sets `layer_position = 'Primary'` (default — user can change on schematic)
- Assigns next `schematic_column`
- Returns the policy as a new underlying row via HTMX swap
- Removes from unassigned list

**"× Remove" action** on existing rows:
- Clears `tower_group` and `program_id` on the policy
- Moves it back to unassigned (or to the standalone policies list on client page)

**Route:** `POST /clients/{client_id}/programs/{tower_group}/assign/{policy_uid}`
**Route:** `POST /clients/{client_id}/programs/{tower_group}/unassign/{policy_uid}`

**Query for unassigned:**
```sql
SELECT policy_uid, policy_type, carrier, premium
FROM policies
WHERE client_id = ? AND archived = 0
  AND (is_opportunity = 0 OR is_opportunity IS NULL)
  AND (tower_group IS NULL OR tower_group = '')
  AND (program_id IS NULL)
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

### New Migrations

```sql
-- Migration A: Add layer_notation column for display
ALTER TABLE policies ADD COLUMN layer_notation TEXT;

-- Migration B: Tower coverage junction (which excess covers which underlying lines)
CREATE TABLE IF NOT EXISTS program_tower_coverage (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    excess_policy_id          INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    underlying_policy_id      INTEGER REFERENCES policies(id) ON DELETE CASCADE,
    underlying_sub_coverage_id INTEGER REFERENCES policy_sub_coverages(id) ON DELETE CASCADE,
    CHECK (underlying_policy_id IS NOT NULL OR underlying_sub_coverage_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_ptc_excess ON program_tower_coverage(excess_policy_id);
CREATE INDEX IF NOT EXISTS idx_ptc_underlying ON program_tower_coverage(underlying_policy_id);
CREATE INDEX IF NOT EXISTS idx_ptc_subcov ON program_tower_coverage(underlying_sub_coverage_id);
```

**Note:** `policy_sub_coverages` with `limit_amount`, `deductible`, and `notes` columns is already built on the `nervous-jones` branch (migrations 090–092). These must be merged first.

### New Junction Table: `program_tower_coverage`

Tracks which excess/umbrella layers cover which underlying tower lines. This is the "Covers" relationship from §2d.

```sql
CREATE TABLE IF NOT EXISTS program_tower_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    excess_policy_id   INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    underlying_policy_id INTEGER REFERENCES policies(id) ON DELETE CASCADE,
    underlying_sub_coverage_id INTEGER REFERENCES policy_sub_coverages(id) ON DELETE CASCADE,
    CHECK (underlying_policy_id IS NOT NULL OR underlying_sub_coverage_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_ptc_excess ON program_tower_coverage(excess_policy_id);
```

- `excess_policy_id` — the umbrella/excess policy
- `underlying_policy_id` — a standalone underlying policy (e.g., Auto Liability), OR
- `underlying_sub_coverage_id` — a sub-coverage from a package policy (e.g., GL from BOP)
- Exactly one of `underlying_policy_id` / `underlying_sub_coverage_id` must be set

This lets the tower preview know which columns an umbrella spans.

### Preserved Model

The existing program model is preserved:
- `is_program` flag on `policies` table
- `tower_group` text field on `policies` table
- `program_id` FK on `policies` table for parent-child links
- `program_carriers` table for multi-carrier program layers
- `policy_sub_coverages` table (from `nervous-jones` branch) for package sub-coverage limits

The unification is primarily at the **UI/UX level** — the underlying data model stays the same, with the addition of `program_tower_coverage` for multi-column tower relationships and `layer_notation` for display caching.

---

## 5. Files

### New Files

| File | Purpose |
|------|---------|
| `migrations/NNN_layer_notation.sql` | Add `layer_notation` column |
| `migrations/NNN_program_tower_coverage.sql` | Tower coverage junction table |
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
| `charts.py` | Fix `_layer_notation()` to use proper currency shorthand instead of `%g`; add multi-column tower rendering with sub-coverage columns |
| `queries.py` | Add `get_tower_coverage_map()` for excess→underlying line relationships; leverage existing `get_sub_coverages_full_by_policy_id()` from `nervous-jones` |

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

---

## 8. Verification

1. **Creation flow:** Click "+ New Program" on client Policies tab → enter name → lands on schematic page with empty matrices
2. **Schematic header:** Program name, term, status, total premium all visible and editable
3. **Assign existing:** Unassigned policies panel shows policies without tower_group; clicking "+ Assign" adds them to underlying lines
4. **Quota share:** Two excess rows at same attachment point render side-by-side in tower preview with QS badge
5. **Limit sync:** Editing limit/attachment on schematic auto-populates `layer_notation`; policy detail page shows tower position badge
6. **Client page:** Single "Programs" section with cards showing summary + mini tower viz; no separate Tower Structure section
7. **Program-Linked Policies:** Collapsed section still shows individual policies belonging to programs
8. **Multi-line umbrella:** Assign BOP + Auto Liability to program → check GL sub-coverage as tower line → add Umbrella covering GL + Auto → tower preview shows umbrella spanning both columns
9. **Sub-coverage limits:** BOP with GL sub-coverage showing $1M limit appears as its own column in the tower at the correct height
10. **Tower line selector:** Package policies show expandable sub-coverage checkboxes; only sub-coverages with `limit_amount` are selectable
11. **Backward compatibility:** Existing programs with `tower_group` values display correctly; single-column towers still work when no "Covers" relationships are defined
