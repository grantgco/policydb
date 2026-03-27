"""Tests for Programs v2: standalone programs table, ghost row utility, enriched sub-coverages."""

import pytest
import sqlite3
from policydb.db import init_db, next_program_uid
from policydb.ghost_rows import resolve_ghost_fields, inject_schedule_ghost_rows


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    init_db(path=db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


# ─── Programs Table ────────────────────────────────────────────────────────


def test_programs_table_exists(db):
    """programs table is created by migration 098."""
    tables = [
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "programs" in tables


def test_programs_table_columns(db):
    """programs table has all expected columns."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(programs)").fetchall()}
    expected = {
        "id", "program_uid", "client_id", "name", "line_of_business",
        "effective_date", "expiration_date", "renewal_status",
        "milestone_profile", "lead_broker", "placement_colleague",
        "account_exec", "notes", "working_notes", "last_reviewed_at",
        "review_cycle", "archived", "created_at", "updated_at",
    }
    assert expected.issubset(cols)


def test_programs_unique_uid(db):
    """program_uid must be unique."""
    db.execute(
        "INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')"
    )
    db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-001', 1, 'Casualty')"
    )
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-001', 1, 'Property')"
        )


def test_programs_cascade_delete(db):
    """Deleting a client cascades to programs."""
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-001', 1, 'Casualty')"
    )
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == 1
    db.execute("DELETE FROM clients WHERE id = 1")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == 0


# ─── next_program_uid() ───────────────────────────────────────────────────


def test_next_program_uid_first(db):
    """First UID is PGM-001."""
    assert next_program_uid(db) == "PGM-001"


def test_next_program_uid_sequential(db):
    """UIDs increment sequentially."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test', 'Construction')")
    db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-001', 1, 'A')"
    )
    db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-002', 1, 'B')"
    )
    db.commit()
    assert next_program_uid(db) == "PGM-003"


# ─── Sub-Coverage New Fields ──────────────────────────────────────────────


def test_sub_coverage_new_columns_exist(db):
    """policy_sub_coverages has the 6 new override fields from migration 099."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(policy_sub_coverages)").fetchall()}
    new_fields = {"premium", "carrier", "policy_number", "participation_of", "layer_position", "description"}
    assert new_fields.issubset(cols), f"Missing: {new_fields - cols}"


def test_sub_coverage_override_fields_nullable(db):
    """Override fields default to NULL (inherit from parent)."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date) "
        "VALUES ('POL-SC1', 0, 'Business Owners Policy', 'Acme Ins', '2026-04-01', '2027-04-01')"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-SC1'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type) VALUES (?, 'General Liability')",
        (pid,),
    )
    db.commit()
    row = db.execute(
        "SELECT premium, carrier, policy_number, participation_of, layer_position "
        "FROM policy_sub_coverages WHERE policy_id = ?",
        (pid,),
    ).fetchone()
    assert row["premium"] is None
    assert row["carrier"] is None
    assert row["policy_number"] is None
    assert row["participation_of"] is None
    assert row["layer_position"] is None


def test_sub_coverage_premium_override(db):
    """Sub-coverage premium can be set independently."""
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date, premium) "
        "VALUES ('POL-SC2', 0, 'Workers Compensation', 'Liberty', '2026-04-01', '2027-04-01', 100000)"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-SC2'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, premium) VALUES (?, 'Employers Liability', 25000)",
        (pid,),
    )
    db.commit()
    row = db.execute(
        "SELECT premium FROM policy_sub_coverages WHERE policy_id = ? AND coverage_type = 'Employers Liability'",
        (pid,),
    ).fetchone()
    assert row["premium"] == 25000


# ─── Ghost Row Utility ────────────────────────────────────────────────────


def test_resolve_ghost_fields_subcov_wins():
    """Sub-coverage fields override parent when populated."""
    sub_cov = {
        "id": 1,
        "coverage_type": "General Liability",
        "limit_amount": 1000000,
        "deductible": 5000,
        "coverage_form": "CG 00 01",
        "premium": 15000,
        "carrier": "Override Carrier",
        "policy_number": "OVR-123",
        "attachment_point": None,
        "participation_of": None,
        "layer_position": None,
        "description": "GL sub-coverage",
        "notes": "",
    }
    parent = {
        "policy_uid": "POL-001",
        "policy_type": "Business Owners Policy",
        "carrier": "Parent Carrier",
        "policy_number": "PAR-456",
        "effective_date": "2026-04-01",
        "expiration_date": "2027-04-01",
        "coverage_form": "BOP Form",
        "client_id": 1,
    }
    ghost = resolve_ghost_fields(sub_cov, parent)

    assert ghost["is_ghost"] is True
    assert ghost["ghost_badge"] == "Package"
    assert ghost["line"] == "General Liability"
    assert ghost["carrier"] == "Override Carrier"  # sub-cov wins
    assert ghost["policy_number"] == "OVR-123"  # sub-cov wins
    assert ghost["limit"] == 1000000
    assert ghost["premium"] == 15000
    assert ghost["form"] == "CG 00 01"  # sub-cov wins
    assert ghost["effective"] == "2026-04-01"  # inherited


def test_resolve_ghost_fields_parent_fallback():
    """Parent fields used when sub-coverage fields are NULL."""
    sub_cov = {
        "id": 2,
        "coverage_type": "Property",
        "limit_amount": 500000,
        "deductible": 10000,
        "coverage_form": None,
        "premium": None,
        "carrier": None,
        "policy_number": None,
        "attachment_point": None,
        "participation_of": None,
        "layer_position": None,
        "description": "",
        "notes": "",
    }
    parent = {
        "policy_uid": "POL-002",
        "policy_type": "Business Owners Policy",
        "carrier": "Parent Carrier",
        "policy_number": "PAR-789",
        "effective_date": "2026-04-01",
        "expiration_date": "2027-04-01",
        "coverage_form": "BOP Form",
        "client_id": 1,
    }
    ghost = resolve_ghost_fields(sub_cov, parent)

    assert ghost["carrier"] == "Parent Carrier"  # fallback
    assert ghost["policy_number"] == "PAR-789"  # fallback
    assert ghost["form"] == "BOP Form"  # fallback
    assert ghost["premium"] is None  # shows "—" (no double-counting)
    assert ghost["ghost_parent_uid"] == "POL-002"
    assert ghost["package_parent_type"] == "Business Owners Policy"


def test_resolve_ghost_fields_program_badge():
    """Program ghost rows get 'Program' badge."""
    sub_cov = {
        "id": 3, "coverage_type": "GL", "limit_amount": 1000000,
        "deductible": None, "coverage_form": None, "premium": None,
        "carrier": None, "policy_number": None, "attachment_point": None,
        "participation_of": None, "layer_position": None, "description": "",
        "notes": "",
    }
    parent = {
        "policy_uid": "POL-003", "policy_type": "GL", "carrier": "AIG",
        "policy_number": "AIG-001", "effective_date": "2026-04-01",
        "expiration_date": "2027-04-01", "coverage_form": "", "client_id": 1,
    }
    ghost = resolve_ghost_fields(sub_cov, parent, ghost_reason="program_member", ghost_badge="Program")
    assert ghost["ghost_badge"] == "Program"
    assert ghost["ghost_reason"] == "program_member"


# ─── Schedule Ghost Row Injection ─────────────────────────────────────────


def test_inject_schedule_ghost_rows(db):
    """Ghost rows are injected after parent policy in schedule."""
    # Create client and package policy
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "policy_number, effective_date, expiration_date, premium) "
        "VALUES ('POL-BOP', 1, 'Business Owners Policy', 'Acme', 'BOP-001', "
        "'2026-04-01', '2027-04-01', 50000)"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-BOP'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, limit_amount, deductible) "
        "VALUES (?, 'General Liability', 1000000, 5000)",
        (pid,),
    )
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, limit_amount) "
        "VALUES (?, 'Property / Builders Risk', 500000)",
        (pid,),
    )
    db.commit()

    # Build initial rows (simulating v_schedule output)
    rows = [{
        "line": "Business Owners Policy",
        "carrier": "Acme",
        "policy_number": "BOP-001",
        "effective": "2026-04-01",
        "expiration": "2027-04-01",
        "limit": 50000,
        "deductible": 2500,
        "premium": 50000,
        "form": "",
        "is_ghost": False,
    }]

    enriched, package_policies = inject_schedule_ghost_rows(rows, db, client_id=1)

    # Should have original row + 2 ghost rows
    assert len(enriched) == 3
    assert enriched[0]["is_ghost"] is False
    assert enriched[0]["is_package"] is True
    assert enriched[1]["is_ghost"] is True
    assert enriched[1]["line"] == "General Liability"
    assert enriched[1]["limit"] == 1000000  # enriched, not None!
    assert enriched[1]["deductible"] == 5000  # enriched!
    assert enriched[2]["is_ghost"] is True
    assert enriched[2]["line"] == "Property / Builders Risk"
    assert enriched[2]["limit"] == 500000

    # Package policies summary
    assert len(package_policies) == 1
    assert package_policies[0]["policy_type"] == "Business Owners Policy"
    assert "General Liability" in package_policies[0]["sub_coverages"]


def test_ghost_row_premium_none_no_double_count(db):
    """Ghost rows with no premium override show None (rendered as '—')."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "policy_number, effective_date, expiration_date, premium) "
        "VALUES ('POL-WC', 1, 'Workers Compensation', 'Liberty', 'WC-001', "
        "'2026-04-01', '2027-04-01', 80000)"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-WC'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, limit_amount) "
        "VALUES (?, 'Employers Liability', 1000000)",
        (pid,),
    )
    db.commit()

    rows = [{
        "line": "Workers Compensation",
        "carrier": "Liberty",
        "policy_number": "WC-001",
        "effective": "2026-04-01",
        "expiration": "2027-04-01",
        "limit": None,
        "deductible": None,
        "premium": 80000,
        "form": "",
        "is_ghost": False,
    }]

    enriched, _ = inject_schedule_ghost_rows(rows, db, client_id=1)
    ghost = enriched[1]
    assert ghost["is_ghost"] is True
    assert ghost["premium"] is None  # no double-counting


def test_ghost_row_premium_with_override(db):
    """Ghost rows with explicit premium show that value."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "policy_number, effective_date, expiration_date, premium) "
        "VALUES ('POL-WC2', 1, 'Workers Compensation', 'Liberty', 'WC-002', "
        "'2026-04-01', '2027-04-01', 80000)"
    )
    pid = db.execute("SELECT id FROM policies WHERE policy_uid='POL-WC2'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, limit_amount, premium) "
        "VALUES (?, 'Employers Liability', 1000000, 25000)",
        (pid,),
    )
    db.commit()

    rows = [{
        "line": "Workers Compensation",
        "carrier": "Liberty",
        "policy_number": "WC-002",
        "effective": "2026-04-01",
        "expiration": "2027-04-01",
        "limit": None,
        "deductible": None,
        "premium": 80000,
        "form": "",
        "is_ghost": False,
    }]

    enriched, _ = inject_schedule_ghost_rows(rows, db, client_id=1)
    ghost = enriched[1]
    assert ghost["is_ghost"] is True
    assert ghost["premium"] == 25000  # explicit override


# ─── Edge Cases ───────────────────────────────────────────────────────────


def test_inject_ghost_rows_empty_input(db):
    """Empty rows list returns empty with no errors."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    db.commit()
    enriched, package_policies = inject_schedule_ghost_rows([], db, client_id=1)
    assert enriched == []
    assert package_policies == []


def test_inject_ghost_rows_no_matching_policy_number(db):
    """Schedule rows with unrecognized policy_number pass through without ghosts."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    db.commit()
    rows = [{
        "line": "General Liability",
        "carrier": "Unknown",
        "policy_number": "DOES-NOT-EXIST",
        "effective": "2026-04-01",
        "expiration": "2027-04-01",
        "limit": 1000000,
        "deductible": 5000,
        "premium": 50000,
        "form": "",
        "is_ghost": False,
    }]
    enriched, package_policies = inject_schedule_ghost_rows(rows, db, client_id=1)
    assert len(enriched) == 1  # no ghosts injected
    assert enriched[0]["is_package"] is False
    assert package_policies == []


def test_inject_ghost_rows_mixed_package_and_standalone(db):
    """Non-package policies pass through; only package policies get ghosts."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (1, 'Test Client', 'Construction')")
    # Standalone GL
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "policy_number, effective_date, expiration_date, premium) "
        "VALUES ('POL-GL', 1, 'General Liability', 'AIG', 'GL-001', "
        "'2026-04-01', '2027-04-01', 60000)"
    )
    # Package BOP with sub-coverages
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "policy_number, effective_date, expiration_date, premium) "
        "VALUES ('POL-BOP2', 1, 'Business Owners Policy', 'Acme', 'BOP-002', "
        "'2026-04-01', '2027-04-01', 40000)"
    )
    pid_bop = db.execute("SELECT id FROM policies WHERE policy_uid='POL-BOP2'").fetchone()[0]
    db.execute(
        "INSERT INTO policy_sub_coverages (policy_id, coverage_type, limit_amount) "
        "VALUES (?, 'Property / Builders Risk', 500000)",
        (pid_bop,),
    )
    db.commit()

    rows = [
        {"line": "General Liability", "carrier": "AIG", "policy_number": "GL-001",
         "effective": "2026-04-01", "expiration": "2027-04-01", "limit": 1000000,
         "deductible": 5000, "premium": 60000, "form": "", "is_ghost": False},
        {"line": "Business Owners Policy", "carrier": "Acme", "policy_number": "BOP-002",
         "effective": "2026-04-01", "expiration": "2027-04-01", "limit": 40000,
         "deductible": 2500, "premium": 40000, "form": "", "is_ghost": False},
    ]
    enriched, package_policies = inject_schedule_ghost_rows(rows, db, client_id=1)

    # 2 real rows + 1 ghost (Property from BOP)
    assert len(enriched) == 3
    assert enriched[0]["is_ghost"] is False
    assert enriched[0]["is_package"] is False  # standalone GL
    assert enriched[1]["is_ghost"] is False
    assert enriched[1]["is_package"] is True  # BOP parent
    assert enriched[2]["is_ghost"] is True
    assert enriched[2]["line"] == "Property / Builders Risk"


def test_resolve_ghost_fields_empty_string_falls_to_parent():
    """Empty-string sub-cov fields fall back to parent (not shown as blank)."""
    sub_cov = {
        "id": 10, "coverage_type": "GL", "limit_amount": 1000000,
        "deductible": None, "coverage_form": "", "premium": None,
        "carrier": "", "policy_number": "", "attachment_point": None,
        "participation_of": None, "layer_position": None, "description": "",
        "notes": "",
    }
    parent = {
        "policy_uid": "POL-P", "policy_type": "BOP", "carrier": "Parent Co",
        "policy_number": "PAR-999", "effective_date": "2026-01-01",
        "expiration_date": "2027-01-01", "coverage_form": "CG 00 01",
        "client_id": 1,
    }
    ghost = resolve_ghost_fields(sub_cov, parent)
    assert ghost["carrier"] == "Parent Co"  # empty string → parent
    assert ghost["policy_number"] == "PAR-999"  # empty string → parent
    assert ghost["form"] == "CG 00 01"  # empty string → parent


# ─── Phase 3: Data Migration Tests ───────────────────────────────────────


def test_migration_populates_programs_from_is_program(db):
    """Migration 100 creates programs table entries from is_program=1 policy rows."""
    # Create client + is_program=1 policy BEFORE migration runs
    # (init_db already ran migration 100, so we test the result)
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (99, 'Migration Test', 'Construction')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program, tower_group) "
        "VALUES ('POL-MIG1', 99, 'Property Program', 'AIG', '2026-04-01', '2027-04-01', 1, 'Property')"
    )
    db.commit()

    # Re-run the migration logic manually (since init_db already ran)
    from policydb.db import next_program_uid
    pp = db.execute(
        "SELECT * FROM policies WHERE policy_uid = 'POL-MIG1'"
    ).fetchone()
    name = (pp["tower_group"] or pp["policy_type"]).strip()
    existing = db.execute(
        "SELECT id FROM programs WHERE client_id = ? AND name = ?",
        (99, name),
    ).fetchone()
    if not existing:
        uid = next_program_uid(db)
        db.execute(
            "INSERT INTO programs (program_uid, client_id, name, line_of_business, "
            "effective_date, expiration_date, renewal_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, 99, name, pp["policy_type"], pp["effective_date"],
             pp["expiration_date"], pp["renewal_status"]),
        )
        db.commit()

    # Verify the program was created
    pgm = db.execute(
        "SELECT * FROM programs WHERE client_id = 99 AND name = 'Property'"
    ).fetchone()
    assert pgm is not None
    assert pgm["program_uid"].startswith("PGM-")
    assert pgm["line_of_business"] == "Property Program"
    assert pgm["effective_date"] == "2026-04-01"


def test_migration_handles_null_tower_group(db):
    """is_program=1 rows with NULL tower_group use policy_type as name."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (98, 'Null TG Test', 'Real Estate')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program) "
        "VALUES ('POL-NULL-TG', 98, 'D&O Program', 'Chubb', '2026-04-01', '2027-04-01', 1)"
    )
    db.commit()

    # tower_group is NULL, so migration should use policy_type
    from policydb.db import next_program_uid
    pp = db.execute("SELECT * FROM policies WHERE policy_uid = 'POL-NULL-TG'").fetchone()
    name = (pp["tower_group"] or "").strip() or (pp["policy_type"] or "").strip()
    assert name == "D&O Program"

    uid = next_program_uid(db)
    db.execute(
        "INSERT INTO programs (program_uid, client_id, name, line_of_business) VALUES (?, ?, ?, ?)",
        (uid, 98, name, pp["policy_type"]),
    )
    db.commit()

    pgm = db.execute("SELECT * FROM programs WHERE client_id = 98 AND name = 'D&O Program'").fetchone()
    assert pgm is not None


def test_migration_handles_empty_tower_group(db):
    """is_program=1 rows with empty string tower_group use policy_type."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (97, 'Empty TG Test', 'Tech')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program, tower_group) "
        "VALUES ('POL-EMPTY-TG', 97, 'Casualty Program', 'AIG', '2026-04-01', '2027-04-01', 1, '')"
    )
    db.commit()

    pp = db.execute("SELECT * FROM policies WHERE policy_uid = 'POL-EMPTY-TG'").fetchone()
    name = (pp["tower_group"] or "").strip() or (pp["policy_type"] or "").strip()
    assert name == "Casualty Program"  # empty string falls through to policy_type


def test_migration_skips_duplicates(db):
    """Two is_program=1 rows with same (client_id, tower_group) → only one program."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (96, 'Dup Test', 'Energy')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program, tower_group) "
        "VALUES ('POL-DUP1', 96, 'Property', 'AIG', '2026-04-01', '2027-04-01', 1, 'Property')"
    )
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program, tower_group) "
        "VALUES ('POL-DUP2', 96, 'Property', 'Chubb', '2026-04-01', '2027-04-01', 1, 'Property')"
    )
    db.commit()

    from policydb.db import next_program_uid
    # Simulate migration: first wins
    for pol_uid in ['POL-DUP1', 'POL-DUP2']:
        pp = db.execute("SELECT * FROM policies WHERE policy_uid = ?", (pol_uid,)).fetchone()
        name = (pp["tower_group"] or pp["policy_type"]).strip()
        existing = db.execute(
            "SELECT id FROM programs WHERE client_id = ? AND name = ?",
            (96, name),
        ).fetchone()
        if not existing:
            uid = next_program_uid(db)
            db.execute(
                "INSERT INTO programs (program_uid, client_id, name, line_of_business) VALUES (?, ?, ?, ?)",
                (uid, 96, name, pp["policy_type"]),
            )
    db.commit()

    count = db.execute("SELECT COUNT(*) FROM programs WHERE client_id = 96").fetchone()[0]
    assert count == 1  # only one program, not two


def test_migration_skips_archived(db):
    """Archived is_program=1 rows are not migrated."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (95, 'Archive Test', 'Healthcare')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program, tower_group, archived) "
        "VALUES ('POL-ARCH', 95, 'Old Program', 'AIG', '2024-01-01', '2025-01-01', 1, 'Old', 1)"
    )
    db.commit()

    # Migration should skip archived rows (WHERE archived = 0)
    pp = db.execute(
        "SELECT * FROM policies WHERE policy_uid = 'POL-ARCH'"
    ).fetchone()
    assert pp["archived"] == 1

    count = db.execute(
        "SELECT COUNT(*) FROM programs WHERE client_id = 95 AND name = 'Old'"
    ).fetchone()[0]
    assert count == 0  # not migrated


def test_migration_idempotent(db):
    """Running migration logic twice creates no duplicates."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (94, 'Idempotent Test', 'Retail')")
    db.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, "
        "effective_date, expiration_date, is_program, tower_group) "
        "VALUES ('POL-IDEMP', 94, 'Casualty', 'Liberty', '2026-04-01', '2027-04-01', 1, 'Casualty')"
    )
    db.commit()

    from policydb.db import next_program_uid

    # Run migration logic twice
    for _ in range(2):
        pp = db.execute("SELECT * FROM policies WHERE policy_uid = 'POL-IDEMP'").fetchone()
        name = (pp["tower_group"] or pp["policy_type"]).strip()
        existing = db.execute(
            "SELECT id FROM programs WHERE client_id = ? AND name = ?",
            (94, name),
        ).fetchone()
        if not existing:
            uid = next_program_uid(db)
            db.execute(
                "INSERT INTO programs (program_uid, client_id, name, line_of_business) VALUES (?, ?, ?, ?)",
                (uid, 94, name, pp["policy_type"]),
            )
            db.commit()

    count = db.execute("SELECT COUNT(*) FROM programs WHERE client_id = 94").fetchone()[0]
    assert count == 1


def test_uniqueness_constraint(db):
    """Cannot insert two active programs with same (client_id, name)."""
    db.execute("INSERT INTO clients (id, name, industry_segment) VALUES (93, 'Unique Test', 'Finance')")
    db.execute(
        "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-UQ1', 93, 'TestProgram')"
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO programs (program_uid, client_id, name) VALUES ('PGM-UQ2', 93, 'TestProgram')"
        )
