# Locations as Source of Truth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the locations table the single source of truth for policy exposure addresses, with cascade updates on assignment and address changes, and move the locations section to the client Overview tab.

**Architecture:** When a policy is assigned to a location, its exposure_address fields get overwritten from the location. When a location's address changes, all linked policies cascade-update. The Overview tab gets a new contenteditable locations table (moved from Policies tab). Policy edit page shows read-only exposure fields when assigned to a location.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, Google Places API (existing `/api/address/*` endpoints)

**Spec:** `docs/superpowers/specs/2026-03-25-locations-source-of-truth-design.md`

---

### Task 1: Add Address Cascade to Location Assignment Endpoints

**Files:**
- Modify: `src/policydb/web/routes/clients.py:4682-4697` (location_assign)
- Modify: `src/policydb/web/routes/clients.py:4717-4735` (location_bulk_assign)

- [ ] **Step 1: Modify `location_assign()` to cascade address**

After the existing `UPDATE policies SET project_id=?, project_name=?` query, add a second query to copy the location's address fields to the policy:

```python
# In location_assign(), BEFORE conn.commit(), after the UPDATE that sets project_id:
loc = conn.execute(
    "SELECT address, city, state, zip FROM projects WHERE id=?",
    (project_id,),
).fetchone()
if loc:
    conn.execute(
        """UPDATE policies SET exposure_address=?, exposure_city=?,
           exposure_state=?, exposure_zip=?
           WHERE policy_uid=? AND client_id=?""",
        (loc["address"] or "", loc["city"] or "", loc["state"] or "", loc["zip"] or "",
         policy_uid, client_id),
    )
```

- [ ] **Step 2: Modify `location_bulk_assign()` to cascade address**

Same pattern — after the existing bulk UPDATE, cascade the location's address to all matched policies:

```python
# In location_bulk_assign(), BEFORE conn.commit(), after the UPDATE that sets project_id:
loc = conn.execute(
    "SELECT address, city, state, zip FROM projects WHERE id=?",
    (project_id,),
).fetchone()
if loc:
    conn.execute(
        """UPDATE policies SET exposure_address=?, exposure_city=?,
           exposure_state=?, exposure_zip=?
           WHERE project_id=? AND client_id=? AND archived=0""",
        (loc["address"] or "", loc["city"] or "", loc["state"] or "", loc["zip"] or "",
         project_id, client_id),
    )
```

- [ ] **Step 3: Verify the existing cascade in `project_pipeline_field()`**

Read `clients.py:4214-4220` and confirm the existing cascade logic for address field edits is working. It should already have:

```python
_address_to_exposure = {"address": "exposure_address", "city": "exposure_city",
                        "state": "exposure_state", "zip": "exposure_zip"}
if field in _address_to_exposure:
    exposure_field = _address_to_exposure[field]
    conn.execute(f"UPDATE policies SET {exposure_field} = ? WHERE project_id = ? AND archived = 0",
                 (clean_value, project_id))
```

No changes needed here — just verify it exists and works.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/clients.py
git commit -m "feat: cascade location address to policies on assign/bulk-assign"
```

---

### Task 2: Data Backfill Migration

**Files:**
- Create: `src/policydb/migrations/080_backfill_location_addresses.sql`
- Modify: `src/policydb/db.py` (wire migration)

- [ ] **Step 1: Create backfill migration**

```sql
-- Sync existing assigned policies with their location's address.
-- Only updates where the location has a non-empty address.
UPDATE policies SET
  exposure_address = (SELECT address FROM projects WHERE projects.id = policies.project_id),
  exposure_city = (SELECT city FROM projects WHERE projects.id = policies.project_id),
  exposure_state = (SELECT state FROM projects WHERE projects.id = policies.project_id),
  exposure_zip = (SELECT zip FROM projects WHERE projects.id = policies.project_id)
WHERE project_id IS NOT NULL
  AND (SELECT address FROM projects WHERE projects.id = policies.project_id) IS NOT NULL
  AND (SELECT address FROM projects WHERE projects.id = policies.project_id) != '';
```

- [ ] **Step 2: Wire migration into `init_db()`**

Add version check + INSERT into `schema_version` for migration 080. Follow the existing pattern in `db.py`.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/migrations/080_backfill_location_addresses.sql src/policydb/db.py
git commit -m "feat: migration 080 — backfill policy exposure addresses from locations"
```

---

### Task 3: Overview Tab — Locations Partial

**Files:**
- Create: `src/policydb/web/templates/clients/_overview_locations.html`
- Modify: `src/policydb/web/routes/clients.py` (add endpoint)
- Modify: `src/policydb/web/templates/clients/_tab_overview.html:95` (include after Meetings)

- [ ] **Step 1: Add locations data to the existing Overview tab route**

In `clients.py`, find the `client_tab_overview()` route that serves `_tab_overview.html`. Add the locations query to its context — this is consistent with how Meetings and other sections are loaded (all use `{% include %}` with data from the parent route, not separate HTMX lazy-loads):

```python
# Add to client_tab_overview(), alongside existing context building:
locations = _get_project_locations(conn, client_id)
unassigned_count = conn.execute(
    """SELECT COUNT(*) FROM policies
       WHERE client_id=? AND archived=0
       AND (project_id IS NULL OR project_id=0)
       AND (is_opportunity=0 OR is_opportunity IS NULL)""",
    (client_id,),
).fetchone()[0]

# Add to the template context dict:
# "locations": locations,
# "unassigned_count": unassigned_count,
```

- [ ] **Step 2: Create the `_overview_locations.html` partial**

Build a contenteditable locations table following the same pattern as `_project_locations.html`. Include:
- Contenteditable cells for name, address, city, state, zip
- Read-only policy count, premium, coverage badge columns
- `data-endpoint` attributes pointing to `/clients/{{ client.id }}/projects/{{ loc.id }}/field`
- Google Places autocomplete on address cells (reuse the `loc-address-suggestions` dropdown + the JS handler from `_project_locations.html`)
- "+ Add Location" button
- "Organize →" link to `/clients/{{ client.id }}/locations`
- Red unassigned banner (if `unassigned_count > 0`)
- Coverage badge: green (`placed_coverages == total_coverages` and > 0), amber otherwise
- Coverage badge click: fetch `GET /clients/{client_id}/projects/{loc.id}/coverage` (existing endpoint) and toggle an inline detail row showing linked policy names/types below the location row

- [ ] **Step 3: Include in Overview tab**

In `_tab_overview.html`, after line 95 (the Meetings include, BEFORE the `<script>` block on line 98), add:

```html
<!-- Locations -->
{% include "clients/_overview_locations.html" %}
```

- [ ] **Step 4: Restart server and verify Overview tab shows locations after Meetings**

- [ ] **Step 5: Commit**

```bash
git add src/policydb/web/routes/clients.py \
  src/policydb/web/templates/clients/_overview_locations.html \
  src/policydb/web/templates/clients/_tab_overview.html
git commit -m "feat: add locations section to Overview tab with contenteditable inline editing"
```

---

### Task 4: Remove Locations from Policies Tab

**Files:**
- Modify: `src/policydb/web/templates/clients/_tab_policies.html` (remove `_project_locations.html` include)

- [ ] **Step 1: Find and remove the `_project_locations.html` include from the Policies tab**

Search `_tab_policies.html` for `{% include "clients/_project_locations.html" %}` and remove the entire locations section (the include and its surrounding wrapper div). Keep everything else on the Policies tab.

- [ ] **Step 2: Verify Policies tab still renders correctly without locations section**

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/clients/_tab_policies.html
git commit -m "refactor: remove locations table from Policies tab (moved to Overview)"
```

---

### Task 5: Policy Edit Page — Read-Only Exposure Address When Assigned

**Files:**
- Modify: `src/policydb/web/templates/policies/_tab_details.html:483-511`

- [ ] **Step 1: Add conditional rendering for exposure address section**

Wrap the exposure address section (lines 483-511) in a Jinja2 conditional. When `policy.project_id` is set, render read-only fields with a location link label. When unset, render the existing editable fields.

```html
<div>
  <p class="text-xs font-medium text-gray-500 uppercase tracking-wide mb-3">Primary Exposure Address / Location</p>

  {% if policy.project_id %}
  {# Locked to location — read-only #}
  <div class="mb-2">
    <span class="text-xs text-gray-400">Address from:</span>
    <a href="/clients/{{ policy.client_id }}/projects/{{ policy.project_id }}"
       class="text-xs text-marsh hover:underline font-medium">{{ policy.project_name }}</a>
    <span class="text-xs text-gray-400 ml-1">— edit location to change</span>
  </div>
  <div class="grid grid-cols-1 sm:grid-cols-6 gap-4">
    <div class="sm:col-span-6">
      <label class="field-label">Street Address</label>
      <input type="text" value="{{ policy.exposure_address or '' }}" disabled
        class="form-input w-full bg-gray-50 text-gray-500 cursor-not-allowed">
    </div>
    <div class="sm:col-span-3">
      <label class="field-label">City</label>
      <input type="text" value="{{ policy.exposure_city or '' }}" disabled
        class="form-input w-full bg-gray-50 text-gray-500 cursor-not-allowed">
    </div>
    <div class="sm:col-span-2">
      <label class="field-label">State</label>
      <input type="text" value="{{ policy.exposure_state or '' }}" disabled
        class="form-input w-full bg-gray-50 text-gray-500 cursor-not-allowed">
    </div>
    <div class="sm:col-span-1">
      <label class="field-label">ZIP</label>
      <input type="text" value="{{ policy.exposure_zip or '' }}" disabled
        class="form-input w-full bg-gray-50 text-gray-500 cursor-not-allowed">
    </div>
  </div>
  {% else %}
  {# Editable — existing fields (unchanged) #}
  <div class="grid grid-cols-1 sm:grid-cols-6 gap-4">
    <!-- ... existing editable fields exactly as they are today ... -->
  </div>
  {% endif %}
</div>
```

Keep the existing editable HTML in the `{% else %}` block unchanged.

- [ ] **Step 2: Verify policy edit page for an assigned policy shows read-only fields with location link**

- [ ] **Step 3: Verify policy edit page for an unassigned policy shows editable fields as before**

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/policies/_tab_details.html
git commit -m "feat: lock exposure address fields on policy edit when assigned to a location"
```

---

### Task 6: QA Testing All Affected Pages

- [ ] **Step 1: Test location assignment cascade**
Navigate to `/clients/1/locations`, drag an unassigned policy to a location. Verify the policy's exposure address fields updated in the database.

- [ ] **Step 2: Test location address edit cascade**
On the Overview tab, edit a location's address. Verify all linked policies' exposure addresses updated.

- [ ] **Step 3: Test policy unassign**
Unassign a policy from a location. Verify exposure address stays (not wiped).

- [ ] **Step 4: Test policy edit page — assigned policy**
Navigate to a policy assigned to a location. Verify exposure address is read-only with location link.

- [ ] **Step 5: Test policy edit page — unassigned policy**
Navigate to an unassigned policy. Verify exposure address is editable with Google Places autocomplete.

- [ ] **Step 6: Test Overview tab layout**
Verify locations section appears after Meetings, before Activity. Verify contenteditable editing works, "Organize →" link works, "+ Add Location" works.

- [ ] **Step 7: Test Policies tab**
Verify locations section no longer appears on the Policies tab.

- [ ] **Step 8: Screenshot all tested pages for visual verification**
