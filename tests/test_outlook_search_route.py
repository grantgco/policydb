"""Tests for POST /outlook/search route."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from policydb.db import get_connection, init_db
from policydb.web.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (name, cn_number, industry_segment, account_exec) "
        "VALUES ('Acme', '122333627', 'Tech', 'Grant')"
    )
    cid = conn.execute("SELECT id FROM clients WHERE name='Acme'").fetchone()["id"]
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, premium, account_exec) "
        "VALUES ('POL-042', ?, 'GL', 'Zurich', ?, ?, 10000, 'Grant')",
        (cid, today, today),
    )
    conn.commit()
    conn.close()

    return TestClient(app)


def test_search_policy_returns_query_and_tokens(client):
    with patch(
        "policydb.outlook.trigger_search",
        return_value={
            "status": "searched",
            "query": "",
            "message": "Searched Outlook.",
        },
    ):
        r = client.post(
            "/outlook/search",
            json={"entity_type": "policy", "entity_id": "POL-042", "mode": "wide"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "searched"
    assert "POL-042" in body["tokens"]
    assert "POL042" in body["tokens"]
    # Wide mode from a policy no longer includes the client CN — it would
    # OR-match every message about the client and drown policy results.
    # Use mode="client" to sweep all client correspondence.
    assert "CN122333627" not in body["tokens"]
    assert body["truncated"] is False
    assert body["query"].startswith('"')


def test_search_policy_in_client_mode_returns_cn_only(client):
    """Shift-click path: mode='client' from a policy returns only the CN token."""
    with patch(
        "policydb.outlook.trigger_search",
        return_value={
            "status": "searched",
            "query": "",
            "message": "Searched Outlook.",
        },
    ):
        r = client.post(
            "/outlook/search",
            json={"entity_type": "policy", "entity_id": "POL-042", "mode": "client"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["tokens"] == ["CN122333627"]


def test_search_missing_entity_returns_404(client):
    r = client.post(
        "/outlook/search",
        json={"entity_type": "policy", "entity_id": "POL-9999"},
    )
    assert r.status_code == 404


def test_search_invalid_entity_type_returns_422(client):
    r = client.post(
        "/outlook/search",
        json={"entity_type": "foobar", "entity_id": "x"},
    )
    assert r.status_code == 422  # Pydantic Literal rejects


def test_search_respects_auto_paste_config(client, monkeypatch):
    """When outlook_search_auto_paste=False, trigger_search gets auto_paste=False."""
    captured: list[bool] = []

    def fake_trigger(query, auto_paste=True):
        captured.append(auto_paste)
        return {"status": "clipboard_only", "query": query, "message": "..."}

    # Patch where the route looks it up (outlook module, imported by the route).
    monkeypatch.setattr("policydb.outlook.trigger_search", fake_trigger)

    # Make load_config().get("outlook_search_auto_paste") return False
    from policydb import config as cfg_mod

    class _FakeCfg:
        def get(self, key, default=None):
            if key == "outlook_search_auto_paste":
                return False
            return default

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _FakeCfg())

    client.post(
        "/outlook/search",
        json={"entity_type": "policy", "entity_id": "POL-042"},
    )

    assert captured == [False]
