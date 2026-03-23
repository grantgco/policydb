"""LLM extraction schema definitions and normalizer registry.

Defines the structured schemas that drive LLM-based document import:
- POLICY_EXTRACTION_SCHEMA: flat field list for policy/certificate parsing
- COMPLIANCE_EXTRACTION_SCHEMA: nested schema for contract requirement extraction
- NORMALIZER_REGISTRY: maps string names to callable post-processing functions
- generate_extraction_prompt(): builds a complete LLM extraction prompt from schema + context
- generate_json_template(): returns the JSON template portion for a schema

Date normalization is intentionally excluded from the registry — the parser
special-cases fields with type == "date" and uses dateparser directly.
"""

import json

from policydb.utils import (
    format_city,
    format_fein,
    format_state,
    format_zip,
    normalize_carrier,
    normalize_coverage_type,
    normalize_policy_number,
    parse_currency_with_magnitude,
)

# ---------------------------------------------------------------------------
# Normalizer Registry
# ---------------------------------------------------------------------------

NORMALIZER_REGISTRY: dict[str, callable] = {
    "normalize_carrier": normalize_carrier,
    "normalize_coverage_type": normalize_coverage_type,
    "normalize_policy_number": normalize_policy_number,
    "parse_currency_with_magnitude": parse_currency_with_magnitude,
    "format_city": format_city,
    "format_state": format_state,
    "format_zip": format_zip,
    "format_fein": format_fein,
}

# ---------------------------------------------------------------------------
# Policy Extraction Schema
# ---------------------------------------------------------------------------

POLICY_EXTRACTION_SCHEMA: dict = {
    "name": "policy_extraction",
    "version": 1,
    "description": (
        "Extract policy details from a certificate of insurance, "
        "declaration page, binder, or quote document"
    ),
    "context_fields": ["client_name", "industry"],
    "fields": [
        # --- Required fields ---
        {
            "key": "carrier",
            "label": "Insurance Carrier",
            "type": "string",
            "required": True,
            "description": "The insurance company or underwriting entity",
            "config_values": "carriers",
            "config_mode": "prefer",
            "normalizer": "normalize_carrier",
            "example": "Travelers",
        },
        {
            "key": "policy_type",
            "label": "Line of Business / Coverage Type",
            "type": "string",
            "required": True,
            "description": "The type of insurance coverage (e.g. General Liability, Workers Comp)",
            "config_values": "policy_types",
            "config_mode": "prefer",
            "normalizer": "normalize_coverage_type",
            "example": "General Liability",
        },
        {
            "key": "policy_number",
            "label": "Policy Number",
            "type": "string",
            "required": True,
            "description": "The unique policy identifier assigned by the carrier",
            "normalizer": "normalize_policy_number",
            "example": "TC-GL-2026-001",
        },
        {
            "key": "effective_date",
            "label": "Effective Date",
            "type": "date",
            "required": True,
            "description": "Policy inception / effective date",
            "example": "2026-04-01",
        },
        {
            "key": "expiration_date",
            "label": "Expiration Date",
            "type": "date",
            "required": True,
            "description": "Policy expiration date",
            "example": "2027-04-01",
        },
        # --- Optional fields ---
        {
            "key": "premium",
            "label": "Annual Premium",
            "type": "number",
            "required": False,
            "description": "Total annual premium amount",
            "normalizer": "parse_currency_with_magnitude",
            "example": "45000",
        },
        {
            "key": "limit_amount",
            "label": "Per-Occurrence Limit",
            "type": "number",
            "required": False,
            "description": "Per-occurrence or per-claim limit of liability",
            "normalizer": "parse_currency_with_magnitude",
            "example": "1000000",
        },
        {
            "key": "deductible",
            "label": "Deductible",
            "type": "number",
            "required": False,
            "description": "Policy deductible or self-insured retention",
            "normalizer": "parse_currency_with_magnitude",
            "example": "5000",
        },
        {
            "key": "coverage_form",
            "label": "Coverage Form",
            "type": "string",
            "required": False,
            "description": "Coverage trigger form (e.g. Occurrence, Claims-Made)",
            "config_values": "coverage_forms",
            "config_mode": "strict",
            "example": "Occurrence",
        },
        {
            "key": "first_named_insured",
            "label": "First Named Insured",
            "type": "string",
            "required": False,
            "description": "The primary named insured on the policy",
            "example": "ABC Construction LLC",
        },
        {
            "key": "fein",
            "label": "Federal Employer ID Number",
            "type": "string",
            "required": False,
            "description": "FEIN / EIN of the insured entity",
            "normalizer": "format_fein",
            "example": "12-3456789",
        },
        {
            "key": "description",
            "label": "Coverage Description / Summary",
            "type": "string",
            "required": False,
            "description": "Brief summary or description of the coverage provided",
            "example": "Commercial general liability coverage",
        },
        {
            "key": "layer_position",
            "label": "Layer Position",
            "type": "string",
            "required": False,
            "description": "Position in the tower (e.g. Primary, 1st Excess, Umbrella)",
            "example": "Primary",
        },
        {
            "key": "commission_rate",
            "label": "Commission Rate",
            "type": "number",
            "required": False,
            "description": "Broker commission rate as a percentage",
            "example": "15",
        },
        {
            "key": "prior_premium",
            "label": "Prior Term Premium",
            "type": "number",
            "required": False,
            "description": "Premium from the prior policy term for rate comparison",
            "normalizer": "parse_currency_with_magnitude",
            "example": "42000",
        },
        {
            "key": "underwriter_name",
            "label": "Underwriter Name",
            "type": "string",
            "required": False,
            "description": "Name of the underwriter at the carrier",
            "example": "Jane Smith",
        },
        {
            "key": "underwriter_contact",
            "label": "Underwriter Email or Phone",
            "type": "string",
            "required": False,
            "description": "Contact information for the underwriter",
            "example": "jane@carrier.com",
        },
        {
            "key": "placement_colleague",
            "label": "Placement Colleague / Broker",
            "type": "string",
            "required": False,
            "description": "Name of the placement broker or colleague handling the policy",
            "example": "Bob Jones",
        },
        {
            "key": "exposure_address",
            "label": "Property / Risk Address",
            "type": "string",
            "required": False,
            "description": "Street address of the insured property or risk location",
            "example": "123 Main St",
        },
        {
            "key": "exposure_city",
            "label": "City",
            "type": "string",
            "required": False,
            "description": "City of the insured property or risk location",
            "normalizer": "format_city",
            "example": "Austin",
        },
        {
            "key": "exposure_state",
            "label": "State",
            "type": "string",
            "required": False,
            "description": "State of the insured property or risk location",
            "normalizer": "format_state",
            "example": "TX",
        },
        {
            "key": "exposure_zip",
            "label": "ZIP Code",
            "type": "string",
            "required": False,
            "description": "ZIP code of the insured property or risk location",
            "normalizer": "format_zip",
            "example": "78701",
        },
        {
            "key": "exposure_basis",
            "label": "Exposure Basis",
            "type": "string",
            "required": False,
            "description": "Basis used for rating (e.g. Payroll, Revenue, Area)",
            "config_values": "exposure_basis_options",
            "config_mode": "prefer",
            "example": "Payroll",
        },
        {
            "key": "exposure_amount",
            "label": "Exposure Amount",
            "type": "number",
            "required": False,
            "description": "Exposure value used for premium calculation",
            "normalizer": "parse_currency_with_magnitude",
            "example": "12500000",
        },
        {
            "key": "project_name",
            "label": "Location / Project Name",
            "type": "string",
            "required": False,
            "description": "Name of the project, location, or job site",
            "example": "Main Office",
        },
        {
            "key": "access_point",
            "label": "Program / Access Point",
            "type": "string",
            "required": False,
            "description": "Program name or market access point used to place this policy",
        },
        {
            "key": "attachment_point",
            "label": "Attachment Point",
            "type": "number",
            "required": False,
            "description": "Dollar amount where excess coverage attaches above underlying",
            "normalizer": "parse_currency_with_magnitude",
            "example": "1000000",
        },
        {
            "key": "notes",
            "label": "Additional Notes",
            "type": "string",
            "required": False,
            "description": "Any additional notes, conditions, or remarks from the document",
        },
    ],
}

# ---------------------------------------------------------------------------
# Compliance Extraction Schema
# ---------------------------------------------------------------------------

COMPLIANCE_EXTRACTION_SCHEMA: dict = {
    "name": "compliance_extraction",
    "version": 1,
    "description": (
        "Extract insurance requirements from a contract, "
        "loan covenant, or lease agreement"
    ),
    "context_fields": ["client_name", "location_name", "source_name"],
    "fields": {
        "source": [
            {
                "key": "name",
                "label": "Document / Source Name",
                "type": "string",
                "required": True,
                "description": "Name or title of the contract or agreement",
            },
            {
                "key": "counterparty",
                "label": "Counterparty",
                "type": "string",
                "required": True,
                "description": "The other party to the contract requiring insurance",
            },
            {
                "key": "clause_ref",
                "label": "Clause / Section Reference",
                "type": "string",
                "required": False,
                "description": "Section or clause number where insurance requirements appear",
            },
            {
                "key": "notes",
                "label": "Notes",
                "type": "string",
                "required": False,
                "description": "Additional notes about the source document",
            },
        ],
        "requirements": [
            {
                "key": "coverage_line",
                "label": "Coverage Line",
                "type": "string",
                "required": True,
                "description": "The type of insurance coverage required",
                "config_values": "policy_types",
                "config_mode": "prefer",
                "normalizer": "normalize_coverage_type",
            },
            {
                "key": "required_limit",
                "label": "Required Limit",
                "type": "number",
                "required": False,
                "description": "Minimum limit of liability required by the contract",
                "normalizer": "parse_currency_with_magnitude",
            },
            {
                "key": "max_deductible",
                "label": "Maximum Deductible",
                "type": "number",
                "required": False,
                "description": "Maximum allowable deductible or self-insured retention",
                "normalizer": "parse_currency_with_magnitude",
            },
            {
                "key": "deductible_type",
                "label": "Deductible Type",
                "type": "string",
                "required": False,
                "description": "Type of deductible (e.g. Per Occurrence, Aggregate)",
                "config_values": "deductible_types",
                "config_mode": "prefer",
            },
            {
                "key": "required_endorsements",
                "label": "Required Endorsements",
                "type": "array",
                "required": False,
                "description": "List of endorsements or additional insured requirements",
                "config_values": "endorsement_types",
                "config_mode": "prefer",
            },
            {
                "key": "notes",
                "label": "Notes",
                "type": "string",
                "required": False,
                "description": "Additional notes about this coverage requirement",
            },
        ],
        "cope": [
            {
                "key": "construction_type",
                "label": "Construction Type",
                "type": "string",
                "required": False,
                "description": "Building construction classification",
                "config_values": "construction_types",
                "config_mode": "prefer",
            },
            {
                "key": "year_built",
                "label": "Year Built",
                "type": "number",
                "required": False,
                "description": "Year the structure was built or last renovated",
            },
            {
                "key": "stories",
                "label": "Number of Stories",
                "type": "number",
                "required": False,
                "description": "Number of stories in the building",
            },
            {
                "key": "sq_footage",
                "label": "Square Footage",
                "type": "number",
                "required": False,
                "description": "Total square footage of the building",
            },
            {
                "key": "sprinklered",
                "label": "Sprinkler Status",
                "type": "string",
                "required": False,
                "description": "Whether the building has sprinkler protection",
                "config_values": "sprinkler_options",
                "config_mode": "strict",
            },
            {
                "key": "roof_type",
                "label": "Roof Type",
                "type": "string",
                "required": False,
                "description": "Type or material of the roof",
                "config_values": "roof_types",
                "config_mode": "prefer",
            },
            {
                "key": "occupancy_description",
                "label": "Occupancy Description",
                "type": "string",
                "required": False,
                "description": "Description of how the building is occupied or used",
            },
            {
                "key": "protection_class",
                "label": "Protection Class",
                "type": "string",
                "required": False,
                "description": "ISO protection class rating",
                "config_values": "protection_classes",
                "config_mode": "prefer",
            },
            {
                "key": "total_insurable_value",
                "label": "Total Insurable Value",
                "type": "number",
                "required": False,
                "description": "Total insurable value of the property",
                "normalizer": "parse_currency_with_magnitude",
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Prompt Generator
# ---------------------------------------------------------------------------

# Section headings for compliance schema groups
_COMPLIANCE_SECTION_HEADINGS: dict[str, str] = {
    "source": "## Source",
    "requirements": "## Requirements (return as JSON array)",
    "cope": "## COPE Data (optional — include only if property data found)",
}


def _build_field_instruction(field: dict, config_lists: dict) -> str:
    """Build a single field instruction line with config value annotations."""
    key = field["key"]
    label = field["label"]
    ftype = field["type"]
    required = field.get("required", False)
    description = field.get("description", "")
    req_label = "required" if required else "optional — omit if not found"

    line = f"- {label} ({key}, {ftype}, {req_label}): {description}"

    # Config value injection
    config_key = field.get("config_values")
    config_mode = field.get("config_mode", "prefer")
    if config_key and config_key in config_lists:
        values = config_lists[config_key]
        if values:
            joined = ", ".join(values)
            if config_mode == "strict":
                line += f" Must be one of: [{joined}]."
            else:
                line += (
                    f" Prefer one of: [{joined}]."
                    " If no match, use the exact name as it appears in the document."
                )

    # Type-specific annotations
    if ftype == "date":
        line += " Format: YYYY-MM-DD"
    elif ftype == "number":
        line += " Numeric value only, no currency symbols or commas"

    return line


def _build_json_example(schema: dict) -> dict:
    """Build a JSON example dict from schema field definitions."""
    fields = schema["fields"]

    # Flat schema (policy) — fields is a list
    if isinstance(fields, list):
        result = {}
        for f in fields:
            result[f["key"]] = f.get("example", "")
        return result

    # Nested schema (compliance) — fields is a dict of group_name -> list
    result = {}
    for group_name, group_fields in fields.items():
        group_obj = {}
        for f in group_fields:
            group_obj[f["key"]] = f.get("example", "")
        if group_name == "requirements":
            result[group_name] = [group_obj]
        else:
            result[group_name] = group_obj
    return result


def generate_json_template(schema: dict) -> str:
    """Return just the JSON template example from a schema.

    Builds a JSON object using the ``example`` values from each field in the
    schema.  For nested schemas (compliance), groups are nested as
    ``{"source": {...}, "requirements": [{...}], "cope": {...}}``.

    This is used for the "Copy JSON Template Only" button in the UI.
    """
    example = _build_json_example(schema)
    return json.dumps(example, indent=2)


def generate_extraction_prompt(schema: dict, context: dict) -> str:
    """Build a complete extraction prompt from schema definition and context.

    Returns a prompt string with four sections:
    1. Role — establishes the LLM as an insurance document analyst
    2. Field Instructions — per-field extraction rules with config value hints
    3. Context — client/industry/location context for disambiguation
    4. JSON Template — example JSON structure the LLM should return
    """
    config_lists = context.get("config_lists", {})
    fields = schema["fields"]
    is_policy = isinstance(fields, list)
    parts: list[str] = []

    # --- Section 1: Role ---
    parts.append(
        "You are an insurance document analyst. Extract the following fields "
        "from the attached document and return valid JSON only — no commentary, "
        "no markdown."
    )
    parts.append("")

    # --- Section 2: Field Instructions ---
    parts.append("## Fields")
    parts.append("")

    if is_policy:
        # Flat field list
        for f in fields:
            parts.append(_build_field_instruction(f, config_lists))

        # Policy-specific aggregate/retention instruction
        parts.append("")
        parts.append(
            "If the document lists an aggregate limit, retention, or "
            "self-insured retention (SIR), include these values in the notes field."
        )
    else:
        # Nested field groups (compliance)
        for group_name, group_fields in fields.items():
            heading = _COMPLIANCE_SECTION_HEADINGS.get(group_name, f"## {group_name}")
            parts.append("")
            parts.append(heading)
            parts.append("")
            for f in group_fields:
                parts.append(_build_field_instruction(f, config_lists))

    parts.append("")

    # --- Section 3: Context ---
    context_fields = schema.get("context_fields", [])
    context_lines: list[str] = []
    # Map context_field keys to human-readable labels
    _context_labels = {
        "client_name": "Client",
        "industry": "Industry",
        "location_name": "Location",
        "source_name": "Source Document",
    }
    for cf in context_fields:
        value = context.get(cf)
        if value:
            label = _context_labels.get(cf, cf)
            context_lines.append(f"- {label}: {value}")

    if context_lines:
        parts.append("Context:")
        parts.extend(context_lines)
        parts.append("")

    # --- Section 4: JSON Template ---
    json_example = json.dumps(_build_json_example(schema), indent=2)
    parts.append("Return ONLY valid JSON matching this structure:")
    parts.append(f"```json\n{json_example}\n```")

    return "\n".join(parts)
