---
name: policydb-prompt-builder
description: AI Export Prompt Builder reference for PolicyDB. Use when working on prompt assembly, template management, depth tiers, data context formatting, or adding new record types to the assembler registry. Covers the assembler architecture, template data model, route endpoints, and UI patterns.
---

# Prompt Builder — Reference

## Overview

The Prompt Builder assembles structured text blocks from database records for pasting into external LLMs. No API calls — pure text assembly and clipboard export.

**Route:** `/prompt-builder` (Tools dropdown in nav)
**Route module:** `src/policydb/web/routes/prompt_builder.py`
**Assembler:** `src/policydb/prompt_assembler.py`
**Migration:** 135 (`prompt_templates` + `prompt_export_log`)

---

## Assembler Architecture

### Registry Pattern

Every record type has a registered assembler function:

```python
from policydb.prompt_assembler import register

@register("my_type")
def assemble_my_type(conn, record_id, depth=DEPTH_FULL):
    """Return markdown string."""
    ...
```

**Adding a new record type:**
1. Add `@register("type_name")` function in `prompt_assembler.py`
2. Add the type string to relevant templates' `required_record_types` JSON arrays
3. If it's a primary selectable type, add to `PRIMARY_RECORD_TYPES` list and add a search query in `prompt_builder.py`'s `record_search()` route

### Depth Tiers

| Tier | Constant | Use | Behavior |
|------|----------|-----|----------|
| 1 | `DEPTH_FULL` | Primary record | All non-null fields, full collections |
| 2 | `DEPTH_SUMMARY` | Related records (default) | Key fields only, collections capped at 5 |
| 3 | `DEPTH_REFERENCE` | Distant relations | Name + ID + status, one line |
| 4 | (omit) | Never | Audit logs, system fields, timestamps |

### Registered Assemblers

**Primary types** (user selects in UI):
- `client` — queries `clients` table
- `policy` — queries `policies` table + sub-coverages + policy contacts
- `renewal` — calls `assemble_policy()` + adds timeline milestones, days-to-renewal, checklist
- `issue` — queries `activity_log WHERE item_kind='issue'` + linked activities + checklist

**Related types** (included in templates as supporting data):
- `policies` — all policies for a client (active at tier 2, expired at tier 3, capped at 3)
- `renewals` — renewal-state policies for a client (within 365 days)
- `issues` — open at tier 2, closed at tier 3 (capped at 5 most recent)
- `follow_ups` — last 5 at tier 2, older omitted
- `milestones` — from `policy_timeline` + `policy_milestones`; < 10 = all at tier 2, else incomplete only
- `contacts` — ≤ 5 = all at tier 2, else primary at tier 2, rest at tier 3

### Relationship Key Resolution

`_resolve_keys(conn, primary_type, record_id)` returns `{client_id, policy_id, policy_uid, issue_id}`:

| Primary | Keys resolved |
|---------|---------------|
| client | `client_id = record_id` |
| policy/renewal | `policy_id = record_id`, `client_id` + `policy_uid` from policies table |
| issue | `issue_id = record_id`, `client_id` + `policy_id` from activity_log |

### Truncation

```python
_truncated_list(items, limit, noun="items")
# Returns items[:limit] + ["[N additional {noun} not shown]"]
```

Collections exceeding limits get a notice appended (e.g., "[12 additional follow-ups not shown]").

### Formatting Helpers

- `_fmt_currency(v)` → `$1,234,567` (no cents)
- `_fmt_date(d)` → `Month DD, YYYY` (ISO string or date object)
- `_field(label, value, fmt=None)` → `**Label:** value\n` or `""` if None/empty

---

## Data Model

### prompt_templates

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| name | TEXT | Template name |
| deliverable_type | TEXT | email, report, agenda, narrative, memo, schedule, submission, other |
| description | TEXT | Shown when selecting |
| system_prompt | TEXT | LLM role/behavior instruction |
| closing_instruction | TEXT | Final directive after data block |
| required_record_types | TEXT (JSON) | `["client","policies","issues"]` — first = primary type for filtering |
| depth_overrides | TEXT (JSON) | `{"issues": 1}` — override default tier per related type |
| active | INTEGER | 1 = available in builder |
| is_builtin | INTEGER | 1 = cannot edit, only duplicate |
| created_at, updated_at | DATETIME | Auto |

### prompt_export_log

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto |
| template_id | INTEGER FK | → prompt_templates |
| record_type | TEXT | client, policy, renewal, issue |
| record_id | INTEGER | ID of the selected record |
| exported_at | DATETIME | Logged on clipboard copy |

---

## Template Filtering

When user selects a primary record type (e.g., "Client"), templates are filtered:
- Show template if `required_record_types[0] == selected_type`
- Templates with empty `[]` show for all types

---

## Routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/prompt-builder` | Main page (Builder + Manage tabs) |
| GET | `/prompt-builder/records?type=...&q=...` | HTMX: searchable record dropdown |
| GET | `/prompt-builder/templates-for-type?type=...` | HTMX: filtered template cards |
| POST | `/prompt-builder/preview` | HTMX: assembled prompt output |
| POST | `/prompt-builder/log-export` | JSON: log copy event |
| GET | `/prompt-builder/templates` | HTMX: template manager tab |
| POST | `/prompt-builder/templates/new` | Create custom template |
| GET/POST | `/prompt-builder/templates/{id}/edit` | Edit form / save |
| POST | `/prompt-builder/templates/{id}/duplicate` | Clone (including built-ins) |
| POST | `/prompt-builder/templates/{id}/toggle` | Toggle active status |
| POST | `/prompt-builder/templates/{id}/delete` | Delete custom template |

---

## Output Format

```
{system_prompt}

---

## {Primary Record Type}
**Field:** Value
**Field:** Value

## {Related Type}
- Item 1
- Item 2
[N additional items not shown]

*Data as of: Month DD, YYYY at HH:MM AM/PM*

---

{closing_instruction}
```

- Null/empty fields omitted entirely
- Dates formatted as `Month DD, YYYY`
- Currency formatted as `$1,234,567`
- Issues sorted: open first, then by due date ascending
- "Data as of:" timestamp footer on every context block

---

## Built-in Seed Templates (7)

1. **Renewal Status Email** — `["renewal","client","milestones","issues"]` — email
2. **Open Items Call Agenda** — `["client","issues","follow_ups"]` — agenda
3. **Stewardship Report Shell** — `["client","policies","renewals","issues"]` — report
4. **Submission Cover Note** — `["policy","client","renewals"]` — submission
5. **Client Coverage Narrative** — `["client","policies"]` — narrative
6. **Issue Escalation Memo** — `["issue","client"]` — memo — overrides: `{"issues":1,"follow_ups":1}`
7. **New Business Prospect Brief** — `["client","policies"]` — narrative

---

## Future: Record Detail Entry Point

The assembler and routes are designed for a future "Build Prompt" button on individual record detail views (client page, policy page, etc.). When implementing:
1. Pass `record_type` + `record_id` as query params to `/prompt-builder`
2. Pre-select the record type and record in the UI
3. No refactoring of the assembler needed — the same `assemble_prompt()` function works
