"""Regression tests for the + Add Location button on the client Overview tab.

window.addLocation used to live inside `{% if locations %}`. When a client
had a construction pipeline but zero location projects, the empty-state
button rendered but its onclick handler was undefined. These tests lock in:

  1. The JS handler is emitted on the page in every pipeline/location
     combination.
  2. POST /clients/{id}/projects/location creates exactly one Location
     project without disturbing existing Construction pipeline projects.
"""

import pytest
from starlette.testclient import TestClient

from policydb.db import get_connection, init_db


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)

    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Builder Co', 'Construction')"
    )
    conn.commit()
    conn.close()

    from policydb.web.app import app
    yield TestClient(app, raise_server_exceptions=False), db_path


def _seed_pipeline(db_path, client_id: int, n: int) -> None:
    conn = get_connection(db_path)
    for i in range(n):
        conn.execute(
            "INSERT INTO projects (client_id, name, project_type, status) "
            "VALUES (?, ?, 'Construction', 'Upcoming')",
            (client_id, f"Tower {i + 1}"),
        )
    conn.commit()
    conn.close()


def _seed_locations(db_path, client_id: int, n: int) -> None:
    conn = get_connection(db_path)
    for i in range(n):
        conn.execute(
            "INSERT INTO projects (client_id, name, project_type) "
            "VALUES (?, ?, 'Location')",
            (client_id, f"Site {i + 1}"),
        )
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    "pipeline_count, location_count, label",
    [
        (0, 0, "empty client"),
        (2, 0, "pipeline-only (reported bug scenario)"),
        (0, 2, "locations-only"),
        (2, 2, "both populated"),
    ],
)
def test_add_location_handler_is_defined(
    app_client, pipeline_count, location_count, label
):
    """window.addLocation must be emitted on every render so the button works."""
    client, db_path = app_client
    _seed_pipeline(db_path, 1, pipeline_count)
    _seed_locations(db_path, 1, location_count)

    resp = client.get("/clients/1/tab/overview")
    assert resp.status_code == 200, f"{label}: overview tab failed to load"
    html = resp.text

    assert "addLocation(1)" in html, (
        f"{label}: + Add Location button missing from overview"
    )
    assert "window.addLocation" in html, (
        f"{label}: window.addLocation handler not emitted — "
        f"button will throw ReferenceError when clicked"
    )
    assert "/projects/location" in html, (
        f"{label}: addLocation POST endpoint not referenced in page JS"
    )


def test_add_location_with_construction_pipeline_creates_location_only(app_client):
    """Reported bug: client has a construction pipeline, zero locations,
    user clicks + Add Location. A single Location project should be created
    and existing Construction projects must be untouched."""
    client, db_path = app_client
    _seed_pipeline(db_path, 1, 3)

    resp = client.post("/clients/1/projects/location")
    assert resp.status_code == 200
    assert resp.json().get("ok") is True

    conn = get_connection(db_path)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT name, project_type FROM projects WHERE client_id = 1 "
            "ORDER BY id"
        ).fetchall()
    ]
    conn.close()

    construction = [r for r in rows if r["project_type"] == "Construction"]
    locations = [r for r in rows if r["project_type"] == "Location"]
    assert len(construction) == 3, "Construction pipeline must be preserved"
    assert len(locations) == 1, "Exactly one new Location project should be created"
    assert locations[0]["name"].startswith("New Location")


def test_add_location_autoincrements_name_on_repeat_click(app_client):
    """Clicking the button three times must generate three unique names."""
    client, db_path = app_client
    _seed_pipeline(db_path, 1, 1)

    for _ in range(3):
        r = client.post("/clients/1/projects/location")
        assert r.status_code == 200 and r.json()["ok"] is True

    conn = get_connection(db_path)
    names = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM projects WHERE client_id=1 AND project_type='Location' "
            "ORDER BY id"
        ).fetchall()
    ]
    conn.close()
    assert names == ["New Location", "New Location 2", "New Location 3"]


def test_add_location_endpoint_is_scoped_to_client(app_client):
    """Adding a location for client 1 must not leak into client 2."""
    client, db_path = app_client
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (2, 'Other Co', 'Retail')"
    )
    conn.commit()
    conn.close()

    r = client.post("/clients/1/projects/location")
    assert r.status_code == 200

    conn = get_connection(db_path)
    other_count = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE client_id=2"
    ).fetchone()[0]
    conn.close()
    assert other_count == 0
