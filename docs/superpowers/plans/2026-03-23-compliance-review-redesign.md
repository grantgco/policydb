# Compliance Review Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the compliance review page so users never lose context during review, add location-aware JSON import, and implement professional XLSX + PDF export reports.

**Architecture:** Location-tabbed layout with targeted HTMX partial swaps replaces full-page reloads. All CRUD operations return the updated partial + OOB swaps for summary/matrix. Export uses openpyxl for XLSX and fpdf2 for PDF, following existing exporter patterns.

**Tech Stack:** FastAPI, Jinja2, HTMX (OOB swaps), openpyxl, fpdf2, Pillow (optional for logo resize)

**Spec:** `docs/superpowers/specs/2026-03-23-compliance-review-redesign.md`

---

## Task 0: Add Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1:** Add `fpdf2>=2.7` to the dependencies list in `pyproject.toml`. It is currently only a lazy import — making it explicit ensures `pip install` works.

- [ ] **Step 2:** Optionally add `Pillow>=10.0` for logo resize. If omitted, the logo upload code handles it gracefully (fpdf2 scales at render time).

- [ ] **Step 3:** Run `pip install -e .` to verify installation. Commit.

```bash
git add pyproject.toml
git commit -m "deps: add fpdf2 for PDF compliance reports"
```

---

## Task 1: Add Persistent IDs, Wrapper Elements, and Location Tab Bar

**Files:**
- Modify: `src/policydb/web/templates/compliance/_summary_banner.html:11`
- Modify: `src/policydb/web/templates/compliance/index.html:15-200`

This task adds the stable DOM IDs that all subsequent OOB swaps depend on.

- [ ] **Step 1:** In `_summary_banner.html`, add `id="compliance-summary"` to the outer `<div>` at line 11.

- [ ] **Step 2:** In `index.html`, wrap the entire sources `<details>` element (lines ~19-106) in `<div id="sources-container">`. Do NOT wrap `<tbody>` — that would be invalid HTML inside a table.

- [ ] **Step 3:** In `index.html`, after the matrix include (`_matrix.html`), add the location workspace structure:

```html
<div id="location-workspace" class="mt-6">
  <div class="border-b border-gray-200 mb-4">
    <div class="flex gap-1 overflow-x-auto" id="location-tab-bar">
      {% for loc in locations %}
      <button class="px-3 py-2 text-sm font-medium border-b-2 whitespace-nowrap
                     {% if loc.project.id == active_location_id %}border-marsh text-marsh{% else %}border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300{% endif %}"
              hx-get="/compliance/client/{{ client_id }}/location/{{ loc.project.id }}"
              hx-target="#location-tab-content"
              hx-push-url="?location={{ loc.project.id }}"
              hx-swap="innerHTML">
        {{ loc.project.name[:20] }}
        {% if loc.compliance_summary.gap > 0 %}<span class="ml-1 text-[10px] bg-red-100 text-red-600 px-1.5 rounded-full">{{ loc.compliance_summary.gap }}</span>{% endif %}
      </button>
      {% endfor %}
      <button class="px-3 py-2 text-sm font-medium border-b-2 whitespace-nowrap
                     {% if active_location_id == 0 %}border-marsh text-marsh{% else %}border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300{% endif %}"
              hx-get="/compliance/client/{{ client_id }}/location/corporate"
              hx-target="#location-tab-content"
              hx-push-url="?location=0"
              hx-swap="innerHTML">
        Corporate
      </button>
    </div>
  </div>
  <div id="location-tab-content">
    {# Loaded on page render if ?location= param present, otherwise empty #}
  </div>
</div>
```

- [ ] **Step 4:** In `index.html`, add JS to auto-load active tab from URL param:

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
  const params = new URLSearchParams(window.location.search);
  const loc = params.get('location');
  if (loc) {
    const btn = document.querySelector('#location-tab-bar button[hx-push-url="?location=' + loc + '"]');
    if (btn) btn.click();
  } else {
    // Auto-load first tab
    const first = document.querySelector('#location-tab-bar button');
    if (first) first.click();
  }
});
</script>
```

- [ ] **Step 5:** In `compliance.py`, update `_compliance_context()` (line ~41) to accept and pass `active_location_id`. Since `_compliance_context` is a helper (not a route handler), it cannot use `Query()` directly. Instead, extract from the request in the helper:

```python
def _compliance_context(conn, client_id, request):
    active_location_id = int(request.query_params.get("location", 0))
    # ... existing code ...
    ctx["active_location_id"] = active_location_id
    return ctx
```

- [ ] **Step 5b:** Remove the existing `<div id="location-detail"></div>` from `_matrix.html` (line ~239) — it is replaced by `#location-tab-content` in the new workspace. Also change `hx-target="#location-detail"` at `_matrix.html` lines ~39 and ~158 to `hx-target="#location-tab-content"`. Update the close button in `_location_detail.html` line ~21 that references `document.getElementById('location-detail')` to use `location-tab-content`.

- [ ] **Step 6:** Verify server starts, navigate to compliance page, confirm tab bar renders with location names. Commit.

```bash
git add src/policydb/web/templates/compliance/_summary_banner.html \
        src/policydb/web/templates/compliance/index.html \
        src/policydb/web/routes/compliance.py
git commit -m "feat: add location tab bar and persistent zone IDs for compliance review"
```

---

## Task 2: Fix All hx-target="body" References

**Files:**
- Modify: `src/policydb/web/templates/compliance/index.html:63,77,148`
- Modify: `src/policydb/web/templates/compliance/_location_detail.html:154,168`
- Modify: `src/policydb/web/templates/compliance/_requirement_row_edit.html:5,86,89`
- Modify: `src/policydb/web/templates/compliance/_source_row.html:25`
- Modify: `src/policydb/web/templates/compliance/_source_row_edit.html:32`

- [ ] **Step 1:** In `index.html`, change `hx-target="body"` at lines ~63, ~77 (source operations) to `hx-target="#sources-container"`.

- [ ] **Step 2:** In `index.html`, change `hx-target="body"` at line ~148 (add requirement form) to `hx-target="#location-tab-content"`.

- [ ] **Step 3:** In `_location_detail.html`, change `hx-target="body"` at lines ~154, ~168 to `hx-target="#location-tab-content"`.

- [ ] **Step 4:** In `_requirement_row_edit.html`, change `hx-target="body"` at lines ~5, ~89 to `hx-target="#location-tab-content"`.

- [ ] **Step 5:** In `_requirement_row_edit.html`, replace the cancel button at line ~86 (`onclick="window.location.reload()"`) with:

```html
<button type="button"
  hx-get="/compliance/client/{{ client_id }}/requirements/{{ req.id }}/row"
  hx-target="#req-row-{{ req.id }}"
  hx-swap="outerHTML"
  class="text-xs text-gray-500 hover:underline">Cancel</button>
```

- [ ] **Step 6:** In `_source_row.html`, change `hx-target="body"` at line ~25 to `hx-target="#sources-container"`.

- [ ] **Step 7:** In `_source_row_edit.html`, change `hx-target="body"` at line ~32 to `hx-target="#sources-container"`.

- [ ] **Step 8:** Commit.

```bash
git add src/policydb/web/templates/compliance/
git commit -m "fix: replace all hx-target=body with targeted partials in compliance templates"
```

---

## Task 3: Refactor Route Handlers to Return Targeted Partials + OOB

**Files:**
- Modify: `src/policydb/web/routes/compliance.py` (multiple route handlers)

This is the core backend change. Each CRUD handler must return the affected partial + OOB swaps for `#compliance-summary` and `#compliance-matrix`.

- [ ] **Step 0:** Extract the requirement row markup from inside `_location_detail.html`'s `{% for req in ... %}` loop into a new partial `compliance/_requirement_row.html`. Add `id="req-row-{{ req.id }}"` to the outer element. Include this partial from `_location_detail.html` inside the loop. This is needed for per-row OOB swaps and the cancel-edit restore.

- [ ] **Step 1:** Create a helper function `_oob_summary_and_matrix()` that generates OOB HTML for both zones. Use `hx-swap-oob="outerHTML"` to replace the entire element (avoids double-wrapping since the template itself renders the outer div):

```python
def _oob_summary_and_matrix(request, conn, client_id):
    """Return OOB HTML string for summary banner + matrix refresh."""
    ctx = _compliance_context(conn, client_id, request)
    summary_resp = templates.TemplateResponse("compliance/_summary_banner.html", {
        "request": request, **ctx
    })
    matrix_resp = templates.TemplateResponse("compliance/_matrix.html", {
        "request": request, **ctx
    })
    # outerHTML replaces the entire #compliance-summary / #compliance-matrix divs
    summary_oob = summary_resp.body.decode().replace(
        'id="compliance-summary"', 'id="compliance-summary" hx-swap-oob="outerHTML"', 1
    )
    matrix_oob = matrix_resp.body.decode().replace(
        'id="compliance-matrix"', 'id="compliance-matrix" hx-swap-oob="outerHTML"', 1
    )
    return summary_oob + matrix_oob
```

- [ ] **Step 1b:** Create a helper `_location_response()` that builds the location detail partial for a given project_id:

```python
def _location_response(request, conn, client_id, project_id):
    """Build _location_detail.html context and render for a given location."""
    # Use get_client_compliance_data to find the location's data
    data = get_client_compliance_data(conn, client_id)
    loc = next((l for l in data["locations"] if l["project"]["id"] == project_id), None)
    if not loc:
        return ""
    # Add navigation context
    locs = data["locations"]
    idx = next((i for i, l in enumerate(locs) if l["project"]["id"] == project_id), 0)
    next_loc = locs[idx + 1]["project"] if idx + 1 < len(locs) else None
    return templates.TemplateResponse("compliance/_location_detail.html", {
        "request": request, "client_id": client_id, "loc": loc,
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "policy_types": cfg.get("policy_types", []),
        "location_index": idx, "location_count": len(locs),
        "next_location": next_loc,
    }).body.decode()
```

- [ ] **Step 2:** Refactor `requirements_status()` (line ~649) to return the updated requirement row + OOB:

```python
# After saving status...
oob = _oob_summary_and_matrix(request, conn, client_id)
row_html = templates.TemplateResponse("compliance/_requirement_row.html", {
    "request": request, "req": updated_req, "client_id": client_id, ...
}).body.decode()
return HTMLResponse(row_html + oob)
```

- [ ] **Step 3:** Refactor `requirements_edit()` (line ~584), `requirements_delete()` (line ~683), `requirements_add()` (line ~495), and `requirements_link_policy()` (line ~666) to return `_location_detail.html` + OOB instead of full `index.html`.

For each, replace the final `return templates.TemplateResponse("compliance/index.html", ctx)` with:

```python
loc_html = _location_response(request, conn, client_id, project_id)
oob = _oob_summary_and_matrix(request, conn, client_id)
return HTMLResponse(loc_html + oob)
```

- [ ] **Step 4:** Refactor `sources_add()` (line ~333), `sources_edit()` (line ~360), and `sources_delete()` (line ~472) to return sources container partial + OOB.

- [ ] **Step 5:** Add the requirement row restore route:

```python
@router.get("/client/{client_id}/requirements/{req_id}/row", response_class=HTMLResponse)
def requirement_row_display(client_id: int, req_id: int, request: Request, conn=Depends(get_db)):
    req = dict(conn.execute("SELECT * FROM coverage_requirements WHERE id=?", (req_id,)).fetchone())
    # Parse endorsements JSON
    try:
        req["_endorsements_list"] = json.loads(req.get("required_endorsements") or "[]")
    except (ValueError, TypeError):
        req["_endorsements_list"] = []
    return templates.TemplateResponse("compliance/_requirement_row.html", {
        "request": request, "req": req, "client_id": client_id,
    })
```

- [ ] **Step 6:** Add the corporate location route. **IMPORTANT:** This literal route MUST be placed BEFORE the parameterized `location_detail()` route (`/client/{cid}/location/{project_id}`) in the file to avoid FastAPI capturing "corporate" as a project_id (per CLAUDE.md: literals before parameterized routes).

```python
@router.get("/client/{client_id}/location/corporate", response_class=HTMLResponse)
def location_corporate(client_id: int, request: Request, conn=Depends(get_db)):
    """Corporate (client-wide) requirements tab."""
    # Fetch requirements where project_id IS NULL
    reqs = [dict(r) for r in conn.execute(
        "SELECT * FROM coverage_requirements WHERE client_id=? AND project_id IS NULL ORDER BY coverage_line",
        (client_id,),
    ).fetchall()]
    for req in reqs:
        try:
            req["_endorsements_list"] = json.loads(req.get("required_endorsements") or "[]")
        except (ValueError, TypeError):
            req["_endorsements_list"] = []
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM requirement_sources WHERE client_id=? AND (project_id IS NULL) ORDER BY name",
        (client_id,),
    ).fetchall()]
    return templates.TemplateResponse("compliance/_location_detail.html", {
        "request": request, "client_id": client_id,
        "loc": {"project": {"id": 0, "name": "Corporate"}, "requirements": reqs, "sources": sources},
        "compliance_statuses": cfg.get("compliance_statuses", []),
        "policy_types": cfg.get("policy_types", []),
    })
```

- [ ] **Step 7:** Verify: navigate to compliance page, click a location tab, change a status — matrix should update live. Edit a requirement, cancel — should stay in the location. Commit.

```bash
git add src/policydb/web/routes/compliance.py
git commit -m "feat: refactor compliance routes to targeted partials + OOB swaps"
```

---

## Task 4: Location Detail Navigation Footer

**Files:**
- Modify: `src/policydb/web/templates/compliance/_location_detail.html`

- [ ] **Step 1:** At the end of `_location_detail.html` (before the closing `{% endif %}`), add a navigation footer:

```html
{# Location navigation footer #}
{% if location_index is defined %}
<div class="flex items-center justify-between mt-4 pt-3 border-t border-gray-100 text-xs text-gray-400">
  <span>Location {{ location_index + 1 }} of {{ location_count }}</span>
  {% if next_location %}
  <button class="text-marsh font-medium hover:underline"
          hx-get="/compliance/client/{{ client_id }}/location/{{ next_location.id }}"
          hx-target="#location-tab-content"
          hx-push-url="?location={{ next_location.id }}">
    Next: {{ next_location.name }} &rarr;
  </button>
  {% else %}
  <span class="text-green-600 font-medium">All locations reviewed</span>
  {% endif %}
</div>
{% endif %}
```

- [ ] **Step 2:** Update the `location_detail()` route to pass `location_index`, `location_count`, and `next_location` in the template context. These are derived from the full locations list.

- [ ] **Step 3:** Verify: click through locations using "Next →" link. Confirm tab bar highlights correctly. Commit.

```bash
git add src/policydb/web/templates/compliance/_location_detail.html \
        src/policydb/web/routes/compliance.py
git commit -m "feat: add next-location navigation footer to compliance review"
```

---

## Task 5: JSON Import Location Selector

**Files:**
- Modify: `src/policydb/web/templates/_ai_import_panel.html:50,238`
- Modify: `src/policydb/web/routes/compliance.py` (ai_import_parse + prompt)

- [ ] **Step 1:** In `_ai_import_panel.html`, after line ~50 (after context badges), add a conditional location selector:

```html
{% if locations is defined and locations %}
<div class="mb-4">
  <label class="block text-xs font-medium text-gray-500 uppercase tracking-wide mb-1.5">Location</label>
  <select name="project_id" id="ai-import-location"
    class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-marsh">
    <option value="">Corporate (All Locations)</option>
    {% for loc in locations %}
    <option value="{{ loc.project.id }}" {% if loc.project.id == active_location_id %}selected{% endif %}>
      {{ loc.project.name }}
    </option>
    {% endfor %}
  </select>
</div>
{% endif %}
```

- [ ] **Step 2:** In the JS that submits the parse form (line ~238), add project_id to the request body:

```javascript
var locSelect = document.getElementById('ai-import-location');
var projectId = locSelect ? locSelect.value : '';
// Include in the fetch body
body: 'json_text=' + encodeURIComponent(raw) + '&project_id=' + encodeURIComponent(projectId)
```

- [ ] **Step 3:** In `compliance.py`, update `_compliance_context()` and the AI import prompt route to pass `locations` and `active_location_id` to the template context.

- [ ] **Step 4:** Verify: open AI import from a location tab — location should be pre-selected. Switch to Corporate — location should default to empty. Commit.

```bash
git add src/policydb/web/templates/_ai_import_panel.html \
        src/policydb/web/routes/compliance.py
git commit -m "feat: add location selector to AI import panel for COPE data targeting"
```

---

## Task 6: XLSX Export

**Files:**
- Modify: `src/policydb/exporter.py`
- Modify: `src/policydb/web/routes/compliance.py`
- Modify: `src/policydb/web/templates/compliance/_summary_banner.html`

- [ ] **Step 1:** In `exporter.py`, add `export_compliance_xlsx(conn, client_id)`:

Build 5 sheets following the existing `_write_sheet()` pattern:
1. **Executive Summary** — client name, date, scores (use merged cells + header styling)
2. **Compliance Matrix** — coverage lines × locations with conditional fills
3. **Gap Detail** — filtered to non-compliant only
4. **All Requirements** — flat table with auto-filter
5. **COPE Data** — JOIN `cope_data` with `projects` for address

Use `get_client_compliance_data(conn, client_id)` for the data. Return bytes via `_wb_to_bytes(wb)`.

- [ ] **Step 2:** In `compliance.py`, add the export route:

```python
@router.get("/client/{client_id}/export/xlsx")
def export_xlsx(client_id: int, conn=Depends(get_db)):
    from policydb.exporter import export_compliance_xlsx
    xlsx_bytes, filename = export_compliance_xlsx(conn, client_id)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
```

- [ ] **Step 3:** In `_summary_banner.html`, wire the XLSX export button to `/compliance/client/{{ client_id }}/export/xlsx`.

- [ ] **Step 4:** Verify: click XLSX export, open in Excel, confirm all 5 sheets render correctly with data. Commit.

```bash
git add src/policydb/exporter.py src/policydb/web/routes/compliance.py \
        src/policydb/web/templates/compliance/_summary_banner.html
git commit -m "feat: add XLSX compliance export with 5-sheet workbook"
```

---

## Task 7: PDF Export

**Files:**
- Modify: `src/policydb/exporter.py`
- Modify: `src/policydb/web/routes/compliance.py`
- Modify: `src/policydb/config.py`

- [ ] **Step 1:** In `config.py`, add `report_logo_path` to `_DEFAULTS`:

```python
"report_logo_path": str(Path.home() / ".policydb" / "logo.png"),
```

- [ ] **Step 2:** In `exporter.py`, add `export_compliance_pdf(conn, client_id)`:

Build PDF using fpdf2 following the Combined layout:
1. **Header** — logo (if exists at config path, auto-scaled) + title + client name + date
2. **Executive Summary** — score boxes + key findings
3. **Compliance Matrix** — table with colored cell fills
4. **Gap Drill-Down** — filtered non-compliant rows with detail
5. **Per-Location Sections** — page break per location, full requirement tables
6. **COPE Data** — table if any COPE data exists

Colors: green (#dcfce7), red (#fef2f2), amber (#fefce8). Return bytes.

- [ ] **Step 3:** In `compliance.py`, add the PDF export route:

```python
@router.get("/client/{client_id}/export/pdf")
def export_pdf(client_id: int, conn=Depends(get_db)):
    from policydb.exporter import export_compliance_pdf
    pdf_bytes, filename = export_compliance_pdf(conn, client_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
```

- [ ] **Step 4:** Wire the PDF export button in `_summary_banner.html`.

- [ ] **Step 5:** Verify: click PDF export, open in viewer, confirm all sections render with correct layout and colors. Commit.

```bash
git add src/policydb/exporter.py src/policydb/web/routes/compliance.py \
        src/policydb/config.py src/policydb/web/templates/compliance/_summary_banner.html
git commit -m "feat: add PDF compliance report with fpdf2"
```

---

## Task 8: Logo Upload in Settings

**Files:**
- Modify: `src/policydb/web/routes/settings.py`
- Modify: `src/policydb/web/templates/settings.html`

- [ ] **Step 1:** In `settings.py`, add logo upload and remove routes:

Note: The settings router already has `prefix="/settings"`, so route decorators use `/logo` (not `/settings/logo`) to produce the correct URL path `/settings/logo`.

```python
@router.post("/logo", response_class=HTMLResponse)
async def upload_logo(request: Request, file: UploadFile = File(...)):
    import shutil
    logo_path = Path(cfg.get("report_logo_path"))
    logo_path.parent.mkdir(parents=True, exist_ok=True)
    with open(logo_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # Optional: resize with Pillow if available
    try:
        from PIL import Image
        img = Image.open(logo_path)
        img.thumbnail((300, 80))
        img.save(logo_path)
    except ImportError:
        pass  # fpdf2 handles scaling at render time
    return HTMLResponse('<div id="logo-preview" hx-swap-oob="innerHTML">...(preview html)...</div>')

@router.delete("/logo")
def remove_logo():
    logo_path = Path(cfg.get("report_logo_path"))
    if logo_path.exists():
        logo_path.unlink()
    return JSONResponse({"ok": True})
```

- [ ] **Step 2:** In `settings.html`, add a "Report Logo" card in the appropriate section:

```html
<div class="card mb-6">
  <div class="px-5 py-3 border-b border-gray-100">
    <h3 class="text-sm font-semibold text-gray-700">Report Logo</h3>
    <p class="text-xs text-gray-400">Appears in PDF compliance reports</p>
  </div>
  <div class="px-5 py-4" id="logo-preview">
    {% if logo_exists %}
    <img src="/settings/logo/preview" class="max-h-12 mb-3" alt="Current logo">
    <button hx-delete="/settings/logo" hx-target="#logo-preview" hx-swap="innerHTML"
            class="text-xs text-red-500 hover:underline">Remove Logo</button>
    {% else %}
    <p class="text-xs text-gray-400 mb-3">No logo uploaded</p>
    {% endif %}
    <form hx-post="/settings/logo" hx-target="#logo-preview" hx-swap="innerHTML"
          hx-encoding="multipart/form-data" class="mt-2">
      <input type="file" name="file" accept="image/*" class="text-xs">
      <button type="submit" class="ml-2 text-xs text-marsh hover:underline">Upload</button>
    </form>
  </div>
</div>
```

- [ ] **Step 3:** Add a `GET /settings/logo/preview` route that serves the logo file if it exists (for the `<img>` tag).

- [ ] **Step 4:** Verify: upload a logo in settings, confirm it appears in the preview. Export a PDF, confirm logo appears in the header. Remove logo, export again, confirm text-only header. Commit.

```bash
git add src/policydb/web/routes/settings.py src/policydb/web/templates/settings.html
git commit -m "feat: add logo upload to settings for PDF compliance reports"
```

---

## Task 9: QA and Final Verification

**Files:** None (testing only)

- [ ] **Step 1:** Full workflow test: navigate to compliance page for a client with multiple locations. Verify tab bar renders.

- [ ] **Step 2:** Click through locations, change statuses — verify matrix and summary update live, location stays active.

- [ ] **Step 3:** Edit a requirement within a location, save — verify location persists. Cancel — verify row restores without page reload.

- [ ] **Step 4:** Open AI import from a location tab — verify location is pre-selected. Import JSON with COPE data — verify COPE lands in the correct location.

- [ ] **Step 5:** Export XLSX — verify all 5 sheets have correct data and formatting.

- [ ] **Step 6:** Export PDF — verify all sections render, logo appears if uploaded, colors are correct.

- [ ] **Step 7:** Test edge cases: 0 locations (only Corporate tab), empty COPE data, no requirements.

- [ ] **Step 8:** Take screenshots of key pages for PR documentation.

- [ ] **Step 9:** Final commit and push.

```bash
git push origin worktree-issue-triage-fixes
```
