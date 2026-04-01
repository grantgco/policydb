# Team Chart — Design Spec

**Date:** 2026-04-01
**Status:** Approved

## Context

PolicyDB tracks internal team members assigned to each client via `contact_client_assignments` (contact_type='internal'). Currently there's no way to visualize the team structure in a presentation-quality format for client deliverables or renewal decks.

The `assignment` field on internal contacts is freeform text and underutilized. Making it a combobox from a config list enables reliable grouping in the chart.

**Goal:** A manual chart template that auto-populates from client team data, supports two layout modes (grid and grouped), and can be polished and snapshotted for presentations.

---

## Part 1: Assignment Field Upgrade

### Changes

1. **New config list** `team_assignments` in `config.py` `_DEFAULTS`:
   - Seed values: `["Account Management", "Placement/Broking", "Claims", "Analytics", "Risk Engineering", "Administration"]`
   - Add to `EDITABLE_LISTS` in `settings.py` so it appears in Settings UI

2. **Combobox upgrade** in `_team_contacts.html`:
   - Change the Assignment column from plain contenteditable to a combobox
   - Data source: `team_assignments` config list
   - Custom input allowed (combobox pattern, not strict select)
   - Existing freeform values continue to work

### Files Modified
- `src/policydb/config.py` — add `team_assignments` to `_DEFAULTS`
- `src/policydb/web/routes/settings.py` — add `team_assignments` to `EDITABLE_LISTS`
- `src/policydb/web/templates/clients/_team_contacts.html` — assignment column → combobox
- `src/policydb/web/routes/clients.py` — pass `team_assignments` to template context

---

## Part 2: Manual Chart Template — `team_chart`

### Registry Entry

Add to `MANUAL_CHART_REGISTRY` in `src/policydb/web/routes/charts.py`:

```python
{"id": "team_chart", "title": "Team Chart",
 "description": "Org chart showing internal team members grouped by assignment with contact details.",
 "category": "builder", "icon": "team"}
```

### Layout Modes

Two modes via segmented control (like timeline builder):

**Grid Layout:**
- 2-3 column responsive card grid (CSS grid, `grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))`)
- All members displayed as equal cards
- Assignment shown as small badge on each card
- Best for small teams (3-6 people)

**Grouped Layout:**
- Cards organized under section headers by assignment value
- Section header: assignment name in accent-colored bar (same pattern as timeline phases)
- Cards within each group in a horizontal flex row, wrapping
- Best for larger teams or when functional areas matter

### Card Design

Each team member card:
- **Header area:** Name in Noto Serif 16px bold, midnight color
- **Subtitle:** Title + Role (e.g., "Senior Vice President — Account Executive") in 12px neutral750
- **Assignment badge** (Grid layout only): small pill in accent color tint, 10px uppercase
- **Contact strip:** Phone icon + number, Email icon + address — 11px, neutral750. Only shown if "Show Contact Info" toggle is on.
- **Notes:** Optional, 11px italic, neutral750. Only shown if "Show Notes" toggle is on.

**Card styling:**
- White background, 1px border neutral500, border-radius 4px, subtle shadow
- Padding: 16px 20px
- Hover state in browser (not exported): tooltip with full contact details (phone, mobile, email, notes)

**Tooltip on hover:**
- Shows all contact fields including mobile
- CSS-only tooltip (no JS library) — positioned above/below card
- Not rendered in PNG export (stripped by prepareClone)

### Export

- `.manual-chart-page-auto` (variable height)
- Auto-height export at natural dimensions, 2x scale
- Tooltips stripped during export (`.no-print` class)
- All export-safe CSS rules apply: px units, hex colors, no CSS variables, explicit font-family

### Color System

- Accent color selector (swatches from ManualChart.COLORS)
- Group headers: accent color background, white text
- Card borders: neutral500
- Card background: white
- Text: midnight (name), neutral750 (subtitle, contact, notes)

---

## Part 3: Editor Panel

### Structure

```
Display Toggles:
  [x] Show Title
  [x] Show Subtitle
  [x] Show Contact Info
  [ ] Show Notes

Settings:
  Title:     [Your Team          ]
  Subtitle:  [                   ]
  Layout:    [Grid] [Grouped]
  Accent:    [color swatches]
  Width:     [700 / 900 / 1100]

Load Team:
  [Load from Client] button (next to snapshot bar client combobox)

Team Members:
  ┌─ [1] Jane Smith — Account Management  [↑][↓][×] ─┐
  │  Name:       [Jane Smith              ]           │
  │  Title:      [Senior Vice President   ]           │
  │  Role:       [Account Executive  ▾    ]           │
  │  Assignment: [Account Management ▾    ]           │
  │  Phone:      [212-555-0100            ]           │
  │  Email:      [jane.smith@marsh.com    ]           │
  │  Notes:      [Lead AE, 15yr relationship]         │
  └───────────────────────────────────────────────────┘
  ┌─ [2] Bob Lee — Claims                [↑][↓][×] ─┐
  │  (collapsed)                                      │
  └───────────────────────────────────────────────────┘
  [+ Add Member]
```

### Dynamic State Management

Following timeline builder pattern:
- JavaScript `members` array holds all team member objects
- Hidden `<textarea id="team-state">` serializes `{ members: [...], nextId: N }`
- `serializeState()` called after every member modification
- `deserializeState()` called in `refreshCurrentChart()` when loading snapshots
- `ManualChart.collectAll()` picks up the hidden textarea automatically

### Member Object Schema

```javascript
{
  id: 1,
  name: "Jane Smith",
  title: "Senior Vice President",
  role: "Account Executive",
  assignment: "Account Management",
  phone: "212-555-0100",
  email: "jane.smith@marsh.com",
  mobile: "917-555-0200",
  notes: "Lead AE, 15yr relationship"
}
```

### Editor Interactions

- **Collapsible cards:** Click header to expand/collapse body
- **Role combobox:** Populated from `contact_roles` config list (same as client contacts screen)
- **Assignment combobox:** Populated from `team_assignments` config list
- **Reorder:** Up/down arrows reorder `members` array, rebuild editor + canvas
- **Delete:** Remove from array with confirmation, rebuild
- **Field changes:** Update member object on input, serialize, rebuild canvas
- **Add Member:** Appends blank member to array, scrolls to new card, opens it

---

## Part 4: Load from Client API

### New Endpoint

`GET /charts/api/team/{client_id}` in `src/policydb/web/routes/charts.py`

**Query:**
```sql
SELECT c.name, c.email, c.phone, c.mobile,
       ca.title, ca.role, ca.assignment, ca.notes
FROM contact_client_assignments ca
JOIN contacts c ON c.id = ca.contact_id
WHERE ca.client_id = ? AND ca.contact_type = 'internal'
ORDER BY ca.assignment, c.name
```

**Response:** JSON array of member objects matching the editor schema.

### Load Flow

1. User selects client in snapshot bar combobox (already exists)
2. User clicks "Load Team" button
3. If members already exist in editor: confirm "Replace current team with data from {client name}?"
4. Fetch `GET /charts/api/team/{client_id}`
5. Populate `members` array from response
6. Call `serializeState()` + `refreshCurrentChart()`
7. Toast: "Loaded {N} team members from {client name}"

---

## Part 5: Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/policydb/config.py` | Edit | Add `team_assignments` to `_DEFAULTS` |
| `src/policydb/web/routes/settings.py` | Edit | Add `team_assignments` to `EDITABLE_LISTS` |
| `src/policydb/web/templates/clients/_team_contacts.html` | Edit | Assignment column → combobox |
| `src/policydb/web/routes/clients.py` | Edit | Pass `team_assignments` to contacts tab context |
| `src/policydb/web/routes/charts.py` | Edit | Add registry entry + Load Team endpoint |
| `src/policydb/web/templates/charts/manual/_tpl_team_chart.html` | Create | New template file |

**No migrations needed** — no schema changes, just config and UI.

---

## Verification

1. **Settings:** Navigate to `/settings`, verify `team_assignments` list appears and is editable
2. **Client contacts:** Open a client → Contacts tab → Internal Team. Verify assignment column is now a combobox with config values
3. **Chart gallery:** Navigate to `/charts/manual`, verify "Team Chart" appears in Visual Builders section
4. **Chart editor:** Open Team Chart editor, manually add 3-4 members with different assignments
5. **Grid layout:** Verify cards render in 2-3 column grid with badges
6. **Grouped layout:** Toggle to Grouped, verify members grouped under assignment headers
7. **Tooltips:** Hover a card, verify tooltip shows full contact details
8. **Load from Client:** Select a client with internal team contacts, click "Load Team", verify members populate
9. **Snapshot:** Save snapshot, reload page, load snapshot, verify all members restore correctly
10. **PNG export:** Export as PNG, verify clean output with no tooltip artifacts, proper fonts, no CSS variable issues
