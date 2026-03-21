# Backup Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate three backup mechanisms into a unified two-tier system: migration backups (pre-schema-change, WAL checkpointed, verified, pruned to last 10) and startup backups (every server start, no throttle, verified, pruned to last 30).

**Architecture:** Refactor `_backup_db()` and `_auto_backup()` in `db.py` to implement the two-tier model. Remove throttle. Add WAL checkpoint and integrity verification to migration backups. Refactor CLI `db backup` to delegate to `_auto_backup()`. Update health card to show both tiers.

**Tech Stack:** Python, SQLite (WAL mode, PRAGMA integrity_check, PRAGMA wal_checkpoint), shutil, Jinja2, FastAPI

**Spec:** `docs/superpowers/specs/2026-03-21-backup-consolidation-design.md`

---

### Task 1: Add config key `migration_backup_retention_count`

**Files:**
- Modify: `src/policydb/config.py:314` (after `backup_retention_count`)

- [ ] **Step 1: Add the config default**

In `src/policydb/config.py`, find line 314 (`"backup_retention_count": 30,`) and add the new key after it:

```python
    "backup_retention_count": 30,
    "migration_backup_retention_count": 10,
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/config.py
git commit -m "feat: add migration_backup_retention_count config key (default 10)"
```

---

### Task 2: Add migration-tier fields to `_HEALTH_STATUS`

**Files:**
- Modify: `src/policydb/db.py:18-26`

- [ ] **Step 1: Add the new fields**

Replace the `_HEALTH_STATUS` dict at lines 18-26 with:

```python
_HEALTH_STATUS: dict = {
    "integrity": "ok",
    "fk_violations": 0,
    "last_backup": None,
    "last_backup_verified": False,
    "backup_count": 0,
    "migration_last_backup": None,
    "migration_last_backup_verified": False,
    "migration_backup_count": 0,
    "db_size": 0,
    "wal_size": 0,
}
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/db.py
git commit -m "feat: add migration backup fields to _HEALTH_STATUS"
```

---

### Task 3: Refactor `_backup_db()` — migration tier

**Files:**
- Modify: `src/policydb/db.py:55-63`

The current function is a simple `shutil.copy2` to the root directory. Replace it with the full migration-tier implementation.

- [ ] **Step 1: Rewrite `_backup_db()`**

Replace lines 55-63 with:

```python
def _backup_db(conn: sqlite3.Connection, db_path: Path) -> None:
    """Create a verified pre-migration backup in ~/.policydb/backups/migrations/.

    Checkpoints WAL on the existing connection, copies the database,
    verifies integrity, and prunes old migration backups.
    Never raises — backup failure must not block migrations or startup.
    """
    import shutil
    import datetime

    if not db_path.exists():
        return

    try:
        # Checkpoint WAL on the already-open connection before copying
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:
        import sys
        print(f"[WARNING] Pre-migration WAL checkpoint failed: {e}", file=sys.stderr)

    migration_dir = db_path.parent / "backups" / "migrations"
    try:
        migration_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        import sys
        print(f"[WARNING] Cannot create migration backup dir: {e}", file=sys.stderr)
        return

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = migration_dir / f"policydb_{ts}_pre_migration.sqlite"

    try:
        shutil.copy2(db_path, backup_path)
    except Exception as e:
        import sys
        print(f"[WARNING] Migration backup copy failed: {e}", file=sys.stderr)
        # Clean up partial file
        try:
            backup_path.unlink(missing_ok=True)
        except Exception:
            pass
        return

    # Verify integrity
    verified = False
    try:
        bconn = sqlite3.connect(str(backup_path))
        result = bconn.execute("PRAGMA integrity_check").fetchone()
        verified = result is not None and result[0] == "ok"
        bconn.close()
    except Exception:
        verified = False

    _HEALTH_STATUS["migration_last_backup"] = str(backup_path)
    _HEALTH_STATUS["migration_last_backup_verified"] = verified

    # Prune old migration backups
    from policydb import config as _cfg
    max_migration_backups = _cfg.get("migration_backup_retention_count", 10)
    existing = sorted(
        migration_dir.glob("policydb_*_pre_migration.sqlite"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    _HEALTH_STATUS["migration_backup_count"] = len(existing)
    for old_backup in existing[max_migration_backups:]:
        try:
            old_backup.unlink()
        except Exception:
            pass
```

- [ ] **Step 2: Update the call site in `init_db()`**

Search for `_backup_db(db_path)` in the `init_db()` function (near the `_KNOWN_MIGRATIONS` check) and change it to `_backup_db(conn, db_path)`:

```python
    if _KNOWN_MIGRATIONS - applied:
        _backup_db(conn, db_path)
```

Note: Line numbers shift after Tasks 2 and 3 modify earlier code. Search by pattern, not line number.

- [ ] **Step 3: Verify server starts**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.db import init_db; init_db()"`
Expected: No errors. If migrations are pending, a backup should appear in `~/.policydb/backups/migrations/`.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/db.py
git commit -m "feat: refactor _backup_db() for migration tier with WAL checkpoint, verification, pruning"
```

---

### Task 4: Refactor `_auto_backup()` — remove throttle, add error handling

**Files:**
- Modify: `src/policydb/db.py:66-123` (the `_auto_backup` function)

- [ ] **Step 1: Rewrite `_auto_backup()`**

Replace lines 66-123 with:

```python
def _auto_backup(db_path: Path, max_backups: int = 30) -> None:
    """Create a verified backup in ~/.policydb/backups/, pruning old ones.

    Runs on every server start — no throttle. When called from the web UI
    (server is running, concurrent writers possible), checkpoints WAL first.
    Never raises — backup failure must not block server startup.
    """
    import shutil
    import datetime

    if not db_path.exists():
        return

    backup_dir = db_path.parent / "backups"
    try:
        backup_dir.mkdir(exist_ok=True)
    except Exception as e:
        import sys
        print(f"[WARNING] Cannot create backup dir: {e}", file=sys.stderr)
        return

    # Checkpoint WAL before copying to ensure consistency.
    # At startup this is redundant (closing the last connection triggers a passive checkpoint) but harmless.
    try:
        ckpt_conn = sqlite3.connect(str(db_path))
        ckpt_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ckpt_conn.close()
    except Exception as e:
        import sys
        print(f"[WARNING] Backup WAL checkpoint failed: {e}", file=sys.stderr)

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = backup_dir / f"policydb_{ts}.sqlite"

    try:
        shutil.copy2(db_path, backup_path)
    except Exception as e:
        import sys
        print(f"[WARNING] Backup copy failed: {e}", file=sys.stderr)
        try:
            backup_path.unlink(missing_ok=True)
        except Exception:
            pass
        return

    # Verify backup integrity
    verified = False
    try:
        bconn = sqlite3.connect(str(backup_path))
        result = bconn.execute("PRAGMA integrity_check").fetchone()
        verified = result is not None and result[0] == "ok"
        bconn.close()
    except Exception:
        verified = False

    _HEALTH_STATUS["last_backup"] = str(backup_path)
    _HEALTH_STATUS["last_backup_verified"] = verified

    # Refresh the list after adding the new backup
    existing = sorted(
        backup_dir.glob("policydb_*.sqlite"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    _HEALTH_STATUS["backup_count"] = len(existing)

    # Prune backups beyond max_backups
    for old_backup in existing[max_backups:]:
        try:
            old_backup.unlink()
        except Exception:
            pass
```

- [ ] **Step 2: Verify the `init_db()` call site is compatible**

Search for `_auto_backup(db_path` in the `init_db()` function (near the end, after `conn.close()`). The existing call is:

```python
    # Auto-backup (runs after connection is closed so the file is fully flushed)
    from policydb import config as _cfg
    _auto_backup(db_path, max_backups=_cfg.get("backup_retention_count", 30))
```

This call does NOT pass `force=True` — it's already compatible with the new signature. No changes needed here.

- [ ] **Step 3: Update the web route call in `settings.py`**

In `src/policydb/web/routes/settings.py`, find the `db_backup_now()` function. Remove `force=True` from the `_auto_backup()` call and update the docstring:

```python
@router.post("/db/backup")
def db_backup_now():
    """Create a backup immediately."""
    from policydb.db import _auto_backup
    try:
        _auto_backup(DB_PATH, max_backups=cfg.get("backup_retention_count", 30))
```

- [ ] **Step 4: Verify server starts**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -c "from policydb.db import init_db; init_db()"`
Expected: No errors. A backup should appear in `~/.policydb/backups/` on every run (no throttle).

- [ ] **Step 5: Commit**

```bash
git add src/policydb/db.py src/policydb/web/routes/settings.py
git commit -m "feat: remove throttle from _auto_backup(), add WAL checkpoint and error handling"
```

---

### Task 5: Refactor CLI `policydb db backup`

**Files:**
- Modify: `src/policydb/cli.py:119-158`

- [ ] **Step 1: Rewrite the CLI command**

Replace lines 119-158 with:

```python
@db_group.command("backup")
@click.option("--dest-dir", default=None, hidden=True, help="DEPRECATED — ignored. Backups always go to ~/.policydb/backups/.")
@click.option("--keep", default=30, show_default=True, help="Number of backups to retain before pruning oldest.")
def db_backup(dest_dir, keep):
    """Back up the database to a timestamped file and prune old backups.

    Creates ~/.policydb/backups/policydb_YYYY-MM-DD_HHMMSS.sqlite.
    Run daily via launchd or cron; oldest copies beyond --keep are deleted.
    """
    if dest_dir is not None:
        click.echo("Warning: --dest-dir is deprecated and ignored. Backups are always written to ~/.policydb/backups/")

    src = get_db_path()
    if not src.exists():
        raise click.ClickException("No database found.")

    from policydb.db import _auto_backup, _HEALTH_STATUS
    _auto_backup(src, max_backups=keep)

    backup_path = _HEALTH_STATUS.get("last_backup", "")
    verified = _HEALTH_STATUS.get("last_backup_verified", False)
    count = _HEALTH_STATUS.get("backup_count", 0)

    if backup_path:
        click.echo(f"Backup saved: {backup_path}")
        click.echo(f"Integrity: {'Verified' if verified else 'UNVERIFIED'}")
        click.echo(f"Total backups: {count} (keeping {keep})")
    else:
        click.echo("Warning: Backup may have failed — check ~/.policydb/backups/")
```

- [ ] **Step 2: Verify the CLI command works**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m policydb db backup`
Expected: Creates a backup and prints path, verification status, and count.

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m policydb db backup --dest-dir /tmp`
Expected: Prints deprecation warning, still creates backup in `~/.policydb/backups/`.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/cli.py
git commit -m "refactor: CLI db backup delegates to _auto_backup(), deprecate --dest-dir"
```

---

### Task 6: Update health card to show both tiers

**Files:**
- Modify: `src/policydb/web/templates/settings/_db_health.html:49-67`
- Modify: `src/policydb/web/routes/settings.py:49-54` (backup listing)

- [ ] **Step 1: Add migration and legacy backup listings to settings route context**

In `src/policydb/web/routes/settings.py`, find the existing backup listing block (search for `backup_dir = DB_PATH.parent / "backups"`). Add the migration and legacy backup listings immediately after the existing `backups` variable:

```python
    migration_backup_dir = backup_dir / "migrations"
    migration_backups = (
        sorted(migration_backup_dir.glob("policydb_*_pre_migration.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if migration_backup_dir.exists()
        else []
    )
    # Legacy backups in root dir
    legacy_backups = sorted(DB_PATH.parent.glob("policydb.sqlite.backup_*"), key=lambda p: p.stat().st_mtime, reverse=True)
```

Then find the template context dict and add these keys after the `"backups": backups,` line:

```python
        "migration_backups": migration_backups,
        "legacy_backups": legacy_backups,
        "backup_retention_max": cfg.get("backup_retention_count", 30),
        "migration_backup_retention_max": cfg.get("migration_backup_retention_count", 10),
```

- [ ] **Step 2: Update the health card template**

In `_db_health.html`, find the "Last Backup" metric tile (search for `<p class="text-xs text-gray-500 mb-0.5">Last Backup</p>`). Replace that entire `<div class="bg-gray-50 rounded-lg p-3">` block with two tiles:

```html
    <div class="bg-gray-50 rounded-lg p-3">
      <p class="text-xs text-gray-500 mb-0.5">Startup Backups</p>
      {% if db_health.last_backup %}
      <p class="text-sm font-semibold text-gray-800">{{ db_health.backup_count }} of {{ backup_retention_max }}</p>
      <p class="text-xs {{ 'text-green-600' if db_health.last_backup_verified else 'text-amber-600' }}">
        Last: {{ 'Verified' if db_health.last_backup_verified else 'Unverified' }}
      </p>
      {% else %}
      <p class="text-sm font-semibold text-amber-600">None yet</p>
      {% endif %}
    </div>
    <div class="bg-gray-50 rounded-lg p-3">
      <p class="text-xs text-gray-500 mb-0.5">Migration Backups</p>
      {% if db_health.migration_last_backup %}
      <p class="text-sm font-semibold text-gray-800">{{ db_health.migration_backup_count }} of {{ migration_backup_retention_max }}</p>
      <p class="text-xs {{ 'text-green-600' if db_health.migration_last_backup_verified else 'text-amber-600' }}">
        Last: {{ 'Verified' if db_health.migration_last_backup_verified else 'Unverified' }}
      </p>
      {% else %}
      <p class="text-sm font-semibold text-gray-500">None</p>
      <p class="text-xs text-gray-400">Created before migrations</p>
      {% endif %}
    </div>
```

Note: This replaces one tile with two, so the grid becomes 5 columns. Change the grid class from `grid-cols-2 sm:grid-cols-4` to `grid-cols-2 sm:grid-cols-5` on the parent `<div>`, or keep 4-col and let the tiles wrap.

- [ ] **Step 3: Add legacy backup cleanup section**

In `_db_health.html`, find the purge confirmation panel (search for `id="db-purge-panel"`). After its closing `</div>`, but before the final `</div></details>` tags, add the legacy cleanup notice:

```html
  {% if legacy_backups %}
  <div class="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg flex items-center justify-between">
    <div>
      <p class="text-xs font-medium text-amber-700">{{ legacy_backups | length }} legacy backup{{ 's' if legacy_backups | length != 1 else '' }} in ~/.policydb/</p>
      <p class="text-xs text-amber-600">Old-format backups from before the backup consolidation. Safe to remove.</p>
    </div>
    <button type="button" onclick="dbCleanupLegacy()"
      class="text-xs bg-amber-100 text-amber-800 hover:bg-amber-200 border border-amber-300 px-3 py-1.5 rounded-lg font-medium transition-colors whitespace-nowrap ml-3">
      Clean Up
    </button>
  </div>
  {% endif %}
```

- [ ] **Step 4: Add the `dbCleanupLegacy()` JS function**

In the `<script>` block at the end of `_db_health.html`, add:

```javascript
function dbCleanupLegacy() {
  if (!confirm('Delete legacy backup files from ~/.policydb/?')) return;
  _dbSetStatus('Cleaning up…', 'text-gray-500');
  fetch('/settings/db/cleanup-legacy', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        _dbSetStatus(d.message, 'text-green-600');
        setTimeout(function() { location.reload(); }, 1500);
      } else {
        _dbSetStatus('Error: ' + d.error, 'text-red-600');
      }
    })
    .catch(function(e) { _dbSetStatus('Request failed.', 'text-red-600'); });
}
```

- [ ] **Step 5: Add the legacy cleanup route**

In `src/policydb/web/routes/settings.py`, after the `db_backup_now()` route, add:

```python
@router.post("/db/cleanup-legacy")
def db_cleanup_legacy():
    """Delete old-format backup files from ~/.policydb/ root."""
    try:
        legacy = sorted(DB_PATH.parent.glob("policydb.sqlite.backup_*"))
        count = 0
        for f in legacy:
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        return JSONResponse({"ok": True, "message": f"Removed {count} legacy backup(s)."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
```

- [ ] **Step 6: Verify the settings page renders and Backup Now works**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && policydb serve`
Navigate to `http://127.0.0.1:8000/settings` and verify:
1. The Database Health card shows both Startup Backups and Migration Backups tiles with "X of Y" counts
2. Click the "Backup Now" button — verify a backup is created with verified status
3. If legacy backup files exist in `~/.policydb/`, verify the amber cleanup banner appears

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/templates/settings/_db_health.html src/policydb/web/routes/settings.py
git commit -m "feat: health card shows both backup tiers + legacy cleanup action"
```

---

### Task 7: Update the "Schema Version" tile to show backup file counts from both tiers

**Files:**
- Modify: `src/policydb/web/templates/settings/_db_health.html`

The current Schema Version tile shows `{{ backups | length }} backup files` which only counts startup-tier backups.

- [ ] **Step 1: Update the Schema Version tile**

Search for `Schema Version` in `_db_health.html` and replace the entire tile `<div>` with:

```html
    <div class="bg-gray-50 rounded-lg p-3">
      <p class="text-xs text-gray-500 mb-0.5">Schema Version</p>
      <p class="text-sm font-semibold text-gray-800">Migration {{ max_migration or '—' }}</p>
      <p class="text-xs text-gray-400">{{ backups | length }} startup + {{ migration_backups | length }} migration files</p>
    </div>
```

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/settings/_db_health.html
git commit -m "fix: schema version tile shows both backup tier counts"
```
