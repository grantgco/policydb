# Locations as Source of Truth

**Date:** 2026-03-25
**Status:** Draft

## Problem

Policy exposure addresses are freeform text fields independent of the locations/projects they're assigned to. This creates data drift — a location's address and its policies' exposure addresses can diverge silently. There's no single source of truth for "where is this risk located?"

Additionally, the locations table is buried in the Policies tab on the client page. It should be front-and-center on the Overview tab since locations are a primary organizing concept for the book.

## Design

### 1. Locations Own the Address

When a policy is assigned to a location (via `project_id`), the location's address becomes the policy's exposure address. The policy's `exposure_address`, `exposure_city`, `exposure_state`, `exposure_zip` fields are overwritten with the location's values.

**Assignment flow:**
- Policy assigned to location → copy location's `address/city/state/zip` into policy's `exposure_address/city/state/zip`
- Location address edited → cascade update to all linked policies (`UPDATE policies SET exposure_address=?, exposure_city=?, exposure_state=?, exposure_zip=? WHERE project_id=?`)
- Policy unassigned from location → exposure fields keep the last-known address (no wipe; no code change needed — existing unassign endpoint already only clears `project_id` and `project_name`)

**Unassigned policies** retain freeform editable exposure address fields — no change from current behavior.

### 2. Policy Edit Page — Locked Fields

When `policy.project_id IS NOT NULL`:
- Exposure address input fields render as disabled/read-only (the current page uses `<input>` elements, not contenteditable)
- Small label above: "Address from: [Location Name]" with link to location detail page
- User must edit the location's address to change the policy's exposure address

When `policy.project_id IS NULL`:
- Exposure address fields remain editable as today (with Google Places autocomplete)

### 3. Overview Tab — Locations Section

Move the locations summary to the Overview tab, positioned after the Meetings section and before the Activity timeline.

**Table columns (contenteditable inline editing):**

| Column | Editable | Notes |
|--------|----------|-------|
| Location name | Yes | Contenteditable cell |
| Address | Yes | Contenteditable with Google Places autocomplete; edits cascade to linked policies |
| City | Yes | Auto-filled from autocomplete selection |
| State | Yes | Auto-filled from autocomplete selection |
| ZIP | Yes | Auto-filled from autocomplete selection |
| Policies | No | Count of linked policies |
| Premium | No | Sum of linked policies' premiums |
| Coverage | No | Badge: placed count / total policies linked (green if all placed, amber if any non-placed remain) |

**Additional UI elements:**
- "+ Add Location" button — appends a blank editable row
- "Organize →" link — navigates to full location assignment board (`/clients/{id}/locations`)
- Red unassigned-policies banner — shows when policies exist without a `project_id`, with count and "Assign →" link to assignment board
- Click coverage badge to expand inline row showing linked policy names/types

**Remove locations from Policies tab** — the Policies tab's `_project_locations.html` table is removed since the Overview tab now owns this. The Policies tab retains the policy list only.

### 4. Cascade Logic

**File:** `src/policydb/web/routes/clients.py`

**Existing behavior:** The `project_pipeline_field()` endpoint (`PATCH /clients/{id}/projects/{pid}/field`) already cascades address field changes to linked policies. This existing cascade logic should be verified and tested but does not need reimplementation.

**New code needed — location assignment endpoints:**

Both assignment endpoints currently only set `project_id` and `project_name` on the policy. They must be updated to also copy the location's address fields:

- `location_assign()` (`PATCH /clients/{id}/locations/assign`) — after setting `project_id`, query the project's `address/city/state/zip` and overwrite the policy's `exposure_address/city/state/zip`
- `location_bulk_assign()` (`PATCH /clients/{id}/locations/bulk-assign`) — same cascade: after bulk-setting `project_id`, overwrite exposure fields for all affected policies from the location's address

**Autocomplete address selection on Overview tab:**
When user selects from Google Places dropdown on a location row, the autocomplete handler fills address/city/state/zip cells and each triggers a PATCH save on blur. The existing cascade in `project_pipeline_field()` handles the policy updates per-field. This means 4 sequential PATCH+cascade operations. This is acceptable for a single-user local app — the risk of partial update from a network failure is negligible since all calls are to localhost.

### 5. Overview Tab Partial

**New template:** `src/policydb/web/templates/clients/_overview_locations.html`

**New endpoint:** `GET /clients/{client_id}/overview/locations` — returns the rendered partial. Uses the existing `_get_project_locations(conn, client_id)` function.

Loaded via HTMX lazy-load on the Overview tab (same `hx-get` + `hx-trigger="load"` pattern as other Overview sections).

The contenteditable table follows the same pattern as `_project_locations.html` — uses `.loc-cell` class, `data-field`, `data-endpoint` attributes, and the existing Google Places autocomplete handler from `_project_locations.html`.

### 6. Files to Modify

| File | Change |
|------|--------|
| `src/policydb/web/routes/clients.py` | Add address cascade to `location_assign()` and `location_bulk_assign()`; add `GET /clients/{id}/overview/locations` endpoint |
| `src/policydb/web/templates/clients/_overview_locations.html` | **New:** contenteditable locations table for Overview tab |
| `src/policydb/web/templates/clients/detail.html` | Add locations section to Overview tab after Meetings (HTMX lazy-load) |
| `src/policydb/web/templates/policies/_tab_details.html` | Conditionally render exposure address inputs as disabled when `policy.project_id` is set, with location link label |
| `src/policydb/web/templates/clients/_project_locations.html` | Remove from Policies tab (replaced by Overview locations) |

### 7. Migration

No schema migration needed. The `projects` table already has `address/city/state/zip` columns and `policies` already has `exposure_address/city/state/zip`. The change is behavioral (cascade on assignment), not structural.

**Data backfill:** A one-time migration SQL to sync existing assigned policies with their location's address. Skips policies whose location has no address set (avoids overwriting with NULL):

```sql
UPDATE policies SET
  exposure_address = (SELECT address FROM projects WHERE projects.id = policies.project_id),
  exposure_city = (SELECT city FROM projects WHERE projects.id = policies.project_id),
  exposure_state = (SELECT state FROM projects WHERE projects.id = policies.project_id),
  exposure_zip = (SELECT zip FROM projects WHERE projects.id = policies.project_id)
WHERE project_id IS NOT NULL
  AND (SELECT address FROM projects WHERE projects.id = policies.project_id) IS NOT NULL
  AND (SELECT address FROM projects WHERE projects.id = policies.project_id) != '';
```

### 8. Verification

1. Edit a location's address on the Overview tab → confirm all linked policies' exposure addresses update
2. Assign a policy to a location (drag-drop) → confirm policy's exposure address overwrites with location's
3. Bulk-assign policies to a location → confirm all policies' exposure addresses update
4. Unassign a policy → confirm exposure address stays (not wiped)
5. On policy edit page for assigned policy → confirm exposure fields are disabled with location link
6. On policy edit page for unassigned policy → confirm exposure fields remain editable
7. Overview tab renders locations section after Meetings with correct data
8. "+ Add Location" creates a new row, "Organize →" navigates to assignment board
9. Unassigned policies banner appears/disappears correctly
10. Locations table no longer appears on Policies tab
