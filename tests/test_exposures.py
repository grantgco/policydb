"""Tests for exposure-policy linkage and rate calculation."""
import sqlite3
import pytest
from policydb.exposures import (
    create_exposure_link,
    delete_exposure_link,
    set_primary_exposure,
    recalc_exposure_rate,
    get_policy_exposures,
    find_or_create_exposure,
)


@pytest.fixture
def conn():
    """In-memory SQLite with required schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("""CREATE TABLE policies (
        id INTEGER PRIMARY KEY, policy_uid TEXT UNIQUE, client_id INTEGER,
        premium REAL, effective_date TEXT, project_id INTEGER)""")
    db.execute("""CREATE TABLE projects (
        id INTEGER PRIMARY KEY, client_id INTEGER, name TEXT)""")
    db.execute("""CREATE TABLE client_exposures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER, project_id INTEGER, policy_id INTEGER,
        exposure_type TEXT, is_custom INTEGER DEFAULT 0,
        unit TEXT DEFAULT 'number', year INTEGER,
        amount REAL, denominator INTEGER DEFAULT 1,
        source_document TEXT, notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("""CREATE TABLE policy_exposure_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        policy_uid TEXT NOT NULL, exposure_id INTEGER NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0,
        rate REAL, rate_updated_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(policy_uid, exposure_id))""")
    # Seed data
    db.execute("INSERT INTO clients (id, name) VALUES (1, 'Acme Corp')")
    db.execute("INSERT INTO policies (id, policy_uid, client_id, premium, effective_date) VALUES (1, 'POL-001', 1, 50000, '2026-01-01')")
    db.execute("INSERT INTO policies (id, policy_uid, client_id, premium, effective_date) VALUES (2, 'POL-002', 1, 25000, '2026-01-01')")
    db.execute("""INSERT INTO client_exposures (id, client_id, exposure_type, year, amount, denominator)
        VALUES (1, 1, 'Payroll', 2026, 10000000, 100)""")
    db.execute("""INSERT INTO client_exposures (id, client_id, exposure_type, year, amount, denominator)
        VALUES (2, 1, 'Revenue', 2026, 28000000, 1000)""")
    db.commit()
    return db


def test_create_link_and_rate(conn):
    link = create_exposure_link(conn, "POL-001", 1, is_primary=True)
    assert link["is_primary"] == 1
    # rate = 50000 / (10000000 / 100) = 0.50
    assert abs(link["rate"] - 0.50) < 0.001


def test_duplicate_link_rejected(conn):
    create_exposure_link(conn, "POL-001", 1)
    with pytest.raises(Exception):
        create_exposure_link(conn, "POL-001", 1)


def test_only_one_primary_per_policy(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=True)
    create_exposure_link(conn, "POL-001", 2, is_primary=True)
    links = get_policy_exposures(conn, "POL-001")
    primaries = [l for l in links if l["is_primary"]]
    assert len(primaries) == 1
    assert primaries[0]["exposure_id"] == 2  # latest wins


def test_delete_link(conn):
    create_exposure_link(conn, "POL-001", 1)
    delete_exposure_link(conn, "POL-001", 1)
    assert len(get_policy_exposures(conn, "POL-001")) == 0


def test_recalc_by_policy(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=True)
    # Change premium
    conn.execute("UPDATE policies SET premium=100000 WHERE policy_uid='POL-001'")
    conn.commit()
    recalc_exposure_rate(conn, policy_uid="POL-001")
    links = get_policy_exposures(conn, "POL-001")
    # rate = 100000 / (10000000 / 100) = 1.00
    assert abs(links[0]["rate"] - 1.00) < 0.001


def test_recalc_by_exposure(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=True)
    create_exposure_link(conn, "POL-002", 1, is_primary=True)
    # Change exposure amount
    conn.execute("UPDATE client_exposures SET amount=5000000 WHERE id=1")
    conn.commit()
    recalc_exposure_rate(conn, exposure_id=1)
    links_1 = get_policy_exposures(conn, "POL-001")
    links_2 = get_policy_exposures(conn, "POL-002")
    # POL-001: 50000 / (5000000 / 100) = 1.00
    assert abs(links_1[0]["rate"] - 1.00) < 0.001
    # POL-002: 25000 / (5000000 / 100) = 0.50
    assert abs(links_2[0]["rate"] - 0.50) < 0.001


def test_null_rate_on_zero_amount(conn):
    conn.execute("UPDATE client_exposures SET amount=0 WHERE id=1")
    conn.commit()
    link = create_exposure_link(conn, "POL-001", 1, is_primary=True)
    assert link["rate"] is None


def test_null_rate_on_null_premium(conn):
    conn.execute("UPDATE policies SET premium=NULL WHERE policy_uid='POL-001'")
    conn.commit()
    link = create_exposure_link(conn, "POL-001", 1, is_primary=True)
    assert link["rate"] is None


def test_set_primary_exposure(conn):
    create_exposure_link(conn, "POL-001", 1, is_primary=False)
    create_exposure_link(conn, "POL-001", 2, is_primary=False)
    set_primary_exposure(conn, "POL-001", 1)
    links = get_policy_exposures(conn, "POL-001")
    primary = [l for l in links if l["is_primary"]]
    assert len(primary) == 1
    assert primary[0]["exposure_id"] == 1


def test_find_or_create_existing(conn):
    exp_id = find_or_create_exposure(conn, client_id=1, project_id=None,
                                     exposure_type="Payroll", year=2026,
                                     amount=10000000, denominator=100)
    assert exp_id == 1  # should find existing row


def test_find_or_create_new(conn):
    exp_id = find_or_create_exposure(conn, client_id=1, project_id=None,
                                     exposure_type="Headcount", year=2026,
                                     amount=500, denominator=1)
    assert exp_id > 2  # new row
    row = conn.execute("SELECT * FROM client_exposures WHERE id=?", (exp_id,)).fetchone()
    assert row["exposure_type"] == "Headcount"
    assert row["amount"] == 500
