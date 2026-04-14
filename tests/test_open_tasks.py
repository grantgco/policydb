"""Tests for Open Tasks panel: sync helpers, creation helper, get_open_tasks."""
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


def _seed_client(conn, name="Sync Test Co"):
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES (?, 'Test')", (name,))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_policy(conn, client_id, uid="POL-001"):
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date) "
        "VALUES (?, ?, 'GL', 'Test Carrier', '2026-01-01', '2027-01-01')",
        (uid, client_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_followup(conn, client_id, policy_id, subject, fu_date, done=0):
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', ?, ?, ?, 'followup', 'Grant')",
        (date.today().isoformat(), client_id, policy_id, subject, fu_date, done),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── sync_policy_follow_up_date ────────────────────────────────────────────────

def test_sync_policy_fu_date_sets_earliest_open(tmp_db):
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "later", "2026-05-10")
    _insert_followup(conn, cid, pid, "earlier", "2026-05-01")
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] == "2026-05-01"


def test_sync_policy_fu_date_clears_when_no_open(tmp_db):
    from policydb.queries import sync_policy_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    pid = _seed_policy(conn, cid)
    _insert_followup(conn, cid, pid, "done", "2026-05-01", done=1)
    conn.execute("UPDATE policies SET follow_up_date='2026-05-01' WHERE id=?", (pid,))
    conn.commit()

    sync_policy_follow_up_date(conn, pid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM policies WHERE id=?", (pid,)).fetchone()
    assert row["follow_up_date"] is None


def test_sync_client_fu_date_sets_earliest_open(tmp_db):
    from policydb.queries import sync_client_follow_up_date
    conn = get_connection()
    cid = _seed_client(conn)
    # Client-level follow-ups have policy_id = NULL
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'direct1', '2026-06-10', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, NULL, 'Call', 'direct2', '2026-06-01', 0, 'followup', 'Grant')",
        (date.today().isoformat(), cid),
    )
    conn.commit()

    sync_client_follow_up_date(conn, cid)
    conn.commit()

    row = conn.execute("SELECT follow_up_date FROM clients WHERE id=?", (cid,)).fetchone()
    assert row["follow_up_date"] == "2026-06-01"
