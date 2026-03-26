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
