---
name: policydb-route-patterns
description: >
  FastAPI route patterns for PolicyDB — route ordering rules, HTMX row edit pattern (3 variants),
  PATCH response format, and common pitfalls. Use when adding new routes, modifying route modules,
  or debugging 404/capture issues.
---

# Route Patterns

## Route Ordering Rule (CRITICAL)

**Literal routes MUST be registered BEFORE parameterized routes.**

FastAPI matches routes in registration order. If `/{policy_uid}/row` is registered before `/new`, then a request to `/new` will be captured by `/{policy_uid}` with `policy_uid="new"`.

```python
# CORRECT — literals first
@router.get("/search")          # ← literal
@router.get("/spreadsheet")     # ← literal
@router.get("/new")             # ← literal
@router.post("/new")            # ← literal
@router.get("/{uid}/row")       # ← parameterized (AFTER literals)
@router.get("/{uid}/row/edit")  # ← parameterized

# WRONG — will cause 404 on /new
@router.get("/{uid}/row")       # ← captures "new" as uid
@router.get("/new")             # ← never reached
```

**After adding any route:** Verify ordering with:
```python
python -c "
from policydb.web.app import app
for r in app.routes:
    if hasattr(r, 'path') and '/policies/' in r.path:
        print(r.path, r.methods)
"
```

### Comment Convention

When placing a literal route, add a comment:
```python
# MUST be before /{policy_uid} to avoid route capture
@router.get("/new", response_class=HTMLResponse)
```

## HTMX Row Edit Pattern

Every pipeline/table view needs these endpoint variants per row:

```
GET  /{uid}/row/edit  → inline edit form (replaces #row-{uid})
POST /{uid}/row/edit  → saves, returns display row
GET  /{uid}/row       → restore display row (Cancel)
GET  /{uid}/row/log   → inline activity log form
POST /{uid}/row/log   → saves, restores display row
```

Three row context variants exist:
- `row` — client detail policy table
- `dash` — dashboard pipeline
- `renew` — renewals page

Each has its own template partial (e.g., `_policy_row.html`, `_policy_dash_row.html`, `_policy_renew_row.html`).

## PATCH Cell-Save Response

All PATCH endpoints for inline cell editing MUST return:

```json
{"ok": true, "formatted": "..."}
```

The `formatted` value is what gets displayed in the cell. When it differs from the raw input, the JS calls `flashCell()` to show the green highlight animation.

### Money fields
```python
from policydb.utils import parse_currency_with_magnitude
parsed = parse_currency_with_magnitude(raw_value)
formatted = f"${parsed:,.2f}" if parsed else ""
return {"ok": True, "formatted": formatted}
```

### Phone fields
```python
from policydb.utils import format_phone
formatted = format_phone(raw_value)
return {"ok": True, "formatted": formatted}
```

### Email fields
```python
from policydb.utils import clean_email
formatted = clean_email(raw_value)
return {"ok": True, "formatted": formatted}
```

## Template Context Requirements

Always pass these to templates that need them:
- `renewal_statuses` — any template rendering `_status_badge.html`
- `opportunity_statuses` — opportunity-related templates
- Pipeline rows must have `_attach_milestone_progress(conn, rows)` called first
- Pipeline rows need `_attach_client_ids(conn, rows)` for client linking

## Config Lists in Routes

**Never hardcode lists.** Always read from config:
```python
# CORRECT
statuses = cfg.get("renewal_statuses")

# WRONG
statuses = ["Not Started", "In Progress", "Pending Bind", "Bound"]
```

## SQL Safety

All user-supplied values use parameterized `?` placeholders. For dynamic column names (e.g., PATCH field save), validate against an allowlist first:

```python
_EDITABLE_FIELDS = {"premium", "limit_amount", "carrier", ...}

if field not in _EDITABLE_FIELDS:
    return JSONResponse({"ok": False}, status_code=400)

conn.execute(f"UPDATE policies SET {field} = ? WHERE id = ?", (value, pid))
```
