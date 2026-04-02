# Knowledge Base Reference Web — Design Spec

**Date:** 2026-04-02
**Status:** Approved

## Overview

Redesign the Knowledge Base to present all content types (articles, local files, DEVONthink links) in a unified list with clear visual identity per type, expand the filter system, introduce bi-directional linking across all entity types, and add backlinks panels everywhere. A network graph explorer comes as a future phase.

## Goals

1. One unified KB list where articles, local files, and DEVONthink links are visually distinct at a glance
2. Rich filtering and sorting so the user can quickly find what they need
3. Bi-directional linking between KB items and any entity (issues, policies, clients, activities, projects, other KB items)
4. Backlinks panels on every detail page — the daily workhorse for navigating the reference web
5. (Phase 2) Network graph explorer as a "Map" tab on the KB index

## Content Types & Visual Identity

All three content types share the same card layout (Style B — left border + source line):

### Articles
- **Left border:** Blue `#0B4BFF`
- **Icon:** Pen/edit icon (blue)
- **UID format:** `KB-NNN`
- **Source line:** Content preview (first ~150 chars of article body)

### Local Files
- **Left border:** File-type color (red `#ef4444` for PDF, blue `#3b82f6` for Word, green `#22c55e` for Excel, orange `#f59e0b` for PowerPoint)
- **Icon:** Filled document icon in file-type color
- **UID format:** `ATT-NNN`
- **Source line:** File icon + filename, file size shown on the right

### DEVONthink Links
- **Left border:** Purple `#8b5cf6`
- **Icon:** Link chain icon (purple)
- **UID format:** `ATT-NNN`
- **Source line:** Purple "DEVONthink" label + filename
- **Click behavior:** Opens `x-devonthink-item://` URL (navigates to DEVONthink)

### Common Card Elements
- UID in monospace pill (neutral gray background)
- Title (semibold, primary text color)
- Category badge (colored pill, right-aligned)
- Date (right-aligned, gray)
- Link count badge when item has references (e.g., "3 links")
- Tags row below (existing behavior, unchanged)

## Expanded Filter Bar

### Source Toggle (replaces current type filter)
- **All** | **Articles** | **Local Files** | **DEVONthink**
- Styled as toggle buttons (existing `btn-filter` pattern)
- Maps to query param `source_filter`

### Category Pills (existing, unchanged)
- Configured via `kb_categories` in config
- Click to filter, click "All" to clear

### Linked-to Filter (new)
- Combobox input that searches across all linkable entity types (clients, policies, issues, activities, projects, KB items)
- Selecting an entity filters the KB list to only items linked to that entity
- Shows entity type + UID + name in dropdown results
- Clear button to remove the filter
- Maps to query params `linked_type` + `linked_id`

### Sort Control (new)
- Dropdown or segmented control
- Options: Recently Updated (default), Title A-Z, Most Linked, Category
- Maps to query param `sort`

### Filter Interaction
- All filters combine (AND logic) — selecting "DEVONthink" + "Carrier Intel" shows only DEVONthink links in that category
- All filters trigger via `htmx.ajax()` to `/kb/search` (using the fixed pattern from the filter bug fix)
- Filter state preserved in hidden inputs within `#kb-filters`

## Bi-directional Linking

### Data Model

Use a single unified linking table (new migration):

```sql
CREATE TABLE IF NOT EXISTS kb_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,   -- 'kb_article', 'attachment', 'issue', 'policy', 'client', 'activity', 'project'
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,   -- same enum
    target_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_type, source_id, target_type, target_id)
);
```

**Bi-directionality:** Each link is stored once. Queries check both directions:
```sql
-- Find all items linked to KB article 5
SELECT * FROM kb_links
WHERE (source_type = 'kb_article' AND source_id = 5)
   OR (target_type = 'kb_article' AND target_id = 5)
```

**Migration from existing tables:**
- Migrate `kb_record_links` rows into `kb_links`
- Migrate relevant `record_attachments` rows (where `record_type = 'kb_article'`) into `kb_links`
- Keep old tables for backward compatibility during transition

### Linkable Entity Types
- `kb_article` — Knowledge Base articles
- `attachment` — Local files and DEVONthink links
- `issue` — Issue tracker items
- `policy` — Policies
- `client` — Clients
- `activity` — Activities and follow-ups
- `project` — Projects/locations

### Linking UI — From KB Detail Page

On the KB article or document detail page, a "Link to..." section:

1. Button labeled "+ Link" opens a search combobox
2. Combobox searches across all entity types via `/kb/search-linkable?q=...`
3. Results grouped by type, showing: type icon + UID + name/title
4. Selecting a result creates the link via `POST /kb/links`
5. Linked items appear in the backlinks panel immediately (HTMX swap)
6. Each linked item has an "x" button to unlink

### Linking UI — From Entity Pages

On issue detail, policy detail, client detail, activity detail, project detail:

1. A "Knowledge Base" section (in sidebar or relevant panel area)
2. Shows linked KB items as compact rows: type icon + UID pill + title
3. "+ Link KB item" combobox searches KB entries via `/kb/search-entries?q=...`
4. Selecting creates the link via `POST /kb/links`
5. Each item has an "x" button to unlink
6. Clicking a KB item navigates to its detail page

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/kb/links` | Create a link (body: source_type, source_id, target_type, target_id) |
| DELETE | `/kb/links/{id}` | Remove a link |
| GET | `/kb/links/for/{entity_type}/{entity_id}` | Get all links for an entity |
| GET | `/kb/search-linkable` | Search all linkable entities (for combobox) |

## Backlinks Panel (Phase 1)

### Appearance

A single **"References"** section on every detail page that has links. No directional split (links to / linked from) — since links are bi-directional, the direction is arbitrary and adds confusion. Just show all connected items as one flat list, grouped by entity type.

Each row shows:
- Entity type icon (small, colored)
- UID pill (e.g., `KB-003`, `ISS-12`, `POL-042`)
- Title/name (truncated)
- Entity type label (right-aligned, gray)

### Placement by Page Type
- **KB article/document detail:** In the existing right sidebar, above tags
- **Issue detail:** In the detail panel, as a collapsible section
- **Policy detail:** In the sidebar or a dedicated tab section
- **Client detail:** In the sidebar
- **Activity detail:** Inline below activity content
- **Project detail:** In the detail panel

### Template
A shared partial `_backlinks_panel.html` that accepts the entity type and ID, fetches links via the API, and renders the grouped list. Loaded via HTMX on page render.

## Network Graph Explorer (Phase 2 — Future)

A "Map" tab on the KB index page (`/kb?tab=map`):

- Interactive force-directed graph (D3.js or vis.js)
- Nodes colored by entity type:
  - KB articles: blue
  - Attachments (local): red
  - Attachments (DEVONthink): purple
  - Issues: amber
  - Policies: violet
  - Clients: green
  - Activities: teal
  - Projects: orange
- Edges represent links
- Click a node to navigate to its detail page
- Filter by entity type (toggle node types on/off)
- Zoom and drag to explore
- Node size scaled by link count

**Not in scope for Phase 1.** Build after the backlinks system is solid and there's enough linked data to make the graph useful.

## Migration Plan

### New Migration: `NNN_kb_links.sql`

```sql
CREATE TABLE IF NOT EXISTS kb_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_type, source_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_links_source ON kb_links(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kb_links_target ON kb_links(target_type, target_id);
```

### Data Migration
- Migrate existing `kb_record_links` into `kb_links` (article → client/policy)
- Migrate `record_attachments` where `record_type = 'kb_article'` (attachment → article)
- Old tables kept but no longer written to by new code

## Search Route Changes

### Updated `/kb/search` Parameters

| Param | Type | Description |
|-------|------|-------------|
| `q` | string | Text search across titles, content, filenames, tags |
| `source_filter` | string | `""` (all), `"article"`, `"local"`, `"devonthink"` |
| `category` | string | Category name filter |
| `linked_type` | string | Entity type to filter by linked-to |
| `linked_id` | integer | Entity ID to filter by linked-to |
| `sort` | string | `"updated"` (default), `"title"`, `"most_linked"`, `"category"` |

### Source Filter Logic
- `article` → query `kb_articles` only
- `local` → query `attachments WHERE source = 'local'`
- `devonthink` → query `attachments WHERE source = 'devonthink'`
- empty → query both tables (current behavior, but attachments split by source for card rendering)

### Link Count
Each card needs a link count for display and for "most linked" sort. Computed via:
```sql
SELECT COUNT(*) FROM kb_links
WHERE (source_type = ? AND source_id = ?)
   OR (target_type = ? AND target_id = ?)
```

## Phase 1 Scope

1. Unified card rendering with type-specific visual identity (border, icon, source line)
2. Expanded filter bar (source toggle, category, linked-to combobox, sort)
3. `kb_links` table + migration + data migration from old tables
4. Link/unlink API endpoints
5. Linking UI on KB detail pages (article + document)
6. Linking UI on entity pages (issues, policies, clients, activities, projects)
7. Backlinks panel partial, rendered on all applicable detail pages
8. Link count badge on KB cards

## Phase 2 Scope (Future)

1. Network graph explorer ("Map" tab on KB index)
2. Graph filtering and interaction
3. Potential: "Related items" suggestions based on shared links or content similarity
