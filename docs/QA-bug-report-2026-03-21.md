# QA Bug Report — Full System Walkthrough (2026-03-21)

## Testing Approach
Systematically navigated every page as an account executive: created a client, added contacts, reviewed policies, tested compliance review, checked settings, follow-ups, renewals, templates, inbox, and contacts directory.

---

## CRITICAL BUGS

### BUG-001: Policy Edit — Form Fields Buried Below Activity Log
**Page:** `/policies/{uid}/edit`
**Severity:** High — core workflow broken
**Description:** The "Edit Policy" page title suggests you can edit policy details, but the actual form fields (Line of Business, Carrier, dates, limits, deductible, etc.) are buried in collapsible `<details>` sections (▶ Core Fields, ▶ Placement & Renewal, etc.) BELOW the Contacts, Activity Log, and Working Notes sections. An AE arriving at this page would not know the fields exist without scrolling past all the display content.
**Expected:** Editable form fields should be at the top of the page, immediately after the header. Activity log and contacts should be below the form.
**Fix:** Reorder the template sections — move the `<details>` form sections (Core Fields, Placement & Renewal, Description, Program Structure, Exposure, Internal) above the Contacts and Activity Log sections.

---

## MODERATE BUGS

### BUG-002: Contacts Page — Email Column Overflow
**Page:** `/contacts`
**Severity:** Medium — visual
**Description:** Long email addresses (e.g., `dsharp@coalitioninc.com`) overflow their column and overlap into the Mobile column. The `table-fixed` layout with `colgroup` percentages doesn't account for long emails.
**Fix:** Add `truncate` class or `overflow-hidden text-ellipsis` to the email `<td>`, or widen the email column.

### BUG-003: Compliance Review — "Unlinked" Source Name for Orphaned Requirements
**Page:** `/compliance/client/{id}` — location detail drill-down
**Severity:** Medium — confusing UX
**Description:** Requirements whose source was deleted show "Unlinked" as the source group header with an "↓ Inherited" badge. This is confusing — the user doesn't know what "Unlinked" means or where these requirements came from.
**Fix:** Either auto-delete orphaned requirements when source is deleted (cascade already implemented), or show a warning: "Source deleted — reassign or remove these requirements." The cascade was added but existing orphaned data from before the fix still shows.

### BUG-004: Compliance Matrix — Location List Shows "0%" with Red Dot for All Locations
**Page:** `/compliance/client/{id}` — location list view (>6 locations)
**Severity:** Low-Medium — misleading
**Description:** Every location shows a red dot on the compliance score circle even when it has 0 requirements. The red dot comes from the SVG rendering a tiny arc at 0%. Should either show no arc/dot or show gray "No requirements" state.
**Fix:** In `_matrix.html`, don't render the colored arc when `pct == 0` or `s.total == 0`.

### BUG-005: Review Mode — Combobox Dropdown Still Spans Full Width in Some Cases
**Page:** `/compliance/client/{id}` — Review Mode table
**Severity:** Medium — usability
**Description:** The combobox dropdown for Coverage Line and Ded Type sometimes renders wider than the cell despite the `relative` class fix. This may be because `initMatrix()` opens the combobox at the `<td>` level which has `position: relative`, but the dropdown CSS uses `left: 0; right: 0` which constrains to the parent's width — but if the parent is narrow, the dropdown text wraps awkwardly.
**Fix:** Set a `min-width: 200px` on `.matrix-combo-dropdown` or position it relative to viewport.

### BUG-006: Dashboard — Est. Revenue Shows "—" for All Clients
**Page:** `/` (Dashboard)
**Severity:** Low-Medium — missing feature or data
**Description:** The "EST. REVENUE" summary card shows "—" with "comm + fees" subtitle. No revenue is computed or displayed. This field appears to never be populated.
**Fix:** Either implement revenue calculation (commission_rate × premium + broker_fee) or remove the card if not ready.

---

## MINOR BUGS / POLISH

### BUG-007: New Client Form — Preferred Contact Method "—" Pill Selected by Default
**Page:** `/clients/new`
**Severity:** Low — cosmetic
**Description:** The Preferred Contact Method starts with "—" selected (dark pill). The default should probably be "Email" or no selection.

### BUG-008: Client Detail — "Client Follow-Up" Date Input Not Styled
**Page:** `/clients/{id}` — right sidebar
**Severity:** Low — cosmetic
**Description:** The "Client Follow-Up" date input shows as `mm/dd/yyyy` with a raw browser date picker. It's unstyled compared to other date inputs on the page.

### BUG-009: Dashboard — Scratchpad Empty with Full Toolbar
**Page:** `/`
**Severity:** Low — design
**Description:** The scratchpad WYSIWYG editor shows a full toolbar (H, B, I, S, lists, table, link, code) even when empty. The toolbar takes up significant vertical space. Consider collapsing it or making it appear on focus.

### BUG-010: Policy Edit — "Attachment Point ($)" Shows "5" for a GL Policy
**Page:** `/policies/POL-004/edit` — Program Structure section
**Severity:** Low — data quality
**Description:** The Attachment Point field shows "5" which is likely test data ($5 attachment point makes no sense for GL). Not a code bug but worth noting as data cleanup.

### BUG-011: Compliance — Add Requirement Form Uses `<select>` for Coverage Line
**Page:** `/compliance/client/{id}` — Add Requirement section
**Severity:** Low — inconsistent with UI standards
**Description:** CLAUDE.md says "No `<select>` elements for fields where the user might type — use a combobox instead." The Add Requirement form uses a plain `<select>` for Coverage Line. Should be a combobox with filtering.

### BUG-012: Contacts Directory — "New Person" Entries in Data
**Page:** `/contacts`
**Severity:** Low — data hygiene
**Description:** Multiple contacts named "New Person" appear in the directory — these are blank rows created by `initMatrix()` add-row but never filled in. Consider auto-cleaning on page load or preventing save of "New Person" default.

### BUG-013: Compliance — Location Detail Endorsements Not Displaying as Pills
**Page:** `/compliance/client/{id}` — location detail requirements
**Severity:** Low — missing feature
**Description:** In the location detail drill-down, required endorsements for individual requirements are not shown. The matrix view shows endorsement pills, but the drill-down per-source table doesn't display them per requirement row.

### BUG-014: Review Mode — No Visual Feedback on Cell Save
**Page:** `/compliance/client/{id}` — Review Mode
**Severity:** Low — UX
**Description:** When editing a cell in Review Mode and tabbing away, there's no toast or flash to confirm the save succeeded. The `initMatrix()` pattern should call `flashCell()` or `showToast()` on successful save.

### BUG-015: Compliance Summary Banner — "0% Compliant" Donut is Gray, Hard to Distinguish
**Page:** `/compliance/client/{id}`
**Severity:** Low — cosmetic
**Description:** When compliance is 0% or very low, the donut chart ring is barely visible (thin gray line). Consider showing the empty ring more prominently or adding a "not started" state.

---

## FEATURE GAPS (Not Bugs)

### GAP-001: No Way to Add a Policy from the Client Detail Page
The client detail page has no "+ Add Policy" button. You have to navigate away to create a policy and link it back. Consider adding a quick-add button.

### GAP-002: Export XLSX/PDF Buttons on Compliance Page Not Implemented
The "Export XLSX" and "Export PDF" buttons exist but the routes aren't built yet. They'll 404.

### GAP-003: Compliance — No Way to Link a Policy to a Requirement from the Matrix
The matrix shows gaps and policies but there's no quick way to link a policy to a requirement. Must use the inline edit form.

### GAP-004: Risk Profile — "→ Create Compliance Requirements" Button Not Tested
The spawn button was added but not tested in browser during this QA (it was verified via API).

---

## PAGES TESTED

| Page | Status | Notes |
|------|--------|-------|
| Dashboard | ✅ | Working, see BUG-006, BUG-009 |
| Clients list | ✅ | Clean, filters/segments work |
| New Client | ✅ | Form works, see BUG-007 |
| Client detail | ✅ | All sections render, see BUG-008 |
| Contacts (matrix) | ✅ | Add/edit/tab-through works |
| External Stakeholders | ✅ | Fixed today |
| Internal Team | ✅ | Renders correctly |
| Policy edit | ⚠️ | Fields buried, see BUG-001 |
| Renewals pipeline | ✅ | Filters, status pills, checklist |
| Follow-ups | ✅ | Overdue/upcoming, actions |
| Contacts directory | ✅ | See BUG-002, BUG-012 |
| Settings | ✅ | All lists visible including compliance |
| Templates | ✅ | Renders correctly |
| Inbox | ✅ | 2 pending, scratchpads |
| Compliance review | ✅ | Matrix, drill-down, COPE, review mode |
| Search | Not tested | |
| Reconcile | Not tested | |
