---
name: policydb-currency-parsing
description: >
  Currency parsing rules for PolicyDB — always use parse_currency_with_magnitude(), never raw float().
  Covers backend parsing, template display filters, XLSX export format, PATCH response format, and
  known violation locations. Use when working on any field that stores or displays money values.
---

# Currency Parsing Rules

## The Rule

**Every money field MUST use `parse_currency_with_magnitude()` from `src/policydb/utils.py`.**

Never use:
- `float()` directly on user input
- `parse_currency()` (the older, non-magnitude-aware version)
- `_float()` local helpers for money fields
- Python `%g` formatting (produces scientific notation for large/small values)

## Backend: Parsing User Input

```python
from policydb.utils import parse_currency_with_magnitude

# Handles all these formats:
parse_currency_with_magnitude("1.5M")      # → 1500000.0
parse_currency_with_magnitude("500k")      # → 500000.0
parse_currency_with_magnitude("$2,000,000") # → 2000000.0
parse_currency_with_magnitude("1m")        # → 1000000.0
parse_currency_with_magnitude("50000")     # → 50000.0
```

### Where to use it
- All Form POST handlers that accept premium, limit, deductible, broker_fee, attachment_point, participation_of, exposure_amount, prior_premium, commission_rate
- All PATCH cell-save endpoints for money fields
- Import functions (importer.py) — use `_parse_money` alias
- CLI commands that accept money input

### PATCH Response Format

Cell-save endpoints MUST return:
```json
{"ok": true, "formatted": "$1,500,000"}
```

The JS callback calls `flashCell()` when the formatted value differs from raw input (e.g., user typed "1.5m", cell shows "$1,500,000").

## Templates: Displaying Currency

```jinja2
{# Full format: $1,234,567.00 #}
{{ value | currency }}

{# Short format: $1.5M or $500K #}
{{ value | currency_short }}

{# NEVER do this: #}
${{ "{:,.0f}".format(value) }}   {# ← violates standard #}
{{ value | format_number }}       {# ← no currency symbol #}
```

## XLSX Export Format

```python
# In _write_sheet() and custom sheets:
currency_fmt = wb.add_format({'num_format': '"$"#,##0.00'})
```

Headers: Navy `#003865`, white Noto Sans 11pt bold.
Alternating rows: warm neutral fills.

## Known Violation Locations (from 2026-04-02 audit)

These locations still use `float()` or `_float()` for money fields and should be migrated:

| File | Lines | Fields |
|------|-------|--------|
| `policies.py` POST edit handlers | ~3335, ~4672 | premium, limit, deductible via `_float()` |
| `clients.py` | ~514, ~3262 | broker_fee via `_float()` |
| `reconcile.py` | ~1441 | limit_amount, deductible via `_f()` |
| `review.py` | ~410 | premium, limit via `_float()` |
| `compliance.py` | ~223 | required_limit, limit_amount via `float()` |
| `cli.py` | ~403, ~911 | premium via `click.prompt(type=float)` |
| `onboard.py` | ~182 | premium via float |

**Note:** The PATCH cell-save in `policies.py:~3499` correctly uses `parse_currency_with_magnitude()` — this is the pattern to follow.

## `parse_currency` vs `parse_currency_with_magnitude`

`parse_currency()` is the older function that strips `$` and commas but does NOT handle magnitude suffixes (`M`, `K`, `B`). It should be considered deprecated for new code. The importer alias `_parse_currency` now points to the magnitude-aware version.
