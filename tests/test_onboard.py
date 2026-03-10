"""Tests for analysis engine (coverage gaps, tower detection, etc.)."""

import pytest

from policydb.db import get_connection, init_db
from policydb.analysis import (
    run_coverage_gap_analysis,
    detect_towers,
    detect_standalones,
    find_duplicate_policies,
    cluster_expirations,
)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    c = get_connection(db_path)
    c.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Test RE', 'Real Estate Development', 'Grant')"
    )
    c.commit()
    yield c
    c.close()


def _add_policy(conn, client_id, policy_type, carrier="Zurich", tower_group=None, pol_number=None):
    from datetime import date
    today = date.today().isoformat()
    uid = f"POL-{conn.execute('SELECT COUNT(*) FROM policies').fetchone()[0]+1:03d}"
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, account_exec, tower_group)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, client_id, policy_type, carrier, pol_number, today, today, 10000, "Grant", tower_group),
    )
    conn.commit()
    return uid


def test_coverage_gap_gl_without_umbrella(conn):
    client_id = conn.execute("SELECT id FROM clients WHERE name='Test RE'").fetchone()["id"]
    _add_policy(conn, client_id, "General Liability")
    gaps = run_coverage_gap_analysis(conn, client_id)
    messages = " ".join(gaps)
    assert "Umbrella" in messages


def test_coverage_gap_gl_with_umbrella_no_gap(conn):
    client_id = conn.execute("SELECT id FROM clients WHERE name='Test RE'").fetchone()["id"]
    _add_policy(conn, client_id, "General Liability")
    _add_policy(conn, client_id, "Umbrella / Excess")
    gaps = run_coverage_gap_analysis(conn, client_id)
    # GL + Umbrella gap should not appear
    assert not any("Umbrella" in g for g in gaps)


def test_coverage_gap_re_without_eo(conn):
    client_id = conn.execute("SELECT id FROM clients WHERE name='Test RE'").fetchone()["id"]
    _add_policy(conn, client_id, "General Liability")
    gaps = run_coverage_gap_analysis(conn, client_id)
    # RE developer without E&O
    assert any("E&O" in g or "Professional" in g for g in gaps)


def test_detect_towers():
    policies = [
        {"tower_group": "GL Tower", "policy_type": "GL", "carrier": "Zurich"},
        {"tower_group": "GL Tower", "policy_type": "Umbrella", "carrier": "Swiss Re"},
        {"tower_group": None, "policy_type": "Cyber", "carrier": "Coalition"},
    ]
    towers = detect_towers(policies)
    assert "GL Tower" in towers
    assert len(towers["GL Tower"]) == 2


def test_detect_standalones():
    policies = [
        {"tower_group": "GL Tower", "policy_type": "GL"},
        {"tower_group": None, "policy_type": "Cyber"},
        {"tower_group": None, "policy_type": "Crime"},
    ]
    standalones = detect_standalones(policies)
    assert len(standalones) == 2


def test_find_duplicate_policy_numbers():
    policies = [
        {"policy_number": "GL-001", "policy_type": "GL", "carrier": "Zurich", "client_id": 1, "tower_group": None},
        {"policy_number": "GL-001", "policy_type": "GL", "carrier": "Zurich", "client_id": 1, "tower_group": None},
    ]
    dupes = find_duplicate_policies(policies)
    assert len(dupes) >= 1


def test_cluster_expirations():
    policies = [
        {"expiration_date": "2025-04-01"},
        {"expiration_date": "2025-04-15"},
        {"expiration_date": "2025-07-01"},
    ]
    clusters = cluster_expirations(policies)
    assert "2025-04" in clusters
    assert len(clusters["2025-04"]) == 2
    assert "2025-07" in clusters
