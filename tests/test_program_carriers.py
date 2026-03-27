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


def test_v_policy_status_program_fields(tmp_db):
    """v_policy_status should include program_id and program_name via JOIN to programs table."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('View Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO programs (program_uid, client_id, name, line_of_business) VALUES ('PGM-001', ?, 'Property', 'Property Program')",
        (cid,),
    )
    pgm_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, carrier, renewal_status, program_id)
           VALUES ('VT-001', ?, 'Property', 'AIG', 'Bound', ?)""",
        (cid, pgm_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM v_policy_status WHERE policy_uid='VT-001'").fetchone()
    assert row is not None
    d = dict(row)
    assert d["program_id"] == pgm_id
    assert d["program_name"] == "Property"
    assert d["program_uid"] == "PGM-001"
    conn.close()


def test_v_schedule_uses_policy_carrier_directly(tmp_db):
    """v_schedule should use the policy's own carrier field directly (no program_carriers lookup)."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Sched Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, carrier,
                                 effective_date, expiration_date)
           VALUES ('SC-001', ?, 'Casualty', 'Zurich', '2025-01-01', '2026-01-01')""",
        (cid,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM v_schedule WHERE \"Line of Business\" = 'Casualty'").fetchone()
    assert row is not None
    carrier_val = dict(row)["Carrier"]
    assert carrier_val == "Zurich"
    conn.close()


from policydb.reconciler import ReconcileRow, reconcile


def test_reconciler_child_policy_direct_match():
    """Child policies (with program_id) should match 1:1 like any other policy."""
    db_rows = [{
        "id": 10, "policy_uid": "POL-010", "client_name": "Acme Corp",
        "policy_type": "Property", "carrier": "AIG",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 200000, "limit_amount": 5000000, "policy_number": "POL-4481",
        "is_program": 0, "program_carriers": "", "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "program_id": 1,
    }]
    ext_rows = [{
        "client_name": "Acme Corp", "policy_type": "Property",
        "carrier": "AIG", "policy_number": "POL-4481",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 200000, "limit_amount": 5000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    matches = [r for r in results if r.status == "PAIRED"]
    assert len(matches) == 1
    assert matches[0].db["program_id"] == 1


def test_reconciler_no_program_overlay_fields():
    """ReconcileRow should not have is_program_match or matched_carrier_id fields."""
    fields = list(ReconcileRow.__dataclass_fields__)
    assert "is_program_match" not in fields
    assert "matched_carrier_id" not in fields
