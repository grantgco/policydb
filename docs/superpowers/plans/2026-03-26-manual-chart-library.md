# Manual Chart Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a library of 8 manually-editable, presentation-ready chart templates within PolicyDB's existing `/charts` module.

**Architecture:** New `/charts/manual` routes serve a gallery page and per-chart editor pages. Each chart type is a self-contained Jinja2 template with Chart.js rendering and an editor panel for all values. Snapshots persist via the existing `chart_snapshots` table (migration 090 makes `client_id` nullable).

**Tech Stack:** Chart.js 4.x (canvas), Jinja2 templates, HTMX (snapshot load/save), existing `chart_snapshots` table, Marsh brand fonts (Noto Sans/Serif via Google Fonts).

**Spec:** `docs/superpowers/specs/2026-03-26-manual-chart-library-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/policydb/migrations/090_chart_snapshots_nullable_client.sql` | Make `client_id` nullable |
| Modify | `src/policydb/db.py` (~line 1190) | Wire migration 090 |
| Modify | `src/policydb/web/routes/charts.py` | Add manual chart routes + registry (BEFORE existing `/{client_id}` routes) |
| Modify | `src/policydb/web/templates/base.html` (~line 511) | Add "Manual Charts" nav link |
| Create | `src/policydb/web/templates/charts/manual/gallery.html` | Chart type picker + recent snapshots |
| Create | `src/policydb/web/templates/charts/manual/editor.html` | Shared editor layout (chart + panel) |
| Create | `src/policydb/web/templates/charts/manual/_tpl_rate_premium_baseline.html` | Rate & Premium chart + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_benchmark_distribution.html` | Benchmarking chart + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_loss_history.html` | Loss History chart + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_premium_allocation.html` | Premium Allocation chart + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_rate_trend_line.html` | Rate Trend chart + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_tcor_trend.html` | TCOR Trend chart + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_tcor_breakdown.html` | TCOR Breakdown waterfall + editor |
| Create | `src/policydb/web/templates/charts/manual/_tpl_freq_severity.html` | Frequency vs. Severity chart + editor |
| Create | `src/policydb/web/static/charts/manual.js` | Shared editor helpers (addRow, removeRow, save/load, export) |
| Create | `src/policydb/web/static/charts/chart.min.js` | Chart.js 4.4.1 bundled locally |
| Create | `src/policydb/web/static/charts/manual.css` | Manual chart editor styles |

---

## Task 1: Migration — Make `client_id` Nullable

**Files:**
- Create: `src/policydb/migrations/090_chart_snapshots_nullable_client.sql`
- Modify: `src/policydb/db.py` (after line ~1189, the migration 089 block)

- [ ] **Step 1: Create the migration SQL file**

Create `src/policydb/migrations/090_chart_snapshots_nullable_client.sql`:

```sql
-- Allow chart_snapshots without a client (for manual chart library)
CREATE TABLE chart_snapshots_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES clients(id),
    chart_type TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO chart_snapshots_new SELECT * FROM chart_snapshots;
DROP TABLE chart_snapshots;
ALTER TABLE chart_snapshots_new RENAME TO chart_snapshots;

CREATE INDEX IF NOT EXISTS idx_chart_snapshots_client
    ON chart_snapshots(client_id, chart_type);
CREATE INDEX IF NOT EXISTS idx_chart_snapshots_manual
    ON chart_snapshots(chart_type) WHERE client_id IS NULL;
```

- [ ] **Step 2: Wire migration into `db.py`**

Add after the migration 89 block (~line 1189) in `src/policydb/db.py`:

```python
    if 90 not in applied:
        sql = (_MIGRATIONS_DIR / "090_chart_snapshots_nullable_client.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (90, "Make chart_snapshots.client_id nullable for manual charts"),
        )
        conn.commit()
```

- [ ] **Step 3: Test migration**

Kill any existing server and start fresh to trigger migration:

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
pdb serve &
sleep 2
```

Verify: `sqlite3 ~/.policydb/policydb.sqlite "PRAGMA table_info(chart_snapshots);"` — `client_id` column should show `notnull=0`.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/migrations/090_chart_snapshots_nullable_client.sql src/policydb/db.py
git commit -m "feat: migration 090 — make chart_snapshots.client_id nullable for manual charts"
```

---

## Task 2: Chart.js Static Asset + Shared JS/CSS

**Files:**
- Create: `src/policydb/web/static/charts/chart.min.js`
- Create: `src/policydb/web/static/charts/manual.js`
- Create: `src/policydb/web/static/charts/manual.css`

- [ ] **Step 1: Download Chart.js 4.4.1 locally**

```bash
curl -sL https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js \
  -o src/policydb/web/static/charts/chart.min.js
```

Verify file exists and is ~200KB+.

- [ ] **Step 2: Create `manual.js` — shared editor helpers**

Create `src/policydb/web/static/charts/manual.js` with these functions:

- `ManualChart.formatCurrency(n)` — formats numbers as $1.2M / $500K / $1,234
- `ManualChart.formatPct(n)` — formats as +5.2% / -3.1%
- `ManualChart.formatRate(n)` — formats as 0.450
- `ManualChart.addRow(gridId, template)` — appends a new input row to a value grid, re-indexes labels
- `ManualChart.removeRow(btn)` — removes the parent `.input-group` and re-indexes
- `ManualChart.collectValues(editorId)` — reads all `input` and `select` values within `#editorId` into a flat JSON object
- `ManualChart.populateEditor(editorId, data)` — fills editor inputs from a snapshot JSON blob (by matching `name` or `id` attributes)
- `ManualChart.saveSnapshot(chartType, name, data)` — POST to `/charts/manual/snapshots/{chartType}`, returns promise with `{ok, id, name}`
- `ManualChart.loadSnapshot(chartType, snapshotId)` — GET from `/charts/manual/snapshots/{chartType}/{snapshotId}`, returns promise with snapshot data
- `ManualChart.listSnapshots(chartType)` — GET list from `/charts/manual/snapshots/{chartType}`, returns promise with array
- `ManualChart.deleteSnapshot(chartType, snapshotId)` — DELETE, returns promise
- `ManualChart.exportPng(chartInstance, filename)` — uses `chart.toBase64Image()` to create a PNG download link. For charts with custom plugins (waterfall labels, average lines), first renders to a temporary 1920×1080 canvas at 2x scale for high-quality export.

All functions namespaced under `window.ManualChart = {}`.

The Marsh brand palette should be defined as constants at the top:

```javascript
ManualChart.COLORS = {
  midnight: '#000F47',
  sky: '#CEECFF',
  blue500: '#82BAFF',
  green1000: '#2F7500', green750: '#6ABF30',
  purple1000: '#5E017F',
  gold1000: '#CB7E03', gold750: '#FFBF00',
  red: '#c8102e',
  teal: '#0e8c79',
  neutral1000: '#3D3C37', neutral750: '#7B7974',
  neutral500: '#B9B6B1', neutral250: '#F7F3EE',
  active: '#0B4BFF',
};
ManualChart.DATA_ORDER = ['midnight','green1000','purple1000','gold1000','blue500','green750','red','teal'];
```

- [ ] **Step 3: Create `manual.css` — editor panel styles**

Create `src/policydb/web/static/charts/manual.css`. This provides all styles for the manual chart editor UI. The CSS pattern mirrors the editor from the Rate & Premium standalone template (see `docs/superpowers/specs/2026-03-26-manual-chart-library-design.md` for the reference file location: `~/Downloads/premium-rate-chart-2.html`).

**CSS custom properties (root vars):**
```css
:root {
  --mc-bg: #F7F3EE;         /* neutral-250 */
  --mc-surface: #ffffff;
  --mc-midnight: #000F47;
  --mc-active: #0B4BFF;
  --mc-teal: #0e8c79;
  --mc-red: #c8102e;
  --mc-gold: #CB7E03;
  --mc-text: #3D3C37;        /* neutral-1000 */
  --mc-muted: #7B7974;       /* neutral-750 */
  --mc-border: #B9B6B1;      /* neutral-500 */
}
```

**Required class definitions:**
- `.manual-chart-page` — 960×540, white bg, centered, 16:9 aspect ratio, `box-shadow`, `border-radius: 4px`
- `.config-strip` — flex row, bg neutral-250, border, rounded, padding 1rem, gap 0.75rem, flex-wrap
- `.config-field` — flex column, gap 0.2rem; `.config-label` — 0.68rem uppercase, muted color
- `.config-field input, select` — border, rounded, padding, font DM Sans → Noto Sans, width 110px
- `.editor-grid` — CSS grid, `repeat(auto-fill, minmax(120px, 1fr))`, gap 0.6rem
- `.input-group` — flex column, gap 0.22rem; `.input-group label` — 0.69rem, muted; `.input-group.is-baseline label` — red, bold
- `.input-group input` — border, rounded, bg neutral-250, full width
- `.pill-btn` — flex row, rounded-full, border, padding, transition; `.pill-btn.on-*` — colored border/bg
- `.callout-bar` — flex row, bg sky-light, border-left 3px blue, rounded-right
- `.callout-stat` — flex column, padding, border-right; `.val.green` / `.val.red` coloring
- `.editor` — border-top, padding-top; `.editor-title` — uppercase, muted
- `.section-head` — uppercase, colored border-bottom (`.teal` / `.blue` / `.gold` variants)
- `.btn-apply`, `.btn-update` — bg midnight, white text, rounded, hover state
- `.gallery-grid` — CSS grid, `repeat(auto-fill, minmax(300px, 1fr))`, gap 1.5rem
- `.gallery-card` — white bg, border, rounded-lg, overflow hidden, hover shadow + translateY

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/static/charts/chart.min.js \
        src/policydb/web/static/charts/manual.js \
        src/policydb/web/static/charts/manual.css
git commit -m "feat: add Chart.js static asset and manual chart shared JS/CSS"
```

---

## Task 3: Routes + Navigation

**Files:**
- Modify: `src/policydb/web/routes/charts.py` (add manual routes BEFORE existing `/{client_id}` routes)
- Modify: `src/policydb/web/templates/base.html` (~line 511)

- [ ] **Step 1: Add `MANUAL_CHART_REGISTRY` and manual routes to `charts.py`**

Insert the following ABOVE the existing `@router.get("/")` route (around line 38). **Two critical route ordering rules:**
1. All `/manual/*` routes must come before any `/{client_id}/*` routes (prevents FastAPI coercing "manual" to int)
2. All `/manual/snapshots/*` routes must come before `/manual/{chart_type}` (prevents "snapshots" being captured as a chart_type)

```python
# ── Manual Chart Library ──────────────────────────────────────────────────────

MANUAL_CHART_REGISTRY = [
    {"id": "rate_premium_baseline", "title": "Rate & Premium vs. Baseline",
     "description": "Dual-axis line chart comparing rate and premium trends against a baseline year.",
     "category": "financial", "icon": "chart-line"},
    {"id": "benchmark_distribution", "title": "Benchmarking Distribution",
     "description": "Percentile bars (10th/25th/50th/75th/90th) with average line overlay.",
     "category": "benchmarking", "icon": "chart-bar"},
    {"id": "loss_history", "title": "Loss History",
     "description": "Incurred losses by year with loss ratio trend line overlay.",
     "category": "loss", "icon": "chart-bar"},
    {"id": "premium_allocation", "title": "Premium Allocation",
     "description": "Donut chart showing premium distribution by coverage line.",
     "category": "financial", "icon": "chart-pie"},
    {"id": "rate_trend_line", "title": "Rate Trend by Line",
     "description": "Multi-line chart tracking rate change % over years by coverage line.",
     "category": "rate", "icon": "chart-line"},
    {"id": "tcor_trend", "title": "Total Cost of Risk (Trend)",
     "description": "Stacked bars: retained losses + premiums with TCOR trend line.",
     "category": "tcor", "icon": "chart-bar"},
    {"id": "tcor_breakdown", "title": "TCOR Breakdown (Single Year)",
     "description": "Waterfall chart showing TCOR components for a single year.",
     "category": "tcor", "icon": "chart-bar"},
    {"id": "freq_severity", "title": "Claims Frequency vs. Severity",
     "description": "Dual-axis: claim count bars + average claim cost line.",
     "category": "loss", "icon": "chart-bar"},
]

_MANUAL_TITLE_MAP = {c["id"]: c["title"] for c in MANUAL_CHART_REGISTRY}


# ── Manual Snapshot CRUD — MUST come BEFORE /manual/{chart_type} ──────────
# Otherwise FastAPI captures "snapshots" as a chart_type parameter.

@router.get("/manual/snapshots/{chart_type}", response_class=JSONResponse)
async def manual_list_snapshots(chart_type: str, conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT id, name, updated_at FROM chart_snapshots "
        "WHERE chart_type = ? AND client_id IS NULL ORDER BY updated_at DESC",
        (f"manual_{chart_type}",),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/manual/snapshots/{chart_type}/{snapshot_id}", response_class=JSONResponse)
async def manual_load_snapshot(chart_type: str, snapshot_id: int, conn=Depends(get_db)):
    row = conn.execute(
        "SELECT id, name, data, updated_at FROM chart_snapshots "
        "WHERE id = ? AND chart_type = ? AND client_id IS NULL",
        (snapshot_id, f"manual_{chart_type}"),
    ).fetchone()
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    result = dict(row)
    result["data"] = json.loads(result["data"])
    return result


@router.post("/manual/snapshots/{chart_type}", response_class=JSONResponse)
async def manual_save_snapshot(request: Request, chart_type: str, conn=Depends(get_db)):
    body = await request.json()
    name = body.get("name", "").strip() or "Untitled"
    data = body.get("data", {})
    snapshot_id = body.get("id")

    if snapshot_id:
        conn.execute(
            "UPDATE chart_snapshots SET name = ?, data = ?, updated_at = datetime('now') "
            "WHERE id = ? AND chart_type = ? AND client_id IS NULL",
            (name, json.dumps(data), snapshot_id, f"manual_{chart_type}"),
        )
        conn.commit()
        return {"ok": True, "id": snapshot_id, "name": name}
    else:
        cur = conn.execute(
            "INSERT INTO chart_snapshots (client_id, chart_type, name, data) VALUES (NULL, ?, ?, ?)",
            (f"manual_{chart_type}", name, json.dumps(data)),
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid, "name": name}


@router.delete("/manual/snapshots/{chart_type}/{snapshot_id}", response_class=JSONResponse)
async def manual_delete_snapshot(chart_type: str, snapshot_id: int, conn=Depends(get_db)):
    conn.execute(
        "DELETE FROM chart_snapshots WHERE id = ? AND chart_type = ? AND client_id IS NULL",
        (snapshot_id, f"manual_{chart_type}"),
    )
    conn.commit()
    return {"ok": True}


# ── Manual Gallery + Editor — AFTER snapshot routes, BEFORE /{client_id} ──

@router.get("/manual", response_class=HTMLResponse)
async def manual_gallery(request: Request, conn=Depends(get_db)):
    """Manual chart library gallery."""
    snapshots = conn.execute(
        "SELECT id, chart_type, name, updated_at FROM chart_snapshots "
        "WHERE chart_type LIKE 'manual_%' AND client_id IS NULL "
        "ORDER BY updated_at DESC LIMIT 12"
    ).fetchall()
    snapshots = [dict(r) for r in snapshots]
    for s in snapshots:
        bare = s["chart_type"].replace("manual_", "", 1)
        s["title"] = _MANUAL_TITLE_MAP.get(bare, bare)

    return templates.TemplateResponse(
        "charts/manual/gallery.html",
        {"request": request, "charts": MANUAL_CHART_REGISTRY, "snapshots": snapshots},
    )


@router.get("/manual/{chart_type}", response_class=HTMLResponse)
async def manual_editor(request: Request, chart_type: str, snapshot_id: Optional[int] = None, conn=Depends(get_db)):
    """Manual chart editor page."""
    chart_info = next((c for c in MANUAL_CHART_REGISTRY if c["id"] == chart_type), None)
    if not chart_info:
        return HTMLResponse("Chart type not found", status_code=404)

    snapshot_data = None
    if snapshot_id:
        row = conn.execute(
            "SELECT data FROM chart_snapshots WHERE id = ? AND chart_type = ?",
            (snapshot_id, f"manual_{chart_type}"),
        ).fetchone()
        if row:
            snapshot_data = json.loads(row["data"])

    return templates.TemplateResponse(
        "charts/manual/editor.html",
        {
            "request": request,
            "chart_type": chart_type,
            "chart_info": chart_info,
            "snapshot_data": snapshot_data,
            "snapshot_id": snapshot_id,
        },
    )
```

- [ ] **Step 2: Add "Manual Charts" to nav in `base.html`**

In `src/policydb/web/templates/base.html`, find the line with `<a href="/charts"` (~line 511) and add the manual charts link right after it:

```html
<a href="/charts/manual" class="block px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-100 {% if active == 'manual-charts' %}font-semibold text-marsh{% endif %}">Manual Charts</a>
```

Also add `'manual-charts'` to the active check list on line 506. The current condition is:
```
{% if active in ['briefing','reconcile','templates','settings','ref-lookup','review','meetings','charts'] %}
```
Replace with:
```
{% if active in ['briefing','reconcile','templates','settings','ref-lookup','review','meetings','charts','manual-charts'] %}
```

- [ ] **Step 3: Verify route ordering**

Start the server and confirm:
- `GET /charts/manual` returns 200 (not 422)
- `GET /charts/manual/rate_premium_baseline` returns 200 (not 422)
- `GET /charts/1/deck` still works (existing route unbroken)

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
pdb serve &
sleep 2
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/charts/manual
```

Expected: `200`

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/routes/charts.py src/policydb/web/templates/base.html
git commit -m "feat: add manual chart routes and nav link"
```

---

## Task 4: Gallery + Editor Layout Templates

**Files:**
- Create: `src/policydb/web/templates/charts/manual/gallery.html`
- Create: `src/policydb/web/templates/charts/manual/editor.html`

- [ ] **Step 1: Create gallery template**

Create `src/policydb/web/templates/charts/manual/gallery.html`. Extends `base.html`, sets `{% set active = "manual-charts" %}`.

Layout:
- Page title: "Manual Chart Library"
- Subtitle: "Presentation-ready, editable chart templates"
- Grid of cards (3 columns, `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`) from `charts` context
- Each card: SVG icon placeholder (simple inline SVG silhouette per category), title, description, category badge, links to `/charts/manual/{{ c.id }}`
- Below the grid: "Recent Snapshots" section if `snapshots` is non-empty. Each snapshot card shows name, chart type title, last modified date, and links to `/charts/manual/{{ chart_type }}?snapshot_id={{ s.id }}`

Category badge colors:
- financial → `bg-blue-50 text-blue-700`
- loss → `bg-red-50 text-red-700`
- tcor → `bg-amber-50 text-amber-700`
- benchmarking → `bg-purple-50 text-purple-700`
- rate → `bg-green-50 text-green-700`

- [ ] **Step 2: Create editor layout template**

Create `src/policydb/web/templates/charts/manual/editor.html`. Extends `base.html`, sets `{% set active = "manual-charts" %}`.

Layout (top to bottom):
- **Header bar**: Chart title (`chart_info.title`), breadcrumb back to gallery, "Export PNG" button, snapshot save/load controls
- **Chart display area**: `<div class="manual-chart-page">` (960×540, white, centered), contains `{% include "charts/manual/_tpl_" ~ chart_type ~ ".html" %}`
- **Editor panel**: Also rendered by the included template partial (each `_tpl_*.html` contains both the chart canvas and its editor panel)

Loads these static assets in the `{% block content %}`:
```html
<link rel="stylesheet" href="/static/charts/manual.css">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif:wght@400;700&family=Noto+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="/static/charts/chart.min.js"></script>
<script src="/static/charts/manual.js"></script>
```

Snapshot controls (in header bar):
- Dropdown `<select>` populated on page load via `ManualChart.listSnapshots(chartType)` fetch
- "Load" button → fetches snapshot data, calls `ManualChart.populateEditor()` then the chart's refresh function
- "Save" button → prompt for name, calls `ManualChart.saveSnapshot()`
- "Delete" button → confirms, calls `ManualChart.deleteSnapshot()`

If `snapshot_data` is set (from query param), auto-populate the editor and render on page load.

- [ ] **Step 3: Verify gallery renders**

```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/charts/manual
```

Expected: `200`. The gallery should render with 8 chart cards. **Note:** Individual chart editor pages (`/charts/manual/{chart_type}`) will return Jinja2 TemplateNotFound errors until the corresponding `_tpl_*.html` files are created in Tasks 5–12. This is expected.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/charts/manual/gallery.html \
        src/policydb/web/templates/charts/manual/editor.html
git commit -m "feat: add gallery and editor layout templates for manual charts"
```

---

## Task 5: Chart Template — Rate & Premium vs. Baseline

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_rate_premium_baseline.html`

- [ ] **Step 1: Port the chart template from `~/Downloads/premium-rate-chart-2.html`**

Create `_tpl_rate_premium_baseline.html`. This is the most complex template — it sets the pattern for all others. Port the full HTML/JS from the user's existing standalone file, adapting:

- Remove `<!DOCTYPE>`, `<html>`, `<head>`, `<body>` — this is an `{% include %}` partial
- Remove inline `<style>` — those styles are now in `manual.css`
- Chart canvas goes inside the `.manual-chart-page` container from `editor.html`
- Editor panel (config strip + toggle pills + callout bar + value grids + footer) goes below the chart
- Replace color vars with Marsh brand (`--navy` → `--midnight`, etc.)
- Use `ManualChart.COLORS` for Chart.js dataset colors
- "Update Chart" button label → "Refresh Chart"
- Add `ManualChart.exportPng()` call on the export button
- Wrap the render function as `window.renderRatePremiumBaseline = function() { ... }` so the editor layout can call it
- If `snapshot_data` is available (passed from Jinja2 as `{{ snapshot_data | tojson }}`), auto-populate inputs on load

Key behavior (preserved from source):
- Config strip: Start Year, Number of Years (2–8), Baseline Year dropdown, Rate Unit Label
- "Apply" rebuilds year range and value grids, preserving existing values
- Pill toggles for Rate / Premium (prevent both-off)
- Callout bar with computed deltas
- Chart.js dual-axis line chart with baseline dashed lines

- [ ] **Step 2: Test in browser**

Navigate to `http://127.0.0.1:8000/charts/manual/rate_premium_baseline`. Verify:
- Chart renders with placeholder data
- Changing values and clicking "Refresh Chart" updates the chart
- Toggle pills show/hide series
- Config strip changes rebuild the input grids
- Export PNG downloads a valid image

- [ ] **Step 3: Test snapshot save/load**

Save a snapshot, reload the page, load it back. Verify all values and config restore correctly.

- [ ] **Step 4: Commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_rate_premium_baseline.html
git commit -m "feat: add Rate & Premium vs. Baseline manual chart template"
```

---

## Task 6: Chart Template — Benchmarking Distribution

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_benchmark_distribution.html`

- [ ] **Step 1: Create the template**

Chart.js bar chart with a custom `afterDraw` plugin that renders a horizontal dashed gold average line.

**Editor panel:**
- Config strip: Title, Value Unit Label (e.g., "$M", "per $1000")
- Value grid: Editable labels (default: 10th, 25th, 50th, 75th, 90th) + value per percentile
- Average Value input (separate, highlighted)
- Add/Remove percentile rows

**Chart behavior:**
- Bars: neutral gray (`#B9B6B1`) with data labels on top
- Average line: gold dashed (`#CB7E03`) horizontal across chart with "Avg: $32M" label
- NOT rendered as a bar — uses a Chart.js plugin `afterDraw` to draw the line

Wrap as `window.renderBenchmarkDistribution = function() { ... }`

- [ ] **Step 2: Test in browser**

Navigate to `http://127.0.0.1:8000/charts/manual/benchmark_distribution`. Verify chart renders, average line draws correctly, values are editable, export works.

- [ ] **Step 3: Commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_benchmark_distribution.html
git commit -m "feat: add Benchmarking Distribution manual chart template"
```

---

## Task 7: Chart Template — Loss History

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_loss_history.html`

- [ ] **Step 1: Create the template**

Dual-axis: Bar (incurred losses, left axis) + Line (loss ratio %, right axis).

**Editor panel:**
- Config strip: Start Year, Number of Years
- Value grid per year: Incurred Losses ($), Earned Premium ($)
- Loss Ratio auto-calculated: `(incurred / earned * 100).toFixed(1) + '%'`
- Optional: Large Loss Threshold input (draws dashed horizontal line)

**Chart behavior:**
- Bars: blue-500 (`#82BAFF`)
- Loss ratio line: red (`#c8102e`), right axis 0–100%
- Large loss threshold: dashed gray line (if set)

- [ ] **Step 2: Test in browser, commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_loss_history.html
git commit -m "feat: add Loss History manual chart template"
```

---

## Task 8: Chart Template — Premium Allocation

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_premium_allocation.html`

- [ ] **Step 1: Create the template**

Doughnut chart with center text showing total premium.

**Editor panel:**
- Dynamic rows: Label (coverage line name) + Amount ($)
- Add Row / Remove Row buttons
- Default 6 rows: Property, GL, Auto, WC, Umbrella, Other

**Chart behavior:**
- Colors: Marsh data order (midnight, green, purple, gold, blue-500, neutral-500, ...)
- Center text via `afterDraw` plugin: "Total Premium" label + formatted sum
- Legend on right side
- 60% cutout

- [ ] **Step 2: Test in browser, commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_premium_allocation.html
git commit -m "feat: add Premium Allocation manual chart template"
```

---

## Task 9: Chart Template — Rate Trend by Line

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_rate_trend_line.html`

- [ ] **Step 1: Create the template**

Multi-line chart with zero-line reference.

**Editor panel:**
- Config strip: Start Year, Number of Years
- Dynamic line rows: Line Name (e.g., "Property", "GL") + per-year rate change %
- Add Line / Remove Line buttons
- Color auto-assigned from Marsh data order

**Chart behavior:**
- Each line gets a color from `ManualChart.DATA_ORDER`
- Zero-line: dashed gray via `afterDraw` plugin
- Y-axis: `+5%` / `-3%` format
- Tension: 0.3 for smooth curves

- [ ] **Step 2: Test in browser, commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_rate_trend_line.html
git commit -m "feat: add Rate Trend by Line manual chart template"
```

---

## Task 10: Chart Template — TCOR Trend

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_tcor_trend.html`

- [ ] **Step 1: Create the template**

Stacked bar + line chart.

**Editor panel:**
- Config strip: Start Year, Number of Years
- Value grid per year: Insurance Premium ($), Retained Losses ($)
- TCOR total auto-calculated (sum of both)

**Chart behavior:**
- Stacked bars: navy (premium, bottom) + gold (retained, top)
- TCOR line: red, rides above the stacked bars
- Y-axis: currency format ($1.2M)
- Tooltip shows all three values

- [ ] **Step 2: Test in browser, commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_tcor_trend.html
git commit -m "feat: add TCOR Trend manual chart template"
```

---

## Task 11: Chart Template — TCOR Breakdown (Waterfall)

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_tcor_breakdown.html`

- [ ] **Step 1: Create the template**

Waterfall chart using floating stacked bars (not a native Chart.js type).

**Implementation pattern:**
Two stacked datasets:
1. Invisible spacer (transparent) — height = cumulative sum of prior categories
2. Visible segment — height = current category value
3. "TOTAL" bar: spacer=0, segment=grand total

**Editor panel:**
- Config strip: Year Label (e.g., "2024")
- Dynamic rows: Category Label + Amount ($)
- Default 8 rows: Retained Losses, Property, GL, Auto, WC, Umbrella, Broker Fees, Risk Mgmt
- Add Row / Remove Row buttons
- TOTAL auto-calculated

**Chart behavior:**
- Colors: Gold for retained losses (first row), blue-500 for premium lines, neutral-500 for admin/fees, midnight for TOTAL
- Value labels above each bar via `afterDraw` plugin
- Tooltip only on visible dataset (filter out spacer)

- [ ] **Step 2: Test in browser, commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_tcor_breakdown.html
git commit -m "feat: add TCOR Breakdown waterfall manual chart template"
```

---

## Task 12: Chart Template — Claims Frequency vs. Severity

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_freq_severity.html`

- [ ] **Step 1: Create the template**

Dual-axis: bars (claim count) + line (avg claim cost).

**Editor panel:**
- Config strip: Start Year, Number of Years
- Value grid per year: Claim Count, Total Incurred ($)
- Avg Claim Cost auto-calculated: `(total_incurred / claim_count).toFixed(0)`

**Chart behavior:**
- Bars: midnight (`#000F47`), left axis (claim count, integer)
- Line: gold (`#CB7E03`), right axis (avg cost, currency format)
- Tooltip shows count, total incurred, and calculated avg

- [ ] **Step 2: Test in browser, commit**

```bash
git add src/policydb/web/templates/charts/manual/_tpl_freq_severity.html
git commit -m "feat: add Frequency vs. Severity manual chart template"
```

---

## Task 13: QA — Full Visual Testing

- [ ] **Step 1: Test gallery page**

Navigate to `http://127.0.0.1:8000/charts/manual`. Screenshot. Verify:
- All 8 chart cards render with title, description, category badge
- Cards are clickable and link to correct editor URLs
- Nav shows "Manual Charts" highlighted under Tools dropdown

- [ ] **Step 2: Test each chart editor**

For each of the 8 chart types:
1. Navigate to `/charts/manual/{chart_type}`
2. Verify chart renders with placeholder data
3. Edit values, click "Refresh Chart" — chart updates
4. Test add/remove rows (where applicable)
5. Test config strip changes (year range, labels)
6. Test "Export PNG" — downloads a valid image file
7. Test "Save Snapshot" — saves, appears in dropdown
8. Test "Load Snapshot" — restores all values and re-renders chart
9. Screenshot for visual verification

- [ ] **Step 3: Test existing chart deck still works**

Navigate to `/charts` → select a client → configure deck → view charts. Verify no regressions.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: QA fixes for manual chart library"
```

---

## Task 14: Final Commit + PR

- [ ] **Step 1: Verify all changes**

```bash
git log --oneline -10
git diff main --stat
```

- [ ] **Step 2: Push and create PR**

```bash
git push -u origin claude/awesome-sutherland
gh pr create --title "feat: manual chart library — 8 editable presentation templates" --body "$(cat <<'EOF'
## Summary
- New `/charts/manual` section with 8 presentation-ready, manually-editable chart templates
- Rate & Premium vs. Baseline, Benchmarking Distribution, Loss History, Premium Allocation, Rate Trend by Line, TCOR Trend, TCOR Breakdown (waterfall), Claims Frequency vs. Severity
- All charts use Chart.js + Marsh brand styling (Noto fonts, official color palette)
- Every element editable: titles, values, colors, series count, axis labels
- Snapshot save/load via existing chart_snapshots table (migration 090 makes client_id nullable)
- PNG export via Chart.js native toBase64Image()
- Gallery page with chart type cards + recent snapshots

## Test plan
- [ ] Gallery page renders all 8 chart types
- [ ] Each chart editor renders and updates correctly
- [ ] Snapshot save/load round-trips all values
- [ ] PNG export produces valid images
- [ ] Existing data-driven chart deck unaffected
- [ ] Migration 090 runs cleanly on existing databases

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
