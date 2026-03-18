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


def test_v_policy_status_program_carrier_count(tmp_db):
    """v_policy_status should derive carrier count from program_carriers table."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('View Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, carrier, is_program, renewal_status)
           VALUES ('VT-001', ?, 'Property Program', 'AIG', 1, 'Bound')""",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'AIG', 50000)", (pid,))
    conn.execute("INSERT INTO program_carriers (program_id, carrier, premium) VALUES (?, 'Chubb', 75000)", (pid,))
    conn.commit()
    row = conn.execute("SELECT * FROM v_policy_status WHERE policy_uid='VT-001'").fetchone()
    assert row is not None
    assert dict(row)["program_carrier_count"] == 2
    conn.close()


def test_v_schedule_program_carriers_from_table(tmp_db):
    """v_schedule should list carriers from program_carriers table for programs."""
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Sched Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO policies (policy_uid, client_id, policy_type, carrier, is_program,
                                 effective_date, expiration_date)
           VALUES ('SC-001', ?, 'Casualty Program', 'Zurich', 1, '2025-01-01', '2026-01-01')""",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO program_carriers (program_id, carrier, sort_order) VALUES (?, 'Zurich', 0)", (pid,))
    conn.execute("INSERT INTO program_carriers (program_id, carrier, sort_order) VALUES (?, 'Liberty', 1)", (pid,))
    conn.commit()
    row = conn.execute("SELECT * FROM v_schedule WHERE \"Policy Number\" IS NULL AND \"Line of Business\" LIKE '%Casualty%'").fetchone()
    assert row is not None
    carrier_val = dict(row)["Carrier"]
    assert "Zurich" in carrier_val
    assert "Liberty" in carrier_val
    conn.close()


from policydb.reconciler import ReconcileRow, reconcile


def test_reconciler_program_carrier_match_with_policy_number():
    """Reconciler should match import rows to program carrier entries using policy number."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-001", "client_name": "Acme Corp",
        "policy_type": "Property Program", "carrier": "AIG",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 500000, "limit_amount": 10000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 10, "carrier": "AIG", "policy_number": "POL-4481", "premium": 200000, "limit_amount": 5000000},
            {"id": 11, "carrier": "Chubb", "policy_number": "CHB-889", "premium": 300000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [{
        "client_name": "Acme Corp", "policy_type": "Property",
        "carrier": "AIG", "policy_number": "POL-4481",
        "effective_date": "2025-04-01", "expiration_date": "2026-04-01",
        "premium": 200000, "limit_amount": 5000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    matches = [r for r in results if r.status in ("MATCH", "DIFF")]
    assert len(matches) >= 1
    assert matches[0].is_program_match is True
    assert matches[0].matched_carrier_id == 10


def test_reconciler_program_carrier_no_match():
    """Carrier not in program_carrier_rows should not get program bonus."""
    db_rows = [{
        "id": 1, "policy_uid": "PGM-002", "client_name": "Beta Inc",
        "policy_type": "Casualty Program", "carrier": "Zurich",
        "effective_date": "2025-01-01", "expiration_date": "2026-01-01",
        "premium": 100000, "limit_amount": 5000000, "policy_number": "",
        "is_program": 1, "program_carriers": None, "program_carrier_count": 0,
        "first_named_insured": "", "deductible": 0,
        "_program_carrier_rows": [
            {"id": 20, "carrier": "Zurich", "policy_number": "ZNA-001", "premium": 100000, "limit_amount": 5000000},
        ],
    }]
    ext_rows = [{
        "client_name": "Beta Inc", "policy_type": "Casualty",
        "carrier": "Hartford", "policy_number": "HFD-999",
        "effective_date": "2025-01-01", "expiration_date": "2026-01-01",
        "premium": 50000, "limit_amount": 2000000, "deductible": 0,
        "first_named_insured": "",
    }]
    results = reconcile(ext_rows, db_rows)
    missing = [r for r in results if r.status == "MISSING"]
    assert len(missing) >= 1
