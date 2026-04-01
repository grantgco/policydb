---
name: policydb-charts
description: >
  Manual Chart Library & Visual Builders reference for PolicyDB. Use when working on chart
  templates, adding new chart types, fixing export/snapshot issues, or modifying the chart
  gallery. Covers template structure, export-safe styling rules (html2canvas gotchas),
  snapshot system, display toggles, and key files. Also trigger when user mentions
  html2canvas, chart export, PNG export, or ManualChart namespace.
---

# Manual Chart Library & Visual Builders

## Overview
`/charts/manual/` — editable Chart.js + HTML chart templates for client deliverables. Gallery at `/charts/manual`, editor at `/charts/manual/{chart_type}`. Templates live in `src/policydb/web/templates/charts/manual/_tpl_{id}.html`. Registry in `MANUAL_CHART_REGISTRY` in `charts.py`.

**Current types:** 9 charts (rate/premium, loss triangle, etc.), 1 quote comparison, 5 callout cards (stat, coverage, carrier, milestone, narrative), 1 timeline builder.

## Adding a New Chart/Visual (2 files only)
1. Add registry entry to `MANUAL_CHART_REGISTRY` in `charts.py` (id, title, description, category, icon)
2. Create template `_tpl_{id}.html` in `src/policydb/web/templates/charts/manual/`
3. No routes, migrations, or JS library changes needed — the editor wrapper auto-includes

## Template Structure Pattern
Every template follows this structure:
- **Display area** (top): `.manual-chart-page` (fixed 960×540) or `.manual-chart-page-auto` (variable height)
- **Editor panel** (bottom): `#editor-panel` with `.mc-editor` class — display toggles, content inputs, style options
- **Script** (IIFE): `window.refreshCurrentChart()` function for snapshot load, uses `ManualChart.COLORS` and `ManualChart.DATA_COLORS`

## Export-Safe Styling Rules (CRITICAL)
All elements inside the display area (`.manual-chart-page` / `.manual-chart-page-auto`) MUST use export-safe CSS because html2canvas renders them to PNG:

| Rule | Why |
|------|-----|
| **Use px units, not rem** | rem is relative to root font-size which doesn't exist in the offscreen clone |
| **Use solid hex colors, not rgba()** | html2canvas renders rgba() backgrounds incorrectly or transparent |
| **Use direct hex values, not CSS variables** | `var(--mc-text)` won't resolve in the offscreen container |
| **Use inline styles on JS-generated elements** | CSS class styles may not apply in the clone |
| **Use explicit `font-family` on generated elements** | Font inheritance breaks in offscreen clones |
| **No `font-variant-numeric`** | html2canvas ignores it |
| **`<canvas>` → `<img>` conversion** | html2canvas cannot render `<canvas>` drawings — `prepareClone()` handles this automatically |

**Editor panel** (`.mc-editor`) can use rem, CSS variables, and Tailwind classes freely — it's never exported.

## Snapshot System
- Save/Load via `ManualChart.collectAll()` / `populateAll()` — collects all `<input>`, `<select>`, `<textarea>` with `name` attributes inside the editor panel
- Complex state (dynamic rows, steps) serialized to a hidden `<textarea>` with a `name` attribute
- `window.refreshCurrentChart()` must handle two-phase restore: populateAll sets textarea value → refreshCurrentChart parses it and rebuilds UI
- Client tagging optional via snapshot bar combobox

## Display Toggle Pattern
- Toggle checkboxes in editor call `applyDisplayOptions()`
- `ManualChart.applyToggle(toggleId, targetId)` sets `display: ''` or `'none'`
- **Gotcha:** `applyToggle` sets `display: ''` which resets to the element's default (usually `block`). If the target needs `flex` or `grid`, handle it manually instead of using `applyToggle`

## Key Files
| File | Purpose |
|------|---------|
| `charts.py` | Registry, routes, snapshot CRUD, gallery |
| `editor.html` | Shared wrapper: snapshot bar, client combobox, export/copy buttons |
| `gallery.html` | Chart library gallery (Charts + Visual Builders sections) |
| `manual.js` | `ManualChart` namespace: COLORS, DATA_COLORS, fmtCurrency, collectAll/populateAll, applyToggle |
| `manual.css` | `.manual-chart-page`, `.manual-chart-page-auto`, `.mc-editor`, `.mc-legend` classes |
| `_tpl_*.html` | Individual chart/builder templates |

## Export Infrastructure
- `prepareClone()` in `editor.html` — strips `.no-print`, border/shadow/radius, converts `<canvas>` to `<img>`
- `exportChartAtSize()` — fixed 960×540 charts with size picker (Small/Medium/Large)
- `exportAutoHeight()` — variable-height cards at natural dimensions, 2x scale, single "Export PNG" button
- `copyToClipboard()` — same as export but writes to clipboard via `ClipboardItem`
- All three paths use `prepareClone()` — fixes apply globally

## Chart-Specific Bug Patterns

**html2canvas cannot render `<canvas>` elements:** Chart.js draws on `<canvas>` which html2canvas skips. Must convert to `<img>` via `toDataURL()` before export. The shared `prepareClone()` in `editor.html` handles this automatically.

**html2canvas export styling:** Never use `rgba()`, CSS variables (`var(--x)`), `rem` units, or `font-variant-numeric` in elements that will be exported to PNG. Use solid hex colors, `px` units, and explicit `font-family`. See Export-Safe Styling Rules above.

**`ManualChart.applyToggle` resets display to `''` (block):** If the toggled element needs `display:flex` or `display:grid`, handle the toggle manually instead of using `applyToggle`.

**`fmtCurrency` regex must not eat significant zeros:** The regex `/\.?0+$/` strips trailing zeros from integers too (`"40"` → `"4"`). For the K path, use `Math.round()` with no regex. For the M path, use a decimal-anchored regex: `.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')`.

**PNG export must strip decorative CSS:** `prepareClone()` removes `border`, `box-shadow`, and `border-radius` from the clone so exports are clean white with no gray edges.
