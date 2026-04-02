---
name: policydb-design-system
description: >
  Visual Design System and UI Pattern Library for PolicyDB — color palette, typography, warm neutrals,
  active accent, Marsh Brand Guide, plus all UI interaction patterns: HTMX conventions, contenteditable
  tables, combobox, tabs, row edit, slideover, save feedback, card structure, badge system, and matrix
  components. Use this skill when implementing any frontend UI work, building templates, styling
  components, choosing input patterns, or making any visual/interaction design decisions.
---

# PolicyDB Design System & UI Patterns

## Color Theme

Professional, data-forward design built on deep navy with warm neutrals. Authoritative and analytical while approachable through warm off-whites.

---

## Core Brand Colors

| Token | HEX | Role |
|-------|-----|------|
| Midnight Blue | `#000F47` | Primary brand; headings, borders, hero backgrounds, navbars |
| Sky Blue | `#CEECFF` | Light tints; card highlights, hover states, tag backgrounds |
| White | `#FFFFFF` | Chart areas, modal backgrounds, content surfaces |

## Warm Neutral Scale

| Token | HEX | Role |
|-------|-----|------|
| Neutral 1000 | `#3D3C37` | Primary body text, data labels (prefer over pure black) |
| Neutral 750 | `#7B7974` | Secondary text, captions, metadata |
| Neutral 500 | `#B9B6B1` | Dividers, input borders, disabled states |
| Neutral 250 | `#F7F3EE` | Page background, subtotal rows, sidebar fills |

## Active Accent

`#0B4BFF` — **Blue 750**. Used strictly for interactive affordances: links, focus rings, CTA buttons, progress indicators, active nav items. Never decorative.

## Tailwind Custom Tokens (in base.html config)

```
marsh: #000F47, marsh-light: #0B4BFF
gray-50: #F7F3EE, gray-100: #F0EBE5, gray-200: #D9D5CF
gray-300: #B9B6B1, gray-400: #9A9792, gray-500: #7B7974
gray-600: #5C5B56, gray-700: #4C4B47, gray-800: #434239, gray-900: #3D3C37
```

---

## Typography

| Role | Font | Weight |
|------|------|--------|
| Display / H1-H2 | DM Serif Display | Regular (400) |
| UI / Body / Labels | DM Sans | Regular (400), Medium (500) |
| Monospace / Code | JetBrains Mono | Regular (400) |

Headings in DM Serif Display set in `#000F47`. Body in DM Sans at Neutral 1000. Never set display headings in warm neutrals.

---

## Design Principles

1. **Navy anchors, warmth softens.** Midnight Blue in structural chrome; Neutral 250 backgrounds for approachability.
2. **Accent earns attention.** Blue 750 is the only interactive signal — use consistently and exclusively.
3. **Data colors are ordinal, not decorative.** Assign by priority, not aesthetics.
4. **Borders are structural, not decorative.** Use Neutral 500 dividers only where functionally necessary.

---

## Marsh Brand Guide (Charts & Deliverables)

All charts, exports, and client-facing deliverables use official Marsh palette.

**Typography:** Noto Serif (headings), Noto Sans (body/labels)

**Data Color Order** (multi-series charts):

| Priority | Color | 1000 | 750 | 500 | 250 |
|----------|-------|------|-----|-----|-----|
| 1st | Blue | `#000F47` | — | `#82BAFF` | `#CEECFF` |
| 2nd | Green | `#2F7500` | `#6ABF30` | `#B0DC92` | `#DFECD7` |
| 3rd | Purple | `#5E017F` | `#8F20DE` | `#DEB1FF` | `#F5E8FF` |
| 4th | Gold | `#CB7E03` | `#FFBF00` | `#FFD98A` | `#FFF3DA` |

---

# UI Pattern Library

## Core UI Defaults

Standing decisions — do not deviate without explicit user approval.

| Decision | Default | Notes |
|----------|---------|-------|
| Page layout | Tabbed (4 tabs), lazy-loaded via HTMX | Client + policy pages both use tabs |
| Tab loading | Lazy-load each tab on first click | Active tab loads on render; others on demand |
| Tab persistence | sessionStorage remembers last tab per page | `initTabs(containerId, storageKey)` |
| Save behavior | Per-field PATCH on blur — no Save button | Every field saves individually |
| Field style | Contenteditable + combobox everywhere | Never `<input>` in table cells |
| Form sections | All open by default | No collapsed `<details>` |
| Sidebar | Sticky right sidebar on client page | Key Dates + Quick Actions |
| Working Notes | Floating panel accessible from any tab | Not locked to one tab |
| Contacts | Editable inline (matrix pattern) | Full add/edit/remove |

---

## Input Pattern Hierarchy

| Field Type | Use | Avoid |
|---|---|---|
| Freeform text in tables | `contenteditable` cell | `<input>` inside `<td>` |
| Single-field edits | Click-to-edit (display -> input on click) | Always-visible input |
| Carrier, industry, LOB | Combobox with filtered dropdown | `<select>` dropdown |
| Multiple values (tags) | Pill/tag input (Enter to add, x to remove) | Multi-select `<select>` |
| Boolean flags | CSS toggle switch | `<input type="checkbox">` |
| 2-5 mutually exclusive options | Segmented control (pill button group) | `<select>` or radio |
| Dates | `<input type="date">` styled to match UI | Plain text input |
| Limits, retentions | Stepper with +/- buttons | Plain `<input type="number">` |
| Row ordering | Drag-to-reorder with handle | Manual order fields |

---

## Tab Component

**CSS:** `.tab-bar` (flex, border-bottom), `.tab-btn` (13px, Neutral 750), `.tab-btn.active` (Midnight Blue, bold, bottom border), `.tab-badge` (pill counter)

**JS:** `window.initTabs(containerId, storageKey)` — manages tab switching, lazy loading via `data-tab-url`, sessionStorage persistence.

```html
<div class="tab-bar">
  <button class="tab-btn active" data-tab="overview"
    data-tab-url="/clients/{{ client.id }}/tab/overview">Overview</button>
  <button class="tab-btn" data-tab="policies"
    data-tab-url="/clients/{{ client.id }}/tab/policies">Policies</button>
</div>
<script>initTabs('client-tabs', 'client-tab-{{ client.id }}');</script>
```

---

## Contenteditable Tables

Cells appear static; editable on click. Part of the matrix system (`initMatrix()`).

**Structure:**
```html
<div contenteditable="true"
     class="matrix-cell-editable outline-none text-gray-700 rounded px-1 -mx-1 leading-relaxed"
     data-field="description"
     data-placeholder="Add description...">{{ value or '' }}</div>
```

**Behavior:**
- Focused cell gets bottom border highlight in brand color (`border-bottom: 2px solid #000F47`)
- `Tab` advances to next cell; `Tab` on last cell appends a blank row
- Empty cells show placeholder via `data-placeholder` + `::before` CSS
- Save on `blur` via PATCH. New rows POST and store returned `id` as `data-id`
- Paste only allows plaintext (execCommand insertText)
- `+ Add row` button below table, hidden in `@media print`

---

## Combobox Pattern

Click-to-open filtered dropdown for config-driven fields. Part of the matrix system.

**Structure:**
```html
<td class="matrix-cell-combo px-3 py-2.5 cursor-pointer relative"
    data-field="role"
    data-options='{{ contact_roles | tojson }}'>
  <span class="cell-display">
    <span class="text-xs text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">{{ value }}</span>
  </span>
</td>
```

**Behavior:**
- Click opens input + dropdown list
- Type-ahead filters (case-insensitive substring)
- Arrow keys navigate; Enter/Tab select; Escape closes
- On save: PATCH to `cfg.patchUrl(rowId)` with `{field, value}`
- Max dropdown height: 180px; hover highlight: `bg-F0EBE5`

**Jinja2 rule:** Use single-quote delimiters: `data-options='{{ items | tojson }}'`. Never use `| e` with `tojson` inside double-quoted attributes.

---

## Matrix System

`window.initMatrix(config)` — unified inline editing for tables with three cell types:

| Cell Type | Class | Use |
|-----------|-------|-----|
| Contenteditable | `.matrix-cell-editable` | Freeform text (description, notes) |
| Combobox | `.matrix-cell-combo` | Config-driven dropdowns (role, severity, carrier) |
| Email | `.matrix-cell-email` | Email with validation + mailto link |

**Config:**
```javascript
initMatrix({
  tableId: 'contacts-table',
  patchUrl: (id) => `/contacts/${id}/cell`,
  newRowUrl: '/contacts/new',
  deleteUrl: (id) => `/contacts/${id}`,
});
```

---

## HTMX Row Edit Pattern

Three endpoint variants per row (every pipeline/table view):

```
GET  /{uid}/row/edit  → inline edit form (replaces #row-{uid})
POST /{uid}/row/edit  → saves, returns display row
GET  /{uid}/row       → restore display row (Cancel)
GET  /{uid}/row/log   → inline activity log form
POST /{uid}/row/log   → saves, restores display row
```

**Display row:**
```html
<tr id="row-{{ uid }}">
  <td>...</td>
  <td><button hx-get="/policies/{{ uid }}/row/edit"
              hx-target="#row-{{ uid }}" hx-swap="outerHTML">Edit</button></td>
</tr>
```

**Edit row:** Blue highlight (`bg-blue-50 border-t-2 border-marsh`), Cancel button targets original row GET.

---

## Slideover Panel (Preferred Pattern for Record Access)

**Slideover is the default pattern for accessing and editing records from list/table views.** Use slideover instead of full-page navigation or new tabs. Full pages are reserved for views with 4+ tabs or deep nested navigation (e.g., full client detail page).

Right-aligned fixed panel for detail/edit views. Shared container in `base.html`, content swapped via HTMX.

### Shell (in base.html)
```html
<div id="fu-edit-backdrop" class="fixed inset-0 bg-black/30 z-40 hidden" onclick="closeFollowupEdit()"></div>
<div id="fu-edit-panel" class="fixed top-0 right-0 bottom-0 w-[480px] max-sm:w-full z-50 hidden flex-col bg-white shadow-xl">
  <div id="fu-edit-content"></div>
</div>
```

480px wide desktop, full-width mobile. Backdrop z-40, panel z-50. `openFollowupEdit()` / `closeFollowupEdit()` toggle visibility + body scroll lock.

### Trigger Button Pattern
```html
<button type="button"
  hx-get="/activities/{{ item.id }}/edit-slideover"
  hx-target="#fu-edit-content" hx-swap="innerHTML"
  onclick="openFollowupEdit()"
  class="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-1.5 rounded hover:border-gray-300 hover:text-marsh transition-colors"
  title="Edit">&#9998;</button>
```

### Partial Template Structure
Each slideover is a standalone partial with header, fields, and self-contained JS:
```html
{# Header with close button #}
<div class="flex items-start justify-between px-5 py-4 border-b border-gray-200 flex-shrink-0">
  <div>
    <h2 class="text-sm font-semibold text-gray-900">Edit [Type]</h2>
    <p class="text-xs text-gray-500 mt-0.5">Context info</p>
  </div>
  <button type="button" onclick="closeFollowupEdit()" class="text-gray-400 hover:text-gray-600">X</button>
</div>
{# Fields — per-field save on blur via PATCH #}
<div class="flex-1 overflow-y-auto px-5 py-4 space-y-5">
  {# Date with quick-add buttons #}
  {# Pill buttons for status/severity (toggle active class on click) #}
  {# Text inputs and textareas with onblur save #}
</div>
<script>/* patchField() function — PATCH JSON, green flash on success */</script>
```

### Styling Rules
- Labels: `text-[10px] font-medium text-gray-500 uppercase tracking-wide`
- Inputs: `text-sm border border-gray-300 rounded px-2 py-1.5 focus:ring-1 focus:ring-marsh focus:border-marsh`
- Quick-date buttons: `text-[10px] border border-gray-200 rounded px-1.5 py-0.5 text-gray-400 hover:text-marsh`
- Pill buttons (active): `bg-marsh text-white border-marsh`
- Pill buttons (inactive): `border-gray-200 text-gray-500 hover:border-marsh hover:text-marsh`
- Green flash: `backgroundColor = '#ecfdf5'` for 800ms

### Existing Slideovers
| Endpoint | Source | Fields |
|----------|--------|--------|
| `GET /activities/{id}/edit-slideover` | activity_log | follow-up date, subject, disposition, type, contact, notes, hours, activity date |
| `GET /policies/{uid}/edit-followup-slideover` | policies | follow-up date, renewal status |
| `GET /clients/{id}/edit-followup-slideover` | clients | follow-up date, notes |
| `GET /issues/{id}/edit-slideover` | activity_log (issues) | due date, severity, status, subject, details |

### Where Slideover is Used
- **Action Center Follow-ups tab:** pencil on activity, project, policy, client, issue rows (not milestones)
- **Action Center Activities tab:** pencil on each table row
- **Action Center Issues tab:** pencil on list rows and board cards

### When to Add a New Slideover
Use this pattern when a record needs lightweight inline editing from a list/table context without full-page navigation. Create a GET endpoint returning the partial, a PATCH endpoint for field saves, and wire a pencil button (`&#9998;`) with `hx-get` targeting `#fu-edit-content`.

---

## Save Feedback

**Flash Cell** — green highlight on auto-format:
```javascript
function flashCell(el) {
  el.style.transition = 'background-color 0.3s ease';
  el.style.backgroundColor = '#d1fae5';  // green-200
  setTimeout(() => { el.style.backgroundColor = ''; }, 800);
}
```

**PATCH response contract:** All cell-save endpoints return `{"ok": true, "formatted": "..."}`. JS updates cell and calls `flashCell()` when formatted value differs from raw input.

**Toast:** `showToast('Saved', true)` for success confirmation.

---

## Card & Section Structure

```css
.card { background: #fff; border-radius: 0.75rem; border: 1px solid #B9B6B1; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
.card-header { padding: 0.75rem 1.25rem; border-bottom: 1px solid #D9D5CF; background: #F7F3EE; }
.section-label { font-size: 0.6875rem; font-weight: 600; color: #7B7974; text-transform: uppercase; letter-spacing: 0.06em; border-left: 3px solid #000F47; padding-left: 0.5rem; }
```

**Standard card with header:**
```html
<div class="card">
  <div class="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
    <h2 class="font-semibold text-gray-900">Section Title</h2>
    <button class="text-xs bg-marsh text-white rounded px-3 py-1.5">+ New</button>
  </div>
  <div class="divide-y divide-gray-50"><!-- rows --></div>
</div>
```

---

## Badge System

**Renewal Status** (select dropdown, auto-saves via HTMX):
```
Not Started: bg-gray-100 text-gray-600 border-gray-200
In Progress: bg-blue-50 text-blue-700 border-blue-200
Pending Bind: bg-amber-50 text-amber-700 border-amber-200
Bound: bg-green-50 text-green-700 border-green-200
```

**Severity:**
```
Critical: bg-red-100 text-red-700
High: bg-amber-100 text-amber-700
Normal: bg-blue-100 text-blue-700
Low: bg-gray-100 text-gray-600
```

**Context Badges (cross-references):**
```html
<span class="text-[10px] bg-purple-100 text-purple-600 font-medium px-1.5 py-0.5 rounded">📍 {{ location }}</span>
<span class="text-[10px] bg-amber-100 text-amber-700 font-medium px-1.5 py-0.5 rounded">📄 via {{ issue_uid }}</span>
```

**Activity type:** `text-xs font-medium bg-gray-100 text-gray-600 px-2 py-0.5 rounded`

---

## Key JavaScript Functions

| Function | Purpose |
|----------|---------|
| `initTabs(containerId, storageKey)` | Tab component with session persistence + lazy load |
| `initMatrix(config)` | Matrix table with contenteditable, combobox, email cells |
| `flashCell(el)` | Green flash feedback on auto-format |
| `confirmAction(btn, msg)` | Two-click confirmation (no `alert()`) |
| `showToast(msg, success)` | Toast notification |
| `initMarkdownEditor(id, opts)` | Toast UI markdown editor |
| `renderMarkdown(selector)` | Markdown viewer rendering |
| `copyRefTag()` | Copy `[PDB:...]` ref tag to clipboard |

---

## Confirmation & Error Feedback Patterns

**Destructive actions** — use `confirmAction()` (two-click pattern), NEVER `confirm()`:
```html
<button onclick="confirmAction(this, 'Delete this record?')"
  data-href="/items/{{ id }}/delete" data-method="DELETE">Delete</button>
```

**Validation errors** — use `showToast()` or inline red border + message, NEVER `alert()`:
```javascript
// Success feedback
showToast('Saved', true);

// Error feedback
showToast('Failed to save — check required fields', false);

// Inline error (preferred for field validation)
el.classList.add('border-red-500');
el.nextElementSibling.textContent = 'Required field';
```

**Error logging** — always pair `console.error()` with visible user feedback:
```javascript
// WRONG
console.error('Save failed', err);

// CORRECT
console.error('Save failed', err);
showToast('Save failed — please try again', false);
```

---

## Color Migration Note

**`#003865` is the OLD Marsh navy.** The current design system primary brand is `#000F47` (Midnight Blue), mapped to the Tailwind `marsh` token. Always use `bg-marsh`, `text-marsh`, `border-marsh` instead of hardcoded `#003865` or `[#003865]`. There are ~182 legacy occurrences of `#003865` in templates that should be migrated to the `marsh` token as files are touched.

---

## Anti-Patterns (Never Do)

- No `<input>` inside `<td>` — use contenteditable
- No `<select>` where user might type — use combobox
- No raw `<input type="checkbox">` — use toggle switch
- No `alert()` or `confirm()` — use `showToast()` or `confirmAction()`
- No `console.error()` as only error feedback — show red border + inline message or toast
- No hardcoded `#003865` — use Tailwind `marsh` token (`#000F47`)
- No Save buttons on auto-save pages
- No collapsed `<details>` sections on detail pages
- No `| e` with `tojson` in double-quoted HTML attributes
- No `target="_blank"` for same-app navigation — only for mailto, exports, PDFs
- No raw `${{ "{:,.0f}".format(value) }}` — use `{{ value | currency }}` filter
