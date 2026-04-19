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


def test_migration_161_adds_reviewed_at_column(tmp_db):
    conn = get_connection(tmp_db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(activity_log)").fetchall()}
    assert "reviewed_at" in cols
    conn.close()


def test_migration_161_creates_timesheet_closeouts(tmp_db):
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


def test_migration_161_partial_index_on_reviewed_at(tmp_db):
    conn = get_connection(tmp_db)
    idxs = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='activity_log'"
    ).fetchall()}
    assert "idx_activity_log_reviewed_at" in idxs
    conn.close()


def test_migration_161_closeouts_unique_week_start(tmp_db):
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


def _seed_policy(conn, *, client_id, expiration_date, is_opportunity=0):
    from policydb.db import next_policy_uid
    uid = next_policy_uid(conn)
    cur = conn.execute(
        """INSERT INTO policies (policy_uid, client_id, first_named_insured, policy_type,
                                 expiration_date, is_opportunity, renewal_status)
           VALUES (?, ?, 'Test Ins', 'General Liability', ?, ?, 'In Progress')""",
        (uid, client_id, expiration_date, is_opportunity),
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
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload

    cid = _seed_client(conn, "Dormant Corp")
    start = date.today() - timedelta(days=date.today().weekday())
    payload = build_timesheet_payload(conn, start=start, end=start + timedelta(days=6))

    names = {c["name"] for c in payload["flags"]["silent_clients"]}
    assert "Dormant Corp" not in names
    conn.close()


def test_unreviewed_emails_count(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn)

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

    _seed_activity(conn, client_id=cid, activity_date="2026-04-14", duration_hours=None)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-15", duration_hours=None)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-14", duration_hours=1.0)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-15", duration_hours=0.5)
    _seed_activity(conn, client_id=cid, activity_date="2026-04-16", duration_hours=2.0)

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 19),
    )
    assert payload["flags"]["null_hour_activities"] == 2
    assert payload["totals"]["flag_count"] >= 2
    conn.close()


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


def test_load_activities_includes_context_fields(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import _load_activities
    cid = _seed_client(conn, "Acme")
    pid = _seed_policy(conn, client_id=cid, expiration_date="2026-12-31")
    conn.execute(
        "INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')",
        (cid,),
    )
    prj_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    iss_id = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            item_kind, issue_uid, follow_up_done)
           VALUES ('2026-04-13', ?, 'WC audit dispute', 'Issue',
                   'issue', 'ISS-001', 0)""",
        (cid,),
    ).lastrowid
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, project_id, issue_id,
            subject, activity_type, duration_hours, item_kind)
           VALUES ('2026-04-13', ?, ?, ?, ?, 'Follow up', 'Call', 0.5,
                   'activity')""",
        (cid, pid, prj_id, iss_id),
    )
    conn.commit()

    rows = _load_activities(conn, date(2026, 4, 13), date(2026, 4, 13))
    assert len(rows) == 2
    work_row = next(r for r in rows if r["item_kind"] == "activity")
    assert work_row["client_name"] == "Acme"
    assert work_row["policy_uid"] is not None
    assert work_row["project_name"] == "Plant 3"
    assert work_row["issue_uid"] == "ISS-001"
    assert work_row["issue_subject"] == "WC audit dispute"
    conn.close()


def test_build_payload_exposes_context_hrefs(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.timesheet import build_timesheet_payload
    cid = _seed_client(conn, "Acme")
    pid = _seed_policy(conn, client_id=cid, expiration_date="2026-12-31")
    conn.execute(
        "INSERT INTO projects (client_id, name) VALUES (?, 'Plant 3')",
        (cid,),
    )
    prj_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, project_id,
            subject, activity_type, duration_hours, item_kind)
           VALUES ('2026-04-13', ?, ?, ?, 'Follow up', 'Call', 0.5, 'activity')""",
        (cid, pid, prj_id),
    )
    conn.commit()

    payload = build_timesheet_payload(
        conn, start=date(2026, 4, 13), end=date(2026, 4, 13),
    )
    act = payload["days"][0]["activities"][0]
    assert act["client_name"] == "Acme"
    assert act["client_href"] == f"/clients/{cid}"
    assert act["policy_uid"].startswith("POL-")
    assert act["policy_href"] == f"/policies/{act['policy_uid']}/edit"
    assert act["project_name"] == "Plant 3"
    assert act["project_href"] == f"/clients/{cid}/projects/{prj_id}"
    assert act["issue_uid"] is None
    assert act["issue_href"] is None
    conn.close()


def test_get_timesheet_badge(tmp_db):
    conn = get_connection(tmp_db)
    from policydb.queries import get_timesheet_badge
    cid = _seed_client(conn)

    start = date.today() - timedelta(days=date.today().weekday())
    _seed_activity(conn, client_id=cid,
                   activity_date=start.isoformat(),
                   duration_hours=0.1, source="outlook_sync")
    _seed_activity(conn, client_id=cid,
                   activity_date=start.isoformat(),
                   duration_hours=0.1, source="outlook_sync")
    cid2 = _seed_client(conn, "Silent B")
    _seed_policy(conn, client_id=cid2,
                 expiration_date=(date.today() + timedelta(days=5)).isoformat())

    badge = get_timesheet_badge(conn)
    assert isinstance(badge, dict)
    assert badge["unreviewed_emails"] == 2
    assert badge["flags"] >= 1
    conn.close()
