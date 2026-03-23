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


# ---------------------------------------------------------------------------
# JSON Parser — parse_llm_json()
# ---------------------------------------------------------------------------


def test_parse_llm_json_strips_code_fences():
    """Parser should extract JSON from markdown code fences."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '''Here's the extracted data:

```json
{
    "carrier": "Travelers",
    "policy_type": "General Liability",
    "policy_number": "TC-GL-2026-001",
    "effective_date": "2026-04-01",
    "expiration_date": "2027-04-01",
    "premium": 45000
}
```

Let me know if you need anything else!'''

    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert result["parsed"]["carrier"] == "Travelers"
    assert result["parsed"]["premium"] == 45000.0


def test_parse_llm_json_plain_json():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Chubb", "policy_type": "Property", "policy_number": "CHB-001", "effective_date": "2026-01-01", "expiration_date": "2027-01-01"}'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert result["parsed"]["carrier"] == "Chubb"


def test_parse_llm_json_normalizes_fields():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '''{
        "carrier": "travelers",
        "policy_type": "gl",
        "policy_number": "  tc-001  ",
        "effective_date": "April 1, 2026",
        "expiration_date": "April 1, 2027",
        "premium": "45k",
        "limit_amount": "1m",
        "deductible": "$5,000",
        "exposure_state": "texas"
    }'''
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    p = result["parsed"]
    assert p["premium"] == 45000.0
    assert p["limit_amount"] == 1000000.0
    assert p["deductible"] == 5000.0
    assert p["effective_date"] == "2026-04-01"
    assert p["expiration_date"] == "2027-04-01"
    assert p["exposure_state"] == "TX"
    assert p["policy_number"] == "TC-001"


def test_parse_llm_json_warns_on_missing_required():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Travelers", "policy_type": "GL"}'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert len(result["warnings"]) > 0
    warning_text = " ".join(result["warnings"]).lower()
    assert "policy_number" in warning_text or "effective_date" in warning_text


def test_parse_llm_json_invalid_json():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Travelers", "policy_type": }'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is False
    assert "error" in result
    assert len(result["error"]) > 0


def test_parse_llm_json_compliance_nested():
    from policydb.llm_schemas import parse_llm_json, COMPLIANCE_EXTRACTION_SCHEMA

    raw = '''{
        "source": {
            "name": "General Contract",
            "counterparty": "XYZ Development"
        },
        "requirements": [
            {
                "coverage_line": "gl",
                "required_limit": "2m",
                "max_deductible": "10k",
                "required_endorsements": ["Additional Insured", "Waiver of Subrogation"]
            },
            {
                "coverage_line": "workers comp",
                "notes": "Statutory limits"
            }
        ],
        "cope": {
            "construction_type": "Type II",
            "sq_footage": 85000,
            "sprinklered": "Yes"
        }
    }'''
    result = parse_llm_json(raw, COMPLIANCE_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    p = result["parsed"]
    assert p["source"]["name"] == "General Contract"
    assert len(p["requirements"]) == 2
    assert p["requirements"][0]["required_limit"] == 2000000.0
    assert p["requirements"][0]["max_deductible"] == 10000.0
    assert p["requirements"][0]["required_endorsements"] == ["Additional Insured", "Waiver of Subrogation"]
    assert p["cope"]["sq_footage"] == 85000
    assert p["cope"]["sprinklered"] == "Yes"


def test_parse_llm_json_normalizer_failure_passes_through():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '''{
        "carrier": "Valid Carrier",
        "policy_type": "GL",
        "policy_number": "P-001",
        "effective_date": "not-a-real-date",
        "expiration_date": "2027-01-01"
    }'''
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert any("effective_date" in w for w in result["warnings"])
    assert result["parsed"]["effective_date"] == "not-a-real-date"


def test_parse_llm_json_rejects_oversized_input():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    huge = "x" * (500 * 1024 + 1)
    result = parse_llm_json(huge, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is False
    assert "too large" in result["error"].lower()


def test_parse_llm_json_empty_object():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    result = parse_llm_json("{}", POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is False
    assert "no fields" in result["error"].lower()


def test_parse_llm_json_ignores_extra_fields():
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Travelers", "policy_type": "GL", "policy_number": "P-1", "effective_date": "2026-01-01", "expiration_date": "2027-01-01", "unknown_field": "should be ignored", "another_extra": 42}'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert "unknown_field" not in result["parsed"]
    assert "another_extra" not in result["parsed"]
