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
