"""Route tests for the Today tab and task CRUD endpoints."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def app_client(tmp_db):
    from policydb.web.app import app
    with TestClient(app) as c:
        yield c


def test_today_tab_renders(app_client):
    r = app_client.get("/action-center?tab=today")
    assert r.status_code == 200
    assert "Today" in r.text
    assert "today-grid" in r.text  # Tabulator mount point


def test_task_create_with_client(app_client):
    conn = get_connection()
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Acme Co', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    r = app_client.post(
        "/tasks/create",
        data={
            "subject": "Call Acme about renewal",
            "client_id": cid,
            "follow_up_date": "2026-04-19",
            "contact_person": "Sarah Johnson",
        },
    )
    assert r.status_code in (200, 201)
    row = conn.execute("SELECT * FROM activity_log WHERE subject = 'Call Acme about renewal'").fetchone()
    assert row is not None
    assert row["client_id"] == cid
    assert row["contact_person"] == "Sarah Johnson"
    assert row["follow_up_date"] == "2026-04-19"


def test_task_create_standalone(app_client):
    r = app_client.post(
        "/tasks/create",
        data={"subject": "Standalone reminder", "follow_up_date": "2026-04-19"},
    )
    assert r.status_code in (200, 201)
    conn = get_connection()
    row = conn.execute("SELECT * FROM activity_log WHERE subject = 'Standalone reminder'").fetchone()
    assert row is not None
    assert row["client_id"] is None


def test_task_create_rejects_empty_subject(app_client):
    r = app_client.post("/tasks/create", data={"subject": ""})
    assert r.status_code == 422 or r.status_code == 400


def test_task_create_with_policy_supersedes_older_followup(app_client):
    """When creating a task on a policy, older open follow-ups on same policy must close."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES ('Super Co', 'Test')"
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date) "
        "VALUES ('POL-S1', ?, 'GL', 'Test', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES ('2026-04-15', ?, ?, 'Call', 'older', '2026-04-20', 0, 'followup', 'Grant')",
        (cid, pid),
    )
    older_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    r = app_client.post(
        "/tasks/create",
        data={"subject": "newer", "client_id": cid, "policy_id": pid, "follow_up_date": "2026-04-25"},
    )
    assert r.status_code == 201

    older = conn.execute(
        "SELECT follow_up_done FROM activity_log WHERE id = ?", (older_id,)
    ).fetchone()
    assert older["follow_up_done"] == 1, "older open follow-up on same policy should have been superseded"


def test_task_complete_sets_follow_up_done(app_client):
    app_client.post("/tasks/create", data={"subject": "Temp task"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Temp task'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/complete")
    assert r.status_code == 204
    row = conn.execute("SELECT follow_up_done FROM activity_log WHERE id = ?", (tid,)).fetchone()
    assert row["follow_up_done"] == 1


def test_task_complete_emits_hx_trigger(app_client):
    app_client.post("/tasks/create", data={"subject": "Trigger task"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Trigger task'").fetchone()["id"]
    r = app_client.post(f"/tasks/{tid}/complete")
    assert "taskCompleted" in r.headers.get("HX-Trigger", "")


def test_task_undo_complete_reopens_task(app_client):
    app_client.post("/tasks/create", data={"subject": "Undo me"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Undo me'").fetchone()["id"]
    app_client.post(f"/tasks/{tid}/complete")

    r = app_client.post(f"/tasks/{tid}/undo-complete")
    assert r.status_code == 204
    row = conn.execute("SELECT follow_up_done FROM activity_log WHERE id = ?", (tid,)).fetchone()
    assert row["follow_up_done"] == 0


def test_snooze_tomorrow(app_client):
    app_client.post("/tasks/create", data={"subject": "Snooze me"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Snooze me'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/snooze", data={"option": "tomorrow"})
    assert r.status_code == 200
    row = conn.execute("SELECT follow_up_date FROM activity_log WHERE id = ?", (tid,)).fetchone()
    expected = (date.today() + timedelta(days=1)).isoformat()
    assert row["follow_up_date"] == expected


def test_snooze_this_week_moves_to_next_monday(app_client):
    app_client.post("/tasks/create", data={"subject": "Next Monday"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Next Monday'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/snooze", data={"option": "this_week"})
    assert r.status_code == 200
    row = conn.execute("SELECT follow_up_date FROM activity_log WHERE id = ?", (tid,)).fetchone()
    new = date.fromisoformat(row["follow_up_date"])
    today = date.today()
    # Must be a Monday (weekday 0), on or after today.
    assert new.weekday() == 0
    assert new >= today


def test_snooze_custom_date(app_client):
    app_client.post("/tasks/create", data={"subject": "Custom"})
    conn = get_connection()
    tid = conn.execute("SELECT id FROM activity_log WHERE subject = 'Custom'").fetchone()["id"]

    r = app_client.post(f"/tasks/{tid}/snooze", data={"option": "custom", "date": "2026-05-01"})
    assert r.status_code == 200
    row = conn.execute("SELECT follow_up_date FROM activity_log WHERE id = ?", (tid,)).fetchone()
    assert row["follow_up_date"] == "2026-05-01"
