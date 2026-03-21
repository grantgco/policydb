# Database Health & Management — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Automatic backups on server start, startup health checks (WAL checkpoint, integrity, FK validation), export-then-purge for archived records, DB health card on Settings page, SQL console with pre-seeded examples, schema reference, downloadable DB.

---

## Problem Statement

PolicyDB's database grows over time with archived records never cleaned up, no automatic backups, no visibility into DB health from the web UI, and no way to run ad-hoc queries without external tools. The CLI has backup and stats commands, but the primary interface is the web UI.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Backup trigger | On every server start | No scheduler needed. Restarts happen frequently during development. |
| Backup retention | Keep last 30 (configurable) | Prevents disk bloat while maintaining history |
| Backup skip | If one exists within last hour | Prevents rapid-restart spam |
| Archive cleanup | Manual export-then-purge from Settings | User controls when. Export created before any deletes. |
| Health visibility | Settings page card | Already where system config lives. No extra nav. |
| Startup checks | WAL checkpoint, integrity, FK check | Lightweight, catches issues early |
| SQL console | Read-only by default, write toggle | Safety first, power when needed |

---

## 1. Startup Health Checks

### Added to `init_db()` in `src/policydb/db.py`, after migrations and existing health fixes:

**Order of operations on every server start:**
1. Run pending migrations (existing)
2. Data hygiene fixes (existing — cn_number, address backfill)
3. Create views (existing)
4. Generate mandated activities (existing)
5. **NEW: WAL checkpoint** — `PRAGMA wal_checkpoint(TRUNCATE)` — flushes WAL to main file
6. **NEW: Integrity check** — `PRAGMA integrity_check` — verifies DB not corrupted
7. **NEW: FK check** — `PRAGMA foreign_key_check` — finds orphaned records
8. **NEW: Auto-backup** — create timestamped backup with verification and pruning

### Implementation

```python
# After existing init_db work...

# WAL checkpoint — flush WAL to main DB file
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

# Integrity check
_integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
if _integrity != "ok":
    print(f"[WARNING] Database integrity check failed: {_integrity}")

# Foreign key check
_fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
if _fk_violations:
    print(f"[WARNING] {len(_fk_violations)} foreign key violation(s) found")

# Auto-backup with pruning
_auto_backup(db_path, max_backups=cfg.get("backup_retention_count", 30))
```

### Auto-backup logic

```python
def _auto_backup(db_path, max_backups=30):
    """Create timestamped backup, verify it, prune old backups."""
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    # Skip if a backup was created in the last hour
    existing = sorted(backup_dir.glob("policydb_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    if existing:
        age_seconds = time.time() - existing[0].stat().st_mtime
        if age_seconds < 3600:
            return  # Recent backup exists

    # Create backup
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = backup_dir / f"policydb_{timestamp}.sqlite"
    shutil.copy2(db_path, backup_path)

    # Verify backup
    try:
        verify_conn = sqlite3.connect(str(backup_path))
        result = verify_conn.execute("PRAGMA integrity_check").fetchone()[0]
        verify_conn.close()
        if result != "ok":
            print(f"[WARNING] Backup verification failed: {result}")
    except Exception as e:
        print(f"[WARNING] Backup verification error: {e}")

    # Prune old backups
    all_backups = sorted(backup_dir.glob("policydb_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in all_backups[max_backups:]:
        old.unlink()
```

### Config

```python
"backup_retention_count": 30,
```

### Stored results for Settings display

Store the last startup check results in a module-level dict so the Settings page can display them:

```python
_HEALTH_STATUS = {
    "integrity": "ok",
    "fk_violations": 0,
    "last_backup": None,  # datetime or path
    "backup_count": 0,
    "wal_size": 0,
}
```

Updated during `init_db()` and accessible via import.

---

## 2. Settings Page — Database Health Card

### New section on `/settings`

**Endpoint:** The existing settings GET handler loads health data and passes to template.

**Data needed:**
```python
import os
from policydb.db import DB_PATH, _HEALTH_STATUS

db_size = os.path.getsize(DB_PATH)
wal_path = str(DB_PATH) + "-wal"
wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0

backup_dir = DB_PATH.parent / "backups"
backups = sorted(backup_dir.glob("policydb_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.exists() else []

counts = {
    "clients": conn.execute("SELECT COUNT(*) FROM clients WHERE archived=0").fetchone()[0],
    "clients_archived": conn.execute("SELECT COUNT(*) FROM clients WHERE archived=1").fetchone()[0],
    "policies": conn.execute("SELECT COUNT(*) FROM policies WHERE archived=0").fetchone()[0],
    "policies_archived": conn.execute("SELECT COUNT(*) FROM policies WHERE archived=1").fetchone()[0],
    "activities": conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
    "contacts": conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
    "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
}

max_migration = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
```

**Template:** New card `_db_health.html` included on settings page.

**Actions:**
- **Backup Now** — `POST /settings/db/backup` — runs `_auto_backup()` immediately, returns toast
- **Purge Archived** — `POST /settings/db/purge` — preview + confirm + export + delete (see section 3)
- **VACUUM** — `POST /settings/db/vacuum` — runs `VACUUM`, returns before/after size
- **Download DB** — `GET /settings/db/download` — checkpoints WAL, serves `.sqlite` file

---

## 3. Export-Then-Purge for Archived Records

### Flow

**Step 1: Preview** — `GET /settings/db/purge-preview`
- Returns JSON with counts: `{policies: 3, clients: 0, activities: 12}`
- Shows what would be exported and deleted

**Step 2: Confirm + Execute** — `POST /settings/db/purge`
1. Export archived records to `~/.policydb/exports/archive_YYYYMMDD_HHMMSS.xlsx`
   - Sheet 1: Archived policies (all fields)
   - Sheet 2: Archived clients (all fields)
   - Sheet 3: Activities linked to archived records
2. Delete in transaction:
   - `DELETE FROM mandated_activity_log WHERE policy_uid IN (SELECT policy_uid FROM policies WHERE archived=1)`
   - `DELETE FROM policy_milestones WHERE policy_id IN (SELECT id FROM policies WHERE archived=1)`
   - `DELETE FROM contact_policy_assignments WHERE policy_id IN (SELECT id FROM policies WHERE archived=1)`
   - `DELETE FROM program_carriers WHERE program_id IN (SELECT id FROM policies WHERE archived=1)`
   - `DELETE FROM activity_log WHERE policy_id IN (SELECT id FROM policies WHERE archived=1)`
   - `DELETE FROM policies WHERE archived=1`
   - For archived clients: same cascade pattern
   - `DELETE FROM clients WHERE archived=1`
3. Run `VACUUM` after purge to reclaim space
4. Return confirmation with export path and counts

**Safety:**
- Export created BEFORE any deletes
- Full transaction — rolls back on any error
- VACUUM after to reclaim space immediately

---

## 4. SQL Console

### Location

New collapsible section on Settings page, below the DB Health card.

### Endpoints

**`POST /settings/db/query`**
- Body: `{"sql": "SELECT ...", "write_mode": false}`
- If `write_mode` is false: reject any query not starting with SELECT, PRAGMA, EXPLAIN, or WITH
- If `write_mode` is true: execute any SQL (INSERT, UPDATE, DELETE allowed)
- Returns: `{"ok": true, "columns": [...], "rows": [...], "row_count": N, "duration_ms": N}`
- Error: `{"ok": false, "error": "..."}`
- Limit results to 1000 rows

**`GET /settings/db/query/export?sql=...`**
- Runs the query, returns results as CSV download

### Pre-seeded examples

```python
_SQL_EXAMPLES = [
    {"label": "Policies expiring in 30 days", "sql": "SELECT policy_uid, c.name, policy_type, carrier, expiration_date FROM policies p JOIN clients c ON p.client_id = c.id WHERE p.archived = 0 AND p.expiration_date BETWEEN date('now') AND date('now', '+30 days') ORDER BY expiration_date"},
    {"label": "Clients with no activity in 90 days", "sql": "SELECT c.name, MAX(a.activity_date) AS last_activity FROM clients c LEFT JOIN activity_log a ON a.client_id = c.id WHERE c.archived = 0 GROUP BY c.id HAVING last_activity < date('now', '-90 days') OR last_activity IS NULL ORDER BY last_activity"},
    {"label": "Duplicate contacts by email", "sql": "SELECT email, GROUP_CONCAT(name, ', ') AS names, COUNT(*) AS cnt FROM contacts WHERE email IS NOT NULL AND email != '' GROUP BY LOWER(email) HAVING cnt > 1"},
    {"label": "Orphaned records (FK violations)", "sql": "PRAGMA foreign_key_check"},
    {"label": "Premium by carrier", "sql": "SELECT carrier, COUNT(*) AS policies, SUM(premium) AS total_premium FROM policies WHERE archived = 0 AND carrier IS NOT NULL GROUP BY carrier ORDER BY total_premium DESC"},
    {"label": "Activity hours by client (30 days)", "sql": "SELECT c.name, SUM(a.duration_hours) AS hours, COUNT(*) AS activities FROM activity_log a JOIN clients c ON a.client_id = c.id WHERE a.activity_date >= date('now', '-30 days') GROUP BY c.id ORDER BY hours DESC"},
    {"label": "All archived records", "sql": "SELECT 'Policy' AS type, policy_uid AS id, policy_type AS name FROM policies WHERE archived = 1 UNION ALL SELECT 'Client', CAST(id AS TEXT), name FROM clients WHERE archived = 1"},
    {"label": "Coverage types in use", "sql": "SELECT policy_type, COUNT(*) AS cnt FROM policies WHERE archived = 0 GROUP BY policy_type ORDER BY cnt DESC"},
    {"label": "Thread summary", "sql": "SELECT thread_id, COUNT(*) AS attempts, MIN(subject) AS subject, GROUP_CONCAT(disposition, ' → ') AS dispositions FROM activity_log WHERE thread_id IS NOT NULL GROUP BY thread_id ORDER BY MAX(activity_date) DESC LIMIT 20"},
    {"label": "Database size by table", "sql": "SELECT name, (SELECT COUNT(*) FROM pragma_table_info(name)) AS columns FROM sqlite_master WHERE type='table' ORDER BY name"},
]
```

### UI

```html
<details class="card mb-4">
  <summary>SQL Console</summary>
  <div class="p-4">
    <div class="flex items-center gap-3 mb-2">
      <select onchange="loadExample(this.value)">
        <option value="">— Examples —</option>
        {% for ex in sql_examples %}
        <option value="{{ ex.sql }}">{{ ex.label }}</option>
        {% endfor %}
      </select>
      <label class="toggle-switch">
        <input type="checkbox" id="write-mode">
        <span class="toggle-track"></span>
        <span class="text-xs text-gray-600 ml-2">Enable Write</span>
      </label>
    </div>
    <textarea id="sql-input" rows="4" class="w-full font-mono text-sm border rounded p-2"></textarea>
    <div class="flex gap-2 mt-2">
      <button onclick="runQuery()" class="bg-marsh text-white text-sm px-4 py-1.5 rounded">Run Query</button>
      <button onclick="exportQueryCSV()" class="text-sm text-gray-500 border rounded px-3 py-1.5">Export CSV</button>
      <span id="query-status" class="text-xs text-gray-400 ml-auto"></span>
    </div>
    <div id="query-results" class="mt-3 overflow-x-auto"></div>
  </div>
</details>
```

---

## 5. Schema Reference

### Location

Collapsible section on Settings page, below SQL Console.

### Endpoint

**`GET /settings/db/schema?table=clients`**
- Returns JSON: `{"columns": [...], "indexes": [...], "row_count": N}`
- Each column: `{"name": "...", "type": "...", "nullable": bool, "default": "..."}`
- Indexes from `PRAGMA index_list()` + `PRAGMA index_info()`

### UI

```html
<details class="card mb-4">
  <summary>Schema Reference</summary>
  <div class="p-4">
    <select onchange="loadSchema(this.value)">
      <option value="">— Select table —</option>
      {% for t in db_tables %}
      <option>{{ t }}</option>
      {% endfor %}
    </select>
    <div id="schema-results" class="mt-3"></div>
  </div>
</details>
```

Table dropdown populated from `SELECT name FROM sqlite_master WHERE type='table' ORDER BY name`. Selection loads PRAGMA info and renders as a table.

**Always current:** Schema reference reads live from the database via PRAGMA, not from static docs. Any migration that adds/modifies columns is immediately reflected.

---

## 6. Download DB

### Endpoint

**`GET /settings/db/download`**
- Checkpoints WAL: `PRAGMA wal_checkpoint(TRUNCATE)`
- Serves the `.sqlite` file as a download
- Filename: `policydb_YYYYMMDD_HHMM.sqlite`
- Content-Type: `application/octet-stream`

---

## 7. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Integrity check fails | Warning printed to console + shown on health card in red. Server still starts. |
| FK violations found | Warning with count. Doesn't block startup. User can investigate via SQL console. |
| Backup dir doesn't exist | Created automatically by `_auto_backup()` |
| Backup verification fails | Warning printed. Backup file kept but marked as unverified. |
| Purge with 0 archived records | Button shows "(0 policies, 0 clients)" — disabled or shows "Nothing to purge" |
| VACUUM on large DB | May take a few seconds. Returns before/after size. |
| SQL console write mode — DROP TABLE | Allowed when write mode enabled. User's responsibility. Confirmation dialog on destructive keywords. |
| SQL console timeout | 10 second query timeout. Returns error if exceeded. |
| Download while server is writing | WAL checkpoint ensures consistent snapshot. |
| Multiple rapid restarts | Backup skipped if one exists within last hour. |
