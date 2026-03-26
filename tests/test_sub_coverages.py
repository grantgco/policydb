import pytest
import sqlite3
from policydb.db import init_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    init_db(path=db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_sub_coverages_table_exists(db):
    """policy_sub_coverages table is created by migration 090."""
    tables = [
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "policy_sub_coverages" in tables


def test_sub_coverages_unique_constraint(db):
    """Cannot insert duplicate (policy_id, coverage_type) pair."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-TEST', 0, 'Workers Compensation')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-TEST'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, ?)",
        (pid, "Employers Liability"),
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, ?)",
            (pid, "Employers Liability"),
        )


def test_sub_coverages_cascade_delete(db):
    """Deleting a policy cascades to its sub-coverages."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-DEL', 0, 'Business Owners Policy')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-DEL'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, ?)",
        (pid, "General Liability"),
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("DELETE FROM policies WHERE id = ?", (pid,))
    db.commit()
    rows = db.execute(
        "SELECT * FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert len(rows) == 0


from policydb.config import _DEFAULTS
import policydb.config as cfg


def test_config_has_bop_policy_type():
    """Business Owners Policy and Employers Liability are in the default policy_types list."""
    types = _DEFAULTS["policy_types"]
    assert "Business Owners Policy" in types
    assert "Employers Liability" in types


def test_config_has_auto_sub_coverages():
    """auto_sub_coverages default maps WC to EL."""
    auto = _DEFAULTS["auto_sub_coverages"]
    assert auto.get("Workers Compensation") == ["Employers Liability"]


from policydb.utils import normalize_coverage_type


def test_bop_normalizes_to_business_owners():
    """BOP variants normalize to Business Owners Policy, not Property."""
    assert normalize_coverage_type("BOP") == "Business Owners Policy"
    assert normalize_coverage_type("bop policy") == "Business Owners Policy"
    assert normalize_coverage_type("businessowners") == "Business Owners Policy"
    assert normalize_coverage_type("Business Owners Policy") == "Business Owners Policy"


def test_property_aliases_unchanged():
    """Property aliases still normalize correctly (regression check)."""
    assert normalize_coverage_type("commercial property") == "Property / Builders Risk"
    assert normalize_coverage_type("building") == "Property / Builders Risk"


def test_auto_generate_wc_creates_el(db):
    """Creating a WC policy auto-inserts Employers Liability sub-coverage."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-WC', 0, 'Workers Compensation')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-WC'").fetchone()[0]

    from policydb.queries import auto_generate_sub_coverages
    auto_generate_sub_coverages(db, pid, "Workers Compensation")

    rows = db.execute(
        "SELECT coverage_type FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert [r[0] for r in rows] == ["Employers Liability"]


def test_auto_generate_no_op_for_gl(db):
    """GL has no auto-sub-coverages configured."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-GL', 0, 'General Liability')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-GL'").fetchone()[0]

    from policydb.queries import auto_generate_sub_coverages
    auto_generate_sub_coverages(db, pid, "General Liability")

    rows = db.execute(
        "SELECT * FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert len(rows) == 0


def test_auto_generate_idempotent(db):
    """Calling auto-generate twice doesn't create duplicates."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-WC2', 0, 'Workers Compensation')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-WC2'").fetchone()[0]

    from policydb.queries import auto_generate_sub_coverages
    auto_generate_sub_coverages(db, pid, "Workers Compensation")
    auto_generate_sub_coverages(db, pid, "Workers Compensation")

    rows = db.execute(
        "SELECT * FROM policy_sub_coverages WHERE policy_id = ?", (pid,)
    ).fetchall()
    assert len(rows) == 1


def test_sub_coverages_email_token(db):
    """policy_context returns comma-separated sub-coverages token."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', '')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-TOK', 1, 'Business Owners Policy')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-TOK'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, sort_order) VALUES (?, ?, ?)",
        (pid, "General Liability", 0),
    )
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, sort_order) VALUES (?, ?, ?)",
        (pid, "Property / Builders Risk", 1),
    )
    db.commit()

    from policydb.email_templates import policy_context
    tokens = policy_context(db, "POL-TOK")
    assert tokens["sub_coverages"] == "General Liability, Property / Builders Risk"


def test_sub_coverages_token_empty_when_none(db):
    """policy_context returns empty string when no sub-coverages."""
    db.execute("INSERT OR IGNORE INTO clients (id, name, industry_segment) VALUES (2, 'Another Client', '')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type) "
        "VALUES ('POL-NONE', 2, 'General Liability')"
    )
    db.commit()

    from policydb.email_templates import policy_context
    tokens = policy_context(db, "POL-NONE")
    assert tokens["sub_coverages"] == ""
