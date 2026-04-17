"""Route tests for Phase 4 Timesheet Review."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    from policydb.db import init_db
    init_db(path=db_path)
    from policydb.web.app import app
    return TestClient(app)


def test_timesheet_panel_default_returns_200(client):
    resp = client.get("/timesheet/panel")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text


def test_timesheet_panel_accepts_kind_week(client):
    resp = client.get("/timesheet/panel?kind=week")
    assert resp.status_code == 200


def test_timesheet_panel_accepts_explicit_range(client):
    resp = client.get("/timesheet/panel?kind=day&start=2026-04-15&end=2026-04-15")
    assert resp.status_code == 200


def _make_activity(client, *, subject="Test", hours=0.1, source="manual"):
    """Insert an activity via raw SQL (fast path) and return its id."""
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('Cust', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, subject, activity_type,
            duration_hours, source, item_kind)
           VALUES (date('now'), ?, ?, 'Email', ?, ?, 'activity')""",
        (cid, subject, hours, source),
    )
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


# ---------------------------------------------------------------------------
# Task 10: POST /activity/{id}/review
# ---------------------------------------------------------------------------

def test_post_review_stamps_reviewed_at(client):
    aid = _make_activity(client)
    resp = client.post(f"/timesheet/activity/{aid}/review")
    assert resp.status_code in (200, 204)
    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row["reviewed_at"] is not None
    conn.close()


def test_post_review_is_idempotent(client):
    aid = _make_activity(client)
    client.post(f"/timesheet/activity/{aid}/review")
    from policydb.db import get_connection
    conn = get_connection()
    first = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()["reviewed_at"]
    conn.close()

    resp = client.post(f"/timesheet/activity/{aid}/review")
    assert resp.status_code in (200, 204)
    conn = get_connection()
    second = conn.execute(
        "SELECT reviewed_at FROM activity_log WHERE id=?", (aid,)
    ).fetchone()["reviewed_at"]
    assert first == second
    conn.close()


def test_post_review_404_on_missing(client):
    resp = client.post("/timesheet/activity/999999/review")
    assert resp.status_code == 404
