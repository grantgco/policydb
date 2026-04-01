---
name: policydb-reports
description: Insurance report and chart reference for PolicyDB. Use when building, modifying, or discussing chart deck reports, manual chart templates, renewal recaps, tower/layer diagrams, program schematics, TCOR, benchmarking, loss analysis, quote comparison, or any insurance visualization. Covers all 29 chart types, their insurance meaning, data sources, and Marsh-style notation conventions.
---

# PolicyDB Reports & Insurance Schematics

## Overview

PolicyDB has two complementary chart systems producing insurance deliverables:

1. **Chart Deck Builder** (`/charts/{client_id}/deck`) — 14 auto-generated, data-driven charts assembled into a paginated renewal recap presentation. Data pulled live from policies, premium_history, client_exposures, and activity_log tables.

2. **Manual Chart Library** (`/charts/manual`) — 15 editable Chart.js + HTML templates for custom presentations. Fully user-editable with snapshot persistence. See `policydb-charts` skill for template mechanics and export-safe CSS rules.

Both render at 16:9 (960x540) and export as PNG via html2canvas. Chart decks export as ZIP bundles.

---

## Chart Deck: 14 Auto-Generated Reports

### Core Charts (9)

| ID | Title | Viz Type | Insurance Purpose |
|----|-------|----------|-------------------|
| `premium_comparison` | Premium Comparison | D3 grouped bar | Prior vs current premium by line of business — shows where costs went up/down |
| `schedule` | Schedule of Insurance | HTML table | Complete policy listing: line, carrier, policy #, dates, limit, deductible, premium, form. Ghost rows expand package sub-coverages |
| `tower` | Tower / Layer Diagram | D3 SVG schematic | **Program structure visualization** — underlying primary lines as columns, excess/umbrella layers stacked above. See Tower Schematics section below |
| `carrier_breakdown` | Carrier Breakdown | D3 donut | Premium concentration by carrier — reveals dependency risk |
| `rate_change` | Rate Change Summary | D3 horizontal bar | % premium change by line — green for decreases, red for increases, sorted by magnitude |
| `activity_timeline` | Activity Timeline | D3 vertical timeline | 180-day activity history: calls, meetings, emails with dates and contacts |
| `market_conditions` | Market Conditions | D3 grouped bar | Client's actual rate change vs market average by line — manual entry rows for benchmark data |
| `premium_history` | Premium History Trend | D3 multi-line | Premium over 3-5 terms by line — reveals long-term cost trajectory |
| `coverage_comparison` | Coverage Comparison | HTML table | Current vs prior term side-by-side per line — highlights changed fields (carrier, premium, limit, deductible) |

### Exposure Analytics (4)

| ID | Title | Viz Type | Insurance Purpose |
|----|-------|----------|-------------------|
| `exposure_trend` | Exposure Trend | D3 multi-line | Corporate exposure values (Payroll, Revenue, Vehicle Count, etc.) over years — tracks risk growth |
| `normalized_premium` | Normalized Premium | D3 grouped bar | Premium per $1M of exposure (current vs prior) — the true rate metric for commercial insurance. Reveals whether premium changes are driven by rate or exposure changes |
| `observations` | Key Observations | HTML cards | YoY exposure change with severity: >15% severe, >8% high, >5% moderate, <=5% low. Color-coded direction arrows |
| `exposure_vs_premium` | Exposure vs Premium | D3 dual-axis line | YoY % growth in exposure vs premium — shows whether premium keeps pace with risk growth |

### Executive (1)

| ID | Title | Viz Type | Insurance Purpose |
|----|-------|----------|-------------------|
| `exec_summary` | Executive Financial Summary | HTML table | Bound program premiums grouped by section (Program - Primary/Excess). Columns: Line, Carrier, Expiring, Normalized, Renewal, Delta $, Delta %. Subtotals per section + grand total. Editable via snapshot or inline configurator |

---

## Manual Chart Library: 15 Editable Templates

### Financial & Analysis Charts (9)

| ID | Title | Chart.js Type | Insurance Use Case |
|----|-------|---------------|---------------------|
| `rate_premium_baseline` | Rate & Premium vs. Baseline | Dual-axis line | Compare rate and premium trends against a selectable baseline year. Dual Y-axes: teal for rate, navy for premium. Shows rate inflation vs premium cost movement over time |
| `benchmark_distribution` | Benchmarking Distribution | Bar + avg line | Percentile bars (10th/25th/50th/75th/90th) showing where client sits within industry/peer distribution. Horizontal average line overlay + stat callout |
| `loss_history` | Loss History | Stacked bar + trend | Incurred losses by year with loss ratio trend line. Optional large-loss threshold marker. Reveals abnormal loss years |
| `premium_allocation` | Premium Allocation | Doughnut | Premium distribution by coverage line — shows concentration risk (e.g., 60% in Property) |
| `rate_trend_line` | Rate Trend by Line | Multi-line | Rate change % over years by coverage line — reveals which lines face rate pressure. Zero baseline reference |
| `tcor_trend` | Total Cost of Risk (Trend) | Stacked bar + line | Stacked bars: insurance premium (navy) + retained losses (gold). TCOR trend line overlaid. Reveals true cost burden including self-insured retention |
| `tcor_breakdown` | TCOR Breakdown (Waterfall) | Waterfall | Single-year TCOR decomposition: retained losses + premium by line + fees = total. Waterfall bars building to final total |
| `freq_severity` | Claims Frequency vs. Severity | Dual-axis bar + line | Claim count bars + average claim cost line. Separates frequency trends from severity — reveals if losses driven by more claims or bigger claims |
| `quote_comparison` | Quote Comparison | Bubble + table | Bubble chart: X=TIV, Y=applied rate, size=premium. Ranked table: carriers sorted by rate rank and premium rank with "vs. lowest" column. Identifies best-value carrier placement |

### Visual Builders (7)

| ID | Title | Type | Insurance Use Case |
|----|-------|------|---------------------|
| `timeline_builder` | Timeline Builder | Process timeline | Renewal process milestones, underwriting workflows, phase tracking. Horizontal/vertical/phase layouts with progress bar |
| `team_chart` | Team Chart | Org chart | Internal team members for a client, grouped by assignment or role. Grid and Grouped layouts with dynamic scaling (4-15+ members). "Load from Client" pre-populates from `contact_client_assignments`. Fixed 960x540 slide format |
| `callout_stat` | Big Stat / KPI | Metric card | Single KPI: "22% Premium Reduction", "15% Loss Ratio", "$5M TCOR". Direction arrow (green down = favorable, red up = unfavorable) |
| `callout_coverage` | Coverage Card | Summary card | Single coverage line snapshot: line, carrier, limit, deductible/SIR, premium, effective date, key terms tags |
| `callout_carrier` | Carrier Tile | Summary card | Carrier profile: AM Best rating, lines written, total premium, participation %, retention/SIR, notes |
| `callout_milestone` | Milestone Card | Status card | Key date with status badge (pending/complete/at-risk), description, progress bar |
| `callout_narrative` | Narrative Card | Text card | Market commentary, analyst observations, risk narrative with blockquote callout |

---

## Tower / Layer Schematics — Insurance Domain Knowledge

The tower diagram is the most domain-complex chart. It visualizes how a commercial insurance program is structured across primary (underlying) lines and excess layers.

### Program Structure Concepts

| Concept | Meaning | Example |
|---------|---------|---------|
| **Underlying / Primary** | First-dollar coverage lines sitting at ground level | General Liability $2M, Auto Liability $1M |
| **Excess / Umbrella** | Layers stacking above primary, triggered when primary exhausts | $10M Umbrella x Primary (sits on top of all underlying) |
| **Attachment Point** | Dollar threshold where an excess layer begins responding | $10M x $5M = $10M limit attaching at $5M |
| **Participation Of** | Co-insurance — carrier shares a layer with others | $5M po $25M x $10M = $5M part-of $25M layer at $10M attachment |
| **Tower Group** | Logical grouping of related lines into one program column set | "Casualty" groups GL + Auto + WC + Umbrella + Excess |
| **Statutory** | Workers' Comp is statutory (state-mandated limits), not a dollar limit | WC column shows "Statutory" instead of limit amount |
| **Package Policy** | Single policy containing multiple sub-coverages (BOP = Building + Contents + GL) | Package "explodes" into individual columns in the tower |

### Tower Notation Convention (Marsh Standard)

Generated by `_layer_notation()` in `charts.py`:

| Notation | Meaning | When Used |
|----------|---------|-----------|
| `$5M x Primary` | $5M limit sitting directly on primary lines | Umbrella with attachment_point = 0 |
| `$10M x $5M` | $10M limit attaching at $5M | Standard excess layer |
| `$10M x $30M` | $10M limit attaching at $30M | Higher excess layer |
| `$5M po $25M x $10M` | $5M participation of a $25M layer at $10M | Co-insured layer (multiple carriers sharing) |

**Formatting rules:** Values >= $1M show as `$NM`, >= $1K as `$NK`, otherwise `$N,NNN`. Decimal precision only when needed (`$1.5M` not `$1.0M`).

### Package Explosion Logic

When a policy has sub-coverages (`policy_sub_coverages` table):

1. **Non-WC packages** (e.g., BOP): Parent row replaced by individual sub-coverage columns, each with its own limit/deductible
2. **WC packages**: Statutory WC column kept; Employers' Liability split to separate column
3. **Excess-type sub-coverages** (Umbrella, Excess keywords): Promoted from underlying to the layers list regardless of parent package type
4. **Premium attribution**: Sub-coverage columns show `$0` premium to avoid double-counting (parent row has the total)

### Tower Rendering Architecture

- **D3.js SVG** with unified Y-axis mapping dollar values to pixel positions
- **Underlying columns** drawn flush at bottom with deductible bars below
- **Excess layers** stacked above, sorted by `attachment_point` ascending
- **Column ordering** via `schematic_column` field on policy (user-configurable)
- **Program grouping**: `tower_group` field or `program.name` — multiple programs render as separate tower diagrams
- **Co-insured participants** queried via `program_tower_coverage` junction table

### Tower Data Flow

```
policies table (tower_group, layer_position, attachment_point, participation_of)
  + programs table (program grouping)
  + policy_sub_coverages (package explosion)
  + program_tower_coverage (co-insured linkage)
    → get_tower_data() in charts.py
      → groups by program_name
      → separates underlying vs layers
      → explodes packages
      → attaches participants
      → returns [{program_name, underlying[], layers[]}]
```

---

## Insurance Metrics Reference

### Normalized Premium
Premium per $1M of exposure. The true "rate" metric for commercial insurance because it adjusts for exposure size changes. A premium increase of 10% with a 15% exposure increase actually represents a rate *decrease*.

**Formula:** `(total_premium / exposure_amount) * 1,000,000`

### Total Cost of Risk (TCOR)
The complete cost of risk to the organization: insurance premiums + retained losses (deductibles, SIR claims, uninsured losses) + risk management costs + administrative fees. Tracked as both trend (multi-year) and breakdown (single-year waterfall).

### Loss Ratio
Incurred losses divided by earned premium. A loss ratio > 100% means losses exceed premiums paid. Tracked as trend line overlaid on loss history bars.

### Frequency vs Severity
Separating claim count (frequency) from average claim cost (severity) reveals whether loss deterioration is driven by more claims happening or each claim costing more. Different root causes require different risk controls.

### Benchmarking Distribution
Percentile bands (10th/25th/50th/75th/90th) from industry data showing where the client's metric sits relative to peers. Used for premium rates, loss ratios, TCOR, and retention levels.

### Exposure Types
Corporate-level risk metrics tracked in `client_exposures` table: Revenue, Payroll, Vehicle Count, Property Values (TIV), Employee Count, Square Footage. Each with annual values and unit type.

---

## Data Sources by Chart

| Chart | Primary Table(s) | Key Fields |
|-------|-------------------|------------|
| premium_comparison | policies | policy_type, premium, prior_premium |
| schedule | v_schedule view | All policy fields via view |
| tower | v_tower + policy_sub_coverages + program_tower_coverage | tower_group, layer_position, attachment_point, participation_of, limit_amount |
| carrier_breakdown | policies | carrier, premium |
| rate_change | policies | policy_type, premium, prior_premium (HAVING prior > 0) |
| activity_timeline | activity_log | activity_date, activity_type, subject, contact_person, details |
| coverage_comparison | policies + premium_history | Current vs most recent prior term per policy_type |
| premium_history | premium_history | policy_type, term_effective, premium (3-5 terms) |
| exposure_trend | client_exposures | exposure_type, unit, year, amount (corporate level only) |
| normalized_premium | policies + client_exposures | Total premium / exposure amount * 1M |
| observations | client_exposures (via queries.py) | YoY % change by exposure_type |
| exposure_vs_premium | client_exposures + premium_history | YoY growth rates compared |
| exec_summary | policies + programs | Grouped by program section (Primary/Excess), delta calculations |
| market_conditions | Manual entry | User-provided market benchmark data per line |

**All auto-charts:** Filter `is_opportunity = 0` and `archived = 0`. Views use `client_name` subquery, not `client_id` directly.

---

## Deck Lifecycle

```
/charts/ (client selector)
  → /charts/{id}/deck (configurator: checkboxes per chart + inline editors)
    → POST /charts/{id}/deck/view (paginated 16:9 viewer)
      → Per-chart "Save PNG" (html2canvas)
      → "Export All as ZIP" (JSZip → {client_name}_renewal_recap.zip)
      → Keyboard nav (← →), sidebar index
```

**Tower layout option:** "combined" (one slide) vs "separate" (one slide per program group).

**Exec summary:** Editable table in configurator with snapshot save/load. Manual override rows: Section, Line, Carrier, Expiring, Normalized, Renewal.

**Market conditions:** Dynamic row table for manual benchmark entry per line (market avg %, notes).

---

## Key Files

| File | Purpose |
|------|---------|
| `src/policydb/charts.py` | 13 data functions + tower notation helper |
| `src/policydb/web/routes/charts.py` | All routes: deck configurator, viewer, manual gallery/editor, snapshot CRUD |
| `src/policydb/web/templates/charts/deck.html` | Deck configurator form |
| `src/policydb/web/templates/charts/view.html` | Paginated deck viewer + export controls |
| `src/policydb/web/templates/charts/_chart_*.html` | 14 individual deck chart templates |
| `src/policydb/web/templates/charts/manual/_tpl_*.html` | 15 manual chart templates |
| `src/policydb/web/static/charts/charts.js` | D3 renderers, formatters, ChartNav controller |
| `src/policydb/web/static/charts/manual.js` | ManualChart namespace: COLORS, formatters, snapshot CRUD |
| `src/policydb/ghost_rows.py` | Package sub-coverage ghost row injection for schedule |

---

## Color System (Marsh Brand)

**Deck charts (D3):** `prior: #9ca3af` (gray), `current: #003865` (navy), `increase: #dc2626` (red), `decrease: #059669` (green)

**Manual charts (Chart.js):** Midnight `#000F47`, Sky `#CEECFF`, Green `#2F7500`/`#6ABF30`, Purple `#5E017F`, Gold `#CB7E03`, Red `#c8102e`, Teal `#0e8c79`

**Data color sequence:** `['#000F47', '#2F7500', '#5E017F', '#CB7E03', '#82BAFF', '#6ABF30', '#8F20DE', '#FFBF00']`

**Fonts:** Noto Serif (headings/callouts), Noto Sans (body/labels/data). See `policydb-design-system` skill for full brand guide.
