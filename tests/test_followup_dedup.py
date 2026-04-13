"""Tests for Focus Queue follow-up dedup invariants.

Covers:
1. ``activity_complete`` closes sibling pending follow-ups on the same policy.
2. ``_dedup_activity_siblings`` keeps only the most recent follow-up per policy.
3. ``activity_abandon`` marks the row done with an ``[Abandoned]`` prefix and
   closes siblings.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from policydb.db import get_connection, init_db
from policydb.focus_queue import _dedup_activity_siblings


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


@pytest.fixture
def app_client(tmp_db):
    from policydb.web.app import app
    with TestClient(app) as client:
        yield client


def _seed_client_and_policy(conn):
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Dedup Co', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date) "
        "VALUES ('POL-001', ?, 'GL', 'Test Carrier', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return cid, pid


def _insert_followup(conn, client_id, policy_id, subject, follow_up_date):
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', ?, ?, 0, 'followup', 'Grant')",
        (date.today().isoformat(), client_id, policy_id, subject, follow_up_date),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── _dedup_activity_siblings ──────────────────────────────────────────────────


def test_dedup_siblings_keeps_earliest_date():
    items = [
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 1, "follow_up_date": "2026-04-01"},
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 2, "follow_up_date": "2026-04-10"},
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 3, "follow_up_date": "2026-03-28"},
    ]
    kept, dropped = _dedup_activity_siblings(items)
    assert dropped == 2
    assert len(kept) == 1
    assert kept[0]["id"] == 3  # earliest follow_up_date wins — most urgent


def test_dedup_siblings_breaks_tie_on_id():
    items = [
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 1, "follow_up_date": "2026-04-01"},
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 5, "follow_up_date": "2026-04-01"},
    ]
    kept, dropped = _dedup_activity_siblings(items)
    assert dropped == 1
    assert kept[0]["id"] == 5  # on date tie, most recently written wins


def test_dedup_siblings_prefers_overdue_over_future():
    """The overdue item must survive so it doesn't get nullified by horizon filter."""
    items = [
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 1, "follow_up_date": "2026-04-01"},  # overdue
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 2, "follow_up_date": "2099-04-01"},  # far future
    ]
    kept, dropped = _dedup_activity_siblings(items)
    assert dropped == 1
    assert kept[0]["id"] == 1


def test_dedup_siblings_leaves_other_policies_alone():
    items = [
        {"kind": "followup", "source": "activity", "policy_uid": "POL-001",
         "id": 1, "follow_up_date": "2026-04-01"},
        {"kind": "followup", "source": "activity", "policy_uid": "POL-002",
         "id": 2, "follow_up_date": "2026-04-01"},
    ]
    kept, dropped = _dedup_activity_siblings(items)
    assert dropped == 0
    assert len(kept) == 2


def test_dedup_siblings_ignores_non_followup_kinds():
    items = [
        {"kind": "issue", "source": "issue", "policy_uid": "POL-001",
         "id": 1, "follow_up_date": "2026-04-01"},
        {"kind": "milestone", "source": "milestone", "policy_uid": "POL-001",
         "id": 2, "follow_up_date": "2026-04-01"},
    ]
    kept, dropped = _dedup_activity_siblings(items)
    assert dropped == 0
    assert len(kept) == 2


def test_dedup_siblings_no_policy_uid_passthrough():
    items = [
        {"kind": "followup", "source": "activity", "policy_uid": None,
         "id": 1, "follow_up_date": "2026-04-01"},
        {"kind": "followup", "source": "activity", "policy_uid": None,
         "id": 2, "follow_up_date": "2026-04-01"},
    ]
    kept, dropped = _dedup_activity_siblings(items)
    assert dropped == 0
    assert len(kept) == 2


# ── /activities/{id}/complete supersede ───────────────────────────────────────


def test_activity_complete_supersedes_siblings(tmp_db, app_client):
    conn = get_connection(tmp_db)
    cid, pid = _seed_client_and_policy(conn)
    a1 = _insert_followup(conn, cid, pid, "FU #1", "2026-04-01")
    a2 = _insert_followup(conn, cid, pid, "FU #2", "2026-04-08")
    a3 = _insert_followup(conn, cid, pid, "FU #3", "2026-04-15")
    conn.commit()
    conn.close()

    # Complete the middle one
    resp = app_client.post(f"/activities/{a2}/complete", data={"duration_hours": 0})
    assert resp.status_code == 200

    conn = get_connection(tmp_db)
    rows = conn.execute(
        "SELECT id, follow_up_done, auto_close_reason, auto_closed_by FROM activity_log "
        "WHERE id IN (?, ?, ?)", (a1, a2, a3)
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    # a2 is the one we completed
    assert by_id[a2]["follow_up_done"] == 1
    # a1 and a3 got swept as siblings
    assert by_id[a1]["follow_up_done"] == 1
    assert by_id[a1]["auto_close_reason"] == "superseded"
    assert by_id[a1]["auto_closed_by"] == "activity_complete"
    assert by_id[a3]["follow_up_done"] == 1
    assert by_id[a3]["auto_close_reason"] == "superseded"
    conn.close()


def test_activity_complete_doesnt_touch_other_policy_followups(tmp_db, app_client):
    conn = get_connection(tmp_db)
    cid, pid1 = _seed_client_and_policy(conn)
    # Second policy for the same client
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date) "
        "VALUES ('POL-002', ?, 'Auto', 'Test Carrier', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pid2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    a1 = _insert_followup(conn, cid, pid1, "FU #1", "2026-04-01")
    a2 = _insert_followup(conn, cid, pid2, "FU #2 (other policy)", "2026-04-01")
    conn.commit()
    conn.close()

    app_client.post(f"/activities/{a1}/complete", data={"duration_hours": 0})

    conn = get_connection(tmp_db)
    r2 = conn.execute(
        "SELECT follow_up_done, auto_close_reason FROM activity_log WHERE id=?", (a2,)
    ).fetchone()
    assert r2["follow_up_done"] == 0  # untouched
    assert r2["auto_close_reason"] is None
    conn.close()


# ── /activities/{id}/abandon ──────────────────────────────────────────────────


def test_activity_abandon_marks_done_and_prefixes_note(tmp_db, app_client):
    conn = get_connection(tmp_db)
    cid, pid = _seed_client_and_policy(conn)
    a1 = _insert_followup(conn, cid, pid, "FU to clear", "2026-04-01")
    conn.commit()
    conn.close()

    resp = app_client.post(f"/activities/{a1}/abandon", data={})
    assert resp.status_code == 200
    assert resp.text == ""

    conn = get_connection(tmp_db)
    row = conn.execute(
        "SELECT follow_up_done, details FROM activity_log WHERE id=?", (a1,)
    ).fetchone()
    assert row["follow_up_done"] == 1
    assert (row["details"] or "").startswith("[Abandoned]")
    conn.close()


def test_activity_abandon_also_supersedes_siblings(tmp_db, app_client):
    conn = get_connection(tmp_db)
    cid, pid = _seed_client_and_policy(conn)
    a1 = _insert_followup(conn, cid, pid, "FU #1", "2026-04-01")
    a2 = _insert_followup(conn, cid, pid, "FU #2", "2026-04-08")
    conn.commit()
    conn.close()

    app_client.post(f"/activities/{a1}/abandon", data={})

    conn = get_connection(tmp_db)
    r2 = conn.execute(
        "SELECT follow_up_done, auto_close_reason, auto_closed_by FROM activity_log WHERE id=?",
        (a2,),
    ).fetchone()
    assert r2["follow_up_done"] == 1
    assert r2["auto_close_reason"] == "superseded"
    assert r2["auto_closed_by"] == "activity_abandon"
    conn.close()
