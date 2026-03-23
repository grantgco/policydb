# LLM JSON Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a copy-paste bridge between PolicyDB and a private GPT 5.2 LLM that generates extraction prompts from schema definitions and parses JSON responses to pre-fill policy edit and compliance screens.

**Architecture:** Schema-first with auto-prompt. Python-defined schemas with rich field metadata drive both prompt generation and JSON validation/normalization. Two extraction types: policy (dec pages → policy edit form) and compliance (contracts → requirement sources + coverage requirements + COPE data). Contextual UI buttons on existing pages trigger a slideover panel for the copy-paste workflow.

**Tech Stack:** Python 3, FastAPI, Jinja2, HTMX, SQLite, dateparser, existing normalizer functions from `src/policydb/utils.py`

**Spec:** `docs/superpowers/specs/2026-03-22-llm-json-import-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/policydb/llm_schemas.py` | **Create** | Schema definitions (policy + compliance), normalizer registry, prompt generator, JSON parser |
| `tests/test_llm_schemas.py` | **Create** | Unit tests for schemas, prompt generation, JSON parsing, normalization |
| `src/policydb/web/templates/_ai_import_panel.html` | **Create** | Shared slideover panel template (step 1: prompt display, step 2: paste textarea) |
| `src/policydb/web/routes/policies.py` | **Modify** | Add `GET /policies/{uid}/ai-import/prompt` and `POST /policies/{uid}/ai-import/parse` |
| `src/policydb/web/routes/compliance.py` | **Modify** | Add `GET /compliance/client/{id}/ai-import/prompt` and `POST /compliance/client/{id}/ai-import/parse` |
| `src/policydb/web/templates/policies/edit.html` | **Modify** | Add "Import from AI" button to toolbar |
| `src/policydb/web/templates/compliance/index.html` | **Modify** | Add "Import from AI" button near review mode |

---

## Task 1: Schema Definitions and Normalizer Registry

**Files:**
- Create: `src/policydb/llm_schemas.py`
- Create: `tests/test_llm_schemas.py`

This task builds the core data structures — the two extraction schemas and the normalizer registry that maps string names to callable functions.

- [ ] **Step 1: Write failing test for normalizer registry**

```python
# tests/test_llm_schemas.py
import pytest


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policydb.llm_schemas'`

- [ ] **Step 3: Write failing tests for schema structure**

```python
# tests/test_llm_schemas.py (append)

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
    # These five are required per spec
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
```

- [ ] **Step 4: Implement `llm_schemas.py` — schemas and registry**

Create `src/policydb/llm_schemas.py` with:

1. `NORMALIZER_REGISTRY` dict — imports and maps string names to functions from `policydb.utils`:
   - `"normalize_carrier"` → `utils.normalize_carrier` (line 63)
   - `"normalize_coverage_type"` → `utils.normalize_coverage_type` (line 438)
   - `"normalize_policy_number"` → `utils.normalize_policy_number` (line 457)
   - `"parse_currency_with_magnitude"` → `utils.parse_currency_with_magnitude` (line 809)
   - `"format_city"` → `utils.format_city` (line 628)
   - `"format_state"` → `utils.format_state` (line 602)
   - `"format_zip"` → `utils.format_zip` (line 579)
   - `"format_fein"` → `utils.format_fein` (line 714)

2. `POLICY_EXTRACTION_SCHEMA` dict — ~28 fields per the spec table. Each field is a dict with keys: `key`, `label`, `type`, `required`, `description`, `db_column` (optional), `config_values` (optional), `config_mode` (optional, defaults to "prefer"), `normalizer` (optional), `example`.

3. `COMPLIANCE_EXTRACTION_SCHEMA` dict — nested structure with `fields.source` (4 fields), `fields.requirements` (6 fields), `fields.cope` (9 fields) per the spec tables.

Reference the spec field tables exactly for all field definitions. Use the DB column mapping from the spec (e.g., `carrier` → `policies.carrier`, `underwriter_name` → contact system, dates → special-case).

- [ ] **Step 5: Run all tests to verify they pass**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/llm_schemas.py tests/test_llm_schemas.py
git commit -m "feat: LLM import schema definitions and normalizer registry"
```

---

## Task 2: Prompt Generator

**Files:**
- Modify: `src/policydb/llm_schemas.py`
- Modify: `tests/test_llm_schemas.py`

Builds the `generate_extraction_prompt()` function that reads a schema + context and produces the full prompt string.

- [ ] **Step 1: Write failing test for prompt generator — basic structure**

```python
# tests/test_llm_schemas.py (append)

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
    # Section 1: Role
    assert "insurance document analyst" in prompt.lower()
    # Section 2: Field instructions
    assert "Insurance Carrier" in prompt
    assert "Line of Business" in prompt
    # Section 3: Context
    assert "ABC Construction" in prompt
    # Section 4: JSON template
    assert "{" in prompt and "}" in prompt
```

- [ ] **Step 2: Write failing test for config value injection**

```python
# tests/test_llm_schemas.py (append)

def test_generate_prompt_injects_config_values_prefer_mode():
    """Config values in 'prefer' mode inject the list with passthrough instruction."""
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

    # Prefer mode: list + passthrough instruction
    assert "Travelers" in prompt
    assert "Chubb" in prompt
    # Should have passthrough language for prefer fields
    assert "exact name" in prompt.lower() or "as it appears" in prompt.lower()


def test_generate_prompt_injects_config_values_strict_mode():
    """Config values in 'strict' mode inject the list as mandatory options."""
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

    # Strict mode: "Must be one of"
    assert "Occurrence" in prompt
    assert "Claims-Made" in prompt
    assert "must be one of" in prompt.lower()
```

- [ ] **Step 3: Write failing test for JSON template helper**

```python
# tests/test_llm_schemas.py (append)

def test_generate_json_template_returns_valid_json():
    """JSON template helper returns parseable JSON matching schema structure."""
    import json
    from policydb.llm_schemas import generate_json_template, POLICY_EXTRACTION_SCHEMA

    template = generate_json_template(POLICY_EXTRACTION_SCHEMA)
    parsed = json.loads(template)
    assert isinstance(parsed, dict)
    assert "carrier" in parsed
    assert "policy_type" in parsed


def test_generate_json_template_compliance_nested():
    """Compliance JSON template has source, requirements[], cope structure."""
    import json
    from policydb.llm_schemas import generate_json_template, COMPLIANCE_EXTRACTION_SCHEMA

    template = generate_json_template(COMPLIANCE_EXTRACTION_SCHEMA)
    parsed = json.loads(template)
    assert "source" in parsed
    assert "requirements" in parsed
    assert isinstance(parsed["requirements"], list)
```

- [ ] **Step 4: Write failing test for compliance prompt nested structure**

```python
# tests/test_llm_schemas.py (append)

def test_generate_prompt_compliance_nested_json():
    """Compliance prompt should show nested JSON with source, requirements[], cope."""
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
    # Endorsement types should be injected
    assert "Additional Insured" in prompt
```

- [ ] **Step 5: Write failing test for aggregate limit/retention instruction**

```python
# tests/test_llm_schemas.py (append)

def test_generate_prompt_includes_aggregate_retention_instruction():
    """Prompt should instruct LLM to put aggregate limit/retention/SIR in notes."""
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
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -k "generate_prompt or generate_json_template" -v`
Expected: FAIL — `ImportError: cannot import name 'generate_extraction_prompt'`

- [ ] **Step 7: Implement `generate_extraction_prompt(schema, context)` and `generate_json_template(schema)`**

Add to `src/policydb/llm_schemas.py`:

```python
def generate_extraction_prompt(schema: dict, context: dict) -> str:
    """Build a complete extraction prompt from schema definition and context.

    Args:
        schema: A schema dict (POLICY_EXTRACTION_SCHEMA or COMPLIANCE_EXTRACTION_SCHEMA)
        context: Dict with context_fields values + "config_lists" sub-dict mapping
                 config keys to their current value lists.

    Returns:
        Complete prompt string with Role, Fields, Context, and JSON Template sections.
    """
```

Implementation:

**Section 1 — Role:** Static preamble: "You are an insurance document analyst. Extract the following fields from the attached document and return valid JSON only — no commentary, no markdown."

**Section 2 — Field Instructions:** Iterate over schema fields (flat list for policy, nested groups for compliance). For each field:
- `"- {label} ({key}, {type}{', required' if required else ', optional — omit if not found'}): {description}"`
- If `config_values` and the key exists in `context["config_lists"]`:
  - `config_mode == "prefer"`: append `"Prefer one of: [{comma-separated list}]. If no match, use the exact name as it appears in the document."`
  - `config_mode == "strict"`: append `"Must be one of: [{comma-separated list}]."`
- Date fields: append `"Format: YYYY-MM-DD"`
- Number fields: append `"Numeric value only, no currency symbols or commas"`

For policy schema only, add after field list: "If the document lists an aggregate limit, retention, or self-insured retention (SIR), include these values in the notes field."

For compliance schema, group fields under `## Source`, `## Requirements (array)`, `## COPE Data (optional)` headings.

**Section 3 — Context:** Iterate `schema["context_fields"]`, look up each in `context` dict, format as `"Context:\n- Client: {client_name}\n- Industry: {industry}"`. Skip keys not present in context.

**Section 4 — JSON Template:** Build an example JSON object from schema fields using the `example` values. For compliance, nest as `{"source": {...}, "requirements": [{...}], "cope": {...}}`. Wrap in `"Return ONLY valid JSON matching this structure:\n```json\n{json}\n```"`.

Also generate a `json_template` string (just the Section 4 JSON example without the instruction wrapper) and store it accessible — this is returned separately by the route for the "Copy JSON Template" button. Add a helper:

```python
def generate_json_template(schema: dict) -> str:
    """Return just the JSON template example from a schema, for copy-template button."""
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/policydb/llm_schemas.py tests/test_llm_schemas.py
git commit -m "feat: prompt generator — auto-builds extraction prompts from schema definitions"
```

---

## Task 3: JSON Parser

**Files:**
- Modify: `src/policydb/llm_schemas.py`
- Modify: `tests/test_llm_schemas.py`

Builds `parse_llm_json()` — extracts JSON from LLM response text, validates, normalizes.

- [ ] **Step 1: Write failing test for JSON extraction from code fences**

```python
# tests/test_llm_schemas.py (append)

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
```

- [ ] **Step 2: Write failing test for plain JSON (no fences)**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_plain_json():
    """Parser should handle raw JSON without code fences."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Chubb", "policy_type": "Property", "policy_number": "CHB-001", "effective_date": "2026-01-01", "expiration_date": "2027-01-01"}'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert result["parsed"]["carrier"] == "Chubb"
```

- [ ] **Step 3: Write failing test for normalization pipeline**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_normalizes_fields():
    """Parser runs declared normalizers on field values."""
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

    # Currency normalization
    assert p["premium"] == 45000.0
    assert p["limit_amount"] == 1000000.0
    assert p["deductible"] == 5000.0

    # Date normalization — should be YYYY-MM-DD strings
    assert p["effective_date"] == "2026-04-01"
    assert p["expiration_date"] == "2027-04-01"

    # State normalization
    assert p["exposure_state"] == "TX"

    # Policy number normalization (uppercase, trimmed)
    assert p["policy_number"] == "TC-001"
```

- [ ] **Step 4: Write failing test for missing required field warnings**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_warns_on_missing_required():
    """Missing required fields produce warnings, not errors."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Travelers", "policy_type": "GL"}'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True  # Still OK — warnings, not errors
    assert len(result["warnings"]) > 0
    warning_text = " ".join(result["warnings"]).lower()
    assert "policy_number" in warning_text or "effective_date" in warning_text
```

- [ ] **Step 5: Write failing test for invalid JSON error**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_invalid_json():
    """Malformed JSON returns ok=False with error message."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Travelers", "policy_type": }'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is False
    assert "error" in result
    assert len(result["error"]) > 0
```

- [ ] **Step 6: Write failing test for compliance nested parsing**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_compliance_nested():
    """Compliance parser handles source + requirements[] + cope structure."""
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
```

- [ ] **Step 7: Write failing test for normalizer failure graceful handling**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_normalizer_failure_passes_through():
    """If a normalizer throws, the raw value passes through with a warning."""
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
    # The bad date should produce a warning
    assert any("effective_date" in w for w in result["warnings"])
    # Raw value should pass through
    assert result["parsed"]["effective_date"] == "not-a-real-date"
```

- [ ] **Step 8: Write failing test for size limit**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_rejects_oversized_input():
    """Inputs over 500KB are rejected."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    huge = "x" * (500 * 1024 + 1)
    result = parse_llm_json(huge, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is False
    assert "too large" in result["error"].lower()
```

- [ ] **Step 9: Write failing test for empty JSON**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_empty_object():
    """Empty JSON object returns ok=False with 'no fields extracted' error."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    result = parse_llm_json("{}", POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is False
    assert "no fields" in result["error"].lower()
```

- [ ] **Step 10: Write failing test for extra fields ignored**

```python
# tests/test_llm_schemas.py (append)

def test_parse_llm_json_ignores_extra_fields():
    """Fields not in the schema are silently ignored."""
    from policydb.llm_schemas import parse_llm_json, POLICY_EXTRACTION_SCHEMA

    raw = '{"carrier": "Travelers", "policy_type": "GL", "policy_number": "P-1", "effective_date": "2026-01-01", "expiration_date": "2027-01-01", "unknown_field": "should be ignored", "another_extra": 42}'
    result = parse_llm_json(raw, POLICY_EXTRACTION_SCHEMA)
    assert result["ok"] is True
    assert "unknown_field" not in result["parsed"]
    assert "another_extra" not in result["parsed"]
```

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -k "parse_llm_json" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_llm_json'`

- [ ] **Step 12: Implement `parse_llm_json(raw_text, schema)`**

Add to `src/policydb/llm_schemas.py`:

```python
def parse_llm_json(raw_text: str, schema: dict) -> dict:
    """Parse LLM JSON response against a schema, validate and normalize.

    Args:
        raw_text: Raw text from the LLM (may include code fences, commentary)
        schema: The schema dict to validate against

    Returns:
        {"ok": True, "parsed": {...}, "warnings": [...], "raw": {...}}
        or {"ok": False, "error": "...", "raw_text": "..."}
    """
```

Implementation steps:

1. **Size check:** If `len(raw_text) > 500 * 1024`, return `{"ok": False, "error": "Input too large (max 500KB)."}`

2. **Extract JSON:** Try these patterns in order:
   - Regex for ` ```json\n...\n``` ` code fences — extract content between fences
   - Regex for ` ```\n...\n``` ` generic code fences
   - Find outermost `{` and matching `}` (brace counting)
   - If none found, try `json.loads(raw_text.strip())` directly

3. **Parse:** `json.loads(extracted)`. On `JSONDecodeError`, return `{"ok": False, "error": f"Invalid JSON: {e}", "raw_text": raw_text}`

4. **Determine schema type:** If `schema["name"]` starts with `"compliance"`, use nested parsing. Otherwise flat.

5. **Flat parsing (policy):**
   - `raw = {}` (copy of original values)
   - `parsed = {}` (normalized values)
   - `warnings = []`
   - For each field in `schema["fields"]`:
     - If field key not in JSON data: if `required`, add warning. Skip.
     - Store `raw[key] = data[key]`
     - If field has `normalizer`: look up in `NORMALIZER_REGISTRY`, call it, store result in `parsed[key]`. If normalizer throws, add warning, store raw value.
     - If field `type == "date"`: call `dateparser.parse(value)`, format as `YYYY-MM-DD`. If parse returns None, add warning, store raw value.
     - Otherwise: store value as-is in `parsed[key]`

6. **Nested parsing (compliance):**
   - Validate `data` has `source` (dict), `requirements` (list). `cope` is optional.
   - Parse `source` fields against `schema["fields"]["source"]`
   - Parse each item in `requirements` against `schema["fields"]["requirements"]`
   - If `cope` present, parse against `schema["fields"]["cope"]`
   - Return nested structure: `{"source": {...}, "requirements": [...], "cope": {...}}`

7. **Empty check:** If no schema-defined keys were found in the JSON, return `{"ok": False, "error": "No fields were extracted. Check that the JSON contains the expected structure."}`

8. **Return:** `{"ok": True, "parsed": parsed, "warnings": warnings, "raw": raw}`

- [ ] **Step 13: Run all tests to verify they pass**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -v`
Expected: All tests PASS

- [ ] **Step 14: Commit**

```bash
git add src/policydb/llm_schemas.py tests/test_llm_schemas.py
git commit -m "feat: JSON parser — extract, validate, and normalize LLM responses against schemas"
```

---

## Task 4: Slideover Panel Template

**Files:**
- Create: `src/policydb/web/templates/_ai_import_panel.html`

This is the shared Jinja2 template for the two-step slideover panel used on both policy and compliance pages.

- [ ] **Step 1: Create the slideover panel template**

Create `src/policydb/web/templates/_ai_import_panel.html`:

The template receives these context variables from the route:
- `import_type` — "policy" or "compliance"
- `prompt_text` — the full generated prompt string
- `json_template` — just the JSON example (for "Copy Template" button)
- `context_display` — dict of `{label: value}` pairs for the context header
- `parse_url` — the POST URL for step 2

**Structure:**

1. **Backdrop** — `<div>` with `position: fixed; inset: 0; background: rgba(0,0,0,0.3); z-index: 40` and `onclick` to close panel.

2. **Panel** — `<div>` with `position: fixed; top: 0; right: 0; bottom: 0; width: 480px; background: white; z-index: 50; transform: translateX(0); transition: transform 0.2s ease; overflow-y: auto; box-shadow`. On mobile (`max-width: 640px`), `width: 100%`.

3. **Header** — Title ("Import from AI"), close button (X icon, top-right).

4. **Step 1 div** (`id="ai-step-prompt"`):
   - Context block — iterate `context_display` items, show as labeled pills
   - Prompt display — `<pre>` in a dark code block with the prompt text. **Critical Jinja2 safety:** The prompt text is injected via `data-prompt='{{ prompt_text | tojson }}'` on the `<pre>` element, and JavaScript sets `pre.textContent = JSON.parse(pre.dataset.prompt)` on load. This avoids Jinja2 processing `{{` inside the prompt.
   - "Copy Prompt to Clipboard" button — primary, full width. JS: reads from `dataset.prompt`, uses `navigator.clipboard.writeText()` with fallback. On success, button text changes to "Copied!" for 2 seconds, then automatically shows Step 2.
   - "Copy JSON Template Only" link — smaller, secondary. Copies from `data-template` attribute.
   - Helper text: "Paste this into your AI tool along with the document"

5. **Step 2 div** (`id="ai-step-paste"`, hidden initially):
   - "Back to Prompt" link — shows step 1, hides step 2
   - `<textarea>` — monospace, `rows="12"`, `id="ai-json-input"`, placeholder "Paste the JSON response here..."
   - Error display area — `<div id="ai-parse-error">` hidden by default, red border
   - "Import & Review" button — primary. JS: checks size (500KB), POSTs textarea content to `parse_url` via `htmx.ajax('POST', parseUrl, {values: {json_text: textarea.value}, target: ...})`. On success (200): close panel, swap response into page. On error (422): show error message in error div.
   - "Cancel" button — closes panel

6. **JavaScript** — Inline `<script>` at bottom of template:
   - `openAiImport()` / `closeAiImport()` — show/hide panel with transition
   - `copyToClipboard(text, btn)` — clipboard write with fallback
   - `showStep(n)` — toggle between step 1 and step 2
   - Size validation before POST (500KB max)
   - HTMX `afterSwap` handler to close panel on successful parse

All string interpolation uses `data-` attributes + JS, never inline `{{ }}` in script blocks.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/_ai_import_panel.html
git commit -m "feat: AI import slideover panel template — prompt display + JSON paste"
```

---

## Task 5: Policy Import Routes

**Files:**
- Modify: `src/policydb/web/routes/policies.py` (add 2 new endpoints)
- Modify: `tests/test_llm_schemas.py` (add route integration tests)

Adds the prompt generation and parse endpoints for policy extraction.

- [ ] **Step 1: Create test fixtures**

```python
# tests/test_llm_schemas.py (append)
import sqlite3
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_db(tmp_path, monkeypatch):
    """Create a temp DB, initialize it, insert test data, return TestClient."""
    db_path = str(tmp_path / "test.sqlite")
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.web.app.DB_PATH", db_path)

    from policydb.db import init_db
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Insert a test client
    conn.execute(
        "INSERT INTO clients (name, industry_segment, account_exec) VALUES (?, ?, ?)",
        ("Test Construction LLC", "General Contractor", "John Doe"),
    )
    client_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Insert a test policy
    conn.execute(
        "INSERT INTO policies (policy_uid, client_id, policy_type, carrier, effective_date, expiration_date, premium, renewal_status, account_exec) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("POL-TEST-001", client_id, "General Liability", "Travelers", "2026-01-01", "2027-01-01", 25000, "Not Started", "John Doe"),
    )
    conn.commit()
    conn.close()

    from policydb.web.app import app
    yield TestClient(app), client_id, "POL-TEST-001"


@pytest.fixture
def client_with_policy(app_with_db):
    """Return (TestClient, policy_uid) for policy route tests."""
    test_client, client_id, policy_uid = app_with_db
    return test_client, policy_uid


@pytest.fixture
def client_with_compliance(app_with_db):
    """Return (TestClient, client_id) for compliance route tests."""
    test_client, client_id, policy_uid = app_with_db
    return test_client, client_id
```

Note: These fixtures may need adjustment based on the actual `init_db` and `app` import paths. Check `tests/test_tabbed_pages.py` for the correct monkeypatch targets and adapt.

- [ ] **Step 2: Write failing test for prompt endpoint**

```python
# tests/test_llm_schemas.py (append)

def test_policy_prompt_endpoint_returns_panel(client_with_policy):
    """GET /policies/{uid}/ai-import/prompt returns the slideover panel HTML."""
    client, policy_uid = client_with_policy
    resp = client.get(f"/policies/{policy_uid}/ai-import/prompt")
    assert resp.status_code == 200
    html = resp.text
    assert "ai-step-prompt" in html
    assert "Copy Prompt" in html
    assert "data-prompt" in html
```

- [ ] **Step 3: Write failing test for parse endpoint**

```python
# tests/test_llm_schemas.py (append)

def test_policy_parse_endpoint_returns_prefilled_form(client_with_policy):
    """POST /policies/{uid}/ai-import/parse returns pre-filled edit form partial."""
    client, policy_uid = client_with_policy
    json_text = '{"carrier": "Travelers", "policy_type": "General Liability", "policy_number": "TV-GL-001", "effective_date": "2026-04-01", "expiration_date": "2027-04-01", "premium": "45k"}'
    resp = client.post(
        f"/policies/{policy_uid}/ai-import/parse",
        data={"json_text": json_text},
    )
    assert resp.status_code == 200
    html = resp.text
    # Should contain pre-filled values
    assert "Travelers" in html
    assert "General Liability" in html
```

- [ ] **Step 4: Write failing test for parse error handling**

```python
# tests/test_llm_schemas.py (append)

def test_policy_parse_endpoint_returns_error_on_bad_json(client_with_policy):
    """POST with malformed JSON returns 422 with error message."""
    client, policy_uid = client_with_policy
    resp = client.post(
        f"/policies/{policy_uid}/ai-import/parse",
        data={"json_text": "not valid json {"},
    )
    assert resp.status_code == 422
    assert "error" in resp.text.lower() or "invalid" in resp.text.lower()
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -k "policy_prompt_endpoint or policy_parse_endpoint" -v`
Expected: FAIL — 404 error (routes don't exist yet)

- [ ] **Step 6: Implement policy import routes**

Add to `src/policydb/web/routes/policies.py`. These routes use `{policy_uid}` as the first path segment, same as existing `/{policy_uid}/edit`, so FastAPI resolves by the second segment (`ai-import` vs `edit`). Place them near other policy-specific endpoints.

```python
# GET /policies/{policy_uid}/ai-import/prompt
```

Implementation:
1. Fetch policy row and client row from DB
2. Build context dict:
   - `client_name` from client row
   - `industry` from client row
   - `config_lists` — call `cfg.get()` for each config key referenced by `POLICY_EXTRACTION_SCHEMA` fields that have `config_values`
3. Call `generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)`
4. Call `generate_json_template(POLICY_EXTRACTION_SCHEMA)`
5. Build `context_display` dict: `{"Client": client_name, "Industry": industry}`
6. Return `_ai_import_panel.html` template with `import_type="policy"`, `prompt_text`, `json_template`, `context_display`, `parse_url=f"/policies/{policy_uid}/ai-import/parse"`

```python
# POST /policies/{policy_uid}/ai-import/parse
```

**Critical implementation detail — how prefill works:**

The parse route must build the FULL template context that the Details tab needs (same as `policy_tab_details` at line ~967), then overlay parsed values onto the policy dict. Specifically:

1. Get `json_text` from form data
2. Call `parse_llm_json(json_text, POLICY_EXTRACTION_SCHEMA)`
3. If `result["ok"]` is False: return 422 response with error HTML (the step 2 error div content)
4. If OK:
   a. Fetch the existing policy row as a mutable dict: `policy = dict(row)`
   b. Overlay parsed values: `for k, v in result["parsed"].items(): policy[k] = v`
   c. Build the FULL template context needed by `_tab_details.html` — reuse the same helper function or inline the context-building logic from `policy_tab_details`. This includes: `policy` (the merged dict), `policy_types` from config, `coverage_forms` from config, `renewal_statuses` from config, `us_states`, `opportunity_statuses`, `tower_layers`, `cycle_labels`, `program_linked_policies`, `linkable_policies`, `program_carrier_rows`. The simplest approach: extract the context-building into a shared helper `_policy_detail_context(conn, policy_dict)` used by both `policy_tab_details` and the parse route.
   d. Add `ai_warnings = result["warnings"]` to the context
   e. Return the `_tab_details.html` partial with this context, plus an OOB `<div id="ai-import-warnings" hx-swap-oob="true">` containing warning pills

5. **FEIN cross-reference:** If `result["parsed"]` contains `fein`, look up the client's existing FEIN. If both exist and differ, add a warning: "FEIN '{extracted}' differs from client record '{existing}' — verify."

**The HTMX target for the parse button** in `_ai_import_panel.html` should be `#tab-details-content` (or whatever the Details tab's content container ID is — check the actual template). The JS in the panel should also close the slideover after a successful swap.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/policies.py tests/test_llm_schemas.py
git commit -m "feat: policy AI import routes — prompt generation and JSON parse endpoints"
```

---

## Task 6: Policy Edit Template Button

**Files:**
- Modify: `src/policydb/web/templates/policies/edit.html`

Adds the "Import from AI" trigger button to the policy edit page toolbar.

- [ ] **Step 1: Add the button and panel include to the policy edit template**

Find the toolbar/action area in `policies/edit.html`. Add an "Import from AI" button that triggers an HTMX GET to `/policies/{{ policy.policy_uid }}/ai-import/prompt`. The response loads into a panel container div.

Add to the template:
1. A button in the toolbar area: `<button hx-get="/policies/{{ policy.policy_uid }}/ai-import/prompt" hx-target="#ai-import-container" hx-swap="innerHTML" class="...">Import from AI</button>` styled as a secondary/outline button with an icon.
2. An empty container div at the bottom of the page: `<div id="ai-import-container"></div>` — the slideover panel HTML loads here.
3. A warnings banner placeholder: `<div id="ai-import-warnings"></div>` — positioned at the top of the form area, receives OOB swaps from the parse response.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/policies/edit.html
git commit -m "feat: add 'Import from AI' button to policy edit page toolbar"
```

---

## Task 7: Compliance Import Routes

**Files:**
- Modify: `src/policydb/web/routes/compliance.py` (add 2 new endpoints)
- Modify: `tests/test_llm_schemas.py` (add compliance route tests)

Adds prompt generation and parse endpoints for compliance extraction.

- [ ] **Step 1: Write failing test for compliance prompt endpoint**

```python
# tests/test_llm_schemas.py (append)

def test_compliance_prompt_endpoint_returns_panel(client_with_compliance):
    """GET /compliance/client/{id}/ai-import/prompt returns the panel."""
    client, client_id = client_with_compliance
    resp = client.get(f"/compliance/client/{client_id}/ai-import/prompt")
    assert resp.status_code == 200
    html = resp.text
    assert "ai-step-prompt" in html
    assert "requirements" in html.lower()
```

Uses the `client_with_compliance` fixture defined in Task 5 Step 1.

- [ ] **Step 2: Write failing test for compliance parse endpoint**

```python
# tests/test_llm_schemas.py (append)

def test_compliance_parse_endpoint_returns_prefilled_rows(client_with_compliance):
    """POST /compliance/client/{id}/ai-import/parse returns pre-filled review mode rows."""
    client, client_id = client_with_compliance
    json_text = '''{
        "source": {"name": "GC Contract", "counterparty": "Owner LLC"},
        "requirements": [
            {"coverage_line": "General Liability", "required_limit": "2m"},
            {"coverage_line": "Workers Compensation"}
        ]
    }'''
    resp = client.post(
        f"/compliance/client/{client_id}/ai-import/parse",
        data={"json_text": json_text},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "General Liability" in html
    assert "Workers Compensation" in html
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -k "compliance_prompt_endpoint or compliance_parse_endpoint" -v`
Expected: FAIL

- [ ] **Step 4: Implement compliance import routes**

Add to `src/policydb/web/routes/compliance.py`:

Both routes use `/compliance/client/{client_id}/ai-import/...` — FastAPI resolves by the sub-path, no ordering concerns.

```python
# GET /compliance/client/{client_id}/ai-import/prompt
```

Implementation:
1. Fetch client row from DB
2. Optionally read `source_id` and `project_id` from query params
3. Build context dict:
   - `client_name` from client row
   - `location_name` from project row if `project_id` provided
   - `source_name` from source row if `source_id` provided
   - `config_lists` — `policy_types`, `deductible_types`, `endorsement_types`, `construction_types`, `sprinkler_options`, `roof_types`, `protection_classes` from cfg
4. Call `generate_extraction_prompt(COMPLIANCE_EXTRACTION_SCHEMA, context)`
5. Call `generate_json_template(COMPLIANCE_EXTRACTION_SCHEMA)`
6. Return `_ai_import_panel.html` with `import_type="compliance"`, `parse_url` including query params

```python
# POST /compliance/client/{client_id}/ai-import/parse
```

**Critical architectural decision — compliance creates DB rows on parse:**

Unlike the policy flow (which pre-fills an existing form), the compliance parse endpoint creates real DB rows. This is necessary because the review mode table's contenteditable cells rely on `req.id` for their PATCH endpoints, and `initMatrix()` needs `data-req-id` on every row. Rows without DB IDs would break all existing cell-save infrastructure.

**This is acceptable because:**
- Requirements are created with `compliance_status='Needs Review'` — visually flagged as unreviewed
- The user reviews and edits each row using existing contenteditable PATCH pattern
- Deleting unwanted rows uses the existing delete endpoint
- This matches the existing "Add Row" behavior which also immediately inserts a blank DB row

**Implementation:**
1. Get `json_text` from form data, plus `source_id` and `project_id` from query params
2. Call `parse_llm_json(json_text, COMPLIANCE_EXTRACTION_SCHEMA)`
3. If not OK: return 422 with error HTML
4. If OK:
   a. **Source:** If `source_id` provided, update the existing source's fields from `parsed["source"]`. If not provided, INSERT a new `requirement_sources` row with `client_id` from URL, `project_id` from query param, and extracted fields. Get the resulting `source_id`.
   b. **Requirements:** For each item in `parsed["requirements"]`, INSERT a `coverage_requirements` row with `client_id`, `project_id`, `source_id`, and all extracted/normalized fields. Set `compliance_status='Needs Review'`.
   c. **COPE:** If `parsed["cope"]` present and `project_id` provided, UPSERT into `cope_data`.
   d. **Response:** Return a re-rendered review mode panel (`_review_mode.html`) targeting the review mode container, filtered to the source. Include OOB warnings banner.
5. User reviews rows in existing contenteditable table, edits as needed (blur → PATCH), deletes incorrect rows via existing delete endpoint.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/test_llm_schemas.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/routes/compliance.py tests/test_llm_schemas.py
git commit -m "feat: compliance AI import routes — prompt generation and JSON parse with pre-fill"
```

---

## Task 8: Compliance Template Button

**Files:**
- Modify: `src/policydb/web/templates/compliance/index.html`

Adds the "Import from AI" trigger button to the compliance page.

- [ ] **Step 1: Add button and container to compliance template**

Find the area near the "Review Mode" section in `compliance/index.html`. Add:

1. An "Import from AI" button next to or near the review mode controls. The button triggers HTMX GET to `/compliance/client/{{ client_id }}/ai-import/prompt` with query params for current `source_id` and `project_id` if selected. Target: `#ai-import-container`.
2. Empty container div: `<div id="ai-import-container"></div>` at bottom of page.
3. Warnings banner: `<div id="ai-import-warnings"></div>` at top of compliance content area.

The button should pass context via `hx-vals` or URL query params so the prompt endpoint knows which source and location are currently selected.

- [ ] **Step 2: Commit**

```bash
git add src/policydb/web/templates/compliance/index.html
git commit -m "feat: add 'Import from AI' button to compliance page"
```

---

## Task 9: QA Testing

**Files:** None (verification only)

End-to-end QA using the browser to verify the full workflow.

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/grantgreeson/Documents/Projects/policydb && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Start the server and test policy import flow**

Start: `cd /Users/grantgreeson/Documents/Projects/policydb && policydb serve`

Test flow:
1. Navigate to any policy edit page
2. Click "Import from AI" — verify slideover panel opens
3. Verify prompt text is displayed with context (client name, industry, carrier list)
4. Click "Copy Prompt" — verify clipboard contains the full prompt
5. Click through to Step 2 — verify textarea appears
6. Paste sample JSON:
   ```json
   {"carrier": "Travelers", "policy_type": "General Liability", "policy_number": "TV-GL-2026", "effective_date": "2026-04-01", "expiration_date": "2027-04-01", "premium": "45k", "limit_amount": "1m", "deductible": "5000"}
   ```
7. Click "Import & Review" — verify panel closes and form fields are pre-filled
8. Verify $45,000 in premium, $1,000,000 in limit, $5,000 in deductible (normalized)
9. Test error case: paste invalid JSON, verify error message appears inline

- [ ] **Step 3: Test compliance import flow**

1. Navigate to a client's compliance page
2. Click "Import from AI" — verify panel opens with compliance-specific prompt
3. Verify prompt mentions "requirements", "source", "cope"
4. Paste sample compliance JSON:
   ```json
   {"source": {"name": "GC Contract", "counterparty": "Owner LLC", "clause_ref": "Article 11"}, "requirements": [{"coverage_line": "General Liability", "required_limit": "2m", "max_deductible": "10k", "required_endorsements": ["Additional Insured"]}, {"coverage_line": "Workers Compensation", "notes": "Statutory"}], "cope": {"construction_type": "Type II", "sq_footage": 85000, "sprinklered": "Yes"}}
   ```
5. Click "Import & Review" — verify source form, requirements rows, and COPE panel are pre-filled
6. Verify coverage types are normalized, currency values formatted

- [ ] **Step 4: Test edge cases**

1. Test "Copy JSON Template Only" link — verify just the JSON structure is copied
2. Test "Back to Prompt" link on step 2 — verify returns to step 1 without losing textarea
3. Test with empty JSON `{}` — verify warnings for missing required fields
4. Test oversized input (paste >500KB) — verify error message
5. Test panel close via backdrop click and Escape key
6. Test on narrow viewport — verify panel goes full-width on mobile

- [ ] **Step 5: Take screenshots and document results**

Use the Chrome plugin to screenshot each step of the flow for verification. Fix any visual issues found.

- [ ] **Step 6: Final commit if any fixes were needed**

Stage only the specific files that were fixed (do NOT use `git add -A`):
```bash
git add <specific files that were fixed>
git commit -m "fix: QA fixes for AI import feature"
```
