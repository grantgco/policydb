"""Regression tests for clearing currency fields via the program cell PATCH
routes (patch_underlying_cell_v2 / patch_excess_cell_v2 / patch_child_cell).

Bug: sending an empty value for a currency field like attachment_point
used to make the handler return `{"formatted": "$0"}`. The `initMatrix`
focusout callback then wrote "$0" into the cell, and on the next focus
the cell's `data-original` cached "$0", so subsequent clears round-
tripped back to "$0" forever. Users reported they "couldn't clear
attachment_point to make a policy primary."

Fix: `_parse_and_format_currency` now returns an empty display string
for empty/whitespace input AND for parsed zero. The DB still gets 0 so
template `{% if value %}` guards hide the cell on reload. Paired with a
base.html tweak that accepts `formatted == ""` as a legitimate "blank
this cell out" signal.
"""

import pytest
from starlette.testclient import TestClient

from policydb.db import init_db, get_connection


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
        "INSERT INTO clients (id, name, industry_segment) "
        "VALUES (1, 'Acme Construction', 'Construction')"
    )
    conn.execute(
        "INSERT INTO programs (id, program_uid, client_id, name, line_of_business) "
        "VALUES (1, 'PGM-001', 1, 'Casualty', 'Casualty')"
    )
    # Excess policy with $5M attachment — this is the one we'll try to clear.
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id, tower_group,
                layer_position, policy_type, carrier, policy_number, premium,
                limit_amount, attachment_point, effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (20, 'POL-020', 1, 1, 'Casualty', 'Excess',
                   'Excess Liability', 'Berkshire', 'XS-20', 25000,
                   10000000, 5000000, '2026-01-01', '2027-01-01',
                   'Bound', 0, 0)"""
    )
    conn.commit()
    conn.close()

    from policydb.web.app import app
    tc = TestClient(app, raise_server_exceptions=False)
    yield tc


def _attachment(db_path, policy_id):
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT attachment_point, layer_position FROM policies WHERE id = ?",
        (policy_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── child cell PATCH (Overview tab grid) ─────────────────────────────────


def test_child_cell_clear_attachment_point_empty_string(client, tmp_path):
    db_path = tmp_path / "test.sqlite"
    assert _attachment(db_path, 20)["attachment_point"] == 5000000

    resp = client.patch(
        "/programs/PGM-001/child/20/cell",
        json={"field": "attachment_point", "value": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # Display must be empty — the row template hides zero via {% if %} and
    # the JS must not write "$0" into the cell on the user's screen.
    assert body["formatted"] == "", (
        f"Clearing attachment_point returned {body['formatted']!r} instead "
        "of an empty string. A non-empty 'formatted' makes the cell sticky: "
        "the user's blank input gets replaced with '$0' and the next clear "
        "round-trips back to '$0' because data-original is now '$0'."
    )
    # DB is zeroed.
    assert _attachment(db_path, 20)["attachment_point"] == 0


def test_child_cell_clear_attachment_point_explicit_zero(client, tmp_path):
    """Typing '0' or '$0' should be treated exactly like clearing."""
    db_path = tmp_path / "test.sqlite"

    for raw in ("0", "$0", "$0.00", " "):
        resp = client.patch(
            "/programs/PGM-001/child/20/cell",
            json={"field": "attachment_point", "value": raw},
        )
        assert resp.status_code == 200, f"PATCH failed for {raw!r}"
        body = resp.json()
        assert body["ok"] is True
        assert body["formatted"] == "", (
            f"Input {raw!r} returned {body['formatted']!r} instead of ''."
        )
        assert _attachment(db_path, 20)["attachment_point"] == 0


def test_child_cell_set_attachment_point_still_formats(client, tmp_path):
    """Non-zero values should still come back pretty-formatted."""
    db_path = tmp_path / "test.sqlite"
    resp = client.patch(
        "/programs/PGM-001/child/20/cell",
        json={"field": "attachment_point", "value": "5M"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["formatted"] == "$5,000,000"
    assert _attachment(db_path, 20)["attachment_point"] == 5000000


# ── excess matrix PATCH (Schematic tab) ──────────────────────────────────


def test_excess_cell_clear_attachment_point(client, tmp_path):
    db_path = tmp_path / "test.sqlite"
    resp = client.patch(
        "/programs/PGM-001/excess/20/cell",
        json={"field": "attachment_point", "value": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["formatted"] == ""
    # The excess route also returns the recomputed layer notation.
    assert "notation" in body
    assert _attachment(db_path, 20)["attachment_point"] == 0


# ── underlying matrix PATCH (symmetry check) ─────────────────────────────


def test_underlying_cell_clear_currency_field(client, tmp_path):
    """Same fix applies to premium/deductible on the underlying matrix."""
    db_path = tmp_path / "test.sqlite"
    conn = get_connection(db_path)
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id, tower_group,
                layer_position, policy_type, carrier, policy_number, premium,
                limit_amount, deductible, effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (21, 'POL-021', 1, 1, 'Casualty', 'Primary',
                   'General Liability', 'Zurich', 'GL-21', 50000,
                   1000000, 5000, '2026-01-01', '2027-01-01',
                   'Bound', 0, 0)"""
    )
    conn.commit()
    conn.close()

    resp = client.patch(
        "/programs/PGM-001/underlying/21/cell",
        json={"field": "deductible", "value": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["formatted"] == ""
    conn = get_connection(db_path)
    val = conn.execute("SELECT deductible FROM policies WHERE id = 21").fetchone()[0]
    conn.close()
    assert val == 0
