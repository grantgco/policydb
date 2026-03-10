"""Tests for database initialization, schema, and migrations."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from policydb.db import get_connection, init_db, next_policy_uid


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_init_creates_tables(tmp_db):
    conn = get_connection(tmp_db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "clients" in tables
    assert "policies" in tables
    assert "activity_log" in tables
    assert "premium_history" in tables
    assert "schema_version" in tables
    conn.close()


def test_init_creates_views(tmp_db):
    conn = get_connection(tmp_db)
    views = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()]
    assert "v_policy_status" in views
    assert "v_client_summary" in views
    assert "v_schedule" in views
    assert "v_renewal_pipeline" in views
    assert "v_overdue_followups" in views
    conn.close()


def test_schema_version_recorded(tmp_db):
    conn = get_connection(tmp_db)
    row = conn.execute("SELECT version FROM schema_version WHERE version = 1").fetchone()
    assert row is not None
    conn.close()


def test_updated_at_trigger(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Test Co', 'Technology', 'Grant')"
    )
    conn.commit()
    before = conn.execute("SELECT updated_at FROM clients WHERE name = 'Test Co'").fetchone()["updated_at"]

    import time; time.sleep(1.1)
    conn.execute("UPDATE clients SET notes = 'updated' WHERE name = 'Test Co'")
    conn.commit()
    after = conn.execute("SELECT updated_at FROM clients WHERE name = 'Test Co'").fetchone()["updated_at"]
    assert after >= before
    conn.close()


def test_next_policy_uid_empty(tmp_db):
    conn = get_connection(tmp_db)
    uid = next_policy_uid(conn)
    assert uid == "POL-001"
    conn.close()


def test_next_policy_uid_increments(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('C', 'Technology', 'Grant')"
    )
    client_id = conn.execute("SELECT id FROM clients WHERE name='C'").fetchone()["id"]
    from datetime import date
    today = date.today().isoformat()
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date, premium, account_exec)
           VALUES ('POL-005', ?, 'GL', 'Zurich', ?, ?, 10000, 'Grant')""",
        (client_id, today, today),
    )
    conn.commit()
    uid = next_policy_uid(conn)
    assert uid == "POL-006"
    conn.close()


def test_foreign_key_enforcement(tmp_db):
    conn = get_connection(tmp_db)
    from datetime import date
    today = date.today().isoformat()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO policies
               (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date, premium, account_exec)
               VALUES ('POL-999', 9999, 'GL', 'Zurich', ?, ?, 10000, 'Grant')""",
            (today, today),
        )
        conn.commit()
    conn.close()
