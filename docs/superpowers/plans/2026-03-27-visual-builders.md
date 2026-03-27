# Visual Builders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Timeline Builder + 5 Callout Card types to the Manual Chart Library with Marsh brand styling, snapshot save/load, client tagging, and PNG export.

**Architecture:** Each builder is a self-contained Jinja2 template partial (`_tpl_*.html`) included by the existing `editor.html` wrapper. Templates render a preview card in an auto-height container (`.manual-chart-page-auto`) with an editor panel below. State is serialized to hidden textareas for snapshot compatibility with `collectAll`/`populateAll`.

**Tech Stack:** Jinja2 templates, vanilla JS, html2canvas (already loaded), ManualChart helpers (manual.js), Marsh brand colors/fonts (Noto Serif/Sans).

**Spec:** `docs/superpowers/specs/2026-03-27-visual-builders-design.md`

**Source files:** The original standalone tools are at `/Users/grantgreeson/Downloads/timeline-builder.html` and `/Users/grantgreeson/Downloads/callout-builder.html`. Read these for reference CSS/JS when building templates.

**Key constraints:**
- All named inputs for snapshot state MUST be inside `#editor-panel` (not in the display area). `doSaveSnapshot()` collects from `.max-w-5xl` (whole page) but `doLoadSnapshot()` populates only `#editor-panel`. Hidden textareas for serialized state go in the editor panel.
- No `/api/clients/search` JSON endpoint exists — must be created in `charts.py` for the client combobox.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/policydb/web/routes/charts.py` | Registry entries + snapshot route changes for client_id |
| `src/policydb/web/static/charts/manual.css` | Add `.manual-chart-page-auto` class |
| `src/policydb/web/templates/charts/manual/gallery.html` | Visual Builders section + new icons/badges |
| `src/policydb/web/templates/charts/manual/editor.html` | Client combobox + auto-height export path |
| `src/policydb/web/templates/charts/manual/_tpl_callout_stat.html` | Big Stat / KPI card |
| `src/policydb/web/templates/charts/manual/_tpl_callout_coverage.html` | Coverage Card |
| `src/policydb/web/templates/charts/manual/_tpl_callout_carrier.html` | Carrier Tile |
| `src/policydb/web/templates/charts/manual/_tpl_callout_milestone.html` | Milestone Card |
| `src/policydb/web/templates/charts/manual/_tpl_callout_narrative.html` | Narrative Card |
| `src/policydb/web/templates/charts/manual/_tpl_timeline_builder.html` | Timeline Builder (3 layout modes) |

---

### Task 1: Infrastructure — CSS, Registry, Gallery

**Files:**
- Modify: `src/policydb/web/static/charts/manual.css` (after line 30)
- Modify: `src/policydb/web/routes/charts.py` (lines 65-68 registry, lines 76-130 snapshot routes, lines 135-152 gallery route)
- Modify: `src/policydb/web/templates/charts/manual/gallery.html`

- [ ] **Step 1: Add `.manual-chart-page-auto` CSS class**

In `manual.css`, after the `.manual-chart-page` block (line 30), add:

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

- [ ] **Step 2: Add 6 entries to `MANUAL_CHART_REGISTRY`**

In `charts.py`, after the `quote_comparison` entry (line 67), before the closing `]`:

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

- [ ] **Step 3: Add client search JSON endpoint**

Add a new route in `charts.py` BEFORE the snapshot routes (after `_MANUAL_TITLE_MAP`). This provides a lightweight JSON endpoint for the client combobox:

```python
@router.get("/api/client-search", response_class=JSONResponse)
async def chart_client_search(q: str = "", conn=Depends(get_db)):
    """Search clients by name for snapshot tagging."""
    if not q or len(q) < 2:
        return []
    rows = conn.execute(
        "SELECT id, name FROM clients WHERE archived = 0 AND name LIKE ? ORDER BY name LIMIT 10",
        (f"%{q}%",),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]
```

**Important:** This route MUST come before `/manual/snapshots/{chart_type}` to avoid path capture conflicts.

- [ ] **Step 4: Update snapshot routes to support `client_id`**

In `charts.py`, update all 4 snapshot routes:

**`manual_list_snapshots` (line 76):**
```python
rows = conn.execute(
    "SELECT s.id, s.name, s.updated_at, s.client_id, c.name as client_name "
    "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
    "WHERE s.chart_type = ? ORDER BY s.updated_at DESC",
    (f"manual_{chart_type}",),
).fetchall()
```

**`manual_load_snapshot` (line 86):**
```python
row = conn.execute(
    "SELECT s.id, s.name, s.data, s.updated_at, s.client_id, c.name as client_name "
    "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
    "WHERE s.id = ? AND s.chart_type = ?",
    (snapshot_id, f"manual_{chart_type}"),
).fetchone()
```

**`manual_save_snapshot` (line 100):**
```python
client_id = body.get("client_id") or None  # convert empty string/0 to None

if snapshot_id:
    conn.execute(
        "UPDATE chart_snapshots SET name = ?, data = ?, client_id = ?, updated_at = datetime('now') "
        "WHERE id = ? AND chart_type = ?",
        (name, json.dumps(data), client_id, snapshot_id, f"manual_{chart_type}"),
    )
else:
    cur = conn.execute(
        "INSERT INTO chart_snapshots (client_id, chart_type, name, data) VALUES (?, ?, ?, ?)",
        (client_id, f"manual_{chart_type}", name, json.dumps(data)),
    )
```

**`manual_delete_snapshot` (line 124):** Remove `AND client_id IS NULL`:
```python
conn.execute(
    "DELETE FROM chart_snapshots WHERE id = ? AND chart_type = ?",
    (snapshot_id, f"manual_{chart_type}"),
)
```

- [ ] **Step 5: Update gallery route for client names**

In the `manual_gallery` route (line 136), update the snapshots query:
```python
snapshots = conn.execute(
    "SELECT s.id, s.chart_type, s.name, s.updated_at, s.client_id, c.name as client_name "
    "FROM chart_snapshots s LEFT JOIN clients c ON s.client_id = c.id "
    "WHERE s.chart_type LIKE 'manual_%' "
    "ORDER BY s.updated_at DESC LIMIT 12"
).fetchall()
```

Also split the registry into `charts` and `builders` for the template:
```python
charts = [c for c in MANUAL_CHART_REGISTRY if c["category"] not in ("builder", "card")]
builders = [c for c in MANUAL_CHART_REGISTRY if c["category"] in ("builder", "card")]
```

Pass both to the template context.

- [ ] **Step 6: Update `gallery.html` — two sections + new icons/badges**

**NOTE:** Steps 5 and 6 must be done together — the route passes `charts` and `builders` as separate lists, and the template must loop over both. If only one is updated, the gallery breaks.

Split the existing single grid into two sections. Add new icon SVGs for `timeline`, `stat`, `card`. Add badge colors for `builder` and `card` categories. Show client name on recent snapshots when present.

Key additions to the icon section:
```html
{% elif c.icon == 'timeline' %}
<svg width="40" height="28" viewBox="0 0 40 28" fill="none" stroke="#000F47" stroke-width="2">
  <line x1="6" y1="14" x2="34" y2="14"/>
  <circle cx="6" cy="14" r="4" fill="#CEECFF"/><circle cx="20" cy="14" r="4" fill="#CEECFF"/><circle cx="34" cy="14" r="4" fill="#CEECFF"/>
</svg>
{% elif c.icon == 'stat' %}
<svg width="32" height="28" viewBox="0 0 32 28" fill="none">
  <text x="4" y="22" font-family="Noto Serif,serif" font-size="22" font-weight="700" fill="#000F47">42</text>
  <path d="M26 8 L26 18 M22 14 L26 18 L30 14" stroke="#2F7500" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
{% elif c.icon == 'card' %}
<svg width="36" height="28" viewBox="0 0 36 28" fill="none">
  <rect x="2" y="2" width="32" height="24" rx="3" fill="#CEECFF" stroke="#000F47" stroke-width="1.5"/>
  <rect x="2" y="2" width="32" height="8" rx="3" fill="#000F47"/>
</svg>
```

Badge colors:
```
{% elif c.category == 'builder' %}bg-teal-50 text-teal-700
{% elif c.category == 'card' %}bg-purple-50 text-purple-700
```

- [ ] **Step 7: Verify gallery renders with new sections**

Start server, navigate to `/charts/manual`, verify:
- Charts section shows 9 existing chart cards
- "Visual Builders" section header appears below
- 6 new builder/card items show with correct icons and badge colors
- Each links to `/charts/manual/{id}` (will 404 until templates exist — that's expected)

- [ ] **Step 8: Commit**

```
feat: add Visual Builders gallery section + registry entries

- 6 new MANUAL_CHART_REGISTRY entries (builder/card categories)
- Gallery split into Charts + Visual Builders sections
- New icon SVGs (timeline, stat, card)
- .manual-chart-page-auto CSS class for variable-height containers
- Snapshot routes updated to support optional client_id
```

---

### Task 2: Editor Wrapper — Client Tagging + Auto-Height Export

**Files:**
- Modify: `src/policydb/web/templates/charts/manual/editor.html`

- [ ] **Step 1: Add client combobox to snapshot bar**

In `editor.html`, inside the `#snapshot-panel` div (after the snapshot name input, before Save button), add:

```html
<div style="position:relative;display:inline-block;">
  <input type="text" id="snapshot-client" placeholder="Client (optional)"
         autocomplete="off" class="text-sm" style="width:180px;"
         oninput="searchClients(this.value)">
  <div id="client-dropdown" style="display:none;position:absolute;top:100%;left:0;right:0;
       background:white;border:1px solid #ccc;border-radius:4px;box-shadow:0 4px 12px rgba(0,0,0,0.1);
       max-height:200px;overflow-y:auto;z-index:100;"></div>
</div>
<input type="hidden" id="snapshot-client-id" value="">
```

Add JS for search + dropdown selection (NOT `<datalist>` — poor cross-browser support):
```javascript
async function searchClients(q) {
  const dd = document.getElementById('client-dropdown');
  if (!q || q.length < 2) { dd.style.display = 'none'; return; }
  const r = await fetch('/charts/api/client-search?q=' + encodeURIComponent(q));
  const clients = await r.json();
  if (!clients.length) { dd.style.display = 'none'; return; }
  dd.innerHTML = '';
  clients.forEach(c => {
    const div = document.createElement('div');
    div.textContent = c.name;
    div.style.cssText = 'padding:6px 10px;cursor:pointer;font-size:13px;';
    div.onmouseover = () => div.style.background = '#f5f5f5';
    div.onmouseout = () => div.style.background = '';
    div.onclick = () => {
      document.getElementById('snapshot-client').value = c.name;
      document.getElementById('snapshot-client-id').value = c.id;
      dd.style.display = 'none';
    };
    dd.appendChild(div);
  });
  dd.style.display = '';
}
// Close dropdown on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('#snapshot-client') && !e.target.closest('#client-dropdown'))
    document.getElementById('client-dropdown').style.display = 'none';
});
```

Update `doSaveSnapshot()` to include `client_id`:
```javascript
const clientId = document.getElementById('snapshot-client-id').value;
const payload = { name, data };
if (existingId) payload.id = parseInt(existingId);
if (clientId) payload.client_id = parseInt(clientId);
```

Update `doLoadSnapshot()` to populate client name:
```javascript
// After populating other fields:
document.getElementById('snapshot-client').value = result.client_name || '';
document.getElementById('snapshot-client-id').value = result.client_id || '';
```

- [ ] **Step 2: Update export for auto-height containers**

In the `exportChart()` function, update the target selection and branching:

```javascript
function exportChart(e) {
  var chartPage = document.querySelector('.manual-chart-page')
               || document.querySelector('.manual-chart-page-auto');
  if (!chartPage) { ManualChart.toast('No chart to export', 'error'); return; }

  var isAutoHeight = chartPage.classList.contains('manual-chart-page-auto');

  if (isAutoHeight) {
    // Auto-height: export directly at natural size × 2, no size picker
    exportAutoHeight(chartPage);
  } else {
    // Fixed-height: show size picker dropdown (existing behavior)
    // ... existing menu code unchanged ...
  }
}

function exportAutoHeight(chartPage) {
  var clone = chartPage.cloneNode(true);
  clone.querySelectorAll('.no-print').forEach(el => el.remove());

  var nativeW = chartPage.offsetWidth;
  var nativeH = chartPage.offsetHeight;

  var container = document.createElement('div');
  container.style.cssText = 'position:fixed;left:-9999px;top:0;width:' + nativeW + 'px;height:' + nativeH + 'px;background:white;';
  document.body.appendChild(container);
  container.appendChild(clone);

  html2canvas(container, {
    width: nativeW, height: nativeH,
    scale: 2, backgroundColor: '#ffffff',
    useCORS: true, logging: false
  }).then(function(canvas) {
    document.body.removeChild(container);
    canvas.toBlob(function(blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = CHART_TYPE + '.png';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(a.href);
      ManualChart.toast('PNG exported', 'success');
    }, 'image/png');
  }).catch(function(err) {
    document.body.removeChild(container);
    ManualChart.toast('Export failed: ' + err.message, 'error');
  });
}
```

For auto-height containers, the Export PNG button calls `exportAutoHeight()` directly — no dropdown menu.

- [ ] **Step 3: Test client tagging + export with an existing chart**

Navigate to an existing chart (e.g., `/charts/manual/freq_severity`). Verify:
- Client combobox appears in snapshot bar
- Typing searches clients
- Save with client tags correctly
- Load restores client name
- Export PNG still works for existing fixed-height charts

- [ ] **Step 4: Commit**

```
feat: client tagging on snapshots + auto-height export support

- Client combobox in snapshot save bar (all chart types)
- Snapshot routes accept/return client_id + client_name
- Export function supports auto-height containers
```

---

### Task 3: Callout — Big Stat / KPI Card

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_callout_stat.html`

- [ ] **Step 1: Create the template**

Reference the original `callout-builder.html` stat card (lines 96-117 for HTML, 696-706 for state, 788-836 for form, 1096-1117 for render).

Template structure:
- **Display:** `.manual-chart-page-auto` container → card with left accent bar, eyebrow, large stat value + direction arrow + unit, label, sublabel, footer
- **Editor:** Display toggles, eyebrow input, direction segmented control (↓ ↑ —), value, unit, label, sublabel textarea, footer, accent color swatches (ManualChart.DATA_COLORS), card width selector (`<select name="cfg-card-width">`)
- **Script:** `refreshCurrentChart()` reads inputs, builds card HTML, applies width. No Chart.js needed. Use inline styles with `ManualChart.COLORS` references.

Brand conversion: Noto Serif for `.stat-value`, Noto Sans for everything else. Colors from `ManualChart.COLORS`.

- [ ] **Step 2: Verify in browser**

Navigate to `/charts/manual/callout_stat`. Verify:
- Card renders with placeholder data
- Editing values + clicking Refresh updates preview
- Accent color swatches change the left bar color
- Width selector resizes card
- Export PNG captures the card at natural dimensions
- Snapshot save/load works

- [ ] **Step 3: Commit**

```
feat: add Big Stat / KPI callout card to manual chart library
```

---

### Task 4: Callout — Coverage Card

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_callout_coverage.html`

- [ ] **Step 1: Create the template**

Reference original `callout-builder.html` coverage card (lines 362-440 for CSS, 708-718 for state, 838-887 for form, 1119-1143 for render).

Template structure:
- **Display:** `.manual-chart-page-auto` → colored header (coverage line + carrier badge), body with 2×2 grid (Limit, Deductible, Premium, Effective Date), key terms as pills
- **Editor:** Line of coverage, carrier, limit, deductible, premium, effective date, key terms (tag input — text input + Add button + removable pills), header color swatches, card width
- **Script:** Key terms stored in a hidden `<textarea name="co-cov-state">` as JSON array. Tag add/remove rebuilds the pill list. `refreshCurrentChart()` builds card HTML.

Brand: Noto Serif for `.cov-line` header text, Noto Sans elsewhere. `ManualChart.COLORS` for header background swatches.

- [ ] **Step 2: Verify in browser**

Navigate to `/charts/manual/callout_coverage`. Verify card renders, terms can be added/removed, color changes header, snapshot save/load preserves terms list.

- [ ] **Step 3: Commit**

```
feat: add Coverage Card callout to manual chart library
```

---

### Task 5: Callout — Carrier Tile

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_callout_carrier.html`

- [ ] **Step 1: Create the template**

Reference original (lines 509-581 CSS, 719-728 state, 889-934 form, 1145-1167 render).

Template structure:
- **Display:** `.manual-chart-page-auto` → colored header (carrier name + AM Best badge + lines written), body with 2×2 stats (Premium, Participation, Retention), notes section below divider
- **Editor:** Name, rating, lines, premium, participation, retention, notes textarea, header color, card width
- **Script:** Simple — all named inputs, `refreshCurrentChart()` reads them and builds HTML.

- [ ] **Step 2: Verify in browser + commit**

```
feat: add Carrier Tile callout to manual chart library
```

---

### Task 6: Callout — Milestone Card

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_callout_milestone.html`

- [ ] **Step 1: Create the template**

Reference original (lines 442-507 CSS, 729-738 state, 936-985 form, 1169-1197 render).

Template structure:
- **Display:** `.manual-chart-page-auto` → date block (super label + large Noto Serif date), status badge (Complete/Pending/Upcoming/Overdue with color-coded backgrounds), milestone title, description, progress bar
- **Editor:** Super label, date line 1, date line 2, milestone title, description textarea, status pills (segmented), progress slider (`<input type="range">`), accent color, card width
- **Script:** Status drives badge color. Progress drives bar width.

Status color map:
```javascript
complete: { bg: '#dcfce7', color: '#15803d' }
pending:  { bg: '#fef3c7', color: '#b45309' }
upcoming: { bg: '#dbeafe', color: '#1d4ed8' }
overdue:  { bg: '#fee2e2', color: '#b91c1c' }
```

- [ ] **Step 2: Verify in browser + commit**

```
feat: add Milestone Card callout to manual chart library
```

---

### Task 7: Callout — Narrative Card

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_callout_narrative.html`

- [ ] **Step 1: Create the template**

Reference original (lines 583-628 CSS, 739-745 state, 987-1021 form, 1199-1217 render).

Template structure:
- **Display:** `.manual-chart-page-auto` → 5px accent top bar, tag badge (color-tinted background), title (Noto Serif), body text, optional callout quote (left border + tinted background)
- **Editor:** Tag, title, body textarea, callout textarea, accent color, card width
- **Script:** Simple — reads inputs, builds HTML. Callout section hidden if textarea empty.

- [ ] **Step 2: Verify in browser + commit**

```
feat: add Narrative Card callout to manual chart library
```

---

### Task 8: Timeline Builder

**Files:**
- Create: `src/policydb/web/templates/charts/manual/_tpl_timeline_builder.html`

This is the most complex template. Reference the original `timeline-builder.html` extensively.

- [ ] **Step 1: Create the display area**

`.manual-chart-page-auto` container with:
- Header: eyebrow (uppercase), title (Noto Serif), subtitle, progress bar (X of Y complete)
- Body: initially empty, populated by `refreshCurrentChart()`
- Footer: note text + status legend (4 dots with labels)

Use inline styles referencing `ManualChart.COLORS`. Container width set by `cfg-canvas-width` select.

- [ ] **Step 2: Create the editor panel**

Editor panel sections:
1. **Display toggles:** Title, Subtitle, Progress, Footer, Legend
2. **Settings section:** Eyebrow, title, subtitle, footer note inputs. Accent color swatches using `ManualChart.DATA_COLORS`. Canvas width `<select name="cfg-canvas-width">` (700/900/1100/1300). Layout mode segmented control (Horizontal | Vertical | Phases).
3. **Steps section:** Dynamic collapsible step cards. Each has: title, date, description, owner, status pills (Complete/Active/Upcoming/Overdue), phase input (shown when layout=phases). Up/down/delete buttons on header.
4. **Add Step button** + **Refresh Chart button**
5. **Hidden state:** `<textarea name="tl-state" style="display:none">` for step data serialization

- [ ] **Step 3: Implement step editor JS**

Functions needed:
- `buildStepEditor()` — renders step cards in `#steps-editor` from the steps array
- `toggleStep(id)` — expand/collapse step card body
- `updateStep(id, key, val)` — update a step field, re-render preview
- `moveStep(id, dir)` — swap step with neighbor
- `deleteStep(id)` — remove step from array
- `addStep()` — append new step with defaults, expand it, scroll to bottom

State: `let steps = [...]` array with `{id, title, date, desc, owner, status, phase}` objects. `let nextId` counter. `let openStep` tracks which card is expanded.

- [ ] **Step 4: Implement horizontal layout renderer**

```javascript
function buildHorizontal(steps, accentColor) {
  // Steps in a flex row, connector lines between dots
  // Each step: numbered dot (colored by status), label, date, desc, owner
  // Complete = green dot with ✓, Active = accent color with ring, Upcoming = gray, Overdue = red
  // Connector line: filled color for done steps, gray for upcoming
}
```

Use the CSS patterns from the original (`.tl-step-h`, `.tl-dot-h`, `.tl-label-h`, etc.) but inlined in the template since we can't reference external CSS.

- [ ] **Step 5: Implement vertical layout renderer**

```javascript
function buildVertical(steps, accentColor) {
  // Steps stacked vertically with connector line on left
  // Each step: dot on left, content block on right with title + status badge, date, desc, owner
}
```

- [ ] **Step 6: Implement phases layout renderer**

```javascript
function buildPhases(steps, accentColor) {
  // Group steps by phase field
  // Each group: phase label badge, then horizontal step row within group
  // Reuses horizontal step rendering per group
}
```

- [ ] **Step 7: Implement `refreshCurrentChart()` and state management**

```javascript
window.refreshCurrentChart = function() {
  // 1. Check if loading from snapshot (tl-state has data, no step cards)
  var stateEl = document.getElementById('tl-state');
  if (stateEl.value && !document.querySelectorAll('#steps-editor .step-card').length) {
    var saved = JSON.parse(stateEl.value);
    if (saved.steps) { steps = saved.steps; nextId = saved.nextId || steps.length + 1; }
    // Also restore layout, accentColor from saved state
  }

  // 2. Read current settings from inputs
  // 3. Serialize state to tl-state textarea
  // 4. Build step editor cards
  // 5. Render preview (header + body based on layout + footer)
  // 6. Apply display toggles
  // 7. Set container width
};
```

- [ ] **Step 8: Verify all 3 layouts in browser**

Navigate to `/charts/manual/timeline_builder`. Test:
- Default horizontal layout with 8 seed steps renders correctly
- Switching to vertical layout re-renders
- Switching to phases layout groups steps by phase
- Adding/removing/reordering steps works
- Status changes update dot colors and progress bar
- Accent color swatches change the theme
- Canvas width selector resizes the container
- Export PNG captures at the selected width
- Snapshot save/load preserves all steps + settings

- [ ] **Step 9: Commit**

```
feat: add Timeline Builder to manual chart library

Three layout modes (horizontal, vertical, phases), configurable steps
with status tracking, progress bar, and Marsh brand styling.
```

---

### Task 9: Final QA + Polish

**Files:** All created/modified files

- [ ] **Step 1: Full gallery QA**

Navigate to `/charts/manual`. Verify:
- Charts section: 9 cards with correct icons/badges
- Visual Builders section: 6 cards with correct icons/badges
- Recent Snapshots section: shows client name badges when tagged
- All 6 new links open their editors

- [ ] **Step 2: Cross-template QA**

For each of the 6 new types:
- Create content, save as snapshot with client tag
- Reload page, load snapshot, verify all fields restore
- Export PNG, verify image captures correctly
- Toggle display options, verify elements show/hide

- [ ] **Step 3: Verify existing charts unbroken**

Open 2-3 existing chart types (e.g., `freq_severity`, `quote_comparison`). Verify:
- Chart renders correctly
- Snapshot save/load still works
- Export PNG still works at all 3 sizes
- Client combobox appears but is optional

- [ ] **Step 4: Fix any issues found**

- [ ] **Step 5: Final commit**

```
chore: QA polish for visual builders integration
```
