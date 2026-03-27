# Programs v2 Phase 4 — Full Code Cleanup & Cutover

**Date:** 2026-03-27
**Status:** Draft
**Parent spec:** `2026-03-27-programs-v2-standalone-entities-design.md` (Phases 1-3)
**Phase:** 4 of 4

---

## Problem

Phases 1-3 introduced the standalone `programs` table, program detail page, and data migration. But all existing application code still reads `is_program=1` policy rows, queries the `program_carriers` table, and groups by `tower_group` text. The two models coexist — the new `programs` table is populated but unused by most code paths.

Phase 4 cuts over: remove all legacy program references from Python code, drop `program_carriers`, repoint queries to use `program_id` FK, and delete ~900 lines of dead v1 route code.

---

## Blast Radius

| Reference | Python files | Templates | Key hotspots |
|-----------|-------------|-----------|-------------|
| `is_program` | 21 | 7 | reconciler.py (~15), exporter.py (~10), policies.py (~10), programs.py (~15) |
| `program_carriers` | 12 | 1 | policies.py CRUD (~12), reconcile.py (~12), exporter.py (4), views.py (3) |
| `tower_group` | 19 | 9 | charts.py (~15), programs.py (3), schematic templates (5) |

**Baseline tests:** 281 passed, 2 pre-existing failures (compliance pct + LLM schema — unrelated to programs).

---

## Design Decision: `program_carriers` Elimination

**Choice:** Full elimination. Drop the table entirely. Reconciler creates child policies instead of carrier rows. Existing CRUD endpoints for the carrier matrix are removed. Program carriers are always derived from child policies.

**Rationale:** The `program_carriers` table duplicates data that should live on real policy rows. Each carrier participation IS a policy in the correct data model. Keeping the table (even read-only) leaves dead code and confusion.

---

## Cutover Strategy: Layer-by-Layer (Bottom-Up)

Execute in dependency order. Each layer builds on the previous.

| Step | Layer | What changes |
|------|-------|-------------|
| 1 | Migration | Set `program_id` FK on children, convert carrier rows to policies, archive `is_program=1` rows, drop `program_carriers` |
| 2 | Views | Rebuild all views to reference `programs` table instead of `is_program` flag |
| 3 | Core modules | `queries.py`, `timeline_engine.py`, `email_templates.py`, `compliance.py`, `charts.py`, `dedup.py`, `llm_schemas.py`, `analysis.py`, `display.py`, `models.py`, `importer.py` |
| 4 | Routes | All route files: `clients.py`, `policies.py`, `programs.py`, `review.py`, `reconcile.py`, `meetings.py` |
| 5 | Templates | Remove `is_program` conditionals, `tower_group` inputs, carrier matrix includes |
| 6 | Reconciler | Simplify program matching to use child policies directly |
| 7 | Cleanup | Drop table migration, delete dead tests/templates, seed/CLI updates |

**Why this order:** Migration establishes FK relationships. Views depend on migration. Queries depend on views. Routes depend on queries. Templates depend on route context. Reconciler is most complex and benefits from everything else being stable. Cleanup is safe after all code paths updated.

---

## Layer 1: Data Migration

### Migration 101 — `101_phase4_program_cutover.sql` + Python

**Step A: Link child policies to programs via FK**

For each program in the `programs` table, set `program_id` on child policies currently linked by `tower_group` text matching:

```sql
UPDATE policies
SET program_id = (
    SELECT pg.id FROM programs pg
    WHERE pg.client_id = policies.client_id
      AND pg.name = policies.tower_group
      AND pg.archived = 0
)
WHERE tower_group IS NOT NULL AND tower_group != ''
  AND (is_program = 0 OR is_program IS NULL)
  AND program_id IS NULL
  AND archived = 0;
```

**Step B: Convert `program_carriers` rows to child policies**

For each row in `program_carriers`, create a new policy under the corresponding program. The carrier row provides `carrier`, `premium`, `limit_amount`, `policy_number`. The new policy inherits dates, status, and `policy_type` from the program (the `is_program=1` policy row), and gets `program_id` set to the corresponding `programs` table entry.

Python migration logic (in `init_db()`):

```python
# For each program_carriers row:
#   1. Look up the parent is_program=1 policy (program_carriers.program_id -> policies.id)
#   2. Look up the corresponding programs table entry (by client_id + name match)
#   3. Create a new policy with:
#      - carrier, premium, limit_amount, policy_number from carrier row
#      - policy_type, effective_date, expiration_date, client_id, layer_position from parent
#      - program_id = programs.id
#      - policy_uid = next_policy_uid()
#   4. Record mapping for audit trail
```

**Step C: Archive `is_program=1` policy rows**

```sql
UPDATE policies SET archived = 1
WHERE is_program = 1;
```

Don't delete — preserves audit trail and rollback option.

**Step D: Verify integrity**

After migration, assert:
- Every non-archived policy with `tower_group` matching a program name has `program_id` set
- Every `program_carriers` row has a corresponding child policy
- No orphaned `program_id` references

### Migration 102 — `102_drop_program_carriers.sql`

```sql
DROP TABLE IF EXISTS program_carriers;
```

Runs after all code paths are updated (wired at the end of Phase 4 implementation).

---

## Layer 2: Views

All views updated in `src/policydb/views.py`:

### `v_policy_status`

- **Remove:** `p.is_program` from SELECT
- **Remove:** `program_carriers` / `program_carrier_count` subqueries (computed from dropped table)
- **Add:** `p.program_id`, LEFT JOIN to `programs` for `programs.name AS program_name`, `programs.program_uid`
- **Carrier column:** Always `p.carrier` (no more conditional concat from `program_carriers`)
- **Display label:** No more `|| ' [PROGRAM]'` suffix

### `v_client_summary`

- **Replace:** `COUNT(CASE WHEN p.is_program = 1 THEN 1 END) AS program_count`
- **With:** `(SELECT COUNT(*) FROM programs WHERE client_id = c.id AND archived = 0) AS program_count`
- **Policy counts:** Continue to count all non-archived, non-opportunity policies. Child policies count as real policies.

### `v_schedule`

- **Remove:** `[PROGRAM]` label logic
- **Remove:** Carrier concat from `program_carriers` table
- **Result:** Programs don't appear as schedule rows. Child policies appear with their own carrier. Ghost rows handled at Python level.

### `v_renewal_pipeline`

- **Replace:** `AND (p.is_program = 0 OR p.is_program IS NULL)`
- **With:** `AND p.program_id IS NULL` — only standalone policies in pipeline (child policies managed at program level)

### `v_review_queue`

- **Replace:** `is_program` filter with `program_id IS NULL`
- **Programs:** Get their own review entries via separate query at route level from `programs` table

---

## Layer 3: Core Modules

### `queries.py` — FK junction fix

All 6 program query functions switch from `tower_group` string matching to `program_id` FK:

| Function | Old signature | New signature |
|----------|--------------|---------------|
| `get_program_child_policies` | `(conn, program_name, client_id)` | `(conn, program_id)` |
| `get_program_aggregates` | `(conn, program_name, client_id)` | `(conn, program_id)` |
| `get_programs_for_client` | `(conn, client_id)` | `(conn, client_id)` — calls aggregates by id |
| `get_unassigned_policies` | `(conn, client_id)` | `(conn, client_id)` — checks `program_id IS NULL` |
| `get_program_timeline_milestones` | `(conn, program_name, client_id)` | `(conn, program_id)` |
| `get_program_activities` | `(conn, program_name, client_id)` | `(conn, program_id)` |

WHERE clauses simplify from `tower_group = ? AND client_id = ? AND (is_program = 0 OR is_program IS NULL)` to `program_id = ?`.

### `timeline_engine.py`

- Remove `is_program` from SELECT columns (lines 91, 120)
- Existing `program_id IS NOT NULL → skip` logic preserved (children inherit program timeline)
- No functional change, just drop unused column reference

### `email_templates.py`

- **Remove:** `if row["is_program"]: query program_carriers` branch
- **Replace with:** If policy has `program_id`, look up program from `programs` table, derive carriers from `SELECT DISTINCT carrier FROM policies WHERE program_id = ?`
- Token definitions in `CONTEXT_TOKEN_GROUPS` unchanged (names stay the same)

### `compliance.py`

- Replace `p.is_program` checks with `p.program_id IS NOT NULL` for child detection
- Replace program carrier lookup from `program_carriers` table with child policy query
- `if p.get("is_program"): p["children"] = []` becomes a JOIN to `programs` table
- Sort by program membership instead of `is_program DESC`

### `charts.py`

- Replace `tower_group` grouping with `program_id` FK grouping
- Replace `is_program = 1` filter with query against `programs` table
- Replace `program_carriers` lookup with child policy query
- D3 rendering data structure: `tower_group` key becomes `program_name` from `programs.name`

### `dedup.py`

- Remove `if a.get("is_program") and b.get("is_program"): return None` — programs are in a separate table now, guard is unnecessary

### `llm_schemas.py`

- Replace `WHERE client_id = ? AND is_program = 1` with query against `programs` table

### `analysis.py` / `display.py`

- Replace `tower_group` grouping with `program_id` FK

### `models.py`

- Remove `tower_group` from Policy pydantic model (or mark as deprecated Optional)

### `importer.py`

- Keep `tower_group` as an importable field alias (stored on policy but no longer the grouping mechanism)
- Reconciler handles program assignment separately

---

## Layer 4: Routes

### `programs.py` — Major rewrite

**v2 routes (lines 69-437):** Stay mostly unchanged. Fix: pass `program.id` instead of `program.name` to updated `queries.py` functions.

**v1 legacy routes (lines 441-1334):** Delete entirely (~900 lines). These are the `tower_group`-based routes for `/clients/{client_id}/programs/{tower_group}/...`. Replace with a single catch-all redirect:

```python
@router.get("/clients/{client_id}/programs/{tower_group}")
async def redirect_legacy_program(client_id: int, tower_group: str, ...):
    """Redirect old tower_group URLs to new program detail page."""
    program = conn.execute(
        "SELECT program_uid FROM programs WHERE client_id = ? AND name = ?",
        (client_id, tower_group)
    ).fetchone()
    if program:
        return RedirectResponse(f"/programs/{program['program_uid']}")
    raise HTTPException(404)
```

### `policies.py`

- **Remove:** `program_carriers` CRUD endpoints (carrier matrix add/delete/reorder/merge/dissolve — ~250 lines)
- **Remove:** `is_program` from policy creation form handler
- **Remove:** `if merged.get("is_program"): query children` conditional in policy detail
- Every policy is just a policy — no "this is actually a program" branch

### `clients.py`

- **Remove:** Legacy `FROM policies WHERE is_program = 1` query
- **Remove:** Corporate programs section querying `is_program=1` + `program_carriers`
- **Remove:** `program_carriers` INSERT during import/merge
- Programs section comes from `get_programs_for_client()` exclusively
- Renewal month summary: replace `is_program` carrier count with programs table subquery

### `review.py`

- Replace `if prog_row and prog_row["is_program"]` with lookup against `programs` table by UID
- Cascade `last_reviewed_at` to child policies via `WHERE program_id = ?`

### `reconcile.py`

- Deferred to Layer 6 (reconciler section)

### `meetings.py`

- Replace `CASE WHEN is_program = 1 THEN 'Program'` with LEFT JOIN to `programs` via `program_id`

### Other routes (`action_center.py`, `activities.py`, `dashboard.py`)

- Check for any `is_program` references, update to `program_id IS NULL` filter where needed

---

## Layer 5: Templates

### Delete entirely

| Template | Reason |
|----------|--------|
| `policies/_program_carriers_matrix.html` | CRUD for dropped table |
| `programs/schematic.html` | Standalone v1 schematic page (replaced by v2 tab) |

### `is_program` removal

| Template | Change |
|----------|--------|
| `policies/new.html` | Remove "This is a Program" checkbox, `toggleProgramMode()` JS, `is_program` form field |
| `policies/_tab_details.html` | Remove `{% if policy.is_program %}` block and `_program_carriers_matrix.html` include |
| `reconcile/_create_form.html` | Remove `is_program` checkbox |
| `reconcile/_pairing_board.html` | Replace `r.is_program_match` with updated reconciler output |
| `compliance/_policy_links.html` | Replace `pol.get('is_program')` with `pol.get('program_id')` grouping |
| `compliance/_requirement_slideover.html` | Same `is_program` → `program_id` replacement |

### `tower_group` removal

| Template | Change |
|----------|--------|
| `policies/new.html` | Remove `tower_group` input, datalist, autocomplete config |
| `policies/_tab_details.html` | Remove `tower_group` input, datalist, AC_FIELDS entry |
| `programs/_tab_schematic.html` | Replace `{{ tower_group \| urlencode }}` URLs with `{{ program.program_uid }}` |
| `programs/_underlying_matrix.html` | Same URL pattern update |
| `programs/_excess_matrix.html` | Same URL pattern update |
| `programs/_schematic_preview.html` | Replace `tower.tower_group` with `tower.program_name` |
| `charts/_chart_tower.html` | Replace `tower.tower_group` with `tower.program_name` |
| `clients/_programs.html` | Ensure all links use `/programs/{{ pgm.program_uid }}` |
| `reconcile/index.html` | Keep `tower_group` as importable column alias (backward compat) |

---

## Layer 6: Reconciler

### Architecture change

**Current (complex):**

```
Import row → try match against program parent (is_program=1 policy)
           → overlay each program_carrier's fields onto parent
           → score against each overlay, pick best
           → track is_program_match + matched_carrier_id
           → programs are "sticky" (accept multiple matches)
```

**New (simple):**

```
Import row → match against child policies directly (1:1, same as any policy)
           → child policies already have their own carrier/premium/limit
           → group results by program_id for display
```

### Scoring preservation guarantee

`_score_pair()` is **completely unchanged** — same weights, same fields, same tiers. Child policies carry the same carrier/premium/limit/policy_number data that the old carrier overlays provided, so scores are identical.

### Code deleted from `reconciler.py`

| Code | Reason |
|------|--------|
| `_resolve_program_carrier()` | No more carrier rows to resolve |
| `_program_carrier_rows` loading | Child policies are loaded as normal DB rows |
| `_program_indices` / sticky logic | Each child policy matches 1:1 |
| `is_program_match` field on ReconcileRow | No longer a distinct match type |
| `matched_carrier_id` field on ReconcileRow | No carrier rows exist |
| Program carrier overlay in Pass 0/1/2 | Direct matching replaces overlay |
| `program_reconcile_summary()` (old version) | Rewritten as simple group-by |

### New `program_reconcile_summary()`

Simplified to group matched policies by `program_id`:

```python
def program_reconcile_summary(results: list[ReconcileRow]) -> dict[int, dict]:
    """Group reconcile results by program for summary display."""
    by_program = {}
    for r in results:
        if r.db and r.db.get("program_id"):
            pid = r.db["program_id"]
            if pid not in by_program:
                by_program[pid] = {"matched": 0, "total_premium": 0.0, "children": []}
            by_program[pid]["matched"] += 1
            by_program[pid]["total_premium"] += float(r.ext.get("premium") or 0)
            by_program[pid]["children"].append(r)
    return by_program
```

### `reconcile.py` route changes

- **Pair confirmation:** Remove `INSERT INTO program_carriers`. Confirming a match confirms a normal policy pair.
- **Program creation during reconcile:** Replace "create is_program=1 policy + program_carriers rows" with "create program in `programs` table + create child policies."
- **Add unmatched to program:** Replace "INSERT INTO program_carriers" with "create child policy with program_id."
- **Pairing board display:** Group matched child policies under program header. Show "4 of 5 matched, $X of $Y" at program level.

---

## Layer 7: Cleanup

### Migration 102 — Drop `program_carriers` table

```sql
DROP TABLE IF EXISTS program_carriers;
```

Wired in `init_db()` after all code paths are updated.

### Dead test code

| Action | File |
|--------|------|
| Delete | `tests/test_program_carriers.py` |
| Update | `tests/test_programs_v2.py` — test FK-based queries |
| Update | `tests/test_reconcile_algorithm.py` — remove carrier matching tests, add child-policy tests |

### Utility files

| File | Change |
|------|--------|
| `seed.py` | Remove `tower_group` param from `add_policy()`, create programs via `programs` table |
| `cli.py` | Remove `tower_group` prompt, display, edit prompts |
| `onboard.py` | Remove `tower_group` from UPDATE statements |

### Exporter rewrite

- Programs come from `programs` table, not `is_program=1` policies
- Carrier list: `SELECT DISTINCT carrier FROM policies WHERE program_id = ?`
- `_compute_completeness()`: remove `is_program` special case
- Program Review sheet: query `programs` table, join to child policies for aggregates

### Deprecated columns (kept in schema, ignored in code)

| Column | Status |
|--------|--------|
| `policies.is_program` | Left as INT DEFAULT 0, never read |
| `policies.tower_group` | Left as TEXT, importable but not used for grouping |
| `policies.program_carriers` (TEXT) | Already deprecated |
| `policies.program_carrier_count` (INT) | Already deprecated |

---

## Scope Summary

| Metric | Count |
|--------|-------|
| New migrations | 2 (101: data migration, 102: drop table) |
| Files deleted | 2 templates + 1 test file |
| Files with major rewrites | 5 (views.py, queries.py, reconciler.py, programs.py, exporter.py) |
| Files with moderate edits | 8 (policies.py, clients.py, charts.py, compliance.py, email_templates.py, reconcile.py route, plus templates) |
| Files with minor edits | ~12 (single-line removals of is_program references) |
| Lines removed (estimated) | ~600 (v1 routes, carrier CRUD, overlay scoring, dead templates) |
| Lines added (estimated) | ~150 (migration, simplified reconciler summary, redirects) |
| Net change | ~-450 lines |

---

## Verification

### Per-layer checks

1. **Migration:** All child policies have `program_id` set. All carrier rows converted. `is_program=1` rows archived.
2. **Views:** `v_policy_status`, `v_schedule`, `v_client_summary`, `v_renewal_pipeline` render correctly with no `is_program` or `program_carriers` references.
3. **Core modules:** `queries.py` functions return correct data via FK joins. Email tokens populate correctly.
4. **Routes:** Program detail page loads. Client programs section renders. Policy detail has no program branch.
5. **Templates:** No Jinja2 errors. No `is_program` or `tower_group` in rendered HTML (except import column mapping).
6. **Reconciler:** Import rows match child policies with identical scores to old overlay method. Program summary groups correctly.
7. **Cleanup:** `program_carriers` table dropped. All tests pass. No remaining `is_program` references in Python code.

### Full QA

After all layers complete: schedule, tower, client detail, reconcile, export, import, timeline, review queue, compliance, charts.

---

## Files Inventory

### Modified files (~25)

| File | Layer | Change scope |
|------|-------|-------------|
| `db.py` | 1 | Wire migrations 101-102, update migration logic |
| `views.py` | 2 | Rebuild 5 views |
| `queries.py` | 3 | Rewrite 6 functions to use FK |
| `timeline_engine.py` | 3 | Remove `is_program` column refs |
| `email_templates.py` | 3 | Rewrite program token population |
| `compliance.py` | 3 | Replace `is_program` checks |
| `charts.py` | 3 | Replace `tower_group` grouping |
| `dedup.py` | 3 | Remove `is_program` guard |
| `llm_schemas.py` | 3 | Query programs table |
| `analysis.py` | 3 | Replace `tower_group` grouping |
| `display.py` | 3 | Replace `tower_group` grouping |
| `models.py` | 3 | Remove `tower_group` field |
| `importer.py` | 3 | Keep `tower_group` as import alias only |
| `routes/programs.py` | 4 | Delete v1 routes, fix v2 query calls |
| `routes/policies.py` | 4 | Remove carrier CRUD, `is_program` creation |
| `routes/clients.py` | 4 | Remove legacy program queries |
| `routes/review.py` | 4 | Programs table lookup |
| `routes/reconcile.py` | 4,6 | Remove carrier INSERTs, program creation rewrite |
| `routes/meetings.py` | 4 | Replace `is_program` label |
| `reconciler.py` | 6 | Remove overlay scoring, simplify to 1:1 |
| `exporter.py` | 7 | Rewrite program export |
| `seed.py` | 7 | Remove `tower_group` usage |
| `cli.py` | 7 | Remove `tower_group` prompts |
| `onboard.py` | 7 | Remove `tower_group` UPDATE |

### Templates modified (~12)

| Template | Layer | Change |
|----------|-------|--------|
| `policies/new.html` | 5 | Remove program checkbox + tower_group input |
| `policies/_tab_details.html` | 5 | Remove program block + tower_group input |
| `reconcile/_create_form.html` | 5 | Remove `is_program` checkbox |
| `reconcile/_pairing_board.html` | 5 | Update program match display |
| `compliance/_policy_links.html` | 5 | `is_program` → `program_id` grouping |
| `compliance/_requirement_slideover.html` | 5 | `is_program` → `program_id` grouping |
| `programs/_tab_schematic.html` | 5 | URL pattern update |
| `programs/_underlying_matrix.html` | 5 | URL pattern update |
| `programs/_excess_matrix.html` | 5 | URL pattern update |
| `programs/_schematic_preview.html` | 5 | Label update |
| `charts/_chart_tower.html` | 5 | Label update |
| `clients/_programs.html` | 5 | Link pattern update |

### Files deleted (3)

| File | Reason |
|------|--------|
| `policies/_program_carriers_matrix.html` | CRUD for dropped table |
| `programs/schematic.html` | v1 standalone page |
| `tests/test_program_carriers.py` | Tests dropped table |

### New files (2)

| File | Purpose |
|------|---------|
| `migrations/101_phase4_program_cutover.sql` | Data migration |
| `migrations/102_drop_program_carriers.sql` | Drop table |
