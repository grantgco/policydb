# Programs v2 — Standalone Entities + Universal Ghost Rows

**Date:** 2026-03-27
**Status:** Draft
**Supersedes:** `2026-03-18-programs-aggregate-design.md`, `2026-03-18-program-carriers-table-design.md`, `2026-03-26-program-redesign-unified-flow-design.md`, `2026-03-26-program-schematic-entry-design.md`
**Companion:** `2026-03-26-package-policy-sub-coverages-design.md` (sub-coverages spec, implemented together)

---

## Problem

Programs pretend to be policies. A policy with `is_program=1` acts as a container, but it's still a row in the policies table — with required fields that don't apply (carrier, policy_number, effective_date), exclusion logic scattered across every view, and a confusing creation flow where a user must "sacrifice" a policy to become a program.

The current model has **six related tables/columns** that all exist because programs were shoehorned into the policies schema:
- `is_program` flag on policies
- `program_id` FK on policies (child → parent)
- `tower_group` text label on policies
- `program_carriers` table (duplicates data from child policies)
- `program_tower_coverage` junction table
- `program_tower_lines` junction table

Meanwhile, ghost rows (sub-coverages appearing in coverage sections they belong to) are implemented ad-hoc for the package sub-coverages spec but have no universal pattern.

---

## Design Decisions (Validated via Brainstorm)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Program identity | **Standalone `programs` table** — not a policy row | Programs are containers, not policies. Separate table eliminates `is_program` confusion and required-field hacks |
| 2 | `program_carriers` table | **Eliminated** — child policies ARE the carrier/premium source of truth | Removes data duplication and sync bugs. Carrier list derived from children. |
| 3 | Ghost row mechanism | **Convention-based** — renderer checks known relationships, applies standard ghost template | No new storage table. Each ghost reason (sub-coverage, program membership) teaches the renderer one more relationship type. Extensible without schema changes. |
| 4 | Program page structure | **Detail page + schematic tab** — programs get a home page for daily management; schematic/tower is a tab within it | Separates "manage this program" from "visualize the tower" |
| 5 | Migration strategy | **Parallel introduction + gradual migration** — new table alongside existing model, phased cutover | Each phase independently shippable. Existing data keeps working until migration step. |
| 6 | Sub-coverage fields | **Expand with premium, carrier override, participation_of, policy_number override** | Ghost rows need enough data to be useful without navigating to parent |

---

## 1. New `programs` Table

### Schema

```sql
CREATE TABLE IF NOT EXISTS programs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uid         TEXT NOT NULL UNIQUE,
    client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name                TEXT NOT NULL DEFAULT '',
    line_of_business    TEXT DEFAULT '',
    effective_date      DATE,
    expiration_date     DATE,
    renewal_status      TEXT NOT NULL DEFAULT 'Not Started',
    milestone_profile   TEXT DEFAULT '',
    lead_broker         TEXT DEFAULT '',
    placement_colleague TEXT DEFAULT '',
    account_exec        TEXT NOT NULL DEFAULT 'Grant',
    notes               TEXT DEFAULT '',
    working_notes       TEXT DEFAULT '',
    last_reviewed_at    DATETIME,
    review_cycle        TEXT DEFAULT '1w',
    archived            INTEGER NOT NULL DEFAULT 0,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_programs_client ON programs(client_id);
CREATE INDEX IF NOT EXISTS idx_programs_uid ON programs(program_uid);
```

### What lives on `programs` vs child `policies`

| Programs table | Child policies (as today) |
|---|---|
| Program name | Carrier |
| Client (FK) | Policy number |
| Effective / Expiration (program term) | Premium |
| Renewal status | Limit / Retention / Deductible |
| Lead broker / placement colleague | Layer position (Primary, Excess, Umbrella) |
| Milestone profile + timeline | Policy type / LOB |
| Account exec | Attachment point / Participation |
| Notes / working notes | Coverage form |
| Review cycle | Exposure data |

### Derived/aggregated from children

- **Total premium** — `SUM(premium)` from child policies
- **Total limit** — display max or sum depending on context
- **Carrier list** — `DISTINCT carrier` from child policies
- **Carrier count** — count of distinct carriers
- **Milestone progress** — aggregate of child policy checklists

### Program UIDs

Auto-generated sequential `PGM-001`, `PGM-002`, etc. via a new `next_program_uid()` function in `db.py` (same pattern as `next_policy_uid()`).

---

## 2. Updated `policies` Table — Program FK

### Replace `program_id` FK target

**Current:** `program_id INTEGER REFERENCES policies(id)` — points to a policy row with `is_program=1`

**New:** `program_id INTEGER REFERENCES programs(id) ON DELETE SET NULL` — points to the new `programs` table

**Migration approach:** Add `program_id_new` column, populate from old `program_id` via mapping, then rename. The old `is_program`, `program_id`, `tower_group` columns remain but are deprecated.

### Deprecated columns (kept in schema, ignored in code)

- `is_program` — replaced by existence in `programs` table
- `tower_group` — replaced by `programs.name`
- `program_carriers` (TEXT) — already deprecated
- `program_carrier_count` — already deprecated

---

## 3. `program_carriers` Table — Eliminated

The `program_carriers` table is **dropped** after migration. All carrier/premium/limit data lives on child policies. The program's carrier breakdown is a query against its children:

```sql
SELECT DISTINCT carrier, policy_number, premium, limit_amount, layer_position
FROM policies
WHERE program_id = ?
ORDER BY layer_position, carrier
```

### Quota Share Handling

For quota share arrangements where multiple carriers split a single layer:
- Each carrier's participation is a separate child policy with the same `layer_position` and `attachment_point`
- The tower renderer detects shared attachment points and renders them side-by-side
- No separate table needed — the relationship is implicit in the data

---

## 4. Sub-Coverage Field Expansion

### Current `policy_sub_coverages` fields
- `coverage_type`, `sort_order`, `limit_amount`, `deductible`, `coverage_form`, `notes`, `attachment_point`, `created_at`

### New fields needed for ghost rows

| Field | Type | Default | Rationale |
|---|---|---|---|
| `premium` | REAL | NULL | Some sub-coverages have their own premium (e.g., EL has separate premium from WC). NULL = inherits parent display as "—" |
| `carrier` | TEXT | NULL | Override field. NULL = inherit from parent policy. For rare cases where sub-coverage is placed with different carrier |
| `policy_number` | TEXT | NULL | Override field. NULL = inherit from parent. For sub-coverages with their own policy number |
| `participation_of` | REAL | NULL | For layered sub-coverages in tower — total layer participation |
| `layer_position` | TEXT | NULL | Override for tower: NULL = 'Primary'. Allows a sub-coverage to be 'Excess' or 'Umbrella' independently |
| `description` | TEXT | DEFAULT '' | Brief description for schedule display |

### Fields NOT added (inherit from parent)

| Field | Why not |
|---|---|
| `effective_date` / `expiration_date` | Sub-coverages share parent's term. If dates differ, it's a separate policy. |
| `renewal_status` | Managed at parent level. Sub-coverages don't renew independently. |
| `follow_up_date` | Tracked at parent level. |
| `placement_colleague` / `underwriter_name` | Usually same as parent. Override not worth the complexity. |
| `exposure_*` fields | Exposures link at the policy level via `policy_exposure_links`. |
| `archived` | If parent is archived, sub-coverages are too (CASCADE). |

### Ghost Row Field Resolution

When rendering a ghost row, the convention is **sub-coverage field wins if populated, else fall back to parent policy**:

```python
def resolve_ghost_fields(sub_cov: dict, parent_policy: dict) -> dict:
    """Build a ghost row dict by merging sub-coverage overrides with parent."""
    return {
        "coverage_type": sub_cov["coverage_type"],
        "limit_amount": sub_cov["limit_amount"],
        "deductible": sub_cov["deductible"],
        "coverage_form": sub_cov["coverage_form"] or parent_policy["coverage_form"],
        "premium": sub_cov.get("premium"),  # None = show "—" to avoid double-counting
        "carrier": sub_cov.get("carrier") or parent_policy["carrier"],
        "policy_number": sub_cov.get("policy_number") or parent_policy["policy_number"],
        "attachment_point": sub_cov.get("attachment_point"),
        "participation_of": sub_cov.get("participation_of"),
        "layer_position": sub_cov.get("layer_position") or "Primary",
        "notes": sub_cov.get("notes") or sub_cov.get("description") or "",
        # Always inherited
        "effective_date": parent_policy["effective_date"],
        "expiration_date": parent_policy["expiration_date"],
        "policy_uid": parent_policy["policy_uid"],
        "client_id": parent_policy["client_id"],
        # Ghost metadata
        "is_ghost": True,
        "ghost_reason": "sub_coverage",  # or "program_member"
        "ghost_source_id": sub_cov["id"],
        "ghost_parent_uid": parent_policy["policy_uid"],
        "ghost_badge": "Package",  # or "Program"
    }
```

---

## 5. Universal Ghost Row Convention

### What is a ghost row?

A ghost row is a **read-only reference** to a real record that appears in a view where it logically belongs but doesn't canonically live. It's a display-time concept, not a data model concept.

### Ghost row sources (convention-based)

| Source | Relationship | Ghost appears in | Badge |
|---|---|---|---|
| Package sub-coverage | `policy_sub_coverages.coverage_type` matches a schedule section | Schedule of Insurance coverage section | `Package` (indigo) |
| Package sub-coverage | Sub-coverage has `limit_amount` and parent is in a program | Program tower as underlying column | `Package` (indigo) |
| Program child policy | Policy has `program_id` | Client-level schedule grouped by program | `Program` (blue) |
| Future: linked account policy | Policy shared across linked accounts | Other account's schedule | `Linked` (purple) |

### Rendering rules (universal)

All ghost rows follow these conventions:

1. **Muted styling** — `text-gray-400 italic` (lighter than real rows)
2. **Badge** — Colored pill showing the reason: `Package`, `Program`, `Linked`
3. **Premium** — Shows "—" (dash) unless the sub-coverage/child has its own `premium` value. Prevents double-counting.
4. **Click target** — Clicking the ghost row navigates to the canonical record (parent policy or program)
5. **Non-editable** — Ghost rows cannot be edited in-place. Edit at the source.
6. **Sort position** — Ghost rows appear after standalone records within the same section
7. **Print** — Ghost rows print with lighter styling + badge, clearly distinguishable from real rows

### Implementation pattern

Ghost rows are injected at **Python query time**, not at the SQL view level:

```python
def inject_ghost_rows(rows: list[dict], conn, client_id: int) -> list[dict]:
    """Inject ghost rows from known relationship types."""
    ghosts = []

    # 1. Package sub-coverages → schedule sections
    sub_covs = get_sub_coverages_for_client(conn, client_id)
    for sc in sub_covs:
        ghost = resolve_ghost_fields(sc, sc["_parent_policy"])
        ghosts.append(ghost)

    # 2. Program children → program summary sections
    # (handled separately in program detail view)

    # Merge and sort
    all_rows = rows + ghosts
    all_rows.sort(key=lambda r: (r.get("coverage_type", ""), r.get("is_ghost", False)))
    return all_rows
```

Each view (schedule, matrix, tower) calls `inject_ghost_rows()` after its main query, then the template checks `row.is_ghost` to apply ghost styling.

---

## 6. Program Detail Page

### URL: `/programs/{program_uid}`

### Tab structure (4 tabs, lazy-loaded via HTMX)

| Tab | Content |
|---|---|
| **Overview** | Program header (name, term, status, totals), child policy list with quick-edit, working notes panel |
| **Schematic** | Underlying lines matrix + excess layers matrix + live D3 tower preview (existing schematic page content, migrated) |
| **Timeline** | Milestone timeline (same as policy timeline but at program level), health indicators, accountability tracking |
| **Activity** | Activity log, follow-ups, notes — rolled up from program + all child policies |

### Overview tab

```
┌─────────────────────────────────────────────────────────────────┐
│ PGM-001  Casualty Program                                       │
│ Acme Holdings Inc.                                [← Client]    │
│                                                                 │
│ Term: Apr 1, 2026 – Apr 1, 2027    Status: [In Progress ▾]     │
│ Total Premium: $1,245,000    Carriers: 5    Policies: 7         │
├─────────────────────────────────────────────────────────────────┤
│ CHILD POLICIES                                                  │
│ ┌───────────────┬──────────┬──────────┬──────────┬────────────┐ │
│ │ Policy        │ Carrier  │ Premium  │ Limit    │ Layer      │ │
│ ├───────────────┼──────────┼──────────┼──────────┼────────────┤ │
│ │ POL-042 GL    │ AIG      │ $350K    │ $1M      │ Primary    │ │
│ │ POL-043 Auto  │ Liberty  │ $125K    │ $1M      │ Primary    │ │
│ │ POL-044 UMB   │ National │ $420K    │ $5M xs1M │ Umbrella   │ │
│ │ POL-045 XS1   │ Zurich   │ $200K    │ $10M xs6M│ Excess     │ │
│ │ POL-046 XS2   │ Markel   │ $150K    │ $10Mxs16M│ Excess     │ │
│ │   └ BOP GL    │ (Acme)   │ —        │ $1M      │ Primary 📦│ │
│ │   └ BOP Prop  │ (Acme)   │ —        │ $500K    │ — (excl)  │ │
│ └───────────────┴──────────┴──────────┴──────────┴────────────┘ │
│ [+ Assign Policy]  [+ Add New Policy to Program]                │
└─────────────────────────────────────────────────────────────────┘
```

**Child policy rows** are real policy rows with quick-edit capability (status, follow-up, premium via popover — same as client page pattern).

**Ghost rows** from package sub-coverages appear indented under their parent with the Package badge. Non-editable, click navigates to parent policy.

### Schematic tab

Migrated from current `/clients/{client_id}/programs/{tower_group}` page. Same underlying/excess matrices, same cell editing, same D3 preview. Key changes:

- **Data source:** Queries policies where `program_id = programs.id` instead of `tower_group = ?`
- **Unassigned panel:** Shows client policies not assigned to any program
- **Package-aware assignment:** Assigning a package policy explodes its sub-coverages into tower lines (existing behavior, preserved)

### Assign / Unassign

- **"+ Assign Policy"** — shows dropdown of client policies not in any program. Selecting one sets `program_id` on the policy.
- **"+ Add New Policy to Program"** — opens inline form to create a new policy pre-linked to this program.
- **Remove** — clears `program_id` on the policy. Policy returns to standalone status.

---

## 7. Client Page — Programs Section

### Unified section (replaces Corporate Programs + Tower Structure)

```
PROGRAMS · 2 programs · $2.45M total premium         [+ New Program]

┌─────────────────────────────────────────────────────────────────┐
│ PGM-001  Casualty    5 carriers · $1.25M   [Bound]  Open → │
│ ┌─ GL · Auto · EL ─┬─ UMB $5M ─┬─ XS $10M ─┬─ XS $10M ──────┐│
│ └──────────────────┴────────────┴────────────┴─────────────────┘│
├─────────────────────────────────────────────────────────────────┤
│ PGM-002  Property    3 carriers · $1.20M   [In Prog] Open → │
│ ┌─ Prop · IM ───────┬─ XS $25M ───────────────────────────────┐│
│ └───────────────────┴──────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

Each program card shows:
- **PGM badge** + program UID + program name
- **Summary:** carrier count, total premium (aggregated from children), renewal status badge
- **Mini tower bar:** Horizontal proportional visualization of tower layers
- **"Open →"** link to `/programs/{program_uid}`

### Creation flow

**Button:** `+ New Program` on client Policies tab

**Inline form:**
- Program Name — text input
- Primary Line of Business — optional combobox from `policy_types`
- "Create & Open →" button

**On submit:**
1. INSERT into `programs` table with `client_id`, `name`, `line_of_business`
2. Assign `program_uid` via `next_program_uid()`
3. Redirect to `/programs/{program_uid}`

---

## 8. Timeline Engine Changes

### Generate at program level (preserved behavior)

The timeline engine already generates milestones for programs and skips children. The only change is the **source table**:

**Current:** `SELECT ... FROM policies WHERE is_program = 1 OR program_id IS NULL`
**New:** Timeline generation queries the `programs` table directly for program-level milestones

Child policies (with `program_id IS NOT NULL`) continue to be skipped — they inherit the program's timeline.

### Review cascade (preserved behavior)

Marking a program as reviewed cascades `last_reviewed_at` to all child policies. Same logic, different source:

```python
# Current: check is_program on policies table
# New: check if UID starts with PGM- or look up programs table
program = conn.execute("SELECT id FROM programs WHERE program_uid = ?", (uid,)).fetchone()
if program:
    conn.execute(
        "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE program_id = ?",
        (program["id"],)
    )
```

---

## 9. Migration Plan (Phased)

### Phase 1: New table + ghost row convention

1. **Migration: Create `programs` table**
2. **Migration: Add new sub-coverage fields** (`premium`, `carrier`, `policy_number`, `participation_of`, `layer_position`, `description`)
3. **Build ghost row utility** — `inject_ghost_rows()` in a new `src/policydb/ghost_rows.py`
4. **Wire ghost rows into schedule** — sub-coverage ghosts appear in coverage sections
5. **Tests** for ghost row injection and field resolution

### Phase 2: Program detail page

1. **New route:** `/programs/{program_uid}` with 4 tabs
2. **New templates:** `programs/detail.html`, `programs/_tab_overview.html`, `programs/_tab_schematic.html`, `programs/_tab_timeline.html`, `programs/_tab_activity.html`
3. **Migrate schematic content** from `/clients/{id}/programs/{tower_group}` to schematic tab
4. **Program CRUD endpoints:** create, rename, update header, delete
5. **Assign/unassign endpoints** — set/clear `program_id` on policies

### Phase 3: Data migration + cleanup

1. **Migration: Populate `programs` from existing `is_program=1` rows**
   - For each `policies` row where `is_program=1`:
     - INSERT into `programs` (name=tower_group, client_id, dates, status, etc.)
     - UPDATE child policies: `SET program_id = new_programs.id WHERE program_id = old_policy.id`
2. **Migration: Drop `program_carriers` table** (data now lives on child policies)
3. **Deprecate columns:** `is_program`, `tower_group`, old `program_id` FK — leave in schema, remove from application code
4. **Update all views** (`v_policy_status`, `v_schedule`, `v_tower`, `v_client_summary`, `v_renewal_pipeline`, `v_review_queue`) to reference `programs` table instead of `is_program` flag
5. **Update reconciler** to match against programs via children
6. **Redirect old URLs:** `/clients/{id}/programs/{tower_group}` → `/programs/{program_uid}`

### Phase 4: Cleanup

1. Remove all `is_program` checks from Python code
2. Remove `program_carriers` table references
3. Remove `tower_group` from views and queries
4. Update email template tokens for programs
5. Update exporter for new program model
6. Update LLM import schemas

---

## 10. `program_tower_coverage` and `program_tower_lines` — Updated FKs

These tables currently reference `policies(id)` for the program. After migration:

### `program_tower_lines`

```sql
-- Updated to reference programs table
ALTER TABLE program_tower_lines ADD COLUMN program_id_new INTEGER REFERENCES programs(id) ON DELETE CASCADE;
-- Populate from mapping, then rename
```

### `program_tower_coverage`

No FK changes needed — this table links excess policies to underlying policies/sub-coverages. The program relationship is implicit through the policies' `program_id`.

---

## 11. Views Impact

### Updated views

| View | Current program handling | New handling |
|---|---|---|
| `v_policy_status` | `is_program` flag, subquery for `program_carriers` | JOIN to `programs` table for program metadata. No more `program_carriers` subquery. |
| `v_schedule` | `[PROGRAM]` suffix if `is_program=1`, carriers from `program_carriers` table | Programs get their own section. Child policies show program badge. Ghost rows from sub-coverages injected at Python level. |
| `v_tower` | Groups by `tower_group` text | Groups by `program_id` FK. `programs.name` replaces `tower_group`. |
| `v_client_summary` | `program_count` via `is_program=1` | `program_count` via JOIN to `programs` table |
| `v_renewal_pipeline` | `AND (is_program = 0 OR is_program IS NULL)` | Programs excluded by nature (they're in a different table). Child policies with `program_id IS NOT NULL` excluded. |
| `v_review_queue` | `AND (program_id IS NULL)` | Same logic — child policies excluded. Programs get their own review queue entry from `programs` table. |

---

## 12. Reconciler Impact

### Matching changes

**Current:** Reconciler matches against `program_carriers` rows for structured carrier/policy-number matching.

**New:** Reconciler matches import rows against child policies directly. A program match is inferred when multiple import rows match different child policies that share the same `program_id`.

### Program summary in reconcile results

Group matched child policies by `program_id` in the results UI. Show:
- Program name and total premium
- Per-child-policy match status
- Running total: "4 of 5 policies reconciled · $1.1M of $1.25M matched"

---

## 13. Email Template Tokens

### New tokens

| Token | Source | Value |
|---|---|---|
| `{{program_name}}` | `programs.name` | "Casualty Program" |
| `{{program_uid}}` | `programs.program_uid` | "PGM-001" |
| `{{program_carriers}}` | Distinct carriers from child policies | "AIG, Liberty Mutual, National Indemnity" |
| `{{program_carrier_count}}` | Count of distinct carriers | "5" |
| `{{program_total_premium}}` | Sum of child policy premiums | "$1,245,000" |
| `{{program_total_limit}}` | Aggregated limit display | "$36,000,000" |
| `{{sub_coverages}}` | Comma-separated sub-coverage types | "General Liability, Property" |

---

## 14. Edge Cases

| Scenario | Behavior |
|---|---|
| Policy removed from program | `program_id` set to NULL. Policy becomes standalone. Tower lines cleaned up. |
| Program deleted | All child policies have `program_id` set to NULL (ON DELETE SET NULL). Programs table row removed. Tower lines/coverage CASCADE deleted. |
| Policy in two programs | Not allowed. `program_id` is a single FK. A policy belongs to at most one program. |
| Program with zero children | Valid empty state. Overview shows "No policies assigned yet" prompt. |
| Ghost row from sub-coverage with premium override | Shows the sub-coverage's own premium in schedule. Parent policy's premium is reduced by that amount in display (TBD — may be too complex; simpler to show parent premium as-is and sub-coverage premium separately). |
| Sub-coverage with carrier override | Ghost row shows the overridden carrier. Rare but valid (e.g., BOP with GL placed separately). |
| Existing `is_program=1` rows during migration | Phase 3 migration maps them to `programs` table rows. Until Phase 3, old code paths still work. |
| Reconciler import during Phase 1-2 | Old reconciler code still works against `is_program` flag. Phase 3 updates reconciler. |
| Program UID in ref tags | `build_ref_tag()` updated to handle `PGM-` prefix. Copy format: `[PDB:CN123-PGM001-POL042]` |

---

## 15. Files

### New files

| File | Purpose |
|---|---|
| `migrations/098_programs_table.sql` | Create `programs` table |
| `migrations/099_sub_coverage_fields.sql` | Add premium, carrier, policy_number, participation_of, layer_position, description to policy_sub_coverages |
| `migrations/100_migrate_programs.sql` | Phase 3: populate programs from is_program=1 rows, update FKs |
| `src/policydb/ghost_rows.py` | Universal ghost row injection utility |
| `src/policydb/web/routes/programs_v2.py` | New program CRUD + detail page routes |
| `templates/programs/detail.html` | Program detail page (4-tab layout) |
| `templates/programs/_tab_overview.html` | Overview tab partial |
| `templates/programs/_tab_schematic.html` | Schematic tab (migrated from schematic.html) |
| `templates/programs/_tab_timeline.html` | Timeline tab partial |
| `templates/programs/_tab_activity.html` | Activity tab partial |

### Modified files

| File | Change |
|---|---|
| `db.py` | Wire migrations, `next_program_uid()`, program queries |
| `views.py` | Update all views for programs table JOIN |
| `queries.py` | Program-aware queries, ghost row data fetching |
| `timeline_engine.py` | Generate timelines from programs table |
| `reconciler.py` | Match against child policies, infer program grouping |
| `email_templates.py` | Program tokens, sub_coverages token |
| `exporter.py` | Export programs from new table |
| `utils.py` | `build_ref_tag()` for PGM- prefix |
| `routes/clients.py` | Query programs for client detail, pass to templates |
| `routes/action_center.py` | Program milestones from new table |
| `routes/review.py` | Review cascade from programs table |
| `templates/clients/_tab_policies.html` | Unified Programs section |
| `templates/clients/_programs.html` | Rewrite for new model |
| `config.py` | Add program-related config defaults |
| `settings.py` | Program config lists in Settings UI |

---

## 16. Verification

### Phase 1
1. Sub-coverage fields migration runs clean
2. Ghost rows appear in schedule for package policies
3. Ghost row styling: muted, badge, non-editable, click navigates to parent
4. Premium shows "—" on ghost rows without explicit premium
5. Sub-coverage with carrier override shows overridden carrier in ghost row

### Phase 2
1. Create program via client Policies tab → lands on program detail page
2. Program detail shows 4 tabs, all lazy-load correctly
3. Assign existing policy to program → appears in child policy list
4. Schematic tab shows underlying/excess matrices with live tower preview
5. Package policy assignment explodes sub-coverages into tower lines
6. Program header editable: name, term, status all save on blur
7. Program totals auto-update when child policies change

### Phase 3
1. Migration maps all existing `is_program=1` rows to `programs` table
2. Child policies' `program_id` FK correctly points to new `programs.id`
3. `program_carriers` table dropped without data loss
4. All views render correctly with new JOINs
5. Reconciler matches work against child policies
6. Old URLs redirect to new program detail page
7. Timeline engine generates from programs table
8. Review cascade works from programs table

### Phase 4
1. No remaining references to `is_program` in Python code
2. No remaining references to `program_carriers` table
3. No remaining references to `tower_group` in queries
4. All tests pass
5. Full QA on: schedule, tower, client detail, reconcile, export, import, timeline, review queue
