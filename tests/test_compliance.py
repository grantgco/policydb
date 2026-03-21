"""Tests for the compliance engine."""

from policydb.compliance import (
    resolve_governing_requirements,
    suggest_policy_for_requirement,
    compute_compliance_summary,
    get_risk_review_prompts,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _req(coverage_line="General Liability", required_limit=1_000_000,
         max_deductible=None, deductible_type=None,
         ai_required=0, wos_required=0, primary_noncontrib=0,
         per_project_aggregate=0, noc_required=0, completed_ops_required=0,
         professional_liability_required=0, pollution_required=0,
         cyber_required=0, builders_risk_required=0,
         source_id=None, source_name="Contract A",
         project_id=None, client_id=1, id=1, risk_id=None,
         compliance_status="Needs Review", linked_policy_uid=None,
         notes=None):
    return {
        "id": id, "client_id": client_id, "project_id": project_id,
        "risk_id": risk_id, "source_id": source_id, "source_name": source_name,
        "coverage_line": coverage_line, "required_limit": required_limit,
        "max_deductible": max_deductible, "deductible_type": deductible_type,
        "ai_required": ai_required, "wos_required": wos_required,
        "primary_noncontrib": primary_noncontrib,
        "per_project_aggregate": per_project_aggregate,
        "noc_required": noc_required,
        "completed_ops_required": completed_ops_required,
        "professional_liability_required": professional_liability_required,
        "pollution_required": pollution_required,
        "cyber_required": cyber_required,
        "builders_risk_required": builders_risk_required,
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


def test_endorsement_flags_or_across_sources():
    """If ANY source requires an endorsement, it's required."""
    reqs = [
        _req(id=1, coverage_line="GL", ai_required=1, wos_required=0,
             source_name="A"),
        _req(id=2, coverage_line="GL", ai_required=0, wos_required=1,
             source_name="B"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["GL"]["ai_required"] == 1
    assert gov["GL"]["wos_required"] == 1


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


def test_all_endorsement_flags_propagate():
    """All endorsement flags OR correctly across sources."""
    reqs = [
        _req(id=1, coverage_line="GL", noc_required=1, source_name="A"),
        _req(id=2, coverage_line="GL", completed_ops_required=1, source_name="B"),
        _req(id=3, coverage_line="GL", per_project_aggregate=1, source_name="C"),
    ]
    gov = resolve_governing_requirements(reqs)
    assert gov["GL"]["noc_required"] == 1
    assert gov["GL"]["completed_ops_required"] == 1
    assert gov["GL"]["per_project_aggregate"] == 1


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
