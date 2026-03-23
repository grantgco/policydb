"""Tests for LLM extraction schema definitions and normalizer registry."""

import pytest


# ---------------------------------------------------------------------------
# Normalizer Registry
# ---------------------------------------------------------------------------


def test_normalizer_registry_has_known_functions():
    """Registry maps string names to callable normalizer functions."""
    from policydb.llm_schemas import NORMALIZER_REGISTRY

    assert callable(NORMALIZER_REGISTRY["normalize_carrier"])
    assert callable(NORMALIZER_REGISTRY["normalize_coverage_type"])
    assert callable(NORMALIZER_REGISTRY["normalize_policy_number"])
    assert callable(NORMALIZER_REGISTRY["parse_currency_with_magnitude"])
    assert callable(NORMALIZER_REGISTRY["format_city"])
    assert callable(NORMALIZER_REGISTRY["format_state"])
    assert callable(NORMALIZER_REGISTRY["format_zip"])
    assert callable(NORMALIZER_REGISTRY["format_fein"])


def test_normalizer_registry_excludes_date():
    """Date normalization is special-cased, not in the registry."""
    from policydb.llm_schemas import NORMALIZER_REGISTRY

    assert "dateparser.parse" not in NORMALIZER_REGISTRY


# ---------------------------------------------------------------------------
# Policy Extraction Schema — metadata
# ---------------------------------------------------------------------------


def test_policy_schema_has_required_metadata():
    from policydb.llm_schemas import POLICY_EXTRACTION_SCHEMA

    assert POLICY_EXTRACTION_SCHEMA["name"] == "policy_extraction"
    assert POLICY_EXTRACTION_SCHEMA["version"] == 1
    assert "description" in POLICY_EXTRACTION_SCHEMA
    assert "context_fields" in POLICY_EXTRACTION_SCHEMA
    assert "fields" in POLICY_EXTRACTION_SCHEMA
    assert isinstance(POLICY_EXTRACTION_SCHEMA["fields"], list)


def test_policy_schema_required_fields():
    from policydb.llm_schemas import POLICY_EXTRACTION_SCHEMA

    fields = {f["key"]: f for f in POLICY_EXTRACTION_SCHEMA["fields"]}
    for key in ["carrier", "policy_type", "policy_number", "effective_date", "expiration_date"]:
        assert key in fields, f"Missing required field: {key}"
        assert fields[key]["required"] is True, f"{key} should be required"


def test_policy_schema_field_structure():
    from policydb.llm_schemas import POLICY_EXTRACTION_SCHEMA

    carrier = next(f for f in POLICY_EXTRACTION_SCHEMA["fields"] if f["key"] == "carrier")
    assert carrier["label"] == "Insurance Carrier"
    assert carrier["type"] == "string"
    assert carrier["config_values"] == "carriers"
    assert carrier["config_mode"] == "prefer"
    assert carrier["normalizer"] == "normalize_carrier"
    assert "example" in carrier


# ---------------------------------------------------------------------------
# Compliance Extraction Schema
# ---------------------------------------------------------------------------


def test_compliance_schema_has_nested_structure():
    from policydb.llm_schemas import COMPLIANCE_EXTRACTION_SCHEMA

    assert COMPLIANCE_EXTRACTION_SCHEMA["name"] == "compliance_extraction"
    assert COMPLIANCE_EXTRACTION_SCHEMA["version"] == 1
    fields = COMPLIANCE_EXTRACTION_SCHEMA["fields"]
    assert "source" in fields
    assert "requirements" in fields
    assert "cope" in fields
    assert isinstance(fields["source"], list)
    assert isinstance(fields["requirements"], list)
    assert isinstance(fields["cope"], list)


def test_compliance_schema_requirement_fields():
    from policydb.llm_schemas import COMPLIANCE_EXTRACTION_SCHEMA

    req_fields = {f["key"]: f for f in COMPLIANCE_EXTRACTION_SCHEMA["fields"]["requirements"]}
    assert "coverage_line" in req_fields
    assert req_fields["coverage_line"]["required"] is True
    assert "required_limit" in req_fields
    assert "max_deductible" in req_fields
    assert "required_endorsements" in req_fields
    assert req_fields["required_endorsements"]["type"] == "array"


# ---------------------------------------------------------------------------
# Prompt Generator
# ---------------------------------------------------------------------------


def test_generate_prompt_has_four_sections():
    """Prompt should have Role, Fields, Context, and JSON Template sections."""
    from policydb.llm_schemas import generate_extraction_prompt, POLICY_EXTRACTION_SCHEMA

    context = {
        "client_name": "ABC Construction",
        "industry": "General Contractor",
        "config_lists": {
            "carriers": ["Travelers", "Chubb", "Hartford"],
            "policy_types": ["General Liability", "Property"],
            "coverage_forms": ["Occurrence", "Claims-Made", "Reporting"],
        },
    }
    prompt = generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)

    assert isinstance(prompt, str)
    assert len(prompt) > 100
    assert "insurance document analyst" in prompt.lower()
    assert "Insurance Carrier" in prompt
    assert "Line of Business" in prompt
    assert "ABC Construction" in prompt
    assert "{" in prompt and "}" in prompt


def test_generate_prompt_injects_config_values_prefer_mode():
    from policydb.llm_schemas import generate_extraction_prompt, POLICY_EXTRACTION_SCHEMA

    context = {
        "client_name": "Test Client",
        "industry": "Construction",
        "config_lists": {
            "carriers": ["Travelers", "Chubb"],
            "policy_types": ["General Liability"],
            "coverage_forms": ["Occurrence", "Claims-Made", "Reporting"],
        },
    }
    prompt = generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)
    assert "Travelers" in prompt
    assert "Chubb" in prompt
    assert "exact name" in prompt.lower() or "as it appears" in prompt.lower()


def test_generate_prompt_injects_config_values_strict_mode():
    from policydb.llm_schemas import generate_extraction_prompt, POLICY_EXTRACTION_SCHEMA

    context = {
        "client_name": "Test Client",
        "industry": "Construction",
        "config_lists": {
            "carriers": [],
            "policy_types": [],
            "coverage_forms": ["Occurrence", "Claims-Made", "Reporting"],
        },
    }
    prompt = generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)
    assert "Occurrence" in prompt
    assert "Claims-Made" in prompt
    assert "must be one of" in prompt.lower()


def test_generate_json_template_returns_valid_json():
    import json
    from policydb.llm_schemas import generate_json_template, POLICY_EXTRACTION_SCHEMA

    template = generate_json_template(POLICY_EXTRACTION_SCHEMA)
    parsed = json.loads(template)
    assert isinstance(parsed, dict)
    assert "carrier" in parsed
    assert "policy_type" in parsed


def test_generate_json_template_compliance_nested():
    import json
    from policydb.llm_schemas import generate_json_template, COMPLIANCE_EXTRACTION_SCHEMA

    template = generate_json_template(COMPLIANCE_EXTRACTION_SCHEMA)
    parsed = json.loads(template)
    assert "source" in parsed
    assert "requirements" in parsed
    assert isinstance(parsed["requirements"], list)


def test_generate_prompt_compliance_nested_json():
    from policydb.llm_schemas import generate_extraction_prompt, COMPLIANCE_EXTRACTION_SCHEMA

    context = {
        "client_name": "ABC Construction",
        "location_name": "Project Alpha",
        "source_name": "GC Contract",
        "config_lists": {
            "policy_types": ["General Liability", "Workers Compensation"],
            "deductible_types": ["Per Occurrence", "Aggregate"],
            "endorsement_types": ["Additional Insured", "Waiver of Subrogation"],
            "construction_types": ["Type I", "Type II"],
            "sprinkler_options": ["Yes", "No", "Unknown"],
            "roof_types": ["Built-Up"],
            "protection_classes": ["1", "2"],
        },
    }
    prompt = generate_extraction_prompt(COMPLIANCE_EXTRACTION_SCHEMA, context)

    assert "source" in prompt
    assert "requirements" in prompt
    assert "cope" in prompt
    assert "Project Alpha" in prompt
    assert "GC Contract" in prompt
    assert "Additional Insured" in prompt


def test_generate_prompt_includes_aggregate_retention_instruction():
    from policydb.llm_schemas import generate_extraction_prompt, POLICY_EXTRACTION_SCHEMA

    context = {
        "client_name": "Test",
        "industry": "Test",
        "config_lists": {"carriers": [], "policy_types": [], "coverage_forms": []},
    }
    prompt = generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)
    assert "aggregate" in prompt.lower()
    assert "retention" in prompt.lower() or "sir" in prompt.lower()
    assert "notes" in prompt.lower()
