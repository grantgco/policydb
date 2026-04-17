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
    conn.execute(
        "INSERT OR IGNORE INTO clients (name, industry_segment, account_exec) VALUES ('Cust', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='Cust'").fetchone()["id"]
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


# ---------------------------------------------------------------------------
# Task 11: PATCH /activity/{id}
# ---------------------------------------------------------------------------

def test_patch_activity_updates_duration_hours(client):
    aid = _make_activity(client, hours=0.1)
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"duration_hours": "1.25"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "total_hours" in body

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT duration_hours, reviewed_at FROM activity_log WHERE id=?",
        (aid,),
    ).fetchone()
    # round-to-0.1 per feedback_hours_any_numeric: 1.25 -> 1.3
    assert abs(float(row["duration_hours"]) - 1.3) < 0.001
    assert row["reviewed_at"] is not None
    conn.close()


def test_patch_activity_updates_subject_and_type(client):
    aid = _make_activity(client)
    resp = client.patch(f"/timesheet/activity/{aid}",
                        data={"subject": "New subject", "activity_type": "Call"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT subject, activity_type FROM activity_log WHERE id=?", (aid,)
    ).fetchone()
    assert row["subject"] == "New subject"
    assert row["activity_type"] == "Call"
    conn.close()


def test_patch_activity_404_on_missing(client):
    resp = client.patch("/timesheet/activity/999999",
                        data={"duration_hours": "1.0"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 12: POST /activity (create new row)
# ---------------------------------------------------------------------------

def test_post_activity_creates_row_and_stamps_reviewed(client):
    from policydb.db import get_connection
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES ('NewClient', 'Tech', 'Grant')"
    )
    cid = cur.lastrowid
    conn.commit()
    conn.close()

    resp = client.post(
        "/timesheet/activity",
        data={
            "client_id": str(cid),
            "activity_date": "2026-04-15",
            "subject": "Forgotten phone call",
            "activity_type": "Call",
            "duration_hours": "0.5",
        },
    )
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert body["ok"] is True
    new_id = body["id"]

    conn = get_connection()
    row = conn.execute(
        "SELECT subject, duration_hours, reviewed_at, item_kind FROM activity_log WHERE id=?",
        (new_id,),
    ).fetchone()
    assert row["subject"] == "Forgotten phone call"
    assert float(row["duration_hours"]) == 0.5
    assert row["reviewed_at"] is not None
    assert row["item_kind"] == "activity"
    conn.close()


def test_post_activity_requires_client(client):
    resp = client.post(
        "/timesheet/activity",
        data={"activity_date": "2026-04-15", "subject": "nope"},
    )
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Task 13: DELETE /activity/{id}
# ---------------------------------------------------------------------------

def test_delete_activity_removes_row(client):
    aid = _make_activity(client)
    resp = client.delete(f"/timesheet/activity/{aid}")
    assert resp.status_code in (200, 204)

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM activity_log WHERE id=?", (aid,)).fetchone()
    assert row is None
    conn.close()


def test_delete_activity_404_on_missing(client):
    resp = client.delete("/timesheet/activity/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 14: POST /closeout + POST /closeout/{id}/reopen
# ---------------------------------------------------------------------------

def test_post_closeout_creates_row_and_bulk_stamps(client):
    aid1 = _make_activity(client)
    aid2 = _make_activity(client)
    week_start = "2026-04-13"

    resp = client.post("/timesheet/closeout", data={"week_start": week_start})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    from policydb.db import get_connection
    conn = get_connection()
    co = conn.execute(
        "SELECT * FROM timesheet_closeouts WHERE week_start=?", (week_start,)
    ).fetchone()
    assert co is not None

    stamped = conn.execute(
        "SELECT COUNT(*) AS n FROM activity_log WHERE reviewed_at IS NOT NULL"
    ).fetchone()["n"]
    assert stamped >= 2
    conn.close()


def test_post_closeout_rejects_duplicate_week(client):
    week_start = "2026-04-13"
    client.post("/timesheet/closeout", data={"week_start": week_start})
    resp = client.post("/timesheet/closeout", data={"week_start": week_start})
    assert resp.status_code == 409


def test_post_reopen_deletes_closeout(client):
    week_start = "2026-04-13"
    first = client.post("/timesheet/closeout", data={"week_start": week_start})
    co_id = first.json()["id"]
    resp = client.post(f"/timesheet/closeout/{co_id}/reopen")
    assert resp.status_code == 200

    from policydb.db import get_connection
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM timesheet_closeouts WHERE id=?", (co_id,)).fetchone()
    assert row is None
    conn.close()


def test_post_closeout_rejects_non_monday(client):
    resp = client.post("/timesheet/closeout", data={"week_start": "2026-04-15"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Task 15: Range cap behavior lock
# ---------------------------------------------------------------------------

def test_range_exceeding_cap_returns_400(client):
    resp = client.get("/timesheet/panel?kind=range&start=2025-01-01&end=2026-04-15")
    assert resp.status_code == 400


def test_range_below_cap_returns_200(client):
    resp = client.get("/timesheet/panel?kind=range&start=2026-04-01&end=2026-04-30")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Task 16: GET /timesheet full-page wrapper
# ---------------------------------------------------------------------------

def test_full_page_renders(client):
    resp = client.get("/timesheet")
    assert resp.status_code == 200
    assert "timesheet-panel" in resp.text
    assert "<html" in resp.text.lower()
