# Backup Consolidation Design

**Date:** 2026-03-21
**Status:** Approved
**Supersedes:** Throttling decisions in `2026-03-20-db-health-management-design.md` (the "skip if one exists within last hour" policy is replaced by "always back up").

## Problem

The backup system has three separate mechanisms with inconsistent behavior:

1. **Pre-migration backup** (`_backup_db()`) — writes to root `~/.policydb/`, never pruned, no WAL checkpoint, no integrity verification
2. **Auto-backup** (`_auto_backup()`) — writes to `~/.policydb/backups/`, throttled to skip if one exists within 1 hour, verified, pruned to last 30
3. **CLI backup** (`policydb db backup`) — writes to `~/.policydb/backups/`, uses age-based pruning (days) instead of count-based, own copy logic

The user wants backups on every migration, every upgrade, and every fresh server start — no throttle. The system should be consolidated into two clearly separated tiers with consistent behavior.

## Design

### Two-Tier Backup Architecture

#### Tier 1: Migration Backups (`~/.policydb/backups/migrations/`)

**Purpose:** Safety net before schema changes. Higher retention value — these capture the last known-good state before a migration touched the database.

**Trigger:** Only when `_KNOWN_MIGRATIONS - applied` is non-empty (pending migrations exist).

**Behavior:**
- Checkpoint WAL on the already-open `conn` from `init_db()`: `conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")`
- Copy database to `~/.policydb/backups/migrations/policydb_YYYY-MM-DD_HHMMSS_pre_migration.sqlite`
- Create `migrations/` subdirectory if it doesn't exist
- Run `PRAGMA integrity_check` on the backup copy via a temporary read-only connection
- Prune to last N copies, oldest first (default 10, configurable via `migration_backup_retention_count`)
- Update `_HEALTH_STATUS` with migration-specific backup metadata
- All operations wrapped in try/except — backup failure never blocks migrations or server startup
- On copy failure, clean up any partial file before continuing

**Config key:** `migration_backup_retention_count` (default: 10)

**WAL checkpoint note:** The checkpoint runs on the existing `conn` (already opened by `init_db()`) before any migration SQL executes. Do NOT open a temporary connection for the checkpoint — a second connection may fail to checkpoint if the primary connection holds a lock.

#### Tier 2: Startup Backups (`~/.policydb/backups/`)

**Purpose:** Routine snapshots on every server start. Provides a rolling window of recent database states.

**Trigger:** Every `init_db()` call — no throttle, no skip.

**Behavior:**
- Runs at end of `init_db()` after `conn.close()` and after the WAL checkpoint has already been performed
- Copy database to `~/.policydb/backups/policydb_YYYY-MM-DD_HHMMSS.sqlite` (naming unchanged)
- Run `PRAGMA integrity_check` on the backup copy via a temporary read-only connection
- Prune to last N copies, oldest first (default 30, configurable via `backup_retention_count` — already exists)
- Update `_HEALTH_STATUS` with backup metadata
- All operations wrapped in try/except — backup failure never prevents server startup
- On copy failure, clean up any partial file before continuing

**Concurrency note:** At startup, this runs after `conn.close()` and before the FastAPI server begins accepting requests, so there are no concurrent writers. The "Backup Now" button on the Settings page also calls `_auto_backup()` — since the server is running and may be handling requests, the function must checkpoint WAL before copying to ensure consistency: open a temporary connection, run `PRAGMA wal_checkpoint(TRUNCATE)`, close it, then `shutil.copy2()`.

**Config key:** `backup_retention_count` (default: 30, already exists)

#### CLI Command: `policydb db backup`

**Refactored** to delegate to `_auto_backup()` instead of maintaining its own copy/prune logic.

- Calls `_auto_backup(db_path, max_backups=keep)`
- `--keep` flag maps to `max_backups` parameter (default 30)
- `--dest-dir` flag deprecated — accepted but ignored with a printed warning: "Warning: --dest-dir is deprecated and ignored. Backups are always written to ~/.policydb/backups/"
- Retains its own CLI output messaging (echo backup path, echo prune count)

### Changes from Current System

| Change | Before | After |
|--------|--------|-------|
| Startup backup throttle | Skip if backup exists within 1 hour | No throttle — always back up |
| Migration backup location | `~/.policydb/policydb.sqlite.backup_*` | `~/.policydb/backups/migrations/policydb_*_pre_migration.sqlite` |
| Migration backup WAL handling | No WAL checkpoint before copy | `PRAGMA wal_checkpoint(TRUNCATE)` on existing conn before copy |
| Migration backup verification | None | `PRAGMA integrity_check` on copy |
| Migration backup pruning | Never pruned | Last 10 (configurable) |
| CLI backup logic | Own shutil.copy2 + age-based pruning | Delegates to `_auto_backup()` with count-based pruning |
| CLI `--dest-dir` flag | Supported | Deprecated with warning (accepted but ignored) |
| Config keys | `backup_retention_count` | + `migration_backup_retention_count` |
| Error handling | Exceptions propagate, can crash startup | All backup ops wrapped in try/except, partial files cleaned up, never block startup |
| Naming convention | Mixed (`YYYYMMDD` vs `YYYY-MM-DD`) | Standardized to `YYYY-MM-DD_HHMMSS` for both tiers |

### Breaking Changes

- **`--dest-dir` on `policydb db backup`**: Deprecated. The flag is still accepted but ignored with a warning. Any external automation (launchd, cron) using `--dest-dir` should be updated to remove it.

### Error Handling

All backup operations (both tiers) follow these rules:

1. **Never block startup or migrations.** All backup code is wrapped in try/except. If the backup fails, log a warning to stderr and continue.
2. **Clean up partial files.** If `shutil.copy2()` fails mid-copy (disk full, permissions), delete the partial file in the except block.
3. **Integrity check failure is non-fatal.** If `PRAGMA integrity_check` fails on the copy, set `_HEALTH_STATUS["last_backup_verified"] = False` but keep the backup file — a potentially-corrupt backup is better than no backup.
4. **Directory creation failure.** If `mkdir` fails for `backups/` or `backups/migrations/`, log warning and skip backup.

### Health Status Updates

`_HEALTH_STATUS` dict gains migration-specific fields:

```python
_HEALTH_STATUS: dict = {
    # ... existing fields ...
    "migration_last_backup": None,        # path to most recent migration backup
    "migration_last_backup_verified": False,
    "migration_backup_count": 0,
}
```

### Health Card UI

The Settings health card (`_db_health.html`) "Last Backup" section becomes two rows:

- **Startup backup:** filename, count (e.g., "28 of 30"), verified status
- **Migration backup:** filename, count (e.g., "6 of 10"), verified status

If legacy backup files exist in `~/.policydb/policydb.sqlite.backup_*`, show a count with a "Clean up legacy backups" link that deletes them. This is a one-time cleanup action.

### Pruning Scope

Each tier's pruning glob is scoped to its own directory:

- Startup tier: `~/.policydb/backups/policydb_*.sqlite` — only matches startup backups
- Migration tier: `~/.policydb/backups/migrations/policydb_*_pre_migration.sqlite` — only matches migration backups

The directory separation prevents cross-tier pruning. The `_pre_migration` suffix is not used as a filter criterion — directory scoping is the sole mechanism.

### Legacy File Handling

Existing root-level backup files (`~/.policydb/policydb.sqlite.backup_*`) are left in place. No automated migration of old files. New backups go to the new locations only. The health card shows a count with a one-time cleanup action.

### File Layout After Changes

```
~/.policydb/
├── policydb.sqlite                          ← Main database
├── policydb.sqlite-wal                      ← Write-ahead log
├── policydb.sqlite-shm                      ← Shared memory
├── policydb.sqlite.backup_*                 ← Legacy pre-migration backups (left in place)
├── config.yaml                              ← Config overrides
├── backups/
│   ├── policydb_YYYY-MM-DD_HHMMSS.sqlite    ← Startup tier (last 30)
│   └── migrations/
│       └── policydb_YYYY-MM-DD_HHMMSS_pre_migration.sqlite  ← Migration tier (last 10)
└── exports/                                 ← CSV/Excel exports
```

### Config Defaults

```yaml
backup_retention_count: 30              # Startup tier — already exists
migration_backup_retention_count: 10    # Migration tier — new
```

### Affected Files

| File | Change |
|------|--------|
| `src/policydb/db.py` | Refactor `_backup_db()` (WAL checkpoint on conn, new location, verification, pruning, error handling), remove throttle from `_auto_backup()` (add WAL checkpoint for web-triggered calls, error handling), update `_HEALTH_STATUS` |
| `src/policydb/config.py` | Add `migration_backup_retention_count: 10` to `_DEFAULTS` |
| `src/policydb/cli.py` | Refactor `db backup` command to delegate to `_auto_backup()`, deprecate `--dest-dir` with warning |
| `src/policydb/web/templates/settings/_db_health.html` | Show both tiers in health card, add legacy cleanup action |
