# Design System Consistency Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert policy rows from row-edit swap to contenteditable, standardize table styling across all views, convert status badges to pills with auto-colors for custom statuses, standardize quick forms, remove target="_blank" overuse.

**Architecture:** New PATCH `/policies/{uid}/cell` endpoint for per-field saves. Shared `initPolicyMatrix()` JS controller. `get_status_color()` Jinja2 global for auto-colored pills. Quick forms converted to single-line flex layout.

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-03-20-design-system-consistency-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Delete | `src/policydb/web/templates/policies/_policy_dash_row_edit.html` | Old edit form |
| Delete | `src/policydb/web/templates/policies/_policy_renew_row_edit.html` | Old edit form |
| Delete | `src/policydb/web/templates/policies/_policy_row_edit.html` | Old edit form |
| Create | `src/policydb/web/templates/policies/_policy_matrix_row.html` | Shared contenteditable row |
| Modify | `src/policydb/web/routes/policies.py` | PATCH cell endpoint, remove old edit endpoints, status color helper |
| Modify | `src/policydb/web/app.py` | Register get_status_color as Jinja2 global |
| Modify | `src/policydb/web/templates/policies/_policy_dash_row.html` | Contenteditable cells |
| Modify | `src/policydb/web/templates/policies/_policy_renew_row.html` | Contenteditable cells |
| Modify | `src/policydb/web/templates/policies/_policy_row.html` | Contenteditable cells |
| Modify | `src/policydb/web/templates/policies/_status_badge.html` | Pills instead of select |
| Modify | `src/policydb/web/templates/policies/_pipeline_table.html` | Table styling |
| Modify | `src/policydb/web/templates/renewals.html` | Table styling |
| Modify | `src/policydb/web/templates/policies/edit.html` | Quick form standardization |
| Modify | `src/policydb/web/templates/clients/detail.html` | Quick form + remove target="_blank" |
| Modify | `src/policydb/web/templates/dashboard.html` | Remove target="_blank" |
| Modify | ~30 templates | Remove target="_blank" from in-app links |

---

### Task 1: PATCH Cell Endpoint + Status Color System

**Files:**
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/app.py`

- [ ] **Step 1: Add PATCH /policies/{uid}/cell endpoint**

In `policies.py`, add:

```python
@router.patch("/{policy_uid}/cell")
async def policy_cell_save(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Save a single field on a policy (contenteditable cell save)."""
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"policy_type", "carrier", "policy_number", "premium",
               "effective_date", "expiration_date", "limit_amount", "deductible",
               "description", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Invalid field: {field}"}, status_code=400)

    uid = policy_uid.upper()
    policy = conn.execute("SELECT id FROM policies WHERE policy_uid = ?", (uid,)).fetchone()
    if not policy:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    formatted = value
    if field in ("premium", "limit_amount", "deductible"):
        from policydb.utils import parse_currency_with_magnitude
        num = parse_currency_with_magnitude(value)
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (num, uid))
        formatted = f"${num:,.0f}"
    elif field == "policy_type":
        from policydb.utils import normalize_coverage_type
        formatted = normalize_coverage_type(value)
        conn.execute("UPDATE policies SET policy_type = ? WHERE policy_uid = ?", (formatted, uid))
    elif field == "policy_number":
        from policydb.utils import normalize_policy_number
        formatted = normalize_policy_number(value)
        conn.execute("UPDATE policies SET policy_number = ? WHERE policy_uid = ?", (formatted, uid))
    elif field in ("effective_date", "expiration_date"):
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (value.strip() or None, uid))
        formatted = value.strip()
    else:
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (value.strip() or None, uid))
        formatted = value.strip()

    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})
```

- [ ] **Step 2: Add status color system**

Add to `policies.py` (or a shared location):

```python
_STATUS_COLORS = {
    "Not Started": ("gray-100", "gray-600", "gray-300"),
    "In Progress": ("blue-100", "blue-700", "blue-300"),
    "Quoted": ("purple-100", "purple-700", "purple-300"),
    "Pending Bind": ("amber-100", "amber-700", "amber-300"),
    "Bound": ("green-100", "green-700", "green-300"),
}

_COLOR_PALETTE = [
    ("pink-100", "pink-700", "pink-300"),
    ("sky-100", "sky-700", "sky-300"),
    ("yellow-100", "yellow-700", "yellow-300"),
    ("rose-100", "rose-700", "rose-300"),
    ("teal-100", "teal-700", "teal-300"),
    ("indigo-100", "indigo-700", "indigo-300"),
    ("orange-100", "orange-700", "orange-300"),
    ("lime-100", "lime-700", "lime-300"),
]

def get_status_color(status: str, all_statuses: list | None = None) -> tuple:
    if status in _STATUS_COLORS:
        return _STATUS_COLORS[status]
    if all_statuses:
        custom = [s for s in all_statuses if s not in _STATUS_COLORS]
        try:
            idx = custom.index(status)
            return _COLOR_PALETTE[idx % len(_COLOR_PALETTE)]
        except ValueError:
            pass
    return ("gray-100", "gray-600", "gray-300")
```

- [ ] **Step 3: Register as Jinja2 global**

In `src/policydb/web/app.py`, add:

```python
from policydb.web.routes.policies import get_status_color
templates.env.globals["get_status_color"] = get_status_color
```

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/policies.py src/policydb/web/app.py
git commit -m "feat: PATCH /policies/{uid}/cell endpoint + status color system with auto-palette"
```

---

### Task 2: Convert Dashboard Policy Rows to Contenteditable

**Files:**
- Modify: `src/policydb/web/templates/policies/_policy_dash_row.html`
- Delete: `src/policydb/web/templates/policies/_policy_dash_row_edit.html`
- Modify: `src/policydb/web/routes/policies.py` (remove old dash edit endpoints)
- Modify: `src/policydb/web/templates/policies/_pipeline_table.html` (table styling)

- [ ] **Step 1: Convert _policy_dash_row.html to contenteditable cells**

Replace all static display cells with contenteditable spans. Each cell gets:
- `contenteditable="true"` (for text/currency fields)
- `class="policy-cell"` for JS delegation
- `data-field="policy_type"` etc.
- `data-uid="{{ p.policy_uid }}"`

Status: replace `{% include "_status_badge.html" %}` with inline pill buttons using `get_status_color()`.

Dates: use `<input type="date">` with `onchange` save.

Premium/limit: contenteditable with currency formatting.

Keep: quick log button, compose button, any action buttons.

- [ ] **Step 2: Delete _policy_dash_row_edit.html**

- [ ] **Step 3: Remove old dash edit endpoints from policies.py**

Remove `policy_dash_edit_form` (GET) and `policy_dash_edit_post` (POST). Keep `policy_dash_log` (quick log).

- [ ] **Step 4: Standardize _pipeline_table.html table styling**

Update header to canonical pattern: `border-b border-gray-100 text-left text-xs text-gray-400 uppercase tracking-wide bg-gray-50`.

- [ ] **Step 5: Add JS controller to the dashboard page**

Add `initPolicyMatrix()` function that handles blur-save for `.policy-cell` elements, status pill clicks, and date change saves. Same event delegation pattern as carrier matrix.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: contenteditable dashboard policy rows with status pills"
```

---

### Task 3: Convert Renewals Policy Rows

**Files:**
- Modify: `src/policydb/web/templates/policies/_policy_renew_row.html`
- Delete: `src/policydb/web/templates/policies/_policy_renew_row_edit.html`
- Modify: `src/policydb/web/routes/policies.py`
- Modify: `src/policydb/web/templates/renewals.html`

- [ ] **Step 1: Convert _policy_renew_row.html to contenteditable**

Same pattern as Task 2. The renewals row has additional columns (milestones, team) — keep those read-only. Only convert editable fields.

- [ ] **Step 2: Delete _policy_renew_row_edit.html**

- [ ] **Step 3: Remove old renew edit endpoints**

Remove `policy_renew_edit_form` and `policy_renew_edit_post`. Keep `policy_renew_log`.

- [ ] **Step 4: Standardize renewals.html table styling**

Remove the sticky header shadow. Match canonical header pattern.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: contenteditable renewals policy rows with standardized table styling"
```

---

### Task 4: Convert Client Detail Policy Rows

**Files:**
- Modify: `src/policydb/web/templates/policies/_policy_row.html`
- Delete: `src/policydb/web/templates/policies/_policy_row_edit.html`
- Modify: `src/policydb/web/routes/policies.py`

- [ ] **Step 1: Convert _policy_row.html to contenteditable**

Same pattern. Client detail rows may have different columns — adapt the same contenteditable cells to the column layout used here.

- [ ] **Step 2: Delete _policy_row_edit.html**

- [ ] **Step 3: Remove old row edit endpoints**

Remove `policy_row_edit_form` and `policy_row_edit_post`. Keep `policy_row_log`.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: contenteditable client detail policy rows"
```

---

### Task 5: Quick Form Standardization

**Files:**
- Modify: `src/policydb/web/templates/policies/edit.html` (quick log form)
- Modify: `src/policydb/web/templates/clients/detail.html` (quick log form)

- [ ] **Step 1: Standardize policy edit quick log form**

Convert the 2-column grid form (lines 138-182 of edit.html) to single-line flex layout with green left border action zone. Keep all fields but compact the layout.

- [ ] **Step 2: Standardize client detail quick log form**

Convert the 4-column grid form to same single-line flex layout.

- [ ] **Step 3: Standardize button labels**

"Save Activity" → "Log" across all quick forms.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: standardized quick forms with compact single-line layout"
```

---

### Task 6: Remove target="_blank" from In-App Links

**Files:**
- ~30 templates

- [ ] **Step 1: Find all target="_blank" in templates**

```bash
grep -rn 'target="_blank"' src/policydb/web/templates/ | grep -v 'mailto' | grep -v 'export' | grep -v 'download'
```

- [ ] **Step 2: Remove from in-app navigation links**

Remove `target="_blank"` from:
- Client name links (`/clients/{id}`)
- Policy edit links (`/policies/{uid}/edit`)
- Contact detail links (`/contacts/{id}`)
- Project detail links
- Any other in-app `<a>` tags

Keep `target="_blank"` on:
- `mailto:` links
- Export/download links
- External URLs

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove target=_blank from in-app navigation links"
```

---

### Task 7: Print Safety Audit

- [ ] **Step 1: Grep for interactive elements missing no-print**

```bash
grep -rn 'contenteditable\|type="checkbox"\|onclick=' src/policydb/web/templates/ | grep -v 'no-print'
```

- [ ] **Step 2: Add no-print class where missing**

Add `no-print` to: edit buttons, pill selectors, action buttons, checkboxes, drag handles, compose buttons, snooze buttons — anything that shouldn't appear in print.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: add no-print class to all interactive elements for print safety"
```

---

### Task 8: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`

- [ ] **Step 2: Manual test**

1. **Dashboard:** Click cells to edit premium, carrier, policy type — verify blur saves with flash. Click status pills — verify one-click save. Verify no "Edit" button.
2. **Renewals:** Same contenteditable behavior. Verify table header matches dashboard.
3. **Client detail:** Same. Verify policy rows match pattern.
4. **Custom statuses:** Add a custom status in Settings. Verify it gets an auto-assigned color from the palette.
5. **Quick forms:** Verify single-line compact layout on policy and client pages.
6. **target="_blank":** Click client/policy links — verify they stay in same tab. Click mailto — verify new tab.
7. **Print:** Ctrl+P on any page — verify no edit controls or buttons in print preview.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for design system consistency"
```
