"""LLM extraction schema definitions and normalizer registry.

Defines the structured schemas that drive LLM-based document import:
- POLICY_EXTRACTION_SCHEMA: flat field list for policy/certificate parsing
- COMPLIANCE_EXTRACTION_SCHEMA: nested schema for contract requirement extraction
- NORMALIZER_REGISTRY: maps string names to callable post-processing functions

Date normalization is intentionally excluded from the registry — the parser
special-cases fields with type == "date" and uses dateparser directly.
"""

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
    "context_fields": ["client_name", "first_named_insured"],
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
