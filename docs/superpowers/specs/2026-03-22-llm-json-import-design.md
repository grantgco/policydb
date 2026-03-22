# LLM JSON Import — Design Spec

**Date:** 2026-03-22
**Status:** Approved

## Overview

A feature that bridges PolicyDB with the user's private GPT 5.2 LLM to extract structured data from insurance documents. PolicyDB generates a context-aware prompt, the user pastes it into GPT 5.2 alongside a document (PDF/image), copies the JSON response back, and PolicyDB parses it to pre-fill existing edit screens.

Two extraction types:
1. **Policy extraction** — from dec pages, binders, certificates of insurance → populates policy edit form
2. **Compliance extraction** — from contracts, loan covenants, lease agreements → populates requirement sources, coverage requirements, and COPE data

## Architecture: Schema-First with Auto-Prompt

A single source of truth: Python-defined schemas with rich field metadata. The prompt generator reads the schema and auto-generates the extraction prompt. Add a field to the schema → the prompt updates automatically. No separate prompt templates to maintain.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Workflow | Copy-paste bridge | GPT 5.2 is private/work LLM, no API access |
| Review model | Preview & edit | JSON pre-fills existing edit screens, user reviews before save |
| UI placement | Contextual per-screen | Buttons on policy edit + compliance pages, not a standalone page |
| Prompt strategy | Context-aware, auto-generated | Embed config lists + client/location context, generated from schema |
| Extraction approach | One-shot | GPT 5.2 is capable enough for full-document extraction in one pass |
| JSON input | Paste textarea | Simple textarea in slideover panel |
| Config values in prompts | Prefer mode | Suggest canonical names, accept unknown values passthrough |
| Architecture | Schema-first | Schema drives both prompt generation and JSON validation/normalization |

## Schema Definitions (`src/policydb/llm_schemas.py`)

### Field Definition Structure

Each field in a schema is a dict with metadata that drives both prompt generation and import validation:

```python
{
    "key": "carrier",                    # JSON key + maps to DB column
    "label": "Insurance Carrier",        # Human-readable name for the prompt
    "type": "string",                    # string | number | date | boolean | array
    "required": True,                    # Whether the prompt marks this as required
    "description": "The insurance company providing coverage",
    "config_values": "carriers",         # Key into cfg.get() — injects allowed values into prompt
    "config_mode": "prefer",             # "prefer" = use if match, accept unknown
                                         # "strict" = must be one of these values
    "normalizer": "normalize_carrier",   # Function name from utils.py/reconciler.py to run on import
    "example": "Travelers"               # Example value for the JSON template in prompt
}
```

### Config Value Modes

- **`prefer`** (default): Prompt says "Use one of these if it matches: [list]. If not found, use the exact name from the document." Unknown values pass through to the edit form where the user sees them in a combobox and can correct or accept.
- **`strict`**: Prompt says "Must be one of: [list]." Used only for closed enumerations like `coverage_form` (Occurrence, Claims-Made, Reporting).

### Schema: Policy Extraction (`POLICY_EXTRACTION_SCHEMA`)

Top-level metadata:
```python
{
    "name": "policy_extraction",
    "description": "Extract policy details from a declaration page, binder, or certificate of insurance",
    "context_fields": ["client_name", "industry"],
    "fields": [...]
}
```

Fields (~25):

| Key | Label | Type | Required | Config Values | Normalizer |
|-----|-------|------|----------|---------------|------------|
| `carrier` | Insurance Carrier | string | yes | `carriers` (prefer) | `normalize_carrier` |
| `policy_type` | Line of Business / Coverage Type | string | yes | `policy_types` (prefer) | `normalize_coverage_type` |
| `policy_number` | Policy Number | string | yes | — | `normalize_policy_number` |
| `effective_date` | Effective Date | date | yes | — | `dateparser.parse` |
| `expiration_date` | Expiration Date | date | yes | — | `dateparser.parse` |
| `premium` | Annual Premium | number | no | — | `parse_currency_with_magnitude` |
| `limit_amount` | Per-Occurrence Limit | number | no | — | `parse_currency_with_magnitude` |
| `deductible` | Deductible | number | no | — | `parse_currency_with_magnitude` |
| `coverage_form` | Coverage Form | string | no | `coverage_forms` (strict) | — |
| `first_named_insured` | First Named Insured | string | no | — | — |
| `description` | Coverage Description / Summary | string | no | — | — |
| `layer_position` | Layer Position | string | no | — | — |
| `commission_rate` | Commission Rate | number | no | — | — |
| `prior_premium` | Prior Term Premium | number | no | — | `parse_currency_with_magnitude` |
| `underwriter_name` | Underwriter Name | string | no | — | — |
| `underwriter_contact` | Underwriter Email or Phone | string | no | — | — |
| `placement_colleague` | Placement Colleague / Broker | string | no | — | — |
| `exposure_address` | Property / Risk Address | string | no | — | — |
| `exposure_city` | City | string | no | — | `format_city` |
| `exposure_state` | State | string | no | — | `format_state` |
| `exposure_zip` | ZIP Code | string | no | — | `format_zip` |
| `exposure_basis` | Exposure Basis | string | no | `exposure_basis_options` (prefer) | — |
| `project_name` | Location / Project Name | string | no | — | — |
| `access_point` | Program / Access Point | string | no | — | — |
| `notes` | Additional Notes | string | no | — | — |

### Schema: Compliance Extraction (`COMPLIANCE_EXTRACTION_SCHEMA`)

Top-level metadata:
```python
{
    "name": "compliance_extraction",
    "description": "Extract insurance requirements from a contract, loan covenant, or lease agreement",
    "context_fields": ["client_name", "location_name", "source_name"],
    "fields": {
        "source": [...],
        "requirements": [...],
        "cope": [...]
    }
}
```

**Source fields:**

| Key | Label | Type | Required | Normalizer |
|-----|-------|------|----------|------------|
| `name` | Document / Agreement Name | string | yes | — |
| `counterparty` | Counterparty | string | yes | — |
| `clause_ref` | Insurance Clause Reference(s) | string | no | — |
| `notes` | Source Notes | string | no | — |

**Requirement fields (array):**

| Key | Label | Type | Required | Config Values | Normalizer |
|-----|-------|------|----------|---------------|------------|
| `coverage_line` | Coverage Type Required | string | yes | `policy_types` (prefer) | `normalize_coverage_type` |
| `required_limit` | Required Limit | number | no | — | `parse_currency_with_magnitude` |
| `max_deductible` | Maximum Deductible Allowed | number | no | — | `parse_currency_with_magnitude` |
| `deductible_type` | Deductible Type | string | no | `deductible_types` (prefer) | — |
| `required_endorsements` | Required Endorsements | array | no | `endorsement_types` (prefer) | — |
| `notes` | Requirement Notes | string | no | — | — |

**COPE fields (optional object):**

| Key | Label | Type | Config Values | Normalizer |
|-----|-------|------|---------------|------------|
| `construction_type` | ISO Construction Type | string | `construction_types` (prefer) | — |
| `year_built` | Year Built | number | — | — |
| `stories` | Number of Stories | number | — | — |
| `sq_footage` | Square Footage | number | — | — |
| `sprinklered` | Sprinklered | string | `sprinkler_options` (strict) | — |
| `roof_type` | Roof Type | string | `roof_types` (prefer) | — |
| `occupancy_description` | Occupancy Description | string | — | — |
| `protection_class` | Protection Class | string | `protection_classes` (prefer) | — |
| `total_insurable_value` | Total Insurable Value (TIV) | number | — | `parse_currency_with_magnitude` |

## Prompt Generator

### Function: `generate_extraction_prompt(schema, context)`

Reads the schema definition and builds a complete prompt string with four sections:

**Section 1 — Role & Task:**
> "You are an insurance document analyst. Extract the following fields from the attached document and return valid JSON."

**Section 2 — Field Instructions:**
Auto-generated from schema fields. Each field becomes a bullet:
- Label + description + type format hint
- Config values in prefer mode: "Prefer one of: [list]. If no match, use exact name from document."
- Config values in strict mode: "Must be one of: [list]."
- Required fields: marked as required
- Optional fields: "Omit if not found in document"

**Section 3 — Context Block:**
Injected from the `context` dict built by the route handler:
- Policy: "Client: ABC Construction, Industry: General Contractor"
- Compliance: "Client: ABC Construction, Location: Project Alpha at 123 Main St, Source: GC Contract with XYZ Owner"

**Section 4 — JSON Template:**
A concrete example object with placeholder values demonstrating the expected structure and formats. For compliance, shows the nested `{source, requirements[], cope}` structure.

### Context Building

Route handlers build the context dict using existing functions:
- `policy_context(conn, policy_uid)` from `email_templates.py` for policy fields
- `client_context(conn, client_id)` for client fields
- `cfg.get("carriers")`, `cfg.get("policy_types")`, etc. for config lists

## JSON Import Pipeline

### Function: `parse_llm_json(raw_text, schema)`

**Step 1 — Extract JSON:**
- Strip markdown code fences (```json ... ```)
- Strip any commentary/text before or after the JSON object
- Find the outermost `{` ... `}` or `[` ... `]`
- Parse with `json.loads()`
- If parse fails, return error with the specific JSON syntax issue

**Step 2 — Validate structure:**
- Check required fields are present
- Collect missing required fields as warnings (not hard errors)
- For compliance: validate `requirements` is an array, `source` is an object

**Step 3 — Normalize per field:**
Walk each field through its declared normalizer:
- `normalize_carrier()` for carrier
- `normalize_coverage_type()` for policy_type / coverage_line
- `parse_currency_with_magnitude()` for all money fields
- `dateparser.parse()` for dates (output as YYYY-MM-DD string)
- `format_state()`, `format_city()`, `format_zip()` for address fields
- Unknown normalizer names are resolved at runtime from `utils.py` and `reconciler.py`
- Fields without normalizers pass through as-is

**Step 4 — Return result:**
```python
{
    "ok": True,
    "parsed": { ... },        # Normalized values ready for form pre-fill
    "warnings": [             # List of warning strings
        "Carrier 'National Specialty Insurance' not in carrier list — verify or add in Settings",
        "Missing required field: expiration_date"
    ],
    "raw": { ... }            # Original values before normalization
}
```

On parse failure:
```python
{
    "ok": False,
    "error": "Invalid JSON: Expecting ',' on line 12",
    "raw_text": "..."         # The text that was submitted
}
```

## UI: Prompt & Paste Slideover Panel

### Trigger
- **Policy page:** "Import from AI" button on policy edit page toolbar
- **Compliance page:** "Import from AI" button on compliance page (near review mode)

### Panel Behavior
- Right-side slideover panel (consistent with existing PolicyDB slideover patterns)
- Two-step flow within the same panel:

**Step 1 — Generate & Copy Prompt:**
- Context block at top showing client name, industry, location (read-only)
- Auto-generated prompt displayed in a dark code block (scrollable, read-only)
- "Copy Prompt to Clipboard" button (full width, primary color)
- Helper text: "Paste this into your AI tool along with the policy document"
- After copy, panel transitions to Step 2

**Step 2 — Paste JSON Response:**
- Textarea (monospace, ~200px height) for pasting JSON
- "Import & Review" primary button + "Cancel" secondary button
- On submit: POST to parse endpoint
- If parse succeeds: panel closes, form/table pre-filled, amber warning banner if warnings
- If parse fails: inline error message below textarea with specific issue, textarea preserved for editing

### Shared Template
Single template `_ai_import_panel.html` used by both policy and compliance pages. The route passes:
- `import_type` ("policy" or "compliance")
- `prompt_text` (the generated prompt)
- `context_display` (dict of context fields to show in the header)
- `parse_url` (the POST endpoint for step 2)

## Integration: Policy Flow

1. User is on `/policies/{uid}/edit`
2. Clicks "Import from AI" → HTMX GET to `/policies/{uid}/ai-import/prompt`
3. Route calls `generate_extraction_prompt(POLICY_EXTRACTION_SCHEMA, context)` where context includes client name, industry, and all config lists
4. Returns slideover panel HTML (step 1) with the prompt
5. User copies prompt, opens GPT 5.2, uploads document, pastes prompt, gets JSON back
6. User pastes JSON into textarea, clicks "Import & Review"
7. HTMX POST to `/policies/{uid}/ai-import/parse` with the raw text
8. Route calls `parse_llm_json(raw_text, POLICY_EXTRACTION_SCHEMA)`
9. On success: returns the policy edit form template pre-filled with `parsed` values + OOB warning banner
10. User reviews pre-filled form, adjusts as needed, saves normally (existing per-field PATCH on blur)

## Integration: Compliance Flow

1. User is on `/compliance/client/{id}` (optionally with a source/location selected)
2. Clicks "Import from AI" → HTMX GET to `/compliance/client/{id}/ai-import/prompt`
3. Route builds context: client name, location name (if selected), source name (if selected), plus config lists
4. Returns slideover panel HTML (step 1)
5. User copies prompt, processes document in GPT 5.2, gets JSON back
6. User pastes JSON, clicks "Import & Review"
7. HTMX POST to `/compliance/client/{id}/ai-import/parse`
8. Route calls `parse_llm_json(raw_text, COMPLIANCE_EXTRACTION_SCHEMA)`
9. On success:
   - If source context exists: update source fields (or show diff if they differ)
   - If no source context: create new requirement source from `source` block
   - Each item in `requirements[]`: insert as `coverage_requirement` row linked to the source
   - If `cope` present and location context exists: upsert COPE data
10. Returns updated compliance page with:
    - Source populated/updated
    - Review mode table filled with extracted requirements
    - COPE panel filled (if data present)
    - Amber warning banner for any issues
11. User reviews and edits inline through existing contenteditable cells

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/policydb/llm_schemas.py` | **New** | Schema definitions, prompt generator, JSON parser |
| `src/policydb/web/routes/policies.py` | Modified | Add `GET /policies/{uid}/ai-import/prompt` and `POST /policies/{uid}/ai-import/parse` |
| `src/policydb/web/routes/compliance.py` | Modified | Add `GET /compliance/client/{id}/ai-import/prompt` and `POST /compliance/client/{id}/ai-import/parse` |
| `src/policydb/web/templates/_ai_import_panel.html` | **New** | Shared slideover panel template (step 1: prompt, step 2: paste) |
| `src/policydb/web/templates/policies/edit.html` | Modified | Add "Import from AI" button to toolbar |
| `src/policydb/web/templates/compliance/index.html` | Modified | Add "Import from AI" button near review mode |

**No new migrations.** All data flows through existing tables via existing save paths.

**No new config keys required.** Schemas reference existing config lists (`carriers`, `policy_types`, `coverage_forms`, etc.).

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Invalid JSON syntax | Inline error below textarea: "Invalid JSON: [specific error]". Textarea preserved. |
| Missing required fields | Warning banner on edit form: "Missing: carrier, expiration_date". Fields left empty for manual entry. |
| Unknown carrier/policy type | Warning banner: "Carrier 'X' not in your list — verify or add in Settings." Value passes through to combobox. |
| Empty JSON / no fields extracted | Error: "No fields were extracted. Check that the JSON contains the expected structure." |
| JSON has extra unexpected fields | Silently ignored. Only schema-defined fields are processed. |
| Compliance JSON missing `requirements` array | Error: "Expected a 'requirements' array in the JSON." |
| Normalizer fails on a value | Warning for that field, raw value passes through. Other fields still processed. |
