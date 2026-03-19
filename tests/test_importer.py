"""Tests for the CSV import system."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from policydb.db import get_connection, init_db
from policydb.importer import (
    PolicyImporter,
    ClientImporter,
    _parse_currency,
    _parse_date,
    _parse_bool,
    _normalize_renewal_status,
)


# ─── UNIT TESTS: PARSING HELPERS ─────────────────────────────────────────────

def test_parse_currency_basic():
    assert _parse_currency("$85,000") == 85000.0
    assert _parse_currency("$1,234,567.89") == 1234567.89
    assert _parse_currency("0") == 0.0
    assert _parse_currency("") == 0.0
    assert _parse_currency("100000") == 100000.0


def test_parse_currency_strips_symbols():
    assert _parse_currency("$42,500.00") == 42500.0
    assert _parse_currency("42500") == 42500.0


def test_parse_date_iso():
    assert _parse_date("2025-01-15") == "2025-01-15"


def test_parse_date_us_format():
    result = _parse_date("01/15/2025")
    assert result == "2025-01-15"


def test_parse_date_short_year():
    result = _parse_date("1/15/25")
    assert result is not None
    assert "2025" in result


def test_parse_date_invalid():
    assert _parse_date("not-a-date") is None
    assert _parse_date("") is None


def test_parse_bool():
    assert _parse_bool("1") == 1
    assert _parse_bool("true") == 1
    assert _parse_bool("yes") == 1
    assert _parse_bool("Y") == 1
    assert _parse_bool("0") == 0
    assert _parse_bool("false") == 0
    assert _parse_bool("") == 0


def test_normalize_renewal_status():
    assert _normalize_renewal_status("Not Started") == "Not Started"
    assert _normalize_renewal_status("In Progress") == "In Progress"
    assert _normalize_renewal_status("Bound") == "Bound"
    assert _normalize_renewal_status("") == "Not Started"
    # Fuzzy
    result = _normalize_renewal_status("bound")
    assert result == "Bound"


# ─── INTEGRATION TESTS: CSV IMPORT ───────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


@pytest.fixture
def conn(tmp_db):
    c = get_connection(tmp_db)
    # Pre-create a client (normalized name: 'Corp' suffix → 'Corp.')
    c.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Test Corp.', 'Technology', 'Grant')"
    )
    c.commit()
    yield c
    c.close()


def _write_csv(tmp_path, filename, content):
    p = tmp_path / filename
    p.write_text(content)
    return p


def test_policy_import_basic(conn, tmp_path):
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date,premium\n"
        "Test Corp,General Liability,Zurich,2025-01-01,2026-01-01,50000\n"
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    importer.import_csv(csv_file, interactive=False)
    assert importer.imported == 1
    assert importer.skipped == 0
    row = conn.execute("SELECT * FROM policies WHERE client_id = (SELECT id FROM clients WHERE name='Test Corp.')").fetchone()
    assert row is not None
    assert row["policy_type"] == "General Liability"
    assert row["premium"] == 50000.0


def test_policy_import_currency_strings(conn, tmp_path):
    # Currency values with commas must be quoted in CSV (standard CSV behavior)
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date,premium,limit\n"
        'Test Corp,General Liability,Zurich,2025-01-01,2026-01-01,"$50,000","$2,000,000"\n'
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    importer.import_csv(csv_file, interactive=False)
    assert importer.imported == 1
    row = conn.execute("SELECT premium, limit_amount FROM policies").fetchone()
    assert row["premium"] == 50000.0
    assert row["limit_amount"] == 2000000.0


def test_policy_import_missing_optional_columns(conn, tmp_path):
    """Import should succeed even without optional columns."""
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date,premium\n"
        "Test Corp,General Liability,Zurich,2025-01-01,2026-01-01,50000\n"
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    importer.import_csv(csv_file, interactive=False)
    assert importer.imported == 1


def test_policy_import_bad_dates(conn, tmp_path):
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date,premium\n"
        "Test Corp,General Liability,Zurich,NOT-A-DATE,ALSO-BAD,50000\n"
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    importer.import_csv(csv_file, interactive=False)
    assert importer.imported == 0
    assert importer.skipped == 1
    assert len(importer.warnings) == 1


def test_policy_import_missing_required_column(conn, tmp_path):
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date\n"
        "Test Corp,GL,Zurich,2025-01-01,2026-01-01\n"
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    import click
    with pytest.raises(click.ClickException):
        importer.import_csv(csv_file, interactive=False)


def test_policy_import_multiple_date_formats(conn, tmp_path):
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date,premium\n"
        "Test Corp,General Liability,Zurich,01/15/2025,01/15/2026,50000\n"
        "Test Corp,Workers Compensation,Hartford,1/1/25,1/1/26,30000\n"
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    importer.import_csv(csv_file, interactive=False)
    assert importer.imported == 2


def test_client_import_basic(conn, tmp_path):
    csv_content = (
        "name,industry_segment\n"
        "New Client Co,Technology\n"
    )
    csv_file = _write_csv(tmp_path, "clients.csv", csv_content)
    importer = ClientImporter(conn)
    importer.import_csv(csv_file)
    assert importer.imported == 1
    row = conn.execute("SELECT * FROM clients WHERE name='New Client Co.'").fetchone()
    assert row is not None


def test_client_import_skips_duplicates(conn, tmp_path):
    csv_content = (
        "name,industry_segment\n"
        "Test Corp,Technology\n"  # Already exists
    )
    csv_file = _write_csv(tmp_path, "clients.csv", csv_content)
    importer = ClientImporter(conn)
    importer.import_csv(csv_file)
    assert importer.skipped == 1
    assert importer.imported == 0


def test_policy_tracks_missing_descriptions(conn, tmp_path):
    csv_content = (
        "client_name,policy_type,carrier,effective_date,expiration_date,premium\n"
        "Test Corp,General Liability,Zurich,2025-01-01,2026-01-01,50000\n"
    )
    csv_file = _write_csv(tmp_path, "policies.csv", csv_content)
    importer = PolicyImporter(conn)
    importer.import_csv(csv_file, interactive=False)
    assert len(importer._missing_descriptions) == 1
