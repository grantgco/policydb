"""Regression tests for the 'in-force' filter on program aggregation.

Bug: the client page showed program premium and policy count that
included expired child policies, overstating the in-force numbers. The
derived term (MIN effective / MAX expiration) also stretched across last
year's schedule because expired rows were still pulled into the MIN/MAX.

Fix: `get_program_aggregates` and `get_program_by_uid` now filter children
through `_IN_FORCE_POLICY_FILTER`:
    archived = 0
    AND (is_opportunity = 0 OR is_opportunity IS NULL)
    AND (expiration_date IS NULL OR expiration_date >= date('now'))

NULL expiration is kept in-force because brand-new policies may not yet
have the expiration filled in. These tests lock in:

1. A program whose only children are expired → aggregate is (0, $0) and
   derived dates are (None, None).
2. A program with a mix of expired and current children → only current
   contribute to count/premium and derived term.
3. A policy with NULL expiration counts as in-force.
4. A program with only current children behaves exactly like before
   (no regression on the happy path).
"""

import pytest

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
    yield conn
    conn.close()


def _make_program(conn, program_id, program_uid, name="Casualty"):
    conn.execute(
        "INSERT INTO programs (id, program_uid, client_id, name) "
        "VALUES (?, ?, 1, ?)",
        (program_id, program_uid, name),
    )


def _make_policy(conn, policy_id, program_id, effective, expiration,
                 premium=10000, archived=0, is_opportunity=0):
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id,
                policy_type, carrier, premium, limit_amount,
                effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (?, ?, 1, ?, 'GL', 'Zurich', ?, 1000000, ?, ?,
                   'Bound', ?, ?)""",
        (policy_id, f"POL-{policy_id}", program_id, premium,
         effective, expiration, is_opportunity, archived),
    )


# ── all-expired program ─────────────────────────────────────────────────


def test_all_expired_program_aggregates_to_zero(db):
    _make_program(db, 1, "PGM-001")
    _make_policy(db, 10, 1, "2020-01-01", "2021-01-01", premium=50000)
    _make_policy(db, 11, 1, "2022-01-01", "2023-01-01", premium=75000)
    db.commit()

    agg = get_program_aggregates(db, 1)
    assert agg["policy_count"] == 0, (
        "All children are expired — in-force count should be 0, not include "
        "last year's policies."
    )
    assert agg["total_premium"] == 0
    assert agg["effective_date"] is None
    assert agg["expiration_date"] is None


def test_all_expired_program_has_no_derived_term(db):
    _make_program(db, 1, "PGM-001")
    _make_policy(db, 10, 1, "2020-01-01", "2021-01-01")
    db.commit()

    pgm = get_program_by_uid(db, "PGM-001")
    assert pgm is not None
    assert pgm["effective_date"] is None
    assert pgm["expiration_date"] is None


# ── mixed expired + current ─────────────────────────────────────────────


def test_mixed_program_only_counts_current(db):
    _make_program(db, 1, "PGM-001")
    # Last year's expired schedule
    _make_policy(db, 10, 1, "2025-01-01", "2026-01-01", premium=50000)
    _make_policy(db, 11, 1, "2025-03-01", "2026-03-01", premium=25000)
    # This year's renewed schedule (effective 2026-01, exp 2027-01 — still in force)
    _make_policy(db, 20, 1, "2026-01-01", "2027-01-01", premium=55000)
    _make_policy(db, 21, 1, "2026-03-01", "2027-03-01", premium=28000)
    db.commit()

    agg = get_program_aggregates(db, 1)
    assert agg["policy_count"] == 2, (
        "Only the two current-term policies should count — expired rows "
        "inflate the in-force premium number."
    )
    assert agg["total_premium"] == 55000 + 28000


def test_mixed_program_derived_term_uses_current_only(db):
    _make_program(db, 1, "PGM-001")
    # Last year expired
    _make_policy(db, 10, 1, "2025-01-01", "2026-01-01")
    # Current
    _make_policy(db, 20, 1, "2026-01-01", "2027-01-01")
    _make_policy(db, 21, 1, "2026-03-01", "2027-03-01")
    db.commit()

    pgm = get_program_by_uid(db, "PGM-001")
    # Expected: MIN and MAX over the two current policies only.
    assert pgm["effective_date"] == "2026-01-01", (
        "Derived effective must ignore expired rows — otherwise the header "
        "would show last year's start date."
    )
    assert pgm["expiration_date"] == "2027-03-01"


# ── NULL expiration treated as in-force ────────────────────────────────


def test_null_expiration_counts_as_in_force(db):
    _make_program(db, 1, "PGM-001")
    # A brand new policy being entered with no expiration yet
    db.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id,
                policy_type, carrier, premium, limit_amount,
                effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (30, 'POL-030', 1, 1, 'GL', 'Zurich', 12000, 1000000,
                   '2026-06-01', NULL, 'Not Started', 0, 0)"""
    )
    db.commit()

    agg = get_program_aggregates(db, 1)
    assert agg["policy_count"] == 1, (
        "A policy mid-entry with NULL expiration is in-force, not expired."
    )
    assert agg["total_premium"] == 12000
    assert agg["effective_date"] == "2026-06-01"
    # MAX ignores NULL so derived expiration is None here — that's fine,
    # the template `{% if %}` guard hides it until the user fills it in.
    assert agg["expiration_date"] is None


# ── happy path unchanged ───────────────────────────────────────────────


def test_all_current_program_unchanged(db):
    """Programs with only in-force children should behave exactly like
    before the filter was added — ensures the test_program_derived_dates
    scenarios are still correct and this PR is a pure additive fix."""
    _make_program(db, 1, "PGM-001")
    _make_policy(db, 10, 1, "2026-03-15", "2027-03-14", premium=10000)
    _make_policy(db, 11, 1, "2026-01-01", "2027-06-30", premium=15000)
    _make_policy(db, 12, 1, "2026-04-01", "2027-04-30", premium=20000)
    db.commit()

    agg = get_program_aggregates(db, 1)
    assert agg["policy_count"] == 3
    assert agg["total_premium"] == 45000
    assert agg["effective_date"] == "2026-01-01"
    assert agg["expiration_date"] == "2027-06-30"

    pgm = get_program_by_uid(db, "PGM-001")
    assert pgm["effective_date"] == "2026-01-01"
    assert pgm["expiration_date"] == "2027-06-30"
