"""Tests for program_carriers table and related functionality."""

import sqlite3
import pytest
from policydb.db import get_connection, init_db


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


def test_program_carriers_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "program_carriers" in tables
    conn.close()


def test_program_carriers_columns(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(program_carriers)").fetchall()]
    assert "id" in cols
    assert "program_id" in cols
    assert "carrier" in cols
    assert "policy_number" in cols
    assert "premium" in cols
    assert "limit_amount" in cols
    assert "sort_order" in cols
    conn.close()


def test_program_carriers_cascade_delete(tmp_db):
    conn = get_connection(tmp_db)
    # Create a client and program policy
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Test Client', 'Test')")
    client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, is_program)
           VALUES ('TST-001', ?, 'Property Program', 1)""",
        (client_id,),
    )
    policy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Insert carrier rows
    conn.execute(
        "INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'AIG', 100000)",
        (policy_id,),
    )
    conn.execute(
        "INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'Chubb', 200000)",
        (policy_id,),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM program_carriers WHERE program_id=?", (policy_id,)).fetchone()[0] == 2
    # Delete the policy — carriers should cascade
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM policies WHERE id=?", (policy_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM program_carriers WHERE program_id=?", (policy_id,)).fetchone()[0] == 0
    conn.close()
