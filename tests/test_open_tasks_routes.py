"""Route tests for the Open Tasks panel endpoints."""
from datetime import date

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


@pytest.fixture
def seeded(tmp_db):
    conn = get_connection()
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Route Co', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date) "
        "VALUES ('POL-R1', ?, 'GL', 'Test', '2026-01-01', '2027-01-01')",
        (cid,),
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Test issue', 'issue', 'ISS-R', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), cid, pid),
    )
    issue_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, issue_id, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'route task', '2026-04-15', 0, 'followup', ?, 'Grant')",
        (date.today().isoformat(), cid, pid, issue_id),
    )
    act_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return {"client_id": cid, "policy_id": pid, "issue_id": issue_id, "activity_id": act_id}


def test_panel_renders_for_issue_scope(app_client, seeded):
    r = app_client.get("/open-tasks/panel", params={"scope_type": "issue", "scope_id": seeded["issue_id"]})
    assert r.status_code == 200
    assert "route task" in r.text


def test_panel_rejects_invalid_scope_type(app_client, seeded):
    r = app_client.get("/open-tasks/panel", params={"scope_type": "bogus", "scope_id": 1})
    assert r.status_code == 400


def test_mark_done_closes_activity_and_syncs_policy(app_client, seeded):
    conn = get_connection()
    # Seed policies.follow_up_date to match the activity
    conn.execute(
        "UPDATE policies SET follow_up_date='2026-04-15' WHERE id=?",
        (seeded["policy_id"],),
    )
    conn.commit()

    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/done",
        data={"return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT follow_up_done, auto_close_reason FROM activity_log WHERE id=?",
        (seeded["activity_id"],),
    ).fetchone()
    assert row["follow_up_done"] == 1
    assert row["auto_close_reason"] == "manual"

    pol = conn.execute(
        "SELECT follow_up_date FROM policies WHERE id=?", (seeded["policy_id"],)
    ).fetchone()
    assert pol["follow_up_date"] is None  # synced after mark-done


def test_snooze_shifts_date_by_days(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/snooze",
        data={"days": 7, "return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT follow_up_date FROM activity_log WHERE id=?", (seeded["activity_id"],)
    ).fetchone()
    # Original date was 2026-04-15; +7 = 2026-04-22
    assert row["follow_up_date"] == "2026-04-22"


def test_disposition_toggles_to_waiting(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/disposition",
        data={"move": "waiting", "return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200
    conn = get_connection()
    row = conn.execute(
        "SELECT disposition FROM activity_log WHERE id=?", (seeded["activity_id"],)
    ).fetchone()
    # First waiting_external disposition label from config should be set
    assert row["disposition"]


def test_disposition_rejects_invalid_move(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/disposition",
        data={"move": "invalid", "return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 400


def test_log_close_clears_date_and_marks_done(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/log-close",
        data={"return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200
    conn = get_connection()
    row = conn.execute(
        "SELECT follow_up_done, follow_up_date FROM activity_log WHERE id=?",
        (seeded["activity_id"],),
    ).fetchone()
    assert row["follow_up_done"] == 1
    assert row["follow_up_date"] is None


def test_attach_sets_issue_id(app_client, seeded):
    # Create a second issue and a loose activity, then attach it
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, item_kind, issue_uid, issue_status, issue_severity, account_exec) "
        "VALUES (?, ?, ?, 'Note', 'Second issue', 'issue', 'ISS-B', 'Open', 'Normal', 'Grant')",
        (date.today().isoformat(), seeded["client_id"], seeded["policy_id"]),
    )
    iss_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, "
        "subject, follow_up_date, follow_up_done, item_kind, account_exec) "
        "VALUES (?, ?, ?, 'Call', 'loose-one', '2026-05-01', 0, 'followup', 'Grant')",
        (date.today().isoformat(), seeded["client_id"], seeded["policy_id"]),
    )
    loose_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    r = app_client.post(
        f"/open-tasks/{loose_id}/attach",
        data={"target_issue_id": iss_b, "return_scope_type": "issue", "return_scope_id": iss_b},
    )
    assert r.status_code == 200

    conn = get_connection()
    row = conn.execute(
        "SELECT issue_id FROM activity_log WHERE id=?", (loose_id,)
    ).fetchone()
    assert row["issue_id"] == iss_b


def test_attach_returns_404_for_missing_activity(app_client, seeded):
    r = app_client.post(
        "/open-tasks/999999/attach",
        data={"target_issue_id": seeded["issue_id"], "return_scope_type": "issue", "return_scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 404


def test_note_creates_sibling_activity(app_client, seeded):
    r = app_client.post(
        f"/open-tasks/{seeded['activity_id']}/note",
        data={
            "text": "Quick FYI",
            "return_scope_type": "issue",
            "return_scope_id": seeded["issue_id"],
        },
    )
    assert r.status_code == 200

    conn = get_connection()
    # Original task should still be open
    orig = conn.execute(
        "SELECT follow_up_done FROM activity_log WHERE id=?",
        (seeded["activity_id"],),
    ).fetchone()
    assert orig["follow_up_done"] == 0

    # A new sibling note activity should exist
    note = conn.execute(
        """SELECT id, subject, activity_type, follow_up_done, follow_up_date, issue_id
           FROM activity_log
           WHERE subject = 'Quick FYI' AND activity_type = 'Note'"""
    ).fetchone()
    assert note is not None
    assert note["follow_up_done"] == 1
    assert note["follow_up_date"] is None
    assert note["issue_id"] == seeded["issue_id"]


def test_new_task_create_issue_scope(app_client, seeded):
    r = app_client.post(
        "/open-tasks/new",
        data={
            "scope_type": "issue",
            "scope_id": seeded["issue_id"],
            "subject": "Net new task",
            "policy_id": seeded["policy_id"],
            "follow_up_date": "2026-05-30",
            "disposition": "",
        },
    )
    assert r.status_code == 200

    conn = get_connection()
    new = conn.execute(
        "SELECT id, issue_id, subject FROM activity_log WHERE subject = 'Net new task'"
    ).fetchone()
    assert new is not None
    assert new["issue_id"] == seeded["issue_id"]


def test_new_task_form_get_renders(app_client, seeded):
    r = app_client.get(
        "/open-tasks/new",
        params={"scope_type": "issue", "scope_id": seeded["issue_id"]},
    )
    assert r.status_code == 200
    assert "hx-post=\"/open-tasks/new\"" in r.text
    assert "data-disposition=\"waiting\"" in r.text
    assert "name=\"disposition\"" in r.text
