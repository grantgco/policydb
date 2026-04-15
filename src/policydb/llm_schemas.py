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
import re

import dateparser

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
# Shared Field Definitions — reused across policy and compliance schemas
# ---------------------------------------------------------------------------

COPE_FIELDS: list[dict] = [
    {
        "key": "construction_type",
        "label": "Construction Type",
        "type": "string",
        "required": False,
        "description": "Building construction classification",
        "config_values": "construction_types",
        "config_mode": "prefer",
        "example": "Fire Resistive",
    },
    {
        "key": "year_built",
        "label": "Year Built",
        "type": "number",
        "required": False,
        "description": "Year the structure was built or last renovated",
        "example": "2005",
    },
    {
        "key": "stories",
        "label": "Number of Stories",
        "type": "number",
        "required": False,
        "description": "Number of stories in the building",
        "example": "3",
    },
    {
        "key": "sq_footage",
        "label": "Square Footage",
        "type": "number",
        "required": False,
        "description": "Total square footage of the building",
        "example": "45000",
    },
    {
        "key": "sprinklered",
        "label": "Sprinkler Status",
        "type": "string",
        "required": False,
        "description": "Whether the building has sprinkler protection",
        "config_values": "sprinkler_options",
        "config_mode": "strict",
        "example": "Yes",
    },
    {
        "key": "roof_type",
        "label": "Roof Type",
        "type": "string",
        "required": False,
        "description": "Type or material of the roof",
        "config_values": "roof_types",
        "config_mode": "prefer",
        "example": "TPO Membrane",
    },
    {
        "key": "occupancy_description",
        "label": "Occupancy Description",
        "type": "string",
        "required": False,
        "description": "Description of how the building is occupied or used",
        "example": "Office space, ground floor retail",
    },
    {
        "key": "protection_class",
        "label": "Protection Class",
        "type": "string",
        "required": False,
        "description": "ISO protection class rating",
        "config_values": "protection_classes",
        "config_mode": "prefer",
        "example": "3",
    },
    {
        "key": "total_insurable_value",
        "label": "Total Insurable Value",
        "type": "number",
        "required": False,
        "description": "Total insurable value of the property (building + contents + BI)",
        "normalizer": "parse_currency_with_magnitude",
        "example": "12500000",
    },
]

LOCATION_FIELDS: list[dict] = [
    {
        "key": "name",
        "label": "Location / Building Name",
        "type": "string",
        "required": False,
        "description": "Name or label for the location or building",
        "example": "Main Office",
    },
    {
        "key": "address",
        "label": "Street Address",
        "type": "string",
        "required": False,
        "description": "Street address of the location",
        "example": "123 Main St",
    },
    {
        "key": "city",
        "label": "City",
        "type": "string",
        "required": False,
        "description": "City",
        "normalizer": "format_city",
        "example": "Austin",
    },
    {
        "key": "state",
        "label": "State",
        "type": "string",
        "required": False,
        "description": "State code",
        "normalizer": "format_state",
        "example": "TX",
    },
    {
        "key": "zip",
        "label": "ZIP Code",
        "type": "string",
        "required": False,
        "description": "ZIP code",
        "normalizer": "format_zip",
        "example": "78701",
    },
    {
        "key": "notes",
        "label": "Location Notes",
        "type": "string",
        "required": False,
        "description": "Special conditions, building characteristics, or other notes about this location",
        "example": "24hr security, backup generator",
    },
]

# ---------------------------------------------------------------------------
# Policy Extraction Schema
# ---------------------------------------------------------------------------

POLICY_EXTRACTION_SCHEMA: dict = {
    "name": "policy_extraction",
    "version": 2,
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
            "key": "endorsements",
            "label": "Endorsements / Forms Attached",
            "type": "array",
            "required": False,
            "description": (
                "List of endorsements attached to the policy. Look in the 'Forms and "
                "Endorsements' schedule, in CG/CA form numbers with their titles, or in "
                "additional coverage notes. Return the canonical endorsement name only "
                "(e.g., 'Waiver of Subrogation', 'Additional Insured - Blanket', "
                "'Primary & Non-Contributory', 'Per Project Aggregate', 'Notice of "
                "Cancellation'), not form numbers. Omit endorsements that are part of "
                "the base coverage form (e.g., CG 00 01 itself)."
            ),
            "config_values": "endorsement_types",
            "config_mode": "prefer",
            "example": '["Waiver of Subrogation", "Additional Insured - Blanket", "Primary & Non-Contributory"]',
        },
        {
            "key": "layer_position",
            "label": "Layer Position",
            "type": "string",
            "required": False,
            "description": (
                "Position in the insurance tower. Use 'Primary' for ground-up coverage, "
                "'1st Excess' / '2nd Excess' etc. for layers above primary, "
                "'Umbrella' for umbrella policies. If the document says 'excess of' "
                "a specific limit, this is an excess layer."
            ),
            "example": "Primary",
        },
        {
            "key": "tower_group",
            "label": "Tower / Program Group",
            "type": "string",
            "required": False,
            "description": (
                "Name of the tower or layered program this policy belongs to "
                "(e.g. 'GL Tower', 'Property Program'). Group policies that stack "
                "on top of each other under the same tower group name."
            ),
            "example": "GL Tower",
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
            "description": (
                "Dollar amount where excess/umbrella coverage begins (attaches above underlying). "
                "For example, if a policy says 'excess of $1,000,000', the attachment point is 1000000. "
                "Leave blank/omit for Primary layers."
            ),
            "normalizer": "parse_currency_with_magnitude",
            "example": "1000000",
        },
        {
            "key": "participation_of",
            "label": "Participation Of / Part Of",
            "type": "number",
            "required": False,
            "description": (
                "Total layer limit when multiple carriers share a layer. "
                "For example, '$10M part of $30M' means participation_of is 30000000. "
                "Leave blank for sole-carrier layers."
            ),
            "normalizer": "parse_currency_with_magnitude",
            "example": "30000000",
        },
        {
            "key": "notes",
            "label": "Additional Notes",
            "type": "string",
            "required": False,
            "description": "Any additional notes, conditions, or remarks from the document",
        },
    ],
    "nested_groups": {
        "exposures": {
            "type": "array",
            "optional": True,
            "description": (
                "Exposure rating bases used to calculate premium.  Extract EVERY "
                "rating basis the document lists, not just the primary one.  A "
                "single policy commonly has multiple exposures — for example a "
                "General Liability policy may be rated on both payroll and gross "
                "sales, or a Workers Comp policy may list per-state payroll by "
                "classification.  Create one list item per distinct rating row.  "
                "If an exposure is tied to a specific location (building, site, "
                "project), include the address fields so the importer can upsert "
                "a location record and attach the exposure to it.  Mark the "
                "principal rating basis with is_primary=true if the document "
                "distinguishes one; otherwise the first list item becomes primary."
            ),
            "fields": [
                {
                    "key": "exposure_type",
                    "label": "Exposure Type / Rating Basis",
                    "type": "string",
                    "required": True,
                    "description": (
                        "The rating basis this exposure uses (e.g., Payroll, "
                        "Revenue, Gross Sales, Area, Units, TIV).  Prefer the "
                        "configured list when applicable, but use whatever "
                        "terminology the policy uses if it doesn't match."
                    ),
                    "config_values": "exposure_basis_options",
                    "config_mode": "prefer",
                    "example": "Payroll",
                },
                {
                    "key": "amount",
                    "label": "Exposure Amount",
                    "type": "number",
                    "required": True,
                    "description": (
                        "The exposure value used for premium calculation "
                        "(e.g., total payroll dollars, total gross receipts, "
                        "total square feet)."
                    ),
                    "normalizer": "parse_currency_with_magnitude",
                    "example": "12500000",
                },
                {
                    "key": "denominator",
                    "label": "Rating Denominator",
                    "type": "number",
                    "required": False,
                    "description": (
                        "The 'per X' denominator used when quoting a rate. "
                        "For example, 'per $100 of payroll' has denominator 100; "
                        "'per $1,000 of revenue' has denominator 1000. "
                        "Default to 1 when not specified."
                    ),
                    "example": "100",
                },
                {
                    "key": "unit",
                    "label": "Rating Unit Description",
                    "type": "string",
                    "required": False,
                    "description": (
                        "Human-readable rating unit string, e.g. "
                        "'Per $100 Payroll', 'Per $1,000 Revenue', 'Flat'."
                    ),
                    "config_values": "exposure_unit_options",
                    "config_mode": "prefer",
                    "example": "Per $100 Payroll",
                },
                {
                    "key": "is_primary",
                    "label": "Is Primary Rating Basis",
                    "type": "boolean",
                    "required": False,
                    "description": (
                        "True when this is the dominant rating basis for the "
                        "policy.  Only mark one entry primary; if unsure, leave "
                        "all entries false and the importer will default the "
                        "first to primary."
                    ),
                    "example": "true",
                },
                {
                    "key": "location_label",
                    "label": "Location / Project Name",
                    "type": "string",
                    "required": False,
                    "description": (
                        "Name of the location or project this exposure applies "
                        "to, if the policy rates per-location (e.g., 'Main "
                        "Office', 'Site B', 'Plant #3')."
                    ),
                    "example": "Main Office",
                },
                {
                    "key": "address",
                    "label": "Location Street Address",
                    "type": "string",
                    "required": False,
                    "description": (
                        "Street address of the exposure location.  When "
                        "present, the importer will upsert a location/project "
                        "record and attach this exposure to it."
                    ),
                    "example": "123 Main St",
                },
                {
                    "key": "city",
                    "label": "Location City",
                    "type": "string",
                    "required": False,
                    "description": "City of the exposure location.",
                    "normalizer": "format_city",
                    "example": "Austin",
                },
                {
                    "key": "state",
                    "label": "Location State",
                    "type": "string",
                    "required": False,
                    "description": "State abbreviation of the exposure location.",
                    "normalizer": "format_state",
                    "example": "TX",
                },
                {
                    "key": "zip",
                    "label": "Location ZIP",
                    "type": "string",
                    "required": False,
                    "description": "ZIP code of the exposure location.",
                    "normalizer": "format_zip",
                    "example": "78701",
                },
            ],
        },
        "locations": {
            "type": "array",
            "optional": True,
            "description": (
                "Locations or buildings described in the document. Include if the document "
                "contains property schedules, SOVs, building descriptions, or COPE data."
            ),
            "fields": LOCATION_FIELDS,
            "nested": {
                "cope": {
                    "type": "object",
                    "optional": True,
                    "description": "COPE (Construction, Occupancy, Protection, Exposure) data for this location",
                    "fields": COPE_FIELDS,
                },
            },
        },
        "sub_coverages": {
            "type": "array",
            "optional": True,
            "description": (
                "Sub-coverages, endorsements, or coverage parts within this policy. "
                "Use for: package/BOP policies with multiple lines, Workers Comp "
                "with Employers Liability, or any policy listing multiple coverage "
                "sections with separate limits or deductibles."
            ),
            "fields": [
                {
                    "key": "coverage_type",
                    "label": "Coverage Type",
                    "type": "string",
                    "required": True,
                    "description": "The sub-line coverage type (e.g., General Liability, Property, Employers Liability)",
                    "config_values": "policy_types",
                    "config_mode": "prefer",
                    "normalizer": "normalize_coverage_type",
                },
                {
                    "key": "limit_amount",
                    "label": "Limit",
                    "type": "number",
                    "required": False,
                    "description": "Per-occurrence or aggregate limit for this sub-coverage",
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "deductible",
                    "label": "Deductible / Retention",
                    "type": "number",
                    "required": False,
                    "description": "Deductible or self-insured retention for this sub-coverage",
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "coverage_form",
                    "label": "Coverage Form",
                    "type": "string",
                    "required": False,
                    "description": "Coverage trigger form (e.g., Occurrence, Claims-Made)",
                    "config_values": "coverage_forms",
                    "config_mode": "strict",
                },
                {
                    "key": "notes",
                    "label": "Notes",
                    "type": "string",
                    "required": False,
                    "description": "Any additional notes about this sub-coverage",
                },
            ],
        },
    },
}

# ---------------------------------------------------------------------------
# Policy Bulk Import Schema — for cleaning messy spreadsheet data via LLM
# ---------------------------------------------------------------------------
# Uses the same field definitions as POLICY_EXTRACTION_SCHEMA but returns
# an array of policies. The generate_policy_bulk_prompt() function adds
# client-specific context (known locations, programs, carriers).

POLICY_BULK_IMPORT_SCHEMA: dict = {
    "name": "policy_bulk_import",
    "version": 1,
    "description": (
        "Extract and normalize multiple policies from messy spreadsheet data, "
        "prior AE notes, or unstructured policy lists. Return a JSON array "
        "of policy objects."
    ),
    "context_fields": ["client_name", "industry"],
    "is_array": True,  # signals that the LLM should return an array
    "fields": POLICY_EXTRACTION_SCHEMA["fields"],  # reuse same field defs
    "nested_groups": {
        "program_layers": {
            "type": "array",
            "optional": True,
            "description": (
                "If this policy is part of a layered program (multiple carriers "
                "stacking on the same coverage type with the same dates), list "
                "each carrier/layer here instead of creating separate policy objects. "
                "Use this when you see multiple carriers for the same coverage type "
                "and effective/expiration dates."
            ),
            "fields": [
                {
                    "key": "carrier",
                    "label": "Carrier",
                    "type": "string",
                    "required": True,
                    "normalizer": "normalize_carrier",
                    "config_values": "carriers",
                    "config_mode": "prefer",
                },
                {
                    "key": "layer_position",
                    "label": "Layer Position",
                    "type": "string",
                    "required": False,
                    "description": "Primary, 1st Excess, 2nd Excess, Umbrella, etc.",
                },
                {
                    "key": "policy_number",
                    "label": "Policy Number",
                    "type": "string",
                    "required": False,
                    "normalizer": "normalize_policy_number",
                },
                {
                    "key": "premium",
                    "label": "Premium",
                    "type": "number",
                    "required": False,
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "limit_amount",
                    "label": "Limit",
                    "type": "number",
                    "required": False,
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "attachment_point",
                    "label": "Attachment Point",
                    "type": "number",
                    "required": False,
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "participation_of",
                    "label": "Participation Of / Part Of",
                    "type": "number",
                    "required": False,
                    "description": (
                        "Total layer limit when multiple carriers share a layer. "
                        "For example, '$10M part of $30M' means participation_of is 30000000."
                    ),
                    "normalizer": "parse_currency_with_magnitude",
                },
            ],
        },
        "sub_coverages": {
            "type": "array",
            "optional": True,
            "description": (
                "If this policy is a package or bundled policy (e.g., BOP, Business "
                "Owners Policy) that covers multiple lines of business under one policy "
                "number, list each sub-coverage here. Also use for Workers Compensation "
                "policies to include an Employers Liability sub-coverage. Each sub-coverage "
                "can have its own limit and deductible."
            ),
            "fields": [
                {
                    "key": "coverage_type",
                    "label": "Coverage Type",
                    "type": "string",
                    "required": True,
                    "description": "The sub-line coverage type (e.g., General Liability, Property)",
                    "config_values": "policy_types",
                    "config_mode": "prefer",
                    "normalizer": "normalize_coverage_type",
                },
                {
                    "key": "limit_amount",
                    "label": "Limit",
                    "type": "number",
                    "required": False,
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "deductible",
                    "label": "Deductible",
                    "type": "number",
                    "required": False,
                    "normalizer": "parse_currency_with_magnitude",
                },
                {
                    "key": "notes",
                    "label": "Notes",
                    "type": "string",
                    "required": False,
                    "description": "Any additional notes about this sub-coverage",
                },
            ],
        },
    },
}


def generate_policy_bulk_prompt(conn, client_id: int) -> str:
    """Build a specialized prompt for bulk policy extraction from messy data.

    Pre-loads client-specific context so the LLM normalizes to known values:
    - Valid carriers and aliases
    - Valid policy types
    - Known locations/projects for this client
    - Known programs for this client
    """
    import policydb.config as _cfg

    # --- Client info ---
    client = conn.execute(
        "SELECT name, industry_segment FROM clients WHERE id = ?", (client_id,)
    ).fetchone()
    client_name = client["name"] if client else "Unknown"
    industry = (client["industry_segment"] or "") if client else ""

    # --- Known locations ---
    locations = conn.execute(
        "SELECT name, address, city, state FROM projects "
        "WHERE client_id = ? AND (project_type = 'Location' OR project_type IS NULL) "
        "ORDER BY name",
        (client_id,),
    ).fetchall()
    loc_list = []
    for loc in locations:
        parts = [loc["name"]]
        addr = ", ".join(filter(None, [loc["address"], loc["city"], loc["state"]]))
        if addr:
            parts.append(f"({addr})")
        loc_list.append(" ".join(parts))

    # --- Known programs ---
    programs = conn.execute(
        "SELECT program_uid, name, line_of_business FROM programs "
        "WHERE client_id = ? AND archived = 0 "
        "ORDER BY name",
        (client_id,),
    ).fetchall()
    prog_list = [f"{p['program_uid']}: {p['name']} ({p['line_of_business'] or 'multi-line'})" for p in programs]

    # --- Config lists ---
    carriers = _cfg.get("carriers", [])
    carrier_aliases = _cfg.get("carrier_aliases", {})
    policy_types = _cfg.get("policy_types", [])
    renewal_statuses = _cfg.get("renewal_statuses", [])

    # --- Build prompt ---
    parts: list[str] = []

    parts.append(
        "You are an insurance data analyst. I will provide raw, messy spreadsheet data "
        "about insurance policies for a single client. Your job is to extract and normalize "
        "this data into clean, structured JSON.\n"
    )

    parts.append("## Output Format\n")
    parts.append(
        "Return a JSON **array** of policy objects. Each policy should have the fields "
        "listed below. Omit fields you cannot determine from the data.\n"
    )

    # Field instructions (reuse the schema's field definitions)
    config_lists = {}
    for field in POLICY_BULK_IMPORT_SCHEMA["fields"]:
        ck = field.get("config_values")
        if ck:
            config_lists[ck] = _cfg.get(ck, [])
    for _gname, gdef in POLICY_BULK_IMPORT_SCHEMA.get("nested_groups", {}).items():
        for field in gdef.get("fields", []):
            ck = field.get("config_values")
            if ck:
                config_lists[ck] = _cfg.get(ck, [])

    parts.append("## Fields per Policy\n")
    for f in POLICY_BULK_IMPORT_SCHEMA["fields"]:
        parts.append(_build_field_instruction(f, config_lists))

    # Program layers
    parts.append("\n## Program Layers (optional)\n")
    parts.append(
        "If multiple rows share the same coverage type and effective/expiration dates "
        "but have different carriers or limits, they are likely layers in a program. "
        "Instead of creating separate policy objects, create ONE policy object with a "
        '"program_layers" array containing each carrier/layer:\n'
    )
    for f in POLICY_BULK_IMPORT_SCHEMA["nested_groups"]["program_layers"]["fields"]:
        parts.append(_build_field_instruction(f, config_lists))

    # --- Client context ---
    parts.append("\n## Client Context\n")
    parts.append(f"- **Client Name**: {client_name}")
    if industry:
        parts.append(f"- **Industry**: {industry}")

    if loc_list:
        parts.append(f"\n### Known Locations ({len(loc_list)} total)")
        parts.append("Match policies to these locations when possible. Use the exact name.")
        for loc in loc_list:
            parts.append(f"  - {loc}")

    if prog_list:
        parts.append(f"\n### Existing Programs ({len(prog_list)} total)")
        parts.append("These programs already exist. If the data contains their carriers/layers, group them under program_layers.")
        for p in prog_list:
            parts.append(f"  - {p}")

    # --- Carrier reference ---
    parts.append("\n### Valid Carriers")
    parts.append("Normalize carrier names to these canonical forms:")
    for c in carriers:
        aliases = carrier_aliases.get(c, [])
        if aliases:
            parts.append(f'  - **{c}** (also known as: {", ".join(aliases[:5])})')
        else:
            parts.append(f"  - **{c}**")

    # --- Coverage type reference ---
    parts.append("\n### Valid Coverage Types")
    parts.append("Normalize coverage/LOB names to these canonical forms:")
    for pt in policy_types:
        parts.append(f"  - {pt}")

    # --- Formatting rules ---
    parts.append("\n## Formatting Rules\n")
    parts.append("- Dates: YYYY-MM-DD format")
    parts.append("- Currency: plain numbers, no $ signs or commas (e.g. 50000 not $50,000)")
    parts.append("- If a value is unknown or missing, omit the field entirely")
    parts.append('- For the `project_name` field, use the location name from the Known Locations list above')
    parts.append('- If you cannot match a location, put the address or location info in `exposure_address`')
    parts.append('- Preserve any original notes, comments, or unusual data in the `notes` field')

    # --- JSON template ---
    example_policy = {}
    for f in POLICY_BULK_IMPORT_SCHEMA["fields"]:
        if f.get("example"):
            example_policy[f["key"]] = f["example"]
    example_with_layers = dict(example_policy)
    example_with_layers["program_layers"] = [
        {"carrier": "Carrier A", "layer_position": "Primary", "premium": 25000,
         "limit_amount": 1000000, "policy_number": "POL-001"},
        {"carrier": "Carrier B", "layer_position": "1st Excess", "premium": 15000,
         "limit_amount": 5000000, "attachment_point": 1000000, "policy_number": "POL-002"},
    ]

    parts.append("\n## JSON Template\n")
    parts.append("Return ONLY valid JSON matching this structure (array of policies):")
    template = json.dumps([example_policy, example_with_layers], indent=2)
    parts.append(f"```json\n{template}\n```")

    parts.append("\n---\n")
    parts.append("**PASTE THE RAW SPREADSHEET DATA BELOW THIS LINE:**\n")

    return "\n".join(parts)


def parse_policy_bulk_json(raw_text: str) -> dict:
    """Parse LLM JSON response for bulk policy import.

    Expects a JSON array of policy objects. Normalizes each using
    POLICY_BULK_IMPORT_SCHEMA field definitions.

    Returns:
        {"ok": True, "policies": [...], "warnings": [...], "count": N}
        or {"ok": False, "error": "...", "raw_text": "..."}
    """
    if len(raw_text) > 1_000_000:
        return {"ok": False, "error": "Input too large (max 1MB).", "raw_text": raw_text[:200]}

    # Try code fences first, then raw JSON.
    # _extract_json_str only finds {} objects, so we also look for [] arrays.
    json_str = _extract_json_str(raw_text)

    # If _extract_json_str returned a single object but the raw text has an array,
    # try extracting the array directly
    if json_str is None or (json_str.startswith("{") and "[" in raw_text):
        # Try to find a JSON array in the text
        for pattern in [_RE_JSON_CODE_FENCE, _RE_GENERIC_CODE_FENCE]:
            m = pattern.search(raw_text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("["):
                    json_str = candidate
                    break
        if json_str is None or not json_str.startswith("["):
            # Fallback: find outermost [ ... ] via bracket counting
            start = raw_text.find("[")
            if start != -1:
                depth = 0
                in_string = False
                escape_next = False
                for i in range(start, len(raw_text)):
                    ch = raw_text[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == "\\":
                        escape_next = True
                        continue
                    if ch == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            json_str = raw_text[start:i + 1]
                            break

    if json_str is None:
        return {"ok": False, "error": "No JSON found in input.", "raw_text": raw_text}

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Invalid JSON: {e}", "raw_text": raw_text}

    # Accept both array and single object
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return {"ok": False, "error": "Expected a JSON array of policies.", "raw_text": raw_text}

    fields = POLICY_BULK_IMPORT_SCHEMA["fields"]
    layer_fields = POLICY_BULK_IMPORT_SCHEMA["nested_groups"]["program_layers"]["fields"]
    all_warnings: list[str] = []
    policies: list[dict] = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            all_warnings.append(f"Item [{i}] is not an object, skipping.")
            continue

        parsed, raw, warnings = _parse_flat_fields(item, fields)
        for w in warnings:
            all_warnings.append(f"Policy [{i}]: {w}")

        # Parse program_layers if present
        layers_data = item.get("program_layers")
        if layers_data and isinstance(layers_data, list):
            parsed_layers = []
            for j, layer in enumerate(layers_data):
                if not isinstance(layer, dict):
                    all_warnings.append(f"Policy [{i}] layer [{j}] is not an object, skipping.")
                    continue
                lp, lr, lw = _parse_flat_fields(layer, layer_fields)
                for w in lw:
                    all_warnings.append(f"Policy [{i}] layer [{j}]: {w}")
                if lp:
                    parsed_layers.append(lp)
            if parsed_layers:
                parsed["program_layers"] = parsed_layers

        if parsed:
            parsed["_raw"] = raw
            parsed["_index"] = i
            policies.append(parsed)

    if not policies:
        return {"ok": False, "error": "No valid policies extracted from JSON.", "raw_text": raw_text}

    return {
        "ok": True,
        "policies": policies,
        "warnings": all_warnings,
        "count": len(policies),
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
        "cope": COPE_FIELDS,
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

        # Append nested_groups examples (e.g. locations with COPE)
        nested_groups = schema.get("nested_groups", {})
        for group_name, group_def in nested_groups.items():
            group_example = {}
            for f in group_def.get("fields", []):
                group_example[f["key"]] = f.get("example", "")
            # Sub-nested groups (e.g. cope inside location)
            for sub_name, sub_def in group_def.get("nested", {}).items():
                sub_example = {}
                for f in sub_def.get("fields", []):
                    sub_example[f["key"]] = f.get("example", "")
                group_example[sub_name] = sub_example
            if group_def.get("type") == "array":
                result[group_name] = [group_example]
            else:
                result[group_name] = group_example
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

        # Nested groups (locations + COPE)
        nested_groups = schema.get("nested_groups", {})
        for group_name, group_def in nested_groups.items():
            desc = group_def.get("description", "")
            optional = group_def.get("optional", False)
            opt_note = " (optional — include only if data is found in the document)" if optional else ""
            parts.append("")
            parts.append(f"## {group_name.title()}{opt_note}")
            if desc:
                parts.append(f"{desc}")
            parts.append("")
            gtype = group_def.get("type", "object")
            if gtype == "array":
                parts.append(
                    f'Return "{group_name}" as a JSON array. Each element should contain:'
                )
                parts.append("")
            for f in group_def.get("fields", []):
                parts.append(_build_field_instruction(f, config_lists))
            # Sub-nested (e.g. cope inside location)
            for sub_name, sub_def in group_def.get("nested", {}).items():
                sub_desc = sub_def.get("description", "")
                sub_opt = sub_def.get("optional", False)
                sub_note = " (optional)" if sub_opt else ""
                parts.append("")
                parts.append(f"### {sub_name.upper()} Data{sub_note}")
                if sub_desc:
                    parts.append(f"{sub_desc}")
                parts.append("")
                for f in sub_def.get("fields", []):
                    parts.append(_build_field_instruction(f, config_lists))
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


# ---------------------------------------------------------------------------
# JSON Parser
# ---------------------------------------------------------------------------

_MAX_INPUT_BYTES = 500 * 1024  # 500 KB

# Patterns tried in order to extract JSON from LLM output
_RE_JSON_CODE_FENCE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)
_RE_GENERIC_CODE_FENCE = re.compile(r"```\s*\n(.*?)\n\s*```", re.DOTALL)


def _extract_json_str(raw_text: str) -> str | None:
    """Try several strategies to extract a JSON string from LLM output."""
    # Strategy 1: ```json ... ``` code fence
    m = _RE_JSON_CODE_FENCE.search(raw_text)
    if m:
        return m.group(1).strip()

    # Strategy 2: ``` ... ``` generic code fence
    m = _RE_GENERIC_CODE_FENCE.search(raw_text)
    if m:
        return m.group(1).strip()

    # Strategy 3: Find outermost { ... } via brace counting
    start = raw_text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(raw_text)):
            ch = raw_text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return raw_text[start : i + 1]

    # Strategy 4: Try the whole thing
    stripped = raw_text.strip()
    if stripped.startswith("{"):
        return stripped

    return None


def _normalize_field_value(
    key: str, value, field_def: dict, warnings: list[str]
):
    """Normalize a single field value according to its schema definition.

    Returns the normalized value. Appends to ``warnings`` on problems.
    """
    ftype = field_def.get("type", "string")

    # --- Date fields: special-case with dateparser ---
    if ftype == "date":
        raw_str = str(value)
        try:
            parsed_dt = dateparser.parse(raw_str)
            if parsed_dt is not None:
                return parsed_dt.strftime("%Y-%m-%d")
            else:
                warnings.append(
                    f"Could not parse date for '{key}': {raw_str!r}"
                )
                return raw_str
        except Exception:
            warnings.append(
                f"Could not parse date for '{key}': {raw_str!r}"
            )
            return raw_str

    # --- Normalizer from registry ---
    normalizer_name = field_def.get("normalizer")
    if normalizer_name and normalizer_name in NORMALIZER_REGISTRY:
        fn = NORMALIZER_REGISTRY[normalizer_name]
        try:
            return fn(value)
        except Exception:
            warnings.append(
                f"Normalizer '{normalizer_name}' failed for '{key}': {value!r}"
            )
            return value

    # --- No normalizer: pass through ---
    return value


def _parse_flat_fields(
    data: dict, field_defs: list[dict]
) -> tuple[dict, dict, list[str]]:
    """Parse a flat JSON dict against a list of field definitions.

    Returns (parsed, raw, warnings).
    """
    parsed: dict = {}
    raw: dict = {}
    warnings: list[str] = []

    for field_def in field_defs:
        key = field_def["key"]
        if key not in data:
            if field_def.get("required"):
                warnings.append(f"Missing required field: '{key}'")
            continue
        value = data[key]
        raw[key] = value
        parsed[key] = _normalize_field_value(key, value, field_def, warnings)

    return parsed, raw, warnings


def parse_llm_json(raw_text: str, schema: dict) -> dict:
    """Parse LLM JSON response against a schema, validate and normalize.

    Returns:
        {"ok": True, "parsed": {...}, "warnings": [...], "raw": {...}}
        or {"ok": False, "error": "...", "raw_text": "..."}
    """
    # --- Size check ---
    if len(raw_text) > _MAX_INPUT_BYTES:
        return {
            "ok": False,
            "error": "Input too large (max 500KB).",
            "raw_text": raw_text[:200] + "...",
        }

    # --- Extract JSON string ---
    json_str = _extract_json_str(raw_text)
    if json_str is None:
        return {
            "ok": False,
            "error": "No JSON object found in input.",
            "raw_text": raw_text,
        }

    # --- Parse JSON ---
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": f"Invalid JSON: {e}",
            "raw_text": raw_text,
        }

    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": "Expected a JSON object, got a different type.",
            "raw_text": raw_text,
        }

    fields = schema["fields"]

    # --- Flat schema (policy) — fields is a list ---
    if isinstance(fields, list):
        parsed, raw, warnings = _parse_flat_fields(data, fields)
        if not parsed:
            return {
                "ok": False,
                "error": "No fields were extracted from the JSON.",
                "raw_text": raw_text,
            }

        # Parse nested groups (locations with COPE)
        nested_groups = schema.get("nested_groups", {})
        for group_name, group_def in nested_groups.items():
            group_data = data.get(group_name)
            if group_data is None:
                continue  # optional group not present — backward compat
            gtype = group_def.get("type", "object")
            group_fields = group_def.get("fields", [])
            sub_groups = group_def.get("nested", {})

            if gtype == "array" and isinstance(group_data, list):
                parsed_items: list[dict] = []
                raw_items: list[dict] = []
                for i, item in enumerate(group_data):
                    if not isinstance(item, dict):
                        warnings.append(f"{group_name}[{i}] is not an object, skipping.")
                        continue
                    p, r, w = _parse_flat_fields(item, group_fields)
                    # Parse sub-nested groups (e.g. cope inside location)
                    for sub_name, sub_def in sub_groups.items():
                        sub_data = item.get(sub_name)
                        if sub_data is not None and isinstance(sub_data, dict):
                            sp, sr, sw = _parse_flat_fields(sub_data, sub_def.get("fields", []))
                            p[sub_name] = sp
                            r[sub_name] = sr
                            warnings.extend(sw)
                    parsed_items.append(p)
                    raw_items.append(r)
                    warnings.extend(w)
                if parsed_items:
                    parsed[group_name] = parsed_items
                    raw[group_name] = raw_items
            elif isinstance(group_data, dict):
                p, r, w = _parse_flat_fields(group_data, group_fields)
                for sub_name, sub_def in sub_groups.items():
                    sub_data = group_data.get(sub_name)
                    if sub_data is not None and isinstance(sub_data, dict):
                        sp, sr, sw = _parse_flat_fields(sub_data, sub_def.get("fields", []))
                        p[sub_name] = sp
                        r[sub_name] = sr
                        warnings.extend(sw)
                parsed[group_name] = p
                raw[group_name] = r
                warnings.extend(w)

        return {"ok": True, "parsed": parsed, "warnings": warnings, "raw": raw}

    # --- Nested schema (compliance) — fields is a dict of groups ---
    parsed: dict = {}
    raw: dict = {}
    warnings: list[str] = []

    # Source
    source_defs = fields.get("source", [])
    source_data = data.get("source", {})
    if isinstance(source_data, dict):
        p, r, w = _parse_flat_fields(source_data, source_defs)
        parsed["source"] = p
        raw["source"] = r
        warnings.extend(w)
    else:
        parsed["source"] = {}
        raw["source"] = {}
        warnings.append("'source' field is not an object.")

    # Requirements
    req_defs = fields.get("requirements", [])
    req_data = data.get("requirements", [])
    if not isinstance(req_data, list):
        return {
            "ok": False,
            "error": "'requirements' must be a JSON array.",
            "raw_text": raw_text,
        }
    parsed_reqs: list[dict] = []
    raw_reqs: list[dict] = []
    for i, item in enumerate(req_data):
        if not isinstance(item, dict):
            warnings.append(f"requirements[{i}] is not an object, skipping.")
            continue
        p, r, w = _parse_flat_fields(item, req_defs)
        parsed_reqs.append(p)
        raw_reqs.append(r)
        warnings.extend(w)
    parsed["requirements"] = parsed_reqs
    raw["requirements"] = raw_reqs

    # COPE (optional)
    cope_defs = fields.get("cope", [])
    cope_data = data.get("cope")
    if cope_data is not None and isinstance(cope_data, dict):
        p, r, w = _parse_flat_fields(cope_data, cope_defs)
        parsed["cope"] = p
        raw["cope"] = r
        warnings.extend(w)

    # Empty check: no source fields and no requirements
    if not parsed.get("source") and not parsed.get("requirements"):
        return {
            "ok": False,
            "error": "No fields were extracted from the JSON.",
            "raw_text": raw_text,
        }

    return {"ok": True, "parsed": parsed, "warnings": warnings, "raw": raw}


# ---------------------------------------------------------------------------
# Contact Extraction Schema — email chain → policy contact list
# ---------------------------------------------------------------------------

CONTACT_EXTRACTION_SCHEMA: dict = {
    "name": "contact_extraction",
    "version": 1,
    "description": (
        "Extract contacts from an email chain, distribution list, "
        "or correspondence thread related to an insurance policy"
    ),
    "is_array": True,
    "fields": [
        {
            "key": "name",
            "label": "Full Name",
            "type": "string",
            "required": True,
            "description": "Full name of the person (first and last name)",
            "example": "Jane Smith",
        },
        {
            "key": "email",
            "label": "Email Address",
            "type": "string",
            "required": False,
            "description": "Email address (from headers, cc/bcc, or signature block)",
            "example": "jane.smith@carrier.com",
        },
        {
            "key": "phone",
            "label": "Phone Number",
            "type": "string",
            "required": False,
            "description": "Office or direct phone number from signature block",
            "example": "(555) 123-4567",
        },
        {
            "key": "mobile",
            "label": "Mobile Number",
            "type": "string",
            "required": False,
            "description": "Cell/mobile number from signature block",
            "example": "(555) 987-6543",
        },
        {
            "key": "organization",
            "label": "Company / Organization",
            "type": "string",
            "required": False,
            "description": (
                "Company or organization name from signature block or email domain. "
                "For carrier employees, use the carrier name."
            ),
            "example": "Travelers Insurance",
        },
        {
            "key": "title",
            "label": "Job Title",
            "type": "string",
            "required": False,
            "description": "Job title or role from signature block",
            "example": "Senior Underwriter",
        },
        {
            "key": "role",
            "label": "Policy Role",
            "type": "string",
            "required": False,
            "description": (
                "The person's role relative to this insurance policy. "
                "Infer from context: carrier employees are likely Underwriters, "
                "brokerage colleagues are Placement Colleagues or Brokers, "
                "client employees are client contacts."
            ),
            "config_values": "contact_roles",
            "config_mode": "prefer",
            "example": "Underwriter",
        },
    ],
}

# ---------------------------------------------------------------------------
# Contact Bulk Import Schema — client-level mass contact import
# ---------------------------------------------------------------------------

CONTACT_BULK_IMPORT_SCHEMA: dict = {
    "name": "contact_bulk_import",
    "version": 1,
    "description": (
        "Extract contacts from email signatures, rosters, meeting notes, "
        "or any text containing people and their contact details"
    ),
    "is_array": True,
    "fields": [
        {
            "key": "name",
            "label": "Full Name",
            "type": "string",
            "required": True,
            "description": "Full name of the person (first and last name)",
            "example": "Jane Smith",
        },
        {
            "key": "email",
            "label": "Email Address",
            "type": "string",
            "required": False,
            "description": "Email address (from headers, cc/bcc, or signature block)",
            "example": "jane.smith@example.com",
        },
        {
            "key": "phone",
            "label": "Phone Number",
            "type": "string",
            "required": False,
            "description": "Office or direct phone number from signature block",
            "example": "(555) 123-4567",
        },
        {
            "key": "mobile",
            "label": "Mobile Number",
            "type": "string",
            "required": False,
            "description": "Cell/mobile number from signature block",
            "example": "(555) 987-6543",
        },
        {
            "key": "organization",
            "label": "Company / Organization",
            "type": "string",
            "required": False,
            "description": (
                "Company or organization name from signature block or email domain"
            ),
            "example": "Acme Insurance",
        },
        {
            "key": "title",
            "label": "Job Title",
            "type": "string",
            "required": False,
            "description": "Job title or role from signature block",
            "example": "Senior Underwriter",
        },
        {
            "key": "role",
            "label": "Account Role",
            "type": "string",
            "required": False,
            "description": (
                "The person's role relative to this client account. "
                "Infer from context: carrier employees are likely Underwriters, "
                "brokerage colleagues are Brokers or Account Managers, "
                "client employees are client contacts."
            ),
            "config_values": "contact_roles",
            "config_mode": "prefer",
            "example": "Underwriter",
        },
        {
            "key": "contact_type",
            "label": "Contact Type",
            "type": "string",
            "required": False,
            "description": (
                "Whether this person is a 'client' contact (works for the client), "
                "'internal' (works at your brokerage), or 'external' (works at a "
                "carrier, vendor, or third party). Infer from organization name."
            ),
            "example": "client",
        },
    ],
}


def generate_contact_bulk_import_prompt(conn, client_id: int) -> str:
    """Build a prompt for bulk contact import at the client level."""
    import policydb.config as _cfg

    client = conn.execute(
        "SELECT name, industry_segment FROM clients WHERE id = ?",
        (client_id,),
    ).fetchone()

    client_name = client["name"] if client else "Unknown"
    industry = (client["industry_segment"] or "") if client else ""

    # Gather context
    brokerage = _cfg.get("brokerage_name", "")
    contact_roles = _cfg.get("contact_roles", [])
    carriers_on_account = [
        r["carrier"]
        for r in conn.execute(
            "SELECT DISTINCT carrier FROM policies "
            "WHERE client_id = ? AND carrier IS NOT NULL AND carrier != ''",
            (client_id,),
        ).fetchall()
    ]
    existing_names = [
        r["name"]
        for r in conn.execute(
            "SELECT DISTINCT co.name FROM contacts co "
            "JOIN contact_client_assignments cca ON co.id = cca.contact_id "
            "WHERE cca.client_id = ?",
            (client_id,),
        ).fetchall()
    ]

    config_lists = {"contact_roles": contact_roles}
    parts: list[str] = []

    parts.append(
        "You are an insurance operations analyst. I will provide text that "
        "contains contact information — this may be email signatures, a contact "
        "roster, meeting notes, a distribution list, or any text mentioning "
        "people with their details. Your job is to extract all people and "
        "return their contact information as structured JSON.\n"
    )

    parts.append("## Output Format\n")
    parts.append(
        "Return a JSON **array** of contact objects. Each contact should have "
        "the fields listed below. Omit fields you cannot determine.\n"
    )

    parts.append("## Fields per Contact\n")
    for f in CONTACT_BULK_IMPORT_SCHEMA["fields"]:
        parts.append(_build_field_instruction(f, config_lists))

    parts.append("\n## Client Context\n")
    parts.append(f"- **Client**: {client_name}")
    if industry:
        parts.append(f"- **Industry**: {industry}")
    if carriers_on_account:
        parts.append(
            f"- **Known carriers on this account**: {', '.join(carriers_on_account)}"
        )
    if brokerage:
        parts.append(f"- **Your brokerage**: {brokerage}")
    if existing_names:
        parts.append(
            f"- **Already-known contacts** (skip or note if seen): "
            f"{', '.join(existing_names[:30])}"
        )

    parts.append("\n## Contact Type Inference Rules\n")
    if carriers_on_account:
        parts.append(
            f"- People from these carriers are 'external': "
            f"{', '.join(carriers_on_account)}"
        )
    if brokerage:
        parts.append(
            f"- People from '{brokerage}' or its subsidiaries are 'internal'"
        )
    parts.append("- All others are 'client' (unless context suggests otherwise)")
    parts.append(
        "- If you cannot determine contact_type, omit it (defaults to 'client')"
    )

    parts.append("\n## Extraction Rules\n")
    parts.append("- Extract contacts from email headers (From, To, CC, BCC)")
    parts.append("- Extract contact details from email signature blocks")
    parts.append("- Do NOT include generic/no-reply email addresses")
    parts.append(
        "- If the same person appears multiple times, merge into one entry "
        "with the most complete information"
    )

    # JSON template
    example = {}
    for f in CONTACT_BULK_IMPORT_SCHEMA["fields"]:
        if f.get("example"):
            example[f["key"]] = f["example"]
    parts.append("\n## JSON Template\n")
    parts.append(
        "Return ONLY valid JSON matching this structure (array of contacts):"
    )
    template = json.dumps([example], indent=2)
    parts.append(f"```json\n{template}\n```")

    parts.append("\n---\n")
    parts.append("**PASTE THE TEXT CONTAINING CONTACTS BELOW THIS LINE:**\n")

    return "\n".join(parts)


_VALID_CONTACT_TYPES = {"client", "internal", "external"}


def parse_contact_bulk_import_json(raw_text: str) -> dict:
    """Parse LLM JSON response for bulk contact import.

    Expects a JSON array of contact objects. Normalizes each using
    CONTACT_BULK_IMPORT_SCHEMA field definitions. Validates contact_type.

    Returns:
        {"ok": True, "contacts": [...], "warnings": [...], "count": N}
        or {"ok": False, "error": "...", "raw_text": "..."}
    """
    if len(raw_text) > _MAX_INPUT_BYTES:
        return {
            "ok": False,
            "error": "Input too large (max 500KB).",
            "raw_text": raw_text[:200],
        }

    # Try code fences first, then raw JSON — same strategy as contact extraction
    json_str = _extract_json_str(raw_text)

    if json_str is None or (json_str.startswith("{") and "[" in raw_text):
        for pattern in [_RE_JSON_CODE_FENCE, _RE_GENERIC_CODE_FENCE]:
            m = pattern.search(raw_text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("["):
                    json_str = candidate
                    break
        if json_str is None or not json_str.startswith("["):
            start = raw_text.find("[")
            if start != -1:
                depth = 0
                in_string = False
                escape_next = False
                for i in range(start, len(raw_text)):
                    ch = raw_text[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == "\\":
                        escape_next = True
                        continue
                    if ch == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            json_str = raw_text[start : i + 1]
                            break

    if json_str is None:
        return {
            "ok": False,
            "error": "No JSON found in input.",
            "raw_text": raw_text,
        }

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": f"Invalid JSON: {e}",
            "raw_text": raw_text,
        }

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": "Expected a JSON array of contacts.",
            "raw_text": raw_text,
        }

    fields = CONTACT_BULK_IMPORT_SCHEMA["fields"]
    all_warnings: list[str] = []
    contacts: list[dict] = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            all_warnings.append(f"Item [{i}] is not an object, skipping.")
            continue

        parsed, _raw, warnings = _parse_flat_fields(item, fields)
        for w in warnings:
            all_warnings.append(f"Contact [{i}]: {w}")

        if not parsed.get("name"):
            all_warnings.append(f"Contact [{i}]: Missing name, skipping.")
            continue

        # Validate contact_type
        ct = parsed.get("contact_type", "")
        if ct and ct.lower() not in _VALID_CONTACT_TYPES:
            all_warnings.append(
                f"Contact [{i}]: Invalid contact_type '{ct}', defaulting to 'client'."
            )
            parsed["contact_type"] = "client"
        elif ct:
            parsed["contact_type"] = ct.lower()

        parsed["_index"] = i
        contacts.append(parsed)

    if not contacts:
        return {
            "ok": False,
            "error": "No valid contacts extracted from JSON.",
            "raw_text": raw_text,
        }

    return {
        "ok": True,
        "contacts": contacts,
        "warnings": all_warnings,
        "count": len(contacts),
    }


def generate_contact_extraction_prompt(conn, policy_uid: str) -> str:
    """Build a prompt for extracting contacts from an email chain."""
    import policydb.config as _cfg

    policy = conn.execute(
        "SELECT p.*, c.name AS client_name, c.industry_segment "
        "FROM policies p JOIN clients c ON p.client_id = c.id "
        "WHERE p.policy_uid = ?",
        (policy_uid,),
    ).fetchone()

    client_name = policy["client_name"] if policy else "Unknown"
    carrier = (policy["carrier"] or "") if policy else ""
    policy_type = (policy["policy_type"] or "") if policy else ""

    contact_roles = _cfg.get("contact_roles", [])
    config_lists = {"contact_roles": contact_roles}

    parts: list[str] = []

    parts.append(
        "You are an insurance operations analyst. I will provide an email chain "
        "or correspondence thread related to an insurance policy. Your job is to "
        "extract all people mentioned (senders, recipients, cc'd, referenced in "
        "signature blocks) and return their contact information as structured JSON.\n"
    )

    parts.append("## Output Format\n")
    parts.append(
        "Return a JSON **array** of contact objects. Each contact should have "
        "the fields listed below. Omit fields you cannot determine.\n"
    )

    parts.append("## Fields per Contact\n")
    for f in CONTACT_EXTRACTION_SCHEMA["fields"]:
        parts.append(_build_field_instruction(f, config_lists))

    parts.append("\n## Policy Context\n")
    parts.append(f"- **Client**: {client_name}")
    if carrier:
        parts.append(f"- **Carrier**: {carrier}")
    if policy_type:
        parts.append(f"- **Coverage**: {policy_type}")

    parts.append("\n## Extraction Rules\n")
    parts.append("- Extract contacts from email headers (From, To, CC, BCC)")
    parts.append("- Extract contact details from email signature blocks")
    parts.append("- Do NOT include generic/no-reply email addresses")
    parts.append(
        "- If the same person appears multiple times, merge into one entry "
        "with the most complete information"
    )
    if carrier:
        parts.append(
            f"- For the role field, infer from context. People from "
            f"'{carrier}' are likely Underwriters or Claims contacts."
        )
    parts.append(
        "- Brokerage colleagues are likely 'Placement Colleague' or 'Broker'"
    )
    parts.append(
        "- People from the client organization are likely client contacts"
    )

    # JSON template
    example = {}
    for f in CONTACT_EXTRACTION_SCHEMA["fields"]:
        if f.get("example"):
            example[f["key"]] = f["example"]
    parts.append("\n## JSON Template\n")
    parts.append(
        "Return ONLY valid JSON matching this structure (array of contacts):"
    )
    template = json.dumps([example], indent=2)
    parts.append(f"```json\n{template}\n```")

    parts.append("\n---\n")
    parts.append("**PASTE THE EMAIL CHAIN BELOW THIS LINE:**\n")

    return "\n".join(parts)


def parse_contact_extraction_json(raw_text: str) -> dict:
    """Parse LLM JSON response for contact extraction.

    Expects a JSON array of contact objects. Normalizes each using
    CONTACT_EXTRACTION_SCHEMA field definitions.

    Returns:
        {"ok": True, "contacts": [...], "warnings": [...], "count": N}
        or {"ok": False, "error": "...", "raw_text": "..."}
    """
    if len(raw_text) > _MAX_INPUT_BYTES:
        return {
            "ok": False,
            "error": "Input too large (max 500KB).",
            "raw_text": raw_text[:200],
        }

    # Try code fences first, then raw JSON — same strategy as bulk import
    json_str = _extract_json_str(raw_text)

    # _extract_json_str only finds {} objects; also look for [] arrays
    if json_str is None or (json_str.startswith("{") and "[" in raw_text):
        for pattern in [_RE_JSON_CODE_FENCE, _RE_GENERIC_CODE_FENCE]:
            m = pattern.search(raw_text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("["):
                    json_str = candidate
                    break
        if json_str is None or not json_str.startswith("["):
            start = raw_text.find("[")
            if start != -1:
                depth = 0
                in_string = False
                escape_next = False
                for i in range(start, len(raw_text)):
                    ch = raw_text[i]
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == "\\":
                        escape_next = True
                        continue
                    if ch == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            json_str = raw_text[start : i + 1]
                            break

    if json_str is None:
        return {
            "ok": False,
            "error": "No JSON found in input.",
            "raw_text": raw_text,
        }

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": f"Invalid JSON: {e}",
            "raw_text": raw_text,
        }

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return {
            "ok": False,
            "error": "Expected a JSON array of contacts.",
            "raw_text": raw_text,
        }

    fields = CONTACT_EXTRACTION_SCHEMA["fields"]
    all_warnings: list[str] = []
    contacts: list[dict] = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            all_warnings.append(f"Item [{i}] is not an object, skipping.")
            continue

        parsed, _raw, warnings = _parse_flat_fields(item, fields)
        for w in warnings:
            all_warnings.append(f"Contact [{i}]: {w}")

        if not parsed.get("name"):
            all_warnings.append(f"Contact [{i}]: Missing name, skipping.")
            continue

        parsed["_index"] = i
        contacts.append(parsed)

    if not contacts:
        return {
            "ok": False,
            "error": "No valid contacts extracted from JSON.",
            "raw_text": raw_text,
        }

    return {
        "ok": True,
        "contacts": contacts,
        "warnings": all_warnings,
        "count": len(contacts),
    }
