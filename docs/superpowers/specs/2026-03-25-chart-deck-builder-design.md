# Chart Deck Builder — Design Spec

## Context

Insurance brokers need presentation-quality visuals for client meetings — particularly renewal/placement recap decks. PolicyDB tracks all the underlying data (premiums, carriers, coverage terms, activities, premium history) but has no charting or presentation export capability. The goal is to add a **configurable deck builder** within policydb that generates D3.js charts and tables, exportable as PNG images for pasting into PowerPoint presentations.

The first deck type is **Renewal Recap** — presented after placing or renewing a client's policies. Future deck types (Portfolio Overview, Stewardship Report) can be added using the same infrastructure.

## Architecture

### Isolation Strategy

All chart functionality lives in an isolated `/charts/` namespace within policydb — separate routes, templates, and static assets, but sharing the database and server.

### New Files

```
src/policydb/
├── charts.py                          # Data assembly functions for charts
├── web/
│   ├── routes/
│   │   └── charts.py                  # All chart routes (APIRouter prefix="/charts")
│   ├── templates/
│   │   └── charts/
│   │       ├── index.html             # Client selector + deck type picker
│   │       ├── deck.html              # Chart configurator (checkboxes + configure)
│   │       ├── view.html              # Paginated chart viewer with export
│   │       ├── _chart_base.html       # Shared macro: 16:9 container + export button
│   │       ├── _chart_premium_comparison.html
│   │       ├── _chart_schedule.html
│   │       ├── _chart_tower.html
│   │       ├── _chart_carrier_breakdown.html
│   │       ├── _chart_rate_change.html
│   │       ├── _chart_activity_timeline.html
│   │       ├── _chart_market_conditions.html
│   │       ├── _chart_premium_history.html
│   │       └── _chart_coverage_comparison.html
│   └── static/
│       └── charts/
│           ├── d3.v7.min.js           # D3.js v7.9.0
│           ├── html2canvas.min.js     # html2canvas v1.4.1 (for table PNG export)
│           ├── jszip.min.js           # JSZip v3.10.1 (for Export All as ZIP)
│           ├── charts.js              # Shared chart rendering utilities
│           ├── export.js              # SVG → Canvas → PNG export logic
│           └── charts.css             # Chart-specific styles
```

**Note:** policydb currently has no `web/static/` directory — all JS/CSS is served via CDN in `base.html`. The `static/` directory and `StaticFiles` mount are new infrastructure. D3, html2canvas, and JSZip are served locally (not CDN) to ensure offline reliability.

### Modified Files

```
src/policydb/web/app.py               # Add: from starlette.staticfiles import StaticFiles
                                       # Add: app.mount("/static", StaticFiles(directory="..."), name="static")
                                       # Add: from policydb.web.routes import charts
                                       # Add: app.include_router(charts.router)
```

## User Flow

### Step 1 — Select Client & Deck Type

**Route:** `GET /charts/`

- Client search/filter dropdown (reuses existing client list query)
- Deck type selector: "Renewal Recap" (only option initially; extensible)
- Submit navigates to configurator

### Step 2 — Configure Charts

**Route:** `GET /charts/{client_id}/deck?type=renewal-recap`

- Grid of 9 chart cards, each with checkbox (all checked by default for renewal-recap)
- Each card has a "Configure" expand toggle (HTMX partial load for per-chart options):
  - **Premium Comparison:** date range filter
  - **Schedule:** column visibility toggles
  - **Tower:** which tower group to display (if multiple)
  - **Carrier Breakdown:** include/exclude specific lines
  - **Rate Change:** sort order (by % change vs by line)
  - **Activity Timeline:** date range, activity types to include
  - **Market Conditions:** form fields for manual benchmarking data (line, market avg %, notes) — supports dynamic row addition
  - **Premium History:** number of terms to show (3-5 cycles)
  - **Coverage Comparison:** which fields to compare
- "Generate Deck" button submits the entire form as a **POST** to Step 3

### Step 3 — Review & Export

**Route:** `POST /charts/{client_id}/deck/view`

Configuration travels from Step 2 → Step 3 via POST form data. The route handler receives:
- `selected_charts[]` — list of checked chart IDs
- Per-chart config fields (prefixed by chart ID, e.g., `premium_comparison__date_range`)
- Market conditions manual rows (as repeated field groups: `market__line[]`, `market__avg_pct[]`, `market__notes[]`)

The route assembles all chart data server-side, passes it to the template as JSON context, and D3 renders client-side.

**View features:**
- Paginated view: one chart per "page" at 16:9 presentation aspect ratio
- Sidebar navigation showing all included charts
- Per-chart: **Save PNG** button
- **Export All** button: generates a ZIP file (via JSZip) containing all chart PNGs, triggers a single browser download

### Chart Rendering Architecture

`view.html` includes each selected chart partial via Jinja2 loop:

```jinja2
{% for chart_id in selected_charts %}
  <div class="chart-page" id="chart-{{ chart_id }}" data-chart="{{ chart_id }}" style="display: none;">
    {% include "charts/_chart_" ~ chart_id ~ ".html" %}
  </div>
{% endfor %}
```

Each `_chart_*.html` partial extends the `_chart_base.html` macro which provides the 16:9 container, white background, title area, and export button. Chart data is injected as a `<script>` tag with JSON: `const chartData = {{ chart_data[chart_id] | tojson }};`

JavaScript in `charts.js` initializes D3 rendering for the active page on navigation.

## Chart Specifications

### 1. Premium Comparison (D3 bar chart)
- **Type:** Grouped bar chart
- **Data:** `policies.premium` + `policies.prior_premium` grouped by `policy_type`
- **Visual:** Gray bars (prior term) next to blue bars (current term), labeled axes
- **Empty state:** If no policies have `prior_premium`, show "No prior term data available" message

### 2. Schedule of Insurance (HTML table)
- **Type:** Formatted table
- **Data:** `v_schedule` view (exists; filtered via `WHERE client_name = (SELECT name FROM clients WHERE id = ?)` since `v_schedule` lacks `client_id`)
- **Columns:** Line, Carrier, Policy #, Effective, Expiration, Limit, Deductible, Premium, Form
- **Footer:** Total premium + policy count
- **Empty state:** "No active policies found"

### 3. Tower / Layer Diagram (D3 SVG) — Program Schematic
- **Type:** Grid/matrix block diagram (NOT a simple vertical stack)
- **Data:** `v_tower` view (filtered via `client_name` subquery) + `program_carriers` table
- **Visual complexity — must support real program schematics:**
  - Multiple underlying lines side-by-side at bottom (Auto, GL, Employers' Liability, WC)
  - Co-insured/shared excess layers — multiple carriers splitting a layer horizontally (e.g., AIG, First Specialty, Great American each with "$10M po $30M x $70M")
  - Separate independent towers (e.g., WC excess stack separate from casualty tower)
  - "po" (part of) notation for participation amounts
  - Varying deductible/SIR structures per underlying line
  - Color coding by layer type/position
- **Reference:** Real Marsh program schematic (Fieldale Farms 2026 Casualty) provided by user as target fidelity
- **Note:** policydb has tower positioning logic in `routes/clients.py` and fields `tower_group`, `layer_position`, `attachment_point`, `participation_of` — reuse the grouping approach
- **Empty state:** If no tower groups exist, show "No tower/layer structures — all policies are standalone"

### 4. Carrier Breakdown (D3 donut chart)
- **Type:** Donut/pie chart
- **Data:** New query — `SELECT carrier, SUM(premium) FROM policies WHERE client_id = ? AND is_opportunity = 0 GROUP BY carrier`
- **Visual:** Segments colored per carrier, legend with % and dollar amounts
- **Empty state:** "No premium data available"

### 5. Rate Change Summary (D3 horizontal bar chart)
- **Type:** Horizontal diverging bar chart
- **Data:** New query — policies with both `premium` and `prior_premium` set; calculates `(premium - prior_premium) / prior_premium * 100` per `policy_type`
- **Visual:** Green bars for decreases, red for increases, labeled with % change
- **Empty state:** If no policies have `prior_premium`, show "No prior term data for rate comparison"
- **Division safety:** Skip policies where `prior_premium` is 0 or NULL

### 6. Activity Timeline (D3 SVG)
- **Type:** Vertical timeline
- **Data:** New query — `activity_log` filtered by `client_id` + configurable date range (default: 180 days back from most recent policy expiration)
- **Visual:** Chronological dots with date labels, activity type + subject text
- **Color coding:** By activity type (call, email, meeting, etc.)
- **Empty state:** "No activity records found for the selected period"

### 7. Market Conditions (D3 grouped bar + manual input)
- **Type:** Grouped bar chart
- **Data source:** Manual entry (market avg rate change by line) + auto-populated actual rate change reusing `get_rate_change_data()` for the client's actual % change per line
- **Visual:** Side-by-side bars per line — market average (gray) vs. client's actual (blue/green/red)
- **Input:** Form fields on Step 2 configure panel: line of coverage (dropdown matching client's policy types), market avg %, optional notes. Dynamic "Add Row" button for multiple lines.
- **Empty state:** If no manual data entered, chart is skipped in the deck

### 8. Premium History Trend (D3 line chart)
- **Type:** Multi-line time series chart
- **Data:** `premium_history` table — `SELECT policy_type, term_effective, premium FROM premium_history WHERE client_id = ? ORDER BY term_effective`
- **Visual:** One line per coverage type, x-axis = term periods, y-axis = premium
- **Config:** Number of terms to display (default: 5)
- **Empty state:** "No premium history records found"

### 9. Coverage Comparison Grid (HTML table)
- **Type:** Side-by-side comparison table
- **Data:** New query — current policies joined with most recent `premium_history` row per policy_type to get prior term values
- **Columns per line:** Carrier (prior → current), Limit, Deductible, Premium, Form
- **Visual:** Highlight cells that changed between terms (CSS class on changed cells)
- **Empty state:** "No prior term data available for comparison"

### 10. Exposure Trend (D3 line chart) — requires exposure tracking
- **Type:** Multi-line time series chart
- **Data:** `client_exposures` table grouped by `exposure_type` across years
- **Visual:** One line per exposure type, x-axis = years, y-axis = value
- **Config:** Number of years to display (default: 5)
- **Empty state:** "No exposure data tracked for this client"

### 11. Normalized Premium (D3 grouped bar) — requires exposure tracking
- **Type:** Grouped bar chart
- **Data:** Premium per $M payroll, per $M revenue, etc. — joins `policies.premium` with `client_exposures.amount`
- **Visual:** Bars per exposure type showing normalized rate, with YoY trend
- **Empty state:** "No exposure data available for normalization"

### 12. Key Observations Slide (HTML) — requires exposure tracking
- **Type:** Styled observation cards (same format as the Exposures tab panel)
- **Data:** `client_exposures` YoY changes, sorted by absolute % change
- **Visual:** Color-banded cards (red/orange/blue/green by severity) with notes
- **Empty state:** "No prior year exposure data for comparison"

### 13. Exposure vs Premium (D3 dual-axis) — requires exposure tracking
- **Type:** Dual-axis line chart
- **Data:** Exposure growth % vs premium growth % over time
- **Visual:** Two lines with separate y-axes — shows whether premiums are keeping pace with exposure growth
- **Empty state:** "Insufficient historical data for comparison"

## Data Layer (`src/policydb/charts.py`)

Functions that assemble chart-ready data. Some reuse existing query functions; others require new SQL queries (noted below).

```python
# Reuses existing query patterns
def get_premium_comparison_data(conn, client_id) -> list[dict]
def get_schedule_data(conn, client_id) -> list[dict]       # Uses v_schedule with client_name subquery
def get_tower_data(conn, client_id) -> list[dict]           # Uses v_tower with client_name subquery
def get_premium_history_data(conn, client_id, num_terms=5) -> list[dict]

# Requires new SQL queries
def get_carrier_breakdown_data(conn, client_id) -> list[dict]   # New: GROUP BY carrier
def get_rate_change_data(conn, client_id) -> list[dict]         # New: rate calc with div-by-zero guard
def get_activity_timeline_data(conn, client_id, days_back=180) -> list[dict]  # New: date-range filtered
def get_coverage_comparison_data(conn, client_id) -> list[dict] # New: current + prior term join

# Requires exposure tracking (client_exposures table)
def get_exposure_trend_data(conn, client_id, num_years=5) -> list[dict]
def get_normalized_premium_data(conn, client_id) -> list[dict]
def get_exposure_observations_data(conn, client_id) -> list[dict]
def get_exposure_vs_premium_data(conn, client_id) -> list[dict]
```

**Market Conditions** has no server-side data function — manual form data is passed directly from Step 2 POST to the template. The client's actual rate change data is auto-populated by reusing `get_rate_change_data()`.

## PNG Export Approach

**D3 SVG charts (charts 1, 3, 4, 5, 6, 7, 8):**
1. D3 renders as SVG elements
2. Export: serialize SVG to XML string → create `Blob` → load as `Image` → draw to `Canvas` → `canvas.toDataURL('image/png')` → trigger download
3. **Do NOT use `foreignObject`** — it taints the canvas and blocks `toDataURL()`. Use the SVG serialization approach.

**HTML tables (charts 2, 9):**
1. Use `html2canvas` library (served locally from `static/charts/html2canvas.min.js`) to capture the table element
2. `html2canvas(element).then(canvas => ...)` → `canvas.toDataURL('image/png')` → trigger download

**Export All:**
1. Iterate through all chart pages, export each to PNG blob
2. Add all blobs to a JSZip archive (served locally from `static/charts/jszip.min.js`)
3. Generate ZIP → trigger single browser download as `{client_name}_renewal_recap.zip`

## Styling

- Charts use a professional, muted color palette suitable for client presentations
- **White background** (not dark theme) — optimized for PPT insertion
- Tailwind CSS for page layout (already in base.html)
- Chart-specific CSS in `static/charts/charts.css`
- All chart containers: 16:9 aspect ratio (960×540px default, scalable)
- `_chart_base.html` macro provides the shared container: white bg, title, 16:9 ratio, export button

## Deck Types (Extensible)

The system is built around deck types. Each type defines:
- Which charts are included by default
- Default configuration per chart
- Chart ordering

**Initial:** Renewal Recap (13 charts — 9 core + 4 exposure-based)
**Future:** Portfolio Overview, Stewardship Report (reuse same chart components with different defaults)

## Potential Schema Extensions

Some charts may benefit from additional policydb fields (noted for future):
- Financial detail fields (TBD based on presentation needs)
- Market benchmarking data persistence (if we want to save manual inputs between sessions)
- Adding `client_id` to `v_schedule` and `v_tower` views (currently use `client_name` workaround)
- These will be identified during implementation and added via migrations

## Verification

1. Start policydb: `pdb serve`
2. Navigate to `localhost:8000/charts/`
3. Select a client with existing policies and premium history
4. Configure a Renewal Recap deck (all 9 charts)
5. Verify each chart renders correctly with real data
6. Test empty states: select a client with no premium history, verify graceful fallback messages
7. Test "Save PNG" on each chart type — confirm clean white-background image
8. Test "Export All" — confirm ZIP downloads with all chart PNGs
9. Test Market Conditions manual input — add rows, verify comparison chart renders
10. Verify charts look professional on white background at 16:9 ratio
11. Paste a PNG into PowerPoint — confirm it looks clean at standard slide dimensions
