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
