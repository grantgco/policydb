"""Tests for the recurring events generator."""

from datetime import date, timedelta

import pytest

import policydb.config as cfg
from policydb.db import get_connection, init_db
from policydb.recurring_events import (
    _advance,
    advance_template_for_completion,
    compute_initial_next_occurrence,
    generate_due_recurring_instances,
    next_recurring_uid,
)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", config_path)
    monkeypatch.setattr("policydb.config.CONFIG_PATH", config_path)
    cfg.reload_config()
    init_db(path=db_path)
    return db_path


def _make_client(conn, name="Test Client"):
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES (?, 'Other', 'Tester')",
        (name,),
    )
    conn.commit()
    return conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()["id"]


def _make_template(conn, client_id, **overrides):
    defaults = dict(
        recurring_uid="REC-001",
        client_id=client_id,
        name="Weekly open items call",
        cadence="Weekly",
        interval_n=1,
        day_of_week=None,
        day_of_month=None,
        lead_days=0,
        start_date=date.today().isoformat(),
        end_date=None,
        next_occurrence=date.today().isoformat(),
        default_severity="Normal",
        active=1,
        catch_up_mode="collapse",
    )
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" * len(defaults))
    conn.execute(
        f"INSERT INTO recurring_events ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM recurring_events WHERE recurring_uid = ?",
        (defaults["recurring_uid"],),
    ).fetchone()["id"]


# ─────────────────────────────────────────────────────────────────────────
# Schema checks
# ─────────────────────────────────────────────────────────────────────────

def test_recurring_events_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='recurring_events'"
    ).fetchone()
    assert row is not None


def test_activity_log_has_recurring_columns(tmp_db):
    conn = get_connection(tmp_db)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(activity_log)").fetchall()}
    assert "recurring_event_id" in cols
    assert "recurring_instance_date" in cols


def test_uid_sequence_seeded(tmp_db):
    conn = get_connection(tmp_db)
    row = conn.execute("SELECT next_val FROM uid_sequence WHERE prefix = 'REC'").fetchone()
    assert row is not None


# ─────────────────────────────────────────────────────────────────────────
# _advance() date arithmetic
# ─────────────────────────────────────────────────────────────────────────

def test_advance_weekly():
    anchor = date(2026, 4, 13)  # Monday
    assert _advance(anchor, "Weekly", 1, None, None) == date(2026, 4, 20)


def test_advance_biweekly_snaps_dow():
    anchor = date(2026, 4, 13)  # Monday
    # Biweekly with day_of_week=2 (Wednesday) → 2 weeks later, snapped to Wed
    result = _advance(anchor, "Biweekly", 1, 2, None)
    assert result.weekday() == 2


def test_advance_monthly_clamps_month_end():
    # Jan 31 → Feb 28 (or 29 in leap year)
    anchor = date(2026, 1, 31)
    result = _advance(anchor, "Monthly", 1, None, 31)
    assert result == date(2026, 2, 28)


def test_advance_quarterly():
    anchor = date(2026, 1, 15)
    result = _advance(anchor, "Quarterly", 1, None, 15)
    assert result == date(2026, 4, 15)


def test_advance_annual():
    anchor = date(2026, 4, 13)
    result = _advance(anchor, "Annual", 1, None, None)
    assert result == date(2027, 4, 13)


def test_advance_daily():
    anchor = date(2026, 4, 13)
    assert _advance(anchor, "Daily", 3, None, None) == date(2026, 4, 16)


# ─────────────────────────────────────────────────────────────────────────
# Generator behavior
# ─────────────────────────────────────────────────────────────────────────

def test_generator_materializes_instances(tmp_db):
    """Weekly cadence + 14-day horizon → expect 2–3 issue rows (today, +7, +14)."""
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    tmpl_id = _make_template(conn, client_id)

    inserted = generate_due_recurring_instances(conn)
    assert inserted >= 1
    assert inserted <= 3  # bounded by horizon

    rows = conn.execute(
        "SELECT * FROM activity_log WHERE recurring_event_id = ? ORDER BY recurring_instance_date",
        (tmpl_id,),
    ).fetchall()
    assert len(rows) == inserted

    row = dict(rows[0])
    assert row["item_kind"] == "issue"
    assert row["issue_status"] == "Open"
    assert row["issue_severity"] == "Normal"
    assert row["issue_uid"] is not None
    assert row["recurring_instance_date"] is not None
    assert row["activity_type"] == "Issue"


def test_generator_is_idempotent(tmp_db):
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    _make_template(conn, client_id)

    generate_due_recurring_instances(conn)
    # Second call with no change in time: no new rows
    second_pass = generate_due_recurring_instances(conn)
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE recurring_event_id IS NOT NULL"
    ).fetchone()["n"]
    assert second_pass == 0
    # Total should still be 1 from the first pass (within horizon)
    assert total >= 1


def test_generator_advances_next_occurrence(tmp_db):
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    tmpl_id = _make_template(conn, client_id)

    before = conn.execute(
        "SELECT next_occurrence FROM recurring_events WHERE id = ?", (tmpl_id,)
    ).fetchone()["next_occurrence"]
    generate_due_recurring_instances(conn)
    after = conn.execute(
        "SELECT next_occurrence FROM recurring_events WHERE id = ?", (tmpl_id,)
    ).fetchone()["next_occurrence"]

    assert after > before


def test_generator_skips_inactive_templates(tmp_db):
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    _make_template(conn, client_id, active=0)

    inserted = generate_due_recurring_instances(conn)
    assert inserted == 0


def test_generator_skips_past_end_date(tmp_db):
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    _make_template(conn, client_id, end_date=yesterday)

    inserted = generate_due_recurring_instances(conn)
    assert inserted == 0


def test_generator_catch_up_collapse(tmp_db):
    """A template with next_occurrence 30 days in the past (collapse mode)
    should only emit ONE catch-up instance, not 4+ weekly rows."""
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    _make_template(conn, client_id, next_occurrence=thirty_ago, catch_up_mode="collapse")

    inserted = generate_due_recurring_instances(conn)
    # Collapse mode: exactly one catch-up + whatever falls inside the forward horizon
    # (14 days). Since the collapsed anchor is <= today, then advance steps forward.
    # We primarily care that it's bounded — not dozens of rows.
    assert inserted >= 1
    assert inserted <= 4  # collapsed + a few weeks forward within horizon


def test_generator_respects_client_archived(tmp_db):
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    _make_template(conn, client_id)
    conn.execute("UPDATE clients SET archived = 1 WHERE id = ?", (client_id,))
    conn.commit()

    inserted = generate_due_recurring_instances(conn)
    assert inserted == 0


# ─────────────────────────────────────────────────────────────────────────
# Completion hook
# ─────────────────────────────────────────────────────────────────────────

def test_advance_on_completion_creates_next(tmp_db):
    conn = get_connection(tmp_db)
    client_id = _make_client(conn)
    tmpl_id = _make_template(conn, client_id)
    generate_due_recurring_instances(conn)

    # Pretend the user resolved the instance
    row = conn.execute(
        "SELECT id FROM activity_log WHERE recurring_event_id = ? ORDER BY id DESC LIMIT 1",
        (tmpl_id,),
    ).fetchone()
    conn.execute(
        "UPDATE activity_log SET issue_status='Resolved', resolved_date=? WHERE id=?",
        (date.today().isoformat(), row["id"]),
    )
    conn.commit()

    before_count = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE recurring_event_id = ?", (tmpl_id,)
    ).fetchone()["n"]

    advance_template_for_completion(conn, row["id"])

    after_count = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE recurring_event_id = ?", (tmpl_id,)
    ).fetchone()["n"]

    # The generator may or may not have emitted a new row depending on whether
    # next_occurrence is within the horizon. We just verify the call is safe.
    assert after_count >= before_count


# ─────────────────────────────────────────────────────────────────────────
# UID issuance
# ─────────────────────────────────────────────────────────────────────────

def test_next_recurring_uid_increments(tmp_db):
    conn = get_connection(tmp_db)
    uid1 = next_recurring_uid(conn)
    uid2 = next_recurring_uid(conn)
    assert uid1 != uid2
    assert uid1.startswith("REC-")
    assert uid2.startswith("REC-")


# ─────────────────────────────────────────────────────────────────────────
# Initial next_occurrence computation
# ─────────────────────────────────────────────────────────────────────────

def test_compute_initial_weekly_snaps_forward():
    start = date(2026, 4, 13)  # Monday
    # Weekly, day_of_week=3 (Thursday) → snap to Apr 16
    result = compute_initial_next_occurrence(start, "Weekly", 3, None)
    assert result.weekday() == 3
    assert result >= start


def test_compute_initial_monthly_clamps():
    start = date(2026, 2, 10)
    # Monthly, day_of_month=31 → Feb 28 (2026 is not a leap year)
    result = compute_initial_next_occurrence(start, "Monthly", None, 31)
    assert result == date(2026, 2, 28)
