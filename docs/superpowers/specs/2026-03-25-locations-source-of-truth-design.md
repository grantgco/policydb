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
- Policy unassigned from location → exposure fields keep the last-known address (no wipe)

**Unassigned policies** retain freeform editable exposure address fields — no change from current behavior.

### 2. Policy Edit Page — Locked Fields

When `policy.project_id IS NOT NULL`:
- Exposure address fields render as read-only styled text (not contenteditable)
- Small label above: "Address from: [Location Name]" with link to location detail page
- User must edit the location's address to change the policy's exposure address

When `policy.project_id IS NULL`:
- Exposure address fields remain editable as today (contenteditable with Google Places autocomplete)

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
| Coverage | No | Badge: placed/expected (green if complete, amber if gaps) |

**Additional UI elements:**
- "+ Add Location" button — appends a blank editable row
- "Organize →" link — navigates to full location assignment board (`/clients/{id}/locations`)
- Red unassigned-policies banner — shows when policies exist without a `project_id`, with count and "Assign →" link to assignment board
- Click coverage badge to expand inline row showing linked policy names/types

### 4. Cascade Logic

**File:** `src/policydb/web/routes/clients.py`

The existing `PATCH /clients/{id}/projects/{pid}/field` endpoint handles field saves for location rows. When the field is `address`, `city`, `state`, or `zip`:

1. Save to `projects` table (existing behavior)
2. After save, cascade: `UPDATE policies SET exposure_{field}=? WHERE project_id=? AND client_id=?`
3. Return the normal `{ok: true, formatted: ...}` response

**Location assignment** (`PATCH /clients/{id}/locations/assign`):
- After setting `policy.project_id`, also overwrite `exposure_address/city/state/zip` from the location's current values

**Location address autocomplete selection** (on Overview tab):
- When user selects from Google Places dropdown, city/state/zip fill automatically (existing pattern)
- Each field's blur/save triggers the cascade independently

### 5. Overview Tab Partial

**New template:** `src/policydb/web/templates/clients/_overview_locations.html`

Loaded via HTMX lazy-load on the Overview tab (same pattern as other Overview sections). Data comes from the existing `_get_project_locations(conn, client_id)` function in `clients.py`.

### 6. Files to Modify

| File | Change |
|------|--------|
| `src/policydb/web/routes/clients.py` | Add cascade logic to project field PATCH; add cascade to location assign; add Overview locations partial endpoint |
| `src/policydb/web/templates/clients/_overview_locations.html` | New partial: contenteditable locations table for Overview tab |
| `src/policydb/web/templates/clients/detail.html` | Add locations section to Overview tab after Meetings |
| `src/policydb/web/templates/policies/_tab_details.html` | Conditionally render exposure address as read-only when `policy.project_id` is set |
| `src/policydb/web/templates/clients/_project_locations.html` | May keep as-is on Policies tab or remove if redundant with Overview placement |

### 7. Migration

No schema migration needed. The `projects` table already has `address/city/state/zip` columns and `policies` already has `exposure_address/city/state/zip`. The change is behavioral (cascade on save), not structural.

**Data backfill consideration:** Existing policies assigned to locations may have exposure addresses that differ from their location's address. A one-time backfill script should sync them:
```sql
UPDATE policies SET
  exposure_address = (SELECT address FROM projects WHERE projects.id = policies.project_id),
  exposure_city = (SELECT city FROM projects WHERE projects.id = policies.project_id),
  exposure_state = (SELECT state FROM projects WHERE projects.id = policies.project_id),
  exposure_zip = (SELECT zip FROM projects WHERE projects.id = policies.project_id)
WHERE project_id IS NOT NULL;
```

### 8. Verification

1. Edit a location's address on the Overview tab → confirm all linked policies' exposure addresses update
2. Assign a policy to a location → confirm policy's exposure address overwrites with location's
3. Unassign a policy → confirm exposure address stays (not wiped)
4. On policy edit page for assigned policy → confirm exposure fields are read-only with location link
5. On policy edit page for unassigned policy → confirm exposure fields remain editable
6. Overview tab renders locations section after Meetings with correct data
7. "+ Add Location" creates a new row, "Organize →" navigates to assignment board
8. Unassigned policies banner appears/disappears correctly
