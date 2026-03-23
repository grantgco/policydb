# Compliance Review — Workflow Persistence + Export Reports

**Date:** 2026-03-23
**Issues:** #27 (UI Workflow Improvements), #24 (Compliance Review XLSX/PDF Export)
**Status:** Design approved

---

## Problem Statement

The compliance review page loses user context on every CRUD operation — status changes, requirement edits, and deletes all trigger full-page reloads that collapse the location drill-down. Users must re-click the same location after each action. Additionally, the XLSX and PDF export buttons return 404 — no export functionality exists.

## Design Overview

Three interconnected changes:

1. **Workflow Persistence** — Restructure the compliance page into a location-tabbed layout with targeted HTMX partial swaps. No full-page reloads during review work.
2. **JSON Import with Location Context** — Add a location selector to the AI import slideover so COPE data lands in the correct location.
3. **Professional Exports** — Server-generated PDF (via `fpdf2`, already used in codebase) and formatted XLSX (via `openpyxl`) following the Combined report layout: executive summary → matrix → gap drill-down → per-location detail.

**No new migrations required.** All needed tables (`coverage_requirements`, `requirement_sources`, `cope_data`, `projects`) already exist via migration 066.

---

## 1. Workflow Persistence (#27)

### Page Structure

The compliance page is restructured into three persistent zones:

| Zone | ID | Content | Notes |
|------|----|---------|-------|
| Summary Banner | `#compliance-summary` | Donut chart, scores, export buttons | **Must add this ID to `_summary_banner.html` outer div** |
| Matrix Overview | `#compliance-matrix` | Heatmap grid (coverage × locations) | Already has this ID |
| Location Workspace | `#location-workspace` | Tab bar + active location content | New container div |

**ID Migration:** The current `#location-detail` div (in `_matrix.html` line 239) becomes `#location-tab-content` inside the new `#location-workspace` zone. All existing `hx-target="#location-detail"` references in `_matrix.html` and the `location_detail()` route must be updated to `#location-tab-content`.

### Location Tabs

- **Tab bar** renders one tab per location (from `projects` table) plus a "Corporate" tab for client-wide requirements.
- Active tab loads its content via `hx-get="/compliance/client/{cid}/location/{pid}"` into `#location-tab-content`.
- **Corporate tab** uses route `GET /compliance/client/{cid}/location/corporate` — displays only client-wide requirements (`project_id IS NULL`) without inheritance resolution. Uses `project_id=0` convention (0 = corporate).
- Tab state persists via:
  - `hx-push-url="?location={project_id}"` — URL reflects active location
  - `sessionStorage` fallback — remembers last tab per client
  - On page load, if `?location=` param exists, that tab activates automatically
- **Tab overflow (8+ locations):** Tab bar uses `overflow-x-auto` with horizontal scroll. Matches the app's existing pattern for narrow viewports.

### Targeted HTMX Swaps (No Full-Page Reloads)

Every CRUD operation within a location returns targeted partials instead of the full page:

| Operation | Current Behavior | New Behavior |
|-----------|-----------------|--------------|
| Status change (PATCH) | Returns `_matrix.html` only | Returns updated requirement row + OOB `#compliance-summary` + OOB `#compliance-matrix` |
| Requirement edit (POST) | Returns full `index.html` via `hx-target="body"` | Returns updated `_location_detail.html` into `#location-tab-content` + OOB summary + OOB matrix |
| Requirement delete (POST) | Returns full page | Returns updated `_location_detail.html` into `#location-tab-content` + OOB summary + OOB matrix |
| Requirement add (POST) | Returns full page | Returns updated `_location_detail.html` into `#location-tab-content` + OOB summary + OOB matrix |
| Source add/edit/delete | Returns full page | Returns updated sources partial + OOB summary + OOB matrix |
| Cancel edit | `window.location.reload()` | `hx-get` restores display row (swap `outerHTML` on the row) |

### Template `hx-target="body"` Occurrences to Fix

All of these must change from `hx-target="body"` to targeted partials:

| Template | Lines | New Target |
|----------|-------|------------|
| `_location_detail.html` | ~154, ~168 | `hx-target="#location-tab-content"` |
| `_requirement_row_edit.html` | ~5, ~89 | `hx-target="#location-tab-content"` (save); `hx-get` row restore (cancel) |
| `_source_row.html` | ~25 | `hx-target="#sources-container"` |
| `_source_row_edit.html` | ~32 | `hx-target="#sources-container"` |
| `index.html` | ~63, ~77, ~148 | `hx-target="#sources-container"` or `#location-tab-content"` as appropriate |

### New Route: Requirement Row Restore

Add `GET /compliance/client/{cid}/requirements/{rid}/row` — returns the display-mode requirement row HTML. Analogous to the existing `GET /compliance/client/{cid}/sources/{sid}/row` pattern. Used by the cancel button on `_requirement_row_edit.html`.

### Location Navigation

- **"Next: [Location Name] →"** link at the bottom of each location tab advances to the next tab.
- **"Location X of Y"** counter shows progress.
- Matrix heatmap cells remain clickable — clicking a cell switches to that location's tab.

### Live Matrix Updates

When a status changes within a location tab:
1. The PATCH endpoint saves the new status
2. Response includes the updated requirement row HTML
3. OOB swap: `#compliance-summary` with recalculated scores
4. OOB swap: `#compliance-matrix` with updated heatmap colors

The user sees the matrix update in real-time as they work through requirements.

---

## 2. JSON Import with Location Context

### Location Selector in Import Slideover

Add a `<select>` dropdown to `_ai_import_panel.html` between the header and step content:

- Options: "Corporate (All Locations)" + each location from `projects` table
- **Conditional rendering:** The location selector only appears when `locations` context variable is provided (compliance import passes it, policy import does not). This keeps the shared panel working for both contexts.
- **Pre-selection logic:** If a location tab is active when the user opens the import, pre-select that location in the dropdown
- The selected `project_id` is included in the POST to `/ai-import/parse`

### COPE Data Handling

| Location Selected? | COPE in JSON? | Behavior |
|-------------------|---------------|----------|
| Yes | Yes | COPE data imports into `cope_data` for that `project_id` |
| Yes | No | Normal — no COPE action needed |
| No (Corporate) | Yes | Warning banner: "COPE data found but no location selected — skipped" |
| No (Corporate) | No | Normal — requirements created as client-wide |

### Post-Import Behavior

- Success banner shows count of imported requirements
- If a location was selected, the active location tab refreshes to show new requirements
- Matrix updates via OOB swap
- Import confirmation diff (from #10 fix) shows fields being created/updated

### Prompt Context

When a location is selected, the AI prompt template includes location name and address for more targeted extraction.

---

## 3. Professional Exports (#24)

### Dependencies

Use `fpdf2` for server-side PDF generation — consistent with existing codebase usage (lazy import in `clients.py`). No new dependency needed (already available). If `fpdf2` proves too limited for the report layout, fall back to a print-optimized HTML template with `window.print()`.

### Logo Support

- **Storage path:** `~/.policydb/logo.png` (saved by the upload handler, any format converted to PNG)
- **Upload UI:** New section in `/settings` page — "Report Logo" card with:
  - Current logo preview (thumbnail) if one exists
  - File upload input (`<input type="file" accept="image/*">`) with "Upload Logo" button
  - "Remove Logo" button if one exists
  - Route: `POST /settings/logo` (multipart form, saves to `~/.policydb/logo.png`), `DELETE /settings/logo` (removes the file)
- **Fallback:** If no logo file exists, PDF header renders client name as styled text only.
- **Config key:** `report_logo_path` in config.yaml (defaults to `~/.policydb/logo.png`). Must be added to `_DEFAULTS` in `config.py`.
- **Resize:** On upload, the image is resized to max 300px wide / 80px tall (preserving aspect ratio) using `Pillow`. If `Pillow` is not available, the raw file is saved as-is and `fpdf2` handles scaling at render time.

### XLSX Workbook Structure

**5 sheets:**

| Sheet | Type | Content |
|-------|------|---------|
| Executive Summary | Formatted | Client name, date, overall score, gap count, location count, key findings |
| Compliance Matrix | Formatted | Coverage lines × locations grid with conditional fill colors (green=compliant, red=gap, amber=partial, purple=N/A) |
| Gap Detail | Formatted | Non-compliant rows only: location, coverage, required limit, in-place limit, shortfall, source reference. Sorted by severity. |
| All Requirements | Raw data | Every requirement across all locations: location, coverage line, required limit, max deductible, deductible type, endorsements, compliance status, linked policy, source name, source clause ref, notes. Auto-filtered. |
| COPE Data | Raw data | One row per location: project name, address (JOIN from `projects` table), construction type, year built, stories, sq footage, sprinklered, roof type, occupancy, protection class, TIV |

**Implementation:** New function `export_compliance_xlsx(conn, client_id)` in `exporter.py` following existing patterns. Route returns `Response(content=xlsx_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")` with `Content-Disposition: attachment; filename="..."` header — same pattern as existing export routes in `clients.py`.

### PDF Report Structure

**Combined layout (Option C from brainstorm):**

| Section | Content |
|---------|---------|
| Header | Logo (or text fallback) + "Insurance Compliance Review" + client name + date |
| Executive Summary | Score cards (% compliant, gap count, partial count, location count) + key findings bullets |
| Compliance Matrix | Compact heatmap grid with color legend |
| Gap Drill-Down | Only gaps/partials — coverage line, location, required vs in-place, shortfall, source reference |
| Per-Location Sections | One section per location with full requirement table: coverage, required limit, max deductible, deductible type, endorsements, compliance status, source reference, notes |
| COPE Data | Table of COPE data per location (only if COPE data exists for any location) |

**Implementation:**
- `fpdf2` builds the PDF programmatically (header, sections, tables)
- New function `export_compliance_pdf(conn, client_id)` in `exporter.py`
- Route handler at `GET /compliance/client/{cid}/export/pdf`
- Colors match the web UI: green (#dcfce7) for compliant, red (#fef2f2) for gap, amber (#fefce8) for partial

---

## 4. Route Changes Summary

### Modified Routes

| Route | Change |
|-------|--------|
| `POST .../requirements/{req_id}/edit` | Return `_location_detail.html` + OOB summary/matrix instead of full page |
| `POST .../requirements/{req_id}/delete` | Same — targeted partial |
| `POST .../requirements/add` | Same — targeted partial |
| `POST .../requirements/{req_id}/status` | Add OOB summary swap alongside matrix |
| `POST .../requirements/{req_id}/link-policy` | Targeted partial |
| `POST .../sources/add` | Return sources partial + OOB swaps |
| `POST .../sources/{sid}/edit` | Same |
| `POST .../sources/{sid}/delete` | Same |
| `POST .../ai-import/parse` | Accept `project_id` from location selector |

### New Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `GET /compliance/client/{cid}/requirements/{rid}/row` | GET | Requirement display row restore (cancel edit) |
| `GET /compliance/client/{cid}/location/corporate` | GET | Corporate (client-wide) requirements tab |
| `GET /compliance/client/{cid}/export/xlsx` | GET | Download XLSX workbook |
| `GET /compliance/client/{cid}/export/pdf` | GET | Download PDF report |

### Template Changes

| Template | Change |
|----------|--------|
| `compliance/index.html` | Add location tab bar inside `#location-workspace`, restructure zones with persistent IDs, fix `hx-target="body"` at lines ~63/77/148 |
| `compliance/_location_detail.html` | Add "Next location →" footer, change `hx-target="body"` at ~154/168 to `#location-tab-content` |
| `compliance/_requirement_row_edit.html` | Change `hx-target="body"` at ~5/89, replace `window.location.reload()` cancel with `hx-get` row restore |
| `compliance/_source_row.html` | Change `hx-target="body"` at ~25 to `#sources-container` |
| `compliance/_source_row_edit.html` | Change `hx-target="body"` at ~32 to `#sources-container` |
| `compliance/_summary_banner.html` | Add `id="compliance-summary"` to outer div, wire export buttons to real routes |
| `_ai_import_panel.html` | Add conditional location selector (only when `locations` context provided) |

---

## 5. File Changes Summary

| File | Type | Changes |
|------|------|---------|
| `src/policydb/web/routes/compliance.py` | Modify | Refactor all CRUD returns to targeted partials + OOB; add export routes; add requirement row restore route; add corporate location route |
| `src/policydb/web/templates/compliance/index.html` | Modify | Location tab bar, zone IDs, fix all `hx-target="body"` |
| `src/policydb/web/templates/compliance/_location_detail.html` | Modify | hx-target fixes, next-location nav |
| `src/policydb/web/templates/compliance/_requirement_row_edit.html` | Modify | Cancel button fix, hx-target fix |
| `src/policydb/web/templates/compliance/_source_row.html` | Modify | hx-target fix |
| `src/policydb/web/templates/compliance/_source_row_edit.html` | Modify | hx-target fix |
| `src/policydb/web/templates/compliance/_summary_banner.html` | Modify | Add `#compliance-summary` ID, wire export buttons |
| `src/policydb/web/templates/_ai_import_panel.html` | Modify | Conditional location selector |
| `src/policydb/exporter.py` | Modify | Add `export_compliance_xlsx()` and `export_compliance_pdf()` |
| `src/policydb/compliance.py` | Modify | Add helper for export data aggregation (COPE JOIN with projects for address) |
| `src/policydb/config.py` | Modify | Add `report_logo_path` to `_DEFAULTS` |
| `src/policydb/web/routes/settings.py` | Modify | Add `POST /settings/logo` upload + `DELETE /settings/logo` remove routes |
| `src/policydb/web/templates/settings.html` | Modify | Add "Report Logo" upload card section |

---

## Non-Goals

- Requirement-level field accept/reject during import (future enhancement)
- Bulk status changes across locations
- Requirement template builder UI redesign
- Logo cropping/editing UI (upload only — cropping done externally before upload)
