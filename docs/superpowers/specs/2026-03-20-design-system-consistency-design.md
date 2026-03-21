# Design System Consistency Pass — Design Spec

**Date:** 2026-03-20
**Status:** Draft
**Scope:** Convert policy row-edit swap to contenteditable pattern across dashboard, renewals, and client detail. Standardize table styling, status badges, hover states, and currency formatting across all views. Remove target="_blank" overuse.

---

## Problem Statement

PolicyDB has accumulated two coexisting edit patterns:
1. **Row-edit swap** (old) — click "Edit" → GET replaces row with form → fill inputs → POST → returns display row. Used on dashboard pipeline, renewals, client detail policy rows.
2. **Contenteditable matrix** (new) — click cell to edit, blur saves via PATCH. Used on carrier matrix, contacts, project pipeline, locations.

Additionally, table styling diverges across views: different border weights, hover colors, header shadows. Status badges use different markup in different contexts. 73 `target="_blank"` links open new tabs without clear policy.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Policy row edit pattern | Convert to contenteditable | Match the established matrix pattern used everywhere else |
| Slideover panels | Deferred to future project | Fundamental navigation paradigm shift — too large to bundle |
| Table styling | Standardize to program carriers spec section 5 | One pattern, documented, already proven |
| Status badges | Pill buttons everywhere (not selects) | Consistent with disposition pills, project pipeline pills |
| target="_blank" | Remove from same-app navigation, keep for external | In-app links should stay in same tab; only external (mailto, exports) open new tabs |
| Quick log forms | Keep as-is | Action forms (logging activities) are different from field editing |

---

## 1. Policy Row Contenteditable Conversion

### Views to convert

**Dashboard pipeline** (`_policy_dash_row.html` / `_policy_dash_row_edit.html`):
- Currently: click "Edit" → form row replaces display row → Save/Cancel buttons
- New: all fields are contenteditable cells. Blur saves via PATCH. No edit/save cycle.

**Renewals page** (`_policy_renew_row.html` / `_policy_renew_row_edit.html`):
- Same conversion pattern

**Client detail** (`_policy_row.html` / `_policy_row_edit.html`):
- Same conversion pattern

### Editable fields per row

| Field | Edit Pattern | PATCH endpoint |
|-------|-------------|----------------|
| Policy Type | Combobox (config list) | `PATCH /policies/{uid}/cell` |
| Carrier | Contenteditable text | Same |
| Policy Number | Contenteditable text | Same |
| Premium | Contenteditable with currency formatting | Same |
| Effective Date | Inline date input | Same |
| Expiration Date | Inline date input | Same |
| Renewal Status | Pill buttons (config list) | `POST /policies/{uid}/status` (existing) |
| Limit | Contenteditable with currency formatting | Same |
| Deductible | Contenteditable with currency formatting | Same |

### New PATCH endpoint

`PATCH /policies/{uid}/cell` — saves a single field on a policy.

```python
@router.patch("/{policy_uid}/cell")
async def policy_cell_save(request: Request, policy_uid: str, conn=Depends(get_db)):
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"policy_type", "carrier", "policy_number", "premium",
               "effective_date", "expiration_date", "limit_amount", "deductible",
               "description", "notes"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": "Invalid field"}, status_code=400)

    uid = policy_uid.upper()
    formatted = value
    if field in ("premium", "limit_amount", "deductible"):
        num = float(str(value).replace("$", "").replace(",", "").strip() or "0")
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
    else:
        conn.execute(f"UPDATE policies SET {field} = ? WHERE policy_uid = ?", (value.strip() or None, uid))
        formatted = value.strip()

    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})
```

### What happens to old edit templates

- `_policy_dash_row_edit.html` — **deleted** (no longer needed)
- `_policy_renew_row_edit.html` — **deleted**
- `_policy_row_edit.html` — **deleted**
- GET/POST `/{uid}/dash/edit`, `/{uid}/renew/edit`, `/{uid}/row/edit` endpoints — **removed**
- Quick log endpoints (`/{uid}/dash/log`, `/{uid}/renew/log`, `/{uid}/row/log`) — **kept** (these are activity forms, not field edits)

### Contenteditable row template pattern

Each policy row becomes a single `<tr>` with contenteditable cells:

```html
<tr id="policy-row-{{ p.policy_uid }}" class="border-b border-gray-50 hover:bg-gray-50 transition-colors">
  <td class="px-4 py-2.5">
    <span contenteditable="true" class="policy-cell outline-none font-medium text-gray-800"
          data-field="policy_type" data-uid="{{ p.policy_uid }}"
          data-placeholder="coverage type">{{ p.policy_type }}</span>
  </td>
  <td class="px-4 py-2.5">
    <span contenteditable="true" class="policy-cell outline-none text-gray-600"
          data-field="carrier" data-uid="{{ p.policy_uid }}"
          data-placeholder="carrier">{{ p.carrier or '' }}</span>
  </td>
  <!-- ... more cells ... -->
  <td class="px-4 py-2.5 text-right tabular-nums">
    <span contenteditable="true" class="policy-cell outline-none text-gray-800"
          data-field="premium" data-uid="{{ p.policy_uid }}"
          data-placeholder="$0">{% if p.premium %}{{ p.premium | currency }}{% endif %}</span>
  </td>
  <td class="px-4 py-2.5">
    <!-- Renewal status as pill buttons -->
    {% for s in renewal_statuses %}
    <button type="button" data-pp-field="status"
      onclick="policyStatusPill(this, '{{ p.policy_uid }}', '{{ s }}')"
      class="text-[10px] px-2 py-0.5 rounded border transition-colors
             {% if p.renewal_status == s %}bg-marsh text-white border-marsh{% else %}border-gray-200 text-gray-500 hover:border-marsh{% endif %}">
      {{ s }}
    </button>
    {% endfor %}
  </td>
  <!-- Quick log + compose buttons (kept as-is) -->
</tr>
```

### JS controller

One shared `initPolicyMatrix()` function that handles:
- Blur-save via `PATCH /policies/{uid}/cell`
- Currency formatting + flashCell
- Combobox for policy_type
- Tab navigation between cells
- Status pill click → POST to existing status endpoint

---

## 2. Table Styling Standardization

### Canonical pattern (from program carriers spec section 5a)

**Header row:**
```html
<tr class="border-b border-gray-100 text-left text-xs text-gray-400 uppercase tracking-wide bg-gray-50">
  <th class="px-4 py-2 font-medium">{Column}</th>
  <th class="px-4 py-2 font-medium text-right">{Currency}</th>
</tr>
```

**Data row:**
```html
<tr class="border-b border-gray-50 hover:bg-gray-50 transition-colors">
  <td class="px-4 py-2.5 text-gray-600">{value}</td>
  <td class="px-4 py-2.5 text-right font-medium text-gray-900 tabular-nums">{currency}</td>
</tr>
```

### Views to update

| View | Current | Change |
|------|---------|--------|
| Dashboard pipeline | `divide-y divide-gray-50`, `hover:bg-gray-50` | Match canonical. Remove `divide-y`, use `border-b border-gray-50`. |
| Renewals | `border-gray-200` + shadow on header, no row hover | Match canonical. Remove shadow, use `border-gray-100`. Add `hover:bg-gray-50`. |
| Follow-ups | `hover:bg-red-50/40` / `hover:bg-amber-50/40` contextual | Keep contextual colors for follow-ups (they serve a purpose — urgency signaling). Not a consistency issue. |
| Client detail | `hover:bg-gray-50 cursor-pointer` | Remove `cursor-pointer` (rows aren't clickable). Match canonical otherwise. |

---

## 3. Status Badge Standardization

### Current state
- Policy rows: `<select>` dropdown via `_status_badge.html`
- Opportunities: inline `opp_colors` dict with `<span>` badges
- Activities: inline `bg-gray-100` badges

### Target: pill buttons everywhere

Replace the `<select>` dropdown in `_status_badge.html` with pill buttons:

```html
{% for s in renewal_statuses %}
<button type="button"
  hx-post="/policies/{{ p.policy_uid }}/status" hx-vals='{"status": "{{ s }}"}'
  hx-swap="none"
  class="text-[10px] px-2 py-0.5 rounded border transition-colors
         {% if p.renewal_status == s %}{{ status_colors.get(s, 'bg-gray-100 text-gray-600 border-gray-200') }}
         {% else %}border-gray-200 text-gray-500 hover:border-marsh{% endif %}">
  {{ s }}
</button>
{% endfor %}
```

Status colors (standardized):
```python
status_colors = {
    "Not Started": "bg-gray-100 text-gray-600 border-gray-300",
    "In Progress": "bg-blue-100 text-blue-700 border-blue-300",
    "Quoted": "bg-purple-100 text-purple-700 border-purple-300",
    "Pending Bind": "bg-amber-100 text-amber-700 border-amber-300",
    "Bound": "bg-green-100 text-green-700 border-green-300",
}
```

Apply same pill pattern to opportunity statuses on the opportunities table.

---

## 4. target="_blank" Cleanup

### Policy

- **Same-app navigation** — remove `target="_blank"`. Client links, policy links, contact links stay in the same tab.
- **External/downloads** — keep `target="_blank"` for mailto links, file exports, PDF downloads.
- **Exception: policy edit from dashboard/renewals** — these open in same tab. The "Back to Client" link (already added) provides return navigation.

### Implementation

Search and replace across all templates:
1. Remove `target="_blank"` from client name links (`/clients/{id}`)
2. Remove `target="_blank"` from policy edit links (`/policies/{uid}/edit`)
3. Remove `target="_blank"` from contact detail links (`/contacts/{id}`)
4. Keep `target="_blank"` on `mailto:` links
5. Keep `target="_blank"` on export/download links

Estimated: ~50 of 73 occurrences removed.

---

## 5. Print Safety Audit

Ensure all interactive elements have `no-print` class:
- Edit buttons, pill selectors, action buttons
- Checkboxes, drag handles
- Compose buttons, snooze shortcuts

Quick grep + add `no-print` where missing.

---

## 6. Files Affected

### Delete
- `src/policydb/web/templates/policies/_policy_dash_row_edit.html`
- `src/policydb/web/templates/policies/_policy_renew_row_edit.html`
- `src/policydb/web/templates/policies/_policy_row_edit.html`

### Create
- (none — all changes are modifications to existing files)

### Modify
- `src/policydb/web/routes/policies.py` — add PATCH cell endpoint, remove old edit GET/POST endpoints
- `src/policydb/web/templates/policies/_policy_dash_row.html` — contenteditable cells
- `src/policydb/web/templates/policies/_policy_renew_row.html` — contenteditable cells
- `src/policydb/web/templates/policies/_policy_row.html` — contenteditable cells
- `src/policydb/web/templates/policies/_status_badge.html` — pills instead of select
- `src/policydb/web/templates/policies/_pipeline_table.html` — table styling
- `src/policydb/web/templates/renewals.html` — table styling
- `src/policydb/web/templates/clients/detail.html` — table styling, remove target="_blank"
- `src/policydb/web/templates/dashboard.html` — remove target="_blank"
- ~30 other templates — target="_blank" removal

---

## 7. Quick Form Standardization

### Current state
Activity log forms on policy edit and client detail pages use different layouts (2-column grid vs 4-column grid), different border radii (4px vs 6px), different button labels ("Save Activity" vs "Save Activity"), and different sizing.

### Canonical quick form pattern

**Layout:** Single-line flex layout. All fields in one row. Green left border as "action zone" indicator.

```
┌───────────────────────────────────────────────────────────────────┐
│ ▌ Type [Call▾]  Contact [____]  Subject* [___________]  Hrs [_]  │
│ ▌ Follow-Up [____]  ☐ COR   [Log]                               │
└───────────────────────────────────────────────────────────────────┘
```

**Styling standards:**
- Labels: `text-[10px] text-gray-500`
- Inputs: `text-xs border border-gray-300 rounded px-2 py-1` (12px, 4px radius)
- Primary button: `bg-marsh text-white text-xs font-medium px-4 py-1.5 rounded` — verb label ("Log", "Save", "Send")
- Secondary button: `border border-gray-300 text-gray-500 text-xs px-3 py-1.5 rounded`
- Action zone: `border-l-3 border-marsh bg-green-50/50` left border + light green background
- Layout: `flex items-end gap-3 flex-wrap` — single-line, wraps on narrow screens

**Apply to:**
- Policy edit quick log form (lines 138-182 of edit.html)
- Client detail quick log form (lines 560-595 of detail.html)
- Renewals bulk log form
- Any other inline action forms

---

## 8. Custom Status Color System

### Problem
When users add custom renewal statuses in Settings, the status pills need colors. Currently only the built-in statuses have color mappings.

### Solution: auto-assign from a rotating palette

Built-in statuses get fixed colors:
```python
_STATUS_COLORS = {
    "Not Started": ("gray-100", "gray-600", "gray-300"),
    "In Progress": ("blue-100", "blue-700", "blue-300"),
    "Quoted": ("purple-100", "purple-700", "purple-300"),
    "Pending Bind": ("amber-100", "amber-700", "amber-300"),
    "Bound": ("green-100", "green-700", "green-300"),
}
```

Custom statuses rotate through a palette based on their index:
```python
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

def get_status_color(status: str, all_statuses: list[str]) -> tuple[str, str, str]:
    if status in _STATUS_COLORS:
        return _STATUS_COLORS[status]
    custom_idx = [s for s in all_statuses if s not in _STATUS_COLORS].index(status)
    return _COLOR_PALETTE[custom_idx % len(_COLOR_PALETTE)]
```

Register as a Jinja2 global so templates can call `{{ get_status_color(status, renewal_statuses) }}`.

---

## 9. Edge Cases

| Scenario | Behavior |
|----------|----------|
| User clicks cell but doesn't change value | Blur fires PATCH with same value. Server returns same formatted value. No flash. No wasted write (could add client-side dirty check). |
| Policy type combobox with unknown value | `normalize_coverage_type()` title-cases it. Accepted. |
| Premium entered as "$15M" | `parse_currency_with_magnitude()` handles it → saves 15000000, returns "$15,000,000" |
| Status pill click on read-only views (briefing, print) | Pills hidden via `no-print`. Briefing uses display-only badges, not interactive pills. |
| Quick log form still works | Kept as-is — separate action form, not a field edit. Toggles below the row. |
| Renewal status change triggers auto-review | Existing POST `/policies/{uid}/status` already handles this. Pills use the same endpoint. |
| Tab navigation between cells | Same pattern as carrier matrix — Tab advances, Shift+Tab goes back. |
| Multiple users editing same policy | Not a concern — single-user local app. |
