# Location-Scoped Programs

**Date:** 2026-03-30
**Status:** Draft

## Problem

Programs and projects/locations are currently independent entities in PolicyDB. Both hang off a client, but they have no direct relationship. In practice, insurance programs are often tied to specific locations (a plant's coverage tower) or construction projects (OCIP/CCIP wrap-ups). Without this link, the user must mentally track which programs belong to which locations.

## Solution

Add a nullable `project_id` FK on the `programs` table, creating a direct Client → Location → Program → Policies hierarchy. Location-scoped programs auto-sync their location to child policies on assignment.

---

## Schema

### Migration (next sequential number)

```sql
ALTER TABLE programs ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE;
CREATE INDEX idx_programs_project ON programs(project_id);
```

- `ON DELETE CASCADE` — deleting a location deletes its programs
- Before cascading, application code must null out `policies.program_id` for affected child policies (since `policies.program_id` FK lacks CASCADE)
- Existing programs get `project_id = NULL` — client-level programs, unchanged behavior

---

## Auto-Sync Behavior

### Policy → Location-Scoped Program Assignment

When a policy is assigned to a program that has `project_id` set:

1. Set `policies.program_id` = program.id *(existing)*
2. Set `policies.project_id` = program.project_id *(new)*
3. Set `policies.project_name` = project.name *(new)*
4. Sync exposure address fields from project *(existing location-assign behavior)*

### Policy Unassignment

When a policy is removed from a location-scoped program:
- Clear `policies.program_id` *(existing)*
- **Do not** clear `policies.project_id` — the policy may legitimately remain at that location

### Program Location Change

When a program's `project_id` is updated to a different location:
- Show inline prompt: "Move X child policies to new location too?"
- If yes: update all child policies' `project_id` + exposure fields
- If no: only the program's link changes; policies keep their current location

---

## UI Changes

### Program Detail Page (Header)

- Show location name + link next to program name if `project_id` is set
- Location picker (combobox of client's locations/projects) in header, editable
- Changing location triggers the move-children prompt above

### Program Creation Flow

- Existing "Create Program" on client Programs tab stays the same
- New: optional location picker (combobox) in creation form
- New: "Create Program" button on project/location detail page — pre-fills `project_id`

### Project/Location Detail Page

New **Programs** section below existing content:
- Cards for each program at this location: name, LOB, total premium, policy count, status badge
- "Create Program" button (scoped to this location)
- Click card → navigate to program detail page

### Client Programs Tab

Group programs by location:
- **Location A** — Program 1, Program 2
- **Location B** — Program 3
- **Client-Level** — Programs with no location (`project_id IS NULL`)

Each group is collapsible. Location name links to project detail page.

### Location Assignment Board

- Show small program count badge on location cards that have programs
- e.g., "2 programs" next to policy count

### Construction Project Completion

When a construction project's status changes to "Complete":
- Check for active (non-archived) programs linked to this project
- If found, show inline confirmation: "This project has X active program(s). Archive them too?"
- "Yes" → set `programs.archived = 1` for all linked programs
- "No" → programs remain active (tail coverage scenario)

---

## Query Changes

### New Queries

- `get_programs_for_project(conn, project_id)` — all programs for a location with aggregates (policy_count, carrier_count, total_premium)

### Modified Queries

- `get_programs_for_client(conn, client_id)` — add JOIN to projects for `project_id`, `project_name`; support grouping by location
- `get_program_pipeline(conn, client_id, window_days)` — include project info for display/grouping
- `get_programs_for_client()` result set gets `project_name` field

### Auto-Sync Helper

New function in `programs.py`:
```
_sync_policy_to_program_location(conn, policy_uid, program)
```
Sets `project_id`, `project_name`, and exposure address fields on the policy from the program's linked project.

Called from:
- `/programs/{uid}/assign/{policy_uid}` endpoint
- Bulk renewal flow (new child policies)

---

## Renewal Behavior

When a program renews via `/programs/{uid}/renew`:
- New child policies inherit `program_id` *(existing)*
- New child policies inherit the program's `project_id` via auto-sync *(new)*
- The program itself keeps its `project_id` — location doesn't change on renewal

---

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Existing program, no location | Works as today. `project_id = NULL`. |
| Policy at location A, assigned to program at location B | Auto-sync overwrites to location B |
| Program moved to different location | Prompt: "Move X children too?" |
| Location deleted (CASCADE) | Null child policies' `program_id` first, then programs cascade-delete |
| Program unlinked from location (`project_id` → NULL) | Becomes client-level. Children keep their current location. |
| Multiple programs at same location | Each operates independently. No conflict. |

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/policydb/migrations/NNN_program_project_link.sql` | New migration: add `project_id` FK + index |
| `src/policydb/db.py` | Wire migration into `init_db()` |
| `src/policydb/queries.py` | `get_programs_for_project()`, modify `get_programs_for_client()`, `get_program_pipeline()` |
| `src/policydb/web/routes/programs.py` | Auto-sync on assign, location picker in header, location change prompt |
| `src/policydb/web/routes/clients.py` | Programs section on project detail, completion prompt, location board badge |
| `src/policydb/web/templates/programs/detail.html` | Location display + picker in header |
| `src/policydb/web/templates/clients/project.html` | New Programs section |
| `src/policydb/web/templates/clients/_programs.html` | Group by location |
| `src/policydb/web/templates/clients/_location_board.html` | Program count badge |

---

## Verification

1. **Create location-scoped program:** Create a program from a location detail page. Verify `project_id` is set.
2. **Assign policy:** Assign a policy to the location-scoped program. Verify policy's `project_id` and exposure address auto-sync.
3. **Unassign policy:** Remove policy from program. Verify `project_id` stays on the policy.
4. **Move program location:** Change program's location. Verify prompt appears. Test both Yes/No paths.
5. **Client Programs tab:** Verify programs grouped by location with client-level section.
6. **Location board:** Verify program count badges on locations.
7. **Construction completion:** Set project status to Complete. Verify archive prompt for programs.
8. **Renewal:** Renew a location-scoped program. Verify new policies get the same location.
9. **Delete location:** Delete a location with programs. Verify programs cascade-delete and child policies' `program_id` is nulled.
10. **Existing programs:** Verify client-level programs (no location) work unchanged.
