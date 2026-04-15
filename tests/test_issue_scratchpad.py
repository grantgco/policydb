"""Tests for the per-issue working notes (scratchpad) — migration 152.

Mirrors the policy/client scratchpad pattern:
- Auto-save POST persists content
- Log-as-activity creates an activity_log row linked to the issue (issue_id)
  and clears the scratchpad
- Clear wipes the content without creating an activity
- Scratchpad is scoped by issue_uid and isolated per issue
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


def _seed_client(conn, name="Scratch Test Co"):
    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES (?, 'Construction')",
        (name,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_issue(conn, client_id, issue_uid="ISS-9001", subject="Test issue"):
    """Insert a manual issue header row and return its (id, issue_uid)."""
    conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, activity_type, subject, details,
            account_exec, item_kind, issue_uid, is_renewal_issue,
            issue_status, issue_severity)
           VALUES (date('now'), ?, 'Note', ?, '', 'Grant', 'issue', ?, 0,
                   'Open', 'Normal')""",
        (client_id, subject, issue_uid),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return issue_id, issue_uid


def test_migration_152_creates_issue_scratchpad_table(tmp_db):
    conn = get_connection()
    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "issue_scratchpad" in tables
    cols = [r[1] for r in conn.execute("PRAGMA table_info(issue_scratchpad)").fetchall()]
    assert set(cols) == {"issue_id", "content", "updated_at"}


def test_autosave_persists_content(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    cid = _seed_client(conn)
    _, issue_uid = _seed_issue(conn, cid, "ISS-9001")

    client = TestClient(app)
    resp = client.post(
        f"/issues/{issue_uid}/scratchpad",
        data={"content": "Carrier owes us endorsement by Friday."},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    conn2 = get_connection()
    row = conn2.execute(
        """SELECT content FROM issue_scratchpad
           WHERE issue_id = (SELECT id FROM activity_log WHERE issue_uid = ?)""",
        (issue_uid,),
    ).fetchone()
    assert row is not None
    assert row["content"] == "Carrier owes us endorsement by Friday."


def test_autosave_updates_existing_row(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    cid = _seed_client(conn)
    _, issue_uid = _seed_issue(conn, cid, "ISS-9002")

    client = TestClient(app)
    client.post(f"/issues/{issue_uid}/scratchpad",
                data={"content": "first draft"},
                headers={"Accept": "application/json"})
    client.post(f"/issues/{issue_uid}/scratchpad",
                data={"content": "second draft — more specific"},
                headers={"Accept": "application/json"})

    conn2 = get_connection()
    rows = conn2.execute(
        """SELECT content FROM issue_scratchpad
           WHERE issue_id = (SELECT id FROM activity_log WHERE issue_uid = ?)""",
        (issue_uid,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "second draft — more specific"


def test_autosave_404_for_unknown_issue(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    client = TestClient(app)
    resp = client.post(
        "/issues/ISS-NOPE/scratchpad",
        data={"content": "anything"},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 404


def test_log_as_activity_creates_linked_activity_and_clears(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    cid = _seed_client(conn)
    issue_id, issue_uid = _seed_issue(conn, cid, "ISS-9003")

    client = TestClient(app)
    # Pre-fill the scratchpad
    client.post(f"/issues/{issue_uid}/scratchpad",
                data={"content": "Spoke with UW, waiting on quote rev."},
                headers={"Accept": "application/json"})

    # Log as activity
    resp = client.post(
        "/inbox/scratchpad/process",
        data={
            "source": "issue",
            "scope_id": issue_uid,
            "activity_type": "Note",
            "subject": "UW conversation",
        },
    )
    assert resp.status_code == 200

    # A new activity row exists, linked to the issue via issue_id and
    # carrying the client_id from the issue header.
    conn2 = get_connection()
    activity = conn2.execute(
        """SELECT client_id, issue_id, subject, details
           FROM activity_log
           WHERE item_kind != 'issue' AND issue_id = ?
           ORDER BY id DESC LIMIT 1""",
        (issue_id,),
    ).fetchone()
    assert activity is not None
    assert activity["client_id"] == cid
    assert activity["issue_id"] == issue_id
    assert activity["subject"] == "UW conversation"
    assert "waiting on quote rev" in activity["details"]

    # Scratchpad is cleared
    scratch = conn2.execute(
        "SELECT content FROM issue_scratchpad WHERE issue_id = ?",
        (issue_id,),
    ).fetchone()
    assert scratch["content"] == ""


def test_clear_wipes_without_creating_activity(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    cid = _seed_client(conn)
    issue_id, issue_uid = _seed_issue(conn, cid, "ISS-9004")

    client = TestClient(app)
    client.post(f"/issues/{issue_uid}/scratchpad",
                data={"content": "junk thoughts, discard me"},
                headers={"Accept": "application/json"})

    # Capture activity count before clearing
    conn2 = get_connection()
    before = conn2.execute(
        "SELECT COUNT(*) FROM activity_log WHERE item_kind != 'issue' AND issue_id = ?",
        (issue_id,),
    ).fetchone()[0]

    resp = client.post(
        "/inbox/scratchpad/clear",
        data={"source": "issue", "scope_id": issue_uid},
    )
    assert resp.status_code == 200

    conn3 = get_connection()
    scratch = conn3.execute(
        "SELECT content FROM issue_scratchpad WHERE issue_id = ?",
        (issue_id,),
    ).fetchone()
    assert scratch["content"] == ""

    after = conn3.execute(
        "SELECT COUNT(*) FROM activity_log WHERE item_kind != 'issue' AND issue_id = ?",
        (issue_id,),
    ).fetchone()[0]
    assert after == before, "clear must not create activities"


def test_scratchpads_are_isolated_per_issue(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    cid = _seed_client(conn)
    id_a, uid_a = _seed_issue(conn, cid, "ISS-9010", "Issue A")
    id_b, uid_b = _seed_issue(conn, cid, "ISS-9011", "Issue B")

    client = TestClient(app)
    client.post(f"/issues/{uid_a}/scratchpad",
                data={"content": "notes for A"},
                headers={"Accept": "application/json"})
    client.post(f"/issues/{uid_b}/scratchpad",
                data={"content": "notes for B"},
                headers={"Accept": "application/json"})

    conn2 = get_connection()
    a = conn2.execute(
        "SELECT content FROM issue_scratchpad WHERE issue_id=?", (id_a,)
    ).fetchone()
    b = conn2.execute(
        "SELECT content FROM issue_scratchpad WHERE issue_id=?", (id_b,)
    ).fetchone()
    assert a["content"] == "notes for A"
    assert b["content"] == "notes for B"


def test_issue_detail_page_includes_scratchpad_widget(tmp_db):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    conn = get_connection()
    cid = _seed_client(conn)
    _, issue_uid = _seed_issue(conn, cid, "ISS-9020", "Detail render test")

    client = TestClient(app)
    # Pre-fill so we can assert content is rendered
    client.post(f"/issues/{issue_uid}/scratchpad",
                data={"content": "persisted draft"},
                headers={"Accept": "application/json"})

    resp = client.get(f"/issues/{issue_uid}")
    assert resp.status_code == 200
    assert "Working Notes" in resp.text
    assert "persisted draft" in resp.text
    assert f"issue-scratchpad-{issue_uid}" in resp.text
