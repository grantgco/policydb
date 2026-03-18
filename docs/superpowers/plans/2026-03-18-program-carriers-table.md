# Program Carriers Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the comma-separated `program_carriers` text field with a structured `program_carriers` table, upgrade the reconciler to use structured matching, and build contenteditable matrix UIs for carrier management.

**Architecture:** New `program_carriers` table with FK to `policies.id`. All reads/writes of the deprecated text field are replaced with table queries. Reconciler gains per-carrier structured matching with policy number bonuses. UIs use the contenteditable matrix pattern with PATCH-on-blur cell saves.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS (contenteditable + fetch)

**Spec:** `docs/superpowers/specs/2026-03-18-program-carriers-table-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/migrations/058_program_carriers_table.sql` | Schema: new table + index + data migration |
| Create | `tests/test_program_carriers.py` | All tests for this feature |
| Create | `src/policydb/web/templates/policies/_program_carriers_matrix.html` | Contenteditable carrier matrix partial |
| Create | `src/policydb/web/templates/policies/_program_carrier_row.html` | Single carrier row partial (for HTMX add-row) |
| Modify | `src/policydb/reconciler.py:323-339,649-657,922-946` | ReconcileRow dataclass + structured matching + enhanced summary |
| Modify | `src/policydb/views.py:95-98,156-162` | v_policy_status + v_schedule: replace text field with subquery/JOIN |
| Modify | `src/policydb/web/routes/policies.py:1254-1322,1363-1401,2256-2314` | CRUD endpoints for carrier rows, deprecate text field writes |
| Modify | `src/policydb/web/routes/reconcile.py:55-69,567-652` | Pre-load carrier rows, batch-create inserts rows |
| Modify | `src/policydb/web/routes/clients.py:437-456` | Load carrier rows from table instead of text field |
| Modify | `src/policydb/web/templates/policies/edit.html:329-400` | Replace textarea with matrix partial include |
| Modify | `src/policydb/web/templates/clients/_programs.html` | Nested carrier rows from table data |
| Modify | `src/policydb/web/templates/reconcile/_batch_create_review.html:100-126` | Program creation preview with carrier detail |
| Modify | `src/policydb/email_templates.py` | Add program_carriers and program_carrier_count tokens |

---

### Task 1: Migration — Create `program_carriers` Table

**Files:**
- Create: `src/policydb/migrations/058_program_carriers_table.sql`
- Test: `tests/test_program_carriers.py`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 058_program_carriers_table.sql
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

-- Migrate any existing comma-separated data (safety net)
INSERT INTO program_carriers (program_id, carrier, sort_order)
SELECT p.id, TRIM(c.value), c.key
FROM policies p, json_each('["' || REPLACE(p.program_carriers, ',', '","') || '"]') c
WHERE p.is_program = 1
  AND p.program_carriers IS NOT NULL
  AND p.program_carriers != '';
```

Write this file to `src/policydb/migrations/058_program_carriers_table.sql`.

- [ ] **Step 2: Write the test for table creation**

```python
# tests/test_program_carriers.py
"""Tests for program_carriers table and related functionality."""

import sqlite3
import pytest
from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_program_carriers_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "program_carriers" in tables
    conn.close()


def test_program_carriers_columns(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(program_carriers)").fetchall()]
    assert "id" in cols
    assert "program_id" in cols
    assert "carrier" in cols
    assert "policy_number" in cols
    assert "premium" in cols
    assert "limit_amount" in cols
    assert "sort_order" in cols
    conn.close()


def test_program_carriers_cascade_delete(tmp_db):
    conn = get_connection(tmp_db)
    # Create a client and program policy
    conn.execute("INSERT INTO clients (name) VALUES ('Test Client')")
    client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, is_program)
           VALUES ('TST-001', ?, 'Property Program', 1)""",
        (client_id,),
    )
    policy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Insert carrier rows
    conn.execute(
        "INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'AIG', 100000)",
        (policy_id,),
    )
    conn.execute(
        "INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'Chubb', 200000)",
        (policy_id,),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM program_carriers WHERE program_id=?", (policy_id,)).fetchone()[0] == 2
    # Delete the policy — carriers should cascade
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM policies WHERE id=?", (policy_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM program_carriers WHERE program_id=?", (policy_id,)).fetchone()[0] == 0
    conn.close()
```

Write this file to `tests/test_program_carriers.py`.

- [ ] **Step 3: Run tests to verify migration works**

Run: `pytest tests/test_program_carriers.py -v`
Expected: All 3 tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/058_program_carriers_table.sql tests/test_program_carriers.py
git commit -m "feat: add program_carriers table (migration 058)"
```

---

### Task 2: Update Views — Deprecate Text Field in SQL Views

**Files:**
- Modify: `src/policydb/views.py:95-98,156-162`
- Test: `tests/test_program_carriers.py`

- [ ] **Step 1: Write tests for updated views**

Add to `tests/test_program_carriers.py`:

```python
def test_v_policy_status_program_carrier_count(tmp_db):
    """v_policy_status should derive carrier count from program_carriers table."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name) VALUES ('View Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, carrier, is_program, renewal_status)
           VALUES ('VT-001', ?, 'Property Program', 'AIG', 1, 'Bound')""",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'AIG', 50000)", (pid,))
    conn.execute("INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'Chubb', 75000)", (pid,))
    conn.commit()
    row = conn.execute("SELECT * FROM v_policy_status WHERE policy_uid='VT-001'").fetchone()
    assert row is not None
    # program_carrier_count should come from table count, not the deprecated column
    assert dict(row)["program_carrier_count"] == 2
    conn.close()


def test_v_schedule_program_carriers_from_table(tmp_db):
    """v_schedule should list carriers from program_carriers table for programs."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name) VALUES ('Sched Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, carrier, is_program,
                                 effective_date, expiration_date)
           VALUES ('SC-001', ?, 'Casualty Program', 'Zurich', 1, '2025-01-01', '2026-01-01')""",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO program_carriers (program_id, carrier, sort_order) VALUES (?, 'Zurich', 0)", (pid,))
    conn.execute("INSERT INTO program_carriers (program_id, carrier, sort_order) VALUES (?, 'Liberty', 1)", (pid,))
    conn.commit()
    row = conn.execute("SELECT * FROM v_schedule WHERE \"Policy Number\" IS NULL AND \"Line of Business\" LIKE '%Casualty%'").fetchone()
    assert row is not None
    carrier_val = dict(row)["Carrier"]
    assert "Zurich" in carrier_val
    assert "Liberty" in carrier_val
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_program_carriers.py::test_v_policy_status_program_carrier_count tests/test_program_carriers.py::test_v_schedule_program_carriers_from_table -v`
Expected: FAIL — views still read from deprecated text columns

- [ ] **Step 3: Update `v_policy_status` in `src/policydb/views.py`**

Replace lines 96-97 (the `p.program_carriers, p.program_carrier_count` select):

```python
# Old:
    p.program_carriers,
    p.program_carrier_count,

# New:
    (SELECT GROUP_CONCAT(pc.carrier, ', ') FROM program_carriers pc WHERE pc.program_id = p.id ORDER BY pc.sort_order) AS program_carriers,
    (SELECT COUNT(*) FROM program_carriers pc WHERE pc.program_id = p.id) AS program_carrier_count,
```

- [ ] **Step 4: Update `v_schedule` in `src/policydb/views.py`**

Replace line 162 (the `CASE WHEN p.is_program...` carrier line):

```python
# Old:
    CASE WHEN p.is_program = 1 THEN COALESCE(p.program_carriers, p.carrier) ELSE p.carrier END AS "Carrier",

# New:
    CASE WHEN p.is_program = 1
         THEN COALESCE((SELECT GROUP_CONCAT(pc.carrier, ', ') FROM program_carriers pc WHERE pc.program_id = p.id ORDER BY pc.sort_order), p.carrier)
         ELSE p.carrier END AS "Carrier",
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_program_carriers.py -v`
Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/views.py tests/test_program_carriers.py
git commit -m "feat: update SQL views to read from program_carriers table"
```

---

### Task 3: Reconciler — Structured Matching + Enhanced Summary

**Files:**
- Modify: `src/policydb/reconciler.py:323-339,649-657,922-946`
- Test: `tests/test_program_carriers.py`

- [ ] **Step 1: Write tests for structured matching**

Add to `tests/test_program_carriers.py`:

```python
from policydb.reconciler import ReconcileRow, reconcile


def test_reconciler_program_carrier_match_with_policy_number():
    """Reconciler should match import rows to program carrier entries using policy number."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-001", "client_name": "Acme Corp",
        "policy_type": "Property Program", "carrier": "AIG",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 500000, "limit_amount": 10000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 10, "carrier": "AIG", "policy_number": "POL-4481", "premium": 200000, "limit_amount": 5000000},
            {"id": 11, "carrier": "Chubb", "policy_number": "CHB-889", "premium": 300000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [{
        "client_name": "Acme Corp", "policy_type": "Property",
        "carrier": "AIG", "policy_number": "POL-4481",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 200000, "limit_amount": 5000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    matches = [r for r in results if r.status in ("MATCH", "DIFF")]
    assert len(matches) >= 1
    assert matches[0].is_program_match is True
    assert matches[0].matched_carrier_id == 10


def test_reconciler_program_carrier_no_match():
    """Carrier not in program_carrier_rows should not get program bonus."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-002", "client_name": "Beta Inc",
        "policy_type": "Casualty Program", "carrier": "Zurich",
        "effective_date": "2025-01-01", "expiration_date": "2026-01-01",
        "premium": 100000, "limit_amount": 5000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 20, "carrier": "Zurich", "policy_number": "ZNA-001", "premium": 100000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [{
        "client_name": "Beta Inc", "policy_type": "Casualty",
        "carrier": "Hartford", "policy_number": "HFD-999",
        "effective_date": "2025-01-01", "expiration_date": "2026-01-01",
        "premium": 50000, "limit_amount": 2000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    # Hartford doesn't match Zurich carrier row — should be MISSING
    missing = [r for r in results if r.status == "MISSING"]
    assert len(missing) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_program_carriers.py::test_reconciler_program_carrier_match_with_policy_number tests/test_program_carriers.py::test_reconciler_program_carrier_no_match -v`
Expected: FAIL — `matched_carrier_id` field doesn't exist on ReconcileRow, and matching logic still uses text field

- [ ] **Step 3: Add `matched_carrier_id` to ReconcileRow dataclass**

In `src/policydb/reconciler.py`, after line 339 (`is_program_match: bool = False`), add:

```python
    matched_carrier_id: int | None = None  # ID of matched program_carriers row
```

- [ ] **Step 4: Replace substring matching with structured matching**

In `src/policydb/reconciler.py`, replace lines 654-657 (the `elif db.get("is_program")` block):

```python
        # Old:
            # Program carrier list: if ext carrier appears in program_carriers, boost
            elif db.get("is_program") and db.get("program_carriers"):
                if ext_carrier.strip().lower() in db["program_carriers"].lower():
                    combined += 15  # carrier found in program carrier list

        # New:
            # Program carrier rows: structured matching against carrier table
            elif db.get("is_program") and db.get("_program_carrier_rows"):
                for _pc in db["_program_carrier_rows"]:
                    if fuzz.WRatio(ext_carrier, _pc.get("carrier", "")) >= 70:
                        combined += 10
                        _pc_pn = _normalize_policy_number(_pc.get("policy_number") or "")
                        if ext_pn and _pc_pn:
                            if ext_pn == _pc_pn:
                                combined += 30
                            elif fuzz.ratio(ext_pn, _pc_pn) >= 90:
                                combined += 25
                            elif fuzz.ratio(ext_pn, _pc_pn) >= 75:
                                combined += 10
                        break
```

- [ ] **Step 5: Track matched carrier ID in result construction**

In the fuzzy match result construction (around lines 893-898), when building `ReconcileRow` for program matches, add `matched_carrier_id`. Find the code that sets `is_program_match=db_idx in _program_indices` and update:

```python
            # Determine matched carrier ID for program matches
            _matched_cid = None
            if db_idx in _program_indices and db.get("_program_carrier_rows") and ext:
                _ext_carrier = ext.get("carrier", "")
                for _pc in db["_program_carrier_rows"]:
                    if fuzz.WRatio(_ext_carrier, _pc.get("carrier", "")) >= 70:
                        _matched_cid = _pc.get("id")
                        break
            row = ReconcileRow(status, ext, db, diff_fields, score, cosmetic_diffs=cosmetic,
                               is_program_match=db_idx in _program_indices,
                               matched_carrier_id=_matched_cid)
```

- [ ] **Step 6: Update `program_reconcile_summary()` with per-carrier detail**

Replace the `program_reconcile_summary()` function (lines 922-946):

```python
def program_reconcile_summary(results: list[ReconcileRow], carrier_map: dict | None = None) -> dict[str, dict]:
    """Build per-program reconciliation summary from results.

    Args:
        results: List of ReconcileRow from reconcile()
        carrier_map: {program_id: [carrier_row_dicts]} for per-carrier detail

    Returns: {policy_uid: {total_premium, matched_premium, matched_count, carrier_count,
                           fully_reconciled, carrier_detail, new_carriers}}
    """
    carrier_map = carrier_map or {}
    summaries: dict[str, dict] = {}
    for r in results:
        if not r.is_program_match or r.db is None:
            continue
        uid = r.db.get("policy_uid", "")
        pid = r.db.get("id")
        if uid not in summaries:
            db_carriers = carrier_map.get(pid, [])
            summaries[uid] = {
                "policy_type": r.db.get("policy_type", ""),
                "total_premium": float(r.db.get("premium") or 0),
                "carrier_count": len(db_carriers),
                "matched_premium": 0.0,
                "matched_count": 0,
                "carrier_detail": [],
                "new_carriers": [],
                "_matched_carrier_ids": set(),
            }
        ext_prem = float(r.ext.get("premium") or 0) if r.ext else 0
        summaries[uid]["matched_premium"] += ext_prem
        summaries[uid]["matched_count"] += 1
        if r.matched_carrier_id:
            summaries[uid]["_matched_carrier_ids"].add(r.matched_carrier_id)
            # Find the DB carrier row for comparison
            db_carrier = next((c for c in carrier_map.get(pid, []) if c["id"] == r.matched_carrier_id), None)
            db_prem = float(db_carrier["premium"]) if db_carrier else 0
            status = "MATCH" if abs(ext_prem - db_prem) <= db_prem * 0.01 else "DIFF"
            summaries[uid]["carrier_detail"].append({
                "carrier_id": r.matched_carrier_id,
                "carrier": r.ext.get("carrier", "") if r.ext else "",
                "db_premium": db_prem,
                "ext_premium": ext_prem,
                "status": status,
            })
        else:
            # Matched to program but no specific carrier row — treat as new
            summaries[uid]["new_carriers"].append({
                "carrier": r.ext.get("carrier", "") if r.ext else "",
                "policy_number": r.ext.get("policy_number", "") if r.ext else "",
                "premium": ext_prem,
                "limit_amount": float(r.ext.get("limit_amount") or 0) if r.ext else 0,
            })

    for uid, s in summaries.items():
        total = s["total_premium"]
        s["fully_reconciled"] = s["matched_premium"] >= total * 0.95 if total > 0 else s["matched_count"] > 0
        del s["_matched_carrier_ids"]
    return summaries
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_program_carriers.py -v`
Expected: All 7 tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/policydb/reconciler.py tests/test_program_carriers.py
git commit -m "feat: structured program carrier matching in reconciler"
```

---

### Task 4: API Endpoints — Program Carrier CRUD

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Create: `src/policydb/web/templates/policies/_program_carrier_row.html`

- [ ] **Step 1: Add PATCH endpoint for cell save**

Add after the `program_link_policy` function (after line 1322 of `src/policydb/web/routes/policies.py`):

```python
@router.patch("/{policy_uid}/program-carrier/{carrier_id}")
async def program_carrier_patch(
    request: Request,
    policy_uid: str,
    carrier_id: int,
    conn=Depends(get_db),
):
    """Update a single field on a program carrier row (contenteditable cell save)."""
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    allowed_fields = {"carrier", "policy_number", "premium", "limit_amount"}
    if field not in allowed_fields:
        return JSONResponse({"ok": False, "error": f"Invalid field: {field}"}, status_code=400)

    # Verify the carrier row belongs to this program
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    row = conn.execute(
        "SELECT * FROM program_carriers WHERE id = ? AND program_id = ?",
        (carrier_id, program["id"]),
    ).fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "Carrier row not found"}, status_code=404)

    # Format value based on field type
    formatted = value
    if field in ("premium", "limit_amount"):
        try:
            num = float(str(value).replace("$", "").replace(",", "").strip() or "0")
            conn.execute(f"UPDATE program_carriers SET {field} = ? WHERE id = ?", (num, carrier_id))
            formatted = f"${num:,.0f}"
        except ValueError:
            return JSONResponse({"ok": False, "error": "Invalid number"}, status_code=400)
    else:
        conn.execute(f"UPDATE program_carriers SET {field} = ? WHERE id = ?", (value.strip(), carrier_id))
        formatted = value.strip()

    conn.commit()

    # Update parent policy premium/limit totals
    totals = conn.execute(
        "SELECT COALESCE(SUM(premium), 0) AS total_premium, COALESCE(SUM(limit_amount), 0) AS total_limit FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()
    conn.execute(
        "UPDATE policies SET premium = ?, limit_amount = ? WHERE id = ?",
        (totals["total_premium"], totals["total_limit"], program["id"]),
    )
    conn.commit()

    return JSONResponse({
        "ok": True,
        "formatted": formatted,
        "totals": {
            "premium": f"${totals['total_premium']:,.0f}",
            "limit": f"${totals['total_limit']:,.0f}",
            "count": conn.execute("SELECT COUNT(*) FROM program_carriers WHERE program_id = ?", (program["id"],)).fetchone()[0],
        },
    })


@router.post("/{policy_uid}/program-carrier")
def program_carrier_add(
    policy_uid: str,
    conn=Depends(get_db),
):
    """Add a new blank carrier row to a program."""
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()[0]

    conn.execute(
        "INSERT INTO program_carriers (program_id, carrier, sort_order) VALUES (?, '', ?)",
        (program["id"], max_order + 1),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        f'<tr data-id="{new_id}" class="border-b border-gray-50 hover:bg-gray-50 transition-colors">'
        f'<td class="px-2 py-2 text-gray-400 cursor-grab no-print" draggable="true" '
        f'ondragstart="pcDragStart(event)" ondragover="pcDragOver(event)" ondrop="pcDrop(event)">&#x2807;</td>'
        f'<td class="px-3 py-2 text-sm text-gray-800" contenteditable="true" '
        f'data-field="carrier" data-id="{new_id}" data-placeholder="carrier" '
        f'data-endpoint="/policies/{policy_uid.upper()}/program-carrier/{new_id}"></td>'
        f'<td class="px-3 py-2 text-sm text-gray-800" contenteditable="true" '
        f'data-field="policy_number" data-id="{new_id}" data-placeholder="policy #" '
        f'data-endpoint="/policies/{policy_uid.upper()}/program-carrier/{new_id}"></td>'
        f'<td class="px-3 py-2 text-sm text-gray-800 text-right tabular-nums" contenteditable="true" '
        f'data-field="premium" data-id="{new_id}" data-placeholder="$0" '
        f'data-endpoint="/policies/{policy_uid.upper()}/program-carrier/{new_id}"></td>'
        f'<td class="px-3 py-2 text-sm text-gray-800 text-right tabular-nums" contenteditable="true" '
        f'data-field="limit_amount" data-id="{new_id}" data-placeholder="$0" '
        f'data-endpoint="/policies/{policy_uid.upper()}/program-carrier/{new_id}"></td>'
        f'<td class="px-2 py-2 no-print">'
        f'<button type="button" onclick="pcDeleteRow(this, \'{policy_uid.upper()}\', {new_id})" '
        f'class="text-red-300 hover:text-red-500 text-xs">✕</button></td>'
        f'</tr>'
    )


@router.delete("/{policy_uid}/program-carrier/{carrier_id}")
def program_carrier_delete(
    policy_uid: str,
    carrier_id: int,
    conn=Depends(get_db),
):
    """Delete a carrier row from a program."""
    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    conn.execute(
        "DELETE FROM program_carriers WHERE id = ? AND program_id = ?",
        (carrier_id, program["id"]),
    )
    # Update totals
    totals = conn.execute(
        "SELECT COALESCE(SUM(premium), 0) AS total_premium, COALESCE(SUM(limit_amount), 0) AS total_limit FROM program_carriers WHERE program_id = ?",
        (program["id"],),
    ).fetchone()
    conn.execute(
        "UPDATE policies SET premium = ?, limit_amount = ? WHERE id = ?",
        (totals["total_premium"], totals["total_limit"], program["id"]),
    )
    conn.commit()
    return JSONResponse({"ok": True})


@router.post("/{policy_uid}/program-carrier/reorder")
async def program_carrier_reorder(
    request: Request,
    policy_uid: str,
    conn=Depends(get_db),
):
    """Reorder carrier rows in a program."""
    body = await request.json()
    order = body.get("order", [])

    program = conn.execute(
        "SELECT id FROM policies WHERE policy_uid = ? AND is_program = 1",
        (policy_uid.upper(),),
    ).fetchone()
    if not program:
        return JSONResponse({"ok": False, "error": "Program not found"}, status_code=404)

    for i, cid in enumerate(order):
        conn.execute(
            "UPDATE program_carriers SET sort_order = ? WHERE id = ? AND program_id = ?",
            (i, cid, program["id"]),
        )
    conn.commit()
    return JSONResponse({"ok": True})
```

- [ ] **Step 2: Remove `program_carriers` and `program_carrier_count` from policy edit save**

In `src/policydb/web/routes/policies.py`, function `policy_edit_post` (line 1325):
- Remove the `program_carriers: str = Form("")` and `program_carrier_count: str = Form("")` parameters (lines 1364-1365)
- Remove lines 1385-1388 (the `pgm_carriers` / `pgm_count` computation)
- Remove `program_carriers=?, program_carrier_count=?` from the UPDATE SQL (line 1401)
- Remove `pgm_carriers, pgm_count` from the parameter tuple

Similarly in `policy_new_post` (line 2217):
- Remove the same parameters (lines 2257-2258)
- Remove lines 2279-2282 (computation)
- Remove from INSERT SQL (line 2295)
- Remove from parameter tuple (line 2314)

- [ ] **Step 3: Load carrier rows in policy edit GET handler**

In the `policy_edit` function (around line 1254), replace the `program_linked_policies` query with:

```python
        "program_carrier_rows": [dict(r) for r in conn.execute(
            "SELECT * FROM program_carriers WHERE program_id = ? ORDER BY sort_order",
            (policy_dict["id"],),
        ).fetchall()] if policy_dict.get("is_program") else [],
```

Keep the existing `program_linked_policies` and `linkable_policies` queries as they are (linked policies still exist as a feature).

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/policies.py
git commit -m "feat: add program carrier CRUD endpoints, deprecate text field writes"
```

---

### Task 5: Policy Edit UI — Contenteditable Carrier Matrix

**Files:**
- Create: `src/policydb/web/templates/policies/_program_carriers_matrix.html`
- Modify: `src/policydb/web/templates/policies/edit.html:329-400`

- [ ] **Step 1: Create the carrier matrix partial**

Write to `src/policydb/web/templates/policies/_program_carriers_matrix.html`:

```html
{# Program Carriers — Contenteditable Matrix #}
{% set carrier_rows = program_carrier_rows or [] %}
{% set total_premium = carrier_rows | sum(attribute='premium') %}
{% set total_limit = carrier_rows | sum(attribute='limit_amount') %}

<div class="mt-1">
  <div class="flex items-center justify-between mb-2">
    <p class="text-xs font-semibold text-gray-500 uppercase tracking-wide">Participating Carriers</p>
    <span class="text-xs text-gray-400" id="pc-summary">{{ carrier_rows | length }} carrier{{ 's' if carrier_rows | length != 1 }} · {{ total_premium | currency }}</span>
  </div>

  <style>
    td[contenteditable][data-placeholder]:empty::before {
      content: attr(data-placeholder);
      color: #94a3b8;
      font-style: italic;
      pointer-events: none;
    }
    td[contenteditable]:focus {
      outline: none;
      border-bottom: 2px solid #3b82f6;
      background: rgba(59, 130, 246, 0.03);
    }
  </style>

  <table class="w-full text-sm" id="pc-matrix">
    <thead>
      <tr class="border-b border-gray-100 text-left text-xs text-gray-400">
        <th class="px-2 py-1.5 font-medium w-8 no-print"></th>
        <th class="px-3 py-1.5 font-medium">Carrier</th>
        <th class="px-3 py-1.5 font-medium">Policy #</th>
        <th class="px-3 py-1.5 font-medium text-right">Premium</th>
        <th class="px-3 py-1.5 font-medium text-right">Limit</th>
        <th class="px-2 py-1.5 font-medium w-8 no-print"></th>
      </tr>
    </thead>
    <tbody id="pc-tbody">
      {% for cr in carrier_rows %}
      <tr data-id="{{ cr.id }}" class="border-b border-gray-50 hover:bg-gray-50 transition-colors">
        <td class="px-2 py-2 text-gray-400 cursor-grab no-print" draggable="true"
            ondragstart="pcDragStart(event)" ondragover="pcDragOver(event)" ondrop="pcDrop(event)">&#x2807;</td>
        <td class="px-3 py-2 text-sm text-gray-800" contenteditable="true"
            data-field="carrier" data-id="{{ cr.id }}" data-placeholder="carrier"
            data-endpoint="/policies/{{ policy.policy_uid }}/program-carrier/{{ cr.id }}">{{ cr.carrier or '' }}</td>
        <td class="px-3 py-2 text-sm text-gray-800" contenteditable="true"
            data-field="policy_number" data-id="{{ cr.id }}" data-placeholder="policy #"
            data-endpoint="/policies/{{ policy.policy_uid }}/program-carrier/{{ cr.id }}">{{ cr.policy_number or '' }}</td>
        <td class="px-3 py-2 text-sm text-gray-800 text-right tabular-nums" contenteditable="true"
            data-field="premium" data-id="{{ cr.id }}" data-placeholder="$0"
            data-endpoint="/policies/{{ policy.policy_uid }}/program-carrier/{{ cr.id }}">{% if cr.premium %}${{ '{:,.0f}'.format(cr.premium) }}{% endif %}</td>
        <td class="px-3 py-2 text-sm text-gray-800 text-right tabular-nums" contenteditable="true"
            data-field="limit_amount" data-id="{{ cr.id }}" data-placeholder="$0"
            data-endpoint="/policies/{{ policy.policy_uid }}/program-carrier/{{ cr.id }}">{% if cr.limit_amount %}${{ '{:,.0f}'.format(cr.limit_amount) }}{% endif %}</td>
        <td class="px-2 py-2 no-print">
          <button type="button" onclick="pcDeleteRow(this, '{{ policy.policy_uid }}', {{ cr.id }})"
                  class="text-red-300 hover:text-red-500 text-xs">&#10005;</button>
        </td>
      </tr>
      {% endfor %}
    </tbody>
    <tfoot>
      <tr class="border-t border-gray-200">
        <td colspan="3" class="px-3 py-2">
          <button type="button" class="no-print text-xs text-gray-400 border border-dashed border-gray-300 px-3 py-1 rounded hover:border-gray-400 hover:text-gray-600 transition-colors"
                  hx-post="/policies/{{ policy.policy_uid }}/program-carrier"
                  hx-target="#pc-tbody"
                  hx-swap="beforeend">
            + Add Carrier
          </button>
        </td>
        <td class="px-3 py-2 text-right text-xs font-semibold text-gray-500 tabular-nums" id="pc-total-premium">
          {% if total_premium %}${{ '{:,.0f}'.format(total_premium) }}{% endif %}
        </td>
        <td class="px-3 py-2 text-right text-xs font-semibold text-gray-500 tabular-nums" id="pc-total-limit">
          {% if total_limit %}${{ '{:,.0f}'.format(total_limit) }}{% endif %}
        </td>
        <td></td>
      </tr>
    </tfoot>
  </table>
</div>

<script>
(function() {
  function flashCell(el) {
    el.style.transition = 'background-color 0.3s ease';
    el.style.backgroundColor = '#d1fae5';
    setTimeout(function() {
      el.style.backgroundColor = '';
      setTimeout(function() { el.style.transition = ''; }, 300);
    }, 800);
  }

  function saveCell(cell) {
    var raw = cell.textContent.trim();
    var endpoint = cell.dataset.endpoint;
    var field = cell.dataset.field;
    fetch(endpoint, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({field: field, value: raw})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        if (data.formatted !== raw) {
          cell.textContent = data.formatted;
          flashCell(cell);
        }
        if (data.totals) {
          var s = document.getElementById('pc-summary');
          if (s) s.textContent = data.totals.count + ' carrier' + (data.totals.count !== 1 ? 's' : '') + ' \u00b7 ' + data.totals.premium;
          var tp = document.getElementById('pc-total-premium');
          if (tp) tp.textContent = data.totals.premium;
          var tl = document.getElementById('pc-total-limit');
          if (tl) tl.textContent = data.totals.limit;
        }
      }
    });
  }

  // Attach blur handler to existing and future cells
  document.getElementById('pc-matrix').addEventListener('blur', function(e) {
    if (e.target.matches('td[contenteditable]')) saveCell(e.target);
  }, true);

  // Tab navigation
  document.getElementById('pc-matrix').addEventListener('keydown', function(e) {
    if (e.key === 'Tab' && e.target.matches('td[contenteditable]')) {
      e.preventDefault();
      var cells = Array.from(this.querySelectorAll('td[contenteditable]'));
      var idx = cells.indexOf(e.target);
      if (idx < cells.length - 1) {
        e.target.blur();
        cells[idx + 1].focus();
      } else {
        // Last cell — trigger add row
        e.target.blur();
        var addBtn = this.querySelector('tfoot button[hx-post]');
        if (addBtn) htmx.trigger(addBtn, 'click');
      }
    }
  });

  // Drag reorder
  var pcDragRow = null;
  window.pcDragStart = function(e) { pcDragRow = e.target.closest('tr'); };
  window.pcDragOver = function(e) { e.preventDefault(); };
  window.pcDrop = function(e) {
    e.preventDefault();
    var target = e.target.closest('tr');
    if (target && pcDragRow !== target) {
      var tbody = target.closest('tbody');
      tbody.insertBefore(pcDragRow, target);
      var order = Array.from(tbody.querySelectorAll('tr[data-id]')).map(function(tr) {
        return parseInt(tr.dataset.id);
      });
      fetch('/policies/{{ policy.policy_uid }}/program-carrier/reorder', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({order: order})
      });
    }
  };

  // Delete row
  window.pcDeleteRow = function(btn, uid, carrierId) {
    var row = btn.closest('tr');
    fetch('/policies/' + uid + '/program-carrier/' + carrierId, {method: 'DELETE'})
    .then(function(r) { return r.json(); })
    .then(function(data) { if (data.ok) row.remove(); });
  };
})();
</script>
```

- [ ] **Step 2: Update `edit.html` to use the matrix partial**

In `src/policydb/web/templates/policies/edit.html`, replace lines 336-347 (the textarea and number input for program fields) with:

```html
      {% include 'policies/_program_carriers_matrix.html' %}
```

Keep the "Linked Policies" section below it (lines 349-397) unchanged — linked policies and carrier rows are separate features.

- [ ] **Step 3: Manual test — start server, navigate to a program policy edit page**

Run: `policydb serve`
Navigate to any program policy edit page. Verify:
- Matrix renders with existing carrier data (or empty state)
- Click a cell to edit — blue bottom border appears
- Tab navigates between cells
- Blur saves and shows green flash if formatted
- "+ Add Carrier" adds a new row
- "✕" deletes a row
- Drag handle reorders
- Summary line updates on save

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/policies/_program_carriers_matrix.html src/policydb/web/templates/policies/edit.html
git commit -m "feat: contenteditable carrier matrix on policy edit page"
```

---

### Task 6: Client Detail — Updated Programs Card

**Files:**
- Modify: `src/policydb/web/routes/clients.py:437-456`
- Modify: `src/policydb/web/templates/clients/_programs.html`

- [ ] **Step 1: Update client detail route to load carrier rows from table**

In `src/policydb/web/routes/clients.py`, replace lines 438-456:

```python
    # Corporate programs (is_program=1) with carrier rows from program_carriers table
    programs = [dict(r) for r in conn.execute(
        """SELECT id, policy_uid, policy_type, carrier, effective_date, expiration_date,
                  premium, limit_amount, renewal_status
           FROM policies WHERE client_id = ? AND archived = 0 AND is_program = 1
           ORDER BY policy_type""",
        (client_id,),
    ).fetchall()]
    _program_linked_ids = set()
    for pgm in programs:
        # Carrier rows from structured table
        pgm["carrier_rows"] = [dict(r) for r in conn.execute(
            """SELECT id, carrier, policy_number, premium, limit_amount
               FROM program_carriers WHERE program_id = ? ORDER BY sort_order""",
            (pgm["id"],),
        ).fetchall()]
        pgm["program_carrier_count"] = len(pgm["carrier_rows"])
        # Still load linked policies (legacy feature)
        linked = [dict(r) for r in conn.execute(
            """SELECT policy_uid, policy_type, carrier, premium, limit_amount,
                      effective_date, expiration_date
               FROM policies WHERE program_id = ? AND archived = 0
               ORDER BY policy_type""",
            (pgm["id"],),
        ).fetchall()]
        pgm["linked_policies"] = linked
        for lp in linked:
            _program_linked_ids.add(lp["policy_uid"])
```

- [ ] **Step 2: Update `_programs.html` template with nested carrier rows**

Replace the full content of `src/policydb/web/templates/clients/_programs.html`:

```html
{% if programs %}
{% set total_program_premium = programs | sum(attribute='premium') %}
<details open class="card mb-4 overflow-hidden">
  <summary class="px-4 py-2.5 bg-blue-50 border-b border-blue-100 cursor-pointer select-none list-none flex items-center gap-2 hover:bg-blue-100 transition-colors">
    <span class="text-xs text-blue-400 details-arrow">&#9654;</span>
    <span class="text-xs font-bold text-marsh uppercase tracking-wide">Corporate Programs</span>
    <span class="text-xs text-gray-400">&middot; {{ programs | length }} program{{ 's' if programs | length != 1 }} &middot; {{ total_program_premium | currency }} total premium</span>
  </summary>
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-gray-100 text-left text-xs text-gray-400">
          <th class="px-4 py-2 font-medium"></th>
          <th class="px-4 py-2 font-medium">Program</th>
          <th class="px-4 py-2 font-medium">Lead Carrier</th>
          <th class="px-4 py-2 font-medium text-right">Total Limit</th>
          <th class="px-4 py-2 font-medium text-right">Total Premium</th>
          <th class="px-4 py-2 font-medium text-center">Carriers</th>
          <th class="px-4 py-2 font-medium">Term</th>
          <th class="px-4 py-2 font-medium">Status</th>
        </tr>
      </thead>
      <tbody>
        {% for p in programs %}
        <tr class="border-b border-gray-50 hover:bg-gray-50 transition-colors">
          <td class="px-4 py-2.5">
            <span class="bg-blue-100 text-blue-700 text-[10px] font-bold px-1.5 py-0.5 rounded">PGM</span>
          </td>
          <td class="px-4 py-2.5">
            <a href="/policies/{{ p.policy_uid }}/edit" class="font-medium text-marsh hover:underline" target="_blank">{{ p.policy_type }}</a>
          </td>
          <td class="px-4 py-2.5 text-gray-600">{{ p.carrier_rows[0].carrier if p.carrier_rows else (p.carrier or '\u2014') }}</td>
          <td class="px-4 py-2.5 text-right font-medium text-gray-900 tabular-nums">{% if p.limit_amount %}{{ p.limit_amount | currency }}{% else %}&mdash;{% endif %}</td>
          <td class="px-4 py-2.5 text-right font-medium text-gray-900 tabular-nums">{% if p.premium %}{{ p.premium | currency }}{% else %}&mdash;{% endif %}</td>
          <td class="px-4 py-2.5 text-center">
            {% if p.program_carrier_count %}
            <span class="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">{{ p.program_carrier_count }}</span>
            {% else %}&mdash;{% endif %}
          </td>
          <td class="px-4 py-2.5 text-xs text-gray-500">
            {% if p.effective_date and p.expiration_date %}
            {{ p.effective_date }} &ndash; {{ p.expiration_date }}
            {% else %}&mdash;{% endif %}
          </td>
          <td class="px-4 py-2.5">
            {% if p.renewal_status %}
            <span class="text-xs px-2 py-0.5 rounded
              {% if p.renewal_status == 'Bound' %}bg-green-50 text-green-700
              {% elif p.renewal_status == 'In Progress' %}bg-blue-50 text-blue-700
              {% elif p.renewal_status == 'Pending Bind' %}bg-amber-50 text-amber-700
              {% else %}bg-gray-100 text-gray-600{% endif %}">{{ p.renewal_status }}</span>
            {% endif %}
          </td>
        </tr>
        {# Nested carrier rows from program_carriers table #}
        {% for cr in p.carrier_rows %}
        <tr class="border-b border-gray-50 bg-blue-50/30">
          <td class="px-4 py-1.5 pl-8">
            <span class="text-[9px] text-gray-300">{{ '\u2514' if loop.last else '\u251c' }}</span>
          </td>
          <td class="px-4 py-1.5 text-xs text-gray-600">{{ cr.carrier }}</td>
          <td class="px-4 py-1.5 text-xs text-gray-400 font-mono">{{ cr.policy_number or '' }}</td>
          <td class="px-4 py-1.5 text-xs text-right text-gray-600 tabular-nums">{% if cr.limit_amount %}{{ cr.limit_amount | currency }}{% else %}&mdash;{% endif %}</td>
          <td class="px-4 py-1.5 text-xs text-right text-gray-600 tabular-nums">{% if cr.premium %}{{ cr.premium | currency }}{% else %}&mdash;{% endif %}</td>
          <td class="px-4 py-1.5"></td>
          <td class="px-4 py-1.5"></td>
          <td class="px-4 py-1.5"></td>
        </tr>
        {% endfor %}
        {# Linked policies (legacy — still shown if any exist) #}
        {% if p.linked_policies %}
        {% for lp in p.linked_policies %}
        <tr class="border-b border-gray-50 bg-gray-50/50">
          <td class="px-4 py-1.5 pl-8">
            <span class="text-[9px] text-gray-300">&rarr;</span>
          </td>
          <td class="px-4 py-1.5">
            <a href="/policies/{{ lp.policy_uid }}/edit" class="text-xs text-marsh hover:underline" target="_blank">{{ lp.policy_type }}</a>
          </td>
          <td class="px-4 py-1.5 text-xs text-gray-500">{{ lp.carrier or '\u2014' }}</td>
          <td class="px-4 py-1.5 text-xs text-right text-gray-600 tabular-nums">{% if lp.limit_amount %}{{ lp.limit_amount | currency }}{% else %}&mdash;{% endif %}</td>
          <td class="px-4 py-1.5 text-xs text-right text-gray-600 tabular-nums">{% if lp.premium %}{{ lp.premium | currency }}{% else %}&mdash;{% endif %}</td>
          <td class="px-4 py-1.5"></td>
          <td class="px-4 py-1.5 text-[10px] text-gray-400">{{ lp.effective_date or '' }}</td>
          <td class="px-4 py-1.5"></td>
        </tr>
        {% endfor %}
        {% endif %}
        {% endfor %}
      </tbody>
    </table>
  </div>
</details>
{% endif %}
```

- [ ] **Step 3: Manual test — navigate to a client detail page with a program**

Verify carrier rows show nested under the program with indentation, and legacy linked policies still appear if any exist.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_programs.html
git commit -m "feat: client detail programs card with structured carrier rows"
```

---

### Task 7: Reconcile — Batch Create Inserts Carrier Rows

**Files:**
- Modify: `src/policydb/web/routes/reconcile.py:55-69,567-652`
- Modify: `src/policydb/web/templates/reconcile/_batch_create_review.html`

- [ ] **Step 1: Update `_load_db_policies()` to attach carrier rows**

In `src/policydb/web/routes/reconcile.py`, after the `_load_db_policies()` function returns `db_rows` (around line 69), add a helper to attach carrier rows. Find where `_load_db_policies` is called (in the `reconcile_post` handler) and add after the call:

```python
    # Attach program carrier rows for structured matching
    program_ids = [r["id"] for r in db_rows if r.get("is_program")]
    _carrier_map = {}
    if program_ids:
        _pc_rows = conn.execute(
            f"SELECT * FROM program_carriers WHERE program_id IN ({','.join('?' * len(program_ids))})",
            program_ids,
        ).fetchall()
        for _pcr in _pc_rows:
            _carrier_map.setdefault(_pcr["program_id"], []).append(dict(_pcr))
    for r in db_rows:
        if r.get("is_program"):
            r["_program_carrier_rows"] = _carrier_map.get(r["id"], [])
```

Store `_carrier_map` so it can be passed to `program_reconcile_summary()` later.

- [ ] **Step 2: Update `batch_create_program` to insert carrier rows**

Replace the `batch_create_program` function body (lines 567-652) — specifically the INSERT and response. After the policy INSERT, add carrier row inserts:

```python
    # After the conn.execute INSERT for the program policy...
    policy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert carrier rows from selected import data
    for sort_idx, idx in enumerate(selected_indices):
        if idx < 0 or idx >= len(missing_rows_list):
            continue
        ext = missing_rows_list[idx]
        c = (ext.get("carrier") or "").strip()
        pn = (ext.get("policy_number") or "").strip()
        try:
            prem = float(ext.get("premium") or 0)
        except (TypeError, ValueError):
            prem = 0
        try:
            lim = float(ext.get("limit_amount") or 0)
        except (TypeError, ValueError):
            lim = 0
        conn.execute(
            """INSERT INTO program_carriers (program_id, carrier, policy_number, premium, limit_amount, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (policy_id, c, pn, prem, lim, sort_idx),
        )
    conn.commit()
```

Also remove `program_carriers` and `program_carrier_count` from the policy INSERT SQL — replace with just `is_program` = 1. The `carrier` column gets the first carrier (lead), `premium` and `limit_amount` get the sums.

- [ ] **Step 3: Update batch create review template with carrier preview**

In `src/policydb/web/templates/reconcile/_batch_create_review.html`, update the Option 2 section (lines 100-126) to show a more descriptive preview. The existing `program-preview` span already shows carrier count and total premium — this is sufficient for now. The key change is that the backend now inserts structured rows.

- [ ] **Step 4: Manual test — import a CSV, batch-create a program, verify carrier rows exist**

Run: `policydb serve`
1. Go to `/reconcile`, upload a test CSV with multiple rows for the same client
2. Click "Batch Create Review"
3. Select rows and click "Create Program from Selected"
4. Navigate to the created program's edit page
5. Verify the carrier matrix shows one row per selected import row with carrier, policy #, premium, limit populated

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/reconcile.py src/policydb/web/templates/reconcile/_batch_create_review.html
git commit -m "feat: batch create program inserts structured carrier rows"
```

---

### Task 8: Reconcile — Pre-load Carrier Rows for Matching

**Files:**
- Modify: `src/policydb/web/routes/reconcile.py`

- [ ] **Step 1: Pass `carrier_map` to `program_reconcile_summary()`**

In the reconcile_post handler, where `program_reconcile_summary(results)` is called, update to:

```python
program_summary = program_reconcile_summary(results, carrier_map=_carrier_map)
```

This enables per-carrier detail in the summary passed to the template.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/routes/reconcile.py
git commit -m "feat: pass carrier map to program reconcile summary"
```

---

### Task 9: Email Template Tokens

**Files:**
- Modify: `src/policydb/email_templates.py`

- [ ] **Step 1: Find the `policy_context()` function and `CONTEXT_TOKENS` dict**

Read `src/policydb/email_templates.py` to find where policy tokens are defined.

- [ ] **Step 2: Add program carrier tokens**

In `policy_context()`, add:

```python
    # Program carrier info (from program_carriers table)
    if row.get("is_program"):
        carrier_rows = conn.execute(
            "SELECT carrier FROM program_carriers WHERE program_id = ? ORDER BY sort_order",
            (row["id"],),
        ).fetchall()
        ctx["program_carriers"] = ", ".join(r["carrier"] for r in carrier_rows)
        ctx["program_carrier_count"] = str(len(carrier_rows))
    else:
        ctx["program_carriers"] = ""
        ctx["program_carrier_count"] = ""
```

In `CONTEXT_TOKENS`, under the `"policy"` key, add:

```python
    ("program_carriers", "Program Carriers"),
    ("program_carrier_count", "Program Carrier Count"),
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/email_templates.py
git commit -m "feat: add program carrier tokens to email template system"
```

---

### Task 10: Cleanup — Remove Deprecated Text Field References

**Files:**
- Modify: `src/policydb/web/templates/policies/new.html`
- Modify: `src/policydb/web/templates/reconcile/_create_form.html`
- Modify: `src/policydb/exporter.py`

- [ ] **Step 1: Update `new.html` — remove textarea for program_carriers**

In `src/policydb/web/templates/policies/new.html`, find the program_carriers textarea and program_carrier_count input. Remove them. Programs created via the new policy form will get carrier rows added after creation on the edit page (the matrix is only available after the policy exists and has an ID).

Add a note in the program section: `<p class="text-xs text-gray-400">Add carrier detail after creating the program.</p>`

- [ ] **Step 2: Update `_create_form.html` — remove textarea for program_carriers**

In `src/policydb/web/templates/reconcile/_create_form.html`, find and remove the program_carriers textarea and program_carrier_count input. The single-create from reconcile will insert carrier data directly when `is_program=1`.

- [ ] **Step 3: Verify exporter**

Check `src/policydb/exporter.py` — if it reads `program_carriers` directly (not via the view), update it to query the table. If it reads via `v_policy_status` (which was already updated in Task 2), no changes needed.

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/policies/new.html src/policydb/web/templates/reconcile/_create_form.html src/policydb/exporter.py
git commit -m "chore: remove deprecated program_carriers text field references"
```

---

### Task 11: Final Integration Test

**Files:**
- Test: manual

- [ ] **Step 1: Full workflow test**

Run: `policydb serve`

1. **Create program from scratch:** `/policies/new` → create a program policy → go to edit page → add carriers via matrix → verify totals update
2. **Create program from reconcile:** Upload CSV → batch create review → select rows → create program → verify carrier rows populated
3. **Edit carriers:** Click cells, edit values, verify saves and green flash
4. **Reorder carriers:** Drag rows, verify order persists on reload
5. **Delete carrier:** Click ✕, verify row removed and totals update
6. **Client detail:** Navigate to client with program → verify nested carrier rows display
7. **Reconcile existing program:** Upload new CSV with updated premiums for the same program → verify per-carrier matching works, DIFF rows show accept/keep

- [ ] **Step 2: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for program carriers"
```
