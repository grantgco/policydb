# Database Health & Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic backups on server start, startup health checks, export-then-purge for archived records, DB health card on Settings page, SQL console, schema reference, and downloadable DB.

**Architecture:** Health checks and auto-backup run in `init_db()`. Results stored in module-level dict for Settings display. New endpoints on settings router for backup, purge, vacuum, download, query, and schema. New template partials for health card, SQL console, and schema reference.

**Tech Stack:** SQLite, FastAPI, Jinja2, openpyxl (for archive export)

**Spec:** `docs/superpowers/specs/2026-03-20-db-health-management-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/web/templates/settings/_db_health.html` | Health card partial |
| Create | `src/policydb/web/templates/settings/_sql_console.html` | SQL console partial |
| Create | `src/policydb/web/templates/settings/_schema_ref.html` | Schema reference partial |
| Modify | `src/policydb/db.py` | Auto-backup, health checks, _HEALTH_STATUS |
| Modify | `src/policydb/config.py` | backup_retention_count default |
| Modify | `src/policydb/web/routes/settings.py` | All new endpoints |
| Modify | `src/policydb/web/templates/settings.html` | Include new partials |

---

### Task 1: Startup Health Checks + Auto-Backup

**Files:**
- Modify: `src/policydb/db.py`
- Modify: `src/policydb/config.py`

- [ ] **Step 1: Add _HEALTH_STATUS dict and _auto_backup function to db.py**

Add at module level (near top, after imports):
```python
_HEALTH_STATUS = {
    "integrity": "ok",
    "fk_violations": 0,
    "last_backup": None,
    "last_backup_verified": False,
    "backup_count": 0,
    "db_size": 0,
    "wal_size": 0,
}
```

Add `_auto_backup(db_path, max_backups)` function that:
- Creates `~/.policydb/backups/` dir if needed
- Skips if a backup exists within last hour
- Copies DB to `policydb_YYYY-MM-DD_HHMMSS.sqlite`
- Verifies backup with `PRAGMA integrity_check`
- Prunes old backups beyond max_backups
- Updates `_HEALTH_STATUS`

- [ ] **Step 2: Add health checks to init_db()**

After the existing mandated activities generation, add:
```python
# WAL checkpoint
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

# Integrity check
_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
_HEALTH_STATUS["integrity"] = _integrity
if _integrity != "ok":
    print(f"[WARNING] DB integrity: {_integrity}")

# FK check
_fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
_HEALTH_STATUS["fk_violations"] = len(_fk_violations)
if _fk_violations:
    print(f"[WARNING] {len(_fk_violations)} FK violation(s)")

# DB size
_HEALTH_STATUS["db_size"] = os.path.getsize(db_path) if os.path.exists(db_path) else 0
_wal = str(db_path) + "-wal"
_HEALTH_STATUS["wal_size"] = os.path.getsize(_wal) if os.path.exists(_wal) else 0

# Auto-backup
from policydb import config as _cfg
_auto_backup(db_path, max_backups=_cfg.get("backup_retention_count", 30))
```

- [ ] **Step 3: Add config default**

In `config.py` `_DEFAULTS`, add:
```python
"backup_retention_count": 30,
```

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/ -v
git add src/policydb/db.py src/policydb/config.py
git commit -m "feat: startup health checks and auto-backup on server start"
```

---

### Task 2: Settings — DB Health Card

**Files:**
- Create: `src/policydb/web/templates/settings/_db_health.html`
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1: Add health data loading to settings GET handler**

In `settings.py`, in the settings GET handler, add:
```python
import os
from policydb.db import DB_PATH, _HEALTH_STATUS

db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
wal_path = str(DB_PATH) + "-wal"
wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0

backup_dir = DB_PATH.parent / "backups"
backups = sorted(backup_dir.glob("policydb_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.exists() else []

db_counts = {}
with get_db_conn() as conn:  # or however DB connection is obtained
    for table in ["clients", "policies", "activity_log", "contacts", "projects"]:
        db_counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    db_counts["clients_archived"] = conn.execute("SELECT COUNT(*) FROM clients WHERE archived=1").fetchone()[0]
    db_counts["policies_archived"] = conn.execute("SELECT COUNT(*) FROM policies WHERE archived=1").fetchone()[0]
    max_migration = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
```

Pass to template context: `db_health`, `db_counts`, `backups`, `max_migration`.

- [ ] **Step 2: Create _db_health.html partial**

Card showing: DB size, WAL size, integrity status, FK violations, record counts (with archived counts), last backup info, backup count, migration count. Action buttons: Backup Now, Purge Archived, VACUUM, Download DB.

- [ ] **Step 3: Add action endpoints**

In `settings.py`:

`POST /settings/db/backup` — runs `_auto_backup()` force (skip recency check), returns toast.

`POST /settings/db/vacuum` — runs `VACUUM`, returns before/after size.

`GET /settings/db/download` — checkpoints WAL, serves DB file as download.

`GET /settings/db/purge-preview` — returns JSON with archived counts.

`POST /settings/db/purge` — exports archived records to XLSX, then deletes them in transaction, then vacuums.

- [ ] **Step 4: Include in settings.html**

Add `{% include 'settings/_db_health.html' %}` at the top of the settings page.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: DB health card on Settings with backup, purge, vacuum, download actions"
```

---

### Task 3: SQL Console

**Files:**
- Create: `src/policydb/web/templates/settings/_sql_console.html`
- Modify: `src/policydb/web/routes/settings.py`

- [ ] **Step 1: Add query endpoint**

```python
@router.post("/db/query")
async def db_query(request: Request, conn=Depends(get_db)):
    import time
    body = await request.json()
    sql = body.get("sql", "").strip()
    write_mode = body.get("write_mode", False)

    if not sql:
        return JSONResponse({"ok": False, "error": "No query provided"})

    # Safety check
    if not write_mode:
        first_word = sql.split()[0].upper() if sql.split() else ""
        if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
            return JSONResponse({"ok": False, "error": "Read-only mode. Enable write mode for INSERT/UPDATE/DELETE."})

    try:
        start = time.time()
        cursor = conn.execute(sql)
        if cursor.description:
            columns = [d[0] for d in cursor.description]
            rows = [list(r) for r in cursor.fetchmany(1000)]
        else:
            columns = []
            rows = []
            conn.commit()
        duration = round((time.time() - start) * 1000, 1)
        return JSONResponse({"ok": True, "columns": columns, "rows": rows, "row_count": len(rows), "duration_ms": duration})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})
```

- [ ] **Step 2: Add CSV export endpoint**

```python
@router.get("/db/query/export")
def db_query_export(sql: str = "", conn=Depends(get_db)):
    # Read-only only
    first_word = sql.split()[0].upper() if sql.split() else ""
    if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
        return HTMLResponse("Read-only queries only for export", status_code=400)
    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description] if cursor.description else []
    rows = cursor.fetchall()
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for r in rows:
        writer.writerow(list(r))
    from starlette.responses import Response
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="query_results.csv"'})
```

- [ ] **Step 3: Create _sql_console.html partial**

Collapsible card with:
- Examples dropdown (pre-seeded queries)
- Read-only / Write mode toggle
- Textarea for SQL input
- Run Query + Export CSV buttons
- Results table (dynamically rendered from JSON response)
- Status line showing row count and duration

JS functions: `loadExample()`, `runQuery()`, `exportQueryCSV()`.

- [ ] **Step 4: Pass SQL examples to template context**

Add `sql_examples` list to the settings template context.

- [ ] **Step 5: Include in settings.html**

Add `{% include 'settings/_sql_console.html' %}` after the health card.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: SQL console on Settings with pre-seeded examples and CSV export"
```

---

### Task 4: Schema Reference

**Files:**
- Create: `src/policydb/web/templates/settings/_schema_ref.html`
- Modify: `src/policydb/web/routes/settings.py`

- [ ] **Step 1: Add schema endpoint**

```python
@router.get("/db/schema")
def db_schema(table: str = "", conn=Depends(get_db)):
    if not table:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        return JSONResponse({"tables": tables})
    columns = [{"name": r[1], "type": r[2], "nullable": not r[3], "default": r[4], "pk": bool(r[5])}
               for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    indexes = [{"name": r[1], "unique": bool(r[2])} for r in conn.execute(f"PRAGMA index_list({table})").fetchall()]
    row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return JSONResponse({"table": table, "columns": columns, "indexes": indexes, "row_count": row_count})
```

- [ ] **Step 2: Create _schema_ref.html partial**

Collapsible card with table dropdown. On selection, loads schema via fetch and renders column table + index list + row count.

- [ ] **Step 3: Pass table list to template context**

Add `db_tables` to settings context.

- [ ] **Step 4: Include in settings.html**

Add `{% include 'settings/_schema_ref.html' %}` after the SQL console.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: schema reference on Settings with live PRAGMA table info"
```

---

### Task 5: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`

- [ ] **Step 2: Manual test**

1. **Restart server** — verify backup created, health checks run, no warnings
2. **Settings page** — verify DB Health card shows size, counts, integrity ✓, last backup
3. **Backup Now** — click, verify toast, backup count increments
4. **VACUUM** — click, verify before/after size shown
5. **Download DB** — click, verify .sqlite file downloads
6. **Purge preview** — verify archived counts shown
7. **Purge** — if archived records exist, purge and verify export file created
8. **SQL Console** — run example query, verify results table renders
9. **CSV export** — export query results, verify CSV downloads
10. **Write mode** — toggle on, run UPDATE, verify it works
11. **Schema reference** — select a table, verify columns/indexes/count display

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for DB health management"
```
