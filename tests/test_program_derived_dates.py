"""Tests for program effective/expiration date derivation.

Programs no longer own their term — it's derived from the child policies'
MIN(effective_date) / MAX(expiration_date), excluding archived and
opportunity rows. These tests lock in:

1. get_program_by_uid overrides the stored pg.effective/expiration with
   derived values.
2. get_program_aggregates returns derived dates in its result dict.
3. The header PATCH route rejects attempts to write effective_date or
   expiration_date.
4. Edge cases: empty program, archived children excluded, opportunity
   children excluded.
"""

import pytest
from starlette.testclient import TestClient

from policydb.db import init_db, get_connection
from policydb.queries import get_program_by_uid, get_program_aggregates


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) "
        "VALUES (1, 'Acme', 'Construction')"
    )
    yield conn, db_path
    conn.close()


def _make_program(conn, program_id, program_uid, name="Casualty",
                  effective_date="2099-01-01", expiration_date="2099-12-31"):
    """Insert a program with deliberately stale stored dates so we can prove
    the helper derives from children and ignores the stored column."""
    conn.execute(
        """INSERT INTO programs (id, program_uid, client_id, name,
                effective_date, expiration_date)
           VALUES (?, ?, 1, ?, ?, ?)""",
        (program_id, program_uid, name, effective_date, expiration_date),
    )


def _make_policy(conn, policy_id, program_id, effective, expiration,
                 archived=0, is_opportunity=0, policy_uid=None):
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id,
                policy_type, carrier, premium, limit_amount,
                effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (?, ?, 1, ?, 'GL', 'Zurich', 10000, 1000000, ?, ?,
                   'Bound', ?, ?)""",
        (policy_id, policy_uid or f"POL-{policy_id}", program_id,
         effective, expiration, is_opportunity, archived),
    )


# ── get_program_by_uid ──────────────────────────────────────────────────


def test_empty_program_has_null_dates(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    conn.commit()
    pgm = get_program_by_uid(conn, "PGM-001")
    assert pgm is not None
    assert pgm["effective_date"] is None
    assert pgm["expiration_date"] is None


def test_single_policy_program_uses_that_policy_dates(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    conn.commit()
    pgm = get_program_by_uid(conn, "PGM-001")
    assert pgm["effective_date"] == "2026-03-15"
    assert pgm["expiration_date"] == "2027-03-14"


def test_multi_policy_program_takes_min_eff_max_exp(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    # Three policies with ragged dates — program should span MIN..MAX
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    _make_policy(conn, 11, 1, "2026-01-01", "2027-06-30")
    _make_policy(conn, 12, 1, "2026-04-01", "2027-04-30")
    conn.commit()
    pgm = get_program_by_uid(conn, "PGM-001")
    assert pgm["effective_date"] == "2026-01-01"  # min
    assert pgm["expiration_date"] == "2027-06-30"  # max


def test_derived_dates_ignore_stored_columns(db):
    """Proves the helper actually computes from children and doesn't
    fall through to whatever's on the programs row."""
    conn, _ = db
    _make_program(
        conn, 1, "PGM-001",
        effective_date="2099-01-01",
        expiration_date="2099-12-31",
    )
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    conn.commit()
    pgm = get_program_by_uid(conn, "PGM-001")
    assert pgm["effective_date"] == "2026-03-15"
    assert pgm["expiration_date"] == "2027-03-14"


def test_archived_child_is_excluded_from_derivation(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    _make_policy(conn, 11, 1, "2020-01-01", "2099-12-31", archived=1)
    conn.commit()
    pgm = get_program_by_uid(conn, "PGM-001")
    # Archived policy must not stretch the derived term.
    assert pgm["effective_date"] == "2026-03-15"
    assert pgm["expiration_date"] == "2027-03-14"


def test_opportunity_child_is_excluded_from_derivation(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    _make_policy(conn, 11, 1, "2020-01-01", "2099-12-31", is_opportunity=1)
    conn.commit()
    pgm = get_program_by_uid(conn, "PGM-001")
    assert pgm["effective_date"] == "2026-03-15"
    assert pgm["expiration_date"] == "2027-03-14"


# ── get_program_aggregates ─────────────────────────────────────────────


def test_aggregates_include_derived_dates(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    _make_policy(conn, 11, 1, "2026-01-01", "2027-06-30")
    conn.commit()
    agg = get_program_aggregates(conn, 1)
    assert agg["effective_date"] == "2026-01-01"
    assert agg["expiration_date"] == "2027-06-30"
    assert agg["policy_count"] == 2


def test_aggregates_empty_program_returns_null_dates(db):
    conn, _ = db
    _make_program(conn, 1, "PGM-001")
    conn.commit()
    agg = get_program_aggregates(conn, 1)
    assert agg["effective_date"] is None
    assert agg["expiration_date"] is None
    assert agg["policy_count"] == 0


# ── header PATCH route ─────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Acme', 'Construction')"
    )
    conn.execute(
        """INSERT INTO programs (id, program_uid, client_id, name,
                effective_date, expiration_date)
           VALUES (1, 'PGM-001', 1, 'Casualty', '2026-01-01', '2026-12-31')"""
    )
    _make_policy(conn, 10, 1, "2026-03-15", "2027-03-14")
    conn.commit()
    conn.close()

    from policydb.web.app import app
    yield TestClient(app, raise_server_exceptions=False), db_path


def test_header_patch_rejects_effective_date(app_client):
    client, _ = app_client
    resp = client.patch(
        "/programs/PGM-001/header",
        json={"effective_date": "2020-01-01"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert "derived" in body["error"].lower()


def test_header_patch_rejects_expiration_date(app_client):
    client, _ = app_client
    resp = client.patch(
        "/programs/PGM-001/header",
        json={"expiration_date": "2099-01-01"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert "derived" in body["error"].lower()


def test_header_patch_still_accepts_other_fields(app_client):
    client, _ = app_client
    resp = client.patch(
        "/programs/PGM-001/header",
        json={"name": "Property"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_header_patch_rejects_date_even_when_bundled_with_other_fields(app_client):
    """If the payload includes date + other fields, reject the whole request
    so we never silently drop the user's date input."""
    client, _ = app_client
    resp = client.patch(
        "/programs/PGM-001/header",
        json={"name": "Property", "effective_date": "2020-01-01"},
    )
    assert resp.status_code == 400
