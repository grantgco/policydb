"""Regression: adding an existing contact to a client via the picker must
not fire the "duplicate contact" warning.

A contact ↔ client relationship is many-to-many (contact_client_assignments),
so reusing an existing contact row from the picker is the supported flow, not
a duplicate. The `contact_add` route used to run `_find_similar_contacts` on
every submit — the exact-name match against the existing row returned itself
at 100% and surfaced a false-positive warning.
"""
import pytest

import policydb.web.app  # noqa: F401 — boot FastAPI app

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


def _seed_client(conn, name):
    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES (?, 'Construction')",
        (name,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_contact(conn, name, email=None):
    conn.execute(
        "INSERT INTO contacts (name, email) VALUES (?, ?)",
        (name, email),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_add_existing_contact_does_not_trigger_duplicate_warning(tmp_db):
    """Picker → submit exact name → no duplicate warning on the response."""
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    client_a = _seed_client(conn, "Acme Corp")
    client_b = _seed_client(conn, "Blue Holdings")
    # Pre-seed a contact already attached to client A
    contact_id = _seed_contact(conn, "Alice Johnson", "alice@acme.com")
    conn.execute(
        "INSERT INTO contact_client_assignments (contact_id, client_id, contact_type) "
        "VALUES (?, ?, 'client')",
        (contact_id, client_a),
    )
    conn.commit()

    # Simulate picking Alice from the database list and assigning her to
    # client B (second client).
    client = TestClient(app)
    resp = client.post(
        f"/clients/{client_b}/contacts/add",
        data={
            "name": "Alice Johnson",
            "email": "alice@acme.com",
            "phone": "",
            "mobile": "",
            "title": "",
            "role": "",
            "notes": "",
        },
    )
    assert resp.status_code == 200
    # The "Possible duplicate" warning block must NOT be present.
    assert "Possible duplicate" not in resp.text
    assert "possible duplicate" not in resp.text.lower()

    # Alice should now be linked to BOTH clients via assignments.
    conn2 = get_connection()
    assignments = conn2.execute(
        "SELECT client_id FROM contact_client_assignments WHERE contact_id=?",
        (contact_id,),
    ).fetchall()
    client_ids = {r["client_id"] for r in assignments}
    assert client_ids == {client_a, client_b}

    # And there should still be exactly one Alice Johnson in contacts.
    alice_count = conn2.execute(
        "SELECT COUNT(*) FROM contacts WHERE LOWER(TRIM(name)) = 'alice johnson'"
    ).fetchone()[0]
    assert alice_count == 1


def test_add_new_contact_near_existing_still_warns(tmp_db):
    """A genuinely new-but-similar name should still surface the warning."""
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    client_id = _seed_client(conn, "Acme Corp")
    _seed_contact(conn, "Alice Johnson", "alice@acme.com")
    conn.commit()

    client = TestClient(app)
    resp = client.post(
        f"/clients/{client_id}/contacts/add",
        data={
            "name": "Alice Johnston",  # new name, 1 letter off
            "email": "",
            "phone": "",
            "mobile": "",
            "title": "",
            "role": "",
            "notes": "",
        },
    )
    assert resp.status_code == 200
    # The similarity check should still fire for a near-match new name.
    assert "Possible duplicate" in resp.text or "possible duplicate" in resp.text.lower()
