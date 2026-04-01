---
name: policydb-copy-table
description: >
  Copy Table Pattern for clipboard rich-paste in PolicyDB. Use when adding a new "Copy Table"
  button, modifying Outlook-safe HTML table rendering, or working on clipboard copy functionality.
  Covers the backend/frontend architecture, HTML table styling rules for Outlook, and current
  deployment locations.
---

# Copy Table Pattern (Clipboard Rich-Paste)

Reusable pattern for one-click "Copy Table" buttons that put both HTML (for Outlook/rich paste) and plain text (for plain editors) on the clipboard.

## Architecture

1. **Backend function** in `email_templates.py`: `build_policy_table(conn, client_id, project_name, rows)` returns `{"html": ..., "text": ...}`. Also: `_render_policy_table_html(rows)` and `_render_policy_table_text(rows)` for rendering pre-fetched rows.
2. **API endpoint**: Returns `JSONResponse({"html": ..., "text": ...})` — e.g. `GET /clients/{id}/copy-table?project=...`
3. **JS function** in `base.html`: `copyPolicyTable(url, btn)` — fetches the endpoint, writes both MIME types via `ClipboardItem`, with `writeText()` fallback.

## Adding a New Copy Table Button

**Template** — add a button that calls `copyPolicyTable(url, btn)`:
```html
<button type="button"
  onclick="copyPolicyTable('/your/endpoint?params=...', this)"
  class="text-xs text-gray-300 hover:text-marsh" title="Copy table to clipboard">Copy Table</button>
```

**Route** — return JSON with both formats:
```python
from policydb.email_templates import build_policy_table
result = build_policy_table(conn, client_id, project_name=project)
return JSONResponse(result)
```

For custom row sources (e.g. renewal pipeline), build row dicts with keys: `policy_type`, `carrier`, `access_point`, `policy_number`, `effective_date`, `expiration_date`, `premium`, `limit_amount`, `description` — then pass as `rows=` parameter.

## HTML Table Styling (Outlook-Safe)

- **Inline styles only** — Outlook strips `<style>` blocks and CSS classes
- Header: Marsh navy `#003865`, white text, Noto Sans font
- Alternating rows: `#FFFFFF` / `#F7F3EE`
- Borders: `1px solid #B9B6B1`
- Currency/limit columns: right-aligned
- Carrier column: `"Carrier via Access Point"` when access_point exists

## Current Deployment Locations

| Location | Endpoint | Template |
|----------|----------|----------|
| Project group header | `/clients/{id}/copy-table?project=...` | `_project_header.html` |
| Client policies section | `/clients/{id}/copy-table` | `_tab_policies.html` |
| Project detail page | `/clients/{id}/copy-table?project=...` | `project.html` |
| Renewal pipeline | `/renewals/copy-table?window=...&status=...&client_id=...` | `renewals.html` |
