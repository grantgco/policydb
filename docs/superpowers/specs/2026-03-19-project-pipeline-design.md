# Project Pipeline Tracker — Design Spec

**Date:** 2026-03-19
**Status:** Draft
**Scope:** Extend the existing `projects` table with pipeline fields (type, status, dates, value), add a contenteditable pipeline table and timeline summary bar to the client detail page, add coverage tracking via linked policies/opportunities, add table and timeline exports.

---

## Problem Statement

PolicyDB's `projects` table serves as both a location grouper (permanent sites) and a project tracker (time-bound construction/development work). There's no way to:

- Distinguish a permanent location from a time-bound project
- Track project timeline (start, completion, insurance placement deadline)
- See a pipeline of upcoming projects with their status and coverage needs
- Quickly export a project pipeline for client-facing communication

Clients send lists of upcoming projects with varying levels of detail. The system needs to capture whatever is available and let the user fill in gaps over time.

---

## Design Decisions (Validated)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Project vs Location | `project_type` field distinguishes them | Same table, optional fields. Location projects ignore pipeline fields |
| Stage tracking | Config-managed list with defaults | Flexible, same pattern as renewal_statuses |
| Coverage tracking | Linked policies/opportunities | No duplicate data entry. Opportunities ARE the "needed" markers |
| Primary view | Contenteditable table | Consistent with user's preferred pattern |
| Timeline view | Lightweight summary bar above table | Not a full Gantt — just enough to see overlaps and deadlines |
| Financial tracking | Computed from linked policies | Premium = SUM of linked. Revenue = SUM(premium * commission_rate). Not manually entered |
| All pipeline fields | Optional | A project with just a name is valid. No required fields beyond name |

---

## 1. Schema Changes

### New columns on `projects` table

**Migration file:** `src/policydb/migrations/061_project_pipeline.sql`

```sql
ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT 'Location';
ALTER TABLE projects ADD COLUMN status TEXT DEFAULT 'Upcoming';
ALTER TABLE projects ADD COLUMN project_value REAL;
ALTER TABLE projects ADD COLUMN start_date DATE;
ALTER TABLE projects ADD COLUMN target_completion DATE;
ALTER TABLE projects ADD COLUMN insurance_needed_by DATE;
ALTER TABLE projects ADD COLUMN scope_description TEXT;
ALTER TABLE projects ADD COLUMN general_contractor TEXT;
ALTER TABLE projects ADD COLUMN owner_name TEXT;
```

All columns are optional (nullable). Existing projects default to `project_type = 'Location'` and `status = 'Upcoming'`.

### New config keys

Added to `_DEFAULTS` in `src/policydb/config.py`:

```python
"project_stages": ["Upcoming", "Quoting", "Bound", "Active", "Complete"],
"project_types": ["Location", "Construction", "Development", "Renovation"],
```

Managed in Settings UI via existing `_list_card.html` pattern (flat string lists).

---

## 2. UI — Pipeline Table

### Location on client detail page

**File:** `src/policydb/web/templates/clients/detail.html`

New section above the existing policy groupings. Only renders if the client has any projects with `project_type != 'Location'`.

### Contenteditable table

```
┌──────────┬─────────────┬────────┬───────────┬──────────┬───────────┬──────────┬─────────┬──────────┐
│ Type     │ Project     │ Status │ Ins. By   │ Start    │ Complete  │ Value    │ Premium │ Coverage │
├──────────┼─────────────┼────────┼───────────┼──────────┼───────────┼──────────┼─────────┼──────────┤
│ Constr.  │ Tower West  │Quoting │ 06/01/26  │ 08/01/26 │ 12/01/27  │ $15M     │ $465K   │ 2 of 4   │
│ Dev.     │ Phase II    │Upcoming│ 09/01/26  │ 01/01/27 │ 06/01/28  │ $42M     │ $0      │ 0 of 3   │
│ Renov.   │ Lobby Remod │ Active │ —         │ 03/15/26 │ 07/01/26  │ $800K    │ $125K   │ 1 of 1   │
└──────────┴─────────────┴────────┴───────────┴──────────┴───────────┴──────────┴─────────┴──────────┘
  + Add Project
```

**Columns:**

| Column | Editable | Source | Notes |
|--------|----------|--------|-------|
| Type | Yes — pill selector | `projects.project_type` | Config-managed list |
| Project | Yes — contenteditable | `projects.name` | Links to project detail page |
| Status | Yes — pill selector | `projects.status` | Config-managed stages |
| Ins. By | Yes — date | `projects.insurance_needed_by` | When placement must be done |
| Start | Yes — date | `projects.start_date` | Project start date |
| Complete | Yes — date | `projects.target_completion` | Target completion |
| Value | Yes — contenteditable | `projects.project_value` | Contract/project value, formatted as currency |
| Premium | Read-only | Computed | SUM(premium) from linked policies + opportunities |
| Revenue | Read-only | Computed | SUM(premium * commission_rate) from linked policies |
| Coverage | Read-only | Computed | "X of Y" — bound policies / total linked (policies + opportunities) |

**Interactions:**
- Click cell to edit, blur saves via PATCH (same as carrier matrix pattern)
- Type and Status use pill button selectors (not dropdowns, per user preference)
- Premium, Revenue, and Coverage are computed — no PATCH, just display
- Project name links to existing `/clients/{client_id}/projects/{project_id}` detail page
- "+ Add Project" creates a new row with `project_type = 'Construction'` and `status = 'Upcoming'`
- Drag handle for reorder (optional — may not be needed since table is sortable)
- Delete button per row

### Coverage expansion

Click the "2 of 4" coverage cell to expand an inline detail showing linked policies and opportunities:

```
│ Coverage         │ Status     │ Carrier  │ Premium       │
├──────────────────┼────────────┼──────────┼───────────────┤
│ Builders Risk    │ ● Bound    │ Zurich   │ $125,000      │
│ OCIP             │ ● Quoted   │ AIG      │ $340,000      │
│ Sub Default      │ ○ Opp      │ —        │ —             │
│ Excess Liability │ ○ Opp      │ —        │ —             │
```

- Green dot (●) for bound/active policies
- Amber dot for quoted opportunities
- Open circle (○) for unquoted opportunities
- Each row links to the policy/opportunity edit page
- No separate "needed" tracking — opportunities ARE the needed markers

---

## 3. UI — Timeline Summary Bar

**Location:** Above the pipeline table on the client detail page.

**Only renders when:** 2+ projects have `start_date` or `target_completion` set.

**Visual:** Horizontal bars spanning start → completion for each project, laid out on a shared time axis.

```
2026                          2027                          2028
|-------|-------|-------|-------|-------|-------|-------|-------|
  Tower West    ████████████████████████░░░░
  Phase II                    ░░░░░░████████████████████████
  Lobby Remod   ██████████
                    ▼ ins. needed
```

- Filled portion (████) = time elapsed or active
- Dashed/light portion (░░░░) = future
- Triangle (▼) marker = `insurance_needed_by` date
- Color-coded by status: gray=Upcoming, blue=Quoting, green=Bound/Active, muted=Complete
- Compact — one line per project, fixed height

**Implementation:** Pure HTML/CSS using `<div>` elements with percentage-based widths calculated from date ranges. No charting library needed.

---

## 4. API Endpoints

### New endpoints

**`PATCH /clients/{client_id}/projects/{project_id}/field`**
- Updates a single field on the project (contenteditable cell save)
- Request: `{"field": "project_value", "value": "15000000"}`
- Response: `{"ok": true, "formatted": "$15,000,000"}`
- Allowed fields: `project_type`, `status`, `project_value`, `start_date`, `target_completion`, `insurance_needed_by`, `scope_description`, `general_contractor`, `owner_name`
- Currency fields formatted via server, flash on reformat
- Date fields validated (ISO format)

**`POST /clients/{client_id}/projects/pipeline`**
- Creates a new pipeline project (not location)
- Defaults: `project_type = 'Construction'`, `status = 'Upcoming'`
- Returns HTML partial of the new table row

**`GET /clients/{client_id}/projects/{project_id}/coverage`**
- Returns coverage detail expansion HTML
- Queries policies + opportunities linked to this project
- Computes bound count, opportunity count, total premium, total revenue

### Existing endpoints (no changes needed)

- `POST /clients/{client_id}/project/rename` — works with new fields
- `POST /clients/{client_id}/project/delete` — works with new fields
- `GET /clients/{client_id}/projects/{project_id}` — project detail page (unchanged)

---

## 5. Exports

### Table export (CSV/XLSX)

**Endpoint:** `GET /clients/{client_id}/projects/pipeline/export?format=xlsx`

**Columns:**
- Project Name, Type, Status
- Insurance Needed By, Start Date, Target Completion
- Project Value, Total Premium, Total Revenue
- General Contractor, Owner
- Coverages (comma-joined list of linked policy types with status)
- Scope Description

**Header:** Client name + generation date.

### Timeline export (PDF/PNG)

**Endpoint:** `GET /clients/{client_id}/projects/pipeline/timeline?format=pdf`

- Renders the same horizontal bar chart as the on-screen timeline
- Clean, client-ready format — no internal IDs or system data
- Header: Client name, "Project Pipeline Timeline", generation date
- Uses fpdf2 (already a dependency) for PDF generation
- PNG option via server-side HTML-to-image if needed (or just PDF)

---

## 6. Pipeline Data Query

**Computing coverage stats per project:**

```python
def get_project_pipeline(conn, client_id: int) -> list[dict]:
    """Load all non-location projects with computed coverage stats."""
    projects = conn.execute("""
        SELECT p.*,
               (SELECT COUNT(*) FROM policies pol
                WHERE pol.project_id = p.id AND pol.archived = 0) AS total_coverages,
               (SELECT COUNT(*) FROM policies pol
                WHERE pol.project_id = p.id AND pol.archived = 0
                AND (pol.is_opportunity = 0 OR pol.is_opportunity IS NULL)) AS bound_coverages,
               (SELECT COALESCE(SUM(pol.premium), 0) FROM policies pol
                WHERE pol.project_id = p.id AND pol.archived = 0) AS total_premium,
               (SELECT COALESCE(SUM(CASE WHEN pol.commission_rate > 0
                THEN pol.premium * pol.commission_rate ELSE 0 END), 0)
                FROM policies pol
                WHERE pol.project_id = p.id AND pol.archived = 0) AS total_revenue
        FROM projects p
        WHERE p.client_id = ? AND p.project_type != 'Location'
        ORDER BY p.insurance_needed_by, p.start_date, p.name
    """, (client_id,)).fetchall()
    return [dict(r) for r in projects]
```

---

## 7. Settings Integration

Two new config lists managed in Settings UI via existing `_list_card.html`:

- **Project Stages** — flat string list, same as `renewal_statuses`
- **Project Types** — flat string list, same as `activity_types`

**Files affected:**
- `src/policydb/config.py` — add defaults
- `src/policydb/web/routes/settings.py` — pass to template context (already handles generic lists)
- `src/policydb/web/templates/settings.html` — include two new `_list_card.html` instances

---

## 8. Edge Cases

| Scenario | Behavior |
|----------|----------|
| Existing projects with no `project_type` | Default to `'Location'`. No pipeline fields shown. |
| Project with no dates set | Appears in table but not in timeline bar. |
| Project with no linked policies | Coverage shows "0 of 0". Premium/Revenue show $0. |
| Change project type from Construction → Location | Pipeline fields remain in DB but row disappears from pipeline table. Reappears if changed back. |
| Delete a pipeline project | Same as existing delete — clears `project_id` on linked policies, moves them to Corporate/Standalone. |
| Project value entered as "$15M" | Server parses to 15000000, formats back as "$15,000,000", flash on reformat. |
| Multiple projects with overlapping dates | Timeline bar shows overlaps clearly (bars stack vertically). |
| Export with 0 pipeline projects | Export buttons hidden. |
| Location projects in table export | Excluded — only non-location projects export. |
