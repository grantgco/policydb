"""Audit PR regression tests — policies.exposure_* columns are gone and
the new `attach_primary_exposure()` helper hydrates dicts correctly.
"""
import pytest

import policydb.web.app  # noqa: F401 — boot FastAPI app

from policydb.db import get_connection, init_db
from policydb.exposures import create_exposure_link, find_or_create_exposure
from policydb.queries import (
    attach_primary_exposure,
    find_or_create_project_from_address,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def _seed_client(conn, name="Acme Corp"):
    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES (?, 'Construction')",
        (name,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_policy(conn, client_id, uid="POL-9001", project_id=None):
    conn.execute(
        """INSERT INTO policies
             (policy_uid, client_id, policy_type, carrier, premium,
              effective_date, expiration_date, project_id)
           VALUES (?, ?, 'GL', 'Travelers', 50000, '2026-01-01', '2027-01-01', ?)""",
        (uid, client_id, project_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Schema tests ───────────────────────────────────────────────────────────


def test_all_deprecated_columns_dropped(tmp_db):
    conn = get_connection()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(policies)").fetchall()}
    dropped = {
        "exposure_basis", "exposure_amount", "exposure_unit",
        "exposure_denominator", "exposure_address", "exposure_city",
        "exposure_state", "exposure_zip",
    }
    leaks = dropped & cols
    assert not leaks, f"Deprecated columns still present: {leaks}"


def test_migration_151_registered(tmp_db):
    conn = get_connection()
    row = conn.execute(
        "SELECT description FROM schema_version WHERE version = 151"
    ).fetchone()
    assert row is not None
    assert "exposure" in row["description"].lower()


def test_audit_trigger_does_not_reference_dropped_columns(tmp_db):
    conn = get_connection()
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'audit_policies_update'"
    ).fetchone()
    assert row is not None
    dropped_refs = [
        "exposure_basis", "exposure_amount", "exposure_unit",
        "exposure_address", "exposure_city", "exposure_state", "exposure_zip",
    ]
    for ref in dropped_refs:
        assert ref not in row["sql"], f"audit_policies_update still references {ref}"


def test_views_do_not_reference_dropped_columns(tmp_db):
    """Every view must be able to SELECT from the reshaped `policies`
    table.  If a view still references a dropped column, CREATE VIEW
    would fail at startup and `init_db()` would raise — so instead of
    grepping the SQL text (which picks up harmless code comments), we
    try to run each view to confirm it resolves against the live schema.
    """
    conn = get_connection()
    view_names = [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        ).fetchall()
    ]
    for name in view_names:
        # LIMIT 0 avoids any row work but still forces SQLite to resolve
        # every column referenced in the SELECT list + joins.
        conn.execute(f"SELECT * FROM {name} LIMIT 0")  # noqa: S608


# ── attach_primary_exposure() ──────────────────────────────────────────────


def test_attach_primary_exposure_no_links_sets_none(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-A")
    policies = [{"policy_uid": "POL-A"}]
    attach_primary_exposure(conn, policies)
    assert policies[0]["primary_exposure_type"] is None
    assert policies[0]["primary_exposure_amount"] is None
    assert policies[0]["exposure_location_address"] is None


def test_attach_primary_exposure_hydrates_from_primary_link(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    # Pre-create a project so the exposure can attach to a location
    project_id = find_or_create_project_from_address(
        conn, client_id=cid, address="1 Primary St", city="Austin", state="TX",
    )
    _seed_policy(conn, cid, "POL-B", project_id=project_id)
    exp_id = find_or_create_exposure(
        conn, client_id=cid, project_id=project_id,
        exposure_type="Payroll", year=2026, amount=5_000_000, denominator=100,
    )
    create_exposure_link(conn, "POL-B", exp_id, is_primary=True)

    policies = [{"policy_uid": "POL-B"}]
    attach_primary_exposure(conn, policies)
    assert policies[0]["primary_exposure_type"] == "Payroll"
    assert policies[0]["primary_exposure_amount"] == 5_000_000
    assert policies[0]["primary_exposure_denominator"] == 100
    assert policies[0]["exposure_location_address"] == "1 Primary St"
    assert policies[0]["exposure_location_city"] == "Austin"
    assert policies[0]["exposure_location_state"] == "TX"


def test_attach_primary_exposure_ignores_non_primary_links(tmp_db):
    """Non-primary links should not populate the primary_exposure_* fields."""
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-C")
    payroll_id = find_or_create_exposure(
        conn, client_id=cid, project_id=None,
        exposure_type="Payroll", year=2026, amount=1_000_000, denominator=100,
    )
    sales_id = find_or_create_exposure(
        conn, client_id=cid, project_id=None,
        exposure_type="Gross Sales", year=2026, amount=10_000_000, denominator=1000,
    )
    create_exposure_link(conn, "POL-C", sales_id, is_primary=True)
    create_exposure_link(conn, "POL-C", payroll_id, is_primary=False)

    policies = [{"policy_uid": "POL-C"}]
    attach_primary_exposure(conn, policies)
    assert policies[0]["primary_exposure_type"] == "Gross Sales"
    assert policies[0]["primary_exposure_amount"] == 10_000_000


def test_attach_primary_exposure_falls_back_to_exposure_project(tmp_db):
    """When policies.project_id is NULL but the exposure has a project_id,
    the address comes from the exposure's own project."""
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-D", project_id=None)
    exp_project_id = find_or_create_project_from_address(
        conn, client_id=cid, address="2 Exposure Ave", city="Dallas",
    )
    exp_id = find_or_create_exposure(
        conn, client_id=cid, project_id=exp_project_id,
        exposure_type="Revenue", year=2026, amount=2_000_000, denominator=1000,
    )
    create_exposure_link(conn, "POL-D", exp_id, is_primary=True)

    policies = [{"policy_uid": "POL-D"}]
    attach_primary_exposure(conn, policies)
    assert policies[0]["exposure_location_address"] == "2 Exposure Ave"
    assert policies[0]["exposure_location_city"] == "Dallas"


def test_v_policy_status_exposes_primary_fields_via_view(tmp_db):
    """The v_policy_status view joins client_exposures + projects and
    exposes the expected aliases for legacy templates/queries."""
    conn = get_connection()
    cid = _seed_client(conn)
    pid = find_or_create_project_from_address(
        conn, client_id=cid, address="100 View Way",
    )
    _seed_policy(conn, cid, "POL-E", project_id=pid)
    exp_id = find_or_create_exposure(
        conn, client_id=cid, project_id=pid,
        exposure_type="Payroll", year=2026, amount=750_000, denominator=100,
    )
    create_exposure_link(conn, "POL-E", exp_id, is_primary=True)

    row = conn.execute(
        """SELECT primary_exposure_type, primary_exposure_amount,
                  primary_exposure_denominator, exposure_address
           FROM v_policy_status WHERE policy_uid = 'POL-E'"""
    ).fetchone()
    assert row is not None
    assert row["primary_exposure_type"] == "Payroll"
    assert row["primary_exposure_amount"] == 750_000
    assert row["primary_exposure_denominator"] == 100
    assert row["exposure_address"] == "100 View Way"
