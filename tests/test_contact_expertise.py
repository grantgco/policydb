"""Tests for contact expertise tracking."""
import pytest
from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_contact_expertise_table_exists(tmp_db):
    conn = get_connection(tmp_db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "contact_expertise" in tables
    conn.close()


def test_expertise_notes_column(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(contacts)").fetchall()]
    assert "expertise_notes" in cols
    conn.close()


def test_expertise_tagging(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO contacts (name) VALUES ('John Smith')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'Casualty')", (cid,))
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'industry', 'Sports & Entertainment')", (cid,))
    conn.commit()
    tags = conn.execute("SELECT category, tag FROM contact_expertise WHERE contact_id = ? ORDER BY category", (cid,)).fetchall()
    assert len(tags) == 2
    assert tags[0]["category"] == "industry"
    assert tags[0]["tag"] == "Sports & Entertainment"
    assert tags[1]["category"] == "line"
    assert tags[1]["tag"] == "Casualty"
    conn.close()


def test_expertise_unique_constraint(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO contacts (name) VALUES ('Jane Doe')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'Property')", (cid,))
    conn.commit()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'Property')", (cid,))
    conn.close()


def test_expertise_cascade_delete(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO contacts (name) VALUES ('Bob Wilson')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO contact_expertise (contact_id, category, tag) VALUES (?, 'line', 'D&O')", (cid,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM contact_expertise WHERE contact_id = ?", (cid,)).fetchone()[0] == 0
    conn.close()
