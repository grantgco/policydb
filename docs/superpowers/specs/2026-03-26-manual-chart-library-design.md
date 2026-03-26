# Manual Chart Library — Design Spec

**Date:** 2026-03-26
**Status:** Approved
**Scope:** New "Manual Charts" section within the existing `/charts` module — a library of presentation-ready, editable chart templates that are independent of PolicyDB data.

---

## Problem

The existing Chart Deck Builder generates charts from PolicyDB data — great for renewal recaps but useless for standalone presentations where you need to manually enter benchmarking data, TCOR figures, or custom rate comparisons. Today these get built in Excel, which produces inconsistent formatting, breaks the Marsh brand guidelines, and can't be easily reused or shared.

## Solution

A library of 8 self-contained, manually-editable chart templates rendered with Chart.js and styled to Marsh brand standards. Each template has a full editor panel where every element (titles, values, colors, series count, axis labels) is configurable. Charts export as PNG for PowerPoint insertion and can be saved/loaded as snapshots for reuse.

---

## Architecture

### Relationship to Existing Charts Module

The manual chart library lives alongside the data-driven deck builder under the same `/charts` route prefix:

| Aspect | Data-Driven (existing) | Manual Library (new) |
|--------|----------------------|---------------------|
| Route prefix | `/charts/{client_id}/deck` | `/charts/manual` |
| Data source | PolicyDB SQL queries | User-entered values |
| Templates | `charts/_chart_*.html` | `charts/manual/_tpl_*.html` |
| Rendering | D3.js (SVG) | Chart.js (Canvas) |
| Export | html2canvas → PNG/ZIP | Chart.js native `toBase64Image()` → PNG |
| Persistence | `chart_snapshots` table | Same table, `manual_` prefix on `chart_type` |
| Client required | Yes | No (optional tag) |

### Why Chart.js (not D3)

- The user's existing Rate & Premium HTML template already uses Chart.js
- Chart.js is simpler for the destroy/recreate pattern needed by the editor
- Canvas-based export via native `chart.toBase64Image()` is more reliable than the html2canvas clone pipeline (which produces blank canvases for Chart.js elements)
- D3 remains the right choice for the data-driven charts (tower diagrams, complex SVG)

### Chart.js Loading

Chart.js is NOT currently loaded in the codebase (existing charts use D3). Add `chart.min.js` as a local static file at `static/charts/chart.min.js` (consistent with the existing pattern of bundling `d3.v7.min.js` locally). Load it only in the manual chart templates — not globally in `base.html`.

### Route Ordering (Critical)

**All `/charts/manual/*` routes MUST be registered BEFORE any `/{client_id}/*` routes in `charts.py`.** FastAPI resolves routes in registration order — if `/{client_id}/deck` comes first, a request to `/charts/manual/...` will try to parse `"manual"` as an `int` client_id and return a 422 error. This is a known pattern (CLAUDE.md lesson #11: "FastAPI literal routes before parameterized routes to avoid capture conflicts").

### Google Fonts

Load Noto Serif and Noto Sans only in `editor.html` and `gallery.html` — not globally. This keeps page load unaffected for the rest of the app.

---

## Chart Type Registry

```python
MANUAL_CHART_REGISTRY = [
    {
        "id": "rate_premium_baseline",
        "title": "Rate & Premium vs. Baseline",
        "description": "Dual-axis line chart comparing rate and premium trends against a baseline year.",
        "category": "financial",
        "icon": "chart-line",
    },
    {
        "id": "benchmark_distribution",
        "title": "Benchmarking Distribution",
        "description": "Percentile bars (10th/25th/50th/75th/90th) with average line overlay.",
        "category": "benchmarking",
        "icon": "chart-bar",
    },
    {
        "id": "loss_history",
        "title": "Loss History",
        "description": "Incurred losses by year with loss ratio trend line overlay.",
        "category": "loss",
        "icon": "chart-bar",
    },
    {
        "id": "premium_allocation",
        "title": "Premium Allocation",
        "description": "Donut chart showing premium distribution by coverage line.",
        "category": "financial",
        "icon": "chart-pie",
    },
    {
        "id": "rate_trend_line",
        "title": "Rate Trend by Line",
        "description": "Multi-line chart tracking rate change % over years by coverage line.",
        "category": "rate",
        "icon": "chart-line",
    },
    {
        "id": "tcor_trend",
        "title": "Total Cost of Risk (Trend)",
        "description": "Stacked bars: retained losses + premiums with TCOR trend line.",
        "category": "tcor",
        "icon": "chart-bar",
    },
    {
        "id": "tcor_breakdown",
        "title": "TCOR Breakdown (Single Year)",
        "description": "Waterfall chart showing TCOR components for a single year.",
        "category": "tcor",
        "icon": "chart-bar",
    },
    {
        "id": "freq_severity",
        "title": "Claims Frequency vs. Severity",
        "description": "Dual-axis: claim count bars + average claim cost line.",
        "category": "loss",
        "icon": "chart-bar",
    },
]
```

Adding a new chart type requires only:
1. A new entry in `MANUAL_CHART_REGISTRY`
2. A new `_tpl_*.html` template file

No Python data layer, no migrations, no route changes.

---

## User Flow

### Step 1: Gallery (`GET /charts/manual`)

Grid of chart type cards showing:
- Static inline SVG icon per chart type (simple line/bar/donut silhouette — NOT live Chart.js renders, which would be heavy)
- Title and one-line description
- Category badge (Financial, Loss, TCOR, Benchmarking, Rate)

Below the grid: **Recent Snapshots** section showing saved charts with name, chart type, and last-modified date. Click to resume editing.

### Step 2: Editor (`GET /charts/manual/{chart_type}`)

Full-page layout with two zones:

**Top: Chart Display**
- 16:9 container (960×540) using existing `.chart-page` CSS
- Chart rendered via Chart.js
- "Export PNG" button (reuses `exportChartToPng()` from `export.js`)

**Bottom: Editor Panel**
- Follows the exact pattern from the Rate & Premium HTML template
- Structured as sections per the Editor Pattern below

### Step 3: Save / Load / Export

- **Save**: POST snapshot to `/charts/manual/snapshots/{chart_type}` — stores all editor state as JSON
- **Load**: Dropdown of saved snapshots, populate editor fields, re-render chart
- **Export PNG**: Canvas → PNG download via existing export pipeline
- **Optional client tag**: Associate snapshot with a client for organization (not required)

---

## Editor Pattern (Universal)

Every chart template implements the same editor structure. The specific fields vary per chart type, but the layout and behavior are identical.

### Config Strip
Top bar with chart-wide settings:
- **Title** (text input) — chart heading
- **Subtitle** (text input) — secondary label
- **Year range** or **category count** — controls how many data points
- **Unit labels** — axis annotations (e.g., "per $100 TIV", "$M", "%")
- **Apply** button — rebuilds the input grids when config changes

### Series Toggles
Pill buttons to show/hide each data series (e.g., Rate / Premium toggle from the Rate & Premium template). Only shown for multi-series charts.

### Value Grids
Per-series input sections with:
- Section header (colored border, matching series color)
- Grid of labeled inputs — one per data point
- Baseline indicator (red label) where applicable
- Dynamic: number of inputs matches config strip settings
- **Add Row / Remove Row** for variable-category charts (TCOR Breakdown, Premium Allocation)

### Color Overrides (Optional)
Per-series color picker — defaults to Marsh palette, user can override.

### Footer
- Subtitle/description text input
- **Refresh Chart** button — destroys and recreates Chart.js instance with current editor values (visual preview only, not persisted)
- **Save Snapshot** / **Load Snapshot** dropdown + buttons (persists to database)

### Behavior
- "Update Chart" reads all editor field values, assembles a config object, and calls the chart-specific render function
- Chart.js instance is destroyed and recreated on each update (not animated/patched)
- All inputs use standard `<input>` elements (not contenteditable — these are tools, not data records)
- No auto-save on blur — explicit "Update Chart" button (matches existing template pattern)

---

## Per-Chart Template Specifications

### 1. Rate & Premium vs. Baseline (`_tpl_rate_premium_baseline.html`)
- **Chart type**: Line (dual Y-axis)
- **Config**: Start year, number of years (2–8), baseline year, rate unit label
- **Series**: Rate (left axis, teal), Premium (right axis, navy)
- **Reference**: Baseline dashed lines (red) for both series
- **Toggles**: Rate on/off, Premium on/off
- **Callout bar**: Computed deltas vs. baseline (green/red)
- **Based on**: User's existing HTML template — port directly

### 2. Benchmarking Distribution (`_tpl_benchmark_distribution.html`)
- **Chart type**: Bar + horizontal line annotation
- **Config**: Title, value unit label (e.g., "$M", "per $1000"), number of percentiles
- **Default categories**: 10th, 25th, 50th, 75th, 90th (editable labels)
- **Series**: Percentile bars (neutral gray), Average line (gold dashed, horizontal)
- **Data inputs**: One value per percentile + average value
- **Average rendered as**: Dashed horizontal line with label — NOT a bar

### 3. Loss History (`_tpl_loss_history.html`)
- **Chart type**: Bar + Line (dual Y-axis)
- **Config**: Start year, number of years, loss ratio axis max
- **Series**: Incurred Losses (bars, blue), Loss Ratio % (line, red, right axis)
- **Data inputs**: Per-year: incurred losses ($) + earned premium ($) → loss ratio auto-calculated
- **Optional**: Large loss threshold line (dashed)

### 4. Premium Allocation (`_tpl_premium_allocation.html`)
- **Chart type**: Doughnut
- **Config**: Title, number of categories (add/remove rows)
- **Series**: One ring — segments by coverage line
- **Data inputs**: Per-category: label + amount ($)
- **Center text**: Auto-calculated total premium
- **Colors**: Marsh data color order (Blue, Green, Purple, Gold) with tint stacks

### 5. Rate Trend by Line (`_tpl_rate_trend_line.html`)
- **Chart type**: Multi-line
- **Config**: Start year, number of years, number of lines (add/remove)
- **Series**: One line per coverage type
- **Data inputs**: Per-line per-year: rate change %
- **Reference**: Zero-line (dashed gray)
- **Colors**: Marsh data color order, auto-assigned

### 6. TCOR Trend (`_tpl_tcor_trend.html`)
- **Chart type**: Stacked Bar + Line
- **Config**: Start year, number of years
- **Series**: Insurance Premium (bars, navy), Retained Losses (bars, gold), TCOR total (line, red)
- **Data inputs**: Per-year: premium ($) + retained losses ($) → TCOR auto-summed
- **Stack**: Premium on bottom, retained on top
- **TCOR line**: Rides above the stacked bars

### 7. TCOR Breakdown — Single Year (`_tpl_tcor_breakdown.html`)
- **Chart type**: Waterfall (implemented as floating stacked bars — not a native Chart.js type)
- **Implementation**: Two stacked datasets: invisible base spacer + visible segment. Each bar starts where the previous ended. The "TOTAL" bar spans from zero to sum. This is the standard Chart.js floating bar pattern.
- **Config**: Year label, number of categories (add/remove rows)
- **Default categories**: Retained Losses, Property, GL, Auto, WC, Umbrella, Broker Fees, Risk Mgmt
- **Data inputs**: Per-category: label + amount ($)
- **Final bar**: "TOTAL" — auto-summed, full-height from zero
- **Colors**: Gold for retained, blue for premiums, gray for admin/fees, navy for total
- **Value labels**: Above each bar segment (via custom Chart.js plugin `afterDraw`)

### 8. Claims Frequency vs. Severity (`_tpl_freq_severity.html`)
- **Chart type**: Bar + Line (dual Y-axis)
- **Config**: Start year, number of years
- **Series**: Claim Count (bars, navy, left axis), Avg Claim Cost (line, gold, right axis)
- **Data inputs**: Per-year: claim count + total incurred ($) → avg cost auto-calculated
- **Axis labels**: "Claim Count" (left), "Avg Claim Cost" (right)

---

## Snapshot Persistence

Reuses the existing `chart_snapshots` table:

| Column | Value for manual charts |
|--------|------------------------|
| `client_id` | NULL (or optional client ID if tagged) |
| `chart_type` | `"manual_{chart_id}"` (e.g., `"manual_tcor_trend"`) |
| `name` | User-provided (e.g., "Acme Corp 2025 TCOR") |
| `data` | JSON blob: `{"config": {...}, "series": {...}, "values": {...}}` |

The JSON blob stores the complete editor state — all config strip values, all series data, all value inputs, color overrides, title, subtitle. Loading a snapshot populates every editor field and re-renders the chart.

### Migration Required

The existing `chart_snapshots` table has `client_id INTEGER NOT NULL` — manual charts with no client association will fail. **Migration 090** recreates the table with `client_id` nullable:

```sql
-- 090_chart_snapshots_nullable_client.sql
-- Allow chart_snapshots without a client (for manual chart library)
CREATE TABLE chart_snapshots_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES clients(id),  -- NOW NULLABLE
    chart_type TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO chart_snapshots_new SELECT * FROM chart_snapshots;
DROP TABLE chart_snapshots;
ALTER TABLE chart_snapshots_new RENAME TO chart_snapshots;

CREATE INDEX idx_chart_snapshots_client ON chart_snapshots(client_id, chart_type);
CREATE INDEX idx_chart_snapshots_manual ON chart_snapshots(chart_type) WHERE client_id IS NULL;
```

### Snapshot Routes (added to `routes/charts.py`)

```
GET  /charts/manual/snapshots/{chart_type}              → list snapshots
GET  /charts/manual/snapshots/{chart_type}/{snapshot_id} → load snapshot
POST /charts/manual/snapshots/{chart_type}               → save/update snapshot
DELETE /charts/manual/snapshots/{chart_type}/{snapshot_id} → delete snapshot
```

These mirror the existing client-scoped snapshot routes but without `client_id` in the path.

**SQL logic:** List queries use `WHERE chart_type = ? AND client_id IS NULL` for untagged manual charts. If a `?client_id=` query parameter is provided, filter by that instead. The `chart_type` value is always prefixed with `manual_` (e.g., `manual_tcor_trend`) to prevent collisions with data-driven snapshot types.

---

## File Structure

```
src/policydb/web/
├── routes/charts.py                          (extend: +~80 lines for manual routes)
├── templates/charts/
│   ├── manual/
│   │   ├── gallery.html                      (chart type picker + recent snapshots)
│   │   ├── editor.html                       (shared editor layout: chart + panel)
│   │   ├── _tpl_rate_premium_baseline.html
│   │   ├── _tpl_benchmark_distribution.html
│   │   ├── _tpl_loss_history.html
│   │   ├── _tpl_premium_allocation.html
│   │   ├── _tpl_rate_trend_line.html
│   │   ├── _tpl_tcor_trend.html
│   │   ├── _tpl_tcor_breakdown.html
│   │   └── _tpl_freq_severity.html
│   └── (existing templates untouched)
└── static/charts/
    ├── manual.js                             (shared editor helpers)
    └── (existing JS untouched)
```

### `manual.js` — Shared Editor Helpers

Common functions used across all template editors:

- `addRow(gridId, template)` — append a new input row to a value grid
- `removeRow(btn)` — remove a row and re-index
- `collectValues(formId)` — read all editor inputs into a JSON object
- `populateEditor(data)` — fill editor inputs from a snapshot JSON blob
- `saveSnapshot(chartType, name)` — POST to snapshot API
- `loadSnapshot(chartType, snapshotId)` — GET from snapshot API, populate editor
- `renderChart(chartType, config)` — dispatch to chart-specific render function
- `exportManualChart(chartInstance, filename)` — uses Chart.js native `chart.toBase64Image()` → create download link. Does NOT use the html2canvas clone pipeline (which fails for canvas elements)

---

## Navigation

The existing Tools dropdown in `base.html` gains a second link:

```
Tools ▾
  ├── Chart Deck         (existing: /charts)
  └── Manual Charts      (new: /charts/manual)
```

---

## Styling

All manual charts follow the Marsh Brand Guide from CLAUDE.md:

- **Typography**: Noto Serif for titles, Noto Sans for body/labels
- **Core colors**: Midnight Blue `#000F47`, Sky Blue `#CEECFF`
- **Data color order**: Blue → Green → Purple → Gold (with 1000/750/500/250 tint stacks)
- **Warm neutrals**: `#3D3C37` (text), `#7B7974` (secondary), `#B9B6B1` (borders), `#F7F3EE` (backgrounds)
- **Active accent**: `#0B4BFF` for interactive highlights
- **Chart containers**: White background, 16:9 aspect ratio (960×540), optimized for PowerPoint

Google Fonts loaded: `Noto Serif:wght@400;700` and `Noto Sans:wght@300;400;500;600;700`.

---

## Extensibility

Adding a new chart type to the library:

1. Add entry to `MANUAL_CHART_REGISTRY` in `routes/charts.py`
2. Create `_tpl_{chart_id}.html` in `templates/charts/manual/`
3. Done — no routes, no migrations, no Python data functions

The template file contains both the Chart.js render function and the editor panel HTML. The shared `editor.html` layout provides the chrome (header, save/load bar, export button).

---

## Reference: Source Template

The Rate & Premium vs. Baseline chart template that inspired this library is located at `~/Downloads/premium-rate-chart-2.html`. This standalone HTML file demonstrates the target editor pattern: config strip + value grids + pill toggles + Chart.js rendering. The `_tpl_rate_premium_baseline.html` template should be ported from this file, adapting it to the shared editor layout and Marsh brand colors.

---

## What This Does NOT Include

- No auto-population from PolicyDB data (that's the existing deck builder's job)
- No multi-chart deck assembly (each chart is standalone — compose in PowerPoint)
- No PDF export (PNG insertion into decks is the workflow)
- No real-time collaboration or sharing
