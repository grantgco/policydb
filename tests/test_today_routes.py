"""Route tests for the Today tab and task CRUD endpoints."""
from __future__ import annotations

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
