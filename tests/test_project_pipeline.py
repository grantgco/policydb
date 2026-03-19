"""Tests for project pipeline tracker."""
import pytest
from datetime import date
from policydb.db import get_connection, init_db
from policydb.utils import parse_currency_with_magnitude


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_project_pipeline_columns(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    for col in ["project_type", "status", "project_value", "start_date",
                "target_completion", "insurance_needed_by", "scope_description",
                "general_contractor", "owner_name", "address", "city", "state", "zip"]:
        assert col in cols, f"Missing column: {col}"
    conn.close()


def test_existing_projects_default_to_location(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO projects (client_id, name) VALUES (?, 'HQ')", (cid,))
    conn.commit()
    row = conn.execute("SELECT project_type, status FROM projects WHERE name='HQ'").fetchone()
    assert row["project_type"] == "Location"
    assert row["status"] == "Upcoming"
    conn.close()


def test_pipeline_project_with_all_fields(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Builder', 'Construction')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO projects (client_id, name, project_type, status, project_value,
                                 start_date, target_completion, insurance_needed_by,
                                 general_contractor, owner_name, address, city, state, zip)
           VALUES (?, 'Tower West', 'Construction', 'Quoting', 15000000,
                   '2026-08-01', '2027-12-01', '2026-06-01',
                   'ABC Builders', 'Developer LLC', '100 Main St', 'Austin', 'TX', '78701')""",
        (cid,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE name='Tower West'").fetchone()
    assert row["project_type"] == "Construction"
    assert row["project_value"] == 15000000
    assert row["city"] == "Austin"
    conn.close()


# --- parse_currency_with_magnitude tests ---

def test_parse_currency_plain():
    assert parse_currency_with_magnitude("15000000") == 15000000.0

def test_parse_currency_with_dollar_commas():
    assert parse_currency_with_magnitude("$15,000,000") == 15000000.0

def test_parse_currency_millions():
    assert parse_currency_with_magnitude("$15M") == 15000000.0
    assert parse_currency_with_magnitude("15m") == 15000000.0
    assert parse_currency_with_magnitude("$1.5M") == 1500000.0

def test_parse_currency_thousands():
    assert parse_currency_with_magnitude("$800K") == 800000.0
    assert parse_currency_with_magnitude("800k") == 800000.0

def test_parse_currency_billions():
    assert parse_currency_with_magnitude("$1.2B") == 1200000000.0

def test_parse_currency_empty():
    assert parse_currency_with_magnitude("") == 0.0
    assert parse_currency_with_magnitude(None) == 0.0
