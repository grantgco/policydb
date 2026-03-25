# Compliance Requirement Slideover + Auto-Status

## Problem

The compliance requirement editing experience is clunky. The current inline edit form (`_requirement_row_edit.html`) crams 8+ fields into a 4-column grid inside a table cell. There is no side-by-side comparison of requirement limits vs. policy coverage. Compliance status is fully manual — users must remember to set "Compliant" even when a linked policy clearly satisfies the requirement. Additionally, Waived and N/A requirements incorrectly drag down the compliance percentage.

## Solution

Replace the inline edit form with a **right slideover detail panel** that shows requirement fields on the left and policy coverage comparison on the right. Add an **auto-compliance engine** that computes status from linked policy data. Fix the percentage calculation to exclude Waived/N/A from the denominator.

---

## 1. Auto-Compliance Engine

### New function: `compute_auto_status(requirement, policy) -> str`

**File:** `src/policydb/compliance.py`

Accepts a governing requirement dict and its primary linked policy dict. Returns one of: `"Compliant"`, `"Partial"`, `"Gap"`.

**Logic:**

| Condition | Result |
|-----------|--------|
| No policy linked | Gap |
| Policy `limit_amount` < requirement `required_limit` | Gap |
| Requirement has `max_deductible` and policy `deductible` > `max_deductible` | Gap |
| Policy limits pass but requirement has `required_endorsements` (non-empty) | Partial |
| Policy limits pass and no endorsements required | Compliant |

**Endorsement limitation:** The `policies` table has no endorsements column. Endorsement coverage cannot be verified against policy data. Any non-empty `required_endorsements` on a requirement always results in "Partial" regardless of what the policy actually carries. This is a deliberate design choice — endorsement verification remains a human judgment.

**Override preservation:** Auto-compute is skipped when the current `compliance_status` is `"Waived"`, `"N/A"`, or a manually confirmed `"Compliant"` (set via the "Confirm Compliant" button). These represent human decisions that should not be overridden by automation. Internally, when a user clicks "Confirm Compliant" on a Partial requirement, a flag `status_manual_override = 1` is set on the `coverage_requirements` row. Auto-compute checks this flag and skips the row. The flag is cleared when a policy link changes (add/remove/swap primary), forcing re-evaluation.

**Trigger points:**
- Policy link added, removed, or primary changed (in link endpoints). Clears `status_manual_override` flag.
- Requirement limit/deductible fields edited (in cell-patch endpoint)
- Page load: `get_client_compliance_data()` calls auto-compute for governing requirements with linked policies, but **only** for rows with `compliance_status = 'Needs Review'`. This avoids the complexity of staleness detection (no `last_computed_at` column needed).

### Percentage fix

**File:** `src/policydb/compliance.py`, `compute_compliance_summary()`

Already fixed in this branch. The denominator now excludes Waived and N/A:

```python
applicable = total - counts["waived"] - counts["na"]
pct = round(counts["compliant"] / applicable * 100) if applicable else (100 if total else 0)
```

---

## 2. Requirement Slideover Panel

### Trigger

Clicking "Edit" on any requirement row in the location detail view or compliance matrix opens the slideover. The current inline edit form is removed.

**Route:** `GET /compliance/client/{client_id}/requirements/{req_id}/detail`

Returns `compliance/_requirement_slideover.html` rendered into `#requirement-slideover-container` (a new div on the compliance index page, same pattern as `#ai-import-container`).

### Template: `compliance/_requirement_slideover.html`

**New file.** Uses the same backdrop + fixed panel pattern as `_ai_import_panel.html`.

#### Panel sections (top to bottom):

**A. Status Banner** — Full-width colored strip at the top.
- Green background for Compliant, amber for Partial, red for Gap, gray for Waived/N/A/Needs Review.
- Shows auto-computed status label with dot indicator.
- "Confirm Compliant" button (shown when Partial): sets `compliance_status = 'Compliant'` and `status_manual_override = 1`. Saves via `PATCH .../review-mode/{req_id}/cell` and re-fetches the slideover to update the banner.
- "Override ▾" dropdown with all 6 status options. Saves via `PATCH .../review-mode/{req_id}/cell` with `field=compliance_status`. For Waived/N/A, also sets `status_manual_override = 1`. Re-fetches the slideover to update.

**B. Requirement Fields** — Click-to-edit section.
- Coverage line: combobox (config `policy_types`)
- Required limit: contenteditable, saves via PATCH, displays with currency filter
- Max deductible: contenteditable, saves via PATCH
- Deductible type: combobox (config `deductible_types`)
- Source: dropdown (from `requirement_sources`)
- Location: dropdown (from `projects`)
- Endorsements: toggleable pill buttons (rounded pills with breathing room). Active = green, inactive = gray. Each click toggles the pill, updates the hidden JSON field, and **immediately** saves via PATCH (no separate save action).
- All fields save on blur via `PATCH /compliance/client/{id}/review-mode/{req_id}/cell` (the JSON-returning endpoint, not the HTML-returning `requirements/{id}/cell`). Returns `{"ok": true, "formatted": "..."}` for `flashCell()` feedback.

**C. Policy Comparison** — Background color matches status (green/amber/red).
- Shows primary linked policy: UID, carrier, policy type, expiration date.
- Two comparison cards side by side:
  - Limit card: policy limit vs. required limit, checkmark or warning icon, pass/fail text.
  - Deductible card: policy deductible vs. max deductible, checkmark or warning icon.
- "Change Policy" combobox to search and switch primary link. Implemented as: add new link via `POST .../links/add`, then set as primary via `POST .../links/{id}/set-primary`. Both calls chain automatically. After link change, re-fetch slideover to recalculate auto-status.
- "Also Linked" section listing secondary policies with remove (×) buttons and link type badges.
- "+ Link Policy" button triggers combobox search, POSTs to existing link endpoint.
- When no policy is linked: red panel with "No policy covers this requirement" message and "Link a Policy →" button.

**D. Notes** — Contenteditable div, saves on blur via `PATCH .../review-mode/{req_id}/cell` (JSON endpoint). Always visible (not collapsed).

**E. Footer** — Created/updated timestamps. "Delete Requirement" link with `hx-confirm`.

### OOB Update Strategy

The slideover uses a **full re-fetch pattern** for state changes that affect auto-status. When any of these actions occur, the JS handler re-fetches the entire slideover via `hx-get` on `#requirement-slideover-container`:
- Policy link added, removed, or primary changed
- Status override applied ("Confirm Compliant" or "Override" dropdown)

For simple field edits (limit, deductible, notes, endorsements), the per-field PATCH returns JSON and `flashCell()` provides feedback. The status banner does NOT auto-update on every field edit — the user must close and re-open (or the next link change triggers it). This avoids excessive round-trips for each keystroke.

The parent page (matrix + summary banner) updates when the slideover is **closed**: closing the slideover fires `hx-get` on the location detail tab to refresh the requirement rows and summary stats. This is a single refresh, not per-edit OOB swaps.

### Closing behavior

- Click backdrop overlay → closes
- Press Escape → closes
- Click ✕ button → closes
- JS function `closeRequirementDetail()` clears the container

### What changes in existing templates

- `_requirement_row.html`: Edit button changes from `hx-get="...row/edit" hx-target="#req-row-{id}" hx-swap="outerHTML"` to `hx-get=".../{req_id}/detail" hx-target="#requirement-slideover-container" hx-swap="innerHTML"`.
- `_requirement_row_edit.html`: No longer referenced by the Edit button. Kept for backward compatibility but effectively deprecated.
- `_location_detail.html`: No changes needed (rows already use `_requirement_row.html`).
- `compliance/index.html`: Add `<div id="requirement-slideover-container"></div>` near the AI import container.

---

## 3. Schema Change

**Migration:** Add `status_manual_override` column to `coverage_requirements`.

```sql
ALTER TABLE coverage_requirements ADD COLUMN status_manual_override INTEGER DEFAULT 0;
```

This flag is set to `1` when a user manually confirms "Compliant" on a Partial requirement, or manually sets Waived/N/A. Auto-compute skips rows where this flag is `1`. The flag is cleared to `0` when a policy link changes (add/remove/swap primary), forcing re-evaluation with the new policy data.

## 4. Files to Modify

| File | Change |
|------|--------|
| `src/policydb/migrations/076_status_manual_override.sql` | **New.** Add `status_manual_override` column. |
| `src/policydb/db.py` | Wire migration 076. |
| `src/policydb/compliance.py` | Add `compute_auto_status()`. Percentage fix already done. Call auto-compute in `get_client_compliance_data()` for `Needs Review` rows. |
| `src/policydb/web/routes/compliance.py` | New `GET .../requirements/{req_id}/detail` endpoint. Update link endpoints to clear `status_manual_override` and trigger auto-status recompute. Update review-mode cell-patch to trigger auto-status on limit/deductible changes. |
| `src/policydb/web/templates/compliance/_requirement_slideover.html` | **New.** Full slideover panel with status banner, click-to-edit fields, policy comparison, notes, footer. Includes `closeRequirementDetail()` JS + escape handler. |
| `src/policydb/web/templates/compliance/_requirement_row.html` | Edit button targets slideover instead of inline edit. |
| `src/policydb/web/templates/compliance/_requirement_row_edit.html` | Add deprecation comment at top. |
| `src/policydb/web/templates/compliance/index.html` | Add `#requirement-slideover-container` div. |

## 5. Existing Code to Reuse

- `_ai_import_panel.html` — Slideover panel pattern (backdrop, fixed positioning, close-on-escape JS)
- `_policy_links.html` — Policy link combobox, star/primary toggle, link type badges
- `PATCH .../review-mode/{req_id}/cell` — JSON-returning cell-patch endpoint for all field saves
- Link endpoints `POST .../links/add`, `.../links/{id}/remove`, `.../links/{id}/set-primary`
- `suggest_policy_for_requirement()` — Auto-suggest for the "Link a Policy" combobox
- `flashCell()` in `base.html` — Visual feedback on save

## 6. Verification

1. Start server: `policydb serve`
2. Navigate to a client's compliance page with existing requirements
3. Click Edit on a requirement row — verify slideover opens (not inline edit)
4. Verify click-to-edit works: change limit, see it save on blur with green flash
5. Toggle endorsement pills — verify they save
6. Link a policy — verify auto-status computes and banner updates
7. Link a policy that meets limits but requirement has endorsements — verify "Partial"
8. Remove all policy links — verify "Gap"
9. Set status to "Waived" via Override — verify auto-compute does not overwrite it
10. Check compliance percentage in summary banner — verify Waived/N/A excluded from denominator
11. Close panel (×, Escape, backdrop click) — verify it closes cleanly
12. Test on narrow viewport — verify panel is responsive (full-width on mobile)
