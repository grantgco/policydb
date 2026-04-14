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
