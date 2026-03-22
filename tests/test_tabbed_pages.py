"""Tests for tabbed page layout: per-field PATCH, tab routes, and page shells."""

import sqlite3
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from policydb.db import init_db, get_connection


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Create a TestClient with a fresh temporary database."""
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    # Seed a client + policy for testing
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')"
    )
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, policy_type, carrier,
                   effective_date, expiration_date, premium, limit_amount, deductible,
                   renewal_status, is_opportunity, archived)
           VALUES (1, 'POL-001', 1, 'General Liability', 'Test Carrier',
                   '2025-04-01', '2026-04-01', 50000, 1000000, 5000,
                   'Not Started', 0, 0)"""
    )
    conn.commit()
    conn.close()

    from policydb.web.app import app
    client = TestClient(app, raise_server_exceptions=False)
    yield client


# ──────────────────────────────────────────────────────────
# PATCH /policies/{uid}/cell — per-field save
# ──────────────────────────────────────────────────────────


class TestPolicyCellPatch:
    """Test per-field PATCH endpoint for all field types."""

    def _patch(self, client, field, value):
        return client.patch(
            "/policies/POL-001/cell",
            json={"field": field, "value": value},
        )

    def test_currency_field_premium(self, app_client):
        r = self._patch(app_client, "premium", "1.5m")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["formatted"] == "$1,500,000"

    def test_currency_field_limit(self, app_client):
        r = self._patch(app_client, "limit_amount", "500k")
        assert r.status_code == 200
        assert r.json()["formatted"] == "$500,000"

    def test_currency_field_deductible(self, app_client):
        r = self._patch(app_client, "deductible", "$10,000")
        assert r.status_code == 200
        assert r.json()["formatted"] == "$10,000"

    def test_currency_field_attachment_point(self, app_client):
        r = self._patch(app_client, "attachment_point", "2m")
        assert r.status_code == 200
        assert r.json()["formatted"] == "$2,000,000"

    def test_currency_field_prior_premium(self, app_client):
        r = self._patch(app_client, "prior_premium", "45000")
        assert r.status_code == 200
        assert r.json()["formatted"] == "$45,000"

    def test_date_field_follow_up(self, app_client):
        r = self._patch(app_client, "follow_up_date", "2026-05-01")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["formatted"] == "2026-05-01"

    def test_date_field_effective(self, app_client):
        r = self._patch(app_client, "effective_date", "2025-07-01")
        assert r.status_code == 200
        assert r.json()["formatted"] == "2025-07-01"

    def test_date_field_clear(self, app_client):
        r = self._patch(app_client, "follow_up_date", "")
        assert r.status_code == 200
        assert r.json()["formatted"] == ""

    def test_bool_field_is_bor(self, app_client):
        r = self._patch(app_client, "is_bor", "true")
        assert r.status_code == 200
        assert r.json()["formatted"] == "1"

    def test_bool_field_is_standalone(self, app_client):
        r = self._patch(app_client, "is_standalone", "0")
        assert r.status_code == 200
        assert r.json()["formatted"] == "0"

    def test_text_field_policy_number(self, app_client):
        r = self._patch(app_client, "policy_number", " abc-123 ")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # normalize_policy_number uppercases
        assert r.json()["formatted"] == "ABC-123"

    def test_text_field_description(self, app_client):
        r = self._patch(app_client, "description", "Test description text")
        assert r.status_code == 200
        assert r.json()["formatted"] == "Test description text"

    def test_text_field_notes(self, app_client):
        r = self._patch(app_client, "notes", "Internal note here")
        assert r.status_code == 200
        assert r.json()["formatted"] == "Internal note here"

    def test_text_field_exposure_address(self, app_client):
        r = self._patch(app_client, "exposure_address", "123 Main St")
        assert r.status_code == 200
        assert r.json()["formatted"] == "123 Main St"

    def test_combobox_renewal_status(self, app_client):
        r = self._patch(app_client, "renewal_status", "In Progress")
        assert r.status_code == 200
        assert r.json()["formatted"] == "In Progress"

    def test_combobox_coverage_form(self, app_client):
        r = self._patch(app_client, "coverage_form", "Occurrence")
        assert r.status_code == 200
        assert r.json()["formatted"] == "Occurrence"

    def test_combobox_layer_position(self, app_client):
        r = self._patch(app_client, "layer_position", "2")
        assert r.status_code == 200
        assert r.json()["formatted"] == "2"

    def test_carrier_normalization(self, app_client):
        r = self._patch(app_client, "carrier", "travelers")
        assert r.status_code == 200
        # normalize_carrier should capitalize
        assert r.json()["ok"] is True

    def test_policy_type_normalization(self, app_client):
        r = self._patch(app_client, "policy_type", "GL")
        assert r.status_code == 200
        # normalize_coverage_type maps GL → General Liability
        assert r.json()["formatted"] == "General Liability"

    def test_commission_rate(self, app_client):
        r = self._patch(app_client, "commission_rate", "0.12")
        assert r.status_code == 200
        assert r.json()["formatted"] == "0.120"

    def test_project_name(self, app_client):
        r = self._patch(app_client, "project_name", "Main St Condos")
        assert r.status_code == 200
        assert r.json()["formatted"] == "Main St Condos"

    def test_invalid_field_rejected(self, app_client):
        r = self._patch(app_client, "nonexistent_field", "value")
        assert r.status_code == 400
        assert r.json()["ok"] is False

    def test_not_found_policy(self, app_client):
        r = app_client.patch(
            "/policies/FAKE-999/cell",
            json={"field": "premium", "value": "1000"},
        )
        assert r.status_code == 404

    def test_db_persistence(self, app_client):
        """Verify PATCH actually persists to the database."""
        self._patch(app_client, "premium", "75000")
        self._patch(app_client, "renewal_status", "Bound")
        self._patch(app_client, "description", "Persisted note")

        # Read directly from the DB
        from policydb.db import DB_PATH
        conn = get_connection(DB_PATH)
        row = conn.execute("SELECT premium, renewal_status, description FROM policies WHERE policy_uid='POL-001'").fetchone()
        conn.close()
        assert row["premium"] == 75000.0
        assert row["renewal_status"] == "Bound"
        assert row["description"] == "Persisted note"


# ──────────────────────────────────────────────────────────
# Policy tab routes — each returns 200
# ──────────────────────────────────────────────────────────


class TestPolicyTabRoutes:
    """Test that each policy tab route returns 200 with content."""

    def test_details_tab(self, app_client):
        r = app_client.get("/policies/POL-001/tab/details")
        assert r.status_code == 200
        assert "Placement" in r.text or "Core Fields" in r.text

    def test_activity_tab(self, app_client):
        r = app_client.get("/policies/POL-001/tab/activity")
        assert r.status_code == 200
        assert "Activity Log" in r.text or "activity" in r.text.lower()

    def test_contacts_tab(self, app_client):
        r = app_client.get("/policies/POL-001/tab/contacts")
        assert r.status_code == 200

    def test_workflow_tab(self, app_client):
        r = app_client.get("/policies/POL-001/tab/workflow")
        assert r.status_code == 200
        assert "Checklist" in r.text or "checklist" in r.text.lower()

    def test_edit_shell(self, app_client):
        r = app_client.get("/policies/POL-001/edit")
        assert r.status_code == 200
        assert "tab-bar" in r.text
        assert "Details" in r.text
        assert "Activity" in r.text
        assert "Contacts" in r.text
        assert "Workflow" in r.text

    def test_nonexistent_policy_tab(self, app_client):
        r = app_client.get("/policies/FAKE-999/tab/details")
        assert r.status_code == 404


# ──────────────────────────────────────────────────────────
# Client tab routes — each returns 200
# ──────────────────────────────────────────────────────────


class TestClientTabRoutes:
    """Test that each client tab route returns 200."""

    def test_overview_tab(self, app_client):
        r = app_client.get("/clients/1/tab/overview")
        assert r.status_code == 200
        assert "Account Pulse" in r.text or "Activity" in r.text

    def test_policies_tab(self, app_client):
        r = app_client.get("/clients/1/tab/policies")
        assert r.status_code == 200
        assert "Policies" in r.text

    def test_contacts_tab(self, app_client):
        r = app_client.get("/clients/1/tab/contacts")
        assert r.status_code == 200

    def test_risk_tab(self, app_client):
        r = app_client.get("/clients/1/tab/risk")
        assert r.status_code == 200

    def test_detail_shell(self, app_client):
        r = app_client.get("/clients/1")
        assert r.status_code == 200
        assert "tab-bar" in r.text
        assert "Overview" in r.text
        assert "Policies" in r.text
        assert "Contacts" in r.text

    def test_nonexistent_client_tab(self, app_client):
        r = app_client.get("/clients/999/tab/overview")
        assert r.status_code == 404


# ──────────────────────────────────────────────────────────
# Regression: key pages still load
# ──────────────────────────────────────────────────────────


class TestRegression:
    """Verify critical pages still render after tabbed layout changes."""

    def test_dashboard(self, app_client):
        r = app_client.get("/")
        assert r.status_code == 200

    def test_client_list(self, app_client):
        r = app_client.get("/clients")
        assert r.status_code == 200

    def test_renewals(self, app_client):
        r = app_client.get("/renewals")
        assert r.status_code == 200

    def test_followups(self, app_client):
        r = app_client.get("/followups")
        assert r.status_code == 200
