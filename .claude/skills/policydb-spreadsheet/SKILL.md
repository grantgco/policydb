---
name: policydb-spreadsheet
description: >
  Tabulator 6.3 spreadsheet component reference for PolicyDB. Use when building or modifying
  spreadsheet/grid views, configuring initSpreadsheet(), adding new spreadsheet pages, or
  troubleshooting Tabulator theming/behavior. Covers the API, cell save pattern, theming,
  and when to use Tabulator vs initMatrix().
---

# Spreadsheet Component (Tabulator)

Reusable spreadsheet/grid component for large editable data tables, built on **Tabulator 6.3** (CDN).

## Architecture

| File | Purpose |
|------|---------|
| `_spreadsheet.html` | Shared partial: Tabulator CDN includes, Marsh CSS overrides, `initSpreadsheet(config)` JS function |
| `policies/spreadsheet.html` | Policy spreadsheet wrapper (extends base.html, includes `_spreadsheet.html`) |
| (Future) `clients/spreadsheet.html` | Client spreadsheet wrapper |
| (Future) `followups/spreadsheet.html` | Follow-up spreadsheet wrapper |

## `initSpreadsheet(config)` API

```javascript
initSpreadsheet({
    el: "#spreadsheet-grid",         // container selector
    data: [...],                      // row objects
    columns: [...],                   // Tabulator column definitions
    frozenFields: ["client_name"],    // fields to freeze on left
    patchUrl: "/policies/{uid}/cell", // URL template for cell save
    idField: "policy_uid",            // row field for PATCH URL
    entityName: "policy",             // for UI labels
    addRowUrl: "/policies/quick-add", // POST endpoint for new rows (null to disable)
    exportUrl: "/policies/spreadsheet/export", // branded XLSX export
    projectsByClient: {...},          // client-scoped dropdown data
})
```

## When to Use

For any **large data table** or **bulk-editing UI** with 50+ rows, use this Tabulator component instead of building a custom contenteditable table. Benefits: virtual scrolling, column resize, built-in sort/filter/header filters, keyboard navigation.

For **small tables** (< 20 rows) within detail pages, continue using the existing `initMatrix()` contenteditable pattern — it's lighter and doesn't need a CDN dependency.

## Cell Save Pattern

`cellEdited` → `fetch(PATCH, {field, value})` → on success update cell with `formatted` value + green flash → on error restore old value + red flash. Same backend endpoints as existing inline editing.

## Theming

Tabulator's default dark gray theme is overridden in `_spreadsheet.html` to match Marsh brand: navy `#003865` headers, `#F7F3EE` alt-rows, `#B9B6B1` borders, Noto Sans font, `#CEECFF` selected rows.
