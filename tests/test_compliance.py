"""Tests for the compliance engine."""

import json
import sqlite3
from pathlib import Path

from policydb.compliance import (
    _parse_endorsements,
    resolve_governing_requirements,
    suggest_policy_for_requirement,
    compute_compliance_summary,
    get_risk_review_prompts,
    get_location_requirements,
    get_client_compliance_data,
    spawn_requirements_from_risk,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _req(coverage_line="General Liability", required_limit=1_000_000,
         max_deductible=None, deductible_type=None,
         required_endorsements=None,
         source_id=None, source_name="Contract A",
         project_id=None, client_id=1, id=1, risk_id=None,
         compliance_status="Needs Review", linked_policy_uid=None,
         notes=None):
    return {
        "id": id, "client_id": client_id, "project_id": project_id,
        "risk_id": risk_id, "source_id": source_id, "source_name": source_name,
        "coverage_line": coverage_line, "required_limit": required_limit,
        "max_deductible": max_deductible, "deductible_type": deductible_type,
        "required_endorsements": required_endorsements or [],
        "compliance_status": compliance_status,
        "linked_policy_uid": linked_policy_uid, "notes": notes,
    }


def _policy(uid="POL-001", policy_type="General Liability", carrier="Hartford",
            limit_amount=1_000_000, deductible=5_000, project_id=None):
    return {
        "policy_uid": uid, "policy_type": policy_type, "carrier": carrier,
        "limit_amount": limit_amount, "deductible": deductible,
        "project_id": project_id,
    }


# ── Governing resolution ────────────────────────────────────────────────────

def test_single_source_becomes_governing():
    """One requirement for a coverage line = that requirement governs."""
    reqs = [_req(coverage_line="GL", required_limit=1_000_000)]
    gov = resolve_governing_requirements(reqs)
    assert len(gov) == 1
    assert gov["GL"]["required_limit"] == 1_000_000


def test_most_stringent_limit_wins():
    """When two sources require different limits, higher limit governs."""
    reqs = [
        _req(id=1, coverage_line="GL", required_limit=1_000_000,
             source_name="Contract A"),
        _req(id=2, coverage_line="GL", required_limit=2_000_000,
             source_name="Contract B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["GL"]["required_limit"] == 2_000_000
    assert gov["GL"]["governing_source"] == "Contract B"


def test_most_stringent_deductible_wins():
    """Lower max_deductible is more stringent."""
    reqs = [
        _req(id=1, coverage_line="Property", max_deductible=10_000,
             source_name="Lender A"),
        _req(id=2, coverage_line="Property", max_deductible=2_000,
             source_name="Lender B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["Property"]["max_deductible"] == 2_000
    assert gov["Property"]["governing_source"] == "Lender B"


def test_endorsements_unioned_across_sources():
    """Endorsements from all sources are unioned together."""
    reqs = [
        _req(id=1, coverage_line="GL",
             required_endorsements=["Additional Insured"],
             source_name="A"),
        _req(id=2, coverage_line="GL",
             required_endorsements=["Waiver of Subrogation"],
             source_name="B"),
    ]
    gov = resolve_governing_requirements(reqs)
    endorsements = gov["GL"]["required_endorsements"]
    assert "Additional Insured" in endorsements
    assert "Waiver of Subrogation" in endorsements


def test_multiple_coverage_lines_resolved_independently():
    """Each coverage line resolves independently."""
    reqs = [
        _req(id=1, coverage_line="GL", required_limit=1_000_000),
        _req(id=2, coverage_line="Umbrella", required_limit=5_000_000),
    ]
    gov = resolve_governing_requirements(reqs)
    assert "GL" in gov
    assert "Umbrella" in gov
    assert gov["GL"]["required_limit"] == 1_000_000
    assert gov["Umbrella"]["required_limit"] == 5_000_000


def test_empty_requirements_returns_empty():
    gov = resolve_governing_requirements([])
    assert gov == {}


def test_governing_source_field_present_single():
    """governing_source is set even for single-source requirements."""
    reqs = [_req(coverage_line="Cyber", source_name="Vendor Contract")]
    gov = resolve_governing_requirements(reqs)
    assert gov["Cyber"]["governing_source"] == "Vendor Contract"


def test_source_requirements_list_attached():
    """source_requirements contains all raw requirements for the line."""
    reqs = [
        _req(id=1, coverage_line="GL", required_limit=1_000_000, source_name="A"),
        _req(id=2, coverage_line="GL", required_limit=2_000_000, source_name="B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert len(gov["GL"]["source_requirements"]) == 2


def test_deductible_none_means_no_restriction():
    """If governing has no deductible limit but a new source does, adopt it."""
    reqs = [
        _req(id=1, coverage_line="Property", max_deductible=None, source_name="A"),
        _req(id=2, coverage_line="Property", max_deductible=5_000, source_name="B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["Property"]["max_deductible"] == 5_000


def test_endorsements_deduplicated_across_sources():
    """Duplicate endorsements across sources are deduplicated."""
    reqs = [
        _req(id=1, coverage_line="GL",
             required_endorsements=["Additional Insured", "Notice of Cancellation"],
             source_name="A"),
        _req(id=2, coverage_line="GL",
             required_endorsements=["Additional Insured", "Completed Operations"],
             source_name="B"),
        _req(id=3, coverage_line="GL",
             required_endorsements=["Per-Project Aggregate"],
             source_name="C"),
    ]
    gov = resolve_governing_requirements(reqs)
    endorsements = gov["GL"]["required_endorsements"]
    assert len(endorsements) == 4
    assert "Additional Insured" in endorsements
    assert "Notice of Cancellation" in endorsements
    assert "Completed Operations" in endorsements
    assert "Per-Project Aggregate" in endorsements


# ── Policy matching ─────────────────────────────────────────────────────────

def test_suggest_exact_coverage_match():
    """Policy with matching coverage type is suggested."""
    policies = [_policy(uid="POL-001", policy_type="General Liability")]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, policies)
    assert suggestion is not None
    assert suggestion["policy_uid"] == "POL-001"


def test_suggest_prefers_location_policy_over_corporate():
    """Location-specific policy preferred over corporate (no project_id)."""
    policies = [
        _policy(uid="CORP-GL", policy_type="General Liability", project_id=None),
        _policy(uid="LOC-GL", policy_type="General Liability", project_id=5),
    ]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000,
               "project_id": 5}
    suggestion = suggest_policy_for_requirement(gov_req, policies, location_project_id=5)
    assert suggestion["policy_uid"] == "LOC-GL"


def test_suggest_corporate_when_no_location_match():
    """Corporate policy (no project_id) covers any location."""
    policies = [
        _policy(uid="CORP-GL", policy_type="General Liability", project_id=None),
    ]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000,
               "project_id": 5}
    suggestion = suggest_policy_for_requirement(gov_req, policies, location_project_id=5)
    assert suggestion["policy_uid"] == "CORP-GL"


def test_suggest_no_match_returns_none():
    """No matching policy returns None."""
    policies = [_policy(uid="POL-WC", policy_type="Workers Compensation")]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, policies)
    assert suggestion is None


def test_suggest_no_policies_returns_none():
    """Empty policy list returns None."""
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, [])
    assert suggestion is None


def test_suggest_no_location_project_id_uses_corporate():
    """When no location_project_id given, corporate policies match."""
    policies = [_policy(uid="CORP-GL", policy_type="General Liability", project_id=None)]
    gov_req = {"coverage_line": "General Liability", "required_limit": 1_000_000}
    suggestion = suggest_policy_for_requirement(gov_req, policies, location_project_id=None)
    assert suggestion["policy_uid"] == "CORP-GL"


# ── Summary computation ─────────────────────────────────────────────────────

def test_compliance_summary():
    """Summary computes correct totals and percentages."""
    governing = {
        "GL": {"compliance_status": "Compliant", "coverage_line": "GL"},
        "Umbrella": {"compliance_status": "Compliant", "coverage_line": "Umbrella"},
        "Property": {"compliance_status": "Gap", "coverage_line": "Property"},
        "D&O": {"compliance_status": "Needs Review", "coverage_line": "D&O"},
    }
    summary = compute_compliance_summary(governing)
    assert summary["total"] == 4
    assert summary["compliant"] == 2
    assert summary["gap"] == 1
    assert summary["needs_review"] == 1
    assert summary["compliance_pct"] == 50  # 2/4 * 100


def test_compliance_summary_empty():
    """Empty governing returns zeros and zero percent."""
    summary = compute_compliance_summary({})
    assert summary["total"] == 0
    assert summary["compliance_pct"] == 0


def test_compliance_summary_all_compliant():
    """All compliant = 100%."""
    governing = {
        "GL": {"compliance_status": "Compliant"},
        "WC": {"compliance_status": "Compliant"},
    }
    summary = compute_compliance_summary(governing)
    assert summary["compliance_pct"] == 100
    assert summary["compliant"] == 2


def test_compliance_summary_waived_and_na():
    """Waived and N/A statuses are tracked but not counted as compliant."""
    governing = {
        "GL": {"compliance_status": "Compliant"},
        "D&O": {"compliance_status": "Waived"},
        "EPL": {"compliance_status": "N/A"},
        "Cyber": {"compliance_status": "Partial"},
    }
    summary = compute_compliance_summary(governing)
    assert summary["total"] == 4
    assert summary["compliant"] == 1
    assert summary["waived"] == 1
    assert summary["na"] == 1
    assert summary["partial"] == 1
    assert summary["compliance_pct"] == 25


def test_compliance_summary_none_status_treated_as_needs_review():
    """None compliance_status defaults to needs_review."""
    governing = {
        "GL": {"compliance_status": None},
    }
    summary = compute_compliance_summary(governing)
    assert summary["needs_review"] == 1


# ── Risk review prompts ─────────────────────────────────────────────────────

def test_prompts_generated_for_any_client():
    """Prompts are returned for all clients regardless of industry."""
    client = {"industry": "Retail", "name": "Test Co"}
    policies = []
    cfg_prompts = [
        {"prompt": "Review GL limits", "priority": "Medium",
         "industry_keywords_high": [], "coverage_lines": []},
    ]
    result = get_risk_review_prompts(client, [], policies, cfg_prompts)
    assert len(result) == 1
    assert result[0]["prompt"] == "Review GL limits"
    assert result[0]["priority"] == "Medium"


def test_industry_keyword_escalates_priority():
    """Industry keyword match in client industry escalates priority to High."""
    client = {"industry": "General Contractor", "name": "Build Co"}
    policies = []
    cfg_prompts = [
        {"prompt": "Check completed ops coverage", "priority": "Low",
         "industry_keywords_high": ["contractor", "construction"],
         "coverage_lines": []},
    ]
    result = get_risk_review_prompts(client, [], policies, cfg_prompts)
    assert result[0]["priority"] == "High"
    assert "contractor" in result[0]["relevance"].lower()


def test_coverage_gap_escalates_priority():
    """Missing required coverage type escalates priority to High."""
    client = {"industry": "Manufacturing", "name": "Acme Mfg"}
    # No cyber policy
    policies = [_policy(uid="POL-GL", policy_type="General Liability")]
    cfg_prompts = [
        {"prompt": "Verify cyber coverage", "priority": "Low",
         "industry_keywords_high": [],
         "coverage_lines": ["Cyber / Tech E&O"]},
    ]
    result = get_risk_review_prompts(client, [], policies, cfg_prompts)
    assert result[0]["priority"] == "High"
    assert "Missing" in result[0]["relevance"]


def test_no_escalation_when_coverage_present():
    """Priority stays as-is when required coverage is present."""
    client = {"industry": "Retail"}
    policies = [_policy(uid="POL-GL", policy_type="General Liability")]
    cfg_prompts = [
        {"prompt": "Verify GL coverage", "priority": "Medium",
         "industry_keywords_high": [],
         "coverage_lines": ["General Liability"]},
    ]
    result = get_risk_review_prompts(client, [], policies, cfg_prompts)
    assert result[0]["priority"] == "Medium"
    assert result[0]["relevance"] == ""


def test_empty_prompts_returns_empty():
    """Empty cfg_prompts returns empty list."""
    result = get_risk_review_prompts({"industry": "Retail"}, [], [], [])
    assert result == []


def test_relevance_field_always_present():
    """Every returned prompt has a 'relevance' field."""
    client = {"industry": "Tech"}
    cfg_prompts = [
        {"prompt": "Check E&O", "priority": "High",
         "industry_keywords_high": [], "coverage_lines": []},
    ]
    result = get_risk_review_prompts(client, [], [], cfg_prompts)
    assert "relevance" in result[0]


# ── Integration helpers ──────────────────────────────────────────────────────

_MIGRATION_SQL = (
    Path(__file__).parent.parent
    / "src" / "policydb" / "migrations" / "066_compliance_requirements.sql"
)


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with minimal supporting tables
    and the compliance migration applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Minimal supporting tables (FK-referenced by migration)
    conn.executescript("""
        CREATE TABLE clients (
            id   INTEGER PRIMARY KEY,
            name TEXT,
            industry_segment TEXT
        );
        CREATE TABLE projects (
            id        INTEGER PRIMARY KEY,
            client_id INTEGER,
            name      TEXT,
            address   TEXT
        );
        CREATE TABLE policies (
            policy_uid    TEXT PRIMARY KEY,
            client_id     INTEGER,
            policy_type   TEXT,
            carrier       TEXT,
            limit_amount  REAL,
            deductible    REAL,
            project_id    INTEGER,
            archived      INTEGER DEFAULT 0,
            policy_number TEXT
        );
        CREATE TABLE client_risks (
            id        INTEGER PRIMARY KEY,
            client_id INTEGER,
            category  TEXT,
            description TEXT,
            severity  TEXT DEFAULT 'Medium'
        );
    """)

    # Run the compliance migration
    conn.executescript(_MIGRATION_SQL.read_text())
    conn.commit()
    return conn


# ── Integration tests ────────────────────────────────────────────────────────

def test_full_compliance_flow_with_db():
    """Full flow: insert requirements across two locations, resolve governing,
    suggest policies, and build the full compliance dataset."""
    conn = _make_db()

    # Client
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'ABC Condos')")

    # Two locations
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (1, 1, 'Oceanview Tower')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (2, 1, 'Bayfront Villa')")

    # Corporate policies (no project_id)
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, policy_type, carrier,
                              limit_amount, deductible, project_id, archived)
        VALUES ('POL-GL', 1, 'General Liability', 'Hartford', 2000000, 5000, NULL, 0)
    """)
    conn.execute("""
        INSERT INTO policies (policy_uid, client_id, policy_type, carrier,
                              limit_amount, deductible, project_id, archived)
        VALUES ('POL-UMB', 1, 'Umbrella', 'Travelers', 10000000, 0, NULL, 0)
    """)

    # Requirement sources
    # Source 1: Management Agreement — client-wide (project_id = NULL)
    conn.execute("""
        INSERT INTO requirement_sources (id, client_id, project_id, name)
        VALUES (1, 1, NULL, 'Management Agreement')
    """)
    # Source 2: Lender Covenant — Oceanview Tower only (project_id = 1)
    conn.execute("""
        INSERT INTO requirement_sources (id, client_id, project_id, name)
        VALUES (2, 1, 1, 'Lender Covenant')
    """)

    # Requirements
    # Source 1: GL $1M — client-wide (project_id IS NULL)
    conn.execute("""
        INSERT INTO coverage_requirements
            (client_id, project_id, source_id, coverage_line,
             required_limit, required_endorsements)
        VALUES (1, NULL, 1, 'General Liability', 1000000, '[]')
    """)
    # Source 2: GL $2M with AI + WOS — Oceanview only (project_id = 1)
    conn.execute("""
        INSERT INTO coverage_requirements
            (client_id, project_id, source_id, coverage_line,
             required_limit, required_endorsements)
        VALUES (1, 1, 2, 'General Liability', 2000000, ?)
    """, (json.dumps(["Additional Insured", "Waiver of Subrogation"]),))
    # Source 2: Property $10M — Oceanview only (project_id = 1)
    conn.execute("""
        INSERT INTO coverage_requirements
            (client_id, project_id, source_id, coverage_line,
             required_limit, required_endorsements)
        VALUES (1, 1, 2, 'Property', 10000000, '[]')
    """)
    conn.commit()

    # ── get_location_requirements ────────────────────────────────────────────

    oceanview_reqs = get_location_requirements(conn, 1, 1)
    # Oceanview inherits the client-wide GL + gets its 2 lender reqs → 3 total
    assert len(oceanview_reqs) == 3, (
        f"Expected 3 requirements for Oceanview, got {len(oceanview_reqs)}"
    )

    bayfront_reqs = get_location_requirements(conn, 1, 2)
    # Bayfront only inherits the client-wide GL → 1 requirement
    assert len(bayfront_reqs) == 1, (
        f"Expected 1 requirement for Bayfront, got {len(bayfront_reqs)}"
    )

    # ── resolve_governing_requirements for Oceanview ─────────────────────────

    gov = resolve_governing_requirements(oceanview_reqs)

    # GL governing limit should be $2M (lender wins over $1M management agreement)
    assert "General Liability" in gov
    assert gov["General Liability"]["required_limit"] == 2_000_000, (
        f"Expected GL governing limit $2M, got {gov['General Liability']['required_limit']}"
    )
    assert gov["General Liability"]["governing_source"] == "Lender Covenant"

    # Endorsements should include both AI and WOS
    gl_endorsements = gov["General Liability"]["required_endorsements"]
    assert "Additional Insured" in gl_endorsements
    assert "Waiver of Subrogation" in gl_endorsements

    # Property requirement should also be present
    assert "Property" in gov
    assert gov["Property"]["required_limit"] == 10_000_000

    # ── suggest_policy_for_requirement ──────────────────────────────────────

    all_policies = [dict(r) for r in conn.execute(
        "SELECT policy_uid, policy_type, carrier, limit_amount, deductible, project_id "
        "FROM policies WHERE client_id=1 AND archived=0"
    ).fetchall()]

    gl_suggestion = suggest_policy_for_requirement(
        gov["General Liability"], all_policies, location_project_id=1
    )
    assert gl_suggestion is not None
    assert gl_suggestion["policy_uid"] == "POL-GL"

    # ── get_client_compliance_data ───────────────────────────────────────────

    data = get_client_compliance_data(conn, 1)

    assert len(data["locations"]) == 2
    assert len(data["sources"]) == 2

    # Overall summary should aggregate governing reqs across both locations
    assert data["overall_summary"]["total"] > 0

    conn.close()


def test_endorsements_stored_as_json():
    """Endorsements round-trip through JSON and are correctly unioned during
    governing resolution."""
    conn = _make_db()

    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Test Client')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (1, 1, 'Site A')")

    # Source with two endorsements
    conn.execute("""
        INSERT INTO requirement_sources (id, client_id, project_id, name)
        VALUES (1, 1, NULL, 'Contract X')
    """)
    endorsements_a = ["Additional Insured", "Primary & Non-Contributory"]
    conn.execute("""
        INSERT INTO coverage_requirements
            (client_id, project_id, source_id, coverage_line,
             required_limit, required_endorsements)
        VALUES (1, NULL, 1, 'General Liability', 1000000, ?)
    """, (json.dumps(endorsements_a),))

    # Second source for same coverage with a different endorsement
    conn.execute("""
        INSERT INTO requirement_sources (id, client_id, project_id, name)
        VALUES (2, 1, NULL, 'Contract Y')
    """)
    endorsements_b = ["Waiver of Subrogation"]
    conn.execute("""
        INSERT INTO coverage_requirements
            (client_id, project_id, source_id, coverage_line,
             required_limit, required_endorsements)
        VALUES (1, NULL, 2, 'General Liability', 500000, ?)
    """, (json.dumps(endorsements_b),))
    conn.commit()

    reqs = get_location_requirements(conn, 1, 1)
    assert len(reqs) == 2

    # Verify _parse_endorsements correctly parses each raw row
    for req in reqs:
        parsed = _parse_endorsements(req["required_endorsements"])
        assert isinstance(parsed, list)

    # Governing resolution should union all three endorsements
    gov = resolve_governing_requirements(reqs)
    unioned = gov["General Liability"]["required_endorsements"]
    assert "Additional Insured" in unioned
    assert "Primary & Non-Contributory" in unioned
    assert "Waiver of Subrogation" in unioned
    assert len(unioned) == 3  # deduplicated

    conn.close()


def test_empty_client_returns_empty_data():
    """A client with no locations, policies, or requirements returns empty
    lists and a zero summary."""
    conn = _make_db()

    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Empty Client')")
    conn.commit()

    data = get_client_compliance_data(conn, 1)

    assert data["locations"] == []
    assert data["client_requirements"] == []
    assert data["sources"] == []
    assert data["all_policies"] == []
    assert data["overall_summary"]["total"] == 0
    assert data["overall_summary"]["compliance_pct"] == 0

    conn.close()


# ── Phase 2-3 Tests ──────────────────────────────────────────────────────────


def test_spawn_requirements_from_risk():
    """spawn_requirements_from_risk creates requirements for each coverage line."""
    conn = _make_db()

    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Test Client')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (1, 1, 'Loc A')")
    conn.execute("CREATE TABLE IF NOT EXISTS risk_coverage_lines (id INTEGER PRIMARY KEY, risk_id INTEGER, coverage_line TEXT, policy_uid TEXT, adequacy TEXT, notes TEXT)")
    conn.execute("INSERT INTO client_risks (id, client_id, category, description) VALUES (1, 1, 'Liability', 'Slip and fall exposure')")
    conn.execute("INSERT INTO risk_coverage_lines (risk_id, coverage_line, adequacy) VALUES (1, 'General Liability', 'Needs Review')")
    conn.execute("INSERT INTO risk_coverage_lines (risk_id, coverage_line, adequacy) VALUES (1, 'Umbrella / Excess', 'Needs Review')")
    conn.commit()

    created = spawn_requirements_from_risk(conn, 1, 1)
    assert len(created) == 2

    reqs = conn.execute("SELECT * FROM coverage_requirements WHERE client_id=1 AND risk_id=1").fetchall()
    assert len(reqs) == 2
    lines = {r["coverage_line"] for r in reqs}
    assert "General Liability" in lines
    assert "Umbrella / Excess" in lines
    assert reqs[0]["compliance_status"] == "Needs Review"
    assert "Slip and fall" in (reqs[0]["notes"] or "")

    # Spawn again — should skip duplicates
    created_again = spawn_requirements_from_risk(conn, 1, 1)
    assert len(created_again) == 0
    conn.close()


def test_spawn_requirements_empty_risk():
    """spawn_requirements_from_risk with no coverage lines returns empty."""
    conn = _make_db()
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Test Client')")
    conn.execute("CREATE TABLE IF NOT EXISTS risk_coverage_lines (id INTEGER PRIMARY KEY, risk_id INTEGER, coverage_line TEXT, policy_uid TEXT, adequacy TEXT, notes TEXT)")
    conn.execute("INSERT INTO client_risks (id, client_id, category, description) VALUES (1, 1, 'Test', 'No lines')")
    conn.commit()

    created = spawn_requirements_from_risk(conn, 1, 1)
    assert created == []
    conn.close()


def test_source_scoping_filters_by_source_project_id():
    """Requirements from a source scoped to Location B don't appear in Location A."""
    conn = _make_db()
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Condo Client')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (1, 1, 'Location A')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (2, 1, 'Location B')")
    conn.execute("INSERT INTO requirement_sources (id, client_id, project_id, name) VALUES (1, 1, 2, 'Lender Covenant for Loc B')")
    conn.execute("INSERT INTO coverage_requirements (client_id, source_id, project_id, coverage_line, required_limit, required_endorsements) VALUES (1, 1, NULL, 'General Liability', 2000000, '[]')")
    conn.commit()

    reqs_a = get_location_requirements(conn, 1, 1)
    assert len(reqs_a) == 0, f"Location A should have 0 reqs but got {len(reqs_a)}"

    reqs_b = get_location_requirements(conn, 1, 2)
    assert len(reqs_b) == 1, f"Location B should have 1 req but got {len(reqs_b)}"
    assert reqs_b[0]["coverage_line"] == "General Liability"
    conn.close()


def test_client_wide_source_inherited_everywhere():
    """Requirements from a client-wide source appear in all locations."""
    conn = _make_db()
    conn.execute("INSERT INTO clients (id, name) VALUES (1, 'Multi-Loc Client')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (1, 1, 'Loc A')")
    conn.execute("INSERT INTO projects (id, client_id, name) VALUES (2, 1, 'Loc B')")
    conn.execute("INSERT INTO requirement_sources (id, client_id, project_id, name) VALUES (1, 1, NULL, 'Master Agreement')")
    conn.execute("INSERT INTO coverage_requirements (client_id, source_id, project_id, coverage_line, required_limit, required_endorsements) VALUES (1, 1, NULL, 'Property', 10000000, '[]')")
    conn.commit()

    reqs_a = get_location_requirements(conn, 1, 1)
    reqs_b = get_location_requirements(conn, 1, 2)
    assert len(reqs_a) == 1
    assert len(reqs_b) == 1
    assert reqs_a[0]["coverage_line"] == "Property"
    conn.close()
