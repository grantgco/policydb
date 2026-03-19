"""Tests for follow-up disposition tracking and threading."""

import sqlite3
import pytest
from datetime import date, timedelta
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


def test_disposition_column_exists(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(activity_log)").fetchall()]
    assert "disposition" in cols
    assert "thread_id" in cols
    conn.close()


def test_thread_id_index_exists(tmp_db):
    conn = get_connection(tmp_db)
    indices = [r[1] for r in conn.execute("PRAGMA index_list(activity_log)").fetchall()]
    assert "idx_activity_thread" in indices
    conn.close()


def test_thread_grouping(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Thread Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, disposition) VALUES (?, ?, 'Call', 'Initial RFI', 1, 'Sent RFI')",
        (date.today().isoformat(), cid),
    )
    a1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE activity_log SET thread_id = ? WHERE id = ?", (a1, a1))
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, disposition) VALUES (?, ?, 'Call', 'Follow-up: Initial RFI', ?, 'Left VM')",
        (date.today().isoformat(), cid, a1),
    )
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, disposition) VALUES (?, ?, 'Call', 'Follow-up: Initial RFI', ?, 'Connected')",
        (date.today().isoformat(), cid, a1),
    )
    conn.commit()
    chain = conn.execute(
        "SELECT * FROM activity_log WHERE thread_id = ? ORDER BY id", (a1,)
    ).fetchall()
    assert len(chain) == 3
    assert chain[0]["disposition"] == "Sent RFI"
    assert chain[1]["disposition"] == "Left VM"
    assert chain[2]["disposition"] == "Connected"
    conn.close()


def test_lazy_thread_creation(tmp_db):
    """Re-diarying a standalone activity should create a thread lazily."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Lazy Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, follow_up_date) VALUES (?, ?, 'Call', 'Check in', '2025-01-15')",
        (date.today().isoformat(), cid),
    )
    a1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    row = conn.execute("SELECT thread_id FROM activity_log WHERE id=?", (a1,)).fetchone()
    assert row["thread_id"] is None
    conn.execute("UPDATE activity_log SET thread_id=?, follow_up_done=1, disposition='Left VM' WHERE id=?", (a1, a1))
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, activity_type, subject, thread_id, follow_up_date) VALUES (?, ?, 'Call', 'Follow-up: Check in', ?, '2025-01-18')",
        (date.today().isoformat(), cid, a1),
    )
    conn.commit()
    chain = conn.execute("SELECT * FROM activity_log WHERE thread_id=? ORDER BY id", (a1,)).fetchall()
    assert len(chain) == 2
    assert chain[0]["disposition"] == "Left VM"
    conn.close()
