import pytest
from policydb.db import init_db, get_connection
import policydb.config as cfg


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


def test_mandated_activities_have_prep_days(tmp_db):
    """All mandated activities must have prep_days field."""
    cfg.reload_config()
    activities = cfg.get("mandated_activities")
    for act in activities:
        assert "prep_days" in act, f"{act['name']} missing prep_days"
        assert isinstance(act["prep_days"], int)


def test_dispositions_have_accountability(tmp_db):
    """All dispositions must map to an accountability state."""
    cfg.reload_config()
    dispositions = cfg.get("follow_up_dispositions")
    for d in dispositions:
        assert "accountability" in d, f"{d['label']} missing accountability"
        assert d["accountability"] in ("my_action", "waiting_external", "scheduled")


def test_milestone_profiles_exist(tmp_db):
    cfg.reload_config()
    profiles = cfg.get("milestone_profiles")
    assert len(profiles) >= 3
    names = [p["name"] for p in profiles]
    assert "Full Renewal" in names
    assert "Standard Renewal" in names
    assert "Simple Renewal" in names


def test_timeline_engine_config(tmp_db):
    cfg.reload_config()
    te = cfg.get("timeline_engine")
    assert te["minimum_gap_days"] == 3
    assert te["drift_threshold_days"] == 7
    assert te["compression_threshold"] == 0.5


# ── Task 3: Timeline Generation Tests ──────────────────────────────────


from datetime import date, timedelta
from policydb.timeline_engine import generate_policy_timelines, get_policy_timeline


def _insert_test_client(conn, client_id=1, name="Acme Corp"):
    """Helper to insert a test client with required NOT NULL fields."""
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (?, ?, 'Other')",
        (client_id, name),
    )


def _insert_test_policy(conn, policy_uid, client_id, eff_date, exp_date, **kw):
    """Helper to insert a test policy with required NOT NULL fields."""
    cols = {
        "policy_uid": policy_uid,
        "client_id": client_id,
        "effective_date": eff_date,
        "expiration_date": exp_date,
        "policy_type": "General Liability",
        "is_opportunity": 0,
        "archived": 0,
        "milestone_profile": "",
    }
    cols.update(kw)
    keys = ", ".join(cols.keys())
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO policies ({keys}) VALUES ({placeholders})",
        tuple(cols.values()),
    )


def test_generate_timeline_standalone_policy(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    _insert_test_client(conn)
    _insert_test_policy(conn, 'POL-001', 1, eff_date, exp_date,
                        milestone_profile='Simple Renewal')
    conn.commit()
    generate_policy_timelines(conn)
    timeline = get_policy_timeline(conn, 'POL-001')
    milestone_names = [row["milestone_name"] for row in timeline]
    assert "Quote Received" in milestone_names
    assert "Client Approved" in milestone_names
    assert "Binder Requested" in milestone_names
    assert "RSM Meeting" not in milestone_names


def test_generate_timeline_ideal_equals_projected_initially(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    _insert_test_client(conn)
    _insert_test_policy(conn, 'POL-001', 1, eff_date, exp_date,
                        milestone_profile='Simple Renewal')
    conn.commit()
    generate_policy_timelines(conn)
    timeline = get_policy_timeline(conn, 'POL-001')
    for row in timeline:
        assert row["ideal_date"] == row["projected_date"]


def test_skip_child_policies_in_program(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    _insert_test_client(conn)
    _insert_test_policy(conn, 'PGM-001', 1, eff_date, exp_date,
                        id=1, is_program=1, milestone_profile='Full Renewal')
    _insert_test_policy(conn, 'POL-002', 1, eff_date, exp_date,
                        id=2, program_id=1, milestone_profile='')
    conn.commit()
    generate_policy_timelines(conn)
    pgm_timeline = get_policy_timeline(conn, 'PGM-001')
    assert len(pgm_timeline) > 0
    child_timeline = get_policy_timeline(conn, 'POL-002')
    assert len(child_timeline) == 0


def test_skip_opportunities(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    _insert_test_client(conn)
    _insert_test_policy(conn, 'OPP-001', 1, eff_date, exp_date,
                        is_opportunity=1, milestone_profile='Simple Renewal')
    conn.commit()
    generate_policy_timelines(conn)
    timeline = get_policy_timeline(conn, 'OPP-001')
    assert len(timeline) == 0


def test_default_profile_when_empty(tmp_db):
    conn = get_connection(tmp_db)
    exp_date = (date.today() + timedelta(days=150)).isoformat()
    eff_date = (date.today() - timedelta(days=215)).isoformat()
    _insert_test_client(conn)
    _insert_test_policy(conn, 'POL-001', 1, eff_date, exp_date,
                        milestone_profile='')
    conn.commit()
    generate_policy_timelines(conn)
    timeline = get_policy_timeline(conn, 'POL-001')
    names = [r["milestone_name"] for r in timeline]
    assert "Quote Received" in names
    assert "RSM Meeting" not in names


# ── Task 4: Health Computation Tests ───────────────────────────────────


from policydb.timeline_engine import compute_health


def test_health_on_track():
    result = compute_health(
        projected_date=date.today() + timedelta(days=14),
        ideal_date=date.today() + timedelta(days=16),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30, current_spacing=28,
    )
    assert result == "on_track"


def test_health_completed_is_on_track():
    result = compute_health(
        projected_date=date.today() - timedelta(days=5),
        ideal_date=date.today() - timedelta(days=10),
        completed_date=date.today() - timedelta(days=3),
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30, current_spacing=28,
    )
    assert result == "on_track"


def test_health_drifting():
    result = compute_health(
        projected_date=date.today() + timedelta(days=10),
        ideal_date=date.today() + timedelta(days=25),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30, current_spacing=28,
    )
    assert result == "drifting"


def test_health_compressed():
    result = compute_health(
        projected_date=date.today() + timedelta(days=14),
        ideal_date=date.today() + timedelta(days=16),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=120),
        is_critical_milestone=False,
        original_spacing=30, current_spacing=12,
    )
    assert result == "compressed"


def test_health_at_risk_overdue():
    result = compute_health(
        projected_date=date.today() - timedelta(days=3),
        ideal_date=date.today() - timedelta(days=3),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=60),
        is_critical_milestone=False,
        original_spacing=30, current_spacing=28,
    )
    assert result == "at_risk"


def test_health_at_risk_imminent():
    result = compute_health(
        projected_date=date.today() + timedelta(days=3),
        ideal_date=date.today() + timedelta(days=3),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=60),
        is_critical_milestone=False,
        original_spacing=30, current_spacing=28,
    )
    assert result == "at_risk"


def test_health_critical():
    result = compute_health(
        projected_date=date.today() + timedelta(days=10),
        ideal_date=date.today() + timedelta(days=10),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=25),
        is_critical_milestone=True,
        original_spacing=30, current_spacing=28,
    )
    assert result == "critical"


def test_health_evaluation_order_critical_wins():
    result = compute_health(
        projected_date=date.today() - timedelta(days=5),
        ideal_date=date.today() - timedelta(days=5),
        completed_date=None,
        expiration_date=date.today() + timedelta(days=20),
        is_critical_milestone=True,
        original_spacing=30, current_spacing=28,
    )
    assert result == "critical"
