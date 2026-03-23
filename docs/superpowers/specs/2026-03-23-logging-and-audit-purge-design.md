# Logging & Audit Log Purge Design

**Date:** 2026-03-23
**Status:** Draft

## Context

PolicyDB has database-level audit logging (SQLite triggers on 7 tables, migration 067) but no application-level logging and no mechanism to purge old audit records. The audit_log table grows indefinitely at ~140 KB/day. There is no Python `logging` configured — diagnostics rely on uvicorn stderr and the audit log viewer at `/settings/audit-log`.

**Goals:**
1. Add application logging for troubleshooting (errors, stack traces) and operational visibility (request metrics, business activity counts)
2. Add automatic purge of old audit_log and app_log records with a 2-year default retention

## Approach: Unified SQLite + File Hybrid

Python's `logging` module with two handlers:
- **RotatingFileHandler** → `~/.policydb/logs/policydb.log` for raw debug/error output (what you `tail -f`)
- **Custom SQLite handler** → `app_log` table for structured metrics the UI can query, filter, and aggregate

---

## 1. Logging Config Module

**New file:** `src/policydb/logging_config.py`

Configures Python logging on server startup (called from `app.py`).

### File Handler
- Path: `~/.policydb/logs/policydb.log`
- `RotatingFileHandler`: 5 MB max per file, 5 backup files (25 MB total cap)
- Format: `[2026-03-23 14:02:31] [WARNING] reconciler: Score below threshold for POL-042 (score=32)`
- Level controlled by `log_level` config key (default: `INFO`)

### SQLite Handler
- Custom `logging.Handler` subclass that inserts into `app_log`
- Only captures INFO+ (no DEBUG spam in the database)
- Batches writes: flushes every 5 seconds or 50 entries (whichever first) to avoid per-request DB overhead
- Flush on shutdown to avoid data loss

### What Gets Logged Where

| Event | File | SQLite |
|-------|------|--------|
| Errors / stack traces | YES | YES (ERROR level) |
| Startup / shutdown | YES | YES |
| Slow requests (>2s) | YES | YES |
| All HTTP requests (method, path, status, duration) | no | YES |
| Business events (policy saved, activity created, reconcile run) | no | YES |
| Debug output (SQL queries, template rendering) | YES (if DEBUG) | no |

---

## 2. Data Schema — `app_log` Table

**New migration** (next sequential number):

```sql
CREATE TABLE IF NOT EXISTS app_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level       TEXT NOT NULL,
    category    TEXT NOT NULL,
    source      TEXT,
    message     TEXT NOT NULL,
    method      TEXT,
    path        TEXT,
    status_code INTEGER,
    duration_ms INTEGER,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_log_logged_at ON app_log (logged_at);
CREATE INDEX IF NOT EXISTS idx_app_log_category ON app_log (category);
CREATE INDEX IF NOT EXISTS idx_app_log_level ON app_log (level);
```

### Categories

| Category | Purpose | Fields Used |
|----------|---------|-------------|
| `request` | Every HTTP request | method, path, status_code, duration_ms |
| `business` | Policy saved, activity created, reconcile run, follow-up created | source, message |
| `system` | Startup, shutdown, migration run, purge completed | source, message |
| `error` | Exceptions, failed operations | source, message, detail (JSON: stack trace) |

---

## 3. Request Metrics Middleware

Lightweight FastAPI middleware in `app.py`:

```python
@app.middleware("http")
async def log_requests(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    # Skip /static/* paths
    # Log to app_log via SQLite handler with category='request'
    return response
```

Captures: method, path, status_code, duration_ms for every non-static request.

---

## 4. Business Event Logging

Lightweight `logger.info(...)` calls added to existing route handlers at key save points:

- `policies.py` — after policy create/update/delete
- `clients.py` — after client create/update
- `activities.py` — after activity creation
- `reconcile.py` — after reconcile run start/complete
- `inbox.py` — after inbox item processed
- `templates.py` — after email template compose/send

Each call includes `extra={"category": "business"}` so the SQLite handler routes it correctly.

---

## 5. Audit Log Purge

### Mechanism

Runs on every server startup, after migrations complete in `init_db()`:

1. Read `log_retention_days` from config (default: 730 = 2 years)
2. Delete old records:
   ```sql
   DELETE FROM audit_log WHERE changed_at < date('now', '-730 days');
   DELETE FROM app_log WHERE logged_at < date('now', '-730 days');
   ```
3. Log the result as a `system` category entry: "Purged 142 audit_log rows and 3,841 app_log rows older than 730 days"
4. Run `VACUUM` only if >1,000 total rows were purged (reclaims disk space without overhead on small purges)

### Safety

- Purge runs inside a transaction — rolls back on failure, logs error
- Single config key `log_retention_days` controls both tables

---

## 6. Logs UI Page

**Route:** `GET /logs` — standalone page with its own nav entry (not under Settings).

The audit log viewer stays at `/settings/audit-log` (unchanged).

### Layout

- **Filter bar:** category dropdown (All / Request / Business / System / Error), level dropdown (All / INFO / WARNING / ERROR), date range picker, free-text search on message/path
- **Summary bar:** total entries, error count in last 24h, avg response time today
- **Results table:** `logged_at | level | category | source | message | duration_ms`
  - Level column: color-coded badges (green=INFO, amber=WARNING, red=ERROR)
  - Request rows show `METHOD path → status` in the message column
  - Clicking a row expands to show `detail` JSON (stack traces, extra context)
- **Pagination:** 100 rows per page, newest first
- **Retention notice:** "Logs older than 2 years are automatically purged on server startup"

---

## 7. Config Keys

Added to `_DEFAULTS` in `config.py`:

| Key | Default | Purpose |
|-----|---------|---------|
| `log_level` | `"INFO"` | Controls file handler verbosity (DEBUG/INFO/WARNING/ERROR) |
| `log_retention_days` | `730` | Days to keep audit_log and app_log records before auto-purge |

Both are simple scalar values, not list-type configs — no Settings UI list management needed.

---

## 8. Files Modified

| File | Change |
|------|--------|
| `src/policydb/logging_config.py` | **NEW** — logging setup, file handler, SQLite handler |
| `src/policydb/migrations/NNN_app_log.sql` | **NEW** — app_log table + indexes |
| `src/policydb/db.py` | Wire migration, add purge logic to `init_db()` |
| `src/policydb/web/app.py` | Call logging config on startup, add request middleware |
| `src/policydb/web/routes/logs.py` | **NEW** — `/logs` page route + filters |
| `src/policydb/web/templates/logs/index.html` | **NEW** — logs viewer template |
| `src/policydb/config.py` | Add `log_level` and `log_retention_days` to `_DEFAULTS` |
| `src/policydb/web/routes/policies.py` | Add business event logging at save points |
| `src/policydb/web/routes/clients.py` | Add business event logging at save points |
| `src/policydb/web/routes/activities.py` | Add business event logging at save points |
| `src/policydb/web/routes/reconcile.py` | Add business event logging at save points |
| `src/policydb/web/routes/inbox.py` | Add business event logging at save points |
| `src/policydb/web/routes/templates.py` | Add business event logging at save points |
| `src/policydb/web/templates/base.html` | Add "Logs" to nav |

---

## 9. Verification

1. Start server — confirm `~/.policydb/logs/policydb.log` is created, startup logged
2. Hit several pages — confirm request entries appear in `app_log` table
3. Save a policy — confirm business event logged
4. Trigger an error (e.g., bad input) — confirm error + stack trace logged to both file and DB
5. Visit `/logs` — confirm filters work, rows display, detail expands
6. Set `log_retention_days` to 1, insert a fake old record, restart — confirm purge runs and removes it
7. Confirm audit log viewer at `/settings/audit-log` still works unchanged
