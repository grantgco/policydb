# Project Pipeline Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the projects table with pipeline fields (type, status, dates, value, address) and build a contenteditable pipeline table with timeline summary bar and exports on the client detail page.

**Architecture:** New columns on existing `projects` table. Coverage stats computed from linked policies/opportunities via subqueries. Contenteditable table follows the carrier matrix PATCH-on-blur pattern. Timeline bar is pure HTML/CSS with percentage-based widths. Exports via openpyxl (XLSX) and fpdf2 (PDF).

**Tech Stack:** SQLite, FastAPI, Jinja2, HTMX, vanilla JS, openpyxl, fpdf2

**Spec:** `docs/superpowers/specs/2026-03-19-project-pipeline-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/migrations/061_project_pipeline.sql` | Schema: new columns on projects table |
| Create | `tests/test_project_pipeline.py` | All tests for this feature |
| Create | `src/policydb/web/templates/clients/_project_pipeline.html` | Pipeline table partial |
| Create | `src/policydb/web/templates/clients/_project_pipeline_row.html` | Single pipeline row (for HTMX add) |
| Create | `src/policydb/web/templates/clients/_project_timeline.html` | Timeline summary bar partial |
| Create | `src/policydb/web/templates/clients/_project_coverage_detail.html` | Coverage expansion partial |
| Modify | `src/policydb/config.py` | Add project_stages and project_types defaults |
| Modify | `src/policydb/db.py` | Register migration 061 |
| Modify | `src/policydb/utils.py` | Add `parse_currency_with_magnitude()` |
| Modify | `src/policydb/web/routes/clients.py` | Pipeline query, PATCH/POST/GET endpoints, exports |
| Modify | `src/policydb/web/templates/clients/detail.html` | Include pipeline + timeline partials |
| Modify | `src/policydb/web/routes/settings.py` | Pass project_stages and project_types to context |
| Modify | `src/policydb/web/templates/settings.html` | Include two new list cards |

---

### Task 1: Migration + Config

**Files:**
- Create: `src/policydb/migrations/061_project_pipeline.sql`
- Modify: `src/policydb/db.py`
- Modify: `src/policydb/config.py`
- Create: `tests/test_project_pipeline.py`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 061_project_pipeline.sql
ALTER TABLE projects ADD COLUMN project_type TEXT DEFAULT 'Location';
ALTER TABLE projects ADD COLUMN status TEXT DEFAULT 'Upcoming';
ALTER TABLE projects ADD COLUMN project_value REAL;
ALTER TABLE projects ADD COLUMN start_date DATE;
ALTER TABLE projects ADD COLUMN target_completion DATE;
ALTER TABLE projects ADD COLUMN insurance_needed_by DATE;
ALTER TABLE projects ADD COLUMN scope_description TEXT;
ALTER TABLE projects ADD COLUMN general_contractor TEXT;
ALTER TABLE projects ADD COLUMN owner_name TEXT;
ALTER TABLE projects ADD COLUMN address TEXT;
ALTER TABLE projects ADD COLUMN city TEXT;
ALTER TABLE projects ADD COLUMN state TEXT;
ALTER TABLE projects ADD COLUMN zip TEXT;
```

Write to `src/policydb/migrations/061_project_pipeline.sql`.

- [ ] **Step 2: Register migration in db.py**

Add 61 to `_KNOWN_MIGRATIONS` set and add the `if 61 not in applied` block following the pattern of migration 060.

- [ ] **Step 3: Add config defaults**

In `src/policydb/config.py`, add to `_DEFAULTS`:

```python
    "project_stages": ["Upcoming", "Quoting", "Bound", "Active", "Complete"],
    "project_types": ["Location", "Construction", "Development", "Renovation"],
```

- [ ] **Step 4: Write tests**

```python
# tests/test_project_pipeline.py
"""Tests for project pipeline tracker."""

import pytest
from datetime import date
from policydb.db import get_connection, init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.sqlite"
    monkeypatch.setattr("policydb.db.DB_PATH", db_path)
    monkeypatch.setattr("policydb.db.DB_DIR", tmp_path)
    monkeypatch.setattr("policydb.db.EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr("policydb.db.CONFIG_PATH", tmp_path / "config.yaml")
    init_db(path=db_path)
    return db_path


def test_project_pipeline_columns(tmp_db):
    conn = get_connection(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    for col in ["project_type", "status", "project_value", "start_date",
                "target_completion", "insurance_needed_by", "scope_description",
                "general_contractor", "owner_name", "address", "city", "state", "zip"]:
        assert col in cols, f"Missing column: {col}"
    conn.close()


def test_existing_projects_default_to_location(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Test', 'Test')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO projects (client_id, name) VALUES (?, 'HQ')", (cid,))
    conn.commit()
    row = conn.execute("SELECT project_type, status FROM projects WHERE name='HQ'").fetchone()
    assert row["project_type"] == "Location"
    assert row["status"] == "Upcoming"
    conn.close()


def test_pipeline_project_with_all_fields(tmp_db):
    conn = get_connection(tmp_db)
    conn.execute("INSERT INTO clients (name, industry_segment) VALUES ('Builder', 'Construction')")
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """INSERT INTO projects (client_id, name, project_type, status, project_value,
                                 start_date, target_completion, insurance_needed_by,
                                 general_contractor, owner_name, address, city, state, zip)
           VALUES (?, 'Tower West', 'Construction', 'Quoting', 15000000,
                   '2026-08-01', '2027-12-01', '2026-06-01',
                   'ABC Builders', 'Developer LLC', '100 Main St', 'Austin', 'TX', '78701')""",
        (cid,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM projects WHERE name='Tower West'").fetchone()
    assert row["project_type"] == "Construction"
    assert row["project_value"] == 15000000
    assert row["city"] == "Austin"
    conn.close()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_project_pipeline.py -v`
Expected: All 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/policydb/migrations/061_project_pipeline.sql src/policydb/db.py src/policydb/config.py tests/test_project_pipeline.py
git commit -m "feat: add project pipeline columns and config (migration 061)"
```

---

### Task 2: Currency Magnitude Parser

**Files:**
- Modify: `src/policydb/utils.py`
- Test: `tests/test_project_pipeline.py`

- [ ] **Step 1: Write tests**

Add to `tests/test_project_pipeline.py`:

```python
from policydb.utils import parse_currency_with_magnitude


def test_parse_currency_plain():
    assert parse_currency_with_magnitude("15000000") == 15000000.0


def test_parse_currency_with_dollar_commas():
    assert parse_currency_with_magnitude("$15,000,000") == 15000000.0


def test_parse_currency_millions():
    assert parse_currency_with_magnitude("$15M") == 15000000.0
    assert parse_currency_with_magnitude("15m") == 15000000.0
    assert parse_currency_with_magnitude("$1.5M") == 1500000.0


def test_parse_currency_thousands():
    assert parse_currency_with_magnitude("$800K") == 800000.0
    assert parse_currency_with_magnitude("800k") == 800000.0


def test_parse_currency_billions():
    assert parse_currency_with_magnitude("$1.2B") == 1200000000.0


def test_parse_currency_empty():
    assert parse_currency_with_magnitude("") == 0.0
    assert parse_currency_with_magnitude(None) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_project_pipeline.py::test_parse_currency_plain -v`
Expected: FAIL — function doesn't exist yet

- [ ] **Step 3: Implement**

Add to `src/policydb/utils.py`:

```python
def parse_currency_with_magnitude(raw) -> float:
    """Parse a currency value with optional magnitude suffix (K, M, B).

    Examples:
        "$15M" → 15000000.0
        "$800K" → 800000.0
        "$1.2B" → 1200000000.0
        "$15,000,000" → 15000000.0
        "15000000" → 15000000.0
        "" → 0.0
    """
    if not raw:
        return 0.0
    s = str(raw).strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    multiplier = 1
    if s[-1].upper() == "K":
        multiplier = 1_000
        s = s[:-1]
    elif s[-1].upper() == "M":
        multiplier = 1_000_000
        s = s[:-1]
    elif s[-1].upper() == "B":
        multiplier = 1_000_000_000
        s = s[:-1]
    try:
        return float(s) * multiplier
    except (ValueError, TypeError):
        return 0.0
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_project_pipeline.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/policydb/utils.py tests/test_project_pipeline.py
git commit -m "feat: add parse_currency_with_magnitude() to utils"
```

---

### Task 3: Pipeline Query + API Endpoints

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 1: Add `get_project_pipeline` query function**

Add to `src/policydb/web/routes/clients.py` (or as a helper function near the top):

```python
def _get_project_pipeline(conn, client_id: int) -> list[dict]:
    """Load all non-location projects with computed coverage stats."""
    projects = conn.execute("""
        SELECT p.*,
               (SELECT COUNT(*) FROM policies pol
                WHERE pol.project_id = p.id AND pol.archived = 0) AS total_coverages,
               (SELECT COUNT(*) FROM policies pol
                WHERE pol.project_id = p.id AND pol.archived = 0
                AND (pol.is_opportunity = 0 OR pol.is_opportunity IS NULL)) AS placed_coverages,
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

- [ ] **Step 2: Add pipeline data to client_detail context**

In the `client_detail` function (around line 255), add the pipeline query and pass to template context:

```python
        "pipeline_projects": _get_project_pipeline(conn, client_id),
        "project_stages": cfg.get("project_stages", []),
        "project_types": cfg.get("project_types", []),
```

- [ ] **Step 3: Add PATCH endpoint for field updates**

```python
@router.patch("/clients/{client_id}/projects/{project_id}/field")
async def project_pipeline_field(
    request: Request,
    client_id: int,
    project_id: int,
    conn=Depends(get_db),
):
    """Update a single field on a pipeline project (contenteditable cell save)."""
    body = await request.json()
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"project_type", "status", "name", "project_value", "start_date",
               "target_completion", "insurance_needed_by", "scope_description",
               "general_contractor", "owner_name", "address", "city", "state", "zip"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Invalid field: {field}"}, status_code=400)

    project = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND client_id = ?",
        (project_id, client_id),
    ).fetchone()
    if not project:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    formatted = value
    if field == "project_value":
        from policydb.utils import parse_currency_with_magnitude
        num = parse_currency_with_magnitude(value)
        conn.execute("UPDATE projects SET project_value = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (num, project_id))
        formatted = f"${num:,.0f}"
    elif field in ("start_date", "target_completion", "insurance_needed_by"):
        conn.execute(f"UPDATE projects SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (value.strip() or None, project_id))
        formatted = value.strip()
    elif field == "name":
        # Check unique constraint
        existing = conn.execute(
            "SELECT id FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?)) AND id != ?",
            (client_id, value.strip(), project_id),
        ).fetchone()
        if existing:
            return JSONResponse({"ok": False, "error": "Project name already exists"}, status_code=400)
        conn.execute("UPDATE projects SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (value.strip(), project_id))
        # Also update project_name on linked policies for backward compat
        conn.execute("UPDATE policies SET project_name = ? WHERE project_id = ?",
                     (value.strip(), project_id))
        formatted = value.strip()
    else:
        conn.execute(f"UPDATE projects SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (value.strip() or None, project_id))
        formatted = value.strip()

    conn.commit()
    return JSONResponse({"ok": True, "formatted": formatted})
```

- [ ] **Step 4: Add POST endpoint for new pipeline project**

```python
@router.post("/clients/{client_id}/projects/pipeline", response_class=HTMLResponse)
def project_pipeline_add(
    request: Request,
    client_id: int,
    conn=Depends(get_db),
):
    """Create a new pipeline project with default values."""
    # Generate unique name
    base = "New Project"
    name = base
    counter = 2
    while conn.execute(
        "SELECT id FROM projects WHERE client_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (client_id, name),
    ).fetchone():
        name = f"{base} {counter}"
        counter += 1

    conn.execute(
        """INSERT INTO projects (client_id, name, project_type, status)
           VALUES (?, ?, 'Construction', 'Upcoming')""",
        (client_id, name),
    )
    project_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    project = dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
    project["total_coverages"] = 0
    project["placed_coverages"] = 0
    project["total_premium"] = 0
    project["total_revenue"] = 0

    return templates.TemplateResponse("clients/_project_pipeline_row.html", {
        "request": request,
        "p": project,
        "client": {"id": client_id},
        "project_stages": cfg.get("project_stages", []),
        "project_types": cfg.get("project_types", []),
    })
```

- [ ] **Step 5: Add GET endpoint for coverage expansion**

```python
@router.get("/clients/{client_id}/projects/{project_id}/coverage", response_class=HTMLResponse)
def project_coverage_detail(
    request: Request,
    client_id: int,
    project_id: int,
    conn=Depends(get_db),
):
    """Return coverage detail expansion for a pipeline project."""
    policies = [dict(r) for r in conn.execute("""
        SELECT policy_uid, policy_type, carrier, premium, renewal_status,
               is_opportunity, opportunity_status
        FROM policies
        WHERE project_id = ? AND archived = 0
        ORDER BY is_opportunity, policy_type
    """, (project_id,)).fetchall()]

    return templates.TemplateResponse("clients/_project_coverage_detail.html", {
        "request": request,
        "policies": policies,
    })
```

- [ ] **Step 6: Add DELETE endpoint for pipeline project**

```python
@router.delete("/clients/{client_id}/projects/{project_id}/pipeline")
def project_pipeline_delete(
    client_id: int,
    project_id: int,
    conn=Depends(get_db),
):
    """Delete a pipeline project, unlinking its policies."""
    conn.execute("UPDATE policies SET project_id = NULL, project_name = NULL WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ? AND client_id = ?", (project_id, client_id))
    conn.commit()
    return JSONResponse({"ok": True})
```

- [ ] **Step 7: Commit**

```bash
git add src/policydb/web/routes/clients.py
git commit -m "feat: pipeline query and CRUD endpoints for project tracker"
```

---

### Task 4: Pipeline Table UI

**Files:**
- Create: `src/policydb/web/templates/clients/_project_pipeline.html`
- Create: `src/policydb/web/templates/clients/_project_pipeline_row.html`
- Create: `src/policydb/web/templates/clients/_project_coverage_detail.html`
- Modify: `src/policydb/web/templates/clients/detail.html`

- [ ] **Step 1: Create the pipeline row partial**

Write `src/policydb/web/templates/clients/_project_pipeline_row.html` — a single `<tr>` with contenteditable cells for each column. Follows the carrier matrix pattern with `data-field`, `data-id`, `data-endpoint` attributes. Type and Status use pill buttons. Premium, Revenue, Coverage are read-only computed values. Project name links to detail page. Delete button.

Key data attributes on cells:
- `data-endpoint="/clients/{{ client.id }}/projects/{{ p.id }}/field"`
- `data-field="project_value"` etc.

Coverage cell is clickable to expand via `hx-get="/clients/{{ client.id }}/projects/{{ p.id }}/coverage"`.

- [ ] **Step 2: Create the coverage detail partial**

Write `src/policydb/web/templates/clients/_project_coverage_detail.html` — renders a small table of linked policies/opportunities with status dots (green=placed, amber=quoted, open=opportunity).

- [ ] **Step 3: Create the pipeline table partial**

Write `src/policydb/web/templates/clients/_project_pipeline.html` — wraps the rows in a `<details>` card with header showing project count. Includes table headers, tbody with row includes, tfoot with "+ Add Project" button. Includes inline `<style>` for contenteditable cells and `<script>` for cell save, pill selection, delete, and coverage toggle.

The JS follows the same pattern as the carrier matrix: event delegation on blur for cell save, `flashCell()` for reformatted values, pill selection toggling.

- [ ] **Step 4: Include pipeline in client detail**

In `src/policydb/web/templates/clients/detail.html`, add before the policy groups section:

```html
{% if pipeline_projects %}
{% include 'clients/_project_pipeline.html' %}
{% endif %}
```

- [ ] **Step 5: Manual test**

Run: `policydb serve`
Navigate to a client page. Verify:
- Pipeline table appears if non-location projects exist
- "+ Add Project" creates a row
- Cells are editable, save on blur
- Type/Status pills work
- Coverage click expands inline
- Delete removes row

- [ ] **Step 6: Commit**

```bash
git add src/policydb/web/templates/clients/_project_pipeline.html src/policydb/web/templates/clients/_project_pipeline_row.html src/policydb/web/templates/clients/_project_coverage_detail.html src/policydb/web/templates/clients/detail.html
git commit -m "feat: contenteditable project pipeline table on client detail page"
```

---

### Task 5: Timeline Summary Bar

**Files:**
- Create: `src/policydb/web/templates/clients/_project_timeline.html`
- Modify: `src/policydb/web/templates/clients/_project_pipeline.html` (include timeline)

- [ ] **Step 1: Create timeline partial**

Write `src/policydb/web/templates/clients/_project_timeline.html` — renders horizontal bars for each project with dates. Uses percentage-based widths calculated from the overall date range.

Logic (in Jinja2):
1. Find min start_date and max target_completion across all projects with dates
2. For each project, compute bar position and width as percentages of the total range
3. Render `<div>` bars with inline styles for left/width
4. Mark `insurance_needed_by` with a triangle marker
5. Color by status: gray=Upcoming, blue=Quoting, green=Bound/Active, muted=Complete

Only renders when 2+ projects have dates set.

- [ ] **Step 2: Include timeline in pipeline partial**

In `_project_pipeline.html`, add above the table:

```html
{% include 'clients/_project_timeline.html' %}
```

- [ ] **Step 3: Manual test**

Create 2+ projects with dates, verify the timeline bar renders correctly.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/clients/_project_timeline.html src/policydb/web/templates/clients/_project_pipeline.html
git commit -m "feat: timeline summary bar for project pipeline"
```

---

### Task 6: Settings Integration

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1: Pass project config lists to settings context**

In the settings GET handler, add to the template context:

```python
"project_stages": cfg.get("project_stages", []),
"project_types": cfg.get("project_types", []),
```

These are flat string lists, so the existing `_list_card.html` pattern works.

- [ ] **Step 2: Include list cards in settings.html**

Add two new `_list_card.html` includes for `project_stages` and `project_types`.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings.html
git commit -m "feat: project stages and types in settings UI"
```

---

### Task 7: Table Export (XLSX/CSV)

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 1: Add pipeline export endpoint**

```python
@router.get("/clients/{client_id}/projects/pipeline/export")
def project_pipeline_export(
    client_id: int,
    format: str = "xlsx",
    conn=Depends(get_db),
):
    """Export project pipeline as CSV or XLSX."""
    client = conn.execute("SELECT name FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        return HTMLResponse("Not found", status_code=404)

    projects = _get_project_pipeline(conn, client_id)

    # Attach coverage list per project
    for p in projects:
        pols = conn.execute("""
            SELECT policy_type, is_opportunity, renewal_status
            FROM policies WHERE project_id = ? AND archived = 0
            ORDER BY is_opportunity, policy_type
        """, (p["id"],)).fetchall()
        coverages = []
        for pol in pols:
            status = "Opp" if pol["is_opportunity"] else (pol["renewal_status"] or "Placed")
            coverages.append(f"{pol['policy_type']} ({status})")
        p["coverage_list"] = ", ".join(coverages) if coverages else ""

    cols = ["name", "project_type", "status", "address", "city", "state", "zip",
            "insurance_needed_by", "start_date", "target_completion",
            "project_value", "total_premium", "total_revenue",
            "general_contractor", "owner_name", "coverage_list", "scope_description"]
    headers = ["Project", "Type", "Status", "Address", "City", "State", "ZIP",
               "Insurance Needed By", "Start Date", "Target Completion",
               "Project Value", "Total Premium", "Total Revenue",
               "General Contractor", "Owner", "Coverages", "Scope"]

    # Build export inline (exporter.py doesn't have generic helpers)
    import io
    if format == "csv":
        import csv as _csv
        output = io.StringIO()
        writer = _csv.writer(output)
        writer.writerow(headers)
        for p in projects:
            writer.writerow([p.get(c, "") or "" for c in cols])
        from starlette.responses import Response
        safe_name = re.sub(r'[^\w\s-]', '', client["name"]).strip().replace(' ', '_')
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_pipeline.csv"'},
        )

    # XLSX via openpyxl
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Pipeline"
    ws.append(headers)
    for p in projects:
        ws.append([p.get(c, "") or "" for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from starlette.responses import Response
    safe_name = re.sub(r'[^\w\s-]', '', client["name"]).strip().replace(' ', '_')
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_pipeline.xlsx"'},
    )
```

Note: Uses openpyxl directly (already a dependency) and inline CSV. No dependency on exporter helper functions.

- [ ] **Step 2: Add export buttons to pipeline template**

In `_project_pipeline.html` header, add:

```html
<a href="/clients/{{ client.id }}/projects/pipeline/export?format=xlsx" class="text-xs text-marsh hover:underline no-print">Export XLSX</a>
<a href="/clients/{{ client.id }}/projects/pipeline/export?format=csv" class="text-xs text-gray-400 hover:underline no-print ml-1">CSV</a>
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_project_pipeline.html
git commit -m "feat: project pipeline XLSX/CSV export"
```

---

### Task 8: Timeline Export (PDF)

**Files:**
- Modify: `src/policydb/web/routes/clients.py`

- [ ] **Step 0: Verify fpdf2 is in pyproject.toml**

Check `pyproject.toml` for `fpdf2` in dependencies. If missing, add it: `"fpdf2>=2.7"`. Then `pip install -e .` to ensure it's available.

- [ ] **Step 1: Add timeline PDF export endpoint**

```python
@router.get("/clients/{client_id}/projects/pipeline/timeline")
def project_timeline_export(
    client_id: int,
    format: str = "pdf",
    conn=Depends(get_db),
):
    """Export project timeline as PDF."""
    from fpdf import FPDF

    client = conn.execute("SELECT name FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not client:
        return HTMLResponse("Not found", status_code=404)

    projects = _get_project_pipeline(conn, client_id)
    dated = [p for p in projects if p.get("start_date") or p.get("target_completion")]

    if not dated:
        return HTMLResponse("No projects with dates to render", status_code=400)

    # Build the PDF timeline
    pdf = FPDF()
    pdf.add_page("L")  # landscape
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"{client['name']} - Project Pipeline Timeline", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, f"Generated {_date.today().strftime('%B %d, %Y')}", ln=True)
    pdf.ln(5)

    # Compute date range
    all_dates = []
    for p in dated:
        if p.get("start_date"): all_dates.append(p["start_date"])
        if p.get("target_completion"): all_dates.append(p["target_completion"])
        if p.get("insurance_needed_by"): all_dates.append(p["insurance_needed_by"])
    min_date = min(all_dates)
    max_date = max(all_dates)

    from datetime import date as _date
    d_min = _date.fromisoformat(min_date)
    d_max = _date.fromisoformat(max_date)
    total_days = max((d_max - d_min).days, 1)

    chart_x = 60
    chart_w = 210  # landscape width minus margins
    bar_h = 8
    gap = 3

    # Status colors
    colors = {
        "Upcoming": (180, 180, 180),
        "Quoting": (59, 130, 246),
        "Bound": (34, 197, 94),
        "Active": (34, 197, 94),
        "Complete": (156, 163, 175),
    }

    pdf.set_font("Helvetica", "", 8)
    for p in dated:
        s = p.get("start_date") or p.get("target_completion")
        e = p.get("target_completion") or p.get("start_date")
        ds = _date.fromisoformat(s)
        de = _date.fromisoformat(e)

        x_start = chart_x + ((ds - d_min).days / total_days) * chart_w
        x_width = max(((de - ds).days / total_days) * chart_w, 3)

        # Label
        pdf.set_xy(5, pdf.get_y())
        pdf.cell(55, bar_h, p["name"][:25], 0, 0)

        # Bar
        r, g, b = colors.get(p.get("status", ""), (180, 180, 180))
        pdf.set_fill_color(r, g, b)
        pdf.rect(x_start, pdf.get_y(), x_width, bar_h, "F")

        # Insurance needed marker
        if p.get("insurance_needed_by"):
            di = _date.fromisoformat(p["insurance_needed_by"])
            x_ins = chart_x + ((di - d_min).days / total_days) * chart_w
            pdf.set_draw_color(220, 38, 38)
            pdf.line(x_ins, pdf.get_y(), x_ins, pdf.get_y() + bar_h)

        pdf.ln(bar_h + gap)

    content = pdf.output()
    from starlette.responses import Response
    return Response(
        content=bytes(content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{client["name"]}_timeline.pdf"'},
    )
```

- [ ] **Step 2: Add timeline export button to pipeline template**

In `_project_pipeline.html` header, add:

```html
<a href="/clients/{{ client.id }}/projects/pipeline/timeline?format=pdf" class="text-xs text-marsh hover:underline no-print ml-2">Timeline PDF</a>
```

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/routes/clients.py src/policydb/web/templates/clients/_project_pipeline.html
git commit -m "feat: project timeline PDF export"
```

---

### Task 9: Final Integration Test

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Manual workflow test**

Run: `policydb serve`

1. **Create pipeline projects:** Navigate to a client. Click "+ Add Project" in the pipeline section. Fill in name, type, status, dates, value via contenteditable cells.
2. **Link policies:** Create opportunities for the project (set `project_name` on policy create). Verify coverage column updates.
3. **Timeline bar:** Add dates to 2+ projects. Verify the timeline renders with colored bars and insurance-needed markers.
4. **Exports:** Click "Export XLSX" and "Timeline PDF". Verify files download with correct data.
5. **Settings:** Navigate to `/settings`. Verify Project Stages and Project Types cards appear. Add/remove/reorder items.
6. **Coverage expansion:** Click a coverage cell. Verify inline detail shows linked policies with status dots.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for project pipeline"
```
