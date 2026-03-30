# LLM Import — Sub-Coverage Extraction & Confirmation

**Date:** 2026-03-30
**Status:** Approved

## Summary

Enhance the single-policy AI import flow to also extract sub-coverages (coverage types, limits, deductibles) from the LLM response, present them in a confirmation panel, and allow the user to selectively apply them to the policy.

## Current State

- `POLICY_EXTRACTION_SCHEMA` extracts policy-level fields and locations/COPE — no sub-coverages.
- `POLICY_BULK_IMPORT_SCHEMA` already defines a `sub_coverages` nested group (coverage_type, limit, deductible, notes).
- The `_ai_review_panel.html` shows Section A (policy field diffs) and Section B (location/COPE diffs) — no sub-coverage section.
- Sub-coverage CRUD endpoints already exist: `POST /policies/{uid}/sub-coverages`, `PATCH /policies/{uid}/sub-coverages/{id}`, `DELETE`.
- DB table `policy_sub_coverages` supports 13+ columns; we extract the essential 5.

## Design

### 1. Schema Change (`llm_schemas.py`)

Add `sub_coverages` to `POLICY_EXTRACTION_SCHEMA["nested_groups"]`:

```python
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
}
```

No changes to prompt generation or JSON template code — the generic `nested_groups` handling in `generate_extraction_prompt()`, `generate_json_template()`, and `parse_llm_json()` already handles arbitrary nested groups.

### 2. Parse Logic (`policies.py` — `_ai_import_parse_inner`)

After the location diff block, add sub-coverage diff logic:

1. Extract `sub_coverages_parsed = result["parsed"].get("sub_coverages", [])`
2. Load existing: `existing_subs = _get_sub_coverages(conn, merged["id"])`
3. For each extracted sub-coverage:
   - Fuzzy-match `coverage_type` against existing sub-coverages using `fuzz.ratio()` (threshold ≥ 75)
   - If matched: build per-field diffs (`limit_amount`, `deductible`, `coverage_form`, `notes`) — same `{field, label, current, extracted, is_fill}` pattern
   - If no match: mark as `match_type: "new"`, all fields are fills
4. Build `ai_sub_coverage_data` list, each entry:
   ```python
   {
       "index": int,
       "coverage_type": str,       # extracted coverage type
       "extracted": dict,          # all extracted fields
       "existing": dict | None,    # matched existing sub-coverage row
       "existing_id": int | None,  # DB id if updating
       "diffs": list[dict],        # per-field diffs
       "match_type": "matched" | "new",
       "match_score": int,
   }
   ```
5. Pass `ai_sub_coverage_data` to template context.

### 3. Review Panel UI (`_ai_review_panel.html`)

Add Section C after locations. Purple/violet theme to distinguish from policy (indigo) and location (emerald):

- **Card**: `border-violet-200 bg-violet-50/20`
- **Header**: "Sub-Coverages" with count badge
- **Per sub-coverage**: coverage type as header, match badge if updating, per-field diff rows with checkboxes (pre-checked for fills)
- **Actions**: "Apply Sub-Coverages" button + Select All / Clear All links
- **Success state**: Green confirmation with count of applied sub-coverages

### 4. Apply JS Function

`applySubCoverages()` in the review panel script:

1. Collect checked sub-coverages and their selected fields from the parsed JSON
2. For each new sub-coverage:
   - `POST /policies/{uid}/sub-coverages` with `coverage_type` body
   - Then `PATCH /policies/{uid}/sub-coverages/{new_id}` with financial fields
3. For each existing sub-coverage update:
   - `PATCH /policies/{uid}/sub-coverages/{existing_id}` with selected fields
4. On success, replace the card with green confirmation
5. Currency fields sent as raw numbers (the PATCH endpoint handles formatting)

## Files Changed

| File | Change |
|------|--------|
| `src/policydb/llm_schemas.py` | Add `sub_coverages` to `POLICY_EXTRACTION_SCHEMA["nested_groups"]` |
| `src/policydb/web/routes/policies.py` | Add sub-coverage diff logic to `_ai_import_parse_inner()`, pass `ai_sub_coverage_data` to template |
| `src/policydb/web/templates/policies/_ai_review_panel.html` | Add Section C for sub-coverage review + JS apply function |

## Not Changed

- No new routes (reuses existing sub-coverage CRUD endpoints)
- No migrations (table already has all needed columns)
- No new JS libraries
- No config changes
- Prompt generation and JSON template code unchanged (generic nested_groups handling)
