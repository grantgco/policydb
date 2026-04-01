---
name: policydb-exports
description: >
  Data export system reference for PolicyDB — xlsx theming, CSV, HTML copy-table,
  PDF exports. Use when adding new export endpoints, fixing export styling, working
  on clipboard copy-table, or ensuring Marsh brand compliance in exported files.
  Covers _write_sheet(), build_generic_table(), exporter.py patterns, and the
  complete list of export endpoints.
---

# Data Export System

All exported files (xlsx, HTML tables, PDFs) must use the Marsh brand palette and shared utilities. Never create raw unstyled workbooks.

---

## XLSX Theming (Marsh Brand)

### Shared Constants (`src/policydb/exporter.py:918-928`)

```python
_HEADER_FILL = PatternFill("solid", fgColor="003865")    # Navy header background
_HEADER_FONT = Font(name="Noto Sans", bold=True, color="FFFFFF", size=11)
_DATA_FONT = Font(name="Noto Sans", size=11, color="3D3C37")
_ALT_ROW_FILL = PatternFill("solid", fgColor="F7F3EE")   # Warm neutral alternating rows
_BORDER_COLOR = "B9B6B1"
_THIN_BORDER = Border(left=Side(...), right=Side(...), top=Side(...), bottom=Side(...))
_CURRENCY_FMT = '"$"#,##0.00'
```

### `_write_sheet()` — The Core Utility

**Always use this** for xlsx sheet creation. Never manually style cells.

```python
from policydb.exporter import _write_sheet, _wb_to_bytes
from openpyxl import Workbook

wb = Workbook()
_write_sheet(wb, "Sheet Title", rows, col_widths={"Name": 30})
if wb.sheetnames and wb.sheetnames[0] == "Sheet":
    del wb["Sheet"]  # Remove default empty sheet
return Response(content=_wb_to_bytes(wb), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ...)
```

**What `_write_sheet()` does automatically:**
- Navy header row with white bold Noto Sans
- Noto Sans body text in Neutral 1000
- Alternating row fills (warm beige #F7F3EE)
- Thin warm gray borders on all cells
- Currency formatting ($X,XXX.XX) for columns named Premium, Limit, Deductible, etc.
- Auto-sized column widths (max 45)
- Friendly header labels via `_friendly()` (snake_case → Title Case)

**Input format:** `rows` must be a list of dicts. Keys become column headers.

```python
rows = [{"Project": "Main St", "Total Premium": 50000}, ...]
_write_sheet(wb, "Pipeline", rows)
```

### `_wb_to_bytes()` — Serialize Workbook

```python
content = _wb_to_bytes(wb)  # Returns bytes ready for Response
```

---

## HTML Copy-Table (Clipboard)

### Generic Table Builder

`build_generic_table()` in `src/policydb/email_templates.py` — Outlook-safe inline-styled HTML.

```python
from policydb.email_templates import build_generic_table

columns = [
    ("field_key", "Display Label", is_currency_bool),
    ("name", "Project", False),
    ("premium", "Premium", True),
]
result = build_generic_table(rows, columns)
# result = {"html": "<table>...</table>", "text": "tab-separated"}
```

**Route pattern:**
```python
@router.get("/{client_id}/projects/pipeline/copy-table")
def pipeline_copy_table(client_id: int, conn=Depends(get_db)):
    from fastapi.responses import JSONResponse
    from policydb.email_templates import build_generic_table
    rows = get_data(conn, client_id)
    columns = [("name", "Project", False), ("premium", "Premium", True)]
    return JSONResponse(build_generic_table(rows, columns))
```

**Frontend JS:** `copyPolicyTable(url, btn)` in `base.html` — fetches the JSON endpoint, writes HTML to clipboard.

### Policy-Specific Table

`build_policy_table()` in `email_templates.py` — specialized for policy schedule tables. Used by the existing `/clients/{id}/copy-table` endpoint.

### HTML Styling (Marsh brand, inline for Outlook)

- Header: `background-color:#003865; color:#FFFFFF; font-weight:600`
- Font: `'Noto Sans', Calibri, Arial, sans-serif; font-size:13px; color:#3D3C37`
- Borders: `1px solid #B9B6B1`
- Alternating rows: `background-color:#F7F3EE` on odd rows
- Currency: right-aligned, formatted via `_fmt_currency()`

---

## Export Endpoints

### Exporter Functions (`src/policydb/exporter.py`)

All use `_write_sheet()` + `_wb_to_bytes()`:

| Function | Sheets | Purpose |
|----------|--------|---------|
| `export_schedule_xlsx()` | 1 | Schedule of Insurance |
| `export_client_xlsx()` | 2 | Policies + Premium History |
| `export_full_xlsx()` | 4 | Policies, Contacts, Activities, Notes |
| `export_renewals_xlsx()` | 1 | Renewal pipeline with status filter |
| `export_request_bundle_xlsx()` | 1 | Client request bundle |
| `export_client_requests_xlsx()` | N | All request bundles (one sheet each) |
| `export_rfi_by_location_xlsx()` | N | RFI items grouped by location |
| `export_project_group_xlsx()` | 1 | All policies for a project group |
| `export_single_policy_xlsx()` | 1 | Single policy full details |
| `export_compliance_xlsx()` | 5 | Executive Summary, Compliance Matrix, Gap Detail, All Requirements, COPE |
| `export_book_review_xlsx()` | 8 | Instructions, Summary, Policies, Duplicates, Locations, Missing, Programs, Actions |
| `export_programs_xlsx()` | 1 | All programs with child policies |

### Route-Level Exports (in route files)

| Route | File | Method |
|-------|------|--------|
| `/clients/{id}/spreadsheet-export` | `clients.py` | `_write_sheet()` |
| `/policies/spreadsheet-export` | `policies.py` | `_write_sheet()` |
| `/activities/followups/spreadsheet-export` | `activities.py` | `_write_sheet()` |
| `/exports/download` | `exports.py` | `_write_sheet()` (generic) |
| `/clients/{id}/projects/pipeline/export` | `clients.py` | `_write_sheet()` |
| `/clients/{id}/projects/locations/export` | `clients.py` | `_write_sheet()` |

### Copy-Table Endpoints

| Route | Returns |
|-------|---------|
| `/clients/{id}/copy-table` | Policy table (HTML + text) |
| `/clients/{id}/projects/pipeline/copy-table` | Pipeline table (HTML + text) |
| `/clients/{id}/projects/locations/copy-table` | Locations table (HTML + text) |

---

## Adding a New Export

### XLSX Export Checklist

1. Build rows as `list[dict]` with display-friendly keys
2. Use `_write_sheet(wb, title, rows)` — never manual cell styling
3. Return via `_wb_to_bytes(wb)`
4. Currency columns auto-detected by name: Premium, Limit, Deductible, Commission, Exposure Amount, Broker Fee, Prior Premium

### Copy-Table Checklist

1. Define columns as `list[tuple[key, label, is_currency]]`
2. Call `build_generic_table(rows, columns)`
3. Return `JSONResponse(result)`
4. Add a button in the template: `onclick="copyPolicyTable('/path/to/copy-table', this)"`

### Anti-Patterns

- **Never** create a raw `Workbook()` and manually append rows without styling
- **Never** inline openpyxl Font/Fill/Border in route files — use `_write_sheet()`
- **Never** build HTML tables with non-brand colors
- **Never** use `<table>` without inline styles for clipboard copy (Outlook strips `<style>` tags)

---

## Compliance XLSX — Custom Extension

`export_compliance_xlsx()` extends the base theme with status-specific fills:

```python
_COMPLIANCE_FILLS = {
    "compliant": PatternFill("solid", fgColor="C6EFCE"),    # Light green
    "gap": PatternFill("solid", fgColor="FFC7CE"),          # Light red
    "partial": PatternFill("solid", fgColor="FFF2CC"),      # Light yellow
    "needs_review": PatternFill("solid", fgColor="B4C7E7"), # Light blue
    "waived": PatternFill("solid", fgColor="D9D9D9"),       # Gray
}
```

This is the correct pattern for domain-specific extensions: use base theme + add semantic colors.

---

## Key Files

| File | Purpose |
|------|---------|
| `src/policydb/exporter.py` | All export functions, `_write_sheet()`, `_wb_to_bytes()`, style constants |
| `src/policydb/email_templates.py` | `build_policy_table()`, `build_generic_table()`, HTML/text renderers |
| `src/policydb/web/routes/clients.py` | Client/pipeline/location export routes |
| `src/policydb/web/routes/exports.py` | Generic export download handler |
| `src/policydb/web/routes/activities.py` | Follow-up/renewal spreadsheet exports |
| `src/policydb/web/routes/policies.py` | Policy spreadsheet export |
| `src/policydb/web/routes/compliance.py` | Compliance xlsx export route |
