import pytest
from policydb.db import init_db, get_connection


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_policy_timeline_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='policy_timeline'"
    )
    assert cur.fetchone() is not None


def test_policy_timeline_columns(tmp_db):
    conn = get_connection(tmp_db)
    cur = conn.execute("PRAGMA table_info(policy_timeline)")
    cols = {r["name"] for r in cur.fetchall()}
    expected = {
        "id", "policy_uid", "milestone_name", "ideal_date", "projected_date",
        "completed_date", "prep_alert_date", "accountability", "waiting_on",
        "health", "acknowledged", "acknowledged_at", "created_at",
    }
    assert expected.issubset(cols)


def test_milestone_profile_column_on_policies(tmp_db):
    conn = get_connection(tmp_db)
    cur = conn.execute("PRAGMA table_info(policies)")
    cols = {r["name"] for r in cur.fetchall()}
    assert "milestone_profile" in cols


def test_policy_timeline_unique_constraint(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Test Client', 'Other', 'Test')")
    conn.commit()
    client_id = conn.execute("SELECT id FROM clients WHERE name = 'Test Client'").fetchone()["id"]
    conn.execute("INSERT INTO policies (policy_uid, client_id, policy_type) VALUES ('POL-001', ?, 'General Liability')", (client_id,))
    conn.execute("""
        INSERT INTO policy_timeline (policy_uid, milestone_name, ideal_date, projected_date)
        VALUES ('POL-001', 'RSM Meeting', '2026-06-01', '2026-06-01')
    """)
    conn.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO policy_timeline (policy_uid, milestone_name, ideal_date, projected_date)
            VALUES ('POL-001', 'RSM Meeting', '2026-06-01', '2026-06-01')
        """)
