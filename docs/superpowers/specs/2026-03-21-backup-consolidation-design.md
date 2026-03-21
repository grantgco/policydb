# Backup Consolidation Design

**Date:** 2026-03-21
**Status:** Approved

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
- Checkpoint WAL via temporary connection: `PRAGMA wal_checkpoint(TRUNCATE)`
- Copy database to `~/.policydb/backups/migrations/policydb_YYYYMMDD_HHMMSS_pre_migration.sqlite`
- Create `migrations/` subdirectory if it doesn't exist
- Run `PRAGMA integrity_check` on the backup copy
- Prune to last N copies, oldest first (default 10, configurable via `migration_backup_retention_count`)
- Update `_HEALTH_STATUS` with migration-specific backup metadata

**Config key:** `migration_backup_retention_count` (default: 10)

#### Tier 2: Startup Backups (`~/.policydb/backups/`)

**Purpose:** Routine snapshots on every server start. Provides a rolling window of recent database states.

**Trigger:** Every `init_db()` call — no throttle, no skip.

**Behavior:**
- Runs at end of `init_db()` after WAL checkpoint has already been performed
- Copy database to `~/.policydb/backups/policydb_YYYY-MM-DD_HHMMSS.sqlite` (naming unchanged)
- Run `PRAGMA integrity_check` on the backup copy
- Prune to last N copies, oldest first (default 30, configurable via `backup_retention_count` — already exists)
- Update `_HEALTH_STATUS` with backup metadata

**Config key:** `backup_retention_count` (default: 30, already exists)

#### CLI Command: `policydb db backup`

**Refactored** to delegate to `_auto_backup()` instead of maintaining its own copy/prune logic.

- Calls `_auto_backup(db_path, max_backups=keep, force=True)`
- `--keep` flag maps to `max_backups` parameter (default 30)
- `--dest-dir` flag removed — always writes to `~/.policydb/backups/`
- Retains its own CLI output messaging (echo backup path, echo prune count)

### Changes from Current System

| Change | Before | After |
|--------|--------|-------|
| Startup backup throttle | Skip if backup exists within 1 hour | No throttle — always back up |
| Migration backup location | `~/.policydb/policydb.sqlite.backup_*` | `~/.policydb/backups/migrations/policydb_*_pre_migration.sqlite` |
| Migration backup WAL handling | No WAL checkpoint before copy | `PRAGMA wal_checkpoint(TRUNCATE)` before copy |
| Migration backup verification | None | `PRAGMA integrity_check` on copy |
| Migration backup pruning | Never pruned | Last 10 (configurable) |
| CLI backup logic | Own shutil.copy2 + age-based pruning | Delegates to `_auto_backup()` with count-based pruning |
| CLI `--dest-dir` flag | Supported | Removed |
| Config keys | `backup_retention_count` | + `migration_backup_retention_count` |

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

The Settings health card (`_db_health.html`) should display both tiers: startup backup count/status and migration backup count/status.

### Legacy File Handling

Existing root-level backup files (`~/.policydb/policydb.sqlite.backup_*`) are left in place. No automated migration of old files. New backups go to the new locations only.

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
│       └── policydb_YYYYMMDD_HHMMSS_pre_migration.sqlite  ← Migration tier (last 10)
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
| `src/policydb/db.py` | Refactor `_backup_db()`, remove throttle from `_auto_backup()`, update `_HEALTH_STATUS` |
| `src/policydb/config.py` | Add `migration_backup_retention_count: 10` to `_DEFAULTS` |
| `src/policydb/cli.py` | Refactor `db backup` command to delegate to `_auto_backup()`, remove `--dest-dir` |
| `src/policydb/web/templates/settings/_db_health.html` | Show both tiers in health card |
