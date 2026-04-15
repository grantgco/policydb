"""Regression tests for Bug 3 — exposure rollup from AI import + importer.

Covers:
1. `find_or_create_project_from_address()` upsert + idempotency semantics
2. The AI import exposure chain: multi-entry, primary flag, address→project,
   policy.project_id stamping on first-assign only
3. Importer CSV row exposure post-processing uses the same chain
"""
from datetime import date

import pytest

import policydb.web.app  # noqa: F401 — boot FastAPI app

from policydb.db import get_connection, init_db
from policydb.exposures import (
    create_exposure_link,
    find_or_create_exposure,
    get_policy_exposures,
)
from policydb.queries import find_or_create_project_from_address


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def _seed_client(conn, name="Acme Corp"):
    conn.execute(
        "INSERT INTO clients (name, industry_segment) VALUES (?, 'Construction')",
        (name,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_policy(conn, client_id, uid="POL-9001", project_id=None):
    conn.execute(
        """INSERT INTO policies
             (policy_uid, client_id, policy_type, carrier, premium,
              effective_date, expiration_date, project_id)
           VALUES (?, ?, 'GL', 'Travelers', 50000, '2026-01-01', '2027-01-01', ?)""",
        (uid, client_id, project_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── find_or_create_project_from_address ────────────────────────────────────


def test_project_helper_returns_none_without_usable_info(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    assert find_or_create_project_from_address(conn, client_id=cid) is None
    assert find_or_create_project_from_address(
        conn, client_id=cid, address="  "
    ) is None


def test_project_helper_creates_new_project(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    pid = find_or_create_project_from_address(
        conn,
        client_id=cid,
        address="123 Main St",
        city="Austin",
        state="TX",
        zip_code="78701",
    )
    assert pid is not None

    row = conn.execute(
        "SELECT name, address, city, state, zip, status FROM projects WHERE id=?",
        (pid,),
    ).fetchone()
    assert row["address"] == "123 Main St"
    assert row["city"] == "Austin"
    assert row["state"] == "TX"
    assert row["zip"] == "78701"
    assert row["status"] == "Active"
    # Default name falls back to first line of address
    assert row["name"].startswith("123 Main St")


def test_project_helper_uses_label_as_name_when_provided(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    pid = find_or_create_project_from_address(
        conn,
        client_id=cid,
        address="500 Pine Ave",
        label="Downtown Tower",
    )
    name = conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()["name"]
    assert name == "Downtown Tower"


def test_project_helper_reuses_existing_address(tmp_db):
    """Same client + same normalized address → returns existing id."""
    conn = get_connection()
    cid = _seed_client(conn)
    first = find_or_create_project_from_address(
        conn, client_id=cid, address="100 Market St", city="San Francisco",
    )
    # Slightly different casing / whitespace should still match
    second = find_or_create_project_from_address(
        conn, client_id=cid, address="  100 MARKET ST  ", city="San Francisco",
    )
    assert first == second

    count = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE client_id=?", (cid,)
    ).fetchone()[0]
    assert count == 1


def test_project_helper_does_not_cross_clients(tmp_db):
    conn = get_connection()
    c1 = _seed_client(conn, "Client A")
    c2 = _seed_client(conn, "Client B")
    p1 = find_or_create_project_from_address(conn, client_id=c1, address="1 Shared St")
    p2 = find_or_create_project_from_address(conn, client_id=c2, address="1 Shared St")
    assert p1 != p2  # same address, different clients, two separate projects


def test_project_helper_name_match_when_no_address(tmp_db):
    """When the caller only provides a label (no address), reuse by name."""
    conn = get_connection()
    cid = _seed_client(conn)
    first = find_or_create_project_from_address(conn, client_id=cid, label="Main Office")
    second = find_or_create_project_from_address(conn, client_id=cid, label="main office")
    assert first == second


# ── AI import exposure chain ───────────────────────────────────────────────


def _run_ai_exposure_ingest(conn, policy_uid, client_id, exposures, eff_date):
    """Mirror the exposure chain from the /ai-import/apply-exposures route.

    This tests the project upsert → client_exposures upsert → link chain
    without wiring an HTTP Request. Keep in lock-step with
    ``src/policydb/web/routes/policies.py::policy_ai_import_apply_exposures``.
    """
    year = int(eff_date[:4])

    policy_project_id = conn.execute(
        "SELECT project_id FROM policies WHERE policy_uid=?", (policy_uid,)
    ).fetchone()["project_id"]

    # Normalize + filter up front so the primary-flag decision is made
    # over valid entries only.
    valid_exposures = []
    for exp in exposures:
        if not isinstance(exp, dict):
            continue
        exposure_type = (exp.get("exposure_type") or "").strip()
        try:
            amount = float(exp.get("amount") or 0) or None
        except (ValueError, TypeError):
            amount = None
        if not exposure_type or not amount:
            continue
        try:
            denominator = int(exp.get("denominator") or 1) or 1
        except (ValueError, TypeError):
            denominator = 1
        valid_exposures.append({
            **exp,
            "exposure_type": exposure_type,
            "amount": amount,
            "denominator": denominator,
        })

    primary_idx = next(
        (i for i, e in enumerate(valid_exposures) if e.get("is_primary")),
        0 if valid_exposures else -1,
    )

    for idx, exp in enumerate(valid_exposures):
        exp_project_id = find_or_create_project_from_address(
            conn,
            client_id=client_id,
            address=exp.get("address"),
            city=exp.get("city"),
            state=exp.get("state"),
            zip_code=exp.get("zip"),
            label=exp.get("location_label"),
        )

        if exp_project_id and not policy_project_id:
            conn.execute(
                "UPDATE policies SET project_id = ? WHERE policy_uid = ?",
                (exp_project_id, policy_uid),
            )
            policy_project_id = exp_project_id

        exp_id = find_or_create_exposure(
            conn,
            client_id=client_id,
            project_id=exp_project_id,
            exposure_type=exp["exposure_type"],
            year=year,
            amount=exp["amount"],
            denominator=exp["denominator"],
        )
        create_exposure_link(
            conn, policy_uid, exp_id, is_primary=(idx == primary_idx),
        )

    conn.commit()


def test_single_exposure_creates_link_not_policy_columns(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-5001")

    _run_ai_exposure_ingest(
        conn,
        policy_uid="POL-5001",
        client_id=cid,
        exposures=[
            {
                "exposure_type": "Payroll",
                "amount": 12500000,
                "denominator": 100,
                "unit": "Per $100 Payroll",
            }
        ],
        eff_date="2026-04-01",
    )

    links = get_policy_exposures(conn, "POL-5001")
    assert len(links) == 1
    link = links[0]
    assert link["exposure_type"] == "Payroll"
    assert link["amount"] == 12500000
    assert link["denominator"] == 100
    assert link["year"] == 2026
    assert link["is_primary"] == 1


def test_multi_exposure_first_is_primary_by_default(tmp_db):
    """A GL policy rated on both payroll and sales → two exposures, first primary."""
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-5002")

    _run_ai_exposure_ingest(
        conn,
        policy_uid="POL-5002",
        client_id=cid,
        exposures=[
            {"exposure_type": "Payroll", "amount": 5000000, "denominator": 100},
            {"exposure_type": "Gross Sales", "amount": 25000000, "denominator": 1000},
        ],
        eff_date="2026-04-01",
    )

    links = {l["exposure_type"]: l for l in get_policy_exposures(conn, "POL-5002")}
    assert set(links) == {"Payroll", "Gross Sales"}
    assert links["Payroll"]["is_primary"] == 1
    assert links["Gross Sales"]["is_primary"] == 0


def test_multi_exposure_honors_explicit_is_primary(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-5003")

    _run_ai_exposure_ingest(
        conn,
        policy_uid="POL-5003",
        client_id=cid,
        exposures=[
            {"exposure_type": "Payroll", "amount": 5000000, "denominator": 100},
            {
                "exposure_type": "Gross Sales",
                "amount": 25000000,
                "denominator": 1000,
                "is_primary": True,
            },
        ],
        eff_date="2026-04-01",
    )

    links = {l["exposure_type"]: l for l in get_policy_exposures(conn, "POL-5003")}
    assert links["Payroll"]["is_primary"] == 0
    assert links["Gross Sales"]["is_primary"] == 1


def test_exposure_with_address_upserts_project_and_stamps_policy(tmp_db):
    """Exposure address → new project + policies.project_id set when unassigned."""
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-5004", project_id=None)

    _run_ai_exposure_ingest(
        conn,
        policy_uid="POL-5004",
        client_id=cid,
        exposures=[
            {
                "exposure_type": "Building Value",
                "amount": 10000000,
                "denominator": 1,
                "address": "500 Commerce St",
                "city": "Dallas",
                "state": "TX",
                "zip": "75201",
            }
        ],
        eff_date="2026-07-01",
    )

    # Project was created
    project = conn.execute(
        "SELECT id, address, city FROM projects WHERE client_id=? AND address='500 Commerce St'",
        (cid,),
    ).fetchone()
    assert project is not None
    assert project["city"] == "Dallas"

    # Policy project_id was stamped
    pol = conn.execute(
        "SELECT project_id FROM policies WHERE policy_uid='POL-5004'"
    ).fetchone()
    assert pol["project_id"] == project["id"]

    # Exposure linked to that project
    links = get_policy_exposures(conn, "POL-5004")
    assert len(links) == 1
    assert links[0]["project_id"] == project["id"]


def test_existing_policy_project_id_never_overwritten(tmp_db):
    """If the policy already has project_id, AI import must not overwrite it."""
    conn = get_connection()
    cid = _seed_client(conn)
    # Pre-create a project and link the policy to it
    existing_pid = find_or_create_project_from_address(
        conn, client_id=cid, address="1 Original St",
    )
    _seed_policy(conn, cid, "POL-5005", project_id=existing_pid)

    # AI extracts an exposure with a different address
    _run_ai_exposure_ingest(
        conn,
        policy_uid="POL-5005",
        client_id=cid,
        exposures=[
            {
                "exposure_type": "Payroll",
                "amount": 3000000,
                "denominator": 100,
                "address": "99 Different Ave",
                "city": "Austin",
                "state": "TX",
            }
        ],
        eff_date="2026-01-01",
    )

    # The new address still gets a project row (could be reused later)
    new_project = conn.execute(
        "SELECT id FROM projects WHERE client_id=? AND address='99 Different Ave'",
        (cid,),
    ).fetchone()
    assert new_project is not None

    # But the policy's project_id did NOT change
    pol = conn.execute(
        "SELECT project_id FROM policies WHERE policy_uid='POL-5005'"
    ).fetchone()
    assert pol["project_id"] == existing_pid


def test_idempotent_address_reuses_project(tmp_db):
    """Running AI import twice with the same address should not duplicate."""
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-5006")

    payload = [
        {
            "exposure_type": "Revenue",
            "amount": 8000000,
            "denominator": 1000,
            "address": "22 Elm Rd",
            "city": "Houston",
            "state": "TX",
        }
    ]
    _run_ai_exposure_ingest(conn, "POL-5006", cid, payload, "2026-04-01")
    _run_ai_exposure_ingest(conn, "POL-5006", cid, payload, "2026-04-01")

    project_count = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE client_id=? AND address='22 Elm Rd'",
        (cid,),
    ).fetchone()[0]
    assert project_count == 1

    # Only one exposure row, one link
    exposure_count = conn.execute(
        "SELECT COUNT(*) FROM client_exposures WHERE client_id=? AND exposure_type='Revenue'",
        (cid,),
    ).fetchone()[0]
    assert exposure_count == 1

    links = get_policy_exposures(conn, "POL-5006")
    assert len(links) == 1


def test_empty_or_invalid_exposure_entries_are_skipped(tmp_db):
    conn = get_connection()
    cid = _seed_client(conn)
    _seed_policy(conn, cid, "POL-5007")

    _run_ai_exposure_ingest(
        conn,
        policy_uid="POL-5007",
        client_id=cid,
        exposures=[
            {"exposure_type": "", "amount": 1000},  # no type
            {"exposure_type": "Payroll", "amount": 0},  # zero amount
            {"exposure_type": "Revenue", "amount": "not a number"},  # bad amount
            {"exposure_type": "Sales", "amount": 500000, "denominator": 1000},
            "not a dict",  # should be skipped without error
        ],
        eff_date="2026-04-01",
    )

    links = get_policy_exposures(conn, "POL-5007")
    assert len(links) == 1
    assert links[0]["exposure_type"] == "Sales"
    assert links[0]["is_primary"] == 1


# ── Importer CSV rewrite ───────────────────────────────────────────────────


def test_importer_post_inserts_exposure_via_client_exposures(tmp_db, tmp_path):
    """PolicyImporter.import_csv routes exposure columns into client_exposures."""
    from policydb.importer import PolicyImporter

    conn = get_connection()
    _seed_client(conn, "Importer Test Holdings LLC")
    conn.commit()

    csv_path = tmp_path / "policies.csv"
    csv_path.write_text(
        "client_name,policy_type,carrier,policy_number,effective_date,expiration_date,"
        "premium,exposure_basis,exposure_amount,exposure_denominator,"
        "exposure_address,exposure_city,exposure_state,exposure_zip\n"
        "Importer Test Holdings LLC,General Liability,Travelers,TC-IMP-1,"
        "2026-04-01,2027-04-01,60000,"
        "Payroll,12500000,100,"
        "777 Harbor Way,Seattle,WA,98101\n",
        encoding="utf-8",
    )

    importer = PolicyImporter(conn)
    importer.import_csv(csv_path, interactive=False)
    assert importer.imported == 1
    assert importer.skipped == 0

    # Find the policy we just imported
    pol = conn.execute(
        "SELECT id, policy_uid, project_id FROM policies WHERE policy_number='TC-IMP-1'"
    ).fetchone()
    assert pol is not None

    # Project was upserted from the address
    project = conn.execute(
        "SELECT id, address, city FROM projects WHERE address='777 Harbor Way'"
    ).fetchone()
    assert project is not None
    assert project["city"] == "Seattle"
    assert pol["project_id"] == project["id"]

    # Exposure row lives on client_exposures, linked to the project
    links = get_policy_exposures(conn, pol["policy_uid"])
    assert len(links) == 1
    link = links[0]
    assert link["exposure_type"] == "Payroll"
    assert link["amount"] == 12500000
    assert link["denominator"] == 100
    assert link["year"] == 2026
    assert link["project_id"] == project["id"]
    assert link["is_primary"] == 1


def test_importer_without_exposure_columns_skips_exposure_flow(tmp_db, tmp_path):
    """A row with no exposure columns imports cleanly without creating exposures."""
    from policydb.importer import PolicyImporter

    conn = get_connection()
    _seed_client(conn, "Bare Import Holdings LLC")
    conn.commit()

    csv_path = tmp_path / "bare.csv"
    csv_path.write_text(
        "client_name,policy_type,carrier,policy_number,effective_date,expiration_date,premium\n"
        "Bare Import Holdings LLC,Auto,Progressive,TC-BARE-1,2026-04-01,2027-04-01,10000\n",
        encoding="utf-8",
    )

    importer = PolicyImporter(conn)
    importer.import_csv(csv_path, interactive=False)
    assert importer.imported == 1

    pol = conn.execute(
        "SELECT policy_uid FROM policies WHERE policy_number='TC-BARE-1'"
    ).fetchone()
    assert pol is not None
    assert get_policy_exposures(conn, pol["policy_uid"]) == []


# ── AI import parse route: exposure review (no eager write) ────────────────


@pytest.fixture
def client_and_policy(tmp_db):
    """Seed a client + policy and yield (client_id, policy_uid)."""
    conn = get_connection()
    cid = _seed_client(conn, "Route Test Co")
    _seed_policy(conn, cid, "POL-7001")
    conn.commit()
    return cid, "POL-7001"


def _post_parse(policy_uid: str, extracted: dict):
    """Drive the /ai-import/parse route and return (status, html_body)."""
    from fastapi.testclient import TestClient
    from policydb.web.app import app
    import json

    client = TestClient(app)
    resp = client.post(
        f"/policies/{policy_uid}/ai-import/parse",
        data={"json_text": json.dumps(extracted)},
    )
    return resp.status_code, resp.text


def _post_apply_exposures(policy_uid: str, year: int, rows: list[dict]):
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    client = TestClient(app)
    resp = client.post(
        f"/policies/{policy_uid}/ai-import/apply-exposures",
        json={"year": year, "exposures": rows},
    )
    return resp.status_code, resp.json()


def test_parse_route_does_not_eager_write_exposures(client_and_policy):
    """AI parse must build diffs, not write client_exposures."""
    cid, uid = client_and_policy
    conn = get_connection()

    status, body = _post_parse(uid, {
        "effective_date": "2026-05-01",
        "expiration_date": "2027-05-01",
        "exposures": [
            {
                "exposure_type": "Payroll",
                "amount": 7500000,
                "denominator": 100,
                "unit": "Per $100 Payroll",
            }
        ],
    })
    assert status == 200

    # Nothing should be in client_exposures or policy_exposure_links yet.
    exp_count = conn.execute(
        "SELECT COUNT(*) FROM client_exposures WHERE client_id=?", (cid,)
    ).fetchone()[0]
    assert exp_count == 0

    link_count = conn.execute(
        "SELECT COUNT(*) FROM policy_exposure_links WHERE policy_uid=?", (uid,)
    ).fetchone()[0]
    assert link_count == 0

    # The rendered review panel should mention the Exposures section.
    assert "Exposures" in body
    assert "Payroll" in body
    assert "Apply Selected Exposures" in body


def test_apply_route_creates_exposure_from_approved_row(client_and_policy):
    cid, uid = client_and_policy

    status, data = _post_apply_exposures(uid, 2026, [
        {
            "exposure_type": "Payroll",
            "amount": 7500000,
            "denominator": 100,
            "unit": "Per $100 Payroll",
            "is_primary": True,
        }
    ])
    assert status == 200
    assert data["ok"] is True
    assert data["applied"] == 1

    conn = get_connection()
    links = get_policy_exposures(conn, uid)
    assert len(links) == 1
    assert links[0]["exposure_type"] == "Payroll"
    assert links[0]["amount"] == 7500000
    assert links[0]["year"] == 2026
    assert links[0]["is_primary"] == 1


def test_apply_route_honors_primary_flag_on_non_first_row(client_and_policy):
    _, uid = client_and_policy

    status, data = _post_apply_exposures(uid, 2026, [
        {"exposure_type": "Payroll", "amount": 5000000, "denominator": 100,
         "is_primary": False},
        {"exposure_type": "Gross Sales", "amount": 25000000, "denominator": 1000,
         "is_primary": True},
    ])
    assert status == 200
    assert data["applied"] == 2

    conn = get_connection()
    links = {l["exposure_type"]: l for l in get_policy_exposures(conn, uid)}
    assert links["Payroll"]["is_primary"] == 0
    assert links["Gross Sales"]["is_primary"] == 1


def test_apply_route_upserts_project_from_address(client_and_policy):
    cid, uid = client_and_policy

    status, data = _post_apply_exposures(uid, 2026, [
        {
            "exposure_type": "Building Value",
            "amount": 10000000,
            "denominator": 1,
            "address": "500 Commerce St",
            "city": "Dallas",
            "state": "TX",
            "zip": "75201",
            "is_primary": True,
        }
    ])
    assert status == 200
    assert data["locations_created"] == 1

    conn = get_connection()
    project = conn.execute(
        "SELECT id FROM projects WHERE client_id=? AND address='500 Commerce St'",
        (cid,),
    ).fetchone()
    assert project is not None

    # Policy.project_id was stamped because it was previously null.
    pol = conn.execute(
        "SELECT project_id FROM policies WHERE policy_uid=?", (uid,)
    ).fetchone()
    assert pol["project_id"] == project["id"]


def test_apply_route_rejects_missing_year():
    from fastapi.testclient import TestClient
    from policydb.web.app import app

    client = TestClient(app)
    resp = client.post(
        "/policies/POL-0001/ai-import/apply-exposures",
        json={"exposures": [{"exposure_type": "Payroll", "amount": 1000}]},
    )
    assert resp.status_code == 400
    assert "year" in resp.json()["error"].lower()


def test_apply_route_skips_invalid_rows(client_and_policy):
    _, uid = client_and_policy

    status, data = _post_apply_exposures(uid, 2026, [
        {"exposure_type": "", "amount": 1000},       # no type → skipped
        {"exposure_type": "Payroll", "amount": 0},    # zero amount → skipped
        {"exposure_type": "Sales", "amount": 500000, "denominator": 1000,
         "is_primary": True},
    ])
    assert status == 200
    assert data["applied"] == 1

    conn = get_connection()
    links = get_policy_exposures(conn, uid)
    assert len(links) == 1
    assert links[0]["exposure_type"] == "Sales"


def test_apply_route_updates_existing_exposure_amount(client_and_policy):
    """Re-apply with a different amount updates the client_exposures row."""
    cid, uid = client_and_policy

    # First apply
    status, _ = _post_apply_exposures(uid, 2026, [
        {"exposure_type": "Payroll", "amount": 5000000, "denominator": 100,
         "is_primary": True},
    ])
    assert status == 200

    # Second apply with a new amount for the same type/year
    status, data = _post_apply_exposures(uid, 2026, [
        {"exposure_type": "Payroll", "amount": 7500000, "denominator": 100,
         "is_primary": True},
    ])
    assert status == 200
    assert data["applied"] == 1

    conn = get_connection()
    # Still one row (idempotent on type/year), amount is updated
    rows = conn.execute(
        "SELECT amount FROM client_exposures WHERE client_id=? AND exposure_type='Payroll'",
        (cid,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["amount"] == 7500000
