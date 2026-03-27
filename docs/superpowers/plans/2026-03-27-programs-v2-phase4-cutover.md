# Programs v2 Phase 4 — Full Code Cleanup & Cutover

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all legacy program references (`is_program`, `program_carriers` table, `tower_group` grouping) from the codebase, completing the migration to standalone `programs` table + `program_id` FK.

**Architecture:** Layer-by-layer cutover: migration first (sets FK relationships), then views, core modules, routes, templates, reconciler, cleanup. Each layer builds on the previous. The `_score_pair()` reconciler function is untouched — scoring logic is preserved.

**Tech Stack:** Python/FastAPI, SQLite, Jinja2/HTMX, pytest

**Spec:** `docs/superpowers/specs/2026-03-27-programs-v2-phase4-cutover-design.md`

**Baseline:** 281 tests pass, 2 pre-existing failures (compliance pct + LLM schema — unrelated).

---

## File Map

### New files
| File | Purpose |
|------|---------|
| `src/policydb/migrations/101_phase4_program_cutover.sql` | Stub SQL (main logic in Python) |
| `src/policydb/migrations/102_drop_program_carriers.sql` | Drop program_carriers table |
| `tests/test_phase4_migration.py` | Migration verification tests |

### Major rewrites (5 files)
| File | What changes |
|------|-------------|
| `src/policydb/views.py` | Rebuild 6 views to remove is_program/program_carriers/tower_group |
| `src/policydb/queries.py:2453-2580` | Rewrite 6 functions: tower_group → program_id FK |
| `src/policydb/reconciler.py` | Remove overlay scoring (~200 lines), simplify to 1:1 child matching |
| `src/policydb/web/routes/programs.py:437-1454` | Delete ~1000 lines of v1 legacy routes, add redirect |
| `src/policydb/exporter.py` | Rewrite program export sections |

### Moderate edits (9 files)
| File | What changes |
|------|-------------|
| `src/policydb/db.py:363,1298-1395` | Wire migrations 101-102, remove program_carriers from carrier normalization |
| `src/policydb/web/routes/policies.py` | Remove carrier CRUD (~250 lines), is_program creation |
| `src/policydb/web/routes/clients.py` | Remove legacy program queries, program_carriers INSERT |
| `src/policydb/web/routes/reconcile.py` | Remove carrier INSERTs, rewrite program creation |
| `src/policydb/charts.py` | Replace tower_group grouping with program_id FK |
| `src/policydb/web/routes/charts.py` | Replace tower_group in layout expansion |
| `src/policydb/compliance.py` | Replace is_program checks with program_id |
| `src/policydb/email_templates.py` | Rewrite program token population |
| `src/policydb/web/routes/review.py` | Programs table lookup for review cascade |

### Minor edits (~12 files)
| File | What changes |
|------|-------------|
| `src/policydb/timeline_engine.py` | Remove is_program from SELECT |
| `src/policydb/dedup.py` | Remove is_program guard |
| `src/policydb/llm_schemas.py` | Query programs table |
| `src/policydb/analysis.py` | Replace tower_group grouping |
| `src/policydb/display.py` | Replace tower_group grouping |
| `src/policydb/models.py` | Remove tower_group field |
| `src/policydb/importer.py` | Keep tower_group as import alias |
| `src/policydb/web/routes/meetings.py` | Replace is_program label |
| `src/policydb/seed.py` | Remove tower_group |
| `src/policydb/cli.py` | Remove tower_group prompts |
| `src/policydb/onboard.py` | Remove tower_group UPDATE |

### Templates deleted (2)
| Template | Reason |
|----------|--------|
| `src/policydb/web/templates/policies/_program_carriers_matrix.html` | CRUD for dropped table |
| `src/policydb/web/templates/programs/schematic.html` | v1 standalone page |

### Templates modified (~12)
| Template | Change |
|----------|--------|
| `policies/new.html` | Remove program checkbox + tower_group input |
| `policies/_tab_details.html` | Remove program block + tower_group input + carrier matrix include |
| `reconcile/_create_form.html` | Remove is_program checkbox |
| `reconcile/_pairing_board.html` | Update program match display |
| `compliance/_policy_links.html` | is_program → program_id grouping |
| `compliance/_requirement_slideover.html` | is_program → program_id grouping |
| `programs/_tab_schematic.html` | URL pattern: tower_group → program_uid |
| `programs/_underlying_matrix.html` | URL pattern update |
| `programs/_excess_matrix.html` | URL pattern update |
| `programs/_schematic_preview.html` | tower.tower_group → tower.program_name |
| `charts/_chart_tower.html` | tower.tower_group → tower.program_name |
| `clients/_programs.html` | Remove legacy section, update links |

### Tests modified/deleted (3)
| File | Change |
|------|--------|
| `tests/test_program_carriers.py` | DELETE entirely |
| `tests/test_programs_v2.py` | Update to test FK-based queries |
| `tests/test_reconcile_algorithm.py` | Remove carrier matching tests |

---

## Task 1: Data Migration — Link Children + Convert Carriers

**Files:**
- Create: `src/policydb/migrations/101_phase4_program_cutover.sql`
- Modify: `src/policydb/db.py:363,1339+`
- Create: `tests/test_phase4_migration.py`

- [ ] **Step 1: Write migration test (pre-conditions)**

Create `tests/test_phase4_migration.py`:

```python
"""Tests for Phase 4 program cutover migration."""
import sqlite3
import pytest
from policydb.db import init_db, next_policy_uid


@pytest.fixture
def migrated_db(tmp_path):
    """Create a fresh DB with all migrations applied (including 101)."""
    db_path = tmp_path / "test.sqlite"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_migration_101_applied(migrated_db):
    """Migration 101 should be in schema_version."""
    row = migrated_db.execute(
        "SELECT version FROM schema_version WHERE version = 101"
    ).fetchone()
    assert row is not None


def test_child_policies_have_program_id(migrated_db):
    """After migration, children with tower_group matching a program name
    should have program_id set."""
    # Insert test data: a program + a child policy with matching tower_group
    migrated_db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-TEST', 999, 'TestProg')"
    )
    migrated_db.execute(
        "INSERT INTO policies (policy_uid, client_id, tower_group, is_program, archived) "
        "VALUES ('POL-CHILD', 999, 'TestProg', 0, 0)"
    )
    migrated_db.commit()
    # The migration runs at init_db time, so for NEW data we'd need to re-run
    # Just verify the migration SQL logic works by running it manually
    pgm_id = migrated_db.execute(
        "SELECT id FROM programs WHERE program_uid = 'PGM-TEST'"
    ).fetchone()["id"]
    migrated_db.execute(
        """UPDATE policies SET program_id = (
            SELECT pg.id FROM programs pg
            WHERE pg.client_id = policies.client_id AND pg.name = policies.tower_group
            AND pg.archived = 0 LIMIT 1
        ) WHERE tower_group IS NOT NULL AND tower_group != ''
        AND (is_program = 0 OR is_program IS NULL)
        AND program_id IS NULL AND archived = 0 AND policy_uid = 'POL-CHILD'"""
    )
    migrated_db.commit()
    child = migrated_db.execute(
        "SELECT program_id FROM policies WHERE policy_uid = 'POL-CHILD'"
    ).fetchone()
    assert child["program_id"] == pgm_id


def test_is_program_rows_archived(migrated_db):
    """After migration, is_program=1 rows should be archived."""
    # Verify no unarchived is_program=1 rows exist after migration
    count = migrated_db.execute(
        "SELECT COUNT(*) FROM policies WHERE is_program = 1 AND archived = 0"
    ).fetchone()[0]
    assert count == 0


def test_program_tower_lines_has_program_id_column(migrated_db):
    """Migration adds program_id column to program_tower_lines."""
    cols = [r["name"] for r in migrated_db.execute(
        "PRAGMA table_info(program_tower_lines)"
    ).fetchall()]
    assert "program_id" in cols
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_phase4_migration.py -v`
Expected: FAIL (migration 101 not yet wired)

- [ ] **Step 3: Create migration SQL stub**

Create `src/policydb/migrations/101_phase4_program_cutover.sql`:

```sql
-- Phase 4: Program cutover
-- Main logic is in Python (db.py) because it needs next_policy_uid() for carrier conversion.
-- This SQL handles the simple structural changes.

-- Step C: Add program_id column to program_tower_lines for FK repoint
ALTER TABLE program_tower_lines ADD COLUMN program_id INTEGER REFERENCES programs(id) ON DELETE CASCADE;
```

- [ ] **Step 4: Wire migration 101 into db.py**

In `src/policydb/db.py`:

1. Add `101, 102` to `_KNOWN_MIGRATIONS` set (line 363)
2. After the migration 100 block (after line 1339), add:

```python
    if 101 not in applied:
        # Step 0: Structural SQL
        sql = (_MIGRATIONS_DIR / "101_phase4_program_cutover.sql").read_text()
        conn.executescript(sql)

        # Step A: Link child policies to programs via FK
        conn.execute("""
            UPDATE policies
            SET program_id = (
                SELECT pg.id FROM programs pg
                WHERE pg.client_id = policies.client_id
                  AND pg.name = policies.tower_group
                  AND pg.archived = 0
                LIMIT 1
            )
            WHERE tower_group IS NOT NULL AND tower_group != ''
              AND (is_program = 0 OR is_program IS NULL)
              AND program_id IS NULL
              AND archived = 0
        """)

        # Step B: Convert program_carriers rows to child policies
        carrier_rows = conn.execute("""
            SELECT pc.id, pc.carrier, pc.policy_number, pc.premium, pc.limit_amount,
                   pc.sort_order, pc.program_id AS old_program_policy_id,
                   p.client_id, p.policy_type, p.effective_date, p.expiration_date,
                   p.layer_position, p.tower_group, p.renewal_status,
                   p.account_exec, p.project_id
            FROM program_carriers pc
            JOIN policies p ON p.id = pc.program_id
            WHERE p.is_program = 1
        """).fetchall()

        for cr in carrier_rows:
            # Find the programs table entry
            pgm = conn.execute(
                "SELECT id FROM programs WHERE client_id = ? AND name = ? AND archived = 0 LIMIT 1",
                (cr["client_id"], (cr["tower_group"] or cr["policy_type"] or "").strip()),
            ).fetchone()
            if not pgm:
                continue  # Skip orphaned carrier rows

            uid = next_policy_uid(conn)
            conn.execute("""
                INSERT INTO policies (
                    policy_uid, client_id, policy_type, carrier, policy_number,
                    premium, limit_amount, layer_position, effective_date,
                    expiration_date, renewal_status, account_exec, project_id,
                    program_id, tower_group, is_program, archived, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, CURRENT_TIMESTAMP)
            """, (
                uid, cr["client_id"], cr["policy_type"], cr["carrier"],
                cr["policy_number"], cr["premium"], cr["limit_amount"],
                cr["layer_position"], cr["effective_date"], cr["expiration_date"],
                cr["renewal_status"] or "Not Started", cr["account_exec"] or "",
                cr["project_id"], pgm["id"], cr["tower_group"],
            ))

        # Step C: Repoint program_tower_lines FK
        conn.execute("""
            UPDATE program_tower_lines
            SET program_id = (
                SELECT pg.id FROM programs pg
                JOIN policies p ON p.client_id = pg.client_id
                  AND (p.tower_group = pg.name OR p.policy_type = pg.name)
                WHERE p.id = program_tower_lines.program_policy_id
                  AND pg.archived = 0
                LIMIT 1
            )
        """)

        # Step D: Archive is_program=1 policy rows
        conn.execute("UPDATE policies SET archived = 1 WHERE is_program = 1")

        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (101, "Phase 4: link children, convert carriers, archive is_program rows"),
        )
        conn.commit()
        logger.info("Migration 101: Phase 4 program cutover complete")
```

3. Temporarily wrap `program_carriers` carrier normalization (line 1386-1390 in db.py) in try/except so it doesn't crash after migration 102 drops the table. **This is temporary** — Task 9 will delete this block entirely when migration 102 is wired.

```python
    try:
        for r in conn.execute("SELECT id, carrier FROM program_carriers WHERE carrier IS NOT NULL AND carrier != ''").fetchall():
            n = normalize_carrier(r["carrier"])
            if n != r["carrier"]:
                conn.execute("UPDATE program_carriers SET carrier = ? WHERE id = ?", (n, r["id"]))
                _carrier_changed += 1
    except Exception:
        pass  # Table may have been dropped (migration 102); block removed in Task 9
```

- [ ] **Step 5: Run migration tests**

Run: `pytest tests/test_phase4_migration.py -v`
Expected: PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: All pass (migration is additive, existing code still works)

- [ ] **Step 7: Commit**

```bash
git add src/policydb/migrations/101_phase4_program_cutover.sql src/policydb/db.py tests/test_phase4_migration.py
git commit -m "feat(migration): Phase 4 program cutover — link children, convert carriers, archive is_program"
```

---

## Task 2: Views — Rebuild All Program-Related Views

**Files:**
- Modify: `src/policydb/views.py` (all 6 views)
- Modify: `tests/test_programs_v2.py` (add view tests)

- [ ] **Step 1: Write view tests**

Add to `tests/test_programs_v2.py`:

```python
def test_v_policy_status_no_program_carriers_subquery(db_conn):
    """v_policy_status should not reference program_carriers table."""
    from policydb.views import V_POLICY_STATUS
    assert "program_carriers" not in V_POLICY_STATUS


def test_v_policy_status_has_program_id(db_conn):
    """v_policy_status should include program_id and program_name via JOIN."""
    from policydb.views import V_POLICY_STATUS
    assert "program_id" in V_POLICY_STATUS
    assert "programs" in V_POLICY_STATUS  # JOIN to programs table


def test_v_client_summary_programs_from_table(db_conn):
    """v_client_summary should count programs from programs table, not is_program."""
    from policydb.views import V_CLIENT_SUMMARY
    assert "is_program" not in V_CLIENT_SUMMARY


def test_v_schedule_no_is_program(db_conn):
    """v_schedule should not reference is_program or program_carriers."""
    from policydb.views import V_SCHEDULE
    assert "is_program" not in V_SCHEDULE
    assert "program_carriers" not in V_SCHEDULE


def test_v_renewal_pipeline_excludes_children(db_conn):
    """v_renewal_pipeline should exclude child policies via program_id IS NULL."""
    from policydb.views import V_RENEWAL_PIPELINE
    assert "program_id IS NULL" in V_RENEWAL_PIPELINE
    assert "is_program" not in V_RENEWAL_PIPELINE


def test_v_tower_uses_program_id(db_conn):
    """v_tower should group by program_id, not tower_group."""
    from policydb.views import V_TOWER
    assert "program_id" in V_TOWER or "programs" in V_TOWER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_programs_v2.py -v -k "v_policy_status or v_client_summary or v_schedule or v_renewal or v_tower"`
Expected: FAIL

- [ ] **Step 3: Update V_POLICY_STATUS**

In `src/policydb/views.py`, update the V_POLICY_STATUS view:
- Remove line 96: `p.is_program,`
- Remove lines 97-98: `program_carriers` and `program_carrier_count` subqueries
- Replace with: `p.program_id, pg.program_uid, pg.name AS program_name,`
- Add LEFT JOIN: `LEFT JOIN programs pg ON pg.id = p.program_id`
- Keep `p.tower_group` (still a column, just not used for logic)

- [ ] **Step 4: Update V_CLIENT_SUMMARY**

Replace line 147: `COUNT(CASE WHEN p.is_program = 1 THEN 1 END) AS program_count`
With: `(SELECT COUNT(*) FROM programs pg2 WHERE pg2.client_id = c.id AND pg2.archived = 0) AS program_count`

- [ ] **Step 5: Update V_SCHEDULE**

Remove lines 162-165 (the is_program CASE statements for policy_type and carrier).
Replace with simple: `p.policy_type AS display_type, p.carrier,`

- [ ] **Step 6: Update V_TOWER**

Replace `p.tower_group` in SELECT/ORDER BY with `p.program_id` and JOIN to `programs.name`:
- Add: `pg.name AS program_name,`
- Add: `LEFT JOIN programs pg ON pg.id = p.program_id`
- ORDER BY: `c.name, pg.name, COALESCE(p.attachment_point, 0) ASC`

- [ ] **Step 7: Update V_RENEWAL_PIPELINE**

Replace line 298: `AND (p.is_program = 0 OR p.is_program IS NULL)`
With: `AND p.program_id IS NULL`

- [ ] **Step 8: Verify V_REVIEW_QUEUE**

Check if line 396 already uses `AND (p.program_id IS NULL)` — if so, no change needed. If it still uses `is_program`, update it.

- [ ] **Step 9: Run tests**

Run: `pytest tests/test_programs_v2.py -v && pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/policydb/views.py tests/test_programs_v2.py
git commit -m "feat(views): rebuild views for programs table — remove is_program/program_carriers refs"
```

---

## Task 3: Core Modules — queries.py FK Rewrite

**Files:**
- Modify: `src/policydb/queries.py:2453-2580`
- Modify: `tests/test_programs_v2.py`

- [ ] **Step 1: Write tests for new function signatures**

Add to `tests/test_programs_v2.py`:

```python
def test_get_program_child_policies_by_id(db_conn):
    """get_program_child_policies should accept program_id (int), not name."""
    from policydb.queries import get_program_child_policies
    import inspect
    sig = inspect.signature(get_program_child_policies)
    params = list(sig.parameters.keys())
    assert "program_id" in params
    assert "program_name" not in params


def test_get_program_aggregates_by_id(db_conn):
    """get_program_aggregates should accept program_id (int)."""
    from policydb.queries import get_program_aggregates
    import inspect
    sig = inspect.signature(get_program_aggregates)
    params = list(sig.parameters.keys())
    assert "program_id" in params


def test_get_unassigned_no_is_program(db_conn):
    """get_unassigned_policies should not check is_program."""
    from policydb.queries import get_unassigned_policies
    import inspect
    source = inspect.getsource(get_unassigned_policies)
    assert "is_program" not in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_programs_v2.py -v -k "child_policies_by_id or aggregates_by_id or unassigned_no_is"`
Expected: FAIL

- [ ] **Step 3: Rewrite all 6 functions**

In `src/policydb/queries.py` lines 2453-2580, rewrite:

```python
def get_program_child_policies(conn, program_id: int) -> list[dict]:
    """Return child policies for a program via program_id FK."""
    rows = conn.execute(
        """SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.policy_number,
                  p.premium, p.limit_amount, p.deductible, p.layer_position,
                  p.renewal_status, p.effective_date, p.expiration_date,
                  p.attachment_point, p.participation_of, p.coverage_form
           FROM policies p
           WHERE p.program_id = ?
             AND p.archived = 0
             AND (p.is_opportunity = 0 OR p.is_opportunity IS NULL)
           ORDER BY p.layer_position, p.policy_type""",
        (program_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_program_aggregates(conn, program_id: int) -> dict:
    """Compute aggregate stats for a program from its child policies."""
    row = conn.execute(
        """SELECT COUNT(*) AS policy_count,
                  COUNT(DISTINCT carrier) AS carrier_count,
                  COALESCE(SUM(premium), 0) AS total_premium,
                  COALESCE(MAX(limit_amount), 0) AS max_limit
           FROM policies
           WHERE program_id = ? AND archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)""",
        (program_id,),
    ).fetchone()
    return dict(row) if row else {
        "policy_count": 0, "carrier_count": 0, "total_premium": 0, "max_limit": 0
    }


def get_programs_for_client(conn, client_id: int) -> list[dict]:
    """Return all programs for a client with aggregated stats."""
    rows = conn.execute(
        "SELECT * FROM programs WHERE client_id = ? AND archived = 0 ORDER BY name",
        (client_id,),
    ).fetchall()
    programs = []
    for r in rows:
        pgm = dict(r)
        agg = get_program_aggregates(conn, pgm["id"])
        pgm.update(agg)
        programs.append(pgm)
    return programs


def get_unassigned_policies(conn, client_id: int) -> list[dict]:
    """Return active policies not assigned to any program."""
    rows = conn.execute(
        """SELECT policy_uid, policy_type, carrier, premium, limit_amount
           FROM policies
           WHERE client_id = ? AND archived = 0
             AND (is_opportunity = 0 OR is_opportunity IS NULL)
             AND program_id IS NULL
           ORDER BY policy_type""",
        (client_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_program_timeline_milestones(conn, program_id: int) -> list[dict]:
    """Return timeline milestones for all child policies of a program."""
    try:
        rows = conn.execute(
            """SELECT pt.policy_uid, pt.milestone_name, pt.ideal_date,
                      pt.projected_date, pt.completed_date, pt.health,
                      pt.accountability, pt.waiting_on,
                      p.policy_type, p.carrier
               FROM policy_timeline pt
               JOIN policies p ON p.policy_uid = pt.policy_uid
               WHERE p.program_id = ?
               ORDER BY pt.ideal_date""",
            (program_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_program_activities(conn, program_id: int, limit: int = 50) -> list[dict]:
    """Return recent activities from all child policies of a program."""
    rows = conn.execute(
        """SELECT a.id, a.activity_type, a.description, a.contact_name,
                  a.created_at, a.follow_up_date, a.disposition,
                  p.policy_type, p.carrier, p.policy_uid
           FROM activity_log a
           JOIN policies p ON p.id = a.policy_id
           WHERE p.program_id = ?
           ORDER BY a.created_at DESC
           LIMIT ?""",
        (program_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Update callers in programs.py v2 routes**

**Critical:** The function signatures changed, so all callers must be updated in the same task to avoid intermediate breakage. In `src/policydb/web/routes/programs.py`, update all v2 route calls:

- `get_program_child_policies(conn, program["name"], program["client_id"])` → `get_program_child_policies(conn, program["id"])`
- `get_program_aggregates(conn, program["name"], program["client_id"])` → `get_program_aggregates(conn, program["id"])`
- `get_program_timeline_milestones(conn, program["name"], program["client_id"])` → `get_program_timeline_milestones(conn, program["id"])`
- `get_program_activities(conn, program["name"], program["client_id"])` → `get_program_activities(conn, program["id"])`

Also update the schematic tab direct SQL queries (~lines 187-198) to use `WHERE p.program_id = ?` instead of `WHERE p.tower_group = ? AND p.client_id = ?`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_programs_v2.py -v && pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/queries.py src/policydb/web/routes/programs.py tests/test_programs_v2.py
git commit -m "feat(queries): rewrite program queries to use program_id FK, update callers"
```

---

## Task 4: Core Modules — Minor Files Sweep

**Files:**
- Modify: `src/policydb/timeline_engine.py`
- Modify: `src/policydb/email_templates.py`
- Modify: `src/policydb/compliance.py`
- Modify: `src/policydb/dedup.py`
- Modify: `src/policydb/llm_schemas.py`
- Modify: `src/policydb/analysis.py`
- Modify: `src/policydb/display.py`
- Modify: `src/policydb/models.py`

- [ ] **Step 1: Write test for email_templates program tokens**

Add to `tests/test_programs_v2.py`:

```python
def test_email_template_program_tokens_from_programs_table(db_conn):
    """Program tokens should be populated from programs table, not program_carriers."""
    from policydb.email_templates import policy_context
    import inspect
    source = inspect.getsource(policy_context)
    assert "program_carriers" not in source, "Should not query program_carriers table"
    assert "programs" in source, "Should query programs table for program tokens"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_programs_v2.py -v -k "email_template_program_tokens"`
Expected: FAIL (still references program_carriers)

- [ ] **Step 3: Update timeline_engine.py**

Remove `is_program` from SELECT columns (around lines 91 and 120). The `program_id IS NOT NULL → skip` logic stays.

- [ ] **Step 2: Update email_templates.py**

Remove the `if row["is_program"]: query program_carriers` branch (around line 342). Replace with:

```python
# Program tokens — derive from programs table if policy has program_id
if row.get("program_id"):
    pgm_row = conn.execute(
        "SELECT program_uid, name FROM programs WHERE id = ?",
        (row["program_id"],),
    ).fetchone()
    if pgm_row:
        ctx["program_name"] = pgm_row["name"]
        ctx["program_uid"] = pgm_row["program_uid"]
        carrier_rows = conn.execute(
            "SELECT DISTINCT carrier FROM policies WHERE program_id = ? AND carrier IS NOT NULL AND carrier != '' AND archived = 0",
            (row["program_id"],),
        ).fetchall()
        ctx["program_carriers"] = ", ".join(r["carrier"] for r in carrier_rows)
        ctx["program_carrier_count"] = str(len(carrier_rows))
```

- [ ] **Step 3: Update compliance.py**

Replace all `p.get("is_program")` checks with `p.get("program_id")` checks (or look up from programs table). Replace `program_carriers` table queries with child policy queries. Replace sort `ORDER BY p.is_program DESC` with `ORDER BY (CASE WHEN p.program_id IS NOT NULL THEN 0 ELSE 1 END), p.policy_type`.

- [ ] **Step 4: Update dedup.py**

Remove the guard at line 127: `if a.get("is_program") and b.get("is_program"): return None`. Programs are in a separate table now and won't appear in dedup candidates.

- [ ] **Step 5: Update llm_schemas.py**

Replace `WHERE client_id = ? AND is_program = 1` with a query against `programs` table.

- [ ] **Step 6: Update analysis.py and display.py**

Replace `tower_group` grouping with `program_id` FK. In `group_by_tower()`: use `r.get("program_id")` or `r.get("program_name")` instead of `r["tower_group"]`. In `get_standalones()`: filter by `not p.get("program_id")`.

- [ ] **Step 7: Update models.py**

Remove `tower_group` from the Policy pydantic model, or mark as `Optional[str] = None` with a deprecation comment.

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/policydb/timeline_engine.py src/policydb/email_templates.py src/policydb/compliance.py src/policydb/dedup.py src/policydb/llm_schemas.py src/policydb/analysis.py src/policydb/display.py src/policydb/models.py
git commit -m "feat(core): remove is_program/tower_group from 8 core modules"
```

---

## Task 5: Routes — programs.py (Delete v1, Fix v2)

**Files:**
- Modify: `src/policydb/web/routes/programs.py`

**Note on `program_tower_lines` and `program_tower_coverage`:**
- `program_tower_lines` has both `program_policy_id` (old, points to policies.id) and `program_id` (new, points to programs.id, added in migration 101). All SELECT queries should use `WHERE program_id = ?`. All INSERT statements must set BOTH columns for backward compat: `program_policy_id` (for any code not yet migrated) and `program_id`.
- `program_tower_coverage` references `excess_policy_id` and `underlying_policy_id` which point to `policies(id)` — these are still valid since child policies remain in the policies table. No FK changes needed. Scoping queries that filter by program should use `JOIN policies ON policies.id = ptc.excess_policy_id WHERE policies.program_id = ?`.

- [ ] **Step 1: Delete v1 legacy routes**

Delete everything from line 437 (`# Legacy v1`) to end of file (~line 1454). This removes ~1000 lines of tower_group-based routes.

- [ ] **Step 2: Add legacy URL redirect**

After the v2 routes (after line 434), add a catch-all redirect:

```python
@router.get("/clients/{client_id}/programs/{tower_group}")
async def redirect_legacy_program(request: Request, client_id: int, tower_group: str):
    """Redirect old tower_group URLs to new program detail page."""
    conn = get_db()
    program = conn.execute(
        "SELECT program_uid FROM programs WHERE client_id = ? AND name = ? AND archived = 0 LIMIT 1",
        (client_id, tower_group),
    ).fetchone()
    if program:
        return RedirectResponse(f"/programs/{program['program_uid']}", status_code=302)
    raise HTTPException(status_code=404, detail="Program not found")
```

- [ ] **Step 3: Fix v2 route query calls**

In the v2 routes (lines 64-434), update all calls to queries.py functions:
- `get_program_child_policies(conn, program["name"], program["client_id"])` → `get_program_child_policies(conn, program["id"])`
- `get_program_aggregates(conn, program["name"], program["client_id"])` → `get_program_aggregates(conn, program["id"])`
- `get_program_timeline_milestones(conn, program["name"], program["client_id"])` → `get_program_timeline_milestones(conn, program["id"])`
- `get_program_activities(conn, program["name"], program["client_id"])` → `get_program_activities(conn, program["id"])`

Also in the schematic tab (lines 166-298):
- Child policy query (~line 187): `WHERE p.tower_group = ? AND p.client_id = ?` → `WHERE p.program_id = ?`
- `program_tower_lines` SELECT (~line 260): `WHERE program_policy_id IN (SELECT id FROM policies WHERE tower_group = ? AND is_program = 1)` → `WHERE program_id = ?`
- `program_tower_lines` INSERT (~lines 607, 615): add `program_id = ?` column alongside existing `program_policy_id`
- `program_tower_coverage` queries (~lines 252, 875): scope via JOIN: `JOIN policies p ON p.id = ptc.excess_policy_id WHERE p.program_id = ?`
- `program_tower_coverage` DELETE (~lines 428, 644-646): no change (operates on specific policy IDs)
- `program_tower_lines` DELETE (~lines 430, 646): no change (operates on specific policy IDs)

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/programs.py
git commit -m "feat(routes): delete v1 program routes (~1000 lines), fix v2 to use program_id FK"
```

---

## Task 6: Routes — policies.py, clients.py, review.py, Others

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/routes/clients.py`
- Modify: `src/policydb/web/routes/review.py`
- Modify: `src/policydb/web/routes/meetings.py`
- Modify: `src/policydb/web/routes/charts.py`

- [ ] **Step 1: Update policies.py**

1. Remove `program_carriers` CRUD endpoints (~lines 2940-3190): carrier matrix add row, delete row, reorder, merge, dissolve. These managed the now-dropped table.
2. Remove `is_program` from policy creation handler (~line 3314+). Remove `is_program` from INSERT.
3. Remove `if merged.get("is_program"): query children` conditionals in policy detail (~lines 1387-1400).

- [ ] **Step 2: Update clients.py**

1. Remove legacy program query `FROM policies WHERE is_program = 1` (~line 720).
2. Remove corporate programs section querying `is_program=1` + `program_carriers` (~lines 1104-1108).
3. Remove `program_carriers` INSERT during import/merge (~line 5201).
4. Fix renewal month summary: replace correlated subquery against `program_carriers` (~lines 1329-1334) with `(SELECT COUNT(DISTINCT carrier) FROM policies WHERE program_id = ...)` or remove program carrier count from calendar.
5. Programs section: use `get_programs_for_client()` exclusively.

- [ ] **Step 3: Update review.py**

Replace `if prog_row and prog_row["is_program"]` (~line 165) with:

```python
program = conn.execute(
    "SELECT id FROM programs WHERE program_uid = ?", (uid,)
).fetchone()
if program:
    conn.execute(
        "UPDATE policies SET last_reviewed_at = CURRENT_TIMESTAMP WHERE program_id = ?",
        (program["id"],),
    )
```

- [ ] **Step 4: Update meetings.py**

Replace `CASE WHEN is_program = 1 THEN 'Program'` (~line 380) with a LEFT JOIN to `programs` via `program_id`:
`CASE WHEN p.program_id IS NOT NULL THEN 'In Program' ELSE '' END AS program_label`

- [ ] **Step 5: Update routes/charts.py**

Replace `tower_group` references in tower layout expansion (~lines 412-439) with `program_name` or `program_id`.

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/policies.py src/policydb/web/routes/clients.py src/policydb/web/routes/review.py src/policydb/web/routes/meetings.py src/policydb/web/routes/charts.py
git commit -m "feat(routes): remove is_program/program_carriers from 5 route files"
```

---

## Task 7: Templates — Remove Legacy References

**Files:**
- Delete: `src/policydb/web/templates/policies/_program_carriers_matrix.html`
- Delete: `src/policydb/web/templates/programs/schematic.html`
- Modify: 12 template files (see file map above)

- [ ] **Step 1: Delete dead templates**

```bash
rm src/policydb/web/templates/policies/_program_carriers_matrix.html
rm src/policydb/web/templates/programs/schematic.html
```

- [ ] **Step 2: Update policies/new.html**

1. Remove "This is a Program" checkbox toggle (~lines 73-78)
2. Remove `toggleProgramMode()` JS function
3. Remove `tower_group` input field (~line 247), datalist (~line 427), and AC_FIELDS entry (~line 432)

- [ ] **Step 3: Update policies/_tab_details.html**

1. Remove `{% if policy.is_program %}` conditional block (~line 46)
2. Remove `_program_carriers_matrix.html` include (~line 67)
3. Remove `tower_group` input field (~line 415), datalist (~line 670), and AC_FIELDS entry (~line 724)

- [ ] **Step 4: Update reconcile templates**

1. `reconcile/_create_form.html`: Remove `is_program` checkbox (~line 34)
2. `reconcile/_pairing_board.html`: Replace `r.is_program_match` references with program grouping display

- [ ] **Step 5: Update compliance templates**

1. `compliance/_policy_links.html`: Replace `pol.get('is_program')` with `pol.get('program_id')` grouping
2. `compliance/_requirement_slideover.html`: Same replacement

- [ ] **Step 6: Update programs schematic templates**

1. `programs/_tab_schematic.html`: Replace `{{ tower_group | urlencode }}` URLs with `{{ program.program_uid }}`
2. `programs/_underlying_matrix.html`: Same URL pattern update
3. `programs/_excess_matrix.html`: Same URL pattern update
4. `programs/_schematic_preview.html`: Replace `tower.tower_group` with `tower.program_name`

- [ ] **Step 7: Update chart template**

`charts/_chart_tower.html`: Replace `tower.tower_group` with `tower.program_name`

- [ ] **Step 8: Update clients/_programs.html**

Remove entire legacy programs section (the `{# Legacy Programs #}` block). Ensure all links use `/programs/{{ pgm.program_uid }}`.

- [ ] **Step 9: Run server and QA**

Start server: `lsof -ti:8000 | xargs kill -9 2>/dev/null; pdb serve &`
Navigate to: client detail, policy detail, program detail, reconcile page.
Verify: no Jinja2 errors, no broken links, no `is_program` or `tower_group` in rendered HTML.

- [ ] **Step 10: Commit**

```bash
git add -A src/policydb/web/templates/
git commit -m "feat(templates): remove is_program/tower_group from 12 templates, delete 2 dead files"
```

---

## Task 8: Reconciler — Simplify to 1:1 Child Matching

**Files:**
- Modify: `src/policydb/reconciler.py`
- Modify: `src/policydb/web/routes/reconcile.py`
- Modify: `tests/test_reconcile_algorithm.py`

- [ ] **Step 1: Write test for simplified reconciler**

Add to `tests/test_reconcile_algorithm.py`:

```python
def test_reconcile_no_program_overlay():
    """Reconciler should not use _program_carrier_rows overlay."""
    from policydb.reconciler import ReconcileRow
    import inspect
    fields = [f.name for f in ReconcileRow.__dataclass_fields__.values()]
    assert "is_program_match" not in fields
    assert "matched_carrier_id" not in fields


def test_reconcile_child_policy_matches_directly():
    """Child policies with program_id should match 1:1 like any policy."""
    from policydb.reconciler import reconcile
    ext_rows = [{"carrier": "AIG", "premium": 100000, "policy_number": "ABC-123",
                 "policy_type": "GL", "effective_date": "2026-04-01"}]
    db_rows = [{"id": 1, "carrier": "AIG", "premium": 100000, "policy_number": "ABC-123",
                "policy_type": "General Liability", "effective_date": "2026-04-01",
                "expiration_date": "2027-04-01", "program_id": 5, "policy_uid": "POL-001"}]
    results = reconcile(ext_rows, db_rows, single_client=True)
    paired = [r for r in results if r.status == "PAIRED"]
    assert len(paired) == 1
    assert paired[0].db["program_id"] == 5  # Matched to child policy directly
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reconcile_algorithm.py -v -k "no_program_overlay or child_policy_matches"`
Expected: FAIL (is_program_match still exists)

- [ ] **Step 3: Update ReconcileRow dataclass**

Remove lines 73-75 from `reconciler.py`:
```python
    # DELETE these fields:
    # is_program_match: bool = False
    # matched_carrier_id: int | None = None
```

- [ ] **Step 4: Delete _resolve_program_carrier()**

Delete the entire function (lines 548-572).

- [ ] **Step 5: Remove _program_indices and sticky logic**

Delete _program_indices set creation (lines 621-624). Update `_claim_db()` to always remove from unmatched (remove the `if db_idx not in _program_indices` guard at line 628).

- [ ] **Step 6: Remove program overlay from Pass 0, 1, 2**

In each pass, remove:
- The `is_program = db_idx in _program_indices` check
- The `matched_cid, score_target = _resolve_program_carrier(...)` call
- The `if matched_cid: score_db = {**db, ...}` overlay logic
- The `is_program_match=...` and `matched_carrier_id=...` params in `_build_reconcile_row()`

The scoring just uses `_score_pair(ext, db, ...)` directly against each db row.

- [ ] **Step 7: Remove _build_reconcile_row program params**

Remove `is_program_match` and `matched_carrier_id` parameters from `_build_reconcile_row()` (lines 518-545).

- [ ] **Step 8: Remove _program_matched_indices from EXTRA logic**

Delete lines 787-789 (the `_program_matched_indices` set). Programs are no longer special in EXTRA detection.

- [ ] **Step 9: Rewrite program_reconcile_summary()**

Replace the old function (lines 819-875) with:

```python
def program_reconcile_summary(results: list[ReconcileRow]) -> dict[int, dict]:
    """Group reconcile results by program for summary display."""
    by_program: dict[int, dict] = {}
    for r in results:
        if r.db and r.db.get("program_id"):
            pid = r.db["program_id"]
            if pid not in by_program:
                by_program[pid] = {
                    "matched": 0, "total_premium": 0.0, "children": [],
                }
            if r.status == "PAIRED":
                by_program[pid]["matched"] += 1
                by_program[pid]["total_premium"] += float(
                    r.ext.get("premium") or 0
                ) if r.ext else 0
            by_program[pid]["children"].append(r)
    return by_program
```

- [ ] **Step 10: Update reconcile.py route**

1. Remove `_program_carrier_rows` attachment in `_load_db_policies()` (~lines 701-713 in reconcile.py route)
2. Remove `program_carriers` INSERT on pair confirmation (~lines 1685-1697)
3. Replace "create is_program=1 policy + program_carriers rows" with "create program in programs table + create child policies" (~lines 2172-2229)
4. Replace "INSERT INTO program_carriers" for unmatched rows with "create child policy with program_id" (~lines 2389-2436)

- [ ] **Step 11: Remove old program carrier tests**

In `tests/test_reconcile_algorithm.py`, remove any tests that reference `is_program_match`, `matched_carrier_id`, or `_program_carrier_rows`.

- [ ] **Step 12: Run full test suite**

Run: `pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add src/policydb/reconciler.py src/policydb/web/routes/reconcile.py tests/test_reconcile_algorithm.py
git commit -m "feat(reconciler): simplify to 1:1 child policy matching, remove overlay scoring"
```

---

## Task 9: Cleanup — Drop Table, Dead Code, Exporter

**Files:**
- Create: `src/policydb/migrations/102_drop_program_carriers.sql`
- Modify: `src/policydb/db.py` (wire migration 102)
- Delete: `tests/test_program_carriers.py`
- Modify: `src/policydb/exporter.py`
- Modify: `src/policydb/charts.py`
- Modify: `src/policydb/seed.py`
- Modify: `src/policydb/cli.py`
- Modify: `src/policydb/onboard.py`
- Modify: `src/policydb/importer.py`

- [ ] **Step 1: Create migration 102**

Create `src/policydb/migrations/102_drop_program_carriers.sql`:

```sql
DROP TABLE IF EXISTS program_carriers;
```

- [ ] **Step 2: Wire migration 102 in db.py**

After the migration 101 block, add:

```python
    if 102 not in applied:
        sql = (_MIGRATIONS_DIR / "102_drop_program_carriers.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (102, "Drop program_carriers table"),
        )
        conn.commit()
        logger.info("Migration 102: dropped program_carriers table")
```

Also remove the `program_carriers` carrier normalization block from db.py (lines 1386-1390) entirely (no more try/except, just delete).

- [ ] **Step 3: Delete test_program_carriers.py**

```bash
rm tests/test_program_carriers.py
```

- [ ] **Step 4: Update exporter.py**

1. Remove `is_program, program_carriers, program_carrier_count` from SELECT (~line 1027)
2. Remove `if policy.get("is_program"): return 100` from `_compute_completeness()` (~line 2552)
3. Replace program export section (~lines 2608-2891): query `programs` table, join to child policies for carrier list and aggregates

- [ ] **Step 5: Update charts.py**

Key areas (approximately 20 references across the file):

1. **Program carriers lookup** (~lines 178-200): Replace `SELECT ... FROM program_carriers WHERE program_id = ?` with `SELECT DISTINCT carrier, premium, limit_amount FROM policies WHERE program_id = ? AND archived = 0`.

2. **Tower grouping logic** (~lines 270-330): Replace `tg = r["tower_group"] or "Ungrouped"` with `tg = r.get("program_name") or "Ungrouped"`. The data structure key `tower_group` becomes `program_name` throughout.

3. **Key tuple construction** (~lines 217, 239, 270, 286, 330): Replace `(r["tower_group"], ...)` with `(r.get("program_name", ""), ...)`.

4. **`is_program = 1` filter** (~line 187): Replace `AND p.is_program = 1` with a query against `programs` table or join via `program_id`.

5. **Corporate programs query** (~lines 1024-1042): Replace `WHERE is_program = 1` and `tower_group` grouping with `SELECT ... FROM programs JOIN policies ON policies.program_id = programs.id`.

6. **Data dict output** (~line 440): Replace `"tower_group": tg` with `"program_name": tg`.

- [ ] **Step 6: Update seed.py, cli.py, onboard.py**

1. `seed.py`: Remove `tower_group` parameter from `add_policy()`
2. `cli.py`: Remove `tower_group` prompt, display, edit prompts (~lines 420, 437, 445, 510, 550)
3. `onboard.py`: Remove `tower_group` from UPDATE statements

- [ ] **Step 7: Update importer.py**

Keep `tower_group` as an import alias mapping (~line 152). The field is still stored on policies but no longer used for program grouping.

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -q --ignore=tests/test_compliance.py --ignore=tests/test_llm_schemas.py`
Expected: PASS

- [ ] **Step 9: Verify no remaining is_program references in Python**

```bash
grep -rn "is_program" src/policydb/ --include="*.py" | grep -v "__pycache__" | grep -v "# deprecated" | grep -v "migrations/"
```

Expected: Zero results (or only deprecated column comments)

- [ ] **Step 10: Verify no remaining program_carriers references**

```bash
grep -rn "program_carriers" src/policydb/ --include="*.py" --include="*.html" | grep -v "__pycache__" | grep -v "migrations/"
```

Expected: Zero results

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(cleanup): drop program_carriers, remove is_program from exporter/charts/seed/cli/onboard"
```

---

## Task 10: Final Verification

**Files:** None (testing only)

- [ ] **Step 1: Run complete test suite**

```bash
pytest tests/ -v 2>&1 | tail -20
```

Expected: Same pass count as baseline (281+), same 2 pre-existing failures only.

- [ ] **Step 2: Start server and QA**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
pdb serve &
```

Navigate to and verify:
1. Dashboard — loads, no errors
2. Client detail — programs section shows programs from `programs` table
3. Program detail (`/programs/PGM-001`) — all 4 tabs load
4. Policy detail — no program-specific fields, no carrier matrix
5. Policy creation form — no "is_program" checkbox, no tower_group field
6. Reconcile page — upload, map columns, pair, confirm all work
7. Schedule of insurance — child policies show with their own carrier
8. Tower chart — groups by program_id, correct labels
9. Review queue — programs and standalone policies both appear
10. Settings — no program-related config breakage

- [ ] **Step 3: Verify grep zero results**

```bash
# No is_program in active Python (excluding migrations, tests, deprecated comments)
grep -rn "is_program" src/policydb/ --include="*.py" | grep -v "__pycache__" | grep -v "migrations/" | grep -v "# deprecated" | wc -l
# Expected: 0

# No program_carriers anywhere
grep -rn "program_carriers" src/policydb/ --include="*.py" --include="*.html" | grep -v "__pycache__" | grep -v "migrations/" | wc -l
# Expected: 0
```

- [ ] **Step 4: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: Phase 4 QA fixups"
```
