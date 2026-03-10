"""Tests for export system."""

import json
import sqlite3

import pytest

from policydb.db import get_connection, init_db
from policydb.seed import run_seed
from policydb.exporter import (
    export_schedule_md,
    export_schedule_csv,
    export_schedule_json,
    export_llm_client_md,
    export_llm_client_json,
    export_llm_book_md,
)


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    conn = get_connection(db_path)

    # Insert minimal test data without calling seed (avoids click prompts)
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Acme Corp', 'Technology', 'Grant')"
    )
    client_id = conn.execute("SELECT id FROM clients WHERE name='Acme Corp'").fetchone()["id"]
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, limit_amount, deductible,
            description, renewal_status, commission_rate, prior_premium, account_exec, notes)
           VALUES ('POL-001', ?, 'General Liability', 'Zurich', 'GL-12345',
                   '2025-01-01', '2026-01-01', 50000, 2000000, 25000,
                   'Covers GL for all operations.', 'In Progress', 0.12, 45000, 'Grant', 'Internal note.')""",
        (client_id,),
    )
    conn.execute(
        """INSERT INTO policies
           (policy_uid, client_id, policy_type, carrier, policy_number,
            effective_date, expiration_date, premium, renewal_status,
            commission_rate, account_exec)
           VALUES ('POL-002', ?, 'Cyber / Tech E&O', 'Coalition', 'CYBER-99',
                   '2025-01-01', '2026-01-01', 85000, 'Bound', 0.08, 'Grant')""",
        (client_id,),
    )
    conn.commit()
    yield db_path, client_id, conn
    conn.close()


def test_schedule_md_excludes_internal_fields(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_schedule_md(conn, client_id, "Acme Corp")
    # Internal fields must not appear
    assert "commission_rate" not in content
    assert "commission_amount" not in content
    assert "prior_premium" not in content
    assert "renewal_status" not in content
    assert "Internal note" not in content
    # Client-facing content must be present
    assert "Acme Corp" in content
    assert "General Liability" in content
    assert "Zurich" in content
    assert "Covers GL for all operations" in content


def test_schedule_csv_excludes_internal_fields(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_schedule_csv(conn, client_id)
    assert "commission_rate" not in content
    assert "prior_premium" not in content
    assert "renewal_status" not in content


def test_schedule_json_excludes_internal_fields(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_schedule_json(conn, client_id, "Acme Corp")
    data = json.loads(content)
    policies = data["policies"]
    assert len(policies) > 0
    for p in policies:
        assert "commission_rate" not in p
        assert "prior_premium" not in p
        assert "renewal_status" not in p


def test_llm_export_includes_internal_fields(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_llm_client_md(conn, client_id)
    # LLM export should include renewal status and internal data
    assert "In Progress" in content or "Bound" in content
    assert "Grant" in content  # account exec
    assert "Acme Corp" in content


def test_llm_export_json_structure(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_llm_client_json(conn, client_id)
    data = json.loads(content)
    assert "metadata" in data
    assert data["metadata"]["export_type"] == "client_program"
    assert "client" in data
    assert "policies" in data
    assert len(data["policies"]) == 2
    # Computed fields should be present
    for p in data["policies"]:
        assert "computed" in p
        assert "days_to_renewal" in p["computed"]
        assert "urgency" in p["computed"]


def test_schedule_md_total_premium(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_schedule_md(conn, client_id, "Acme Corp")
    # Total: 50000 + 85000 = 135000
    assert "135,000" in content or "135000" in content


def test_llm_book_md_structure(seeded_db):
    db_path, client_id, conn = seeded_db
    content = export_llm_book_md(conn)
    assert "Book of Business" in content
    assert "Acme Corp" in content
    assert "export_type: book_of_business" in content
