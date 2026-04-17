"""Tests for the timesheet module and schema."""

import sqlite3
from datetime import date, timedelta

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
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO timesheet_closeouts
               (week_start, week_end, total_hours, activity_count, flag_count)
               VALUES (?, ?, ?, ?, ?)""",
            ("2026-04-13", "2026-04-19", 28.0, 18, 3),
        )
        conn.commit()
    conn.close()


def test_timesheet_thresholds_default():
    from policydb import config as cfg
    thresholds = cfg.get("timesheet_thresholds", {})
    assert thresholds.get("low_day_threshold_hours") == 4.0
    assert thresholds.get("silence_renewal_window_days") == 30
    assert thresholds.get("range_cap_days") == 92


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
    assert len(by_date["2026-04-13"]["activities"]) == 1
    assert len(by_date["2026-04-14"]["activities"]) == 1
    assert payload["flags"]["low_days"] == ["2026-04-13"]
    conn.close()


def test_low_day_flag_ignores_weekend(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)
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
