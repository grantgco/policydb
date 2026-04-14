"""Regression tests for programs tower construction delete buttons.

Bug: clicking the red X on an Excess (or Underlying) row in the Schematic tab
hard-deletes the underlying `policies` row. That's fine for blank placeholder
rows created via `+ Add Excess` / `+ Add Underlying`, but destroys real
pre-existing policies that were linked to the program via the `/assign` route
(the layer picker on the Unassigned Policies panel). A user who drops an
existing Excess policy onto a program via "+ Assign as Excess" and then later
clicks the X on the schematic row expects the policy to be removed *from the
program*, not wiped from the entire system.

These tests drive the HTTP routes (the same path the browser takes) and assert
that:

1. Deleting a placeholder row (limit=0, no policy_number, no carrier) should
   still hard-delete that row — backward compatibility.
2. Deleting a real row (has policy_number OR carrier OR non-zero limit) should
   **unassign** the policy from the program (clear `program_id` / `tower_group`
   and `layer_position`) and leave the policy intact in the `policies` table.

Both tests exist for underlying and excess delete routes.

Driver bug report:
  "I created a program with two policies (GL and Excess). I deleted the Excess
   policy from the child policies table in the tower construction tab. However,
   it is now not showing up anywhere in the system."
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
    # A real General Liability policy assigned to the program as Primary.
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id, tower_group,
                layer_position, policy_type, carrier, policy_number, premium,
                limit_amount, deductible, effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (10, 'POL-010', 1, 1, 'Casualty', 'Primary',
                   'General Liability', 'Zurich', 'GL-12345', 50000,
                   1000000, 5000, '2026-01-01', '2027-01-01',
                   'Bound', 0, 0)"""
    )
    # A real Excess Liability policy assigned to the program as Excess —
    # the policy the user is reporting on.
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id, tower_group,
                layer_position, policy_type, carrier, policy_number, premium,
                limit_amount, attachment_point, effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (11, 'POL-011', 1, 1, 'Casualty', 'Excess',
                   'Excess Liability', 'Berkshire', 'XS-98765', 25000,
                   10000000, 1000000, '2026-01-01', '2027-01-01',
                   'Bound', 0, 0)"""
    )
    # A blank placeholder Excess row created via "+ Add Excess" in the UI —
    # no carrier, no policy number, zero limit. Safe to hard-delete.
    conn.execute(
        """INSERT INTO policies (id, policy_uid, client_id, program_id, tower_group,
                layer_position, policy_type, carrier, policy_number, premium,
                limit_amount, attachment_point, effective_date, expiration_date,
                renewal_status, is_opportunity, archived)
           VALUES (12, 'POL-012', 1, 1, 'Casualty', 'Excess',
                   'Excess Liability', '', '', 0,
                   0, 0, NULL, NULL,
                   'Not Started', 0, 0)"""
    )
    conn.commit()
    conn.close()

    from policydb.web.app import app
    tc = TestClient(app, raise_server_exceptions=False)
    yield tc


def _policy_row(db_path, policy_id):
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT id, policy_uid, program_id, tower_group, layer_position, "
        "carrier, policy_number, limit_amount, archived "
        "FROM policies WHERE id = ?",
        (policy_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Excess ───────────────────────────────────────────────────────────────


def test_delete_excess_preserves_real_policy(client, tmp_path):
    """Deleting a real Excess policy from the schematic must unassign, not
    hard-delete. This is the exact bug the user reported."""
    db_path = tmp_path / "test.sqlite"

    # Sanity: the real excess row starts linked to the program.
    before = _policy_row(db_path, 11)
    assert before is not None
    assert before["program_id"] == 1
    assert before["layer_position"] == "Excess"
    assert before["carrier"] == "Berkshire"

    resp = client.delete("/programs/PGM-001/excess/11")
    assert resp.status_code == 200

    after = _policy_row(db_path, 11)
    # The policy row must still exist — we only wanted to remove it from the program.
    assert after is not None, (
        "Real Excess policy was hard-deleted from the database when the user "
        "clicked the X in the schematic matrix. The delete endpoint must "
        "unassign policies that have real data (carrier, policy_number, or "
        "non-zero limit), not DROP them."
    )
    # And it should no longer be attached to the program.
    assert after["program_id"] is None
    assert after["tower_group"] in (None, "")
    assert after["layer_position"] in (None, "Primary", "")
    # Business data is preserved.
    assert after["carrier"] == "Berkshire"
    assert after["policy_number"] == "XS-98765"
    assert after["limit_amount"] == 10000000
    assert after["archived"] == 0


def test_delete_excess_placeholder_still_hard_deletes(client, tmp_path):
    """A blank placeholder row created by '+ Add Excess' (no carrier, no
    policy_number, zero limit) should still be hard-deleted so the old
    add-then-cancel workflow keeps working."""
    db_path = tmp_path / "test.sqlite"

    before = _policy_row(db_path, 12)
    assert before is not None
    assert before["carrier"] in (None, "")
    assert before["policy_number"] in (None, "")
    assert before["limit_amount"] in (None, 0)

    resp = client.delete("/programs/PGM-001/excess/12")
    assert resp.status_code == 200

    after = _policy_row(db_path, 12)
    assert after is None, (
        "Placeholder rows (no carrier/number/limit) should still be hard-"
        "deleted so '+ Add Excess' followed by X remains a no-op."
    )


# ── Underlying ───────────────────────────────────────────────────────────


def test_delete_underlying_preserves_real_policy(client, tmp_path):
    """Same bug lives in delete_underlying_v2. Deleting the GL row from the
    underlying matrix must unassign it, not wipe it."""
    db_path = tmp_path / "test.sqlite"

    before = _policy_row(db_path, 10)
    assert before is not None
    assert before["program_id"] == 1
    assert before["carrier"] == "Zurich"

    resp = client.delete("/programs/PGM-001/underlying/10")
    assert resp.status_code == 200

    after = _policy_row(db_path, 10)
    assert after is not None, (
        "Real underlying policy was hard-deleted from the database when the "
        "user clicked the X in the schematic underlying matrix. The delete "
        "endpoint must unassign policies with real data, not DROP them."
    )
    assert after["program_id"] is None
    assert after["tower_group"] in (None, "")
    assert after["carrier"] == "Zurich"
    assert after["policy_number"] == "GL-12345"
    assert after["limit_amount"] == 1000000
    assert after["archived"] == 0
