# Visual Builders Integration — Design Spec

**Date:** 2026-03-27
**Status:** Approved
**Scope:** Add Timeline Builder + 5 Callout Card types to the Manual Chart Library

## Overview

Integrate two standalone HTML tools — a **Timeline Builder** (multi-step process timelines) and a **Callout Builder** (5 insurance card types) — into PolicyDB's existing Manual Chart Library. All new types share the same route, snapshot, and export infrastructure as existing charts.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Gallery organization | Separate "Visual Builders" section below charts | Clear separation; no route changes needed |
| Editor layout | Stacked (preview top, editor below) | Consistent with existing charts; reuses editor.html wrapper |
| Callout types | 6 separate gallery cards (1 timeline + 5 callout types) | Each gets a focused editor; simpler templates |
| Export container | Auto-height with width selector | Cards/timelines have variable height unlike fixed 960×540 charts |
| Client tagging | Combobox on snapshot save bar | Applies to ALL manual chart types; uses existing `client_id` column |

## Gallery Structure

The gallery page (`gallery.html`) gets a section divider. Existing charts appear under **"Charts"**, new items under **"Visual Builders"**.

### New Registry Entries

Add to `MANUAL_CHART_REGISTRY` in `charts.py`:

```python
# ── Visual Builders ──
{"id": "timeline_builder", "title": "Timeline Builder",
 "description": "Multi-step process timeline with horizontal, vertical, or phase layouts.",
 "category": "builder", "icon": "timeline"},
{"id": "callout_stat", "title": "Big Stat / KPI",
 "description": "Large metric callout with direction arrow, label, and context.",
 "category": "card", "icon": "stat"},
{"id": "callout_coverage", "title": "Coverage Card",
 "description": "Coverage line summary with limit, deductible, premium, and key terms.",
 "category": "card", "icon": "card"},
{"id": "callout_carrier", "title": "Carrier Tile",
 "description": "Carrier summary with rating, participation, premium, and notes.",
 "category": "card", "icon": "card"},
{"id": "callout_milestone", "title": "Milestone Card",
 "description": "Single milestone with date, status badge, description, and progress bar.",
 "category": "card", "icon": "timeline"},
{"id": "callout_narrative", "title": "Narrative Card",
 "description": "Market update or analysis narrative with callout quote block.",
 "category": "card", "icon": "card"},
```

### Gallery Template Changes

- Split the `{% for c in charts %}` loop into two grids using `charts` and `builders` context lists (filtered by category in the route)
- Add section header: `<h2>Visual Builders</h2>` with subtitle between the two grids
- Add category badge colors:
  ```
  {% elif c.category == 'builder' %}bg-teal-50 text-teal-700
  {% elif c.category == 'card' %}bg-purple-50 text-purple-700
  ```

### Gallery Icon SVGs

New icon types map to inline SVGs in `gallery.html`:

- `timeline` — horizontal dots connected by a line (used for Timeline Builder and Milestone Card)
- `stat` — large number/arrow icon (used for Big Stat / KPI)
- `card` — rectangle with header bar (used for Coverage, Carrier, Narrative cards)

## Brand Conversion

All templates convert from DM Serif Display / DM Sans to Marsh brand:

| Element | Source Font | Target Font |
|---------|-----------|-------------|
| Headings, titles, large stats | DM Serif Display | Noto Serif |
| Body text, labels, inputs | DM Sans | Noto Sans |

### Color Palette Conversion

Source colors map to `ManualChart.COLORS`:

| Source | Target | Marsh Token |
|--------|--------|-------------|
| `#0d2d4f` (navy) | `#000F47` | `MC.midnight` |
| `#c8102e` (red) | `#c8102e` | `MC.red` (same) |
| `#1a5fa8` (blue) | `#0B4BFF` | `MC.active` |
| `#0e8c79` (teal) | `#0e8c79` | `MC.teal` (same) |
| `#b45309` (amber) | `#CB7E03` | `MC.gold1000` |
| `#4a5568` (slate) | `#3D3C37` | `MC.neutral1000` |

Accent color swatches in editors use `ManualChart.DATA_COLORS` order.

## Export & Container

### Auto-Height Container

Add `.manual-chart-page-auto` CSS class in `manual.css`:

```css
.manual-chart-page-auto {
  max-width: 960px;
  background: var(--mc-surface);
  margin: 0 auto;
  border: 1px solid var(--mc-border);
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  overflow: hidden;
  position: relative;
}
```

Same as `.manual-chart-page` but without `height: 540px` and `display: flex`.

### Width Selectors

- **Timeline Builder:** 700 / 900 / 1100 / 1300px (matches original). Input: `<select name="cfg-canvas-width">`
- **Callout Cards:** 400 / 520 / 680px (matches original). Input: `<select name="cfg-card-width">`

Width stored as a named input for snapshot persistence via `collectAll`/`populateAll`.

### PNG Export for Auto-Height Containers

The existing `exportChart()` in `editor.html` hardcodes 960×540 native dimensions. For auto-height containers, a separate export path is needed:

1. **Target selection:** `exportChart()` queries `.manual-chart-page` first; if not found, falls back to `.manual-chart-page-auto`
2. **Dynamic dimensions:** For `.manual-chart-page-auto`, read `offsetWidth` and `offsetHeight` from the live element instead of using fixed 960×540
3. **Offscreen container:** Set to the element's actual width/height (not fixed 960×540)
4. **Scale calculation:** `scale = 2` (always 2× retina) — no preset-based width ratio needed
5. **Export size picker:** Skip the Small/Medium/Large presets for auto-height containers; export at native size × 2 only (single "Export PNG" button, no dropdown)
6. **html2canvas call:** Pass `{ width: el.offsetWidth, height: el.offsetHeight, scale: 2 }`

## Client Tagging on Snapshots

### Snapshot Save Bar Changes (`editor.html`)

Add a client combobox between the snapshot name input and Save button:

```
[Snapshot name input] [Client: ___________▾] [Save] [Delete]
```

- Combobox searches `/api/clients/search?q=...` (already exists)
- Stores selected `client_id` in a hidden input
- Passes `client_id` in the save POST payload
- Shows client name in the dropdown when loading a snapshot

### Gallery Changes

- "Recent Snapshots" section shows client name badge next to snapshot name when `client_id` is set
- No client filtering in this phase (future enhancement)

### Route Changes (`charts.py`)

- `manual_save_snapshot` POST handler: accept optional `client_id` in JSON payload, store in DB
- `manual_list_snapshots` GET: **remove** `client_id IS NULL` filter; join on `clients` table to return `client_name`. Tagged snapshots appear alongside untagged ones in the dropdown.
- `manual_get_snapshot` GET: return `client_id` and `client_name`
- Gallery route: join on `clients` to return `client_name` for recent snapshots display

## Template Specifications

### 1. Timeline Builder (`_tpl_timeline_builder.html`)

**Display area:** Auto-height container with configurable width. Contains:
- Header: eyebrow, title (Noto Serif), subtitle, progress bar
- Body: renders in one of 3 layouts (horizontal/vertical/phases)
- Footer: note text + status legend (Complete/Active/Upcoming/Overdue)

**Editor panel:**
- Display toggles: Title, Subtitle, Progress, Footer, Legend
- Settings: eyebrow, title, subtitle, footer note, accent color swatches, canvas width, layout mode (segmented control: Horizontal | Vertical | Phases)
- Steps section: collapsible step cards, each with title, date, description, owner, status pills, phase (shown when layout=phases)
- Add Step button, Refresh Chart button

**State management:** Steps array serialized to hidden `<textarea name="tl-state">` for snapshot compatibility. On snapshot load, `populateAll()` restores the raw JSON string to the textarea. `refreshCurrentChart()` must then: (1) parse the textarea JSON, (2) rebuild the step editor cards from the parsed data, (3) render the preview. This two-phase restore (populate textarea → rebuild UI from it) follows the same pattern used by `_tpl_quote_comparison.html`.

**Layout modes:**
- **Horizontal:** Steps in a row with connector lines, numbered dots, labels below
- **Vertical:** Steps stacked with vertical connector line, status badges inline
- **Phases:** Steps grouped by `phase` field, each group gets a label badge, steps within group are horizontal

### 2. Big Stat / KPI (`_tpl_callout_stat.html`)

**Display area:** Card with left accent bar (6px), eyebrow text, large stat value with direction arrow (↓/↑/—), unit suffix, label, sublabel, footer.

**Editor panel:** Eyebrow, direction (segmented: ↓ ↑ —), value, unit, label, sublabel, footer, accent color, card width.

### 3. Coverage Card (`_tpl_callout_coverage.html`)

**Display area:** Colored header with coverage line name + carrier badge. Body with 2×2 grid (Limit, Deductible, Premium, Effective Date). Key terms as pill tags below.

**Editor panel:** Line of coverage, carrier, limit, deductible, premium, effective date, key terms (tag input with add/remove), header color, card width.

### 4. Carrier Tile (`_tpl_callout_carrier.html`)

**Display area:** Colored header with carrier name, AM Best rating badge, lines written. Body with 2×2 stats grid (Premium, Participation, Retention). Notes section below divider.

**Editor panel:** Carrier name, rating, lines, premium, participation, retention, notes textarea, header color, card width.

### 5. Milestone Card (`_tpl_callout_milestone.html`)

**Display area:** Date block (super label + large date in Noto Serif), status badge (Complete/Pending/Upcoming/Overdue), milestone title, description, progress bar.

**Editor panel:** Super label, date line 1, date line 2, milestone title, description, status pills, progress slider, accent color, card width.

### 6. Narrative Card (`_tpl_callout_narrative.html`)

**Display area:** 5px accent top bar, tag badge, title (Noto Serif), body text, optional callout quote with left border.

**Editor panel:** Tag, title, body textarea, callout textarea, accent color, card width.

## Files Changed

| File | Action |
|------|--------|
| `src/policydb/web/routes/charts.py` | Add 6 entries to `MANUAL_CHART_REGISTRY`; update snapshot save/load for `client_id` |
| `src/policydb/web/templates/charts/manual/gallery.html` | Add "Visual Builders" section divider + new category badges |
| `src/policydb/web/templates/charts/manual/editor.html` | Client combobox in snapshot bar; auto-height container support; export target fallback |
| `src/policydb/web/static/charts/manual.css` | Add `.manual-chart-page-auto` class |
| `src/policydb/web/templates/charts/manual/_tpl_timeline_builder.html` | **NEW** |
| `src/policydb/web/templates/charts/manual/_tpl_callout_stat.html` | **NEW** |
| `src/policydb/web/templates/charts/manual/_tpl_callout_coverage.html` | **NEW** |
| `src/policydb/web/templates/charts/manual/_tpl_callout_carrier.html` | **NEW** |
| `src/policydb/web/templates/charts/manual/_tpl_callout_milestone.html` | **NEW** |
| `src/policydb/web/templates/charts/manual/_tpl_callout_narrative.html` | **NEW** |

## Implementation Notes

- **CSS class naming:** `manual.css` already has `.callout-bar` and `.callout-stat` classes for chart callout bars. New callout card templates should use prefixed class names (e.g., `.co-stat-card`, `.co-cov-header`) or inline styles to avoid collisions.
- **Existing chart count:** The library currently has 9 chart types (including `quote_comparison` added in this session). After this work: 15 total (9 charts + 6 builders).

## What's NOT Included

- No new routes — all 6 types use existing `/charts/manual/{type}`
- No new database tables or migrations — uses existing `chart_snapshots`
- No new JS libraries — reuses Chart.js, html2canvas, ManualChart helpers
- No drag-to-reorder steps (timeline) — up/down buttons
- No client-based snapshot filtering (future enhancement)
