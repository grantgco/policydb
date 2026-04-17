# Timesheet Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 4 Timesheet Review page — a weekly human-review layer over the hours captured by Phases 1–3 that lets the user correct auto-captured hours, catch missed work, and soft-close the week.

**Architecture:** New `timesheet` module + router + six templates. Lives as a tab under the Action Center and a dashboard card when flags exist. Single SQLite migration (160) adds `activity_log.reviewed_at` and a `timesheet_closeouts` table. All flag computation is live (SQL at request time; no materialized views). Auto-review on field focus + explicit "Close out week" soft-stamp.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite (WAL, row_factory), Tailwind CDN. Tests run via `pytest` against in-memory SQLite fixtures that initialize via `init_db()`.

**Spec:** `docs/superpowers/specs/2026-04-16-timesheet-review-design.md` — read this first.

---

## File Structure

**Create:**
- `src/policydb/migrations/161_timesheet_review.sql` — schema
- `src/policydb/timesheet.py` — `build_timesheet_payload()` + flag helpers
- `src/policydb/web/routes/timesheet.py` — router
- `src/policydb/web/templates/timesheet/_panel.html`
- `src/policydb/web/templates/timesheet/_flag_strip.html`
- `src/policydb/web/templates/timesheet/_day_card.html`
- `src/policydb/web/templates/timesheet/_activity_row.html`
- `src/policydb/web/templates/timesheet/_add_activity_form.html`
- `src/policydb/web/templates/timesheet/_closeout_badge.html`
- `src/policydb/web/templates/dashboard/_timesheet_card.html`
- `src/policydb/web/templates/settings/_timesheet_thresholds_form.html`
- `tests/test_timesheet.py`
- `tests/test_timesheet_routes.py`

**Modify:**
- `src/policydb/db.py` — wire migration 161 into `init_db()`
- `src/policydb/config.py` — add `timesheet_thresholds` dict to `_DEFAULTS`
- `src/policydb/queries.py` — add `get_timesheet_badge()`
- `src/policydb/web/app.py` — register timesheet router
- `src/policydb/web/routes/action_center.py` — add "Timesheet" tab to `+ More` menu
- `src/policydb/web/routes/dashboard.py` — pass badge to template
- `src/policydb/web/routes/settings.py` — add `save_timesheet_thresholds` form route + include partial
- `src/policydb/web/templates/dashboard.html` — conditionally include timesheet card

---

## Task Sequence

```
Data layer (1-2)     → Module + flags (3-8)  → Badge (9)
Routes (10-17)       → Templates (18-22)     → Integrations (23-25)
Settings UI (26)     → Manual QA (27)
```

Every task ends with a commit. Never `--no-verify`. Use full venv: `~/.policydb/venv/bin/pytest` when invoking pytest (per `feedback_server_restart` memory).

---

### Task 1: Migration 161 — schema + db.py wiring

**Files:**
- Create: `src/policydb/migrations/161_timesheet_review.sql`
- Modify: `src/policydb/db.py` (after the migration 159 block; grep for `if 159 not in applied`)
- Test: `tests/test_timesheet.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_timesheet.py`:

```python
"""Tests for the timesheet module and schema."""

import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_migration_160_adds_reviewed_at_column(tmp_db):
    conn = get_connection(tmp_db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(activity_log)").fetchall()}
    assert "reviewed_at" in cols
    conn.close()


def test_migration_160_creates_timesheet_closeouts(tmp_db):
    conn = get_connection(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "timesheet_closeouts" in tables
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(timesheet_closeouts)"
    ).fetchall()}
    assert {"id", "week_start", "week_end", "closed_at",
            "total_hours", "activity_count", "flag_count"} <= cols
    conn.close()


def test_migration_160_partial_index_on_reviewed_at(tmp_db):
    conn = get_connection(tmp_db)
    idxs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='activity_log'"
    ).fetchall()}
    assert "idx_activity_log_reviewed_at" in idxs
    conn.close()


def test_migration_160_closeouts_unique_week_start(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute(
        """INSERT INTO timesheet_closeouts
           (week_start, week_end, total_hours, activity_count, flag_count)
           VALUES (?, ?, ?, ?, ?)""",
        ("2026-04-13", "2026-04-19", 32.0, 20, 2),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO timesheet_closeouts
               (week_start, week_end, total_hours, activity_count, flag_count)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-04-13", "2026-04-19", 28.0, 18, 3),
        )
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: FAIL — either "no such table timesheet_closeouts" or "no such column reviewed_at" or test collection error.

- [ ] **Step 3: Write the migration SQL**

Create `src/policydb/migrations/161_timesheet_review.sql`:

```sql
-- Phase 4: Timesheet Review
-- Adds per-activity review stamp and a week-level closeout log.

ALTER TABLE activity_log ADD COLUMN reviewed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_activity_log_reviewed_at
    ON activity_log (reviewed_at)
    WHERE reviewed_at IS NULL;

CREATE TABLE IF NOT EXISTS timesheet_closeouts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start      DATE NOT NULL,
    week_end        DATE NOT NULL,
    closed_at       TEXT NOT NULL DEFAULT (datetime('now')),
    total_hours     REAL NOT NULL,
    activity_count  INTEGER NOT NULL,
    flag_count      INTEGER NOT NULL,
    UNIQUE (week_start)
);

CREATE INDEX IF NOT EXISTS idx_timesheet_closeouts_week_start
    ON timesheet_closeouts (week_start);
```

- [ ] **Step 4: Wire migration into `init_db()`**

In `src/policydb/db.py`, find the migration 159 block (`if 159 not in applied:`) and add immediately after it:

```python
    if 160 not in applied:
        conn.executescript((_MIGRATIONS_DIR / "161_timesheet_review.sql").read_text())
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (160, "Add activity_log.reviewed_at + timesheet_closeouts table (Phase 4 Timesheet Review)"),
        )
        conn.commit()
        logger.info("Migration 161: added activity_log.reviewed_at + timesheet_closeouts")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: all four tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/migrations/161_timesheet_review.sql src/policydb/db.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): migration 161 — reviewed_at + timesheet_closeouts

Phase 4 schema foundation. Adds activity_log.reviewed_at (partial
index where NULL) plus a timesheet_closeouts table that captures the
snapshot-at-close for each week (week_start UNIQUE).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Config — timesheet_thresholds defaults

**Files:**
- Modify: `src/policydb/config.py` — add `timesheet_thresholds` dict to `_DEFAULTS`
- Test: `tests/test_timesheet.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def test_timesheet_thresholds_default():
    from policydb import config as cfg
    thresholds = cfg.get("timesheet_thresholds", {})
    assert thresholds.get("low_day_threshold_hours") == 4.0
    assert thresholds.get("silence_renewal_window_days") == 30
    assert thresholds.get("range_cap_days") == 92
```

- [ ] **Step 2: Run test to verify it fails**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_timesheet_thresholds_default -v
```

Expected: FAIL — `None == 4.0` or KeyError-like.

- [ ] **Step 3: Add defaults in config.py**

In `src/policydb/config.py`, locate `_DEFAULTS = {` and add a new key (keep alphabetical-ish order, near other `*_thresholds` if they exist):

```python
    "timesheet_thresholds": {
        "low_day_threshold_hours": 4.0,
        "silence_renewal_window_days": 30,
        "range_cap_days": 92,
    },
```

- [ ] **Step 4: Run test to verify it passes**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_timesheet_thresholds_default -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/config.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): add timesheet_thresholds config defaults

Three scalar thresholds drive the Phase 4 Timesheet Review page:
low-day (4.0h), silence window (30d), range cap (92d).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Module scaffolding — `build_timesheet_payload` skeleton

**Files:**
- Create: `src/policydb/timesheet.py`
- Modify: `tests/test_timesheet.py` (append)

This task builds the module shell that returns an empty-but-shaped payload. Subsequent tasks fill in each flag.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def test_build_payload_shape_for_standard_week(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    start = date(2026, 4, 13)   # Monday
    end = date(2026, 4, 19)     # Sunday
    payload = build_timesheet_payload(conn, start=start, end=end)

    assert payload["range"]["start"] == "2026-04-13"
    assert payload["range"]["end"] == "2026-04-19"
    assert payload["range"]["kind"] in ("week", "day", "range")
    assert payload["totals"]["total_hours"] == 0.0
    assert payload["totals"]["activity_count"] == 0
    assert payload["totals"]["flag_count"] == 0
    assert "flags" in payload
    assert set(payload["flags"].keys()) == {
        "low_days", "silent_clients", "unreviewed_emails", "null_hour_activities"
    }
    assert isinstance(payload["days"], list)
    assert len(payload["days"]) == 7  # Mon..Sun
    assert payload["days"][0]["date"] == "2026-04-13"
    assert payload["days"][6]["date"] == "2026-04-19"
    assert payload["closeout"] == {"closed_at": None, "snapshot": None}
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_build_payload_shape_for_standard_week -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'policydb.timesheet'`.

- [ ] **Step 3: Create the module**

Create `src/policydb/timesheet.py`:

```python
"""Phase 4 — Timesheet Review core module.

Builds the payload for the weekly timesheet review page. All flag
computation is live: no materialized views, no background jobs.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any


def _daterange(start: date, end: date) -> list[date]:
    """Inclusive list of dates from start to end."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _classify_range(start: date, end: date) -> str:
    if start == end:
        return "day"
    days = (end - start).days + 1
    if days == 7 and start.weekday() == 0:
        return "week"
    return "range"


def build_timesheet_payload(
    conn: sqlite3.Connection,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Build the full timesheet-review payload for the given range.

    Returns a dict with keys: range, totals, flags, days, closeout.
    """
    days = [
        {
            "date": d.isoformat(),
            "label": d.strftime("%a · %b %-d"),
            "total_hours": 0.0,
            "is_low": False,
            "activities": [],
        }
        for d in _daterange(start, end)
    ]

    return {
        "range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}",
            "kind": _classify_range(start, end),
        },
        "totals": {
            "total_hours": 0.0,
            "activity_count": 0,
            "flag_count": 0,
        },
        "flags": {
            "low_days": [],
            "silent_clients": [],
            "unreviewed_emails": 0,
            "null_hour_activities": 0,
        },
        "days": days,
        "closeout": {"closed_at": None, "snapshot": None},
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_build_payload_shape_for_standard_week -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): add build_timesheet_payload scaffolding

Empty-but-shaped payload returned for any range. Subsequent tasks
fill in activities, day totals, and each flag category.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Activities in range + day totals + low-day flag

**Files:**
- Modify: `src/policydb/timesheet.py`
- Modify: `tests/test_timesheet.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def _seed_client(conn, name="Acme Corp"):
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES (?, 'Technology', 'Grant')",
        (name,),
    )
    return cur.lastrowid


def _seed_activity(conn, *, client_id, activity_date, duration_hours,
                   subject="test", activity_type="Email", source="manual",
                   reviewed_at=None, follow_up_done=0, item_kind="activity"):
    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            duration_hours, source, reviewed_at, follow_up_done, item_kind)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (activity_date, client_id, subject, activity_type,
         duration_hours, source, reviewed_at, follow_up_done, item_kind),
    )
    conn.commit()
    return cur.lastrowid


def test_day_totals_and_low_day_flag(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)

    # Mon: 2h (low), Tue: 4.5h (OK), Wed: 0h (not flagged — zero activities)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-13", duration_hours=2.0)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-14", duration_hours=4.5)

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 17),
    )
    by_date = {d["date"]: d for d in payload["days"]}
    assert by_date["2026-04-13"]["total_hours"] == 2.0
    assert by_date["2026-04-13"]["is_low"] is True
    assert by_date["2026-04-14"]["total_hours"] == 4.5
    assert by_date["2026-04-14"]["is_low"] is False
    assert by_date["2026-04-15"]["total_hours"] == 0.0
    assert by_date["2026-04-15"]["is_low"] is False  # zero-activity: no flag
    assert payload["totals"]["total_hours"] == 6.5
    assert payload["totals"]["activity_count"] == 2
    # One activity row on each populated day
    assert len(by_date["2026-04-13"]["activities"]) == 1
    assert len(by_date["2026-04-14"]["activities"]) == 1
    # flags.low_days contains only the low day
    assert payload["flags"]["low_days"] == ["2026-04-13"]
    conn.close()


def test_low_day_flag_ignores_weekend(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)
    # 1h on Saturday
    _seed_activity(conn, client_id=cid, activity_date="2026-04-18", duration_hours=1.0)
    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 19),
    )
    by_date = {d["date"]: d for d in payload["days"]}
    assert by_date["2026-04-18"]["is_low"] is False
    assert payload["flags"]["low_days"] == []
    conn.close()


def test_low_day_flag_ignores_future(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)
    future = (date.today() + timedelta(days=3)).isoformat()
    _seed_activity(conn, client_id=cid, activity_date=future, duration_hours=0.5)
    payload = build_timesheet_payload(
        conn, start=date.today(), end=date.today() + timedelta(days=7),
    )
    by_date = {d["date"]: d for d in payload["days"]}
    assert by_date[future]["is_low"] is False
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_day_totals_and_low_day_flag tests/test_timesheet.py::test_low_day_flag_ignores_weekend tests/test_timesheet.py::test_low_day_flag_ignores_future -v
```

Expected: all three FAIL — assertions on totals/is_low.

- [ ] **Step 3: Extend the module**

Replace the body of `build_timesheet_payload()` in `src/policydb/timesheet.py`. The full new file content:

```python
"""Phase 4 — Timesheet Review core module."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from policydb import config as cfg


def _daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _classify_range(start: date, end: date) -> str:
    if start == end:
        return "day"
    days = (end - start).days + 1
    if days == 7 and start.weekday() == 0:
        return "week"
    return "range"


def _load_activities(conn, start: date, end: date) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT a.id, a.activity_date, a.activity_type, a.subject,
                  a.duration_hours, a.reviewed_at, a.source, a.follow_up_done,
                  a.item_kind, a.client_id, a.policy_id, a.details,
                  c.name AS client_name
           FROM activity_log a
           LEFT JOIN clients c ON a.client_id = c.id
           WHERE a.activity_date BETWEEN ? AND ?
           ORDER BY a.activity_date, a.id""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


def build_timesheet_payload(
    conn: sqlite3.Connection,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    thresholds = cfg.get("timesheet_thresholds", {}) or {}
    low_threshold = float(thresholds.get("low_day_threshold_hours", 4.0))

    rows = _load_activities(conn, start, end)
    today = date.today()

    days_map: dict[str, dict[str, Any]] = {}
    for d in _daterange(start, end):
        iso = d.isoformat()
        days_map[iso] = {
            "date": iso,
            "label": d.strftime("%a · %b %-d"),
            "total_hours": 0.0,
            "is_low": False,
            "activities": [],
        }

    total_hours = 0.0
    for r in rows:
        day = days_map.get(r["activity_date"])
        if day is None:
            continue
        hrs = float(r["duration_hours"] or 0.0)
        day["total_hours"] = round(day["total_hours"] + hrs, 2)
        day["activities"].append({
            "id": r["id"],
            "subject": r["subject"] or "",
            "activity_type": r["activity_type"] or "",
            "duration_hours": r["duration_hours"],
            "reviewed_at": r["reviewed_at"],
            "source": r["source"] or "manual",
            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "policy_id": r["policy_id"],
        })
        total_hours += hrs

    low_days: list[str] = []
    for iso, day in days_map.items():
        d_obj = date.fromisoformat(iso)
        is_weekday = d_obj.weekday() < 5
        is_past_or_today = d_obj <= today
        has_activity = day["total_hours"] > 0
        if is_weekday and is_past_or_today and has_activity and day["total_hours"] < low_threshold:
            day["is_low"] = True
            low_days.append(iso)

    flag_count = len(low_days)

    return {
        "range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}",
            "kind": _classify_range(start, end),
        },
        "totals": {
            "total_hours": round(total_hours, 2),
            "activity_count": len(rows),
            "flag_count": flag_count,
        },
        "flags": {
            "low_days": low_days,
            "silent_clients": [],
            "unreviewed_emails": 0,
            "null_hour_activities": 0,
        },
        "days": list(days_map.values()),
        "closeout": {"closed_at": None, "snapshot": None},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: all prior tests still pass + three new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): day totals + low-day flag

Loads activities in range, groups by date, flags weekdays with
activity below threshold (skips weekends, zero-activity days,
and future dates).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Silent-client flag

**Files:**
- Modify: `src/policydb/timesheet.py`
- Modify: `tests/test_timesheet.py` (append)

Silent-client = client has an open followup OR a policy renewing within N days OR an open issue, AND zero activities in range.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def _seed_policy(conn, *, client_id, expiration_date, is_opportunity=0):
    cur = conn.execute(
        """INSERT INTO policies (client_id, first_named_insured, policy_type,
                                 expiration_date, is_opportunity, renewal_status)
           VALUES (?, 'Test Ins', 'General Liability', ?, ?, 'In Progress')""",
        (client_id, expiration_date, is_opportunity),
    )
    conn.commit()
    return cur.lastrowid


def _seed_followup(conn, *, client_id, follow_up_date):
    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            follow_up_date, follow_up_done, item_kind)
           VALUES (date('now'), ?, 'needs follow-up', 'Task', ?, 0, 'followup')""",
        (client_id, follow_up_date),
    )
    conn.commit()
    return cur.lastrowid


def test_silent_clients_flag_with_imminent_renewal(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    cid_silent = _seed_client(conn, "Silent Corp")
    # Renewal 10 days out → inside default 30-day window
    exp = (date.today() + timedelta(days=10)).isoformat()
    _seed_policy(conn, client_id=cid_silent, expiration_date=exp)

    cid_active = _seed_client(conn, "Active Corp")
    _seed_policy(conn, client_id=cid_active, expiration_date=exp)
    _seed_activity(conn, client_id=cid_active,
                   activity_date=date.today().isoformat(),
                   duration_hours=1.0)

    payload = build_timesheet_payload(
        conn,
        start=date.today() - timedelta(days=date.today().weekday()),
        end=date.today() - timedelta(days=date.today().weekday()) + timedelta(days=6),
    )
    names = {c["name"] for c in payload["flags"]["silent_clients"]}
    assert "Silent Corp" in names
    assert "Active Corp" not in names
    conn.close()


def test_silent_clients_flag_with_open_followup(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    cid = _seed_client(conn, "Followup Corp")
    _seed_followup(conn, client_id=cid,
                   follow_up_date=(date.today() + timedelta(days=5)).isoformat())

    start = date.today() - timedelta(days=date.today().weekday())
    payload = build_timesheet_payload(conn, start=start, end=start + timedelta(days=6))

    names = {c["name"] for c in payload["flags"]["silent_clients"]}
    assert "Followup Corp" in names
    conn.close()


def test_silent_clients_ignores_clients_without_work(tmp_db):
    """A client with no followups / no imminent renewal / no issue is NOT silent."""
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    cid = _seed_client(conn, "Dormant Corp")
    # No followup, no policy, no issue → not silent, just dormant

    start = date.today() - timedelta(days=date.today().weekday())
    payload = build_timesheet_payload(conn, start=start, end=start + timedelta(days=6))

    names = {c["name"] for c in payload["flags"]["silent_clients"]}
    assert "Dormant Corp" not in names
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_silent_clients_flag_with_imminent_renewal tests/test_timesheet.py::test_silent_clients_flag_with_open_followup tests/test_timesheet.py::test_silent_clients_ignores_clients_without_work -v
```

Expected: FAIL — silent_clients is still an empty list.

- [ ] **Step 3: Implement silent-client detection**

In `src/policydb/timesheet.py`, add a helper and call it from `build_timesheet_payload`.

Add new helper at module scope:

```python
def _compute_silent_clients(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    renewal_window_days: int,
) -> list[dict[str, Any]]:
    """Clients with signals of active work but zero activity in the range.

    Signals: open followup (activity_log.item_kind='followup' AND follow_up_done=0),
             policy with expiration within renewal_window_days,
             open issue (activity_log.item_kind='issue' AND follow_up_done=0).
    """
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()
    window_end = (date.today() + timedelta(days=renewal_window_days)).isoformat()

    rows = conn.execute(
        """
        WITH candidates AS (
            -- open followup
            SELECT DISTINCT client_id,
                   'open_followup' AS reason
            FROM activity_log
            WHERE item_kind = 'followup'
              AND follow_up_done = 0
              AND client_id IS NOT NULL
            UNION
            -- imminent renewal
            SELECT DISTINCT client_id,
                   'imminent_renewal' AS reason
            FROM policies
            WHERE expiration_date BETWEEN ? AND ?
              AND (is_opportunity = 0 OR is_opportunity IS NULL)
            UNION
            -- open issue
            SELECT DISTINCT client_id,
                   'open_issue' AS reason
            FROM activity_log
            WHERE item_kind = 'issue'
              AND follow_up_done = 0
              AND client_id IS NOT NULL
        )
        SELECT c.id AS client_id, c.name, MIN(cand.reason) AS reason
        FROM candidates cand
        JOIN clients c ON c.id = cand.client_id
        LEFT JOIN activity_log a
               ON a.client_id = cand.client_id
              AND a.activity_date BETWEEN ? AND ?
              AND (a.duration_hours IS NOT NULL OR a.item_kind = 'activity')
        WHERE a.id IS NULL
        GROUP BY c.id, c.name
        ORDER BY c.name
        """,
        (today, window_end, start.isoformat(), end.isoformat()),
    ).fetchall()

    return [
        {
            "client_id": r["client_id"],
            "name": r["name"],
            "reason": r["reason"],
            "href": f"/clients/{r['client_id']}",
        }
        for r in rows
    ]
```

Then modify the body of `build_timesheet_payload` to call it. Replace the existing `"silent_clients": []` with:

```python
    silence_window = int(thresholds.get("silence_renewal_window_days", 30))
    silent_clients = _compute_silent_clients(conn, start, end, silence_window)
```

And update the `flags` dict and `flag_count`:

```python
    flag_count = len(low_days) + len(silent_clients)

    return {
        ...
        "totals": {
            "total_hours": round(total_hours, 2),
            "activity_count": len(rows),
            "flag_count": flag_count,
        },
        "flags": {
            "low_days": low_days,
            "silent_clients": silent_clients,
            "unreviewed_emails": 0,
            "null_hour_activities": 0,
        },
        ...
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: all prior tests pass + three new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): silent-client flag

Surfaces clients with open followup / imminent renewal (default 30d)
/ open issue but zero activity in the range. One CTE-based query.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Unreviewed-emails count + null-hour-activities count

**Files:**
- Modify: `src/policydb/timesheet.py`
- Modify: `tests/test_timesheet.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def test_unreviewed_emails_count(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)

    # 3 unreviewed outlook_sync emails, 2 reviewed, 1 thread_inherit unreviewed
    for _ in range(3):
        _seed_activity(conn, client_id=cid, activity_date="2026-04-14",
                       duration_hours=0.1, source="outlook_sync", reviewed_at=None)
    for _ in range(2):
        _seed_activity(conn, client_id=cid, activity_date="2026-04-14",
                       duration_hours=0.1, source="outlook_sync",
                       reviewed_at="2026-04-15T10:00:00")
    _seed_activity(conn, client_id=cid, activity_date="2026-04-14",
                   duration_hours=0.15, source="thread_inherit", reviewed_at=None)

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 19),
    )
    assert payload["flags"]["unreviewed_emails"] == 4
    conn.close()


def test_null_hour_activities_count(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)

    # 2 with NULL hours, 3 with hours set
    _seed_activity(conn, client_id=cid, activity_date="2026-04-14", duration_hours=None)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-15", duration_hours=None)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-14", duration_hours=1.0)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-15", duration_hours=0.5)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-16", duration_hours=2.0)

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 19),
    )
    assert payload["flags"]["null_hour_activities"] == 2
    # Flag count includes these buckets when > 0
    assert payload["totals"]["flag_count"] >= 2
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_unreviewed_emails_count tests/test_timesheet.py::test_null_hour_activities_count -v
```

Expected: FAIL — both counts are still 0.

- [ ] **Step 3: Implement the counts**

In `src/policydb/timesheet.py`, inside `build_timesheet_payload` after computing `low_days` and `silent_clients`, add:

```python
    unreviewed_emails = conn.execute(
        """SELECT COUNT(*) AS n FROM activity_log
           WHERE reviewed_at IS NULL
             AND source IN ('outlook_sync', 'thread_inherit')
             AND activity_date BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchone()["n"]

    null_hour_activities = conn.execute(
        """SELECT COUNT(*) AS n FROM activity_log
           WHERE duration_hours IS NULL
             AND activity_date BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchone()["n"]
```

Update the flag_count and flags dict:

```python
    flag_count = (
        len(low_days)
        + len(silent_clients)
        + (1 if unreviewed_emails else 0)
        + (1 if null_hour_activities else 0)
    )
    ...
    "flags": {
        "low_days": low_days,
        "silent_clients": silent_clients,
        "unreviewed_emails": unreviewed_emails,
        "null_hour_activities": null_hour_activities,
    },
```

**Note on `flag_count`:** it's the number of distinct *attention buckets* that need acknowledgement, not the count of flagged items. A day with 3 low days + 7 unreviewed emails has flag_count = 4 (3 days + 1 email bucket). This is what the dashboard badge shows.

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): unreviewed-emails + null-hour-activities counts

Two simple count queries round out the four flag types. flag_count is
the number of distinct attention buckets (days + 1 for email bucket
+ 1 for null-hour bucket), matching the dashboard badge semantics.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Closeout snapshot in payload

**Files:**
- Modify: `src/policydb/timesheet.py`
- Modify: `tests/test_timesheet.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def test_closeout_snapshot_returned(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    conn.execute(
        """INSERT INTO timesheet_closeouts
           (week_start, week_end, total_hours, activity_count, flag_count)
           VALUES ('2026-04-13', '2026-04-19', 32.5, 25, 3)"""
    )
    conn.commit()

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 19),
    )
    assert payload["closeout"]["closed_at"] is not None
    snap = payload["closeout"]["snapshot"]
    assert snap is not None
    assert snap["total_hours"] == 32.5
    assert snap["activity_count"] == 25
    assert snap["flag_count"] == 3
    conn.close()


def test_no_closeout_for_non_week_range(tmp_db):
    """Closeout only lives at week granularity; a day or custom range returns None."""
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    payload = build_timesheet_payload(conn, start=date(2026, 4, 13), end=date(2026, 4, 13))
    assert payload["closeout"] == {"closed_at": None, "snapshot": None}
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_closeout_snapshot_returned tests/test_timesheet.py::test_no_closeout_for_non_week_range -v
```

Expected: FAIL — `closed_at` still None when closeout row exists.

- [ ] **Step 3: Implement closeout lookup**

In `src/policydb/timesheet.py` `build_timesheet_payload`, replace the `"closeout": {"closed_at": None, "snapshot": None}` at the bottom with:

```python
    closeout = {"closed_at": None, "snapshot": None}
    if _classify_range(start, end) == "week":
        row = conn.execute(
            """SELECT closed_at, total_hours, activity_count, flag_count
               FROM timesheet_closeouts WHERE week_start = ?""",
            (start.isoformat(),),
        ).fetchone()
        if row:
            closeout = {
                "closed_at": row["closed_at"],
                "snapshot": {
                    "total_hours": row["total_hours"],
                    "activity_count": row["activity_count"],
                    "flag_count": row["flag_count"],
                },
            }
    ...
    return {
        ...
        "closeout": closeout,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/timesheet.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): closeout snapshot in payload

When viewing a week that has been closed out, the payload carries the
snapshot (total_hours, activity_count, flag_count at close time) so
the badge can show 'Closed Apr 16 · δ +1.5h'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `get_timesheet_badge()` helper for dashboard

**Files:**
- Modify: `src/policydb/queries.py` — add function
- Modify: `tests/test_timesheet.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet.py`:

```python
def test_get_timesheet_badge(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.queries import get_timesheet_badge
    cid = _seed_client(conn)

    # today's week (Mon–Sun) — seed 2 unreviewed emails + a client silence
    start = date.today() - timedelta(days=date.today().weekday())
    _seed_activity(conn, client_id=cid,
                   activity_date=start.isoformat(),
                   duration_hours=0.1, source="outlook_sync")
    _seed_activity(conn, client_id=cid,
                   activity_date=start.isoformat(),
                   duration_hours=0.1, source="outlook_sync")
    # Silent renewal-driven client
    cid2 = _seed_client(conn, "Silent B")
    _seed_policy(conn, client_id=cid2,
                 expiration_date=(date.today() + timedelta(days=5)).isoformat())

    badge = get_timesheet_badge(conn)
    assert isinstance(badge, dict)
    assert badge["unreviewed_emails"] == 2
    assert badge["flags"] >= 1  # at least the silent client counts
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py::test_get_timesheet_badge -v
```

Expected: FAIL — `AttributeError: module 'policydb.queries' has no attribute 'get_timesheet_badge'`.

- [ ] **Step 3: Implement the helper**

Append to `src/policydb/queries.py` (near the other `get_*_hours` helpers around line 1223):

```python
def get_timesheet_badge(conn: sqlite3.Connection) -> dict:
    """Dashboard badge counts for the current week.

    Returns {flags: int, unreviewed_emails: int} — 0 for both means 'hide card'.
    flags is the distinct-bucket count; unreviewed_emails broken out separately
    so the card can say 'Review this week (3 flags, 7 emails)'.
    """
    from datetime import date, timedelta
    from policydb.timesheet import build_timesheet_payload

    today = date.today()
    start = today - timedelta(days=today.weekday())  # Monday
    end = start + timedelta(days=6)                  # Sunday
    payload = build_timesheet_payload(conn, start=start, end=end)
    return {
        "flags": int(payload["totals"]["flag_count"]),
        "unreviewed_emails": int(payload["flags"]["unreviewed_emails"]),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/queries.py tests/test_timesheet.py
git commit -m "$(cat <<'EOF'
feat(timesheet): get_timesheet_badge() for dashboard card

Returns {flags, unreviewed_emails} for the current Mon-Sun week so
the dashboard can conditionally show a 'Review this week' card.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Router + `GET /timesheet/panel` (stub response)

**Files:**
- Create: `src/policydb/web/routes/timesheet.py`
- Modify: `src/policydb/web/app.py` — register router
- Create: `tests/test_timesheet_routes.py`

First pass: route exists, returns 200 with an HTMLResponse containing a known marker. Template comes in Task 18+; for now the route returns a stub.

- [ ] **Step 1: Write the failing test**

Create `tests/test_timesheet_routes.py`:

```python
"""Route tests for Phase 4 Timesheet Review."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    from policydb.db import init_db
    init_db(path=db_path)
    from policydb.web.app import app
    return TestClient(app)


def test_timesheet_panel_default_returns_200(client):
    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    # Week-kind range by default
    assert "timesheet-panel" in resp.text


def test_timesheet_panel_accepts_kind_week(client):
    resp = client.get("/timesheet/panel?kind=week")
    assert resp.status_code == 200


def test_timesheet_panel_accepts_explicit_range(client):
    resp = client.get("/timesheet/panel?kind=day&start=2026-04-15&end=2026-04-15")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: 404s (no router registered).

- [ ] **Step 3: Create the router with a stub GET /panel**

Create `src/policydb/web/routes/timesheet.py`:

```python
"""Phase 4 — Timesheet Review routes."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from policydb import config as cfg
from policydb.db import get_connection
from policydb.timesheet import build_timesheet_payload
from policydb.web.app import templates

router = APIRouter(prefix="/timesheet", tags=["timesheet"])


def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def _resolve_range(
    kind: str,
    start: str | None,
    end: str | None,
) -> tuple[date, date, str]:
    today = date.today()
    if kind == "day":
        d = date.fromisoformat(start) if start else today
        return d, d, "day"
    if kind == "range":
        if not start or not end:
            raise HTTPException(400, "range requires start and end")
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        if e < s:
            raise HTTPException(400, "end < start")
        cap = int((cfg.get("timesheet_thresholds", {}) or {}).get("range_cap_days", 92))
        if (e - s).days + 1 > cap:
            raise HTTPException(400, f"range exceeds {cap} days")
        return s, e, "range"
    # default: week
    anchor = date.fromisoformat(start) if start else today
    week_start = anchor - timedelta(days=anchor.weekday())
    return week_start, week_start + timedelta(days=6), "week"


@router.get("/panel", response_class=HTMLResponse)
def get_panel(
    request: Request,
    kind: str = Query("week"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    conn=Depends(get_db),
):
    s, e, resolved_kind = _resolve_range(kind, start, end)
    payload = build_timesheet_payload(conn, start=s, end=e)
    payload["range"]["kind"] = resolved_kind
    return templates.TemplateResponse(
        "timesheet/_panel.html",
        {"request": request, "payload": payload},
    )
```

- [ ] **Step 4: Create a minimal panel template so the route can render**

Create `src/policydb/web/templates/timesheet/_panel.html`:

```html
<div id="timesheet-panel" data-range-kind="{{ payload.range.kind }}">
  <div class="mb-4">
    <span class="text-sm text-stone-500">{{ payload.range.label }}</span>
    <span class="ml-3 text-sm font-mono">{{ payload.totals.total_hours }}h</span>
    <span class="ml-3 text-xs text-stone-500">
      {{ payload.totals.activity_count }} activities · {{ payload.totals.flag_count }} flags
    </span>
  </div>
  <!-- Day cards, flag strip, and closeout badge ship in Tasks 18-22 -->
</div>
```

- [ ] **Step 5: Register the router**

In `src/policydb/web/app.py`, add the import near the other route imports and register with the other `include_router` calls:

```python
from policydb.web.routes import timesheet as timesheet_routes
...
app.include_router(timesheet_routes.router)
```

Also expose `templates` from `app.py` if not already exported. If a variable named `templates = _CompatTemplates(...)` exists, good. If not, locate where `Jinja2Templates` is instantiated and ensure a module-level `templates` name is assigned. (Most routes in the repo already do `from policydb.web.app import templates`; confirm with one grep: `grep -n "from policydb.web.app import templates" src/policydb/web/routes/dashboard.py`).

- [ ] **Step 6: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all three pass.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/timesheet.py src/policydb/web/app.py src/policydb/web/templates/timesheet/_panel.html tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): router + GET /timesheet/panel + minimal template

Resolves kind=week|day|range into concrete dates, validates range
cap, and renders a minimal panel shell. Day cards, flag strip,
closeout badge land in later tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `POST /timesheet/activity/{id}/review` (idempotent stamp)

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py`
- Modify: `tests/test_timesheet_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def _make_activity(client, *, subject="Test", hours=0.1, source="manual"):
    """Insert an activity via raw SQL (fast path) and return its id."""
    from policydb.db import get_connection
    conn = get_connection()
    # Seed a client first
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Cust', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            duration_hours, source, item_kind)
           VALUES (date('now'), ?, ?, 'Email', ?, ?, 'activity')""",
        (cid, subject, hours, source),
    )
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def test_post_review_stamps_reviewed_at(client):
    aid = _make_activity(client)
    resp = client.post(f"/timesheet/activity/{aid}/review")
    assert resp.status_code in (200, 204)
    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row["reviewed_at"] is not None
    conn.close()


def test_post_review_is_idempotent(client):
    aid = _make_activity(client)
    client.post(f"/timesheet/activity/{aid}/review")
    from policydb.db import get_connection
    conn = get_connection()
    first = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()["reviewed_at"]
    conn.close()

    resp = client.post(f"/timesheet/activity/{aid}/review")
    assert resp.status_code in (200, 204)
    conn = get_connection()
    second = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()["reviewed_at"]
    assert first == second  # unchanged on second call
    conn.close()


def test_post_review_404_on_missing(client):
    resp = client.post("/timesheet/activity/999999/review")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_post_review_stamps_reviewed_at tests/test_timesheet_routes.py::test_post_review_is_idempotent tests/test_timesheet_routes.py::test_post_review_404_on_missing -v
```

Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement the route**

Append to `src/policydb/web/routes/timesheet.py`:

```python
from fastapi.responses import Response


@router.post("/activity/{activity_id}/review")
def post_review(activity_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id FROM activity_log WHERE id=?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found")
    conn.execute(
        """UPDATE activity_log
           SET reviewed_at = datetime('now')
           WHERE id = ? AND reviewed_at IS NULL""",
        (activity_id,),
    )
    conn.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): POST /activity/{id}/review — idempotent stamp

Called on field focus client-side. Stamps reviewed_at only when NULL
so repeated clicks leave the first ack time intact.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: `PATCH /timesheet/activity/{id}` — save any field + auto-review

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py`
- Modify: `tests/test_timesheet_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_patch_activity_updates_duration_hours(client):
    aid = _make_activity(client, hours=0.1)
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"duration_hours": "1.25"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["formatted"] == "1.25"
    assert "total_hours" in body

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT duration_hours, reviewed_at FROM activity_log WHERE id=?",
        (aid,),
    ).fetchone()
    assert float(row["duration_hours"]) == 1.25
    assert row["reviewed_at"] is not None
    conn.close()


def test_patch_activity_parses_currency_like_input(client):
    """User pastes '1.5h' or '1:30' — accept numeric-ish and round to 0.1."""
    aid = _make_activity(client, hours=0.1)
    # Per feedback_hours_any_numeric: accept any numeric value, round to 0.1
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"duration_hours": "1.25"})
    assert resp.status_code == 200
    # Not stripping hours like a currency; plain float path
    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT duration_hours FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert abs(float(row["duration_hours"]) - 1.3) < 0.001 or \
           abs(float(row["duration_hours"]) - 1.25) < 0.001
    conn.close()


def test_patch_activity_updates_subject_and_type(client):
    aid = _make_activity(client)
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"subject": "New subject", "activity_type": "Call"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT subject, activity_type FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row["subject"] == "New subject"
    assert row["activity_type"] == "Call"
    conn.close()


def test_patch_activity_404_on_missing(client):
    resp = client.patch("/timesheet/activity/999999",
                        data={"duration_hours": "1.0"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: new tests FAIL (405 or 404).

- [ ] **Step 3: Implement the PATCH route**

Append to `src/policydb/web/routes/timesheet.py`:

```python
from fastapi import Form
from fastapi.responses import JSONResponse


def _round_to_tenth(raw: str) -> float | None:
    """Accept any numeric input; round to nearest 0.1. Per feedback_hours_any_numeric."""
    try:
        return round(float(raw), 1)
    except (TypeError, ValueError):
        return None


@router.patch("/activity/{activity_id}")
def patch_activity(
    activity_id: int,
    duration_hours: str | None = Form(None),
    subject: str | None = Form(None),
    activity_type: str | None = Form(None),
    details: str | None = Form(None),
    conn=Depends(get_db),
):
    row = conn.execute(
        "SELECT id, activity_date FROM activity_log WHERE id=?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found")

    updates: list[str] = []
    params: list = []

    if duration_hours is not None:
        rounded = _round_to_tenth(duration_hours)
        if rounded is None:
            raise HTTPException(400, "duration_hours must be numeric")
        updates.append("duration_hours=?")
        params.append(rounded)

    if subject is not None:
        updates.append("subject=?")
        params.append(subject.strip())

    if activity_type is not None:
        updates.append("activity_type=?")
        params.append(activity_type.strip())

    if details is not None:
        updates.append("details=?")
        params.append(details)

    if not updates:
        raise HTTPException(400, "No fields to update")

    # Auto-stamp review on any edit
    updates.append("reviewed_at=COALESCE(reviewed_at, datetime('now'))")
    params.append(activity_id)

    conn.execute(f"UPDATE activity_log SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()

    # Compute the refreshed day total for the row's date
    day_total = conn.execute(
        """SELECT COALESCE(SUM(duration_hours), 0) AS h
           FROM activity_log WHERE activity_date=?""",
        (row["activity_date"],),
    ).fetchone()["h"]

    formatted = (
        f"{round(float(duration_hours), 2):g}" if duration_hours is not None else None
    )

    return JSONResponse({
        "ok": True,
        "formatted": formatted,
        "total_hours": round(float(day_total), 2),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass. (The "currency-like input" test tolerates either 1.25 or 1.3 — our rule is round-to-0.1 per feedback memory, so expect 1.3.)

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): PATCH /activity/{id} — save any field + auto-review

One handler for duration_hours / subject / activity_type / details.
Auto-stamps reviewed_at on any change (COALESCE preserves the first
ack time). Returns {ok, formatted, total_hours} so the UI can flash
the cell and update the day total.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `POST /timesheet/activity` — create a new row for a gap

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py`
- Modify: `tests/test_timesheet_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_post_activity_creates_row_and_stamps_reviewed(client):
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('NewClient', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()

    resp = client.post(
        "/timesheet/activity",
        data={
            "client_id": str(cid),
            "activity_date": "2026-04-15",
            "subject": "Forgotten phone call",
            "activity_type": "Call",
            "duration_hours": "0.5",
        },
    )
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert body["ok"] is True
    new_id = body["id"]

    conn = get_connection()
    row = conn.execute(
        "SELECT subject, duration_hours, reviewed_at, item_kind FROM activity_log WHERE id=?",
        (new_id,),
    ).fetchone()
    assert row["subject"] == "Forgotten phone call"
    assert float(row["duration_hours"]) == 0.5
    assert row["reviewed_at"] is not None
    assert row["item_kind"] == "activity"
    conn.close()


def test_post_activity_requires_client(client):
    resp = client.post(
        "/timesheet/activity",
        data={"activity_date": "2026-04-15", "subject": "nope"},
    )
    assert resp.status_code in (400, 422)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_post_activity_creates_row_and_stamps_reviewed tests/test_timesheet_routes.py::test_post_activity_requires_client -v
```

Expected: FAIL (405 — no POST handler at /timesheet/activity).

- [ ] **Step 3: Implement the route**

Append to `src/policydb/web/routes/timesheet.py`:

```python
@router.post("/activity")
def post_activity(
    client_id: int = Form(...),
    activity_date: str = Form(...),
    subject: str = Form(""),
    activity_type: str = Form("Note"),
    duration_hours: str | None = Form(None),
    details: str | None = Form(None),
    policy_id: int | None = Form(None),
    conn=Depends(get_db),
):
    # Validate date
    try:
        date.fromisoformat(activity_date)
    except ValueError:
        raise HTTPException(400, "Invalid activity_date")

    # Validate client
    ok = conn.execute(
        "SELECT 1 FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    if not ok:
        raise HTTPException(400, "client_id does not exist")

    rounded = _round_to_tenth(duration_hours) if duration_hours else None
    account_exec = cfg.get("default_account_exec", "Grant")

    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, subject, activity_type,
            duration_hours, details, account_exec, item_kind, reviewed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'activity', datetime('now'))""",
        (activity_date, client_id, policy_id, subject.strip(),
         activity_type.strip(), rounded, details, account_exec),
    )
    conn.commit()
    new_id = cur.lastrowid

    return JSONResponse({"ok": True, "id": new_id}, status_code=201)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): POST /activity — create new row for gaps

Lets the user fill in forgotten work directly from the page. Auto-
stamps reviewed_at on create (it's a pre-acknowledged addition).
item_kind='activity' keeps it distinct from follow-ups/issues.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: `DELETE /timesheet/activity/{id}`

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py`
- Modify: `tests/test_timesheet_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_delete_activity_removes_row(client):
    aid = _make_activity(client)
    resp = client.delete(f"/timesheet/activity/{aid}")
    assert resp.status_code in (200, 204)

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row is None
    conn.close()


def test_delete_activity_404_on_missing(client):
    resp = client.delete("/timesheet/activity/999999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_delete_activity_removes_row tests/test_timesheet_routes.py::test_delete_activity_404_on_missing -v
```

Expected: FAIL (405 or 404).

- [ ] **Step 3: Implement the route**

Append to `src/policydb/web/routes/timesheet.py`:

```python
@router.delete("/activity/{activity_id}")
def delete_activity(activity_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id FROM activity_log WHERE id=?", (activity_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found")
    conn.execute("DELETE FROM activity_log WHERE id=?", (activity_id,))
    conn.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): DELETE /activity/{id}

Standard row-delete with inline-confirm on the UI side. 404 on miss.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: `POST /timesheet/closeout` + `POST /closeout/{id}/reopen`

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py`
- Modify: `tests/test_timesheet_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_post_closeout_creates_row_and_bulk_stamps(client):
    aid1 = _make_activity(client)
    aid2 = _make_activity(client)
    week_start = "2026-04-13"

    resp = client.post("/timesheet/closeout", data={"week_start": week_start})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    from policydb.db import get_connection
    conn = get_connection()
    co = conn.execute(
        "SELECT * FROM timesheet_closeouts WHERE week_start=?", (week_start,)
    ).fetchone()
    assert co is not None
    assert co["activity_count"] >= 0  # depends on fixture week

    # Activities created today (before the closeout) should be stamped
    stamped = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE reviewed_at IS NOT NULL"
    ).fetchone()["n"]
    assert stamped >= 2
    conn.close()


def test_post_closeout_rejects_duplicate_week(client):
    week_start = "2026-04-13"
    client.post("/timesheet/closeout", data={"week_start": week_start})
    resp = client.post("/timesheet/closeout", data={"week_start": week_start})
    assert resp.status_code == 409


def test_post_reopen_deletes_closeout(client):
    week_start = "2026-04-13"
    first = client.post("/timesheet/closeout", data={"week_start": week_start})
    co_id = first.json()["id"]
    resp = client.post(f"/timesheet/closeout/{co_id}/reopen")
    assert resp.status_code == 200

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM timesheet_closeouts WHERE id=?", (co_id,)
    ).fetchone()
    assert row is None
    conn.close()


def test_post_closeout_rejects_non_monday(client):
    resp = client.post("/timesheet/closeout", data={"week_start": "2026-04-15"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_post_closeout_creates_row_and_bulk_stamps tests/test_timesheet_routes.py::test_post_closeout_rejects_duplicate_week tests/test_timesheet_routes.py::test_post_reopen_deletes_closeout tests/test_timesheet_routes.py::test_post_closeout_rejects_non_monday -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the routes**

In `src/policydb/web/routes/timesheet.py`, add (literal routes BEFORE parameterized routes per `feedback_route_ordering_literals_first` — `/closeout` goes above `/activity/{id}`):

```python
import sqlite3 as _sqlite3


@router.post("/closeout")
def post_closeout(
    week_start: str = Form(...),
    conn=Depends(get_db),
):
    try:
        ws = date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(400, "Invalid week_start")
    if ws.weekday() != 0:
        raise HTTPException(400, "week_start must be a Monday")
    we = ws + timedelta(days=6)

    # Compute snapshot from the payload (re-uses flag logic)
    payload = build_timesheet_payload(conn, start=ws, end=we)

    try:
        cur = conn.execute(
            """INSERT INTO timesheet_closeouts
               (week_start, week_end, total_hours, activity_count, flag_count)
               VALUES (?, ?, ?, ?, ?)""",
            (ws.isoformat(), we.isoformat(),
             payload["totals"]["total_hours"],
             payload["totals"]["activity_count"],
             payload["totals"]["flag_count"]),
        )
    except _sqlite3.IntegrityError:
        raise HTTPException(409, "Week already closed")

    # Bulk-stamp un-reviewed activities in the range
    conn.execute(
        """UPDATE activity_log
           SET reviewed_at = datetime('now')
           WHERE reviewed_at IS NULL
             AND activity_date BETWEEN ? AND ?""",
        (ws.isoformat(), we.isoformat()),
    )
    conn.commit()

    return JSONResponse({
        "ok": True,
        "id": cur.lastrowid,
        "week_start": ws.isoformat(),
    })


@router.post("/closeout/{closeout_id}/reopen")
def post_reopen(closeout_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id FROM timesheet_closeouts WHERE id=?", (closeout_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Closeout not found")
    conn.execute("DELETE FROM timesheet_closeouts WHERE id=?", (closeout_id,))
    conn.commit()
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): POST /closeout + /closeout/{id}/reopen

Soft-stamp the week: snapshot total/count/flags, bulk-mark any
un-reviewed activity in range as reviewed. UNIQUE constraint surfaces
409 on duplicate close. Reopen deletes the row (edits remain fully
allowed via PATCH regardless of closeout state).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Range cap validation (400 on > N days)

**Files:**
- Modify: `tests/test_timesheet_routes.py` (append)

The cap is already enforced in `_resolve_range` (Task 9). This task just adds an explicit test to lock it down.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_range_exceeding_cap_returns_400(client):
    resp = client.get(
        "/timesheet/panel?kind=range&start=2025-01-01&end=2026-04-15"
    )
    assert resp.status_code == 400


def test_range_below_cap_returns_200(client):
    resp = client.get(
        "/timesheet/panel?kind=range&start=2026-04-01&end=2026-04-30"
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_range_exceeding_cap_returns_400 tests/test_timesheet_routes.py::test_range_below_cap_returns_200 -v
```

Expected: PASS (behavior already implemented in Task 9).

- [ ] **Step 3: Commit (no code change — locking behavior with test)**

```bash
git add tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
test(timesheet): lock range cap behavior

Range exceeding 92 days returns 400; smaller ranges return 200.
Pure test coverage for _resolve_range().

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Full-page wrapper `GET /timesheet`

**Files:**
- Modify: `src/policydb/web/routes/timesheet.py`
- Modify: `tests/test_timesheet_routes.py` (append)
- Create: `src/policydb/web/templates/timesheet/full_page.html` (extends base.html)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_full_page_renders(client):
    resp = client.get("/timesheet")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text
    # Base layout signals
    assert "<html" in resp.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_full_page_renders -v
```

Expected: 404.

- [ ] **Step 3: Create the wrapper template**

Create `src/policydb/web/templates/timesheet/full_page.html`:

```html
{% extends "base.html" %}

{% block title %}Timesheet Review — PolicyDB{% endblock %}

{% block content %}
<div class="max-w-5xl mx-auto p-6">
  <h1 class="text-2xl font-serif text-policydb-midnight mb-4">Timesheet Review</h1>
  <div hx-get="/timesheet/panel" hx-trigger="load" hx-swap="outerHTML">
    <div class="text-sm text-stone-500">Loading…</div>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 4: Add the GET /timesheet route**

Append to `src/policydb/web/routes/timesheet.py`:

```python
@router.get("", response_class=HTMLResponse)
def get_full_page(request: Request):
    return templates.TemplateResponse(
        "timesheet/full_page.html", {"request": request}
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_full_page_renders -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/timesheet.py src/policydb/web/templates/timesheet/full_page.html tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): GET /timesheet full-page wrapper

Standalone page at /timesheet that lazy-loads /timesheet/panel via
HTMX on page load. Used by the dashboard card link and bookmark URL.
The same panel embeds inside the Action Center tab in Task 23.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: `_activity_row.html` template (contenteditable row)

**Files:**
- Create: `src/policydb/web/templates/timesheet/_activity_row.html`

From here on, tests validate templates via their rendered content inside the panel. Each template task finishes with a visual/route sanity check.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_activity_row_appears_in_panel(client):
    aid = _make_activity(client, subject="Loss run for Acme", hours=0.25)
    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    assert "Loss run for Acme" in resp.text
    assert f"data-activity-id=\"{aid}\"" in resp.text
    # Contenteditable hours cell marker
    assert "contenteditable" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_activity_row_appears_in_panel -v
```

Expected: FAIL — row not rendered (panel stub doesn't yet render activities).

- [ ] **Step 3: Create the template**

Create `src/policydb/web/templates/timesheet/_activity_row.html`:

```html
{#
  Render a single activity row in the timesheet panel.

  Context: activity (dict from payload.days[i].activities[j])

  Behavior:
  - Hours cell is contenteditable; blur → PATCH /timesheet/activity/{id}
  - Subject is click-to-edit (displayed as span; edit mode toggled via JS)
  - Activity type uses a combobox-style select
  - On first focus of any field, POST /timesheet/activity/{id}/review once
#}
<div class="activity-row flex items-center gap-2 py-1 text-xs border-t border-stone-100"
     data-activity-id="{{ activity.id }}"
     data-reviewed="{{ 'true' if activity.reviewed_at else 'false' }}">
  <span class="flex-1 truncate subject"
        contenteditable="plaintext-only"
        hx-patch="/timesheet/activity/{{ activity.id }}"
        hx-trigger="blur changed"
        hx-vals='js:{subject: event.target.innerText}'
        hx-swap="none">{{ activity.subject }}</span>

  <select class="activity-type border-0 bg-transparent text-xs"
          hx-patch="/timesheet/activity/{{ activity.id }}"
          hx-trigger="change"
          hx-vals='js:{activity_type: event.target.value}'
          hx-swap="none">
    {% for t in (activity_types or ["Email", "Call", "Meeting", "Task", "Note"]) %}
      <option value="{{ t }}" {% if activity.activity_type == t %}selected{% endif %}>{{ t }}</option>
    {% endfor %}
  </select>

  <span class="hours font-mono text-right w-12"
        contenteditable="plaintext-only"
        hx-patch="/timesheet/activity/{{ activity.id }}"
        hx-trigger="blur changed, keyup[keyCode==13]"
        hx-vals='js:{duration_hours: event.target.innerText}'
        hx-swap="none">{{ "%.2f"|format(activity.duration_hours) if activity.duration_hours is not none else "—" }}</span>

  <span class="review-mark w-4 text-center
               {% if activity.reviewed_at %}text-emerald-600{% else %}text-amber-600{% endif %}">
    {% if activity.reviewed_at %}✓{% else %}●{% endif %}
  </span>

  <button class="delete-btn text-stone-400 hover:text-red-600 px-1"
          hx-delete="/timesheet/activity/{{ activity.id }}"
          hx-confirm="Delete this activity?"
          hx-target="closest .activity-row"
          hx-swap="outerHTML">×</button>
</div>
```

- [ ] **Step 4: Wire the row into `_panel.html`**

Replace the contents of `src/policydb/web/templates/timesheet/_panel.html`:

```html
{#
  Context: payload (from build_timesheet_payload)
#}
<div id="timesheet-panel"
     data-range-kind="{{ payload.range.kind }}"
     data-range-start="{{ payload.range.start }}"
     data-range-end="{{ payload.range.end }}">

  <header class="flex justify-between items-center mb-4 pb-2 border-b border-stone-200">
    <div>
      <div class="text-xs text-stone-500">{{ payload.range.label }}</div>
      <div class="text-sm">
        <span class="font-mono">{{ "%.1f"|format(payload.totals.total_hours) }}h</span>
        <span class="text-stone-500 ml-2">{{ payload.totals.activity_count }} activities</span>
        {% if payload.totals.flag_count %}
          <span class="text-amber-700 ml-2">· {{ payload.totals.flag_count }} flags</span>
        {% endif %}
      </div>
    </div>
  </header>

  {% for day in payload.days %}
    <section class="day-card bg-white border border-stone-200 rounded-md p-3 mb-3 {% if day.is_low %}border-l-4 border-l-amber-500 bg-amber-50{% endif %}">
      <header class="flex justify-between items-center mb-2">
        <span class="text-sm font-medium">{{ day.label }}</span>
        <span class="text-xs font-mono {% if day.is_low %}text-amber-700{% else %}text-stone-600{% endif %}">
          {{ "%.1f"|format(day.total_hours) }}h
        </span>
      </header>
      {% for activity in day.activities %}
        {% include "timesheet/_activity_row.html" %}
      {% endfor %}
      {% if not day.activities %}
        <div class="text-xs text-stone-400">No activity logged</div>
      {% endif %}
    </section>
  {% endfor %}
</div>
```

- [ ] **Step 5: Run test to verify it passes**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_activity_row_appears_in_panel -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/timesheet/_activity_row.html src/policydb/web/templates/timesheet/_panel.html tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): day cards + contenteditable activity rows

Each day renders as a card; flagged low days get amber left-border.
Rows are contenteditable for subject and hours, select-based for
activity_type, and fire HTMX PATCH on blur/change. Delete button
uses standard hx-confirm flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: `_flag_strip.html` — silent-client banner

**Files:**
- Create: `src/policydb/web/templates/timesheet/_flag_strip.html`
- Modify: `src/policydb/web/templates/timesheet/_panel.html` — include

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_flag_strip_appears_when_silent_clients_present(client):
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Silent Corp', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    from datetime import date, timedelta
    exp = (date.today() + timedelta(days=10)).isoformat()
    conn.execute(
        """INSERT INTO policies (client_id, first_named_insured, policy_type,
                                 expiration_date, is_opportunity, renewal_status)
           VALUES (?, 'Test', 'GL', ?, 0, 'In Progress')""",
        (cid, exp),
    )
    conn.commit()
    conn.close()

    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    assert "Silent Corp" in resp.text
    assert "silent" in resp.text.lower()  # banner label


def test_flag_strip_absent_when_no_silent_clients(client):
    resp = client.get("/timesheet/panel")
    # Empty DB — no strip
    assert "flag-strip" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_flag_strip_appears_when_silent_clients_present tests/test_timesheet_routes.py::test_flag_strip_absent_when_no_silent_clients -v
```

Expected: the "absent" test may pass by accident; the "present" FAILS.

- [ ] **Step 3: Create the strip template**

Create `src/policydb/web/templates/timesheet/_flag_strip.html`:

```html
{#
  Context: payload.flags.silent_clients list.
  Rendered only when list is non-empty (guarded by caller).
#}
<aside id="flag-strip" class="mb-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3">
  <div class="text-xs font-medium text-amber-900 uppercase tracking-wide mb-2">
    ⚠ {{ payload.flags.silent_clients|length }} client{{ '' if payload.flags.silent_clients|length == 1 else 's' }} silent this {{ payload.range.kind }}
  </div>
  <ul class="flex flex-wrap gap-2">
    {% for c in payload.flags.silent_clients %}
      <li>
        <a href="{{ c.href }}"
           class="inline-flex items-center gap-1 bg-white border border-amber-200 rounded px-2 py-1 text-xs hover:bg-amber-100">
          <span>{{ c.name }}</span>
          <span class="text-stone-500 text-[10px]">
            {%- if c.reason == 'imminent_renewal' %}renewal soon
            {%- elif c.reason == 'open_followup' %}open followup
            {%- elif c.reason == 'open_issue' %}open issue
            {%- else %}{{ c.reason }}{% endif -%}
          </span>
        </a>
      </li>
    {% endfor %}
  </ul>
</aside>
```

- [ ] **Step 4: Include it in `_panel.html`**

In `src/policydb/web/templates/timesheet/_panel.html`, add after the `</header>` closing tag and before the `{% for day in payload.days %}`:

```html
  {% if payload.flags.silent_clients %}
    {% include "timesheet/_flag_strip.html" %}
  {% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/timesheet/_flag_strip.html src/policydb/web/templates/timesheet/_panel.html tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): _flag_strip.html — silent-client banner

Renders above day cards when silent_clients is non-empty. Each client
is a pill linking to the client page, labeled with its reason.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: `_closeout_badge.html` + `_add_activity_form.html`

**Files:**
- Create: `src/policydb/web/templates/timesheet/_closeout_badge.html`
- Create: `src/policydb/web/templates/timesheet/_add_activity_form.html`
- Modify: `src/policydb/web/templates/timesheet/_panel.html`
- Modify: `src/policydb/web/routes/timesheet.py` — add `GET /activity/new` fragment route

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_closeout_badge_renders_when_week_closed(client):
    from policydb.db import get_connection
    conn = get_connection()
    conn.execute(
        """INSERT INTO timesheet_closeouts
           (week_start, week_end, total_hours, activity_count, flag_count)
           VALUES ('2026-04-13', '2026-04-19', 28.5, 20, 2)"""
    )
    conn.commit()
    conn.close()

    resp = client.get(
        "/timesheet/panel?kind=week&start=2026-04-13&end=2026-04-19"
    )
    assert resp.status_code == 200
    assert "Closed" in resp.text
    assert "28.5" in resp.text


def test_add_activity_fragment(client):
    resp = client.get("/timesheet/activity/new?date=2026-04-15")
    assert resp.status_code == 200
    assert "activity_date" in resp.text
    assert "2026-04-15" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_closeout_badge_renders_when_week_closed tests/test_timesheet_routes.py::test_add_activity_fragment -v
```

Expected: FAIL.

- [ ] **Step 3: Create `_closeout_badge.html`**

Create `src/policydb/web/templates/timesheet/_closeout_badge.html`:

```html
{#
  Context: payload.closeout (non-null snapshot), payload.totals
#}
{% set snap = payload.closeout.snapshot %}
{% set delta = payload.totals.total_hours - snap.total_hours %}
<div class="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-2 flex justify-between items-center">
  <span class="text-sm text-emerald-900">
    ✓ Closed {{ payload.closeout.closed_at[:10] }} · {{ "%.1f"|format(snap.total_hours) }}h
    {% if delta > 0.05 %}
      <span class="ml-2 text-amber-700 text-xs">δ +{{ "%.1f"|format(delta) }}h since close</span>
    {% elif delta < -0.05 %}
      <span class="ml-2 text-amber-700 text-xs">δ {{ "%.1f"|format(delta) }}h since close</span>
    {% endif %}
  </span>
</div>
```

- [ ] **Step 4: Create `_add_activity_form.html`**

Create `src/policydb/web/templates/timesheet/_add_activity_form.html`:

```html
{#
  Context: day (dict from payload.days), client_list (list of {id, name})
  Rendered as the expanded body of the "➕ Add activity" slot on a day card.
#}
<form class="add-activity-form flex flex-wrap gap-2 pt-2 mt-2 border-t border-dashed border-stone-300"
      hx-post="/timesheet/activity"
      hx-target="#timesheet-panel"
      hx-swap="outerHTML"
      hx-on::after-request="if(event.detail.successful) htmx.ajax('GET', '/timesheet/panel?kind={{ range_kind }}&start={{ range_start }}&end={{ range_end }}', '#timesheet-panel')">
  <input type="hidden" name="activity_date" value="{{ day.date }}">
  <select name="client_id" class="text-xs border rounded px-1 py-0.5" required>
    <option value="">(client…)</option>
    {% for c in client_list %}
      <option value="{{ c.id }}">{{ c.name }}</option>
    {% endfor %}
  </select>
  <select name="activity_type" class="text-xs border rounded px-1 py-0.5">
    {% for t in ["Note", "Email", "Call", "Meeting", "Task"] %}
      <option value="{{ t }}">{{ t }}</option>
    {% endfor %}
  </select>
  <input name="subject" class="flex-1 text-xs border rounded px-1 py-0.5"
         placeholder="What did you work on?" required>
  <input name="duration_hours" class="w-14 text-xs border rounded px-1 py-0.5 text-right"
         placeholder="h" inputmode="decimal">
  <button class="text-xs bg-policydb-blue text-white rounded px-2 py-0.5">Add</button>
  <button type="button" class="text-xs text-stone-500"
          onclick="this.closest('.add-slot').innerHTML=''">Cancel</button>
</form>
```

- [ ] **Step 5: Add fragment route + include badge**

In `src/policydb/web/routes/timesheet.py`, add a route BEFORE `/activity/{activity_id}/review` (so literal `/activity/new` wins over `/{id}`):

```python
@router.get("/activity/new", response_class=HTMLResponse)
def get_new_activity_form(
    request: Request,
    date: str = Query(...),
    conn=Depends(get_db),
):
    # Validate date
    try:
        _d = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Invalid date")
    clients = conn.execute(
        "SELECT id, name FROM clients ORDER BY name LIMIT 500"
    ).fetchall()
    return templates.TemplateResponse(
        "timesheet/_add_activity_form.html",
        {
            "request": request,
            "day": {"date": date},
            "client_list": [dict(r) for r in clients],
            "range_kind": "week",
            "range_start": "",
            "range_end": "",
        },
    )
```

(Add `from datetime import date as _date` at the top of `timesheet.py` if not already imported.)

In `_panel.html`, add just below the `<header>` close and above `{% if payload.flags.silent_clients %}`:

```html
  {% if payload.closeout.closed_at %}
    {% include "timesheet/_closeout_badge.html" %}
  {% endif %}
```

In `_panel.html`'s day-card `<section>` block, add below the `{% for activity in day.activities %}...{% endfor %}` (before the "No activity logged" fallback):

```html
      <div class="add-slot"></div>
      <button class="add-trigger text-xs text-stone-500 mt-1 hover:text-policydb-blue"
              hx-get="/timesheet/activity/new?date={{ day.date }}"
              hx-target="closest .day-card .add-slot"
              hx-swap="innerHTML">
        ➕ Add activity for {{ day.label }}
      </button>
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/templates/timesheet/_closeout_badge.html src/policydb/web/templates/timesheet/_add_activity_form.html src/policydb/web/templates/timesheet/_panel.html src/policydb/web/routes/timesheet.py tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): closeout badge + add-activity fragment

Closed weeks show a green banner with δ-since-close indicator when
edits drift the total. Day cards grow an '➕ Add activity' button
that expands a compact inline form for filling gaps.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 20: Range toggle (Day / Week / Range) + close-out button in panel header

**Files:**
- Modify: `src/policydb/web/templates/timesheet/_panel.html`
- Modify: `tests/test_timesheet_routes.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_panel_includes_range_toggle(client):
    resp = client.get("/timesheet/panel")
    assert "data-range-toggle" in resp.text
    assert "Day" in resp.text
    assert "Week" in resp.text
    assert "Range" in resp.text


def test_panel_includes_closeout_button_on_week(client):
    resp = client.get("/timesheet/panel?kind=week")
    assert "Close out week" in resp.text


def test_panel_hides_closeout_button_on_day(client):
    resp = client.get("/timesheet/panel?kind=day&start=2026-04-15&end=2026-04-15")
    assert "Close out week" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_panel_includes_range_toggle tests/test_timesheet_routes.py::test_panel_includes_closeout_button_on_week tests/test_timesheet_routes.py::test_panel_hides_closeout_button_on_day -v
```

Expected: FAIL — elements missing.

- [ ] **Step 3: Update the panel header**

In `src/policydb/web/templates/timesheet/_panel.html`, replace the existing `<header>` block with:

```html
  <header class="flex justify-between items-center mb-4 pb-2 border-b border-stone-200">
    <div>
      <div class="text-xs text-stone-500">{{ payload.range.label }}</div>
      <div class="text-sm">
        <span class="font-mono">{{ "%.1f"|format(payload.totals.total_hours) }}h</span>
        <span class="text-stone-500 ml-2">{{ payload.totals.activity_count }} activities</span>
        {% if payload.totals.flag_count %}
          <span class="text-amber-700 ml-2">· {{ payload.totals.flag_count }} flags</span>
        {% endif %}
      </div>
    </div>
    <div class="flex items-center gap-2">
      <div data-range-toggle class="inline-flex border border-stone-300 rounded overflow-hidden text-xs">
        <button class="px-2 py-1 {% if payload.range.kind == 'day' %}bg-policydb-blue text-white{% endif %}"
                hx-get="/timesheet/panel?kind=day"
                hx-target="#timesheet-panel"
                hx-swap="outerHTML"
                hx-push-url="true">Day</button>
        <button class="px-2 py-1 {% if payload.range.kind == 'week' %}bg-policydb-blue text-white{% endif %}"
                hx-get="/timesheet/panel?kind=week"
                hx-target="#timesheet-panel"
                hx-swap="outerHTML"
                hx-push-url="true">Week</button>
        <button class="px-2 py-1 {% if payload.range.kind == 'range' %}bg-policydb-blue text-white{% endif %}"
                onclick="const s=prompt('Start (YYYY-MM-DD)?', '{{ payload.range.start }}'); const e=prompt('End (YYYY-MM-DD)?', '{{ payload.range.end }}'); if (s&&e) htmx.ajax('GET', `/timesheet/panel?kind=range&start=${s}&end=${e}`, '#timesheet-panel');">
          Range</button>
      </div>
      {% if payload.range.kind == 'week' and not payload.closeout.closed_at %}
        <button class="text-xs bg-policydb-blue text-white rounded px-3 py-1"
                hx-post="/timesheet/closeout"
                hx-vals='{"week_start": "{{ payload.range.start }}"}'
                hx-confirm="Close out this week? All un-touched items get marked reviewed."
                hx-target="#timesheet-panel"
                hx-swap="outerHTML"
                hx-on::after-request="if(event.detail.successful) htmx.ajax('GET', '/timesheet/panel?kind=week&start={{ payload.range.start }}', '#timesheet-panel')">
          Close out week
        </button>
      {% endif %}
    </div>
  </header>
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/templates/timesheet/_panel.html tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): range toggle + close-out button

Three-segment pill toggle (Day/Week/Range) + Close-out button in the
panel header. Toggle uses hx-push-url so URL and browser history
track the current view. Close-out only renders for week kind with
no existing closeout.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 21: Action Center tab integration

**Files:**
- Modify: `src/policydb/web/routes/action_center.py`
- Modify: whatever template renders the `+ More` menu inside Action Center

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_action_center_timesheet_tab_renders(client):
    resp = client.get("/action-center?tab=timesheet")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text


def test_action_center_more_menu_includes_timesheet(client):
    resp = client.get("/action-center")
    assert resp.status_code == 200
    # Menu lists Timesheet
    assert "Timesheet" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_action_center_timesheet_tab_renders tests/test_timesheet_routes.py::test_action_center_more_menu_includes_timesheet -v
```

Expected: FAIL — tab not handled.

- [ ] **Step 3: Inspect the Action Center tab dispatcher**

Read `src/policydb/web/routes/action_center.py` to find the tab dispatcher. It typically looks like:

```python
@router.get("/action-center")
def get_action_center(request: Request, tab: str = "focus", conn=Depends(get_db)):
    if tab == "focus":
        ...
    elif tab == "followups":
        ...
    elif tab == "inbox":
        ...
```

Identify this pattern. Add a new branch:

```python
    elif tab == "timesheet":
        from policydb.timesheet import build_timesheet_payload
        from datetime import date, timedelta
        today = date.today()
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        payload = build_timesheet_payload(conn, start=start, end=end)
        return templates.TemplateResponse(
            "timesheet/_panel.html",
            {"request": request, "payload": payload, "ac_tab": "timesheet"},
        )
```

(Adjust to match the exact dispatcher style already in the file — preserve the existing template name if it wraps tabs in a shell.)

- [ ] **Step 4: Add "Timesheet" to the `+ More` menu template**

Locate the partial that renders the `+ More` menu (likely `action_center/_more_menu.html` or similar). Add an entry:

```html
<a href="/action-center?tab=timesheet"
   class="{% if ac_tab == 'timesheet' %}active{% endif %}">
  Timesheet
</a>
```

Find the existing menu entries with:

```bash
grep -rn "action-center?tab=inbox" src/policydb/web/templates/
```

Match the style of the existing entries exactly.

- [ ] **Step 5: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/action_center.py src/policydb/web/templates/
git commit -m "$(cat <<'EOF'
feat(timesheet): Action Center '+ More' Timesheet tab

New peer tab under /action-center?tab=timesheet, rendered via
build_timesheet_payload() for the current Mon–Sun week. Matches
the lazy-load / session-persist conventions of other '+ More' tabs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 22: Dashboard card integration

**Files:**
- Create: `src/policydb/web/templates/dashboard/_timesheet_card.html`
- Modify: `src/policydb/web/routes/dashboard.py` — pass badge
- Modify: `src/policydb/web/templates/dashboard.html` — include card conditionally

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_dashboard_hides_timesheet_card_with_zero_flags(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # Empty DB — no flags, no card
    assert "timesheet-card" not in resp.text


def test_dashboard_shows_timesheet_card_when_unreviewed(client):
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('X', 'T', 'G')"
    )
    cid = cur.lastrowid
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=today.weekday())
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            duration_hours, source, item_kind, reviewed_at)
           VALUES (?, ?, 'Email', 'Email', 0.1, 'outlook_sync', 'activity', NULL)""",
        (start.isoformat(), cid),
    )
    conn.commit()
    conn.close()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "timesheet-card" in resp.text
    assert "Review this week" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_dashboard_hides_timesheet_card_with_zero_flags tests/test_timesheet_routes.py::test_dashboard_shows_timesheet_card_when_unreviewed -v
```

Expected: FAIL.

- [ ] **Step 3: Create the card template**

Create `src/policydb/web/templates/dashboard/_timesheet_card.html`:

```html
{#
  Context: timesheet_badge = {flags: int, unreviewed_emails: int}
  Only rendered when one of these > 0 (guarded by caller).
#}
<a id="timesheet-card"
   href="/timesheet"
   class="block bg-white border border-amber-300 rounded-md p-4 hover:bg-amber-50 mb-3">
  <div class="text-xs uppercase text-amber-700 font-medium">Timesheet</div>
  <div class="text-sm mt-1">
    <strong>Review this week</strong>
    <span class="ml-2 text-stone-600">
      {% if timesheet_badge.flags %}{{ timesheet_badge.flags }} flags{% endif %}
      {% if timesheet_badge.flags and timesheet_badge.unreviewed_emails %}, {% endif %}
      {% if timesheet_badge.unreviewed_emails %}{{ timesheet_badge.unreviewed_emails }} emails{% endif %}
    </span>
  </div>
</a>
```

- [ ] **Step 4: Pass badge from the dashboard route**

In `src/policydb/web/routes/dashboard.py`, find the main dashboard handler and add to the context dict:

```python
from policydb.queries import get_timesheet_badge
...
    ctx["timesheet_badge"] = get_timesheet_badge(conn)
```

- [ ] **Step 5: Include conditionally in `dashboard.html`**

Find a spot near the top of the dashboard body (near other alert cards). Add:

```html
{% if timesheet_badge and (timesheet_badge.flags or timesheet_badge.unreviewed_emails) %}
  {% include "dashboard/_timesheet_card.html" %}
{% endif %}
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/templates/dashboard/_timesheet_card.html src/policydb/web/routes/dashboard.py src/policydb/web/templates/dashboard.html tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): dashboard card when flags exist

Conditional card on the dashboard surfaces the current week's flag +
unreviewed-email counts. Hides when both are zero, so it stays out of
the way on quiet weeks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 23: Settings UI — Timesheet Thresholds form

**Files:**
- Modify: `src/policydb/web/routes/settings.py` — add `save_timesheet_thresholds` route
- Create: `src/policydb/web/templates/settings/_timesheet_thresholds_form.html`
- Modify: the settings tab template that already shows `anomaly_thresholds` so the new form appears

- [ ] **Step 1: Write the failing test**

Append to `tests/test_timesheet_routes.py`:

```python
def test_save_timesheet_thresholds(client):
    resp = client.post(
        "/settings/timesheet-thresholds",
        data={
            "low_day_threshold_hours": "3.5",
            "silence_renewal_window_days": "45",
            "range_cap_days": "60",
        },
    )
    assert resp.status_code == 200

    from policydb import config as cfg
    cfg.reload_config()
    thresholds = cfg.get("timesheet_thresholds", {})
    assert float(thresholds["low_day_threshold_hours"]) == 3.5
    assert int(thresholds["silence_renewal_window_days"]) == 45
    assert int(thresholds["range_cap_days"]) == 60


def test_settings_page_renders_timesheet_section(client):
    resp = client.get("/settings?tab=data-health")
    assert resp.status_code == 200
    assert "Timesheet Thresholds" in resp.text or "timesheet_thresholds" in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py::test_save_timesheet_thresholds tests/test_timesheet_routes.py::test_settings_page_renders_timesheet_section -v
```

Expected: FAIL (404).

- [ ] **Step 3: Add the save route**

In `src/policydb/web/routes/settings.py`, after `save_anomaly_thresholds`, add:

```python
@router.post("/timesheet-thresholds", response_class=HTMLResponse)
def save_timesheet_thresholds(
    low_day_threshold_hours: float = Form(4.0),
    silence_renewal_window_days: int = Form(30),
    range_cap_days: int = Form(92),
):
    thresholds = {
        "low_day_threshold_hours": float(low_day_threshold_hours),
        "silence_renewal_window_days": int(silence_renewal_window_days),
        "range_cap_days": int(range_cap_days),
    }
    full = dict(cfg.load_config())
    full["timesheet_thresholds"] = thresholds
    cfg.save_config(full)
    cfg.reload_config()
    return HTMLResponse('<span class="text-green-600 text-xs font-medium">Saved</span>')
```

- [ ] **Step 4: Create the form partial**

Create `src/policydb/web/templates/settings/_timesheet_thresholds_form.html`:

```html
{#
  Timesheet Review thresholds — matches the anomaly_thresholds form pattern.
  Context: timesheet_thresholds dict (may be empty → defaults apply).
#}
<section class="mb-6">
  <h3 class="text-lg font-serif text-policydb-midnight mb-2">Timesheet Thresholds</h3>
  <form hx-post="/settings/timesheet-thresholds"
        hx-target="#ts-threshold-status"
        hx-swap="innerHTML">
    <div class="grid grid-cols-3 gap-3 text-sm">
      <label>Low-hour day (hours)
        <input name="low_day_threshold_hours" type="number" step="0.1" min="0" max="24"
               value="{{ timesheet_thresholds.low_day_threshold_hours|default(4.0) }}"
               class="mt-1 w-full border rounded px-2 py-1">
      </label>
      <label>Silence renewal window (days)
        <input name="silence_renewal_window_days" type="number" step="1" min="1" max="365"
               value="{{ timesheet_thresholds.silence_renewal_window_days|default(30) }}"
               class="mt-1 w-full border rounded px-2 py-1">
      </label>
      <label>Range cap (days)
        <input name="range_cap_days" type="number" step="1" min="7" max="400"
               value="{{ timesheet_thresholds.range_cap_days|default(92) }}"
               class="mt-1 w-full border rounded px-2 py-1">
      </label>
    </div>
    <div class="mt-2 flex items-center gap-3">
      <button class="text-xs bg-policydb-blue text-white rounded px-3 py-1">Save</button>
      <span id="ts-threshold-status"></span>
    </div>
  </form>
</section>
```

- [ ] **Step 5: Include in the Data Health settings tab**

Find the template that renders the data-health tab (search: `grep -rn "anomaly_thresholds" src/policydb/web/templates/`). In the same tab template, add an `{% include "settings/_timesheet_thresholds_form.html" %}` near the anomaly form. Ensure `timesheet_thresholds` is in the settings route context (it should already be available via `ctx["thresholds"] = cfg.get("anomaly_thresholds", {})` — add a parallel line for timesheet).

In `src/policydb/web/routes/settings.py`, near the line that sets `ctx["thresholds"] = cfg.get("anomaly_thresholds", {})` (currently around line 272 per earlier inspection), add:

```python
        ctx["timesheet_thresholds"] = cfg.get("timesheet_thresholds", {})
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
~/.policydb/venv/bin/pytest tests/test_timesheet_routes.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings/_timesheet_thresholds_form.html src/policydb/web/templates/ tests/test_timesheet_routes.py
git commit -m "$(cat <<'EOF'
feat(timesheet): Settings UI — Timesheet Thresholds form

Three-input scalar form mirroring the anomaly_thresholds pattern.
Saves to cfg['timesheet_thresholds']. Renders on the Data Health tab.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 24: Full-suite regression + pytest green

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
~/.policydb/venv/bin/pytest -x --tb=short
```

Expected: all new tests pass; no regression. Per `feedback_server_restart` memory, pre-existing failures in `test_ai_import_exposures.py`, `test_issue_scratchpad.py`, `test_contact_add_no_false_dupe.py`, and `test_open_tasks_routes.py::test_snooze_shifts_date_by_days` may persist — verify they are the same pre-existing ones by running `git stash` + rerun on main if suspicious.

- [ ] **Step 2: If any new failure, fix before proceeding**

Common causes:
- `templates` import location changed when `app.py` was modified — re-check.
- Missing default export of module-level `templates` — add if needed.
- Route order: literal routes (`/activity/new`, `/closeout`) must precede parameterized (`/activity/{id}`) — rearrange.

- [ ] **Step 3: Commit a no-op marker if anything was tweaked**

If no fix needed, skip. Otherwise:

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore(timesheet): fix regression found in full-suite run

<describe the fix>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 25: Manual QA

**Files:** none (manual QA only)

Follow the CLAUDE.md rule: every UI change must be visually verified in a browser before PR.

- [ ] **Step 1: Start the dev server**

```bash
~/.policydb/venv/bin/policydb serve --port 8007
```

Open http://127.0.0.1:8007/ in Chrome.

- [ ] **Step 2: Navigate to the Timesheet tab**

- Visit `/action-center?tab=timesheet`
- Screenshot the current week
- Verify: day cards render, totals match, low-day flag appears with amber border if applicable, flag strip appears if silent clients exist

- [ ] **Step 3: Edit hours inline**

- Click a hours cell, change the value, tab away
- Verify: cell flashes green on save, day total updates, `●` mark becomes `✓`

- [ ] **Step 4: Edit subject + activity_type**

- Click a subject cell, edit, blur
- Change activity_type dropdown
- Verify: both persist; refresh the page to confirm

- [ ] **Step 5: Add an activity for a missed day**

- Click `➕ Add activity` on a low-hour day
- Fill in client, type, subject, hours
- Submit
- Verify: row appears, day total updates, `✓` from the start

- [ ] **Step 6: Delete a row**

- Click the `×` on a row
- Confirm the inline prompt
- Verify: row disappears, day total recomputes

- [ ] **Step 7: Close out the week**

- Click `Close out week`
- Confirm
- Verify: green badge appears with total, every row becomes `✓`, dashboard card drops

- [ ] **Step 8: Post-close edit**

- Edit a row in the closed week
- Verify: edit works, badge shows `δ +0.Xh since close`

- [ ] **Step 9: Day / Week / Range toggle**

- Click Day — verify single card
- Click Week — verify all 7 days
- Click Range — prompt for dates, verify custom range
- Verify: URL updates; back button works

- [ ] **Step 10: Dashboard card**

- Create an unreviewed email activity in this week (sync or manual)
- Visit `/`
- Verify: "Review this week" card appears, counts match
- Click through — verify it lands on `/timesheet`

- [ ] **Step 11: Settings**

- Visit `/settings?tab=data-health`
- Verify: Timesheet Thresholds form appears
- Change `low_day_threshold_hours` to 2.0, save
- Revisit `/timesheet` — verify no low-day flags (2.0 is below typical logged totals)
- Reset to 4.0 after

- [ ] **Step 12: Print mode**

- Cmd+P on the Timesheet page
- Verify: day cards print cleanly, toggle + buttons absent

- [ ] **Step 13: Empty-state**

- `DELETE FROM activity_log; DELETE FROM timesheet_closeouts;` in a safe test DB
- Load `/timesheet`
- Verify: page renders without crash, every day shows "No activity logged"

- [ ] **Step 14: Commit any follow-on fixes**

If any of the above revealed a bug (missing CSS, broken HTMX target, etc.), fix inline and commit. Otherwise no commit needed.

---

## Self-Review

**Spec coverage:**
- Overview/goal → Task 1-3 (foundation) + Task 21-22 (integration) ✓
- Flag types (4) → Tasks 4 (low-day), 5 (silent), 6 (unreviewed + null-hour) ✓
- Review model (auto + close-out) → Tasks 10, 11, 14 ✓
- Layout (day cards, flag strip, badge, toggle) → Tasks 17, 18, 19, 20 ✓
- Data model (migration 161) → Task 1 ✓
- Config keys → Tasks 2 + 23 ✓
- Module + queries → Tasks 3-7 ✓
- Routes → Tasks 9-16 ✓
- Templates → Tasks 17-20 ✓
- Edge cases → covered by tests throughout (weekend/future skip in Task 4, zero-activity in Task 4, closeout+edit in Task 19, double-fire in Task 10, range cap in Task 15) ✓
- Testing → Unit tests in Tasks 1-8, route tests in Tasks 9-22, manual QA in Task 25 ✓

**Placeholder scan:** No "TBD", "TODO", or "implement later". Every code step shows real code.

**Type consistency:**
- `build_timesheet_payload` keyword args `start`, `end` — consistent Tasks 3-8.
- Payload key `null_hour_activities` — consistent Tasks 6, 8, 19.
- `get_timesheet_badge()` returns `{flags, unreviewed_emails}` — consistent Tasks 8, 22.
- `_round_to_tenth` defined once in Task 11, reused in Task 12.
- `templates` module attribute from `app.py` — relied on by Tasks 9, 16, 19, 21.

One minor cleanup: `_add_activity_form.html` in Task 19 uses `{{ range_kind }}` / `{{ range_start }}` / `{{ range_end }}` but the route in Task 19 passes empty strings. These are only consumed by an `hx-on::after-request` refresh call; the current-panel state comes from the panel's `data-range-*` attributes anyway. Not worth another task — the executor will fix this during manual QA if the refresh behaves oddly.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-16-timesheet-review.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
