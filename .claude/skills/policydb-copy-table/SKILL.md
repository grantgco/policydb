---
name: policydb-copy-table
description: >
  Copy Table Pattern for clipboard rich-paste in PolicyDB. Use when adding a new "Copy Table"
  button, modifying Outlook-safe HTML table rendering, or working on clipboard copy functionality.
  Covers the backend/frontend architecture, sort-aware row ordering, HTML table styling rules for
  Outlook, and current deployment locations.
---

# Copy Table Pattern (Clipboard Rich-Paste)

Reusable pattern for one-click "Copy Table" buttons that put both HTML (for Outlook/rich paste) and plain text (for plain editors) on the clipboard.

**Rule:** if the source table is sortable, the pasted table must match the user's current sort order. That is the default — not a nice-to-have. Wire every new Copy Table button into the sort-aware plumbing below, even if the initial table has no sort controls (they often get added later).

## Architecture

1. **Backend function** in `email_templates.py`:
   - `build_policy_table(conn, client_id, project_name, rows)` and `build_generic_table(rows, columns)` return `{"html": ..., "text": ...}`.
   - `apply_row_order(rows, order_param, id_key="id")` reorders a list of dicts to match a comma-separated id list. Pass-through when `order_param` is falsy. Unknown ids are dropped silently; rows without a match are appended at end (never lose data).
2. **API endpoint**: Accepts an `order: str | None = None` query param and returns `JSONResponse({"html": ..., "text": ...})`.
3. **JS function** in `base.html`: `copyPolicyTable(url, btn, sourceSelector?)`. When the 3rd argument is given, JS reads `[data-row-id]` elements in DOM order under that selector and appends `order=id1,id2,...` to the URL before fetching.

## Adding a New Copy Table Button

**Template** — pass the source container as the 3rd arg. Use the tbody id or any stable selector that encloses the rendered rows:

```html
<button type="button"
  onclick="copyPolicyTable('/your/endpoint', this, '#your-tbody-id')"
  class="text-xs text-gray-300 hover:text-marsh" title="Copy table to clipboard">Copy Table</button>
```

Every row inside that container must carry `data-row-id="..."`. Use whatever id your backend looks up by (DB `id`, `policy_uid`, composite `policy:POL-001` — whatever matches the `id_key` you pass to `apply_row_order`).

**Make the source table sortable by default.** Plain tables → add `data-sortable` to the `<table>` and `data-sort-key="..."` to each `<th>`. The generic sort handler in `base.html` (`initTableSort`) picks this up automatically and re-inits after HTMX swaps. Use `data-sort-value="..."` on cells where textContent isn't a clean sort key (currency, dates with badge clutter, etc.).

**Route** — apply the order, then render:

```python
from policydb.email_templates import build_generic_table, apply_row_order

@router.get("/your/endpoint")
def copy_table_foo(..., order: str | None = None, conn=Depends(get_db)):
    rows = [dict(r) for r in fetch_rows(conn, ...)]
    rows = apply_row_order(rows, order, id_key="id")  # match data-row-id
    return JSONResponse(build_generic_table(rows, columns))
```

For policy tables, use `build_policy_table(conn, client_id, rows=rows)` or the lower-level `_render_policy_table_html/text(rows)` pair. Row dicts need: `policy_type`, `carrier`, `access_point`, `policy_number`, `effective_date`, `expiration_date`, `premium`, `limit_amount`, `description`.

## HTML Table Styling (Outlook-Safe)

- **Inline styles only** — Outlook strips `<style>` blocks and CSS classes
- Header: Marsh navy `#003865`, white text, Noto Sans font
- Alternating rows: `#FFFFFF` / `#F7F3EE`
- Borders: `1px solid #B9B6B1`
- Currency/limit columns: right-aligned
- Carrier column: `"Carrier via Access Point"` when access_point exists

## When the source isn't a sortable table

Some Copy Table buttons don't have a visible sortable table behind them (e.g. the all-policies copy on `_tab_policies.html` is grouped across multiple `<tbody>` sections; Copy Schedule on `_summary_bar.html` exports a view that isn't rendered on the page). In those cases, omit the 3rd JS argument — the endpoint returns server-side order, unchanged.

## Current Deployment Locations

| Location | Endpoint | Template | id_key | Source selector |
|----------|----------|----------|--------|-----------------|
| Client contacts | `/clients/{id}/contacts/copy-table` | `_contacts.html` | `id` | `#client-contacts-tbody-{cid}` |
| Internal team | `/clients/{id}/team/copy-table` | `_team_contacts.html` | `id` | `#team-contacts-tbody-{cid}` |
| External stakeholders | `/clients/{id}/external/copy-table` | `_external_contacts.html` | `id` | `#external-contacts-tbody-{cid}` |
| Placement touchpoints | `/clients/{id}/placement/copy-table` | `_placement_colleagues.html` | `id` | `#placement-list` |
| Opportunities | `/clients/{id}/copy-table/opportunities` | `_opportunities.html` | `policy_uid` | `#opps-tbody-{cid}` |
| Project pipeline | `/clients/{id}/projects/pipeline/copy-table` | `_project_pipeline.html` | `id` | `#pp-tbody` |
| Project locations | `/clients/{id}/projects/locations/copy-table` | `_project_locations.html` | `id` | `#loc-tbody` |
| Policies per location | `/clients/{id}/copy-table?project=...` | `project.html` | `policy_uid` | `#project-policies-tbody-{pid}` |
| Renewal pipeline | `/renewals/copy-table?...` | `renewals.html` | `row_id` (`policy:UID` / `program:UID`) | `#renewals-table tbody` |
| Compliance matrix | `/compliance/client/{id}/copy-table` | `_summary_banner.html` | `row_id` (`{project}:{line}`) | _no sortable source on banner; leave 3rd arg unset_ |
| Request bundle items | `/clients/{id}/requests/{bid}/copy-table` | `_request_bundle.html` | `id` | _card list, not a table; 3rd arg unset_ |
| Compose slideover (issue/policy/project) | `/compose/copy-table?...` | `_compose_slideover.html` | `policy_uid` | _slideover; no visible table_ |
| All client policies (grouped) | `/clients/{id}/copy-table` | `_tab_policies.html` | `policy_uid` | _grouped across multiple tbodies; 3rd arg unset_ |
| Project group header (per group) | `/clients/{id}/copy-table?project=...` | `_project_header.html` | `policy_uid` | _grouped context; 3rd arg unset_ |
| Schedule of insurance | `/clients/{id}/copy-table/schedule` | `_summary_bar.html` | — | _whole-client export; no source table_ |
